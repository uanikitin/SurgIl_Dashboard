"""Shared LaTeX utilities — xelatex binary discovery."""
from __future__ import annotations

import shutil
from pathlib import Path

_cached_path: str | None = None


def find_xelatex() -> str:
    """Find xelatex binary: system PATH, then TinyTeX user install."""
    global _cached_path
    if _cached_path:
        return _cached_path

    path = shutil.which("xelatex")
    if path:
        _cached_path = path
        return path

    # Search known TinyTeX / MacTeX locations
    candidates = [
        Path.home() / ".TinyTeX" / "bin" / "x86_64-linux" / "xelatex",
        Path("/opt/render/project/src/.tinytex/bin/x86_64-linux/xelatex"),
        # macOS MacTeX
        Path("/Library/TeX/texbin/xelatex"),
        Path("/usr/local/texlive/2024/bin/universal-darwin/xelatex"),
        Path("/usr/local/texlive/2025/bin/universal-darwin/xelatex"),
        # macOS TinyTeX
        Path.home() / "Library" / "TinyTeX" / "bin" / "universal-darwin" / "xelatex",
    ]
    for tinytex in candidates:
        if tinytex.exists():
            _cached_path = str(tinytex)
            return _cached_path

    raise FileNotFoundError(
        "xelatex not found. Install texlive-xetex or TinyTeX."
    )
