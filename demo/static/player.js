// SessionPlayer — renders one recorded ChainSWE session as an agent transcript and replays it step by step at
// emulated real-time speed. Used by compare.html (two players side by side). Pacing rule (same as index.html):
// the session's measured wall time (results.jsonl `secs`) is spread over its steps in proportion to the tokens each
// step generated, plus a short per-command pause; the memory-write phase lasts its measured `mem_write_secs`.
// Per-step latencies are not in the logs (the ledger books actor calls at session end), so this emulates the
// recorded timing rather than replaying it.

// memop / rawcat: recorded-only memory methods of the paper run, shown by compare.html (it defines --memop / --rawcat)
export const NAMES = { kv: "Knowledge Vault", mem0: "Mem0", none: "No memory", memop: "MemOp", rawcat: "Full history" };
export const COLOR = { kv: "var(--kv)", mem0: "var(--mem0)", none: "var(--none)", memop: "var(--memop)", rawcat: "var(--rawcat)" };
export const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
export const usd = v => v == null ? "–" : "$" + (+v).toFixed(2);
export const tok = v => v == null ? "–" : v >= 1e6 ? (v/1e6).toFixed(2)+"M" : v >= 1000 ? (v/1000).toFixed(1)+"K" : String(Math.round(v));
// ---------------------------------------------------------------- per-model list prices (demo/model_prices.json via api/configs)
let MODEL_PRICES = {};
export function setModelPrices(t) { MODEL_PRICES = t || {}; }
/** Longest-prefix match of a (possibly dated) served model id -> {input, cached_input, output} per M tokens, or null. */
export function priceForModel(model, table = MODEL_PRICES) {
  if (!model) return null; model = String(model).replace(/^openai\//, ""); let best = null;
  for (const k in table) if ((model === k || model.startsWith(k)) && (!best || k.length > best.length)) best = k;
  return best ? table[best] : null;
}
/** USD of one agent turn at the list price of the model that served it; null when the model is unknown / unpriced. */
export function turnUsd(t) {
  const p = t && t.usage && priceForModel(t.model); if (!p) return null;
  const pt = t.usage.prompt || 0, cc = Math.min(t.usage.cached || 0, pt);
  return ((pt - cc) * p.input + cc * (p.cached_input ?? p.input) + (t.usage.completion || 0) * p.output) / 1e6;
}
/** The cost to show for a session / chain / list entry: real model price for live & router runs (every call's model
 *  recorded), the selected price row for recorded paper runs. Servers before `usd_shown` get the same rule here. */
export function costOf(x) {
  if (!x) return null; if (x.usd_shown != null) return x.usd_shown;
  const m = x.models ? Object.keys(x.models) : null;
  return m && m.length && !m.includes("model not recorded") && x.usd_model != null ? x.usd_model : x.usd;
}
export const isRealPriced = x => !!x && (x.priced_by ? x.priced_by === "model" : !!(x.models && Object.keys(x.models).length && !("model not recorded" in x.models)));
/** The model that served most of a session's steps (a who-line names a step's model when it is another one). */
export function mainModel(turns) {
  const n = {}; for (const t of turns || []) if (t.model) n[t.model] = (n[t.model] || 0) + 1;
  return Object.entries(n).sort((a, b) => b[1] - a[1])[0]?.[0] || null;
}
/** Who-line label for a turn, the same on every page: "agent · step 3 · 1.5K tokens in · 48 out". The model that served the step and
 *  its cost show only with "Show details" (class dt), except a step served by another model than the session's main one (`main`,
 *  e.g. router `auto` picking per call): that step names its model. */
export function turnWho(t, main = null) {
  const m = t.model && !/(^|\/)coder-next$/.test(t.model) ? String(t.model).replace(/^openai\//, "") : null;
  const c = m ? turnUsd(t) : null, tm = m ? ` · <span class="tm" title="model that served this call">${esc(m)}</span>` : "";
  // one inline span: it wraps like a sentence also where .who is a flex row (the Agent page)
  return `<span class="wl">agent${tm && main && t.model !== main ? tm : tm ? `<span class="dt">${tm}</span>` : ""} · step ${t.step}${t.usage ? ` · ${tok(t.usage.prompt)} tokens in · ${tok(t.usage.completion)} out` : ""}${c != null ? `<span class="dt"> · ${c < 0.01 ? "<$0.01" : usd(c)}</span>` : ""}</span>`;
}
/** How a session ended, in plain words (the raw status shows only with "Show details"). null = it submitted. */
export function endText(st) {
  st = String(st || "");
  if (!st || st === "Submitted") return null;
  if (st === "LimitsExceeded") return "Stopped at the step limit";
  if (st === "ContextWindowExceededError") return "Stopped: the conversation outgrew the model's context window";
  if (st === "RepeatedFormatError") return "Stopped: the model kept replying in the wrong format";
  if (/BadRequestError/.test(st)) return "Ended with an error: the model provider refused the request";
  if (/^error\b/i.test(st) || /Error$/.test(st)) return "Ended with an error";
  return `Session ended: ${st}`;
}
export const fmtClock = sec => { sec = Math.max(0, Math.round(sec)); return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`; };
const sleep = ms => new Promise(r => setTimeout(r, ms));

/** Recorded sessions do not change: the compare page prefetches a chain's sessions and the player reuses them
 *  instead of fetching each again on load / auto-advance. Cleared by the page when a live run adds sessions. */
export const sessionCache = new Map();
export const sessionUrl = q => `api/session?run=${encodeURIComponent(q.run)}&arm=${encodeURIComponent(q.arm)}&cid=${encodeURIComponent(q.cid)}&order=${q.order}`;
export async function getSession(q) { const u = sessionUrl(q); if (!sessionCache.has(u)) sessionCache.set(u, api(u).catch(e => { sessionCache.delete(u); throw e; })); return sessionCache.get(u); }
// Knowledge Vault version (⚙ settings, shared by every page via localStorage "kv-sys"): GET api/* calls carry ?kv= so the
// server shows that version's runs where they exist; new runs are started with it (memory_system).
export function kvVersion() { try { return localStorage.getItem("kv-sys") || ""; } catch { return ""; } }
export function setKvVersion(v) { try { if (v) localStorage.setItem("kv-sys", v); else localStorage.removeItem("kv-sys"); } catch {} }
export function withKv(path, opts) { const k = kvVersion(); if (!k || (opts && opts.method && opts.method.toUpperCase() !== "GET") || !/^api\//.test(path)) return path; return path + (path.includes("?") ? "&" : "?") + "kv=" + encodeURIComponent(k); }
/** ⚙ panel section: <select id> filled from meta.kv_versions; changing it stores the choice and reloads the page. */
export function wireKvSetting(sel, note, meta) {
  const vs = (meta && meta.kv_versions) || []; if (!sel || !vs.length) return;
  sel.innerHTML = vs.map(v => `<option value="${esc(v.id)}">${esc(v.label)}</option>`).join("");
  const cur = vs.some(v => v.id === kvVersion()) ? kvVersion() : vs[0].id; sel.value = cur;
  const show = () => { const v = vs.find(x => x.id === sel.value); if (note) note.textContent = (v ? v.note + ". " : "") + "Used for new live, compare and sandbox runs; runs of this version are shown where they exist, otherwise the v3 runs."; };
  show(); sel.onchange = () => { setKvVersion(sel.value === vs[0].id ? "" : sel.value); show(); location.reload(); };
}
export async function api(path, opts) { const r = await fetch(withKv(path, opts), opts); const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || r.statusText); return j; }

export function noteHtml(text) {
  // KV notes: "DID: … | WHERE: … | VERIFY: …" (+ CONTEXT / LESSON / STATE …); mem0 facts: "[instance__f1]\nfact".
  // One row per line, colour-coded by tag; WHERE / VERIFY become their own pills with the path / command in mono.
  const TAG = /^(DID|CONTEXT|LESSON|STATE|NOTE|TODO|WARN|FACT):\s*(.*)$/;
  return esc(text).split("\n").map(l => {
    const t = l.trim();
    if (!t) return `<div class="nl-gap"></div>`;
    if (/^\[[^\]]+\]$/.test(t)) return `<div class="nl-inst">${t.slice(1, -1)}</div>`;
    const m = t.match(TAG);
    if (!m) return `<div class="nl">${t}</div>`;
    const k = m[1].toLowerCase(), parts = m[2].split(/\s+\|\s+(WHERE|VERIFY):\s*/);
    let h = `<div class="nl nl-${k}"><b class="tag tag-${k}">${m[1]}</b><span class="nl-body">${parts[0]}</span>`;
    for (let i = 1; i < parts.length; i += 2) h += `<span class="nl-sub"><b class="tag tag-${parts[i].toLowerCase()}">${parts[i]}</b><code>${parts[i + 1] || ""}</code></span>`;
    return h + `</div>`;
  }).join("");
}
/** A memory text as a `.note` block: MemOp's guidance is a markdown document, Full history is raw session text;
 *  every other kind (kv / mem0 / none) gets noteHtml's tagged rows, exactly as before. */
export function memNoteHtml(text, kind) {
  if (kind === "memop") return `<div class="note md">${md(text)}</div>`;
  if (kind === "rawcat") return `<div class="note rawtext">${esc(text)}</div>`;
  return `<div class="note">${noteHtml(text)}</div>`;
}


// ---------------------------------------------------------------- transcript markup shared by the replay player and the live feed
export function memoryHtml(s, kind) {
  if (s.memory) return `<div class="card memory" style="--arm:${COLOR[kind]}"><div class="hd"><span class="dot" style="background:${COLOR[kind]}"></span>Memory injected from earlier sessions <span class="meta">· ${s.memory.length} chars</span></div>${memNoteHtml(s.memory, kind)}</div>`;
  return `<div class="card nomem" style="color:var(--muted);font-size:13px">No cross-session memory injected${s.order === 1 ? " — first task of the chain" : kind === "none" ? " — no-memory baseline" : " — the store was empty"}.</div>`;
}
/** Light markdown for issue text and model answers: headings, **bold**, `code`, fenced blocks, bullets (input is escaped first). */
export function md(text) {
  // light markdown over escaped text: fenced blocks, headings, **bold**, `code`, bullets, [text](https://…) links.
  // Inline code is set aside first and put back last, kept as it is (`__all__` stays __all__, not a bold "all"), as index.html's md() does.
  const parts = esc(text).replace(/\u0000/g, "").split(/```[a-zA-Z0-9_+-]*[ \t]*\n?/), code = [];
  return parts.map((p, i) => i % 2 ? `<pre class="mdcode">${p.replace(/\n$/, "")}</pre>` :
    p.replace(/\n{3,}/g, "\n\n")
     .replace(/^[ \t]*#{1,6}[ \t]+(.*?)[ \t#]*$/gm, '<span class="md-h">$1</span>')
     .replace(/`([^`\n]+)`/g, (m, c) => `\u0000${code.push(c) - 1}\u0000`)
     .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[^\w])__(?=\S)(.+?)__(?!\w)/gm, "$1<b>$2</b>")
     .replace(/^[ \t]*[-*+][ \t]+/gm, "• ")
     .replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
     .replace(/\u0000(\d+)\u0000/g, (m, j) => `<code>${code[+j]}</code>`)).join("");
}
export function issueHtml(s) {
  return `<div class="msg user"><div class="who">Issue · ${esc(s.repo)}</div><div class="bubble md issue">${md(s.problem_statement)}</div></div>`;
}
/** One agent step. `pending` = the command is still executing (live feed): show the running marker instead of an observation.
 *  The observation bar reads "ok" / "exit N"; its size and the raw end status of the step show only with "Show details". */
export function turnHtml(t, { maxObs = 1200, pending = false, main = null } = {}) {
  const o = t.observation;
  let h = `<div class="msg assistant turn" data-k="${t.step}"><div class="who">${turnWho(t, main)}</div>
    <div class="bubble th">${esc(t.thought)}</div>${t.command ? `<div class="cmd">${esc(t.command)}</div>` : ""}<div class="obsw">`;
  if (o) { const long = o.output.length > maxObs;
    h += `<div class="obs-bar"><span class="rc ${o.returncode ? "err" : ""}">${o.returncode === 0 ? "ok" : `exit ${o.returncode ?? "?"}`}</span><span class="dt">${o.chars.toLocaleString()} chars${o.truncated ? " (truncated in log)" : ""}</span>${long ? `<button data-more="${t.step}">show all</button>` : ""}</div><div class="obs" data-obs="${t.step}">${esc(long ? o.output.slice(0, maxObs) + "\n…" : o.output)}</div>`; }
  else if (t.exit) h += `<div class="obs-bar dt"><span class="rc">${esc(t.exit)}</span></div>`;      // the card after the last step says how it ended
  else if (pending) h += `<div class="running">running</div>`;
  return h + `</div></div>`;
}
const WRAP = "overflow-wrap:anywhere;min-width:0";          // test ids and paths wrap instead of running past the column edge
/** The card that says how the session ended: "Submitted · 2 files changed", or why it stopped (raw status + error with details). */
function exitCardHtml(s) {
  const ch = s.changes || [], add = ch.reduce((a, f) => a + f.added, 0), rem = ch.reduce((a, f) => a + f.removed, 0), r = s.result || {};
  if (s.exit_status === "Submitted" || ch.length) return `<div class="card tail" data-tail="exit"><div class="hd">Submitted <span class="meta">· ${ch.length ? `${ch.length} file${ch.length === 1 ? "" : "s"} changed <span class="pm"><span class="a">+${add}</span> <span class="d">−${rem}</span></span>` : "no files changed"}</span></div>${ch.map(f => `<div class="mono" style="font-size:12px;${WRAP}">${esc(f.path)}</div>`).join("")}</div>`;
  if (!s.exit_status) return "";
  const txt = endText(s.exit_status), raw = !/^Session ended: /.test(txt);
  return `<div class="card tail" data-tail="exit"><div class="hd">${esc(txt)}${raw ? ` <span class="meta dt">· ${esc(s.exit_status)}</span>` : ""}</div>${r.error ? `<div class="note dt" style="color:var(--bad);${WRAP}">${esc(r.error)}</div>` : ""}</div>`;
}
/** "≈10.8K tokens to write" (≈ when the writer's tokens are estimated from the logs) */
const writeTok = s => { const t = s.tok || {}, w = (t.writer_prompt || 0) + (t.writer_completion || 0); return w ? `${t.writer_src === "estimate" ? "≈" : ""}${tok(w)} tokens to write` : ""; };
/** The cards after the last step: how it ended, the grade, memory written. */
export function tailHtml(s, kind) {
  let h = exitCardHtml(s);
  const r = s.result || {};
  if (r.resolved !== null && r.resolved !== undefined) {
    const f2p = Object.entries(r.f2p || {}), reg = r.p2p_failed || [], n = f2p.length + reg.length, pass = f2p.filter(([, st]) => st === "PASSED").length;
    const li = (good, t, extra = "") => `<li><span class="${good ? "ok" : "no"}">${good ? "✓" : "✗"}</span><span class="mono" style="font-size:12px;${WRAP}">${esc(t)}</span>${extra}</li>`;
    const ul = `<ul class="tests">${f2p.map(([t, st]) => li(st === "PASSED", t)).join("")}${reg.map(t => li(false, t, ` <span class="badge" title="this test passed before the change">regression</span>`)).join("")}</ul>`;
    h += `<div class="card tail" data-tail="grade"><div class="hd">${r.resolved ? `<span class="ok">✓ Resolved</span>` : `<span class="no">✗ Not resolved</span>`} <span class="meta">· checked with the issue's tests</span></div>${n > 5 ? `<details class="tlist"><summary>${n} tests · ${pass} pass, ${n - pass} fail</summary>${ul}</details>` : n ? ul : ""}</div>`;
  }
  const w = writeTok(s);
  if (s.note_written) h += `<div class="card memory tail" data-tail="note" style="--arm:${COLOR[kind]}"><div class="hd"><span class="dot" style="background:${COLOR[kind]}"></span>Written to memory for the next task <span class="meta">${w ? `· ${w}` : ""}<span class="dt"> · ${s.note_written.length.toLocaleString()} chars${r.mem_write_secs ? ` · ${r.mem_write_secs}s` : ""}</span></span></div>${memNoteHtml(s.note_written, kind)}</div>`;
  else if (kind !== "none" && r.mem_nodes === 0) h += `<div class="card tail" data-tail="note" style="color:var(--muted);font-size:13px">Nothing was written to memory for the next task (the writer stored 0 items)${w ? ` — it still used ${w.replace(/ to write$/, "")}` : ""}.</div>`;
  return h;
}
/** A task's name: the server's short summary (4-10 plain words), else the first line of its text without markdown. */
export function taskName(summary, text) {
  const s = String(summary || "").trim(); if (s) return s;
  return (String(text || "").split("\n").map(l => l.trim()).find(Boolean) || "").replace(/^#+\s*/, "").replace(/\*\*|__|`/g, "");
}
/** The pinned task header: {order, n, instance, repo, text, summary}. Returns {title, html} for a .taskpin element. */
export function taskPinHtml(t) {
  const text = (t.text || "").trim(), name = taskName(t.summary, text) || "(no task text)";
  return `<button class="tp-head" type="button" aria-expanded="false"><span class="tp-chev">▸</span>${t.noLabel ? "" : `<span class="tp-n">Task ${t.order}${t.n ? ` of ${t.n}` : ""}</span>`}<span class="tp-title">${esc(name)}</span><span class="tp-inst mono">${esc(t.instance || "")}</span></button>
    <div class="tp-body" hidden><div class="tp-meta">${esc(t.repo || "")}${t.instance ? ` · <span class="mono">${esc(t.instance)}</span>` : ""} · ${text.length.toLocaleString()} chars</div><div class="tp-text md">${md(text)}</div></div>`;
}
/** Fill a .taskpin element and wire the expand toggle (idempotent). Pass null to hide it. */
export function expandObs(root, turns, step) {
  const t = (turns || []).find(x => x.step === step), o = root.querySelector(`[data-obs="${step}"]`), b = root.querySelector(`[data-more="${step}"]`);
  if (t && t.observation && o) { o.textContent = t.observation.output; o.classList.add("open"); } if (b) b.remove();
}
export function setTaskPin(el, t) {
  if (!el) return;
  if (!t) { el.hidden = true; el.innerHTML = ""; return; }
  const open = el.classList.contains("open");
  el.hidden = false; el.innerHTML = taskPinHtml(t);
  const head = el.querySelector(".tp-head"), body = el.querySelector(".tp-body");
  const set = o => { el.classList.toggle("open", o); body.hidden = !o; head.setAttribute("aria-expanded", o); el.querySelector(".tp-chev").textContent = o ? "▾" : "▸"; };
  head.onclick = () => set(!el.classList.contains("open")); set(open);
}

export class SessionPlayer {
  /** root: container element. opts: {kind, price: {input, cached_input, output}, speed, follow(fn), onStats(fn), onDone(fn), nextOrder(fn)} */
  constructor(root, opts = {}) {
    this.root = root; this.opts = opts; this.speed = opts.speed || 1;
    this.sess = null; this.plan = null; this.k = 0; this.phase = 0; this.on = false; this.token = 0; this.done = false; this.prepared = false;
    this.elapsed = 0; this.total = 0; this.counted = { steps: 0, actor: 0, usd: 0 };
    this.cum = { steps: 0, actor: 0, writer: 0, usd: 0, elapsed: 0, tasks: 0, resolved: 0, models: {} };   // carried across tasks of a chain
    this.graded = false; this.wrote = false;      // this task's grade / memory write are counted in cum (once per task; prepare() resets)
    this._click = e => { const b = e.target.closest("[data-more]"); if (!b || !this.sess) return; expandObs(root, this.sess.turns, +b.dataset.more); };
    root.addEventListener("click", this._click);
  }
  /** The page is done with this player (a new one takes its column): stop it and remove its click listener from the transcript. */
  destroy() { this.pause(); this.sess = null; if (this.root) this.root.removeEventListener("click", this._click); }
  price(prompt, completion, cached = 0, model = null) {
    const p = (this.opts.priceFn && this.opts.priceFn(model)) || this.opts.price || { input: 0, cached_input: 0, output: 0 };
    return ((prompt - cached) * p.input + cached * p.cached_input + completion * p.output) / 1e6;
  }
  stats() {
    const s = this.sess, t = s?.tok || {};
    return { ...this.cum, task: s ? s.order : null, step: this.k, nSteps: s ? s.turns.length : 0, elapsedTask: this.elapsed, totalTask: this.total,
             resolvedTask: s ? s.result.resolved : null, done: this.done, live: !!s, kind: this.opts.kind, prepared: this.prepared };
  }
  emit() { if (this.opts.onStats) this.opts.onStats(this.stats()); }

  /** Load a task and show it whole (every step and the result cards); play / step / seek / showAll start the replay from step 1. */
  async load(q) {
    this.pause(); this.sess = null; this.prepared = false; this.done = false; this.k = 0; this.phase = 0; this.elapsed = 0; this.total = 0; this.cumAtTaskStart = null;
    this.root.innerHTML = `<div class="empty">Loading session…</div>`;
    try { this.sess = await getSession(q); }
    catch (e) { this.root.innerHTML = `<div class="empty">${esc(e.message)}</div>`; this.emit(); return false; }
    this.render(); this.root.scrollTop = 0; this.emit(); if (this.opts.onLoad) this.opts.onLoad(this.sess, this); return true;
  }
  resetCum() { this.cum = { steps: 0, actor: 0, writer: 0, usd: 0, elapsed: 0, tasks: 0, resolved: 0, models: {} }; }

  render() {
    const s = this.sess, kind = this.opts.kind || s.kind;
    const main = mainModel(s.turns);
    this.root.innerHTML = memoryHtml(s, kind) + issueHtml(s) + s.turns.map(t => turnHtml(t, { main })).join("") + tailHtml(s, kind);
  }

  planTimes() {
    const s = this.sess, comp = s.turns.map(t => (t.usage && t.usage.completion) || 50), sum = comp.reduce((a, b) => a + b, 0) || 1;
    const secs = s.result.secs || s.turns.length * 4, gen = secs * 0.75, run = secs * 0.25 / Math.max(1, s.turns.length);
    return s.turns.map((t, i) => ({ think: Math.max(0.8, gen * comp[i] / sum), obs: t.observation ? Math.max(0.3, Math.min(6, run + t.observation.chars / 10000)) : 0.3 }));
  }
  snapToLedger() {
    // The transcript can hold fewer steps than the ledger (sessions that end in RepeatedFormatError keep API calls the
    // trajectory did not save). At the end of a task, replace the replay's per-turn estimate with the session's recorded totals.
    const s = this.sess, t = s.tok || {}; if (!t.src) return;
    const steps = s.result.n_steps ?? this.counted.steps, actor = (t.actor_prompt || 0) + (t.actor_completion || 0);
    const usdActor = (this.opts.priceFn && t.usd_model_actor != null) ? t.usd_model_actor : (t.usd_actor ?? this.counted.usd);
    this.cum.steps += steps - this.counted.steps; this.cum.actor += actor - this.counted.actor; this.cum.usd += usdActor - this.counted.usd;
    this.counted = { steps, actor, usd: usdActor };
  }
  prepare() {
    const s = this.sess; this.plan = this.planTimes(); this.k = 0; this.phase = 0; this.done = false; this.elapsed = 0; this.counted = { steps: 0, actor: 0, usd: 0 };
    this.graded = false; this.wrote = false;
    if (!this.cumAtTaskStart) this.cumAtTaskStart = JSON.parse(JSON.stringify(this.cum));
    const hasNote = s.note_written || ((this.opts.kind || s.kind) !== "none" && s.result.mem_nodes === 0) || (s.tok && s.tok.writer_prompt);
    this.total = this.plan.reduce((a, p) => a + p.think + p.obs, 0) + 2.5 + (hasNote ? (s.result.mem_write_secs || 4) : 0);
    this.root.querySelectorAll(".turn, .tail").forEach(el => { el.hidden = true; });
    this.prepared = true; this.emit();
  }
  autoScroll() { const b = this.root; if (b.scrollHeight - b.scrollTop - b.clientHeight < 300) b.scrollTop = b.scrollHeight; }
  async typeText(el, text, dur, my) {
    const steps = Math.max(1, Math.min(text.length, Math.round(dur * 30 / this.speed)));
    el.classList.add("typing");
    for (let i = 1; i <= steps; i++) { if (my !== this.token) { el.classList.remove("typing"); return false; } el.textContent = text.slice(0, Math.round(text.length * i / steps)); await sleep(dur * 1000 / this.speed / steps); }
    el.textContent = text; el.classList.remove("typing"); return my === this.token;
  }
  creditTurn(t) {
    const u = t.usage || {}; const p = u.prompt || 0, c = u.completion || 0;
    const usd = this.price(p, c, 0, t.model);
    this.cum.steps += 1; this.cum.actor += p + c; this.cum.usd += usd; this.counted.steps += 1; this.counted.actor += p + c; this.counted.usd += usd;
    if (t.model) this.cum.models[t.model] = (this.cum.models[t.model] || 0) + 1;
  }
  async revealTurn(k, my) {
    const s = this.sess, t = s.turns[k], el = this.root.querySelector(`.turn[data-k="${t.step}"]`), plan = this.plan[k];
    if (!el) return true;
    const th = el.querySelector(".th"), cmd = el.querySelector(".cmd"), obsw = el.querySelector(".obsw");
    if (this.phase <= 1) {
      el.hidden = false; th.textContent = ""; if (cmd) cmd.hidden = true; obsw.hidden = true; this.autoScroll();
      // paused while "thinking": the step goes back to hidden (no placeholder blinking while paused); play() shows it again
      if (this.phase === 0) { th.innerHTML = `<span class="thinking">thinking</span>`; await sleep(Math.min(plan.think * 0.3, 2.5) * 1000 / this.speed); if (my !== this.token) { if (!this.on && this.sess && this.sess.turns[this.k] === t && !this.done) { el.hidden = true; th.textContent = ""; } return false; } this.phase = 1; }
      if (!await this.typeText(th, t.thought, plan.think * 0.7, my)) return false;
      this.elapsed += plan.think; this.cum.elapsed += plan.think; this.phase = 2; this.emit();
    }
    if (this.phase === 2) {
      if (cmd) { cmd.hidden = false; if (this.opts.follow) this.opts.follow(this, t); }
      obsw.hidden = false; const hold = obsw.innerHTML; obsw.innerHTML = `<div class="running">running</div>`; this.autoScroll();
      await sleep(plan.obs * 1000 / this.speed); if (my !== this.token) { obsw.innerHTML = hold; return false; }
      obsw.innerHTML = hold; this.elapsed += plan.obs; this.cum.elapsed += plan.obs; this.phase = 0; this.creditTurn(t); this.autoScroll(); this.emit();
    }
    return true;
  }
  async revealTail(my) {
    const s = this.sess, box = this.root;
    const show = sel => { const el = box.querySelector(sel); if (el) { el.hidden = false; this.autoScroll(); } return el; };
    const phase = async (text, secs) => { const el = document.createElement("div"); el.className = "card tail phase"; el.innerHTML = `<span class="running">${text}</span>`; box.appendChild(el); this.autoScroll(); await sleep(secs * 1000 / this.speed); el.remove(); return my === this.token; };
    show('.tail[data-tail="exit"]');
    // paused while the memory was being written, then ▶ again: the task is graded and counted already, only the write is left
    if (!this.graded) {
      if (!await phase("checking the result with the issue's tests", 2.5)) return false;
      this.elapsed += 2.5; this.cum.elapsed += 2.5; show('.tail[data-tail="grade"]');
      this.snapToLedger(); this.cum.tasks += 1; if (s.result.resolved) this.cum.resolved += 1; this.graded = true; this.emit();
    }
    const t = s.tok || {}, wp = t.writer_prompt || 0, wc = t.writer_completion || 0;
    if (!this.wrote && (box.querySelector('.tail[data-tail="note"]') || wp)) {       // the writer runs after every session, incl. the last one
      const w = s.result.mem_write_secs || 4;
      if (!await phase("writing memory for the next task", w)) return false;
      this.elapsed += w; this.cum.elapsed += w; show('.tail[data-tail="note"]');
      this.cum.writer += wp + wc; this.cum.usd += (this.opts.priceFn && t.usd_model_writer != null) ? t.usd_model_writer : this.price(wp, wc); this.wrote = true;
    }
    this.elapsed = this.total; this.emit(); return true;
  }
  async play() {
    if (!this.sess || this.on) return;
    if (!this.prepared || this.done) { if (this.done) return; this.prepare(); }
    this.on = true; const my = ++this.token; this.emit();
    while (this.on && my === this.token) {
      if (this.k < this.sess.turns.length) { if (!await this.revealTurn(this.k, my)) return; this.k++; this.emit(); }
      else { if (!await this.revealTail(my)) return; this.done = true; this.on = false; this.emit();
        const nx = this.opts.nextOrder ? this.opts.nextOrder(this) : null;
        if (nx) { await sleep(1200 / this.speed); if (my === this.token) { const ok = await this.load(nx); if (ok) this.play(); } }
        else if (this.opts.onDone) this.opts.onDone(this);
        return; }
    }
  }
  pause() { this.on = false; this.token++; this.emit(); }
  step() {
    if (!this.sess) return; if (this.on) this.pause(); if (!this.prepared) this.prepare(); if (this.done) return;
    if (this.k < this.sess.turns.length) {
      const t = this.sess.turns[this.k], el = this.root.querySelector(`.turn[data-k="${t.step}"]`);
      el.hidden = false; el.querySelector(".th").textContent = t.thought; el.querySelector(".th").classList.remove("typing"); const c = el.querySelector(".cmd"); if (c) c.hidden = false; el.querySelector(".obsw").hidden = false;
      const p = this.plan[this.k]; this.elapsed += p.think + p.obs; this.cum.elapsed += p.think + p.obs; this.creditTurn(t); this.k++; this.phase = 0;
    } else {
      this.root.querySelectorAll(".tail").forEach(e => e.hidden = false); this.elapsed = this.total; this.done = true;
      // a replay paused while grading or writing memory has counted that part already (graded / wrote): each task counts once
      const s = this.sess, t = s.tok || {};
      if (!this.graded) { this.snapToLedger(); this.cum.tasks += 1; if (s.result.resolved) this.cum.resolved += 1; this.graded = true; }
      if (t.writer_prompt && !this.wrote) { this.cum.writer += (t.writer_prompt || 0) + (t.writer_completion || 0); this.cum.usd += (this.opts.priceFn && t.usd_model_writer != null) ? t.usd_model_writer : this.price(t.writer_prompt || 0, t.writer_completion || 0); this.wrote = true; }
    }
    this.autoScroll(); this.emit();
  }
  showAll() { this.pause(); while (!this.done && this.sess) this.step(); }
  /** Jump to step index k (0-based) of the loaded task: the k steps before it are revealed and credited, the last of them
   *  scrolls to the top of the transcript, and play() resumes from step k + 1 (its "thinking" placeholder appears then, not
   *  while paused). Seeking backwards rewinds this task's counters to its start. */
  seek(k) {
    if (!this.sess) return; this.pause();
    const n = this.sess.turns.length; k = Math.max(0, Math.min(n, k));
    if (!this.prepared || this.done || k < this.k) { if (this.cumAtTaskStart) this.cum = JSON.parse(JSON.stringify(this.cumAtTaskStart)); this.render(); this.prepare(); }
    while (this.k < k && !this.done) this.step();
    this.phase = 0;
    const t = k > 0 && this.sess.turns[k - 1], el = t && this.root.querySelector(`.turn[data-k="${t.step}"]`), r = this.root;
    if (el) r.scrollTop += el.getBoundingClientRect().top - r.getBoundingClientRect().top - 8; else r.scrollTop = 0;   // scrolls the transcript only, not the page
    this.emit();
  }
  restart() { this.pause(); if (!this.sess) return; this.render(); this.prepare(); this.root.scrollTop = 0; }
}


// ---------------------------------------------------------------- LiveFeed: follow a harness job as it runs
// Polls GET /api/live/{job}/state (where the job is: task in progress, graded tasks, phase) and, while a task is
// in progress, GET /api/session for it — the harness rewrites the trajectory after every model reply
// (harness/live_agent.py, RSM_LIVE_SAVE=1), so the transcript grows step by step like a coding agent's console.
// Finished tasks of a chain stay in the transcript, collapsed; the task in progress is appended below them.
// opts: {interval, onTask(task, info), onSession(sess, info), onState(info), onTaskDone(order, sess, info), onDone(info), follow(turn)}
export class LiveFeed {
  constructor(root, job, opts = {}) {
    this.root = root; this.job = job; this.opts = opts; this.interval = opts.interval || 1500;
    // n = tasks in the chain; a sandbox job runs one task of its project: n = the project's task count (boot), at least its order
    this.on = false; this.state = null; this.order = undefined; this.sess = null; this.tasks = {}; this.n = job.custom ? (job.order || 1) : 0;
    this.doneShown = new Set(); this.finished = []; this.complete = 0; this.pinned = null; this.lastFollowed = 0;
    this.obsFull = {};                                            // "order:step" -> full observation text ("show all")
    this._click = e => { const b = e.target.closest("[data-more]"); if (!b) return; const key = b.dataset.key || `${this.order}:${b.dataset.more}`;
      const o = this.root && this.root.querySelector(`[data-obs="${b.dataset.more}"][data-okey="${key}"]`) || this.root.querySelector(`[data-obs="${b.dataset.more}"]`);
      if (o && this.obsFull[key] != null) { o.textContent = this.obsFull[key]; o.classList.add("open"); } b.remove(); };
    // follow mode: stick to the bottom until the viewer scrolls up; scrolling back to the bottom resumes it.
    // Scroll events our own DOM changes cause (a clamp when finished tasks collapse) are ignored.
    this.following = true; this._quietUntil = 0;
    this._scroll = () => { const b = this.root; if (!b || performance.now() < this._quietUntil) return; this.following = b.scrollHeight - b.scrollTop - b.clientHeight < 80; };
    if (root) this.listen(root);
  }
  get kind() { return this.job.kind; }
  // overflow-anchor off: Chrome's scroll anchoring otherwise re-anchors on the rewritten pending/flight nodes and
  // drags a viewer who scrolled up back to the bottom; follow mode does the anchoring we want.
  listen(root) { root.addEventListener("click", this._click); root.addEventListener("scroll", this._scroll, { passive: true }); root.style.overflowAnchor = "none"; }
  unlisten() { if (this.root) { this.root.removeEventListener("click", this._click); this.root.removeEventListener("scroll", this._scroll); this.root.style.overflowAnchor = ""; } }
  attach(root) { this.unlisten(); this.root = root; if (root) { this.following = true; this.listen(root); this.renderAll(); } }
  detach() { this.unlisten(); this.root = null; }
  start() { this.on = true; this.boot(); return this; }
  stop() { this.on = false; clearTimeout(this._t); }
  schedule() { clearTimeout(this._t); if (this.on) this._t = setTimeout(() => this.tick(), this.interval); }
  async boot() {
    try { const c = await api(`api/chain/${encodeURIComponent(this.job.cid)}?run=__live__`); for (const t of c.chain.tasks) this.tasks[t.order] = { ...t, repo: c.chain.repo };
      this.n = this.job.custom ? Math.max(this.job.order || 1, c.chain.n_tasks || 0) : c.chain.n_tasks; } catch {}
    this.renderAll(); this.tick();
  }
  sessionUrl(order) { return `api/session?run=${encodeURIComponent(this.job.run_id)}&arm=${encodeURIComponent(this.job.arm)}&cid=${encodeURIComponent(this.job.cid)}&order=${order}`; }
  async tick() {
    if (!this.on) return;
    let st; try { st = await api(`api/live/${this.job.id}/state`); } catch { this.schedule(); return; }
    if (!this.on) return;                                         // stopped while the request was out: deliver nothing more
    this.state = st; if (!this.job.custom && st.n_tasks) this.n = st.n_tasks;
    for (const o of st.done) if (!this.doneShown.has(o)) { this.doneShown.add(o); await this.finishTask(o); if (!this.on) return; }
    if (st.cur !== this.order) { this.order = st.cur; this.sess = null; this.complete = 0; this.pinned = null; this.lastFollowed = 0; this.newTask(); }
    if (st.cur != null && st.has_traj) {
      try { const sess = await api(this.sessionUrl(st.cur)); if (!this.on) return; if (this.order === st.cur) { this.sess = sess; this.renderTurns(); if (this.opts.onSession) this.opts.onSession(sess, this.info()); } }
      catch {}                                                    // a trajectory mid-write: keep what we have, try again next tick
    }
    this.renderStatus();
    if (this.opts.onState) this.opts.onState(this.info());
    if (st.status !== "running") { this.on = false; if (this.opts.onDone) this.opts.onDone(this.info()); return; }
    this.schedule();
  }
  phase() {
    const st = this.state, s = this.sess;
    if (!st) return "connecting";
    if (st.status !== "running") return "finished";
    if (st.cur == null) return "finishing";
    if (!st.has_traj) return st.done.length ? "preparing" : "starting";
    if (!s) return "loading";
    if (s.exit_status) return "grading";
    const last = s.turns[s.turns.length - 1];
    if (last && last.command && !last.observation) return "running";
    return "thinking";
  }
  /** The phase as one plain sentence (the status card; info().phaseText). */
  phaseText() {
    const ph = this.phase(), st = this.state, mem = this.job.kind !== "none", o = this.order;
    if (ph === "finished") return st.status === "done" ? "Run finished" : st.status === "stopped" ? (this.job.custom ? "Run stopped" : "Run stopped — finished tasks are kept") : "Run ended with an error";
    if (ph === "grading" && this.job.custom) return mem ? "Writing memory for the next task" : "Wrapping up";     // sandbox tasks have no tests
    return { connecting: "Connecting…", starting: "Starting: preparing the sandbox", preparing: mem ? `Task ${o}: loading memory from earlier tasks` : `Preparing task ${o}`,
      loading: "Loading the steps…", thinking: "The model is thinking", running: "Running the command in the sandbox",
      grading: "Checking the result with the issue's tests" + (mem ? ", then writing memory" : ""), finishing: "Wrapping up" }[ph] || "";
  }
  info() {
    const st = this.state || {}, s = this.sess, turns = s ? s.turns : [];
    let prompt = 0, completion = 0; for (const t of turns) if (t.usage) { prompt += t.usage.prompt || 0; completion += t.usage.completion || 0; }
    return { job: this.job, cid: this.job.cid, kind: this.job.kind, arm: this.job.arm, run_id: this.job.run_id, status: st.status || "running", phase: this.phase(), phaseText: this.phaseText(),
             order: this.order ?? null, n: this.n, done: st.done || [], results: st.results || [], step: turns.length, prompt, completion, n_calls: st.n_calls || 0,
             elapsed: st.now && st.started ? st.now - st.started : 0, model: st.model, sess: s, finished: this.finished, log_tail: st.log_tail || "",
             usd: this.finished.reduce((a, f) => a + (costOf(f.sess && f.sess.tok) || 0), 0) + turns.reduce((a, t) => a + (turnUsd(t) || 0), 0),
             priced: turns.some(t => turnUsd(t) != null) || this.finished.some(f => isRealPriced(f.sess && f.sess.tok)) };
  }
  task(order) { const t = this.tasks[order] || {}; const s = this.sess && this.sess.order === order ? this.sess : null;
    return { order, n: this.n, instance: t.instance || (s && s.instance) || "", repo: t.repo || (s && s.repo) || "", text: (s && s.problem_statement) || t.problem_statement || "", summary: (s && s.summary) || t.summary || "" }; }
  // ---- rendering
  renderAll() {
    if (!this.root) return;
    this.root.innerHTML = `<div class="card live-status" data-lf="status"></div><div data-lf="history"></div><div data-lf="cur"><div data-lf="head"></div><div data-lf="turns"></div><div data-lf="pending"></div><div data-lf="flight"></div></div>`;
    for (const f of this.finished) this.root.querySelector('[data-lf="history"]').insertAdjacentHTML("beforeend", this.doneHtml(f.sess, f === this.finished[this.finished.length - 1]));
    this.complete = 0; this.renderHead(); this.renderTurns(); this.renderStatus();
  }
  el(name) { return this.root ? this.root.querySelector(`[data-lf="${name}"]`) : null; }
  newTask() {
    const cur = this.el("cur"); if (cur) { cur.innerHTML = `<div data-lf="head"></div><div data-lf="turns"></div><div data-lf="pending"></div><div data-lf="flight"></div>`; }
    this.renderHead();
    if (this.order != null && this.opts.onTask) { const t = this.task(this.order); if (t.text) { this.pinned = this.order; this.opts.onTask(t, this.info()); } }
  }
  renderHead() {
    const head = this.el("head"); if (!head) return;
    if (this.order == null) { head.innerHTML = ""; return; }
    const t = this.task(this.order);
    head.innerHTML = (this.sess ? memoryHtml(this.sess, this.job.kind) : "") + (t.text ? issueHtml({ repo: t.repo, problem_statement: t.text }) : `<div class="card" style="color:var(--muted);font-size:13px">Task ${this.order}${this.n ? ` of ${this.n}` : ""} — starting…</div>`);
  }
  renderTurns() {
    const s = this.sess; if (!s || !this.root) return;
    if (this.pinned !== s.order && this.opts.onTask) { this.pinned = s.order; this.opts.onTask(this.task(s.order), this.info()); }
    if (this.complete === 0) this.renderHead();                   // first trajectory of this task: the memory card is known now
    const turns = this.el("turns"), pending = this.el("pending"), flight = this.el("flight"); if (!turns) return;
    const n = s.turns.length, main = mainModel(s.turns); let done = 0; while (done < n && (s.turns[done].observation || s.turns[done].exit)) done++;
    for (let i = this.complete; i < done; i++) { const t = s.turns[i]; this.obsFull[`${s.order}:${t.step}`] = t.observation ? t.observation.output : null;
      turns.insertAdjacentHTML("beforeend", turnHtml(t, { main }).replace(`data-obs="${t.step}"`, `data-obs="${t.step}" data-okey="${s.order}:${t.step}"`).replace(`data-more="${t.step}"`, `data-more="${t.step}" data-key="${s.order}:${t.step}"`));
      if (this.opts.follow && t.command && t.step > this.lastFollowed) { this.lastFollowed = t.step; this.opts.follow(t, s); } }
    if (done > this.complete) { this.complete = done; this.autoScroll(); }
    pending.innerHTML = n > done ? s.turns.slice(done).map(t => turnHtml(t, { pending: true, main })).join("") : "";
    const ph = this.phase();
    const mem = this.job.kind !== "none", after = this.job.custom ? (mem ? "writing memory for the next task" : "") : `checking the result with the issue's tests${mem ? ", then writing memory for the next task" : ""}`;
    flight.innerHTML = ph === "thinking" ? `<div class="msg assistant"><div class="who">agent · step ${n + 1}</div><div class="bubble th"><span class="thinking">thinking</span></div></div>`
      : ph === "grading" ? exitCardHtml(s) + (after ? `<div class="card tail phase"><span class="running">${after}</span></div>` : "") : "";
    if (n > done || ph === "grading") this.autoScroll();
  }
  /** The status card: the live dot and one sentence about what the run is doing; the harness log only with "Show details".
   *  (Which method, model, task and step it is: the page around the feed says that once.) */
  renderStatus() {
    const box = this.el("status"); if (!box) return;
    const st = this.state, ph = this.phase(), live = !st || st.status === "running", log = (st && st.log_tail) || "";
    box.innerHTML = `<div class="hd"><span class="live-dot ${st && live ? "on" : ""}"></span><span class="lf-phase"><span class="${live ? "running" : ""}">${esc(this.phaseText())}</span></span>${live ? "" : ` <span class="meta dt">· ${esc(st.status)}</span>`}</div>
      <details class="sub lf-log" ${ph === "starting" || ph === "finished" && st && st.status !== "done" ? "open" : ""}><summary>harness log</summary><pre>${esc(log.split("\n").slice(-8).join("\n"))}</pre></details>`;
  }
  doneHtml(sess, open) {
    const r = sess.result || {}, res = r.resolved === true ? `<span class="ok">✓ resolved</span>` : r.resolved === false ? `<span class="no">✗ not resolved</span>` : this.job.custom ? "" : `<span class="na">not graded</span>`;
    const end = endText(sess.exit_status), meta = [end, `${sess.turns.length} step${sess.turns.length === 1 ? "" : "s"}`, r.secs ? fmtClock(r.secs) : "", sess.tok ? usd(costOf(sess.tok)) : ""].filter(Boolean).join(" · ");
    const main = mainModel(sess.turns);
    const html = memoryHtml(sess, this.job.kind) + issueHtml(sess) + sess.turns.map(t => turnHtml(t, { main }).replace(`data-obs="${t.step}"`, `data-obs="${t.step}" data-okey="${sess.order}:${t.step}"`).replace(`data-more="${t.step}"`, `data-more="${t.step}" data-key="${sess.order}:${t.step}"`)).join("") + tailHtml(sess, this.job.kind);
    return `<details class="lf-done" ${open ? "open" : ""}><summary><span class="tp-n">Task ${sess.order}${this.n ? ` of ${this.n}` : ""}</span> ${res} <span class="meta">· ${esc(meta)}</span><span class="tp-title">${esc(taskName(sess.summary, sess.problem_statement))}</span></summary><div class="lf-done-body">${html}</div></details>`;
  }
  async finishTask(order) {
    let sess = null; try { sess = await api(this.sessionUrl(order)); } catch {}
    if (!sess) return;
    this._quietUntil = performance.now() + 250;                   // collapsing the open task may clamp scrollTop: not a viewer scroll
    for (const t of sess.turns) this.obsFull[`${order}:${t.step}`] = t.observation ? t.observation.output : null;
    this.finished.push({ order, sess });
    const hist = this.el("history");
    if (hist) { hist.querySelectorAll("details.lf-done[open]").forEach(d => d.open = false); hist.insertAdjacentHTML("beforeend", this.doneHtml(sess, true)); }
    if (this.order === order) { this.order = undefined; this.sess = null; const cur = this.el("cur"); if (cur) cur.innerHTML = `<div data-lf="head"></div><div data-lf="turns"></div><div data-lf="pending"></div><div data-lf="flight"></div>`; }
    this.autoScroll();
    if (this.opts.onTaskDone) this.opts.onTaskDone(order, sess, this.info());
  }
  autoScroll() { const b = this.root; if (b && this.following) { this._quietUntil = Math.max(this._quietUntil, performance.now() + 100); b.scrollTop = b.scrollHeight; } }
}


// ---------------------------------------------------------------- memory comparison (retrieved / written / saved per task, side by side)
/** cols: [{label, color, kind, data (GET api/memory) | null, note (why there is no data), tip (header tooltip)}]. mode: "written" | "store".
 *  One row per task, one column per memory system, so the same task lines up across systems. `cur` = task to highlight.
 *  opts: {titles: {order: task title} (row heads "Task 1 — <title>"), unit: "chain" | "project" ("first task of the project")}.
 *  The page says once what a row shows; ids and how the store was read back show only with "Show details". */
export function memoryCompareHtml(cols, mode = "written", cur = null, opts = {}) {
  const n = Math.max(0, ...cols.map(c => c.data ? Math.max(c.data.n_tasks || 0, ...c.data.tasks.map(t => t.order)) : 0));
  const titles = opts.titles || {}, unit = opts.unit === "project" ? "project" : "chain";
  const head = cols.map(c => {
    const d = c.data, T = d && d.totals;
    const stats = !d ? `<div class="mc-stats muted">${esc(c.note || "no sessions")}</div>` : `<div class="mc-stats">
        <span><b>${T.written_items}</b> item${T.written_items === 1 ? "" : "s"} written</span>
        ${T.empty_writes ? `<span class="mc-warn"><b>${T.empty_writes}</b> of ${d.tasks.length} writes stored nothing</span>` : ""}
        <span>writer <b>${T.writer_src === "estimate" ? "≈" : ""}${tok(T.writer_tokens)}</b> tokens · <b>${usd(T.writer_usd)}</b></span>
        <span>store <b>${T.store_items}</b> item${T.store_items === 1 ? "" : "s"} · <b>${tok(T.store_chars)}</b> chars${T.store_after_order ? ` <span class="muted">after task ${T.store_after_order}</span>` : ""}</span>
        <span>injected into later tasks <b>${tok(T.injected_chars)}</b> chars</span></div>`;
    return `<div class="mc-head" data-kind="${esc(c.kind || "")}" style="--arm:${c.color}"><div class="mc-title"${c.tip ? ` title="${esc(c.tip)}"` : ""}><span class="dot" style="background:${c.color}"></span>${esc(c.label)}${d && d.exact ? ` <span class="badge dt" title="the harness saved the store after every write">saved store</span>` : d ? ` <span class="badge dt" title="store after task k is read back from what task k+1 was shown">reconstructed</span>` : ""}</div>${stats}</div>`;
  }).join("");
  let rows = "";
  for (let k = 1; k <= n; k++) {
    const inst = cols.map(c => c.data?.tasks.find(t => t.order === k)?.instance).find(Boolean) || "";
    rows += `<button class="mc-row ${k === cur ? "cur" : ""}" type="button" data-order="${k}" title="open task ${k}"><b>Task ${k}${titles[k] ? ` — ${esc(titles[k])}` : ""}</b>${inst ? ` <span class="mono dt">${esc(inst)}</span>` : ""}</button>`;
    // a column without data (No memory): empty, borderless cells keep the rows aligned
    rows += cols.map(c => c.data ? `<div class="mc-cell ${k === cur ? "cur" : ""}" data-kind="${esc(c.kind || "")}" style="--arm:${c.color}">${cellHtml(c, c.data.tasks.find(t => t.order === k), mode, k, n, c.data.tasks.find(t => t.order === k - 1), unit)}</div>`
      : `<div class="mc-cell mc-nodata" data-kind="${esc(c.kind || "")}" style="border:0;background:none;box-shadow:none;padding:0" aria-hidden="true"></div>`).join("");
  }
  return `<div class="mc" style="--mcn:${cols.length}"><div class="mc-grid">${head}${rows || `<div class="empty" style="grid-column:1/-1">No sessions.</div>`}</div></div>`;
}
function itemHtml(it, cls = "", from = false, kind = undefined) {
  const src = from ? `<span class="mc-from">${it.order ? `from task ${it.order}` : "from an earlier write"}</span>` : "";
  return `<div class="mc-item ${cls}" title="${esc(it.name)}">${it.updated ? `<span class="badge">updated</span> ` : ""}${src}${memNoteHtml(it.text, kind)}</div>`;
}
/** What the agent of task k was shown before it started (the memory system's retrieval/injection for that task). */
function retrievedHtml(t, k, prev, kind = undefined, unit = "chain") {
  // servers before the `retrieved` field: the store left by task k-1 is what task k was shown (whole-store injection)
  const r = t.retrieved || (k === 1 ? { items: [], chars: 0 } : prev?.store?.known ? { items: prev.store.items, chars: prev.store.chars, truncated: prev.store.truncated } : null);
  if (!r) return `<div class="mc-sec"><span class="mc-sec-t">Retrieved before task ${k}</span><span><b>${t.injected_chars ? tok(t.injected_chars) : 0}</b> chars</span></div><div class="mc-empty">Item list not available.</div>`;
  const head = `<div class="mc-sec"><span class="mc-sec-t">Retrieved before task ${k}</span><span><b>${r.items.length}</b> item${r.items.length === 1 ? "" : "s"} · <b>${tok(r.chars)}</b> chars</span>${r.truncated ? `<span class="mc-warn" title="the injected block is capped at the most recent characters; older items fell off">capped</span>` : ""}</div>`;
  if (!r.items.length) return head + `<div class="mc-empty">${k === 1 ? `Nothing — first task of the ${unit}.` : "Nothing — the store was empty when this task started."}</div>`;
  return head + `<div class="mc-items mc-retrieved">${r.items.map(i => itemHtml(i, "old", true, kind)).join("")}</div>`;
}
function cellHtml(c, t, mode, k, n, prev, unit = "chain") {
  if (!t) return `<div class="muted">not run</div>`;
  const res = t.resolved === true ? `<span class="ok" title="resolved">✓</span>` : t.resolved === false ? `<span class="no" title="not resolved">✗</span>` : `<span class="na">·</span>`;
  const w = t.written, s = t.store;
  if (mode === "store") {
    if (!s.known) return `<div class="mc-meta">${res} <span>store after this task not logged</span></div><div class="mc-empty">This run did not save the store, and no later task shows it${w.nodes ? ` (${w.nodes} item${w.nodes === 1 ? "" : "s"} were added)` : ""}. Live runs save it after every write.</div>`;
    const nw = s.items.filter(i => i.order === k).length;
    return `<div class="mc-meta">${res} <span><b>${s.items.length}</b> item${s.items.length === 1 ? "" : "s"} · <b>${tok(s.chars)}</b> chars</span>${nw ? `<span class="mc-new">+${nw} new</span>` : ""}${s.truncated ? `<span class="mc-warn" title="the injected block is capped at the most recent characters; older items fell off">capped</span>` : ""}${k < n ? `<span class="muted">→ task ${k + 1}</span>` : ""}</div>` +
      (s.items.length ? s.items.map(i => itemHtml(i, i.order === k ? "new" : "old", false, c.kind)).join("") : `<div class="mc-empty">Empty store.</div>`);
  }
  const cost = w.tokens ? `writer <b>${w.src === "estimate" ? "≈" : ""}${tok(w.tokens)}</b> tokens · ${usd(w.usd)}${w.secs ? `<span class="dt"> · ${w.secs}s</span>` : ""}` : "";
  const wHead = (count) => `<div class="mc-sec"><span class="mc-sec-t">Written after task ${k}</span>${count}<span>${cost}</span></div>`;
  let written;
  if (w.error) written = wHead("") + `<div class="mc-empty mc-warn">Writer error: ${esc(w.error)}</div>`;
  else if (!w.known) written = wHead(`<span><b>${w.nodes ?? "?"}</b> item${w.nodes === 1 ? "" : "s"}</span>`) + `<div class="mc-empty">Text not in the recorded logs: no later task shows what the last write stored. Live runs save it.</div>`;
  else if (!w.items.length) written = wHead(`<span><b>0</b> items</span>`) + `<div class="mc-empty mc-warn">Nothing stored${w.tokens ? `, though the writer still used ${tok(w.tokens)} tokens` : ""}.</div>`;
  else written = wHead(`<span><b>${w.items.length}</b> item${w.items.length === 1 ? "" : "s"} · <b>${tok(w.chars)}</b> chars</span>`) + `<div class="mc-items">${w.items.map(i => itemHtml(i, "new", false, c.kind)).join("")}</div>`;
  return `<div class="mc-meta">${res} <span>task ${k} ${t.resolved === true ? "resolved" : t.resolved === false ? "not resolved" : "not graded"}</span></div>` + retrievedHtml(t, k, prev, c.kind, unit) + written;
}
export function memorySegHtml(mode) {
  return `<div class="seg mc-seg" role="group" aria-label="Memory view"><button type="button" data-mcmode="written" aria-pressed="${mode !== "store"}">Retrieved + written</button><button type="button" data-mcmode="store" aria-pressed="${mode === "store"}">Saved store</button></div>`;
}

// Stop button with a second click to confirm (no dialogs): first click arms it for 6 s, second click runs `stop()`.
// `stop` returns a promise; the button shows "stopping…" until it resolves.
export function armStop(btn, stop, label = "■ Stop") {
  btn.textContent = label; btn.classList.remove("armed"); btn.disabled = false;
  btn.onclick = async () => {
    if (!btn.classList.contains("armed")) {
      btn.classList.add("armed"); btn.textContent = "■ Stop? click again";
      clearTimeout(btn._t); btn._t = setTimeout(() => { btn.classList.remove("armed"); btn.textContent = label; }, 6000);
      return;
    }
    clearTimeout(btn._t); btn.classList.remove("armed"); btn.disabled = true; btn.textContent = "stopping…";
    try { await stop(); } finally { btn.disabled = false; btn.textContent = label; }
  };
}
export async function stopJobs(ids) {
  try { return await api("api/live/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ jobs: ids }) }); }
  catch {                                         // a server older than the bulk endpoint: stop them one by one
    const r = await Promise.all(ids.map(id => api(`api/live/${id}/stop`, { method: "POST" }).then(() => id).catch(() => null)));
    return { ok: true, stopped: r.filter(Boolean) };
  }
}
