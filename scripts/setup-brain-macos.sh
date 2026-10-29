#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python_bin=${COFLORIST_PYTHON:-python3.13}
node_bin=${COFLORIST_NODE:-/opt/homebrew/opt/node@24/bin/node}
if [ ! -x "$node_bin" ]; then node_bin=node; fi
"$python_bin" -m venv .venv
if command -v uv >/dev/null 2>&1; then
  uv pip install --python .venv/bin/python -r python/requirements.txt
else
  .venv/bin/python -m pip install -r python/requirements.txt
fi
.venv/bin/python python/prepare_data.py
"$node_bin" python/test-brain-worker.mjs
