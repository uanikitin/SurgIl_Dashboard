"""
build_purge_library.py — Создание библиотеки продувок из маркированных событий.

Источники данных:
  1. МАРКЕРЫ СОБЫТИЙ (events) — ручные показания манометра (p_tube, p_line)
     start/press/stop — точные значения давления от оператора
  2. LORA ДАННЫЕ (pressure_raw) — непрерывный сигнал датчика (может не видеть продувку)

3 фазы продувки:
  Фаза 1 (СТРАВЛИВАНИЕ): start → press
    Перекрыли шлейф, открыли продувку. Давление стравливается в атмосферу.
  Фаза 2 (НАБОР ДАВЛЕНИЯ): press → stop
    Закрыли продувку. Скважина набирает давление (часто выше медианного).
  Фаза 3 (ПУСК В ЛИНИЮ): stop →
    Открыли шлейф, пустили газ в линию. Давление снижается до рабочего.

Параметры:
  - Давления: до (start), минимум (press), овершут (stop), после стабилизации
  - Длительности: стравливание, набор давления, простой (start→stop)
  - Выброс: интеграл ΔP по времени (стравливание)
  - Скорости: стравливания, набора давления
  - Видимость LoRa: видит ли датчик продувку
  - P_line: поведение линейного давления
"""
from __future__ import annotations

import os
import sys
import json
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import text, create_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backend.settings import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

engine = create_engine(settings.DATABASE_URL)


# ═══════════════════════════════════════════════════
# 1. Загрузить все маркеры продувок
# ═══════════════════════════════════════════════════

def load_all_purge_markers() -> pd.DataFrame:
    """Загружает все purge events, привязывает well_id."""
    sql = text("""
        SELECT e.id, e.well, e.event_time, e.purge_phase,
               e.p_tube, e.p_line, e.description,
               w.id AS well_id, w.number AS well_number
        FROM events e
        JOIN wells w ON e.well = w.number::text
        WHERE e.event_type = 'purge'
          AND e.purge_phase IN ('start', 'press', 'stop')
        ORDER BY w.id, e.event_time
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()

    df = pd.DataFrame(rows, columns=[
        "event_id", "well", "event_time", "purge_phase",
        "p_tube", "p_line", "description",
        "well_id", "well_number",
    ])
    df["event_time"] = pd.to_datetime(df["event_time"])
    log.info("Загружено %d маркеров продувок для %d скважин",
             len(df), df["well_id"].nunique())
    return df


# ═══════════════════════════════════════════════════
# 2. Группировка маркеров в сессии
# ═══════════════════════════════════════════════════

def group_purge_sessions(markers: pd.DataFrame) -> list[dict]:
    """
    Группирует маркеры start/press/stop в сессии.
    - start открывает сессию
    - press фиксирует минимум (может отсутствовать)
    - stop закрывает сессию
    - Разрыв > 4 часов = новая сессия
    """
    MAX_GAP = timedelta(hours=4)
    sessions = []

    for well_id, group in markers.groupby("well_id"):
        group = group.sort_values("event_time").reset_index(drop=True)
        well_number = group.iloc[0]["well_number"]
        current = None

        for _, row in group.iterrows():
            phase = row["purge_phase"]
            t = row["event_time"]

            if phase == "start":
                if current is not None:
                    current["has_stop"] = False
                    sessions.append(current)
                current = {
                    "well_id": int(well_id),
                    "well_number": well_number,
                    "start_time": t,
                    "press_time": None,
                    "stop_time": None,
                    # Ручные показания манометра
                    "gauge_start_p_tube": float(row["p_tube"]) if pd.notna(row["p_tube"]) else None,
                    "gauge_start_p_line": float(row["p_line"]) if pd.notna(row["p_line"]) else None,
                    "gauge_press_p_tube": None,
                    "gauge_press_p_line": None,
                    "gauge_stop_p_tube": None,
                    "gauge_stop_p_line": None,
                    "description": row["description"],
                    "has_start": True,
                    "has_press": False,
                    "has_stop": False,
                }

            elif phase == "press":
                if current is not None and (t - current["start_time"]) < MAX_GAP:
                    current["press_time"] = t
                    current["gauge_press_p_tube"] = float(row["p_tube"]) if pd.notna(row["p_tube"]) else None
                    current["gauge_press_p_line"] = float(row["p_line"]) if pd.notna(row["p_line"]) else None
                    current["has_press"] = True

            elif phase == "stop":
                if current is not None and (t - current["start_time"]) < MAX_GAP:
                    current["stop_time"] = t
                    current["gauge_stop_p_tube"] = float(row["p_tube"]) if pd.notna(row["p_tube"]) else None
                    current["gauge_stop_p_line"] = float(row["p_line"]) if pd.notna(row["p_line"]) else None
                    current["has_stop"] = True
                    sessions.append(current)
                    current = None

        if current is not None:
            sessions.append(current)

    n_full = sum(1 for s in sessions if s["has_start"] and s["has_press"] and s["has_stop"])
    n_partial = sum(1 for s in sessions if s["has_start"] and s["has_stop"] and not s["has_press"])
    n_incomplete = sum(1 for s in sessions if not s["has_stop"])
    log.info("Сгруппировано %d сессий: %d полных (start+press+stop), %d без press, %d незакрытых",
             len(sessions), n_full, n_partial, n_incomplete)
    return sessions


# ═══════════════════════════════════════════════════
# 3. Загрузить LoRa давление (СЫРОЕ — без маскировки продувки)
# ═══════════════════════════════════════════════════

def load_lora_pressure(well_id: int, dt_start: datetime, dt_end: datetime) -> pd.DataFrame:
    """
    Загружает сырое давление из LoRa.
    НЕ применяет clean_pressure() — чтобы не маскировать реальные нули продувки.
    Вместо этого: убираем comm_loss (оба канала = 0 одновременно),
    но сохраняем реальные нули (p_tube=0 при p_line>0 = продувка).
    """
    margin = timedelta(minutes=60)
    t0 = dt_start - margin
    t1 = dt_end + margin

    sql = text("""
        SELECT measured_at, p_tube, p_line
        FROM pressure_raw
        WHERE well_id = :wid
          AND measured_at >= :t0
          AND measured_at <= :t1
        ORDER BY measured_at
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"wid": well_id, "t0": t0, "t1": t1}).fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["measured_at", "p_tube", "p_line"])
    df["measured_at"] = pd.to_datetime(df["measured_at"])
    df = df.set_index("measured_at").sort_index()

    # Убираем comm_loss: оба канала = 0 или None одновременно
    both_zero = ((df["p_tube"] == 0) | df["p_tube"].isna()) & \
                ((df["p_line"] == 0) | df["p_line"].isna())
    df.loc[both_zero, ["p_tube", "p_line"]] = np.nan

    # p_tube = 0 при p_line > 0 — реальная продувка, СОХРАНЯЕМ

    # Убираем одиночные нули p_line (comm_loss p_line при нормальном p_tube)
    for col in ("p_tube", "p_line"):
        s = df[col]
        zero_mask = s == 0
        other_col = "p_line" if col == "p_tube" else "p_tube"
        # Ноль при нормальном давлении другого канала < 5 = comm_loss
        # Ноль при p_tube > 5 = comm_loss p_line
        # Ноль при p_tube < 5 = может быть продувка — сохраняем
        if col == "p_line":
            comm_loss = zero_mask & (df["p_tube"] > 5)
            df.loc[comm_loss, col] = np.nan

    # Отрицательные и > 85 атм — артефакты
    for col in ("p_tube", "p_line"):
        invalid = (df[col] < 0) | (df[col] > 85)
        df.loc[invalid, col] = np.nan

    return df


# ═══════════════════════════════════════════════════
# 4. Анализ продувки: маркеры + LoRa
# ═══════════════════════════════════════════════════

def analyze_purge(session: dict, df_lora: pd.DataFrame) -> dict:
    """
    Анализирует продувку.
    Основные параметры: из РУЧНЫХ ПОКАЗАНИЙ маркеров (gauge_*).
    Дополнительные: из LoRa данных (если датчик видит продувку).
    """
    result = {
        "well_id": session["well_id"],
        "well_number": session["well_number"],
        "description": session.get("description"),
        "has_start": session["has_start"],
        "has_press": session["has_press"],
        "has_stop": session["has_stop"],
        "start_time": session["start_time"].isoformat(),
        "press_time": session["press_time"].isoformat() if session["press_time"] else None,
        "stop_time": session["stop_time"].isoformat() if session["stop_time"] else None,
    }

    start_time = session["start_time"]
    press_time = session["press_time"]
    stop_time = session["stop_time"]

    # ═══ ПАРАМЕТРЫ ИЗ МАРКЕРОВ (ручные показания) ═══

    g_start_pt = session["gauge_start_p_tube"]
    g_start_pl = session["gauge_start_p_line"]
    g_press_pt = session["gauge_press_p_tube"]
    g_press_pl = session["gauge_press_p_line"]
    g_stop_pt = session["gauge_stop_p_tube"]
    g_stop_pl = session["gauge_stop_p_line"]

    result["gauge"] = {
        "start_p_tube": g_start_pt,
        "start_p_line": g_start_pl,
        "press_p_tube": g_press_pt,
        "press_p_line": g_press_pl,
        "stop_p_tube": g_stop_pt,
        "stop_p_line": g_stop_pl,
    }

    # ─── Фаза 1: Стравливание (start → press) ───
    if g_start_pt is not None and g_press_pt is not None and press_time is not None:
        venting_duration = (press_time - start_time).total_seconds() / 60
        pressure_drop = g_start_pt - g_press_pt
        venting_rate = pressure_drop / venting_duration if venting_duration > 0 else 0
    elif g_start_pt is not None and stop_time is not None:
        # Нет press — используем start→stop как общую длительность
        venting_duration = None
        pressure_drop = None
        venting_rate = None
    else:
        venting_duration = None
        pressure_drop = None
        venting_rate = None

    result["phase1_venting"] = {
        "duration_min": round(venting_duration, 1) if venting_duration is not None else None,
        "pressure_drop_atm": round(pressure_drop, 2) if pressure_drop is not None else None,
        "rate_atm_per_min": round(venting_rate, 3) if venting_rate is not None else None,
        "p_start": g_start_pt,
        "p_bottom": g_press_pt,
    }

    # ─── Фаза 2: Набор давления (press → stop) ───
    if g_press_pt is not None and g_stop_pt is not None and press_time and stop_time:
        buildup_duration = (stop_time - press_time).total_seconds() / 60
        pressure_rise = g_stop_pt - g_press_pt
        buildup_rate = pressure_rise / buildup_duration if buildup_duration > 0 else 0
        overshoot = g_stop_pt - g_start_pt if g_start_pt else None
    else:
        buildup_duration = None
        pressure_rise = None
        buildup_rate = None
        overshoot = None

    result["phase2_buildup"] = {
        "duration_min": round(buildup_duration, 1) if buildup_duration is not None else None,
        "pressure_rise_atm": round(pressure_rise, 2) if pressure_rise is not None else None,
        "rate_atm_per_min": round(buildup_rate, 3) if buildup_rate is not None else None,
        "p_overshoot": g_stop_pt,
        "overshoot_above_baseline_atm": round(overshoot, 2) if overshoot is not None else None,
    }

    # ─── Общий простой (start → stop) ───
    if stop_time and start_time:
        total_downtime = (stop_time - start_time).total_seconds() / 60
    else:
        total_downtime = None

    result["total_downtime_min"] = round(total_downtime, 1) if total_downtime is not None else None

    # ─── P_line поведение ───
    p_line_values = [v for v in [g_start_pl, g_press_pl, g_stop_pl] if v is not None]
    if len(p_line_values) >= 2:
        p_line_mean = np.mean(p_line_values)
        p_line_std = np.std(p_line_values)
        result["p_line_gauge"] = {
            "mean": round(float(p_line_mean), 2),
            "std": round(float(p_line_std), 2),
            "stable": p_line_std < 1.0,  # p_line стабильна если std < 1 атм
            "values": [round(v, 2) for v in p_line_values],
        }
    else:
        result["p_line_gauge"] = None

    # ═══ ПАРАМЕТРЫ ИЗ LORA ═══

    if df_lora.empty or "p_tube" not in df_lora.columns:
        result["lora"] = {"available": False}
        return result

    # Baseline LoRa: медиана за 30 мин ДО продувки
    pre_start = start_time - timedelta(minutes=30)
    pre_data = df_lora.loc[pre_start:start_time, "p_tube"].dropna()

    if pre_data.empty:
        result["lora"] = {"available": False}
        return result

    lora_baseline = float(pre_data.median())

    # Данные LoRa во время продувки
    end_t = stop_time if stop_time else start_time + timedelta(hours=3)
    purge_lora = df_lora.loc[start_time:end_t, "p_tube"].dropna()

    if purge_lora.empty:
        result["lora"] = {
            "available": True,
            "baseline": round(lora_baseline, 2),
            "visible": False,
            "reason": "no_data_during_purge",
        }
        return result

    lora_min = float(purge_lora.min())
    lora_drop = lora_baseline - lora_min

    # Видимость: LoRa видит продувку если перепад > 3 атм
    visible = lora_drop > 3.0

    lora_info = {
        "available": True,
        "baseline": round(lora_baseline, 2),
        "min_during_purge": round(lora_min, 2),
        "drop_atm": round(lora_drop, 2),
        "visible": visible,
        "data_points": len(purge_lora),
    }

    # Если LoRa видит — считаем дополнительные параметры
    if visible and stop_time:
        # Выброс: интеграл (baseline - p_tube) за время стравливания
        venting_lora = df_lora.loc[start_time:end_t, "p_tube"]
        delta_p = lora_baseline - venting_lora
        delta_p = delta_p.clip(lower=0)
        dt_min = venting_lora.index.to_series().diff().dt.total_seconds().fillna(0) / 60
        emission_integral = float((delta_p * dt_min).sum())
        lora_info["emission_integral_atm_min"] = round(emission_integral, 1)

        # Стабилизация после stop (LoRa)
        post_stop = df_lora.loc[stop_time:stop_time + timedelta(minutes=120), "p_tube"].dropna()
        if not post_stop.empty:
            tolerance = 0.15 * lora_baseline
            stable_mask = (post_stop - lora_baseline).abs() <= tolerance
            if stable_mask.any():
                stable_idx = stable_mask.idxmax()
                stab_time = (stable_idx - stop_time).total_seconds() / 60
                lora_info["stabilization_after_stop_min"] = round(stab_time, 1)
                lora_info["p_stabilized"] = round(float(post_stop.loc[stable_idx]), 2)

        # P_line LoRa во время продувки
        if "p_line" in df_lora.columns:
            lora_pline = df_lora.loc[start_time:end_t, "p_line"].dropna()
            if not lora_pline.empty:
                lora_info["p_line_mean"] = round(float(lora_pline.mean()), 2)
                lora_info["p_line_std"] = round(float(lora_pline.std()), 2)

    result["lora"] = lora_info
    return result


# ═══════════════════════════════════════════════════
# 5. Главная функция
# ═══════════════════════════════════════════════════

def build_purge_library() -> list[dict]:
    """Полный цикл: маркеры → сессии → давление → параметры."""
    markers = load_all_purge_markers()
    if markers.empty:
        log.warning("Нет маркированных продувок")
        return []

    sessions = group_purge_sessions(markers)

    library = []
    for i, session in enumerate(sessions):
        well_id = session["well_id"]
        start = session["start_time"]
        stop = session["stop_time"] or start + timedelta(hours=3)

        df_lora = load_lora_pressure(well_id, start, stop)
        result = analyze_purge(session, df_lora)
        result["purge_id"] = i + 1
        library.append(result)

        if (i + 1) % 20 == 0:
            log.info("  обработано %d/%d сессий...", i + 1, len(sessions))

    log.info("Библиотека: %d продувок", len(library))
    return library


# ═══════════════════════════════════════════════════
# 6. Статистический анализ
# ═══════════════════════════════════════════════════

def analyze_library(library: list[dict]) -> dict:
    """Агрегированный анализ паттернов продувок."""
    if not library:
        return {}

    # Полные сессии: есть start + press + stop
    full = [p for p in library if p["has_start"] and p["has_press"] and p["has_stop"]]
    has_stop = [p for p in library if p["has_start"] and p["has_stop"]]

    analysis = {
        "total_purges": len(library),
        "full_cycles": len(full),
        "with_stop": len(has_stop),
        "incomplete": len(library) - len(has_stop),
        "wells_count": len(set(p["well_id"] for p in library)),
        "wells": sorted(set(p["well_number"] for p in library)),
    }

    if not full:
        return analysis

    # ─── Статистика по параметрам (из маркеров) ───

    def _stats(values: list[float], label: str) -> dict:
        s = pd.Series([v for v in values if v is not None])
        if s.empty:
            return None
        return {
            "label": label,
            "count": int(len(s)),
            "mean": round(float(s.mean()), 2),
            "median": round(float(s.median()), 2),
            "std": round(float(s.std()), 2),
            "min": round(float(s.min()), 2),
            "max": round(float(s.max()), 2),
            "p25": round(float(s.quantile(0.25)), 2),
            "p75": round(float(s.quantile(0.75)), 2),
        }

    params = {}

    # Стравливание
    params["venting_duration_min"] = _stats(
        [p["phase1_venting"]["duration_min"] for p in full],
        "Стравливание (мин)"
    )
    params["pressure_drop_atm"] = _stats(
        [p["phase1_venting"]["pressure_drop_atm"] for p in full],
        "Перепад давления (атм)"
    )
    params["venting_rate"] = _stats(
        [p["phase1_venting"]["rate_atm_per_min"] for p in full],
        "Скорость стравливания (атм/мин)"
    )
    params["p_bottom"] = _stats(
        [p["phase1_venting"]["p_bottom"] for p in full],
        "Минимальное давление (атм)"
    )

    # Набор давления
    params["buildup_duration_min"] = _stats(
        [p["phase2_buildup"]["duration_min"] for p in full],
        "Набор давления (мин)"
    )
    params["pressure_rise_atm"] = _stats(
        [p["phase2_buildup"]["pressure_rise_atm"] for p in full],
        "Подъём давления (атм)"
    )
    params["buildup_rate"] = _stats(
        [p["phase2_buildup"]["rate_atm_per_min"] for p in full],
        "Скорость набора (атм/мин)"
    )
    params["overshoot_atm"] = _stats(
        [p["phase2_buildup"]["overshoot_above_baseline_atm"] for p in full],
        "Овершут выше baseline (атм)"
    )

    # Общий простой
    params["total_downtime_min"] = _stats(
        [p["total_downtime_min"] for p in full],
        "Общий простой (мин)"
    )

    # Baseline давление
    params["baseline_p_tube"] = _stats(
        [p["phase1_venting"]["p_start"] for p in full],
        "Baseline P_tube (атм)"
    )

    # LoRa видимость
    lora_visible = sum(1 for p in full if p.get("lora", {}).get("visible", False))
    analysis["lora_visibility"] = {
        "visible": lora_visible,
        "invisible": len(full) - lora_visible,
        "visibility_pct": round(100 * lora_visible / len(full), 1) if full else 0,
    }

    analysis["parameters"] = {k: v for k, v in params.items() if v is not None}

    # ─── По скважинам ───
    per_well = []
    well_groups = {}
    for p in full:
        wid = p["well_id"]
        if wid not in well_groups:
            well_groups[wid] = []
        well_groups[wid].append(p)

    for wid, purges in well_groups.items():
        venting = [p["phase1_venting"]["duration_min"] for p in purges if p["phase1_venting"]["duration_min"]]
        drops = [p["phase1_venting"]["pressure_drop_atm"] for p in purges if p["phase1_venting"]["pressure_drop_atm"]]
        buildup = [p["phase2_buildup"]["duration_min"] for p in purges if p["phase2_buildup"]["duration_min"]]
        overshoots = [p["phase2_buildup"]["overshoot_above_baseline_atm"] for p in purges
                      if p["phase2_buildup"]["overshoot_above_baseline_atm"] is not None]
        downtime = [p["total_downtime_min"] for p in purges if p["total_downtime_min"]]
        baselines = [p["phase1_venting"]["p_start"] for p in purges if p["phase1_venting"]["p_start"]]
        bottoms = [p["phase1_venting"]["p_bottom"] for p in purges if p["phase1_venting"]["p_bottom"] is not None]
        lora_vis = sum(1 for p in purges if p.get("lora", {}).get("visible", False))

        per_well.append({
            "well_id": int(wid),
            "well_number": purges[0]["well_number"],
            "count": len(purges),
            "avg_baseline_atm": round(np.mean(baselines), 2) if baselines else None,
            "avg_p_bottom_atm": round(np.mean(bottoms), 2) if bottoms else None,
            "avg_venting_min": round(np.mean(venting), 1) if venting else None,
            "avg_pressure_drop_atm": round(np.mean(drops), 2) if drops else None,
            "avg_buildup_min": round(np.mean(buildup), 1) if buildup else None,
            "avg_overshoot_atm": round(np.mean(overshoots), 2) if overshoots else None,
            "avg_downtime_min": round(np.mean(downtime), 1) if downtime else None,
            "lora_visible_pct": round(100 * lora_vis / len(purges), 0),
        })
    analysis["per_well"] = sorted(per_well, key=lambda x: x["count"], reverse=True)

    return analysis


def print_summary(analysis: dict):
    """Печатает сводку."""
    print("\n" + "=" * 70)
    print("  БИБЛИОТЕКА ПРОДУВОК (из ручных показаний манометра)")
    print("=" * 70)
    print(f"  Всего сессий:          {analysis.get('total_purges', 0)}")
    print(f"  Полных (start+press+stop): {analysis.get('full_cycles', 0)}")
    print(f"  С start+stop (без press):  {analysis.get('with_stop', 0) - analysis.get('full_cycles', 0)}")
    print(f"  Незакрытых:            {analysis.get('incomplete', 0)}")
    print(f"  Скважин:               {analysis.get('wells_count', 0)}")
    print(f"  Скважины:              {analysis.get('wells', [])}")

    lv = analysis.get("lora_visibility", {})
    if lv:
        print(f"\n  LoRa видимость:        {lv.get('visible', 0)} видит / "
              f"{lv.get('invisible', 0)} не видит ({lv.get('visibility_pct', 0)}%)")

    print("\n" + "-" * 70)
    print("  ПАРАМЕТРЫ ПРОДУВОК (полные циклы, ручные показания)")
    print("-" * 70)

    for key in [
        "venting_duration_min",
        "pressure_drop_atm",
        "venting_rate",
        "p_bottom",
        "buildup_duration_min",
        "pressure_rise_atm",
        "buildup_rate",
        "overshoot_atm",
        "total_downtime_min",
        "baseline_p_tube",
    ]:
        s = analysis.get("parameters", {}).get(key)
        if not s:
            continue
        print(f"\n  {s['label']}  (n={s['count']})")
        print(f"    Среднее: {s['mean']},  Медиана: {s['median']},  Std: {s['std']}")
        print(f"    Min: {s['min']},  Max: {s['max']}")
        print(f"    P25: {s['p25']},  P75: {s['p75']}")

    print("\n" + "-" * 70)
    print("  ПО СКВАЖИНАМ")
    print("-" * 70)

    for w in analysis.get("per_well", []):
        print(f"\n  Скв. {w['well_number']} (well_id={w['well_id']}): {w['count']} продувок")
        print(f"    Baseline: {w['avg_baseline_atm']} атм → P_min: {w['avg_p_bottom_atm']} атм")
        print(f"    Стравливание: {w['avg_venting_min']} мин, перепад: {w['avg_pressure_drop_atm']} атм")
        print(f"    Набор давления: {w['avg_buildup_min']} мин, овершут: {w['avg_overshoot_atm']} атм")
        print(f"    Простой: {w['avg_downtime_min']} мин")
        print(f"    LoRa видимость: {w['lora_visible_pct']}%")


# ═══════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    library = build_purge_library()

    if library:
        output_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        os.makedirs(output_dir, exist_ok=True)

        # Сохранить библиотеку
        lib_path = os.path.join(output_dir, "purge_library.json")
        with open(lib_path, "w", encoding="utf-8") as f:
            json.dump(library, f, ensure_ascii=False, indent=2, default=str)
        log.info("Библиотека сохранена: %s", lib_path)

        # Анализ
        analysis = analyze_library(library)
        analysis_path = os.path.join(output_dir, "purge_analysis.json")
        with open(analysis_path, "w", encoding="utf-8") as f:
            json.dump(analysis, f, ensure_ascii=False, indent=2, default=str)
        log.info("Анализ сохранён: %s", analysis_path)

        print_summary(analysis)
    else:
        log.warning("Библиотека пуста!")
