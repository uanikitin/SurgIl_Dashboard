/**
 * flow_rate_chart.js — График расчётного дебита газа
 *
 * Данные: GET /api/flow-rate/calculate/{well_id}?days=N&smooth=true&multiplier=M
 * Ответ:  { summary, chart: {timestamps, flow_rate, cumulative_flow, p_tube, p_line},
 *           downtime_periods, data_points }
 *
 * Синхронизируется с остальными графиками через synchronized_chart.js:
 *   window.flowRateChartReload(days) вызывается из updateAllCharts()
 *
 * Используется на странице well.html
 */
(function () {
  'use strict';

  console.log('[flow_rate_chart] Script loaded');

  // ═══════════════════ Canvas ═══════════════════
  var canvas = document.getElementById('chart_flow_rate');
  if (!canvas) {
    console.log('[flow_rate_chart] Canvas not found — секция скрыта (нет штуцера)');
    return;
  }

  var wellId = canvas.dataset.wellId;
  if (!wellId) {
    console.log('[flow_rate_chart] Well ID not found');
    return;
  }

  console.log('[flow_rate_chart] Initializing for well:', wellId);

  // ═══════════════════ Состояние ═══════════════════
  var flowChart = null;
  var currentDays = 7;
  var currentMultiplier = 4.1;

  // ═══════════════════ Цвета ═══════════════════
  var COLORS = {
    flowRate:   '#ff9800',
    flowFill:   'rgba(255, 152, 0, 0.12)',
    cumulative: '#1565c0',
    grid:       '#e9ecef',
  };

  // ═══════════════════ Сообщение «нет данных» ═══════════════════
  var noDataMsg = document.createElement('div');
  noDataMsg.style.cssText = 'padding:40px; text-align:center; color:#999; font-size:14px; display:none;';
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

  // ═══════════════════ Индикаторы загрузки ═══════════════════
  var loadingOverlay = document.getElementById('flow-chart-loading');
  var statsLoading   = document.getElementById('flow-stats-loading');
  var statsPanel     = document.getElementById('flow-stats-panel');

  function showLoading() {
    if (loadingOverlay) loadingOverlay.style.display = 'flex';
    if (statsLoading)   statsLoading.style.display   = 'block';
    if (statsPanel)     statsPanel.style.display      = 'none';
  }

  function hideLoading() {
    if (loadingOverlay) loadingOverlay.style.display = 'none';
    if (statsLoading)   statsLoading.style.display   = 'none';
    if (statsPanel)     statsPanel.style.display      = '';
  }

  // ═══════════════════ Помощник: установить текст по ID ═══════════════════
  function setTextById(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  // ══════════════════════════════════════════════════════════════
  //                    ГЛАВНАЯ: loadChart
  // ══════════════════════════════════════════════════════════════
  async function loadChart(days) {
    if (days !== undefined) currentDays = days;
    console.log('[flow_rate_chart] loadChart, days:', currentDays, 'multiplier:', currentMultiplier);
    showLoading();

    try {
      var url = '/api/flow-rate/calculate/' + wellId +
                '?days=' + currentDays +
                '&smooth=true&multiplier=' + currentMultiplier;
      console.log('[flow_rate_chart] Fetching:', url);

      var resp = await fetch(url);
      if (!resp.ok) {
        if (resp.status === 404) {
          var errData = null;
          try { errData = await resp.json(); } catch (e) { /* ignore */ }
          var detail = (errData && errData.detail) ? errData.detail : 'Нет данных или штуцер не задан';
          showNoData(detail);
          hideLoading();
          return;
        }
        throw new Error('HTTP ' + resp.status);
      }

      var json = await resp.json();
      console.log('[flow_rate_chart] well=' + wellId +
                  ' points=' + json.data_points +
                  ' downtime=' + (json.downtime_periods ? json.downtime_periods.length : 0));

      updateStatsPanel(json.summary);
      renderChart(json.chart, json.downtime_periods);
      renderDowntimeTable(json.downtime_periods);
      hideLoading();

    } catch (err) {
      console.error('[flow_rate_chart] Ошибка загрузки:', err);
      showNoData('Ошибка загрузки данных дебита');
      hideLoading();
    }
  }

  // ══════════════════════════════════════════════════════════════
  //                    Панель статистики
  // ══════════════════════════════════════════════════════════════
  function updateStatsPanel(summary) {
    if (!summary || summary.error) {
      setTextById('flow-stat-median',        '—');
      setTextById('flow-stat-mean',          '—');
      setTextById('flow-stat-cumulative',    '—');
      setTextById('flow-stat-blowout-count', '—');
      setTextById('flow-stat-total-loss',    '—');
      setTextById('flow-stat-loss-pct',      '—');
      setTextById('flow-stat-effective',     '—');
      return;
    }

    setTextById('flow-stat-median',
      summary.median_flow_rate != null ? summary.median_flow_rate.toFixed(3) : '—');
    setTextById('flow-stat-mean',
      summary.mean_flow_rate != null ? summary.mean_flow_rate.toFixed(3) : '—');
    setTextById('flow-stat-cumulative',
      summary.cumulative_flow != null ? summary.cumulative_flow.toFixed(2) : '—');
    setTextById('flow-stat-blowout-count',
      summary.blowout_count != null ? String(summary.blowout_count) : '—');
    setTextById('flow-stat-total-loss',
      summary.total_loss_daily != null ? summary.total_loss_daily.toFixed(4) : '—');
    setTextById('flow-stat-loss-pct',
      summary.loss_coefficient_pct != null ? summary.loss_coefficient_pct.toFixed(2) : '—');
    setTextById('flow-stat-effective',
      summary.effective_flow_rate != null ? summary.effective_flow_rate.toFixed(3) : '—');

    // Дополнительные показатели
    setTextById('flow-stat-obs-days',
      summary.observation_days != null ? summary.observation_days.toFixed(1) : '—');
    setTextById('flow-stat-downtime-hours',
      summary.downtime_hours != null ? summary.downtime_hours.toFixed(1) : '—');
    setTextById('flow-stat-choke',
      summary.choke_mm != null ? summary.choke_mm.toFixed(1) : '—');
  }

  // ══════════════════════════════════════════════════════════════
  //                    Рендеринг графика
  // ══════════════════════════════════════════════════════════════
  function renderChart(chartData, downtimePeriods) {
    if (!chartData || !chartData.timestamps || chartData.timestamps.length === 0) {
      showNoData('Нет данных для графика дебита');
      return;
    }

    // Подготовка данных
    var flowData = [];
    var cumulativeData = [];
    for (var i = 0; i < chartData.timestamps.length; i++) {
      flowData.push({ x: chartData.timestamps[i], y: chartData.flow_rate[i] });
      cumulativeData.push({ x: chartData.timestamps[i], y: chartData.cumulative_flow[i] });
    }

    // Аннотации простоев (box annotations)
    var annotations = {};
    if (downtimePeriods && downtimePeriods.length > 0) {
      var showLabels = downtimePeriods.length <= 20;
      for (var j = 0; j < downtimePeriods.length; j++) {
        var p = downtimePeriods[j];
        annotations['dt_' + j] = {
          type: 'box',
          xMin: p.start,
          xMax: p.end,
          backgroundColor: p.is_blowout
            ? 'rgba(229, 57, 53, 0.18)'
            : 'rgba(255, 152, 0, 0.10)',
          borderColor: p.is_blowout ? '#e53935' : '#ff9800',
          borderWidth: 1,
          label: {
            display: showLabels,
            content: p.is_blowout ? 'Продувка' : 'Простой',
            position: 'start',
            font: { size: 9 },
            color: p.is_blowout ? '#c62828' : '#e65100',
          },
        };
      }
    }

    // Наборы данных
    var datasets = [
      {
        label: 'Дебит (тыс. м\u00B3/сут)',
        data: flowData,
        borderColor: COLORS.flowRate,
        backgroundColor: COLORS.flowFill,
        fill: 'origin',
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.3,
        yAxisID: 'y',
      },
      {
        label: 'Накопленный (тыс. м\u00B3)',
        data: cumulativeData,
        borderColor: COLORS.cumulative,
        backgroundColor: 'transparent',
        fill: false,
        borderWidth: 1.5,
        borderDash: [5, 3],
        pointRadius: 0,
        pointHoverRadius: 3,
        tension: 0.3,
        yAxisID: 'y1',
      },
    ];

    // Уничтожить предыдущий график
    if (flowChart) {
      flowChart.destroy();
      flowChart = null;
    }
    showCanvas();

    var ctx = canvas.getContext('2d');
    flowChart = new Chart(ctx, {
      type: 'line',
      data: { datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            labels: { usePointStyle: true, padding: 15, font: { size: 12 } },
          },
          tooltip: {
            callbacks: {
              title: function (items) {
                if (!items.length) return '';
                var d = new Date(items[0].parsed.x);
                var dd = String(d.getDate()).padStart(2, '0');
                var mm = String(d.getMonth() + 1).padStart(2, '0');
                var hh = String(d.getHours()).padStart(2, '0');
                var mi = String(d.getMinutes()).padStart(2, '0');
                return dd + '.' + mm + '.' + d.getFullYear() + ' ' + hh + ':' + mi;
              },
              label: function (context) {
                var val = context.parsed.y;
                if (context.dataset.yAxisID === 'y1') {
                  return context.dataset.label + ': ' + val.toFixed(2) + ' \u0442\u044B\u0441. \u043C\u00B3';
                }
                return context.dataset.label + ': ' + val.toFixed(3) + ' \u0442\u044B\u0441. \u043C\u00B3/\u0441\u0443\u0442';
              },
            },
          },
          annotation: { annotations: annotations },
          zoom: {
            pan: { enabled: false, mode: 'x' },
            zoom: {
              drag: {
                enabled: true,
                backgroundColor: 'rgba(255, 152, 0, 0.08)',
                borderColor: 'rgba(255, 152, 0, 0.4)',
                borderWidth: 1,
              },
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
            adapters: { date: { locale: 'ru' } },
            grid: { color: COLORS.grid },
            ticks: { font: { size: 11 }, maxRotation: 0 },
          },
          y: {
            type: 'linear',
            position: 'left',
            title: {
              display: true,
              text: '\u0414\u0435\u0431\u0438\u0442 (\u0442\u044B\u0441. \u043C\u00B3/\u0441\u0443\u0442)',
              font: { size: 12, weight: 'bold' },
              color: COLORS.flowRate,
            },
            min: 0,
            grid: { color: COLORS.grid },
            ticks: { font: { size: 11 }, color: COLORS.flowRate },
          },
          y1: {
            type: 'linear',
            position: 'right',
            title: {
              display: true,
              text: '\u041D\u0430\u043A\u043E\u043F\u043B\u0435\u043D\u043D\u044B\u0439 (\u0442\u044B\u0441. \u043C\u00B3)',
              font: { size: 12, weight: 'bold' },
              color: COLORS.cumulative,
            },
            min: 0,
            grid: { drawOnChartArea: false },
            ticks: { font: { size: 11 }, color: COLORS.cumulative },
          },
        },
      },
    });
  }

  // ══════════════════════════════════════════════════════════════
  //                    Таблица простоев
  // ══════════════════════════════════════════════════════════════
  function renderDowntimeTable(periods) {
    var section = document.getElementById('flow-downtime-section');
    var tbody   = document.getElementById('flow-downtime-tbody');
    var countEl = document.getElementById('flow-downtime-count');

    if (!periods || periods.length === 0) {
      if (section) section.style.display = 'none';
      return;
    }

    if (section) section.style.display = '';
    if (countEl) countEl.textContent = periods.length;
    if (!tbody) return;
    tbody.innerHTML = '';

    function fmtDate(iso) {
      var d = new Date(iso);
      var dd = String(d.getDate()).padStart(2, '0');
      var mm = String(d.getMonth() + 1).padStart(2, '0');
      var hh = String(d.getHours()).padStart(2, '0');
      var mi = String(d.getMinutes()).padStart(2, '0');
      return dd + '.' + mm + ' ' + hh + ':' + mi;
    }

    for (var i = 0; i < periods.length; i++) {
      var p = periods[i];
      var tr = document.createElement('tr');
      tr.style.borderBottom = '1px solid #f0f0f0';

      // Длительность
      var durStr;
      if (p.duration_min < 60) {
        durStr = Math.round(p.duration_min) + ' мин';
      } else {
        var h = Math.floor(p.duration_min / 60);
        var m = Math.round(p.duration_min % 60);
        durStr = h + 'ч ' + m + 'м';
      }

      // Тип
      var typeHtml = p.is_blowout
        ? '<span style="background:#ffebee; color:#c62828; padding:2px 8px; border-radius:4px; font-size:10px; font-weight:600;">Продувка</span>'
        : '<span style="background:#fff3e0; color:#e65100; padding:2px 8px; border-radius:4px; font-size:10px; font-weight:600;">Простой</span>';

      tr.innerHTML =
        '<td style="padding:5px 8px; font-size:12px; font-family:monospace;">' + (i + 1) + '</td>' +
        '<td style="padding:5px 8px; font-size:12px; font-family:monospace;">' + fmtDate(p.start) + '</td>' +
        '<td style="padding:5px 8px; font-size:12px; font-family:monospace;">' + fmtDate(p.end) + '</td>' +
        '<td style="padding:5px 8px; text-align:right; font-weight:600; font-size:12px;">' + durStr + '</td>' +
        '<td style="padding:5px 8px; text-align:center;">' + typeHtml + '</td>';
      tbody.appendChild(tr);
    }
  }

  // ═══════════════════ Ввод множителя ═══════════════════
  var multiplierInput = document.getElementById('flow-multiplier-input');
  if (multiplierInput) {
    multiplierInput.value = currentMultiplier;
    multiplierInput.addEventListener('change', function () {
      var val = parseFloat(this.value);
      if (!isNaN(val) && val > 0) {
        currentMultiplier = val;
        loadChart();
      }
    });
    multiplierInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        var val = parseFloat(this.value);
        if (!isNaN(val) && val > 0) {
          currentMultiplier = val;
          loadChart();
        }
        this.blur();
      }
    });
  }

  // ═══════════════════ Сброс зума ═══════════════════
  var resetBtn = document.getElementById('flow-reset-zoom');
  if (resetBtn) {
    resetBtn.addEventListener('click', function () {
      if (flowChart) flowChart.resetZoom();
    });
  }

  // ═══════════════════ Первичная загрузка ═══════════════════
  loadChart(7);

  // ═══════════════════ Экспорт для координатора ═══════════════════
  window.flowRateChartReload = function (days) {
    loadChart(days);
  };

})();
