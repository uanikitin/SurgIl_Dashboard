/**
 * observation_ui.js — UI для конструктора блоков главы «Наблюдение».
 *
 * Подключается в adaptation_wizard.html.
 *
 * Контракт:
 *  - Vanilla JS, без зависимостей (кроме fetch)
 *  - Использует только endpoints /api/observation/*
 *  - НЕ трогает cmp_segments.js и wz3cmp-* пространство
 *  - Изолирован в namespace .wz3obs-*
 *  - Инициализируется автоматически при наличии .wz3obs-constructor в DOM
 *  - Переинициализируется через window.obsUiInit() при навигации по шагам визарда
 */
(function () {
  'use strict';

  // ── STATE ────────────────────────────────────────────────────────────────────

  const state = {
    wellId: null,
    activeKind: 'observation_baseline',
    currentPreview: null,   // snapshot из последнего /preview/*
    blocks: [],             // список из GET /blocks (items)
    baselineBlocks: [],     // фильтрованный список observation_baseline для period form
    timeline: null,         // текущий TimelineBuilder instance
  };

  // ── CONSTANTS ─────────────────────────────────────────────────────────────────

  const KIND_LABELS = {
    observation_baseline:  'Baseline',
    observation_period:    'Period analysis',
    observation_segment:   'Segment analysis',
    observation_analysis:  'Анализ наблюдения',
    segment_analysis:      'Сегментный анализ',
    segment_comparison:    'Сравнение сегментов',
  };

  const STATUS_COLORS = {
    ok:                '#22c55e',
    partial:           '#60a5fa',
    no_data:           '#9ca3af',
    insufficient_data: '#eab308',
    legacy_v0:         '#f97316',
    invalid_schema:    '#ef4444',
    corrupted:         '#991b1b',
  };

  const STATUS_LABELS = {
    ok:                'OK',
    partial:           'Частично',
    no_data:           'Нет данных',
    insufficient_data: 'Мало данных',
    legacy_v0:         'Устаревший',
    invalid_schema:    'Неверная схема',
    corrupted:         'Повреждён',
  };

  // Цветная полоса карточки по kind (см. customer_daily — cd-kind-*).
  const KIND_STRIPE = {
    observation_baseline:  '#d97706',
    observation_period:    '#16a34a',
    observation_segment:   '#5b21b6',
    observation_analysis:  '#6366f1',
    segment_analysis:      '#5b21b6',
    segment_comparison:    '#0891b2',
  };

  // Каталог частей блока «Что включить в PDF».
  // ЗЕРКАЛО backend/constants/observation_parts.py (OBSERVATION_PARTS +
  // OBSERVATION_PART_LABELS). При изменении того файла — синхронизировать здесь.
  const PART_DEFS = {
    observation_baseline: [
      ['prefix_note',   'Вступительный текст'],
      ['intro',         'Период / заголовок'],
      ['metrics_table', 'Таблица метрик'],
      ['quality',       'Качество данных'],
      ['flags',         'Флаги'],
      ['description',   'Текстовое описание'],
      ['suffix_note',   'Заключительный текст'],
    ],
    observation_period: [
      ['prefix_note',              'Вступительный текст'],
      ['intro',                    'Период / заголовок'],
      ['metrics_table',            'Таблица метрик'],
      ['charts',                   'Графики'],
      ['comparison_with_b1',       'Сравнение с baseline (B1)'],
      ['comparison_with_customer', 'Сравнение с заказчиком'],
      ['diagnostics',              'Диагностика'],
      ['daily_table',              'Суточная таблица'],
      ['flags',                    'Флаги'],
      ['description',              'Текстовое описание'],
      ['suffix_note',              'Заключительный текст'],
    ],
    observation_segment: [
      ['prefix_note',    'Вступительный текст'],
      ['intro',          'Период / заголовок'],
      ['chart',          'График'],
      ['segments_table', 'Таблица сегментов'],
      ['changepoints',   'Точки перелома'],
      ['diagnostics',    'Диагностика'],
      ['flags',          'Флаги'],
      ['description',    'Текстовое описание'],
      ['suffix_note',    'Заключительный текст'],
    ],
    // Полный сегментный анализ (из customer_daily)
    segment_analysis: [
      ['q_segment_chart',    'График Q с трендами'],
      ['segments_table',     'Таблица сегментов'],
      ['descriptions',       'Описание сегментов'],
      ['changepoints',       'Точки перелома'],
      ['injections_table',   'Таблица вбросов'],
    ],
    // Сравнение сегментов
    segment_comparison: [
      ['chart',              'Overlay-график'],
      ['diff_table',         'Таблица различий'],
      ['interpretation',     'Интерпретация'],
    ],
  };

  // ── API HELPERS ───────────────────────────────────────────────────────────────

  async function api(method, path, body = null) {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' },
    };
    if (body !== null) opts.body = JSON.stringify(body);
    const resp = await fetch('/api/observation' + path, opts);
    if (!resp.ok) {
      let err;
      try { err = await resp.json(); } catch (_) { err = { error_code: 'network_error', message: 'Network error' }; }
      throw err;
    }
    return resp.json();
  }

  // ── DOM HELPERS ───────────────────────────────────────────────────────────────

  function el(selector) {
    const container = document.querySelector('.wz3obs-constructor');
    if (!container) return null;
    return container.querySelector(selector);
  }

  function setHtml(selector, html) {
    const node = el(selector);
    if (node) node.innerHTML = html;
  }

  function show(selector) {
    const node = el(selector);
    if (node) node.style.display = '';
  }

  function hide(selector) {
    const node = el(selector);
    if (node) node.style.display = 'none';
  }

  function setStatus(msg, isError) {
    const node = el('.wz3obs-status');
    if (!node) return;
    node.textContent = msg;
    node.style.color = isError ? '#dc2626' : '#059669';
  }

  function debounce(fn, ms) {
    let t = null;
    return function (...args) {
      if (t) clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), ms);
    };
  }

  // ── BADGE ─────────────────────────────────────────────────────────────────────

  function makeStatusBadge(status) {
    const color = STATUS_COLORS[status] || '#9ca3af';
    const label = STATUS_LABELS[status] || status;
    return `<span class="wz3obs-badge" style="background:${color}" title="${status}">${label}</span>`;
  }

  // ── FORM RENDERERS ────────────────────────────────────────────────────────────

  function renderBaselineForm() {
    return `
      <div class="wz3obs-timeline-wrap">
        <div id="obs-timeline-bl" class="wz3obs-timeline-container"></div>
        <input type="hidden" id="obs-bl-from">
        <input type="hidden" id="obs-bl-to">
      </div>
      <div class="wz3obs-field-row">
        <label class="wz3obs-label">Комментарий</label>
        <textarea class="wz3obs-textarea" id="obs-bl-comment" rows="2" placeholder="Необязательно"></textarea>
      </div>
      <button type="button" class="wz3obs-btn wz3obs-btn-primary" id="obs-preview-btn">
        Предпросмотр
      </button>
    `;
  }

  function renderPeriodForm() {
    const baselineOptions = state.baselineBlocks.map(b =>
      `<option value="${b.block_id}">${escHtml(b.title)} (${b.created_at ? b.created_at.slice(0, 10) : ''})</option>`
    ).join('');

    return `
      <div class="wz3obs-timeline-wrap">
        <div id="obs-timeline-pd" class="wz3obs-timeline-container"></div>
        <input type="hidden" id="obs-pd-from">
        <input type="hidden" id="obs-pd-to">
      </div>
      <div class="wz3obs-field-row">
        <label class="wz3obs-label">Базовый период (baseline)</label>
        <select class="wz3obs-select" id="obs-pd-baseline">
          <option value="">— без сравнения —</option>
          ${baselineOptions}
        </select>
      </div>
      <div class="wz3obs-field-row">
        <label class="wz3obs-label" style="font-size:0.78rem;color:#6b7280;">
          Данные заказчика
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:0.84rem;">
          <input type="checkbox" id="obs-pd-cust-same" checked>
          Тот же период что анализ
        </label>
      </div>
      <button type="button" class="wz3obs-btn wz3obs-btn-primary" id="obs-preview-btn">
        Предпросмотр
      </button>
    `;
  }

  function renderSegmentForm() {
    return `
      <div class="wz3obs-timeline-wrap">
        <div id="obs-timeline-sg" class="wz3obs-timeline-container"></div>
        <input type="hidden" id="obs-sg-from">
        <input type="hidden" id="obs-sg-to">
      </div>
      <div class="wz3obs-field-row">
        <label class="wz3obs-label">Агрегация</label>
        <select class="wz3obs-select" id="obs-sg-agg">
          <option value="daily" selected>Суточная</option>
          <option value="12h">12 часов</option>
          <option value="6h">6 часов</option>
          <option value="hourly">Почасовая</option>
        </select>
      </div>
      <div class="wz3obs-field-row">
        <label class="wz3obs-label">Чувствительность</label>
        <select class="wz3obs-select" id="obs-sg-sens">
          <option value="low">Низкая</option>
          <option value="medium" selected>Средняя</option>
          <option value="high">Высокая</option>
        </select>
      </div>
      <div class="wz3obs-field-row">
        <label style="display:flex;align-items:center;gap:6px;font-size:0.84rem;">
          <input type="checkbox" id="obs-sg-ignore-shut" checked>
          Игнорировать дни остановок
        </label>
      </div>
      <button type="button" class="wz3obs-btn wz3obs-btn-primary" id="obs-preview-btn">
        Предпросмотр
      </button>
    `;
  }

  function destroyTimeline() {
    if (state.timeline) {
      state.timeline.destroy();
      state.timeline = null;
    }
  }

  function initTimeline(kind) {
    if (!window.TimelineBuilder) {
      console.warn('[observation_ui] TimelineBuilder not loaded');
      return;
    }
    if (!state.wellId) {
      console.warn('[observation_ui] wellId not set, cannot init timeline');
      return;
    }

    let containerId, dateInputs;
    if (kind === 'observation_baseline') {
      containerId = 'obs-timeline-bl';
      dateInputs = { from: 'obs-bl-from', to: 'obs-bl-to' };
    } else if (kind === 'observation_period') {
      containerId = 'obs-timeline-pd';
      dateInputs = { from: 'obs-pd-from', to: 'obs-pd-to' };
    } else if (kind === 'observation_segment') {
      containerId = 'obs-timeline-sg';
      dateInputs = { from: 'obs-sg-from', to: 'obs-sg-to' };
    } else {
      return;
    }

    state.timeline = window.TimelineBuilder.create({
      containerId: containerId,
      wellId: state.wellId,
      dateInputs: dateInputs,
      features: {
        bars: { customer: true, sensors: true },
        stages: true,
        events: true,
        yearFilter: true,
        anchorMode: true,
        zoom: true,
        sourcesBlock: true,  // Показываем блок «Источник периода»
      },
      cssPrefix: 'tl',
      // Callback после загрузки данных - строим список источников
      onLoad: function(data) {
        const sources = buildSourcesFromData(data);
        if (state.timeline) {
          state.timeline.setSources(sources);
        }
      },
    });

    state.timeline.init();
  }

  /**
   * Строит список источников периода из данных API timeline.
   * @param {Object} data - данные от /api/adaptation-report/timeline
   * @returns {Array} массив источников
   */
  function buildSourcesFromData(data) {
    const sources = [];

    // 1. Из таймлайна (выбор вручную на шкале)
    sources.push({
      key: 'timeline',
      label: '📍 Из таймлайна (выбор на шкале выше)',
      from: null,
      to: null,
    });

    // 2. Этапы из well_status (observation, adaptation, etc.)
    const stages = (data && data.stages) || [];
    for (const s of stages) {
      if (s.dt_start) {
        const from = s.dt_start.slice(0, 10);
        const to = s.dt_end ? s.dt_end.slice(0, 10) : null;
        sources.push({
          key: 'stage_' + s.status,
          label: '📋 ' + (s.label || s.status) + ' (из журнала)',
          from: from,
          to: to,
        });
      }
    }

    // 3. События (от установки до первого вброса)
    const events = (data && data.events) || [];
    const installEvent = events.find(e => e.type === 'install' || e.label && e.label.toLowerCase().includes('установк'));
    const firstInjection = events.find(e => e.type === 'injection' || e.label && e.label.toLowerCase().includes('вброс'));
    if (installEvent && firstInjection && installEvent.dt && firstInjection.dt) {
      sources.push({
        key: 'events',
        label: '🔧 От установки до первого вброса',
        from: installEvent.dt.slice(0, 10),
        to: firstInjection.dt.slice(0, 10),
      });
    }

    // 4. Ручной ввод
    sources.push({
      key: 'manual',
      label: '✏️ Указать вручную',
      from: null,
      to: null,
    });

    return sources;
  }

  function renderForm(kind) {
    const container = el('.wz3obs-form');
    if (!container) return;

    // Уничтожаем предыдущий timeline
    destroyTimeline();

    let html = '';
    if (kind === 'observation_baseline') html = renderBaselineForm();
    else if (kind === 'observation_period') html = renderPeriodForm();
    else if (kind === 'observation_segment') html = renderSegmentForm();
    container.innerHTML = html;
    bindPreviewButton();
    hide('.wz3obs-preview-result');
    hide('.wz3obs-save-block');

    // Инициализируем новый timeline
    initTimeline(kind);
  }

  // ── PREVIEW HANDLERS ──────────────────────────────────────────────────────────

  function bindPreviewButton() {
    const btn = el('#obs-preview-btn');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = 'Загрузка…';
      setStatus('', false);
      try {
        if (state.activeKind === 'observation_baseline') await handlePreviewBaseline();
        else if (state.activeKind === 'observation_period') await handlePreviewPeriod();
        else if (state.activeKind === 'observation_segment') await handlePreviewSegment();
      } catch (err) {
        setStatus('Ошибка: ' + (err.message || JSON.stringify(err)), true);
      } finally {
        btn.disabled = false;
        btn.textContent = 'Предпросмотр';
      }
    });
  }

  async function handlePreviewBaseline() {
    const from = el('#obs-bl-from').value;
    const to = el('#obs-bl-to').value;
    if (!from || !to) { setStatus('Укажите период с и по', true); return; }
    const comment = el('#obs-bl-comment').value.trim() || null;
    const resp = await api('POST', '/preview/baseline', {
      well_id: state.wellId,
      period: { from, to },
      sensor_source: 'lora',
      comment,
      include_raw_chart: true,  // включаем данные графиков для правой панели и PDF
    });
    state.currentPreview = resp.snapshot;
    renderPreviewResult(resp.snapshot);
    show('.wz3obs-preview-result');
    show('.wz3obs-save-block');
    el('#obs-save-title').value = '';
    el('#obs-save-comment').value = '';
  }

  async function handlePreviewPeriod() {
    const from = el('#obs-pd-from').value;
    const to = el('#obs-pd-to').value;
    if (!from || !to) { setStatus('Укажите период с и по', true); return; }
    const baselineIdRaw = el('#obs-pd-baseline').value;
    const baseline_block_id = baselineIdRaw ? parseInt(baselineIdRaw) : null;
    const samePeriod = el('#obs-pd-cust-same').checked;
    const customer_period = { use_same_as_analysis: samePeriod };
    const resp = await api('POST', '/preview/period', {
      well_id: state.wellId,
      period: { from, to },
      baseline_block_id,
      customer_period,
      include_raw_chart: true,  // включаем данные графиков для правой панели и PDF
    });
    state.currentPreview = resp.snapshot;
    renderPreviewResult(resp.snapshot);
    show('.wz3obs-preview-result');
    show('.wz3obs-save-block');
    el('#obs-save-title').value = '';
    el('#obs-save-comment').value = '';
  }

  async function handlePreviewSegment() {
    const from = el('#obs-sg-from').value;
    const to = el('#obs-sg-to').value;
    if (!from || !to) { setStatus('Укажите период с и по', true); return; }
    const aggregation = el('#obs-sg-agg').value;
    const sensitivity = el('#obs-sg-sens').value;
    const ignore_shutdown_days = el('#obs-sg-ignore-shut').checked;
    const resp = await api('POST', '/preview/segment', {
      well_id: state.wellId,
      period: { from, to },
      aggregation,
      sensitivity,
      ignore_shutdown_days,
      ignore_purge_window_hours: 24,
      include_raw_chart: true,  // включаем данные графиков для правой панели и PDF
    });
    state.currentPreview = resp.snapshot;
    renderPreviewResult(resp.snapshot);
    show('.wz3obs-preview-result');
    show('.wz3obs-save-block');
    el('#obs-save-title').value = '';
    el('#obs-save-comment').value = '';
  }

  // ── PREVIEW RENDERERS ─────────────────────────────────────────────────────────

  function escHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function fmtNum(v, digits) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    return Number(v).toFixed(digits !== undefined ? digits : 2);
  }

  function renderPreviewResult(snapshot) {
    if (!snapshot) { setHtml('.wz3obs-preview-body', '<p>Нет данных.</p>'); return; }

    const status = snapshot.block_status || 'unknown';
    let html = `<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;">
      <strong>Статус:</strong> ${makeStatusBadge(status)}
    </div>`;

    // Quality
    if (snapshot.quality) {
      html += renderQualityBadge(snapshot.quality);
    }

    // Flags
    if (snapshot.flags && Object.keys(snapshot.flags).length > 0) {
      html += renderFlagsBlock(snapshot.flags);
    }

    // Metrics
    if (snapshot.metrics) {
      html += renderMetricsTable(snapshot.metrics);
    }

    // Comparisons (period kind)
    if (snapshot.comparisons) {
      html += renderComparisons(snapshot.comparisons);
    }

    // Diagnostics (period kind)
    if (Array.isArray(snapshot.diagnostics) && snapshot.diagnostics.length > 0) {
      html += renderDiagnosticsList(snapshot.diagnostics);
    }

    // Segments (segment kind)
    if (Array.isArray(snapshot.segments) && snapshot.segments.length > 0) {
      html += renderSegmentsTable(snapshot.segments);
    }

    // Changepoints
    if (Array.isArray(snapshot.changepoints) && snapshot.changepoints.length > 0) {
      html += renderChangepointsList(snapshot.changepoints);
    }

    setHtml('.wz3obs-preview-body', html);
  }

  function renderQualityBadge(quality) {
    if (!quality) return '';
    const score = quality.completeness_pct !== undefined ? quality.completeness_pct : quality.score;
    const reasons = quality.low_quality_reasons || quality.warnings || [];
    let html = `<div class="wz3obs-quality-box">
      <span style="font-weight:600;">Качество данных:</span>`;
    if (score !== undefined && score !== null) {
      const pct = Number(score).toFixed(0);
      const color = pct >= 80 ? '#22c55e' : pct >= 50 ? '#eab308' : '#ef4444';
      html += `<span style="color:${color};font-weight:700;margin-left:6px;">${pct}%</span>`;
    }
    if (quality.grade) {
      html += `<span style="margin-left:6px;font-size:0.84rem;color:#6b7280;">${escHtml(quality.grade)}</span>`;
    }
    if (reasons.length > 0) {
      html += `<ul style="margin:4px 0 0 16px;font-size:0.82rem;color:#dc2626;">`;
      reasons.forEach(r => { html += `<li>${escHtml(String(r))}</li>`; });
      html += '</ul>';
    }
    html += '</div>';
    return html;
  }

  function renderFlagsBlock(flags) {
    const entries = Object.entries(flags).filter(([, v]) => v === true || v > 0);
    if (entries.length === 0) return '';
    let html = `<div class="wz3obs-flags-box"><strong>Флаги:</strong><ul style="margin:4px 0 0 16px;font-size:0.82rem;">`;
    entries.forEach(([k, v]) => {
      html += `<li><code>${escHtml(k)}</code>: ${escHtml(String(v))}</li>`;
    });
    html += '</ul></div>';
    return html;
  }

  function renderMetricsTable(metrics) {
    if (!metrics || typeof metrics !== 'object') return '';
    const METRIC_LABELS = {
      p_tube_avg:    'P трубное (ср.)',
      p_tube_min:    'P трубное (мин.)',
      p_tube_max:    'P трубное (макс.)',
      p_line_avg:    'P линейное (ср.)',
      p_line_min:    'P линейное (мин.)',
      p_line_max:    'P линейное (макс.)',
      dp_avg:        'ΔP (ср.)',
      dp_min:        'ΔP (мин.)',
      dp_max:        'ΔP (макс.)',
      q_avg:         'Q (ср.)',
      q_max:         'Q (макс.)',
      q_total:       'Q суммарно',
      hours_total:   'Часов всего',
      hours_working: 'Часов работы',
      days_total:    'Дней всего',
      days_working:  'Дней работы',
    };
    let html = `<div class="wz3obs-section-title">Метрики</div>
      <table class="wz3obs-table">
        <thead><tr><th>Показатель</th><th>Значение</th></tr></thead>
        <tbody>`;
    for (const [key, val] of Object.entries(metrics)) {
      if (typeof val === 'object' && val !== null) {
        // Nested object — expand sub-keys
        for (const [sk, sv] of Object.entries(val)) {
          const label = METRIC_LABELS[`${key}_${sk}`] || METRIC_LABELS[sk] || `${key}.${sk}`;
          html += `<tr><td>${escHtml(label)}</td><td>${escHtml(fmtNum(sv))}</td></tr>`;
        }
      } else {
        const label = METRIC_LABELS[key] || key;
        const display = typeof val === 'number' ? fmtNum(val) : escHtml(String(val ?? '—'));
        html += `<tr><td>${escHtml(label)}</td><td>${display}</td></tr>`;
      }
    }
    html += '</tbody></table>';
    return html;
  }

  function renderComparisons(comparisons) {
    if (!comparisons || typeof comparisons !== 'object') return '';
    let html = `<div class="wz3obs-section-title">Сравнения</div>`;
    for (const [key, cmp] of Object.entries(comparisons)) {
      if (!cmp || typeof cmp !== 'object') continue;
      const label = key === 'with_b1' ? 'Относительно baseline' : key === 'with_customer' ? 'Относительно данных заказчика' : key;
      html += `<div class="wz3obs-cmp-block">
        <div style="font-weight:600;margin-bottom:4px;">${escHtml(label)}</div>
        <table class="wz3obs-table wz3obs-table-compact"><tbody>`;
      for (const [k, v] of Object.entries(cmp)) {
        if (typeof v === 'object') continue;
        html += `<tr><td>${escHtml(k)}</td><td>${typeof v === 'number' ? fmtNum(v) : escHtml(String(v ?? '—'))}</td></tr>`;
      }
      html += '</tbody></table></div>';
    }
    return html;
  }

  function renderDiagnosticsList(diagnostics) {
    if (!diagnostics.length) return '';
    let html = `<div class="wz3obs-section-title">Диагностика</div><ul class="wz3obs-diag-list">`;
    diagnostics.forEach(d => {
      const level = d.level || 'info';
      const color = level === 'error' ? '#dc2626' : level === 'warning' ? '#d97706' : '#6b7280';
      html += `<li style="color:${color};margin-bottom:3px;">
        <strong>[${escHtml(level)}]</strong> ${escHtml(d.message || d.note || JSON.stringify(d))}
      </li>`;
    });
    html += '</ul>';
    return html;
  }

  function renderSegmentsTable(segments) {
    if (!segments.length) return '<p style="color:#6b7280;font-size:0.84rem;">Сегменты не обнаружены.</p>';
    let html = `<div class="wz3obs-section-title">Сегменты (${segments.length})</div>
      <table class="wz3obs-table">
        <thead><tr><th>#</th><th>Тип</th><th>С</th><th>По</th><th>ΔP ср.</th><th>Q ср.</th></tr></thead>
        <tbody>`;
    segments.forEach((seg, i) => {
      const type = seg.segment_type || seg.type || '—';
      html += `<tr>
        <td>${i + 1}</td>
        <td>${escHtml(type)}</td>
        <td>${escHtml(seg.from || seg.date_from || '—')}</td>
        <td>${escHtml(seg.to || seg.date_to || '—')}</td>
        <td>${fmtNum(seg.dp_avg !== undefined ? seg.dp_avg : seg.dp_mean)}</td>
        <td>${fmtNum(seg.q_avg !== undefined ? seg.q_avg : seg.q_mean)}</td>
      </tr>`;
    });
    html += '</tbody></table>';
    return html;
  }

  function renderChangepointsList(changepoints) {
    if (!changepoints.length) return '';
    let html = `<div class="wz3obs-section-title">Точки перелома (${changepoints.length})</div>
      <ul style="margin:4px 0 0 16px;font-size:0.83rem;">`;
    changepoints.forEach(cp => {
      const date = cp.date || cp.timestamp || JSON.stringify(cp);
      const note = cp.diagnostic_note || cp.note || '';
      html += `<li style="margin-bottom:4px;">
        <strong>${escHtml(String(date))}</strong>
        ${note ? `<span style="color:#6b7280;font-size:0.8rem;"> — ${escHtml(note)}</span>` : ''}
      </li>`;
    });
    html += '</ul>';
    return html;
  }

  // ── SAVE FLOW ─────────────────────────────────────────────────────────────────

  function bindSaveHandlers() {
    const btn = el('#obs-save-confirm-btn');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      const title = (el('#obs-save-title').value || '').trim();
      const comment = (el('#obs-save-comment').value || '').trim() || null;
      if (!title) { setStatus('Введите название блока', true); return; }
      if (!state.currentPreview) { setStatus('Сначала выполните предпросмотр', true); return; }
      btn.disabled = true;
      btn.textContent = 'Сохранение…';
      try {
        // Подготовка params из текущего kind
        const params = buildParamsForSave();
        await api('POST', '/blocks', {
          well_id: state.wellId,
          kind: state.activeKind,
          title,
          params,
          data_snapshot: state.currentPreview,
          comment,
          in_report: true,
          sort_order: state.blocks.length,
        });
        setStatus('Блок сохранён', false);
        state.currentPreview = null;
        hide('.wz3obs-preview-result');
        hide('.wz3obs-save-block');
        // Сбросить форму
        renderForm(state.activeKind);
        await loadBlocks();
        await refreshChapterPreview();
      } catch (err) {
        const msg = err.message || (err.detail && err.detail.message) || JSON.stringify(err);
        setStatus('Ошибка сохранения: ' + msg, true);
      } finally {
        btn.disabled = false;
        btn.textContent = 'Сохранить в главу';
      }
    });
  }

  function buildParamsForSave() {
    const base = { well_id: state.wellId, source: 'observation', chapter: 'observation' };
    if (state.activeKind === 'observation_baseline') {
      return Object.assign(base, {
        period: {
          from: el('#obs-bl-from') ? el('#obs-bl-from').value : '',
          to:   el('#obs-bl-to')   ? el('#obs-bl-to').value   : '',
        },
        sensor_source: 'lora',
      });
    }
    if (state.activeKind === 'observation_period') {
      const baselineIdRaw = el('#obs-pd-baseline') ? el('#obs-pd-baseline').value : '';
      return Object.assign(base, {
        period: {
          from: el('#obs-pd-from') ? el('#obs-pd-from').value : '',
          to:   el('#obs-pd-to')   ? el('#obs-pd-to').value   : '',
        },
        baseline_block_id: baselineIdRaw ? parseInt(baselineIdRaw) : null,
      });
    }
    if (state.activeKind === 'observation_segment') {
      return Object.assign(base, {
        period: {
          from: el('#obs-sg-from') ? el('#obs-sg-from').value : '',
          to:   el('#obs-sg-to')   ? el('#obs-sg-to').value   : '',
        },
        aggregation: el('#obs-sg-agg') ? el('#obs-sg-agg').value : 'daily',
        sensitivity: el('#obs-sg-sens') ? el('#obs-sg-sens').value : 'medium',
      });
    }
    return base;
  }

  // ── BLOCKS LIST ───────────────────────────────────────────────────────────────

  async function loadBlocks() {
    if (!state.wellId) return;
    try {
      const resp = await api('GET', '/blocks?well_id=' + state.wellId);
      state.blocks = resp.items || [];
      state.baselineBlocks = state.blocks.filter(b => b.kind === 'observation_baseline');
      renderBlocksList();
    } catch (err) {
      setStatus('Ошибка загрузки блоков: ' + (err.message || JSON.stringify(err)), true);
    }
  }

  // part видима, если не задана явно как false (default-on, как в backend).
  function isPartOn(block, key) {
    const parts = (block.params && block.params.parts) || {};
    return parts[key] !== false;
  }

  function renderBlockCard(block, idx) {
    const id = block.block_id;
    const kindLabel = KIND_LABELS[block.kind] || block.kind;
    const stripe = KIND_STRIPE[block.kind] || '#6b7280';
    const inReportChecked = block.in_report ? 'checked' : '';
    const canUp = idx > 0;
    const canDown = idx < state.blocks.length - 1;
    const dateStr = block.created_at ? block.created_at.slice(0, 10) : '';
    const params = block.params || {};
    const prefixNote = params.prefix_note || '';
    const suffixNote = params.suffix_note || '';
    const comment = block.comment || '';

    const partsHtml = (PART_DEFS[block.kind] || []).map(([key, label]) => {
      const checked = isPartOn(block, key) ? 'checked' : '';
      return `<label class="wz3obs-part">
        <input type="checkbox" class="wz3obs-part-cb"
               data-block-id="${id}" data-part="${escHtml(key)}" ${checked}>
        <span>${escHtml(label)}</span>
      </label>`;
    }).join('');

    return `<li class="wz3obs-block wz3obs-kind-${escHtml(block.kind)}" data-block-id="${id}">
      <div class="wz3obs-card-stripe" style="background:${stripe};"></div>
      <div class="wz3obs-card-body">
        <div class="wz3obs-block-header">
          <label class="wz3obs-in-report-label" title="Включить в главу отчёта">
            <input type="checkbox" class="wz3obs-in-report-cb" data-block-id="${id}" ${inReportChecked}>
            <span>в отчёт</span>
          </label>
          <span class="wz3obs-block-kind-badge"
                style="background:${stripe};color:#fff;">${escHtml(kindLabel)}</span>
          <input type="text" class="wz3obs-title-input" data-block-id="${id}"
                 value="${escHtml(block.title)}" title="Название блока — правка на месте">
          ${dateStr ? `<span class="wz3obs-block-date">${escHtml(dateStr)}</span>` : ''}
          <div class="wz3obs-block-actions">
            <button type="button" class="wz3obs-btn-icon" data-action="up" data-block-id="${id}"
                    ${canUp ? '' : 'disabled'} title="Вверх">&#8593;</button>
            <button type="button" class="wz3obs-btn-icon" data-action="down" data-block-id="${id}"
                    ${canDown ? '' : 'disabled'} title="Вниз">&#8595;</button>
            <button type="button" class="wz3obs-btn-icon wz3obs-btn-danger" data-action="delete"
                    data-block-id="${id}" title="Удалить">&#10005;</button>
          </div>
        </div>
        <details class="wz3obs-card-details">
          <summary>✏️ Комментарии и настройки PDF</summary>
          <div class="wz3obs-card-details-body">
            <label class="wz3obs-field-label">Вступительный текст (перед блоком)</label>
            <textarea class="wz3obs-note-ta wz3obs-prefix-ta" data-block-id="${id}"
                      rows="2" placeholder="Необязательно">${escHtml(prefixNote)}</textarea>
            <label class="wz3obs-field-label">Комментарий оператора</label>
            <textarea class="wz3obs-note-ta wz3obs-comment-ta" data-block-id="${id}"
                      rows="2" placeholder="Необязательно">${escHtml(comment)}</textarea>
            <label class="wz3obs-field-label">Заключительный текст (после блока)</label>
            <textarea class="wz3obs-note-ta wz3obs-suffix-ta" data-block-id="${id}"
                      rows="2" placeholder="Необязательно">${escHtml(suffixNote)}</textarea>
            <div class="wz3obs-parts-box">
              <div class="wz3obs-parts-title">⚙️ Что включить в PDF</div>
              <div class="wz3obs-parts-grid">${partsHtml}</div>
            </div>
          </div>
        </details>
      </div>
    </li>`;
  }

  function renderBlocksList() {
    const countEl = el('.wz3obs-count');
    if (countEl) countEl.textContent = state.blocks.length;

    const list = el('.wz3obs-blocks');
    if (!list) return;

    if (state.blocks.length === 0) {
      list.innerHTML = '<li class="wz3obs-empty">Нет прикреплённых блоков</li>';
      return;
    }

    list.innerHTML = state.blocks
      .map((block, idx) => renderBlockCard(block, idx))
      .join('');
    bindBlockCardHandlers(list);
  }

  function bindBlockCardHandlers(list) {
    list.querySelectorAll('.wz3obs-in-report-cb').forEach(cb => {
      cb.addEventListener('change', async () => {
        await toggleInReport(parseInt(cb.dataset.blockId), cb.checked);
      });
    });
    list.querySelectorAll('[data-action="up"]').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!btn.disabled) await moveBlock(parseInt(btn.dataset.blockId), 'up');
      });
    });
    list.querySelectorAll('[data-action="down"]').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!btn.disabled) await moveBlock(parseInt(btn.dataset.blockId), 'down');
      });
    });
    list.querySelectorAll('[data-action="delete"]').forEach(btn => {
      btn.addEventListener('click', async () => {
        await deleteBlock(parseInt(btn.dataset.blockId));
      });
    });
    // Inline-правка заголовка (debounced PUT title).
    list.querySelectorAll('.wz3obs-title-input').forEach(inp => {
      const send = debounce(
        () => updateTitle(parseInt(inp.dataset.blockId), inp.value), 600);
      inp.addEventListener('input', send);
    });
    // Комментарий оператора (debounced PUT comment).
    list.querySelectorAll('.wz3obs-comment-ta').forEach(ta => {
      const send = debounce(
        () => updateComment(parseInt(ta.dataset.blockId), ta.value), 700);
      ta.addEventListener('input', send);
    });
    // prefix/suffix notes (debounced PUT params).
    list.querySelectorAll('.wz3obs-prefix-ta').forEach(ta => {
      const send = debounce(
        () => updateNote(parseInt(ta.dataset.blockId), 'prefix_note', ta.value), 700);
      ta.addEventListener('input', send);
    });
    list.querySelectorAll('.wz3obs-suffix-ta').forEach(ta => {
      const send = debounce(
        () => updateNote(parseInt(ta.dataset.blockId), 'suffix_note', ta.value), 700);
      ta.addEventListener('input', send);
    });
    // Чекбоксы «Что включить в PDF» (PUT params.parts).
    list.querySelectorAll('.wz3obs-part-cb').forEach(cb => {
      cb.addEventListener('change', async () => {
        await togglePart(parseInt(cb.dataset.blockId), cb.dataset.part, cb.checked);
      });
    });
  }

  async function updateTitle(blockId, value) {
    const v = (value || '').trim();
    if (!v) { setStatus('Название не может быть пустым', true); return; }
    try {
      await api('PUT', '/blocks/' + blockId, { title: v });
      const block = state.blocks.find(b => b.block_id === blockId);
      if (block) block.title = v;
      setStatus('Название обновлено', false);
      await refreshChapterPreview();
    } catch (err) {
      setStatus('Ошибка: ' + (err.message || JSON.stringify(err)), true);
    }
  }

  async function updateComment(blockId, value) {
    try {
      await api('PUT', '/blocks/' + blockId, { comment: value });
      const block = state.blocks.find(b => b.block_id === blockId);
      if (block) block.comment = value;
      setStatus('Комментарий сохранён', false);
      await refreshChapterPreview();
    } catch (err) {
      setStatus('Ошибка: ' + (err.message || JSON.stringify(err)), true);
    }
  }

  async function updateNote(blockId, field, value) {
    try {
      await api('PUT', '/blocks/' + blockId, { params: { [field]: value } });
      const block = state.blocks.find(b => b.block_id === blockId);
      if (block) {
        block.params = block.params || {};
        block.params[field] = value;
      }
      setStatus('Сохранено', false);
      await refreshChapterPreview();
    } catch (err) {
      setStatus('Ошибка: ' + (err.message || JSON.stringify(err)), true);
    }
  }

  async function togglePart(blockId, partKey, checked) {
    const block = state.blocks.find(b => b.block_id === blockId);
    const currentParts = (block && block.params && block.params.parts) || {};
    const newParts = Object.assign({}, currentParts, { [partKey]: checked });
    try {
      await api('PUT', '/blocks/' + blockId, { params: { parts: newParts } });
      if (block) {
        block.params = block.params || {};
        block.params.parts = newParts;
      }
      setStatus('Состав блока обновлён', false);
      await refreshChapterPreview();
    } catch (err) {
      setStatus('Ошибка: ' + (err.message || JSON.stringify(err)), true);
      await loadBlocks();  // откат UI к серверному состоянию
    }
  }

  async function toggleInReport(blockId, newValue) {
    try {
      await api('PUT', '/blocks/' + blockId, { in_report: newValue });
      const block = state.blocks.find(b => b.block_id === blockId);
      if (block) block.in_report = newValue;
      await refreshChapterPreview();
    } catch (err) {
      setStatus('Ошибка обновления: ' + (err.message || JSON.stringify(err)), true);
      // Revert checkbox
      await loadBlocks();
    }
  }

  async function deleteBlock(blockId) {
    if (!window.confirm('Удалить блок? Это действие необратимо.')) return;
    try {
      await api('DELETE', '/blocks/' + blockId);
      await loadBlocks();
      await refreshChapterPreview();
    } catch (err) {
      setStatus('Ошибка удаления: ' + (err.message || JSON.stringify(err)), true);
    }
  }

  async function moveBlock(blockId, direction) {
    const idx = state.blocks.findIndex(b => b.block_id === blockId);
    if (idx === -1) return;
    const swapIdx = direction === 'up' ? idx - 1 : idx + 1;
    if (swapIdx < 0 || swapIdx >= state.blocks.length) return;

    const target = state.blocks[idx];
    const swapWith = state.blocks[swapIdx];

    const oldTargetOrder = target.sort_order;
    const oldSwapOrder = swapWith.sort_order;

    // Если sort_order одинаковые — используем позицию
    const newTargetOrder = oldSwapOrder !== oldTargetOrder ? oldSwapOrder : swapIdx;
    const newSwapOrder   = oldTargetOrder !== oldSwapOrder ? oldTargetOrder : idx;

    try {
      await Promise.all([
        api('PUT', '/blocks/' + target.block_id,  { sort_order: newTargetOrder }),
        api('PUT', '/blocks/' + swapWith.block_id, { sort_order: newSwapOrder }),
      ]);
      await loadBlocks();
      await refreshChapterPreview();
    } catch (err) {
      setStatus('Ошибка изменения порядка: ' + (err.message || JSON.stringify(err)), true);
    }
  }

  // ── CHAPTER PREVIEW ───────────────────────────────────────────────────────────

  async function refreshChapterPreview() {
    const container = el('.wz3obs-chapter-html');
    if (!container) return;
    if (!state.wellId) return;
    try {
      const resp = await api('POST', '/chapter-preview', { well_id: state.wellId });
      container.innerHTML = resp.html || '<p style="color:#9ca3af;">Нет блоков для превью.</p>';
      if (resp.warnings && resp.warnings.length > 0) {
        const warnDiv = document.createElement('div');
        warnDiv.className = 'wz3obs-chapter-warnings';
        warnDiv.innerHTML = '<strong>Предупреждения:</strong><ul>' +
          resp.warnings.map(w => `<li style="font-size:0.8rem;color:#d97706;">${escHtml(w)}</li>`).join('') +
          '</ul>';
        container.appendChild(warnDiv);
      }
      // Рендерим Plotly графики для segment_analysis блоков
      setTimeout(() => renderSegmentCharts(container), 100);
    } catch (err) {
      container.innerHTML = `<p style="color:#dc2626;font-size:0.84rem;">
        Ошибка загрузки превью: ${escHtml(err.message || JSON.stringify(err))}
      </p>`;
    }
  }

  // Рендерит Plotly графики для всех контейнеров .obs-segment-chart-container и .obs-comparison-chart-container
  function renderSegmentCharts(parentContainer) {
    if (!window.Plotly) {
      console.warn('[obs-ui] Plotly not loaded, skipping chart render');
      return;
    }
    // Segment analysis charts
    const segContainers = parentContainer.querySelectorAll('.obs-segment-chart-container');
    console.log('[obs-ui] renderSegmentCharts: found', segContainers.length, 'segment containers');
    segContainers.forEach((el, idx) => {
      const snapshotStr = el.dataset.snapshot;
      if (!snapshotStr) {
        console.warn('[obs-ui] container', idx, 'has no data-snapshot');
        return;
      }
      console.log('[obs-ui] container', idx, 'snapshot length:', snapshotStr.length);
      try {
        const snap = JSON.parse(snapshotStr);
        console.log('[obs-ui] parsed snapshot:', {
          hasChartData: !!snap.chart_data,
          chartDataKeys: snap.chart_data ? Object.keys(snap.chart_data) : [],
          segsCount: (snap.segments_extended || []).length
        });
        renderSegmentChart(el, snap);
      } catch (e) {
        console.warn('Failed to parse segment chart snapshot:', e);
        el.innerHTML = '<div style="color:#dc2626;padding:10px;">Ошибка загрузки графика</div>';
      }
    });
    // Segment comparison charts
    parentContainer.querySelectorAll('.obs-comparison-chart-container').forEach(el => {
      const snapshotStr = el.dataset.snapshot;
      if (!snapshotStr) return;
      try {
        const snap = JSON.parse(snapshotStr);
        renderComparisonChart(el, snap);
      } catch (e) {
        console.warn('Failed to parse comparison chart snapshot:', e);
        el.innerHTML = '<div style="color:#dc2626;padding:10px;">Ошибка загрузки графика</div>';
      }
    });

    // Baseline metrics bar charts
    parentContainer.querySelectorAll('.obs-baseline-chart-container').forEach(el => {
      const snapshotStr = el.dataset.snapshot;
      if (!snapshotStr) return;
      try {
        const snap = JSON.parse(snapshotStr);
        renderBaselineChart(el, snap);
      } catch (e) {
        console.warn('Failed to parse baseline chart snapshot:', e);
        el.innerHTML = '<div style="color:#dc2626;padding:10px;">Ошибка загрузки графика</div>';
      }
    });
  }

  // Рендерит Plotly bar chart метрик для observation_baseline
  function renderBaselineChart(container, snap) {
    const m = snap.metrics || {};
    const metricDefs = [
      ['p_tube', 'P уст.', '#1d4ed8'],
      ['p_line', 'P шлейф', '#16a34a'],
      ['dp', 'ΔP', '#dc2626'],
      ['q', 'Q', '#f59e0b'],
    ];
    const labels = [];
    const means = [];
    const stds = [];
    const colors = [];

    for (const [key, label, color] of metricDefs) {
      const metric = m[key];
      if (!metric || metric.mean == null) continue;
      labels.push(label);
      means.push(metric.mean);
      stds.push(metric.std != null ? metric.std : 0);
      colors.push(color);
    }

    if (!labels.length) {
      container.innerHTML = '<div style="color:#9ca3af;padding:15px;text-align:center;">Нет данных метрик</div>';
      return;
    }

    const traces = [{
      x: labels,
      y: means,
      type: 'bar',
      marker: { color: colors },
      error_y: {
        type: 'data',
        array: stds,
        visible: true,
        color: '#374151',
        thickness: 1.5,
        width: 5
      },
      text: means.map(v => v != null ? v.toFixed(2) : ''),
      textposition: 'outside',
    }];

    const layout = {
      height: 260,
      margin: { l: 50, r: 20, t: 30, b: 40 },
      title: 'Метрики baseline (среднее ± std)',
      yaxis: { title: 'Значение' },
      showlegend: false,
    };

    try {
      Plotly.newPlot(container, traces, layout, { responsive: true, displayModeBar: false });
    } catch (e) {
      console.error('[obs-ui] baseline chart error:', e);
      container.innerHTML = '<div style="color:#dc2626;padding:10px;">Ошибка: ' + e.message + '</div>';
    }
  }

  // Рендерит один Plotly график сегментного анализа
  function renderSegmentChart(container, snap) {
    // Поддерживаем оба формата: chart_data и raw.chart_payload
    const rawPayload = snap.raw?.chart_payload;
    const cd = normalizeChartData(snap.chart_data || {}, rawPayload);
    const segs = snap.segments_extended || snap.segments || [];
    const cps = snap.cp_marks || snap.changepoints || [];

    console.log('[obs-ui] renderSegmentChart:', {
      datesCount: (cd.dates || []).length,
      qTotalCount: (cd.q_total || []).length,
      segsCount: segs.length,
      cpCount: cps.length
    });

    if (!cd.dates || !cd.dates.length || !segs.length) {
      console.warn('[obs-ui] renderSegmentChart: missing data, dates=', cd.dates?.length, 'segs=', segs.length);
      container.innerHTML = '<div style="color:#9ca3af;padding:15px;text-align:center;">Нет данных для графика</div>';
      return;
    }

    const traces = [];
    const SEG_COLORS = ['#6366f1', '#22c55e', '#f59e0b', '#ec4899', '#14b8a6', '#8b5cf6', '#ef4444', '#06b6d4'];

    // Q total line + markers
    traces.push({
      x: cd.dates, y: cd.q_total,
      name: 'Q общий',
      mode: 'lines+markers',
      marker: { size: 4, color: '#1f77b4' },
      line: { color: '#1f77b4', width: 1 },
      hovertemplate: 'Q = %{y:.2f}<extra></extra>',
    });

    // Segment trend lines
    segs.forEach((seg, i) => {
      const color = SEG_COLORS[i % SEG_COLORS.length];
      const startIdx = seg.start_idx ?? 0;
      const endIdx = seg.end_idx ?? cd.dates.length;
      const slope = seg.slope ?? seg.slope_total ?? 0;
      const intercept = seg.intercept ?? (seg.mean_value ?? seg.mean_q ?? 0);
      const xDates = cd.dates.slice(startIdx, endIdx);
      const yTrend = xDates.map((_, j) => intercept + slope * j);
      traces.push({
        x: xDates, y: yTrend,
        name: `Сегмент ${seg.num || i + 1}`,
        mode: 'lines',
        line: { color, width: 2 },
        hoverinfo: 'skip',
      });
    });

    // Changepoint vertical lines
    cps.forEach(cp => {
      const cpDate = cp.date || cp.timestamp;
      if (!cpDate) return;
      traces.push({
        x: [cpDate, cpDate],
        y: [Math.min(...(cd.q_total || [0])), Math.max(...(cd.q_total || [1]))],
        mode: 'lines',
        line: { color: '#ef4444', width: 1, dash: 'dash' },
        showlegend: false,
        hoverinfo: 'skip',
      });
    });

    const layout = {
      height: 350,
      margin: { l: 50, r: 20, t: 30, b: 40 },
      legend: { orientation: 'h', y: -0.15 },
      xaxis: { title: '', tickangle: -45 },
      yaxis: { title: 'Q, тыс.м³/сут' },
    };

    try {
      console.log('[obs-ui] Plotly.newPlot:', container.id, 'traces:', traces.length);
      Plotly.newPlot(container, traces, layout, { responsive: true, displayModeBar: false });
      console.log('[obs-ui] Plotly.newPlot success');
    } catch (e) {
      console.error('[obs-ui] Plotly.newPlot failed:', e);
      container.innerHTML = '<div style="color:#dc2626;padding:10px;">Ошибка Plotly: ' + e.message + '</div>';
    }
  }

  // Нормализует chart_data из разных форматов
  // Рендерит Plotly overlay-график для сравнения сегментов
  function renderComparisonChart(container, snap) {
    const chartOverlay = snap.chart_overlay || {};
    const series = chartOverlay.series || [];
    const segsCmp = snap.segments_compared || [];

    if (!series.length) {
      container.innerHTML = '<div style="color:#9ca3af;padding:15px;text-align:center;">Нет данных для графика сравнения</div>';
      return;
    }

    const SEG_COLORS = ['#6366f1', '#22c55e', '#f59e0b', '#ec4899', '#14b8a6', '#8b5cf6', '#ef4444', '#06b6d4'];
    const traces = [];

    series.forEach((s, i) => {
      const color = segsCmp[i]?.color || SEG_COLORS[i % SEG_COLORS.length];
      const label = segsCmp[i]?.label || `Сегмент ${i + 1}`;
      traces.push({
        x: s.x_norm || s.x || Array.from({ length: (s.values || []).length }, (_, j) => j),
        y: s.values || [],
        name: label,
        mode: 'lines+markers',
        marker: { size: 4, color },
        line: { color, width: 2 },
        hovertemplate: `${label}: %{y:.2f}<extra></extra>`,
      });
    });

    const layout = {
      height: 300,
      margin: { l: 50, r: 20, t: 20, b: 40 },
      legend: { orientation: 'h', y: -0.2 },
      xaxis: { title: 'День (нормализованный)', zeroline: false },
      yaxis: { title: 'Q' },
    };

    Plotly.newPlot(container, traces, layout, { responsive: true, displayModeBar: false });
  }

  function normalizeChartData(chartData, rawPayload) {
    // Формат 1: уже нормализован (segment_v1)
    if (chartData && chartData.q_total && chartData.dates) {
      console.log('[obs-ui] normalizeChartData: already has q_total, len=', chartData.q_total.length);
      return chartData;
    }

    // Формат 2: segment-demo (primary.values)
    if (chartData && chartData.primary && chartData.primary.values) {
      const result = { dates: chartData.dates || [] };
      result.q_total = chartData.primary.values;
      console.log('[obs-ui] normalizeChartData: converted primary.values to q_total, len=', result.q_total.length);
      const sec = chartData.secondary || {};
      for (const [colName, colData] of Object.entries(sec)) {
        if (!colData || !colData.values) continue;
        const vals = colData.values;
        if (colName.includes('flow') || colName === 'flow_rate_mean') {
          if (!result.q_working) result.q_working = vals;
        } else if (colName.includes('shutdown') || colName.includes('downtime')) {
          result.shutdown_min = vals;
        }
      }
      return result;
    }

    // Формат 3: observation_segment (raw.chart_payload)
    // Поддерживаем оба ключа: dates (новый) и timestamps (legacy)
    const timeValues = rawPayload?.dates || rawPayload?.timestamps;
    if (rawPayload && timeValues && rawPayload.q) {
      const dates = timeValues.map(ts =>
        typeof ts === 'string' && ts.length >= 10 ? ts.slice(0, 10) : ts
      );
      const result = {
        dates: dates,
        q_total: rawPayload.q,
        shutdown_min: rawPayload.shutdown_min,
      };
      console.log('[obs-ui] normalizeChartData: converted raw.chart_payload, len=', result.q_total?.length);
      return result;
    }

    // Fallback
    if (!chartData) {
      console.log('[obs-ui] normalizeChartData: no chartData');
      return {};
    }
    console.warn('[obs-ui] normalizeChartData: unrecognized format, chartData keys=', Object.keys(chartData));
    return chartData;
  }

  // ── TAB HANDLERS ──────────────────────────────────────────────────────────────

  function bindTabHandlers() {
    const container = document.querySelector('.wz3obs-constructor');
    if (!container) return;
    container.querySelectorAll('.wz3obs-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        container.querySelectorAll('.wz3obs-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        state.activeKind = tab.dataset.kind;
        state.currentPreview = null;
        renderForm(state.activeKind);
      });
    });
  }

  // ── SAVE BLOCK UI ─────────────────────────────────────────────────────────────

  function injectSaveBlockHtml() {
    const target = el('.wz3obs-save-block');
    if (!target) return;
    target.innerHTML = `
      <div class="wz3obs-save-inner">
        <div class="wz3obs-field-row">
          <label class="wz3obs-label">Название блока</label>
          <input type="text" class="wz3obs-input" id="obs-save-title"
                 placeholder="Напр.: «Базовый период до КРС»" style="flex:1;">
        </div>
        <div class="wz3obs-field-row">
          <label class="wz3obs-label">Комментарий</label>
          <textarea class="wz3obs-textarea" id="obs-save-comment" rows="2"
                    placeholder="Необязательно"></textarea>
        </div>
        <button type="button" class="wz3obs-btn wz3obs-btn-success" id="obs-save-confirm-btn">
          Сохранить в главу
        </button>
      </div>
    `;
    bindSaveHandlers();
  }

  // ── INIT ──────────────────────────────────────────────────────────────────────

  function init() {
    const container = document.querySelector('.wz3obs-constructor');
    if (!container) return;

    const wellIdAttr = container.dataset.wellId;
    state.wellId = wellIdAttr ? parseInt(wellIdAttr) : null;

    bindTabHandlers();
    renderForm(state.activeKind);
    injectSaveBlockHtml();

    if (state.wellId) {
      loadBlocks().then(() => refreshChapterPreview());
    }
  }

  // Public reinit hook — вызывается wizard'ом при переключении на Step 3
  window.obsUiInit = function (wellId) {
    const container = document.querySelector('.wz3obs-constructor');
    if (!container) return;
    if (wellId !== undefined && wellId !== null) {
      state.wellId = parseInt(wellId);
      container.dataset.wellId = String(state.wellId);
    }
    if (state.wellId) {
      loadBlocks().then(() => refreshChapterPreview());
    }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
