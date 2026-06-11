# AI CBP MDO Service

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111.0-009688.svg)](https://fastapi.tiangolo.com/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0_async-red.svg)](https://www.sqlalchemy.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14+-336791.svg)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg)](https://www.docker.com/)
[![iGOT](https://img.shields.io/badge/platform-iGOT-orange.svg)](https://igotkarmayogi.gov.in/)

A FastAPI microservice that powers the **MDO-side approval workflow** and **SPV Admin designation approval workflow** for CBP (Competency-Based Plan) requests originating from the AI CBP standalone portal.

---

## Table of Contents

- [AI CBP MDO Service](#ai-cbp-mdo-service)
  - [Table of Contents](#table-of-contents)
  - [Project Overview](#project-overview)
  - [Project Structure](#project-structure)
  - [Key Features](#key-features)
  - [Tech Stack](#tech-stack)
  - [Quick Start](#quick-start)
    - [Prerequisites](#prerequisites)
    - [Installation](#installation)
    - [Environment Variables](#environment-variables)
    - [Run Application](#run-application)
  - [Run Using Docker](#run-using-docker)
    - [Build \& Run](#build--run)
    - [Common Docker Commands](#common-docker-commands)

---

## Project Overview

This service is the MDO-side approval layer of the iGOT ecosystem. The workflow begins on the AI CBP standalone portal, where users create CBP plans linked to designations (with role responsibilities, activities, and competencies). These plans are submitted as approval requests and arrive in this service with a `PENDING` status, awaiting review by the responsible MDO administrator.

**End-to-end flows:**

```
CBP Portal → MDO Approval
  └─ User creates CBP plan (designations + courses/competencies)
       └─ Submits for approval → approval_request (PENDING) stored in DB
            └─ MDO Admin reviews via this service
                 ├─ APPROVE → calls iGOT CBP Create API + Publish API → status = APPROVED
                 └─ REJECT  → stores rejection comments               → status = REJECTED

Designation Approval (SPV Admin)
  └─ User requests a new designation
       └─ Request stored as designation_approval (PENDING)
            └─ SPV Admin reviews via this service
                 ├─ APPROVE → status = APPROVED
                 └─ REJECT  → stores reviewer comments → status = REJECTED
```

MDO administrators can view pending CBP requests, drill into designation details, and take approval actions individually or in bulk. SPV Administrators manage designation approval requests. The system maintains a full audit trail of every action.

---

## Project Structure

```
src/
├── main.py                       # FastAPI application entry point
├── api/
│   └── v1/
│       ├── mdo_approval.py       # MDO approval endpoints
│       ├── designation_approval.py  # SPV Admin designation approval endpoints
│       └── kb_apis.py            # Proxy endpoints to iGOT (course/designation search)
├── controller/
│   ├── mdo_approval.py           # MDO approval business logic
│   └── designation_approval.py   # Designation approval business logic
├── core/
│   ├── auth.py                   # JWT validation & role-based access control (RBAC)
│   ├── configs.py                # Application configuration
│   ├── database.py               # Database connection and session management
│   ├── logger.py                 # Logging configuration
│   └── logging.conf              # Logging configuration file
├── crud/
│   ├── mdo_approval_request.py   # Database operations for MDO approval requests
│   └── designation_approval.py   # Database operations for designation approvals
├── models/
│   ├── mdo_approval.py           # SQLAlchemy models for MDO approval
│   ├── designation_approval.py   # SQLAlchemy model for designation approvals
│   └── user.py                   # SQLAlchemy User model
├── schemas/
│   ├── comman.py                 # Common schemas and enums
│   ├── mdo_approval.py           # Pydantic schemas for MDO approval
│   └── designation_approval.py   # Pydantic schemas for designation approval
└── services/
    ├── igot_service.py           # iGOT CBP plan create & publish API integration
    └── notification_service.py   # Email notification service (approval/rejection alerts)
templates/
└── emails/
    ├── cbplan_request_status_email.html          # CBP plan approval/rejection email
    ├── designation_approval_request_email copy.html  # Designation approved email
    └── designation_rejection_request_email.html   # Designation rejected email
pyproject.toml                    # Python project configuration
Dockerfile                        # Container configuration
.env                              # Environment variables
```

---

## Key Features

| Feature | Description |
|---------|-------------|
| **CBP → MDO Flow** | CBP plans from the CBP portal arrive as `PENDING` requests for MDO review |
| **Designation Approval (SPV Admin)** | SPV Admins review, approve, or reject new designation requests with comments |
| **Designation Review** | Detailed view of designations with role responsibilities, activities, and competencies |
| **Two-Step iGOT Integration** | On approval, calls the iGOT CBP **Create** API then the **Publish** API; stores the returned `igot_cbp_plan_id` |
| **iGOT Proxy APIs** | Course suggestion search and designation search proxied to iGOT platform |
| **Bulk Approval / Rejection** | Approve or reject all designations in a request in a single call |
| **Retry Publish** | Retry publishing a single failed item from an already-approved request |
| **Item-Level Rejection** | Reject individual designations with specific reviewer comments |
| **Email Notifications** | Automated email alerts on approval/rejection for both CBP plans and designation requests |
| **Role-Based Access Control** | JWT-based auth with role enforcement (`MDO_ADMIN`, `MDO_LEADER`, `SPV_ADMIN`) via Sunbird SSO |
| **Status Tracking** | `PENDING` → `APPROVED` / `REJECTED` with automatic transitions |
| **Search & Filtering** | Filter by status, date range, or search by request / org name |
| **Audit Trail** | Full history of MDO actions with timestamps and comments |
| **Pagination** | Efficient pagination for large datasets |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI 0.111.0 |
| Database | PostgreSQL 14+ with AsyncPG driver |
| ORM | SQLAlchemy 2.0 (async) |
| Auth | JWT via python-jose (Sunbird SSO / Keycloak) |
| Validation | Pydantic v2 |
| HTTP Client | httpx (async) |
| Package Manager | [uv](https://docs.astral.sh/uv/) |
| Containerisation | Docker |

---

## Quick Start

### Prerequisites

- Python 3.12+
- PostgreSQL 14+
- [uv](https://docs.astral.sh/uv/) package manager
- Docker *(optional, for containerised deployment)*

### Installation

```bash
# Clone repository
git clone <repository-url>
cd cbp-ai-mdo-service

# Install dependencies
uv sync

# Activate virtual environment
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows
```

### Environment Variables

Create a `.env` file in the project root:

```bash
LOG_LEVEL="INFO"
ENVIRONMENT="local"              # local | staging | production

# Database
DATABASE_URL="postgresql+asyncpg://user:password@localhost:5432/dbname"

# Roles required to access MDO endpoints
REQUIRED_ROLES=["MDO_ADMIN", "MDO_LEADER"]
# SPV_ADMIN role is enforced separately for designation approval endpoints

# iGOT / Karmayogi Bharat portal
KB_BASE_URL="https://portal.dev.karmayogibharat.net"
KB_AUTH_TOKEN="your-kb-auth-token-here"

# SSO Configuration
SUNBIRD_SSO_REALM="sunbird"
SUNBIRD_SSO_URL="https://portal.dev.karmayogibharat.net/auth/"

# Email Notifications
NOTIFICATION_BASE_URL="https://notification-service-url"
ENABLE_EMAIL_NOTIFICATION=false   # Set to true to enable email alerts
```

> **Note**: `KB_BASE_URL` and `KB_AUTH_TOKEN` are required for the approval (publish) flow. Without them, the iGOT Create and Publish API calls will fail.
>
> **Note**: Set `ENABLE_EMAIL_NOTIFICATION=true` and provide `NOTIFICATION_BASE_URL` to enable email notifications on approval/rejection actions.

### Run Application

```bash
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

API available at: `http://localhost:8000`

---

## Run Using Docker

### Build & Run

```bash
# Build image
docker build -t mdo-approval-system .

# Run container
docker run -d \
  --name mdo-approval-system \
  -p 8000:8000 \
  --env-file .env \
  mdo-approval-system
```

### Common Docker Commands

```bash
docker logs -f mdo-approval-system    # Stream logs
docker stop mdo-approval-system       # Stop container
docker rm mdo-approval-system         # Remove container
docker restart mdo-approval-system    # Restart container
```
