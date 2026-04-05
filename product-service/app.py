"""
Product Service - Manages product inventory (CRUD).
Fetches enriched product details from Product Detail Service (synchronous inter-service communication).
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
DETAIL_SERVICE_URL = os.getenv("DETAIL_SERVICE_URL", "http://product-detail-service:8002")
SERVICE_NAME = "product-service"
SERVICE_PORT = 8001

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(SERVICE_NAME)

# --- In-Memory Store ---
products: dict[str, dict] = {
    "p001": {"id": "p001", "name": "Classic T-Shirt", "category": "Apparel", "active": True},
    "p002": {"id": "p002", "name": "Running Shoes", "category": "Footwear", "active": True},
    "p003": {"id": "p003", "name": "Leather Wallet", "category": "Accessories", "active": True},
}


# --- Models ---
class ProductCreate(BaseModel):
    name: str
    category: str


class ProductUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    active: bool | None = None


# --- Helper: Register with Service Registry ---
async def register_with_registry():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(f"{REGISTRY_URL}/register", json={
                "name": SERVICE_NAME,
                "host": SERVICE_NAME,
                "port": SERVICE_PORT,
                "metadata": {"type": "core-service"}
            })
            logger.info("Registered with service registry")
    except Exception as e:
        logger.warning(f"Could not register with registry: {e}")


# --- Helper: Fetch details from Product Detail Service (Synchronous) ---
async def fetch_product_details(product_id: str, correlation_id: str = "") -> dict | None:
    """Synchronous inter-service call to Product Detail Service."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{DETAIL_SERVICE_URL}/details/{product_id}",
                headers={"X-Correlation-ID": correlation_id},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.warning(f"Could not fetch details for product {product_id}: {e}")
    return None


# --- Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting on port {SERVICE_PORT}")
    await register_with_registry()
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


# --- App ---
app = FastAPI(title="Product Service", version="1.0.0", lifespan=lifespan)


# --- Middleware: Correlation ID ---
@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id
    logger.info(f"[{correlation_id}] {request.method} {request.url.path}")
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


# --- Endpoints ---
@app.get("/products")
async def list_products(page: int = 1, size: int = 10):
    """List all active products with pagination."""
    active = [p for p in products.values() if p.get("active", True)]
    start = (page - 1) * size
    end = start + size
    paginated = active[start:end]
    return {
        "products": paginated,
        "total": len(active),
        "page": page,
        "size": size,
        "total_pages": (len(active) + size - 1) // size,
    }


@app.get("/products/{product_id}")
async def get_product(product_id: str, request: Request, enrich: bool = True):
    """Get a single product, optionally enriched with details from Product Detail Service."""
    if product_id not in products:
        raise HTTPException(status_code=404, detail="Product not found")

    product = dict(products[product_id])
    correlation_id = getattr(request.state, "correlation_id", "")

    if enrich:
        details = await fetch_product_details(product_id, correlation_id)
        if details:
            product["details"] = details

    return product


@app.post("/products", status_code=201)
async def create_product(product: ProductCreate):
    """Add a new product to inventory (Admin)."""
    product_id = f"p{str(uuid.uuid4())[:8]}"
    products[product_id] = {
        "id": product_id,
        "name": product.name,
        "category": product.category,
        "active": True,
    }
    logger.info(f"Product created: {product_id}")
    return products[product_id]


@app.put("/products/{product_id}")
async def update_product(product_id: str, update: ProductUpdate):
    """Update a product."""
    if product_id not in products:
        raise HTTPException(status_code=404, detail="Product not found")

    if update.name is not None:
        products[product_id]["name"] = update.name
    if update.category is not None:
        products[product_id]["category"] = update.category
    if update.active is not None:
        products[product_id]["active"] = update.active

    logger.info(f"Product updated: {product_id}")
    return products[product_id]


@app.delete("/products/{product_id}")
async def delete_product(product_id: str):
    """Remove a product from inventory (Admin)."""
    if product_id not in products:
        raise HTTPException(status_code=404, detail="Product not found")

    removed = products.pop(product_id)
    logger.info(f"Product deleted: {product_id}")
    return {"message": "Product removed", "product": removed}


@app.get("/health")
async def health():
    return {"status": "UP", "service": SERVICE_NAME}


# --- Global Exception Handler ---
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
