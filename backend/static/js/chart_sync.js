/**
 * chart_sync.js — Глобальный координатор единого временного фильтра.
 *
 * Все графики на странице скважины используют один период.
 * При нажатии кнопки периода/интервала на ЛЮБОМ графике —
 * обновляются ВСЕ графики и статистика одновременно.
 *
 * Загружается ПЕРВЫМ — до pressure_chart.js, delta_pressure_chart.js,
 * synchronized_chart.js, well_events_chart.js.
 *
 * Зависимости: window.wellEventsData (из Jinja-шаблона)
 */
(function () {
  'use strict';

  let currentDays = 7;
  let currentInterval = 5;

  // ══════════════════ Фильтрация событий по «последние N дней» ══════════════════

  function filterEventsByDays(days) {
    var evData = window.wellEventsData;
    if (!evData) return { timelineEvents: [], timelineInjections: [] };

    // Серверное время UTC+5 (Кунград) — naive ISO строка, как и event_time.
    // new Date() интерпретирует обе строки одинаково (в TZ браузера).
    var nowMs = window.serverNowIso ? new Date(window.serverNowIso).getTime() : Date.now();
    var cutoffMs = nowMs - days * 24 * 60 * 60 * 1000;
    console.log('[chart_sync] filterEventsByDays:', days, 'days, cutoff:', new Date(cutoffMs).toISOString(),
      'serverNowIso:', window.serverNowIso || 'N/A');

    return {
      timelineEvents: (evData.timelineEvents || []).filter(function (ev) {
        if (!ev.t) return false;
        return new Date(ev.t).getTime() >= cutoffMs;
      }),
      timelineInjections: (evData.timelineInjections || []).filter(function (inj) {
        if (!inj.t) return false;
        return new Date(inj.t).getTime() >= cutoffMs;
      }),
    };
  }

  // ══════════════════ Пересчёт статистики в DOM ══════════════════

  function recalcStats(filteredEvents, filteredInjections) {
    // Считаем по типам
    var reagentCount = filteredInjections.length;
    var totalQty = 0;
    filteredInjections.forEach(function (inj) {
      totalQty += parseFloat(inj.qty) || 0;
    });

    var purgeCount = 0;
    var pressureCount = 0;
    filteredEvents.forEach(function (ev) {
      var type = (ev.type || 'other').toLowerCase();
      if (type === 'purge') purgeCount++;
      else if (type === 'pressure') pressureCount++;
    });

    // Обновляем DOM-карточки
    var el;
    el = document.getElementById('stat-reagent-count');
    if (el) el.textContent = reagentCount;

    el = document.getElementById('stat-reagent-qty');
    if (el) el.textContent = totalQty.toFixed(2);

    el = document.getElementById('stat-purge-count');
    if (el) el.textContent = purgeCount;

    el = document.getElementById('stat-pressure-count');
    if (el) el.textContent = pressureCount;

    // Период
    el = document.getElementById('stat-period');
    if (el) {
      var nowMs = window.serverNowIso ? new Date(window.serverNowIso).getTime() : Date.now();
      var now = new Date(nowMs);
      var from = new Date(nowMs - currentDays * 24 * 60 * 60 * 1000);
      var pad = function (n) { return String(n).padStart(2, '0'); };
      el.textContent = 'Период: ' +
        pad(from.getDate()) + '.' + pad(from.getMonth() + 1) + '.' + from.getFullYear() +
        ' — ' +
        pad(now.getDate()) + '.' + pad(now.getMonth() + 1) + '.' + now.getFullYear();
    }
  }

  // ══════════════════ Синхронизация подсветки кнопок ══════════════════

  function syncButtonHighlights(days, interval) {
    // Все группы кнопок периода
    ['pressure-days', 'sync-days'].forEach(function (prefix) {
      document.querySelectorAll('[data-' + prefix + ']').forEach(function (btn) {
        var val = parseInt(btn.dataset[prefix.replace('-', '')  // pressureDays / syncDays
          .replace(/^(.)/, function (m) { return m; })] ||
          btn.getAttribute('data-' + prefix));
        // Проще: читаем атрибут напрямую
        val = parseInt(btn.getAttribute('data-' + prefix));

        if (val === days) {
          btn.style.background = prefix === 'pressure-days' ? '#0d6efd' : '#1565c0';
          btn.style.color = 'white';
          btn.style.borderColor = prefix === 'pressure-days' ? '#0d6efd' : '#1565c0';
        } else {
          btn.style.background = 'white';
          btn.style.color = '#333';
          btn.style.borderColor = '#dee2e6';
        }
      });
    });

    // Все группы кнопок интервала
    if (interval !== undefined) {
      ['pressure-interval', 'sync-interval'].forEach(function (prefix) {
        document.querySelectorAll('[data-' + prefix + ']').forEach(function (btn) {
          var val = parseInt(btn.getAttribute('data-' + prefix));
          if (val === interval) {
            btn.style.background = prefix === 'pressure-interval' ? '#0d6efd' : '#1565c0';
            btn.style.color = 'white';
            btn.style.borderColor = prefix === 'pressure-interval' ? '#0d6efd' : '#1565c0';
          } else {
            btn.style.background = 'white';
            btn.style.color = '#333';
            btn.style.borderColor = '#dee2e6';
          }
        });
      });
    }
  }

  // ══════════════════ Главная функция: обновить ВСЕ графики ══════════════════

  window.updateAllCharts = function (days, interval) {
    if (days !== undefined) currentDays = days;
    if (interval !== undefined) currentInterval = interval;

    // 1) Фильтруем события
    var filtered = filterEventsByDays(currentDays);

    // 2) Сохраняем для использования графиками
    window.wellEventsFiltered = {
      timelineEvents: filtered.timelineEvents,
      timelineInjections: filtered.timelineInjections,
      reagentColors: (window.wellEventsData || {}).reagentColors || {},
      eventColors: (window.wellEventsData || {}).eventColors || {},
      wellNumber: (window.wellEventsData || {}).wellNumber || '',
    };

    // 3) Пересчёт статистики
    recalcStats(filtered.timelineEvents, filtered.timelineInjections);

    // 4) Обновляем каждый график (если экспортирован)
    if (window.pressureChartReload) {
      window.pressureChartReload(currentDays, currentInterval);
    }

    if (window.deltaChart && window.deltaChart.reload) {
      window.deltaChart.reload(currentDays, currentInterval);
    }

    if (window.syncChartReload) {
      window.syncChartReload(currentDays, currentInterval);
    }

    if (window.eventsChartReload) {
      window.eventsChartReload();
    }

    // 5) Синхронизация подсветки кнопок
    syncButtonHighlights(currentDays, currentInterval);
  };

  // Текущее состояние
  window.chartSyncState = {
    getDays: function () { return currentDays; },
    getInterval: function () { return currentInterval; },
  };

  // ══════════════════ Начальная фильтрация при загрузке ══════════════════
  // Фильтруем сразу, чтобы графики при первом loadChart() получили правильный набор.
  (function initialFilter() {
    var filtered = filterEventsByDays(currentDays);
    window.wellEventsFiltered = {
      timelineEvents: filtered.timelineEvents,
      timelineInjections: filtered.timelineInjections,
      reagentColors: (window.wellEventsData || {}).reagentColors || {},
      eventColors: (window.wellEventsData || {}).eventColors || {},
      wellNumber: (window.wellEventsData || {}).wellNumber || '',
    };
    recalcStats(filtered.timelineEvents, filtered.timelineInjections);
    syncButtonHighlights(currentDays, currentInterval);
    console.log('[chart_sync] initial filter: ', currentDays, 'days →',
      filtered.timelineEvents.length, 'events,',
      filtered.timelineInjections.length, 'injections');
  })();

})();
