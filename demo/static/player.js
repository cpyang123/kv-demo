// SessionPlayer — renders one recorded ChainSWE session as an agent transcript and replays it step by step at
// emulated real-time speed. Used by compare.html (two players side by side). Pacing rule (same as index.html):
// the session's measured wall time (results.jsonl `secs`) is spread over its steps in proportion to the tokens each
// step generated, plus a short per-command pause; the memory-write phase lasts its measured `mem_write_secs`.
// Per-step latencies are not in the logs (the ledger books actor calls at session end), so this emulates the
// recorded timing rather than replaying it.

export const NAMES = { kv: "Knowledge Vault", mem0: "Mem0", none: "No memory" };
export const COLOR = { kv: "var(--kv)", mem0: "var(--mem0)", none: "var(--none)" };
export const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
export const usd = v => v == null ? "–" : "$" + (+v).toFixed(2);
export const tok = v => v == null ? "–" : v >= 1e6 ? (v/1e6).toFixed(2)+"M" : v >= 1000 ? (v/1000).toFixed(1)+"K" : String(Math.round(v));
export const fmtClock = sec => { sec = Math.max(0, Math.round(sec)); return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`; };
const sleep = ms => new Promise(r => setTimeout(r, ms));

export async function api(path, opts) { const r = await fetch(path, opts); const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || r.statusText); return j; }

export function noteHtml(text) {
  return esc(text).split("\n").map(l => {
    if (/^\[[^\]]+\]$/.test(l.trim())) return `<span class="inst">${l}</span>`;
    return l.replace(/^(DID|CONTEXT|LESSON|STATE|NOTE|TODO|WARN|FACT):/, (m, t) => `<b class="tag">${t}</b>`).replace(/ \| (WHERE|VERIFY):/g, (m, t) => ` <b class="tag">${t}</b>`);
  }).join("\n");
}


// ---------------------------------------------------------------- transcript markup shared by the replay player and the live feed
export function memoryHtml(s, kind) {
  if (s.memory) return `<div class="card memory" style="--arm:${COLOR[kind]}"><div class="hd"><span class="dot" style="background:${COLOR[kind]}"></span>Memory injected from earlier sessions <span class="meta">· ${s.memory.length} chars</span></div><div class="note">${noteHtml(s.memory)}</div></div>`;
  return `<div class="card" style="color:var(--muted);font-size:13px">No cross-session memory injected${s.order === 1 ? " — first task of the chain" : kind === "none" ? " — no-memory baseline" : " — the store was empty"}.</div>`;
}
export function issueHtml(s) {
  return `<div class="msg user"><div class="who">Issue · ${esc(s.repo)}</div><div class="bubble">${esc(s.problem_statement)}</div></div>`;
}
/** One agent step. `pending` = the command is still executing (live feed): show the running marker instead of an observation. */
export function turnHtml(t, { maxObs = 1200, pending = false } = {}) {
  const o = t.observation;
  let h = `<div class="msg assistant turn" data-k="${t.step}"><div class="who">agent · step ${t.step}${t.usage ? ` · ${tok(t.usage.prompt)} in / ${t.usage.completion} out` : ""}</div>
    <div class="bubble th">${esc(t.thought)}</div>${t.command ? `<div class="cmd">${esc(t.command)}</div>` : ""}<div class="obsw">`;
  if (o) { const long = o.output.length > maxObs;
    h += `<div class="obs-bar"><span class="rc ${o.returncode ? "err" : ""}">rc ${o.returncode ?? "?"}</span><span>${o.chars.toLocaleString()} chars${o.truncated ? " (truncated in log)" : ""}</span>${long ? `<button data-more="${t.step}">show all</button>` : ""}</div><div class="obs" data-obs="${t.step}">${esc(long ? o.output.slice(0, maxObs) + "\n…" : o.output)}</div>`; }
  else if (t.exit) h += `<div class="obs-bar"><span class="rc">${esc(t.exit)}</span></div>`;
  else if (pending) h += `<div class="running">running</div>`;
  return h + `</div></div>`;
}
/** The cards after the last step: submission, grade, memory written. */
export function tailHtml(s, kind) {
  let h = "";
  const ch = s.changes || [], add = ch.reduce((a, f) => a + f.added, 0), rem = ch.reduce((a, f) => a + f.removed, 0), r = s.result || {};
  if (s.exit_status === "Submitted" || ch.length) h += `<div class="card tail" data-tail="exit"><div class="hd">Submitted <span class="meta">· ${ch.length} file${ch.length === 1 ? "" : "s"} changed <span class="pm"><span class="a">+${add}</span> <span class="d">−${rem}</span></span></span></div>${ch.map(f => `<div class="mono" style="font-size:12px">${esc(f.path)}</div>`).join("")}</div>`;
  else if (s.exit_status) h += `<div class="card tail" data-tail="exit"><div class="hd">Session ended: <span class="meta">${esc(s.exit_status)}</span></div>${r.error ? `<div class="note" style="color:var(--bad)">${esc(r.error)}</div>` : ""}</div>`;
  if (r.resolved !== null && r.resolved !== undefined) {
    const f2p = Object.entries(r.f2p || {});
    h += `<div class="card tail" data-tail="grade"><div class="hd">${r.resolved ? `<span class="ok">✓ Resolved</span>` : `<span class="no">✗ Not resolved</span>`} <span class="meta">· F2P/P2P grading${r.secs ? ` · ${r.secs}s session` : ""}</span></div><ul class="tests">${f2p.map(([t, st]) => `<li><span class="${st === "PASSED" ? "ok" : "no"}">${st === "PASSED" ? "✓" : "✗"}</span><span class="mono" style="font-size:12px">${esc(t)}</span></li>`).join("")}${(r.p2p_failed || []).map(t => `<li><span class="no">✗</span><span class="mono" style="font-size:12px">${esc(t)}</span> <span class="badge">regression</span></li>`).join("")}</ul></div>`;
  }
  if (s.note_written) h += `<div class="card memory tail" data-tail="note" style="--arm:${COLOR[kind]}"><div class="hd"><span class="dot" style="background:${COLOR[kind]}"></span>Written to memory <span class="meta">· ${s.note_written.length} chars${s.tok && s.tok.writer_prompt ? ` · writer ${tok(s.tok.writer_prompt + s.tok.writer_completion)} tok${s.tok.writer_src === "estimate" ? " (est.)" : ""}` : ""}${r.mem_write_secs ? ` · ${r.mem_write_secs}s` : ""}</span></div><div class="note">${noteHtml(s.note_written)}</div></div>`;
  else if (kind !== "none" && r.mem_nodes === 0) h += `<div class="card tail" data-tail="note" style="color:var(--muted);font-size:13px">The writer produced nothing for this session (0 items stored)${s.tok && s.tok.writer_prompt ? ` — it still cost ${tok(s.tok.writer_prompt + s.tok.writer_completion)} tokens` : ""}.</div>`;
  return h;
}
/** The pinned task header: {order, n, instance, repo, text}. Returns {title, html} for a .taskpin element. */
export function taskPinHtml(t) {
  const text = (t.text || "").trim(), first = text.split("\n").map(l => l.trim()).find(Boolean) || "(no task text)";
  return `<button class="tp-head" type="button" aria-expanded="false"><span class="tp-chev">▸</span><span class="tp-n">Task ${t.order}${t.n ? ` of ${t.n}` : ""}</span><span class="tp-title">${esc(first)}</span><span class="tp-inst mono">${esc(t.instance || "")}</span></button>
    <div class="tp-body" hidden><div class="tp-meta">${esc(t.repo || "")}${t.instance ? ` · <span class="mono">${esc(t.instance)}</span>` : ""} · ${text.length.toLocaleString()} chars</div><pre class="tp-text">${esc(text)}</pre></div>`;
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
    root.addEventListener("click", e => { const b = e.target.closest("[data-more]"); if (!b || !this.sess) return; expandObs(root, this.sess.turns, +b.dataset.more); });
  }
  price(prompt, completion, cached = 0, model = null) {
    const p = (this.opts.priceFn && this.opts.priceFn(model)) || this.opts.price || { input: 0, cached_input: 0, output: 0 };
    return ((prompt - cached) * p.input + cached * p.cached_input + completion * p.output) / 1e6;
  }
  stats() {
    const s = this.sess, t = s?.tok || {};
    return { ...this.cum, task: s ? s.order : null, step: this.k, nSteps: s ? s.turns.length : 0, elapsedTask: this.elapsed, totalTask: this.total,
             resolvedTask: s ? s.result.resolved : null, done: this.done, live: !!s, kind: this.opts.kind };
  }
  emit() { if (this.opts.onStats) this.opts.onStats(this.stats()); }

  async load(q) {
    this.pause(); this.sess = null; this.prepared = false; this.done = false; this.k = 0; this.phase = 0; this.elapsed = 0; this.cumAtTaskStart = null;
    this.root.innerHTML = `<div class="empty">Loading session…</div>`;
    try { this.sess = await api(`api/session?run=${encodeURIComponent(q.run)}&arm=${encodeURIComponent(q.arm)}&cid=${encodeURIComponent(q.cid)}&order=${q.order}`); }
    catch (e) { this.root.innerHTML = `<div class="empty">${esc(e.message)}</div>`; this.emit(); return false; }
    this.render(); this.prepare(); if (this.opts.onLoad) this.opts.onLoad(this.sess, this); return true;
  }
  resetCum() { this.cum = { steps: 0, actor: 0, writer: 0, usd: 0, elapsed: 0, tasks: 0, resolved: 0, models: {} }; }

  render() {
    const s = this.sess, kind = this.opts.kind || s.kind;
    this.root.innerHTML = memoryHtml(s, kind) + issueHtml(s) + s.turns.map(t => turnHtml(t)).join("") + tailHtml(s, kind);
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
    for (let i = 1; i <= steps; i++) { if (my !== this.token) return false; el.textContent = text.slice(0, Math.round(text.length * i / steps)); await sleep(dur * 1000 / this.speed / steps); }
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
      if (this.phase === 0) { th.innerHTML = `<span class="thinking">thinking</span>`; await sleep(Math.min(plan.think * 0.3, 2.5) * 1000 / this.speed); if (my !== this.token) return false; this.phase = 1; }
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
    if (!await phase("grading: running the F2P / P2P tests", 2.5)) return false;
    this.elapsed += 2.5; this.cum.elapsed += 2.5; show('.tail[data-tail="grade"]');
    this.snapToLedger(); this.cum.tasks += 1; if (s.result.resolved) this.cum.resolved += 1; this.emit();
    const t = s.tok || {}, wp = t.writer_prompt || 0, wc = t.writer_completion || 0;
    if (box.querySelector('.tail[data-tail="note"]') || wp) {       // the writer runs after every session, incl. the last one
      const w = s.result.mem_write_secs || 4;
      if (!await phase("memory writer: distilling this session", w)) return false;
      this.elapsed += w; this.cum.elapsed += w; show('.tail[data-tail="note"]');
      this.cum.writer += wp + wc; this.cum.usd += (this.opts.priceFn && t.usd_model_writer != null) ? t.usd_model_writer : this.price(wp, wc);
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
      const s = this.sess, t = s.tok || {}; this.snapToLedger(); this.cum.tasks += 1; if (s.result.resolved) this.cum.resolved += 1;
      if (t.writer_prompt) { this.cum.writer += (t.writer_prompt || 0) + (t.writer_completion || 0); this.cum.usd += (this.opts.priceFn && t.usd_model_writer != null) ? t.usd_model_writer : this.price(t.writer_prompt || 0, t.writer_completion || 0); }
    }
    this.autoScroll(); this.emit();
  }
  showAll() { this.pause(); while (!this.done && this.sess) this.step(); }
  /** Jump to step index k (0-based) of the loaded task: everything before it is revealed and credited, the transcript
   *  scrolls there, and play() resumes from it. Seeking backwards rewinds this task's counters to its start. */
  seek(k) {
    if (!this.sess) return; this.pause();
    const n = this.sess.turns.length; k = Math.max(0, Math.min(n, k));
    if (!this.prepared || this.done || k < this.k) { if (this.cumAtTaskStart) this.cum = JSON.parse(JSON.stringify(this.cumAtTaskStart)); this.render(); this.prepare(); }
    while (this.k < k && !this.done) this.step();
    this.phase = 0;
    const t = this.sess.turns[Math.min(k, n - 1)]; const el = t && this.root.querySelector(`.turn[data-k="${t.step}"]`);
    if (el) { if (k >= n) el.scrollIntoView({ block: "end" }); else { el.hidden = false; el.querySelector(".th").textContent = ""; el.querySelector(".th").innerHTML = `<span class="thinking">thinking</span>`; const c = el.querySelector(".cmd"); if (c) c.hidden = true; el.querySelector(".obsw").hidden = true; el.scrollIntoView({ block: "start" }); } }
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
    this.on = false; this.state = null; this.order = undefined; this.sess = null; this.tasks = {}; this.n = job.custom ? 1 : 0;
    this.doneShown = new Set(); this.finished = []; this.complete = 0; this.pinned = null; this.lastFollowed = 0;
    this.obsFull = {};                                            // "order:step" -> full observation text ("show all")
    this._click = e => { const b = e.target.closest("[data-more]"); if (!b) return; const key = b.dataset.key || `${this.order}:${b.dataset.more}`;
      const o = this.root && this.root.querySelector(`[data-obs="${b.dataset.more}"][data-okey="${key}"]`) || this.root.querySelector(`[data-obs="${b.dataset.more}"]`);
      if (o && this.obsFull[key] != null) { o.textContent = this.obsFull[key]; o.classList.add("open"); } b.remove(); };
    if (root) root.addEventListener("click", this._click);
  }
  get kind() { return this.job.kind; }
  attach(root) { if (this.root) this.root.removeEventListener("click", this._click); this.root = root; if (root) { root.addEventListener("click", this._click); this.renderAll(); } }
  detach() { if (this.root) this.root.removeEventListener("click", this._click); this.root = null; }
  start() { this.on = true; this.boot(); return this; }
  stop() { this.on = false; clearTimeout(this._t); }
  schedule() { clearTimeout(this._t); if (this.on) this._t = setTimeout(() => this.tick(), this.interval); }
  async boot() {
    try { const c = await api(`api/chain/${encodeURIComponent(this.job.cid)}?run=__live__`); for (const t of c.chain.tasks) this.tasks[t.order] = { ...t, repo: c.chain.repo }; if (!this.job.custom) this.n = c.chain.n_tasks; } catch {}
    this.renderAll(); this.tick();
  }
  sessionUrl(order) { return `api/session?run=${encodeURIComponent(this.job.run_id)}&arm=${encodeURIComponent(this.job.arm)}&cid=${encodeURIComponent(this.job.cid)}&order=${order}`; }
  async tick() {
    if (!this.on) return;
    let st; try { st = await api(`api/live/${this.job.id}/state`); } catch { this.schedule(); return; }
    this.state = st; if (!this.job.custom && st.n_tasks) this.n = st.n_tasks;
    for (const o of st.done) if (!this.doneShown.has(o)) { this.doneShown.add(o); await this.finishTask(o); }
    if (st.cur !== this.order) { this.order = st.cur; this.sess = null; this.complete = 0; this.pinned = null; this.lastFollowed = 0; this.newTask(); }
    if (st.cur != null && st.has_traj) {
      try { const sess = await api(this.sessionUrl(st.cur)); if (this.order === st.cur) { this.sess = sess; this.renderTurns(); if (this.opts.onSession) this.opts.onSession(sess, this.info()); } }
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
  info() {
    const st = this.state || {}, s = this.sess, turns = s ? s.turns : [];
    let prompt = 0, completion = 0; for (const t of turns) if (t.usage) { prompt += t.usage.prompt || 0; completion += t.usage.completion || 0; }
    return { job: this.job, cid: this.job.cid, kind: this.job.kind, arm: this.job.arm, run_id: this.job.run_id, status: st.status || "running", phase: this.phase(),
             order: this.order ?? null, n: this.n, done: st.done || [], results: st.results || [], step: turns.length, prompt, completion, n_calls: st.n_calls || 0,
             elapsed: st.now && st.started ? st.now - st.started : 0, model: st.model, sess: s, finished: this.finished, log_tail: st.log_tail || "" };
  }
  task(order) { const t = this.tasks[order] || {}; const s = this.sess && this.sess.order === order ? this.sess : null;
    return { order, n: this.n, instance: t.instance || (s && s.instance) || "", repo: t.repo || (s && s.repo) || "", text: (s && s.problem_statement) || t.problem_statement || "" }; }
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
    head.innerHTML = (this.sess ? memoryHtml(this.sess, this.job.kind) : "") + (t.text ? issueHtml({ repo: t.repo, problem_statement: t.text }) : `<div class="card" style="color:var(--muted);font-size:13px">Task ${this.order}${this.n ? ` of ${this.n}` : ""} — waiting for the harness…</div>`);
  }
  renderTurns() {
    const s = this.sess; if (!s || !this.root) return;
    if (this.pinned !== s.order && this.opts.onTask) { this.pinned = s.order; this.opts.onTask(this.task(s.order), this.info()); }
    if (this.complete === 0) this.renderHead();                   // first trajectory of this task: the memory card is known now
    const turns = this.el("turns"), pending = this.el("pending"), flight = this.el("flight"); if (!turns) return;
    const n = s.turns.length; let done = 0; while (done < n && (s.turns[done].observation || s.turns[done].exit)) done++;
    for (let i = this.complete; i < done; i++) { const t = s.turns[i]; this.obsFull[`${s.order}:${t.step}`] = t.observation ? t.observation.output : null;
      turns.insertAdjacentHTML("beforeend", turnHtml(t).replace(`data-obs="${t.step}"`, `data-obs="${t.step}" data-okey="${s.order}:${t.step}"`).replace(`data-more="${t.step}"`, `data-more="${t.step}" data-key="${s.order}:${t.step}"`));
      if (this.opts.follow && t.command && t.step > this.lastFollowed) { this.lastFollowed = t.step; this.opts.follow(t, s); } }
    if (done > this.complete) { this.complete = done; this.autoScroll(); }
    pending.innerHTML = n > done ? s.turns.slice(done).map(t => turnHtml(t, { pending: true })).join("") : "";
    const ph = this.phase();
    flight.innerHTML = ph === "thinking" ? `<div class="msg assistant"><div class="who">agent · step ${n + 1}</div><div class="bubble th"><span class="thinking">thinking</span></div></div>`
      : ph === "grading" ? `<div class="card tail" data-tail="exit"><div class="hd">Session ended: <span class="meta">${esc(s.exit_status)}</span></div></div><div class="card tail phase"><span class="running">grading: resetting the test files and running the F2P / P2P tests${this.job.kind !== "none" ? ", then the memory writer distils this session" : ""}</span></div>` : "";
    if (n > done || ph === "grading") this.autoScroll();
  }
  renderStatus() {
    const box = this.el("status"); if (!box) return;
    const st = this.state, i = this.info(), ph = this.phase(), kind = this.job.kind;
    const text = { connecting: "connecting to the harness…", starting: "starting: opening the task image in Apptainer and preparing the workspace", preparing: kind !== "none" ? `task ${this.order}: the memory layer retrieves notes from the earlier sessions` : `preparing task ${this.order}`,
      loading: "loading the trajectory…", thinking: "the model is thinking", running: "the command is executing in the sandbox", grading: "grading the submission" + (kind !== "none" ? " and writing memory" : ""),
      finishing: "wrapping up", finished: st && st.status === "done" ? "finished" : `ended: ${st ? st.status : "?"}` }[ph];
    const live = st && st.status === "running";
    box.innerHTML = `<div class="hd"><span class="live-dot ${live ? "on" : ""}"></span>${live ? "Live" : "Live run " + esc(st ? st.status : "")} <span class="meta">· <span class="dot" style="background:${COLOR[kind]}"></span> ${esc(NAMES[kind] || this.job.arm)} on ${esc(this.job.cid)}${i.model ? ` · ${esc(i.model)}` : ""} · ${fmtClock(i.elapsed)}${this.n ? ` · task ${this.order ?? "–"} of ${this.n}` : ""} · ${i.done.length} graded · ${i.n_calls} ledger calls</span></div>
      <div class="lf-phase"><span class="${live ? "running" : ""}">${esc(text)}</span></div>
      <details class="sub lf-log" ${ph === "starting" || ph === "finished" && st && st.status !== "done" ? "open" : ""}><summary>harness log</summary><pre>${esc((i.log_tail || "").split("\n").slice(-8).join("\n"))}</pre></details>`;
  }
  doneHtml(sess, open) {
    const r = sess.result || {}, res = r.resolved === true ? `<span class="ok">✓ resolved</span>` : r.resolved === false ? `<span class="no">✗ not resolved</span>` : `<span class="na">not graded</span>`;
    const html = memoryHtml(sess, this.job.kind) + issueHtml(sess) + sess.turns.map(t => turnHtml(t).replace(`data-obs="${t.step}"`, `data-obs="${t.step}" data-okey="${sess.order}:${t.step}"`).replace(`data-more="${t.step}"`, `data-more="${t.step}" data-key="${sess.order}:${t.step}"`)).join("") + tailHtml(sess, this.job.kind);
    return `<details class="lf-done" ${open ? "open" : ""}><summary><span class="tp-n">Task ${sess.order}${this.n ? ` of ${this.n}` : ""}</span> ${res} <span class="meta">· ${esc(sess.exit_status || "")} · ${sess.turns.length} steps${r.secs ? ` · ${fmtClock(r.secs)}` : ""}${sess.tok ? ` · ${usd(sess.tok.usd)}` : ""}</span><span class="tp-title">${esc((sess.problem_statement || "").split("\n").map(l => l.trim()).find(Boolean) || "")}</span></summary><div class="lf-done-body">${html}</div></details>`;
  }
  async finishTask(order) {
    let sess = null; try { sess = await api(this.sessionUrl(order)); } catch {}
    if (!sess) return;
    for (const t of sess.turns) this.obsFull[`${order}:${t.step}`] = t.observation ? t.observation.output : null;
    this.finished.push({ order, sess });
    const hist = this.el("history");
    if (hist) { hist.querySelectorAll("details.lf-done[open]").forEach(d => d.open = false); hist.insertAdjacentHTML("beforeend", this.doneHtml(sess, true)); }
    if (this.order === order) { this.order = undefined; this.sess = null; const cur = this.el("cur"); if (cur) cur.innerHTML = `<div data-lf="head"></div><div data-lf="turns"></div><div data-lf="pending"></div><div data-lf="flight"></div>`; }
    this.autoScroll();
    if (this.opts.onTaskDone) this.opts.onTaskDone(order, sess, this.info());
  }
  autoScroll() { const b = this.root; if (b && b.scrollHeight - b.scrollTop - b.clientHeight < 320) b.scrollTop = b.scrollHeight; }
}
