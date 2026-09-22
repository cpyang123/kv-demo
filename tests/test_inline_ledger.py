#!/usr/bin/env python3
"""Offline contract + unit tests for v5inline and the token ledger (no GPU, no containers, no network).

Covers DESIGN_inline_ledger_0921.md §C.2:
  1. strip_action_residue / parse_note on recorded-shape samples
  2. the fallback state machine truth table (exit_status, headroom, valid1, valid2)
  3. ledger roll-up: the three totals from a synthetic calls.jsonl, plus the cached-token estimator
  4. the role rule table, including a no-drift assertion against the live prompt constants
  5. request bytes unchanged: the body distill_fused_note puts on the wire is byte-identical
     with RSM_LEDGER unset and with RSM_LEDGER=1 (the ledger only watches)
  6. results.jsonl key set unchanged: every line the patch added to mini_swe_agent.py is guarded
  7. registry: v5inline exists, v5knote is untouched

Run:  python test_inline_ledger.py            (plain runner)
      python -m pytest test_inline_ledger.py  (if pytest is installed)
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent          # chainswe/
sys.path.insert(0, str(ROOT))

from harness import inline_note as INL        # noqa: E402
from harness import ledger as L               # noqa: E402

FAILURES: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(f"{name} {detail}")


def _clear_env(*names):
    for n in names:
        os.environ.pop(n, None)


# ---------------------------------------------------------------- 1. note parser

SAMPLES = [
    # (raw model output, must-contain-first-line, must_be_valid)
    ("FILES: a/b.py\nSTATUS: submitted | TESTS: pytest a [exit 0]\n", "FILES: a/b.py", True),
    # mswea_bash_command fence residue -> stripped, note still valid
    ("FILES: a/b.py\n```mswea_bash_command\npytest -q\n```\nDID: fixed it | WHERE: a/b.py:10\n",
     "FILES: a/b.py", True),
    # think block
    ("<think>let me summarise</think>\nFILES: x.py\nCONTEXT: tests live in tests/ | WHERE: tox.ini:3\n",
     "FILES: x.py", True),
    # THOUGHT: prose plus out-of-scope prose lines
    ("THOUGHT: I will now write the note.\nHere is the note:\nFILES: x.py\nsome trailing prose\n",
     "FILES: x.py", True),
    # half-sentence / no tags at all -> invalid
    ("I fixed the bug by editing the parser and then ran the tests, which", "", False),
    # a pure fence, nothing else -> invalid
    ("```\nFILES: x.py\n```", "", False),
]


def test_parser():
    _clear_env("RSM_INLINE_STRICT", "RSM_VALID_V2", "RSM_NOTE_CAP")
    for i, (raw, first, ok_want) in enumerate(SAMPLES):
        note, ok = INL.parse_note(raw)
        check(f"parser[{i}] validity", ok is ok_want, f"got ok={ok} note={note[:60]!r}")
        if ok_want:
            check(f"parser[{i}] first line", note.splitlines()[0] == first,
                  f"got {note.splitlines()[0]!r}")
            check(f"parser[{i}] no fence", "```" not in note)
            check(f"parser[{i}] all lines tagged",
                  all(l.startswith(INL.V4_TAGS) for l in note.splitlines() if l.strip()))
    # 5th sample is invalid under the lenient path because stripping leaves nothing
    note, ok = INL.parse_note(SAMPLES[5][0])
    check("parser fence-only strips to empty", note == "" and not ok, f"got {note!r}")
    # cap: whole-line truncation under RSM_VALID_V2=1
    os.environ["RSM_VALID_V2"] = "1"
    os.environ["RSM_NOTE_CAP"] = "40"
    long = "FILES: " + "a" * 60 + "\nDID: second line | WHERE: x\n"
    note, _ = INL.parse_note(long)
    check("parser cap is whole-line", "\n" not in note and len(note) <= 40, f"got {note!r}")
    _clear_env("RSM_VALID_V2", "RSM_NOTE_CAP")
    # strict mode = validate before stripping -> the fence sample is rejected
    os.environ["RSM_INLINE_STRICT"] = "1"
    _, ok = INL.parse_note(SAMPLES[1][0])
    check("strict rejects fence residue", not ok)
    _clear_env("RSM_INLINE_STRICT")


# ---------------------------------------------------------------- 2. state machine

def test_state_machine():
    _clear_env("RSM_INLINE_DISABLE", "RSM_INLINE_FALLBACK_EXITS", "RSM_INLINE_WINDOW",
               "RSM_INLINE_MAXTOK")
    W = 98304
    cases = [
        # (exit_status, prev_prompt_tokens, tail_chars, go, reason)
        ("Submitted", 40000, 8000, True, None),
        ("LimitsExceeded", 40000, 8000, True, None),          # NOT in the default fallback set
        ("TimeExceeded", 40000, 8000, False, "bad_exit"),
        ("RepeatedFormatError", 40000, 8000, False, "bad_exit"),
        ("error:ContextWindowExceededError", 100, 100, False, "bad_exit"),
        ("error:SomethingElse", 100, 100, False, "bad_exit"),
        ("Submitted", W - 500, 100, False, "no_headroom"),
        ("Submitted", 0, 0, True, None),
    ]
    for ex, pt, tc, go_w, rs_w in cases:
        go, rs, est = INL.decide(ex, pt, tc)
        check(f"decide({ex},{pt},{tc})", (go, rs) == (go_w, rs_w), f"got {(go, rs)} est={est}")
    # bad_exit precedence over no_headroom
    go, rs, _ = INL.decide("TimeExceeded", W, 10000)
    check("bad_exit beats no_headroom", (go, rs) == (False, "bad_exit"), f"got {(go, rs)}")
    # RSM_INLINE_DISABLE=1 -> straight to the separate writer (same arm, writing path swapped)
    os.environ["RSM_INLINE_DISABLE"] = "1"
    go, rs, _ = INL.decide("Submitted", 100, 100)
    check("RSM_INLINE_DISABLE", (go, rs) == (False, "disabled"), f"got {(go, rs)}")
    _clear_env("RSM_INLINE_DISABLE")
    # the fallback exit set is configurable
    os.environ["RSM_INLINE_FALLBACK_EXITS"] = "LimitsExceeded"
    check("configurable exit set", INL.bad_exit("LimitsExceeded"))
    _clear_env("RSM_INLINE_FALLBACK_EXITS")
    # headroom arithmetic: est = prev + ceil(chars/3.2)*1.15
    ok, est = INL.headroom_ok(1000, 3200, maxtok=900, window=98304)
    check("headroom arithmetic", est == 1000 + int(1000 * 1.15) and ok, f"est={est}")


# ---------------------------------------------------------------- 3. ledger roll-up

def _row(role, p, c, comp, note_turn=False, src="usage"):
    return {"role": role, "prompt_tokens": p, "cached_tokens": c, "uncached_prompt_tokens": p - c,
            "completion_tokens": comp, "cached_src": src, "note_turn": note_turn}


def test_rollup():
    rows = [_row("actor", 1000, 900, 100),
            _row("actor", 2000, 1800, 200),
            _row("note_writer", 5000, 4900, 300, note_turn=True),
            _row("memory_extract", 400, 0, 50, src="none"),
            _row("judge", 100, 0, 10, src="none")]
    s = L.summarize(rows)
    raw = (1000 + 100) + (2000 + 200) + (5000 + 300) + (400 + 50) + (100 + 10)
    ca = (100 + 100) + (200 + 200) + (100 + 300) + (400 + 50) + (100 + 10)
    check("raw total", s["tok_raw_total"] == raw, f"got {s['tok_raw_total']} want {raw}")
    check("cacheaware total", s["tok_cacheaware_total"] == ca, f"got {s['tok_cacheaware_total']} want {ca}")
    check("actor split", (s["tok_actor_prompt"], s["n_calls_actor"]) == (3000, 2))
    check("writer split", (s["tok_writer_prompt"], s["n_calls_writer"]) == (5000, 1))
    check("other split", (s["tok_memother_prompt"], s["n_calls_memother"]) == (500, 2))
    check("estimated share", abs(s["cached_src_estimated_share"] - 2 / 5) < 1e-9,
          f"got {s['cached_src_estimated_share']}")
    # priced: hand-computed against the default opus5 row
    os.environ.pop("RSM_PRICE_ROW", None)
    os.environ.pop("RSM_PRICE_CACHE_WRITE", None)
    L._PRICE["row"] = None
    pr = L.price_row()
    unc = sum(r["uncached_prompt_tokens"] for r in rows)
    cch = sum(r["cached_tokens"] for r in rows)
    cmp_ = sum(r["completion_tokens"] for r in rows)
    want = (unc * pr["input"] + cch * pr["cached_input"] + cmp_ * pr["output"]) / 1e6
    check("priced total", abs(L.cost_usd(rows) - want) < 1e-12, f"got {L.cost_usd(rows)} want {want}")
    check("price row default", pr["_row"] == "opus5", f"got {pr['_row']}")
    check("price sha recorded", len(L.price_table_sha()) == 12, f"got {L.price_table_sha()}")
    # a calls.jsonl round trip, including the no-trailing-newline counting trap
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "calls.jsonl"
        f.write_text("\n".join(json.dumps(dict(r, slot=2)) for r in rows))   # no trailing \n
        sys.path.insert(0, str(ROOT.parent / "train-corpus"))
        import importlib
        os.environ["RSM_LEDGER_ROLLUP_TEST"] = "1"
        back = [json.loads(l) for l in open(f) if l.strip()]
        check("calls.jsonl no-trailing-newline read", len(back) == len(rows), f"got {len(back)}")
        s2 = L.summarize(back)
        check("round-trip totals equal", s2["tok_raw_total"] == raw)
        importlib.invalidate_caches()


def test_cached_estimator():
    """cached_tokens missing -> longest-common-prefix estimator, floored to the block size."""
    L._PREV_PROMPT.clear()
    base = "A" * 3200            # ~1000 tokens at 3.2 chars/token
    os.environ["RSM_LEDGER"] = "1"
    os.environ["RSM_LEDGER_BLOCK"] = "16"
    try:
        L.begin_session(run_id="t", chain="c", slot=1, instance="i", arm="a", out=None)
        r1 = L.record("note_writer", body={"model": "m", "messages": [{"role": "user", "content": base}]},
                      usage={"prompt_tokens": 1000, "completion_tokens": 10}, endpoint="ep")
        check("first call has no prefix", r1["cached_tokens"] == 0 and r1["cached_src"] == "none",
              f"got {r1['cached_tokens']}/{r1['cached_src']}")
        r2 = L.record("note_writer", body={"model": "m", "messages": [{"role": "user", "content": base + "BBBB"}]},
                      usage={"prompt_tokens": 1002, "completion_tokens": 10}, endpoint="ep")
        check("lcp estimate fires", r2["cached_src"] == "lcp_estimate", f"got {r2['cached_src']}")
        # ceil(3200/3.2) = 1000 tokens of shared prefix, floored to the 16-token block -> 992
        check("lcp estimate value", r2["cached_tokens"] == 992 and r2["cached_tokens"] % 16 == 0,
              f"got {r2['cached_tokens']}")
        check("uncached = prompt - cached",
              r2["uncached_prompt_tokens"] == r2["prompt_tokens"] - r2["cached_tokens"])
        # usage present -> src=usage and the estimator is not used
        r3 = L.record("actor", body={"model": "m", "messages": []},
                      usage={"prompt_tokens": 500, "completion_tokens": 5,
                             "prompt_tokens_details": {"cached_tokens": 480}}, endpoint="ep")
        check("usage path", (r3["cached_src"], r3["cached_tokens"]) == ("usage", 480),
              f"got {r3['cached_src']}/{r3['cached_tokens']}")
        # cached_tokens: 0 from a 0.27.x server is distinguishable from "switch off"
        r4 = L.record("actor", body={"model": "m", "messages": []},
                      usage={"prompt_tokens": 500, "completion_tokens": 5,
                             "prompt_tokens_details": {"cached_tokens": 0}}, endpoint="ep")
        check("cached_tokens=0 stays 'usage'", (r4["cached_src"], r4["cached_tokens"]) == ("usage", 0),
              f"got {r4['cached_src']}/{r4['cached_tokens']}")
        s = L.summarize(L.rows())
        check("summary over recorded rows", s["n_calls_actor"] == 2 and s["n_calls_writer"] == 2,
              f"got {s['n_calls_actor']}/{s['n_calls_writer']}")
    finally:
        _clear_env("RSM_LEDGER", "RSM_LEDGER_BLOCK")
        L.begin_session()


def test_injected_tokens():
    from memory.base import MEM_WRAP_HEAD
    payload = "FILES: a.py\nDID: x | WHERE: a.py:1"
    text = f"{MEM_WRAP_HEAD}{payload}\n</memory_context>\n\nthe next task statement"
    check("injected payload extracted", L.injected_payload(text).strip() == payload,
          f"got {L.injected_payload(text)!r}")
    n, src = L.count_injected(text)
    check("injected estimate", src == "estimate" and n == round(len(payload + "\n") / 3.6),
          f"got {n}/{src}")
    check("no memory context -> 0", L.count_injected("plain task") == (0, "none"))


# ---------------------------------------------------------------- 4. role rules

def test_role_rules():
    import memory.baselines.reasoningbank as RB
    import memory.ours.fusednote as FN
    import memory.attribution.probes as PR
    # no drift: the literals in ledger.py must still be prefixes of the live constants
    check("JUDGE_SYS no drift", RB.JUDGE_SYS.startswith(L.JUDGE_SYS_PREFIX), RB.JUDGE_SYS[:40])
    check("READER_SYS no drift", FN.READER_SYS.startswith(L.READER_SYS_PREFIX), FN.READER_SYS[:40])
    check("READER_SYS_V5 no drift", FN.READER_SYS_V5.startswith(L.READER_SYS_PREFIX))
    check("STATENOTE_SYS no drift", PR.STATENOTE_SYS.startswith(L.STATENOTE_SYS_PREFIX))
    # replayed call-site system prompts -> expected role
    P = ROOT / "memory/ours/prompts"
    B = ROOT / "memory/baselines/prompts"
    cases = [
        ((P / "writer_prompt_v5_sharp.txt").read_text(), "", False, "note_writer"),   # v5knote / v5inline fallback
        ((P / "writer_prompt_v4_skel.txt").read_text(), "", False, "note_writer"),    # v4knote
        ((P / "fused_writer_prompt.txt").read_text(), "", False, "note_writer"),      # fusednote / trainednote
        ((B / "memop_memory_prompt.txt").read_text(), "", False, "note_writer"),      # memop
        ((B / "rb_swe_succ.txt").read_text(), "", False, "note_writer"),              # rbank extract
        ((B / "cline_membank_instructions.txt").read_text(), "", False, "note_writer"),
        (RB.JUDGE_SYS, "", False, "judge"),                                           # rbankj judge
        (FN.READER_SYS, "", False, "memory_retrieve"),                                # reader gate v1
        (FN.READER_SYS_V5, "", False, "memory_retrieve"),                             # reader gate v5
        (PR.STATENOTE_SYS, "", False, "memory_extract"),                              # statenote
        ("You are a smart memory manager", "", True, "memory_extract"),               # mem0 via openai SDK
        ("something nobody registered", "", False, "other"),
    ]
    for sysm, ep, sdk, want in cases:
        got = L.role_of(sysm, endpoint=ep, sdk=sdk)
        check(f"role_of({sysm[:28]!r})", got == want, f"got {got} want {want}")
    # reader endpoint rule
    os.environ["RSM_READER_API"] = "http://127.0.0.1:1234/v1"
    os.environ["RSM_API_BASE"] = "http://127.0.0.1:8013/v1"
    check("reader endpoint rule", L.role_of("anything at all", endpoint="127.0.0.1:1234") == "memory_retrieve")
    # same host as the actor -> the endpoint rule must NOT fire (a self-written note would be mislabelled)
    os.environ["RSM_READER_API"] = "http://127.0.0.1:8013/v1"
    check("reader endpoint rule off on shared host",
          L.role_of("anything at all", endpoint="127.0.0.1:8013") == "other")
    _clear_env("RSM_READER_API")
    # explicit context wins
    with L.role_ctx("note_writer", note_turn=True):
        check("role_ctx forces", L.role_of(RB.JUDGE_SYS) == "note_writer")


# ---------------------------------------------------------------- 5. request bytes unchanged

class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b


def _capture_distill_body():
    """Run distill_fused_note against a stubbed urlopen and return sha256 of the request bytes."""
    import urllib.request
    import memory.ours.fusednote as FN
    seen = {}
    real = urllib.request.urlopen

    def stub(req, *a, **kw):
        seen.setdefault("bodies", []).append(bytes(req.data))
        return _FakeResp({"choices": [{"message": {"content": "FILES: a.py\nDID: x | WHERE: a.py:1"},
                                       "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                    "prompt_tokens_details": {"cached_tokens": 0}}})

    urllib.request.urlopen = stub
    try:
        note = FN.distill_fused_note("ASSISTANT:\nhello\n\nFINAL DIFF:\ndiff --git a b",
                                     prompt_file="writer_prompt_v5_sharp.txt",
                                     tags=FN.V4_TAGS, first_tag_hint="FILES:")
    finally:
        urllib.request.urlopen = real
    return note, [hashlib.sha256(b).hexdigest() for b in seen.get("bodies", [])]


def test_request_bytes_unchanged():
    os.environ.setdefault("RSM_API_BASE", "http://127.0.0.1:9/v1")
    _clear_env("RSM_LEDGER", "RSM_WRITER_API", "RSM_WRITER_MODEL")
    note_off, sha_off = _capture_distill_body()
    # switches ON: install the ledger and re-run; the ledger only watches
    os.environ["RSM_LEDGER"] = "1"
    L._INSTALLED["done"] = False
    L.install()
    L.begin_session(run_id="t", chain="c", slot=2, instance="i", arm="a", out=None)
    note_on, sha_on = _capture_distill_body()
    _clear_env("RSM_LEDGER")
    check("distill request bytes identical", sha_off == sha_on and len(sha_off) == 1,
          f"off={sha_off} on={sha_on}")
    check("distill output identical", note_off == note_on, f"{note_off!r} vs {note_on!r}")


def test_urlopen_wrapper_transparent():
    """The urllib patch must hand the caller back byte-identical response content."""
    import urllib.request
    payload = {"choices": [{"message": {"content": "FILES: a.py"}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 7, "completion_tokens": 2,
                         "prompt_tokens_details": {"cached_tokens": 4}}}
    real = urllib.request.urlopen
    urllib.request.urlopen = lambda req, *a, **kw: _FakeResp(payload)
    os.environ["RSM_LEDGER"] = "1"
    try:
        L._INSTALLED["done"] = False
        L._patch_urllib()
        L.begin_session(run_id="t", chain="c", slot=2, instance="i", arm="a", out=None)
        req = urllib.request.Request("http://127.0.0.1:9/v1/chat/completions",
                                     json.dumps({"model": "m", "messages": [
                                         {"role": "system", "content": "You are a helpful assistant that judges x"},
                                         {"role": "user", "content": "hi"}]}).encode(),
                                     {"Content-Type": "application/json"})
        got = json.load(urllib.request.urlopen(req, timeout=5))
        check("wrapper returns same json", got == payload, f"got {got}")
        rows = L.rows()
        check("wrapper recorded one row", len(rows) == 1, f"got {len(rows)}")
        check("wrapper role", rows and rows[0]["role"] == "judge", rows and rows[0]["role"])
        check("wrapper cached", rows and rows[0]["cached_tokens"] == 4)
    finally:
        urllib.request.urlopen = real
        _clear_env("RSM_LEDGER")
        L._INSTALLED["done"] = False
        L.begin_session()


# ------------------------------------------------- 6. every patched line is guarded

GUARD_TOKENS = ("_LEDGER", "INL", "inline_self_write", "pending_inline", "RSM_LEDGER")


def test_runner_patch_is_guarded():
    import difflib
    new = ROOT / "harness/mini_swe_agent.py"
    old = ROOT / "harness/mini_swe_agent.py.bak_0922_inline"
    if not old.exists():
        check("runner backup present", False, str(old))
        return
    added, removed = [], []
    for l in difflib.unified_diff(old.read_text().splitlines(), new.read_text().splitlines(), n=0):
        if l.startswith("+++") or l.startswith("---") or l.startswith("@@"):
            continue
        if l.startswith("+"):
            added.append(l[1:])
        elif l.startswith("-"):
            removed.append(l[1:])
    check("no line removed from the runner", not removed, str(removed[:3]))
    bad = [l for l in added
           if l.strip() and not l.lstrip().startswith("#")
           and not any(t in l for t in GUARD_TOKENS)]
    check("every added runner line is guarded or a comment", not bad, str(bad[:3]))
    mem = ROOT / "memory/__init__.py"
    memold = ROOT / "memory/__init__.py.bak_0922_inline"
    if memold.exists():
        extra = [l for l in mem.read_text().splitlines()
                 if l.strip() and l not in memold.read_text().splitlines()]
        check("memory/__init__ adds only the registration import",
              len(extra) == 1 and "inline_note" in extra[0], str(extra))


# ---------------------------------------------------------------- 7. registry

def test_registry():
    import memory
    from memory import base as MB
    check("v5inline registered", "v5inline" in MB.REGISTRY, sorted(MB.REGISTRY))
    check("v5knote still registered", "v5knote" in MB.REGISTRY)
    cls = MB.REGISTRY["v5inline"]
    check("v5inline flags", getattr(cls, "inline_self_write", False) and getattr(cls, "llm_writer", False))
    check("v5knote has no inline flag", not getattr(MB.REGISTRY["v5knote"], "inline_self_write", False))
    check("v5inline is a V5-family layer",
          issubclass(cls, memory.ours.fusednote.V4KNote) and issubclass(cls, memory.ours.fusednote.ShuffleWrapMixin))
    # fusednote.py untouched by this change
    fn = (ROOT / "memory/ours/fusednote.py").read_text()
    check("fusednote.py has no inline code", "inline" not in fn.lower())


def test_layer_write_paths():
    """The layer's write(): inline note is used as-is; anything else falls back to the v5knote path."""
    from memory import base as MB
    import memory.ours.inline_note as MI
    spec = {"swebench_instance_id": "repo__x-1"}
    lay = MB.REGISTRY["v5inline"]()
    lay.pending_inline = {"note": "FILES: a.py\nDID: x | WHERE: a.py:1", "mode": "inline",
                          "reason": "ok", "retries": 0, "n_note_turns": 1,
                          "prompt_tokens": 100, "cached_tokens": 95, "prompt_sha": "abc123def456"}
    n = lay.write(spec, "material", "delta", None)
    check("inline note stored", n == 1 and lay.store.items[0][1].startswith("FILES:"))
    check("rec_extra mode", lay.rec_extra["note_write_mode"] == "inline")
    check("rec_extra sha", lay.rec_extra["note_prompt_sha"] == "abc123def456")
    check("rec_extra cached", lay.rec_extra["note_cached_tokens"] == 95)
    check("pending cleared", lay.pending_inline is None)
    # fallback: the layer calls the v5knote path (stubbed here)
    calls = {}
    real = MI.distill_fused_note

    def stub(material, prompt_file=None, tags=None, first_tag_hint=None):
        calls.update(prompt_file=prompt_file, tags=tags, first_tag_hint=first_tag_hint)
        return "FILES: b.py\nSTATUS: submitted"

    MI.distill_fused_note = stub
    try:
        lay2 = MB.REGISTRY["v5inline"]()
        lay2.pending_inline = {"note": "", "mode": "fallback_separate", "reason": "invalid_twice",
                               "retries": 1, "n_note_turns": 2}
        n2 = lay2.write(spec, "material", "delta", None)
    finally:
        MI.distill_fused_note = real
    check("fallback writes a note", n2 == 1)
    check("fallback uses the v5 sharp prompt", calls.get("prompt_file") == "writer_prompt_v5_sharp.txt",
          str(calls))
    check("fallback first tag hint", calls.get("first_tag_hint") == "FILES:")
    check("fallback mode recorded", lay2.rec_extra["note_write_mode"] == "fallback_separate")
    check("fallback reason recorded", lay2.rec_extra["note_write_reason"] == "invalid_twice")


def test_runner_module_switch():
    """Importing the runner without RSM_LEDGER must leave _LEDGER None (the module is not even loaded)."""
    import subprocess
    code = ("import sys; sys.path.insert(0, %r); import harness.mini_swe_agent as M; "
            "print('LEDGER_IS', M._LEDGER is None, 'ledger' in sys.modules or 'harness.ledger' in sys.modules)"
            % str(ROOT))
    env = dict(os.environ); env.pop("RSM_LEDGER", None)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    check("runner _LEDGER is None when off", "LEDGER_IS True False" in r.stdout,
          r.stdout.strip() + r.stderr[-200:])
    env["RSM_LEDGER"] = "1"
    r2 = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    check("runner _LEDGER loaded when on", "LEDGER_IS False True" in r2.stdout,
          r2.stdout.strip() + r2.stderr[-200:])


def test_results_key_set_unchanged():
    """The rec dict literal in the runner is byte-identical to the backup, so an old arm's
    results.jsonl key set cannot change. Checked against a real completed arm when one is present."""
    import re
    new = (ROOT / "harness/mini_swe_agent.py").read_text()
    old = (ROOT / "harness/mini_swe_agent.py.bak_0922_inline").read_text()
    pat = re.compile(r'rec = \{"order".*?"crashed": bool\(err\)\}', re.S)
    a, b = pat.search(old), pat.search(new)
    check("rec literal unchanged", a and b and a.group(0) == b.group(0))
    blank = re.compile(r'def _blank_rec.*?\Z', re.S)
    check("_blank_rec unchanged",
          blank.search(old).group(0) == blank.search(new).group(0))
    # an existing completed arm must still parse with the documented key set
    import glob
    cands = sorted(glob.glob(str(ROOT / "runs/ft1_grpo/*__seq_seqB/results.jsonl")))
    if cands:
        keys = set()
        for l in open(cands[0]):
            if l.strip():
                keys |= set(json.loads(l))
        new_keys = {"note_write_mode", "note_write_reason", "n_note_turns", "note_chars",
                    "note_retries", "note_prompt_tokens", "note_cached_tokens", "note_prompt_sha",
                    "tok_raw_total", "tok_cacheaware_total", "cost_usd_priced", "injected_tokens",
                    "ledger_ver", "price_table_sha"}
        check("no new key leaked into an old run", not (keys & new_keys), str(keys & new_keys))


def test_ledger_off_is_inert():
    _clear_env("RSM_LEDGER")
    check("on() false", not L.on())
    check("session_summary empty when off", L.session_summary() == {})
    check("record returns None when off", L.record("actor", usage={"prompt_tokens": 1}) is None)


# ---------------------------------------------------------------- runner

def main():
    for fn in (test_parser, test_state_machine, test_rollup, test_cached_estimator,
               test_injected_tokens, test_role_rules, test_request_bytes_unchanged,
               test_urlopen_wrapper_transparent, test_runner_patch_is_guarded,
               test_registry, test_layer_write_paths, test_runner_module_switch,
               test_results_key_set_unchanged, test_ledger_off_is_inert):
        print(f"\n== {fn.__name__}")
        fn()
    print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURES"))
    for f in FAILURES:
        print("  -", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
