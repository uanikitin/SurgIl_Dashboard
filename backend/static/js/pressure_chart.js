/**
 * pressure_chart.js — График давлений скважины (Chart.js)
 *
 * Получает данные через /api/pressure/chart/{well_id}?days=N&interval=M
 * Отрисовывает линейный график с двумя линиями: Ptr (устье) и Pshl (шлейф)
 * + область min-max как заливка.
 *
 * Поддерживает:
 *   - Выбор интервала агрегации: 5, 10, 15, 30, 60 минут
 *   - Фильтры: убрать нули, сгладить спайки, заполнить пропуски
 *
 * Используется на странице well.html
 */

(function () {
  'use strict';

  const canvasId = 'chart_pressure';
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const wellId = canvas.dataset.wellId;
  if (!wellId) return;

  let pressureChart = null;
  let currentDays = 7;
  let currentInterval = 5;   // По умолчанию 5 минут
  let defaultYMax = null;     // Авто-вычисляется из данных

  // Цвета
  const COLORS = {
    tube:     '#e53935',   // красный — давление устья
    tubeFill: 'rgba(229, 57, 53, 0.08)',
    line:     '#1e88e5',   // синий — давление шлейфа
    lineFill: 'rgba(30, 136, 229, 0.08)',
    grid:     '#e9ecef',
  };

  /**
   * Собирает параметры фильтрации из UI-контролов
   */
  function getFilterParams() {
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
   * Отображает статистику фильтрации
   */
  function showFilterStats(filterStats) {
    const el = document.getElementById('filter-stats');
    if (!el) return;

    if (!filterStats) {
      el.textContent = '';
      return;
    }

    const parts = [];
    if (filterStats.zeros_removed > 0) parts.push(`нулей: ${filterStats.zeros_removed}`);
    if (filterStats.spikes_detected > 0) parts.push(`спайков: ${filterStats.spikes_detected}`);
    if (filterStats.gaps_filled > 0) parts.push(`пропусков: ${filterStats.gaps_filled}`);

    if (parts.length > 0) {
      el.textContent = `Фильтрация: ${parts.join(', ')} (из ${filterStats.total_points} точек)`;
      el.style.color = '#e65100';
    } else {
      el.textContent = '';
    }
  }

  /**
   * Загружает данные и строит/обновляет график
   */
  async function loadChart(days, interval) {
    if (days !== undefined) currentDays = days;
    if (interval !== undefined) currentInterval = interval;

    try {
      const filterParams = getFilterParams();
      const resp = await fetch(`/api/pressure/chart/${wellId}?days=${currentDays}&interval=${currentInterval}${filterParams}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json = await resp.json();
      console.log(`[pressure_chart] well=${wellId} days=${currentDays} interval=${currentInterval}min points=${json.points?.length || 0}` +
        (json.filter_stats ? ` filters: zeros=${json.filter_stats.zeros_removed} spikes=${json.filter_stats.spikes_detected} filled=${json.filter_stats.gaps_filled}` : ''));
      renderChart(json.points);
      initYSlider(json.points);
      showFilterStats(json.filter_stats || null);
    } catch (err) {
      console.error('Pressure chart error:', err);
      canvas.parentElement.innerHTML =
        '<div style="padding:40px;text-align:center;color:#999;">Нет данных давлений</div>';
    }
  }

  /**
   * Отрисовка графика
   */
  function renderChart(points) {
    if (!points || points.length === 0) {
      canvas.parentElement.innerHTML =
        '<div style="padding:40px;text-align:center;color:#999;">Нет данных давлений за выбранный период</div>';
      return;
    }

    // Данные для datasets
    const tubeAvg = [], tubeMin = [], tubeMax = [];
    const lineAvg = [], lineMin = [], lineMax = [];

    for (const p of points) {
      const t = p.t;
      if (p.p_tube_avg !== null) {
        tubeAvg.push({ x: t, y: p.p_tube_avg });
        tubeMin.push({ x: t, y: p.p_tube_min });
        tubeMax.push({ x: t, y: p.p_tube_max });
      }
      if (p.p_line_avg !== null) {
        lineAvg.push({ x: t, y: p.p_line_avg });
        lineMin.push({ x: t, y: p.p_line_min });
        lineMax.push({ x: t, y: p.p_line_max });
      }
    }

    const datasets = [];

    // Область min-max для Ptr
    if (tubeMin.length > 0) {
      datasets.push({
        label: 'Ptr диапазон',
        data: tubeMax,
        fill: '-1',
        backgroundColor: COLORS.tubeFill,
        borderColor: 'transparent',
        pointRadius: 0,
        tension: 0.3,
        order: 3,
      });
      datasets.push({
        label: 'Ptr min',
        data: tubeMin,
        borderColor: 'transparent',
        pointRadius: 0,
        tension: 0.3,
        order: 4,
        hidden: false,
      });
    }

    // Линия Ptr avg
    if (tubeAvg.length > 0) {
      datasets.push({
        label: 'Ptr (устье)',
        data: tubeAvg,
        borderColor: COLORS.tube,
        backgroundColor: COLORS.tube,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.3,
        order: 1,
      });
    }

    // Область min-max для Pshl
    if (lineMin.length > 0) {
      datasets.push({
        label: 'Pshl диапазон',
        data: lineMax,
        fill: '-1',
        backgroundColor: COLORS.lineFill,
        borderColor: 'transparent',
        pointRadius: 0,
        tension: 0.3,
        order: 5,
      });
      datasets.push({
        label: 'Pshl min',
        data: lineMin,
        borderColor: 'transparent',
        pointRadius: 0,
        tension: 0.3,
        order: 6,
        hidden: false,
      });
    }

    // Линия Pshl avg
    if (lineAvg.length > 0) {
      datasets.push({
        label: 'Pshl (шлейф)',
        data: lineAvg,
        borderColor: COLORS.line,
        backgroundColor: COLORS.line,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.3,
        order: 2,
      });
    }

    // Если нет ни одного dataset с данными — показать сообщение
    if (datasets.length === 0) {
      canvas.parentElement.innerHTML =
        '<div style="padding:40px;text-align:center;color:#999;">Нет данных давлений за выбранный период</div>';
      return;
    }

    if (pressureChart) {
      pressureChart.destroy();
    }

    const ctx = canvas.getContext('2d');
    pressureChart = new Chart(ctx, {
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
              filter: (item) => {
                // Скрываем служебные datasets (min, диапазон)
                return !item.text.includes('min') && !item.text.includes('диапазон');
              },
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
                const dd = String(d.getUTCDate()).padStart(2,'0');
                const mm = String(d.getUTCMonth()+1).padStart(2,'0');
                const yy = d.getUTCFullYear();
                const hh = String(d.getUTCHours()).padStart(2,'0');
                const mi = String(d.getUTCMinutes()).padStart(2,'0');
                return `${dd}.${mm}.${yy} ${hh}:${mi} (Кунград, UTC+5)`;
              },
              label: function(ctx) {
                if (ctx.dataset.label.includes('min') || ctx.dataset.label.includes('диапазон')) {
                  return null;
                }
                return `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)} атм`;
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
              text: 'Давление (атм)',
              font: { size: 12, weight: 'bold' },
            },
            grid: { color: COLORS.grid },
            ticks: { font: { size: 11 } },
          },
        },
      },
    });
  }

  // ── Ползунок масштаба Y ──
  const ySlider = document.getElementById('pressure-y-slider');
  const yLabel  = document.getElementById('pressure-y-label');

  function initYSlider(points) {
    if (!ySlider) return;
    // Вычисляем макс из данных
    let dataMax = 0;
    for (const p of points) {
      if (p.p_tube_max !== null && p.p_tube_max > dataMax) dataMax = p.p_tube_max;
      if (p.p_line_max !== null && p.p_line_max > dataMax) dataMax = p.p_line_max;
    }
    defaultYMax = Math.ceil(dataMax + 2); // +2 атм запас, округление вверх
    ySlider.max = Math.max(defaultYMax * 2, 100);
    ySlider.value = defaultYMax;
    if (yLabel) yLabel.textContent = defaultYMax;
  }

  function applyYSlider() {
    if (!pressureChart || !ySlider) return;
    const val = parseFloat(ySlider.value);
    pressureChart.options.scales.y.max = val;
    pressureChart.update('none');
    if (yLabel) yLabel.textContent = val;
  }

  function resetYSlider() {
    if (!ySlider || !defaultYMax) return;
    ySlider.value = defaultYMax;
    if (yLabel) yLabel.textContent = defaultYMax;
    if (pressureChart) {
      delete pressureChart.options.scales.y.max;
      pressureChart.update('none');
    }
  }

  if (ySlider) {
    ySlider.addEventListener('input', applyYSlider);
  }

  // ── Кнопки выбора периода ──
  function setActiveBtn(group, activeBtn) {
    document.querySelectorAll(`[data-${group}]`).forEach(b => {
      b.style.background = 'white';
      b.style.color = '#333';
      b.style.borderColor = '#dee2e6';
    });
    activeBtn.style.background = '#0d6efd';
    activeBtn.style.color = 'white';
    activeBtn.style.borderColor = '#0d6efd';
  }

  document.querySelectorAll('[data-pressure-days]').forEach(btn => {
    btn.addEventListener('click', function () {
      setActiveBtn('pressure-days', this);
      loadChart(parseInt(this.dataset.pressureDays), undefined);
    });
  });

  // ── Кнопки выбора интервала ──
  document.querySelectorAll('[data-pressure-interval]').forEach(btn => {
    btn.addEventListener('click', function () {
      setActiveBtn('pressure-interval', this);
      loadChart(undefined, parseInt(this.dataset.pressureInterval));
    });
  });

  // Reset zoom + Y slider
  const resetBtn = document.getElementById('pressure-reset-zoom');
  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      resetYSlider();
      if (pressureChart) pressureChart.resetZoom();
    });
  }

  // ── Обработчики фильтров (общие для обоих графиков) ──
  ['filter-zeros', 'filter-spikes'].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('change', () => {
        loadChart();
        // Также перезагружаем ΔP график если он есть
        if (window.deltaChart && window.deltaChart.reload) {
          window.deltaChart.reload();
        }
      });
    }
  });

  const fillModeEl = document.getElementById('filter-fill-mode');
  if (fillModeEl) {
    fillModeEl.addEventListener('change', () => {
      loadChart();
      if (window.deltaChart && window.deltaChart.reload) {
        window.deltaChart.reload();
      }
    });
  }

  const maxGapEl = document.getElementById('filter-max-gap');
  if (maxGapEl) {
    maxGapEl.addEventListener('change', () => {
      loadChart();
      if (window.deltaChart && window.deltaChart.reload) {
        window.deltaChart.reload();
      }
    });
  }

  // ── Правый клик → сырые данные из БД ──
  const rawPopup = document.getElementById('raw-data-popup');
  const rawTable = document.getElementById('raw-data-table');
  const rawClose = document.getElementById('raw-data-close');

  function hideRawPopup() {
    if (rawPopup) rawPopup.style.display = 'none';
  }

  if (rawClose) {
    rawClose.addEventListener('click', hideRawPopup);
  }

  // Клик вне попапа — закрыть
  document.addEventListener('click', function (e) {
    if (rawPopup && rawPopup.style.display !== 'none' && !rawPopup.contains(e.target)) {
      hideRawPopup();
    }
  });

  // Escape — закрыть
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') hideRawPopup();
  });

  function showRawDataPopup(rows, centerTime, mouseX, mouseY) {
    if (!rawPopup || !rawTable) return;

    // Формируем таблицу
    let html = '<thead><tr>' +
      '<th style="padding:4px 8px; border-bottom:2px solid #dee2e6; text-align:left; color:#666; font-weight:600;">#</th>' +
      '<th style="padding:4px 8px; border-bottom:2px solid #dee2e6; text-align:left; color:#666; font-weight:600;">Время (UTC+5)</th>' +
      '<th style="padding:4px 8px; border-bottom:2px solid #dee2e6; text-align:right; color:#e53935; font-weight:600;">Ptr</th>' +
      '<th style="padding:4px 8px; border-bottom:2px solid #dee2e6; text-align:right; color:#1e88e5; font-weight:600;">Pshl</th>' +
      '<th style="padding:4px 8px; border-bottom:2px solid #dee2e6; text-align:right; color:#2e7d32; font-weight:600;">ΔP</th>' +
      '</tr></thead><tbody>';

    // Находим центральную строку (ближайшую к времени клика)
    let centerIdx = 0;
    let minDiff = Infinity;
    const centerMs = new Date(centerTime).getTime();
    rows.forEach((r, i) => {
      const rowMs = new Date(r.measured_at.replace(' ', 'T')).getTime();
      const diff = Math.abs(rowMs - centerMs);
      if (diff < minDiff) { minDiff = diff; centerIdx = i; }
    });

    rows.forEach((r, i) => {
      const isCenter = (i === centerIdx);
      const bgColor = isCenter ? '#fff3e0' : (i % 2 === 0 ? '#fafafa' : 'white');
      const fontWeight = isCenter ? 'bold' : 'normal';
      const borderLeft = isCenter ? '3px solid #e65100' : '3px solid transparent';

      const ptr = r.p_tube !== null ? r.p_tube.toFixed(3) : '—';
      const pshl = r.p_line !== null ? r.p_line.toFixed(3) : '—';
      const dp = (r.p_tube !== null && r.p_line !== null)
        ? Math.max(0, r.p_tube - r.p_line).toFixed(3)
        : '—';

      // Форматируем время: dd.MM HH:mm:ss
      const parts = r.measured_at.split(' ');
      const dateParts = parts[0].split('-');
      const timeStr = parts[1] || '';
      const displayTime = `${dateParts[2]}.${dateParts[1]} ${timeStr}`;

      html += `<tr style="background:${bgColor}; border-left:${borderLeft}; font-weight:${fontWeight};">` +
        `<td style="padding:3px 8px; color:#999; font-size:10px;">${i + 1}</td>` +
        `<td style="padding:3px 8px; white-space:nowrap;">${displayTime}</td>` +
        `<td style="padding:3px 8px; text-align:right; color:#e53935;">${ptr}</td>` +
        `<td style="padding:3px 8px; text-align:right; color:#1e88e5;">${pshl}</td>` +
        `<td style="padding:3px 8px; text-align:right; color:#2e7d32;">${dp}</td>` +
        '</tr>';
    });
    html += '</tbody>';
    rawTable.innerHTML = html;

    // Позиционируем попап у курсора
    rawPopup.style.display = 'block';
    const popW = rawPopup.offsetWidth;
    const popH = rawPopup.offsetHeight;
    const winW = window.innerWidth;
    const winH = window.innerHeight;

    let left = mouseX + 12;
    let top = mouseY + 12;
    if (left + popW > winW - 10) left = mouseX - popW - 12;
    if (top + popH > winH - 10) top = mouseY - popH - 12;
    if (left < 10) left = 10;
    if (top < 10) top = 10;

    rawPopup.style.left = left + 'px';
    rawPopup.style.top = top + 'px';
  }

  canvas.addEventListener('contextmenu', async function (e) {
    e.preventDefault();
    if (!pressureChart) return;

    // Получаем время из позиции курсора на графике
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const xScale = pressureChart.scales.x;
    if (!xScale) return;

    const xValue = xScale.getValueForPixel(x);
    if (!xValue) return;

    const clickTime = new Date(xValue);
    // Время уже в UTC+5 (от API), отправляем как ISO без Z
    const pad = (n) => String(n).padStart(2, '0');
    const isoStr = `${clickTime.getUTCFullYear()}-${pad(clickTime.getUTCMonth()+1)}-${pad(clickTime.getUTCDate())}T${pad(clickTime.getUTCHours())}:${pad(clickTime.getUTCMinutes())}:${pad(clickTime.getUTCSeconds())}`;

    try {
      const resp = await fetch(`/api/pressure/raw_nearby/${wellId}?t=${encodeURIComponent(isoStr)}&n=5`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json = await resp.json();

      if (!json.rows || json.rows.length === 0) {
        console.warn('[pressure_chart] No raw data for', isoStr);
        return;
      }

      showRawDataPopup(json.rows, isoStr, e.clientX, e.clientY);
    } catch (err) {
      console.error('Raw data popup error:', err);
    }
  });

  // Начальная загрузка
  loadChart(7, 5);

  // Экспортируем getFilterParams для использования в delta_pressure_chart.js
  window.pressureFilterParams = getFilterParams;

})();
