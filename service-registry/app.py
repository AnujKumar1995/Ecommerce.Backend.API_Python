"""
Service Registry - Central service discovery for all microservices.
Provides registration, deregistration, health checking, and service lookup.
"""

import time
import threading
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("service-registry")

# --- In-Memory Store ---
registry: dict[str, dict] = {}
lock = threading.Lock()

HEALTH_CHECK_INTERVAL = 30  # seconds


# --- Models ---
class ServiceRegistration(BaseModel):
    name: str
    host: str
    port: int
    metadata: dict = {}


class ServiceResponse(BaseModel):
    name: str
    host: str
    port: int
    status: str
    registered_at: str
    last_heartbeat: str
    metadata: dict = {}


# --- Health Check Background Task ---
def health_check_loop():
    """Periodically checks registered services health."""
    while True:
        time.sleep(HEALTH_CHECK_INTERVAL)
        with lock:
            for service_id, service in list(registry.items()):
                url = f"http://{service['host']}:{service['port']}/health"
                try:
                    resp = httpx.get(url, timeout=5.0)
                    if resp.status_code == 200:
                        service["status"] = "UP"
                        service["last_heartbeat"] = datetime.now(timezone.utc).isoformat()
                    else:
                        service["status"] = "DOWN"
                except Exception:
                    service["status"] = "DOWN"
                    logger.warning(f"Health check failed for {service_id}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Service Registry starting on port 8500")
    t = threading.Thread(target=health_check_loop, daemon=True)
    t.start()
    yield
    logger.info("Service Registry shutting down")


# --- App ---
app = FastAPI(title="Service Registry", version="1.0.0", lifespan=lifespan)


@app.post("/register", status_code=201)
async def register_service(reg: ServiceRegistration):
    """Register a new service instance."""
    service_id = f"{reg.name}-{reg.host}-{reg.port}"
    now = datetime.now(timezone.utc).isoformat()
    with lock:
        registry[service_id] = {
            "name": reg.name,
            "host": reg.host,
            "port": reg.port,
            "status": "UP",
            "registered_at": now,
            "last_heartbeat": now,
            "metadata": reg.metadata,
        }
    logger.info(f"Registered service: {service_id}")
    return {"service_id": service_id, "message": "Registered successfully"}


@app.delete("/deregister/{service_id}")
async def deregister_service(service_id: str):
    """Deregister a service instance."""
    with lock:
        if service_id not in registry:
            raise HTTPException(status_code=404, detail="Service not found")
        del registry[service_id]
    logger.info(f"Deregistered service: {service_id}")
    return {"message": "Deregistered successfully"}


@app.put("/heartbeat/{service_id}")
async def heartbeat(service_id: str):
    """Update heartbeat for a service."""
    with lock:
        if service_id not in registry:
            raise HTTPException(status_code=404, detail="Service not found")
        registry[service_id]["last_heartbeat"] = datetime.now(timezone.utc).isoformat()
        registry[service_id]["status"] = "UP"
    return {"message": "Heartbeat recorded"}


@app.get("/services")
async def list_services():
    """List all registered services."""
    with lock:
        return {"services": list(registry.values())}


@app.get("/services/{name}")
async def get_service(name: str):
    """Get a specific service by name (returns first UP instance)."""
    with lock:
        for _, svc in registry.items():
            if svc["name"] == name and svc["status"] == "UP":
                return svc
    raise HTTPException(status_code=404, detail=f"Service '{name}' not found or not available")


@app.get("/health")
async def health():
    return {"status": "UP", "service": "service-registry"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8500)
