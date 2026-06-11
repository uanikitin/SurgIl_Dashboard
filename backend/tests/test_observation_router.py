"""
Тесты для backend/routers/observation.py — Phase C4.

22 теста (минимум) для 9 эндпоинтов.

Разделены на:
  - Unit/integration тесты с реальной БД (well_id=21, скважина Сургильская-1).
  - Структурные тесты (pydantic validation, error codes).

Запуск:
    cd /Users/volodymyrnikitin/Documents/PythonFiles/SurgIl_Dashboard
    python -m pytest backend/tests/test_observation_router.py -v
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app import app
from backend.db import SessionLocal

client = TestClient(app)

# Скважина для интеграционных тестов — Сургильская-1 (id=21)
WELL_ID = 21
# Период с реальными данными (последние 30 дней)
_D_TO = date(2025, 5, 1)
_D_FROM = date(2025, 4, 1)
PERIOD_FROM = _D_FROM.isoformat()
PERIOD_TO = _D_TO.isoformat()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def cleanup_obs_blocks(db_session):
    """Удаляет все observation-блоки для well_id=21 до и после теста."""
    db_session.execute(
        text("""
            DELETE FROM customer_report_block
            WHERE params->>'source' = 'observation' AND well_id = :wid
        """),
        {"wid": WELL_ID},
    )
    db_session.commit()
    yield
    db_session.execute(
        text("""
            DELETE FROM customer_report_block
            WHERE params->>'source' = 'observation' AND well_id = :wid
        """),
        {"wid": WELL_ID},
    )
    db_session.commit()


def _minimal_baseline_snapshot() -> dict:
    """Минимально валидный snapshot obs_baseline_v1 для тестов CRUD."""
    return {
        "_v":             "obs_baseline_v1",
        "schema_version": "1.0",
        "computed_at":    "2025-04-01T00:00:00Z",
        "block_status":   "ok",
        "period":         {"from": PERIOD_FROM, "to": PERIOD_TO},
        "metrics": {
            "p_tube": {
                "mean": 15.0, "median": 15.0, "min": 14.0, "max": 16.0,
                "std": 0.5, "cv": 3.3, "slope": 0.001, "direction": "stable",
            },
            "p_line": {
                "mean": 10.0, "median": 10.0, "min": 9.0, "max": 11.0,
                "std": 0.4, "cv": 4.0, "slope": -0.001, "direction": "stable",
            },
            "dp": {
                "mean": 5.0, "median": 5.0, "min": 4.0, "max": 6.0,
                "std": 0.3, "cv": 6.0, "slope": 0.0, "direction": "stable",
            },
            "q": {
                "mean": 20.0, "median": 20.0, "min": 18.0, "max": 22.0,
                "std": 1.0, "cv": 5.0, "slope": 0.01, "direction": "rising",
            },
            "downtime": {
                "total_hours": 2.0, "events_count": 1,
                "max_event_hours": 2.0, "downtime_pct_of_period": 0.3,
            },
            "purge_events_count": 2,
        },
        "quality": {
            "status": "ok",
            "flags": [],
            "metrics": {
                "coverage_pct": 95.0, "gap_count": 0,
                "max_gap_hours": 1.0, "suspicious_spikes_count": 0,
                "false_zero_pct": 0.1, "days_with_data": 30, "days_requested": 30,
            },
        },
        "flags": {
            "low_coverage": False,
            "significant_gap": False,
            "outlier_detected": False,
        },
    }


def _save_block_via_api(snapshot: dict | None = None, kind: str = "observation_baseline") -> int:
    """Хелпер: создаёт блок через API, возвращает block_id."""
    snap = snapshot or _minimal_baseline_snapshot()
    resp = client.post(
        "/api/observation/blocks",
        json={
            "well_id":      WELL_ID,
            "kind":         kind,
            "title":        "Test block",
            "params":       {"well_id": WELL_ID, "period": {"from": PERIOD_FROM, "to": PERIOD_TO}},
            "data_snapshot": snap,
            "in_report":    True,
            "sort_order":   0,
        },
    )
    assert resp.status_code == 201, f"save_block failed: {resp.text}"
    return resp.json()["block_id"]


# ---------------------------------------------------------------------------
# 1. preview/baseline — 200 OK
# ---------------------------------------------------------------------------


def test_preview_baseline_200_ok():
    resp = client.post(
        "/api/observation/preview/baseline",
        json={
            "well_id": WELL_ID,
            "period":  {"from": PERIOD_FROM, "to": PERIOD_TO},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    snap = data["snapshot"]
    assert snap["_v"] == "obs_baseline_v1"
    assert "metrics" in snap
    assert "quality" in snap
    assert "flags" in snap
    assert "comparisons" not in snap
    assert "diagnostics" not in snap


# ---------------------------------------------------------------------------
# 2. preview/period — 200 OK
# ---------------------------------------------------------------------------


def test_preview_period_200_ok():
    resp = client.post(
        "/api/observation/preview/period",
        json={
            "well_id": WELL_ID,
            "period":  {"from": PERIOD_FROM, "to": PERIOD_TO},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    snap = data["snapshot"]
    assert snap["_v"] == "obs_period_v1"
    assert "metrics" in snap
    assert "comparisons" in snap
    assert "diagnostics" in snap


# ---------------------------------------------------------------------------
# 3. preview/segment — 200 OK
# ---------------------------------------------------------------------------


def test_preview_segment_200_ok():
    resp = client.post(
        "/api/observation/preview/segment",
        json={
            "well_id": WELL_ID,
            "period":  {"from": PERIOD_FROM, "to": PERIOD_TO},
            "sensitivity": "medium",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    snap = data["snapshot"]
    assert snap["_v"] == "obs_segment_v1"
    assert "quality" in snap


# ---------------------------------------------------------------------------
# 4. payload без well_id → 422
# ---------------------------------------------------------------------------


def test_preview_invalid_payload_422():
    resp = client.post(
        "/api/observation/preview/baseline",
        json={"period": {"from": PERIOD_FROM, "to": PERIOD_TO}},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 5. sensitivity='unknown' → 422
# ---------------------------------------------------------------------------


def test_preview_malformed_sensitivity_422():
    resp = client.post(
        "/api/observation/preview/segment",
        json={
            "well_id":     WELL_ID,
            "period":      {"from": PERIOD_FROM, "to": PERIOD_TO},
            "sensitivity": "unknown",
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 6. aggregation='weekly' → 422
# ---------------------------------------------------------------------------


def test_preview_malformed_aggregation_422():
    resp = client.post(
        "/api/observation/preview/segment",
        json={
            "well_id":     WELL_ID,
            "period":      {"from": PERIOD_FROM, "to": PERIOD_TO},
            "aggregation": "weekly",
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 7. period.from > period.to → 422
# ---------------------------------------------------------------------------


def test_preview_invalid_date_range_422():
    resp = client.post(
        "/api/observation/preview/baseline",
        json={
            "well_id": WELL_ID,
            "period":  {"from": PERIOD_TO, "to": PERIOD_FROM},  # перевёрнуто
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 8. save block — 201 OK
# ---------------------------------------------------------------------------


def test_save_block_200_ok(cleanup_obs_blocks):
    snap = _minimal_baseline_snapshot()
    resp = client.post(
        "/api/observation/blocks",
        json={
            "well_id":       WELL_ID,
            "kind":          "observation_baseline",
            "title":         "Базовый период",
            "params":        {"well_id": WELL_ID, "period": {"from": PERIOD_FROM, "to": PERIOD_TO}},
            "data_snapshot": snap,
            "in_report":     True,
            "sort_order":    0,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["block_id"], int)
    assert data["block_id"] > 0


# ---------------------------------------------------------------------------
# 9. save block — invalid schema (без _v) → 400
# ---------------------------------------------------------------------------


def test_save_block_invalid_schema_400(cleanup_obs_blocks):
    snap_no_v = {
        # Нет _v, нет schema_version
        "computed_at":  "2025-04-01T00:00:00Z",
        "block_status": "ok",
        "period":       {"from": PERIOD_FROM, "to": PERIOD_TO},
        "metrics":      {},
        "quality":      {"status": "ok", "flags": [], "metrics": {}},
        "flags":        {},
    }
    resp = client.post(
        "/api/observation/blocks",
        json={
            "well_id":       WELL_ID,
            "kind":          "observation_baseline",
            "title":         "Bad block",
            "params":        {"well_id": WELL_ID, "period": {"from": PERIOD_FROM, "to": PERIOD_TO}},
            "data_snapshot": snap_no_v,
            "in_report":     True,
        },
    )
    # Нет _v → legacy (has_legacy) → не invalid_schema, но validate будет иметь warnings.
    # С3 классифицирует: legacy → block_status=legacy_v0, has_invalid_schema=False.
    # Поэтому 400 по invalid_schema не триггерится. Проверяем что сохранение прошло (201).
    # Если хочется 400, нужно отдельное правило. Тест корректируем: нет _v И нет metrics → invalid.
    # Пересобираем: явно нет обязательного слоя metrics для baseline.
    snap_missing_layer = {
        "_v":             "obs_baseline_v1",
        "schema_version": "1.0",
        "computed_at":    "2025-04-01T00:00:00Z",
        "block_status":   "ok",
        "period":         {"from": PERIOD_FROM, "to": PERIOD_TO},
        # нет metrics, нет quality, нет flags
    }
    resp2 = client.post(
        "/api/observation/blocks",
        json={
            "well_id":       WELL_ID,
            "kind":          "observation_baseline",
            "title":         "Bad block",
            "params":        {"well_id": WELL_ID, "period": {"from": PERIOD_FROM, "to": PERIOD_TO}},
            "data_snapshot": snap_missing_layer,
            "in_report":     True,
        },
    )
    assert resp2.status_code == 400
    data2 = resp2.json()
    detail = data2.get("detail", data2)
    assert detail.get("error_code") == "invalid_snapshot_schema"


# ---------------------------------------------------------------------------
# 10. save block — corrupted snapshot (coverage_pct=200) → 409
# ---------------------------------------------------------------------------


def test_save_block_corrupted_409(cleanup_obs_blocks):
    snap = _minimal_baseline_snapshot()
    # coverage_pct > 100 → corrupted по C3._validate_quality_layer
    snap["quality"]["metrics"]["coverage_pct"] = 200.0
    resp = client.post(
        "/api/observation/blocks",
        json={
            "well_id":       WELL_ID,
            "kind":          "observation_baseline",
            "title":         "Corrupted block",
            "params":        {"well_id": WELL_ID, "period": {"from": PERIOD_FROM, "to": PERIOD_TO}},
            "data_snapshot": snap,
            "in_report":     True,
        },
    )
    assert resp.status_code == 409
    detail = resp.json().get("detail", resp.json())
    assert detail.get("error_code") == "snapshot_corrupted"


# ---------------------------------------------------------------------------
# 11. update block — 200 OK
# ---------------------------------------------------------------------------


def test_update_block_200_ok(cleanup_obs_blocks):
    block_id = _save_block_via_api()
    resp = client.put(
        f"/api/observation/blocks/{block_id}",
        json={"title": "Обновлённый заголовок", "sort_order": 5},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["block_id"] == block_id


# ---------------------------------------------------------------------------
# 12. update block — not found → 404
# ---------------------------------------------------------------------------


def test_update_block_not_found_404():
    resp = client.put(
        "/api/observation/blocks/9999999",
        json={"title": "Whatever"},
    )
    assert resp.status_code == 404
    detail = resp.json().get("detail", resp.json())
    assert detail.get("error_code") == "not_found"


# ---------------------------------------------------------------------------
# 13. delete block — 200 OK
# ---------------------------------------------------------------------------


def test_delete_block_200_ok(cleanup_obs_blocks):
    block_id = _save_block_via_api()
    resp = client.delete(f"/api/observation/blocks/{block_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["deleted"] is True

    # После удаления — GET возвращает 404
    get_resp = client.get(f"/api/observation/blocks/{block_id}")
    assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# 14. delete block — not found → 404
# ---------------------------------------------------------------------------


def test_delete_block_not_found_404():
    resp = client.delete("/api/observation/blocks/9999999")
    assert resp.status_code == 404
    detail = resp.json().get("detail", resp.json())
    assert detail.get("error_code") == "not_found"


# ---------------------------------------------------------------------------
# 15. get block by id — 200 OK
# ---------------------------------------------------------------------------


def test_get_block_by_id_200_ok(cleanup_obs_blocks):
    block_id = _save_block_via_api()
    resp = client.get(f"/api/observation/blocks/{block_id}")
    # Может быть 200 или 409 (corrupted). Нам нужен 200 для валидного snapshot.
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["block_id"] == block_id
    assert "snapshot" in data
    assert "block_status" in data


# ---------------------------------------------------------------------------
# 16. get block — corrupted → 409
# ---------------------------------------------------------------------------


def test_get_block_corrupted_409(db_session, cleanup_obs_blocks):
    """INSERT блок с NaN в pressure (coverage_pct=200) через SQL → GET → 409."""
    snap = _minimal_baseline_snapshot()
    snap["quality"]["metrics"]["coverage_pct"] = 200.0  # corrupted

    db_session.execute(
        text("""
            INSERT INTO customer_report_block
                (well_id, kind, title, params, data_snapshot, in_report, sort_order)
            VALUES
                (:wid, 'observation_baseline', 'Corrupted test',
                 CAST(:params_json AS jsonb),
                 CAST(:snap_json AS jsonb),
                 true, 0)
        """),
        {
            "wid":        WELL_ID,
            "params_json": json.dumps({"source": "observation", "well_id": WELL_ID,
                                       "period": {"from": PERIOD_FROM, "to": PERIOD_TO}}),
            "snap_json":  json.dumps(snap),
        },
    )
    db_session.commit()

    # Найдём только что вставленный блок
    row = db_session.execute(
        text("""
            SELECT id FROM customer_report_block
            WHERE well_id = :wid AND params->>'source' = 'observation'
            ORDER BY created_at DESC LIMIT 1
        """),
        {"wid": WELL_ID},
    ).fetchone()
    assert row is not None
    block_id = row[0]

    resp = client.get(f"/api/observation/blocks/{block_id}")
    assert resp.status_code == 409
    detail = resp.json().get("detail", resp.json())
    assert detail.get("error_code") == "block_corrupted"


# ---------------------------------------------------------------------------
# 17. get block — legacy v0 → 200, block_status=legacy_v0
# ---------------------------------------------------------------------------


def test_get_block_legacy_v0_graceful(db_session, cleanup_obs_blocks):
    """INSERT блок с _v='obs_period_v0' → GET → 200, block_status='legacy_v0'."""
    snap = {
        "_v":             "obs_period_v0",   # устаревший _v
        "schema_version": "1.0",
        "computed_at":    "2025-04-01T00:00:00Z",
        "block_status":   "ok",
        "period":         {"from": PERIOD_FROM, "to": PERIOD_TO},
        "metrics": {
            "p_tube": {"mean": 15.0, "median": 15.0, "min": 14.0, "max": 16.0,
                       "std": 0.5, "cv": 3.3, "slope": 0.0, "direction": "stable"},
            "p_line": {"mean": 10.0, "median": 10.0, "min": 9.0, "max": 11.0,
                       "std": 0.4, "cv": 4.0, "slope": 0.0, "direction": "stable"},
            "dp":     {"mean": 5.0, "median": 5.0, "min": 4.0, "max": 6.0,
                       "std": 0.3, "cv": 6.0, "slope": 0.0, "direction": "stable"},
            "q":      {"mean": 20.0, "median": 20.0, "min": 18.0, "max": 22.0,
                       "std": 1.0, "cv": 5.0, "slope": 0.0, "direction": "stable"},
            "downtime": {"total_hours": 0.0, "events_count": 0,
                         "max_event_hours": None, "downtime_pct_of_period": 0.0},
            "purge_events_count": 0,
        },
        "quality": {
            "status": "ok",
            "flags": [],
            "metrics": {
                "coverage_pct": 90.0, "gap_count": 0, "max_gap_hours": 0.0,
                "suspicious_spikes_count": 0, "false_zero_pct": 0.0,
                "days_with_data": 30, "days_requested": 30,
            },
        },
        "comparisons": {"with_b1": {"status": "no_baseline", "baseline_block_id": None,
                                    "baseline_period": None, "deltas": None},
                        "with_customer": {"status": "no_customer_data",
                                          "customer_period": {"from": PERIOD_FROM, "to": PERIOD_TO},
                                          "customer_days_available": 0,
                                          "customer_days_requested": 30,
                                          "mape": None, "q_source_used": None,
                                          "daily_table": []}},
        "diagnostics": [{"target": "overall", "context": "combined",
                         "verdict": "no_significant_change",
                         "magnitude": None, "requires_log_check": True}],
        "flags": {"low_coverage": False, "significant_gap": False, "outlier_detected": False,
                  "short_intersection": False, "baseline_mismatch_period": False,
                  "outdated_baseline_version": False, "invalid_comparison": False},
    }

    db_session.execute(
        text("""
            INSERT INTO customer_report_block
                (well_id, kind, title, params, data_snapshot, in_report, sort_order)
            VALUES
                (:wid, 'observation_period', 'Legacy test',
                 CAST(:params_json AS jsonb),
                 CAST(:snap_json AS jsonb),
                 true, 0)
        """),
        {
            "wid":         WELL_ID,
            "params_json": json.dumps({"source": "observation", "well_id": WELL_ID,
                                       "period": {"from": PERIOD_FROM, "to": PERIOD_TO}}),
            "snap_json":   json.dumps(snap),
        },
    )
    db_session.commit()

    row = db_session.execute(
        text("""
            SELECT id FROM customer_report_block
            WHERE well_id = :wid AND params->>'source' = 'observation'
            ORDER BY created_at DESC LIMIT 1
        """),
        {"wid": WELL_ID},
    ).fetchone()
    block_id = row[0]

    resp = client.get(f"/api/observation/blocks/{block_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["block_status"] == "legacy_v0"


# ---------------------------------------------------------------------------
# 18. chapter-preview — multi-block
# ---------------------------------------------------------------------------


def test_chapter_preview_multi_block(cleanup_obs_blocks):
    """Сохраняем 2 блока → POST /chapter-preview → 200, html содержит данные."""
    _save_block_via_api()
    _save_block_via_api()

    resp = client.post("/api/observation/chapter-preview", json={"well_id": WELL_ID})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["blocks_count"] >= 2
    assert isinstance(data["html"], str)
    assert len(data["html"]) > 0
    # HTML должен содержать классы блоков
    assert "obs-chapter" in data["html"]
    assert "obs-block" in data["html"]


# ---------------------------------------------------------------------------
# 19. chapter-preview — нет блоков → 200, html содержит "Нет блоков"
# ---------------------------------------------------------------------------


def test_chapter_preview_empty_returns_minimal_html(cleanup_obs_blocks):
    resp = client.post("/api/observation/chapter-preview", json={"well_id": WELL_ID})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["blocks_count"] == 0
    assert "Нет блоков" in data["html"]


# ---------------------------------------------------------------------------
# 20. preview не пишет в БД
# ---------------------------------------------------------------------------


def test_no_db_writes_from_preview(db_session):
    """Количество строк до и после preview/period должно совпадать."""
    count_before = db_session.execute(
        text("SELECT COUNT(*) FROM customer_report_block WHERE well_id = :wid"),
        {"wid": WELL_ID},
    ).scalar()

    resp = client.post(
        "/api/observation/preview/period",
        json={
            "well_id": WELL_ID,
            "period":  {"from": PERIOD_FROM, "to": PERIOD_TO},
        },
    )
    assert resp.status_code == 200

    count_after = db_session.execute(
        text("SELECT COUNT(*) FROM customer_report_block WHERE well_id = :wid"),
        {"wid": WELL_ID},
    ).scalar()

    assert count_before == count_after


# ---------------------------------------------------------------------------
# 21. unsupported schema_version → 400
# ---------------------------------------------------------------------------


def test_unsupported_schema_version_in_save_400(cleanup_obs_blocks):
    """snapshot с schema_version='abc' → corrupted → 409 (или 400 за invalid_schema)."""
    snap = _minimal_baseline_snapshot()
    snap["schema_version"] = "abc"  # не semver → corrupted по C3

    resp = client.post(
        "/api/observation/blocks",
        json={
            "well_id":       WELL_ID,
            "kind":          "observation_baseline",
            "title":         "Bad schema_version",
            "params":        {"well_id": WELL_ID, "period": {"from": PERIOD_FROM, "to": PERIOD_TO}},
            "data_snapshot": snap,
            "in_report":     True,
        },
    )
    # schema_version='abc' → invalid_semver → has_corrupted=True → 409
    assert resp.status_code == 409
    detail = resp.json().get("detail", resp.json())
    assert detail.get("error_code") == "snapshot_corrupted"


# ---------------------------------------------------------------------------
# 22. concurrent preview requests — все 200
# ---------------------------------------------------------------------------


def test_concurrent_preview_requests():
    """5 одновременных preview/baseline-запросов → все возвращают 200."""

    def _call(_: int) -> int:
        resp = client.post(
            "/api/observation/preview/baseline",
            json={
                "well_id": WELL_ID,
                "period":  {"from": PERIOD_FROM, "to": PERIOD_TO},
            },
        )
        return resp.status_code

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(_call, range(5)))

    assert all(code == 200 for code in results), f"Not all 200: {results}"


# ===========================================================================
# Phase O1 — PUT params (whitelist-merge) + chapter-preview parts/comment
# ===========================================================================


def _get_block_params(db_session, block_id: int) -> dict:
    """Читает params блока напрямую из БД (GET /blocks не возвращает params)."""
    row = db_session.execute(
        text("SELECT params FROM customer_report_block WHERE id = :id"),
        {"id": block_id},
    ).fetchone()
    db_session.commit()
    return row[0] if row and isinstance(row[0], dict) else {}


def test_o1_update_params_parts_persisted(cleanup_obs_blocks, db_session):
    block_id = _save_block_via_api()
    resp = client.put(
        f"/api/observation/blocks/{block_id}",
        json={"params": {"parts": {"metrics_table": False, "flags": True}}},
    )
    assert resp.status_code == 200, resp.text
    params = _get_block_params(db_session, block_id)
    assert params.get("parts") == {"metrics_table": False, "flags": True}
    # source/chapter не затронуты merge'ом
    assert params.get("source") == "observation"
    assert params.get("chapter") == "observation"


def test_o1_update_params_prefix_suffix_persisted(cleanup_obs_blocks, db_session):
    block_id = _save_block_via_api()
    resp = client.put(
        f"/api/observation/blocks/{block_id}",
        json={"params": {"prefix_note": "Вступление", "suffix_note": "Заключение"}},
    )
    assert resp.status_code == 200, resp.text
    params = _get_block_params(db_session, block_id)
    assert params.get("prefix_note") == "Вступление"
    assert params.get("suffix_note") == "Заключение"


def test_o1_update_params_protected_keys_ignored(cleanup_obs_blocks, db_session):
    """source/chapter/kind/period не перезаписываются через PUT params."""
    block_id = _save_block_via_api()
    resp = client.put(
        f"/api/observation/blocks/{block_id}",
        json={"params": {
            "source": "hacked", "chapter": "hacked", "kind": "evil",
            "period": {"from": "1999-01-01", "to": "1999-12-31"},
            "prefix_note": "ok-text",
        }},
    )
    assert resp.status_code == 200, resp.text
    params = _get_block_params(db_session, block_id)
    assert params.get("source") == "observation"
    assert params.get("chapter") == "observation"
    assert params.get("prefix_note") == "ok-text"
    assert params.get("period", {}).get("from") == PERIOD_FROM


def test_o1_update_params_data_snapshot_untouched(cleanup_obs_blocks, db_session):
    """PUT params не трогает data_snapshot."""
    block_id = _save_block_via_api()
    before = db_session.execute(
        text("SELECT data_snapshot FROM customer_report_block WHERE id=:id"),
        {"id": block_id},
    ).fetchone()[0]
    db_session.commit()
    client.put(f"/api/observation/blocks/{block_id}",
               json={"params": {"prefix_note": "x"}})
    after = db_session.execute(
        text("SELECT data_snapshot FROM customer_report_block WHERE id=:id"),
        {"id": block_id},
    ).fetchone()[0]
    db_session.commit()
    assert before == after


def test_o1_update_params_invalid_parts_type_400(cleanup_obs_blocks):
    block_id = _save_block_via_api()
    resp = client.put(
        f"/api/observation/blocks/{block_id}",
        json={"params": {"parts": "not-an-object"}},
    )
    assert resp.status_code == 400
    detail = resp.json().get("detail", resp.json())
    assert detail.get("error_code") == "invalid_params"


def test_o1_update_params_invalid_part_value_400(cleanup_obs_blocks):
    block_id = _save_block_via_api()
    resp = client.put(
        f"/api/observation/blocks/{block_id}",
        json={"params": {"parts": {"flags": "yes"}}},
    )
    assert resp.status_code == 400


def test_o1_update_params_unknown_part_dropped(cleanup_obs_blocks, db_session):
    """Неизвестный для kind part-ключ отбрасывается (без 400)."""
    block_id = _save_block_via_api()  # observation_baseline
    resp = client.put(
        f"/api/observation/blocks/{block_id}",
        json={"params": {"parts": {"metrics_table": False, "segments_table": True}}},
    )
    assert resp.status_code == 200, resp.text
    params = _get_block_params(db_session, block_id)
    # segments_table не входит в каталог baseline → отброшен
    assert params["parts"] == {"metrics_table": False}


def test_o1_chapter_preview_respects_prefix_note(cleanup_obs_blocks):
    """end-to-end: PUT params.prefix_note → виден в chapter-preview;
    отключение части prefix_note → исчезает."""
    block_id = _save_block_via_api()
    client.put(f"/api/observation/blocks/{block_id}",
               json={"params": {"prefix_note": "PREVIEW-MARK-O1"}})
    resp = client.post("/api/observation/chapter-preview", json={"well_id": WELL_ID})
    assert resp.status_code == 200
    assert "PREVIEW-MARK-O1" in resp.json()["html"]

    client.put(f"/api/observation/blocks/{block_id}",
               json={"params": {"parts": {"prefix_note": False}}})
    resp2 = client.post("/api/observation/chapter-preview", json={"well_id": WELL_ID})
    assert "PREVIEW-MARK-O1" not in resp2.json()["html"]


def test_o1_chapter_preview_respects_comment(cleanup_obs_blocks):
    """end-to-end: PUT comment → виден в chapter-preview HTML главы."""
    block_id = _save_block_via_api()
    client.put(f"/api/observation/blocks/{block_id}",
               json={"comment": "COMMENT-MARK-O1"})
    resp = client.post("/api/observation/chapter-preview", json={"well_id": WELL_ID})
    assert resp.status_code == 200
    assert "COMMENT-MARK-O1" in resp.json()["html"]
