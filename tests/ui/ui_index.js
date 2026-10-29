// Thorough pass over the agent view (recorded sessions, playback, panes, composer, sandbox tab, filters, selectors).
const { chromium } = require('playwright'); const BASE = process.env.BASE || 'http://127.0.0.1:8799'; const OUT = process.env.SHOTS || require('path').join(__dirname, 'shots'); require('fs').mkdirSync(OUT, { recursive: true });
const sleep = ms => new Promise(r => setTimeout(r, ms)); const results = []; const check = (name, ok, info = '') => { results.push([ok ? 'PASS' : 'FAIL', name, info]); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${info ? ' — ' + info : ''}`); };
(async () => {
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1500, height: 950 } }); const pg = await ctx.newPage(); const errs = [];
  pg.on('pageerror', e => errs.push('PAGEERROR ' + e.message)); pg.on('response', r => { if (r.status() >= 400 && !/api\/file/.test(r.url())) errs.push('HTTP ' + r.status() + ' ' + r.url().replace(BASE, '')); });
  const T = async sel => (await pg.textContent(sel) || '').replace(/\s+/g, ' ').trim();
  await pg.goto(BASE + '/'); await pg.waitForSelector('#taskpin:not([hidden]) .tp-head', { timeout: 30000 });
  await pg.waitForFunction(() => !/checking/.test(document.querySelector('#epstatus').textContent), null, { timeout: 30000 });
  // ---- boot state
  check('boot: showcase chain opened', /Task 1 of 3/.test(await T('#chathead')), await T('#chathead'));
  check('boot: transcript has turns', (await pg.$$('#transcript .turn')).length > 5);
  check('boot: savings chip', /%/.test(await T('#savings')), await T('#savings'));
  check('boot: run selector groups', (await pg.$$eval('#run optgroup', e => e.length)) >= 1);
  // ---- pinned task
  await pg.click('#taskpin .tp-head'); check('pin expands', await pg.isVisible('#taskpin .tp-body')); await pg.click('#taskpin .tp-head'); check('pin collapses', !(await pg.isVisible('#taskpin .tp-body')));
  // ---- playback
  await pg.click('#pb-play'); await sleep(1500); const hiddenTurns = await pg.$$eval('#transcript .turn[hidden]', e => e.length); check('play hides later turns and reveals progressively', hiddenTurns > 0 && /Pause/.test(await T('#pb-play')), `hidden=${hiddenTurns}`);
  await pg.click('#pb-play'); await sleep(300); check('pause', /Resume/.test(await T('#pb-play')), await T('#pb-play'));
  const before = await pg.$$eval('#transcript .turn:not([hidden])', e => e.length); await pg.click('#pb-step'); await sleep(200); const after = await pg.$$eval('#transcript .turn:not([hidden])', e => e.length); check('step reveals one more turn', after === before + 1, `${before}->${after}`);
  await pg.click('#pb-end'); await sleep(300); check('show all reveals everything', (await pg.$$eval('#transcript .turn[hidden]', e => e.length)) === 0);
  await pg.click('#pb-restart'); await sleep(300); check('restart hides turns again', (await pg.$$eval('#transcript .turn[hidden]', e => e.length)) > 0);
  await pg.selectOption('#pb-speed', '10'); await pg.click('#pb-play'); await sleep(200); check('speed select applies', /10×/.test(await T('#pb-status')), await T('#pb-status')); await pg.click('#pb-play'); await pg.click('#pb-end');
  await pg.keyboard.press('Space'); await sleep(300); check('space toggles play', /Pause/.test(await T('#pb-play'))); await pg.keyboard.press('Space'); await pg.click('#pb-end');
  // ---- right panes
  for (const t of ['changes', 'files', 'memory', 'issue', 'ledger']) { await pg.click(`#tabs button[data-t="${t}"]`); await sleep(250); const txt = await T('#pane'); check(`pane ${t} renders`, txt.length > 20, txt.slice(0, 60)); }
  await pg.click('#tabs button[data-t="files"]'); await sleep(200); const f = await pg.$('#pane .file'); if (f) { await f.click(); await sleep(1500); check('file view opens (image or excerpt)', /←/.test(await T('#pane')), (await T('#pane')).slice(0, 80)); await pg.click('#pane #back'); }
  await pg.click('#tabs button[data-t="changes"]'); await sleep(200); const dh = await pg.$('#pane .diff-head'); check('changes tab lists diffs', !!dh);
  // ---- left list: sessions, chains, filter, arms
  await pg.click('#chains .chain[data-cid] + .sessions .sess[data-order="2"]').catch(() => {}); await sleep(800); check('clicking session 2 opens it', /Task 2/.test(await T('#chathead')), await T('#chathead'));
  await pg.fill('#search', 'tablib'); await sleep(300); const n = await pg.$$eval('#chains .chain[data-cid]', e => e.length); check('filter narrows chain list', n >= 1 && n < 10, `n=${n}`); await pg.fill('#search', ''); await sleep(300);
  await pg.click('#armseg button[data-k="mem0"]'); await sleep(1000); check('arm switch to Mem0 opens its session', /Mem0/.test(await T('#chathead')), await T('#chathead'));
  await pg.click('#armseg button[data-k="none"]'); await sleep(1000); check('arm switch to none', /No memory/.test(await T('#chathead')), await T('#chathead'));
  await pg.click('#armseg button[data-k="kv"]'); await sleep(1000);
  const chains = await pg.$$('#chains .chain[data-cid]'); await chains[3].click(); await sleep(1200); check('clicking another chain opens its first session', (await pg.$$('#transcript .turn')).length > 0, await T('#chathead'));
  // ---- price row
  const usd0 = await T('#savings'); await pg.selectOption('#price', { index: 1 }); await pg.waitForFunction(() => !/Loading/.test(document.querySelector('#chains').textContent), null, { timeout: 20000 }); await sleep(400); check('price row change updates savings chip', (await T('#savings')) !== usd0, await T('#savings'));
  // ---- composer modes
  await pg.selectOption('#cmode', 'chat'); check('chat mode placeholder', /Chat/.test(await pg.getAttribute('#q', 'placeholder')));
  await pg.selectOption('#cmode', 'task'); check('task mode shows arms', await pg.isVisible('#taskarms') && /Run/.test(await T('#send')));
  await pg.fill('#q', 'hello'); await pg.evaluate(() => { document.querySelector('#send').disabled = false; }); await pg.click('#send'); await sleep(500); check('task run outside sandbox is refused with a toast', /sandbox project/.test(await T('#toast')), await T('#toast'));
  await pg.selectOption('#cmode', 'ask'); await pg.fill('#q', '');
  // ---- sandbox tab
  await pg.click('#srcseg button[data-src="custom"]'); await sleep(1500); check('sandbox tab shows projects or empty hint', /project/i.test(await T('#chains')) || /project/i.test(await T('#leftfoot')), (await T('#chains')).slice(0, 80));
  check('sandbox: run-live button hidden', !(await pg.isVisible('#runlive')));
  check('sandbox: composer in task mode', (await pg.$eval('#cmode', e => e.value)) === 'task');
  const proj = await pg.$('#chains .chain[data-proj]'); if (proj) { await proj.click(); await sleep(1200); check('sandbox project opens', (await T('#chathead')).length > 0, await T('#chathead')); }
  await pg.click('#srcseg button[data-src="chains"]'); await pg.waitForFunction(() => document.querySelectorAll('#chains .chain[data-cid]').length > 10, null, { timeout: 20000 }).catch(() => {}); await sleep(300); check('back to chains restores run', (await pg.$eval('#run', e => e.value)) !== 'custom' && (await pg.$$('#chains .chain[data-cid]')).length > 10);
  // ---- run selector to a live run and back
  const opts = await pg.$$eval('#run option', e => e.map(o => o.value)); const lr = opts.find(o => /^live_|demo_live/.test(o)); if (lr) { await pg.selectOption('#run', lr); await sleep(1500); check('selecting a saved live run lists its chains', (await pg.$$('#chains .chain[data-cid]')).length >= 1 && /selected live run/.test((await pg.$$eval('#chains .lsec', e => e.map(x => x.textContent))).pop() || ''), (await pg.$$eval('#chains .lsec', e => e.map(x => x.textContent.trim()))).join(' | ')); await pg.selectOption('#run', 'v3main100'); await sleep(1500); }
  // ---- saved live runs list entries open
  const sv = await pg.$('#chains .chain[data-open]'); if (sv) { await sv.click(); await sleep(1500); check('saved live run entry opens', /^live_|demo_live/.test(await pg.$eval('#run', e => e.value)), await pg.$eval('#run', e => e.value) + ' / ' + await T('#chathead')); await pg.selectOption('#run', 'v3main100'); await sleep(1500); }
  // ---- compare & report links
  check('page nav links', (await pg.$$eval('.pnav a', e => e.map(a => a.getAttribute('href')))).join(',') === './,compare,report' && (await pg.getAttribute('.pnav a[aria-current="page"]', 'href')) === './');
  // ---- narrow viewport
  await pg.setViewportSize({ width: 420, height: 900 }); await sleep(500); const sw = await pg.evaluate(() => document.documentElement.scrollWidth); check('phone width: no horizontal overflow', sw <= 430, `scrollWidth=${sw}`); await pg.screenshot({ path: OUT + '/20_index_phone.png', fullPage: false });
  await pg.setViewportSize({ width: 1500, height: 950 });
  // ---- dark theme render
  await pg.emulateMedia({ colorScheme: 'dark' }); await sleep(300); await pg.screenshot({ path: OUT + '/21_index_dark.png' }); const bg = await pg.evaluate(() => getComputedStyle(document.body).backgroundColor); check('dark scheme applies', !/255, 255, 255|243, 243, 240/.test(bg), bg); await pg.emulateMedia({ colorScheme: 'light' });
  console.log('errors:', errs.length ? errs.join('\n') : 'none'); console.log('SUMMARY', results.filter(r => r[0] === 'FAIL').length, 'failed of', results.length);
  await b.close();
})().catch(e => { console.error('FAILED', e); process.exit(1); });
