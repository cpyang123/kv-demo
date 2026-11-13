// End to end in "Your sandbox" (replay server :8799, canned model, REAL custom_runner + Apptainer `3.12` image):
// draft → Empty folder task with KV + Mem0 → second task gets the memory; Your files (2 files, a folder, a single .zip);
// Git repo (https://github.com/octocat/Hello-World, needs network) and a failing git URL that keeps the draft; Add files… to an
// existing project then a task that sees them; Download files returns a zip; rename and delete. The sandbox has no memory switcher
// (hidden at 2000 / 1500 / 1280 / 420 px from the draft through a live task to the finished one): "Run with" picks the methods (also
// when changed while the project is still being made) and each task row's chips open a method's session or watch it live. While a task
// runs: no banner and no developer words, every running method live on its row, Run ▶ waits for a ticked method that is still busy, a
// reload re-attaches to the live view, ■ Stop stops the task for every method and the view stays on the method you watched. Tasks are
// named by their summary (else the title); toasts in plain words; the newest project first. While the agent works, Changes says so
// ("No changes yet — the agent is still working."); a task stopped before it finished says its changes stay in the workspace, the right
// panel says there is nothing to show, and its row is marked "stopped" before its ▶ chips. MemOp (unticked under "Run with" by default): a
// task with Knowledge Vault + MemOp, watched on MemOp live (header, status line, Run ▶ waiting names it), its chips, the guidance-document
// wording, ⋯ Download of its workspace, the ↻ panel, and ■ Stop for both methods. Late answers: Add files… and Rename answered after a
// switch to ChainSWE chains leave the chain card there (the renamed project opens back in the sandbox); a failed GET /api/live right
// after a task starts on two methods does not stop the page from learning that the unwatched one ended.
// Never point this at a server with real model keys: every task here calls the model.
// PREFIX= starts the name of every project this makes (e.g. PREFIX=me- → me-empty-…, me-look-at-the…, me-Hello-World…); the git
// project, named after its repo, is renamed as soon as its task is done (also when the run stops early).
const { chromium } = require('playwright'); const fs = require('fs'), path = require('path');
const BASE = process.env.BASE || 'http://127.0.0.1:8799'; const OUT = process.env.SHOTS || path.join(__dirname, 'shots'); fs.mkdirSync(OUT, { recursive: true });
if (/:8765\b|:8766\b/.test(BASE)) { console.error('ui_sandbox.js runs agent tasks — use the replay server (8799), not', BASE); process.exit(2); }
const sleep = ms => new Promise(r => setTimeout(r, ms)); const results = []; const check = (name, ok, info = '') => { results.push([ok ? 'PASS' : 'FAIL', name, info]); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${info ? ' — ' + info : ''}`); };
const PFX = process.env.PREFIX || '';
// a project named after its task gets the server's slug of the task text (demo/server.py _hint_slug): its first five words, lowercased,
// joined by "-", at most 40 characters; "-2", "-3" … when the name is taken. A task text starting "<prefix>: …" gives "<prefix>-…", so
// the prefix's own words count among the five (PREFIX=me- → me-look-at-the-files, PREFIX=r2-v-s- → r2-v-s-look-at)
const hintSlug = s => { const ws = (s.match(/[A-Za-z0-9]+/g) || []).slice(0, 5).map(w => w.toLowerCase()); let out = ws.length ? ws[0].slice(0, 40) : '';
  for (const w of ws.slice(1)) { if (out.length + 1 + w.length > 40) break; out += '-' + w; } return out; };
const reEsc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const PT = PFX ? PFX.replace(/[-_. ]+$/, '') + ': ' : '';

// ---- fixtures: two files, a folder, and a .zip whose members share one top folder (stripped by the server)
const FX = path.join(OUT, 'fixtures'); fs.rmSync(FX, { recursive: true, force: true }); fs.mkdirSync(path.join(FX, 'folder/sub'), { recursive: true });
fs.writeFileSync(path.join(FX, 'a.txt'), 'alpha\n'); fs.writeFileSync(path.join(FX, 'calc.py'), 'def add(a, b):\n    return a + b\n');
fs.writeFileSync(path.join(FX, 'folder/README.md'), '# folder\n'); fs.writeFileSync(path.join(FX, 'folder/sub/data.csv'), 'x,y\n1,2\n');
const crcT = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
const crc32 = b => { let c = 0xFFFFFFFF; for (const x of b) c = crcT[(c ^ x) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; };
function zip(entries) {   // stored (uncompressed) zip, enough for a test fixture
  const loc = [], cen = []; let off = 0;
  for (const [name, data] of entries) { const n = Buffer.from(name), d = Buffer.from(data), c = crc32(d);
    const h = Buffer.alloc(30); h.writeUInt32LE(0x04034b50, 0); h.writeUInt16LE(20, 4); h.writeUInt32LE(c, 14); h.writeUInt32LE(d.length, 18); h.writeUInt32LE(d.length, 22); h.writeUInt16LE(n.length, 26);
    const ce = Buffer.alloc(46); ce.writeUInt32LE(0x02014b50, 0); ce.writeUInt16LE(20, 4); ce.writeUInt16LE(20, 6); ce.writeUInt32LE(c, 16); ce.writeUInt32LE(d.length, 20); ce.writeUInt32LE(d.length, 24); ce.writeUInt16LE(n.length, 28); ce.writeUInt32LE(off, 42);
    loc.push(h, n, d); cen.push(ce, n); off += 30 + n.length + d.length; }
  const cd = Buffer.concat(cen), e = Buffer.alloc(22); e.writeUInt32LE(0x06054b50, 0); e.writeUInt16LE(entries.length, 8); e.writeUInt16LE(entries.length, 10); e.writeUInt32LE(cd.length, 12); e.writeUInt32LE(off, 16);
  return Buffer.concat([...loc, cd, e]);
}
fs.writeFileSync(path.join(FX, 'bundle.zip'), zip([['proj-main/README.md', '# zipped\n'], ['proj-main/notes.txt', 'n\n'], ['proj-main/src/app.py', 'print(1)\n']]));

(async () => {
  // classic scrollbars (as on desktop Windows / Linux; Playwright hides them by default): the top-bar boxes below must hold with them too
  const b = await chromium.launch({ ignoreDefaultArgs: ['--hide-scrollbars'] }); const pg = await b.newPage({ viewport: { width: 1500, height: 900 }, acceptDownloads: true }); const errs = [], writes = [];
  pg.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  // the projects this page makes, by their current names (followed through renames and deletes): each must carry PREFIX at the end
  const mine = new Set();
  pg.on('response', async r => { try { const m = r.request().method(), u = new URL(r.url()).pathname; if (!r.ok()) return;
    if (m === 'POST' && /\/api\/custom\/project$/.test(u)) mine.add((await r.json()).name);
    else if (m === 'POST' && /\/api\/custom\/project\/rename$/.test(u)) { if (mine.delete(r.request().postDataJSON().name)) mine.add((await r.json()).name); }
    else if (m === 'DELETE' && /\/api\/custom\/project\/[^/]+$/.test(u)) mine.delete(decodeURIComponent(u.split('/').pop())); } catch {} }); pg.on('response', r => { if (r.status() >= 400 && !/api\/file|api\/custom\/project($|\?|\/[^/]+\/files)/.test(r.url())) errs.push('HTTP ' + r.status() + ' ' + r.url().replace(BASE, '')); });
  pg.on('request', r => { if (r.method() !== 'GET' && /\/api\//.test(r.url())) writes.push(r.method() + ' ' + r.url().replace(BASE, '')); });
  const T = async sel => ((await pg.textContent(sel).catch(() => '')) || '').replace(/\s+/g, ' ').trim();
  const IT = async sel => ((await pg.innerText(sel).catch(() => '')) || '').replace(/\s+/g, ' ').trim();   // what is on screen
  const live = () => pg.$eval('#playbar', e => e.classList.contains('live'));
  const projects = async () => (await (await pg.request.get(BASE + '/api/custom/projects')).json());
  const pickVia = async (btn, files) => { const [fc] = await Promise.all([pg.waitForEvent('filechooser'), pg.click(btn)]); await fc.setFiles(files); await sleep(300); };
  const arms = async ks => { for (const k of ['kv', 'mem0', 'memop', 'none']) { const i = await pg.$(`#taskarms input[data-k="${k}"]`); if ((await i.isChecked()) !== ks.includes(k)) await i.click(); } };   // as a user ticks them
  // the sandbox has no memory switcher: hidden at each width (page widths checked one after the other, back to 1500 px)
  const WS = [2000, 1500, 1280, 420];
  const swShown = () => pg.$eval('#armseg', e => !!(e.offsetWidth || e.offsetHeight)).catch(() => false);
  const noSwitcher = async () => { const o = []; for (const w of WS) { await pg.setViewportSize({ width: w, height: 900 }); await sleep(250); if (await swShown()) o.push(w); } await pg.setViewportSize({ width: 1500, height: 900 }); await sleep(200); return o; };
  const chip = () => pg.$$eval('#chathead .kchip', e => (e.find(x => x.offsetWidth || x.offsetHeight) || {}).innerText || '').then(t => t.trim()).catch(() => '');
  const head = re => pg.waitForFunction(r => new RegExp(r).test(document.querySelector('#chathead').textContent), re.source, { timeout: 60000 }).catch(() => {});
  const jobsOf = async name => (await (await pg.request.get(BASE + '/api/live')).json()).filter(j => j.custom && j.cid === name);
  const DEV = /mem_\w+|custom_runner|LEDGER|\/srv\/\S*|ledger calls|graded|run custom/g;   // developer words a live sandbox task must not show
  const waitRun = async () => { await pg.waitForFunction(() => document.querySelector('#playbar').classList.contains('live'), null, { timeout: 180000 }).catch(() => {}); const l = await live();
    await pg.waitForFunction(() => !document.querySelector('#playbar').classList.contains('live'), null, { timeout: 300000 }); await sleep(2500); return l; };
  const firstObs = async () => { await pg.click('#pb-end').catch(() => {}); await sleep(300); return (await pg.$$eval('#transcript .turn .obs', e => e.map(x => x.textContent)))[0] || ''; };
  const openProj = () => pg.$eval('#chains .chain.open[data-proj]', e => e.dataset.proj).catch(() => '');
  const newDraft = async () => { await pg.click('#chains .chain[data-new]'); await pg.waitForSelector('#startcard'); await sleep(200); };
  const ts = Date.now().toString().slice(-5);
  // give a project this run made the prefix (git projects are named after their repo): never under a running task; -ts: unique across runs
  const prefix = async name => { for (let i = 0; i < 200 && (await (await pg.request.get(BASE + '/api/live')).json()).some(j => j.custom && j.cid === name && j.status === 'running'); i++) await sleep(1500);
    const to = `${PFX}${name}-${ts}`, r = await pg.request.post(BASE + '/api/custom/project/rename', { data: { name, new_name: to } });
    if (r.ok()) { mine.delete(name); mine.add(to); } console.log(r.ok() ? `   renamed ${name} → ${to}` : `   could not rename ${name} → ${to}: ${r.status()} ${await r.text()}`); return r.ok() ? to : ''; };

  await pg.goto(BASE + '/'); await pg.waitForSelector('#startcard', { timeout: 30000 }); await pg.waitForFunction(() => !/checking/.test(document.querySelector('#epstatus').textContent), null, { timeout: 60000 });
  check('endpoint ready (canned model), Run enabled', /ready/.test(await T('#epstatus')) && !(await pg.$eval('#send', e => e.disabled)), await T('#epstatus'));

  // ---------------------------------------------------------------- 1. Empty folder, KV + Mem0
  const pE = PFX + 'empty-' + ts, tE = 'Add a NOTES.md file with a short description of this workspace.'; await pg.fill('#sc-name', pE); await pg.fill('#q', tE); await arms(['kv', 'mem0']);
  check('draft: no memory switcher at any width; Run with = KV + Mem0', !(await noSwitcher()).length && (await pg.$$eval('#taskarms input:checked', e => e.map(x => x.dataset.k).join(','))) === 'kv,mem0');
  await pg.fill('#tasksteps', '8').catch(() => {}); await pg.click('#send'); await sleep(1500);
  check('empty: task started, live view; the toast in plain words', await live() && /^Task 1 started — Knowledge Vault \+ Mem0$/.test(await T('#toast')), await T('#toast'));
  check('empty: the new project is first in Your projects, selected', (await openProj()) === pE && (await pg.$eval('#chains .chain[data-proj]', e => e.dataset.proj)) === pE, `${await openProj()} · first ${await pg.$eval('#chains .chain[data-proj]', e => e.dataset.proj)}`);
  await head(/Task 1/); await sleep(300);                         // (the live feed names the task on its first tick)
  const devW = ((await pg.evaluate(() => document.body.innerText)).match(DEV) || []).join(', ');
  check('live: no banner and no developer words (arm ids, runner, paths, "ledger calls", "graded")', !(await pg.$('#livebanner')) && !devW, devW);
  check('live: the header names the watched method (Knowledge Vault) and says live', /Knowledge Vault/.test(await chip()) && (await pg.$$eval('#chathead .chip', e => e.map(x => x.innerText.trim()))).includes('live') && /^Task 1 of 1/.test(await T('#chathead')), await T('#chathead'));
  check('live: both methods live on the task row, no ▶ offered for them', (await pg.$$eval('#chains .live-chip', e => e.map(x => x.textContent.trim()))).join(',') === 'live · KV,live · Mem0' && !(await pg.$('#chains [data-rerun="kv:1"], #chains [data-rerun="mem0:1"]')), await T('#chains .chain.open[data-proj] ~ .sessions'));
  { await pg.click('#tabs button[data-t="changes"]'); await pg.waitForFunction(() => !!(S.sess && !S.sess.exit_status && S.live && S.liveFocus), null, { timeout: 8000 }).catch(() => {});
    const ch = await T('#pane'), still = await pg.evaluate(() => !!(S.sess && !S.sess.exit_status && S.live && S.liveFocus));
    if (still) check('live: Changes says the agent is still working (not "No patch was submitted")', ch === 'No changes yet — the agent is still working.', ch);
    else check('live: Changes before the first step says the agent is starting', /The agent is starting|No changes yet — the agent is still working|NOTES\.md/.test(ch), ch); }
  // the next task waits for a ticked method that is still busy here (the server would refuse it); without them it may start
  await pg.fill('#q', 'Next task');
  const busy1 = await T('#cstatus'), dis1 = await pg.$eval('#send', e => e.disabled);
  await arms(['none']); const busy0 = await T('#cstatus'), dis0 = await pg.$eval('#send', e => e.disabled); await arms(['kv', 'mem0']); await pg.fill('#q', '');
  check('busy: Run ▶ waits while a ticked method still works on task 1, and says why; only No memory ticked → Run ▶ again', !(await live()) || (dis1 && /^Knowledge Vault and Mem0 are still working on task 1 — wait for them or stop them$|^(Knowledge Vault|Mem0) is still working on task 1 — wait for it or stop it$/.test(busy1) && !dis0 && !busy0), `"${busy1}" ${dis1} · "${busy0}" ${dis0}`);
  await pg.screenshot({ path: OUT + '/30_sandbox_live.png' });
  const hiddenLive = await noSwitcher();
  // a reload while it runs keeps the place (the URL) and re-attaches to the live view
  if (await live()) { const u = pg.url(); await pg.reload(); await pg.waitForFunction(() => document.querySelector('#chains .chain.open[data-proj]'), null, { timeout: 30000 }).catch(() => {}); await sleep(2000);
    const run = (await jobsOf(pE)).filter(j => j.status === 'running').length;
    check('reload while it runs: same project and task (the URL keeps them), the live view re-attaches, the rows show the running methods', /[?&]cid=/.test(u) && (await openProj()) === pE && (!run || (await live() && (await pg.$$('#chains .live-chip')).length === run)), `${u} · running ${run} · live ${await live()} · chips ${(await pg.$$('#chains .live-chip')).length}`); }
  await waitRun(); const toastDone = await T('#toast');
  check('no switcher at any width while the task ran and once it finished', !hiddenLive.length && !(await noSwitcher()).length, hiddenLive.join(','));
  check('empty: finished, session open', /Task 1 of 1/.test(await T('#chathead')) && /Knowledge Vault/.test(await chip()), await T('#chathead'));
  check('empty: the toast says the task is done, in plain words', /^Task 1 done — Knowledge Vault$/.test(toastDone) || !toastDone, toastDone);
  check('empty: 3 canned steps + Submitted', (await pg.$$('#transcript .turn')).length === 3 && /Submitted/.test(await T('#transcript')));
  check('empty: agent saw an empty workspace', /sandbox: empty workspace/.test(await firstObs()));
  await pg.click('#tabs button[data-t="changes"]'); await sleep(300); check('empty: Changes has NOTES.md', /NOTES\.md/.test(await T('#pane')));
  await pg.waitForFunction(() => document.querySelectorAll('#chains .chip.tiny[data-open]').length >= 2, null, { timeout: 120000 }).catch(() => {});
  check('empty: KV and Mem0 chips on the task row, MemOp ▶ and No memory ▶ to run the others', (await pg.$$eval('#chains .chip.tiny[data-open]', e => e.map(x => x.dataset.open))).sort().join(',') === 'kv:1,mem0:1' && !!(await pg.$('#chains [data-rerun="memop:1"]')) && !!(await pg.$('#chains [data-rerun="none:1"]')) && (await pg.$$eval('#chains .chips [data-rerun]', e => e.map(x => x.innerText.trim()).join(' | '))) === 'MemOp ▶ | No memory ▶', (await T('#chains .chain.open + .pmenu, #chains .chain.open ~ .sessions')).slice(0, 120));
  { const t1 = (await projects()).find(p => p.name === pE)?.tasks?.[0] || {}, want = t1.summary || t1.title, got = await pg.$eval('#chains .chain.open[data-proj] ~ .sessions .task-row .tt', e => [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim());
    check(`empty: the task row names the task (${t1.summary ? "the server's summary" : 'no summary from the server yet: the title'})`, !!want && got === want, `"${got}" vs "${want}"`);
    check('empty: the pinned task names it too', (await T('#taskpin .tp-title')) === want, await T('#taskpin .tp-title')); }
  check('empty: the Task tab (not "Issue")', (await T('#tabs button[data-t="issue"]')) === 'Task');
  const w1 = writes.length; await pg.click('#chains [data-open="mem0:1"]'); await head(/Mem0/); await sleep(500);
  check('empty: the Mem0 chip on the task row opens the Mem0 session (no switcher), starts nothing', /Mem0/.test(await chip()) && /Task 1 of 1/.test(await T('#chathead')) && writes.length === w1 && !(await swShown()), await T('#chathead'));
  await pg.click('#chains [data-open="kv:1"]'); await head(/Knowledge Vault/); await sleep(500);
  await pg.screenshot({ path: OUT + '/31_sandbox_done.png' });
  // second task: KV memory from task 1 is injected
  await pg.fill('#q', 'Append a second line to NOTES.md.'); await arms(['kv']); await pg.click('#send'); await waitRun();
  check('empty: task 2 runs with the memory from task 1', /Task 2 of 2/.test(await T('#chathead')) && /Memory injected/.test(await T('#transcript')), await T('#chathead'));
  check('empty: the project\'s two tasks are listed by name', (await pg.$$('#chains .chain.open[data-proj] ~ .sessions .task-row')).length === 2);

  // ---------------------------------------------------------------- 1b. ■ Stop: the task stops for every method; the view stays put
  await newDraft(); const pS = PFX + 'stop-' + ts; await pg.fill('#sc-name', pS); await pg.fill('#q', 'Add a NOTES.md file.'); await arms(['kv', 'mem0']); await pg.click('#send');
  await pg.waitForFunction(() => document.querySelector('#playbar').classList.contains('live'), null, { timeout: 120000 }).catch(() => {}); await head(/Task 1/);
  check('stop: one ■ Stop for the task, its tooltip says it stops every method', await pg.isVisible('#pb-stop') && (await pg.getAttribute('#pb-stop', 'title')) === 'Stop this task for every memory system running it (click twice)', await pg.getAttribute('#pb-stop', 'title'));
  const runS = (await jobsOf(pS)).filter(j => j.status === 'running').map(j => j.kind).sort().join(',');
  await pg.click('#pb-stop'); await sleep(200); await pg.click('#pb-stop');
  for (let i = 0; i < 80 && (await jobsOf(pS)).some(j => j.status === 'running'); i++) await sleep(500);
  await sleep(4000); const stS = (await jobsOf(pS)).map(j => `${j.kind}:${j.status}`).sort().join(',');
  check(`stop: two clicks stop the task for every method running it (${runS || 'none still running'}); the view stays on Knowledge Vault`, (await jobsOf(pS)).every(j => j.status !== 'running') && (!runS.includes(',') || (await jobsOf(pS)).filter(j => runS.includes(j.kind)).every(j => j.status === 'stopped' || j.status === 'done')) && /Knowledge Vault/.test(await chip()) && !(await live()), `${stS} · ${await T('#chathead')} · ${(await T('#transcript')).slice(0, 90)}`);
  { const jk = (await jobsOf(pS)).find(j => j.kind === 'kv'), hasSess = await pg.evaluate(() => !!S.sess);
    if (jk && jk.status === 'stopped' && !hasSess) {
      check('stop: the transcript says the task was stopped and that its changes stay in the workspace', (await T('#transcript')) === 'Task 1 was stopped. The changes it made so far stay in the workspace.', await T('#transcript'));
      const panes = []; for (const t of ['changes', 'ledger']) { await pg.click(`#tabs button[data-t="${t}"]`); await sleep(150); panes.push(await T('#pane')); }
      check('stop: the right panel says there is nothing to show (not the first-visit text), on every tab', panes.every(x => x === 'Task 1 was stopped before it finished, so there is nothing to show here.'), panes.join(' | '));
      const row = await pg.$eval('#chains .chain.open[data-proj] ~ .sessions .task-row .chips', e => e.innerText.replace(/\s+/g, ' ').trim()).catch(() => '');
      const ranAny = Object.values((await projects()).find(p => p.name === pS)?.arms || {}).some(a => (a.orders || []).includes(1));   // another method finished it
      check(`stop: the task row ${ranAny ? 'shows the method that finished (no "stopped")' : 'says "stopped" before its ▶ chips'}`, ranAny ? !/stopped/.test(row) : /^stopped KV ▶ Mem0 ▶ MemOp ▶ No memory ▶$/.test(row), row);
      await pg.reload(); await pg.waitForFunction(() => document.querySelector('#chains .chain.open[data-proj]'), null, { timeout: 30000 }).catch(() => {}); await sleep(2000);
      const row2 = await pg.$eval('#chains .chain.open[data-proj] ~ .sessions .task-row .chips', e => e.innerText.replace(/\s+/g, ' ').trim()).catch(() => '');
      if (!ranAny) check('stop: after a reload the row still says "stopped"', /^stopped /.test(row2), row2); }
    else console.log(`   (the task finished before the stop took effect — ${jk ? jk.status : 'no job'}, session ${hasSess}: the stopped-task texts are not exercised)`); }
  await pg.screenshot({ path: OUT + '/37_sandbox_stopped.png' });
  if ((await pg.request.delete(BASE + '/api/custom/project/' + encodeURIComponent(pS))).ok()) mine.delete(pS);

  // ---------------------------------------------------------------- 1c. Knowledge Vault + MemOp: watch MemOp live, its chips, Download, ↻, ■ Stop
  // (the canned model's MemOp writer gives no usable guidance — MEMOP_DISTILL_INVALID in the log on an older canned model — so the page says
  // the guidance document was not rewritten)
  await newDraft(); const pM = PFX + 'memop-' + ts; await pg.fill('#sc-name', pM); await pg.fill('#q', 'Add a NOTES.md file with a short description of this workspace.');
  check('Run with: MemOp offered, unticked by default (KV + Mem0 ticked)', (await pg.$$eval('#taskarms input[data-k]', e => e.map(x => x.dataset.k + ':' + x.checked).join(','))) === 'kv:true,mem0:true,memop:false,none:false' && /^MemOp: /.test(await pg.$eval('#taskarms input[data-k="memop"]', e => e.closest('label').title)), await pg.$$eval('#taskarms input[data-k]', e => e.map(x => x.dataset.k + ':' + x.checked).join(',')));
  await arms(['kv', 'memop']); await pg.click('#send'); await sleep(1500);
  check('memop: task started with Knowledge Vault + MemOp; the toast names both', await live() && /^Task 1 started — Knowledge Vault \+ MemOp$/.test(await T('#toast')), await T('#toast'));
  await pg.waitForFunction(() => document.querySelectorAll('#chains .live-chip').length >= 1, null, { timeout: 60000 }).catch(() => {});
  const mChip = await pg.$$('#chains .live-chip').then(async cs => { for (const c of cs) if (/MemOp/.test(await c.textContent())) return c; return null; });
  if (mChip) { const lc = await pg.$$eval('#chains .live-chip', e => e.map(x => x.textContent.trim()).join(','));
    await mChip.click(); await head(/MemOp/); await sleep(800);
    check('memop: "live · MemOp" on the task row watches the MemOp run: the header names MemOp and says live, the status line runs', await live() && /MemOp/.test(await chip()) && /^Task 1 of 1/.test(await T('#chathead')) && (await pg.$$eval('#chathead .chip', e => e.map(x => x.innerText.trim()))).includes('live') && /\d\d:\d\d/.test(await T('#pb-livetxt')) && /live · KV|live · MemOp/.test(lc), `${await T('#chathead')} · ${await T('#pb-live')} · chips ${lc}`);
    await pg.fill('#q', 'Next task'); await sleep(300); const bl = await T('#cstatus'), bd = await pg.$eval('#send', e => e.disabled); await pg.fill('#q', '');
    check('memop: Run ▶ waits for the busy methods and names MemOp', !(await live()) || (bd && /^(Knowledge Vault and MemOp are still working on task 1 — wait for them or stop them|MemOp is still working on task 1 — wait for it or stop it|Knowledge Vault is still working on task 1 — wait for it or stop it)$/.test(bl) && (/MemOp/.test(bl) || !(await jobsOf(pM)).some(j => j.kind === 'memop' && j.status === 'running'))), `"${bl}" disabled ${bd}`);
    await pg.screenshot({ path: OUT + '/38_sandbox_memop_live.png' }); }
  else console.log('   (MemOp finished before its live chip could be clicked)');
  if (await live()) await waitRun(); else await sleep(2500);
  await pg.waitForFunction(() => document.querySelectorAll('#chains .chip.tiny[data-open]').length >= 2, null, { timeout: 120000 }).catch(() => {});
  if (!(/MemOp/.test(await chip()))) { await pg.click('#chains [data-open="memop:1"]').catch(() => {}); await head(/MemOp/); await sleep(800); }
  check('memop: finished on the MemOp session (Task 1 of 1)', /Task 1 of 1/.test(await T('#chathead')) && /MemOp/.test(await chip()), await T('#chathead'));
  check('memop: the task row has KV and MemOp sessions, "Mem0 ▶" and "No memory ▶" to run the others', (await pg.$$eval('#chains .chip.tiny[data-open]', e => e.map(x => x.dataset.open))).sort().join(',') === 'kv:1,memop:1' && (await pg.$$eval('#chains .chips [data-rerun]', e => e.map(x => x.innerText.trim()).join(' | '))) === 'Mem0 ▶ | No memory ▶', await T('#chains .chain.open[data-proj] ~ .sessions .chips'));
  { const nt = await T('#transcript .tail[data-tail="note"]'), mt = await (async () => { await pg.click('#tabs button[data-t="memory"]'); await sleep(250); return T('#pane'); })(), lg = await (async () => { await pg.click('#tabs button[data-t="ledger"]'); await sleep(250); return T('#pane'); })();
    check('memop: the session speaks of MemOp\'s guidance document (transcript, Memory tab, Ledger), in plain words', /guidance document/.test(nt) && /across the project/.test(mt) && /guidance document|first task of the project/.test(mt) && /Rewriting the guidance document for the next task/.test(lg) && !((await pg.evaluate(() => document.body.innerText)).match(DEV)), `${nt.slice(0, 120)} · ${mt.slice(0, 120)}`); }
  await pg.screenshot({ path: OUT + '/39_sandbox_memop_done.png' });
  await pg.click(`#chains [data-pmenu="${pM}"]`); await sleep(300);
  check('memop: ⋯ Download files offers Knowledge Vault and MemOp (no Mem0, no No memory)', (await pg.$$eval('#chains .pm-dl a[data-dl]', e => e.map(x => x.dataset.dl).join(','))) === 'kv,memop' && /Download files: Knowledge Vault MemOp/.test(await IT('#chains .pmenu')), await IT('#chains .pmenu'));
  { const hrefM = await pg.getAttribute('#chains a[data-dl="memop"]', 'href'), rM = await pg.request.get(BASE + '/' + hrefM), bM = await rM.body();
    check('memop: the MemOp download is a zip of its workspace', rM.status() === 200 && /zip/.test(rM.headers()['content-type'] || '') && bM.slice(0, 2).toString() === 'PK' && bM.includes('NOTES.md'), `${rM.status()} ${bM.length} B`);
    const [dlM] = await Promise.all([pg.waitForEvent('download', { timeout: 15000 }).catch(() => null), pg.click('#chains a[data-dl="memop"]')]);
    check('memop: clicking it downloads <project>-memop.zip', !!dlM && dlM.suggestedFilename() === `${pM}-memop.zip`, dlM ? dlM.suggestedFilename() : 'no download event'); }
  await pg.screenshot({ path: OUT + '/40_sandbox_memop_menu.png' });
  await pg.click('#chains [data-pm="cancel"]'); await sleep(200);
  { const w0 = writes.length; await pg.click('#chains [data-rr="1"]'); await sleep(300);
    check('memop: ↻ opens the rerun panel with MemOp, the methods that ran ticked (KV + MemOp); it starts nothing', (await IT('.rrpanel .rr-ks')) === 'KV Mem0 MemOp No memory' && (await pg.$$eval('.rrpanel [data-rrk]', e => e.filter(x => x.checked).map(x => x.dataset.rrk).join(','))) === 'kv,memop' && writes.length === w0, `${await IT('.rrpanel .rr-ks')} · ${await pg.$$eval('.rrpanel [data-rrk]', e => e.filter(x => x.checked).map(x => x.dataset.rrk).join(','))}`);
    await pg.screenshot({ path: OUT + '/41_sandbox_memop_rerun.png' }); await pg.click('.rrpanel [data-rra="cancel"]'); await sleep(200); }
  // a second task with both, watched on MemOp; ■ Stop stops it for both methods and the view stays on MemOp
  await pg.fill('#q', 'Append a second line to NOTES.md.'); await arms(['kv', 'memop']); await pg.click('#send');
  await pg.waitForFunction(() => document.querySelector('#playbar').classList.contains('live'), null, { timeout: 120000 }).catch(() => {}); await head(/Task 2/);
  check('memop: task 2 opens on MemOp (the method on screen), live', await live() && /MemOp/.test(await chip()) && /^Task 2 of 2/.test(await T('#chathead')), await T('#chathead'));
  const runM = (await jobsOf(pM)).filter(j => j.status === 'running' && j.order === 2).map(j => j.kind).sort().join(',');
  await pg.click('#pb-stop'); await sleep(200); await pg.click('#pb-stop'); await sleep(600); const stT = await T('#toast');
  for (let i = 0; i < 80 && (await jobsOf(pM)).some(j => j.status === 'running'); i++) await sleep(500);
  await sleep(4500);
  check(`memop: ■ Stop stops task 2 for every method running it (${runM || 'none still running'}), the toast names MemOp, the view stays on MemOp`, (await jobsOf(pM)).every(j => j.status !== 'running') && (!runM || /^Stopping task 2 — (MemOp \+ Knowledge Vault|Knowledge Vault \+ MemOp|MemOp|Knowledge Vault)$/.test(stT)) && /MemOp/.test(await chip()) && !(await live()), `${stT} · ${(await jobsOf(pM)).map(j => `${j.order}:${j.kind}:${j.status}`).join(',')} · ${await T('#chathead')}`);
  { const row = await pg.$$eval('#chains .chain.open[data-proj] ~ .sessions .task-row .chips', e => (e[1] || {}).innerText || '').then(t => t.replace(/\s+/g, ' ').trim()), ranAny = Object.values((await projects()).find(p => p.name === pM)?.arms || {}).some(a => (a.orders || []).includes(2));
    if (!ranAny) check('memop: the stopped task\'s row says "stopped" before its four ▶ chips', /^stopped KV ▶ Mem0 ▶ MemOp ▶ No memory ▶$/.test(row), row); }
  await pg.screenshot({ path: OUT + '/42_sandbox_memop_stopped.png' });
  if ((await pg.request.delete(BASE + '/api/custom/project/' + encodeURIComponent(pM))).ok()) mine.delete(pM);

  // ---------------------------------------------------------------- 2. Your files: two files + a folder (file and folder pickers)
  await newDraft(); check('draft resets', (await pg.$eval('#sc-name', e => e.value)) === '' && (await pg.getAttribute('[data-from="empty"]', 'aria-pressed')) === 'true');
  await pg.click('[data-from="upload"]'); await pickVia('#sc-pickf', [path.join(FX, 'a.txt'), path.join(FX, 'calc.py')]);
  check('files: summary "2 files · … B" with a clear control', /2 files · \d+ B/.test(await T('#sc-sum')) && !!(await pg.$('#sc-clear')), await T('#sc-sum'));
  await pg.click('#sc-clear'); check('files: × clears', /No files/.test(await T('#sc-sum')));
  await pickVia('#sc-pickf', [path.join(FX, 'a.txt'), path.join(FX, 'calc.py')]); await pickVia('#sc-pickd', path.join(FX, 'folder'));
  check('files: folder picker adds the folder', /4 files/.test(await T('#sc-sum')), await T('#sc-sum'));
  await pg.screenshot({ path: OUT + '/32_sandbox_files_draft.png' });
  await pg.fill('#q', PT + 'Look at the files and write NOTES.md.'); await arms(['kv']); await pg.click('#send'); await waitRun();
  const fName = hintSlug(PT + 'Look at the files and write NOTES.md.'), pF = await openProj();
  check(`files: project named after the task ("${fName}", the server's five-word rule)`, new RegExp('^' + reEsc(fName) + '(-\\d+)?$').test(pF), pF);
  const oF = await firstObs(); check('files: agent sees both files and the folder', /a\.txt/.test(oF) && /calc\.py/.test(oF) && /folder/.test(oF), oF.slice(0, 200));
  check('files: label "Your files · Python 3.12"', /Your files · Python 3\.12/.test(await T('#chains .chain.open[data-proj]')), await T('#chains .chain.open[data-proj]'));

  // ---------------------------------------------------------------- 3. a single .zip, unpacked
  await newDraft(); await pg.click('[data-from="upload"]'); await pickVia('#sc-pickf', path.join(FX, 'bundle.zip'));
  check('zip: summary says it is unpacked', /unpacked/.test(await T('#sc-sum')), await T('#sc-sum'));
  // "Run with" changed while the project is still being made (its POST held back 3 s, as for a big upload or a slow clone): the task starts
  // with what "Run with" shows by then; the page watches the first of them (Knowledge Vault), and the task row's live chip of the other
  // one (No memory) watches that run instead
  await pg.fill('#q', PT + 'Summarise the zipped project in NOTES.md.'); await arms(['kv']);
  let startKinds = null; const onStart = r => { if (r.method() === 'POST' && /\/api\/custom\/start$/.test(new URL(r.url()).pathname)) startKinds = (r.postDataJSON() || {}).kinds; };
  pg.on('request', onStart); await pg.route('**/api/custom/project', async r => { if (r.request().method() === 'POST') await sleep(3000); await r.continue(); });
  const w2 = writes.length; await pg.click('#send'); await pg.waitForFunction(() => /creating the project/.test(document.querySelector('#cstatus').textContent), null, { timeout: 10000 }).catch(() => {});
  const mid = await T('#cstatus'); await pg.click('#taskarms input[data-k="none"]'); await sleep(150); const midNone = await pg.isChecked('#taskarms input[data-k="none"]');
  check('zip: No memory ticked under Run with while the project is being made', /creating the project/.test(mid) && midNone && !(await swShown()), `"${mid}" · none ticked ${midNone}`);
  await pg.waitForFunction(() => document.querySelector('#playbar').classList.contains('live'), null, { timeout: 180000 }).catch(() => {});
  await pg.unroute('**/api/custom/project'); pg.off('request', onStart);
  check('zip: the task starts with what Run with showed by then (KV + No memory)', JSON.stringify(startKinds) === '["kv","none"]', JSON.stringify(startKinds) + ' · ' + writes.slice(w2).join(', '));
  await head(/Task 1/); await sleep(300);
  check('zip: the live view shows Knowledge Vault (the first method ticked)', /Knowledge Vault/.test(await chip()), await T('#chathead'));
  const nl = await pg.$('#chains [data-live]') && await pg.$$eval('#chains [data-live]', e => e.map(x => x.textContent.trim()));
  const noneLive = await pg.$$('#chains .live-chip').then(async cs => { for (const c of cs) if (/No memory/.test(await c.textContent())) return c; return null; });
  if (noneLive) { await noneLive.click(); await head(/No memory/); await sleep(500);
    check('zip: the "live · No memory" chip on the task row watches the No memory run', await live() && /No memory/.test(await chip()) && /^Task 1 of 1/.test(await T('#chathead')), `${await T('#chathead')} · chips ${JSON.stringify(nl)}`); }
  else { await pg.click('#chains [data-open="none:1"]').catch(() => {}); await head(/No memory/); console.log('   (No memory finished before its live chip could be clicked: opened its session instead)'); }
  await pg.screenshot({ path: OUT + '/36_sandbox_watch_chosen.png' });
  if (await live()) await waitRun(); else await sleep(2500);
  check('zip: finished on the No memory session (a stop or finish never flips the view to another method)', /Task 1 of 1/.test(await T('#chathead')) && /No memory/.test(await chip()), await T('#chathead'));
  const oZ = await firstObs(); check('zip: unpacked, top folder stripped, zip not kept', /README\.md/.test(oZ) && /notes\.txt/.test(oZ) && /src/.test(oZ) && !/proj-main|bundle\.zip/.test(oZ), oZ.slice(0, 260));

  // ---------------------------------------------------------------- 4. Git: a bad URL keeps the draft; then Hello-World
  await newDraft(); const n0 = (await projects()).length; await pg.click('[data-from="git"]');
  check('new draft: Run with keeps your ticks (KV + No memory), still no switcher', (await pg.$$eval('#taskarms input:checked', e => e.map(x => x.dataset.k).join(','))) === 'kv,none' && !(await swShown()));
  const gT = PT + 'Describe this repository in NOTES.md.';
  await pg.fill('#sc-git', 'https://github.com/octocat/this-repo-does-not-exist-kvdemo'); await pg.fill('#q', gT); await arms(['kv']);
  await pg.click('#send');
  await pg.waitForFunction(() => !document.querySelector('#send').disabled, null, { timeout: 120000 }); await sleep(300);
  check('git error: message next to the composer', /⚠ git clone failed/.test(await T('#cstatus')) && /err/.test(await pg.getAttribute('#cstatus', 'class')), await T('#cstatus'));
  check('git error: draft kept (task text, URL, Git repo selected)', (await pg.$eval('#q', e => e.value)) === gT && /does-not-exist/.test(await pg.$eval('#sc-git', e => e.value)) && (await pg.getAttribute('[data-from="git"]', 'aria-pressed')) === 'true' && !!(await pg.$('#startcard')));
  check('git error: no project left behind', (await projects()).length === n0);
  await pg.screenshot({ path: OUT + '/33_sandbox_git_error.png' });
  await pg.fill('#sc-git', 'http://github.com/octocat/Hello-World'); await pg.click('#send'); await pg.waitForFunction(() => !document.querySelector('#send').disabled, null, { timeout: 60000 }); await sleep(300);
  check('git error: http:// refused with a plain sentence', /only https:\/\/ git URLs are supported/.test(await T('#cstatus')), await T('#cstatus'));
  // the project's name comes back from its POST (a clone happens inside it), so it gets the prefix right after its task, even if a step fails
  const made = pg.waitForResponse(r => r.request().method() === 'POST' && /\/api\/custom\/project$/.test(new URL(r.url()).pathname), { timeout: 180000 }).catch(() => null);
  await pg.fill('#sc-git', 'https://github.com/octocat/Hello-World'); await pg.click('#send'); await sleep(100);
  const st = await T('#cstatus'), gr = await made, gName = gr && gr.ok() ? (await gr.json()).name : ''; let gTo = '';
  try { await waitRun();
    const pG = await openProj(); check('git: project named after the repo', /^Hello-World/.test(pG) && pG === gName, `${pG} (status was: ${st})`);
    check('git: label shows the repo', /github\.com\/octocat\/Hello-World · Python 3\.12/.test(await T('#chains .chain.open[data-proj]')), await T('#chains .chain.open[data-proj]'));
    check('git: agent sees the cloned README', /README/.test(await firstObs()));
  } finally { if (PFX && gName && !gName.startsWith(PFX)) gTo = await prefix(gName); }
  if (PFX && gName && !gName.startsWith(PFX)) check('git: renamed with the prefix as soon as its task is done', !!gTo && (await projects()).some(p => p.name === gTo), gTo || '(rename failed)');

  // ---------------------------------------------------------------- 5. Add files to an existing project, then the next task; download
  await pg.click(`#chains .chain[data-proj="${pF}"]`); await sleep(1200);
  await pg.click(`#chains [data-pmenu="${pF}"]`); await sleep(200);
  check('menu: Add files…, Rename, Delete…, Download files (KV)', !!(await pg.$('#chains [data-pm="files"]')) && !!(await pg.$('#chains [data-pm="rename"]')) && !!(await pg.$('#chains [data-pm="confirm"]')) && !!(await pg.$('#chains a[data-dl="kv"]')), await T('#chains .pmenu'));
  check('menu: no download for a memory system that never ran', !(await pg.$('#chains a[data-dl="none"]')));
  await pg.screenshot({ path: OUT + '/34_sandbox_menu.png' });
  const href = await pg.getAttribute('#chains a[data-dl="kv"]', 'href'); const r = await pg.request.get(BASE + '/' + href); const body = await r.body();
  check('download: a zip of the KV workspace', r.status() === 200 && /zip/.test(r.headers()['content-type'] || '') && body.slice(0, 2).toString() === 'PK' && body.includes('NOTES.md') && body.includes('calc.py'), `${r.status()} ${r.headers()['content-disposition']} ${body.length} B`);
  const [dl] = await Promise.all([pg.waitForEvent('download', { timeout: 15000 }).catch(() => null), pg.click('#chains a[data-dl="kv"]')]);
  check('download: clicking the link downloads <project>-kv.zip', !!dl && dl.suggestedFilename() === `${pF}-kv.zip`, dl ? dl.suggestedFilename() : 'no download event');
  await pg.click('#chains [data-pm="files"]'); await sleep(200);
  fs.writeFileSync(path.join(FX, 'extra.txt'), 'added later\n'); await pickVia('#chains [data-pm="pickf"]', path.join(FX, 'extra.txt')); await sleep(1500);
  check('add files: toast "Added 1 file — the next task sees them"', /^Added 1 file — the next task sees them$/.test(await T('#toast')), await T('#toast'));
  await pg.fill('#q', 'List the files again.'); await arms(['kv']); await pg.click('#send'); await waitRun();
  check('add files: task 2 of the project', /Task 2 of 2/.test(await T('#chathead')), await T('#chathead'));
  check('add files: the next task sees the added file', /extra\.txt/.test(await firstObs()));
  await pg.screenshot({ path: OUT + '/35_sandbox_addfiles.png' });

  // ---------------------------------------------------------------- 6. rename + delete
  const pR = PFX + 'renamed-' + ts; await pg.click(`#chains [data-pmenu="${pE}"]`); await sleep(200); await pg.click('#chains [data-pm="rename"]'); await sleep(200);
  await pg.fill('#pm-name', pR); await pg.keyboard.press('Enter'); await sleep(1500);
  check('rename: the list shows the new name', !!(await pg.$(`#chains .chain[data-proj="${pR}"]`)) && !(await pg.$(`#chains .chain[data-proj="${pE}"]`)) && /^Renamed /.test(await T('#toast')), await T('#toast'));
  await pg.click(`#chains .chain[data-proj="${pR}"]`); await sleep(1500); check('rename: the renamed project still opens its session', /Task 1 of 2/.test(await T('#chathead')), await T('#chathead'));
  await pg.click(`#chains [data-pmenu="${pR}"]`); await sleep(200); await pg.click('#chains [data-pm="confirm"]'); await sleep(200);
  check('delete asks first', /can't be undone/.test(await T('#chains .pmenu.danger')));
  await pg.click('#chains [data-pm="delete"]'); await sleep(1800);
  check('delete: gone, back to the draft', !(await pg.$(`#chains .chain[data-proj="${pR}"]`)) && !!(await pg.$('#startcard')) && !(await projects()).some(p => p.name === pR), await T('#toast'));

  // ---------------------------------------------------------------- 7. answers that land after the viewer moved on; a failed poll
  // Add files… and Rename answered after a switch to ChainSWE chains leave the chain card there (no project list); back in the sandbox
  // the renamed project opens. One failed GET /api/live right after a task starts on two methods does not stop the polling: the method
  // the page does not watch ends too (its row stops saying live, Run ▶ is free again).
  { const gate = { slow: false, live: false }; let blocked = 0;
    await pg.route('**/api/**', async r => { const q = r.request(), u = new URL(q.url()).pathname;
      if (gate.live && q.method() === 'GET' && /\/api\/live$/.test(u)) { blocked++; return r.abort(); }
      if (gate.slow && q.method() === 'POST' && /\/(files|rename)$/.test(u)) await sleep(4000);
      return r.continue().catch(() => {}); });
    const inChains = async () => ({ pressed: await pg.$$eval('#srcseg button[aria-pressed="true"]', e => e.map(x => x.dataset.src).join()), card: !!(await pg.$('#chains .excard')), projs: (await pg.$$('#chains [data-proj]')).length });
    const chainsOk = c => c.pressed === 'chains' && c.card && !c.projs;
    await pg.click(`#chains .chain[data-proj="${pF}"]`); await sleep(1200);
    await pg.click(`#chains [data-pmenu="${pF}"]`); await sleep(200); await pg.click('#chains [data-pm="files"]'); await sleep(200);
    gate.slow = true; fs.writeFileSync(path.join(FX, 'late.txt'), 'late\n'); await pickVia('#chains [data-pm="pickf"]', path.join(FX, 'late.txt'));
    await pg.click('#srcseg button[data-src="chains"]'); await sleep(6000);
    let c = await inChains(); check('late answer: Add files… answered in ChainSWE chains leaves the chain card (no project list)', chainsOk(c) && /^Added 1 file/.test(await T('#toast')), `${JSON.stringify(c)} · ${await T('#toast')}`);
    await pg.click('#srcseg button[data-src="custom"]'); await sleep(2500);
    const pF2 = pF + '-r'; await pg.click(`#chains [data-pmenu="${pF}"]`); await sleep(200); await pg.click('#chains [data-pm="rename"]'); await sleep(200);
    await pg.fill('#pm-name', pF2); await pg.keyboard.press('Enter'); await sleep(150); await pg.click('#srcseg button[data-src="chains"]'); await sleep(6000); gate.slow = false;
    c = await inChains(); check('late answer: Rename answered in ChainSWE chains leaves the chain card (no project list)', chainsOk(c), JSON.stringify(c));
    await pg.click('#srcseg button[data-src="custom"]'); await sleep(2500);
    check('late answer: back in the sandbox, the renamed project opens', (await openProj()) === pF2, `open "${await openProj()}" · ${await T('#chathead')}`);
    // a failed poll: Knowledge Vault (watched) and No memory (not watched) on the next task, GET /api/live refused for 5 s from the start
    await pg.fill('#q', 'Look at the files once more.'); await arms(['kv', 'none']); gate.live = true; await pg.click('#send');
    await pg.waitForFunction(() => document.querySelector('#playbar').classList.contains('live'), null, { timeout: 180000 }).catch(() => {}); await sleep(5000); gate.live = false;
    for (let i = 0; i < 150 && (await jobsOf(pF2)).some(j => j.status === 'running'); i++) await sleep(2000);
    await sleep(14000); await pg.fill('#q', 'Next'); await sleep(400);
    const js = await pg.evaluate(n => Object.values(S.jobs).filter(j => j.cid === n).map(j => `${j.kind}:${j.order}:${j.status}`), pF2);
    check('a failed poll: the page still learns that both methods ended (no live chip, Run ▶ free)', blocked > 0 && js.length >= 2 && !js.some(x => /:running$/.test(x)) && !(await pg.$('#chains .live-chip')) && !(await pg.$eval('#send', e => e.disabled)), `blocked ${blocked} · ${js.join(' ')} · ${await T('#cstatus')}`);
    await pg.fill('#q', ''); await pg.unroute('**/api/**'); }
  if (PFX) { const left = [...mine], live = (await projects()).map(p => p.name);
    check('every project this run made and left carries the prefix', left.length > 0 && left.every(n => n.startsWith(PFX) && live.includes(n)), left.join(', ')); }

  console.log('errors:', errs.length ? errs.join('\n') : 'none'); check('no page errors', !errs.some(e => /PAGEERROR/.test(e)));
  console.log('SUMMARY', results.filter(r => r[0] === 'FAIL').length, 'failed of', results.length); await b.close(); process.exit(results.some(r => r[0] === 'FAIL') ? 1 : 0);
})().catch(e => { console.error('FAILED', e); process.exit(1); });
