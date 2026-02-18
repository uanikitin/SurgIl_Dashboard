# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SurgIl_Dashboard is an industrial operations dashboard for gas well management. It tracks wells, equipment, reagent inventory, LoRa pressure sensors, and generates PDF documents for regulatory compliance.

**Tech Stack:**

- Backend: FastAPI (Python 3.11) + SQLAlchemy ORM + PostgreSQL
- Frontend: Jinja2 templates + vanilla JavaScript + Chart.js
- PDF Generation: XeLaTeX (must be installed on host system)
- Notifications: Telegram bot API, SMTP email
- Data Ingestion: SQLite import from LoRa sensors

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server
uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000

# Run database migrations
alembic upgrade head

# Create new migration (ONLY MANUAL - see warning below)
alembic revision -m "description"

# Rollback migration
alembic downgrade -1

# Utility scripts
python scripts/fill_reagent_catalog.py    # Populate reagent catalog
python scripts/sync_reagent_catalog.py    # Sync reagent catalog from external source
python scripts/run_pressure_update.py     # Manual pressure pipeline run (normally scheduled)
python scripts/diagnose_pressure_tz.py    # Debug timezone issues in pressure data
python scripts/clean_inf_pressure.py      # Remove invalid/infinite pressure readings
```

**External Dependencies:**

- PostgreSQL database
- XeLaTeX (for PDF generation) - install via `apt install texlive-xetex` or MacTeX

**No test suite exists** - consider adding pytest tests when modifying critical logic.

**CRITICAL: Database Migrations**
- NEVER use `alembic revision --autogenerate` — it drops existing tables and causes data loss!
- ALWAYS create migrations manually: `alembic revision -m "description"` and write SQL by hand
- Before any migration, backup the database

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
- `flow_analysis_report.tex` - Flow rate analysis report (scenario results, comparison, corrections log)

**Equipment Management:**
Split across multiple routers: `equipment_management.py`, `equipment_documents.py`, `equipment_admin.py`, and `well_equipment_integration.py`

**Reagent Accounting:**
Routes in `app.py` + API in `api/reagents.py` + service logic in `services/reagent_balance_service.py`

**Notifications:**
`documents/services/notification_service.py` handles Telegram and email notifications for document events.

**Pressure Data Pipeline:**
LoRa sensors → SQLite files → `pressure_pipeline.py` → PostgreSQL (`pressure_reading`, `pressure_hourly`, `pressure_latest`)

- `scripts/run_pressure_update.py` - Scheduler wrapper (launchd runs every minute, applies day/night intervals from `scripts/schedule_config.json`)
- `services/pressure_import_sqlite.py` - Imports raw readings from SQLite sensor databases
- `services/pressure_aggregate_service.py` - Hourly aggregation for historical charts
- `services/pressure_filter_service.py` - Spike/outlier detection and filtering
- `routers/pressure.py` - API endpoints for pressure data and charts

Schedule config (`scripts/schedule_config.json`):

- Day (07:00-22:00): 5-minute intervals
- Night: 30-minute intervals

**Flow Rate Analysis (Анализ дебита):**
Scenario-based gas flow rate calculation with corrections, comparison, and PDF reporting. Raw pressure data (`pressure_raw`) is never modified — corrections are applied in-memory during calculation.

Architecture: `pressure_raw` → `FlowScenario` (params) + `FlowCorrection` (edits) → pipeline → `FlowResult` (daily aggregates) → LaTeX PDF

- `models/flow_analysis.py` - ORM: FlowScenario, FlowCorrection, FlowResult
- `services/flow_rate/scenario_service.py` - Calculation pipeline with corrections + daily aggregation + scenario comparison
- `services/flow_rate/chart_renderer.py` - matplotlib → PNG for LaTeX reports
- `services/flow_rate/report_service.py` - LaTeX PDF generation (xelatex)
- `routers/flow_analysis.py` - 15 API endpoints (`/api/flow-analysis/*`) + HTML page route (`/flow-analysis`)
- `templates/flow_analysis.html` + `static/js/flow_analysis.js` - Analysis page UI
- `templates/latex/flow_analysis_report.tex` - LaTeX report template

Pipeline (reuses all existing `flow_rate/` modules without modification):
`get_pressure_data` → `clean_pressure` → **apply_corrections** → `smooth_pressure` → `calculate_flow_rate` → `calculate_purge_loss` → `PurgeDetector` → `recalculate_purge_loss` → `calculate_cumulative` → `detect_downtime` → `build_summary` → `aggregate_to_daily`

Correction types: `exclude` (NaN+ffill), `interpolate` (linear/nearest/spline), `manual_value` (fixed P), `clamp` (clip to range)

DB tables: `flow_scenario`, `flow_correction`, `flow_result` (migration: `5031002768e7`)

**Background Jobs API:**
`routers/jobs_api.py` provides endpoints for automated tasks:

- `POST /api/jobs/reagent-expense/auto-create` - Auto-create reagent expense documents
- `POST /api/jobs/send/telegram/{document_id}` - Send document via Telegram
- `POST /api/jobs/send/email/{document_id}` - Send document via email

Jobs require either `X-Job-Secret` header (for cron) or user session (for UI).

### Known Technical Debt

1. **Monolithic app.py** (~3700 lines) - contains mixed concerns (auth, admin, wells, reagents)
2. **Route duplication** - `well_equipment_integration.py` duplicates routes from `equipment_management.py`
3. **Business logic in routes** - SQLAlchemy queries directly in FastAPI handlers

See `DUPLICATES_AND_DEAD_CODE.md` and `QUICK_WINS.md` for detailed analysis.

### Claude Agent Specializations

The `.claude/agents/` directory contains specialized agent configurations:

- **Analyst** - Business logic and workflow analysis
- **SQLArchitect** - Database schema design
- **APIEngineer** - Backend API development
- **SignalProcessingEngineer** - Pressure time-series analysis
- **DocumentEngineer** - LaTeX/GOST document templates
- **NotificationsEngineer** - Telegram/email notifications
- **UIDesigner** - Dashboard interface design
- **ValidationEngineer** - Data verification
- **DataIntegration** - ETL and data import pipelines
- **RecoveryEngineer** - Backup and recovery planning

Note: Technical documentation files (`PROJECT_OVERVIEW.md`, `QUICK_WINS.md`) are in Russian.

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
- `PressureReading` / `PressureHourly` / `PressureLatest` - LoRa sensor pressure data (raw, aggregated, current)
- `LoraSensor` - LoRa sensor registry linked to wells
- `FlowScenario` / `FlowCorrection` / `FlowResult` - Flow rate analysis scenarios, corrections, and daily results

## Route Documentation

See `ROUTES_MAP.md` for exhaustive route documentation including:

- All HTTP methods and URLs
- Handler functions and templates
- Response types (HTML, JSON, Redirect, File)
