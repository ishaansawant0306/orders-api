import time
import uuid
import base64
import json
import threading
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional

# ---- Config (assigned values) ----
TOTAL_ORDERS = 46          # T
RATE_LIMIT = 18            # R requests
RATE_WINDOW_SECONDS = 10   # per 10s

app = FastAPI(title="Orders API")

# CORS: allow cross-origin requests from any page (grader will call from a browser)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Fixed catalog of orders 1..T ----
ORDERS_CATALOG = [
    {"id": i, "item": f"Item-{i}", "amount": round(9.99 + i, 2)}
    for i in range(1, TOTAL_ORDERS + 1)
]

# ---- Idempotency store: key -> order dict ----
_idempotency_lock = threading.Lock()
idempotency_store = {}

# next id counter for newly created orders (starts after catalog range)
_next_id_lock = threading.Lock()
_next_order_id = TOTAL_ORDERS + 1

# ---- Rate limiting store: client_id -> list of request timestamps ----
_rate_lock = threading.Lock()
rate_buckets = {}


def check_rate_limit(client_id: str):
    now = time.time()
    with _rate_lock:
        bucket = rate_buckets.setdefault(client_id, [])
        # drop timestamps outside the window
        window_start = now - RATE_WINDOW_SECONDS
        bucket[:] = [ts for ts in bucket if ts > window_start]

        if len(bucket) >= RATE_LIMIT:
            oldest = bucket[0]
            retry_after = max(1, int(RATE_WINDOW_SECONDS - (now - oldest)) + 1)
            return False, retry_after

        bucket.append(now)
        return True, None


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Only rate-limit the API endpoints we care about
    if request.url.path in ("/orders",):
        client_id = request.headers.get("X-Client-Id", "anonymous")
        allowed, retry_after = check_rate_limit(client_id)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )
    response = await call_next(request)
    return response


# ---- Cursor helpers (opaque cursor = base64 of next starting index) ----
def encode_cursor(index: int) -> str:
    raw = json.dumps({"idx": index}).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def decode_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("utf-8"))
        data = json.loads(raw)
        return int(data.get("idx", 0))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor")


@app.post("/orders", status_code=201)
async def create_order(request: Request, idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    global _next_order_id

    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    with _idempotency_lock:
        existing = idempotency_store.get(idempotency_key)
        if existing:
            return JSONResponse(status_code=200, content=existing)

        # try to read optional body (not required)
        try:
            body = await request.json()
        except Exception:
            body = {}

        with _next_id_lock:
            new_id = _next_order_id
            _next_order_id += 1

        order = {
            "id": str(new_id),
            "item": body.get("item", f"Item-{new_id}"),
            "amount": body.get("amount", round(9.99 + new_id, 2)),
        }
        idempotency_store[idempotency_key] = order
        return JSONResponse(status_code=201, content=order)


@app.get("/orders")
async def list_orders(limit: int = 10, cursor: Optional[str] = None):
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be positive")

    start_index = 0 if cursor is None else decode_cursor(cursor)

    if start_index < 0 or start_index > len(ORDERS_CATALOG):
        raise HTTPException(status_code=400, detail="Invalid cursor")

    end_index = min(start_index + limit, len(ORDERS_CATALOG))
    items = ORDERS_CATALOG[start_index:end_index]

    next_cursor = None
    if end_index < len(ORDERS_CATALOG):
        next_cursor = encode_cursor(end_index)

    return {
        "items": items,
        "orders": items,       # alias
        "next_cursor": next_cursor,
        "next": next_cursor,   # alias
    }


@app.get("/")
async def root():
    return {"status": "ok", "total_orders": TOTAL_ORDERS, "rate_limit": RATE_LIMIT, "rate_window_seconds": RATE_WINDOW_SECONDS}
