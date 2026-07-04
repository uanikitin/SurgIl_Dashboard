"""Роза нестабильности — диагностика скважин-кандидатов по обводнению.

Идея (отличается от старой `customer_rose_service` «роза критериев»):
  • Эталон — НЕ история-распределение и НЕ соседи, а математический идеал
    «стабильный режим» (нет тренда, нет колебаний, нет простоев).
  • Каждая ось — БЕЗРАЗМЕРНОЕ расстояние от идеала, 0..100 (0 = идеально спокоен).
  • Период задаётся ЯКОРНОЙ датой + скользящими окнами назад (не «от–до»).
  • Дополнительно: L* — длина текущего стабильного периода (через плоский хвост).
  • Итоговый индекс I = взвешенная сумма осей. Большой I + малый L* = кандидат.

Контракт ответа `compute_stability_rose()` — см. docstring функции; формат
готов под сохранение в `customer_report_block.data_snapshot` (kind='stability_rose').

Шкалы откалиброваны на 133 скважинах well_daily (перцентиль P95), 2026-06-24.
Полная мат-модель и обоснование: plans/handoffs/HANDOFF_customer_chapter_map_2026-06-23.md
(Приложение C).
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from backend.services import customer_daily_service as csvc

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  Оси, технические подписи и описания (для отчёта)
# ═══════════════════════════════════════════════════════════════════

AXES: tuple[str, ...] = ("trend", "rough", "cyc", "dp", "uplift", "freq", "purge")

LABELS_FULL: dict[str, str] = {
    "trend":  "Снижение дебита газа",
    "rough":  "Неравномерность дебита",
    "cyc":    "Колебания давления на устье",
    "dp":     "Снижение перепада давления (ΔP)",
    "uplift": "Прирост дебита после остановки",
    "freq":   "Частота остановок",
    "purge":  "Признаки продувок",
}

LABELS_SHORT: dict[str, str] = {
    "trend":  "Снижение\nдебита",
    "rough":  "Неравном.\nдебита",
    "cyc":    "Колебания\nР устья",
    "dp":     "Снижение\nперепада ΔP",
    "uplift": "Прирост Q\nпосле остан.",
    "freq":   "Частота\nостановок",
    "purge":  "Признаки\nпродувок",
}

# Краткое описание принципа и значения каждого параметра (вставляется в отчёт).
DESCRIPTIONS: dict[str, str] = {
    "trend":  "Устойчивый тренд падения дебита за период (робастный наклон "
              "Тейла–Сена). Может указывать на накопление жидкости в стволе либо "
              "истощение пласта. Чем больше — тем сильнее падение.",
    "rough":  "Разброс дебита вокруг тренда (рывки вместо плавного хода). Высокая "
              "неравномерность характерна для нестабильной работы и самозадавливания "
              "жидкостью.",
    "cyc":    "Изменчивость давления на устье — циклы «накопление–вынос». "
              "Циклические колебания типичны для периодического скопления и выноса "
              "жидкости.",
    "dp":     "Падение перепада давления ΔP = (давление на устье − давление в линии) "
              "при СТАБИЛЬНОМ давлении в линии. Если перепад снижается со стороны устья, "
              "а не из-за роста давления в линии, это МОЖЕТ указывать на процессы в самой "
              "скважине (предположительно накопление жидкости). Вывод осторожный: "
              "снижение ΔP возможно и по другим причинам (изменение режима, штуцер, "
              "состояние пласта).",
    "uplift": "Прирост дебита после остановки/продувки. Если после остановки дебит "
              "заметно вырос, это МОЖЕТ косвенно указывать на вынос жидкости при продувке "
              "(возможный признак обводнения). Трактовать предположительно: прирост "
              "способны давать и другие факторы (перераспределение режима, восстановление "
              "пластового давления).",
    "freq":   "Число эпизодов простоя за 30 суток. Учащение остановок косвенно "
              "указывает на нестабильную работу и продувки.",
    "purge":  "Частые КОРОТКИЕ остановки (порог ~5 ч) в отличие от редких длинных "
              "(= отказ оборудования). Короткие частые остановки — продувочный режим "
              "борьбы с жидкостью.",
}

# ═══════════════════════════════════════════════════════════════════
#  Калибровка (P95 по well_daily, 2026-06-24) — см. Приложение C.6/C.9
# ═══════════════════════════════════════════════════════════════════

SCALES: dict[str, float] = {
    "trend":  0.614,   # g*  — относит. спад дебита за окно (доля)
    "rough":  0.16,    # ρ*  — робастный CV остатков дебита
    "cyc":    0.211,   # a*  — амплитуда колебаний устьевого давления / среднее
    "dp":     1.53,    # b*  — относит. падение перепада ΔP
    "uplift": 0.126,   # u*  — относит. прирост дебита после остановки
    "freq":   12.9,    # λ*  — эпизодов простоя / 30 дн
}
DSTAR: float = 300.0   # граница «продувка↔отказ», мин/эпизод (~5 ч)

# Отдельные шкалы для LoRa (минутные данные → суточные агрегаты + точные эпизоды).
# Калибровка P95 на 31 скважине с датчиками (scratchpad/calibrate_lora.py, 2026-06-24):
# распределения LoRa существенно отличаются от УзКор (особенно отклик/частота).
SCALES_LORA: dict[str, float] = {
    "trend":  0.574,
    "rough":  0.25,
    "cyc":    0.373,
    "dp":     1.5,
    "uplift":  1.06,
    "freq":   155.0,   # частота МИНУТНЫХ эпизодов/30д
}
DSTAR_LORA: float = 66.0   # граница продувка↔отказ для LoRa (мин/эпизод, P95≈66)

WEIGHTS: dict[str, float] = {
    "trend": 0.15, "rough": 0.10, "cyc": 0.15, "dp": 0.20,
    "uplift": 0.20, "freq": 0.10, "purge": 0.10,
}

EPS_FLAT: float = 0.05      # порог «нет тренда» для L*
DELTA_FLAT: float = 0.03    # порог «нет колебаний» для L*
KAPPA_LINE: float = 0.05    # CV линии < κ → линия стабильна (гейт оси dp)
SHUTDOWN_MIN: float = 30.0  # день/строка считается простоем при shutdown_min > X

WINDOW_TREND_DAYS: int = 30   # окно трендовых осей (на коротких 3–7 дн тренда нет)
WINDOW_DOWNTIME_DAYS: int = 90
MIN_STABLE_DAYS: int = 7      # L* ниже → режим не считается стабильным
# Достоверность трендовых осей по числу рабочих точек в окне (см. консультацию:
# Var(наклон)=σ²/Σ(xᵢ−x̄)², n=7 в ~80× менее устойчив чем n=30).
N_MIN_HARD: int = 8           # < → ось insufficient (обнуляется)
N_MIN_WORK: int = 14          # < → reduced (считаем, но флаг достоверности)
# Уровень-вердикт определяется НЕ только индексом I (сильный одиночный сигнал
# или отсутствие стабильного периода тоже выводят в кандидаты), иначе явные
# признаки (частые остановки/продувки с малым весом) тонут в взвешенной сумме.
I_CANDIDATE: float = 25.0   # индекс ≥ → кандидат
I_WATCH: float = 12.0       # индекс ≥ → наблюдать
PETAL_ALARM: float = 66.0   # любая ось ≥ → «тревога» (одиночный сильный сигнал)
PETAL_WATCH: float = 50.0   # любая ось ≥ → как минимум «наблюдать»


# ═══════════════════════════════════════════════════════════════════
#  Математика
# ═══════════════════════════════════════════════════════════════════

def _petal(x: float | None, scale: float) -> float:
    """Безразмерное значение → лепесток 0..100 (насыщение clipped-linear)."""
    if x is None or not math.isfinite(x) or scale <= 0:
        return 0.0
    return float(100.0 * min(1.0, max(0.0, float(x)) / scale))


def _theil_sen(x: np.ndarray, y: np.ndarray) -> float:
    """Робастный наклон (медиана попарных наклонов). 0 если точек мало."""
    n = len(x)
    slopes: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            dx = x[j] - x[i]
            if dx != 0:
                slopes.append((y[j] - y[i]) / dx)
    return float(np.median(slopes)) if slopes else 0.0


def _stable_run_len(d: np.ndarray, q: np.ndarray, sd: np.ndarray) -> int:
    """L* — длина текущего «плоского хвоста» (дней): тренд<ε, колебания<δ,
    доля простоев < 0.3. Идём от конца назад, пока режим остаётся плоским."""
    idx = np.where(np.isfinite(q))[0]
    if idx.size < 5:
        return 0
    end = idx[-1]
    best = 0
    for start in idx[::-1]:
        sel = idx[(idx >= start) & (idx <= end)]
        if sel.size < 5:
            continue
        qq = q[sel]
        xx = (d[sel] - d[sel[0]]).astype(float)
        m = float(np.median(qq))
        if m <= 0:
            break
        b = _theil_sen(xx, qq)
        T = abs(b) * (xx[-1] - xx[0] + 1) / m
        res = qq - (np.median(qq) - b * np.median(xx) + b * xx)
        F = 1.4826 * np.median(np.abs(res - np.median(res))) / m
        if T < EPS_FLAT and F < DELTA_FLAT and np.mean(sd[sel] > SHUTDOWN_MIN) < 0.3:
            best = int((d[sel[-1]] - d[sel[0]]).astype("timedelta64[D]").astype(int)) + 1
        else:
            break
    return best


def _compute_factors(
    df: pd.DataFrame, w_trend: int = WINDOW_TREND_DAYS,
    w_downtime: int = WINDOW_DOWNTIME_DAYS,
    scales: dict | None = None, dstar: float | None = None,
) -> tuple[dict, dict, int, dict, int]:
    """Считает сырые безразмерные значения и лепестки 7 осей + L* для df,
    упорядоченного по дате. Окно трендовых осей = w_trend.
    scales/dstar — пороги нормировки; None → SCALES/DSTAR (УзКор). LoRa передаёт
    SCALES_LORA/DSTAR_LORA. Возвращает (petals, raw, L*, confidence, n_work)."""
    sc = scales if scales is not None else SCALES
    ds = dstar if dstar is not None else DSTAR
    df = df.sort_values("date").copy()
    d = pd.to_datetime(df["date"]).values.astype("datetime64[D]")
    q = pd.to_numeric(df.get("q_gas_total"), errors="coerce").to_numpy(float)
    pw = pd.to_numeric(df.get("p_wellhead"), errors="coerce").to_numpy(float)
    pf = pd.to_numeric(df.get("p_flowline"), errors="coerce").to_numpy(float)
    sd = np.nan_to_num(pd.to_numeric(df.get("shutdown_min"), errors="coerce").to_numpy(float))
    n = len(q)

    pet = {k: 0.0 for k in AXES}
    raw: dict[str, float | None] = {k: None for k in AXES}

    # ── трендовые оси: окно w_trend дн, только рабочие дни ──
    last = slice(max(0, n - w_trend), n)
    ql, sdl = q[last], sd[last]
    work = sdl <= SHUTDOWN_MIN
    mask = work & np.isfinite(ql)
    qe = ql[mask]
    n_work = int(qe.size)
    # достоверность трендовой группы по числу рабочих точек (см. консультацию)
    conf_trend = ("normal" if n_work >= N_MIN_WORK
                  else "reduced" if n_work >= N_MIN_HARD else "insufficient")
    if qe.size >= N_MIN_HARD and np.median(qe) > 0:
        m = float(np.median(qe))
        x = np.arange(len(ql))[mask].astype(float)
        b = _theil_sen(x, qe)
        T = max(0.0, -b) * len(ql) / m
        res = qe - (np.median(qe) - b * np.median(x) + b * x)
        F = 1.4826 * np.median(np.abs(res - np.median(res))) / m
        raw["trend"], raw["rough"] = T, F
        pet["trend"], pet["rough"] = _petal(T, sc["trend"]), _petal(F, sc["rough"])

    # ── давления (окно 30 дн) ──
    pwl, pfl = pw[last], pf[last]
    dp = pwl - pfl
    dpf = dp[np.isfinite(dp)]
    pff = pfl[np.isfinite(pfl)]
    pwf = pwl[np.isfinite(pwl)]
    cvline = float(pff.std() / abs(pff.mean())) if pff.size >= 3 and pff.mean() != 0 else 1.0
    if pwf.size >= 4 and np.median(pwf) > 0:
        xx = np.arange(len(pwf)).astype(float)
        bb = _theil_sen(xx, pwf)
        det = pwf - (np.median(pwf) - bb * np.median(xx) + bb * xx)
        amp = float(np.std(det) / np.median(pwf))
        raw["cyc"] = amp
        pet["cyc"] = _petal(amp, sc["cyc"])
    # ось dp — ТОЛЬКО при стабильном давлении в линии (гейт R1/R2)
    if dpf.size >= 3 and np.median(np.abs(dpf)) > 0 and cvline < KAPPA_LINE:
        rel = (dpf[0] - dpf[-1]) / abs(np.median(dpf))
        raw["dp"] = rel
        pet["dp"] = _petal(rel, sc["dp"])

    # ── простои (окно w_downtime дн) ──
    last90 = slice(max(0, n - w_downtime), n)
    sd9 = sd[last90]
    tau9 = min(w_downtime, n)
    isd = sd9 > SHUTDOWN_MIN
    episodes = int(np.sum(isd & ~np.concatenate([[False], isd[:-1]])))
    lam = episodes * 30.0 / max(1, tau9)
    raw["freq"] = lam
    pet["freq"] = _petal(lam, sc["freq"])
    if episodes > 0:
        dbar = float(sd9[isd].sum()) / episodes
        # продувочный паттерн: частые И короткие
        pet["purge"] = _petal(lam / sc["freq"], 1.0) * max(0.0, 1.0 - min(1.0, dbar / ds))
        raw["purge"] = dbar

    # ── прирост дебита после остановки (по всей доступной истории) ──
    isdh = sd > SHUTDOWN_MIN
    ups: list[float] = []
    i = 0
    while i < len(isdh):
        if isdh[i]:
            s0 = i
            while i < len(isdh) and isdh[i]:
                i += 1
            if s0 - 1 >= 0 and i < len(q) and np.isfinite(q[s0 - 1]) and np.isfinite(q[i]) and q[s0 - 1] > 0:
                ups.append((q[i] - q[s0 - 1]) / q[s0 - 1])
        else:
            i += 1
    posu = [u for u in ups if u > 0]
    up = float(np.mean(posu)) if posu else 0.0
    raw["uplift"] = up
    pet["uplift"] = _petal(up, sc["uplift"])

    # Группа трендовых осей (trend/rough/cyc/dp) считается на окне w_trend →
    # её достоверность общая. freq/purge/uplift устойчивы (эпизоды, не наклон).
    if conf_trend == "insufficient":
        for k in ("trend", "rough", "cyc", "dp"):
            pet[k] = 0.0
    confidence = {
        "trend": conf_trend, "rough": conf_trend, "cyc": conf_trend, "dp": conf_trend,
        "uplift": "normal", "freq": "normal", "purge": "normal",
    }
    L = _stable_run_len(d, q, sd)
    return pet, raw, L, confidence, n_work


# ═══════════════════════════════════════════════════════════════════
#  Entry-point
# ═══════════════════════════════════════════════════════════════════

def compute_stability_rose(
    db: Session,
    well_number: str,
    *,
    anchor: date | None = None,
    source: str = "well_daily",
    window_days: int | None = None,
    downtime_window_days: int | None = None,
) -> dict[str, Any]:
    """Роза нестабильности для (скважина, якорная дата).

    anchor — момент оценки; окна берутся НАЗАД от него. None → последняя дата.
    source — 'well_daily' (суточные УзКорГаз). 'lora' (минутные) — этап 2.
    window_days — ручное окно трендовых осей (≥ N_MIN_HARD). None → авто-выбор
        из W_TREND_FALLBACKS (30→21→14) по доступным данным (защита от нехватки).

    Возвращает snapshot с полями petals/raw/contributions/L_star/index_I/level/
    verdict/labels/descriptions/scales, а также confidence{ось→...}, n_work,
    window_used и window_auto (был ли выбор автоматическим).
    Если расчёт невозможен — {ok: False, error: "..."}.
    """
    if source not in ("well_daily", "lora"):
        return {"ok": False, "error": f"Неизвестный источник: {source}"}

    if source == "well_daily":
        # ── УзКорГаз: суточные сводки well_daily, фильтр по текущему штуцеру ──
        df = csvc.load_for_well(db, str(well_number))
        if df.empty:
            return {"ok": False, "error": f"Нет данных по скв. №{well_number} в well_daily"}
        df = df.copy()
        df["_d"] = pd.to_datetime(df["date"]).dt.date
        if anchor is not None:
            df = df[df["_d"] <= anchor]
        if len(df) < 3:
            return {"ok": False, "error": "Слишком мало данных до якорной даты (< 3 дн.)"}
        anchor_eff = df["_d"].max()
        choke = _select_current_choke(df, anchor_eff)
        dfc = _filter_by_choke(df, choke) if choke is not None else df
        _lora_ctx = None
    else:
        # ── LoRa: минутные давления → дебит (flow_rate) → суточная агрегация.
        # Переиспользуем готовый pipeline our_daily_data (как overlay «наши данные»).
        well = csvc.find_well(db, str(well_number))
        if not well:
            return {"ok": False, "error": f"Скв. №{well_number} не найдена в реестре (нужна привязка для LoRa)"}
        d_to_eff = anchor if anchor is not None else date.today()
        d_from_eff = d_to_eff - timedelta(days=365)
        data = csvc.our_daily_data(db, int(well["id"]), d_from=d_from_eff, d_to=d_to_eff)
        dfc = _lora_daily_df(data)
        if dfc is None or dfc.empty:
            return {"ok": False, "error": "Нет данных LoRa за период (датчик не установлен / нет замеров)"}
        dfc = dfc.copy()
        dfc["_d"] = pd.to_datetime(dfc["date"]).dt.date
        anchor_eff = dfc["_d"].max()
        choke = data.get("choke_mm")
        _lora_ctx = int(well["id"])

    if len(dfc) < 3:
        return {"ok": False, "error": "Слишком мало данных (< 3 дн.)"}

    # Окно трендовых осей. ВАЖНО: окно скользящее НАЗАД — меньшее окно = МЕНЬШЕ
    # точек, поэтому «ужимать при нехватке данных» вредно. Защита от нехватки —
    # это не отказ и не шринк, а расчёт на доступных точках + флаг достоверности
    # (normal/reduced/insufficient) из _compute_factors. Срез сам ограничивается
    # доступной историей. Ручной window_days позволяет СФОКУСИРОВАТЬ на недавнем.
    window_auto = window_days is None and downtime_window_days is None
    w_trend = WINDOW_TREND_DAYS if window_days is None else max(N_MIN_HARD, int(window_days))
    w_downtime = (WINDOW_DOWNTIME_DAYS if downtime_window_days is None
                  else max(N_MIN_HARD, int(downtime_window_days)))

    _sc = SCALES_LORA if source == "lora" else SCALES
    _ds = DSTAR_LORA if source == "lora" else DSTAR
    pet, raw, L, confidence, n_work = _compute_factors(dfc, w_trend, w_downtime, _sc, _ds)

    # LoRa: частота и длительность остановок — ТОЧНО из минутных эпизодов
    # (преимущество датчиков; по УзКор такого не было). Переопределяем freq/purge
    # поверх суточной оценки. УзКор-путь (_lora_ctx=None) НЕ затрагивается.
    lora_episodes = None
    if _lora_ctx is not None:
        lora_episodes = _lora_downtime_episodes(_lora_ctx, anchor_eff, w_downtime)
        if lora_episodes is not None:
            lam = lora_episodes["freq_per_30d"]
            dbar = lora_episodes["mean_dur_min"]
            pet["freq"] = _petal(lam, SCALES_LORA["freq"])
            pet["purge"] = (_petal(lam / SCALES_LORA["freq"], 1.0)
                            * max(0.0, 1.0 - min(1.0, dbar / DSTAR_LORA)))
            raw["freq"], raw["purge"] = lam, dbar
            confidence["freq"] = confidence["purge"] = "normal"

    wsum = sum(WEIGHTS.values())
    contributions = {k: round(float(WEIGHTS[k] * pet[k]), 2) for k in AXES}
    index_I = round(float(sum(contributions.values()) / wsum), 1)

    # Уровень: stable / watch / candidate.
    max_petal = max(pet.values()) if pet else 0.0
    n_warn = sum(1 for v in pet.values() if v >= 33.0)
    if index_I >= I_CANDIDATE or max_petal >= PETAL_ALARM or (L < MIN_STABLE_DAYS and n_warn >= 2):
        level = "candidate"
    elif index_I >= I_WATCH or max_petal >= PETAL_WATCH or L < MIN_STABLE_DAYS:
        level = "watch"
    else:
        level = "stable"
    is_candidate = (level == "candidate")
    if level == "candidate":
        verdict = "КАНДИДАТ на проверку"
    elif level == "watch":
        verdict = f"Наблюдать — отклонения режима (L*={L} дн.)"
    else:
        verdict = f"Стабильна {L} дн." if L > 0 else "Стабильна"

    # ── ВАРИАНТ A: авто-переключение в режим ЭФФЕКТИВНОСТИ ПАВ ──
    # Если в периоде есть вбросы ПАВ → скважина НА ЛЕЧЕНИИ. Колебания/прирост Q —
    # реакция на ПАВ, а не нестабильность. Вердикт = эффективность (ИРВ-Score:
    # отклик на вброс), а не кандидатство. Кандидатские оси остаются справочно.
    analysis_mode = "instability"
    effectiveness = _treatment_effectiveness(db, well_number, anchor_eff)
    if effectiveness and effectiveness.get("injections_total", 0) > 0:
        analysis_mode = "effectiveness"
        es = float(effectiveness.get("score") or 0.0)
        if es >= 70:
            level, verdict = "stable", f"ПАВ эффективен · Score {es:.0f}/100"
        elif es >= 45:
            level, verdict = "watch", f"ПАВ умеренно · Score {es:.0f}/100"
        else:
            level, verdict = "candidate", f"ПАВ слабо · Score {es:.0f}/100 — пересмотреть реагент"
        is_candidate = (level == "candidate")

    return {
        "ok": True,
        "_v": "1",
        "kind": "stability_rose",
        "well_number": str(well_number),
        "anchor": anchor_eff.isoformat(),
        "source": source,
        "choke_mm": choke,
        "n_days": int(len(dfc)),
        "windows": {"trend_days": int(w_trend), "downtime_days": int(w_downtime)},
        "window_auto": bool(window_auto),
        "n_work": int(n_work),
        "lora_episodes": lora_episodes,   # точные остановки (только LoRa); None для УзКор
        "confidence": confidence,
        "data_quality": ("normal" if confidence["trend"] == "normal"
                         else "reduced" if confidence["trend"] == "reduced"
                         else "insufficient"),
        "petals": {k: round(float(pet[k]), 1) for k in AXES},
        "raw": {k: (round(float(v), 6) if v is not None else None) for k, v in raw.items()},
        "weights": dict(WEIGHTS),
        "contributions": contributions,
        "L_star": int(L),
        "index_I": index_I,
        "level": level,
        "is_candidate": bool(is_candidate),
        "verdict": verdict,
        "analysis_mode": analysis_mode,      # instability (кандидат) | effectiveness (на ПАВ)
        "effectiveness": effectiveness if analysis_mode == "effectiveness" else None,
        "labels": dict(LABELS_FULL),
        "labels_short": dict(LABELS_SHORT),
        "descriptions": dict(DESCRIPTIONS),
        "scales": {**SCALES, "DSTAR": DSTAR},
    }


# ── вспомогательное: текущий штуцер и фильтр (логика как в customer_rose) ──

def _select_current_choke(df_all: pd.DataFrame, period_to: date) -> float | None:
    if df_all.empty or "choke_mm" not in df_all.columns:
        return None
    d = df_all.copy()
    d["_do"] = pd.to_datetime(d["date"]).dt.date
    sub = d[d["_do"] <= period_to].sort_values("_do")
    if sub.empty:
        return None
    choke = pd.to_numeric(sub["choke_mm"], errors="coerce").dropna()
    if choke.empty:
        return None
    val = float(choke.iloc[-1])
    if val <= 0 or not math.isfinite(val):
        return None
    return val


def _lora_daily_df(data: dict) -> pd.DataFrame | None:
    """Суточный df из our_daily_data (LoRa) в формате осей well_daily.

    our_daily_data возвращает {pressure:{dates,p_tube,p_line,dp},
    flow:{avg_flow_rate,cumulative_flow,downtime}, choke_mm}. Маппинг:
    p_tube→p_wellhead, p_line→p_flowline, avg_flow_rate→q_gas_total,
    downtime→shutdown_min."""
    pres = (data or {}).get("pressure") or {}
    flow = (data or {}).get("flow") or {}
    dates = pres.get("dates") or []
    if not dates:
        return None
    n = len(dates)

    def col(arr):
        a = list(arr or [])
        return a + [None] * (n - len(a)) if len(a) < n else a[:n]

    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "q_gas_total": col(flow.get("avg_flow_rate")),
        "p_wellhead": col(pres.get("p_tube")),
        "p_flowline": col(pres.get("p_line")),
        "shutdown_min": col(flow.get("downtime")),
        "choke_mm": (data or {}).get("choke_mm"),
    })


def _lora_downtime_episodes(well_id: int, anchor_eff: date, w_downtime: int) -> dict | None:
    """ТОЧНЫЕ эпизоды простоя из минутных LoRa за окно [anchor-w_downtime, anchor].

    Преимущество датчиков: считаем число остановок и длительность КАЖДОЙ поминутно
    (по УзКор это было невозможно). Возвращает {freq_per_30d, mean_dur_min,
    n_episodes} или None при ошибке/отсутствии данных. Используется только для
    источника LoRa; УзКор-путь не затрагивает."""
    try:
        from backend.services.flow_rate.data_access import get_pressure_data
        from backend.services.flow_rate.cleaning import clean_pressure
        from backend.services.flow_rate.calculator import calculate_purge_loss
        from backend.services.flow_rate.downtime import detect_downtime_periods
    except Exception:
        return None
    d_from = anchor_eff - timedelta(days=int(w_downtime))
    try:
        dfm = get_pressure_data(int(well_id), d_from.isoformat(), anchor_eff.isoformat())
        if dfm is None or dfm.empty:
            return None
        dfm = clean_pressure(dfm)
        dfm = calculate_purge_loss(dfm)
        eps = detect_downtime_periods(dfm, dp_threshold=0.1, include_purge=True)
    except Exception:
        log.exception("lora downtime episodes failed well=%s", well_id)
        return None
    n = 0 if eps is None or len(eps) == 0 else int(len(eps))
    span = max(1.0, float(w_downtime))
    return {
        "freq_per_30d": float(n * 30.0 / span),
        "mean_dur_min": float(eps["duration_min"].mean()) if n > 0 else 0.0,
        "n_episodes": n,
    }


def _treatment_effectiveness(db: Session, well_number: str, anchor_eff: date) -> dict | None:
    """Вариант A: если в периоде есть вбросы ПАВ → скважина НА ЛЕЧЕНИИ, оцениваем
    ЭФФЕКТИВНОСТЬ (ИРВ-Score = отклик на вброс), а не кандидатство. Сначала лёгкая
    проверка вбросов; тяжёлый analyze — только если вбросы есть.
    Возвращает {injections_total, score(0..100), best_reagent} или None."""
    well = csvc.find_well(db, str(well_number))
    if not well:
        return None
    from datetime import datetime, time as _t
    end = datetime.combine(anchor_eff, _t(23, 59, 59))
    start = datetime.combine(anchor_eff - timedelta(days=180), _t(0, 0, 0))
    try:
        from backend.services import reagent_effectiveness_service as _res
        inj = _res._get_reagent_injections(int(well["id"]), start, end)
        if not inj:
            return {"injections_total": 0}
        res = _res.analyze_reagent_effectiveness(int(well["id"]), start, end)
    except Exception:
        log.exception("treatment effectiveness failed well=%s", well_number)
        return None
    best = res.get("best_reagent") or {}
    sc01 = float(best.get("score") or 0.0) if isinstance(best, dict) else 0.0
    return {
        "injections_total": int(res.get("injections_total") or 0),
        "merged_injections": int(res.get("merged_injections") or 0),
        "score": round(sc01 * 100.0, 0),
        "best_reagent": (best.get("reagent") if isinstance(best, dict) else None),
    }


def _filter_by_choke(df_all: pd.DataFrame, choke: float | None, tol: float = 0.01) -> pd.DataFrame:
    if df_all.empty or choke is None or "choke_mm" not in df_all.columns:
        return df_all
    ch = pd.to_numeric(df_all["choke_mm"], errors="coerce")
    mask = ch.notna() & (np.abs(ch - choke) <= tol)
    return df_all.loc[mask].reset_index(drop=True)
