# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SurgIl_Dashboard is an industrial operations dashboard for gas well management. It tracks wells, equipment, reagent inventory, and generates PDF documents for regulatory compliance.

**Tech Stack:**

- Backend: FastAPI (Python 3.11.9) + SQLAlchemy ORM + PostgreSQL
- Frontend: Jinja2 templates + vanilla JavaScript + Chart.js
- PDF Generation: XeLaTeX (must be installed on host system)
- Notifications: Telegram bot API, SMTP email

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

# Utility scripts
python scripts/fill_reagent_catalog.py    # Populate reagent catalog
python scripts/sync_reagent_catalog.py    # Sync reagent catalog from external source
```

**External Dependencies:**

- PostgreSQL database
- XeLaTeX (for PDF generation) - install via `apt install texlive-xetex` or MacTeX

**No test suite exists** - consider adding pytest tests when modifying critical logic.

## Architecture

### Directory Structure

```text
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
├── documents/          # Document generation subsystem
│   ├── models.py       # Document, DocumentType, DocumentItem models
│   ├── service.py      # Document CRUD operations
│   ├── numbering.py    # Auto-numbering by type/well/period
│   └── services/       # Specialized services (reagent_expense, auto_create, notifications)
├── config/             # Equipment types, status registry
├── schemas/            # Pydantic request/response models
├── templates/
│   ├── *.html          # Jinja2 HTML templates
│   └── latex/          # LaTeX templates for PDF documents
└── static/
    ├── css/
    └── js/             # Chart.js visualizations
```

### Request Flow

1. Routes in `app.py` or `routers/*.py` handle HTTP requests
2. Services in `services/` contain business logic
3. Repositories in `repositories/` handle database operations
4. Models in `models/` define SQLAlchemy entities

### Key Subsystems

**Document Generation Pipeline:**
Form data → Service logic → LaTeX template (Jinja2) → XeLaTeX compilation → PDF stored in `backend/generated/pdf/`

LaTeX templates in `templates/latex/`:

- `well_handover.tex` - Well handover acts
- `equipment_act.tex`, `equipment_install.tex`, `equipment_removal.tex` - Equipment documents
- `reagent_expense.tex`, `reagent_expense_split.tex` - Reagent expense acts

**Equipment Management:**
Split across multiple routers: `equipment_management.py`, `equipment_documents.py`, `equipment_admin.py`, and `well_equipment_integration.py`

**Reagent Accounting:**
Routes in `app.py` + API in `api/reagents.py` + service logic in `services/reagent_balance_service.py`

**Notifications:**
`documents/services/notification_service.py` handles Telegram and email notifications for document events.

### Known Technical Debt

1. **Monolithic app.py** (~3400 lines) - contains mixed concerns (auth, admin, wells, reagents)
2. **Route duplication** - `well_equipment_integration.py` duplicates routes from `equipment_management.py`
3. **Business logic in routes** - SQLAlchemy queries directly in FastAPI handlers

See `DUPLICATES_AND_DEAD_CODE.md` and `QUICK_WINS.md` for detailed analysis.

## Configuration

Environment variables (`.env`):

**Required:**

- `DATABASE_URL` - PostgreSQL connection string
- `SECRET_KEY` - Session encryption key

**Optional:**

- `APP_TITLE` - Dashboard title
- `TZ` - Timezone (default: Asia/Tashkent)
- `JOB_API_SECRET` - Secret for background job API endpoints

**Notifications (optional):**

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_DEFAULT_CHAT_ID` - Telegram notifications
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM` - Email notifications

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
