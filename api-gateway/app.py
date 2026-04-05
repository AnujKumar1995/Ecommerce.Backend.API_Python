"""
API Gateway - Central entry point for all client requests.
Handles routing, JWT authentication, rate limiting, request logging, and correlation tracing.
"""

import os
import time
import uuid
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from contextlib import asynccontextmanager

import jwt
import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# --- Configuration ---
SECRET_KEY = os.getenv("JWT_SECRET", "ecommerce-super-secret-key-2026")
ALGORITHM = "HS256"
TOKEN_EXPIRY_MINUTES = 60
RATE_LIMIT_REQUESTS = 100
RATE_LIMIT_WINDOW = 60  # seconds

REGISTRY_URL = os.getenv("REGISTRY_URL", "http://service-registry:8500")

# Service route map: prefix -> service name
SERVICE_ROUTES = {
    "/api/products": "product-service",
    "/api/cart": "cart-service",
    "/api/orders": "order-service",
    "/api/product-details": "product-detail-service",
}

# Endpoints that don't require auth
PUBLIC_ENDPOINTS = [
    ("POST", "/api/auth/token"),
    ("GET", "/api/products"),
    ("GET", "/health"),
]

# Admin-only endpoints (require role=admin in JWT)
ADMIN_ENDPOINTS = [
    ("POST", "/api/products"),
    ("DELETE", "/api/products"),
    ("POST", "/api/product-details"),
    ("DELETE", "/api/product-details"),
]

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("api-gateway")

# --- In-Memory Users (for demo) ---
USERS = {
    "admin": {"password": "admin123", "role": "admin"},
    "user1": {"password": "user123", "role": "user"},
    "user2": {"password": "user123", "role": "user"},
}

# --- Rate Limiter ---
rate_limit_store: dict[str, list[float]] = defaultdict(list)


# --- Models ---
class TokenRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = TOKEN_EXPIRY_MINUTES * 60


# --- Service Discovery ---
service_cache: dict[str, dict] = {}
cache_ttl: dict[str, float] = {}
CACHE_TTL_SECONDS = 30


async def discover_service(service_name: str) -> dict:
    """Discover a service via the registry with caching."""
    now = time.time()
    if service_name in service_cache and now - cache_ttl.get(service_name, 0) < CACHE_TTL_SECONDS:
        return service_cache[service_name]

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{REGISTRY_URL}/services/{service_name}")
            if resp.status_code == 200:
                svc = resp.json()
                service_cache[service_name] = svc
                cache_ttl[service_name] = now
                return svc
    except Exception as e:
        logger.error(f"Service discovery failed for {service_name}: {e}")

    raise HTTPException(status_code=503, detail=f"Service '{service_name}' unavailable")


def resolve_service_url(service_name: str) -> str:
    """Build service URL from docker-compose service name (fallback)."""
    port_map = {
        "product-service": 8001,
        "product-detail-service": 8002,
        "cart-service": 8003,
        "order-service": 8004,
        "notification-service": 8005,
    }
    port = port_map.get(service_name, 8001)
    return f"http://{service_name}:{port}"


# --- JWT Helpers ---
def create_token(username: str, role: str) -> str:
    payload = {
        "sub": username,
        "role": role,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=TOKEN_EXPIRY_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# --- Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("API Gateway starting on port 8000")
    # Register self with service registry
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(f"{REGISTRY_URL}/register", json={
                "name": "api-gateway",
                "host": "api-gateway",
                "port": 8000,
                "metadata": {"type": "gateway"}
            })
    except Exception as e:
        logger.warning(f"Could not register with registry: {e}")
    yield
    logger.info("API Gateway shutting down")


# --- App ---
app = FastAPI(title="API Gateway", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Middleware ---
@app.middleware("http")
async def gateway_middleware(request: Request, call_next):
    """Adds correlation ID, logging, and rate limiting."""
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id
    start_time = time.time()

    # Rate limiting by client IP
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    rate_limit_store[client_ip] = [
        t for t in rate_limit_store[client_ip] if now - t < RATE_LIMIT_WINDOW
    ]
    if len(rate_limit_store[client_ip]) >= RATE_LIMIT_REQUESTS:
        logger.warning(f"Rate limit exceeded for {client_ip}", extra={"correlation_id": correlation_id})
        return Response(
            content='{"detail":"Rate limit exceeded"}',
            status_code=429,
            media_type="application/json",
        )
    rate_limit_store[client_ip].append(now)

    logger.info(
        f"→ {request.method} {request.url.path}",
        extra={"correlation_id": correlation_id},
    )

    response = await call_next(request)

    duration = round((time.time() - start_time) * 1000, 2)
    response.headers["X-Correlation-ID"] = correlation_id
    response.headers["X-Response-Time"] = f"{duration}ms"

    logger.info(
        f"← {request.method} {request.url.path} [{response.status_code}] {duration}ms",
        extra={"correlation_id": correlation_id},
    )

    return response


# --- Auth Endpoint ---
@app.post("/api/auth/token", response_model=TokenResponse)
async def get_token(req: TokenRequest):
    """Authenticate user and return JWT token."""
    user = USERS.get(req.username)
    if not user or user["password"] != req.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(req.username, user["role"])
    return TokenResponse(access_token=token)


# --- Auth Helper ---
def authenticate(request: Request) -> dict:
    """Extract and verify JWT from request."""
    path = request.url.path
    method = request.method

    # Check if endpoint is public
    for pub_method, pub_path in PUBLIC_ENDPOINTS:
        if method == pub_method and path.startswith(pub_path):
            return {"sub": "anonymous", "role": "public"}

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth_header[7:]
    return verify_token(token)


def check_admin(request: Request, claims: dict):
    """Check if admin-only endpoint requires admin role."""
    path = request.url.path
    method = request.method
    for admin_method, admin_path in ADMIN_ENDPOINTS:
        if method == admin_method and path.startswith(admin_path):
            if claims.get("role") != "admin":
                raise HTTPException(status_code=403, detail="Admin access required")
            return
    return


# --- Proxy Logic ---
async def proxy_request(request: Request, service_name: str, path: str) -> Response:
    """Forward request to downstream service."""
    correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))

    # Try service discovery first, fallback to DNS
    try:
        svc = await discover_service(service_name)
        base_url = f"http://{svc['host']}:{svc['port']}"
    except HTTPException:
        base_url = resolve_service_url(service_name)

    url = f"{base_url}{path}"
    headers = {
        "X-Correlation-ID": correlation_id,
        "Content-Type": request.headers.get("Content-Type", "application/json"),
    }

    # Forward auth header
    auth = request.headers.get("Authorization")
    if auth:
        headers["Authorization"] = auth

    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body,
                params=dict(request.query_params),
            )
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type="application/json",
            )
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail=f"Service '{service_name}' is unavailable")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail=f"Service '{service_name}' timed out")


# --- Route: Products ---
@app.api_route("/api/products", methods=["GET", "POST"])
@app.api_route("/api/products/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_products(request: Request, path: str = ""):
    claims = authenticate(request)
    check_admin(request, claims)
    target_path = f"/products/{path}" if path else "/products"
    return await proxy_request(request, "product-service", target_path)


# --- Route: Product Details ---
@app.api_route("/api/product-details", methods=["GET", "POST"])
@app.api_route("/api/product-details/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_product_details(request: Request, path: str = ""):
    claims = authenticate(request)
    check_admin(request, claims)
    target_path = f"/details/{path}" if path else "/details"
    return await proxy_request(request, "product-detail-service", target_path)


# --- Route: Cart ---
@app.api_route("/api/cart", methods=["GET", "POST"])
@app.api_route("/api/cart/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_cart(request: Request, path: str = ""):
    authenticate(request)
    target_path = f"/cart/{path}" if path else "/cart"
    return await proxy_request(request, "cart-service", target_path)


# --- Route: Orders ---
@app.api_route("/api/orders", methods=["GET", "POST"])
@app.api_route("/api/orders/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_orders(request: Request, path: str = ""):
    authenticate(request)
    target_path = f"/orders/{path}" if path else "/orders"
    return await proxy_request(request, "order-service", target_path)


# --- Health ---
@app.get("/health")
async def health():
    return {"status": "UP", "service": "api-gateway"}


# --- Global Exception Handler ---
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    correlation_id = getattr(request.state, "correlation_id", "N/A")
    logger.error(
        f"Unhandled exception: {exc}",
        extra={"correlation_id": correlation_id},
        exc_info=True,
    )
    return Response(
        content='{"detail":"Internal server error"}',
        status_code=500,
        media_type="application/json",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
