const { chromium } = require('playwright');
// Compare view: columns = memory methods (Knowledge Vault left, Mem0 / MemOp / Full history middle, No memory right), each with
// an API and a Model select. ⚙ What to compare picks where the runs come from: Example chains (showcase chains where Knowledge
// Vault saves tokens against the enabled columns; the paper's recorded runs), Saved live runs (one chain × model; each column shows
// its method's newest saved live run with that model) or Sandbox projects (the Agent page's projects).
// One top bar carries the view switch, playback, "same model" and the live-run buttons; messages show in a status line under it.
// The live-run part (replayed runs) only runs against the replay server (port 8799, or ALLOW_LIVE=1): it never starts a run
// elsewhere, and on 8765 / 8766 (or RO=1) every non-GET /api request the page makes is aborted.
// PREFIX (default "ui-") starts the model names of its live runs: <PREFIX>live, so parallel suites don't collide (409).
const path = require('path'), fs = require('fs');
const BASE = process.env.BASE || 'http://127.0.0.1:8799', OUT = process.env.SHOTS || path.join(__dirname, 'shots'); fs.mkdirSync(OUT, { recursive: true });
const LIVE_OK = /:8799\b/.test(BASE) || process.env.ALLOW_LIVE === '1', RO = /:876[56]\b/.test(BASE) || process.env.RO === '1';
const PREFIX = process.env.PREFIX || 'ui-', LIVE_MODEL = `${PREFIX}live`;
// DEF = the chain the page opens on: the server's example default (api/showcase default_cid, Python-Markdown since the pre-prod round),
// read from the server, so the suite passes before and after a server restart. OLD = pyccel, the default until then: it left the example
// list but its recorded runs are still there (a deep link to it must still work).
let DEF; const SQL = 'tobymao_sqlglot_20240910_b10255eb', OLD = 'pyccel_pyccel_20240307';
const LIVE_TIP = 'Watching live runs — ▶ replays them when they end';
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failed = 0, total = 0;
const check = (name, ok, info = '') => { total++; if (!ok) failed++; console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${info && !ok ? ' — ' + info : ''}`); };
const until = async (fn, ms = 20000, step = 400) => { const t = Date.now(); while (Date.now() - t < ms) { if (await fn().catch(() => false)) return true; await sleep(step); } return false; };
const cfgRun = (p, m) => 'cfg__' + `${p}__${m}`.replace(/[^A-Za-z0-9.]+/g, '-').replace(/^-|-$/g, '');
const firstLine = t => (String(t || '').split('\n').map(l => l.trim()).find(Boolean) || '').replace(/^#+\s*/, '').replace(/\*\*|__|`/g, '');
const taskName = (summary, text) => String(summary || '').trim() || firstLine(text);
// the page's number formats (compare.html mtok / fewer / pct), to say what the footer sentence must read
const tokS = v => v >= 1e6 ? (v / 1e6).toFixed(2) + 'M' : v >= 1000 ? (v / 1000).toFixed(1) + 'K' : String(Math.round(v));
const mtok = v => v >= 1e5 ? (v / 1e6).toFixed(2) + 'M' : tokS(v), pctS = x => `${Math.round(Math.abs(x) * 100)}%`, fewerS = x => Math.round(x * 100) >= 100 ? '>99%' : pctS(x);
const NM = { kv: 'Knowledge Vault', mem0: 'Mem0', memop: 'MemOp', rawcat: 'Full history', none: 'No memory' };
(async () => {
  const b = await chromium.launch(); const errs = [];
  const getJ = async p => (await (await fetch(BASE + '/' + p)).json());
  const sc = await getJ('api/showcase'), meta = await getJ('api/meta');
  const LIVE_KINDS = Array.isArray(meta.live_kinds) && meta.live_kinds.length ? meta.live_kinds : ['kv', 'mem0', 'none'];
  const memopLive = LIVE_KINDS.includes('memop');
  console.log(`INFO live kinds ${LIVE_KINDS.join(',')}${RO ? ' · read-only' : ''}${LIVE_OK ? ' · live runs on' : ''}`);
  const scIds = new Set(sc.chains.map(c => c.cid)), margin = sc.margin ?? 0.2;
  const qualifies = (ch, cols) => cols.filter(k => k !== 'kv').every(k => ch.stats[k] && ch.stats.kv.tokens <= (1 - margin) * ch.stats[k].tokens && ch.stats.kv.resolved >= ch.stats[k].resolved);
  const expect = cols => sc.chains.filter(c => qualifies(c, cols)).map(c => c.cid).sort();
  const sess = (run, arm, cid, order) => getJ(`api/session?run=${encodeURIComponent(run)}&arm=${encodeURIComponent(arm)}&cid=${encodeURIComponent(cid)}&order=${order}`);
  const saving = (ch, cols) => Math.min(...cols.filter(k => k !== 'kv').map(k => 1 - ch.stats.kv.tokens / ch.stats[k].tokens));
  { const l3 = sc.chains.filter(c => qualifies(c, ['kv', 'mem0', 'none'])).sort((a, b) => saving(b, ['kv', 'mem0', 'none']) - saving(a, ['kv', 'mem0', 'none']));
    DEF = l3.some(c => c.cid === sc.default_cid) ? sc.default_cid : l3[0]?.cid; }       // = compare.html pickDefault()
  const DCH = sc.chains.find(c => c.cid === DEF) || {}, DN = DCH.n_tasks || 3, repoOf = cid => String((sc.chains.find(c => c.cid === cid) || meta.chains.find(c => c.cid === cid) || {}).repo || cid).split(' ')[0].split('/').pop();
  console.log(`INFO default task ${DEF} (${DCH.title} — ${DCH.repo}, ${DN} turns) · api/meta default_cid ${meta.default_cid} · ${sc.chains.length} example tasks`);
  // the recorded run of the default chain: its numbers say what the strip and the footer must show
  const rc0 = await getJ(`api/runchains?run=${sc.run}&cid=${DEF}&kinds=kv,mem0,memop,rawcat,none`), arms0 = rc0.chains[0].arms;
  const tokOf = k => (arms0[k]?.sessions || []).filter(x => !/^error/i.test(String(x.exit || '')) || /ContextWindowExceeded/i.test(String(x.exit || ''))).reduce((a, x) => a + (x.tokens || 0), 0);
  const heroPart = (a, o, name) => { const r = a / o; return Math.abs(1 - r) < 0.005 ? `about the same as ${name}` : r < 1 ? `${fewerS(1 - r)} fewer than ${name}` : r >= 2 ? `${r.toFixed(r >= 10 ? 0 : 1)}× as many as ${name}` : `${pctS(r - 1)} more than ${name}`; };
  const HERO0 = `Knowledge Vault used ${mtok(tokOf('kv'))} tokens — ${['mem0', 'none'].map(k => heroPart(tokOf('kv'), tokOf(k), NM[k])).join(', ')}.`;
  const KVRES = `${arms0.kv.sessions.filter(x => x.resolved).length}/${DN}`;

  const newCtx = async opts => { const c = await b.newContext({ viewport: { width: 1500, height: 900 }, ...opts }); if (RO) await c.route('**/api/**', r => r.request().method() === 'GET' ? r.continue() : r.abort()); return c; };
  const ctx = await newCtx();
  const watch = (pg, tag) => { pg.on('pageerror', e => errs.push(`${tag} PAGEERROR ${e.message}`)); pg.on('response', r => { if (r.status() >= 400 && !/api\/file/.test(r.url())) errs.push(`${tag} HTTP ${r.status()} ${r.url().replace(BASE, '')}`); }); };
  const pg = await ctx.newPage(); watch(pg, 'main');
  const T = async sel => ((await pg.textContent(sel).catch(() => '')) || '').replace(/\s+/g, ' ').trim();
  const IT = async sel => ((await pg.$eval(sel, e => e.innerText).catch(() => '')) || '').replace(/\s+/g, ' ').trim();
  const kinds = () => pg.$$eval('#cols .col', e => e.map(x => x.dataset.kind));
  const cidNow = () => pg.$eval('#cid', e => e.value);
  const ready = async () => { await pg.waitForSelector('#sum table tbody tr', { state: 'attached', timeout: 30000 }); await sleep(1200); };   // the table sits in the collapsed footer
  const footer = () => pg.$$eval('#sum table tbody tr', trs => Object.fromEntries(trs.map(tr => [tr.dataset.kind, +(tr.querySelector('td[data-tokens]')?.dataset.tokens || NaN)])));
  const footCost = () => pg.$$eval('#sum table tbody tr', trs => Object.fromEntries(trs.map(tr => [tr.dataset.kind, tr.querySelector('td:last-child')?.textContent.trim()])));
  const gear = async on => { const hidden = await pg.$eval('#gearpanel', e => e.hidden); if (hidden === on) await pg.click('#gearbtn'); await sleep(200); };
  const src = () => pg.$eval('#srcseg button[aria-pressed="true"]', e => e.dataset.src).catch(() => null);
  const visTurns = k => pg.$$eval(`#tr-${k} .turn`, e => e.filter(x => !x.hidden && x.offsetParent !== null).length);
  /** the chain select sits in ⚙: open the panel, pick the chain (the panel stays open) */
  const chooseChain = async cid => { await gear(true); await pg.selectOption('#cid', cid); };
  /** select every chain option and check the footer: Knowledge Vault has fewer tokens than every other visible column */
  const footerAll = async (label, cols) => {
    const opts = await pg.$$eval('#cid option', e => e.map(o => o.value)); const bad = [];
    for (const cid of opts) {
      await chooseChain(cid);
      await pg.waitForFunction(c => new URL(location.href).searchParams.get('cid') === c && document.querySelector('#sum td[data-tokens]'), cid, { timeout: 20000 }); await sleep(400);
      const f = await footer();
      for (const k of cols.filter(k => k !== 'kv')) if (!(f.kv < f[k])) bad.push(`${cid}: kv ${f.kv} vs ${k} ${f[k]}`);
      if (/more than|as many as|100% fewer/.test(await T('#hero'))) bad.push(`${cid}: hero ${await T('#hero')}`);
      const ht = await heroVsTable(); if (ht.length) bad.push(`${cid}: ${ht.join(', ')}`);
    }
    check(`${label}: on all ${opts.length} tasks the footer shows Knowledge Vault with fewer tokens than every column, its table agreeing with the sentence`, !bad.length, bad.join('; '));
    await gear(false);
  };
  /** The footer table's "tokens vs KV" never contradicts the sentence above it: each ratio fits the percentage the sentence gives for
   *  that column ("24% fewer than Mem0" ⇒ Mem0 ≈ 1.31×), and a column the sentence gives no number for (another model, no task in
   *  common) shows "–". [] when they agree, else what disagrees. */
  const heroVsTable = async () => {
    const hero = await T('#hero'), rows = await pg.$$eval('#sum table tbody tr', trs => trs.map(tr => [tr.dataset.kind, tr.children.length > 5 ? tr.children[5].textContent.trim() : null, tr.children.length > 5 ? tr.children[5].title : '']));
    const bad = [];
    for (const [k, cell, tip] of rows) {
      if (k === 'kv' || !NM[k] || cell == null) continue;
      const nm = NM[k], end = '(?![\\w])'; let lo = null, hi = null, m;
      if ((m = hero.match(new RegExp(`(>99|\\d+)% fewer than ${nm}${end}`)))) { if (m[1] === '>99') { lo = 1 / 0.015; hi = Infinity; } else { const p = +m[1] / 100; lo = 1 / (1 - Math.max(0, p - 0.005)); hi = p + 0.005 >= 1 ? Infinity : 1 / (1 - p - 0.005); } }
      else if ((m = hero.match(new RegExp(`(\\d+)% more than ${nm}${end}`)))) { const p = +m[1] / 100; lo = 1 / (1 + p + 0.005); hi = 1 / (1 + Math.max(0, p - 0.005)); }
      else if ((m = hero.match(new RegExp(`([\\d.]+)× as many as ${nm}${end}`)))) { const x = +m[1], h = /\./.test(m[1]) ? 0.05 : 0.5; lo = 1 / (x + h); hi = 1 / (x - h); }
      else if (new RegExp(`about the same as ${nm}${end}`).test(hero)) { lo = 1 / 1.005; hi = 1 / 0.995; }
      if (lo == null) { if (cell !== '–') bad.push(`${k}: "${cell}" but the sentence gives no number for ${NM[k]} (${hero})`);
        else if (new RegExp(`${nm} (and [^.]* )?ran with another model|${nm}, [^.]*ran with another model`).test(hero) && !/another model/.test(tip)) bad.push(`${k}: "–" without saying it ran another model`);
        continue; }
      const d = parseFloat(cell), dec = (String(cell).match(/\.(\d+)/) || ['', ''])[1].length, h2 = 0.5 * 10 ** -dec;
      if (!/^[\d.]+×$/.test(cell) || d + h2 < lo - 1e-9 || d - h2 > hi + 1e-9) bad.push(`${k}: "${cell}" vs the sentence (${hero})`);
      else if (new RegExp(`${nm} over the \\d+ turns? both ran`).test(hero) && !/over the \d+ turns? both ran/.test(tip)) bad.push(`${k}: ratio tooltip doesn't say it is over the turns both ran`);
    }
    return bad;
  };

  // ---------------------------------------------------------------- defaults
  await pg.goto(BASE + '/compare'); await ready();
  check('boot: columns KV | Mem0 | No memory', (await kinds()).join(',') === 'kv,mem0,none', (await kinds()).join(','));
  check(`boot: opens on the server's default example task (${DEF})`, (await cidNow()) === DEF && new URL(pg.url()).searchParams.get('cid') === DEF, await cidNow());
  check('boot: title and favicon', (await pg.title()) === 'Compare · Knowledge Vault' && !!(await pg.$('link[rel="icon"][href^="data:image/svg"]')));
  check('boot: KV header has the "ours" tag', /ours/.test(await T('#col-kv .hname')));
  for (const k of ['kv', 'mem0', 'none']) {
    const a = await pg.$eval(`#api-${k}`, e => [e.value, e.selectedOptions[0].textContent, e.options[0].title]), m = await pg.$eval(`#model-${k}`, e => [e.value, e.options.length]);
    check(`boot: ${k} API = "Recorded run" first (title: the paper's recorded run), model Qwen3.8-27B only`, a[0] === 'rec' && a[1] === 'Recorded run' && /the paper's recorded run · Qwen3\.8-27B/.test(a[2]) && m[0] === 'Qwen3.8-27B' && m[1] === 1, `${a} ${m}`);
  }
  check('boot: each column header has exactly two selects', (await pg.$$eval('#cols .col .head', hs => hs.map(h => h.querySelectorAll('select').length))).every(n => n === 2));
  await until(async () => (await pg.$$eval('#api-kv option', e => e.length)) > 1, 15000);
  check('boot: API lists the endpoints that answer, nothing marked unavailable', (await pg.$$eval('#api-kv option', e => e.length)) > 1 && !(await pg.$$eval('#cols select option', e => e.some(o => /unavailable|saved runs only/.test(o.textContent)))));
  check('boot: no option groups in any select', !(await pg.$('optgroup')));
  check('boot: task option text "title — repo", no token figure', (await pg.$eval('#cid', e => e.selectedOptions[0].textContent)) === `${DCH.title} — ${DCH.repo}` && (await pg.$$eval('#cid option', e => e.every(o => !/KV −|tokens/.test(o.textContent)))), await pg.$eval('#cid', e => e.selectedOptions[0].textContent));
  check('boot: ⚙ first section "What to compare" holds the select, not the page', !(await pg.isVisible('#cid')) && (await pg.$eval('#gearpanel section', e => e.id)) === 'chainsec' && !!(await pg.$('#gearpanel #chainsec #cid')) && /^What to compare$/i.test(await T('#chainsec h4')), await T('#chainsec h4'));
  check('boot: the source switch starts on Example tasks', (await src()) === 'example' || await pg.$eval('#srcseg', e => e.hidden), String(await src()));
  check('boot: the ⚙ line says what the columns show', (await T('#chainnote')).includes(String(DCH.blurb || '').replace(/\s+/g, ' ').slice(0, 40)) && new RegExp(`${DN} turns`).test(await T('#chainnote')) && /Each column replays the paper's recorded run/.test(await T('#chainnote')), await T('#chainnote'));
  check('boot: one top bar with view, playback, same model, Run live, nav, ⚙', !(await pg.$('.controls')) && (await pg.$$eval('header.top #viewseg, header.top #play, header.top #step, header.top #restart, header.top #end, header.top #samewrap, header.top #runlive, header.top .pnav, header.top #gearbtn', e => e.length)) === 9);
  await sleep(2500);       // the endpoint status arrives after the page
  check('boot: no Run live and no "Same model everywhere" while every column shows the recorded run', await pg.$eval('#runlive', e => e.hidden) && await pg.$eval('#samewrap', e => e.hidden));
  check('boot: the top bar is one row at 1500 px', (await pg.$eval('header.top', e => e.getBoundingClientRect().height)) <= 52, `${await pg.$eval('header.top', e => e.getBoundingClientRect().height)}px`);
  check('boot: no status line while there is no message', await pg.$eval('#stline', e => e.hidden));
  check(`boot: footer hero (token-first) "${HERO0}"`, (await T('#hero')) === HERO0, await T('#hero'));
  { const ht = await heroVsTable(); check('boot: the footer table\'s "tokens vs KV" agrees with the sentence above it', !ht.length, ht.join('; ')); }
  const ths = (await pg.$$eval('#sum thead th', e => e.map(x => x.textContent.trim()))).join('|');
  check('boot: footer table columns', ths === 'whole task|model|resolved|steps|tokens|tokens vs KV|cost', ths);
  check('boot: no per-column run buttons beside the selects', !(await pg.$('.head button')));
  check('boot: the Agent link opens this task', (await pg.$eval('#nav-agent', e => e.getAttribute('href'))) === `./?cid=${DEF}`, await pg.$eval('#nav-agent', e => e.getAttribute('href')));
  const opts0 = await pg.$$eval('#cid option', e => e.map(o => o.value));
  check(`tasks: only showcase tasks, all of them qualify (${opts0.length} of the server's ${sc.chains.length})`, opts0.every(c => scIds.has(c)) && opts0.slice().sort().join() === expect(['kv', 'mem0', 'none']).join(), `${opts0.length} options`);
  if (!scIds.has(OLD)) check(`tasks: ${OLD} (the old default) is no longer in the list`, !opts0.includes(OLD));
  const order0 = sc.chains.filter(c => qualifies(c, ['kv', 'mem0', 'none'])).sort((a, b) => saving(b, ['kv', 'mem0', 'none']) - saving(a, ['kv', 'mem0', 'none'])).map(c => c.cid);
  check('tasks: sorted by Knowledge Vault\'s saving vs the closest column, descending', opts0.join() === order0.join(), opts0.join());
  await pg.screenshot({ path: `${OUT}/cmp_default.png` });

  // ---------------------------------------------------------------- before ▶: each column shows its first task whole (C1), named by its summary
  const N1 = {}, TEXT1 = {};       // steps of task 1 per column, the issue text of task 1
  for (const k of ['kv', 'mem0', 'none']) {
    const s1 = arms0[k].sessions[0], full = await sess(sc.run, arms0[k].arm, DEF, s1.order), n = full.turns.length; N1[k] = n; TEXT1[k] = full.problem_statement;
    check(`${k}: every step and the result cards of turn 1 are on screen before ▶`, (await visTurns(k)) === n && (await pg.$$eval(`#tr-${k} .tail[data-tail="grade"]`, e => e.filter(x => !x.hidden).length)) === 1, `${await visTurns(k)} of ${n}`);
    check(`${k}: turn row "Turn 1/${DN} · ${n} steps"`, (await T(`#task-${k}`)) === `Turn 1/${DN} · ${n} steps`, await T(`#task-${k}`));
    const want = taskName(full.summary || s1.summary, full.problem_statement);
    check(`${k}: the turn row names the turn by its ${full.summary ? 'summary' : 'first line (no summary on this server)'}`, (await T(`#ttl-${k}`)) === want, `${await T(`#ttl-${k}`)} vs ${want}`);
    const n1 = await pg.$eval(`#col-${k}`, c => ([...c.children].filter(x => !x.classList.contains('transcript')).map(x => x.innerText).join(' ').match(/Turn \d/g) || []).length);
    check(`${k}: "Turn 1" shown once above the transcript`, n1 === 1, `${n1}×`);
  }
  check('before ▶: no "Press ▶ Play all" hint in any column', !/Press ▶ Play all/.test(await pg.evaluate(() => document.body.innerText)) && await pg.$$eval('#cols .transcript', ts => ts.every(t => getComputedStyle(t, '::before').content === 'none')));
  check('before ▶: the whole issue text stays behind the turn row', (await T('#tpb-kv')).includes(firstLine(TEXT1.kv).slice(0, 40)) && (await T('#tpb-kv')).length > 200, (await T('#tpb-kv')).slice(0, 80));

  // ---------------------------------------------------------------- compact chrome: one task row, collapsed numbers, collapsed footer
  check('task select: no group heading, no threshold jargon', !(await pg.$('#cid optgroup')) && !/≥/.test(await pg.evaluate(() => document.body.innerText + [...document.querySelectorAll('#cid option')].map(o => o.textContent).join(' '))));
  check('header: no inline method description, it is the name tooltip', !(await pg.$('#cols .hdesc')) && /^Knowledge Vault \(ours\) — .*writer distils/.test(await pg.$eval('#col-kv .hname', e => e.title)) && !/writer distils/.test(await T('#col-kv .head')), await pg.$eval('#col-kv .hname', e => e.title));
  const headTxt = await pg.$$eval('#cols .col .head', hs => hs.map(h => [h.querySelector('.hname').textContent.trim(), h.querySelectorAll('.hsel > :not(select)').length]));
  check('header: no "API" / "Model" text labels', headTxt.every(([t, n]) => !/\bAPI\b|\bModel\b/.test(t) && n === 0), JSON.stringify(headTxt));
  check('header: selects carry aria-labels', (await pg.$eval('#api-kv', e => e.getAttribute('aria-label'))) === 'Knowledge Vault: API' && (await pg.$eval('#model-none', e => e.getAttribute('aria-label'))) === 'No memory: model');
  check('header: name and selects on one line at 1500 px', (await pg.$$eval('#cols .col .head', hs => hs.map(h => h.getBoundingClientRect().height))).every(h => h < 42));
  check('no separate chip/sub line', !(await pg.$('#cols .col > .sub')));
  const chrome = await pg.$$eval('#cols .col', cs => cs.map(c => Math.round(c.querySelector('.transcript').getBoundingClientRect().top - c.getBoundingClientRect().top)));
  check('collapsed chrome above the transcripts ≤ 110 px and equal in every column', chrome.every(h => h <= 110 && h === chrome[0]), chrome.join(','));
  check('progress collapsed by default: one line, step rows hidden', !(await pg.isVisible('#ctr-kv .rows')) && (await pg.$eval('#ctr-kv .sline', e => e.getBoundingClientRect().height)) < 30 && new RegExp(`whole task\\s*${mtok(tokOf('kv')).replace('.', '\\.')}\\s*tokens\\s*\\$[\\d.]+\\s*${KVRES} resolved`).test(await T('#ctr-kv .sline')), await T('#ctr-kv .sline'));
  check('strip: never "0/0"', !/\b0\/0\b/.test(await pg.evaluate(() => document.querySelector('#cols').innerText)));
  check('footer collapsed by default: hero + details toggle, no table', await pg.isVisible('#hero') && !(await pg.isVisible('#sum table')) && /details ▸/.test(await T('#ftog')) && (await pg.$eval('#sum', e => e.getBoundingClientRect().height)) < 50);
  // the task row opens the whole task text
  await pg.click('#tph-mem0'); await sleep(200);
  check('turn row: click shows the whole turn text', await pg.isVisible('#tpb-mem0') && (await T('#tpb-mem0')).length > 100 && (await pg.$eval('#tph-mem0', e => e.getAttribute('aria-expanded'))) === 'true');
  await pg.click('#tph-mem0'); await sleep(200); check('turn row: second click hides it', !(await pg.isVisible('#tpb-mem0')));
  // one toggle expands the step rows of every column; the rows line up across the columns; steps not reached yet are faint
  await pg.click('#ctr-mem0 [data-ptog]'); await sleep(500);
  check('steps ▸: every column shows its step rows', (await pg.$$eval('#cols .strip .rows', e => e.map(x => getComputedStyle(x).display))).every(d => d === 'block'));
  const faint = await pg.$$eval('#cols .strip .trow[data-o="1"]', rs => rs.map(r => { const d = [...r.querySelectorAll('.d2')]; return [r.closest('.col').dataset.kind, d.length, d.filter(x => { const o = +getComputedStyle(x).opacity; return o > 0.2 && o < 0.6; }).length]; }));
  check('steps ▸ before ▶: every task-1 dot is shown, faintly', faint.every(([, n, f]) => n > 0 && n === f), JSON.stringify(faint));
  const rowH = await pg.$$eval('#cols .strip .trow[data-o]', rs => { const m = {}; for (const r of rs) (m[r.dataset.o] = m[r.dataset.o] || []).push(Math.round(r.getBoundingClientRect().height)); return m; });
  check('steps ▸: turn rows have the same height in every column', Object.values(rowH).every(hs => hs.length === 3 && new Set(hs).size === 1), JSON.stringify(rowH));
  const tops = await pg.$$eval('#cols .strip .trow[data-o="3"]', rs => rs.map(r => Math.round(r.getBoundingClientRect().top)));
  check('steps ▸: T3 sits at the same height in every column', new Set(tops).size === 1, tops.join(','));
  check('steps ▸: a turn label names the turn', /^Turn 2 — .+: click to jump/.test(await pg.$eval('#ctr-kv .trow[data-o="2"] .tl', e => e.title)), await pg.$eval('#ctr-kv .trow[data-o="2"] .tl', e => e.title));
  await pg.click('#ftog'); await sleep(300);
  check('details ▸: footer shows the table and the step-dot legend', await pg.isVisible('#sum table') && await pg.isVisible('#sum .legend') && /command ok/.test(await T('#sum .legend')) && /details ▾/.test(await T('#ftog')));
  await pg.screenshot({ path: `${OUT}/cmp_expanded.png` });
  await pg.reload(); await ready();
  check('expanded steps + footer are remembered', (await pg.isVisible('#ctr-kv .rows')) && (await pg.isVisible('#sum table')));

  // ---------------------------------------------------------------- playback (steps expanded, so every dot can be clicked)
  await pg.click('#play'); await sleep(2500);
  check('play all: the replay starts from step 1', new RegExp(`step [0-3]/${N1.kv}$`).test(await T('#task-kv')) && (await visTurns('kv')) <= 3, `${await T('#task-kv')} · ${await visTurns('kv')} visible`);
  check('play all: button toggles', /Pause/.test(await T('#play')));
  await pg.click('#play'); await sleep(300);
  check('pause: no "thinking" placeholder left blinking', !(await pg.$('#cols .thinking')));
  const s0 = await T('#task-mem0'); await pg.click('#step'); await sleep(400); check('step all advances', (await T('#task-mem0')) !== s0, `${s0} -> ${await T('#task-mem0')}`);
  await pg.click('#end'); await sleep(800); check('show all: turn result in the turn row', /resolved/.test(await T('#res-kv')) && /resolved/.test(await T('#pin-kv .tp-head')), await T('#res-kv'));
  await pg.click('#restart'); await sleep(1500);
  check('↺: every column rewinds to the start of turn 1 (step 0, no step shown)', (await T('#task-kv')) === `Turn 1/${DN} · step 0/${N1.kv}` && (await visTurns('kv')) === 0 && new RegExp(`^Turn 1/${DN} · step 0/`).test(await T('#task-mem0')), `${await T('#task-kv')} · ${await visTurns('kv')} | ${await T('#task-mem0')}`);
  check('↺: one status line says so, ▶ replays it', /Back to the start of turn 1/.test(await T('#livest')) && /Play all/.test(await T('#play')), await T('#livest'));
  await pg.click('#play'); await sleep(2500); check('↺ then ▶: the replay starts again from step 1', new RegExp(`^Turn 1/${DN} · step [1-9]\\d*/${N1.kv}$`).test(await T('#task-kv')) && (await visTurns('kv')) >= 1, `${await T('#task-kv')} · ${await visTurns('kv')}`);
  await pg.click('#play'); await sleep(300);
  const dot = (await pg.$$('#ctr-kv .rows .trow[data-o="1"] .d2'))[4]; if (dot) { await dot.click(); await sleep(500); check('a dot jumps there: steps 1–5 shown, no "thinking"', new RegExp(`step 5/${N1.kv}$`).test(await T('#task-kv')) && (await visTurns('kv')) === 5 && !(await pg.$('#tr-kv .thinking')), `${await T('#task-kv')} · ${await visTurns('kv')}`); }
  const tl = await pg.$('#ctr-kv .trow[data-o="2"] .tl'); if (tl) { await tl.click(); await sleep(1500); check('jump to turn 2 via its label', /Turn 2\/3/.test(await T('#task-kv')), await T('#task-kv')); }
  for (let i = 0; i < 6; i++) { await pg.click('#end'); await sleep(700); }
  check('⇥ to the end of the task', new RegExp(`^Turn ${DN}/${DN} `).test(await T('#task-kv')), await T('#task-kv'));
  await pg.click('#play'); await sleep(1800);
  check('▶ at the end of the task replays turn 1', new RegExp(`^Turn 1/${DN} · step [0-3]/`).test(await T('#task-kv')) && /Pause/.test(await T('#play')), await T('#task-kv'));
  await pg.click('#play'); await sleep(300);
  await pg.click('#ctr-kv [data-ptog]'); await pg.click('#ftog'); await sleep(400);
  check('collapsing again: the current turn\'s dots inline, table hidden', !(await pg.isVisible('#ctr-kv .rows')) && (await pg.$$('#ctr-kv .sline .idots .d2')).length > 0 && !(await pg.isVisible('#sum table')));

  // ---------------------------------------------------------------- ⚙ Columns: the chain list follows the columns
  // A chain that no longer qualifies against the ticked columns is left for the top one, with one line saying why. Which column moves
  // the page off the default chain depends on the data (since the pre-prod round: MemOp keeps it, Full history moves off it).
  const rx = t => String(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  /** tick column k in ⚙ (cols = the columns after it) and check where the page went */
  const tick = async (k, cols, label) => {
    const before = await cidNow(), ch = sc.chains.find(c => c.cid === before), stays = !ch || qualifies(ch, cols);
    await gear(true); await pg.check(`#colchecks input[data-col="${k}"]`); await sleep(2500); await ready();
    check(`${label}: column order ${cols.join(' | ')}`, (await kinds()).join(',') === cols.join(','), (await kinds()).join(','));
    const order = sc.chains.filter(c => qualifies(c, cols)).sort((a, b) => saving(b, cols) - saving(a, cols)).map(c => c.cid), opts = await pg.$$eval('#cid option', e => e.map(o => o.value));
    check(`${label}: the task list = the ${order.length} tasks that qualify against these columns, by the saving vs the closest column`, opts.join() === order.join() && opts.length <= opts0.length, opts.join());
    if (stays) check(`${label}: stays on ${before} (Knowledge Vault still saves ≥ ${Math.round(margin * 100)}% there), no message`, (await cidNow()) === before && await pg.$eval('#stline', e => e.hidden), `${await cidNow()} | ${await T('#livest')}`);
    else {
      check(`${label}: moved off ${before} to the top task`, (await cidNow()) === order[0], await cidNow());
      check(`${label}: status line says why (visible on the page)`, new RegExp(`^${rx(repoOf(before))}: .*${NM[k]}.* — switched to ${rx(repoOf(order[0]))} `).test(await T('#livest')) && await pg.isVisible('#livest'), await T('#livest'));
      const now = sc.chains.find(c => c.cid === order[0]);
      check(`${label}: ⚙ line follows the new task`, !!now && (await T('#chainnote')).includes(now.blurb.slice(0, 30)), await T('#chainnote'));
      await pg.keyboard.press('Escape'); await pg.click('#stx'); await sleep(200);
      check('status line: × dismisses it', await pg.$eval('#stline', e => e.hidden));
    }
    await gear(false); return stays;
  };
  await gear(true);
  check('gear: Columns checkboxes Mem0, MemOp, Full history, No memory', (await pg.$$eval('#colchecks input', e => e.map(i => i.dataset.col))).join() === 'mem0,memop,rawcat,none');
  check('gear: one-line descriptions', /one guidance document/.test(await T('#colchecks')) && /verbatim/.test(await T('#colchecks')));
  check(`gear: "recorded only" on ${memopLive ? 'Full history only' : 'MemOp and Full history'}`, (await pg.$$eval('#colchecks label', ls => ls.filter(l => /recorded only/.test(l.textContent)).map(l => l.querySelector('input').dataset.col))).join() === (memopLive ? 'rawcat' : 'memop,rawcat'));
  await tick('memop', ['kv', 'mem0', 'memop', 'none'], 'MemOp on');
  check('MemOp on: URL has cols', /cols=kv,mem0,memop,none/.test(pg.url()), pg.url());
  const moved = !(await tick('rawcat', ['kv', 'mem0', 'memop', 'rawcat', 'none'], 'all columns'));
  if (!moved) console.log('INFO no column moved the page off the default task on this server (the data decide)');
  for (const k of ['memop', 'rawcat']) {
    const a = await pg.$$eval(`#api-${k} option`, e => e.map(o => [o.value, o.disabled]));
    if (k === 'memop' && memopLive) check('memop (runs live): its API select offers the endpoints', a[0][0] === 'rec' && a.length > 1 && a.slice(1).every(x => !x[1]), JSON.stringify(a));
    else check(`${k}: only "Recorded run" enabled`, a[0][0] === 'rec' && !a[0][1] && a.slice(1).every(x => x[1]), JSON.stringify(a));
    if (!(k === 'memop' && memopLive)) check(`${k}: model Qwen3.8-27B only`, (await pg.$$eval(`#model-${k} option`, e => e.map(o => o.value))).join() === 'Qwen3.8-27B');
  }
  await pg.screenshot({ path: `${OUT}/cmp_all_columns.png` });
  await footerAll('all columns', ['kv', 'mem0', 'memop', 'rawcat', 'none']);
  // the last ticked comparison method can't be unticked
  await pg.goto(BASE + '/compare?cols=kv,mem0'); await ready(); await gear(true);
  check('Columns: the last ticked method is locked', await pg.$eval('#colchecks input[data-col="mem0"]', e => e.checked && e.disabled) && /Keep at least one method/.test(await pg.$eval('#colchecks input[data-col="mem0"]', e => e.closest('label').title)));
  await gear(false);
  await pg.goto(BASE + '/compare?cols=kv,mem0,memop,rawcat,none'); await ready();
  // the choice is remembered
  await pg.goto(BASE + '/compare'); await ready();
  check('columns remembered (localStorage)', (await kinds()).join(',') === 'kv,mem0,memop,rawcat,none', (await kinds()).join(','));
  await pg.goto(BASE + '/compare?cols=kv,mem0,none'); await ready();
  check('?cols= overrides the saved choice', (await kinds()).join(',') === 'kv,mem0,none' && (await cidNow()) === DEF, `${await kinds()} ${await cidNow()}`);
  await footerAll('default columns', ['kv', 'mem0', 'none']);

  // ---------------------------------------------------------------- API / Model + same model
  await pg.goto(BASE + `/compare?cols=kv,mem0,memop,none&cid=${SQL}`); await ready(); await sleep(2500);
  const ep = await pg.$$eval('#api-kv option', e => e.filter(o => o.value !== 'rec' && !o.disabled).map(o => o.value));
  if (!ep.length) check('an endpoint is available', false, 'no enabled endpoint in the API select');
  else {
    const e0 = ep[0];
    await pg.selectOption('#api-kv', e0); await sleep(2500);
    const m0 = await pg.$eval('#model-kv', e => e.value);
    check('endpoint: Model lists the endpoint models, in one flat list', (await pg.$$eval('#model-kv option', e => e.length)) >= 1 && m0 && m0 !== 'Qwen3.8-27B' && !(await pg.$('#model-kv optgroup')), m0);
    check('endpoint: "Same model everywhere" shows now (on)', !(await pg.$eval('#samewrap', e => e.hidden)) && await pg.$eval('#same', e => e.checked) && /Same model in all columns/.test(await pg.$eval('#samewrap', e => e.title)));
    check('same model: Mem0 and No memory follow', (await pg.$eval('#api-mem0', e => e.value)) === e0 && (await pg.$eval('#api-none', e => e.value)) === e0 && (await pg.$eval('#model-none', e => e.value)) === m0);
    if (memopLive) check('same model: MemOp (runs live) follows too, no "recorded run" chip', (await pg.$eval('#api-memop', e => e.value)) === e0 && (await pg.$eval('#rec-memop', e => e.hidden)));
    else check('same model: MemOp keeps the recorded run + chip', (await pg.$eval('#api-memop', e => e.value)) === 'rec' && !(await pg.$eval('#rec-memop', e => e.hidden)));
    check('same model: URL carries api/model', pg.url().includes(`api=${encodeURIComponent(e0)}`), pg.url());
    check('endpoint: the ⚙ line says the columns show the picked model', /model picked in its header/.test(await T('#chainnote')), await T('#chainnote'));
    await pg.uncheck('#same'); await sleep(500);
    await pg.selectOption('#api-mem0', 'rec'); await sleep(2000);
    check('same off: only that column changes', (await pg.$eval('#api-mem0', e => e.value)) === 'rec' && (await pg.$eval('#api-kv', e => e.value)) === e0);
    await pg.check('#same'); await sleep(2500);
    check('same on again: every live column follows KV', (await pg.$eval('#api-mem0', e => e.value)) === e0);
    await pg.selectOption('#api-kv', 'rec'); await sleep(2500);
    check('back to recorded: all columns recorded, chip and Same model hidden', (await pg.$eval('#api-none', e => e.value)) === 'rec' && (await pg.$eval('#rec-memop', e => e.hidden)) && (await pg.$eval('#samewrap', e => e.hidden)));
  }

  // ---------------------------------------------------------------- memory view with a MemOp column
  await pg.click('#viewseg button[data-view="memory"]'); await pg.waitForSelector('#memview .mc-grid', { timeout: 30000 }); await sleep(800);
  const heads = await pg.$$eval('#memview .mc-head', e => e.map(x => x.dataset.kind));
  check('memory view: one column per memory method incl. MemOp', heads.join(',') === 'kv,mem0,memop,none', heads.join(','));
  check('memory view: MemOp shows its guidance document (markdown)', !!(await pg.$('#memview .mc-cell[data-kind="memop"] .note.md .md-h')), (await T('#memview .mc-cell[data-kind="memop"]')).slice(0, 120));
  check('memory view: play controls hidden', await pg.$eval('#play', e => e.hidden));
  check('memory view: URL has view=memory', /view=memory/.test(pg.url()));
  { const ch = await getJ(`api/chain/${SQL}?run=__live__`), t1 = ch.chain.tasks.find(t => t.order === 1);
    check(`memory view: rows "Turn 1 — <the turn's ${t1.summary ? 'summary' : 'first line'}>"`, (await T('#memview .mc-row[data-order="1"] b')) === `Turn 1 — ${taskName(t1.summary, t1.title)}`, await T('#memview .mc-row[data-order="1"] b')); }
  await pg.screenshot({ path: `${OUT}/cmp_memory_memop.png` });
  await pg.click('#memview [data-mcmode="store"]'); await sleep(400);
  check('memory view: saved store mode', (await pg.$$('#memview .mc-item')).length > 0);
  await pg.click('#memview .mc-row[data-order="2"]'); await sleep(2000);
  check('memory view: a turn row returns to the transcripts at that turn, shown whole', !(await pg.$eval('#cols', e => e.hidden)) && /^Turn 2\/3 · \d+ steps$/.test(await T('#task-kv')) && !(await pg.$('#cols .thinking')), await T('#task-kv'));
  await pg.goto(BASE + '/compare?cols=kv,rawcat&view=memory'); await pg.waitForSelector('#memview .mc-grid', { timeout: 30000 }); await sleep(800);
  check('?view=memory&cols=kv,rawcat: Full history column with raw text', (await pg.$$eval('#memview .mc-head', e => e.map(x => x.dataset.kind))).join() === 'kv,rawcat' && !!(await pg.$('#memview .mc-cell[data-kind="rawcat"] .note.rawtext')));

  // ---------------------------------------------------------------- deep links
  await pg.goto(BASE + '/compare?cols=kv,mem0,none&cid=HypothesisWorks_hypothesis_20221202'); await ready();
  check('?cid= outside the showcase: extra option', /not a showcase task/.test(await pg.$eval('#cid', e => e.selectedOptions[0].textContent)), await pg.$eval('#cid', e => e.selectedOptions[0].textContent));
  check('?cid= outside the showcase: the Agent link opens it', (await pg.$eval('#nav-agent', e => e.getAttribute('href'))) === './?cid=HypothesisWorks_hypothesis_20221202');
  // the old default left the example list: a link to it still opens it, with its recorded runs
  if (scIds.has(OLD)) console.log(`SKIP ${OLD} is still an example task on this server`);
  else {
    await pg.goto(BASE + `/compare?cols=kv,mem0,none&cid=${OLD}`); await ready();
    check(`?cid=${OLD} (left the example list): it opens, as "(not a showcase task)", and the ⚙ line says so`, (await cidNow()) === OLD && /\(not a showcase task\)$/.test(await pg.$eval('#cid', e => e.selectedOptions[0].textContent)) && /not one of the example tasks/.test(await T('#chainnote')), `${await cidNow()} | ${await T('#chainnote')}`);
    const rows = await pg.$$eval('#cols .tp-n', e => e.map(x => x.textContent.trim()));
    check(`?cid=${OLD}: every column shows its recorded turn 1, the footer compares them`, rows.length === 3 && rows.every(t => /^Turn 1\/3 · \d+ steps$/.test(t)) && /^Knowledge Vault used [\d.]+[MK]? tokens — \d+% fewer than Mem0/.test(await T('#hero')), `${rows.join(' | ')} | ${await T('#hero')}`);
    await gear(true); await pg.check('#colchecks input[data-col="memop"]'); await sleep(2500); await ready(); await gear(false);
    check(`?cid=${OLD}: ticking a column stays on it (it is no example task to switch away from)`, (await cidNow()) === OLD && (await kinds()).join() === 'kv,mem0,memop,none', `${await cidNow()} ${await kinds()}`);
  }

  // ---------------------------------------------------------------- pre-prod fixes (read-only)
  // player.js md(): inline code is kept as it is — `__all__` stays __all__, not a bold "all"
  { const out = await pg.evaluate(async () => (await import(new URL('static/player.js', location.href).href)).md('Set `__all__` in `pkg/__init__.py`; **bold**, __strong__, snake__case__name, `[x](https://example.org)`'));
    check('md(): inline code verbatim (`__all__`, a link inside code), bold still works, no bold inside a word', out.includes('<code>__all__</code>') && out.includes('<code>pkg/__init__.py</code>') && out.includes('<code>[x](https://example.org)</code>') && out.includes('<b>bold</b>') && out.includes('<b>strong</b>') && out.includes('snake__case__name') && !/<b>all<\/b>|<b>init<\/b>|<b>case<\/b>/.test(out), out); }
  // paused while memory is written, then ⇥ or ▶: the task is counted once, not twice
  for (const then of ['end', 'play']) {
    await pg.goto(BASE + `/compare?cols=kv,mem0,none&cid=${DEF}`); await ready();
    await pg.evaluate(n => document.querySelector(`#ctr-kv [data-jump="kv:1:${n}"]`).click(), N1.kv); await sleep(300);
    await pg.click('#play');
    const seen = await until(() => pg.evaluate(() => [...document.querySelectorAll('#tr-kv .tail.phase')].some(e => /writing memory/.test(e.textContent))), 15000, 50);
    await pg.click('#play'); await sleep(300);                                     // Pause all, while the memory is written
    if (then === 'end') { await pg.click('#end'); await sleep(700); }
    else { await pg.click('#play'); await until(async () => /^Turn 2\//.test(await T('#task-kv')), 20000, 200); await pg.click('#play'); await sleep(300); }
    const r1 = arms0.kv.sessions[0].resolved ? 1 : 0, st = await T('#ctr-kv .sline .stat');
    check(`paused while "writing memory for the next turn", then ${then === 'end' ? '⇥' : '▶'}: turn 1 counts once (${r1}/1 resolved)`, seen && new RegExp(`(?<!\\d)${r1}/1 resolved`).test(st), `${seen ? '' : 'writing phase not seen · '}${st}`);
  }
  // ↺ again and again: one click listener on a transcript, not one more per player
  { await pg.goto(BASE + `/compare?cols=kv,mem0,none&cid=${DEF}`); await ready();
    const cdp = await ctx.newCDPSession(pg);
    const count = async () => { const { result } = await cdp.send('Runtime.evaluate', { expression: 'document.querySelector("#tr-kv")' }); const { listeners } = await cdp.send('DOMDebugger.getEventListeners', { objectId: result.objectId }); return listeners.filter(l => l.type === 'click').length; };
    const c0 = await count(); for (let i = 0; i < 6; i++) { await pg.click('#restart'); await sleep(400); }
    const c1 = await count(); await cdp.detach();
    check(`↺ six times: the transcript still has ${c0} click listener (each new player removes the old one's)`, c0 === c1 && c0 >= 1 && c0 <= 2, `${c0} → ${c1}`); }
  // a pick superseded by a newer one: its late answer leaves the comparison playing; a column load never mixes its run with the new chain
  { const [A, C] = opts0.filter(c => c !== DEF), lp = await ctx.newPage(); watch(lp, 'late');
    let delay = null; await lp.route('**/api/**', async r => { const ms = delay && delay(r.request().url()); if (ms) await sleep(ms); return r.fallback(); });
    const LT = async sel => ((await lp.textContent(sel).catch(() => '')) || '').replace(/\s+/g, ' ').trim(), has = (u, c) => u.includes(`cid=${encodeURIComponent(c)}`);
    const open2 = async () => { await lp.goto(BASE + `/compare?cols=kv,mem0,none&cid=${DEF}`); await lp.waitForSelector('#sum table tbody tr', { state: 'attached', timeout: 30000 }); await sleep(1500); };
    const pick2 = async () => { await lp.click('#gearbtn'); await lp.selectOption('#cid', A); await sleep(300); await lp.selectOption('#cid', C); await lp.keyboard.press('Escape'); };
    await open2(); delay = u => /api\/runchains/.test(u) && has(u, A) ? 6000 : 0;
    await pick2(); await sleep(2500); await lp.click('#play'); await sleep(6000);
    check('pick A (answers 6 s late), then C and ▶: A\'s late answer leaves C playing', /Pause/.test(await LT('#play')) && /· step [1-9]\d*\//.test(await LT('#task-kv')) && (await lp.$eval('#cid', e => e.value)) === C && new URL(lp.url()).searchParams.get('cid') === C, `${await LT('#play')} | ${await LT('#task-kv')} | ${lp.url()}`);
    await lp.click('#play'); delay = null;
    await open2(); let cRc = 0; const early = [];
    lp.on('response', r => { if (/api\/runchains/.test(r.url()) && has(r.url(), C)) cRc = Date.now(); });
    lp.on('request', r => { if (/api\/session\?/.test(r.url()) && has(r.url(), C) && !cRc) early.push(r.url().replace(BASE, '')); });
    delay = u => /api\/session\?/.test(u) && has(u, A) ? 1500 : /api\/runchains/.test(u) && has(u, C) ? 4000 : 0;
    await pick2(); await sleep(7000); delay = null;
    const rows = await lp.$$eval('#cols .tp-n', e => e.map(x => x.textContent.trim()));
    check('pick A (its sessions late), then C (its runs late): no column loads A\'s run under C, each shows C\'s turn 1', !early.length && rows.every(t => /^Turn 1\/\d+ · \d+ steps?$/.test(t)), `${early.join(' ')} | ${rows.join(' | ')}`);
    await lp.close(); }
  // api/runchains failing is not "no run yet": the columns say they couldn't load, offer Try again (no ▶ Run live), and it works
  { const fp2 = await ctx.newPage(); watch(fp2, 'rcfail'); let fail = true;
    await fp2.route('**/api/runchains**', r => fail && r.request().url().includes(`cid=${encodeURIComponent(SQL)}`) ? r.abort() : r.fallback());
    await fp2.goto(BASE + `/compare?cols=kv,mem0,none&cid=${SQL}`); await fp2.waitForSelector('#sum table tbody tr', { state: 'attached', timeout: 30000 }); await sleep(3000);
    const FT = async sel => ((await fp2.textContent(sel).catch(() => '')) || '').replace(/\s+/g, ' ').trim();
    const rows = await fp2.$$eval('#cols .col', cs => cs.map(c => [c.querySelector('.tp-n').textContent.trim(), (c.querySelector('.colempty .msg1') || {}).textContent, !!c.querySelector('[data-retry]'), !!c.querySelector('[data-runcol]')]));
    check('api/runchains failing: each column says "Couldn\'t load this run — try again." with Try again — not "no run yet", no ▶ Run live', rows.length === 3 && rows.every(([t, m, r, rl]) => t === "couldn't load" && m === "Couldn't load this run — try again." && r && !rl) && await fp2.$eval('#runlive', e => e.hidden) && /^Couldn't load Knowledge Vault's run/.test(await FT('#hero')), `${JSON.stringify(rows)} | ${await FT('#hero')}`);
    fail = false; await fp2.click('#tr-kv [data-retry]'); await sleep(3500);
    check('… Try again loads the columns once the server answers', (await fp2.$$eval('#cols .tp-n', e => e.map(x => x.textContent.trim()))).every(t => /^Turn 1\/\d+ · \d+ steps?$/.test(t)) && /^Knowledge Vault used /.test(await FT('#hero')), `${await fp2.$$eval('#cols .tp-n', e => e.map(x => x.textContent.trim()).join(' | '))} | ${await FT('#hero')}`);
    await fp2.close(); }

  // ---------------------------------------------------------------- ⚙ What to compare › Saved live runs
  const provs = await getJ('api/providers').catch(() => []);
  const lr = await getJ('api/liveruns').catch(() => []);
  const cfgRuns = (meta.runs || []).filter(r => r.startsWith('cfg__'));
  const liveKeys = new Set(lr.filter(e => !e.run.startsWith('cfg__') && e.sessions.length).map(e => `${e.cid}|${e.model || ''}`));
  let cfgHas = 0; for (const r of cfgRuns) { const rc = await getJ(`api/runchains?run=${encodeURIComponent(r)}`).catch(() => ({ chains: [] })); cfgHas += (rc.chains || []).filter(c => Object.values(c.arms).some(a => a.sessions.length)).length; }
  const projs = await getJ('api/custom/projects').catch(() => []), projRan = projs.filter(p => Object.values(p.arms || {}).some(a => (a.orders || []).length));
  await pg.goto(BASE + '/compare?cols=kv,mem0,none'); await ready(); await gear(true);
  const segShown = await pg.$$eval('#srcseg button', e => e.filter(x => !x.hidden).map(x => x.dataset.src));
  const wantSeg = ['example', ...(liveKeys.size || cfgHas ? ['saved'] : []), ...(projRan.length ? ['project'] : [])];
  check(`What to compare: the switch shows ${wantSeg.join(' | ')}${wantSeg.length < 2 ? ' (hidden: one source)' : ''}`, wantSeg.length < 2 ? await pg.$eval('#srcseg', e => e.hidden) : segShown.join() === wantSeg.join(), segShown.join());
  await pg.screenshot({ path: `${OUT}/cmp_gear_example.png` });
  if (!segShown.includes('saved')) console.log('SKIP saved live runs (none on this server)');
  else {
    await pg.click('#srcseg button[data-src="saved"]'); await sleep(3000); await ready();
    const so = await pg.$$eval('#cid option', e => e.map(o => [o.value, o.textContent]));
    check('saved: one option per task × model, "<task> · <model> · <methods> · <date>"', so.length >= liveKeys.size && so.every(([, t]) => /^[^·]+ · [^·]+ · (Knowledge Vault|Mem0|MemOp|No memory)( \+ (Mem0|MemOp|No memory))*( · \w{3} \d{1,2}(, \d{4})?)?$/.test(t)), JSON.stringify(so.slice(0, 3)));
    check('saved: every saved live_* run of the Agent page is listed', [...liveKeys].every(k => so.some(([v]) => v === k)), [...liveKeys].filter(k => !so.some(([v]) => v === k)).join());
    check('saved: no option groups', !(await pg.$('#cid optgroup')));
    check('saved: the switch, the URL and the select agree', (await src()) === 'saved' && new URL(pg.url()).searchParams.has('saved') && (await pg.$eval('#cid', e => e.getAttribute('aria-label'))) === 'Saved live run', pg.url());
    // the first pick has Knowledge Vault and another method (something to compare)
    const [val, lab] = await pg.$eval('#cid', e => [e.value, e.selectedOptions[0].textContent]);
    const [vcid, vmodel] = [val.slice(0, val.indexOf('|')), val.slice(val.indexOf('|') + 1)];
    check('saved: the first pick has Knowledge Vault and another method', /Knowledge Vault \+/.test(lab) || !so.some(([, t]) => /Knowledge Vault \+/.test(t)), lab);
    check('saved: the ⚙ line says what the columns show', /Each column shows its method's live run with/.test(await T('#chainnote')) && (!vmodel || (await T('#chainnote')).includes(vmodel)), await T('#chainnote'));
    const hs = await pg.$$eval('#cols .col', cs => cs.map(c => [c.dataset.kind, c.querySelector('.hsel').innerText.trim(), c.querySelectorAll('.hsel select').length, c.querySelector('.tp-n').textContent.trim()]));
    check('saved: headers name the model and the run instead of API / Model selects', hs.every(([, t, n]) => n === 0 && /· live run|· other run|· running live|· no run yet|^no live runs$/.test(t)), JSON.stringify(hs));
    const ran = lab.split(' · ')[2].split(' + ');
    check('saved: each method that ran shows its saved run, the others say they have none', hs.every(([k, t, , task]) => ran.includes({ kv: 'Knowledge Vault', mem0: 'Mem0', memop: 'MemOp', none: 'No memory', rawcat: 'Full history' }[k]) ? /^Turn \d+\/\d+ · \d+ steps?$/.test(task) && /live run|other run/.test(t) : /no run yet|no live runs/.test(t) && task === 'no run yet'), JSON.stringify(hs));
    // the transcript says "agent · step N" as on the recorded columns; the model that served a step and its cost only with Show details
    { const who = async () => pg.$eval('#cols .col .transcript .turn .who', e => [e.innerText.trim(), e.querySelector('.tm')?.textContent || '']).catch(() => null);
      const w0 = await who();
      if (!w0) console.log('SKIP saved: who-line (no step on screen)');
      else {
        check('saved: a step\'s who-line starts "agent · step N", no model id and no $ without Show details', /^agent · step \d+ · /.test(w0[0]) && !/\$/.test(w0[0]) && (!w0[1] || !w0[0].includes(w0[1])), JSON.stringify(w0));
        await pg.check('#details'); await sleep(200); const w1 = await who(); await pg.uncheck('#details'); await sleep(200);
        check('saved: … with Show details the model shows (when the step recorded one)', !w1[1] || w1[0].includes(w1[1]), JSON.stringify(w1));
      } }
    check('saved: no "Same model everywhere" and no recorded-run chip', await pg.$eval('#samewrap', e => e.hidden) && (await pg.$$eval('#cols .recchip', e => e.every(x => x.hidden))));
    check('saved: the URL keeps cid and model', new URL(pg.url()).searchParams.get('cid') === vcid && new URL(pg.url()).searchParams.get('saved') === vmodel, pg.url());
    await pg.screenshot({ path: `${OUT}/cmp_gear_saved.png` });
    await gear(false); await pg.screenshot({ path: `${OUT}/cmp_saved_columns.png` });
    const rowsBefore = await pg.$$eval('#cols .tp-n', e => e.map(x => x.textContent.trim()).join('|'));
    await pg.reload(); await ready(); await sleep(1000);
    check('saved: reload reopens the same saved runs', (await src()) === 'saved' && (await cidNow()) === val && (await pg.$$eval('#cols .tp-n', e => e.map(x => x.textContent.trim()).join('|'))) === rowsBefore, `${await src()} ${await cidNow()}`);
    // ↺ then ⇥ through the whole run: each strip's cost = the footer's, with list prices per model and with one price row for all
    for (const pricing of ['model', 'global']) {
      if (pricing === 'global') { await gear(true); await pg.selectOption('#pricing', 'global'); await sleep(2500); await ready(); await gear(false); }
      const n = Math.max(...(await pg.$$eval('#cols .tp-n', e => e.map(x => +((x.textContent.match(/^Turn \d+\/(\d+)/) || [])[1] || 0)))));
      await pg.click('#restart'); await sleep(1500); for (let i = 0; i <= n; i++) { await pg.click('#end'); await sleep(800); }
      const fc = await footCost(), st = Object.fromEntries(await pg.$$eval('#cols .col', cs => cs.map(c => [c.dataset.kind, (c.querySelector('.strip .s-usd') || {}).textContent || ''])));
      const ks = Object.keys(st).filter(k => st[k]);
      check(`saved, ${pricing === 'model' ? 'list prices per model' : 'one price row for all'}: after ⇥ through the run each strip's cost = the footer's`, ks.length > 0 && ks.every(k => st[k].trim() === fc[k]), `${JSON.stringify(st)} vs ${JSON.stringify(fc)}`);
    }
    await gear(true); await pg.selectOption('#pricing', 'model'); await sleep(2500); await ready(); await gear(false);
    // a pick where a method has no run: that column says so in one line
    const lack = so.find(([, t]) => !/No memory/.test(t.split(' · ')[2] || ''));
    if (lack) { await gear(true); await pg.selectOption('#cid', lack[0]); await sleep(2500); await ready();
      check('saved: a method without a run says so in one line', /^No run without memory (with \S+|of this model) on this task yet\.$/.test(await T('#tr-none .msg1')) && /No run yet: .*No memory/.test(await T('#chainnote')), `${await T('#tr-none .msg1')} | ${await T('#chainnote')}`);
      if (LIVE_OK) check('saved: … with ▶ Run live when its model can run here', !!(await pg.$('#tr-none [data-runcol="none"]')) || /can't|isn't|didn't/.test(await T('#tr-none .why')), await T('#tr-none'));
      await gear(false); }
    // back to Example chains: the recorded runs again
    await gear(true); await pg.click('#srcseg button[data-src="example"]'); await sleep(2500); await ready();
    check('saved → Example tasks: recorded runs again, URL without saved=', (await src()) === 'example' && !new URL(pg.url()).searchParams.has('saved') && (await pg.$$eval('#cols select[id^="api-"]', e => e.every(s => s.value === 'rec'))) && (await T('#hero')) === HERO0, `${pg.url()} ${await T('#hero')}`);
    await gear(false);
  }
  // ---------------------------------------------------------------- ⚙ What to compare › Sandbox projects (and ?cid=<project> from the Agent page)
  if (!segShown.includes('project')) console.log('SKIP sandbox projects (no sandbox project with runs on this server)');
  else {
    await gear(true); await pg.click('#srcseg button[data-src="project"]'); await sleep(3500);
    const po = await pg.$$eval('#cid option', e => e.map(o => [o.value, o.textContent]));
    check('projects: "<project> · <label> · N turns", newest first', po.every(([v, t]) => { const p = projs.find(x => x.name === v); return p && t === [p.name, p.label, `${p.tasks.length} turn${p.tasks.length === 1 ? '' : 's'}`].filter(Boolean).join(' · '); }) && po.map(([v]) => v).join() === projRan.slice().sort((a, b) => (b.created > a.created) - (b.created < a.created)).map(p => p.name).join(), JSON.stringify(po));
    const pname = await cidNow();
    check('projects: static "as run in your sandbox" instead of selects', /as run in your sandbox/.test(await T('#hsel-kv')) && !(await pg.$('#cols select')));
    check('projects: the URL is ?cid=<project>, the Agent link opens it', new URL(pg.url()).searchParams.get('cid') === pname && (await pg.$eval('#nav-agent', e => e.getAttribute('href'))) === `./?cid=${encodeURIComponent(pname)}`, pg.url());
    check('projects: the ⚙ line says what the columns show', new RegExp(`Each column shows the turns you ran in ${pname.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`).test(await T('#chainnote')), await T('#chainnote'));
    await pg.screenshot({ path: `${OUT}/cmp_gear_project.png` });
    await gear(false);
    await pg.goto(BASE + '/compare?cid=' + encodeURIComponent(pname)); await sleep(4000);
    check('?cid=<project>: opens in Sandbox projects', (await src()) === 'project' && (await cidNow()) === pname && /as run in your sandbox/.test(await T('#hsel-kv')));
    const ks = await kinds(); check('?cid=<project>: KV first, only live kinds', ks[0] === 'kv' && ks.every(k => LIVE_KINDS.includes(k)), ks.join());
    const wc = await pg.$$eval('#cols .strip .s-wc', e => e.map(x => x.textContent)), th0 = await pg.$eval('#sum thead th', e => e.textContent).catch(() => '');
    check('?cid=<project>: strips and the footer table say "whole project"', wc.every(t => t === 'whole project') && th0 === 'whole project', `${wc.join()} | ${th0}`);
    // a column that ran with another model than Knowledge Vault gets no percentage: "No memory ran with another model (gpt-5.2)"
    { const mdl = Object.fromEntries(await pg.$$eval('#cols .col', cs => cs.map(c => [c.dataset.kind, (c.querySelector('.hsel').innerText.split(' · ')[1] || '').trim()])));
      const hero = await T('#hero'), nm = { mem0: 'Mem0', memop: 'MemOp', none: 'No memory' };
      const odd = ks.filter(k => k !== 'kv' && mdl[k] && mdl.kv && mdl[k] !== mdl.kv && !/,/.test(mdl[k] + mdl.kv));
      check(`?cid=<project>: the footer sentence gives no percentage for a column on another model${odd.length ? ` (${odd.join(', ')})` : ' (none here)'}`, !/different models/.test(hero) && odd.every(k => !new RegExp(`(% (fewer|more) than|as many as|same as) ${nm[k]}`).test(hero)), hero); }
    await pg.screenshot({ path: `${OUT}/cmp_project.png` });
    // every project: the table's "tokens vs KV" agrees with the sentence (over the tasks both ran; "–" for a column on another model)
    { const bad = []; for (const p of projRan) { await pg.goto(BASE + '/compare?cid=' + encodeURIComponent(p.name)); await ready(); bad.push(...(await heroVsTable()).map(x => `${p.name}: ${x}`)); }
      check(`projects: on all ${projRan.length} projects the footer table's "tokens vs KV" agrees with the sentence above it`, !bad.length, bad.join('; ')); }
    // a project's strip never says "resolved" (sandbox tasks have no tests); a column whose tasks skip one (1, 3) reaches its last one with ⇥
    { const gap = projRan.find(p => Object.values(p.arms || {}).some(a => { const o = (a.orders || []).slice().sort((x, y) => x - y); return o.some((x, i) => i && x > o[i - 1] + 1); })), p = gap || projRan[0];
      await pg.goto(BASE + '/compare?cid=' + encodeURIComponent(p.name)); await ready();
      const ks2 = await kinds(), last = Object.fromEntries(ks2.map(k => [k, Math.max(0, ...(((p.arms || {})[k] || {}).orders || []))])), seen = [];
      for (let i = 0; i <= (p.tasks || []).length; i++) { await pg.click('#end'); await sleep(900); seen.push((await pg.$$eval('#cols .strip .stat', e => e.map(x => x.innerText))).join(' | ').replace(/\s+/g, ' ')); }
      check(`projects (${p.name}): ⇥ through it, the strips never say "resolved"`, seen.every(t => !/resolved/.test(t)) && seen.some(t => /so far/.test(t)), seen.join(' || '));
      if (!gap) console.log('SKIP projects: a column whose turns skip one (no such project on this server)');
      else { const rows = await Promise.all(ks2.map(k => T(`#task-${k}`)));
        check(`projects (${p.name}): a column whose turns skip one reaches its last turn with ⇥ (${ks2.map(k => `${k} → ${last[k]}`).join(', ')})`, ks2.every((k, i) => !last[k] || new RegExp(`^Turn ${last[k]}/`).test(rows[i])), rows.join(' | ')); } }
  }

  // ---------------------------------------------------------------- live runs (replay server only)
  if (!LIVE_OK) console.log('SKIP live runs (only against the replay server on :8799 or with ALLOW_LIVE=1)');
  else {
    const p = provs.find(x => x.ok);
    const run = p && cfgRun(p.name, LIVE_MODEL);
    const rc = run ? await getJ(`api/runchains?run=${encodeURIComponent(run)}&kinds=${LIVE_KINDS.join(',')}`).catch(() => ({ chains: [] })) : { chains: [] };
    const has = new Set((rc.chains || []).filter(c => Object.values(c.arms || {}).some(a => a.sessions.length)).map(c => c.cid));
    const meta2 = await getJ('api/meta');   // showcase chains first; once all have runs of this model, any other chain with an image
    const free = [...new Set([...sc.chains.filter(c => c.sif), ...meta2.chains.filter(c => c.sif && !c.custom)].map(c => c.cid))].filter(c => !has.has(c));
    // the replayed runs last as long as the recorded sessions (FAKE_STEP_SECS per step, FAKE_TASKS tasks): the 3-column run needs
    // sessions long enough to still be going when it is stopped, the 2-column run (which runs to the end) short ones
    const recRc = await getJ(`api/runchains?run=${sc.run}`).catch(() => ({ chains: [] })), steps = {};
    for (const c of recRc.chains || []) steps[c.cid] = Math.min(...['kv', 'mem0', 'none'].map(k => (c.arms[k]?.sessions || []).slice(0, +(process.env.FAKE_TASKS || 3)).reduce((a, x) => a + (x.n_steps || 0), 0)));
    const byLen = (lo, hi, not = []) => free.filter(c => !not.includes(c) && (steps[c] ?? 0) >= lo && (steps[c] ?? 0) <= hi).sort((a, b) => steps[a] - steps[b])[0];
    const cid = byLen(90, 1e9) || free[0], cid2 = byLen(40, 130, [cid]) || free.find(c => c !== cid), cid3 = free.find(c => c !== cid && c !== cid2);
    const nT = c => (meta2.chains.find(x => x.cid === c) || {}).n_tasks || 3, n1 = nT(cid);
    console.log(`INFO live tasks: ${cid} (${steps[cid]} steps), ${cid2} (${steps[cid2]}), ${cid3}`);
    if (!p || free.length < 3) check('live: three tasks without runs of the model', false, `provider ${p && p.name}; ${free.length} tasks free of ${run}`);
    else {
      const leak = async k => { const t = await pg.$eval(`#col-${k}`, e => e.innerText); const nm = { kv: 'Knowledge Vault', mem0: 'Mem0', none: 'No memory', memop: 'MemOp' }[k];
        return [(t.match(new RegExp(nm, 'g')) || []).length, (t.match(/turn \d\/\d/gi) || []).length, /pyccel_pyccel_|_20\d{6}|ledger calls|Apptainer|turn –/.test(t), /graded/.test(t)]; };
      await pg.goto(BASE + `/compare?cols=kv,mem0,none&cid=${cid}&api=${encodeURIComponent(p.name)}&model=${encodeURIComponent(LIVE_MODEL)}`); await ready();
      await until(async () => !!(await pg.$('#tr-kv [data-runcol="kv"]')), 15000);
      check('live: empty columns explain + offer Run live', /No Knowledge Vault run with/.test(await T('#tr-kv')) && !!(await pg.$('#tr-kv [data-runcol="kv"]')), await T('#tr-kv'));
      check('live: bar Run live shows now, tooltip mentions the cost', !(await pg.$eval('#runlive', e => e.hidden || e.disabled)) && /\$1–5/.test(await pg.$eval('#runlive', e => e.title)));
      await pg.screenshot({ path: `${OUT}/cmp_live_empty.png` });
      await pg.click('#tr-kv [data-runcol="kv"]'); await sleep(6000);
      check(`live: KV column streams "● live · turn 1/${n1} · step N"`, new RegExp(`^live · turn 1/${n1} · step \\d+$`).test(await T('#task-kv')) && !!(await pg.$('#task-kv .live-dot.on')), await T('#task-kv'));
      check('live: the running column shows its own Stop, the bar its ■ Stop', !(await pg.$eval('#stop-kv', e => e.hidden)) && !(await pg.$eval('#stoplive', e => e.hidden)));
      check('live: no bar Run live next to ■ Stop', await pg.$eval('#runlive', e => e.hidden));
      check('live: playback is off while the live run is all there is to watch', await pg.$$eval('#play, #step, #restart, #end', (bs, tip) => bs.every(b => b.disabled && b.title === tip), LIVE_TIP));
      await pg.click('#tr-mem0 [data-runcol="mem0"]'); await sleep(800); await pg.click('#tr-none [data-runcol="none"]'); await sleep(5000);
      check('live: all three columns stream', /live · turn/.test(await T('#task-mem0')) && /live · turn/.test(await T('#task-none')), `${await T('#task-mem0')} | ${await T('#task-none')}`);
      let lk = await Promise.all(['kv', 'mem0', 'none'].map(leak));
      check('live (~5 s): each fact once — method name only in the header, "turn k/3" once, no ids, no "graded" yet', lk.every(([nm, tk, bad, gr]) => nm === 1 && tk === 1 && !bad && !gr), JSON.stringify(lk));
      const lrows = await pg.$$eval('#cols .col .taskpin', ps => ps.map(p => { const h = p.querySelector('.tp-head'); return [Math.round(p.getBoundingClientRect().height), parseFloat(getComputedStyle(h).lineHeight) || 18, p.innerText]; }));
      check('live: the turn rows are at most two lines, all the same height, no "served by" on them', lrows.every(([h, lh, t]) => h <= 2 * lh + 14 && h === lrows[0][0] && !/served b/.test(t)), JSON.stringify(lrows));
      await pg.screenshot({ path: `${OUT}/cmp_live_5s.png` });
      await sleep(24000);
      lk = await Promise.all(['kv', 'mem0', 'none'].map(leak));
      check('live (~30 s): still each fact once, no ids', lk.every(([nm, tk, bad]) => nm === 1 && tk === 1 && !bad), JSON.stringify(lk));
      await pg.screenshot({ path: `${OUT}/cmp_live_30s.png` });
      await pg.click('#stop-mem0'); await sleep(200); await pg.click('#stop-mem0');
      check('live: a column Stop stops that column only', await until(async () => /^Stopped · \d of \d turns? graded$/.test(await T('#task-mem0')), 20000) && /live · turn/.test(await T('#task-kv')), `${await T('#task-mem0')} | ${await T('#task-kv')}`);
      await sleep(3000);
      await pg.click('#stoplive'); await sleep(200); await pg.click('#stoplive');
      check('live: bar Stop stops all, status says so', await until(async () => /Live runs (stopped|finished)/.test(await T('#livest')), 60000), await T('#livest'));
      await sleep(2000);
      check('live: Stop buttons hidden afterwards', (await pg.$eval('#stoplive', e => e.hidden)) && (await pg.$eval('#stop-kv', e => e.hidden)));
      check(`live: the turn rows say "Stopped · N of ${n1} turns graded"`, (await Promise.all(['kv', 'mem0', 'none'].map(k => T(`#task-${k}`)))).every(t => new RegExp(`^Stopped · \\d of ${n1} turns? graded$`).test(t)), (await Promise.all(['kv', 'mem0', 'none'].map(k => T(`#task-${k}`)))).join(' | '));
      check('live: playback is on again', await pg.$$eval('#play, #step, #restart, #end', bs => bs.every(b => !b.disabled)));
      // each stopped column: its saved totals (never "nothing was saved" once a task is graded), "only N of M tasks ran · run again" on
      // its own line (the link wholly visible), and the task row names a task that ran, not one that never started
      { const ch = await getJ(`api/chain/${cid}?run=__live__`), nameOf = o => { const t = ch.chain.tasks.find(x => x.order === o) || {}; return taskName(t.summary, t.title); };
        const st = await pg.$$eval('#cols .col', cs => cs.map(c => { const a = c.querySelector('.strip [data-again]'), r = a && a.getBoundingClientRect(), cr = c.getBoundingClientRect();
          return [c.dataset.kind, c.querySelector('.tp-n').textContent, c.querySelector('.strip').innerText.replace(/\s+/g, ' '), c.querySelector('.tp-title').textContent, !!a && r.width > 0 && r.left >= cr.left && r.right <= cr.right + 0.5 && !a.closest('.stat')]; }));
        const ok = st.map(([k, row, strip, name, again]) => { const g = +((row.match(/· (\d+) of/) || [])[1] || 0);
          return [k, g ? !/nothing was saved/.test(strip) && /tokens/.test(strip) && (g >= n1 || (new RegExp(`only ${g} of ${n1} turns ran · run again`).test(strip) && again)) && [...Array(g)].some((_, i) => nameOf(i + 1) === name) : /nothing was saved/.test(strip)]; });
        check('live: after the stop each column shows its saved totals, "run again" wholly visible, the turn row on a turn that ran', ok.every(([, v]) => v), JSON.stringify(st)); }
      const f = await footer();
      check('live: footer table has the graded turns', Number.isFinite(f.kv) && f.kv > 0, JSON.stringify(f));
      check('live: footer hero compares the live runs', /^Knowledge Vault used [\d.]+[MK]? tokens/.test(await T('#hero')), await T('#hero'));
      { const ht = await heroVsTable(); check('live: after the stops (columns that ran different turns) the table\'s "tokens vs KV" agrees with the sentence, over the turns both ran', !ht.length, `${ht.join('; ')} | ${await T('#hero')}`); }
      const sCost = Object.fromEntries(await Promise.all(['kv', 'mem0', 'none'].map(async k => [k, ((await T(`#ctr-${k} .s-usd`)) || '').trim()]))), fc = await footCost();
      check('live: after the run each strip shows the footer\'s cost', ['kv', 'mem0', 'none'].every(k => !sCost[k] || sCost[k] === fc[k]), `${JSON.stringify(sCost)} vs ${JSON.stringify(fc)}`);
      await pg.screenshot({ path: `${OUT}/cmp_live_after.png` });
      // the run is a saved live run now: ⚙ What to compare › Saved live runs lists it
      await gear(true); await pg.click('#srcseg button[data-src="saved"]').catch(() => {}); await sleep(2500);
      check('live: ⚙ Saved live runs lists the new run', (await pg.$$eval('#cid option', e => e.map(o => o.value))).includes(`${cid}|${LIVE_MODEL}`), JSON.stringify(await pg.$$eval('#cid option', e => e.map(o => o.value).slice(0, 6))));
      await pg.selectOption('#cid', `${cid}|${LIVE_MODEL}`).catch(() => {}); await sleep(2500); await gear(false);
      check('live: picked there, every column shows its run of the model', (await pg.$$eval('#cols .hsel', e => e.map(x => x.innerText))).every(t => new RegExp(`^${LIVE_MODEL} · live run`).test(t)), JSON.stringify(await pg.$$eval('#cols .hsel', e => e.map(x => x.innerText))));
      await pg.click('#play'); await sleep(2500); await pg.click('#play');
      check('live: Play all replays the saved live sessions', /Turn \d\/\d · step/.test(await T('#task-kv')), await T('#task-kv'));

      // a 2-column live run to the end: the footer follows the graded tasks; at the end strip and footer show the same cost
      await pg.goto(BASE + `/compare?cols=kv,mem0&cid=${cid2}&api=${encodeURIComponent(p.name)}&model=${encodeURIComponent(LIVE_MODEL)}`); await ready();
      await until(async () => !(await pg.$eval('#runlive', e => e.hidden || e.disabled)), 15000);
      await pg.click('#runlive');
      check('2 columns: both stream', await until(async () => /live · turn/.test(await T('#task-kv')) && /live · turn/.test(await T('#task-mem0')), 15000));
      const n2 = nT(cid2), graded1 = await until(async () => new RegExp(`turn [2-9]/${n2}`).test(await T('#task-kv')), 180000, 1000);
      check('2 columns: after turn 1 the footer names the graded turn', graded1 && await until(async () => new RegExp(`^Knowledge Vault used [\\d.]+[MK]? tokens on [1-9] of ${n2} turns`).test(await T('#hero')), 20000), await T('#hero'));
      const ended = await until(async () => /Live runs (finished|stopped|ended)/.test(await T('#livest')), 420000, 2000);
      const nf = Math.min(n2, +(process.env.FAKE_TASKS || 3));        // the replay server replays FAKE_TASKS tasks (servers.sh: 3)
      check(`2 columns: the runs end, the rows say "Finished · ${nf} of ${n2} turns graded"`, ended && new RegExp(`^Finished · ${nf} of ${n2} turns? graded$`).test(await T('#task-kv')), `${await T('#livest')} | ${await T('#task-kv')}`);
      await sleep(1500);
      const sc2 = Object.fromEntries(await Promise.all(['kv', 'mem0'].map(async k => [k, ((await T(`#ctr-${k} .s-usd`)) || '').trim()]))), fc2 = await footCost();
      check('2 columns: strip cost = footer cost', ['kv', 'mem0'].every(k => sc2[k] && sc2[k] === fc2[k]), `${JSON.stringify(sc2)} vs ${JSON.stringify(fc2)}`);
      check('2 columns: no "100% fewer" and no "No runs to compare"', !/100% fewer|No runs to compare/.test(await T('#hero')), await T('#hero'));
      await pg.screenshot({ path: `${OUT}/cmp_live_2col_done.png` });
      // ⚙ Saved live runs of that chain × model: No memory has no run; ▶ Run live there: its header says "running live" at once and
      // the ⚙ line stops listing it under "No run yet"
      await pg.goto(BASE + `/compare?cols=kv,mem0,none&cid=${cid2}&saved=${encodeURIComponent(LIVE_MODEL)}`); await ready();
      await until(async () => !(await pg.$eval('#runlive', e => e.hidden || e.disabled)), 15000);
      check('saved + ▶ Run live: before, No memory says "no run yet" (header and ⚙ line)', /· no run yet$/.test(await IT('#hsel-none')) && /No run yet: No memory/.test(await T('#chainnote')), `${await IT('#hsel-none')} | ${await T('#chainnote')}`);
      { const t0 = Date.now(); await pg.click('#runlive');
        const ok = await until(async () => /· running live$/.test(await IT('#hsel-none')) && !/No run yet/.test(await T('#chainnote')), 5000, 250);
        check('saved + ▶ Run live: within 5 s the running column\'s header says "running live", the ⚙ line no longer lists it', ok, `${Date.now() - t0} ms: ${await IT('#hsel-none')} | ${await T('#chainnote')}`); }
      await pg.screenshot({ path: `${OUT}/cmp_saved_runlive.png` });
      await until(async () => !(await pg.$eval('#stoplive', e => e.hidden)), 8000);
      await pg.click('#stoplive'); await sleep(200); await pg.click('#stoplive');
      await until(async () => /Live runs/.test(await T('#livest')), 60000, 1000);

      // MemOp runs live where the server says so (meta.live_kinds)
      if (!memopLive) console.log('SKIP MemOp live (this server does not run MemOp live)');
      else {
        await pg.goto(BASE + `/compare?cols=kv,memop&cid=${cid3}&api=${encodeURIComponent(p.name)}&model=${encodeURIComponent(LIVE_MODEL)}`); await ready();
        await until(async () => !!(await pg.$('#tr-memop [data-runcol="memop"]')), 15000);
        check('MemOp live: API + Model selects, no "recorded run" chip, no "not available" tooltip', (await pg.$eval('#api-memop', e => e.value)) === p.name && (await pg.$eval('#model-memop', e => e.value)) === LIVE_MODEL && (await pg.$eval('#rec-memop', e => e.hidden)) && !/aren't available/.test(await pg.$eval('#hsel-memop', e => e.innerHTML)));
        check('MemOp live: its empty column offers ▶ Run live', /No MemOp run with/.test(await T('#tr-memop')) && !!(await pg.$('#tr-memop [data-runcol="memop"]')), await T('#tr-memop'));
        await pg.click('#tr-memop [data-runcol="memop"]');
        check('MemOp live: the column streams', await until(async () => new RegExp(`^live · turn \\d+/${nT(cid3)} · step \\d+$`).test(await T('#task-memop')), 20000), await T('#task-memop'));
        const lm = await leak('memop'); check('MemOp live: each fact once', lm[0] === 1 && lm[1] === 1 && !lm[2], JSON.stringify(lm));
        await until(async () => /Written to memory|Memory injected/.test(await T('#tr-memop')) || /turn [2-9]\//.test(await T('#task-memop')), 120000, 1000);
        await pg.screenshot({ path: `${OUT}/cmp_live_memop.png` });
        await pg.click('#stop-memop'); await sleep(200); await pg.click('#stop-memop');
        check('MemOp live: stops', await until(async () => /^Stopped · \d of \d turns? graded$/.test(await T('#task-memop')), 30000), await T('#task-memop'));
        await until(async () => /Live runs/.test(await T('#livest')), 20000);
      }

      // two tabs: the second one's ▶ Run live of a column already running streams that run; and ↺ the moment a run ends rewinds the
      // run just saved — never "no run yet ▶ Run live" over it (a paid overwrite)
      { const EM = `${LIVE_MODEL}-end`, erun = cfgRun(p.name, EM);
        const erc = await getJ(`api/runchains?run=${encodeURIComponent(erun)}&kinds=${LIVE_KINDS.join(',')}`).catch(() => ({ chains: [] }));
        const eHas = new Set((erc.chains || []).filter(c => Object.values(c.arms || {}).some(a => a.sessions.length)).map(c => c.cid));
        const kvSteps = c => ((recRc.chains || []).find(x => x.cid === c)?.arms.kv?.sessions || []).slice(0, +(process.env.FAKE_TASKS || 3)).reduce((a, x) => a + (x.n_steps || 0), 0);
        const cid4 = [...new Set([...sc.chains.filter(c => c.sif), ...meta2.chains.filter(c => c.sif && !c.custom)].map(c => c.cid))].filter(c => !eHas.has(c) && kvSteps(c) > 0).sort((a, b) => kvSteps(a) - kvSteps(b))[0];
        if (!cid4) check('two tabs: a task without runs of the model', false, EM);
        else {
          console.log(`INFO two tabs + ↺ at the end: ${cid4} (${kvSteps(cid4)} Knowledge Vault steps)`);
          const url = `/compare?cols=kv,none&cid=${cid4}&api=${encodeURIComponent(p.name)}&model=${encodeURIComponent(EM)}`;
          const p2 = await ctx.newPage(); watch(p2, 'tab2'); const T2 = async sel => ((await p2.textContent(sel).catch(() => '')) || '').replace(/\s+/g, ' ').trim();
          await pg.goto(BASE + url); await p2.goto(BASE + url);
          await until(async () => !!(await pg.$('#tr-kv [data-runcol="kv"]')) && !!(await p2.$('#tr-kv [data-runcol="kv"]')), 20000);
          await pg.click('#tr-kv [data-runcol="kv"]'); await until(async () => /^live · turn/.test(await T('#task-kv')), 15000);
          await p2.click('#tr-kv [data-runcol="kv"]');          // the second tab still offers ▶ Run live; the server refuses a second run
          const adopted = await until(async () => /^live · turn/.test(await T2('#task-kv')) && !(await p2.$eval('#stop-kv', e => e.hidden)), 10000, 250);
          check('two tabs: the second tab\'s ▶ Run live of a column already running streams that run, with its ■ Stop, and says so (no error)', adopted && !(await p2.$eval('#livest', e => e.classList.contains('err'))) && /already running — showing it live/.test(await T2('#livest')), `${await T2('#task-kv')} | ${await T2('#livest')}`);
          await p2.close();
          // the run ends (FAKE_TASKS tasks): ↺ as soon as the playback buttons are back
          const back = await until(async () => !/^live/.test(await T('#task-kv')) && !(await pg.$eval('#restart', e => e.disabled)), 300000, 50);
          if (back) await pg.click('#restart');
          await sleep(6000);
          const kvRow = await T('#task-kv');
          check('a live run that just ended: ↺ the moment playback is back rewinds the saved run (no "no run yet", no ▶ Run live over it)', back && !(await pg.$('#tr-kv .colempty')) && !(await pg.$('#tr-kv [data-runcol]')) && /^Turn 1\/\d+ · step 0\/\d+$/.test(kvRow), `${back ? '' : 'playback never came back · '}${kvRow} | ${(await T('#tr-kv')).slice(0, 90)}`);
          await pg.screenshot({ path: `${OUT}/cmp_live_end_restart.png` });
        }
      }
    }
  }

  // ---------------------------------------------------------------- ⚙ Saved live runs: one run per entry (the base run), ?run= pins it
  // An entry (chain × model) can hold several runs. Its columns come from one run: the one with sessions of the most columns on screen
  // (newest on ties); a method without a session there shows its newest run with the model and its header says "other run";
  // ?run=<id> makes <id> that run and stays in the URL. On a replay server the suite first makes such an entry: two Agent-page
  // live runs of one chain (Knowledge Vault, then Mem0), which record no model there.
  if (LIVE_OK) {
    const p = provs.find(x => x.ok), post = async (u, d) => { const r = await fetch(BASE + '/' + u, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d || {}) }); return r.json(); };
    if (p) {
      const ids = [];
      for (const kind of ['kv', 'mem0']) { const j = await post('api/live/start', { cid: DEF, kind, provider: p.name, model: `${LIVE_MODEL}-agent` }).catch(() => null); if (j && j.id) ids.push(j.id); await sleep(3500); }
      await sleep(3000); for (const id of ids) await post(`api/live/${id}/stop`).catch(() => {});
      await until(async () => (await Promise.all(ids.map(id => getJ(`api/live/${id}`)))).every(j => j.status !== 'running'), 60000, 1000);
      check('saved runs: two Agent-page live runs made for the next checks', ids.length === 2, `${ids.length} started`);
    }
  }
  {
    const lr2 = await getJ('api/liveruns').catch(() => []), meta3 = await getJ('api/meta'), C = ['kv', 'mem0', 'none'];
    // entries with at least one Agent-page run: cid|model -> run -> {kind: started}
    const G = new Map(), add = (cid, model, run, kind, t) => { const g = G.get(`${cid}|${model}`) || new Map(); const r = g.get(run) || {}; r[kind] = Math.max(r[kind] || 0, t || 0); g.set(run, r); G.set(`${cid}|${model}`, g); };
    for (const e of lr2) if (!e.run.startsWith('cfg__') && (e.sessions || []).length && LIVE_KINDS.includes(e.kind)) add(e.cid, e.model || '', e.run, e.kind, e.started);
    for (const key of [...G.keys()]) { const [cid, model] = [key.slice(0, key.indexOf('|')), key.slice(key.indexOf('|') + 1)]; if (!model) continue;
      for (const pv of provs) { const r = cfgRun(pv.name, model); if (!(meta3.runs || []).includes(r)) continue;
        const rc = await getJ(`api/runchains?run=${encodeURIComponent(r)}&cid=${encodeURIComponent(cid)}&kinds=${LIVE_KINDS.join(',')}`).catch(() => ({ chains: [] }));
        for (const c of rc.chains || []) if (c.cid === cid) for (const [k, a] of Object.entries(c.arms || {})) if ((a.sessions || []).length) add(cid, model, r, k, a.started); } }
    const multi = [...G.entries()].filter(([, g]) => g.size >= 2 && [...g.values()].some(r => C.some(k => k in r)));
    // prefer an entry whose runs cover the columns differently (one run has more of them), then the one with the most runs
    const cover = r => C.filter(k => k in r).length, newest = r => Math.max(...Object.values(r));
    multi.sort(([, a], [, b]) => (new Set([...b.values()].map(cover)).size - new Set([...a.values()].map(cover)).size) || b.size - a.size);
    if (!multi.length) console.log('SKIP saved runs: no task × model with two saved runs on this server');
    else {
      const [key, g] = multi[0], cid = key.slice(0, key.indexOf('|')), model = key.slice(key.indexOf('|') + 1), runs = [...g.entries()];
      const base = runs.slice().sort(([, a], [, b]) => cover(b) - cover(a) || newest(b) - newest(a))[0][0];
      const want = pin => C.map(k => { const own = g.get(pin)?.[k] != null ? pin : runs.filter(([, r]) => k in r).sort(([, a], [, b]) => b[k] - a[k])[0]?.[0]; return [k, own || null, own && own !== pin]; });
      const heads = () => pg.$$eval('#cols .col', cs => cs.map(c => [c.dataset.kind, c.querySelector('.hsel').innerText.trim(), c.querySelector('.hsel .static')?.title || '']));
      const agree = (hs, w) => hs.every(([k, t, tip]) => { const [, run, other] = w.find(x => x[0] === k); return !run ? /no run yet/.test(t) : tip.includes(`(${run})`) && (other ? /· other run/.test(t) : /· live run/.test(t)); });
      console.log(`INFO saved entry ${key}: ${runs.map(([r, x]) => `${r} [${Object.keys(x).join(',')}]`).join(' · ')}; base ${base}`);
      await pg.setViewportSize({ width: 1500, height: 900 });
      await pg.goto(BASE + `/compare?cols=kv,mem0,none&cid=${encodeURIComponent(cid)}&saved=${encodeURIComponent(model)}`); await ready();
      let hs = await heads();
      check('saved runs: the columns come from the run with the most of them; a method missing there says "other run"', agree(hs, want(base)), JSON.stringify(hs));
      check('saved runs: no "⚙ What to compare picks another" in the tooltips', hs.every(([, , tip]) => !/picks another/.test(tip)));
      const hero1 = await T('#hero');
      if (base.startsWith('cfg__') && cover(g.get(base)) === C.length) {
        const pv = provs.find(x => cfgRun(x.name, model) === base);
        if (pv) { await pg.goto(BASE + `/compare?cols=kv,mem0,none&cid=${encodeURIComponent(cid)}&api=${encodeURIComponent(pv.name)}&model=${encodeURIComponent(model)}`); await ready();
          check('saved runs: the footer sentence equals the same run picked by API and model', (await T('#hero')) === hero1, `${hero1} | ${await T('#hero')}`); }
      }
      const pin = runs.map(([r]) => r).find(r => r !== base);
      await pg.goto(BASE + `/compare?cols=kv,mem0,none&cid=${encodeURIComponent(cid)}&run=${encodeURIComponent(pin)}`); await ready();
      hs = await heads();
      check('saved runs: ?run=<id> shows that run and stays in the URL; the other methods say "other run"', new URL(pg.url()).searchParams.get('run') === pin && (await src()) === 'saved' && agree(hs, want(pin)), `${pg.url()} ${JSON.stringify(hs)}`);
      // the footer sentence says when Knowledge Vault resolved fewer of the compared tasks
      const res = Object.fromEntries(await pg.$$eval('#sum table tbody tr', trs => trs.map(tr => [tr.dataset.kind, tr.children[2].textContent.trim()])));
      const [kr, kn] = String(res.kv || '').split('/').map(Number), more = Object.entries(res).filter(([k, v]) => k !== 'kv' && /^\d+\/\d+$/.test(v) && +v.split('/')[1] === kn && +v.split('/')[0] > kr);
      if (Number.isFinite(kr) && more.length) check(`saved runs: the footer says Knowledge Vault resolved fewer turns ("resolved ${kr} of ${kn}")`, (await T('#hero')).includes(`it resolved ${kr} of ${kn} turn`), await T('#hero'));
      await pg.screenshot({ path: `${OUT}/cmp_saved_pinned.png` });
      await pg.reload(); await ready();
      check('saved runs: a reload keeps the pinned run', new URL(pg.url()).searchParams.get('run') === pin && agree(await heads(), want(pin)), pg.url());
    }
  }

  // ---------------------------------------------------------------- sizes, phone, dark
  const ep0 = provs.find(x => x.ok), sameUrl = ep0 ? `/compare?cols=kv,mem0,none&api=${encodeURIComponent(ep0.name)}&model=${encodeURIComponent(ep0.default_model || (ep0.models || [])[0] || 'auto')}` : null;
  for (const [w, h, url, tag] of [[2000, 1100, '/compare?cols=kv,mem0,none', 'cmp_2000'], [1920, 1000, '/compare?cols=kv,mem0,memop,rawcat,none', 'cmp_1920_5'], [1280, 800, '/compare?cols=kv,mem0,none', 'cmp_1280'], [1050, 800, '/compare?cols=kv,mem0,none', 'cmp_1050'],
      ...(sameUrl ? [[1050, 800, sameUrl, 'cmp_1050_same']] : []), [420, 900, '/compare?cols=kv,mem0,memop,rawcat,none', 'cmp_phone']]) {
    await pg.setViewportSize({ width: w, height: h }); await pg.goto(BASE + url); await ready();
    const sw = await pg.evaluate(() => document.documentElement.scrollWidth);
    check(`${w}px${tag.endsWith('_same') ? ' (a model picked)' : ''}: no horizontal overflow`, sw <= w, `scrollWidth=${sw}`);
    const hh = await pg.$eval('header.top', e => e.getBoundingClientRect().height);
    if (tag.endsWith('_same')) check(`${w}px with a model picked: the top bar is at most two rows`, hh <= 96, `${hh}px`);
    else if (w >= 1050) check(`${w}px: the top bar is one row`, hh <= 52, `${hh}px`); else check(`${w}px: the top bar wraps into at most 3 rows`, hh <= 130, `${hh}px`);
    // the page nav and ⚙ stay together at the right edge, also when the bar wraps
    const ng = await pg.evaluate(() => { const n = document.querySelector('.pnav').getBoundingClientRect(), g = document.querySelector('#gearbtn').getBoundingClientRect(); return [Math.round(n.top + n.height / 2), Math.round(g.top + g.height / 2), Math.round(g.right)]; });
    check(`${w}px${tag.endsWith('_same') ? ' (a model picked)' : ''}: nav and ⚙ on one row at the right edge`, Math.abs(ng[0] - ng[1]) <= 4 && ng[2] >= w - (w < 600 ? 15 : 20), JSON.stringify(ng));
    if (w === 1280 || w === 1050) {
      // narrow columns: the task's name gets its own line (or at least 25 characters of it show); hovering the row gives the whole name
      const tn = await pg.$$eval('#cols .col', cs => cs.map(c => { const t = c.querySelector('.tp-title'), n = c.querySelector('.tp-n'), cut = t.scrollWidth > t.clientWidth + 1;
        return [c.dataset.kind, t.getBoundingClientRect().top >= n.getBoundingClientRect().bottom - 2, cut ? Math.floor(t.textContent.length * t.clientWidth / t.scrollWidth) : t.textContent.length, c.querySelector('.tp-head').title === t.textContent]; }));
      check(`${w}px: every turn name has its own line or shows ≥ 25 characters, and its tooltip is the name`, tn.every(([, own, vis, tip]) => (own || vis >= 25) && tip), JSON.stringify(tn));
    }
    if (w < 600) {
      const wl = await pg.$eval('#cols .col .transcript .turn .who', e => [e.getBoundingClientRect().height, parseFloat(getComputedStyle(e).lineHeight) || 17]).catch(() => null);
      if (wl) check('phone: a step\'s who-line is one line', wl[0] <= 1.6 * wl[1], JSON.stringify(wl));
    }
    const sel = await pg.$$eval('#cols .col', cs => cs.map(c => { const a = c.querySelector('select[id^="api-"]'); return a ? Math.round(a.getBoundingClientRect().width) : 0; }));
    check(`${w}px: every API select is at least 120 px wide ("Recorded run" readable)`, sel.every(x => x >= 120), sel.join(','));
    await pg.click('#gearbtn'); await sleep(300);
    const r = await pg.$eval('#gearpanel', e => { const b = e.getBoundingClientRect(); return [b.left, b.right]; }), sg = await pg.$$eval('#srcseg button:not([hidden])', e => e.map(x => { const b = x.getBoundingClientRect(); return [b.left, b.right]; }));
    check(`${w}px: the ⚙ panel and its source switch fit the screen`, r[0] >= 0 && r[1] <= w && sg.every(([l, rr]) => l >= r[0] && rr <= r[1]), `${r.join()} ${JSON.stringify(sg)}`);
    if (w < 600) await pg.screenshot({ path: `${OUT}/${tag}_gear.png` });
    await pg.keyboard.press('Escape');
    const xs = await pg.$$eval('#cols .col', e => e.map(x => Math.round(x.getBoundingClientRect().left)));
    if (w < 600) check('phone: columns stack', new Set(xs).size === 1, xs.join()); else check(`${w}px: columns side by side`, new Set(xs).size === xs.length, xs.join());
    await pg.screenshot({ path: `${OUT}/${tag}.png`, fullPage: w < 600 });
  }
  const dctx = await newCtx({ colorScheme: 'dark' }), dk = await dctx.newPage(); watch(dk, 'dark');
  await dk.goto(BASE + '/compare?cols=kv,mem0,memop,rawcat,none'); await dk.waitForSelector('#sum table tbody tr', { state: 'attached', timeout: 30000 }); await sleep(1200);
  const bg = await dk.evaluate(() => getComputedStyle(document.body).backgroundColor), mc = await dk.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--memop').trim());
  check('dark: dark palette incl. --memop', bg !== 'rgb(243, 243, 240)' && mc === '#9a85ec', `${bg} ${mc}`);
  await dk.screenshot({ path: `${OUT}/cmp_dark.png` });
  // the server not answering: a plain card, controls off
  const fctx = await newCtx(), fp = await fctx.newPage(); fp.on('pageerror', e => errs.push(`bootfail PAGEERROR ${e.message}`));
  await fp.route('**/api/meta*', r => r.abort()); await fp.goto(BASE + '/compare'); await sleep(2500);
  check('server away: "Couldn\'t reach the demo server" card, playback off', /Couldn't reach the demo server/.test(await fp.textContent('#cols')) && !!(await fp.$('#retry')) && await fp.$eval('#play', e => e.disabled));

  console.log('errors:', errs.length ? errs.join('\n') : 'none'); console.log(`SUMMARY ${failed} failed of ${total}`); await b.close(); process.exit(failed || errs.some(e => /PAGEERROR/.test(e)) ? 1 : 0);
})().catch(e => { console.error('FAILED', e); process.exit(1); });
