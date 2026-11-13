// Agent view (index.html): sandbox-first layout, "New project" draft by default, ChainSWE mode (example-chain card, live runs that
// never disappear when one is selected), ⚙ pickers, playback, panes, the Files tree (whole repository, the agent's files
// highlighted; ChainSWE chain, saved live run, sandbox project, follow-the-agent), composer modes, phone width, dark scheme.
// The memory switcher: ChainSWE chains only, in the left panel under the "Your sandbox | ChainSWE chains" toggle and right above the
// example chain card; hidden in every sandbox state (also for a single frame while a page loads). In ChainSWE chains it has the same
// box, to the pixel, in every state (example chain, Mem0, a saved live run, a live run, the memory view, side panels collapsed and
// restored, ⚙ Show details) at 2000 / 1500 / 1280 / 420 px with classic scrollbars and at 420 px as a phone. It is one row of four,
// Knowledge Vault | Mem0 | MemOp | No memory: the full names where they fit (measured; on these fonts a phone's full-width row), else
// KV · Mem0 · MemOp · None with the full names in the tooltips, never running over (also at 1400 / 1366 / 1050 px, light and dark); a
// method with nothing to show is dimmed (still readable) with the reason as its tooltip. The method chip next to "Task 1 of 3": always in the sandbox; in ChainSWE chains at desktop width only while the left
// panel is collapsed (or for a Knowledge Vault version with details on); always on phones. The logo goes to New project from every state.
// Tasks are named by their short summary (the server's "summary", else the title) on the example chain card, live-run rows, sandbox task
// rows and the pinned task; instance ids only with Show details. Also: plain words in default views, ⚙ chain picker, the URL keeps the
// view (reload), ▶ Run live greys out a method already running live, the ask box waits for a live run, the boot failure card.
// Nothing shown twice: the play bar is empty while idle ("step 4/27 · 00:05" while playing), the memory view has one way back, the
// "+ New project" row says "start a new task". The right panel's tabs fit beside its » button (no scrollbar under them) at 2000 / 1500 /
// 1280 / 1050 px; the top bar's page nav and ⚙ sit where Compare and Report have them. The example chain card stays on the example chain
// when the URL opens a saved live run; a chain outside the showcase list is called "Chain" and named "repo · date". Plain words: "27 steps",
// "No memory", the Ledger's cost "at $5 / $25 per million tokens", "(no output)", "no task finished", a gone project link.
// MemOp everywhere the page offers a method: "Run with" (unticked), task chips and the rerun panel, ▶ Run live (built from the server's
// live kinds; a replayed MemOp live run, its stop and its Live runs entry), saved MemOp live runs listed and opened from their URL, the
// chain card's whole-chain tokens and savings (red where Knowledge Vault used more tokens: MemOp on pyccel, said so), the
// switcher's task rows, and MemOp's memory as one guidance document (transcript, Memory tab, Ledger, the memory view next to KV).
// Late answers and odd keys: a session answering after the viewer picked another method or switched to Your sandbox does not replace
// what is on screen; a toggle click while the page loads opens that side; Space in the memory view plays nothing; no ⇆ during a live run.
// BASE defaults to the replay server (8799). On a server with real model keys set RO=1 (or point BASE at :8766): the steps that
// call a model or start a run (Ask, chat, Run live / stop) are skipped, and every non-GET /api request the page makes is aborted.
// PREFIX= names what this makes on the replay server (e.g. PREFIX=me- → project me-uitree-…, live runs with the model me-live).
const { chromium } = require('playwright'); const BASE = process.env.BASE || 'http://127.0.0.1:8799'; const OUT = process.env.SHOTS || require('path').join(__dirname, 'shots'); require('fs').mkdirSync(OUT, { recursive: true });
const RO = process.env.RO === '1' || /:8766\b|:8765\b/.test(BASE);
const PFX = process.env.PREFIX || 'ui-', LIVE_MODEL = PFX + 'live';   // live runs get their own model name: the server refuses a 2nd run of chain + method + model
const sleep = ms => new Promise(r => setTimeout(r, ms)); const results = []; const check = (name, ok, info = '') => { results.push([ok ? 'PASS' : 'FAIL', name, info]); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${info ? ' — ' + info : ''}`); };
// The example chain is the server's (/api/showcase default_cid, else /api/meta default_cid, as the page picks it: Python-Markdown's "Table-of-contents fixes" since the list was cut to the 8
// chains where Knowledge Vault uses ≥ 20 % fewer tokens than Mem0, MemOp and No memory alike; pyccel before): every expectation about it
// is read from the server. pyccel_pyccel_20240307 is opened by its id where a check needs its numbers (Knowledge Vault used more tokens
// than MemOp there: the red figure; Mem0's task 3 stopped at the step limit) and as a chain outside the list (named "repo · date").
const PYC = 'pyccel_pyccel_20240307';
const escRe = s => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
(async () => {
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1500, height: 900 } }); const pg = await ctx.newPage(); const errs = [], writes = [], gets = [];
  if (RO) await pg.route('**/api/**', r => r.request().method() === 'GET' ? r.continue() : r.abort());   // read-only: nothing on this server is changed or run
  pg.on('request', r => { if (r.method() !== 'GET' && /\/api\//.test(r.url())) writes.push(r.method() + ' ' + r.url().replace(BASE, '')); else gets.push(r.url().replace(BASE, '')); });
  pg.on('pageerror', e => errs.push('PAGEERROR ' + e.message)); pg.on('response', r => { if (r.status() >= 400 && !/api\/(file|tree)/.test(r.url())) errs.push('HTTP ' + r.status() + ' ' + r.url().replace(BASE, '')); });
  const T = async sel => ((await pg.textContent(sel).catch(() => '')) || '').replace(/\s+/g, ' ').trim();
  const IT = async sel => ((await pg.innerText(sel).catch(() => '')) || '').replace(/\s+/g, ' ').trim();   // what is on screen (no hidden detail)
  const vis = sel => pg.isVisible(sel).catch(() => false);
  const NAME = { kv: 'Knowledge Vault', mem0: 'Mem0', memop: 'MemOp', none: 'No memory' }, KS = ['kv', 'mem0', 'memop', 'none'];
  const FULL = 'Knowledge Vault · Mem0 · MemOp · No memory', SHORT = 'KV · Mem0 · MemOp · None';
  const getJ = async u => (await pg.request.get(BASE + u)).json();
  // the example chain and what its card must say, from the server: its showcase entry, its row in the recorded run, task 1's session
  const META = await getJ('/api/meta'), SC = await getJ('/api/showcase'), DEF = SC.default_cid || META.default_cid, EXRUN = SC.run || META.default_run;
  const scOf = cid => (SC.chains || []).find(c => c.cid === cid) || null, rowOf = async cid => (await getJ(`/api/runchains?run=${EXRUN}&kinds=kv,mem0,memop,none&cid=${encodeURIComponent(cid)}`)).chains.find(c => c.cid === cid);
  const cidDate = cid => { const m = /_(\d{4})(\d{2})(\d{2})(?:_|$)/.exec(cid || ''); return m ? `${m[1]}-${m[2]}-${m[3]}` : ''; };
  const DROW = await rowOf(DEF), DSC = scOf(DEF), N = DROW.n_tasks, DNAME = DSC ? DSC.title : DROW.repo + (cidDate(DEF) ? ' · ' + cidDate(DEF) : '');
  const DS1 = await getJ(`/api/session?run=${EXRUN}&arm=${DROW.arms.kv.arm || 'mem_fusednote'}&cid=${DEF}&order=${DROW.arms.kv.sessions[0].order}`);
  // a chain card's token line and savings as the page words them (tokens summed over the row's sessions; −x % / +x % per method; worse = red)
  const cardNums = row => { const t = {}; for (const k of KS) { const a = row.arms[k]; if (a && a.sessions && a.sessions.length) t[k] = a.sessions.reduce((x, s) => x + (s.tokens || 0), 0); }
    const big = Math.max(0, ...Object.values(t)) >= 1e6, f = v => big ? (v / 1e6).toFixed(2) + 'M' : v >= 1e5 ? Math.round(v / 1e3) + 'K' : (v / 1e3).toFixed(1) + 'K';
    const part = k => row.arms[k].sessions.length < row.n_tasks ? ` (${row.arms[k].sessions.length}/${row.n_tasks} tasks)` : '';
    const toks = KS.filter(k => t[k] != null).map(k => (k === 'kv' ? 'KV' : NAME[k]) + ' ' + f(t[k]) + part(k)).join(' · ');
    const others = KS.filter(k => k !== 'kv' && t[k]), pct = k => { const d = Math.round(100 * (1 - t.kv / t[k])); return d >= 0 ? `−${d} %` : `+${-d} %`; };
    return { toks, save: others.map(k => `${pct(k)} vs ${k === 'none' ? 'no memory' : NAME[k]}`).join(' · '), worse: others.filter(k => t.kv > t[k]) }; };
  const DCARD = cardNums(DROW);
  // the memory switcher: "kv* mem0 none~" = KV watched, No memory dimmed (nothing to show; its tooltip says why)
  const swOf = p => p.$$eval('#armseg button', e => e.map(x => `${x.dataset.k}${x.getAttribute('aria-pressed') === 'true' ? '*' : ''}${x.hasAttribute('data-na') ? '~' : ''}`).join(' ')), sw = () => swOf(pg);
  const ticked = () => pg.$$eval('#taskarms input[data-k]:checked', e => e.map(x => x.dataset.k).join(','));
  const tip = k => pg.getAttribute(`#armseg button[data-k="${k}"]`, 'title');
  // where the switcher is (page coordinates, scrolled to the top first), the toggle above it and the example chain card below it, whether
  // a label overflows its button, the labels, and the method chip next to "Task k of n" when it shows
  const armOf = p => p.evaluate(() => { scrollTo(0, 0); const f = s => { const e = document.querySelector(s); if (!e || !(e.offsetWidth || e.offsetHeight)) return null; const r = e.getBoundingClientRect(); return [r.x + scrollX, r.y + scrollY, r.width, r.height].map(v => +v.toFixed(2)); };
    const seg = document.querySelector('#armseg'), shown = !!(seg && (seg.offsetWidth || seg.offsetHeight)), chip = [...document.querySelectorAll('#chathead .kchip')].find(e => e.offsetWidth || e.offsetHeight);
    return { arm: f('#armseg'), src: f('#srcseg'), card: f('#chains .excard'), inLeft: !!(seg && seg.closest('aside.left')), inHeader: !!document.querySelector('header.top #armseg'),
      over: shown && ([...seg.querySelectorAll('button')].some(x => x.scrollWidth > x.clientWidth + 1) || seg.scrollWidth > seg.clientWidth + 1),
      labels: shown ? [...seg.querySelectorAll('button')].map(x => x.innerText.trim()).join(' · ') : '', chip: chip ? chip.innerText.trim() : null,
      oneRow: shown && new Set([...seg.querySelectorAll('button')].map(x => Math.round(x.getBoundingClientRect().top))).size === 1,
      inPanel: shown && seg.getBoundingClientRect().right <= document.querySelector('aside.left').getBoundingClientRect().right + 0.5,
      fullFits: shown ? (() => { const had = seg.classList.contains('short'); seg.classList.remove('short'); seg.classList.add('measure'); const cs = getComputedStyle(seg);
        const room = seg.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight), need = [...seg.querySelectorAll('button')].reduce((a, x) => a + x.getBoundingClientRect().width, 0);
        seg.classList.remove('measure'); if (had) seg.classList.add('short'); return need <= room - 2; })() : null }; }), arm = () => armOf(pg);
  // the labels the switcher must show: the four full names where they fit on its one row (measured), else KV · Mem0 · MemOp · None
  const labelsOk = a => !!a.arm && a.labels === (a.fullFits ? FULL : SHORT) && !a.over && a.oneRow && a.inPanel;
  const sameBox = (a, b) => !!a.arm && JSON.stringify(a.arm) === JSON.stringify(b.arm);
  const placed = a => !!a.arm && !!a.src && !!a.card && a.inLeft && !a.inHeader && a.arm[1] >= a.src[1] + a.src[3] && a.arm[1] + a.arm[3] <= a.card[1] && a.card[1] - (a.arm[1] + a.arm[3]) < 16;
  // WCAG contrast of each switcher label on the switcher's background, and the opacity of its dot (mouse moved off it first: no hover)
  const armContrast = async () => { await pg.mouse.move(700, 600); await sleep(250); return pg.evaluate(() => { const lum = c => { const [r, g, b] = c.match(/[\d.]+/g).slice(0, 3).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
    const bg = getComputedStyle(document.querySelector('#armseg')).backgroundColor, ratio = c => { const x = lum(c), y = lum(bg); return +((Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)).toFixed(2); };
    return Object.fromEntries([...document.querySelectorAll('#armseg button')].map(x => [x.dataset.k, { na: x.hasAttribute('data-na'), pressed: x.getAttribute('aria-pressed') === 'true', c: getComputedStyle(x).color, cr: ratio(getComputedStyle(x).color), dot: +getComputedStyle(x.querySelector('.dot')).opacity }])); }); };
  const gear = async on => { if ((await pg.$eval('#gearpanel', e => e.hidden)) === on) await pg.click('#gearbtn'); await sleep(150); };
  const pick = async (sel, v) => { await gear(true); await pg.selectOption(sel, v); await sleep(200); await pg.keyboard.press('Escape'); };
  const details = async on => { await gear(true); if ((await pg.isChecked('#details')) !== on) await pg.click('#details'); await pg.keyboard.press('Escape'); await sleep(250); };
  const live = () => pg.$eval('#playbar', e => e.classList.contains('live'));
  const query = () => Object.fromEntries(new URL(pg.url()).searchParams);
  const head = (re, p = pg) => p.waitForFunction(r => new RegExp(r).test(document.querySelector('#chathead').textContent), re.source, { timeout: 30000 }).catch(() => {});
  // words a first-time visitor should not meet in a default view (Show details off): benchmark and harness terms, raw ids
  const JARGON0 = /\bF2P\b|\bP2P\b|\brc [01]\b|LimitsExceeded|BadRequestError|pyccel_pyccel_|not graded|injects|```|ledger calls|mem_fusednote|mem_factsall/;
  const JARGON = new RegExp(JARGON0.source + '|' + escRe(DEF) + '|' + escRe(PYC));   // + the chains' raw ids
  // (what the agent typed and what its commands printed are its own content: commands, their output, diffs and file views are left out)
  const jargon = async () => { const t = await pg.evaluate(() => { const st = document.createElement('style'); st.textContent = '.cmd, .obs, .diff, pre.view { display: none !important; }'; document.head.appendChild(st);
    const x = document.body.innerText; st.remove(); return x; }); return (t.match(new RegExp(JARGON.source, 'g')) || []).join(', '); };
  // a task's name as the page should show it: the server's summary, else its title (from the row, else the chain's task list)
  const chainTasks = {}; const tasksOf = async cid => chainTasks[cid] || (chainTasks[cid] = (await getJ(`/api/chain/${encodeURIComponent(cid)}?run=__live__`)).chain.tasks);
  const nameFor = async (cid, s) => (s && (s.summary || s.title)) || (t => t && (t.summary || t.title))((await tasksOf(cid)).find(x => x.order === s.order)) || '';
  // Files tab of the open session: the whole repository as a tree; edited files green, files named in a command accent + count,
  // files only seen in command output lighter; folders on the way to edited / read files open, the rest closed with a file count
  // (a closed folder's badge counts the agent's files inside it); only open folders' children in the DOM; one tab stop; toggle,
  // keyboard, file view + back, the scroll position kept across tabs; the tab's count = the summary
  const treeInfo = () => pg.evaluate(() => { const rows = [...document.querySelectorAll('#ftree .tr')], inTree = rows.filter(r => r.dataset.f != null && !r.matches('.miss'));
    const lg = [...document.querySelectorAll('.fsum .lg')].map(x => x.textContent.trim()), num = re => +((lg.map(x => re.exec(x)).find(Boolean) || [, 0])[1]);
    return { src: (document.querySelector('.ftools .src') || {}).textContent || '', rows: rows.length, files: rows.filter(r => r.dataset.f != null).length,
      ed: inTree.filter(r => r.matches('.ed')).length, rd: inTree.filter(r => r.matches('.rd')).length, seen: inTree.filter(r => r.matches('.seen')).length,
      closedRel: rows.filter(r => r.matches('.d:not(.grp)') && r.getAttribute('aria-expanded') === 'false').reduce((a, r) => a + +((r.querySelector('.badge.rel') || {}).textContent || 0), 0),
      nEd: num(/^(\d+) edited$/), nRd: num(/^(\d+) read$/), nSeen: num(/^(\d+) seen in output$/), nMiss: num(/^(\d+) not (?:in repo|placed)$/),
      tab: +((document.querySelector('#cnt-files') || {}).textContent || 0), stops: rows.filter(r => r.tabIndex === 0).length, buttons: rows.every(r => r.tagName === 'BUTTON'),
      edBadges: rows.filter(r => r.matches('.f.ed')).every(r => /^(edited|new|deleted)$/.test((r.querySelector('.badge.ed') || {}).textContent || '')),
      rdCounts: rows.filter(r => r.matches('.f.rd, .f.seen')).every(r => /^\d+×$/.test((r.querySelector('.badge.mc') || {}).textContent || '')),
      relOpen: rows.filter(r => r.matches('.d.hasrel')).every(r => r.getAttribute('aria-expanded') === 'true' && r.querySelector('.badge.rel')),
      othersClosed: rows.filter(r => r.matches('.d:not(.hasrel):not(.grp)')).every(r => r.getAttribute('aria-expanded') === 'false' && /^\d[\d,]*$/.test((r.querySelector('.n') || {}).textContent || '')),
      only: rows.filter(r => r.dataset.f != null && !r.querySelector('.badge.nr')).every(r => r.matches('.ed, .rd, .seen')) }; });
  const treeReady = () => pg.waitForFunction(() => document.querySelector('#ftree') && !/Loading the repository/.test(document.querySelector('.ftools .src').textContent), null, { timeout: 60000 }).catch(() => {});
  const fileReady = () => pg.waitForFunction(() => !/loading…/.test(document.querySelector('#pane').textContent), null, { timeout: 30000 }).catch(() => {});
  async function filesTree(what, { listing = /\d[\d,]* files?/, deep = true } = {}) {
    await pg.click('#tabs button[data-t="files"]'); await treeReady(); await sleep(200);
    let t = await treeInfo(); const full = !/No file listing/.test(t.src);
    check(`Files (${what}): tree of the whole repository`, full && listing.test(t.src) && t.buttons, t.src);
    check(`Files (${what}): highlighted rows = the summary (${t.nEd} edited, ${t.nRd} read, ${t.nSeen} seen in output)`, t.ed === t.nEd && t.rd === t.nRd && t.ed + t.rd + t.seen + t.closedRel === t.nEd + t.nRd + t.nSeen && t.ed + t.rd > 0 && t.edBadges && t.rdCounts, JSON.stringify(t));
    check(`Files (${what}): the tab's count = the summary (${t.tab})`, t.tab === t.nEd + t.nRd + t.nSeen + t.nMiss, `tab ${t.tab}, summary ${t.nEd}+${t.nRd}+${t.nSeen}+${t.nMiss}`);
    check(`Files (${what}): folders with edited / read files open, the others closed with a file count`, t.relOpen && t.othersClosed);
    check(`Files (${what}): the tree is one tab stop`, t.stops === 1, `${t.stops} rows with tabindex 0`);
    if (!deep) return;
    const n = +((/([\d,]+) files?/.exec(t.src) || [, '0'])[1].replace(/,/g, '')); check(`Files (${what}): lazy (only open folders' rows in the DOM)`, t.rows < n, `${t.rows} rows for ${n} files`);
    await pg.focus('#ftonly'); await pg.keyboard.press('Tab'); const inT = await pg.evaluate(() => !!document.activeElement.closest('#ftree'));
    // an edited row reached with the keyboard shows a focus ring (its inset highlight would otherwise hide it)
    const ringEd = await pg.evaluate(() => { const r = document.querySelector('#ftree .tr.f.ed'); if (!r) return null; r.focus(); const cs = getComputedStyle(r); return { vis: r.matches(':focus-visible'), style: cs.outlineStyle, w: parseFloat(cs.outlineWidth) }; });
    if (ringEd) check(`Files (${what}): an edited row reached with the keyboard shows a focus ring`, ringEd.vis && ringEd.style !== 'none' && ringEd.w >= 2, JSON.stringify(ringEd));
    await pg.focus('#ftonly'); await pg.keyboard.press('Tab');
    await pg.keyboard.press('Tab'); const outT = await pg.evaluate(() => !document.activeElement.closest('#ftree'));
    check(`Files (${what}): Tab enters the tree, the next Tab leaves it (${t.rows} rows)`, inT && outT);
    const d = await pg.$('#ftree .tr.d[aria-expanded="false"]');
    if (d) { const k = await d.getAttribute('data-d'), r0 = (await treeInfo()).rows; await d.click(); await sleep(150); const r1 = (await treeInfo()).rows;
      const sel = `#ftree .tr.d[data-d="${k}"]`; check(`Files (${what}): a closed folder opens on click`, r1 > r0 && (await pg.getAttribute(sel, 'aria-expanded')) === 'true', `${r0}->${r1}`);
      await pg.focus(sel); await pg.keyboard.press('Enter'); await sleep(150); check(`Files (${what}): Enter closes it again, focus stays`, (await pg.getAttribute(sel, 'aria-expanded')) === 'false' && (await treeInfo()).rows === r0 && await pg.evaluate(k => document.activeElement.dataset.d === k, k));
      await pg.keyboard.press('ArrowRight'); await sleep(150); check(`Files (${what}): → opens, ↓ moves to its first child`, (await pg.getAttribute(sel, 'aria-expanded')) === 'true' && await (async () => { await pg.keyboard.press('ArrowDown'); return pg.evaluate(k => { const a = document.activeElement; return a.classList.contains('tr') && a.dataset.d !== k; }, k); })());
      check(`Files (${what}): the row the keyboard is on is the tab stop`, await pg.evaluate(() => document.activeElement.tabIndex === 0 && document.querySelectorAll('#ftree .tr[tabindex="0"]').length === 1));
      // the open folder must still be open after a file view and "← files"
      const f = await pg.$('#ftree .tr.f.ed') || await pg.$('#ftree .tr.f.rd'), fp = await f.getAttribute('data-f'); await f.click(); await fileReady();
      const pane = await T('#pane'); check(`Files (${what}): a file opens in the file view`, /← files/.test(pane) && pane.includes(fp) && /(edited|created|deleted) by the agent|mentioned \d+×/.test(pane) && (await pg.$$('#pane pre.view, #pane .obs, #pane .diff')).length > 0, pane.slice(0, 140));
      if (/(edited|created) by the agent/.test(pane)) check(`Files (${what}): an edited file shows its diff`, !!(await pg.$('#pane .diff')));
      await pg.screenshot({ path: OUT + `/19_index_files_${what}_file.png` });
      await pg.click('#pane #back'); await sleep(200); check(`Files (${what}): ← files returns to the tree with the folders as they were`, (await pg.getAttribute(sel, 'aria-expanded')) === 'true' && await pg.evaluate(p => document.activeElement.dataset.f === p, fp));
      await pg.click(sel); await sleep(100); }
    // (a scroll position that keeps the current row — the file opened last — in view: a tree drawn anew brings that row back into view)
    const y = await pg.$eval('#pane', p => { const max = p.scrollHeight - p.clientHeight, c = p.querySelector('#ftree .tr.cur'); let y = Math.min(400, max);
      if (c) { const top = c.getBoundingClientRect().top - p.getBoundingClientRect().top + p.scrollTop, h = c.offsetHeight;
        if (top < y || top + h > y + p.clientHeight) y = Math.max(0, Math.min(max, top + h - p.clientHeight + 8)); if (!y) y = Math.max(0, Math.min(max, top - 8)); }
      p.scrollTop = y; return p.scrollTop; });
    await pg.click('#tabs button[data-t="changes"]'); await sleep(150); await pg.click('#tabs button[data-t="files"]'); await sleep(300);
    const y2 = await pg.$eval('#pane', p => p.scrollTop); check(`Files (${what}): the tree's scroll position is kept across tabs`, Math.abs(y2 - y) <= 2, `${y} -> ${y2}`);
    if (t.nEd > 1) { const hops = [];
      for (let i = 0; i < t.nEd; i++) { await pg.click('.fsum [data-jump="ed"]'); await sleep(200);
        hops.push(await pg.evaluate(() => { const c = document.querySelector('#ftree .tr.cur'), p = document.querySelector('#pane'); if (!c) return null; const r = c.getBoundingClientRect(), q = p.getBoundingClientRect(); return { f: c.dataset.f, ed: c.matches('.ed'), inView: r.top >= q.top && r.bottom <= q.bottom }; })); }
      check(`Files (${what}): "${t.nEd} edited" goes to each edited file in turn, in view`, hops.every(x => x && x.ed && x.inView) && new Set(hops.map(x => x.f)).size === t.nEd, JSON.stringify(hops)); }
    await pg.click('#ftonly'); await sleep(150); t = await treeInfo(); check(`Files (${what}): "Only files the agent touched" shows just those`, t.only && t.files > 0 && (await pg.getAttribute('#ftonly', 'aria-pressed')) === 'true', `${t.files} files`);
    await pg.screenshot({ path: OUT + `/19_index_files_${what}_only.png` });
    await pg.click('#ftonly'); await sleep(150); check(`Files (${what}): toggle off = the full tree again`, (await pg.getAttribute('#ftonly', 'aria-pressed')) === 'false' && !(await treeInfo()).only);
    await pg.screenshot({ path: OUT + `/19_index_files_${what}.png` });
  }
  const epReady = (p = pg) => p.waitForFunction(() => !/checking/.test(document.querySelector('#epstatus').textContent), null, { timeout: 60000 });
  // ▶ Run live with Knowledge Vault from the page (⚙ Model = this suite's own name); returns when the page watches it
  const runLiveKV = async (p = pg) => { await p.click('#gearbtn'); await p.selectOption('#provider', 'local').catch(() => {}); await p.fill('#model', LIVE_MODEL); await p.dispatchEvent('#model', 'change'); await p.keyboard.press('Escape');
    await p.click('#runlive'); await sleep(500); await p.click('#runmenu button[data-k="kv"]'); await p.waitForFunction(() => document.querySelector('#playbar').classList.contains('live'), null, { timeout: 30000 }).catch(() => {}); };
  const stopMine = async () => { for (const j of (await getJ('/api/live')).filter(j => j.status === 'running' && !j.custom && j.model === LIVE_MODEL)) await pg.request.post(BASE + `/api/live/${j.id}/stop`);
    for (let i = 0; i < 60 && (await getJ('/api/live')).some(j => j.status === 'running' && !j.custom && j.model === LIVE_MODEL); i++) await sleep(500); };
  console.log(`BASE ${BASE}${RO ? ' (read-only: no model calls, no runs)' : ''}`);
  if (!RO) await stopMine();                                        // a run this suite left going (an earlier run that was cut off)

  // ---------------------------------------------------------------- boot: Your sandbox → New project
  await pg.goto(BASE + '/'); await pg.waitForSelector('#startcard', { timeout: 30000 }); await epReady(); await sleep(400);
  check('boot: draft "New project" with the start card', /New project/.test(await T('#chathead')) && await vis('#startcard'), await T('#chathead'));
  check('boot: tab title "Agent · Knowledge Vault", favicon inline (no /favicon.ico request)', (await pg.title()) === 'Agent · Knowledge Vault' && !gets.some(u => /favicon\.ico/.test(u)) && /^data:image\/svg/.test(await pg.getAttribute('link[rel="icon"]', 'href')), await pg.title());
  check('boot: source toggle = Your sandbox (left, pressed) | ChainSWE chains (right)', (await pg.$$eval('#srcseg button', e => e.map(x => x.dataset.src + ':' + x.getAttribute('aria-pressed')))).join(',') === 'custom:true,chains:false');
  check('boot: "+ New project" row first and highlighted', await pg.$eval('#chains > :first-child', e => e.matches('.chain.newrow.open[data-new]')));
  check('boot: the "+ New project" row says what it does (the start card lists the four sources)', (await IT('#chains .newrow')) === '+ New project start a new task', await IT('#chains .newrow'));
  check('boot: four start-from cards, Empty folder pressed', (await pg.$$eval('#srcpick [data-from]', e => e.map(x => x.dataset.from + ':' + x.getAttribute('aria-pressed')))).join(',') === 'empty:true,upload:false,git:false,image:false');
  const A0 = await arm();
  check('boot: no memory switcher in the sandbox (not in the top bar either)', !A0.arm && !A0.inHeader && A0.inLeft, JSON.stringify(A0));
  check('boot: playbar hidden in the draft', !(await vis('#playbar')));
  check('boot: no run selector, no savings chip, no mode select, no old create form', !(await pg.$('#run')) && !(await pg.$('#savings')) && !(await pg.$('#cmode')) && !(await pg.$('#newproj, #np-create')));
  check('boot: Run with = KV ✓, Mem0 ✓, MemOp ☐, No memory ☐ (in Compare\'s order)', (await pg.$$eval('#taskarms input[data-k]', e => e.map(x => x.dataset.k + ':' + x.checked))).join(',') === 'kv:true,mem0:true,memop:false,none:false', await pg.$$eval('#taskarms input[data-k]', e => e.map(x => x.dataset.k + ':' + x.checked).join(',')));
  check('boot: composer runs a task, focus in the task box, plain placeholder', /Run/.test(await T('#send')) && (await pg.evaluate(() => document.activeElement && document.activeElement.id)) === 'q' && (await pg.getAttribute('#q', 'placeholder')) === 'Describe a task, e.g. add a word-count script', await pg.getAttribute('#q', 'placeholder'));
  check('boot: the URL of the draft is plain "/"', new URL(pg.url()).search === '', pg.url());
  check('boot: Compare link from the draft goes to plain compare', await pg.$eval('.pnav a[href^="compare"]', a => { a.dispatchEvent(new Event('mouseenter')); return a.getAttribute('href'); }) === 'compare');
  // "Run with" (the sandbox picks its methods here; no switcher): ticking starts nothing; the tooltips are the server's plain wording
  const w0 = writes.length;
  await pg.click('#taskarms input[data-k="none"]'); await pg.click('#taskarms input[data-k="kv"]'); await sleep(150); const t1 = await ticked();
  await pg.click('#taskarms input[data-k="kv"]'); await pg.click('#taskarms input[data-k="none"]'); await sleep(150);
  check('Run with: ticking and unticking starts nothing, the switcher stays hidden', t1 === 'mem0,none' && (await ticked()) === 'kv,mem0' && writes.length === w0 && !(await arm()).arm, `${t1} → ${await ticked()} · ${writes.slice(w0).join(', ')}`);
  const rwt = await pg.$$eval('#taskarms label.armt', e => e.map(x => x.title));
  check('Run with: tooltips in plain words ("…passes them to the next task", not "injects"); MemOp "keeps one guidance document, rewritten after every task"', rwt.length === 4 && /^Mem0: .*passes them to the next task/.test(rwt[1]) && /^MemOp: keeps one guidance document, rewritten after every task/.test(rwt[2]) && !rwt.some(x => /inject/i.test(x)), rwt.join(' | '));
  await pg.screenshot({ path: OUT + '/10_index_draft_runwith.png', clip: { x: 0, y: 0, width: 1500, height: 900 } });
  for (const [k, sel] of [['upload', '[data-pane="upload"]'], ['git', '[data-pane="git"]'], ['image', '[data-pane="image"]']]) { const btn = await pg.$(`[data-from="${k}"]`); if (await btn.isDisabled()) { check(`start-from ${k}: disabled on this server`, true); continue; } await btn.click(); await sleep(150); check(`start-from ${k}: shows its pane`, await vis(sel) && (await pg.$$eval('.sc-pane:not([hidden])', e => e.length)) === 1); }
  check('Git pane: placeholder + note', (await pg.getAttribute('#sc-git', 'placeholder')) === 'https://github.com/owner/repo' && /nothing is pushed back/.test(await T('[data-pane="git"]')));
  check('Example repo: chain images listed by repo name', (await pg.$$eval('#sc-image option', e => e.map(o => o.textContent))).every(t => !/ — /.test(t)), (await pg.$$eval('#sc-image option', e => e.map(o => o.textContent))).slice(0, 3).join(', '));
  await pg.click('[data-from="empty"]');
  await pg.screenshot({ path: OUT + '/10_index_draft.png' });
  // ⚙ order; its example-chain section only in ChainSWE mode
  await gear(true); const heads = await pg.$$eval('#gearpanel section h4', e => e.map(x => x.textContent.trim()));
  check('⚙ sections in order', heads.join(' | ') === 'Example chain (ChainSWE) | Model endpoint | Sandbox tasks | Knowledge Vault | Cost | Playback', heads.join(' | '));
  check('⚙ in the sandbox: no example-chain section', !(await vis('#gs-chain')));
  check('⚙ step limit default 40, chat-mode checkbox present', (await pg.$eval('#tasksteps', e => e.value)) === '40' && !!(await pg.$('#chatmode')));
  await pg.screenshot({ path: OUT + '/11_index_gear.png' }); await gear(false);
  // while a page loads, the switcher never shows in the sandbox (not for one frame), and in ChainSWE chains never dims a method that the
  // loaded page does not dim. Sampled on every frame; the projects list is held back 1.2 s, as on a slow network. GET-only context.
  { const bctx = await b.newContext({ viewport: { width: 1500, height: 900 } });
    await bctx.addInitScript(() => { window.__swRun = ms => { const t0 = performance.now(), gen = window.__swGen = (window.__swGen || 0) + 1; window.__sw = [];   // a new run stops the last
        const tick = () => { if (gen !== window.__swGen) return; const seg = document.querySelector('#armseg'), bs = [...document.querySelectorAll('#armseg button')], on = !!(seg && (seg.offsetWidth || seg.offsetHeight));
          const s = (on ? '' : 'hidden ') + bs.map(x => `${x.dataset.k}${x.getAttribute('aria-pressed') === 'true' ? '*' : ''}${x.hasAttribute('data-na') ? '~' : ''}`).join(' '), l = __sw[__sw.length - 1];
          if (bs.length && (!l || l[1] !== s)) __sw.push([Math.round(performance.now() - t0), s]); if (performance.now() - t0 < ms) requestAnimationFrame(tick); }; requestAnimationFrame(tick); }; __swRun(15000); });
    const bp = await bctx.newPage();
    await bp.route('**/api/**', async r => { if (r.request().method() !== 'GET') return r.abort(); if (/\/api\/custom\/projects/.test(r.request().url())) await sleep(1200); return r.continue(); });
    bp.on('request', r => { if (r.method() !== 'GET' && /\/api\//.test(r.url())) writes.push(r.method() + ' ' + r.url().replace(BASE, '')); }); bp.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
    const dimmed = s => s.split(' ').filter(x => x.endsWith('~')).map(x => x.replace(/[*~]/g, ''));
    const frames = async () => { await sleep(700); return bp.evaluate(() => __sw); };
    const noFlash = async what => { const fr = await frames(), fin = dimmed(fr[fr.length - 1][1]), bad = fr.filter(([, s]) => !/^hidden /.test(s) && dimmed(s).some(k => !fin.includes(k)));
      check(`${what}: the switcher never dims a method the loaded page does not (every frame, projects list held back 1.2 s)`, fr.length > 0 && !bad.length && !/^hidden /.test(fr[fr.length - 1][1]), (bad.length ? 'dimmed meanwhile: ' + bad.map(([t, s]) => `${t} ms "${s}"`).join(', ') : fr.map(([t, s]) => `${t} ms "${s}"`).join(' → '))); };
    const neverShown = async what => { const fr = await frames(), shown = fr.filter(([, s]) => !/^hidden /.test(s));
      check(`${what}: the switcher is hidden on every frame`, fr.length > 0 && !shown.length, shown.map(([t, s]) => `${t} ms "${s}"`).join(', ') || `${fr.length} frames hidden`); };
    const pk = (await getJ('/api/custom/projects')).find(p => (p.arms?.kv?.orders || []).length && !/^(pol|r2)-/.test(p.name));
    for (const [u, ready, sandbox] of [['/', '#startcard', true], ['/?src=chains', '.excard .sess.sel', false], ['/?view=memory', '#transcript .mc-wrap', false], ...(pk ? [['/?cid=' + encodeURIComponent(pk.name), null, true]] : [])]) {
      await bp.goto(BASE + u); if (ready) await bp.waitForSelector(ready, { timeout: 30000 }); else await head(/Task \d/, bp);
      if (sandbox) await neverShown(`loading ${u}`); else await noFlash(`loading ${u}`); }
    await bp.goto(BASE + '/?src=chains'); await bp.waitForSelector('.excard .sess.sel', { timeout: 30000 }); await sleep(300);
    await bp.evaluate(() => { document.querySelector('#srcseg button[data-src="custom"]').click(); __swRun(15000); });   // sampled from the first frame after the click
    await bp.waitForSelector('#startcard', { timeout: 30000 }); await neverShown('ChainSWE chains → Your sandbox');
    await bctx.close(); }

  // ---------------------------------------------------------------- ChainSWE mode: the example chain card
  await pg.click('#srcseg button[data-src="chains"]'); await pg.waitForSelector('#taskpin:not([hidden]) .tp-head', { timeout: 30000 }); await sleep(600);
  const card = await T('.excard');
  check(`chains: card "Example chain", title, repo and blurb (${DEF})`, /^Example chain/i.test(await IT('.excard .ex-top')) && !!DSC && card.includes(DSC.title) && card.includes(`${DROW.repo} · ${N} tasks`) && card.includes(DSC.blurb), card.slice(0, 160));
  check('chains: whole-chain tokens with KV first, MemOp before No memory (the recorded run\'s sums)', card.includes('Tokens for the whole chain: ' + DCARD.toks), `want "${DCARD.toks}" · ${(/Tokens for the whole chain:[^−+]*/.exec(card) || [''])[0]}`);
  check('chains: KV emphasised', (await T('.excard .ex-kv')) === DCARD.toks.split(' · ')[0], await T('.excard .ex-kv'));
  check(`chains: savings line, MemOp included ("${DCARD.save}")`, (await T('.excard .ex-save')) === DCARD.save, await T('.excard .ex-save'));
  // an example chain is one where Knowledge Vault used fewer tokens than every other method: nothing in red (pyccel's red MemOp figure below)
  { const sv = await pg.$$eval('.excard .ex-save span', e => e.map(x => ({ t: x.textContent.trim(), worse: x.classList.contains('worse'), tip: x.title })));
    check(`chains: the savings in red exactly where Knowledge Vault used more tokens (${DCARD.worse.join(', ') || 'none here'}), plain tooltips`, sv.length === DCARD.save.split(' · ').length && sv.every(x => x.worse === DCARD.worse.some(k => x.t.endsWith(k === 'none' ? 'no memory' : NAME[k]))) && sv.every(x => /^Knowledge Vault used \d+ % (fewer|more) tokens than .+ on this chain$/.test(x.tip)), JSON.stringify(sv)); }
  // the card's numbers are the recorded run's: MemOp's whole-chain tokens and tasks resolved = /api/showcase = the sum over /api/runchains sessions
  { const st = DSC && DSC.stats.memop, a = DROW.arms.memop;
    const sum = a ? a.sessions.reduce((x, s) => x + s.tokens, 0) : null, shown = (/MemOp ([\d.]+[MK])/.exec(card) || [])[1], tipT = await pg.getAttribute('.excard .ex-tok', 'title');
    check(`chains: the card's MemOp figure is the recorded run's (${st ? st.tokens.toLocaleString() : '?'} tokens = Σ sessions; resolved ${st ? st.resolved : '?'} of ${N} in the tooltip)`, !!st && st.tokens === sum && DCARD.toks.includes('MemOp ' + shown) && new RegExp(`MemOp ${st.resolved} of ${N}`).test(tipT || ''), `showcase ${st && st.tokens} · runchains ${sum} · card ${shown} · ${tipT}`); }
  { const s1 = DROW.arms.kv.sessions[0], mark = s1.resolved === true ? '✓' : s1.resolved === false ? '✗' : '·', n1 = s1.n_steps;
    check(`chains: ${DROW.arms.kv.sessions.length} KV task rows with ✓/✗, steps, $ ("${n1} steps", not "${n1} st")`, (await pg.$$('.excard .sess[data-ex]')).length === DROW.arms.kv.sessions.length && new RegExp(`${mark}\\s*${n1} steps? · \\$`).test(await T(`.excard .sess[data-ex="${s1.order}"]`)), await T(`.excard .sess[data-ex="${s1.order}"]`)); }
  check('chains: a task row\'s tooltip is its name (no instance id)', !/__/.test(await pg.getAttribute('.excard .sess[data-ex="1"] .inst', 'title')), await pg.getAttribute('.excard .sess[data-ex="1"] .inst', 'title'));
  check('chains: the ⚙ button is named "Settings" (type button), as on Compare and Report', (await pg.getAttribute('#gearbtn', 'aria-label')) === 'Settings' && (await pg.getAttribute('#gearbtn', 'type')) === 'button');
  check('chains: task 1 open, KV', new RegExp(`Task 1 of ${N}`).test(await T('#chathead')) && /Knowledge Vault/.test(await T('#chathead')), await T('#chathead'));
  check('chains: no list of 100 chains', (await pg.$$('#chains .chain[data-cid]')).length === 0);
  const C0 = await arm();
  check('chains: the switcher is in the left panel, under the toggle, right above the example chain card; Knowledge Vault | Mem0 | MemOp | No memory', placed(C0) && (await pg.$$eval('#armseg button', e => e.map(x => x.dataset.k))).join(',') === 'kv,mem0,memop,none', JSON.stringify(C0));
  check(`chains (1500 px): one row of four, the full names where they fit, else the short ones (measured: ${C0.fullFits ? 'full fit' : 'short'})`, labelsOk(C0), `${C0.labels} · fullFits ${C0.fullFits} · over ${C0.over} · one row ${C0.oneRow}`);
  check('chains: each switcher tooltip names its method in full', (await pg.$$eval('#armseg button', e => e.map(x => x.title.split('\n').pop()))).every((t, i) => t.startsWith(['Knowledge Vault', 'Mem0', 'MemOp', 'No memory'][i] + ' ') || t.startsWith(['Knowledge Vault', 'Mem0', 'MemOp', 'No memory'][i] + ':')), (await pg.$$eval('#armseg button', e => e.map(x => x.title.split('\n').pop()))).join(' | '));
  { const want = KS.map(k => k + (k === 'kv' ? '*' : '') + ((DROW.arms[k]?.sessions || []).some(s => s.order === 1) ? '' : '~')).join(' ');
    check(`chains: on the example chain every method with a task-1 run is offered, the others dimmed (${want})`, (await sw()) === want, await sw()); }
  check('chains: the chip naming the open method is dropped at desktop width (the switcher names it)', await pg.$eval('#chathead .kchip', e => getComputedStyle(e).display === 'none' && /Knowledge Vault/.test(e.textContent)).catch(() => false));
  check('chains: playbar + Run live, composer asks about the session', await vis('#playbar') && await vis('#runlive') && (await T('#send')) === 'Send' && /^Ask about this session — e\.g\. why did task 2 fail\?$/.test(await pg.getAttribute('#q', 'placeholder')) && !(await vis('#taskarms')));
  check('chains: Compare link → compare?cid=<showcase chain>', await pg.$eval('.pnav a[href^="compare"]', a => { a.dispatchEvent(new Event('mouseenter')); return a.getAttribute('href'); }) === 'compare?cid=' + DEF);
  check('chains: the URL keeps the view (?src=chains&cid=…&kind=kv&order=1)', JSON.stringify(query()) === JSON.stringify({ src: 'chains', cid: DEF, kind: 'kv', order: '1' }), pg.url());
  check('⚙ in ChainSWE chains: the example-chain section is there', await (async () => { await gear(true); const v = await vis('#gs-chain'); await gear(false); return v; })());
  // tasks named by their summary (else their title); instance ids only with Show details
  { const ss = DROW.arms.kv.sessions, iids = ss.map(s => (s.instance || '').split('__').pop()).filter(Boolean), want = [], got = await pg.$$eval('.excard .sess[data-ex] .inst', e => e.map(x => x.innerText.trim()));
    for (const s of ss) want.push(await nameFor(DEF, s));
    const src = ss.some(s => s.summary) ? 'summaries from the server' : 'no summary from the server yet: the titles';
    check(`chains: the example chain's task rows name each task (${src}), no instance id`, JSON.stringify(got) === JSON.stringify(want) && !got.some(x => iids.some(i => x.includes(i))), `${JSON.stringify(got)} vs ${JSON.stringify(want)}`);
    const sess = DS1, pinWant = (sess.summary || sess.title) || want[0];
    check('chains: the pinned task shows the task\'s name, its instance id only with details', (await IT('#taskpin .tp-title')) === pinWant && !(await vis('#taskpin .tp-inst')), `"${await IT('#taskpin .tp-title')}" vs "${pinWant}"`);
    await details(true); const gotD = await pg.$$eval('.excard .sess[data-ex] .inst', e => e.map(x => x.innerText.trim()));
    check('chains, Show details: the rows add the instance id', gotD.every((x, i) => x.startsWith(want[i]) && /· [\w.-]+-\d+$/.test(x)) && await vis('#taskpin .tp-inst'), JSON.stringify(gotD));
    await details(false); }
  await pg.screenshot({ path: OUT + '/12_index_chains.png' });
  // pinned task
  await pg.click('#taskpin .tp-head'); check('pin expands', await vis('#taskpin .tp-body')); await pg.click('#taskpin .tp-head'); check('pin collapses', !(await vis('#taskpin .tp-body')));
  // playback: the bar's status is empty while idle (the header has the steps and the time); while playing "step 1/27 · 00:02"
  check('play bar: no status while idle (the header has the steps and the time)', (await IT('#pb-status')) === '', await IT('#pb-status'));
  await pg.click('#pb-play'); await sleep(1500); const hid = await pg.$$eval('#transcript .turn[hidden]', e => e.length); check('play reveals progressively', hid > 0 && /Pause/.test(await T('#pb-play')), `hidden=${hid}`);
  check(`play bar while playing: "step k/${DS1.turns.length} · mm:ss" (no second total)`, new RegExp(`^step \\d+\\/${DS1.turns.length} · \\d\\d:\\d\\d$`).test(await IT('#pb-status')), await IT('#pb-status'));
  await pg.click('#pb-play'); await sleep(300); check('pause', /Resume/.test(await T('#pb-play')), await T('#pb-play'));
  await pg.click('#pb-step'); await sleep(200); const b4 = await pg.$$eval('#transcript .turn:not([hidden])', e => e.length); await pg.click('#pb-step'); await sleep(200); const af = await pg.$$eval('#transcript .turn:not([hidden])', e => e.length); check('step reveals one more turn', af === b4 + 1, `${b4}->${af}`);
  await pg.click('#pb-end'); await sleep(300); check('⇥ shows the whole task', (await pg.$$eval('#transcript .turn[hidden]', e => e.length)) === 0);
  check('⇥: the play bar is idle again (no status)', (await IT('#pb-status')) === '', await IT('#pb-status'));
  { const o2 = await pg.$eval('#obs-2', e => e.innerText.trim()).catch(() => null), raw = await pg.evaluate(() => { const t = S.sess.turns.find(x => x.step === 2); return t && t.observation ? t.observation.output : null; });
    if (raw != null && !raw.trim()) check('a command that printed nothing says "(no output)" (no empty box)', o2 === '(no output)', JSON.stringify(o2));
    else console.log('   (step 2 printed something here: the "(no output)" line is not exercised)'); }
  check('plain words: a whole ChainSWE session (⇥) has no benchmark / harness terms', !(await jargon()), await jargon());
  check('plain words: the grade card says "checked with the issue\'s tests"', /✓ Resolved · checked with the issue's tests/.test(await IT('#transcript .tail[data-tail="grade"] .hd')), await IT('#transcript .tail[data-tail="grade"] .hd'));
  await pg.click('#pb-restart'); await sleep(300); check('↺ hides turns again', (await pg.$$eval('#transcript .turn[hidden]', e => e.length)) > 0);
  await pick('#pb-speed', '10'); await pg.click('#pb-play'); await sleep(200); check('speed applies', /10×/.test(await T('#pb-status')), await T('#pb-status')); await pg.click('#pb-play'); await pg.click('#pb-end');
  await pg.click('#chathead h2'); await pg.keyboard.press('Space'); await sleep(300); check('space toggles play', /Pause/.test(await T('#pb-play'))); await pg.keyboard.press('Space'); await pg.click('#pb-end'); await pick('#pb-speed', '1');
  // panes + memory comparison view
  for (const t of ['changes', 'files', 'memory', 'issue', 'ledger']) { await pg.click(`#tabs button[data-t="${t}"]`); await sleep(250); const txt = await T('#pane'); check(`pane ${t} renders`, txt.length > 20, txt.slice(0, 60)); }
  check('Memory tab: plain wording (across the chain)', /⇆ Compare Knowledge Vault and Mem0 memory across the chain/.test(await (async () => { await pg.click('#tabs button[data-t="memory"]'); await sleep(200); return IT('#pane'); })()) && /Nothing passed in before this task — it is the first task of the chain\./.test(await IT('#pane')) && !/injected chars|\(last task\)/.test(await IT('#pane')), (await IT('#pane')).slice(0, 160));
  check('Issue tab: the repository, its instance id only with details', await (async () => { await pg.click('#tabs button[data-t="issue"]'); await sleep(200); return (await IT('#pane .src')) === DROW.repo; })(), await IT('#pane .src'));
  await pg.click('#tabs button[data-t="ledger"]'); await sleep(200); const lg = await IT('#pane');
  check('Ledger tab: plain words (the agent · tokens in / out, all tokens, cost), no harness file names or role ids', /The agent · tokens in \/ out/.test(lg) && /All tokens/.test(lg) && /Cost at /.test(lg) && !/calls\.jsonl|ledger\.py|Actor tokens|Writer-side|actor\b/.test(lg), lg.slice(0, 200));
  { const pr = (await getJ('/api/meta')).price_rows.opus5;
    check('Ledger tab: the cost "at $5 / $25 per million tokens (in / out)", no price-row id, no "harness"', new RegExp(`Cost at \\$${+pr.input} / \\$${+pr.output} per million tokens \\(in / out\\)`).test(lg) && !/opus5|harness|price row/.test(lg) && (!/Every model call/.test(lg) || /Every model call, as logged\./.test(lg) && /\bsecs\b/.test(lg)), lg.slice(0, 260)); }
  await filesTree('chain');
  // quotes in a path the agent controls (a diff path, a file name, a mention) stay text, and a carriage return in a name survives
  // the HTML parser: rows, titles and data-f round-trip
  const xss = await pg.evaluate(() => { const s = S.sess, t = P.treeCache[treeKey(s)], evil = 'q"onmouseover="window.__xss=1"/m.py', keep = [t.files, s.files, s.changes];
    t.files = [...t.files, 'w/' + evil, 'w/cr\rname.py']; t.set = new Set(t.files); t.base = t.built = t.byBase = null;
    s.files = [...s.files, { path: evil, mentions: 1, in_commands: 1, edited: false, first_step: 1 }, { path: 'w/cr\rname.py', mentions: 1, in_commands: 1, edited: false, first_step: 1 }];
    s.changes = [...s.changes, { path: 'z' + evil, added: 1, removed: 0, diff: `diff --git a/z${evil} b/z${evil}\nnew file mode 100644\n+x` }]; S.ftree = null; renderPane();
    const row = p => [...document.querySelectorAll('#ftree .tr.f')].find(r => r.dataset.f === p), w = row('w/' + evil);
    const out = { injected: document.querySelectorAll('#pane [onmouseover]').length, mention: !!w, title: w ? w.querySelector('.badge.mc').title : '', diffRow: !!row('z' + evil), cr: !!row('w/cr\rname.py'), xss: window.__xss };
    [t.files, s.files, s.changes] = keep; t.set = new Set(t.files); t.base = t.built = t.byBase = null; S.ftree = null; renderPane(); return out; });
  check('Files: quotes in paths stay text (no attribute injected)', xss.injected === 0 && xss.mention && xss.diffRow && xss.cr && xss.title.includes('also as q"onmouseover=') && xss.xss === undefined, JSON.stringify(xss));
  // a listing cut at TREE_MAX: the agent's files beyond the cut stay in the tree, marked "not listed" (never "new"), and the counts hold
  const cut = await pg.evaluate(() => { const s = S.sess, t = P.treeCache[treeKey(s)], keep = [t.files, t.n, t.truncated];
    const es = [...agentFiles(s, t).values()], pick = (k, full) => (es.find(e => e.tier === k && e.state === "" && e.path.includes("/") && (!full || !e.aliases.length)) || {}).path;
    const gone = [pick("ed", false), pick("rd", true)];          // an edited file (its diff names the full path) and a read one the agent named by its full path
    t.files = t.files.filter(f => !gone.includes(f)); t.set = new Set(t.files); t.truncated = true; t.n = t.files.length + 2; t.base = t.built = t.byBase = null; S.ftree = null; renderPane();
    const rows = [...document.querySelectorAll('#ftree .tr')], lg = [...document.querySelectorAll('.fsum .lg')].map(x => x.textContent.trim()), num = re => +((lg.map(x => re.exec(x)).find(Boolean) || [, 0])[1]);
    const out = { gone, shown: rows.filter(r => r.matches('.f.ed, .f.rd, .f.seen')).length, closed: rows.filter(r => r.matches('.d:not(.grp)') && r.getAttribute('aria-expanded') === 'false').reduce((a, r) => a + +((r.querySelector('.badge.rel') || {}).textContent || 0), 0),
      sum: num(/^(\d+) edited$/) + num(/^(\d+) read$/) + num(/^(\d+) seen in output$/), cut: gone.map(g => { const r = rows.find(r => r.dataset.f === g); return r ? { text: r.textContent, isNew: !!r.querySelector('.badge.new') } : null; }) };
    [t.files, t.n, t.truncated] = keep; t.set = new Set(t.files); t.base = t.built = t.byBase = null; S.ftree = null; renderPane(); return out; });
  check('Files: a cut listing keeps the agent\'s files beyond the cut ("not listed", never "new")', cut.shown + cut.closed === cut.sum && cut.cut.every(x => x && /not listed/.test(x.text) && !x.isNew), JSON.stringify(cut));
  // a file the agent created at the top stays there, even when a file of that name sits deeper (CONTRIBUTING.md vs .github/CONTRIBUTING.md)
  const dp = await pg.evaluate(() => { const s = S.sess, t = P.treeCache[treeKey(s)], keep = [t.files, s.changes];
    t.files = [...t.files, 'docs/zz_new.md']; t.set = new Set(t.files); t.base = t.built = t.byBase = null;
    s.changes = [...s.changes, { path: 'zz_new.md', added: 1, removed: 0, diff: 'diff --git a/zz_new.md b/zz_new.md\nnew file mode 100644\n+x' }]; S.ftree = null; renderPane();
    const row = p => [...document.querySelectorAll('#ftree .tr.f')].find(r => r.dataset.f === p), a = row('zz_new.md'), b = row('docs/zz_new.md');
    const out = { top: a ? a.textContent : null, deeper: b ? b.className : '(its folder stays closed)' };
    [t.files, s.changes] = keep; t.set = new Set(t.files); t.base = t.built = t.byBase = null; S.ftree = null; renderPane(); return out; });
  check('Files: a file the agent created at the top stays there (not merged into a deeper one of the same name)', /new/.test(dp.top || '') && !/\bed\b/.test(dp.deeper), JSON.stringify(dp));
  // follow the agent: during playback the file a command names is revealed and marked in the tree
  await pg.click('#tabs button[data-t="changes"]'); await pg.click('#pb-restart'); await sleep(200); await pick('#pb-speed', '10'); await pg.click('#pb-play');
  await pg.waitForFunction(() => document.querySelector('#tabs button[data-t="files"]').getAttribute('aria-selected') === 'true' && document.querySelector('#ftree .tr.cur'), null, { timeout: 30000 }).catch(() => {});
  check('Files: follow-the-agent reveals the file in the tree', !!(await pg.$('#ftree .tr.cur')), await T('#ftree .tr.cur'));
  await pg.click('#pb-play'); await pg.click('#pb-end'); await pick('#pb-speed', '1');
  await pg.click('#tabs button[data-t="memory"]'); await sleep(200); await pg.click('#memcmp2'); await pg.waitForSelector('#transcript .mc-wrap', { timeout: 20000 }).catch(() => {});
  check('memory comparison view opens (#memcmp2)', !!(await pg.$('#transcript .mc-wrap')) && !(await vis('#playbar')));
  { const backs = await pg.$$eval('button', e => e.filter(b => (b.offsetWidth || b.offsetHeight) && /Back|← Session/.test(b.innerText)).map(b => b.innerText.trim()));
    check('memory view: one way back (the header\'s "← Session"; Esc does the same)', backs.length === 1 && backs[0] === '← Session', JSON.stringify(backs)); }
  check('memory view: no "Press ▶ Play" hint (it has no play bar), the URL says view=memory', await pg.$eval('#transcript', e => getComputedStyle(e, '::before').content).then(c => !/Press/.test(c)) && query().view === 'memory', `${await pg.$eval('#transcript', e => getComputedStyle(e, '::before').content)} · ${pg.url()}`);
  check('plain words: the memory view has no raw ids or harness terms', !(await jargon()), await jargon());
  const SW1 = await arm(); check('memory view: the switcher is where it was', sameBox(SW1, C0), JSON.stringify(SW1.arm));
  // Space in the memory view does nothing: it has no play bar (before: the hidden session played and the view moved to the next task)
  { const u0 = pg.url(), o0 = await pg.evaluate(() => S.order); await pg.click('#chathead h2'); await pg.keyboard.press('Space'); await sleep(2500);
    const st = await pg.evaluate(() => ({ on: P.on, order: S.order, mv: S.memView, phases: document.querySelectorAll('#transcript .card.tail.phase').length }));
    check('memory view: Space plays nothing and keeps the task (no hidden playback)', !st.on && st.order === o0 && st.mv && !st.phases && pg.url() === u0 && !!(await pg.$('#transcript .mc-wrap')), `${JSON.stringify(st)} · ${pg.url().replace(BASE, '')}`); }
  await pg.keyboard.press('Escape'); await sleep(500); check('Esc leaves the memory view', !(await pg.$('#transcript .mc-wrap')) && (await pg.$$('#transcript .turn')).length > 0 && !query().view);
  // task rows + memory switcher
  await pg.click('.excard .sess[data-ex="2"]'); await sleep(1200); check('task row 2 opens task 2', new RegExp(`Task 2 of ${N}`).test(await T('#chathead')) && (await pg.$$('.excard .sess.sel[data-ex="2"]')).length === 1, await T('#chathead'));
  await pg.click('#armseg button[data-k="mem0"]'); await sleep(1300); check('switcher → Mem0, same task, card rows follow, the switcher stays put', new RegExp(`Task 2 of ${N}`).test(await T('#chathead')) && /Mem0/.test(await T('#chathead')) && /with Mem0/.test(await T('.excard .ex-sub')) && sameBox(await arm(), C0), await T('#chathead'));
  // the URL keeps the view: reload lands on Mem0 task 2
  check('URL: Mem0 task 2 is in the address', JSON.stringify(query()) === JSON.stringify({ src: 'chains', cid: DEF, kind: 'mem0', order: '2' }), pg.url());
  await pg.reload(); await pg.waitForSelector('.excard .sess.sel', { timeout: 30000 }); await head(new RegExp(`Task 2 of ${N}`)); await sleep(600);
  check('reload: back on Mem0 task 2 of the example chain', new RegExp(`Task 2 of ${N}`).test(await T('#chathead')) && /Mem0/.test(await T('#chathead')) && (await sw()) === 'kv mem0* memop none', `${await T('#chathead')} · ${await sw()}`);
  await epReady();
  // MemOp: the same task, the card's rows follow; its memory is one guidance document (markdown), said so in the Memory tab, the Ledger
  // and the memory view, which puts it next to Knowledge Vault
  await pg.click('#armseg button[data-k="memop"]'); await head(/MemOp/); await sleep(1000);
  check(`switcher → MemOp: same task, the card's rows follow (${DROW.arms.memop.sessions.length} MemOp tasks), the switcher stays put, the URL says kind=memop`, new RegExp(`Task 2 of ${N}`).test(await T('#chathead')) && /MemOp/.test(await T('#chathead .kchip')) && /^Its tasks with MemOp$/.test(await IT('.excard .ex-sub')) && (await pg.$$('.excard .sess[data-ex]')).length === DROW.arms.memop.sessions.length && sameBox(await arm(), C0) && query().kind === 'memop' && (await sw()) === 'kv mem0 memop* none', `${await T('#chathead')} · ${await IT('.excard .ex-sub')} · ${await sw()} · ${pg.url()}`);
  { const mcard = await IT('#transcript .card.memory .hd');
    check('MemOp transcript: "Guidance document from earlier tasks · MemOp", shown as a document (markdown)', /^Guidance document from earlier tasks · MemOp/.test(mcard) && !!(await pg.$('#transcript .card.memory .note.md')), mcard);
    await pg.click('#tabs button[data-t="memory"]'); await sleep(250); const mt = await IT('#pane');
    check('MemOp Memory tab: "MemOp\'s guidance document, rewritten after every task" (markdown), the compare button pairs Knowledge Vault with MemOp', /Passed in before this task: MemOp's guidance document, rewritten after every task/.test(mt) && /^⇆ Compare Knowledge Vault and MemOp memory across the chain/.test(mt) && !!(await pg.$('#pane .card.memory .note.md')) && !/\*\*|^#/m.test(await pg.$eval('#pane .card.memory', e => e.innerText)), mt.slice(0, 200));
    await pg.click('#tabs button[data-t="ledger"]'); await sleep(250); const ml = await IT('#pane');
    check('MemOp Ledger: "Rewriting the guidance document for the next task"', /Rewriting the guidance document for the next task/.test(ml) && /All tokens/.test(ml), ml.slice(0, 160));
    await pg.click('#tabs button[data-t="memory"]'); await sleep(200); await pg.click('#memcmp2'); await pg.waitForSelector('#transcript .mc-wrap', { timeout: 20000 }).catch(() => {}); await sleep(600);
    const mh = await pg.$$eval('.mc-head', e => e.map(x => x.dataset.kind).join(',')), bar = await IT('.mc-bar');
    check('memory view on MemOp: Knowledge Vault next to MemOp, its cells a document; the bar says what MemOp keeps and how to pick the method', mh === 'kv,memop' && !!(await pg.$('.mc-cell[data-kind="memop"] .note.md')) && /MemOp's memory is one guidance document, rewritten after every task\./.test(bar) && /Pick Mem0 or MemOp in the switcher to compare that method with Knowledge Vault\./.test(bar) && query().view === 'memory' && query().kind === 'memop', `${mh} · ${bar.slice(-160)}`);
    await pg.screenshot({ path: OUT + '/24_index_memop_memview.png' });
    await pg.click('#armseg button[data-k="mem0"]'); await pg.waitForFunction(() => [...document.querySelectorAll('.mc-head')].map(x => x.dataset.kind).join() === 'kv,mem0', null, { timeout: 20000 }).catch(() => {});
    check('memory view: the switcher on Mem0 → Knowledge Vault next to Mem0', (await pg.$$eval('.mc-head', e => e.map(x => x.dataset.kind).join(','))) === 'kv,mem0', await pg.$$eval('.mc-head', e => e.map(x => x.dataset.kind).join(',')));
    await pg.keyboard.press('Escape'); await sleep(500); }
  await pg.click('#armseg button[data-k="none"]'); await sleep(1300); check('switcher → No memory', /No memory/.test(await T('#chathead')) && /without memory/.test(await T('.excard .ex-sub')), await T('#chathead'));
  // a trip through the sandbox draft keeps the method chosen here (the draft opens on a method it runs), and never ticks it there
  const toSandbox = async () => { await pg.click('#srcseg button[data-src="custom"]'); await pg.waitForSelector('#startcard', { timeout: 30000 }); await sleep(400); };
  const toChains = async () => { await pg.click('#srcseg button[data-src="chains"]'); await pg.waitForSelector('.excard .sess.sel', { timeout: 30000 }); await sleep(900); };
  const wr = writes.length; await toSandbox(); const rt = await ticked(), rtA = await arm(); await toChains();
  check('round trip: No memory chosen here → Your sandbox (Run with unchanged: KV + Mem0; no switcher) → back: No memory again', rt === 'kv,mem0' && !rtA.arm && (await sw()) === 'kv mem0 memop none*' && /No memory/.test(await T('#chathead')) && writes.length === wr, `draft ticks ${rt} · back ${await sw()} · ${(await T('#chathead')).slice(0, 60)}`);
  await pg.click('#armseg button[data-k="kv"]'); await sleep(1200);
  // late answers (GET-only pages of their own): (1) Mem0's session answers after MemOp was picked → MemOp stays on screen; (2) the
  // chain's first session answers after a switch to Your sandbox → the New project draft stays; (3) the toggle clicked while the page still
  // loads (the example chains held back 2.5 s) → the page opens that side
  { const lctx = await b.newContext({ viewport: { width: 1500, height: 900 } }), lp = await lctx.newPage(); let slow = null, t0 = 0;
    await lp.route('**/api/**', async r => { const q = r.request(); if (q.method() !== 'GET') return r.abort();
      if ((slow && slow.test(q.url())) || (t0 && /\/api\/showcase/.test(q.url()) && Date.now() - t0 < 4000)) await sleep(3000); return r.continue().catch(() => {}); });
    lp.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
    const LT = async sel => ((await lp.innerText(sel).catch(() => '')) || '').replace(/\s+/g, ' ').trim();
    await lp.goto(BASE + '/?src=chains'); await lp.waitForSelector('.excard .sess.sel', { timeout: 30000 }); await sleep(800);
    slow = /api\/session\?.*arm=mem_factsall/; await lp.click('#armseg button[data-k="mem0"]'); await sleep(200); slow = null; await lp.click('#armseg button[data-k="memop"]'); await sleep(4500);
    check('late answer: Mem0\'s session answering after MemOp was picked does not replace MemOp', /MemOp/.test(await LT('#chathead .kchip')) && /with MemOp/.test(await LT('.excard .ex-sub')) && new URL(lp.url()).searchParams.get('kind') === 'memop', `${await LT('#chathead .kchip')} · ${await LT('.excard .ex-sub')}`);
    await lp.click('#srcseg button[data-src="custom"]'); await lp.waitForSelector('#startcard', { timeout: 30000 }); await sleep(600);
    slow = /api\/session\?/; await lp.click('#srcseg button[data-src="chains"]'); await sleep(150); slow = null; await lp.click('#srcseg button[data-src="custom"]'); await sleep(4500);
    check('late answer: a chain session answering after the switch to Your sandbox leaves the New project draft', await lp.isVisible('#startcard') && /^New project/.test(await LT('#chathead')) && new URL(lp.url()).search === '', `${await LT('#chathead')} · ${lp.url().replace(BASE, '')}`);
    t0 = Date.now(); await lp.goto(BASE + '/', { waitUntil: 'commit' }); await lp.waitForSelector('#srcseg button', { timeout: 10000 }); await sleep(1000);
    const early = !(await lp.evaluate(() => S.booted)); await lp.click('#srcseg button[data-src="chains"]'); await lp.waitForSelector('.excard .sess.sel', { timeout: 30000 }).catch(() => {}); await sleep(800);
    check('a toggle click while the page loads opens that side (ChainSWE chains)', early && (await lp.$$eval('#srcseg button[aria-pressed="true"]', e => e.map(x => x.dataset.src).join())) === 'chains' && !!(await lp.$('.excard .sess.sel')) && new URL(lp.url()).searchParams.get('src') === 'chains', `clicked before boot ended: ${early} · ${await LT('#chathead')} · ${lp.url().replace(BASE, '')}`);
    await lctx.close(); }
  // ⚙: chain picker ("<title> — <repo>", as on Compare; no groups) and recorded run picker (paper and endpoint runs; others with details)
  await pg.click('#exchange'); await sleep(300); check('"change" opens ⚙ at the chain picker', !(await pg.$eval('#gearpanel', e => e.hidden)) && (await pg.evaluate(() => document.activeElement.id)) === 'exchain');
  { const sc = await getJ('/api/showcase'), opts = await pg.$$eval('#exchain option', e => e.map(o => o.textContent)), og = (await pg.$$('#exchain optgroup')).length;
    const first = `${sc.chains[0].title} — ${sc.chains[0].repo}`;
    check(`chain picker: the ${sc.chains.length} showcase chains in the server's order, no groups, first "${first}"`, !og && opts.length === sc.chains.length && opts.join('|') === sc.chains.map(c => `${c.title} — ${c.repo}`).join('|'), `${opts.length} options, ${og} groups: ${opts.slice(0, 2).join(' | ')}`);
    check('chain picker: the blurb as tooltip', (await pg.$eval(`#exchain option[value="${DEF}"]`, o => o.title)) === DSC.blurb);
    const wSel = await pg.$eval('#exchain', e => e.scrollWidth <= e.clientWidth + 1 && e.getBoundingClientRect().width > 200);
    check('chain picker: on its own row, the closed select shows the whole title', wSel); }
  const runs = await pg.$$eval('#exrun option', e => e.map(o => o.value + '=' + o.textContent)), meta = await getJ('/api/meta');
  check('run picker: friendly labels, the paper runs only (no live runs, no Compare endpoint runs, no test runs without details)', runs[0] === 'v3main100=Paper run (v3, 100 chains)' && runs.every(r => /^(v3main100|v2main100)=/.test(r)), runs.join(', '));
  { const other = (meta.runs || []).filter(r => !/^(live_|demo_live|custom|v3main100|v2main100)/.test(r)); await pg.keyboard.press('Escape'); await details(true); await gear(true);
    const runsD = await pg.$$eval('#exrun option', e => e.map(o => o.value)); await pg.keyboard.press('Escape'); await details(false);
    check(`run picker with Show details: also the other recorded runs, Compare's endpoint runs among them (${other.length})`, other.every(r => runsD.includes(r)), `${other.join(', ')} · listed ${runsD.length}`); }
  const OTH = SC.chains.find(c => c.cid !== DEF);
  await gear(true); await pg.selectOption('#exchain', OTH.cid); await sleep(1600);
  check(`chain change (${OTH.cid}): card re-renders, task 1 of the new chain opens`, (await T('.excard')).includes(OTH.title) && new RegExp(`Task 1 of ${OTH.n_tasks}`).test(await T('#chathead')) && (await T('#chathead')).toLowerCase().includes(OTH.repo.split('/')[1].toLowerCase()) && query().cid === OTH.cid, await T('#chathead'));
  if (runs.some(r => r.startsWith('v2main100='))) { await pg.selectOption('#exrun', 'v2main100'); await sleep(2500); check('run change: card shows the other run', /Paper run \(v2\)/.test(await T('.excard')) && (await pg.$$('.excard .sess[data-ex]')).length >= 1, (await T('.excard')).slice(0, 140)); await pg.selectOption('#exrun', 'v3main100'); await sleep(2500); }
  await pg.selectOption('#exchain', DEF); await sleep(1600); await pg.keyboard.press('Escape');
  check('back on the example chain', (await T('.excard')).includes(DNAME) && new RegExp(`Task 1 of ${N}`).test(await T('#chathead')) && query().cid === DEF);
  // price row
  const usd0 = await T('#chathead .hstats .big'); await pick('#price', { index: 1 }); await sleep(2500); check('price row change updates the session cost', (await T('#chathead .hstats .big')) !== usd0, `${usd0} -> ${await T('#chathead .hstats .big')}`); await pick('#price', { index: 0 }); await sleep(2000);
  // pyccel (pyccel_pyccel_20240307) by its id: the old example chain, outside the list now — the card says "Chain" and names it
  // "pyccel/pyccel · 2024-03-07", as ⚙'s chain picker and the memory view do (a repository can have several chains); Knowledge Vault used
  // more tokens than MemOp there: in red, said so
  { const prow = await rowOf(PYC);
    if (!prow) console.log(`   (${PYC} is not in ${EXRUN} here: its checks are skipped)`);
    else { const psc = scOf(PYC), pname = psc ? psc.title : `${prow.repo} · ${cidDate(PYC)}`, pc = cardNums(prow);
      await pg.goto(BASE + `/?src=chains&cid=${PYC}`); await pg.waitForSelector('.excard .sess.sel', { timeout: 30000 }); await head(/Task 1 of/); await sleep(800);
      check(`${PYC}: ${psc ? 'a listed chain: "Example chain" and its title' : `outside the list: the card says "Chain" and names it "${pname}", no raw id on the page`}`, (psc ? /^Example chain/i : /^Chain\b/i).test(await IT('.excard .ex-top')) && (await IT('.excard .ex-title')) === pname && !(await IT('#chains')).includes(PYC) && !(await IT('#chathead')).includes(PYC), `${await IT('.excard .ex-top')} · ${await IT('.excard .ex-title')}`);
      const opt = await pg.$eval('#exchain', e => e.selectedOptions[0] ? e.selectedOptions[0].textContent : ''), cmp = await pg.$eval('.pnav a[href^="compare"]', a => { a.dispatchEvent(new Event('mouseenter')); return a.getAttribute('href'); });
      check(`${PYC}: ⚙'s chain picker names it the same way; the Compare link ${psc ? 'opens it' : 'is plain (Compare lists the example chains)'}`, opt === (psc ? `${psc.title} — ${psc.repo}` : pname) && cmp === (psc ? 'compare?cid=' + PYC : 'compare'), `"${opt}" · ${cmp}`);
      check(`${PYC}: the card's tokens and savings ("${pc.save}")`, (await T('.excard')).includes('Tokens for the whole chain: ' + pc.toks) && (await T('.excard .ex-save')) === pc.save, `${(/Tokens for the whole chain:[^−+]*/.exec(await T('.excard')) || [''])[0]} · ${await T('.excard .ex-save')}`);
      const sv = await pg.$$eval('.excard .ex-save span', e => e.map(x => ({ t: x.textContent.trim(), worse: x.classList.contains('worse'), tip: x.title, c: getComputedStyle(x).color })));
      const mo = sv.find(x => /MemOp/.test(x.t)), m0 = sv.find(x => /Mem0/.test(x.t)), d = mo ? +(/(\d+) %/.exec(mo.t) || [])[1] : null;
      if (pc.worse.includes('memop')) check(`${PYC}: "+${d} % vs MemOp" marked worse (red), its tooltip "Knowledge Vault used ${d} % more tokens than MemOp on this chain"`, !!mo && mo.worse && !m0.worse && mo.c !== m0.c && mo.tip === `Knowledge Vault used ${d} % more tokens than MemOp on this chain` && /^Knowledge Vault used \d+ % fewer tokens than Mem0/.test(m0.tip), JSON.stringify(sv));
      else console.log(`   (${PYC}: Knowledge Vault used fewer tokens than MemOp on this server's data: the red figure is not exercised)`);
      await pg.screenshot({ path: OUT + '/12_index_chain_pyccel.png' });
      await pg.goto(BASE + `/?src=chains&cid=${PYC}&kind=kv&order=1&view=memory`); await pg.waitForSelector('#transcript .mc-wrap', { timeout: 30000 }).catch(() => {}); await sleep(800);
      check(`${PYC}: the memory view names the chain "${pname}"`, (await IT('.mc-bar')).includes(`${pname} — for every task`), (await IT('.mc-bar')).slice(0, 120));
      // Mem0 task 3 stopped at the step limit: plain words without details, the raw status with them
      await pg.goto(BASE + `/?src=chains&cid=${PYC}&kind=mem0&order=3`); await pg.waitForSelector('.excard .sess.sel', { timeout: 30000 }); await head(/Task 3 of 3/); await sleep(800);
      const ex3 = await IT('#transcript .tail[data-tail="exit"]');
      if (ex3) { check('deep link ?kind=mem0&order=3 opens Mem0 task 3; its end in plain words ("Stopped at the step limit"), no raw status', /Mem0/.test(await T('#chathead')) && /^Stopped at the step limit/.test(ex3) && !/LimitsExceeded/.test(ex3), ex3);
        await details(true); check('…with Show details the raw exit status shows again', /LimitsExceeded/.test(await IT('#transcript .tail[data-tail="exit"]')), await IT('#transcript .tail[data-tail="exit"]')); await details(false); }
      else check('deep link ?kind=mem0&order=3 opens Mem0 task 3', /Task 3 of 3/.test(await T('#chathead')) && /Mem0/.test(await T('#chathead')), await T('#chathead')); } }
  await pg.goto(BASE + '/?src=chains'); await pg.waitForSelector('.excard .sess.sel', { timeout: 30000 }); await head(new RegExp(`Task 1 of ${N}`)); await epReady(); await sleep(800);

  // ---------------------------------------------------------------- live runs: every saved run stays visible, in place
  const all = () => pg.$$eval('#chains .chain.lrun[data-open]', e => e.map(x => x.dataset.open));
  const main = () => pg.$$eval('#chains > .chain.lrun[data-open]', e => e.map(x => x.dataset.open));
  // every method the page shows, MemOp included (Compare's Full history is recorded only: never a live run)
  const api = (await getJ('/api/liveruns')).filter(e => KS.includes(e.kind)); const A1 = await all(), M0 = await main();
  check('live runs: every saved run of Knowledge Vault, Mem0, MemOp and No memory listed', A1.length === api.length, `${A1.length} listed / ${api.length} saved`);
  { const mo = api.filter(e => e.kind === 'memop'), shown = A1.filter(o => o.split('|')[2] === 'memop');
    if (mo.length) check(`live runs: the saved MemOp live runs are listed (${mo.length}), named "MemOp" with its dot`, shown.length === mo.length && (await pg.$$eval('#chains .chain.lrun[data-open*="|memop|"] .id', e => e.every(x => /^MemOp\b/.test(x.innerText.trim()) && getComputedStyle(x.querySelector('.dot')).backgroundColor !== 'rgba(0, 0, 0, 0)'))), `${shown.length} listed / ${mo.length} saved`);
    else console.log('   (no saved MemOp live run on this server yet: the Run live MemOp step below makes one)'); }
  const nEmpty = api.filter(e => !(e.sessions || []).length && !e.running).length;
  if (nEmpty) check('live runs: runs without a finished task in ONE collapsed line', (await pg.$$('details.lempty')).length === 1 && !!(await pg.$('details.lempty:not([open])')) && new RegExp(`^${nEmpty} runs? without a finished task`).test(await T('details.lempty > summary')), await T('details.lempty > summary'));
  let same = true;
  for (const e of M0.slice(0, 8)) { await pg.click(`#chains > .chain.lrun[data-open="${e}"]`); await sleep(1200);
    const open = await pg.$eval('#chains .chain.lrun.open', x => x.dataset.open).catch(() => ''); if ((await all()).join() !== A1.join() || (await main()).join() !== M0.join() || open !== e) { same = false; console.log('   changed after selecting', e); } }
  if (M0.length) { check('live runs: selecting one keeps all others visible, in place', same, `${Math.min(8, M0.length)} selections over ${A1.length} runs`);
    check('live runs: the selected run expands its task rows', (await pg.$$('#chains .chain.lrun.open + .sessions .sess[data-open]')).length >= 1);
    check('live runs: selections never change the card or ⚙', (await T('.excard')).includes(DNAME) && (await pg.$eval('#exchain', e => e.value)) === DEF && (await pg.$eval('#exrun', e => e.value)) === 'v3main100');
    if (nEmpty) check('live runs: the empty-runs line stays collapsed', !!(await pg.$('details.lempty:not([open])')));
    await pg.screenshot({ path: OUT + '/13_index_liverun_selected.png' });
    if (await pg.$('#chains .chain.lrun.open + .sessions .sess[data-open]')) { await pg.click('#chains .chain.lrun.open + .sessions .sess[data-open]'); await sleep(1500); await filesTree('saved live run', { deep: false }); }
    await pg.focus(`#chains > .chain.lrun[data-open="${M0[0]}"]`); await pg.keyboard.press('Enter'); await sleep(1300);
    check('keyboard: Enter on a run opens it, focus stays', (await pg.evaluate(() => document.activeElement && document.activeElement.dataset.open)) === M0[0]);
    // a saved live run has ONE method: the other two are dimmed with the reason; clicking one does what it always did (the example
    // chain's recorded session of that method) and starts nothing
    const one = M0.find(e => /^live_/.test(e) && !/\|$/.test(e));
    if (one) { const [run1, cid1, k1] = one.split('|'), k2 = KS.find(k => k !== k1), ent = api.find(e => e.run === run1 && e.cid === cid1 && e.kind === k1);
      await pg.click(`#chains > .chain.lrun[data-open="${one}"]`); await sleep(1500); const s1 = await sw(), t2 = await tip(k2);
      check('saved live run: the methods it did not use are dimmed, the tooltip says why; the switcher stays put', s1 === KS.map(k => k + (k === k1 ? '*' : '~')).join(' ') && /^Not part of this live run \(it used only /.test(t2) && sameBox(await arm(), C0), `${s1} · ${t2.split('\n')[0]}`);
      // its rows: the tasks' names; the address carries the run, and a reload lands on the same run and task
      const rows = await pg.$$eval('#chains .chain.lrun.open + .sessions .sess[data-open] .inst', e => e.map(x => x.innerText.trim())), wantR = [];
      for (const s of ent.sessions) wantR.push(await nameFor(cid1, s));
      check('saved live run: its task rows name the tasks (summary, else title)', JSON.stringify(rows) === JSON.stringify(wantR), `${JSON.stringify(rows)} vs ${JSON.stringify(wantR)}`);
      const q1 = query(); check('URL: a saved live run keeps its run id in the address', q1.run === run1 && q1.cid === cid1 && q1.kind === k1, pg.url());
      await pg.reload(); await pg.waitForSelector('#chains .chain.lrun.open', { timeout: 30000 }).catch(() => {}); await head(/Task \d/); await sleep(800);
      check('reload: the same saved live run and task', (await pg.$eval('#chains .chain.lrun.open', x => x.dataset.open).catch(() => '')).startsWith(`${run1}|${cid1}|${k1}|`) && new RegExp(`Task ${q1.order || 1} of`).test(await T('#chathead')), `${await pg.$eval('#chains .chain.lrun.open', x => x.dataset.open).catch(() => 'none open')} · ${await T('#chathead')}`);
      check('reload of a saved live run: the card is still the example chain', /^Example chain/i.test(await IT('.excard .ex-top')) && (await T('.excard')).includes(DNAME), (await IT('.excard')).slice(0, 80));
      check('plain words: a saved live run session has no benchmark / harness terms', !(await jargon()), await jargon());
      await epReady();
      const kc = await pg.$eval('#chathead .kchip', e => [getComputedStyle(e).display !== 'none', /·/.test(e.textContent)]).catch(() => [null, null]);
      await details(true); const kcD = await pg.$eval('#chathead .kchip', e => getComputedStyle(e).display !== 'none').catch(() => null); await details(false);
      check('saved live run: the method chip is dropped at desktop width; with details it shows only for a Knowledge Vault version', kc[0] === false && kcD === kc[1], JSON.stringify({ shown: kc[0], version: kc[1], shownWithDetails: kcD }));
      await pg.screenshot({ path: OUT + '/13_index_liverun_switcher.png' });
      const w1 = writes.length; await pg.click(`#armseg button[data-k="${k2}"]`); await sleep(1600); const s2 = await sw();
      check(`saved live run: clicking dimmed ${NAME[k2]} opens the example chain's ${NAME[k2]} session, as before; starts nothing`, new RegExp(NAME[k2]).test(await T('#chathead')) && (await pg.$$('.excard .sess.sel')).length === 1 && !(await pg.$('#chains .chain.lrun.open')) && s2.split(' ').includes(k2 + '*') && writes.length === w1, `${await T('#chathead')} · ${s2}`);
      await pg.click('#armseg button[data-k="kv"]'); await sleep(1200); } }
  else if (!A1.length) check('live runs: none saved yet (hint shown)', /None yet/.test(await T('#chains')));
  else check('live runs: none with a finished task — they are folded into one line', (await pg.$$('details.lempty')).length === 1, await T('details.lempty > summary'));
  await pg.click('.excard .sess[data-ex="1"]'); await sleep(1200); check('example row reopens the recorded chain', new RegExp(`Task 1 of ${N}`).test(await T('#chathead')) && (await pg.$$('.excard .sess.sel')).length === 1);

  // ---------------------------------------------------------------- the chain card, saved live runs and other edges
  { const sc = await getJ('/api/showcase'), scIds = new Set(sc.chains.map(c => c.cid)), lr = await getJ('/api/liveruns'), v3 = await getJ('/api/runchains?run=v3main100');
    const cardOk = async () => /^Example chain/i.test(await IT('.excard .ex-top')) && (await T('.excard')).includes(DNAME) && (await T('.excard .ex-save')) === DCARD.save;
    // a saved live run of a chain outside the showcase list, opened from its URL (reload, or Back from Compare): the card stays on the example chain
    const farOk = e => !scIds.has(e.cid) && (e.sessions || []).length && KS.includes(e.kind), far = lr.find(e => e.cid === PYC && farOk(e)) || lr.find(farOk);
    if (far) { const u = `/?src=chains&cid=${far.cid}&kind=${far.kind}&order=1&run=${far.run}`;
      await pg.goto(BASE + u); await pg.waitForSelector('#chains .chain.lrun.open', { timeout: 30000 }).catch(() => {}); await head(/Task 1 of/); await sleep(800);
      check(`URL of a saved live run of a chain outside the list (${far.cid}): the card stays on the example chain, the run opens`, await cardOk() && (await pg.$eval('#chains .chain.lrun.open', x => x.dataset.open).catch(() => '')).startsWith(`${far.run}|${far.cid}|${far.kind}|`), `${(await IT('.excard')).slice(0, 90)} · ${await pg.$eval('#chains .chain.lrun.open', x => x.dataset.open).catch(() => 'none open')}`);
      { const fname = `${far.repo} · ${cidDate(far.cid)}`, tipF = await pg.$eval('#chains .chain.lrun.open', x => x.title).catch(() => '');
        check(`…its entry names the chain "${fname}" (tooltip), its task opens, no raw chain id on the page`, tipF.includes(`on “${fname}”`) && /^Task 1 of/.test(await IT('#chathead')) && !(await IT('#chains')).includes(far.cid) && !(await IT('#chathead')).includes(far.cid), `${tipF} · ${await IT('#chathead')}`); }
      await pg.goto(BASE + '/compare'); await pg.waitForSelector('header.top', { timeout: 30000 }); await sleep(800); await pg.goBack(); await pg.waitForSelector('#chains .chain.lrun.open', { timeout: 30000 }).catch(() => {}); await head(/Task 1 of/); await sleep(800);
      check('…and after Compare → Back', await cardOk() && (await pg.$eval('#chains .chain.lrun.open', x => x.dataset.open).catch(() => '')).startsWith(`${far.run}|${far.cid}|`), `${(await IT('.excard')).slice(0, 90)} · ${pg.url()}`); }
    else console.log('   (no saved live run of a chain outside the showcase list here: the card-on-reload case is covered by the saved live run above)');
    // a chain outside the showcase list (a link, the Report's "Watch in the Agent view"): "Chain", named "repo · date", never its raw id
    const nsc = v3.chains.find(c => c.cid === 'HypothesisWorks_hypothesis_20210304') || v3.chains.find(c => !scIds.has(c.cid));
    if (nsc) { await pg.goto(BASE + '/?src=chains&cid=' + nsc.cid); await pg.waitForSelector('.excard .sess.sel', { timeout: 30000 }).catch(() => {}); await sleep(600);
      const m = /_(\d{4})(\d{2})(\d{2})(?:_|$)/.exec(nsc.cid), want = nsc.repo + (m ? ` · ${m[1]}-${m[2]}-${m[3]}` : '');
      check(`a chain outside the showcase list: the card says "Chain" and names it "${want}", no raw id in the list`, /^Chain\b/i.test(await IT('.excard .ex-top')) && !/^Example/i.test(await IT('.excard .ex-top')) && (await IT('.excard .ex-title')) === want && !(await IT('#chains')).includes(nsc.cid), `${await IT('.excard .ex-top')} · ${await IT('.excard .ex-title')}`);
      await pg.screenshot({ path: OUT + '/12_index_chain_not_showcase.png' }); }
    // a saved MemOp live run: listed with the others, its URL opens it (MemOp in the switcher, the others dimmed), no arm id on screen
    await pg.goto(BASE + '/?src=chains'); await pg.waitForSelector('.excard .sess.sel', { timeout: 30000 }); await sleep(500);
    check('live runs: only the methods this page shows (no Full history), no arm ids', !/mem_memop|mem_rawcat|Full history/.test(await IT('#chains')) && (await all()).every(o => KS.includes(o.split('|')[2])), (await all()).filter(o => !KS.includes(o.split('|')[2])).join(', '));
    const mo = lr.find(e => e.kind === 'memop' && (e.sessions || []).length);
    if (mo) { const e0 = errs.length; await pg.goto(BASE + `/?src=chains&cid=${mo.cid}&kind=memop&order=${mo.sessions[0].order}&run=${mo.run}`); await pg.waitForSelector('#chains .chain.lrun.open', { timeout: 30000 }).catch(() => {}); await head(/Task \d of/); await sleep(800);
      check('a saved MemOp live run\'s URL opens that run on MemOp (the other methods dimmed), no 404, no "undefined"', errs.length === e0 && !/undefined|no trajectory/.test(await IT('#transcript') + await IT('#chathead')) && (await pg.$eval('#chains .chain.lrun.open', x => x.dataset.open).catch(() => '')).startsWith(`${mo.run}|${mo.cid}|memop|`) && (await sw()) === 'kv~ mem0~ memop* none~' && query().run === mo.run, `${errs.slice(e0).join(', ')} · ${await IT('#chathead')} · ${await sw()}`);
      await pg.screenshot({ path: OUT + '/13_index_liverun_memop.png' }); }
    else console.log('   (no saved MemOp live run with a finished task here: its URL is not exercised)');
    // a saved live run without a finished task: "no task finished" (not "0/0 ✓"; it sits in the folded line of such runs), and ▶ Run live
    // gives the real reason when it is off
    const em = lr.find(e => !(e.sessions || []).length && !e.running && KS.includes(e.kind));
    if (em) { await pg.goto(BASE + `/?src=chains&cid=${em.cid}&kind=${em.kind}&order=1&run=${em.run}`); await sleep(2500); await epReady(); await sleep(300);
      const note = await pg.$eval('#runnote', e => e.textContent);
      check('a saved live run without a finished task: "no task finished" (never "0/0 ✓"), ▶ Run live not "Open a ChainSWE chain first"', !/\b0\/0\b/.test(await T('#chains')) && /no task finished/.test(await T('#chains')) && note !== 'Open a ChainSWE chain first', `${note} · ${pg.url()}`);
      await pg.screenshot({ path: OUT + '/13_index_liverun_empty.png' }); }
    // a link to a project that is gone: New project, said so
    await pg.goto(BASE + '/?cid=no-such-project-' + Date.now().toString(36)); await pg.waitForSelector('#startcard', { timeout: 30000 }).catch(() => {}); await sleep(500);
    check('a link to a project that is gone: Your sandbox → New project, toast "That project no longer exists"', !!(await pg.$('#startcard')) && (await pg.getAttribute('#srcseg button[data-src="custom"]', 'aria-pressed')) === 'true' && /That project no longer exists/.test(await T('#toast')), `${await T('#chathead')} · ${await T('#toast')}`);
    // a sandbox project: a chip's cost = the header's; "8 steps"; the rerun panel names "No memory" (opening it starts nothing)
    const pm = (await getJ('/api/custom/projects')).find(p => p.name === 'Restaurant-Ranking' && (p.arms?.mem0?.orders || []).includes(1)) || (await getJ('/api/custom/projects')).find(p => (p.arms?.mem0?.orders || []).includes(1));
    if (pm) { await pg.goto(BASE + `/?cid=${encodeURIComponent(pm.name)}&kind=mem0&order=1`); await head(/Task 1 of/); await sleep(1200);
      const chip = await pg.$eval('#chains [data-open="mem0:1"]', e => e.innerText.trim()).catch(() => ''), hd = await IT('#chathead .hstats .big');
      check(`sandbox (${pm.name}): the Mem0 chip's cost = the header's, "N steps"`, !!hd && chip.endsWith('· ' + hd) && /^\d+ steps? · \$/.test(chip), `chip "${chip}" · header "${hd}"`);
      const w0 = writes.length; await pg.click('#chains [data-rr="1"]'); await sleep(300); const rr = await IT('.rrpanel .rr-ks');
      check('sandbox: the rerun panel names the methods as the chips do (KV, Mem0, MemOp, "No memory"), opening it starts nothing', rr === 'KV Mem0 MemOp No memory' && writes.length === w0, rr);
      const tc = await pg.$$eval('#chains .chain.open[data-proj] ~ .sessions .task-row:first-child .chips button', e => e.map(x => x.dataset.open || x.dataset.rerun || x.dataset.live).map(v => v.split(':')[0]));
      check('sandbox: a task row has a chip per method, MemOp included (its session, or "MemOp ▶" to run it)', tc.join(',') === 'kv,mem0,memop,none' || (tc.includes('memop') && tc.length === 4), tc.join(','));
      await pg.click('.rrpanel [data-rra="cancel"]'); await sleep(200); }
    await pg.goto(BASE + '/?src=chains'); await pg.waitForSelector('.excard .sess.sel', { timeout: 30000 }); await head(new RegExp(`Task 1 of ${N}`)); await epReady(); await sleep(600); }

  // ---------------------------------------------------------------- composer: chat mode (⚙), Ask
  await gear(true); await pg.check('#chatmode'); await gear(false); check('chat mode: placeholder changes', /Chat with the model/.test(await pg.getAttribute('#q', 'placeholder')));
  await gear(true); await pg.uncheck('#chatmode'); await gear(false); check('chat mode off: Ask again', /Ask about this session/.test(await pg.getAttribute('#q', 'placeholder')));
  if (!RO) { await pg.fill('#q', 'What did the agent change?'); await pg.click('#send'); await pg.waitForFunction(() => document.querySelectorAll('#transcript .msg.model').length >= 1 && !document.querySelector('#send').disabled, null, { timeout: 90000 }).catch(() => {});
    check('Ask: question and answer appended to the transcript', (await pg.$$('#transcript .msg.you')).length === 1 && (await pg.$$('#transcript .msg.model')).length === 1, (await T('#transcript .msg.model')).slice(0, 100)); }

  // ---------------------------------------------------------------- Run live (replayed on 8799, ⚙ Model = this suite's own name), stop
  if (!RO) {
    check(`Run live enabled for the example chain (${DEF})`, !(await pg.$eval('#runlive', e => e.disabled)), await pg.getAttribute('#runlive', 'title'));
    { const lk = (await getJ('/api/meta')).live_kinds || ['kv', 'mem0', 'none'], want = KS.filter(k => lk.includes(k));
      await pg.click('#runlive'); await sleep(300); const items = await pg.$$eval('#runmenu button[data-k]', e => e.map(x => ({ k: x.dataset.k, off: x.disabled, t: x.innerText.trim(), tip: x.title })));
      check(`Run live menu: built from the server's live kinds (${want.join(', ')}), in page order, all offered, plain labels and tooltips`, items.map(x => x.k).join(',') === want.join(',') && items.every(x => !x.off) && items.map(x => x.t).join(' | ') === want.map(k => k === 'none' ? 'without memory' : 'with ' + NAME[k]).join(' | ') && items.every(x => x.tip === (x.k === 'none' ? 'Run the chain live without memory' : 'Run the chain live with ' + NAME[x.k])) && /\$1–5/.test(await T('#runnote')), JSON.stringify(items));
      await pg.screenshot({ path: OUT + '/14_index_runlive_menu.png' }); await pg.keyboard.press('Escape'); await pg.click('#chathead h2'); }
    const tl = Date.now(); await runLiveKV(); await pg.waitForSelector('#chains .liveblock', { timeout: 30000 }).catch(() => {}); await sleep(1500);
    check('live: watching, live block on top of Live runs', await live() && (await pg.$eval('#chains .lsec + .liveblock', () => true).catch(() => false)), (await T('#chains .liveblock')).slice(0, 80));
    check('live: card unchanged', (await T('.excard')).includes(DNAME));
    check('live: the switcher names the live run (KV), the methods not in it dimmed with the reason; the switcher stays put', (await sw()) === 'kv* mem0~ memop~ none~' && /^Not part of this live run \(it uses only Knowledge Vault\)\. Click to open the Mem0 run of this chain — the live run keeps going\./.test(await tip('mem0')) && sameBox(await arm(), C0), `${await sw()} · ${(await tip('mem0')).split('\n')[0]}`);
    check('live: toast in plain words', (await T('#toast')).includes(`Live run started: Knowledge Vault on “${DNAME}”`) || Date.now() - tl > 3500, await T('#toast'));
    await pg.waitForFunction(() => /step \d/.test(document.querySelector('#pb-livetxt').textContent), null, { timeout: 60000 }).catch(() => {});
    check('live: the bottom bar is the one status line ("<what it does> · step k · mm:ss · N tokens"), the model at its end', /^.+ · step \d+ · \d\d:\d\d · [\d.]+K? tokens/.test(await IT('#pb-livetxt')) && !/graded|task \d|ledger|tok\b/.test(await IT('#pb-live')) && (await IT('#pb-status')) === LIVE_MODEL && !(await vis('#transcript .live-status')), `${await IT('#pb-livetxt')} | ${await IT('#pb-status')}`);
    check('live: the header says "live" (no chain id), the pinned task named', new RegExp(`^Task 1 of ${N}`).test(await IT('#chathead')) && /\blive$/.test(await IT('#chathead')) && !(await IT('#chathead')).includes(DEF) && (await IT('#taskpin .tp-title')).length > 3, `${await IT('#chathead')} · ${await IT('#taskpin .tp-title')}`);
    check('live: the ask box waits for the run ("once the run finishes"), Send off', await pg.$eval('#q', e => e.disabled) && /once the run finishes/.test(await pg.getAttribute('#q', 'placeholder')) && await pg.$eval('#send', e => e.disabled), await pg.getAttribute('#q', 'placeholder'));
    await pg.click('#runlive'); await sleep(900); const menu = await pg.$$eval('#runmenu button[data-k]', e => e.map(x => `${x.dataset.k}:${x.disabled}:${x.title}`));
    check('live: ▶ Run live greys out Knowledge Vault (already running, tooltip says where to watch it), the others still offered', /^kv:true:Knowledge Vault is already running live on this chain with .+ — pick it in the switcher above to watch it$/.test(menu[0]) && menu.slice(1).length >= 2 && menu.slice(1).every(m => /^\w+:false:/.test(m)), menu.join(' | '));
    await pg.screenshot({ path: OUT + '/14_index_live_menu.png' }); await pg.keyboard.press('Escape'); await pg.click('#chathead h2');
    check('live: plain words while streaming (no harness log, no ids)', !(await jargon()), await jargon());
    await pg.waitForFunction(() => document.querySelectorAll('#transcript .turn').length >= 2, null, { timeout: 60000 }).catch(() => {});
    await pg.screenshot({ path: OUT + '/14_index_live.png' });
    await pg.click('#tabs button[data-t="files"]'); await treeReady(); await sleep(300);
    await pg.click('#tabs button[data-t="memory"]'); await sleep(300);
    check('live: the Memory tab offers no ⇆ Compare (the memory view compares finished tasks, not this run)', !(await pg.$('#memcmp2')), (await IT('#pane')).slice(0, 80));
    await pg.click('#tabs button[data-t="files"]'); await treeReady(); await sleep(300);
    check('live: Files tab shows the repository tree', /base commit · [\d,]+ files/.test(await T('.ftools .src')) && (await pg.$$('#ftree .tr')).length > 3, await T('.ftools .src'));
    await pg.screenshot({ path: OUT + '/14_index_live_files.png' });
    await pg.click('#pb-stop'); await sleep(200); check('stop asks for a second click', /click again/.test(await T('#pb-stop')));
    await pg.click('#pb-stop'); await pg.waitForFunction(() => !document.querySelector('#playbar').classList.contains('live'), null, { timeout: 60000 }).catch(() => {}); await sleep(2500);
    check('stop: the live view ends', !(await live()) && !(await pg.$('#chains .liveblock')), await T('#toast'));
    check('stop: the stopped run is in the Live runs list', (await all()).length === A1.length + 1, `${A1.length} -> ${(await all()).length}`);
    check('stop: the ask box asks again', !(await pg.$eval('#q', e => e.disabled)) && /Ask about this session/.test(await pg.getAttribute('#q', 'placeholder')));
    await stopMine();
    // ▶ Run live with MemOp (replayed; ⚙ Model is still this suite's own): the switcher, the live block and the status line; ■ Stop keeps
    // the finished tasks; the run is then in Live runs as MemOp
    if (((await getJ('/api/meta')).live_kinds || []).includes('memop')) {
      const nB = (await all()).length, tm = Date.now(), moB = (await all()).filter(o => o.split('|')[2] === 'memop');
      await pg.click('#runlive'); await sleep(500); await pg.click('#runmenu button[data-k="memop"]');
      await pg.waitForFunction(() => document.querySelector('#playbar').classList.contains('live'), null, { timeout: 30000 }).catch(() => {}); await pg.waitForSelector('#chains .liveblock', { timeout: 30000 }).catch(() => {}); await sleep(1500);
      check('live MemOp: watching; the switcher names MemOp, the others dimmed ("it uses only MemOp"); the switcher stays put', await live() && (await sw()) === 'kv~ mem0~ memop* none~' && /^Not part of this live run \(it uses only MemOp\)\./.test(await tip('kv')) && sameBox(await arm(), C0), `${await sw()} · ${(await tip('kv')).split('\n')[0]}`);
      check(`live MemOp: toast "Live run started: MemOp on “${DNAME}”"`, (await T('#toast')).includes(`Live run started: MemOp on “${DNAME}”`) || Date.now() - tm > 3500, await T('#toast'));
      check('live MemOp: the live block on top of Live runs names MemOp and the model', new RegExp(`^MemOp · ${LIVE_MODEL} ● live`).test(await IT('#chains .liveblock')), await IT('#chains .liveblock'));
      await pg.waitForFunction(() => /step \d/.test(document.querySelector('#pb-livetxt').textContent), null, { timeout: 60000 }).catch(() => {});
      check('live MemOp: the status line ("<what it does> · step k · mm:ss · N tokens"), the model at its end, the header says live', /^.+ · step \d+ · \d\d:\d\d · [\d.]+K? tokens/.test(await IT('#pb-livetxt')) && (await IT('#pb-status')) === LIVE_MODEL && new RegExp(`^Task \\d of ${N}`).test(await IT('#chathead')) && /\blive$/.test(await IT('#chathead')), `${await IT('#pb-livetxt')} | ${await IT('#pb-status')} | ${await IT('#chathead')}`);
      await pg.click('#runlive'); await sleep(900); const mm = await pg.$$eval('#runmenu button[data-k]', e => e.map(x => `${x.dataset.k}:${x.disabled}`).join(' '));
      check('live MemOp: ▶ Run live greys out MemOp only', /memop:true/.test(mm) && /kv:false/.test(mm) && /mem0:false/.test(mm) && /none:false/.test(mm), mm); await pg.keyboard.press('Escape'); await pg.click('#chathead h2');
      await pg.screenshot({ path: OUT + '/14_index_live_memop.png' });
      await pg.waitForFunction(n => new RegExp(`[1-9]\\d*\\/${n} done`).test((document.querySelector('#chains .liveblock') || {}).innerText || ''), N, { timeout: 90000 }).catch(() => {});
      await pg.click('#pb-stop'); await sleep(200); await pg.click('#pb-stop'); await pg.waitForFunction(() => !document.querySelector('#playbar').classList.contains('live'), null, { timeout: 60000 }).catch(() => {}); await sleep(2500);
      const ent = await pg.$$eval('#chains > .chain.lrun[data-open*="|memop|"]', e => e.map(x => ({ o: x.dataset.open, t: x.innerText.replace(/\s+/g, ' ').trim() }))), nw = ent.filter(x => !moB.includes(x.o));
      check('live MemOp: stopped — the run is in Live runs as MemOp, with its finished tasks', !(await live()) && (await all()).length === nB + 1 && nw.length === 1 && /^MemOp\b.* \d+\/\d+ ✓/.test(nw[0].t), `${nB} -> ${(await all()).length} · new: ${nw.map(x => x.t).join(' || ')}`);
      await pg.screenshot({ path: OUT + '/14_index_live_memop_saved.png' });
      await stopMine(); }
  }

  // ---------------------------------------------------------------- back to the sandbox; old ?src=custom links; a saved project
  await pg.click('#srcseg button[data-src="custom"]'); await pg.waitForSelector('#startcard'); await sleep(500);
  check('toggle back: draft again, run-live hidden, no switcher, the URL is "/"', /New project/.test(await T('#chathead')) && !(await vis('#runlive')) && !(await arm()).arm && new URL(pg.url()).search === '');
  const proj = await pg.$('#chains .chain[data-proj]'); if (proj) { const name = await proj.getAttribute('data-proj'); await proj.click(); await sleep(1500);
    check('saved project opens, no switcher, draft row not highlighted', (await T('#chathead')).length > 0 && !(await arm()).arm && !(await pg.$('#chains .chain.newrow.open')), await T('#chathead'));
    check('Compare link → compare?cid=<project>', await pg.$eval('.pnav a[href^="compare"]', a => { a.dispatchEvent(new Event('mouseenter')); return a.getAttribute('href'); }) === 'compare?cid=' + encodeURIComponent(name));
    check('filter box only with > 8 projects', (await vis('#searchbox')) === ((await pg.$$('#chains .chain[data-proj]')).length > 8)); }
  const projs = await getJ('/api/custom/projects'), sp = projs.find(p => p.tasks.length && (p.downloads || []).includes('kv'));
  const openKind = async (name, k) => { await pg.goto(BASE + '/?cid=' + encodeURIComponent(name)); await head(/Task \d/); await sleep(500);
    if (!new RegExp(NAME[k]).test(await T('#chathead'))) { const c = await pg.$(`#chains [data-open^="${k}:"]`); if (c) { await c.click(); await sleep(1200); } } };
  // a project with two or more tasks: rows named by their summary, the method chip in the header, the Task tab, the URL and a reload
  const p2 = projs.filter(p => !/^(pol|r2)-/.test(p.name)).find(p => p.tasks.length >= 2 && KS.some(k => (p.arms?.[k]?.orders || []).includes(2))) || projs.find(p => p.tasks.length >= 2 && KS.some(k => (p.arms?.[k]?.orders || []).includes(2)));
  if (p2) { const k2 = KS.find(k => (p2.arms?.[k]?.orders || []).includes(2));
    await pg.goto(BASE + '/?cid=' + encodeURIComponent(p2.name)); await head(/Task \d/); await sleep(600);
    const rows = await pg.$$eval('#chains .chain.open[data-proj] ~ .sessions .task-row .tt', e => e.map(x => ({ text: [...x.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim(), title: x.title })));
    const want = p2.tasks.map(t => t.summary || t.title), src = p2.tasks.some(t => t.summary) ? 'summaries' : 'no summary from the server yet: the titles';
    check(`sandbox (${p2.name}): the task rows name each task (${src})`, JSON.stringify(rows.map(r => r.text)) === JSON.stringify(want) && rows.every((r, i) => r.title === p2.tasks[i].title), JSON.stringify(rows.map(r => r.text)));
    check('sandbox: no switcher; the method chip next to "Task 1 of n" names the method', !(await arm()).arm && !!(await arm()).chip, JSON.stringify((await arm()).chip));
    check('sandbox: the Issue tab is called "Task"; it shows the project label, the instance id only with details', await (async () => { await pg.click('#tabs button[data-t="issue"]'); await sleep(200); return (await IT('#tabs button[data-t="issue"]')) === 'Task' && (await IT('#pane .src')) === (p2.label || p2.sandbox); })(), `${await IT('#tabs button[data-t="issue"]')} · ${await IT('#pane .src')}`);
    check('sandbox: the Memory tab compares "across the project"', await (async () => { await pg.click('#tabs button[data-t="memory"]'); await sleep(200); return /across the project/.test(await IT('#pane')) && !/\(last task\)/.test(await IT('#pane')); })(), (await IT('#pane')).slice(0, 100));
    check('plain words: a sandbox session has no benchmark / harness terms (no "not graded")', !(await jargon()), await jargon());
    await pg.click(`#chains [data-open="${k2}:2"]`); await head(/Task 2 of/); await sleep(500);
    check('URL: a sandbox project task is in the address (?cid=&kind=&order=2)', JSON.stringify(query()) === JSON.stringify({ cid: p2.name, kind: k2, order: '2' }), pg.url());
    await pg.reload(); await head(/Task 2 of/); await sleep(800);
    check('reload: the same project, method and task', /Task 2 of/.test(await T('#chathead')) && new RegExp(NAME[k2]).test(await T('#chathead .kchip')) && (await pg.$eval('#chains .chain.open[data-proj]', e => e.dataset.proj).catch(() => '')) === p2.name, await T('#chathead'));
    await epReady(); await pg.screenshot({ path: OUT + '/20_index_sandbox_project.png' }); }
  if (sp) { await openKind(sp.name, 'kv');
    await pg.click('#tabs button[data-t="files"]'); await treeReady(); await sleep(200); const st = await treeInfo();
    check(`Files (sandbox ${sp.name}): the workspace's files as a tree`, /workspace( now)? · \d+ files?/.test(st.src) && st.files >= 1 && st.buttons && st.stops === 1, st.src);
    const ff = await pg.$('#ftree .tr.f.ed') || await pg.$('#ftree .tr.f.rd') || await pg.$('#ftree .tr.f');
    if (ff) { await ff.click(); await fileReady();
      check(`Files (sandbox): a file opens from the workspace`, /Right after this task|In the workspace now/.test(await T('#pane')) && !!(await pg.$('#pane pre.view')), (await T('#pane')).slice(0, 160));
      await pg.screenshot({ path: OUT + '/19_index_files_sandbox_file.png' }); await pg.click('#pane #back'); await sleep(200); }
    await pg.screenshot({ path: OUT + '/19_index_files_sandbox.png' }); }
  // a memory system that ran in a project whose workspace is no longer on the server: said so; a project started from an example
  // repo shows that repo before any task instead, and its files open
  const gp = projs.map(p => [p, Object.keys(p.arms || {}).find(k => (p.arms[k].orders || []).length && !(p.downloads || []).includes(k))]).find(([, k]) => k);
  if (gp) { const [p, k] = gp; await openKind(p.name, k);
    await pg.click('#tabs button[data-t="files"]'); await treeReady(); await sleep(300); const src = await T('.ftools .src'), img = (p.source || {}).kind === 'image';
    check(`Files (sandbox ${p.name}, ${NAME[k]}): a workspace that is gone is said so`, img ? /example repository before any task — this memory system's workspace is no longer on the server · [\d,]+ files/.test(src) : /no longer on the server/.test(src), src);
    await pg.screenshot({ path: OUT + '/19_index_files_gone.png' });
    const gf = img && (await pg.$('#ftree .tr.f.rd:not(.miss)') || await pg.$('#ftree .tr.f.seen:not(.miss)'));
    if (gf) { await gf.click(); await fileReady(); const txt = await T('#pane');
      check(`Files (sandbox ${p.name}): a file of the example repo opens`, /The example repository before any task/.test(txt) && !!(await pg.$('#pane pre.view')), txt.slice(0, 160)); await pg.screenshot({ path: OUT + '/19_index_files_gone_file.png' }); } }
  // a chain without an image on this server: no listing is asked for (no 404 / error), the tree is pieced together from the agent's
  // paths and says so; no URL or traceback fragment becomes a folder; a file the agent named with and without its folders is one row
  const noImg = (await getJ('/api/showcase')).chains.find(c => c.sif === false);
  if (noImg) { const g0 = gets.length; await pg.goto(BASE + `/?src=chains&cid=${noImg.cid}`); await pg.waitForSelector('#taskpin:not([hidden]) .tp-head', { timeout: 30000 }); await sleep(800);
    await pg.click('#tabs button[data-t="files"]'); await sleep(500);
    check(`Files (${noImg.repo}, no image here): the page asks for no listing, the header says the folders are pieced together`, !gets.slice(g0).some(u => /\/api\/tree/.test(u)) && /No file listing for this chain on this server — the folders below are pieced together/.test(await T('.ftools .src')), await T('.ftools .src')); }
  else console.log('   (every showcase chain has its image on this server: the no-image tree is not exercised)');
  if ((await pg.request.get(BASE + '/api/session?run=v3main100&arm=mem_fusednote&cid=pyccel_pyccel_20240318&order=1')).ok() && (await getJ('/api/runchains?run=v3main100')).chains.find(c => c.cid === 'pyccel_pyccel_20240318')?.sif === false) {
    await pg.goto(BASE + '/?src=chains&cid=pyccel_pyccel_20240318'); await pg.waitForSelector('#taskpin:not([hidden]) .tp-head', { timeout: 30000 }); await sleep(800);
    await pg.click('#tabs button[data-t="files"]'); await treeReady(); await sleep(300);
    const nl = await pg.evaluate(() => { const rows = [...document.querySelectorAll('#ftree .tr')], files = rows.filter(r => r.dataset.f != null && !r.matches('.miss')).map(r => r.dataset.f), base = {};
      for (const f of files) { const k = f.split('/').pop(); (base[k] = base[k] || []).push(f); }
      return { src: document.querySelector('.ftools .src').textContent, top: rows.filter(r => r.dataset.d != null && !r.matches('.grp') && r.style.getPropertyValue('--dep') === '0').map(r => r.dataset.d), dup: Object.entries(base).filter(([, v]) => v.length > 1) }; });
    check('Files (chain without an image): the header says the folders are pieced together', /No file listing for this chain on this server — the folders below are pieced together/.test(nl.src), nl.src);
    check('Files (chain without an image): no URL or traceback fragment as a folder', !nl.top.some(d => /^(\d+|com|org|(?:[a-z0-9-]+\.)+(?:com|org|net|io|dev))$/i.test(d.split('/')[0])), nl.top.join(' '));
    check('Files (chain without an image): a file named with and without its folders is one row', !nl.dup.some(([k]) => ['list_methods.py', 'ccode.py', 'fcode.py', 'pycode.py'].includes(k)), JSON.stringify(nl.dup));
    await pg.screenshot({ path: OUT + '/19_index_files_noimage.png' }); }
  // a sandbox task, live (8799: the canned agent writes NOTES.md): no banner, the row and the header say live; while it runs the tree is
  // the workspace now; once its commit lands the same page shows the state right after the task, for the tree and for a file opened
  // again; after "rerun from a clean state" (started elsewhere) reopening the task shows the new attempt
  if (!RO) {
    const name = PFX + 'uitree-' + Date.now().toString(36);
    if ((await pg.request.post(BASE + '/api/custom/project', { data: { name, source: 'empty' } })).ok()) {
      await pg.goto(BASE + '/?cid=' + name); await sleep(2500);
      check('empty project: "No task yet", no switcher', /No task yet/.test(await T('#transcript')) && !(await arm()).arm, await T('#transcript'));
      await pg.$$eval('#taskarms input[data-k]', e => e.forEach(x => { if ((x.dataset.k === 'kv') !== x.checked) x.click(); }));
      await pg.fill('#q', 'Write NOTES.md'); await pg.click('#send');
      await pg.waitForFunction(() => document.querySelector('#playbar').classList.contains('live'), null, { timeout: 60000 }).catch(() => {});
      await head(/Task 1/); await sleep(500); const midA = await arm(), midH = await IT('#chathead'), midTxt = await pg.evaluate(() => document.body.innerText);
      check('live sandbox task: no banner, no developer words (arm ids, runner, server paths)', !(await pg.$('#livebanner')) && !/mem_|custom_runner|LEDGER|\/srv\/|ledger calls|graded/.test(midTxt), (midTxt.match(/mem_\w+|custom_runner|LEDGER|\/srv\/\S+|ledger calls|graded/g) || []).join(', '));
      check('live sandbox task: no switcher; the header names the method and says live; the row shows it live', !midA.arm && /Knowledge Vault/.test(midH) && /live$/.test(midH) && (await pg.$$('#chains .live-chip')).length >= 1 && !/Task \d+ of 0/.test(midH), `${midH} · ${await T('#chains .chain.open[data-proj] ~ .sessions')}`);
      await pg.click('#tabs button[data-t="files"]'); let nowSrc = '', notes = '';
      for (let i = 0; i < 600 && await live(); i++) { await sleep(150); const s = await T('.ftools .src'); if (/workspace now/.test(s)) nowSrc = s;
        const r = await pg.$('#ftree .tr.f[data-f="NOTES.md"]:not(.miss)'); if (r && !notes) { await r.click(); await fileReady(); notes = await T('#pane'); await pg.click('#pane #back').catch(() => {}); } }
      check('live sandbox task: the tree is the workspace now while it runs', /Files in Knowledge Vault's workspace now · \d+ files?/.test(nowSrc), nowSrc || '(never shown)');
      if (notes) check('live sandbox task: a file the agent just wrote opens from the working copy', /In the workspace now/.test(notes), notes.slice(0, 120));
      else console.log('   (NOTES.md landed between two refreshes of the tree: the during-the-task file view was not exercised)');
      await sleep(4000); await pg.click('#tabs button[data-t="files"]'); await treeReady(); await sleep(1500);
      check('live sandbox task: done — the toast says so in plain words, the session opens', /Task 1 of 1/.test(await T('#chathead')) && !(await arm()).arm, `${await T('#chathead')} · ${await T('#toast')}`);
      const after = await T('.ftools .src'); check('live sandbox task: after its commit the same page shows the state right after the task', /Files right after this task, in Knowledge Vault's workspace/.test(after), after);
      const nr = await pg.$('#ftree .tr.f[data-f="NOTES.md"]'); if (nr) { await nr.click(); await fileReady(); }
      check('live sandbox task: the file opened again shows "Right after this task" (no stale label)', /Right after this task/.test(await T('#pane')), (await T('#pane')).slice(0, 120));
      await pg.screenshot({ path: OUT + '/19_index_files_sandbox_live_after.png' });
      const c1 = await pg.evaluate(() => { const c = P.treeCache[treeKey(S.sess)]; return c && c.commit; });
      const rr = await pg.request.post(BASE + '/api/custom/start', { data: { project: name, task: '', kinds: ['kv'], order: 1, fresh: true, provider: 'local', step_limit: 10 } });
      if (rr.ok()) { for (let i = 0; i < 120; i++) { await sleep(1500); const jobs = await getJ('/api/live'); if (!jobs.some(j => j.cid === name && j.status === 'running')) break; }
        await sleep(2500); const server = await getJ(`/api/tree?cid=${name}&run=custom&arm=mem_fusednote&order=1`);
        await pg.click('#chains [data-open="kv:1"]').catch(() => {}); await sleep(1500); await pg.click('#tabs button[data-t="files"]'); await treeReady(); await sleep(1500);
        const c2 = await pg.evaluate(() => { const c = P.treeCache[treeKey(S.sess)]; return c && c.commit; });
        check('rerun from a clean state: reopening the task shows the new attempt', server.at === 'task' && server.commit !== c1 && c2 === server.commit, `before ${c1}, server ${server.commit}, page ${c2}`); }
      else check('rerun from a clean state: started', false, await rr.text());
      await pg.request.delete(BASE + '/api/custom/project/' + name);
    }
  }
  await pg.goto(BASE + '/?src=custom'); await pg.waitForSelector('#startcard', { timeout: 30000 }); check('?src=custom opens the sandbox draft', /New project/.test(await T('#chathead')));

  // ---------------------------------------------------------------- the switcher's place and the method chip, at four widths and on a phone
  // In ChainSWE chains the switcher keeps one box, to the pixel, in every state; in the sandbox it is hidden. In a browser with classic
  // scrollbars that take room (desktop Windows / Linux) at the four widths, and as a phone (overlay scrollbars, touch) at 420 px.
  // (Playwright's default hides scrollbars but still keeps the room `scrollbar-gutter` asks for: a narrowed desktop window, not a phone.)
  // The logo goes back to New project from each state it is clicked in.
  { const pj = projs.find(p => p.tasks.length && !/^(pol|r2)-/.test(p.name)) || projs.find(p => p.tasks.length);
    const lrun = (await getJ('/api/liveruns')).find(e => (e.sessions || []).length);
    const b2 = await chromium.launch({ ignoreDefaultArgs: ['--hide-scrollbars'] });
    const modes = [['classic scrollbars', await (await b2.newContext({ viewport: { width: 1500, height: 900 } })).newPage(), [2000, 1500, 1280, 420]],
      ['phone (overlay scrollbars, touch)', await (await b.newContext({ viewport: { width: 420, height: 900 }, isMobile: true, hasTouch: true })).newPage(), [420]]];
    for (const [, P] of modes) { if (RO) await P.route('**/api/**', r => r.request().method() === 'GET' ? r.continue() : r.abort());
      P.on('request', r => { if (r.method() !== 'GET' && /\/api\//.test(r.url())) writes.push(r.method() + ' ' + r.url().replace(BASE, '')); }); P.on('pageerror', e => errs.push('PAGEERROR ' + e.message)); }
    for (const [sb, P, widths] of modes) {
      const panels = async () => { await P.click('#lctoggle'); await P.click('#rctoggle'); await sleep(450); };   // collapse both side panels / show them again
      const rpanel = async () => { await P.click('#rctoggle'); await sleep(450); };
      const det = async () => { await P.click('#gearbtn'); await P.click('#details'); await P.keyboard.press('Escape'); await sleep(250); };   // ⚙ Show details on / off
      const home = async () => { await P.click('header.top a.brand'); await P.waitForSelector('#startcard', { timeout: 30000 }); };
      // [name, go, mode]: mode "sb" = sandbox (no switcher), "ch" = ChainSWE (the reference box), "hidden" = ChainSWE with the left panel collapsed
      const states = [
        ['sandbox draft', async () => { await P.goto(BASE + '/'); await P.waitForSelector('#startcard', { timeout: 30000 }); }, 'sb'],
        ['draft, both side panels collapsed', panels, 'sb'], ['draft, side panels back', panels, 'sb'],
        ...(pj ? [[`sandbox project "${pj.name}"`, async () => { await P.click(`#chains .chain[data-proj="${pj.name}"]`); await head(/Task \d|hasn't run|No task yet/, P); }, 'sb'],
          ['logo from the sandbox project', home, 'sb']] : []),
        ['ChainSWE: example chain', async () => { await P.click('#srcseg button[data-src="chains"]'); await P.waitForSelector('.excard .sess.sel', { timeout: 30000 }); }, 'ch'],
        ['ChainSWE: Mem0', async () => { await P.click('#armseg button[data-k="mem0"]'); await head(/Mem0/, P); }, 'ch'],
        ['ChainSWE: MemOp', async () => { await P.click('#armseg button[data-k="memop"]'); await head(/MemOp/, P); }, 'ch'],
        ['ChainSWE: back to Knowledge Vault', async () => { await P.click('#armseg button[data-k="kv"]'); await head(/Knowledge Vault/, P); }, 'ch'],
        ['ChainSWE, right panel collapsed', rpanel, 'ch'], ['ChainSWE, right panel back', rpanel, 'ch'],
        ['ChainSWE, both side panels collapsed', panels, 'hidden'], ['ChainSWE, side panels back', panels, 'ch'],
        ['⚙ Show details on', det, 'ch'], ['⚙ Show details off', det, 'ch'],
        ...(lrun ? [[`saved live run (${NAME[lrun.kind]} only)`, async () => { await P.click(`#chains > .chain.lrun[data-open^="${lrun.run}|${lrun.cid}|${lrun.kind}|"]`); await P.waitForSelector('#chains .chain.lrun.open', { timeout: 30000 }); await head(/Task \d/, P); }, 'ch'],
          ['logo from the saved live run', home, 'sb'], ['ChainSWE again', async () => { await P.click('#srcseg button[data-src="chains"]'); await P.waitForSelector('.excard .sess.sel', { timeout: 30000 }); }, 'ch']] : []),
        ...(!RO ? [['a live run (Knowledge Vault)', async () => { await epReady(P); await runLiveKV(P); await head(/live/, P); }, 'ch'], ['logo from the live run', home, 'sb']] : []),
        ['memory view (?view=memory)', async () => { if (!RO) await stopMine(); await P.goto(BASE + '/?view=memory'); await P.waitForSelector('#transcript .mc-wrap', { timeout: 30000 }); }, 'ch'],
        ['logo from the memory view', home, 'sb'],
      ];
      for (const w of widths) {
        await P.setViewportSize({ width: w, height: 900 }); const got = [];
        for (const [name, go, mode] of states) { await go(); await sleep(450); got.push([name, mode, await armOf(P), await swOf(P), new URL(P.url()).search]); }
        if (!RO) await stopMine();
        const chs = got.filter(g => g[1] === 'ch'), ref = chs[0][2], off = chs.filter(g => !sameBox(g[2], ref)), shownSb = got.filter(g => g[1] !== 'ch' && g[2].arm);
        console.log(`   ${sb}, ${w}px #armseg [x, y, w, h] in ChainSWE chains: ${JSON.stringify(ref.arm)} "${ref.labels}" — ` + got.map(([n, m, a, s]) => `${n}: ${a.arm ? (sameBox(a, ref) ? 'same' : JSON.stringify(a.arm)) : 'hidden'}, chip ${a.chip ? 'shown' : '—'} (${s})`).join(' · '));
        check(`${w}px, ${sb}: ChainSWE chains — the switcher has the same box in every state, to the pixel (${chs.length} states), under the toggle, right above the example chain card`, !!ref.arm && !off.length && placed(ref), off.map(g => `${g[0]}: ${JSON.stringify(g[2].arm)}`).join('; ') || JSON.stringify(ref));
        check(`${w}px, ${sb}: the switcher is hidden in every sandbox state and while the left panel is collapsed (${got.length - chs.length} states)`, !shownSb.length, shownSb.map(g => g[0]).join(', '));
        const labels = ref.fullFits ? FULL : SHORT;   // measured: the full names where all four fit on the row, else the short ones
        check(`${w}px, ${sb}: one row of four that fits, the same labels in every state — "${labels}" (${ref.fullFits ? 'the full names fit' : 'the full names do not fit'})`, chs.every(g => labelsOk(g[2]) && g[2].labels === labels), [...new Set(chs.map(g => `${g[2].labels}${g[2].over ? ' (overflows)' : ''}${g[2].oneRow ? '' : ' (two rows)'}`))].join(' / '));
        // the method chip: always in the sandbox; in ChainSWE chains only where the switcher is not on screen (left panel collapsed, a phone)
        const chipBad = got.filter(([n, m, a]) => /Task \d|live/.test(n) || /project|Mem0|MemOp|Knowledge Vault|example chain|saved|panel|details/.test(n)).filter(([n, m, a]) => {
          if (/draft|logo/.test(n)) return false;                         // no session open: no chip
          return m === 'sb' ? !a.chip : m === 'hidden' ? !a.chip : w <= 860 ? !a.chip : !!a.chip; });
        check(`${w}px, ${sb}: the method chip shows in the sandbox, in ChainSWE chains ${w <= 860 ? 'too (phone width)' : 'only while the left panel is collapsed'}`, !chipBad.length, chipBad.map(([n, , a]) => `${n}: ${a.chip || 'none'}`).join('; '));
        const homeBad = got.filter(([n]) => /^logo/.test(n)).filter(g => g[4] !== '');
        check(`${w}px, ${sb}: the logo goes to Your sandbox → New project from every state (URL "/")`, !homeBad.length, homeBad.map(g => `${g[0]}: ${g[4]}`).join('; '));
        if (w === 1500 || w === 420) await P.screenshot({ path: OUT + `/21_index_switcher_${w}_${sb.split(' ')[0]}.png` });
      }
    }
    // the switcher at seven widths, light and dark (classic scrollbars): one row of four inside the panel, nothing running over; the full
    // names where they fit (measured), else KV · Mem0 · MemOp · None with each full name in its tooltip. And the rule is a measurement, not
    // a breakpoint: the same 1500 px window with a wider panel shows the full names
    { const P = modes[0][1], got = [];
      for (const scheme of ['light', 'dark']) { await P.emulateMedia({ colorScheme: scheme }); await P.setViewportSize({ width: 1500, height: 900 }); await P.goto(BASE + '/?src=chains'); await P.waitForSelector('.excard .sess.sel', { timeout: 30000 }); await sleep(500);
        for (const w of [2000, 1500, 1400, 1366, 1280, 1050, 420]) { await P.setViewportSize({ width: w, height: 900 }); await sleep(450); const a = await armOf(P);
          const tips = await P.$$eval('#armseg button', e => e.map(x => x.title.split('\n').pop())), tipOk = tips.every((t, i) => t.startsWith(['Knowledge Vault', 'Mem0', 'MemOp', 'No memory'][i]));
          got.push([scheme, w, a, tipOk]); if ([1500, 1366, 1050, 420].includes(w)) { const x = await P.evaluate(() => { const r = document.querySelector('aside.left').getBoundingClientRect(); return { x: r.x, w: r.width }; });
            await P.screenshot({ path: OUT + `/25_index_switcher_${scheme}_${w}.png`, clip: { x: Math.max(0, x.x), y: 0, width: Math.min(w, x.w + 2), height: w <= 860 ? 420 : 300 } }); } } }
      await P.emulateMedia({ colorScheme: 'light' });
      const bad = got.filter(([, , a, t]) => !labelsOk(a) || !t);
      console.log('   switcher labels by width: ' + got.filter(g => g[0] === 'light').map(([, w, a]) => `${w}px "${a.labels}"`).join(' · '));
      check('switcher at 2000 / 1500 / 1400 / 1366 / 1280 / 1050 / 420 px, light and dark: one row of four inside the panel, nothing runs over, full names where they fit (measured) else KV · Mem0 · MemOp · None, every tooltip names its method in full', !bad.length, bad.map(([sc, w, a]) => `${sc} ${w}px: "${a.labels}" fullFits ${a.fullFits} over ${a.over} oneRow ${a.oneRow} inPanel ${a.inPanel}`).join('; ') || 'ok');
      await P.setViewportSize({ width: 1500, height: 900 }); await sleep(300); const before = await armOf(P);
      await P.evaluate(() => document.querySelector('.app').style.setProperty('--lw', '440px')); await sleep(600); const wide = await armOf(P);
      await P.evaluate(() => document.querySelector('.app').style.removeProperty('--lw')); await sleep(600); const back = await armOf(P);
      check('switcher: the labels follow a measurement — a 440 px panel at the same 1500 px width fits the full names, 340 px goes back', wide.fullFits && wide.labels === FULL && labelsOk(wide) && back.labels === before.labels && labelsOk(back), `340 px "${before.labels}" · 440 px "${wide.labels}" · back "${back.labels}"`); }
    // the logo on Compare and Report also lands on New project
    { const P = modes[0][1]; await P.setViewportSize({ width: 1500, height: 900 });
      for (const pgName of ['compare', 'report']) { await P.goto(BASE + '/' + pgName); await P.waitForSelector('header.top a.brand', { timeout: 30000 }); await P.click('header.top a.brand'); await P.waitForSelector('#startcard', { timeout: 30000 }).catch(() => {});
        check(`logo on ${pgName}: lands on Your sandbox → New project`, new URL(P.url()).pathname.endsWith('/') && new URL(P.url()).search === '' && /New project/.test(await P.textContent('#chathead').catch(() => '')), P.url()); } }
    // the right panel's tab strip: no scrollbar under it, the last tab clear of the » button; all five tabs whole from 1280 px up; at
    // 1050 px (a 340 px panel) all five names (the counts may go); it still scrolls (without a bar) when it really overflows
    { const tctx = await b2.newContext({ viewport: { width: 1500, height: 900 } }), TP = await tctx.newPage();
      if (RO) await TP.route('**/api/**', r => r.request().method() === 'GET' ? r.continue() : r.abort());
      TP.on('request', r => { if (r.method() !== 'GET' && /\/api\//.test(r.url())) writes.push(r.method() + ' ' + r.url().replace(BASE, '')); }); TP.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
      const strip = () => TP.evaluate(() => { const t = document.querySelector('#tabs'), tb = t.getBoundingClientRect(), g = document.querySelector('#rctoggle').getBoundingClientRect();
        const bs = [...t.querySelectorAll('button')].map(b => { const r = b.getBoundingClientRect(), lab = [...b.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim(), c = b.querySelector('.cnt');
          return { lab, l: r.left, r: r.right, cnt: c ? getComputedStyle(c).display !== 'none' && c.textContent.trim() !== '' : false }; });
        return { bar: t.offsetHeight - t.clientHeight, over: t.scrollWidth - t.clientWidth, left: tb.left + t.clientLeft, toggle: g.left, bs }; });
      for (const [what, u] of [['ChainSWE chains', '/?src=chains'], ...(pj ? [[`sandbox project "${pj.name}"`, '/?cid=' + encodeURIComponent(pj.name)]] : [])]) {
        await TP.goto(BASE + u); await head(/Task \d|hasn't run|No task yet/, TP); await sleep(900); const bad = [];
        for (const w of [2000, 1500, 1280, 1050]) { await TP.setViewportSize({ width: w, height: 900 }); await sleep(450); const m = await strip(), last = m.bs[m.bs.length - 1];
          const whole = m.bs.every(b => b.l >= m.left - 0.5 && b.r <= m.toggle - 2), labels = m.bs.map(b => b.lab).join(' ');
          if (m.bar !== 0 || last.r > m.toggle - 2 || !whole || m.bs.length !== 5) bad.push(`${w}px: bar ${m.bar}, last "${last.lab}" ends ${last.r.toFixed(1)} vs » at ${m.toggle.toFixed(1)}, ${labels}`);
          if (w === 1500 || w === 1050) await TP.screenshot({ path: OUT + `/23_index_tabs_${w}_${what.split(' ')[0]}.png`, clip: { x: w - 440, y: 0, width: 440, height: 140 } }); }
        check(`tab strip (${what}): no scrollbar under the tabs, all five whole and clear of the » button at 2000 / 1500 / 1280 / 1050 px`, !bad.length, bad.join('; ') || 'ok'); }
      await TP.setViewportSize({ width: 1500, height: 900 }); await sleep(300);
      const sc = await TP.evaluate(() => { const t = document.querySelector('#tabs'), w = t.style.width; t.style.width = '220px'; t.scrollLeft = 60; const r = { over: t.scrollWidth > t.clientWidth, left: t.scrollLeft, bar: t.offsetHeight - t.clientHeight }; t.scrollLeft = 0; t.style.width = w; return r; });
      check('tab strip: still scrolls (without a bar) when it really overflows', sc.over && sc.left > 0 && sc.bar === 0, JSON.stringify(sc));
      await tctx.close(); }
    await modes[1][1].context().close(); await b2.close(); }
  // the top bar: the page nav and ⚙ in the same place and size as on Report (and Compare) — 1280 and 1500 px, and a phone at 420 px
  { const at = async (u, w, mobile) => { const c = await b.newContext({ viewport: { width: w, height: 900 }, isMobile: mobile, hasTouch: mobile }), P = await c.newPage();
      await P.route('**/api/**', r => r.request().method() === 'GET' ? r.continue() : r.abort());
      await P.goto(BASE + u); await P.waitForSelector('#gearbtn', { timeout: 30000 }); await sleep(900);
      const r = await P.evaluate(() => Object.fromEntries(['.pnav', '#gearbtn'].map(s => { const b = document.querySelector(s).getBoundingClientRect(); return [s, [+b.x.toFixed(1), +b.width.toFixed(1)]]; }))); await c.close(); return r; };
    for (const [w, mobile] of [[1280, false], [1500, false], [420, true]]) { const a = await at('/', w, mobile), r = await at('/report', w, mobile);
      const same = ['.pnav', '#gearbtn'].every(s => Math.abs(a[s][0] - r[s][0]) <= 1 && Math.abs(a[s][1] - r[s][1]) <= 1);
      check(`top bar ${w}px${mobile ? ' (phone)' : ''}: page nav and ⚙ where Report has them (x and width within 1 px)`, same, `agent ${JSON.stringify(a)} · report ${JSON.stringify(r)}`); } }

  // ---------------------------------------------------------------- the server can't be reached: one card, controls off, Try again reloads
  { const fctx = await b.newContext({ viewport: { width: 1500, height: 900 } }), fp = await fctx.newPage(); let block = true, loads = 0;
    await fp.route('**/api/**', r => { if (r.request().method() !== 'GET' || (block && /\/api\/meta/.test(r.request().url()))) return r.abort(); return r.continue(); });
    fp.on('load', () => loads++); const fe = []; fp.on('pageerror', e => fe.push(e.message));
    await fp.goto(BASE + '/'); await fp.waitForSelector('.bootcard', { timeout: 15000 }).catch(() => {});
    const card = ((await fp.textContent('#transcript').catch(() => '')) || '').replace(/\s+/g, ' ').trim();
    check('server unreachable: "Couldn\'t reach the demo server" card, Run ▶ and the box off, no page error', /Couldn't reach the demo server/.test(card) && /try again in a moment/.test(card) && await fp.$eval('#send', e => e.disabled) && await fp.$eval('#q', e => e.disabled) && !fe.length, `${card} · ${fe.join(' | ')}`);
    await fp.screenshot({ path: OUT + '/22_index_bootfail.png' });
    block = false; const l0 = loads; await fp.click('#bootretry'); await fp.waitForSelector('#startcard', { timeout: 30000 }).catch(() => {});
    check('server unreachable: "Try again" reloads the page, which then loads', loads > l0 && !!(await fp.$('#startcard')), `loads ${l0} → ${loads}`);
    await fctx.close(); }

  // ---------------------------------------------------------------- widths, dark
  for (const [w, h] of [[1366, 768], [1280, 800], [420, 900]]) {
    await pg.setViewportSize({ width: w, height: h }); await pg.goto(BASE + '/'); await pg.waitForSelector('#startcard'); await sleep(700);
    let sx = await pg.evaluate(() => document.documentElement.scrollWidth); check(`${w}px draft: no horizontal overflow`, sx <= w, `scrollWidth=${sx}`); await pg.screenshot({ path: OUT + `/15_index_${w}_draft.png`, fullPage: w < 500 });
    await pg.click('#srcseg button[data-src="chains"]'); await pg.waitForSelector('.excard .sess[data-ex]'); await sleep(1200);
    sx = await pg.evaluate(() => document.documentElement.scrollWidth); const lx = await pg.$eval('#chains', e => e.scrollWidth <= e.clientWidth + 1);
    check(`${w}px chains: no horizontal overflow (page and left list)`, sx <= w && lx, `scrollWidth=${sx}`); await pg.screenshot({ path: OUT + `/16_index_${w}_chains.png`, fullPage: w < 500 });
    const kc = await pg.$eval('#chathead .kchip', e => getComputedStyle(e).display !== 'none' && /Knowledge Vault/.test(e.textContent)).catch(() => false);
    if (w < 861) check(`${w}px: the open method is named next to the transcript (the switcher scrolls away)`, kc); else check(`${w}px: only the switcher names the open method (no duplicate chip)`, !kc);
    if (w < 861) for (const pw of [w, 390]) {   // ⚙ opens on screen under the top bar, also when the bar wraps to three rows (390 px), and its checkboxes work
      await pg.setViewportSize({ width: pw, height: h }); await sleep(300); await pg.evaluate(() => scrollTo(0, 0)); await gear(true);
      const gp = await pg.evaluate(() => { const r = document.querySelector('#gearpanel').getBoundingClientRect(), d = document.querySelector('#details').getBoundingClientRect(); return { l: Math.round(r.left), r: Math.round(r.right), b: Math.round(r.bottom), vw: document.documentElement.clientWidth, vh: innerHeight, dx: Math.round(d.left) }; });
      let ok = true; try { await pg.click('#details', { timeout: 5000 }); } catch { ok = false; } const on = await pg.evaluate(() => document.body.classList.contains('details'));
      if (on) await pg.click('#details'); await pg.screenshot({ path: OUT + `/16_index_${pw}_gear.png` }); await gear(false);
      check(`${pw}px: ⚙ opens on screen under the top bar and "Show details" can be clicked`, gp.l >= 0 && gp.r <= gp.vw && gp.b <= gp.vh && gp.dx >= 0 && ok && on && !(await pg.evaluate(() => document.body.classList.contains('details'))), JSON.stringify(gp)); }
    await pg.setViewportSize({ width: w, height: h }); await sleep(200);
    await pg.click('#tabs button[data-t="files"]'); await treeReady(); await sleep(300); await pg.$eval('#ftree', e => e.scrollIntoView());
    sx = await pg.evaluate(() => document.documentElement.scrollWidth); const rw = await pg.$$eval('#ftree .tr', e => Math.max(...e.map(x => x.getBoundingClientRect().right)));
    check(`${w}px Files tree: no horizontal overflow`, sx <= w && rw <= w, `scrollWidth=${sx} rows end at ${Math.round(rw)}`); await pg.screenshot({ path: OUT + `/16_index_${w}_files.png` });
  }
  await pg.setViewportSize({ width: 1500, height: 900 }); await pg.emulateMedia({ colorScheme: 'dark' }); await pg.goto(BASE + '/'); await pg.waitForSelector('#startcard'); await sleep(600);
  const bg = await pg.evaluate(() => getComputedStyle(document.body).backgroundColor); check('dark scheme applies', !/255, 255, 255|243, 243, 240/.test(bg), bg); await pg.screenshot({ path: OUT + '/17_index_dark_draft.png' });
  await pg.click('#srcseg button[data-src="chains"]'); await pg.waitForSelector('.excard .sess[data-ex]'); await sleep(1000); await pg.screenshot({ path: OUT + '/18_index_dark_chains.png' });
  // a dimmed method in the dark: open a saved live run of one method (the other two dimmed)
  const lr1 = (await main()).find(e => /^live_/.test(e) && !/\|$/.test(e));
  if (lr1) { await pg.click(`#chains > .chain.lrun[data-open="${lr1}"]`); await sleep(1500);
    const dk = await armContrast(), dd = dk[KS.find(k => dk[k].na)], oo = dk[KS.find(k => !dk[k].na)];
    check('dark: a dimmed method is muted with a faded dot yet readable (≥ 4.5:1)', !!dd && dd.cr >= 4.5 && dd.dot < 1 && !!oo && oo.dot === 1 && dd.c !== oo.c, JSON.stringify(dk));
    await pg.screenshot({ path: OUT + '/17_index_dark_switcher.png' }); await pg.click('.excard .sess[data-ex="1"]'); await sleep(1200); }
  await pg.click('#tabs button[data-t="files"]'); await treeReady(); await sleep(300);
  const ec = await pg.$eval('#ftree .tr.f.ed', e => getComputedStyle(e).backgroundColor).catch(() => ''); check('dark: Files tree highlights edited files', /^(rgba?|color)\(/.test(ec) && !/^rgba\(0, 0, 0, 0\)$/.test(ec), ec);
  await pg.screenshot({ path: OUT + '/18_index_dark_files.png' });
  // light: the dimmed method's contrast
  await pg.emulateMedia({ colorScheme: 'light' });
  if (lr1) { await pg.click(`#chains > .chain.lrun[data-open="${lr1}"]`); await sleep(1500); const lc = await armContrast(), dim = KS.find(k => lc[k].na), ok = KS.find(k => !lc[k].na);
    check('light: a dimmed method stays readable (≥ 4.5:1) yet quieter than the available one, its dot faded', !!dim && !!ok && lc[dim].cr >= 4.5 && lc[dim].cr < lc[ok].cr && lc[dim].dot < 1 && lc[ok].dot === 1, JSON.stringify(lc)); }

  if (RO) check('read-only: the page attempted no write request', !writes.length, writes.join(', '));
  console.log('errors:', errs.length ? errs.join('\n') : 'none'); check('no page errors', !errs.some(e => /PAGEERROR/.test(e)));
  console.log('SUMMARY', results.filter(r => r[0] === 'FAIL').length, 'failed of', results.length);
  await b.close(); process.exit(results.some(r => r[0] === 'FAIL') ? 1 : 0);
})().catch(e => { console.error('FAILED', e); process.exit(1); });
