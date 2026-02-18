/**
 * flow_analysis.js — Анализ дебита газа: сценарии, коррекции, график, сравнение.
 *
 * API: /api/flow-analysis/*
 */
(function () {
    'use strict';

    console.log('[flow_analysis] Script loaded');

    var API = '/api/flow-analysis';

    // ═══════ DOM refs ═══════
    var wellSelect      = document.getElementById('faWellSelect');
    var periodStart     = document.getElementById('faPeriodStart');
    var periodEnd       = document.getElementById('faPeriodEnd');
    var scenarioSelect  = document.getElementById('faScenarioSelect');
    var scenarioStatus  = document.getElementById('faScenarioStatus');
    var btnNew          = document.getElementById('faNewScenario');
    var btnSave         = document.getElementById('faSaveScenario');
    var btnDelete       = document.getElementById('faDeleteScenario');
    var btnCalc         = document.getElementById('faCalculate');
    var btnPreview      = document.getElementById('faPreview');
    var btnBaseline     = document.getElementById('faSetBaseline');
    var canvas          = document.getElementById('faChart');
    var loadingEl       = document.getElementById('faLoading');
    var noDataEl        = document.getElementById('faNoData');
    var summaryContent  = document.getElementById('faSummaryContent');
    var corrList        = document.getElementById('faCorrectionsList');
    var resultsBody     = document.getElementById('faResultsBody');
    var comparePanel    = document.getElementById('faComparePanel');
    var compareContent  = document.getElementById('faCompareContent');
    var compareGran     = document.getElementById('faCompareGranularity');
    var btnAddCorr      = document.getElementById('faAddCorrection');

    // Param inputs
    var inMult     = document.getElementById('faMult');
    var inC1       = document.getElementById('faC1');
    var inC2       = document.getElementById('faC2');
    var inC3       = document.getElementById('faC3');
    var inCrit     = document.getElementById('faCritRatio');
    var inChoke    = document.getElementById('faChoke');
    var inSmooth   = document.getElementById('faSmooth');

    // Modal refs
    var corrModal       = document.getElementById('faCorrModal');
    var corrType        = document.getElementById('faCorrType');
    var corrStart       = document.getElementById('faCorrStart');
    var corrEnd         = document.getElementById('faCorrEnd');
    var corrExtra       = document.getElementById('faCorrExtraFields');
    var corrReason      = document.getElementById('faCorrReason');
    var corrSave        = document.getElementById('faCorrSave');
    var corrCancel      = document.getElementById('faCorrCancel');
    var corrModalTitle  = document.getElementById('faCorrModalTitle');

    var newModal   = document.getElementById('faNewModal');
    var newName    = document.getElementById('faNewName');
    var newDesc    = document.getElementById('faNewDesc');
    var newSave    = document.getElementById('faNewSave');
    var newCancel  = document.getElementById('faNewCancel');

    if (!canvas) { console.warn('[flow_analysis] Canvas not found'); return; }

    // ═══════ State ═══════
    var chart = null;
    var currentScenario = null;  // full scenario object from API
    var baselineId = null;
    var lastCalcData = null;     // last calculate/preview response (for selection stats)
    var eventsData = [];          // events from backend
    var selectionMode = false;    // region selection mode active
    var selStart = null;          // selection start timestamp (ms)
    var selEnd = null;            // selection end timestamp (ms)

    var COLORS = {
        flowRate:    '#ff9800',
        flowFill:    'rgba(255,152,0,0.12)',
        cumulative:  '#1565c0',
        pTube:       '#e53935',
        pLine:       '#43a047',
        exclude:     'rgba(239,68,68,0.18)',
        interpolate: 'rgba(59,130,246,0.18)',
        manual:      'rgba(22,163,74,0.18)',
        clamp:       'rgba(245,158,11,0.18)',
    };

    // ═══════ Init ═══════
    loadWells();

    // Set default period: last 30 days
    var now = new Date();
    var ago = new Date(now.getTime() - 30 * 86400000);
    periodEnd.value   = now.toISOString().slice(0, 10);
    periodStart.value = ago.toISOString().slice(0, 10);

    // ═══════ Event bindings ═══════
    wellSelect.addEventListener('change', onWellChange);
    scenarioSelect.addEventListener('change', onScenarioChange);
    btnNew.addEventListener('click', openNewModal);
    btnSave.addEventListener('click', saveScenario);
    btnDelete.addEventListener('click', deleteScenario);
    btnCalc.addEventListener('click', function () { runCalculation(true); });
    btnPreview.addEventListener('click', function () { runCalculation(false); });
    btnBaseline.addEventListener('click', setBaseline);
    btnAddCorr.addEventListener('click', function () { openCorrModal(null); });
    corrSave.addEventListener('click', saveCorrection);
    corrCancel.addEventListener('click', function () { corrModal.classList.remove('show'); });
    newCancel.addEventListener('click', function () { newModal.classList.remove('show'); });
    newSave.addEventListener('click', createScenario);
    corrType.addEventListener('change', updateCorrExtraFields);
    compareGran.addEventListener('change', loadComparison);

    // Selection mode + zoom reset
    document.getElementById('faSelectMode').addEventListener('click', toggleSelectionMode);
    document.getElementById('faResetZoom').addEventListener('click', function () { if (chart) chart.resetZoom(); });
    document.getElementById('faSelClose').addEventListener('click', function () {
        clearSelectionAnnotations(); selStart = null; selEnd = null;
    });
    document.getElementById('faSelCorr').addEventListener('click', createCorrectionFromSelection);
    canvas.addEventListener('click', onChartClick);

    // Close modals on overlay click
    corrModal.addEventListener('click', function (e) { if (e.target === corrModal) corrModal.classList.remove('show'); });
    newModal.addEventListener('click', function (e) { if (e.target === newModal) newModal.classList.remove('show'); });


    // ═══════════════════════════════════════════
    //  API helpers
    // ═══════════════════════════════════════════

    async function apiFetch(url, opts) {
        var resp = await fetch(url, opts);
        if (!resp.ok) {
            var err;
            try { err = await resp.json(); } catch (e) { err = { detail: 'HTTP ' + resp.status }; }
            throw new Error(err.detail || 'Error');
        }
        return resp.json();
    }

    function apiPost(url, body)  { return apiFetch(url, { method: 'POST',   headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) }); }
    function apiPut(url, body)   { return apiFetch(url, { method: 'PUT',    headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) }); }
    function apiDelete(url)      { return apiFetch(url, { method: 'DELETE' }); }


    // ═══════════════════════════════════════════
    //  Wells
    // ═══════════════════════════════════════════

    async function loadWells() {
        try {
            var wells = await apiFetch('/api/flow-rate/wells?days=90');
            wellSelect.innerHTML = '<option value="">-- Скважина --</option>';
            wells.forEach(function (w) {
                var opt = document.createElement('option');
                opt.value = w.id;
                opt.textContent = w.number + (w.name ? ' — ' + w.name : '');
                wellSelect.appendChild(opt);
            });
        } catch (e) {
            console.error('[flow_analysis] loadWells error:', e);
        }
    }

    function onWellChange() {
        var wid = wellSelect.value;
        if (!wid) return;
        loadScenarios(parseInt(wid));
    }


    // ═══════════════════════════════════════════
    //  Scenarios
    // ═══════════════════════════════════════════

    async function loadScenarios(wellId) {
        try {
            var scenarios = await apiFetch(API + '/scenarios?well_id=' + wellId);
            scenarioSelect.innerHTML = '<option value="">-- Сценарий --</option>';
            baselineId = null;

            scenarios.forEach(function (s) {
                var opt = document.createElement('option');
                opt.value = s.id;
                var label = s.name + ' [' + s.status + ']';
                if (s.is_baseline) {
                    label += ' (базовый)';
                    baselineId = s.id;
                }
                opt.textContent = label;
                scenarioSelect.appendChild(opt);
            });

            // Если нет сценариев — предложить создать
            if (scenarios.length === 0) {
                noDataEl.style.display = '';
                noDataEl.innerHTML = 'Нет сценариев для этой скважины.<br>'
                    + '<button class="btn btn-primary" style="margin-top:10px;" '
                    + 'onclick="document.getElementById(\'faNewScenario\').click()">'
                    + '+ Создать сценарий</button>';
            }
        } catch (e) {
            console.error('[flow_analysis] loadScenarios error:', e);
        }
    }

    async function onScenarioChange() {
        var sid = scenarioSelect.value;
        if (!sid) {
            currentScenario = null;
            updateButtonStates();
            return;
        }
        try {
            currentScenario = await apiFetch(API + '/scenarios/' + sid);
            populateParams(currentScenario);
            renderCorrections(currentScenario.corrections || []);
            updateButtonStates();
            updateStatusBadge();

            // Load results if calculated
            if (currentScenario.status === 'calculated' || currentScenario.status === 'locked') {
                loadResults(currentScenario.id);
                // Load chart from meta summary
                if (currentScenario.meta && currentScenario.meta.summary) {
                    renderSummary(currentScenario.meta.summary);
                }
            }

            // Show comparison panel if baseline exists
            if (baselineId && baselineId !== currentScenario.id) {
                comparePanel.style.display = '';
                loadComparison();
            } else {
                comparePanel.style.display = 'none';
            }

        } catch (e) {
            console.error('[flow_analysis] loadScenario error:', e);
        }
    }

    function populateParams(s) {
        if (!s) return;
        inMult.value  = s.multiplier;
        inC1.value    = s.c1;
        inC2.value    = s.c2;
        inC3.value    = s.c3;
        inCrit.value  = s.critical_ratio;
        inChoke.value = s.choke_mm || '';
        inSmooth.checked = s.smooth_enabled;
        if (s.period_start) periodStart.value = s.period_start.slice(0, 10);
        if (s.period_end)   periodEnd.value   = s.period_end.slice(0, 10);
    }

    function readParams() {
        return {
            multiplier:      parseFloat(inMult.value) || 4.1,
            c1:              parseFloat(inC1.value) || 2.919,
            c2:              parseFloat(inC2.value) || 4.654,
            c3:              parseFloat(inC3.value) || 286.95,
            critical_ratio:  parseFloat(inCrit.value) || 0.5,
            choke_mm:        inChoke.value ? parseFloat(inChoke.value) : null,
            smooth_enabled:  inSmooth.checked,
            period_start:    periodStart.value + 'T00:00:00',
            period_end:      periodEnd.value + 'T23:59:59',
        };
    }

    function updateButtonStates() {
        var hasSc = !!currentScenario;
        var locked = hasSc && currentScenario.status === 'locked';
        btnSave.disabled    = !hasSc || locked;
        btnDelete.disabled  = !hasSc;
        btnCalc.disabled    = !hasSc || locked;
        btnPreview.disabled = !hasSc;
        btnBaseline.disabled = !hasSc;
        // Tooltips для disabled-кнопок
        var hint = !hasSc ? 'Сначала создайте или выберите сценарий' : '';
        btnCalc.title    = locked ? 'Сценарий заблокирован' : hint;
        btnPreview.title = hint;
        btnSave.title    = locked ? 'Сценарий заблокирован' : hint;
    }

    function updateStatusBadge() {
        if (!currentScenario) { scenarioStatus.innerHTML = ''; return; }
        var s = currentScenario.status;
        var cls = 'badge-' + s;
        var html = '<span class="' + cls + '">' + s + '</span>';
        if (currentScenario.is_baseline) html += ' <span class="badge-baseline">baseline</span>';
        scenarioStatus.innerHTML = html;
    }


    // ═══════════════════════════════════════════
    //  New / Save / Delete scenario
    // ═══════════════════════════════════════════

    function openNewModal() {
        if (!wellSelect.value) { alert('Сначала выберите скважину'); return; }
        newName.value = '';
        newDesc.value = '';
        newModal.classList.add('show');
    }

    async function createScenario() {
        var name = newName.value.trim();
        if (!name) { alert('Введите название'); return; }

        var params = readParams();
        params.well_id = parseInt(wellSelect.value);
        params.name = name;
        params.description = newDesc.value.trim() || null;

        try {
            var s = await apiPost(API + '/scenarios', params);
            newModal.classList.remove('show');
            await loadScenarios(s.well_id);
            scenarioSelect.value = s.id;
            await onScenarioChange();
        } catch (e) {
            alert('Ошибка создания: ' + e.message);
        }
    }

    async function saveScenario() {
        if (!currentScenario) return;
        var params = readParams();
        try {
            await apiPut(API + '/scenarios/' + currentScenario.id, params);
            await onScenarioChange();
        } catch (e) {
            alert('Ошибка сохранения: ' + e.message);
        }
    }

    async function deleteScenario() {
        if (!currentScenario) return;
        if (!confirm('Удалить сценарий "' + currentScenario.name + '"?')) return;
        try {
            await apiDelete(API + '/scenarios/' + currentScenario.id);
            currentScenario = null;
            await loadScenarios(parseInt(wellSelect.value));
            scenarioSelect.value = '';
            updateButtonStates();
            summaryContent.innerHTML = '<div style="color:#999;text-align:center;padding:20px;">Нет данных</div>';
            resultsBody.innerHTML = '';
        } catch (e) {
            alert('Ошибка удаления: ' + e.message);
        }
    }

    async function setBaseline() {
        if (!currentScenario) return;
        try {
            await apiPost(API + '/scenarios/' + currentScenario.id + '/set-baseline', {});
            baselineId = currentScenario.id;
            await loadScenarios(currentScenario.well_id);
            scenarioSelect.value = currentScenario.id;
            await onScenarioChange();
        } catch (e) {
            alert('Ошибка: ' + e.message);
        }
    }


    // ═══════════════════════════════════════════
    //  Calculation
    // ═══════════════════════════════════════════

    async function runCalculation(save) {
        if (!currentScenario) return;

        showLoading();
        noDataEl.style.display = 'none';
        canvas.style.display = 'none';

        var endpoint = save
            ? API + '/scenarios/' + currentScenario.id + '/calculate'
            : API + '/scenarios/' + currentScenario.id + '/preview';

        try {
            var method = save ? 'POST' : 'GET';
            var resp = await fetch(endpoint, { method: method });
            if (!resp.ok) {
                var err;
                try { err = await resp.json(); } catch (e) { err = {}; }
                throw new Error(err.detail || 'HTTP ' + resp.status);
            }
            var data = await resp.json();
            lastCalcData = data;

            renderChart(data.chart, data.correction_zones || []);
            renderSummary(data.summary);
            renderDailyResults(data.daily_results || []);

            // Fetch and display events on chart
            try {
                await fetchEvents(
                    parseInt(wellSelect.value),
                    periodStart.value + 'T00:00:00',
                    periodEnd.value + 'T23:59:59'
                );
                addEventsToChart();
            } catch (evErr) {
                console.warn('[flow_analysis] fetchEvents error:', evErr);
            }

            if (save) {
                await onScenarioChange(); // refresh status
            }

            hideLoading();
        } catch (e) {
            hideLoading();
            noDataEl.style.display = '';
            noDataEl.textContent = 'Ошибка: ' + e.message;
            console.error('[flow_analysis] calculation error:', e);
        }
    }


    // ═══════════════════════════════════════════
    //  Chart
    // ═══════════════════════════════════════════

    function renderChart(chartData, corrZones) {
        if (!chartData || !chartData.timestamps || !chartData.timestamps.length) {
            noDataEl.style.display = '';
            noDataEl.textContent = 'Нет данных для отображения';
            canvas.style.display = 'none';
            return;
        }

        noDataEl.style.display = 'none';
        canvas.style.display = '';

        // Reset selection on re-render
        selStart = null;
        selEnd = null;
        var statsPanel = document.getElementById('faSelectionStats');
        if (statsPanel) statsPanel.style.display = 'none';

        if (chart) chart.destroy();

        var labels = chartData.timestamps;

        // Correction zone annotations
        var annotations = {};
        (corrZones || []).forEach(function (z, i) {
            var colorMap = {
                exclude:      COLORS.exclude,
                interpolate:  COLORS.interpolate,
                manual_value: COLORS.manual,
                clamp:        COLORS.clamp,
            };
            annotations['corr_' + i] = {
                type: 'box',
                xMin: z.start,
                xMax: z.end,
                backgroundColor: colorMap[z.type] || 'rgba(150,150,150,0.1)',
                borderWidth: 0,
                label: {
                    display: true,
                    content: z.type,
                    position: 'start',
                    font: { size: 9 },
                    color: '#999',
                },
            };
        });

        chart = new Chart(canvas, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Дебит, тыс.м3/сут',
                        data: chartData.flow_rate,
                        borderColor: COLORS.flowRate,
                        backgroundColor: COLORS.flowFill,
                        fill: true,
                        borderWidth: 1.5,
                        pointRadius: 0,
                        yAxisID: 'y',
                        tension: 0.1,
                    },
                    {
                        label: 'Накоп., тыс.м3',
                        data: chartData.cumulative_flow,
                        borderColor: COLORS.cumulative,
                        borderWidth: 1,
                        pointRadius: 0,
                        borderDash: [4, 2],
                        yAxisID: 'y1',
                        tension: 0.1,
                    },
                    {
                        label: 'P трубн.',
                        data: chartData.p_tube,
                        borderColor: COLORS.pTube,
                        borderWidth: 1,
                        pointRadius: 0,
                        yAxisID: 'y2',
                        hidden: true,
                    },
                    {
                        label: 'P сбор.',
                        data: chartData.p_line,
                        borderColor: COLORS.pLine,
                        borderWidth: 1,
                        pointRadius: 0,
                        yAxisID: 'y2',
                        hidden: true,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                scales: {
                    x: {
                        type: 'time',
                        time: { unit: 'day', tooltipFormat: 'yyyy-MM-dd HH:mm' },
                        grid: { color: '#f0f0f0' },
                        ticks: { maxTicksLimit: 15, font: { size: 10 } },
                    },
                    y: {
                        position: 'left',
                        title: { display: true, text: 'Q, тыс.м3/сут', font: { size: 11 } },
                        grid: { color: '#f0f0f0' },
                        beginAtZero: true,
                    },
                    y1: {
                        position: 'right',
                        title: { display: true, text: 'Qнак, тыс.м3', font: { size: 11 } },
                        grid: { drawOnChartArea: false },
                    },
                    y2: {
                        position: 'right',
                        display: false,
                        title: { display: false, text: 'P, кгс/см2' },
                        grid: { drawOnChartArea: false },
                    },
                },
                plugins: {
                    legend: { position: 'top', labels: { usePointStyle: true, font: { size: 11 } } },
                    annotation: { annotations: annotations },
                    zoom: {
                        pan: { enabled: true, mode: 'x' },
                        zoom: {
                            drag: { enabled: true, backgroundColor: 'rgba(59,130,246,0.12)' },
                            mode: 'x',
                        },
                    },
                },
            },
        });
    }


    // ═══════════════════════════════════════════
    //  Summary
    // ═══════════════════════════════════════════

    function renderSummary(s) {
        if (!s || s.error) {
            summaryContent.innerHTML = '<div style="color:#999;text-align:center;padding:20px;">' + (s && s.error || 'Нет данных') + '</div>';
            return;
        }

        var html = '';
        html += statRow('Медианный дебит', fmt(s.median_flow_rate) + ' тыс.м3/сут', true);
        html += statRow('Средний дебит',   fmt(s.mean_flow_rate));
        html += statRow('Эффективный',     fmt(s.effective_flow_rate));
        html += statRow('Накоп. добыча',   fmt(s.cumulative_flow) + ' тыс.м3');
        html += '<div style="border-top:1px solid #e5e7eb; margin:4px 0;"></div>';
        html += statRow('Наблюдение',      fmt(s.observation_days) + ' сут');
        html += statRow('Штуцер',          s.choke_mm + ' мм');
        html += statRow('P труб. (мед.)',  fmt(s.median_p_tube) + ' кгс/см2');
        html += statRow('P сбор. (мед.)',  fmt(s.median_p_line) + ' кгс/см2');
        html += statRow('dP (мед.)',       fmt(s.median_dp));
        html += '<div style="border-top:1px solid #e5e7eb; margin:4px 0;"></div>';
        html += statRow('Простои',         fmt(s.downtime_total_hours) + ' ч');
        html += statRow('Потери стравл.',  fmt(s.purge_loss_total) + ' тыс.м3');
        html += statRow('Потери/сут',      fmt(s.total_loss_daily));
        html += statRow('Коэфф. потерь',   fmt(s.loss_coefficient_pct) + '%');
        html += statRow('Продувки',        s.purge_venting_count + ' шт');

        summaryContent.innerHTML = html;
    }

    function statRow(label, value, highlight) {
        var cls = highlight ? 'fa-stat-row fa-stat-highlight' : 'fa-stat-row';
        return '<div class="' + cls + '"><span class="fa-stat-label">' + label + '</span><span class="fa-stat-value">' + value + '</span></div>';
    }

    function fmt(v) {
        if (v === null || v === undefined) return '—';
        return typeof v === 'number' ? v.toFixed(3) : String(v);
    }


    // ═══════════════════════════════════════════
    //  Daily results
    // ═══════════════════════════════════════════

    async function loadResults(scenarioId) {
        try {
            var results = await apiFetch(API + '/scenarios/' + scenarioId + '/results');
            renderDailyResults(results);
        } catch (e) {
            console.error('[flow_analysis] loadResults error:', e);
        }
    }

    function renderDailyResults(results) {
        if (!results || !results.length) {
            resultsBody.innerHTML = '<tr><td colspan="5" style="color:#999;text-align:center;padding:10px;">Нет данных</td></tr>';
            return;
        }
        var html = '';
        results.forEach(function (r) {
            html += '<tr>'
                + '<td>' + r.result_date + '</td>'
                + '<td>' + fmt(r.avg_flow_rate) + '</td>'
                + '<td>' + fmt(r.cumulative_flow) + '</td>'
                + '<td>' + fmt(r.avg_p_tube) + '</td>'
                + '<td>' + fmt(r.avg_p_line) + '</td>'
                + '</tr>';
        });
        resultsBody.innerHTML = html;
    }


    // ═══════════════════════════════════════════
    //  Corrections
    // ═══════════════════════════════════════════

    var editingCorrId = null;

    function renderCorrections(corrections) {
        if (!corrections || !corrections.length) {
            corrList.innerHTML = '<div style="color:#999;text-align:center;padding:8px;">Нет коррекций</div>';
            return;
        }
        var html = '';
        corrections.forEach(function (c) {
            var start = c.dt_start ? c.dt_start.slice(5, 16) : '';
            var end   = c.dt_end   ? c.dt_end.slice(5, 16) : '';
            html += '<div class="fa-corr-item" data-type="' + c.correction_type + '" data-id="' + c.id + '">'
                + '<span class="fa-corr-type">' + c.correction_type + '</span>'
                + '<span class="fa-corr-range">' + start + ' — ' + end + '</span>'
                + '<span class="fa-corr-del" data-corr-id="' + c.id + '">&times;</span>'
                + '</div>';
        });
        corrList.innerHTML = html;

        // Bind delete
        corrList.querySelectorAll('.fa-corr-del').forEach(function (el) {
            el.addEventListener('click', function (e) {
                e.stopPropagation();
                deleteCorrection(parseInt(this.dataset.corrId));
            });
        });
    }

    function openCorrModal(corrObj) {
        if (!currentScenario) { alert('Сначала выберите сценарий'); return; }
        editingCorrId = corrObj ? corrObj.id : null;
        corrModalTitle.textContent = corrObj ? 'Редактировать коррекцию' : 'Добавить коррекцию';
        corrType.value   = corrObj ? corrObj.correction_type : 'exclude';
        corrStart.value  = corrObj ? corrObj.dt_start.slice(0, 16) : '';
        corrEnd.value    = corrObj ? corrObj.dt_end.slice(0, 16) : '';
        corrReason.value = corrObj ? (corrObj.reason || '') : '';
        updateCorrExtraFields();
        corrModal.classList.add('show');
    }

    function updateCorrExtraFields() {
        var type = corrType.value;
        var html = '';
        if (type === 'manual_value') {
            html += '<div class="fa-field"><label>P трубн. (кгс/см2)</label><input type="number" id="faCorrPTube" step="0.1"></div>';
            html += '<div class="fa-field"><label>P сбор. (кгс/см2)</label><input type="number" id="faCorrPLine" step="0.1"></div>';
        } else if (type === 'clamp') {
            html += '<div class="fa-field"><label>Мин.</label><input type="number" id="faCorrClampMin" step="0.1"></div>';
            html += '<div class="fa-field"><label>Макс.</label><input type="number" id="faCorrClampMax" step="0.1"></div>';
        } else if (type === 'interpolate') {
            html += '<div class="fa-field"><label>Метод</label><select id="faCorrInterp"><option value="linear">linear</option><option value="nearest">nearest</option></select></div>';
        }
        corrExtra.innerHTML = html;
    }

    async function saveCorrection() {
        if (!currentScenario) return;
        var body = {
            correction_type: corrType.value,
            dt_start: corrStart.value + ':00',
            dt_end:   corrEnd.value + ':00',
            reason:   corrReason.value || null,
            sort_order: 0,
        };

        if (corrType.value === 'manual_value') {
            var ptEl = document.getElementById('faCorrPTube');
            var plEl = document.getElementById('faCorrPLine');
            body.manual_p_tube = ptEl && ptEl.value ? parseFloat(ptEl.value) : null;
            body.manual_p_line = plEl && plEl.value ? parseFloat(plEl.value) : null;
        } else if (corrType.value === 'clamp') {
            var minEl = document.getElementById('faCorrClampMin');
            var maxEl = document.getElementById('faCorrClampMax');
            body.clamp_min = minEl && minEl.value ? parseFloat(minEl.value) : null;
            body.clamp_max = maxEl && maxEl.value ? parseFloat(maxEl.value) : null;
        } else if (corrType.value === 'interpolate') {
            var intEl = document.getElementById('faCorrInterp');
            body.interp_method = intEl ? intEl.value : 'linear';
        }

        try {
            if (editingCorrId) {
                await apiPut(API + '/corrections/' + editingCorrId, body);
            } else {
                await apiPost(API + '/scenarios/' + currentScenario.id + '/corrections', body);
            }
            corrModal.classList.remove('show');
            await onScenarioChange();
        } catch (e) {
            alert('Ошибка: ' + e.message);
        }
    }

    async function deleteCorrection(corrId) {
        if (!confirm('Удалить коррекцию?')) return;
        try {
            await apiDelete(API + '/corrections/' + corrId);
            await onScenarioChange();
        } catch (e) {
            alert('Ошибка: ' + e.message);
        }
    }


    // ═══════════════════════════════════════════
    //  Comparison
    // ═══════════════════════════════════════════

    async function loadComparison() {
        if (!currentScenario || !baselineId || baselineId === currentScenario.id) return;
        var gran = compareGran.value;
        try {
            var data = await apiFetch(
                API + '/compare?scenario_id=' + currentScenario.id
                + '&baseline_id=' + baselineId
                + '&granularity=' + gran
            );
            renderComparison(data);
        } catch (e) {
            compareContent.innerHTML = '<div style="color:#999;">Ошибка: ' + e.message + '</div>';
        }
    }

    function renderComparison(data) {
        if (!data || !data.rows || !data.rows.length) {
            compareContent.innerHTML = '<div style="color:#999;text-align:center;">Нет данных для сравнения</div>';
            return;
        }

        var html = '';
        // Totals
        if (data.totals) {
            var t = data.totals;
            html += '<div style="margin-bottom:8px;">';
            html += statRow('Текущий итого', fmt(t.current_total_flow) + ' тыс.м3');
            html += statRow('Базовый итого', fmt(t.baseline_total_flow) + ' тыс.м3');
            if (t.delta_total_pct !== undefined) {
                var cls = t.delta_total_pct >= 0 ? 'fa-delta-pos' : 'fa-delta-neg';
                html += '<div class="fa-stat-row"><span class="fa-stat-label">Разница</span><span class="fa-stat-value ' + cls + '">' + (t.delta_total_pct >= 0 ? '+' : '') + t.delta_total_pct + '%</span></div>';
            }
            html += '</div><div style="border-top:1px solid #e5e7eb; margin:6px 0;"></div>';
        }

        // Rows table
        html += '<div style="max-height:200px;overflow-y:auto;">';
        html += '<table class="fa-results-table"><thead><tr><th>Период</th><th>Тек.</th><th>Баз.</th><th>Delta%</th></tr></thead><tbody>';
        data.rows.forEach(function (r) {
            var dcls = '';
            var dval = '—';
            if (r.delta_flow_pct !== null && r.delta_flow_pct !== undefined) {
                dcls = r.delta_flow_pct >= 0 ? 'fa-delta-pos' : 'fa-delta-neg';
                dval = (r.delta_flow_pct >= 0 ? '+' : '') + r.delta_flow_pct + '%';
            }
            html += '<tr>'
                + '<td>' + r.period + '</td>'
                + '<td>' + (r.current_avg_flow !== null ? r.current_avg_flow.toFixed(2) : '—') + '</td>'
                + '<td>' + (r.baseline_avg_flow !== null ? r.baseline_avg_flow.toFixed(2) : '—') + '</td>'
                + '<td class="' + dcls + '">' + dval + '</td>'
                + '</tr>';
        });
        html += '</tbody></table></div>';

        compareContent.innerHTML = html;
    }


    // ═══════════════════════════════════════════
    //  Events on chart
    // ═══════════════════════════════════════════

    async function fetchEvents(wellId, start, end) {
        var url = API + '/events?well_id=' + wellId + '&start=' + encodeURIComponent(start) + '&end=' + encodeURIComponent(end);
        eventsData = await apiFetch(url);
    }

    function addEventsToChart() {
        if (!chart || !eventsData.length) return;
        var ann = chart.options.plugins.annotation.annotations;
        var colorMap = {
            purge: '#ef4444', reagent: '#8b5cf6', equip: '#f59e0b',
            pressure: '#6b7280', note: '#3b82f6', other: '#9ca3af'
        };
        eventsData.forEach(function (ev, i) {
            ann['evt_' + i] = {
                type: 'line',
                xMin: ev.event_time,
                xMax: ev.event_time,
                borderColor: colorMap[ev.event_type] || '#9ca3af',
                borderWidth: 1,
                borderDash: [4, 3],
                label: {
                    display: true,
                    content: ev.event_type + (ev.description ? ': ' + ev.description.slice(0, 30) : ''),
                    position: 'start',
                    rotation: -90,
                    font: { size: 9 },
                    color: '#666',
                    backgroundColor: 'rgba(255,255,255,0.8)',
                },
            };
        });
        chart.update('none');
    }


    // ═══════════════════════════════════════════
    //  Selection mode
    // ═══════════════════════════════════════════

    function toggleSelectionMode() {
        selectionMode = !selectionMode;
        var btn = document.getElementById('faSelectMode');
        btn.classList.toggle('fa-select-btn-active', selectionMode);
        selStart = null;
        selEnd = null;
        clearSelectionAnnotations();

        if (chart) {
            chart.options.plugins.zoom.zoom.drag.enabled = !selectionMode;
            chart.options.plugins.zoom.pan.enabled = !selectionMode;
            chart.update('none');
        }
        canvas.style.cursor = selectionMode ? 'crosshair' : '';
    }

    function onChartClick(e) {
        if (!selectionMode || !chart) return;

        var rect = canvas.getBoundingClientRect();
        var x = e.clientX - rect.left;
        var ts = chart.scales.x.getValueForPixel(x);
        if (!ts) return;

        if (!selStart) {
            // First click — mark start
            selStart = ts;
            addSelectionMarker('selMarkerStart', ts, '#3b82f6');
        } else if (!selEnd) {
            // Second click — mark end, show region + stats
            selEnd = ts;
            if (selEnd < selStart) { var tmp = selStart; selStart = selEnd; selEnd = tmp; }
            addSelectionMarker('selMarkerEnd', selEnd, '#3b82f6');
            addSelectionRegion(selStart, selEnd);
            calculateSelectionStats(selStart, selEnd);
        } else {
            // Third click — reset, start new selection
            clearSelectionAnnotations();
            selStart = ts;
            selEnd = null;
            addSelectionMarker('selMarkerStart', ts, '#3b82f6');
        }
    }

    function addSelectionMarker(id, ts, color) {
        chart.options.plugins.annotation.annotations[id] = {
            type: 'line',
            xMin: ts, xMax: ts,
            borderColor: color,
            borderWidth: 2,
            borderDash: [6, 3],
        };
        chart.update('none');
    }

    function addSelectionRegion(start, end) {
        chart.options.plugins.annotation.annotations['selRegion'] = {
            type: 'box',
            xMin: start, xMax: end,
            backgroundColor: 'rgba(59,130,246,0.08)',
            borderColor: '#3b82f6',
            borderWidth: 1,
        };
        chart.update('none');
    }

    function clearSelectionAnnotations() {
        if (!chart) return;
        var ann = chart.options.plugins.annotation.annotations;
        delete ann['selMarkerStart'];
        delete ann['selMarkerEnd'];
        delete ann['selRegion'];
        chart.update('none');
        document.getElementById('faSelectionStats').style.display = 'none';
    }


    // ═══════════════════════════════════════════
    //  Selection statistics
    // ═══════════════════════════════════════════

    function calculateSelectionStats(startTs, endTs) {
        if (!lastCalcData || !lastCalcData.chart) return;

        var cd = lastCalcData.chart;
        var indices = [];
        cd.timestamps.forEach(function (t, i) {
            var ms = new Date(t).getTime();
            if (ms >= startTs && ms <= endTs) indices.push(i);
        });
        if (!indices.length) {
            document.getElementById('faSelContent').innerHTML = '<div style="color:#999;padding:8px;">Нет данных в выбранном диапазоне</div>';
            document.getElementById('faSelectionStats').style.display = '';
            return;
        }

        var flows  = indices.map(function (i) { return cd.flow_rate[i]; }).filter(function (v) { return v != null; });
        var pTubes = indices.map(function (i) { return cd.p_tube[i]; }).filter(function (v) { return v != null; });
        var pLines = indices.map(function (i) { return cd.p_line[i]; }).filter(function (v) { return v != null; });

        var durationH = ((endTs - startTs) / 3600000).toFixed(1);

        // Events in range
        var eventsInRange = eventsData.filter(function (ev) {
            var t = new Date(ev.event_time).getTime();
            return t >= startTs && t <= endTs;
        });

        // Downtime overlap
        var downtimeMin = 0;
        (lastCalcData.downtime_periods || []).forEach(function (d) {
            var ds = new Date(d.start).getTime(), de = new Date(d.end).getTime();
            var os = Math.max(ds, startTs), oe = Math.min(de, endTs);
            if (oe > os) downtimeMin += (oe - os) / 60000;
        });

        // Build HTML
        var html = '';
        html += '<div class="fa-sel-grid">';
        html += selStatRow('Период', fmtDt(cd.timestamps[indices[0]]) + ' — ' + fmtDt(cd.timestamps[indices[indices.length - 1]]));
        html += selStatRow('Длительность', durationH + ' ч (' + indices.length + ' точек)');
        html += '</div>';

        if (flows.length) {
            var slope = linearSlope(flows);
            var slopeDir = slope > 0.0001 ? 'рост' : (slope < -0.0001 ? 'падение' : 'стаб.');
            var slopeColor = slope > 0.0001 ? '#16a34a' : (slope < -0.0001 ? '#dc2626' : '#6b7280');
            html += '<div class="fa-sel-section"><div class="fa-sel-section-title">Дебит, тыс.м3/сут</div>';
            html += '<div class="fa-sel-grid">';
            html += selStatRow('Min', fmt(Math.min.apply(null, flows)));
            html += selStatRow('Max', fmt(Math.max.apply(null, flows)));
            html += selStatRow('Среднее', fmt(flows.reduce(function (a, b) { return a + b; }, 0) / flows.length));
            html += selStatRow('Медиана', fmt(arrMedian(flows)));
            html += selStatRow('Тренд', '<span style="color:' + slopeColor + ';">' + slopeDir + ' (' + (slope * 1440).toFixed(4) + '/сут)</span>');
            html += '</div></div>';
        }

        if (pTubes.length) {
            html += '<div class="fa-sel-section"><div class="fa-sel-section-title">P трубн., кгс/см2</div>';
            html += '<div class="fa-sel-grid">';
            html += selStatRow('Min', fmt(Math.min.apply(null, pTubes)));
            html += selStatRow('Max', fmt(Math.max.apply(null, pTubes)));
            html += selStatRow('Среднее', fmt(pTubes.reduce(function (a, b) { return a + b; }, 0) / pTubes.length));
            html += '</div></div>';
        }

        if (pLines.length) {
            html += '<div class="fa-sel-section"><div class="fa-sel-section-title">P сбор., кгс/см2</div>';
            html += '<div class="fa-sel-grid">';
            html += selStatRow('Min', fmt(Math.min.apply(null, pLines)));
            html += selStatRow('Max', fmt(Math.max.apply(null, pLines)));
            html += selStatRow('Среднее', fmt(pLines.reduce(function (a, b) { return a + b; }, 0) / pLines.length));
            html += '</div></div>';
        }

        // Events summary
        html += '<div class="fa-sel-section"><div class="fa-sel-section-title">События и простои</div>';
        html += '<div class="fa-sel-grid">';
        html += selStatRow('Событий', eventsInRange.length);
        if (eventsInRange.length) {
            var typeCounts = {};
            eventsInRange.forEach(function (ev) {
                typeCounts[ev.event_type] = (typeCounts[ev.event_type] || 0) + 1;
            });
            var typeParts = [];
            for (var tp in typeCounts) typeParts.push(tp + ': ' + typeCounts[tp]);
            html += selStatRow('По типам', typeParts.join(', '));
        }
        html += selStatRow('Простои', downtimeMin > 0 ? downtimeMin.toFixed(0) + ' мин' : '0');
        html += '</div></div>';

        document.getElementById('faSelContent').innerHTML = html;
        document.getElementById('faSelectionStats').style.display = '';
    }

    function selStatRow(label, value) {
        return '<div class="fa-stat-row"><span class="fa-stat-label">' + label + '</span><span class="fa-stat-value">' + value + '</span></div>';
    }

    function fmtDt(iso) {
        if (!iso) return '—';
        return iso.replace('T', ' ').slice(0, 16);
    }

    function arrMedian(arr) {
        var s = arr.slice().sort(function (a, b) { return a - b; });
        var mid = Math.floor(s.length / 2);
        return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
    }

    function linearSlope(arr) {
        var n = arr.length;
        if (n < 2) return 0;
        var sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0;
        for (var i = 0; i < n; i++) {
            sumX += i; sumY += arr[i]; sumXY += i * arr[i]; sumX2 += i * i;
        }
        var denom = n * sumX2 - sumX * sumX;
        return denom === 0 ? 0 : (n * sumXY - sumX * sumY) / denom;
    }

    function createCorrectionFromSelection() {
        if (!selStart || !selEnd) return;
        var dtStart = new Date(selStart).toISOString().slice(0, 16);
        var dtEnd = new Date(selEnd).toISOString().slice(0, 16);
        openCorrModal(null);
        // Pre-fill dates after modal opens
        corrStart.value = dtStart;
        corrEnd.value = dtEnd;
    }


    // ═══════════════════════════════════════════
    //  Loading
    // ═══════════════════════════════════════════

    function showLoading() { loadingEl.classList.add('show'); }
    function hideLoading() { loadingEl.classList.remove('show'); }

})();
