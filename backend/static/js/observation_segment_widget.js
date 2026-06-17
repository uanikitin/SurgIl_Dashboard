/**
 * observation_segment_widget.js — Виджет сегментного анализа для страницы Наблюдение.
 *
 * Интегрирует полный сегментный анализ (из customer_daily) в главу «Наблюдение».
 * Использует API /api/customer-daily/segment-analysis/preview для расчёта.
 * Сохраняет блоки kind='segment_analysis' с chapter='observation'.
 *
 * Документация: docs/segment_blocks_widget.md
 */
(function() {
  'use strict';

  // ── Глобальный экспорт ───────────────────────────────────────────────────────
  window.ObservationSegmentWidget = ObservationSegmentWidget;

  // ── CONSTANTS ─────────────────────────────────────────────────────────────────

  const DEFAULT_SELECTORS = {
    container: '#obs-seg-container',
    periodFrom: '#obs-seg-from',
    periodTo: '#obs-seg-to',
    calcBtn: '#obs-seg-calc-btn',
    saveBtn: '#obs-seg-save-btn',
    status: '#obs-seg-status',
    chartContainer: '#obs-seg-chart',
    segmentsTable: '#obs-seg-table',
    resultContainer: '#obs-seg-result',
    saveBlock: '#obs-seg-save-block',
    saveTitle: '#obs-seg-save-title',
    saveComment: '#obs-seg-save-comment',
    saveStatus: '#obs-seg-save-status',
    blocksPanel: '#obs-seg-blocks-panel',
    blocksGrid: '#obs-seg-blocks-grid',
  };

  const SEGMENT_TYPE_COLORS = {
    stable: '#22c55e',
    rising: '#3b82f6',
    falling: '#ef4444',
    volatile: '#f59e0b',
    unknown: '#9ca3af',
  };

  const SEGMENT_TYPE_LABELS = {
    initial: 'Начальный',
    stable: 'Стабильный',
    rise: 'Рост',
    decline: 'Снижение',
    sharp_rise: 'Резкий рост',
    sharp_decline: 'Резкое снижение',
    volatile: 'Волатильный',
    unknown: 'Неопределён',
    // legacy
    rising: 'Рост',
    falling: 'Падение',
  };

  // ── CLASS ─────────────────────────────────────────────────────────────────────

  function ObservationSegmentWidget(config) {
    this.config = config || {};
    this.wellId = null;
    this.wellNumber = '';
    this.apiBase = config.apiBase || '/api/customer-daily';
    this.blocksApiBase = config.blocksApiBase || '/api/customer-daily/blocks';
    this.chapter = config.chapter || 'observation';  // Глава для сохранения блоков
    this.selectors = Object.assign({}, DEFAULT_SELECTORS, config.selectors || {});
    this.getWellId = config.getWellId || (() => this.wellId);
    this.getWellNumber = config.getWellNumber || (() => this.wellNumber);
    this.getPeriodFrom = config.getPeriodFrom || (() => null);
    this.getPeriodTo = config.getPeriodTo || (() => null);
    this.getSensitivity = config.getSensitivity || (() => 5);
    this.onBlockSaved = config.onBlockSaved || function() {};
    this.onCalculated = config.onCalculated || function() {};

    this.lastResult = null;
    this.chartInstance = null;
    this.lastSavedBlockData = null;  // Данные последнего сохранённого/загруженного блока
  }

  // ── INITIALIZATION ────────────────────────────────────────────────────────────

  ObservationSegmentWidget.prototype.init = function() {
    console.log('[ObsSegWidget] init() called');
    this._bindEvents();
    this._loadBlocks();
    ObservationSegmentWidget.injectStyles();
    console.log('[ObsSegWidget] init() completed');
  };

  ObservationSegmentWidget.prototype._bindEvents = function() {
    const self = this;
    const calcBtn = document.querySelector(this.selectors.calcBtn);
    const saveBtn = document.querySelector(this.selectors.saveBtn);
    const fullBtn = document.querySelector(this.selectors.fullAnalysisBtn || '#obs-seg-full-btn');

    if (calcBtn) {
      calcBtn.addEventListener('click', function() {
        self.calculate();
      });
    }

    if (saveBtn) {
      saveBtn.addEventListener('click', function() {
        self.save();
      });
    }

    if (fullBtn) {
      fullBtn.addEventListener('click', function() {
        self.openFullAnalysis();
      });
    }
  };

  /**
   * Открывает полную страницу сегментного анализа с редактированием,
   * сравнением участков и ручным режимом.
   */
  ObservationSegmentWidget.prototype.openFullAnalysis = function() {
    const self = this;
    const wellId = this.getWellId();
    const periodFrom = this.getPeriodFrom();
    const periodTo = this.getPeriodTo();

    if (!wellId) {
      alert('Сначала выберите скважину');
      return;
    }
    if (!periodFrom || !periodTo) {
      alert('Укажите период анализа');
      return;
    }

    // Формируем URL для страницы полного анализа с указанием главы (observation или adaptation)
    const sensitivity = this.getSensitivity();
    const url = `/segment-analysis?well_id=${wellId}&date_from=${periodFrom}&date_to=${periodTo}&series=flow_rate&sensitivity=${sensitivity}&chapter=${this.chapter}`;

    // Открываем в новом окне
    const popup = window.open(url, '_blank', 'width=1400,height=900,scrollbars=yes,resizable=yes');

    // Обновляем панель блоков при возврате фокуса (пользователь сохранил блок в полном анализе)
    const onFocus = function() {
      self._loadBlocks();
      // Обновляем также правую панель превью главы, если доступна
      if (window._obsChapterPanel && typeof window._obsChapterPanel.refresh === 'function') {
        window._obsChapterPanel.refresh();
      }
    };
    window.addEventListener('focus', onFocus, { once: true });

    // Также проверяем через интервал, закрыто ли окно
    const checkClosed = setInterval(function() {
      if (popup && popup.closed) {
        clearInterval(checkClosed);
        self._loadBlocks();
        if (window._obsChapterPanel && typeof window._obsChapterPanel.refresh === 'function') {
          window._obsChapterPanel.refresh();
        }
      }
    }, 1000);

    // Останавливаем проверку через 30 минут (на случай если окно не закрыто)
    setTimeout(function() { clearInterval(checkClosed); }, 30 * 60 * 1000);
  };

  // ── CALCULATE ─────────────────────────────────────────────────────────────────

  ObservationSegmentWidget.prototype.calculate = async function() {
    const wellId = this.getWellId();
    const wellNumber = this.getWellNumber();
    const periodFrom = this.getPeriodFrom();
    const periodTo = this.getPeriodTo();

    console.log('[ObsSegWidget] calculate() called with:', {
      wellId, wellNumber, periodFrom, periodTo
    });

    if (!wellId) {
      this._setStatus('Скважина не выбрана', true);
      return null;
    }
    if (!periodFrom || !periodTo) {
      this._setStatus('Укажите период анализа', true);
      return null;
    }

    this._setStatus('Расчёт...', false);
    const calcBtn = document.querySelector(this.selectors.calcBtn);
    if (calcBtn) {
      calcBtn.disabled = true;
      calcBtn.textContent = 'Расчёт...';
    }

    // Используем API сегментного анализа на наших данных (pressure/flow_rate)
    const sensitivity = this.getSensitivity();
    const requestBody = {
      well_id: wellId,
      date_from: periodFrom,
      date_to: periodTo,
      primary_series: 'flow_rate',  // Q дебит
      sensitivity: sensitivity,
    };
    console.log('[ObsSegWidget] API request body:', requestBody);

    try {
      const resp = await fetch('/api/segment-demo/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
      });

      const data = await resp.json();
      console.log('[ObsSegWidget] API response:', data);

      if (!resp.ok || data.error || data.ok === false) {
        throw new Error(data.error || data.detail || 'Ошибка API');
      }

      this.lastResult = data;
      this.wellId = wellId;
      this.wellNumber = wellNumber;

      this._renderResult(data);
      this._showSaveBlock();
      this._setStatus('Анализ завершён', false);
      this.onCalculated(data);

      return data;

    } catch (err) {
      console.error('[ObservationSegmentWidget] calculate error:', err);
      this._setStatus('Ошибка: ' + err.message, true);
      return null;

    } finally {
      if (calcBtn) {
        calcBtn.disabled = false;
        calcBtn.textContent = '▶ Рассчитать';
      }
    }
  };

  // ── RENDER RESULT ─────────────────────────────────────────────────────────────

  ObservationSegmentWidget.prototype._renderResult = function(data) {
    const resultContainer = document.querySelector(this.selectors.resultContainer);
    if (resultContainer) {
      resultContainer.style.display = 'block';
    }

    this._renderChart(data);
    this._renderSegmentsTable(data);
  };

  ObservationSegmentWidget.prototype._renderChart = function(data) {
    const container = document.querySelector(this.selectors.chartContainer);
    if (!container || !window.Plotly) {
      console.warn('[ObservationSegmentWidget] Plotly or container not available');
      return;
    }

    // API /api/segment-demo/analyze возвращает:
    // chart_data: {dates: [...], primary: {name: "Q дебит", values: [...]}}
    // segments: [{num, start_idx, end_idx, segment_type, mean, std, slope, intercept, ...}]
    const chartData = data.chart_data || {};
    const dates = chartData.dates || [];
    const primary = chartData.primary || {};
    const values = primary.values || [];
    const seriesLabel = primary.name || 'Q дебит (тыс.м³/сут)';
    const segments = data.segments_extended || data.segments || [];
    const typeColors = data.type_colors || SEGMENT_TYPE_COLORS;
    const typeLabels = data.type_labels || SEGMENT_TYPE_LABELS;

    console.log('[ObsSegWidget] Chart data:', {
      datesCount: dates.length,
      valuesCount: values.length,
      segmentsCount: segments.length,
      sampleDates: dates.slice(0, 3),
      sampleValues: values.slice(0, 3)
    });

    if (dates.length === 0 || values.length === 0) {
      container.innerHTML = `
        <div style="background:#fef3c7; border:1px solid #f59e0b; border-radius:6px; padding:16px; text-align:center;">
          <p style="color:#92400e; margin:0 0 8px; font-weight:500;">⚠️ Нет данных за выбранный период</p>
          <p style="color:#a16207; font-size:0.82rem; margin:0;">
            Проверьте наличие данных давления с датчиков LoRa для этой скважины.
          </p>
        </div>
      `;
      return;
    }

    const traces = [];

    // Основная линия данных
    traces.push({
      x: dates,
      y: values,
      type: 'scatter',
      mode: 'lines',
      name: 'Данные',
      line: { color: '#3b82f6', width: 1.5 },
    });

    // Сегменты с заливкой и трендами
    segments.forEach((seg, idx) => {
      const startIdx = seg.start_idx !== undefined ? seg.start_idx : 0;
      const endIdx = seg.end_idx !== undefined ? seg.end_idx : dates.length - 1;
      const segDates = dates.slice(startIdx, endIdx + 1);
      const segValues = values.slice(startIdx, endIdx + 1);
      const segType = seg.segment_type || seg.type || 'unknown';
      const color = typeColors[segType] || '#9ca3af';
      const label = typeLabels[segType] || segType;

      // Заливка сегмента
      traces.push({
        x: segDates,
        y: segValues,
        type: 'scatter',
        mode: 'lines',
        name: `${seg.num || idx + 1}. ${label}`,
        line: { color: color, width: 2.5 },
        fill: 'tozeroy',
        fillcolor: color + '20',
      });

      // Тренд (оранжевая линия)
      if (seg.slope !== undefined && seg.intercept !== undefined) {
        const trendY = segDates.map((_, i) => seg.intercept + seg.slope * i);
        traces.push({
          x: segDates,
          y: trendY,
          type: 'scatter',
          mode: 'lines',
          name: `Тренд ${seg.num || idx + 1}`,
          line: { color: '#f97316', width: 2.5 },
          showlegend: false,
        });
      }
    });

    // Точки перелома (changepoints)
    const changepoints = data.changepoints || [];
    if (changepoints.length > 0) {
      const cpX = changepoints.map(idx => dates[idx]).filter(d => d);
      const cpY = changepoints.map(idx => values[idx]).filter(v => v !== undefined);
      traces.push({
        x: cpX,
        y: cpY,
        type: 'scatter',
        mode: 'markers+text',
        name: 'Точки перелома',
        marker: { color: '#dc2626', size: 8, symbol: 'diamond' },
        text: changepoints.map((_, i) => `CP${i + 1}`),
        textposition: 'top center',
        textfont: { size: 10, color: '#dc2626' },
      });
    }

    const layout = {
      title: {
        text: seriesLabel,
        font: { size: 14 },
      },
      xaxis: {
        title: 'Дата',
        type: 'date',
      },
      yaxis: {
        title: seriesLabel,
      },
      margin: { t: 40, r: 20, b: 50, l: 60 },
      legend: {
        orientation: 'h',
        y: -0.2,
      },
      hovermode: 'x unified',
    };

    const config = {
      responsive: true,
      displayModeBar: true,
      modeBarButtonsToRemove: ['lasso2d', 'select2d'],
    };

    Plotly.newPlot(container, traces, layout, config);
    this.chartInstance = container;
  };

  ObservationSegmentWidget.prototype._renderSegmentsTable = function(data) {
    const container = document.querySelector(this.selectors.segmentsTable);
    if (!container) return;

    const segments = data.segments_extended || data.segments || [];
    const typeColors = data.type_colors || SEGMENT_TYPE_COLORS;
    const typeLabels = data.type_labels || SEGMENT_TYPE_LABELS;

    if (segments.length === 0) {
      container.innerHTML = '<p style="color:#9ca3af;font-size:0.84rem;">Сегменты не обнаружены</p>';
      return;
    }

    // Формат из /api/segment-demo/analyze:
    // {num, start_idx, end_idx, start_date, end_date, segment_type, mean, std, slope, intercept, r_squared, drift_pct, ...}
    let html = `
      <table class="obs-seg-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Тип</th>
            <th>Границы</th>
            <th>Дн</th>
            <th>Q общ.</th>
            <th>P_шл</th>
            <th>P_уст</th>
            <th>ΔP</th>
            <th>Раб%</th>
            <th>Изм%</th>
            <th>Тренд</th>
          </tr>
        </thead>
        <tbody>
    `;

    segments.forEach((seg, idx) => {
      // Fallback: извлекаем давления из secondary_means если нет на верхнем уровне
      const sm = seg.secondary_means || {};
      if (seg.mean_p_flowline === undefined && sm.p_line_mean !== undefined) {
        seg.mean_p_flowline = sm.p_line_mean;
      }
      if (seg.mean_p_wellhead === undefined && sm.p_tube_mean !== undefined) {
        seg.mean_p_wellhead = sm.p_tube_mean;
      }
      if (seg.mean_dp === undefined && sm.dp_mean !== undefined) {
        seg.mean_dp = sm.dp_mean;
      }
      if (seg.working_pct === undefined && sm.downtime_min !== undefined) {
        seg.working_pct = Math.max(0, (1440 - sm.downtime_min) / 1440 * 100);
      }

      const segType = seg.segment_type || seg.type || 'unknown';
      const color = typeColors[segType] || '#9ca3af';
      const label = typeLabels[segType] || segType;

      // Границы времени (формат HH:MM—HH:MM или даты)
      const startDate = seg.start_date || '';
      const endDate = seg.end_date || '';
      let boundaries = '—';
      if (startDate && endDate) {
        // Если это datetime строки, показываем время
        const startTime = startDate.includes('T') ? startDate.split('T')[1]?.slice(0, 5) : startDate.slice(11, 16);
        const endTime = endDate.includes('T') ? endDate.split('T')[1]?.slice(0, 5) : endDate.slice(11, 16);
        if (startTime && endTime) {
          boundaries = `${startTime}—${endTime}`;
        } else {
          boundaries = `${startDate.slice(0, 10)} — ${endDate.slice(0, 10)}`;
        }
      }

      // Количество дней
      const days = seg.days !== undefined ? seg.days :
                   (seg.n_points !== undefined ? seg.n_points :
                   (seg.end_idx !== undefined && seg.start_idx !== undefined ? seg.end_idx - seg.start_idx + 1 : '—'));

      // Среднее значение Q — API возвращает mean_q, иногда mean
      const meanVal = seg.mean_q !== undefined ? seg.mean_q :
                      (seg.mean !== undefined ? seg.mean :
                      (seg.mean_value !== undefined ? seg.mean_value : undefined));
      const mean = meanVal !== undefined ? Number(meanVal).toFixed(2) : '—';

      // Рабочий % времени (вместо std)
      const workingPct = seg.working_pct !== undefined ? seg.working_pct.toFixed(0) + '%' : '—';

      // Дрейф/изменение (%) — API возвращает change_pct, иногда drift_pct
      let driftHtml = '—';
      const changePct = seg.change_pct !== undefined ? seg.change_pct :
                        (seg.drift_pct !== undefined ? seg.drift_pct : undefined);
      if (changePct !== undefined && changePct !== null) {
        const drift = Number(changePct);
        const driftClass = drift >= 0 ? 'drift-positive' : 'drift-negative';
        const driftSign = drift >= 0 ? '+' : '';
        driftHtml = `<span class="${driftClass}">${driftSign}${drift.toFixed(1)}%</span>`;
      }

      // Тренд (slope_total)
      const slopeVal = seg.slope_total !== undefined ? seg.slope_total :
                       (seg.slope !== undefined ? seg.slope : undefined);
      const slope = slopeVal !== undefined ? (slopeVal >= 0 ? '+' : '') + Number(slopeVal).toFixed(3) : '—';

      // Давления: P_шлейф, P_устье, ΔP
      const pFlowline = seg.mean_p_flowline !== undefined && seg.mean_p_flowline !== null
                        ? Number(seg.mean_p_flowline).toFixed(1) : '—';
      const pWellhead = seg.mean_p_wellhead !== undefined && seg.mean_p_wellhead !== null
                        ? Number(seg.mean_p_wellhead).toFixed(1) : '—';
      const deltaP = seg.mean_dp !== undefined && seg.mean_dp !== null
                     ? Number(seg.mean_dp).toFixed(1) : '—';

      html += `
        <tr>
          <td><span class="obs-seg-num" style="background:${color}">${seg.num || idx + 1}</span></td>
          <td><span class="obs-seg-type-badge" style="background:${color};color:#fff;padding:2px 8px;border-radius:4px;font-size:0.75rem;">${this._escHtml(label)}</span></td>
          <td style="font-size:0.82rem;">${boundaries}</td>
          <td>${days}</td>
          <td><b>${mean}</b></td>
          <td style="font-size:0.82rem;color:#6b7280;">${pFlowline}</td>
          <td style="font-size:0.82rem;color:#6b7280;">${pWellhead}</td>
          <td style="font-size:0.82rem;color:#3b82f6;font-weight:500;">${deltaP}</td>
          <td>${workingPct}</td>
          <td>${driftHtml}</td>
          <td style="font-size:0.82rem;color:#9ca3af;font-family:monospace;">${slope}</td>
        </tr>
      `;
    });

    html += '</tbody></table>';

    // Добавляем стили для дрейфа
    html = `
      <style>
        .drift-positive { color: #22c55e; font-weight: 500; }
        .drift-negative { color: #ef4444; font-weight: 500; }
      </style>
    ` + html;

    container.innerHTML = html;
  };

  // ── SAVE ──────────────────────────────────────────────────────────────────────

  ObservationSegmentWidget.prototype._showSaveBlock = function() {
    const saveBlock = document.querySelector(this.selectors.saveBlock);
    if (saveBlock) {
      saveBlock.style.display = 'block';
    }

    const periodFrom = this.getPeriodFrom();
    const periodTo = this.getPeriodTo();
    const titleInput = document.querySelector(this.selectors.saveTitle);
    if (titleInput && !titleInput.value) {
      titleInput.value = `Сегментный анализ ${periodFrom} — ${periodTo}`;
    }
  };

  ObservationSegmentWidget.prototype.save = async function() {
    if (!this.lastResult) {
      this._setSaveStatus('Сначала выполните расчёт', true);
      return null;
    }

    const titleInput = document.querySelector(this.selectors.saveTitle);
    const commentInput = document.querySelector(this.selectors.saveComment);
    const title = titleInput ? titleInput.value.trim() : '';
    const comment = commentInput ? commentInput.value.trim() : '';

    if (!title) {
      this._setSaveStatus('Введите название блока', true);
      return null;
    }

    const saveBtn = document.querySelector(this.selectors.saveBtn);
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = 'Сохранение...';
    }

    try {
      const wellId = this.getWellId();
      const snapshot = this._buildSnapshot();

      const resp = await fetch(this.blocksApiBase, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          well_id: wellId,
          kind: 'segment_analysis',
          title: title,
          params: {
            period: {
              from: this.getPeriodFrom(),
              to: this.getPeriodTo(),
            },
            source: this.chapter,
            chapter: this.chapter,
          },
          data_snapshot: snapshot,
          comment: comment || null,
          in_report: true,
        }),
      });

      const data = await resp.json();

      if (!resp.ok) {
        throw new Error(data.detail || data.error || 'Ошибка сохранения');
      }

      this._setSaveStatus('Блок сохранён', false);
      this._loadBlocks();
      this.onBlockSaved(data);

      // Clear form
      if (titleInput) titleInput.value = '';
      if (commentInput) commentInput.value = '';

      return data;

    } catch (err) {
      console.error('[ObservationSegmentWidget] save error:', err);
      this._setSaveStatus('Ошибка: ' + err.message, true);
      return null;

    } finally {
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.textContent = '💾 Сохранить в отчёт';
      }
    }
  };

  ObservationSegmentWidget.prototype._buildSnapshot = function() {
    if (!this.lastResult) return null;

    const data = this.lastResult;
    // API /api/segment-demo/analyze возвращает: segments
    // API /api/customer-daily/segment-analysis/preview возвращает: segments_extended
    const segments = data.segments_extended || data.segments || [];
    const typeLabels = data.type_labels || SEGMENT_TYPE_LABELS;

    // Даты из chart_data для вычисления start_date/end_date сегментов
    const chartDates = (data.chart_data && data.chart_data.dates) || [];

    // Утилита: ISO → DD.MM.YYYY
    const isoToDMY = (iso) => {
      if (!iso) return '?';
      // Если уже в формате DD.MM.YYYY
      if (/^\d{2}\.\d{2}\.\d{4}$/.test(iso)) return iso;
      // ISO формат: 2026-02-02 или 2026-02-02T00:00:00
      const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
      return m ? `${m[3]}.${m[2]}.${m[1]}` : String(iso);
    };

    // Маппинг полей API → оба рендерера
    // Поддерживаем ОБА формата API:
    //   segment-demo: {mean_value, segment_type, slope, start_idx, end_idx}
    //   customer-daily: {mean_q, type, slope_total, start_date, end_date, start, end}
    const mappedSegments = segments.map((seg, idx) => {
      // Даты: вычисляем из chart_data.dates если нет в сегменте
      let startDateIso = seg.start_date;
      let endDateIso = seg.end_date;
      if (!startDateIso && chartDates.length > 0 && seg.start_idx !== undefined) {
        startDateIso = chartDates[seg.start_idx] || null;
      }
      if (!endDateIso && chartDates.length > 0 && seg.end_idx !== undefined) {
        // end_idx exclusive, поэтому берём end_idx - 1
        const endIdx = Math.max(0, (seg.end_idx || 1) - 1);
        endDateIso = chartDates[Math.min(endIdx, chartDates.length - 1)] || null;
      }

      // Среднее: segment-demo = mean_value, customer-daily = mean_q
      const meanVal = seg.mean_value !== undefined ? seg.mean_value :
                      (seg.mean_q !== undefined ? seg.mean_q :
                      (seg.mean !== undefined ? seg.mean : null));

      // Тренд: segment-demo = slope, customer-daily = slope_total
      const slopeVal = seg.slope !== undefined ? seg.slope :
                       (seg.slope_total !== undefined ? seg.slope_total : null);

      // Тип: segment-demo = segment_type, customer-daily = type
      const typeVal = seg.segment_type || seg.type || 'unknown';

      // Давления из secondary_means (API segment-demo возвращает там)
      const sm = seg.secondary_means || {};
      const pFlowline = seg.mean_p_flowline !== undefined ? seg.mean_p_flowline : sm.p_line_mean;
      const pWellhead = seg.mean_p_wellhead !== undefined ? seg.mean_p_wellhead : sm.p_tube_mean;
      const dpMean = seg.mean_dp !== undefined ? seg.mean_dp : sm.dp_mean;
      // working_pct из downtime_min
      let workingPct = seg.working_pct;
      if (workingPct === undefined && sm.downtime_min !== undefined) {
        workingPct = Math.max(0, (1440 - sm.downtime_min) / 1440 * 100);
      }

      return {
        num: seg.num || (idx + 1),
        start_idx: seg.start_idx,
        end_idx: seg.end_idx,
        // ISO формат (observation_chapter_renderer.py)
        start_date: startDateIso,
        end_date: endDateIso,
        // DD.MM.YYYY формат (chapter_render.js)
        start: seg.start || isoToDMY(startDateIso),
        end: seg.end || isoToDMY(endDateIso),
        days: seg.days || (seg.end_idx && seg.start_idx ? seg.end_idx - seg.start_idx : null),
        // Тип режима — оба формата
        type: typeVal,
        segment_type: typeVal,
        color: seg.color,
        // Средние — оба формата
        mean_value: meanVal,           // observation_chapter_renderer.py / segment-demo
        mean_q: meanVal,               // chapter_render.js / customer-daily
        std_value: seg.std_value,      // segment-demo
        mean_q_working: seg.mean_q_working,
        // Давления: P_шлейф, P_устье, ΔP (из secondary_means или напрямую)
        mean_p_flowline: pFlowline,
        mean_p_wellhead: pWellhead,
        mean_dp: dpMean,
        // Тренд — оба формата
        slope: slopeVal,               // observation_chapter_renderer.py / segment-demo
        slope_total: slopeVal,         // chapter_render.js / customer-daily
        intercept: seg.intercept,
        intercept_total: seg.intercept_total || seg.intercept,
        intercept_working: seg.intercept_working,
        slope_working: seg.slope_working,
        r_squared: seg.r_squared,
        // Изменение %
        change_pct: seg.change_pct,
        change_abs: seg.change_abs,
        change_q_working_pct: seg.change_q_working_pct,
        change_dp_pct: seg.change_dp_pct,
        // Рабочее время
        working_pct: workingPct,
        mean_shutdown: seg.mean_shutdown,
        // Причина и флаги
        cause: seg.cause,
        trend_direction: seg.trend_direction,
        is_shutdown_cluster: seg.is_shutdown_cluster,
        is_workover: seg.is_workover,
        gradual_trend: seg.gradual_trend,
      };
    });

    return {
      _v: 'segment_analysis_v2',  // Новая версия для данных из segment-demo
      schema_version: '2.0',
      computed_at: new Date().toISOString(),
      ok: true,
      well_id: this.getWellId(),
      well_number: this.getWellNumber() || data.well_number,
      // chapter_render.js ожидает date_from/date_to на верхнем уровне
      date_from: data.date_from || this.getPeriodFrom(),
      date_to: data.date_to || this.getPeriodTo(),
      // observation_chapter_renderer.py ожидает period.from/to
      period: {
        from: data.date_from || this.getPeriodFrom(),
        to: data.date_to || this.getPeriodTo(),
      },
      n_points: data.n_points || chartDates.length || 0,
      days_count: data.days_count || chartDates.length || 0,
      primary_series: data.primary_series || 'flow_rate',
      // Полные данные графика — трансформируем из segment-demo формата если нужно
      // segment-demo: {dates, primary: {name, values}, secondary: {...}}
      // customer-daily/chapter_render: {dates, q_total, q_working, shutdown_min, ...}
      chart_data: this._normalizeChartData(data.chart_data, data.primary_series),
      // Сегменты в расширенном формате
      segments_extended: mappedSegments,
      // Точки перелома — оба формата
      // segment-demo: changepoints = [idx, idx, ...]
      // customer-daily: cp_marks = [{idx, date, tag, source}, ...]
      changepoints: data.changepoints || (data.cp_marks || []).map(cp => cp.idx),
      cp_marks: data.cp_marks || (data.changepoints || []).map((idx, i) => ({
        idx: idx,
        date: chartDates[idx] || null,
        tag: `CP${i+1}`,
      })),
      // Кластеры простоев (для графика) — разные имена в разных API
      shutdown_clusters: data.shutdown_clusters || data.anomaly_clusters || [],
      anomaly_clusters: data.anomaly_clusters || data.shutdown_clusters || [],
      // Dual summary для отладки
      dual_summary: data.dual_summary || null,
      // Цвета и лейблы
      type_colors: data.type_colors,
      type_labels: data.type_labels,
      // Интерпретация
      descriptions: data.descriptions || [],
      cp_descriptions: data.cp_descriptions || [],
      interpretation: {
        summary: this._generateSummary(mappedSegments, typeLabels),
        descriptions: (data.descriptions || []).slice(0),
      },
      display_settings: {
        show_chart: true,
        show_segments_table: true,
        show_interpretation: true,
        in_report: true,
      },
    };
  };

  /**
   * Нормализация chart_data из разных форматов API.
   *
   * segment-demo format:
   *   {dates, primary: {name, values}, secondary: {col: {name, values}}}
   *
   * customer-daily/chapter_render format:
   *   {dates, q_total, q_working, shutdown_min, p_wellhead, p_flowline, dp, choke_mm}
   */
  ObservationSegmentWidget.prototype._normalizeChartData = function(chartData, primarySeries) {
    if (!chartData) return null;

    // Если уже в формате customer-daily (есть q_total) — возвращаем как есть
    if (chartData.q_total) return chartData;

    // Трансформируем из segment-demo формата
    const result = {
      dates: chartData.dates || [],
    };

    // Primary → q_total (или соответствующее поле)
    if (chartData.primary && chartData.primary.values) {
      const primaryName = primarySeries || chartData.primary.name || 'flow_rate';
      if (primaryName === 'flow_rate' || primaryName.includes('flow')) {
        result.q_total = chartData.primary.values;
      } else if (primaryName === 'p_tube' || primaryName.includes('wellhead')) {
        result.p_wellhead = chartData.primary.values;
      } else if (primaryName === 'p_line' || primaryName.includes('flowline')) {
        result.p_flowline = chartData.primary.values;
      } else if (primaryName === 'dp') {
        result.dp = chartData.primary.values;
      } else if (primaryName === 'downtime_min' || primaryName.includes('shutdown')) {
        result.shutdown_min = chartData.primary.values;
      } else {
        // Fallback: сохраняем как q_total
        result.q_total = chartData.primary.values;
      }
    }

    // Secondary ряды
    const sec = chartData.secondary || {};
    for (const [colName, colData] of Object.entries(sec)) {
      if (!colData || !colData.values) continue;
      const vals = colData.values;

      if (colName.includes('flow') || colName === 'flow_rate_mean') {
        if (!result.q_total) result.q_total = vals;
        else result.q_working = vals;
      } else if (colName.includes('p_tube') || colName === 'p_tube_mean') {
        result.p_wellhead = vals;
      } else if (colName.includes('p_line') || colName === 'p_line_mean') {
        result.p_flowline = vals;
      } else if (colName.includes('dp') || colName === 'dp_mean') {
        result.dp = vals;
      } else if (colName.includes('downtime') || colName.includes('shutdown')) {
        result.shutdown_min = vals;
      }
    }

    // Вычисляем dp если есть оба давления
    if (!result.dp && result.p_wellhead && result.p_flowline) {
      result.dp = result.p_wellhead.map((pw, i) => {
        const pf = result.p_flowline[i];
        return (pw != null && pf != null) ? pw - pf : null;
      });
    }

    return result;
  };

  ObservationSegmentWidget.prototype._generateSummary = function(segments, typeLabels) {
    if (!segments || segments.length === 0) {
      return 'Сегменты не обнаружены.';
    }

    const labels = typeLabels || SEGMENT_TYPE_LABELS;
    const typeCount = {};
    segments.forEach(seg => {
      const t = seg.segment_type || seg.type || 'unknown';
      typeCount[t] = (typeCount[t] || 0) + 1;
    });

    const parts = [];
    for (const [type, count] of Object.entries(typeCount)) {
      const label = labels[type] || type;
      parts.push(`${count} ${label.toLowerCase()}`);
    }

    return `Обнаружено ${segments.length} сегментов: ${parts.join(', ')}.`;
  };

  // ── BLOCKS MANAGEMENT ─────────────────────────────────────────────────────────

  ObservationSegmentWidget.prototype._loadBlocks = async function() {
    const wellId = this.getWellId();
    if (!wellId) return;

    const panel = document.querySelector(this.selectors.blocksPanel);
    const grid = document.querySelector(this.selectors.blocksGrid);
    if (!panel || !grid) return;

    try {
      // Используем серверную фильтрацию по chapter (observation или adaptation)
      const resp = await fetch(`${this.blocksApiBase}?well_id=${wellId}&chapter=${this.chapter}`);
      const data = await resp.json();
      const blocks = (data.blocks || data.items || []).filter(b =>
        b.kind === 'segment_analysis' || b.kind === 'segment_comparison'
      );

      // Сохраняем для последующего использования
      this._savedBlocks = blocks;

      if (blocks.length === 0) {
        panel.style.display = 'none';
        return;
      }

      panel.style.display = 'block';
      grid.innerHTML = blocks.map(b => this._renderBlockCard(b)).join('');
      this._bindBlockHandlers(grid);

      // Автоматически загружаем последний блок для текущего периода
      this._autoLoadMatchingBlock();

    } catch (err) {
      console.error('[ObservationSegmentWidget] loadBlocks error:', err);
    }
  };

  /**
   * Автоматически загружает данные из сохранённого блока, если он соответствует текущему периоду.
   */
  ObservationSegmentWidget.prototype._autoLoadMatchingBlock = function() {
    const periodFrom = this.getPeriodFrom();
    const periodTo = this.getPeriodTo();
    if (!periodFrom || !periodTo || !this._savedBlocks || this._savedBlocks.length === 0) return;

    // Ищем блок segment_analysis с совпадающим периодом (последний по дате создания)
    const matching = this._savedBlocks
      .filter(b => b.kind === 'segment_analysis')
      .filter(b => {
        const p = b.data_snapshot?.period || b.params?.period || {};
        return p.from === periodFrom && p.to === periodTo;
      })
      .sort((a, b) => new Date(b.created_at) - new Date(a.created_at));

    if (matching.length > 0) {
      const block = matching[0];
      console.log('[ObsSegWidget] Auto-loading saved block:', block.id, block.title);
      this._displaySavedBlock(block);
    }
  };

  /**
   * Отображает данные из сохранённого блока (вместо пересчёта).
   */
  ObservationSegmentWidget.prototype._displaySavedBlock = function(block) {
    const snapshot = block.data_snapshot;
    if (!snapshot) return;

    // Нормализуем chart_data: снапшот хранит q_total, _renderChart ожидает primary.values
    let chartData = snapshot.chart_data;
    if (chartData && !chartData.primary && chartData.q_total) {
      chartData = {
        dates: chartData.dates,
        primary: { name: 'Q дебит (тыс.м³/сут)', values: chartData.q_total }
      };
    }

    // Преобразуем snapshot в формат, совместимый с API-ответом
    let segments = snapshot.segments_extended || snapshot.segments || [];

    // Миграция: обогащаем сегменты полями давления из secondary_means (если есть)
    let needsResave = false;
    segments = segments.map(seg => {
      // Если поля давления отсутствуют, но есть secondary_means — вытащим оттуда
      if (seg.mean_p_flowline === undefined && seg.secondary_means) {
        const sm = seg.secondary_means;
        seg.mean_p_flowline = sm.p_line_mean;
        seg.mean_p_wellhead = sm.p_tube_mean;
        seg.mean_dp = sm.dp_mean;
        if (sm.downtime_min !== undefined && seg.working_pct === undefined) {
          seg.working_pct = Math.max(0, (1440 - sm.downtime_min) / 1440 * 100);
        }
        needsResave = true;
      }
      return seg;
    });

    const data = {
      ok: true,
      well_id: block.well_id,
      chart_data: chartData,
      segments: segments,
      changepoints: (snapshot.cp_marks || []).map(cp => cp.idx),
      type_colors: snapshot.type_colors || SEGMENT_TYPE_COLORS,
      interpretation: snapshot.interpretation,
    };

    this.lastResult = data;
    this.lastSavedBlockData = block;
    this._renderResult(data);

    // Показываем индикатор, что это сохранённый блок
    if (needsResave) {
      this._setStatus(`📦 Загружен блок: ${block.title} ⚠️ Пересохраните для обновления данных давления`, false);
    } else if (segments.length > 0 && segments[0].mean_p_flowline === undefined) {
      // Старый блок без данных давления — рекомендуем пересчитать
      this._setStatus(`📦 Загружен блок: ${block.title} ⚠️ Нажмите "Рассчитать" для получения данных давления`, false);
    } else {
      this._setStatus(`📦 Загружен блок: ${block.title}`, false);
    }
  };

  /**
   * Загружает блок по ID и отображает его данные.
   */
  ObservationSegmentWidget.prototype.loadBlock = function(blockId) {
    const block = (this._savedBlocks || []).find(b => b.id === blockId);
    if (block) {
      this._displaySavedBlock(block);
    }
  };

  ObservationSegmentWidget.prototype._renderBlockCard = function(block) {
    const snapshot = block.data_snapshot || {};
    const segments = snapshot.segments_extended || [];
    const period = snapshot.period || block.params?.period || {};
    const kindLabel = block.kind === 'segment_analysis' ? 'Анализ' : 'Сравнение';
    const kindClass = block.kind === 'segment_analysis' ? 'kind-analysis' : 'kind-comparison';
    const isAnalysis = block.kind === 'segment_analysis';

    return `
      <div class="obs-seg-block-card ${kindClass}" data-block-id="${block.id}">
        <div class="obs-seg-block-header">
          <span class="obs-seg-block-badge">${this._escHtml(kindLabel)}</span>
          <span class="obs-seg-block-period">${this._escHtml(period.from || '')} — ${this._escHtml(period.to || '')}</span>
        </div>
        <div class="obs-seg-block-title">${this._escHtml(block.title)}</div>
        <div class="obs-seg-block-meta">
          <span>${segments.length} сегм.</span>
          <span>${block.in_report ? '✓ в отчёте' : '○ не в отчёте'}</span>
        </div>
        <div class="obs-seg-block-actions">
          ${isAnalysis ? `
          <button type="button" class="obs-seg-btn-load" data-action="load" data-block-id="${block.id}"
                  title="Загрузить и показать на графике">📊 Загрузить</button>
          ` : ''}
          <label class="obs-seg-block-cb">
            <input type="checkbox" data-action="in-report" data-block-id="${block.id}"
                   ${block.in_report ? 'checked' : ''}>
            В отчёт
          </label>
          <button type="button" class="obs-seg-btn-icon" data-action="delete" data-block-id="${block.id}"
                  title="Удалить">🗑️</button>
        </div>
      </div>
    `;
  };

  ObservationSegmentWidget.prototype._bindBlockHandlers = function(grid) {
    const self = this;

    grid.querySelectorAll('[data-action="in-report"]').forEach(cb => {
      cb.addEventListener('change', function() {
        self._updateBlockInReport(parseInt(cb.dataset.blockId), cb.checked);
      });
    });

    grid.querySelectorAll('[data-action="delete"]').forEach(btn => {
      btn.addEventListener('click', function() {
        self._deleteBlock(parseInt(btn.dataset.blockId));
      });
    });

    grid.querySelectorAll('[data-action="load"]').forEach(btn => {
      btn.addEventListener('click', function() {
        self.loadBlock(parseInt(btn.dataset.blockId));
      });
    });
  };

  ObservationSegmentWidget.prototype._updateBlockInReport = async function(blockId, value) {
    try {
      await fetch(`${this.blocksApiBase}/${blockId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ in_report: value }),
      });
      // Trigger refresh in parent panel if available
      if (window._obsChapterPanel && typeof window._obsChapterPanel.refresh === 'function') {
        window._obsChapterPanel.refresh();
      }
    } catch (err) {
      console.error('[ObservationSegmentWidget] updateBlockInReport error:', err);
    }
  };

  ObservationSegmentWidget.prototype._deleteBlock = async function(blockId) {
    if (!confirm('Удалить блок сегментного анализа?')) return;

    try {
      await fetch(`${this.blocksApiBase}/${blockId}`, { method: 'DELETE' });
      this._loadBlocks();
      // Trigger refresh in parent panel if available
      if (window._obsChapterPanel && typeof window._obsChapterPanel.refresh === 'function') {
        window._obsChapterPanel.refresh();
      }
    } catch (err) {
      console.error('[ObservationSegmentWidget] deleteBlock error:', err);
    }
  };

  // ── HELPERS ───────────────────────────────────────────────────────────────────

  ObservationSegmentWidget.prototype._setStatus = function(msg, isError) {
    const statusEl = document.querySelector(this.selectors.status);
    if (statusEl) {
      statusEl.textContent = msg;
      statusEl.style.color = isError ? '#dc2626' : '#059669';
    }
  };

  ObservationSegmentWidget.prototype._setSaveStatus = function(msg, isError) {
    const statusEl = document.querySelector(this.selectors.saveStatus);
    if (statusEl) {
      statusEl.textContent = msg;
      statusEl.style.color = isError ? '#dc2626' : '#10b981';
    }
  };

  ObservationSegmentWidget.prototype._escHtml = function(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  };

  // ── PUBLIC METHODS ────────────────────────────────────────────────────────────

  ObservationSegmentWidget.prototype.setWell = function(wellId, wellNumber) {
    this.wellId = wellId;
    this.wellNumber = wellNumber;
    this._loadBlocks();
  };

  ObservationSegmentWidget.prototype.refresh = function() {
    this._loadBlocks();
  };

  // ── STATIC: CSS INJECTION ─────────────────────────────────────────────────────

  ObservationSegmentWidget.injectStyles = function() {
    if (document.getElementById('obs-seg-widget-styles')) return;

    const css = `
      .obs-seg-widget {
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 16px;
        background: #fafbff;
        margin-top: 16px;
      }
      .obs-seg-widget h4 {
        margin: 0 0 12px;
        font-size: 0.95rem;
        font-weight: 600;
        color: #1e3a5f;
      }
      .obs-seg-period-row {
        display: flex;
        gap: 10px;
        align-items: center;
        margin-bottom: 12px;
        flex-wrap: wrap;
      }
      .obs-seg-period-row label {
        font-size: 0.84rem;
        color: #374151;
      }
      .obs-seg-period-row input {
        padding: 5px 8px;
        border: 1px solid #d1d5db;
        border-radius: 4px;
        font-size: 0.84rem;
      }
      .obs-seg-btn {
        padding: 6px 14px;
        border: none;
        border-radius: 5px;
        font-size: 0.84rem;
        cursor: pointer;
        font-weight: 500;
        transition: background 0.15s;
      }
      .obs-seg-btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
      .obs-seg-btn-primary {
        background: #2563eb;
        color: #fff;
      }
      .obs-seg-btn-primary:hover:not(:disabled) {
        background: #1d4ed8;
      }
      .obs-seg-btn-success {
        background: #16a34a;
        color: #fff;
      }
      .obs-seg-btn-success:hover:not(:disabled) {
        background: #15803d;
      }
      .obs-seg-result {
        margin-top: 14px;
        background: #fff;
        border: 1px solid #e5e7eb;
        border-radius: 6px;
        padding: 14px;
      }
      .obs-seg-chart {
        height: 350px;
        margin-bottom: 14px;
      }
      .obs-seg-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.84rem;
      }
      .obs-seg-table th,
      .obs-seg-table td {
        padding: 6px 10px;
        text-align: left;
        border-bottom: 1px solid #e5e7eb;
      }
      .obs-seg-table th {
        background: #f9fafb;
        font-weight: 600;
        color: #374151;
      }
      .obs-seg-num {
        display: inline-block;
        width: 22px;
        height: 22px;
        line-height: 22px;
        text-align: center;
        border-radius: 50%;
        color: #fff;
        font-weight: 600;
        font-size: 0.78rem;
      }
      .obs-seg-type {
        font-weight: 500;
      }
      .obs-seg-save-block {
        margin-top: 14px;
        padding: 12px;
        background: #fef3c7;
        border: 1px solid #fcd34d;
        border-radius: 6px;
      }
      .obs-seg-save-block h5 {
        margin: 0 0 10px;
        font-size: 0.9rem;
        color: #92400e;
      }
      .obs-seg-save-row {
        display: flex;
        gap: 8px;
        align-items: center;
        margin-bottom: 8px;
        flex-wrap: wrap;
      }
      .obs-seg-save-row input,
      .obs-seg-save-row textarea {
        flex: 1;
        min-width: 200px;
        padding: 5px 8px;
        border: 1px solid #fcd34d;
        border-radius: 4px;
        font-size: 0.84rem;
        font-family: inherit;
      }
      .obs-seg-blocks-panel {
        margin-top: 16px;
        padding: 12px;
        background: #f9fafb;
        border-radius: 6px;
      }
      .obs-seg-blocks-panel h5 {
        margin: 0 0 10px;
        font-size: 0.88rem;
        color: #374151;
      }
      .obs-seg-blocks-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
        gap: 10px;
      }
      .obs-seg-block-card {
        background: #fff;
        border: 1px solid #e5e7eb;
        border-left: 4px solid #5b21b6;
        border-radius: 6px;
        padding: 10px 12px;
      }
      .obs-seg-block-card.kind-comparison {
        border-left-color: #0891b2;
      }
      .obs-seg-block-header {
        display: flex;
        gap: 8px;
        align-items: center;
        margin-bottom: 6px;
      }
      .obs-seg-block-badge {
        font-size: 0.72rem;
        font-weight: 600;
        padding: 2px 6px;
        background: #5b21b6;
        color: #fff;
        border-radius: 3px;
        text-transform: uppercase;
      }
      .kind-comparison .obs-seg-block-badge {
        background: #0891b2;
      }
      .obs-seg-block-period {
        font-size: 0.78rem;
        color: #6b7280;
      }
      .obs-seg-block-title {
        font-weight: 600;
        font-size: 0.86rem;
        color: #1f2937;
        margin-bottom: 4px;
      }
      .obs-seg-block-meta {
        display: flex;
        gap: 12px;
        font-size: 0.78rem;
        color: #6b7280;
        margin-bottom: 8px;
      }
      .obs-seg-block-actions {
        display: flex;
        gap: 8px;
        align-items: center;
      }
      .obs-seg-block-cb {
        font-size: 0.8rem;
        display: flex;
        align-items: center;
        gap: 4px;
        cursor: pointer;
      }
      .obs-seg-btn-icon {
        background: none;
        border: none;
        cursor: pointer;
        font-size: 1rem;
        padding: 2px;
      }
      .obs-seg-btn-icon:hover {
        opacity: 0.7;
      }
      .obs-seg-btn-load {
        padding: 3px 8px;
        background: #3b82f6;
        color: #fff;
        border: none;
        border-radius: 4px;
        font-size: 0.75rem;
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 4px;
      }
      .obs-seg-btn-load:hover {
        background: #2563eb;
      }
    `;

    const style = document.createElement('style');
    style.id = 'obs-seg-widget-styles';
    style.textContent = css;
    document.head.appendChild(style);
  };

})();
