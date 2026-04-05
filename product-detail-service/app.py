"""
Product Detail Service - Manages product details (size, price, design).
Separate from Product Service to allow independent scaling and updates.
"""

import os
import uuid
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

# --- Configuration ---
REGISTRY_URL = os.getenv("REGISTRY_URL", "http://service-registry:8500")
SERVICE_NAME = "product-detail-service"
SERVICE_PORT = 8002

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(SERVICE_NAME)

# --- In-Memory Store ---
product_details: dict[str, dict] = {
    "p001": {
        "product_id": "p001",
        "sizes": ["S", "M", "L", "XL"],
        "price": 29.99,
        "currency": "USD",
        "design": "Solid Navy Blue",
        "material": "100% Cotton",
        "weight": "200g",
    },
    "p002": {
        "product_id": "p002",
        "sizes": ["8", "9", "10", "11", "12"],
        "price": 89.99,
        "currency": "USD",
        "design": "Sport Black/Red",
        "material": "Mesh & Synthetic",
        "weight": "350g",
    },
    "p003": {
        "product_id": "p003",
        "sizes": ["One Size"],
        "price": 49.99,
        "currency": "USD",
        "design": "Classic Brown",
        "material": "Genuine Leather",
        "weight": "150g",
    },
}


# --- Models ---
class DetailCreate(BaseModel):
    sizes: list[str] = []
    price: float
    currency: str = "USD"
    design: str = ""
    material: str = ""
    weight: str = ""


class DetailUpdate(BaseModel):
    sizes: list[str] | None = None
    price: float | None = None
    currency: str | None = None
    design: str | None = None
    material: str | None = None
    weight: str | None = None


# --- Service Registration ---
async def register_with_registry():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(f"{REGISTRY_URL}/register", json={
                "name": SERVICE_NAME,
                "host": SERVICE_NAME,
                "port": SERVICE_PORT,
                "metadata": {"type": "detail-service"}
            })
            logger.info("Registered with service registry")
    except Exception as e:
        logger.warning(f"Could not register with registry: {e}")


# --- Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting on port {SERVICE_PORT}")
    await register_with_registry()
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


# --- App ---
app = FastAPI(title="Product Detail Service", version="1.0.0", lifespan=lifespan)


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
@app.get("/details/{product_id}")
async def get_details(product_id: str):
    """Get details for a product by ID."""
    if product_id not in product_details:
        raise HTTPException(status_code=404, detail="Product details not found")
    return product_details[product_id]


@app.get("/details")
async def list_all_details():
    """List all product details."""
    return {"details": list(product_details.values()), "total": len(product_details)}


@app.post("/details/{product_id}", status_code=201)
async def create_or_update_details(product_id: str, detail: DetailCreate):
    """Add or replace details for a product (Admin)."""
    product_details[product_id] = {
        "product_id": product_id,
        "sizes": detail.sizes,
        "price": detail.price,
        "currency": detail.currency,
        "design": detail.design,
        "material": detail.material,
        "weight": detail.weight,
    }
    logger.info(f"Details set for product: {product_id}")
    return product_details[product_id]


@app.put("/details/{product_id}")
async def update_details(product_id: str, update: DetailUpdate):
    """Partially update details for a product."""
    if product_id not in product_details:
        raise HTTPException(status_code=404, detail="Product details not found")

    existing = product_details[product_id]
    if update.sizes is not None:
        existing["sizes"] = update.sizes
    if update.price is not None:
        existing["price"] = update.price
    if update.currency is not None:
        existing["currency"] = update.currency
    if update.design is not None:
        existing["design"] = update.design
    if update.material is not None:
        existing["material"] = update.material
    if update.weight is not None:
        existing["weight"] = update.weight

    logger.info(f"Details updated for product: {product_id}")
    return existing


@app.delete("/details/{product_id}")
async def delete_details(product_id: str):
    """Remove details for a product (Admin)."""
    if product_id not in product_details:
        raise HTTPException(status_code=404, detail="Product details not found")

    removed = product_details.pop(product_id)
    logger.info(f"Details deleted for product: {product_id}")
    return {"message": "Details removed", "details": removed}


# --- Price lookup (convenience endpoint) ---
@app.get("/price/{product_id}")
async def get_price(product_id: str):
    """Quick price lookup for a product."""
    if product_id not in product_details:
        raise HTTPException(status_code=404, detail="Product not found")
    d = product_details[product_id]
    return {"product_id": product_id, "price": d["price"], "currency": d["currency"]}


@app.get("/health")
async def health():
    return {"status": "UP", "service": SERVICE_NAME}


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
