const { chromium } = require('playwright');
// Memory comparison view (what each memory method retrieved / wrote / kept, task by task) on the agent and compare pages,
// rendered by static/player.js memoryCompareHtml. The agent part is written against the ChainSWE mode of the agent view
// (?src=chains&cid=…) and tolerates renamed controls: what it cannot find is reported as SKIP, not FAIL.
// Starts nothing; on 8765 / 8766 (or RO=1) every non-GET /api request the pages make is aborted.
const path = require('path'), fs = require('fs');
const OUT = process.env.SHOTS || path.join(__dirname, 'shots'), BASE = process.env.BASE || 'http://127.0.0.1:8799'; fs.mkdirSync(OUT, { recursive: true });
const RO = /:876[56]\b/.test(BASE) || process.env.RO === '1';
const HYP = 'HypothesisWorks_hypothesis_20221202', PYC = 'pyccel_pyccel_20240307', SQL = 'tobymao_sqlglot_20240910_b10255eb';
let failed = 0, total = 0;
const ok = (n, c, d = '') => { total++; if (!c) failed++; console.log((c ? 'PASS ' : 'FAIL ') + n + (c ? '' : ' — ' + d)); };
const skip = (n, why) => console.log(`SKIP ${n} — ${why}`);
const firstLine = t => (String(t || '').split('\n').map(l => l.trim()).find(Boolean) || '').replace(/^#+\s*/, '').replace(/\*\*|__|`/g, '').trim();
const JARGON = /F2P|P2P|reconstructed|saved store\b(?! mode)|tobymao__sqlglot-\d|ours:/;
(async () => {
  const b = await chromium.launch(); const errs = [];
  const getJ = async p => (await (await fetch(BASE + '/' + p)).json());
  const chainSql = await getJ(`api/chain/${SQL}?run=__live__`);
  const nameOf = o => { const t = chainSql.chain.tasks.find(x => x.order === o) || {}; return String(t.summary || '').trim() || firstLine(t.title); };
  for (const scheme of ['light', 'dark']) {
    const ctx = await b.newContext({ viewport: { width: 1500, height: 900 }, colorScheme: scheme });
    if (RO) await ctx.route('**/api/**', r => r.request().method() === 'GET' ? r.continue() : r.abort());
    const p = await ctx.newPage();
    p.on('pageerror', e => errs.push(e.message));
    const txt = async sel => ((await p.textContent(sel).catch(() => '')) || '').replace(/\s+/g, ' ');
    const itxt = async sel => ((await p.$eval(sel, e => e.innerText).catch(() => '')) || '').replace(/\s+/g, ' ');

    // ------------------------------------------------------------ agent view (ChainSWE mode)
    await p.goto(BASE + `/?src=chains&cid=${HYP}`, { waitUntil: 'domcontentloaded' });
    const opened = await p.waitForSelector('#transcript .turn', { timeout: 30000 }).then(() => true).catch(() => false);
    if (!opened) skip(`agent (${scheme})`, 'no session opened for ?src=chains&cid=…');
    else {
      await p.waitForTimeout(800);
      // the comparison opens from the Memory tab (button #memcmp2); fall back to the ?view=memory deep link
      let how = 'tab';
      const tab = await p.$('#tabs button[data-t="memory"]'); if (tab) await tab.click().catch(() => {});
      const btn = await p.$('#memcmp2');
      if (btn && await btn.isVisible()) await btn.click(); else { how = 'link'; await p.goto(BASE + `/?src=chains&cid=${HYP}&view=memory`, { waitUntil: 'domcontentloaded' }); }
      const grid = await p.waitForSelector('.mc-grid', { timeout: 20000 }).then(() => true).catch(() => false);
      if (scheme === 'light') ok(`agent: memory comparison opens (${how})`, grid);
      if (grid) {
        const heads = await p.$$eval('.mc-head .mc-title', e => e.map(x => x.textContent.trim()));
        if (scheme === 'light') {
          ok('agent: two columns, Knowledge Vault then Mem0', heads.length === 2 && /Knowledge Vault/.test(heads[0]) && /Mem0/.test(heads[1]), heads.join('|'));
          const pb = await p.$('#playbar'); if (pb) ok('agent: playbar hidden in memory view', await pb.evaluate(e => e.hidden || getComputedStyle(e).display === 'none')); else skip('agent: playbar', '#playbar not found');
          ok('agent: rows per turn', (await p.$$('.mc-row')).length === 3, `${(await p.$$('.mc-row')).length}`);
          ok('agent: row heads "Turn k" or "Turn k — <name>", ids only with Show details', (await p.$$eval('.mc-row', e => e.map(x => x.innerText.trim()))).every((t, i) => new RegExp(`^Turn ${i + 1}( — .+)?$`).test(t)), JSON.stringify(await p.$$eval('.mc-row', e => e.map(x => x.innerText.trim()))));
          const c2 = await p.$$eval('.mc-cell', e => e.map(x => x.textContent));
          ok('agent: each cell shows retrieved and written', c2.length > 0 && c2.every(t => /Retrieved before turn \d/.test(t) && /Written after turn \d/.test(t)), (c2[0] || '').slice(0, 120));
          ok('agent: turn 1 retrieved nothing', /Nothing — first turn/.test(c2[0] || ''));
          ok('agent: KV turn 2 retrieved the note from turn 1', /from turn 1/.test(c2[2] || ''), (c2[2] || '').slice(0, 160));
        }
        await p.screenshot({ path: `${OUT}/mem_${scheme}_written.png` });
        const store = await p.$('[data-mcmode="store"]');
        if (store) { await store.click(); await p.waitForTimeout(300); if (scheme === 'light') ok('agent: saved store mode shows new badges', (await p.$$('.mc-item.new')).length > 0); await p.screenshot({ path: `${OUT}/mem_${scheme}_store.png` }); }
        if (scheme === 'light') {
          const row = await p.$('.mc-row[data-order="2"]');
          if (row) { await row.click(); const back = await p.waitForSelector('#transcript .turn', { timeout: 15000 }).then(() => true).catch(() => false);
            ok('agent: a turn row opens that session', back && /Turn 2|2 of|turn 2/i.test(await txt('#chathead') + await txt('#transcript') + await txt('#taskpin')), await txt('#chathead')); }
          // Esc returns to the session
          if (tab) await tab.click().catch(() => {}); const b2 = await p.$('#memcmp2');
          if (b2 && await b2.isVisible()) { await b2.click(); await p.waitForSelector('.mc-grid'); await p.keyboard.press('Escape'); await p.waitForTimeout(400);
            ok('agent: Esc returns to the session', !(await p.$('.mc-grid')) && !!(await p.$('#transcript .turn'))); } else skip('agent: Esc', '#memcmp2 not found');
          // deep link on another chain
          await p.goto(BASE + `/?src=chains&cid=${PYC}&view=memory`, { waitUntil: 'domcontentloaded' });
          const dl = await p.waitForSelector('.mc-grid', { timeout: 30000 }).then(() => true).catch(() => false);
          ok('agent: ?view=memory deep link', dl);
          if (dl) ok('agent: Mem0 empty writes flagged on pyccel', /writes? stored nothing/.test(await txt('.mc-grid')), (await txt('.mc-grid')).slice(0, 200));
        }
      }
    }

    // ------------------------------------------------------------ compare view
    await p.goto(BASE + `/compare?cid=${SQL}&cols=kv,mem0,memop,rawcat,none&view=memory`, { waitUntil: 'domcontentloaded' });
    await p.waitForSelector('#memview .mc-grid', { timeout: 30000 }); await p.waitForTimeout(800);
    const kinds = await p.$$eval('#memview .mc-head', e => e.map(x => x.dataset.kind));
    if (scheme === 'light') {
      ok('compare: one memory column per method, in column order', kinds.join() === 'kv,mem0,memop,rawcat,none' && kinds.join() === (await p.$$eval('#cols .col', e => e.map(x => x.dataset.kind))).join(), kinds.join());
      ok('compare: play controls hidden', await p.$eval('#play', e => e.hidden));
      ok('compare: the bar names the task "<title> — <repo>" and says once what a row shows', /— tobymao\/sqlglot\./.test(await itxt('#memview .mc-bar')) && ((await itxt('#memview .mc-bar')).match(/Each row is a turn/g) || []).length === 1, await itxt('#memview .mc-bar'));
      ok(`compare: rows "Turn k — <the turn's ${chainSql.chain.tasks[0].summary ? 'summary' : 'first line'}>"`, (await p.$$eval('#memview .mc-row b', e => e.map(x => x.textContent.trim()))).every((t, i) => t === `Turn ${i + 1} — ${nameOf(i + 1)}`), JSON.stringify(await p.$$eval('#memview .mc-row b', e => e.map(x => x.textContent.trim()))));
      ok('compare: plain words without Show details (no ids, badges or jargon)', !JARGON.test(await itxt('#memview')), ((await itxt('#memview')).match(JARGON) || [])[0]);
      // (the server drops the "## Task Guidance" line MemOp puts in front of its document when it injects it; servers before that show it)
      ok('compare: MemOp column = one guidance document (markdown: headings, no raw "#")', !!(await p.$('#memview .mc-cell[data-kind="memop"] .note.md .md-h')) && !/(^|\n)\s*#{1,6} /.test(await p.$eval('#memview .mc-cell[data-kind="memop"] .note.md', e => e.innerText)), (await txt('#memview .mc-cell[data-kind="memop"]')).slice(0, 160));
      ok('compare: Full history column = earlier sessions verbatim', !!(await p.$('#memview .mc-cell[data-kind="rawcat"] .note.rawtext')));
      ok('compare: Full history has no writer cost', /writer 0 tokens/.test(await txt('#memview .mc-head[data-kind="rawcat"]')), await txt('#memview .mc-head[data-kind="rawcat"]'));
      ok('compare: MemOp writer cost is estimated (> 0)', /writer ≈?[\d.]+K? tokens/.test(await txt('#memview .mc-head[data-kind="memop"]')) && !/writer 0 tokens/.test(await txt('#memview .mc-head[data-kind="memop"]')), await txt('#memview .mc-head[data-kind="memop"]'));
      ok('compare: No memory column says nothing is kept', /nothing is written or kept/.test(await txt('#memview .mc-head[data-kind="none"]')));
      ok('compare: No memory cells are empty and borderless (no "–" boxes)', await p.$$eval('#memview .mc-cell[data-kind="none"]', e => e.length > 0 && e.every(x => !x.textContent.trim() && getComputedStyle(x).borderTopWidth === '0px')));
      ok('compare: KV and Mem0 cells show retrieved + written', /Retrieved before turn 1/.test(await txt('#memview .mc-cell[data-kind="kv"]')) && /Written after turn 1/.test(await txt('#memview .mc-cell[data-kind="mem0"]')));
      ok('compare: KV header tooltip "Knowledge Vault (ours) — …"', /^Knowledge Vault \(ours\) — /.test(await p.$eval('#memview .mc-head[data-kind="kv"] .mc-title', e => e.title)), await p.$eval('#memview .mc-head[data-kind="kv"] .mc-title', e => e.title));
      const wide = await p.$$eval('#memview .mc-cell', cs => cs.filter(c => [...c.querySelectorAll('*')].some(e => { const r = e.getBoundingClientRect(); return r.width && r.right > c.getBoundingClientRect().right + 1.5; })).length);
      ok('compare: nothing inside a cell is wider than the cell', wide === 0, `${wide} cells`);
      // Show details: ids and how the store was read back appear again
      await p.click('#gearbtn'); await p.check('#details'); await p.keyboard.press('Escape'); await p.waitForTimeout(300);
      ok('compare: Show details shows the instance ids and the store badges', /tobymao__sqlglot-\d/.test(await itxt('#memview .mc-row')) && /saved store|reconstructed/.test(await itxt('#memview .mc-grid')), (await itxt('#memview .mc-row')).slice(0, 120));
      await p.click('#gearbtn'); await p.uncheck('#details'); await p.keyboard.press('Escape');
    }
    await p.screenshot({ path: `${OUT}/mem_compare_${scheme}.png` });
    if (scheme === 'light') {
      await p.click('#memview [data-mcmode="store"]'); await p.waitForTimeout(400);
      ok('compare: saved store mode', /Saved store/.test(await txt('#memview .mc-seg')) && (await p.$$('#memview .mc-item')).length > 0 && /the store each method kept/.test(await itxt('#memview .mc-bar')));
      await p.click('#memview .mc-row[data-order="2"]'); await p.waitForTimeout(1800);
      ok('compare: turn row returns to transcripts at that turn', !(await p.$eval('#cols', e => e.hidden)) && /Turn 2/.test(await txt('#task-kv')) && /Turn 2/.test(await txt('#task-memop')), `${await txt('#task-kv')} | ${await txt('#task-memop')}`);
      ok('compare: … and the turn row names it like the memory row', (await txt('#ttl-kv')).trim() === nameOf(2), `${await txt('#ttl-kv')} vs ${nameOf(2)}`);
      // the strips count the task on screen as ⇥ does: the same numbers as ↺, then ⇥ until task 2 is shown whole
      const strips = () => p.$$eval('#cols .col', cs => cs.map(c => [c.dataset.kind, c.querySelector('.tp-n').innerText.trim(), (c.querySelector('.strip .stat')?.innerText || '').replace(/\s+/g, ' ').trim()]));
      const viaRow = await strips();
      await p.click('#restart'); await p.waitForTimeout(1500); await p.click('#end'); await p.waitForTimeout(800); await p.click('#end'); await p.waitForTimeout(1500);
      const viaEnd = await strips();
      ok('compare: after a memory-row jump the strips count the turn on screen, as ↺ then ⇥ do (2 turns counted)', JSON.stringify(viaRow) === JSON.stringify(viaEnd) && viaRow.every(([, row, st]) => /^Turn 2\/\d+ · \d+ steps?$/.test(row) && /tokens.*\/2 resolved/.test(st)), `${JSON.stringify(viaRow)} vs ${JSON.stringify(viaEnd)}`);
      // the row of the task already on screen (task 1, before any playback): shown whole and counted, as ⇥ at rest does
      await p.goto(BASE + `/compare?cid=${SQL}&cols=kv,mem0,memop,rawcat,none`, { waitUntil: 'domcontentloaded' }); await p.waitForSelector('#sum table tbody tr', { state: 'attached', timeout: 30000 }); await p.waitForTimeout(1500);
      await p.click('#end'); await p.waitForTimeout(1500); const viaEnd1 = await strips();
      await p.goto(BASE + `/compare?cid=${SQL}&cols=kv,mem0,memop,rawcat,none&view=memory`, { waitUntil: 'domcontentloaded' }); await p.waitForSelector('#memview .mc-grid', { timeout: 30000 }); await p.waitForTimeout(800);
      await p.click('#memview .mc-row[data-order="1"]'); await p.waitForTimeout(1800); const viaRow1 = await strips();
      ok('compare: the memory row of turn 1 before any playback counts it, as ⇥ at rest does (1 turn counted)', JSON.stringify(viaRow1) === JSON.stringify(viaEnd1) && viaRow1.every(([, row, st]) => /^Turn 1\/\d+ · \d+ steps?$/.test(row) && /tokens.*\/1 resolved/.test(st)), `${JSON.stringify(viaRow1)} vs ${JSON.stringify(viaEnd1)}`);
      // switching the view back and forth keeps the columns
      await p.click('#viewseg button[data-view="memory"]'); await p.waitForSelector('#memview .mc-grid'); await p.waitForTimeout(500);
      ok('compare: view switch keeps the memory columns', (await p.$$eval('#memview .mc-head', e => e.length)) === 5);
      // phone width
      await p.setViewportSize({ width: 420, height: 900 }); await p.waitForTimeout(500);
      const sw = await p.evaluate(() => document.documentElement.scrollWidth); ok('compare: memory view, no horizontal page overflow at 420 px', sw <= 420, `scrollWidth=${sw}`);
      await p.screenshot({ path: `${OUT}/mem_compare_phone.png` });
      await p.setViewportSize({ width: 1500, height: 900 });
      // ⚙ What to compare › Saved live runs: the memory view of a saved live run (a chain × model with Knowledge Vault and another method)
      await p.goto(BASE + '/compare?cols=kv,mem0,none', { waitUntil: 'domcontentloaded' }); await p.waitForSelector('#sum table tbody tr', { state: 'attached', timeout: 30000 });
      const savedBtn = await p.$('#srcseg button[data-src="saved"]');
      if (!savedBtn || await savedBtn.evaluate(e => e.hidden)) skip('compare: memory view of a saved live run', 'no saved live runs on this server');
      else {
        await p.click('#gearbtn'); await savedBtn.click(); await p.waitForTimeout(3000); await p.keyboard.press('Escape');
        const model = new URL(p.url()).searchParams.get('saved');
        await p.click('#viewseg button[data-view="memory"]'); const g = await p.waitForSelector('#memview .mc-grid', { timeout: 30000 }).then(() => true).catch(() => false);
        ok('compare: memory view of a saved live run', g && (await itxt('#memview .mc-bar')).includes(`live runs with ${model || 'an unrecorded model'}`) && (await p.$$('#memview .mc-row')).length > 0, (await itxt('#memview .mc-bar')).slice(0, 160));
        ok('compare: … its columns name the model', (await p.$$eval('#memview .mc-head .mc-title', e => e.map(x => x.innerText.trim()))).every(t => model ? t.endsWith(`· ${model}`) : /· live run$/.test(t)), JSON.stringify(await p.$$eval('#memview .mc-head .mc-title', e => e.map(x => x.innerText.trim()))));
        await p.screenshot({ path: `${OUT}/mem_compare_saved.png` });
      }
    }
    await ctx.close();
  }
  console.log('errors: ' + (errs.length ? errs.join(' | ') : 'none')); console.log(`SUMMARY ${failed} failed of ${total}`); await b.close(); process.exit(failed || errs.length ? 1 : 0);
})().catch(e => { console.error('FAILED', e); process.exit(1); });
