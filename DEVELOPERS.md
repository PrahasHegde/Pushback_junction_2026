# Pushback: Developer's Guide

This guide explains how Pushback works end to end, where each piece lives, and how to change it safely. Read it top to bottom once. After that, use the **"How do I…"** section as a reference.

---

## 1. What the app does (in one paragraph)

A user pastes (or photographs) a letter they think is unfair: a rent increase, a cancelled flight, a medical bill. **Gemini** reads the letter and pulls out every claim the other party makes. **Exa** then searches the live web (laws, government pages, real cases) to check each claim. The browser shows the work as it happens: claims are highlighted in the document and stamped *Valid / Questionable / Likely invalid* as evidence comes in. At the end the user gets a structured **case file**: a verdict, the money at stake, deadlines, an action plan, contacts, a ready-to-send reply letter, and the sources behind every statement. They can then chat with the agent about the case or ask for the letter to be rewritten.

---

## 2. Quick start

```bash
pip install -r requirements.txt
copy .env.example .env          # then put your keys in .env
python main.py                  # → http://localhost:8899
```

`.env` needs:

| Variable | What it is |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio key (reasoning). |
| `EXA_API_KEY` | Exa key (web search). |
| `PORT` *(optional)* | Defaults to `8899`. |
| `GEMINI_MODEL` *(optional)* | Main investigator. Default `gemini-pro-latest`. |
| `GEMINI_FALLBACK_MODEL` *(optional)* | Used when the main model is rate-limited. Default `gemini-flash-latest`. |
| `GEMINI_FAST_MODEL` *(optional)* | Chat and letter rewrites. Default `gemini-flash-latest`. |
| `GEMINI_TRIAGE_MODEL` *(optional)* | The ~2-second first read. Default `gemini-flash-lite-latest`. |

Check it's alive: http://localhost:8899/api/health should return `{"exa": true, "gemini": true}`.

---

## 3. Project map

```
exa_agent/
├── main.py            Web server (FastAPI). HTTP endpoints, streaming, saved runs.
├── agent.py           The brain. Prompts, tool definitions, the agent loop, chat, rewrites.
├── exa_client.py      Talks to Exa. Turns every search result into a numbered source (S1, S2…).
├── static/
│   ├── index.html     Page skeleton: intake screen + case workspace (sections are empty shells).
│   ├── style.css      The whole design system (colors, layout, every component).
│   ├── app.js         All front-end logic: streaming, rendering, tabs, chat, rewrites.
│   └── samples.js     The demo cases shown on the home page.
├── runs/              Every finished investigation, saved for offline replay (auto-created).
├── requirements.txt
├── .env / .env.example
├── README.md          Pitch + demo script (for judges).
└── DEVELOPERS.md      This file.
```

There's **no build step and no database**. Python serves plain HTML/CSS/JS. Case state lives in memory on the server and in JS variables in the browser.

---

## 4. Architecture

```
 Browser (static/app.js)                     Server (main.py)                 agent.py                      External
 ───────────────────────                     ────────────────                 ────────                      ────────
 Intake form ──POST /api/investigate──────▶  investigate() ─────────────────▶ run_case()  ───────────────▶ Gemini (triage, Flash-Lite)
                                                                               │                            Gemini (investigator, Pro)
   ◀────────── NDJSON event stream ────────  streams each event as one line ◀──┤ yields events              Exa /search, /contents
   handle(ev) updates the UI live                                              │   (via exa_client.py)
                                             saves run → runs/*.ndjson         └ result → Session (memory)
 Ask tab ──────POST /api/chat─────────────▶  chat() ────────────────────────▶ followup()     ───────────▶ Gemini Flash + Exa
 Letter tab ───POST /api/rewrite──────────▶  rewrite() ─────────────────────▶ rewrite_letter() ────────▶ Gemini Flash
 ?replay ──────POST /api/replay/{id}──────▶  replay() re-streams a saved run (no API calls)
```

### Why NDJSON streaming?
Every endpoint that does AI work returns `application/x-ndjson`, meaning **one JSON object per line**. The server `yield`s events as they happen, and the browser reads the response body chunk by chunk ([app.js `streamNDJSON`](static/app.js#L57)), calling `handle(ev)` for each line. That's what makes the UI react live, and it needs no WebSockets.

---

## 5. The life of one investigation

Everything below happens inside [`run_case()` in agent.py](agent.py#L390). Read that function alongside this list.

1. **Triage (~2s).** [`triage()`](agent.py#L328) sends the document (text, image or PDF) to **Gemini Flash-Lite** with a strict JSON schema ([`TRIAGE_SCHEMA`](agent.py#L305)). It returns the issue type, jurisdiction, other party, amounts, dates, and the claims, each with an **exact quote** from the document. Claims get IDs `C1, C2, …`.
   → event `triage`. The browser draws the **Document X-ray** (highlights the quotes, shows "checking…" cards).

2. **Investigation loop (up to `MAX_TURNS = 14`).** The main model (Gemini Pro) gets the [`SYSTEM_PROMPT`](agent.py#L26), the document and the pre-extracted claims. On each turn:
   - The reply **streams** via [`stream_turn()`](agent.py#L275). Thought summaries → `thought_delta` events (the "reasoning" text that types itself out in the Research log).
   - The model can call **tools** (defined in the `TOOLS` list near the top of agent.py):

     | Tool | What happens | UI event(s) |
     |---|---|---|
     | `exa_search` | Exa search with optional domain / date / category filters | `search`, then `found` |
     | `exa_read_pages` | Fetch full page text for up to 4 URLs | `read`, then `found` |
     | `mark_claim` | Model records a verdict on one claim *as soon as it knows* | `claim_update` (the stamp animation) |
     | `submit_case_file` | Final structured case file. Ends the loop | `result` |

   - Research tools in one turn run **in parallel** (`asyncio.gather`), and their results go back to the model as function responses.
   - If the model stops without calling a tool, we nudge it ("call submit_case_file"). Three turns before the limit, we tell it to wrap up.

3. **Finishing.** The case file goes through two safety passes:
   - [`_normalize_case()`](agent.py#L574) fills any missing fields (Gemini doesn't strictly enforce schemas), so the UI never crashes.
   - [`_validate_citations()`](agent.py#L625) **removes any source ID the model made up** and marks sections with no evidence as `uncited`. This is the anti-hallucination guarantee.

4. **Session.** The finished case plus its sources are stored in memory ([`Session`](agent.py#L240), max 100, oldest dropped). The `case_id` goes to the browser so chat and rewrites can refer back to it.

5. **Saved run.** [`main.py investigate()`](main.py#L40) logs every event with a timestamp and writes `runs/<time>.ndjson` when a case completes. `/?replay` lists these and plays them back with the original timing (sped up 4×).

### Event reference (server → browser)

| `type` | Payload | Where the UI uses it |
|---|---|---|
| `status` | `text` | Research log line |
| `triage` | `triage` {doc_text, issue, jurisdiction, other_party, gist, amounts[], dates[], claims[]} | Case bar title, key facts, X-ray |
| `thought_delta` / `thought_end` | `text` | Live "reasoning" item in Research log |
| `search` | `id, query, purpose, domains, since, category, mode` | Research log item with spinner |
| `read` | `id, urls, purpose` | Research log item |
| `found` | `id, sources[], error` | Source chips, stats counters |
| `claim_update` | `claim_id, verdict, headline, source_ids` | Stamps a claim card + recolors its highlight |
| `result` | `case_id, case, sources[], stats` | Builds every tab and switches to Overview |
| `error` | `text` | Error box, status → Failed |
| `token` / `done` | *(chat & rewrite only)* | Streaming text |

---

## 6. Sources and citations (exa_client.py)

- [`Exa.search()`](exa_client.py#L105) calls `POST https://api.exa.ai/search` with `contents.text` (excerpt) and `contents.highlights` (the most relevant sentences for the query). [`Exa.contents()`](exa_client.py#L138) calls `/contents` with `livecrawl: "fallback"` for full pages.
- Every result is registered in a per-case [`SourceRegistry`](exa_client.py#L57), which gives it a **stable ID** (`S1`, `S2`…, deduplicated by URL) and tracks Exa cost.
- [`_is_official()`](exa_client.py#L51) flags government / regulator domains (`gov.uk`, `ca.gov`, `europa.eu`, `kkv.fi`…). These get the green "Official" badge.
- [`render_for_model()`](exa_client.py#L155) formats results for Gemini as `[S4] [OFFICIAL] Title / URL / key passages / excerpt`. The model can **only** cite these IDs, and the validator enforces that.

---

## 7. The front end (static/)

### Layout
[index.html](static/index.html) has two screens:
- **`#intake`**: hero, composer (textarea + attach + mic + location), sample-case cards, "how it works".
- **`#work`**: the case workspace:
  - **Case bar**: issue tag, title, status pill, live stats, progress bar.
  - **`#layout`** has two modes, switched by the CSS class `live`:
    - **`.live` (investigating):** no sidebar. Claims X-ray (left) and Research log (right, sticky) side by side.
    - **normal (case ready):** sidebar nav + one section (`.view`) at a time: Overview · Claims · Action plan · Reply letter · Ask · Evidence · Research log.

Sections are empty shells in HTML. JS fills them when the result arrives (`renderOverview`, `renderPlan`, `renderLetter`, `renderAsk`, `renderEvidence`).

### State (top of [app.js](static/app.js#L16))
| Variable | Holds |
|---|---|
| `SOURCES` | `{S1: card, …}` every source seen (investigation + chat) |
| `CITED` | Set of source IDs actually cited (drives the Evidence "Cited" filter) |
| `CLAIMS`, `ORDER` | Claim objects by ID, and their display order |
| `DOC` | The document text shown in the X-ray |
| `CASE_ID`, `CASE` | Server session ID and the final case file |

### Key functions
| Function | Job |
|---|---|
| [`runCase`](static/app.js#L68) | Resets the workspace, opens the stream, sends each event to `handle` |
| [`handle`](static/app.js#L122) | **The central switch.** One `case` per event type |
| [`renderTriage`](static/app.js#L184) / [`renderXray`](static/app.js#L207) | Facts and the highlighted document + claim cards |
| [`locate`](static/app.js#L198) | Finds a claim's quote in the document (exact → case-insensitive → whitespace-tolerant) |
| [`stampClaim`](static/app.js#L234) | Applies a live verdict (card swap + highlight color + log line) |
| [`renderCase`](static/app.js#L266) | Merges final verdicts, builds all tabs, leaves live mode, opens Overview |
| [`setTab`](static/app.js#L103) | Switches sections |
| [`rewrite`](static/app.js#L425) | Streams a rewritten letter into the textarea (with Undo) |
| [`ask`](static/app.js#L452) | Streams a chat answer; shows any live searches inside the bubble |
| [`cites`](static/app.js#L261) | Renders `S#` citation chips. Clicking one jumps to Evidence and flashes the card |

### Styling
[style.css](static/style.css) is organised by section headers (`Intake`, `Workspace`, `Overview`, `Claims / X-ray`, `Plan`, `Letter`, `Ask`, `Evidence`, `Research log`). All colors are CSS variables in `:root`, with a dark-mode override under `prefers-color-scheme: dark`. **Change colors only there.** The breakpoints are 1000px (sidebar becomes horizontal tabs) and 640px (phone).

---

## 8. How do I…

**…add a demo case?** Add an object to [static/samples.js](static/samples.js) with `label` (start with an emoji, a space, then the title), `location` and `text`. Use fictional company names.

**…change which model is used?** Set `GEMINI_MODEL` (or the others) in `.env` and restart. No code change needed.

**…make investigations faster or cheaper?** Lower `MAX_TURNS` in agent.py, set `GEMINI_MODEL=gemini-flash-latest`, or change "typically 6–14 searches" in `SYSTEM_PROMPT`.

**…change what the AI says or how it reasons?** Edit `SYSTEM_PROMPT` (investigation), `CHAT_PROMPT` (follow-up chat) or `REWRITE_PROMPT` (letter rewrites) in agent.py.

**…add a field to the case file** (e.g. "evidence to collect")?
1. Add it to the `submit_case_file` `input_schema` in `TOOLS` (agent.py), with a clear `description`.
2. Give it a default in `_normalize_case()`.
3. Render it in the right `render…()` function in app.js.

**…add a new tool for the agent** (e.g. a currency converter)?
1. Add a definition to `TOOLS` (name, description, JSON schema).
2. Add its name to `CASE_TOOLS` (and/or `CHAT_TOOLS`).
3. Handle it in `_exec_tool()`, which returns `(text_for_model, sources_list, error_or_None)`.
4. Optionally yield a UI event for it in `run_case()` and add a `case` in `handle()`.

**…add a new section/tab?** Add a `<button data-tab="x">` to `.sidenav` and a `<section class="view" data-panel="x">` in index.html, then fill it from `renderCase()` in app.js.

**…mark more domains as "official"?** Add to `OFFICIAL_LABELS` or `OFFICIAL_HINTS` in exa_client.py.

**…change the port?** `PORT=9000 python main.py`, or change the default at the bottom of main.py.

---

## 9. Testing and debugging

- **Health:** `GET /api/health`.
- **Watch the raw stream** (PowerShell):
  `curl.exe -N -X POST -F "text=My landlord kept my whole deposit" -F "location=London, UK" http://localhost:8899/api/investigate`
- **Replay without spending API credits:** http://localhost:8899/?replay. The dashed cards are saved runs. Great for working on UI: edit CSS/JS, refresh, replay. Static files are served fresh, so **no server restart is needed for front-end changes**. Python changes **do** need a restart.
- **Browser DevTools → Network → `investigate` → Response** shows every event line.
- **Common errors:**

  | Symptom | Cause / fix |
  |---|---|
  | `EXA_API_KEY is not set` | `.env` missing or not in the project folder |
  | `Gemini API error (429)` | Rate limited. It retries and falls back automatically; wait a moment if it persists |
  | `This case has expired` in chat | Server was restarted (sessions are in memory). Re-run, or use a replay |
  | Port already in use | Another app is on that port. Use `PORT=xxxx` |
  | Claim not highlighted in the X-ray | The model's quote didn't match the text exactly. The card still shows; `locate()` is the place to make matching fuzzier |

---

## 10. Known limits

- Sessions and chat history live **in memory**: a restart clears them, and they aren't shared between server processes.
- No accounts or auth. It's built to run locally for a demo.
- Legal accuracy depends on what Exa finds. The UI always shows sources and a "not a lawyer" note, and cases with missing evidence are marked `uncited` / *Unverified*.
- Voice input relies on the browser's Web Speech API (Chrome / Edge).
- The `mailto:` "Approve & open in email" button can truncate very long letters in some email clients. Use Copy instead.
