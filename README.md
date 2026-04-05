# E-Commerce Microservices Backend (Python)

## Architecture Overview

This project implements a microservices-based e-commerce backend with the following services:

| Service | Port | Description |
|---------|------|-------------|
| Service Registry | 8500 | Service discovery and health monitoring |
| API Gateway | 8000 | Entry point, routing, JWT auth, rate limiting |
| Product Service | 8001 | Product inventory CRUD operations |
| Product Detail Service | 8002 | Product details (size, price, design) |
| Cart Service | 8003 | Shopping cart & checkout |
| Order Service | 8004 | Order management |
| Notification Service | 8005 | Event-driven notifications (console) |
| RabbitMQ | 5672/15672 | Async message broker |

## Quick Start

```bash
# Build and run all services
docker-compose up --build

# Run in detached mode
docker-compose up --build -d

# View logs
docker-compose logs -f

# Stop all services
docker-compose down
```

## Communication Patterns

- **Synchronous**: REST HTTP calls between API Gateway ↔ Services, Product Service ↔ Product Detail Service
- **Asynchronous**: RabbitMQ for event-driven communication (Order events → Notification Service)

## Authentication

- Admin endpoints require JWT token with `role: admin`
- Get token: `POST /api/auth/token` with `{"username": "admin", "password": "admin123"}`
- Pass token as: `Authorization: Bearer <token>`

## API Endpoints

### Auth
- `POST /api/auth/token` - Get JWT token

### Products (Admin)
- `POST /api/products` - Add product
- `DELETE /api/products/{id}` - Remove product
- `GET /api/products` - List all products
- `GET /api/products/{id}` - Get product with full details

### Product Details (Admin)
- `POST /api/products/{id}/details` - Add/update product details
- `DELETE /api/products/{id}/details` - Remove product details
- `GET /api/products/{id}/details` - Get product details

### Cart
- `POST /api/cart/{user_id}/items` - Add item to cart
- `DELETE /api/cart/{user_id}/items/{product_id}` - Remove from cart
- `GET /api/cart/{user_id}` - View cart
- `POST /api/cart/{user_id}/checkout` - Checkout

### Orders
- `GET /api/orders/{user_id}` - Get user's orders
- `GET /api/orders/{user_id}/{order_id}` - Get specific order

## Tech Stack
- **Language**: Python 3.11
- **Framework**: FastAPI
- **Message Broker**: RabbitMQ
- **Containerization**: Docker & Docker Compose
- **Auth**: JWT (PyJWT)
- **Service Discovery**: Custom Service Registry
