import time
import uuid
from fastapi import FastAPI, Header, Request
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

TOTAL_ORDERS = 46
RATE_LIMIT = 18
WINDOW_SECONDS = 10

# In-memory stores
idempotency_store = {}  # key -> order dict
rate_buckets = {}  # client_id -> list of timestamps

CATALOG = [
    {"id": i, "item": f"Order-{i}", "status": "confirmed"}
    for i in range(1, TOTAL_ORDERS + 1)
]


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path == "/orders":
        client_id = request.headers.get("X-Client-Id")
        if client_id:
            now = time.time()
            bucket = rate_buckets.setdefault(client_id, [])
            # Remove timestamps outside the window
            bucket[:] = [t for t in bucket if now - t < WINDOW_SECONDS]

            if len(bucket) >= RATE_LIMIT:
                oldest = bucket[0]
                retry_after = int(WINDOW_SECONDS - (now - oldest)) + 1
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded"},
                    headers={"Retry-After": str(retry_after)},
                )

            bucket.append(now)

    response = await call_next(request)
    return response


@app.post("/orders", status_code=201)
async def create_order(request: Request, idempotency_key: str = Header(default=None, alias="Idempotency-Key")):
    if idempotency_key and idempotency_key in idempotency_store:
        return JSONResponse(content=idempotency_store[idempotency_key], status_code=201)

    new_order = {
        "id": str(uuid.uuid4()),
        "status": "created",
    }

    if idempotency_key:
        idempotency_store[idempotency_key] = new_order

    return JSONResponse(content=new_order, status_code=201)


@app.get("/orders")
async def list_orders(limit: int = 10, cursor: Optional[str] = None):
    start = 0
    if cursor:
        try:
            start = int(cursor)
        except ValueError:
            start = 0

    end = start + limit
    page_items = CATALOG[start:end]

    next_cursor = str(end) if end < TOTAL_ORDERS else None

    return {
        "items": page_items,
        "next_cursor": next_cursor,
        "next": next_cursor,
        "orders": page_items,
    }
