/**
 * delta_pressure_chart.js — График ΔP (разница давлений) скважины
 *
 * ΔP = Ptr (устье) - Pshl (шлейф)
 * Значения < 0 приравниваются к 0.
 *
 * Использует тот же API: /api/pressure/chart/{well_id}?days=N&interval=M
 * СИНХРОНИЗИРОВАН с основным графиком давлений (synchronized_chart.js):
 *   - Zoom/Pan синхронизируются между графиками
 *   - Используются те же фильтры (sync-filter-*)
 *
 * Возможности:
 *   - Критическая линия (порог) — по умолчанию 0.5 атм, настраивается
 *   - Область ниже порога закрашивается красным
 *   - Zoom по X (drag-select), масштаб Y — ползунок
 */

(function () {
  'use strict';

  console.log('[delta_chart] Script loaded');

  // ══════════════════ Плагин: вертикальные линии событий ══════════════════
  const deltaVerticalLinePlugin = {
    id: 'deltaVerticalLines',
    afterDatasetsDraw(chart) {
      const ctx = chart.ctx;
      const yAxis = chart.scales.y;
      const xAxis = chart.scales.x;
      if (!yAxis || !xAxis) return;

      chart.data.datasets.forEach((dataset, dsIndex) => {
        if (!dataset._isEventLine) return;
        const meta = chart.getDatasetMeta(dsIndex);
        if (!meta || meta.hidden) return;

        meta.data.forEach((point) => {
          const x = point.x;
          if (isNaN(x) || x < xAxis.left || x > xAxis.right) return;
          ctx.save();
          ctx.strokeStyle = dataset.borderColor || '#999';
          ctx.lineWidth = 1.5;
          ctx.setLineDash([5, 4]);
          ctx.globalAlpha = 0.45;
          ctx.beginPath();
          ctx.moveTo(x, yAxis.top);
          ctx.lineTo(x, yAxis.bottom);
          ctx.stroke();
          ctx.restore();
        });
      });
    },
  };

  // ══════════════════ Константы событий ══════════════════
  const DELTA_EVENT_ICONS = {
    pressure: '\u{1F4CA}', purge: '\u{1F4A8}', reagent: '\u{1F489}',
    equip: '\u{1F527}', note: '\u{1F4DD}', other: '\u{1F4CC}',
  };
  const DELTA_EVENT_LABELS = {
    pressure: 'Замер', purge: 'Продувка', reagent: 'Реагент',
    equip: 'Оборудование', note: 'Заметка', other: 'Другое',
  };
  const DELTA_EVENT_Y = {
    pressure: 0.90, purge: 0.70, reagent: 0.50,
    equip: 0.30, note: 0.15, other: 0.05,
  };

  /** Нормализация времени: обрезка TZ-суффиксов и лишних десятичных знаков */
  function normalizeTime(t) {
    if (!t) return t;
    t = t.replace(/[+-]\d{2}:\d{2}$/, '').replace(/Z$/, '');
    const dotIdx = t.lastIndexOf('.');
    if (dotIdx !== -1 && t.length - dotIdx > 4) {
      t = t.substring(0, dotIdx + 4);
    }
    return t;
  }

  let showEvents = false; // управляется чекбоксом

  const canvasId = 'chart_delta_pressure';
  const canvas = document.getElementById(canvasId);
  if (!canvas) {
    console.log('[delta_chart] Canvas not found:', canvasId);
    return;
  }

  const wellId = canvas.dataset.wellId;
  if (!wellId) {
    console.log('[delta_chart] Well ID not found');
    return;
  }

  console.log('[delta_chart] Initializing for well:', wellId);

  // Создаём контейнер для сообщения "нет данных" рядом с canvas (не уничтожая его)
  const noDataMsg = document.createElement('div');
  noDataMsg.style.cssText = 'padding:40px;text-align:center;color:#999;display:none;';
  canvas.parentElement.insertBefore(noDataMsg, canvas);

  function showNoData(msg) {
    noDataMsg.textContent = msg;
    noDataMsg.style.display = 'block';
    canvas.style.display = 'none';
  }
  function showCanvas() {
    noDataMsg.style.display = 'none';
    canvas.style.display = 'block';
  }

  let deltaChart = null;
  let currentDays = 7;
  let currentInterval = 5;
  let currentStart = null;  // ISO string (Кунград) или null
  let currentEnd = null;    // ISO string (Кунград) или null
  let criticalThreshold = 0.5; // атм — по умолчанию
  let currentDeltaData = [];   // хранение данных для пересчёта статистики

  // Цвета
  const COLORS = {
    delta:         '#2e7d32',   // зелёный — ΔP
    deltaFill:     'rgba(46, 125, 50, 0.15)',
    critical:      '#e53935',   // красный — критическая линия
    criticalFill:  'rgba(229, 57, 53, 0.10)',
    warning:       '#ff9800',   // оранжевый — зона предупреждения
    grid:          '#e9ecef',
  };

  /**
   * Собирает параметры фильтрации (из sync-filter-* элементов)
   */
  function getFilterParams() {
    // Читаем из sync-filter-* элементов (синхронизированный график)
    const zeros = document.getElementById('sync-filter-zeros');
    const spikes = document.getElementById('sync-filter-spikes');
    const spikeThreshold = document.getElementById('sync-filter-spike-threshold');
    const fillMode = document.getElementById('sync-filter-fill-mode');
    const maxGap = document.getElementById('sync-filter-max-gap');
    const gapBreak = document.getElementById('sync-filter-gap-break');

    let params = '';
    if (zeros && zeros.checked) params += '&filter_zeros=true';
    if (spikes && spikes.checked) params += '&filter_spikes=true';
    if (spikeThreshold && parseFloat(spikeThreshold.value) > 0)
      params += `&spike_threshold=${spikeThreshold.value}`;
    if (fillMode && fillMode.value !== 'none') params += `&fill_mode=${fillMode.value}`;
    if (maxGap) params += `&max_gap=${maxGap.value}`;
    if (gapBreak) params += `&gap_break=${gapBreak.value}`;
    const modeSelect = document.getElementById('sync-chart-mode');
    const mode = modeSelect ? modeSelect.value : '';
    if (mode) {
      // mode overrides individual filter params on the backend
      return `&mode=${mode}`;
    }
    return params;
  }

  /**
   * Загружает данные и строит/обновляет график ΔP
   */
  async function loadChart(days, interval, start, end) {
    if (days !== undefined) currentDays = days;
    if (interval !== undefined) currentInterval = interval;
    if (start !== undefined) currentStart = start;
    if (end !== undefined) currentEnd = end;

    console.log('[delta_chart] loadChart called, days:', currentDays, 'interval:', currentInterval, 'start:', currentStart, 'end:', currentEnd);

    try {
      const filterParams = getFilterParams();
      let url;
      if (currentStart && currentEnd) {
        url = `/api/pressure/chart/${wellId}?interval=${currentInterval}&start=${encodeURIComponent(currentStart)}&end=${encodeURIComponent(currentEnd)}${filterParams}`;
      } else {
        url = `/api/pressure/chart/${wellId}?days=${currentDays}&interval=${currentInterval}${filterParams}`;
      }
      console.log('[delta_chart] Fetching:', url);

      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json = await resp.json();
      console.log(`[delta_chart] well=${wellId} days=${currentDays} interval=${currentInterval}min points=${json.points?.length || 0}` +
        (json.filter_stats ? ` filters applied` : ''));
      renderChart(json.points);
      syncYSlider();
    } catch (err) {
      console.error('[delta_chart] Load error:', err);
      showNoData('Нет данных для графика ΔP');
    }
  }

  /**
   * Строит datasets событий из window.wellEventsData.
   * Фильтрует по текущему периоду — показываем только события за выбранный диапазон.
   */
  function buildEventDatasets(ds) {
    const evData = window.wellEventsData || {};
    const eventColors = evData.eventColors || {};
    const reagentColors = evData.reagentColors || {};
    let cutoffMs, endMs;
    if (currentStart && currentEnd) {
      cutoffMs = new Date(currentStart).getTime();
      endMs = new Date(currentEnd).getTime();
    } else {
      cutoffMs = Date.now() - currentDays * 24 * 60 * 60 * 1000;
      endMs = Date.now();
    }

    // Не-reagent события → вертикальные линии
    const eventsByType = {};
    (evData.timelineEvents || []).forEach(ev => {
      if (!ev.t) return;
      const evMs = new Date(normalizeTime(ev.t)).getTime();
      if (evMs < cutoffMs || evMs > endMs) return;
      const type = (ev.type || 'other').toLowerCase();
      if (!eventsByType[type]) eventsByType[type] = [];
      eventsByType[type].push(ev);
    });

    Object.entries(eventsByType).forEach(([type, events]) => {
      const color = eventColors[type] || '#9e9e9e';
      ds.push({
        label: `${DELTA_EVENT_ICONS[type] || '\u{1F4CC}'} ${DELTA_EVENT_LABELS[type] || type}`,
        type: 'scatter',
        data: events.map(ev => ({
          x: normalizeTime(ev.t),
          y: DELTA_EVENT_Y[type] || 0.5,
          eventData: ev,
        })),
        backgroundColor: color,
        borderColor: color,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 6,
        pointHitRadius: 20,
        yAxisID: 'y_events',
        order: 10,
        _isEventLine: true,
      });
    });

    // Реагенты → scatter circles
    const injByReagent = {};
    (evData.timelineInjections || []).forEach(inj => {
      if (!inj.t) return;
      const injMs = new Date(normalizeTime(inj.t)).getTime();
      if (injMs < cutoffMs || injMs > endMs) return;
      const name = inj.reagent || 'Реагент';
      if (!injByReagent[name]) injByReagent[name] = [];
      injByReagent[name].push(inj);
    });

    Object.entries(injByReagent).forEach(([name, injs]) => {
      const color = reagentColors[name] || '#e53935';
      ds.push({
        label: `\u{1F489} ${name}`,
        type: 'scatter',
        data: injs.map(inj => ({
          x: normalizeTime(inj.t),
          y: 0.50,
          eventData: inj,
        })),
        backgroundColor: color,
        borderColor: color,
        borderWidth: 2,
        pointRadius: 6,
        pointHoverRadius: 9,
        pointHitRadius: 20,
        pointStyle: 'circle',
        yAxisID: 'y_events',
        order: 11,
        _isEventLine: false,
      });
    });
  }

  /**
   * Отрисовка графика ΔP
   */
  function renderChart(points) {
    console.log('[delta_chart] renderChart called, points:', points?.length || 0);

    if (!points || points.length === 0) {
      console.log('[delta_chart] No points to render');
      showNoData('Нет данных ΔP за выбранный период');
      return;
    }

    // Вычисляем ΔP = Ptr_avg - Pshl_avg, clamped to >= 0
    // Gap-маркеры (_gap:true) → null → Chart.js рвёт линию при больших разрывах.
    // Обычные null → пропускаем → Chart.js рисует плавно через короткие пропуски.
    const deltaData = [];
    for (const p of points) {
      if (!p.t) continue;
      if (p._gap) {
        deltaData.push({ x: p.t, y: null });
      } else if (p.p_tube_avg != null && p.p_line_avg != null) {
        const dp = p.p_tube_avg - p.p_line_avg;
        deltaData.push({ x: p.t, y: Math.max(0, dp) });
      }
    }

    const validDeltaCount = deltaData.filter(d => d.y !== null).length;
    console.log('[delta_chart] deltaData computed:', deltaData.length, 'points,', validDeltaCount, 'valid');

    if (validDeltaCount === 0) {
      console.log('[delta_chart] No valid delta data (both sensors required)');
      showNoData('Нет данных ΔP (требуются оба датчика)');
      updateStatsPanel(null);
      return;
    }

    // Сохраняем данные для пересчёта статистики при смене порога
    currentDeltaData = deltaData;

    // Вычисляем статистику и обновляем панель
    calculateAndDisplayStats(deltaData, criticalThreshold);

    // Определяем цвет сегментов: ниже порога — красный, выше — зелёный
    const segmentColor = function(ctx) {
      const yVal = ctx.p1.parsed.y;
      return yVal < criticalThreshold ? COLORS.critical : COLORS.delta;
    };

    const defaultMax = criticalThreshold * 4;

    const datasets = [
      {
        label: 'ΔP (Ptr − Pshl)',
        data: deltaData,
        borderColor: COLORS.delta,
        backgroundColor: function(context) {
          const chart = context.chart;
          const { ctx, chartArea } = chart;
          if (!chartArea) return COLORS.deltaFill;

          // Градиент: зелёный сверху, красный внизу (у порога)
          const gradient = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
          gradient.addColorStop(0, 'rgba(46, 125, 50, 0.25)');
          gradient.addColorStop(0.7, 'rgba(46, 125, 50, 0.08)');
          gradient.addColorStop(1, 'rgba(229, 57, 53, 0.15)');
          return gradient;
        },
        fill: 'origin',
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.3,
        spanGaps: false,
        segment: {
          borderColor: segmentColor,
        },
      },
    ];

    // ── События (если включены) ──
    if (showEvents) {
      buildEventDatasets(datasets);
    }

    if (deltaChart) {
      deltaChart.destroy();
    }

    showCanvas();
    const ctx = canvas.getContext('2d');
    deltaChart = new Chart(ctx, {
      type: 'line',
      data: { datasets },
      plugins: [deltaVerticalLinePlugin],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: {
          mode: 'index',
          intersect: false,
        },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            labels: {
              usePointStyle: true,
              padding: 15,
              font: { size: 13 },
            },
          },
          tooltip: {
            enabled: false,  // Отключаем всплывающий tooltip
            external: function(context) {
              updateDeltaCursorPanel(context);
            },
          },
          annotation: {
            annotations: {
              criticalLine: {
                type: 'line',
                yMin: criticalThreshold,
                yMax: criticalThreshold,
                borderColor: COLORS.critical,
                borderWidth: 2,
                borderDash: [8, 4],
                label: {
                  display: true,
                  content: `Порог: ${criticalThreshold} атм`,
                  position: 'start',
                  backgroundColor: 'rgba(229, 57, 53, 0.85)',
                  color: 'white',
                  font: { size: 11, weight: 'bold' },
                  padding: { top: 3, bottom: 3, left: 6, right: 6 },
                  borderRadius: 3,
                },
              },
            },
          },
          zoom: {
            pan: {
              enabled: true,   // Always true so Hammer.js registers pan recognizers
              mode: 'xy',
              threshold: 5,
              onPanComplete: function({ chart }) {
                syncZoomToMain(chart);
              },
            },
            zoom: {
              drag: { enabled: !window._syncPanMode, backgroundColor: 'rgba(13, 110, 253, 0.1)' },
              mode: 'xy',
              onZoomComplete: function({ chart }) {
                syncZoomToMain(chart);
              },
            },
          },
        },
        scales: {
          x: {
            type: 'time',
            time: {
              tooltipFormat: 'dd.MM.yyyy HH:mm',
              displayFormats: {
                minute: 'HH:mm',
                hour: 'ccc dd.MM HH:mm',
                day: 'ccc dd.MM',
                week: 'ccc dd.MM',
                month: 'MMM yyyy',
              },
            },
            adapters: {
              date: { locale: 'ru' },  // Время уже Кунградское
            },
            grid: { color: COLORS.grid },
            ticks: { font: { size: 11 }, maxRotation: 0 },
          },
          y: {
            title: {
              display: true,
              text: 'ΔP (атм)',
              font: { size: 12, weight: 'bold' },
            },
            min: 0,
            max: defaultMax,
            grid: { color: COLORS.grid },
            ticks: { font: { size: 11 } },
          },
          y_events: {
            display: false,
            position: 'right',
            min: 0, max: 1,
            grid: { drawOnChartArea: false },
          },
        },
      },
    });

    // После создания: отключаем pan если режим не активен
    if (!window._syncPanMode) {
      deltaChart.options.plugins.zoom.pan.enabled = false;
      deltaChart.update('none');
    }

    console.log('[delta_chart] Chart created successfully');
  }

  // ══════════════════ Панель статистики ΔP ══════════════════

  /**
   * Вычисляет статистику ΔP и обновляет панель
   * @param {Array} deltaData - массив точек {x, y} где y = ΔP
   * @param {number} threshold - пороговое значение ΔP
   */
  function calculateAndDisplayStats(deltaData, threshold) {
    if (!deltaData || deltaData.length === 0) {
      updateStatsPanel(null);
      return;
    }

    // Извлекаем значения ΔP (пропускаем null gap-маркеры)
    const values = deltaData.map(d => d.y).filter(v => v !== null && v !== undefined);

    // Основная статистика
    const min = Math.min(...values);
    const max = Math.max(...values);
    const sum = values.reduce((a, b) => a + b, 0);
    const avg = sum / values.length;
    const current = values[values.length - 1];

    // Медиана
    const sorted = [...values].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    const median = sorted.length % 2 !== 0
      ? sorted[mid]
      : (sorted[mid - 1] + sorted[mid]) / 2;

    // Время выше/ниже порога
    // Предполагаем равномерные интервалы между точками
    let pointsAbove = 0;
    let pointsBelow = 0;

    for (const val of values) {
      if (val >= threshold) {
        pointsAbove++;
      } else {
        pointsBelow++;
      }
    }

    const totalPoints = values.length;
    const percentAbove = ((pointsAbove / totalPoints) * 100).toFixed(1);
    const percentBelow = ((pointsBelow / totalPoints) * 100).toFixed(1);

    // Вычисляем примерное время (если интервал известен)
    // Используем разницу между первой и последней точкой
    let timeAboveStr = `${percentAbove}%`;
    let timeBelowStr = `${percentBelow}%`;

    if (deltaData.length >= 2) {
      const firstTime = new Date(deltaData[0].x).getTime();
      const lastTime = new Date(deltaData[deltaData.length - 1].x).getTime();
      const totalDurationMs = lastTime - firstTime;
      const totalDurationHours = totalDurationMs / (1000 * 60 * 60);

      const hoursAbove = (pointsAbove / totalPoints) * totalDurationHours;
      const hoursBelow = (pointsBelow / totalPoints) * totalDurationHours;

      timeAboveStr = formatDuration(hoursAbove);
      timeBelowStr = formatDuration(hoursBelow);
    }

    // Обновляем панель
    updateStatsPanel({
      current, max, min, avg, median,
      timeAbove: timeAboveStr,
      timeBelow: timeBelowStr,
      percentAbove, percentBelow,
    });
  }

  /**
   * Форматирует длительность в часах в читаемую строку
   */
  function formatDuration(hours) {
    if (hours < 1) {
      return `${Math.round(hours * 60)} мин`;
    } else if (hours < 24) {
      const h = Math.floor(hours);
      const m = Math.round((hours - h) * 60);
      return m > 0 ? `${h}ч ${m}м` : `${h}ч`;
    } else {
      const d = Math.floor(hours / 24);
      const h = Math.round(hours % 24);
      return h > 0 ? `${d}д ${h}ч` : `${d}д`;
    }
  }

  /**
   * Обновляет панель курсора (время и значение) при наведении на график
   */
  function updateDeltaCursorPanel(context) {
    const tooltipModel = context.tooltip;
    const timeEl = document.getElementById('delta-cursor-time');
    const valueEl = document.getElementById('delta-cursor-value');

    if (!timeEl || !valueEl) return;

    // Если нет данных — оставляем последние значения
    if (tooltipModel.opacity === 0 || !tooltipModel.dataPoints?.length) {
      return;
    }

    const dp = tooltipModel.dataPoints[0];
    if (!dp) return;

    // Время
    const d = new Date(dp.parsed.x);
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const hh = String(d.getHours()).padStart(2, '0');
    const mi = String(d.getMinutes()).padStart(2, '0');
    timeEl.textContent = `${dd}.${mm} ${hh}:${mi}`;

    // Значение ΔP
    const val = dp.parsed.y;
    const status = val < criticalThreshold ? ' ⚠' : '';
    valueEl.textContent = `${val.toFixed(3)} атм${status}`;
    valueEl.style.color = val < criticalThreshold ? '#c62828' : '#2e7d32';
  }

  /**
   * Обновляет DOM элементы панели статистики
   */
  function updateStatsPanel(stats) {
    const currentEl = document.getElementById('delta-stat-current');
    const maxEl = document.getElementById('delta-stat-max');
    const minEl = document.getElementById('delta-stat-min');
    const avgEl = document.getElementById('delta-stat-avg');
    const medianEl = document.getElementById('delta-stat-median');
    const aboveEl = document.getElementById('delta-stat-above');
    const belowEl = document.getElementById('delta-stat-below');

    if (!currentEl) return;

    if (!stats) {
      currentEl.textContent = '—';
      maxEl.textContent = '—';
      minEl.textContent = '—';
      avgEl.textContent = '—';
      medianEl.textContent = '—';
      aboveEl.textContent = '—';
      belowEl.textContent = '—';
      return;
    }

    currentEl.textContent = `${stats.current.toFixed(3)} атм`;
    maxEl.textContent = `${stats.max.toFixed(3)} атм`;
    minEl.textContent = `${stats.min.toFixed(3)} атм`;
    avgEl.textContent = `${stats.avg.toFixed(3)} атм`;
    medianEl.textContent = `${stats.median.toFixed(3)} атм`;
    aboveEl.textContent = `${stats.timeAbove} (${stats.percentAbove}%)`;
    belowEl.textContent = `${stats.timeBelow} (${stats.percentBelow}%)`;
  }

  // ── Ползунок масштаба Y ──
  const ySlider = document.getElementById('delta-y-slider');
  const yLabel  = document.getElementById('delta-y-label');

  function getDefaultYMax() {
    return Math.round((criticalThreshold * 4) * 10) / 10; // округление до 0.1
  }

  function syncYSlider() {
    if (!ySlider) return;
    const def = getDefaultYMax();
    ySlider.value = def;
    if (yLabel) yLabel.textContent = def.toFixed(1);
  }

  function applyYSlider() {
    if (!deltaChart || !ySlider) return;
    const val = parseFloat(ySlider.value);
    deltaChart.options.scales.y.max = val;
    deltaChart.update('none');
    if (yLabel) yLabel.textContent = val.toFixed(1);
  }

  function resetYSlider() {
    const def = getDefaultYMax();
    if (ySlider) {
      ySlider.value = def;
      if (yLabel) yLabel.textContent = def.toFixed(1);
    }
    if (deltaChart) {
      deltaChart.options.scales.y.max = def;
      deltaChart.update('none');
    }
  }

  if (ySlider) {
    ySlider.addEventListener('input', applyYSlider);
  }

  /**
   * Обновляет порог критического значения
   */
  function updateThreshold(newValue) {
    const val = parseFloat(newValue);
    if (isNaN(val) || val < 0) return;
    criticalThreshold = val;

    if (deltaChart) {
      // Обновляем аннотацию
      const ann = deltaChart.options.plugins.annotation.annotations.criticalLine;
      ann.yMin = criticalThreshold;
      ann.yMax = criticalThreshold;
      ann.label.content = `Порог: ${criticalThreshold} атм`;

      // Обновляем масштаб Y: max = 4 × порог
      const def = getDefaultYMax();
      deltaChart.options.scales.y.max = def;

      // Обновляем ползунок
      if (ySlider) {
        ySlider.value = def;
        if (yLabel) yLabel.textContent = def.toFixed(1);
      }

      // Обновляем сегментную раскраску — нужно перерисовать
      deltaChart.update();

      // Пересчитываем статистику с новым порогом
      if (currentDeltaData.length > 0) {
        calculateAndDisplayStats(currentDeltaData, criticalThreshold);
      }
    }
  }

  // ── Кнопки выбора периода ──
  // УДАЛЕНЫ: дублирующие listeners для [data-pressure-days] и [data-pressure-interval].
  // Теперь координатор (chart_sync.js) вызывает window.deltaChart.reload(days, interval).

  // Reset zoom + Y slider — своя кнопка
  const resetBtn = document.getElementById('delta-reset-zoom');
  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      resetYSlider();
      if (deltaChart) deltaChart.resetZoom();
    });
  }

  // ── Поле ввода порога ──
  const thresholdInput = document.getElementById('delta-threshold-input');
  if (thresholdInput) {
    thresholdInput.value = criticalThreshold;
    thresholdInput.addEventListener('change', function () {
      updateThreshold(this.value);
    });
    thresholdInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        updateThreshold(this.value);
        this.blur();
      }
    });
  }

  // ══════════════════ Синхронизация zoom с основным графиком ══════════════════

  /**
   * Синхронизирует zoom/pan обратно на основной график давлений
   * @param {Chart} chart - экземпляр графика delta
   */
  function syncZoomToMain(chart) {
    const xScale = chart.scales.x;
    if (!xScale) return;
    // Синхронизация с графиком давлений
    if (window.syncChart && window.syncChart.syncZoom) {
      window.syncChart.syncZoom(xScale.min, xScale.max);
    }
    // Синхронизация с графиком дебита
    if (window.flowRateChart && window.flowRateChart.syncZoom) {
      window.flowRateChart.syncZoom(xScale.min, xScale.max);
    }
  }

  /**
   * Синхронизирует масштаб X с основным графиком
   * Вызывается из synchronized_chart.js при zoom/pan
   */
  function syncZoom(xMin, xMax) {
    if (!deltaChart) return;
    deltaChart.options.scales.x.min = xMin;
    deltaChart.options.scales.x.max = xMax;
    deltaChart.update('none');
  }

  /**
   * Сбрасывает синхронизированный zoom
   */
  function resetSyncZoom() {
    if (!deltaChart) return;
    delete deltaChart.options.scales.x.min;
    delete deltaChart.options.scales.x.max;
    deltaChart.update('none');
  }

  // ══════════════════ Слушатели фильтров ══════════════════
  // Перезагружаем при изменении sync-filter-* элементов

  ['sync-filter-zeros', 'sync-filter-spikes'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', () => loadChart());
  });

  const syncFillEl = document.getElementById('sync-filter-fill-mode');
  if (syncFillEl) syncFillEl.addEventListener('change', () => loadChart());

  const syncGapEl = document.getElementById('sync-filter-max-gap');
  if (syncGapEl) syncGapEl.addEventListener('change', () => loadChart());

  // ══════════════════ Чекбокс событий ══════════════════
  const eventsToggle = document.getElementById('delta-events-toggle');
  if (eventsToggle) {
    eventsToggle.addEventListener('change', function () {
      showEvents = this.checked;
      loadChart();
    });
  }

  // ══════════════════ Инициализация ══════════════════

  // Начальная загрузка
  loadChart(7, 5);

  // Экспортируем для внешнего доступа
  window.deltaChart = {
    reload: loadChart,
    updateThreshold: updateThreshold,
    syncZoom: syncZoom,
    resetZoom: resetSyncZoom,
    get instance() { return deltaChart; },
  };

})();
