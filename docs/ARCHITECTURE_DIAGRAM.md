# Architecture Diagram — E-Commerce Microservices Platform

## High-Level Architecture

```mermaid
graph TB
    subgraph "Client Layer"
        Client["🌐 Client<br/>(Postman / Browser / Mobile App)"]
    end

    subgraph "Gateway Layer"
        AG["API Gateway<br/>Port 8000<br/>FastAPI + Uvicorn<br/>──────────<br/>• JWT Auth (HS256)<br/>• Rate Limiting (100/min)<br/>• CORS<br/>• Correlation ID<br/>• Request Logging<br/>• Service Discovery"]
    end

    subgraph "Service Discovery Layer"
        SR["Service Registry<br/>Port 8500<br/>FastAPI + Uvicorn<br/>──────────<br/>• Service Registration<br/>• Health Checks (30s)<br/>• Heartbeat Tracking"]
    end

    subgraph "Business Services Layer"
        PS["Product Service<br/>Port 8001<br/>──────────<br/>Catalog CRUD<br/>Product Enrichment"]
        PDS["Product Detail Service<br/>Port 8002<br/>──────────<br/>Price, Size, Design<br/>Material, Weight"]
        CS["Cart Service<br/>Port 8003<br/>──────────<br/>Cart Mgmt<br/>Checkout + Event Pub"]
        OS["Order Service<br/>Port 8004<br/>──────────<br/>Order Persistence<br/>Event Consumer"]
        NS["Notification Service<br/>Port 8005<br/>──────────<br/>Notification Store<br/>Event Consumer"]
    end

    subgraph "Messaging Layer"
        RMQ["RabbitMQ<br/>Port 5672 / 15672<br/>──────────<br/>Exchange: ecommerce_events (topic)<br/>Queues: order_events, notification_events"]
    end

    subgraph "Data Layer (In-Memory)"
        D1[("Products Dict")]
        D2[("Details Dict")]
        D3[("Carts Dict")]
        D4[("Orders Dict")]
        D5[("Notifications List")]
        D6[("Registry Dict")]
    end

    %% Client → Gateway
    Client -->|"HTTPS Requests"| AG

    %% Gateway → Services (Synchronous REST)
    AG -->|"REST"| PS
    AG -->|"REST"| PDS
    AG -->|"REST"| CS
    AG -->|"REST"| OS
    AG -->|"REST"| NS

    %% Service Discovery
    AG -.->|"Lookup Service"| SR
    PS -.->|"Register"| SR
    PDS -.->|"Register"| SR
    CS -.->|"Register"| SR
    OS -.->|"Register"| SR
    NS -.->|"Register"| SR

    %% Inter-Service Sync Calls
    PS -->|"GET /details/{id}"| PDS
    CS -->|"GET /products/{id}"| PS

    %% Async Messaging
    CS ==>|"Publish Events"| RMQ
    RMQ ==>|"order.created"| OS
    RMQ ==>|"notification.order_placed"| NS

    %% Data Store Connections
    PS --- D1
    PDS --- D2
    CS --- D3
    OS --- D4
    NS --- D5
    SR --- D6

    %% Health Checks
    SR -.->|"Health Ping /health (30s)"| PS
    SR -.->|"Health Ping /health (30s)"| PDS
    SR -.->|"Health Ping /health (30s)"| CS
    SR -.->|"Health Ping /health (30s)"| OS
    SR -.->|"Health Ping /health (30s)"| NS
    SR -.->|"Health Ping /health (30s)"| AG
```

---

## Application Startup Sequence

When you run `docker-compose up`, here is the **exact order** in which services start and the **methods that execute** at each step.

### Step-by-Step Startup Flow

```mermaid
sequenceDiagram
    participant DC as docker-compose
    participant RMQ as RabbitMQ (Port 5672)
    participant SR as Service Registry (Port 8500)
    participant AG as API Gateway (Port 8000)
    participant PS as Product Service (Port 8001)
    participant PDS as Product Detail Svc (Port 8002)
    participant CS as Cart Service (Port 8003)
    participant OS as Order Service (Port 8004)
    participant NS as Notification Service (Port 8005)

    Note over DC: Phase 1 — Infrastructure
    DC->>RMQ: Start RabbitMQ container
    RMQ-->>RMQ: rabbitmq-server starts
    RMQ-->>RMQ: Health: rabbitmq-diagnostics -q ping

    DC->>SR: Start Service Registry container
    SR-->>SR: uvicorn app:app --host 0.0.0.0 --port 8500
    SR-->>SR: @app.on_event("startup") → start_health_checker()
    SR-->>SR: Background thread: check_health() loop (every 30s)
    SR-->>SR: Health: GET /health → {"status": "healthy"}

    Note over DC: Phase 2 — Business Services (after SR + RMQ healthy)
    DC->>AG: Start API Gateway container
    AG-->>AG: uvicorn app:app --host 0.0.0.0 --port 8000
    AG-->>AG: @app.on_event("startup") → register_service()
    AG->>SR: POST /register {name: "api-gateway", host, port: 8000}
    SR-->>AG: 200 OK {service_id: "..."}

    DC->>PS: Start Product Service container
    PS-->>PS: uvicorn app:app --host 0.0.0.0 --port 8001
    PS-->>PS: @app.on_event("startup") → register_service()
    PS-->>PS: Initialize PRODUCTS dict with p001, p002, p003
    PS->>SR: POST /register {name: "product-service", host, port: 8001}
    SR-->>PS: 200 OK

    DC->>PDS: Start Product Detail Service container
    PDS-->>PDS: uvicorn app:app --host 0.0.0.0 --port 8002
    PDS-->>PDS: @app.on_event("startup") → register_service()
    PDS-->>PDS: Initialize DETAILS dict with p001, p002, p003 details
    PDS->>SR: POST /register {name: "product-detail-service", host, port: 8002}
    SR-->>PDS: 200 OK

    DC->>CS: Start Cart Service container
    CS-->>CS: uvicorn app:app --host 0.0.0.0 --port 8003
    CS-->>CS: @app.on_event("startup") → register_service()
    CS-->>CS: @app.on_event("startup") → init_rabbitmq()
    CS-->>CS: Connect to RabbitMQ with retry (5 attempts, 5s delay)
    CS-->>CS: Declare exchange: ecommerce_events (topic)
    CS-->>CS: Declare queues: order_events, notification_events
    CS-->>CS: Bind order_events → order.*, notification_events → notification.*
    CS->>SR: POST /register {name: "cart-service", host, port: 8003}
    SR-->>CS: 200 OK

    DC->>OS: Start Order Service container
    OS-->>OS: uvicorn app:app --host 0.0.0.0 --port 8004
    OS-->>OS: @app.on_event("startup") → register_service()
    OS-->>OS: @app.on_event("startup") → start_consumer()
    OS-->>OS: Connect to RabbitMQ with retry (5 attempts, 5s delay)
    OS-->>OS: Spawn daemon thread: consume_orders()
    OS-->>OS: Thread subscribes to order_events queue
    OS-->>OS: basic_consume(queue="order_events", callback=process_order)
    OS->>SR: POST /register {name: "order-service", host, port: 8004}
    SR-->>OS: 200 OK

    DC->>NS: Start Notification Service container
    NS-->>NS: uvicorn app:app --host 0.0.0.0 --port 8005
    NS-->>NS: @app.on_event("startup") → register_service()
    NS-->>NS: @app.on_event("startup") → start_consumer()
    NS-->>NS: Connect to RabbitMQ with retry (5 attempts, 5s delay)
    NS-->>NS: Spawn daemon thread: consume_notifications()
    NS-->>NS: Thread subscribes to notification_events queue
    NS-->>NS: basic_consume(queue="notification_events", callback=process_notification)
    NS->>SR: POST /register {name: "notification-service", host, port: 8005}
    SR-->>NS: 200 OK

    Note over DC: Phase 3 — System Ready
    SR-->>SR: All 6 services registered, health checks active
    Note over AG: API Gateway ready to accept client requests
```

---

### Detailed Startup Instructions

#### Phase 1: Infrastructure Services

**1. RabbitMQ (Message Broker)**

```
Container: rabbitmq
Image: rabbitmq:3-management
Ports: 5672 (AMQP), 15672 (Management UI)
Health Check: rabbitmq-diagnostics -q ping (every 10s)
```

- Docker Compose starts RabbitMQ first
- Broker initializes, management plugin loads
- Health check confirms broker is ready before dependent services start

**2. Service Registry (Port 8500)**

```
Container: service-registry
Entry Point: uvicorn app:app --host 0.0.0.0 --port 8500
Depends On: Nothing (first app service)
```

**Startup method execution order:**
1. `uvicorn` starts the ASGI server, loads `app:app` (FastAPI instance)
2. FastAPI triggers `@app.on_event("startup")` → calls `start_health_checker()`
3. `start_health_checker()` spawns a **daemon background thread** running `check_health()`
4. `check_health()` runs in an infinite loop:
   - Sleeps for 30 seconds
   - Iterates over all registered services
   - Sends `GET /health` to each service
   - Sets `status = "DOWN"` if the health check fails
   - Updates `last_heartbeat` on success
5. Service Registry is now ready to accept registrations at `POST /register`

---

#### Phase 2: Application Services (start after Registry + RabbitMQ are healthy)

**3. API Gateway (Port 8000)**

```
Container: api-gateway
Entry Point: uvicorn app:app --host 0.0.0.0 --port 8000
Depends On: service-registry (healthy)
```

**Startup method execution order:**
1. `uvicorn` starts, loads `app:app`
2. Module-level initialization:
   - `USERS` dict initialized with 3 hardcoded users (admin, user1, user2)
   - `SECRET_KEY` set for JWT signing
   - `rate_limiter` dict initialized (empty)
   - `service_cache` dict initialized (empty, 30s TTL)
3. FastAPI triggers `@app.on_event("startup")` → calls `register_service()`
4. `register_service()` sends `POST http://service-registry:8500/register` with:
   ```json
   {"name": "api-gateway", "host": "api-gateway", "port": 8000}
   ```
5. Middleware stack is active:
   - `correlation_id_middleware` → generates `X-Correlation-ID` for every request
   - `logging_middleware` → logs method, path, status, duration
6. Gateway is ready to proxy requests. For each incoming request:
   - Rate limit check (100 req/min per IP)
   - JWT verification (except public endpoints)
   - Role-based access check
   - Service discovery (query registry or use DNS fallback)
   - Proxy to downstream service with correlation ID header

**4. Product Service (Port 8001)**

```
Container: product-service
Entry Point: uvicorn app:app --host 0.0.0.0 --port 8001
Depends On: service-registry (healthy)
```

**Startup method execution order:**
1. `uvicorn` starts, loads `app:app`
2. Module-level initialization:
   - `PRODUCTS` dict pre-seeded with 3 products:
     - `p001`: Classic T-Shirt (Apparel)
     - `p002`: Running Shoes (Footwear)
     - `p003`: Leather Wallet (Accessories)
3. `@app.on_event("startup")` → `register_service()`
4. Registers with Service Registry as `"product-service"` on port 8001
5. Ready to serve product CRUD requests and enrich responses via Product Detail Service

**5. Product Detail Service (Port 8002)**

```
Container: product-detail-service
Entry Point: uvicorn app:app --host 0.0.0.0 --port 8002
Depends On: service-registry (healthy)
```

**Startup method execution order:**
1. `uvicorn` starts, loads `app:app`
2. Module-level initialization:
   - `DETAILS` dict pre-seeded with details for p001, p002, p003:
     - Includes sizes, price, currency, design, material, weight
3. `@app.on_event("startup")` → `register_service()`
4. Registers with Service Registry as `"product-detail-service"` on port 8002
5. Ready to serve detail lookups and price queries

**6. Cart Service (Port 8003)**

```
Container: cart-service
Entry Point: uvicorn app:app --host 0.0.0.0 --port 8003
Depends On: service-registry (healthy), rabbitmq (healthy)
```

**Startup method execution order:**
1. `uvicorn` starts, loads `app:app`
2. Module-level initialization:
   - `carts` dict initialized (empty — carts created on demand)
   - `rabbitmq_connection` and `rabbitmq_channel` set to `None`
3. `@app.on_event("startup")` → `register_service()` — registers with Service Registry
4. `@app.on_event("startup")` → `init_rabbitmq()`:
   - **Retry loop**: Attempts RabbitMQ connection up to 5 times with 5-second delays
   - `pika.BlockingConnection(pika.ConnectionParameters(host='rabbitmq'))` — opens AMQP connection
   - `connection.channel()` — creates a channel
   - `channel.exchange_declare(exchange='ecommerce_events', exchange_type='topic')` — declares the topic exchange
   - `channel.queue_declare(queue='order_events')` — declares the order events queue
   - `channel.queue_declare(queue='notification_events')` — declares the notification events queue
   - `channel.queue_bind(exchange='ecommerce_events', queue='order_events', routing_key='order.*')` — binds order queue
   - `channel.queue_bind(exchange='ecommerce_events', queue='notification_events', routing_key='notification.*')` — binds notification queue
5. Cart Service is ready. On checkout:
   - Publishes to `ecommerce_events` exchange with routing key `order.created`
   - Publishes to `ecommerce_events` exchange with routing key `notification.order_placed`

**7. Order Service (Port 8004)**

```
Container: order-service
Entry Point: uvicorn app:app --host 0.0.0.0 --port 8004
Depends On: service-registry (healthy), rabbitmq (healthy)
```

**Startup method execution order:**
1. `uvicorn` starts, loads `app:app`
2. Module-level initialization:
   - `orders` dict initialized (empty)
   - `user_orders` dict initialized (empty — maps user_id to list of order_ids)
3. `@app.on_event("startup")` → `register_service()` — registers with Service Registry
4. `@app.on_event("startup")` → `start_consumer()`:
   - **Retry loop**: Connect to RabbitMQ (5 attempts, 5-second delay between retries)
   - `pika.BlockingConnection(...)` — opens connection
   - `connection.channel()` — creates channel
   - `channel.queue_declare(queue='order_events')` — ensures queue exists
   - Spawns a **daemon thread** running `consume_orders()`:
     ```python
     thread = threading.Thread(target=consume_orders, daemon=True)
     thread.start()
     ```
   - Inside `consume_orders()`:
     - `channel.basic_consume(queue='order_events', on_message_callback=process_order, auto_ack=True)`
     - `channel.start_consuming()` — **blocks the thread**, waiting for messages
   - `process_order(ch, method, properties, body)` callback:
     - Parses JSON body
     - Extracts: order_id, user_id, items, total, shipping_address, correlation_id
     - Stores order in `orders` dict with `status = "CONFIRMED"`
     - Appends order_id to `user_orders[user_id]`
5. Order Service API is ready for queries while consumer thread processes events in background

**8. Notification Service (Port 8005)**

```
Container: notification-service
Entry Point: uvicorn app:app --host 0.0.0.0 --port 8005
Depends On: service-registry (healthy), rabbitmq (healthy)
```

**Startup method execution order:**
1. `uvicorn` starts, loads `app:app`
2. Module-level initialization:
   - `notifications` list initialized (empty)
3. `@app.on_event("startup")` → `register_service()` — registers with Service Registry
4. `@app.on_event("startup")` → `start_consumer()`:
   - **Retry loop**: Connect to RabbitMQ (5 attempts, 5-second delay between retries)
   - `pika.BlockingConnection(...)` — opens connection
   - `connection.channel()` — creates channel
   - `channel.queue_declare(queue='notification_events')` — ensures queue exists
   - Spawns a **daemon thread** running `consume_notifications()`:
     ```python
     thread = threading.Thread(target=consume_notifications, daemon=True)
     thread.start()
     ```
   - Inside `consume_notifications()`:
     - `channel.basic_consume(queue='notification_events', on_message_callback=process_notification, auto_ack=True)`
     - `channel.start_consuming()` — **blocks the thread**, waiting for messages
   - `process_notification(ch, method, properties, body)` callback:
     - Parses JSON body
     - Extracts: event_type, user_id, message, order_id, correlation_id
     - Logs notification with formatted console output
     - Appends notification to `notifications` list with `status = "DELIVERED"`
5. Notification Service API is ready for queries while consumer thread processes events in background

---

### Phase 3: System Ready — Health Check Loop Active

Once all services are registered, the Service Registry's **health check background thread** begins pinging all services every 30 seconds:

```
Service Registry check_health() loop:
  → GET http://api-gateway:8000/health
  → GET http://product-service:8001/health
  → GET http://product-detail-service:8002/health
  → GET http://cart-service:8003/health
  → GET http://order-service:8004/health
  → GET http://notification-service:8005/health
```

If any service fails to respond, its status is set to `DOWN` in the registry.

---

## Running the Application

### Prerequisites
- Docker and Docker Compose installed
- Ports 5672, 8000–8005, 15672 available

### Start All Services
```bash
docker-compose up --build
```

### What Happens on `docker-compose up`

| Step | What Starts | Method Executed First | What It Does |
|------|-------------|----------------------|--------------|
| 1 | RabbitMQ | `rabbitmq-server` | Broker starts, management plugin loads |
| 2 | Service Registry | `uvicorn` → `@startup` → `start_health_checker()` | Launches health check thread |
| 3 | API Gateway | `uvicorn` → `@startup` → `register_service()` | Registers with registry, middleware stack active |
| 4 | Product Service | `uvicorn` → `@startup` → `register_service()` | Seeds 3 products, registers |
| 5 | Product Detail Service | `uvicorn` → `@startup` → `register_service()` | Seeds 3 detail records, registers |
| 6 | Cart Service | `uvicorn` → `@startup` → `register_service()` + `init_rabbitmq()` | Registers, connects to RabbitMQ, declares exchange & queues |
| 7 | Order Service | `uvicorn` → `@startup` → `register_service()` + `start_consumer()` | Registers, spawns consumer daemon thread |
| 8 | Notification Service | `uvicorn` → `@startup` → `register_service()` + `start_consumer()` | Registers, spawns consumer daemon thread |

### Verify All Services Are Running
```bash
curl http://localhost:8500/services
```
Expected: All 6 services listed with `status: "UP"`

### Stop All Services
```bash
docker-compose down
```

---

## Request flow — Complete Checkout Example

This shows every method call from the moment a user checks out:

```mermaid
sequenceDiagram
    participant C as Client
    participant AG as API Gateway :8000
    participant CS as Cart Service :8003
    participant PS as Product Service :8001
    participant RMQ as RabbitMQ :5672
    participant OS as Order Service :8004
    participant NS as Notification Service :8005

    Note over C: Step 1 — Authenticate
    C->>AG: POST /api/auth/token {username, password}
    AG->>AG: validate_credentials()
    AG->>AG: create_jwt_token(username, role)
    AG-->>C: {access_token: "eyJ...", expires_in: 3600}

    Note over C: Step 2 — Browse Products
    C->>AG: GET /api/products (Bearer token)
    AG->>AG: verify_token() → extract role
    AG->>AG: get_service_url("product-service")
    AG->>PS: GET /products?page=1&size=10
    PS->>PS: Paginate PRODUCTS dict (active only)
    PS-->>AG: {products: [...], total: 3, page: 1}
    AG-->>C: Product list

    Note over C: Step 3 — Add Items to Cart
    C->>AG: POST /api/cart/user1/items {product_id: "p001", quantity: 2}
    AG->>CS: Proxy request
    CS->>PS: GET /products/p001 (validate product exists)
    PS-->>CS: {id: "p001", name: "Classic T-Shirt", active: true}
    CS->>CS: Fetch price, add to carts["user1"]["p001"]
    CS-->>AG: {message: "Item added", cart_size: 1}
    AG-->>C: Cart updated

    Note over C: Step 4 — Checkout
    C->>AG: POST /api/cart/user1/checkout {shipping_address: "123 Main St"}
    AG->>CS: Proxy request
    CS->>CS: Calculate total from carts["user1"]
    CS->>CS: Generate order_id = "ord-" + uuid4()
    CS->>RMQ: Publish to ecommerce_events (routing_key: order.created)
    CS->>RMQ: Publish to ecommerce_events (routing_key: notification.order_placed)
    CS->>CS: Clear carts["user1"]
    CS-->>AG: {order_id, total, status: "CREATED"}
    AG-->>C: Order confirmation

    Note over RMQ: Step 5 — Async Event Processing
    RMQ->>OS: Deliver to order_events queue
    OS->>OS: process_order() callback fires
    OS->>OS: Parse event, store in orders dict (status: CONFIRMED)
    OS->>OS: Map user_orders["user1"].append(order_id)

    RMQ->>NS: Deliver to notification_events queue
    NS->>NS: process_notification() callback fires
    NS->>NS: Log notification to console
    NS->>NS: Store in notifications list (status: DELIVERED)

    Note over C: Step 6 — View Order
    C->>AG: GET /api/orders/user1
    AG->>OS: Proxy request
    OS->>OS: Lookup user_orders["user1"], paginate
    OS-->>AG: {orders: [{order_id, status: "CONFIRMED", ...}]}
    AG-->>C: Order history
```

---

## Communication Patterns Summary

| Pattern | Source | → | Destination | Mechanism |
|---------|--------|---|-------------|-----------|
| **Synchronous REST** | Client | → | API Gateway | HTTP/JSON |
| **Synchronous REST** | API Gateway | → | All Services | HTTP/JSON (proxied) |
| **Synchronous REST** | Product Service | → | Product Detail Service | HTTP/JSON (enrichment) |
| **Synchronous REST** | Cart Service | → | Product Service | HTTP/JSON (validation) |
| **Async Messaging** | Cart Service | → | Order Service | RabbitMQ (order.created) |
| **Async Messaging** | Cart Service | → | Notification Service | RabbitMQ (notification.order_placed) |
| **Service Discovery** | All Services | → | Service Registry | HTTP/JSON (registration) |
| **Health Monitoring** | Service Registry | → | All Services | HTTP/JSON (GET /health every 30s) |

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Web Framework | FastAPI |
| ASGI Server | Uvicorn |
| Message Broker | RabbitMQ 3 (with Management Plugin) |
| RabbitMQ Client | Pika |
| HTTP Client | httpx (async) |
| Authentication | PyJWT (HS256) |
| Containerization | Docker + Docker Compose |
| Networking | Docker bridge network (`ecommerce-net`) |
| Data Storage | In-memory (Python dicts/lists) |
