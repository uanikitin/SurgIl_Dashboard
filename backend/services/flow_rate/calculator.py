"""
Расчёт дебита газа и потерь при продувках.

Векторизованный (numpy) — ~100x быстрее чем df.apply().
Единицы входных данных: p_tube, p_line в кгс/см².
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import (
    FlowRateConfig, PurgeLossConfig,
    DEFAULT_FLOW, DEFAULT_PURGE,
)


def calculate_flow_rate(
    df: pd.DataFrame,
    choke_mm: float,
    cfg: FlowRateConfig = DEFAULT_FLOW,
) -> pd.DataFrame:
    """
    Мгновенный дебит для каждой строки.

    Ожидает колонки: p_tube, p_line (кгс/см²).
    Добавляет: flow_rate (тыс. м³/сут).
    """
    wh = df["p_tube"].values.astype(float)
    lp = df["p_line"].values.astype(float)

    # Защита от деления на 0
    safe_wh = np.where(wh > 0, wh, 1.0)
    r = np.where(wh > lp, (wh - lp) / safe_wh, 0.0)

    choke_sq = (choke_mm / cfg.C2) ** 2

    # Докритический (r < 0.5)
    q_sub = (
        cfg.C1 * choke_sq * wh
        * (1.0 - r / 1.5)
        * np.sqrt(np.maximum(r / cfg.C3, 0.0))
    )

    # Критический (r >= 0.5)
    q_crit = (
        0.667 * cfg.C1 * choke_sq * wh
        * np.sqrt(0.5 / cfg.C3)
    )

    q_base = np.where(r < cfg.critical_ratio, q_sub, q_crit)
    q = np.where(wh > lp, q_base * cfg.multiplier, 0.0)

    df = df.copy()
    df["flow_rate"] = np.maximum(q, 0.0)
    return df


def calculate_cumulative(df: pd.DataFrame) -> pd.DataFrame:
    """
    Накопленный дебит методом трапеций.
    Добавляет: cumulative_flow (тыс. м³).
    """
    dt = 1.0 / 1440.0  # 1 минута = 1/1440 суток
    q = df["flow_rate"].values
    q_prev = np.roll(q, 1)
    q_prev[0] = 0.0

    df = df.copy()
    df["cumulative_flow"] = np.cumsum((q_prev + q) * dt / 2.0)
    return df


def calculate_purge_loss(
    df: pd.DataFrame,
    cfg: PurgeLossConfig = DEFAULT_PURGE,
) -> pd.DataFrame:
    """
    Потери газа при продувках (стравливание в атмосферу).

    Логика: если p_tube < p_line → скважина в режиме продувки,
    газ стравливается через штуцер.

    Добавляет: purge_flag, purge_loss_per_min, cumulative_purge_loss.
    """
    wh = df["p_tube"].values.astype(float)
    lp = df["p_line"].values.astype(float)

    # Пересчёт кгс/см² → МПа
    wh_mpa = wh * cfg.kgscm2_to_MPa

    # Флаг продувки
    is_purge = wh < lp

    # Перепад относительно атмосферы (Па)
    delta_p = np.maximum((wh_mpa - cfg.atm_pressure_MPa) * 1e6, 0.0)

    # Площадь сечения штуцера продувки (м²)
    area = np.pi * (cfg.choke_diameter_m / 2.0) ** 2

    # Плотность газа при устьевых условиях (кг/м³)
    rho = np.maximum(
        (wh_mpa * 1e6) * cfg.molar_mass
        / (cfg.gas_constant * cfg.standard_temp_K),
        1e-6,
    )

    # Объёмный расход (м³/с) → тыс. м³/мин
    q = cfg.discharge_coeff * area * np.sqrt(2.0 * delta_p / rho)
    loss_per_min = q * 60.0 / 1000.0

    # Обнуляем если нет продувки или ΔP ≤ 0
    loss_per_min = np.where(is_purge & (delta_p > 0), loss_per_min, 0.0)

    df = df.copy()
    df["purge_flag"] = is_purge.astype(int)
    df["purge_loss_per_min"] = loss_per_min
    df["cumulative_purge_loss"] = np.cumsum(loss_per_min * is_purge)
    return df
