/*
 * chapter_render.js — общий движок рендера главы отчёта из блоков.
 *
 * Извлечён из customer_daily.html (renderChapterPreview + куст).
 * Текстовый рендер: блоки + снапшоты -> HTML текста главы.
 * Переиспользуется любой главой (Заказчик / Наблюдение / Адаптация).
 *
 * Публичный API: window.renderChapter(blocks, config)
 *   config = { containerId, countId, chapterTitle, sourceLabel,
 *              kindOrder?, numPrefix? }
 *
 * Графики (_render*Charts) — опциональны, добавляются отдельно.
 */
(function () {
  'use strict';
  function $(id) { return document.getElementById(id); }

  // ===== PART_LABELS + _getPartsForBlock (извлечено 1-в-1) =====
  const PART_LABELS = {
    chapter_intro: [
      ['text', '📝 Текст вступления главы'],
    ],
    period_analysis: [
      ['prefix_note',           '↑ Вступительный текст'],
      ['intro',                 '📝 Сводка периода (динамический текст)'],
      ['description',           '✏️ Комментарий пользователя'],
      ['key_metrics',           '🟧 Плитки «Ключевые показатели»'],
      ['pressures_chart',       '📈 График давлений'],
      ['dp_chart',              '📉 График ΔP'],
      ['flow_dt_chart',         '⚡ График Q + простой'],
      ['monthly_chart',         '📊 Помесячный график'],
      ['describe_table',        '📋 Описательная статистика'],
      ['monthly_table',         '📋 Помесячная агрегация'],
      ['monthly_descriptions',  '📝 Помесячный анализ (UzKor + UniTool блоки)'],
      ['overlap_text',          '🔀 3 текстовых блока (UzKor/UniTool/Совм.)'],
      ['strict_compare',        '⚖ Таблица «Строгое сравнение»'],
      ['suffix_note',           '↓ Заключительный текст'],
    ],
    comparison: [
      ['prefix_note',           '↑ Вступительный текст'],
      ['segments_table',        '📋 Таблица параметров сегментов'],
      ['chart',                 '📈 График сравнения'],
      ['description',           '✏️ Комментарий пользователя'],
      ['suffix_note',           '↓ Заключительный текст'],
    ],
    criteria_rose: [
      ['prefix_note',           '↑ Вступительный текст'],
      ['rose_chart',            '📊 Роза критериев'],
      ['contributions_chart',   '📈 Бар-чарт вкладов'],
      ['metrics_table',         '📋 Таблица 6 критериев'],
      ['warnings',              '⚠️ Предупреждения'],
      ['description',           '✏️ Комментарий пользователя'],
      ['suffix_note',           '↓ Заключительный текст'],
    ],
    // Сегментный анализ: блок ПАВ-карточка + сегменты + переломы + описания.
    // По умолчанию все = true (ТЗ §0 п.3).
    segment_analysis: [
      ['prefix_note',           '↑ Вступительный текст'],
      ['pav_card',              '📊 ПАВ-карточка (балл + сценарий + рекомендация)'],
      ['signs_table',           '✓✗ Признаки за/против ПАВ'],
      ['q_segment_chart',       '📈 График Q + сегменты + переломы'],
      ['segments_table',        '📋 Таблица сегментов'],
      ['cp_descriptions',       '🔻 События переломов'],
      ['descriptions',          '📝 Развёрнутые описания сегментов'],
      ['description',           '✏️ Комментарий пользователя'],
      ['suffix_note',           '↓ Заключительный текст'],
    ],
    // ─── Observation blocks (from sensors) ───
    // observation_analysis — 4 отдельных графика из wz3RenderCharts
    observation_analysis: [
      ['prefix_note',        '↑ Вступительный текст'],
      ['intro',              '📝 Период / заголовок'],
      ['metrics_table',      '🟧 Таблица метрик'],
      ['chart_pressures',    '📈 График давлений (P тр, P лин)'],
      ['chart_dp',           '📈 График ΔP'],
      ['chart_q',            '📈 График Q(t)'],
      ['chart_utilization',  '📊 Рабочее время по дням'],
      ['quality',            '📊 Качество данных'],
      ['flags',              '⚠️ Флаги'],
      ['description',        '✏️ Текстовое описание'],
      ['suffix_note',        '↓ Заключительный текст'],
    ],
    observation_baseline: [
      ['prefix_note',       '↑ Вступительный текст'],
      ['intro',             '📝 Период / заголовок'],
      ['metrics_table',     '🟧 Таблица метрик'],
      ['chart_metrics',     '📈 График метрик'],
      ['quality',           '📊 Качество данных'],
      ['flags',             '⚠️ Флаги'],
      ['description',       '✏️ Текстовое описание'],
      ['suffix_note',       '↓ Заключительный текст'],
    ],
    observation_period: [
      ['prefix_note',              '↑ Вступительный текст'],
      ['intro',                    '📝 Период / заголовок'],
      ['metrics_table',            '🟧 Таблица метрик'],
      ['chart_timeseries',         '📈 График Q + ΔP'],
      ['chart_compare_b1',         '📊 График сравнения с B1'],
      ['comparison_with_b1',       '⚖️ Сравнение с baseline'],
      ['comparison_with_customer', '📊 Сравнение с заказчиком'],
      ['diagnostics',              '🔍 Диагностика'],
      ['daily_table',              '📋 Суточная таблица'],
      ['flags',                    '⚠️ Флаги'],
      ['description',              '✏️ Текстовое описание'],
      ['suffix_note',              '↓ Заключительный текст'],
    ],
    observation_segment: [
      ['prefix_note',       '↑ Вступительный текст'],
      ['intro',             '📝 Период / заголовок'],
      ['chart_segments',    '📈 График сегментов'],
      ['segments_table',    '📋 Таблица сегментов'],
      ['changepoints',      '🔻 Точки перелома'],
      ['diagnostics',       '🔍 Диагностика'],
      ['flags',             '⚠️ Флаги'],
      ['description',       '✏️ Текстовое описание'],
      ['suffix_note',       '↓ Заключительный текст'],
    ],
    // Сравнение сегментов (observation chapter)
    segment_comparison: [
      ['prefix_note',       '↑ Вступительный текст'],
      ['chart',             '📈 Overlay-график'],
      ['diff_table',        '📋 Таблица различий'],
      ['interpretation',    '📝 Интерпретация'],
      ['description',       '✏️ Комментарий пользователя'],
      ['suffix_note',       '↓ Заключительный текст'],
    ],
    // ─── Adaptation blocks (Step 5 wizard) ───
    adaptation_period_analysis: [
      ['prefix_note',        '↑ Вступительный текст'],
      ['comparison_table',   '📋 Таблица сравнения (Наблюд. → Адапт.)'],
      ['metrics_cards',      '🟧 Карточки метрик (R, R★, B2, B1)'],
      ['charts',             '📈 Графики (P, ΔP, Q, util)'],
      ['events_table',       '📋 Таблица событий и интервалов'],
      ['description',        '✏️ Комментарий пользователя'],
      ['suffix_note',        '↓ Заключительный текст'],
    ],
    optimal_window: [
      ['prefix_note',        '↑ Вступительный текст'],
      ['regime_summary',     '📊 Сводка окна R★'],
      ['metrics_table',      '📋 Таблица метрик окна'],
      ['description',        '✏️ Комментарий пользователя'],
      ['suffix_note',        '↓ Заключительный текст'],
    ],
    reagent_irv_summary: [
      ['prefix_note',        '↑ Вступительный текст'],
      ['summary',            '📊 Сводка (вбросов, ИРВ, лучший)'],
      ['scores_table',       '📋 Таблица Score по реагентам'],
      ['description',        '✏️ Комментарий пользователя'],
      ['suffix_note',        '↓ Заключительный текст'],
    ],
  };
  // baseline ≈ period_analysis + justification (обоснование выбора)
  PART_LABELS.baseline = [
    ['prefix_note',          '↑ Вступительный текст'],
    ['justification',        '✓ Обоснование выбора B1 (описательный текст)'],
    ...PART_LABELS.period_analysis.filter(([k]) => k !== 'prefix_note'),
  ];

  function _getPartsForBlock(b) {
    const defs = PART_LABELS[b.kind] || [];
    const saved = (b.params && b.params.parts) || {};
    const out = {};
    for (const [key] of defs) {
      // Дефолт: всё true
      out[key] = (key in saved) ? !!saved[key] : true;
    }
    return out;
  }

  // ===== _escHtml / _fmtNum / _kindBadge (извлечено 1-в-1) =====
  function _escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function _fmtNum(v, d = 2) {
    return (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d);
  }
  function _kindBadge(kind) {
    const k = ({
      chapter_intro:        ['¶', '#e0e7ff', '#3730a3', 'Общий текст главы'],
      baseline:             ['B1', '#fef3c7', '#92400e', 'Базовый период'],
      period_analysis:      ['§', '#dcfce7', '#166534', 'Анализ периода'],
      comparison:           ['↔', '#ede9fe', '#5b21b6', 'Сравнение участков'],
      criteria_rose:        ['◉', '#fee2e2', '#991b1b', 'Роза критериев'],
      segment_analysis:     ['🔬', '#f3e8ff', '#5b21b6', 'Сегментный анализ'],
      // Observation (датчики)
      observation_analysis: ['📊', '#cffafe', '#0891b2', 'Анализ (датчики)'],
      observation_baseline: ['🎯', '#fef3c7', '#d97706', 'Baseline (датчики)'],
      observation_period:   ['📈', '#dcfce7', '#16a34a', 'Period (датчики)'],
      observation_segment:  ['🔬', '#f3e8ff', '#5b21b6', 'Segment (датчики)'],
      // Adaptation (Step 5 wizard)
      adaptation_period_analysis: ['📊', '#fed7aa', '#c2410c', 'Анализ адаптации'],
      optimal_window:             ['R★', '#d1fae5', '#166534', 'Окно R★'],
      reagent_irv_summary:        ['💊', '#fef3c7', '#92400e', 'Реагенты (ИРВ)'],
    })[kind] || ['•', '#f3f4f6', '#374151', kind];
    return `<span title="${k[3]}" style="display:inline-block; padding:1px 7px;
            border-radius:3px; background:${k[1]}; color:${k[2]};
            font-size:0.72rem; font-weight:600;">${k[0]} ${k[3]}</span>`;
  }


  // ===== _buildPeriodSnapshotPreviewHtml (извлечено 1-в-1) =====
  function _buildPeriodSnapshotPreviewHtml(snap, block, idPrefix) {
    if (!snap) return '';
    const fmt = (v, d=2) => (v == null || isNaN(v))
      ? '—' : Number(v).toFixed(d);
    const dFrom = snap.date_from || (block?.params?.date_from) || '—';
    const dTo   = snap.date_to   || (block?.params?.date_to)   || '—';
    const days  = snap.days || 0;
    const hasOurOverlay = !!(snap.unitool && snap.unitool.daily
                             && snap.unitool.daily.dates
                             && snap.unitool.daily.dates.length);
    const intro = (text) =>
      `<p style="margin:6px 0 10px; padding:8px 12px; background:#f0f9ff;
                 border-left:3px solid #0284c7; border-radius:4px;
                 font-size:0.85rem; color:#0c4a6e; line-height:1.5;">${text}</p>`;
    const parts = (block && typeof _getPartsForBlock === 'function')
      ? _getPartsForBlock(block) : {};
    const on = (key) => parts[key] !== false;  // дефолт = true

    let html = '';

    // ─── 1. Ключевые показатели за период ────────────────────────
    if (on('key_metrics')) {
      html +=
        `<h3 style="margin:0 0 6px;">Ключевые показатели за период</h3>` +
        intro(
          `Сводка средних и медианных значений за выбранный диапазон <b>${dFrom} → ${dTo}</b> ` +
          `(<b>${days}</b> сут.) по данным заказчика UzKorGaz` +
          (hasOurOverlay ? ' (наложение UniTool активно — см. графики и таблицы ниже).' : '.')
        ) + `
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr));
                    gap:8px; margin-bottom:18px;">
          <div class="b1-metric"><div class="b1-metric-title">Q общий</div>
            <div class="b1-metric-val">${fmt(snap.q_total_avg)} <span class="b1-unit">ср.</span></div>
            <div class="b1-metric-val">${fmt(snap.q_total_median)} <span class="b1-unit">мед.</span></div>
            <div class="b1-metric-unit">тыс.м³/сут</div></div>
          <div class="b1-metric"><div class="b1-metric-title">Q рабочий</div>
            <div class="b1-metric-val">${fmt(snap.q_working_avg)} <span class="b1-unit">ср.</span></div>
            <div class="b1-metric-val">${fmt(snap.q_working_median)} <span class="b1-unit">мед.</span></div>
            <div class="b1-metric-unit">тыс.м³/сут</div></div>
          <div class="b1-metric"><div class="b1-metric-title">ΔP</div>
            <div class="b1-metric-val">${fmt(snap.dp_avg)} <span class="b1-unit">ср.</span></div>
            <div class="b1-metric-val">${fmt(snap.dp_median)} <span class="b1-unit">мед.</span></div>
            <div class="b1-metric-unit">кгс/см²</div></div>
          <div class="b1-metric"><div class="b1-metric-title">P устье / шлейф</div>
            <div class="b1-metric-val">${fmt(snap.p_wellhead_median)} <span class="b1-unit">мед. устье</span></div>
            <div class="b1-metric-val">${fmt(snap.p_flowline_median)} <span class="b1-unit">мед. шлейф</span></div>
            <div class="b1-metric-unit">кгс/см²</div></div>
          <div class="b1-metric"><div class="b1-metric-title">Простой</div>
            <div class="b1-metric-val">${fmt(snap.shutdown_min_total, 0)} <span class="b1-unit">мин всего</span></div>
            <div class="b1-metric-val">${snap.shutdown_min_total ? (snap.shutdown_min_total/60).toFixed(1) : '0'} <span class="b1-unit">часов</span></div>
            <div class="b1-metric-unit">${snap.shutdown_days_count || 0} дней с простоем</div></div>
        </div>`;
    }

    // ─── 2. Графики динамики (2×2 grid) ───────────────────────────
    if (snap.chart && snap.chart.dates && snap.chart.dates.length) {
      const hasAnyChart = on('pressures_chart') || on('dp_chart')
                       || on('flow_dt_chart')  || on('monthly_chart');
      if (hasAnyChart) {
        html +=
          `<h3 style="margin:14px 0 6px;">Графики динамики показателей</h3>` +
          intro(
            'Суточная динамика давлений (устье / затрубное / шлейф / статическое), перепада ΔP, ' +
            'дебитов (общий и рабочий), а также времени простоя по данным UzKorGaz.' +
            (hasOurOverlay
              ? ' Поверх (заливка) наложены данные UniTool с LoRa-датчиков, агрегированные посуточно.'
              : '')
          ) + `
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
            ${on('pressures_chart') ? `<div id="${idPrefix}pres" style="height:300px;"></div>` : ''}
            ${on('dp_chart')        ? `<div id="${idPrefix}dp"   style="height:300px;"></div>` : ''}
            ${on('flow_dt_chart')   ? `<div id="${idPrefix}flow" style="height:300px;"></div>` : ''}
            ${on('monthly_chart')   ? `<div id="${idPrefix}mon"  style="height:300px;"></div>` : ''}
          </div>`;
      }
    }

    // ─── 3. Статистика по показателям (describe) ─────────────────
    if (on('describe_table') && snap.describe && snap.describe.length) {
      html +=
        `<h3 style="margin:18px 0 6px;">Статистика по показателям</h3>` +
        intro(
          'Сводная таблица по каждому параметру — количество измерений, среднее, ' +
          'СКО (рассеяние) и медиана. По СКО видно стабильность параметра в периоде.'
        );
      let descHtml = `<table class="cd-table"><thead><tr>
          <th>Параметр</th><th>N</th><th>Среднее</th><th>СКО</th><th>Медиана</th>
        </tr></thead><tbody>`;
      for (const r of snap.describe) {
        descHtml += `<tr><td>${r.label}</td>
          <td>${r.n}</td><td>${fmt(r.mean)}</td>
          <td>${fmt(r.std)}</td><td>${fmt(r.median)}</td></tr>`;
      }
      descHtml += '</tbody></table>';
      html += descHtml;
    }

    // ─── 4. Динамика показателей по месяцам (monthly) ────────────
    if (on('monthly_table') && snap.monthly && snap.monthly.length) {
      html +=
        `<h3 style="margin:18px 0 6px;">Динамика показателей по месяцам</h3>` +
        intro(
          'Помесячная агрегация показателей UzKorGaz. По колонкам Q видно изменение дебита ' +
          'между месяцами; ΔP — изменение перепада давления (рост ΔP обычно говорит о ' +
          'засорении призабойной зоны или ствола скважины); простой — суммарное время ' +
          'неработающего состояния скважины в среднем за день месяца.'
        );
      let mHtml = `<table class="cd-table"><thead><tr>
          <th>Месяц</th><th>Дней</th>
          <th>Q общ. ср.</th><th>Q общ. мед.</th>
          <th>Q раб. ср.</th><th>Q раб. мед.</th>
          <th>ΔP ср.</th><th>ΔP мед.</th>
          <th>P уст. ср.</th><th>Простой</th>
        </tr></thead><tbody>`;
      for (const r of snap.monthly) {
        mHtml += `<tr>
          <td>${r.month_label}</td><td>${r.days}</td>
          <td>${fmt(r.mean_q_total)}</td><td>${fmt(r.median_q_total)}</td>
          <td>${fmt(r.mean_q_working)}</td><td>${fmt(r.median_q_working)}</td>
          <td>${fmt(r.mean_dp)}</td><td>${fmt(r.median_dp)}</td>
          <td>${fmt(r.mean_p_wellhead)}</td><td>${fmt(r.mean_shutdown, 1)}</td>
        </tr>`;
      }
      mHtml += '</tbody></table>';
      html += mHtml;
    }

    // ─── 5. Развёрнутое описание по месяцам (UzKor + UniTool) ────
    if (on('monthly_descriptions')
        && ((snap.monthly_desc && snap.monthly_desc.length)
            || (snap.unitool && snap.unitool.by_month))) {
      html +=
        `<h3 style="margin:18px 0 6px;">Развёрнутое описание изменений по месяцам</h3>` +
        intro(
          'Поясняющий текст к каждому месяцу: средние значения, тренды, изменения ' +
          'по сравнению с предыдущим месяцем. Для месяцев, в которых есть данные UniTool, ' +
          'добавлен отдельный блок с метриками датчиков и оценкой расхождения с UzKorGaz.'
        );
      const descs = snap.monthly_desc || [];
      const ourBy = snap.unitool?.by_month || {};
      const monthsCust = snap.monthly || [];
      let monthsHtml = '<div class="cd-month-list">';
      for (const d of descs) {
        const cust = `<div style="margin-top:4px; padding:8px 10px; background:#dbeafe;
                                  border-left:3px solid #1d4ed8; border-radius:4px;">
                        <div style="font-weight:600; color:#1e3a8a; font-size:0.78rem;
                                    margin-bottom:3px;">📊 UzKorGaz</div>
                        <div class="text" style="font-size:0.84rem; line-height:1.55;">${d.text}</div>
                      </div>`;
        let our = '';
        const o = ourBy[d.label];
        if (o && o.days) {
          const cust_r = monthsCust.find(m => m.month_label === d.label) || {};
          const custDays = cust_r.days || 0;
          const cmpRatio = Math.max(custDays, o.days)
            ? Math.abs(custDays - o.days) / Math.max(custDays, o.days) * 100 : 0;
          const compat = cmpRatio <= 10;
          const lines = [];
          lines.push(`За месяц записано <b>${o.days}</b> сут измерений.`);
          if (o.mean_q != null || o.median_q != null) {
            lines.push(`Q (расчётный): среднее <b>${fmt(o.mean_q)}</b>, медиана <b>${fmt(o.median_q)}</b> тыс.м³/сут.`);
          }
          if (o.mean_dp != null || o.median_dp != null) {
            lines.push(`Перепад давления: среднее <b>${fmt(o.mean_dp)}</b>, медиана <b>${fmt(o.median_dp)}</b> кгс/см².`);
          }
          if (o.mean_p_tube != null) {
            lines.push(`Среднее устьевое давление: <b>${fmt(o.mean_p_tube)}</b> кгс/см².`);
          }
          if (compat && cust_r.mean_q_total != null && o.mean_q != null) {
            const dQ = (o.mean_q - cust_r.mean_q_total) / cust_r.mean_q_total * 100;
            const dDp = (cust_r.mean_dp != null && o.mean_dp != null && cust_r.mean_dp !== 0)
              ? (o.mean_dp - cust_r.mean_dp) / cust_r.mean_dp * 100 : null;
            const arr = (v) => v >= 0 ? '↑' : '↓';
            const cmp = [`Q ${arr(dQ)} ${(dQ >= 0 ? '+' : '') + dQ.toFixed(1)}%`];
            if (dDp != null) cmp.push(`ΔP ${arr(dDp)} ${(dDp >= 0 ? '+' : '') + dDp.toFixed(1)}%`);
            lines.push(`<b>Расхождение с UzKorGaz:</b> ${cmp.join(', ')}.`);
          }
          const compatNote = compat
            ? ` <span style="color:#059669;">Сопоставимо с UzKorGaz (${custDays} сут): разница ${cmpRatio.toFixed(1)}% ≤ 10%.</span>`
            : ` <span style="color:#b45309;">⚠ Несопоставимо с UzKorGaz (${custDays} сут): разница ${cmpRatio.toFixed(1)}% &gt; 10%.</span>`;
          our = `<div style="margin-top:6px; padding:8px 10px; background:#fed7aa;
                              border-left:3px solid #c2410c; border-radius:4px;">
                   <div style="font-weight:600; color:#7c2d12; font-size:0.78rem;
                               margin-bottom:3px;">🌐 UniTool (LoRa-датчики)</div>
                   <div class="text" style="font-size:0.84rem; line-height:1.55;">
                     ${lines.join(' ')}${compatNote}
                   </div>
                 </div>`;
        } else if (snap.unitool) {
          our = `<div style="margin-top:6px; padding:8px 10px; background:#f3f4f6;
                              border-left:3px solid #9ca3af; border-radius:4px;
                              font-size:0.82rem; color:#6b7280; font-style:italic;">
                   🌐 UniTool: нет данных за этот месяц
                   ${snap.unitool.equip_dt ? `(датчик установлен ${snap.unitool.equip_dt.slice(0,10)})` : ''}
                 </div>`;
        }
        monthsHtml += `<div class="cd-month-item">
          <div class="label">${d.label}</div>${cust}${our}
        </div>`;
      }
      monthsHtml += '</div>';
      html += monthsHtml;
    }

    // ─── 6. Анализ периода (overlap_html) ────────────────────────
    if (on('overlap_text') && snap.overlap_html) {
      html +=
        `<h3 style="margin:18px 0 6px;">Анализ периода: UzKorGaz и UniTool</h3>` +
        intro(
          'Развёрнутое описание выбранного периода по двум источникам — суточные сводки ' +
          'заказчика и LoRa-датчики UniTool — с указанием дат, объёма данных, ключевых ' +
          'метрик и совместного периода (когда измерения есть и там, и там).'
        ) + `<div>${snap.overlap_html}</div>`;
    }

    // ─── 7. Сопоставление UzKor vs UniTool на совпадающих днях ───
    if (on('strict_compare') && snap.strict_compare) {
      const sc = snap.strict_compare;
      const sign = (v) => v == null ? '—' : (v >= 0 ? '+' : '') + fmt(v);
      const pct = (a, b) => (a != null && b != null && b !== 0) ? (a-b)/b*100 : null;
      const mkRow = (label, c, o) => {
        const dM = (c.mean != null && o.mean != null) ? (o.mean - c.mean) : null;
        const dMp = pct(o.mean, c.mean);
        const dMd = (c.median != null && o.median != null) ? (o.median - c.median) : null;
        const dMdp = pct(o.median, c.median);
        return `<tr><td>${label}</td>
          <td>${fmt(c.mean)}</td><td>${fmt(c.median)}</td>
          <td>${fmt(o.mean)}</td><td>${fmt(o.median)}</td>
          <td>${sign(dM)}</td><td>${dMp != null ? sign(dMp) + '%' : '—'}</td>
          <td>${sign(dMd)}</td><td>${dMdp != null ? sign(dMdp) + '%' : '—'}</td>
        </tr>`;
      };
      html +=
        `<h3 style="margin:18px 0 6px;">Сопоставление UzKorGaz и UniTool на совпадающих днях</h3>` +
        intro(
          `Когда периоды UzKorGaz и UniTool различаются по длине, прямое сравнение средних ` +
          `некорректно. Здесь метрики посчитаны <b>только на тех днях, где есть оба источника</b> ` +
          `(${sc.days} сут., ${sc.dates_from} — ${sc.dates_to}). Δ показывает расхождение между ` +
          `источниками: положительное = UniTool выше UzKorGaz, отрицательное = ниже.`
        ) + `
        <table class="cd-table"><thead><tr>
          <th rowspan="2">Параметр</th>
          <th colspan="2" style="background:#dbeafe;">UzKorGaz</th>
          <th colspan="2" style="background:#fed7aa;">UniTool</th>
          <th colspan="4" style="background:#fef3c7;">Δ (UniTool − UzKorGaz)</th>
        </tr><tr>
          <th>Среднее</th><th>Медиана</th>
          <th>Среднее</th><th>Медиана</th>
          <th>Δ ср.</th><th>Δ ср. %</th>
          <th>Δ мед.</th><th>Δ мед. %</th>
        </tr></thead><tbody>
        ${mkRow('Q общий, тыс.м³/сут', sc.cust_q_total, sc.our_q)}
        ${mkRow('Q рабочий, тыс.м³/сут', sc.cust_q_working, sc.our_q)}
        ${mkRow('ΔP, кгс/см²', sc.cust_dp, sc.our_dp)}
        ${mkRow('P устье, кгс/см²', sc.cust_p_tube, sc.our_p_tube)}
        ${mkRow('P шлейф, кгс/см²', sc.cust_p_line, sc.our_p_line)}
        </tbody></table>`;
    }

    // ─── 8. Комментарий блока ───────────────────────────────────
    if (block && block.comment && on('description')) {
      html += `<h3 style="margin:18px 0 6px;">Комментарий к блоку</h3>
        <div style="padding:10px 12px; background:#f9fafb; border-left:3px solid #6b7280;
                    border-radius:4px; font-size:0.88rem; line-height:1.5;
                    white-space:pre-wrap;">${String(block.comment).replace(/</g,'&lt;')}</div>`;
    }

    return html;
  }

  // Рендер 4 Plotly-графиков в контейнеры с префиксом idPrefix.
  // Контейнеры (если включены тогглами): {prefix}pres, {prefix}dp, {prefix}flow, {prefix}mon.
  // Если контейнера нет — график пропускается. Если данных нет — пропускается.
  function _renderPeriodSnapshotCharts(snap, idPrefix) {
    if (!window.Plotly) return;
    const c = snap.chart;
    if (!c || !c.dates) return;
    const layout = { margin:{l:50,r:30,t:30,b:50}, hovermode:'x unified',
                     font:{size:10}, legend:{orientation:'h', y:-0.18} };
    const cfg = { displaylogo:false, responsive:true };
    const ud = snap.unitool?.daily;
    const hasOur = !!(ud && ud.dates && ud.dates.length);
    const ourColor = '#ea580c';
    const ourFill = (alpha) => `rgba(234, 88, 12, ${alpha})`;

    // Давления
    const elPres = document.getElementById(`${idPrefix}pres`);
    if (elPres) {
      const traces = [
        { x:c.dates, y:c.p_wellhead, name:'UzKorGaz: устье', mode:'lines+markers', line:{width:1.4} },
        { x:c.dates, y:c.p_annular,  name:'UzKorGaz: затрубное', mode:'lines+markers', line:{width:1.4} },
        { x:c.dates, y:c.p_flowline, name:'UzKorGaz: шлейф', mode:'lines+markers', line:{width:1.4} },
        { x:c.dates, y:c.p_static,   name:'UzKorGaz: статич.', mode:'lines+markers', line:{width:1.4} },
      ];
      if (hasOur) {
        traces.push(
          { x:ud.dates, y:ud.p_tube, name:'UniTool: P_tube', mode:'lines',
            line:{color:ourColor, width:2}, fill:'tozeroy', fillcolor:ourFill(0.18) },
          { x:ud.dates, y:ud.p_line, name:'UniTool: P_line', mode:'lines',
            line:{color:'#fb923c', width:2}, fill:'tozeroy', fillcolor:ourFill(0.12) },
        );
      }
      try { Plotly.newPlot(elPres, traces, { ...layout, title:'Давления, кгс/см²' }, cfg); }
      catch (e) { console.warn(e); }
    }

    // ΔP
    const elDp = document.getElementById(`${idPrefix}dp`);
    if (elDp) {
      const traces = [
        { x:c.dates, y:c.dp, name:'UzKorGaz: ΔP', mode:'lines+markers', line:{color:'#dc2626', width:1.6} },
      ];
      if (hasOur) {
        traces.push({ x:ud.dates, y:ud.dp, name:'UniTool: ΔP', mode:'lines',
          line:{color:ourColor, width:2}, fill:'tozeroy', fillcolor:ourFill(0.20) });
      }
      try { Plotly.newPlot(elDp, traces, { ...layout, title:'Перепад давления, кгс/см²' }, cfg); }
      catch (e) { console.warn(e); }
    }

    // Дебиты + простой
    const elFlow = document.getElementById(`${idPrefix}flow`);
    if (elFlow) {
      const traces = [
        { x:c.dates, y:c.q_gas_total,   name:'UzKorGaz: Q общий',   mode:'lines+markers', line:{color:'#1d4ed8', width:1.5}, yaxis:'y' },
        { x:c.dates, y:c.q_gas_working, name:'UzKorGaz: Q рабочий', mode:'lines+markers', line:{color:'#16a34a', width:1.5}, yaxis:'y' },
        { x:c.dates, y:c.shutdown_min,  name:'UzKorGaz: простой, мин', type:'bar', marker:{color:'rgba(220,38,38,0.35)'}, yaxis:'y2' },
      ];
      if (hasOur && snap.unitool?.has_flow) {
        traces.push({ x:ud.dates, y:ud.q, name:'UniTool: Q (расчёт)', mode:'lines',
          line:{color:ourColor, width:2}, fill:'tozeroy', fillcolor:ourFill(0.18), yaxis:'y' });
      }
      try {
        Plotly.newPlot(elFlow, traces, {
          ...layout, title:'Дебиты и простой',
          yaxis:{title:'Q, тыс.м³/сут'},
          yaxis2:{title:'Простой, мин', overlaying:'y', side:'right', showgrid:false, rangemode:'tozero'},
          barmode:'overlay'
        }, cfg);
      } catch (e) { console.warn(e); }
    }

    // Помесячно
    const elMon = document.getElementById(`${idPrefix}mon`);
    if (elMon && snap.monthly && snap.monthly.length) {
      const m = snap.monthly;
      const x = m.map(r => r.month_label);
      try {
        Plotly.newPlot(elMon, [
          { x, y:m.map(r=>r.mean_q_total),   name:'Q общ. ср.',  type:'bar', marker:{color:'#1d4ed8'} },
          { x, y:m.map(r=>r.median_q_total), name:'Q общ. мед.', type:'bar', marker:{color:'#93c5fd'} },
          { x, y:m.map(r=>r.mean_q_working), name:'Q раб. ср.',  type:'bar', marker:{color:'#16a34a'} },
          { x, y:m.map(r=>r.median_q_working),name:'Q раб. мед.', type:'bar', marker:{color:'#86efac'} },
        ], { ...layout, title:'Помесячно: Q', barmode:'group', yaxis:{title:'Q, тыс.м³/сут'} }, cfg);
      } catch (e) { console.warn(e); }
    }
  }

  // Дорисовка Plotly-графиков блока period_analysis / baseline /
  // observation_analysis после insert HTML. Префикс контейнеров —
  // тот же prev-{id}-, что в _renderBlockSummaryHtml.
  function _renderBlockChartsInline(b) {
    _renderPeriodSnapshotCharts(b.data_snapshot || {}, 'prev-' + (b.id || 'x') + '-');
  }

  // ===== _buildSegmentBlockHtml (извлечено 1-в-1) =====
  function _buildSegmentBlockHtml(snap, block, idPrefix) {
    if (!snap || !snap.ok) {
      return `<div style="color:#9ca3af; padding:18px;">
        ${(snap && snap.error) || 'Снимок данных пуст.'}</div>`;
    }
    const parts = (block && typeof _getPartsForBlock === 'function')
      ? _getPartsForBlock(block) : {};
    const on = (key) => parts[key] !== false;

    // PLAN A — Core источники данных (новый формат segment_v1):
    const segs = snap.segments_extended || [];
    const cpMarks = snap.cp_marks || [];
    const dualSummary = snap.dual_summary || {};
    const descriptions = snap.descriptions || [];
    const cpDescs = snap.cp_descriptions || [];
    const includePav = snap.include_pav_recommendation === true;
    const pav = (includePav && snap.pav) ? snap.pav : null;

    const fmt = (v, d=2) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d);
    const fmtSigned = (v, d=2) => {
      if (v == null || isNaN(v)) return '—';
      const n = Number(v);
      return (n > 0 ? '+' : '') + n.toFixed(d);
    };
    const intro = (text) =>
      `<p style="margin:6px 0 10px; padding:8px 12px; background:#faf5ff;
                 border-left:3px solid #7c3aed; border-radius:4px;
                 font-size:0.85rem; color:#4c1d95; line-height:1.5;">${text}</p>`;

    let html = '';

    // ─── 0. Header ────────────────────────────────────────────────
    html += `<div style="font-size:0.85rem; color:#374151; margin-bottom:8px;">
        <b>Период:</b> ${snap.date_from} … ${snap.date_to}
        (${snap.days_count} сут, ${snap.n_points} точек данных)
        ${snap.thresholds_used?.has_overrides
          ? ` <span style="color:#7c3aed; font-size:0.75rem;">⚙ пороги переопределены</span>`
          : ''}
      </div>`;

    // ─── 1. Основной график (Plotly после insert) ─────────────────
    // ТЗ §7.2: 6 слоёв (Q общий точки + линия, Q рабочий точки + линия,
    // тренды Q общего сплошные, тренды Q рабочего пунктир, вертикали
    // переломов 3 стиля, подсветка кластеров простоев, теги поворот 90°)
    if (on('q_segment_chart') && segs.length) {
      html += `<h3 style="margin:14px 0 6px;">📈 Дебит, точки перелома и линейные тренды по сегментам</h3>` +
        intro(
          'Q общий (синие кружки + линия) и Q рабочий (зелёные квадраты + линия) — ' +
          'реальные суточные точки. Цветные сплошные линии — тренды Q общего по сегментам ' +
          '(slope × x + intercept). Пунктирные того же цвета — тренды Q рабочего. ' +
          'Вертикальные линии — точки перелома: ' +
          '<b>красная сплошная толстая</b> — подтверждено обеими кривыми; ' +
          '<b>красная пунктирная тонкая</b> — только в Q общем; ' +
          '<b>зелёная пунктирная</b> — только в Q рабочем (фактический сдвиг). ' +
          'Подписи рядом с вертикалями — причины переломов. ' +
          'Розовая фоновая полоса — кластер простоев.'
        ) +
        `<div id="${idPrefix}qchart" style="height:480px;"></div>`;
    }

    // ─── 2. Таблица сегментов: 11 колонок (§5.2 + §6.1 «Тип режима»)
    if (on('segments_table') && segs.length) {
      let tbl = `<h3 style="margin:14px 0 6px;">📋 Сегменты режима (${segs.length})</h3>
        <table class="cd-table"><thead><tr>
          <th>№</th><th>Период</th><th>Дн</th>
          <th>Q общ.</th><th>Q раб.</th>
          <th>Тренд</th><th>Изм%</th>
          <th>Прост, мин</th><th>Раб%</th><th>ΔP</th>
          <th>Тип режима</th>
        </tr></thead><tbody>`;
      for (const s of segs) {
        const chg = s.change_pct;
        const chgColor = chg == null ? '#9ca3af'
                       : chg > 0 ? '#28a745' : chg < 0 ? '#dc3545' : '#6b7280';
        const chgTxt = chg == null ? '—' : (chg > 0 ? '+' : '') + chg.toFixed(0) + '%';
        const slopeTxt = s.slope_total == null ? '—' : fmtSigned(s.slope_total);
        tbl += `<tr>
          <td><b>${s.num}</b></td>
          <td style="white-space:nowrap;">${s.start}–${s.end}</td>
          <td>${s.days}</td>
          <td>${fmt(s.mean_q, 1)}</td>
          <td>${fmt(s.mean_q_working, 1)}</td>
          <td>${slopeTxt}</td>
          <td style="color:${chgColor}; font-weight:600;">${chgTxt}</td>
          <td>${fmt(s.mean_shutdown, 0)}</td>
          <td>${fmt(s.working_pct, 0)}</td>
          <td>${fmt(s.mean_dp, 2)}</td>
          <td style="font-weight:500; color:${s.color || '#374151'};">
            ${s.type || '—'}
          </td>
        </tr>`;
      }
      tbl += '</tbody></table>';
      html += tbl;
    }

    // ─── 3. Описание сегментов (§6.4) — краткие параграфы ─────────
    if (on('descriptions') && segs.length) {
      html += `<h3 style="margin:14px 0 6px;">📝 Описание сегментов</h3>` +
        `<div style="display:flex; flex-direction:column; gap:6px;">`;
      segs.forEach((s, i) => {
        const prev = i > 0 ? segs[i - 1] : null;
        const changeLine = prev
          ? `<br><b>Изменение vs Сег.${prev.num}:</b> Q общ ${fmtSigned(s.change_pct, 1)}%, ` +
            `Q раб ${fmtSigned(s.change_q_working_pct, 1)}%, ` +
            `ΔP ${fmtSigned(s.change_dp_pct, 1)}%, ` +
            `простой ${fmtSigned(s.change_shutdown, 0)} мин` +
            (s.change_choke != null && Math.abs(s.change_choke) > 0.01
              ? `, штуцер ${fmtSigned(s.change_choke, 1)} мм` : '')
          : '';
        const causeLine = s.cause
          ? `<br><b>Основной фактор:</b> ${_escHtml(s.cause)}`
          : '';
        const preshutLine = (s.preshutdown_q != null && s.preshutdown_verdict)
          ? `<br><b>До простоя/после:</b> ${fmt(s.preshutdown_q, 1)} → ${fmt(s.mean_q, 1)} ` +
            `(${fmtSigned(s.preshutdown_delta_pct, 0)}%). ${_escHtml(s.preshutdown_verdict)}`
          : '';
        const gradualLine = s.gradual_trend
          ? `<br><b>Плавный тренд:</b> ${s.gradual_trend === 'down' ? 'спад' : 'рост'} ` +
            `на ${fmtSigned(s.gradual_drift_pct, 1)}% за период`
          : '';
        html += `<div style="padding:10px 14px; background:#fff;
                  border-left:3px solid ${s.color || '#d1d5db'}; border-radius:3px;
                  font-size:0.86rem; line-height:1.55; color:#1f2937;">
            <div style="font-weight:600; margin-bottom:3px;">
              Сегмент ${s.num} · ${s.start}–${s.end} · ${s.days} дн · тип: ${s.type}
            </div>
            <b>Средние:</b> Q общ ${fmt(s.mean_q, 1)},
            Q раб ${fmt(s.mean_q_working, 1)} тыс.м³/сут,
            ΔP ${fmt(s.mean_dp, 2)} кгс/см²,
            P уст ${fmt(s.mean_p_wellhead, 1)},
            P шл ${fmt(s.mean_p_flowline, 1)} кгс/см²,
            штуцер ${fmt(s.mean_choke_mm, 1)} мм,
            раб. время ${fmt(s.working_pct, 0)}%
            ${changeLine}
            ${gradualLine}
            ${preshutLine}
            ${causeLine}
          </div>`;
      });
      html += `</div>`;
    }

    // ─── 4. События переломов (§6.5) ──────────────────────────────
    if (on('cp_descriptions') && cpDescs.length) {
      html += `<h3 style="margin:14px 0 6px;">🔻 События переломов (${cpDescs.length})</h3>` +
        `<div style="display:flex; flex-direction:column; gap:6px;">` +
        cpDescs.map(t => `<div style="padding:8px 12px; background:#fff;
                  border-left:3px solid #7c3aed; border-radius:3px;
                  font-size:0.84rem; line-height:1.55; color:#1f2937;
                  white-space:pre-wrap;">${(t||'').replace(/</g,'&lt;')}</div>`).join('') +
        `</div>`;
    }

    // ─── 5. Развёрнутые описания (модульные descriptions по §6.4) ─
    if (on('descriptions') && descriptions.length) {
      html += `<h3 style="margin:14px 0 6px;">📑 Развёрнутые описания (из алгоритма)</h3>` +
        `<div style="display:flex; flex-direction:column; gap:6px;">` +
        descriptions.map(t => `<div style="padding:8px 12px; background:#fff;
                  border-left:3px solid #d1d5db; border-radius:3px;
                  font-size:0.84rem; line-height:1.55; color:#374151;
                  white-space:pre-wrap;">${(t||'').replace(/</g,'&lt;')}</div>`).join('') +
        `</div>`;
    }

    // ─── 6. Сравнение Q общий ↔ Q рабочий (§11) ───────────────────
    // Из snap.dual_summary (формат segment_v1).
    if (dualSummary && (dualSummary.primary_cp_count != null
                        || dualSummary.working_cp_count != null)) {
      const cd = dualSummary.common_dates || [];
      const ot = dualSummary.only_total_dates || [];
      const ow = dualSummary.only_working_dates || [];
      // R1: бадж экспериментального режима, если promote_only_working был активен.
      const r1Enabled = !!dualSummary.r1_promote_enabled;
      const r1Promoted = dualSummary.r1_promoted_count || 0;
      const r1Near     = dualSummary.r1_near_confirmed_count || 0;
      const r1Badge = r1Enabled
        ? `<span style="display:inline-block; margin-left:8px; font-size:0.72rem;
                         color:#5b21b6; background:#ede9fe; padding:1px 6px;
                         border:1px solid #c4b5fd; border-radius:3px; font-weight:600;
                         vertical-align:middle;"
                title="Активирован экспериментальный режим: only_working CPs продвинуты до границ сегментов">
              ⚗ R1: promoted=${r1Promoted}, near_confirmed=${r1Near}
           </span>`
        : '';
      html += `<h3 style="margin:14px 0 6px;">⇄ Сравнение Q общий ↔ Q рабочий${r1Badge}</h3>` +
        `<div style="font-size:0.85rem; line-height:1.6; color:#374151;
                     padding:10px 14px; background:#fff; border-radius:4px;
                     border-left:3px solid #1d4ed8;">
          По <b>Q общему</b> найдено переломов: <b>${dualSummary.primary_cp_count || 0}</b>.
          По <b>Q рабочему</b>: <b>${dualSummary.working_cp_count || 0}</b>.
          <b>Подтверждено обеими кривыми:</b> <b>${dualSummary.common_count || 0}</b>${cd.length ? ` (${cd.join(', ')})` : ''}.
          ${ow.length
            ? `<br><b>Только в Q рабочем</b> (фактический сдвиг режима, не отражённый в паспорте): ${ow.join(', ')}.`
            : '<br>Переломов «только в Q рабочем» нет.'}
          ${ot.length
            ? `<br><b>Только в Q общем</b> (вероятный артефакт паспортного Q — событие может быть скрыто): ${ot.join(', ')}.`
            : '<br>Переломов «только в Q общем» нет.'}
        </div>`;
    }

    // ─── 7. Комментарий блока ─────────────────────────────────────
    if (block && block.comment && on('description')) {
      html += `<h3 style="margin:14px 0 6px;">✏️ Комментарий к блоку</h3>
        <div style="padding:10px 12px; background:#f9fafb; border-left:3px solid #6b7280;
                    border-radius:4px; font-size:0.88rem; line-height:1.5;
                    white-space:pre-wrap;">${String(block.comment).replace(/</g,'&lt;')}</div>`;
    }

    // ─── 8. ПАВ-блок — ТОЛЬКО при include_pav_recommendation=true ─
    // PLAN A: по умолчанию для главы заказчика flag=false → блок не рисуется.
    // Появляется если в API передано include_pav: true.
    if (includePav && pav && on('pav_card')) {
      const score = pav.score;
      const scoreColor = score == null ? '#9ca3af'
                       : score >= 65 ? '#dc2626'
                       : score >= 35 ? '#d97706'
                       : '#059669';
      const scoreBg = score == null ? '#f3f4f6'
                    : score >= 65 ? '#fee2e2'
                    : score >= 35 ? '#fef3c7'
                    : '#d1fae5';
      const scoreText = score == null ? '—' : Number(score).toFixed(0);
      html += `<div style="margin-top:14px; padding:8px 12px;
                    background:#f3f4f6; border-radius:4px;
                    font-size:0.76rem; color:#6b7280; font-style:italic;">
          ⓘ ПАВ-балл — optional layer (включён в этом запросе через include_pav=true).
          Не входит в ядро сегментного анализа. По умолчанию для главы
          «Анализ данных заказчика» отключён.
        </div>
        <h3 style="margin:14px 0 6px; color:#374151;">📊 ПАВ-карточка</h3>
        <div style="display:grid; grid-template-columns:200px 1fr; gap:12px;
                    border:2px solid ${scoreColor}; border-radius:8px;
                    background:${scoreBg}; padding:12px;">
          <div style="text-align:center; border-right:1px solid ${scoreColor}66;
                      padding-right:12px;">
            <div style="font-size:0.78rem; color:#4b5563; font-weight:600;">
              ПАВ-балл
            </div>
            <div style="font-size:2.4rem; font-weight:700; color:${scoreColor}; line-height:1;">
              ${scoreText}<span style="font-size:0.9rem; color:#6b7280;"> / 100</span>
            </div>
            <div style="font-size:0.78rem; color:#6b7280; margin-top:4px;">
              Уверенность: <b>${pav.confidence || '—'}</b>
            </div>
          </div>
          <div>
            <div style="font-size:0.95rem; font-weight:700; color:${scoreColor};
                        margin-bottom:4px;">${pav.scenario || '—'}</div>
            <div style="font-size:0.85rem; color:#374151; line-height:1.5;">
              ${pav.recommendation || ''}
            </div>
          </div>
        </div>`;
      if (on('signs_table')) {
        const sPro = pav.signs_pro || [];
        const sCon = pav.signs_con || [];
        const sigItem = (label, strength, color) =>
          `<div style="display:flex; gap:8px; align-items:baseline;
                       padding:4px 8px; background:#fff; border-radius:3px;
                       border-left:3px solid ${color};">
            <span style="font-size:0.75rem; font-weight:700; color:${color}; min-width:38px;">
              ${Math.round((strength || 0) * 100)}%
            </span>
            <span style="font-size:0.84rem; color:#1f2937;">${label}</span>
          </div>`;
        html += `<h4 style="margin:10px 0 6px;">Обоснование ПАВ-балла</h4>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
            <div>
              <div style="font-weight:600; color:#059669; margin-bottom:4px;
                          font-size:0.85rem;">✓ Признаки В ПОЛЬЗУ ПАВ (${sPro.length})</div>
              ${sPro.length
                ? sPro.map(([t, s]) => sigItem(t, s, '#059669')).join('<div style="height:4px;"></div>')
                : '<div style="color:#9ca3af; font-size:0.82rem; padding:4px 8px;">— нет —</div>'}
            </div>
            <div>
              <div style="font-weight:600; color:#dc2626; margin-bottom:4px;
                          font-size:0.85rem;">✗ Контраргументы (${sCon.length})</div>
              ${sCon.length
                ? sCon.map(([t, s]) => sigItem(t, s, '#dc2626')).join('<div style="height:4px;"></div>')
                : '<div style="color:#9ca3af; font-size:0.82rem; padding:4px 8px;">— нет —</div>'}
            </div>
          </div>`;
      }
    }

    return html;
  }

  // ===== _buildSegmentComparisonHtml — рендер блока сравнения сегментов =====
  function _buildSegmentComparisonHtml(snap, block, idPrefix) {
    if (!snap) return '<div style="color:#9ca3af;">Снапшот не загружен.</div>';
    const parts = (block && typeof _getPartsForBlock === 'function')
      ? _getPartsForBlock(block) : {};
    const on = (key) => parts[key] !== false;
    const fmt = (v, d=2) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d);

    const segsCmp = snap.segments_compared || [];
    const diffTable = snap.diff_table || {};
    const interpretation = snap.interpretation || {};
    const chartOverlay = snap.chart_overlay || {};

    let html = '';

    // Header
    html += `<div style="font-size:0.85rem; color:#374151; margin-bottom:8px;">
        <b>Сравнение:</b> ${segsCmp.length} сегментов
      </div>`;

    // Chart placeholder
    if (on('chart') && chartOverlay.series && chartOverlay.series.length) {
      html += `<h3 style="margin:14px 0 6px;">📈 Overlay-график сегментов</h3>
        <div id="${idPrefix}cmpchart" style="height:350px;"></div>`;
    }

    // Compared segments list
    if (segsCmp.length) {
      html += `<h3 style="margin:14px 0 6px;">📋 Сравниваемые сегменты</h3>
        <table class="cd-table"><thead><tr>
          <th>Сегмент</th><th>Период</th><th>Q ср.</th><th>Δ%</th>
        </tr></thead><tbody>`;
      segsCmp.forEach(function(s) {
        var num = s.segment_num || s.num || '?';
        var label = s.label || ('Сегмент ' + num);
        var pFrom = s.period?.from || '—';
        var pTo = s.period?.to || '—';
        var meanVal = s.mean_value || s.mean_q;
        var deltaPct = s.delta_pct;
        html += `<tr>
          <td style="font-weight:500; color:${s.color || '#374151'};">${_escHtml(label)}</td>
          <td style="white-space:nowrap;">${_escHtml(pFrom)} — ${_escHtml(pTo)}</td>
          <td style="text-align:right;">${fmt(meanVal)}</td>
          <td style="text-align:right; color:${deltaPct > 0 ? '#22c55e' : deltaPct < 0 ? '#dc2626' : '#6b7280'};">
            ${deltaPct != null ? (deltaPct > 0 ? '+' : '') + fmt(deltaPct, 1) + '%' : '—'}
          </td>
        </tr>`;
      });
      html += '</tbody></table>';
    }

    // Diff table
    if (on('diff_table') && diffTable.rows && diffTable.rows.length) {
      html += `<h3 style="margin:14px 0 6px;">📊 Таблица различий</h3>
        <table class="cd-table"><thead><tr>
          <th>Метрика</th>`;
      segsCmp.forEach(function(s) {
        html += `<th>${_escHtml(s.label || 'Сег.' + s.segment_num)}</th>`;
      });
      html += '</tr></thead><tbody>';
      diffTable.rows.forEach(function(row) {
        html += '<tr><td>' + _escHtml(row.metric || row.label || '—') + '</td>';
        (row.values || []).forEach(function(v) {
          html += '<td style="text-align:right;">' + fmt(v) + '</td>';
        });
        html += '</tr>';
      });
      html += '</tbody></table>';
    }

    // Interpretation
    if (on('interpretation') && interpretation.summary) {
      html += `<h3 style="margin:14px 0 6px;">📝 Интерпретация</h3>
        <div style="padding:8px 12px; background:#f9fafb; border-radius:4px; font-size:0.85rem;">
          ${_escHtml(interpretation.summary)}
        </div>`;
    }

    return html;
  }

  // ===== _buildObservationBlockHtml — рендер observation блоков (датчики) =====
  function _buildObservationBlockHtml(snap, block, parts) {
    if (!snap) return '<div style="color:#9ca3af;">Снапшот не загружен.</div>';
    const fmt = (v, d=2) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d);
    const on = (key) => parts[key] !== false;
    const bid = block.id || 'x';
    const kind = block.kind || '';

    // Period info
    const period = snap.period || {};
    const dFrom = period.from || snap.date_from || block.params?.date_from || '—';
    const dTo = period.to || snap.date_to || block.params?.date_to || '—';
    const days = period.days || snap.days || 0;

    let html = '';

    // ─── Intro / Period header ───
    if (on('intro')) {
      html += `<div style="margin-bottom:8px; padding:6px 10px; background:#f0f9ff;
                           border-left:3px solid #0284c7; border-radius:4px;
                           font-size:0.85rem; color:#0c4a6e;">
        Период: <b>${_escHtml(dFrom)}</b> → <b>${_escHtml(dTo)}</b> (${days} сут.)
        <br>Источник: данные датчиков давления.
      </div>`;
    }

    // ─── Metrics table ───
    // Поддержка трёх форматов:
    // 1) wz3obs_v1 (плоский): q_total_avg, q_total_median, dp_avg, dp_median, p_wellhead_median, p_flowline_median
    // 2) observation_v1.0 (вложенный): metrics.q.mean, metrics.q.median, ...
    // 3) obs_baseline_v1 (вложенный): metrics.q.mean, metrics.p_tube.median, + metrics.downtime
    if (on('metrics_table')) {
      const m = snap.metrics || {};
      // wz3obs_v1 flat format с fallback на nested
      const q_avg = snap.q_total_avg ?? m.q?.mean;
      const q_med = snap.q_total_median ?? m.q?.median;
      const dp_avg = snap.dp_avg ?? m.dp?.mean;
      const dp_med = snap.dp_median ?? m.dp?.median;
      const p_tube_med = snap.p_wellhead_median ?? m.p_tube?.median;
      const p_line_med = snap.p_flowline_median ?? m.p_line?.median;
      // wz3obs_v1 downtime
      const shutdown_min = snap.shutdown_min_total;
      const working_hours = snap.working_hours;
      // obs_baseline_v1 downtime (nested)
      const downtime = m.downtime || {};
      const downtime_hours = downtime.total_hours;
      const downtime_pct = downtime.downtime_pct_of_period;

      const hasData = q_avg != null || q_med != null || dp_avg != null;
      if (hasData) {
        // Для observation_baseline показываем расширенную таблицу с min/max/direction
        if (kind === 'observation_baseline') {
          const dirLabel = (d) => ({rising:'↑', falling:'↓', stable:'→'})[d] || '';
          html += `<div style="margin-bottom:12px;">
            <table style="width:100%; font-size:0.78rem; border-collapse:collapse;">
              <thead><tr style="background:#f3f4f6;">
                <th style="text-align:left; padding:3px 6px;">Метрика</th>
                <th style="text-align:right; padding:3px 6px;">Среднее</th>
                <th style="text-align:right; padding:3px 6px;">Медиана</th>
                <th style="text-align:right; padding:3px 6px;">Мин</th>
                <th style="text-align:right; padding:3px 6px;">Макс</th>
                <th style="text-align:right; padding:3px 6px;">Тренд</th>
              </tr></thead>
              <tbody>
                <tr>
                  <td style="padding:2px 6px;">Q, тыс.м³/сут</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(m.q?.mean)}</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(m.q?.median)}</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(m.q?.min)}</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(m.q?.max)}</td>
                  <td style="text-align:right; padding:2px 6px;">${dirLabel(m.q?.direction)}</td>
                </tr>
                <tr>
                  <td style="padding:2px 6px;">ΔP, кгс/см²</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(m.dp?.mean)}</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(m.dp?.median)}</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(m.dp?.min)}</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(m.dp?.max)}</td>
                  <td style="text-align:right; padding:2px 6px;">${dirLabel(m.dp?.direction)}</td>
                </tr>
                <tr>
                  <td style="padding:2px 6px;">P уст., кгс/см²</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(m.p_tube?.mean)}</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(m.p_tube?.median)}</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(m.p_tube?.min)}</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(m.p_tube?.max)}</td>
                  <td style="text-align:right; padding:2px 6px;">${dirLabel(m.p_tube?.direction)}</td>
                </tr>
                <tr>
                  <td style="padding:2px 6px;">P шлейф, кгс/см²</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(m.p_line?.mean)}</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(m.p_line?.median)}</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(m.p_line?.min)}</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(m.p_line?.max)}</td>
                  <td style="text-align:right; padding:2px 6px;">${dirLabel(m.p_line?.direction)}</td>
                </tr>
                ${downtime_hours != null ? `<tr>
                  <td style="padding:2px 6px;">Простой</td>
                  <td style="text-align:right; padding:2px 6px;">${fmt(downtime_hours, 1)} ч</td>
                  <td style="text-align:right; padding:2px 6px;" colspan="4">${fmt(downtime_pct, 1)}% периода</td>
                </tr>` : ''}
              </tbody>
            </table>
          </div>`;
        } else {
          // wz3obs_v1 / observation_analysis: компактные плитки метрик
          html += `<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(130px, 1fr));
                              gap:8px; margin-bottom:12px;">
            <div class="obs-metric"><div class="obs-metric-title">Q факт.</div>
              <div class="obs-metric-val">${fmt(q_avg)} <span style="color:#9ca3af;">ср.</span></div>
              <div class="obs-metric-val">${fmt(q_med)} <span style="color:#9ca3af;">мед.</span></div>
              <div class="obs-metric-unit">тыс.м³/сут</div></div>
            <div class="obs-metric"><div class="obs-metric-title">ΔP</div>
              <div class="obs-metric-val">${fmt(dp_avg)} <span style="color:#9ca3af;">ср.</span></div>
              <div class="obs-metric-val">${fmt(dp_med)} <span style="color:#9ca3af;">мед.</span></div>
              <div class="obs-metric-unit">кгс/см²</div></div>
            <div class="obs-metric"><div class="obs-metric-title">P уст. / шлейф</div>
              <div class="obs-metric-val">${fmt(p_tube_med)} <span style="color:#9ca3af;">уст.</span></div>
              <div class="obs-metric-val">${fmt(p_line_med)} <span style="color:#9ca3af;">шл.</span></div>
              <div class="obs-metric-unit">кгс/см²</div></div>
            ${shutdown_min != null ? `<div class="obs-metric"><div class="obs-metric-title">Простой</div>
              <div class="obs-metric-val">${fmt(shutdown_min, 0)} <span style="color:#9ca3af;">мин</span></div>
              <div class="obs-metric-val">${fmt(working_hours, 1)} <span style="color:#9ca3af;">раб.ч</span></div>
              <div class="obs-metric-unit"></div></div>` : ''}
          </div>`;
        }
      }
    }

    // ─── Charts (контейнеры для Plotly) ───
    // observation_analysis: 4 отдельных графика из wz3RenderCharts
    if (kind === 'observation_analysis') {
      if (on('chart_pressures')) {
        html += `<div id="prev-${bid}-obs-pres" style="height:240px; margin:8px 0;
                      background:#fafafa; border-radius:4px;"></div>`;
      }
      if (on('chart_dp')) {
        html += `<div id="prev-${bid}-obs-dp" style="height:240px; margin:8px 0;
                      background:#fafafa; border-radius:4px;"></div>`;
      }
      if (on('chart_q')) {
        html += `<div id="prev-${bid}-obs-q" style="height:240px; margin:8px 0;
                      background:#fafafa; border-radius:4px;"></div>`;
      }
      if (on('chart_utilization')) {
        html += `<div id="prev-${bid}-obs-util" style="height:200px; margin:8px 0;
                      background:#fafafa; border-radius:4px;"></div>`;
      }
    }
    // baseline: chart_metrics
    if (kind === 'observation_baseline' && on('chart_metrics')) {
      html += `<div id="prev-${bid}-obs-metrics" style="height:260px; margin:8px 0;
                    background:#fafafa; border-radius:4px;"></div>`;
    }
    // period: chart_timeseries + chart_compare_b1
    if (kind === 'observation_period') {
      if (on('chart_timeseries')) {
        html += `<div id="prev-${bid}-obs-ts" style="height:260px; margin:8px 0;
                      background:#fafafa; border-radius:4px;"></div>`;
      }
      if (on('chart_compare_b1')) {
        html += `<div id="prev-${bid}-obs-cmp" style="height:200px; margin:8px 0;
                      background:#fafafa; border-radius:4px;"></div>`;
      }
    }
    // segment: chart_segments
    if (kind === 'observation_segment' && on('chart_segments')) {
      html += `<div id="prev-${bid}-obs-seg" style="height:280px; margin:8px 0;
                    background:#fafafa; border-radius:4px;"></div>`;
    }

    // ─── Quality ───
    const quality = snap.quality || {};
    if (on('quality') && Object.keys(quality).length) {
      const cov = quality.coverage_pct;
      const gaps = quality.gaps_count || 0;
      const covColor = cov >= 95 ? '#16a34a' : cov >= 80 ? '#d97706' : '#dc2626';
      html += `<div style="margin:8px 0; padding:6px 10px; background:#f9fafb;
                           border-radius:4px; font-size:0.82rem;">
        <b>Качество:</b> покрытие <span style="color:${covColor}; font-weight:600;">${fmt(cov, 1)}%</span>,
        пропусков: ${gaps}, дней: ${quality.days_count || days}
      </div>`;
    }

    // ─── Comparisons (for observation_period) ───
    const comparisons = snap.comparisons || {};
    // with_b1
    if (on('comparison_with_b1') && comparisons.with_b1) {
      const cmpB1 = comparisons.with_b1;
      if (cmpB1.status === 'ok') {
        const d = cmpB1.deltas || {};
        let rows = '';
        ['p_tube', 'p_line', 'dp', 'q'].forEach(k => {
          const dv = d[k];
          if (!dv) return;
          const arrow = dv.pct > 0 ? '↑' : dv.pct < 0 ? '↓' : '→';
          const color = Math.abs(dv.pct) > 10 ? '#dc2626' : '#374151';
          rows += `<tr>
            <td style="padding:2px 6px;">${_escHtml(k)}</td>
            <td style="text-align:right; padding:2px 6px;">${fmt(dv.baseline_value)}</td>
            <td style="text-align:right; padding:2px 6px;">${fmt(dv.current_value)}</td>
            <td style="text-align:right; padding:2px 6px; color:${color};">${arrow} ${fmt(dv.pct, 1)}%</td>
          </tr>`;
        });
        html += `<div style="margin:8px 0;">
          <div style="font-weight:600; font-size:0.85rem; margin-bottom:4px;">⚖️ Сравнение с baseline</div>
          <table style="width:100%; font-size:0.78rem; border-collapse:collapse;">
            <tr style="background:#f3f4f6;">
              <th style="text-align:left; padding:3px 6px;">Метрика</th>
              <th style="text-align:right; padding:3px 6px;">B1</th>
              <th style="text-align:right; padding:3px 6px;">Текущий</th>
              <th style="text-align:right; padding:3px 6px;">Δ</th>
            </tr>
            ${rows}
          </table>
        </div>`;
      }
    }
    // with_customer
    if (on('comparison_with_customer') && comparisons.with_customer) {
      const cmpCust = comparisons.with_customer;
      if (cmpCust.status === 'ok') {
        html += `<div style="margin:8px 0; padding:6px 10px; background:#f9fafb;
                             border-radius:4px; font-size:0.82rem;">
          <b>📊 Сравнение с заказчиком:</b>
          Q: ${fmt(cmpCust.q_delta_pct, 1)}%, ΔP: ${fmt(cmpCust.dp_delta_pct, 1)}%
        </div>`;
      }
    }

    // ─── Diagnostics ───
    const diag = snap.diagnostics || [];
    if (on('diagnostics') && diag.length) {
      html += `<div style="margin:8px 0;">
        <div style="font-weight:600; font-size:0.85rem; margin-bottom:4px;">🔍 Диагностика</div>
        ${diag.map(d => `<div style="padding:3px 8px; margin:2px 0;
                                      background:${d.severity === 'warning' ? '#fef3c7' : '#fee2e2'};
                                      border-left:3px solid ${d.severity === 'warning' ? '#f59e0b' : '#dc2626'};
                                      font-size:0.78rem; border-radius:2px;">
          ${_escHtml(d.message || d)}
        </div>`).join('')}
      </div>`;
    }

    // ─── Segments table (for observation_segment) ───
    const segments = snap.segments || [];
    if (on('segments_table') && segments.length) {
      let rows = '';
      segments.forEach((s, i) => {
        const trend = s.trend || s.regime || '—';
        const trendColor = trend === 'rise' ? '#16a34a' : trend === 'decay' ? '#dc2626' : '#374151';
        rows += `<tr>
          <td style="padding:2px 6px;">${i + 1}</td>
          <td style="padding:2px 6px;">${_escHtml(s.from || '—')}</td>
          <td style="padding:2px 6px;">${_escHtml(s.to || '—')}</td>
          <td style="text-align:right; padding:2px 6px;">${fmt(s.q_mean)}</td>
          <td style="text-align:right; padding:2px 6px;">${fmt(s.dp_mean)}</td>
          <td style="padding:2px 6px; color:${trendColor};">${_escHtml(trend)}</td>
        </tr>`;
      });
      html += `<div style="margin:8px 0;">
        <div style="font-weight:600; font-size:0.85rem; margin-bottom:4px;">📋 Сегменты</div>
        <table style="width:100%; font-size:0.78rem; border-collapse:collapse;">
          <tr style="background:#f3f4f6;">
            <th style="text-align:left; padding:3px 6px;">#</th>
            <th style="text-align:left; padding:3px 6px;">От</th>
            <th style="text-align:left; padding:3px 6px;">До</th>
            <th style="text-align:right; padding:3px 6px;">Q ср.</th>
            <th style="text-align:right; padding:3px 6px;">ΔP ср.</th>
            <th style="text-align:left; padding:3px 6px;">Тренд</th>
          </tr>
          ${rows}
        </table>
      </div>`;
    }

    // ─── Changepoints (for observation_segment) ───
    const changepoints = snap.changepoints || [];
    if (on('changepoints') && changepoints.length) {
      html += `<div style="margin:8px 0;">
        <div style="font-weight:600; font-size:0.85rem; margin-bottom:4px;">🔻 Точки перелома (${changepoints.length})</div>
        ${changepoints.map(cp => `<div style="padding:3px 8px; margin:2px 0;
                                               background:#f3e8ff; border-left:3px solid #7c3aed;
                                               font-size:0.78rem; border-radius:2px;">
          ${_escHtml(cp.datetime || cp.date || '—')}: ${_escHtml(cp.description || cp.type || '—')}
        </div>`).join('')}
      </div>`;
    }

    // ─── Flags ───
    const flags = snap.flags || {};
    if (on('flags') && Object.keys(flags).length) {
      const flagItems = Object.entries(flags).filter(([, v]) => v).map(([k]) => k);
      if (flagItems.length) {
        html += `<div style="margin:8px 0; padding:6px 10px; background:#fef3c7;
                             border-left:3px solid #f59e0b; border-radius:4px; font-size:0.82rem;">
          <b>⚠️ Флаги:</b> ${flagItems.map(f => _escHtml(f)).join(', ')}
        </div>`;
      }
    }

    // ─── Description (текст от пользователя) ───
    if (on('description') && snap.description) {
      html += `<div style="margin:8px 0; padding:6px 10px; background:#f1f5f9;
                           border-left:3px solid #94a3b8; border-radius:4px;
                           font-size:0.85rem; color:#475569; white-space:pre-wrap;">
        ${_escHtml(snap.description)}
      </div>`;
    }

    return html || '<div style="color:#9ca3af; font-size:0.82rem;">Данные блока пусты.</div>';
  }

  // ===== _renderObservationBlockCharts — Plotly графики для observation блоков =====
  // Рендерит 4 графика для observation_analysis в контейнеры prev-{id}-obs-*
  function _renderObservationBlockCharts(b) {
    if (!window.Plotly) return;
    const snap = b.data_snapshot || {};
    const bid = b.id || 'x';
    const kind = b.kind || '';
    const parts = _getPartsForBlock(b);
    const on = (key) => parts[key] !== false;

    // Данные графиков:
    // - observation_analysis: snap.chart { dates, p_wellhead, p_flowline, ... }
    // - observation_baseline/period/segment: snap.raw.chart_payload { dates, q, dp, ... }
    const c = snap.chart;
    const rawChart = snap.raw?.chart_payload || {};

    // Ранний выход только если нет данных ни в одном источнике
    const hasChartData = c && c.dates && c.dates.length;
    const hasRawData = rawChart.dates && rawChart.dates.length;
    if (!hasChartData && !hasRawData) return;

    const layout = { margin:{l:45,r:20,t:30,b:40}, hovermode:'x unified',
                     font:{size:10}, legend:{orientation:'h', y:-0.18} };
    const cfg = { displaylogo:false, responsive:true };

    // ─── observation_analysis: 4 отдельных графика ───
    // Требуется snap.chart с данными
    if (kind === 'observation_analysis' && c && c.dates && c.dates.length) {
      // 1) Давления (chart_pressures)
      if (on('chart_pressures')) {
        const elPres = document.getElementById(`prev-${bid}-obs-pres`);
        if (elPres) {
          const traces = [];
          if (c.p_wellhead && c.p_wellhead.some(v => v != null)) {
            traces.push({ x:c.dates, y:c.p_wellhead, name:'P трубное', mode:'lines',
                          line:{color:'#1f77b4', width:1.4}, connectgaps:false });
          }
          if (c.p_flowline && c.p_flowline.some(v => v != null)) {
            traces.push({ x:c.dates, y:c.p_flowline, name:'P линейное', mode:'lines',
                          line:{color:'#d62728', width:1.4}, connectgaps:false });
          }
          if (traces.length) {
            try { Plotly.newPlot(elPres, traces, {...layout, title:'Давления, кгс/см²', yaxis:{title:'кгс/см²'}}, cfg); }
            catch(e) { console.warn('obs-pres', e); }
          }
        }
      }

      // 2) ΔP (chart_dp)
      if (on('chart_dp')) {
        const elDp = document.getElementById(`prev-${bid}-obs-dp`);
        if (elDp && c.dp && c.dp.some(v => v != null)) {
          const traces = [{
            x:c.dates, y:c.dp, name:'ΔP', mode:'lines',
            line:{color:'#ef6c00', width:1.6}, fill:'tozeroy',
            fillcolor:'rgba(239,108,0,0.1)', connectgaps:false
          }];
          try { Plotly.newPlot(elDp, traces, {...layout, title:'Перепад ΔP, кгс/см²', yaxis:{title:'кгс/см²'}}, cfg); }
          catch(e) { console.warn('obs-dp', e); }
        }
      }

      // 3) Q(t) (chart_q)
      if (on('chart_q')) {
        const elQ = document.getElementById(`prev-${bid}-obs-q`);
        if (elQ) {
          const qData = c.q_gas_total || c.q_gas_working || [];
          if (qData.some(v => v != null)) {
            const traces = [{
              x:c.dates, y:qData, name:'Q факт.', mode:'lines',
              line:{color:'#2e7d32', width:1.6}, connectgaps:false
            }];
            try { Plotly.newPlot(elQ, traces, {...layout, title:'Дебит Q(t), тыс.м³/сут', yaxis:{title:'тыс.м³/сут'}}, cfg); }
            catch(e) { console.warn('obs-q', e); }
          }
        }
      }

      // 4) Рабочее время по дням (chart_utilization)
      if (on('chart_utilization')) {
        const elUtil = document.getElementById(`prev-${bid}-obs-util`);
        if (elUtil && c.dates && c.dates.length) {
          // Группируем по дням: подсчёт рабочих/простойных часов
          const byDay = {};
          for (let i = 0; i < c.dates.length; i++) {
            const day = String(c.dates[i]).slice(0, 10);
            if (!byDay[day]) byDay[day] = { work: 0, down: 0 };
            const dpVal = c.dp ? c.dp[i] : null;
            const pVal = c.p_wellhead ? c.p_wellhead[i] : null;
            // Простой: dp <= 0.1 или нет данных
            if (dpVal == null || dpVal <= 0.1 || pVal == null) {
              byDay[day].down += 1;
            } else {
              byDay[day].work += 1;
            }
          }
          const dayLabels = Object.keys(byDay).sort();
          const workHours = dayLabels.map(d => byDay[d].work);
          const downHours = dayLabels.map(d => byDay[d].down);
          if (dayLabels.length) {
            try {
              Plotly.newPlot(elUtil, [
                {x:dayLabels, y:workHours, name:'Рабочих ч', type:'bar', marker:{color:'#16a34a'}},
                {x:dayLabels, y:downHours, name:'Простой ч', type:'bar', marker:{color:'#dc2626'}},
              ], {...layout, title:'Учёт рабочего времени по дням', yaxis:{title:'часов'}, barmode:'stack'}, cfg);
            } catch(e) { console.warn('obs-util', e); }
          }
        }
      }
    }

    // ─── observation_baseline: chart_metrics ───
    // BAR CHART: 4 метрики (P_tube, P_line, ΔP, Q) с mean ± std
    // Соответствует PDF: observation_chart_renderer.render_baseline_chart
    if (kind === 'observation_baseline' && on('chart_metrics')) {
      const elMetrics = document.getElementById(`prev-${bid}-obs-metrics`);
      if (elMetrics) {
        const m = snap.metrics || {};
        // Собираем данные для bar chart
        const metricDefs = [
          ['p_tube', 'P уст.', '#1d4ed8'],
          ['p_line', 'P шлейф', '#16a34a'],
          ['dp',     'ΔP',      '#dc2626'],
          ['q',      'Q',       '#f59e0b'],
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
        if (labels.length) {
          const traces = [{
            x: labels,
            y: means,
            type: 'bar',
            marker: {color: colors},
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
          const plotLayout = {
            ...layout,
            title: 'Метрики baseline (среднее ± std)',
            yaxis: {title: 'Значение'},
            showlegend: false,
          };
          try { Plotly.newPlot(elMetrics, traces, plotLayout, cfg); }
          catch(e) { console.warn('obs-baseline-metrics', e); }
        }
      }
    }

    // ─── observation_period: chart_timeseries + chart_compare_b1 ───
    // Данные из snap.raw.chart_payload: {dates, p_tube, p_line, dp, q, shutdown_hours}
    // Соответствует PDF: observation_chart_renderer.render_period_timeseries_chart
    if (kind === 'observation_period') {
      const rawChart = snap.raw?.chart_payload || {};
      const dates = rawChart.dates || [];

      // chart_timeseries: Q (верхний subplot) + ΔP (нижний subplot)
      if (on('chart_timeseries')) {
        const elTs = document.getElementById(`prev-${bid}-obs-ts`);
        if (elTs && dates.length) {
          const traces = [];
          const hasQ = rawChart.q && rawChart.q.some(v => v != null);
          const hasDp = rawChart.dp && rawChart.dp.some(v => v != null);

          if (hasQ) {
            traces.push({
              x: dates, y: rawChart.q, name: 'Q (тыс.м³/сут)', mode: 'lines+markers',
              line: {color: '#1d4ed8', width: 1.3},
              marker: {size: 3},
              xaxis: 'x', yaxis: 'y', connectgaps: false
            });
          }
          if (hasDp) {
            traces.push({
              x: dates, y: rawChart.dp, name: 'ΔP (атм)', mode: 'lines+markers',
              line: {color: '#dc2626', width: 1.3},
              marker: {size: 3},
              xaxis: 'x2', yaxis: 'y2', connectgaps: false
            });
          }
          if (traces.length) {
            // Layout с двумя вертикально расположенными subplots
            const plotLayout = {
              ...layout,
              title: 'Дебит Q и перепад давления ΔP',
              grid: {rows: 2, columns: 1, pattern: 'independent'},
              xaxis: {domain: [0, 1], anchor: 'y'},
              yaxis: {domain: [0.55, 1], title: 'Q, тыс.м³/сут', anchor: 'x'},
              xaxis2: {domain: [0, 1], anchor: 'y2'},
              yaxis2: {domain: [0, 0.45], title: 'ΔP, атм', anchor: 'x2'},
              showlegend: false,
            };
            try { Plotly.newPlot(elTs, traces, plotLayout, cfg); }
            catch(e) { console.warn('obs-period-ts', e); }
          }
        }
      }

      // chart_compare_b1: grouped bar chart — baseline vs current
      // Соответствует PDF: observation_chart_renderer.render_period_compare_b1_chart
      if (on('chart_compare_b1')) {
        const elCmp = document.getElementById(`prev-${bid}-obs-cmp`);
        if (elCmp) {
          const comparisons = snap.comparisons || {};
          const withB1 = comparisons.with_b1 || {};
          if (withB1.status === 'ok' && withB1.deltas) {
            const deltas = withB1.deltas;
            // Собираем данные для grouped bar chart
            const labels = [];
            const baselineVals = [];
            const currentVals = [];
            const metricDefs = [
              ['p_tube', 'P уст.'],
              ['p_line', 'P шлейф'],
              ['dp',     'ΔP'],
              ['q',      'Q'],
            ];
            for (const [key, label] of metricDefs) {
              const d = deltas[key];
              if (!d || d.baseline_value == null || d.current_value == null) continue;
              labels.push(label);
              baselineVals.push(d.baseline_value);
              currentVals.push(d.current_value);
            }
            if (labels.length) {
              const traces = [
                {
                  x: labels, y: baselineVals, type: 'bar', name: 'Baseline',
                  marker: {color: '#60a5fa'}
                },
                {
                  x: labels, y: currentVals, type: 'bar', name: 'Текущий период',
                  marker: {color: '#34d399'}
                },
              ];
              const plotLayout = {
                ...layout,
                title: 'Сравнение с baseline (B1)',
                yaxis: {title: 'Значение'},
                barmode: 'group',
                legend: {orientation: 'h', y: -0.25}
              };
              try { Plotly.newPlot(elCmp, traces, plotLayout, cfg); }
              catch(e) { console.warn('obs-period-cmp', e); }
            }
          }
        }
      }
    }

    // ─── observation_segment: chart_segments ───
    // Q time series с colored bands per segment + vertical lines на changepoints
    // Соответствует PDF: observation_chart_renderer.render_segment_chart
    if (kind === 'observation_segment' && on('chart_segments')) {
      const elSeg = document.getElementById(`prev-${bid}-obs-seg`);
      if (elSeg) {
        const rawChart = snap.raw?.chart_payload || {};
        const dates = rawChart.dates || [];
        const segments = snap.segments || [];
        const changepoints = snap.changepoints || [];
        if (dates.length && rawChart.q) {
          // Основной Q trace
          const traces = [{
            x: dates, y: rawChart.q, name: 'Q (тыс.м³/сут)', mode: 'lines',
            line: {color: '#1d4ed8', width: 1.3}, connectgaps: false
          }];

          // Colored bands per segment (shapes)
          const segmentColors = ['#dbeafe', '#fef3c7', '#d1fae5', '#fce7f3', '#ede9fe', '#ffedd5'];
          const shapes = [];
          for (let i = 0; i < segments.length; i++) {
            const seg = segments[i];
            if (!seg || !seg.start_date || !seg.end_date) continue;
            shapes.push({
              type: 'rect',
              xref: 'x', yref: 'paper',
              x0: seg.start_date, x1: seg.end_date,
              y0: 0, y1: 1,
              fillcolor: segmentColors[i % segmentColors.length],
              opacity: 0.25,
              line: {width: 0},
              layer: 'below',
            });
          }

          // Vertical lines на changepoints
          for (const cp of changepoints) {
            if (!cp) continue;
            const cpDate = cp.date || cp.changepoint_date;
            if (!cpDate) continue;
            shapes.push({
              type: 'line',
              xref: 'x', yref: 'paper',
              x0: cpDate, x1: cpDate,
              y0: 0, y1: 1,
              line: {color: '#dc2626', width: 1.5, dash: 'dash'},
            });
            // Annotation с magnitude
            const mag = cp.magnitude_pct;
            if (mag != null) {
              traces.push({
                x: [cpDate], y: [null], mode: 'markers',
                marker: {size: 0}, showlegend: false,
                hoverinfo: 'text',
                hovertext: `Changepoint: ${mag >= 0 ? '+' : ''}${mag.toFixed(1)}%`
              });
            }
          }

          const plotLayout = {
            ...layout,
            title: 'Сегментный анализ Q',
            yaxis: {title: 'тыс.м³/сут'},
            shapes: shapes,
            showlegend: segments.length <= 5,
            legend: {orientation: 'h', y: -0.25}
          };
          try { Plotly.newPlot(elSeg, traces, plotLayout, cfg); }
          catch(e) { console.warn('obs-segment-chart', e); }
        }
      }
    }
  }

  // ═════════════════════════════════════════════════════════════════════════
  //   ADAPTATION BLOCKS RENDERERS (Step 5 wizard)
  // ═════════════════════════════════════════════════════════════════════════

  /**
   * _buildAdaptationPeriodAnalysisHtml — рендер блока adaptation_period_analysis
   *
   * Снапшот: { adaptation, observation, b1, comparison, optimal }
   * Рендерит: 4 карточки (R, R★, B2, B1), таблицу сравнения, графики, события
   */
  function _buildAdaptationPeriodAnalysisHtml(snap, block, parts) {
    if (!snap || (!snap.adaptation && !snap.observation)) {
      return `<div style="color:#9ca3af; padding:12px;">Снимок данных пуст.</div>`;
    }

    const adapt = snap.adaptation || {};
    const obs = snap.observation || {};
    const b1 = snap.b1 || {};
    const cmp = snap.comparison || {};
    const opt = snap.optimal || {};
    const p = block.params || {};

    const fmt = (v, d = 2) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d);
    const fmtSgn = (v, d = 2) => {
      if (v == null || isNaN(v)) return '—';
      const n = Number(v);
      return (n > 0 ? '+' : '') + n.toFixed(d);
    };
    const on = (key) => parts[key] !== false;

    let html = '';

    // ─── Заголовок периода ───
    const dFrom = p.date_from || adapt.date_from || '—';
    const dTo = p.date_to || adapt.date_to || '—';
    html += `<div style="font-size:0.85rem; color:#374151; margin-bottom:8px;">
      <b>Период адаптации:</b> ${_escHtml(dFrom)} — ${_escHtml(dTo)}
      ${adapt.duration_days ? `(${adapt.duration_days} сут.)` : ''}
    </div>`;

    // ─── Карточки метрик (R, R★, B2, B1) ───
    if (on('metrics_cards')) {
      const mkCard = (label, color, data) => {
        if (!data) return '';
        return `<div style="flex:1; min-width:140px; padding:8px 10px; background:#fff;
                           border:1px solid ${color}; border-radius:6px; text-align:center;">
          <div style="font-weight:600; color:${color}; font-size:0.9rem;">${_escHtml(label)}</div>
          <div style="margin-top:4px;">
            <div style="font-size:0.78rem; color:#6b7280;">Q медиана</div>
            <div style="font-size:1.1rem; font-weight:600;">${fmt(data.flow_median)}</div>
          </div>
          <div style="margin-top:4px;">
            <div style="font-size:0.78rem; color:#6b7280;">ΔP медиана</div>
            <div style="font-size:1.1rem; font-weight:600;">${fmt(data.dp_median)}</div>
          </div>
          ${data.utilization_pct != null ? `
          <div style="margin-top:4px;">
            <div style="font-size:0.78rem; color:#6b7280;">КИВ</div>
            <div style="font-size:1rem;">${fmt(data.utilization_pct, 1)}%</div>
          </div>` : ''}
        </div>`;
      };
      html += `<div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px;">
        ${mkCard('R (Адаптация)', '#f59e0b', adapt)}
        ${opt.regime ? mkCard('R★ (Оптимум)', '#22c55e', opt.regime) : ''}
        ${obs ? mkCard('B2 (Наблюд.)', '#6366f1', obs) : ''}
        ${b1 ? mkCard('B1 (Базовый)', '#64748b', b1) : ''}
      </div>`;
    }

    // ─── Таблица сравнения Наблюдение → Адаптация ───
    if (on('comparison_table') && (adapt || obs)) {
      html += `<h4 style="margin:10px 0 6px; font-size:0.9rem; color:#374151;">
        Сравнение: Наблюдение → Адаптация
      </h4>
      <table style="width:100%; font-size:0.8rem; border-collapse:collapse;">
        <tr style="background:#f3f4f6;">
          <th style="text-align:left; padding:4px 8px;">Параметр</th>
          <th style="text-align:right; padding:4px 8px;">Наблюдение</th>
          <th style="text-align:right; padding:4px 8px;">Адаптация</th>
          <th style="text-align:right; padding:4px 8px;">Δ</th>
        </tr>
        <tr>
          <td style="padding:4px 8px;">Q медианный, тыс.м³/сут</td>
          <td style="text-align:right; padding:4px 8px;">${fmt(obs.flow_median)}</td>
          <td style="text-align:right; padding:4px 8px;">${fmt(adapt.flow_median)}</td>
          <td style="text-align:right; padding:4px 8px;">${fmtSgn(cmp.delta_flow_median)}</td>
        </tr>
        <tr style="background:#fafafa;">
          <td style="padding:4px 8px;">ΔP, кгс/см²</td>
          <td style="text-align:right; padding:4px 8px;">${fmt(obs.dp_median)}</td>
          <td style="text-align:right; padding:4px 8px;">${fmt(adapt.dp_median)}</td>
          <td style="text-align:right; padding:4px 8px;">${fmtSgn(cmp.delta_dp)}</td>
        </tr>
        <tr>
          <td style="padding:4px 8px;">КИВ, %</td>
          <td style="text-align:right; padding:4px 8px;">${fmt(obs.utilization_pct, 1)}</td>
          <td style="text-align:right; padding:4px 8px;">${fmt(adapt.utilization_pct, 1)}</td>
          <td style="text-align:right; padding:4px 8px;">—</td>
        </tr>
      </table>`;
    }

    // ─── Раздел R★ (если есть) ───
    if (opt.regime) {
      const r = opt.regime;
      html += `<div style="margin-top:12px; padding:8px 10px; background:#ecfdf5;
                          border-left:3px solid #22c55e; border-radius:4px;">
        <div style="font-weight:600; color:#166534;">R★ Оптимальное окно</div>
        <div style="font-size:0.85rem; margin-top:4px;">
          ${_escHtml(r.start || '—')} — ${_escHtml(r.end || '—')}
          ${r.duration_label ? `(${_escHtml(r.duration_label)})` : ''}
        </div>
        ${r.score != null ? `<div style="font-size:0.85rem;">Score: <b>${fmt(r.score, 1)}</b></div>` : ''}
      </div>`;
    }

    return html;
  }

  /**
   * _buildOptimalWindowHtml — рендер блока optimal_window (R★)
   *
   * Снапшот: { regime, observation, b1 }
   */
  function _buildOptimalWindowHtml(snap, block, parts) {
    if (!snap || !snap.regime) {
      return `<div style="color:#9ca3af; padding:12px;">Окно R★ не выбрано.</div>`;
    }

    const r = snap.regime;
    const obs = snap.observation || {};
    const b1 = snap.b1 || {};
    const p = block.params || {};
    const fmt = (v, d = 2) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d);
    const on = (key) => parts[key] !== false;

    let html = '';

    // ─── Сводка окна ───
    if (on('regime_summary')) {
      html += `<div style="padding:10px 12px; background:#ecfdf5;
                          border-left:3px solid #22c55e; border-radius:4px; margin-bottom:10px;">
        <div style="font-weight:600; color:#166534; font-size:1rem;">R★ Оптимальное окно</div>
        <div style="margin-top:4px; font-size:0.9rem;">
          <b>${_escHtml(r.start || p.optimal_from || '—')}</b> —
          <b>${_escHtml(r.end || p.optimal_to || '—')}</b>
          ${r.duration_label ? `<span style="color:#6b7280;"> (${_escHtml(r.duration_label)})</span>` : ''}
        </div>
        ${r.score != null ? `
        <div style="margin-top:6px; font-size:1.3rem; font-weight:700; color:#15803d;">
          Score: ${fmt(r.score, 1)}
        </div>` : ''}
      </div>`;
    }

    // ─── Таблица метрик окна ───
    if (on('metrics_table')) {
      html += `<table style="width:100%; font-size:0.82rem; border-collapse:collapse;">
        <tr style="background:#f3f4f6;">
          <th style="text-align:left; padding:4px 8px;">Параметр</th>
          <th style="text-align:right; padding:4px 8px;">Значение</th>
        </tr>
        <tr>
          <td style="padding:4px 8px;">P труб. (медиана), кгс/см²</td>
          <td style="text-align:right; padding:4px 8px;">${fmt(r.p_tube_median)}</td>
        </tr>
        <tr style="background:#fafafa;">
          <td style="padding:4px 8px;">P лин. (медиана), кгс/см²</td>
          <td style="text-align:right; padding:4px 8px;">${fmt(r.p_line_median)}</td>
        </tr>
        <tr>
          <td style="padding:4px 8px;">ΔP (медиана), кгс/см²</td>
          <td style="text-align:right; padding:4px 8px;">${fmt(r.dp_median)}</td>
        </tr>
        <tr style="background:#fafafa;">
          <td style="padding:4px 8px;">Q медианный, тыс.м³/сут</td>
          <td style="text-align:right; padding:4px 8px;">${fmt(r.flow_median)}</td>
        </tr>
        <tr>
          <td style="padding:4px 8px;">Q средний, тыс.м³/сут</td>
          <td style="text-align:right; padding:4px 8px;">${fmt(r.flow_avg)}</td>
        </tr>
        <tr style="background:#fafafa;">
          <td style="padding:4px 8px;">КИВ, %</td>
          <td style="text-align:right; padding:4px 8px;">${fmt(r.utilization_pct, 1)}</td>
        </tr>
      </table>`;
    }

    return html;
  }

  /**
   * _buildReagentIrvSummaryHtml — рендер блока reagent_irv_summary
   *
   * Снапшот: { injections_total, irv_count, scores, best_reagent }
   */
  function _buildReagentIrvSummaryHtml(snap, block, parts) {
    if (!snap || (!snap.injections_total && !snap.irv_count && !snap.scores)) {
      return `<div style="color:#9ca3af; padding:12px;">Данные по реагентам отсутствуют.</div>`;
    }

    const fmt = (v, d = 1) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d);
    const on = (key) => parts[key] !== false;

    let html = '';

    // ─── Сводка ───
    if (on('summary')) {
      html += `<div style="padding:10px 12px; background:#fef3c7;
                          border-left:3px solid #f59e0b; border-radius:4px; margin-bottom:10px;">
        <div style="font-weight:600; color:#92400e; font-size:0.95rem;">Эффективность реагентов (ИРВ)</div>
        <div style="margin-top:6px; font-size:0.88rem; color:#78350f;">
          <b>Всего вбросов:</b> ${snap.injections_total || 0} &nbsp;|&nbsp;
          <b>ИРВ:</b> ${snap.irv_count || 0}
        </div>
        ${snap.best_reagent ? `
        <div style="margin-top:6px; font-size:0.9rem;">
          <b>Лучший реагент:</b>
          <span style="color:#15803d; font-weight:600;">${_escHtml(snap.best_reagent)}</span>
        </div>` : ''}
      </div>`;
    }

    // ─── Таблица Score по реагентам ───
    if (on('scores_table') && snap.scores) {
      const scores = snap.scores;
      // scores может быть объектом {reagent: score} или массивом [{reagent, score}]
      const rows = Array.isArray(scores)
        ? scores
        : Object.entries(scores).map(([name, score]) => ({ reagent: name, score }));

      if (rows.length > 0) {
        let tableRows = '';
        rows.forEach((r, i) => {
          const name = r.reagent || r.name || '—';
          const sc = r.score != null ? r.score : r;
          const bg = i % 2 === 0 ? '#fff' : '#fafafa';
          const scoreColor = sc >= 70 ? '#15803d' : sc >= 40 ? '#d97706' : '#dc2626';
          tableRows += `<tr style="background:${bg};">
            <td style="padding:4px 8px;">${_escHtml(name)}</td>
            <td style="text-align:right; padding:4px 8px; font-weight:600; color:${scoreColor};">
              ${fmt(sc)}
            </td>
          </tr>`;
        });

        html += `<table style="width:100%; font-size:0.82rem; border-collapse:collapse;">
          <tr style="background:#f3f4f6;">
            <th style="text-align:left; padding:4px 8px;">Реагент</th>
            <th style="text-align:right; padding:4px 8px;">Score</th>
          </tr>
          ${tableRows}
        </table>`;
      }
    }

    return html;
  }

  // ===== _renderBlockSummaryHtml (извлечено 1-в-1) =====
  function _renderBlockSummaryHtml(b) {
    const snap = b.data_snapshot || {};
    const p = b.params || {};
    const parts = _getPartsForBlock(b);
    const showComment = parts.description !== false;
    const comment = (b.comment && showComment) ? `<div style="font-style:italic; color:#6b7280; margin-top:4px;
                                       padding:4px 8px; background:#f9fafb; border-radius:3px;">
        «${_escHtml(b.comment)}»</div>` : '';

    // ─── chapter_intro: просто пользовательский текст вступления главы ───
    if (b.kind === 'chapter_intro') {
      const text = (p.text || '').trim();
      if (!text) {
        return `<div style="font-style:italic; color:#9ca3af; font-size:0.85rem;">
                  (текст не задан)
                </div>`;
      }
      return `<div style="font-size:0.95rem; line-height:1.5; color:#1f2937;
                          white-space:pre-wrap;">${_escHtml(text)}</div>`;
    }

    // ─── segment_analysis: тот же shared-рендер что snapshot-preview ───
    if (b.kind === 'segment_analysis') {
      const bid = b.id || 'x';
      return _buildSegmentBlockHtml(snap, b, `segprev-${bid}-`);
    }

    // ─── segment_comparison: рендер сравнения сегментов ───
    if (b.kind === 'segment_comparison') {
      const bid = b.id || 'x';
      return _buildSegmentComparisonHtml(snap, b, `segcmp-${bid}-`);
    }

    // baseline/period_analysis — данные заказчика (UzKorGaz)
    // observation_analysis обрабатывается ниже через observation_* блок
    if (b.kind === 'baseline' || b.kind === 'period_analysis') {
      // WYSIWYG: используем ту же shared-функцию, что и snapshot-preview
      // модалка (_showBlockPreview) и (через PNG) PDF. Гарантирует, что
      // три представления одного блока РАСХОДИТЬСЯ НЕ МОГУТ.
      // Контейнеры графиков получают уникальный prefix prev-{block.id}-
      // → чтобы несколько блоков на странице не конфликтовали.
      const bid = b.id || 'x';
      const dFrom = snap.date_from || p.date_from || '—';
      const dTo   = snap.date_to   || p.date_to   || '—';
      const days  = snap.days || 0;

      // Описательный текст обоснования B1 (специфика baseline, раздел 4 ТЗ)
      let baselineDescHtml = '';
      if (b.kind === 'baseline') {
        const just = (p.justification || '').trim();
        const showJust = (parts.justification !== false) && just;
        baselineDescHtml = `
          <div style="font-size:0.9rem; color:#1f2937; margin-bottom:8px;
                      padding:8px 10px; background:#fffbeb;
                      border-left:3px solid #d97706; border-radius:3px;">
            В качестве периода для сравнения и оценки эффективности работ
            выбран период <b>${_escHtml(dFrom)} — ${_escHtml(dTo)}</b>
            (${days} сут.).
            ${showJust ? `<br><b>Обоснование выбора:</b> ${_escHtml(just)}` : ''}
          </div>`;
      }

      // Shared WYSIWYG-рендер (плитки + 2×2 графики + таблицы + UzKor/UniTool блоки).
      // Plotly-графики дорисовываются после insert в _renderBlockChartsInline,
      // тоже через shared _renderPeriodSnapshotCharts с тем же prefix.
      const sharedHtml = _buildPeriodSnapshotPreviewHtml(snap, b, `prev-${bid}-`);
      return baselineDescHtml + sharedHtml;
    }

    if (b.kind === 'comparison') {
      // WYSIWYG: Plotly график «день от начала» (parts.chart) + таблица
      // характеристик сегментов (parts.segments_table)
      const bid = b.id || 'x';
      const segs = snap.segments || [];
      const metric = snap.metric_label || snap.metric || '—';
      const method = snap.method === 'theil_sen' ? 'Theil-Sen' : 'МНК линейная';

      const hdrHtml = `
        <div style="font-size:0.85rem;">
          Метрика: <b>${_escHtml(metric)}</b>, метод: <b>${_escHtml(method)}</b>,
          сегментов: <b>${segs.length}</b>
        </div>`;
      // Контейнер графика (Plotly newPlot после insert)
      const chartBox = (parts.chart !== false && segs.length)
        ? `<div id="prev-${bid}-cmp" style="height:240px; margin-top:6px;"></div>` : '';
      // Таблица сегментов
      let tableHtml = '';
      if (parts.segments_table !== false) {
        let rows = '';
        segs.forEach(s => {
          const arrow = (s.slope_per_hour ?? 0) > 0.001 ? '↑'
                      : (s.slope_per_hour ?? 0) < -0.001 ? '↓' : '→';
          const dur = s.duration_hours ? `${(s.duration_hours / 24).toFixed(1)} сут` : '—';
          rows += `<tr><td style="padding:2px 6px;"><b style="color:${s.color || '#374151'};">${_escHtml(s.letter || '?')}</b></td>
                       <td style="padding:2px 6px;">${_escHtml(s.source === 'customer' ? 'UzKor' : 'UniT')}</td>
                       <td style="padding:2px 6px;">${dur}</td>
                       <td style="text-align:right; padding:2px 6px;">${_fmtNum(s.mean)}</td>
                       <td style="text-align:right; padding:2px 6px;">${_fmtNum(s.median)}</td>
                       <td style="text-align:right; padding:2px 6px;">${arrow} ${_fmtNum(s.slope_per_day, 3)}/д</td></tr>`;
        });
        tableHtml = `
          <table style="width:100%; margin-top:6px; font-size:0.78rem; border-collapse:collapse;">
            <tr style="background:#f3f4f6;">
              <th style="text-align:left; padding:3px 6px;">Сег</th>
              <th style="text-align:left; padding:3px 6px;">Источник</th>
              <th style="text-align:left; padding:3px 6px;">Длит.</th>
              <th style="text-align:right; padding:3px 6px;">Среднее</th>
              <th style="text-align:right; padding:3px 6px;">Медиана</th>
              <th style="text-align:right; padding:3px 6px;">Тренд</th>
            </tr>
            ${rows || '<tr><td colspan="6" style="text-align:center; color:#9ca3af;">Сегментов нет</td></tr>'}
          </table>`;
      }
      return hdrHtml + chartBox + tableHtml + comment;
    }

    if (b.kind === 'criteria_rose') {
      // WYSIWYG: реальные Plotly роза + бар + таблица. Контейнеры с уникальными
      // id, Plotly.newPlot вызывается из renderChapterPreview после insert.
      const bid = b.id || 'x';
      const h = snap.history || {};
      const hdrHtml = `
        <div style="font-size:0.85rem;">
          Скв. <b>${_escHtml(snap.well_number)}</b>, период <b>${_escHtml(snap.period_from)}…${_escHtml(snap.period_to)}</b>
          (${snap.period_days || '?'} сут.), штуцер <b>${h.choke_mm ?? '—'} мм</b>,
          режим <b>${_escHtml(snap.mode || '—')}</b>,
          окон в истории: <b>${h.windows_count || 0}</b>.
        </div>
        <div style="font-size:1.4rem; font-weight:700; color:#d62728; margin:4px 0;">
          Балл: ${(snap.score ?? 0).toFixed(1)} / 100
        </div>`;
      // Контейнеры для розы и бар-чарта
      const roseBox = (parts.rose_chart !== false)
        ? `<div id="prev-${bid}-rose" style="height:280px;"></div>` : '';
      const barBox = (parts.contributions_chart !== false)
        ? `<div id="prev-${bid}-rosebar" style="height:200px; margin-top:6px;"></div>` : '';
      // Таблица 6 метрик
      let tableHtml = '';
      if (parts.metrics_table !== false) {
        const ranks = snap.ranks || {};
        const contrib = snap.contributions || {};
        const labels = snap.labels || {
          decline: 'Снижение Q', p_wh_down: 'Снижение P устья',
          p_wh_cv: 'Волатильность P устья', p_fl_up: 'Рост P шлейфа',
          shutdown: 'Доля простоя', freq: 'Частота простоев',
        };
        const KEYS = ['decline', 'p_wh_down', 'p_wh_cv', 'p_fl_up', 'shutdown', 'freq'];
        let rows = '';
        KEYS.forEach(k => {
          const r = ranks[k];
          const c = contrib[k] || {};
          const zone = r == null ? '—'
            : r > 75 ? '<span style="color:#dc2626; font-weight:600;">риск</span>'
            : r > 50 ? '<span style="color:#d97706;">хуже нормы</span>'
            : r < 50 ? '<span style="color:#16a34a;">лучше нормы</span>'
            : 'норма';
          rows += `<tr><td style="padding:2px 6px;">${_escHtml(labels[k] || k)}</td>
                       <td style="text-align:right; padding:2px 6px; font-weight:600;">${r == null ? '—' : r.toFixed(0)}</td>
                       <td style="text-align:right; padding:2px 6px;">${(c.weight ?? 0).toFixed(2)}</td>
                       <td style="text-align:right; padding:2px 6px; color:#d62728;">${(c.actual ?? 0).toFixed(1)}</td>
                       <td style="padding:2px 6px;">${zone}</td></tr>`;
        });
        tableHtml = `
          <table style="width:100%; margin-top:6px; font-size:0.78rem; border-collapse:collapse;">
            <tr style="background:#f3f4f6;">
              <th style="text-align:left; padding:3px 6px;">Критерий</th>
              <th style="text-align:right; padding:3px 6px;">Ранг</th>
              <th style="text-align:right; padding:3px 6px;">Вес</th>
              <th style="text-align:right; padding:3px 6px;">Вклад</th>
              <th style="text-align:left; padding:3px 6px;">Зона</th>
            </tr>
            ${rows}
          </table>`;
      }
      // Предупреждения
      let warnHtml = '';
      if (parts.warnings !== false && snap.warnings && snap.warnings.length) {
        warnHtml = snap.warnings.map(w => `
          <div style="margin-top:4px; padding:4px 8px; background:#fef3c7;
               border-left:3px solid #f59e0b; font-size:0.78rem; color:#92400e;">
            ⚠ ${_escHtml(w)}
          </div>`).join('');
      }
      return hdrHtml + roseBox + barBox + tableHtml + warnHtml + comment;
    }

    // ─── Observation blocks (датчики) ───
    if (b.kind && b.kind.startsWith('observation_')) {
      return _buildObservationBlockHtml(snap, b, parts) + comment;
    }

    // ─── Adaptation blocks (Step 5 wizard) ───
    if (b.kind === 'adaptation_period_analysis') {
      return _buildAdaptationPeriodAnalysisHtml(snap, b, parts) + comment;
    }
    if (b.kind === 'optimal_window') {
      return _buildOptimalWindowHtml(snap, b, parts) + comment;
    }
    if (b.kind === 'reagent_irv_summary') {
      return _buildReagentIrvSummaryHtml(snap, b, parts) + comment;
    }

    // Fallback для неизвестных kinds
    return `<div style="font-size:0.82rem; color:#9ca3af;">
              Тип блока «${_escHtml(b.kind)}» — сводки нет.
            </div>${comment}`;
  }

  // ===== _renderSegmentBlockCharts — Plotly графики для segment_analysis =====
  // Рендерит график Q с трендами сегментов и вертикальными линиями changepoints
  function _renderSegmentBlockCharts(snap, idPrefix) {
    if (!window.Plotly) return;

    const chartEl = document.getElementById(idPrefix + 'qchart');
    if (!chartEl) {
      console.warn('[chapter_render] _renderSegmentBlockCharts: no container', idPrefix + 'qchart');
      return;
    }

    // Нормализация chart_data из разных форматов
    const cd = _normalizeSegmentChartData(snap);
    const segs = snap.segments_extended || snap.segments || [];
    const cps = snap.cp_marks || snap.changepoints || [];

    if (!cd.dates || !cd.dates.length) {
      chartEl.innerHTML = '<div style="color:#9ca3af;padding:15px;text-align:center;">Нет данных для графика</div>';
      return;
    }

    const traces = [];
    const SEG_COLORS = ['#6366f1', '#22c55e', '#f59e0b', '#ec4899', '#14b8a6', '#8b5cf6', '#ef4444', '#06b6d4'];

    // Q total line + markers
    if (cd.q_total && cd.q_total.length) {
      traces.push({
        x: cd.dates, y: cd.q_total,
        name: 'Q общий',
        mode: 'lines+markers',
        marker: { size: 4, color: '#1f77b4' },
        line: { color: '#1f77b4', width: 1 },
        hovertemplate: 'Q = %{y:.2f}<extra></extra>',
      });
    }

    // Q working line (if available)
    if (cd.q_working && cd.q_working.length) {
      traces.push({
        x: cd.dates, y: cd.q_working,
        name: 'Q рабочий',
        mode: 'lines+markers',
        marker: { size: 3, symbol: 'square', color: '#22c55e' },
        line: { color: '#22c55e', width: 1 },
        hovertemplate: 'Q раб = %{y:.2f}<extra></extra>',
      });
    }

    // Segment trend lines
    segs.forEach(function(seg, i) {
      var color = SEG_COLORS[i % SEG_COLORS.length];
      var startIdx = seg.start_idx != null ? seg.start_idx : 0;
      var endIdx = seg.end_idx != null ? seg.end_idx : cd.dates.length;
      var slope = seg.slope != null ? seg.slope : (seg.slope_total || 0);
      var intercept = seg.intercept != null ? seg.intercept : (seg.mean_value || seg.mean_q || 0);
      var xDates = cd.dates.slice(startIdx, endIdx);
      var yTrend = xDates.map(function(_, j) { return intercept + slope * j; });
      traces.push({
        x: xDates, y: yTrend,
        name: 'Сегмент ' + (seg.num || i + 1),
        mode: 'lines',
        line: { color: color, width: 2 },
        hoverinfo: 'skip',
      });
    });

    // Changepoint vertical lines
    var minQ = cd.q_total && cd.q_total.length ? Math.min.apply(null, cd.q_total.filter(function(v) { return v != null; })) : 0;
    var maxQ = cd.q_total && cd.q_total.length ? Math.max.apply(null, cd.q_total.filter(function(v) { return v != null; })) : 1;
    cps.forEach(function(cp) {
      var cpDate = (typeof cp === 'object') ? (cp.date || cp.timestamp) : null;
      // Если cp — число (индекс), получаем дату
      if (cpDate == null && typeof cp === 'number' && cd.dates[cp]) {
        cpDate = cd.dates[cp];
      }
      if (!cpDate) return;
      traces.push({
        x: [cpDate, cpDate],
        y: [minQ * 0.95, maxQ * 1.05],
        mode: 'lines',
        line: { color: '#ef4444', width: 1, dash: 'dash' },
        showlegend: false,
        hoverinfo: 'skip',
      });
    });

    var layout = {
      height: 450,
      margin: { l: 55, r: 20, t: 30, b: 50 },
      legend: { orientation: 'h', y: -0.12 },
      xaxis: { title: '', tickangle: -45 },
      yaxis: { title: 'Q, тыс.м³/сут' },
    };

    try {
      Plotly.newPlot(chartEl, traces, layout, { responsive: true, displayModeBar: false });
    } catch (e) {
      console.error('[chapter_render] segment chart error:', e);
      chartEl.innerHTML = '<div style="color:#dc2626;padding:10px;">Ошибка графика: ' + e.message + '</div>';
    }
  }

  // Нормализует chart_data из разных форматов (segment_analysis)
  function _normalizeSegmentChartData(snap) {
    var chartData = snap.chart_data || {};

    // Формат 1: уже нормализован (q_total + dates)
    if (chartData.q_total && chartData.dates) {
      return chartData;
    }

    // Формат 2: segment-demo API (primary.values)
    if (chartData.primary && chartData.primary.values) {
      var result = { dates: chartData.dates || [], q_total: chartData.primary.values };
      var sec = chartData.secondary || {};
      Object.keys(sec).forEach(function(colName) {
        var colData = sec[colName];
        if (!colData || !colData.values) return;
        var vals = colData.values;
        if (colName.indexOf('flow') !== -1 || colName === 'flow_rate_mean') {
          if (!result.q_working) result.q_working = vals;
        } else if (colName.indexOf('shutdown') !== -1 || colName.indexOf('downtime') !== -1) {
          result.shutdown_min = vals;
        }
      });
      return result;
    }

    // Формат 3: observation_segment (raw.chart_payload)
    var raw = snap.raw || {};
    var payload = raw.chart_payload || {};
    var timeValues = payload.dates || payload.timestamps;
    if (timeValues && payload.q) {
      var dates = timeValues.map(function(ts) {
        return (typeof ts === 'string' && ts.length >= 10) ? ts.slice(0, 10) : ts;
      });
      return { dates: dates, q_total: payload.q, shutdown_min: payload.shutdown_min };
    }

    // Fallback
    return chartData;
  }

  // ===== _renderSegmentComparisonChart — Plotly overlay для сравнения сегментов =====
  function _renderSegmentComparisonChart(snap, idPrefix) {
    if (!window.Plotly) return;

    var chartEl = document.getElementById(idPrefix + 'cmpchart');
    if (!chartEl) return;

    var chartOverlay = snap.chart_overlay || {};
    var series = chartOverlay.series || [];
    var segsCmp = snap.segments_compared || [];

    if (!series.length) {
      chartEl.innerHTML = '<div style="color:#9ca3af;padding:15px;text-align:center;">Нет данных для графика сравнения</div>';
      return;
    }

    var SEG_COLORS = ['#6366f1', '#22c55e', '#f59e0b', '#ec4899', '#14b8a6', '#8b5cf6', '#ef4444', '#06b6d4'];
    var traces = [];

    series.forEach(function(s, i) {
      var color = (segsCmp[i] && segsCmp[i].color) || SEG_COLORS[i % SEG_COLORS.length];
      var label = (segsCmp[i] && segsCmp[i].label) || ('Сегмент ' + (i + 1));
      traces.push({
        x: s.x_norm || s.x || Array.from({ length: (s.values || []).length }, function(_, j) { return j; }),
        y: s.values || [],
        name: label,
        mode: 'lines+markers',
        marker: { size: 4, color: color },
        line: { color: color, width: 2 },
        hovertemplate: label + ': %{y:.2f}<extra></extra>',
      });
    });

    var layout = {
      height: 320,
      margin: { l: 50, r: 20, t: 20, b: 45 },
      legend: { orientation: 'h', y: -0.18 },
      xaxis: { title: 'День (нормализованный)', zeroline: false },
      yaxis: { title: 'Q' },
    };

    try {
      Plotly.newPlot(chartEl, traces, layout, { responsive: true, displayModeBar: false });
    } catch (e) {
      console.error('[chapter_render] comparison chart error:', e);
      chartEl.innerHTML = '<div style="color:#dc2626;padding:10px;">Ошибка графика: ' + e.message + '</div>';
    }
  }

  // ─────────────────────────────────────────────────────────────────
  //  renderChapter — параметризованный движок (из renderChapterPreview).
  //  Параметризованы 4 точки: containerId, countId, chapterTitle, kindOrder.
  //  Графики (_render*Charts) вызываются опционально — через typeof guard
  //  (в этой версии движка только текст; графики — следующий под-шаг).
  // ─────────────────────────────────────────────────────────────────
  function renderChapter(allBlocks, cfg) {
    cfg = cfg || {};
    var content = $(cfg.containerId);
    var counter = cfg.countId ? $(cfg.countId) : null;
    if (!content) return;
    var inReport = (allBlocks || []).filter(function (b) { return b.in_report; });
    if (counter) {
      counter.textContent = inReport.length
        ? inReport.length + ' ' +
          (inReport.length === 1 ? 'блок' : inReport.length < 5 ? 'блока' : 'блоков') +
          ' в отчёт'
        : 'пока пусто';
    }
    if (inReport.length && typeof markPdfStale === 'function') markPdfStale();
    if (typeof cdBroadcastBlocks === 'function') cdBroadcastBlocks(allBlocks);

    if (!inReport.length) {
      content.innerHTML =
        '<div style="text-align:center; padding:20px; color:#6b7280;">' +
        '<div style="font-size:2.2rem; margin-bottom:6px;">\u{1F4D1}</div>' +
        '<div>Глава пуста — ни один блок не помечен «в отчёт».</div>' +
        '<div style="font-size:0.82rem; margin-top:4px;">' +
        'Поставь галочку «в отчёт» у блока — он появится здесь.</div></div>';
      return;
    }

    var KIND_ORDER = cfg.kindOrder || {
      chapter_intro: 1, period_analysis: 2, segment_analysis: 3,
      comparison: 4, criteria_rose: 5, baseline: 6,
      // Observation (датчики): baseline → period → segment → analysis
      observation_baseline: 1,
      observation_period: 2,
      observation_segment: 3,
      observation_analysis: 4,
      // Adaptation (Step 5 wizard)
      adaptation_period_analysis: 1,
      optimal_window: 2,
      reagent_irv_summary: 3,
    };
    inReport.sort(function (a, b) {
      var ka = KIND_ORDER[a.kind] || 99;
      var kb = KIND_ORDER[b.kind] || 99;
      if (ka !== kb) return ka - kb;
      return (a.sort_order || 0) - (b.sort_order || 0);
    });

    var html =
      '<h3 style="margin:0 0 8px; font-size:1.05rem; color:#374151; ' +
      'border-bottom:1.5px solid #d1d5db; padding-bottom:4px;">' +
      _escHtml(cfg.chapterTitle || 'Глава') + '</h3>';
    if (cfg.sourceLabel) {
      html += '<div style="font-size:0.78rem; color:#6b7280; margin-bottom:14px;">' +
              _escHtml(cfg.sourceLabel) +
              ' В отчёт включено: ' + inReport.length + '.</div>';
    }

    var numPrefix = cfg.numPrefix || '';
    inReport.forEach(function (b, i) {
      var subIdx = numPrefix + (i + 1);
      var title = _escHtml(b.title || ('Блок #' + b.id));
      var parts = _getPartsForBlock(b);
      var pre = (b.params && b.params.prefix_note) || '';
      var suf = (b.params && b.params.suffix_note) || '';
      var noteCss = 'font-style:italic; color:#475569; margin:6px 0; ' +
        'padding:5px 8px; background:#f1f5f9; border-radius:3px; ' +
        'border-left:2px solid #94a3b8; white-space:pre-wrap;';
      var preHtml = (parts.prefix_note !== false && pre.trim())
        ? '<div style="' + noteCss + '">' + _escHtml(pre) + '</div>' : '';
      var sufHtml = (parts.suffix_note !== false && suf.trim())
        ? '<div style="' + noteCss + '">' + _escHtml(suf) + '</div>' : '';
      html +=
        '<div class="cd-preview-block" data-block-id="' + b.id + '" ' +
        'style="margin-bottom:14px; padding:10px 12px; background:#fafafa; ' +
        'border-left:3px solid #6366f1; border-radius:4px;">' +
        '<div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">' +
        '<span style="font-weight:600; color:#374151;">' + subIdx + '. ' + title + '</span>' +
        _kindBadge(b.kind) + '</div>' +
        preHtml + _renderBlockSummaryHtml(b) + sufHtml + '</div>';
    });
    content.innerHTML = html;

    // Графики — опционально (следующий под-шаг). Без них движок рендерит текст.
    inReport.forEach(function (b) {
      // baseline/period_analysis — данные заказчика (UzKorGaz)
      if (b.kind === 'baseline' || b.kind === 'period_analysis') {
        if (typeof _renderBlockChartsInline === 'function') _renderBlockChartsInline(b);
      } else if (b.kind === 'criteria_rose') {
        if (typeof _renderRoseBlockCharts === 'function') _renderRoseBlockCharts(b);
      } else if (b.kind === 'comparison') {
        if (typeof _renderComparisonBlockChart === 'function') _renderComparisonBlockChart(b);
      } else if (b.kind === 'segment_analysis') {
        if (typeof _renderSegmentBlockCharts === 'function') {
          _renderSegmentBlockCharts(b.data_snapshot || {}, 'segprev-' + b.id + '-');
        }
      } else if (b.kind === 'segment_comparison') {
        if (typeof _renderSegmentComparisonChart === 'function') {
          _renderSegmentComparisonChart(b.data_snapshot || {}, 'segcmp-' + b.id + '-');
        }
      } else if (b.kind && b.kind.startsWith('observation_')) {
        // Observation блоки (датчики) — рендерим Plotly графики
        if (typeof _renderObservationBlockCharts === 'function') {
          _renderObservationBlockCharts(b);
        }
      }
    });
  }

  window.renderChapter = renderChapter;
})();
