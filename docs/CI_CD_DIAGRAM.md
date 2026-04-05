# CI/CD Pipeline Diagram

## Pipeline Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          CI/CD PIPELINE                                 │
│                     (GitHub Actions / GitLab CI)                        │
└─────────────────────────────────────────────────────────────────────────┘

┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  SOURCE  │───▶│  BUILD   │───▶│   TEST   │───▶│ PUBLISH  │───▶│  DEPLOY  │
│  STAGE   │    │  STAGE   │    │  STAGE   │    │  STAGE   │    │  STAGE   │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
```

## Detailed Pipeline Stages

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│  STAGE 1: SOURCE (Trigger)                                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ • Developer pushes code to Git repository                           │    │
│  │ • Trigger: push to main/develop OR pull request                     │    │
│  │ • Branch protection rules enforced                                   │    │
│  │ • Code review required for main branch                              │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  STAGE 2: BUILD (Parallel per service)                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ For each microservice (parallel):                                    │    │
│  │ ├── Install Python dependencies (pip install -r requirements.txt)    │    │
│  │ ├── Lint check (flake8 / ruff)                                      │    │
│  │ ├── Type check (mypy)                                               │    │
│  │ ├── Security scan (bandit)                                          │    │
│  │ └── Build Docker image                                              │    │
│  │                                                                      │    │
│  │ Services built in parallel:                                          │    │
│  │ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐        │    │
│  │ │  Service   │ │  Product   │ │   Cart     │ │   Order    │        │    │
│  │ │  Registry  │ │  Service   │ │  Service   │ │  Service   │        │    │
│  │ └────────────┘ └────────────┘ └────────────┘ └────────────┘        │    │
│  │ ┌────────────┐ ┌────────────┐ ┌────────────┐                       │    │
│  │ │API Gateway │ │Product Det.│ │Notification│                       │    │
│  │ │            │ │  Service   │ │  Service   │                       │    │
│  │ └────────────┘ └────────────┘ └────────────┘                       │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  STAGE 3: TEST                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ 3a. Unit Tests (parallel per service)                                │    │
│  │     ├── pytest for each service                                      │    │
│  │     └── Code coverage report (>80% target)                           │    │
│  │                                                                      │    │
│  │ 3b. Integration Tests                                                │    │
│  │     ├── docker-compose up (test environment)                         │    │
│  │     ├── Run API integration tests                                    │    │
│  │     ├── Test inter-service communication                             │    │
│  │     ├── Test RabbitMQ event flow                                     │    │
│  │     └── docker-compose down                                          │    │
│  │                                                                      │    │
│  │ 3c. Security Tests                                                   │    │
│  │     ├── OWASP dependency check                                       │    │
│  │     ├── Container image vulnerability scan (Trivy)                   │    │
│  │     └── API security tests (auth bypass, injection)                  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  STAGE 4: PUBLISH                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ • Tag Docker images with version (git SHA + semantic version)        │    │
│  │ • Push images to Container Registry                                  │    │
│  │   (Docker Hub / AWS ECR / GCP GCR)                                  │    │
│  │                                                                      │    │
│  │   registry/service-registry:v1.0.0-abc1234                          │    │
│  │   registry/api-gateway:v1.0.0-abc1234                               │    │
│  │   registry/product-service:v1.0.0-abc1234                           │    │
│  │   registry/product-detail-service:v1.0.0-abc1234                    │    │
│  │   registry/cart-service:v1.0.0-abc1234                              │    │
│  │   registry/order-service:v1.0.0-abc1234                             │    │
│  │   registry/notification-service:v1.0.0-abc1234                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  STAGE 5: DEPLOY                                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                                                                      │    │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐             │    │
│  │  │   STAGING    │───▶│  E2E TESTS  │───▶│ PRODUCTION  │             │    │
│  │  │ (Automatic)  │    │ (Automatic)  │    │  (Manual    │             │    │
│  │  │              │    │              │    │  Approval)   │             │    │
│  │  └─────────────┘    └─────────────┘    └─────────────┘             │    │
│  │                                                                      │    │
│  │  Deployment Strategy: Rolling Update / Blue-Green                    │    │
│  │  Infrastructure: Kubernetes (EKS/GKE) or Docker Swarm               │    │
│  │  Config Management: Helm charts / Kustomize                         │    │
│  │                                                                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## GitHub Actions Example (`.github/workflows/ci-cd.yml`)

```yaml
name: CI/CD Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  build-and-test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        service:
          - service-registry
          - api-gateway
          - product-service
          - product-detail-service
          - cart-service
          - order-service
          - notification-service
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install & Lint
        run: |
          cd ${{ matrix.service }}
          pip install -r requirements.txt
          pip install flake8
          flake8 . --max-line-length=120
      - name: Build Docker Image
        run: docker build -t ${{ matrix.service }}:test ./${{ matrix.service }}

  integration-test:
    needs: build-and-test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run Integration Tests
        run: |
          docker-compose up --build -d
          sleep 30
          # Run API tests
          docker-compose down

  publish:
    needs: integration-test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Push to Registry
        run: |
          echo "Push Docker images to container registry"

  deploy-staging:
    needs: publish
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to Staging
        run: echo "Deploy to staging environment"

  deploy-production:
    needs: deploy-staging
    environment: production
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to Production
        run: echo "Deploy to production (manual approval required)"
```

## Monitoring Post-Deployment

```
┌─────────────────────────────────────────────────────┐
│                POST-DEPLOY MONITORING                │
│                                                       │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐             │
│  │  Health  │  │  Logs   │  │ Metrics │             │
│  │  Checks │  │(ELK/CW) │  │(Grafana)│             │
│  └─────────┘  └─────────┘  └─────────┘             │
│                                                       │
│  • Auto-rollback on health check failures            │
│  • Alert on error rate > threshold                   │
│  • Log aggregation via correlation IDs               │
│  • Dashboard for service health and latency          │
└─────────────────────────────────────────────────────┘
```
