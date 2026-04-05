# High-Level Design Document

## 1. Architecture Diagram

```
                         ┌─────────────────────┐
                         │      Clients         │
                         │  (Postman / curl)    │
                         └─────────┬───────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │        API Gateway            │
                    │     (Port 8000)               │
                    │  • JWT Authentication         │
                    │  • Rate Limiting              │
                    │  • Request Routing            │
                    │  • Correlation ID Tracing     │
                    │  • Logging                    │
                    └──────┬───┬───┬───┬───────────┘
                           │   │   │   │
              ┌────────────┘   │   │   └──────────────┐
              ▼                ▼   ▼                   ▼
   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
   │   Product    │  │    Cart      │  │    Order     │
   │   Service    │  │   Service    │  │   Service    │
   │  (Port 8001) │  │ (Port 8003)  │  │ (Port 8004)  │
   └──────┬───────┘  └──────┬───────┘  └──────▲───────┘
          │                  │                  │
          ▼                  │           ┌──────┘
   ┌──────────────┐          │           │ (Async)
   │Product Detail│          ▼           │
   │   Service    │  ┌──────────────┐    │
   │ (Port 8002)  │  │   RabbitMQ   │────┘
   └──────────────┘  │  (Port 5672)  │
        (Sync)       └──────┬───────┘
                            │ (Async)
                            ▼
                     ┌──────────────┐
                     │ Notification │
                     │   Service    │
                     │ (Port 8005)  │
                     └──────────────┘

        ┌──────────────────────────────────────┐
        │          Service Registry            │
        │           (Port 8500)                │
        │  All services register on startup    │
        └──────────────────────────────────────┘
```

## 2. Technology Stack

| Component | Technology | Justification |
|-----------|-----------|---------------|
| Language | Python 3.11 | Modern, async-capable, rich ecosystem |
| Framework | FastAPI | High performance, async, auto-docs, Pydantic validation |
| Message Broker | RabbitMQ | Reliable, supports topic routing, persistent messages |
| Containerization | Docker + Compose | Consistent environments, easy orchestration |
| Authentication | JWT (PyJWT) | Stateless, scalable, standard |
| HTTP Client | httpx | Async-capable, modern Python HTTP client |
| Service Discovery | Custom Registry | Lightweight, purpose-built for this use case |

## 3. Communication Patterns

### 3.1 Synchronous (REST/HTTP)
Used when the client needs an immediate response:
- **Client → API Gateway → Services**: All user-facing requests
- **Product Service → Product Detail Service**: Product enrichment (aggregator pattern)
- **Cart Service → Product Service**: Product validation during add-to-cart

### 3.2 Asynchronous (RabbitMQ / Event-Driven)
Used when operations can be processed independently:
- **Cart Service → RabbitMQ → Order Service**: `order.created` event
- **Cart Service → RabbitMQ → Notification Service**: `notification.order_placed` event

**Exchange**: `ecommerce_events` (topic exchange)
**Queues**: `order_events`, `notification_events`
**Routing Keys**: `order.*`, `notification.*`

## 4. Cross-Cutting Concerns

### 4.1 Logging and Tracing
- **Structured logging** in every service using Python's `logging` module
- **Correlation ID** propagated through all requests:
  - Generated at API Gateway (UUID)
  - Passed in `X-Correlation-ID` header to all downstream services
  - Included in RabbitMQ event payloads
  - Enables tracing a single user request across all services

### 4.2 Exception Handling
- **Global exception handler** in every FastAPI service catches unhandled exceptions
- **HTTP-specific exceptions** return proper status codes (400, 401, 403, 404, 500, 503)
- **RabbitMQ consumers** use `nack + requeue` for failed message processing
- **Circuit breaker pattern** via timeout and retry in HTTP clients

### 4.3 Security
- **JWT Authentication**: Stateless tokens with expiry, role-based claims
- **Role-Based Access Control (RBAC)**: Admin vs. user roles
- **Rate Limiting**: 100 requests/minute per IP at the gateway
- **Input Validation**: Pydantic models validate all request payloads
- **CORS**: Configured at API Gateway level
- **No credential leakage**: Secrets via environment variables

### 4.4 Scalability Options

| Strategy | Applicable When | Implementation |
|----------|----------------|----------------|
| **Horizontal Scaling** | Any service under load | Docker Compose `replicas` or Kubernetes HPA |
| **Database per Service** | Production deployment | Each service connects to its own DB instance |
| **CQRS** | Read-heavy services (Product) | Separate read/write models and stores |
| **Event Sourcing** | Order Service | Store events, derive state from event stream |
| **Caching** | Product catalog | Redis cache in front of Product Service |
| **Load Balancing** | Multiple instances | Nginx or Kubernetes Service as load balancer |
| **Message Queue Scaling** | High event throughput | Multiple RabbitMQ consumers, prefetch tuning |
| **API Gateway Scaling** | High request volume | Multiple gateway instances behind a load balancer |

**Current Design Choices for Scalability**:
1. Stateless services (no session state) → easy horizontal scaling
2. In-memory stores can be swapped for databases without architecture changes
3. Async messaging decouples services → no cascading load
4. Service registry enables dynamic discovery of scaled instances

## 5. Database Decisions (For Production)

| Service | Recommended DB | Reasoning |
|---------|---------------|-----------|
| Product Service | PostgreSQL | Relational data, complex queries, ACID |
| Product Detail Service | PostgreSQL or MongoDB | Semi-structured data, flexible schema |
| Cart Service | Redis | Fast read/write, TTL for abandoned carts |
| Order Service | PostgreSQL | Transactional data, reporting queries |
| Notification Service | MongoDB | Append-heavy, flexible schema, TTL indexes |
| Service Registry | etcd or Consul | Purpose-built for service discovery |

**Current Implementation**: All services use in-memory Python dictionaries (dicts/lists) for simplicity. The data layer is abstracted such that swapping to a real database requires changes only in the data access functions.

## 6. Design Patterns Used

| Pattern | Where | Purpose |
|---------|-------|---------|
| **API Gateway** | api-gateway | Single entry point, routing, auth |
| **Service Registry** | service-registry | Dynamic service discovery |
| **Aggregator** | Product Service | Fetches and combines data from Detail Service |
| **Event-Driven** | Cart → Order, Notification | Asynchronous processing |
| **Pub-Sub** | RabbitMQ topic exchange | Decoupled event distribution |
| **Database per Service** | All services | Independent data stores |
| **Correlation ID** | All services | Distributed tracing |
| **Circuit Breaker** | HTTP clients (timeout) | Fault tolerance |
| **Backend for Frontend** | API Gateway | Client-optimized API |

## 7. Assumptions

1. No UI is required; all testing via Postman/curl
2. In-memory data stores are acceptable (no persistence across restarts)
3. RabbitMQ is the chosen message broker (alternatives: Kafka for higher throughput)
4. JWT secret is shared via environment variables (production would use a vault)
5. Single instance per service (production would use replicas)
6. No HTTPS (production would terminate TLS at load balancer or gateway)
7. Pre-seeded sample data for products and details (3 products)
8. User accounts are hardcoded (production would have a User Service)
9. Console logging for notifications (production would integrate SMTP/push/SMS)
10. No payment processing (out of scope for first release)
