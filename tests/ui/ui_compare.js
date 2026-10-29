const { chromium } = require('playwright'); const BASE = process.env.BASE || 'http://127.0.0.1:8799'; const OUT = process.env.SHOTS || require('path').join(__dirname, 'shots'); require('fs').mkdirSync(OUT, { recursive: true });
const sleep = ms => new Promise(r => setTimeout(r, ms)); const results = []; const check = (name, ok, info = '') => { results.push([ok ? 'PASS' : 'FAIL', name, info]); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${info ? ' — ' + info : ''}`); };
(async () => {
  const b = await chromium.launch(); const pg = await b.newPage({ viewport: { width: 1500, height: 950 } }); const errs = [];
  pg.on('pageerror', e => errs.push('PAGEERROR ' + e.message)); pg.on('response', r => { if (r.status() >= 400 && !/api\/file/.test(r.url())) errs.push('HTTP ' + r.status() + ' ' + r.url().replace(BASE, '')); });
  const T = async sel => (await pg.textContent(sel) || '').replace(/\s+/g, ' ').trim();
  await pg.goto(BASE + '/compare'); await pg.waitForSelector('#pinA:not([hidden]) .tp-head', { timeout: 30000 }); await sleep(800);
  check('boot: showcase 3 columns', (await pg.$$('#cols .col')).length === 3 && (await pg.$eval('#ncols', e => e.value)) === '3');
  check('boot: chain selector set', /pyccel/.test(await pg.$eval('#cid', e => e.value)));
  check('boot: strips show totals', /\$/.test(await T('#ctrA')) && /steps/.test(await T('#ctrA')), (await T('#ctrA')).slice(0, 80));
  check('boot: footer totals table', /whole chain/.test(await T('#sum')));
  check('boot: transcripts loaded with turns hidden (ready to play)', (await pg.$$('#trA .turn')).length > 0 && (await pg.$$('#trA .turn[hidden]')).length > 0);
  // play all / pause / step / show all / restart
  await pg.click('#play'); await sleep(2500); check('play all: counters tick', /step [1-9]/.test(await T('#taskA')), await T('#taskA')); check('play all: button toggles', /Pause/.test(await T('#play')));
  await pg.click('#play'); await sleep(400); check('pause all', /Play/.test(await T('#play')));
  const s0 = await T('#taskB'); await pg.click('#step'); await sleep(300); check('step all advances', (await T('#taskB')) !== s0, `${s0} -> ${await T('#taskB')}`);
  await pg.click('#end'); await sleep(800); check('show all: task result chips', /resolved/.test(await T('#resA')) || /not resolved/.test(await T('#resA')), await T('#resA'));
  check('show all: footer hero diff', /%/.test(await T('#sum .hero')) , await T('#sum .hero'));
  await pg.click('#restart'); await sleep(1500); check('restart resets counters', /step 0\//.test(await T('#taskA')), await T('#taskA'));
  // jump via dots / task labels
  const dot = (await pg.$$('#ctrA .d2'))[5]; if (dot) { await dot.click(); await sleep(400); check('jump to a step via dot', /step [5-6]\//.test(await T('#taskA')), await T('#taskA')); }
  const tl = await pg.$('#ctrA .trow:nth-child(3) .tl'); if (tl) { await tl.click(); await sleep(1500); check('jump to task 2 via label', /Task 2/.test(await T('#taskA')), await T('#taskA')); }
  // speed, auto, columns, pricing, chain change
  await pg.selectOption('#speed', '10'); await pg.click('#play'); await sleep(3000); await pg.click('#play'); check('10x speed advances fast', /step (\d+)\//.test(await T('#taskA')) && +(await T('#taskA')).match(/step (\d+)\//)[1] > 5, await T('#taskA'));
  await pg.selectOption('#ncols', '2'); await sleep(1500); check('2 columns', (await pg.$$('#cols .col')).length === 2);
  await pg.selectOption('#pricing', 'global'); await sleep(1500); check('global pricing reloads', /\$/.test(await T('#ctrA')));
  await pg.selectOption('#price', { index: 1 }); await sleep(1500); check('price row change ok', /\$/.test(await T('#ctrA')));
  const cids = await pg.$$eval('#cid option', e => e.map(o => o.value)); if (cids.length > 1) { await pg.selectOption('#cid', cids[1]); await sleep(2500); check('chain change reloads pins', (await T('#pinA .tp-head')).length > 10 && !(await pg.$eval('#pinA', e => e.hidden)), await T('#pinA .tp-head')); }
  // config selectors: pick a config without sessions -> empty hint + pin hidden
  await pg.selectOption('#selA', 'kv_local'); await sleep(2000); check('config without run: empty hint', /No .* run for this chain|Pick a configuration|Run all columns live/.test(await T('#trA')), (await T('#trA')).slice(0, 80)); check('config without run: pin hidden', await pg.$eval('#pinA', e => e.hidden));
  await pg.selectOption('#selA', '__custom'); await sleep(500); check('custom config form opens', await pg.isVisible('#ccA'));
  await pg.selectOption('#selA', 'kv_v3'); await sleep(2000); check('back to recorded config', (await pg.$$('#trA .turn')).length > 0);
  // deep link
  await pg.goto(BASE + '/compare?cid=pyccel_pyccel_20240307&a=kv_v3&b=none_v3'); await pg.waitForSelector('#pinA:not([hidden]) .tp-head', { timeout: 30000 }); await sleep(500);
  check('deep link: 2 columns, right configs', (await pg.$$('#cols .col')).length === 2 && (await pg.$eval('#selA', e => e.value)) === 'kv_v3' && (await pg.$eval('#selB', e => e.value)) === 'none_v3');
  await pg.setViewportSize({ width: 420, height: 900 }); await sleep(500); const sw = await pg.evaluate(() => document.documentElement.scrollWidth); check('phone width: no horizontal overflow', sw <= 430, `scrollWidth=${sw}`); await pg.screenshot({ path: OUT + '/22_compare_phone.png' });
  await pg.setViewportSize({ width: 1500, height: 950 }); await pg.emulateMedia({ colorScheme: 'dark' }); await sleep(300); await pg.screenshot({ path: OUT + '/23_compare_dark.png' });
  console.log('errors:', errs.length ? errs.join('\n') : 'none'); console.log('SUMMARY', results.filter(r => r[0] === 'FAIL').length, 'failed of', results.length); await b.close();
})().catch(e => { console.error('FAILED', e); process.exit(1); });
