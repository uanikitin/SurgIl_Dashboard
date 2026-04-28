-- ═══════════════════════════════════════════════════════════════
-- Migration b7e1a9f20001: add lora_sensor_assignment
-- Создаёт ТОЛЬКО новую таблицу + индексы. НИЧЕГО не удаляет.
-- Безопасно повторять (IF NOT EXISTS).
-- ═══════════════════════════════════════════════════════════════

\echo '=== Шаг 0: проверка состояния ==='
SELECT version_num AS current_alembic_version FROM alembic_version;
SELECT COUNT(*) AS lora_assignment_table_exists
FROM information_schema.tables
WHERE table_name = 'lora_sensor_assignment';

BEGIN;

\echo '=== Шаг 1: создание таблицы ==='
CREATE TABLE IF NOT EXISTS lora_sensor_assignment (
    id          SERIAL PRIMARY KEY,
    sensor_id   INTEGER NOT NULL
                REFERENCES lora_sensors(id) ON DELETE CASCADE,
    role        VARCHAR(10) NOT NULL,
    valid_from  TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    valid_to    TIMESTAMP WITHOUT TIME ZONE,
    note        VARCHAR(500),
    created_by  INTEGER,
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_lsa_role
        CHECK (role IN ('tube','line')),
    CONSTRAINT ck_lsa_valid_range
        CHECK (valid_to IS NULL OR valid_to > valid_from)
);

\echo '=== Шаг 2: индексы ==='
CREATE INDEX IF NOT EXISTS ix_lsa_sensor_from
    ON lora_sensor_assignment (sensor_id, valid_from);

CREATE INDEX IF NOT EXISTS ix_lsa_sensor_active
    ON lora_sensor_assignment (sensor_id)
    WHERE valid_to IS NULL;

\echo '=== Шаг 3: обновление alembic_version ==='
-- Обновляем ТОЛЬКО если сейчас стоит d7dd1888bb00.
-- Если alembic_version уже на другом ID — ничего не делаем (увидите 0 обновлений).
UPDATE alembic_version
SET version_num = 'b7e1a9f20001'
WHERE version_num = 'd7dd1888bb00';

\echo '=== Шаг 4: финальная проверка ==='
SELECT version_num AS new_alembic_version FROM alembic_version;
SELECT COUNT(*) AS lora_assignment_table_exists
FROM information_schema.tables
WHERE table_name = 'lora_sensor_assignment';

\echo 'Если всё ок — введите: COMMIT;'
\echo 'Если что-то не так — введите: ROLLBACK;'
-- НЕ коммитим автоматически: оставляем транзакцию открытой,
-- чтобы вы посмотрели результаты и решили сами.
