"""
Интеллектуальная детекция продувок (venting-циклов) скважин.

Продувка = цикл стравливания газа в атмосферу:
  1. venting  (start→press): задвижка открыта, газ стравливается → считаем потери
  2. buildup  (press→stop):  задвижка закрыта, набор давления   → Q=0, потерь нет
  3. restart  (stop→):       пуск в линию, возобновление добычи

Простой (downtime) — отдельное понятие: все участки p_tube ≤ p_line.
Простои включают фазу buildup, но потерь при простоях нет.

Два источника детекции:
  - marker:    маркеры из таблицы events (start/press/stop)
  - algorithm: анализ кривой давления (V-образный паттерн)
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from .config import PurgeDetectionConfig, DEFAULT_PURGE_DETECTION

log = logging.getLogger(__name__)


def _make_cycle_id(source: str, venting_start: datetime | None) -> str:
    """Детерминированный ID: одинаковый для одного и того же цикла между вызовами."""
    key = f"{source}:{venting_start.isoformat() if venting_start else 'none'}"
    return hashlib.md5(key.encode()).hexdigest()[:8]


# ═══════════════════════════════════════════════════════════
#  Структура данных: один цикл продувки
# ═══════════════════════════════════════════════════════════

@dataclass
class PurgeCycle:
    """Один полный или частичный цикл продувки."""
    id: str = ""
    source: str = "marker"          # 'marker' или 'algorithm'

    # Фаза стравливания (потери газа)
    venting_start: Optional[datetime] = None
    venting_end: Optional[datetime] = None

    # Фаза набора давления (Q=0, потерь нет)
    buildup_start: Optional[datetime] = None
    buildup_end: Optional[datetime] = None

    # Момент пуска в линию
    restart_time: Optional[datetime] = None

    # Давления (кгс/см²)
    p_start: float = 0.0       # давление в начале стравливания
    p_bottom: float = 0.0      # дно (минимум после стравливания)
    p_end: float = 0.0         # давление в конце набора / при рестарте

    # Метаданные
    confidence: float = 0.0    # уверенность 0..1
    excluded: bool = False     # пользователь исключил из расчёта

    @property
    def venting_duration_min(self) -> float:
        if self.venting_start and self.venting_end:
            return (self.venting_end - self.venting_start).total_seconds() / 60.0
        return 0.0

    @property
    def buildup_duration_min(self) -> float:
        if self.buildup_start and self.buildup_end:
            return (self.buildup_end - self.buildup_start).total_seconds() / 60.0
        return 0.0

    @property
    def total_duration_min(self) -> float:
        t_start = self.venting_start or self.buildup_start
        t_end = self.buildup_end or self.restart_time or self.venting_end
        if t_start and t_end:
            return (t_end - t_start).total_seconds() / 60.0
        return 0.0

    def to_dict(self) -> dict:
        """Сериализация для JSON-ответа API."""
        def fmt(dt):
            return dt.isoformat() if dt else None
        return {
            "id": self.id,
            "source": self.source,
            "venting_start": fmt(self.venting_start),
            "venting_end": fmt(self.venting_end),
            "buildup_start": fmt(self.buildup_start),
            "buildup_end": fmt(self.buildup_end),
            "restart_time": fmt(self.restart_time),
            "p_start": round(self.p_start, 2),
            "p_bottom": round(self.p_bottom, 2),
            "p_end": round(self.p_end, 2),
            "venting_duration_min": round(self.venting_duration_min, 1),
            "buildup_duration_min": round(self.buildup_duration_min, 1),
            "total_duration_min": round(self.total_duration_min, 1),
            "confidence": round(self.confidence, 2),
            "excluded": self.excluded,
        }


# ═══════════════════════════════════════════════════════════
#  PurgeDetector — основной класс
# ═══════════════════════════════════════════════════════════

class PurgeDetector:
    """
    Детектор продувок: маркерный + алгоритмический методы.

    Использование:
        detector = PurgeDetector(cfg)
        cycles = detector.detect(df, events_df, exclude_ids={'abc12345'})
    """

    def __init__(self, cfg: PurgeDetectionConfig = DEFAULT_PURGE_DETECTION):
        self.cfg = cfg

    # ─────────────── Основной метод ───────────────

    def detect(
        self,
        df: pd.DataFrame,
        events_df: pd.DataFrame | None = None,
        exclude_ids: set[str] | None = None,
    ) -> list[PurgeCycle]:
        """
        Полная детекция продувок.

        1. По маркерам (если есть события)
        2. По кривой давления (для участков без маркеров)
        3. Удаление перекрытий, фильтр по confidence
        4. Пометка исключённых

        Parameters
        ----------
        df : DataFrame с индексом datetime, колонки p_tube, p_line
        events_df : DataFrame маркеров из get_purge_events() (или None)
        exclude_ids : множество ID циклов для исключения

        Returns
        -------
        list[PurgeCycle] отсортированный по venting_start
        """
        exclude_ids = exclude_ids or set()
        cycles: list[PurgeCycle] = []

        # 1) Маркерная детекция
        marker_cycles = []
        if events_df is not None and not events_df.empty:
            marker_cycles = self._detect_from_markers(df, events_df)
            cycles.extend(marker_cycles)
            log.info("Marker detection: %d cycles", len(marker_cycles))

        # 2) Алгоритмическая детекция
        algo_cycles = self._detect_from_curve(df)
        log.info("Algorithm detection: %d candidates", len(algo_cycles))

        # 3) Убираем перекрытия с маркерными
        if marker_cycles and algo_cycles:
            algo_cycles = self._remove_overlaps(marker_cycles, algo_cycles)
            log.info("After overlap removal: %d algorithm cycles", len(algo_cycles))

        cycles.extend(algo_cycles)

        # 4) Фильтр по confidence
        cycles = [c for c in cycles if c.confidence >= self.cfg.min_confidence]

        # 5) Сортировка по времени
        cycles.sort(key=lambda c: c.venting_start or c.buildup_start or datetime.min)

        # 6) Назначаем детерминированные ID
        for c in cycles:
            c.id = _make_cycle_id(c.source, c.venting_start)

        # 7) Пометка исключённых
        for c in cycles:
            if c.id in exclude_ids:
                c.excluded = True

        log.info(
            "Total purge cycles: %d (marker=%d, algo=%d, excluded=%d)",
            len(cycles),
            sum(1 for c in cycles if c.source == 'marker'),
            sum(1 for c in cycles if c.source == 'algorithm'),
            sum(1 for c in cycles if c.excluded),
        )
        return cycles

    # ─────────────── Маркерная детекция ───────────────

    def _detect_from_markers(
        self,
        df: pd.DataFrame,
        events_df: pd.DataFrame,
    ) -> list[PurgeCycle]:
        """
        Детекция по маркерам start→press→stop из таблицы events.

        Жадное сопоставление: берём события по порядку,
        пытаемся собрать полный цикл start→press→stop.
        """
        cycles: list[PurgeCycle] = []

        # Нормализуем purge_phase к нижнему регистру
        events = events_df.copy()
        events["purge_phase"] = events["purge_phase"].str.lower().str.strip()
        events = events.sort_values("event_time").reset_index(drop=True)

        i = 0
        while i < len(events):
            row = events.iloc[i]
            phase = row["purge_phase"]

            if phase != "start":
                i += 1
                continue

            # Нашли start — ищем press и stop
            start_time = pd.Timestamp(row["event_time"])
            start_p = float(row.get("p_tube", 0) or 0)

            press_time = None
            press_p = None
            stop_time = None
            stop_p = None

            j = i + 1
            while j < len(events):
                next_row = events.iloc[j]
                next_phase = next_row["purge_phase"]
                next_time = pd.Timestamp(next_row["event_time"])

                # Если встретили новый start — прервать текущий цикл
                if next_phase == "start":
                    break

                if next_phase == "press" and press_time is None:
                    press_time = next_time
                    press_p = float(next_row.get("p_tube", 0) or 0)

                elif next_phase == "stop" and stop_time is None:
                    stop_time = next_time
                    stop_p = float(next_row.get("p_tube", 0) or 0)
                    j += 1
                    break

                j += 1

            # Собираем цикл
            cycle = PurgeCycle(source="marker")

            # Привязка к кривой давления
            cycle.venting_start = self._snap_to_curve(df, start_time)
            cycle.p_start = self._get_pressure_at(df, cycle.venting_start) or start_p

            if press_time is not None:
                cycle.venting_end = self._snap_to_curve(df, press_time)
                cycle.buildup_start = cycle.venting_end
                cycle.p_bottom = self._get_pressure_at(df, cycle.venting_end) or press_p

                if stop_time is not None:
                    # Полный цикл: start → press → stop
                    cycle.buildup_end = self._snap_to_curve(df, stop_time)
                    cycle.restart_time = cycle.buildup_end
                    cycle.p_end = self._get_pressure_at(df, cycle.buildup_end) or stop_p
                    cycle.confidence = 0.95
                else:
                    # Есть start и press, нет stop
                    # Попробуем найти конец набора по кривой
                    estimated_end = self._estimate_buildup_end(df, cycle.buildup_start)
                    if estimated_end:
                        cycle.buildup_end = estimated_end
                        cycle.restart_time = estimated_end
                        cycle.p_end = self._get_pressure_at(df, estimated_end) or 0
                    cycle.confidence = 0.6
            else:
                # Только start, нет press
                # Ищем дно по кривой
                bottom = self._find_bottom_after(df, cycle.venting_start)
                if bottom:
                    cycle.venting_end = bottom
                    cycle.buildup_start = bottom
                    cycle.p_bottom = self._get_pressure_at(df, bottom) or 0
                    # Ищем конец набора
                    estimated_end = self._estimate_buildup_end(df, bottom)
                    if estimated_end:
                        cycle.buildup_end = estimated_end
                        cycle.restart_time = estimated_end
                        cycle.p_end = self._get_pressure_at(df, estimated_end) or 0
                cycle.confidence = 0.5

            # Валидация: venting_start должен быть
            if cycle.venting_start is not None:
                # Проверка длительности стравливания
                if cycle.venting_end and cycle.venting_duration_min > self.cfg.max_venting_minutes:
                    cycle.confidence *= 0.5  # снижаем уверенность
                cycles.append(cycle)

            i = j

        return cycles

    # ─────────────── Алгоритмическая детекция ───────────────

    def _detect_from_curve(self, df: pd.DataFrame) -> list[PurgeCycle]:
        """
        Детекция продувок по форме кривой давления (V-образный паттерн).

        Алгоритм:
        1. Сглаживание + dp/dt
        2. Поиск сегментов непрерывного падения
        3. Для каждого — поиск дна и восстановления
        """
        if len(df) < 30:
            return []

        cycles: list[PurgeCycle] = []
        p = df["p_tube"].values.astype(float)
        times = df.index

        # Сглаживание (скользящее среднее, 5 точек)
        kernel = 5
        if len(p) >= kernel:
            p_smooth = np.convolve(p, np.ones(kernel) / kernel, mode="same")
        else:
            p_smooth = p.copy()

        # dp/dt (кгс/см² / мин) — каждая строка ~1 мин
        dp = np.diff(p_smooth, prepend=p_smooth[0])

        # Поиск сегментов падения: dp < -min_decline_rate
        threshold = -self.cfg.min_decline_rate
        min_points = self.cfg.min_decline_minutes  # 1 точка ≈ 1 мин

        in_decline = False
        decline_start = 0

        for i in range(1, len(dp)):
            if dp[i] < threshold:
                if not in_decline:
                    in_decline = True
                    decline_start = i
            else:
                if in_decline:
                    decline_len = i - decline_start
                    if decline_len >= min_points:
                        # Нашли сегмент падения длиной >= min_decline_minutes
                        cycle = self._analyze_v_pattern(
                            df, p_smooth, times,
                            decline_start, i,
                        )
                        if cycle:
                            cycles.append(cycle)
                    in_decline = False

        # Последний сегмент
        if in_decline:
            decline_len = len(dp) - decline_start
            if decline_len >= min_points:
                cycle = self._analyze_v_pattern(
                    df, p_smooth, times,
                    decline_start, len(dp),
                )
                if cycle:
                    cycles.append(cycle)

        return cycles

    def _analyze_v_pattern(
        self,
        df: pd.DataFrame,
        p_smooth: np.ndarray,
        times: pd.DatetimeIndex,
        decline_start: int,
        decline_end: int,
    ) -> PurgeCycle | None:
        """
        Анализ V-образного паттерна: падение → дно → восстановление.

        Returns PurgeCycle или None если паттерн не подтвердился.
        """
        p_raw = df["p_tube"].values.astype(float)

        # Начальное давление (перед падением)
        p_at_start = float(p_smooth[max(0, decline_start - 1)])

        # Ищем дно в районе конца падения (±5 точек)
        search_start = max(0, decline_end - 5)
        search_end = min(len(p_smooth), decline_end + 10)
        bottom_idx = search_start + int(np.argmin(p_smooth[search_start:search_end]))
        p_at_bottom = float(p_smooth[bottom_idx])

        # Глубина падения
        drop = p_at_start - p_at_bottom
        if drop < 1.0:
            # Слишком маленькое падение — вероятно не продувка
            return None

        # Ищем восстановление: p_tube >= recovery_threshold * p_at_start
        target_p = p_at_start * self.cfg.recovery_threshold
        max_buildup_points = self.cfg.max_buildup_hours * 60  # минуты

        recovery_idx = None
        for i in range(bottom_idx + 1, min(len(p_smooth), bottom_idx + max_buildup_points)):
            if p_smooth[i] >= target_p:
                recovery_idx = i
                break

        # Ищем рестарт (после восстановления давление снова начинает падать = добыча)
        restart_idx = recovery_idx
        if recovery_idx is not None:
            # Ищем момент, когда давление стабилизируется или начинает падать
            for i in range(recovery_idx, min(len(p_smooth), recovery_idx + 30)):
                if i + 3 < len(p_smooth):
                    # Среднее изменение за 3 точки — если отрицательное, это рестарт
                    avg_dp = (p_smooth[i + 3] - p_smooth[i]) / 3.0
                    if avg_dp < -0.02:
                        restart_idx = i
                        break

        # Оценка уверенности
        confidence = 0.0

        # Глубина падения (больше = лучше)
        if drop >= 5.0:
            confidence += 0.3
        elif drop >= 2.0:
            confidence += 0.2
        else:
            confidence += 0.1

        # Полнота восстановления
        if recovery_idx is not None:
            p_at_recovery = float(p_smooth[recovery_idx])
            recovery_ratio = p_at_recovery / p_at_start if p_at_start > 0 else 0
            if recovery_ratio >= 0.9:
                confidence += 0.3
            elif recovery_ratio >= 0.7:
                confidence += 0.2
            else:
                confidence += 0.1
        else:
            confidence += 0.0  # нет восстановления

        # Форма V (скорость падения + скорость восстановления)
        decline_duration = decline_end - decline_start
        if decline_duration > 0:
            decline_rate = drop / decline_duration
            if decline_rate >= 0.3:
                confidence += 0.2
            elif decline_rate >= 0.1:
                confidence += 0.1

        # Рестарт найден
        if restart_idx and restart_idx != recovery_idx:
            confidence += 0.1

        # Собираем цикл
        cycle = PurgeCycle(source="algorithm")
        cycle.venting_start = times[max(0, decline_start - 1)]
        cycle.venting_end = times[bottom_idx]
        cycle.buildup_start = times[bottom_idx]
        cycle.p_start = p_at_start
        cycle.p_bottom = p_at_bottom

        if recovery_idx is not None:
            cycle.buildup_end = times[recovery_idx]
            cycle.p_end = float(p_smooth[recovery_idx])

        if restart_idx is not None:
            cycle.restart_time = times[restart_idx]
            if cycle.p_end == 0:
                cycle.p_end = float(p_smooth[restart_idx])

        cycle.confidence = min(confidence, 1.0)

        # Дополнительная валидация
        if cycle.venting_duration_min > self.cfg.max_venting_minutes:
            cycle.confidence *= 0.5

        return cycle

    # ─────────────── Вспомогательные методы ───────────────

    def _snap_to_curve(
        self,
        df: pd.DataFrame,
        target_time: pd.Timestamp,
    ) -> pd.Timestamp | None:
        """
        Привязка времени маркера к ближайшей точке на кривой давления.
        Допуск: ±marker_time_tolerance_min.
        """
        if df.empty:
            return target_time

        tolerance = pd.Timedelta(minutes=self.cfg.marker_time_tolerance_min)

        # Ищем ближайшую точку
        mask = (df.index >= target_time - tolerance) & (df.index <= target_time + tolerance)
        nearby = df[mask]

        if nearby.empty:
            return target_time

        # Ближайшая по времени
        idx = (nearby.index - target_time).map(lambda x: abs(x.total_seconds()))
        return nearby.index[idx.argmin()]

    def _get_pressure_at(
        self,
        df: pd.DataFrame,
        time: pd.Timestamp | None,
    ) -> float | None:
        """Давление p_tube в ближайшей точке к заданному времени."""
        if time is None or df.empty:
            return None

        tolerance = pd.Timedelta(minutes=5)
        mask = (df.index >= time - tolerance) & (df.index <= time + tolerance)
        nearby = df[mask]

        if nearby.empty:
            return None

        idx = (nearby.index - time).map(lambda x: abs(x.total_seconds()))
        return float(nearby["p_tube"].iloc[idx.argmin()])

    def _find_bottom_after(
        self,
        df: pd.DataFrame,
        start_time: pd.Timestamp,
    ) -> pd.Timestamp | None:
        """
        Найти минимум давления после start_time
        (в окне max_venting_minutes).
        """
        window = pd.Timedelta(minutes=self.cfg.max_venting_minutes)
        mask = (df.index >= start_time) & (df.index <= start_time + window)
        segment = df[mask]

        if segment.empty:
            return None

        return segment["p_tube"].idxmin()

    def _estimate_buildup_end(
        self,
        df: pd.DataFrame,
        buildup_start: pd.Timestamp,
    ) -> pd.Timestamp | None:
        """
        Оценка конца набора давления:
        ищем момент, когда p_tube восстановилось до recovery_threshold
        от давления до начала стравливания.
        """
        if buildup_start is None:
            return None

        window = pd.Timedelta(hours=self.cfg.max_buildup_hours)
        mask = (df.index > buildup_start) & (df.index <= buildup_start + window)
        segment = df[mask]

        if segment.empty:
            return None

        # Берём давление перед продувкой (за 10 мин до начала)
        pre_mask = df.index < buildup_start
        if pre_mask.any():
            # Среднее давление за 10 мин до
            pre_segment = df[pre_mask].tail(10)
            p_before = float(pre_segment["p_tube"].mean())
        else:
            return None

        target = p_before * self.cfg.recovery_threshold

        # Ищем первый момент, когда давление превысило target
        above = segment[segment["p_tube"] >= target]
        if above.empty:
            return None

        return above.index[0]

    def _remove_overlaps(
        self,
        marker_cycles: list[PurgeCycle],
        algo_cycles: list[PurgeCycle],
    ) -> list[PurgeCycle]:
        """
        Удаление алгоритмических циклов, перекрывающихся с маркерными.
        Маркеры имеют приоритет.
        """
        result = []
        for ac in algo_cycles:
            a_start = ac.venting_start or ac.buildup_start
            a_end = ac.buildup_end or ac.restart_time or ac.venting_end
            if a_start is None or a_end is None:
                continue

            overlaps = False
            for mc in marker_cycles:
                m_start = mc.venting_start or mc.buildup_start
                m_end = mc.buildup_end or mc.restart_time or mc.venting_end
                if m_start is None or m_end is None:
                    continue

                # Проверка перекрытия: два интервала перекрываются если
                # start1 < end2 AND start2 < end1
                if a_start < m_end and m_start < a_end:
                    overlaps = True
                    break

            if not overlaps:
                result.append(ac)

        return result


# ═══════════════════════════════════════════════════════════
#  Утилита: пересчёт purge_loss с учётом обнаруженных циклов
# ═══════════════════════════════════════════════════════════

def recalculate_purge_loss_with_cycles(
    df: pd.DataFrame,
    cycles: list[PurgeCycle],
    purge_loss_cfg=None,
) -> pd.DataFrame:
    """
    Пересчитываем potери при продувках: ТОЛЬКО в фазах venting.

    Если нет обнаруженных циклов — оставляем текущую логику
    (потери при p_tube < p_line).

    Parameters
    ----------
    df : DataFrame с колонками purge_loss_per_min, cumulative_purge_loss
    cycles : список обнаруженных PurgeCycle (не исключённых)

    Returns
    -------
    DataFrame с пересчитанными purge_loss_per_min и cumulative_purge_loss
    """
    active_cycles = [c for c in cycles if not c.excluded]

    if not active_cycles:
        # Нет обнаруженных продувок — оставляем как есть
        return df

    df = df.copy()

    # Создаём маску: True только в фазах venting
    venting_mask = np.zeros(len(df), dtype=bool)

    for cycle in active_cycles:
        if cycle.venting_start and cycle.venting_end:
            mask = (df.index >= cycle.venting_start) & (df.index <= cycle.venting_end)
            venting_mask |= np.asarray(mask)

    # Обнуляем потери вне фаз venting
    df["purge_loss_per_min"] = np.where(
        venting_mask,
        df["purge_loss_per_min"].values,
        0.0,
    )

    # Пересчитываем накопленные потери
    df["cumulative_purge_loss"] = np.cumsum(df["purge_loss_per_min"].values)

    # Обновляем purge_flag: 1 только в фазах venting
    df["purge_flag"] = venting_mask.astype(int)

    return df
