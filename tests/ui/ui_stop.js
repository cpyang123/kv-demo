// Stop buttons for live runs: compare (the bar's ■ Stop, which replaces ▶ Run live while runs go, and each running column's own ■ Stop)
// and the agent page (playback bar + banner). Needs replay_server.py (fake live runs that honour stop). Both stop buttons need a second
// click to confirm. Its runs use the model <PREFIX>stop (PREFIX default "ui-"), so they never collide (409) with other suites' runs.
// One compare run goes through a slow server (GET /api/meta delayed 1.5 s): a stopped run still shows what it saved (<PREFIX>stop-slow).
const { chromium } = require('playwright'); const BASE = process.env.BASE || 'http://127.0.0.1:8799';
const PREFIX = process.env.PREFIX || 'ui-', MODEL = `${PREFIX}stop`;
if (/:876[56]\b/.test(BASE)) { console.log('ui_stop.js starts live runs: replay servers only'); process.exit(1); }
const sleep = ms => new Promise(r => setTimeout(r, ms)); const results = []; const check = (name, ok, info = '') => { results.push([ok ? 'PASS' : 'FAIL', name, info]); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${info ? ' — ' + info : ''}`); };
const until = async (fn, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { if (await fn().catch(() => false)) return true; await sleep(300); } return false; };
(async () => {
  const b = await chromium.launch(); const pg = await b.newPage({ viewport: { width: 1500, height: 950 } }); const errs = [];
  pg.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  const T = async sel => ((await pg.textContent(sel).catch(() => '')) || '').replace(/\s+/g, ' ').trim();
  const jobs = async () => (await (await pg.request.get(BASE + '/api/live')).json());
  const shots = process.env.SHOTS || __dirname + '/shots';

  // ---- compare page: columns = memory methods; an endpoint model in the headers (?api=&model=) so the columns lack data, then
  //      the bar's ▶ Run live / ■ Stop (stop-all) and a column's own ■ Stop
  const getJ = async u => (await (await pg.request.get(BASE + '/' + u)).json());
  const meta = await getJ('api/meta'), live = Array.isArray(meta.live_kinds) && meta.live_kinds.length ? meta.live_kinds : ['kv', 'mem0', 'none'];
  const cols = ['kv', 'mem0', 'memop', 'none'].filter(k => live.includes(k));            // every method that runs live (MemOp where the server runs it)
  const prov = (await getJ('api/providers')).find(p => p.ok);
  const cfgRun = (p, m) => 'cfg__' + `${p}__${m}`.replace(/[^A-Za-z0-9.]+/g, '-').replace(/^-|-$/g, '');
  /** chains (showcase first, then the other chains with an image) where the model has no session yet */
  const freeChains = async (model = MODEL) => {
    const rc = await getJ(`api/runchains?run=${encodeURIComponent(cfgRun(prov.name, model))}&kinds=${live.join(',')}`).catch(() => ({ chains: [] }));
    const has = new Set((rc.chains || []).filter(c => Object.values(c.arms || {}).some(a => a.sessions.length)).map(c => c.cid));
    const sc = (await getJ('api/showcase')).chains.filter(c => c.sif).map(c => c.cid), mc = meta.chains.filter(c => c.sif && !c.custom).map(c => c.cid);
    return [...new Set([...sc, ...mc])].filter(c => !has.has(c));
  };
  const openEmpty = async cid => {
    await pg.goto(BASE + `/compare?cols=${cols.join(',')}&cid=${cid}&api=${encodeURIComponent(prov.name)}&model=${encodeURIComponent(MODEL)}`);
    await until(async () => !!(await pg.$('#tr-kv [data-runcol]')) && !(await pg.$eval('#runlive', e => e.hidden || e.disabled)), 20000);
  };
  const mine = (js, ids) => js.filter(j => ids.includes(j.id));
  let free = prov ? await freeChains() : [];
  if (!prov || free.length < 2) check('compare: an endpoint and two tasks without runs of the model', false, `provider ${prov && prov.name}, ${free.length} free tasks`);
  else {
    await openEmpty(free[0]);
    check('compare: columns lack data, ▶ Run live shows, ■ Stop hidden before any run', /No Knowledge Vault run with/.test(await T('#tr-kv')) && !(await pg.$eval('#runlive', e => e.hidden)) && await pg.$eval('#stoplive', e => e.hidden), await T('#tr-kv'));
    const before = new Set((await jobs()).map(j => j.id));
    await pg.click('#runlive');
    check('compare: ■ Stop appears once runs start, in place of ▶ Run live', await until(async () => !(await pg.$eval('#stoplive', e => e.hidden)) && await pg.$eval('#runlive', e => e.hidden), 8000));
    const ids = (await jobs()).filter(j => !before.has(j.id)).map(j => j.id);
    check(`compare: the bar runs every column lacking data (${cols.join(', ')})`, ids.length === cols.length, `${ids.length} new jobs`);
    check('compare: each running column shows its own stop', await until(async () => (await pg.$$eval('.colstop', e => e.filter(x => !x.hidden).length)) === cols.length, 8000));
    await pg.click('#stoplive'); await sleep(200);
    check('compare: first click only arms', /click again/.test(await T('#stoplive')) && mine(await jobs(), ids).every(j => j.status === 'running' || j.status === 'done'), await T('#stoplive'));
    await sleep(6300); check('compare: arming times out', /^■ Stop$/.test(await T('#stoplive')), await T('#stoplive'));
    await pg.click('#stoplive'); await sleep(200); await pg.click('#stoplive');
    check('compare: runs end as stopped', await until(async () => mine(await jobs(), ids).every(j => j.status === 'stopped'), 15000), mine(await jobs(), ids).map(j => j.status).join(','));
    check('compare: status line says stopped, buttons hide', await until(async () => /stopped/.test(await T('#livest')) && await pg.$eval('#stoplive', e => e.hidden) && (await pg.$$eval('.colstop', e => e.every(x => x.hidden))), 15000), await T('#livest'));
    check('compare: each column\'s turn row says "Stopped · N of 3 turns graded"', (await Promise.all(cols.map(k => T(`#task-${k}`)))).every(t => /^Stopped · \d of \d turns? graded$/.test(t)), (await Promise.all(cols.map(k => T(`#task-${k}`)))).join(' | '));
    await pg.screenshot({ path: shots + '/40_compare_stopped.png' });

    // ---- compare page: one column's empty-state ▶ Run live and its own ■ Stop
    await openEmpty(free[1]);
    const b2 = new Set((await jobs()).map(j => j.id));
    await pg.click('#tr-kv [data-runcol="kv"]');
    check('column: only that column starts', await until(async () => (await jobs()).filter(j => !b2.has(j.id)).length === 1, 8000), `${(await jobs()).filter(j => !b2.has(j.id)).length} new`);
    const jid = (await jobs()).find(j => !b2.has(j.id))?.id;
    check('column: its stop shows in its turn row', await until(async () => !(await pg.$eval('#stop-kv', e => e.hidden)), 8000));
    check('column: the stop-all button shows too', !(await pg.$eval('#stoplive', e => e.hidden)));
    check('column: other columns still offer Run live', !!(await pg.$('#tr-mem0 [data-runcol="mem0"]')));
    await pg.click('#stop-kv'); await sleep(200); check('column: stop arms first', /click again/.test(await T('#stop-kv')), await T('#stop-kv'));
    await pg.click('#stop-kv');
    check('column: run ends as stopped', await until(async () => (await jobs()).find(j => j.id === jid)?.status === 'stopped', 15000), (await jobs()).find(j => j.id === jid)?.status);
    check('column: stop hides again', await until(async () => await pg.$eval('#stop-kv', e => e.hidden) && await pg.$eval('#stoplive', e => e.hidden), 15000));
  }

  // ---- compare page on a slow server (GET /api/meta answers after 1.5 s, as over a tunnel): a Mem0-only run stopped during task 1
  //      still ends with its saved totals and "run again" (never "nothing was saved" next to a graded task), the task row on the task that ran
  if (prov) {
    // pyccel (no longer an example chain, its recorded runs still there) when free: its task lengths suit "stopped during task 1"
    const SLOW = `${MODEL}-slow`, fc = await freeChains(SLOW), cid = fc.includes('pyccel_pyccel_20240307') ? 'pyccel_pyccel_20240307' : fc[0];
    if (!cid) check('slow server: a task without runs of the model', false, SLOW);
    else {
      const sp = await b.newPage({ viewport: { width: 1500, height: 950 } }); sp.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
      await sp.route(/\/api\/meta/, async r => { if (r.request().method() === 'GET') await sleep(1500); r.continue(); });
      const ST = async sel => ((await sp.textContent(sel).catch(() => '')) || '').replace(/\s+/g, ' ').trim();
      await sp.goto(BASE + `/compare?cols=kv,mem0,none&cid=${cid}&api=${encodeURIComponent(prov.name)}&model=${encodeURIComponent(SLOW)}`);
      await until(async () => !!(await sp.$('#tr-mem0 [data-runcol="mem0"]')), 30000);
      const b3 = new Set((await jobs()).map(j => j.id));
      await sp.click('#tr-mem0 [data-runcol="mem0"]'); await sleep(9000);
      const sid = (await jobs()).find(j => !b3.has(j.id))?.id;
      await sp.click('#stop-mem0'); await sleep(200); await sp.click('#stop-mem0');
      await until(async () => (await jobs()).find(j => j.id === sid)?.status !== 'running', 30000);
      const t1 = (await getJ(`api/chain/${cid}?run=__live__`)).chain.tasks.find(t => t.order === 1) || {};
      const name1 = String(t1.summary || '').trim() || String(t1.title || '').split('\n')[0].replace(/^#+\s*/, '').replace(/\*\*|__|`/g, '').trim();
      const ok = await until(async () => /^Stopped · 1 of \d turns? graded$/.test(await ST('#task-mem0')) && /[01]\/1 resolved/.test(await ST('#ctr-mem0')) && /only 1 of \d turns ran · run again/.test(await ST('#ctr-mem0')), 20000);
      check('slow server: a stopped Mem0 run shows its saved totals and "only 1 of N turns ran · run again"', ok && !/nothing was saved/.test(await ST('#ctr-mem0')), `${await ST('#task-mem0')} | ${await ST('#ctr-mem0')}`);
      check('slow server: … its turn row names turn 1 (the turn that ran)', (await ST('#ttl-mem0')) === name1, `${await ST('#ttl-mem0')} vs ${name1}`);
      check('slow server: … and "run again" is wholly visible', await sp.$eval('#ctr-mem0 [data-again]', a => { const r = a.getBoundingClientRect(), c = a.closest('.col').getBoundingClientRect(); return r.width > 0 && r.left >= c.left && r.right <= c.right + 0.5 && !a.closest('.stat'); }).catch(() => false));
      await sp.screenshot({ path: shots + '/42_compare_slow_stopped.png' });
      await sp.close();
    }
  }

  // ---- agent page: banner stop for a run started elsewhere, then the playback-bar stop while watching. The banner names runs of the
  //      chain on screen: the run goes on the example chain the Agent page opens on (api/showcase default_cid, else api/meta's)
  const ACID = (await getJ('api/showcase').catch(() => ({}))).default_cid || meta.default_cid;
  console.log(`INFO agent page task ${ACID}`);
  const start = async () => { const r = await pg.request.post(BASE + '/api/live/start', { data: { cid: ACID, kind: 'kv', provider: prov ? prov.name : 'local', model: MODEL } }); const j = await r.json(); if (!r.ok()) throw new Error(`live/start ${r.status()}: ${j.detail}`); return j; };
  let j = await start(); await pg.goto(BASE + `/?src=chains&cid=${encodeURIComponent(ACID)}`);
  check('agent: banner offers stop', await until(async () => !!(await pg.$('#bannerstop')), 15000));
  await pg.click('#bannerstop'); await sleep(4500);                                   // a poll (4 s) happens while armed
  check('agent: armed banner button survives a poll', /click again/.test(await T('#bannerstop')), await T('#bannerstop'));
  await pg.click('#bannerstop');
  check('agent: banner stop ends the run', await until(async () => (await jobs()).find(x => x.id === j.id).status === 'stopped', 15000));
  j = await start(); await pg.goto(BASE + `/?src=chains&cid=${encodeURIComponent(ACID)}`);
  await until(async () => !!(await pg.$('#gotolive')), 15000); await pg.click('#gotolive');
  check('agent: playback bar shows stop while watching', await until(() => pg.isVisible('#pb-stop'), 10000));
  await pg.click('#pb-stop'); await sleep(200); await pg.click('#pb-stop');
  check('agent: playback stop ends the run', await until(async () => (await jobs()).find(x => x.id === j.id).status === 'stopped', 15000));
  await pg.screenshot({ path: shots + '/41_agent_stopped.png' });

  console.log('errors:', errs.length ? errs.join('\n') : 'none'); console.log('SUMMARY', results.filter(r => r[0] === 'FAIL').length, 'failed of', results.length); await b.close(); process.exit(results.some(r => r[0] === 'FAIL') || errs.length ? 1 : 0);
})().catch(e => { console.error('FAILED', e); process.exit(1); });
