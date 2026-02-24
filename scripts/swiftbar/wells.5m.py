#!/usr/bin/env python3
# <bitbar.title>SurgIl Wells Monitor</bitbar.title>
# <bitbar.version>v2.0</bitbar.version>
# <bitbar.author>SurgIl</bitbar.author>
# <bitbar.author.github>volodymyrnikitin</bitbar.author.github>
# <bitbar.desc>Gas well pressure and flow rate monitor</bitbar.desc>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>

"""
SwiftBar plugin for SurgIl Dashboard.
Shows well pressure, delta P, and flow rate in macOS menu bar.

Config: ~/.config/surgil-widget/config.json
  {
    "enabled_statuses": ["Наблюдение", "Адаптация"],
    "dp_thresholds": {"43": 2.0, "48": 1.5},
    "dp_default": null
  }
"""

import base64
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

# ── Configuration ──────────────────────────────────────────
API_URL = os.environ.get("SURGIL_API_URL", "http://localhost:8000")
WIDGET_ENDPOINT = f"{API_URL}/api/widget/summary"
DASHBOARD_URL = os.environ.get("SURGIL_DASHBOARD_URL", API_URL)

CONFIG_DIR = Path.home() / ".config" / "surgil-widget"
CONFIG_PATH = CONFIG_DIR / "config.json"

SELF = os.path.abspath(__file__)

# SwiftBar font settings
FONT = "font=Menlo size=12"
FONT_SMALL = "font=Menlo size=11"
FONT_HEADER = "font=Menlo size=12"

# ── Status colors ─────────────────────────────────────────
STATUS_COLORS = {
    "Наблюдение":        "#2196F3",  # blue
    "Адаптация":         "#FF9800",  # orange
    "Оптимизация":       "#4CAF50",  # green
    "Простой":           "#F44336",  # red
    "КРС":               "#9C27B0",  # purple
    "Не обслуживается":  "#9E9E9E",  # gray
    "Освоение":          "#00BCD4",  # teal
    "Другое":            "#795548",  # brown
}
DEFAULT_STATUS_COLOR = "#888888"

# Short status labels for table column
STATUS_SHORT = {
    "Наблюдение":        "Набл",
    "Адаптация":         "Адап",
    "Оптимизация":       "Опт",
    "Простой":           "Прст",
    "КРС":               "КРС",
    "Не обслуживается":  "НеОб",
    "Освоение":          "Осв",
    "Другое":            "Друг",
}

# ΔP threshold presets (кгс/см²)
DP_PRESETS = [0.3, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0]

COLOR_RED = "color=#F44336"
COLOR_DIM = "color=#999999"


# ── Base64 helpers (avoid Cyrillic/space issues in params) ─
def b64e(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def b64d(s: str) -> str:
    return base64.urlsafe_b64decode(s.encode()).decode()


# ── Config management ─────────────────────────────────────
def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"enabled_statuses": [], "dp_thresholds": {}, "dp_default": None}


def save_config(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2))


# ── Action handlers ───────────────────────────────────────
def handle_action():
    """Process click actions from SwiftBar menu items."""
    action = sys.argv[1]

    if action == "toggle_status":
        status = b64d(sys.argv[2])
        config = load_config()
        enabled = config.get("enabled_statuses", [])
        if status in enabled:
            enabled.remove(status)
        else:
            enabled.append(status)
        config["enabled_statuses"] = enabled
        save_config(config)

    elif action == "reset_filter":
        config = load_config()
        config["enabled_statuses"] = []
        save_config(config)

    elif action == "set_dp":
        well_num = sys.argv[2]  # well number as string
        value = sys.argv[3]     # threshold value or "none"
        config = load_config()
        thresholds = config.get("dp_thresholds", {})
        if value == "none":
            thresholds.pop(well_num, None)
        else:
            thresholds[well_num] = float(value)
        config["dp_thresholds"] = thresholds
        save_config(config)


# ── Fetch data from API ───────────────────────────────────
def fetch_data() -> dict | None:
    try:
        req = urllib.request.Request(WIDGET_ENDPOINT, method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


# ── ANSI 24-bit color helpers ─────────────────────────────
def _ansi(hex_color: str) -> str:
    """Convert #RRGGBB to ANSI 24-bit foreground escape."""
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    return f"\033[38;2;{r};{g};{b}m"

ANSI_RESET = "\033[0m"
ANSI_GREEN = _ansi("#4CAF50")
ANSI_RED = _ansi("#F44336")
ANSI_DIM = _ansi("#999999")


# ── Formatting helpers ────────────────────────────────────
def fmt_val(val, width=5):
    if val is None:
        return "-".rjust(width)
    return f"{val:>{width}.1f}"


def status_color(status: str) -> str:
    return STATUS_COLORS.get(status, DEFAULT_STATUS_COLOR)


def build_well_line(num, p_tube, dp_val, dp_str, flow, short, threshold, status):
    """Build ANSI-colored well row.
    ΔP column: green/red by threshold (marker).
    All other columns: status color."""
    sc = _ansi(status_color(status))

    if dp_val is None and p_tube == "-".rjust(5):
        # No data at all — dim everything
        dim = ANSI_DIM
        return f"{dim}{num}  {p_tube}  {dp_str}  {flow}  {short}{ANSI_RESET}"

    # ΔP color logic
    if threshold is not None and dp_val is not None:
        dp_c = ANSI_RED if dp_val <= threshold else ANSI_GREEN
    else:
        dp_c = sc  # no threshold — same as status

    return (
        f"{sc}{num}  {p_tube}  "
        f"{dp_c}{dp_str}"
        f"{sc}  {flow}  {short}{ANSI_RESET}"
    )


# ── Main output ───────────────────────────────────────────
def main():
    # Handle actions
    if len(sys.argv) > 1:
        handle_action()
        return

    config = load_config()
    enabled_statuses = config.get("enabled_statuses", [])
    dp_thresholds = config.get("dp_thresholds", {})

    data = fetch_data()

    if data is None:
        print("⛽ ✕ | color=red")
        print("---")
        print(f"Нет связи с сервером | {FONT_SMALL} {COLOR_RED}")
        print(f"{WIDGET_ENDPOINT} | {FONT_SMALL} {COLOR_DIM}")
        print("---")
        print("Обновить | refresh=true")
        return

    wells = data.get("wells", [])
    all_statuses = data.get("statuses", [])
    updated_at = data.get("updated_at", "")

    # Apply status filter
    if enabled_statuses:
        filtered = [w for w in wells if w["status"] in enabled_statuses]
    else:
        filtered = wells

    active_count = len(filtered)

    # ═══ Menu bar title ═══
    print(f"⛽ {active_count} скв | {FONT_SMALL}")

    # ═══ Dropdown ═══
    print("---")

    # ── Status filter ──
    filter_label = "все" if not enabled_statuses else f"{len(enabled_statuses)} выбр."
    print(f"Фильтр: {filter_label} | {FONT_HEADER} color=#888888")

    for status in all_statuses:
        checked = "checked=true" if status in enabled_statuses else ""
        count = sum(1 for w in wells if w["status"] == status)
        sc = status_color(status)
        encoded = b64e(status)
        print(
            f"-- ● {status} ({count}) | "
            f"bash={SELF} param1=toggle_status param2={encoded} "
            f"terminal=false refresh=true {checked} {FONT_SMALL} color={sc}"
        )

    if enabled_statuses:
        print("-- ---")
        print(
            f"-- Сбросить фильтр | "
            f"bash={SELF} param1=reset_filter param2=_ "
            f"terminal=false refresh=true {FONT_SMALL} {COLOR_DIM}"
        )

    print("---")

    # ── Table header ──
    print(f"{'№':>5}  {'Pтр':>5}  {'ΔP':>5}  {'Q':>5}  {'':>4} | {FONT_HEADER} color=#888888")

    # ── Well rows ──
    for w in filtered:
        well_num = str(w.get("number", "?"))
        num_str = well_num.rjust(5)
        p_tube_str = fmt_val(w.get("p_tube"))
        dp_val = w.get("dp")
        dp_str = fmt_val(dp_val)
        flow = fmt_val(w.get("flow_rate"))

        status = w.get("status", "")
        short = STATUS_SHORT.get(status, status[:4])

        threshold = dp_thresholds.get(well_num)

        line = build_well_line(
            num_str, p_tube_str, dp_val, dp_str, flow, f"{short:>4}",
            threshold, status,
        )

        well_url = f"{DASHBOARD_URL}/wells/{w['id']}"
        print(f"{line} | {FONT} ansi=true href={well_url}")

        # ── ΔP threshold submenu per well ──
        current_th = dp_thresholds.get(well_num)
        th_label = f"{current_th}" if current_th is not None else "—"
        print(f"-- Порог ΔP: {th_label} кгс/см² | {FONT_SMALL} color=#888888")

        for preset in DP_PRESETS:
            checked = "checked=true" if current_th == preset else ""
            print(
                f"---- {preset} | "
                f"bash={SELF} param1=set_dp param2={well_num} param3={preset} "
                f"terminal=false refresh=true {checked} {FONT_SMALL}"
            )

        # Option to remove threshold
        if current_th is not None:
            print("---- ---")
            print(
                f"---- Убрать порог | "
                f"bash={SELF} param1=set_dp param2={well_num} param3=none "
                f"terminal=false refresh=true {FONT_SMALL} {COLOR_DIM}"
            )

    if not filtered:
        print(f"  Нет скважин | {FONT_SMALL} {COLOR_DIM}")

    print("---")

    # ── Footer ──
    print(f"Обновлено: {updated_at} | {FONT_SMALL} {COLOR_DIM}")
    print(f"Открыть дашборд | href={DASHBOARD_URL}/visual {FONT_SMALL}")
    print(f"Обновить | refresh=true {FONT_SMALL}")


if __name__ == "__main__":
    main()
