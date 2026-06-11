/**
 * SegmentBlocksWidget — модуль управления блоками сегментного анализа.
 *
 * Функциональность:
 *   - Создание снапшотов segment_analysis и segment_comparison
 *   - Отображение плиток сохранённых блоков
 *   - Управление настройками отображения блоков (in_report, show_chart, etc.)
 *   - Удаление блоков
 *
 * Быстрый старт:
 *   const widget = new SegmentBlocksWidget({
 *     wellId: 123,
 *     wellNumber: '123-бис',
 *     chapterFilter: 'observation'  // опционально — фильтр по главе
 *   });
 *
 *   // Сохранить анализ
 *   widget.saveAnalysis({
 *     chartData: () => chartData,
 *     segments: () => segments,
 *     changepoints: () => changepoints,
 *     eventsData: () => eventsData,
 *     period: { from: '2024-01-01', to: '2024-12-31' },
 *     series: 'flow_rate'
 *   });
 *
 *   // Сохранить сравнение
 *   widget.saveComparison({
 *     chartData: () => chartData,
 *     segments: () => segments,
 *     selectedForCompare: () => selectedForCompare,  // Set
 *     compareColors: ['#8b5cf6', '#0891b2', '#22c55e', '#f59e0b']
 *   });
 *
 *   // Загрузить плитки
 *   widget.loadBlocks();
 *
 * Полная документация: docs/segment_blocks_widget.md
 *
 * @version 1.0.0
 * @author SurgIl Dashboard Team
 */

class SegmentBlocksWidget {
  constructor(config) {
    this.wellId = config.wellId || 0;
    this.wellNumber = config.wellNumber || '';
    this.apiBase = config.apiBase || '/api/customer-daily/blocks';
    this.panelSelector = config.panelSelector || '#saved-blocks-panel';
    this.gridSelector = config.gridSelector || '#saved-blocks-grid';
    this.chapterFilter = config.chapterFilter || null;
    this.kinds = config.kinds || ['segment_analysis', 'segment_comparison'];
    this.onBlockSaved = config.onBlockSaved || (() => {});
    this.onBlockDeleted = config.onBlockDeleted || (() => {});

    // Bind methods for use as callbacks
    this.deleteBlock = this.deleteBlock.bind(this);
    this.updateBlockInReport = this.updateBlockInReport.bind(this);
    this.updateBlockSetting = this.updateBlockSetting.bind(this);

    // Expose methods globally for onclick handlers in rendered HTML
    window.segmentBlocksWidget_deleteBlock = this.deleteBlock;
    window.segmentBlocksWidget_updateBlockInReport = this.updateBlockInReport;
    window.segmentBlocksWidget_updateBlockSetting = this.updateBlockSetting;
  }

  // ─────────────────────────────────────────────────────────────
  // API: Сохранение анализа
  // ─────────────────────────────────────────────────────────────

  /**
   * Сохранить сегментный анализ как блок
   * @param {Object} dataGetters - объект с getter-функциями для данных
   */
  async saveAnalysis(dataGetters) {
    try {
      const snapshot = this._buildAnalysisSnapshot(dataGetters);
      const period = dataGetters.period || {};
      const defaultTitle = `Сегментный анализ ${period.from || ''} — ${period.to || ''}`;
      const title = prompt('Название блока:', defaultTitle);
      if (!title) return null;

      const body = {
        well_id: this.wellId,
        kind: 'segment_analysis',
        title: title,
        params: {
          period: period,
          series: dataGetters.series || 'flow_rate'
        },
        data_snapshot: snapshot,
        in_report: true
      };

      if (this.chapterFilter) {
        body.params.chapter = this.chapterFilter;
      }

      const resp = await fetch(this.apiBase, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });

      const result = await resp.json();
      if (result.ok || result.id) {
        alert('Анализ сохранён как блок отчёта');
        this.loadBlocks();
        this.onBlockSaved(result);
        return result;
      } else {
        alert('Ошибка сохранения: ' + (result.error || 'неизвестная ошибка'));
        return null;
      }
    } catch (err) {
      console.error('saveAnalysis error:', err);
      alert('Ошибка сохранения: ' + err.message);
      return null;
    }
  }

  /**
   * Сохранить сравнение сегментов как блок
   * @param {Object} dataGetters - объект с getter-функциями для данных
   */
  async saveComparison(dataGetters) {
    try {
      const selectedSet = typeof dataGetters.selectedForCompare === 'function'
        ? dataGetters.selectedForCompare()
        : dataGetters.selectedForCompare;

      if (!selectedSet || selectedSet.size < 2) {
        alert('Выберите минимум 2 сегмента для сравнения');
        return null;
      }

      const snapshot = this._buildComparisonSnapshot(dataGetters);
      const segNums = Array.from(selectedSet).sort((a, b) => a - b).join(', ');
      const defaultTitle = `Сравнение сегментов ${segNums}`;
      const title = prompt('Название блока:', defaultTitle);
      if (!title) return null;

      const body = {
        well_id: this.wellId,
        kind: 'segment_comparison',
        title: title,
        params: {
          segments: Array.from(selectedSet)
        },
        data_snapshot: snapshot,
        in_report: true
      };

      if (this.chapterFilter) {
        body.params.chapter = this.chapterFilter;
      }

      const resp = await fetch(this.apiBase, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });

      const result = await resp.json();
      if (result.ok || result.id) {
        alert('Сравнение сохранено как блок отчёта');
        this.loadBlocks();
        this.onBlockSaved(result);
        return result;
      } else {
        alert('Ошибка сохранения: ' + (result.error || 'неизвестная ошибка'));
        return null;
      }
    } catch (err) {
      console.error('saveComparison error:', err);
      alert('Ошибка сохранения: ' + err.message);
      return null;
    }
  }

  // ─────────────────────────────────────────────────────────────
  // API: Загрузка и отображение блоков
  // ─────────────────────────────────────────────────────────────

  /**
   * Загрузить и отобразить сохранённые блоки
   */
  async loadBlocks() {
    try {
      if (!this.wellId) return;

      let url = `${this.apiBase}?well_id=${this.wellId}`;
      if (this.chapterFilter) {
        url += `&chapter=${encodeURIComponent(this.chapterFilter)}`;
      }

      const resp = await fetch(url);
      const data = await resp.json();
      const blocks = (data.blocks || []).filter(b => this.kinds.includes(b.kind));

      const panel = document.querySelector(this.panelSelector);
      const grid = document.querySelector(this.gridSelector);

      if (!panel || !grid) {
        console.warn('SegmentBlocksWidget: panel or grid element not found');
        return;
      }

      if (blocks.length === 0) {
        panel.style.display = 'none';
        return;
      }

      panel.style.display = 'block';
      grid.innerHTML = blocks.map(b => this._renderBlockCard(b)).join('');
    } catch (err) {
      console.error('loadBlocks error:', err);
    }
  }

  /**
   * Обновить настройку display_settings (локально в localStorage)
   */
  updateBlockSetting(blockId, key, value) {
    const localKey = `block_${blockId}_settings`;
    const settings = JSON.parse(localStorage.getItem(localKey) || '{}');
    settings[key] = value;
    localStorage.setItem(localKey, JSON.stringify(settings));
  }

  /**
   * Обновить in_report через API
   */
  async updateBlockInReport(blockId, value) {
    try {
      await fetch(`${this.apiBase}/${blockId}`, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ in_report: value })
      });
    } catch (err) {
      console.error('updateBlockInReport error:', err);
    }
  }

  /**
   * Удалить блок
   */
  async deleteBlock(blockId) {
    if (!confirm('Удалить блок?')) return;
    try {
      await fetch(`${this.apiBase}/${blockId}`, { method: 'DELETE' });
      this.loadBlocks();
      this.onBlockDeleted(blockId);
    } catch (err) {
      console.error('deleteBlock error:', err);
      alert('Ошибка удаления: ' + err.message);
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Private: Сборка снапшотов
  // ─────────────────────────────────────────────────────────────

  _buildAnalysisSnapshot(dataGetters) {
    const chartData = typeof dataGetters.chartData === 'function'
      ? dataGetters.chartData() : dataGetters.chartData;
    const segments = typeof dataGetters.segments === 'function'
      ? dataGetters.segments() : dataGetters.segments;
    const changepoints = typeof dataGetters.changepoints === 'function'
      ? dataGetters.changepoints() : dataGetters.changepoints;
    const eventsData = typeof dataGetters.eventsData === 'function'
      ? dataGetters.eventsData() : dataGetters.eventsData;
    const period = dataGetters.period || {};

    // Нормализуем chart_data из segment-demo формата в customer-daily формат
    const normalizedChartData = this._normalizeChartData(chartData);
    const chartDates = normalizedChartData?.dates || [];

    // Нормализуем сегменты для поддержки обоих рендереров
    const normalizedSegments = this._normalizeSegments(segments, chartDates);

    return {
      _v: "segment_analysis_v2",
      schema_version: "2.0",
      computed_at: new Date().toISOString(),
      ok: true,
      well_id: this.wellId,
      well_number: this.wellNumber,
      // Верхний уровень для customer_daily.html
      date_from: period.from || null,
      date_to: period.to || null,
      days_count: chartDates.length || 0,
      n_points: chartDates.length || 0,
      // Вложенный период для observation_chapter_renderer.py
      period: period,
      // Нормализованные данные
      chart_data: normalizedChartData,
      segments_extended: normalizedSegments,
      cp_marks: (changepoints || []).map((cpIdx, i) => ({
        idx: cpIdx,
        date: chartDates[cpIdx] || null,
        tag: `CP${i + 1}`,
        source: "only_total"
      })),
      changepoints: changepoints || [],
      injections_table: this._buildInjectionsTable(eventsData, normalizedChartData, normalizedSegments),
      interpretation: {
        summary: typeof dataGetters.generateSummary === 'function'
          ? dataGetters.generateSummary() : '',
        descriptions: (normalizedSegments || []).map((seg, i) =>
          `Сегмент ${i + 1}: ${seg.type || seg.segment_type || 'неизвестно'}, среднее ${(seg.mean_q || seg.mean_value || 0).toFixed(2)}`
        )
      },
      display_settings: {
        show_chart: true,
        show_injections_table: true,
        show_interpretation: true,
        in_report: true
      }
    };
  }

  /**
   * Нормализация chart_data из segment-demo формата в customer-daily формат.
   * segment-demo: {dates, primary: {name, values}, secondary: {col: {name, values}}}
   * customer-daily: {dates, q_total, q_working, shutdown_min, p_wellhead, p_flowline, dp}
   */
  _normalizeChartData(chartData) {
    if (!chartData) return null;

    // Если уже в формате customer-daily (есть q_total) — возвращаем как есть
    if (chartData.q_total) return chartData;

    const result = {
      dates: chartData.dates || [],
    };

    // Primary → q_total
    if (chartData.primary && chartData.primary.values) {
      result.q_total = chartData.primary.values;
    }

    // Secondary ряды
    const sec = chartData.secondary || {};
    for (const [colName, colData] of Object.entries(sec)) {
      if (!colData || !colData.values) continue;
      const vals = colData.values;

      if (colName.includes('flow') || colName === 'flow_rate_mean') {
        if (!result.q_working) result.q_working = vals;
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
  }

  /**
   * Нормализация сегментов для поддержки обоих рендереров:
   * - observation_chapter_renderer.py ожидает: type, start_date, end_date, mean_value, slope
   * - customer_daily.html ожидает: type, start, end, mean_q, slope_total, change_pct
   */
  _normalizeSegments(segments, chartDates) {
    if (!segments || !segments.length) return [];

    const isoToDMY = (iso) => {
      if (!iso) return '?';
      if (/^\d{2}\.\d{2}\.\d{4}$/.test(iso)) return iso;
      const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
      return m ? `${m[3]}.${m[2]}.${m[1]}` : String(iso);
    };

    return segments.map((seg, idx) => {
      // Даты: вычисляем из chartDates если нет в сегменте
      let startDateIso = seg.start_date;
      let endDateIso = seg.end_date;
      if (!startDateIso && chartDates.length > 0 && seg.start_idx !== undefined) {
        startDateIso = chartDates[seg.start_idx] || null;
      }
      if (!endDateIso && chartDates.length > 0 && seg.end_idx !== undefined) {
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

      // Дни
      const days = seg.days !== undefined ? seg.days :
                   (seg.end_idx !== undefined && seg.start_idx !== undefined
                    ? seg.end_idx - seg.start_idx : null);

      return {
        num: seg.num || (idx + 1),
        start_idx: seg.start_idx,
        end_idx: seg.end_idx,
        // ISO формат (observation_chapter_renderer.py)
        start_date: startDateIso,
        end_date: endDateIso,
        // DD.MM.YYYY формат (customer_daily.html / chapter_render.js)
        start: seg.start || isoToDMY(startDateIso),
        end: seg.end || isoToDMY(endDateIso),
        days: days,
        // Тип режима — оба формата
        type: typeVal,
        segment_type: typeVal,
        color: seg.color,
        // Средние — оба формата
        mean_value: meanVal,
        mean_q: meanVal,
        std_value: seg.std_value || seg.std,
        mean_q_working: seg.mean_q_working,
        mean_dp: seg.mean_dp,
        mean_p_wellhead: seg.mean_p_wellhead,
        mean_p_flowline: seg.mean_p_flowline,
        mean_choke_mm: seg.mean_choke_mm,
        mean_shutdown: seg.mean_shutdown,
        // Тренд — оба формата
        slope: slopeVal,
        slope_total: slopeVal,
        intercept: seg.intercept,
        intercept_total: seg.intercept_total || seg.intercept,
        r_squared: seg.r_squared,
        // Изменение %
        change_pct: seg.change_pct || seg.drift_pct,
        change_abs: seg.change_abs,
        change_q_working_pct: seg.change_q_working_pct,
        change_dp_pct: seg.change_dp_pct,
        change_shutdown: seg.change_shutdown,
        change_choke: seg.change_choke,
        // Рабочее время
        working_pct: seg.working_pct,
        // Причина и флаги
        cause: seg.cause,
        trend_direction: seg.trend_direction,
        gradual_trend: seg.gradual_trend,
        gradual_drift_pct: seg.gradual_drift_pct,
        preshutdown_q: seg.preshutdown_q,
        preshutdown_delta_pct: seg.preshutdown_delta_pct,
        preshutdown_verdict: seg.preshutdown_verdict,
        // Min/max для comparison
        min_value: seg.min_value || seg.min,
        max_value: seg.max_value || seg.max,
      };
    });
  }

  _buildComparisonSnapshot(dataGetters) {
    const chartData = typeof dataGetters.chartData === 'function'
      ? dataGetters.chartData() : dataGetters.chartData;
    const segments = typeof dataGetters.segments === 'function'
      ? dataGetters.segments() : dataGetters.segments;
    const selectedSet = typeof dataGetters.selectedForCompare === 'function'
      ? dataGetters.selectedForCompare() : dataGetters.selectedForCompare;
    const compareColors = dataGetters.compareColors ||
      ['#8b5cf6', '#0891b2', '#22c55e', '#f59e0b', '#ef4444', '#6366f1'];

    // Нормализуем chartData
    const normalizedChartData = this._normalizeChartData(chartData);
    const chartDates = normalizedChartData?.dates || chartData?.dates || [];

    // Получаем значения Q (для overlay графика)
    const qValues = normalizedChartData?.q_total ||
                    (chartData?.primary?.values) || [];

    const isoToDMY = (iso) => {
      if (!iso) return '?';
      if (/^\d{2}\.\d{2}\.\d{4}$/.test(iso)) return iso;
      const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
      return m ? `${m[3]}.${m[2]}.${m[1]}` : String(iso);
    };

    const compared = Array.from(selectedSet).map(num => {
      const seg = segments.find(s => s.num === num);
      if (!seg) return null;

      // Вычисляем даты из индексов если нет
      let startDateIso = seg.start_date;
      let endDateIso = seg.end_date;
      if (!startDateIso && chartDates.length > 0 && seg.start_idx !== undefined) {
        startDateIso = chartDates[seg.start_idx] || null;
      }
      if (!endDateIso && chartDates.length > 0 && seg.end_idx !== undefined) {
        const endIdx = Math.max(0, (seg.end_idx || 1) - 1);
        endDateIso = chartDates[Math.min(endIdx, chartDates.length - 1)] || null;
      }

      const meanVal = seg.mean_value !== undefined ? seg.mean_value :
                      (seg.mean_q !== undefined ? seg.mean_q : null);
      const stdVal = seg.std_value !== undefined ? seg.std_value :
                     (seg.std !== undefined ? seg.std : null);
      const slopeVal = seg.slope !== undefined ? seg.slope :
                       (seg.slope_total !== undefined ? seg.slope_total : null);

      return {
        segment_num: num,
        period: {
          from: startDateIso,
          to: endDateIso
        },
        // Также в DD.MM.YYYY формате
        start: isoToDMY(startDateIso),
        end: isoToDMY(endDateIso),
        color: compareColors[(num - 1) % compareColors.length],
        label: `Сегмент ${num}`,
        mean_value: meanVal,
        std_value: stdVal,
        min_value: seg.min_value || seg.min,
        max_value: seg.max_value || seg.max,
        slope: slopeVal,
        intercept: seg.intercept,
        // Дополнительные метрики для таблицы сравнения
        type: seg.segment_type || seg.type || 'unknown',
        days: seg.days || (seg.end_idx && seg.start_idx ? seg.end_idx - seg.start_idx : null),
        working_pct: seg.working_pct,
        mean_dp: seg.mean_dp,
      };
    }).filter(s => s !== null);

    // Chart overlay data
    const chartOverlay = {
      normalized_x: [],
      series: []
    };

    compared.forEach(seg => {
      const fullSeg = segments.find(s => s.num === seg.segment_num);
      if (!fullSeg) return;
      const vals = qValues.slice(fullSeg.start_idx, fullSeg.end_idx);
      chartOverlay.series.push({
        segment_num: seg.segment_num,
        color: seg.color,
        values: vals
      });
      if (vals.length > chartOverlay.normalized_x.length) {
        chartOverlay.normalized_x = Array.from({length: vals.length}, (_, i) => i);
      }
    });

    // Diff table
    const diffTable = {
      metrics: ['mean', 'std', 'min', 'max', 'slope'],
      values: compared.map(s => ({
        segment_num: s.segment_num,
        mean: s.mean_value,
        std: s.std_value,
        min: s.min_value,
        max: s.max_value,
        slope: s.slope
      })),
      deltas: {}
    };

    if (compared.length === 2) {
      const s1 = compared[0], s2 = compared[1];
      const safeNum = (v) => (v != null && !isNaN(v)) ? Number(v) : 0;
      diffTable.deltas = {
        mean: {
          abs: safeNum(s2.mean_value) - safeNum(s1.mean_value),
          pct: s1.mean_value ? ((safeNum(s2.mean_value) - safeNum(s1.mean_value)) / safeNum(s1.mean_value) * 100).toFixed(1) : null
        },
        std: { abs: (safeNum(s2.std_value) - safeNum(s1.std_value)).toFixed(2), pct: null },
        min: { abs: (safeNum(s2.min_value) - safeNum(s1.min_value)).toFixed(2), pct: null },
        max: { abs: (safeNum(s2.max_value) - safeNum(s1.max_value)).toFixed(2), pct: null }
      };
    }

    return {
      _v: "segment_comparison_v2",
      schema_version: "2.0",
      computed_at: new Date().toISOString(),
      ok: true,  // Добавляем ok для проверки в рендерере
      well_id: this.wellId,
      well_number: this.wellNumber,
      segments_compared: compared,
      chart_overlay: chartOverlay,
      diff_table: diffTable,
      interpretation: {
        summary: `Сравнение ${compared.length} сегментов`
      },
      display_settings: {
        show_chart: true,
        show_diff_table: true,
        show_interpretation: true,
        in_report: true
      }
    };
  }

  _buildInjectionsTable(eventsData, chartData, segments) {
    const result = { total_count: 0, by_reagent: {}, by_segment: [], events: [] };
    if (!eventsData || !eventsData.timeline_injections) return result;
    if (!chartData || !chartData.dates || !segments) return result;

    const normalizeDate = (d) => d ? d.replace('T', ' ').substring(0, 16) : '';

    segments.forEach(seg => {
      const startIdx = seg.start_idx;
      const endIdx = Math.min(seg.end_idx, chartData.dates.length) - 1;
      const startDate = normalizeDate(chartData.dates[startIdx]);
      const endDate = normalizeDate(chartData.dates[endIdx]);

      const segInj = eventsData.timeline_injections.filter(ev => {
        if (!ev.t) return false;
        const evNorm = normalizeDate(ev.t);
        return evNorm >= startDate && evNorm <= endDate;
      });

      const segReagents = new Set();
      segInj.forEach(inj => {
        result.total_count++;
        const reagent = inj.reagent || 'Неизвестно';
        segReagents.add(reagent);
        if (!result.by_reagent[reagent]) {
          result.by_reagent[reagent] = { count: 0, total_kg: 0 };
        }
        result.by_reagent[reagent].count++;
        result.by_reagent[reagent].total_kg += inj.amount || 0;
        result.events.push({
          date: inj.t,
          segment_num: seg.num,
          reagent: reagent,
          amount_kg: inj.amount || 0
        });
      });

      result.by_segment.push({
        segment_num: seg.num,
        count: segInj.length,
        reagents: Array.from(segReagents)
      });
    });

    return result;
  }

  // ─────────────────────────────────────────────────────────────
  // Private: Рендер карточки блока
  // ─────────────────────────────────────────────────────────────

  _renderBlockCard(block) {
    const s = block.data_snapshot || {};
    const ds = s.display_settings || {};
    const kindLabel = block.kind === 'segment_analysis' ? 'Анализ' : 'Сравнение';
    const kindClass = `kind-${block.kind}`;

    let metrics = '';
    if (block.kind === 'segment_analysis') {
      const segCount = (s.segments_extended || []).length;
      const injCount = s.injections_table?.total_count || 0;
      metrics = `<span>${segCount} сегм.</span><span>${injCount} вбросов</span>`;
    } else {
      const cmpCount = (s.segments_compared || []).length;
      metrics = `<span>${cmpCount} сегм. сравнения</span>`;
    }

    const period = s.period ? `${s.period.from || ''} — ${s.period.to || ''}` : '';

    return `
      <div class="saved-block-card ${kindClass}" data-block-id="${block.id}">
        <div class="block-header">
          <span class="block-kind-badge">${kindLabel}</span>
          <span class="block-period">${period}</span>
        </div>
        <div class="block-title">${block.title || 'Без названия'}</div>
        <div class="block-metrics">${metrics}</div>

        <details>
          <summary>⚙️ Настройки</summary>
          <div class="block-settings">
            <label class="checkbox-row">
              <input type="checkbox" data-setting="show_chart" ${ds.show_chart !== false ? 'checked' : ''}
                     onchange="segmentBlocksWidget_updateBlockSetting(${block.id}, 'show_chart', this.checked)">
              Показать график
            </label>
            ${block.kind === 'segment_analysis' ? `
            <label class="checkbox-row">
              <input type="checkbox" data-setting="show_injections_table" ${ds.show_injections_table !== false ? 'checked' : ''}
                     onchange="segmentBlocksWidget_updateBlockSetting(${block.id}, 'show_injections_table', this.checked)">
              Показать таблицу вбросов
            </label>
            ` : `
            <label class="checkbox-row">
              <input type="checkbox" data-setting="show_diff_table" ${ds.show_diff_table !== false ? 'checked' : ''}
                     onchange="segmentBlocksWidget_updateBlockSetting(${block.id}, 'show_diff_table', this.checked)">
              Показать таблицу различий
            </label>
            `}
            <label class="checkbox-row">
              <input type="checkbox" data-setting="show_interpretation" ${ds.show_interpretation !== false ? 'checked' : ''}
                     onchange="segmentBlocksWidget_updateBlockSetting(${block.id}, 'show_interpretation', this.checked)">
              Показать интерпретацию
            </label>
            <label class="checkbox-row">
              <input type="checkbox" data-setting="in_report" ${block.in_report !== false ? 'checked' : ''}
                     onchange="segmentBlocksWidget_updateBlockInReport(${block.id}, this.checked)">
              Включить в отчёт
            </label>
          </div>
        </details>

        <div class="block-actions">
          <button class="btn btn-sm btn-danger" onclick="segmentBlocksWidget_deleteBlock(${block.id})">🗑️ Удалить</button>
        </div>
      </div>
    `;
  }

  // ─────────────────────────────────────────────────────────────
  // Static: CSS для карточек блоков
  // ─────────────────────────────────────────────────────────────

  /**
   * Вставить CSS стили для виджета.
   * Вызывать один раз при загрузке страницы.
   */
  static injectStyles() {
    if (document.getElementById('segment-blocks-widget-styles')) return;

    const style = document.createElement('style');
    style.id = 'segment-blocks-widget-styles';
    style.textContent = `
      /* Панель сохранённых блоков */
      .saved-blocks-panel {
        margin-top: 20px;
        padding: 16px;
        background: #f9fafb;
        border-radius: 8px;
        border: 1px solid #e5e7eb;
      }
      .saved-blocks-panel h3 {
        margin: 0 0 12px 0;
        font-size: 16px;
        color: #1f2937;
      }
      .saved-blocks-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
        gap: 12px;
      }
      .saved-block-card {
        background: #fff;
        border: 2px solid #e5e7eb;
        border-left: 5px solid #5b21b6;
        border-radius: 8px;
        padding: 12px;
        transition: box-shadow 0.2s;
      }
      .saved-block-card:hover {
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
      }
      .saved-block-card.kind-segment_comparison {
        border-left-color: #0891b2;
      }
      .saved-block-card .block-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 6px;
      }
      .saved-block-card .block-kind-badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 10px;
        font-weight: 600;
        text-transform: uppercase;
        background: #5b21b6;
        color: #fff;
      }
      .saved-block-card.kind-segment_comparison .block-kind-badge {
        background: #0891b2;
      }
      .saved-block-card .block-period {
        font-size: 11px;
        color: #6b7280;
      }
      .saved-block-card .block-title {
        font-weight: 600;
        font-size: 14px;
        color: #1f2937;
        margin-bottom: 6px;
      }
      .saved-block-card .block-metrics {
        display: flex;
        gap: 12px;
        margin: 8px 0;
        font-size: 12px;
        color: #6b7280;
      }
      .saved-block-card .block-metrics span {
        background: #f3f4f6;
        padding: 2px 8px;
        border-radius: 4px;
      }
      .saved-block-card details {
        margin-top: 8px;
        font-size: 13px;
      }
      .saved-block-card details summary {
        cursor: pointer;
        color: #4b5563;
        font-size: 12px;
      }
      .saved-block-card .block-settings {
        padding: 8px 0;
      }
      .saved-block-card .checkbox-row {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 4px 0;
        font-size: 12px;
      }
      .saved-block-card .checkbox-row input[type="checkbox"] {
        width: 14px;
        height: 14px;
      }
      .saved-block-card .block-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
        margin-top: 10px;
        padding-top: 10px;
        border-top: 1px solid #e5e7eb;
      }
    `;
    document.head.appendChild(style);
  }
}

// Auto-inject styles when module loads
if (typeof document !== 'undefined') {
  SegmentBlocksWidget.injectStyles();
}

// Export for ES modules (if used)
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { SegmentBlocksWidget };
}
