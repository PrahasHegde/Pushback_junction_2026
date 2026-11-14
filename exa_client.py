"""Thin async wrapper around the Exa REST API (search + contents).

Every result that comes back is registered in a per-case SourceRegistry and gets a
stable id like S3, so the agent can cite it and the UI can render the evidence.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

EXA_BASE = "https://api.exa.ai"


@dataclass
class Source:
    id: str
    url: str
    title: str
    published: str
    highlights: list[str]
    text: str
    query: str
    domain: str
    official: bool

    def card(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "published": self.published,
            "highlights": self.highlights[:3],
            "domain": self.domain,
            "official": self.official,
            "query": self.query,
        }


# Domain labels that mark government sites anywhere in the hostname (gov.uk, ca.gov, service-public.gouv.fr …)
OFFICIAL_LABELS = {"gov", "gob", "gouv", "govt", "gv", "mil", "gc", "europa", "admin", "bund", "overheid", "regjeringen"}
# Known official / authoritative hosts and fragments
OFFICIAL_HINTS = ("finlex.fi", "suomi.fi", "kela.fi", "vero.fi", "kkv.fi", "oikeus.fi", "ym.fi", "law.cornell.edu",
                  "courts.", "judiciary", "ombudsman", "legislation.", "europa.eu", "service-public.fr",
                  "consumerfinance.gov", "citizensadvice.org.uk", "shelter.org.uk", "nhs.uk")


def _is_official(domain: str) -> bool:
    d = domain.lower()
    return bool(OFFICIAL_LABELS & set(d.split("."))) or any(h in d for h in OFFICIAL_HINTS)


@dataclass
class SourceRegistry:
    by_url: dict[str, Source] = field(default_factory=dict)
    order: list[Source] = field(default_factory=list)
    cost: float = 0.0
    calls: int = 0

    def add(self, r: dict[str, Any], query: str) -> Source:
        url = r.get("url") or r.get("id") or ""
        if url in self.by_url:
            src = self.by_url[url]
            # merge in any fresh text/highlights
            if r.get("text") and len(r["text"]) > len(src.text):
                src.text = r["text"]
            for h in r.get("highlights") or []:
                if h not in src.highlights:
                    src.highlights.append(h)
            return src
        domain = urlparse(url).netloc.replace("www.", "")
        src = Source(
            id=f"S{len(self.order) + 1}",
            url=url,
            title=(r.get("title") or domain or url).strip(),
            published=(r.get("publishedDate") or "")[:10],
            highlights=[h.strip() for h in (r.get("highlights") or []) if h and h.strip()],
            text=(r.get("text") or "").strip(),
            query=query,
            domain=domain,
            official=_is_official(domain),
        )
        self.by_url[url] = src
        self.order.append(src)
        return src

    def get(self, sid: str) -> Source | None:
        return next((s for s in self.order if s.id == sid), None)


class Exa:
    def __init__(self, api_key: str | None = None):
        key = api_key or os.environ.get("EXA_API_KEY")
        if not key:
            raise RuntimeError("EXA_API_KEY is not set")
        self.http = httpx.AsyncClient(
            base_url=EXA_BASE,
            headers={"x-api-key": key, "content-type": "application/json"},
            timeout=60.0,
        )

    async def search(
        self,
        registry: SourceRegistry,
        query: str,
        *,
        mode: str = "auto",
        num_results: int = 6,
        include_domains: list[str] | None = None,
        start_published_date: str | None = None,
        category: str | None = None,
    ) -> list[Source]:
        body: dict[str, Any] = {
            "query": query,
            "type": mode if mode in ("auto", "fast", "deep", "neural", "keyword") else "auto",
            "numResults": max(1, min(num_results, 10)),
            "contents": {
                "text": {"maxCharacters": 2500},
                "highlights": {"numSentences": 3, "highlightsPerUrl": 3, "query": query},
            },
        }
        if include_domains:
            body["includeDomains"] = include_domains
        if start_published_date:
            body["startPublishedDate"] = start_published_date
        if category:
            body["category"] = category
        resp = await self.http.post("/search", json=body)
        resp.raise_for_status()
        data = resp.json()
        registry.calls += 1
        registry.cost += float((data.get("costDollars") or {}).get("total") or 0)
        return [registry.add(r, query) for r in data.get("results", [])]

    async def contents(self, registry: SourceRegistry, urls: list[str]) -> list[Source]:
        body = {
            "urls": urls[:5],
            "text": {"maxCharacters": 7000},
            "livecrawl": "fallback",
        }
        resp = await self.http.post("/contents", json=body)
        resp.raise_for_status()
        data = resp.json()
        registry.calls += 1
        registry.cost += float((data.get("costDollars") or {}).get("total") or 0)
        return [registry.add(r, "read: " + r.get("url", "")) for r in data.get("results", [])]

    async def aclose(self) -> None:
        await self.http.aclose()


def render_for_model(sources: list[Source], text_chars: int = 1800) -> str:
    """Compact, citation-friendly text block handed back to the model."""
    if not sources:
        return "No results. Try rephrasing, widening the date range, or dropping domain filters."
    out = []
    for s in sources:
        tag = " [OFFICIAL]" if s.official else ""
        lines = [f"[{s.id}]{tag} {s.title}", f"URL: {s.url}"]
        if s.published:
            lines.append(f"Published: {s.published}")
        if s.highlights:
            lines.append("Key passages:\n" + "\n".join(f"  - {h}" for h in s.highlights[:3]))
        if s.text:
            lines.append("Excerpt: " + s.text[:text_chars].replace("\n", " "))
        out.append("\n".join(lines))
    return "\n\n".join(out)
