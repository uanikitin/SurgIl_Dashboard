// ==========================================
// 0. ГЛОБАЛЬНІ НАЛАШТУВАННЯ CHART.JS
// ==========================================
Chart.defaults.animation = false;
Chart.defaults.responsive = true;
Chart.defaults.maintainAspectRatio = false;

// ==========================================
// ФУНКЦІЇ ДЛЯ РОБОТИ З КОЛЬОРАМИ
// ==========================================
function openColorSettings() {
  const modal = document.getElementById('colorModal');
  const container = document.getElementById('colorInputs');
  container.innerHTML = '';

  // Розділ для реагентів
  const reagentsSection = document.createElement('div');
  reagentsSection.style.marginBottom = '20px';
  reagentsSection.innerHTML = '<h4 style="margin: 10px 0; color: #495057; font-size: 14px;">💉 Реагенти</h4>';
  
  Object.keys(window.reagentsData.reagentColors).forEach(name => {
    const div = document.createElement('div');
    div.style.marginBottom = '10px';
    div.innerHTML = `
      <label style="display: flex; align-items: center; gap: 10px;">
        <span style="min-width: 120px; font-weight: 500;">${name}:</span>
        <input type="color" value="${window.reagentsData.reagentColors[name]}" 
               data-reagent="${name}" class="reagent-color-input"
               style="width: 60px; height: 30px; cursor: pointer;">
      </label>
    `;
    reagentsSection.appendChild(div);
  });
  container.appendChild(reagentsSection);

  // Розділ для подій
  const eventsSection = document.createElement('div');
  eventsSection.innerHTML = '<h4 style="margin: 10px 0; color: #495057; font-size: 14px;">📌 Події</h4>';
  
  Object.keys(window.reagentsData.eventColors).forEach(name => {
    const div = document.createElement('div');
    div.style.marginBottom = '10px';
    div.innerHTML = `
      <label style="display: flex; align-items: center; gap: 10px;">
        <span style="min-width: 120px; font-weight: 500;">${name}:</span>
        <input type="color" value="${window.reagentsData.eventColors[name]}" 
               data-event="${name}" class="event-color-input"
               style="width: 60px; height: 30px; cursor: pointer;">
      </label>
    `;
    eventsSection.appendChild(div);
  });
  container.appendChild(eventsSection);

  modal.style.display = 'block';
}

function closeColorSettings() {
  document.getElementById('colorModal').style.display = 'none';
}

function saveColors() {
  const reagentInputs = document.querySelectorAll('.reagent-color-input');
  const eventInputs = document.querySelectorAll('.event-color-input');
  
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
  
  // Зберігаємо в localStorage
  localStorage.setItem('reagentColors', JSON.stringify(newReagentColors));
  localStorage.setItem('eventColors', JSON.stringify(newEventColors));
  
  // Оновлюємо глобальні дані
  window.reagentsData.reagentColors = { ...window.reagentsData.reagentColors, ...newReagentColors };
  window.reagentsData.eventColors = { ...window.reagentsData.eventColors, ...newEventColors };
  
  // Перемальовуємо ВСІ графіки
  if (window.timelineChart) {
    window.timelineChart.destroy();
  }
  if (window.stockChart) {
    window.stockChart.destroy();
  }
  
  createTimelineChart();
  createStockChart();
  updateLegends();
  
  closeColorSettings();
  alert('Кольори збережено та застосовано на всій сторінці!');
}

// ==========================================
// ОНОВЛЕННЯ ЛЕГЕНД
// ==========================================
function updateLegends() {
  const legendReagents = document.getElementById('legend_reagents');
  const legendEvents = document.getElementById('legend_events');
  const legendWells = document.getElementById('legend_wells');
  
  if (legendReagents) {
    legendReagents.innerHTML = '';
    
    // Пошук по реагентах
    const searchDiv = document.createElement('div');
    searchDiv.style.cssText = 'margin-bottom: 8px;';
    searchDiv.innerHTML = `
      <input type="text" id="reagent-search" placeholder="🔍 Пошук реагента..." 
             style="width: 100%; padding: 6px 10px; border: 1px solid #dee2e6; border-radius: 4px; font-size: 12px;"
             oninput="filterLegendItems('reagent')">
    `;
    legendReagents.appendChild(searchDiv);
    
    const itemsContainer = document.createElement('div');
    itemsContainer.id = 'reagent-items';
    itemsContainer.style.cssText = `
      max-height: 250px;
      overflow-y: auto;
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      padding: 4px;
    `;
    
    Object.keys(window.reagentsData.reagentColors).forEach(reagent => {
      const item = document.createElement('div');
      item.className = 'reagent-legend-item';
      item.dataset.name = reagent.toLowerCase();
      item.style.cssText = `
        display: flex;
        align-items: center;
        padding: 6px 8px;
        border-radius: 6px;
        transition: all 0.2s;
        cursor: pointer;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      `;
      
      item.onmouseover = () => {
        item.style.background = '#e9ecef';
        item.style.transform = 'translateY(-2px)';
        item.style.boxShadow = '0 2px 4px rgba(0,0,0,0.1)';
      };
      item.onmouseout = () => {
        item.style.background = 'transparent';
        item.style.transform = 'translateY(0)';
        item.style.boxShadow = 'none';
      };

      const box = document.createElement('div');
      box.style.cssText = `
        min-width: 14px;
        width: 14px;
        height: 14px;
        background-color: ${window.reagentsData.reagentColors[reagent]};
        border-radius: 50%;
        margin-right: 8px;
        border: 2px solid #fff;
        box-shadow: 0 1px 3px rgba(0,0,0,0.2);
      `;

      const label = document.createElement('span');
      label.textContent = reagent;
      label.style.cssText = `
        font-size: 12px;
        font-weight: 500;
        overflow: hidden;
        text-overflow: ellipsis;
      `;
      label.title = reagent; // Tooltip при наведенні

      item.appendChild(box);
      item.appendChild(label);
      itemsContainer.appendChild(item);
    });
    
    legendReagents.appendChild(itemsContainer);
  }

  if (legendEvents) {
    legendEvents.innerHTML = '';
    
    // Кнопки "Вибрати все/нічого"
    const controlsDiv = document.createElement('div');
    controlsDiv.style.cssText = 'margin-bottom: 8px; display: flex; gap: 8px;';
    controlsDiv.innerHTML = `
      <button onclick="selectAllEvents(true)" style="padding: 4px 8px; font-size: 11px; border: none; background: #28a745; color: white; border-radius: 4px; cursor: pointer;">✓ Все</button>
      <button onclick="selectAllEvents(false)" style="padding: 4px 8px; font-size: 11px; border: none; background: #dc3545; color: white; border-radius: 4px; cursor: pointer;">✗ Нічого</button>
    `;
    legendEvents.appendChild(controlsDiv);
    
    Object.keys(window.reagentsData.eventColors).forEach(type => {
      const item = document.createElement('div');
      item.style.cssText = `
        display: flex;
        align-items: center;
        margin-bottom: 6px;
        padding: 6px 10px;
        border-radius: 6px;
      `;

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'event-checkbox';
      checkbox.value = type;
      checkbox.checked = true;
      checkbox.style.marginRight = '8px';
      checkbox.onchange = () => createTimelineChart();

      const box = document.createElement('div');
      box.style.cssText = `
        width: 4px;
        height: 20px;
        background-color: ${window.reagentsData.eventColors[type]};
        margin-right: 10px;
        border-radius: 2px;
      `;

      const label = document.createElement('span');
      label.textContent = type;
      label.style.fontSize = '13px';
      label.style.fontWeight = '500';

      item.appendChild(checkbox);
      item.appendChild(box);
      item.appendChild(label);
      legendEvents.appendChild(item);
    });
  }

  // Легенда свердловин з чекбоксами та пошуком
  if (legendWells) {
    legendWells.innerHTML = '';
    
    // Кнопки "Вибрати все/нічого"
    const controlsDiv = document.createElement('div');
    controlsDiv.style.cssText = 'margin-bottom: 8px; display: flex; gap: 8px;';
    controlsDiv.innerHTML = `
      <button onclick="selectAllWells(true)" style="padding: 4px 8px; font-size: 11px; border: none; background: #28a745; color: white; border-radius: 4px; cursor: pointer;">✓ Все</button>
      <button onclick="selectAllWells(false)" style="padding: 4px 8px; font-size: 11px; border: none; background: #dc3545; color: white; border-radius: 4px; cursor: pointer;">✗ Нічого</button>
    `;
    legendWells.appendChild(controlsDiv);

    // Пошук по свердловинах
    const searchDiv = document.createElement('div');
    searchDiv.style.cssText = 'margin-bottom: 8px;';
    searchDiv.innerHTML = `
      <input type="text" id="well-search" placeholder="🔍 Пошук свердловини..." 
             style="width: 100%; padding: 6px 10px; border: 1px solid #dee2e6; border-radius: 4px; font-size: 12px;"
             oninput="filterLegendItems('well')">
    `;
    legendWells.appendChild(searchDiv);

    const itemsContainer = document.createElement('div');
    itemsContainer.id = 'well-items';
    itemsContainer.style.cssText = `
      max-height: 250px;
      overflow-y: auto;
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 6px;
      padding: 4px;
    `;

    // Отримуємо всі свердловини
    const allWells = new Set();
    const injections = window.reagentsData.timelineInjections || [];
    const events = window.reagentsData.timelineEvents || [];
    injections.forEach(inj => allWells.add(inj.well));
    events.forEach(ev => allWells.add(ev.well));
    
    Array.from(allWells).sort().forEach(well => {
      const item = document.createElement('div');
      item.className = 'well-legend-item';
      item.dataset.name = well.toLowerCase();
      item.style.cssText = `
        display: flex;
        align-items: center;
        padding: 5px 8px;
        border-radius: 6px;
        cursor: pointer;
        transition: all 0.2s;
        white-space: nowrap;
        overflow: hidden;
      `;
      
      item.onmouseover = () => {
        item.style.background = '#e9ecef';
        item.style.transform = 'translateY(-2px)';
        item.style.boxShadow = '0 2px 4px rgba(0,0,0,0.1)';
      };
      item.onmouseout = () => {
        item.style.background = 'transparent';
        item.style.transform = 'translateY(0)';
        item.style.boxShadow = 'none';
      };

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'well-checkbox';
      checkbox.value = well;
      checkbox.checked = true;
      checkbox.style.cssText = 'margin-right: 6px; cursor: pointer;';
      checkbox.onchange = () => createTimelineChart();

      const label = document.createElement('span');
      label.textContent = `🎯 ${well}`;
      label.style.cssText = `
        font-size: 12px;
        font-weight: 500;
        overflow: hidden;
        text-overflow: ellipsis;
      `;
      label.title = `Свердловина ${well}`;

      item.appendChild(checkbox);
      item.appendChild(label);
      itemsContainer.appendChild(item);
    });
    
    legendWells.appendChild(itemsContainer);
  }
}

// Функція фільтрації легенд
function filterLegendItems(type) {
  const searchInput = document.getElementById(`${type}-search`);
  const container = document.getElementById(`${type}-items`);
  
  if (!searchInput || !container) return;
  
  const searchTerm = searchInput.value.toLowerCase();
  const items = container.querySelectorAll(`.${type}-legend-item`);
  
  let visibleCount = 0;
  
  items.forEach(item => {
    const name = item.dataset.name;
    if (name.includes(searchTerm)) {
      item.style.display = 'flex';
      visibleCount++;
    } else {
      item.style.display = 'none';
    }
  });
  
  // Якщо нічого не знайдено - показуємо повідомлення
  let noResultsMsg = container.querySelector('.no-results-msg');
  
  if (visibleCount === 0) {
    if (!noResultsMsg) {
      noResultsMsg = document.createElement('div');
      noResultsMsg.className = 'no-results-msg';
      noResultsMsg.style.cssText = `
        grid-column: 1 / -1;
        text-align: center;
        padding: 20px;
        color: #6c757d;
        font-size: 13px;
      `;
      noResultsMsg.textContent = '🔍 Нічого не знайдено';
      container.appendChild(noResultsMsg);
    }
  } else {
    if (noResultsMsg) {
      noResultsMsg.remove();
    }
  }
}

// Функції для кнопок "Вибрати все"
function selectAllWells(checked) {
  document.querySelectorAll('.well-checkbox').forEach(cb => {
    cb.checked = checked;
  });
  createTimelineChart();
}

function selectAllEvents(checked) {
  document.querySelectorAll('.event-checkbox').forEach(cb => {
    cb.checked = checked;
  });
  createTimelineChart();
}

// ==========================================
// СТВОРЕННЯ ГРАФІКА ТАЙМЛАЙНУ
// ==========================================
function createTimelineChart() {
  const ctxTimeline = document.getElementById('chart_timeline');
  if (!ctxTimeline) return;

  const data = window.reagentsData || {};
  const injections = data.timelineInjections || [];
  const events = data.timelineEvents || [];
  const reagentColors = data.reagentColors || {};
  const eventColors = data.eventColors || {};

  // Отримуємо вибрані свердловини з чекбоксів
  const selectedWells = Array.from(document.querySelectorAll('.well-checkbox:checked'))
    .map(cb => cb.value);
  
  // Отримуємо вибрані типи подій з чекбоксів
  const selectedEventTypes = Array.from(document.querySelectorAll('.event-checkbox:checked'))
    .map(cb => cb.value);

  // Фільтруємо дані
  const filteredInjections = selectedWells.length > 0 
    ? injections.filter(inj => selectedWells.includes(inj.well))
    : injections;
    
  const filteredEvents = events.filter(ev => {
    const wellMatch = selectedWells.length === 0 || selectedWells.includes(ev.well);
    const typeMatch = selectedEventTypes.length === 0 || selectedEventTypes.includes(ev.type);
    return wellMatch && typeMatch;
  });

  // Отримуємо унікальні свердловини з відфільтрованих даних
  const allWells = new Set();
  filteredInjections.forEach(inj => allWells.add(inj.well));
  filteredEvents.forEach(ev => allWells.add(ev.well));
  const wellsList = Array.from(allWells).sort();

  // Якщо немає даних - показуємо повідомлення
  if (wellsList.length === 0) {
    const ctx = ctxTimeline.getContext('2d');
    ctx.clearRect(0, 0, ctxTimeline.width, ctxTimeline.height);
    ctx.font = '16px Arial';
    ctx.fillStyle = '#999';
    ctx.textAlign = 'center';
    ctx.fillText('Виберіть свердловини або події для відображення', ctxTimeline.width / 2, ctxTimeline.height / 2);
    return;
  }

  // Створюємо mapping: свердловина -> індекс по осі Y
  const wellToY = {};
  wellsList.forEach((well, idx) => {
    wellToY[well] = idx;
  });

  // --- Datasets для вбросів (точки по свердловинам) ---
  const injectionsGrouped = {};
  filteredInjections.forEach(inj => {
    const reagent = inj.reagent || 'Невідомо';
    if (!injectionsGrouped[reagent]) {
      injectionsGrouped[reagent] = [];
    }
    injectionsGrouped[reagent].push({
      x: inj.t,
      y: wellToY[inj.well] ?? 0,
      qty: inj.qty,
      well: inj.well,
      description: inj.description
    });
  });

  const injectionsDatasets = Object.keys(injectionsGrouped).map(reagent => ({
    label: `💉 ${reagent}`,
    type: 'scatter',
    data: injectionsGrouped[reagent],
    backgroundColor: reagentColors[reagent] || '#6c757d',
    borderColor: '#ffffff',
    borderWidth: 2,
    pointRadius: 8,
    pointHoverRadius: 12,
    pointStyle: 'circle',
    pointHoverBorderWidth: 3,
  }));

  // --- Datasets для подій (вертикальні лінії) ---
  const eventsGrouped = {};
  filteredEvents.forEach(ev => {
    const type = ev.type || 'other';
    if (!eventsGrouped[type]) {
      eventsGrouped[type] = [];
    }
    
    const yPos = wellToY[ev.well] ?? 0;
    
    // Додаємо дві точки для вертикальної лінії
    eventsGrouped[type].push({
      x: ev.t,
      y: yPos - 0.3,
      well: ev.well,
      description: ev.description,
      p_tube: ev.p_tube,
      p_line: ev.p_line
    });
    eventsGrouped[type].push({
      x: ev.t,
      y: yPos + 0.3,
      well: ev.well,
      description: ev.description,
      p_tube: ev.p_tube,
      p_line: ev.p_line
    });
  });

  // --- Datasets для подій (вертикальні стовпчики на рівні свердловини) ---
  const eventsDatasets = [];
  
  Object.keys(eventsGrouped).forEach(type => {
    eventsGrouped[type].forEach((evPoint, idx) => {
      // Беремо тільки перші точки (не дублюємо)
      if (idx % 2 === 0) {
        const yCenter = evPoint.y + 0.3; // центр свердловини
        
        // Створюємо bar, який йде від центру вгору і вниз
        eventsDatasets.push({
          label: `📌 ${type}`,
          type: 'bar',
          data: [{
            x: evPoint.x,
            y: [yCenter - 0.25, yCenter + 0.25] // висота бара в межах свердловини
          }],
          backgroundColor: eventColors[type] || '#999',
          borderColor: eventColors[type] || '#999',
          borderWidth: 1,
          barThickness: 3,
          barPercentage: 1.0,
          categoryPercentage: 1.0,
          // Зберігаємо метадані для tooltip
          meta: {
            well: evPoint.well,
            description: evPoint.description,
            p_tube: evPoint.p_tube,
            p_line: evPoint.p_line
          }
        });
      }
    });
  });

  const allDatasets = [...injectionsDatasets, ...eventsDatasets];

  // Знищуємо старий графік
  if (window.timelineChart) {
    window.timelineChart.destroy();
  }

  // Створюємо новий графік
  window.timelineChart = new Chart(ctxTimeline, {
    type: 'scatter',
    data: {
      datasets: allDatasets
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: 'nearest',
        intersect: false
      },
      plugins: {
        legend: {
          display: false
        },
        tooltip: {
          backgroundColor: 'rgba(33, 37, 41, 0.96)',
          padding: 14,
          titleFont: { size: 13, weight: '600', family: 'system-ui, -apple-system, sans-serif' },
          bodyFont: { size: 12, family: 'system-ui, -apple-system, sans-serif' },
          borderColor: 'rgba(222, 226, 230, 0.8)',
          borderWidth: 1,
          cornerRadius: 6,
          displayColors: true,
          callbacks: {
            title: (items) => {
              if (items.length === 0) return '';
              const item = items[0];
              const raw = item.raw;
              
              // Для bar (події) беремо x, для scatter беремо raw.x
              const timeValue = raw.x || item.parsed.x;
              const date = new Date(timeValue);
              return '🕒 ' + date.toLocaleString('uk-UA', {
                day: '2-digit',
                month: 'short',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
              });
            },
            label: (ctx) => {
              const raw = ctx.raw;
              const meta = ctx.dataset.meta;
              const lines = [];
              
              // Дані з raw (для scatter - вбросів)
              if (raw.well) lines.push(`🎯 Свердловина: ${raw.well}`);
              if (raw.qty) lines.push(`📊 Кількість: ${raw.qty.toFixed(3)}`);
              if (raw.description) lines.push(`📝 ${raw.description}`);
              if (raw.p_tube) lines.push(`🔧 P трубн: ${raw.p_tube} атм`);
              if (raw.p_line) lines.push(`🔧 P лін: ${raw.p_line} атм`);
              
              // Дані з meta (для bar - подій)
              if (meta) {
                if (meta.well) lines.push(`🎯 Свердловина: ${meta.well}`);
                if (meta.description) lines.push(`📝 ${meta.description}`);
                if (meta.p_tube) lines.push(`🔧 P трубн: ${meta.p_tube} атм`);
                if (meta.p_line) lines.push(`🔧 P лін: ${meta.p_line} атм`);
              }
              
              return lines.length > 0 ? lines : ctx.dataset.label;
            },
            labelColor: (ctx) => {
              return {
                borderColor: ctx.dataset.borderColor || ctx.dataset.backgroundColor,
                backgroundColor: ctx.dataset.backgroundColor,
                borderWidth: 2,
                borderRadius: 3
              };
            }
          }
        },
        zoom: {
          pan: {
            enabled: true,
            mode: 'x',
            modifierKey: 'shift'
          },
          zoom: {
            drag: {
              enabled: true,
              backgroundColor: 'rgba(75, 192, 192, 0.2)',
              borderColor: 'rgba(75, 192, 192, 0.8)',
              borderWidth: 2
            },
            wheel: {
              enabled: false
            },
            mode: 'x'
          },
          limits: {
            x: { min: 'original', max: 'original' }
          }
        }
      },
      scales: {
        x: {
          type: 'time',
          time: {
            unit: 'day',
            displayFormats: {
              millisecond: 'HH:mm:ss.SSS',
              second: 'HH:mm:ss',
              minute: 'HH:mm',
              hour: 'HH:mm',
              day: 'dd MMM',
              week: 'dd MMM',
              month: 'MMM yyyy',
              quarter: 'MMM yyyy',
              year: 'yyyy'
            },
            tooltipFormat: 'dd MMM yyyy HH:mm'
          },
          title: {
            display: true,
            text: '📅 Дата/час',
            font: { size: 14, weight: '600', family: 'system-ui, -apple-system, sans-serif' },
            padding: { top: 10 },
            color: '#495057'
          },
          grid: {
            color: 'rgba(0, 0, 0, 0.06)',
            lineWidth: 1,
            drawBorder: true,
            borderColor: '#dee2e6',
            borderWidth: 2
          },
          ticks: {
            font: { size: 11, family: 'system-ui, -apple-system, sans-serif' },
            color: '#495057',
            padding: 8,
            maxRotation: 0,
            autoSkip: true,
            autoSkipPadding: 20
          }
        },
        y: {
          type: 'linear',
          min: -0.5,
          max: wellsList.length - 0.5,
          ticks: {
            stepSize: 1,
            callback: function(value) {
              return wellsList[Math.round(value)] || '';
            },
            font: { size: 12, weight: '500', family: 'system-ui, -apple-system, sans-serif' },
            color: '#495057',
            padding: 12
          },
          title: {
            display: true,
            text: '🎯 Свердловина',
            font: { size: 14, weight: '600', family: 'system-ui, -apple-system, sans-serif' },
            padding: { bottom: 10 },
            color: '#495057'
          },
          grid: {
            color: 'rgba(0, 0, 0, 0.08)',
            lineWidth: 1,
            drawBorder: true,
            borderColor: '#dee2e6',
            borderWidth: 2
          }
        }
      }
    }
  });

  // Додаємо кнопки управління
  addZoomControls();
}

// ==========================================
// КНОПКИ УПРАВЛІННЯ МАСШТАБОМ
// ==========================================
function addZoomControls() {
  // Видаляємо стару панель, якщо є
  const oldControls = document.getElementById('zoom-controls');
  if (oldControls) {
    oldControls.remove();
  }

  const chartBox = document.querySelector('#chart_timeline')?.closest('.chart-box') || 
                   document.querySelector('#chart_timeline')?.parentElement;
  
  if (!chartBox) return;

  const controls = document.createElement('div');
  controls.id = 'zoom-controls';
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
            onmouseout="this.style.background='#007bff'" onclick="zoomIn()">🔍 +</button>
    <button style="${buttonStyle}" onmouseover="this.style.background='#0056b3'" 
            onmouseout="this.style.background='#007bff'" onclick="zoomOut()">🔍 −</button>
    <button style="${buttonStyle}" onmouseover="this.style.background='#0056b3'" 
            onmouseout="this.style.background='#007bff'" onclick="resetZoom()">↺ Reset</button>
    <span style="padding: 8px; font-size: 12px; color: #666;">
      💡 Виділіть область мишкою для zoom
    </span>
  `;

  // Вставляємо ПЕРЕД графіком
  chartBox.insertBefore(controls, chartBox.firstChild);
}

function zoomIn() {
  if (window.timelineChart) {
    window.timelineChart.zoom(1.2);
  }
}

function zoomOut() {
  if (window.timelineChart) {
    window.timelineChart.zoom(0.8);
  }
}

function resetZoom() {
  if (window.timelineChart) {
    window.timelineChart.resetZoom();
  }
}

// ==========================================
// ЗАВАНТАЖЕННЯ DOM
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
  const data = window.reagentsData || {};

  // Завантажуємо збережені кольори з localStorage
  const savedReagentColors = localStorage.getItem('reagentColors');
  const savedEventColors = localStorage.getItem('eventColors');
  
  if (savedReagentColors) {
    window.reagentsData.reagentColors = { 
      ...window.reagentsData.reagentColors, 
      ...JSON.parse(savedReagentColors) 
    };
  }
  
  if (savedEventColors) {
    window.reagentsData.eventColors = { 
      ...window.reagentsData.eventColors, 
      ...JSON.parse(savedEventColors) 
    };
  }

  // ==========================================
  // 1. ГРАФІК ЗАЛИШКІВ (ТОП-30)
  // ==========================================
  createStockChart();

  // ==========================================
  // 2. ТАЙМЛАЙН
  // ==========================================
  createTimelineChart();
  updateLegends();
});

// ==========================================
// ФУНКЦІЯ СТВОРЕННЯ ГРАФІКА ЗАЛИШКІВ
// ==========================================
function createStockChart() {
  const data = window.reagentsData || {};
  const ctxStock = document.getElementById('chart_stock');
  
  if (!ctxStock || !data.stockLabels || !data.stockValues) return;
  
  if (window.stockChart instanceof Chart) {
    window.stockChart.destroy();
  }

  const maxValue = Math.max(...data.stockValues);
  const suggestedMax = Math.ceil(maxValue * 1.1);
  
  const chartHeight = 600;
  
  if (!ctxStock.dataset.heightSet) {
    ctxStock.style.height = chartHeight + 'px';
    ctxStock.height = chartHeight;
    ctxStock.dataset.heightSet = 'true';
  }

  // Використовуємо ТІ САМІ кольори, що й на таймлайні
  const colors = data.stockLabels.map(reagentName => {
    // Шукаємо колір для цього реагента
    return window.reagentsData.reagentColors[reagentName] || '#6c757d';
  });

  window.stockChart = new Chart(ctxStock, {
    type: 'bar',
    data: {
      labels: data.stockLabels,
      datasets: [{
        label: 'Залишок',
        data: data.stockValues,
        backgroundColor: colors,
        borderColor: colors.map(c => c),
        borderWidth: 1,
        barThickness: 18,
        maxBarThickness: 18
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      onResize: null,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const value = ctx.parsed.x.toFixed(3);
              const unit = data.stockUnits?.[ctx.dataIndex] || 'шт';
              return `Залишок: ${value} ${unit}`;
            }
          }
        }
      },
      scales: {
        x: {
          beginAtZero: true,
          max: suggestedMax,
          ticks: {
            stepSize: Math.ceil(suggestedMax / 10)
          },
          title: {
            display: true,
            text: 'Кількість'
          }
        },
        y: {
          ticks: {
            font: { size: 11 },
            autoSkip: false
          }
        }
      }
    }
  });
}