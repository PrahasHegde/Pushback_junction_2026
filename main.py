"""Pushback web server. Run: python main.py  -> http://localhost:8899"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import uvicorn  # noqa: E402
from fastapi import Body, FastAPI, File, Form, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from agent import followup, rewrite_letter, run_case, session_from_result  # noqa: E402

ROOT = Path(__file__).parent
RUNS = ROOT / "runs"
app = FastAPI(title="Pushback")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

ALLOWED = {"image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf"}


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
async def health():
    return {"exa": bool(os.environ.get("EXA_API_KEY")), "gemini": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))}


@app.post("/api/investigate")
async def investigate(
    text: str = Form(""),
    location: str = Form(""),
    file: UploadFile | None = File(None),
):
    file_b64 = media_type = None
    if file is not None and file.filename:
        if file.content_type not in ALLOWED:
            return JSONResponse({"error": f"Unsupported file type {file.content_type}"}, status_code=400)
        data = await file.read()
        if len(data) > 15 * 1024 * 1024:
            return JSONResponse({"error": "File too large (15MB max)"}, status_code=400)
        file_b64 = base64.standard_b64encode(data).decode()
        media_type = file.content_type
    if not text.strip() and not file_b64:
        return JSONResponse({"error": "Paste the letter or upload a photo/PDF of it."}, status_code=400)
    text, location = text[:30000], location[:200]  # keep prompts bounded even for huge pastes

    async def stream():
        log: list[tuple[float, dict]] = []
        t0 = time.monotonic()
        try:
            async for event in run_case(text, location, file_b64, media_type):
                log.append((round(time.monotonic() - t0, 2), event))
                yield json.dumps(event) + "\n"
        except Exception as e:  # last-resort: never leave the UI hanging
            yield json.dumps({"type": "error", "text": f"{type(e).__name__}: {e}"}) + "\n"
        if log and log[-1][1].get("type") == "result":
            RUNS.mkdir(exist_ok=True)
            name = time.strftime("%Y%m%d-%H%M%S") + ".ndjson"
            (RUNS / name).write_text("\n".join(json.dumps({"t": t, "e": e}) for t, e in log), encoding="utf-8")

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs")
async def runs():
    """Saved investigations, newest first (for offline demo replay)."""
    if not RUNS.exists():
        return []
    out = []
    for p in sorted(RUNS.glob("*.ndjson"), reverse=True):
        last = json.loads(p.read_text(encoding="utf-8").splitlines()[-1])["e"]
        out.append({"id": p.stem, "title": last.get("case", {}).get("title", p.stem)})
    return out


@app.post("/api/replay/{run_id}")
async def replay(run_id: str, speed: float = 4.0):
    """Re-stream a saved run with its original pacing (sped up), a backup for flaky venue Wi-Fi."""
    path = RUNS / f"{Path(run_id).name}.ndjson"
    if not path.exists():
        return JSONResponse({"error": "run not found"}, status_code=404)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    async def stream():
        prev = 0.0
        for row in rows:
            await asyncio.sleep(max(0.0, (row["t"] - prev) / max(speed, 0.1)))
            prev = row["t"]
            ev = row["e"]
            if ev.get("type") == "result":  # give the replayed case a live session so chat/rewrite work
                ev = {**ev, "case_id": session_from_result(ev["case"], ev.get("sources", []))}
            yield json.dumps(ev) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


def _ndjson(gen):
    async def stream():
        try:
            async for event in gen:
                yield json.dumps(event) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "text": f"{type(e).__name__}: {e}"}) + "\n"
    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/chat")
async def chat(case_id: str = Body(...), message: str = Body(...)):
    """Follow-up questions on a finished case; the agent may run more Exa searches."""
    return _ndjson(followup(case_id, message[:4000]))


@app.post("/api/rewrite")
async def rewrite(case_id: str = Body(""), letter: str = Body(...), instruction: str = Body(...)):
    """Stream a rewritten version of the reply letter (tone / length / language)."""
    return _ndjson(rewrite_letter(case_id, letter[:20000], instruction[:500]))


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8899)), reload=False)
