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

  // Дефолтный текст блока «Рекомендации (ТПАВ)» для анализа нестабильности.
  // ЗЕРКАЛО: backend/services/adaptation_report_service.py (STABILITY_RECOMMENDATION_DEFAULT)
  // и backend/templates/customer_daily.html (префилл textarea). Менять — во всех трёх.
  const STABILITY_RECOMMENDATION_DEFAULT =
    'По результатам оценки критериев нестабильности скважина рекомендуется к постановке ' +
    'на наблюдение для сбора фактических данных о режимах работы.\n\n' +
    'Для этого необходимо:\n' +
    '1. Установить систему мониторинга SMOD с фиксацией устьевых параметров — давления ' +
    'на устье (трубного) и давления в линии за штуцером (линейного).\n' +
    '2. Выполнить детальный анализ и сопоставление полученных данных: оценить ключевые ' +
    'параметры дебита с учётом фактического рабочего времени скважины.\n' +
    '3. На основании сопоставления фактических данных принять окончательное решение о ' +
    'целесообразности проведения работ по адаптации технологии применения ТПАВ для ' +
    'данной скважины.';
  window.STABILITY_RECOMMENDATION_DEFAULT = STABILITY_RECOMMENDATION_DEFAULT;

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
    stability_rose: [
      ['prefix_note',           '↑ Вступительный текст'],
      ['rose_chart',            '🌀 Роза нестабильности'],
      ['contributions_chart',   '📈 Бар-чарт вкладов'],
      ['metrics_table',         '📋 Таблица параметров'],
      ['descriptions',          'ⓘ Описание параметров'],
      ['description',           '✏️ Комментарий пользователя'],
      ['recommendation',        '✍️ Рекомендации (ТПАВ)'],
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
      ['descriptions',          '📝 Описание сегментов (кратко)'],
      ['detailed',              '📑 Развёрнутый анализ сегментов (подробно)'],
      ['description',           '✏️ Комментарий пользователя'],
      ['suffix_note',           '↓ Заключительный текст'],
    ],
    // ─── Observation blocks (from sensors) ───
    // observation_analysis — 4 отдельных графика из wz3RenderCharts
    observation_analysis: [
      ['prefix_note',        '↑ Вступительный текст'],
      ['intro',              '📝 Период / заголовок'],
      ['metrics_table',      '🟧 Таблица метрик'],
      ['metrics_tiles',      '🎯 Плитки метрик (альтернатива таблице)'],
      ['charts_grid',        '📊 Графики 2×2 (вместо колонки)'],
      ['chart_pressures',    '📈 График давлений (P тр, P лин)'],
      ['chart_dp',           '📈 График ΔP'],
      ['chart_q',            '📈 График Q(t)'],
      ['chart_utilization',  '📊 Рабочее время по дням'],
      ['events_table',       '📋 Таблица событий'],
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
    // Сопоставление LoRa и УзКорГаз (observation chapter)
    sensor_customer_comparison: [
      ['methodology',       '📖 Методика сравнения'],
      ['show_q_chart',      '📈 График дебита Q'],
      ['show_dp_chart',     '📈 График перепада ΔP'],
      ['trend_character',   '📈 Сравнение характера динамики'],
      ['show_table',        '📋 Таблица посуточного сравнения'],
      ['show_conclusion',   '📝 Заключение'],
    ],
    pressure_spectrum: [
      ['show_ptube_chart',  '📊 Гистограмма P устьевого'],
      ['show_dp_chart',     '📊 Гистограмма ΔP'],
      ['show_metrics_table','📋 Таблица метрик стабильности'],
      ['show_comparison',   '🔀 Сравнение спектров'],
      ['show_method_note',  '📖 Описание метода'],
      ['description',       '✏️ Комментарий пользователя'],
    ],
    param_correlation: [
      ['show_chart',        '📈 График зависимости (scatter + регрессия)'],
      ['show_stats',        '📋 Параметры регрессии'],
      ['show_method_note',  '📖 Описание метода'],
      ['description',       '✏️ Комментарий пользователя'],
    ],
    // ─── Adaptation blocks (Step 5 wizard) ───
    adaptation_period_analysis: [
      ['prefix_note',        '↑ Вступительный текст'],
      ['setup_description',  '📋 Описание установки (ШУ, мониторинг, расчёт дебита)'],
      ['sensor_table',       '📋 Параметры датчиков (Табл. 2.1)'],
      ['flow_methodology',   '📐 Формулы и методика расчёта дебита'],
      ['intro',              '📋 Введение (период, датчики SMOD)'],
      ['charts',             '📈 Графики (P, ΔP, Q) + события'],
      ['downtime_chart',     '🟩 Рабочее/нерабочее время (посуточно)'],
      ['stats_table',        '📊 Статистические показатели'],
      ['events_table',       '📋 Таблица событий'],
      ['events_stats',       '🟦 Статистика событий (плитки)'],
      ['metrics_cards',      '🟧 Карточки метрик (R, R★, B2, B1)'],
      ['comparison_table',   '📋 Таблица сравнения (Наблюд. → Адапт.)'],
      ['trends',             '📉 Тренды (Q, ΔP)'],
      ['description',        '✏️ Комментарий пользователя'],
      ['suffix_note',        '↓ Заключительный текст'],
    ],
    adaptation_effectiveness: [
      ['intro',              '📋 Вводное описание'],
      ['summary_table',      '📋 Сводная таблица'],
      ['chart_q',            '📈 Диаграмма «Дебит Q»'],
      ['chart_dp',           '📈 Диаграмма «Перепад ΔP»'],
      ['chart_downtime',     '📈 Диаграмма «Нерабочее время»'],
      ['captions',           '💬 Пояснения к диаграммам'],
      ['series_b1',          '🏢 Данные УзКорГаз (суточные сводки)'],
      ['series_b2',          '🔭 Данные наблюдения (B2)'],
      ['series_r',           '🧪 Данные адаптации (R)'],
      ['verdict',            '📝 Заключение'],
      ['recommendation',     '✍️ Рекомендации'],
      ['prefix_note',        '↑ Вступительный текст'],
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
      ['cards',              '🟧 Карточки (лучший / вбросы / расход)'],
      ['narrative',          '📖 Описание'],
      ['scores_table',       '📋 Шкала эффективности реагентов'],
      ['periodicity_table',  '⏱ Анализ периодичности вбросов'],
      ['intervals_chart',    '📊 Диаграмма интервалов между вбросами'],
      ['charts',             '📈 Графики (вбросы / Score по времени)'],
      ['detailed',           '🔻 Детализация по ИРВ (таблица)'],
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
  // Глава «Отчёт за период»: period_full_analysis = зеркало adaptation_period_analysis
  // (тот же снапшот и набор частей). Источник истины — один PART_LABELS-список.
  PART_LABELS.period_full_analysis = PART_LABELS.adaptation_period_analysis;
  // Сравнение метрик периода (parity с PERIOD_PART_LABELS в adaptation_wizard.html).
  PART_LABELS.period_comparison = [
    ['summary_table',  '📋 Таблица сравнения метрик'],
    ['overlay_charts', '📈 Графики-оверлеи (Q / ΔP)'],
    ['description',    '✏️ Текстовое описание сравнения'],
  ];

  // Parts с дефолтом OFF (альтернативные представления, опциональные раскладки)
  const _PARTS_DEFAULT_OFF = new Set([
    'metrics_tiles',   // альтернатива metrics_table
    'charts_grid',     // альтернативная раскладка 2×2
  ]);

  function _getPartsForBlock(b) {
    const defs = PART_LABELS[b.kind] || [];
    const saved = (b.params && b.params.parts) || {};
    const out = {};
    for (const [key] of defs) {
      // Дефолт: true, кроме _PARTS_DEFAULT_OFF
      const defaultVal = !_PARTS_DEFAULT_OFF.has(key);
      out[key] = (key in saved) ? !!saved[key] : defaultVal;
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
      stability_rose:       ['🌀', '#fee2e2', '#991b1b', 'Роза нестабильности'],
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
      adaptation_effectiveness:   ['📊', '#fef9c3', '#854d0e', 'Оценка эффективности'],
      // Отчёт за период (Step 8 wizard)
      period_full_analysis:       ['📊', '#dbeafe', '#1e40af', 'Анализ периода'],
      period_comparison:          ['↔', '#ede9fe', '#5b21b6', 'Сравнение (период)'],
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
    // Описания сегментов могут лежать в разных местах снимка в зависимости от пути
    // сохранения блока (standalone segment_analysis vs sub-section адаптации).
    const descriptions = snap.descriptions
      || (snap.interpretation && snap.interpretation.descriptions)
      || (snap.segment_analysis && snap.segment_analysis.interpretation
          && snap.segment_analysis.interpretation.descriptions)
      || [];
    const cpDescs = snap.cp_descriptions
      || (snap.interpretation && snap.interpretation.cp_descriptions)
      || [];
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

    // Цель раздела (парность с PDF)
    html += `<div style="font-size:0.82rem; color:#475569; font-style:italic; line-height:1.5; margin-bottom:10px;">Цель раздела — разбить кривую дебита на характерные участки (рост / плато / спад) по точкам перелома и оценить реакцию скважины на вбросы реагента. По каждому сегменту приведены тренды и признаки выноса воды (ПАВ), что помогает понять, как реагент влияет на режим в динамике.</div>`;

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

    // ─── 2. Таблица сегментов: 12 колонок (§5.2 + §6.1 «Тип режима» + P_шл, P_уст, без Q раб.)
    // Словарь русских названий типов режима
    const segTypeLabels = {
      'initial': 'Начальный',
      'stable': 'Стабильный',
      'rise': 'Рост',
      'decline': 'Снижение',
      'sharp_rise': 'Резкий рост',
      'sharp_decline': 'Резкое снижение',
      'volatile': 'Волатильный',
      'unknown': 'Неопределён',
    };
    if (on('segments_table') && segs.length) {
      let tbl = `<h3 style="margin:14px 0 6px;">📋 Сегменты режима (${segs.length})</h3>
        <table class="cd-table"><thead><tr>
          <th>№</th><th>Период</th><th>Дн</th>
          <th>Q общ.</th>
          <th>Тренд</th><th>Изм%</th>
          <th>Прост, мин</th><th>Раб%</th>
          <th>P_шл</th><th>P_уст</th><th>ΔP</th>
          <th>Тип режима</th>
        </tr></thead><tbody>`;
      for (const s of segs) {
        const chg = s.change_pct;
        const chgColor = chg == null ? '#9ca3af'
                       : chg > 0 ? '#28a745' : chg < 0 ? '#dc3545' : '#6b7280';
        const chgTxt = chg == null ? '—' : (chg > 0 ? '+' : '') + chg.toFixed(0) + '%';
        const slopeTxt = s.slope_total == null ? '—' : fmtSigned(s.slope_total);
        const typeRaw = s.type || 'unknown';
        const typeLabel = segTypeLabels[typeRaw] || typeRaw;
        tbl += `<tr>
          <td><b>${s.num}</b></td>
          <td style="white-space:nowrap;">${s.start}–${s.end}</td>
          <td>${s.days}</td>
          <td>${fmt(s.mean_q, 1)}</td>
          <td>${slopeTxt}</td>
          <td style="color:${chgColor}; font-weight:600;">${chgTxt}</td>
          <td>${fmt(s.mean_shutdown, 0)}</td>
          <td>${fmt(s.working_pct, 0)}</td>
          <td>${fmt(s.mean_p_flowline, 1)}</td>
          <td>${fmt(s.mean_p_wellhead, 1)}</td>
          <td>${fmt(s.mean_dp, 2)}</td>
          <td style="font-weight:500; color:${s.color || '#374151'};">
            ${typeLabel}
          </td>
        </tr>`;
      }
      tbl += '</tbody></table>';
      html += tbl;
    }

    // ─── 3. Полное описание сегментов (§6.4) — из алгоритма ────────
    // Показываем полный текст описания из snapshot.descriptions
    // (генерируется в segment_analysis_module.py)
    if (on('descriptions') && descriptions.length) {
      html += `<h3 style="margin:14px 0 6px;">📝 Описание сегментов (${descriptions.length})</h3>` +
        `<div style="display:flex; flex-direction:column; gap:6px;">` +
        descriptions.map(t => `<div style="padding:10px 14px; background:#fff;
                  border-left:3px solid #6366f1; border-radius:3px;
                  font-size:0.86rem; line-height:1.6; color:#1f2937;
                  white-space:pre-wrap;">${(t||'').replace(/\*\*/g,'').replace(/</g,'&lt;')}</div>`).join('') +
        `</div>`;
    }

    // ─── 4. События переломов (§6.5) ──────────────────────────────
    if (on('cp_descriptions') && cpDescs.length) {
      html += `<h3 style="margin:14px 0 6px;">🔻 События переломов (${cpDescs.length})</h3>` +
        `<div style="display:flex; flex-direction:column; gap:6px;">` +
        cpDescs.map(t => `<div style="padding:8px 12px; background:#fff;
                  border-left:3px solid #7c3aed; border-radius:3px;
                  font-size:0.84rem; line-height:1.55; color:#1f2937;
                  white-space:pre-wrap;">${(t||'').replace(/\*\*/g,'').replace(/</g,'&lt;')}</div>`).join('') +
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

    // Цель раздела (парность с PDF)
    html += `<div style="font-size:0.82rem; color:#475569; font-style:italic; line-height:1.5; margin-bottom:10px;">Цель раздела — наложить выбранные участки работы скважины на один график и сопоставить их по ключевым показателям и трендам, чтобы оценить изменение режима между участками.</div>`;

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

  // ===== _buildSensorCustomerComparisonHtml — сопоставление LoRa и УзКорГаз =====
  function _buildSensorCustomerComparisonHtml(snap, block, idPrefix) {
    if (!snap) return '<div style="color:#9ca3af;">Снапшот не загружен.</div>';
    // Используем parts из block.params (единый механизм управления частями)
    // Fallback на settings из snapshot для обратной совместимости
    const parts = (block && block.params && block.params.parts) || {};
    const settings = snap.settings || {};
    // Хелпер: part включён? Приоритет: parts из блока > settings из snapshot > default true
    const on = (key) => {
      if (key in parts) return !!parts[key];
      if (key in settings) return !!settings[key];
      return true;
    };
    const summary = snap.summary || {};
    const dailyDiff = snap.daily_diff || [];
    const curves = snap.curves || {};
    const conclusion = snap.conclusion || '';

    const fmt = (v, d = 2) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d);
    const fmtPct = (v) => {
      if (v == null || isNaN(v)) return '—';
      const sign = v >= 0 ? '+' : '';
      const cls = Math.abs(v) < 10 ? 'delta-ok' : 'delta-warn';
      return `<span style="color:${Math.abs(v) < 10 ? '#22c55e' : '#d97706'};">${sign}${v.toFixed(1)}%</span>`;
    };

    let html = '';

    // Header
    html += `<div style="font-size:0.9rem; color:#374151; margin-bottom:10px;">
      <b>Период:</b> ${_escHtml(snap.period_from || '—')} — ${_escHtml(snap.period_to || '—')}
      ${snap.well_number ? `, скв. ${_escHtml(snap.well_number)}` : ''}
    </div>`;

    // Аналитический вводный абзац (паритет с .tex §3.4.N sensor_customer_comparison).
    html += `<div style="font-size:0.85rem; color:#374151; margin-bottom:8px;">
      За выбранный период выполнено сопоставление показателей, полученных датчиками давления
      (LoRa) в реальном времени, с суточными сводками заказчика (УзКорГаз) для оценки
      согласованности данных мониторинга и отчётности. На графиках ниже показаны расчётный
      дебит Q и перепад давления ΔP по обоим источникам с линейными трендами.
    </div>`;

    // Методика сопоставления (манометры LoRa ↔ суточные сводки УзКорГаз)
    if (on('methodology') && snap.methodology) {
      html += `<div style="font-size:0.8rem; color:#475569; background:#f8fafc; border-left:3px solid #94a3b8; padding:7px 10px; margin-bottom:12px; line-height:1.5;">
        <b>Методика:</b> ${_escHtml(snap.methodology)}</div>`;
    }

    // Summary tiles
    html += `<div style="display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px;">
      <div style="background:#fff; border:1px solid #e0e0e0; border-radius:4px; padding:6px 10px; min-width:100px;">
        <div style="font-size:0.7rem; color:#666;">Q LoRa (ср.)</div>
        <div style="font-weight:600;">${fmt(summary.sensor_q_avg)}</div>
      </div>
      <div style="background:#fff; border:1px solid #e0e0e0; border-radius:4px; padding:6px 10px; min-width:100px;">
        <div style="font-size:0.7rem; color:#666;">Q УзКорГаз (ср.)</div>
        <div style="font-weight:600;">${fmt(summary.customer_q_avg)}</div>
      </div>
      <div style="background:#fff; border:1px solid #e0e0e0; border-radius:4px; padding:6px 10px; min-width:100px;">
        <div style="font-size:0.7rem; color:#666;">Δ Q рабочий</div>
        <div style="font-weight:600;">${fmt(summary.delta_q_avg, 3)} (${fmtPct(summary.delta_q_pct)})</div>
      </div>
      <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:4px; padding:6px 10px; min-width:100px;">
        <div style="font-size:0.7rem; color:#666;">Q общий LoRa (ср.)</div>
        <div style="font-weight:600;">${fmt(summary.sensor_q_total_avg)}</div>
      </div>
      <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:4px; padding:6px 10px; min-width:100px;">
        <div style="font-size:0.7rem; color:#666;">Q общий УзКорГаз (ср.)</div>
        <div style="font-weight:600;">${fmt(summary.customer_q_total_avg)}</div>
      </div>
      <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:4px; padding:6px 10px; min-width:100px;">
        <div style="font-size:0.7rem; color:#666;">Δ Q общий</div>
        <div style="font-weight:600;">${fmt(summary.delta_q_total_avg, 3)} (${fmtPct(summary.delta_q_total_pct)})</div>
      </div>
      <div style="background:#fff; border:1px solid #e0e0e0; border-radius:4px; padding:6px 10px; min-width:100px;">
        <div style="font-size:0.7rem; color:#666;">ΔP LoRa (ср.)</div>
        <div style="font-weight:600;">${fmt(summary.sensor_dp_avg)}</div>
      </div>
      <div style="background:#fff; border:1px solid #e0e0e0; border-radius:4px; padding:6px 10px; min-width:100px;">
        <div style="font-size:0.7rem; color:#666;">ΔP УзКорГаз (ср.)</div>
        <div style="font-weight:600;">${fmt(summary.customer_dp_avg)}</div>
      </div>
      <div style="background:#fff; border:1px solid #e0e0e0; border-radius:4px; padding:6px 10px; min-width:100px;">
        <div style="font-size:0.7rem; color:#666;">Δ ΔP</div>
        <div style="font-weight:600;">${fmt(summary.delta_dp_avg, 3)} (${fmtPct(summary.delta_dp_pct)})</div>
      </div>
      <div style="background:#fff; border:1px solid #e0e0e0; border-radius:4px; padding:6px 10px; min-width:100px;">
        <div style="font-size:0.7rem; color:#666;">Дней сопоставлено</div>
        <div style="font-weight:600;">${summary.days_matched || 0} / ${summary.days_total || 0}</div>
      </div>
    </div>`;

    // Charts
    if (on('show_q_chart') || on('show_dp_chart')) {
      html += `<div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:12px;">`;
      if (on('show_q_chart')) {
        html += `<div>
          <div style="font-weight:500; font-size:0.85rem; margin-bottom:4px;">Дебит (Q), тыс.м³/сут</div>
          <div id="${idPrefix}chart-q" style="height:220px;"></div>
        </div>`;
      }
      if (on('show_dp_chart')) {
        html += `<div>
          <div style="font-weight:500; font-size:0.85rem; margin-bottom:4px;">Перепад давления (ΔP), кгс/см²</div>
          <div id="${idPrefix}chart-dp" style="height:220px;"></div>
        </div>`;
      }
      html += `</div>`;
      html += `<div style="font-size:0.78rem; color:#6b7280; margin:-4px 0 12px; line-height:1.45;">
        <i>Как читать:</i> ось X — дата; сплошные синие линии — данные датчика LoRa,
        пунктирные оранжевые — суточные сводки заказчика (УзКорГаз). Пунктир того же цвета — линейный тренд.
        Расхождение линий = разница между мониторингом в реальном времени и отчётными данными заказчика.
      </div>`;
    }

    // График относительного отклонения Δ% по дням — с настраиваемыми зонами допуска
    const _dispT = snap.display || {};
    const _t1 = Number(_dispT.dev_tol_green) > 0 ? Number(_dispT.dev_tol_green) : 15;
    const _t2 = Number(_dispT.dev_tol_amber) > _t1 ? Number(_dispT.dev_tol_amber) : _t1 + 1;
    const _t1s = _t1.toFixed(0), _t2s = _t2.toFixed(0);
    // Текстовый итог отклонения — считаем под текущие пороги (живёт в модалке/HTML;
    // PARITY с _deviation_summary_from_daily в comparison_service).
    const _devText = (() => {
      const dv = dailyDiff.filter(r => r.delta_q != null && r.sensor_q).map(r => [r.date, r.delta_q / r.sensor_q * 100]);
      if (!dv.length) return '';
      const n = dv.length;
      const g = dv.filter(([, p]) => Math.abs(p) <= _t1).length;
      const a = dv.filter(([, p]) => Math.abs(p) > _t1 && Math.abs(p) <= _t2).length;
      const r = dv.filter(([, p]) => Math.abs(p) > _t2).length;
      const w = dv.reduce((m, x) => Math.abs(x[1]) > Math.abs(m[1]) ? x : m, dv[0]);
      return `Относительное отклонение LoRa от УзКорГаз (к данным LoRa) по дням: в допуске ±${_t1s}% — ${g} из ${n} дн.; повышенное ${_t1s}–${_t2s}% — ${a} дн.; значительное >${_t2s}% — ${r} дн. Максимум ${Math.abs(w[1]).toFixed(0)}% (${w[0]}, LoRa ${w[1] > 0 ? 'выше' : 'ниже'} заказчика).`;
    })();
    html += `<div style="margin-bottom:8px;">
      <div style="font-weight:500; font-size:0.85rem; margin-bottom:2px;">Относительное отклонение (LoRa − УзКорГаз) / LoRa, % — зоны допуска</div>
      <div style="font-size:0.74rem; color:#6b7280; margin-bottom:4px;">🟩 ±${_t1s}% — в допуске · 🟧 ${_t1s}–${_t2s}% — повышенное · 🟥 &gt;${_t2s}% — значительное расхождение.</div>
      <div id="${idPrefix}chart-dev" style="height:220px;"></div>
      ${_devText ? `<div style="font-size:0.8rem; color:#374151; margin-top:4px; line-height:1.45;">${_escHtml(_devText)}</div>` : ''}
    </div>`;

    // Анализ согласия (отклонение / тренды / смещение / разброс / задержка)
    const analysis = snap.analysis || {};
    const aRows = [analysis.q_working, analysis.q_total, analysis.dp].filter(a => a && !a.insufficient);
    if (aRows.length) {
      const fmtA = (v, d = 1) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d);
      html += `<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:8px 10px;margin-bottom:12px;">
        <div style="font-weight:600;font-size:0.85rem;margin-bottom:4px;">⚖️ Анализ согласия LoRa / УзКорГаз</div>`;
      aRows.forEach(a => { html += `<div style="font-size:0.82rem;margin:2px 0;line-height:1.4;">• ${_escHtml(a.verdict)}</div>`; });
      html += `<table class="cd-table" style="font-size:0.78rem;width:100%;margin-top:6px;">
        <thead><tr><th style="text-align:left;">Показатель</th><th>Смещение,%</th><th>Разброс MAPE,%</th><th>Форма r</th><th>Тренд</th><th>Задержка,дн</th></tr></thead><tbody>`;
      aRows.forEach(a => {
        const tm = a.trend_match == null ? '—' : (a.trend_match ? 'совпадает' : 'расходится');
        html += `<tr><td style="text-align:left;">${_escHtml(a.label)}</td>
          <td style="text-align:right;">${fmtA(a.bias_pct, 0)}</td>
          <td style="text-align:right;">${fmtA(a.mape, 0)}</td>
          <td style="text-align:right;">${a.pearson_r == null ? '—' : fmtA(a.pearson_r, 2)}</td>
          <td style="text-align:center;">${tm}</td>
          <td style="text-align:right;">${a.best_lag_days || 0}</td></tr>`;
      });
      html += `</tbody></table>
        <div style="font-size:0.74rem;color:#6b7280;margin-top:4px;">Смещение — систематическая разница величин; MAPE — случайный разброс; r — совпадение формы; задержка — сдвиг по дням (кросс-корреляция).</div>
      </div>`;
    }

    // Сравнение характера динамики по ТРЁМ показателям (Q раб / Q общ / ΔP).
    // Каждый — несколько устойчивых мер (общий тренд + форма с учётом задержки).
    const tcList = (snap.trend_characters && snap.trend_characters.length)
      ? snap.trend_characters
      : (snap.trend_character ? [snap.trend_character] : []);
    const tcShow = on('trend_character') ? tcList.filter(t => t && (t.sensor_seq || t.verdict)) : [];
    if (tcShow.length) {
      const lvlColor = (lvl) => ({ full: '#16a34a', partial: '#d97706', none: '#dc2626' }[lvl] || '#6b7280');
      const otTxt = (t) => (t.overall_match == null) ? '—'
        : (t.overall_match ? `совпадает (оба — ${_escHtml(t.overall_sensor || '')})`
          : `расходится (LoRa — ${_escHtml(t.overall_sensor || '')}, УзКГ — ${_escHtml(t.overall_customer || '')})`);
      const shapeTxt = (t) => (t.shape_corr == null) ? '—'
        : `r=${Number(t.shape_corr).toFixed(2)}${t.best_lag ? ` (лаг ~${Math.abs(t.best_lag)} дн)` : ''}`;
      const dayTxt = (t) => (t.agreement_pct == null) ? '—'
        : `${t.agreement_pct}%${(t.agreement_lag_pct != null && t.agreement_lag_pct !== t.agreement_pct) ? ` (с лагом ${t.agreement_lag_pct}%)` : ''}`;
      let rows = '';
      tcShow.forEach(t => {
        rows += `<tr>
          <td style="text-align:left;">${_escHtml(t.label || '—')}</td>
          <td style="text-align:left;">${_escHtml(t.sensor_seq || '—')}</td>
          <td style="text-align:left;">${_escHtml(t.customer_seq || '—')}</td>
          <td style="text-align:left;color:${t.overall_match ? '#16a34a' : (t.overall_match === false ? '#dc2626' : '#6b7280')};">${otTxt(t)}</td>
          <td style="text-align:center;">${shapeTxt(t)}</td>
          <td style="text-align:center;">${dayTxt(t)}</td>
        </tr>`;
      });
      let verdicts = '';
      tcShow.forEach(t => { if (t.verdict) verdicts += `<div style="font-size:0.8rem;color:${lvlColor(t.match_level)};line-height:1.45;margin-top:2px;">• ${_escHtml(t.verdict)}</div>`; });
      html += `<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:8px 10px;margin-bottom:12px;">
        <div style="font-weight:600;font-size:0.85rem;margin-bottom:4px;">📈 Сравнение характера динамики (LoRa vs УзКорГаз)</div>
        <table class="cd-table" style="font-size:0.78rem;width:100%;margin-bottom:4px;">
          <thead><tr><th style="text-align:left;">Показатель</th><th style="text-align:left;">LoRa: фазы</th><th style="text-align:left;">УзКГ: фазы</th><th style="text-align:left;">Общий тренд</th><th>Форма</th><th>Посуточно</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        ${verdicts}
        <div style="font-size:0.73rem;color:#6b7280;margin-top:3px;line-height:1.4;">Оценка по устойчивым мерам — общий тренд и форма с учётом задержки; посуточное совпадение справочно (чувствительно к суточной дискретности сводок заказчика).</div>
      </div>`;
    }

    // Daily diff table
    if (on('show_table') && dailyDiff.length) {
      html += `<div style="margin-bottom:12px;">
        <div style="font-weight:500; font-size:0.85rem; margin-bottom:6px;">Посуточное сравнение</div>
        <div style="max-height:200px; overflow-y:auto;">
          <table class="cd-table" style="font-size:0.78rem;">
            <thead><tr>
              <th>Дата</th>
              <th>Q раб LoRa</th>
              <th>Q раб УзКГ</th>
              <th>Δ Q раб</th>
              <th>Q общ LoRa</th>
              <th>Q общ УзКГ</th>
              <th>Δ Q общ</th>
              <th>ΔP LoRa</th>
              <th>ΔP УзКГ</th>
              <th>Δ ΔP</th>
            </tr></thead>
            <tbody>`;
      dailyDiff.forEach(r => {
        const fmtDelta = (v) => {
          if (v == null) return '—';
          const sign = v >= 0 ? '+' : '';
          const color = v > 0 ? '#dc2626' : v < 0 ? '#22c55e' : '#6b7280';
          return `<span style="color:${color};">${sign}${v.toFixed(3)}</span>`;
        };
        html += `<tr>
          <td>${_escHtml(r.date || '—')}</td>
          <td style="text-align:right;">${fmt(r.sensor_q)}</td>
          <td style="text-align:right;">${fmt(r.customer_q)}</td>
          <td style="text-align:right;">${fmtDelta(r.delta_q)}</td>
          <td style="text-align:right;">${fmt(r.sensor_q_total)}</td>
          <td style="text-align:right;">${fmt(r.customer_q_total)}</td>
          <td style="text-align:right;">${fmtDelta(r.delta_q_total)}</td>
          <td style="text-align:right;">${fmt(r.sensor_dp)}</td>
          <td style="text-align:right;">${fmt(r.customer_dp)}</td>
          <td style="text-align:right;">${fmtDelta(r.delta_dp)}</td>
        </tr>`;
      });
      html += `</tbody></table></div>
        <div style="font-size:0.78rem; color:#6b7280; margin-top:4px; line-height:1.45;">
          Колонки: Q раб — рабочий дебит (без простоев), Q общ — общий дебит за сутки;
          LoRa — наши датчики, УзКГ — сводки заказчика; Δ — разница (LoRa − УзКГ);
          ΔP — перепад давления. Значения по дням за период сопоставления.
        </div></div>`;
    }

    // Conclusion
    if (on('show_conclusion') && conclusion) {
      html += `<div style="background:#f8f9fa; border:1px solid #e0e0e0; padding:10px; border-radius:4px; font-size:0.85rem;">
        <b>Заключение:</b> ${_escHtml(conclusion)}
      </div>`;
    }

    return html;
  }

  // ===== buildPressureSpectrumComparison — сравнение спектра с наложенными =====
  // ЕДИНЫЙ источник вердикта сравнения (DRY+PARITY): зовётся и live-превью
  // (_buildPressureSpectrumHtml), и виджетом — он встраивает результат в
  // snapshot.comparison, чтобы PDF-форматтер (_format_pressure_spectrum)
  // только ОТОБРАЖАЛ его, без дублирования логики. База = текущий спектр
  // (snap.signals), опора = каждый снимок из snap.overlays.
  const _PS_CLASS_RU = { stable: 'стабильно', moderate: 'умеренно', unstable: 'нестабильно', degenerate: 'вырождено', no_data: 'нет данных' };
  function _psBuildComparison(snap, chapter) {
    const overlays = (snap && snap.overlays) || [];
    if (!overlays.length || !snap.signals) return null;
    const isAdapt = (chapter || (snap && snap._chapter)) === 'adaptation';
    const SIG = [['p_tube', 'P устьевое'], ['dp', 'ΔP']];
    const num = (v) => (v == null || isNaN(v)) ? null : Number(v);
    const f = (v, d = 2) => (v == null) ? '—' : Number(v).toFixed(d);
    const items = overlays.map((ov) => {
      const signals = SIG.map(([key, label]) => {
        const b = ((snap.signals[key] || {}).metrics) || {};
        const o = (((ov.signals || {})[key] || {}).metrics) || {};
        const bMed = num(b.median), oMed = num(o.median);
        const bNiqr = num(b.niqr), oNiqr = num(o.niqr);
        const bClass = (snap.signals[key] || {}).stability_class;
        const oClass = ((ov.signals || {})[key] || {}).stability_class;
        let verdict = '';
        if (bMed != null && oMed != null) {
          const dMed = bMed - oMed;
          const dNiqr = (bNiqr != null && oNiqr != null) ? bNiqr - oNiqr : null;
          const parts = [];
          // ── Смещение медианы (вправо/вверх = рост — положительно) ──
          const dTxt = `медиана ${f(oMed)}→${f(bMed)} (${dMed >= 0 ? '+' : ''}${f(dMed)})`;
          if (key === 'dp') {
            if (dMed > 0) parts.push(`${dTxt}: перепад ΔP вырос → увеличение дебита (положительно)`);
            else if (dMed < 0) parts.push(`${dTxt}: перепад ΔP снизился → снижение дебита`);
            else parts.push(`${dTxt}: перепад без изменений`);
          } else {
            if (dMed > 0) parts.push(`${dTxt}: рост медианного устьевого давления (положительно)`);
            else if (dMed < 0) parts.push(`${dTxt}: снижение медианного устьевого давления`);
            else parts.push(`${dTxt}: медианное давление без изменений`);
          }
          // ── Ширина спектра. Сужение — однозначно улучшение. Расширение —
          //    НЕ интерпретируем однозначно (особенно на адаптации: переходные
          //    процессы, реакция на вброс реагента). ──
          if (dNiqr != null) {
            const rel = oNiqr > 0 ? dNiqr / oNiqr : 0;
            const nTxt = `nIQR ${f(oNiqr, 3)}→${f(bNiqr, 3)}`;
            if (rel < -0.05) {
              parts.push(`${nTxt}: спектр сузился — улучшение, более стабильная работа`);
            } else if (rel > 0.05) {
              parts.push(isAdapt
                ? `${nTxt}: спектр расширился — на этапе адаптации это может быть переходным процессом (выход на режим) или реакцией на вброс реагента, не обязательно ухудшение`
                : `${nTxt}: спектр расширился — возможно менее стабильная работа (оценивать с учётом режима и продувок/вбросов)`);
            } else {
              parts.push(`${nTxt}: ширина спектра почти не изменилась`);
            }
          }
          if (bClass && oClass && bClass !== oClass) {
            parts.push(`класс стабильности: ${_PS_CLASS_RU[oClass] || oClass}→${_PS_CLASS_RU[bClass] || bClass}`);
          }
          verdict = label + ': ' + parts.join('; ') + '.';
        }
        return { key, label, base_median: bMed, ov_median: oMed,
          base_niqr: bNiqr, ov_niqr: oNiqr, base_class: bClass, ov_class: oClass, verdict };
      });
      return { label: ov.label || '', period: ov.period || {}, signals };
    });
    return { base_period: snap.period || {}, items };
  }
  window.buildPressureSpectrumComparison = _psBuildComparison;

  // ===== _buildPressureSpectrumHtml — спектр распределения давления (стабильность) =====
  // PARITY: при изменении таблицы/секций синхронно править
  // _format_pressure_spectrum (adaptation_report_service.py) и .tex-ветку.
  function _buildPressureSpectrumHtml(snap, block, idPrefix) {
    if (!snap || !snap.signals) return '<div style="color:#9ca3af;">Снапшот спектра не загружен.</div>';
    const parts = (block.params && block.params.parts) || {};
    const on = (k) => parts[k] !== false;
    const fmt = (v, d = 2) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d);
    const period = snap.period || {};
    const sigs = snap.signals || {};

    const CLASS_LABELS = { stable: 'стабильно', moderate: 'умеренно', unstable: 'нестабильно', degenerate: 'вырождено', no_data: 'нет данных' };
    const CLASS_COLORS = { stable: '#16a34a', moderate: '#d97706', unstable: '#dc2626', degenerate: '#6b7280', no_data: '#6b7280' };
    const NORM_LABELS = { normal: 'нормальное', near_normal: 'близко к норм.', non_normal: 'не нормальное' };

    let html = '';
    html += `<div style="font-size:0.9rem; color:#374151; margin-bottom:8px;">
      <b>Период:</b> ${_escHtml(period.from || '—')} — ${_escHtml(period.to || '—')}${snap.well_number ? `, скв. ${_escHtml(snap.well_number)}` : ''}
      &nbsp;·&nbsp; бины: P ${fmt(snap.bin_width_pressure, 2)} / ΔP ${fmt(snap.bin_width_dp, 2)} кгс/см²
    </div>`;
    // Аналитический вводный абзац (паритет с .tex §3.4.N pressure_spectrum).
    html += `<div style="font-size:0.85rem; color:#374151; margin-bottom:8px;">
      Для оценки устойчивости режима за период построены спектры распределения устьевого
      давления и перепада ΔP. На графиках ниже показано, как часто встречаются те или иные
      уровни давления: узкий выраженный пик отвечает устойчивому режиму, широкое
      распределение — повышенной изменчивости работы скважины.
    </div>`;
    if (snap.block_status && snap.block_status !== 'ok') {
      html += `<div style="background:#fef3c7;border-left:3px solid #f59e0b;padding:6px 10px;font-size:0.82rem;margin-bottom:8px;">⚠ Статус данных: ${_escHtml(snap.block_status)}.</div>`;
    }
    if (on('show_ptube_chart') || on('show_dp_chart')) {
      html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px;">`;
      if (on('show_ptube_chart')) html += `<div><div style="font-weight:500;font-size:0.85rem;margin-bottom:4px;">Устьевое давление P_уст</div><div id="${idPrefix}chart-ptube" style="height:270px;"></div></div>`;
      if (on('show_dp_chart')) html += `<div><div style="font-weight:500;font-size:0.85rem;margin-bottom:4px;">Перепад ΔP</div><div id="${idPrefix}chart-dp" style="height:270px;"></div></div>`;
      html += `</div>`;
    }
    if (on('show_metrics_table')) {
      const mA = (sigs.p_tube && sigs.p_tube.metrics) || {};
      const mB = (sigs.dp && sigs.dp.metrics) || {};
      const row = (label, key, d = 2) => `<tr><td style="text-align:left;">${label}</td><td style="text-align:right;">${fmt(mA[key], d)}</td><td style="text-align:right;">${fmt(mB[key], d)}</td></tr>`;
      const pc = (sigs.p_tube || {}).stability_class || 'no_data';
      const dc = (sigs.dp || {}).stability_class || 'no_data';
      html += `<table class="cd-table" style="font-size:0.8rem;width:100%;margin-bottom:8px;">
        <thead><tr><th style="text-align:left;">Метрика</th><th style="text-align:right;">P устьевое</th><th style="text-align:right;">ΔP</th></tr></thead><tbody>
        ${row('Медиана, кгс/см²', 'median')}
        ${row('IQR (P25–P75)', 'iqr')}
        ${row('nIQR = IQR/медиана', 'niqr', 3)}
        ${row('σ (std)', 'std')}
        ${row('CV, %', 'cv_median', 1)}
        ${row('Размах P10–P90', 'w90')}
        ${row('Асимметрия', 'skewness')}
        ${row('Эксцесс', 'kurtosis')}
        <tr><td style="text-align:left;">Распределение</td><td style="text-align:right;">${NORM_LABELS[mA.normality] || '—'}</td><td style="text-align:right;">${NORM_LABELS[mB.normality] || '—'}</td></tr>
        <tr><td style="text-align:left;">Стабильность</td>
          <td style="text-align:right;color:${CLASS_COLORS[pc]};font-weight:600;">${CLASS_LABELS[pc] || pc}</td>
          <td style="text-align:right;color:${CLASS_COLORS[dc]};font-weight:600;">${CLASS_LABELS[dc] || dc}</td></tr>
        </tbody></table>`;
      const fl = snap.flags || {};
      const warns = [];
      if (fl.high_variability) warns.push('повышенная вариативность (CV&gt;15%)');
      if (fl.outliers_present) warns.push('присутствуют выбросы');
      if (fl.short_period) warns.push('короткий период (&lt;2 сут)');
      if (warns.length) html += `<div style="font-size:0.78rem;color:#92400e;margin-bottom:8px;">⚠ ${warns.join('; ')}.</div>`;
    }
    // Авто-описание полученных данных (по P_уст и ΔP)
    if (on('show_metrics_table')) {
      const dp_ = (sigs.p_tube || {}).description, dd_ = (sigs.dp || {}).description;
      if (dp_ || dd_) {
        html += `<div style="font-size:0.82rem;color:#374151;line-height:1.5;margin-bottom:8px;">
          <b>Описание данных:</b>
          ${dp_ ? `<div style="margin-top:3px;">• ${_escHtml(dp_)}</div>` : ''}
          ${dd_ ? `<div style="margin-top:3px;">• ${_escHtml(dd_)}</div>` : ''}
        </div>`;
      }
    }
    // ── Сравнение с наложенными спектрами (если выбраны) ──
    // Свой период — это ДВА графика выше (раздельно). Наложение — отдельные
    // графики НИЖЕ (ps-…-chart-*-cmp) + таблица и вердикты.
    if (on('show_comparison')) {
      const _chapter = (block.params && block.params.chapter) || '';
      const cmp = (window.buildPressureSpectrumComparison) ? window.buildPressureSpectrumComparison(snap, _chapter) : null;
      if (cmp && cmp.items.length) {
        html += `<div style="margin:10px 0;">
          <div style="font-weight:600;font-size:0.86rem;margin-bottom:5px;">🔀 Сравнение спектров (наложение)</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px;">
            <div><div style="font-weight:500;font-size:0.82rem;margin-bottom:4px;">P устьевое — наложение</div><div id="${idPrefix}chart-ptube-cmp" style="height:270px;"></div></div>
            <div><div style="font-weight:500;font-size:0.82rem;margin-bottom:4px;">ΔP — наложение</div><div id="${idPrefix}chart-dp-cmp" style="height:270px;"></div></div>
          </div>`;
        cmp.items.forEach((it) => {
          html += `<div style="font-size:0.82rem;margin-bottom:8px;">
            <div style="color:#475569;margin-bottom:2px;">Относительно «${_escHtml(it.label)}»:</div>
            <table class="cd-table" style="font-size:0.78rem;width:100%;margin:3px 0;">
              <thead><tr><th style="text-align:left;">Сигнал</th><th>Медиана (опорн.→текущ.)</th><th>nIQR (опорн.→текущ.)</th><th>Стабильность</th></tr></thead><tbody>`;
          it.signals.forEach((s) => {
            const oc = CLASS_LABELS[s.ov_class] || s.ov_class || '—';
            const bc = CLASS_LABELS[s.base_class] || s.base_class || '—';
            html += `<tr><td style="text-align:left;">${s.label}</td>
              <td style="text-align:center;">${fmt(s.ov_median)} → ${fmt(s.base_median)}</td>
              <td style="text-align:center;">${fmt(s.ov_niqr, 3)} → ${fmt(s.base_niqr, 3)}</td>
              <td style="text-align:center;">${oc} → ${bc}</td></tr>`;
          });
          html += `</tbody></table>`;
          it.signals.forEach((s) => { if (s.verdict) html += `<div style="margin-top:2px;color:#374151;">• ${_escHtml(s.verdict)}</div>`; });
          html += `</div>`;
        });
        html += `</div>`;
      }
    }
    if (on('show_method_note')) {
      html += `<details style="font-size:0.8rem;color:#475569;margin-bottom:6px;">
        <summary style="cursor:pointer;color:#2563eb;">📖 Описание метода</summary>
        <div style="margin-top:6px;line-height:1.45;">
        <b>Спектр распределения давления</b> показывает стабильность работы скважины — не среднее давление, а то, как часто давление отклоняется от рабочего. Гистограмма: по горизонтали давление, по вертикали — число минут с этим давлением. Узкий острый пик = стабильный режим; растянутое плоское распределение = нестабильный. Цвет графика отражает оценку: зелёный — стабильно, оранжевый — умеренно, красный — нестабильно.
        <br><br>Отслеживаемые параметры (для оценки изменения режима):
        <ul style="margin:4px 0 4px 18px;padding:0;">
          <li><b>Ширина спектра</b> — основной показатель <b>nIQR</b> (межквартильный размах ÷ медиана): чем шире, тем хуже стабильность. Заметные выбросы и высокая вариативность (CV) дополнительно ухудшают оценку, даже если ядро узкое.</li>
          <li><b>Нормальность</b> — форма распределения. Для скважины идеальная нормальность почти недостижима, поэтому «близко к нормальному» считается нормой и серьёзно не штрафуется.</li>
          <li><b>Медиана</b> — положение рабочей точки. Для ΔP: чем больше медиана по абсолютному значению, тем выше дебит. Сдвиг медианы между этапами = изменение режима.</li>
        </ul>
        При сравнении этапов важно: сузился ли спектр (стало стабильнее), сместилась ли медиана (особенно ΔP — индикатор дебита), изменилась ли форма.
        </div></details>`;
    }
    const comment = block.comment || (block.params && block.params.comment) || '';
    if (on('description') && comment) {
      html += `<div style="background:#f1f5f9;border-left:2px solid #94a3b8;padding:5px 8px;font-style:italic;font-size:0.85rem;">${_escHtml(comment)}</div>`;
    }
    return html;
  }

  // ===== _renderPressureSpectrumCharts — Plotly-гистограммы P_уст и ΔP =====
  function _renderPressureSpectrumCharts(snap, idPrefix) {
    if (!window.Plotly || !snap || !snap.signals) return;
    const sigs = snap.signals;
    // Наложенные спектры (сравнение этапов). Свой период рисуется БЕЗ наложений
    // (raw «мин»), а наложение — на отдельных графиках …-cmp, где Y нормируется
    // в «% времени» (формы периодов разной длины сопоставимы) + легенда.
    const overlays = ((snap.overlays || []).filter((o) => o && o.signals));
    const hasOverlays = overlays.length > 0;
    // Настройки отображения (регулируются в модалке, хранятся в snapshot.display)
    const disp = snap.display || {};
    const ctype = disp.chart_type || 'stairs';   // 'stairs' | 'bars' | 'lines' | 'stems'
    const colorAuto = disp.color_auto !== false;  // по умолчанию — цвет по стабильности
    const customColor = disp.color || '#2563eb';
    const layout = {
      height: 270, bargap: 0.04,
      plot_bgcolor: '#fff', paper_bgcolor: '#fff', font: { family: 'Arial, sans-serif', size: 11 },
    };
    const cfg = { responsive: true, displayModeBar: false };
    const gridStyle = { gridcolor: '#eef1f4', zeroline: false, linecolor: '#cbd5e1', ticks: 'outside', tickcolor: '#cbd5e1' };

    const STAB = {
      stable:     { line: '#16a34a', fill: 'rgba(22,163,74,0.18)' },
      moderate:   { line: '#d97706', fill: 'rgba(217,119,6,0.18)' },
      unstable:   { line: '#dc2626', fill: 'rgba(220,38,38,0.18)' },
      degenerate: { line: '#6b7280', fill: 'rgba(107,114,128,0.18)' },
      no_data:    { line: '#9ca3af', fill: 'rgba(156,163,175,0.18)' },
    };
    const OVERLAY_PALETTE = ['#7c3aed', '#0891b2', '#db2777', '#65a30d', '#ea580c'];
    function hexToRgba(hex, a) {
      const h = (hex || '').replace('#', '');
      if (h.length < 6) return 'rgba(37,99,235,' + a + ')';
      const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
      return `rgba(${r},${g},${b},${a})`;
    }
    function colorFor(sig) {
      if (colorAuto) { const c = STAB[sig.stability_class] || STAB.no_data; return c; }
      return { line: customColor, fill: hexToRgba(customColor, 0.18) };
    }
    // Адаптивная шкала X: робастное окно median ± 4·IQR, зажатое в [min, max].
    function xRange(sig) {
      const m = sig.metrics || {};
      if (m.median == null || m.iqr == null) return null;
      const K = 4;
      const dmin = (m.min != null) ? m.min : 0;
      const dmax = (m.max != null) ? m.max : (m.median + 1);
      let lo = Math.max(dmin, m.median - K * m.iqr);
      let hi = Math.min(dmax, m.median + K * m.iqr);
      if (!(hi > lo)) { lo = dmin; hi = dmax; }
      const pad = Math.max(hi - lo, 1e-6) * 0.1;
      return [Math.max(0, lo - pad), hi + pad];
    }
    // Трейсы одного сигнала с заданным стилем. В norm-режиме counts → % времени.
    function mkTraces(sig, colObj, name, dash, opacity, norm) {
      const edges = sig.bin_edges, counts = sig.counts || [];
      if (!edges || edges.length < 2) return [];
      const total = counts.reduce((a, b) => a + (b || 0), 0) || 1;
      const k = norm ? (100 / total) : 1;
      const Y = counts.map((c) => (c || 0) * k);
      const line = colObj.line, fill = colObj.fill;
      if (ctype === 'bars') {
        const centers = [], widths = [];
        for (let i = 0; i < Y.length; i++) { centers.push((edges[i] + edges[i + 1]) / 2); widths.push(edges[i + 1] - edges[i]); }
        return [{ x: centers, y: Y, width: widths, type: 'bar', marker: { color: line, line: { width: 0 } }, opacity: opacity, name: name }];
      } else if (ctype === 'lines') {
        const centers = [];
        for (let i = 0; i < Y.length; i++) centers.push((edges[i] + edges[i + 1]) / 2);
        return [{ x: centers, y: Y, type: 'scatter', mode: 'lines', line: { color: line, width: 1.8, shape: 'spline', dash: dash }, fill: 'tozeroy', fillcolor: fill, name: name, opacity: opacity }];
      } else if (ctype === 'stems') {
        const lx = [], ly = [], cx = [], cy = [];
        for (let i = 0; i < Y.length; i++) {
          const c = (edges[i] + edges[i + 1]) / 2;
          cx.push(c); cy.push(Y[i]);
          lx.push(c, c, null); ly.push(0, Y[i], null);
        }
        return [
          { x: lx, y: ly, type: 'scatter', mode: 'lines', line: { color: line, width: 1.3, dash: 'dash' }, hoverinfo: 'skip', name: name, showlegend: false },
          { x: cx, y: cy, type: 'scatter', mode: 'markers', marker: { color: line, size: 6, symbol: 'triangle-up' }, name: name, opacity: opacity },
        ];
      }
      // stairs (лесенка) — по умолчанию
      const xs = [], ys = [];
      for (let i = 0; i < Y.length; i++) { xs.push(edges[i]); ys.push(Y[i]); xs.push(edges[i + 1]); ys.push(Y[i]); }
      return [{ x: xs, y: ys, type: 'scatter', mode: 'lines', line: { color: line, width: 1.6, shape: 'linear', dash: dash }, fill: 'tozeroy', fillcolor: fill, name: name, opacity: opacity }];
    }
    function gaussianTrace(sig, norm) {
      const showNormal = !!disp.show_normal && !norm;  // гауссиану прячем при наложении
      const m = sig.metrics || {};
      if (!(showNormal && m.std > 0 && sig.n_points > 0)) return null;
      const edges = sig.bin_edges;
      const mu = m.mean, sd = m.std, N = sig.n_points, bw = sig.bin_width || (edges[1] - edges[0]);
      const x0 = edges[0], x1 = edges[edges.length - 1], steps = 120, nx = [], ny = [];
      const k = norm ? (bw * 100) : (N * bw);
      for (let i = 0; i <= steps; i++) {
        const x = x0 + (x1 - x0) * i / steps;
        const pdf = Math.exp(-((x - mu) * (x - mu)) / (2 * sd * sd)) / (sd * Math.sqrt(2 * Math.PI));
        nx.push(x); ny.push(pdf * k);
      }
      return { x: nx, y: ny, type: 'scatter', mode: 'lines', line: { color: '#7c3aed', width: 1.8 }, name: 'Нормальное', hoverinfo: 'skip' };
    }
    // withOverlays=false → свой период раздельно (raw «мин»); true → наложение
    // (норм. «% времени» + легенда), рисуется в отдельные …-cmp графики.
    function draw(elId, baseSig, key, title, withOverlays) {
      const el = document.getElementById(elId);
      if (!el || !baseSig || !baseSig.bin_edges || baseSig.bin_edges.length < 2) return;
      const norm = !!withOverlays && hasOverlays;
      const per = snap.period || {};
      const baseName = norm ? ('Текущий (' + (per.from || '') + '—' + (per.to || '') + ')') : title;
      let traces = mkTraces(baseSig, colorFor(baseSig), baseName, 'solid', 0.82, norm);
      const gt = gaussianTrace(baseSig, norm); if (gt) traces.push(gt);
      const shapes = [];
      const bm = baseSig.metrics || {};
      if (bm.median != null) shapes.push({ type: 'line', xref: 'x', yref: 'paper', x0: bm.median, x1: bm.median, y0: 0, y1: 1, line: { color: '#111827', width: 1.5, dash: 'dash' } });
      let xr = xRange(baseSig);
      if (norm) {
        overlays.forEach((ov, idx) => {
          const osig = (ov.signals || {})[key];
          if (!osig || !osig.bin_edges || osig.bin_edges.length < 2) return;
          const pc = OVERLAY_PALETTE[idx % OVERLAY_PALETTE.length];
          traces = traces.concat(mkTraces(osig, { line: pc, fill: hexToRgba(pc, 0.08) }, ov.label || ('Спектр ' + (idx + 1)), 'dot', 0.55, norm));
          const om = osig.metrics || {};
          if (om.median != null) shapes.push({ type: 'line', xref: 'x', yref: 'paper', x0: om.median, x1: om.median, y0: 0, y1: 1, line: { color: pc, width: 1, dash: 'dot' } });
          const r = xRange(osig);
          if (xr && r) xr = [Math.min(xr[0], r[0]), Math.max(xr[1], r[1])];
        });
      }
      const xaxis = Object.assign({ title: { text: 'кгс/см²', font: { size: 9 } } }, gridStyle);
      if (xr) { xaxis.range = xr; } else { xaxis.rangemode = 'tozero'; }
      try {
        Plotly.newPlot(el, traces, {
          ...layout, margin: { l: 48, r: 16, t: norm ? 40 : 26, b: 38 },
          title: { text: title, font: { size: 11 } },
          showlegend: norm,
          legend: norm ? { orientation: 'h', y: 1.16, x: 0, font: { size: 9 } } : undefined,
          xaxis: xaxis,
          yaxis: Object.assign({ title: { text: norm ? '% времени' : 'мин', font: { size: 9 } }, rangemode: 'tozero' }, gridStyle), shapes,
        }, cfg);
      } catch (e) { console.warn('ps-chart', e); }
    }
    // Свой период — два раздельных графика (без наложения).
    draw(idPrefix + 'chart-ptube', sigs.p_tube, 'p_tube', 'P устьевое', false);
    draw(idPrefix + 'chart-dp', sigs.dp, 'dp', 'ΔP', false);
    // Наложение — отдельные графики ниже (если выбраны спектры для сравнения).
    if (hasOverlays) {
      draw(idPrefix + 'chart-ptube-cmp', sigs.p_tube, 'p_tube', 'P устьевое — наложение', true);
      draw(idPrefix + 'chart-dp-cmp', sigs.dp, 'dp', 'ΔP — наложение', true);
    }
  }

  // ===== _buildParamCorrelationHtml — зависимость двух параметров (scatter+регрессия) =====
  // PARITY: при изменении синхронно править _format_param_correlation + .tex-ветку.
  function _buildParamCorrelationHtml(snap, block, idPrefix) {
    if (!snap || !snap.points) return '<div style="color:#9ca3af;">Снапшот зависимости не загружен.</div>';
    const parts = (block.params && block.params.parts) || {};
    const on = (k) => parts[k] !== false;
    const fmt = (v, d = 3) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d);
    const period = snap.period || {};
    const xs = snap.x || {}, ys = snap.y || {};
    const r = snap.regression || {};

    let html = '';
    html += `<div style="font-size:0.9rem; color:#374151; margin-bottom:8px;">
      <b>Период:</b> ${_escHtml(period.from || '—')} — ${_escHtml(period.to || '—')}${snap.well_number ? `, скв. ${_escHtml(snap.well_number)}` : ''}
      &nbsp;·&nbsp; <b>${_escHtml(xs.label || 'X')}</b> → <b>${_escHtml(ys.label || 'Y')}</b>, точек: ${snap.n_points || 0}
    </div>`;
    if (snap.warning) {
      html += `<div style="background:#fee2e2;border-left:3px solid #dc2626;padding:6px 10px;font-size:0.82rem;margin-bottom:8px;color:#991b1b;">⚠ ${_escHtml(snap.warning)}</div>`;
    }
    if (snap.block_status && snap.block_status !== 'ok') {
      html += `<div style="background:#fef3c7;border-left:3px solid #f59e0b;padding:6px 10px;font-size:0.82rem;margin-bottom:8px;">⚠ Статус данных: ${_escHtml(snap.block_status)}.</div>`;
    }
    if (on('show_chart')) {
      html += `<div id="${idPrefix}chart" style="height:300px; margin-bottom:8px;"></div>`;
    }
    if (on('show_stats') && r && r.slope != null) {
      const trend = r.slope > 0 ? 'прямая (рост Y с ростом X)' : (r.slope < 0 ? 'обратная (падение Y с ростом X)' : 'нет');
      const strength = r.r2 >= 0.7 ? 'сильная' : (r.r2 >= 0.4 ? 'умеренная' : 'слабая');
      html += `<table class="cd-table" style="font-size:0.8rem;width:100%;margin-bottom:8px;">
        <tbody>
        <tr><td style="text-align:left;">Наклон (slope), ${_escHtml(ys.unit || '')}/${_escHtml(xs.unit || '')}</td><td style="text-align:right;">${fmt(r.slope, 4)}</td></tr>
        <tr><td style="text-align:left;">Свободный член (intercept)</td><td style="text-align:right;">${fmt(r.intercept, 3)}</td></tr>
        <tr><td style="text-align:left;">R² (сила связи)</td><td style="text-align:right;font-weight:600;">${fmt(r.r2, 3)} — ${strength}</td></tr>
        <tr><td style="text-align:left;">Зависимость</td><td style="text-align:right;">${trend}</td></tr>
        </tbody></table>`;
    }
    if (on('show_method_note')) {
      html += `<details style="font-size:0.8rem;color:#475569;margin-bottom:6px;">
        <summary style="cursor:pointer;color:#2563eb;">📖 Описание метода</summary>
        <div style="margin-top:6px;line-height:1.45;">
        График показывает <b>зависимость двух параметров</b>: каждая точка — минутный замер (${_escHtml(xs.label || 'X')} по горизонтали, ${_escHtml(ys.label || 'Y')} по вертикали). Линия — регрессия методом наименьших квадратов; <b>наклон</b> показывает, насколько Y растёт с ростом X, а <b>R²</b> — насколько тесно они связаны (1 — идеально, 0 — нет связи). Для ΔP→Q положительный наклон подтверждает: больше перепад — выше дебит. При сравнении этапов сдвиг линии вверх/круче = улучшение режима.
        </div></details>`;
    }
    const comment = block.comment || (block.params && block.params.comment) || '';
    if (on('description') && comment) {
      html += `<div style="background:#f1f5f9;border-left:2px solid #94a3b8;padding:5px 8px;font-style:italic;font-size:0.85rem;">${_escHtml(comment)}</div>`;
    }
    return html;
  }

  // ===== _renderParamCorrelationCharts — Plotly scatter + линия регрессии =====
  function _renderParamCorrelationCharts(snap, idPrefix) {
    if (!window.Plotly || !snap || !snap.points) return;
    const el = document.getElementById(idPrefix + 'chart');
    if (!el) return;
    const px = snap.points.x || [], py = snap.points.y || [];
    const xs = snap.x || {}, ys = snap.y || {};
    const r = snap.regression || {};
    const traces = [{
      x: px, y: py, type: 'scatter', mode: 'markers', name: 'точки',
      marker: { color: 'rgba(37,99,235,0.45)', size: 4 },
    }];
    if (r && r.slope != null && r.x_min != null && r.x_max != null) {
      const x0 = r.x_min, x1 = r.x_max;
      traces.push({
        x: [x0, x1], y: [r.slope * x0 + r.intercept, r.slope * x1 + r.intercept],
        type: 'scatter', mode: 'lines', name: 'регрессия',
        line: { color: '#dc2626', width: 2 },
      });
    }
    const layout = {
      height: 290, margin: { l: 50, r: 15, t: 24, b: 40 }, showlegend: false,
      title: { text: `${xs.label || 'X'} → ${ys.label || 'Y'}`, font: { size: 11 } },
      xaxis: { title: { text: (xs.label || 'X') + (xs.unit ? ', ' + xs.unit : ''), font: { size: 9 } } },
      yaxis: { title: { text: (ys.label || 'Y') + (ys.unit ? ', ' + ys.unit : ''), font: { size: 9 } } },
    };
    try { Plotly.newPlot(el, traces, layout, { responsive: true, displayModeBar: false }); }
    catch (e) { console.warn('pc-chart', e); }
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
      // Аналитический вводный абзац (паритет с .tex §3.4.N интро).
      html += `<div style="font-size:0.85rem; color:#374151; margin-bottom:8px;">
        За указанный период по минутным данным датчиков давления (UniTool) выполнен
        анализ фактического режима работы скважины. Ниже приведены ключевые показатели
        режима, их сводные таблицы и графики динамики во времени.
      </div>`;
    }

    // ─── Metrics table ───
    // Поддержка трёх форматов:
    // 1) wz3obs_v1 (плоский): q_total_avg, q_total_median, dp_avg, dp_median, p_wellhead_median, p_flowline_median
    // 2) observation_v1.0 (вложенный): metrics.q.mean, metrics.q.median, ...
    // 3) obs_baseline_v1 (вложенный): metrics.q.mean, metrics.p_tube.median, + metrics.downtime
    // Метрики объявлены на уровне функции (а не внутри if metrics_table) —
    // их использует и таблица, и блок плиток metrics_tiles. Иначе ReferenceError
    // (q_avg is not defined) при включённых плитках без таблицы.
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
    if (on('metrics_table')) {
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
          // observation_analysis: полная таблица метрик с трендом и КИВ
          const q_trend = snap.q_trend_line || snap.q_trend;
          const trendLabel = (t) => {
            if (!t || t.slope == null) return '—';
            const sign = t.slope >= 0 ? '↑' : '↓';
            return `<span style="color:${t.slope >= 0 ? '#16a34a' : '#dc2626'};">${sign} ${Math.abs(t.slope).toFixed(3)}</span>`;
          };
          const utilPct = snap.utilization_pct;
          const purgeCount = snap.purge_count ?? 0;
          const days = snap.days || 0;

          // Лид-ин перед таблицей (паритет с .tex «В таблице сведены…»).
          html += `<div style="font-size:0.82rem; color:#374151; margin-bottom:6px;">
            В таблице сведены средние и медианные значения ключевых параметров режима за
            период — расчётного дебита, перепада давления, устьевого и линейного давления;
            ниже — показатели использования рабочего времени и число технологических событий.
          </div>`;
          html += `<div style="margin-bottom:12px;">
            <table style="width:100%; font-size:0.82rem; border-collapse:collapse;">
              <thead><tr style="background:linear-gradient(to right, #fef9c3, #fef3c7);">
                <th style="text-align:left; padding:6px 10px;">Показатель</th>
                <th style="text-align:right; padding:6px 10px;">Среднее</th>
                <th style="text-align:right; padding:6px 10px;">Медиана</th>
                <th style="text-align:right; padding:6px 10px;">Ед.</th>
                <th style="text-align:right; padding:6px 10px;">Тренд /сут</th>
              </tr></thead>
              <tbody>
                <tr style="border-bottom:1px solid #e5e7eb;">
                  <td style="padding:5px 10px;">Q фактический</td>
                  <td style="text-align:right; padding:5px 10px; font-weight:600; color:#1d4ed8;">${fmt(q_avg)}</td>
                  <td style="text-align:right; padding:5px 10px; font-weight:600; color:#1d4ed8;">${fmt(q_med)}</td>
                  <td style="text-align:right; padding:5px 10px; color:#6b7280;">тыс.м³/сут</td>
                  <td style="text-align:right; padding:5px 10px;">${trendLabel(q_trend)}</td>
                </tr>
                <tr style="border-bottom:1px solid #e5e7eb;">
                  <td style="padding:5px 10px;">P трубное</td>
                  <td style="text-align:right; padding:5px 10px; font-weight:600;">${fmt(p_tube_med)}</td>
                  <td style="text-align:right; padding:5px 10px; font-weight:600;">${fmt(p_tube_med)}</td>
                  <td style="text-align:right; padding:5px 10px; color:#6b7280;">кгс/см²</td>
                  <td style="text-align:right; padding:5px 10px;">—</td>
                </tr>
                <tr style="border-bottom:1px solid #e5e7eb;">
                  <td style="padding:5px 10px;">P линейное</td>
                  <td style="text-align:right; padding:5px 10px; font-weight:600;">${fmt(p_line_med)}</td>
                  <td style="text-align:right; padding:5px 10px; font-weight:600;">${fmt(p_line_med)}</td>
                  <td style="text-align:right; padding:5px 10px; color:#6b7280;">кгс/см²</td>
                  <td style="text-align:right; padding:5px 10px;">—</td>
                </tr>
                <tr>
                  <td style="padding:5px 10px;">ΔP</td>
                  <td style="text-align:right; padding:5px 10px; font-weight:600;">${fmt(dp_avg)}</td>
                  <td style="text-align:right; padding:5px 10px; font-weight:600;">${fmt(dp_med)}</td>
                  <td style="text-align:right; padding:5px 10px; color:#6b7280;">кгс/см²</td>
                  <td style="text-align:right; padding:5px 10px;">—</td>
                </tr>
              </tbody>
            </table>
            <div style="display:flex; flex-wrap:wrap; gap:12px 24px; margin-top:8px; padding:8px 10px;
                        background:#fefce8; border-radius:4px; font-size:0.8rem;">
              ${utilPct != null ? `<span>КИВ: <b style="color:#16a34a;">${fmt(utilPct, 2)}%</b></span>` : ''}
              ${working_hours != null ? `<span>Рабочих часов: <b>${fmt(working_hours, 2)}</b></span>` : ''}
              ${shutdown_min != null ? `<span>Простой: <b>${fmt(shutdown_min / 60, 2)}</b> ч</span>` : ''}
              <span>Продувок: <b>${purgeCount}</b></span>
              ${days ? `<span>Длительность: <b>${days} сут.</b></span>` : ''}
            </div>
          </div>`;
        }
      }
    }

    // ─── Metrics tiles (альтернатива таблице) ───
    if (kind === 'observation_analysis' && on('metrics_tiles')) {
      const tiles = [
        { label: 'Q факт.', value: q_avg, unit: 'тыс.м³/сут', color: '#1d4ed8', bg: '#eff6ff' },
        { label: 'Q медиана', value: q_med, unit: 'тыс.м³/сут', color: '#1d4ed8', bg: '#eff6ff' },
        { label: 'ΔP среднее', value: dp_avg, unit: 'кгс/см²', color: '#ea580c', bg: '#fff7ed' },
        { label: 'ΔP медиана', value: dp_med, unit: 'кгс/см²', color: '#ea580c', bg: '#fff7ed' },
        { label: 'P устье', value: p_tube_med, unit: 'кгс/см²', color: '#059669', bg: '#ecfdf5' },
        { label: 'P шлейф', value: p_line_med, unit: 'кгс/см²', color: '#059669', bg: '#ecfdf5' },
        { label: 'КИВ', value: snap.utilization_pct, unit: '%', color: '#7c3aed', bg: '#f5f3ff' },
        { label: 'Раб. часов', value: snap.working_hours, unit: 'ч', color: '#0891b2', bg: '#ecfeff' },
      ];
      html += `<div style="display:grid; grid-template-columns:repeat(4, 1fr); gap:8px; margin:12px 0;">`;
      tiles.forEach(t => {
        if (t.value == null) return;
        html += `<div style="padding:10px; background:${t.bg}; border-radius:6px; border-left:3px solid ${t.color}; text-align:center;">
          <div style="font-size:0.72rem; color:#6b7280; margin-bottom:2px;">${_escHtml(t.label)}</div>
          <div style="font-size:1.1rem; font-weight:700; color:${t.color};">${fmt(t.value, 2)}</div>
          <div style="font-size:0.68rem; color:#9ca3af;">${_escHtml(t.unit)}</div>
        </div>`;
      });
      html += `</div>`;
    }

    // ─── Charts (контейнеры для Plotly) ───
    // observation_analysis: 4 отдельных графика
    if (kind === 'observation_analysis') {
      const useGrid = on('charts_grid');
      // Подпись графика (стиль Адаптации: серый, мелкий, под рисунком)
      const _cap = (txt) => `<div style="font-size:0.78rem; color:#6b7280; margin-top:4px; margin-bottom:8px; padding:0 4px; line-height:1.45;">${txt}</div>`;
      // Вводное пояснение «что изображено» (стиль Адаптации)
      html += `<div style="font-size:0.84rem; color:#4b5563; line-height:1.5; margin:8px 0 8px;">
        Ниже — динамика ключевых параметров за период блока по данным датчиков давления
        (минутные ряды, тот же конвейер, что страница скважины). Розовые зоны на графиках —
        интервалы простоя; зелёные маркеры — вбросы реагента.
      </div>`;
      if (useGrid) {
        // 2×2 сетка графиков
        html += `<div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin:8px 0;">`;
      }
      if (on('chart_pressures')) {
        const h = useGrid ? '200px' : '240px';
        html += `<div id="prev-${bid}-obs-pres" style="height:${h};
                      background:#fafafa; border-radius:4px;"></div>`;
        if (!useGrid) html += _cap(`<b>Рис.</b> Устьевое (P трубное, синий) и линейное (P шлейф, красный) давление, кгс/см². Разница между кривыми — перепад ΔP.`);
      }
      if (on('chart_dp')) {
        const h = useGrid ? '200px' : '240px';
        html += `<div id="prev-${bid}-obs-dp" style="height:${h};
                      background:#fafafa; border-radius:4px;"></div>`;
        if (!useGrid) html += _cap(`<b>Рис.</b> Перепад давления ΔP = max(0, P трубное − P линейное), кгс/см². Рост ΔP — увеличение дебита; падение — возможное накопление жидкости.`);
      }
      if (on('chart_q')) {
        const h = useGrid ? '200px' : '240px';
        html += `<div id="prev-${bid}-obs-q" style="height:${h};
                      background:#fafafa; border-radius:4px;"></div>`;
        if (!useGrid) html += _cap(`<b>Рис.</b> Расчётный мгновенный дебит газа Q, тыс.м³/сут (по давлению на штуцере). Розовые зоны — простои.`);
      }
      if (on('chart_utilization')) {
        const h = useGrid ? '180px' : '200px';
        html += `<div id="prev-${bid}-obs-util" style="height:${h};
                      background:#fafafa; border-radius:4px;"></div>`;
        if (!useGrid) html += _cap(`<b>Рис.</b> Учёт рабочего времени по дням: зелёное — рабочие часы, красное — простой. КИВ — доля рабочего времени за период.`);
      }
      if (useGrid) {
        html += `</div>`;
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

    // ─── events_table для observation_analysis ───
    if (kind === 'observation_analysis' && on('events_table')) {
      const obsEvents = snap.events || [];
      if (obsEvents.length > 0) {
        let evRows = '';
        const eventsToShow = obsEvents.slice(0, 30);
        eventsToShow.forEach((e, i) => {
          const bg = i % 2 === 0 ? '#fff' : '#fafafa';
          const isEquip = e.type === 'equip' || e.type === 'equipment';
          const icon = e.type === 'purge' ? '💨' : e.type === 'reagent' ? '💊' : isEquip ? '🔧' : '•';
          let typeLabel, descr;
          if (e.type === 'purge') {
            typeLabel = 'Продувка';
            descr = '—';
          } else if (e.type === 'reagent') {
            typeLabel = 'Вброс реагента';
            descr = e.reagent || e.label || '—';
          } else if (isEquip) {
            typeLabel = 'Установка оборудования';
            descr = e.description || e.label || '—';
          } else if (e.type === 'other') {
            typeLabel = 'Прочее';
            descr = e.description || e.label || '—';
          } else {
            typeLabel = e.type || '—';
            descr = e.label || e.description || '—';
          }
          const pTube = e.p_tube != null ? Number(e.p_tube).toFixed(1) : '—';
          const pLine = e.p_line != null ? Number(e.p_line).toFixed(1) : '—';
          evRows += `<tr style="background:${bg};">
            <td style="padding:4px 8px; text-align:center;">${icon}</td>
            <td style="padding:4px 8px;">${_escHtml((e.t || e.ts || e.time || '').slice(0, 16))}</td>
            <td style="padding:4px 8px;">${_escHtml(typeLabel)}</td>
            <td style="padding:4px 8px;">${_escHtml(descr)}</td>
            <td style="text-align:right; padding:4px 8px;">${e.qty || e.amount || '—'}</td>
            <td style="text-align:right; padding:4px 8px;">${pTube}</td>
            <td style="text-align:right; padding:4px 8px;">${pLine}</td>
          </tr>`;
        });

        html += `<h4 style="margin:16px 0 8px; font-size:0.95rem; color:#1f2937;">
          📋 События за период (${obsEvents.length})
        </h4>
        <table style="width:100%; font-size:0.8rem; border-collapse:collapse; border:1px solid #e5e7eb;">
          <thead>
            <tr style="background:#f3f4f6;">
              <th style="padding:6px 8px; width:40px;"></th>
              <th style="text-align:left; padding:6px 8px;">Дата/время</th>
              <th style="text-align:left; padding:6px 8px;">Тип</th>
              <th style="text-align:left; padding:6px 8px;">Описание</th>
              <th style="text-align:right; padding:6px 8px;">Кол-во</th>
              <th style="text-align:right; padding:6px 8px;">P устье</th>
              <th style="text-align:right; padding:6px 8px;">P линия</th>
            </tr>
          </thead>
          <tbody>${evRows}</tbody>
        </table>`;

        const reagentEvents = obsEvents.filter(e => e.type === 'reagent');
        const purgeEvents = obsEvents.filter(e => e.type === 'purge');
        const equipEvents = obsEvents.filter(e => e.type === 'equip' || e.type === 'equipment');
        html += `<div style="font-size:0.8rem; color:#6b7280; margin-top:6px; padding:6px 8px; background:#f9fafb; border-radius:4px;">
          <b>Итого:</b> ${reagentEvents.length} вбросов реагента,
          ${purgeEvents.length} продувок${equipEvents.length ? `, ${equipEvents.length} событий оборудования` : ''}.
        </div>`;

        if (obsEvents.length > 30) {
          html += `<div style="font-size:0.75rem; color:#9ca3af; margin-top:4px;">
            ... показаны первые 30 из ${obsEvents.length} событий
          </div>`;
        }
      }
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
        // Ключи дельт snapshot: *_mean (каждый {abs, pct}). Паритет с PDF
        // (_format_observation_rfc_block): колонки Метрика | Δ абс. | Δ %.
        [['p_tube_mean', 'P уст.'], ['p_line_mean', 'P лин.'], ['dp_mean', 'ΔP'], ['q_mean', 'Q']].forEach(([k, label]) => {
          const dv = d[k];
          if (!dv) return;
          const arrow = dv.pct > 0 ? '↑' : dv.pct < 0 ? '↓' : '→';
          const color = Math.abs(dv.pct) > 10 ? '#dc2626' : '#374151';
          rows += `<tr>
            <td style="padding:2px 6px;">${_escHtml(label)}</td>
            <td style="text-align:right; padding:2px 6px;">${fmt(dv.abs)}</td>
            <td style="text-align:right; padding:2px 6px; color:${color};">${arrow} ${fmt(dv.pct, 1)}%</td>
          </tr>`;
        });
        html += `<div style="margin:8px 0;">
          <div style="font-weight:600; font-size:0.85rem; margin-bottom:4px;">⚖️ Сравнение с базовым периодом (B1)</div>
          <table style="width:100%; font-size:0.78rem; border-collapse:collapse;">
            <tr style="background:#f3f4f6;">
              <th style="text-align:left; padding:3px 6px;">Метрика</th>
              <th style="text-align:right; padding:3px 6px;">Δ абс.</th>
              <th style="text-align:right; padding:3px 6px;">Δ %</th>
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
          ${_escHtml(d.verdict || d.message || '')}
        </div>`).join('')}
      </div>`;
    }

    // ─── Segments table (for observation_segment) ───
    const segments = snap.segments || [];
    if (on('segments_table') && segments.length) {
      let rows = '';
      segments.forEach((s, i) => {
        // Ключи snapshot: start_date/end_date/mean_q/mean_dp/direction
        // (направление rising/falling/stable). Паритет с PDF (_format_observation_rfc_block).
        const dir = s.direction || '';
        const dirArrow = ({rising: '↑', falling: '↓', stable: '→'})[dir] || '—';
        const trendColor = dir === 'rising' ? '#16a34a' : dir === 'falling' ? '#dc2626' : '#374151';
        rows += `<tr>
          <td style="padding:2px 6px;">${s.num || (i + 1)}</td>
          <td style="padding:2px 6px;">${_escHtml(s.start_date || '—')}</td>
          <td style="padding:2px 6px;">${_escHtml(s.end_date || '—')}</td>
          <td style="text-align:right; padding:2px 6px;">${fmt(s.mean_q)}</td>
          <td style="text-align:right; padding:2px 6px;">${fmt(s.mean_dp)}</td>
          <td style="padding:2px 6px; color:${trendColor};">${dirArrow}</td>
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
      // Тип впадина/вершина по знаку Δ ДО и ПОСЛЕ перелома (та же логика, что в PDF cp_rows).
      const SRC = { confirmed: 'подтв. обоими', only_total: 'только Q общ', only_working: 'только Q раб' };
      let prevM = null;
      const tdS = 'padding:3px 8px; border:1px solid #e5e7eb;';
      const rowsHtml = changepoints.map((cp, i) => {
        const m = cp.magnitude_pct;
        let arrow = '—', typ = '—';
        if (m != null) {
          if (prevM != null && prevM > 0 && m < 0) { arrow = '∧'; typ = 'вершина'; }
          else if (prevM != null && prevM < 0 && m > 0) { arrow = '∨'; typ = 'впадина'; }
          else if (m >= 0) { arrow = '↗'; typ = 'рост'; }
          else { arrow = '↘'; typ = 'спад'; }
          prevM = m;
        }
        const qd = m != null ? ((m >= 0 ? '+' : '') + Number(m).toFixed(1) + '%') : '—';
        return `<tr>
          <td style="${tdS}">${_escHtml(cp.date || cp.datetime || '—')}</td>
          <td style="${tdS} text-align:center;">${_escHtml(cp.tag || ('CP' + (i + 1)))}</td>
          <td style="${tdS} text-align:center;">${arrow} ${typ}</td>
          <td style="${tdS} text-align:right;">${qd}</td>
          <td style="${tdS}">${_escHtml(SRC[cp.source] || cp.source || '')}</td>
        </tr>`;
      }).join('');
      html += `<div style="margin:8px 0;">
        <div style="font-weight:600; font-size:0.85rem; margin-bottom:2px;">🔻 Точки перелома (${changepoints.length})</div>
        <div style="font-size:0.74rem; color:#6b7280; font-style:italic; margin-bottom:5px;">Тип: <b>∧</b> вершина (рост→спад), <b>∨</b> впадина (спад→рост), ↗ рост, ↘ спад. Метка «только Q общ» — паспортный дебит (возможный артефакт), «только Q раб» — фактический сдвиг режима, «подтв. обоими» — надёжный.</div>
        <table style="border-collapse:collapse; width:100%; font-size:0.78rem;">
          <thead><tr style="background:#f3f4f6; font-weight:600;">
            <td style="${tdS}">Дата / время</td><td style="${tdS} text-align:center;">CP</td>
            <td style="${tdS} text-align:center;">Тип</td><td style="${tdS} text-align:right;">ΔQ</td>
            <td style="${tdS}">Метка</td>
          </tr></thead>
          <tbody>${rowsHtml}</tbody>
        </table>
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
      // Подготовка shapes и annotations из snap.downtime_periods и snap.events
      const downtimePeriods = snap.downtime_periods || [];
      const obsEvents = snap.events || [];

      // Shapes для простоев (розовые зоны)
      const downtimeShapes = downtimePeriods.map(p => ({
        type: 'rect',
        xref: 'x', yref: 'paper',
        x0: p.start || p.from, x1: p.end || p.to,
        y0: 0, y1: 1,
        fillcolor: 'rgba(244, 114, 182, 0.25)',  // pink
        line: { width: 0 },
        layer: 'below'
      }));

      // Shapes для событий (вертикальные линии для продувок)
      const eventShapes = [];
      const eventAnnotations = [];
      obsEvents.forEach(e => {
        const ts = e.t || e.ts || e.time;
        if (!ts) return;
        if (e.type === 'purge') {
          eventShapes.push({
            type: 'line',
            xref: 'x', yref: 'paper',
            x0: ts, x1: ts, y0: 0, y1: 1,
            line: { color: '#8b5cf6', width: 1.5, dash: 'dot' },
            layer: 'above'
          });
        }
      });

      const allShapes = [...downtimeShapes, ...eventShapes];

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
          // Добавляем маркеры для событий реагента
          const reagentEvents = obsEvents.filter(e => e.type === 'reagent' && (e.t || e.ts || e.time));
          if (reagentEvents.length) {
            traces.push({
              x: reagentEvents.map(e => e.t || e.ts || e.time),
              y: reagentEvents.map(e => e.p_tube || null),
              mode: 'markers', name: 'Вброс реагента',
              marker: { color: '#10b981', size: 8, symbol: 'diamond' },
              hovertext: reagentEvents.map(e => e.reagent || e.label || 'Реагент')
            });
          }
          if (traces.length) {
            try { Plotly.newPlot(elPres, traces, {...layout, title:'Давления, кгс/см²', yaxis:{title:'кгс/см²'}, shapes:allShapes}, cfg); }
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
          try { Plotly.newPlot(elDp, traces, {...layout, title:'Перепад ΔP, кгс/см²', yaxis:{title:'кгс/см²', rangemode:'tozero'}, shapes:allShapes}, cfg); }
          catch(e) { console.warn('obs-dp', e); }
        }
      }

      // 3) Q(t) (chart_q) — с трендом, если есть
      if (on('chart_q')) {
        const elQ = document.getElementById(`prev-${bid}-obs-q`);
        if (elQ) {
          const qData = c.q_gas_total || c.q_gas_working || [];
          if (qData.some(v => v != null)) {
            const traces = [{
              x:c.dates, y:qData, name:'Q факт.', mode:'lines',
              line:{color:'#2e7d32', width:1.6}, connectgaps:false
            }];
            // Добавляем линию тренда, если есть
            const qTrend = snap.q_trend_line || snap.q_trend;
            if (qTrend && qTrend.slope != null && qTrend.intercept != null && c.dates.length >= 2) {
              const n = c.dates.length;
              const y0 = qTrend.intercept;
              const y1 = qTrend.intercept + qTrend.slope * (n - 1);
              traces.push({
                x: [c.dates[0], c.dates[n - 1]],
                y: [y0, y1],
                mode: 'lines', name: `Тренд (${qTrend.slope >= 0 ? '+' : ''}${qTrend.slope.toFixed(3)}/сут)`,
                line: { color: '#9333ea', width: 2, dash: 'dash' }
              });
            }
            try { Plotly.newPlot(elQ, traces, {...layout, title:'Дебит Q(t), тыс.м³/сут', yaxis:{title:'тыс.м³/сут'}, shapes:allShapes}, cfg); }
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
  //   ADAPTATION BLOCKS RENDERERS (Step 5 wizard) — UNIVERSAL TEMPLATE v2
  // ═════════════════════════════════════════════════════════════════════════

  /**
   * _buildAdaptationPeriodAnalysisHtml — УНИВЕРСАЛЬНЫЙ рендер блока adaptation_period_analysis
   *
   * Структура (порядок блоков):
   *   1. Введение (intro) — текст с датчиками SMOD
   *   2. График давлений (P устье, P линия) + события
   *   3. График ΔP + пояснение
   *   4. График Q + пояснение
   *   5. Таблица событий
   *   6. 4 карточки метрик (R, R★, B2, B1)
   *   7. Таблица сравнения (Наблюдение → Адаптация)
   *   8. Тренды
   *   9. Сегментный анализ (если есть)
   *
   * Снапшот: { adaptation, observation, b1, comparison, optimal, segment_analysis? }
   */
  // ===== Статические блоки этапа адаптации (ПАРНОСТЬ HTML↔PDF). При правке
  //       синхронно менять adaptation_report.tex (ветка adaptation_period_analysis). =====
  function _adaptSetupDescriptionHtml(wellNumber) {
    const wn = wellNumber ? ('№' + _escHtml(String(wellNumber))) : 'скважины';
    return `<div style="margin-bottom:12px; padding:12px 14px; background:#f8fafc; border-left:4px solid #3b82f6; border-radius:4px; font-size:0.86rem; line-height:1.6; color:#374151;">
      Для адаптации технологии и оптимизации работы скважины с применением ТПАВ на устье скважины ${wn} установлено шлюзовое устройство для дозирования ТПАВ (ШУ-50-З5-550, ТУ У 29.5-40846045-002:2018). Для on-line мониторинга устьевых параметров (давление на буфере, давление в линии за штуцером) установлен комплект системы мониторинга SMOD-03.001U.
      <br><br>До начала работ по выбору типа и периодичности дозирования реагента, с целью уточнения текущих режимов работы скважины, предварительного анализа и прогноза, проводилась фиксация и запись базовых рабочих параметров скважины (устьевое давление, давление в линии за штуцером) системой мониторинга технологических параметров SMOD-03.001U. Технические характеристики измерительных датчиков приведены в Табл. 2.1.
      <br><br>На основании данных, полученных с датчиков системы мониторинга, проведён расчёт текущего дебита скважины. Расчёт текущего дебита газа выполнен с использованием эмпирических коэффициентов СП ООО «Uz-Kor Gas Chemical».
    </div>`;
  }
  const _SENSOR_PARAMS = [
    ['Напряжение питания', '3.6 В DC, литий-ионный батарейный источник'],
    ['Диапазон давления', '−0.1–6 МПа'],
    ['Ток возбуждения', '0.2 мА'],
    ['Интерфейс связи', 'беспроводной сигнал (wireless)'],
    ['Рабочая температура', '−20–60 °C'],
    ['Рабочая влажность', '0–80 % RH'],
    ['Точность измерения', '0.5 % FS'],
    ['Интервал между измерениями', '60 сек'],
  ];
  function _sensorParamsTableHtml() {
    const rows = _SENSOR_PARAMS.map(([k, v]) => `<tr><td style="text-align:left;">${_escHtml(k)}</td><td style="text-align:left;">${_escHtml(v)}</td></tr>`).join('');
    return `<div style="margin-bottom:12px;">
      <div style="font-weight:600; font-size:0.86rem; margin-bottom:4px;">Табл. 2.1. Параметры измерительных датчиков (SMOD)</div>
      <table class="cd-table" style="font-size:0.82rem; width:100%;">
        <thead><tr><th style="text-align:left;">Параметр датчика</th><th style="text-align:left;">Значение</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }
  function _flowMethodologyHtml() {
    return `<div style="margin-bottom:12px; padding:12px 14px; background:#fafafa; border:1px solid #e5e7eb; border-radius:6px; font-size:0.84rem; line-height:1.55; color:#374151;">
      <div style="font-weight:700; font-size:0.92rem; margin-bottom:6px;">📐 Методика расчёта дебита</div>
      Расчётный дебит газа определяется по модифицированной формуле <b>Гилберта-Пирвердяна</b>, связывающей перепад давлений через штуцер (P<sub>тр</sub> и P<sub>шл</sub>) с расходом газа.
      <div style="background:#fff; border:1px solid #e0e0e0; border-radius:6px; padding:10px 14px; margin:8px 0; font-family:monospace; font-size:0.82rem;">
        <div style="color:#666; font-size:0.78rem;">Докритический режим (r &lt; 0.5):</div>
        <div style="color:#1565c0;">Q = M · (C₁·P<sub>тр</sub> − C₂·P<sub>шл</sub>) · d² / C₃</div>
        <div style="color:#666; font-size:0.78rem; margin-top:6px;">Критический режим (r ≥ 0.5):</div>
        <div style="color:#c62828;">Q = M · C₁·P<sub>тр</sub> · (1 − r) · d² / C₃</div>
        <div style="color:#888; font-size:0.76rem; margin-top:4px;">где r = P<sub>шл</sub> / P<sub>тр</sub> — отношение давлений</div>
      </div>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:4px; font-size:0.8rem;">
        <div><b>Q</b> — дебит газа, тыс. м³/сут</div><div><b>M</b> — калибровочный множитель (4.1)</div>
        <div><b>P<sub>тр</sub></b> — давление устья, кгс/см²</div><div><b>P<sub>шл</sub></b> — давление шлейфа, кгс/см²</div>
        <div><b>d</b> — диаметр штуцера, мм</div><div><b>C₁, C₂, C₃</b> — эмпирические константы</div>
      </div>
      <div style="font-weight:600; margin:8px 0 2px;">Алгоритм</div>
      <ol style="margin:0; padding-left:18px; font-size:0.8rem;">
        <li>Чтение давлений из БД (pressure_raw)</li>
        <li>Очистка: отрицательные/нулевые → NaN, интерполяция (ffill/bfill)</li>
        <li>Сглаживание: фильтр Савицкого-Голея (окно 17, полином 3, 2 прохода)</li>
        <li>Расчёт дебита по формуле для каждой минутной записи</li>
        <li>Детекция продувок (маркеры БД start→press→stop + алгоритмически)</li>
        <li>Потери при стравливании: уравнение Бернулли через штуцер (фаза venting)</li>
        <li>Простои: P<sub>тр</sub> ≤ P<sub>шл</sub> (дебит = 0, потерь нет)</li>
        <li>Накопленный дебит: интеграция трапециями (Δt = 1/1440 сут)</li>
      </ol>
      <div style="font-weight:600; margin:8px 0 2px;">Потери при стравливании (продувка)</div>
      <div style="font-family:monospace; font-size:0.82rem; color:#c62828;">Q<sub>loss</sub> = C<sub>d</sub> · A · √(2·ΔP / ρ)</div>
      <div style="font-size:0.76rem; color:#888;">C<sub>d</sub>=0.9 (коэфф. расхода), A=π·d²/4 (сечение штуцера продувки), ΔP=P<sub>уст</sub>−P<sub>атм</sub>, ρ — плотность газа при P<sub>уст</sub>.</div>
    </div>`;
  }
  function _buildAdaptationPeriodAnalysisHtml(snap, block, parts) {
    if (!snap || (!snap.adaptation && !snap.observation)) {
      return `<div style="color:#9ca3af; padding:12px;">Снимок данных пуст.</div>`;
    }

    const adapt = snap.adaptation || {};
    const obs = snap.observation || {};
    const b1 = snap.b1 || {};
    const cmp = snap.comparison || {};
    const opt = snap.optimal || {};
    const seg = snap.segment_analysis || {};
    const p = block.params || {};
    const bid = block.id || 'x';

    const fmt = (v, d = 2) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d);
    const fmtSgn = (v, d = 2) => {
      if (v == null || isNaN(v)) return '—';
      const n = Number(v);
      return (n > 0 ? '+' : '') + n.toFixed(d);
    };
    const fmtPct = (v) => (v == null || isNaN(v)) ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(1) + '%';
    const on = (key) => parts[key] !== false;
    const trendArrow = (t) => {
      if (!t || !t.direction) return '→';
      return t.direction === 'up' ? '↑' : t.direction === 'down' ? '↓' : '→';
    };

    let html = '';
    const dFrom = (p.date_from || adapt.date_from || '—').slice(0, 10);
    const dTo = (p.date_to || adapt.date_to || '—').slice(0, 10);

    const _wn = snap.well_number || (adapt.intro && adapt.intro.well_number) || (block.params && block.params.well_number) || '';
    // ── Описание установки (ШУ, мониторинг, расчёт дебита) — парность с PDF ──
    if (on('setup_description')) {
      html += _adaptSetupDescriptionHtml(_wn);
    }
    // ── Параметры датчиков (Табл. 2.1) ──
    if (on('sensor_table')) {
      html += _sensorParamsTableHtml();
    }
    // ── Формулы и методика расчёта дебита (тоггл, default on) ──
    if (on('flow_methodology')) {
      html += _flowMethodologyHtml();
    }

    // ═══════════════════════════════════════════════════════════════════
    // 1. ВВЕДЕНИЕ (intro) — текст о периоде и датчиках
    // ═══════════════════════════════════════════════════════════════════
    const intro = adapt.intro || {};
    if (intro.intro_text || on('intro')) {
      html += `<div style="margin-bottom:14px; padding:12px 14px; background:#f8fafc;
                          border-left:4px solid #3b82f6; border-radius:4px;">
        <div style="font-size:0.9rem; line-height:1.6; color:#374151;">
          ${_escHtml(intro.intro_text || `В период с ${dFrom} по ${dTo} проводились работы по адаптации технологии к условиям скважины.`)}
        </div>
      </div>`;
    }

    // ═══════════════════════════════════════════════════════════════════
    // 2-4. ГРАФИКИ (давления, ΔP, Q) + события
    // ═══════════════════════════════════════════════════════════════════
    const chartData = adapt.chart_data || [];
    const events = adapt.events_for_chart || [];

    if (on('charts') && chartData.length > 0) {
      html += `<h4 style="margin:14px 0 8px; font-size:0.95rem; color:#1f2937;">
        📊 Графики параметров за период адаптации
      </h4>`;
      // 1.2 — переходный текст с описанием графиков
      html += `<div style="font-size:0.84rem; color:#4b5563; line-height:1.5; margin-bottom:10px;">
        Ниже показана динамика ключевых параметров скважины за период адаптации по данным
        датчиков давления (1 измерение/мин). На графиках отображены: <b>устьевое и линейное
        давление</b> (Рис. 4.1), <b>перепад ΔP</b> между ними (Рис. 4.2) и <b>расчётный дебит
        газа Q</b> (Рис. 4.3). Маркеры на графиках — события периода: точки — вбросы реагента,
        вертикальные линии — продувки.
      </div>`;

      // 2. График давлений (P устье + P линия)
      html += `<div style="margin-bottom:12px;">
        <div id="adapt-${bid}-pres" style="height:280px; background:#fafafa; border:1px solid #e5e7eb; border-radius:6px;"></div>
        <div style="font-size:0.78rem; color:#6b7280; margin-top:4px; padding:0 4px;">
          <b>Рис. 4.1.</b> Динамика устьевого и линейного давления.
          ${events.length > 0 ? 'Вертикальные линии — продувки, точки — вбросы реагента.' : ''}
        </div>
      </div>`;

      // 3. График ΔP
      html += `<div style="margin-bottom:12px;">
        <div id="adapt-${bid}-dp" style="height:250px; background:#fafafa; border:1px solid #e5e7eb; border-radius:6px;"></div>
        <div style="font-size:0.78rem; color:#6b7280; margin-top:4px; padding:0 4px;">
          <b>Рис. 4.2.</b> Перепад давления (ΔP = P устье − P линия).
          Рост ΔP указывает на увеличение дебита; снижение — на возможное накопление жидкости.
        </div>
      </div>`;

      // 4. График Q
      html += `<div style="margin-bottom:12px;">
        <div id="adapt-${bid}-flow" style="height:250px; background:#fafafa; border:1px solid #e5e7eb; border-radius:6px;"></div>
        <div style="font-size:0.78rem; color:#6b7280; margin-top:4px; padding:0 4px;">
          <b>Рис. 4.3.</b> Расчётный дебит газа по показаниям датчиков давления.
          ${adapt.choke_label ? `Диаметр штуцера: ${_escHtml(adapt.choke_label)}.` : ''}
          ${adapt.flow_median ? `Медиана Q = ${fmt(adapt.flow_median)} тыс.м³/сут.` : ''}
        </div>
      </div>`;
    }

    // 1.3 — Посуточный график рабочего/нерабочего времени
    if (on('downtime_chart') && chartData.length > 0) {
      html += `<div style="margin-bottom:12px;">
        <div id="adapt-${bid}-downtime" style="height:260px; background:#fafafa; border:1px solid #e5e7eb; border-radius:6px;"></div>
        <div style="font-size:0.78rem; color:#6b7280; margin-top:4px; padding:0 4px;">
          <b>Рис. 4.4.</b> Рабочее и нерабочее время по суткам. Зелёным (полупрозрачно) — часы работы,
          красным — простой. Высота столбца — число часов с данными за сутки.
        </div>
      </div>`;
    }

    // ═══════════════════════════════════════════════════════════════════
    // 4.5. ТАБЛИЦА СТАТИСТИЧЕСКИХ ПОКАЗАТЕЛЕЙ (1.4)
    // Считается из chart_data — тот же источник, что и в PDF (парность).
    // ═══════════════════════════════════════════════════════════════════
    if (on('stats_table') && chartData.length > 0) {
      // Активные точки: q>0 (отсев простоя/ложных нулей — инвариант проекта).
      const _active = chartData.filter(p => p.q != null && Number(p.q) > 0);
      const _stat = (key) => {
        const xs = _active.map(p => p[key]).filter(v => v != null && !isNaN(v)).map(Number);
        if (!xs.length) return null;
        const n = xs.length;
        const mean = xs.reduce((s, x) => s + x, 0) / n;
        const srt = xs.slice().sort((a, b) => a - b);
        const median = n % 2 ? srt[(n - 1) / 2] : (srt[n / 2 - 1] + srt[n / 2]) / 2;
        const sd = Math.sqrt(xs.reduce((s, x) => s + (x - mean) ** 2, 0) / n);
        return { mean, median, min: srt[0], max: srt[n - 1], sd };
      };
      const sRows = [
        ['Дебит Q, тыс.м³/сут', _stat('q')],
        ['P трубное, кгс/см²', _stat('p_tube')],
        ['P линейное, кгс/см²', _stat('p_line')],
        ['Перепад ΔP, кгс/см²', _stat('dp')],
      ].filter(r => r[1]);
      if (sRows.length) {
        const thS = 'padding:5px 8px; border-bottom:2px solid #d1d5db; text-align:right; font-size:0.74rem; color:#6b7280; background:#f9fafb;';
        const tdS = 'padding:4px 8px; border-bottom:1px solid #eee; text-align:right;';
        const body = sRows.map(([lbl, s]) => `<tr>
            <td style="${tdS} text-align:left;">${lbl}</td>
            <td style="${tdS}">${fmt(s.mean)}</td><td style="${tdS}">${fmt(s.median)}</td>
            <td style="${tdS}">${fmt(s.min)}</td><td style="${tdS}">${fmt(s.max)}</td>
            <td style="${tdS}">${fmt(s.sd)}</td></tr>`).join('');
        const wh = adapt.working_hours, kiv = adapt.utilization_pct, dwh = adapt.downtime_hours;
        const nDown = (adapt.downtime_periods_list || []).length;
        html += `<div style="margin:12px 0;">
          <div style="font-weight:600; color:#374151; margin-bottom:3px; font-size:0.88rem;">Табл. Статистические показатели за период</div>
          <div style="font-size:0.78rem; color:#6b7280; margin-bottom:6px;">Сводные статистики по активным точкам (исключены простои): среднее, медиана, минимум, максимум и стандартное отклонение σ по дебиту Q и давлениям; ниже — сводка режима работы (продувки, рабочее время/КИВ, простои).</div>
          <table style="width:100%; border-collapse:collapse; font-size:0.82rem;">
            <thead><tr>
              <th style="${thS} text-align:left;">Показатель</th><th style="${thS}">Среднее</th>
              <th style="${thS}">Медиана</th><th style="${thS}">Мин</th><th style="${thS}">Макс</th>
              <th style="${thS}">σ</th>
            </tr></thead><tbody>${body}</tbody>
          </table>
          <div style="margin-top:8px; font-size:0.82rem; color:#4b5563; display:flex; gap:18px; flex-wrap:wrap;">
            <span><b>Продувки:</b> ${adapt.purge_count != null ? adapt.purge_count : '—'}</span>
            <span><b>Рабочее время:</b> ${wh != null ? fmt(wh, 1) + ' ч' : '—'}${kiv != null ? ` (КИВ ${fmt(kiv, 1)}%)` : ''}</span>
            <span><b>Простои:</b> ${dwh != null ? fmt(dwh, 1) + ' ч' : '—'}${nDown ? ` (${nDown} периодов)` : ''}</span>
          </div>
        </div>`;
      }
    }

    // ═══════════════════════════════════════════════════════════════════
    // 5. ТАБЛИЦА СОБЫТИЙ
    // ═══════════════════════════════════════════════════════════════════
    if (on('events_table') && events.length > 0) {
      let evRows = '';
      const eventsToShow = events.slice(0, 30);
      eventsToShow.forEach((e, i) => {
        const bg = i % 2 === 0 ? '#fff' : '#fafafa';
        const icon = e.type === 'purge' ? '💨' : e.type === 'reagent' ? '💊' : '•';
        // Логика типов и описаний:
        // - reagent → Тип: "Вброс реагента", Описание: название реагента
        // - other → Тип: "Прочее", Описание: description из события
        // - purge → Тип: "Продувка", Описание: —
        let typeLabel, descr;
        if (e.type === 'purge') {
          typeLabel = 'Продувка';
          descr = '—';
        } else if (e.type === 'reagent') {
          typeLabel = 'Вброс реагента';
          descr = e.reagent || '—';
        } else if (e.type === 'other') {
          typeLabel = 'Прочее';
          descr = e.description || e.label || '—';
        } else {
          typeLabel = e.type || '—';
          descr = e.label || '—';
        }
        // Давления на момент события
        const pTube = e.p_tube != null ? e.p_tube.toFixed(1) : '—';
        const pLine = e.p_line != null ? e.p_line.toFixed(1) : '—';
        evRows += `<tr style="background:${bg};">
          <td style="padding:4px 8px; text-align:center;">${icon}</td>
          <td style="padding:4px 8px;">${_escHtml((e.t || e.ts || e.time || '').slice(0, 16))}</td>
          <td style="padding:4px 8px;">${_escHtml(typeLabel)}</td>
          <td style="padding:4px 8px;">${_escHtml(descr)}</td>
          <td style="text-align:right; padding:4px 8px;">${e.qty || e.amount || '—'}</td>
          <td style="text-align:right; padding:4px 8px;">${pTube}</td>
          <td style="text-align:right; padding:4px 8px;">${pLine}</td>
        </tr>`;
      });

      html += `<h4 style="margin:16px 0 4px; font-size:0.95rem; color:#1f2937;">
        📋 Табл. События за период (${events.length})
      </h4>
      <div style="font-size:0.78rem; color:#6b7280; margin-bottom:6px;">Хронология событий за период: вбросы реагента (с указанием названия и количества), продувки и прочие события; для каждого приведены давления устья и линии на момент события.</div>
      <table style="width:100%; font-size:0.8rem; border-collapse:collapse; border:1px solid #e5e7eb;">
        <thead>
          <tr style="background:#f3f4f6;">
            <th style="padding:6px 8px; width:40px;"></th>
            <th style="text-align:left; padding:6px 8px;">Дата/время</th>
            <th style="text-align:left; padding:6px 8px;">Тип</th>
            <th style="text-align:left; padding:6px 8px;">Описание</th>
            <th style="text-align:right; padding:6px 8px;">Кол-во</th>
            <th style="text-align:right; padding:6px 8px;">P устье</th>
            <th style="text-align:right; padding:6px 8px;">P линия</th>
          </tr>
        </thead>
        <tbody>${evRows}</tbody>
      </table>`;

      if (events.length > 30) {
        html += `<div style="font-size:0.75rem; color:#9ca3af; margin-top:4px;">
          ... показаны первые 30 из ${events.length} событий
        </div>`;
      }
    }

    // ═══════════════════════════════════════════════════════════════════
    // 5.1. ПЛИТКИ «СТАТИСТИКА СОБЫТИЙ» (1.6) — после таблицы событий
    // ═══════════════════════════════════════════════════════════════════
    if (on('events_stats') && events.length > 0) {
      const reagentEv = events.filter(e => e.type === 'reagent');
      const purgeEv = events.filter(e => e.type === 'purge');
      const otherEv = events.filter(e => e.type !== 'reagent' && e.type !== 'purge');
      const evTile = (bg, bd, title, big, sub) => `<div style="flex:1; min-width:150px; background:${bg};
          border:1px solid ${bd}; border-left:4px solid ${bd}; border-radius:8px; padding:10px 12px;">
        <div style="font-size:0.72rem; font-weight:700; text-transform:uppercase; color:#6b7280;">${title}</div>
        <div style="font-size:1.3rem; font-weight:700; color:#374151; margin:2px 0;">${big}</div>
        <div style="font-size:0.78rem; color:#6b7280;">${sub}</div></div>`;
      html += `<div style="display:flex; gap:10px; flex-wrap:wrap; margin:10px 0;">
        ${evTile('#ecfdf5', '#16a34a', 'Вбросы реагента', reagentEv.length, adapt.reagent_qty != null ? `Σ ${fmt(adapt.reagent_qty, 1)} ед.` : '&nbsp;')}
        ${evTile('#eff6ff', '#3b82f6', 'Продувки', purgeEv.length, '&nbsp;')}
        ${evTile('#f9fafb', '#9ca3af', 'Прочие события', otherEv.length, '&nbsp;')}
      </div>`;
    }

    // ═══════════════════════════════════════════════════════════════════
    // 6. КАРТОЧКИ МЕТРИК (R, R★, B2, B1)
    // ═══════════════════════════════════════════════════════════════════
    if (on('metrics_cards')) {
      const mkCard = (label, color, bgColor, data, isB1 = false) => {
        if (!data || Object.keys(data).length === 0) return '';
        const qMed = isB1 ? (data.q_total_median || data.flow_median) : data.flow_median;
        const qAvg = isB1 ? (data.q_total_avg || data.flow_avg) : data.flow_avg;
        const dpMed = data.dp_median;
        const pTube = isB1 ? data.p_wellhead_median : data.p_tube_median;
        const pLine = isB1 ? data.p_flowline_median : data.p_line_median;
        const util = data.utilization_pct;
        const dur = data.duration_days || data.days_count;

        return `<div style="flex:1; min-width:165px; padding:10px 12px; background:${bgColor};
                           border:2px solid ${color}; border-radius:8px;">
          <div style="font-weight:700; color:${color}; font-size:0.92rem; margin-bottom:8px;
                      border-bottom:1px solid ${color}; padding-bottom:4px;">${_escHtml(label)}</div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:3px; font-size:0.78rem;">
            <div style="color:#6b7280;">Q мед:</div>
            <div style="text-align:right; font-weight:600;">${fmt(qMed)}</div>
            <div style="color:#6b7280;">Q ср:</div>
            <div style="text-align:right;">${fmt(qAvg)}</div>
            <div style="color:#6b7280;">ΔP мед:</div>
            <div style="text-align:right; font-weight:600;">${fmt(dpMed)}</div>
            <div style="color:#6b7280;">P тр:</div>
            <div style="text-align:right;">${fmt(pTube)}</div>
            <div style="color:#6b7280;">P лин:</div>
            <div style="text-align:right;">${fmt(pLine)}</div>
            ${util != null ? `<div style="color:#6b7280;">КИВ:</div>
            <div style="text-align:right;">${fmt(util, 1)}%</div>` : ''}
            ${dur != null ? `<div style="color:#6b7280;">Дней:</div>
            <div style="text-align:right;">${dur}</div>` : ''}
          </div>
        </div>`;
      };

      html += `<h4 style="margin:18px 0 8px; font-size:0.95rem; color:#1f2937;">
        📈 Статистический анализ
      </h4>
      <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px;">
        ${mkCard('R (Адаптация)', '#f59e0b', '#fffbeb', adapt)}
        ${mkCard('R★ (Оптимум)', '#22c55e', '#ecfdf5', opt)}
        ${mkCard('B2 (Наблюд.)', '#6366f1', '#eef2ff', obs)}
        ${mkCard('B1 (Базовый)', '#64748b', '#f8fafc', b1, true)}
      </div>`;
    }

    // ═══════════════════════════════════════════════════════════════════
    // 7. ТАБЛИЦА СРАВНЕНИЯ (Наблюдение → Адаптация)
    // ═══════════════════════════════════════════════════════════════════
    if (on('comparison_table') && (adapt || obs || b1 || opt)) {
      // Богатая таблица: B1 · B2 · R · R★ + дельты (ΔR vs B2, ΔR vs B1, ΔR★ vs B2).
      // Зеркало wz5RenderCompare (страница анализа) — единая логика.
      const b1QMed = b1 ? (b1.q_total_median ?? b1.q_working_median ?? b1.flow_median) : null;
      const b1QAvg = b1 ? (b1.q_total_avg ?? b1.q_working_avg ?? b1.flow_avg) : null;
      const b1Shut = b1 && b1.shutdown_min_total != null ? b1.shutdown_min_total / 60
                     : (b1 ? b1.downtime_hours : null);
      const cmpRows = [
        { section: 'Дебит' },
        { l: 'Q медиана', u: 'тыс.м³/сут', b1: b1QMed, b2: obs.flow_median, r: adapt.flow_median, rs: opt.flow_median },
        { l: 'Q среднее', u: 'тыс.м³/сут', b1: b1QAvg, b2: obs.flow_avg, r: adapt.flow_avg, rs: opt.flow_avg },
        { l: 'Q максимум', u: 'тыс.м³/сут', b1: null, b2: obs.q_max, r: adapt.q_max, rs: opt.q_max },
        { l: 'Q накопл.', u: 'тыс.м³', b1: null, b2: obs.flow_cumulative, r: adapt.flow_cumulative, rs: opt.flow_cumulative },
        { section: 'Давления и ΔP' },
        { l: 'ΔP медиана', u: 'кгс/см²', b1: b1.dp_median, b2: obs.dp_median, r: adapt.dp_median, rs: opt.dp_median },
        { l: 'ΔP среднее', u: 'кгс/см²', b1: b1.dp_avg, b2: obs.dp_avg, r: adapt.dp_avg, rs: opt.dp_avg },
        { l: 'P трубное мед.', u: 'кгс/см²', b1: (b1.p_wellhead_median ?? b1.p_tube_median), b2: obs.p_tube_median, r: adapt.p_tube_median, rs: opt.p_tube_median },
        { l: 'P линейное мед.', u: 'кгс/см²', b1: (b1.p_flowline_median ?? b1.p_line_median), b2: obs.p_line_median, r: adapt.p_line_median, rs: opt.p_line_median },
        { section: 'Режим работы' },
        { l: 'КИВ', u: '%', b1: null, b2: obs.utilization_pct, r: adapt.utilization_pct, rs: opt.utilization_pct },
        { l: 'Простой', u: 'ч', b1: b1Shut, b2: obs.downtime_hours, r: adapt.downtime_hours, rs: opt.downtime_hours },
        { l: 'Вбросов реагента', u: 'шт', b1: null, b2: obs.reagent_count, r: adapt.reagent_count, rs: opt.reagent_count },
        { l: 'Продувок', u: 'шт', b1: null, b2: obs.purge_count, r: adapt.purge_count, rs: opt.purge_count },
        { l: 'Длительность', u: 'сут', b1: b1.days_count, b2: obs.duration_days, r: adapt.duration_days, rs: opt.duration_days },
      ];
      const lowerBetter = new Set(['ΔP медиана', 'ΔP среднее', 'Простой', 'Продувок']);
      const dCell = (cur, base, label) => {
        if (cur == null || base == null || base === 0 || isNaN(cur) || isNaN(base)) return ['—', '#6b7280'];
        const diff = cur - base, pct = diff / Math.abs(base) * 100;
        const better = lowerBetter.has(label) ? diff < 0 : diff > 0;
        const color = Math.abs(pct) < 5 ? '#6b7280' : (better ? '#16a34a' : '#dc2626');
        const s = diff >= 0 ? '+' : '';
        return [`${s}${diff.toFixed(2)} (${s}${pct.toFixed(1)}%)`, color];
      };
      const c = (v) => (v == null || isNaN(v)) ? '—' : fmt(v);
      let body = '';
      for (const it of cmpRows) {
        if (it.section) { body += `<tr><td colspan="8" style="padding:6px 8px; background:#f3f4f6; font-weight:700; font-size:0.78rem; text-transform:uppercase; color:#6b7280;">${it.section}</td></tr>`; continue; }
        const [d1, k1] = dCell(it.r, it.b2, it.l);
        const [d2, k2] = dCell(it.r, it.b1, it.l);
        const [d3, k3] = dCell(it.rs, it.b2, it.l);
        const td = 'text-align:right; padding:4px 8px; border-bottom:1px solid #eee;';
        body += `<tr>
          <td style="text-align:left; padding:4px 8px; border-bottom:1px solid #eee;">${it.l}<span style="color:#9ca3af; font-size:0.74rem;">, ${it.u}</span></td>
          <td style="${td}">${c(it.b1)}</td><td style="${td}">${c(it.b2)}</td>
          <td style="${td} font-weight:600;">${c(it.r)}</td><td style="${td} background:#fffbeb;">${c(it.rs)}</td>
          <td style="${td} color:${k1};">${d1}</td><td style="${td} color:${k2};">${d2}</td><td style="${td} color:${k3};">${d3}</td>
        </tr>`;
      }
      const th = 'padding:6px 8px; border-bottom:2px solid #d1d5db; font-size:0.74rem; text-align:right; color:#374151;';
      html += `<div style="font-size:0.82rem; color:#475569; line-height:1.5; margin:14px 0 4px;">Для оценки результатов адаптации показатели сравниваются с базовыми периодами: <b>B2</b> — этап наблюдения (до начала работ) и <b>B1</b> — данные заказчика (УзКорГаз). Это позволяет отделить технологический эффект от естественной динамики скважины и оценить как фактический режим адаптации (<b>R</b>), так и выбранное оптимальное окно (<b>R★</b>).</div>
      <h4 style="margin:8px 0 4px; font-size:0.95rem; color:#1f2937;">🎯 Сравнение метрик: Адаптация · R★ · B2 · B1</h4>
      <div style="font-size:0.78rem; color:#6b7280; margin-bottom:6px;">Каждая метрика R и R★ в контексте baseline'ов. Зелёный = улучшение, красный = деградация, серый = без значимого изменения (&lt;5%).</div>
      <div style="overflow-x:auto;"><table style="width:100%; font-size:0.8rem; border-collapse:collapse;">
        <thead><tr>
          <th style="${th} text-align:left;">Показатель</th>
          <th style="${th}">B1<br>УзКорГаз</th><th style="${th}">B2<br>наблюдение</th>
          <th style="${th}">R<br>адаптация</th><th style="${th} background:#fffbeb;">R★<br>оптимум</th>
          <th style="${th}">Δ R vs B2</th><th style="${th}">Δ R vs B1</th><th style="${th}">Δ R★ vs B2</th>
        </tr></thead><tbody>${body}</tbody></table></div>`;
      // Детальное пояснение баз сравнения и метрик (парность с PDF)
      const optDays = (opt.duration_days != null && !isNaN(opt.duration_days)) ? opt.duration_days : '~3';
      html += `<div style="font-size:0.8rem; color:#374151; line-height:1.5; margin:8px 0 4px; padding:10px 12px; background:#f9fafb; border:1px solid #e5e7eb; border-radius:8px;">
        <div style="font-weight:600; margin-bottom:3px;">Базы сравнения (что это за период):</div>
        <ul style="margin:0 0 6px; padding-left:18px;">
          <li><b>B1 — База заказчика:</b> официальные суточные показатели заказчика (УзКорГаз) до наблюдения — исходная точка «как было».</li>
          <li><b>B2 — Наблюдение:</b> период наблюдения без активного дозирования реагента — фон, относительно которого оценивается эффект адаптации.</li>
          <li><b>R — Адаптация:</b> весь период активного дозирования реагента.</li>
          <li><b>R★ — Оптимум:</b> лучший подпериод внутри адаптации (${optDays} сут.), выбранный скользящим окном по максимуму композитного Score (стабильность Q, прирост ΔP, КИВ, минимум простоев).</li>
        </ul>
        <div style="font-weight:600; margin-bottom:3px;">Метрики (как считаются на каждом периоде):</div>
        <ul style="margin:0 0 6px; padding-left:18px;">
          <li><b>Q медиана / среднее</b> — центральная / средняя суточная добыча газа (тыс.м³/сут) по суточным значениям периода; медиана устойчива к выбросам.</li>
          <li><b>Q максимум / накопл.</b> — наибольшая суточная и суммарная добыча за период.</li>
          <li><b>ΔP медиана / среднее</b> — перепад «трубное − линейное» (кгс/см²); в данной оценке снижение ΔP трактуется как улучшение.</li>
          <li><b>P трубное / линейное мед.</b> — медианные устьевое (трубное) и линейное давления (кгс/см²).</li>
          <li><b>КИВ, %</b> — коэффициент использования времени: доля рабочего времени без простоев за период.</li>
          <li><b>Простой, ч</b> — суммарная длительность простоев (меньше — лучше).</li>
          <li><b>Вбросов реагента / Продувок, шт</b> — число вбросов реагента и продувок за период.</li>
          <li><b>Длительность, сут</b> — длина соответствующего периода.</li>
        </ul>
        <div><b>Дельты:</b> Δ R vs B2 — эффект адаптации относительно наблюдения; Δ R vs B1 — относительно базы заказчика; Δ R★ vs B2 — эффект лучшего окна относительно наблюдения. Цвет: зелёный — улучшение, красный — деградация, серый — изменение &lt;5%. Для ΔP, простоев и продувок «улучшение» означает снижение.</div>
      </div>`;
    }

    // ═══════════════════════════════════════════════════════════════════
    // 8. ТРЕНДЫ
    // ═══════════════════════════════════════════════════════════════════
    if (on('trends') && (adapt.q_trend || adapt.dp_trend)) {
      html += `<div style="margin-top:14px; padding:10px 12px; background:#f0f9ff;
                          border-left:4px solid #0284c7; border-radius:4px;">
        <div style="font-weight:600; color:#0369a1; margin-bottom:8px; font-size:0.92rem;">
          📈 Тренды за период адаптации
        </div>
        <div style="font-size:0.84rem; display:grid; grid-template-columns:1fr 1fr; gap:10px;">
          ${adapt.q_trend ? `<div>
            <b>Q:</b> ${trendArrow(adapt.q_trend)} ${fmt(adapt.q_trend.slope_per_day, 3)} тыс.м³/сут/день
            ${adapt.q_trend.mk_significant ? '<span style="color:#16a34a; font-weight:600;">(знач.)</span>' : ''}
          </div>` : ''}
          ${adapt.dp_trend ? `<div>
            <b>ΔP:</b> ${trendArrow(adapt.dp_trend)} ${fmt(adapt.dp_trend.slope_per_day, 4)} кгс/см²/день
          </div>` : ''}
          ${adapt.p_tube_trend ? `<div>
            <b>P труб:</b> ${trendArrow(adapt.p_tube_trend)} ${fmt(adapt.p_tube_trend.slope_per_day, 3)} кгс/см²/день
          </div>` : ''}
          ${adapt.p_line_trend ? `<div>
            <b>P лин:</b> ${trendArrow(adapt.p_line_trend)} ${fmt(adapt.p_line_trend.slope_per_day, 3)} кгс/см²/день
          </div>` : ''}
        </div>
        <div style="font-size:0.78rem; color:#6b7280; margin-top:8px; line-height:1.45;">
          Тренд — наклон линейной регрессии показателя за период (изменение в сутки); «знач.» —
          статистически значимый тренд по критерию Манна–Кендалла. Рост Q и ΔP при снижении
          P трубного указывает на эффективную очистку ствола реагентом.
        </div>
      </div>`;
    }

    // ═══════════════════════════════════════════════════════════════════
    // 9. СЕГМЕНТНЫЙ АНАЛИЗ — УБРАН из отчёта (1.7/3.1).
    // Сегментный анализ — отдельный функционал (своя плитка/блок segment_analysis),
    // не должен дублироваться внутри «Полного анализа».
    // ═══════════════════════════════════════════════════════════════════

    // ═══════════════════════════════════════════════════════════════════
    // R★ ОПТИМАЛЬНОЕ ОКНО (отдельный блок если score высокий)
    // ═══════════════════════════════════════════════════════════════════
    if (opt && (opt.date_from || opt.flow_median) && opt.score != null) {
      const r = opt;
      html += `<div style="margin-top:16px; padding:12px 14px; background:#ecfdf5;
                          border:2px solid #22c55e; border-radius:8px;">
        <div style="font-weight:700; color:#166534; font-size:1rem; margin-bottom:6px;">
          ⭐ R★ Оптимальное окно
        </div>
        <div style="font-size:0.78rem; color:#4b5563; margin-bottom:8px; line-height:1.45;">
          Методика выбора: внутри этапа адаптации скользящим окном (≈3 сут.) ищется подпериод
          с наилучшим композитным Score (0–100), агрегирующим стабильность дебита (низкий CV),
          максимум Q, высокий КИВ и минимум продувок. Сравнение «до/после» ведётся именно по этому окну.
        </div>
        <div style="font-size:0.9rem; margin-bottom:8px;">
          <b>${_escHtml((r.date_from || '').slice(0, 10))}</b> — <b>${_escHtml((r.date_to || '').slice(0, 10))}</b>
          ${r.duration_label ? `<span style="color:#6b7280;"> (${_escHtml(r.duration_label)})</span>` : ''}
        </div>
        <div style="font-size:1.5rem; font-weight:700; color:#15803d; margin-bottom:10px;">
          Score: ${fmt(r.score, 1)}
        </div>
        <div style="display:grid; grid-template-columns:repeat(4, 1fr); gap:8px; font-size:0.82rem;">
          <div><span style="color:#6b7280;">Q мед:</span> <b>${fmt(r.flow_median)}</b> тыс.м³/сут</div>
          <div><span style="color:#6b7280;">ΔP:</span> <b>${fmt(r.dp_median)}</b> кгс/см²</div>
          <div><span style="color:#6b7280;">P тр:</span> ${fmt(r.p_tube_median)} кгс/см²</div>
          <div><span style="color:#6b7280;">КИВ:</span> ${fmt(r.utilization_pct, 1)}%</div>
        </div>
      </div>`;
    }

    // ═══════════════════════════════════════════════════════════════════
    // NARRATIVE (описание периода) — в конце
    // ═══════════════════════════════════════════════════════════════════
    if (adapt.narrative) {
      html += `<div style="margin-top:14px; padding:12px 14px; background:#f9fafb;
                          border-left:4px solid #6b7280; border-radius:4px;">
        <div style="font-size:0.84rem; line-height:1.6; white-space:pre-wrap; color:#4b5563;">${_escHtml(adapt.narrative)}</div>
      </div>`;
    }

    // ВЫВОДЫ — вынесены в отдельный блок adaptation_effectiveness (3.3),
    // см. _buildAdaptationEffectivenessHtml. Здесь больше не рендерятся.

    return html;
  }

  /**
   * _buildAdaptationEffectivenessHtml — ОТДЕЛЬНЫЙ блок «Оценка эффективности» (3.3).
   * snapshot: { optimal, observation, b1 } (+ params.recommendation).
   * Содержит: диаграмму B2↔R★ (adapt-{id}-eff), дельты, вердикт, ручные рекомендации.
   */
  function _buildAdaptationEffectivenessHtml(snap, block, parts) {
    snap = snap || {};
    parts = parts || {};
    const on = (k) => parts[k] !== false;  // настройки «что включать» (params.parts)
    const fmt = (v, d = 2) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d);
    const fmtSgn = (v, d = 2) => (v == null || isNaN(v)) ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(d);
    const opt = snap.optimal || {}, obs = snap.observation || {}, b1 = snap.b1 || {}, ad = snap.adaptation || {};
    const p = (block && block.params) || {};
    const bid = (block && block.id) || 'x';
    if (!(opt.flow_median != null || opt.score != null)) {
      return '<div style="color:#9ca3af; padding:12px;">Недостаточно данных для оценки эффективности.</div>';
    }
    const oq = opt.flow_median, bq = obs.flow_median;
    const odp = opt.dp_median, bdp = obs.dp_median;
    const ou = opt.utilization_pct, bu = obs.utilization_pct;
    const b1q = b1.flow_median || b1.q_total_median;

    const lines = []; let dq = null;
    if (oq != null && bq != null) {
      dq = oq - bq;
      const dqPct = bq ? (dq / bq * 100) : null;
      const pct = dqPct != null ? ` (${fmtSgn(dqPct, 1)}%)` : '';
      lines.push(`Дебит Q (медиана): наблюдение (B2) ${fmt(bq)} → оптимум (R★) ${fmt(oq)} — отклонение ${fmtSgn(dq)} тыс.м³/сут${pct}.`);
    }
    if (odp != null && bdp != null) lines.push(`Перепад ΔP: B2 ${fmt(bdp)} → R★ ${fmt(odp)} — отклонение ${fmtSgn(odp - bdp)} кгс/см².`);
    if (ou != null && bu != null) lines.push(`Рабочее время (КИВ): B2 ${fmt(bu, 1)}% → R★ ${fmt(ou, 1)}% — отклонение ${fmtSgn(ou - bu, 1)} п.п.`);
    if (oq != null && b1q != null) lines.push(`Сравнение с базой заказчика (B1): Q ${fmt(b1q)} → ${fmt(oq)} — отклонение ${fmtSgn(oq - b1q)} тыс.м³/сут.`);

    // Вердикт: ФАКТ (R адаптация vs B2) + накопленная добыча + ПОТЕНЦИАЛ (R★).
    // R — фактический режим с переходными процессами; R★ — потенциал (оптимистический).
    const EFF_DQ_GOOD = 3.0;
    const rq = (ad.flow_avg != null) ? ad.flow_avg : ad.flow_median;
    const bqAvg = (obs.flow_avg != null) ? obs.flow_avg : bq;
    const adDays = ad.duration_days || 0;
    const adCum = ad.flow_cumulative;
    const vparts = [];  // структурированное заключение: [{lead, body}]
    // 1. Фактический результат: всего газа за период + рост среднесуточного дебита + доп. добыча
    if (rq != null && bqAvg != null) {
      const dqf = rq - bqAvg;
      const pctf = bqAvg ? (dqf / bqAvg * 100) : null;
      let body = '';
      if (adCum != null && adDays) body += `За ${adDays} сут адаптации скважина дала ≈ ${fmt(adCum, 1)} тыс.м³ газа (накопленная добыча этапа). `;
      body += `Среднесуточный дебит вырос с ${fmt(bqAvg)} тыс.м³/сут (наблюдение, B2) до ${fmt(rq)} тыс.м³/сут (адаптация, R) — увеличение ${fmtSgn(dqf)} тыс.м³/сут${pctf != null ? ` (${fmtSgn(pctf, 1)}%)` : ''}.`;
      if (adDays) { const extra = dqf * adDays; body += ` Относительно режима наблюдения за этот период дополнительно получено ≈ ${fmt(extra, 1)} тыс.м³.`; }
      vparts.push({ lead: 'Фактический результат', body });
    }
    // 2. Переходные режимы — почему средний R занижен
    vparts.push({ lead: 'Переходные режимы', body: 'Период адаптации включает вывод скважины на установившийся режим, поэтому часть периода занимают переходные процессы — средний дебит R занижает достигнутый рабочий уровень.' });
    // 3. Оптимальное окно R★ — реально достигнутый максимум В ХОДЕ работ (не сценарий, не прогноз)
    if (dq != null) {
      const pctO = bq ? (dq / bq * 100) : null;
      vparts.push({ lead: 'Оптимальное окно', body: `R★ — окно наилучшей фактической работы скважины в ходе адаптации (реально достигнутый максимум, не прогноз). В этом окне среднесуточный дебит составил ${fmt(oq)} тыс.м³/сут — на ${fmtSgn(dq)} тыс.м³/сут${pctO != null ? ` (${fmtSgn(pctO, 1)}%)` : ''} выше наблюдения; это показывает устойчиво достижимый уровень режима${dq >= EFF_DQ_GOOD ? '. Технологический эффект значительный — рекомендуется продолжить работы' : ''}.` });
    }
    const recommend = (p.recommendation || '').trim();
    // Разделяем дельты: отдельно vs Наблюдение (B2) и vs Заказчик (B1).
    const linesB2 = [], linesB1 = [];
    if (oq != null && bq != null) {
      const pct = bq ? ` (${fmtSgn(dq / bq * 100, 1)}%)` : '';
      linesB2.push(`Дебит Q: ${fmt(bq)} → ${fmt(oq)} — ${fmtSgn(dq)} тыс.м³/сут${pct}.`);
    }
    if (odp != null && bdp != null) linesB2.push(`Перепад ΔP: ${fmt(bdp)} → ${fmt(odp)} — ${fmtSgn(odp - bdp)} кгс/см².`);
    if (ou != null && bu != null) linesB2.push(`Рабочее время (КИВ): ${fmt(bu, 1)}% → ${fmt(ou, 1)}% — ${fmtSgn(ou - bu, 1)} п.п.`);
    const b1dp = b1.dp_median;
    if (oq != null && b1q != null) {
      const pct = b1q ? ` (${fmtSgn((oq - b1q) / b1q * 100, 1)}%)` : '';
      linesB1.push(`Дебит Q: ${fmt(b1q)} → ${fmt(oq)} — ${fmtSgn(oq - b1q)} тыс.м³/сут${pct}.`);
    }
    if (odp != null && b1dp != null) linesB1.push(`Перепад ΔP: ${fmt(b1dp)} → ${fmt(odp)} — ${fmtSgn(odp - b1dp)} кгс/см².`);

    // ── Гейтинг по настройкам блока (params.parts) ──
    const showB2 = on('series_b2'), showB1 = on('series_b1');
    // Тексты описаний/пояснений — ЕДИНЫЙ источник с PDF (.tex). При правке текста
    // синхронно менять captions/intro в adaptation_report.tex (ветка effectiveness).
    const EFF_INTRO = 'Оценка эффективности сопоставляет ключевые показатели работы скважины по периодам: B1 — данные заказчика (УзКорГаз), B2 — наблюдение, R — адаптация, R★ — выбранное оптимальное окно. По каждому показателю видно изменение режима и технологический эффект перехода к оптимальному режиму R★.';
    const EFF_CAP_Q = 'На рисунке отображено сравнение показателей суточного дебита Q (тыс.м³/сут) по периодам. Чем выше столбец — тем больше добыча; рост к R★ соответствует положительному технологическому эффекту.';
    const EFF_CAP_DP = 'На рисунке отображено сравнение перепада давления ΔP (кгс/см²) по периодам. Рост ΔP при дозировании реагента — признак вспенивания и выноса воды из ствола скважины.';
    const EFF_CAP_DOWN = 'На рисунке отображено сравнение нерабочего времени / простоев (ч) по периодам. Меньше простоев — стабильнее и эффективнее работа скважины.';
    const cap = (txt) => on('captions') ? `<div style="font-size:0.8rem; color:#713f12; margin:2px 0 8px; line-height:1.45;">${_escHtml(txt)}</div>` : '';
    // Per-metric акцент рамки/заголовка — чтобы 3 графика не сливались (цвет
    // столбцов = период, а рамка/заголовок = показатель). Каждый — своя карточка.
    const _metCard = (acc, tint, icon, num, title, divId, capTxt) =>
      `<div style="border:1px solid ${tint.b}; border-left:4px solid ${acc}; background:${tint.bg}; border-radius:7px; padding:7px 10px 5px; margin-bottom:10px;">
        <div style="font-size:0.88rem; font-weight:700; color:${acc}; margin-bottom:2px;">${icon} ${num}. ${title}</div>
        <div id="${divId}" style="height:210px; width:100%;"></div>${capTxt}</div>`;
    const chartsHtml =
      (on('chart_q') ? _metCard('#4f46e5', { b: '#c7d2fe', bg: 'rgba(79,70,229,0.05)' }, '⛽', 1, 'Дебит Q (тыс.м³/сут) — больше лучше', `adapt-${bid}-eff-q`, cap(EFF_CAP_Q)) : '') +
      (on('chart_dp') ? _metCard('#0d9488', { b: '#99f6e4', bg: 'rgba(13,148,136,0.05)' }, '📉', 2, 'Перепад ΔP (кгс/см²) — рост = вынос воды', `adapt-${bid}-eff-dp`, cap(EFF_CAP_DP)) : '') +
      (on('chart_downtime') ? _metCard('#e11d48', { b: '#fecdd3', bg: 'rgba(225,29,72,0.05)' }, '⏱', 3, 'Нерабочее время / простой (ч) — меньше лучше', `adapt-${bid}-eff-down`, cap(EFF_CAP_DOWN)) : '');
    const colB2 = showB2 ? `<div><div style="font-weight:600; color:#713f12; font-size:0.86rem; margin-bottom:4px;">Относительно наблюдения (B2)</div>
          <ul style="margin:0; padding-left:18px; font-size:0.83rem; line-height:1.6; color:#713f12;">
            ${linesB2.map(l => `<li>${_escHtml(l)}</li>`).join('') || '<li>—</li>'}</ul></div>` : '';
    const colB1 = showB1 ? `<div><div style="font-weight:600; color:#713f12; font-size:0.86rem; margin-bottom:4px;">Относительно данных УзКорГаз (суточные сводки)</div>
          <ul style="margin:0; padding-left:18px; font-size:0.83rem; line-height:1.6; color:#713f12;">
            ${linesB1.map(l => `<li>${_escHtml(l)}</li>`).join('') || '<li>—</li>'}</ul></div>` : '';
    const nCols = (showB2 ? 1 : 0) + (showB1 ? 1 : 0);
    const deltasHtml = nCols ? `<div style="display:grid; grid-template-columns:${nCols === 2 ? '1fr 1fr' : '1fr'}; gap:12px;">${colB2}${colB1}</div>` : '';

    // ── Сводная таблица: столбцы по периодам, фильтр теми же series-тогглами,
    //    что и диаграммы (снятие B1/B2/R убирает столбец и здесь, и на графике) ──
    const b1down = (b1.shutdown_min_total != null) ? b1.shutdown_min_total / 60 : b1.downtime_hours;
    const tCols = [
      { on: on('series_b1'), label: 'УзКорГаз (сводки)', q: b1q,           dp: b1.dp_median,  down: b1down,             kiv: b1.utilization_pct,  score: b1.score },
      { on: on('series_b2'), label: 'B2 (Наблюдение)', q: obs.flow_median, dp: obs.dp_median, down: obs.downtime_hours, kiv: obs.utilization_pct, score: obs.score },
      { on: on('series_r'),  label: 'R (Адаптация)',   q: ad.flow_median,  dp: ad.dp_median,  down: ad.downtime_hours,  kiv: ad.utilization_pct,  score: ad.score },
      { on: true,            label: 'R★ (Оптимум)',    q: opt.flow_median, dp: opt.dp_median, down: opt.downtime_hours, kiv: opt.utilization_pct, score: opt.score },
    ].filter(c => c.on);
    let tableHtml = '';
    if (on('summary_table') && tCols.length) {
      const tRows = [['Дебит Q (рабочий), тыс.м³/сут', 'q', 2], ['Перепад ΔP, кгс/см²', 'dp', 2],
        ['Нерабочее время, ч', 'down', 1], ['КИВ (рабочее время), %', 'kiv', 1]];
      const head = tCols.map(c => `<th style="text-align:right;">${_escHtml(c.label)}</th>`).join('');
      let body = tRows.map(([lbl, key, d]) =>
        `<tr><td style="text-align:left;">${lbl}</td>${tCols.map(c => `<td style="text-align:right;">${fmt(c[key], d)}</td>`).join('')}</tr>`).join('');
      if (tCols.some(c => c.score != null && !isNaN(c.score))) {
        body += `<tr><td style="text-align:left;">Score (0–100)</td>${tCols.map(c => `<td style="text-align:right;">${(c.score == null || isNaN(c.score)) ? '—' : Number(c.score).toFixed(0)}</td>`).join('')}</tr>`;
      }
      tableHtml = `<table class="cd-table" style="font-size:0.82rem;width:100%;margin-bottom:12px;">
        <thead><tr><th style="text-align:left;">Показатель</th>${head}</tr></thead>
        <tbody>${body}</tbody></table>`;
    }

    let html = `<div style="margin-top:8px; padding:14px; background:#fef9c3; border:2px solid #facc15; border-radius:8px;">
      <div style="font-weight:700; color:#854d0e; margin-bottom:10px; font-size:0.95rem;">📊 Оценка эффективности</div>
      ${on('intro') ? `<div style="font-size:0.84rem; color:#713f12; margin-bottom:10px; line-height:1.5;">${_escHtml(EFF_INTRO)}</div>` : ''}
      ${tableHtml}
      ${chartsHtml ? `<div style="margin-bottom:12px;">${chartsHtml}</div>` : ''}
      ${deltasHtml}
      ${(on('verdict') && vparts.length) ? `<div style="margin-top:12px; padding:12px 14px; background:#fef9c3; border-left:4px solid #eab308; border-radius:6px; color:#713f12; font-size:0.88rem; line-height:1.5;">
        <div style="font-weight:700; margin-bottom:6px; font-size:0.92rem;">📝 Заключение по эффективности</div>
        ${vparts.map((pp) => `<p style="margin:6px 0;"><strong style="color:#854d0e;">${_escHtml(pp.lead)}.</strong> ${_escHtml(pp.body)}</p>`).join('')}
      </div>` : ''}
      ${(on('recommendation') && recommend) ? `<div style="margin-top:10px; padding:10px 12px; background:#fffbeb; border:1px dashed #d97706; border-radius:6px; font-size:0.85rem; color:#713f12;"><b>Рекомендации:</b> ${_escHtml(recommend)}</div>` : ''}
    </div>`;
    return html;
  }

  // ═══ period_comparison — сравнение метрик периода с выбранными источниками ═══
  // snapshot: { _v:'period_comparison_v1', current:{label,metrics,series},
  //            targets:[{key,label,metrics,series}], }
  // metrics: { q_median, q_avg, dp_median, p_tube_median, p_line_median,
  //            utilization_pct, downtime_hours }; series: { q:[...], dp:[...] }.
  // PARITY: эта же логика — в .tex (_attached_blocks.tex, ветка period_comparison)
  // и форматтере _format_period_block. Графики prcmp-{id}-q/-dp снимаются в PDF.
  const PCMP_ROWS = [
    ['Дебит Q (медиана), тыс.м³/сут', 'q_median', 2],
    ['Дебит Q (среднее), тыс.м³/сут', 'q_avg', 2],
    ['Перепад ΔP (медиана), кгс/см²', 'dp_median', 2],
    ['P трубное (медиана), кгс/см²', 'p_tube_median', 2],
    ['P линейное (медиана), кгс/см²', 'p_line_median', 2],
    ['КИВ (рабочее время), %', 'utilization_pct', 1],
    ['Простой, ч', 'downtime_hours', 1],
  ];
  const PCMP_LOWER_BETTER = { dp_median: 1, downtime_hours: 1 };

  function _pcmpDeltaCell(cur, val, d, lowerBetter) {
    if (cur == null || val == null || isNaN(cur) || isNaN(val) || cur === 0)
      return { txt: '—', color: '#6b7280' };
    const diff = val - cur;
    const pct = diff / Math.abs(cur) * 100;
    const better = lowerBetter ? (diff < 0) : (diff > 0);
    const color = Math.abs(pct) < 5 ? '#6b7280' : (better ? '#15803d' : '#b91c1c');
    const s = diff >= 0 ? '+' : '';
    return { txt: `${s}${diff.toFixed(d)} (${s}${pct.toFixed(1)}%)`, color };
  }

  function _pcmpTextLines(cur, targets, fmt) {
    const cm = cur.metrics || {};
    const lines = [];
    const sgn = (v, d = 2) => (v == null || isNaN(v)) ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(d);
    targets.forEach(t => {
      const tm = t.metrics || {};
      const parts = [];
      if (cm.q_median != null && tm.q_median != null) {
        const dd = cm.q_median - tm.q_median;
        const pct = tm.q_median ? ` (${sgn(dd / Math.abs(tm.q_median) * 100, 1)}%)` : '';
        parts.push(`дебит Q ${fmt(tm.q_median)} → ${fmt(cm.q_median)} (${sgn(dd)} тыс.м³/сут${pct})`);
      }
      if (cm.dp_median != null && tm.dp_median != null)
        parts.push(`перепад ΔP ${fmt(tm.dp_median)} → ${fmt(cm.dp_median)} (${sgn(cm.dp_median - tm.dp_median)} кгс/см²)`);
      if (cm.utilization_pct != null && tm.utilization_pct != null)
        parts.push(`КИВ ${fmt(tm.utilization_pct, 1)}% → ${fmt(cm.utilization_pct, 1)}% (${sgn(cm.utilization_pct - tm.utilization_pct, 1)} п.п.)`);
      if (parts.length) lines.push(`Относительно «${t.label}»: ${parts.join('; ')}.`);
    });
    return lines;
  }

  function _buildPeriodComparisonHtml(snap, block, parts) {
    parts = parts || {};
    const on = (k) => parts[k] !== false;
    const fmt = (v, d = 2) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d);
    const bid = (block && block.id) || 'x';
    const cur = (snap && snap.current) || null;
    const targets = (snap && snap.targets) || [];
    if (!cur || !targets.length) {
      return '<div style="color:#9ca3af; padding:12px;">Выберите хотя бы один источник для сравнения (наблюдение / адаптация / другой период).</div>';
    }
    const cm = cur.metrics || {};
    // Стиль/форма — как блок «Оценка эффективности» Адаптации (жёлтая карточка,
    // per-метрика карточки графиков, подписи, блок «Заключение»). Выбор источников
    // (динамические колонки) — наш, остаётся.
    const introTxt = `Текущий период «${cur.label || 'текущий'}» (медиана за период) сопоставлен с выбранными источниками: наблюдение (B2), оптимум R★ (адаптация), УзКорГаз (B1) и другие сохранённые периоды. В таблице справа от значения — отклонение (Δ, %) относительно текущего; на диаграммах столбец = источник.`;
    const introHtml = on('intro') ? `<div style="font-size:0.84rem; color:#713f12; margin-bottom:10px; line-height:1.5;">${_escHtml(introTxt)}</div>` : '';

    const _hcell = (label, sub) => `<th style="text-align:right;"><div>${_escHtml(label || '')}</div>${sub ? `<div style="font-weight:400; font-size:0.72rem; color:#a16207;">${_escHtml(sub)}</div>` : ''}</th>`;
    let tableHtml = '';
    if (on('summary_table')) {
      const head = `<th style="text-align:left;">Показатель</th>` +
        _hcell(cur.label || 'Текущий период', cur.sublabel) +
        targets.map(t => _hcell(t.label, t.sublabel)).join('');
      const body = PCMP_ROWS.map(([lbl, key, d]) => {
        const curV = cm[key];
        const cells = targets.map(t => {
          const v = (t.metrics || {})[key];
          const dc = _pcmpDeltaCell(curV, v, d, !!PCMP_LOWER_BETTER[key]);
          return `<td style="text-align:right;">${fmt(v, d)}<div style="font-size:0.72rem; color:${dc.color};">${dc.txt}</div></td>`;
        }).join('');
        return `<tr><td style="text-align:left;">${lbl}</td><td style="text-align:right; background:#fefce8; font-weight:600;">${fmt(curV, d)}</td>${cells}</tr>`;
      }).join('');
      tableHtml = `<table class="cd-table" style="font-size:0.82rem; width:100%; margin-bottom:12px;">
        <thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
    }

    // Per-метрика карточки графиков (зеркало _metCard в adaptation_effectiveness).
    const PCMP_CAP_Q = 'Сравнение суточного дебита Q (тыс.м³/сут) по источникам. Чем выше столбец — тем больше добыча.';
    const PCMP_CAP_DP = 'Сравнение перепада давления ΔP (кгс/см²) по источникам. Рост ΔP — признак выноса воды/вспенивания.';
    const PCMP_CAP_DOWN = 'Сравнение нерабочего времени / простоя (ч) по источникам. Меньше простоя — стабильнее работа.';
    const cap = (txt) => on('captions') ? `<div style="font-size:0.8rem; color:#713f12; margin:2px 0 8px; line-height:1.45;">${_escHtml(txt)}</div>` : '';
    const _metCard = (acc, tint, icon, num, title, divId, capTxt) =>
      `<div style="border:1px solid ${tint.b}; border-left:4px solid ${acc}; background:${tint.bg}; border-radius:7px; padding:7px 10px 5px; margin-bottom:10px;">
        <div style="font-size:0.88rem; font-weight:700; color:${acc}; margin-bottom:2px;">${icon} ${num}. ${title}</div>
        <div id="${divId}" style="height:230px; width:100%;"></div>${capTxt}</div>`;
    let chartsHtml = '';
    if (on('overlay_charts')) {
      chartsHtml =
        _metCard('#4f46e5', { b: '#c7d2fe', bg: 'rgba(79,70,229,0.05)' }, '⛽', 1, 'Дебит Q (тыс.м³/сут) — больше лучше', `prcmp-${bid}-q`, cap(PCMP_CAP_Q)) +
        _metCard('#0d9488', { b: '#99f6e4', bg: 'rgba(13,148,136,0.05)' }, '📉', 2, 'Перепад ΔP (кгс/см²) — рост = вынос воды', `prcmp-${bid}-dp`, cap(PCMP_CAP_DP)) +
        _metCard('#e11d48', { b: '#fecdd3', bg: 'rgba(225,29,72,0.05)' }, '⏱', 3, 'Нерабочее время / простой (ч) — меньше лучше', `prcmp-${bid}-down`, cap(PCMP_CAP_DOWN));
    }

    // Блок «Заключение» (жёлтый, как в Адаптации).
    let verdictHtml = '';
    if (on('description')) {
      const lines = _pcmpTextLines(cur, targets, fmt);
      if (lines.length) {
        verdictHtml = `<div style="margin-top:12px; padding:12px 14px; background:#fef9c3; border-left:4px solid #eab308; border-radius:6px; color:#713f12; font-size:0.88rem; line-height:1.5;">
          <div style="font-weight:700; margin-bottom:6px; font-size:0.92rem;">📝 Заключение по сравнению</div>
          <ul style="margin:0; padding-left:18px;">${lines.map(l => `<li>${_escHtml(l)}</li>`).join('')}</ul>
        </div>`;
      }
    }

    return `<div style="margin-top:8px; padding:14px; background:#fef9c3; border:2px solid #facc15; border-radius:8px;">
      <div style="font-weight:700; color:#854d0e; margin-bottom:10px; font-size:0.95rem;">📊 Оценка эффективности (сравнение периодов)</div>
      ${introHtml}
      ${tableHtml}
      ${chartsHtml ? `<div style="margin-bottom:4px;">${chartsHtml}</div>` : ''}
      ${verdictHtml}
    </div>`;
  }

  // Столбчатые диаграммы по метрикам (как adaptation_effectiveness): по одному
  // bar-чарту на метрику, столбцы = текущий период + выбранные источники.
  const PCMP_CHART_PALETTE = ['#4f46e5', '#f59e0b', '#94a3b8', '#3b82f6', '#22c55e', '#dc2626', '#0891b2', '#be185d'];
  function _renderPeriodComparisonCharts(snap, idPrefix) {
    if (!window.Plotly || !snap) return;
    const cur = snap.current; const targets = snap.targets || [];
    if (!cur) return;
    // Подпись столбца: имя сверху + даты мелким снизу (Plotly поддерживает <br>/<span>).
    const _catLabel = (s) => (s.label || '') + (s.sublabel ? `<br><span style="font-size:9px;color:#64748b">${s.sublabel}</span>` : '');
    const cats = [_catLabel(cur)].concat(targets.map(_catLabel));
    const colors = cats.map((_, i) => PCMP_CHART_PALETTE[i % PCMP_CHART_PALETTE.length]);
    const cfg = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ['lasso2d', 'select2d'] };
    const metricsArr = [cur.metrics || {}].concat(targets.map(t => t.metrics || {}));
    const plot = (suffix, key, unit, dec) => {
      const el = document.getElementById(idPrefix + suffix);
      if (!el) return;
      const v = metricsArr.map(m => { const x = m[key]; return (x == null || isNaN(x)) ? null : Number(x); });
      if (!v.some(x => x != null)) { el.innerHTML = '<div style="color:#9ca3af;font-size:0.8rem;text-align:center;padding-top:70px;">нет данных</div>'; return; }
      const traces = [{ type: 'bar', x: cats, y: v, marker: { color: colors }, width: 0.55,
        text: v.map(x => x == null ? '' : x.toFixed(dec)), textposition: 'outside', textfont: { size: 13 }, cliponaxis: false,
        hovertemplate: '%{x}<br>%{y:.' + dec + 'f} ' + unit + '<extra></extra>' }];
      const layout = { margin: { l: 56, r: 16, t: 18, b: 64 }, font: { size: 13 },
        yaxis: { title: { text: unit, font: { size: 12 } }, rangemode: 'tozero', tickfont: { size: 12 } },
        xaxis: { tickfont: { size: 11 }, automargin: true }, showlegend: false, bargap: 0.4,
        plot_bgcolor: '#fff', paper_bgcolor: '#fff' };
      try { Plotly.newPlot(el, traces, layout, cfg); } catch (e) { console.warn('prcmp bar ' + suffix, e); }
    };
    plot('q', 'q_median', 'тыс.м³/сут', 2);
    plot('dp', 'dp_median', 'кгс/см²', 2);
    plot('down', 'downtime_hours', 'ч', 1);
  }

  /**
   * _buildOptimalWindowHtml — ПОЛНЫЙ рендер блока optimal_window (R★)
   *
   * Снапшот: { regime, observation, b1 } — но regime содержит 45 ключей
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
    const trendArrow = (t) => {
      if (!t || !t.direction) return '→';
      return t.direction === 'up' ? '↑' : t.direction === 'down' ? '↓' : '→';
    };

    let html = '';

    // Цель раздела (парность с PDF)
    const _owFrom = (r.date_from || r.start || p.optimal_from || '').slice(0, 10);
    const _owTo = (r.date_to || r.end || p.optimal_to || '').slice(0, 10);
    html += `<div style="font-size:0.82rem; color:#475569; font-style:italic; line-height:1.5; margin-bottom:10px;">Цель раздела — выделить внутри этапа адаптации оптимальное окно работы R★ и зафиксировать его параметры как целевой режим. Окно выбирается по совокупному показателю эффективности (Score): максимум дебита при стабильном режиме (узкий разброс давлений) и минимуме простоев${(_owFrom && _owTo) ? `. Установлено в период ${_escHtml(_owFrom)} — ${_escHtml(_owTo)}` : ''}.</div>`;

    // ─── Заголовок с Score ───
    html += `<div style="padding:12px; background:linear-gradient(135deg, #ecfdf5 0%, #d1fae5 100%);
                        border:2px solid #22c55e; border-radius:10px; margin-bottom:12px;">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <div style="font-weight:700; color:#166534; font-size:1.1rem;">⭐ R★ Оптимальное окно</div>
          <div style="margin-top:4px; font-size:0.9rem; color:#15803d;">
            <b>${_escHtml((r.date_from || r.start || p.optimal_from || '').slice(0, 10))}</b> —
            <b>${_escHtml((r.date_to || r.end || p.optimal_to || '').slice(0, 10))}</b>
            ${r.duration_label ? ` (${_escHtml(r.duration_label)})` : r.duration_days ? ` (${r.duration_days} сут.)` : ''}
          </div>
        </div>
        ${r.score != null ? `<div style="text-align:center;">
          <div style="font-size:2rem; font-weight:800; color:#15803d;">${fmt(r.score, 1)}</div>
          <div style="font-size:0.75rem; color:#6b7280;">Score</div>
        </div>` : ''}
      </div>
    </div>`;

    // ─── ПОЛНАЯ таблица метрик окна ───
    if (on('metrics_table')) {
      html += `<table style="width:100%; font-size:0.84rem; border-collapse:collapse; border:1px solid #d1d5db;">
        <thead>
          <tr style="background:#f3f4f6;">
            <th style="text-align:left; padding:6px 10px; border-bottom:2px solid #d1d5db;">Параметр</th>
            <th style="text-align:right; padding:6px 10px; border-bottom:2px solid #d1d5db;">Значение</th>
          </tr>
        </thead>
        <tbody>
          <tr><td style="padding:5px 10px;">Q медианный, тыс.м³/сут</td>
              <td style="text-align:right; padding:5px 10px; font-weight:600;">${fmt(r.flow_median)}</td></tr>
          <tr style="background:#fafafa;"><td style="padding:5px 10px;">Q средний, тыс.м³/сут</td>
              <td style="text-align:right; padding:5px 10px;">${fmt(r.flow_avg)}</td></tr>
          <tr><td style="padding:5px 10px;">Q диапазон</td>
              <td style="text-align:right; padding:5px 10px;">${fmt(r.q_min)} — ${fmt(r.q_max)}</td></tr>
          <tr style="background:#fafafa;"><td style="padding:5px 10px;">ΔP медиана, кгс/см²</td>
              <td style="text-align:right; padding:5px 10px; font-weight:600;">${fmt(r.dp_median)}</td></tr>
          <tr><td style="padding:5px 10px;">P труб. медиана, кгс/см²</td>
              <td style="text-align:right; padding:5px 10px;">${fmt(r.p_tube_median)}</td></tr>
          <tr style="background:#fafafa;"><td style="padding:5px 10px;">P лин. медиана, кгс/см²</td>
              <td style="text-align:right; padding:5px 10px;">${fmt(r.p_line_median)}</td></tr>
          <tr><td style="padding:5px 10px;">КИВ, %</td>
              <td style="text-align:right; padding:5px 10px;">${fmt(r.utilization_pct, 1)}</td></tr>
          <tr style="background:#fafafa;"><td style="padding:5px 10px;">Рабочих часов</td>
              <td style="text-align:right; padding:5px 10px;">${fmt(r.working_hours, 1)}</td></tr>
          <tr><td style="padding:5px 10px;">Продувок</td>
              <td style="text-align:right; padding:5px 10px;">${r.purge_count || 0}</td></tr>
          <tr style="background:#fafafa;"><td style="padding:5px 10px;">Вбросов реагента</td>
              <td style="text-align:right; padding:5px 10px;">${r.reagent_count || 0}</td></tr>
        </tbody>
      </table>`;
    }

    // ─── Тренды ───
    if (r.q_trend || r.dp_trend) {
      html += `<div style="margin-top:12px; padding:8px 10px; background:#f0f9ff;
                          border-left:3px solid #0284c7; border-radius:4px;">
        <div style="font-weight:600; color:#0369a1; margin-bottom:4px;">📈 Тренды</div>
        <div style="font-size:0.82rem; display:flex; gap:16px; flex-wrap:wrap;">
          ${r.q_trend ? `<div><b>Q:</b> ${trendArrow(r.q_trend)} ${fmt(r.q_trend.slope_per_day, 3)}/день</div>` : ''}
          ${r.dp_trend ? `<div><b>ΔP:</b> ${trendArrow(r.dp_trend)} ${fmt(r.dp_trend.slope_per_day, 4)}/день</div>` : ''}
        </div>
      </div>`;
    }

    // ─── Score breakdown ───
    if (r.score_breakdown) {
      const sb = r.score_breakdown;
      html += `<div style="margin-top:12px; padding:8px 10px; background:#fef3c7;
                          border-left:3px solid #f59e0b; border-radius:4px;">
        <div style="font-weight:600; color:#92400e; margin-bottom:4px;">🎯 Компоненты Score</div>
        <div style="font-size:0.8rem; display:grid; grid-template-columns:repeat(3, 1fr); gap:6px;">
          ${sb.rank_q != null ? `<div>rank_q: ${fmt(sb.rank_q, 2)}</div>` : ''}
          ${sb.rank_stability != null ? `<div>stability: ${fmt(sb.rank_stability, 2)}</div>` : ''}
          ${sb.rank_utilization != null ? `<div>util: ${fmt(sb.rank_utilization, 2)}</div>` : ''}
          ${sb.rank_purge != null ? `<div>purge: ${fmt(sb.rank_purge, 2)}</div>` : ''}
          ${sb.cv_q != null ? `<div>CV(Q): ${fmt(sb.cv_q, 4)}</div>` : ''}
        </div>
      </div>`;
    }

    // ─── Narrative ───
    if (r.narrative) {
      html += `<div style="margin-top:12px; padding:10px 12px; background:#f9fafb;
                          border-left:3px solid #6b7280; border-radius:4px;">
        <div style="font-weight:600; color:#374151; margin-bottom:6px;">📝 Описание</div>
        <div style="font-size:0.84rem; line-height:1.5; white-space:pre-wrap; color:#4b5563;">${_escHtml(r.narrative)}</div>
      </div>`;
    }

    return html;
  }

  /**
   * _buildReagentIrvSummaryHtml — ПОЛНЫЙ рендер блока reagent_irv_summary
   *
   * Снапшот: { injections_total, irv_count, scores, best_reagent, irv_results? }
   */
  function _buildReagentIrvSummaryHtml(snap, block, parts) {
    snap = snap || {};
    const rows   = snap.irv_results || [];
    const scores = (snap.scores || []).slice().sort((a, b) => (b.score || 0) - (a.score || 0));
    if (!rows.length && !scores.length) {
      const msg = (snap.warnings && snap.warnings[0]) || 'Данные по реагентам отсутствуют.';
      return `<div style="color:#9ca3af; padding:12px;">${_escHtml(msg)}</div>`;
    }

    const fmt = (v, d = 2) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d);
    const on = (key) => parts[key] !== false;
    const plural = (n, a, b, c) => {
      const m10 = n % 10, m100 = n % 100;
      if (m10 === 1 && m100 !== 11) return a;
      if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return b;
      return c;
    };

    const best   = snap.best_reagent || null;   // объект {reagent, score, level_name, …}
    const choke  = snap.choke_mm;
    const totalInj  = snap.injections_total || rows.length;
    const mergedInj = snap.merged_injections || rows.length;

    // Цвета по реагентам (стабильная палитра)
    const PALETTE = ['#3b82f6', '#ef4444', '#16a34a', '#f59e0b', '#8b5cf6',
                     '#ec4899', '#0ea5e9', '#22c55e', '#d946ef', '#14b8a6'];
    const colorByReagent = {};
    [...new Set(rows.map(x => x.reagent).filter(Boolean))]
      .forEach((n, i) => { colorByReagent[n] = PALETTE[i % PALETTE.length]; });

    // Σ qty и счётчики по реагентам (из irv_results)
    const qtyByReagent = {}, cntByReagent = {};
    let totalQty = 0, qtyKnownCount = 0;
    for (const it of rows) {
      const name = it.reagent || '';
      if (!name) continue;
      cntByReagent[name] = (cntByReagent[name] || 0) + 1;
      if (it.qty != null && !isNaN(Number(it.qty))) {
        qtyByReagent[name] = (qtyByReagent[name] || 0) + Number(it.qty);
        totalQty += Number(it.qty); qtyKnownCount += 1;
      }
    }
    const someQty = qtyKnownCount > 0;

    // Период и темп
    const pf = (snap.period && snap.period.from) || (block && block.params && block.params.date_from);
    const pt = (snap.period && snap.period.to)   || (block && block.params && block.params.date_to);
    let periodDays = 0;
    if (pf && pt) periodDays = Math.max(1, (new Date(pt) - new Date(pf)) / 86400000);
    const ratePerDay = periodDays > 0 ? (totalInj / periodDays) : 0;

    // ─── Нарратив словами ───
    const narr = [];
    narr.push(`За период (${periodDays.toFixed(0)} сут.) сделано <b>${totalInj}</b> `
      + plural(totalInj, 'вброс', 'вброса', 'вбросов')
      + (mergedInj !== totalInj ? ` (после группировки близких — <b>${mergedInj}</b>)` : '') + '.');
    if (scores.length) {
      narr.push(`Применялось <b>${scores.length}</b> `
        + plural(scores.length, 'тип реагента', 'типа реагента', 'типов реагента') + '.');
      const byCnt = scores.slice().sort((a, b) => (b.irv_count || 0) - (a.irv_count || 0));
      const top1 = byCnt[0], top2 = byCnt[1];
      const sumCnt = scores.reduce((s, x) => s + (x.irv_count || 0), 0) || 1;
      if (top1) narr.push(`Чаще всего применялся <b>${_escHtml(top1.reagent)}</b> — ${top1.irv_count} ИРВ `
        + `(${(top1.irv_count / sumCnt * 100).toFixed(0)}%).`);
      if (top2 && top2.irv_count > 0) narr.push(`На втором месте — <b>${_escHtml(top2.reagent)}</b> `
        + `(${top2.irv_count} ИРВ, ${(top2.irv_count / sumCnt * 100).toFixed(0)}%).`);
    }
    if (someQty) narr.push(`Суммарный расход: <b>${totalQty.toFixed(1)}</b> `
      + (best && qtyByReagent[best.reagent] ? `(в т.ч. ${_escHtml(best.reagent)} — ${qtyByReagent[best.reagent].toFixed(1)})` : '') + '.');
    if (ratePerDay > 0) narr.push(`Темп: <b>${ratePerDay.toFixed(2)}</b> вбр./сут (≈ ${(ratePerDay * 7).toFixed(1)} вбр./нед).`);
    if (best) {
      const sc = best.score != null ? Math.round(best.score * 100) : null;
      narr.push(`По эффективности (Score) лидирует <b>${_escHtml(best.reagent)}</b>`
        + (sc != null ? ` (${sc} из 100)` : '')
        + (best.level_name ? `, уровень: «${_escHtml(best.level_name)}»` : '') + '.');
    }

    let html = '';

    // ─── Заголовок подраздела ───
    html += `<div style="font-weight:700; color:#92400e; font-size:0.95rem; margin-bottom:6px;">🧪 Анализ эффективности реагентов</div>`;
    // Цель раздела (парность с PDF)
    html += `<div style="font-size:0.82rem; color:#6b5b3e; font-style:italic; line-height:1.5; margin-bottom:10px;">Для оценки эффективности и выбора оптимального режима и типа реагента проведено исследование интервалов реагентного воздействия (ИРВ) — от вброса до следующего вброса/продувки. По каждому ИРВ рассчитаны расширенные метрики (прирост ΔP, удельный дебит, длительность эффекта и др.) и сводный показатель эффективности Score; выполнен анализ периодичности вбросов и динамики эффективности по реагентам.</div>`;

    // ─── Карточки: Лучший / Всего вбросов / Расход ───
    if (on('cards')) {
      const cardCss = 'background:#fff; border:1px solid #e5e7eb; border-radius:8px; padding:10px 12px; border-left:4px solid #d1d5db; min-width:0;';
      const grid = 'display:grid; grid-template-columns:1fr auto; gap:3px 10px; font-size:0.82rem;';
      html += `<div style="display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-bottom:12px;">
        <div style="${cardCss} border-left-color:#f59e0b; background:linear-gradient(120deg,#fef3c7,#fde68a);">
          <div style="font-size:0.74rem; font-weight:700; text-transform:uppercase; color:#92400e;">⭐ Лучший реагент</div>
          ${best ? `<div style="font-size:1.05rem; font-weight:700; color:#92400e; margin:2px 0 4px;">${_escHtml(best.reagent)}</div>
            <div style="${grid}">
              <span style="color:#6b7280;">Score</span><b>${best.score != null ? Math.round(best.score * 100) : '—'}</b>
              <span style="color:#6b7280;">Уровень</span><b>${_escHtml(best.level_name || '—')}</b>
              <span style="color:#6b7280;">Q/шт мед.</span><b>${fmt(best.median_q_per_unit, 2)}</b>
              <span style="color:#6b7280;">Эффект, ч</span><b>${fmt(best.median_effect_hours, 1)}</b>
            </div>` : '<div style="color:#9ca3af;">Не определён</div>'}
        </div>
        <div style="${cardCss} border-left-color:#7c3aed; background:#f5f3ff;">
          <div style="font-size:0.74rem; font-weight:700; text-transform:uppercase; color:#5b21b6;">Всего вбросов</div>
          <div style="${grid} margin-top:4px;">
            <span style="color:#6b7280;">Событий</span><b>${totalInj}</b>
            <span style="color:#6b7280;">После группировки</span><b>${mergedInj}</b>
            <span style="color:#6b7280;">Типов реагентов</span><b>${scores.length}</b>
            ${ratePerDay > 0 ? `<span style="color:#6b7280;">Темп, вбр./сут</span><b>${ratePerDay.toFixed(2)}</b>` : ''}
          </div>
        </div>
        <div style="${cardCss} border-left-color:#059669; background:#ecfdf5;">
          <div style="font-size:0.74rem; font-weight:700; text-transform:uppercase; color:#065f46;">Расход</div>
          <div style="${grid} margin-top:4px;">
            <span style="color:#6b7280;">Σ qty</span><b>${someQty ? totalQty.toFixed(1) : '— нет qty —'}</b>
            ${choke != null ? `<span style="color:#6b7280;">Штуцер, мм</span><b>${fmt(choke, 1)}</b>` : ''}
          </div>
        </div>
      </div>`;
    }

    // ─── Описание словами ───
    if (on('narrative') && narr.length) {
      html += `<div style="background:#fff; border:1px solid #ddd6fe; border-radius:8px; padding:12px 14px; margin-bottom:12px;">
        <div style="font-size:0.9rem; color:#4b5563; line-height:1.7;">
          ${narr.map(s => `<div style="margin:4px 0;">▸ ${s}</div>`).join('')}
        </div>
      </div>`;
    }

    // ─── Шкала эффективности реагентов (главная таблица) ───
    if (on('scores_table') && scores.length) {
      const th = 'padding:6px 8px; border-bottom:2px solid #d1d5db; text-align:right; font-size:0.72rem; text-transform:uppercase; color:#6b7280; background:#f9fafb;';
      const thL = th + ' text-align:left;';
      const td = 'padding:5px 8px; border-bottom:1px solid #eee; text-align:right;';
      const tdL = td + ' text-align:left;';
      let body = '';
      for (const s of scores) {
        const sc  = s.score != null ? s.score.toFixed(2) : '—';
        const cnt = cntByReagent[s.reagent] ?? s.irv_count;
        const qty = qtyByReagent[s.reagent];
        const sw  = colorByReagent[s.reagent] || '#9ca3af';
        const degr = s.degradation_slope != null
          ? `<span style="color:${s.degradation_slope < 0 ? '#b91c1c' : '#059669'};">${s.degradation_slope >= 0 ? '+' : ''}${s.degradation_slope.toFixed(3)}/ИРВ</span>`
          : '—';
        const flags = (s.flags || []).map(f =>
          `<span style="display:inline-block; margin:1px; padding:1px 6px; border-radius:8px; background:#f3f4f6; color:#4b5563; font-size:0.72rem;">${_escHtml(f)}</span>`).join('');
        body += `<tr>
          <td style="${tdL}"><span style="display:inline-block; width:10px; height:10px; border-radius:50%; background:${sw}; margin-right:6px; vertical-align:middle;"></span><b>${_escHtml(s.reagent)}</b></td>
          <td style="${td}">${cnt ?? s.irv_count}</td>
          <td style="${td}">${s.valid_irv_count ?? '—'}</td>
          <td style="${td}">${qty != null ? qty.toFixed(1) : '—'}</td>
          <td style="${td}">${fmt(s.median_q_per_unit, 3)}</td>
          <td style="${td}">${fmt(s.median_dp_gain, 2)}</td>
          <td style="${td}">${fmt(s.median_effect_hours, 1)}</td>
          <td style="${td}"><b>${sc}</b></td>
          <td style="${td}">${_escHtml(s.level_name || '—')}</td>
          <td style="${td}">${degr}</td>
          <td style="${tdL} font-size:0.74rem;">${flags}</td>
        </tr>`;
      }
      html += `<div style="font-weight:600; color:#5b21b6; margin:6px 0 2px;">Табл. Шкала эффективности реагентов</div>
        <div style="font-size:0.78rem; color:#6b7280; margin-bottom:6px; line-height:1.45;">
          Метод ИРВ (Интервал Реагентного Воздействия): анализируется каждый отрезок «от вброса до следующего вброса/продувки». По каждому реагенту считается сводный <b>Score (0–100)</b>, агрегирующий расширенные метрики. Пояснение колонок:
          <b>Вбросов</b> — число ИРВ; <b>Валидных</b> — пригодных к расчёту; <b>Σ qty</b> — суммарный расход;
          <b>Q/шт мед.</b> — медианный прирост дебита на единицу реагента (тыс.м³/сут);
          <b>ΔP прирост</b> — медианный рост перепада после вброса (кгс/см²);
          <b>Длит. эффекта</b> — медианная длительность эффекта (ч);
          <b>Уровень</b> — качественная оценка по Score; <b>Деградация</b> — тренд Score от вброса к вбросу;
          <b>Флаги</b> — предупреждения (мало данных, нестабильность и т.п.).
        </div>
        <div style="overflow-x:auto; margin-bottom:8px;">
        <table style="width:100%; border-collapse:collapse; font-size:0.82rem;">
          <thead><tr>
            <th style="${thL}">Реагент</th><th style="${th}">Вбросов</th><th style="${th}">Валидных</th>
            <th style="${th}">Σ qty</th><th style="${th}">Q/шт мед.</th><th style="${th}">ΔP прирост</th>
            <th style="${th}">Длит. эффекта</th><th style="${th}">Score</th><th style="${th}">Уровень</th>
            <th style="${th}">Деградация</th><th style="${thL}">Флаги</th>
          </tr></thead><tbody>${body}</tbody>
        </table></div>`;

      // ─── Как считается Score (методика, парность с PDF) ───
      html += `<div style="margin:8px 0 10px; padding:10px 12px; background:#faf5ff; border:1px solid #e9d5ff; border-radius:8px; font-size:0.8rem; color:#374151; line-height:1.5;">
        <b>Как считается Score (оценка эффективности реагента).</b> Score — единая оценка по каждому ИРВ (от вброса до следующего), агрегирующая <b>7 метрик</b> формы кривой ΔP с весами (Σ весов = 1):
        <ul style="margin:4px 0; padding-left:18px;">
          <li><b>Прирост ΔP_peak</b> (вес 0.25) — макс. рост перепада над фоном (0–3 кгс/см²); больше — лучше.</li>
          <li><b>Длительность плато</b> (0.20) — суммарное время стабильного действия (0–6 ч); больше — лучше.</li>
          <li><b>Длительность спада</b> (0.15) — суммарное время затухания (0–12 ч); дольше — лучше.</li>
          <li><b>Скорость спада</b> (0.15) — |наклон| затухания (0–1 кгс/см²/ч); меньше — лучше.</li>
          <li><b>Время отклика</b> (0.10) — от вброса до начала роста (0–2 ч); меньше — лучше.</li>
          <li><b>Скорость роста</b> (0.10) — наклон первого подъёма (0–1 кгс/см²/ч); больше — лучше.</li>
          <li><b>КИВ</b> (0.05) — доля рабочего времени (0–100%); больше — лучше.</li>
        </ul>
        Каждая метрика нормируется в [0..1], затем Score = 100 × Σ(вес × норм. значение). Пороги: <b style="color:#16a34a;">≥70 — хороший</b>, <b style="color:#ca8a04;">45–70 — средний</b>, <b style="color:#dc2626;">&lt;45 — слабый</b>.
      </div>`;
    }

    // ─── Анализ периодичности вбросов (2.7) + диаграмма интервалов (2.8) ───
    {
      const _bid = (block && block.id) || 'x';
      const _times = rows.map(r => r.event_time).filter(Boolean)
        .map(t => new Date(t).getTime()).filter(x => !isNaN(x)).sort((a, b) => a - b);
      const fmtH = (h) => h >= 24 ? (h / 24).toFixed(2) + ' сут' : h.toFixed(1) + ' ч';
      if (on('periodicity_table') && _times.length >= 2) {
        const iv = []; for (let i = 1; i < _times.length; i++) iv.push((_times[i] - _times[i - 1]) / 3600000);
        const n = iv.length, mean = iv.reduce((s, x) => s + x, 0) / n;
        const srt = iv.slice().sort((a, b) => a - b);
        const median = n % 2 ? srt[(n - 1) / 2] : (srt[n / 2 - 1] + srt[n / 2]) / 2;
        const sd = Math.sqrt(iv.reduce((s, x) => s + (x - mean) ** 2, 0) / n);
        const cv = mean > 0 ? sd / mean : 0;
        const regime = cv < 0.3 ? 'регулярный' : cv < 0.6 ? 'умеренно нерегулярный' : 'нерегулярный';
        const chip = (l, v) => `<span style="margin-right:16px; white-space:nowrap;"><b>${l}:</b> ${v}</span>`;
        // Детальная таблица интервалов (от вброса до следующего)
        const srtRows = rows.filter(r => r.event_time).slice()
          .sort((a, b) => new Date(a.event_time) - new Date(b.event_time));
        let ivBody = '';
        for (let i = 0; i < srtRows.length - 1; i++) {
          const t0 = (srtRows[i].event_time || '').replace('T', ' ').slice(0, 16);
          const t1 = (srtRows[i + 1].event_time || '').replace('T', ' ').slice(0, 16);
          const h = (new Date(srtRows[i + 1].event_time) - new Date(srtRows[i].event_time)) / 3600000;
          ivBody += `<tr><td style="padding:3px 8px; border-bottom:1px solid #eee;">${i + 1}</td>
            <td style="padding:3px 8px; border-bottom:1px solid #eee; font-family:monospace;">${_escHtml(t0)}</td>
            <td style="padding:3px 8px; border-bottom:1px solid #eee; font-family:monospace;">${_escHtml(t1)}</td>
            <td style="padding:3px 8px; border-bottom:1px solid #eee;">${_escHtml(srtRows[i].reagent || '—')} → ${_escHtml(srtRows[i + 1].reagent || '—')}</td>
            <td style="padding:3px 8px; border-bottom:1px solid #eee; text-align:right; font-weight:600;">${fmtH(h)}</td></tr>`;
        }
        html += `<div style="margin:10px 0; padding:10px 12px; background:#faf5ff; border:1px solid #e9d5ff; border-radius:8px;">
          <div style="font-weight:600; color:#5b21b6; margin-bottom:2px;">⏱ Табл. Анализ периодичности вбросов</div>
          <div style="font-size:0.78rem; color:#6b7280; margin-bottom:6px;">${n} интервалов между ${_times.length} вбросами. CV = σ/среднее — мера регулярности дозирования: режим <b>${regime}</b>.</div>
          <div style="font-size:0.84rem; color:#374151; margin-bottom:6px;">${chip('Среднее', fmtH(mean))}${chip('Медиана', fmtH(median))}${chip('Мин', fmtH(srt[0]))}${chip('Макс', fmtH(srt[n - 1]))}${chip('σ', fmtH(sd))}${chip('CV', cv.toFixed(2))}</div>
          <details><summary style="cursor:pointer; color:#5b21b6; font-size:0.82rem;">Полный список интервалов (${n})</summary>
            <table style="width:100%; border-collapse:collapse; font-size:0.8rem; margin-top:6px;">
              <thead><tr style="background:#f5f3ff;"><th style="padding:4px 8px; text-align:left;">№</th><th style="padding:4px 8px; text-align:left;">От вброса</th><th style="padding:4px 8px; text-align:left;">До вброса</th><th style="padding:4px 8px; text-align:left;">Реагент</th><th style="padding:4px 8px; text-align:right;">Интервал</th></tr></thead>
              <tbody>${ivBody}</tbody></table></details>
        </div>`;
      }
      if (on('intervals_chart') && _times.length >= 2) {
        html += `<div style="margin-bottom:10px;">
          <div style="font-weight:600; color:#5b21b6; font-size:0.9rem; margin-bottom:4px;">Диаграмма интервалов между вбросами</div>
          <div id="irv-${_bid}-intervals" style="height:260px;"></div>
          <div style="font-size:0.78rem; color:#6b7280; margin-top:4px;">Ступенчатая линия — длительность интервала (ч) между последовательными вбросами по их номеру; точки окрашены по реагенту начала интервала. Высокая неравномерность ступеней = нерегулярный режим дозирования.</div>
        </div>`;
      }
    }

    // ─── Детально по ИРВ (раскрывающаяся таблица) ───
    if (on('detailed') && rows.length) {
      const th = 'padding:5px 8px; border-bottom:2px solid #d1d5db; text-align:right; font-size:0.72rem; text-transform:uppercase; color:#6b7280; background:#f9fafb;';
      const thL = th + ' text-align:left;';
      const td = 'padding:4px 8px; border-bottom:1px solid #eee; text-align:right;';
      const tdL = td + ' text-align:left;';
      let body = '';
      for (const it of rows) {
        const m = it.metrics || {};
        const t = (it.event_time || '').replace('T', ' ').slice(0, 16);
        const inv = m.invalid_reason;
        const ext = it.extended || {};
        const irvScore = ext.score != null ? Math.round(ext.score) : null;
        const cat = ext.category;
        const catColor = cat === 'good' ? '#16a34a' : cat === 'average' ? '#ca8a04' : cat === 'weak' ? '#dc2626' : '#9ca3af';
        const sw = colorByReagent[it.reagent] || '#9ca3af';
        body += `<tr>
          <td style="${tdL} font-family:monospace;">${_escHtml(t)}</td>
          <td style="${tdL}"><span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:${sw}; margin-right:5px; vertical-align:middle;"></span>${_escHtml(it.reagent || '—')}</td>
          <td style="${td}">${it.qty != null ? it.qty : '—'}${it.merged_count > 1 ? ` <span style="color:#9ca3af; font-size:0.72rem;">(${it.merged_count})</span>` : ''}</td>
          <td style="${td}">${fmt(it.duration_hours, 1)}</td>
          <td style="${td}">${fmt(m.q_per_unit, 2)}</td>
          <td style="${td}">${fmt(m.dp_gain, 2)}</td>
          <td style="${td}">${fmt(m.effect_duration_hours, 1)}</td>
          <td style="${td}">${fmt(m.utilisation_pct, 1)}</td>
          <td style="${td}">${fmt(m.q_cumulative, 2)}</td>
          <td style="${td}">${irvScore != null ? `<span style="background:${catColor}; color:#fff; padding:2px 6px; border-radius:8px; font-weight:600;">${irvScore}</span>` : '—'}</td>
          <td style="${tdL}">${inv ? `<span style="color:#b91c1c; font-size:0.74rem;">${_escHtml(inv)}</span>` : '<span style="color:#059669;">✓</span>'}</td>
        </tr>`;
      }
      html += `<details style="margin-bottom:8px;">
        <summary style="cursor:pointer; color:#5b21b6; font-weight:500; font-size:0.86rem;">Детализация по интервалам реагентного воздействия (ИРВ) — развернуть (${rows.length})</summary>
        <div style="overflow-x:auto; max-height:400px; overflow-y:auto; margin-top:6px;">
        <table style="width:100%; border-collapse:collapse; font-size:0.8rem;">
          <thead><tr>
            <th style="${thL}">Дата вброса</th><th style="${thL}">Реагент</th><th style="${th}">qty</th>
            <th style="${th}">Длит. ИРВ, ч</th><th style="${th}">Q/шт</th><th style="${th}">ΔP gain</th>
            <th style="${th}">Эффект, ч</th><th style="${th}">КИВ, %</th><th style="${th}">Σ Q, тыс.м³</th>
            <th style="${th}">Score</th><th style="${thL}">Статус</th>
          </tr></thead><tbody>${body}</tbody>
        </table></div></details>`;
      html += `<div style="font-size:0.78rem; color:#6b7280; margin:-2px 0 8px; line-height:1.45;">
        <i>Колонка «Статус»:</i> <span style="color:#059669;">✓</span> — ИРВ <b>валиден</b> (пригоден к расчёту Score и вошёл в сводную оценку реагента). Если указана причина («мало данных», «нет роста ΔP», «короткий интервал») — ИРВ <b>исключён</b> из расчёта Score. Критерии валидности — в методике расчёта реагентов.
      </div>`;
    }

    // ─── Графики: вбросы по реагентам (donut) + Score по времени ───
    if (on('charts') && (scores.length || rows.length)) {
      const bid = (block && block.id) || 'x';
      // Круговая — компактно по центру; Score — на всю ширину (так PNG-снимок
      // получается широким и чётким при выводе в PDF на всю ширину страницы).
      html += `<div style="margin-bottom:10px;">
          <div style="font-weight:600; color:#5b21b6; font-size:0.9rem; margin-bottom:4px;">Вбросы по реагентам</div>
          <div id="irv-${bid}-pie" style="height:300px; max-width:460px; margin:0 auto;"></div>
          <div style="font-size:0.78rem; color:#6b7280; margin-top:4px;">Круговая диаграмма: доля каждого реагента в суммарном расходе (Σ qty); подписи — число ИРВ. Показывает структуру применения реагентов.</div></div>
        <div style="margin-bottom:8px;">
          <div style="font-weight:600; color:#5b21b6; font-size:0.9rem; margin-bottom:4px;">Эффективность вбросов (Score по времени)</div>
          <div id="irv-${bid}-score" style="height:300px; width:100%;"></div>
          <div style="font-size:0.78rem; color:#6b7280; margin-top:4px;">Столбики — Score (0–100) каждого вброса во времени, цвет по реагенту; пунктир: 70 — «хороший», 45 — «средний». Динамика качества вбросов.</div></div>`;
    }

    return html;
  }

  // Рендер Plotly-графиков ИРВ (donut + Score по времени) — из снапшота.
  function _renderReagentIrvCharts(snap, idPrefix) {
    if (!window.Plotly || !snap) return;
    const rows   = snap.irv_results || [];
    const scores = (snap.scores || []).slice().sort((a, b) => (b.score || 0) - (a.score || 0));
    if (!rows.length && !scores.length) return;

    const PALETTE = ['#3b82f6', '#ef4444', '#16a34a', '#f59e0b', '#8b5cf6',
                     '#ec4899', '#0ea5e9', '#22c55e', '#d946ef', '#14b8a6'];
    const reagentNames = [...new Set(rows.map(x => x.reagent).filter(Boolean))];
    const colorByReagent = {};
    reagentNames.forEach((n, i) => { colorByReagent[n] = PALETTE[i % PALETTE.length]; });

    const qtyByReagent = {}, cntByReagent = {};
    let totalQty = 0, qtyKnownCount = 0;
    for (const it of rows) {
      const name = it.reagent || ''; if (!name) continue;
      cntByReagent[name] = (cntByReagent[name] || 0) + 1;
      if (it.qty != null && !isNaN(Number(it.qty))) {
        qtyByReagent[name] = (qtyByReagent[name] || 0) + Number(it.qty);
        totalQty += Number(it.qty); qtyKnownCount += 1;
      }
    }
    const someQty = qtyKnownCount > 0;
    const totalInj = snap.injections_total || rows.length;

    // 1) Donut
    const pieEl = document.getElementById(idPrefix + 'pie');
    if (pieEl && scores.length) {
      const labels = scores.map(s => s.reagent);
      const cnts   = scores.map(s => cntByReagent[s.reagent] ?? s.irv_count ?? 0);
      const qtys   = scores.map(s => qtyByReagent[s.reagent] ?? 0);
      const values = someQty ? qtys : cnts;
      try {
        window.Plotly.newPlot(pieEl, [{
          type: 'pie', hole: 0.45, labels: labels, values: values,
          marker: { colors: labels.map(n => colorByReagent[n] || '#9ca3af') },
          text: cnts.map(c => c + ' ИРВ'), textinfo: 'label+percent', textposition: 'inside',
          hovertemplate: '<b>%{label}</b><br>%{value} ' + (someQty ? '(qty)' : 'ИРВ') + '<br>%{percent}<extra></extra>',
        }], {
          margin: { l: 10, r: 10, t: 10, b: 10 }, showlegend: false, font: { size: 11 },
          annotations: [{ font: { size: 13 }, showarrow: false,
            text: someQty ? ('Σ qty<br>' + totalQty.toFixed(0)) : (totalInj + ' вбр.'), x: 0.5, y: 0.5 }],
        }, { displaylogo: false, responsive: true });
      } catch (e) { /* no-op */ }
    }

    // 2) Score по времени (bar по реагентам)
    const scEl = document.getElementById(idPrefix + 'score');
    if (scEl && rows.length) {
      const traces = reagentNames.map(name => {
        const items = rows.filter(r => r.reagent === name);
        return {
          type: 'bar', name: name,
          x: items.map(it => it.event_time),
          y: items.map(it => (it.extended && it.extended.score) != null ? it.extended.score : null),
          marker: { color: colorByReagent[name] },
          hovertemplate: '<b>' + name + '</b><br>%{x}<br>Score: %{y}<extra></extra>',
        };
      });
      try {
        window.Plotly.newPlot(scEl, traces, {
          margin: { l: 48, r: 14, t: 10, b: 70 }, bargap: 0.3,
          xaxis: { type: 'date', title: { text: 'Дата вброса', font: { size: 12 }, standoff: 8 }, tickfont: { size: 11 } },
          yaxis: { title: { text: 'Score (0–100)', font: { size: 12 } }, range: [0, 100], tickfont: { size: 11 } },
          font: { size: 12 }, legend: { orientation: 'h', y: -0.32, x: 0.5, xanchor: 'center', font: { size: 11 } },
          shapes: [
            { type: 'line', xref: 'paper', x0: 0, x1: 1, y0: 70, y1: 70, line: { color: '#16a34a', dash: 'dash', width: 1.5 } },
            { type: 'line', xref: 'paper', x0: 0, x1: 1, y0: 45, y1: 45, line: { color: '#ca8a04', dash: 'dash', width: 1.5 } },
          ],
          annotations: [
            { x: 1, xref: 'paper', y: 70, text: '70 — хороший', showarrow: false, xanchor: 'right', yanchor: 'bottom', font: { size: 11, color: '#16a34a' }, bgcolor: 'rgba(255,255,255,0.85)' },
            { x: 1, xref: 'paper', y: 45, text: '45 — средний', showarrow: false, xanchor: 'right', yanchor: 'bottom', font: { size: 11, color: '#ca8a04' }, bgcolor: 'rgba(255,255,255,0.85)' },
          ],
        }, { displaylogo: false, responsive: true });
      } catch (e) { /* no-op */ }
    }

    // 3) Интервалы между вбросами — СТУПЕНЧАТЫЙ график (stair), отличный по
    // стилю от столбчатого Score-графика. Линия-ступень = интервал по № вброса;
    // точки окрашены по реагенту начала интервала. — 2.8
    const ivEl = document.getElementById(idPrefix + 'intervals');
    if (ivEl && rows.length >= 2) {
      const srt = rows.filter(r => r.event_time).slice()
        .sort((a, b) => new Date(a.event_time) - new Date(b.event_time));
      const xs = [], ys = [];
      const byReagent = {};  // реагент → точки (для легенды по цвету)
      for (let i = 0; i < srt.length - 1; i++) {
        const h = (new Date(srt[i + 1].event_time) - new Date(srt[i].event_time)) / 3600000;
        if (isNaN(h)) continue;
        const name = srt[i].reagent || '—';
        xs.push(i + 1); ys.push(h);
        const g = (byReagent[name] = byReagent[name] || { x: [], y: [], hov: [] });
        g.x.push(i + 1); g.y.push(h);
        g.hov.push(`${name}<br>${(srt[i].event_time || '').slice(0, 16)}<br>интервал ${h.toFixed(1)} ч`);
      }
      // Ступенчатая линия (без легенды) + точки по реагентам (с легендой по цвету).
      const ivTraces = [{
        type: 'scatter', mode: 'lines', x: xs, y: ys,
        line: { shape: 'hv', color: '#7c3aed', width: 2 },
        fill: 'tozeroy', fillcolor: 'rgba(124,58,237,0.08)',
        showlegend: false, hoverinfo: 'skip',
      }];
      for (const name of Object.keys(byReagent)) {
        const g = byReagent[name];
        ivTraces.push({
          type: 'scatter', mode: 'markers', name: name, x: g.x, y: g.y,
          marker: { color: colorByReagent[name] || '#9ca3af', size: 10, line: { color: '#fff', width: 1 } },
          text: g.hov, hovertemplate: '%{text}<extra></extra>',
        });
      }
      try {
        window.Plotly.newPlot(ivEl, ivTraces, {
          margin: { l: 52, r: 12, t: 12, b: 64 },
          xaxis: { title: { text: '№ интервала между вбросами', font: { size: 12 } }, tickfont: { size: 11 }, dtick: 1 },
          yaxis: { title: { text: 'Интервал, ч', font: { size: 12 } }, tickfont: { size: 11 }, rangemode: 'tozero' },
          font: { size: 12 }, showlegend: true,
          legend: { orientation: 'h', y: -0.28, x: 0.5, xanchor: 'center', font: { size: 11 },
                    title: { text: 'Реагент начала интервала:', font: { size: 11 } } },
        }, { displaylogo: false, responsive: true });
      } catch (e) { /* no-op */ }
    }
  }

  // Блок «Оценка эффективности»: 3 ОТДЕЛЬНЫХ графика (разные размерности) —
  // Дебит Q, Перепад ΔP, Нерабочее время — столбики по B1/B2/R/R★.
  function _renderEffectivenessChart(snap, idPrefix, parts) {
    if (!window.Plotly || !snap) return;
    parts = parts || {};
    const on = (k) => parts[k] !== false;
    const opt = snap.optimal || {}, obs = snap.observation || {},
          b1 = snap.b1 || {}, ad = snap.adaptation || {};
    const disp = snap.display || {};
    // Тип: bar | lollipop | dot | diff (разница/эффект). Совместимость: старые
    // hbar/vbar = bar с заданной ориентацией.
    const style = disp.eff_chart_style || 'hbar';
    const baseStyle = (style === 'hbar' || style === 'vbar') ? 'bar' : style;
    let horiz;
    if (style === 'hbar') horiz = true;
    else if (style === 'vbar') horiz = false;
    else horiz = (disp.eff_orientation || 'v') === 'h';   // ориентация для всех типов (bar по умолч. вертик.)
    // Цветовые схемы [B1, B2, R, R★]
    const SCHEMES = {
      default: ['#f59e0b', '#94a3b8', '#3b82f6', '#22c55e'],
      cool:    ['#06b6d4', '#94a3b8', '#6366f1', '#10b981'],
      warm:    ['#f97316', '#a8a29e', '#e11d48', '#f59e0b'],
      mono:    ['#cbd5e1', '#94a3b8', '#64748b', '#1e293b'],
      vivid:   ['#a855f7', '#94a3b8', '#ec4899', '#14b8a6'],
    };
    const fullColors = SCHEMES[disp.eff_color_scheme] || SCHEMES.default;
    const want = [on('series_b1'), on('series_b2'), on('series_r'), true];
    const idx = [0, 1, 2, 3].filter((i) => want[i]);
    const FULL_CATS = ['Данные УзКорГаз<br>(суточные сводки)', 'B2 (Наблюдение)', 'R (Адаптация)', 'R★ (Оптимум)'];
    const cats = FULL_CATS.filter((_, i) => want[i]);
    const colors = fullColors.filter((_, i) => want[i]);
    const b1q = b1.flow_median != null ? b1.flow_median : b1.q_total_median;
    const b1down = b1.shutdown_min_total != null ? b1.shutdown_min_total / 60 : b1.downtime_hours;
    const cfg = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ['lasso2d', 'select2d'] };
    // diff: между какими периодами фиксируем разницу (база → цель)
    const clampI = (x, def) => Math.max(0, Math.min(3, (x == null ? def : x)));
    const baseIdx = clampI(disp.eff_diff_base, 1);    // по умолч. B2 (наблюдение)
    const tgtIdx = clampI(disp.eff_diff_target, 3);   // по умолч. R★ (оптимум)
    const num = (x) => (x == null || isNaN(x)) ? null : Number(x);
    const noData = (el) => { el.innerHTML = '<div style="color:#9ca3af;font-size:0.8rem;text-align:center;padding-top:70px;">нет данных</div>'; };
    // Оси под ориентацию: cat-ось (категории) + val-ось (значения)
    const axes = (unit) => {
      const valAx = { title: { text: unit, font: { size: 12 } }, rangemode: 'tozero', zeroline: true, zerolinecolor: '#cbd5e1', gridcolor: '#eef1f4', tickfont: { size: 11 } };
      const catAx = { automargin: true, tickfont: { size: 12 } };
      if (horiz) { catAx.autorange = 'reversed'; return { xaxis: valAx, yaxis: catAx, margin: { l: 8, r: 92, t: 16, b: 30 } }; }
      return { xaxis: catAx, yaxis: valAx, margin: { l: 56, r: 16, t: 16, b: 56 } };
    };
    const plot = (suffix, vals, unit, dec, betterUp) => {
      const el = document.getElementById(idPrefix + suffix);
      if (!el) return;
      let traces, layout;
      // ── Диаграмма «Разница / эффект»: периоды + опорная линия базы + зона
      //    улучшения (фон) + дельты-бейджи относительно базы ──
      if (baseStyle === 'diff') {
        const v = idx.map((i) => vals[i]).map(num);
        if (!v.some((x) => x != null)) { noData(el); return; }
        const baseV = num(vals[baseIdx]);
        const fin = v.filter((x) => x != null).concat(baseV != null ? [baseV] : []);
        const maxV = fin.length ? Math.max.apply(null, fin) : 1;
        const lbls = v.map((x) => x == null ? '' : x.toFixed(dec) + ' ' + unit);
        const bd = horiz ? { orientation: 'h', y: cats, x: v } : { x: cats, y: v };
        traces = [Object.assign({ type: 'bar', marker: { color: colors, line: { width: 0 } }, width: 0.6,
          text: lbls, textposition: 'inside', insidetextanchor: horiz ? 'start' : 'middle', textfont: { size: 12, color: '#fff' }, cliponaxis: false,
          hovertemplate: '%{' + (horiz ? 'y' : 'x') + '}<br>%{' + (horiz ? 'x' : 'y') + ':.' + dec + 'f} ' + unit + '<extra></extra>' }, bd)];
        const shapes = [], anns = [];
        if (baseV != null) {
          if (horiz) {
            shapes.push({ type: 'rect', xref: 'x', yref: 'paper', x0: baseV, x1: maxV * 1.2, y0: 0, y1: 1, fillcolor: 'rgba(34,197,94,0.07)', line: { width: 0 }, layer: 'below' });
            shapes.push({ type: 'line', xref: 'x', yref: 'paper', x0: baseV, x1: baseV, y0: 0, y1: 1, line: { color: '#475569', width: 1.6, dash: 'dash' } });
          } else {
            shapes.push({ type: 'rect', xref: 'paper', yref: 'y', x0: 0, x1: 1, y0: baseV, y1: maxV * 1.2, fillcolor: 'rgba(34,197,94,0.07)', line: { width: 0 }, layer: 'below' });
            shapes.push({ type: 'line', xref: 'paper', yref: 'y', x0: 0, x1: 1, y0: baseV, y1: baseV, line: { color: '#475569', width: 1.6, dash: 'dash' } });
          }
          idx.forEach((gi, k) => {
            if (gi === baseIdx || v[k] == null) return;
            const delta = v[k] - baseV;
            const improved = (delta > 0) === betterUp;
            const col = improved ? '#16a34a' : '#dc2626';
            const pct = baseV ? (delta / Math.abs(baseV) * 100) : null;
            const txt = (delta >= 0 ? '▲ +' : '▼ ') + delta.toFixed(dec) + (pct != null ? ' (' + (delta >= 0 ? '+' : '') + pct.toFixed(0) + '%)' : '');
            anns.push(horiz
              ? { x: v[k], y: cats[k], text: txt, showarrow: false, xanchor: 'left', xshift: 6, font: { size: 12, color: col }, bgcolor: 'rgba(255,255,255,0.92)', bordercolor: col, borderwidth: 1, borderpad: 2 }
              : { x: cats[k], y: v[k], text: txt, showarrow: false, yanchor: 'bottom', yshift: 6, font: { size: 12, color: col }, bgcolor: 'rgba(255,255,255,0.92)', bordercolor: col, borderwidth: 1, borderpad: 2 });
          });
        }
        layout = Object.assign({ font: { size: 13 }, showlegend: false, bargap: 0.45, plot_bgcolor: '#fff', paper_bgcolor: '#fff', shapes: shapes, annotations: anns,
          title: { text: 'Эффект относительно базы (' + FULL_CATS[baseIdx].replace('<br>', ' ') + ')', font: { size: 12, color: '#475569' } } }, axes(unit));
        layout.margin = horiz ? { l: 8, r: 160, t: 30, b: 30 } : { l: 56, r: 16, t: 42, b: 56 };
        try { Plotly.newPlot(el, traces, layout, cfg); } catch (e) { console.warn('eff diff ' + suffix, e); }
        return;
      }
      // ── bar / lollipop / dot ──
      const v = idx.map((i) => vals[i]).map(num);
      if (!v.some((x) => x != null)) { noData(el); return; }
      const lbls = v.map((x) => x == null ? '' : x.toFixed(dec) + ' ' + unit);
      const txtPos = horiz ? 'middle right' : 'top center';
      let effAnns = [], effShapes = [], effRange = null;   // линии баз + зона + дельты R,R★ к базам
      if (baseStyle === 'lollipop') {
        const lc = [], lv = [];
        cats.forEach((c, i) => { if (v[i] != null) { lc.push(c, c, null); lv.push(0, v[i], null); } });
        const stem = horiz ? { x: lv, y: lc } : { x: lc, y: lv };
        const mk = horiz ? { x: v, y: cats } : { x: cats, y: v };
        traces = [
          Object.assign({ type: 'scatter', mode: 'lines', line: { color: '#cbd5e1', width: 2.5 }, hoverinfo: 'skip' }, stem),
          Object.assign({ type: 'scatter', mode: 'markers+text', marker: { color: colors, size: 17, line: { color: '#fff', width: 1.5 } }, text: lbls, textposition: txtPos, textfont: { size: 13, color: '#374151' }, cliponaxis: false }, mk),
        ];
      } else if (baseStyle === 'dot') {
        const mk = horiz ? { x: v, y: cats } : { x: cats, y: v };
        traces = [Object.assign({ type: 'scatter', mode: 'markers+text', marker: { color: colors, size: 19, symbol: 'circle', line: { color: '#fff', width: 2 } }, text: lbls, textposition: txtPos, textfont: { size: 13, color: '#374151' }, cliponaxis: false }, mk)];
      } else {  // СОВРЕМЕННЫЙ дизайн (lollipop с якорем B2) — стержень B2→значение = дельта;
        //          одна якорная вертикаль B2; цвет = направление (лучше/хуже базы); чистый текст.
        const baseB2 = num(vals[1]);    // якорь = наблюдение (точка отсчёта эффекта)
        const PCOL = ['#9e9e9e', '#455a64', '#1976d2', '#2e7d32'];   // B1, B2, R, R★
        const GOODC = { 2: '#1976d2', 3: '#2e7d32' };
        const cat = (gi) => FULL_CATS[gi].replace('<br>', ' ');
        const dirColor = (gi, val) => {
          if (gi !== 2 && gi !== 3) return PCOL[gi];
          if (baseB2 == null) return GOODC[gi];
          return (betterUp ? (val > baseB2) : (val < baseB2)) ? GOODC[gi] : '#c62828';
        };
        // Строки снизу вверх: B1, B2, R, R★ (фильтр по idx → можно скрыть любой период).
        const rows = [0, 1, 2, 3].filter((gi) => idx.indexOf(gi) >= 0 && num(vals[gi]) != null);
        const yCats = rows.map(cat);
        traces = [];
        rows.forEach((gi) => {
          const val = num(vals[gi]);
          const color = dirColor(gi, val);
          if (gi !== 1 && baseB2 != null) {   // стержень от B2 до значения (кроме самой базы)
            traces.push({ type: 'scatter', mode: 'lines', x: [baseB2, val], y: [cat(gi), cat(gi)], line: { color: color, width: 3 }, hoverinfo: 'skip', showlegend: false });
          }
          traces.push({ type: 'scatter', mode: 'markers', x: [val], y: [cat(gi)], marker: { size: 15, color: color }, showlegend: false,
            hovertemplate: cat(gi) + ': %{x:.' + dec + 'f} ' + unit + '<extra></extra>' });
          if (gi === 1) {
            effAnns.push({ x: val, y: cat(gi), text: 'база', showarrow: false, xanchor: 'right', xshift: -10, font: { size: 11, color: '#455a64' } });
          } else if ((gi === 2 || gi === 3) && baseB2 != null) {
            const d = val - baseB2, pct = baseB2 ? (d / Math.abs(baseB2) * 100) : null;
            const txt = (d >= 0 ? '+' : '') + d.toFixed(dec) + ' ' + unit + (pct != null ? ' (' + (d >= 0 ? '+' : '') + pct.toFixed(0) + '%)' : '');
            effAnns.push({ x: val, y: cat(gi), text: txt, showarrow: false, xanchor: 'left', xshift: 12, font: { size: 12, color: color }, bgcolor: 'rgba(255,255,255,0.7)' });
          } else {
            effAnns.push({ x: val, y: cat(gi), text: val.toFixed(dec) + ' ' + unit, showarrow: false, xanchor: 'left', xshift: 12, font: { size: 11, color: '#9e9e9e' }, bgcolor: 'rgba(255,255,255,0.7)' });
          }
        });
        if (baseB2 != null) effShapes.push({ type: 'line', xref: 'x', yref: 'paper', x0: baseB2, x1: baseB2, y0: 0, y1: 1, line: { color: '#455a64', width: 1.5 } });
        const allv = rows.map((gi) => num(vals[gi])).filter((x) => x != null);
        if (baseB2 != null) allv.push(baseB2);
        const mx2 = allv.length ? Math.max.apply(null, allv) : 1;
        const TINT = { 'eff-q': 'rgba(79,70,229,0.04)', 'eff-dp': 'rgba(13,148,136,0.04)', 'eff-down': 'rgba(225,29,72,0.04)' };
        layout = {
          font: { size: 13 }, showlegend: false, plot_bgcolor: TINT[suffix] || '#fff', paper_bgcolor: '#fff',
          margin: { l: 8, r: 175, t: 14, b: 34 }, annotations: effAnns, shapes: effShapes,
          xaxis: { title: { text: unit, font: { size: 12 } }, rangemode: 'tozero', range: [0, mx2 * 1.12], zeroline: false, showgrid: false, tickfont: { size: 11 } },
          yaxis: { categoryarray: yCats, categoryorder: 'array', automargin: true, tickfont: { size: 12 } },
        };
        try { Plotly.newPlot(el, traces, layout, cfg); } catch (e) { console.warn('eff lollipop ' + suffix, e); }
        return;
      }
      const TINT = { 'eff-q': 'rgba(79,70,229,0.045)', 'eff-dp': 'rgba(13,148,136,0.05)', 'eff-down': 'rgba(225,29,72,0.045)' };
      layout = Object.assign({ font: { size: 13 }, showlegend: false, bargap: 0.42, plot_bgcolor: TINT[suffix] || '#fff', paper_bgcolor: '#fff', annotations: effAnns, shapes: effShapes }, axes(unit));
      if (effRange) {
        if (horiz) { layout.xaxis = Object.assign({}, layout.xaxis, { range: effRange }); layout.margin = Object.assign({}, layout.margin, { r: 150 }); }
        else { layout.yaxis = Object.assign({}, layout.yaxis, { range: effRange }); }
      }
      try { Plotly.newPlot(el, traces, layout, cfg); } catch (e) { console.warn('eff ' + suffix, e); }
    };
    // betterUp: рост хорош для Q и ΔP (больше дебит/вынос воды), плох для простоев.
    plot('eff-q', [b1q, obs.flow_median, ad.flow_median, opt.flow_median], 'тыс.м³/сут', 2, true);
    plot('eff-dp', [b1.dp_median, obs.dp_median, ad.dp_median, opt.dp_median], 'кгс/см²', 2, true);
    plot('eff-down', [b1down, obs.downtime_hours, ad.downtime_hours, opt.downtime_hours], 'ч', 1, false);
  }

  // Рендер Plotly-графиков для adaptation_period_analysis
  function _renderAdaptationCharts(snap, idPrefix) {
    if (!window.Plotly || !snap || !snap.adaptation) return;

    const cd = snap.adaptation.chart_data;
    if (!cd || !cd.length) return;

    const layout = { margin: {l: 50, r: 20, t: 30, b: 40}, hovermode: 'x unified',
                     font: {size: 10}, legend: {orientation: 'h', y: -0.18} };
    const cfg = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ['lasso2d', 'select2d'] };
    const dates = cd.map(p => p.t);
    const events = snap.adaptation.events_for_chart || [];

    // Создаём вертикальные линии (shapes) для событий
    const makeEventShapes = (yMin, yMax) => {
      const shapes = [];
      events.forEach(ev => {
        if (ev.type === 'purge') {
          shapes.push({
            type: 'line', x0: ev.t, x1: ev.t, y0: yMin, y1: yMax,
            line: { color: '#6b7280', width: 1, dash: 'dot' },
          });
        }
      });
      return shapes;
    };

    // Создаём маркеры для вбросов реагента
    const makeReagentTrace = (yValues) => {
      const reagentEvents = events.filter(e => e.type === 'reagent');
      if (!reagentEvents.length) return null;
      // Для каждого вброса находим ближайшую точку данных
      const xs = [], ys = [], texts = [];
      reagentEvents.forEach(ev => {
        const idx = dates.findIndex(d => d >= ev.t);
        if (idx >= 0 && yValues[idx] != null) {
          xs.push(ev.t);
          ys.push(yValues[idx]);
          texts.push(ev.label || ev.reagent || 'Вброс');
        }
      });
      if (!xs.length) return null;
      return {
        x: xs, y: ys, mode: 'markers', name: 'Вброс',
        marker: { color: '#059669', size: 10, symbol: 'diamond' },
        text: texts, hovertemplate: '%{text}<extra></extra>',
        showlegend: true,
      };
    };

    // ────────────────────────────────────────────────
    // 1. Давления (P устье + P линия)
    // ────────────────────────────────────────────────
    const elPres = document.getElementById(idPrefix + 'pres');
    if (elPres) {
      try {
        const pTube = cd.map(p => p.p_tube);
        const pLine = cd.map(p => p.p_line);
        const allP = [...pTube, ...pLine].filter(v => v != null && !isNaN(v));
        const pMin = Math.min(...allP) * 0.95;
        const pMax = Math.max(...allP) * 1.05;

        const traces = [
          { x: dates, y: pTube, name: 'P устье', mode: 'lines', line: {color: '#ef4444', width: 1.5} },
          { x: dates, y: pLine, name: 'P линия', mode: 'lines', line: {color: '#3b82f6', width: 1.5} },
        ];
        const reagentTrace = makeReagentTrace(pTube);
        if (reagentTrace) traces.push(reagentTrace);

        Plotly.newPlot(elPres, traces, {
          ...layout, title: { text: 'Давления, кгс/см²', font: {size: 12} },
          shapes: makeEventShapes(pMin, pMax),
          yaxis: { title: 'кгс/см²', range: [pMin, pMax] },
        }, cfg);
      } catch (e) { console.warn('adapt chart pres', e); }
    }

    // ────────────────────────────────────────────────
    // 2. ΔP (перепад давления)
    // ────────────────────────────────────────────────
    const elDp = document.getElementById(idPrefix + 'dp');
    if (elDp) {
      try {
        const dpVals = cd.map(p => p.dp);
        const dpClean = dpVals.filter(v => v != null && !isNaN(v));
        const dpMin = Math.min(...dpClean) * 0.9;
        const dpMax = Math.max(...dpClean) * 1.1;

        const traces = [
          { x: dates, y: dpVals, name: 'ΔP', mode: 'lines', line: {color: '#dc2626', width: 2}, fill: 'tozeroy', fillcolor: 'rgba(220,38,38,0.1)' },
        ];
        const reagentTrace = makeReagentTrace(dpVals);
        if (reagentTrace) {
          reagentTrace.marker.color = '#16a34a';
          traces.push(reagentTrace);
        }

        Plotly.newPlot(elDp, traces, {
          ...layout, title: { text: 'Перепад ΔP, кгс/см²', font: {size: 12} },
          shapes: makeEventShapes(dpMin, dpMax),
          yaxis: { title: 'кгс/см²', range: [dpMin, dpMax] },
        }, cfg);
      } catch (e) { console.warn('adapt chart dp', e); }
    }

    // ────────────────────────────────────────────────
    // 3. Q (расчётный дебит)
    // ────────────────────────────────────────────────
    const elFlow = document.getElementById(idPrefix + 'flow');
    if (elFlow) {
      try {
        const qVals = cd.map(p => p.q);
        const qClean = qVals.filter(v => v != null && !isNaN(v) && v > 0);
        const qMin = qClean.length ? Math.min(...qClean) * 0.9 : 0;
        const qMax = qClean.length ? Math.max(...qClean) * 1.1 : 50;

        const traces = [
          { x: dates, y: qVals, name: 'Q', mode: 'lines',
            line: {color: '#2563eb', width: 2}, fill: 'tozeroy', fillcolor: 'rgba(37,99,235,0.08)' },
        ];
        const reagentTrace = makeReagentTrace(qVals);
        if (reagentTrace) {
          reagentTrace.marker.color = '#059669';
          traces.push(reagentTrace);
        }

        // Добавляем медиану как горизонтальную линию
        const qMed = snap.adaptation.flow_median;
        if (qMed != null) {
          traces.push({
            x: [dates[0], dates[dates.length - 1]], y: [qMed, qMed],
            mode: 'lines', name: `Медиана: ${qMed.toFixed(1)}`,
            line: {color: '#f59e0b', width: 1.5, dash: 'dash'},
          });
        }

        // 1.9 — отметка окна R★ на графике дебита
        const _flowShapes = makeEventShapes(qMin, qMax);
        const _opt = snap.optimal || {};
        const _flowAnnos = [];
        if (_opt.date_from && _opt.date_to) {
          _flowShapes.push({ type: 'rect', xref: 'x', yref: 'paper',
            x0: _opt.date_from, x1: _opt.date_to, y0: 0, y1: 1,
            fillcolor: 'rgba(34,197,94,0.13)', line: { width: 0 }, layer: 'below' });
          _flowAnnos.push({ x: _opt.date_from, xref: 'x', y: 1, yref: 'paper',
            text: 'R★', showarrow: false, xanchor: 'left', yanchor: 'top',
            font: { size: 11, color: '#15803d' }, bgcolor: 'rgba(255,255,255,0.8)' });
        }
        Plotly.newPlot(elFlow, traces, {
          ...layout, title: { text: 'Дебит Q, тыс.м³/сут', font: {size: 12} },
          shapes: _flowShapes, annotations: _flowAnnos,
          yaxis: { title: 'тыс.м³/сут', range: [qMin, qMax] },
        }, cfg);
      } catch (e) { console.warn('adapt chart flow', e); }
    }

    // ────────────────────────────────────────────────
    // 1.3 Посуточный график рабочего/нерабочего времени
    // ────────────────────────────────────────────────
    const elDown = document.getElementById(idPrefix + 'downtime');
    if (elDown) {
      try {
        const cdd = snap.adaptation.chart_data || [];
        const work = {}, down = {};
        for (const p of cdd) {
          const day = (p.t || '').slice(0, 10); if (!day) continue;
          const q = p.q;
          if (q != null && !isNaN(q) && Number(q) > 0) work[day] = (work[day] || 0) + 1;
          else down[day] = (down[day] || 0) + 1;
        }
        const days = [...new Set([...Object.keys(work), ...Object.keys(down)])].sort();
        Plotly.newPlot(elDown, [
          { type: 'bar', name: 'Работа, ч', x: days, y: days.map(d => work[d] || 0),
            marker: { color: 'rgba(22,163,74,0.45)', line: { color: '#16a34a', width: 1 } } },
          { type: 'bar', name: 'Простой, ч', x: days, y: days.map(d => down[d] || 0),
            marker: { color: 'rgba(220,38,38,0.85)' } },
        ], {
          barmode: 'stack', title: { text: 'Рабочее / нерабочее время по суткам, ч', font: { size: 12 } },
          margin: { l: 44, r: 12, t: 34, b: 54 }, font: { size: 11 },
          xaxis: { type: 'category', tickfont: { size: 10 } },
          yaxis: { title: 'часов', rangemode: 'tozero' },
          legend: { orientation: 'h', y: -0.24 },
        }, cfg);
      } catch (e) { console.warn('adapt chart downtime', e); }
    }

    // ────────────────────────────────────────────────
    // 4. Сегментный анализ (если есть данные)
    // ────────────────────────────────────────────────
    const seg = snap.segment_analysis || {};
    const elSeg = document.getElementById(idPrefix + 'segments');
    if (elSeg && seg.chart_data) {
      try {
        const segDates = seg.chart_data.dates || [];
        const segQ = seg.chart_data.q_total || [];
        const cps = seg.changepoints || [];
        const cpMarks = seg.cp_marks || [];

        // Основной trace
        const traces = [
          { x: segDates, y: segQ, name: 'Q', mode: 'lines',
            line: {color: '#6366f1', width: 2} },
        ];

        // Вертикальные линии на changepoints
        const cpShapes = [];
        cps.forEach((cpIdx, i) => {
          if (cpIdx >= 0 && cpIdx < segDates.length) {
            cpShapes.push({
              type: 'line', x0: segDates[cpIdx], x1: segDates[cpIdx],
              y0: 0, y1: 1, yref: 'paper',
              line: { color: '#f59e0b', width: 1.5, dash: 'dash' },
            });
          }
        });

        // Аннотации для CP1, CP2, ...
        const cpAnnotations = cpMarks.slice(0, 8).map((cp, i) => ({
          x: cp.date, y: 1, yref: 'paper', yanchor: 'bottom',
          text: cp.tag, showarrow: false,
          font: { size: 9, color: '#d97706' },
          bgcolor: '#fffbeb', borderpad: 2,
        }));

        const segQClean = segQ.filter(v => v != null && !isNaN(v) && v > 0);
        const segYMin = segQClean.length ? Math.min(...segQClean) * 0.9 : 0;
        const segYMax = segQClean.length ? Math.max(...segQClean) * 1.1 : 50;

        Plotly.newPlot(elSeg, traces, {
          ...layout,
          title: { text: 'Сегментный анализ Q', font: {size: 12} },
          shapes: cpShapes,
          annotations: cpAnnotations,
          yaxis: { title: 'тыс.м³/сут', range: [segYMin, segYMax] },
        }, cfg);
      } catch (e) { console.warn('adapt chart segments', e); }
    }
  }

  // ===== _renderBlockSummaryHtml (извлечено 1-в-1) =====
  // Роза НЕСТАБИЛЬНОСТИ — Plotly (геометрия база=50: зелёный внутрь / красный
  // наружу, серый диск 0-50, красное кольцо 75-100, пунктир порога, полигон,
  // числа на концах) + горизонтальный бар вкладов. Определена ЗДЕСЬ (источник
  // истины), чтобы PDF снимал те же графики через plotly_png_service.
  function _renderStabilityRoseCharts(b) {
    if (typeof Plotly === 'undefined') return;
    const bid = b.id || 'x';
    const snap = b.data_snapshot || {};
    const AX = ['trend', 'rough', 'cyc', 'dp', 'uplift', 'freq', 'purge'];
    const petals = snap.petals || {};
    const labelsShort = snap.labels_short || {};
    const labels = snap.labels || {};
    const theta = AX.map(k => (labelsShort[k] || k).replace(/\n/g, ' '));
    const vals = AX.map(k => +(petals[k] ?? 0));
    const BASE = 50;
    const slot = 360 / AX.length;
    const traces = [];
    traces.push({ type: 'barpolar', r: AX.map(() => 50), theta: theta, base: 0,
      width: AX.map(() => 1), marker: { color: 'rgba(221,227,237,0.55)' }, hoverinfo: 'skip', showlegend: false });
    traces.push({ type: 'barpolar', r: AX.map(() => 25), theta: theta, base: 75,
      width: AX.map(() => 1), marker: { color: 'rgba(214,39,40,0.10)' }, hoverinfo: 'skip', showlegend: false });
    // ЧИСТЫЙ ЦЕНТР: тревожные оси (v>50) — красный лепесток НАРУЖУ за порог;
    // спокойные (v<50) — короткий зелёный штрих у порога (центр пустой, серый диск).
    const GSTUB = 5;
    const redR = [], redB = [], redT = [], grnR = [], grnB = [], grnT = [];
    AX.forEach((k, i) => {
      const v = vals[i];
      if (v > BASE) { redR.push(v - BASE); redB.push(BASE); redT.push(theta[i]); }
      else if (v < BASE) { const s = Math.min(BASE - v, GSTUB); grnR.push(s); grnB.push(BASE - s); grnT.push(theta[i]); }
    });
    if (redR.length) traces.push({ type: 'barpolar', r: redR, base: redB, theta: redT,
      width: redR.map(() => 0.55), marker: { color: '#d62728', line: { color: '#7a1010', width: 0.8 } },
      hoverinfo: 'skip', showlegend: false });
    if (grnR.length) traces.push({ type: 'barpolar', r: grnR, base: grnB, theta: grnT,
      width: grnR.map(() => 0.55), marker: { color: '#28a745', line: { color: '#155724', width: 0.6 } },
      hoverinfo: 'skip', showlegend: false });
    // пунктир порога 50
    traces.push({ type: 'scatterpolar', r: AX.map(() => 50).concat([50]), theta: theta.concat([theta[0]]),
      mode: 'lines', line: { color: '#5a6f8a', width: 1.2, dash: 'dash' }, hoverinfo: 'skip', showlegend: false });
    // числа: тревожные — снаружи у конца лепестка; спокойные — у порога (центр чистый)
    traces.push({ type: 'scatterpolar',
      r: vals.map(v => v >= BASE ? Math.min(v + 6, 97) : 44), theta: theta,
      mode: 'text', text: vals.map(v => Math.round(v).toString()),
      textfont: { size: 11, color: vals.map(v => v >= BASE ? '#7a1010' : '#155724'), family: 'Arial Black' },
      hoverinfo: 'skip', showlegend: false });
    const layout = {
      polar: {
        radialaxis: { range: [0, 100], tickvals: [25, 50, 75, 100],
          ticktext: ['25', '50 — порог', '75', '100'], tickfont: { size: 9, color: '#888' }, angle: 90 },
        angularaxis: { direction: 'clockwise', rotation: 90, tickfont: { size: 10, color: '#374151' } },
        bgcolor: '#fff',
      },
      margin: { t: 34, r: 70, b: 34, l: 70 }, paper_bgcolor: '#fff',
      title: { text: 'Роза нестабильности', font: { size: 13, color: '#374151' } },
    };
    try { Plotly.newPlot('prev-' + bid + '-strose', traces, layout, { displayModeBar: false, responsive: true }); }
    catch (e) { console.warn('strose', e); }

    const contrib = snap.contributions || {};
    const weights = snap.weights || {};
    const items = AX.map(k => ({ full: (labels[k] || k), got: +(contrib[k] ?? 0),
      max: 100 * (+(weights[k] ?? 0)), w: +(weights[k] ?? 0) })).sort((a, b2) => a.got - b2.got);
    const yb = items.map(it => it.full);
    const maxX = Math.max(20, Math.max.apply(null, items.map(it => it.max)) * 1.4);
    const bt = [
      { type: 'bar', orientation: 'h', y: yb, x: items.map(it => it.got), marker: { color: '#d62728' },
        text: items.map(it => it.got.toFixed(1)), textposition: 'inside', textfont: { color: '#fff' },
        hovertemplate: '%{y}: %{x:.1f}<extra></extra>', showlegend: false },
      { type: 'bar', orientation: 'h', y: yb, x: items.map(it => Math.max(0, it.max - it.got)),
        marker: { color: '#e6e6e6' }, text: items.map(it => 'вес ' + it.w.toFixed(2)), textposition: 'outside',
        textfont: { size: 9, color: '#6b7280' }, hoverinfo: 'skip', showlegend: false },
    ];
    const blay = { barmode: 'stack', margin: { t: 28, r: 80, b: 32, l: 175 },
      xaxis: { title: { text: 'Вклад = вес × лепесток', font: { size: 10 } }, range: [0, maxX] },
      yaxis: { automargin: true }, paper_bgcolor: '#fff', plot_bgcolor: '#fff',
      title: { text: 'Вклад в нестабильность · I=' + ((snap.index_I != null) ? snap.index_I : 0), font: { size: 13, color: '#374151' } } };
    try { Plotly.newPlot('prev-' + bid + '-strosebar', bt, blay, { displayModeBar: false, responsive: true }); }
    catch (e) { console.warn('strosebar', e); }
  }

  // ===== _renderWorksAnalysisChart — Plotly radar для works_analysis (_v='2') =====
  // Контейнер: prev-{bid}-worksrose (высота 360px, строится в _renderBlockSummaryHtml).
  // В compare-режиме два контура: «Период» (синий) и «До работ» (серый, пунктир).
  function _renderWorksAnalysisChart(snap, bid) {
    if (typeof Plotly === 'undefined') return;
    const el = document.getElementById('prev-' + bid + '-worksrose');
    if (!el) return;
    const pm = snap.period_main || {};
    const pr = snap.period_ref  || null;
    const isCompare = snap.mode === 'compare' && !!pr;

    const axes = pm.rose_axes   || [];
    const vals = pm.rose_values || [];
    if (!axes.length) {
      el.innerHTML = '<div style="color:#9ca3af; padding:15px; text-align:center;">Нет данных розы</div>';
      return;
    }

    // Замыкаем полигон (scatterpolar требует повтора первого элемента)
    const theta = axes.concat([axes[0]]);
    const r1    = vals.map(Number).concat([+(vals[0] || 0)]);

    const traces = [];
    traces.push({
      type: 'scatterpolar', r: r1, theta: theta,
      fill: 'toself', fillcolor: 'rgba(59,130,246,0.15)',
      line: { color: '#3b82f6', width: 2 },
      name: 'Период', mode: 'lines+markers',
      marker: { size: 5, color: '#3b82f6' },
    });

    if (isCompare && pr) {
      const vals2 = pr.rose_values || [];
      const r2    = vals2.map(Number).concat([+(vals2[0] || 0)]);
      traces.push({
        type: 'scatterpolar', r: r2, theta: theta,
        fill: 'toself', fillcolor: 'rgba(156,163,175,0.15)',
        line: { color: '#9ca3af', width: 2, dash: 'dash' },
        name: 'До работ', mode: 'lines+markers',
        marker: { size: 5, color: '#9ca3af' },
      });
    }

    const layout = {
      polar: {
        radialaxis: { range: [0, 100], tickvals: [25, 50, 75, 100],
          tickfont: { size: 9, color: '#888' }, angle: 90 },
        angularaxis: { direction: 'clockwise', rotation: 90, tickfont: { size: 9, color: '#374151' } },
        bgcolor: '#fff',
      },
      showlegend: isCompare,
      legend: { x: 1.05, y: 1, font: { size: 10 } },
      margin: { t: 34, r: isCompare ? 100 : 70, b: 34, l: 70 },
      paper_bgcolor: '#fff',
      title: { text: 'Профиль эффективности (больше = лучше)', font: { size: 12, color: '#374151' } },
    };
    try { Plotly.newPlot(el, traces, layout, { displayModeBar: false, responsive: true }); }
    catch (e) { console.warn('worksrose', e); }
  }

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

    // ─── sensor_customer_comparison: сопоставление LoRa и УзКорГаз ───
    if (b.kind === 'sensor_customer_comparison') {
      const bid = b.id || 'x';
      return _buildSensorCustomerComparisonHtml(snap, b, `scc-${bid}-`);
    }

    // ─── pressure_spectrum: спектр распределения давления (стабильность) ───
    if (b.kind === 'pressure_spectrum') {
      const bid = b.id || 'x';
      return _buildPressureSpectrumHtml(snap, b, `ps-${bid}-`);
    }

    // ─── param_correlation: зависимость двух параметров (scatter + регрессия) ───
    if (b.kind === 'param_correlation') {
      const bid = b.id || 'x';
      return _buildParamCorrelationHtml(snap, b, `pc-${bid}-`);
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

    if (b.kind === 'stability_rose') {
      // Роза НЕСТАБИЛЬНОСТИ (кандидаты на обводнение). Геометрия: база=50,
      // зелёный лепесток внутрь / красный наружу. Графики Plotly рисуются в
      // _renderStabilityRoseCharts (в этом же файле — источник истины, PDF
      // снимает те же графики через plotly_png_service).
      const bid = b.id || 'x';
      const AX = ['trend', 'rough', 'cyc', 'dp', 'uplift', 'freq', 'purge'];
      const labels = snap.labels || {};
      const petals = snap.petals || {};
      const contrib = snap.contributions || {};
      const weights = snap.weights || {};
      const descr = snap.descriptions || {};
      const I = (snap.index_I != null) ? +snap.index_I : 0;
      const lvl = snap.level || (snap.is_candidate ? 'candidate' : 'stable');
      const lvlColor = lvl === 'candidate' ? '#d62728' : lvl === 'watch' ? '#d97706' : '#28a745';
      const _w = snap.windows || { trend_days: 30, downtime_days: 90 };
      const effMode = snap.analysis_mode === 'effectiveness';
      const eff = snap.effectiveness || {};
      const hdrHtml = `
        <div style="font-size:0.85rem;">
          Скважина: <b>${_escHtml(snap.well_number)}</b>, на дату <b>${_escHtml(snap.anchor)}</b>,
          штуцер <b>${snap.choke_mm ?? '—'} мм</b>, данных <b>${snap.n_days || 0}</b> сут.
          (источник: ${snap.source === 'lora' ? 'LoRa минутные' : 'УзКорГаз суточные'}).
        </div>
        <div style="font-size:0.74rem; color:#6b7280; margin-top:2px;">
          Период: окна назад от даты — динамика дебита/давлений <b>${_w.trend_days}</b> сут${snap.window_auto === false ? ' (задано вручную)' : ''},
          простои/продувки <b>${_w.downtime_days}</b> сут, отклик дебита после остановки — вся история.
          L* — длина стабильного периода к этой дате.
        </div>
        ${(snap.data_quality && snap.data_quality !== 'normal') ? `
        <div style="font-size:0.74rem; margin-top:3px; padding:3px 8px; border-radius:3px;
             background:${snap.data_quality === 'insufficient' ? '#fef2f2' : '#fff7ed'};
             color:${snap.data_quality === 'insufficient' ? '#991b1b' : '#9a3412'};
             border-left:3px solid ${snap.data_quality === 'insufficient' ? '#dc2626' : '#f59e0b'};">
          ⚠ Достоверность снижена: рабочих точек в окне динамики — <b>${snap.n_work ?? '?'}</b>
          ${snap.data_quality === 'insufficient'
            ? '(&lt; 8 — оси динамики обнулены, оценка по простоям/продувкам).'
            : '(&lt; 14 — тренд/давления оценены грубо).'}
        </div>` : ''}
        <div style="font-size:1.0rem; font-weight:700; margin:4px 0; color:${lvlColor};">
          ${effMode
            ? `🧪 Режим: оценка ПАВ · ${_escHtml(snap.verdict || '')}`
            : `Индекс нестабильности I = ${I.toFixed(1)} / 100 · L* = ${snap.L_star ?? 0} дн. · ${_escHtml(snap.verdict || '')}`}
        </div>
        ${effMode ? `
        <div style="font-size:0.74rem; color:#6b7280; margin-top:2px; padding:3px 8px;
             background:#ecfdf5; border-left:3px solid #10b981; border-radius:3px;">
          Скважина на ПАВ-лечении (вбросов: <b>${eff.injections_total ?? '?'}</b>${eff.best_reagent ? ', реагент: <b>' + _escHtml(eff.best_reagent) + '</b>' : ''}).
          Колебания/прирост Q — реакция на ПАВ, не нестабильность. Оси ниже — справочно
          (индекс кандидатства I=${I.toFixed(1)} не применяется).
        </div>` : ''}
        ${comment}`;
      const roseBox = (parts.rose_chart !== false)
        ? `<div id="prev-${bid}-strose" style="height:340px;"></div>` : '';
      const barBox = (parts.contributions_chart !== false)
        ? `<div id="prev-${bid}-strosebar" style="height:240px; margin-top:6px;"></div>` : '';
      let tableHtml = '';
      if (parts.metrics_table !== false) {
        let rows = '';
        AX.forEach(k => {
          const v = +(petals[k] ?? 0);
          const w = +(weights[k] ?? 0);
          const cv = +(contrib[k] ?? 0);
          const zone = v < 33 ? 'спокоен' : v < 66 ? 'внимание' : 'тревога';
          const zc = v < 33 ? '#16a34a' : v < 66 ? '#d97706' : '#dc2626';
          rows += `<tr><td style="padding:2px 6px;">${_escHtml(labels[k] || k)}</td>
                       <td style="text-align:right; padding:2px 6px; font-weight:600;">${v.toFixed(0)}</td>
                       <td style="text-align:right; padding:2px 6px;">${w.toFixed(2)}</td>
                       <td style="text-align:right; padding:2px 6px; color:#d62728;">${cv.toFixed(1)}</td>
                       <td style="padding:2px 6px; color:${zc};">${zone}</td></tr>`;
        });
        tableHtml = `
          <table style="width:100%; margin-top:6px; font-size:0.78rem; border-collapse:collapse;">
            <tr style="background:#f3f4f6;">
              <th style="text-align:left; padding:3px 6px;">Параметр</th>
              <th style="text-align:right; padding:3px 6px;">Значение</th>
              <th style="text-align:right; padding:3px 6px;">Вес</th>
              <th style="text-align:right; padding:3px 6px;">Вклад</th>
              <th style="text-align:left; padding:3px 6px;">Зона</th>
            </tr>
            ${rows}
          </table>`;
      }
      let descrHtml = '';
      if (parts.descriptions !== false && descr && Object.keys(descr).length) {
        descrHtml = `
          <div style="margin-top:6px; font-size:0.76rem; color:#374151;">
            <div style="font-weight:600; margin-bottom:2px;">Описание параметров:</div>
            ${AX.map(k => `<div style="margin:2px 0;"><b>${_escHtml(labels[k] || k)}.</b> ${_escHtml(descr[k] || '')}</div>`).join('')}
          </div>`;
      }
      // Переходной/вводный блок: назначение анализа + суть метода. Параметризован
      // из снимка (источник, якорь, окна). Parity с PDF — _format_stability_rose_block.
      let methodHtml = '';
      if (parts.method !== false) {
        const _srcLbl = snap.source === 'lora' ? 'LoRa (минутные)' : 'УзКорГаз (суточные)';
        methodHtml = `
          <div style="font-size:0.78rem; color:#374151; margin-bottom:8px; padding:8px 10px;
               background:#f8fafc; border-left:3px solid #2563eb; border-radius:4px;">
            <b>Назначение и метод.</b> Для анализа стабильности работы скважины и оценки
            кандидата на проведение работ по оптимизации с применением <b>ТПАВ</b>
            проводился анализ данных <b>${_escHtml(_srcLbl)}</b> по суточным дебитам на
            дату <b>${_escHtml(snap.anchor || '—')}</b>. Метод основан на выявлении
            признаков нестабильной работы скважины (накопление и периодический вынос
            жидкости) по фиксированным окнам наблюдения: динамика дебита и давлений —
            <b>${_w.trend_days}</b> сут, простои и продувки — <b>${_w.downtime_days}</b> сут,
            отклик дебита после остановки — вся доступная история. Окна фиксированы
            намеренно — чтобы результат был воспроизводим и сравним между скважинами и
            датами. По каждому из 7 параметров рассчитывается отклонение от стабильного
            режима (0 — спокоен, 100 — максимум нестабильности); их взвешенная сумма даёт
            индекс нестабильности <b>I</b> (0–100), а <b>L*</b> — число суток устойчивой
            работы к указанной дате. Чем больше красных лепестков наружу — тем выше
            вероятность, что скважина является кандидатом на обводнение и обработку ТПАВ.
          </div>`;
      }
      // Рекомендации (ТПАВ): редактируемый текст (params.recommendation), при
      // пустом — дефолт STABILITY_RECOMMENDATION_DEFAULT. Только для режима
      // нестабильности (кандидат), не для оценки ПАВ. Parity: _format_stability_block.
      let recHtml = '';
      if (!effMode && parts.recommendation !== false) {
        const recText = (p.recommendation || '').trim() || STABILITY_RECOMMENDATION_DEFAULT;
        recHtml = `<div style="margin-top:8px; padding:10px 12px; background:#fffbeb;
             border:1px dashed #d97706; border-radius:6px; font-size:0.82rem;
             color:#713f12; white-space:pre-wrap;"><b>Рекомендации:</b>\n${_escHtml(recText)}</div>`;
      }
      return methodHtml + hdrHtml + roseBox + barBox + tableHtml + descrHtml + recHtml;
    }

    if (b.kind === 'criteria_rose') {
      // WYSIWYG: реальные Plotly роза + бар + таблица. Контейнеры с уникальными
      // id, Plotly.newPlot вызывается из renderChapterPreview после insert.
      const bid = b.id || 'x';
      const h = snap.history || {};
      // Формат сырого значения метрики — 1:1 как PDF (_fmt_raw в _format_rose_block).
      const _fmtRaw = (v) => {
        if (v == null || !isFinite(v)) return '—';
        const av = Math.abs(v);
        if (av >= 100) return v.toFixed(1);
        if (av >= 1)   return v.toFixed(2);
        if (av >= 0.01) return v.toFixed(3);
        return v.toFixed(4);
      };
      // Русская подпись режима — 1:1 как PDF (mode_label в _format_rose_block).
      const _modeLabel = {
        balanced: 'сбалансированный', liquid: 'жидкость/обводнение',
        gsp: 'газ-сепаратор/шлейф', purge_cycles: 'продувочные циклы',
        custom: 'пользовательские веса',
      }[snap.mode] || (snap.mode || '—');
      const hdrHtml = `
        <div style="font-size:0.85rem;">
          Скважина: <b>${_escHtml(snap.well_number)}</b>, период <b>${_escHtml(snap.period_from)} — ${_escHtml(snap.period_to)}</b>
          (${snap.period_days || '?'} дн.), штуцер <b>${h.choke_mm ?? '—'} мм</b>,
          режим <b>${_escHtml(_modeLabel)}</b>,
          окон в истории: <b>${h.windows_count || 0}</b>.
        </div>
        <div style="font-size:1.0rem; font-weight:700; color:#1f2937; margin:4px 0;">
          Балл кандидата: ${(snap.score ?? 0).toFixed(1)} из 100
        </div>
        ${comment}`;
      // Контейнеры для розы и бар-чарта
      const roseBox = (parts.rose_chart !== false)
        ? `<div id="prev-${bid}-rose" style="height:280px;"></div>` : '';
      const barBox = (parts.contributions_chart !== false)
        ? `<div id="prev-${bid}-rosebar" style="height:200px; margin-top:6px;"></div>` : '';
      // Таблица 6 критериев — колонки/значения/зоны 1:1 как PDF (Табл. 2.N.1).
      let tableHtml = '';
      if (parts.metrics_table !== false) {
        const ranks = snap.ranks || {};
        const contrib = snap.contributions || {};
        const currentRaw = snap.current_raw || {};
        const historyMed = snap.history_median_raw || {};
        const units = snap.units || {};
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
          const zone = r == null ? 'нет данных'
            : r > 75 ? 'риск (>75)'
            : r > 50 ? 'хуже нормы'
            : r < 50 ? 'лучше нормы'
            : 'норма';
          rows += `<tr><td style="padding:2px 6px;">${_escHtml(labels[k] || k)}</td>
                       <td style="text-align:right; padding:2px 6px; font-family:monospace;">${_fmtRaw(currentRaw[k])}</td>
                       <td style="text-align:right; padding:2px 6px; font-family:monospace; color:#6b7280;">${_fmtRaw(historyMed[k])}</td>
                       <td style="text-align:center; padding:2px 6px;">${_escHtml(units[k] || '')}</td>
                       <td style="text-align:right; padding:2px 6px; font-weight:600;">${r == null ? '—' : r.toFixed(0)}</td>
                       <td style="text-align:right; padding:2px 6px;">${(c.weight ?? 0).toFixed(2)}</td>
                       <td style="text-align:right; padding:2px 6px; color:#d62728;">${(c.actual ?? 0).toFixed(2)}</td>
                       <td style="padding:2px 6px;">${zone}</td></tr>`;
        });
        tableHtml = `
          <table style="width:100%; margin-top:6px; font-size:0.78rem; border-collapse:collapse;">
            <tr style="background:#f3f4f6;">
              <th style="text-align:left; padding:3px 6px;">Критерий</th>
              <th style="text-align:right; padding:3px 6px;">Период</th>
              <th style="text-align:right; padding:3px 6px;">Норма</th>
              <th style="text-align:center; padding:3px 6px;">Ед.</th>
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
      // Поясняющий абзац «Как читать» — 1:1 как PDF (adaptation_report.tex).
      const howToHtml = `
        <div style="margin-top:6px; font-size:0.72rem; color:#6b7280; font-style:italic;">
          Как читать: серая зона (r≤50) — ось внутри = период не хуже медианы
          собственных окон. Красный лепесток наружу — хуже нормы; зелёный внутрь —
          лучше. Светло-красное кольцо 75–100 — зона риска. Число у конца лепестка —
          точный ранг 0–100. Бар-чарт: цифра у полосы = ранг × вес;
          сумма = балл кандидата.
        </div>`;
      return hdrHtml + roseBox + barBox + tableHtml + warnHtml + howToHtml;
    }

    // ─── Observation blocks (датчики) ───
    if (b.kind && b.kind.startsWith('observation_')) {
      return _buildObservationBlockHtml(snap, b, parts) + comment;
    }

    // ─── Adaptation blocks (Step 5 wizard) ───
    if (b.kind === 'adaptation_period_analysis') {
      return _buildAdaptationPeriodAnalysisHtml(snap, b, parts) + comment;
    }
    // ─── Period report block (Step 8 wizard): зеркало adaptation_period_analysis,
    //     тот же снапшот → тот же рендер (PARITY HTML↔PDF). ───
    if (b.kind === 'period_full_analysis') {
      return _buildAdaptationPeriodAnalysisHtml(snap, b, parts) + comment;
    }
    // ─── Сравнение метрик периода (выбранные источники) ───
    if (b.kind === 'period_comparison') {
      return _buildPeriodComparisonHtml(snap, b, parts) + comment;
    }
    if (b.kind === 'optimal_window') {
      return _buildOptimalWindowHtml(snap, b, parts) + comment;
    }
    if (b.kind === 'reagent_irv_summary') {
      return _buildReagentIrvSummaryHtml(snap, b, parts) + comment;
    }
    if (b.kind === 'adaptation_effectiveness') {
      return _buildAdaptationEffectivenessHtml(snap, b, parts) + comment;
    }

    if (b.kind === 'works_analysis') {
      // _v='2' снимок: роза 8 осей + описание + Δ-таблица + Балл БЭР.
      // SOURCE OF TRUTH для PARITY: PDF (Фаза 4) зеркалит эту ветку.
      // Plotly-роза рендерится в _renderWorksAnalysisChart (пост-вставочный хук).
      const bid  = b.id || 'x';
      const pm   = snap.period_main || {};
      const pr   = snap.period_ref  || null;
      const isCompare = snap.mode === 'compare' && !!pr;
      const flags  = snap.flags        || {};
      const descrs = snap.descriptions || {};

      // Локальный форматировщик чисел
      const _fmtN = (v, dec) => {
        if (v == null || !isFinite(+v)) return '—';
        return (+v).toFixed(dec != null ? dec : 2);
      };

      // Балл БЭР: цвет по порогам (≥70 зел, 55-69 светло-зел, 45-54 сер, 30-44 ор, <30 красн)
      const scoreVal   = snap.score_ber;
      const scoreColor = scoreVal == null  ? '#6b7280'
        : scoreVal >= 70 ? '#16a34a'
        : scoreVal >= 55 ? '#65a30d'
        : scoreVal >= 45 ? '#6b7280'
        : scoreVal >= 30 ? '#d97706'
        : '#dc2626';

      // 1. Шапка
      const hdrHtml = '<div style="font-size:0.85rem; margin-bottom:4px;">'
        + 'Период: <b>' + _escHtml(pm.from || '—') + ' — ' + _escHtml(pm.to || '—') + '</b>'
        + ' (' + (pm.days || '?') + ' дн., ' + (pm.n_points || '?') + ' точек)'
        + (isCompare
          ? '; сравнение с <b>' + _escHtml(pr.from || '—') + ' — ' + _escHtml(pr.to || '—') + '</b>'
          : '')
        + '.</div>'
        + comment;

      // 2. Контейнер розы (single и compare; Plotly вызывается в пост-вставочном хуке)
      const roseBox = '<div id="prev-' + bid + '-worksrose" style="height:360px; margin-top:8px;"></div>';

      // 3. Описание (из снимка, заморожено)
      const descrHtml = pm.description
        ? '<div style="margin-top:8px; font-size:0.82rem; color:#374151;">' + _escHtml(pm.description) + '</div>'
        : '';
      const roseNoteHtml = descrs.rose_note
        ? '<div style="margin-top:4px; font-size:0.72rem; color:#6b7280;">' + _escHtml(descrs.rose_note) + '</div>'
        : '';

      // 3b. Метрики периода (в single — основной контент; в compare — справочно)
      let metricsHtml = '';
      const _pm = pm.metrics || {};
      const _ps = pm.purge_stats || {};
      const _metRows = [
        { label: 'Дебит Q, тыс.м³/сут',    v: _fmtN(_pm.median_flow_rate, 2) },
        { label: 'ΔP, кгс/см²',             v: _fmtN(_pm.median_dp, 2) },
        { label: 'Загрузка, %',              v: _fmtN(_pm.utilization_pct, 1) },
        { label: 'Частота вбросов, шт/сут', v: _fmtN(_pm.inj_freq, 3) },
        { label: 'Продувок за период',      v: _fmtN(_ps.count, 0) },
      ].filter(function(r) { return r.v !== '—'; });
      if (_metRows.length) {
        metricsHtml = '<table style="margin-top:8px; border-collapse:collapse; font-size:0.78rem;">'
          + _metRows.map(function(r) {
              return '<tr><td style="padding:2px 8px 2px 0; color:#6b7280;">' + r.label + '</td>'
                   + '<td style="font-weight:600; font-family:monospace; padding:2px 0;">' + r.v + '</td></tr>';
            }).join('')
          + '</table>';
      }

      // 4. Балл БЭР + вердикт (только compare)
      let scoreHtml = '';
      if (isCompare) {
        if (scoreVal != null) {
          scoreHtml = '<div style="margin-top:10px; display:flex; align-items:flex-start; gap:16px; flex-wrap:wrap;">'
            + '<div>'
            + '<div style="font-size:2.0rem; font-weight:700; color:' + scoreColor + '; line-height:1.1;">'
            + (+scoreVal).toFixed(1) + '</div>'
            + '<div style="font-size:0.72rem; color:#6b7280;">Балл БЭР / 100</div>'
            + '</div>'
            + (snap.verdict_text
              ? '<div style="flex:1; min-width:180px; padding-top:4px;">'
                + '<div style="font-size:0.88rem; font-weight:600; color:#1f2937;">' + _escHtml(snap.verdict_text) + '</div>'
                + '</div>'
              : '')
            + '</div>';
        }
        if (descrs.score_disclaimer) {
          scoreHtml += '<div style="margin-top:4px; font-size:0.70rem; color:#9ca3af;">'
            + _escHtml(descrs.score_disclaimer) + '</div>';
        }
      }

      // 5. Δ-таблица (только compare)
      let deltaHtml = '';
      if (isCompare && snap.delta_table && snap.delta_table.length) {
        let dtRows = '';
        snap.delta_table.forEach(function(row) {
          const dc = row.good === true  ? '#16a34a'
                   : row.good === false ? '#dc2626'
                   : '#6b7280';
          const dv = row.delta;
          const fmtVal = (v) => (v == null || !isFinite(+v))
            ? '—'
            : (+v).toFixed(2) + (row.unit ? ' ' + row.unit : '');
          const ds = (dv == null || !isFinite(+dv))
            ? '—'
            : ((+dv > 0 ? '+' : '') + (+dv).toFixed(2) + (row.unit ? ' ' + row.unit : ''));
          dtRows += '<tr>'
            + '<td style="padding:3px 6px; font-size:0.78rem;">' + _escHtml(row.label || row.key || '') + '</td>'
            + '<td style="text-align:right; padding:3px 6px; font-size:0.78rem; font-family:monospace;">' + fmtVal(row.value_ref) + '</td>'
            + '<td style="text-align:right; padding:3px 6px; font-size:0.78rem; font-family:monospace;">' + fmtVal(row.value_main) + '</td>'
            + '<td style="text-align:right; padding:3px 6px; font-size:0.78rem; font-family:monospace; font-weight:600; color:' + dc + ';">' + ds + '</td>'
            + '</tr>';
        });
        deltaHtml = '<table style="width:100%; margin-top:8px; border-collapse:collapse;">'
          + '<thead><tr style="background:#f3f4f6;">'
          + '<th style="text-align:left; padding:4px 6px; font-size:0.76rem; font-weight:600;">Критерий</th>'
          + '<th style="text-align:right; padding:4px 6px; font-size:0.76rem; font-weight:600;">До</th>'
          + '<th style="text-align:right; padding:4px 6px; font-size:0.76rem; font-weight:600;">Период</th>'
          + '<th style="text-align:right; padding:4px 6px; font-size:0.76rem; font-weight:600;">Δ</th>'
          + '</tr></thead>'
          + '<tbody>' + dtRows + '</tbody>'
          + '</table>';
      }

      // 6. Флаги-предупреждения
      let flagsHtml = '';
      if (flags.insufficient_data) {
        flagsHtml += '<div style="margin-top:6px; padding:5px 10px; background:#fef3c7; border-left:3px solid #f59e0b; font-size:0.78rem; color:#92400e; border-radius:3px;">&#9888; Недостаточно данных в периоде — результаты ориентировочны.</div>';
      }
      if (flags.insufficient_ref) {
        flagsHtml += '<div style="margin-top:6px; padding:5px 10px; background:#fef3c7; border-left:3px solid #f59e0b; font-size:0.78rem; color:#92400e; border-radius:3px;">&#9888; Недостаточно данных в эталонном периоде — сравнение ориентировочно.</div>';
      }
      if (flags.external_pline) {
        flagsHtml += '<div style="margin-top:6px; padding:5px 10px; background:#fef3c7; border-left:3px solid #f59e0b; font-size:0.78rem; color:#92400e; border-radius:3px;">&#9888; Снижение ΔP связано с ростом давления в шлейфе — внешний фактор.</div>';
      }
      if (flags.choke_changed) {
        flagsHtml += '<div style="margin-top:6px; padding:5px 10px; background:#fef3c7; border-left:3px solid #f59e0b; font-size:0.78rem; color:#92400e; border-radius:3px;">&#9888; Штуцер менялся — Q/ΔP сравнивать осторожно.</div>';
      }
      if (flags.purges_no_markers) {
        flagsHtml += '<div style="margin-top:6px; padding:5px 10px; background:#fef3c7; border-left:3px solid #f59e0b; font-size:0.78rem; color:#92400e; border-radius:3px;">&#9888; Продувки без маркеров — точность снижена.</div>';
      }

      return hdrHtml + roseBox + descrHtml + roseNoteHtml + metricsHtml + scoreHtml + deltaHtml + flagsHtml;
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

  // ===== _renderSensorCustomerComparisonCharts — графики сопоставления LoRa/УзКорГаз =====
  function _renderSensorCustomerComparisonCharts(snap, idPrefix) {
    if (!window.Plotly) return;

    var curves = snap.curves || {};
    // Умеренная высота + компактная горизонтальная легенда (мелкий шрифт, короткие
    // имена). Q и ΔP — половинной ширины (2 колонки) → легенда в 2 ряда; dev —
    // полная ширина. В PDF Q и ΔP ставятся рядом (0.49\textwidth).
    var layout = {
      height: 250,
      margin: { l: 50, r: 16, t: 16, b: 56 },
      legend: { orientation: 'h', y: -0.30, x: 0, font: { size: 10 } },
      showlegend: true,
    };
    var config = { responsive: true, displayModeBar: false };

    // Линия линейного тренда по ряду {dates, values}
    function trendTrace(series, color, name) {
      if (!series || !series.dates || series.dates.length < 2) return null;
      var ds = series.dates, vs = series.values || [];
      var idx = [], yy = [];
      for (var i = 0; i < ds.length; i++) { if (vs[i] != null && !isNaN(vs[i])) { idx.push(i); yy.push(Number(vs[i])); } }
      if (idx.length < 2) return null;
      var nn = idx.length, sx = 0, sy = 0, sxx = 0, sxy = 0;
      for (var j = 0; j < nn; j++) { sx += idx[j]; sy += yy[j]; sxx += idx[j] * idx[j]; sxy += idx[j] * yy[j]; }
      var den = nn * sxx - sx * sx; if (den === 0) return null;
      var slope = (nn * sxy - sx * sy) / den, intercept = (sy - slope * sx) / nn;
      var i0 = idx[0], i1 = idx[nn - 1];
      return { x: [ds[i0], ds[i1]], y: [slope * i0 + intercept, slope * i1 + intercept],
               mode: 'lines', name: name, line: { color: color, dash: 'dash', width: 1.3 },
               hoverinfo: 'skip', showlegend: false };
    }

    // Q chart — наложение двух дебитов (рабочий + общий) LoRa vs УзКорГаз
    var qEl = document.getElementById(idPrefix + 'chart-q');
    var hasQ = (curves.sensor_q && curves.customer_q) ||
               (curves.sensor_q_total && curves.customer_q_total);
    if (qEl && hasQ) {
        var qTraces = [];
        if (curves.sensor_q) qTraces.push({
            x: curves.sensor_q.dates || [], y: curves.sensor_q.values || [],
            name: 'LoRa Q раб.', mode: 'lines+markers', marker: { size: 4 },
            line: { color: '#1976d2' },
        });
        if (curves.customer_q) qTraces.push({
            x: curves.customer_q.dates || [], y: curves.customer_q.values || [],
            name: 'УзКГ Q раб.', mode: 'lines+markers', marker: { size: 4 },
            line: { color: '#ff9800', dash: 'dot' },
        });
        if (curves.sensor_q_total) qTraces.push({
            x: curves.sensor_q_total.dates || [], y: curves.sensor_q_total.values || [],
            name: 'LoRa Q общ.', mode: 'lines+markers', marker: { size: 4 },
            line: { color: '#0d47a1', width: 1.2 },
        });
        if (curves.customer_q_total) qTraces.push({
            x: curves.customer_q_total.dates || [], y: curves.customer_q_total.values || [],
            name: 'УзКГ Q общ.', mode: 'lines+markers', marker: { size: 4 },
            line: { color: '#e65100', dash: 'dot', width: 1.2 },
        });
        // Линии трендов (рабочий дебит)
        var tS = trendTrace(curves.sensor_q, '#1976d2', 'тренд LoRa');
        var tC = trendTrace(curves.customer_q, '#ff9800', 'тренд УзКГ');
        if (tS) qTraces.push(tS);
        if (tC) qTraces.push(tC);
        try {
          Plotly.newPlot(qEl, qTraces, { ...layout, yaxis: { title: 'тыс.м³/сут' } }, config);
        } catch (e) {
          qEl.innerHTML = '<div style="color:#dc2626;">Ошибка: ' + e.message + '</div>';
        }
    }

    // DP chart — рендерится только если элемент есть
    var dpEl = document.getElementById(idPrefix + 'chart-dp');
    if (dpEl && curves.sensor_dp && curves.customer_dp) {
        var dpTraces = [
          {
            x: curves.sensor_dp.dates || [],
            y: curves.sensor_dp.values || [],
            name: 'LoRa',
            mode: 'lines+markers',
            marker: { size: 4 },
            line: { color: '#1976d2' },
          },
          {
            x: curves.customer_dp.dates || [],
            y: curves.customer_dp.values || [],
            name: 'УзКорГаз',
            mode: 'lines+markers',
            marker: { size: 4 },
            line: { color: '#ff9800', dash: 'dot' },
          },
        ];
        try {
          // ΔP — шкала от нуля (перепад не бывает отрицательным).
          Plotly.newPlot(dpEl, dpTraces, { ...layout, yaxis: { title: 'кгс/см²', rangemode: 'tozero' } }, config);
        } catch (e) {
          dpEl.innerHTML = '<div style="color:#dc2626;">Ошибка: ' + e.message + '</div>';
        }
    }

    // График ОТНОСИТЕЛЬНОГО отклонения Δ% = (LoRa − УзКГ)/УзКГ·100 по дням,
    // с зонами допуска: зелёная ±15%, жёлтая 15–30%, красная >30%.
    var devEl = document.getElementById(idPrefix + 'chart-dev');
    var dd = snap.daily_diff || [];
    if (devEl && dd.length) {
      // Стиль и ПОРОГИ зон допуска — настраиваются в модалке (snapshot.display)
      var _disp = snap.display || {};
      var devStyle = _disp.dev_style || 'line';
      var tol1 = (Number(_disp.dev_tol_green) > 0) ? Number(_disp.dev_tol_green) : 15;
      var tol2 = (Number(_disp.dev_tol_amber) > tol1) ? Number(_disp.dev_tol_amber) : tol1 + 1;
      var ddates = [], dpct = [], dcolors = [];
      dd.forEach(function (r) {
        // Относительно данных LoRa (сравниваем заказчика с нашим датчиком = эталон)
        var pct = (r.delta_q != null && r.sensor_q) ? (r.delta_q / r.sensor_q * 100) : null;
        ddates.push(r.date); dpct.push(pct);
        var a = (pct == null || !isFinite(pct)) ? null : Math.abs(pct);
        dcolors.push(a == null ? '#9ca3af' : (a <= tol1 ? '#16a34a' : (a <= tol2 ? '#f59e0b' : '#dc2626')));
      });
      var finite = dpct.filter(function (v) { return v != null && isFinite(v); });
      var amax = finite.length ? Math.max(tol2 + 5, Math.ceil(Math.max.apply(null, finite.map(Math.abs)) / 5) * 5) : tol2 + 5;
      var band = function (y0, y1, color) { return { type: 'rect', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: y0, y1: y1, fillcolor: color, line: { width: 0 }, layer: 'below' }; };
      var hline = function (y) { return { type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: y, y1: y, line: { color: '#9ca3af', width: 1, dash: 'dot' } }; };
      var devShapes = [
        band(-tol1, tol1, 'rgba(22,163,74,0.12)'),
        band(tol1, tol2, 'rgba(245,158,11,0.12)'), band(-tol2, -tol1, 'rgba(245,158,11,0.12)'),
        band(tol2, amax, 'rgba(220,38,38,0.09)'), band(-amax, -tol2, 'rgba(220,38,38,0.09)'),
        hline(tol1), hline(-tol1), hline(tol2), hline(-tol2),
      ];
      var hov = '%{x}<br>Δ %{y:.0f}%<extra></extra>';
      var devTraces;
      if (devStyle === 'bar') {
        devTraces = [{ x: ddates, y: dpct, type: 'bar', marker: { color: dcolors }, hovertemplate: hov }];
      } else if (devStyle === 'stem') {
        var lx = [], ly = [];
        ddates.forEach(function (d, i) { lx.push(d, d, null); ly.push(0, dpct[i], null); });
        devTraces = [
          { x: lx, y: ly, type: 'scatter', mode: 'lines', line: { color: '#94a3b8', width: 1.3 }, hoverinfo: 'skip', showlegend: false },
          { x: ddates, y: dpct, type: 'scatter', mode: 'markers', marker: { color: dcolors, size: 9, line: { color: '#fff', width: 1 } }, hovertemplate: hov },
        ];
      } else if (devStyle === 'area') {
        devTraces = [
          { x: ddates, y: dpct, type: 'scatter', mode: 'lines', line: { color: '#64748b', width: 1.5 }, fill: 'tozeroy', fillcolor: 'rgba(100,116,139,0.12)', hoverinfo: 'skip', showlegend: false },
          { x: ddates, y: dpct, type: 'scatter', mode: 'markers', marker: { color: dcolors, size: 7 }, hovertemplate: hov },
        ];
      } else {  // 'line' — линия с маркерами (по умолчанию)
        devTraces = [
          { x: ddates, y: dpct, type: 'scatter', mode: 'lines', line: { color: '#64748b', width: 1.5 }, hoverinfo: 'skip', showlegend: false },
          { x: ddates, y: dpct, type: 'scatter', mode: 'markers', marker: { color: dcolors, size: 8, line: { color: '#fff', width: 1 } }, hovertemplate: hov },
        ];
      }
      var devLayout = { ...layout, showlegend: false, shapes: devShapes,
        yaxis: { title: 'Δ, % к LoRa', range: [-amax, amax], zeroline: true, zerolinecolor: '#111827', zerolinewidth: 1.5 } };
      try { Plotly.newPlot(devEl, devTraces, devLayout, config); }
      catch (e) { devEl.innerHTML = '<div style="color:#dc2626;">Ошибка: ' + e.message + '</div>'; }
    }
  }

  // ─────────────────────────────────────────────────────────────────
  //  renderChapter — параметризованный движок (из renderChapterPreview).
  //  Параметризованы 4 точки: containerId, countId, chapterTitle, kindOrder.
  //  Графики (_render*Charts) вызываются опционально — через typeof guard
  //  (в этой версии движка только текст; графики — следующий под-шаг).
  // ─────────────────────────────────────────────────────────────────
  // ===== _buildSensorParamsHtml — таблица техпараметров датчиков (статическая) =====
  // Зеркало adaptation_report.tex §3.2 «Технические параметры». PARITY HTML↔PDF.
  function _buildSensorParamsHtml() {
    var rows = [
      ['Напряжение питания', '3,6 В DC, литий-ионная батарея'],
      ['Диапазон давления', '−0,1…6 МПа'],
      ['Ток возбуждения', '0,2 мА'],
      ['Интерфейс связи', 'беспроводной сигнал'],
      ['Рабочая температура', '−20…+60 °C'],
      ['Рабочая влажность', '0…80 % RH'],
      ['Точность измерения', '0,5 % FS (от полной шкалы)'],
      ['Интервал между измерениями', '60 сек'],
    ];
    var tr = rows.map(function(r){
      return '<tr><td style="padding:3px 8px;border:1px solid #e5e7eb;">' + _escHtml(r[0]) +
        '</td><td style="padding:3px 8px;border:1px solid #e5e7eb;text-align:right;">' + _escHtml(r[1]) + '</td></tr>';
    }).join('');
    return '<div style="margin:8px 0 14px;">' +
      '<div style="font-weight:600; font-size:0.88rem; color:#374151; margin-bottom:3px;">Табл. 3.1.1. Технические параметры измерительных датчиков</div>' +
      '<table style="width:100%; border-collapse:collapse; font-size:0.82rem;">' +
      '<thead><tr style="background:#f3f4f6;"><th style="text-align:left;padding:4px 8px;border:1px solid #e5e7eb;">Параметр</th>' +
      '<th style="text-align:right;padding:4px 8px;border:1px solid #e5e7eb;">Значение</th></tr></thead><tbody>' + tr +
      '</tbody></table></div>';
  }

  // ===== _buildObsIntroSectionsHtml — §3.1 период/описание + §3.2 список датчиков =====
  // Динамические данные (cfg.obsIntro) с эндпоинта /observation-intro. PARITY с PDF §3.1/§3.2.
  function _buildObsIntroSectionsHtml(intro) {
    if (!intro) return '';
    var h = '';
    if (intro.intro_text) {
      h += '<div style="font-weight:600;font-size:0.95rem;color:#374151;margin:10px 0 3px;">3.1. Период и описание этапа</div>';
      h += '<div style="font-size:0.84rem;color:#374151;line-height:1.55;margin-bottom:10px;">' + _escHtml(intro.intro_text) + '</div>';
    }
    var sensors = intro.sensors || [];
    if (sensors.length) {
      var tr = sensors.map(function(s){
        return '<tr><td style="padding:3px 8px;border:1px solid #e5e7eb;">'+_escHtml(s.serial||'—')+
          '</td><td style="padding:3px 8px;border:1px solid #e5e7eb;">'+_escHtml(s.label||'—')+
          '</td><td style="padding:3px 8px;border:1px solid #e5e7eb;">'+_escHtml(s.position_label||s.position||'—')+
          '</td><td style="padding:3px 8px;border:1px solid #e5e7eb;text-align:right;">'+_escHtml(s.installed_at_fmt||'—')+'</td></tr>';
      }).join('');
      h += '<div style="font-weight:600;font-size:0.95rem;color:#374151;margin:10px 0 3px;">3.2. Датчики давления</div>';
      h += '<div style="font-weight:600;font-size:0.85rem;color:#374151;margin-bottom:3px;">Табл. 3.1. Датчики, на которых построен этап.</div>';
      h += '<table style="width:100%;border-collapse:collapse;font-size:0.82rem;"><thead><tr style="background:#f3f4f6;">'+
        '<th style="text-align:left;padding:4px 8px;border:1px solid #e5e7eb;">Серийный номер</th>'+
        '<th style="text-align:left;padding:4px 8px;border:1px solid #e5e7eb;">Метка</th>'+
        '<th style="text-align:left;padding:4px 8px;border:1px solid #e5e7eb;">Положение</th>'+
        '<th style="text-align:right;padding:4px 8px;border:1px solid #e5e7eb;">Дата установки</th></tr></thead><tbody>'+tr+'</tbody></table>';
    }
    return h;
  }

  // ===== _buildFormulasHtml — статическая секция «Формулы и методика» =====
  // Зеркало adaptation_report.tex §3.2.1 (PARITY HTML↔PDF). Текст со страницы скважины.
  function _buildFormulasHtml() {
    return '<div style="margin:8px 0 16px; padding:10px 12px; background:#f8fafc;' +
      ' border:1px solid #e2e8f0; border-radius:6px;">' +
      '<div style="font-weight:600; font-size:0.95rem; color:#374151; margin-bottom:6px;">3.2.1. Формулы и методика расчёта</div>' +
      '<div style="font-size:0.84rem; color:#374151; line-height:1.5;">' +
      'Расчётный дебит газа определяется по модифицированной формуле Гилберта—Пирвердяна, ' +
      'связывающей перепад давлений на штуцере (P<sub>тр</sub> и P<sub>шл</sub>) с расходом газа.' +
      '<div style="margin:8px 0; font-family:serif;"><b>Докритический режим</b> (r &lt; 0,5):<br>' +
      '<span style="margin-left:12px;">Q = M · (C₁ · P<sub>тр</sub> − C₂ · P<sub>шл</sub>) · d² / C₃</span></div>' +
      '<div style="margin:8px 0; font-family:serif;"><b>Критический режим</b> (r ≥ 0,5):<br>' +
      '<span style="margin-left:12px;">Q = M · C₁ · P<sub>тр</sub> · (1 − r) · d² / C₃,&nbsp;&nbsp; r = P<sub>шл</sub> / P<sub>тр</sub></span></div>' +
      '<b>Обозначения:</b> Q — дебит газа, тыс.м³/сут; M — калибровочный множитель (M=4,1); ' +
      'P<sub>тр</sub> — устьевое (трубное) давление, кгс/см²; P<sub>шл</sub> — давление шлейфа, кгс/см²; ' +
      'd — диаметр штуцера, мм; C₁=2,919, C₂=4,654, C₃=286,95 — эмпирические константы; r — отношение давлений.' +
      '<div style="margin-top:8px;"><b>Алгоритм расчёта:</b><ol style="margin:4px 0 0 18px; padding:0;">' +
      '<li>Чтение минутных данных давления из БД (pressure_raw).</li>' +
      '<li>Очистка: отрицательные и нулевые значения → NaN, интерполяция (ffill/bfill).</li>' +
      '<li>Сглаживание: фильтр Савицкого—Голея (окно 17, полином 3, 2 прохода).</li>' +
      '<li>Расчёт дебита по формуле для каждой минутной записи.</li>' +
      '<li>Детекция продувок по маркерам в БД и алгоритмически по кривой давления.</li>' +
      '<li>Потери при стравливании — по уравнению Бернулли через штуцер (только фаза продувки).</li>' +
      '<li>Простои: периоды P<sub>тр</sub> ≤ P<sub>шл</sub> (дебит = 0, потерь нет).</li>' +
      '<li>Накопленный дебит — интегрирование трапециями (Δt = 1/1440 сут.).</li></ol></div>' +
      '<div style="margin-top:8px;"><b>Продувка и простой:</b><br>' +
      '• <i>Продувка (стравливание):</i> задвижка открыта, газ стравливается в атмосферу; потери считаются через штуцер (Бернулли).<br>' +
      '• <i>Простой</i> (P<sub>тр</sub> ≤ P<sub>шл</sub>): газ не поступает в шлейф, дебит = 0; потерь нет.</div>' +
      '<div style="margin-top:8px;"><b>Потери при стравливании:</b> Q<sub>loss</sub> = C<sub>d</sub> · A · √(2·ΔP / ρ), ' +
      'где C<sub>d</sub> = 0,9 — коэффициент расхода; A = π·d²/4 — сечение штуцера продувки; ' +
      'ΔP = P<sub>уст</sub> − P<sub>атм</sub>; ρ — плотность газа при P<sub>уст</sub>.</div>' +
      '</div></div>';
  }

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

    // Сортировка блоков:
    // - useUserOrder: true → только по sort_order (пользователь задал порядок drag-drop)
    // - useUserOrder: false (default) → сначала по kind (KIND_ORDER), затем по sort_order
    if (cfg.useUserOrder) {
      // Порядок полностью определяется sort_order (из БД после drag-drop)
      inReport.sort(function (a, b) {
        return (a.sort_order || 0) - (b.sort_order || 0);
      });
    } else {
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
        // Отчёт за период (Step 8 wizard)
        period_full_analysis: 1,
        period_comparison: 2,
      };
      inReport.sort(function (a, b) {
        var ka = KIND_ORDER[a.kind] || 99;
        var kb = KIND_ORDER[b.kind] || 99;
        if (ka !== kb) return ka - kb;
        return (a.sort_order || 0) - (b.sort_order || 0);
      });
    }

    var html =
      '<h3 style="margin:0 0 8px; font-size:1.05rem; color:#374151; ' +
      'border-bottom:1.5px solid #d1d5db; padding-bottom:4px;">' +
      _escHtml(cfg.chapterTitle || 'Глава') + '</h3>';
    if (cfg.sourceLabel) {
      html += '<div style="font-size:0.78rem; color:#6b7280; margin-bottom:14px;">' +
              _escHtml(cfg.sourceLabel) +
              ' В отчёт включено: ' + inReport.length + '.</div>';
    }

    // Общий вводный текст главы «Наблюдение» (паритет с PDF §3, начало главы).
    if (cfg.showObsIntro === true) {
      html += '<div style="font-size:0.84rem; color:#374151; line-height:1.55; margin:6px 0 14px;">' +
        '<b>Этап наблюдения</b> — период фиксации фактического режима работы скважины по данным ' +
        'датчиков давления (UniTool) до начала адаптации. <b>Цель этапа:</b> получить эталонную ' +
        'картину работы скважины (расчётный дебит, перепад давления, устьевое и линейное давление, ' +
        'простои), с которой далее сравнивается режим на этапе адаптации.</div>';
      // §3.1 период/описание + §3.2 список датчиков (динамические данные с эндпоинта).
      if (cfg.obsIntro && typeof _buildObsIntroSectionsHtml === 'function') {
        html += _buildObsIntroSectionsHtml(cfg.obsIntro);
      }
      // Таблица техпараметров датчиков (§3.2, паритет с PDF).
      if (typeof _buildSensorParamsHtml === 'function') html += _buildSensorParamsHtml();
    }

    // Секция «Формулы и методика» (вкл/выкл галочкой в настройках главы) —
    // тот же статический текст, что в PDF (adaptation_report.tex §3.2.1). PARITY.
    if (cfg.showFormulas === true && typeof _buildFormulasHtml === 'function') {
      html += _buildFormulasHtml();
    }

    var numPrefix = cfg.numPrefix || '';
    inReport.forEach(function (b, i) {
     try {
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
     } catch (e) {
       console.warn('renderChapter block html', b && b.id, e);
       html += '<div style="color:#dc2626;font-size:0.8rem;">Блок #' + (b && b.id) + ': ошибка рендера.</div>';
     }
    });
    content.innerHTML = html;

    // Графики — опционально (следующий под-шаг). Без них движок рендерит текст.
    inReport.forEach(function (b) {
     try {
      // baseline/period_analysis — данные заказчика (UzKorGaz)
      if (b.kind === 'baseline' || b.kind === 'period_analysis') {
        if (typeof _renderBlockChartsInline === 'function') _renderBlockChartsInline(b);
      } else if (b.kind === 'criteria_rose') {
        if (typeof _renderRoseBlockCharts === 'function') _renderRoseBlockCharts(b);
      } else if (b.kind === 'stability_rose') {
        if (typeof _renderStabilityRoseCharts === 'function') _renderStabilityRoseCharts(b);
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
      } else if (b.kind === 'sensor_customer_comparison') {
        _renderSensorCustomerComparisonCharts(b.data_snapshot || {}, 'scc-' + b.id + '-');
      } else if (b.kind === 'pressure_spectrum') {
        _renderPressureSpectrumCharts(b.data_snapshot || {}, 'ps-' + b.id + '-');
      } else if (b.kind === 'param_correlation') {
        _renderParamCorrelationCharts(b.data_snapshot || {}, 'pc-' + b.id + '-');
      } else if (b.kind && b.kind.startsWith('observation_')) {
        // Observation блоки (датчики) — рендерим Plotly графики
        if (typeof _renderObservationBlockCharts === 'function') {
          _renderObservationBlockCharts(b);
        }
      } else if (b.kind === 'adaptation_period_analysis' || b.kind === 'period_full_analysis') {
        // Adaptation / Отчёт за период — те же Plotly графики (давления, ΔP, Q).
        // Префикс 'adapt-{id}-' общий: block.id уникален, .tex берёт те же ключи.
        _renderAdaptationCharts(b.data_snapshot || {}, 'adapt-' + b.id + '-');
      } else if (b.kind === 'period_comparison') {
        // Сравнение метрик периода — overlay Q/ΔP (prcmp-{id}-q/-dp).
        _renderPeriodComparisonCharts(b.data_snapshot || {}, 'prcmp-' + b.id + '-');
      } else if (b.kind === 'reagent_irv_summary') {
        // ИРВ — donut «вбросы по реагентам» + Score по времени
        _renderReagentIrvCharts(b.data_snapshot || {}, 'irv-' + b.id + '-');
      } else if (b.kind === 'adaptation_effectiveness') {
        // Оценка эффективности — диаграммы B1/B2/R/R★ (фильтр столбцов по parts)
        _renderEffectivenessChart(b.data_snapshot || {}, 'adapt-' + b.id + '-', (b.params && b.params.parts) || {});
      } else if (b.kind === 'works_analysis') {
        _renderWorksAnalysisChart(b.data_snapshot || {}, b.id || 'x');
      }
     } catch (e) { console.warn('renderChapter block charts', b && b.id, e); }
    });
  }

  window.renderChapter = renderChapter;
  // Точечный рендер ОДНОГО блока «роза нестабильности» в контейнер (без обёртки
  // главы) — для превью на странице Заказчика (аккордеон) и правой панели.
  window.renderStabilityBlock = function (b, containerId) {
    var el = document.getElementById(containerId);
    if (!el) return;
    try { el.innerHTML = _renderBlockSummaryHtml(b); } catch (e) { console.warn('stab html', e); return; }
    try { _renderStabilityRoseCharts(b); } catch (e) { console.warn('stab charts', e); }
  };
})();
