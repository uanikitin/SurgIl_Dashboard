#!/usr/bin/env bash
set -o errexit

apt-get update && apt-get install -y --no-install-recommends texlive-xetex

pip install --upgrade pip
pip install -r requirements.txt
