"""
Cart Service - Manages shopping cart and checkout.
Publishes order events to RabbitMQ for asynchronous processing.
"""

import os
import json
import uuid
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import httpx
import pika
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

# --- Configuration ---
REGISTRY_URL = os.getenv("REGISTRY_URL", "http://service-registry:8500")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
PRODUCT_SERVICE_URL = os.getenv("PRODUCT_SERVICE_URL", "http://product-service:8001")
SERVICE_NAME = "cart-service"
SERVICE_PORT = 8003

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(SERVICE_NAME)

# --- In-Memory Store ---
# carts: { user_id: { product_id: { quantity, product_name, price } } }
carts: dict[str, dict[str, dict]] = {}

# RabbitMQ connection (lazy init)
rabbit_connection = None
rabbit_channel = None


# --- Models ---
class CartItem(BaseModel):
    product_id: str
    quantity: int = 1


class CheckoutRequest(BaseModel):
    shipping_address: str = "Default Address, City, 12345"


# --- RabbitMQ Helper ---
def get_rabbit_channel():
    global rabbit_connection, rabbit_channel
    try:
        if rabbit_connection is None or rabbit_connection.is_closed:
            rabbit_connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=RABBITMQ_HOST,
                    heartbeat=600,
                    connection_attempts=3,
                    retry_delay=5,
                )
            )
            rabbit_channel = rabbit_connection.channel()
            rabbit_channel.exchange_declare(exchange="ecommerce_events", exchange_type="topic", durable=True)
            rabbit_channel.queue_declare(queue="order_events", durable=True)
            rabbit_channel.queue_declare(queue="notification_events", durable=True)
            rabbit_channel.queue_bind(queue="order_events", exchange="ecommerce_events", routing_key="order.*")
            rabbit_channel.queue_bind(queue="notification_events", exchange="ecommerce_events", routing_key="notification.*")
            logger.info("Connected to RabbitMQ")
        return rabbit_channel
    except Exception as e:
        logger.error(f"RabbitMQ connection failed: {e}")
        return None


def publish_event(routing_key: str, message: dict):
    """Publish an event to RabbitMQ (asynchronous communication pattern)."""
    try:
        channel = get_rabbit_channel()
        if channel:
            channel.basic_publish(
                exchange="ecommerce_events",
                routing_key=routing_key,
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # persistent
                    content_type="application/json",
                ),
            )
            logger.info(f"Published event: {routing_key}")
        else:
            logger.warning(f"Could not publish event {routing_key}: no RabbitMQ connection")
    except Exception as e:
        logger.error(f"Failed to publish event: {e}")
        # Reset connection for retry
        global rabbit_connection, rabbit_channel
        rabbit_connection = None
        rabbit_channel = None


# --- Helper: Fetch product info ---
async def get_product_info(product_id: str, correlation_id: str = "") -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{PRODUCT_SERVICE_URL}/products/{product_id}",
                headers={"X-Correlation-ID": correlation_id},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.warning(f"Could not fetch product {product_id}: {e}")
    return None


# --- Service Registration ---
async def register_with_registry():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(f"{REGISTRY_URL}/register", json={
                "name": SERVICE_NAME,
                "host": SERVICE_NAME,
                "port": SERVICE_PORT,
                "metadata": {"type": "cart-service"}
            })
            logger.info("Registered with service registry")
    except Exception as e:
        logger.warning(f"Could not register with registry: {e}")


# --- Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting on port {SERVICE_PORT}")
    await register_with_registry()
    # Initialize RabbitMQ
    get_rabbit_channel()
    yield
    logger.info(f"{SERVICE_NAME} shutting down")
    if rabbit_connection and not rabbit_connection.is_closed:
        rabbit_connection.close()


# --- App ---
app = FastAPI(title="Cart Service", version="1.0.0", lifespan=lifespan)


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
@app.get("/cart/{user_id}")
async def get_cart(user_id: str):
    """Get a user's cart."""
    cart = carts.get(user_id, {})
    items = list(cart.values())
    total = sum(i.get("price", 0) * i.get("quantity", 0) for i in items)
    return {
        "user_id": user_id,
        "items": items,
        "item_count": len(items),
        "total": round(total, 2),
    }


@app.post("/cart/{user_id}/items", status_code=201)
async def add_to_cart(user_id: str, item: CartItem, request: Request):
    """Add an item to the user's cart."""
    correlation_id = getattr(request.state, "correlation_id", "")

    # Fetch product info from Product Service (sync inter-service call)
    product = await get_product_info(item.product_id, correlation_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    if user_id not in carts:
        carts[user_id] = {}

    cart = carts[user_id]
    if item.product_id in cart:
        cart[item.product_id]["quantity"] += item.quantity
    else:
        price = 0.0
        if "details" in product and "price" in product["details"]:
            price = product["details"]["price"]
        cart[item.product_id] = {
            "product_id": item.product_id,
            "product_name": product.get("name", "Unknown"),
            "quantity": item.quantity,
            "price": price,
        }

    logger.info(f"Added {item.product_id} x{item.quantity} to cart for user {user_id}")
    return {"message": "Item added to cart", "cart": cart[item.product_id]}


@app.put("/cart/{user_id}/items/{product_id}")
async def update_cart_item(user_id: str, product_id: str, item: CartItem):
    """Update quantity of an item in cart."""
    if user_id not in carts or product_id not in carts[user_id]:
        raise HTTPException(status_code=404, detail="Item not in cart")

    if item.quantity <= 0:
        del carts[user_id][product_id]
        return {"message": "Item removed from cart"}

    carts[user_id][product_id]["quantity"] = item.quantity
    return {"message": "Cart updated", "item": carts[user_id][product_id]}


@app.delete("/cart/{user_id}/items/{product_id}")
async def remove_from_cart(user_id: str, product_id: str):
    """Remove an item from the user's cart."""
    if user_id not in carts or product_id not in carts[user_id]:
        raise HTTPException(status_code=404, detail="Item not in cart")

    removed = carts[user_id].pop(product_id)
    logger.info(f"Removed {product_id} from cart for user {user_id}")
    return {"message": "Item removed from cart", "item": removed}


@app.post("/cart/{user_id}/checkout")
async def checkout(user_id: str, checkout_req: CheckoutRequest, request: Request):
    """Checkout - creates an order and publishes events asynchronously."""
    if user_id not in carts or not carts[user_id]:
        raise HTTPException(status_code=400, detail="Cart is empty")

    correlation_id = getattr(request.state, "correlation_id", "")
    cart = carts[user_id]
    items = list(cart.values())
    total = round(sum(i.get("price", 0) * i.get("quantity", 0) for i in items), 2)
    order_id = f"ord-{str(uuid.uuid4())[:8]}"

    order_event = {
        "event_type": "order.created",
        "order_id": order_id,
        "user_id": user_id,
        "items": items,
        "total": total,
        "shipping_address": checkout_req.shipping_address,
        "status": "CREATED",
        "correlation_id": correlation_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Publish order event to RabbitMQ (Asynchronous communication)
    publish_event("order.created", order_event)

    # Publish notification event
    notification_event = {
        "event_type": "notification.order_placed",
        "user_id": user_id,
        "order_id": order_id,
        "total": total,
        "message": f"Order {order_id} placed successfully! Total: ${total}",
        "correlation_id": correlation_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    publish_event("notification.order_placed", notification_event)

    # Clear cart after checkout
    carts[user_id] = {}
    logger.info(f"Checkout completed for user {user_id}, order {order_id}")

    return {
        "message": "Checkout successful",
        "order_id": order_id,
        "total": total,
        "item_count": len(items),
        "status": "CREATED",
    }


@app.get("/health")
async def health():
    rabbit_ok = rabbit_connection is not None and not rabbit_connection.is_closed
    return {
        "status": "UP",
        "service": SERVICE_NAME,
        "rabbitmq": "connected" if rabbit_ok else "disconnected",
    }


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
