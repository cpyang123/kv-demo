# Ledger cost table (Table 3)

Aggregation scripts that turn the per-session token ledger written by the harness
(`harness/ledger.py`, `harness/prices.json`, `RSM_LEDGER=1`) into the paper's ledger cost table.
They are copies of the scripts in the parent project (`train-corpus/` and `paper/tables/`). The only
edits are path handling; the computation is unchanged.

## Files

| File | Role |
|---|---|
| `make_table3_ledger.py` | The table generator. Per-replay loading, per-slot means, common slots, mean ± across-replay SD, Res/Tok, change tags vs the no-memory row, paired chain-cluster bootstrap stars. Writes `table3_ledger_full.tex` and `table3_ledger_full.md`; with `RSM_T3_LAYOUT=split` the paper's 14-column layout as `table3_ledger_27b_split.tex` and `.md` (see Options). |
| `ledger_rollup.py` | Per-arm ledger loader (`load_arm`): reads `calls.jsonl` per slot, keeps the calls of the last attempt of each slot (see Last-attempt rule) and summarises them with `harness.ledger.summarize`; falls back to `traj_*.json` usage plus `writer_calls.jsonl` for runs without `calls.jsonl`. Also a standalone per-arm roll-up CLI. |
| `table3_ledger_replays.json` | Example replay-pin file for `RSM_T3_REPLAYS`: the replays counted in the paper's 27B block. |
| `exam_table3.py` | Slot loader (`load`): resolve, `n_steps` and the linked flag (`data/task_link_flags_v2.json`) for slots at pos 2 and 3; holds the repo-root and runs-directory settings. |
| `exam_table3_reps.py` | Replay-aware variant of `exam_table3`; imported by `ledger_rollup.py`, not used by the table itself. |
| `make_main_avg.py` | Table 2 generator, imported only as a helper library here: replay discovery (`disc`), number formatting (`num`), change tags (`delta_tag`), row labels. Its own `__main__` builds the main tables and needs run-ids that are not part of the ledger table. |
| `neg_mass_hypothesis.py` | Copied whole (about 82 KB). The table uses only its `boot_ci` (cluster bootstrap). Its other stages need GRPO trainer modules and rollout results from the parent project and do not run from this directory. |

Data read at run time, all inside this repo:

- `runs/<run-id>/<chain>__<seed>__<arm>/results.jsonl` and `calls.jsonl` (plus `traj_<pos>_*.json` for the
  trajectory length, and `writer_calls.jsonl` as fallback)
- `data/chains/frame_test_v1_chains.txt` (48 test chains, identical to `split_v2_test.txt`)
- `data/chains/split_v2_train.txt` (51 training chains, used only with `TABLE3_FULL=1`; the one chain in
  `excluded_v1.txt` is already left out of it)
- `data/task_link_flags_v2.json` (linked / unlinked flag per `<chain>|<pos>`)
- `harness/ledger.py`, `harness/prices.json`

`data/chains/frame_test_v1.tsv` (one line per test slot) and `excluded_v1.txt` are committed alongside
for reference; the scripts do not read them.

## Regenerate

From the repo root:

    python3 analysis/ledger_table/make_table3_ledger.py
    python3 analysis/ledger_table/make_table3_ledger.py --out /some/dir      # other output directory

The paper's 14-column layout with its pinned replays (writes `table3_ledger_27b_split.tex` and `.md`):

    RSM_T3_LAYOUT=split RSM_T3_REPLAYS=analysis/ledger_table/table3_ledger_replays.json \
        python3 analysis/ledger_table/make_table3_ledger.py

Run directories elsewhere (the directory must contain `ft1_ledger/`, and `tr1_ledger/` for the full sample):

    RSM_RUNS=/path/to/runs python3 analysis/ledger_table/make_table3_ledger.py

Environment overrides:

| Variable | Default | Meaning |
|---|---|---|
| `RSM_ROOT` | two levels above the script (this repo) | root holding `data/` and `harness/` |
| `RSM_RUNS` | `$RSM_ROOT/runs` | directory holding the run-ids |
| `RSM_TABLE_OUT` | `analysis/ledger_table/out/` | output directory (`--out` takes precedence) |
| `TABLE3_FULL=1` | unset | add the training half (`tr1_ledger` over `split_v2_train.txt`) |
| `RSM_PRICE_ROW` | `_default_row` of `prices.json` (`opus5`) | price row for the USD meter |
| `RSM_PRICE_TABLE` | `harness/prices.json` | price table file |
| `RSM_T3_LAYOUT` | unset | `split` = the 14-column layout (see Options) |
| `RSM_T3_REPLAYS` | unset | JSON file `{arm: ["_r1", "_r2", ...]}` pinning the counted replays (see Options) |
| `RSM_T3_OUT` | unset | output path prefix, writes `<prefix>.tex` and `<prefix>.md` |
| `RSM_ROLLUP_KEEP_ABORTED=1` | unset | sum the calls of every attempt of a slot (the old roll-up, see Last-attempt rule) |

The console prints per-row completeness (complete replays / found, coverage per replay, sessions without
`calls.jsonl`) and the table. `out/` is ignored by git.

## Expected run-id and arm names

The rows are fixed in `ROWS` of `make_table3_ledger.py`. The test half reads run-id `ft1_ledger`, the
training half `tr1_ledger`; an arm has the same name in both. Arms of the 27B block:

- untrained actor: `seq_w27b`, `mem_rawcat_w27b`, `mem_factsall_w27b`, `mem_memop_w27b`, `mem_rbankj_w27b`,
  `mem_v5knote_base4b_w27b`, `mem_v5knote_w27b`, `mem_v5inline_w27b`
- trained actor (LoRA serving path): `seq_a27dvw6bs0_led`, `mem_v5knote_a27dvw6bs0_selfled`,
  `seq_a27dvw7as20_led`, `mem_v5knote_a27dvw7as20_selfled`, `seq_a27prov8ds20_led`,
  `mem_v5knote_a27prov8ds20_selfled`, `mem_v5inline_a27prov8ds20`

Replays of an arm are the directories whose arm suffix matches `^<arm>(_r\d+)?$` (`<arm>`, `<arm>_r2`,
`<arm>_r3`, ...). Other suffixes (for example `_off`, `_envonly`) are not replays of that arm. The chain is
the part of the directory name before the first `__`, the arm the part after the last `__`.

## Token meters

Per call the ledger records prompt, cached and uncached prompt tokens and completion tokens, with a role
(actor, note_writer, memory_extract / memory_retrieve / judge / other). Per session
(`harness.ledger.summarize`):

- raw = sum of (prompt + completion) over every call of the session
- cache-aware = sum of (uncached prompt + completion), i.e. prefix-cache hits are free
- priced USD = (uncached prompt x input + cached prompt x cached_input + completion x output) / 1e6 at one
  row of `harness/prices.json` for all arms (default `opus5`: 5.00 / 0.50 / 25.00 USD per 1M). This is a
  conversion, not an expense.

Default layout columns: Cache-aware and Raw are K tokens per session at pos >= 2. USD / chain sums every
session of a chain (pos 1, 2, 3). Writer side = uncached prompt + completion of note_writer and memory-side
calls, K per chain. Res/Tok = a replay's overall resolve % divided by that replay's overall cache-aware K per
session, averaged over replays. The `.md` also has Res per USD-chain and prefix-cache reuse
(1 - sum uncached prompt / sum prompt).

Split layout columns (`RSM_T3_LAYOUT=split`, the paper's Table 1). Linked and Unlinked report Resolve and
Steps only. Overall reports Resolve, Steps, Actor, Writer side, Total, Res/Tok on Actor, Res/Tok on Total and
USD / chain. All token columns are cache-aware K per session at pos >= 2:

- Actor = the cache-aware tokens (uncached prompt + completion) of the acting model's own calls (role `actor`).
- Writer side = the cache-aware tokens of every other call made for memory in that session, namely note writing
  (the in-session note turn included), extraction and judging; 0 for the no-memory and raw-concat rows.
- Total = Actor + Writer side = the session's cache-aware total (checked for every counted session).
- Res/Tok (actor) and Res/Tok (total) = a replay's overall resolve % divided by that replay's Actor K or Total K
  per session, then averaged over replays; the bootstrap stars cover both.

Every session ends with one memory write, including the last session of a chain, so the per-session Writer
side is also the cost of writing one note (in `ft1_ledger` every counted session of every memory arm makes
memory-side calls). No method calls a model at read time here. Each injects its stored memory into the
successor's first prompt, and those tokens are counted under Actor.

Raw is an appendix quantity in this layout. It stays in the `.md` (with the per-chain writer side and the
priced USD, which the `.tex` prints as --).

## Options

- `RSM_T3_LAYOUT=split` writes the 14-column layout described under Token meters. Only the rows the paper shows
  are visible; the other rows are kept as `% [hidden]` lines. Output is `table3_ledger_27b_split.tex` and `.md`
  in the output directory unless `RSM_T3_OUT` is set.
- `RSM_T3_REPLAYS=<json>` counts only the listed replays of the listed arms (`"_r1"` or `""` is the unsuffixed
  first replay; keys are arm names or row keys; keys starting with `_` are comments). Arms not listed keep every
  replay. A pinned replay still has to be complete, and the console warns about any pinned replay that is missing
  or incomplete. `table3_ledger_replays.json` is the example.
- `RSM_T3_OUT=<path prefix>` writes `<prefix>.tex` and `<prefix>.md` instead of the default file names.

Unset, the three options leave the default table as it was. The last-attempt rule below applies in both layouts.

## Last-attempt rule

A chain that is killed and restarted rewrites `results.jsonl`, which therefore keeps only the final attempt of
each slot (resolve, steps), while `calls.jsonl` is append-only and keeps the calls of the aborted attempt under
the same slot number. `ledger_rollup.read_calls` therefore keeps, per slot, only the calls of the last attempt,
so the token columns describe the same attempt as resolve and steps. The marker is `call_idx`:
`harness/ledger.py` resets it in `begin_session()` and increments it on every recorded call, so an attempt is a
gap-free run 1..n and a new attempt starts wherever `call_idx` fails to increase over the previous row of the
same slot. In `ft1_ledger` this affects 34 of 14322 slots in 12 chain directories, and the roll-up then equals
the session fields of `results.jsonl` (`tok_cacheaware_total`, `tok_raw_total`, `tok_<role>_*`) on every counted
session of the pinned replays. `RSM_ROLLUP_KEEP_ABORTED=1` restores the old sum over every attempt.

## Replay counting and common slots

1. A replay counts in a half only at full coverage: every slot at pos 2 and 3 of every chain of that half
   (2 x 48 = 96 slots on the test half) has a `results.jsonl` row and a ledger summary. Incomplete replays
   are printed on the console and skipped.
2. Per slot, metrics are averaged over every complete replay of that arm.
3. Common slots = intersection of the slots over all rows that have at least one complete replay; every
   cell is the mean over the common slots of its stratum (linked, unlinked, overall). Chain columns use
   the chains whose pos 2 and pos 3 slots are common and whose pos 1 session has a ledger summary in every
   row.
4. The ± value is the SD across whole-frame replays (a replay suffix complete in every half in use), each
   reduced to its own mean over the common slots; no ± with fewer than two.

The standalone `ledger_rollup.py` CLI uses a different, relative rule (`--min-cov 0.95` of the largest
replay); the table does not use it.

## Bootstrap

Paired chain-cluster bootstrap of (row - no-memory row) on the common slots, using per-slot means over
replays: 2000 resamples, seed 0, chains as clusters (`neg_mass_hypothesis.boot_ci`). Statistics: mean paired
difference for Resolve and Steps; for Res/Tok the difference of sum(res) / sum(cache-aware K) on the resampled
slots (in the split layout also sum(res) / sum(Actor K) for Res/Tok on Actor). `*` = the 95% interval excludes
0, `**` = the 99% interval (same resamples) excludes 0. The `.md` lists the point estimates and both
intervals.
