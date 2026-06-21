"""Серверный генератор фактических описаний сегментов (работа скважины).

Описывает КАЖДЫЙ сегмент фактами, БЕЗ оценки эффективности/«реакции на вброс»
(эффективность реагента — отдельный блок). Состав описания:
  • точка перелома (начало сегмента);
  • был ли вброс ДО точки перелома и за сколько времени (окно PRE_CP_LOOKBACK_H);
  • сдвиг на переломе (рост/спад) — нейтральный факт;
  • длительность и тренд сегмента;
  • сколько вбросов совершено внутри сегмента;
  • метрики сегмента (Q, σ, диапазон, P_шл, P_уст, ΔP, простой, раб%);
  • сравнение с предыдущим сегментом по метрикам и по характеру.

Вход — snapshot формата segment_analysis_v2:
  snapshot["segments_extended"] : list[dict]
  snapshot["chart_data"]["q_total"] / ["dates"]
  snapshot["injections_table"]["events"] : [{date,reagent,amount_kg,segment_num}]

Публичная функция: build_rich_descriptions(snapshot) -> list[str]
"""
from __future__ import annotations

import re
from datetime import datetime

# Окно поиска вброса ПЕРЕД точкой перелома (часы). Настраиваемый параметр.
PRE_CP_LOOKBACK_H = 24
# Макс. разрыв между соседними вбросами, чтобы показывать их «пачкой» (часы).
# Если разрыв больше — показываем только последний (ближайший к перелому).
CLUSTER_GAP_H = 1
# Окно ПОСЛЕ точки перелома: вброс сразу после перелома (в начале сегмента)
# считаем относящимся к перелому; показываем точным временем.
POST_CP_WINDOW_H = 1

_TYPE_LABELS = {
    "initial": "Начальный",
    "stable": "Стабильно",
    "rise": "Рост",
    "decline": "Снижение",
    "sharp_rise": "Резкий рост",
    "sharp_decline": "Резкое снижение",
    "volatile": "Волатильный",
    "unknown": "Неопределён",
}


def is_stub_descriptions(descriptions) -> bool:
    """True, если описания нужно (пере)сгенерировать.

    Признак НОВОГО формата — наличие маркера «Длительность:». Всё остальное
    (пусто, старые короткие заглушки, старый текст «В период с …») считаем
    подлежащим замене.
    """
    if not descriptions:
        return True
    for d in descriptions:
        if isinstance(d, str) and "Длительность:" in d:
            return False
    return True


# ───────────────────────── helpers ─────────────────────────

def _num(v):
    try:
        if v is None:
            return None
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _fmt(v, d=1):
    f = _num(v)
    return f"{f:.{d}f}" if f is not None else "—"


def _signed(v, d=1):
    f = _num(v)
    if f is None:
        return "—"
    return f"{'+' if f >= 0 else '−'}{abs(f):.{d}f}"


def _hhmm(s):
    """'2026-02-17T12:00:..' / '2026-02-17 12:00' -> 'DD-MM HH:MM'."""
    t = str(s).replace("T", " ")
    return t[5:16] if len(t) >= 16 else t


def _parse_dt(s):
    if not s:
        return None
    t = str(s).replace("T", " ")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            pass
    try:
        return datetime.strptime(t[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _fmt_lag(minutes):
    m = int(round(minutes))
    if m < 60:
        return f"{m} мин"
    h, mm = divmod(m, 60)
    return f"{h} ч" if mm == 0 else f"{h} ч {mm} мин"


def _get_segment_injections(seg, events):
    """Вбросы внутри сегмента (по segment_num)."""
    num = seg.get("num")
    seg_events = [e for e in (events or []) if e.get("segment_num") == num]
    by_reagent: dict[str, int] = {}
    for ev in seg_events:
        r = ev.get("reagent") or "Неизвестный"
        by_reagent[r] = by_reagent.get(r, 0) + 1
    return {"total": len(seg_events), "byReagent": by_reagent}


def _get_trend_info(seg):
    mean_v = _num(seg.get("mean_value"))
    if mean_v is None:
        mean_v = _num(seg.get("mean_q"))
    slope = _num(seg.get("slope"))
    if slope is None:
        slope = _num(seg.get("slope_total"))
    days = _num(seg.get("days"))
    drift = (slope * days / mean_v * 100) if (mean_v and slope is not None and days is not None) else 0.0
    abs_drift = abs(drift)
    seg_type = seg.get("segment_type") or seg.get("type") or "unknown"
    desc, direction = {
        "sharp_rise": ("резкий рост", "up"),
        "rise": ("рост", "up"),
        "stable": ("стабильный уровень", "stable"),
        "decline": ("снижение", "down"),
        "sharp_decline": ("резкое снижение", "down"),
    }.get(seg_type, (_TYPE_LABELS.get(seg_type, seg_type).lower(), "unknown"))
    intensity = ""
    if direction != "stable":
        sign = "+" if drift > 0 else "−"
        val = f"{sign}{abs_drift:.1f}% за период"
        if abs_drift > 15:
            intensity = f"высокая интенсивность, {val}"
        elif abs_drift > 5:
            intensity = f"умеренная интенсивность, {val}"
        elif abs_drift > 1:
            intensity = f"низкая интенсивность, {val}"
    return {"description": desc, "intensity": intensity}


def _pre_cp_injections(cp_dt, events, lookback_h=PRE_CP_LOOKBACK_H, cluster_gap_h=CLUSTER_GAP_H):
    """Вбросы ДО точки перелома в окне lookback_h.

    Правило: берём ближайший к перелому вброс и присоединяем более ранние,
    пока разрыв МЕЖДУ соседними вбросами ≤ cluster_gap_h. На первом разрыве
    больше порога — останавливаемся (т.е. показываем только «пачку» рядом с
    переломом; если соседнего близкого вброса нет — только последний).
    Возвращает [(lag_min, reagent)], ближайший первым.
    """
    if cp_dt is None:
        return []
    cand = []
    for ev in events or []:
        ev_dt = _parse_dt(ev.get("date"))
        if ev_dt is None:
            continue
        lag_min = (cp_dt - ev_dt).total_seconds() / 60.0
        if 0 < lag_min <= lookback_h * 60:
            cand.append((ev_dt, lag_min, ev.get("reagent") or "Неизвестный"))
    if not cand:
        return []
    cand.sort(key=lambda x: x[0])  # по времени: ближайший к перелому — последний
    cluster = [cand[-1]]
    for i in range(len(cand) - 2, -1, -1):
        gap_min = (cluster[-1][0] - cand[i][0]).total_seconds() / 60.0
        if gap_min <= cluster_gap_h * 60:
            cluster.append(cand[i])
        else:
            break
    res = [(lag, r) for (_dt, lag, r) in cluster]
    res.sort(key=lambda x: x[0])
    return res


def _post_cp_injections(cp_dt, events, window_h=POST_CP_WINDOW_H):
    """Вбросы ЧУТЬ ПОСЛЕ точки перелома (в окне window_h). Нужны на случай,
    когда оператор занёс время вброса с погрешностью (позже фактического).
    Возвращает [(lag_after_min, reagent)], ближайший к перелому первым."""
    if cp_dt is None:
        return []
    out = []
    for ev in events or []:
        ev_dt = _parse_dt(ev.get("date"))
        if ev_dt is None:
            continue
        lag_min = (ev_dt - cp_dt).total_seconds() / 60.0
        if 0 < lag_min <= window_h * 60:
            out.append((lag_min, ev.get("reagent") or "Неизвестный", ev.get("date")))
    out.sort(key=lambda x: x[0])
    return out


def _median(vals):
    nums = sorted(x for x in (_num(v) for v in vals) if x is not None)
    if not nums:
        return None
    m = len(nums) // 2
    return nums[m] if len(nums) % 2 else (nums[m - 1] + nums[m]) / 2.0


def _edge_pair(series, si, ei, k=5):
    """Медиана первых k точек ВНУТРИ сегмента (после нач. перелома) и
    последних k точек (перед кон. переломом). Не пересекает границы сегмента.
    Возвращает (start_val, end_val) — оба или (None,None)."""
    if not series:
        return (None, None)
    n = len(series)
    si = max(0, si)
    ei = min(ei, n - 1)
    if ei < si:
        return (None, None)
    seg_len = ei - si + 1
    kk = max(1, min(k, seg_len // 2)) if seg_len >= 2 else 1
    start_v = _median(series[si:si + kk])
    end_v = _median(series[ei - kk + 1:ei + 1])
    return (start_v, end_v)


def _seg_dir(seg):
    t = seg.get("segment_type") or seg.get("type") or ""
    if t in ("rise", "sharp_rise"):
        return "up"
    if t in ("decline", "sharp_decline"):
        return "down"
    return "flat"


_GENITIVE = {
    "резкий рост": "резкого роста",
    "рост": "роста",
    "снижение": "снижения",
    "резкое снижение": "резкого снижения",
    "стабильный уровень": "стабильного уровня",
    "начальный": "начального участка",
}


def _genitive(desc):
    return _GENITIVE.get(desc, desc)


def _cp_type(prev_seg, seg):
    """Тип точки перелома по направлениям соседних сегментов."""
    if not prev_seg:
        return None
    pd, cd = _seg_dir(prev_seg), _seg_dir(seg)
    if pd == "up" and cd == "down":
        return "пик"
    if pd == "down" and cd == "up":
        return "впадина"
    return None


def _describe_segment(seg, prev_seg, idx, values, dp_values, dates, events, events_ops=None):
    n = len(dates)
    si = int(seg.get("start_idx") or 0)
    ei = min(int(seg.get("end_idx") or 1) - 1, n - 1)
    label = _TYPE_LABELS.get(seg.get("segment_type") or seg.get("type") or "unknown",
                             seg.get("type") or "")
    num = seg.get("num") or (idx + 1)
    cp_dt = _parse_dt(dates[si]) if 0 <= si < n else None

    tr = _get_trend_info(seg)
    days = seg.get("days")
    # Блоки описания (каждый — отдельная смысловая строка, разделяются \n).
    parts = [f"Сегмент {num} ({label})"]

    when = _hhmm(dates[si]) if 0 <= si < n else "—"
    # ── Перелом + тренд («X после Y») ──
    if idx == 0:
        parts.append(f"Перелом: начало периода ({when}).")
        trend_line = f"Тренд: {tr['description']}"
    else:
        parts.append(f"Перелом: {when}.")
        prev_desc = _get_trend_info(prev_seg)["description"]
        if _seg_dir(prev_seg) != _seg_dir(seg) and prev_desc:
            trend_line = f"Тренд: {tr['description']} после {_genitive(prev_desc)}"
        else:
            trend_line = f"Тренд: {tr['description']} (продолжение)"
    if tr["intensity"]:
        trend_line += f", {tr['intensity']}"
    parts.append(trend_line + ".")
    parts.append(f"Длительность периода: {days} ч.")

    # ── События у перелома: вброс(ы) до (лаг) + сразу после (точное время) ──
    if idx != 0:
        pre = _pre_cp_injections(cp_dt, events)
        post = _post_cp_injections(cp_dt, events)
        ev = [f"за {_fmt_lag(lag)} до перелома — вброс «{r}»" for lag, r in pre[:5]]
        ev += [f"{_hhmm(edt)} вброс «{r}»" for lag, r, edt in post]
        # прочие события (продувки/оборуд./прочее) у перелома
        for o in (events_ops or []):
            odt = _parse_dt(o.get("time"))
            if odt is None or cp_dt is None:
                continue
            lag_before = (cp_dt - odt).total_seconds() / 60.0
            if 0 < lag_before <= PRE_CP_LOOKBACK_H * 60:
                ev.append(f"за {_fmt_lag(lag_before)} до перелома — {str(o.get('label','')).lower()}")
            elif -POST_CP_WINDOW_H * 60 <= lag_before < 0:
                ev.append(f"{_hhmm(o.get('time'))} {str(o.get('label','')).lower()}")
        if ev:
            parts.append("События у перелома: " + "; ".join(ev) + ".")

    # ── Динамика (Q с→до + ΔP с→до) ──
    qs, qe = _edge_pair(values, si, ei, 5)
    if qs is not None and qe is not None:
        dq = qe - qs
        dqp = (dq / qs * 100) if qs else 0.0
        days_n = _num(days)
        line = f"Изменение дебита Q: {_fmt(qs, 1)} → {_fmt(qe, 1)} ({_signed(dq, 1)} тыс.м³, {_signed(dqp, 0)}%)"
        if days_n:
            line += f", темп {_signed(dq / days_n, 2)} тыс.м³/ч"
        parts.append(line + ".")
    if dp_values:
        ds, de = _edge_pair(dp_values, si, ei, 5)
        if ds is not None and de is not None:
            dd = de - ds
            ddp = (dd / ds * 100) if ds else 0.0
            sig = " — значимо" if abs(dd) >= 0.3 else ""
            parts.append(f"Перепад давления ΔP: {_fmt(ds, 2)} → {_fmt(de, 2)} "
                         f"({_signed(dd, 2)} кгс/см², {_signed(ddp, 0)}%){sig}.")

    # ── Вбросы в сегменте (точное время каждого) ──
    seg_ev = sorted(
        [(e.get("date"), e.get("reagent") or "Неизвестный")
         for e in (events or []) if e.get("segment_num") == num],
        key=lambda x: x[0] or "")
    if not seg_ev:
        parts.append("В период вбросов не осуществлялось.")
    else:
        lst = "; ".join(f"«{r}» {_hhmm(d)}" for d, r in seg_ev)
        parts.append(f"В период осуществлено вбросов: {len(seg_ev)} — {lst}.")

    # ── Прочие события в период (продувки/оборудование/прочее) ──
    t0 = _parse_dt(dates[si]) if 0 <= si < n else None
    t1 = _parse_dt(dates[ei]) if 0 <= ei < n else None
    seg_ops = []
    for o in (events_ops or []):
        odt = _parse_dt(o.get("time"))
        if odt and t0 and t1 and t0 <= odt <= t1:
            seg_ops.append((o.get("time"), str(o.get("label", ""))))
    if seg_ops:
        seg_ops.sort(key=lambda x: x[0] or "")
        lst2 = "; ".join(f"{lbl.lower()} {_hhmm(tm)}" for tm, lbl in seg_ops)
        parts.append("Другие события в период: " + lst2 + ".")

    # ── Метрики ──
    parts.append(
        "Метрики: "
        f"Q ср. {_fmt(seg.get('mean_q') if seg.get('mean_q') is not None else seg.get('mean_value'), 1)}; "
        f"σ {_fmt(seg.get('std_value'), 1)}; "
        f"диапазон {_fmt(seg.get('min_value'), 1)}–{_fmt(seg.get('max_value'), 1)}; "
        f"P_шл {_fmt(seg.get('mean_p_flowline'), 1)}; "
        f"P_уст {_fmt(seg.get('mean_p_wellhead'), 1)}; "
        f"ΔP ср. {_fmt(seg.get('mean_dp'), 2)}; "
        f"простой {_fmt(seg.get('mean_shutdown'), 0)} мин; "
        f"раб. {_fmt(seg.get('working_pct'), 0)}%."
    )

    # ── Сравнение с предыдущим ──
    if prev_seg:
        cmp = []
        mv = _num(seg.get("mean_q") if seg.get("mean_q") is not None else seg.get("mean_value"))
        pv = _num(prev_seg.get("mean_q") if prev_seg.get("mean_q") is not None else prev_seg.get("mean_value"))
        if mv is not None and pv:
            pct = (mv - pv) / pv * 100
            if abs(pct) > 3:
                cmp.append(f"среднее изменение дебита Q {'↑' if pct > 0 else '↓'} {abs(pct):.0f}% ({_fmt(pv,1)}→{_fmt(mv,1)})")
            else:
                cmp.append("средний дебит Q практически не изменился")
        dpc, dpp = _num(seg.get("mean_dp")), _num(prev_seg.get("mean_dp"))
        if dpc is not None and dpp is not None:
            cmp.append(f"ΔP {_signed(dpc - dpp, 2)}")
        wpc, wpp = _num(seg.get("working_pct")), _num(prev_seg.get("working_pct"))
        if wpc is not None and wpp is not None:
            cmp.append("раб. без изм" if abs(wpc - wpp) < 1 else f"раб. {_signed(wpc - wpp, 0)}%")
        pt = prev_seg.get("segment_type") or prev_seg.get("type")
        ct = seg.get("segment_type") or seg.get("type")
        if pt != ct:
            cmp.append(f"характер: {_get_trend_info(prev_seg)['description']} → {tr['description']}")
        if cmp:
            parts.append("Сравнение с предыдущим периодом: " + "; ".join(cmp) + ".")

    return "\n".join(parts)


def build_rich_descriptions(snapshot) -> list[str]:
    if not isinstance(snapshot, dict):
        return []
    segs = snapshot.get("segments_extended") or []
    if not segs:
        return []
    chart = snapshot.get("chart_data") or {}
    dates = chart.get("dates") or []
    values = chart.get("q_total")
    if not values and isinstance(chart.get("primary"), dict):
        values = chart["primary"].get("values")
    values = values or []
    dp_values = chart.get("dp") or []  # поточечный ΔP (если есть в снимке)
    events = ((snapshot.get("injections_table") or {}).get("events")) or []
    events_ops = snapshot.get("events_ops") or []

    out = []
    for i, seg in enumerate(segs):
        prev_seg = segs[i - 1] if i > 0 else None
        try:
            out.append(_describe_segment(seg, prev_seg, i, values, dp_values, dates, events, events_ops))
        except Exception:
            label = _TYPE_LABELS.get(seg.get("type") or "unknown", seg.get("type") or "")
            out.append(f"Сегмент {seg.get('num') or (i + 1)} ({label}). Длительность: {seg.get('days')} ч.")
    return out


# ───────────────────── события из БД (продувки/оборуд./прочее) ─────────────

def _purge_cycles(purge_rows):
    """Группирует фазы продувки в циклы. Новый цикл начинается с 'start'
    или при разрыве > PURGE_EPISODE_MAX_H. Полная продувка = есть все 3 фазы
    (start+press+stop), иначе — неполная с перечислением фаз."""
    PURGE_EPISODE_MAX_H = 12
    ru = {"start": "начало", "press": "под давлением", "stop": "остановка"}
    cycles, cur = [], None
    for t, phase in purge_rows:
        if cur is not None:
            gap_h = (t - cur["last"]).total_seconds() / 3600.0
            if phase == "start" or gap_h > PURGE_EPISODE_MAX_H:
                cycles.append(cur)
                cur = None
        if cur is None:
            cur = {"start": t, "last": t, "phases": []}
        cur["phases"].append(phase)
        cur["last"] = t
    if cur:
        cycles.append(cur)
    out = []
    for c in cycles:
        ph = set(c["phases"])
        if {"start", "press", "stop"} <= ph:
            label = "Продувка (полная)"
        else:
            present = [ru[p] for p in ("start", "press", "stop") if p in ph]
            label = "Продувка неполная (" + ", ".join(present) + ")" if present else "Продувка"
        out.append({"time": c["start"], "label": label, "kind": "purge"})
    return out


def fetch_ops_events(db, well_number, d_from, d_to):
    """События скважины из таблицы events (кроме вбросов и замеров давления):
    продувки (циклами), оборудование, прочее. Возвращает [{time,label,kind}].
    Метки берутся из daily_report_service._smart_event_label (суть, не 'other')."""
    if not (well_number and d_from and d_to):
        return []
    own = None
    if db is None:
        try:
            from backend.db import SessionLocal
            db = own = SessionLocal()
        except Exception:
            return []
    try:
        from sqlalchemy import text
        from datetime import datetime, timedelta
        from backend.services.daily_report_service import _smart_event_label
        _ds = datetime.strptime(str(d_from)[:10], "%Y-%m-%d")
        _de = datetime.strptime(str(d_to)[:10], "%Y-%m-%d") + timedelta(days=1)
        rows = db.execute(text("""
            SELECT event_time, event_type, description, purge_phase
            FROM events
            WHERE well = :wno AND event_type IN ('purge', 'equip', 'other')
              AND event_time >= :ds AND event_time < :de
            ORDER BY event_time
        """), {"wno": str(well_number), "ds": _ds, "de": _de}).fetchall()
    except Exception:
        return []
    finally:
        if own is not None:
            own.close()
    purge_rows, ops = [], []
    for et_time, et, desc, phase in rows:
        if et == "purge":
            purge_rows.append((et_time, (phase or "").lower()))
        else:
            ops.append({"time": et_time, "label": _smart_event_label(et, desc or ""), "kind": et})
    ops += _purge_cycles(purge_rows)
    for o in ops:
        t = o["time"]
        o["time"] = t.isoformat() if hasattr(t, "isoformat") else str(t)
    ops.sort(key=lambda x: x["time"])
    return ops


# ───────────────────── read-time enrichment ─────────────────────

def enrich_snapshot_descriptions(snapshot, db=None) -> bool:
    """Заменяет interpretation.descriptions фактическим текстом IN PLACE,
    если там заглушки/старый формат. Возвращает True при замене. No-op при
    недостатке данных или если уже новый формат."""
    if not isinstance(snapshot, dict):
        return False
    if not snapshot.get("segments_extended"):
        return False
    interp = snapshot.get("interpretation")
    if not isinstance(interp, dict):
        interp = {}
        snapshot["interpretation"] = interp
    if not is_stub_descriptions(interp.get("descriptions") or []):
        return False
    # Подтягиваем события из БД (продувки/оборуд./прочее). Сессию берём из db
    # или (если не передана, напр. при сборке PDF) fetch_ops_events откроет свою.
    if "events_ops" not in snapshot:
        try:
            snapshot["events_ops"] = fetch_ops_events(
                db, snapshot.get("well_number"),
                snapshot.get("date_from"), snapshot.get("date_to"))
        except Exception:
            snapshot["events_ops"] = []
    rich = build_rich_descriptions(snapshot)
    if not rich:
        return False
    interp["descriptions"] = rich
    if "descriptions" in snapshot:
        snapshot["descriptions"] = rich
    return True


def enrich_block_descriptions(block, db=None) -> None:
    """Обогащает блок-словарь (ключ data_snapshot) IN PLACE для
    kind='segment_analysis'. Тихо игнорирует прочее/ошибки."""
    try:
        if not isinstance(block, dict):
            return
        if block.get("kind") != "segment_analysis":
            return
        snap = block.get("data_snapshot")
        if isinstance(snap, dict):
            enrich_snapshot_descriptions(snap, db=db)
    except Exception:
        pass
