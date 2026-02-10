// backend/static/js/well_events_chart.js
// График событий для страницы скважины - ФИНАЛЬНАЯ ВЕРСИЯ

// ==========================================
// ГЛОБАЛЬНЫЕ НАСТРОЙКИ CHART.JS
// ==========================================
Chart.defaults.animation = false;
Chart.defaults.responsive = true;
Chart.defaults.maintainAspectRatio = false;

// ==========================================
// ПЕРЕМЕННЫЕ
// ==========================================
let timelineChartWell = null;

// Словарь перевода типов событий
const EVENT_TYPE_LABELS = {
  'pressure': 'Замер давления',
  'purge': 'Продувка',
  'reagent': 'Вброс реагента',
  'equip': 'Установка оборудования',
  'note': 'Заметка',
  'other': 'Другое'
};

// ==========================================
// ФУНКЦИЯ ДЛЯ ГЕНЕРАЦИИ СТАБИЛЬНЫХ ЦВЕТОВ
// ==========================================
function _stableColor(key) {
  const s = (key || "x");
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (h * 33 + s.charCodeAt(i)) % 360;
  }
  return `hsl(${h}, 70%, 45%)`;
}

// ==========================================
// НАСТРОЙКИ ЦВЕТОВ - МОДАЛЬНОЕ ОКНО
// ==========================================
function openColorSettingsWell() {
  const modal = document.getElementById('colorModalWell');
  if (!modal) {
    console.error('Modal colorModalWell not found!');
    return;
  }

  const container = document.getElementById('colorInputsWell');
  if (!container) {
    console.error('Container colorInputsWell not found!');
    return;
  }

  container.innerHTML = '';

  // Раздел для реагентов
  const reagentsSection = document.createElement('div');
  reagentsSection.style.marginBottom = '20px';
  reagentsSection.innerHTML = '<h4 style="margin: 10px 0; color: #495057; font-size: 14px;">💉 Реагенты</h4>';

  Object.keys(window.wellEventsData.reagentColors).forEach(name => {
    const div = document.createElement('div');
    div.style.marginBottom = '10px';
    div.innerHTML = `
      <label style="display: flex; align-items: center; gap: 10px;">
        <span style="min-width: 120px; font-weight: 500;">${name}:</span>
        <input type="color" value="${window.wellEventsData.reagentColors[name]}" 
               data-reagent="${name}" class="reagent-color-input-well"
               style="width: 60px; height: 30px; cursor: pointer;">
      </label>
    `;
    reagentsSection.appendChild(div);
  });
  container.appendChild(reagentsSection);

  // Раздел для событий
  const eventsSection = document.createElement('div');
  eventsSection.innerHTML = '<h4 style="margin: 10px 0; color: #495057; font-size: 14px;">📌 События</h4>';

  Object.keys(window.wellEventsData.eventColors).forEach(name => {
    const label = EVENT_TYPE_LABELS[name] || name;
    const div = document.createElement('div');
    div.style.marginBottom = '10px';
    div.innerHTML = `
      <label style="display: flex; align-items: center; gap: 10px;">
        <span style="min-width: 120px; font-weight: 500;">${label}:</span>
        <input type="color" value="${window.wellEventsData.eventColors[name]}" 
               data-event="${name}" class="event-color-input-well"
               style="width: 60px; height: 30px; cursor: pointer;">
      </label>
    `;
    eventsSection.appendChild(div);
  });
  container.appendChild(eventsSection);

  modal.style.display = 'block';
}

function closeColorSettingsWell() {
  const modal = document.getElementById('colorModalWell');
  if (modal) {
    modal.style.display = 'none';
  }
}

function saveColorsWell() {
  const reagentInputs = document.querySelectorAll('.reagent-color-input-well');
  const eventInputs = document.querySelectorAll('.event-color-input-well');

  const newReagentColors = {};
  const newEventColors = {};

  reagentInputs.forEach(input => {
    const reagent = input.dataset.reagent;
    newReagentColors[reagent] = input.value;
  });

  eventInputs.forEach(input => {
    const event = input.dataset.event;
    newEventColors[event] = input.value;
  });

  // Сохраняем в localStorage
  localStorage.setItem('reagentColors', JSON.stringify(newReagentColors));
  localStorage.setItem('eventColors', JSON.stringify(newEventColors));

  // Обновляем глобальные данные
  window.wellEventsData.reagentColors = { ...window.wellEventsData.reagentColors, ...newReagentColors };
  window.wellEventsData.eventColors = { ...window.wellEventsData.eventColors, ...newEventColors };

  // Перерисовываем график
  if (timelineChartWell) {
    timelineChartWell.destroy();
  }

  createTimelineChartWell();
  updateLegendsWell();

  closeColorSettingsWell();
  alert('Цвета сохранены и применены!');
}

// ==========================================
// ОБНОВЛЕНИЕ ЛЕГЕНД
// ==========================================
function updateLegendsWell() {
  const legendReagents = document.getElementById('legend_reagents_well');
  const legendEvents = document.getElementById('legend_events_well');

  if (!window.wellEventsData) return;

  const { reagentColors, eventColors } = window.wellEventsData;

  // Легенда реагентов с чекбоксами и 3 колонками
  if (legendReagents) {
    legendReagents.innerHTML = '';

    // Кнопки управления
    const controlsDiv = document.createElement('div');
    controlsDiv.style.cssText = 'margin-bottom: 8px; display: flex; gap: 8px;';
    controlsDiv.innerHTML = `
      <button onclick="selectAllReagentsWell(true)" style="padding: 4px 8px; font-size: 11px; border: none; background: #28a745; color: white; border-radius: 4px; cursor: pointer;">✓ Все</button>
      <button onclick="selectAllReagentsWell(false)" style="padding: 4px 8px; font-size: 11px; border: none; background: #dc3545; color: white; border-radius: 4px; cursor: pointer;">✗ Ничего</button>
    `;
    legendReagents.appendChild(controlsDiv);

    // Контейнер в 3 колонки
    const gridContainer = document.createElement('div');
    gridContainer.style.cssText = `
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
    `;

    Object.keys(reagentColors).forEach(reagent => {
      const item = document.createElement('div');
      item.style.cssText = `
        display: flex;
        align-items: center;
        padding: 4px 6px;
        border-radius: 4px;
        font-size: 12px;
      `;

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'reagent-checkbox-well';
      checkbox.value = reagent;
      checkbox.checked = true;
      checkbox.style.marginRight = '6px';
      checkbox.onchange = () => createTimelineChartWell();

      const box = document.createElement('div');
      box.style.cssText = `
        min-width: 12px;
        width: 12px;
        height: 12px;
        background-color: ${reagentColors[reagent]};
        border-radius: 50%;
        margin-right: 6px;
        border: 2px solid #fff;
        box-shadow: 0 1px 3px rgba(0,0,0,0.2);
      `;

      const label = document.createElement('span');
      label.textContent = reagent;
      label.style.fontWeight = '500';
      label.style.fontSize = '11px';

      item.appendChild(checkbox);
      item.appendChild(box);
      item.appendChild(label);
      gridContainer.appendChild(item);
    });

    legendReagents.appendChild(gridContainer);
  }

  // Легенда событий с чекбоксами в 2 колонки
  if (legendEvents) {
    legendEvents.innerHTML = '';

    // Кнопки "Выбрать все/ничего"
    const controlsDiv = document.createElement('div');
    controlsDiv.style.cssText = 'margin-bottom: 8px; display: flex; gap: 8px;';
    controlsDiv.innerHTML = `
      <button onclick="selectAllEventsWell(true)" style="padding: 4px 8px; font-size: 11px; border: none; background: #28a745; color: white; border-radius: 4px; cursor: pointer;">✓ Все</button>
      <button onclick="selectAllEventsWell(false)" style="padding: 4px 8px; font-size: 11px; border: none; background: #dc3545; color: white; border-radius: 4px; cursor: pointer;">✗ Ничего</button>
    `;
    legendEvents.appendChild(controlsDiv);

    // Контейнер в 2 колонки
    const gridContainer = document.createElement('div');
    gridContainer.style.cssText = `
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 8px;
    `;

    Object.keys(eventColors).forEach(type => {
      const label = EVENT_TYPE_LABELS[type] || type;

      const item = document.createElement('div');
      item.style.cssText = `
        display: flex;
        align-items: center;
        padding: 6px 8px;
        border-radius: 6px;
      `;

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'event-checkbox-well';
      checkbox.value = type;
      checkbox.checked = true;
      checkbox.style.marginRight = '6px';
      checkbox.onchange = () => createTimelineChartWell();

      const box = document.createElement('div');
      box.style.cssText = `
        width: 4px;
        height: 20px;
        background-color: ${eventColors[type]};
        margin-right: 8px;
        border-radius: 2px;
      `;

      const labelSpan = document.createElement('span');
      labelSpan.textContent = label;
      labelSpan.style.fontSize = '12px';
      labelSpan.style.fontWeight = '500';

      item.appendChild(checkbox);
      item.appendChild(box);
      item.appendChild(labelSpan);
      gridContainer.appendChild(item);
    });

    legendEvents.appendChild(gridContainer);
  }
}

// ==========================================
// ВЫБОР ВСЕХ/НИКАКИХ СОБЫТИЙ И РЕАГЕНТОВ
// ==========================================
function selectAllEventsWell(checked) {
  document.querySelectorAll('.event-checkbox-well').forEach(cb => {
    cb.checked = checked;
  });
  createTimelineChartWell();
}

function selectAllReagentsWell(checked) {
  document.querySelectorAll('.reagent-checkbox-well').forEach(cb => {
    cb.checked = checked;
  });
  createTimelineChartWell();
}

// ==========================================
// ПЛАГИН ДЛЯ ВЕРТИКАЛЬНЫХ ЛИНИЙ СОБЫТИЙ
// ==========================================
const verticalLinePlugin = {
  id: 'verticalLinePlugin',
  afterDatasetsDraw(chart) {
    const ctx = chart.ctx;
    const yAxis = chart.scales.y;
    const xAxis = chart.scales.x;

    if (!yAxis || !xAxis) return;

    // Рисуем вертикальные линии для событий (датасеты с label начинающимся с 📌)
    chart.data.datasets.forEach((dataset, datasetIndex) => {
      // Только для событий (не реагентов)
      if (!dataset.label || !dataset.label.startsWith('📌')) return;

      const meta = chart.getDatasetMeta(datasetIndex);
      if (!meta || meta.hidden) return;

      meta.data.forEach((point) => {
        const x = point.x;
        const yTop = yAxis.top;
        const yBottom = yAxis.bottom;

        ctx.save();
        ctx.strokeStyle = dataset.borderColor;
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 3]); // Пунктирная линия

        ctx.beginPath();
        ctx.moveTo(x, yTop);
        ctx.lineTo(x, yBottom);
        ctx.stroke();

        ctx.restore();
      });
    });
  }
};

// Регистрируем плагин
Chart.register(verticalLinePlugin);

// ==========================================
// СОЗДАНИЕ ГРАФИКА ТАЙМЛАЙН
// ==========================================
function createTimelineChartWell() {
  const canvas = document.getElementById('chart_timeline_well');
  if (!canvas) {
    console.error('Canvas chart_timeline_well not found!');
    return;
  }

  // Используем отфильтрованные данные (от координатора chart_sync.js),
  // с fallback на полные данные при первой загрузке
  const filtered = window.wellEventsFiltered || window.wellEventsData;
  if (!filtered) {
    console.error('wellEventsData not found!');
    return;
  }

  // Удаляем старый график
  if (timelineChartWell instanceof Chart) {
    timelineChartWell.destroy();
  }

  const timelineInjections = filtered.timelineInjections || [];
  const timelineEvents = filtered.timelineEvents || [];
  const reagentColors = (window.wellEventsData || filtered).reagentColors || {};
  const eventColors = (window.wellEventsData || filtered).eventColors || {};
  const wellNumber = (window.wellEventsData || filtered).wellNumber || '';

  // Получаем выбранные типы событий
  const selectedEventTypes = new Set();
  document.querySelectorAll('.event-checkbox-well:checked').forEach(cb => {
    selectedEventTypes.add(cb.value);
  });

  // Получаем выбранные реагенты
  const selectedReagents = new Set();
  document.querySelectorAll('.reagent-checkbox-well:checked').forEach(cb => {
    selectedReagents.add(cb.value);
  });

  // Датасеты для вбросов реагентов (ТОЧКИ с Y = количество)
  const reagentDatasets = {};
  let maxQty = 0;

  timelineInjections.forEach(inj => {
    if (!inj.reagent) return;
    // Фильтрация по выбранным реагентам
    if (selectedReagents.size > 0 && !selectedReagents.has(inj.reagent)) return;

    const qty = parseFloat(inj.qty) || 0;
    if (qty > maxQty) maxQty = qty;

    if (!reagentDatasets[inj.reagent]) {
      reagentDatasets[inj.reagent] = {
        label: `💉 ${inj.reagent}`,
        data: [],
        backgroundColor: reagentColors[inj.reagent] || '#6c757d',
        borderColor: reagentColors[inj.reagent] || '#6c757d',
        borderWidth: 2,
        pointRadius: 6,
        pointHoverRadius: 9,
        showLine: false,
        yAxisID: 'y',
      };
    }

    reagentDatasets[inj.reagent].data.push({
      x: inj.t,
      y: qty,  // ВАЖНО: Y = количество!
      qty: qty,
      description: inj.description,
    });
  });

  // Датасеты для других событий (будут рисоваться плагином как вертикальные линии)
  const eventDatasets = {};

  timelineEvents.forEach(ev => {
    const type = ev.type || 'other';
    if (!selectedEventTypes.has(type)) return;

    const label = EVENT_TYPE_LABELS[type] || type;

    if (!eventDatasets[type]) {
      eventDatasets[type] = {
        label: `📌 ${label}`,
        data: [],
        backgroundColor: eventColors[type] || '#6c757d',
        borderColor: eventColors[type] || '#6c757d',
        borderWidth: 2,
        pointRadius: 0, // Скрываем точки визуально
        pointHoverRadius: 15, // Большая область для hover
        pointHitRadius: 15, // Большая область клика
        showLine: false,
        yAxisID: 'y',
      };
    }

    // События на Y=максимум/2 (для показа tooltip при наведении на линию)
    eventDatasets[type].data.push({
      x: ev.t,
      y: maxQty > 0 ? maxQty / 2 : 5, // Точка посередине для hover
      qty: ev.qty,
      description: ev.description,
      p_tube: ev.p_tube,
      p_line: ev.p_line,
      eventType: label,
      purge_phase: ev.purge_phase,
    });
  });

  const allDatasets = [
    ...Object.values(reagentDatasets),
    ...Object.values(eventDatasets),
  ];

  // Создаем график
  const ctx = canvas.getContext('2d');
  timelineChartWell = new Chart(ctx, {
    type: 'scatter',
    data: { datasets: allDatasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: {
          display: false,
        },
// ДОБАВИТЬ В well_events_chart.js
// Заменить секцию tooltip (строки ~460-495)

tooltip: {
  enabled: true,
  mode: 'nearest',
  intersect: false,
  callbacks: {
    title: (items) => {
      if (!items.length) return '';
      const dt = new Date(items[0].parsed.x);
      return dt.toLocaleString('ru-RU', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
      });
    },
    label: (ctx) => {
      const raw = ctx.raw;
      let lines = [ctx.dataset.label];

      // Оператор
      if (raw.operator) {
        lines.push(`👤 Оператор: ${raw.operator}`);
      }

      // Тип события
      if (raw.eventType) {
        lines.push(`📋 Тип: ${raw.eventType}`);
      }

      // Фаза продувки (для purge событий)
      if (raw.purge_phase) {
        const phaseLabels = {
          'start': 'Начало продувки',
          'press': 'Набор давления',
          'stop': 'Пуск скважины в линию'
        };
        const phaseLabel = phaseLabels[raw.purge_phase] || raw.purge_phase;
        lines.push(`🔄 Фаза: ${phaseLabel}`);
      }

      // Количество
      if (raw.qty != null && raw.qty !== 0) {
        lines.push(`📦 Количество: ${raw.qty}`);
      }

      // Давления
      if (raw.p_tube != null) {
        lines.push(`⬆️ P трубное: ${raw.p_tube}`);
      }
      if (raw.p_line != null) {
        lines.push(`⬇️ P линейное: ${raw.p_line}`);
      }

      // Геолокация
      if (raw.geo_status) {
        const geoLabels = {
          'received': '✅ Координаты получены',
          'not_received': '❌ Координаты не получены',
          'manual': '📍 Координаты вручную',
          'auto': '🛰️ Автоматически'
        };
        lines.push(geoLabels[raw.geo_status] || `📍 ${raw.geo_status}`);
      }

      // Описание
      if (raw.description) {
        lines.push(`💬 ${raw.description}`);
      }

      return lines;
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
            drag: {
              enabled: true,
              backgroundColor: 'rgba(59, 130, 246, 0.1)',
              borderColor: 'rgba(59, 130, 246, 0.5)',
              borderWidth: 1,
            },
            mode: 'x',
            onZoom: function({chart}) {
              updateTimeScale(chart);
            },
          },
        },
      },
      scales: {
        x: {
          type: 'time',
          adapters: {
            date: { locale: 'ru' },
          },
          time: {
            unit: 'day',
            displayFormats: {
              millisecond: 'HH:mm:ss.SSS',
              second: 'HH:mm:ss',
              minute: 'HH:mm',
              hour: 'HH:mm',
              day: 'dd.MM.yyyy',
              week: 'dd.MM',
              month: 'MM.yyyy',
              quarter: 'QQQ yyyy',
              year: 'yyyy'
            },
            tooltipFormat: 'dd.MM.yyyy HH:mm',
          },
          title: {
            display: true,
            text: '📅 Дата',
            font: { size: 14, weight: '600' },
            padding: { top: 10 },
            color: '#495057',
          },
          grid: {
            color: 'rgba(0, 0, 0, 0.06)',
            lineWidth: 1,
            drawBorder: true,
            borderColor: '#dee2e6',
            borderWidth: 2,
          },
          ticks: {
            font: { size: 11 },
            color: '#495057',
            padding: 8,
            maxRotation: 0,
            autoSkip: true,
            autoSkipPadding: 20,
          },
        },
        y: {
          type: 'linear',
          min: 0,
          max: maxQty > 0 ? Math.ceil(maxQty * 1.2) : 10, // Автомасштаб: 20% запас
          ticks: {
            callback: function(value) {
              return value.toFixed(1);
            },
            font: { size: 12, weight: '500' },
            color: '#495057',
            padding: 12,
          },
          title: {
            display: true,
            text: `📊 Количество (скв ${wellNumber})`,
            font: { size: 14, weight: '600' },
            padding: { bottom: 10 },
            color: '#495057',
          },
          grid: {
            color: 'rgba(0, 0, 0, 0.08)',
            lineWidth: 1,
            drawBorder: true,
            borderColor: '#dee2e6',
            borderWidth: 2,
          },
        },
      },
    },
  });

  // Начальная настройка шкалы времени
  updateTimeScale(timelineChartWell);

  // Добавляем кнопки управления
  addZoomControlsWell();
}

// ==========================================
// АВТОМАТИЧЕСКОЕ ОБНОВЛЕНИЕ ШКАЛЫ ВРЕМЕНИ
// ==========================================
function updateTimeScale(chart) {
  if (!chart || !chart.scales || !chart.scales.x) return;

  const xScale = chart.scales.x;
  const min = xScale.min;
  const max = xScale.max;

  if (!min || !max) return;

  // Вычисляем диапазон в миллисекундах
  const range = max - min;

  // Определяем оптимальную единицу времени
  const HOUR = 60 * 60 * 1000;
  const DAY = 24 * HOUR;
  const WEEK = 7 * DAY;
  const MONTH = 30 * DAY;

  let unit;
  if (range < HOUR) {
    unit = 'minute';
  } else if (range < DAY) {
    unit = 'hour';
  } else if (range < WEEK) {
    unit = 'day';
  } else if (range < MONTH) {
    unit = 'week';
  } else {
    unit = 'month';
  }

  // Обновляем конфигурацию времени
  chart.options.scales.x.time.unit = unit;
  chart.update('none'); // Обновляем без анимации
}

// ==========================================
// КНОПКИ УПРАВЛЕНИЯ МАСШТАБОМ
// ==========================================
function addZoomControlsWell() {
  const oldControls = document.getElementById('zoom-controls-well');
  if (oldControls) {
    oldControls.remove();
  }

  const chartBox = document.querySelector('#chart_timeline_well')?.closest('.chart-box');
  if (!chartBox) return;

  const controls = document.createElement('div');
  controls.id = 'zoom-controls-well';
  controls.style.cssText = `
    position: sticky;
    top: 10px;
    left: 0;
    display: flex;
    gap: 8px;
    background: white;
    padding: 10px;
    border-radius: 8px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    z-index: 100;
    margin-bottom: 10px;
    flex-wrap: wrap;
    align-items: center;
  `;

  const buttonStyle = `
    padding: 8px 12px;
    border: none;
    border-radius: 4px;
    background: #007bff;
    color: white;
    cursor: pointer;
    font-size: 14px;
    transition: all 0.2s;
    font-weight: 500;
  `;

  controls.innerHTML = `
    <button style="${buttonStyle}" onmouseover="this.style.background='#0056b3'" 
            onmouseout="this.style.background='#007bff'" onclick="zoomInWell()">🔍 +</button>
    <button style="${buttonStyle}" onmouseover="this.style.background='#0056b3'" 
            onmouseout="this.style.background='#007bff'" onclick="zoomOutWell()">🔍 −</button>
    <button style="${buttonStyle}" onmouseover="this.style.background='#0056b3'" 
            onmouseout="this.style.background='#007bff'" onclick="resetZoomWell()">↺ Reset</button>
    <button style="${buttonStyle}; background: #6c757d;" onmouseover="this.style.background='#5a6268'" 
            onmouseout="this.style.background='#6c757d'" onclick="openColorSettingsWell()">🎨 Цвета</button>
    <span style="padding: 8px; font-size: 12px; color: #666;">
      💡 Выделите область мышкой для zoom
    </span>
  `;

  chartBox.insertBefore(controls, chartBox.firstChild);
}

function zoomInWell() {
  if (timelineChartWell) {
    timelineChartWell.zoom(1.2);
    updateTimeScale(timelineChartWell);
  }
}

function zoomOutWell() {
  if (timelineChartWell) {
    timelineChartWell.zoom(0.8);
    updateTimeScale(timelineChartWell);
  }
}

function resetZoomWell() {
  if (timelineChartWell) {
    timelineChartWell.resetZoom();
    updateTimeScale(timelineChartWell);
  }
}

// ==========================================
// ИНИЦИАЛИЗАЦИЯ ПРИ ЗАГРУЗКЕ
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
  console.log('Initializing well events chart...');

  if (window.wellEventsData) {
    console.log('wellEventsData found:', window.wellEventsData);

    // Загружаем сохраненные цвета из localStorage
    const savedReagentColors = localStorage.getItem('reagentColors');
    const savedEventColors = localStorage.getItem('eventColors');

    if (savedReagentColors) {
      window.wellEventsData.reagentColors = {
        ...window.wellEventsData.reagentColors,
        ...JSON.parse(savedReagentColors)
      };
    }

    if (savedEventColors) {
      window.wellEventsData.eventColors = {
        ...window.wellEventsData.eventColors,
        ...JSON.parse(savedEventColors)
      };
    }

    createTimelineChartWell();
    updateLegendsWell();

    // Экспорт для координатора chart_sync.js
    window.eventsChartReload = function () {
      createTimelineChartWell();
      updateLegendsWell();
    };

    console.log('Chart initialized successfully');
  } else {
    console.error('wellEventsData not found!');
  }
});