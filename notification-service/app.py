"""
Notification Service - Event-driven notification handler.
Consumes notification events from RabbitMQ and logs them to console.
Demonstrates asynchronous communication pattern.
"""

import os
import json
import uuid
import logging
import threading
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import httpx
import pika
from fastapi import FastAPI, HTTPException, Request, Response

# --- Configuration ---
REGISTRY_URL = os.getenv("REGISTRY_URL", "http://service-registry:8500")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
SERVICE_NAME = "notification-service"
SERVICE_PORT = 8005

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(SERVICE_NAME)

# Separate logger for notifications (prominent console output)
notification_logger = logging.getLogger("NOTIFICATION")
notification_handler = logging.StreamHandler()
notification_handler.setFormatter(
    logging.Formatter("\n" + "=" * 60 + "\n%(asctime)s [NOTIFICATION]\n%(message)s\n" + "=" * 60)
)
notification_logger.addHandler(notification_handler)
notification_logger.setLevel(logging.INFO)

# --- In-Memory Store (notification log) ---
notifications: list[dict] = []


# --- RabbitMQ Consumer ---
def consume_notification_events():
    """Consume notification events from RabbitMQ in a background thread."""
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
            channel.queue_declare(queue="notification_events", durable=True)
            channel.queue_bind(queue="notification_events", exchange="ecommerce_events", routing_key="notification.*")

            def callback(ch, method, _properties, body):
                try:
                    event = json.loads(body)
                    correlation_id = event.get("correlation_id", "N/A")
                    event_type = event.get("event_type", "unknown")
                    user_id = event.get("user_id", "unknown")
                    message = event.get("message", "No message")

                    # Log notification to console (as required)
                    notification_logger.info(
                        f"Type: {event_type}\n"
                        f"User: {user_id}\n"
                        f"Message: {message}\n"
                        f"Correlation ID: {correlation_id}\n"
                        f"Timestamp: {datetime.now(timezone.utc).isoformat()}"
                    )

                    # Store in memory for API access
                    notification_record = {
                        "id": str(uuid.uuid4())[:8],
                        "event_type": event_type,
                        "user_id": user_id,
                        "message": message,
                        "order_id": event.get("order_id", ""),
                        "correlation_id": correlation_id,
                        "received_at": datetime.now(timezone.utc).isoformat(),
                        "status": "DELIVERED",
                    }
                    notifications.append(notification_record)

                    logger.info(f"[{correlation_id}] Notification processed: {event_type} for user {user_id}")
                    ch.basic_ack(delivery_tag=method.delivery_tag)

                except Exception as e:
                    logger.error(f"Error processing notification event: {e}", exc_info=True)
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue="notification_events", on_message_callback=callback)
            logger.info("Notification event consumer started")
            channel.start_consuming()

        except Exception as e:
            logger.error(f"RabbitMQ consumer connection error: {e}")
            import time
            time.sleep(5)


# --- Service Registration ---
async def register_with_registry():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(f"{REGISTRY_URL}/register", json={
                "name": SERVICE_NAME,
                "host": SERVICE_NAME,
                "port": SERVICE_PORT,
                "metadata": {"type": "notification-service"}
            })
            logger.info("Registered with service registry")
    except Exception as e:
        logger.warning(f"Could not register with registry: {e}")


# --- Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting on port {SERVICE_PORT}")
    await register_with_registry()
    t = threading.Thread(target=consume_notification_events, daemon=True)
    t.start()
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


# --- App ---
app = FastAPI(title="Notification Service", version="1.0.0", lifespan=lifespan)


# --- Middleware ---
@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


# --- Endpoints ---
@app.get("/notifications")
async def list_notifications(user_id: str = None, limit: int = 50):
    """List recent notifications (optionally filtered by user_id)."""
    result = notifications
    if user_id:
        result = [n for n in result if n["user_id"] == user_id]
    return {
        "notifications": result[-limit:],
        "total": len(result),
    }


@app.get("/notifications/{notification_id}")
async def get_notification(notification_id: str):
    """Get a specific notification by ID."""
    for n in notifications:
        if n["id"] == notification_id:
            return n
    raise HTTPException(status_code=404, detail="Notification not found")


@app.get("/health")
async def health():
    return {
        "status": "UP",
        "service": SERVICE_NAME,
        "notifications_processed": len(notifications),
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
