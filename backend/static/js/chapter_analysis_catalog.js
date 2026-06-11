/*
 * chapter_analysis_catalog.js — рендер каталога видов анализа (плитки).
 *
 * Публичный API:
 *   window.renderAnalysisCatalog(config, containerEl, onSelect)
 *
 * config: объект ChapterAnalysisCatalog (см. схему ниже).
 * containerEl: DOM-элемент, куда рендерятся плитки.
 * onSelect: function(analysisType) — вызывается при клике по плитке.
 *
 * Схема ChapterAnalysisCatalog:
 * {
 *   chapter: string,           // 'adaptation' | 'observation' | 'customer'
 *   blocksApiKinds: string,    // '&kinds=...' для запроса блоков этой главы
 *   analysisTypes: [
 *     {
 *       id: string,            // уникальный идентификатор вида
 *       kind: string,          // kind блока в customer_report_block
 *       label: string,         // название плитки (отображается пользователю)
 *       description: string,   // 2-3 строки: что делает анализ
 *       icon: string,          // символ/эмодзи для плитки
 *       level: 'main'|'child', // 'main' — основная плитка, 'child' — вложенная
 *       parentId: string|null, // id родительского вида (для child)
 *       enabled: boolean,      // child: доступен ли (false → disabled до результата родителя)
 *       runner: function|null, // ссылка на функцию запуска анализа
 *       renderer: function|null, // ссылка на функцию рендера результата
 *       attacher: function|null, // ссылка на функцию прикрепления блока
 *     }
 *   ]
 * }
 *
 * Правила рендера:
 * - Плитки 'main' рендерятся полного размера.
 * - Плитки 'child' рендерятся с отступом сразу под своим родителем.
 * - Плитки 'child' disabled, если enabled === false.
 * - Никаких API-запросов внутри этого модуля — snapshot-only.
 * - Данные приходят снаружи через config.
 */
(function () {
  'use strict';

  /**
   * Рендер каталога плиток анализа.
   *
   * @param {Object} config - ChapterAnalysisCatalog
   * @param {HTMLElement} containerEl - контейнер для рендера
   * @param {Function} onSelect - колбэк(analysisType): вызывается при клике
   */
  function renderAnalysisCatalog(config, containerEl, onSelect) {
    if (!containerEl) return;
    containerEl.innerHTML = '';

    var types = (config && config.analysisTypes) || [];

    // Индексируем по id для быстрого поиска родителей
    var byId = {};
    types.forEach(function (t) { byId[t.id] = t; });

    // Рендерим в порядке: main, сразу за ним его child
    var ordered = [];
    var pushed = {};
    types.forEach(function (t) {
      if (t.level !== 'child') {
        ordered.push(t);
        pushed[t.id] = true;
        types.forEach(function (c) {
          if (c.level === 'child' && c.parentId === t.id) {
            ordered.push(c);
            pushed[c.id] = true;
          }
        });
      }
    });
    // Подхватываем сирот-child без существующего родителя
    types.forEach(function (t) {
      if (!pushed[t.id]) { ordered.push(t); pushed[t.id] = true; }
    });

    ordered.forEach(function (type) {
      var isChild = type.level === 'child';
      var isDisabled = isChild && (type.enabled === false);

      var tile = document.createElement('div');
      tile.className = 'cat-tile' +
        (isChild ? ' cat-tile--child' : '') +
        (isDisabled ? ' cat-tile--disabled' : '');
      tile.setAttribute('data-analysis-id', type.id);
      if (isDisabled) {
        var parent = byId[type.parentId];
        tile.setAttribute('title', 'Сначала выполните «' +
          (parent ? parent.label : type.parentId) + '»');
      }

      var icon = document.createElement('div');
      icon.className = 'cat-tile__icon';
      icon.textContent = type.icon || '';

      var body = document.createElement('div');
      body.className = 'cat-tile__body';

      var label = document.createElement('div');
      label.className = 'cat-tile__label';
      label.textContent = type.label || '';

      var desc = document.createElement('div');
      desc.className = 'cat-tile__desc';
      desc.textContent = type.description || '';

      body.appendChild(label);
      body.appendChild(desc);
      tile.appendChild(icon);
      tile.appendChild(body);

      if (!isDisabled) {
        tile.addEventListener('click', function () {
          if (typeof onSelect === 'function') {
            onSelect(type);
          }
        });
      }

      containerEl.appendChild(tile);
    });
  }

  window.renderAnalysisCatalog = renderAnalysisCatalog;
})();
