"""
Order Service - Manages orders.
Consumes order events from RabbitMQ (asynchronous communication pattern).
Also provides REST endpoints for order queries.
"""

import os
import json
import uuid
import logging
import threading
from contextlib import asynccontextmanager

import httpx
import pika
from fastapi import FastAPI, HTTPException, Request, Response

# --- Configuration ---
REGISTRY_URL = os.getenv("REGISTRY_URL", "http://service-registry:8500")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
SERVICE_NAME = "order-service"
SERVICE_PORT = 8004

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(SERVICE_NAME)

# --- In-Memory Store ---
# orders: { order_id: { order_id, user_id, items, total, status, ... } }
orders: dict[str, dict] = {}
# user_orders: { user_id: [order_id, ...] }
user_orders: dict[str, list[str]] = {}


# --- RabbitMQ Consumer (Background Thread) ---
def consume_order_events():
    """Consume order events from RabbitMQ in a background thread."""
    while True:
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=RABBITMQ_HOST,
                    heartbeat=600,
                    connection_attempts=5,
                    retry_delay=5,
                )
            )
            channel = connection.channel()
            channel.exchange_declare(exchange="ecommerce_events", exchange_type="topic", durable=True)
            channel.queue_declare(queue="order_events", durable=True)
            channel.queue_bind(queue="order_events", exchange="ecommerce_events", routing_key="order.*")

            def callback(ch, method, _properties, body):
                try:
                    event = json.loads(body)
                    order_id = event.get("order_id")
                    user_id = event.get("user_id")
                    correlation_id = event.get("correlation_id", "N/A")

                    logger.info(f"[{correlation_id}] Received order event: {event.get('event_type')} for order {order_id}")

                    # Store the order
                    orders[order_id] = {
                        "order_id": order_id,
                        "user_id": user_id,
                        "items": event.get("items", []),
                        "total": event.get("total", 0),
                        "shipping_address": event.get("shipping_address", ""),
                        "status": "CONFIRMED",
                        "created_at": event.get("created_at", ""),
                        "correlation_id": correlation_id,
                    }

                    if user_id not in user_orders:
                        user_orders[user_id] = []
                    user_orders[user_id].append(order_id)

                    logger.info(f"[{correlation_id}] Order {order_id} stored and confirmed")
                    ch.basic_ack(delivery_tag=method.delivery_tag)

                except Exception as e:
                    logger.error(f"Error processing order event: {e}", exc_info=True)
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue="order_events", on_message_callback=callback)
            logger.info("Order event consumer started")
            channel.start_consuming()

        except Exception as e:
            logger.error(f"RabbitMQ consumer connection error: {e}")
            import time
            time.sleep(5)  # Retry after delay


# --- Service Registration ---
async def register_with_registry():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(f"{REGISTRY_URL}/register", json={
                "name": SERVICE_NAME,
                "host": SERVICE_NAME,
                "port": SERVICE_PORT,
                "metadata": {"type": "order-service"}
            })
            logger.info("Registered with service registry")
    except Exception as e:
        logger.warning(f"Could not register with registry: {e}")


# --- Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting on port {SERVICE_PORT}")
    await register_with_registry()
    # Start RabbitMQ consumer in background thread
    t = threading.Thread(target=consume_order_events, daemon=True)
    t.start()
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


# --- App ---
app = FastAPI(title="Order Service", version="1.0.0", lifespan=lifespan)


# --- Middleware ---
@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id
    logger.info(f"[{correlation_id}] {request.method} {request.url.path}")
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


# --- Endpoints ---
@app.get("/orders/{user_id}")
async def get_user_orders(user_id: str, page: int = 1, size: int = 10):
    """Get all orders for a user."""
    order_ids = user_orders.get(user_id, [])
    total = len(order_ids)
    start = (page - 1) * size
    end = start + size
    paginated_ids = order_ids[start:end]
    result = [orders[oid] for oid in paginated_ids if oid in orders]
    return {
        "user_id": user_id,
        "orders": result,
        "total": total,
        "page": page,
        "size": size,
    }


@app.get("/orders/{user_id}/{order_id}")
async def get_order(user_id: str, order_id: str):
    """Get a specific order."""
    if order_id not in orders:
        raise HTTPException(status_code=404, detail="Order not found")
    order = orders[order_id]
    if order["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return order


@app.put("/orders/{order_id}/status")
async def update_order_status(order_id: str, status: str):
    """Update order status (for internal use)."""
    if order_id not in orders:
        raise HTTPException(status_code=404, detail="Order not found")
    orders[order_id]["status"] = status
    logger.info(f"Order {order_id} status updated to {status}")
    return orders[order_id]


@app.get("/health")
async def health():
    return {"status": "UP", "service": SERVICE_NAME, "orders_count": len(orders)}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    correlation_id = getattr(request.state, "correlation_id", "N/A")
    logger.error(f"Unhandled exception: {exc}", extra={"correlation_id": correlation_id}, exc_info=True)
    return Response(
        content='{"detail":"Internal server error"}',
        status_code=500,
        media_type="application/json",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
