#!/usr/bin/env bash
set -o errexit

# ── TinyTeX: install into project dir so it persists on Render ──
TINYTEX_DIR="/opt/render/project/src/.tinytex"

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
        || true

    xelatex --version && echo "==> xelatex OK" || echo "==> WARNING: xelatex not working"
else
    echo "==> TinyTeX already installed at $TINYTEX_DIR, skipping"
fi

# ── Python dependencies ──
pip install --upgrade pip
pip install -r requirements.txt
