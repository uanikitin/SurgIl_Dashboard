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

  console.log('[sync_chart] Script loaded, version 28');

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
  let currentStart = null;  // ISO string (Кунград) или null — точное начало периода
  let currentEnd = null;    // ISO string (Кунград) или null — точный конец периода
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
  let hoverXValue = null;  // текущая позиция мыши (timestamp) для live-подсветки

  // ══════════════════ Аннотации пользователя ══════════════════
  let chartAnnotations = [];      // массив из API
  let annotationsVisible = true;  // показывать ли аннотации

  // ══════════════════ Маски коррекции давления ══════════════════
  let currentMaskZones = [];  // массив зон из API (mask_zones)
  let detectedAnomalyZones = [];  // временные зоны из авто-детекции
  let maskSelectionMode = false;  // режим выбора участка для маски

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

  // ══════════════════ Плагин: live-подсветка выбираемого диапазона ══════════════════
  // Рисует полупрозрачную заливку от rangeStart до текущей позиции мыши
  // в режиме range_start, и фиксированную заливку в range_end.
  const rangeHighlightPlugin = {
    id: 'rangeHighlight',
    afterDatasetsDraw(chart) {
      const xAxis = chart.scales.x;
      const yAxis = chart.scales.y;
      if (!xAxis || !yAxis) return;

      let xStart = null;
      let xEnd = null;
      let isLive = false;

      if (cursorMode === 'range_start' && rangeStart != null && hoverXValue != null) {
        // Live: от начала до текущей мыши
        xStart = xAxis.getPixelForValue(rangeStart);
        xEnd = xAxis.getPixelForValue(hoverXValue);
        isLive = true;
      } else if (cursorMode === 'range_end' && rangeStart != null && rangeEnd != null) {
        // Зафиксированный диапазон
        xStart = xAxis.getPixelForValue(rangeStart);
        xEnd = xAxis.getPixelForValue(rangeEnd);
      }

      if (xStart == null || xEnd == null) return;

      // Нормализуем порядок
      const left = Math.max(Math.min(xStart, xEnd), xAxis.left);
      const right = Math.min(Math.max(xStart, xEnd), xAxis.right);
      if (right - left < 1) return;

      const ctx = chart.ctx;
      ctx.save();

      // Заливка области
      ctx.fillStyle = isLive
        ? 'rgba(21, 101, 192, 0.10)'
        : 'rgba(21, 101, 192, 0.15)';
      ctx.fillRect(left, yAxis.top, right - left, yAxis.bottom - yAxis.top);

      // Вертикальные границы
      ctx.strokeStyle = 'rgba(21, 101, 192, 0.6)';
      ctx.lineWidth = 1.5;
      ctx.setLineDash(isLive ? [4, 4] : []);

      // Линия начала
      const xStartPx = xAxis.getPixelForValue(rangeStart);
      if (xStartPx >= xAxis.left && xStartPx <= xAxis.right) {
        ctx.beginPath();
        ctx.moveTo(xStartPx, yAxis.top);
        ctx.lineTo(xStartPx, yAxis.bottom);
        ctx.stroke();
      }

      // Линия конца (для live — текущая позиция мыши)
      const xEndPx = isLive
        ? xAxis.getPixelForValue(hoverXValue)
        : xAxis.getPixelForValue(rangeEnd);
      if (xEndPx >= xAxis.left && xEndPx <= xAxis.right && Math.abs(xEndPx - xStartPx) > 3) {
        ctx.beginPath();
        ctx.moveTo(xEndPx, yAxis.top);
        ctx.lineTo(xEndPx, yAxis.bottom);
        ctx.stroke();
      }

      ctx.restore();
    },
  };

  // Отслеживаем позицию мыши для live-подсветки диапазона
  let _rangeRafPending = false;
  canvas.addEventListener('mousemove', function (e) {
    if (cursorMode !== 'range_start') return;
    if (!syncChart) return;

    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const xScale = syncChart.scales.x;
    if (!xScale) return;

    const val = xScale.getValueForPixel(x);
    if (val) {
      hoverXValue = new Date(val).getTime();
      if (!_rangeRafPending) {
        _rangeRafPending = true;
        requestAnimationFrame(() => {
          _rangeRafPending = false;
          if (syncChart && cursorMode === 'range_start') syncChart.draw();
        });
      }
    }
  });

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
    const spikeThreshold = document.getElementById('sync-filter-spike-threshold');
    const fill   = document.getElementById('sync-filter-fill-mode');
    const gap    = document.getElementById('sync-filter-max-gap');
    const gapBreak = document.getElementById('sync-filter-gap-break');
    const modeSelect = document.getElementById('sync-chart-mode');

    let s = '';

    // If mode is set, it overrides individual filter params on the backend
    const mode = modeSelect ? modeSelect.value : '';
    if (mode) {
      s += `&mode=${mode}`;
    } else {
      // Legacy: individual filter params
      if (zeros  && zeros.checked)  s += '&filter_zeros=true';
      if (spikes && spikes.checked) s += '&filter_spikes=true';
      if (spikeThreshold && parseFloat(spikeThreshold.value) > 0)
        s += `&spike_threshold=${spikeThreshold.value}`;
      if (fill   && fill.value !== 'none') s += `&fill_mode=${fill.value}`;
      if (gap)   s += `&max_gap=${gap.value}`;
      if (gapBreak) s += `&gap_break=${gapBreak.value}`;
    }
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
    // Gap-маркеры (_gap:true) → null → Chart.js рвёт линию.
    // Обычные null (пустой бакет) → пропускаем → Chart.js рисует плавно.
    const tubeData = [];
    for (const p of points) {
      if (!p.t) continue;
      const v = p.p_tube_avg;
      if (p._gap) {
        tubeData.push({ x: p.t, y: null });
      } else if (v !== null && v !== undefined) {
        tubeData.push({ x: p.t, y: v });
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
      spanGaps: false,
    });

    // ── 2) Кривая Pshl (шлейф) ──
    const lineData = [];
    for (const p of points) {
      if (!p.t) continue;
      const v = p.p_line_avg;
      if (p._gap) {
        lineData.push({ x: p.t, y: null });
      } else if (v !== null && v !== undefined) {
        lineData.push({ x: p.t, y: v });
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
      spanGaps: false,
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
      plugins: [purgeDurationPlugin, verticalEventLinePlugin, rangeHighlightPlugin],
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
          annotation: {
            annotations: buildUserAnnotations(),
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
                if (maskSelectionMode) {
                  // Перехватываем выделение для создания маски
                  const xMin = chart.scales.x.min;
                  const xMax = chart.scales.x.max;
                  const dtStart = new Date(xMin);
                  const dtEnd = new Date(xMax);
                  // Отменяем зум (откатываем к предыдущему состоянию)
                  const prev = zoomHistory.pop();
                  if (prev) {
                    chart.scales.x.options.min = prev.xMin;
                    chart.scales.x.options.max = prev.xMax;
                    chart.scales.y.options.min = prev.yMin;
                    chart.scales.y.options.max = prev.yMax;
                    chart.update('none');
                  } else {
                    chart.resetZoom();
                  }
                  // Выходим из режима выбора
                  exitMaskSelectionMode();
                  // Открываем форму маски с выбранным диапазоном
                  handleMaskSelection(dtStart, dtEnd);
                  return;
                }
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

  const COLOR_STORAGE_KEY = `sync-colors-well-${wellId}`;

  function saveColorSettings() {
    const evData = window.wellEventsData || {};
    const data = {
      eventColors: evData.eventColors || {},
      reagentColors: evData.reagentColors || {},
    };
    try { localStorage.setItem(COLOR_STORAGE_KEY, JSON.stringify(data)); } catch(e) {}
  }

  function restoreColorSettings() {
    let saved;
    try { saved = JSON.parse(localStorage.getItem(COLOR_STORAGE_KEY)); } catch(e) {}
    if (!saved) return;
    const evData = window.wellEventsData;
    if (!evData) return;
    if (saved.eventColors) {
      if (!evData.eventColors) evData.eventColors = {};
      Object.assign(evData.eventColors, saved.eventColors);
    }
    if (saved.reagentColors) {
      if (!evData.reagentColors) evData.reagentColors = {};
      Object.assign(evData.reagentColors, saved.reagentColors);
    }
  }

  // Восстанавливаем цвета сразу при загрузке скрипта
  restoreColorSettings();

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
        saveColorSettings();
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
        saveColorSettings();
        loadChart();  // Перезагружаем график с новыми цветами
      });
    });
  }

  // ══════════════════ Аннотации: загрузка и рендеринг ══════════════════

  async function loadAnnotations() {
    try {
      const resp = await fetch(`/api/chart-annotations?well_id=${wellId}`);
      if (!resp.ok) { chartAnnotations = []; return; }
      chartAnnotations = await resp.json();
      console.log('[sync_chart] Loaded', chartAnnotations.length, 'annotations');
    } catch (err) {
      console.warn('[sync_chart] Failed to load annotations:', err);
      chartAnnotations = [];
    }
  }

  /**
   * Строит объект аннотаций для chartjs-plugin-annotation.
   * point → вертикальная пунктирная линия с label
   * range → полупрозрачный box с label
   */
  function buildUserAnnotations() {
    const annotations = {};

    // Маски коррекции давления
    if (currentMaskZones.length > 0) {
      const MASK_COLORS = {
        hydrate: '#7b1fa2',
        comm_loss: '#e65100',
        sensor_fault: '#c62828',
        manual: '#37474f',
      };
      const MASK_LABELS = {
        hydrate: 'Гидрат',
        comm_loss: 'Потеря связи',
        sensor_fault: 'Неисправн.',
        manual: 'Ручная',
      };
      // Проверяем, включён ли режим масок (для разного стиля)
      const chartModeEl = document.getElementById('sync-chart-mode');
      const correctionActive = chartModeEl && chartModeEl.value === 'masked';

      currentMaskZones.forEach(function (zone) {
        const key = 'mask_zone_' + zone.id;
        const color = MASK_COLORS[zone.problem_type] || '#7b1fa2';
        const label = MASK_LABELS[zone.problem_type] || zone.problem_type;
        const sensor = zone.affected_sensor === 'p_tube' ? 'Ptr' : 'Pshl';
        const METHOD_SHORT = {
          delta_reconstruct: 'dP',
          median_1d: 'med1d',
          median_3d: 'med3d',
          interpolate: 'interp',
          exclude: 'excl',
        };
        const method = METHOD_SHORT[zone.correction_method] || zone.correction_method;
        annotations[key] = {
          type: 'box',
          xMin: zone.dt_start,
          xMax: zone.dt_end,
          backgroundColor: hexToRgba(color, correctionActive ? 0.15 : 0.08),
          borderColor: color,
          borderWidth: correctionActive ? 2 : 1,
          borderDash: correctionActive ? [] : [4, 4],
          label: {
            display: true,
            content: label + ' (' + sensor + ', ' + method + ')' + (correctionActive ? ' *' : ''),
            position: 'start',
            font: { size: 10, weight: '600' },
            color: color,
            backgroundColor: 'rgba(255,255,255,0.92)',
            padding: { top: 2, bottom: 2, left: 4, right: 4 },
          },
        };
      });
    }

    // Пользовательские аннотации
    if (annotationsVisible && chartAnnotations.length > 0) {
      chartAnnotations.forEach(function (ann) {
        const key = 'user_ann_' + ann.id;
        if (ann.ann_type === 'range' && ann.dt_end) {
          annotations[key] = {
            type: 'box',
            xMin: ann.dt_start,
            xMax: ann.dt_end,
            backgroundColor: hexToRgba(ann.color, 0.12),
            borderColor: ann.color,
            borderWidth: 1,
            borderDash: [4, 4],
            label: {
              display: true,
              content: ann.text,
              position: 'start',
              font: { size: 11, weight: 'bold' },
              color: ann.color,
              backgroundColor: 'rgba(255,255,255,0.85)',
              padding: { top: 2, bottom: 2, left: 4, right: 4 },
            },
          };
        } else {
          // point annotation → vertical line
          annotations[key] = {
            type: 'line',
            xMin: ann.dt_start,
            xMax: ann.dt_start,
            borderColor: ann.color,
            borderWidth: 2,
            borderDash: [6, 3],
            label: {
              display: true,
              content: ann.text,
              position: 'start',
              font: { size: 11, weight: 'bold' },
              color: ann.color,
              backgroundColor: 'rgba(255,255,255,0.85)',
              padding: { top: 2, bottom: 2, left: 4, right: 4 },
            },
          };
        }
      });
    }

    // Обнаруженные аномалии (временные зоны из авто-детекции)
    if (detectedAnomalyZones.length > 0) {
      detectedAnomalyZones.forEach(function (zone, idx) {
        const key = 'detected_' + idx;
        const sensor = zone.affected_sensor === 'p_tube' ? 'Ptr' : 'Pshl';
        const conf = Math.round((zone.confidence || 0) * 100);
        annotations[key] = {
          type: 'box',
          xMin: zone.dt_start,
          xMax: zone.dt_end,
          backgroundColor: 'rgba(255, 152, 0, 0.12)',
          borderColor: '#ff9800',
          borderWidth: 2,
          borderDash: [6, 3],
          label: {
            display: true,
            content: 'Аномалия (' + sensor + ', ' + conf + '%)',
            position: 'start',
            font: { size: 10, weight: '600' },
            color: '#e65100',
            backgroundColor: 'rgba(255,255,255,0.92)',
            padding: { top: 2, bottom: 2, left: 4, right: 4 },
          },
        };
      });
    }

    return annotations;
  }

  function hexToRgba(hex, alpha) {
    let c = hex.replace('#', '');
    if (c.length === 3) c = c[0]+c[0]+c[1]+c[1]+c[2]+c[2];
    const r = parseInt(c.substring(0,2), 16);
    const g = parseInt(c.substring(2,4), 16);
    const b = parseInt(c.substring(4,6), 16);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
  }

  /**
   * Обновляет аннотации на графике без перестроения
   */
  function refreshAnnotationsOnChart() {
    if (!syncChart) return;
    if (!syncChart.options.plugins.annotation) {
      syncChart.options.plugins.annotation = { annotations: {} };
    }
    // Удаляем старые user_ann_*, mask_zone_*, detected_*
    const existing = syncChart.options.plugins.annotation.annotations || {};
    Object.keys(existing).forEach(function (k) {
      if (k.startsWith('user_ann_') || k.startsWith('mask_zone_') || k.startsWith('detected_')) delete existing[k];
    });
    // Добавляем новые
    const userAnns = buildUserAnnotations();
    Object.assign(existing, userAnns);
    syncChart.options.plugins.annotation.annotations = existing;
    syncChart.update('none');
  }

  // ══════════════════ Загрузка данных ══════════════════

  async function loadChart(days, interval, start, end) {
    if (days !== undefined) currentDays = days;
    if (interval !== undefined) currentInterval = interval;
    if (start !== undefined) currentStart = start;
    if (end !== undefined) currentEnd = end;

    // 1) Получаем данные давления
    const filterStr = getFilterParams();
    let url;
    if (currentStart && currentEnd) {
      url = `/api/pressure/chart/${wellId}?interval=${currentInterval}&start=${encodeURIComponent(currentStart)}&end=${encodeURIComponent(currentEnd)}${filterStr}`;
    } else {
      url = `/api/pressure/chart/${wellId}?days=${currentDays}&interval=${currentInterval}${filterStr}`;
    }

    try {
      const resp = await fetch(url);
      if (!resp.ok) {
        console.error('[sync_chart] API error:', resp.status);
        return;
      }
      const json = await resp.json();
      const points = json.points || [];

      // 1.5) Маски коррекции давления
      currentMaskZones = json.mask_zones || [];
      if (json.masks_applied) {
        console.log('[sync_chart] Masks applied:', json.mask_corrected_points, 'points,', currentMaskZones.length, 'zones');
      }

      // 2) Получаем события и фильтруем по текущему периоду
      const rawEvData = window.wellEventsData || {};
      const visibleTypes = getVisibleEventTypes();

      // Фильтруем события по периоду
      let cutoffMs, endMs;
      if (currentStart && currentEnd) {
        cutoffMs = new Date(currentStart).getTime();
        endMs = new Date(currentEnd).getTime();
      } else {
        const cutoffTime = new Date();
        cutoffTime.setDate(cutoffTime.getDate() - currentDays);
        cutoffMs = cutoffTime.getTime();
        endMs = Date.now();
      }

      const filteredEvents = (rawEvData.timelineEvents || []).filter(ev => {
        if (!ev.t) return false;
        const evTime = new Date(normalizeTime(ev.t)).getTime();
        return evTime >= cutoffMs && evTime <= endMs;
      });

      const filteredInjections = (rawEvData.timelineInjections || []).filter(inj => {
        if (!inj.t) return false;
        const injTime = new Date(normalizeTime(inj.t)).getTime();
        return injTime >= cutoffMs && injTime <= endMs;
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

      // 4.5) Загружаем аннотации
      await loadAnnotations();

      // 5) Рендер (с привязкой оси X к диапазону давления)
      renderChart(datasets, result.pressureTimeMin, result.pressureTimeMax, result.maxQty);

      // 6) Инициализация Y-слайдера из данных
      initYSlider(points);

      // 7) Перезагружаем delta chart с теми же параметрами
      if (window.deltaChart && window.deltaChart.reload) {
        window.deltaChart.reload(currentDays, currentInterval, currentStart, currentEnd);
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

  // ══════════════════ Sync Zoom со всеми графиками ══════════════════

  /** Синхронизирует текущий zoom/pan с графиками ΔP и дебита */
  function syncZoomToDelta(chart) {
    const xScale = chart.scales.x;
    if (!xScale) return;
    // Синхронизация с ΔP
    if (window.deltaChart && window.deltaChart.syncZoom) {
      window.deltaChart.syncZoom(xScale.min, xScale.max);
    }
    // Синхронизация с графиком дебита
    if (window.flowRateChart && window.flowRateChart.syncZoom) {
      window.flowRateChart.syncZoom(xScale.min, xScale.max);
    }
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
    if (window.flowRateChart && window.flowRateChart.syncZoom) {
      window.flowRateChart.syncZoom(prev.xMin, prev.xMax);
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

    // Показываем кнопку «Аннотировать» в locked режиме
    const annPointBtn = document.getElementById('sync-annotate-point');
    if (annPointBtn) {
      annPointBtn.style.display = cursorMode === 'locked' ? 'inline-block' : 'none';
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
    hoverXValue = null;
    updateCursorModeUI();
    if (syncChart) syncChart.draw();  // убираем подсветку
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

  // ЛКМ на canvas:
  //  - В не-normal режиме → сброс курсора
  canvas.addEventListener('click', function (e) {
    if (cursorMode !== 'normal') {
      resetCursorMode();
      return;
    }
  });

  // Двойной клик на canvas → показать popup с ±10 сырыми замерами из БД
  canvas.addEventListener('dblclick', function (e) {
    if (cursorMode !== 'normal') return;

    // Получаем время клика по X-оси графика
    if (!syncChart) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const xScale = syncChart.scales.x;
    if (!xScale) return;

    const xValue = xScale.getValueForPixel(x);
    if (!xValue) return;

    // xValue уже в UTC+5 (так данные загружены в chart)
    const clickDate = new Date(xValue);
    const isoStr = clickDate.getFullYear() + '-'
      + String(clickDate.getMonth() + 1).padStart(2, '0') + '-'
      + String(clickDate.getDate()).padStart(2, '0') + 'T'
      + String(clickDate.getHours()).padStart(2, '0') + ':'
      + String(clickDate.getMinutes()).padStart(2, '0') + ':'
      + String(clickDate.getSeconds()).padStart(2, '0');

    showRawNearbyPopup(wellId, isoStr, e.clientX, e.clientY);
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

  // ══════════════════ Аннотации: создание, toggle, удаление ══════════════════

  /**
   * Форматирует ms timestamp → ISO naive строку (как в chart data)
   */
  function msToNaiveISO(ms) {
    const d = new Date(ms);
    return d.getFullYear() + '-'
      + String(d.getMonth() + 1).padStart(2, '0') + '-'
      + String(d.getDate()).padStart(2, '0') + 'T'
      + String(d.getHours()).padStart(2, '0') + ':'
      + String(d.getMinutes()).padStart(2, '0') + ':'
      + String(d.getSeconds()).padStart(2, '0');
  }

  /**
   * Открывает модалку создания аннотации
   * @param {string} annType — "point" | "range"
   * @param {number} dtStartMs — ms timestamp начала
   * @param {number|null} dtEndMs — ms timestamp конца (для range)
   */
  function openAnnotationModal(annType, dtStartMs, dtEndMs) {
    const modal = document.getElementById('annotation-modal');
    if (!modal) return;

    const timeLabel = document.getElementById('annotation-time-label');
    const textInput = document.getElementById('annotation-text-input');
    const colorInput = document.getElementById('annotation-color-input');

    // Форматируем время для показа
    const fmtTime = function (ms) {
      const d = new Date(ms);
      return String(d.getDate()).padStart(2, '0') + '.'
        + String(d.getMonth() + 1).padStart(2, '0') + ' '
        + String(d.getHours()).padStart(2, '0') + ':'
        + String(d.getMinutes()).padStart(2, '0');
    };

    if (timeLabel) {
      if (annType === 'range' && dtEndMs) {
        timeLabel.textContent = fmtTime(dtStartMs) + ' — ' + fmtTime(dtEndMs);
      } else {
        timeLabel.textContent = fmtTime(dtStartMs);
      }
    }

    if (textInput) textInput.value = '';
    if (colorInput) colorInput.value = '#ff9800';

    modal.style.display = 'flex';
    if (textInput) textInput.focus();

    // Сохраняем контекст для submit
    modal._annType = annType;
    modal._dtStartMs = dtStartMs;
    modal._dtEndMs = dtEndMs;
  }

  /**
   * Отправляет аннотацию на сервер
   */
  async function submitAnnotation() {
    const modal = document.getElementById('annotation-modal');
    if (!modal) return;

    const textInput = document.getElementById('annotation-text-input');
    const colorInput = document.getElementById('annotation-color-input');
    const text = (textInput ? textInput.value : '').trim();
    if (!text) { if (textInput) textInput.focus(); return; }

    const body = {
      well_id: parseInt(wellId),
      ann_type: modal._annType || 'point',
      dt_start: msToNaiveISO(modal._dtStartMs),
      dt_end: modal._dtEndMs ? msToNaiveISO(modal._dtEndMs) : null,
      text: text,
      color: colorInput ? colorInput.value : '#ff9800',
    };

    try {
      const resp = await fetch('/api/chart-annotations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        console.error('[sync_chart] Failed to create annotation:', resp.status);
        return;
      }
      const created = await resp.json();
      chartAnnotations.push(created);
      refreshAnnotationsOnChart();
      updateAnnotationList();
      console.log('[sync_chart] Annotation created:', created.id);
    } catch (err) {
      console.error('[sync_chart] Error creating annotation:', err);
    }

    modal.style.display = 'none';
    resetCursorMode();
  }

  /**
   * Удаляет аннотацию
   */
  async function deleteAnnotation(annId) {
    try {
      const resp = await fetch('/api/chart-annotations/' + annId, { method: 'DELETE' });
      if (!resp.ok) {
        console.error('[sync_chart] Failed to delete annotation:', resp.status);
        return;
      }
      chartAnnotations = chartAnnotations.filter(function (a) { return a.id !== annId; });
      refreshAnnotationsOnChart();
      updateAnnotationList();
      console.log('[sync_chart] Annotation deleted:', annId);
    } catch (err) {
      console.error('[sync_chart] Error deleting annotation:', err);
    }
  }

  /**
   * Обновляет список аннотаций в модалке
   */
  function updateAnnotationList() {
    const listEl = document.getElementById('annotation-list-body');
    if (!listEl) return;
    if (chartAnnotations.length === 0) {
      listEl.innerHTML = '<div style="color:#999; font-size:12px; text-align:center; padding:8px;">Нет аннотаций</div>';
      return;
    }
    let html = '';
    chartAnnotations.forEach(function (ann) {
      const fmtTime = function (iso) {
        if (!iso) return '';
        const d = new Date(iso);
        return String(d.getDate()).padStart(2, '0') + '.'
          + String(d.getMonth() + 1).padStart(2, '0') + ' '
          + String(d.getHours()).padStart(2, '0') + ':'
          + String(d.getMinutes()).padStart(2, '0');
      };
      const timeStr = ann.ann_type === 'range' && ann.dt_end
        ? fmtTime(ann.dt_start) + ' — ' + fmtTime(ann.dt_end)
        : fmtTime(ann.dt_start);
      html += '<div style="display:flex; align-items:center; gap:6px; padding:4px 0; border-bottom:1px solid #eee;">'
        + '<span style="width:12px; height:12px; border-radius:2px; background:' + ann.color + '; flex-shrink:0;"></span>'
        + '<span style="flex:1; font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="' + ann.text.replace(/"/g, '&quot;') + '">' + ann.text + '</span>'
        + '<span style="font-size:10px; color:#999; white-space:nowrap;">' + timeStr + '</span>'
        + '<button onclick="window._deleteAnnotation(' + ann.id + ')" style="border:none; background:none; color:#dc3545; cursor:pointer; font-size:14px; padding:0 2px;" title="Удалить">&times;</button>'
        + '</div>';
    });
    listEl.innerHTML = html;
  }

  // Expose for onclick handlers in HTML
  window._deleteAnnotation = deleteAnnotation;
  window._submitAnnotation = submitAnnotation;

  // Toggle annotations visibility
  const annToggleBtn = document.getElementById('sync-annotations-toggle');
  if (annToggleBtn) {
    annToggleBtn.addEventListener('click', function () {
      annotationsVisible = !annotationsVisible;
      annToggleBtn.style.background = annotationsVisible ? '#ff9800' : '#fff';
      annToggleBtn.style.color = annotationsVisible ? '#fff' : '#333';
      refreshAnnotationsOnChart();
    });
  }

  // Annotate from locked mode (point)
  const annFromLockedBtn = document.getElementById('sync-annotate-point');
  if (annFromLockedBtn) {
    annFromLockedBtn.addEventListener('click', function () {
      if (lockedXValue) {
        openAnnotationModal('point', lockedXValue, null);
      }
    });
  }

  // Annotate from range menu
  const annFromRangeBtn = document.getElementById('sync-range-annotate');
  if (annFromRangeBtn) {
    annFromRangeBtn.addEventListener('click', function () {
      if (rangeStart && rangeEnd) {
        openAnnotationModal('range', rangeStart, rangeEnd);
      }
    });
  }

  // Annotation modal cancel
  const annCancelBtn = document.getElementById('annotation-modal-cancel');
  if (annCancelBtn) {
    annCancelBtn.addEventListener('click', function () {
      document.getElementById('annotation-modal').style.display = 'none';
    });
  }

  // Annotation modal save
  const annSaveBtn = document.getElementById('annotation-modal-save');
  if (annSaveBtn) {
    annSaveBtn.addEventListener('click', function () {
      submitAnnotation();
    });
  }

  // Annotation modal list toggle
  const annListToggle = document.getElementById('annotation-list-toggle');
  if (annListToggle) {
    annListToggle.addEventListener('click', function () {
      const listPanel = document.getElementById('annotation-list-panel');
      if (listPanel) {
        const visible = listPanel.style.display !== 'none';
        listPanel.style.display = visible ? 'none' : 'block';
        annListToggle.textContent = visible ? 'Показать список' : 'Скрыть список';
        if (!visible) updateAnnotationList();
      }
    });
  }

  // Enter key in text input → save
  const annTextInput = document.getElementById('annotation-text-input');
  if (annTextInput) {
    annTextInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); submitAnnotation(); }
    });
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
      b.style.background = '#f5f5f5';
      b.style.color = '#333';
      b.style.borderColor = '#90a4ae';
    });
    activeBtn.style.background = '#1565c0';
    activeBtn.style.color = 'white';
    activeBtn.style.borderColor = '#1565c0';
  }

  document.querySelectorAll('[data-sync-days]').forEach(btn => {
    btn.addEventListener('click', function () {
      const days = parseInt(this.dataset.syncDays);
      // Сброс ручного диапазона при выборе пресета
      currentStart = null;
      currentEnd = null;
      if (window.updateAllCharts) {
        window.updateAllCharts(days, undefined, null, null);
      } else {
        setActiveBtn('sync-days', this);
        loadChart(days, undefined, null, null);
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

  // ── Ручной выбор периода по дате и времени ──
  const syncDateApply = document.getElementById('sync-date-apply');
  if (syncDateApply) {
    syncDateApply.addEventListener('click', function () {
      const fromEl = document.getElementById('sync-date-from');
      const toEl = document.getElementById('sync-date-to');
      const from = fromEl ? fromEl.value : '';  // "2025-02-17T08:00"
      const to = toEl ? toEl.value : '';
      if (!from) return;

      // datetime-local даёт "YYYY-MM-DDTHH:MM", добавляем секунды
      const startISO = from.length === 16 ? from + ':00' : from;
      const endISO = to ? (to.length === 16 ? to + ':00' : to) : null;

      if (!endISO) return;

      const dateFrom = new Date(startISO);
      const dateTo = new Date(endISO);
      if (dateTo.getTime() <= dateFrom.getTime()) return;

      // Вычисляем приблизительный days для совместимости
      const days = Math.max(1, Math.ceil((dateTo - dateFrom) / 86400000));

      // Сбрасываем активную кнопку периода
      document.querySelectorAll('[data-sync-days]').forEach(b => {
        b.style.background = '#f5f5f5';
        b.style.color = '#333';
        b.style.borderColor = '#90a4ae';
      });
      if (window.updateAllCharts) {
        window.updateAllCharts(days, undefined, startISO, endISO);
      } else {
        loadChart(days, undefined, startISO, endISO);
      }
    });
  }

  // ── Кнопка «Сутки» — выбрать календарный день (00:00–23:59) ──
  const syncDayApply = document.getElementById('sync-day-apply');
  if (syncDayApply) {
    syncDayApply.addEventListener('click', function () {
      const dayEl = document.getElementById('sync-date-day');
      const dayVal = dayEl ? dayEl.value : '';  // "2025-02-17"
      if (!dayVal) return;

      const startISO = dayVal + 'T00:00:00';
      const endISO = dayVal + 'T23:59:59';

      // Сбрасываем активную кнопку периода
      document.querySelectorAll('[data-sync-days]').forEach(b => {
        b.style.background = '#f5f5f5';
        b.style.color = '#333';
        b.style.borderColor = '#90a4ae';
      });
      if (window.updateAllCharts) {
        window.updateAllCharts(1, undefined, startISO, endISO);
      } else {
        loadChart(1, undefined, startISO, endISO);
      }
    });
  }

  // ══════════════════ Сохранение/восстановление настроек фильтров ══════════════════

  const FILTER_STORAGE_KEY = `sync-filters-well-${wellId}`;

  /** Список ID контролов фильтров и их тип */
  const FILTER_CONTROLS = [
    { id: 'sync-filter-zeros',           type: 'checkbox' },
    { id: 'sync-filter-spikes',          type: 'checkbox' },
    { id: 'sync-filter-spike-threshold', type: 'number'   },
    { id: 'sync-filter-fill-mode',       type: 'select'   },
    { id: 'sync-filter-max-gap',         type: 'number'   },
    { id: 'sync-filter-gap-break',       type: 'number'   },
    { id: 'sync-apply-masks',            type: 'checkbox' },
  ];

  function saveFilterSettings() {
    const settings = {};
    for (const ctrl of FILTER_CONTROLS) {
      const el = document.getElementById(ctrl.id);
      if (!el) continue;
      settings[ctrl.id] = ctrl.type === 'checkbox' ? el.checked : el.value;
    }
    try { localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(settings)); } catch(e) {}
  }

  function restoreFilterSettings() {
    let settings;
    try { settings = JSON.parse(localStorage.getItem(FILTER_STORAGE_KEY)); } catch(e) {}
    if (!settings) return;
    for (const ctrl of FILTER_CONTROLS) {
      const el = document.getElementById(ctrl.id);
      if (!el || !(ctrl.id in settings)) continue;
      if (ctrl.type === 'checkbox') {
        el.checked = !!settings[ctrl.id];
      } else {
        el.value = settings[ctrl.id];
      }
    }
  }

  // Восстанавливаем при загрузке
  restoreFilterSettings();

  // ══════════════════ Обработчики собственных фильтров ══════════════════

  /** Общий обработчик: сохранить настройки + перезагрузить график */
  function onFilterChange() {
    saveFilterSettings();
    loadChart();
  }

  ['sync-filter-zeros', 'sync-filter-spikes'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', onFilterChange);
  });

  ['sync-filter-fill-mode'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', onFilterChange);
  });

  // Числовые поля: перезагружаем по change (не input, чтобы не спамить запросами)
  ['sync-filter-spike-threshold', 'sync-filter-max-gap', 'sync-filter-gap-break'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', onFilterChange);
  });

  // Чекбокс масок коррекции
  const chartModeSelect = document.getElementById('sync-chart-mode');
  if (chartModeSelect) {
    chartModeSelect.addEventListener('change', onFilterChange);
  }

  // ══════════════════ Управление масками ══════════════════

  const masksModal = document.getElementById('masks-modal');
  const masksManageBtn = document.getElementById('sync-masks-manage');
  const masksCloseBtn = document.getElementById('masks-modal-close');
  const masksListEl = document.getElementById('masks-list');
  const masksFormEl = document.getElementById('masks-form');
  const masksDetectBtn = document.getElementById('masks-detect-btn');
  const masksCreateBtn = document.getElementById('masks-create-btn');
  const masksFormSaveBtn = document.getElementById('masks-form-save');
  const masksFormCancelBtn = document.getElementById('masks-form-cancel');
  const masksDetectResults = document.getElementById('masks-detect-results');
  const masksSelectBtn = document.getElementById('masks-select-chart');
  const masksDetectRangeBtn = document.getElementById('masks-detect-range');

  // ── Режим выбора участка на графике ──
  let maskSelectionCallback = null;  // 'create' или 'detect'

  function enterMaskSelectionMode(callback) {
    maskSelectionMode = true;
    maskSelectionCallback = callback;
    // Включаем drag (даже если pan mode) и меняем цвет рамки на фиолетовый
    if (syncChart) {
      syncChart.options.plugins.zoom.zoom.drag.enabled = true;
      syncChart.options.plugins.zoom.zoom.drag.backgroundColor = 'rgba(123, 31, 162, 0.15)';
      syncChart.options.plugins.zoom.zoom.drag.borderColor = 'rgba(123, 31, 162, 0.6)';
      // Временно отключаем pan чтобы drag работал
      syncChart.options.plugins.zoom.pan.enabled = false;
      syncChart.update('none');
    }
    // Меняем курсор на crosshair
    canvas.style.cursor = 'crosshair';
    // Скрываем модалку, показываем подсказку
    if (masksModal) masksModal.style.display = 'none';
    showMaskSelectionHint();
  }

  function exitMaskSelectionMode() {
    maskSelectionMode = false;
    maskSelectionCallback = null;
    // Возвращаем стандартные настройки drag/pan
    if (syncChart) {
      syncChart.options.plugins.zoom.zoom.drag.enabled = !panMode;
      syncChart.options.plugins.zoom.zoom.drag.backgroundColor = 'rgba(13, 110, 253, 0.1)';
      syncChart.options.plugins.zoom.zoom.drag.borderColor = 'rgba(13, 110, 253, 0.4)';
      syncChart.options.plugins.zoom.pan.enabled = panMode;
      syncChart.update('none');
    }
    canvas.style.cursor = '';
    hideMaskSelectionHint();
  }

  // Escape отменяет режим выбора маски
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && maskSelectionMode) {
      exitMaskSelectionMode();
    }
  });

  function showMaskSelectionHint() {
    let hint = document.getElementById('mask-selection-hint');
    if (!hint) {
      hint = document.createElement('div');
      hint.id = 'mask-selection-hint';
      hint.style.cssText = 'position:fixed; top:12px; left:50%; transform:translateX(-50%); z-index:10000; background:#7b1fa2; color:white; padding:10px 24px; border-radius:8px; font-size:13px; font-weight:600; box-shadow:0 4px 16px rgba(0,0,0,0.3); display:flex; align-items:center; gap:12px;';
      hint.innerHTML = '<span>Выделите участок на графике мышью</span>' +
        '<button id="mask-selection-cancel" style="background:rgba(255,255,255,0.25); border:none; color:white; padding:4px 12px; border-radius:4px; font-size:12px; cursor:pointer;">Отмена</button>';
      document.body.appendChild(hint);
      document.getElementById('mask-selection-cancel').addEventListener('click', () => {
        exitMaskSelectionMode();
      });
    }
    hint.style.display = 'flex';
  }

  function hideMaskSelectionHint() {
    const hint = document.getElementById('mask-selection-hint');
    if (hint) hint.style.display = 'none';
  }

  function handleMaskSelection(dtStart, dtEnd) {
    // Форматируем даты для datetime-local inputs
    const fmt = (d) => {
      const y = d.getFullYear();
      const mo = String(d.getMonth() + 1).padStart(2, '0');
      const dd = String(d.getDate()).padStart(2, '0');
      const hh = String(d.getHours()).padStart(2, '0');
      const mm = String(d.getMinutes()).padStart(2, '0');
      return `${y}-${mo}-${dd}T${hh}:${mm}`;
    };

    const callback = maskSelectionCallback;

    if (callback === 'detect') {
      // Авто-детекция на выбранном участке
      runDetectOnRange(dtStart, dtEnd);
    } else {
      // Создание маски — открыть форму с pre-filled диапазоном
      if (masksModal) masksModal.style.display = 'flex';
      openMaskForm({
        dt_start: fmt(dtStart),
        dt_end: fmt(dtEnd),
        affected_sensor: 'p_tube',
        correction_method: 'delta_reconstruct',
        problem_type: 'hydrate',
      });
    }
  }

  async function runDetectOnRange(dtStart, dtEnd) {
    // Вычисляем days из диапазона (минимум 1)
    const msRange = dtEnd.getTime() - dtStart.getTime();
    const daysRange = Math.max(1, Math.ceil(msRange / (1000 * 60 * 60 * 24)));

    if (masksModal) masksModal.style.display = 'flex';
    if (masksDetectResults) {
      masksDetectResults.style.display = 'block';
      masksDetectResults.innerHTML = '<div style="color:#999; font-size:12px;">Анализ выбранного участка...</div>';
    }

    try {
      const resp = await fetch(`/api/pressure-masks/detect?well_id=${wellId}&days=${daysRange}`);
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const json = await resp.json();
      let anomalies = json.anomalies || [];

      // Фильтруем аномалии по выбранному диапазону
      anomalies = anomalies.filter(a => {
        const aStart = new Date(a.dt_start).getTime();
        const aEnd = new Date(a.dt_end).getTime();
        return aEnd >= dtStart.getTime() && aStart <= dtEnd.getTime();
      });

      if (anomalies.length === 0) {
        masksDetectResults.innerHTML = '<div style="color:#4caf50; font-size:12px; padding:8px;">Аномалий не обнаружено на выбранном участке.</div>';
        detectedAnomalyZones = [];
        refreshAnnotationsOnChart();
        return;
      }

      // Отображаем результаты (переиспользуем тот же формат)
      masksDetectResults.innerHTML = '<div style="font-size:12px; font-weight:600; color:#7b1fa2; margin-bottom:8px;">Найдено аномалий: ' + anomalies.length + '</div>' +
        anomalies.map((a, i) => {
          const dtS = a.dt_start.replace('T', ' ').slice(0, 16);
          const dtE = a.dt_end.replace('T', ' ').slice(0, 16);
          const sensor = a.affected_sensor === 'p_tube' ? 'Устье' : 'Шлейф';
          return '<div style="background:#f3e5f5; padding:8px 10px; border-radius:6px; margin-bottom:6px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">' +
            `<div style="font-size:12px;"><strong>${dtS}</strong> — <strong>${dtE}</strong> (${a.duration_hours}ч)<br>` +
            `Датчик: <strong>${sensor}</strong>, уверенность: ${Math.round(a.confidence * 100)}%, отклонение: ${a.dp_deviation} атм</div>` +
            `<button data-detect-apply="${i}" style="padding:4px 12px; background:#7b1fa2; color:white; border:none; border-radius:4px; font-size:11px; cursor:pointer; white-space:nowrap;">Создать маску</button>` +
          '</div>';
        }).join('');

      detectedAnomalyZones = anomalies.map(a => ({
        dt_start: a.dt_start, dt_end: a.dt_end,
        affected_sensor: a.affected_sensor, confidence: a.confidence,
      }));
      refreshAnnotationsOnChart();

      masksDetectResults.querySelectorAll('[data-detect-apply]').forEach(btn => {
        btn.addEventListener('click', () => {
          const idx = parseInt(btn.dataset.detectApply);
          const a = anomalies[idx];
          openMaskForm({
            dt_start: a.dt_start, dt_end: a.dt_end,
            affected_sensor: a.affected_sensor,
            correction_method: a.suggested_method,
            problem_type: 'hydrate',
          });
        });
      });
    } catch (err) {
      console.error('[sync_chart] Range detect error:', err);
      masksDetectResults.innerHTML = '<div style="color:#c62828; font-size:12px;">Ошибка детекции: ' + (err.message || err) + '</div>';
    }
  }

  if (masksManageBtn && masksModal) {
    masksManageBtn.addEventListener('click', () => {
      masksModal.style.display = 'flex';
      loadMasksList();
    });
  }
  function closeMasksModal() {
    masksModal.style.display = 'none';
    // Очищаем подсветку обнаруженных аномалий при закрытии
    if (detectedAnomalyZones.length > 0) {
      detectedAnomalyZones = [];
      refreshAnnotationsOnChart();
    }
  }
  if (masksCloseBtn && masksModal) {
    masksCloseBtn.addEventListener('click', closeMasksModal);
  }
  if (masksModal) {
    masksModal.addEventListener('click', (e) => {
      if (e.target === masksModal) closeMasksModal();
    });
  }

  async function loadMasksList() {
    if (!masksListEl) return;
    masksListEl.innerHTML = '<div style="color:#999; font-size:13px;">Загрузка...</div>';
    try {
      const resp = await fetch(`/api/pressure-masks?well_id=${wellId}`);
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const masks = await resp.json();
      if (masks.length === 0) {
        masksListEl.innerHTML = '<div style="color:#999; font-size:13px;">Нет масок для этой скважины.</div>';
        return;
      }
      const METHOD_LABELS = {
        delta_reconstruct: 'ΔP реконструкция',
        median_1d: 'Медиана 1д',
        median_3d: 'Медиана 3д',
        interpolate: 'Интерполяция',
        exclude: 'Исключить',
      };
      const TYPE_LABELS = {
        hydrate: 'Гидрат',
        degradation: 'Деградация',
        purge: 'Продувка',
        comm_loss: 'Потеря связи',
        sensor_fault: 'Неисправн.',
        manual: 'Ручная',
      };
      const TYPE_COLORS = {
        hydrate: '#7b1fa2',
        degradation: '#ff6f00',
        purge: '#2e7d32',
        comm_loss: '#e65100',
        sensor_fault: '#c62828',
        manual: '#37474f',
      };
      masksListEl.innerHTML = '<table style="width:100%; border-collapse:collapse; font-size:12px;">' +
        '<tr style="background:#f5f5f5; font-weight:600; color:#555;">' +
        '<td style="padding:4px 6px;">Период</td>' +
        '<td style="padding:4px 6px;">Тип</td>' +
        '<td style="padding:4px 6px;">Датчик</td>' +
        '<td style="padding:4px 6px;">Метод</td>' +
        '<td style="padding:4px 6px;">Причина</td>' +
        '<td style="padding:4px 6px; text-align:center;">Стат.</td>' +
        '<td style="padding:4px 6px; text-align:right;">Действия</td>' +
        '</tr>' +
        masks.map(m => {
          const dtS = m.dt_start ? m.dt_start.replace('T', ' ').slice(0, 16) : '?';
          const dtE = m.dt_end ? m.dt_end.replace('T', ' ').slice(0, 16) : '?';
          const typeColor = TYPE_COLORS[m.problem_type] || '#666';
          const verBadge = m.is_verified
            ? '<span title="Верифицирована" style="color:#2e7d32;">V</span>'
            : (m.source && m.source !== 'manual'
              ? '<span title="Ожидает верификации" style="color:#e65100;">?</span>'
              : '');
          const activeBadge = m.is_active ? '' : '<span style="color:#999;" title="Отключена">off</span>';
          return '<tr style="border-bottom:1px solid #eee;">' +
            `<td style="padding:4px 6px; white-space:nowrap;">${dtS}<br>${dtE}</td>` +
            `<td style="padding:4px 6px; color:${typeColor}; font-weight:600;">${TYPE_LABELS[m.problem_type] || m.problem_type}</td>` +
            `<td style="padding:4px 6px;">${m.affected_sensor === 'p_tube' ? 'Устье' : 'Шлейф'}</td>` +
            `<td style="padding:4px 6px;">${METHOD_LABELS[m.correction_method] || m.correction_method}</td>` +
            `<td style="padding:4px 6px; max-width:120px; overflow:hidden; text-overflow:ellipsis;">${m.reason || ''}</td>` +
            `<td style="padding:4px 6px; text-align:center; white-space:nowrap;">` +
              `<button data-mask-toggle="${m.id}" style="background:none; border:none; cursor:pointer; font-size:14px;" title="Вкл/Выкл">${m.is_active ? '✅' : '⬜'}</button>` +
              ` ${verBadge}${activeBadge}` +
            `</td>` +
            `<td style="padding:4px 6px; text-align:right; white-space:nowrap;">` +
              `<button data-mask-edit="${m.id}" style="background:none; border:1px solid #1565c0; color:#1565c0; border-radius:4px; padding:2px 8px; font-size:11px; cursor:pointer; margin-right:4px;">Ред.</button>` +
              `<button data-mask-delete="${m.id}" style="background:none; border:1px solid #c62828; color:#c62828; border-radius:4px; padding:2px 8px; font-size:11px; cursor:pointer;">Уд.</button>` +
            `</td>` +
          '</tr>';
        }).join('') +
        '</table>';

      // Обработчики кнопок
      masksListEl.querySelectorAll('[data-mask-toggle]').forEach(btn => {
        btn.addEventListener('click', async () => {
          const id = btn.dataset.maskToggle;
          await fetch(`/api/pressure-masks/${id}/toggle`, { method: 'PATCH' });
          loadMasksList();
          loadChart();
        });
      });
      masksListEl.querySelectorAll('[data-mask-edit]').forEach(btn => {
        btn.addEventListener('click', () => {
          const id = btn.dataset.maskEdit;
          const mask = masks.find(m => m.id == id);
          if (mask) openMaskForm(mask);
        });
      });
      masksListEl.querySelectorAll('[data-mask-delete]').forEach(btn => {
        btn.addEventListener('click', async () => {
          const id = btn.dataset.maskDelete;
          if (!confirm('Удалить маску #' + id + '?')) return;
          await fetch(`/api/pressure-masks/${id}`, { method: 'DELETE' });
          loadMasksList();
          loadChart();
        });
      });
    } catch (err) {
      console.error('[sync_chart] Failed to load masks:', err);
      masksListEl.innerHTML = '<div style="color:#c62828; font-size:13px;">Ошибка загрузки масок</div>';
    }
  }

  function openMaskForm(mask) {
    if (!masksFormEl) return;
    const titleEl = document.getElementById('masks-form-title');
    const editIdEl = document.getElementById('mask-edit-id');

    if (mask) {
      titleEl.textContent = 'Редактировать маску #' + mask.id;
      editIdEl.value = mask.id;
      document.getElementById('mask-dt-start').value = mask.dt_start ? mask.dt_start.slice(0, 16) : '';
      document.getElementById('mask-dt-end').value = mask.dt_end ? mask.dt_end.slice(0, 16) : '';
      document.getElementById('mask-sensor').value = mask.affected_sensor || 'p_tube';
      document.getElementById('mask-method').value = mask.correction_method || 'delta_reconstruct';
      document.getElementById('mask-problem-type').value = mask.problem_type || 'manual';
      document.getElementById('mask-manual-dp').value = mask.manual_delta_p || '';
      document.getElementById('mask-reason').value = mask.reason || '';
    } else {
      titleEl.textContent = 'Новая маска';
      editIdEl.value = '';
      document.getElementById('mask-dt-start').value = '';
      document.getElementById('mask-dt-end').value = '';
      document.getElementById('mask-sensor').value = 'p_tube';
      document.getElementById('mask-method').value = 'delta_reconstruct';
      document.getElementById('mask-problem-type').value = 'hydrate';
      document.getElementById('mask-manual-dp').value = '';
      document.getElementById('mask-reason').value = '';
    }
    masksFormEl.style.display = 'block';
  }

  if (masksCreateBtn) {
    masksCreateBtn.addEventListener('click', () => openMaskForm(null));
  }
  // Кнопка "Выбрать участок" — ручное создание маски по выделению на графике
  if (masksSelectBtn) {
    masksSelectBtn.addEventListener('click', () => enterMaskSelectionMode('create'));
  }
  // Кнопка "Детекция на участке" — авто-детекция на выбранном участке
  if (masksDetectRangeBtn) {
    masksDetectRangeBtn.addEventListener('click', () => enterMaskSelectionMode('detect'));
  }
  if (masksFormCancelBtn) {
    masksFormCancelBtn.addEventListener('click', () => {
      masksFormEl.style.display = 'none';
    });
  }
  if (masksFormSaveBtn) {
    masksFormSaveBtn.addEventListener('click', async () => {
      const editId = document.getElementById('mask-edit-id').value;
      const data = {
        well_id: parseInt(wellId),
        dt_start: document.getElementById('mask-dt-start').value + ':00',
        dt_end: document.getElementById('mask-dt-end').value + ':00',
        affected_sensor: document.getElementById('mask-sensor').value,
        correction_method: document.getElementById('mask-method').value,
        problem_type: document.getElementById('mask-problem-type').value,
        reason: document.getElementById('mask-reason').value || null,
      };
      const manualDp = document.getElementById('mask-manual-dp').value;
      if (manualDp) data.manual_delta_p = parseFloat(manualDp);

      try {
        let resp;
        if (editId) {
          resp = await fetch(`/api/pressure-masks/${editId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
          });
        } else {
          resp = await fetch('/api/pressure-masks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
          });
        }
        if (!resp.ok) {
          const err = await resp.json();
          alert('Ошибка: ' + (err.detail || resp.status));
          return;
        }
        masksFormEl.style.display = 'none';
        loadMasksList();
        // Автоматически переключаем в режим масок и перезагружаем график
        if (chartModeSelect && chartModeSelect.value !== 'masked') {
          chartModeSelect.value = 'masked';
        }
        loadChart();
      } catch (err) {
        console.error('[sync_chart] Failed to save mask:', err);
        alert('Ошибка сохранения маски');
      }
    });
  }

  // Авто-детекция аномалий
  if (masksDetectBtn) {
    masksDetectBtn.addEventListener('click', async () => {
      if (!masksDetectResults) return;
      masksDetectResults.style.display = 'block';
      masksDetectResults.innerHTML = '<div style="color:#999; font-size:12px;">Анализ данных...</div>';
      try {
        const resp = await fetch(`/api/pressure-masks/detect?well_id=${wellId}&days=${currentDays || 30}`);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const json = await resp.json();
        const anomalies = json.anomalies || [];
        if (anomalies.length === 0) {
          masksDetectResults.innerHTML = '<div style="color:#4caf50; font-size:12px; padding:8px;">Аномалий не обнаружено.</div>';
          // Очищаем подсветку аномалий на графике
          detectedAnomalyZones = [];
          refreshAnnotationsOnChart();
          return;
        }
        masksDetectResults.innerHTML = '<div style="font-size:12px; font-weight:600; color:#7b1fa2; margin-bottom:8px;">Найдено аномалий: ' + anomalies.length + '</div>' +
          anomalies.map((a, i) => {
            const dtS = a.dt_start.replace('T', ' ').slice(0, 16);
            const dtE = a.dt_end.replace('T', ' ').slice(0, 16);
            const sensor = a.affected_sensor === 'p_tube' ? 'Устье' : 'Шлейф';
            return '<div style="background:#f3e5f5; padding:8px 10px; border-radius:6px; margin-bottom:6px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">' +
              `<div style="font-size:12px;"><strong>${dtS}</strong> — <strong>${dtE}</strong> (${a.duration_hours}ч)<br>` +
              `Датчик: <strong>${sensor}</strong>, уверенность: ${Math.round(a.confidence * 100)}%, отклонение: ${a.dp_deviation} атм</div>` +
              `<button data-detect-apply="${i}" style="padding:4px 12px; background:#7b1fa2; color:white; border:none; border-radius:4px; font-size:11px; cursor:pointer; white-space:nowrap;">Создать маску</button>` +
            '</div>';
          }).join('');

        // Подсвечиваем найденные аномалии на графике
        detectedAnomalyZones = anomalies.map(a => ({
          dt_start: a.dt_start,
          dt_end: a.dt_end,
          affected_sensor: a.affected_sensor,
          confidence: a.confidence,
        }));
        refreshAnnotationsOnChart();

        masksDetectResults.querySelectorAll('[data-detect-apply]').forEach(btn => {
          btn.addEventListener('click', () => {
            const idx = parseInt(btn.dataset.detectApply);
            const a = anomalies[idx];
            openMaskForm({
              dt_start: a.dt_start,
              dt_end: a.dt_end,
              affected_sensor: a.affected_sensor,
              correction_method: a.suggested_method,
              problem_type: 'hydrate',
            });
          });
        });
      } catch (err) {
        console.error('[sync_chart] Detection error:', err);
        masksDetectResults.innerHTML = '<div style="color:#c62828; font-size:12px;">Ошибка детекции: ' + (err.message || err) + '</div>';
      }
    });
  }

  // ══════════════════ Авто-детекция + создание масок ══════════════════

  const autoDetectBtn = document.getElementById('masks-auto-detect-btn');
  const autoDetectDays = document.getElementById('masks-auto-detect-days');
  const autoDetectStatus = document.getElementById('masks-auto-detect-status');

  if (autoDetectBtn) {
    autoDetectBtn.addEventListener('click', async () => {
      const days = autoDetectDays ? parseInt(autoDetectDays.value) : 7;
      autoDetectBtn.disabled = true;
      if (autoDetectStatus) {
        autoDetectStatus.textContent = 'Анализ...';
        autoDetectStatus.style.color = '#666';
      }
      try {
        const resp = await fetch('/api/pressure-masks/auto-detect', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ well_id: parseInt(wellId), days }),
        });
        if (!resp.ok) {
          const errBody = await resp.text();
          throw new Error('HTTP ' + resp.status + ': ' + errBody);
        }
        const json = await resp.json();
        if (autoDetectStatus) {
          autoDetectStatus.textContent = `Создано: ${json.created}, пропущено: ${json.skipped_overlap}`;
          autoDetectStatus.style.color = json.created > 0 ? '#2e7d32' : '#666';
        }
        loadMasksList();
        loadPendingMasks();
      } catch (err) {
        console.error('[sync_chart] Auto-detect error:', err);
        if (autoDetectStatus) {
          autoDetectStatus.textContent = 'Ошибка: ' + (err.message || err);
          autoDetectStatus.style.color = '#c62828';
        }
      } finally {
        autoDetectBtn.disabled = false;
      }
    });
  }

  // ══════════════════ Панель верификации ══════════════════

  const pendingPanel = document.getElementById('masks-pending-panel');
  const pendingList = document.getElementById('masks-pending-list');
  const approveAllBtn = document.getElementById('masks-approve-all');
  const rejectAllBtn = document.getElementById('masks-reject-all');

  async function loadPendingMasks() {
    if (!pendingList) return;
    try {
      const resp = await fetch(`/api/pressure-masks/pending?well_id=${wellId}`);
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const masks = await resp.json();
      if (masks.length === 0) {
        if (pendingPanel) pendingPanel.style.display = 'none';
        return;
      }
      if (pendingPanel) pendingPanel.style.display = 'block';
      const TYPE_LABELS = {
        hydrate: 'Гидрат', degradation: 'Деградация', purge: 'Продувка',
        comm_loss: 'Потеря связи', sensor_fault: 'Неисправн.', manual: 'Ручная',
      };
      pendingList.innerHTML = masks.map(m => {
        const dtS = m.dt_start ? m.dt_start.replace('T', ' ').slice(0, 16) : '?';
        const dtE = m.dt_end ? m.dt_end.replace('T', ' ').slice(0, 16) : '?';
        const conf = m.detection_confidence ? Math.round(m.detection_confidence * 100) + '%' : '';
        return `<div style="display:flex; justify-content:space-between; align-items:center; padding:4px 0; border-bottom:1px solid #ffe0b2;">
          <span>${dtS} - ${dtE} | ${TYPE_LABELS[m.problem_type] || m.problem_type} | ${m.affected_sensor === 'p_tube' ? 'Устье' : 'Шлейф'} ${conf}</span>
          <span style="white-space:nowrap;">
            <button data-pending-approve="${m.id}" style="padding:2px 8px; background:#2e7d32; color:white; border:none; border-radius:3px; font-size:10px; cursor:pointer; margin-right:3px;">OK</button>
            <button data-pending-reject="${m.id}" style="padding:2px 8px; background:#c62828; color:white; border:none; border-radius:3px; font-size:10px; cursor:pointer;">X</button>
          </span>
        </div>`;
      }).join('');

      // Individual approve/reject
      pendingList.querySelectorAll('[data-pending-approve]').forEach(btn => {
        btn.addEventListener('click', async () => {
          await fetch('/api/pressure-masks/verify-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mask_ids: [parseInt(btn.dataset.pendingApprove)], action: 'approve' }),
          });
          loadMasksList();
          loadPendingMasks();
        });
      });
      pendingList.querySelectorAll('[data-pending-reject]').forEach(btn => {
        btn.addEventListener('click', async () => {
          await fetch('/api/pressure-masks/verify-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mask_ids: [parseInt(btn.dataset.pendingReject)], action: 'reject' }),
          });
          loadMasksList();
          loadPendingMasks();
        });
      });

      // Store IDs for bulk operations
      pendingList._maskIds = masks.map(m => m.id);
    } catch (err) {
      console.error('[sync_chart] Failed to load pending masks:', err);
    }
  }

  // Bulk approve/reject
  if (approveAllBtn) {
    approveAllBtn.addEventListener('click', async () => {
      const ids = pendingList && pendingList._maskIds;
      if (!ids || ids.length === 0) return;
      if (!confirm(`Подтвердить ${ids.length} маск(и)?`)) return;
      await fetch('/api/pressure-masks/verify-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mask_ids: ids, action: 'approve' }),
      });
      loadMasksList();
      loadPendingMasks();
      loadChart();
    });
  }
  if (rejectAllBtn) {
    rejectAllBtn.addEventListener('click', async () => {
      const ids = pendingList && pendingList._maskIds;
      if (!ids || ids.length === 0) return;
      if (!confirm(`Отклонить ${ids.length} маск(и)?`)) return;
      await fetch('/api/pressure-masks/verify-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mask_ids: ids, action: 'reject' }),
      });
      loadMasksList();
      loadPendingMasks();
    });
  }

  // Load pending masks when modal opens
  if (masksManageBtn) {
    const origHandler = masksManageBtn.onclick;
    masksManageBtn.addEventListener('click', () => {
      loadPendingMasks();
    });
  }

  // ══════════════════ Модалка справки по фильтрам ══════════════════

  const helpModal = document.getElementById('sync-filter-help-modal');
  const helpBtn = document.getElementById('sync-filter-help-btn');
  const helpClose = document.getElementById('sync-filter-help-close');

  if (helpBtn && helpModal) {
    helpBtn.addEventListener('click', () => { helpModal.style.display = 'flex'; });
  }
  if (helpClose && helpModal) {
    helpClose.addEventListener('click', () => { helpModal.style.display = 'none'; });
  }
  if (helpModal) {
    helpModal.addEventListener('click', (e) => {
      if (e.target === helpModal) helpModal.style.display = 'none';
    });
  }

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
   * @param {string|null} start - ISO-строка начала периода (Кунград) или null
   * @param {string|null} end - ISO-строка конца периода (Кунград) или null
   */
  window.updateAllCharts = async function (days, interval, start, end) {
    // Обновляем глобальный диапазон
    if (start !== undefined) currentStart = start;
    if (end !== undefined) currentEnd = end;

    // Обновляем активные кнопки (только если пресет, не ручной выбор)
    if (days !== undefined && !currentStart) {
      document.querySelectorAll('[data-sync-days]').forEach(b => {
        const d = parseInt(b.dataset.syncDays);
        if (d === days) {
          b.style.background = '#1565c0';
          b.style.color = 'white';
          b.style.borderColor = '#1565c0';
        } else {
          b.style.background = '#f5f5f5';
          b.style.color = '#333';
          b.style.borderColor = '#90a4ae';
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
          b.style.background = '#f5f5f5';
          b.style.color = '#333';
          b.style.borderColor = '#90a4ae';
        }
      });
    }

    // Сначала загружаем давление — дебит рассчитывается на его основе
    await loadChart(days, interval, start, end);

    // Обновляем график событий если есть
    if (window.eventsChartReload) {
      window.eventsChartReload();
    }

    // Обновляем график дебита ПОСЛЕ давления
    console.log('[coordinator] flowRateChartReload exists:', !!window.flowRateChartReload, 'days:', days, 'start:', start, 'end:', end);
    if (window.flowRateChartReload) {
      window.flowRateChartReload(days, currentStart, currentEnd);
    }
  };

  // ══════════════════ Popup сырых данных (ЛКМ) ══════════════════

  let rawPopup = null;

  function removeRawPopup() {
    if (rawPopup) { rawPopup.remove(); rawPopup = null; }
  }

  // Закрытие popup при клике вне него или Escape
  document.addEventListener('mousedown', function (e) {
    if (rawPopup && !rawPopup.contains(e.target) && e.target !== canvas) {
      removeRawPopup();
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') removeRawPopup();
  });

  /**
   * Загружает ±10 сырых замеров из pressure_readings вокруг указанного времени
   * и показывает popup рядом с позицией клика.
   */
  async function showRawNearbyPopup(wId, isoTimeUtc5, mouseX, mouseY) {
    removeRawPopup();

    rawPopup = document.createElement('div');
    rawPopup.className = 'raw-nearby-popup';
    rawPopup.innerHTML = '<div class="raw-nearby-popup__loading">Загрузка...</div>';
    rawPopup.style.left = mouseX + 'px';
    rawPopup.style.top = mouseY + 'px';
    document.body.appendChild(rawPopup);

    try {
      const res = await fetch(`/api/pressure/raw_nearby/${wId}?t=${encodeURIComponent(isoTimeUtc5)}&n=10`);
      const data = await res.json();

      if (!rawPopup) return;  // popup already closed

      if (!data.rows || data.rows.length === 0) {
        rawPopup.innerHTML = '<div class="raw-nearby-popup__loading">Нет данных</div>';
        return;
      }

      // Найдём строку ближайшую к центру для подсветки
      const centerTime = isoTimeUtc5.replace('T', ' ');
      let closestIdx = 0;
      let closestDiff = Infinity;
      data.rows.forEach(function (r, i) {
        const diff = Math.abs(new Date(r.measured_at).getTime() - new Date(centerTime).getTime());
        if (diff < closestDiff) { closestDiff = diff; closestIdx = i; }
      });

      let html = '<div class="raw-nearby-popup__title">Сырые замеры ±10 (UTC+5)</div>';
      html += '<table><tr><th>Время</th><th>Ptr</th><th>Pshl</th></tr>';
      data.rows.forEach(function (r, i) {
        const isCenter = (i === closestIdx);
        const rowCls = isCenter ? ' class="raw-center"' : '';
        const timeShort = r.measured_at.slice(11, 19);  // HH:MM:SS

        const tubeVal = r.p_tube !== null ? r.p_tube.toFixed(2) : '—';
        const lineVal = r.p_line !== null ? r.p_line.toFixed(2) : '—';
        const tubeCls = r.p_tube !== null
          ? (r.p_tube === 0 ? 'val-zero' : 'val-ok')
          : 'val-null';
        const lineCls = r.p_line !== null
          ? (r.p_line === 0 ? 'val-zero' : 'val-line')
          : 'val-null';

        html += '<tr' + rowCls + '>'
          + '<td>' + timeShort + '</td>'
          + '<td class="' + tubeCls + '">' + tubeVal + '</td>'
          + '<td class="' + lineCls + '">' + lineVal + '</td>'
          + '</tr>';
      });
      html += '</table>';
      rawPopup.innerHTML = html;

      // Подправляем позицию если выходит за экран
      const rect = rawPopup.getBoundingClientRect();
      if (rect.right > window.innerWidth) rawPopup.style.left = Math.max(4, mouseX - rect.width) + 'px';
      if (rect.bottom > window.innerHeight) rawPopup.style.top = Math.max(4, mouseY - rect.height) + 'px';

    } catch (err) {
      if (rawPopup) rawPopup.innerHTML = '<div class="raw-nearby-popup__loading">Ошибка: ' + err.message + '</div>';
    }
  }

})();
