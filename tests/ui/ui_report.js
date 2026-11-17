// Report page (/report): top bar and ⚙, plain words, each number once, the methods table at desktop widths, the chain
// explorer (opens on the server's example chain — /api/meta default_cid, read from the server —, two chain groups, task summaries as
// headings, a chain outside the list (pyccel_pyccel_20240307 since the list was cut to 8) opened by a link, memory notes and the issue formatted as
// on the Agent page, the task bars' key, ?cid= / ?kind= / ?run= in the URL, Agent / Compare links), the failure card, phone
// width and dark mode (page background as on the Agent page, a visible memory-writing segment). Starts nothing: every non-GET /api request is aborted, so it is safe on
// any server (the replay server or, read-only, the dev server on 8766).
// Run: BASE=http://127.0.0.1:8799 node tests/ui/ui_report.js   (SHOTS=/abs/dir for the screenshots)
const { chromium } = require('playwright');
const path = require('path');
const BASE = process.env.BASE || 'http://127.0.0.1:8799';
const OUT = process.env.SHOTS || path.join(__dirname, 'shots'); require('fs').mkdirSync(OUT, { recursive: true });
const sleep = ms => new Promise(r => setTimeout(r, ms)); const results = [];
const check = (name, ok, info = '') => { results.push([ok ? 'PASS' : 'FAIL', name, info]); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${info ? ' — ' + info : ''}`); };
// WCAG contrast of two computed colours (rgb()/rgba()/color(srgb …) strings), evaluated in the page through a canvas
const CONTRAST = ([a, b]) => { const cv = document.createElement('canvas'); cv.width = cv.height = 1; const cx = cv.getContext('2d', { willReadFrequently: true });
  const rgb = c => { cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#fff'; cx.fillRect(0, 0, 1, 1); cx.fillStyle = c; cx.fillRect(0, 0, 1, 1); return [...cx.getImageData(0, 0, 1, 1).data].slice(0, 3); };
  const lin = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }, L = c => { const [r, g, b_] = rgb(c); return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b_); };
  const x = L(a), y = L(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
const FORBIDDEN = [/\barms?\b/i, /\bactor\b/i, /\bwriter\b/i, /harness/i, /ledger/i, /mem_/, /\(seq\)/, /\bF2P\b/, /LimitsExceeded/, /calls\.jsonl/, /\/srv\//, /\bSIF\b/, /Replay trajectory/i];

(async () => {
  const b = await chromium.launch(); const errs = [], liveReqs = [];
  const open = async (opts = {}) => {
    const ctx = await b.newContext({ viewport: { width: 1500, height: 950 }, ...opts }); const pg = await ctx.newPage();
    await pg.route('**/api/**', r => r.request().method() === 'GET' ? r.continue() : r.abort());      // read-only on every server
    pg.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
    pg.on('response', r => { if (r.status() >= 400 && !opts.expectFail) errs.push('HTTP ' + r.status() + ' ' + r.url().replace(BASE, '')); });
    pg.on('request', r => { if (/\/api\/live/.test(r.url()) && /\/report/.test(pg.url())) liveReqs.push(r.url().replace(BASE, '')); });   // Compare (opened once to measure its bar) does poll
    return pg;
  };
  const ready = async pg => { await pg.waitForSelector('#chainview .task, #main .card.down', { timeout: 120000 }); await sleep(400); };
  const get = async (pg, p) => pg.evaluate(async p => (await fetch(p)).json(), p);
  const vis = async (pg, sel) => pg.$eval(sel, e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)).catch(() => false);
  const box = async (pg, sel) => pg.$eval(sel, e => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });

  const pg = await open();
  await pg.goto(BASE + '/report'); await ready(pg);
  const meta = await get(pg, '/api/meta'), sc = await get(pg, '/api/showcase');
  const run = await pg.$eval('#run', e => e.value), price = await pg.$eval('#price', e => e.value);
  const sm = await get(pg, `/api/summary?run=${run}&price=${price}`);
  check('report loads: headline, charts, table, explorer', !!(await pg.$('#main .hero')) && (await pg.$$('#main svg')).length >= 2 && !!(await pg.$('#main table')) && !!(await pg.$('#explorer')),
    `svgs=${(await pg.$$('#main svg')).length}`);
  check('tab title and favicon', (await pg.title()) === 'Report · Knowledge Vault' && !!(await pg.$('link[rel=icon][href^="data:"]')), await pg.title());

  // ---------------------------------------------------------------- top bar and ⚙
  const brand = await pg.$eval('header.top a.brand', e => ({ href: e.getAttribute('href'), text: e.textContent.trim() }));
  check('the logo links to ./ (Your sandbox → New project)', brand.href === './' && brand.text === 'Knowledge Vault', JSON.stringify(brand));
  const nav = await pg.$$eval('header.top .pnav a', as => as.map(a => [a.textContent.trim(), a.getAttribute('href'), a.getAttribute('aria-current')]));
  check('top bar: Agent | Compare | Report, Report current', JSON.stringify(nav) === JSON.stringify([['Agent', './', null], ['Compare', 'compare', null], ['Report', 'report', 'page']]), JSON.stringify(nav));
  const nb = await box(pg, 'header.top .pnav'), gb = await box(pg, '#gearbtn');
  check('nav and ⚙ at the top right, nothing above the title', nb.x + nb.w <= gb.x && gb.x + gb.w > 1400 && Math.abs((nb.y + nb.h / 2) - (gb.y + gb.h / 2)) < 4 && (await box(pg, '.pagehead h1')).y > gb.y + gb.h,
    `nav=${Math.round(nb.x)},${Math.round(nb.y)} gear=${Math.round(gb.x)},${Math.round(gb.y)}`);
  check('the old header selects are gone from the page (they live in ⚙)', !(await vis(pg, '#run')) && !(await vis(pg, '#price')) && !(await pg.$('#liveform')) && !(await pg.$('#live_cid')));
  await pg.click('#gearbtn'); await sleep(200);
  const runs = await pg.$$eval('#run option', os => os.map(o => [o.value, o.textContent.trim()]));
  const panel = await pg.$eval('#gearpanel', e => e.innerText);
  check('⚙ opens: Recorded run, Price row, Show details', await vis(pg, '#gearpanel') && /Recorded run/i.test(panel) && /Price row/i.test(panel) && /Show details/i.test(panel) && (await pg.$$('#price option')).length >= 1,
    panel.replace(/\s+/g, ' ').slice(0, 140));
  check('⚙ Recorded run lists only the paper runs, in plain words', runs.length >= 1 && runs.length <= 3 && runs.every(([v, t]) => !/^(live_|cfg__|custom|demo_live|smoke)/.test(v) && (/^Paper run/.test(t) || v === meta.default_run)),
    JSON.stringify(runs));
  await pg.keyboard.press('Escape'); await sleep(150);
  check('Esc closes ⚙ and gives the focus back to it', !(await vis(pg, '#gearpanel')) && (await pg.evaluate(() => document.activeElement && document.activeElement.id)) === 'gearbtn');
  await pg.click('#gearbtn'); await pg.click('.pagehead h1'); await sleep(150);
  check('a click outside closes ⚙', !(await vis(pg, '#gearpanel')));
  {
    const cp = await open(); await cp.goto(BASE + '/compare'); await cp.waitForSelector('header.top .pnav', { timeout: 60000 }); await sleep(800);
    const cn = await box(cp, 'header.top .pnav').catch(() => null), cbr = await cp.$eval('header.top .brand', e => [getComputedStyle(e).fontSize, getComputedStyle(e).color]).catch(() => null); await cp.context().close();
    const rbr = await pg.$eval('header.top .brand', e => [getComputedStyle(e).fontSize, getComputedStyle(e).color]);
    check('the brand looks as on Compare (size and colour)', JSON.stringify(cbr) === JSON.stringify(rbr), `compare ${cbr} report ${rbr}`);
    check('nav pill sits where it does on Compare (1500 px)', !!cn && Math.abs(cn.y - nb.y) <= 2 && Math.abs((cn.x + cn.w) - (nb.x + nb.w)) <= 3, cn ? `compare ${Math.round(cn.x + cn.w)},${Math.round(cn.y)} report ${Math.round(nb.x + nb.w)},${Math.round(nb.y)}` : 'no nav on /compare');
  }

  // ---------------------------------------------------------------- plain words, each number once
  const shown = await pg.evaluate(() => { let t = document.body.innerText; for (const o of document.querySelectorAll('select option')) t = t.split(o.textContent).join(' '); return t; });
  const bad = FORBIDDEN.filter(re => re.test(shown)).map(String);
  check('plain words: no paper or harness jargon on screen (details closed)', !bad.length, bad.join(' ') || `${shown.length} chars checked`);
  const A = sm.arms.kv, B = sm.arms.mem0, usd = v => '$' + v.toFixed(2), save = (Math.abs((B.usd_chain - A.usd_chain) / B.usd_chain) * 100).toFixed(1) + '%';
  const above = await pg.evaluate(() => { const out = []; for (const el of document.querySelector('#main').children) { if (el.querySelector('table')) break; out.push(el.innerText); } return out.join('\n'); });
  const count = s => above.split(s).length - 1;
  check('each number once above the table: cost per task and the saving', count(usd(A.usd_chain)) === 1 && count(save) === 1 && count(usd(B.usd_chain)) === 1,
    `${usd(A.usd_chain)}×${count(usd(A.usd_chain))} ${save}×${count(save)} ${usd(B.usd_chain)}×${count(usd(B.usd_chain))}`);
  const P = sm.price || {}, heroT = (await pg.textContent('#main .hero')).replace(/\s+/g, ' ');
  check('the headline says the price in plain words (no price-row name)', heroT.includes(`Priced at $${P.input} per million input tokens and $${P.output} per million output tokens.`) && !heroT.includes(sm.price_row + ' row'), heroT.slice(-90));
  check('tiles: tokens and steps per turn only', JSON.stringify(await pg.$$eval('.kpis .tile .label', ls => ls.map(l => l.textContent.trim()))) === JSON.stringify(['Tokens per turn (agent + memory writing)', 'Agent steps per turn']));
  check('"How the numbers are made" is collapsed at the bottom', !!(await pg.$('footer details#how')) && !(await pg.$eval('#how', e => e.open)) && /How the numbers are made/.test(await pg.textContent('#how summary')));
  for (const w of [1280, 1500, 2000]) {
    await pg.setViewportSize({ width: w, height: 950 }); await sleep(300);
    const t = await pg.$eval('.tabwrap', e => [e.scrollWidth, e.clientWidth]), sw = await pg.evaluate(() => document.documentElement.scrollWidth);
    check(`the methods table fits at ${w} px (no inner scrollbar)`, t[0] <= t[1] && sw <= w, `table ${t[0]}/${t[1]} page ${sw}`);
  }
  await pg.setViewportSize({ width: 1500, height: 950 }); await sleep(300);

  // ---------------------------------------------------------------- chain explorer
  const def = sc.default_cid || meta.default_cid, scc = (sc.chains || []).find(c => c.cid === def);
  const cur = await pg.$eval('#cid', e => ({ v: e.value, t: e.selectedOptions[0].textContent.trim() }));
  check(`the explorer opens on the example task (${def}, the server's default)`, cur.v === def && def === meta.default_cid && (!(sc.chains || []).length || !!scc) && (!scc || cur.t === `${scc.title} — ${scc.repo}`), `${cur.v} "${cur.t}"`);
  check('its one-line description under the picker', !scc || (await pg.textContent('#cidnote')).trim() === scc.blurb, (await pg.textContent('#cidnote')).trim().slice(0, 80));
  const firsts = await pg.$$eval('#cid option', os => os.map(o => o.value)), inRun = new Set(sm.chains.map(c => c.cid));
  const want = (sc.chains || []).map(c => c.cid).filter(c => inRun.has(c));
  check('the example tasks come first, as on Compare', JSON.stringify(firsts.slice(0, want.length)) === JSON.stringify(want) && firsts.length === sm.chains.length, `${want.length} examples of ${firsts.length}`);
  const groups = await pg.$$eval('#cid optgroup', gs => gs.map(g => [g.label, g.children.length]));
  check('… in their own group, then every other task of the run', !want.length || want.length === firsts.length || (groups.length === 2 && groups[0][0] === 'Example tasks — where Knowledge Vault saves tokens' && groups[0][1] === want.length
    && groups[1][1] === firsts.length - want.length && groups[1][0] === `The other ${firsts.length - want.length} tasks of the paper run`), JSON.stringify(groups));
  const ch = await get(pg, `/api/chain/${def}?run=${run}&price=${price}`);
  const heads = await pg.$$eval('#chainview .task .sum', es => es.map(e => e.textContent.trim()));
  // a server from before task summaries (no "summary" field) gets the page's fallback, the issue's first line
  const hasSum = ch.chain.tasks.every(t => typeof t.summary === 'string');
  const sums = ch.arms.kv.sessions.map(s => { const t = ch.chain.tasks.find(x => x.order === s.order) || {}; return hasSum ? t.summary : t.title || ''; });
  check('each turn is headed by its summary', heads.length === sums.length && heads.length > 0 && heads.every((h, i) => h === sums[i] && h.length > 0) && (!hasSum || !heads.some(h => h.length > 81)),
    (hasSum ? '' : 'THIS SERVER SENDS NO SUMMARIES (restart it): titles shown · ') + JSON.stringify(heads));
  check('the raw first line of the issue only with Show details', !(await vis(pg, '#chainview .task .raw')), 'hidden');
  const t2 = (await pg.$$('#chainview .task'))[1];
  if (t2) {
    for (const d of await t2.$$('details')) await d.evaluate(e => { e.open = true; });
    await sleep(200);
    const s2 = ch.arms.kv.sessions[1], tk2 = ch.chain.tasks.find(x => x.order === s2.order) || {};
    const r2 = await t2.evaluate(e => ({ text: e.innerText, tags: [...e.querySelectorAll('.membox .note b.tag')].map(b => b.textContent), pre: e.querySelectorAll('pre:not(.mdcode)').length,
      ids: [...e.querySelectorAll('[title]')].map(x => x.title), md: !!e.querySelector('.issuebox.md'), code: e.querySelectorAll('.issuebox pre.mdcode').length }));
    const tagsWant = /^DID:/m.test(s2.memory_text || '') ? ['DID', 'WHERE', 'VERIFY'] : [];
    check('memory notes as on the Agent page: one row per DID / WHERE / VERIFY …, not raw text', r2.pre === 0 && tagsWant.every(t => r2.tags.includes(t)) && (!s2.memory_text || r2.tags.length > 0), r2.tags.slice(0, 6).join(' '));
    const insts = ch.chain.tasks.map(x => x.instance).filter(Boolean);
    check('no instance id on screen or in a tooltip without Show details', !insts.some(i => r2.text.includes(i) || r2.ids.some(t => t.includes(i))), insts.join(' '));
    const fences = /```/.test(tk2.problem_statement || ''), heads = /^#{1,6} /m.test(tk2.problem_statement || '');
    check('the issue is formatted: no literal ### or ```, its code in a code block', r2.md && !/^#{1,6} |```/m.test(r2.text) && (!fences || r2.code > 0), `headings ${heads} fences ${fences} code blocks ${r2.code}`);
    await t2.screenshot({ path: OUT + '/report_task2_open.png' }).catch(e => errs.push('SHOT ' + e.message));
  }
  const keys = await pg.$$eval('#chainview .barkey', ks => ks.map(k => k.innerText.replace(/\s+/g, ' ').trim()));
  check('one key for the turn bars, above the turns', keys.length === 1 && /agent tokens/.test(keys[0]) && /memory writing/.test(keys[0])
    && await pg.$eval('#chainview .barkey', k => !!k.nextElementSibling && k.nextElementSibling.classList.contains('tasks')), JSON.stringify(keys));
  await pg.click('#gearbtn'); await pg.check('#details'); await pg.keyboard.press('Escape'); await sleep(200);
  const raw = await pg.$$eval('#chainview .task .raw', es => es.map(e => e.innerText));
  if (t2) check('… Show details also shows the memory note\'s turn ids', await t2.$$eval('.membox .note .nl-inst', es => es.some(e => e.offsetParent)).catch(() => false) || !ch.arms.kv.sessions[1].memory_text);
  check('… and Show details shows it, with the instance id', await vis(pg, '#chainview .task .raw') && raw.every((r, i) => r.includes(ch.chain.tasks[i].instance) && r.includes(ch.chain.tasks[i].title)), raw[0]);
  await (await pg.$('#explorer')).screenshot({ path: OUT + '/report_explorer_details.png' }).catch(e => errs.push('SHOT ' + e.message));
  await pg.click('#gearbtn'); await pg.uncheck('#details'); await pg.keyboard.press('Escape'); await sleep(150);
  const goes = await pg.$$eval('#chainview .task a.go', as => as.map(a => [a.textContent.trim(), a.getAttribute('href')]));
  const watchOk = run !== (sc.run || meta.default_run) ? goes.length === 0 : goes.length === heads.length && goes.every(([t, h], i) => t === 'Watch in the Agent view ▸' && h === `./?src=chains&cid=${def}&kind=kv&order=${ch.arms.kv.sessions[i].order}`);
  check('each turn links to the Agent view at that turn', watchOk, JSON.stringify(goes[0] || null));
  check('the task links to Compare once', (await pg.$$('#explorer a#cmp-link')).length === 1 && (await pg.getAttribute('#cmp-link', 'href')) === `compare?cid=${def}` && (await pg.textContent('#cmp-link')).trim() === 'Compare side by side ▸',
    await pg.getAttribute('#cmp-link', 'href'));
  await pg.screenshot({ path: OUT + '/report_1500.png', fullPage: true });

  await pg.click('#t-mem0'); await sleep(600);
  check('the toggle switches to Mem0 and says so in the URL', (await pg.getAttribute('#t-mem0', 'aria-pressed')) === 'true' && new URL(pg.url()).searchParams.get('kind') === 'mem0'
    && /Mem0/.test(await pg.textContent('#chainview .strip .s.sel')), pg.url().replace(BASE, ''));
  const sq = firsts.find(c => /^tobymao_sqlglot/.test(c)) || firsts.find(c => c !== def);
  await pg.selectOption('#cid', sq); await pg.waitForFunction(c => document.querySelector('#chainview .task') && new URL(location).searchParams.get('cid') === c, sq, { timeout: 60000 }); await sleep(400);
  await pg.reload(); await ready(pg);
  check('?cid= and ?kind= survive a reload', (await pg.$eval('#cid', e => e.value)) === sq && (await pg.getAttribute('#t-mem0', 'aria-pressed')) === 'true', pg.url().replace(BASE, ''));
  // a chain outside the example list (pyccel, the old example chain, when it is not listed; else the run's last chain)
  const scIds = new Set((sc.chains || []).map(c => c.cid)), PYC = 'pyccel_pyccel_20240307';
  const away = firsts.includes(PYC) && !scIds.has(PYC) ? PYC : [...firsts].reverse().find(c => !scIds.has(c)) || firsts[firsts.length - 1];
  await pg.goto(BASE + '/report?cid=' + away); await ready(pg);
  check(`a link with ?cid= opens that task, also one outside the example list (${away})`, (await pg.$eval('#cid', e => e.value)) === away && (await pg.getAttribute('#t-kv', 'aria-pressed')) === 'true' && new URL(pg.url()).searchParams.get('cid') === away
    && (await pg.$$('#chainview .task')).length > 0, away);
  await pg.goto(BASE + '/report?cid=nope&kind=bogus'); await ready(pg);
  const q = new URL(pg.url()).searchParams;
  check('an unknown task or method falls back to the defaults, and the URL says so', (await pg.$eval('#cid', e => e.value)) === def && q.get('cid') === def && q.get('kind') === 'kv', pg.url().replace(BASE, ''));
  const bar = await pg.$('#svg-div rect[data-i]');
  if (bar) { await bar.click(); await sleep(1500); const v = await pg.$eval('#cid', e => e.value); check('a bar of the per-task chart opens its task in the explorer', v !== def || new URL(pg.url()).searchParams.get('cid') === v, v); }
  if (runs.length > 1) {
    const h0 = await pg.textContent('#main .hero'); await pg.click('#gearbtn'); await pg.selectOption('#run', runs[1][0]); await pg.waitForFunction(h => document.querySelector('#main .hero') && document.querySelector('#main .hero').textContent !== h, h0, { timeout: 90000 }).catch(() => {});
    check('⚙ Recorded run switches the results', (await pg.textContent('#main .hero')) !== h0, runs[1][1]);
    await pg.keyboard.press('Escape'); await pg.waitForSelector('#chainview .task', { timeout: 90000 }).catch(() => {}); await sleep(500);
    const other = runs[1][0] !== (sc.run || meta.default_run);
    check('a run the Agent view does not play: no Watch links and no promise of them', !other || ((await pg.$$('#chainview a.go')).length === 0 && !/Agent view/.test(await pg.textContent('#explorer .sub'))), (await pg.textContent('#explorer .sub')).slice(-60));
    await pg.reload(); await ready(pg);
    check('… ?run= keeps that run through a reload', (await pg.$eval('#run', e => e.value)) === runs[1][0] && (runs[1][0] === meta.default_run || new URL(pg.url()).searchParams.get('run') === runs[1][0]), pg.url().replace(BASE, ''));
    await pg.click('#gearbtn'); await pg.selectOption('#run', runs[0][0]); await pg.keyboard.press('Escape'); await sleep(1500);
  }
  check('the page never asks for live runs (no /api/live requests)', !liveReqs.length, liveReqs.slice(0, 3).join(' '));

  check('the page background is the Agent page\'s (light)', (await pg.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--plane').trim())) === '#f3f3f0');

  // ---------------------------------------------------------------- phone width
  await pg.goto(BASE + '/report'); await ready(pg);
  await pg.setViewportSize({ width: 420, height: 900 }); await sleep(500);
  const sw = await pg.evaluate(() => document.documentElement.scrollWidth), pn = await box(pg, 'header.top .pnav'), pgb = await box(pg, '#gearbtn');
  check('phone width: no horizontal overflow', sw <= 420, `scrollWidth=${sw}`);
  check('phone width: nav and ⚙ stay together at the right', Math.abs((pn.y + pn.h / 2) - (pgb.y + pgb.h / 2)) < 4 && pgb.x + pgb.w > 380 && pn.x + pn.w <= pgb.x, `nav y=${Math.round(pn.y)} gear y=${Math.round(pgb.y)}`);
  await pg.screenshot({ path: OUT + '/report_420.png' });
  await pg.screenshot({ path: OUT + '/report_420_full.png', fullPage: true });
  await pg.context().close();

  // ---------------------------------------------------------------- dark mode
  const dk = await open({ colorScheme: 'dark' }); await dk.goto(BASE + '/report'); await ready(dk);
  const bg = await dk.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const lum = (bg.match(/\d+/g) || [255, 255, 255]).slice(0, 3).reduce((s, v) => s + +v, 0) / 765;
  check('dark mode: dark background', lum < 0.2, bg);
  check('dark mode: the page background is the Agent page\'s', (await dk.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--plane').trim())) === '#0f0f0e');
  const seg = await dk.evaluate(() => { const w = [...document.querySelectorAll('#chainview .bar .w')].find(e => e.offsetWidth > 0); return w ? [getComputedStyle(w).backgroundColor, getComputedStyle(w.parentElement).backgroundColor] : null; });
  const segCr = seg ? await dk.evaluate(CONTRAST, seg) : 0;
  check('dark mode: the memory-writing part of a turn bar stands out from the track (≥ 1.5:1)', segCr >= 1.5, `${segCr.toFixed(2)} ${seg}`);
  await dk.screenshot({ path: OUT + '/report_dark_1500.png', fullPage: true });
  await dk.setViewportSize({ width: 420, height: 900 }); await sleep(400); await dk.screenshot({ path: OUT + '/report_dark_420.png' });
  await dk.context().close();

  // ---------------------------------------------------------------- failure card
  const f1 = await open({ expectFail: true }); await f1.route('**/api/meta', r => r.abort());
  await f1.goto(BASE + '/report'); await f1.waitForSelector('#main .card.down', { timeout: 30000 }).catch(() => {});
  const ft = (await f1.textContent('#main').catch(() => '')) || '';
  check('server down: the "Couldn\'t reach the demo server" card with Try again, controls disabled', /Couldn't reach the demo server/.test(ft) && !!(await f1.$('#main [data-retry]')) && await f1.$eval('#t-kv', e => e.disabled), ft.replace(/\s+/g, ' ').slice(0, 90));
  await f1.screenshot({ path: OUT + '/report_down.png' }); await f1.context().close();
  const f2 = await open({ expectFail: true }); await f2.route('**/api/summary**', r => r.fulfill({ status: 502, contentType: 'text/html', body: '<html><body>502 Bad Gateway</body></html>' }));
  await f2.goto(BASE + '/report'); await f2.waitForSelector('#main .card.down', { timeout: 30000 }).catch(() => {});
  const f2t = (await f2.textContent('#main').catch(() => '')) || '';
  check('a proxy error page: a plain message, not a parse error', /Couldn't reach the demo server/.test(f2t) && !/Unexpected token|JSON/.test(f2t), f2t.replace(/\s+/g, ' ').slice(0, 90));
  await f2.context().close();

  console.log('screenshots:', OUT);
  console.log('errors:', errs.length ? errs.join('\n') : 'none');
  if (errs.some(e => e.startsWith('PAGEERROR'))) check('no page errors', false, errs.filter(e => e.startsWith('PAGEERROR')).join(' | '));
  const failed = results.filter(r => r[0] === 'FAIL').length; console.log('SUMMARY', failed, 'failed of', results.length); await b.close(); process.exit(failed ? 1 : 0);
})().catch(e => { console.error('FAILED', e); process.exit(1); });
