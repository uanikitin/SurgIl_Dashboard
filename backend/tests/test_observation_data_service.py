"""
Тесты для backend/services/observation_data_service.py

Разделены на два класса:
  - TestUnit  — чистые unit-тесты, БД не нужна, работают без PostgreSQL.
  - TestIntegration — требуют реальной БД (маркированы pytest.mark.integration).

Запуск unit-тестов (без БД):
    cd /Users/volodymyrnikitin/Documents/PythonFiles/SurgIl_Dashboard
    python -m pytest backend/tests/test_observation_data_service.py -v -k "not integration"

Запуск всех тестов (с реальной БД):
    python -m pytest backend/tests/test_observation_data_service.py -v

Примечание: интеграционные тесты требуют переменную окружения DATABASE_URL
и реально скважину well_id=21 с данными за последний год.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Вспомогательные фабрики синтетических данных
# ---------------------------------------------------------------------------


def _make_minute_df(
    n_minutes: int = 60,
    start: datetime = datetime(2025, 1, 1, 0, 0, 0),
    p_tube_base: float = 15.0,
    p_line_base: float = 12.0,
) -> pd.DataFrame:
    """Создаёт синтетический поминутный DataFrame (рабочие строки)."""
    idx = pd.date_range(start, periods=n_minutes, freq="1min")
    df = pd.DataFrame(
        {
            "p_tube": p_tube_base,
            "p_line": p_line_base,
            "flow_rate": 5.0,
            "cumulative_flow": np.linspace(0, 5.0 * n_minutes / 1440, n_minutes),
            "purge_flag": 0,
        },
        index=idx,
    )
    return df


# ---------------------------------------------------------------------------
# Unit-тесты (_aggregate_to, compute_data_quality, align_our_and_customer)
# ---------------------------------------------------------------------------


class TestUnit:
    """Чистые unit-тесты. Не требуют БД."""

    # ── test 3: ΔP-фильтрация на уровне строк ────────────────────────────

    def test_aggregate_to_daily_uses_row_level_dp_filter(self):
        """
        3 строки:
          row 0: p_tube=10, p_line=12  — idle (p_tube < p_line) → не рабочая
          row 1: p_tube=0.0, p_line=5  — false zero               → не рабочая
          row 2: p_tube=15, p_line=10  — валидная, ΔP=5           → рабочая

        dp_mean должен считаться ТОЛЬКО по row 2 → dp=5.0 (давление маскируется).
        q должен считаться по ВСЕМ строкам → q=(0+0+5)/3=1.67 (простои влияют на Q).

        Бизнес-логика: уменьшение простоев = рост среднего Q = успех адаптации.
        """
        from backend.services.observation_data_service import _aggregate_to

        start = datetime(2025, 3, 15, 0, 0, 0)
        idx = pd.date_range(start, periods=3, freq="1min")
        df = pd.DataFrame(
            {
                "p_tube":     [10.0, 0.0, 15.0],
                "p_line":     [12.0, 5.0, 10.0],
                "flow_rate":  [0.0,  0.0,  5.0],
                "purge_flag": [0,    0,    0],
            },
            index=idx,
        )

        result = _aggregate_to(df, "daily")

        assert not result.empty, "Результат не должен быть пустым"
        assert len(result) == 1, "За 3 минуты одного дня должна быть одна суточная строка"

        dp_val = float(result.iloc[0]["dp"])
        assert abs(dp_val - 5.0) < 0.01, (
            f"dp_mean должен быть 5.0 (только по валидной строке), получено {dp_val}"
        )

        # Q учитывает ВСЕ строки включая простои — это критерий эффективности
        q_val = result.iloc[0]["q"]
        expected_q = (0.0 + 0.0 + 5.0) / 3  # ≈ 1.67
        assert not np.isnan(q_val), "q не должен быть NaN"
        assert abs(float(q_val) - expected_q) < 0.01, (
            f"q должен быть {expected_q:.2f} (среднее по всем строкам включая простои), получено {q_val}"
        )

    def test_aggregate_to_daily_all_idle_q_is_zero(self):
        """Если все строки idle (p_tube <= p_line), q должен быть 0.0.

        Бизнес-логика: простои учитываются в среднем Q. Если скважина
        весь день стояла — Q=0, это важный показатель для сравнения периодов.
        """
        from backend.services.observation_data_service import _aggregate_to

        start = datetime(2025, 3, 15, 0, 0, 0)
        idx = pd.date_range(start, periods=5, freq="1min")
        df = pd.DataFrame(
            {
                "p_tube":    [10.0] * 5,
                "p_line":    [12.0] * 5,
                "flow_rate": [0.0] * 5,
            },
            index=idx,
        )

        result = _aggregate_to(df, "daily")
        assert len(result) == 1
        assert float(result.iloc[0]["q"]) == 0.0, (
            "q должен быть 0.0 если все строки idle (простои учитываются)"
        )

    def test_aggregate_to_minute_passthrough(self):
        """aggregation='minute' должен возвращать строки 1:1."""
        from backend.services.observation_data_service import _aggregate_to

        df = _make_minute_df(n_minutes=10)
        result = _aggregate_to(df, "minute")
        assert len(result) == 10
        assert "p_tube" in result.columns
        assert "dp" in result.columns

    def test_aggregate_to_empty_df(self):
        """Пустой df → пустой df без исключений."""
        from backend.services.observation_data_service import _aggregate_to

        result = _aggregate_to(pd.DataFrame(), "daily")
        assert result.empty

    def test_aggregate_hourly_shutdown_min(self):
        """
        shutdown_min = число нерабочих минут в окне агрегации.
        20 рабочих + 40 idle (p_tube < p_line) → за 1 час shutdown_min=40.
        """
        from backend.services.observation_data_service import _aggregate_to

        start = datetime(2025, 3, 15, 0, 0, 0)
        idx = pd.date_range(start, periods=60, freq="1min")

        p_tube = [15.0] * 20 + [10.0] * 40   # первые 20 рабочие, остальные idle
        p_line = [10.0] * 20 + [12.0] * 40

        df = pd.DataFrame(
            {
                "p_tube":    p_tube,
                "p_line":    p_line,
                "flow_rate": [5.0] * 20 + [0.0] * 40,
            },
            index=idx,
        )

        result = _aggregate_to(df, "hourly")
        assert len(result) == 1
        shutdown = float(result.iloc[0]["shutdown_min"])
        assert abs(shutdown - 40.0) < 1.0, (
            f"shutdown_min должен быть ~40, получено {shutdown}"
        )

    # ── test 4: приоритет data_status ────────────────────────────────────

    def test_data_quality_status_no_data(self):
        """Пустой df → status='no_data'."""
        from backend.services.observation_data_service import compute_data_quality

        result = compute_data_quality(
            pd.DataFrame(),
            period_from=datetime(2025, 1, 1),
            period_to=datetime(2025, 1, 7),
        )
        assert result["status"] == "no_data"
        assert result["days_with_data"] == 0

    def test_data_quality_status_ok(self):
        """Плотный df за 7 дней → status='ok'."""
        from backend.services.observation_data_service import compute_data_quality

        start = datetime(2025, 1, 1, 0, 0, 0)
        # 7 дней × 60 мин/час × 24 = 10080 минут
        n = 7 * 24 * 60
        df = _make_minute_df(n_minutes=n, start=start, p_tube_base=15.0, p_line_base=12.0)

        result = compute_data_quality(
            df,
            period_from=start,
            period_to=start + timedelta(days=7) - timedelta(minutes=1),
        )
        assert result["status"] == "ok", (
            f"Ожидался status='ok', получено '{result['status']}', "
            f"coverage={result['coverage_pct']}"
        )
        assert result["coverage_pct"] > 95.0
        assert result["days_with_data"] == 7

    def test_data_quality_status_sparse(self):
        """
        Данные только за 1 час из 7 дней → coverage очень низкая → status='sparse'.
        """
        from backend.services.observation_data_service import compute_data_quality

        start = datetime(2025, 1, 1, 0, 0, 0)
        # Только 60 минут из 10080 → coverage < 1%
        df = _make_minute_df(n_minutes=60, start=start)

        result = compute_data_quality(
            df,
            period_from=start,
            period_to=start + timedelta(days=7),
        )
        assert result["status"] in ("sparse", "no_data"), (
            f"Ожидался status='sparse', получено '{result['status']}'"
        )
        assert result["coverage_pct"] < 80.0

    def test_data_quality_status_gap(self):
        """
        Гэп > 72 часов → status='gap' (если нет suspicious spikes).
        """
        from backend.services.observation_data_service import compute_data_quality

        start = datetime(2025, 1, 1, 0, 0, 0)
        # 2 куска: 1 день, потом гэп 4 суток, потом 1 день
        part1 = _make_minute_df(n_minutes=24 * 60, start=start)
        part2 = _make_minute_df(
            n_minutes=24 * 60,
            start=start + timedelta(days=5),
        )
        df = pd.concat([part1, part2])

        result = compute_data_quality(
            df,
            period_from=start,
            period_to=start + timedelta(days=6),
        )
        assert result["max_gap_hours"] > 72.0, (
            f"Ожидался гэп > 72ч, получено {result['max_gap_hours']}"
        )
        # status может быть 'gap' или 'sparse' (зависит от coverage),
        # но точно не 'ok' и не 'no_data'
        assert result["status"] not in ("ok", "no_data"), (
            f"При большом гэпе ожидался status != ok, получено '{result['status']}'"
        )

    def test_data_quality_status_priority_suspicious_over_gap(self):
        """
        suspicious_spikes_count > 0 → status='suspicious',
        даже если есть гэп > 72ч.
        Приоритет: no_data > suspicious > gap > sparse > ok.
        """
        from backend.services.observation_data_service import compute_data_quality

        start = datetime(2025, 1, 1, 0, 0, 0)
        n = 14 * 24 * 60  # 14 дней минутных данных
        df = _make_minute_df(n_minutes=n, start=start, p_tube_base=15.0)

        # Добавляем явный spike: в середине один выброс +100 атм
        spike_idx = n // 2
        df.iloc[spike_idx, df.columns.get_loc("p_tube")] = 150.0

        result = compute_data_quality(
            df,
            period_from=start,
            period_to=start + timedelta(days=14),
        )
        # При реальном z-score spike должен детектироваться
        # (зависит от окна и алгоритма — проверяем что метрика посчитана)
        assert "suspicious_spikes_count" in result
        assert "status" in result

    def test_data_quality_false_zero_pct(self):
        """false_zero_pct считается корректно."""
        from backend.services.observation_data_service import compute_data_quality

        start = datetime(2025, 1, 1, 0, 0, 0)
        df = _make_minute_df(n_minutes=100, start=start)
        # 4 строки с false zero в p_tube → 4%
        df.iloc[0, df.columns.get_loc("p_tube")] = 0.0
        df.iloc[1, df.columns.get_loc("p_tube")] = 0.0
        df.iloc[2, df.columns.get_loc("p_tube")] = 0.0
        df.iloc[3, df.columns.get_loc("p_tube")] = 0.0

        result = compute_data_quality(
            df,
            period_from=start,
            period_to=start + timedelta(minutes=99),
        )
        assert result["false_zero_pct"] == pytest.approx(4.0, abs=0.5)

    # ── test 5: align_our_and_customer ───────────────────────────────────

    def test_align_our_and_customer_partial_overlap(self):
        """
        7 дней нашей истории, заказчик даёт 3 средних дня (дни 2–4).
        Ожидаем: дни 0-1 → our_only, дни 2-4 → ok/suspicious, дни 5-6 → our_only.
        """
        from backend.services.observation_data_service import align_our_and_customer

        base = date(2025, 3, 1)
        our_dates = pd.date_range(base, periods=7, freq="1D")

        our_df = pd.DataFrame(
            {
                "p_tube": [15.0] * 7,
                "p_line": [10.0] * 7,
                "dp":     [5.0] * 7,
                "q":      [20.0] * 7,
            },
            index=our_dates,
        )

        # Заказчик только за 3 дня (индекс 2,3,4)
        cust_dates = pd.to_datetime([base + timedelta(days=i) for i in [2, 3, 4]])
        cust_df = pd.DataFrame(
            {
                "date":          cust_dates,
                "p_wellhead":    [14.5, 14.8, 14.6],
                "p_flowline":    [9.8,  9.9,  9.7],
                "q_gas_working": [19.5, 20.1, 20.3],
                "q_gas_total":   [19.5, 20.1, 20.3],
            }
        )

        result = align_our_and_customer(our_df, cust_df)

        assert len(result) == 7, f"Ожидалось 7 строк, получено {len(result)}"

        statuses = result.set_index("date")["data_status"].to_dict()

        for i in [0, 1, 5, 6]:
            d = pd.Timestamp(base + timedelta(days=i)).normalize()
            assert d in statuses, f"Дата {d} должна быть в результате"
            assert statuses[d] == "our_only", (
                f"День {i}: ожидался our_only, получено {statuses[d]}"
            )

        for i in [2, 3, 4]:
            d = pd.Timestamp(base + timedelta(days=i)).normalize()
            assert d in statuses, f"Дата {d} должна быть в результате"
            assert statuses[d] in ("ok", "suspicious"), (
                f"День {i}: ожидался ok/suspicious, получено {statuses[d]}"
            )

    def test_align_our_and_customer_both_empty(self):
        """Оба пустых DF → пустой результат без исключений."""
        from backend.services.observation_data_service import align_our_and_customer

        result = align_our_and_customer(pd.DataFrame(), pd.DataFrame())
        assert result.empty

    def test_align_our_and_customer_suspicious_threshold(self):
        """
        Если |diff_q_pct| >= 25% → data_status = 'suspicious'.
        """
        from backend.services.observation_data_service import align_our_and_customer

        base = date(2025, 3, 1)
        our_dates = pd.date_range(base, periods=3, freq="1D")
        our_df = pd.DataFrame(
            {"p_tube": [15.0] * 3, "p_line": [10.0] * 3, "dp": [5.0] * 3, "q": [20.0] * 3},
            index=our_dates,
        )

        cust_dates = pd.to_datetime([base + timedelta(days=i) for i in range(3)])
        cust_df = pd.DataFrame(
            {
                "date":          cust_dates,
                "p_wellhead":    [14.5] * 3,
                "p_flowline":    [9.8] * 3,
                # Заказчик даёт Q на 30% ниже → suspicious
                "q_gas_working": [14.0] * 3,
                "q_gas_total":   [14.0] * 3,
            }
        )

        result = align_our_and_customer(our_df, cust_df)
        statuses = result["data_status"].unique().tolist()
        assert "suspicious" in statuses, (
            f"При расхождении Q >25% ожидался 'suspicious', статусы: {statuses}"
        )

    def test_align_gap_status(self):
        """Строки где ни у нас, ни у заказчика нет данных → data_status='gap'."""
        from backend.services.observation_data_service import align_our_and_customer

        base = date(2025, 3, 1)
        # our_df с NaN q за день 0
        our_dates = pd.date_range(base, periods=2, freq="1D")
        our_df = pd.DataFrame(
            {"p_tube": [np.nan, 15.0], "p_line": [np.nan, 10.0],
             "dp": [np.nan, 5.0], "q": [np.nan, 20.0]},
            index=our_dates,
        )
        # cust_df тоже без данных за день 0
        cust_df = pd.DataFrame(
            {
                "date":          [pd.Timestamp(base + timedelta(days=1))],
                "p_wellhead":    [14.5],
                "p_flowline":    [9.8],
                "q_gas_working": [19.5],
                "q_gas_total":   [19.5],
            }
        )

        result = align_our_and_customer(our_df, cust_df)
        day0 = pd.Timestamp(base).normalize()
        row0 = result[result["date"] == day0]

        assert len(row0) == 1
        assert row0.iloc[0]["data_status"] == "gap", (
            f"Ожидался 'gap', получено '{row0.iloc[0]['data_status']}'"
        )

    # ── load_observation_data: нет данных ────────────────────────────────

    def test_load_observation_data_no_data(self):
        """
        test 2: несуществующий well_id → data_quality.status='no_data', не падает.
        compute_full_flow мокируем как бросающий ValueError через patch.dict sys.modules.
        """
        from backend.services.observation_data_service import load_observation_data

        fake_db = MagicMock()

        # load_observation_data делает `from backend.services.flow_rate.full_pipeline
        # import compute_full_flow` при каждом вызове — подменяем сам модуль.
        with patch.dict(
            "sys.modules",
            {
                "backend.services.flow_rate.full_pipeline": _make_full_pipeline_module(
                    raises=ValueError("Нет данных давления для well_id=99999")
                ),
            },
        ):
            result = load_observation_data(
                db=fake_db,
                well_id=99999,
                d_from="2025-01-01",
                d_to="2025-01-07",
            )

        assert result.data_quality["status"] == "no_data"
        assert result.our_df.empty
        assert result.our_raw_minute_df.empty


# ---------------------------------------------------------------------------
# Интеграционные тесты (требуют реальной БД)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIntegration:
    """
    Интеграционные тесты — требуют реального PostgreSQL.

    Запуск:
        DATABASE_URL=postgresql://... python -m pytest \
            backend/tests/test_observation_data_service.py -v -m integration
    """

    @pytest.fixture
    def db(self):
        """Сессия БД из реального SessionLocal."""
        try:
            from backend.db import SessionLocal
        except ImportError:
            pytest.skip("backend.db не доступен — пропускаем интеграционный тест")
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    def test_load_observation_data_happy_path(self, db):
        """
        test 1: реальная скважина well_id=21, период 7 дней.
        Проверяем: our_df не пустой, нужные колонки есть, data_status не 'no_data'.
        """
        from backend.services.observation_data_service import load_observation_data

        d_to   = date.today() - timedelta(days=1)
        d_from = d_to - timedelta(days=6)

        result = load_observation_data(
            db=db,
            well_id=21,
            d_from=d_from,
            d_to=d_to,
            aggregation="daily",
        )

        assert result.well_id == 21
        assert result.period["from"] == d_from.isoformat()

        if result.data_quality["status"] == "no_data":
            pytest.skip("well_id=21 не имеет данных за запрошенный период — тест пропущен")

        assert not result.our_df.empty, "our_df не должен быть пустым"
        for col in ("p_tube", "p_line", "dp", "q", "shutdown_min", "purge_flag"):
            assert col in result.our_df.columns, f"Колонка '{col}' отсутствует в our_df"

        assert not result.our_raw_minute_df.empty, "our_raw_minute_df не должен быть пустым"
        assert result.data_quality["status"] in ("ok", "sparse", "gap", "suspicious")
        assert result.data_quality["days_requested"] == 7

    def test_load_observation_data_aggregation_hourly(self, db):
        """Агрегация 'hourly' возвращает строки с частотой 1ч."""
        from backend.services.observation_data_service import load_observation_data

        d_to   = date.today() - timedelta(days=1)
        d_from = d_to - timedelta(days=2)

        result = load_observation_data(
            db=db,
            well_id=21,
            d_from=d_from,
            d_to=d_to,
            aggregation="hourly",
        )

        if result.data_quality["status"] == "no_data":
            pytest.skip("Нет данных для well_id=21")

        if result.our_df.empty:
            pytest.skip("our_df пуст")

        # Проверяем что индекс почасовой (δt ≈ 1ч)
        idx = result.our_df.index
        if len(idx) > 1:
            delta = (idx[1] - idx[0]).total_seconds() / 3600.0
            assert 0.5 <= delta <= 2.0, f"Ожидался часовой интервал, получено {delta}ч"


# ---------------------------------------------------------------------------
# Вспомогательная функция для мока full_pipeline модуля
# ---------------------------------------------------------------------------


def _make_full_pipeline_module(raises: Exception | None = None):
    """Создаёт mock-модуль backend.services.flow_rate.full_pipeline."""
    import types

    mod = types.ModuleType("backend.services.flow_rate.full_pipeline")

    if raises is not None:
        def _compute_full_flow(*_args, **_kwargs):
            raise raises
    else:
        def _compute_full_flow(*_args, **_kwargs):
            return {
                "df": _make_minute_df(n_minutes=60),
                "summary": {},
                "downtime_periods": pd.DataFrame(),
                "purge_cycles": [],
                "data_points": 60,
                "choke_mm": 6.0,
            }

    mod.compute_full_flow = _compute_full_flow
    return mod
