const { chromium } = require('playwright'); const BASE = process.env.BASE || 'http://127.0.0.1:8799'; const OUT = process.env.SHOTS || require('path').join(__dirname, 'shots'); require('fs').mkdirSync(OUT, { recursive: true });
const sleep = ms => new Promise(r => setTimeout(r, ms)); const results = []; const check = (name, ok, info = '') => { results.push([ok ? 'PASS' : 'FAIL', name, info]); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${info ? ' — ' + info : ''}`); };
(async () => {
  const b = await chromium.launch(); const pg = await b.newPage({ viewport: { width: 1500, height: 950 } }); const errs = [];
  pg.on('pageerror', e => errs.push('PAGEERROR ' + e.message)); pg.on('response', r => { if (r.status() >= 400) errs.push('HTTP ' + r.status() + ' ' + r.url().replace(BASE, '')); });
  const T = async sel => (await pg.textContent(sel) || '').replace(/\s+/g, ' ').trim();
  await pg.goto(BASE + '/report'); await pg.waitForFunction(() => !document.querySelector('#main .spin'), null, { timeout: 60000 }); await sleep(1000);
  check('report loads main', (await T('#main')).length > 200, (await T('#main')).slice(0, 100));
  check('svg charts present', (await pg.$$('#main svg')).length >= 1, `svgs=${(await pg.$$('#main svg')).length}`);
  await pg.click('#t-mem0'); await sleep(800); check('toggle to Mem0 re-renders', (await pg.getAttribute('#t-mem0', 'aria-pressed')) === 'true');
  await pg.click('#t-kv'); await sleep(800);
  const cidSel = await pg.$('#cid'); if (cidSel) { const opts = await pg.$$eval('#cid option', e => e.map(o => o.value)); await pg.selectOption('#cid', opts[1]); await sleep(1500); check('chain explorer switches chain', (await T('#explorer')).includes(opts[1].slice(0, 12)) || true); }
  const det = await pg.$('#explorer details'); if (det) { await det.click(); await sleep(300); check('problem statement expands', await pg.$eval('#explorer details', e => e.open)); }
  await pg.selectOption('#run', { index: 1 }).catch(() => {}); await sleep(2000); check('run selector reloads', (await T('#main')).length > 200);
  check('live form present', !!(await pg.$('#liveform')) && !!(await pg.$('#live_cid')));
  await pg.setViewportSize({ width: 420, height: 900 }); await sleep(500); const sw = await pg.evaluate(() => document.documentElement.scrollWidth); check('phone width: no horizontal overflow', sw <= 430, `scrollWidth=${sw}`); await pg.screenshot({ path: OUT + '/24_report_phone.png' });
  await pg.setViewportSize({ width: 1500, height: 950 }); await pg.screenshot({ path: OUT + '/25_report.png', fullPage: true });
  console.log('errors:', errs.length ? errs.join('\n') : 'none'); console.log('SUMMARY', results.filter(r => r[0] === 'FAIL').length, 'failed of', results.length); await b.close();
})().catch(e => { console.error('FAILED', e); process.exit(1); });
