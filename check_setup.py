"""Pushback preflight check. Run before a demo:  python check_setup.py
Add --online to also verify both API keys with one tiny live request each."""
from __future__ import annotations

import importlib
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).parent
OK, WARN, FAIL = "  [ OK ]", "  [WARN]", "  [FAIL]"
problems = 0


def check(cond: bool, ok_msg: str, fail_msg: str, fatal: bool = True) -> bool:
    global problems
    if cond:
        print(f"{OK} {ok_msg}")
    else:
        print(f"{FAIL if fatal else WARN} {fail_msg}")
        problems += int(fatal)
    return cond


print("\nPushback setup check\n")

# 1. Python
check(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]}", f"Python 3.10+ required (found {sys.version.split()[0]})")

# 2. Dependencies
for mod, pkg in [("google.genai", "google-genai"), ("fastapi", "fastapi"), ("uvicorn", "uvicorn"),
                 ("httpx", "httpx"), ("dotenv", "python-dotenv"), ("multipart", "python-multipart")]:
    try:
        importlib.import_module(mod)
        check(True, f"{pkg} installed", "")
    except Exception:
        check(False, "", f"{pkg} missing. Run: pip install -r requirements.txt")

# 3. Files
for f in ["main.py", "agent.py", "exa_client.py", "static/index.html", "static/app.js", "static/style.css", "static/samples.js"]:
    check((ROOT / f).exists(), f"{f}", f"{f} is missing")

# 4. Keys
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass
check((ROOT / ".env").exists(), ".env file found", ".env not found. Copy .env.example to .env and add your keys")
gem = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
exa = os.environ.get("EXA_API_KEY")
check(bool(gem) and "your-" not in gem, "GEMINI_API_KEY set", "GEMINI_API_KEY missing or still the placeholder")
check(bool(exa) and "your-" not in exa, "EXA_API_KEY set", "EXA_API_KEY missing or still the placeholder")

# 5. Port
port = int(os.environ.get("PORT", 8899))
with socket.socket() as s:
    free = s.connect_ex(("127.0.0.1", port)) != 0
check(free, f"port {port} is free", f"port {port} is already in use (Pushback may already be running, or set PORT=xxxx)", fatal=False)

# 6. Saved runs for offline replay
runs = list((ROOT / "runs").glob("*.ndjson")) if (ROOT / "runs").exists() else []
check(bool(runs), f"{len(runs)} saved run(s) for offline replay (/?replay)", "no saved runs yet; offline replay will be empty", fatal=False)

# 7. Optional live key check
if "--online" in sys.argv and gem and exa:
    import httpx
    try:
        r = httpx.post("https://api.exa.ai/search", headers={"x-api-key": exa},
                       json={"query": "EU flight delay compensation rules", "numResults": 1}, timeout=30)
        check(r.status_code == 200, "Exa key works (live search OK)", f"Exa returned HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        check(False, "", f"Exa unreachable: {e}")
    try:
        from google import genai
        client = genai.Client(api_key=gem)   # keep a reference: a temporary client can be closed mid-request
        r = client.models.generate_content(model=os.environ.get("GEMINI_TRIAGE_MODEL", "gemini-flash-lite-latest"),
                                           contents="Reply with the single word: ok")
        check(bool(r.text), "Gemini key works (live call OK)", "Gemini returned an empty response")
    except Exception as e:
        check(False, "", f"Gemini call failed: {str(e)[:160]}")
elif "--online" not in sys.argv:
    print(f"{WARN} live API check skipped (run with --online to test both keys)")

print("\n" + ("All good. Start with:  python main.py   then open http://localhost:%d\n" % port if problems == 0
              else f"{problems} problem(s) found. Fix them above, then run this again.\n"))
sys.exit(1 if problems else 0)
