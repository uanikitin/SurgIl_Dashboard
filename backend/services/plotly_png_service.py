"""
plotly_png_service — рендер графиков главы в PNG через headless-браузер.

PDF (LaTeX) и HTML должны содержать ОДНИ И ТЕ ЖЕ графики. Поэтому графики для
PDF не рисуются отдельно (matplotlib), а снимаются с того же рендера, что и
HTML: window.renderChapter(blocks, cfg) из chapter_render.js.

Поток:
  1) блоки скважины (как берёт фронт: /api/customer-daily/blocks);
  2) временная HTML: локальный Plotly + chapter_render.js + вшитые блоки
     + renderChapter(...);
  3) headless Chromium (Playwright) открывает, ждёт отрисовки;
  4) снимаем КАЖДЫЙ график (.js-plotly-plot) по id в PNG;
  5) обрезаем поля; возвращаем {chart_id: png_path}.

Файлы кладутся в out_dir (временная папка сборки) и не хранятся — вызывающий
код чистит папку после компиляции. Дублей не возникает.

CLI (вызов из пайплайна отдельным процессом — изоляция от event-loop):
  python -m backend.services.plotly_png_service --well 17 --out /tmp/build \
      --chapter adaptation --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, datetime
from pathlib import Path


def _json_serial(obj):
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

ADAPTATION_KINDS = [
    "adaptation_period_analysis",
    "optimal_window",
    "reagent_irv_summary",
    "adaptation_comparison",
    "adaptation_effectiveness",
    "segment_analysis",
    "sensor_customer_comparison",
    "pressure_spectrum",
    "param_correlation",
]

# Виды блоков главы «Отчёт за период» (Шаг 8 мастера, chapter='period').
# Зеркало ADAPTATION_KINDS: те же Plotly-графики (chapter_render.js) снимаются
# headless-браузером для PDF — чтобы PDF и HTML содержали идентичные графики
# (PARITY). period_full_analysis рисует те же диви, что adaptation_period_analysis.
PERIOD_KINDS = [
    "period_full_analysis",
    "period_comparison",
    # «Оценка эффективности» периода — переиспользуем kind adaptation_effectiveness
    # (те же функции/настройки/стили, что в Адаптации), но chapter='period'.
    "adaptation_effectiveness",
    "segment_analysis",
    "segment_comparison",
    "sensor_customer_comparison",
    "pressure_spectrum",
    "param_correlation",
]

# Виды блоков главы «Наблюдение» (Шаг 3 мастера). Те же Plotly-графики
# (chapter_render.js), что в HTML, снимаются headless-браузером для PDF —
# чтобы PDF и HTML содержали идентичные графики (PARITY).
OBSERVATION_KINDS = [
    "observation_analysis",
    "observation_baseline",
    "observation_period",
    "observation_segment",
    "segment_analysis",
    "segment_comparison",
    "sensor_customer_comparison",
    "pressure_spectrum",
    "param_correlation",
    "stability_rose",   # роза нестабильности (глава Заказчик): диви prev-{id}-strose/-strosebar
]

_REPO = Path(__file__).resolve().parents[2]
_STATIC = _REPO / "backend" / "static"
_PLOTLY_JS = _STATIC / "vendor" / "plotly-2.32.0.min.js"
_RENDER_JS = _STATIC / "js" / "chapter_render.js"

_HTML_TMPL = """<!doctype html><html><head><meta charset="utf-8">
<script src="{plotly}"></script>
<script src="{render}"></script>
<style>body{{font-family:'Times New Roman',serif;padding:16px;width:1200px;background:#fff;}}
.js-plotly-plot .svg-container{{position:relative!important;}}
.js-plotly-plot .main-svg{{position:absolute!important;top:0!important;left:0!important;}}
.modebar,.modebar-container{{display:none!important;}}</style>
</head><body><div id="root"></div>
<script>window.__BLOCKS__={blocks_json};</script>
<script>
window.__done=false; window.__err=null;
try {{
  var raw=window.__BLOCKS__||{{}};
  var items=raw.blocks||raw.items||[];
  var blocks=items.map(function(b){{ if(!b.id&&b.block_id)b.id=b.block_id; return b; }});
  var KINDS={kinds_json};
  var CH={chapter_json};
  blocks=blocks.filter(function(b){{
    if(KINDS.indexOf(b.kind)===-1) return false;
    var ch=(b.params&&b.params.chapter)||null;
    return CH ? (ch===CH) : true;
  }});
  window.__nblocks=blocks.length;
  window.renderChapter(blocks,{{containerId:"root",chapterTitle:"Глава"}});
  window.__done=true;
}} catch(e) {{ window.__err=String(e&&e.stack||e); }}
</script></body></html>
"""


def _autocrop(path: str, pad: int = 6) -> None:
    try:
        from PIL import Image, ImageChops
    except Exception:
        return
    try:
        im = Image.open(path).convert("RGB")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        diff = ImageChops.difference(im, bg)
        bbox = diff.getbbox()
        if not bbox:
            return
        l, t, r, b = bbox
        l = max(0, l - pad); t = max(0, t - pad)
        r = min(im.width, r + pad); b = min(im.height, b + pad)
        im.crop((l, t, r, b)).save(path)
    except Exception:
        pass


def render_chart_pngs(blocks_payload, out_dir, chapter="adaptation",
                      kinds=None, scale=3, timeout_ms=20000):
    """Отрисовать графики главы в PNG. Возвращает {chart_id: png_path}."""
    from playwright.sync_api import sync_playwright

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    local_plotly = out / "plotly.min.js"
    local_render = out / "chapter_render.js"
    if not local_plotly.exists():
        shutil.copy(_PLOTLY_JS, local_plotly)
    shutil.copy(_RENDER_JS, local_render)

    html = _HTML_TMPL.format(
        plotly="plotly.min.js",
        render="chapter_render.js",
        blocks_json=json.dumps(blocks_payload, ensure_ascii=False, default=_json_serial),
        kinds_json=json.dumps(kinds or ADAPTATION_KINDS),
        chapter_json=json.dumps(chapter),
    )
    html_path = out / "_render.html"
    html_path.write_text(html, encoding="utf-8")

    result = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(device_scale_factor=scale,
                            viewport={"width": 1300, "height": 1600})
        pg = ctx.new_page()
        pg.goto(html_path.as_uri())
        try:
            pg.wait_for_function("window.__done===true || window.__err!==null",
                                 timeout=timeout_ms)
        except Exception:
            pass
        err = pg.evaluate("window.__err")
        if err:
            b.close()
            raise RuntimeError("renderChapter error: " + str(err))
        pg.wait_for_timeout(1200)
        # ── Перерендер графиков под ПЕЧАТЬ: размер PNG = целевой ширине в .tex ──
        # Главное: график генерируется СРАЗУ под нужную ширину (а не узким и потом
        # растягивается в LaTeX → мыло/огромные шрифты). И жёстко задаём ширину
        # САМОГО элемента — иначе .js-plotly-plot держит width:100% (≈850px) и
        # скриншот захватывает пустую полосу родителя справа от графика.
        # Классы по ширине отображения: half=0.49\textwidth, mid=0.7, full=1.0.
        try:
            pg.evaluate(
                """() => {
                  document.querySelectorAll('.js-plotly-plot').forEach(function(el){
                    var id = el.id || '';
                    var half = (/^scc-.*-chart-(q|dp)$/.test(id)) || (/^ps-.*-chart-(ptube|dp)$/.test(id));
                    var mid  = (/^pc-.*-chart$/.test(id));
                    var pie  = (/-pie$/.test(id));              // донат реагентов — квадрат, крупнее
                    var dev  = (/^scc-.*-chart-dev$/.test(id)); // отклонение — уже по ширине, выше
                    var w, h, ff, tf;
                    if (pie)       { w = 480;  h = 480; ff = 14; tf = 16; }
                    else if (dev)  { w = 1120; h = 300; ff = 14; tf = 16; }  // полная ширина, ниже исходного (440)
                    else if (half) { w = 560;  h = 380; ff = 13; tf = 14; }
                    else if (mid)  { w = 820;  h = 540; ff = 14; tf = 16; }
                    else           { w = 1120; h = 440; ff = 15; tf = 18; }  // full-width
                    try {
                      Plotly.relayout(el, {
                        width: w, height: h, autosize: false,
                        'font.size': ff, 'title.font.size': tf,
                        'font.family': 'Arial, Helvetica, sans-serif'
                      });
                    } catch(e) {}
                    // bounding box элемента == ширине графика → нет пустой полосы
                    el.style.width = w + 'px';
                    el.style.maxWidth = w + 'px';
                    el.style.height = h + 'px';
                    el.style.flex = '0 0 ' + w + 'px';
                    el.style.display = 'block';
                  });
                }"""
            )
            pg.wait_for_timeout(800)
        except Exception:
            pass
        plots = pg.query_selector_all(".js-plotly-plot")
        for i, el in enumerate(plots):
            cid = el.get_attribute("id") or ("chart_%d" % i)
            png = out / ("chart_%s.png" % cid)
            try:
                el.screenshot(path=str(png))
                _autocrop(str(png))
                result[cid] = str(png)
            except Exception:
                continue
        b.close()
    return result


_PDF_HTML_TMPL = """<!doctype html><html><head><meta charset="utf-8">
<script src="{plotly}"></script>
<script src="{render}"></script>
<style>
@page {{ size: A4; margin: 14mm 12mm; }}
html,body{{font-family:'Times New Roman',serif;background:#fff;color:#111;}}
body{{margin:0;padding:0;}}
#root{{width:100%;}}
.js-plotly-plot .modebar,.modebar-container{{display:none!important;}}
table{{page-break-inside:auto;}} tr{{page-break-inside:avoid;}}
h1,h2,h3,h4{{page-break-after:avoid;}}
img,.js-plotly-plot{{page-break-inside:avoid;}}
</style>
</head><body><div id="root"></div>
<script>window.__BLOCKS__={blocks_json};</script>
<script>
window.__done=false; window.__err=null;
try {{
  var raw=window.__BLOCKS__||{{}};
  var items=raw.blocks||raw.items||[];
  var blocks=items.map(function(b){{ if(!b.id&&b.block_id)b.id=b.block_id; return b; }});
  var KINDS={kinds_json};
  var CH={chapter_json};
  blocks=blocks.filter(function(b){{
    if(KINDS.indexOf(b.kind)===-1) return false;
    var ch=(b.params&&b.params.chapter)||null;
    return CH ? (ch===CH) : true;
  }});
  window.__nblocks=blocks.length;
  window.renderChapter(blocks,{{containerId:"root",chapterTitle:{title_json}}});
  window.__done=true;
}} catch(e) {{ window.__err=String(e&&e.stack||e); }}
</script></body></html>
"""


def render_chapter_pdf(blocks_payload, out_pdf, chapter="adaptation",
                       kinds=None, title="Глава", timeout_ms=30000, scale=2):
    """Печать главы в PDF из ТОГО ЖЕ renderChapter(snapshot), что и HTML.

    Единый источник: PDF == HTML по построению (headless Chromium → page.pdf()).
    Возвращает путь к PDF.
    """
    from playwright.sync_api import sync_playwright

    out = Path(out_pdf)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent
    local_plotly = work / "plotly.min.js"
    local_render = work / "chapter_render.js"
    if not local_plotly.exists():
        shutil.copy(_PLOTLY_JS, local_plotly)
    shutil.copy(_RENDER_JS, local_render)

    html = _PDF_HTML_TMPL.format(
        plotly="plotly.min.js",
        render="chapter_render.js",
        blocks_json=json.dumps(blocks_payload, ensure_ascii=False, default=_json_serial),
        kinds_json=json.dumps(kinds or ADAPTATION_KINDS),
        chapter_json=json.dumps(chapter),
        title_json=json.dumps(title),
    )
    html_path = work / "_chapter_pdf.html"
    html_path.write_text(html, encoding="utf-8")

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(device_scale_factor=scale)
        pg = ctx.new_page()
        pg.goto(html_path.as_uri())
        try:
            pg.wait_for_function("window.__done===true || window.__err!==null",
                                 timeout=timeout_ms)
        except Exception:
            pass
        err = pg.evaluate("window.__err")
        if err:
            b.close()
            raise RuntimeError("renderChapter error: " + str(err))
        pg.wait_for_timeout(1500)
        pg.pdf(path=str(out), format="A4", print_background=True,
               margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"})
        b.close()
    return str(out)


def _fetch_blocks(base_url, well_id):
    import urllib.request
    url = "%s/api/customer-daily/blocks?well_id=%d" % (base_url.rstrip("/"), well_id)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--well", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--chapter", default="adaptation")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    a = ap.parse_args()
    payload = _fetch_blocks(a.base_url, a.well)
    mapping = render_chart_pngs(payload, a.out, chapter=a.chapter)
    print(json.dumps(mapping, ensure_ascii=False))


if __name__ == "__main__":
    main()
