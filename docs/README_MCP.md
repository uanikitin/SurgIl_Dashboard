# MCP-серверы для Claude Code

Конфигурация в `.claude/settings.json`. Серверы запускаются автоматически при открытии проекта в Claude Code.

## Переменные окружения

| Переменная | Обязательна | Описание |
|---|---|---|
| `DATABASE_URL` | да | PostgreSQL connection string (полный доступ) |
| `POSTGRES_URL_RO` | нет | Read-only URL для MCP postgres (fallback → DATABASE_URL) |
| `POSTGRES_URL_RW` | нет | Write-access URL — только по явному разрешению |

### Создание read-only роли (рекомендуется)

```sql
-- Подключиться к БД как суперюзер
CREATE ROLE mcp_ro LOGIN PASSWORD 'readonly_pass' IN ROLE pg_read_all_data;
```

Затем в `.env`:
```
POSTGRES_URL_RO=postgresql://mcp_ro:readonly_pass@localhost:5432/surgil_db
```

Если `POSTGRES_URL_RO` не задан, скрипт `scripts/mcp_postgres_ro.sh` берёт `DATABASE_URL` из `.env`.

## MCP-серверы

### 1. filesystem

Доступ к файлам проекта. Root = корень репозитория.

```
npx -y @modelcontextprotocol/server-filesystem <project_root>
```

### 2. postgres

Read-only доступ к PostgreSQL через `scripts/mcp_postgres_ro.sh`.

Проверка доступа:
```bash
# Через psql
psql "$DATABASE_URL" -c "SELECT count(*) FROM wells;"

# Через MCP — Claude сам выполнит SQL через postgres MCP tool
```

### 3. git (через Bash)

Git-операции выполняются через встроенный Bash tool Claude Code.
Разрешённые команды в `.claude/settings.local.json`:
- `git status`, `git log`, `git diff`, `git branch`
- `git add`, `git commit`, `git push`, `git stash`

### 4. Команды (через Bash tool)

Белый список в `.claude/settings.local.json`:

| Команда | Назначение |
|---|---|
| `.venv/bin/pytest` | Запуск тестов |
| `.venv/bin/alembic current` | Текущая ревизия БД |
| `.venv/bin/alembic history` | История миграций |
| `uvicorn backend.app:app --reload` | Dev-сервер |
| `xelatex` | Сборка LaTeX → PDF |

## Запуск dev-сервера

```bash
# Установить зависимости
pip install -r requirements.txt

# Запустить миграции
alembic upgrade head

# Dev-сервер (порт 8000)
uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000
```

Главная страница: http://localhost:8000/

## Сборка LaTeX/PDF

Шаблоны: `backend/templates/latex/*.tex`

Ручная сборка одного шаблона:
```bash
cd backend/generated/pdf/
xelatex -interaction=nonstopmode <файл>.tex
```

В production PDF генерируются автоматически через FastAPI endpoint
при создании документов (актов, отчётов).

## Проверка MCP

```bash
# 1. Postgres MCP работает?
#    Claude: "выполни SELECT count(*) FROM wells"

# 2. Filesystem MCP работает?
#    Claude: "прочитай файл backend/settings.py через filesystem MCP"

# 3. Alembic
.venv/bin/alembic current
.venv/bin/alembic history --verbose | head -20
```

## Структура файлов

```
.claude/
├── settings.json       # MCP-серверы (project-level)
├── settings.local.json # Разрешения Bash (local, не коммитить)
└── agents/             # Специализированные агенты
scripts/
└── mcp_postgres_ro.sh  # Wrapper для postgres MCP
.env.example            # Шаблон переменных окружения
```
