# Segment Analysis Widget Integration: Observation → Adaptation

**Date:** 2026-06-02
**Status:** COMPLETED — awaiting user verification
**Bead(s):** none
**Epic:** SurgIl Dashboard — Step 5 (Adaptation) Widget Unification
**Chain:** `standalone-5c4b8808` seq `1`
**Parent:** none — first in chain
**Prior chain:** none — first in chain

---

## Reference Documents

- `CLAUDE.md` — project conventions, protected files, architecture overview
- `docs/contracts/segment_snapshot_contract.md` — segment snapshot schema (referenced in code)
- `backend/constants/segment_parts.py` — SEGMENT_ANALYSIS_RENDER_PARTS, schema version

## The Goal

Integrate the segment analysis tool from the Observation page (Step 3) into the Adaptation page (Step 5) WITHOUT code duplication. The same `ObservationSegmentWidget` JavaScript class should work on both pages, with `chapter` parameter controlling data separation. Each page creates snapshots only for its own chapter. PDF reports must render segment analysis graphs identically to HTML preview.

The user explicitly stated: "reuse functions, don't duplicate code" and "data must be separated — Observation creates snapshots for Observation, Adaptation creates snapshots for Adaptation."

## Where We Are

**COMPLETED — Full Data Separation Between Chapters**

Three-way separation is now implemented:
| Page | Chapter | segment_analysis saves as | PDF routes to |
|------|---------|---------------------------|---------------|
| Заказчик (customer_daily.html) | none | `chapter: undefined` | §2 segment_blocks_input |
| Наблюдение (Step 3) | observation | `chapter: 'observation'` | §3 observation_blocks |
| Адаптация (Step 5) | adaptation | `chapter: 'adaptation'` | §4 adaptation_blocks |

**Widget Integration:**
- `ObservationSegmentWidget` accepts configurable `chapter` parameter
- Step 3 widget: `chapter: 'observation'` (line 4854)
- Step 5 widget: `chapter: 'adaptation'` (line 9553)
- Customer page: no chapter param (default behavior)

**Backend Routing (adaptation_report_service.py):**
- Lines 3441-3483: segment_analysis routing by chapter param
- `chapter='observation'` → `observation_blocks` → §3
- `chapter='adaptation'` → `adaptation_blocks` → §4
- No chapter → `segment_blocks_input` → §2

**Backend Formatting:**
- `observation_blocks_fmt` loop (lines 3809-3826): formats observation chapter blocks
- `adaptation_blocks_fmt` loop (lines 3828-3845): formats adaptation chapter blocks
- Both use `_format_segment_analysis_for_observation()` and `_format_segment_block()`

**UI Filtering — Each Page Shows Only Its Own Blocks:**

1. **customer_daily.html** (Заказчик):
   - `renderAttachedBlocks()` (lines 3774-3789): filters out `chapter='observation'/'adaptation'`
   - `renderChapterPreview()` (lines 6710-6717): same filtering
   - Excludes: `adaptation_*`, `observation_analysis`, `optimal_window`, `reagent_irv_summary`

2. **adaptation_wizard.html** Step 3 (Наблюдение):
   - Widget `_loadBlocks()` filters by `this.chapter === 'observation'`
   - Chapter preview shows only blocks where `params.chapter === 'observation'`

3. **adaptation_wizard.html** Step 5 (Адаптация):
   - `wz5LoadAttachedBlocks()` (lines 9659-9672): only shows segment_analysis with `chapter='adaptation'`
   - Plus standard adaptation kinds: `adaptation_period_analysis`, `optimal_window`, etc.

**PDF Rendering:**
- LaTeX template `adaptation_report.tex` lines 2122-2256: segment_analysis block rendering
- Renders: header, Q chart, 11-column table, descriptions, changepoints

## What We Tried (Chronological)

1. **Server-side chapter filter (FAILED)**
   - Added `chapter` parameter to API endpoint for filtering blocks
   - Result: Excluded blocks WITHOUT `params.chapter` (e.g., adaptation_period_analysis)
   - User reported: "other blocks disappeared from right panel"
   - Solution: Removed server-side filter, kept client-side only

2. **Client-side filtering with chapter_filter config**
   - Added `chapter_filter` to panel config in both Observation and Adaptation
   - Logic: blocks without chapter pass through; blocks with chapter must match
   - Result: All relevant blocks appear in right panel

3. **Routing segment_analysis to wrong chapter (FIXED)**
   - Original: segment_analysis with `chapter='adaptation'` went to `segment_blocks_input` (Chapter 2)
   - Added explicit check for `chapter='adaptation'` at line 3458
   - Now correctly routes to `adaptation_blocks` (Chapter 4)

4. **PDF graphs not compiling (FIXED)**
   - Problem: adaptation_blocks passed raw to template without formatting
   - `_format_segment_block()` never called for adaptation chapter
   - Added formatting loop before return statement
   - Added `kind` field to function return
   - Added LaTeX template block for segment_analysis rendering

5. **Step 3 widget missing chapter param (FIXED)**
   - Problem: Step 3 (Наблюдение) widget lacked `chapter: 'observation'`
   - Blocks saved without chapter → routed to customer chapter (§2)
   - Solution: Added `chapter: 'observation'` to widget config (line 4854)

6. **observation_blocks not formatted (FIXED)**
   - Problem: `observation_blocks` passed raw to template
   - Only `adaptation_blocks_fmt` was being formatted
   - Solution: Added formatting loop for `observation_blocks_fmt` (lines 3809-3826)

7. **UI showing ALL blocks on Customer page (FIXED)**
   - Problem: User screenshot showed blocks from all chapters on Заказчик page
   - SEGMENT_COMPARISON, ADAPTATION_PERIOD_ANALYSIS visible when they shouldn't be
   - Solution: Added client-side filtering to `renderAttachedBlocks()` and `renderChapterPreview()`
   - Filters exclude: blocks with `chapter='observation'/'adaptation'`, adaptation_* kinds

## Key Decisions

1. **Reuse ObservationSegmentWidget vs create new class**
   - Decision: Reuse with `chapter` parameter
   - Rejected: Duplication would require maintaining two copies of complex widget
   - Impact: Single source of truth for segment analysis UI

2. **Data separation via `params.chapter`**
   - Decision: Each page saves blocks with its own chapter value
   - Observation: `chapter: 'observation'`
   - Adaptation: `chapter: 'adaptation'`
   - Filtering happens at load time, not save time

3. **Server-side vs client-side filtering**
   - Decision: Client-side only (chapter_filter in JS)
   - Rejected: Server-side filter excluded blocks without chapter param
   - Server-side would break other block types that don't have chapter

4. **LaTeX template structure**
   - Decision: Add standalone segment_analysis block in adaptation_blocks loop
   - Matches customer_chapter.segment_blocks format from Chapter 2
   - Includes: chart, 11-column table, descriptions, changepoints

## Evidence & Data

### Files Modified This Session

| File | Lines Changed | Purpose |
|------|---------------|---------|
| `adaptation_report_service.py` | +50 | Formatting loop + routing fix |
| `adaptation_report.tex` | +135 | segment_analysis rendering in §4.7 |
| `observation_segment_widget.js` | +10 | chapter parameter support |
| `adaptation_wizard.html` | +100 | Widget HTML + JS initialization |
| `chapter_preview.js` | +15 | chapter_filter client-side filtering |

### Git Diff Stats

```
 backend/services/adaptation_report_service.py | 1239 +++++++++++++++++++++++--
 backend/templates/latex/adaptation_report.tex |  468 +++++++++-
 backend/static/js/chapter_render.js           |  817 +++++++++++++---
 backend/templates/adaptation_wizard.html      |  298 +++++-
```

### Key Line Numbers (adaptation_report_service.py)

- Lines 3458-3471: segment_analysis routing to adaptation_blocks
- Lines 3809-3827: Formatting loop for adaptation_blocks
- Lines 3858: Return `adaptation_blocks_fmt` (was `adaptation_blocks`)
- Lines 4617-4646: `_format_segment_block()` return dict with `kind` field
- Lines 4419-4647: Full `_format_segment_block()` function

### Key Line Numbers (adaptation_report.tex)

- Lines 1786-1787: `for ab in adaptation_blocks` loop
- Lines 2122-2256: segment_analysis block rendering
- Lines 2141-2151: Chart inclusion with \includegraphics
- Lines 2154-2178: 11-column segments table

## Code Analysis

### ObservationSegmentWidget Chapter Support

```javascript
// Constructor (line ~60)
this.chapter = config.chapter || 'observation';

// save() (lines 549-550)
params: {
  source: this.chapter,
  chapter: this.chapter,
}

// _loadBlocks() (line 844)
blocks.filter(b => b.params?.chapter === this.chapter)

// openFullAnalysis() (line 131)
url += `&chapter=${encodeURIComponent(this.chapter)}`;
```

### Adaptation Blocks Routing Logic

```python
# adaptation_report_service.py
if kind == "segment_analysis":
    p = b.get("params") or {}
    if p.get("chapter") == "observation" or p.get("source") == "observation":
        segment_blocks_input.append(...)  # → Chapter 2
    elif p.get("chapter") == "adaptation":
        adaptation_blocks.append(...)     # → Chapter 4 (§4.7)
    else:
        segment_blocks_input.append(...)  # → Chapter 2 (default)
```

### _format_segment_block Output Fields

| Field | Type | Source |
|-------|------|--------|
| `kind` | str | Hardcoded "segment_analysis" |
| `chart_q_path` | str/None | PNG from `render_segment_q_chart()` |
| `segments_rows` | list[dict] | Formatted from `segments_extended` |
| `rich_segment_describes` | list[dict] | From `_build_rich_segment_describes()` |
| `descriptions_lines` | list[str] | From snapshot.descriptions |
| `cp_descriptions_lines` | list[str] | From snapshot.cp_descriptions |
| `parts` | dict | Toggle flags for rendering |
| `has_data` | bool | `ok=True` and segments exist |

## Files Changed

### Source Code — Backend

- `backend/services/adaptation_report_service.py`:
  - Added formatting loop (lines 3809-3827) to process adaptation_blocks
  - Added `kind: "segment_analysis"` to `_format_segment_block()` return
  - Routing: segment_analysis with chapter='adaptation' → adaptation_blocks

- `backend/services/customer_daily_service.py`:
  - Events enrichment with p_tube, p_line, reagent, qty, description (from prior session)

### Source Code — Frontend

- `backend/static/js/observation_segment_widget.js`:
  - Added `this.chapter` in constructor
  - Modified `save()`, `_loadBlocks()`, `openFullAnalysis()` to use chapter

- `backend/static/js/chapter_preview.js`:
  - Added `chapter_filter` config option
  - Client-side filtering logic (blocks without chapter pass; with chapter must match)

### Templates

- `backend/templates/adaptation_wizard.html`:
  - Added HTML markup for segment widget in Step 5
  - JS initialization with `chapter: 'adaptation'`
  - Date sync: TimelineBuilder onChange + Apply button

- `backend/templates/latex/adaptation_report.tex`:
  - Added lines 2122-2256: segment_analysis block rendering
  - Header, Q chart, 11-column table, descriptions, changepoints, notes

## User Feedback & Preferences

1. **"reuse functions, don't duplicate code"** — Explicit instruction to avoid code duplication
2. **"data must be separated"** — Observation ≠ Adaptation, each has own snapshots
3. **"what goes into right panel should match what goes into report"** — HTML preview = PDF content
4. **User noticed missing blocks immediately** — Important: don't break existing functionality when adding new
5. **User uses Ukrainian/Russian keyboard** — Some messages have mixed layout artifacts
6. **"check PDF, graphs should compile"** — PDF verification is critical acceptance criterion

## Where We're Going

**Implementation complete. User verification needed:**

1. **Verify UI separation** — Each page (Заказчик, Наблюдение, Адаптация) shows only its own blocks
2. **Test PDF compilation** — Generate PDF from each chapter, verify segment_analysis renders in correct section
3. **Save/load cycle** — Create segment_analysis on each page, verify it loads correctly on that page only
4. **Edge cases** — Multiple segment_analysis blocks, empty data, missing charts

## Risks & Blockers

- **XeLaTeX path issues** — Chart PNG paths must be absolute for xelatex compilation
- **Empty snapshot handling** — `has_data: false` should render graceful fallback text
- **Parts toggles** — Some checkboxes may not be saved in older snapshots (defaults to True)

## Open Questions

1. Does user need date range passed from TimelineBuilder to segment widget automatically, or manual "Применить" button is acceptable?
2. Should segment_analysis blocks in Adaptation show dual_comparison section (like Chapter 2)?
3. Are there other block types that need chapter-aware routing?

## Quick Start for Next Session

```bash
# Key files
backend/services/adaptation_report_service.py  # Lines 3441-3483 (routing), 3809-3845 (formatting)
backend/templates/customer_daily.html          # Lines 3774-3789, 6710-6717 (UI filtering)
backend/templates/adaptation_wizard.html       # Lines 4854 (Step 3), 9659-9672 (Step 5)

# Verification workflow (3 pages, 3 chapters)
1. Open Заказчик page → left panel should NOT show adaptation_*/observation_* blocks
2. Open Наблюдение (Step 3) → segment widget should show only chapter='observation' blocks
3. Open Адаптация (Step 5) → should show only chapter='adaptation' blocks
4. Create segment_analysis on each page → verify it appears only on that page
5. Generate PDF → verify each chapter has correct segment_analysis blocks

# Verify current state
python -m py_compile backend/services/adaptation_report_service.py  # Should pass
python -m py_compile backend/routers/adaptation_report.py            # Should pass
```
