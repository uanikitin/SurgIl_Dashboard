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

  let deltaChart = null;
  let currentDays = 7;
  let currentInterval = 5;
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
    const fillMode = document.getElementById('sync-filter-fill-mode');
    const maxGap = document.getElementById('sync-filter-max-gap');

    let params = '';
    if (zeros && zeros.checked) params += '&filter_zeros=true';
    if (spikes && spikes.checked) params += '&filter_spikes=true';
    if (fillMode && fillMode.value !== 'none') params += `&fill_mode=${fillMode.value}`;
    if (maxGap) params += `&max_gap=${maxGap.value}`;
    return params;
  }

  /**
   * Загружает данные и строит/обновляет график ΔP
   */
  async function loadChart(days, interval) {
    if (days !== undefined) currentDays = days;
    if (interval !== undefined) currentInterval = interval;

    console.log('[delta_chart] loadChart called, days:', currentDays, 'interval:', currentInterval);

    try {
      const filterParams = getFilterParams();
      const url = `/api/pressure/chart/${wellId}?days=${currentDays}&interval=${currentInterval}${filterParams}`;
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
      canvas.parentElement.innerHTML =
        '<div style="padding:40px;text-align:center;color:#999;">Нет данных для графика ΔP</div>';
    }
  }

  /**
   * Отрисовка графика ΔP
   */
  function renderChart(points) {
    console.log('[delta_chart] renderChart called, points:', points?.length || 0);

    if (!points || points.length === 0) {
      console.log('[delta_chart] No points to render');
      canvas.parentElement.innerHTML =
        '<div style="padding:40px;text-align:center;color:#999;">Нет данных ΔP за выбранный период</div>';
      return;
    }

    // Вычисляем ΔP = Ptr_avg - Pshl_avg, clamped to >= 0
    const deltaData = [];
    for (const p of points) {
      if (p.p_tube_avg !== null && p.p_line_avg !== null) {
        const dp = p.p_tube_avg - p.p_line_avg;
        deltaData.push({ x: p.t, y: Math.max(0, dp) });
      }
    }

    console.log('[delta_chart] deltaData computed:', deltaData.length, 'points');

    if (deltaData.length === 0) {
      console.log('[delta_chart] No valid delta data (both sensors required)');
      canvas.parentElement.innerHTML =
        '<div style="padding:40px;text-align:center;color:#999;">Нет данных ΔP (требуются оба датчика)</div>';
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
        segment: {
          borderColor: segmentColor,
        },
      },
    ];

    if (deltaChart) {
      deltaChart.destroy();
    }

    const ctx = canvas.getContext('2d');
    deltaChart = new Chart(ctx, {
      type: 'line',
      data: { datasets },
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
        },
      },
    });

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

    // Извлекаем значения ΔP
    const values = deltaData.map(d => d.y);

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
    if (!window.syncChart || !window.syncChart.syncZoom) return;
    const xScale = chart.scales.x;
    if (!xScale) return;
    window.syncChart.syncZoom(xScale.min, xScale.max);
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
