"""
Observation Snapshot Service — Phase C3.

Реализует validation + compatibility boundary для хранимых observation-блоков.
Согласно RFC observation_v1.0 (schema_version="1.0") и owner constraints (13 пунктов).

PUBLIC API: ровно 3 функции (без _ prefix):
  - load_and_validate_snapshot(db, block_id) -> SanitizedSnapshotResult
  - validate_snapshot_structure(snapshot, expected_kind) -> ValidationResult
  - classify_block_status(snapshot, validation) -> str

ОГРАНИЧЕНИЯ (owner constraints):
  - НЕ пишет в БД. НЕ меняет оригинальный snapshot.
  - НЕ вызывает C1/C2 preview-сервисы.
  - НЕ генерирует narrative-тексты.
  - НЕ создаёт metrics/comparisons/chart из воздуха.
  - Работает только на copy.deepcopy(snapshot).
"""
from __future__ import annotations

import copy
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.orm import Session
from sqlalchemy import text

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

CURRENT_V: dict[str, str] = {
    "observation_baseline": "obs_baseline_v1",
    "observation_period":   "obs_period_v1",
    "observation_segment":  "obs_segment_v1",
}
CURRENT_SCHEMA_VERSION = "1.0"

REQUIRED_TOP_KEYS_COMMON = ["_v", "schema_version", "computed_at", "period"]

# Для каждого kind — обязательные слои (top-level ключи в snapshot)
REQUIRED_LAYERS_PER_KIND: dict[str, list[str]] = {
    "observation_baseline": ["metrics", "quality", "flags"],
    "observation_period":   ["metrics", "quality", "comparisons", "diagnostics", "flags"],
    "observation_segment":  ["quality", "flags"],
    # неизвестный kind — валидируем только common keys
}

ALLOWED_BLOCK_STATUS = {
    "ok", "no_data", "insufficient_data", "partial",
    "legacy_v0", "invalid_schema", "corrupted",
}

# Priority списка: первый подходящий побеждает (corrupted > ... > ok)
BLOCK_STATUS_PRIORITY = [
    "corrupted",
    "invalid_schema",
    "legacy_v0",
    "no_data",
    "insufficient_data",
    "partial",
    "ok",
]

ALLOWED_VERDICTS_BY_CONTEXT: dict[str, set[str]] = {
    "vs_b1":       {"improvement", "degradation", "no_significant_change", "insufficient_data"},
    "vs_customer": {"match", "partial_match", "diverge", "insufficient_data"},
    "combined":    {"improvement", "degradation", "no_significant_change", "insufficient_data"},
    "trend":       {"rising", "falling", "stable", "insufficient_data"},
    "changepoint": {"detected", "borderline", "insufficient_data"},
}

# Ключи диагностики: разрешённые + явно запрещённые narrative-поля
ALLOWED_DIAGNOSTIC_KEYS = {"target", "context", "verdict", "magnitude", "requires_log_check"}
NARRATIVE_FIELD_NAMES = {"narrative", "description", "comment", "text", "message", "note"}

# Допустимые quality.status
ALLOWED_QUALITY_STATUS = {
    "ok", "sparse", "gap", "suspicious", "no_data", "insufficient_for_segmentation",
}

# Допустимые daily_table data_status
ALLOWED_DATA_STATUS = {"ok", "missing_our", "missing_customer", "invalid"}

# semver regex
SEMVER_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")

# Ключи recompute_params, необходимые для can_recompute=True
REQUIRED_RECOMPUTE_KEYS = {"well_id", "period"}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ValidationIssue:
    """Одна проблема валидации snapshot."""
    severity: Literal["corrupted", "invalid_schema", "warning", "info"]
    code: str       # "missing_field", "unknown_field", "invalid_enum", "nan_in_metric", ...
    path: str       # JSONPath-like: "metrics.p_tube.mean"
    message: str    # short technical description


@dataclass
class ValidationResult:
    """Агрегированный результат валидации snapshot."""
    issues: list[ValidationIssue] = field(default_factory=list)
    has_corrupted: bool = False
    has_invalid_schema: bool = False
    has_legacy: bool = False      # _v missing/wrong, schema_version mismatch
    has_partial: bool = False     # optional layer missing, required all present
    warnings_count: int = 0


@dataclass
class SanitizedSnapshotResult:
    """Результат C3: sanitized copy snapshot + metadata."""
    snapshot: dict                    # sanitized copy (НЕ оригинал в БД)
    block_status: str                 # вычисленный по priority rules
    original_block_status: str | None # что было в БД до C3 (для аудита)
    validation_warnings: list[str]    # non-blocking issues (codes + paths)
    can_recompute: bool               # params в БД позволяют запустить preview
    recompute_params: dict | None     # ТОЛЬКО из block.params, не из snapshot
    expected_kind: str                # echo
    block_id: int                     # echo


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------


def load_and_validate_snapshot(db: Session, block_id: int) -> SanitizedSnapshotResult:
    """
    Загружает observation-блок из БД, валидирует и sanitizes snapshot.

    НЕ меняет оригинальный data_snapshot в БД.
    Возвращает sanitized copy ТОЛЬКО для renderer/runtime.

    Args:
        db: SQLAlchemy session (только чтение)
        block_id: id в customer_report_block

    Returns:
        SanitizedSnapshotResult с sanitized snapshot, block_status и recompute_params

    Raises:
        ValueError: если блок не найден или params->>'source' != 'observation'
    """
    row = db.execute(
        text("""
            SELECT id, kind, params, data_snapshot, well_id, comment, in_report,
                   created_at, updated_at
            FROM customer_report_block
            WHERE id = :id AND params->>'source' = 'observation'
        """),
        {"id": block_id},
    ).fetchone()

    if not row:
        raise ValueError(f"Observation block {block_id} not found")

    snapshot = row.data_snapshot if isinstance(row.data_snapshot, dict) else {}
    expected_kind = row.kind or ""
    original_block_status = snapshot.get("block_status") if isinstance(snapshot, dict) else None

    # Validate на оригинале (не модифицируем его)
    validation = validate_snapshot_structure(snapshot, expected_kind)

    # Compatibility transform — работает ТОЛЬКО на deepcopy
    sanitized = _apply_compatibility(copy.deepcopy(snapshot), validation, expected_kind)

    # Classify (по sanitized + validation)
    block_status = classify_block_status(sanitized, validation)
    # Override stored block_status вычисленным
    sanitized["block_status"] = block_status

    # Recompute params — ТОЛЬКО из block.params, не из snapshot
    can_recompute, recompute_params = _extract_recompute_params(
        row.params if isinstance(row.params, dict) else {},
        expected_kind,
    )

    # validation_warnings — все issues кроме уже отражённых в block_status
    validation_warnings = [
        f"{i.code} @ {i.path}: {i.message}"
        for i in validation.issues
    ]

    return SanitizedSnapshotResult(
        snapshot=sanitized,
        block_status=block_status,
        original_block_status=original_block_status,
        validation_warnings=validation_warnings,
        can_recompute=can_recompute,
        recompute_params=recompute_params,
        expected_kind=expected_kind,
        block_id=block_id,
    )


def validate_snapshot_structure(
    snapshot: Any,
    expected_kind: str,
) -> ValidationResult:
    """
    Валидирует структуру snapshot без изменений.

    Работает на переданном объекте as-is (не копирует).
    Собирает все issues, устанавливает флаги has_corrupted / has_invalid_schema /
    has_legacy / has_partial.

    Args:
        snapshot: данные из customer_report_block.data_snapshot (любой тип)
        expected_kind: kind блока (observation_baseline / observation_period / observation_segment)

    Returns:
        ValidationResult
    """
    result = ValidationResult()

    # snapshot должен быть dict
    if not isinstance(snapshot, dict):
        _add_issue(result, "corrupted", "not_dict", "$", "snapshot is not a dict")
        return result

    # Top-level ключи + _v + schema_version
    _validate_top_level_keys(snapshot, expected_kind, result)
    _validate_v_and_schema_version(snapshot, expected_kind, result)

    # Если уже corrupted/legacy — дальнейшая валидация слоёв возможна, но не обязательна.
    # Продолжаем — чтобы собрать максимум проблем для диагностики.

    # Period
    period = snapshot.get("period")
    if period is not None:
        _validate_period(period, result)

    # block_status в snapshot (advisory)
    bs = snapshot.get("block_status")
    if bs is not None and not isinstance(bs, str):
        _add_issue(result, "corrupted", "invalid_type", "block_status",
                   f"block_status must be string, got {type(bs).__name__}")

    # Слои — только если kind известен
    layers_required = REQUIRED_LAYERS_PER_KIND.get(expected_kind, [])

    # Проверяем обязательные слои
    for layer in layers_required:
        if layer not in snapshot:
            # Специально: raw — optional для всех (block_status=partial если missing raw)
            # Для period: если layer обязателен — invalid_schema
            _add_issue(result, "invalid_schema", "missing_layer",
                       layer, f"required layer '{layer}' missing")

    # Проверяем raw отдельно (optional → partial если все required слои есть)
    _check_raw_partial(snapshot, expected_kind, result)

    # Валидируем содержимое слоёв если они есть
    if "metrics" in snapshot and isinstance(snapshot["metrics"], dict):
        _validate_metrics_layer(snapshot["metrics"], expected_kind, result)

    if "quality" in snapshot:
        _validate_quality_layer(snapshot["quality"], result)

    if "comparisons" in snapshot and expected_kind == "observation_period":
        _validate_comparisons_layer(snapshot["comparisons"], result)

    if "diagnostics" in snapshot:
        _validate_diagnostics_layer(snapshot["diagnostics"], expected_kind, result)

    if "raw" in snapshot and snapshot["raw"] is not None:
        _validate_raw_layer(snapshot["raw"], result)

    if "flags" in snapshot:
        _validate_flags_layer(snapshot["flags"], result)

    # Пересчитываем счётчики
    result.warnings_count = sum(
        1 for i in result.issues if i.severity == "warning"
    )

    return result


def classify_block_status(snapshot: dict, validation: ValidationResult) -> str:
    """
    Определяет block_status по priority rules.

    Priority (сверху вниз — первое подходящее побеждает):
    corrupted > invalid_schema > legacy_v0 > no_data > insufficient_data > partial > ok

    Args:
        snapshot: sanitized copy snapshot (после apply_compatibility)
        validation: результат validate_snapshot_structure

    Returns:
        str — один из ALLOWED_BLOCK_STATUS
    """
    # 1. corrupted wins
    if validation.has_corrupted:
        return "corrupted"
    # 2. invalid_schema
    if validation.has_invalid_schema:
        return "invalid_schema"
    # 3. legacy
    if validation.has_legacy:
        return "legacy_v0"
    # 4. data-based: читаем quality.status из sanitized snapshot
    quality_status = None
    if isinstance(snapshot, dict):
        quality = snapshot.get("quality")
        if isinstance(quality, dict):
            quality_status = quality.get("status")
    if quality_status == "no_data":
        return "no_data"
    if quality_status == "insufficient_data":
        return "insufficient_data"
    # 5. partial — required все валидны, но optional отсутствуют
    if validation.has_partial:
        return "partial"
    # 6. default
    return "ok"


# ---------------------------------------------------------------------------
# PRIVATE HELPERS — validation
# ---------------------------------------------------------------------------


def _add_issue(
    result: ValidationResult,
    severity: Literal["corrupted", "invalid_schema", "warning", "info"],
    code: str,
    path: str,
    message: str,
) -> None:
    """Добавляет ValidationIssue и устанавливает соответствующий флаг."""
    result.issues.append(ValidationIssue(severity=severity, code=code, path=path, message=message))
    if severity == "corrupted":
        result.has_corrupted = True
    elif severity == "invalid_schema":
        result.has_invalid_schema = True


def _validate_top_level_keys(
    snapshot: dict,
    expected_kind: str,
    result: ValidationResult,
) -> None:
    """Проверяет наличие обязательных top-level ключей."""
    for key in REQUIRED_TOP_KEYS_COMMON:
        if key not in snapshot:
            _add_issue(result, "invalid_schema", "missing_field",
                       key, f"required top-level key '{key}' missing")


def _validate_v_and_schema_version(
    snapshot: dict,
    expected_kind: str,
    result: ValidationResult,
) -> None:
    """
    Определяет legacy/corrupted из _v и schema_version.

    Правила (owner constraint #7):
    - schema_version отсутствует → legacy_v0
    - schema_version = "1.0" → current
    - schema_version minor bump (1.1, 1.2) → best-effort + warning
    - schema_version major != 1 → legacy_v0
    - schema_version не парсится как semver → corrupted
    - _v отсутствует → legacy_v0
    - _v не совпадает с CURRENT_V[kind] → legacy_v0
    """
    v_val = snapshot.get("_v")
    sv_val = snapshot.get("schema_version")

    # --- schema_version ---
    if sv_val is None:
        _add_issue(result, "warning", "missing_field",
                   "schema_version", "schema_version missing → legacy_v0")
        result.has_legacy = True
    elif not isinstance(sv_val, str) or not SEMVER_RE.match(sv_val):
        _add_issue(result, "corrupted", "invalid_semver",
                   "schema_version", f"invalid semver value: {sv_val!r}")
    else:
        major = sv_val.split(".")[0]
        if sv_val == CURRENT_SCHEMA_VERSION:
            pass  # current — ok
        elif major == "1":
            # minor bump — best-effort
            _add_issue(result, "warning", "schema_minor_bump",
                       "schema_version",
                       f"schema_version={sv_val!r} is future minor; best-effort only")
        else:
            # major mismatch → legacy_v0
            _add_issue(result, "warning", "schema_major_mismatch",
                       "schema_version",
                       f"schema_version major={major!r} != '1' → legacy_v0")
            result.has_legacy = True

    # --- _v ---
    if v_val is None:
        _add_issue(result, "warning", "missing_field",
                   "_v", "_v missing → legacy_v0")
        result.has_legacy = True
    elif not isinstance(v_val, str):
        _add_issue(result, "corrupted", "invalid_type",
                   "_v", f"_v must be string, got {type(v_val).__name__}")
    else:
        expected_v = CURRENT_V.get(expected_kind)
        if expected_v is not None and v_val != expected_v:
            _add_issue(result, "warning", "v_mismatch",
                       "_v", f"_v={v_val!r} != expected {expected_v!r} → legacy_v0")
            result.has_legacy = True
        elif expected_v is None and expected_kind:
            # unknown kind — warning только
            _add_issue(result, "warning", "unknown_kind",
                       "_v", f"unknown expected_kind={expected_kind!r}")


def _validate_period(period: Any, result: ValidationResult) -> None:
    """Проверяет period.from / period.to: существуют, парсятся как date, from <= to."""
    if not isinstance(period, dict):
        _add_issue(result, "invalid_schema", "invalid_type",
                   "period", f"period must be dict, got {type(period).__name__}")
        return

    from_val = period.get("from")
    to_val = period.get("to")

    if from_val is None:
        _add_issue(result, "invalid_schema", "missing_field", "period.from", "period.from missing")
    if to_val is None:
        _add_issue(result, "invalid_schema", "missing_field", "period.to", "period.to missing")

    if from_val is not None and to_val is not None:
        try:
            from datetime import date as _date
            d_from = _date.fromisoformat(str(from_val))
            d_to = _date.fromisoformat(str(to_val))
            if d_from > d_to:
                _add_issue(result, "corrupted", "inverted_period",
                           "period", f"period.from={from_val!r} > period.to={to_val!r}")
        except (ValueError, TypeError):
            _add_issue(result, "corrupted", "invalid_date",
                       "period", f"cannot parse period dates: from={from_val!r}, to={to_val!r}")


def _validate_metrics_layer(metrics: dict, kind: str, result: ValidationResult) -> None:
    """Проверяет Layer 2: структура metrics + NaN/Inf/impossible values."""
    numeric_metric_keys = ("p_tube", "p_line", "dp", "q")

    for metric_key in numeric_metric_keys:
        if metric_key not in metrics:
            continue
        metric = metrics[metric_key]
        if not isinstance(metric, dict):
            _add_issue(result, "invalid_schema", "invalid_type",
                       f"metrics.{metric_key}",
                       f"metrics.{metric_key} must be dict, got {type(metric).__name__}")
            continue
        for sub_key in ("mean", "median", "min", "max", "std", "cv", "slope"):
            if sub_key in metric:
                val = metric[sub_key]
                path = f"metrics.{metric_key}.{sub_key}"
                _check_impossible_values(val, path, result)
                # Специально: отрицательное давление — corrupted
                if val is not None and sub_key in ("mean", "median", "min") and metric_key in ("p_tube", "p_line"):
                    _check_negative_pressure(val, path, result)


def _check_impossible_values(value: Any, path: str, result: ValidationResult) -> None:
    """Проверяет одно числовое значение на NaN/Inf."""
    if value is None:
        return
    if isinstance(value, float):
        if math.isnan(value):
            _add_issue(result, "corrupted", "nan_in_metric", path,
                       f"NaN value at {path}")
        elif math.isinf(value):
            _add_issue(result, "corrupted", "inf_in_metric", path,
                       f"Inf value at {path}")


def _check_negative_pressure(value: Any, path: str, result: ValidationResult) -> None:
    """Проверяет, что давление не отрицательное."""
    if value is None:
        return
    try:
        if float(value) < 0:
            _add_issue(result, "corrupted", "negative_pressure", path,
                       f"negative pressure value={value} at {path}")
    except (TypeError, ValueError):
        pass


def _validate_quality_layer(quality: Any, result: ValidationResult) -> None:
    """Проверяет Layer 3: quality.status enum, coverage_pct в [0, 100]."""
    if not isinstance(quality, dict):
        _add_issue(result, "invalid_schema", "invalid_type",
                   "quality", f"quality must be dict, got {type(quality).__name__}")
        return

    status = quality.get("status")
    if status is not None and status not in ALLOWED_QUALITY_STATUS:
        _add_issue(result, "invalid_schema", "invalid_enum",
                   "quality.status",
                   f"quality.status={status!r} not in allowed values")

    metrics = quality.get("metrics")
    if isinstance(metrics, dict):
        cov = metrics.get("coverage_pct")
        if cov is not None:
            _check_impossible_values(cov, "quality.metrics.coverage_pct", result)
            try:
                cov_f = float(cov)
                if not (0.0 <= cov_f <= 100.0):
                    _add_issue(result, "corrupted", "coverage_out_of_range",
                               "quality.metrics.coverage_pct",
                               f"coverage_pct={cov_f} not in [0, 100]")
            except (TypeError, ValueError):
                pass

        days_with = metrics.get("days_with_data")
        days_req = metrics.get("days_requested")
        if days_with is not None and days_req is not None:
            try:
                if int(days_with) > int(days_req):
                    _add_issue(result, "corrupted", "days_inconsistency",
                               "quality.metrics",
                               f"days_with_data={days_with} > days_requested={days_req}")
            except (TypeError, ValueError):
                pass


def _validate_comparisons_layer(comparisons: Any, result: ValidationResult) -> None:
    """Проверяет Layer 4: with_b1, with_customer структуры, daily_table."""
    if not isinstance(comparisons, dict):
        _add_issue(result, "invalid_schema", "invalid_type",
                   "comparisons",
                   f"comparisons must be dict, got {type(comparisons).__name__}")
        return

    # with_b1
    with_b1 = comparisons.get("with_b1")
    if with_b1 is not None and isinstance(with_b1, dict):
        b1_status = with_b1.get("status")
        allowed_b1 = {"ok", "no_baseline", "baseline_corrupted", "insufficient_overlap"}
        if b1_status is not None and b1_status not in allowed_b1:
            _add_issue(result, "invalid_schema", "invalid_enum",
                       "comparisons.with_b1.status",
                       f"with_b1.status={b1_status!r} not in allowed values")

    # with_customer
    with_cust = comparisons.get("with_customer")
    if with_cust is not None and isinstance(with_cust, dict):
        cust_status = with_cust.get("status")
        allowed_cust = {"ok", "no_customer_data", "partial_customer_data", "period_mismatch"}
        if cust_status is not None and cust_status not in allowed_cust:
            _add_issue(result, "invalid_schema", "invalid_enum",
                       "comparisons.with_customer.status",
                       f"with_customer.status={cust_status!r} not in allowed values")

        daily_table = with_cust.get("daily_table")
        if daily_table is not None:
            _validate_daily_table(daily_table, result)


def _validate_daily_table(daily_table: Any, result: ValidationResult) -> None:
    """Каждый row имеет date, data_status в allowed enum."""
    if not isinstance(daily_table, list):
        _add_issue(result, "corrupted", "invalid_type",
                   "comparisons.with_customer.daily_table",
                   f"daily_table must be list, got {type(daily_table).__name__}")
        return

    for i, row in enumerate(daily_table):
        if not isinstance(row, dict):
            _add_issue(result, "invalid_schema", "invalid_type",
                       f"comparisons.with_customer.daily_table[{i}]",
                       f"daily_table row must be dict")
            continue
        if "date" not in row:
            _add_issue(result, "invalid_schema", "missing_field",
                       f"comparisons.with_customer.daily_table[{i}].date",
                       f"daily_table row missing 'date' field")
        data_status = row.get("data_status")
        if data_status is not None and data_status not in ALLOWED_DATA_STATUS:
            _add_issue(result, "invalid_schema", "invalid_enum",
                       f"comparisons.with_customer.daily_table[{i}].data_status",
                       f"data_status={data_status!r} not in allowed values")


def _validate_diagnostics_layer(
    diagnostics: Any,
    kind: str,
    result: ValidationResult,
) -> None:
    """
    Проверяет Layer 5: список, каждый entry имеет target/context/verdict/requires_log_check,
    verdict из ALLOWED_VERDICTS_BY_CONTEXT[context].
    """
    if not isinstance(diagnostics, list):
        _add_issue(result, "corrupted", "invalid_type",
                   "diagnostics", f"diagnostics must be list, got {type(diagnostics).__name__}")
        return

    for i, entry in enumerate(diagnostics):
        if not isinstance(entry, dict):
            _add_issue(result, "invalid_schema", "invalid_type",
                       f"diagnostics[{i}]", "diagnostic entry must be dict")
            continue

        # context + verdict validation
        context = entry.get("context")
        verdict = entry.get("verdict")
        if context is not None and context in ALLOWED_VERDICTS_BY_CONTEXT:
            allowed = ALLOWED_VERDICTS_BY_CONTEXT[context]
            if verdict is not None and verdict not in allowed:
                _add_issue(result, "invalid_schema", "invalid_enum",
                           f"diagnostics[{i}].verdict",
                           f"verdict={verdict!r} not allowed for context={context!r}")


def _validate_raw_layer(raw: Any, result: ValidationResult) -> None:
    """
    Проверяет Layer 1: chart_payload arrays согласованной длины.
    raw отсутствие → has_partial (не corrupted).
    """
    if not isinstance(raw, dict):
        _add_issue(result, "invalid_schema", "invalid_type",
                   "raw", f"raw must be dict, got {type(raw).__name__}")
        return

    cp = raw.get("chart_payload")
    if cp is None:
        return
    if not isinstance(cp, dict):
        _add_issue(result, "invalid_schema", "invalid_type",
                   "raw.chart_payload",
                   f"chart_payload must be dict, got {type(cp).__name__}")
        return

    array_keys = [k for k, v in cp.items() if isinstance(v, list)]
    if len(array_keys) < 2:
        return
    lengths = {k: len(cp[k]) for k in array_keys}
    unique_lengths = set(lengths.values())
    if len(unique_lengths) > 1:
        _add_issue(result, "corrupted", "mismatched_arrays",
                   "raw.chart_payload",
                   f"chart_payload arrays have mismatched lengths: {lengths}")


def _validate_flags_layer(flags: Any, result: ValidationResult) -> None:
    """Проверяет Layer 6: значения — boolean."""
    if not isinstance(flags, dict):
        _add_issue(result, "invalid_schema", "invalid_type",
                   "flags", f"flags must be dict, got {type(flags).__name__}")
        return
    for key, val in flags.items():
        if not isinstance(val, bool):
            _add_issue(result, "warning", "non_bool_flag",
                       f"flags.{key}",
                       f"flags.{key}={val!r} is not bool")


def _check_raw_partial(
    snapshot: dict,
    expected_kind: str,
    result: ValidationResult,
) -> None:
    """
    raw — optional layer. Если все required layers валидны (нет invalid_schema),
    но raw отсутствует → has_partial.
    raw отсутствие НЕ является invalid_schema.
    """
    if "raw" not in snapshot:
        # Проверяем: нет ли invalid_schema issues по required layers
        has_required_issues = any(
            i.severity == "invalid_schema" and i.code == "missing_layer"
            for i in result.issues
        )
        if not has_required_issues:
            result.has_partial = True


# ---------------------------------------------------------------------------
# PRIVATE HELPERS — compatibility transform
# ---------------------------------------------------------------------------


def _apply_compatibility(
    snapshot: dict,
    validation: ValidationResult,
    expected_kind: str = "",
) -> dict:
    """
    Применяет compatibility transform к КОПИИ snapshot.

    Owner constraint #3/#4/#5:
    - strip narrative-fields из diagnostics (+ warning)
    - add synthetic overall diagnostic для observation_period если missing
    - sort diagnostics по (target, context)
    - unknown top-level fields → preserve as opaque + warning
    - НЕ сортировать глобально весь dict
    - НЕ создавать metrics/comparisons/chart из воздуха

    Args:
        snapshot: deepcopy snapshot (изменяется in-place)
        validation: ValidationResult (может дополняться warnings)
        expected_kind: kind блока из БД (для _ensure_overall_diagnostic)
    """
    # 1. Normalize known optional metadata
    if "computed_at" not in snapshot:
        snapshot["computed_at"] = None

    # 2. Strip narrative из diagnostics
    if "diagnostics" in snapshot and isinstance(snapshot["diagnostics"], list):
        snapshot["diagnostics"] = _strip_narrative_from_diagnostics(
            snapshot["diagnostics"], validation
        )
        # 3. Ensure overall diagnostic для observation_period.
        # Используем expected_kind из БД (надёжнее чем _v, который может быть legacy)
        is_period = (
            expected_kind == "observation_period"
            or snapshot.get("_v") == CURRENT_V.get("observation_period")
        )
        if is_period:
            snapshot["diagnostics"] = _ensure_overall_diagnostic(
                snapshot["diagnostics"], "observation_period", validation
            )
        # 4. Normalize diagnostics order
        snapshot["diagnostics"] = _normalize_diagnostics_order(snapshot["diagnostics"])

    return snapshot


def _strip_narrative_from_diagnostics(
    diagnostics: list[dict],
    result: ValidationResult,
) -> list[dict]:
    """
    Удаляет narrative-поля из каждого diagnostic entry.
    Unknown ключи → warning code="unknown_field".
    Narrative ключи → warning code="narrative_field_stripped".
    """
    cleaned = []
    for i, entry in enumerate(diagnostics):
        if not isinstance(entry, dict):
            cleaned.append(entry)
            continue
        new_entry = {}
        for k, v in entry.items():
            if k in ALLOWED_DIAGNOSTIC_KEYS:
                new_entry[k] = v
            elif k in NARRATIVE_FIELD_NAMES:
                result.issues.append(ValidationIssue(
                    severity="warning",
                    code="narrative_field_stripped",
                    path=f"diagnostics[{i}].{k}",
                    message=f"narrative field '{k}' stripped from diagnostic entry",
                ))
                result.warnings_count += 1
            else:
                # Unknown field → warning + preserve as opaque? Нет — это diagnostic entry.
                # Для diagnostic entry unknown ключи тоже удаляются (они вне контракта).
                result.issues.append(ValidationIssue(
                    severity="warning",
                    code="unknown_field",
                    path=f"diagnostics[{i}].{k}",
                    message=f"unknown diagnostic field '{k}' stripped",
                ))
                result.warnings_count += 1
        cleaned.append(new_entry)
    return cleaned


def _ensure_overall_diagnostic(
    diagnostics: list[dict],
    kind: str,
    result: ValidationResult,
) -> list[dict]:
    """
    Для observation_period: добавляет synthetic overall entry если отсутствует.
    Owner constraint #5: только observation_period, только если missing.
    """
    if kind != "observation_period":
        return diagnostics

    has_overall = any(
        isinstance(e, dict) and e.get("target") == "overall"
        for e in diagnostics
    )
    if not has_overall:
        result.issues.append(ValidationIssue(
            severity="warning",
            code="missing_overall_added",
            path="diagnostics",
            message="missing overall diagnostic auto-added with verdict=insufficient_data",
        ))
        result.warnings_count += 1
        diagnostics = diagnostics + [
            {
                "target": "overall",
                "context": "combined",
                "verdict": "insufficient_data",
                "magnitude": None,
                "requires_log_check": True,
            }
        ]
    return diagnostics


def _normalize_diagnostics_order(diagnostics: list[dict]) -> list[dict]:
    """Сортирует diagnostics по (target, context) для стабильного renderer input."""
    def _sort_key(e: dict) -> tuple[str, str]:
        if not isinstance(e, dict):
            return ("", "")
        return (str(e.get("target", "")), str(e.get("context", "")))
    try:
        return sorted(diagnostics, key=_sort_key)
    except Exception:
        return diagnostics


# ---------------------------------------------------------------------------
# PRIVATE HELPERS — recompute params
# ---------------------------------------------------------------------------


def _extract_recompute_params(
    block_params: dict,
    expected_kind: str,
) -> tuple[bool, dict | None]:
    """
    Извлекает recompute_params ТОЛЬКО из block.params (не из snapshot).

    Обязательные ключи: well_id, period (с from + to внутри).
    Если отсутствуют → can_recompute=False, recompute_params=None.

    Owner constraint #8.
    """
    if not isinstance(block_params, dict):
        return False, None

    well_id = block_params.get("well_id")
    period = block_params.get("period")

    if well_id is None:
        return False, None
    if not isinstance(period, dict):
        return False, None
    if period.get("from") is None or period.get("to") is None:
        return False, None

    return True, dict(block_params)


# ---------------------------------------------------------------------------
# PRIVATE HELPERS — misc
# ---------------------------------------------------------------------------


def _is_finite_number(x: Any) -> bool:
    """True если x — конечное число (не None, не NaN, не Inf)."""
    if x is None:
        return False
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False
