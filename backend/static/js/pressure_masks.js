/**
 * pressure_masks.js — Chart.js graph with mask annotations,
 * drag-select for manual mask creation, CRUD operations.
 */
(function () {
  "use strict";

  // ─── Constants ───
  const API_MASKS = "/api/pressure-masks";
  const API_CHART = "/api/pressure/chart";

  const PROBLEM_COLORS = {
    sensor_fault:          { bg: "rgba(239,68,68,0.18)",   border: "#ef4444",  label: "Неисправность датчика" },
    comm_loss:             { bg: "rgba(249,115,22,0.18)",  border: "#f97316",  label: "Потеря связи" },
    pipeline_maintenance:  { bg: "rgba(59,130,246,0.18)",  border: "#3b82f6",  label: "Профилактика трубопровода" },
    gsp_switch:            { bg: "rgba(99,102,241,0.18)",  border: "#6366f1",  label: "Переключение ГСП" },
    well_shutdown:         { bg: "rgba(107,114,128,0.18)", border: "#6b7280",  label: "Скважина не работала" },
    hydrate:               { bg: "rgba(168,85,247,0.18)",  border: "#a855f7",  label: "Гидраты" },
    purge:                 { bg: "rgba(20,184,166,0.18)",  border: "#14b8a6",  label: "Продувка" },
    degradation:           { bg: "rgba(234,179,8,0.18)",   border: "#eab308",  label: "Деградация датчика" },
    manual:                { bg: "rgba(156,163,175,0.18)", border: "#9ca3af",  label: "Ручная коррекция" },
  };

  const METHOD_LABELS = {
    interpolate_noise: "Интерполяция + шум",
    interpolate:       "Линейная интерполяция",
    delta_reconstruct: "По хорошему датчику + ΔP",
    delta_noise:       "По хорошему датчику + ΔP + шум",
    exclude:           "Исключить (NaN)",
    zero_flow:         "Дебит = 0",
    median_3d:         "Медиана (3 дня)",
    median_1d:         "Медиана (1 день)",
  };

  // ─── State ───
  let chart = null;
  let masks = [];
  let candidates = [];      // raw candidates from auto-detect
  let groupedCandidates = []; // after merge
  let selectedMaskId = null;
  let selectMode = false;
  let dragStart = null;      // for drag-select

  // ─── DOM refs ───
  const $well       = document.getElementById("pmWell");
  const $dateFrom   = document.getElementById("pmDateFrom");
  const $dateTo     = document.getElementById("pmDateTo");
  const $load       = document.getElementById("pmLoad");
  const $autoDetect = document.getElementById("pmAutoDetect");
  const $selectMode = document.getElementById("pmSelectMode");
  const $resetZoom  = document.getElementById("pmResetZoom");
  const $canvas     = document.getElementById("pmChart");
  const $status     = document.getElementById("pmStatus");
  const $maskList   = document.getElementById("pmMaskList");
  const $candidateList = document.getElementById("pmCandidateList");
  const $detail     = document.getElementById("pmDetail");
  const $mergeGap   = document.getElementById("pmMergeGap");

  // Detail form
  const $dtStart     = document.getElementById("pmDtStart");
  const $dtEnd       = document.getElementById("pmDtEnd");
  const $problemType = document.getElementById("pmProblemType");
  const $sensor      = document.getElementById("pmSensor");
  const $method      = document.getElementById("pmMethod");
  const $reason      = document.getElementById("pmReason");
  const $maskId      = document.getElementById("pmMaskId");
  const $detailTitle = document.getElementById("pmDetailTitle");
  const $saveMask    = document.getElementById("pmSaveMask");
  const $cancelMask  = document.getElementById("pmCancelMask");
  const $deleteMask  = document.getElementById("pmDeleteMask");

  // ─── Init ───
  setDefaultDates();
  $well.addEventListener("change", onWellChange);
  $load.addEventListener("click", loadData);
  $resetZoom.addEventListener("click", () => { if (chart) chart.resetZoom(); });
  $saveMask.addEventListener("click", saveMask);
  $cancelMask.addEventListener("click", closeDetail);
  $deleteMask.addEventListener("click", deleteMask);

  if ($autoDetect) $autoDetect.addEventListener("click", runAutoDetect);
  if ($selectMode) $selectMode.addEventListener("click", toggleSelectMode);

  // Tabs
  document.querySelectorAll(".pm-tab").forEach(tab => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".pm-tab").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".pm-tab-content").forEach(c => c.classList.remove("active"));
      tab.classList.add("active");
      document.getElementById("tab-" + tab.dataset.tab).classList.add("active");
    });
  });

  // Merge gap regroup
  if (document.getElementById("pmRegroup")) {
    document.getElementById("pmRegroup").addEventListener("click", () => {
      groupedCandidates = mergeCandidates(candidates, parseInt($mergeGap.value) || 30);
      renderCandidates();
      updateAnnotations();
    });
  }

  // Bulk actions
  const $selectAll = document.getElementById("pmSelectAll");
  const $approveSelected = document.getElementById("pmApproveSelected");
  const $rejectSelected = document.getElementById("pmRejectSelected");
  if ($selectAll) $selectAll.addEventListener("click", toggleSelectAllCandidates);
  if ($approveSelected) $approveSelected.addEventListener("click", () => bulkAction("approve"));
  if ($rejectSelected)  $rejectSelected.addEventListener("click",  () => bulkAction("reject"));

  // ─── Functions ───

  function setDefaultDates() {
    const today = new Date();
    const weekAgo = new Date(today);
    weekAgo.setDate(weekAgo.getDate() - 7);
    $dateTo.value = fmt(today);
    $dateFrom.value = fmt(weekAgo);
  }

  function fmt(d) {
    return d.toISOString().slice(0, 10);
  }

  function fmtLocal(isoStr) {
    if (!isoStr) return "—";
    const d = new Date(isoStr);
    return d.toLocaleString("ru-RU", { day:"2-digit", month:"2-digit", hour:"2-digit", minute:"2-digit" });
  }

  function fmtLocalFull(isoStr) {
    if (!isoStr) return "";
    return isoStr.slice(0, 16); // for datetime-local input
  }

  function onWellChange() {
    const hasWell = !!$well.value;
    $load.disabled = !hasWell;
    if ($autoDetect) $autoDetect.disabled = true;
    if ($selectMode) $selectMode.disabled = true;
    $resetZoom.disabled = true;
  }

  // ─── Load data (chart + masks) ───

  async function loadData() {
    const wellId = $well.value;
    if (!wellId) return;

    $status.textContent = "Загрузка...";
    $load.disabled = true;

    try {
      // Load chart data and masks in parallel
      const [chartData, masksData] = await Promise.all([
        fetchChart(wellId),
        fetchMasks(wellId),
      ]);

      masks = masksData;
      renderChart(chartData);
      renderMaskList();
      buildLegend();

      if ($autoDetect) $autoDetect.disabled = false;
      if ($selectMode) $selectMode.disabled = false;
      $resetZoom.disabled = false;
      $status.textContent = `Точек: ${chartData.points.length}, масок: ${masks.length}`;
    } catch (e) {
      $status.textContent = "Ошибка: " + e.message;
      console.error(e);
    } finally {
      $load.disabled = false;
    }
  }

  async function fetchChart(wellId) {
    const params = new URLSearchParams({
      start: $dateFrom.value + "T00:00:00",
      end:   $dateTo.value + "T23:59:59",
      interval: "5",
      filter_zeros: "true",
    });
    const r = await fetch(`${API_CHART}/${wellId}?${params}`);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  async function fetchMasks(wellId) {
    const r = await fetch(`${API_MASKS}?well_id=${wellId}`);
    if (!r.ok) throw new Error(await r.text());
    const all = await r.json();
    // Filter to current date range
    const from = new Date($dateFrom.value + "T00:00:00");
    const to   = new Date($dateTo.value + "T23:59:59");
    return all.filter(m => {
      const ms = new Date(m.dt_start);
      const me = new Date(m.dt_end);
      return ms < to && me > from;
    });
  }

  // ─── Chart ───

  function renderChart(data) {
    const points = data.points || [];
    const labels = points.map(p => p.t);
    const pTube  = points.map(p => p.p_tube_avg);
    const pLine  = points.map(p => p.p_line_avg);

    if (chart) chart.destroy();

    const ctx = $canvas.getContext("2d");
    chart = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "P труб (атм)",
            data: pTube,
            borderColor: "#ef4444",
            backgroundColor: "rgba(239,68,68,0.1)",
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.1,
          },
          {
            label: "P лин (атм)",
            data: pLine,
            borderColor: "#3b82f6",
            backgroundColor: "rgba(59,130,246,0.1)",
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: {
            type: "time",
            time: { tooltipFormat: "dd.MM HH:mm", displayFormats: { hour: "dd.MM HH:mm", day: "dd.MM" } },
            ticks: { maxTicksLimit: 20, font: { size: 10 } },
          },
          y: {
            title: { display: true, text: "атм", font: { size: 11 } },
            ticks: { font: { size: 10 } },
          },
        },
        plugins: {
          legend: { position: "top", labels: { font: { size: 11 }, usePointStyle: true, pointStyle: "line" } },
          zoom: {
            pan: { enabled: true, mode: "x" },
            zoom: {
              wheel: { enabled: true },
              pinch: { enabled: true },
              mode: "x",
            },
          },
          annotation: { annotations: {} },
          tooltip: {
            callbacks: {
              title: (items) => {
                if (!items.length) return "";
                const d = new Date(items[0].parsed.x);
                return d.toLocaleString("ru-RU", { day:"2-digit", month:"2-digit", year:"numeric", hour:"2-digit", minute:"2-digit" });
              },
            },
          },
        },
      },
      plugins: [dragSelectPlugin],
    });

    updateAnnotations();
  }

  // ─── Annotations (mask zones on chart) ───

  function updateAnnotations() {
    if (!chart) return;

    const annotations = {};

    // Active masks
    masks.forEach((m, i) => {
      const colors = PROBLEM_COLORS[m.problem_type] || PROBLEM_COLORS.manual;
      const isSelected = m.id === selectedMaskId;
      annotations[`mask_${m.id}`] = {
        type: "box",
        xMin: m.dt_start,
        xMax: m.dt_end,
        backgroundColor: isSelected ? colors.bg.replace("0.18", "0.35") : colors.bg,
        borderColor: colors.border,
        borderWidth: isSelected ? 3 : 1,
        borderDash: m.is_verified ? [] : [4, 4],
        label: {
          display: true,
          content: colors.label,
          position: "start",
          font: { size: 9, weight: isSelected ? "bold" : "normal" },
          color: colors.border,
          padding: 2,
        },
        click: () => openMaskForEdit(m),
      };
    });

    // Candidate zones
    groupedCandidates.forEach((c, i) => {
      annotations[`cand_${i}`] = {
        type: "box",
        xMin: c.dt_start,
        xMax: c.dt_end,
        backgroundColor: "rgba(255,205,86,0.15)",
        borderColor: "#eab308",
        borderWidth: 1,
        borderDash: [5, 5],
        label: {
          display: true,
          content: c.count > 1 ? `? x${c.count}` : "?",
          position: "start",
          font: { size: 9 },
          color: "#ca8a04",
          padding: 2,
        },
      };
    });

    chart.options.plugins.annotation.annotations = annotations;
    chart.update("none");
  }

  // ─── Drag-select plugin ───

  const dragSelectPlugin = {
    id: "dragSelect",
    beforeEvent(chart, args) {
      if (!selectMode || !IS_ADMIN) return;
      const evt = args.event;
      const area = chart.chartArea;

      if (evt.type === "mousedown" && evt.x >= area.left && evt.x <= area.right) {
        dragStart = chart.scales.x.getValueForPixel(evt.x);
        return;
      }

      if (evt.type === "mouseup" && dragStart !== null) {
        const dragEnd = chart.scales.x.getValueForPixel(evt.x);
        const minT = Math.min(dragStart, dragEnd);
        const maxT = Math.max(dragStart, dragEnd);
        dragStart = null;

        // Open detail panel with selected range
        const dtFrom = new Date(minT);
        const dtTo   = new Date(maxT);
        if ((maxT - minT) > 60000) { // at least 1 minute
          openDetailForNew(dtFrom, dtTo);
        }
        // Remove selection overlay
        delete chart.options.plugins.annotation.annotations._dragsel;
        chart.update("none");
        return;
      }

      if (evt.type === "mousemove" && dragStart !== null) {
        const curX = chart.scales.x.getValueForPixel(evt.x);
        chart.options.plugins.annotation.annotations._dragsel = {
          type: "box",
          xMin: Math.min(dragStart, curX),
          xMax: Math.max(dragStart, curX),
          backgroundColor: "rgba(220,38,38,0.12)",
          borderColor: "#dc2626",
          borderWidth: 2,
          borderDash: [3, 3],
        };
        chart.update("none");
      }
    },
  };

  function toggleSelectMode() {
    selectMode = !selectMode;
    $selectMode.classList.toggle("active", selectMode);
    $canvas.style.cursor = selectMode ? "crosshair" : "";

    // Disable zoom in select mode to avoid conflict
    if (chart) {
      chart.options.plugins.zoom.pan.enabled = !selectMode;
      chart.options.plugins.zoom.zoom.wheel.enabled = !selectMode;
      chart.update("none");
    }
  }

  // ─── Mask list (sidebar) ───

  function renderMaskList() {
    if (!masks.length) {
      $maskList.innerHTML = '<div style="color:#9ca3af; text-align:center; padding:20px;">Нет масок за выбранный период</div>';
      return;
    }

    $maskList.innerHTML = masks.map(m => {
      const colors = PROBLEM_COLORS[m.problem_type] || PROBLEM_COLORS.manual;
      const isSelected = m.id === selectedMaskId;
      return `
        <div class="pm-mask-card ${isSelected ? 'selected' : ''}" data-id="${m.id}">
          <div class="mc-color" style="background:${colors.border}"></div>
          <div class="mc-info">
            <div class="mc-time">${fmtLocal(m.dt_start)} — ${fmtLocal(m.dt_end)}</div>
            <div class="mc-type">${colors.label}${m.is_verified ? '' : ' (не подтв.)'}</div>
            <div class="mc-method">${METHOD_LABELS[m.correction_method] || m.correction_method}</div>
            ${m.reason ? `<div class="mc-method" style="color:#6b7280;">${m.reason}</div>` : ''}
          </div>
          ${IS_ADMIN ? `<div class="mc-actions">
            <button class="mc-btn" onclick="event.stopPropagation(); window._pmEditMask(${m.id})">Изм.</button>
            <button class="mc-btn danger" onclick="event.stopPropagation(); window._pmQuickDelete(${m.id})">✕</button>
          </div>` : ''}
        </div>
      `;
    }).join("");

    // Click to highlight on chart
    $maskList.querySelectorAll(".pm-mask-card").forEach(card => {
      card.addEventListener("click", () => {
        const id = parseInt(card.dataset.id);
        selectedMaskId = selectedMaskId === id ? null : id;
        renderMaskList();
        updateAnnotations();

        // Zoom to mask
        if (selectedMaskId) {
          const m = masks.find(x => x.id === id);
          if (m && chart) {
            const pad = (new Date(m.dt_end) - new Date(m.dt_start)) * 0.5;
            chart.zoomScale("x", {
              min: new Date(m.dt_start).getTime() - pad,
              max: new Date(m.dt_end).getTime() + pad,
            });
          }
        }
      });
    });
  }

  // ─── Global handlers for inline onclick ───

  window._pmEditMask = function (id) {
    const m = masks.find(x => x.id === id);
    if (m) openMaskForEdit(m);
  };

  window._pmQuickDelete = async function (id) {
    if (!confirm("Удалить маску?")) return;
    try {
      const r = await fetch(`${API_MASKS}/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      await loadData();
    } catch (e) {
      alert("Ошибка: " + e.message);
    }
  };

  // ─── Detail panel ───

  function openDetailForNew(dtFrom, dtTo) {
    $maskId.value = "";
    $detailTitle.textContent = "Новая маска";
    $dtStart.value = toLocalInput(dtFrom);
    $dtEnd.value = toLocalInput(dtTo);
    $problemType.value = "manual";
    $sensor.value = "both";
    $method.value = "interpolate_noise";
    $reason.value = "";
    $deleteMask.style.display = "none";
    $detail.classList.add("open");
  }

  function openMaskForEdit(m) {
    if (!IS_ADMIN) return;
    $maskId.value = m.id;
    $detailTitle.textContent = "Редактирование маски #" + m.id;
    $dtStart.value = fmtLocalFull(m.dt_start);
    $dtEnd.value = fmtLocalFull(m.dt_end);
    $problemType.value = m.problem_type;
    $sensor.value = m.affected_sensor;
    $method.value = m.correction_method;
    $reason.value = m.reason || "";
    $deleteMask.style.display = "inline-block";
    $detail.classList.add("open");

    selectedMaskId = m.id;
    renderMaskList();
    updateAnnotations();
  }

  function closeDetail() {
    $detail.classList.remove("open");
    $maskId.value = "";
    selectedMaskId = null;
    renderMaskList();
    updateAnnotations();
  }

  function toLocalInput(d) {
    // Format Date to datetime-local value
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  // ─── Save / Delete mask ───

  async function saveMask() {
    const wellId = $well.value;
    if (!wellId) return;

    const body = {
      well_id: parseInt(wellId),
      dt_start: $dtStart.value + ":00",
      dt_end:   $dtEnd.value + ":00",
      problem_type: $problemType.value,
      affected_sensor: $sensor.value,
      correction_method: $method.value,
      reason: $reason.value || null,
    };

    const maskId = $maskId.value;
    try {
      let r;
      if (maskId) {
        r = await fetch(`${API_MASKS}/${maskId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } else {
        r = await fetch(API_MASKS, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }
      if (!r.ok) throw new Error(await r.text());

      closeDetail();
      await loadData();
    } catch (e) {
      alert("Ошибка сохранения: " + e.message);
    }
  }

  async function deleteMask() {
    const maskId = $maskId.value;
    if (!maskId || !confirm("Удалить маску?")) return;

    try {
      const r = await fetch(`${API_MASKS}/${maskId}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      closeDetail();
      await loadData();
    } catch (e) {
      alert("Ошибка: " + e.message);
    }
  }

  // ─── Auto-detect ───

  async function runAutoDetect() {
    const wellId = $well.value;
    if (!wellId) return;

    $autoDetect.disabled = true;
    $autoDetect.textContent = "Поиск...";

    try {
      const daysDiff = Math.ceil((new Date($dateTo.value) - new Date($dateFrom.value)) / 86400000);
      const r = await fetch(`${API_MASKS}/detect?well_id=${wellId}&days=${Math.max(daysDiff, 7)}`);
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();

      candidates = (data.anomalies || []).map(a => ({
        ...a,
        dt_start: a.dt_start,
        dt_end: a.dt_end,
        checked: false,
      }));

      groupedCandidates = mergeCandidates(candidates, parseInt($mergeGap.value) || 30);
      renderCandidates();
      updateAnnotations();

      // Switch to candidates tab
      document.querySelectorAll(".pm-tab").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".pm-tab-content").forEach(c => c.classList.remove("active"));
      document.querySelector('[data-tab="candidates"]').classList.add("active");
      document.getElementById("tab-candidates").classList.add("active");

      $status.textContent = `Найдено аномалий: ${candidates.length}, групп: ${groupedCandidates.length}`;
    } catch (e) {
      alert("Ошибка автодетекции: " + e.message);
    } finally {
      $autoDetect.disabled = false;
      $autoDetect.textContent = "Автодетекция";
    }
  }

  // ─── Merge candidates ───

  function mergeCandidates(cands, gapMinutes) {
    if (!cands.length) return [];

    const sorted = [...cands].sort((a, b) => new Date(a.dt_start) - new Date(b.dt_start));
    const gapMs = gapMinutes * 60000;
    const groups = [];
    let group = { ...sorted[0], count: 1, items: [sorted[0]] };

    for (let i = 1; i < sorted.length; i++) {
      const c = sorted[i];
      const prevEnd = new Date(group.dt_end).getTime();
      const curStart = new Date(c.dt_start).getTime();

      if (curStart - prevEnd <= gapMs) {
        // Merge
        group.dt_end = c.dt_end;
        group.count++;
        group.items.push(c);
        // Take highest confidence
        if ((c.confidence || 0) > (group.confidence || 0)) {
          group.confidence = c.confidence;
          group.affected_sensor = c.affected_sensor;
        }
      } else {
        groups.push(group);
        group = { ...c, count: 1, items: [c] };
      }
    }
    groups.push(group);
    return groups;
  }

  // ─── Render candidates list ───

  function renderCandidates() {
    if (!groupedCandidates.length) {
      $candidateList.innerHTML = '<div style="color:#9ca3af; text-align:center; padding:20px;">Аномалий не найдено</div>';
      updateBulkButtons();
      return;
    }

    $candidateList.innerHTML = groupedCandidates.map((c, i) => {
      const dur = ((new Date(c.dt_end) - new Date(c.dt_start)) / 3600000).toFixed(1);
      const conf = c.confidence ? `${Math.round(c.confidence * 100)}%` : "—";
      return `
        <div class="pm-mask-card" data-idx="${i}">
          <input type="checkbox" class="cand-check" data-idx="${i}" ${c.checked ? 'checked' : ''}
                 onclick="event.stopPropagation(); window._pmToggleCand(${i}, this.checked)">
          <div class="mc-color" style="background:#eab308"></div>
          <div class="mc-info">
            <div class="mc-time">${fmtLocal(c.dt_start)} — ${fmtLocal(c.dt_end)}</div>
            <div class="mc-type">
              ${c.affected_sensor || 'p_tube'} · ${dur}ч · уверенность ${conf}
              ${c.count > 1 ? ` · <b>x${c.count}</b> аномалий` : ''}
            </div>
            <div class="mc-method">${c.suggested_method || 'interpolate'}</div>
          </div>
          ${IS_ADMIN ? `<div class="mc-actions">
            <button class="mc-btn" onclick="event.stopPropagation(); window._pmApproveCand(${i})"
                    title="Создать маску">&#10003;</button>
          </div>` : ''}
        </div>
      `;
    }).join("");

    // Click to zoom
    $candidateList.querySelectorAll(".pm-mask-card").forEach(card => {
      card.addEventListener("click", () => {
        const idx = parseInt(card.dataset.idx);
        const c = groupedCandidates[idx];
        if (c && chart) {
          const pad = (new Date(c.dt_end) - new Date(c.dt_start)) * 0.5;
          chart.zoomScale("x", {
            min: new Date(c.dt_start).getTime() - pad,
            max: new Date(c.dt_end).getTime() + pad,
          });
        }
      });
    });

    updateBulkButtons();
  }

  window._pmToggleCand = function (idx, checked) {
    groupedCandidates[idx].checked = checked;
    updateBulkButtons();
  };

  window._pmApproveCand = function (idx) {
    const c = groupedCandidates[idx];
    if (!c) return;
    // Open detail panel pre-filled
    openDetailForNew(new Date(c.dt_start), new Date(c.dt_end));
    $sensor.value = c.affected_sensor || "both";
    $method.value = c.suggested_method || "interpolate_noise";
    $problemType.value = "sensor_fault";
  };

  function toggleSelectAllCandidates() {
    const allChecked = groupedCandidates.every(c => c.checked);
    groupedCandidates.forEach(c => c.checked = !allChecked);
    renderCandidates();
  }

  function updateBulkButtons() {
    const checkedCount = groupedCandidates.filter(c => c.checked).length;
    if ($approveSelected) {
      $approveSelected.disabled = checkedCount === 0;
      $approveSelected.textContent = checkedCount > 0 ? `Утвердить (${checkedCount})` : "Утвердить";
    }
    if ($rejectSelected) {
      $rejectSelected.disabled = checkedCount === 0;
    }
  }

  async function bulkAction(action) {
    const selected = groupedCandidates.filter(c => c.checked);
    if (!selected.length) return;

    if (action === "approve") {
      // Create masks for all selected candidates
      const wellId = parseInt($well.value);
      let created = 0;
      for (const c of selected) {
        try {
          const r = await fetch(API_MASKS, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              well_id: wellId,
              dt_start: c.dt_start,
              dt_end: c.dt_end,
              problem_type: "sensor_fault",
              affected_sensor: c.affected_sensor || "both",
              correction_method: c.suggested_method || "interpolate_noise",
              reason: `Авто: ${c.dp_deviation || ''} атм, ${c.duration_hours || ''}ч`,
            }),
          });
          if (r.ok) created++;
        } catch (e) {
          console.error(e);
        }
      }
      alert(`Создано масок: ${created}`);
      // Remove approved from candidates
      groupedCandidates = groupedCandidates.filter(c => !c.checked);
      renderCandidates();
      await loadData();
    } else {
      // Reject — just remove from list
      groupedCandidates = groupedCandidates.filter(c => !c.checked);
      renderCandidates();
      updateAnnotations();
    }
  }

  // ─── Legend ───

  function buildLegend() {
    const $legend = document.getElementById("pmLegend");
    const usedTypes = new Set(masks.map(m => m.problem_type));
    if (!usedTypes.size) {
      $legend.innerHTML = "";
      return;
    }
    $legend.innerHTML = [...usedTypes].map(t => {
      const c = PROBLEM_COLORS[t] || PROBLEM_COLORS.manual;
      return `<div class="pm-legend-item"><div class="dot" style="background:${c.border}"></div>${c.label}</div>`;
    }).join("");
  }

})();
