/* Pushback front-end. Plain JS, no build step.
   Flow: intake form → POST /api/investigate (NDJSON stream) → live board → tabbed case workspace. */
(() => {
  // ---------------------------------------------------------------- helpers
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fav = (d) => `https://www.google.com/s2/favicons?domain=${encodeURIComponent(d)}&sz=32`;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const secs = () => Math.round((Date.now() - t0) / 1000);

  const VERDICT = { push_back: "Push back", negotiate: "Negotiate", comply: "Probably fair", get_help: "Get help" };
  const CLAIM = { valid: "Valid", questionable: "Questionable", likely_invalid: "Likely invalid", unverifiable: "Unverified", pending: "Checking" };

  // ---------------------------------------------------------------- state
  let SOURCES = {}, CITED = new Set(), CLAIMS = {}, ORDER = [], DOC = "", CASE_ID = null, CASE = null;
  let timer = null, t0 = 0, searches = 0, liveThought = null, evFilter = "cited", phase = 0;
  let ctl = null, lastRun = null, lastEventAt = 0, watchdog = null, finished = false;
  const MAX_FILE_MB = 15, STALL_SECONDS = 150;

  const form = $("#form"), text = $("#text"), loc = $("#location"), fileIn = $("#file"), drop = $("#drop"), feed = $("#feed");

  // ================================================================ THEME
  const themeBtn = $("#themeBtn");
  function applyTheme(t, animate) {
    const root = document.documentElement;
    if (animate) { root.classList.add("theme-anim"); setTimeout(() => root.classList.remove("theme-anim"), 400); }
    root.setAttribute("data-theme", t);
    themeBtn.setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme");
    try { localStorage.setItem("pb-theme", t); } catch (e) { /* private mode: theme just won't persist */ }
  }
  applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light", false);
  themeBtn.onclick = () => applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark", true);

  // ================================================================ INTAKE
  function sampleCard(sm, onclick, extraClass = "") {
    const b = document.createElement("button");
    b.type = "button"; b.className = "sample " + extraClass;
    b.innerHTML = `
      <span class="sample-top"><span class="se">${esc(sm.emoji)}</span><span class="cat">${esc(sm.category)}</span></span>
      <span class="st">${esc(sm.title)}</span>
      <span class="sq">${esc(sm.question || "")}</span>
      <span class="sample-foot"><span class="sl">📍 ${esc(sm.location)}</span><span class="go-hint">Load →</span></span>`;
    b.onclick = onclick; return b;
  }
  (window.SAMPLES || []).forEach((sm) => $("#sampleList").appendChild(sampleCard(sm, (e) => {
    text.value = sm.text; loc.value = sm.location; fileIn.value = ""; $("#fileName").textContent = "";
    $$(".sample.selected").forEach((x) => x.classList.remove("selected")); e.currentTarget.classList.add("selected");
    hideNotice(); syncClear(); text.scrollTop = 0; form.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => $("#go").focus({ preventScroll: true }), 350);
  })));

  if (new URLSearchParams(location.search).has("replay")) {   // offline demo backup
    fetch("/api/runs").then((r) => r.json()).then((runs) => {
      if (!runs.length) return;
      $("#replayWrap").classList.remove("hidden");
      runs.forEach((r) => $("#replayList").appendChild(sampleCard(
        { emoji: "▶", category: "Saved run", title: r.title, location: "Replays offline, no API calls", question: "" },
        () => runCase(`/api/replay/${encodeURIComponent(r.id)}`, null), "replay")));
    }).catch(() => {});
  }

  // Notice banner on the intake page (off-topic input, file problems…)
  function showNotice(title, msg) {
    const n = $("#notice"); n.innerHTML = `<b>${esc(title)}</b><span>${esc(msg)}</span><button type="button" aria-label="Dismiss">✕</button>`;
    n.classList.remove("hidden"); n.querySelector("button").onclick = hideNotice;
  }
  function hideNotice() { $("#notice").classList.add("hidden"); }

  // Clear button: visible only when there is something to clear
  const clearBtn = $("#clearBtn");
  const syncClear = () => clearBtn.classList.toggle("hidden", !(text.value.trim() || loc.value.trim() || fileIn.files.length));
  clearBtn.onclick = () => {
    text.value = ""; loc.value = ""; fileIn.value = ""; $("#fileName").textContent = "";
    $$(".sample.selected").forEach((x) => x.classList.remove("selected")); hideNotice();
    syncClear(); text.focus(); toast("Cleared");
  };
  text.addEventListener("input", syncClear); loc.addEventListener("input", syncClear);
  function validFile(f) {
    if (!f) return true;
    if (!/^image\/|^application\/pdf$/.test(f.type)) { showNotice("Unsupported file", "Please attach a photo (JPG, PNG, WEBP) or a PDF of the letter."); return false; }
    if (f.size > MAX_FILE_MB * 1024 * 1024) { showNotice("File too large", `Please keep it under ${MAX_FILE_MB} MB. A phone photo of the letter is plenty.`); return false; }
    return true;
  }
  fileIn.onchange = () => {
    if (!validFile(fileIn.files[0])) fileIn.value = "";
    $("#fileName").textContent = fileIn.files[0] ? "📄 " + fileIn.files[0].name : ""; syncClear();
  };
  ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (ev) => {
    const f = ev.dataTransfer.files?.[0]; if (!f) return;
    if (!validFile(f)) return;
    const dt = new DataTransfer(); dt.items.add(f); fileIn.files = dt.files; $("#fileName").textContent = "📄 " + f.name; syncClear();
  });
  text.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) form.requestSubmit(); });
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (!text.value.trim() && !fileIn.files.length) { text.focus(); toast("Paste a letter or attach a file first"); return; }
    if (!fileIn.files.length && text.value.trim().length < 25) { text.focus(); showNotice("A little more detail, please", "Paste the full letter, or describe what happened: who sent it, what they want, amounts and dates."); return; }
    hideNotice();
    runCase("/api/investigate", new FormData(form));
  });
  $("#newCase").onclick = () => {
    if (ctl && !finished) ctl.abort("user");
    $("#work").classList.add("hidden"); $("#intake").classList.remove("hidden"); $("#newCase").classList.add("hidden"); window.scrollTo(0, 0);
  };

  // ================================================================ STREAMING
  async function streamNDJSON(url, opts, onEvent) {
    let resp;
    try { resp = await fetch(url, opts); }
    catch (e) { if (e.name === "AbortError") throw e; throw new Error("Can't reach the Pushback server. Is it still running?"); }
    if (!resp.ok) { const j = await resp.json().catch(() => ({})); throw new Error(j.error || `Server error ${resp.status}`); }
    const reader = resp.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true }); let i;
      while ((i = buf.indexOf("\n")) >= 0) { const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1); if (line) onEvent(JSON.parse(line)); }
    }
  }

  async function runCase(url, fd) {
    lastRun = { url, fd };
    resetWorkspace();
    const myCtl = ctl = new AbortController(); finished = false; lastEventAt = Date.now();
    clearInterval(watchdog);
    watchdog = setInterval(() => {   // guard against a silent, stuck stream
      if (!finished && Date.now() - lastEventAt > STALL_SECONDS * 1000) myCtl.abort("stall");
    }, 5000);
    try {
      await streamNDJSON(url, { method: "POST", body: fd, signal: myCtl.signal }, (ev) => { if (ctl === myCtl) { lastEventAt = Date.now(); handle(ev); } });
      if (!finished && ctl === myCtl) fail("The investigation ended unexpectedly. Please try again.");
    } catch (e) {
      if (ctl !== myCtl) return;                       // superseded by a newer run
      if (e.name === "AbortError" || myCtl.signal.aborted) {
        if (myCtl.signal.reason === "stall") fail(`No response for ${Math.round(STALL_SECONDS / 60)} minutes. The AI service may be overloaded.`);
        else if (!$("#work").classList.contains("hidden")) stopped();
      } else fail(String(e.message || e));
    } finally {
      if (ctl === myCtl) { clearInterval(timer); clearInterval(watchdog); closeThought(); $("#stopBtn").classList.add("hidden"); }
    }
  }
  $("#stopBtn").onclick = () => { if (ctl && !finished) ctl.abort("user"); };

  function stopped() {
    finished = true; setStatus("err", "Stopped");
    addItem("err", `<div class="purpose">Stopped by you</div>`);
    showError("Investigation stopped", "Nothing was sent anywhere. Run it again whenever you're ready.");
  }

  // ================================================================ WORKSPACE
  function resetWorkspace() {
    $("#intake").classList.add("hidden"); $("#work").classList.remove("hidden"); $("#newCase").classList.remove("hidden");
    SOURCES = {}; CITED = new Set(); CLAIMS = {}; ORDER = []; DOC = ""; CASE_ID = null; CASE = null;
    searches = 0; liveThought = null; evFilter = "cited"; phase = 0;
    feed.innerHTML = ""; $("#facts").innerHTML = ""; $("#ccards").innerHTML = ""; $("#doc").innerHTML = "";
    ["#ovBody", "#planBody", "#letterBody", "#srcList", "#chatLog", "#suggest"].forEach((s) => ($(s).innerHTML = ""));
    $("#claimsSkeleton").classList.remove("hidden"); $("#xrayGrid").classList.add("hidden");
    $("#cbIssue").textContent = "New case"; $("#cbWhere").textContent = ""; $("#cbTitle").textContent = "Reading your document…";
    setStatus("live", "Investigating"); setProgress(4);
    $("#stopBtn").classList.remove("hidden"); $("#errSlot").innerHTML = "";
    $("#layout").classList.add("live");
    $$(".sidenav button").forEach((b) => (b.disabled = true));
    $$("#evFilter button").forEach((b) => b.classList.toggle("on", b.dataset.f === "cited"));
    updateStats(); updateXrayProgress();
    t0 = Date.now(); clearInterval(timer); timer = setInterval(() => ($("#stTime").textContent = secs() + "s"), 250);
    window.scrollTo(0, 0);
  }

  function setStatus(kind, label) { const s = $("#cbStatus"); s.className = `status status-${kind}`; s.querySelector("span").textContent = label; }
  function setProgress(p) { phase = Math.max(phase, p); $("#mainBar").style.width = phase + "%"; }
  function updateStats() {
    const all = Object.values(SOURCES);
    $("#stSearches").textContent = searches;
    $("#stSources").textContent = all.length;
    $("#stOfficial").textContent = all.filter((s) => s.official).length;
    $("#nSources").textContent = all.length || "";
  }

  function setTab(tab) {
    $$(".sidenav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    $$(".view").forEach((v) => v.classList.toggle("active", v.dataset.panel === tab));
    if (tab === "evidence") renderEvidence();
    const top = $("#layout").getBoundingClientRect().top + window.scrollY - 80;
    if (window.scrollY > top) window.scrollTo({ top, behavior: "smooth" });
  }
  $$(".sidenav button").forEach((b) => (b.onclick = () => setTab(b.dataset.tab)));

  // ================================================================ EVENT HANDLER
  function addItem(cls, html, id) {
    const li = document.createElement("li"); li.className = cls; if (id) li.id = "t-" + id;
    li.innerHTML = `<span class="dot"></span>${html}`; feed.appendChild(li);
    const box = $("#layout").classList.contains("live") ? $('.view[data-panel="research"]') : null;
    if (box) box.scrollTo({ top: box.scrollHeight, behavior: "smooth" });
    return li;
  }
  function closeThought() { if (liveThought) { liveThought.classList.remove("live"); liveThought = null; } }

  function handle(ev) {
    switch (ev.type) {
      case "status": addItem("step", `<div class="purpose">${esc(ev.text)}</div>`); break;
      case "triage": renderTriage(ev.triage); setProgress(18); break;
      case "thought_delta": {
        if (!liveThought) {
          liveThought = addItem("think live", `<div class="think-label">● reasoning</div><div class="think-text"></div>`);
          const li = liveThought; li.addEventListener("click", () => li.classList.toggle("open"));
        }
        const t = liveThought.querySelector(".think-text");
        t.textContent += ev.text.replace(/\*\*/g, ""); t.scrollTop = t.scrollHeight;
        setProgress(24);
        break;
      }
      case "thought_end": closeThought(); break;
      case "thinking": addItem("think", `<div class="think-label">reasoning</div><div class="think-text">${esc(ev.text)}</div>`); break; // older replays
      case "search": {
        searches++; updateStats(); closeThought(); setProgress(Math.min(60, phase + 6));
        const f = [];
        if (ev.mode === "deep") f.push(`<span class="flt deep">deep</span>`);
        if (ev.since) f.push(`<span class="flt date">since ${esc(ev.since)}</span>`);
        (ev.domains || []).forEach((d) => f.push(`<span class="flt dom">${esc(d)}</span>`));
        if (ev.category && ev.category !== "none") f.push(`<span class="flt">${esc(ev.category)}</span>`);
        addItem("search", `<div class="purpose"><span class="spin"></span>${esc(ev.purpose || "Searching")}</div>
          <div class="q">exa.search("${esc(ev.query)}")</div>${f.length ? `<div class="filters">${f.join("")}</div>` : ""}<div class="hits"></div>`, ev.id);
        break;
      }
      case "read":
        addItem("read", `<div class="purpose"><span class="spin"></span>${esc(ev.purpose || "Reading full pages")}</div>
          <div class="q">exa.contents(${ev.urls.length} page${ev.urls.length > 1 ? "s" : ""})</div><div class="hits"></div>`, ev.id);
        break;
      case "found": {
        ev.sources.forEach((s) => (SOURCES[s.id] = s)); updateStats();
        const li = $("#t-" + CSS.escape(ev.id)); if (!li) break;
        li.classList.add("done"); li.querySelector(".spin")?.remove();
        const hits = li.querySelector(".hits");
        if (ev.error) hits.innerHTML = `<span class="flt" style="color:var(--bad)">error, trying another way</span>`;
        else if (!ev.sources.length) hits.innerHTML = `<span class="flt">no results</span>`;
        ev.sources.forEach((s, i) => setTimeout(() => hits.insertAdjacentHTML("beforeend",
          `<a class="hit ${s.official ? "off" : ""}" href="${esc(s.url)}" target="_blank" rel="noopener" title="${esc(s.title)}"><img src="${fav(s.domain)}" alt="">${esc(s.domain)}</a>`), i * 70));
        break;
      }
      case "claim_update": stampClaim(ev); break;
      case "off_topic": {
        finished = true;
        $("#work").classList.add("hidden"); $("#intake").classList.remove("hidden"); $("#newCase").classList.add("hidden");
        showNotice("That's outside what Pushback does", ev.text);
        window.scrollTo({ top: 0, behavior: "smooth" });
        break;
      }
      case "result":
        closeThought();
        (ev.sources || []).forEach((s) => (SOURCES[s.id] = s)); updateStats();
        addItem("final", `<div class="purpose">Case file ready</div><div class="q">${ev.stats?.exa_calls ?? "?"} Exa calls · ${ev.stats?.sources ?? "?"} sources${ev.stats?.exa_cost ? ` · $${ev.stats.exa_cost.toFixed(3)}` : ""} · ${secs()}s</div>`);
        CASE_ID = ev.case_id || null; finished = true;
        try { renderCase(ev.case); } catch (err) { console.error(err); fail("The case file arrived but couldn't be displayed. Please try again."); }
        break;
      case "error": finished = true; addItem("err", `<div class="purpose">${esc(ev.text)}</div>`); fail(ev.text); break;
    }
  }

  function fail(msg) {
    finished = true; setStatus("err", "Failed"); clearInterval(timer);
    showError("Something went wrong", msg);
  }
  function showError(title, msg) {
    if (!ORDER.length) $("#claimsSkeleton").classList.add("hidden");
    $("#errSlot").innerHTML = `<div class="err-card"><div><b>${esc(title)}</b><p>${esc(msg)}</p></div>
      <div class="err-actions">${lastRun ? `<button class="btn btn-primary btn-sm" id="retryBtn">↻ Try again</button>` : ""}<button class="btn btn-sm" id="editBtn">Edit input</button></div></div>`;
    $("#retryBtn")?.addEventListener("click", () => runCase(lastRun.url, lastRun.fd));
    $("#editBtn").onclick = () => $("#newCase").click();
  }

  // ================================================================ TRIAGE + X-RAY
  function renderTriage(t) {
    DOC = t.doc_text || text.value || "";
    $("#cbIssue").textContent = t.issue || "Case";
    $("#cbWhere").textContent = [t.jurisdiction && "📍 " + t.jurisdiction, t.other_party && "vs. " + t.other_party].filter(Boolean).join("  ·  ");
    $("#cbTitle").textContent = t.gist || "Investigating…";
    $("#facts").innerHTML = [...(t.amounts || []).map((a) => `💶 ${esc(a.label)}: <b>${esc(a.value)}</b>`),
                             ...(t.dates || []).map((d) => `📅 ${esc(d.label)}: <b>${esc(d.date)}</b>`)]
      .map((f, i) => `<span class="fact" style="animation-delay:${i * 50}ms">${f}</span>`).join("");
    (t.claims || []).forEach((c) => addClaim({ id: c.id, quote: c.quote, claim: c.claim, verdict: "pending" }));
    renderXray();
  }

  function addClaim(c) { if (!CLAIMS[c.id]) ORDER.push(c.id); CLAIMS[c.id] = { ...(CLAIMS[c.id] || {}), ...c }; }

  function locate(doc, quote) {
    if (!quote) return null;
    let i = doc.indexOf(quote); if (i >= 0) return [i, i + quote.length];
    i = doc.toLowerCase().indexOf(quote.toLowerCase()); if (i >= 0) return [i, i + quote.length];
    const pat = quote.trim().split(/\s+/).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+");
    try { const m = new RegExp(pat, "i").exec(doc); if (m) return [m.index, m.index + m[0].length]; } catch {}
    return null;
  }

  function renderXray() {
    if (!ORDER.length) return;
    $("#claimsSkeleton").classList.add("hidden"); $("#xrayGrid").classList.remove("hidden");
    const spans = ORDER.map((id) => { const r = locate(DOC, CLAIMS[id].quote); return r && { id, s: r[0], e: r[1] }; })
      .filter(Boolean).sort((a, b) => a.s - b.s);
    let html = "", pos = 0;
    for (const sp of spans) {
      if (sp.s < pos) continue;
      html += esc(DOC.slice(pos, sp.s)) + `<mark class="hl v-${CLAIMS[sp.id].verdict}" data-c="${sp.id}"><sup>${sp.id}</sup>${esc(DOC.slice(sp.s, sp.e))}</mark>`;
      pos = sp.e;
    }
    $("#doc").innerHTML = html + esc(DOC.slice(pos));
    $("#xrayGrid").classList.toggle("nodoc", !DOC.trim());
    $("#ccards").innerHTML = ORDER.map((id) => cardHtml(CLAIMS[id])).join("");
    updateXrayProgress();
  }

  function cardHtml(c) {
    const p = c.verdict === "pending";
    return `<div class="ccard v-${c.verdict}" data-c="${esc(c.id)}">
      <div class="ccard-top"><span class="cid">${esc(c.id)}</span>${p ? `<span class="checking"><span class="spin"></span>checking</span>` : `<span class="pill p-${c.verdict} slam">${CLAIM[c.verdict] || c.verdict}</span>`}</div>
      <div class="cclaim">${esc(c.claim)}</div>
      ${p ? `<div class="shimmer"></div><div class="shimmer short"></div>` : (c.headline ? `<div class="chead">${esc(c.headline)}${c.explanation ? "" : cites(c.source_ids)}</div>` : "")}
      ${c.explanation ? `<div class="cexp">${esc(c.explanation)}${cites(c.source_ids, c.uncited)}</div>` : ""}
    </div>`;
  }

  function stampClaim(ev) {
    const id = ev.claim_id; if (!CLAIMS[id]) addClaim({ id, claim: ev.headline, quote: "" });
    Object.assign(CLAIMS[id], { verdict: ev.verdict, headline: ev.headline, source_ids: ev.source_ids });
    const card = $(`.ccard[data-c="${CSS.escape(id)}"]`);
    if (card) card.outerHTML = cardHtml(CLAIMS[id]); else renderXray();
    $(`.ccard[data-c="${CSS.escape(id)}"]`)?.classList.add("just");
    const mark = $(`mark[data-c="${CSS.escape(id)}"]`); if (mark) mark.className = `hl v-${ev.verdict} flashmark`;
    addItem("stampitem", `<div class="purpose"><span class="pill p-${ev.verdict}">${CLAIM[ev.verdict]}</span>${esc(id)} · ${esc(ev.headline)}</div>`);
    updateXrayProgress();
  }

  function updateXrayProgress() {
    const done = ORDER.filter((id) => CLAIMS[id].verdict !== "pending").length;
    $("#xrayCount").textContent = `${done} / ${ORDER.length} checked`;
    $("#xrayBar").style.width = (ORDER.length ? (done / ORDER.length) * 100 : 0) + "%";
    $("#nClaims").textContent = ORDER.length || "";
    if (ORDER.length) setProgress(24 + Math.round((done / ORDER.length) * 64));
  }

  // hover link between highlighted text and claim cards
  document.addEventListener("mouseover", (e) => {
    const t = e.target.closest("mark.hl, .ccard"); if (!t) return;
    $$(`[data-c="${CSS.escape(t.dataset.c)}"]`).forEach((el) => el.classList.add("focus"));
  });
  document.addEventListener("mouseout", (e) => { if (e.target.closest("mark.hl, .ccard")) $$(".focus").forEach((el) => el.classList.remove("focus")); });

  // ================================================================ FINAL CASE
  function cites(ids, uncited) {
    if (!ids || !ids.length) return uncited ? `<span class="uncited">⚠ no source</span>` : "";
    return ids.map((id) => `<button class="cite ${SOURCES[id]?.official ? "off" : ""}" data-s="${esc(id)}">${esc(id)}</button>`).join("");
  }

  function renderCase(c) {
    CASE = c;
    (JSON.stringify(c).match(/"S\d+"/g) || []).forEach((x) => CITED.add(x.replace(/"/g, "")));

    // merge final verdicts into the claim board
    let extra = 0;
    (c.claims || []).forEach((x) => {
      const id = x.claim_id && CLAIMS[x.claim_id] ? x.claim_id : `X${++extra}`;
      addClaim({ id, claim: CLAIMS[id]?.claim || x.claim, quote: CLAIMS[id]?.quote || "", verdict: x.verdict,
                 headline: CLAIMS[id]?.headline || "", explanation: x.explanation, source_ids: x.source_ids, uncited: x.uncited });
    });
    ORDER.forEach((id) => { if (CLAIMS[id].verdict === "pending") CLAIMS[id].verdict = "unverifiable"; });
    if (!DOC) DOC = text.value || "";
    renderXray();

    $("#cbTitle").textContent = c.title || "Your case";
    if (c.jurisdiction && !$("#cbWhere").textContent) $("#cbWhere").textContent = "📍 " + c.jurisdiction;
    if ($("#cbIssue").textContent === "New case" && c.category) $("#cbIssue").textContent = c.category[0].toUpperCase() + c.category.slice(1);
    setStatus("done", "Case ready"); setProgress(100); clearInterval(timer);

    renderOverview(c); renderPlan(c); renderLetter(c); renderAsk(); renderEvidence();

    $("#layout").classList.remove("live");
    $$(".sidenav button").forEach((b) => (b.disabled = false));
    setTab("overview");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // ---------- Overview
  function nextDeadline(c) {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    return (c.deadlines || []).filter((d) => d.date && new Date(d.date) >= today).sort((a, b) => a.date.localeCompare(b.date))[0];
  }
  function daysUntil(d) { const t = new Date(); t.setHours(0, 0, 0, 0); return Math.round((new Date(d) - t) / 864e5); }

  function renderOverview(c) {
    const total = ORDER.length;
    const challenged = ORDER.filter((id) => ["likely_invalid", "questionable"].includes(CLAIMS[id].verdict)).length;
    const nd = nextDeadline(c);
    const offCited = [...CITED].filter((id) => SOURCES[id]?.official).length;
    $("#ovBody").innerHTML = `
      <div class="stack">
        <div class="verdict">
          <div class="verdict-side v-${esc(c.verdict)}">
            <div><div class="eyebrow">Our verdict</div><div class="verdict-word">${esc(VERDICT[c.verdict] || c.verdict)}</div></div>
            <div class="verdict-conf"><b>${esc(c.confidence)}</b> confidence · ${CITED.size} sources cited (${offCited} official)</div>
          </div>
          <div class="verdict-main">
            <div class="meta"><span>${esc(c.jurisdiction)}</span><span>·</span><span>${esc(c.category)}</span></div>
            <p class="bottom-line" id="bl"></p>
          </div>
        </div>

        <div class="kpis">
          <div class="kpi bad"><span class="k">At stake</span><span class="v count-up">${esc(c.money.at_stake || "n/a")}</span></div>
          <div class="kpi good"><span class="k">You could recover</span><span class="v count-up">${esc(c.money.potential_recovery || "n/a")}</span></div>
          <div class="kpi"><span class="k">Claims challenged</span><span class="v">${challenged} of ${total}</span><span class="s">${total - challenged} hold up</span></div>
          <div class="kpi ${nd && daysUntil(nd.date) <= 14 ? "hot" : ""}"><span class="k">Next deadline</span><span class="v">${nd ? `${daysUntil(nd.date)} days` : "none"}</span><span class="s">${nd ? esc(nd.date + " · " + nd.label) : "No dated deadlines found"}</span></div>
        </div>

        <div class="grid-2">
          <div class="card"><h3>Why this matters</h3><p class="prose">${esc(c.money.explanation || c.bottom_line)}${cites(c.money.source_ids)}</p></div>
          ${c.opposing_view ? `<div class="card callout"><h3>Their best argument</h3><p class="prose">${esc(c.opposing_view)}</p></div>` : ""}
        </div>

        <div class="card">
          <h3>Your next steps <span class="count">${c.actions.length} total</span></h3>
          <ol class="next-steps">${c.actions.slice(0, 3).map((a) => `<li><div><div class="s">${esc(a.step)}${cites(a.source_ids)}</div><div class="d">${esc(a.detail)}</div></div></li>`).join("")}</ol>
          ${c.actions.length > 3 ? `<button class="link-btn" data-go="plan">See the full action plan →</button>` : ""}
        </div>

        <div class="cta-row">
          <button class="btn btn-accent btn-lg" data-go="letter">Review your reply letter →</button>
          <button class="btn btn-lg" data-go="claims">See the claim audit</button>
          <button class="btn btn-lg" data-go="ask">Ask a question</button>
        </div>
        <div class="disclaimer">${esc(c.caveats)} Pushback is not a lawyer. For high-stakes situations, use the free help listed in your action plan.</div>
      </div>`;
    typewrite($("#bl"), c.bottom_line);
    $$("#ovBody .kpi .v").forEach((v) => v.classList.toggle("long", v.textContent.trim().length > 30));  // prose → compact text
    $$("#ovBody .count-up:not(.long)").forEach(countUp);
  }
  document.addEventListener("click", (e) => { const g = e.target.closest("[data-go]"); if (g) setTab(g.dataset.go); });

  // ---------- Plan
  function renderPlan(c) {
    const dls = (c.deadlines || []).slice().sort((a, b) => (a.date || "9999").localeCompare(b.date || "9999"));
    const dlHtml = dls.map((d) => {
      const n = d.date ? daysUntil(d.date) : null;
      const cls = n == null ? "" : n < 0 ? "past" : n <= 14 ? "hot" : "";
      const tag = n == null ? "" : n < 0 ? "passed" : n === 0 ? "today" : `in ${n} day${n === 1 ? "" : "s"}`;
      return `<li class="${cls}"><div class="dl-date">${esc(d.date || "No fixed date")}${tag ? `<span class="dl-in">${tag}</span>` : ""}</div><div class="dl-label">${esc(d.label)}${cites(d.source_ids, d.uncited)}</div></li>`;
    }).join("");
    $("#planBody").innerHTML = `
      <div class="stack">
        <div class="grid-2">
          <div class="card">
            <h3>Checklist <span class="count" id="planCount">0 / ${c.actions.length}</span></h3>
            <div class="plan-progress"><div id="planBar"></div></div>
            <ul class="checklist">${c.actions.map((a, i) => `<li><label><input type="checkbox" data-i="${i}"><div><div class="s">${esc(a.step)}${cites(a.source_ids)}</div><div class="d">${esc(a.detail)}</div></div></label></li>`).join("")}</ul>
          </div>
          <div class="card">
            <h3>Deadlines <span class="count">${dls.length}</span></h3>
            ${dls.length ? `<ul class="timeline-dl">${dlHtml}</ul>` : `<p class="prose">No hard deadlines found.</p>`}
            ${dls.some((d) => d.date) ? `<button class="btn btn-sm" id="ics" style="margin-top:18px">📅 Add all to calendar</button>` : ""}
          </div>
        </div>
        ${c.contacts.length ? `<div class="card"><h3>Free help near you</h3><div class="contacts">${c.contacts.map((p) => `
          <a class="contact" href="${esc(p.url)}" target="_blank" rel="noopener"><span class="n">${esc(p.name)}</span><span class="r">${esc(p.role)}</span><span class="u">${esc(p.url.replace(/^https?:\/\//, ""))}</span></a>`).join("")}</div></div>` : ""}
      </div>`;
    const boxes = $$("#planBody .checklist input");
    boxes.forEach((cb) => (cb.onchange = () => {
      cb.closest("li").classList.toggle("checked", cb.checked);
      const n = boxes.filter((b) => b.checked).length;
      $("#planCount").textContent = `${n} / ${boxes.length}`; $("#planBar").style.width = (n / boxes.length) * 100 + "%";
      if (n === boxes.length) toast("All steps done. Nice work 🎉");
    }));
    $("#ics")?.addEventListener("click", () => { download("pushback-deadlines.ics", ics(dls.filter((d) => d.date), c.title), "text/calendar"); toast("Calendar file downloaded"); });
  }

  // ---------- Letter
  const TONES = [
    ["💪", "Firmer", "Make it firmer and more assertive, still professional. Mention escalation to the relevant authority if not resolved."],
    ["🤝", "Friendlier", "Make it warmer and more conciliatory, aiming for an amicable solution, while keeping every request."],
    ["✂️", "Shorter", "Make it about half as long. Keep the legal references, amounts, request and deadline."],
    ["🇬🇧", "English", "Translate it into English."],
    ["🌐", "Local language", null],
  ];
  function renderLetter(c) {
    $("#letterBody").innerHTML = `
      <div class="letter-layout">
        <div class="paper">
          <div class="paper-meta"><label>To</label><input id="lTo" value="${esc(c.letter.to)}" placeholder="Recipient email"><label>Subject</label><input id="lSubj" value="${esc(c.letter.subject)}"></div>
          <textarea id="lBody" spellcheck="true">${esc(c.letter.body)}</textarea>
          <div class="paper-actions">
            <button class="btn btn-accent" id="send">✓ Approve &amp; open in email</button>
            <button class="btn" id="copy">Copy</button>
            <button class="btn" id="dlTxt">Download</button>
            <span class="note">Fill in any [brackets] before sending</span>
          </div>
        </div>
        <aside class="rewrite-panel">
          <div class="card">
            <h3>AI rewrite</h3>
            <div class="tone-list">${TONES.map(([e, l, i]) => `<button class="tone" data-i="${esc(i || `Translate it into the official local language of ${c.jurisdiction}.`)}">${e} ${l}</button>`).join("")}</div>
            <form class="tone-custom" id="toneForm"><textarea id="toneIn" placeholder="Or describe a change, e.g. “mention I have photos of the move-in condition”"></textarea><button class="btn btn-sm">Rewrite</button></form>
            <button class="btn btn-sm btn-ghost hidden" id="undo" style="margin-top:8px;width:100%">↶ Undo last rewrite</button>
          </div>
        </aside>
      </div>`;
    const body = () => $("#lBody").value, subj = () => $("#lSubj").value, to = () => $("#lTo").value;
    $("#send").onclick = () => { location.href = `mailto:${encodeURIComponent(to())}?subject=${encodeURIComponent(subj())}&body=${encodeURIComponent(body())}`; };
    $("#copy").onclick = async () => { await navigator.clipboard.writeText(`Subject: ${subj()}\n\n${body()}`); toast("Letter copied"); };
    $("#dlTxt").onclick = () => download("pushback-letter.txt", `To: ${to()}\nSubject: ${subj()}\n\n${body()}`, "text/plain");
    $$("#letterBody .tone").forEach((b) => (b.onclick = () => rewrite(b.dataset.i, b)));
    $("#toneForm").onsubmit = (e) => { e.preventDefault(); const v = $("#toneIn").value.trim(); if (v) { rewrite(v, null); $("#toneIn").value = ""; } };
    $("#undo").onclick = () => { const prev = undoStack.pop(); if (prev != null) $("#lBody").value = prev; if (!undoStack.length) $("#undo").classList.add("hidden"); };
  }

  let undoStack = [], rewriting = false;
  async function rewrite(instruction, btn) {
    if (rewriting) return; rewriting = true;
    const ta = $("#lBody"), before = ta.value; undoStack.push(before);
    $$("#letterBody .tone").forEach((b) => (b.disabled = true)); btn?.classList.add("active");
    ta.classList.add("writing"); ta.value = "";
    try {
      await streamNDJSON("/api/rewrite", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ case_id: CASE_ID || "", letter: before, instruction }) }, (ev) => {
        if (ev.type === "token") { ta.value += ev.text; ta.scrollTop = ta.scrollHeight; }
        if (ev.type === "error") throw new Error(ev.text);
      });
      if (!ta.value.trim()) ta.value = before;
      $("#undo").classList.remove("hidden");
    } catch (e) { ta.value = before; undoStack.pop(); toast("Rewrite failed: " + (e.message || e)); }
    finally { ta.classList.remove("writing"); $$("#letterBody .tone").forEach((b) => (b.disabled = false)); btn?.classList.remove("active"); rewriting = false; }
  }

  // ---------- Ask (chat)
  function renderAsk() {
    $("#chatLog").innerHTML = `<div class="chat-empty"><div class="big">Ask me anything about this case</div>I'll answer from your case file and search the web again if I need to.</div>`;
    $("#suggest").innerHTML = ["What if they refuse or ignore me?", "How strong is my case, honestly?", "What evidence should I collect now?", "Explain it like I'm 12"]
      .map((q) => `<button class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("");
    $$("#suggest .chip").forEach((b) => (b.onclick = () => ask(b.dataset.q)));
  }
  $("#chatForm").onsubmit = (e) => { e.preventDefault(); const v = $("#chatIn").value.trim(); if (v) { $("#chatIn").value = ""; ask(v); } };

  let asking = false;
  async function ask(q) {
    if (asking) return;
    if (!CASE_ID) { toast("Chat needs a live case. Run an investigation first."); return; }
    asking = true; $("#chatSend").disabled = true;
    const log = $("#chatLog"); $(".chat-empty", log)?.remove();
    log.insertAdjacentHTML("beforeend", `<div class="msg me">${esc(q)}</div>`);
    const bot = document.createElement("div"); bot.className = "msg bot typing";
    bot.innerHTML = `<div class="research"></div><div class="body"><span class="dots"><i></i><i></i><i></i></span></div>`;
    log.appendChild(bot); log.scrollTop = log.scrollHeight;
    let txt = "";
    try {
      await streamNDJSON("/api/chat", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ case_id: CASE_ID, message: q }) }, (ev) => {
        if (ev.type === "token") { txt += ev.text; bot.querySelector(".body").innerHTML = md(txt); log.scrollTop = log.scrollHeight; }
        else if (ev.type === "search" || ev.type === "read") {
          searches += ev.type === "search" ? 1 : 0; updateStats();
          bot.querySelector(".research").insertAdjacentHTML("beforeend", `<div class="rs" id="r-${esc(ev.id)}"><span class="spin"></span>${ev.type === "search" ? "🔎" : "📄"} ${esc(ev.purpose || ev.query || "Reading pages")}</div>`);
        } else if (ev.type === "found") {
          ev.sources.forEach((s) => (SOURCES[s.id] = { ...s, fromChat: !SOURCES[s.id] })); updateStats();
          const r = bot.querySelector(`#r-${CSS.escape(ev.id)}`);
          if (r) { r.querySelector(".spin")?.remove(); r.insertAdjacentHTML("beforeend", `<span class="rs-n">· ${ev.sources.length} sources</span>`); }
        } else if (ev.type === "done") (ev.sources || []).forEach((s) => (SOURCES[s.id] = { ...s, fromChat: SOURCES[s.id]?.fromChat }));
        else if (ev.type === "error") throw new Error(ev.text);
      });
      if (!txt) bot.querySelector(".body").textContent = "(no answer)";
      (txt.match(/S\d+/g) || []).forEach((id) => SOURCES[id] && CITED.add(id));
    } catch (e) { bot.querySelector(".body").innerHTML = `<span style="color:var(--bad)">${esc(e.message || e)}</span>`; }
    finally { bot.classList.remove("typing"); asking = false; $("#chatSend").disabled = false; }
  }

  function md(s) {
    const h = esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/(^|\s)\*(?!\s)(.+?)\*(?=\s|$|[.,;:])/g, "$1<i>$2</i>")
      .replace(/\[((?:S\d+\s*,?\s*)+)\]/g, (_, ids) => cites(ids.match(/S\d+/g)));
    let out = "", inList = false;
    for (const l of h.split(/\n/)) {
      const m = l.match(/^\s*(?:[*\-•]|\d+\.)\s+(.*)/);
      if (m) { if (!inList) { out += "<ul>"; inList = true; } out += `<li>${m[1]}</li>`; continue; }
      if (inList) { out += "</ul>"; inList = false; }
      if (l.trim()) out += `<p>${l}</p>`;
    }
    return out + (inList ? "</ul>" : "");
  }

  // ---------- Evidence
  $$("#evFilter button").forEach((b) => (b.onclick = () => { evFilter = b.dataset.f; $$("#evFilter button").forEach((x) => x.classList.toggle("on", x === b)); renderEvidence(); }));
  function renderEvidence() {
    const all = Object.values(SOURCES).sort((a, b) => +a.id.slice(1) - +b.id.slice(1));
    const list = evFilter === "cited" ? all.filter((s) => CITED.has(s.id)) : evFilter === "official" ? all.filter((s) => s.official) : all;
    $("#srcList").innerHTML = list.length ? list.map(srcCard).join("") : `<div class="empty-note">No sources in this view yet.</div>`;
  }
  function srcCard(s) {
    const q = s.highlights?.[0];
    return `<article class="src" id="src-${esc(s.id)}">
      <div class="src-head"><span class="src-id ${s.official ? "off" : ""}">${esc(s.id)}</span>
        <div><a class="src-title" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>
        <div class="src-meta"><img src="${fav(s.domain)}" alt=""> ${esc(s.domain)} ${s.published ? `· ${esc(s.published)}` : ""} ${s.official ? `<span class="badge-off">Official</span>` : ""} ${s.fromChat ? `<span class="badge-new">From chat</span>` : ""}</div></div></div>
      ${q ? `<blockquote title="Click to expand"><span class="qt">${esc(q)}</span></blockquote>` : ""}
      <div class="via">found via: ${esc(s.query)}</div></article>`;
  }

  // ---------- citations: tooltip + jump to evidence
  const tip = $("#tip");
  document.addEventListener("mouseover", (e) => {
    const b = e.target.closest(".cite"); if (!b) return;
    const s = SOURCES[b.dataset.s]; if (!s) return;
    tip.innerHTML = `<b>${esc(s.title)}</b>${s.highlights?.[0] ? `<i>“${esc(s.highlights[0].slice(0, 240))}…”</i><br>` : ""}<span style="opacity:.6">${esc(s.domain)}${s.published ? " · " + esc(s.published) : ""}</span>`;
    tip.classList.remove("hidden");
    const r = b.getBoundingClientRect();
    tip.style.left = Math.max(8, Math.min(r.left, innerWidth - 380)) + "px"; tip.style.top = r.bottom + 8 + "px";
  });
  document.addEventListener("mouseout", (e) => { if (e.target.closest(".cite")) tip.classList.add("hidden"); });
  document.addEventListener("click", (e) => {
    const q = e.target.closest(".src blockquote"); if (q) q.classList.toggle("open");
    const m = e.target.closest("mark.hl"); if (m) $(`.ccard[data-c="${CSS.escape(m.dataset.c)}"]`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    const b = e.target.closest(".cite"); if (!b) return;
    tip.classList.add("hidden");
    const id = b.dataset.s; if (!SOURCES[id]) return;
    if ($("#layout").classList.contains("live")) { window.open(SOURCES[id].url, "_blank"); return; }
    if (evFilter === "cited" && !CITED.has(id)) CITED.add(id);
    setTab("evidence");
    requestAnimationFrame(() => {
      const el = $("#src-" + CSS.escape(id)); if (!el) return;
      el.scrollIntoView({ behavior: "smooth", block: "center" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1600);
    });
  });

  // ================================================================ MICRO-INTERACTIONS
  async function typewrite(el, s) {
    s = s || ""; const step = Math.max(1, Math.ceil(s.length / 70));
    for (let i = 0; i <= s.length; i += step) { el.textContent = s.slice(0, i); await sleep(12); }
    el.textContent = s;
  }
  function countUp(el) {
    const orig = el.textContent, m = orig.match(/\d[\d,.\s]*\d|\d/); if (!m) return;
    const raw = m[0], target = parseFloat(raw.replace(/[\s,]/g, "")); if (!isFinite(target) || target === 0) return;
    const dec = /\.\d{1,2}$/.test(raw.replace(/\s/g, "")) ? raw.split(".").pop().length : 0;
    const t1 = performance.now(), dur = 1100;
    const fmt = (v) => v.toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
    const tick = (now) => {
      const p = Math.min(1, (now - t1) / dur), k = 1 - Math.pow(1 - p, 3);
      el.textContent = orig.replace(raw, fmt(target * k));
      if (p < 1) requestAnimationFrame(tick); else el.textContent = orig;
    };
    requestAnimationFrame(tick);
  }
  let toastTimer;
  function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 2400); }

  // voice input (Chrome / Edge)
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  $$(".mic").forEach((btn) => {
    if (!SR) { btn.classList.add("hidden"); return; }
    btn.onclick = () => {
      const target = $("#" + btn.dataset.target); if (!target) return;
      if (btn.rec) { btn.rec.stop(); return; }
      const rec = new SR(); rec.interimResults = true; rec.continuous = true; rec.lang = navigator.language || "en-US";
      const base = target.value ? target.value.replace(/\s*$/, " ") : "";
      rec.onresult = (e) => { target.value = base + [...e.results].map((r) => r[0].transcript).join(""); target.dispatchEvent(new Event("input")); };
      rec.onend = () => { btn.classList.remove("rec"); btn.rec = null; };
      btn.rec = rec; btn.classList.add("rec"); rec.start();
    };
  });

  function download(name, content, type) {
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([content], { type })); a.download = name; a.click();
  }
  function ics(items, title) {
    const stamp = new Date().toISOString().replace(/[-:]/g, "").split(".")[0] + "Z";
    const ev = items.map((d, i) => {
      const day = d.date.replace(/-/g, ""), next = new Date(d.date); next.setDate(next.getDate() + 1);
      return ["BEGIN:VEVENT", `UID:pushback-${Date.now()}-${i}@pushback`, `DTSTAMP:${stamp}`, `DTSTART;VALUE=DATE:${day}`,
        `DTEND;VALUE=DATE:${next.toISOString().slice(0, 10).replace(/-/g, "")}`, `SUMMARY:${d.label.replace(/[,;]/g, " ").slice(0, 120)}`,
        `DESCRIPTION:${(title || "").replace(/[,;]/g, " ")}`, "BEGIN:VALARM", "TRIGGER:-P2D", "ACTION:DISPLAY", "DESCRIPTION:Pushback deadline", "END:VALARM", "END:VEVENT"].join("\r\n");
    });
    return ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Pushback//EN", ...ev, "END:VCALENDAR"].join("\r\n");
  }
})();
