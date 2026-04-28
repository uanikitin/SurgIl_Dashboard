/**
 * Анализ эффективности реагента — клиентский код.
 */
(function() {
    'use strict';

    const API = '/api/reagent-analysis';
    let chartM2 = null;
    let detailPressureChart = null;
    let detailFlowChart = null;

    // ── Init ──
    document.addEventListener('DOMContentLoaded', async () => {
        await loadWells();
        initSliders();
        initDefaults();

        document.getElementById('ra-btn-analyze').addEventListener('click', runAnalysis);
        document.getElementById('ra-btn-excel').addEventListener('click', exportExcel);
        document.getElementById('ra-modal-close').addEventListener('click', closeModal);
        document.getElementById('ra-detail-modal').addEventListener('click', e => {
            if (e.target === e.currentTarget) closeModal();
        });
    });

    async function loadWells() {
        const sel = document.getElementById('ra-well');
        try {
            const res = await fetch(API + '/wells');
            const wells = await res.json();
            wells.forEach(w => {
                const opt = document.createElement('option');
                opt.value = w.id;
                opt.textContent = w.name || `Скв ${w.number}`;
                sel.appendChild(opt);
            });
        } catch(e) {
            console.error('Failed to load wells', e);
        }
    }

    function initSliders() {
        const mergeSlider = document.getElementById('ra-merge-window');
        const mergeVal = document.getElementById('ra-merge-val');
        mergeSlider.addEventListener('input', () => {
            mergeVal.textContent = mergeSlider.value + ' ч';
        });

        const graceSlider = document.getElementById('ra-grace');
        const graceVal = document.getElementById('ra-grace-val');
        graceSlider.addEventListener('input', () => {
            graceVal.textContent = graceSlider.value + ' м';
        });
    }

    function initDefaults() {
        const now = new Date();
        const from = new Date(now);
        from.setDate(from.getDate() - 90);
        document.getElementById('ra-date-to').value = fmt(now);
        document.getElementById('ra-date-from').value = fmt(from);
    }

    function fmt(d) {
        return d.toISOString().slice(0, 10);
    }

    // ── Analysis ──
    async function runAnalysis() {
        const wellId = +document.getElementById('ra-well').value;
        const dateFrom = document.getElementById('ra-date-from').value;
        const dateTo = document.getElementById('ra-date-to').value;
        const mergeWindow = +document.getElementById('ra-merge-window').value;
        const grace = +document.getElementById('ra-grace').value;

        if (!wellId || !dateFrom || !dateTo) return;

        showLoading(true);
        hideResults();

        try {
            const res = await fetch(API + '/analyze', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    well_id: wellId,
                    period_start: dateFrom + 'T00:00:00',
                    period_end: dateTo + 'T23:59:59',
                    merge_window_hours: mergeWindow,
                    grace_period_min: grace,
                }),
            });
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();
            renderResults(data);
        } catch(e) {
            console.error(e);
            showWarning('Ошибка: ' + e.message);
        } finally {
            showLoading(false);
        }
    }

    function renderResults(data) {
        // Warnings
        const warnEl = document.getElementById('ra-warnings');
        warnEl.innerHTML = '';
        (data.warnings || []).forEach(w => {
            const div = document.createElement('div');
            div.className = 'ra-warning';
            div.textContent = w;
            warnEl.appendChild(div);
        });

        if (!data.irv_results || data.irv_results.length === 0) {
            showWarning('Нет данных для анализа');
            return;
        }

        document.getElementById('ra-btn-excel').style.display = 'inline-block';

        // Cards
        const cardsEl = document.getElementById('ra-cards');
        cardsEl.style.display = 'flex';
        document.getElementById('ra-total-inj').textContent = data.injections_total;
        document.getElementById('ra-merged-inj').textContent =
            data.merged_injections + ' (после группировки)';
        document.getElementById('ra-choke').textContent =
            data.choke_mm != null ? data.choke_mm.toFixed(1) : 'н/д';

        // Best reagent
        if (data.best_reagent) {
            const b = data.best_reagent;
            document.getElementById('ra-best-name').textContent = b.reagent;
            const parts = [];
            if (b.median_q_per_unit != null) parts.push(b.median_q_per_unit.toFixed(2) + ' тыс. м\u00B3/шт');
            if (b.median_effect_hours != null) parts.push('эффект ' + b.median_effect_hours.toFixed(1) + ' ч');
            parts.push('Score ' + b.score.toFixed(2));
            document.getElementById('ra-best-detail').textContent = parts.join(' | ');
        }

        // Well id раньше — нужен клику по столбику для открытия модалки
        _irvWellId = +document.getElementById('ra-well').value;

        // Scores table
        renderScoresTable(data.scores);

        // Bar chart — Score per injection
        renderM2Chart(data.irv_results);

        // IRV table
        renderIRVTable(data.irv_results, _irvWellId);
    }

    function renderScoresTable(scores) {
        const block = document.getElementById('ra-scores-block');
        const body = document.getElementById('ra-scores-body');
        body.innerHTML = '';

        scores.forEach(s => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${esc(s.reagent)}</strong></td>
                <td>${s.irv_count}</td>
                <td>${s.valid_irv_count}</td>
                <td>${v(s.median_q_per_unit, 3)}</td>
                <td>${v(s.median_dp_gain, 2)} кгс/см\u00B2</td>
                <td>${v(s.median_effect_hours, 1)} ч</td>
                <td><strong>${s.score.toFixed(2)}</strong></td>
                <td><span class="score-badge score-${s.level}">${s.level_name}</span></td>
                <td>${renderFlags(s.flags)}</td>
            `;
            body.appendChild(tr);
        });
        block.style.display = 'block';
    }

    function renderFlags(flags) {
        if (!flags || flags.length === 0) return '';
        const map = {
            'деградация': 'ra-flag-degradation',
            'нестабильный': 'ra-flag-unstable',
            'мало данных': 'ra-flag-lowdata',
        };
        return flags.map(f =>
            `<span class="ra-flag ${map[f] || 'ra-flag-lowdata'}">${esc(f)}</span>`
        ).join('');
    }

    // Стабильная палитра для реагентов
    const REAGENT_COLORS = ['#3b82f6','#ef4444','#16a34a','#f59e0b','#8b5cf6',
                            '#ec4899','#0ea5e9','#22c55e','#d946ef','#14b8a6'];

    function _buildReagentColorMap(irvs) {
        const names = [...new Set(irvs.map(r => r.reagent))];
        const map = {};
        names.forEach((n, i) => { map[n] = REAGENT_COLORS[i % REAGENT_COLORS.length]; });
        return map;
    }

    // Цвет Score-бейджа по категории
    function _scoreColor(cat) {
        return cat === 'good'    ? '#16a34a'
             : cat === 'average' ? '#ca8a04'
             : cat === 'weak'    ? '#dc2626' : '#6b7280';
    }

    // Цвет мини-бара нормализованной метрики (0..1)
    function _normColor(n) {
        if (n == null) return '#9ca3af';
        if (n >= 0.66) return '#16a34a';
        if (n >= 0.33) return '#eab308';
        return '#dc2626';
    }

    function _renderChartTooltip(ctx) {
        const tooltipEl = document.getElementById('ra-chart-tooltip');
        const tt = ctx.tooltip;
        if (!tt || tt.opacity === 0) { tooltipEl.style.display = 'none'; return; }
        if (!tt.dataPoints || !tt.dataPoints.length) { tooltipEl.style.display = 'none'; return; }

        const dp = tt.dataPoints[0];
        const irv = dp.raw && dp.raw._irv;
        if (!irv) { tooltipEl.style.display = 'none'; return; }

        const m   = irv.metrics   || {};
        const ext = irv.extended  || {};
        const n   = ext.normalized || {};
        const sc  = ext.score;
        const cat = ext.category;
        const scLabel = cat === 'good' ? 'Хороший' : cat === 'average' ? 'Средний' : 'Слабый';

        const fmt = (v, d=2, u='') => (v == null) ? '—'
            : (Number(v).toFixed(d) + (u ? ' ' + u : ''));

        const metricRow = (label, val, norm) => {
            const nPct = (norm == null) ? 0 : Math.max(0, Math.min(1, norm)) * 100;
            const barColor = _normColor(norm);
            return `<tr>
              <td style="color:#6b7280;">${label}</td>
              <td style="text-align:right; white-space:nowrap;">${val}</td>
              <td><span class="rct-bar"><span style="width:${nPct.toFixed(0)}%; background:${barColor};"></span></span></td>
            </tr>`;
        };

        let html = '';
        html += `<div class="rct-head">${esc(irv.reagent)}`;
        if (irv.merged_count > 1) html += ` <small style="color:#6b7280;">(${irv.merged_count} вбр.)</small>`;
        html += `</div>`;
        html += `<div class="rct-sub">${fmtDt(irv.event_time)} · длит. ИРВ ${fmt(irv.duration_hours, 1)} ч`;
        if (irv.qty != null) html += ` · ${irv.qty} л`;
        html += `</div>`;

        if (sc != null) {
            html += `<div style="margin-bottom:8px;">
              <span class="rct-score" style="background:${_scoreColor(cat)};">${Number(sc).toFixed(0)}</span>
              <span style="font-weight:600; color:${_scoreColor(cat)};">${scLabel}</span>
            </div>`;
        }

        html += `<table>`;
        html += metricRow('Q/шт',          fmt(m.q_per_unit, 2, 'тыс.м³/шт'),   null);
        html += metricRow('ΔP прирост',    fmt(m.dp_gain, 2, 'кгс/см²'),        null);
        html += metricRow('Эффект',        fmt(m.effect_duration_hours, 1, 'ч'), null);
        html += metricRow('T_resp',        fmt(ext.response_time_h, 2, 'ч'),    n.resp);
        html += metricRow('Пик ΔP',        fmt(ext.peak_dp_gain, 2),            n.peak);
        html += metricRow('V_rise',        fmt(ext.rise_slope, 2),              n.rise);
        html += metricRow('Плато',         fmt(ext.plateau_total_h, 1, 'ч'),    n.plateau);
        html += metricRow('|V_decay|',     fmt(ext.decay_v_abs, 2),             n.decay_v);
        html += metricRow('Длит. спада',   fmt(ext.decay_total_h, 1, 'ч'),      n.decay_t);
        html += `</table>`;

        if (m.invalid_reason) {
            html += `<div style="margin-top:6px; color:#b45309; font-size:11px;">⚠ ${esc(m.invalid_reason)}</div>`;
        }

        tooltipEl.innerHTML = html;
        tooltipEl.style.display = 'block';

        // position: fixed → координаты в viewport-пространстве
        const rect = ctx.chart.canvas.getBoundingClientRect();
        const ttW = tooltipEl.offsetWidth;
        const ttH = tooltipEl.offsetHeight;
        const vw  = window.innerWidth;
        const vh  = window.innerHeight;

        let left = rect.left + tt.caretX + 12;
        let top  = rect.top  + tt.caretY - ttH / 2;
        if (left + ttW > vw - 8) left = rect.left + tt.caretX - ttW - 12;
        if (left < 8) left = 8;
        if (top  < 8) top  = 8;
        if (top + ttH > vh - 8) top = vh - ttH - 8;
        tooltipEl.style.left = left + 'px';
        tooltipEl.style.top  = top  + 'px';
    }

    function renderM2Chart(irvs) {
        const block = document.getElementById('ra-chart-block');
        block.style.display = 'block';

        // Берём только ИРВ со Score — это основной показатель эффективности
        const valid = irvs.filter(r => r.extended && r.extended.score != null);
        if (valid.length === 0) { block.style.display = 'none'; return; }

        const colorMap = _buildReagentColorMap(valid);
        const reagents = Object.keys(colorMap);

        // Один dataset на реагент (для группировки в легенде)
        const datasets = reagents.map(reagent => ({
            label: reagent,
            data: valid
                .filter(r => r.reagent === reagent)
                .map(r => ({ x: r.event_time, y: r.extended.score, _irv: r })),
            backgroundColor: colorMap[reagent],
            borderColor: colorMap[reagent],
            borderWidth: 1,
            barThickness: 14,
            maxBarThickness: 20,
            hoverBackgroundColor: colorMap[reagent],
            hoverBorderColor: '#111827',
            hoverBorderWidth: 2,
        }));

        if (chartM2) chartM2.destroy();
        chartM2 = new Chart(document.getElementById('ra-chart-m2'), {
            type: 'bar',
            data: {datasets},
            options: {
                responsive: true,
                maintainAspectRatio: false,
                onHover: (e, els) => {
                    e.native.target.style.cursor = els.length ? 'pointer' : 'default';
                },
                onClick: (e, els) => {
                    if (!els.length) return;
                    const el = els[0];
                    const point = chartM2.data.datasets[el.datasetIndex].data[el.index];
                    const irv = point && point._irv;
                    if (irv && _irvWellId) window._raDetail(irv.event_time, _irvWellId);
                },
                scales: {
                    x: {
                        type: 'time',
                        time: {unit: 'day'},
                        title: {display: true, text: 'Дата вброса'},
                        offset: true,
                    },
                    y: {
                        beginAtZero: true,
                        suggestedMax: 100,
                        title: {display: true, text: 'Score (0–100)'},
                    },
                },
                plugins: {
                    legend: {position: 'top'},
                    tooltip: {
                        enabled: false,
                        external: _renderChartTooltip,
                    },
                    annotation: {
                        annotations: {
                            good: {type: 'line', yMin: 70, yMax: 70,
                                   borderColor: 'rgba(22,163,74,0.4)',
                                   borderWidth: 1, borderDash: [4,4],
                                   label: {display: true, content: '70 — хороший',
                                           position: 'end', font: {size: 10},
                                           color: '#166534',
                                           backgroundColor: 'rgba(255,255,255,0.7)'}},
                            mid:  {type: 'line', yMin: 45, yMax: 45,
                                   borderColor: 'rgba(202,138,4,0.4)',
                                   borderWidth: 1, borderDash: [4,4],
                                   label: {display: true, content: '45 — средний',
                                           position: 'end', font: {size: 10},
                                           color: '#854d0e',
                                           backgroundColor: 'rgba(255,255,255,0.7)'}},
                        },
                    },
                },
            },
        });
    }

    // Состояние сортировки таблицы ИРВ
    let _irvSortKey = 'event_time';
    let _irvSortDir = 'asc';
    let _irvCache = [];
    let _irvWellId = null;

    function _irvSortValue(r, key) {
        const m = r.metrics || {};
        switch (key) {
            case 'event_time': return new Date(r.event_time).getTime();
            case 'reagent': return r.reagent || '';
            case 'qty': return r.qty || 0;
            case 'duration_hours': return r.duration_hours || 0;
            case 'q_cumulative': return m.q_cumulative || 0;
            case 'q_per_unit': return m.q_per_unit || 0;
            case 'dp_gain': return m.dp_gain || 0;
            case 'effect_duration_hours': return m.effect_duration_hours || 0;
            case 'utilisation_pct': return m.utilisation_pct || 0;
            case 'score': return (r.extended && r.extended.score != null)
                ? r.extended.score : -1;
            default: return 0;
        }
    }

    function renderIRVTable(irvs, wellId) {
        _irvCache = irvs || [];
        _irvWellId = wellId;
        _renderIRVRows();
        // Привязка кликов на th
        document.querySelectorAll('#ra-irv-table th[data-sort]').forEach(th => {
            th.style.cursor = 'pointer';
            th.onclick = () => {
                const k = th.dataset.sort;
                if (_irvSortKey === k) {
                    _irvSortDir = (_irvSortDir === 'asc') ? 'desc' : 'asc';
                } else {
                    _irvSortKey = k;
                    _irvSortDir = (k === 'score' || k === 'q_cumulative' || k === 'dp_gain')
                        ? 'desc' : 'asc';
                }
                _renderIRVRows();
            };
        });
    }

    function _renderIRVRows() {
        const block = document.getElementById('ra-irv-block');
        const body  = document.getElementById('ra-irv-body');
        if (!body) return;
        body.innerHTML = '';

        const sorted = [..._irvCache].sort((a, b) => {
            const va = _irvSortValue(a, _irvSortKey);
            const vb = _irvSortValue(b, _irvSortKey);
            if (typeof va === 'string' && typeof vb === 'string') {
                return _irvSortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
            }
            return _irvSortDir === 'asc' ? (va - vb) : (vb - va);
        });

        // Метка стрелки активного ключа
        document.querySelectorAll('#ra-irv-table th[data-sort]').forEach(th => {
            const base = th.textContent.replace(/[ ▲▼]+$/, '');
            th.textContent = base + (th.dataset.sort === _irvSortKey
                ? (_irvSortDir === 'asc' ? ' ▲' : ' ▼') : '');
        });

        sorted.forEach(r => {
            const m = r.metrics || {};
            const isInvalid = m.invalid_reason != null;
            const ext = r.extended || {};
            const score = ext.score;
            // Цвет ячейки Score по категории
            let scoreCell = '<td>—</td>';
            if (score !== undefined && score !== null) {
                const c = ext.category;
                const bg = c === 'good' ? '#dcfce7'
                       : c === 'average' ? '#fef9c3' : '#fee2e2';
                const col = c === 'good' ? '#166534'
                       : c === 'average' ? '#854d0e' : '#991b1b';
                scoreCell = `<td style="background:${bg}; color:${col}; font-weight:600;"
                    title="${esc(ext.summary || '')}">${score.toFixed(0)}</td>`;
            }
            const tr = document.createElement('tr');
            if (isInvalid) tr.className = 'invalid';
            tr.innerHTML = `
                <td>${fmtDt(r.event_time)}</td>
                <td>${esc(r.reagent)}${r.merged_count > 1 ? ' <small>(' + r.merged_count + ' вбр.)</small>' : ''}</td>
                <td>${r.qty != null ? r.qty : 'н/д'}</td>
                <td>${r.duration_hours}</td>
                <td>${v(m.q_cumulative, 3)}</td>
                <td>${v(m.q_per_unit, 3)}</td>
                <td>${v(m.dp_gain, 2)}</td>
                <td>${v(m.effect_duration_hours, 1)}</td>
                <td>${v(m.utilisation_pct, 0)}</td>
                ${scoreCell}
                <td>${isInvalid
                    ? '<small title="' + esc(m.invalid_reason) + '">&#9888;</small>'
                    : '<button class="btn btn-outline" style="padding:2px 8px;font-size:11px;" onclick="window._raDetail(\'' + r.event_time + '\',' + _irvWellId + ')">&#128269;</button>'
                }</td>
            `;
            body.appendChild(tr);
        });
        if (block) block.style.display = 'block';
    }

    // ── Detail modal ──
    window._raDetail = async function(eventTime, wellId) {
        const mergeWindow = +document.getElementById('ra-merge-window').value;
        const grace = +document.getElementById('ra-grace').value;

        try {
            const url = `${API}/irv-detail?well_id=${wellId}&event_time=${encodeURIComponent(eventTime)}&merge_window_hours=${mergeWindow}&grace_period_min=${grace}`;
            const res = await fetch(url);
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();
            renderDetailModal(data);
        } catch(e) {
            console.error(e);
            alert('Ошибка загрузки деталей: ' + e.message);
        }
    };

    function renderDetailModal(data) {
        const modal = document.getElementById('ra-detail-modal');
        modal.classList.add('active');

        document.getElementById('ra-detail-title').textContent =
            `${data.reagent} | ${fmtDt(data.event_time)} | ${data.qty != null ? data.qty + ' л' : 'qty н/д'}`;

        // Metrics cards
        const metricsEl = document.getElementById('ra-detail-metrics');
        const m = data.metrics;
        metricsEl.innerHTML = `
            <div class="ra-card"><h3>Q_cum (M1)</h3><div class="value">${v(m.q_cumulative, 3)}</div><div class="sub">тыс. м\u00B3</div></div>
            <div class="ra-card"><h3>Q/шт (M2)</h3><div class="value">${v(m.q_per_unit, 3)}</div><div class="sub">тыс. м\u00B3/шт</div></div>
            <div class="ra-card"><h3>\u0394P прирост (M3)</h3><div class="value">${v(m.dp_gain, 2)}</div><div class="sub">кгс/см\u00B2</div></div>
            <div class="ra-card"><h3>Длит. эффекта (M4)</h3><div class="value">${v(m.effect_duration_hours, 1)}</div><div class="sub">часов</div></div>
            <div class="ra-card"><h3>Использование (M5)</h3><div class="value">${v(m.utilisation_pct, 0)}</div><div class="sub">%</div></div>
        `;

        // Charts
        if (data.chart_data) {
            renderDetailCharts(data.chart_data, data.segments || [], data.dp_smoothed || null);
        }
        renderSegmentsTable(data.segments || []);
        renderExtendedMetrics(data.extended_metrics || null);
    }

    // ── Расширенные метрики + Score ──
    function renderExtendedMetrics(ext) {
        const wrap = document.getElementById('ra-detail-ext-block');
        if (!wrap) return;
        if (!ext || ext.score === undefined) {
            wrap.innerHTML = '<div style="color:#9ca3af; font-size:12px;">Недостаточно данных для расчёта.</div>';
            return;
        }
        // Цвет Score
        const sc = ext.score, cat = ext.category;
        const scoreColor = cat === 'good' ? '#16a34a'
                       : cat === 'average' ? '#ca8a04' : '#dc2626';
        const scoreLabel = cat === 'good' ? 'Хороший'
                       : cat === 'average' ? 'Средний' : 'Слабый';

        // Цвет ячейки метрики по нормализованному значению (0..1, выше = лучше)
        const cellColor = (n) => {
            if (n === null || n === undefined) return '#f9fafb';
            if (n >= 0.66) return '#dcfce7';
            if (n >= 0.33) return '#fef9c3';
            return '#fee2e2';
        };
        const fmtH = (h) => (h === null || h === undefined) ? '—' : Number(h).toFixed(2) + ' ч';
        const fmtV = (v) => (v === null || v === undefined) ? '—' : Number(v).toFixed(3);
        const n = ext.normalized || {};

        const rows = [
          {label: 'Время реагирования (T_resp)', val: fmtH(ext.response_time_h), n: n.resp,
           hint: 'От вброса до начала роста ΔP. Меньше — быстрее реагент включается.'},
          {label: 'Пик ΔP (прирост к baseline)', val: fmtV(ext.peak_dp_gain) + ' кгс/см²', n: n.peak,
           hint: 'Максимальный прирост ΔP относительно baseline. Больше — сильнее эффект.'},
          {label: 'Скорость роста (V_rise)', val: fmtV(ext.rise_slope) + ' кгс/см²/ч', n: n.rise,
           hint: 'Slope первого rise-сегмента. Больше — резче подъём.'},
          {label: 'Длительность плато', val: fmtH(ext.plateau_total_h), n: n.plateau,
           hint: 'Сумма всех плато. Дольше — стабильнее работа реагента.'},
          {label: 'Скорость спада |V_decay|', val: fmtV(ext.decay_v_abs) + ' кгс/см²/ч', n: n.decay_v,
           hint: 'Средний |slope| decay-сегментов. Меньше — медленнее спад, лучше.'},
          {label: 'Длительность спада', val: fmtH(ext.decay_total_h), n: n.decay_t,
           hint: 'Суммарное время decay. Дольше — постепенный возврат, лучше.'},
          {label: 'Полное время эффекта', val: fmtH(ext.effect_total_h), n: null, hint: ''},
        ];
        let html =
          `<div style="display:flex; align-items:center; gap:14px; flex-wrap:wrap; margin-bottom:8px;">
            <div style="font-size:32px; font-weight:700; color:${scoreColor};">${sc.toFixed(0)}</div>
            <div>
              <div style="font-size:12px; color:#6b7280; text-transform:uppercase;">Score реагента</div>
              <div style="font-size:14px; font-weight:600; color:${scoreColor};">${scoreLabel}</div>
            </div>
            <div style="flex:1; min-width:200px; font-size:13px; color:#4b5563; font-style:italic;">
              ${ext.summary || ''}
            </div>
          </div>
          <table class="ra-table">
            <thead><tr><th>Метрика</th><th>Значение</th><th>Оценка</th></tr></thead>
            <tbody>`;
        for (const r of rows) {
            const bg = cellColor(r.n);
            const nstr = (r.n === null || r.n === undefined) ? '—'
                : (r.n * 100).toFixed(0) + ' / 100';
            html += `<tr title="${r.hint}">
              <td style="background:${bg};">${r.label}</td>
              <td style="background:${bg}; text-align:right;">${r.val}</td>
              <td style="background:${bg}; text-align:right;">${nstr}</td>
            </tr>`;
        }
        html += '</tbody></table>';
        wrap.innerHTML = html;
    }

    // Цветовая схема сегментов ИРВ
    const SEG_COLORS = {
        rise:    'rgba(34, 197, 94, 0.12)',    // зелёный — рост ΔP (положительно)
        plateau: 'rgba(234, 179,  8, 0.10)',   // жёлтый — плато (нет эффекта)
        decay:   'rgba(239,  68, 68, 0.12)',   // красный — спад
    };
    const SEG_LABELS = {
        rise: 'Рост', plateau: 'Плато', decay: 'Спад',
    };

    function _segmentAnnotations(segments, eventTime) {
        // event_time может прийти как ISO; t_start/end сегмента — в часах от вброса.
        // Чтобы корректно поставить xMin/xMax, считаем абсолютное время.
        if (!segments || !segments.length || !eventTime) return {};
        const evMs = new Date(eventTime).getTime();
        const out = {};
        segments.forEach((s, i) => {
            const x0 = evMs + s.start_hours * 3600 * 1000;
            const x1 = evMs + s.end_hours   * 3600 * 1000;
            out['seg' + i] = {
                type: 'box',
                xMin: x0, xMax: x1,
                backgroundColor: SEG_COLORS[s.type] || SEG_COLORS.plateau,
                borderColor: 'rgba(0,0,0,0.05)', borderWidth: 1,
                label: {
                    display: true,
                    content: `${SEG_LABELS[s.type] || s.type} ${s.duration_hours}ч`,
                    position: {x: 'center', y: 'start'},
                    yAdjust: 6,
                    font: {size: 10, weight: '600'},
                    color: '#374151',
                    backgroundColor: 'rgba(255,255,255,0.85)',
                    padding: 2,
                },
            };
        });
        return out;
    }

    function renderDetailCharts(cd, segments, dpSmoothed) {
        const ts = cd.timestamps;

        // Преобразуем dp_smoothed в формат [{x, y}] на отдельной оси Y
        const smoothedPoints = (dpSmoothed && dpSmoothed.timestamps && dpSmoothed.values)
            ? dpSmoothed.timestamps.map((t, i) => ({x: t, y: dpSmoothed.values[i]}))
            : [];

        // Pressure chart
        if (detailPressureChart) detailPressureChart.destroy();
        detailPressureChart = new Chart(document.getElementById('ra-detail-pressure'), {
            type: 'line',
            data: {
                labels: ts,
                datasets: [
                    {label: 'P_tube', data: cd.p_tube, borderColor: '#3b82f6', borderWidth: 1.5,
                     pointRadius: 0, fill: false, spanGaps: true, yAxisID: 'y'},
                    {label: 'P_line', data: cd.p_line, borderColor: '#ef4444', borderWidth: 1.5,
                     pointRadius: 0, fill: false, spanGaps: true, yAxisID: 'y'},
                    // Сглаженная ΔP — отдельная ось справа, чтобы не сжимать P_tube/P_line
                    {label: 'ΔP сглаж.', data: smoothedPoints, borderColor: '#a855f7',
                     borderWidth: 2, borderDash: [3, 3], pointRadius: 0, fill: false,
                     spanGaps: true, yAxisID: 'yDP', parsing: false},
                ],
            },
            options: chartOpts('Давление (кгс/см\u00B2)', cd, segments, true /*dpAxis*/),
        });

        // Flow chart
        if (cd.flow_rate && cd.flow_rate.some(v => v != null)) {
            if (detailFlowChart) detailFlowChart.destroy();
            detailFlowChart = new Chart(document.getElementById('ra-detail-flow'), {
                type: 'line',
                data: {
                    labels: ts,
                    datasets: [
                        {label: 'Q (тыс. м\u00B3/сут)', data: cd.flow_rate, borderColor: '#16a34a', borderWidth: 1.5, pointRadius: 0, fill: true, backgroundColor: '#16a34a22', spanGaps: true},
                    ],
                },
                options: chartOpts('Дебит', cd, segments),
            });
        }
    }

    function chartOpts(yLabel, cd, segments, withDpAxis) {
        const annotations = _segmentAnnotations(segments, cd.event_time);
        if (cd.event_time) {
            annotations.injLine = {
                type: 'line', xMin: cd.event_time, xMax: cd.event_time,
                borderColor: '#f59e0b', borderWidth: 2, borderDash: [4,4],
                label: {display: true, content: '⬇ Вброс', position: 'start',
                        font: {size: 11, weight: '700'},
                        color: '#fff', backgroundColor: '#f59e0b', padding: 3},
            };
        }
        const scales = {
            x: {
                type: 'time',
                time: {unit: 'hour', displayFormats: {hour: 'dd.MM HH:mm'}},
            },
            y: {title: {display: true, text: yLabel}},
        };
        if (withDpAxis) {
            scales.yDP = {
                position: 'right',
                title: {display: true, text: 'ΔP (кгс/см²)'},
                grid: {drawOnChartArea: false},
            };
        }
        return {
            responsive: true,
            maintainAspectRatio: false,
            scales: scales,
            plugins: {
                legend: {position: 'top'},
                annotation: {annotations: annotations},
            },
        };
    }

    function renderSegmentsTable(segments) {
        const wrap = document.getElementById('ra-detail-segments');
        const empty = document.getElementById('ra-detail-seg-empty');
        if (!wrap) return;
        if (!segments || !segments.length) {
            wrap.innerHTML = '';
            if (empty) empty.textContent = '— не определены (мало точек или не выявлены)';
            return;
        }
        if (empty) empty.textContent = '';
        const fmtH = (h) => {
            if (h === null || h === undefined) return '—';
            return Number(h).toFixed(1) + ' ч';
        };
        const fmtDP = (v) => v === null || v === undefined ? '—' : Number(v).toFixed(2);
        const fmtSlope = (v) => v === null || v === undefined ? '—'
            : (v >= 0 ? '+' : '') + Number(v).toFixed(3);
        let html = '<table class="ra-table"><thead><tr>'
          + '<th>Тип</th><th>От вброса</th><th>До</th><th>Длит.</th>'
          + '<th>ΔP начало</th><th>ΔP конец</th><th>Наклон</th>'
          + '</tr></thead><tbody>';
        for (const s of segments) {
            const cls = s.type;
            const tag = `<span style="display:inline-block; padding:2px 8px; border-radius:4px; ` +
                `background:${SEG_COLORS[cls] || SEG_COLORS.plateau}; ` +
                `color:#1f2937; font-weight:600; font-size:11px;">${SEG_LABELS[cls] || cls}</span>`;
            html += `<tr>
              <td>${tag}</td>
              <td>${fmtH(s.start_hours)}</td>
              <td>${fmtH(s.end_hours)}</td>
              <td>${fmtH(s.duration_hours)}</td>
              <td>${fmtDP(s.start_dp)}</td>
              <td>${fmtDP(s.end_dp)}</td>
              <td>${fmtSlope(s.slope)} кгс/см²/ч</td>
            </tr>`;
        }
        html += '</tbody></table>';
        wrap.innerHTML = html;
    }

    async function exportExcel() {
        const wellId = +document.getElementById('ra-well').value;
        const dateFrom = document.getElementById('ra-date-from').value;
        const dateTo = document.getElementById('ra-date-to').value;
        const mergeWindow = +document.getElementById('ra-merge-window').value;
        const grace = +document.getElementById('ra-grace').value;
        if (!wellId) return;

        try {
            const res = await fetch(API + '/export-excel', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    well_id: wellId,
                    period_start: dateFrom + 'T00:00:00',
                    period_end: dateTo + 'T23:59:59',
                    merge_window_hours: mergeWindow,
                    grace_period_min: grace,
                }),
            });
            if (!res.ok) throw new Error(await res.text());
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `reagent_analysis_well${wellId}.xlsx`;
            a.click();
            URL.revokeObjectURL(url);
        } catch(e) {
            alert('Ошибка экспорта: ' + e.message);
        }
    }

    function closeModal() {
        document.getElementById('ra-detail-modal').classList.remove('active');
    }

    // ── Helpers ──
    function showLoading(on) {
        document.getElementById('ra-loading').style.display = on ? 'block' : 'none';
    }

    function hideResults() {
        ['ra-cards','ra-scores-block','ra-chart-block','ra-irv-block'].forEach(id => {
            document.getElementById(id).style.display = 'none';
        });
        document.getElementById('ra-warnings').innerHTML = '';
    }

    function showWarning(msg) {
        const el = document.getElementById('ra-warnings');
        const div = document.createElement('div');
        div.className = 'ra-warning';
        div.textContent = msg;
        el.appendChild(div);
    }

    function v(val, decimals) {
        if (val == null) return '<span style="color:#ccc">н/д</span>';
        return Number(val).toFixed(decimals);
    }

    function esc(s) {
        if (!s) return '';
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    function fmtDt(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        const dd = String(d.getDate()).padStart(2,'0');
        const mm = String(d.getMonth()+1).padStart(2,'0');
        const hh = String(d.getHours()).padStart(2,'0');
        const mi = String(d.getMinutes()).padStart(2,'0');
        return `${dd}.${mm} ${hh}:${mi}`;
    }
})();
