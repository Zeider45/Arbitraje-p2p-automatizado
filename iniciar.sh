#!/usr/bin/env bash
# Arranque sencillo en Linux o macOS: crea el entorno, instala dependencias y abre el panel.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if [ ! -d .venv ]; then
  echo "Creando entorno virtual..."
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if [ ! -f config.toml ] && [ "${1:-}" != "--demo" ]; then
  echo "No existe config.toml: copio config.example.toml (edítalo y vuelve a ejecutar)."
  cp config.example.toml config.toml
fi

exec python -m arbitraje "$@"
