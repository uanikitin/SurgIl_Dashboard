#!/usr/bin/env bash
set -o errexit

# ── TinyTeX: user-space TeX installation (no root required) ──
TINYTEX_DIR="$HOME/.TinyTeX"

if [ ! -d "$TINYTEX_DIR" ]; then
    echo "==> Installing TinyTeX..."
    wget -qO- "https://yihui.org/tinytex/install-bin-unix.sh" | sh

    # Add to PATH for tlmgr commands below
    export PATH="$TINYTEX_DIR/bin/x86_64-linux:$PATH"

    # Install required LaTeX packages for daily reports
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
        graphicx \
        xcolor \
        hyperref \
        caption \
        float
else
    echo "==> TinyTeX already installed, skipping"
fi

# ── Python dependencies ──
pip install --upgrade pip
pip install -r requirements.txt
