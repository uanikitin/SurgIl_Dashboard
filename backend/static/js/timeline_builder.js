/**
 * TimelineBuilder — универсальный компонент временной шкалы для выбора периода.
 *
 * Используется: observation_ui.js, customer_daily.html, adaptation_wizard.html
 *
 * Публичный API:
 *   const timeline = window.TimelineBuilder.create(config);
 *   await timeline.init();
 *   timeline.getSelection() → { from: Date, to: Date }
 *   timeline.setSelection(from, to)
 *   timeline.destroy()
 */
(function() {
  'use strict';

  // ══════════════════════════════════════════════════════════════════════════
  //   КОНСТАНТЫ
  // ══════════════════════════════════════════════════════════════════════════

  const DEFAULT_CONFIG = {
    containerId: null,
    wellId: null,
    apiEndpoint: '/api/adaptation-report/timeline',
    cssPrefix: 'tl',
    dateInputs: null,  // { from: 'input-id', to: 'input-id' }
    onChange: null,    // callback(from, to)
    onLoad: null,      // callback(data)
    onSourceChange: null, // callback(sourceKey)
    features: {
      bars: { customer: true, sensors: true },
      stages: true,
      events: true,
      yearFilter: true,
      anchorMode: true,
      zoom: true,
      sourcesBlock: false, // Показывать блок «Источник периода»
    },
    // Источники периода (предустановленные варианты).
    // Пример: [{ key: 'baseline', label: 'Из baseline', from: '2026-01-01', to: '2026-02-01' }, ...]
    // Специальный key='timeline' = выбор из таймлайна; key='manual' = ввод вручную.
    sources: null,
  };

  const ZOOM_PX_PER_DAY = { day: 12, week: 4, month: 1.6, year: 0.5 };
  const SCALE_HEIGHT = 160;
  const BAR_HEIGHT = 18;
  const BAR_Y = { customer: 76, sensors: 102 };

  // ══════════════════════════════════════════════════════════════════════════
  //   УТИЛИТЫ (приватные)
  // ══════════════════════════════════════════════════════════════════════════

  function _ymd(d) {
    if (!d || isNaN(d.getTime())) return '';
    return [
      d.getFullYear(),
      String(d.getMonth() + 1).padStart(2, '0'),
      String(d.getDate()).padStart(2, '0'),
    ].join('-');
  }

  function _parse(s) {
    if (!s) return null;
    if (s instanceof Date) return isNaN(s.getTime()) ? null : s;
    const m = String(s).slice(0, 10).match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3]);
  }

  function _addDays(d, n) {
    if (!d) return null;
    const out = new Date(d);
    out.setDate(out.getDate() + n);
    return out;
  }

  function _dayDiff(a, b) {
    if (!a || !b) return 0;
    return Math.round((b - a) / 86400000);
  }

  function _escHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function _mergeDeep(target, source) {
    for (const key of Object.keys(source)) {
      if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key])) {
        target[key] = target[key] || {};
        _mergeDeep(target[key], source[key]);
      } else {
        target[key] = source[key];
      }
    }
    return target;
  }

  // ══════════════════════════════════════════════════════════════════════════
  //   ФАБРИКА ЭКЗЕМПЛЯРА
  // ══════════════════════════════════════════════════════════════════════════

  function createInstance(userConfig) {
    // Мержим конфигурацию
    const config = _mergeDeep(JSON.parse(JSON.stringify(DEFAULT_CONFIG)), userConfig);
    const P = config.cssPrefix;  // префикс для CSS классов

    // Приватное состояние
    const state = {
      data: null,
      earliest: null,
      latest: null,
      earliestRaw: null,
      windowMode: 'year',
      windowYear: null,
      zoom: 'month',
      from: null,
      to: null,
      anchor: null,
      listeners: [],
      selectedSource: null,  // Выбранный источник периода (key из config.sources)
    };

    // ────────────────────────────────────────────────────────────────────────
    //   DOM HELPERS
    // ────────────────────────────────────────────────────────────────────────

    function getContainer() {
      return document.getElementById(config.containerId);
    }

    function el(selector) {
      const container = getContainer();
      return container ? container.querySelector(selector) : null;
    }

    function addListener(element, event, handler) {
      if (!element) return;
      element.addEventListener(event, handler);
      state.listeners.push({ el: element, ev: event, fn: handler });
    }

    // ────────────────────────────────────────────────────────────────────────
    //   ВЫЧИСЛЕНИЯ
    // ────────────────────────────────────────────────────────────────────────

    function pct(d) {
      if (!d || !state.earliest || !state.latest) return 0;
      const total = (state.latest - state.earliest) || 86400000;
      return Math.max(0, Math.min(100, ((d - state.earliest) / total) * 100));
    }

    function scaleWidth() {
      if (!state.earliest || !state.latest) return '100%';
      const days = _dayDiff(state.earliest, state.latest) + 1;
      const px = ZOOM_PX_PER_DAY[state.zoom] || 1.6;
      return Math.max(600, Math.round(days * px)) + 'px';
    }

    function yearOptions() {
      const years = new Set();
      const today = _parse(state.data?.today) || new Date();
      years.add(today.getFullYear());
      if (state.earliestRaw) {
        const fromY = state.earliestRaw.getFullYear();
        const toY = today.getFullYear();
        for (let y = fromY; y <= toY; y++) years.add(y);
      }
      return Array.from(years).sort((a, b) => b - a);
    }

    // ────────────────────────────────────────────────────────────────────────
    //   WINDOW MODE
    // ────────────────────────────────────────────────────────────────────────

    function applyWindow() {
      const today = state.latest || new Date();
      if (state.windowMode === 'all' || !state.earliestRaw) {
        state.earliest = state.earliestRaw || _addDays(today, -365);
        state.latest = _parse(state.data?.today) || today;
        return;
      }
      const y = state.windowYear || today.getFullYear();
      state.earliest = new Date(y, 0, 1);
      const todayCur = _parse(state.data?.today) || today;
      if (y === todayCur.getFullYear()) {
        state.latest = todayCur;
      } else {
        state.latest = new Date(y, 11, 31);
      }
    }

    // ────────────────────────────────────────────────────────────────────────
    //   ЗАГРУЗКА ДАННЫХ
    // ────────────────────────────────────────────────────────────────────────

    async function loadData() {
      if (!config.wellId) return;
      try {
        const url = `${config.apiEndpoint}?well_id=${config.wellId}&_=${Date.now()}`;
        const resp = await fetch(url, { cache: 'no-store' });
        if (!resp.ok) {
          console.warn('[TimelineBuilder] HTTP', resp.status);
          return;
        }
        const d = await resp.json();
        state.data = d;

        // Определяем диапазон
        const cands = [];
        if (d.customer_data?.first_date) cands.push(_parse(d.customer_data.first_date));
        if (d.our_data?.first_date) cands.push(_parse(d.our_data.first_date));
        for (const s of (d.stages || [])) {
          if (s.dt_start) cands.push(_parse(s.dt_start));
        }
        const today = _parse(d.today) || new Date();
        state.earliestRaw = cands.length
          ? _addDays(cands.reduce((a, b) => a < b ? a : b), -7)
          : _addDays(today, -365);
        state.latest = today;
        if (!state.windowYear) state.windowYear = today.getFullYear();

        applyWindow();

        if (config.onLoad) config.onLoad(d);
      } catch (e) {
        console.error('[TimelineBuilder] loadData error:', e);
      }
    }

    // ────────────────────────────────────────────────────────────────────────
    //   РЕНДЕР СТРУКТУРЫ (один раз)
    // ────────────────────────────────────────────────────────────────────────

    function renderStructure() {
      const container = getContainer();
      if (!container) return;

      const f = config.features;

      container.innerHTML = `
        <div class="${P}-wrap">
          <div class="${P}-headerrow">
            Режим: <span class="${P}-mode">не выбран</span>
            <span class="${P}-period-display"></span>
          </div>
          ${f.zoom || f.yearFilter ? `
          <div class="${P}-zoom-row">
            ${f.zoom ? `
            <span>Масштаб:</span>
            <button type="button" class="${P}-btn" data-zoom="day">Сутки</button>
            <button type="button" class="${P}-btn" data-zoom="week">Неделя</button>
            <button type="button" class="${P}-btn active" data-zoom="month">Месяц</button>
            <button type="button" class="${P}-btn" data-zoom="year">Год</button>
            ` : ''}
            ${f.yearFilter ? `<span class="${P}-year-buttons"></span>` : ''}
            <button type="button" class="${P}-btn ${P}-clear-btn">✕ Сбросить</button>
          </div>
          ` : ''}
          <div class="${P}-scroll">
            <div class="${P}-scale"></div>
          </div>
          ${f.stages ? `<div class="${P}-shortcuts-row" style="display:none;"></div>` : ''}
          ${f.anchorMode ? `
          <div class="${P}-anchor-bar" style="display:none;">
            От точки <b class="${P}-anchor-date">—</b>:
            <span>назад</span>
            <button type="button" class="${P}-btn" data-shift="back" data-days="3">−3 сут.</button>
            <button type="button" class="${P}-btn" data-shift="back" data-days="7">−1 нед.</button>
            <button type="button" class="${P}-btn" data-shift="back" data-days="30">−1 мес.</button>
            <span>вперёд</span>
            <button type="button" class="${P}-btn" data-shift="fwd" data-days="3">+3 сут.</button>
            <button type="button" class="${P}-btn" data-shift="fwd" data-days="7">+1 нед.</button>
            <button type="button" class="${P}-btn" data-shift="fwd" data-days="30">+1 мес.</button>
            <span>±N сут.:</span>
            <input type="number" class="${P}-custom-days" min="1" max="3650" step="1"
                   style="width:60px;" placeholder="N">
            <button type="button" class="${P}-btn ${P}-custom-back">−N</button>
            <button type="button" class="${P}-btn ${P}-custom-fwd">+N</button>
          </div>
          ` : ''}
          ${f.sourcesBlock ? `<div class="${P}-sources-block"></div>` : ''}
          <div class="${P}-help">
            <b>Клик по шкале</b> — установить точку.
            <b>Клик по этапу</b> — взять весь период.
            ${f.anchorMode ? '<b>Кнопки ±N</b> — расширить от точки.' : ''}
          </div>
        </div>
      `;

      injectStyles();
    }

    // ────────────────────────────────────────────────────────────────────────
    //   СТИЛИ (инжектим один раз)
    // ────────────────────────────────────────────────────────────────────────

    function injectStyles() {
      const styleId = `${P}-styles`;
      if (document.getElementById(styleId)) return;

      const css = `
        .${P}-wrap {
          background: #fafafa; border: 1px solid #e5e7eb; border-radius: 6px;
          padding: 10px 12px; margin-bottom: 12px;
        }
        .${P}-headerrow {
          display: flex; align-items: center; gap: 10px;
          font-size: 0.84rem; color: #374151; flex-wrap: wrap; margin-bottom: 8px;
        }
        .${P}-mode { color: #1d4ed8; font-weight: 500; }
        .${P}-period-display {
          margin-left: auto; font-family: monospace; color: #374151; font-size: 0.78rem;
        }
        .${P}-zoom-row {
          display: flex; align-items: center; gap: 8px; margin-bottom: 6px;
          font-size: 0.78rem; color: #6b7280; flex-wrap: wrap;
        }
        .${P}-btn {
          padding: 3px 10px; font-size: 0.76rem; background: #fff;
          border: 1px solid #d1d5db; border-radius: 999px; cursor: pointer; color: #374151;
        }
        .${P}-btn:hover { background: #eff6ff; border-color: #93c5fd; }
        .${P}-btn.active { background: #2563eb; color: #fff; border-color: #1d4ed8; }
        .${P}-scroll {
          width: 100%; overflow-x: auto; overflow-y: hidden;
          background: #fff; border: 1px solid #e5e7eb; border-radius: 4px;
          padding: 6px 0; margin-bottom: 4px;
        }
        .${P}-scale {
          position: relative; height: ${SCALE_HEIGHT}px; min-width: 100%;
          cursor: crosshair; user-select: none;
        }
        .${P}-bar {
          position: absolute; height: ${BAR_HEIGHT}px; border-radius: 3px; pointer-events: none;
        }
        .${P}-bar-customer { top: ${BAR_Y.customer}px; background: #93c5fd; }
        .${P}-bar-sensors { top: ${BAR_Y.sensors}px; background: #6ee7b7; }
        .${P}-bar-label {
          position: absolute; left: 6px; font-size: 0.7rem;
          color: rgba(0,0,0,0.7); line-height: ${BAR_HEIGHT}px; pointer-events: none;
        }
        .${P}-bar-tip {
          position: absolute; color: #fff; font-size: 0.65rem; font-weight: 600;
          padding: 1px 5px; border-radius: 2px; white-space: nowrap; pointer-events: none;
        }
        .${P}-stage-line {
          position: absolute; top: 12px; height: 130px; width: 2px;
          background: currentColor; opacity: 0.45;
          transform: translateX(-50%); pointer-events: none;
        }
        .${P}-stage-dot {
          position: absolute; top: 6px; width: 14px; height: 14px;
          border-radius: 50%; background: currentColor;
          box-shadow: 0 0 0 2px #fff, 0 1px 4px rgba(0,0,0,0.2);
          transform: translateX(-50%); cursor: pointer; pointer-events: auto;
        }
        .${P}-stage-dot:hover { transform: translateX(-50%) scale(1.4); }
        .${P}-stage-band {
          position: absolute; height: 10px; border-radius: 4px; opacity: 0.7;
          pointer-events: auto; cursor: pointer;
          transition: opacity 0.15s, height 0.15s, top 0.15s;
        }
        .${P}-stage-band:hover {
          opacity: 1; height: 14px;
        }
        .${P}-stage-band-label {
          position: absolute; top: -18px; left: 50%; transform: translateX(-50%);
          font-size: 0.68rem; font-weight: 600; color: #374151;
          background: rgba(255,255,255,0.95); padding: 1px 6px; border-radius: 3px;
          white-space: nowrap; pointer-events: none; opacity: 0;
          transition: opacity 0.15s;
          box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .${P}-stage-band:hover .${P}-stage-band-label { opacity: 1; }
        .${P}-stage-label-side {
          position: absolute; font-size: 0.66rem; font-weight: 600;
          background: #fff; padding: 1px 4px; border-radius: 2px;
          border: 1px solid currentColor; white-space: nowrap;
          pointer-events: none; line-height: 14px;
        }
        .${P}-event-icon {
          position: absolute; top: 54px; transform: translateX(-50%);
          font-size: 13px; line-height: 13px; background: #fff;
          border-radius: 50%; padding: 1px 2px;
          box-shadow: 0 0 0 1px currentColor, 0 1px 3px rgba(0,0,0,0.15);
          pointer-events: auto; cursor: help;
        }
        .${P}-range {
          position: absolute; top: 70px; height: 60px;
          background: rgba(220,38,38,0.10);
          border-left: 2px solid rgba(220,38,38,0.55);
          border-right: 2px solid rgba(220,38,38,0.55); pointer-events: none;
        }
        .${P}-range-label {
          position: absolute; top: -2px; left: 6px;
          background: rgba(220,38,38,0.85); color: #fff;
          font-size: 0.66rem; font-weight: 600;
          padding: 1px 5px; border-radius: 2px; white-space: nowrap;
        }
        .${P}-anchor {
          position: absolute; top: 0; bottom: 18px; width: 0; pointer-events: none;
        }
        .${P}-anchor-line {
          position: absolute; top: 0; bottom: 0; left: -1px;
          width: 2px; background: #dc2626;
          box-shadow: 0 0 6px rgba(220,38,38,0.5);
        }
        .${P}-axis {
          position: absolute; bottom: 0; left: 0; right: 0; height: 16px; pointer-events: none;
        }
        .${P}-axis-tick {
          position: absolute; top: 0; font-size: 0.62rem; color: #6b7280;
          transform: translateX(-50%); white-space: nowrap;
        }
        .${P}-axis-tick::before {
          content: ""; display: block; width: 1px; height: 4px;
          background: #9ca3af; margin: 0 auto 1px;
        }
        .${P}-shortcuts-row {
          display: flex; align-items: center; gap: 8px; margin-top: 6px;
          font-size: 0.78rem; color: #6b7280; flex-wrap: wrap;
        }
        .${P}-anchor-bar {
          display: flex; align-items: center; gap: 6px; margin-top: 6px;
          font-size: 0.78rem; color: #374151; flex-wrap: wrap;
          padding: 8px 10px; background: #fef3c7; border: 1px solid #fcd34d;
          border-radius: 5px;
        }
        .${P}-anchor-bar b { color: #b45309; }
        .${P}-sources-block {
          margin-top: 12px; padding: 10px 12px;
          background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;
        }
        .${P}-sources-title {
          font-size: 0.82rem; font-weight: 600; color: #374151;
          margin-bottom: 8px; display: flex; align-items: center; gap: 6px;
        }
        .${P}-sources-list {
          display: flex; flex-direction: column; gap: 4px;
        }
        .${P}-source-item {
          display: flex; align-items: center; gap: 8px;
          padding: 6px 10px; border: 1px solid #e2e8f0; border-radius: 5px;
          background: #fff; cursor: pointer; transition: all 0.15s;
          font-size: 0.84rem;
        }
        .${P}-source-item:hover { border-color: #94a3b8; background: #f1f5f9; }
        .${P}-source-item.active {
          border-color: #2563eb; background: #eff6ff;
        }
        .${P}-source-item.disabled {
          opacity: 0.5; cursor: not-allowed; pointer-events: none;
        }
        .${P}-source-item input[type="radio"] { margin: 0; }
        .${P}-source-label { flex: 1; color: #374151; }
        .${P}-source-dates {
          font-family: monospace; font-size: 0.76rem; color: #64748b;
          white-space: nowrap;
        }
        .${P}-source-dates.has-dates { color: #059669; font-weight: 500; }
        .${P}-help {
          font-size: 0.7rem; color: #9ca3af; margin-top: 4px;
          display: flex; gap: 12px; flex-wrap: wrap;
        }
        .${P}-help b { color: #4b5563; }
      `;

      const style = document.createElement('style');
      style.id = styleId;
      style.textContent = css;
      document.head.appendChild(style);
    }

    // ────────────────────────────────────────────────────────────────────────
    //   РЕНДЕР ШКАЛЫ
    // ────────────────────────────────────────────────────────────────────────

    function render() {
      const scale = el(`.${P}-scale`);
      if (!scale || !state.earliest || !state.latest) return;

      scale.style.width = scaleWidth();
      const d = state.data || {};
      const f = config.features;
      const parts = [];

      // ── Полоса заказчика ──
      if (f.bars?.customer) {
        if (d.customer_data?.first_date && d.customer_data?.last_date) {
          const a = pct(_parse(d.customer_data.first_date));
          const b = pct(_parse(d.customer_data.last_date));
          parts.push(
            `<div class="${P}-bar ${P}-bar-customer" style="left:${a}%; width:${Math.max(b - a, 0.3)}%;"
                  title="Заказчик (well_daily): ${d.customer_data.first_date} — ${d.customer_data.last_date} (${d.customer_data.days_count} дн.)"></div>`,
            `<div class="${P}-bar-label" style="top:${BAR_Y.customer}px;">Заказчик · ${d.customer_data.days_count || 0} дн.</div>`,
            `<div class="${P}-bar-tip" style="left:${a}%; top:${BAR_Y.customer - 14}px; transform:translateX(-50%); background:#1d4ed8;">${d.customer_data.first_date}</div>`,
            `<div class="${P}-bar-tip" style="left:${b}%; top:${BAR_Y.customer - 14}px; transform:translateX(-50%); background:#1d4ed8;">${d.customer_data.last_date}</div>`
          );
        } else {
          parts.push(`<div class="${P}-bar-label" style="top:${BAR_Y.customer}px; color:#9ca3af;">Заказчик · нет данных</div>`);
        }
      }

      // ── Полоса датчиков ──
      if (f.bars?.sensors) {
        if (d.our_data?.first_date && d.our_data?.last_date) {
          const ourStart = _parse(d.our_data.first_date);
          const ourEnd = _parse(d.our_data.last_date);
          const equipEv = (d.events || []).find(e => e.kind === 'equip');
          const equipDate = equipEv ? _parse(equipEv.dt) : null;

          if (equipDate && ourStart < equipDate) {
            const a = pct(ourStart);
            const m = pct(equipDate);
            parts.push(
              `<div class="${P}-bar" style="left:${a}%; width:${Math.max(m - a, 0.3)}%; top:${BAR_Y.sensors}px; background:#9ca3af; opacity:0.5;"
                    title="Датчики до установки"></div>`
            );
            if (ourEnd > equipDate) {
              const b = pct(ourEnd);
              parts.push(
                `<div class="${P}-bar ${P}-bar-sensors" style="left:${m}%; width:${Math.max(b - m, 0.3)}%;"
                      title="Датчики: ${equipEv.dt.slice(0, 10)} — ${d.our_data.last_date}"></div>`
              );
            }
            parts.push(`<div class="${P}-bar-label" style="top:${BAR_Y.sensors}px;">Датчики (серый = до привязки)</div>`);
          } else {
            const a = pct(ourStart);
            const b = pct(ourEnd);
            parts.push(
              `<div class="${P}-bar ${P}-bar-sensors" style="left:${a}%; width:${Math.max(b - a, 0.3)}%;"
                    title="Датчики: ${d.our_data.first_date} — ${d.our_data.last_date}"></div>`,
              `<div class="${P}-bar-label" style="top:${BAR_Y.sensors}px;">Датчики</div>`
            );
          }
        } else {
          parts.push(`<div class="${P}-bar-label" style="top:${BAR_Y.sensors}px; color:#9ca3af;">Датчики · нет данных</div>`);
        }
      }

      // ── Этапы ──
      if (f.stages) {
        const stages = (d.stages || []).filter(s => s.dt_start);
        stages.forEach((s, i) => {
          const xStart = pct(_parse(s.dt_start));
          const xEnd = s.dt_end ? pct(_parse(s.dt_end)) : 100;
          const yBand = 24 + i * 16;  // Позиция полоски (с запасом для меток)
          const stageFrom = _ymd(_parse(s.dt_start));
          const stageTo = s.dt_end ? _ymd(_parse(s.dt_end)) : _ymd(state.latest);
          const bandWidth = Math.max(xEnd - xStart, 0.5);
          const daysCount = _dayDiff(_parse(s.dt_start), _parse(stageTo)) + 1;
          const bandTitle = `${_escHtml(s.label)}: ${s.dt_start.slice(0, 10)} → ${stageTo} (${daysCount} дн.)`;

          // Полоска этапа (кликабельная!)
          parts.push(
            `<div class="${P}-stage-band" style="left:${xStart}%; width:${bandWidth}%; top:${yBand}px; background:${s.color};"
                  data-stage-from="${stageFrom}"
                  data-stage-to="${stageTo}"
                  title="${bandTitle}">
               <span class="${P}-stage-band-label">${_escHtml(s.label)} · ${daysCount} дн.</span>
            </div>`
          );

          // Вертикальные линии начала и конца
          parts.push(
            `<div class="${P}-stage-line" style="left:${xStart}%; color:${s.color};"></div>`
          );
          if (s.dt_end) {
            parts.push(`<div class="${P}-stage-line" style="left:${xEnd}%; color:${s.color}; opacity:0.35;"></div>`);
          }

          // Точка начала (тоже кликабельная)
          parts.push(
            `<div class="${P}-stage-dot" style="left:${xStart}%; color:${s.color};"
                  data-stage-from="${stageFrom}"
                  data-stage-to="${stageTo}"
                  title="${bandTitle}">
            </div>`
          );

          // Метка сбоку
          parts.push(
            `<div class="${P}-stage-label-side"
                  style="left:${xStart}%; top:${1 + i * 17}px; color:${s.color};
                         transform:translateX(${xStart > 70 ? '-100%' : '6px'});">
               ${_escHtml(s.label.slice(0, 16))} · ${s.dt_start.slice(0, 10)}${s.dt_end ? ' → ' + s.dt_end.slice(0, 10) : ''}
            </div>`
          );
        });
      }

      // ── События ──
      if (f.events) {
        for (const ev of (d.events || [])) {
          if (!ev.dt) continue;
          const x = pct(_parse(ev.dt));
          parts.push(
            `<div class="${P}-event-icon" style="left:${x}%; color:${ev.color};"
                  title="${_escHtml(ev.label)}: ${ev.dt.slice(0, 10)}">
               ${ev.icon || '●'}
            </div>`
          );
        }
      }

      // ── Выбранный диапазон ──
      if (state.from && state.to) {
        const a = pct(state.from);
        const b = pct(state.to);
        const days = _dayDiff(state.from, state.to) + 1;
        parts.push(
          `<div class="${P}-range" style="left:${a}%; width:${Math.max(b - a, 0.3)}%;">
             <div class="${P}-range-label">${_ymd(state.from)} → ${_ymd(state.to)} (${days} дн.)</div>
           </div>`
        );
      } else if (state.from) {
        const x = pct(state.from);
        parts.push(
          `<div class="${P}-anchor" style="left:${x}%;">
             <div class="${P}-anchor-line"></div>
           </div>`
        );
      }

      // ── Ось дат ──
      const totalDays = _dayDiff(state.earliest, state.latest) || 1;
      let step;
      if (state.zoom === 'day') step = 7;
      else if (state.zoom === 'week') step = 14;
      else if (state.zoom === 'month') step = totalDays > 365 ? 60 : 30;
      else step = totalDays > 1500 ? 180 : 90;

      const axis = [];
      for (let cur = new Date(state.earliest); cur <= state.latest; cur = _addDays(cur, step)) {
        const x = pct(cur);
        let lbl;
        if (state.zoom === 'day' || state.zoom === 'week') {
          lbl = String(cur.getDate()).padStart(2, '0') + '.' + String(cur.getMonth() + 1).padStart(2, '0');
        } else {
          lbl = String(cur.getMonth() + 1).padStart(2, '0') + '.' + String(cur.getFullYear()).slice(-2);
        }
        axis.push(`<div class="${P}-axis-tick" style="left:${x}%;">${lbl}</div>`);
      }
      parts.push(`<div class="${P}-axis">${axis.join('')}</div>`);

      scale.innerHTML = parts.join('');

      // Bind stage-dot clicks
      scale.querySelectorAll(`.${P}-stage-dot`).forEach(dot => {
        dot.addEventListener('click', ev => {
          ev.stopPropagation();
          const f = dot.dataset.stageFrom;
          const t = dot.dataset.stageTo;
          if (!f || !t) return;
          state.from = _parse(f);
          state.to = _parse(t);
          state.anchor = null;
          state.selectedSource = 'timeline';  // Выбор из таймлайна
          syncToInputs();
          render();
          updateMode();
          updateAnchorBar();
          fireOnChange();
        });
      });

      // Bind stage-band clicks (полоска этапа — клик выбирает весь период)
      scale.querySelectorAll(`.${P}-stage-band`).forEach(band => {
        band.addEventListener('click', ev => {
          ev.stopPropagation();
          const f = band.dataset.stageFrom;
          const t = band.dataset.stageTo;
          if (!f || !t) return;
          state.from = _parse(f);
          state.to = _parse(t);
          state.anchor = null;
          state.selectedSource = 'timeline';  // Выбор из таймлайна
          syncToInputs();
          render();
          updateMode();
          updateAnchorBar();
          fireOnChange();
        });
      });

      renderYearButtons();
      renderShortcuts();
      renderSources();
    }

    // ────────────────────────────────────────────────────────────────────────
    //   РЕНДЕР КНОПОК ГОДА
    // ────────────────────────────────────────────────────────────────────────

    function renderYearButtons() {
      if (!config.features.yearFilter) return;
      const row = el(`.${P}-year-buttons`);
      if (!row) return;

      const years = yearOptions();
      const todayY = (_parse(state.data?.today) || new Date()).getFullYear();
      const activeY = (state.windowMode === 'year') ? state.windowYear : null;
      const activeAll = (state.windowMode === 'all');

      let html = '<span style="margin-left:8px;">Окно:</span>';
      for (const y of years) {
        const isActive = y === activeY;
        html += `<button type="button" class="${P}-btn ${isActive ? 'active' : ''}"
                         data-year="${y}">${y}${y === todayY ? ' (тек.)' : ''}</button>`;
      }
      html += `<button type="button" class="${P}-btn ${activeAll ? 'active' : ''}"
                       data-window-all="1">Вся история</button>`;
      row.innerHTML = html;

      row.querySelectorAll(`[data-year]`).forEach(btn => {
        btn.addEventListener('click', () => {
          state.windowMode = 'year';
          state.windowYear = parseInt(btn.dataset.year, 10);
          applyWindow();
          render();
        });
      });

      row.querySelectorAll(`[data-window-all]`).forEach(btn => {
        btn.addEventListener('click', () => {
          state.windowMode = 'all';
          state.windowYear = null;
          applyWindow();
          render();
        });
      });
    }

    // ────────────────────────────────────────────────────────────────────────
    //   РЕНДЕР SHORTCUT-КНОПОК
    // ────────────────────────────────────────────────────────────────────────

    function renderShortcuts() {
      if (!config.features.stages) return;
      const row = el(`.${P}-shortcuts-row`);
      if (!row) return;

      const stages = (state.data?.stages || []).filter(s => s.dt_start);
      const events = (state.data?.events || []);

      if (!stages.length && !events.length) {
        row.style.display = 'none';
        return;
      }

      let html = '<span>Этапы и точки:</span>';

      // События (Установка, Первый вброс) — ставят anchor (точку отсчёта)
      for (const ev of events) {
        if (!ev.dt) continue;
        html += `<button type="button" class="${P}-btn"
                         data-anchor="${ev.dt.slice(0, 10)}"
                         style="border-color:${ev.color}; color:${ev.color};"
                         title="Точка отсчёта: ${ev.dt.slice(0, 10)}">
                   ${ev.icon || '●'} ${_escHtml(ev.label)}: ${ev.dt.slice(0, 10)}
                 </button>`;
      }

      // Этапы — клик выбирает ВЕСЬ диапазон этапа
      for (const s of stages) {
        if (!s.dt_start) continue;
        const fromStr = s.dt_start.slice(0, 10);
        const toStr = s.dt_end ? s.dt_end.slice(0, 10) : _ymd(state.latest);
        html += `<button type="button" class="${P}-btn ${P}-stage-shortcut"
                         data-stage-from="${fromStr}"
                         data-stage-to="${toStr}"
                         style="border-color:${s.color}; color:${s.color}; font-weight:600;"
                         title="Выбрать весь период: ${fromStr} → ${toStr}">
                   ${_escHtml(s.label.slice(0, 24))}
                 </button>`;
      }

      row.innerHTML = html;
      row.style.display = '';

      // События — ставят anchor (точку отсчёта)
      row.querySelectorAll('[data-anchor]').forEach(btn => {
        btn.addEventListener('click', () => {
          const d = _parse(btn.dataset.anchor);
          if (d) setAnchor(d);
        });
      });

      // Этапы — выбирают ВЕСЬ диапазон
      row.querySelectorAll('[data-stage-from]').forEach(btn => {
        btn.addEventListener('click', () => {
          const f = btn.dataset.stageFrom;
          const t = btn.dataset.stageTo;
          if (!f || !t) return;
          state.from = _parse(f);
          state.to = _parse(t);
          state.anchor = null;
          state.selectedSource = 'timeline';
          syncToInputs();
          render();
          updateMode();
          updateAnchorBar();
          fireOnChange();
        });
      });
    }

    // ────────────────────────────────────────────────────────────────────────
    //   РЕНДЕР БЛОКА «ИСТОЧНИК ПЕРИОДА»
    // ────────────────────────────────────────────────────────────────────────

    function renderSources() {
      if (!config.features.sourcesBlock) return;
      const block = el(`.${P}-sources-block`);
      if (!block) return;

      const sources = config.sources || [];
      if (!sources.length) {
        block.style.display = 'none';
        return;
      }

      // Если источник не выбран, авто-выбираем первый доступный
      if (!state.selectedSource) {
        const firstAvailable = sources.find(s => s.key === 'timeline' || s.key === 'manual' || (s.from && s.to));
        if (firstAvailable) state.selectedSource = firstAvailable.key;
      }

      let html = `<div class="${P}-sources-title">📋 Источник периода</div>`;
      html += `<div class="${P}-sources-list">`;

      for (const src of sources) {
        const isActive = state.selectedSource === src.key;
        const hasDates = src.from && src.to;
        const isDisabled = src.key !== 'timeline' && src.key !== 'manual' && !hasDates;

        let datesHtml;
        if (src.key === 'timeline') {
          const f = state.from ? _ymd(state.from) : null;
          const t = state.to ? _ymd(state.to) : null;
          datesHtml = (f && t)
            ? `<span class="${P}-source-dates has-dates">${f} → ${t}</span>`
            : `<span class="${P}-source-dates">выбрать на шкале ↑</span>`;
        } else if (src.key === 'manual') {
          datesHtml = `<span class="${P}-source-dates">свои даты →</span>`;
        } else if (hasDates) {
          datesHtml = `<span class="${P}-source-dates has-dates">${src.from.slice(0, 10)} → ${src.to.slice(0, 10)}</span>`;
        } else {
          datesHtml = `<span class="${P}-source-dates">— нет данных —</span>`;
        }

        html += `
          <label class="${P}-source-item ${isActive ? 'active' : ''} ${isDisabled ? 'disabled' : ''}"
                 data-source="${_escHtml(src.key)}">
            <input type="radio" name="${P}-source" value="${_escHtml(src.key)}"
                   ${isActive ? 'checked' : ''} ${isDisabled ? 'disabled' : ''}>
            <span class="${P}-source-label">${_escHtml(src.label)}</span>
            ${datesHtml}
          </label>
        `;
      }

      html += '</div>';
      block.innerHTML = html;
      block.style.display = '';

      // Bind source selection
      block.querySelectorAll(`.${P}-source-item`).forEach(item => {
        item.addEventListener('click', ev => {
          if (item.classList.contains('disabled')) return;
          const key = item.dataset.source;
          if (!key) return;

          state.selectedSource = key;
          const src = sources.find(s => s.key === key);

          // Если выбран источник с датами (не timeline и не manual) — устанавливаем период
          if (src && src.key !== 'timeline' && src.key !== 'manual' && src.from && src.to) {
            state.from = _parse(src.from);
            state.to = _parse(src.to);
            state.anchor = null;
            syncToInputs();
            render();
            updateMode();
            updateAnchorBar();
            fireOnChange();
          }

          renderSources();

          // Fire source change callback
          if (typeof config.onSourceChange === 'function') {
            config.onSourceChange(key);
          }
        });
      });
    }

    // ────────────────────────────────────────────────────────────────────────
    //   ANCHOR MODE
    // ────────────────────────────────────────────────────────────────────────

    function setAnchor(date) {
      if (!date) return;
      state.anchor = date;
      state.from = new Date(date);
      state.to = new Date(date);
      state.selectedSource = 'timeline';  // Выбор из таймлайна
      syncToInputs();
      render();
      updateMode();
      updateAnchorBar();
      fireOnChange();
    }

    function shiftFromAnchor(direction, days) {
      if (!state.anchor) return;
      days = parseInt(days, 10) || 0;

      if (direction === 'back') {
        state.from = _addDays(state.anchor, -days);
        if (!state.to) state.to = new Date(state.anchor);
      } else if (direction === 'fwd') {
        state.to = _addDays(state.anchor, days);
        if (!state.from) state.from = new Date(state.anchor);
      }

      // Clamp
      if (state.from && state.earliest && state.from < state.earliest) {
        state.from = new Date(state.earliest);
      }
      if (state.to && state.latest && state.to > state.latest) {
        state.to = new Date(state.latest);
      }

      syncToInputs();
      render();
      updateMode();
      fireOnChange();
    }

    function updateAnchorBar() {
      if (!config.features.anchorMode) return;
      const bar = el(`.${P}-anchor-bar`);
      const lbl = el(`.${P}-anchor-date`);
      if (!bar) return;

      if (state.anchor) {
        bar.style.display = '';
        if (lbl) lbl.textContent = _ymd(state.anchor);
      } else {
        bar.style.display = 'none';
      }
    }

    // ────────────────────────────────────────────────────────────────────────
    //   СИНХРОНИЗАЦИЯ С INPUT ПОЛЯМИ
    // ────────────────────────────────────────────────────────────────────────

    function syncToInputs() {
      if (!config.dateInputs) return;
      const fromEl = config.dateInputs.from ? document.getElementById(config.dateInputs.from) : null;
      const toEl = config.dateInputs.to ? document.getElementById(config.dateInputs.to) : null;
      if (fromEl && state.from) fromEl.value = _ymd(state.from);
      if (toEl && state.to) toEl.value = _ymd(state.to);
    }

    function syncFromInputs() {
      if (!config.dateInputs) return;
      const fromEl = config.dateInputs.from ? document.getElementById(config.dateInputs.from) : null;
      const toEl = config.dateInputs.to ? document.getElementById(config.dateInputs.to) : null;
      state.from = fromEl ? _parse(fromEl.value) : null;
      state.to = toEl ? _parse(toEl.value) : null;
      state.anchor = null;
      render();
      updateMode();
      updateAnchorBar();
      fireOnChange();
    }

    // ────────────────────────────────────────────────────────────────────────
    //   ОБНОВЛЕНИЕ РЕЖИМА
    // ────────────────────────────────────────────────────────────────────────

    function updateMode() {
      const lbl = el(`.${P}-mode`);
      const disp = el(`.${P}-period-display`);
      if (!lbl) return;

      if (state.from && state.to) {
        const days = _dayDiff(state.from, state.to) + 1;
        lbl.textContent = 'выбран диапазон';
        if (disp) disp.textContent = `${_ymd(state.from)} → ${_ymd(state.to)} · ${days} дн.`;
      } else if (state.from) {
        lbl.textContent = 'задана левая граница';
        if (disp) disp.textContent = `${_ymd(state.from)} → …`;
      } else {
        lbl.textContent = 'не выбран';
        if (disp) disp.textContent = '';
      }
    }

    // ────────────────────────────────────────────────────────────────────────
    //   CALLBACK
    // ────────────────────────────────────────────────────────────────────────

    function fireOnChange() {
      if (config.onChange && state.from && state.to) {
        config.onChange(_ymd(state.from), _ymd(state.to));
      }
    }

    // ────────────────────────────────────────────────────────────────────────
    //   ПРИВЯЗКА ОБРАБОТЧИКОВ (один раз)
    // ────────────────────────────────────────────────────────────────────────

    function bindHandlers() {
      const scale = el(`.${P}-scale`);
      if (!scale) return;

      // Клик по шкале
      addListener(scale, 'click', ev => {
        // Игнорируем клики по этапам (у них свои обработчики)
        if (ev.target.closest(`.${P}-stage-dot`)) return;
        if (ev.target.closest(`.${P}-stage-band`)) return;
        if (!state.earliest || !state.latest) return;

        const rect = scale.getBoundingClientRect();
        const p = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
        const ms = state.earliest.getTime() + p * (state.latest - state.earliest);
        const d = new Date(ms);
        d.setHours(0, 0, 0, 0);
        setAnchor(d);
      });

      // Zoom кнопки
      const container = getContainer();
      container.querySelectorAll(`[data-zoom]`).forEach(btn => {
        addListener(btn, 'click', () => {
          state.zoom = btn.dataset.zoom;
          container.querySelectorAll(`[data-zoom]`).forEach(b => {
            b.classList.toggle('active', b === btn);
          });
          render();
        });
      });

      // Сброс
      const clearBtn = el(`.${P}-clear-btn`);
      addListener(clearBtn, 'click', () => {
        state.from = null;
        state.to = null;
        state.anchor = null;
        syncToInputs();
        render();
        updateMode();
        updateAnchorBar();
        if (config.onChange) config.onChange(null, null);
      });

      // Shift кнопки
      if (config.features.anchorMode) {
        container.querySelectorAll(`[data-shift]`).forEach(btn => {
          addListener(btn, 'click', () => {
            shiftFromAnchor(btn.dataset.shift, btn.dataset.days);
          });
        });

        const customDays = el(`.${P}-custom-days`);
        const customBack = el(`.${P}-custom-back`);
        const customFwd = el(`.${P}-custom-fwd`);

        const doCustom = (dir) => {
          const n = parseInt(customDays?.value || '0', 10);
          if (!n || n <= 0) {
            customDays?.focus();
            return;
          }
          shiftFromAnchor(dir, n);
        };

        addListener(customBack, 'click', () => doCustom('back'));
        addListener(customFwd, 'click', () => doCustom('fwd'));
        addListener(customDays, 'keydown', ev => {
          if (ev.key === 'Enter') doCustom('fwd');
        });
      }

      // Синхронизация с input полями
      if (config.dateInputs) {
        const fromEl = config.dateInputs.from ? document.getElementById(config.dateInputs.from) : null;
        const toEl = config.dateInputs.to ? document.getElementById(config.dateInputs.to) : null;
        if (fromEl) addListener(fromEl, 'change', syncFromInputs);
        if (toEl) addListener(toEl, 'change', syncFromInputs);
      }
    }

    // ────────────────────────────────────────────────────────────────────────
    //   ПУБЛИЧНЫЙ ИНТЕРФЕЙС ЭКЗЕМПЛЯРА
    // ────────────────────────────────────────────────────────────────────────

    return {
      async init() {
        renderStructure();
        await loadData();
        render();
        bindHandlers();
        updateMode();
        updateAnchorBar();
      },

      render() {
        render();
      },

      getSelection() {
        return {
          from: state.from ? _ymd(state.from) : null,
          to: state.to ? _ymd(state.to) : null,
        };
      },

      setSelection(from, to) {
        state.from = _parse(from);
        state.to = _parse(to);
        state.anchor = null;
        syncToInputs();
        render();
        updateMode();
        updateAnchorBar();
        fireOnChange();
      },

      // ── Управление источниками ──

      /**
       * Обновить список источников периода.
       * @param {Array} sources - массив объектов { key, label, from, to }
       */
      setSources(sources) {
        config.sources = sources || [];
        renderSources();
      },

      /**
       * Получить ключ выбранного источника.
       * @returns {string|null}
       */
      getSelectedSource() {
        return state.selectedSource;
      },

      /**
       * Установить источник (key) и применить его период (если есть даты).
       * @param {string} key - ключ источника
       */
      setSelectedSource(key) {
        state.selectedSource = key;
        const src = (config.sources || []).find(s => s.key === key);
        if (src && src.key !== 'timeline' && src.key !== 'manual' && src.from && src.to) {
          state.from = _parse(src.from);
          state.to = _parse(src.to);
          state.anchor = null;
          syncToInputs();
          render();
          updateMode();
          updateAnchorBar();
          fireOnChange();
        }
        renderSources();
      },

      destroy() {
        state.listeners.forEach(({ el, ev, fn }) => {
          el.removeEventListener(ev, fn);
        });
        state.listeners = [];
        const container = getContainer();
        if (container) container.innerHTML = '';
      },

      // Для отладки
      _getState() {
        return state;
      },
    };
  }

  // ══════════════════════════════════════════════════════════════════════════
  //   ПУБЛИЧНЫЙ API
  // ══════════════════════════════════════════════════════════════════════════

  window.TimelineBuilder = {
    /**
     * Создать экземпляр TimelineBuilder.
     *
     * @param {Object} config
     * @param {string} config.containerId - ID контейнера (обязательно)
     * @param {number} config.wellId - ID скважины (обязательно)
     * @param {Object} [config.dateInputs] - { from: 'input-id', to: 'input-id' }
     * @param {Function} [config.onChange] - callback(from, to)
     * @param {Function} [config.onLoad] - callback(data)
     * @param {Object} [config.features] - включение/выключение функций
     * @param {string} [config.apiEndpoint] - endpoint API
     * @param {string} [config.cssPrefix] - префикс CSS классов
     * @returns {Object|null} экземпляр или null при ошибке
     */
    create(config) {
      if (!config || !config.containerId) {
        console.error('[TimelineBuilder] containerId is required');
        return null;
      }
      if (!config.wellId) {
        console.error('[TimelineBuilder] wellId is required');
        return null;
      }
      return createInstance(config);
    },
  };

})();
