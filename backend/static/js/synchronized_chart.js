/**
 * synchronized_chart.js — Синхронизированный график: Давление LoRa + События Telegram
 *
 * Наложение кривых давления (p_tube, p_line) и маркеров событий (продувки, реагент, замеры)
 * на одну временную шкалу. Позволяет визуально видеть корреляции:
 * продувка → падение давления, вброс реагента → изменение кривой.
 *
 * Данные:
 *   - Давление: GET /api/pressure/chart/{well_id}?days=N&interval=M
 *   - События:  window.wellEventsFiltered (отфильтрованные координатором chart_sync.js)
 *
 * Используется на странице well.html
 */

(function () {
  'use strict';

  console.log('[sync_chart] Script loaded, version 24');

  const canvas = document.getElementById('chart_synchronized');
  if (!canvas) {
    console.log('[sync_chart] Canvas not found');
    return;
  }

  const wellId = canvas.dataset.wellId;
  if (!wellId) {
    console.log('[sync_chart] Well ID not found');
    return;
  }

  console.log('[sync_chart] Initializing for well:', wellId);
  console.log('[sync_chart] wellEventsData available:', !!window.wellEventsData);
  if (window.wellEventsData) {
    console.log('[sync_chart] Events count:', (window.wellEventsData.timelineEvents || []).length);
    console.log('[sync_chart] Injections count:', (window.wellEventsData.timelineInjections || []).length);
  }

  let syncChart = null;
  let currentDays = 7;
  let currentInterval = 5;
  let zoomHistory = [];
  let panMode = false;
  window._syncPanMode = false;

  // ══════════════════ Чувствительность захвата событий ══════════════════
  let eventSensitivity = 60;  // пикселей — радиус захвата событий от курсора

  // ══════════════════ Блокировка tooltip и режим выбора диапазона ══════════════════
  let tooltipLocked = false;  // если true — tooltip не обновляется при движении мыши

  // Режимы курсора: 'normal' → 'locked' → 'range_start' → 'range_end'
  let cursorMode = 'normal';
  let rangeStart = null;  // timestamp начала диапазона
  let rangeEnd = null;    // timestamp конца диапазона
  let lockedXValue = null; // зафиксированная позиция курсора на графике

  // ══════════════════ Цвета давления ══════════════════
  const COLORS = {
    tube:     '#e53935',
    tubeFill: 'rgba(229, 57, 53, 0.08)',
    line:     '#1e88e5',
    lineFill: 'rgba(30, 136, 229, 0.08)',
    grid:     '#e9ecef',
  };

  // ══════════════════ Константы событий ══════════════════
  const EVENT_Y = {
    pressure: 0.90,
    purge:    0.70,
    reagent:  0.50,
    equip:    0.30,
    note:     0.15,
    other:    0.05,
  };

  const EVENT_LABELS = {
    pressure: 'Замер давления',
    purge:    'Продувка',
    reagent:  'Реагент',
    equip:    'Оборудование',
    note:     'Заметка',
    other:    'Другое',
  };

  const EVENT_ICONS = {
    pressure: '📊',
    purge:    '💨',
    reagent:  '💉',
    equip:    '🔧',
    note:     '📝',
    other:    '📌',
  };

  function getReagentMarkerSize() {
    return parseInt(localStorage.getItem('reagentMarkerSize') || '8', 10);
  }

  // ══════════════════ Custom interaction mode: syncNearest ══════════════════
  // Показывает в тултипе ВСЕ данные одновременно (давление + события рядом).
  // - Давление (yAxisID: 'y'): ближайшая 1 точка по X на каждый dataset
  // - События/реагенты: ВСЕ точки в радиусе eventSensitivity пикселей по X
  Chart.Interaction.modes.syncNearest = function (chart, e, options, useFinalPosition) {
    const items = [];
    const xPixel = e.x;
    // Используем переменную чувствительности вместо константы
    const threshold = eventSensitivity || 60;

    // Давление: ближайшая 1 точка по X на каждый dataset
    chart.data.datasets.forEach(function (dataset, datasetIndex) {
      if (dataset.yAxisID !== 'y') return;
      var meta = chart.getDatasetMeta(datasetIndex);
      if (!meta || meta.hidden) return;
      var bestIdx = -1, bestDist = Infinity;
      meta.data.forEach(function (el, idx) {
        var d = Math.abs(el.x - xPixel);
        if (d < bestDist) { bestDist = d; bestIdx = idx; }
      });
      if (bestIdx >= 0) {
        items.push({ element: meta.data[bestIdx], datasetIndex: datasetIndex, index: bestIdx });
      }
    });

    // События/реагенты: все точки в радиусе threshold (настраивается слайдером)
    chart.data.datasets.forEach(function (dataset, datasetIndex) {
      if (dataset.yAxisID === 'y') return;
      var meta = chart.getDatasetMeta(datasetIndex);
      if (!meta || meta.hidden) return;
      meta.data.forEach(function (el, idx) {
        if (Math.abs(el.x - xPixel) <= threshold) {
          items.push({ element: el, datasetIndex: datasetIndex, index: idx });
        }
      });
    });

    return items;
  };

  // ══════════════════ Интерполяция давления ══════════════════
  // Вычисляет значение давления в произвольной точке времени (линейная интерполяция).
  // Используется в тултипе: при наведении на событие показываем давление в этот момент.
  function interpolatePressureAtTime(chart, targetMs) {
    var results = [];
    chart.data.datasets.forEach(function (dataset) {
      if (dataset.yAxisID !== 'y') return;
      var data = dataset.data;
      if (!data || data.length < 2) return;
      // Бинарный поиск двух окружающих точек
      var lo = 0, hi = data.length - 1;
      while (lo < hi - 1) {
        var mid = (lo + hi) >> 1;
        if (new Date(data[mid].x).getTime() <= targetMs) lo = mid;
        else hi = mid;
      }
      var t0 = new Date(data[lo].x).getTime();
      var t1 = new Date(data[hi].x).getTime();
      var ratio = (t1 === t0) ? 0 : (targetMs - t0) / (t1 - t0);
      var clampedRatio = Math.max(0, Math.min(1, ratio));
      results.push({
        label: dataset.label,
        value: data[lo].y + clampedRatio * (data[hi].y - data[lo].y),
      });
    });
    return results;
  }

  // ══════════════════ Плагин: вертикальные линии событий ══════════════════
  // Рисует пунктирные вертикальные линии ТОЛЬКО для datasets с _isEventLine: true
  // (события: продувки, замеры, оборудование и т.д.)
  // Вбросы реагента (_isEventLine: false) отображаются как обычные scatter-точки.
  const verticalEventLinePlugin = {
    id: 'syncVerticalLines',
    afterDatasetsDraw(chart) {
      const ctx = chart.ctx;
      const yAxis = chart.scales.y;
      const xAxis = chart.scales.x;
      if (!yAxis || !xAxis) return;

      chart.data.datasets.forEach((dataset, dsIndex) => {
        // Рисуем линии только для событий (не реагентов)
        if (!dataset._isEventLine) return;

        const meta = chart.getDatasetMeta(dsIndex);
        if (!meta || meta.hidden) return;

        meta.data.forEach((point) => {
          const x = point.x;
          // Проверяем по горизонтальным границам X-оси (не Y-оси!)
          if (isNaN(x) || x < xAxis.left || x > xAxis.right) return;

          ctx.save();
          ctx.strokeStyle = dataset.borderColor || '#999';
          ctx.lineWidth = 2;
          ctx.setLineDash([6, 4]);
          ctx.globalAlpha = 0.55;

          ctx.beginPath();
          ctx.moveTo(x, yAxis.top);
          ctx.lineTo(x, yAxis.bottom);
          ctx.stroke();

          ctx.restore();
        });
      });
    },
  };

  // ══════════════════ Плагин: области продувок (start → stop) ══════════════════
  // Рисует полупрозрачные прямоугольники между событиями start и stop продувки.
  // Также вычисляет длительность продувки и сохраняет в eventData.
  const purgeDurationPlugin = {
    id: 'purgeDurationAreas',
    beforeDatasetsDraw(chart) {
      const ctx = chart.ctx;
      const xAxis = chart.scales.x;
      const yAxis = chart.scales.y;
      if (!xAxis || !yAxis) return;

      // Собираем все события продувки из datasets
      const purgeEvents = [];
      chart.data.datasets.forEach((ds) => {
        if (!ds._isEventLine) return;
        if (!ds.label || !ds.label.includes('Продувка')) return;

        ds.data.forEach((pt) => {
          if (!pt.eventData) return;
          const phase = (pt.eventData.purge_phase || '').toLowerCase().trim();
          if (phase === 'start' || phase === 'stop' || phase === 'press') {
            purgeEvents.push({
              time: new Date(pt.x).getTime(),
              phase: phase,
              color: ds.borderColor || '#ffa000',
              eventData: pt.eventData,
            });
          }
        });
      });

      if (purgeEvents.length === 0) return;

      // Сортируем по времени
      purgeEvents.sort((a, b) => a.time - b.time);

      // Находим пары start → stop (или start → press → stop)
      let startEvent = null;
      const pairs = [];

      for (const ev of purgeEvents) {
        if (ev.phase === 'start') {
          startEvent = ev;
        } else if (ev.phase === 'stop' && startEvent) {
          // Вычисляем длительность в минутах
          const durationMs = ev.time - startEvent.time;
          const durationMin = Math.round(durationMs / 60000);

          // Сохраняем длительность в eventData обоих событий
          startEvent.eventData.purge_duration_min = durationMin;
          ev.eventData.purge_duration_min = durationMin;

          pairs.push({
            startTime: startEvent.time,
            stopTime: ev.time,
            durationMin: durationMin,
            color: startEvent.color,
          });

          startEvent = null;
        }
      }

      // Рисуем полупрозрачные области
      for (const pair of pairs) {
        const x1 = xAxis.getPixelForValue(pair.startTime);
        const x2 = xAxis.getPixelForValue(pair.stopTime);

        // Проверяем что область видима на графике
        if (x2 < xAxis.left || x1 > xAxis.right) continue;

        const clampedX1 = Math.max(x1, xAxis.left);
        const clampedX2 = Math.min(x2, xAxis.right);

        ctx.save();

        // Полупрозрачный прямоугольник
        ctx.fillStyle = pair.color.replace(')', ', 0.12)').replace('rgb(', 'rgba(');
        if (!ctx.fillStyle.includes('rgba')) {
          // Если цвет в hex формате
          ctx.fillStyle = hexToRgba(pair.color, 0.12);
        }
        ctx.fillRect(clampedX1, yAxis.top, clampedX2 - clampedX1, yAxis.bottom - yAxis.top);

        // Метка с длительностью сверху
        if (pair.durationMin > 0 && (clampedX2 - clampedX1) > 30) {
          const centerX = (clampedX1 + clampedX2) / 2;
          ctx.fillStyle = pair.color;
          ctx.font = 'bold 10px sans-serif';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'top';
          ctx.fillText(`${pair.durationMin} мин`, centerX, yAxis.top + 4);
        }

        ctx.restore();
      }
    },
  };

  /** Конвертирует HEX цвет в RGBA */
  function hexToRgba(hex, alpha) {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    if (!result) return `rgba(255, 160, 0, ${alpha})`; // fallback оранжевый
    const r = parseInt(result[1], 16);
    const g = parseInt(result[2], 16);
    const b = parseInt(result[3], 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  // ══════════════════ Утилиты ══════════════════

  /** Собирает параметры фильтров из СОБСТВЕННОЙ панели фильтров (sync-filter-*) */
  function getFilterParams() {
    const zeros  = document.getElementById('sync-filter-zeros');
    const spikes = document.getElementById('sync-filter-spikes');
    const fill   = document.getElementById('sync-filter-fill-mode');
    const gap    = document.getElementById('sync-filter-max-gap');

    let s = '';
    if (zeros  && zeros.checked)  s += '&filter_zeros=true';
    if (spikes && spikes.checked) s += '&filter_spikes=true';
    if (fill   && fill.value !== 'none') s += `&fill_mode=${fill.value}`;
    if (gap)   s += `&max_gap=${gap.value}`;
    return s;
  }

  /** Нормализация таймстемпа: убираем TZ суффикс (+00:00, Z) и обрезаем
   *  микросекунды до миллисекунд, чтобы Luxon корректно парсил строку.
   *  PostgreSQL event_time.isoformat() даёт 6 дробных цифр ("...53.414996"),
   *  а Luxon поддерживает максимум 3 ("...53.414"). */
  function normalizeTime(t) {
    if (!t) return t;
    // 1) Убираем TZ суффиксы (+00:00, Z)
    t = t.replace(/[+-]\d{2}:\d{2}$/, '').replace(/Z$/, '');
    // 2) Обрезаем микросекунды до миллисекунд (Luxon поддерживает max 3 дробных цифры)
    //    "2026-02-08T09:01:53.414996" → "2026-02-08T09:01:53.414"
    t = t.replace(/(\.\d{3})\d+$/, '$1');
    return t;
  }

  /** Возвращает список видимых типов событий из чекбоксов */
  function getVisibleEventTypes() {
    const checks = document.querySelectorAll('.sync-event-filter:checked');
    if (checks.length === 0) {
      // Если чекбоксов ещё нет (первая загрузка) — показываем всё
      return Object.keys(EVENT_Y);
    }
    return Array.from(checks).map(c => c.value);
  }

  // ══════════════════ Генерация чекбоксов событий ══════════════════

  function createEventFilters() {
    const container = document.getElementById('sync-event-filters');
    if (!container) {
      console.log('[sync_chart] sync-event-filters container not found');
      return;
    }

    const evData = window.wellEventsData || {};
    const eventColors = evData.eventColors || {};
    const reagentColors = evData.reagentColors || {};

    // Определяем какие типы событий реально есть в данных
    const presentTypes = new Set();
    (evData.timelineEvents || []).forEach(ev => {
      presentTypes.add((ev.type || 'other').toLowerCase());
    });
    if ((evData.timelineInjections || []).length > 0) {
      presentTypes.add('reagent');
    }

    console.log('[sync_chart] Present event types:', Array.from(presentTypes));
    console.log('[sync_chart] Event colors:', eventColors);
    console.log('[sync_chart] Reagent colors:', reagentColors);

    // Создаём чекбоксы для каждого типа
    const allTypes = ['pressure', 'purge', 'reagent', 'equip', 'note', 'other'];
    allTypes.forEach(type => {
      if (!presentTypes.has(type)) return;

      const color = type === 'reagent'
        ? (Object.values(reagentColors)[0] || '#e53935')
        : (eventColors[type] || '#9e9e9e');

      const label = document.createElement('label');
      label.style.cssText = 'font-size:12px; display:flex; align-items:center; gap:4px; cursor:pointer;';
      label.innerHTML = `
        <input type="checkbox" class="sync-event-filter" value="${type}" checked
          style="accent-color:${color};">
        <span style="
          display:inline-block; width:10px; height:10px;
          background:${color}; border-radius:${type === 'reagent' ? '0' : '50%'};
          ${type === 'reagent' ? 'clip-path:polygon(50% 0%, 0% 100%, 100% 100%);' : ''}
        "></span>
        ${EVENT_ICONS[type] || '📌'} ${EVENT_LABELS[type] || type}
      `;
      container.appendChild(label);
    });

    // Слушаем изменения
    container.addEventListener('change', () => {
      loadChart();
    });
  }

  // ══════════════════ Построение датасетов ══════════════════

  function buildDatasets(points, evData, visibleTypes) {
    const ds = [];

    // ── Определяем временной диапазон давления ──
    // Ось X должна соответствовать данным давления, а НЕ расширяться из-за событий за пределами.
    // Нормализуем timestamps давления через normalizeTime() для согласованности с событиями.
    let pressureTimeMin = null;
    let pressureTimeMax = null;
    for (const p of points) {
      if (!p.t) continue;
      p.t = normalizeTime(p.t);  // нормализуем in-place (обрезка микросекунд, TZ суффиксов)
      if (pressureTimeMin === null || p.t < pressureTimeMin) pressureTimeMin = p.t;
      if (pressureTimeMax === null || p.t > pressureTimeMax) pressureTimeMax = p.t;
    }

    // ── 1) Кривая Ptr (устье) ──
    const tubeData = [];
    for (const p of points) {
      if (p.p_tube_avg !== null && p.p_tube_avg !== undefined) {
        tubeData.push({ x: p.t, y: p.p_tube_avg });
      }
    }
    ds.push({
      label: 'Ptr (устье)',
      data: tubeData,
      borderColor: COLORS.tube,
      backgroundColor: COLORS.tubeFill,
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 4,
      tension: 0.3,
      fill: false,
      yAxisID: 'y',
      order: 1,
    });

    // ── 2) Кривая Pshl (шлейф) ──
    const lineData = [];
    for (const p of points) {
      if (p.p_line_avg !== null && p.p_line_avg !== undefined) {
        lineData.push({ x: p.t, y: p.p_line_avg });
      }
    }
    ds.push({
      label: 'Pshl (шлейф)',
      data: lineData,
      borderColor: COLORS.line,
      backgroundColor: COLORS.lineFill,
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 4,
      tension: 0.3,
      fill: false,
      yAxisID: 'y',
      order: 2,
    });

    // ── Диагностика: сравниваем диапазоны давления и событий ──
    const eventColors = evData.eventColors || {};
    const reagentColors = evData.reagentColors || {};

    if (points.length > 0) {
      console.log('[sync_chart] pressure range:', points[0].t, '→', points[points.length - 1].t);
    }
    const _allEvts = [...(evData.timelineEvents || []), ...(evData.timelineInjections || [])];
    if (_allEvts.length > 0) {
      const _sorted = _allEvts.filter(e => e.t).sort((a, b) => a.t.localeCompare(b.t));
      console.log('[sync_chart] events range:', _sorted[0].t, '→', _sorted[_sorted.length - 1].t);
      console.log('[sync_chart] total events:', _allEvts.length);
    } else {
      console.log('[sync_chart] no events in wellEventsFiltered');
    }

    // Подсчёт по типам
    const totalByType = {};
    (evData.timelineEvents || []).forEach(ev => {
      if (!ev.t) return;
      const type = (ev.type || 'other').toLowerCase();
      totalByType[type] = (totalByType[type] || 0) + 1;
    });
    console.log('[sync_chart] events by type:', totalByType);

    // ── 3) События (не-reagent) → вертикальные линии (без видимых точек) ──
    // Точки невидимы (pointRadius: 0), но нужны для тултипов (pointHoverRadius: 15).
    // Плагин verticalEventLinePlugin рисует пунктирные вертикальные линии.
    // События уже отфильтрованы координатором chart_sync.js по дням.
    // Не применяем дополнительный фильтр isInPressureRange — он мог отсекать события.
    const eventsByType = {};
    let skippedNoTime = 0;
    let skippedNotVisible = 0;
    (evData.timelineEvents || []).forEach(ev => {
      if (!ev.t) {
        skippedNoTime++;
        return;
      }
      const type = (ev.type || 'other').toLowerCase();
      if (!visibleTypes.includes(type)) {
        skippedNotVisible++;
        return;
      }
      if (!eventsByType[type]) eventsByType[type] = [];
      eventsByType[type].push(ev);
    });

    console.log('[sync_chart] buildDatasets: events by type:',
      Object.entries(eventsByType).map(([t, arr]) => `${t}:${arr.length}`).join(', ') || 'none',
      '| skipped (no time):', skippedNoTime,
      '| skipped (not visible):', skippedNotVisible,
      '| visibleTypes:', visibleTypes);

    Object.entries(eventsByType).forEach(([type, events]) => {
      const color = eventColors[type] || '#9e9e9e';
      console.log(`[sync_chart] Adding ${type} events: ${events.length} points, color: ${color}, Y: ${EVENT_Y[type] || 0.5}`);
      if (events.length > 0) {
        console.log(`[sync_chart] First ${type} event:`, events[0].t, events[0].type || events[0].description);
      }
      ds.push({
        label: `${EVENT_ICONS[type] || '📌'} ${EVENT_LABELS[type] || type}`,
        type: 'scatter',
        data: events.map(ev => ({
          x: normalizeTime(ev.t),
          y: EVENT_Y[type] || 0.5,   // разная высота для разных типов событий
          eventData: ev,
        })),
        backgroundColor: color,
        borderColor: color,
        borderWidth: 2,
        pointRadius: 0,          // Без маркеров — только вертикальные линии
        pointHoverRadius: 8,
        pointHitRadius: 30,      // Увеличен для лучшего захвата
        yAxisID: 'y_events',
        order: 10,
        _isEventLine: true,      // флаг для плагина вертикальных линий
      });
    });

    // ── 4) Вбросы реагента → видимые точки (scatter circles) ──
    // Реагенты отображаются на отдельной правой оси y_reagent с реальным количеством (qty).
    let maxQty = 0;
    console.log('[sync_chart] Processing injections, reagent visible:', visibleTypes.includes('reagent'),
      '| injections count:', (evData.timelineInjections || []).length);
    if (visibleTypes.includes('reagent')) {
      const injByReagent = {};
      (evData.timelineInjections || []).forEach(inj => {
        if (!inj.t) return;
        const name = inj.reagent || 'Реагент';
        if (!injByReagent[name]) injByReagent[name] = [];
        injByReagent[name].push(inj);
        const q = parseFloat(inj.qty) || 0;
        if (q > maxQty) maxQty = q;
      });
      console.log('[sync_chart] Injections by reagent:', Object.entries(injByReagent).map(([n, arr]) => `${n}:${arr.length}`).join(', '));

      Object.entries(injByReagent).forEach(([name, injs]) => {
        const color = reagentColors[name] || '#e53935';
        ds.push({
          label: `💉 ${name}`,
          type: 'scatter',
          data: injs.map(inj => ({
            x: normalizeTime(inj.t),
            y: parseFloat(inj.qty) || 0,   // реальное количество реагента
            qty: inj.qty,
            eventData: inj,
          })),
          backgroundColor: color,
          borderColor: color,
          borderWidth: 2,
          pointRadius: getReagentMarkerSize(),
          pointHoverRadius: getReagentMarkerSize() + 4,
          pointHitRadius: 25,
          pointStyle: 'circle',        // круги для реагентов
          yAxisID: 'y_reagent',
          order: 11,
          _isEventLine: false,   // без вертикальных линий
        });
      });
    }

    console.log('[sync_chart] Total datasets:', ds.length,
      '| pressure curves:', ds.filter(d => d.yAxisID === 'y').length,
      '| event datasets:', ds.filter(d => d.yAxisID === 'y_events').length,
      '| reagent datasets:', ds.filter(d => d.yAxisID === 'y_reagent').length);

    return { datasets: ds, pressureTimeMin, pressureTimeMax, maxQty };
  }

  // ══════════════════ Внешний Tooltip (правая панель) ══════════════════

  /**
   * Обновляет внешнюю панель tooltip справа от графика.
   * Показывает: время, Ptr, Pshl, ΔP, и ВСЕ события рядом (как раньше).
   */
  function updateExternalTooltip(context) {
    // Если tooltip заблокирован — не обновляем (для прокрутки событий)
    if (tooltipLocked) return;

    const tooltipModel = context.tooltip;

    // Элементы панели
    const timeEl = document.getElementById('sync-tooltip-time');
    const ptrEl = document.getElementById('sync-tooltip-ptr');
    const pshlEl = document.getElementById('sync-tooltip-pshl');
    const deltaEl = document.getElementById('sync-tooltip-delta');
    const eventsContainer = document.getElementById('sync-tooltip-events-container');
    const eventsCount = document.getElementById('sync-tooltip-events-count');
    const eventsList = document.getElementById('sync-tooltip-events-list');

    if (!timeEl) return;

    // Если нет данных — не очищаем, оставляем последние значения
    if (tooltipModel.opacity === 0 || !tooltipModel.dataPoints?.length) {
      return;
    }

    // ── Время ──
    const raw = tooltipModel.dataPoints[0].raw;
    if (raw && raw.x) {
      const d = new Date(raw.x);
      const dd = String(d.getDate()).padStart(2, '0');
      const mm = String(d.getMonth() + 1).padStart(2, '0');
      const hh = String(d.getHours()).padStart(2, '0');
      const mi = String(d.getMinutes()).padStart(2, '0');
      timeEl.textContent = `${dd}.${mm} ${hh}:${mi}`;
    }

    // ── Значения давления и ВСЕ события ──
    let ptr = null, pshl = null;
    const foundEvents = [];  // Собираем ВСЕ события

    for (const dp of tooltipModel.dataPoints) {
      const dsLabel = dp.dataset.label || '';
      const yAxisID = dp.dataset.yAxisID;

      // Давление
      if (yAxisID === 'y') {
        if (dsLabel.includes('Ptr')) ptr = dp.parsed.y;
        if (dsLabel.includes('Pshl')) pshl = dp.parsed.y;
      }

      // Событие или реагент — собираем ВСЕ (с цветом маркера!)
      if ((yAxisID === 'y_events' || yAxisID === 'y_reagent') && dp.raw?.eventData) {
        foundEvents.push({
          data: dp.raw.eventData,
          type: dp.raw.eventData.type || (yAxisID === 'y_reagent' ? 'reagent' : 'other'),
          label: dsLabel,
          qty: dp.raw.qty,
          markerColor: dp.dataset.borderColor || dp.dataset.backgroundColor || '#6c757d',
        });
      }
    }

    // Если нет прямых данных давления — интерполируем
    if (ptr === null && pshl === null && raw && raw.x) {
      const targetMs = new Date(raw.x).getTime();
      if (!isNaN(targetMs)) {
        const interp = interpolatePressureAtTime(syncChart, targetMs);
        interp.forEach(p => {
          if (p.label.includes('Ptr')) ptr = p.value;
          if (p.label.includes('Pshl')) pshl = p.value;
        });
      }
    }

    // Обновляем панель давления
    ptrEl.textContent = ptr !== null ? ptr.toFixed(2) + ' атм' : '—';
    pshlEl.textContent = pshl !== null ? pshl.toFixed(2) + ' атм' : '—';
    deltaEl.textContent = (ptr !== null && pshl !== null) ? (ptr - pshl).toFixed(2) + ' атм' : '—';

    // ── События (все найденные) ──
    if (foundEvents.length > 0 && eventsContainer && eventsList) {
      eventsContainer.style.display = 'block';
      eventsCount.textContent = `(${foundEvents.length})`;

      // Иконки и лейблы
      const icons = {
        pressure: '📊', purge: '💨', reagent: '💉',
        equip: '🔧', note: '📝', other: '📌',
      };
      const labels = {
        pressure: 'Замер давления', purge: 'Продувка', reagent: 'Реагент',
        equip: 'Оборудование', note: 'Заметка', other: 'Событие',
      };

      // Словарь фаз продувки
      const purgePhaseLabels = {
        'start': '🟢 Начало продувки',
        'press': '🔵 Набор давления',
        'stop': '🔴 Пуск в линию',
      };

      // Генерируем HTML для каждого события — ПОЛНАЯ информация
      eventsList.innerHTML = foundEvents.map(found => {
        const ev = found.data;
        const evType = (found.type || 'other').toLowerCase();
        const icon = icons[evType] || '📌';
        const typeLabel = labels[evType] || evType;
        // Используем цвет маркера с графика!
        const borderColor = found.markerColor || '#6c757d';

        // Точное время события
        let timeStr = '';
        if (ev.t) {
          const d = new Date(ev.t);
          const dd = String(d.getDate()).padStart(2, '0');
          const mm = String(d.getMonth() + 1).padStart(2, '0');
          const hh = String(d.getHours()).padStart(2, '0');
          const mi = String(d.getMinutes()).padStart(2, '0');
          timeStr = `${dd}.${mm} ${hh}:${mi}`;
        }

        // Полное описание (без обрезки)
        const desc = ev.description || '';

        // Реагент (с цветом маркера)
        const reagentName = ev.reagent || '';

        // Количество
        const qty = found.qty || ev.qty;
        const qtyStr = qty && parseFloat(qty) > 0 ? `${qty}` : '';

        // Давления из события
        const pTube = ev.p_tube !== null && ev.p_tube !== undefined ? ev.p_tube : null;
        const pLine = ev.p_line !== null && ev.p_line !== undefined ? ev.p_line : null;

        // Фаза продувки с переводом
        const rawPhase = (ev.purge_phase || '').toLowerCase().trim();
        const purgePhaseText = purgePhaseLabels[rawPhase] || (ev.purge_phase ? `⚡ ${ev.purge_phase}` : '');

        // Длительность продувки (если есть)
        const purgeDuration = ev.purge_duration_min ? `⏱ ${ev.purge_duration_min} мин` : '';

        // Оператор
        const operator = ev.operator || ev.username || '';

        // Геостатус
        const geoStatus = ev.geo_status || '';

        return `
          <div style="padding:6px 8px; background:#fff; border-radius:6px; border-left:4px solid ${borderColor}; box-shadow:0 1px 3px rgba(0,0,0,0.08);">
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:4px;">
              <div style="display:flex; align-items:center; gap:6px;">
                <span style="font-size:16px;">${icon}</span>
                <span style="font-size:12px; font-weight:700; color:${borderColor};">${typeLabel}</span>
              </div>
              ${timeStr ? `<span style="font-size:11px; color:#666; font-weight:500;">🕐 ${timeStr}</span>` : ''}
            </div>
            ${reagentName ? `<div style="font-size:12px; color:${borderColor}; font-weight:600; margin-bottom:2px;">💉 ${reagentName}${qtyStr ? ` — ${qtyStr}` : ''}</div>` : ''}
            ${purgePhaseText ? `<div style="font-size:11px; color:${borderColor}; font-weight:600; margin-bottom:2px;">${purgePhaseText}</div>` : ''}
            ${purgeDuration ? `<div style="font-size:11px; color:#666; margin-bottom:2px;">${purgeDuration}</div>` : ''}
            ${desc ? `<div style="font-size:11px; color:#333; line-height:1.4; margin-bottom:2px;">${desc}</div>` : ''}
            ${(pTube !== null || pLine !== null) ? `<div style="font-size:11px; color:#555;">${pTube !== null ? `<span style="color:#e53935;">Ptr: ${pTube}</span>` : ''}${pTube !== null && pLine !== null ? ' • ' : ''}${pLine !== null ? `<span style="color:#1e88e5;">Pshl: ${pLine}</span>` : ''}</div>` : ''}
            ${operator ? `<div style="font-size:10px; color:#888; margin-top:2px;">👤 ${operator}</div>` : ''}
            ${geoStatus && geoStatus !== 'Не указан' ? `<div style="font-size:10px; color:#888;">📍 ${geoStatus}</div>` : ''}
          </div>
        `;
      }).join('');

    } else if (eventsContainer) {
      eventsContainer.style.display = 'none';
    }
  }

  // ══════════════════ Рендеринг графика ══════════════════

  function renderChart(datasets, pressureTimeMin, pressureTimeMax, maxQty) {
    console.log('[sync_chart] Rendering chart with', datasets.length, 'datasets');
    console.log('[sync_chart] X axis range:', pressureTimeMin, '→', pressureTimeMax);

    if (syncChart) {
      syncChart.destroy();
      syncChart = null;
    }

    syncChart = new Chart(canvas, {
      type: 'line',
      data: { datasets },
      plugins: [purgeDurationPlugin, verticalEventLinePlugin],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 400 },
        interaction: {
          mode: 'syncNearest',
          intersect: false,
        },
        plugins: {
          legend: {
            display: false,  // Используем кастомную легенду
          },
          tooltip: {
            enabled: false,  // Отключаем встроенный tooltip
            external: function(context) {
              updateExternalTooltip(context);
            },
          },
          zoom: {
            pan: {
              enabled: true,   // Always true so Hammer.js registers pan recognizers
              mode: 'xy',
              threshold: 5,
              onPanComplete: function ({ chart }) {
                syncZoomToDelta(chart);
              },
            },
            zoom: {
              drag: {
                enabled: !panMode,  // In default mode drag-zoom takes priority over pan
                backgroundColor: 'rgba(13, 110, 253, 0.1)',
                borderColor: 'rgba(13, 110, 253, 0.4)',
                borderWidth: 1,
              },
              mode: 'xy',
              onZoomStart: function ({ chart }) {
                zoomHistory.push({
                  xMin: chart.scales.x.options.min,
                  xMax: chart.scales.x.options.max,
                  yMin: chart.scales.y.options.min,
                  yMax: chart.scales.y.options.max,
                });
              },
              onZoomComplete: function ({ chart }) {
                syncZoomToDelta(chart);
              },
            },
          },
        },
        scales: {
          x: {
            type: 'time',
            // Привязываем ось X к диапазону данных давления
            // чтобы события за пределами не растягивали ось
            min: pressureTimeMin || undefined,
            max: pressureTimeMax || undefined,
            time: {
              tooltipFormat: 'dd.MM.yyyy HH:mm',
              displayFormats: {
                minute: 'HH:mm',
                hour: 'HH:mm',
                day: 'dd.MM',
                week: 'dd.MM',
                month: 'MMM yyyy',
              },
            },
            adapters: {
              date: { locale: 'ru' },
            },
            ticks: {
              // Автомасштаб подписей на основе реального временного диапазона
              callback: function(value, index, ticks) {
                const d = new Date(value);
                const dd = String(d.getDate()).padStart(2, '0');
                const mm = String(d.getMonth() + 1).padStart(2, '0');
                const hh = String(d.getHours()).padStart(2, '0');
                const mi = String(d.getMinutes()).padStart(2, '0');
                const months = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
                const monthName = months[d.getMonth()];
                const days = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
                const dayName = days[d.getDay()];

                // Вычисляем реальный временной диапазон видимой области
                const firstTick = ticks[0]?.value;
                const lastTick = ticks[ticks.length - 1]?.value;
                const rangeDays = (lastTick && firstTick)
                  ? (lastTick - firstTick) / (1000 * 60 * 60 * 24)
                  : currentDays;

                // Прореживаем тики для читаемости
                const totalTicks = ticks.length;
                const skipFactor = rangeDays > 90 ? 3 : (rangeDays > 30 ? 2 : 1);
                if (index % skipFactor !== 0 && index !== 0 && index !== totalTicks - 1) {
                  return '';  // Пропускаем промежуточные тики
                }

                if (rangeDays > 90) {
                  // >90 дней — показываем месяц и число
                  return [`${dd} ${monthName}`, `${d.getFullYear()}`];
                } else if (rangeDays > 7) {
                  // 7-90 дней — дата и день недели
                  return [`${dd}.${mm}`, dayName];
                } else if (rangeDays > 2) {
                  // 2-7 дней — дата и время
                  return [`${dd}.${mm}`, `${hh}:${mi}`];
                } else {
                  // <2 дней — только время (с датой при смене дня)
                  if (index === 0 || (index > 0 && new Date(ticks[index-1].value).getDate() !== d.getDate())) {
                    return [`${dd}.${mm}`, `${hh}:${mi}`];
                  }
                  return `${hh}:${mi}`;
                }
              },
              maxRotation: 0,
              maxTicksLimit: 12,  // Ограничиваем количество тиков
              font: { size: 10 },
            },
            grid: { color: COLORS.grid },
          },
          y: {
            type: 'linear',
            position: 'left',
            title: {
              display: true,
              text: 'Давление (атм)',
              font: { size: 12, weight: 'bold' },
              color: '#374151',
            },
            grid: { color: COLORS.grid },
            ticks: { font: { size: 11 } },
          },
          y_events: {
            type: 'linear',
            position: 'right',
            min: 0,
            max: 1,
            display: false,
            grid: { display: false },
          },
          y_reagent: {
            type: 'linear',
            position: 'right',
            display: (maxQty || 0) > 0,
            title: {
              display: true,
              text: 'Реагент (кол-во)',
              font: { size: 11, weight: 'bold' },
              color: '#9c27b0',
            },
            grid: { display: false },
            ticks: {
              font: { size: 10 },
              color: '#9c27b0',
            },
            min: 0,
            max: maxQty > 0 ? Math.ceil(maxQty * 1.2) : 1,
          },
        },
      },
    });

    // После создания: отключаем pan если режим не активен
    // (pan.enabled=true нужен при создании чтобы Hammer.js зарегистрировал recognizers)
    if (!panMode) {
      syncChart.options.plugins.zoom.pan.enabled = false;
      syncChart.update('none');
    }

    // Обновляем кастомную легенду после рендера
    updateCustomLegend(datasets);
  }

  // ══════════════════ Кастомная легенда ══════════════════

  /**
   * Обновляет кастомную легенду с группами: давление, события, реагенты
   */
  function updateCustomLegend(datasets) {
    const eventsDropdown = document.getElementById('legend-events-dropdown');
    const reagentsDropdown = document.getElementById('legend-reagents-dropdown');
    const eventsCount = document.getElementById('legend-events-count');
    const reagentsCount = document.getElementById('legend-reagents-count');

    if (!eventsDropdown || !reagentsDropdown) return;

    // Группируем datasets
    const eventDatasets = datasets.filter(ds => ds.yAxisID === 'y_events');
    const reagentDatasets = datasets.filter(ds => ds.yAxisID === 'y_reagent');

    // Счётчики
    if (eventsCount) eventsCount.textContent = eventDatasets.length;
    if (reagentsCount) reagentsCount.textContent = reagentDatasets.length;

    // Заполняем выпадающий список событий
    eventsDropdown.innerHTML = eventDatasets.map((ds, i) => {
      const dsIndex = datasets.indexOf(ds);
      const isHidden = syncChart && !syncChart.isDatasetVisible(dsIndex);
      return `
        <label data-ds-index="${dsIndex}" style="
          display:flex; align-items:center; gap:8px; padding:6px 12px;
          cursor:pointer; transition:background 0.15s;
          ${isHidden ? 'opacity:0.5;' : ''}
        " onmouseover="this.style.background='#f5f5f5'" onmouseout="this.style.background='transparent'">
          <input type="checkbox" ${isHidden ? '' : 'checked'}
            style="accent-color:${ds.borderColor || '#999'}; cursor:pointer;">
          <span style="width:16px; height:2px; background:${ds.borderColor || '#999'};"></span>
          <span style="font-size:11px; color:#333;">${ds.label}</span>
        </label>
      `;
    }).join('') || '<div style="padding:8px 12px; color:#999; font-size:11px;">Нет событий</div>';

    // Заполняем выпадающий список реагентов
    reagentsDropdown.innerHTML = reagentDatasets.map((ds, i) => {
      const dsIndex = datasets.indexOf(ds);
      const isHidden = syncChart && !syncChart.isDatasetVisible(dsIndex);
      const count = ds.data?.length || 0;
      return `
        <label data-ds-index="${dsIndex}" style="
          display:flex; align-items:center; gap:8px; padding:6px 12px;
          cursor:pointer; transition:background 0.15s;
          ${isHidden ? 'opacity:0.5;' : ''}
        " onmouseover="this.style.background='#f5f5f5'" onmouseout="this.style.background='transparent'">
          <input type="checkbox" ${isHidden ? '' : 'checked'}
            style="accent-color:${ds.borderColor || '#e53935'}; cursor:pointer;">
          <span style="width:10px; height:10px; background:${ds.borderColor || '#e53935'}; border-radius:50%;"></span>
          <span style="font-size:11px; color:#333; flex:1;">${ds.label}</span>
          <span style="font-size:10px; color:#999;">(${count})</span>
        </label>
      `;
    }).join('') || '<div style="padding:8px 12px; color:#999; font-size:11px;">Нет реагентов</div>';

    // Добавляем обработчики на чекбоксы
    eventsDropdown.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', function() {
        const dsIndex = parseInt(this.closest('label').dataset.dsIndex);
        if (!syncChart) return;
        if (this.checked) {
          syncChart.show(dsIndex);
          this.closest('label').style.opacity = '1';
        } else {
          syncChart.hide(dsIndex);
          this.closest('label').style.opacity = '0.5';
        }
      });
    });

    reagentsDropdown.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', function() {
        const dsIndex = parseInt(this.closest('label').dataset.dsIndex);
        if (!syncChart) return;
        if (this.checked) {
          syncChart.show(dsIndex);
          this.closest('label').style.opacity = '1';
        } else {
          syncChart.hide(dsIndex);
          this.closest('label').style.opacity = '0.5';
        }
      });
    });
  }

  // ══════════════════ Обработчики выпадающих списков легенды ══════════════════

  function setupLegendDropdowns() {
    const eventsToggle = document.getElementById('legend-events-toggle');
    const eventsDropdown = document.getElementById('legend-events-dropdown');
    const eventsArrow = document.getElementById('legend-events-arrow');

    const reagentsToggle = document.getElementById('legend-reagents-toggle');
    const reagentsDropdown = document.getElementById('legend-reagents-dropdown');
    const reagentsArrow = document.getElementById('legend-reagents-arrow');

    function toggleDropdown(dropdown, arrow) {
      const isOpen = dropdown.style.display !== 'none';
      dropdown.style.display = isOpen ? 'none' : 'block';
      arrow.style.transform = isOpen ? 'rotate(0deg)' : 'rotate(180deg)';
    }

    if (eventsToggle && eventsDropdown) {
      eventsToggle.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleDropdown(eventsDropdown, eventsArrow);
        // Закрываем другой dropdown
        if (reagentsDropdown) reagentsDropdown.style.display = 'none';
        if (reagentsArrow) reagentsArrow.style.transform = 'rotate(0deg)';
      });
    }

    if (reagentsToggle && reagentsDropdown) {
      reagentsToggle.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleDropdown(reagentsDropdown, reagentsArrow);
        // Закрываем другой dropdown
        if (eventsDropdown) eventsDropdown.style.display = 'none';
        if (eventsArrow) eventsArrow.style.transform = 'rotate(0deg)';
      });
    }

    // Закрываем при клике вне
    document.addEventListener('click', () => {
      if (eventsDropdown) eventsDropdown.style.display = 'none';
      if (eventsArrow) eventsArrow.style.transform = 'rotate(0deg)';
      if (reagentsDropdown) reagentsDropdown.style.display = 'none';
      if (reagentsArrow) reagentsArrow.style.transform = 'rotate(0deg)';
    });

    // Клики внутри Ptr/Pshl легенды
    const ptrLegend = document.getElementById('legend-ptr');
    const pshlLegend = document.getElementById('legend-pshl');

    if (ptrLegend) {
      ptrLegend.addEventListener('click', () => togglePressureDataset('Ptr'));
    }
    if (pshlLegend) {
      pshlLegend.addEventListener('click', () => togglePressureDataset('Pshl'));
    }
  }

  function togglePressureDataset(name) {
    if (!syncChart) return;
    const datasets = syncChart.data.datasets;
    datasets.forEach((ds, i) => {
      if (ds.label && ds.label.includes(name)) {
        const el = document.getElementById(`legend-${name.toLowerCase()}`);
        if (syncChart.isDatasetVisible(i)) {
          syncChart.hide(i);
          if (el) el.style.opacity = '0.4';
        } else {
          syncChart.show(i);
          if (el) el.style.opacity = '1';
        }
      }
    });
  }

  // ══════════════════ Панель настроек цветов ══════════════════

  function setupColorSettings() {
    const toggleBtn = document.getElementById('color-settings-toggle');
    const dropdown = document.getElementById('color-settings-dropdown');
    const eventsContainer = document.getElementById('color-settings-events');
    const reagentsContainer = document.getElementById('color-settings-reagents');

    if (!toggleBtn || !dropdown) return;

    // Toggle dropdown
    toggleBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const isOpen = dropdown.style.display !== 'none';
      dropdown.style.display = isOpen ? 'none' : 'block';
    });

    // Close on outside click
    document.addEventListener('click', (e) => {
      if (!dropdown.contains(e.target) && e.target !== toggleBtn) {
        dropdown.style.display = 'none';
      }
    });
  }

  /**
   * Заполняет панель настроек цветов
   */
  function populateColorSettings() {
    const eventsContainer = document.getElementById('color-settings-events');
    const reagentsContainer = document.getElementById('color-settings-reagents');

    if (!eventsContainer || !reagentsContainer) return;

    const evData = window.wellEventsData || {};
    const eventColors = evData.eventColors || {};
    const reagentColors = evData.reagentColors || {};

    const eventLabels = {
      pressure: '📊 Замеры',
      purge: '💨 Продувки',
      equip: '🔧 Оборудование',
      note: '📝 Заметки',
      other: '📌 Другое',
    };

    // Events
    eventsContainer.innerHTML = Object.entries(eventLabels).map(([type, label]) => {
      const color = eventColors[type] || '#9e9e9e';
      return `
        <div style="display:flex; align-items:center; gap:8px;">
          <input type="color" data-type="event" data-name="${type}" value="${color}"
            style="width:32px; height:24px; border:1px solid #ddd; border-radius:4px; cursor:pointer;">
          <span style="font-size:11px; color:#333;">${label}</span>
        </div>
      `;
    }).join('');

    // Reagents
    const reagentNames = Object.keys(reagentColors);
    if (reagentNames.length > 0) {
      reagentsContainer.innerHTML = reagentNames.map(name => {
        const color = reagentColors[name] || '#e53935';
        return `
          <div style="display:flex; align-items:center; gap:8px;">
            <input type="color" data-type="reagent" data-name="${name}" value="${color}"
              style="width:32px; height:24px; border:1px solid #ddd; border-radius:4px; cursor:pointer;">
            <span style="font-size:11px; color:#333;">💉 ${name}</span>
          </div>
        `;
      }).join('');
    } else {
      reagentsContainer.innerHTML = '<span style="color:#999; font-size:11px;">Нет реагентов</span>';
    }

    // Add change handlers
    eventsContainer.querySelectorAll('input[type="color"]').forEach(input => {
      input.addEventListener('change', function() {
        const type = this.dataset.name;
        const color = this.value;
        if (window.wellEventsData && window.wellEventsData.eventColors) {
          window.wellEventsData.eventColors[type] = color;
        }
        loadChart();  // Перезагружаем график с новыми цветами
      });
    });

    reagentsContainer.querySelectorAll('input[type="color"]').forEach(input => {
      input.addEventListener('change', function() {
        const name = this.dataset.name;
        const color = this.value;
        if (window.wellEventsData && window.wellEventsData.reagentColors) {
          window.wellEventsData.reagentColors[name] = color;
        }
        loadChart();  // Перезагружаем график с новыми цветами
      });
    });
  }

  // ══════════════════ Загрузка данных ══════════════════

  async function loadChart(days, interval) {
    if (days !== undefined) currentDays = days;
    if (interval !== undefined) currentInterval = interval;

    // 1) Получаем данные давления
    const filterStr = getFilterParams();
    const url = `/api/pressure/chart/${wellId}?days=${currentDays}&interval=${currentInterval}${filterStr}`;

    try {
      const resp = await fetch(url);
      if (!resp.ok) {
        console.error('[sync_chart] API error:', resp.status);
        return;
      }
      const json = await resp.json();
      const points = json.points || [];

      // 2) Получаем события и фильтруем по текущему периоду
      const rawEvData = window.wellEventsData || {};
      const visibleTypes = getVisibleEventTypes();

      // Фильтруем события по периоду (последние currentDays дней)
      const cutoffTime = new Date();
      cutoffTime.setDate(cutoffTime.getDate() - currentDays);
      const cutoffMs = cutoffTime.getTime();

      const filteredEvents = (rawEvData.timelineEvents || []).filter(ev => {
        if (!ev.t) return false;
        const evTime = new Date(normalizeTime(ev.t)).getTime();
        return evTime >= cutoffMs;
      });

      const filteredInjections = (rawEvData.timelineInjections || []).filter(inj => {
        if (!inj.t) return false;
        const injTime = new Date(normalizeTime(inj.t)).getTime();
        return injTime >= cutoffMs;
      });

      const evData = {
        timelineEvents: filteredEvents,
        timelineInjections: filteredInjections,
        eventColors: rawEvData.eventColors || {},
        reagentColors: rawEvData.reagentColors || {},
      };

      console.log('[sync_chart] pressure:', points.length, 'pts,',
        'events:', filteredEvents.length, '(filtered from', (rawEvData.timelineEvents || []).length, ')',
        'injections:', filteredInjections.length, '(filtered from', (rawEvData.timelineInjections || []).length, ')',
        'visibleTypes:', visibleTypes, 'days:', currentDays);

      // Дебаг: первая/последняя точка давления + первое событие
      if (points.length > 0) {
        console.log('[sync_chart] pressure range:', points[0].t, '→', points[points.length - 1].t);
      }
      if (filteredEvents.length > 0) {
        console.log('[sync_chart] first event:', filteredEvents[0].t, ', last event:', filteredEvents[filteredEvents.length - 1].t);
      }

      // 3) Строим датасеты
      const result = buildDatasets(points, evData, visibleTypes);
      const datasets = result.datasets;

      // 4) Статистика событий в диапазоне и сводка
      showEventStats(datasets);
      updateEventsSummaryPanel(evData);

      // 5) Рендер (с привязкой оси X к диапазону давления)
      renderChart(datasets, result.pressureTimeMin, result.pressureTimeMax, result.maxQty);

      // 6) Инициализация Y-слайдера из данных
      initYSlider(points);

      // 7) Перезагружаем delta chart с теми же параметрами
      if (window.deltaChart && window.deltaChart.reload) {
        window.deltaChart.reload(currentDays, currentInterval);
      }

    } catch (err) {
      console.error('[sync_chart] Load error:', err);
    }
  }

  // ══════════════════ Статистика событий ══════════════════

  function showEventStats(datasets) {
    const statsEl = document.getElementById('sync-event-stats');
    if (!statsEl) return;

    let totalEvents = 0;
    datasets.forEach(ds => {
      if (ds.yAxisID === 'y_events' || ds.yAxisID === 'y_reagent') {
        totalEvents += ds.data.length;
      }
    });

    statsEl.textContent = totalEvents > 0
      ? `${totalEvents} событий в диапазоне`
      : 'Нет событий в диапазоне';
  }

  // ══════════════════ Панель сводки событий ══════════════════

  /**
   * Обновляет панель сводки событий за период
   * @param {Object} evData - данные событий (timelineEvents, timelineInjections)
   */
  function updateEventsSummaryPanel(evData) {
    const reagentsListEl = document.getElementById('summary-reagents-list');
    const reagentsTotalEl = document.getElementById('summary-reagents-total');
    const eventsListEl = document.getElementById('summary-events-list');
    const todayListEl = document.getElementById('summary-today-list');

    if (!reagentsListEl) return;

    const injections = evData.timelineInjections || [];
    const events = evData.timelineEvents || [];
    const reagentColors = evData.reagentColors || {};
    const eventColors = evData.eventColors || {};

    // ── Реагенты: группируем по названию и суммируем количество ──
    const reagentTotals = {};
    injections.forEach(inj => {
      const name = inj.reagent || 'Неизвестный';
      const qty = parseFloat(inj.qty) || 0;
      if (!reagentTotals[name]) {
        reagentTotals[name] = { qty: 0, count: 0, color: reagentColors[name] || '#e53935' };
      }
      reagentTotals[name].qty += qty;
      reagentTotals[name].count++;
    });

    reagentsListEl.innerHTML = Object.entries(reagentTotals)
      .map(([name, data]) => `
        <span style="
          display:inline-flex; align-items:center; gap:4px;
          padding:3px 8px; background:white; border-radius:12px;
          border:1px solid ${data.color}; font-size:11px;
        ">
          <span style="width:8px; height:8px; border-radius:50%; background:${data.color};"></span>
          <span style="font-weight:600; color:${data.color};">${name}</span>
          <span style="color:#666;">${data.qty.toFixed(1)} (${data.count}×)</span>
        </span>
      `).join('');

    // Общее количество
    const totalReagentQty = Object.values(reagentTotals).reduce((sum, r) => sum + r.qty, 0);
    reagentsTotalEl.innerHTML = injections.length > 0
      ? `Всего: ${totalReagentQty.toFixed(1)} (${injections.length} вбросов)`
      : '<span style="color:#999;">Нет вбросов реагентов</span>';

    // ── События: группируем по типу ──
    const eventsByType = {};
    events.forEach(ev => {
      const type = (ev.type || 'other').toLowerCase();
      if (!eventsByType[type]) {
        eventsByType[type] = { count: 0, color: eventColors[type] || '#9e9e9e' };
      }
      eventsByType[type].count++;
    });

    const typeLabels = {
      pressure: '📊 Замеры',
      purge: '💨 Продувки',
      equip: '🔧 Оборудование',
      note: '📝 Заметки',
      other: '📌 Другое',
    };

    eventsListEl.innerHTML = Object.entries(eventsByType)
      .map(([type, data]) => `
        <span style="
          display:inline-flex; align-items:center; gap:4px;
          padding:3px 8px; background:white; border-radius:12px;
          border:1px solid ${data.color}; font-size:11px;
        ">
          <span>${typeLabels[type] || type}</span>
          <span style="font-weight:700; color:${data.color};">${data.count}</span>
        </span>
      `).join('') || '<span style="color:#999;">Нет событий</span>';

    // ── События за сегодня ──
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const todayMs = today.getTime();

    const todayEvents = events.filter(ev => {
      if (!ev.t) return false;
      const evDate = new Date(ev.t);
      return evDate.getTime() >= todayMs;
    });

    const todayInjections = injections.filter(inj => {
      if (!inj.t) return false;
      const injDate = new Date(inj.t);
      return injDate.getTime() >= todayMs;
    });

    // Группируем события сегодня по типу
    const todayByType = {};
    todayEvents.forEach(ev => {
      const type = (ev.type || 'other').toLowerCase();
      todayByType[type] = (todayByType[type] || 0) + 1;
    });

    // Группируем вбросы сегодня по реагенту
    const todayReagents = {};
    todayInjections.forEach(inj => {
      const name = inj.reagent || 'Реагент';
      const qty = parseFloat(inj.qty) || 0;
      if (!todayReagents[name]) todayReagents[name] = 0;
      todayReagents[name] += qty;
    });

    let todayHtml = '';
    if (Object.keys(todayByType).length > 0) {
      todayHtml += '<div style="display:flex; flex-wrap:wrap; gap:4px; margin-bottom:4px;">';
      Object.entries(todayByType).forEach(([type, count]) => {
        const color = eventColors[type] || '#9e9e9e';
        todayHtml += `<span style="padding:2px 6px; background:${color}20; border-radius:8px; font-size:10px; color:${color}; font-weight:600;">${typeLabels[type] || type}: ${count}</span>`;
      });
      todayHtml += '</div>';
    }
    if (Object.keys(todayReagents).length > 0) {
      todayHtml += '<div style="display:flex; flex-wrap:wrap; gap:4px;">';
      Object.entries(todayReagents).forEach(([name, qty]) => {
        const color = reagentColors[name] || '#e53935';
        todayHtml += `<span style="padding:2px 6px; background:${color}20; border-radius:8px; font-size:10px; color:${color}; font-weight:600;">💉 ${name}: ${qty.toFixed(1)}</span>`;
      });
      todayHtml += '</div>';
    }

    todayListEl.innerHTML = todayHtml || '<span style="color:#999; font-size:11px;">Нет событий сегодня</span>';
  }

  // ══════════════════ Sync Zoom с Delta Chart ══════════════════

  /** Синхронизирует текущий zoom/pan с графиком ΔP */
  function syncZoomToDelta(chart) {
    if (!window.deltaChart || !window.deltaChart.syncZoom) return;
    const xScale = chart.scales.x;
    if (!xScale) return;
    window.deltaChart.syncZoom(xScale.min, xScale.max);
  }

  // ══════════════════ Zoom: назад + сброс ══════════════════

  function zoomBack() {
    if (!syncChart || zoomHistory.length === 0) return;
    const prev = zoomHistory.pop();
    syncChart.options.scales.x.min = prev.xMin;
    syncChart.options.scales.x.max = prev.xMax;
    syncChart.options.scales.y.min = prev.yMin;
    syncChart.options.scales.y.max = prev.yMax;
    syncChart.update('none');
    if (window.deltaChart && window.deltaChart.syncZoom) {
      window.deltaChart.syncZoom(prev.xMin, prev.xMax);
    }
  }

  const zoomBackBtn = document.getElementById('sync-zoom-back');
  if (zoomBackBtn) {
    zoomBackBtn.addEventListener('click', zoomBack);
  }

  const resetBtn = document.getElementById('sync-reset-zoom');
  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      zoomHistory = [];
      if (panMode) { panMode = false; updatePanButtonUI(); }
      // Полная перезагрузка графика — гарантированно возвращает к исходному периоду
      loadChart();
    });
  }

  // ══════════════════ Pan toggle: переключение Zoom ↔ Pan ══════════════════

  function updatePanButtonUI() {
    const btn = document.getElementById('sync-pan-toggle');
    if (btn) {
      btn.style.background = panMode ? '#17a2b8' : '#fff';
      btn.style.borderColor = panMode ? '#17a2b8' : '#ccc';
    }
  }

  function togglePanMode() {
    panMode = !panMode;
    window._syncPanMode = panMode;  // Для delta chart
    if (!syncChart) return;

    // Pan ON  → pan.enabled=true, drag.enabled=false → Hammer.js pan
    // Pan OFF → pan.enabled=false, drag.enabled=true  → drag-zoom only
    syncChart.options.plugins.zoom.pan.enabled = panMode;
    syncChart.options.plugins.zoom.zoom.drag.enabled = !panMode;
    syncChart.update('none');
    canvas.style.cursor = panMode ? 'grab' : 'crosshair';

    // Синхронизируем с delta chart
    if (window.deltaChart && window.deltaChart.instance) {
      const dc = window.deltaChart.instance;
      dc.options.plugins.zoom.pan.enabled = panMode;
      dc.options.plugins.zoom.zoom.drag.enabled = !panMode;
      dc.update('none');
    }

    updatePanButtonUI();
  }

  const panBtn = document.getElementById('sync-pan-toggle');
  if (panBtn) {
    panBtn.addEventListener('click', togglePanMode);
  }

  // ══════════════════ ПКМ → Фиксация курсора / Выбор диапазона ══════════════════

  /**
   * Обновляет UI индикаторов режима курсора
   */
  function updateCursorModeUI() {
    const modeIndicator = document.getElementById('sync-mode-indicator');
    const rangeMenu = document.getElementById('sync-range-menu');
    const rangeInfo = document.getElementById('sync-range-info');
    const tooltipPanel = document.getElementById('sync-tooltip-panel');

    if (modeIndicator) {
      if (cursorMode === 'locked') {
        modeIndicator.style.display = 'block';
        modeIndicator.textContent = '🔒 Фикс';
        modeIndicator.style.background = '#1565c0';
      } else if (cursorMode === 'range_start') {
        modeIndicator.style.display = 'block';
        modeIndicator.textContent = '📐 Начало';
        modeIndicator.style.background = '#28a745';
      } else if (cursorMode === 'range_end') {
        modeIndicator.style.display = 'block';
        modeIndicator.textContent = '📐 Диапазон';
        modeIndicator.style.background = '#17a2b8';
      } else {
        modeIndicator.style.display = 'none';
      }
    }

    if (tooltipPanel) {
      if (cursorMode !== 'normal') {
        tooltipPanel.style.borderColor = '#1565c0';
        tooltipPanel.style.boxShadow = '0 0 0 2px rgba(21, 101, 192, 0.2)';
      } else {
        tooltipPanel.style.borderColor = '#dee2e6';
        tooltipPanel.style.boxShadow = 'none';
      }
    }

    // Показываем меню диапазона только когда range_end
    if (rangeMenu) {
      if (cursorMode === 'range_end' && rangeStart && rangeEnd) {
        rangeMenu.style.display = 'block';
        // Форматируем информацию о диапазоне
        const formatTime = (ts) => {
          const d = new Date(ts);
          const dd = String(d.getDate()).padStart(2, '0');
          const mm = String(d.getMonth() + 1).padStart(2, '0');
          const hh = String(d.getHours()).padStart(2, '0');
          const mi = String(d.getMinutes()).padStart(2, '0');
          return `${dd}.${mm} ${hh}:${mi}`;
        };
        const durationMs = Math.abs(rangeEnd - rangeStart);
        const durationHrs = (durationMs / (1000 * 60 * 60)).toFixed(1);
        if (rangeInfo) {
          rangeInfo.textContent = `${formatTime(rangeStart)} — ${formatTime(rangeEnd)} (${durationHrs} ч)`;
        }
      } else {
        rangeMenu.style.display = 'none';
      }
    }

    // Обновляем кнопку блокировки
    const lockBtn = document.getElementById('sync-tooltip-lock');
    if (lockBtn) {
      if (cursorMode !== 'normal') {
        lockBtn.textContent = '🔒';
        lockBtn.style.background = '#1565c0';
        lockBtn.style.color = 'white';
        lockBtn.title = 'Нажмите чтобы разблокировать';
      } else {
        lockBtn.textContent = '🔓';
        lockBtn.style.background = '#e9ecef';
        lockBtn.style.color = '#6c757d';
        lockBtn.title = 'ПКМ на графике = фиксация курсора';
      }
    }
  }

  /**
   * Сбрасывает режим курсора в нормальный
   */
  function resetCursorMode() {
    cursorMode = 'normal';
    tooltipLocked = false;
    rangeStart = null;
    rangeEnd = null;
    lockedXValue = null;
    updateCursorModeUI();
    console.log('[sync_chart] Cursor mode reset to normal');
  }

  canvas.addEventListener('contextmenu', function (e) {
    e.preventDefault();
    if (!syncChart) return;

    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const xScale = syncChart.scales.x;
    if (!xScale) return;

    const xValue = xScale.getValueForPixel(x);
    if (!xValue) return;

    const clickTime = new Date(xValue).getTime();

    if (cursorMode === 'normal') {
      // Первый ПКМ → фиксация курсора
      cursorMode = 'locked';
      tooltipLocked = true;
      lockedXValue = clickTime;
      console.log('[sync_chart] Cursor locked at', new Date(clickTime).toISOString());
    } else if (cursorMode === 'locked') {
      // Второй ПКМ (в locked режиме) → начало выбора диапазона
      cursorMode = 'range_start';
      rangeStart = lockedXValue;  // используем зафиксированную позицию как начало
      console.log('[sync_chart] Range start at', new Date(rangeStart).toISOString());
    } else if (cursorMode === 'range_start') {
      // Третий ПКМ → конец диапазона
      cursorMode = 'range_end';
      rangeEnd = clickTime;
      // Убеждаемся что start < end
      if (rangeEnd < rangeStart) {
        [rangeStart, rangeEnd] = [rangeEnd, rangeStart];
      }
      console.log('[sync_chart] Range end at', new Date(rangeEnd).toISOString());
    } else if (cursorMode === 'range_end') {
      // Ещё один ПКМ в режиме range_end → сбрасываем
      resetCursorMode();
    }

    updateCursorModeUI();
  });

  // Любой клик ЛКМ на canvas → возврат в нормальный режим
  canvas.addEventListener('click', function (e) {
    if (cursorMode !== 'normal') {
      resetCursorMode();
    }
  });

  // Также сбрасываем при Escape
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && cursorMode !== 'normal') {
      resetCursorMode();
    }
  });

  // ══════════════════ Обработчики меню диапазона ══════════════════

  const rangeExportBtn = document.getElementById('sync-range-export-excel');
  const rangeSaveImageBtn = document.getElementById('sync-range-save-image');
  const rangeCalculateBtn = document.getElementById('sync-range-calculate');
  const rangeCancelBtn = document.getElementById('sync-range-cancel');

  if (rangeExportBtn) {
    rangeExportBtn.addEventListener('click', async function () {
      if (!rangeStart || !rangeEnd) return;
      await exportRangeToExcel(rangeStart, rangeEnd);
    });
  }

  if (rangeSaveImageBtn) {
    rangeSaveImageBtn.addEventListener('click', function () {
      saveChartImage();
    });
  }

  if (rangeCalculateBtn) {
    rangeCalculateBtn.addEventListener('click', function () {
      if (!rangeStart || !rangeEnd) return;
      calculateRangeStats(rangeStart, rangeEnd);
    });
  }

  if (rangeCancelBtn) {
    rangeCancelBtn.addEventListener('click', function () {
      resetCursorMode();
    });
  }

  /**
   * Экспортирует данные выбранного диапазона в Excel (CSV)
   */
  async function exportRangeToExcel(startMs, endMs) {
    console.log('[sync_chart] Exporting range to Excel:', new Date(startMs), '→', new Date(endMs));

    // Получаем данные давления из текущего графика
    if (!syncChart) return;

    const datasets = syncChart.data.datasets;
    const pressureData = [];
    const eventsData = [];

    // Собираем данные давления
    datasets.forEach(ds => {
      if (ds.yAxisID === 'y') {
        const label = ds.label;
        ds.data.forEach(pt => {
          const t = new Date(pt.x).getTime();
          if (t >= startMs && t <= endMs) {
            pressureData.push({ time: pt.x, label: label, value: pt.y });
          }
        });
      }
    });

    // Собираем события
    datasets.forEach(ds => {
      if (ds.yAxisID === 'y_events' || ds.yAxisID === 'y_reagent') {
        ds.data.forEach(pt => {
          const t = new Date(pt.x).getTime();
          if (t >= startMs && t <= endMs && pt.eventData) {
            eventsData.push({
              time: pt.x,
              type: pt.eventData.type || 'reagent',
              description: pt.eventData.description || pt.eventData.reagent || '',
              qty: pt.eventData.qty || '',
            });
          }
        });
      }
    });

    // Создаём CSV
    let csv = 'Время;Ptr (устье);Pshl (шлейф);ΔP;Событие;Тип;Количество\n';

    // Группируем давление по времени
    const pressureByTime = {};
    pressureData.forEach(p => {
      if (!pressureByTime[p.time]) pressureByTime[p.time] = {};
      if (p.label.includes('Ptr')) pressureByTime[p.time].ptr = p.value;
      if (p.label.includes('Pshl')) pressureByTime[p.time].pshl = p.value;
    });

    // Добавляем события к ближайшему времени давления
    const eventsByTime = {};
    eventsData.forEach(ev => {
      if (!eventsByTime[ev.time]) eventsByTime[ev.time] = [];
      eventsByTime[ev.time].push(ev);
    });

    // Генерируем строки CSV
    Object.keys(pressureByTime).sort().forEach(time => {
      const p = pressureByTime[time];
      const ptr = p.ptr !== undefined ? p.ptr.toFixed(3) : '';
      const pshl = p.pshl !== undefined ? p.pshl.toFixed(3) : '';
      const delta = (p.ptr !== undefined && p.pshl !== undefined) ? (p.ptr - p.pshl).toFixed(3) : '';

      // Форматируем время
      const d = new Date(time);
      const timeStr = `${d.getDate().toString().padStart(2, '0')}.${(d.getMonth()+1).toString().padStart(2, '0')}.${d.getFullYear()} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;

      csv += `${timeStr};${ptr};${pshl};${delta};;;\n`;
    });

    // Добавляем отдельный раздел для событий
    if (eventsData.length > 0) {
      csv += '\n;;;;;;\nСобытия в диапазоне;;;;;;\n';
      eventsData.sort((a, b) => new Date(a.time) - new Date(b.time)).forEach(ev => {
        const d = new Date(ev.time);
        const timeStr = `${d.getDate().toString().padStart(2, '0')}.${(d.getMonth()+1).toString().padStart(2, '0')}.${d.getFullYear()} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
        csv += `${timeStr};;;;${ev.description};${ev.type};${ev.qty}\n`;
      });
    }

    // Скачиваем файл
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const startDate = new Date(startMs);
    const endDate = new Date(endMs);
    a.download = `pressure_${wellId}_${startDate.toISOString().slice(0,10)}_${endDate.toISOString().slice(0,10)}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    console.log('[sync_chart] CSV exported:', pressureData.length, 'pressure points,', eventsData.length, 'events');
    resetCursorMode();
  }

  /**
   * Сохраняет график как изображение
   */
  function saveChartImage() {
    if (!syncChart) return;

    const link = document.createElement('a');
    link.href = canvas.toDataURL('image/png', 1.0);
    const now = new Date();
    link.download = `chart_${wellId}_${now.toISOString().slice(0,10)}.png`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    console.log('[sync_chart] Chart image saved');
    resetCursorMode();
  }

  /**
   * Рассчитывает статистику для выбранного диапазона
   */
  function calculateRangeStats(startMs, endMs) {
    if (!syncChart) return;

    const datasets = syncChart.data.datasets;
    const ptrValues = [];
    const pshlValues = [];

    datasets.forEach(ds => {
      if (ds.yAxisID === 'y') {
        ds.data.forEach(pt => {
          const t = new Date(pt.x).getTime();
          if (t >= startMs && t <= endMs) {
            if (ds.label.includes('Ptr')) ptrValues.push(pt.y);
            if (ds.label.includes('Pshl')) pshlValues.push(pt.y);
          }
        });
      }
    });

    if (ptrValues.length === 0 && pshlValues.length === 0) {
      alert('Нет данных в выбранном диапазоне');
      return;
    }

    const calcStats = (arr) => {
      if (arr.length === 0) return { min: 0, max: 0, avg: 0, count: 0 };
      const min = Math.min(...arr);
      const max = Math.max(...arr);
      const avg = arr.reduce((a, b) => a + b, 0) / arr.length;
      return { min, max, avg, count: arr.length };
    };

    const ptrStats = calcStats(ptrValues);
    const pshlStats = calcStats(pshlValues);

    const durationMs = endMs - startMs;
    const durationHrs = (durationMs / (1000 * 60 * 60)).toFixed(2);

    const message = `📊 Статистика за ${durationHrs} ч

Ptr (устье): ${ptrStats.count} точек
  • Мин: ${ptrStats.min.toFixed(3)} атм
  • Макс: ${ptrStats.max.toFixed(3)} атм
  • Средн: ${ptrStats.avg.toFixed(3)} атм

Pshl (шлейф): ${pshlStats.count} точек
  • Мин: ${pshlStats.min.toFixed(3)} атм
  • Макс: ${pshlStats.max.toFixed(3)} атм
  • Средн: ${pshlStats.avg.toFixed(3)} атм

ΔP (средняя): ${(ptrStats.avg - pshlStats.avg).toFixed(3)} атм`;

    alert(message);
    console.log('[sync_chart] Range stats calculated');
  }

  // ══════════════════ Y-слайдер давления ══════════════════

  const yPressureSlider = document.getElementById('sync-y-pressure-slider');
  const yPressureLabel  = document.getElementById('sync-y-pressure-label');
  let defaultYMax = null;

  function initYSlider(points) {
    if (!yPressureSlider) return;
    let dataMax = 0;
    for (const p of points) {
      if (p.p_tube_avg !== null && p.p_tube_avg !== undefined && p.p_tube_avg > dataMax) dataMax = p.p_tube_avg;
      if (p.p_line_avg !== null && p.p_line_avg !== undefined && p.p_line_avg > dataMax) dataMax = p.p_line_avg;
      if (p.p_tube_max !== null && p.p_tube_max !== undefined && p.p_tube_max > dataMax) dataMax = p.p_tube_max;
      if (p.p_line_max !== null && p.p_line_max !== undefined && p.p_line_max > dataMax) dataMax = p.p_line_max;
    }
    defaultYMax = Math.ceil(dataMax + 2);
    yPressureSlider.max = Math.max(defaultYMax * 2, 100);
    yPressureSlider.value = defaultYMax;
    if (yPressureLabel) yPressureLabel.textContent = defaultYMax;
  }

  function applyYSlider() {
    if (!syncChart || !yPressureSlider) return;
    const val = parseFloat(yPressureSlider.value);
    syncChart.options.scales.y.max = val;
    syncChart.update('none');
    if (yPressureLabel) yPressureLabel.textContent = val;
  }

  function resetYSlider() {
    if (!yPressureSlider || !defaultYMax) return;
    yPressureSlider.value = defaultYMax;
    if (yPressureLabel) yPressureLabel.textContent = defaultYMax;
    if (syncChart) {
      delete syncChart.options.scales.y.max;
      syncChart.update('none');
    }
  }

  if (yPressureSlider) {
    yPressureSlider.addEventListener('input', applyYSlider);
  }

  // ══════════════════ Кнопки дней/интервала ══════════════════

  function setActiveBtn(prefix, activeBtn) {
    document.querySelectorAll(`[data-${prefix}]`).forEach(b => {
      b.style.background = 'white';
      b.style.color = '#333';
      b.style.borderColor = '#dee2e6';
    });
    activeBtn.style.background = '#1565c0';
    activeBtn.style.color = 'white';
    activeBtn.style.borderColor = '#1565c0';
  }

  document.querySelectorAll('[data-sync-days]').forEach(btn => {
    btn.addEventListener('click', function () {
      const days = parseInt(this.dataset.syncDays);
      if (window.updateAllCharts) {
        window.updateAllCharts(days, undefined);
      } else {
        setActiveBtn('sync-days', this);
        loadChart(days, undefined);
      }
    });
  });

  document.querySelectorAll('[data-sync-interval]').forEach(btn => {
    btn.addEventListener('click', function () {
      const interval = parseInt(this.dataset.syncInterval);
      if (window.updateAllCharts) {
        window.updateAllCharts(undefined, interval);
      } else {
        setActiveBtn('sync-interval', this);
        loadChart(undefined, interval);
      }
    });
  });

  // ══════════════════ Обработчики собственных фильтров ══════════════════

  ['sync-filter-zeros', 'sync-filter-spikes'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', () => loadChart());
  });

  const syncFillEl = document.getElementById('sync-filter-fill-mode');
  if (syncFillEl) syncFillEl.addEventListener('change', () => loadChart());

  const syncGapEl = document.getElementById('sync-filter-max-gap');
  if (syncGapEl) syncGapEl.addEventListener('change', () => loadChart());

  // ══════════════════ Слайдер чувствительности событий ══════════════════

  const sensitivitySlider = document.getElementById('sync-event-sensitivity');
  const sensitivityLabel = document.getElementById('sync-event-sensitivity-label');

  if (sensitivitySlider) {
    // Устанавливаем начальное значение
    eventSensitivity = parseInt(sensitivitySlider.value) || 60;
    if (sensitivityLabel) sensitivityLabel.textContent = eventSensitivity + 'px';

    sensitivitySlider.addEventListener('input', function() {
      eventSensitivity = parseInt(this.value) || 60;
      if (sensitivityLabel) sensitivityLabel.textContent = eventSensitivity + 'px';
      // Обновляем tooltip — перезапускаем interaction при следующем движении мыши
      console.log('[sync_chart] Event sensitivity changed to:', eventSensitivity, 'px');
    });
  }

  // ══════════════════ Кнопка блокировки tooltip (сброс режима) ══════════════════

  const tooltipLockBtn = document.getElementById('sync-tooltip-lock');

  if (tooltipLockBtn) {
    tooltipLockBtn.addEventListener('click', function() {
      // Кнопка сбрасывает режим курсора
      if (cursorMode !== 'normal') {
        resetCursorMode();
      }
    });
  }

  // ══════════════════ Инициализация ══════════════════

  // Генерируем чекбоксы событий
  createEventFilters();

  // Настраиваем выпадающие списки легенды
  setupLegendDropdowns();

  // Настраиваем панель цветов
  setupColorSettings();

  // Настраиваем слайдер размера маркеров реагентов
  (function setupReagentMarkerSlider() {
    const slider = document.getElementById('reagent-marker-size');
    const label = document.getElementById('reagent-marker-size-label');
    if (!slider) return;
    const saved = localStorage.getItem('reagentMarkerSize');
    if (saved) { slider.value = saved; if (label) label.textContent = saved; }
    slider.addEventListener('input', function() {
      if (label) label.textContent = this.value;
      localStorage.setItem('reagentMarkerSize', this.value);
      loadChart();
    });
  })();

  // Начальная загрузка
  loadChart(7, 5);

  // Заполняем панель настроек цветов
  populateColorSettings();

  // Экспортируем функции (для внешней синхронизации)
  window.syncChartReload = function (days, interval) {
    loadChart(days, interval);
  };

  // Экспорт для синхронизации zoom с delta chart
  window.syncChart = {
    syncZoom: function(xMin, xMax) {
      if (!syncChart) return;
      syncChart.options.scales.x.min = xMin;
      syncChart.options.scales.x.max = xMax;
      syncChart.update('none');
    },
    resetZoom: function() {
      if (!syncChart) return;
      delete syncChart.options.scales.x.min;
      delete syncChart.options.scales.x.max;
      syncChart.update('none');
    },
  };

  // ══════════════════ Глобальный координатор всех графиков ══════════════════

  /**
   * Обновляет все графики на странице с новым периодом/интервалом.
   * Вызывается при нажатии кнопок периода/интервала.
   * @param {number} days - количество дней (если undefined, сохраняем текущее)
   * @param {number} interval - интервал в минутах (если undefined, сохраняем текущее)
   */
  window.updateAllCharts = function (days, interval) {
    // Обновляем активные кнопки
    if (days !== undefined) {
      document.querySelectorAll('[data-sync-days]').forEach(b => {
        const d = parseInt(b.dataset.syncDays);
        if (d === days) {
          b.style.background = '#1565c0';
          b.style.color = 'white';
          b.style.borderColor = '#1565c0';
        } else {
          b.style.background = 'white';
          b.style.color = '#333';
          b.style.borderColor = '#dee2e6';
        }
      });
    }

    if (interval !== undefined) {
      document.querySelectorAll('[data-sync-interval]').forEach(b => {
        const i = parseInt(b.dataset.syncInterval);
        if (i === interval) {
          b.style.background = '#1565c0';
          b.style.color = 'white';
          b.style.borderColor = '#1565c0';
        } else {
          b.style.background = 'white';
          b.style.color = '#333';
          b.style.borderColor = '#dee2e6';
        }
      });
    }

    // Обновляем synchronized chart (который также обновляет delta chart)
    loadChart(days, interval);

    // Обновляем график событий если есть
    if (window.eventsChartReload) {
      window.eventsChartReload();
    }
  };

})();
