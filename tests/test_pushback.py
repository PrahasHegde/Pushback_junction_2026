"""Offline tests: no API keys or network needed.   Run:  python -m unittest discover -s tests -v"""
import asyncio
import io
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("EXA_API_KEY", "test-key")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import agent  # noqa: E402
import exa_client  # noqa: E402
from exa_client import SourceRegistry, _is_official, render_for_model  # noqa: E402


def collect(agen):
    async def run():
        return [e async for e in agen]
    return asyncio.run(run())


class SourcesTest(unittest.TestCase):
    def test_official_domains(self):
        for d in ["gov.uk", "hcd.ca.gov", "eur-lex.europa.eu", "kkv.fi", "service-public.fr", "oag.ca.gov"]:
            self.assertTrue(_is_official(d), d)
        for d in ["reddit.com", "govtrack.us", "medium.com", "nolo.com"]:
            self.assertFalse(_is_official(d), d)

    def test_registry_numbers_and_dedupes(self):
        reg = SourceRegistry()
        a = reg.add({"url": "https://www.gov.uk/x", "title": "A", "highlights": ["h1"]}, "q1")
        b = reg.add({"url": "https://example.com/y", "title": "B"}, "q2")
        a2 = reg.add({"url": "https://www.gov.uk/x", "title": "A", "highlights": ["h2"], "text": "longer text"}, "q3")
        self.assertEqual((a.id, b.id), ("S1", "S2"))
        self.assertIs(a, a2)
        self.assertEqual(a.highlights, ["h1", "h2"])
        self.assertTrue(a.official)
        self.assertFalse(b.official)
        self.assertEqual(len(reg.order), 2)

    def test_render_for_model_tags_sources(self):
        reg = SourceRegistry()
        s = reg.add({"url": "https://www.gov.uk/x", "title": "Deposit rules", "highlights": ["must be returned in 10 days"]}, "q")
        out = render_for_model([s])
        self.assertIn("[S1] [OFFICIAL] Deposit rules", out)
        self.assertIn("must be returned in 10 days", out)
        self.assertIn("No results", render_for_model([]))


class CaseFileTest(unittest.TestCase):
    def test_validate_citations_drops_invented_ids(self):
        reg = SourceRegistry()
        reg.add({"url": "https://a.gov/1"}, "q")
        case = {"claims": [{"claim": "x", "source_ids": ["S1", "S99"]}, {"claim": "y", "source_ids": ["S42"]}]}
        out = agent._validate_citations(case, reg)
        self.assertEqual(out["claims"][0]["source_ids"], ["S1"])
        self.assertFalse(out["claims"][0]["uncited"])
        self.assertEqual(out["claims"][1]["source_ids"], [])
        self.assertTrue(out["claims"][1]["uncited"])

    def test_normalize_case_fills_gaps(self):
        c = agent._normalize_case({"verdict": "nonsense", "claims": [{"claim": "a", "verdict": "??", "source_ids": ["[S1]"]}, "junk"]})
        self.assertEqual(c["verdict"], "get_help")
        self.assertEqual(c["confidence"], "medium")
        self.assertEqual(len(c["claims"]), 1)
        self.assertEqual(c["claims"][0]["verdict"], "unverifiable")
        self.assertEqual(c["claims"][0]["source_ids"], ["S1"])
        for k in ("money", "letter", "deadlines", "actions", "contacts", "title", "bottom_line"):
            self.assertIn(k, c)
        self.assertEqual(set(c["letter"]), {"to", "subject", "body"})


class OffTopicTest(unittest.TestCase):
    def test_off_topic_stops_before_research(self):
        async def fake_triage(*a, **k):
            return {"in_scope": False, "issue": "Restaurant request"}
        orig_triage, orig_client = agent.triage, agent._client
        agent.triage, agent._client = fake_triage, (lambda: object())
        try:
            events = collect(agent.run_case("Find me a vegan restaurant for Saturday", "Helsinki"))
        finally:
            agent.triage, agent._client = orig_triage, orig_client
        self.assertEqual(events[-1]["type"], "off_topic")
        self.assertFalse(any(e["type"] in ("search", "result") for e in events))


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        import main
        cls.main = main
        cls.client = TestClient(main.app)

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.json()), {"exa", "gemini"})

    def test_index_and_static(self):
        self.assertIn("PUSHBACK", self.client.get("/").text)
        for f in ("app.js", "style.css", "samples.js"):
            self.assertEqual(self.client.get(f"/static/{f}").status_code, 200, f)

    def test_investigate_requires_input(self):
        r = self.client.post("/api/investigate", data={"text": "  ", "location": ""})
        self.assertEqual(r.status_code, 400)

    def test_investigate_rejects_bad_file_type(self):
        r = self.client.post("/api/investigate", data={"text": ""},
                             files={"file": ("x.exe", io.BytesIO(b"MZ"), "application/octet-stream")})
        self.assertEqual(r.status_code, 400)

    def test_chat_unknown_case_is_graceful(self):
        r = self.client.post("/api/chat", json={"case_id": "nope", "message": "hi"})
        self.assertEqual(r.status_code, 200)
        events = [json.loads(l) for l in r.text.splitlines() if l.strip()]
        self.assertEqual(events[-1]["type"], "error")

    def test_runs_and_replay(self):
        runs = self.client.get("/api/runs").json()
        if not runs:
            self.skipTest("no saved runs")
        r = self.client.post(f"/api/replay/{runs[0]['id']}?speed=1000")
        events = [json.loads(l) for l in r.text.splitlines() if l.strip()]
        result = events[-1]
        self.assertEqual(result["type"], "result")
        self.assertTrue(result["case_id"])          # replay gets a live session for chat
        self.assertEqual(self.client.post("/api/replay/does-not-exist").status_code, 404)

    def test_replay_blocks_path_traversal(self):
        self.assertEqual(self.client.post("/api/replay/..%2F..%2Fmain").status_code, 404)

    def test_samples_are_well_formed(self):
        js = (ROOT / "static" / "samples.js").read_text(encoding="utf-8")
        self.assertEqual(js.count("emoji:"), js.count("text: `"))
        self.assertEqual(js.count("emoji:") % 3, 0, "sample grid has 3 columns")


if __name__ == "__main__":
    unittest.main(verbosity=2)
