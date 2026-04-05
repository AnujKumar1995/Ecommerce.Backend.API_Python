# Identified Microservices - Writeup

## Overview

The e-commerce backend is decomposed into **7 microservices** following the **Single Responsibility Principle** and **Domain-Driven Design** approach. Each service owns its data and business logic, communicating via REST (synchronous) and RabbitMQ (asynchronous).

---

## 1. Service Registry (Port 8500)

**Purpose**: Central service discovery and health monitoring.

**Responsibilities**:
- Service registration and deregistration
- Periodic health checks of all registered services
- Service lookup by name (returns host:port of healthy instances)

**Reasoning**: In a microservices architecture, services need to discover each other dynamically. A centralized registry allows services to register themselves at startup and query for other services at runtime. This decouples services from hardcoded URLs and enables dynamic scaling.

**Communication**: REST (services register/query via HTTP)

---

## 2. API Gateway (Port 8000)

**Purpose**: Single entry point for all client requests.

**Responsibilities**:
- Request routing to appropriate microservices
- JWT-based authentication and authorization
- Rate limiting (100 requests/minute per IP)
- Correlation ID injection for distributed tracing
- Request/response logging with timing

**Reasoning**: The API Gateway pattern provides a unified interface to clients, abstracts the internal microservices topology, and centralizes cross-cutting concerns (auth, logging, rate limiting). Clients don't need to know about individual service locations.

**Communication**: 
- Inbound: REST from clients
- Outbound: REST to downstream services (via service discovery)

---

## 3. Product Service (Port 8001)

**Purpose**: Manages the product inventory/catalog.

**Responsibilities**:
- CRUD operations on products (add/remove/update/list)
- Product listing with pagination
- Enriching product data by fetching details from Product Detail Service

**Reasoning**: The product catalog is a core domain bounded context. Separating it from product details (pricing, sizing) allows independent evolution and scaling. The catalog is read-heavy and can be scaled independently.

**Communication**:
- Inbound: REST from API Gateway
- Outbound: **Synchronous REST** to Product Detail Service (for enrichment)

**Inter-Service Communication Pattern**: When a client requests a product with `enrich=true`, the Product Service makes a synchronous HTTP call to the Product Detail Service to fetch price/size/design information. This is appropriate here because the client expects a complete response in real-time.

---

## 4. Product Detail Service (Port 8002)

**Purpose**: Manages product details - sizes, prices, designs, materials.

**Responsibilities**:
- CRUD operations on product details
- Price lookup endpoint
- Detail management independent of product catalog

**Reasoning**: Separated from the Product Service because:
1. Pricing may change frequently without affecting the product catalog
2. Different teams may own pricing vs. catalog
3. Price service may need different scaling characteristics
4. Enables future extensions (dynamic pricing, A/B pricing, etc.)

**Communication**: REST (inbound from Product Service and API Gateway)

---

## 5. Cart Service (Port 8003)

**Purpose**: Manages shopping carts and handles checkout.

**Responsibilities**:
- Add/remove/update items in user's cart
- Calculate cart totals
- Checkout processing
- Publishing order events to RabbitMQ

**Reasoning**: Cart management is a distinct bounded context with different access patterns (high write frequency, per-user state). The checkout triggers an asynchronous flow - once a user checks out, the rest of the processing (order creation, notifications) happens asynchronously.

**Communication**:
- Inbound: REST from API Gateway
- Outbound: 
  - **Synchronous REST** to Product Service (validate products during add-to-cart)
  - **Asynchronous** via RabbitMQ (publish `order.created` and `notification.order_placed` events at checkout)

---

## 6. Order Service (Port 8004)

**Purpose**: Manages order lifecycle.

**Responsibilities**:
- Consume order events from RabbitMQ
- Store and manage orders
- Provide order query endpoints
- Order status management

**Reasoning**: Orders have a different lifecycle from carts. Once created, orders go through states (CREATED → CONFIRMED → SHIPPED → DELIVERED). This domain has different consistency requirements and is a natural candidate for event-driven processing.

**Communication**:
- Inbound: 
  - **Asynchronous** from RabbitMQ (`order.created` events from Cart Service)
  - REST from API Gateway (order queries)

---

## 7. Notification Service (Port 8005)

**Purpose**: Event-driven notification dispatch (console logging for now).

**Responsibilities**:
- Consume notification events from RabbitMQ
- Log notifications to console (extensible to email/SMS/push)
- Maintain notification history

**Reasoning**: Notifications are a classic use case for asynchronous, event-driven processing. The notification service doesn't need to respond in real-time to the checkout request. By consuming events asynchronously, it:
1. Decouples notification logic from business flows
2. Can handle different notification channels independently
3. Can process events at its own pace (natural backpressure)
4. Failures don't affect the main business flow

**Communication**: 
- Inbound: **Asynchronous** from RabbitMQ (`notification.*` events)
- Outbound: Console logging (extensible to SMTP, SMS APIs, etc.)

---

## Communication Matrix

| From → To | Pattern | Protocol | Use Case |
|-----------|---------|----------|----------|
| Client → API Gateway | Sync | REST/HTTP | All client requests |
| API Gateway → All Services | Sync | REST/HTTP | Request routing |
| Product Service → Product Detail Service | Sync | REST/HTTP | Product enrichment |
| Cart Service → Product Service | Sync | REST/HTTP | Product validation |
| Cart Service → Order Service | **Async** | RabbitMQ | Order creation events |
| Cart Service → Notification Service | **Async** | RabbitMQ | Notification events |

---

## Extensibility

The architecture supports adding new services:
- **Payment Service**: Consume `order.created` events, publish `payment.completed`
- **Shipping Service**: Consume `payment.completed` events
- **Review Service**: Independent bounded context with REST API
- **Search Service**: CQRS pattern, consume product events for indexing
- **Recommendation Service**: Event-driven analytics processing
