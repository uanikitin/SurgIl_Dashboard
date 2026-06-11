/*
 * chapter_preview.js — универсальный модуль правой панели главы отчёта.
 *
 * Управляет каркасом из components/_chapter_panel.html:
 *   - переключение табов HTML / PDF
 *   - загрузка HTML-превью главы (preview_endpoint)
 *   - пересборка PDF-превью (pdf_endpoint)
 *   - бэдж актуальности PDF
 *   - скачивание HTML-превью файлом
 *
 * Vanilla JS, без зависимостей. Не использует глобальных переменных,
 * кроме window.initChapterPanel.
 *
 * Использование:
 *   initChapterPanel({
 *     chapter_id:       "demo",
 *     kinds:            ["period_analysis", "comparison"],
 *     preview_endpoint: "/api/chapter/preview",
 *     pdf_endpoint:     "/api/adaptation-report/preview-pdf",
 *     pdf_only_chapter: "customer_data",
 *     get_well_id:      () => currentWellId,
 *   });
 */
(function () {
  'use strict';

  function initChapterPanel(config) {
    const cid = config.chapter_id;
    if (!cid) {
      console.error('[chapter_preview] config.chapter_id обязателен');
      return;
    }

    // ── element refs (id с префиксом chp-<cid>-) ──
    const $ = (suffix) => document.getElementById('chp-' + cid + '-' + suffix);
    const tabsEl     = $('tabs');
    const paneHtml   = $('pane-html');
    const panePdf    = $('pane-pdf');
    const htmlContent= $('html-content');
    const countEl    = $('count');
    const badgeEl    = $('pdf-badge');
    const iframeEl   = $('pdf-iframe');
    const rebuildBtn = $('pdf-rebuild');
    const statusEl   = $('pdf-status');
    const dlPdf      = $('pdf-download');
    const popoutBtn  = $('pdf-popout');
    const refreshBtn = $('refresh');
    const dlHtmlBtn  = $('html-download');

    if (!tabsEl || !paneHtml || !panePdf) {
      console.error('[chapter_preview] каркас chp-' + cid + '-* не найден в DOM');
      return;
    }

    let pdfStale = true;

    // ── переключение табов ──
    function switchTab(tab) {
      tabsEl.querySelectorAll('.chp-tab').forEach((t) => {
        t.classList.toggle('chp-tab-active', t.dataset.tab === tab);
      });
      paneHtml.style.display = (tab === 'html') ? 'block' : 'none';
      panePdf.style.display  = (tab === 'pdf')  ? 'flex'  : 'none';
    }
    tabsEl.querySelectorAll('.chp-tab').forEach((t) => {
      t.addEventListener('click', () => switchTab(t.dataset.tab));
    });

    // ── бэдж актуальности PDF ──
    function markPdfStale() {
      pdfStale = true;
      if (!badgeEl) return;
      badgeEl.textContent = '⚠ PDF устарел';
      badgeEl.style.background = '#fee2e2';
      badgeEl.style.color = '#991b1b';
      badgeEl.style.display = '';
    }
    function markPdfFresh() {
      pdfStale = false;
      if (!badgeEl) return;
      badgeEl.textContent = '✓ PDF актуален';
      badgeEl.style.background = '#d1fae5';
      badgeEl.style.color = '#065f46';
      badgeEl.style.display = '';
    }

    // ── рендер одного блока ──
    function renderBlock(b) {
      const parts = ['<div class="chp-block">'];
      const title = b.title || '(без заголовка)';
      parts.push('<div class="chp-block-title">' + escapeHtml(title) + '</div>');
      if (b.kind) {
        parts.push('<div style="font-size:0.78rem;color:#6b7280;">' +
                   escapeHtml(b.kind) + (b.in_report ? ' · в отчёте' : '') + '</div>');
      }
      if (b.summary) {
        parts.push('<div style="font-size:0.85rem;margin-top:4px;">' +
                   escapeHtml(String(b.summary)) + '</div>');
      }
      if (b.is_corrupted) {
        parts.push('<div class="chp-block-corrupt">⚠ Блок повреждён</div>');
      }
      (b.warnings || []).forEach((w) => {
        parts.push('<div class="chp-block-warn">' + escapeHtml(String(w)) + '</div>');
      });
      parts.push('</div>');
      return parts.join('');
    }

    // ── загрузка HTML-превью ──
    // Три режима:
    //   0. client_render — грузим блоки и рендерим общим движком
    //      window.renderChapter (тот же, что глава «Заказчик»).
    //   1. сервер вернул готовый HTML  → data.html (вставляем как есть)
    //   2. сервер вернул список блоков → data.blocks (примитивный renderBlock)
    async function refreshHtmlPreview() {
      const wellId = config.get_well_id ? config.get_well_id() : null;
      if (!wellId) {
        htmlContent.innerHTML =
          '<div class="chp-empty">Скважина не выбрана.</div>';
        if (countEl) countEl.textContent = '';
        return;
      }
      htmlContent.innerHTML =
        '<div class="chp-empty">Загрузка…</div>';

      // ── Режим 0: общий движок renderChapter ──
      if (config.client_render && typeof window.renderChapter === 'function') {
        try {
          const url = new URL(config.preview_endpoint, window.location.origin);
          url.searchParams.set('well_id', wellId);
          // НЕ передаём chapter в URL — фильтрация только на клиенте,
          // чтобы не исключить блоки без params.chapter (adaptation_period_analysis и др.)
          const resp = await fetch(url.toString());
          if (!resp.ok) throw new Error('HTTP ' + resp.status);
          const data = await resp.json();
          let blocks = data.blocks || data.items || [];
          // Нормализация: некоторые API возвращают block_id вместо id
          blocks = blocks.map(function (b) {
            if (!b.id && b.block_id) b.id = b.block_id;
            return b;
          });
          // Если задан block_filter — он ЕДИНСТВЕННЫЙ источник истины отбора
          // блоков главы (тот же предикат, что использует левый список → HTML
          // и список блоков идентичны по построению). Иначе — legacy kinds +
          // chapter_filter.
          if (typeof config.block_filter === 'function') {
            blocks = blocks.filter(config.block_filter);
          } else {
            if (config.kinds && config.kinds.length) {
              blocks = blocks.filter(function (b) {
                return config.kinds.indexOf(b.kind) !== -1;
              });
            }
            // Клиентская фильтрация по chapter для общих типов блоков (segment_analysis)
            // Блоки без params.chapter проходят (специфичные для главы)
            // Блоки с params.chapter проходят только если совпадает с chapter_filter
            if (config.chapter_filter) {
              blocks = blocks.filter(function (b) {
                const ch = (b.params && b.params.chapter) || null;
                // Блоки без chapter или с совпадающим chapter проходят
                return !ch || ch === config.chapter_filter;
              });
            }
          }
          window.renderChapter(blocks, {
            containerId:  htmlContent.id,
            countId:      countEl ? countEl.id : null,
            chapterTitle: config.title || 'Глава',
          });
        } catch (e) {
          htmlContent.innerHTML =
            '<div class="chp-block-corrupt">Ошибка загрузки превью: ' +
            escapeHtml(String(e.message || e)) + '</div>';
        }
        return;
      }

      try {
        const method = (config.preview_method || 'GET').toUpperCase();
        let resp;
        if (method === 'POST') {
          resp = await fetch(config.preview_endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(
              Object.assign({ well_id: wellId }, config.preview_body || {})
            ),
          });
        } else {
          const url = new URL(config.preview_endpoint, window.location.origin);
          url.searchParams.set('well_id', wellId);
          if (config.kinds && config.kinds.length) {
            url.searchParams.set('kinds', config.kinds.join(','));
          }
          resp = await fetch(url.toString());
        }
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();

        if (typeof data.html === 'string') {
          // Режим 1: готовый HTML с сервера.
          htmlContent.innerHTML = data.html ||
            '<div class="chp-empty">Нет блоков для отчёта.</div>';
          if (countEl) {
            const n = data.blocks_count || 0;
            countEl.textContent = 'блоков в отчёт: ' + n;
          }
        } else {
          // Режим 2: рендер блоков клиентом.
          const blocks = data.blocks || [];
          if (!blocks.length) {
            htmlContent.innerHTML =
              '<div class="chp-empty">Нет блоков для отчёта.</div>';
          } else {
            htmlContent.innerHTML = blocks.map(renderBlock).join('');
          }
          if (countEl) {
            countEl.textContent =
              'блоков: ' + (data.total || blocks.length) +
              ' · в отчёт: ' + (data.in_report_count || 0);
          }
        }
      } catch (e) {
        htmlContent.innerHTML =
          '<div class="chp-block-corrupt">Ошибка загрузки превью: ' +
          escapeHtml(String(e.message || e)) + '</div>';
      }
    }

    // ── пересборка PDF ──
    async function rebuildPdf() {
      const wellId = config.get_well_id ? config.get_well_id() : null;
      if (!wellId) {
        alert('Скважина не выбрана.');
        return;
      }
      switchTab('pdf');
      rebuildBtn.disabled = true;
      const oldText = rebuildBtn.textContent;
      rebuildBtn.textContent = '⏳ Рендер…';
      if (statusEl) {
        statusEl.textContent = 'xelatex 5–12 сек…';
        statusEl.style.color = '#6b7280';
      }
      try {
        // pdf_extra_body может быть объектом или функцией, возвращающей объект
        const extraBody = typeof config.get_pdf_extra_body === 'function'
          ? config.get_pdf_extra_body()
          : (config.pdf_extra_body || {});
        const body = Object.assign(
          { well_id: wellId, only_chapter: config.pdf_only_chapter },
          extraBody
        );
        const resp = await fetch(config.pdf_endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const blob = await resp.blob();
        const objUrl = URL.createObjectURL(blob);
        iframeEl.src = objUrl;
        if (dlPdf) {
          dlPdf.href = objUrl;
          dlPdf.style.display = '';
        }
        if (popoutBtn) popoutBtn.href = objUrl;
        markPdfFresh();
        if (statusEl) {
          statusEl.textContent = '✓ собран';
          statusEl.style.color = '#065f46';
        }
      } catch (e) {
        markPdfStale();
        if (statusEl) {
          statusEl.textContent = 'ошибка: ' + (e.message || e);
          statusEl.style.color = '#991b1b';
        }
      } finally {
        rebuildBtn.disabled = false;
        rebuildBtn.textContent = oldText;
      }
    }

    // ── скачать HTML-превью файлом ──
    function downloadHtml() {
      const inner = htmlContent.innerHTML;
      const doc = '<!DOCTYPE html><html><head><meta charset="utf-8">' +
        '<title>Превью главы</title></head><body style="font-family:' +
        "'Times New Roman',serif;padding:20px;\">" + inner + '</body></html>';
      const blob = new Blob([doc], { type: 'text/html;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'chapter_' + cid + '.html';
      a.click();
      URL.revokeObjectURL(a.href);
    }

    // ── привязка кнопок ──
    if (refreshBtn) refreshBtn.addEventListener('click', refreshHtmlPreview);
    if (rebuildBtn) rebuildBtn.addEventListener('click', rebuildPdf);
    if (dlHtmlBtn)  dlHtmlBtn.addEventListener('click', downloadHtml);

    // ── первичная загрузка ──
    markPdfStale();
    refreshHtmlPreview();

    // ── публичный API экземпляра ──
    return {
      refresh: refreshHtmlPreview,
      rebuildPdf: rebuildPdf,
      switchTab: switchTab,
      markPdfStale: markPdfStale,
    };
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /*
   * buildChapterRightPanel — генерирует HTML-строку правой панели.
   * Для страниц где разметка строится в JS (не через Jinja-макрос),
   * например Step 3 wizard'а. Структура id (chp-<cid>-*) совпадает с
   * Jinja-партиалом _chapter_panel.html — поэтому initChapterPanel
   * привязывается к ней без изменений.
   *
   * Возвращает разметку ТОЛЬКО правой колонки (.chp-right).
   * Оборачивание в .chp-page / .chp-left делает вызывающий код.
   */
  function buildChapterRightPanel(chapterId, title) {
    const c = chapterId;
    return [
      '<div class="chp-right" id="chp-' + c + '-right">',
      '  <div class="chp-rp-head">',
      '    <span class="chp-rp-title">📑 Глава «' + escapeHtml(title) + '»</span>',
      '    <span class="chp-rp-count" id="chp-' + c + '-count"></span>',
      '    <span class="chp-rp-badge" id="chp-' + c + '-pdf-badge"></span>',
      '  </div>',
      '  <div class="chp-tabs" id="chp-' + c + '-tabs">',
      '    <button type="button" class="chp-tab chp-tab-active" data-tab="html">📑 HTML</button>',
      '    <button type="button" class="chp-tab" data-tab="pdf">📄 PDF</button>',
      '    <span class="chp-spacer"></span>',
      '    <button type="button" class="chp-tab-btn" id="chp-' + c + '-html-download"',
      '            title="Скачать HTML-превью файлом">⬇ HTML</button>',
      '    <button type="button" class="chp-tab-btn chp-icon-only" id="chp-' + c + '-refresh"',
      '            title="Перерисовать HTML-превью">🔄</button>',
      '  </div>',
      '  <div class="chp-pane-html" id="chp-' + c + '-pane-html">',
      '    <div id="chp-' + c + '-html-content">',
      '      <div class="chp-empty">Создай блок — он появится здесь.</div>',
      '    </div>',
      '  </div>',
      '  <div class="chp-pane-pdf" id="chp-' + c + '-pane-pdf">',
      '    <div class="chp-pdf-note">⚠ Временно: PDF собирается для главы заказчика ' +
      '(механическая проверка кнопки). PDF главы «' + escapeHtml(title) +
      '» — отдельная задача.</div>',
      '    <iframe class="chp-pdf-iframe" id="chp-' + c + '-pdf-iframe" src="about:blank"></iframe>',
      '    <div class="chp-pdf-bar">',
      '      <button type="button" class="chp-btn-primary" id="chp-' + c + '-pdf-rebuild"',
      '              title="Собрать PDF главы (xelatex)">🔁 Пересобрать</button>',
      '      <a id="chp-' + c + '-pdf-download" href="#" download="chapter.pdf"',
      '         style="display:none; padding:5px 12px; background:#10b981; color:#fff;' +
      ' text-decoration:none; border-radius:4px; font-size:0.8rem;">⬇ Скачать</a>',
      '      <a id="chp-' + c + '-pdf-popout" href="#" target="_blank"',
      '         style="padding:5px 12px; color:#1d4ed8; text-decoration:none;' +
      ' font-size:0.8rem; border:1px solid #93c5fd; border-radius:4px;">↗ В окно</a>',
      '      <span class="chp-pdf-status" id="chp-' + c + '-pdf-status"></span>',
      '    </div>',
      '  </div>',
      '</div>',
    ].join('\n');
  }

  window.initChapterPanel = initChapterPanel;
  window.buildChapterRightPanel = buildChapterRightPanel;
})();
