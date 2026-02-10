/**
 * pressure_events_chart.js — График давлений + события (вертикальные линии)
 *
 * Показывает данные LoRa давлений с наложением событий:
 * - 💉 Реагенты (красные линии)
 * - 🌀 Продувки (оранжевые линии)
 * - 📊 Замеры давления (синие линии)
 */

(function () {
  'use strict';

  const canvasId = 'chart_pressure_events';
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const wellId = canvas.dataset.wellId;
  if (!wellId) return;

  let chart = null;
  let pressureData = [];
  let eventsData = [];

  // Цвета событий
  const EVENT_COLORS = {
    reagent: '#e53935',
    purge: '#ffa000',
    pressure: '#3949ab',
    equip: '#43a047',
    note: '#9e9e9e',
    other: '#607d8b',
  };

  // Цвета давлений
  const COLORS = {
    tube: '#e53935',
    tubeFill: 'rgba(229, 57, 53, 0.08)',
    line: '#1e88e5',
    lineFill: 'rgba(30, 136, 229, 0.08)',
  };

  /**
   * Загрузка данных давлений
   */
  async function loadPressureData(days, interval) {
    const filterParams = window.pressureFilterParams ? window.pressureFilterParams() : '';
    const resp = await fetch(`/api/pressure/chart/${wellId}?days=${days}&interval=${interval}${filterParams}`);
    if (!resp.ok) return [];
    const json = await resp.json();
    return json.points || [];
  }

  /**
   * Загрузка событий для скважины
   */
  async function loadEventsData(days) {
    // Используем данные из wellEventsData если доступны
    if (window.wellEventsData) {
      const data = window.wellEventsFiltered || window.wellEventsData;
      const events = [];

      // Конвертируем injections в события
      (data.timelineInjections || []).forEach(inj => {
        events.push({
          type: 'reagent',
          time: inj.t,
          label: inj.reagent,
          qty: inj.qty,
          description: inj.description,
        });
      });

      // Конвертируем events
      (data.timelineEvents || []).forEach(ev => {
        events.push({
          type: ev.type || 'other',
          time: ev.t,
          label: ev.description,
          p_tube: ev.p_tube,
          p_line: ev.p_line,
        });
      });

      return events;
    }

    return [];
  }

  /**
   * Получение включённых типов событий
   */
  function getEnabledEventTypes() {
    const types = new Set();
    if (document.getElementById('show-reagent-events')?.checked) types.add('reagent');
    if (document.getElementById('show-purge-events')?.checked) types.add('purge');
    if (document.getElementById('show-pressure-events')?.checked) types.add('pressure');
    return types;
  }

  /**
   * Создание аннотаций для событий
   */
  function createEventAnnotations(events, enabledTypes) {
    const annotations = {};

    events.forEach((ev, i) => {
      if (!enabledTypes.has(ev.type)) return;

      const color = EVENT_COLORS[ev.type] || EVENT_COLORS.other;
      const time = new Date(ev.time).getTime();

      annotations[`event_${i}`] = {
        type: 'line',
        xMin: time,
        xMax: time,
        borderColor: color,
        borderWidth: 2,
        borderDash: [5, 3],
        label: {
          display: false,
        },
      };
    });

    return annotations;
  }

  /**
   * Отрисовка графика
   */
  async function renderChart(days = 7, interval = 5) {
    // Загружаем данные параллельно
    const [points, events] = await Promise.all([
      loadPressureData(days, interval),
      loadEventsData(days),
    ]);

    pressureData = points;
    eventsData = events;

    if (!points || points.length === 0) {
      canvas.parentElement.innerHTML =
        '<div style="padding:40px;text-align:center;color:#999;">Нет данных</div>';
      return;
    }

    // Данные для datasets
    const tubeAvg = [], lineAvg = [];

    for (const p of points) {
      if (p.p_tube_avg !== null) {
        tubeAvg.push({ x: p.t, y: p.p_tube_avg });
      }
      if (p.p_line_avg !== null) {
        lineAvg.push({ x: p.t, y: p.p_line_avg });
      }
    }

    const datasets = [];

    if (tubeAvg.length > 0) {
      datasets.push({
        label: 'Ptr (устье)',
        data: tubeAvg,
        borderColor: COLORS.tube,
        backgroundColor: COLORS.tubeFill,
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.3,
        fill: true,
      });
    }

    if (lineAvg.length > 0) {
      datasets.push({
        label: 'Pshl (шлейф)',
        data: lineAvg,
        borderColor: COLORS.line,
        backgroundColor: COLORS.lineFill,
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.3,
        fill: true,
      });
    }

    // Получаем включённые типы событий
    const enabledTypes = getEnabledEventTypes();
    const annotations = createEventAnnotations(events, enabledTypes);

    if (chart) {
      chart.destroy();
    }

    const ctx = canvas.getContext('2d');
    chart = new Chart(ctx, {
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
            display: false,
          },
          tooltip: {
            enabled: false,
            external: function(context) {
              updateExternalTooltip(context, events, enabledTypes);
            },
          },
          annotation: {
            annotations: annotations,
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
              displayFormats: {
                minute: 'HH:mm',
                hour: 'dd.MM HH:mm',
                day: 'dd.MM',
              },
            },
            grid: { color: '#e9ecef' },
            ticks: { font: { size: 10 }, maxRotation: 0 },
          },
          y: {
            grid: { color: '#e9ecef' },
            ticks: { font: { size: 10 } },
          },
        },
      },
    });
  }

  /**
   * Обновление внешнего tooltip
   */
  function updateExternalTooltip(context, events, enabledTypes) {
    const tooltipModel = context.tooltip;
    const timeEl = document.getElementById('tooltip-time');
    const ptrEl = document.getElementById('tooltip-ptr');
    const pshlEl = document.getElementById('tooltip-pshl');
    const deltaEl = document.getElementById('tooltip-delta');
    const eventEl = document.getElementById('tooltip-event');

    if (!timeEl) return;

    if (tooltipModel.opacity === 0 || !tooltipModel.dataPoints?.length) {
      return;
    }

    // Время
    const xValue = tooltipModel.dataPoints[0].parsed.x;
    const d = new Date(xValue);
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const hh = String(d.getHours()).padStart(2, '0');
    const mi = String(d.getMinutes()).padStart(2, '0');
    timeEl.textContent = `${dd}.${mm} ${hh}:${mi}`;

    // Значения давлений
    let ptr = null, pshl = null;
    for (const dp of tooltipModel.dataPoints) {
      const label = dp.dataset.label || '';
      if (label.includes('Ptr')) ptr = dp.parsed.y;
      if (label.includes('Pshl')) pshl = dp.parsed.y;
    }

    ptrEl.textContent = ptr !== null ? ptr.toFixed(2) : '—';
    pshlEl.textContent = pshl !== null ? pshl.toFixed(2) : '—';
    deltaEl.textContent = (ptr !== null && pshl !== null) ? (ptr - pshl).toFixed(2) : '—';

    // Найти ближайшее событие (в пределах 30 минут)
    const threshold = 30 * 60 * 1000; // 30 минут
    let nearestEvent = null;
    let minDist = Infinity;

    for (const ev of events) {
      if (!enabledTypes.has(ev.type)) continue;
      const evTime = new Date(ev.time).getTime();
      const dist = Math.abs(evTime - xValue);
      if (dist < minDist && dist < threshold) {
        minDist = dist;
        nearestEvent = ev;
      }
    }

    if (eventEl) {
      if (nearestEvent) {
        const icon = nearestEvent.type === 'reagent' ? '💉' :
                     nearestEvent.type === 'purge' ? '🌀' :
                     nearestEvent.type === 'pressure' ? '📊' : '📌';
        eventEl.textContent = `${icon} ${nearestEvent.label || nearestEvent.type}`;
        if (nearestEvent.qty) eventEl.textContent += ` (${nearestEvent.qty})`;
      } else {
        eventEl.textContent = '';
      }
    }
  }

  /**
   * Перезагрузка графика
   */
  function reload(days, interval) {
    const currentDays = days || window._currentPressureDays || 7;
    const currentInterval = interval || window._currentPressureInterval || 5;
    renderChart(currentDays, currentInterval);
  }

  // Обработчики чекбоксов событий
  ['show-reagent-events', 'show-purge-events', 'show-pressure-events'].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('change', () => reload());
    }
  });

  // Слушаем обновления от основного графика
  const originalReload = window.pressureChartReload;
  window.pressureChartReload = function(days, interval) {
    window._currentPressureDays = days;
    window._currentPressureInterval = interval;
    if (originalReload) originalReload(days, interval);
    reload(days, interval);
  };

  // Начальная загрузка
  setTimeout(() => {
    renderChart(7, 5);
  }, 100);

  // Экспорт
  window.pressureEventsChartReload = reload;

})();
