/**
 * cmp_segments.js — общий модуль «Выбор участков для сравнения».
 *
 * Используется на нескольких страницах:
 *   • /adaptation-report/wizard (шаг 3 «Наблюдение», шаг 5 «Адаптация»)
 *   • /customer-daily (секция сравнения произвольных периодов)
 *
 * Контекст создаётся вызывающей страницей:
 *   const ctx = {
 *     prefix:        'cdcmp',                        // префикс HTML id-шников
 *     segments:      [],                              // массив выбранных сегментов
 *     lastState:     null,                            // сохранённое состояние для save
 *     fullData:      null,                            // загруженные detail-данные UniTool
 *     defaultSource: 'our_pressure',                  // источник по умолчанию для +сегмента
 *     attachedKind:  'comparison',                    // kind для customer_report_block
 *     stageLabel:    'наблюдение',                    // словесно — для подписей
 *     tlState:       () => null,                      // ссылка на timeline-state (опционально)
 *     getWellId:     () => currentWellId,             // ОБЯЗАТЕЛЬНО — well id для API
 *     getWellNumber: () => currentWellNumber,         // ОБЯЗАТЕЛЬНО — № скважины
 *     _cmpStages:    [],                              // этапы (заполняется при loadSourceChart)
 *     _stepsHint:    () => ({data: {from, to}}),      // wizard-only: для дефолтных дат наблюдения
 *     _metadataHint: () => ({stages_status, stages_events}),  // wizard-only
 *   };
 *
 * Публичный API (через window):
 *   cmpRenderHTML(ctx)                — возвращает HTML-строку секции
 *   _wz3cmpBindHandlers(ctx)          — навешивает обработчики после insert HTML
 *   _wz3cmpLoadSourceChart(ctx)       — загрузка detail-данных + рендер 4 графиков
 *   _wz3cmpAddSegment(ctx)            — добавить сегмент из шкалы вверху
 *   _wz3cmpAddSegmentFromDates(...)   — добавить из конкретных timestamps
 *   _wz3cmpRender(ctx) / _wz3cmpRenderChart(ctx) / _wz3cmpRenderRichCharts(ctx)
 *   _wz3cmpSaveAsBlock(ctx)           — POST в customer_report_block
 *   константы: CMP_PALETTE, CMP_KEY_MAP
 */
(function() {
  'use strict';
  const $ = (id) => document.getElementById(id);

  // Контекст определяет: prefix HTML id-шников, в какой kind сохранять блок,
  // какой timeline-state использовать для «Добавить из шкалы вверху».
  const CMP_PALETTE = ['#1d4ed8', '#dc2626', '#16a34a', '#7c3aed', '#ea580c',
                       '#0891b2', '#db2777', '#65a30d', '#9333ea', '#ca8a04'];
  const CMP_KEY_MAP = {
    'customer': {
      q_total:    'q_gas_total',
      q_working:  'q_gas_working',
      dp:         'dp',
      p_wellhead: 'p_wellhead',
      p_flowline: 'p_flowline',
      // короткие названия для UniTool-метрик чтобы один select работал
      q:          'q_gas_total',
      p_tube:     'p_wellhead',
      p_line:     'p_flowline',
    },
    'our_pressure': {
      q_total:    'q',
      q_working:  'q',
      q:          'q',
      dp:         'dp',
      p_tube:     'p_tube',
      p_line:     'p_line',
      p_wellhead: 'p_tube',
      p_flowline: 'p_line',
    },
  };

  // ─── Контексты по шагам ────────────────────────────────────────────
  // Каждый шаг имеет независимый набор сегментов и свой prefix HTML-id.

  // ─── HTML-фабрика блока «Сравнение участков» ───
  // Возвращает строку HTML с подстановкой ctx.prefix во все id.
  // Используется и в шаге 3 (renderStep3 имеет литеральный wz3cmp-* блок),
  // и в шаге 5 (renderStep5 вставляет результат cmpRenderHTML(wz5cmpCtx)).
  function cmpRenderHTML(ctx) {
    const p = ctx.prefix;
    return `
      <div class="wz-section">
        <h3 style="margin-top:0;">🔁 Выбор участков для сравнения</h3>
        <div class="wz-step-hint" style="margin-bottom:8px;">
          Ниже — детальные графики UniTool (давления, ΔP, дебит, простой)
          с минутным/часовым разрешением. <b>Выделите интересный участок мышкой</b>
          прямо на любом графике (drag по горизонтали) — он добавится как сегмент
          для сравнения. Повторите для других периодов.
          <br>В <b>нижнем</b> графике все сегменты накладываются на общую ось
          <b>«день от начала»</b> по выбранной метрике.
        </div>

        <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;
                    margin-bottom:8px; padding:8px 10px; background:#f5f7fa;
                    border:1px solid #d8dee5; border-radius:6px;">
          <button id="${p}-load-btn" class="wz-btn wz-btn-primary"
                  style="padding:5px 14px; font-size:0.84rem;">
            🔄 Загрузить детальные данные UniTool
          </button>
          <label style="display:inline-flex; align-items:center; gap:6px; font-size:0.84rem;">
            <input type="checkbox" id="${p}-overlay-cust"> Наложить данные UzKorGaz
            (суточные сводки заказчика)
          </label>
          <span id="${p}-source-status" style="font-size:0.78rem; color:#6b7280;
                margin-left:auto;"></span>
        </div>

        <div id="${p}-charts-block" style="display:none;">
          <div style="background:#f5f5f5; padding:0.5rem 0.7rem; border-radius:5px;
                      margin-bottom:0.6rem; display:flex; gap:0.6rem; flex-wrap:wrap;
                      align-items:center; font-size:0.82rem;">
            <b>Показать:</b>
            <label><input type="checkbox" id="${p}-vz-pt" checked> P трубное</label>
            <label><input type="checkbox" id="${p}-vz-pl" checked> P линейное</label>
            <label><input type="checkbox" id="${p}-vz-dp" checked> ΔP</label>
            <label><input type="checkbox" id="${p}-vz-q" checked> Q</label>
            <span style="display:inline-flex; gap:6px; align-items:center;
                         margin-left:14px; padding-left:14px;
                         border-left:1px solid #d1d5db;"
                  title="Диапазон оси X на графиках. Сами данные уже загружены за полный период — переключение только меняет зум.">
              <b>Диапазон:</b>
              <select id="${p}-range-mode"
                      style="padding:2px 6px; border:1px solid #d1d5db;
                             border-radius:4px; font-size:0.8rem;">
                <option value="obs_x2" selected>этап ± длительность (по умолч.)</option>
                <option value="obs_7d">этап ± 7 дней</option>
                <option value="obs_30d">этап ± 30 дней</option>
                <option value="obs_90d">этап ± 3 месяца</option>
                <option value="all">весь диапазон данных</option>
                <option value="manual">вручную…</option>
              </select>
              <input type="date" id="${p}-range-from"
                     style="display:none; padding:2px 4px;
                            border:1px solid #d1d5db; border-radius:4px;
                            font-size:0.8rem;">
              <input type="date" id="${p}-range-to"
                     style="display:none; padding:2px 4px;
                            border:1px solid #d1d5db; border-radius:4px;
                            font-size:0.8rem;">
            </span>
            <span style="color:#9ca3af; margin-left:auto;">
              💡 dragmode: select — тяните мышью по горизонтали для добавления участка
            </span>
          </div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.7rem;">
            <div id="${p}-ch-pres"  style="height:280px;"></div>
            <div id="${p}-ch-dp"    style="height:280px;"></div>
            <div id="${p}-ch-flow"  style="height:280px;"></div>
            <div id="${p}-ch-down"  style="height:280px;"></div>
          </div>
        </div>

        <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;
                    margin-top:14px; margin-bottom:6px;">
          <h4 style="margin:0; font-size:0.92rem;">Выбранные участки:</h4>
          <button id="${p}-clear-btn" class="wz-btn wz-btn-secondary"
                  style="padding:4px 10px; font-size:0.78rem;">
            ✕ Очистить все
          </button>
          <span style="margin-left:auto; display:inline-flex; gap:10px; align-items:center; flex-wrap:wrap;">
            <span style="display:inline-flex; gap:4px; align-items:center;">
              <span style="font-size:0.82rem; color:#374151;">Источник для +сегмента:</span>
              <select id="${p}-add-source"
                      style="padding:3px 6px; border:1px solid #d1d5db;
                             border-radius:4px; font-size:0.8rem;"
                      title="Какие данные использовать для следующего drag-select">
                <option value="auto" selected>Авто (что есть в выделении)</option>
                <option value="our_pressure">UniTool (LoRa)</option>
                <option value="customer">UzKorGaz (well_daily)</option>
              </select>
            </span>
            <span style="display:inline-flex; gap:4px; align-items:center;">
              <span style="font-size:0.82rem; color:#374151;">Метрика для сравнения:</span>
              <select id="${p}-metric"
                      style="padding:3px 6px; border:1px solid #d1d5db;
                             border-radius:4px; font-size:0.8rem;">
                <option value="dp">ΔP, кгс/см²</option>
                <option value="q">Q дебит, тыс.м³/сут</option>
                <option value="p_tube">P трубное, кгс/см²</option>
                <option value="p_line">P линейное, кгс/см²</option>
              </select>
            </span>
          </span>
        </div>
        <div id="${p}-segments" class="wz3cmp-segments"></div>

        <div style="display:flex; gap:14px; align-items:center; flex-wrap:wrap;
                    margin-top:10px; padding:8px 12px; background:#f5f7fa;
                    border:1px solid #d8dee5; border-radius:5px;
                    font-size:0.82rem;">
          <b>Показать на графике:</b>
          <label><input type="checkbox" id="${p}-show-curve" checked> Кривая (точки данных)</label>
          <label><input type="checkbox" id="${p}-show-trend" checked> Линия тренда</label>
          <label><input type="checkbox" id="${p}-show-mean"> Среднее</label>
          <label><input type="checkbox" id="${p}-show-median"> Медиана</label>
          <label><input type="checkbox" id="${p}-show-delta"> Δ между участками</label>
          <label><input type="checkbox" id="${p}-show-forecast"> Прогноз до длины самого длинного</label>
          <span style="margin-left:auto;">
            Метод тренда:
            <select id="${p}-trend-method"
                    style="padding:2px 6px; border:1px solid #d1d5db;
                           border-radius:3px; font-size:0.78rem; margin-left:4px;">
              <option value="linear">Линейная регрессия (МНК)</option>
              <option value="theil_sen">Theil-Sen (робастная)</option>
            </select>
          </span>
        </div>

        <div id="${p}-chart"
             style="height:420px; margin-top:10px; display:none;
                    border:1px solid #e5e7eb; border-radius:6px;"></div>
        <div id="${p}-trends-table" style="margin-top:10px;"></div>

        <div id="${p}-save-block" style="display:none; margin-top:14px;
              padding:12px 14px; background:#fef3c7; border:1px solid #fcd34d;
              border-radius:6px;">
          <div style="font-weight:600; color:#92400e; margin-bottom:8px;
                      font-size:0.92rem;">
            💾 Сохранить анализ сравнения как блок отчёта
          </div>
          <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;
                      margin-bottom:8px;">
            <label style="font-size:0.84rem;">Название блока:</label>
            <input type="text" id="${p}-save-title"
                   style="flex:1; min-width:300px; padding:5px 8px;
                          border:1px solid #fcd34d; border-radius:4px;
                          font-size:0.86rem; background:#fff;"
                   placeholder="Напр.: «Сравнение участков адаптации»">
          </div>
          <div style="font-size:0.78rem; color:#92400e; font-weight:600;
                      margin-bottom:4px; text-transform:uppercase;
                      letter-spacing:0.03em;">
            📄 Описание для PDF (правится — попадёт в отчёт)
          </div>
          <textarea id="${p}-save-desc" rows="5"
                    style="width:100%; padding:8px 10px; border:1px solid #fcd34d;
                           border-radius:4px; font-size:0.85rem; line-height:1.5;
                           resize:vertical; background:#fff; font-family:inherit;
                           box-sizing:border-box;"
                    placeholder="Описание сравнения…"></textarea>
          <div style="display:flex; gap:8px; margin-top:8px; align-items:center;">
            <button id="${p}-save-btn" class="wz-btn wz-btn-success"
                    style="padding:6px 16px;">
              💾 Сохранить в отчёт
            </button>
            <button id="${p}-auto-desc" class="wz-btn wz-btn-secondary"
                    style="padding:6px 12px; font-size:0.82rem;">
              🪄 Авто-описание
            </button>
            <span id="${p}-save-status" style="font-size:0.82rem;
                  color:#10b981; margin-left:8px;"></span>
          </div>
        </div>

        <div id="${p}-status" style="margin-top:6px; font-size:0.82rem;"></div>
        <!-- Скрытые элементы для совместимости с legacy add-handler -->
        <button id="${p}-add-btn" style="display:none;">add</button>
        <select id="${p}-source" style="display:none;"><option value="our_pressure">UniTool</option></select>
      </div>
    `;
  }

  async function _wz3cmpFetchSegment(source, dateFrom, dateTo, ctx) {
    // ctx — обязательный (был забыт при выносе в shared-модуль). Без него
    // ReferenceError, drag-select на графиках не работает.
    if (!ctx || !ctx.getWellNumber || !ctx.getWellNumber() || !ctx.getWellId()) return null;
    const wn = ctx.getWellNumber();
    if (source === 'customer') {
      const r = await fetch(`/api/customer-daily/well/${encodeURIComponent(wn)}` +
                            `?date_from=${dateFrom}&date_to=${dateTo}`);
      if (!r.ok) return null;
      const d = await r.json();
      if (!d.ok || !d.rows) return null;
      return {
        dates: d.chart?.dates || [],
        q_gas_total:   d.chart?.q_gas_total   || [],
        q_gas_working: d.chart?.q_gas_working || [],
        dp:            d.chart?.dp            || [],
        p_wellhead:    d.chart?.p_wellhead    || [],
        p_flowline:    d.chart?.p_flowline    || [],
      };
    }
    // UniTool: возвращаем ПОЛНЫЙ минутный/часовой ряд БЕЗ агрегации до дней
    // — для построения relative-time графика с реальной детализацией
    const start = `${dateFrom}T00:00:00`;
    const end   = `${dateTo}T23:59:59`;
    let r = await fetch(`/api/flow-rate/calculate/${ctx.getWellId()}` +
                        `?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
    let chart = null;
    if (r.ok) {
      const fr = await r.json();
      chart = fr.chart;
    } else {
      const pr = await fetch(`/api/pressure/chart/${ctx.getWellId()}` +
                             `?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}` +
                             `&interval=60&mode=masked`);
      if (!pr.ok) return null;
      const pj = await pr.json();
      const pts = (pj.points || []).filter(p => p.t);
      chart = {
        timestamps: pts.map(p => p.t),
        p_tube: pts.map(p => p.p_tube_avg ?? null),
        p_line: pts.map(p => p.p_line_avg ?? null),
        flow_rate: [],
      };
    }
    if (!chart || !chart.timestamps) return null;
    const ts = chart.timestamps;
    const pt = chart.p_tube || [];
    const pl = chart.p_line || [];
    const fr = chart.flow_rate || [];
    const dp = ts.map((_, i) => {
      const a = pt[i], b = pl[i];
      if (a == null || b == null) return null;
      return Math.max(0, +(a - b).toFixed(2));
    });
    return {
      // ts — массив ISO timestamps в исходном (минутном/часовом) разрешении
      ts:     ts,
      p_tube: pt,
      p_line: pl,
      dp:     dp,
      q:      fr,
    };
  }

  function _wz3cmpAddSegment(ctx = wz3cmpCtx) {
    const tl = ctx.tlState && ctx.tlState();
    const f = tl?.from;
    const t = tl?.to;
    const status = $(ctx.prefix + '-status');
    if (!f || !t) {
      if (status) {
        status.textContent = '⚠ Выберите период на шкале выше (от и до), затем нажмите «Добавить».';
        status.style.color = '#dc2626';
      }
      return;
    }
    const source = $(ctx.prefix + '-source')?.value || ctx.defaultSource;
    const fStr = _w3y(f);
    const tStr = _w3y(t);
    const id = 'seg' + Date.now() + Math.random().toString(36).slice(2, 6);
    const color = CMP_PALETTE[ctx.segments.length % CMP_PALETTE.length];
    const days = _w3diff(f, t) + 1;
    const seg = {
      id, source, color,
      from: fStr, to: tStr, days,
      label: `${source === 'customer' ? 'UzKorGaz' : 'UniTool'} ${fStr}…${tStr} (${days} сут)`,
      data: null, loading: true,
    };
    ctx.segments.push(seg);
    _wz3cmpRender(ctx);
    if (status) {
      status.textContent = `⏳ Загружаю данные сегмента ${seg.label}…`;
      status.style.color = '#92400e';
    }
    _wz3cmpFetchSegment(source, fStr, tStr, ctx).then(data => {
      seg.data = data;
      seg.loading = false;
      _wz3cmpRender(ctx);
      _wz3cmpRenderChart(ctx);
      if (status) {
        status.textContent = data
          ? `✓ Сегмент добавлен (${seg.label}). Всего: ${ctx.segments.length}.`
          : `⚠ Нет данных за ${fStr}…${tStr} в источнике ${source}.`;
        status.style.color = data ? '#059669' : '#dc2626';
        setTimeout(() => { if (status) status.textContent = ''; }, 5000);
      }
    }).catch(e => {
      seg.loading = false;
      seg.error = e.message;
      _wz3cmpRender(ctx);
      if (status) {
        status.textContent = `✗ Ошибка: ${e.message}`;
        status.style.color = '#dc2626';
      }
    });
  }

  function _wz3cmpRemoveSegment(id, ctx = wz3cmpCtx) {
    // mutate in place чтобы не сломать ссылку wz3cmpSegments
    const idx = ctx.segments.findIndex(s => s.id === id);
    if (idx >= 0) ctx.segments.splice(idx, 1);
    _wz3cmpRender(ctx);
    _wz3cmpRenderChart(ctx);
  }

  function _wz3cmpRender(ctx = wz3cmpCtx) {
    const box = $(ctx.prefix + '-segments');
    if (!box) return;
    if (!ctx.segments.length) {
      box.innerHTML = `
        <div style="padding:10px; text-align:center; color:#6b7280;
                    font-size:0.82rem; background:#f9fafb;
                    border:1px dashed #d1d5db; border-radius:6px;">
          Нет выбранных участков. Выделите диапазон мышкой на любом графике
          (Box Select) или нажмите «Добавить из шкалы вверху».
        </div>`;
      _wz3cmpRedrawSelectionShapes(ctx);
      return;
    }
    box.innerHTML = '';
    ctx.segments.forEach((s, i) => {
      const letter = String.fromCharCode(65 + i);
      const fromTs = s.fromTs || (s.from + 'T00:00');
      const toTs   = s.toTs   || (s.to   + 'T23:59');
      const ms = (new Date(toTs)) - (new Date(fromTs));
      const totH = ms > 0 ? ms / 3600000 : 0;
      const days = Math.floor(totH / 24);
      const hrs  = (totH - days * 24).toFixed(1);
      const card = document.createElement('div');
      card.style.cssText = `border:2px solid ${s.color}; border-radius:6px;
        padding:8px 10px; background:#fff; font-size:0.84rem;`;
      card.innerHTML = `
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">
          <span style="display:inline-block; width:14px; height:14px; border-radius:3px;
                       background:${s.color};"></span>
          <b style="color:${s.color};">${letter}.</b>
          <span><b>${days}</b> сут <b>${hrs}</b> ч</span>
          ${s.loading ? '<span style="color:#92400e;">⏳ загрузка…</span>' : ''}
          ${s.error ? `<span style="color:#dc2626;" title="${s.error}">⚠ ошибка</span>` : ''}
          <button data-seg-remove="${s.id}"
                  style="margin-left:auto; background:none; border:none;
                         color:#dc2626; cursor:pointer; font-size:1.1rem;"
                  title="Удалить сегмент">✕</button>
        </div>
        <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
          <label style="font-size:0.78rem; color:#374151;">Начало:</label>
          <input type="datetime-local" data-seg-from="${s.id}" value="${fromTs.slice(0,16)}"
                 step="60"
                 style="padding:2px 6px; border:1px solid #d1d5db; border-radius:3px;
                        font-size:0.78rem; width:11rem;">
          <label style="font-size:0.78rem; color:#374151;">Конец:</label>
          <input type="datetime-local" data-seg-to="${s.id}" value="${toTs.slice(0,16)}"
                 step="60"
                 style="padding:2px 6px; border:1px solid #d1d5db; border-radius:3px;
                        font-size:0.78rem; width:11rem;">
          <label style="font-size:0.78rem; color:#374151;">Источник:</label>
          <select data-seg-source="${s.id}"
                  style="padding:2px 6px; border:1px solid #d1d5db;
                         border-radius:3px; font-size:0.78rem; font-weight:600;"
                  title="Источник данных для этого сегмента — можно менять">
            <option value="our_pressure" ${s.source === 'our_pressure' ? 'selected' : ''}>UniTool (LoRa)</option>
            <option value="customer"     ${s.source === 'customer'     ? 'selected' : ''}>UzKorGaz</option>
          </select>
          <span style="font-size:0.74rem; color:#6b7280;">
            (изменения применятся при потере фокуса)
          </span>
        </div>
      `;
      box.appendChild(card);
    });
    // Bind remove + edit
    box.querySelectorAll('[data-seg-remove]').forEach(btn => {
      btn.addEventListener('click', () => _wz3cmpRemoveSegment(btn.dataset.segRemove, ctx));
    });
    box.querySelectorAll('[data-seg-from]').forEach(inp => {
      inp.addEventListener('change', () => {
        const seg = ctx.segments.find(s => s.id === inp.dataset.segFrom);
        if (!seg) return;
        seg.fromTs = inp.value + ':00';
        seg.from = inp.value.slice(0, 10);
        _wz3cmpReloadSegment(seg, ctx);
      });
    });
    box.querySelectorAll('[data-seg-to]').forEach(inp => {
      inp.addEventListener('change', () => {
        const seg = ctx.segments.find(s => s.id === inp.dataset.segTo);
        if (!seg) return;
        seg.toTs = inp.value + ':00';
        seg.to = inp.value.slice(0, 10);
        _wz3cmpReloadSegment(seg, ctx);
      });
    });
    box.querySelectorAll('[data-seg-source]').forEach(sel => {
      sel.addEventListener('change', () => {
        const seg = ctx.segments.find(s => s.id === sel.dataset.segSource);
        if (!seg) return;
        seg.source = sel.value;
        _wz3cmpReloadSegment(seg, ctx);
      });
    });
    // Перерисовка shapes на основных графиках
    _wz3cmpRedrawSelectionShapes(ctx);
  }

  // Перезагрузка данных сегмента при ручной правке дат
  function _wz3cmpReloadSegment(seg, ctx = wz3cmpCtx) {
    seg.loading = true;
    seg.data = null;
    _wz3cmpRender(ctx);
    _wz3cmpFetchSegment(seg.source, seg.from, seg.to, ctx).then(data => {
      seg.data = data; seg.loading = false;
      _wz3cmpRender(ctx);
      _wz3cmpRenderChart(ctx);
    });
  }

  // Рисует прямоугольники-выделения сегментов на 4 главных графиках
  function _wz3cmpRedrawSelectionShapes(ctx = wz3cmpCtx) {
    const ids = [
      ctx.prefix + '-ch-pres', ctx.prefix + '-ch-dp',
      ctx.prefix + '-ch-flow', ctx.prefix + '-ch-down',
    ];
    const segShapes = ctx.segments.map(s => ({
      type: 'rect', xref: 'x', yref: 'paper',
      x0: s.fromTs || s.from, x1: s.toTs || s.to, y0: 0, y1: 1,
      fillcolor: _hexToRgba(s.color, 0.15),
      line: { color: s.color, width: 1.5 },
      layer: 'above',
    }));
    for (const id of ids) {
      const el = document.getElementById(id);
      if (!el || !el.layout) continue;
      // Объединяем shapes этапов (stageShapes) и наши selection shapes
      const baseShapes = (el._stageShapes || []);
      try {
        Plotly.relayout(el, { shapes: [...baseShapes, ...segShapes] });
      } catch (_) {}
    }
  }
  // Утилита hex → rgba
  function _hexToRgba(hex, alpha) {
    const m = String(hex).match(/^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i);
    if (!m) return `rgba(37,99,235,${alpha})`;
    return `rgba(${parseInt(m[1],16)},${parseInt(m[2],16)},${parseInt(m[3],16)},${alpha})`;
  }
  // Экспорт remove чтобы работал inline-onclick
  window._wz3cmpRemoveSegment = _wz3cmpRemoveSegment;

  function _wz3cmpRenderChart(ctx = wz3cmpCtx) {
    const el = $(ctx.prefix + '-chart');
    if (!el) return;
    const segments = ctx.segments.filter(s => s.data);
    if (!segments.length) {
      el.style.display = 'none';
      const tbl = $(ctx.prefix + '-trends-table');
      if (tbl) tbl.innerHTML = '';
      return;
    }
    el.style.display = '';
    const metric = $(ctx.prefix + '-metric')?.value || 'dp';
    const showCurve    = $(ctx.prefix + '-show-curve')?.checked !== false;
    const showTrend    = $(ctx.prefix + '-show-trend')?.checked !== false;
    const showForecast = $(ctx.prefix + '-show-forecast')?.checked || false;
    const showMean     = $(ctx.prefix + '-show-mean')?.checked || false;
    const showMedian   = $(ctx.prefix + '-show-median')?.checked || false;
    const showDelta    = $(ctx.prefix + '-show-delta')?.checked || false;
    const method = $(ctx.prefix + '-trend-method')?.value || 'linear';

    // Сначала готовим данные для всех сегментов и считаем maxHours
    const segData = [];
    let maxHours = 0;
    for (let i = 0; i < segments.length; i++) {
      const s = segments[i];
      const keyMap = CMP_KEY_MAP[s.source];
      const yKey = keyMap?.[metric];
      if (!yKey) continue;
      const tsArr = s.data.ts || s.data.dates || [];
      const yArr  = s.data[yKey] || [];
      if (!tsArr.length) continue;
      const fromMs = (new Date(s.fromTs || (s.from + 'T00:00'))).getTime();
      const toMs   = (new Date(s.toTs   || (s.to   + 'T23:59'))).getTime();
      const xH = [], yY = [];
      for (let j = 0; j < tsArr.length; j++) {
        const tsMs = (new Date(tsArr[j])).getTime();
        if (tsMs < fromMs || tsMs > toMs) continue;
        if (yArr[j] == null || isNaN(yArr[j])) continue;
        const hours = (tsMs - fromMs) / 3600000;
        xH.push(hours);
        yY.push(+yArr[j]);
        if (hours > maxHours) maxHours = hours;
      }
      const segDur = (toMs - fromMs) / 3600000;
      if (segDur > maxHours) maxHours = segDur;
      const reg = method === 'theil_sen' ? _theilSen(xH, yY) : _linRegress(xH, yY);
      // Среднее / медиана — для горизонтальных линий и Δ-аннотаций
      let mean = null, median = null;
      if (yY.length) {
        mean = yY.reduce((a, b) => a + b, 0) / yY.length;
        const sorted = yY.slice().sort((a, b) => a - b);
        median = sorted.length % 2
          ? sorted[(sorted.length - 1) >> 1]
          : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
      }
      segData.push({ s, i, xH, yY, segDur, reg, mean, median });
    }
    if (!segData.length) {
      el.style.display = 'none';
      const tbl = $(ctx.prefix + '-trends-table');
      if (tbl) tbl.innerHTML = '';
      return;
    }
    const inDays = maxHours > 48;
    const toUnit = (h) => inDays ? h / 24 : h;

    const traces = [];
    const annotations = [];
    for (const sd of segData) {
      const letter = String.fromCharCode(65 + sd.i);
      const label = `${letter}. ${sd.s.source === 'customer' ? 'UzKorGaz' : 'UniTool'} ` +
                    `${(sd.s.fromTs || sd.s.from).slice(0,16).replace('T',' ')}…` +
                    `${(sd.s.toTs   || sd.s.to  ).slice(0,16).replace('T',' ')}`;
      // Кривая
      if (showCurve) {
        traces.push({
          x: sd.xH.map(toUnit), y: sd.yY,
          name: label, mode: 'lines', line: { color: sd.s.color, width: 1.5 },
          hovertemplate: `+%{x:.2f} ${inDays ? 'дн' : 'ч'}: %{y:.3f}<extra>${label}</extra>`,
        });
      }
      // Среднее / медиана — горизонтальная линия от 0 до длительности сегмента
      // (или до maxHours при прогнозе). Цвет — как у сегмента, разный dash.
      const xMaxSeg = showForecast ? maxHours : sd.segDur;
      if (showMean && sd.mean != null) {
        traces.push({
          x: [0, xMaxSeg].map(toUnit), y: [sd.mean, sd.mean],
          name: `${letter}. среднее ${sd.mean.toFixed(3)}`, mode: 'lines',
          line: { color: sd.s.color, width: 1.5, dash: 'dot' },
          opacity: 0.85,
          hovertemplate: `${letter}. среднее: ${sd.mean.toFixed(3)}<extra></extra>`,
        });
      }
      if (showMedian && sd.median != null) {
        traces.push({
          x: [0, xMaxSeg].map(toUnit), y: [sd.median, sd.median],
          name: `${letter}. медиана ${sd.median.toFixed(3)}`, mode: 'lines',
          line: { color: sd.s.color, width: 1.5, dash: 'dashdot' },
          opacity: 0.85,
          hovertemplate: `${letter}. медиана: ${sd.median.toFixed(3)}<extra></extra>`,
        });
      }
      // Линия тренда
      if (showTrend && sd.reg.slope != null) {
        const xMin = 0;
        const xMax = showForecast ? maxHours : sd.segDur;
        const yA = sd.reg.intercept + sd.reg.slope * xMin;
        const yB = sd.reg.intercept + sd.reg.slope * xMax;
        const isForecast = showForecast && xMax > sd.segDur;
        // Если есть прогноз — рисуем 2 куска: фактический + прогноз пунктиром
        if (isForecast) {
          // Факт-часть
          traces.push({
            x: [xMin, sd.segDur].map(toUnit),
            y: [sd.reg.intercept + sd.reg.slope * xMin,
                sd.reg.intercept + sd.reg.slope * sd.segDur],
            name: `${letter}. тренд`, mode: 'lines',
            line: { color: sd.s.color, width: 2.5, dash: 'dash' },
            showlegend: !showCurve,
            hoverinfo: 'skip',
          });
          // Прогноз-часть
          traces.push({
            x: [sd.segDur, xMax].map(toUnit),
            y: [sd.reg.intercept + sd.reg.slope * sd.segDur, yB],
            name: `${letter}. прогноз`, mode: 'lines',
            line: { color: sd.s.color, width: 2, dash: 'dot' },
            opacity: 0.6,
            showlegend: false,
            hoverinfo: 'skip',
          });
        } else {
          traces.push({
            x: [xMin, xMax].map(toUnit), y: [yA, yB],
            name: `${letter}. тренд`, mode: 'lines',
            line: { color: sd.s.color, width: 2.5, dash: 'dash' },
            showlegend: !showCurve,
            hoverinfo: 'skip',
          });
        }
      }
    }
    const metricLabel = {
      dp: 'ΔP, кгс/см²',
      q: 'Q дебит, тыс.м³/сут',
      q_total: 'Q общий, тыс.м³/сут',
      q_working: 'Q рабочий, тыс.м³/сут',
      p_tube: 'P трубное, кгс/см²',
      p_line: 'P линейное, кгс/см²',
      p_wellhead: 'P устье, кгс/см²',
      p_flowline: 'P шлейф, кгс/см²',
    }[metric] || metric;
    const xTitle = inDays
      ? 'Время от начала сегмента, дни'
      : 'Время от начала сегмента, часы';

    // Δ между последовательными сегментами по среднему / медиане.
    // Рисуем как стрелку (трейс) + аннотацию-плашку с цветом:
    //   рост → зелёный, падение → красный.
    if (showDelta && segData.length >= 2) {
      const xRef = inDays ? maxHours / 24 : maxHours;  // правый край
      const drawDelta = (kind, getter, dash) => {
        for (let k = 1; k < segData.length; k++) {
          const a = segData[k - 1], b = segData[k];
          const va = getter(a), vb = getter(b);
          if (va == null || vb == null) continue;
          const d = vb - va;
          const lA = String.fromCharCode(65 + a.i);
          const lB = String.fromCharCode(65 + b.i);
          // Цвет по знаку Δ. Для ΔP/Q «больше — обычно лучше» — зелёный.
          const isUp = d >= 0;
          const fill = isUp ? '#dcfce7' : '#fee2e2';
          const fg   = isUp ? '#166534' : '#991b1b';
          // Вертикальная линия-индикатор Δ справа от графика
          const xPos = xRef * (0.92 + 0.04 * (k - 1));  // смещение для нескольких Δ
          traces.push({
            x: [xPos, xPos], y: [va, vb],
            mode: 'lines+markers',
            line: { color: fg, width: 2.5, dash },
            marker: { symbol: 'triangle-' + (isUp ? 'up' : 'down'),
                      size: 10, color: fg },
            name: `Δ${kind} ${lA}→${lB}: ${(d>=0?'+':'')}${d.toFixed(3)}`,
            hovertemplate:
              `Δ${kind} (${lB} − ${lA}): ${(d>=0?'+':'')}${d.toFixed(3)}` +
              ` (${va.toFixed(3)} → ${vb.toFixed(3)})<extra></extra>`,
            showlegend: true,
          });
          // Плашка с подписью посредине
          annotations.push({
            x: xPos, y: (va + vb) / 2, xref: 'x', yref: 'y',
            text: `Δ${kind === 'avg' ? 'ср' : 'мед'} ${lA}→${lB}<br>` +
                  `<b>${(d>=0?'+':'')}${d.toFixed(3)}</b>`,
            showarrow: false, font: { size: 10, color: fg },
            bgcolor: fill, bordercolor: fg, borderwidth: 1, borderpad: 3,
            align: 'center',
          });
        }
      };
      if (showMean)   drawDelta('avg', sd => sd.mean,   'dot');
      if (showMedian) drawDelta('med', sd => sd.median, 'dashdot');
      // Если ни среднее, ни медиана не выбраны — показываем Δ по среднему
      if (!showMean && !showMedian) drawDelta('avg', sd => sd.mean, 'dot');
    }

    Plotly.newPlot(el, traces, {
      title: `Сравнение участков · ${metricLabel} · ось X = Δt от начала сегмента` +
             (showForecast ? ' (прогноз пунктиром до длины самого длинного)' : ''),
      margin: { l: 60, r: 30, t: 50, b: 60 },
      xaxis: { title: xTitle, hoverformat: '.2f' },
      yaxis: { title: metricLabel },
      hovermode: 'closest',
      legend: { orientation: 'h', y: -0.2 },
      font: { size: 11 },
      annotations,
    }, { displaylogo: false, responsive: true });

    // Таблица характеристик и трендов
    _wz3cmpRenderTrendsTable(segData, metricLabel, method, ctx);
    // Блок «Сохранить сравнение в отчёт» — виден когда есть ≥1 сегмент
    _wz3cmpUpdateSaveBlock(segData, metricLabel, method, ctx);
    // Сохраняем последнее состояние для использования при сохранении блока
    ctx.lastState = { segData, metricLabel, method,
      metric: $(ctx.prefix + '-metric')?.value || 'dp',
      toggles: {
        showCurve:    $(ctx.prefix + '-show-curve')?.checked !== false,
        showTrend:    $(ctx.prefix + '-show-trend')?.checked !== false,
        showForecast: $(ctx.prefix + '-show-forecast')?.checked || false,
        showMean:     $(ctx.prefix + '-show-mean')?.checked   || false,
        showMedian:   $(ctx.prefix + '-show-median')?.checked || false,
        showDelta:    $(ctx.prefix + '-show-delta')?.checked  || false,
      },
    };
  }

  function _wz3cmpUpdateSaveBlock(segData, metricLabel, method, ctx = wz3cmpCtx) {
    const block = $(ctx.prefix + '-save-block');
    if (!block) return;
    if (!segData.length) { block.style.display = 'none'; return; }
    block.style.display = '';
    // Если поле названия пусто — заполняем дефолтным
    const titleEl = $(ctx.prefix + '-save-title');
    if (titleEl && !titleEl.value) {
      const letters = segData.map(sd => String.fromCharCode(65 + sd.i)).join('+');
      titleEl.value = `Сравнение участков ${letters} · ${metricLabel}`;
    }
    // Авто-описание — если поле пусто
    const descEl = $(ctx.prefix + '-save-desc');
    if (descEl && !descEl.value) {
      descEl.value = _wz3cmpBuildAutoDesc(segData, metricLabel, method);
    }
  }

  function _wz3cmpBuildAutoDesc(segData, metricLabel, method) {
    const fmt = (v, d=3) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d);
    const sign = (v, d=3) => v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(d);
    const lines = [];
    lines.push(
      `Анализ сравнения ${segData.length} ${segData.length === 1 ? 'участка' : 'участков'} ` +
      `по показателю «${metricLabel}». Тренды рассчитаны методом ` +
      `${method === 'theil_sen' ? 'Theil-Sen (робастный)' : 'линейной регрессии (МНК)'}.`
    );
    lines.push('');
    lines.push('Сравниваемые участки:');
    for (const sd of segData) {
      const letter = String.fromCharCode(65 + sd.i);
      const src = sd.s.source === 'customer' ? 'UzKorGaz' : 'UniTool';
      const fStr = (sd.s.fromTs || sd.s.from).slice(0, 16).replace('T', ' ');
      const tStr = (sd.s.toTs   || sd.s.to  ).slice(0, 16).replace('T', ' ');
      const days = Math.floor(sd.segDur / 24);
      const hrs  = (sd.segDur - days * 24).toFixed(1);
      const yY = sd.yY;
      const n = yY.length;
      let mean = null, median = null;
      if (n > 0) {
        mean = yY.reduce((s, x) => s + x, 0) / n;
        const sorted = yY.slice().sort((a, b) => a - b);
        median = sorted.length % 2
          ? sorted[(sorted.length - 1) >> 1]
          : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
      }
      const slopeH = sd.reg.slope;
      const slopeD = slopeH != null ? slopeH * 24 : null;
      const direction = slopeH == null ? 'нет данных'
        : slopeH > 0.001 ? 'рост'
        : slopeH < -0.001 ? 'падение'
        : 'стабильно';
      lines.push(
        `  ${letter}. ${src} · ${fStr} … ${tStr} (${days} сут ${hrs} ч, ${n} точек). ` +
        `Среднее ${fmt(mean)}, медиана ${fmt(median)}. ` +
        `Тренд: ${direction}, slope ${sign(slopeH, 4)} ед./час (${sign(slopeD, 3)} ед./день)` +
        (sd.reg.r2 != null ? `, R² = ${fmt(sd.reg.r2, 3)}` : '') + '.'
      );
    }
    // Сравнение между сегментами
    if (segData.length >= 2) {
      const first = segData[0];
      const last  = segData[segData.length - 1];
      const meanF = first.yY.length
        ? first.yY.reduce((s,x)=>s+x,0)/first.yY.length : null;
      const meanL = last.yY.length
        ? last.yY.reduce((s,x)=>s+x,0)/last.yY.length : null;
      if (meanF != null && meanL != null && meanF !== 0) {
        const dPct = (meanL - meanF) / meanF * 100;
        const lF = String.fromCharCode(65 + first.i);
        const lL = String.fromCharCode(65 + last.i);
        lines.push('');
        lines.push(
          `Изменение между ${lF} и ${lL}: среднее ${fmt(meanF)} → ${fmt(meanL)} ` +
          `(${dPct >= 0 ? '+' : ''}${dPct.toFixed(1)}%).`
        );
      }
    }
    return lines.join('\n');
  }

  // Таблица: per-сегмент длительность, тренд (slope в /час и /день),
  // R², min/max/mean/median.
  function _wz3cmpRenderTrendsTable(segData, metricLabel, method, ctx = wz3cmpCtx) {
    const box = $((ctx.prefix + '-trends-table'));
    if (!box) return;
    const fmt = (v, d=3) => (v == null || isNaN(v))
      ? '<span style="color:#9ca3af;">—</span>' : Number(v).toFixed(d);
    const sign = (v, d=3) => v == null ? '—'
      : (v >= 0 ? '+' : '') + Number(v).toFixed(d);
    let html = `<h4 style="margin:8px 0; font-size:0.92rem;">
      Тренды и характеристики участков
      <span style="font-size:0.78rem; font-weight:400; color:#6b7280;">
        (метод: ${method === 'theil_sen' ? 'Theil-Sen' : 'МНК линейная'})
      </span>
    </h4>
    <table class="wz-table" style="font-size:0.82rem;">
      <thead><tr>
        <th>Сегмент</th>
        <th>Длительность</th>
        <th>N точек</th>
        <th>Среднее</th>
        <th>Медиана</th>
        <th>Мин</th>
        <th>Макс</th>
        <th>Slope, /час</th>
        <th>Slope, /день</th>
        <th>Направление</th>
        <th>R²</th>
      </tr></thead><tbody>`;
    for (const sd of segData) {
      const letter = String.fromCharCode(65 + sd.i);
      const yY = sd.yY;
      const n = yY.length;
      let mean = null, median = null, mn = null, mx = null;
      if (n > 0) {
        mean = yY.reduce((s, x) => s + x, 0) / n;
        const sorted = yY.slice().sort((a, b) => a - b);
        median = sorted.length % 2
          ? sorted[(sorted.length - 1) >> 1]
          : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
        mn = sorted[0];
        mx = sorted[sorted.length - 1];
      }
      const slopeH = sd.reg.slope;       // единица/час
      const slopeD = slopeH != null ? slopeH * 24 : null;  // /день
      const arrow = slopeH == null ? '→'
        : slopeH > 0.001 ? '↑ рост'
        : slopeH < -0.001 ? '↓ падение'
        : '→ стабильно';
      const arrowColor = slopeH == null ? '#6b7280'
        : slopeH > 0 ? '#059669' : slopeH < 0 ? '#dc2626' : '#6b7280';
      const dur = sd.segDur;
      const days = Math.floor(dur / 24);
      const hrs  = (dur - days * 24).toFixed(1);
      const r2Str = sd.reg.r2 == null ? '—' : sd.reg.r2.toFixed(3);
      html += `
        <tr>
          <td><span style="display:inline-block; width:12px; height:12px; border-radius:2px;
                           background:${sd.s.color}; vertical-align:middle; margin-right:5px;"></span>
              <b>${letter}.</b> ${sd.s.source === 'customer' ? 'UzKorGaz' : 'UniTool'}</td>
          <td>${days} сут ${hrs} ч</td>
          <td>${n}</td>
          <td>${fmt(mean)}</td>
          <td>${fmt(median)}</td>
          <td>${fmt(mn)}</td>
          <td>${fmt(mx)}</td>
          <td>${sign(slopeH, 4)}</td>
          <td>${sign(slopeD, 3)}</td>
          <td style="color:${arrowColor}; font-weight:600;">${arrow}</td>
          <td>${r2Str}</td>
        </tr>`;
    }
    html += '</tbody></table>';
    box.innerHTML = html;
  }

  function _wz3cmpBindHandlers(ctx = wz3cmpCtx) {
    $((ctx.prefix + '-add-btn'))?.addEventListener('click', () => _wz3cmpAddSegment(ctx));
    $((ctx.prefix + '-clear-btn'))?.addEventListener('click', () => {
      if (!ctx.segments.length) return;
      if (!confirm('Удалить все сегменты сравнения?')) return;
      ctx.segments.length = 0;
      _wz3cmpRender(ctx);
      _wz3cmpRenderChart(ctx);
    });
    // Перерисовка при смене метрики (источник меняется на этапе добавления)
    $((ctx.prefix + '-metric'))?.addEventListener('change', () => _wz3cmpRenderChart(ctx));
    // Кнопка загрузки полных детальных данных
    $((ctx.prefix + '-load-btn'))?.addEventListener('click', () => _wz3cmpLoadSourceChart(ctx));
    // Чекбокс «Наложить UzKorGaz» → перезагрузить с overlay
    $((ctx.prefix + '-overlay-cust'))?.addEventListener('change', () => _wz3cmpLoadSourceChart(ctx));
    // Чекбоксы видимости кривых — перерисовка без повторного fetch
    [(ctx.prefix + '-vz-pt'),(ctx.prefix + '-vz-pl'),(ctx.prefix + '-vz-dp'),(ctx.prefix + '-vz-q')].forEach(id => {
      $(id)?.addEventListener('change', () => {
        if (ctx.fullData) _wz3cmpRenderRichCharts(ctx);
      });
    });
    // Селект диапазона + manual-даты — перерисовка без повторного fetch.
    // Все данные уже загружены за полный период (UniTool: equip…ourTo,
    // UzKorGaz: custFrom…custTo) — нужно только сменить xRange.
    $((ctx.prefix + '-range-mode'))?.addEventListener('change', () => {
      const mode = $((ctx.prefix + '-range-mode'))?.value;
      const isManual = mode === 'manual';
      const fromI = $((ctx.prefix + '-range-from'));
      const toI   = $((ctx.prefix + '-range-to'));
      if (fromI) fromI.style.display = isManual ? '' : 'none';
      if (toI)   toI.style.display   = isManual ? '' : 'none';
      // Подсказка манульных дат — заполняем границами полного диапазона
      if (isManual && fromI && toI && ctx.fullData) {
        if (!fromI.value) fromI.value = (ctx.fullData.dateFrom || '').slice(0, 10);
        if (!toI.value)   toI.value   = (ctx.fullData.dateTo   || '').slice(0, 10);
      }
      if (ctx.fullData) _wz3cmpRenderRichCharts(ctx);
    });
    [(ctx.prefix + '-range-from'),(ctx.prefix + '-range-to')].forEach(id => {
      $(id)?.addEventListener('change', () => {
        if (ctx.fullData) _wz3cmpRenderRichCharts(ctx);
      });
    });
    // Тоггл-кнопки тренда / прогноза / среднего / медианы / Δ
    [(ctx.prefix + '-show-curve'),(ctx.prefix + '-show-trend'),(ctx.prefix + '-show-forecast'),
     (ctx.prefix + '-show-mean'),(ctx.prefix + '-show-median'),(ctx.prefix + '-show-delta'),
     (ctx.prefix + '-trend-method')].forEach(id => {
      $(id)?.addEventListener('change', () => _wz3cmpRenderChart(ctx));
    });
    // Save-блок: «Авто-описание» и «Сохранить»
    $((ctx.prefix + '-auto-desc'))?.addEventListener('click', () => {
      if (!ctx.lastState) return;
      const { segData, metricLabel, method } = ctx.lastState;
      $((ctx.prefix + '-save-desc')).value = _wz3cmpBuildAutoDesc(segData, metricLabel, method);
    });
    $((ctx.prefix + '-save-btn'))?.addEventListener('click', () => _wz3cmpSaveAsBlock(ctx));
  }

  // Сохранить текущее сравнение как customer_report_block с kind='comparison'
  async function _wz3cmpSaveAsBlock(ctx = wz3cmpCtx) {
    const status = $((ctx.prefix + '-save-status'));
    if (!ctx.lastState) {
      if (status) { status.textContent = '⚠ Нет сравнения для сохранения.'; status.style.color = '#dc2626'; }
      return;
    }
    if (!ctx.getWellId()) {
      if (status) { status.textContent = '⚠ Скважина не определена.'; status.style.color = '#dc2626'; }
      return;
    }
    const title = ($((ctx.prefix + '-save-title'))?.value || '').trim()
      || `Сравнение участков · ${ctx.lastState.metricLabel}`;
    const desc  = $((ctx.prefix + '-save-desc'))?.value || '';

    // Снимок: метрика, метод, тогглы, и параметры сегментов
    // + downsampled-кривые для последующей перерисовки графика без fetch.
    const MAX_PTS = 600;  // верхний предел точек на сегмент (~5KB на curve)
    const segs = ctx.lastState.segData.map(sd => {
      const yY = sd.yY;
      let mean = null, median = null, mn = null, mx = null;
      if (yY.length) {
        mean = yY.reduce((s,x)=>s+x,0) / yY.length;
        const sorted = yY.slice().sort((a,b)=>a-b);
        median = sorted.length % 2
          ? sorted[(sorted.length-1) >> 1]
          : (sorted[sorted.length/2-1] + sorted[sorted.length/2]) / 2;
        mn = sorted[0]; mx = sorted[sorted.length-1];
      }
      // Downsample x/y до MAX_PTS точек (равномерное прореживание)
      let xCurve = sd.xH, yCurve = yY;
      if (xCurve.length > MAX_PTS) {
        const step = xCurve.length / MAX_PTS;
        const xx = [], yy = [];
        for (let k = 0; k < MAX_PTS; k++) {
          const idx = Math.floor(k * step);
          if (idx < xCurve.length) {
            xx.push(+xCurve[idx].toFixed(4));
            yy.push(+(yCurve[idx] || 0).toFixed(3));
          }
        }
        xCurve = xx; yCurve = yy;
      } else {
        xCurve = xCurve.map(x => +x.toFixed(4));
        yCurve = yCurve.map(y => +(y || 0).toFixed(3));
      }
      return {
        letter: String.fromCharCode(65 + sd.i),
        source: sd.s.source,
        color:  sd.s.color,
        from_ts: sd.s.fromTs || (sd.s.from + 'T00:00:00'),
        to_ts:   sd.s.toTs   || (sd.s.to   + 'T23:59:59'),
        from:    sd.s.from,
        to:      sd.s.to,
        duration_hours: sd.segDur,
        n: yY.length,
        mean, median, min: mn, max: mx,
        slope_per_hour: sd.reg.slope,
        slope_per_day:  sd.reg.slope != null ? sd.reg.slope * 24 : null,
        intercept:      sd.reg.intercept,
        r2:             sd.reg.r2,
        // Кривая в относительных часах — для перерисовки графика
        // в карточке прикреплённого блока без повторного fetch
        curve_x_hours: xCurve,
        curve_y:       yCurve,
      };
    });
    const snap = {
      _v: 'wz3cmp_v1',
      kind_detail: 'segments_comparison',
      metric: ctx.lastState.metric,
      metric_label: ctx.lastState.metricLabel,
      method: ctx.lastState.method,
      toggles: ctx.lastState.toggles,
      segments: segs,
    };

    if (status) { status.textContent = '⏳ Сохраняю…'; status.style.color = '#92400e'; }
    try {
      const r = await fetch('/api/customer-daily/blocks', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          well_id: ctx.getWellId(),
          kind: ctx.attachedKind || 'comparison',  // 'comparison' для шага 3, 'adaptation_comparison' для шага 5
          title: title,
          params: {
            kind_detail: 'segments_comparison',
            metric: ctx.lastState.metric,
            method: ctx.lastState.method,
            segments_count: segs.length,
            stage: ctx.stageLabel,
          },
          data_snapshot: snap,
          comment: desc,
          in_report: true,
        }),
      });
      const d = await r.json();
      if (!r.ok) {
        if (status) {
          status.textContent = '✗ ' + (d.detail || 'HTTP ' + r.status);
          status.style.color = '#dc2626';
        }
        return;
      }
      if (status) {
        status.textContent = '✓ Блок сравнения #' + (d.id || '?') + ' сохранён';
        status.style.color = '#059669';
      }
      // Сбрасываем title и description чтобы можно было сохранить новое сравнение
      $((ctx.prefix + '-save-title')).value = '';
      $((ctx.prefix + '-save-desc')).value = '';
      // Универсальный callback после сохранения (ctx.onSaved). Каждая страница
      // переопределяет: customer-daily → refreshAttachedBlocks, wizard → wz3LoadAttachedBlocks.
      // Fallback цепочка для обратной совместимости с wizard функциями.
      try {
        if (typeof ctx.onSaved === 'function') {
          await ctx.onSaved();
        } else if (ctx.prefix === 'wz5cmp' && typeof wz5LoadAttachedBlocks === 'function') {
          wz5LoadAttachedBlocks().catch(() => {});
        } else if (ctx.prefix === 'wz3cmp' && typeof wz3LoadAttachedBlocks === 'function') {
          wz3LoadAttachedBlocks().catch(() => {});
        } else if (typeof refreshAttachedBlocks === 'function') {
          refreshAttachedBlocks();
        }
      } catch (e) { console.warn('cmp onSaved callback failed', e); }
      setTimeout(() => { if (status) status.textContent = ''; }, 5000);
    } catch (e) {
      if (status) {
        status.textContent = '✗ Сетевая ошибка: ' + e.message;
        status.style.color = '#dc2626';
      }
    }
  }

  // ─── Расчёт линейной регрессии (МНК) ──────────────────────────────
  // Возвращает {slope, intercept, r2, n} для пары массивов x, y
  function _linRegress(x, y) {
    const v = [];
    for (let i = 0; i < x.length; i++) {
      if (x[i] != null && y[i] != null && !isNaN(x[i]) && !isNaN(y[i])) {
        v.push([+x[i], +y[i]]);
      }
    }
    const n = v.length;
    if (n < 2) return { slope: null, intercept: null, r2: null, n };
    let sx = 0, sy = 0;
    for (const [a, b] of v) { sx += a; sy += b; }
    const mx = sx / n, my = sy / n;
    let num = 0, denX = 0, denY = 0;
    for (const [a, b] of v) {
      num  += (a - mx) * (b - my);
      denX += (a - mx) ** 2;
      denY += (b - my) ** 2;
    }
    if (denX === 0) return { slope: 0, intercept: my, r2: null, n };
    const slope = num / denX;
    const intercept = my - slope * mx;
    const r2 = denY === 0 ? null : (num * num) / (denX * denY);
    return { slope, intercept, r2, n };
  }

  // ─── Theil-Sen робастный slope (медиана попарных наклонов) ────────
  // Не использует МНК, не чувствителен к выбросам
  function _theilSen(x, y) {
    const v = [];
    for (let i = 0; i < x.length; i++) {
      if (x[i] != null && y[i] != null && !isNaN(x[i]) && !isNaN(y[i])) {
        v.push([+x[i], +y[i]]);
      }
    }
    const n = v.length;
    if (n < 2) return { slope: null, intercept: null, r2: null, n };
    // Сэмплируем не более 5000 пар чтобы не повесить браузер
    const slopes = [];
    const maxPairs = 5000;
    const step = Math.max(1, Math.floor(n * (n - 1) / 2 / maxPairs));
    let cnt = 0;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        if (++cnt % step !== 0) continue;
        const dx = v[j][0] - v[i][0];
        if (dx === 0) continue;
        slopes.push((v[j][1] - v[i][1]) / dx);
      }
    }
    if (!slopes.length) return { slope: 0, intercept: v[0][1], r2: null, n };
    slopes.sort((a, b) => a - b);
    const slope = slopes.length % 2
      ? slopes[(slopes.length - 1) >> 1]
      : (slopes[slopes.length / 2 - 1] + slopes[slopes.length / 2]) / 2;
    // Intercept = медиана (y_i - slope * x_i)
    const inters = v.map(([a, b]) => b - slope * a).sort((a, b) => a - b);
    const intercept = inters.length % 2
      ? inters[(inters.length - 1) >> 1]
      : (inters[inters.length / 2 - 1] + inters[inters.length / 2]) / 2;
    return { slope, intercept, r2: null, n };
  }

  // Загружает детальные данные UniTool (минутное/часовое разрешение)
  // через /api/flow-rate/calculate, рисует 4 раздельных графика как
  // в «Графики этапа». На каждом — drag-select для добавления сегмента.
  // Опционально накладывает UzKorGaz (суточные точки) поверх.
  // ctx.fullData = загруженные detail-данные (см. ctx-объекты выше)
  let wz3cmpCustOverlay = null;
  async function _wz3cmpLoadSourceChart(ctx = wz3cmpCtx) {
    const status = $((ctx.prefix + '-source-status'));
    const block = $((ctx.prefix + '-charts-block'));
    if (!block || !ctx.getWellId()) return;
    if (status) { status.textContent = '⏳ Загружаю детальные данные UniTool…'; status.style.color = '#92400e'; }

    // Берём диапазоны UzKorGaz + UniTool + этапы + дату установки
    let ourFrom, ourTo, custFrom, custTo, equipDt;
    try {
      const r = await fetch('/api/adaptation-report/timeline?well_id=' + ctx.getWellId()
                            + '&_=' + Date.now(), { cache: 'no-store' });
      const tl = await r.json();
      ourFrom  = tl.our_data?.first_date;
      ourTo    = tl.our_data?.last_date;
      custFrom = tl.customer_data?.first_date;
      custTo   = tl.customer_data?.last_date;
      const equipEv = (tl.events || []).find(e => e.kind === 'equip');
      equipDt  = equipEv?.dt ? equipEv.dt.slice(0, 10) : null;
      ctx._cmpStages = tl.stages || [];
    } catch (e) {
      if (status) { status.textContent = '✗ ' + e.message; status.style.color = '#dc2626'; }
      return;
    }
    // Полный диапазон ось X — объединение UzKorGaz и UniTool (если оба есть)
    const allFrom = [ourFrom, custFrom].filter(Boolean).sort()[0];
    const allTo   = [ourTo, custTo].filter(Boolean).sort().slice(-1)[0];
    // Для UniTool fetch берём от equip_dt (если был) до конца — отбрасываем
    // pressure_raw до установки оборудования (артефакт привязки)
    const fetchOurFrom = (equipDt && ourFrom && equipDt > ourFrom) ? equipDt : ourFrom;
    const fetchOurTo   = ourTo;
    const dateFrom = fetchOurFrom;  // legacy var, используется ниже
    const dateTo   = fetchOurTo;
    if ((!dateFrom || !dateTo) && !custFrom) {
      if (status) { status.textContent = '⚠ Нет данных ни в pressure_raw, ни в well_daily для этой скважины.'; status.style.color = '#dc2626'; }
      block.style.display = 'none';
      return;
    }

    // Загружаем минутный/часовой ряд через flow-rate (или fallback pressure-chart)
    const start = `${dateFrom}T00:00:00`;
    const end   = `${dateTo}T23:59:59`;
    let chart = null;
    try {
      const fr = await fetch(`/api/flow-rate/calculate/${ctx.getWellId()}` +
                             `?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
      if (fr.ok) {
        const fj = await fr.json();
        chart = fj.chart;
      } else {
        const pr = await fetch(`/api/pressure/chart/${ctx.getWellId()}` +
                               `?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}` +
                               `&interval=60&mode=masked`);
        if (!pr.ok) throw new Error('HTTP ' + pr.status);
        const pj = await pr.json();
        const pts = (pj.points || []).filter(p => p.t);
        chart = {
          timestamps: pts.map(p => p.t),
          p_tube: pts.map(p => p.p_tube_avg ?? null),
          p_line: pts.map(p => p.p_line_avg ?? null),
          flow_rate: [], cumulative_flow: [],
        };
      }
    } catch (e) {
      if (status) { status.textContent = '✗ ' + e.message; status.style.color = '#dc2626'; }
      return;
    }
    const ts = chart?.timestamps || [];
    if (!ts.length) {
      if (status) { status.textContent = '⚠ Пустой ответ.'; status.style.color = '#dc2626'; }
      return;
    }
    const pt = chart.p_tube || [];
    const pl = chart.p_line || [];
    const fr = chart.flow_rate || [];
    const dp = ts.map((_, i) => {
      const a = pt[i], b = pl[i];
      if (a == null || b == null) return null;
      return Math.max(0, +(a - b).toFixed(2));
    });
    ctx.fullData = {
      dates: ts, p_tube: pt, p_line: pl, dp, q: fr, dateFrom, dateTo,
    };

    // Опционально: overlay UzKorGaz за ПОЛНЫЙ его диапазон (custFrom..custTo),
    // НЕ ограниченный UniTool. Так данные январь-февраль увидены до monтажа.
    if ($((ctx.prefix + '-overlay-cust'))?.checked && custFrom && custTo) {
      try {
        const wn = ctx.getWellNumber();
        const cr = await fetch(`/api/customer-daily/well/${encodeURIComponent(wn)}` +
                               `?date_from=${custFrom}&date_to=${custTo}`);
        if (cr.ok) {
          const cd = await cr.json();
          if (cd.ok && cd.chart) wz3cmpCustOverlay = cd.chart;
        }
      } catch (_) {}
    } else {
      wz3cmpCustOverlay = null;
    }

    block.style.display = '';
    _wz3cmpRenderRichCharts(ctx);
    if (status) {
      const dropNote = (equipDt && ourFrom && equipDt > ourFrom)
        ? ` (отбросили pressure_raw до ${equipDt} — до монтажа)` : '';
      status.innerHTML = `✓ UniTool: <b>${ts.length}</b> точек ${dateFrom}…${dateTo}${dropNote}` +
        (wz3cmpCustOverlay
          ? ` · UzKorGaz: <b>${(wz3cmpCustOverlay.dates||[]).length}</b> сут ${custFrom}…${custTo}`
          : '') +
        '. <b>Чтобы выбрать участок</b> — нажмите иконку <b>📦 Box Select</b> в верхнем меню любого графика и проведите мышью по горизонтали.';
      status.style.color = '#059669';
    }
  }

  function _wz3cmpRenderRichCharts(ctx = wz3cmpCtx) {
    const data = ctx.fullData;
    if (!data) return;
    const cust = wz3cmpCustOverlay;
    const vzPt = $((ctx.prefix + '-vz-pt'))?.checked, vzPl = $((ctx.prefix + '-vz-pl'))?.checked;
    const vzDp = $((ctx.prefix + '-vz-dp'))?.checked, vzQ  = $((ctx.prefix + '-vz-q'))?.checked;

    // Зум оси X. Сами данные уже загружены за полный период
    // (UniTool: equip…ourTo, UzKorGaz: custFrom…custTo) — здесь только
    // вычисляется диапазон отображения по выбранному режиму.
    //
    // Источники дат этапа: 1) step3 wz3-from/to (если уже выбрали),
    //                       2) этап «Наблюдение» из ctx._cmpStages,
    //                       3) stages_status.obs_from/to,
    //                       4) stages_events (events-based) obs_from + adapt_from.
    let xRange = null;
    const fmt = (s) => s ? String(s).slice(0, 19) : null;
    let obsFrom = fmt(((ctx._stepsHint && ctx._stepsHint()) || {})?.from);
    let obsTo   = fmt(((ctx._stepsHint && ctx._stepsHint()) || {})?.to);
    if (!obsFrom || !obsTo) {
      const obsStage = (ctx._cmpStages || []).find(s => /набл/i.test(s.label || ''));
      if (obsStage && obsStage.dt_start && obsStage.dt_end) {
        obsFrom = fmt(obsStage.dt_start);
        obsTo   = fmt(obsStage.dt_end);
      }
    }
    if ((!obsFrom || !obsTo) && ((ctx._metadataHint && ctx._metadataHint()) || {}).stages_status) {
      obsFrom = obsFrom || fmt(((ctx._metadataHint && ctx._metadataHint()) || {}).stages_status.obs_from);
      obsTo   = obsTo   || fmt(((ctx._metadataHint && ctx._metadataHint()) || {}).stages_status.obs_to);
    }
    if ((!obsFrom || !obsTo) && ((ctx._metadataHint && ctx._metadataHint()) || {}).stages_events) {
      obsFrom = obsFrom || fmt(((ctx._metadataHint && ctx._metadataHint()) || {}).stages_events.obs_from);
      obsTo   = obsTo   || fmt(((ctx._metadataHint && ctx._metadataHint()) || {}).stages_events.adapt_from);
    }
    const mode = $((ctx.prefix + '-range-mode'))?.value || 'obs_x2';
    const ourFromS  = data.dateFrom ? `${data.dateFrom}T00:00:00` : null;
    const ourToS    = data.dateTo   ? `${data.dateTo}T23:59:59`   : null;
    const custFromS = cust?.dates?.[0] ? String(cust.dates[0]).slice(0,10) + 'T00:00:00' : null;
    const custToS   = cust?.dates?.length
      ? String(cust.dates[cust.dates.length - 1]).slice(0,10) + 'T23:59:59'
      : null;
    const allFrom = [ourFromS, custFromS].filter(Boolean).sort()[0];
    const allTo   = [ourToS,   custToS  ].filter(Boolean).sort().slice(-1)[0];
    if (mode === 'manual') {
      const mF = $((ctx.prefix + '-range-from'))?.value;
      const mT = $((ctx.prefix + '-range-to'))?.value;
      if (mF && mT) xRange = [`${mF}T00:00:00`, `${mT}T23:59:59`];
      else if (allFrom && allTo) xRange = [allFrom, allTo];
    } else if (mode === 'all') {
      if (allFrom && allTo) xRange = [allFrom, allTo];
    } else if (obsFrom && obsTo) {
      const f = new Date(obsFrom).getTime();
      const t = new Date(obsTo).getTime();
      const len = Math.max(t - f, 86400000);  // минимум 1 сутки
      // padDays = 0 → используем длительность этапа (режим 'obs_x2')
      const padMap = { 'obs_x2': 0, 'obs_7d': 7, 'obs_30d': 30, 'obs_90d': 90 };
      const padD = padMap[mode] ?? 0;
      const padMs = padD > 0 ? padD * 86400000 : len;
      let lo = f - padMs;
      let hi = t + padMs;
      // Не выходим за пределы реальных данных — иначе пустой кадр
      if (allFrom) {
        const aF = new Date(allFrom).getTime();
        if (lo < aF) lo = aF;
      }
      if (allTo) {
        const aT = new Date(allTo).getTime();
        if (hi > aT) hi = aT;
      }
      xRange = [
        new Date(lo).toISOString().slice(0, 19),
        new Date(hi).toISOString().slice(0, 19),
      ];
    } else if (allFrom && allTo) {
      // Этап не определён — показываем всё что есть
      xRange = [allFrom, allTo];
    }

    // Vertical shapes для этапов (Наблюдение / Адаптация / Оптимизация)
    const stageShapes = [];
    const stageAnnots = [];
    const stages = ctx._cmpStages || [];  // набивается в _wz3cmpLoadSourceChart
    const stageColor = (lbl) => /набл/i.test(lbl) ? 'rgba(29,78,216,0.10)'
      : /адапт/i.test(lbl) ? 'rgba(217,119,6,0.10)'
      : /оптимиз/i.test(lbl) ? 'rgba(22,163,74,0.10)'
      : 'rgba(107,114,128,0.08)';
    const stageLine = (lbl) => /набл/i.test(lbl) ? '#1d4ed8'
      : /адапт/i.test(lbl) ? '#d97706'
      : /оптимиз/i.test(lbl) ? '#16a34a'
      : '#6b7280';
    for (const s of stages) {
      if (!s.dt_start) continue;
      const x0 = s.dt_start;
      const x1 = s.dt_end || data.dates[data.dates.length - 1];
      const c = stageColor(s.label || '');
      const lc = stageLine(s.label || '');
      stageShapes.push({
        type: 'rect', xref: 'x', yref: 'paper',
        x0, x1, y0: 0, y1: 1,
        fillcolor: c, line: { width: 0 },
        layer: 'below',
      });
      stageShapes.push({
        type: 'line', xref: 'x', yref: 'paper',
        x0, x1: x0, y0: 0, y1: 1,
        line: { color: lc, width: 1.5, dash: 'dot' },
      });
      stageAnnots.push({
        x: x0, y: 1, xref: 'x', yref: 'paper',
        text: (s.label || '').slice(0, 12),
        showarrow: false, yanchor: 'top', xanchor: 'left',
        font: { size: 9, color: lc },
        bgcolor: 'rgba(255,255,255,0.85)', borderpad: 2,
      });
    }

    const layout = (title, ytitle) => ({
      title, margin: { l: 50, r: 30, t: 40, b: 40 },
      hovermode: 'x unified', font: { size: 10 },
      legend: { orientation: 'h', y: -0.18 },
      xaxis: { type: 'date', range: xRange },
      yaxis: { title: ytitle },
      dragmode: 'select', selectdirection: 'h',
      shapes: stageShapes,
      annotations: stageAnnots,
    });
    const cfg = {
      displaylogo: false, responsive: true,
      // Box Select явно в toolbar — пользователь может переключаться
      // между Zoom / Pan / Box Select. autoScale (домик) — чтобы быстро
      // увидеть весь диапазон.
      modeBarButtonsToAdd: ['select2d'],
      modeBarButtonsToRemove: ['lasso2d'],
    };

    // 1. Давления (наши)
    const presTraces = [];
    if (vzPt) presTraces.push({ x: data.dates, y: data.p_tube, name: 'P трубное',
      mode: 'lines', line: { color: '#1d4ed8', width: 1.4 } });
    if (vzPl) presTraces.push({ x: data.dates, y: data.p_line, name: 'P линейное',
      mode: 'lines', line: { color: '#dc2626', width: 1.4 } });
    if (cust) {
      presTraces.push(
        { x: cust.dates, y: cust.p_wellhead, name: 'UzKorGaz: устье',
          mode: 'lines+markers', marker: { size: 4 }, line: { width: 1, dash: 'dash', color: '#1e3a8a' } },
        { x: cust.dates, y: cust.p_flowline, name: 'UzKorGaz: шлейф',
          mode: 'lines+markers', marker: { size: 4 }, line: { width: 1, dash: 'dash', color: '#7f1d1d' } },
      );
    }
    Plotly.newPlot((ctx.prefix + '-ch-pres'), presTraces, layout('Давления', 'кгс/см²'), cfg);
    document.getElementById((ctx.prefix + '-ch-pres'))._stageShapes = stageShapes;
    _wz3cmpWireSelect((ctx.prefix + '-ch-pres'), ctx);

    // 2. ΔP
    const dpTraces = [];
    if (vzDp) dpTraces.push({ x: data.dates, y: data.dp, name: 'ΔP',
      mode: 'lines', line: { color: '#ea580c', width: 1.6 } });
    if (cust && cust.dp) {
      dpTraces.push({ x: cust.dates, y: cust.dp, name: 'UzKorGaz: ΔP',
        mode: 'lines+markers', marker: { size: 4 }, line: { width: 1, dash: 'dash', color: '#7c2d12' } });
    }
    Plotly.newPlot((ctx.prefix + '-ch-dp'), dpTraces, layout('Перепад давления ΔP', 'кгс/см²'), cfg);
    document.getElementById((ctx.prefix + '-ch-dp'))._stageShapes = stageShapes;
    _wz3cmpWireSelect((ctx.prefix + '-ch-dp'), ctx);

    // 3. Q дебит
    const qTraces = [];
    if (vzQ && data.q && data.q.some(v => v != null)) {
      qTraces.push({ x: data.dates, y: data.q, name: 'Q (расчёт UniTool)',
        mode: 'lines', line: { color: '#16a34a', width: 1.5 } });
    }
    if (cust) {
      if (cust.q_gas_total && cust.q_gas_total.some(v => v != null)) {
        qTraces.push({ x: cust.dates, y: cust.q_gas_total, name: 'UzKorGaz: Q общий',
          mode: 'lines+markers', marker: { size: 4 }, line: { width: 1, dash: 'dash', color: '#1d4ed8' } });
      }
      if (cust.q_gas_working && cust.q_gas_working.some(v => v != null)) {
        qTraces.push({ x: cust.dates, y: cust.q_gas_working, name: 'UzKorGaz: Q рабочий',
          mode: 'lines+markers', marker: { size: 4 }, line: { width: 1, dash: 'dash', color: '#15803d' } });
      }
    }
    if (qTraces.length) {
      Plotly.newPlot((ctx.prefix + '-ch-flow'), qTraces, layout('Дебит Q', 'тыс.м³/сут'), cfg);
      document.getElementById((ctx.prefix + '-ch-flow'))._stageShapes = stageShapes;
      _wz3cmpWireSelect((ctx.prefix + '-ch-flow'), ctx);
    } else {
      Plotly.purge((ctx.prefix + '-ch-flow'));
    }

    // 4. Простой / минутный downtime от UzKorGaz (если есть)
    if (cust && cust.shutdown_min && cust.shutdown_min.some(v => v != null)) {
      const dn = cust.shutdown_min.map(v => v == null ? null : v / 60);  // в часы
      const wk = cust.shutdown_min.map(v => v == null ? null : (1440 - v) / 60);
      Plotly.newPlot((ctx.prefix + '-ch-down'), [
        { x: cust.dates, y: wk, name: 'Рабочих ч/сут',
          type: 'bar', marker: { color: '#16a34a' } },
        { x: cust.dates, y: dn, name: 'Простой ч/сут',
          type: 'bar', marker: { color: '#dc2626' } },
      ], { ...layout('Учёт рабочего времени по дням (UzKorGaz)', 'часов'), barmode: 'stack' }, cfg);
      _wz3cmpWireSelect((ctx.prefix + '-ch-down'), ctx);
    } else {
      Plotly.purge((ctx.prefix + '-ch-down'));
    }
  }

  // Привязывает plotly_selected к графику — выделение → новый сегмент.
  // Plotly.newPlot заменяет внутренние event-listeners, поэтому привязываем
  // ЗАНОВО после каждого рендера. Гард через el._wz3cmpListener чтобы не
  // плодить дубли (removeAllListeners сбрасывает старые).
  function _wz3cmpWireSelect(divId, ctx = wz3cmpCtx) {
    const el = document.getElementById(divId);
    if (!el) return;
    // Снимаем старый listener (если есть) — чтобы не плодить дубли
    if (el._wz3cmpListener && typeof el.removeAllListeners === 'function') {
      el.removeAllListeners('plotly_selected');
    }
    const listener = (ev) => {
      if (!ev || !ev.range || !ev.range.x) return;
      const [x0, x1] = ev.range.x;
      const fromTs = String(x0).slice(0, 19);
      const toTs   = String(x1).slice(0, 19);
      if (!fromTs || !toTs || fromTs === toTs) return;
      // Источник: из селекта «Источник для +сегмента»
      // 'auto' → берём UniTool если есть точки в выделении, иначе UzKorGaz
      let source = $((ctx.prefix + '-add-source'))?.value || 'auto';
      if (source === 'auto') {
        const ourTs = ctx.fullData?.dates || [];
        const fromMs = (new Date(fromTs)).getTime();
        const toMs   = (new Date(toTs)).getTime();
        const hasOur = ourTs.some(t => {
          const m = (new Date(t)).getTime();
          return m >= fromMs && m <= toMs;
        });
        source = hasOur ? 'our_pressure' : 'customer';
      }
      _wz3cmpAddSegmentFromDates(source, fromTs, toTs, ctx);
      try { Plotly.relayout(el, { selections: [] }); } catch (_) {}
    };
    el._wz3cmpListener = listener;
    el.on('plotly_selected', listener);
  }

  // Добавление сегмента по полным timestamps (ISO 'YYYY-MM-DDTHH:MM:SS')
  function _wz3cmpAddSegmentFromDates(source, fromTs, toTs, ctx = wz3cmpCtx) {
    const id = 'seg' + Date.now() + Math.random().toString(36).slice(2, 6);
    const color = CMP_PALETTE[ctx.segments.length % CMP_PALETTE.length];
    // f / t — даты для fetch endpoint'а
    const f = String(fromTs).slice(0, 10);
    const t = String(toTs).slice(0, 10);
    const seg = {
      id, source, color,
      from: f, to: t,           // YYYY-MM-DD для совместимости с fetch
      fromTs: fromTs, toTs: toTs, // полные timestamps для отображения и shapes
      data: null, loading: true,
    };
    ctx.segments.push(seg);
    _wz3cmpRender(ctx);
    _wz3cmpFetchSegment(source, f, t, ctx).then(data => {
      seg.data = data; seg.loading = false;
      _wz3cmpRender(ctx);
      _wz3cmpRenderChart(ctx);
    });
  }

  // ═══ Прикреплённые блоки наблюдения ═══

  // ═════════════ Экспорт в window ═════════════
  window.CMP_PALETTE = CMP_PALETTE;
  window.CMP_KEY_MAP = CMP_KEY_MAP;
  window.cmpRenderHTML = cmpRenderHTML;
  window._wz3cmpFetchSegment = _wz3cmpFetchSegment;
  window._wz3cmpAddSegment = _wz3cmpAddSegment;
  window._wz3cmpRemoveSegment = _wz3cmpRemoveSegment;
  window._wz3cmpRender = _wz3cmpRender;
  window._wz3cmpReloadSegment = _wz3cmpReloadSegment;
  window._wz3cmpRedrawSelectionShapes = _wz3cmpRedrawSelectionShapes;
  window._wz3cmpRenderChart = _wz3cmpRenderChart;
  window._wz3cmpUpdateSaveBlock = _wz3cmpUpdateSaveBlock;
  window._wz3cmpBuildAutoDesc = _wz3cmpBuildAutoDesc;
  window._wz3cmpRenderTrendsTable = _wz3cmpRenderTrendsTable;
  window._wz3cmpBindHandlers = _wz3cmpBindHandlers;
  window._wz3cmpSaveAsBlock = _wz3cmpSaveAsBlock;
  window._wz3cmpLoadSourceChart = _wz3cmpLoadSourceChart;
  window._wz3cmpRenderRichCharts = _wz3cmpRenderRichCharts;
  window._wz3cmpWireSelect = _wz3cmpWireSelect;
  window._wz3cmpAddSegmentFromDates = _wz3cmpAddSegmentFromDates;
})();
