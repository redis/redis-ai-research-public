#!/usr/bin/env bash
# Build HTML + PDF from talk-slides.md using Marp.
# Requires Node (npx). PDF export uses your local Chrome/Edge.
set -euo pipefail
cd "$(dirname "$0")"

MARP="npx -y @marp-team/marp-cli@latest"

$MARP talk-slides.md --html --theme-set rqe-dark-theme.css -o talk-slides.html
$MARP talk-slides.md --html --theme-set rqe-dark-theme.css --pdf --allow-local-files -o talk-slides.pdf

echo "Built talk-slides.html and talk-slides.pdf"
