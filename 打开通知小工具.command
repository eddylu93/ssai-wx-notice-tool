#!/bin/zsh
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  exec ".venv/bin/python" app.py
fi

exec /opt/homebrew/bin/python3.13 app.py
