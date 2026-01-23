# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SurgIl_Dashboard is an industrial operations dashboard for gas well management. It tracks wells, equipment, reagent inventory, and generates PDF documents for regulatory compliance.

**Tech Stack:**
- Backend: FastAPI (Python 3.11.9) + SQLAlchemy ORM + PostgreSQL
- Frontend: Jinja2 templates + vanilla JavaScript + Chart.js
- PDF Generation: XeLaTeX

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server
uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000

# Run database migrations
alembic upgrade head

# Create new migration
alembic revision --autogenerate -m "description"

# Rollback migration
alembic downgrade -1
```

## Architecture

### Directory Structure

```
backend/
├── app.py              # Main FastAPI app (monolithic - contains auth, admin, wells, reagents)
├── settings.py         # Pydantic settings from .env
├── db.py               # SQLAlchemy engine and session factory
├── auth.py             # Password hashing and user verification
├── models/             # SQLAlchemy ORM models
├── services/           # Business logic layer
├── repositories/       # Data access layer
├── routers/            # FastAPI route handlers
├── api/                # JSON API endpoints (/api/wells, /api/reagents)
├── documents/          # Document generation subsystem (LaTeX templates, PDF output)
├── config/             # Equipment types, status registry
├── schemas/            # Pydantic request/response models
├── templates/          # Jinja2 HTML templates
│   └── latex/          # LaTeX document templates
└── static/
    ├── css/
    └── js/             # Chart.js visualizations (visual_timeline.js, reagents.js, well_events_chart.js)
```

### Request Flow

1. Routes in `app.py` or `routers/*.py` handle HTTP requests
2. Services in `services/` contain business logic
3. Repositories in `repositories/` handle database operations
4. Models in `models/` define SQLAlchemy entities

### Key Subsystems

**Document Generation Pipeline:**
Form data → Service logic → LaTeX template (Jinja2) → XeLaTeX compilation → PDF stored in `backend/generated/pdf/`

**Equipment Management:**
Split across multiple routers: `equipment_management.py`, `equipment_documents.py`, `equipment_admin.py`, and `well_equipment_integration.py`

**Reagent Accounting:**
Routes in `app.py` + API in `api/reagents.py` + service logic in `services/reagent_balance_service.py`

### Known Technical Debt

1. **Monolithic app.py** (~3400 lines) - contains mixed concerns (auth, admin, wells, reagents)
2. **Route duplication** - `well_equipment_integration.py` duplicates routes from `equipment_management.py`
3. **Business logic in routes** - SQLAlchemy queries directly in FastAPI handlers

See `DUPLICATES_AND_DEAD_CODE.md` and `QUICK_WINS.md` for detailed analysis.

## Configuration

Environment variables (`.env`):
- `DATABASE_URL` - PostgreSQL connection string
- `SECRET_KEY` - Session encryption key
- `TZ` - Timezone (default: Asia/Tashkent)
- `APP_TITLE` - Dashboard title

## Key Models

- `Well` - Gas wells with coordinates and status
- `Equipment` / `EquipmentInstallation` - Equipment catalog and installation tracking
- `Event` - Well events (pressure readings, reagent injections)
- `ReagentCatalog` / `ReagentSupply` - Reagent reference data and transactions
- `Document` / `DocumentType` / `DocumentItem` - Document management system
- `DashboardUser` / `DashboardLoginLog` - User accounts and audit trail

## Route Documentation

See `ROUTES_MAP.md` for exhaustive route documentation including:
- All HTTP methods and URLs
- Handler functions and templates
- Response types (HTML, JSON, Redirect, File)
