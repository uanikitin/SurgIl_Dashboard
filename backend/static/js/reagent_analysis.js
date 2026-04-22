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

        // Scores table
        renderScoresTable(data.scores);

        // M2 chart
        renderM2Chart(data.irv_results);

        // IRV table
        renderIRVTable(data.irv_results, +document.getElementById('ra-well').value);
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

    function renderM2Chart(irvs) {
        const block = document.getElementById('ra-chart-block');
        block.style.display = 'block';

        const valid = irvs.filter(r => r.metrics.q_per_unit != null);
        if (valid.length === 0) { block.style.display = 'none'; return; }

        // Group by reagent
        const byReagent = {};
        valid.forEach(r => {
            if (!byReagent[r.reagent]) byReagent[r.reagent] = [];
            byReagent[r.reagent].push({x: r.event_time, y: r.metrics.q_per_unit});
        });

        const colors = ['#3b82f6','#ef4444','#16a34a','#f59e0b','#8b5cf6','#ec4899'];
        const datasets = Object.keys(byReagent).map((reagent, i) => ({
            label: reagent,
            data: byReagent[reagent],
            borderColor: colors[i % colors.length],
            backgroundColor: colors[i % colors.length] + '33',
            pointRadius: 5,
            pointHoverRadius: 7,
            showLine: true,
            tension: 0.3,
        }));

        if (chartM2) chartM2.destroy();
        chartM2 = new Chart(document.getElementById('ra-chart-m2'), {
            type: 'scatter',
            data: {datasets},
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {type: 'time', time: {unit: 'day'}, title: {display: true, text: 'Дата'}},
                    y: {title: {display: true, text: 'Q/шт (тыс. м\u00B3/шт)'}},
                },
                plugins: {legend: {position: 'top'}},
            },
        });
    }

    function renderIRVTable(irvs, wellId) {
        const block = document.getElementById('ra-irv-block');
        const body = document.getElementById('ra-irv-body');
        body.innerHTML = '';

        irvs.forEach(r => {
            const m = r.metrics;
            const isInvalid = m.invalid_reason != null;
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
                <td>${isInvalid
                    ? '<small title="' + esc(m.invalid_reason) + '">&#9888;</small>'
                    : '<button class="btn btn-outline" style="padding:2px 8px;font-size:11px;" onclick="window._raDetail(\'' + r.event_time + '\',' + wellId + ')">&#128269;</button>'
                }</td>
            `;
            body.appendChild(tr);
        });
        block.style.display = 'block';
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
            renderDetailCharts(data.chart_data);
        }
    }

    function renderDetailCharts(cd) {
        const ts = cd.timestamps;

        // Pressure chart
        if (detailPressureChart) detailPressureChart.destroy();
        detailPressureChart = new Chart(document.getElementById('ra-detail-pressure'), {
            type: 'line',
            data: {
                labels: ts,
                datasets: [
                    {label: 'P_tube', data: cd.p_tube, borderColor: '#3b82f6', borderWidth: 1.5, pointRadius: 0, fill: false},
                    {label: 'P_line', data: cd.p_line, borderColor: '#ef4444', borderWidth: 1.5, pointRadius: 0, fill: false},
                ],
            },
            options: chartOpts('Давление (кгс/см\u00B2)', cd),
        });

        // Flow chart
        if (cd.flow_rate && cd.flow_rate.some(v => v != null)) {
            if (detailFlowChart) detailFlowChart.destroy();
            detailFlowChart = new Chart(document.getElementById('ra-detail-flow'), {
                type: 'line',
                data: {
                    labels: ts,
                    datasets: [
                        {label: 'Q (тыс. м\u00B3/сут)', data: cd.flow_rate, borderColor: '#16a34a', borderWidth: 1.5, pointRadius: 0, fill: true, backgroundColor: '#16a34a22'},
                    ],
                },
                options: chartOpts('Дебит', cd),
            });
        }
    }

    function chartOpts(yLabel, cd) {
        return {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    type: 'time',
                    time: {unit: 'hour', displayFormats: {hour: 'dd.MM HH:mm'}},
                },
                y: {title: {display: true, text: yLabel}},
            },
            plugins: {
                legend: {position: 'top'},
                annotation: cd.event_time ? {
                    annotations: {
                        injLine: {
                            type: 'line', xMin: cd.event_time, xMax: cd.event_time,
                            borderColor: '#f59e0b', borderWidth: 2, borderDash: [4,4],
                            label: {display: true, content: 'Вброс', position: 'start'},
                        },
                    },
                } : undefined,
            },
        };
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
