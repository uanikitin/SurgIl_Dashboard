#!/usr/bin/env bash
set -o errexit

# ── TinyTeX: install into project dir so it persists on Render ──
TINYTEX_DIR="/opt/render/project/src/.tinytex"

# Force reinstall if ragged2e is missing (added after initial install)
if [ -x "$TINYTEX_DIR/bin/x86_64-linux/xelatex" ] && ! kpsewhich ragged2e.sty >/dev/null 2>&1; then
    echo "==> ragged2e missing, reinstalling TinyTeX..."
    rm -rf "$TINYTEX_DIR"
fi

if [ ! -x "$TINYTEX_DIR/bin/x86_64-linux/xelatex" ]; then
    echo "==> Installing TinyTeX to $TINYTEX_DIR ..."
    rm -rf "$TINYTEX_DIR"

    # Download and install TinyTeX to HOME first (installer requires it)
    wget -qO- "https://yihui.org/tinytex/install-bin-unix.sh" | sh

    # Move from HOME to project dir
    mv "$HOME/.TinyTeX" "$TINYTEX_DIR"

    export PATH="$TINYTEX_DIR/bin/x86_64-linux:$PATH"

    # Install required LaTeX packages (|| true — non-critical format errors are OK)
    tlmgr install \
        collection-langcyrillic \
        collection-fontsrecommended \
        fontspec \
        geometry \
        fancyhdr \
        lastpage \
        multirow \
        array \
        tabularx \
        booktabs \
        longtable \
        xcolor \
        hyperref \
        caption \
        float \
        ragged2e \
        || true

    xelatex --version && echo "==> xelatex OK" || echo "==> WARNING: xelatex not working"
else
    echo "==> TinyTeX already installed at $TINYTEX_DIR, skipping"
    export PATH="$TINYTEX_DIR/bin/x86_64-linux:$PATH"
fi

# ── Liberation fonts (Times New Roman substitute for Linux) ──
FONT_DIR="$HOME/.fonts"
if [ ! -f "$FONT_DIR/LiberationSerif-Regular.ttf" ]; then
    echo "==> Installing Liberation fonts..."
    mkdir -p "$FONT_DIR"
    wget -qO /tmp/liberation.tar.gz \
        "https://github.com/liberationfonts/liberation-fonts/files/7261482/liberation-fonts-ttf-2.1.5.tar.gz"
    tar xzf /tmp/liberation.tar.gz -C /tmp
    cp /tmp/liberation-fonts-ttf-*/LiberationSerif*.ttf "$FONT_DIR/"
    cp /tmp/liberation-fonts-ttf-*/LiberationSans*.ttf "$FONT_DIR/"
    fc-cache -f "$FONT_DIR" 2>/dev/null || true
    rm -rf /tmp/liberation*
    echo "==> Liberation fonts installed"
else
    echo "==> Liberation fonts already installed, skipping"
fi

# ── Python dependencies ──
pip install --upgrade pip
pip install -r requirements.txt
