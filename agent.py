"""Pushback agent: Gemini plans, Exa digs, every claim gets a cited verdict."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, AsyncIterator

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from exa_client import Exa, SourceRegistry, render_for_model

MODEL = os.environ.get("GEMINI_MODEL", "gemini-pro-latest")
FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-flash-latest")
FAST_MODEL = os.environ.get("GEMINI_FAST_MODEL", "gemini-flash-latest")  # chat, rewrites
TRIAGE_MODEL = os.environ.get("GEMINI_TRIAGE_MODEL", "gemini-flash-lite-latest")  # instant document X-ray
MAX_TURNS = 14

SYSTEM_PROMPT = """You are Pushback, a personal advocate for ordinary people who received a letter, bill, notice, email or decision that might be unfair: rent increases, deposit deductions, eviction notices, flight cancellations or delays, insurance or benefit denials, parking/traffic fines, surprise medical bills, utility or telecom charges, employer pay issues, debt-collection letters, subscription traps, and similar.

Your job is to take the task off the user's hands: find out what the CURRENT rules actually say for THEIR location, audit every claim in the document, work out what they are owed or can contest, and prepare everything they need so the only thing left is to approve and send.

How to work:
1. A fast pre-reader has already extracted the other party's claims with ids (C1, C2, …); they are listed in the user's message. Use those ids. Also identify the jurisdiction, the type of issue, every amount, and every date or deadline.
   The user is watching a live board of these claims. AS SOON AS you have enough evidence to judge a claim, call mark_claim for it (in the same turn as your next searches is fine) — don't save all verdicts for the end.
2. Research with the exa_search tool. Your training data is outdated; laws, caps, compensation amounts, benefit thresholds and deadlines change every year. Verify everything against live sources.
   - Run several searches in parallel when they are independent (e.g. the statute, the current-year cap/amount, the official complaint procedure, recent news on changes).
   - Prefer official sources (government, regulators, courts, legislation databases, ombudsmen). Use include_domains to target them when you know them.
   - Use start_published_date to find recent changes (e.g. this year's rent cap or a new regulation).
   - Search for how real people successfully contested the same thing (forums, legal aid guides, news) — this gives practical tactics.
   - Use exa_read_pages to read the full text of the key statute or official guidance page when highlights are not enough.
   - Also look for the other party's strongest legitimate argument, so your advice is honest.
   - Find the specific organisations who can help in that location (tenant union, legal aid, consumer ombudsman, regulator, enforcement body) with their real URLs.
3. Be efficient: typically 6–14 searches total. Stop researching once each claim is verified or clearly unverifiable.
4. Deadlines must be concrete: compute real YYYY-MM-DD dates from today's date and the document's dates — e.g. the reply-by date you set in the letter (normally 14 days from today), statutory time limits for claims or appeals, notice periods, payment due dates, and the last day to escalate to the regulator/tribunal. Search for the limitation period if you don't know it.
5. Finish by calling submit_case_file exactly once. Every claim verdict, amount, deadline, action and contact must cite source ids (like "S3") from your research. Never invent a source id, URL, law section, amount or deadline. If something could not be verified, say so and mark it unverifiable.

The reply letter must be ready to send: polite, firm, specific, quoting the exact law/rule (by name and section) and the amounts, with a clear request and a response deadline. Use placeholders like [Your name] only for details you genuinely don't have. Write it in the language the original document was written in (or English if unclear).

You are not a lawyer; where stakes are high or the situation is ambiguous, say so in caveats and point to the free help available. Do not be alarmist and do not overstate certainty."""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "exa_search",
        "description": (
            "Search the live web with Exa (meaning-based search engine). Returns titles, URLs, publish dates, "
            "the most relevant passages and an excerpt for each result, each tagged with a source id like [S4] "
            "you must use for citations. Describe what you need in natural language, e.g. "
            "'California AB 1482 maximum annual rent increase 2026 CPI calculation'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language description of the page you want to find."},
                "purpose": {"type": "string", "description": "One short phrase shown to the user explaining why you run this search, e.g. 'Check the legal rent cap'."},
                "include_domains": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Optional: restrict to these domains, e.g. ['hcd.ca.gov','leginfo.legislature.ca.gov']. Use [] for no restriction.",
                },
                "start_published_date": {
                    "type": "string",
                    "description": "Optional ISO date (YYYY-MM-DD). Only return pages published after it. Use '' for none.",
                },
                "category": {
                    "type": "string",
                    "enum": ["none", "news", "company", "research paper", "pdf", "personal site"],
                    "description": "Optional content category filter. 'none' for general web.",
                },
                "mode": {
                    "type": "string", "enum": ["fast", "auto", "deep"],
                    "description": "fast for simple lookups, auto by default, deep for hard-to-find facts.",
                },
            },
            "required": ["query", "purpose", "include_domains", "start_published_date", "category", "mode"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "exa_read_pages",
        "description": "Fetch the full, current text of up to 4 specific URLs (e.g. a statute or official guidance page found earlier). Use when highlights are not enough to be certain.",
        "input_schema": {
            "type": "object",
            "properties": {
                "urls": {"type": "array", "items": {"type": "string"}},
                "purpose": {"type": "string", "description": "Short phrase shown to the user."},
            },
            "required": ["urls", "purpose"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "mark_claim",
        "description": "Record your verdict on one pre-extracted claim the moment you have verified it. The user sees it stamped live. You may call it again later to revise.",
        "input_schema": {
            "type": "object",
            "properties": {
                "claim_id": {"type": "string", "description": "Claim id from the pre-reader, e.g. 'C2'."},
                "verdict": {"type": "string", "enum": ["valid", "questionable", "likely_invalid", "unverifiable"]},
                "headline": {"type": "string", "description": "Max 14 words, plain language, e.g. 'Cap is 8.8% here — they asked for 14.6%'."},
                "source_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["claim_id", "verdict", "headline", "source_ids"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "submit_case_file",
        "description": "Submit the final case file. Call exactly once, when research is complete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short case title, e.g. 'Rent increase of 12% — San Jose, CA'."},
                "jurisdiction": {"type": "string"},
                "category": {"type": "string", "enum": ["housing", "travel", "benefits", "consumer", "employment", "healthcare", "government", "debt", "other"]},
                "bottom_line": {"type": "string", "description": "2–3 plain-language sentences: what's going on and what the user should do. No jargon."},
                "verdict": {"type": "string", "enum": ["push_back", "negotiate", "comply", "get_help"], "description": "Overall recommendation."},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim_id": {"type": "string", "description": "Pre-reader id like 'C1', or '' for a claim you found yourself."},
                            "claim": {"type": "string", "description": "The other party's claim or demand, quoted or paraphrased from the document."},
                            "verdict": {"type": "string", "enum": ["valid", "questionable", "likely_invalid", "unverifiable"]},
                            "explanation": {"type": "string", "description": "Why, citing the rule. 1–3 sentences."},
                            "source_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["claim_id", "claim", "verdict", "explanation", "source_ids"],
                        "additionalProperties": False,
                    },
                },
                "money": {
                    "type": "object",
                    "properties": {
                        "at_stake": {"type": "string", "description": "A short money figure, MAX 6 WORDS, no sentences. The FULL amount the other party is demanding or withholding, e.g. '$4,200 / year' for a $350/month increase, '£850' kept from a deposit. '' if none."},
                        "potential_recovery": {"type": "string", "description": "A short money figure, MAX 6 WORDS, no sentences. What the user can realistically save or claim by pushing back, e.g. '€400 compensation + meal costs' or '$1,723 / year'."},
                        "explanation": {"type": "string"},
                        "source_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["at_stake", "potential_recovery", "explanation", "source_ids"],
                    "additionalProperties": False,
                },
                "opposing_view": {"type": "string", "description": "The other party's strongest legitimate argument and how to answer it."},
                "deadlines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "YYYY-MM-DD if computable, else ''."},
                            "label": {"type": "string"},
                            "source_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["date", "label", "source_ids"],
                        "additionalProperties": False,
                    },
                },
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "string", "description": "Short imperative, e.g. 'Send the dispute letter by email and certified mail'."},
                            "detail": {"type": "string"},
                            "source_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["step", "detail", "source_ids"],
                        "additionalProperties": False,
                    },
                },
                "contacts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "role": {"type": "string", "description": "What they can do for the user."},
                            "url": {"type": "string"},
                            "source_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["name", "role", "url", "source_ids"],
                        "additionalProperties": False,
                    },
                },
                "letter": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient name/email if known from the document, else ''."},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["to", "subject", "body"],
                    "additionalProperties": False,
                },
                "caveats": {"type": "string"},
            },
            "required": ["title", "jurisdiction", "category", "bottom_line", "verdict", "confidence", "claims", "money",
                         "opposing_view", "deadlines", "actions", "contacts", "letter", "caveats"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def _schema(s: Any) -> Any:
    """JSON schema → Gemini-friendly (drop keys Gemini's function schemas don't accept)."""
    if isinstance(s, dict):
        return {k: _schema(v) for k, v in s.items() if k not in ("additionalProperties", "strict")}
    if isinstance(s, list):
        return [_schema(x) for x in s]
    return s


def _decls(names: list[str]) -> list[types.Tool]:
    return [types.Tool(function_declarations=[
        types.FunctionDeclaration(name=t["name"], description=t["description"], parameters_json_schema=_schema(t["input_schema"]))
        for t in TOOLS if t["name"] in names
    ])]


CASE_TOOLS = _decls(["exa_search", "exa_read_pages", "mark_claim", "submit_case_file"])
CHAT_TOOLS = _decls(["exa_search", "exa_read_pages"])


# ----------------------------------------------------------------------------------------------
# Sessions: keep each finished case in memory so follow-up chat / rewrites can build on it.
# ----------------------------------------------------------------------------------------------
@dataclass
class Session:
    registry: SourceRegistry
    case: dict[str, Any]
    doc: str
    location: str
    chat: list[types.Content] = field(default_factory=list)


SESSIONS: "OrderedDict[str, Session]" = OrderedDict()


def save_session(s: Session) -> str:
    sid = uuid.uuid4().hex[:12]
    SESSIONS[sid] = s
    while len(SESSIONS) > 100:
        SESSIONS.popitem(last=False)
    return sid


def session_from_result(case: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    """Rebuild a session from a saved/replayed result event."""
    reg = SourceRegistry()
    for c in sources:
        reg.add({"url": c["url"], "title": c["title"], "publishedDate": c.get("published", ""),
                 "highlights": c.get("highlights", []), "text": " ".join(c.get("highlights", []))}, c.get("query", ""))
    return save_session(Session(registry=reg, case=case, doc="", location=case.get("jurisdiction", "")))


# ----------------------------------------------------------------------------------------------
# Gemini helpers
# ----------------------------------------------------------------------------------------------
def _client() -> genai.Client:
    return genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


async def stream_turn(client: genai.Client, models: list[str], contents: list[types.Content],
                      config: types.GenerateContentConfig) -> AsyncIterator[tuple[str, Any]]:
    """Stream one model turn. Yields ('thought', str), ('text', str) and finally ('end', parts).
    Retries / falls back to the next model on rate limits, as long as nothing has streamed yet."""
    last: Exception | None = None
    for model in dict.fromkeys(m for m in models if m):
        for attempt in range(3):
            parts: list[types.Part] = []
            try:
                stream = await client.aio.models.generate_content_stream(model=model, contents=contents, config=config)
                async for chunk in stream:
                    cand = chunk.candidates[0] if chunk.candidates else None
                    if not cand or not cand.content or not cand.content.parts:
                        continue
                    for p in cand.content.parts:
                        parts.append(p)
                        if p.text and p.thought:
                            yield "thought", p.text
                        elif p.text:
                            yield "text", p.text
                yield "end", parts
                return
            except genai_errors.APIError as e:
                last = e
                if parts or e.code not in (429, 500, 503, 504):
                    raise
                await asyncio.sleep(2 * (attempt + 1))
    raise last  # type: ignore[misc]


TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "in_scope": {
            "type": "boolean",
            "description": ("true if the input is (or describes) a letter, email, bill, notice, charge, fine, decision, contract term or "
                            "dispute the user may want to check or contest: rent, deposits, eviction, travel disruption, insurance, "
                            "benefits decisions, medical bills, fines, employment pay, debt collection, subscriptions, refunds, etc. "
                            "false for unrelated requests (recommendations, planning, trivia, coding, small talk)."),
        },
        "doc_text": {"type": "string", "description": "If a file/photo was attached: a faithful verbatim transcription of it. Otherwise ''."},
        "issue": {"type": "string", "description": "2–5 words, e.g. 'Rent increase notice'."},
        "jurisdiction": {"type": "string", "description": "Best guess, e.g. 'San Jose, California, USA'."},
        "other_party": {"type": "string"},
        "gist": {"type": "string", "description": "One sentence: what the other party wants from the user."},
        "amounts": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "value": {"type": "string"}}, "required": ["label", "value"]}},
        "dates": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "date": {"type": "string"}}, "required": ["label", "date"]}},
        "claims": {
            "type": "array",
            "description": "Every assertion, demand, or condition from the other party that could be checked against law/rules (3–7 items).",
            "items": {"type": "object", "properties": {
                "quote": {"type": "string", "description": "EXACT verbatim substring of the document (copy characters exactly, 3–25 words)."},
                "claim": {"type": "string", "description": "Plain-English paraphrase, max 15 words."},
            }, "required": ["quote", "claim"]},
        },
    },
    "required": ["in_scope", "doc_text", "issue", "jurisdiction", "other_party", "gist", "amounts", "dates", "claims"],
}


async def triage(client: genai.Client, text: str, location: str, file_b64: str | None, media_type: str | None) -> dict[str, Any]:
    parts: list[types.Part] = []
    if file_b64 and media_type:
        parts.append(types.Part.from_bytes(data=base64.b64decode(file_b64), mime_type=media_type))
    parts.append(types.Part.from_text(text=f"User location: {location or 'unknown'}\n<document>\n{text.strip() or '(see attached file)'}\n</document>\n"
                                             "Extract the structure of this document for a consumer-rights audit."))
    attempts = [  # lite answers in ~2s; flash without thinking (~5s) as a backup
        (TRIAGE_MODEL, types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=TRIAGE_SCHEMA)),
        (FAST_MODEL, types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=TRIAGE_SCHEMA,
                                                 thinking_config=types.ThinkingConfig(thinking_budget=0))),
    ]
    for model, cfg in attempts:
        try:
            r = await client.aio.models.generate_content(model=model, contents=[types.Content(role="user", parts=parts)], config=cfg)
            data = json.loads(r.text or "{}")
            for i, c in enumerate(data.get("claims") or [], 1):
                c["id"] = f"C{i}"
            return data
        except Exception:
            continue
    return {}


# ----------------------------------------------------------------------------------------------
# Main investigation
# ----------------------------------------------------------------------------------------------
@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    native_id: bool = False  # True when Gemini supplied the call id (echo it back)


def _calls_from(parts: list[types.Part], seq: list[int]) -> list[ToolCall]:
    out = []
    for p in parts:
        if p.function_call:
            seq[0] += 1
            fc = p.function_call
            out.append(ToolCall(id=fc.id or f"c{seq[0]}", name=fc.name, input=dict(fc.args or {}), native_id=bool(fc.id)))
    return out


def _fn_response(b: ToolCall, content: str, err: str | None) -> types.Part:
    return types.Part(function_response=types.FunctionResponse(
        id=b.id if b.native_id else None, name=b.name, response={"error": content} if err else {"result": content}))


def _research_announcements(calls: list[ToolCall]) -> list[dict[str, Any]]:
    evs = []
    for b in calls:
        inp = b.input
        if b.name == "exa_search":
            evs.append({"type": "search", "id": b.id, "query": inp.get("query", ""), "purpose": inp.get("purpose", ""),
                        "domains": inp.get("include_domains") or [], "since": inp.get("start_published_date") or "",
                        "category": inp.get("category", "none"), "mode": inp.get("mode", "auto")})
        elif b.name == "exa_read_pages":
            evs.append({"type": "read", "id": b.id, "urls": inp.get("urls", []), "purpose": inp.get("purpose", "")})
    return evs


async def run_case(
    text: str,
    location: str,
    file_b64: str | None = None,
    media_type: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run the agent and yield UI events as dicts."""
    client = _client()
    exa = Exa()
    registry = SourceRegistry()
    seq = [0]

    yield {"type": "status", "text": "Reading your document…"}
    tri = await triage(client, text, location, file_b64, media_type)

    # Not a letter/bill/dispute (e.g. "find me a restaurant"): say so politely instead of forcing a verdict.
    if tri and tri.get("in_scope") is False and not file_b64:
        await exa.aclose()
        yield {"type": "off_topic", "issue": tri.get("issue") or "",
               "text": "Pushback checks letters, bills, notices and decisions you think might be unfair. "
                       "This looks like a different kind of request. Paste the letter or describe the dispute and I'll investigate it."}
        return

    doc = (tri.get("doc_text") or "").strip() or text
    if tri:
        yield {"type": "triage", "triage": {**tri, "doc_text": doc}}

    claims_brief = "\n".join(f'{c["id"]}: "{c.get("quote", "")}" — {c.get("claim", "")}' for c in tri.get("claims") or [])
    user_parts: list[types.Part] = []
    if file_b64 and media_type:
        user_parts.append(types.Part.from_bytes(data=base64.b64decode(file_b64), mime_type=media_type))
    user_parts.append(types.Part.from_text(text=(
        f"Today's date: {date.today().isoformat()}\n"
        f"Where I live: {location or tri.get('jurisdiction') or 'not specified — infer from the document and state your assumption'}\n\n"
        f"Here is what I received / my situation:\n<document>\n{doc or '(see attached file)'}\n</document>\n\n"
        + (f"Pre-extracted claims (use these ids with mark_claim and in submit_case_file):\n{claims_brief}\n\n" if claims_brief else "")
        + "Investigate it, audit every claim against current rules, and prepare my case file and reply."
    )))
    contents: list[types.Content] = [types.Content(role="user", parts=user_parts)]
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=CASE_TOOLS,
        tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="AUTO")),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        thinking_config=types.ThinkingConfig(include_thoughts=True),
        max_output_tokens=32000,
    )
    yield {"type": "status", "text": "Planning the investigation…"}

    try:
        for turn in range(MAX_TURNS):
            if turn == MAX_TURNS - 3:
                contents.append(types.Content(role="user", parts=[types.Part.from_text(
                    text="You are almost out of research budget. Wrap up and call submit_case_file now with what you have verified.")]))

            parts: list[types.Part] = []
            async for kind, val in stream_turn(client, [MODEL, FALLBACK_MODEL], contents, config):
                if kind in ("thought", "text"):
                    yield {"type": "thought_delta", "text": val}
                else:
                    parts = val
            yield {"type": "thought_end"}

            if not parts:
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text="Continue. Use the tools, then call submit_case_file.")]))
                continue
            contents.append(types.Content(role="model", parts=parts))  # keeps thought signatures intact
            calls = _calls_from(parts, seq)

            if not calls:
                contents.append(types.Content(role="user", parts=[types.Part.from_text(
                    text="Please finish by calling the submit_case_file function with your findings.")]))
                continue

            final = next((c for c in calls if c.name == "submit_case_file"), None)
            if final is not None:
                case = _validate_citations(_normalize_case(final.input), registry)
                sid = save_session(Session(registry=registry, case=case, doc=doc, location=location))
                yield {
                    "type": "result", "case_id": sid, "case": case,
                    "sources": [s.card() for s in registry.order],
                    "stats": {"exa_calls": registry.calls, "exa_cost": round(registry.cost, 4), "sources": len(registry.order), "turns": turn + 1},
                }
                return

            # Live verdicts first — they're instant.
            valid_ids = {s.id for s in registry.order}
            responses: dict[str, types.Part] = {}
            for b in calls:
                if b.name == "mark_claim":
                    yield {"type": "claim_update", "claim_id": str(b.input.get("claim_id", "")),
                           "verdict": b.input.get("verdict", "unverifiable"), "headline": b.input.get("headline", ""),
                           "source_ids": [i for i in (b.input.get("source_ids") or []) if i in valid_ids]}
                    responses[b.id] = _fn_response(b, "Recorded and shown to the user.", None)

            research = [b for b in calls if b.name in ("exa_search", "exa_read_pages")]
            for ev in _research_announcements(research):
                yield ev
            results = await asyncio.gather(*(_exec_tool(exa, registry, b) for b in research))
            for b, (content, found, err) in zip(research, results):
                yield {"type": "found", "id": b.id, "sources": [s.card() for s in found], "error": err}
                responses[b.id] = _fn_response(b, content, err)
            for b in calls:
                if b.id not in responses:
                    responses[b.id] = _fn_response(b, f"Unknown tool {b.name}", "unknown tool")
            contents.append(types.Content(role="user", parts=[responses[b.id] for b in calls]))

        yield {"type": "error", "text": "Research ran out of turns before a case file was produced. Try adding more detail."}
    except genai_errors.APIError as e:
        msg = "Gemini is busy right now (rate limit). Please try again in a minute." if e.code == 429 else f"Gemini API error ({e.code}): {e.message}"
        yield {"type": "error", "text": msg}
    finally:
        await exa.aclose()


# ----------------------------------------------------------------------------------------------
# Follow-up chat (can research more) and letter rewrites
# ----------------------------------------------------------------------------------------------
CHAT_PROMPT = """You are Pushback, continuing to help the user with the case below. Answer their follow-up question directly, warmly and concisely (usually 60–180 words). Use short paragraphs or a few bullets.
Cite evidence inline as [S#] using the source ids listed. If you need facts you don't have (e.g. what happens at the tribunal, a form, a phone number, recent news), call exa_search first — never guess legal facts, prices, opening hours, amounts or deadlines.
Today's date: {today}. User location: {location}.

CASE FILE (JSON):
{case}

SOURCES ALREADY GATHERED:
{sources}"""


async def followup(case_id: str, message: str) -> AsyncIterator[dict[str, Any]]:
    s = SESSIONS.get(case_id)
    if s is None:
        yield {"type": "error", "text": "This case has expired. Run the investigation again."}
        return
    client = _client()
    exa = Exa()
    seq = [0]
    src_list = "\n".join(f"[{x.id}] {x.title} — {x.url}" + (f" — “{x.highlights[0][:200]}”" if x.highlights else "") for x in s.registry.order)
    config = types.GenerateContentConfig(
        system_instruction=CHAT_PROMPT.format(today=date.today().isoformat(), location=s.location or s.case.get("jurisdiction", ""),
                                              case=json.dumps(s.case, ensure_ascii=False)[:30000], sources=src_list[:20000]),
        tools=CHAT_TOOLS,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        thinking_config=types.ThinkingConfig(include_thoughts=False),
    )
    s.chat.append(types.Content(role="user", parts=[types.Part.from_text(text=message)]))
    try:
        for _ in range(5):
            parts: list[types.Part] = []
            async for kind, val in stream_turn(client, [FAST_MODEL, MODEL], s.chat, config):
                if kind == "text":
                    yield {"type": "token", "text": val}
                elif kind == "end":
                    parts = val
            if not parts:
                break
            s.chat.append(types.Content(role="model", parts=parts))
            calls = _calls_from(parts, seq)
            if not calls:
                break
            for ev in _research_announcements(calls):
                yield ev
            results = await asyncio.gather(*(_exec_tool(exa, s.registry, b) for b in calls))
            resp = []
            for b, (content, found, err) in zip(calls, results):
                yield {"type": "found", "id": b.id, "sources": [x.card() for x in found], "error": err}
                resp.append(_fn_response(b, content, err))
            s.chat.append(types.Content(role="user", parts=resp))
        yield {"type": "done", "sources": [x.card() for x in s.registry.order]}
    except genai_errors.APIError as e:
        yield {"type": "error", "text": f"Gemini API error ({e.code}): {e.message}"}
    finally:
        await exa.aclose()


REWRITE_PROMPT = """Rewrite the user's dispute letter according to the instruction. Keep every fact, amount, date, law/section reference and request unless the instruction says otherwise. Keep [placeholders]. Output ONLY the new letter body — no preamble, no subject line, no markdown."""


async def rewrite_letter(case_id: str, letter: str, instruction: str) -> AsyncIterator[dict[str, Any]]:
    s = SESSIONS.get(case_id)
    ctx = f"Case context: {s.case.get('title', '')} ({s.case.get('jurisdiction', '')}).\n" if s else ""
    client = _client()
    config = types.GenerateContentConfig(system_instruction=REWRITE_PROMPT,
                                         thinking_config=types.ThinkingConfig(thinking_budget=0))  # instant streaming
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=f"{ctx}Instruction: {instruction}\n\n<letter>\n{letter}\n</letter>")])]
    try:
        async for kind, val in stream_turn(client, [FAST_MODEL, FALLBACK_MODEL], contents, config):
            if kind == "text":
                yield {"type": "token", "text": val}
        yield {"type": "done"}
    except genai_errors.APIError as e:
        yield {"type": "error", "text": f"Gemini API error ({e.code}): {e.message}"}


def _normalize_case(c: dict[str, Any]) -> dict[str, Any]:
    """Gemini function args aren't schema-enforced, so fill any gaps the UI relies on."""
    c = dict(c)
    for k in ("title", "jurisdiction", "category", "bottom_line", "opposing_view", "caveats"):
        c[k] = str(c.get(k) or "")
    c["verdict"] = c.get("verdict") if c.get("verdict") in ("push_back", "negotiate", "comply", "get_help") else "get_help"
    c["confidence"] = c.get("confidence") if c.get("confidence") in ("high", "medium", "low") else "medium"
    for k in ("claims", "deadlines", "actions", "contacts"):
        c[k] = [x for x in (c.get(k) or []) if isinstance(x, dict)]
        for x in c[k]:
            x["source_ids"] = [str(i).strip("[] ") for i in (x.get("source_ids") or [])]
    for x in c["claims"]:
        x["claim_id"] = str(x.get("claim_id") or "")
        if x.get("verdict") not in ("valid", "questionable", "likely_invalid", "unverifiable"):
            x["verdict"] = "unverifiable"
    m = c.get("money") if isinstance(c.get("money"), dict) else {}
    c["money"] = {"at_stake": str(m.get("at_stake") or ""), "potential_recovery": str(m.get("potential_recovery") or ""),
                  "explanation": str(m.get("explanation") or ""), "source_ids": [str(i).strip("[] ") for i in (m.get("source_ids") or [])]}
    l = c.get("letter") if isinstance(c.get("letter"), dict) else {}
    c["letter"] = {"to": str(l.get("to") or ""), "subject": str(l.get("subject") or ""), "body": str(l.get("body") or "")}
    return c


async def _exec_tool(exa: Exa, registry: SourceRegistry, block: Any) -> tuple[str, list, str | None]:
    inp = block.input if isinstance(block.input, dict) else json.loads(block.input)
    try:
        if block.name == "exa_search":
            cat = inp.get("category")
            found = await exa.search(
                registry,
                inp["query"],
                mode=inp.get("mode") or "auto",
                include_domains=[d for d in (inp.get("include_domains") or []) if d] or None,
                start_published_date=(inp.get("start_published_date") or None),
                category=None if cat in (None, "", "none") else cat,
            )
            return render_for_model(found), found, None
        if block.name == "exa_read_pages":
            found = await exa.contents(registry, inp.get("urls") or [])
            return render_for_model(found, text_chars=7000), found, None
        return f"Unknown tool {block.name}", [], "unknown tool"
    except Exception as e:  # surface tool failures to the model so it can adapt
        msg = str(e)
        if hasattr(e, "response") and getattr(e, "response", None) is not None:
            try:
                msg = f"{e.response.status_code}: {e.response.text[:300]}"
            except Exception:
                pass
        return f"Tool error: {msg}. Adjust the request (e.g. drop filters) and try again.", [], msg


def _validate_citations(case: dict[str, Any], registry: SourceRegistry) -> dict[str, Any]:
    """Drop any source id the model cited that doesn't exist; flag sections with no evidence."""
    valid = {s.id for s in registry.order}

    def fix(obj: Any) -> Any:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k == "source_ids" and isinstance(v, list):
                    out[k] = [i for i in v if i in valid]
                    out["uncited"] = len(out[k]) == 0
                else:
                    out[k] = fix(v)
            return out
        if isinstance(obj, list):
            return [fix(x) for x in obj]
        return obj

    return fix(case)
