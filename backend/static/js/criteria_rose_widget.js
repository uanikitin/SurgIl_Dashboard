/**
 * CriteriaRoseWidget — модуль диагностики по критериям (Роза критериев).
 *
 * Функциональность:
 *   - Расчёт 6 критериев относительно исторической нормы скважины
 *   - Отображение полярной розы (Plotly) и bar-chart вкладов
 *   - Таблица метрик с цветовой индикацией зон
 *   - Сохранение результата как блока отчёта (kind='criteria_rose')
 *
 * Быстрый старт:
 *   const widget = new CriteriaRoseWidget({
 *     wellId: 123,
 *     apiBase: '/api/customer-daily',
 *     selectors: {
 *       periodFrom: '#cd-date-from',
 *       periodTo: '#cd-date-to',
 *       wellSelect: '#cd-well-select',
 *       modeSelect: '#rose-mode',
 *       calcBtn: '#rose-calc-btn',
 *       // ... остальные селекторы
 *     },
 *     onBlockSaved: (block) => refreshAttachedBlocks()
 *   });
 *
 *   widget.init();  // Инициализация event listeners
 *
 * Полная документация: docs/criteria_rose_widget.md
 *
 * @version 1.0.0
 * @author SurgIl Dashboard Team
 */

class CriteriaRoseWidget {
  // Константы критериев
  static SCORING_KEYS = ['decline', 'p_wh_down', 'p_wh_cv', 'p_fl_up', 'shutdown', 'freq'];

  static SCORING_LABELS = {
    decline: 'Снижение Q',
    p_wh_down: 'Снижение P устья',
    p_wh_cv: 'Волатильность P устья',
    p_fl_up: 'Рост P шлейфа',
    shutdown: 'Доля простоя',
    freq: 'Частота простоев'
  };

  static SCORING_LABELS_SHORT = {
    decline: 'Q ↓',
    p_wh_down: 'P уст ↓',
    p_wh_cv: 'P уст волат.',
    p_fl_up: 'P шл ↑',
    shutdown: 'Простой %',
    freq: 'Эпизоды/30д'
  };

  static SCORING_MODES = {
    balanced: { label: 'balanced — сбалансированный', weights: { decline: 0.35, p_wh_down: 0.20, p_wh_cv: 0.10, p_fl_up: 0.15, shutdown: 0.10, freq: 0.10 } },
    liquid: { label: 'liquid — обводнение / жидкость', weights: { decline: 0.40, p_wh_down: 0.30, p_wh_cv: 0.10, p_fl_up: 0.00, shutdown: 0.10, freq: 0.10 } },
    gsp: { label: 'gsp — газ-сепаратор / шлейф', weights: { decline: 0.35, p_wh_down: 0.05, p_wh_cv: 0.05, p_fl_up: 0.40, shutdown: 0.10, freq: 0.05 } },
    purge_cycles: { label: 'purge_cycles — продувочные циклы', weights: { decline: 0.35, p_wh_down: 0.15, p_wh_cv: 0.25, p_fl_up: 0.00, shutdown: 0.10, freq: 0.15 } }
  };

  constructor(config) {
    this.wellId = config.wellId || null;
    this.apiBase = config.apiBase || '/api/customer-daily';
    this.blocksApiBase = config.blocksApiBase || '/api/customer-daily/blocks';

    // Getters для динамических значений
    this.getWellId = config.getWellId || (() => this.wellId);
    this.getWellNumber = config.getWellNumber || (() => null);
    this.getPeriodFrom = config.getPeriodFrom || (() => null);
    this.getPeriodTo = config.getPeriodTo || (() => null);
    this.getMode = config.getMode || (() => 'balanced');

    // Селекторы UI элементов
    this.selectors = Object.assign({
      // Inputs
      periodFrom: '#rose-period-from',
      periodTo: '#rose-period-to',
      wellSelect: '#rose-well-select',
      modeSelect: '#rose-mode',
      // Buttons
      calcBtn: '#rose-calc-btn',
      saveBtn: '#rose-save-btn',
      // Containers
      resultContainer: '#rose-result',
      emptyContainer: '#rose-empty',
      emptyMsg: '#rose-empty-msg',
      statusText: '#rose-status',
      periodBadge: '#rose-period-badge',
      // Charts
      polarChart: '#rose-chart',
      contribChart: '#rose-contributions',
      // Score display
      scoreValue: '#rose-score-value',
      scoreBar: '#rose-score-bar',
      scoreMeta: '#rose-meta',
      // Table & warnings
      metricsTable: '#rose-table',
      warningsContainer: '#rose-warnings',
      // Save form
      saveTitle: '#rose-save-title',
      saveComment: '#rose-save-comment',
      saveStatus: '#rose-save-status'
    }, config.selectors || {});

    // Callbacks
    this.onBlockSaved = config.onBlockSaved || (() => {});
    this.onCalculated = config.onCalculated || (() => {});
    this.createBlock = config.createBlock || null;  // Внешняя функция создания блока

    // State
    this.lastResult = null;

    // Bind methods
    this.calculate = this.calculate.bind(this);
    this.save = this.save.bind(this);
  }

  // ─────────────────────────────────────────────────────────────
  // Helpers
  // ─────────────────────────────────────────────────────────────

  $(selector) {
    if (selector.startsWith('#')) {
      return document.getElementById(selector.slice(1));
    }
    return document.querySelector(selector);
  }

  _setStatus(text, color = '#6b7280') {
    const el = this.$(this.selectors.statusText);
    if (el) {
      el.textContent = text;
      el.style.color = color;
    }
  }

  _showResult() {
    const result = this.$(this.selectors.resultContainer);
    const empty = this.$(this.selectors.emptyContainer);
    if (result) result.style.display = '';
    if (empty) empty.style.display = 'none';
  }

  _showEmpty(msg) {
    const result = this.$(this.selectors.resultContainer);
    const empty = this.$(this.selectors.emptyContainer);
    const emptyMsg = this.$(this.selectors.emptyMsg);
    if (result) result.style.display = 'none';
    if (empty) empty.style.display = '';
    if (emptyMsg) emptyMsg.textContent = msg;
  }

  // ─────────────────────────────────────────────────────────────
  // API: Initialization
  // ─────────────────────────────────────────────────────────────

  /**
   * Инициализация виджета: привязка event listeners
   */
  init() {
    const calcBtn = this.$(this.selectors.calcBtn);
    if (calcBtn) {
      calcBtn.addEventListener('click', this.calculate);
    }

    const saveBtn = this.$(this.selectors.saveBtn);
    if (saveBtn) {
      saveBtn.addEventListener('click', this.save);
    }

    // Обновление бейджа периода при изменении полей
    const periodFields = [
      this.selectors.periodFrom,
      this.selectors.periodTo,
      this.selectors.wellSelect
    ];
    periodFields.forEach(sel => {
      const el = this.$(sel);
      if (el) {
        el.addEventListener('change', () => this.updatePeriodBadge());
      }
    });

    this.updatePeriodBadge();
    CriteriaRoseWidget.injectStyles();
  }

  /**
   * Обновить бейдж с информацией о периоде
   */
  updatePeriodBadge() {
    const badge = this.$(this.selectors.periodBadge);
    if (!badge) return;

    const f = this.getPeriodFrom() || this.$(this.selectors.periodFrom)?.value;
    const t = this.getPeriodTo() || this.$(this.selectors.periodTo)?.value;
    const w = this.getWellNumber() || this.$(this.selectors.wellSelect)?.value;

    if (f && t && w) {
      badge.textContent = `скв.${w} · ${f} … ${t}`;
    } else {
      badge.textContent = 'выберите скважину и период';
    }
  }

  // ─────────────────────────────────────────────────────────────
  // API: Calculation
  // ─────────────────────────────────────────────────────────────

  /**
   * Запустить расчёт диагностики
   */
  async calculate() {
    // Получаем параметры
    const periodFrom = this.getPeriodFrom() || this.$(this.selectors.periodFrom)?.value;
    const periodTo = this.getPeriodTo() || this.$(this.selectors.periodTo)?.value;
    const well = this.getWellNumber() || this.$(this.selectors.wellSelect)?.value;
    const mode = this.getMode() || this.$(this.selectors.modeSelect)?.value || 'balanced';

    // Очищаем предыдущее состояние
    this._setStatus('');
    this._showEmpty('');
    const emptyEl = this.$(this.selectors.emptyContainer);
    if (emptyEl) emptyEl.style.display = 'none';

    // Валидация
    if (!well || !periodFrom || !periodTo) {
      this._setStatus('✗ Сначала выберите скважину и период', '#dc2626');
      return null;
    }

    this._setStatus('⏳ Расчёт…', '#6b7280');
    const calcBtn = this.$(this.selectors.calcBtn);
    if (calcBtn) calcBtn.disabled = true;

    try {
      const response = await fetch(`${this.apiBase}/rose/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          well: well,
          period_from: periodFrom,
          period_to: periodTo,
          mode: mode
        })
      });

      let data = null;
      try {
        data = await response.json();
      } catch (_) {
        data = null;
      }

      if (!response.ok) {
        const errMsg = (data && (data.detail || data.error)) || ('HTTP ' + response.status);
        this._showEmpty('✗ ' + errMsg);
        this._setStatus('');
        this.lastResult = null;
        return null;
      }

      if (!data || !data.ok) {
        let msg = (data && data.error) || 'Расчёт невозможен';
        const h = data && data.history;
        if (h && (h.windows_count != null || h.rows_total != null)) {
          msg += ` (история на штуцере ${h.choke_mm ?? '—'} мм: ${h.rows_total ?? 0} дн., окон длины ${h.window_days ?? '?'} = ${h.windows_count ?? 0})`;
        }
        this._showEmpty(msg);
        this._setStatus('');
        this.lastResult = null;
        return null;
      }

      // Успешный результат
      this.lastResult = data;
      this._showResult();
      this._setStatus('✓ Готово', '#10b981');
      this.renderAll(data);

      // Автозаполнение названия блока
      const titleInput = this.$(this.selectors.saveTitle);
      if (titleInput && !titleInput.value) {
        titleInput.value = `Роза критериев · скв.${data.well_number} · ${data.period_from}…${data.period_to} · ${data.mode}`;
      }

      this.onCalculated(data);
      return data;

    } catch (e) {
      this._showEmpty('✗ ' + (e && e.message || e));
      this._setStatus('');
      this.lastResult = null;
      return null;
    } finally {
      if (calcBtn) calcBtn.disabled = false;
    }
  }

  // ─────────────────────────────────────────────────────────────
  // API: Rendering
  // ─────────────────────────────────────────────────────────────

  /**
   * Отрисовать все компоненты
   */
  renderAll(data) {
    this.renderScore(data);
    this.renderPolar(data);
    this.renderContributions(data);
    this.renderTable(data);
    this.renderWarnings(data);
  }

  /**
   * Отрисовать плашку со счётом
   */
  renderScore(data) {
    const score = Number(data.score || 0);

    const scoreValue = this.$(this.selectors.scoreValue);
    if (scoreValue) {
      scoreValue.textContent = score.toFixed(1);
      // Цвет по зонам
      let color = '#28a745';
      if (score >= 75) color = '#d62728';
      else if (score >= 50) color = '#f0c000';
      else if (score >= 25) color = '#3b82f6';
      scoreValue.style.color = color;
    }

    const scoreBar = this.$(this.selectors.scoreBar);
    if (scoreBar) {
      scoreBar.style.width = Math.max(0, Math.min(100, score)) + '%';
    }

    const scoreMeta = this.$(this.selectors.scoreMeta);
    if (scoreMeta) {
      const h = data.history || {};
      const meta = [
        `режим: ${data.mode}`,
        `штуцер: ${h.choke_mm != null ? h.choke_mm + ' мм' : '—'}`,
        `период: ${data.period_days} дн.`,
        `окон в истории: ${h.windows_count}`
      ].join(' · ');
      scoreMeta.textContent = meta;
    }
  }

  /**
   * Отрисовать полярную диаграмму (Plotly)
   */
  renderPolar(data) {
    const container = this.$(this.selectors.polarChart);
    if (!container || typeof Plotly === 'undefined') return;

    const KEYS = CriteriaRoseWidget.SCORING_KEYS;
    const labels = KEYS.map(k => data.labels_short?.[k] || CriteriaRoseWidget.SCORING_LABELS_SHORT[k] || k);
    const ranks = KEYS.map(k => Number(data.ranks?.[k] ?? 0));

    // Слой 1: зона нормы (0..50)
    const layerNorm = {
      type: 'barpolar',
      r: KEYS.map(() => 50),
      theta: labels,
      base: 0,
      width: KEYS.map(() => 60),
      marker: { color: 'rgba(220,225,235,0.55)', line: { width: 0 } },
      hoverinfo: 'skip',
      showlegend: false
    };

    // Слой 2: зона риска (75..100)
    const layerRisk = {
      type: 'barpolar',
      r: KEYS.map(() => 25),
      theta: labels,
      base: 75,
      width: KEYS.map(() => 60),
      marker: { color: 'rgba(214,39,40,0.10)', line: { width: 0 } },
      hoverinfo: 'skip',
      showlegend: false
    };

    // Слой 3: пунктирная линия нормы r=50
    const layerLine = {
      type: 'scatterpolar',
      r: KEYS.map(() => 50).concat([50]),
      theta: labels.concat([labels[0]]),
      mode: 'lines',
      line: { color: '#5a6f8a', width: 1.2, dash: 'dash' },
      hoverinfo: 'skip',
      showlegend: false
    };

    // Слой 4: лепестки
    const redR = [], redBase = [], redTh = [];
    const grnR = [], grnBase = [], grnTh = [];
    KEYS.forEach((k, i) => {
      const rk = Number(data.ranks?.[k] ?? 0);
      if (rk > 50) {
        redR.push(rk - 50);
        redBase.push(50);
        redTh.push(labels[i]);
      } else if (rk < 50) {
        grnR.push(50 - rk);
        grnBase.push(rk);
        grnTh.push(labels[i]);
      }
    });

    const traces = [layerNorm, layerRisk, layerLine];

    if (redR.length) {
      traces.push({
        type: 'barpolar',
        r: redR,
        base: redBase,
        theta: redTh,
        width: redR.map(() => 33),
        marker: { color: '#d62728', opacity: 0.92, line: { color: '#8b0000', width: 0.8 } },
        hoverinfo: 'skip',
        showlegend: false
      });
    }

    if (grnR.length) {
      traces.push({
        type: 'barpolar',
        r: grnR,
        base: grnBase,
        theta: grnTh,
        width: grnR.map(() => 33),
        marker: { color: '#28a745', opacity: 0.78, line: { color: '#155724', width: 0.6 } },
        hoverinfo: 'skip',
        showlegend: false
      });
    }

    // Слой 5: соединяющий полигон
    traces.push({
      type: 'scatterpolar',
      r: ranks.concat([ranks[0]]),
      theta: labels.concat([labels[0]]),
      mode: 'lines+markers',
      line: { color: 'rgba(214,39,40,0.6)', width: 1.2, dash: 'dot' },
      marker: { size: 5, color: '#d62728' },
      hovertemplate: '%{theta}: ранг %{r:.0f}<extra></extra>',
      showlegend: false
    });

    // Слой 6: подписи рангов
    const lblR = KEYS.map(k => {
      const r = Number(data.ranks?.[k] ?? 0);
      return r >= 50 ? Math.min(r + 6, 98) : Math.max(r - 6, 4);
    });
    const lblText = KEYS.map(k => {
      const r = data.ranks?.[k];
      return r != null ? Math.round(r).toString() : '—';
    });
    const lblColors = KEYS.map(k => {
      const r = Number(data.ranks?.[k] ?? 50);
      return r >= 50 ? '#8b0000' : '#155724';
    });

    traces.push({
      type: 'scatterpolar',
      r: lblR,
      theta: labels,
      mode: 'text',
      text: lblText,
      textfont: { size: 11, color: lblColors, family: 'Arial Black' },
      hoverinfo: 'skip',
      showlegend: false
    });

    const layout = {
      polar: {
        radialaxis: {
          range: [0, 100],
          tickvals: [25, 50, 75, 100],
          ticktext: ['25', '50 — норма', '75', '100'],
          tickfont: { size: 10, color: '#777' },
          angle: 30,
          tickangle: 30,
          gridcolor: '#e0e0e0',
          linecolor: '#e0e0e0'
        },
        angularaxis: {
          tickfont: { size: 11, color: '#374151' },
          gridcolor: '#e0e0e0',
          linecolor: '#e0e0e0',
          direction: 'clockwise',
          rotation: 90
        },
        bgcolor: '#fff'
      },
      margin: { t: 24, r: 30, b: 24, l: 30 },
      paper_bgcolor: '#fff',
      title: { text: 'Роза критериев', font: { size: 13, color: '#374151' } }
    };

    Plotly.newPlot(container, traces, layout, {
      displayModeBar: false,
      responsive: true
    });
  }

  /**
   * Отрисовать bar-chart вкладов
   */
  renderContributions(data) {
    const container = this.$(this.selectors.contribChart);
    if (!container || typeof Plotly === 'undefined') return;

    const KEYS = CriteriaRoseWidget.SCORING_KEYS;
    const items = KEYS.map(k => ({
      key: k,
      label: data.labels?.[k] || CriteriaRoseWidget.SCORING_LABELS[k] || k,
      rank: Number(data.contributions?.[k]?.rank ?? 0),
      weight: Number(data.contributions?.[k]?.weight ?? 0),
      actual: Number(data.contributions?.[k]?.actual ?? 0),
      max: Number(data.contributions?.[k]?.max ?? 0)
    })).sort((a, b) => b.actual - a.actual);

    const y = items.map(it => it.label);
    const actual = items.map(it => it.actual);
    const gap = items.map(it => Math.max(0, it.max - it.actual));
    const weights = items.map(it => 'вес ' + it.weight.toFixed(2));

    const traces = [
      {
        type: 'bar',
        orientation: 'h',
        y: y,
        x: actual,
        name: 'набрано',
        marker: { color: '#d62728' },
        text: actual.map(v => v.toFixed(1)),
        textposition: 'inside',
        textfont: { color: '#fff', size: 11, family: 'Arial Black' },
        hovertemplate: '<b>%{y}</b><br>ранг × вес = <b>%{x:.1f}</b><extra></extra>'
      },
      {
        type: 'bar',
        orientation: 'h',
        y: y,
        x: gap,
        name: 'до максимума',
        marker: { color: '#e6e6e6' },
        text: weights,
        textposition: 'outside',
        textfont: { color: '#6b7280', size: 10 },
        hovertemplate: '<b>%{y}</b><br>недобор: %{x:.1f}<extra></extra>'
      }
    ];

    const maxX = Math.max(45, Math.max(...items.map(it => it.max)) * 1.35);
    const layout = {
      barmode: 'stack',
      margin: { t: 30, r: 90, b: 40, l: 150 },
      xaxis: {
        range: [0, maxX],
        title: { text: 'Баллы (ранг × вес)', font: { size: 11 } },
        gridcolor: '#eee',
        zeroline: false
      },
      yaxis: { autorange: 'reversed', tickfont: { size: 11 } },
      showlegend: false,
      paper_bgcolor: '#fff',
      plot_bgcolor: '#fff',
      title: { text: 'Вклад в балл', font: { size: 13, color: '#374151' } }
    };

    Plotly.newPlot(container, traces, layout, {
      displayModeBar: false,
      responsive: true
    });
  }

  /**
   * Отрисовать таблицу метрик
   */
  renderTable(data) {
    const container = this.$(this.selectors.metricsTable);
    if (!container) return;

    const KEYS = CriteriaRoseWidget.SCORING_KEYS;

    const fmtNum = (v, p = 4) => {
      if (v == null || !isFinite(v)) return '—';
      const av = Math.abs(v);
      if (av >= 100) return v.toFixed(1);
      if (av >= 1) return v.toFixed(2);
      if (av >= 0.01) return v.toFixed(3);
      return v.toFixed(p);
    };

    const rows = KEYS.map(k => {
      const rank = data.ranks?.[k];
      const contrib = data.contributions?.[k] || {};
      let cls = 'rose-neutral', label = 'норма';

      if (rank == null) {
        cls = 'rose-na';
        label = 'нет данных';
      } else if (rank > 75) {
        cls = 'rose-bad';
        label = 'риск';
      } else if (rank > 50) {
        cls = 'rose-warn';
        label = 'хуже нормы';
      } else if (rank < 50) {
        cls = 'rose-good';
        label = 'лучше нормы';
      }

      return `
        <tr class="crw-${cls}">
          <td>${data.labels?.[k] || CriteriaRoseWidget.SCORING_LABELS[k] || k}</td>
          <td style="text-align:right; font-family:monospace;">${fmtNum(data.current_raw?.[k])}</td>
          <td style="text-align:right; font-family:monospace; color:#6b7280;">${fmtNum(data.history_median_raw?.[k])}</td>
          <td style="text-align:center; font-size:0.78rem;">${data.units?.[k] || ''}</td>
          <td style="text-align:right; font-weight:600;">${rank == null ? '—' : rank.toFixed(0)}</td>
          <td style="text-align:right; font-family:monospace;">${(contrib.weight ?? 0).toFixed(2)}</td>
          <td style="text-align:right; font-family:monospace; color:#d62728;">${(contrib.actual ?? 0).toFixed(2)}</td>
          <td><span class="crw-pill crw-pill-${cls}">${label}</span></td>
        </tr>`;
    }).join('');

    container.innerHTML = `
      <table class="crw-table">
        <thead><tr>
          <th>Критерий</th>
          <th style="text-align:right;">Текущий период</th>
          <th style="text-align:right;">Медиана истории</th>
          <th style="text-align:center;">Ед.</th>
          <th style="text-align:right;">Ранг 0…100</th>
          <th style="text-align:right;">Вес</th>
          <th style="text-align:right;">Вклад в балл</th>
          <th>Зона</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  /**
   * Отрисовать предупреждения
   */
  renderWarnings(data) {
    const container = this.$(this.selectors.warningsContainer);
    if (!container) return;

    const warnings = (data.warnings || []).filter(Boolean);
    if (!warnings.length) {
      container.innerHTML = '';
      return;
    }

    container.innerHTML = warnings.map(t => `
      <div class="crw-warning">⚠ ${t}</div>
    `).join('');
  }

  // ─────────────────────────────────────────────────────────────
  // API: Saving
  // ─────────────────────────────────────────────────────────────

  /**
   * Получить snapshot для сохранения
   */
  buildSnapshot() {
    return this.lastResult ? JSON.parse(JSON.stringify(this.lastResult)) : null;
  }

  /**
   * Сохранить результат как блок отчёта
   */
  async save() {
    const data = this.lastResult;
    if (!data || !data.ok) {
      alert('Сначала нажмите «Рассчитать диагностику».');
      return null;
    }

    const wellId = this.getWellId();
    if (!wellId) {
      alert('Скважина не определена (нет связи с wells.id).');
      return null;
    }

    const titleInput = this.$(this.selectors.saveTitle);
    const commentInput = this.$(this.selectors.saveComment);
    const statusEl = this.$(this.selectors.saveStatus);

    const title = (titleInput?.value || '').trim() ||
      `Роза критериев · скв.${data.well_number} · ${data.period_from}…${data.period_to}`;

    const params = {
      well_number: data.well_number,
      period_from: data.period_from,
      period_to: data.period_to,
      mode: data.mode,
      weights: data.weights_raw
    };

    const snapshot = this.buildSnapshot();
    const comment = commentInput?.value || '';

    // Если передана внешняя функция создания блока — используем её
    if (this.createBlock) {
      const block = await this.createBlock('criteria_rose', title, params, comment, snapshot);
      if (block) {
        if (statusEl) {
          statusEl.textContent = '✓ Добавлено в отчёт';
          setTimeout(() => { statusEl.textContent = ''; }, 3000);
        }
        this.onBlockSaved(block);
      }
      return block;
    }

    // Иначе используем прямой API вызов
    try {
      const response = await fetch(this.blocksApiBase, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          well_id: wellId,
          kind: 'criteria_rose',
          title: title,
          params: params,
          data_snapshot: snapshot,
          comment: comment,
          in_report: true
        })
      });

      const result = await response.json();
      if (result.ok || result.id) {
        if (statusEl) {
          statusEl.textContent = '✓ Добавлено в отчёт';
          setTimeout(() => { statusEl.textContent = ''; }, 3000);
        }
        this.onBlockSaved(result);
        return result;
      } else {
        alert('Ошибка сохранения: ' + (result.error || 'неизвестная ошибка'));
        return null;
      }
    } catch (err) {
      console.error('CriteriaRoseWidget.save error:', err);
      alert('Ошибка сохранения: ' + err.message);
      return null;
    }
  }

  // ─────────────────────────────────────────────────────────────
  // API: Getters
  // ─────────────────────────────────────────────────────────────

  /**
   * Получить последний результат расчёта
   */
  getLastResult() {
    return this.lastResult;
  }

  /**
   * Проверить, есть ли валидный результат
   */
  hasValidResult() {
    return this.lastResult && this.lastResult.ok;
  }

  // ─────────────────────────────────────────────────────────────
  // Static: CSS injection
  // ─────────────────────────────────────────────────────────────

  static injectStyles() {
    if (document.getElementById('criteria-rose-widget-styles')) return;

    const style = document.createElement('style');
    style.id = 'criteria-rose-widget-styles';
    style.textContent = `
      /* Таблица метрик */
      .crw-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.86rem;
      }
      .crw-table th {
        background: #f3f4f6;
        padding: 6px 8px;
        text-align: left;
        border-bottom: 2px solid #e5e7eb;
        font-weight: 600;
        font-size: 0.82rem;
      }
      .crw-table td {
        padding: 5px 8px;
        border-bottom: 1px solid #f1f5f9;
      }
      .crw-table tr.crw-rose-bad td { background: #fef2f2; }
      .crw-table tr.crw-rose-warn td { background: #fff7ed; }
      .crw-table tr.crw-rose-good td { background: #f0fdf4; }

      /* Пилюли зон */
      .crw-pill {
        display: inline-block;
        padding: 1px 8px;
        border-radius: 10px;
        font-size: 0.72rem;
        font-weight: 600;
      }
      .crw-pill-rose-bad { background: #fecaca; color: #991b1b; }
      .crw-pill-rose-warn { background: #fed7aa; color: #9a3412; }
      .crw-pill-rose-good { background: #bbf7d0; color: #166534; }
      .crw-pill-rose-neutral { background: #e5e7eb; color: #374151; }
      .crw-pill-rose-na { background: #f3f4f6; color: #9ca3af; }

      /* Предупреждения */
      .crw-warning {
        padding: 6px 10px;
        background: #fef3c7;
        border: 1px solid #fde68a;
        border-radius: 6px;
        color: #92400e;
        font-size: 0.85rem;
        margin-bottom: 4px;
      }
    `;
    document.head.appendChild(style);
  }
}

// Auto-inject styles when module loads
if (typeof document !== 'undefined') {
  CriteriaRoseWidget.injectStyles();
}

// Export for ES modules
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { CriteriaRoseWidget };
}
