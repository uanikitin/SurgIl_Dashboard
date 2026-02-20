/**
 * flow_rate_chart.js — График расчётного дебита газа с детекцией продувок
 *
 * Данные: GET /api/flow-rate/calculate/{well_id}?days=N&smooth=true&multiplier=M&...&exclude_periods=...
 * Ответ:  { summary, chart, downtime_periods, purge_cycles, data_points }
 *
 * Продувки подсвечиваются:
 *   Красные  — стравливание (потери газа через штуцер)
 *   Жёлтые   — набор давления (Q=0, потерь нет)
 *   Серые пунктирные — исключённые пользователем
 *
 * Простои (p_tube ≤ p_line) — серые зоны
 *
 * Hover на строке таблицы → подсветка на графике
 * Клик «Исключить»/«Включить» → пересчёт дебита
 * Клик по аннотации на графике → toggle исключения
 */
(function () {
  'use strict';

  console.log('[flow_rate_chart] Script loaded');

  // ═══════════════════ Canvas ═══════════════════
  var canvas = document.getElementById('chart_flow_rate');
  if (!canvas) {
    console.log('[flow_rate_chart] Canvas not found');
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
  var highlightedPurgeId = null;  // ID подсвеченной продувки (hover из таблицы)
  var lastPurgeCycles = null;     // Последние данные для перерисовки подсветки

  // Исключённые циклы — сохраняются в localStorage
  var storageKey = 'flow_exclude_' + wellId;
  var excludedPeriodIds = new Set();
  try {
    var stored = localStorage.getItem(storageKey);
    if (stored) {
      JSON.parse(stored).forEach(function (id) { excludedPeriodIds.add(id); });
    }
  } catch (e) { /* ignore */ }

  function saveExcluded() {
    try {
      localStorage.setItem(storageKey, JSON.stringify(Array.from(excludedPeriodIds)));
    } catch (e) { /* ignore */ }
  }

  // Базовый дебит — сохраняется в localStorage
  var baselineStorageKey = 'flow_baseline_' + wellId;
  var baselineValue = null;
  var lastSummary = null;      // кэш summary для baseline-расчётов
  var lastChartData = null;    // кэш chart data для «текущего» значения
  try {
    var bStored = localStorage.getItem(baselineStorageKey);
    if (bStored) baselineValue = parseFloat(bStored);
  } catch (e) { /* ignore */ }

  // Точка отсчёта — сохраняется в localStorage
  var refPointKey = 'flow_refpoint_' + wellId;
  var refPointTime = null;  // ISO string or null
  try {
    var rpStored = localStorage.getItem(refPointKey);
    if (rpStored) refPointTime = rpStored;
  } catch (e) { /* ignore */ }

  // Анализ участков — состояние
  var segmentMode = false;
  var segmentClickStart = null;  // ISO string, первый клик
  var currentSegStart = null;    // ISO string, после второго клика
  var currentSegEnd = null;      // ISO string, после второго клика
  var savedSegments = [];        // загруженные из БД

  // Коэффициенты (из input-полей)
  function getCoeffs() {
    return {
      M:    parseFloat((document.getElementById('flow-coeff-M')    || {}).value) || 4.1,
      C1:   parseFloat((document.getElementById('flow-coeff-C1')   || {}).value) || 2.919,
      C2:   parseFloat((document.getElementById('flow-coeff-C2')   || {}).value) || 4.654,
      C3:   parseFloat((document.getElementById('flow-coeff-C3')   || {}).value) || 286.95,
      Rcrit: parseFloat((document.getElementById('flow-coeff-Rcrit') || {}).value) || 0.5,
    };
  }

  // ═══════════════════ Цвета ═══════════════════
  var COLORS = {
    flowRate:    '#ff9800',
    flowFill:    'rgba(255, 152, 0, 0.12)',
    cumulative:  '#1565c0',
    grid:        '#e9ecef',
    // Продувки
    venting:     'rgba(229, 57, 53, 0.22)',
    ventingBord: '#e53935',
    buildup:     'rgba(255, 193, 7, 0.18)',
    buildupBord: '#ffa000',
    excluded:    'rgba(158, 158, 158, 0.10)',
    excludedBord:'#bdbdbd',
    downtime:    'rgba(120, 120, 120, 0.08)',
    downtimeBord:'#ccc',
    // Подсветка (hover из таблицы)
    hlVenting:     'rgba(229, 57, 53, 0.55)',
    hlVentingBord: '#b71c1c',
    hlBuildup:     'rgba(255, 193, 7, 0.45)',
    hlBuildupBord: '#ff6f00',
    hlExcluded:    'rgba(158, 158, 158, 0.30)',
    hlExcludedBord:'#757575',
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

  // ═══════════════════ Элементы DOM ═══════════════════
  var loadingOverlay = document.getElementById('flow-chart-loading');
  var statsLoading   = document.getElementById('flow-stats-loading');
  var mainContent    = document.getElementById('flow-main-content');

  function showLoading() {
    if (loadingOverlay) loadingOverlay.style.display = 'flex';
    if (statsLoading)   statsLoading.style.display   = 'block';
    if (mainContent)    mainContent.style.display     = 'none';
  }

  function hideLoading() {
    if (loadingOverlay) loadingOverlay.style.display = 'none';
    if (statsLoading)   statsLoading.style.display   = 'none';
    if (mainContent)    mainContent.style.display     = '';
  }

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  // ═══════════════════ Toggle кнопки ═══════════════════
  var infoBtn   = document.getElementById('flow-toggle-info');
  var infoBlock = document.getElementById('flow-info-block');
  if (infoBtn && infoBlock) {
    infoBtn.addEventListener('click', function () {
      var vis = infoBlock.style.display !== 'none';
      infoBlock.style.display = vis ? 'none' : 'block';
      infoBtn.style.background = vis ? '#f8f9fa' : '#e3f2fd';
      infoBtn.style.borderColor = vis ? '#dee2e6' : '#90caf9';
      infoBtn.style.color = vis ? '#666' : '#1565c0';
    });
  }

  var coeffsBtn   = document.getElementById('flow-toggle-coeffs');
  var coeffsBlock = document.getElementById('flow-coeffs-block');
  if (coeffsBtn && coeffsBlock) {
    coeffsBtn.addEventListener('click', function () {
      var vis = coeffsBlock.style.display !== 'none';
      coeffsBlock.style.display = vis ? 'none' : 'block';
      coeffsBtn.style.background = vis ? '#f8f9fa' : '#fff8e1';
      coeffsBtn.style.borderColor = vis ? '#dee2e6' : '#ffe0b2';
      coeffsBtn.style.color = vis ? '#666' : '#e65100';
    });
  }

  var applyBtn = document.getElementById('flow-apply-coeffs');
  if (applyBtn) {
    applyBtn.addEventListener('click', function () {
      loadChart();
    });
  }

  // ══════════════════════════════════════════════════════════════
  //                    ГЛАВНАЯ: loadChart
  // ══════════════════════════════════════════════════════════════
  async function loadChart(days) {
    if (days !== undefined) currentDays = days;
    var coeffs = getCoeffs();

    console.log('[flow_rate_chart] loadChart, days:', currentDays);
    showLoading();

    try {
      var url = '/api/flow-rate/calculate/' + wellId +
                '?days=' + currentDays +
                '&smooth=true' +
                '&multiplier=' + coeffs.M +
                '&C1=' + coeffs.C1 +
                '&C2=' + coeffs.C2 +
                '&C3=' + coeffs.C3 +
                '&critical_ratio=' + coeffs.Rcrit;

      if (excludedPeriodIds.size > 0) {
        url += '&exclude_periods=' + Array.from(excludedPeriodIds).join(',');
      }

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
      lastPurgeCycles = json.purge_cycles || [];
      lastChartData = json.chart;

      updateStatsPanel(json.summary);
      renderChart(json.chart, json.downtime_periods, json.purge_cycles);
      renderPurgeTable(json.purge_cycles, json.downtime_periods);
      hideLoading();

    } catch (err) {
      console.error('[flow_rate_chart] Ошибка загрузки:', err);
      showNoData('Ошибка загрузки данных дебита');
      hideLoading();
    }
  }

  // ══════════════════════════════════════════════════════════════
  //                    Панель статистики (правая)
  // ══════════════════════════════════════════════════════════════
  function updateStatsPanel(summary) {
    if (!summary || summary.error) {
      ['flow-stat-median', 'flow-stat-mean', 'flow-stat-cumulative',
       'flow-stat-total-loss', 'flow-stat-loss-pct',
       'flow-stat-effective', 'flow-stat-obs-days', 'flow-stat-downtime-hours',
       'flow-stat-choke', 'flow-stat-downtime-count',
       'flow-stat-venting-count', 'flow-stat-venting-hours',
       'flow-stat-buildup-hours', 'flow-stat-purge-loss',
       'flow-stat-purge-source'].forEach(function (id) { setText(id, '\u2014'); });
      return;
    }

    setText('flow-stat-median',
      summary.median_flow_rate != null ? summary.median_flow_rate.toFixed(3) + ' т.м\u00B3/с' : '\u2014');
    setText('flow-stat-mean',
      summary.mean_flow_rate != null ? summary.mean_flow_rate.toFixed(3) + ' т.м\u00B3/с' : '\u2014');
    setText('flow-stat-effective',
      summary.effective_flow_rate != null ? summary.effective_flow_rate.toFixed(3) + ' т.м\u00B3/с' : '\u2014');
    setText('flow-stat-cumulative',
      summary.cumulative_flow != null ? summary.cumulative_flow.toFixed(2) + ' т.м\u00B3' : '\u2014');
    setText('flow-stat-downtime-count',
      summary.total_downtime_periods != null ? String(summary.total_downtime_periods) : '\u2014');
    setText('flow-stat-total-loss',
      summary.total_loss_daily != null ? summary.total_loss_daily.toFixed(4) + ' т.м\u00B3/с' : '\u2014');
    setText('flow-stat-loss-pct',
      summary.loss_coefficient_pct != null ? summary.loss_coefficient_pct.toFixed(2) + '%' : '\u2014');
    setText('flow-stat-downtime-hours',
      summary.downtime_total_hours != null ? summary.downtime_total_hours.toFixed(1) + ' ч' : '\u2014');
    setText('flow-stat-obs-days',
      summary.observation_days != null ? summary.observation_days.toFixed(1) + ' дн' : '\u2014');
    setText('flow-stat-choke',
      summary.choke_mm != null ? summary.choke_mm.toFixed(1) + ' мм' : '\u2014');

    setText('flow-stat-venting-count',
      summary.purge_venting_count != null ? String(summary.purge_venting_count) : '\u2014');
    setText('flow-stat-venting-hours',
      summary.purge_venting_hours != null ? summary.purge_venting_hours.toFixed(1) + ' ч' : '\u2014');
    setText('flow-stat-buildup-hours',
      summary.purge_buildup_hours != null ? summary.purge_buildup_hours.toFixed(1) + ' ч' : '\u2014');
    setText('flow-stat-purge-loss',
      summary.purge_loss_total != null ? summary.purge_loss_total.toFixed(3) + ' т.м\u00B3' : '\u2014');

    var src = '';
    if (summary.purge_marker_count > 0) src += summary.purge_marker_count + ' марк.';
    if (summary.purge_algorithm_count > 0) {
      if (src) src += ' + ';
      src += summary.purge_algorithm_count + ' алго.';
    }
    setText('flow-stat-purge-source', src || '\u2014');

    // Кэшируем для baseline/refpoint-расчётов
    lastSummary = summary;
    updateBaselineStats();
    updateRefPointStats();
  }

  // ══════════════════════════════════════════════════════════════
  //           Обновление курсора при hover (внешний tooltip)
  // ══════════════════════════════════════════════════════════════
  function updateCursorPanel(context) {
    var tooltip = context.tooltip;
    var timeEl  = document.getElementById('flow-cursor-time');
    var valueEl = document.getElementById('flow-cursor-value');
    if (!timeEl || !valueEl) return;

    if (tooltip.opacity === 0 || !tooltip.dataPoints || !tooltip.dataPoints.length) return;

    var dp = tooltip.dataPoints[0];
    if (!dp) return;

    var d = new Date(dp.parsed.x);
    var dd = String(d.getDate()).padStart(2, '0');
    var mm = String(d.getMonth() + 1).padStart(2, '0');
    var hh = String(d.getHours()).padStart(2, '0');
    var mi = String(d.getMinutes()).padStart(2, '0');
    timeEl.textContent = dd + '.' + mm + ' ' + hh + ':' + mi;

    var val = dp.parsed.y;
    valueEl.textContent = val.toFixed(3) + ' тыс. м\u00B3/сут';
    valueEl.style.color = val > 0 ? '#ff9800' : '#c62828';
  }

  // ══════════════════════════════════════════════════════════════
  //         Подсветка аннотации на графике (highlight purge)
  // ══════════════════════════════════════════════════════════════
  function highlightPurgeOnChart(purgeId) {
    if (!flowChart) return;
    var anns = flowChart.options.plugins.annotation.annotations;
    if (!anns) return;

    var changed = false;

    for (var key in anns) {
      if (!anns.hasOwnProperty(key)) continue;
      var ann = anns[key];
      if (!ann._purgeId) continue;

      var isTarget = ann._purgeId === purgeId;
      var isExcluded = ann._isExcluded;

      if (isTarget && purgeId) {
        // Подсветить — ярче, толще
        if (isExcluded) {
          ann.backgroundColor = COLORS.hlExcluded;
          ann.borderColor = COLORS.hlExcludedBord;
        } else if (key.indexOf('vent') >= 0) {
          ann.backgroundColor = COLORS.hlVenting;
          ann.borderColor = COLORS.hlVentingBord;
        } else {
          ann.backgroundColor = COLORS.hlBuildup;
          ann.borderColor = COLORS.hlBuildupBord;
        }
        ann.borderWidth = 3;
        changed = true;
      } else {
        // Вернуть обычные цвета
        if (isExcluded) {
          ann.backgroundColor = COLORS.excluded;
          ann.borderColor = COLORS.excludedBord;
          ann.borderWidth = 1;
        } else if (key.indexOf('vent') >= 0 || key.indexOf('exc_v') >= 0) {
          ann.backgroundColor = COLORS.venting;
          ann.borderColor = COLORS.ventingBord;
          ann.borderWidth = 1;
        } else if (key.indexOf('build') >= 0) {
          ann.backgroundColor = COLORS.buildup;
          ann.borderColor = COLORS.buildupBord;
          ann.borderWidth = 1;
        }
        changed = true;
      }
    }

    if (changed) {
      flowChart.update('none');
    }

    // Скроллим к продувке (pan x-axis), если подсвечиваем
    if (purgeId && flowChart) {
      panToPurge(purgeId);
    }
  }

  function panToPurge(purgeId) {
    if (!lastPurgeCycles || !flowChart) return;
    var cycle = null;
    for (var i = 0; i < lastPurgeCycles.length; i++) {
      if (lastPurgeCycles[i].id === purgeId) {
        cycle = lastPurgeCycles[i];
        break;
      }
    }
    if (!cycle) return;

    var startTime = cycle.venting_start || cycle.buildup_start;
    var endTime = cycle.buildup_end || cycle.venting_end || cycle.restart_time;
    if (!startTime) return;

    var xScale = flowChart.scales.x;
    var currentMin = xScale.min;
    var currentMax = xScale.max;
    var currentRange = currentMax - currentMin;

    var targetCenter = new Date(startTime).getTime();
    if (endTime) {
      targetCenter = (new Date(startTime).getTime() + new Date(endTime).getTime()) / 2;
    }

    // Если продувка уже видна — не скроллим
    var targetStart = new Date(startTime).getTime();
    var targetEnd = endTime ? new Date(endTime).getTime() : targetStart;
    if (targetStart >= currentMin && targetEnd <= currentMax) return;

    // Скроллим так, чтобы продувка была по центру
    var newMin = targetCenter - currentRange / 2;
    var newMax = targetCenter + currentRange / 2;
    xScale.options.min = newMin;
    xScale.options.max = newMax;
    flowChart.update('none');
  }

  // ══════════════════════════════════════════════════════════════
  //                    Рендеринг графика
  // ══════════════════════════════════════════════════════════════
  function renderChart(chartData, downtimePeriods, purgeCycles) {
    if (!chartData || !chartData.timestamps || chartData.timestamps.length === 0) {
      showNoData('Нет данных для графика дебита');
      return;
    }

    var flowData = [];
    var cumulativeData = [];
    for (var i = 0; i < chartData.timestamps.length; i++) {
      flowData.push({ x: chartData.timestamps[i], y: chartData.flow_rate[i] });
      cumulativeData.push({ x: chartData.timestamps[i], y: chartData.cumulative_flow[i] });
    }

    // ── Аннотации ──
    var annotations = {};

    // 1) Продувки
    if (purgeCycles && purgeCycles.length > 0) {
      for (var k = 0; k < purgeCycles.length; k++) {
        var pc = purgeCycles[k];

        if (pc.excluded) {
          if (pc.venting_start && pc.venting_end) {
            annotations['exc_v_' + k] = {
              type: 'box',
              xMin: pc.venting_start,
              xMax: pc.venting_end,
              backgroundColor: COLORS.excluded,
              borderColor: COLORS.excludedBord,
              borderWidth: 1,
              borderDash: [4, 4],
              label: {
                display: purgeCycles.length <= 30,
                content: '\u2716 искл.',
                position: 'start',
                font: { size: 8 },
                color: '#999',
              },
              _purgeId: pc.id,
              _isExcluded: true,
            };
          }
          continue;
        }

        // Фаза стравливания — КРАСНАЯ
        if (pc.venting_start && pc.venting_end) {
          annotations['vent_' + k] = {
            type: 'box',
            xMin: pc.venting_start,
            xMax: pc.venting_end,
            backgroundColor: COLORS.venting,
            borderColor: COLORS.ventingBord,
            borderWidth: 1,
            label: {
              display: purgeCycles.length <= 20,
              content: 'Стравл. ' + pc.venting_duration_min + 'мин',
              position: 'start',
              font: { size: 9 },
              color: '#c62828',
            },
            _purgeId: pc.id,
            _isExcluded: false,
          };
        }

        // Фаза набора — ЖЁЛТАЯ
        if (pc.buildup_start && pc.buildup_end) {
          annotations['build_' + k] = {
            type: 'box',
            xMin: pc.buildup_start,
            xMax: pc.buildup_end,
            backgroundColor: COLORS.buildup,
            borderColor: COLORS.buildupBord,
            borderWidth: 1,
            label: {
              display: purgeCycles.length <= 20,
              content: 'Набор ' + pc.buildup_duration_min + 'мин',
              position: 'start',
              font: { size: 9 },
              color: '#e65100',
            },
            _purgeId: pc.id,
            _isExcluded: false,
          };
        }
      }
    }

    // 2) Простои
    if (downtimePeriods && downtimePeriods.length > 0) {
      for (var j = 0; j < downtimePeriods.length; j++) {
        var dp = downtimePeriods[j];
        annotations['dt_' + j] = {
          type: 'box',
          xMin: dp.start,
          xMax: dp.end,
          backgroundColor: COLORS.downtime,
          borderColor: COLORS.downtimeBord,
          borderWidth: 0.5,
          drawTime: 'beforeDatasetsDraw',
          z: -1,
        };
      }
    }

    // 3) Базовый дебит — горизонтальная линия
    if (baselineValue != null && baselineValue > 0) {
      annotations['baseline'] = {
        type: 'line',
        yMin: baselineValue,
        yMax: baselineValue,
        yScaleID: 'y',
        borderColor: '#4caf50',
        borderWidth: 2,
        borderDash: [8, 4],
        label: {
          display: true,
          content: 'Базовый: ' + baselineValue.toFixed(1),
          position: 'start',
          font: { size: 10, weight: 'bold' },
          color: '#2e7d32',
          backgroundColor: 'rgba(255,255,255,0.85)',
          padding: 3,
        },
      };
    }

    // 4) Точка отсчёта — вертикальная линия
    if (refPointTime) {
      var rpVal = getFlowAtTime(chartData, refPointTime);
      annotations['refpoint'] = {
        type: 'line',
        xMin: refPointTime,
        xMax: refPointTime,
        borderColor: '#1565c0',
        borderWidth: 2,
        borderDash: [6, 3],
        label: {
          display: true,
          content: fmtRefPointLabel(refPointTime, rpVal),
          position: 'start',
          font: { size: 10, weight: 'bold' },
          color: '#1565c0',
          backgroundColor: 'rgba(255,255,255,0.9)',
          padding: 3,
        },
      };
    }

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
        onClick: handleChartClick,
        plugins: {
          legend: {
            display: true,
            position: 'top',
            labels: { usePointStyle: true, padding: 15, font: { size: 12 } },
          },
          tooltip: {
            enabled: false,
            external: updateCursorPanel,
          },
          annotation: { annotations: annotations },
          zoom: {
            pan: {
              enabled: true,
              mode: 'x',
              threshold: 5,
              onPanComplete: function (ctx) { syncZoomToOthers(ctx.chart); },
            },
            zoom: {
              drag: {
                enabled: !window._syncPanMode,
                backgroundColor: 'rgba(255, 152, 0, 0.08)',
                borderColor: 'rgba(255, 152, 0, 0.4)',
                borderWidth: 1,
              },
              mode: 'x',
              onZoomComplete: function (ctx) { syncZoomToOthers(ctx.chart); },
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
              text: 'Дебит (тыс. м\u00B3/сут)',
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
              text: 'Накопленный (тыс. м\u00B3)',
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

    // Pan OFF по умолчанию (drag-zoom): отключаем pan после создания
    if (!window._syncPanMode) {
      flowChart.options.plugins.zoom.pan.enabled = false;
      flowChart.update('none');
    }
  }

  // ══════════════════════════════════════════════════════════════
  //           Клик по графику — toggle исключения продувки
  // ══════════════════════════════════════════════════════════════
  function handleChartClick(event, elements, chart) {
    if (!chart || !chart.options || !chart.options.plugins ||
        !chart.options.plugins.annotation) return;

    var anns = chart.options.plugins.annotation.annotations;
    if (!anns) return;

    var rect = canvas.getBoundingClientRect();
    var clickX = event.native.clientX - rect.left;
    var xScale = chart.scales.x;
    var clickTime = xScale.getValueForPixel(clickX);

    // Shift+Click → установить точку отсчёта
    if (event.native.shiftKey) {
      var isoTime = new Date(clickTime).toISOString();
      setRefPoint(isoTime);
      return;
    }

    // Segment mode — выделение участка кликами
    if (segmentMode) {
      // Используем ближайший timestamp из chart data (Кунград, без TZ)
      // вместо toISOString() который зависит от часового пояса браузера
      var clickISO;
      if (lastChartData && lastChartData.timestamps && lastChartData.timestamps.length > 0) {
        var bestIdx = 0, bestDist = Infinity;
        for (var ci = 0; ci < lastChartData.timestamps.length; ci++) {
          var d = Math.abs(new Date(lastChartData.timestamps[ci]).getTime() - clickTime);
          if (d < bestDist) { bestDist = d; bestIdx = ci; }
        }
        clickISO = lastChartData.timestamps[bestIdx];
      } else {
        clickISO = new Date(clickTime).toISOString();
      }
      if (!segmentClickStart) {
        // Первый клик — начало участка
        segmentClickStart = clickISO;
        anns['seg_start_line'] = {
          type: 'line',
          xMin: clickISO,
          xMax: clickISO,
          borderColor: '#9c27b0',
          borderWidth: 2,
          borderDash: [6, 3],
          label: {
            display: true,
            content: 'Начало',
            position: 'start',
            font: { size: 10, weight: 'bold' },
            color: '#9c27b0',
            backgroundColor: 'rgba(255,255,255,0.9)',
            padding: 3,
          },
        };
        chart.update('none');
      } else {
        // Второй клик — конец участка
        var startISO = segmentClickStart;
        var endISO = clickISO;
        // Гарантируем правильный порядок
        if (new Date(endISO) < new Date(startISO)) {
          var tmp = startISO; startISO = endISO; endISO = tmp;
        }
        currentSegStart = startISO;
        currentSegEnd = endISO;
        segmentClickStart = null;
        // Убираем линию старта, ставим box
        delete anns['seg_start_line'];
        anns['seg_preview_box'] = {
          type: 'box',
          xMin: startISO,
          xMax: endISO,
          backgroundColor: 'rgba(156,39,176,0.1)',
          borderColor: '#9c27b0',
          borderWidth: 1.5,
          borderDash: [4, 2],
          drawTime: 'beforeDatasetsDraw',
        };
        chart.update('none');
        // Запрос статистики
        fetchSegmentStats(startISO, endISO);
      }
      return;
    }

    for (var key in anns) {
      if (!anns.hasOwnProperty(key)) continue;
      var ann = anns[key];
      if (!ann._purgeId) continue;

      var annMinTime = new Date(ann.xMin).getTime();
      var annMaxTime = new Date(ann.xMax).getTime();

      if (clickTime >= annMinTime && clickTime <= annMaxTime) {
        var pid = ann._purgeId;
        if (excludedPeriodIds.has(pid)) {
          excludedPeriodIds.delete(pid);
        } else {
          excludedPeriodIds.add(pid);
        }
        saveExcluded();
        loadChart();
        return;
      }
    }
  }

  // ══════════════════════════════════════════════════════════════
  //         Таблица продувок и простоев (объединённая, по времени)
  // ══════════════════════════════════════════════════════════════
  function renderPurgeTable(purgeCycles, downtimePeriods) {
    var section = document.getElementById('flow-downtime-section');
    var tbody   = document.getElementById('flow-downtime-tbody');
    var countEl = document.getElementById('flow-downtime-count');

    var hasPurges   = purgeCycles && purgeCycles.length > 0;
    var hasDowntime = downtimePeriods && downtimePeriods.length > 0;

    if (!hasPurges && !hasDowntime) {
      if (section) section.style.display = 'none';
      return;
    }

    if (section) section.style.display = '';
    if (!tbody) return;
    tbody.innerHTML = '';

    function fmtDate(iso) {
      if (!iso) return '\u2014';
      var d = new Date(iso);
      var dd = String(d.getDate()).padStart(2, '0');
      var mm = String(d.getMonth() + 1).padStart(2, '0');
      var hh = String(d.getHours()).padStart(2, '0');
      var mi = String(d.getMinutes()).padStart(2, '0');
      return dd + '.' + mm + ' ' + hh + ':' + mi;
    }

    function fmtDur(min) {
      if (!min && min !== 0) return '\u2014';
      if (min < 60) return Math.round(min) + ' мин';
      var h = Math.floor(min / 60);
      var m = Math.round(min % 60);
      return h + 'ч ' + m + 'м';
    }

    // Собираем все строки в единый массив и сортируем по времени
    var allRows = [];

    if (hasPurges) {
      for (var i = 0; i < purgeCycles.length; i++) {
        allRows.push({ type: 'purge', data: purgeCycles[i], sortTime: purgeCycles[i].venting_start || purgeCycles[i].buildup_start });
      }
    }

    if (hasDowntime) {
      for (var j = 0; j < downtimePeriods.length; j++) {
        allRows.push({ type: 'downtime', data: downtimePeriods[j], sortTime: downtimePeriods[j].start });
      }
    }

    allRows.sort(function (a, b) {
      return new Date(a.sortTime).getTime() - new Date(b.sortTime).getTime();
    });

    for (var n = 0; n < allRows.length; n++) {
      var row = allRows[n];
      var tr = document.createElement('tr');
      tr.style.borderBottom = '1px solid #f0f0f0';
      tr.style.transition = 'background 0.15s';

      if (row.type === 'purge') {
        var pc = row.data;
        tr.style.cursor = 'pointer';
        tr.setAttribute('data-purge-id', pc.id);
        if (pc.excluded) tr.style.opacity = '0.45';

        // Тип
        var typeHtml;
        if (pc.excluded) {
          typeHtml = '<span style="background:#f5f5f5; color:#999; padding:2px 8px; border-radius:4px; font-size:10px;">Исключена</span>';
        } else {
          typeHtml = '<span style="background:#ffebee; color:#c62828; padding:2px 8px; border-radius:4px; font-size:10px; font-weight:600;">Продувка</span>';
        }

        // Источник
        var srcHtml = pc.source === 'marker'
          ? '<span style="color:#1565c0; font-size:10px;">маркер</span>'
          : '<span style="color:#7b1fa2; font-size:10px;">алго</span>';

        // Уверенность
        var confColor = pc.confidence >= 0.8 ? '#2e7d32' : (pc.confidence >= 0.5 ? '#e65100' : '#c62828');
        var confHtml = '<span style="color:' + confColor + '; font-size:10px; font-weight:600;">' +
                       (pc.confidence * 100).toFixed(0) + '%</span>';

        // Кнопка исключения
        var btnHtml;
        if (pc.excluded) {
          btnHtml = '<button data-purge-id="' + pc.id + '" class="purge-toggle-btn" ' +
                    'style="background:#e8f5e9; border:1px solid #a5d6a7; color:#2e7d32; ' +
                    'padding:2px 8px; border-radius:4px; font-size:10px; cursor:pointer;">' +
                    'Включить</button>';
        } else {
          btnHtml = '<button data-purge-id="' + pc.id + '" class="purge-toggle-btn" ' +
                    'style="background:#fce4ec; border:1px solid #ef9a9a; color:#c62828; ' +
                    'padding:2px 8px; border-radius:4px; font-size:10px; cursor:pointer;">' +
                    'Исключить</button>';
        }

        tr.innerHTML =
          '<td style="padding:4px 6px; font-size:11px; font-family:monospace;">' + (n + 1) + '</td>' +
          '<td style="padding:4px 6px; text-align:center;">' + typeHtml + '</td>' +
          '<td style="padding:4px 6px; font-size:11px; font-family:monospace;">' + fmtDate(pc.venting_start) + '</td>' +
          '<td style="padding:4px 6px; font-size:11px; font-family:monospace;">' + fmtDate(pc.buildup_end || pc.venting_end) + '</td>' +
          '<td style="padding:4px 6px; text-align:right; font-size:11px;">' + fmtDur(pc.venting_duration_min) + '</td>' +
          '<td style="padding:4px 6px; text-align:right; font-size:11px;">' + fmtDur(pc.buildup_duration_min) + '</td>' +
          '<td style="padding:4px 6px; text-align:center;">' + srcHtml + '</td>' +
          '<td style="padding:4px 6px; text-align:center;">' + confHtml + '</td>' +
          '<td style="padding:4px 6px; text-align:center;">' + btnHtml + '</td>';

      } else {
        // Простой (downtime)
        var dt = row.data;
        tr.style.background = '#fafafa';

        var dtTypeHtml = '<span style="background:#f5f5f5; color:#795548; padding:2px 8px; border-radius:4px; font-size:10px;">Простой</span>';

        tr.innerHTML =
          '<td style="padding:4px 6px; font-size:11px; font-family:monospace; color:#999;">' + (n + 1) + '</td>' +
          '<td style="padding:4px 6px; text-align:center;">' + dtTypeHtml + '</td>' +
          '<td style="padding:4px 6px; font-size:11px; font-family:monospace; color:#999;">' + fmtDate(dt.start) + '</td>' +
          '<td style="padding:4px 6px; font-size:11px; font-family:monospace; color:#999;">' + fmtDate(dt.end) + '</td>' +
          '<td style="padding:4px 6px; text-align:right; font-size:11px; color:#999;" colspan="2">' + fmtDur(dt.duration_min) + '</td>' +
          '<td style="padding:4px 6px; text-align:center; color:#999; font-size:10px;" colspan="3">\u2014</td>';
      }

      tbody.appendChild(tr);
    }

    if (countEl) {
      var purgeCount = purgeCycles ? purgeCycles.length : 0;
      var dtCount = downtimePeriods ? downtimePeriods.length : 0;
      var parts = [];
      if (purgeCount > 0) parts.push(purgeCount + ' прод.');
      if (dtCount > 0) parts.push(dtCount + ' прост.');
      countEl.textContent = parts.join(' / ');
    }

    // ── Обработчики: hover строки продувки → подсветка на графике ──
    var purgeRows = tbody.querySelectorAll('tr[data-purge-id]');
    for (var r = 0; r < purgeRows.length; r++) {
      purgeRows[r].addEventListener('mouseenter', function () {
        var pid = this.getAttribute('data-purge-id');
        this.style.background = '#e3f2fd';
        highlightedPurgeId = pid;
        highlightPurgeOnChart(pid);
      });
      purgeRows[r].addEventListener('mouseleave', function () {
        this.style.background = '';
        highlightedPurgeId = null;
        highlightPurgeOnChart(null);
      });
    }

    // ── Обработчики: кнопки исключения ──
    var toggleBtns = tbody.querySelectorAll('.purge-toggle-btn');
    for (var b = 0; b < toggleBtns.length; b++) {
      toggleBtns[b].addEventListener('click', function (e) {
        e.stopPropagation();
        var pid = this.getAttribute('data-purge-id');
        if (excludedPeriodIds.has(pid)) {
          excludedPeriodIds.delete(pid);
        } else {
          excludedPeriodIds.add(pid);
        }
        saveExcluded();
        loadChart();  // пересчёт дебита с новыми исключениями
      });
    }
  }

  // ══════════════════════════════════════════════════════════════
  //     Синхронизация zoom/pan с другими графиками
  // ══════════════════════════════════════════════════════════════

  /** Синхронизация: дебит → давление + ΔP */
  function syncZoomToOthers(chart) {
    var xScale = chart.scales.x;
    if (!xScale) return;
    if (window.syncChart && window.syncChart.syncZoom) {
      window.syncChart.syncZoom(xScale.min, xScale.max);
    }
    if (window.deltaChart && window.deltaChart.syncZoom) {
      window.deltaChart.syncZoom(xScale.min, xScale.max);
    }
  }

  /** Синхронизация: давление/ΔP → дебит (вызывается извне) */
  function syncZoom(xMin, xMax) {
    if (!flowChart) return;
    flowChart.options.scales.x.min = xMin;
    flowChart.options.scales.x.max = xMax;
    flowChart.update('none');
  }

  function resetSyncZoom() {
    if (!flowChart) return;
    delete flowChart.options.scales.x.min;
    delete flowChart.options.scales.x.max;
    flowChart.update('none');
  }

  // ══════════════════════════════════════════════════════════════
  //                    Базовый дебит — обновление
  // ══════════════════════════════════════════════════════════════
  function updateBaselineOnChart() {
    console.log('[flow_rate_chart] updateBaselineOnChart:', {
      baselineValue: baselineValue, hasChart: !!flowChart,
      lastSummary: !!lastSummary, lastChartData: !!lastChartData
    });
    if (!flowChart) return;
    var anns = flowChart.options.plugins.annotation.annotations;
    if (baselineValue != null && baselineValue > 0) {
      anns['baseline'] = {
        type: 'line',
        yMin: baselineValue,
        yMax: baselineValue,
        yScaleID: 'y',
        borderColor: '#4caf50',
        borderWidth: 2,
        borderDash: [8, 4],
        label: {
          display: true,
          content: 'Базовый: ' + baselineValue.toFixed(1),
          position: 'start',
          font: { size: 10, weight: 'bold' },
          color: '#2e7d32',
          backgroundColor: 'rgba(255,255,255,0.85)',
          padding: 3,
        },
      };
    } else {
      delete anns['baseline'];
    }
    flowChart.update('none');
    updateBaselineStats();
  }

  function setDelta(id, result) {
    var el = document.getElementById(id);
    if (!el) return;
    if (result == null) { el.textContent = '\u2014'; el.style.color = '#333'; return; }
    el.textContent = result.text || '\u2014';
    el.style.color = result.color || '#333';
  }

  function fmtDelta(v) {
    if (v == null) return null;
    var sign = v >= 0 ? '+' : '';
    var color = v >= 0 ? '#2e7d32' : '#c62828';
    return { text: sign + v.toFixed(3), color: color };
  }

  function updateBaselineStats() {
    var section = document.getElementById('flow-baseline-stats');
    if (!section) return;

    if (baselineValue == null || baselineValue <= 0 || !lastSummary) {
      section.style.display = 'none';
      return;
    }
    section.style.display = '';

    var base = baselineValue;

    // Δ текущий = последнее значение flow_rate - baseline
    var currentQ = null;
    if (lastChartData && lastChartData.flow_rate && lastChartData.flow_rate.length > 0) {
      for (var i = lastChartData.flow_rate.length - 1; i >= 0; i--) {
        if (lastChartData.flow_rate[i] != null) {
          currentQ = lastChartData.flow_rate[i];
          break;
        }
      }
    }
    var deltaCurrent = currentQ != null ? currentQ - base : null;

    // Δ среднее = mean - baseline
    var deltaMean = lastSummary.mean_flow_rate != null
      ? lastSummary.mean_flow_rate - base : null;

    // Δ медиана = median - baseline
    var deltaMedian = lastSummary.median_flow_rate != null
      ? lastSummary.median_flow_rate - base : null;

    // Прирост/сут = effective - baseline
    var dailyDelta = lastSummary.effective_flow_rate != null
      ? lastSummary.effective_flow_rate - base : null;

    setDelta('flow-base-delta-current', fmtDelta(deltaCurrent));
    setDelta('flow-base-delta-mean', fmtDelta(deltaMean));
    setDelta('flow-base-delta-median', fmtDelta(deltaMedian));
    setDelta('flow-base-daily-delta', fmtDelta(dailyDelta));
  }

  // ══════════════════════════════════════════════════════════════
  //                    Точка отсчёта
  // ══════════════════════════════════════════════════════════════
  function getFlowAtTime(chartData, isoTime) {
    if (!chartData || !chartData.timestamps) return null;
    var target = new Date(isoTime).getTime();
    var bestIdx = 0;
    var bestDist = Infinity;
    for (var i = 0; i < chartData.timestamps.length; i++) {
      var dist = Math.abs(new Date(chartData.timestamps[i]).getTime() - target);
      if (dist < bestDist) { bestDist = dist; bestIdx = i; }
    }
    return chartData.flow_rate[bestIdx];
  }

  function fmtRefPointLabel(isoTime, val) {
    var d = new Date(isoTime);
    var dd = String(d.getDate()).padStart(2, '0');
    var mm = String(d.getMonth() + 1).padStart(2, '0');
    var hh = String(d.getHours()).padStart(2, '0');
    var mi = String(d.getMinutes()).padStart(2, '0');
    var label = dd + '.' + mm + ' ' + hh + ':' + mi;
    if (val != null) label += ' | ' + val.toFixed(2);
    return label;
  }

  function isoToDatetimeLocal(iso) {
    var d = new Date(iso);
    var yyyy = d.getFullYear();
    var mm = String(d.getMonth() + 1).padStart(2, '0');
    var dd = String(d.getDate()).padStart(2, '0');
    var hh = String(d.getHours()).padStart(2, '0');
    var mi = String(d.getMinutes()).padStart(2, '0');
    return yyyy + '-' + mm + '-' + dd + 'T' + hh + ':' + mi;
  }

  function setRefPoint(isoTime) {
    refPointTime = isoTime;
    localStorage.setItem(refPointKey, isoTime);
    var inp = document.getElementById('flow-refpoint-input');
    if (inp) inp.value = isoToDatetimeLocal(isoTime);
    updateRefPointOnChart();
  }

  function clearRefPoint() {
    refPointTime = null;
    localStorage.removeItem(refPointKey);
    var inp = document.getElementById('flow-refpoint-input');
    if (inp) inp.value = '';
    updateRefPointOnChart();
  }

  function updateRefPointOnChart() {
    if (!flowChart) return;
    var anns = flowChart.options.plugins.annotation.annotations;
    if (refPointTime) {
      var rpVal = getFlowAtTime(lastChartData, refPointTime);
      anns['refpoint'] = {
        type: 'line',
        xMin: refPointTime,
        xMax: refPointTime,
        borderColor: '#1565c0',
        borderWidth: 2,
        borderDash: [6, 3],
        label: {
          display: true,
          content: fmtRefPointLabel(refPointTime, rpVal),
          position: 'start',
          font: { size: 10, weight: 'bold' },
          color: '#1565c0',
          backgroundColor: 'rgba(255,255,255,0.9)',
          padding: 3,
        },
      };
    } else {
      delete anns['refpoint'];
    }
    flowChart.update('none');
    updateRefPointStats();
  }

  function updateRefPointStats() {
    var section = document.getElementById('flow-refpoint-stats');
    if (!section) return;

    if (!refPointTime || !lastChartData || !lastChartData.timestamps) {
      section.style.display = 'none';
      return;
    }

    var refMs = new Date(refPointTime).getTime();
    var vals = [];
    for (var i = 0; i < lastChartData.timestamps.length; i++) {
      if (new Date(lastChartData.timestamps[i]).getTime() >= refMs) {
        if (lastChartData.flow_rate[i] != null) {
          vals.push(lastChartData.flow_rate[i]);
        }
      }
    }

    if (vals.length === 0) {
      section.style.display = 'none';
      return;
    }
    section.style.display = '';

    // Период
    var lastTs = new Date(lastChartData.timestamps[lastChartData.timestamps.length - 1]).getTime();
    var durationHours = (lastTs - refMs) / 3600000;
    var durationText = durationHours >= 24
      ? (durationHours / 24).toFixed(1) + ' дн'
      : durationHours.toFixed(1) + ' ч';
    setText('flow-ref-duration', durationText);

    // Среднее
    var sum = 0;
    for (var j = 0; j < vals.length; j++) sum += vals[j];
    var mean = sum / vals.length;
    setText('flow-ref-mean', mean.toFixed(3));

    // Медиана
    var sorted = vals.slice().sort(function (a, b) { return a - b; });
    var mid = Math.floor(sorted.length / 2);
    var median = sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
    setText('flow-ref-median', median.toFixed(3));

    // Q старт
    setText('flow-ref-q-start', vals[0].toFixed(3));

    // Q текущий
    setText('flow-ref-q-current', vals[vals.length - 1].toFixed(3));

    // Δ от базового
    var refBaseEl = document.getElementById('flow-ref-vs-baseline');
    if (refBaseEl) {
      if (baselineValue != null && baselineValue > 0) {
        var delta = mean - baselineValue;
        var fmt = fmtDelta(delta);
        if (fmt) {
          refBaseEl.textContent = fmt.text;
          refBaseEl.style.color = fmt.color;
        } else {
          refBaseEl.textContent = '\u2014';
          refBaseEl.style.color = '#999';
        }
      } else {
        refBaseEl.textContent = '\u2014';
        refBaseEl.style.color = '#999';
      }
    }
  }

  // ═══════════════════ Анализ участков — функции ═══════════════════

  function toggleSegmentMode() {
    segmentMode = !segmentMode;
    segmentClickStart = null;
    currentSegStart = null;
    currentSegEnd = null;

    var btn = document.getElementById('flow-segment-mode');
    var panel = document.getElementById('flow-segment-panel');
    if (!btn || !panel) return;

    if (segmentMode) {
      btn.style.background = '#f3e5f5';
      btn.style.borderColor = '#ce93d8';
      btn.style.color = '#7b1fa2';
      panel.style.display = '';
      // Отключаем drag-zoom
      if (flowChart) {
        flowChart.options.plugins.zoom.zoom.drag.enabled = false;
        flowChart.update('none');
      }
      canvas.style.cursor = 'crosshair';
      loadSavedSegments();
    } else {
      btn.style.background = '#f8f9fa';
      btn.style.borderColor = '#dee2e6';
      btn.style.color = '#666';
      panel.style.display = 'none';
      // Восстанавливаем drag-zoom
      if (flowChart) {
        flowChart.options.plugins.zoom.zoom.drag.enabled = true;
        var anns = flowChart.options.plugins.annotation.annotations;
        delete anns['seg_start_line'];
        delete anns['seg_preview_box'];
        delete anns['seg_trend_line'];
        flowChart.update('none');
      }
      canvas.style.cursor = '';
      // Скрываем preview
      var preview = document.getElementById('flow-segment-preview');
      if (preview) preview.style.display = 'none';
    }
  }

  function fetchSegmentStats(startISO, endISO) {
    var preview = document.getElementById('flow-segment-preview');
    var rangeEl = document.getElementById('seg-preview-range');
    if (rangeEl) {
      rangeEl.textContent = formatSegDate(startISO) + ' \u2014 ' + formatSegDate(endISO);
    }
    if (preview) preview.style.display = '';

    // Показываем загрузку
    setSegPreviewLoading(true);

    fetch('/api/flow-rate/segment-stats', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ well_id: parseInt(wellId), start: startISO, end: endISO }),
    })
      .then(function (r) { return r.json(); })
      .then(function (stats) {
        renderSegmentPreview(stats);
      })
      .catch(function (err) {
        console.error('[segment] fetchSegmentStats error:', err);
        setSegPreviewLoading(false);
      });
  }

  function formatSegDate(iso) {
    if (!iso) return '\u2014';
    var d = new Date(iso);
    var dd = String(d.getDate()).padStart(2, '0');
    var mm = String(d.getMonth() + 1).padStart(2, '0');
    var hh = String(d.getHours()).padStart(2, '0');
    var mi = String(d.getMinutes()).padStart(2, '0');
    return dd + '.' + mm + ' ' + hh + ':' + mi;
  }

  function setSegPreviewLoading(loading) {
    var ids = [
      'seg-p-mean-flow', 'seg-p-median-flow', 'seg-p-min-flow', 'seg-p-max-flow',
      'seg-p-mean-ptube', 'seg-p-min-ptube', 'seg-p-max-ptube',
      'seg-p-mean-pline', 'seg-p-min-pline', 'seg-p-max-pline',
      'seg-p-mean-dp', 'seg-p-min-dp', 'seg-p-max-dp',
      'seg-p-purge-count', 'seg-p-downtime-count', 'seg-p-downtime-hours', 'seg-p-loss',
      'seg-p-cumulative', 'seg-p-duration', 'seg-p-data-points',
    ];
    for (var i = 0; i < ids.length; i++) {
      var el = document.getElementById(ids[i]);
      if (el) el.textContent = loading ? '...' : '\u2014';
    }
  }

  function fmtV(v, digits) {
    if (v == null) return '\u2014';
    return Number(v).toFixed(digits != null ? digits : 2);
  }

  function renderSegmentPreview(stats) {
    setSegPreviewLoading(false);
    var s = function (id) { return document.getElementById(id); };
    // Дебит
    if (s('seg-p-mean-flow')) s('seg-p-mean-flow').textContent = fmtV(stats.mean_flow, 3);
    if (s('seg-p-median-flow')) s('seg-p-median-flow').textContent = fmtV(stats.median_flow, 3);
    if (s('seg-p-min-flow')) s('seg-p-min-flow').textContent = fmtV(stats.min_flow, 3);
    if (s('seg-p-max-flow')) s('seg-p-max-flow').textContent = fmtV(stats.max_flow, 3);
    if (s('seg-p-effective')) s('seg-p-effective').textContent = fmtV(stats.effective_daily, 3);
    if (s('seg-p-utilization')) s('seg-p-utilization').textContent = fmtV(stats.utilization_pct, 1);
    // P трубное
    if (s('seg-p-mean-ptube')) s('seg-p-mean-ptube').textContent = fmtV(stats.mean_p_tube);
    if (s('seg-p-min-ptube')) s('seg-p-min-ptube').textContent = fmtV(stats.min_p_tube);
    if (s('seg-p-max-ptube')) s('seg-p-max-ptube').textContent = fmtV(stats.max_p_tube);
    // P линейное
    if (s('seg-p-mean-pline')) s('seg-p-mean-pline').textContent = fmtV(stats.mean_p_line);
    if (s('seg-p-min-pline')) s('seg-p-min-pline').textContent = fmtV(stats.min_p_line);
    if (s('seg-p-max-pline')) s('seg-p-max-pline').textContent = fmtV(stats.max_p_line);
    // ΔP
    if (s('seg-p-mean-dp')) s('seg-p-mean-dp').textContent = fmtV(stats.mean_dp);
    if (s('seg-p-min-dp')) s('seg-p-min-dp').textContent = fmtV(stats.min_dp);
    if (s('seg-p-max-dp')) s('seg-p-max-dp').textContent = fmtV(stats.max_dp);
    // Продувки / простои
    if (s('seg-p-purge-count')) s('seg-p-purge-count').textContent = stats.purge_count != null ? stats.purge_count : '\u2014';
    if (s('seg-p-downtime-count')) s('seg-p-downtime-count').textContent = stats.downtime_count != null ? stats.downtime_count : '\u2014';
    if (s('seg-p-downtime-hours')) s('seg-p-downtime-hours').textContent = fmtV(stats.downtime_hours, 1);
    if (s('seg-p-loss')) s('seg-p-loss').textContent = stats.loss_vs_median != null ? fmtV(stats.loss_vs_median, 3) : '\u2014';
    // Итого
    if (s('seg-p-cumulative')) s('seg-p-cumulative').textContent = fmtV(stats.cumulative_flow, 3);
    if (s('seg-p-duration')) s('seg-p-duration').textContent = fmtV(stats.duration_hours, 1);
    if (s('seg-p-data-points')) s('seg-p-data-points').textContent = stats.data_points != null ? stats.data_points : '\u2014';
    // Тренд
    renderTrendBlock(stats, s);
  }

  /* ---------- Тренд-блок: значения + прогноз + тренд-линия ---------- */
  function renderTrendValue(trend, digits) {
    if (!trend) return { text: '\u2014', color: '#666' };
    var arrow = trend.direction === 'up' ? '\u2191' : trend.direction === 'down' ? '\u2193' : '\u2192';
    var color = trend.direction === 'up' ? '#2e7d32' : trend.direction === 'down' ? '#c62828' : '#666';
    return { text: arrow + ' ' + fmtV(Math.abs(trend.slope_per_day), digits), color: color };
  }

  var lastTrendStats = null; // кэш тренд-данных для клиентского прогноза

  function renderTrendBlock(stats, s) {
    lastTrendStats = stats; // сохраняем для прогноза по порогам
    // Q тренд
    var tf = renderTrendValue(stats.trend_flow, 3);
    if (s('seg-p-trend-flow')) { s('seg-p-trend-flow').textContent = tf.text; s('seg-p-trend-flow').style.color = tf.color; }
    if (s('seg-p-trend-r2-flow')) s('seg-p-trend-r2-flow').textContent = stats.trend_flow ? fmtV(stats.trend_flow.r_squared, 3) : '\u2014';
    // ΔP тренд
    var td = renderTrendValue(stats.trend_dp, 3);
    if (s('seg-p-trend-dp')) { s('seg-p-trend-dp').textContent = td.text; s('seg-p-trend-dp').style.color = td.color; }
    if (s('seg-p-trend-r2-dp')) s('seg-p-trend-r2-dp').textContent = stats.trend_dp ? fmtV(stats.trend_dp.r_squared, 3) : '\u2014';
    // P трубное тренд
    var tp = renderTrendValue(stats.trend_p_tube, 3);
    if (s('seg-p-trend-ptube')) { s('seg-p-trend-ptube').textContent = tp.text; s('seg-p-trend-ptube').style.color = tp.color; }
    if (s('seg-p-trend-r2-ptube')) s('seg-p-trend-r2-ptube').textContent = stats.trend_p_tube ? fmtV(stats.trend_p_tube.r_squared, 3) : '\u2014';
    // Прогноз Q → 0
    if (s('seg-p-predict-zero-flow')) {
      var h0 = stats.trend_flow ? stats.trend_flow.hours_to_zero : null;
      s('seg-p-predict-zero-flow').textContent = h0 != null ? fmtV(h0 / 24, 1) + ' сут' : '\u2014';
    }
    // Прогнозы по порогам — обновляем из текущих полей ввода
    updateThresholdPredictions();
    // Тренд-линия на графике
    drawTrendLine(stats);
  }

  /* Клиентский расчёт прогноза по порогу для одного тренда.
     trend = { slope_per_day, intercept }, durH = длительность участка (часы)
     threshold = число. Возвращает часы от конца участка или null. */
  function predictHoursToThreshold(trend, durH, threshold) {
    if (!trend || threshold == null || isNaN(threshold)) return null;
    var slopeH = trend.slope_per_day / 24;
    if (Math.abs(slopeH) < 1e-9) return null; // flat — никогда не достигнет
    var tThresh = (threshold - trend.intercept) / slopeH; // часов от начала
    var remaining = tThresh - durH;
    if (remaining <= 0) return null; // уже достигнут или в прошлом
    // Проверяем направление: тренд должен идти К порогу
    var lastVal = trend.intercept + slopeH * durH;
    if (trend.slope_per_day > 0 && threshold < lastVal) return null; // растёт, порог ниже
    if (trend.slope_per_day < 0 && threshold > lastVal) return null; // падает, порог выше
    return remaining;
  }

  /* Обновить прогнозы по порогам из полей ввода (без запроса к серверу) */
  function updateThresholdPredictions() {
    if (!lastTrendStats) return;
    var durH = lastTrendStats.duration_hours || 0;
    var s = function (id) { return document.getElementById(id); };

    // Q → порог
    var thF = parseFloat((s('seg-threshold-flow') || {}).value);
    var hf = predictHoursToThreshold(lastTrendStats.trend_flow, durH, thF);
    if (s('seg-p-predict-thresh-flow'))
      s('seg-p-predict-thresh-flow').textContent = hf != null ? fmtV(hf / 24, 1) + ' сут' : '\u2014';

    // ΔP → порог
    var thD = parseFloat((s('seg-threshold-dp') || {}).value);
    var hd = predictHoursToThreshold(lastTrendStats.trend_dp, durH, thD);
    if (s('seg-p-predict-thresh-dp'))
      s('seg-p-predict-thresh-dp').textContent = hd != null ? fmtV(hd / 24, 1) + ' сут' : '\u2014';

    // P тр. → порог
    var thP = parseFloat((s('seg-threshold-ptube') || {}).value);
    var hp = predictHoursToThreshold(lastTrendStats.trend_p_tube, durH, thP);
    if (s('seg-p-predict-thresh-ptube'))
      s('seg-p-predict-thresh-ptube').textContent = hp != null ? fmtV(hp / 24, 1) + ' сут' : '\u2014';
  }

  /* ---------- Тренд-линия пунктиром на графике ---------- */
  function drawTrendLine(stats) {
    if (!flowChart || !flowChart.options.plugins.annotation) return;
    var anns = flowChart.options.plugins.annotation.annotations;
    // Удаляем старую
    delete anns['seg_trend_line'];
    if (!stats.trend_flow || !currentSegStart || !currentSegEnd) {
      flowChart.update('none');
      return;
    }
    var t = stats.trend_flow;
    var startDate = new Date(currentSegStart);
    var endDate = new Date(currentSegEnd);
    var durH = (endDate - startDate) / 3600000;
    var y1 = t.intercept;
    // Экстраполяция на 50% вперёд
    var extendH = durH * 0.5;
    var extDate = new Date(endDate.getTime() + extendH * 3600000);
    var y_ext = t.intercept + (t.slope_per_day / 24) * (durH + extendH);

    anns['seg_trend_line'] = {
      type: 'line',
      xMin: currentSegStart,
      xMax: extDate.toISOString(),
      yMin: y1,
      yMax: Math.max(y_ext, 0),
      yScaleID: 'y',
      borderColor: 'rgba(13, 71, 161, 0.6)',
      borderWidth: 2,
      borderDash: [8, 4],
      label: {
        display: true,
        content: (t.direction === 'down' ? '\u2193' : t.direction === 'up' ? '\u2191' : '\u2192') +
                 ' ' + Math.abs(t.slope_per_day).toFixed(2) + '/\u0441\u0443\u0442',
        position: 'end',
        font: { size: 10 },
        backgroundColor: 'rgba(13, 71, 161, 0.8)',
        color: '#fff',
        padding: 3,
      },
    };
    flowChart.update('none');
  }

  function saveSegment() {
    if (!currentSegStart || !currentSegEnd) return;
    var nameInput = document.getElementById('seg-name-input');
    var name = (nameInput && nameInput.value.trim()) || '\u0423\u0447\u0430\u0441\u0442\u043e\u043a';
    var msgEl = document.getElementById('seg-save-msg');

    fetch('/api/flow-rate/segments', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        well_id: parseInt(wellId),
        name: name,
        start: currentSegStart,
        end: currentSegEnd,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.id) {
          if (msgEl) {
            msgEl.style.display = '';
            clearTimeout(msgEl._timer);
            msgEl._timer = setTimeout(function () { msgEl.style.display = 'none'; }, 2000);
          }
          if (nameInput) nameInput.value = '';
          // Убираем preview box + тренд-линию
          if (flowChart) {
            delete flowChart.options.plugins.annotation.annotations['seg_preview_box'];
            delete flowChart.options.plugins.annotation.annotations['seg_trend_line'];
            flowChart.update('none');
          }
          var preview = document.getElementById('flow-segment-preview');
          if (preview) preview.style.display = 'none';
          currentSegStart = null;
          currentSegEnd = null;
          loadSavedSegments();
        }
      })
      .catch(function (err) { console.error('[segment] save error:', err); });
  }

  function loadSavedSegments() {
    fetch('/api/flow-rate/segments?well_id=' + wellId)
      .then(function (r) { return r.json(); })
      .then(function (segments) {
        savedSegments = segments;
        renderSegmentsTable(segments);
      })
      .catch(function (err) { console.error('[segment] load error:', err); });
  }

  function renderSegmentsTable(segments) {
    var wrap = document.getElementById('flow-segments-table-wrap');
    var tbody = document.getElementById('flow-segments-tbody');
    var countEl = document.getElementById('seg-table-count');
    if (!wrap || !tbody) return;

    if (!segments || segments.length === 0) {
      wrap.style.display = 'none';
      return;
    }
    wrap.style.display = '';
    if (countEl) countEl.textContent = segments.length;

    tbody.innerHTML = '';
    for (var i = 0; i < segments.length; i++) {
      var seg = segments[i];
      var st = seg.stats || {};
      var tr = document.createElement('tr');
      tr.style.cssText = 'border-bottom:1px solid #f0f0f0;';
      tr.dataset.segId = seg.id;

      tr.innerHTML =
        '<td style="padding:5px 6px; text-align:center;"><input type="checkbox" class="seg-check" data-seg-idx="' + i + '"></td>' +
        '<td style="padding:5px 6px; font-weight:600;">' + escHtml(seg.name) + '</td>' +
        '<td style="padding:5px 6px; font-size:11px; color:#666;">' + formatSegDate(seg.dt_start) + ' \u2014 ' + formatSegDate(seg.dt_end) + '</td>' +
        '<td style="padding:5px 6px; text-align:right; font-family:monospace;">' + fmtV(st.mean_flow, 3) + '</td>' +
        '<td style="padding:5px 6px; text-align:right; font-family:monospace;">' + fmtV(st.median_flow, 3) + '</td>' +
        '<td style="padding:5px 6px; text-align:right; font-family:monospace;">' + fmtV(st.cumulative_flow, 1) + '</td>' +
        '<td style="padding:5px 6px; text-align:center;">' + (st.purge_count != null ? st.purge_count : '\u2014') + '</td>' +
        '<td style="padding:5px 6px; text-align:center;">' + fmtV(st.downtime_hours, 1) + '</td>' +
        '<td style="padding:5px 6px; text-align:center;">' +
          '<button class="seg-delete-btn" data-seg-id="' + seg.id + '" title="\u0423\u0434\u0430\u043b\u0438\u0442\u044c" style="border:none; background:none; color:#c62828; cursor:pointer; font-size:14px;">&#128465;</button>' +
        '</td>';

      tbody.appendChild(tr);
    }

    // Event delegation для удаления и чекбоксов
    tbody.onclick = function (e) {
      var delBtn = e.target.closest('.seg-delete-btn');
      if (delBtn) {
        e.preventDefault();
        deleteSegment(parseInt(delBtn.dataset.segId));
        return;
      }
      updateCompareButton();
    };

    // Check-all
    var checkAll = document.getElementById('seg-check-all');
    if (checkAll) {
      checkAll.checked = false;
      checkAll.onchange = function () {
        var boxes = tbody.querySelectorAll('.seg-check');
        for (var j = 0; j < boxes.length; j++) boxes[j].checked = checkAll.checked;
        updateCompareButton();
      };
    }
  }

  function escHtml(str) {
    var div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
  }

  function updateCompareButton() {
    var btn = document.getElementById('seg-compare-btn');
    if (!btn) return;
    var checked = document.querySelectorAll('#flow-segments-tbody .seg-check:checked');
    btn.disabled = checked.length < 2;
    btn.style.opacity = checked.length < 2 ? '0.5' : '1';
  }

  function deleteSegment(segId) {
    fetch('/api/flow-rate/segments/' + segId, { method: 'DELETE' })
      .then(function (r) { return r.json(); })
      .then(function () { loadSavedSegments(); })
      .catch(function (err) { console.error('[segment] delete error:', err); });
  }

  function compareSegments() {
    var checked = document.querySelectorAll('#flow-segments-tbody .seg-check:checked');
    if (checked.length < 2) return;

    var selected = [];
    for (var i = 0; i < checked.length; i++) {
      var idx = parseInt(checked[i].dataset.segIdx);
      if (savedSegments[idx]) selected.push(savedSegments[idx]);
    }

    var wrap = document.getElementById('flow-segment-compare');
    var table = document.getElementById('seg-compare-table');
    if (!wrap || !table) return;

    // Метрики для сравнения
    var metrics = [
      { key: 'mean_flow',    label: 'Q среднее (тыс.м³/сут)', digits: 3, hint: 'Среднее арифметическое дебита, включая нули при простоях и продувках' },
      { key: 'median_flow',  label: 'Q медиана', digits: 3, hint: 'Типичный рабочий дебит. Нечувствителен к нулям — завышает при простоях' },
      { key: 'effective_daily', label: 'Q эффект. (тыс.м³/сут)', digits: 3, hint: 'Реальная среднесуточная добыча = накопленный / дни. Учитывает все простои и продувки' },
      { key: 'min_flow',     label: 'Q мин', digits: 3, hint: 'Минимальный мгновенный дебит за участок' },
      { key: 'max_flow',     label: 'Q макс', digits: 3, hint: 'Максимальный мгновенный дебит за участок' },
      { key: 'utilization_pct', label: 'КИВ (%)', digits: 1, hint: 'Коэффициент использования времени — % времени реальной работы скважины' },
      { key: 'cumulative_flow', label: 'Накоплено (тыс.м³)', digits: 1, hint: 'Интеграл дебита (площадь под кривой). Только газ в трубопровод' },
      { key: 'mean_p_tube',  label: 'P труб. ср (кгс/см²)', digits: 2, hint: 'Среднее давление на устье скважины' },
      { key: 'mean_p_line',  label: 'P лин. ср', digits: 2, hint: 'Среднее давление в шлейфе (линейное)' },
      { key: 'mean_dp',      label: '\u0394P ср', digits: 2, hint: 'Средний перепад давления (P трубное − P линейное). Движущая сила потока' },
      { key: 'purge_count',  label: 'Продувок', digits: 0, hint: 'Количество обнаруженных циклов продувок (стравливание газа в атмосферу)' },
      { key: 'downtime_hours', label: 'Простои (ч)', digits: 1, hint: 'Суммарное время простоев (P трубное ≤ P шлейфа, дебит = 0)' },
      { key: 'duration_hours', label: 'Период (ч)', digits: 1, hint: 'Длительность выбранного участка' },
      { key: 'data_points',  label: 'Точек', digits: 0, hint: 'Количество минутных измерений в участке' },
      { key: 'trend_flow_slope', label: 'Тренд Q (/сут)', digits: 3,
        hint: 'Скорость изменения дебита: отрицательный = падение, положительный = рост (тыс.м³/сут²)',
        getter: function(st) { return st.trend_flow ? st.trend_flow.slope_per_day : null; } },
      { key: 'trend_dp_slope', label: 'Тренд ΔP (/сут)', digits: 3,
        hint: 'Скорость изменения перепада давления (кгс/см²/сут)',
        getter: function(st) { return st.trend_dp ? st.trend_dp.slope_per_day : null; } },
      { key: 'trend_ptube_slope', label: 'Тренд P тр. (/сут)', digits: 3,
        hint: 'Скорость изменения трубного давления (кгс/см²/сут)',
        getter: function(st) { return st.trend_p_tube ? st.trend_p_tube.slope_per_day : null; } },
      { key: 'trend_r2', label: 'R² (Q)', digits: 3,
        hint: 'Качество линейного тренда дебита. 1.0 = идеальная линия, <0.5 = слабый тренд',
        getter: function(st) { return st.trend_flow ? st.trend_flow.r_squared : null; } },
    ];

    // Заголовок
    var html = '<thead><tr style="background:#f8f9fa;"><th style="padding:5px 8px; text-align:left; font-size:11px; color:#666;">Метрика</th>';
    for (var j = 0; j < selected.length; j++) {
      html += '<th style="padding:5px 8px; text-align:right; font-size:11px; color:#7b1fa2;">' + escHtml(selected[j].name) + '</th>';
    }
    if (selected.length === 2) {
      html += '<th style="padding:5px 8px; text-align:right; font-size:11px; color:#333;">\u0394</th>';
    }
    html += '</tr></thead><tbody>';

    // Строки
    for (var m = 0; m < metrics.length; m++) {
      var met = metrics[m];
      var titleAttr = met.hint ? ' title="' + met.hint + '"' : '';
      html += '<tr style="border-bottom:1px solid #f0f0f0;"><td style="padding:4px 8px; font-weight:600; font-size:12px; cursor:help;"' + titleAttr + '>' + met.label + '</td>';
      var vals = [];
      for (var s = 0; s < selected.length; s++) {
        var st = selected[s].stats || {};
        var v = met.getter ? met.getter(st) : st[met.key];
        vals.push(v);
        html += '<td style="padding:4px 8px; text-align:right; font-family:monospace; font-size:12px;">' + fmtV(v, met.digits) + '</td>';
      }
      // Дельта для 2 участков
      if (selected.length === 2 && vals[0] != null && vals[1] != null) {
        var diff = vals[1] - vals[0];
        var diffColor = diff > 0 ? '#2e7d32' : diff < 0 ? '#c62828' : '#666';
        var sign = diff > 0 ? '+' : '';
        html += '<td style="padding:4px 8px; text-align:right; font-family:monospace; font-size:12px; font-weight:700; color:' + diffColor + ';">' + sign + fmtV(diff, met.digits) + '</td>';
      } else if (selected.length === 2) {
        html += '<td style="padding:4px 8px; text-align:right; color:#999;">\u2014</td>';
      }
      html += '</tr>';
    }
    html += '</tbody>';
    table.innerHTML = html;
    wrap.style.display = '';
  }

  // ═══════════════════ Сброс зума ═══════════════════
  var resetBtn = document.getElementById('flow-reset-zoom');
  if (resetBtn) {
    resetBtn.addEventListener('click', function () {
      if (flowChart) flowChart.resetZoom();
    });
  }

  // ═══════════════════ Базовый дебит — input + save ═══════════════════
  var baselineInput = document.getElementById('flow-baseline-input');
  var baselineSaveBtn = document.getElementById('flow-baseline-save');
  var baselineSavedMsg = document.getElementById('flow-baseline-saved-msg');
  console.log('[flow_rate_chart] baseline DOM:', {
    input: !!baselineInput, btn: !!baselineSaveBtn, msg: !!baselineSavedMsg,
    storedValue: baselineValue
  });

  function applyBaseline() {
    console.log('[flow_rate_chart] applyBaseline called, input value:', baselineInput ? baselineInput.value : 'no-input');
    if (!baselineInput) return;
    var val = baselineInput.value.trim();
    baselineValue = val ? parseFloat(val) : null;
    if (baselineValue != null && !isNaN(baselineValue)) {
      localStorage.setItem(baselineStorageKey, String(baselineValue));
    } else {
      baselineValue = null;
      localStorage.removeItem(baselineStorageKey);
    }
    console.log('[flow_rate_chart] baselineValue set to:', baselineValue, 'flowChart:', !!flowChart);
    updateBaselineOnChart();
    updateRefPointStats(); // пересчёт «Δ от базового» в refpoint
    // «Сохранено» — 2 секунды
    if (baselineSavedMsg) {
      baselineSavedMsg.style.display = '';
      clearTimeout(baselineSavedMsg._timer);
      baselineSavedMsg._timer = setTimeout(function () {
        baselineSavedMsg.style.display = 'none';
      }, 2000);
    }
  }

  if (baselineInput) {
    if (baselineValue != null && !isNaN(baselineValue)) baselineInput.value = baselineValue;
    baselineInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); applyBaseline(); }
    });
  }
  if (baselineSaveBtn) {
    baselineSaveBtn.addEventListener('click', function (e) {
      e.preventDefault();
      applyBaseline();
    });
  }

  // ═══════════════════ Точка отсчёта — input ═══════════════════
  var refPointInput = document.getElementById('flow-refpoint-input');
  var refPointSetBtn = document.getElementById('flow-refpoint-set');
  var refPointClearBtn = document.getElementById('flow-refpoint-clear');

  if (refPointInput && refPointTime) {
    refPointInput.value = isoToDatetimeLocal(refPointTime);
  }

  if (refPointSetBtn) {
    refPointSetBtn.addEventListener('click', function (e) {
      e.preventDefault();
      if (!refPointInput || !refPointInput.value) return;
      setRefPoint(new Date(refPointInput.value).toISOString());
    });
  }

  if (refPointClearBtn) {
    refPointClearBtn.addEventListener('click', function (e) {
      e.preventDefault();
      clearRefPoint();
    });
  }

  // ═══════════════════ Анализ участков — listeners ═══════════════════
  var segModeBtn = document.getElementById('flow-segment-mode');
  if (segModeBtn) {
    segModeBtn.addEventListener('click', function (e) {
      e.preventDefault();
      toggleSegmentMode();
    });
  }

  var segSaveBtn = document.getElementById('seg-save-btn');
  if (segSaveBtn) {
    segSaveBtn.addEventListener('click', function (e) {
      e.preventDefault();
      saveSegment();
    });
  }

  // Пороги: авто-обновление прогнозов при вводе (каждый независимо)
  ['seg-threshold-flow', 'seg-threshold-dp', 'seg-threshold-ptube'].forEach(function (id) {
    var inp = document.getElementById(id);
    if (inp) inp.addEventListener('input', updateThresholdPredictions);
  });
  var segPredictBtn = document.getElementById('seg-predict-btn');
  if (segPredictBtn) {
    segPredictBtn.addEventListener('click', function (e) {
      e.preventDefault();
      updateThresholdPredictions();
    });
  }

  var segCompareBtn = document.getElementById('seg-compare-btn');
  if (segCompareBtn) {
    segCompareBtn.addEventListener('click', function (e) {
      e.preventDefault();
      compareSegments();
    });
  }

  var segPreviewClose = document.getElementById('seg-preview-close');
  if (segPreviewClose) {
    segPreviewClose.addEventListener('click', function (e) {
      e.preventDefault();
      var preview = document.getElementById('flow-segment-preview');
      if (preview) preview.style.display = 'none';
      if (flowChart) {
        delete flowChart.options.plugins.annotation.annotations['seg_preview_box'];
        delete flowChart.options.plugins.annotation.annotations['seg_trend_line'];
        flowChart.update('none');
      }
      currentSegStart = null;
      currentSegEnd = null;
    });
  }

  var segCompareClose = document.getElementById('seg-compare-close');
  if (segCompareClose) {
    segCompareClose.addEventListener('click', function (e) {
      e.preventDefault();
      var wrap = document.getElementById('flow-segment-compare');
      if (wrap) wrap.style.display = 'none';
    });
  }

  // Segment name input — Enter to save
  var segNameInput = document.getElementById('seg-name-input');
  if (segNameInput) {
    segNameInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); saveSegment(); }
    });
  }

  // ═══════════════════ Первичная загрузка ═══════════════════
  loadChart(7);

  // После создания: отключаем pan если режим не активен
  if (!window._syncPanMode && flowChart) {
    flowChart.options.plugins.zoom.pan.enabled = false;
    flowChart.update('none');
  }

  // ═══════════════════ Экспорт для координатора ═══════════════════
  window.flowRateChartReload = function (days) {
    console.log('[flow_rate_chart] flowRateChartReload called with days:', days, 'currentDays was:', currentDays);
    loadChart(days);
  };

  window.flowRateChart = {
    syncZoom: syncZoom,
    resetZoom: resetSyncZoom,
    get instance() { return flowChart; },
  };

})();
