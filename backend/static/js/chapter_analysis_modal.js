/*
 * chapter_analysis_modal.js — fullscreen-<dialog> для просмотра/запуска анализа.
 *
 * Публичный API:
 *   window.openAnalysisModal(options)
 *   window.closeAnalysisModal()
 *   window.setAnalysisModalLoading(text)
 *
 * options: {
 *   title: string,            // заголовок модалки
 *   kindBadgeHtml: string,    // HTML бейджа (kind-метки)
 *   bodyEl: HTMLElement,      // DOM-узел для тела модалки (если есть)
 *   bodyHtml: string,         // или HTML-строка (если bodyEl не передан)
 *   onAttach: function|null,  // колбэк кнопки «Добавить в отчёт»
 *   onRecalc: function|null,  // колбэк кнопки «Пересчитать» (только в mode='edit')
 *   onClose: function|null,   // колбэк при закрытии
 *   mode: 'view'|'edit',      // 'edit' добавляет кнопку «Пересчитать»
 * }
 *
 * Модалка создаётся один раз (singleton) и переиспользуется.
 * Не привязана ни к какой конкретной главе.
 * Snapshot-only: никаких API-запросов внутри модуля.
 */
(function () {
  'use strict';

  var _dialog = null;
  var _onAttach = null;
  var _onRecalc = null;
  var _onClose = null;

  function _ensureDialog() {
    if (_dialog) return _dialog;

    _dialog = document.createElement('dialog');
    _dialog.className = 'amodal';

    _dialog.innerHTML = [
      '<div class="amodal__head">',
        '<div class="amodal__head-left">',
          '<span class="amodal__badge" id="amodal-badge"></span>',
          '<span class="amodal__title" id="amodal-title"></span>',
        '</div>',
        '<button type="button" class="amodal__close" id="amodal-close" aria-label="Закрыть">&#x2715;</button>',
      '</div>',
      '<div class="amodal__body" id="amodal-body"></div>',
      '<div class="amodal__foot" id="amodal-foot">',
        '<button type="button" class="amodal__btn amodal__btn--recalc" id="amodal-recalc" style="display:none">',
          'Пересчитать',
        '</button>',
        '<div class="amodal__foot-right">',
          '<button type="button" class="amodal__btn amodal__btn--attach" id="amodal-attach">',
            'Добавить в отчёт',
          '</button>',
          '<button type="button" class="amodal__btn amodal__btn--close" id="amodal-close-btn">',
            'Закрыть',
          '</button>',
        '</div>',
      '</div>'
    ].join('');

    document.body.appendChild(_dialog);

    document.getElementById('amodal-close').addEventListener('click', closeAnalysisModal);
    document.getElementById('amodal-close-btn').addEventListener('click', closeAnalysisModal);
    document.getElementById('amodal-attach').addEventListener('click', function () {
      if (typeof _onAttach === 'function') _onAttach();
    });
    document.getElementById('amodal-recalc').addEventListener('click', function () {
      if (typeof _onRecalc === 'function') _onRecalc();
    });

    // Закрытие по клику на backdrop
    _dialog.addEventListener('click', function (e) {
      if (e.target === _dialog) closeAnalysisModal();
    });

    // Единая точка очистки: событие 'close' срабатывает И от кнопок (через
    // closeAnalysisModal → dialog.close()), И от нативного ESC. Значит cleanup
    // (возврат секций в стэш) выполнится при ЛЮБОМ способе выхода.
    _dialog.addEventListener('close', function () {
      var cb = _onClose;
      _onClose = null;
      _onAttach = null;
      _onRecalc = null;
      if (typeof cb === 'function') {
        try { cb(); } catch (e) { /* no-op */ }
      }
    });

    return _dialog;
  }

  /**
   * Установить состояние loading в теле модалки.
   */
  function setAnalysisModalLoading(text) {
    _ensureDialog();
    var body = document.getElementById('amodal-body');
    if (!body) return;
    body.innerHTML =
      '<div class="amodal__loading">' +
        '<span class="amodal__spinner"></span>' +
        (text || 'Вычисляю...') +
      '</div>';
  }

  /**
   * Открыть модалку.
   * @param {Object} options — см. описание выше
   */
  function openAnalysisModal(options) {
    options = options || {};
    _ensureDialog();

    _onAttach = options.onAttach || null;
    _onRecalc = options.onRecalc || null;
    _onClose  = options.onClose  || null;

    var badgeEl = document.getElementById('amodal-badge');
    var titleEl = document.getElementById('amodal-title');
    badgeEl.innerHTML = options.kindBadgeHtml || '';
    titleEl.textContent = options.title || '';

    var body = document.getElementById('amodal-body');
    body.innerHTML = '';
    if (options.bodyEl) {
      body.appendChild(options.bodyEl);
    } else if (options.bodyHtml) {
      body.innerHTML = options.bodyHtml;
    }

    var recalcBtn = document.getElementById('amodal-recalc');
    recalcBtn.style.display = (options.mode === 'edit' && _onRecalc) ? '' : 'none';

    var attachBtn = document.getElementById('amodal-attach');
    attachBtn.style.display = _onAttach ? '' : 'none';
    attachBtn.textContent = options.attachLabel || 'Добавить в отчёт';
    // Когда есть действие утверждения — вторая кнопка называется «Отмена».
    var closeBtn = document.getElementById('amodal-close-btn');
    if (closeBtn) closeBtn.textContent = _onAttach ? 'Отмена' : 'Закрыть';

    if (!_dialog.open) _dialog.showModal();

    // Plotly-графики, построенные в скрытом контейнере, имеют нулевую ширину.
    // После показа модалки уведомляем их о новом размере (cmp/сегментный).
    if (window.requestAnimationFrame) {
      window.requestAnimationFrame(function () {
        try { window.dispatchEvent(new Event('resize')); } catch (e) { /* no-op */ }
        if (window.Plotly && window.Plotly.Plots) {
          body.querySelectorAll('.js-plotly-plot').forEach(function (p) {
            try { window.Plotly.Plots.resize(p); } catch (e) { /* no-op */ }
          });
        }
      });
    }
  }

  /**
   * Закрыть модалку. Очистка выполняется в обработчике события 'close'
   * (см. _ensureDialog) — единообразно для кнопок и ESC.
   */
  function closeAnalysisModal() {
    if (_dialog && _dialog.open) {
      _dialog.close();
    }
  }

  window.openAnalysisModal       = openAnalysisModal;
  window.closeAnalysisModal      = closeAnalysisModal;
  window.setAnalysisModalLoading = setAnalysisModalLoading;
})();
