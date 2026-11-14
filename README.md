# PUSHBACK
**Your letter says one thing. The law might say another.**

Paste (or photograph) the letter that feels wrong: a rent increase, a "no compensation" flight email, a surprise medical bill, a kept deposit, a fine, a benefits denial. Pushback checks every claim in it against the rules that apply **where you live, today**, shows the evidence for each verdict, and hands you a ready-to-send reply. You approve and send it yourself.

## Why it matters
Landlords, airlines and billing departments send these letters by the thousands. They often count on people not checking: "compensation is not applicable", "rent rises 18%, or move out", "balance due $3,180". The rules that would let you push back exist, but they are spread across statutes, regulators' pages, this year's index numbers and forum threads. Few people spend the 3 hours it takes to check. Pushback does it in about 90 seconds.

**Who it's for:** renters, travellers, patients and anyone dealing with a bureaucracy, especially people who can't afford a lawyer for a €400 problem.

## What makes it different
| | |
|---|---|
| **Adversarial audit, not a chatbot** | Pushback reads the other party's document and gives every claim a verdict: *Valid / Questionable / Likely invalid / Unverified*. |
| **Every verdict has evidence** | Each claim, amount, deadline and action carries `S#` citation chips. Hover to see the quoted passage; click to jump to the evidence locker. Any source ID the model invents is removed on the server, and uncited statements are flagged ⚠. |
| **Live law, not training data** | Exa searches filter for recent changes (`startPublishedDate`), target official domains (`includeDomains`), use `deep` mode for hard facts and `/contents` for the full statute text. Official government sources get a green badge. |
| **Honest by design** | A "Their best argument" section makes the agent research the other side's strongest case. |
| **Task done, not just answered** | You get a reply letter in the document's own language that cites the law, a calendar file with deadline reminders, a checklist, and free local help (tenant unions, ombudsmen, legal aid) with real links. |
| **Human approval** | Nothing is sent automatically. You edit the letter, then click **Approve & open in email**. |
| **Photo in, case out** | Snap the letter and upload it (image or PDF). Gemini reads it directly (multimodal). |

## Built to respond while it works
| When | What you see |
|---|---|
| **~2s** | **Document X-ray**: Gemini Flash-Lite pulls out every claim, amount and date, and highlights each claim in the original text. |
| **~7s** | The agent's **reasoning streams live** into the investigation feed. |
| **~20–35s** | **Verdict stamps arrive one by one** as each claim is verified (`mark_claim` tool). Illegal clauses are struck through in the document. |
| **~50–75s** | The full case file: the verdict stamp, money at stake counting up, deadlines, game plan and reply letter. |
| After | **Follow-up chat** that streams answers and runs new Exa searches when it needs to (new sources join the evidence locker). **One-click letter rewrites**: Firmer, Friendlier, Shorter, English, Local language, or your own instruction. **Voice input** 🎙️ and **Ctrl+Enter**. |

## How it uses Exa
- `POST /search` with `type: auto|fast|deep`, `includeDomains` (official sources), `startPublishedDate` (this year's caps and amounts), `category` (news for recent changes), and `contents.highlights` scoped to the query, so every source arrives with quotable evidence.
- `POST /contents` with `livecrawl` to read the full current text of the key statute or guidance page.
- Gemini runs several Exa searches in parallel each turn (typically 4–10 in total). The live investigation feed shows each query, its filters and the domains it hit.

## Run it
```bash
pip install -r requirements.txt
cp .env.example .env        # add GEMINI_API_KEY (Google AI Studio) and EXA_API_KEY
python main.py              # http://localhost:8899
```
Set `PORT` to change the port.

**Demo backup:** each finished investigation is saved to `runs/`. Open `http://localhost:8899/?replay` to replay saved runs with their original pacing (sped up) if the venue Wi-Fi fails.

## Demo script (3 min, reactive version)
1. Click the **14.6% rent increase** card (San Jose), then Investigate. At about 2s, point at the X-ray: "it has already found the 4 claims in the letter."
2. While the stamps arrive: "watch them get struck through. That's live California law from Exa, not training data." Hover a citation to show the quote.
3. Show the verdict and the money. Click **✂️ Shorter**, then **🌐 Local language**, and watch the letter rewrite itself.
4. Ask the chat: *"Can they evict me for disputing this?"* It researches live and answers with sources.
5. Hand the keyboard to a judge for their own case.

## Original demo script
1. **Hook (20s):** "Who here has gotten a letter that felt like a scam, but it was legal-sounding enough that you just paid?"
2. **Live (90s):** click **✈️ Cancelled flight, €50 voucher**, then Investigate. Narrate the live feed: it's finding the EU 261/2004 rules and the Finnish consumer authority's page, and checking recent court rulings. Result: *"Compensation not applicable" → Likely invalid. You're owed €400, not a €50 voucher.* Hover a citation to show the quote. Open the letter, then click Approve.
3. **Judges' turn (40s):** ask a judge for their own case, such as a real bill, a parking ticket or their city, and run it live.
4. **Close (20s):** "Every verdict cited, every source live, and you stay in charge of what gets sent."

## Architecture
```
static/ (vanilla JS UI) ──NDJSON stream──▶ main.py (FastAPI)
                                              │
                                     agent.py: Gemini function-calling loop
                                       ├─ exa_search      ─┐
                                       ├─ exa_read_pages  ─┼─▶ exa_client.py → Exa REST API
                                       └─ submit_case_file (strict JSON schema)
                                              │
                         citation validator (drops invented IDs, flags uncited claims)
```
- **Model:** `gemini-pro-latest` (override with `GEMINI_MODEL`), thought summaries stream into the live feed, parallel function calls, automatic retry + fallback to `gemini-flash-latest` on rate limits (`GEMINI_FALLBACK_MODEL`). Function arguments are normalised server-side before rendering.
- Every Exa result goes into a per-case `SourceRegistry` and gets a stable ID (`S1…Sn`). That ID is the only way the model can cite a source.

*Pushback is not a lawyer and says so. For high-stakes cases it points you to free professional help.*
