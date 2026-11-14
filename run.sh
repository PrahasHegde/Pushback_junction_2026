#!/usr/bin/env sh
# One-click start for macOS / Linux: installs dependencies, checks setup, starts Pushback.
set -e
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Add your GEMINI_API_KEY and EXA_API_KEY to .env, then run this again."
  exit 1
fi
python3 -m pip install -q -r requirements.txt
python3 check_setup.py
PORT="${PORT:-8899}"
( sleep 2; (command -v open >/dev/null && open "http://localhost:$PORT") || (command -v xdg-open >/dev/null && xdg-open "http://localhost:$PORT") || true ) &
exec python3 main.py
