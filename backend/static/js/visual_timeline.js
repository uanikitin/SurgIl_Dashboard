// ==========================================
// TIMELINE CHART - ИСПРАВЛЕННАЯ ВЕРСИЯ
// ==========================================

Chart.defaults.animation = false;
Chart.defaults.responsive = true;
Chart.defaults.maintainAspectRatio = false;

window.timelineChart = null;
window.showGridLines = true;
window.currentPeriod = '3d';
window.filterDateFrom = null;
window.filterDateTo = null;

// Настройка прозрачности полос статуса (0.0 - 1.0)
window.statusStripeOpacity = 0.15;

// Текущий список скважин для плагина statusStripes
window.currentWellsList = [];

// Плагин для отрисовки цветных полос по статусу скважины
const statusStripesPlugin = {
    id: 'statusStripes',
    beforeDraw: (chart) => {
        if (!window.statusStripeOpacity || window.statusStripeOpacity <= 0) return;

        const ctx = chart.ctx;
        const chartArea = chart.chartArea;
        const yScale = chart.scales.y;

        if (!chartArea || !yScale) return;

        const wellStatuses = window.reagentsData?.wellStatuses || {};
        const statusColors = window.reagentsData?.statusColors || {};
        const wellsList = window.currentWellsList || [];

        if (wellsList.length === 0) return;

        ctx.save();

        wellsList.forEach((well, idx) => {
            const status = wellStatuses[well];
            const color = statusColors[status];

            if (!color) return;

            // Вычисляем границы полосы для данной скважины
            const yTop = yScale.getPixelForValue(idx - 0.5);
            const yBottom = yScale.getPixelForValue(idx + 0.5);

            // Устанавливаем цвет с прозрачностью
            ctx.fillStyle = hexToRgba(color, window.statusStripeOpacity);
            ctx.fillRect(
                chartArea.left,
                Math.min(yTop, yBottom),
                chartArea.right - chartArea.left,
                Math.abs(yBottom - yTop)
            );
        });

        ctx.restore();
    }
};

// Вспомогательная функция: HEX -> RGBA
function hexToRgba(hex, alpha) {
    if (!hex) return `rgba(128, 128, 128, ${alpha})`;

    // Убираем # если есть
    hex = hex.replace('#', '');

    // Парсим RGB
    let r, g, b;
    if (hex.length === 3) {
        r = parseInt(hex[0] + hex[0], 16);
        g = parseInt(hex[1] + hex[1], 16);
        b = parseInt(hex[2] + hex[2], 16);
    } else {
        r = parseInt(hex.substring(0, 2), 16);
        g = parseInt(hex.substring(2, 4), 16);
        b = parseInt(hex.substring(4, 6), 16);
    }

    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// Регистрируем плагин
Chart.register(statusStripesPlugin);

// ==========================================
// ПЛАГИН ДЛЯ ОТРИСОВКИ ИНТЕРВАЛОВ ПРОДУВКИ
// ==========================================
window.purgeIntervals = []; // Массив интервалов продувки для отрисовки
window.hoveredPurgeInterval = null; // Интервал под курсором

const purgeIntervalsPlugin = {
    id: 'purgeIntervals',
    beforeDatasetsDraw: (chart) => {
        const intervals = window.purgeIntervals || [];
        if (intervals.length === 0) return;

        const ctx = chart.ctx;
        const chartArea = chart.chartArea;
        const xScale = chart.scales.x;
        const yScale = chart.scales.y;

        if (!chartArea || !xScale || !yScale) return;

        ctx.save();

        intervals.forEach((interval, idx) => {
            const { startTime, endTime, wellY, totalMin, pressTime, pressMin, stopMin, well } = interval;

            // Конвертируем время в пиксели
            const x1 = xScale.getPixelForValue(new Date(startTime));
            const x2 = xScale.getPixelForValue(new Date(endTime));

            // Y-координаты для скважины
            const yTop = yScale.getPixelForValue(wellY - 0.4);
            const yBottom = yScale.getPixelForValue(wellY + 0.4);
            const yCenter = (yTop + yBottom) / 2;
            const height = Math.abs(yBottom - yTop);

            // Проверяем, что интервал виден на графике
            if (x2 < chartArea.left || x1 > chartArea.right) return;

            // Ограничиваем область графика
            const clampedX1 = Math.max(x1, chartArea.left);
            const clampedX2 = Math.min(x2, chartArea.right);
            const width = clampedX2 - clampedX1;

            if (width <= 0) return;

            const isHovered = window.hoveredPurgeInterval === idx;

            // Рисуем двухцветную заливку если есть pressTime
            if (pressTime && !isHovered) {
                const xPress = xScale.getPixelForValue(new Date(pressTime));
                const clampedXPress = Math.max(Math.min(xPress, clampedX2), clampedX1);

                // Оранжевая часть (start → press)
                ctx.fillStyle = 'rgba(251, 146, 60, 0.25)';
                ctx.fillRect(clampedX1, Math.min(yTop, yBottom), clampedXPress - clampedX1, height);

                // Зелёная часть (press → stop)
                ctx.fillStyle = 'rgba(34, 197, 94, 0.25)';
                ctx.fillRect(clampedXPress, Math.min(yTop, yBottom), clampedX2 - clampedXPress, height);
            } else {
                // Одноцветная заливка (или при hover)
                ctx.fillStyle = isHovered ? 'rgba(59, 130, 246, 0.3)' : 'rgba(148, 163, 184, 0.2)';
                ctx.fillRect(clampedX1, Math.min(yTop, yBottom), width, height);
            }

            // Рисуем границы
            ctx.strokeStyle = isHovered ? 'rgba(59, 130, 246, 0.8)' : 'rgba(100, 116, 139, 0.5)';
            ctx.lineWidth = isHovered ? 2 : 1;
            ctx.setLineDash(isHovered ? [] : [4, 2]);
            ctx.strokeRect(clampedX1, Math.min(yTop, yBottom), width, height);
            ctx.setLineDash([]);

            // Сохраняем координаты для обнаружения hover
            interval._bounds = {
                x1: clampedX1, x2: clampedX2,
                y1: Math.min(yTop, yBottom), y2: Math.max(yTop, yBottom)
            };

            // Рисуем текст НАД блоком
            if (width > 25) {
                ctx.font = 'bold 10px -apple-system, BlinkMacSystemFont, sans-serif';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'bottom';

                const textX = clampedX1 + width / 2;
                const textY = Math.min(yTop, yBottom) - 3; // Над блоком

                if (isHovered && pressMin !== undefined && stopMin !== undefined) {
                    // При наведении: детальная информация (две строки над блоком)
                    const line1 = `⏱ ${totalMin} мин`;
                    const line2 = `🔸${pressMin}м → 🔹${stopMin}м`;

                    ctx.font = '9px -apple-system, BlinkMacSystemFont, sans-serif';
                    const maxWidth = Math.max(ctx.measureText(line1).width, ctx.measureText(line2).width) + 8;

                    // Фон для двух строк
                    ctx.fillStyle = 'rgba(255, 255, 255, 0.95)';
                    ctx.fillRect(textX - maxWidth / 2, textY - 26, maxWidth, 24);
                    ctx.strokeStyle = 'rgba(59, 130, 246, 0.6)';
                    ctx.lineWidth = 1;
                    ctx.strokeRect(textX - maxWidth / 2, textY - 26, maxWidth, 24);

                    // Текст
                    ctx.font = 'bold 10px -apple-system, BlinkMacSystemFont, sans-serif';
                    ctx.fillStyle = '#1e40af';
                    ctx.fillText(line1, textX, textY - 13);
                    ctx.font = '9px -apple-system, BlinkMacSystemFont, sans-serif';
                    ctx.fillStyle = '#475569';
                    ctx.fillText(line2, textX, textY - 2);
                } else {
                    // По умолчанию: только общее время над блоком
                    const label = `${totalMin} мин`;
                    const textWidth = ctx.measureText(label).width + 6;

                    ctx.fillStyle = 'rgba(255, 255, 255, 0.92)';
                    ctx.fillRect(textX - textWidth / 2, textY - 13, textWidth, 12);

                    ctx.fillStyle = '#334155';
                    ctx.fillText(label, textX, textY - 2);
                }
            }
        });

        ctx.restore();
    },

    // Обработка движения мыши для hover эффекта
    afterEvent: (chart, args) => {
        if (args.event.type !== 'mousemove') return;

        const intervals = window.purgeIntervals || [];
        const x = args.event.x;
        const y = args.event.y;
        let found = null;

        for (let i = 0; i < intervals.length; i++) {
            const bounds = intervals[i]._bounds;
            if (bounds && x >= bounds.x1 && x <= bounds.x2 && y >= bounds.y1 && y <= bounds.y2) {
                found = i;
                break;
            }
        }

        if (window.hoveredPurgeInterval !== found) {
            window.hoveredPurgeInterval = found;
            chart.draw();
        }
    }
};

Chart.register(purgeIntervalsPlugin);

// Функция для вычисления интервалов продувки из событий
function calculatePurgeIntervals(events, wellToY) {
    const intervals = [];

    // Группируем события продувки по скважине
    const purgesByWell = {};
    events.forEach(ev => {
        if (ev.type === 'purge' && ev.purge_phase) {
            const well = ev.well;
            if (!purgesByWell[well]) {
                purgesByWell[well] = [];
            }
            purgesByWell[well].push({
                time: new Date(ev.t),
                phase: ev.purge_phase,
                well: well
            });
        }
    });

    // Для каждой скважины находим полные циклы продувки (start → press → stop)
    Object.keys(purgesByWell).forEach(well => {
        const purges = purgesByWell[well].sort((a, b) => a.time - b.time);
        const wellY = wellToY[well];

        if (wellY === undefined) return;

        for (let i = 0; i < purges.length; i++) {
            const current = purges[i];

            if (current.phase !== 'start') continue;

            // Ищем полный цикл: start → press → stop или start → stop
            let pressEvent = null;
            let stopEvent = null;

            for (let j = i + 1; j < purges.length; j++) {
                const next = purges[j];
                const diffHours = (next.time - current.time) / (1000 * 60 * 60);
                if (diffHours > 4) break; // Ограничиваем 4 часами

                if (next.phase === 'press' && !pressEvent) {
                    pressEvent = next;
                } else if (next.phase === 'stop') {
                    stopEvent = next;
                    break;
                } else if (next.phase === 'start') {
                    // Новый start прерывает поиск
                    break;
                }
            }

            // Создаём интервал если нашли хотя бы stop
            if (stopEvent) {
                const totalMin = Math.round((stopEvent.time - current.time) / (1000 * 60));
                const pressMin = pressEvent ? Math.round((pressEvent.time - current.time) / (1000 * 60)) : null;
                const stopMin = pressEvent ? Math.round((stopEvent.time - pressEvent.time) / (1000 * 60)) : null;

                intervals.push({
                    startTime: current.time.toISOString(),
                    endTime: stopEvent.time.toISOString(),
                    pressTime: pressEvent ? pressEvent.time.toISOString() : null,
                    wellY: wellY,
                    well: well,
                    totalMin: totalMin,
                    pressMin: pressMin,  // Время start → press (набор давления)
                    stopMin: stopMin     // Время press → stop (пуск в линию)
                });
            } else if (pressEvent) {
                // Неполная продувка: только start → press
                const totalMin = Math.round((pressEvent.time - current.time) / (1000 * 60));
                intervals.push({
                    startTime: current.time.toISOString(),
                    endTime: pressEvent.time.toISOString(),
                    pressTime: null,
                    wellY: wellY,
                    well: well,
                    totalMin: totalMin,
                    pressMin: totalMin,
                    stopMin: null
                });
            }
        }
    });

    return intervals;
}

const EVENT_TRANSLATIONS = {
    'purge': 'Продувка',
    'reagent': 'Вброс реагента',
    'pressure': 'Замер давления',
    'equip': 'Оборудование',
    'production': 'Добыча',
    'maintenance': 'Обслуживание',
    'other': 'Другое',
};

// ==========================================
// ИНИЦИАЛИЗАЦИЯ
// ==========================================
document.addEventListener('DOMContentLoaded', function() {
    console.log('Timeline chart initializing...');

    // Загружаем кастомные цвета
    loadCustomColors();

    // Загружаем сохранённую прозрачность полос
    loadStripeOpacity();

    // Инициализируем период из URL
    initializePeriod();

    // Обновляем легенды
    updateLegends();

    // Создаем график (с небольшой задержкой для гарантии)
    setTimeout(() => {
        createTimelineChart();
    }, 100);
});

// ==========================================
// ПЕРИОД
// ==========================================
function initializePeriod() {
    console.log('Initializing period from URL...');

    // Получаем параметры из URL
    const urlParams = new URLSearchParams(window.location.search);
    const period = urlParams.get('tl_period') || '3d';
    const dateFrom = urlParams.get('tl_date_from');
    const dateTo = urlParams.get('tl_date_to');

    console.log('URL parameters:', { period, dateFrom, dateTo });

    // Устанавливаем активную кнопку
    document.querySelectorAll('.btn-period').forEach(btn => {
        btn.classList.remove('active');
        if (btn.dataset.period === period) {
            btn.classList.add('active');
        }
    });

    window.currentPeriod = period;

    // Устанавливаем даты фильтра
    if (period === 'custom') {
        // Показываем строку с custom датами
        const customRow = document.getElementById('customDatesRow');
        if (customRow) {
            customRow.style.display = 'flex';
        }

        if (dateFrom && dateTo) {
            // Используем даты из URL
            document.getElementById('date_from').value = dateFrom;
            document.getElementById('date_to').value = dateTo;

            window.filterDateFrom = new Date(dateFrom + 'T00:00:00');
            window.filterDateTo = new Date(dateTo + 'T23:59:59');
        } else {
            // По умолчанию: последние 3 дня
            const now = new Date();
            const threeDaysAgo = new Date(now);
            threeDaysAgo.setDate(threeDaysAgo.getDate() - 3);

            const dateFromStr = threeDaysAgo.toISOString().split('T')[0];
            const dateToStr = now.toISOString().split('T')[0];

            document.getElementById('date_from').value = dateFromStr;
            document.getElementById('date_to').value = dateToStr;

            window.filterDateFrom = new Date(dateFromStr + 'T00:00:00');
            window.filterDateTo = new Date(dateToStr + 'T23:59:59');
        }
    } else {
        // Скрываем custom строку
        const customRow = document.getElementById('customDatesRow');
        if (customRow) {
            customRow.style.display = 'none';
        }

        // Устанавливаем даты для стандартного периода
        const now = new Date();
        let daysAgo = 3;

        if (period === '1d') daysAgo = 1;
        else if (period === '1w') daysAgo = 7;
        else if (period === '1m') daysAgo = 30;

        const dateFrom = new Date(now);
        dateFrom.setDate(dateFrom.getDate() - daysAgo);
        dateFrom.setHours(0, 0, 0, 0);

        const dateTo = new Date(now);
        dateTo.setHours(23, 59, 59, 999);

        window.filterDateFrom = dateFrom;
        window.filterDateTo = dateTo;
    }

    console.log('Period initialized:', {
        currentPeriod: window.currentPeriod,
        filterDateFrom: window.filterDateFrom,
        filterDateTo: window.filterDateTo
    });
}

function setPeriod(period) {
    console.log('Setting period to:', period);

    // Обновляем активную кнопку
    document.querySelectorAll('.btn-period').forEach(btn => {
        btn.classList.remove('active');
        if (btn.dataset.period === period) {
            btn.classList.add('active');
        }
    });

    const customRow = document.getElementById('customDatesRow');
    window.currentPeriod = period;

    if (period === 'custom') {
        // Показываем строку с custom датами
        if (customRow) {
            customRow.style.display = 'flex';
        }

        // Устанавливаем значения по умолчанию (последние 3 дня)
        const now = new Date();
        const threeDaysAgo = new Date(now);
        threeDaysAgo.setDate(threeDaysAgo.getDate() - 3);

        document.getElementById('date_from').value = threeDaysAgo.toISOString().split('T')[0];
        document.getElementById('date_to').value = now.toISOString().split('T')[0];

        // Не обновляем график сразу - ждем пока пользователь выберет даты
    } else {
        // Скрываем custom строку
        if (customRow) {
            customRow.style.display = 'none';
        }

        // Устанавливаем даты для стандартного периода
        const now = new Date();
        let daysAgo = 3;

        if (period === '1d') daysAgo = 1;
        else if (period === '1w') daysAgo = 7;
        else if (period === '1m') daysAgo = 30;

        const dateFrom = new Date(now);
        dateFrom.setDate(dateFrom.getDate() - daysAgo);
        dateFrom.setHours(0, 0, 0, 0);

        const dateTo = new Date(now);
        dateTo.setHours(23, 59, 59, 999);

        window.filterDateFrom = dateFrom;
        window.filterDateTo = dateTo;

        // Обновляем URL и перерисовываем график
        updateURL();
        createTimelineChart();
    }
}

function applyCustomDates() {
    console.log('Applying custom dates...');

    const dateFromStr = document.getElementById('date_from').value;
    const dateToStr = document.getElementById('date_to').value;

    if (!dateFromStr || !dateToStr) {
        alert('Пожалуйста, выберите обе даты');
        return;
    }

    const dateFrom = new Date(dateFromStr);
    dateFrom.setHours(0, 0, 0, 0);

    const dateTo = new Date(dateToStr);
    dateTo.setHours(23, 59, 59, 999);

    if (dateFrom > dateTo) {
        alert('Дата "От" не может быть больше даты "До"');
        return;
    }

    window.filterDateFrom = dateFrom;
    window.filterDateTo = dateTo;
    window.currentPeriod = 'custom';

    // Обновляем URL и перерисовываем график
    updateURL();
    createTimelineChart();
}

function updateURL() {
    const url = new URL(window.location);

    // Обновляем параметр периода
    url.searchParams.set('tl_period', window.currentPeriod);

    // Для custom периода добавляем даты
    if (window.currentPeriod === 'custom') {
        if (window.filterDateFrom) {
            url.searchParams.set('tl_date_from', window.filterDateFrom.toISOString().split('T')[0]);
        }
        if (window.filterDateTo) {
            url.searchParams.set('tl_date_to', window.filterDateTo.toISOString().split('T')[0]);
        }
    } else {
        // Для стандартных периодов удаляем параметры дат
        url.searchParams.delete('tl_date_from');
        url.searchParams.delete('tl_date_to');
    }

    // Обновляем историю браузера без перезагрузки
    window.history.replaceState({}, '', url);
    console.log('URL updated:', url.toString());
}

function toggleGridLines() {
    window.showGridLines = document.getElementById('showGridLines').checked;
    createTimelineChart();
}

function updateStripeOpacity(value) {
    const opacity = parseInt(value) / 100;
    window.statusStripeOpacity = opacity;

    // Обновляем отображение значения
    const valueSpan = document.getElementById('stripeOpacityValue');
    if (valueSpan) {
        valueSpan.textContent = value + '%';
    }

    // Перерисовываем график
    if (window.timelineChart) {
        window.timelineChart.update('none');
    }

    // Сохраняем в localStorage
    try {
        localStorage.setItem('surgil_stripe_opacity', value);
    } catch (e) {}
}

// Загрузка сохранённой прозрачности
function loadStripeOpacity() {
    try {
        const saved = localStorage.getItem('surgil_stripe_opacity');
        if (saved !== null) {
            const value = parseInt(saved);
            window.statusStripeOpacity = value / 100;

            const slider = document.getElementById('stripeOpacity');
            const valueSpan = document.getElementById('stripeOpacityValue');
            if (slider) slider.value = value;
            if (valueSpan) valueSpan.textContent = value + '%';
        }
    } catch (e) {}
}

function resetZoom() {
    if (window.timelineChart) {
        window.timelineChart.resetZoom();
    }
}

// ==========================================
// ЦВЕТА
// ==========================================
function loadCustomColors() {
    const savedReagent = localStorage.getItem('reagentColors');
    const savedEvent = localStorage.getItem('eventColors');

    if (savedReagent) {
        try {
            Object.assign(window.reagentsData.reagentColors, JSON.parse(savedReagent));
        } catch (e) {
            console.error('Error loading reagent colors:', e);
        }
    }

    if (savedEvent) {
        try {
            Object.assign(window.reagentsData.eventColors, JSON.parse(savedEvent));
        } catch (e) {
            console.error('Error loading event colors:', e);
        }
    }
}

function openColorSettings() {
    const modal = document.getElementById('colorModal');
    const container = document.getElementById('colorInputs');
    container.innerHTML = '';

    const reagentsSection = document.createElement('div');
    reagentsSection.style.marginBottom = '20px';
    reagentsSection.innerHTML = '<h4 style="margin: 10px 0; color: #495057; font-size: 14px;">💉 Реагенты</h4>';

    Object.keys(window.reagentsData.reagentColors).forEach(name => {
        const div = document.createElement('div');
        div.style.marginBottom = '10px';
        div.innerHTML = `
      <label style="display: flex; align-items: center; gap: 10px;">
        <span style="min-width: 120px; font-weight: 500;">${name}:</span>
        <input type="color" value="${window.reagentsData.reagentColors[name]}" 
               data-reagent="${name}" class="reagent-color-input"
               style="width: 60px; height: 30px; cursor: pointer; border: 1px solid #dee2e6; border-radius: 4px;">
      </label>
    `;
        reagentsSection.appendChild(div);
    });
    container.appendChild(reagentsSection);

    const eventsSection = document.createElement('div');
    eventsSection.innerHTML = '<h4 style="margin: 10px 0; color: #495057; font-size: 14px;">📌 События</h4>';

    Object.keys(window.reagentsData.eventColors).forEach(name => {
        const div = document.createElement('div');
        div.style.marginBottom = '10px';
        div.innerHTML = `
      <label style="display: flex; align-items: center; gap: 10px;">
        <span style="min-width: 120px; font-weight: 500;">${EVENT_TRANSLATIONS[name] || name}:</span>
        <input type="color" value="${window.reagentsData.eventColors[name]}" 
               data-event="${name}" class="event-color-input"
               style="width: 60px; height: 30px; cursor: pointer; border: 1px solid #dee2e6; border-radius: 4px;">
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
        newReagentColors[input.dataset.reagent] = input.value;
    });

    eventInputs.forEach(input => {
        newEventColors[input.dataset.event] = input.value;
    });

    localStorage.setItem('reagentColors', JSON.stringify(newReagentColors));
    localStorage.setItem('eventColors', JSON.stringify(newEventColors));

    Object.assign(window.reagentsData.reagentColors, newReagentColors);
    Object.assign(window.reagentsData.eventColors, newEventColors);

    if (window.timelineChart) {
        window.timelineChart.destroy();
    }

    createTimelineChart();
    updateLegends();

    closeColorSettings();
    alert('Цвета сохранены!');
}

// ==========================================
// ЛЕГЕНДЫ (ОБНОВЛЕННАЯ ВЕРСИЯ)
// ==========================================
function updateLegends() {
    const injections = window.reagentsData.timelineInjections || [];
    const events = window.reagentsData.timelineEvents || [];
    const wellStatuses = window.reagentsData.wellStatuses || {};
    const statusColors = window.reagentsData.statusColors || {};

    console.log('Updating legends...');

    // Реагенты
    const legendReagents = document.getElementById('legend_reagents');
    if (legendReagents) {
        legendReagents.innerHTML = '';
        Object.keys(window.reagentsData.reagentColors).forEach(reagent => {
            const item = createLegendItem(reagent, window.reagentsData.reagentColors[reagent], 'reagent-checkbox', true);
            legendReagents.appendChild(item);
        });
    }

    // События
    const legendEvents = document.getElementById('legend_events');
    if (legendEvents) {
        legendEvents.innerHTML = '';
        const allEventTypes = new Set();
        events.forEach(ev => allEventTypes.add(ev.type));

        Array.from(allEventTypes).sort().forEach(type => {
            const item = createLegendItem(
                EVENT_TRANSLATIONS[type] || type,
                window.reagentsData.eventColors[type] || '#999',
                'event-checkbox',
                false,
                type
            );
            legendEvents.appendChild(item);
        });
    }

    // Статусы - ВАЖНОЕ ИЗМЕНЕНИЕ: по умолчанию только 3 статуса выбраны
    const legendStatuses = document.getElementById('legend_statuses');
    if (legendStatuses) {
        legendStatuses.innerHTML = '';
        const statuses = [
            { code: 'Наблюдение', defaultChecked: true },
            { code: 'Адаптация', defaultChecked: true },
            { code: 'Оптимизация', defaultChecked: true },
            { code: 'Освоение', defaultChecked: false },
            { code: 'Не обслуживается', defaultChecked: false },
            { code: 'Простой', defaultChecked: false },
            { code: 'Другое', defaultChecked: false }
        ];

        statuses.forEach(status => {
            const item = createLegendItem(
                status.code,
                null,
                'status-checkbox',
                false,
                status.code,
                status.defaultChecked
            );
            legendStatuses.appendChild(item);
        });
    }

    // Скважины
    const legendWells = document.getElementById('legend_wells');
    if (legendWells) {
        legendWells.innerHTML = '';
        const allWells = new Set();
        injections.forEach(inj => allWells.add(inj.well));
        events.forEach(ev => allWells.add(ev.well));

        Array.from(allWells).sort((a, b) => {
            const numA = parseInt(a);
            const numB = parseInt(b);
            return !isNaN(numA) && !isNaN(numB) ? numA - numB : a.localeCompare(b);
        }).forEach(well => {
            const status = wellStatuses[well];
            const statusColor = statusColors[status] || '#6c757d';
            const item = createWellLegendItem(well, statusColor, status);
            legendWells.appendChild(item);
        });
    }
}

function createLegendItem(label, color, checkboxClass, isCircle, value, defaultChecked = true) {
    const item = document.createElement('div');
    item.style.cssText = 'display: flex; align-items: center; margin-bottom: 1px; padding: 3px 8px; border-radius: 4px; transition: background 0.2s;';
    item.onmouseover = () => item.style.background = '#f8f9fa';
    item.onmouseout = () => item.style.background = 'transparent';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = checkboxClass;
    checkbox.value = value || label;
    checkbox.checked = defaultChecked;
    checkbox.style.marginRight = '8px';
    checkbox.style.cursor = 'pointer';
    checkbox.onchange = () => createTimelineChart();

    if (color) {
        const colorBox = document.createElement('div');
        colorBox.style.cssText = `
      width: ${isCircle ? '14px' : '4px'};
      height: ${isCircle ? '14px' : '20px'};
      background-color: ${color};
      border-radius: ${isCircle ? '50%' : '2px'};
      margin-right: ${isCircle ? '8px' : '10px'};
      ${isCircle ? 'border: 2px solid #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.2);' : ''}
    `;
        item.appendChild(checkbox);
        item.appendChild(colorBox);
    } else {
        item.appendChild(checkbox);
    }

    const span = document.createElement('span');
    span.textContent = label;
    span.style.fontSize = '13px';
    span.style.fontWeight = '500';
    span.style.cursor = 'pointer';
    span.onclick = () => {
        checkbox.checked = !checkbox.checked;
        createTimelineChart();
    };
    item.appendChild(span);

    return item;
}

function createWellLegendItem(well, statusColor, status) {
    const item = document.createElement('div');
    item.style.cssText = 'display: flex; align-items: center; padding: 2px 6px; border-radius: 4px; transition: background 0.2s;';
    item.onmouseover = () => item.style.background = '#f8f9fa';
    item.onmouseout = () => item.style.background = 'transparent';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'well-checkbox';
    checkbox.value = well;
    checkbox.checked = true;
    checkbox.style.marginRight = '6px';
    checkbox.style.cursor = 'pointer';
    checkbox.onchange = () => createTimelineChart();

    const statusMarker = document.createElement('div');
    statusMarker.style.cssText = `
    width: 8px; height: 8px; background-color: ${statusColor}; border-radius: 50%;
    margin-right: 6px; border: 1px solid #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.2);
  `;
    statusMarker.title = status || 'Статус не определен';

    const label = document.createElement('span');
    label.textContent = well;
    label.style.fontSize = '12px';
    label.style.fontWeight = '500';
    label.style.cursor = 'pointer';
    label.onclick = () => {
        checkbox.checked = !checkbox.checked;
        createTimelineChart();
    };

    item.appendChild(checkbox);
    item.appendChild(statusMarker);
    item.appendChild(label);
    return item;
}

function selectAllReagents(checked) {
    document.querySelectorAll('.reagent-checkbox').forEach(cb => cb.checked = checked);
    createTimelineChart();
}

function selectAllEvents(checked) {
    document.querySelectorAll('.event-checkbox').forEach(cb => cb.checked = checked);
    createTimelineChart();
}

function selectAllWellsTimeline(checked) {
    document.querySelectorAll('.well-checkbox').forEach(cb => cb.checked = checked);
    createTimelineChart();
}

function selectAllStatuses(checked) {
    document.querySelectorAll('.status-checkbox').forEach(cb => cb.checked = checked);
    createTimelineChart();
}

// ==========================================
// СОЗДАНИЕ ГРАФИКА (ИСПРАВЛЕННАЯ ВЕРСИЯ)
// ==========================================
function createTimelineChart() {
    console.log('Creating timeline chart...');

    const ctxTimeline = document.getElementById('chart_timeline');
    if (!ctxTimeline) {
        console.error('Canvas element not found!');
        return;
    }

    const injections = window.reagentsData.timelineInjections || [];
    const events = window.reagentsData.timelineEvents || [];
    const reagentColors = window.reagentsData.reagentColors || {};
    const eventColors = window.reagentsData.eventColors || {};
    const wellStatuses = window.reagentsData.wellStatuses || {};

    console.log('Data for chart:', {
        injections: injections.length,
        events: events.length,
        filterDateFrom: window.filterDateFrom,
        filterDateTo: window.filterDateTo,
        wellStatusesCount: Object.keys(wellStatuses).length
    });

    // ФИЛЬТРАЦИЯ ПО ДАТАМ
    let injectionsFiltered = injections;
    let eventsFiltered = events;

    if (window.filterDateFrom && window.filterDateTo) {
        console.log('Filtering by date:', window.filterDateFrom, 'to', window.filterDateTo);

        injectionsFiltered = injections.filter(inj => {
            if (!inj.t) return false;
            try {
                const eventDate = new Date(inj.t);
                return eventDate >= window.filterDateFrom && eventDate <= window.filterDateTo;
            } catch (e) {
                console.error('Error parsing injection date:', inj.t, e);
                return false;
            }
        });

        eventsFiltered = events.filter(ev => {
            if (!ev.t) return false;
            try {
                const eventDate = new Date(ev.t);
                return eventDate >= window.filterDateFrom && eventDate <= window.filterDateTo;
            } catch (e) {
                console.error('Error parsing event date:', ev.t, e);
                return false;
            }
        });
    } else {
        console.log('No date filters applied');
    }

    const selectedReagents = Array.from(document.querySelectorAll('.reagent-checkbox:checked')).map(cb => cb.value);
    const selectedEvents = Array.from(document.querySelectorAll('.event-checkbox:checked')).map(cb => cb.value);
    const selectedStatuses = Array.from(document.querySelectorAll('.status-checkbox:checked')).map(cb => cb.value);
    const manuallySelectedWells = Array.from(document.querySelectorAll('.well-checkbox:checked')).map(cb => cb.value);

    console.log('Selected filters:', {
        reagents: selectedReagents,
        events: selectedEvents,
        statuses: selectedStatuses,
        wells: manuallySelectedWells
    });

    // Фильтруем скважины по статусам (по умолчанию выбраны только 3 статуса)
    let wellsToShow = new Set();

    if (selectedStatuses.length > 0) {
        // Фильтруем по выбранным статусам
        Object.keys(wellStatuses).forEach(well => {
            const status = wellStatuses[well];
            if (selectedStatuses.includes(status)) {
                wellsToShow.add(well);
            }
        });
    } else {
        // Если не выбраны статусы - показываем все скважины
        Object.keys(wellStatuses).forEach(well => {
            wellsToShow.add(well);
        });
    }

    // Дополнительная фильтрация по ручному выбору скважин
    const finalWells = Array.from(wellsToShow).filter(well =>
        manuallySelectedWells.length === 0 || manuallySelectedWells.includes(well)
    );

    // Фильтруем инъекции
    let filteredInjections = injectionsFiltered.filter(inj => {
        // Проверяем реагент
        const reagentMatch = selectedReagents.length === 0 || selectedReagents.includes(inj.reagent);
        // Проверяем скважину
        const wellMatch = finalWells.includes(inj.well);
        return reagentMatch && wellMatch;
    });

    // Фильтруем события
    let filteredEvents = eventsFiltered.filter(ev => {
        // Проверяем тип события
        const eventMatch = selectedEvents.length === 0 || selectedEvents.includes(ev.type);
        // Проверяем скважину
        const wellMatch = finalWells.includes(ev.well);
        return eventMatch && wellMatch;
    });

    // Порядок сортировки статусов (меньше = выше)
    const statusOrder = {
        'Наблюдение': 1,
        'Адаптация': 2,
        'Оптимизация': 3,
        'Освоение': 4,
        'Простой': 5,
        'Не обслуживается': 6,
        'Другое': 7,
        null: 8,
        undefined: 8,
    };

    // Находим первую дату события для каждой скважины (для сортировки по дате добавления)
    const wellFirstDate = {};
    [...injections, ...events].forEach(item => {
        const well = item.well;
        const date = new Date(item.t);
        if (!wellFirstDate[well] || date < wellFirstDate[well]) {
            wellFirstDate[well] = date;
        }
    });

    // Сортируем скважины: 1) по статусу, 2) по дате первого события
    const wellsList = finalWells.sort((a, b) => {
        const statusA = wellStatuses[a];
        const statusB = wellStatuses[b];
        const orderA = statusOrder[statusA] ?? 8;
        const orderB = statusOrder[statusB] ?? 8;

        // Сначала по статусу
        if (orderA !== orderB) {
            return orderA - orderB;
        }

        // Затем по дате первого события (новые сначала)
        const dateA = wellFirstDate[a] || new Date(0);
        const dateB = wellFirstDate[b] || new Date(0);
        return dateB - dateA;  // новые сначала
    });

    console.log('Final data for chart:', {
        wells: wellsList,
        wellsCount: wellsList.length,
        filteredInjections: filteredInjections.length,
        filteredEvents: filteredEvents.length
    });

    // Если нет скважин для отображения
    if (wellsList.length === 0) {
        console.warn('No wells to display!');
        if (window.timelineChart) {
            window.timelineChart.destroy();
            window.timelineChart = null;
        }
        const ctx = ctxTimeline.getContext('2d');
        ctx.clearRect(0, 0, ctxTimeline.width, ctxTimeline.height);
        ctx.font = '16px Arial';
        ctx.fillStyle = '#999';
        ctx.textAlign = 'center';
        ctx.fillText('Нет данных для отображения', ctxTimeline.width / 2, ctxTimeline.height / 2);
        return;
    }

    // Создаем карту скважин -> Y координата
    const wellToY = {};
    wellsList.forEach((well, idx) => {
        wellToY[well] = idx;
    });

    // Группируем инъекции по реагенту
    const injectionsGrouped = {};
    filteredInjections.forEach(inj => {
        const reagent = inj.reagent || 'Неизвестно';
        if (!injectionsGrouped[reagent]) {
            injectionsGrouped[reagent] = [];
        }
        injectionsGrouped[reagent].push({
            x: inj.t,
            y: wellToY[inj.well] ?? 0,
            qty: inj.qty,
            well: inj.well,
            reagent: reagent,
            description: inj.description,
            username: inj.username,        // ← ДОДАНО
            geo_status: inj.geo_status     // ← ДОДАНО
        });
    });

    // Datasets для реагентов (точки)
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

    // Группируем события по типу
    const eventsGrouped = {};
    filteredEvents.forEach(ev => {
        const type = ev.type || 'other';
        if (!eventsGrouped[type]) {
            eventsGrouped[type] = [];
        }

        const yCenter = wellToY[ev.well] ?? 0;
        eventsGrouped[type].push({
            x: ev.t,
            y: [yCenter - 0.3, yCenter + 0.3],
            well: ev.well,
            type: type,
            description: ev.description,
            p_tube: ev.p_tube,
            p_line: ev.p_line,
            username: ev.username,
            geo_status: ev.geo_status,
            purge_phase: ev.purge_phase   // фаза продувки: start/press/stop
        });
    });

    // Datasets для событий (вертикальные линии)
    const eventsDatasets = Object.keys(eventsGrouped).map(type => ({
        label: `📌 ${EVENT_TRANSLATIONS[type] || type}`,
        type: 'bar',
        data: eventsGrouped[type],
        backgroundColor: eventColors[type] || '#999',
        borderColor: eventColors[type] || '#999',
        borderWidth: 0,
        barThickness: 2,
        barPercentage: 1.0,
        categoryPercentage: 1.0,
    }));

    const allDatasets = [...injectionsDatasets, ...eventsDatasets];

    // Уничтожаем старый график если есть
    if (window.timelineChart) {
        window.timelineChart.destroy();
    }

    // Сохраняем список скважин для плагина statusStripes
    window.currentWellsList = wellsList;

    // Вычисляем интервалы продувки для отрисовки (используем все события, не только отфильтрованные)
    // Фильтруем только по скважинам, чтобы интервалы всегда отображались
    const purgeEventsForIntervals = eventsFiltered.filter(ev =>
        ev.type === 'purge' && ev.purge_phase && finalWells.includes(ev.well)
    );
    window.purgeIntervals = calculatePurgeIntervals(purgeEventsForIntervals, wellToY);

    // Создаем новый график
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
                    // Скрыть стандартный тултип когда курсор над интервалом продувки
                    filter: function(tooltipItem) {
                        return window.hoveredPurgeInterval === null;
                    },
                    backgroundColor: 'rgba(33, 37, 41, 0.96)',
                    padding: 14,
                    titleFont: { size: 13, weight: '600' },
                    bodyFont: { size: 12 },
                    borderColor: 'rgba(222, 226, 230, 0.8)',
                    borderWidth: 1,
                    cornerRadius: 6,
                    displayColors: true,
                    callbacks: {
                        title: (items) => {
                            if (items.length === 0) return '';
                            const item = items[0];
                            const raw = item.raw;
                            const timeValue = raw.x || item.parsed.x;
                            const date = new Date(timeValue);
                            return '🕒 ' + date.toLocaleString('ru-RU', {
                                day: '2-digit',
                                month: 'short',
                                year: 'numeric',
                                hour: '2-digit',
                                minute: '2-digit'
                            });
                        },
                        label: (ctx) => {
                            const raw = ctx.raw;
                            const lines = [];

                            if (raw.qty !== undefined) {
                                // Реагент (injection)
                                lines.push(`🎯 Скважина: ${raw.well}`);
                                if (raw.reagent) {
                                    lines.push(`🧪 Тип: ${raw.reagent}`);
                                }
                                lines.push(`💉 Количество: ${raw.qty || 'N/A'}`);
                                if (raw.description) {
                                    lines.push(`📝 ${raw.description}`);
                                }
                                // ДОДАНО: Оператор та геостатус
                                if (raw.username) {
                                    lines.push(`👤 Оператор: ${raw.username}`);
                                }
                                if (raw.geo_status) {
                                    lines.push(`📍 Геостатус: ${raw.geo_status}`);
                                }
                            } else {
                                // Событие (event)
                                lines.push(`🎯 Скважина: ${raw.well}`);
                                lines.push(`📋 Тип: ${EVENT_TRANSLATIONS[raw.type] || raw.type}`);

                                // Для продувки показываем фазу с расшифровкой
                                if (raw.type === 'purge' && raw.purge_phase) {
                                    const phaseLabels = {
                                        'start': 'Начало продувки',
                                        'press': 'Набор давления',
                                        'stop': 'Пуск скважины в линию'
                                    };
                                    const phaseLabel = phaseLabels[raw.purge_phase] || raw.purge_phase;
                                    lines.push(`🔄 Фаза: ${phaseLabel}`);
                                }

                                if (raw.p_tube !== undefined && raw.p_tube !== null) {
                                    lines.push(`📊 P трубное: ${raw.p_tube}`);
                                }
                                if (raw.p_line !== undefined && raw.p_line !== null) {
                                    lines.push(`📊 P шлейфное: ${raw.p_line}`);
                                }
                                // Описание всегда показываем если есть
                                if (raw.description) {
                                    lines.push(`📝 ${raw.description}`);
                                }
                                if (raw.username) {
                                    lines.push(`👤 Оператор: ${raw.username}`);
                                }
                                if (raw.geo_status) {
                                    lines.push(`📍 Геостатус: ${raw.geo_status}`);
                                }
                            }

                            return lines;
                        }
                    }
                },
                zoom: {
                    pan: {
                        enabled: true,
                        mode: 'x',
                        modifierKey: 'shift',
                    },
                    zoom: {
                        wheel: {
                            enabled: false,
                        },
                        drag: {
                            enabled: true,
                            backgroundColor: 'rgba(13, 110, 253, 0.1)',
                            borderColor: '#0d6efd',
                            borderWidth: 1,
                        },
                        mode: 'x',
                    },
                    limits: {
                        x: {min: 'original', max: 'original'},
                    },
                }
            },
            scales: {
                x: {
                    type: 'time',
                    time: {
                        displayFormats: {
                            hour: 'HH:mm',
                            day: 'dd.MM',
                        },
                        tooltipFormat: 'dd.MM.yyyy HH:mm (cccc)',
                    },
                    beforeBuildTicks(axis) {
                        const totalSpan = axis.max - axis.min;
                        if (totalSpan < 86400000 * 1.5) {
                            axis.options.time.unit = 'hour';
                        } else {
                            axis.options.time.unit = 'day';
                        }
                    },
                    afterBuildTicks(axis) {
                        const totalSpan = axis.max - axis.min;
                        if (totalSpan >= 86400000 * 1.5) {
                            // Дневной режим: принудительно тики на полуночах
                            const newTicks = [];
                            let d = luxon.DateTime.fromMillis(axis.min).startOf('day');
                            const endMs = axis.max;
                            while (d.toMillis() <= endMs) {
                                newTicks.push({ value: d.toMillis() });
                                d = d.plus({ days: 1 });
                            }
                            axis.ticks = newTicks;
                        }
                    },
                    ticks: {
                        autoSkip: false,
                        maxRotation: 0,
                        minRotation: 0,
                        font: { size: 12, weight: '600' },
                        color: '#1e293b',
                        callback: function(value, index, ticks) {
                            const dt = luxon.DateTime.fromMillis(value);
                            const totalSpan = ticks.length >= 2
                                ? Math.abs(ticks[ticks.length - 1].value - ticks[0].value)
                                : Infinity;
                            if (totalSpan < 86400000 * 1.5) {
                                const h = dt.toFormat('HH:mm');
                                if (dt.hour === 0 && dt.minute === 0) {
                                    return [dt.toFormat('dd.MM'), h];
                                }
                                return h;
                            }
                            return [dt.toFormat('dd.MM'), dt.setLocale('ru').toFormat('ccc')];
                        }
                    },
                    title: {
                        display: true,
                        text: 'Дата / время',
                        font: {
                            size: 13,
                            weight: '600'
                        }
                    },
                    grid: {
                        color: 'rgba(0, 0, 0, 0.15)',
                        lineWidth: 1,
                    }
                },
                y: {
                    type: 'linear',
                    min: -0.5,
                    max: wellsList.length - 0.5,
                    ticks: {
                        stepSize: 1,
                        callback: (value) => {
                            const well = wellsList[Math.round(value)];
                            return well ? `Скв ${well}` : '';
                        },
                        font: {
                            size: 11,
                            weight: '500'
                        }
                    },
                    title: {
                        display: true,
                        text: 'Скважины',
                        font: {
                            size: 13,
                            weight: '600'
                        }
                    },
                    grid: {
                        color: (context) => {
                            if (!window.showGridLines) {
                                return context.tick.value % 1 === 0 ? 'rgba(0, 0, 0, 0.08)' : 'transparent';
                            }
                            return 'rgba(0, 0, 0, 0.1)';
                        },
                        lineWidth: 1
                    }
                }
            }
        }
    });

    console.log('Timeline chart created successfully!');
}

// Закрытие модального окна при клике вне его
window.addEventListener('click', function(event) {
    const modal = document.getElementById('colorModal');
    if (event.target === modal) {
        closeColorSettings();
    }
});

// Функция для отладки
window.debugTimeline = function() {
    console.log('=== TIMELINE DEBUG INFO ===');
    console.log('Current period:', window.currentPeriod);
    console.log('Filter date from:', window.filterDateFrom);
    console.log('Filter date to:', window.filterDateTo);
    console.log('Show grid lines:', window.showGridLines);
    console.log('Chart instance:', window.timelineChart ? 'Exists' : 'Null');

    if (window.reagentsData) {
        console.log('Data counts:', {
            injections: window.reagentsData.timelineInjections?.length || 0,
            events: window.reagentsData.timelineEvents?.length || 0,
            reagentColors: Object.keys(window.reagentsData.reagentColors || {}).length,
            eventColors: Object.keys(window.reagentsData.eventColors || {}).length
        });
    }

    const selectedReagents = Array.from(document.querySelectorAll('.reagent-checkbox:checked')).map(cb => cb.value);
    const selectedEvents = Array.from(document.querySelectorAll('.event-checkbox:checked')).map(cb => cb.value);
    const selectedStatuses = Array.from(document.querySelectorAll('.status-checkbox:checked')).map(cb => cb.value);
    const selectedWells = Array.from(document.querySelectorAll('.well-checkbox:checked')).map(cb => cb.value);

    console.log('Selected filters:', {
        reagents: selectedReagents,
        events: selectedEvents,
        statuses: selectedStatuses,
        wells: selectedWells
    });

    console.log('URL params:', new URLSearchParams(window.location.search).toString());
    console.log('===========================');
};