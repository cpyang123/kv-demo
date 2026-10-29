// End-to-end: create a sandbox project, run a task through the REAL harness (custom_runner + Apptainer + canned model), watch it live.
const { chromium } = require('playwright'); const BASE = process.env.BASE || 'http://127.0.0.1:8799'; const OUT = process.env.SHOTS || require('path').join(__dirname, 'shots'); require('fs').mkdirSync(OUT, { recursive: true });
const sleep = ms => new Promise(r => setTimeout(r, ms)); const results = []; const check = (name, ok, info = '') => { results.push([ok ? 'PASS' : 'FAIL', name, info]); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${info ? ' — ' + info : ''}`); };
(async () => {
  const b = await chromium.launch(); const pg = await b.newPage({ viewport: { width: 1500, height: 950 } }); const errs = [];
  pg.on('pageerror', e => errs.push('PAGEERROR ' + e.message)); pg.on('response', r => { if (r.status() >= 400 && !/api\/file/.test(r.url())) errs.push('HTTP ' + r.status() + ' ' + r.url().replace(BASE, '')); });
  const T = async sel => (await pg.textContent(sel) || '').replace(/\s+/g, ' ').trim();
  await pg.goto(BASE + '/?src=custom'); await pg.waitForFunction(() => !/checking/.test(document.querySelector('#epstatus').textContent), null, { timeout: 30000 }); await sleep(1000);
  check('endpoint ok (canned model)', /models|coder-next|8858/.test(await T('#epstatus')) && !(await pg.$eval('#send', e => e.disabled)), await T('#epstatus'));
  check('sandbox images listed', (await pg.$$eval('#np-sandbox option', e => e.map(o => o.value))).includes('3.12'), (await pg.$$eval('#np-sandbox option', e => e.map(o => o.value))).join(','));
  const name = 'ui-smoke-' + Date.now().toString().slice(-5);
  await pg.fill('#np-name', name); await pg.selectOption('#np-sandbox', '3.12'); await pg.click('#np-create'); await sleep(1500);
  check('project created and selected', (await T('#chathead')).includes(name), await T('#chathead'));
  await pg.fill('#q', 'Add a NOTES.md file with a short description of this workspace.');
  await pg.$eval('#taskarms input[data-k="mem0"]', e => { e.checked = false; }); await pg.$eval('#taskarms input[data-k="none"]', e => { e.checked = true; });   // KV + none
  await pg.fill('#tasksteps', '6'); await pg.click('#send'); await sleep(2500);
  check('task started: live view', await pg.$eval('#playbar', e => e.classList.contains('live')), await T('#toast'));
  check('live chip in project list', (await pg.$$('#chains .live-chip')).length >= 1, await T('#chains'));
  await pg.screenshot({ path: OUT + '/30_sandbox_started.png' });
  // watch the first steps
  await pg.waitForFunction(() => document.querySelectorAll('#transcript [data-lf="turns"] .turn, #transcript [data-lf="pending"] .turn').length >= 1, null, { timeout: 180000 }).catch(() => {});
  const t1 = await pg.$$eval('#transcript .turn', e => e.length); check('steps stream in', t1 >= 1, `turns=${t1} | ${await T('#pb-livetxt')}`); await pg.screenshot({ path: OUT + '/31_sandbox_steps.png' });
  await pg.waitForFunction(() => !document.querySelector('#playbar').classList.contains('live'), null, { timeout: 300000 }); await sleep(2500);
  check('run finished and session opened', /Task 1/.test(await T('#chathead')), await T('#chathead'));
  const tr = await T('#transcript'); check('transcript shows the 3 canned steps + submission', (await pg.$$('#transcript .turn')).length === 3 && /Submitted|Session ended/.test(tr), `turns=${(await pg.$$('#transcript .turn')).length}`);
  check('changes tab has NOTES.md', /NOTES\.md/.test(await T('#pane')) || (await (async () => { await pg.click('#tabs button[data-t="changes"]'); await sleep(300); return /NOTES\.md/.test(await T('#pane')); })()), (await T('#pane')).slice(0, 80));
  await pg.click('#tabs button[data-t="memory"]'); await sleep(300); check('memory tab shows what was written (KV)', /Written|Nothing written|DID/.test(await T('#pane')), (await T('#pane')).slice(0, 100));
  await pg.click('#tabs button[data-t="ledger"]'); await sleep(300); check('ledger has rows', /actor/.test(await T('#pane')), (await T('#pane')).slice(0, 80));
  const chips = await pg.$$eval('#chains .chip.tiny', e => e.map(x => x.textContent.trim())); check('project list shows the task with per-system chips', chips.length >= 2, chips.join(' | '));
  await pg.screenshot({ path: OUT + '/32_sandbox_done.png' });
  // the other system (none) ran in parallel: switch arm
  await pg.click('#armseg button[data-k="none"]'); await sleep(2500); check('switching to the no-memory arm shows its session (or live view if still running)', /No memory/.test(await T('#chathead')) || await pg.$eval('#playbar', e => e.classList.contains('live')), await T('#chathead'));
  // second task reuses memory
  await pg.click('#armseg button[data-k="kv"]'); await sleep(1500);
  await pg.fill('#q', 'Append a second line to NOTES.md.'); await pg.$eval('#taskarms input[data-k="none"]', e => { e.checked = false; }); await pg.click('#send'); await sleep(2500);
  check('second task live', await pg.$eval('#playbar', e => e.classList.contains('live')));
  await pg.waitForFunction(() => !document.querySelector('#playbar').classList.contains('live'), null, { timeout: 300000 }); await sleep(2500);
  check('second task: memory injected from task 1', /Memory injected/.test(await T('#transcript')), (await T('#transcript')).slice(0, 120));
  await pg.screenshot({ path: OUT + '/33_sandbox_task2.png' });
  console.log('errors:', errs.length ? errs.join('\n') : 'none'); console.log('SUMMARY', results.filter(r => r[0] === 'FAIL').length, 'failed of', results.length); await b.close();
})().catch(e => { console.error('FAILED', e); process.exit(1); });
