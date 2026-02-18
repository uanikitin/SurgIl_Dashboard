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

  // ═══════════════════ Сброс зума ═══════════════════
  var resetBtn = document.getElementById('flow-reset-zoom');
  if (resetBtn) {
    resetBtn.addEventListener('click', function () {
      if (flowChart) flowChart.resetZoom();
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
