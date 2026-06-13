#!/bin/zsh
set -e

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"

echo "Starting StoneAge Script Studio..."
echo "Project: $SCRIPT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found. Please install Python 3 first."
  read -k 1 "?Press any key to close..."
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating virtual environment..."
  /usr/bin/arch -arm64 python3 -m venv .venv
fi

PYTHON=".venv/bin/python"
PYTHON_RUN=(/usr/bin/arch -arm64 "$PYTHON")

if ! "${PYTHON_RUN[@]}" -c "import PySide6, rapidocr_onnxruntime" >/dev/null 2>&1; then
  echo "Installing dependencies..."
  "${PYTHON_RUN[@]}" -m pip install -r requirements.txt
fi

echo "Launching..."
"${PYTHON_RUN[@]}" -m stoneage_studio
