/**
 * delta_pressure_chart.js — График ΔP (разница давлений) скважины
 *
 * ΔP = Ptr (устье) - Pshl (шлейф)
 * Значения < 0 приравниваются к 0.
 *
 * Использует тот же API: /api/pressure/chart/{well_id}?days=N&interval=M
 * Синхронизирован по времени с основным графиком давлений.
 *
 * Возможности:
 *   - Критическая линия (порог) — по умолчанию 0.5 атм, настраивается
 *   - Область ниже порога закрашивается красным
 *   - Zoom по X (drag-select), масштаб Y — ползунок
 *   - Общие кнопки периода и сглаживания с основным графиком
 */

(function () {
  'use strict';

  const canvasId = 'chart_delta_pressure';
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const wellId = canvas.dataset.wellId;
  if (!wellId) return;

  let deltaChart = null;
  let currentDays = 7;
  let currentInterval = 5;
  let criticalThreshold = 0.5; // атм — по умолчанию

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
   * Собирает параметры фильтрации (общие с pressure_chart.js)
   */
  function getFilterParams() {
    // Используем общую функцию если доступна, иначе читаем из DOM
    if (window.pressureFilterParams) {
      return window.pressureFilterParams();
    }
    const zeros = document.getElementById('filter-zeros');
    const spikes = document.getElementById('filter-spikes');
    const fillMode = document.getElementById('filter-fill-mode');
    const maxGap = document.getElementById('filter-max-gap');

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

    try {
      const filterParams = getFilterParams();
      const resp = await fetch(`/api/pressure/chart/${wellId}?days=${currentDays}&interval=${currentInterval}${filterParams}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json = await resp.json();
      console.log(`[delta_chart] well=${wellId} days=${currentDays} interval=${currentInterval}min points=${json.points?.length || 0}` +
        (json.filter_stats ? ` filters applied` : ''));
      renderChart(json.points);
      syncYSlider();
    } catch (err) {
      console.error('Delta pressure chart error:', err);
      canvas.parentElement.innerHTML =
        '<div style="padding:40px;text-align:center;color:#999;">Нет данных для графика ΔP</div>';
    }
  }

  /**
   * Отрисовка графика ΔP
   */
  function renderChart(points) {
    if (!points || points.length === 0) {
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

    if (deltaData.length === 0) {
      canvas.parentElement.innerHTML =
        '<div style="padding:40px;text-align:center;color:#999;">Нет данных ΔP (требуются оба датчика)</div>';
      return;
    }

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
            backgroundColor: 'rgba(0,0,0,0.85)',
            titleFont: { size: 13 },
            bodyFont: { size: 12 },
            padding: 12,
            callbacks: {
              title: function(items) {
                if (!items.length) return '';
                const d = new Date(items[0].parsed.x);
                const dd = String(d.getUTCDate()).padStart(2, '0');
                const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
                const yy = d.getUTCFullYear();
                const hh = String(d.getUTCHours()).padStart(2, '0');
                const mi = String(d.getUTCMinutes()).padStart(2, '0');
                return `${dd}.${mm}.${yy} ${hh}:${mi} (Кунград, UTC+5)`;
              },
              label: function(ctx) {
                const val = ctx.parsed.y.toFixed(3);
                const status = ctx.parsed.y < criticalThreshold ? ' ⚠ НИЖЕ ПОРОГА' : '';
                return `ΔP: ${val} атм${status}`;
              },
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
              enabled: true,
              mode: 'x',
              modifierKey: 'shift',
            },
            zoom: {
              drag: { enabled: true, backgroundColor: 'rgba(13, 110, 253, 0.1)' },
              mode: 'x',
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
              date: { zone: 'UTC', locale: 'ru' },
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
    }
  }

  // ── Кнопки выбора периода (общие с основным графиком) ──
  // Слушаем те же кнопки data-pressure-days и data-pressure-interval
  document.querySelectorAll('[data-pressure-days]').forEach(btn => {
    btn.addEventListener('click', function () {
      loadChart(parseInt(this.dataset.pressureDays), undefined);
    });
  });

  document.querySelectorAll('[data-pressure-interval]').forEach(btn => {
    btn.addEventListener('click', function () {
      loadChart(undefined, parseInt(this.dataset.pressureInterval));
    });
  });

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

  // Начальная загрузка
  loadChart(7, 5);

  // Экспортируем для внешнего доступа (если нужен)
  window.deltaChart = {
    reload: loadChart,
    updateThreshold: updateThreshold,
  };

})();
