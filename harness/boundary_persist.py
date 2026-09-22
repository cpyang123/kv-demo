"""Boundary persistence for the ChainSWE attribution generation pass (part A).

Implements chainswe/attribution/BOUNDARY_FORMAT.md (frozen 2026-09-04) — read that first.
Pure stdlib; performs no container/GPU work of its own: every git call goes through the
harness env (`env.raw`, i.e. git *inside the task image*), so the persisted diffs are the
image git's bytes, not the host git's.

Gate: RSM_PERSIST_BOUNDARIES=1 (default off => the exam path is unchanged; PROTO_TAG untouched).

Files per boundary k (k = 1..K-1; a boundary exists only where task k+1 exists), written by
ChainRunner._persist_boundary into the seq run dir after _commit_task(k) and after grading:
  boundary_<k>.json     metadata (BOUNDARY_KEYS; sha1s only — the diff text never enters JSON)
  boundary_<k>.cumdiff  git diff --binary <base_oid> HEAD           (tree right after task k)
  delta_<k>.diff        git diff --binary <head_before_k> HEAD      (task k's own delta; C5)
  material_<k>.txt      the exact string ChainRunner._material(agent, delta) returned
  scratch_<k>.json      {"written": [...], "exists_at_boundary": {path: bool}}  (C7 source)

Invariants:
  * every write is tmp + os.replace; boundary_<k>.json is written LAST, so its presence
    means the boundary is complete (the verifier keys off it);
  * the diff bytes are produced with `git diff --binary A B > .git/rsm_tmp/<f>` inside the
    image and read back from the host side of the bind mount.  env.raw() decodes stdout as
    UTF-8 with errors="replace", which would silently corrupt non-UTF-8 hunks and break the
    replay driver's byte-identical sha1 assertion.  `.git/rsm_tmp/` is the directory
    grading.apply_patch already uses: invisible to `git add -A` / `git diff`, so nothing
    leaks into the next boundary's tree, delta or scratch manifest;
  * nothing here writes into the repo working tree.
"""
import hashlib
import json
import os
import pathlib
import posixpath
import re
from datetime import datetime, timezone

BOUNDARY_KEYS = ("chain", "k", "instance_k", "instance_next", "base_oid", "head_before_k",
                 "head_after_k", "exit_status", "api_calls", "n_assistant_msgs", "resolved",
                 "material_chars", "cumdiff_sha1", "delta_sha1", "proto", "actor_model",
                 "created_utc")

FILE_PATTERNS = ("boundary_*.json", "boundary_*.cumdiff", "delta_*.diff",
                 "material_*.txt", "scratch_*.json")

_OID_RE = re.compile(r"^[0-9a-f]{40}$")


def persist_enabled():
    return os.environ.get("RSM_PERSIST_BOUNDARIES") == "1"


def boundary_files(out, k):
    out = pathlib.Path(out)
    return {"json": out / f"boundary_{k}.json",
            "cumdiff": out / f"boundary_{k}.cumdiff",
            "delta": out / f"delta_{k}.diff",
            "material": out / f"material_{k}.txt",
            "scratch": out / f"scratch_{k}.json"}


# ---------- atomic IO ----------

def atomic_write_bytes(path, data):
    path = pathlib.Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def sha1_bytes(data):
    return hashlib.sha1(data).hexdigest()


def clear_stale(out, log=print):
    """A chain that reaches ChainRunner.run() is (re)generated from task 1 in a fresh
    workspace, so boundary files left by an earlier (aborted) attempt describe a different
    tree.  Remove them so a chain that ends earlier this time cannot leave stale k's behind."""
    n = 0
    for pat in FILE_PATTERNS + tuple(p + ".tmp" for p in FILE_PATTERNS):
        for f in sorted(pathlib.Path(out).glob(pat)):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    if n:
        log(f"[persist] removed {n} stale boundary file(s) in {out}")
    return n


# ---------- git (inside the image, via env.raw) ----------

def git_diff_bytes(env, host_repo, rev_a, rev_b, name):
    """`git diff --binary rev_a rev_b` run by the image git, captured byte-exact through a
    file in .git/rsm_tmp/ (bind-mounted; see module docstring).  Returns bytes."""
    for rev in (rev_a, rev_b):
        if not (rev == "HEAD" or _OID_RE.match(rev or "")):
            raise ValueError(f"refusing non-oid revision {rev!r}")
    tmpdir = pathlib.Path(host_repo) / ".git" / "rsm_tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    hostf = tmpdir / name
    if hostf.exists():
        hostf.unlink()
    r = env.raw(f"git diff --binary {rev_a} {rev_b} > .git/rsm_tmp/{name}")
    try:
        if r["returncode"] != 0:
            raise RuntimeError(f"git diff --binary {rev_a[:12]} {rev_b[:12]} rc={r['returncode']}: "
                               f"{(r.get('output') or '')[-300:]}")
        if not hostf.exists():
            raise RuntimeError(f"git diff wrote nothing visible on host: {hostf}")
        return hostf.read_bytes()
    finally:
        try:
            hostf.unlink()
        except OSError:
            pass


# ---------- scratch manifest (channel C7 source) ----------
# Heuristic, pre-registered shape (PREREGISTRATION.md D9 / channels_spec._WRITE_REDIR):
# paths the session *wrote* = `>`/`>>`/`&>` targets, `tee` files, `cat > p <<EOF`, heredoc
# targets, and string-literal paths in `python -c` / `python <<EOF` bodies opened with a
# write mode or written via Path(...).write_text/.write_bytes.

_ACTION_RE = re.compile(r"```mswea_bash_command\s*\n(.*?)\n```", re.DOTALL)  # == LitellmTextbasedModelConfig.action_regex
_HEREDOC_RE = re.compile(r"<<-?\s*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\?([A-Za-z_][\w.-]*))")
_SPLIT_RE = re.compile(r"&&|\|\||\||;|\n|(?<![<>\d])&(?![>&])")
_TOK = r"(?:\$\([^)]*\)|[^\s|&;<>()])+"          # a word; swallows $(...) so the filter can reject it
_REDIR_RE = re.compile(r"(?<![<>])(?:\d?>>?|&>>?|>\|)\s*(\"[^\"\n]*\"|'[^'\n]*'|" + _TOK + ")")
_TEE_RE = re.compile(r"(?:^|[\s(])tee\s+((?:-{1,2}[\w=-]+\s+)*)((?:" + _TOK + r"\s*)+)")
_PYC_RE = re.compile(r"\bpython[\d.]*\s+(?:-[a-zA-Z]+\s+)*-c\s+(?:'([^']*)'|\"((?:[^\"\\]|\\.)*)\"|(\S+))",
                     re.DOTALL)
_PY_HEAD_RE = re.compile(r"(?:^|[\s(])python[\d.]*(?:\s|$)")
_PY_OPEN_RE = re.compile(r"\bopen\(\s*([rRbBuUfF]{0,2})(['\"])([^'\"\n]+)\2\s*,\s*(?:mode\s*=\s*)?"
                         r"[rRbBuU]?(['\"])([rwaxbt+]{1,4})\4")
_PY_PATH_RE = re.compile(r"\bPath\(\s*([rRbBuUfF]{0,2})(['\"])([^'\"\n]+)\2\s*\)\s*\.write_(?:text|bytes)\(")
_SKIP_PREFIX = ("/dev/", "/proc/", "/sys/")
# tokens the exists-probe may leave unquoted so the image shell expands ~ and $VAR
# (assignment context: no word splitting, no globbing; braces only as ${VAR})
_SAFE_UNQUOTED = re.compile(r"^(?:[\w./~@%+:=\-]|\$\w+|\$\{\w+\})+$")


def extract_commands(messages):
    """Commands the session executed, in order.  Assistant messages carry them in
    extra.actions (mini-swe-agent 2.x); fall back to the action regex on the content."""
    cmds = []
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        acts = (m.get("extra") or {}).get("actions")
        if acts is not None:
            cmds += [a.get("command", "") for a in acts if isinstance(a, dict)]
        elif isinstance(m.get("content"), str):
            found = [a.strip() for a in _ACTION_RE.findall(m["content"])]
            if len(found) == 1:          # exactly-one rule: anything else was a FormatError, never executed
                cmds += found
    return [c for c in cmds if isinstance(c, str) and c.strip()]


def _split_heredocs(cmd):
    """Return (shell text with heredoc bodies removed, [body, ...] in order of appearance)."""
    lines = cmd.split("\n")
    out, bodies, i = [], [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        tags = [(m.group(1) or m.group(2) or m.group(3)) for m in _HEREDOC_RE.finditer(line)]
        i += 1
        for tag in tags:
            body = []
            while i < len(lines):
                cur = lines[i]
                i += 1
                if cur.strip() == tag:
                    break
                body.append(cur)
            bodies.append("\n".join(body))
    return "\n".join(out), bodies


def _resolve(p, cwd):
    if p.startswith("/"):
        return posixpath.normpath(p)
    if p.startswith("~") or "$" in p:
        return p                      # left for the image shell to expand at probe time
    joined = posixpath.join(cwd, p)
    return posixpath.normpath(joined) if joined.startswith("/") else joined


def _clean(tok, cwd):
    t = tok.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        t = t[1:-1]
    elif "\"" in t or "'" in t:       # unbalanced quote: `>` was inside a string literal
        return ""
    t = t.strip()
    if not t or t == "-" or t[0] in "&(<" or t.startswith(_SKIP_PREFIX):
        return ""
    if "`" in t or "$(" in t or t.endswith("$"):
        return ""                     # command substitution: not a literal path
    return _resolve(t, cwd)


def _clean_py(prefix, lit, cwd):
    if "f" in prefix.lower() and "{" in lit:
        return ""                     # f-string path: not a literal
    return _clean(lit, cwd)


_PYC_SLOT_RE = re.compile(r"__RSMPYC(\d+)__")


def _paths_in_command(cmd, cwd):
    shell, bodies = _split_heredocs(cmd)
    bodies = list(bodies)
    # stash `python -c "<body>"` bodies BEFORE splitting on ; | && (bodies routinely contain `;`)
    pyc = []

    def _stash(m):
        pyc.append(m.group(1) or m.group(2) or m.group(3) or "")
        return f" __RSMPYC{len(pyc) - 1}__ "
    shell = _PYC_RE.sub(_stash, shell)
    found, cur = [], cwd
    for seg in _SPLIT_RE.split(shell):
        seg = seg.strip().lstrip("({ ").strip()
        if not seg:
            continue
        toks = seg.split()
        if toks[0] in ("cd", "pushd"):
            target = toks[1] if len(toks) > 1 and not toks[1].startswith("-") else ""
            cur = _resolve(target.strip("\"'"), cur) if target else "~"
            continue
        seg_bodies = [bodies.pop(0) for _ in _HEREDOC_RE.finditer(seg) if bodies]
        pybodies = [pyc[int(i)] for i in _PYC_SLOT_RE.findall(seg)]
        if seg_bodies and _PY_HEAD_RE.search(seg):
            pybodies += seg_bodies
        for m in _REDIR_RE.finditer(seg):
            found.append(_clean(m.group(1), cur))
        for m in _TEE_RE.finditer(seg):
            for t in m.group(2).split():
                found.append(_clean(t, cur))
        for body in pybodies:
            for m in _PY_OPEN_RE.finditer(body):
                if any(ch in m.group(5) for ch in "wax+"):
                    found.append(_clean_py(m.group(1), m.group(3), cur))
            for m in _PY_PATH_RE.finditer(body):
                found.append(_clean_py(m.group(1), m.group(3), cur))
    return [p for p in found if p]


def parse_written_paths(commands, cwd="/"):
    """Ordered, de-duplicated list of paths the commands wrote (resolved against `cwd`, the
    per-command starting directory = repo_path under env.execute's --pwd; `cd` is tracked
    within a command).  `~…`/`$VAR…` paths are kept verbatim for the probe to expand."""
    written = []
    for cmd in commands or []:
        for p in _paths_in_command(cmd, cwd):
            if p not in written:
                written.append(p)
    return written


_PROBE_RE = re.compile(r"^RSMX (\d+) ([01])\s*$", re.M)


def build_probe_script(paths):
    lines = []
    for i, p in enumerate(paths):
        tok = p if _SAFE_UNQUOTED.match(p) else "'" + p.replace("'", "'\\''") + "'"
        lines.append(f'p={tok}; if [ -n "$p" ] && [ -e "$p" ]; then echo "RSMX {i} 1"; '
                     f'else echo "RSMX {i} 0"; fi')
    return "\n".join(lines) + "\n"


def probe_exists(env, paths):
    """{path: bool} — `test -e` for every path, run in the image with the same --contain
    semantics the session had (so /tmp scratch shows up as gone, exactly the C7 signal)."""
    if not paths:
        return {}
    r = env.raw(build_probe_script(paths))
    got = {int(i): v == "1" for i, v in _PROBE_RE.findall(r.get("output") or "")}
    missing = [p for i, p in enumerate(paths) if i not in got]
    if missing:
        raise RuntimeError(f"exists-probe gave no verdict for {len(missing)} path(s) "
                           f"(rc={r.get('returncode')}): {missing[:3]}")
    return {p: got[i] for i, p in enumerate(paths)}


# ---------- trajectory ----------

def traj_api_calls(traj_path, fallback):
    """(api_calls, source): info.model_stats.api_calls from the saved trajectory, else the
    agent's own counter (== what DefaultAgent.serialize writes; identical by construction)."""
    try:
        v = json.loads(pathlib.Path(traj_path).read_text())["info"]["model_stats"]["api_calls"]
        return int(v), "traj"
    except Exception:
        return fallback, "agent.n_calls"


# ---------- main entry ----------

def persist_boundary(*, out, env, host_repo, chain_id, k, instance_k, instance_next, base_oid,
                     head_before, head_after, exit_status, resolved, api_calls, n_assistant_msgs,
                     agent_messages, material, proto, actor_model, repo_path, log=print):
    """Write the five boundary files for task k into `out`.  Raises on failure of the diff /
    material steps (caller logs and continues); a failed exists-probe only empties the
    verdict dict (logged) so the expensive cumdiff is never lost over a probe hiccup."""
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    files = boundary_files(out, k)
    for oid, nm in ((base_oid, "base_oid"), (head_before, "head_before"), (head_after, "head_after")):
        if not isinstance(oid, str) or not _OID_RE.match(oid):
            raise ValueError(f"{nm} is not a 40-hex oid: {oid!r}")
    if not isinstance(material, str):
        raise TypeError("material must be the str returned by ChainRunner._material")

    cum = git_diff_bytes(env, host_repo, base_oid, "HEAD", f"boundary_{k}.cumdiff")
    delta = git_diff_bytes(env, host_repo, head_before, "HEAD", f"delta_{k}.diff")
    atomic_write_bytes(files["cumdiff"], cum)
    atomic_write_bytes(files["delta"], delta)
    atomic_write_bytes(files["material"], material.encode("utf-8", errors="replace"))

    written = parse_written_paths(extract_commands(agent_messages), repo_path)
    try:
        exists = probe_exists(env, written)
    except Exception as e:   # noqa: BLE001 — probe failure must not cost the boundary
        log(f"[persist] k={k}: exists-probe failed, scratch_{k}.json has no verdicts: "
            f"{type(e).__name__}: {str(e)[:200]}")
        exists = {}
    atomic_write_bytes(files["scratch"], (json.dumps(
        {"written": written, "exists_at_boundary": exists}, indent=1) + "\n").encode("utf-8"))

    meta = {"chain": chain_id, "k": int(k), "instance_k": instance_k, "instance_next": instance_next,
            "base_oid": base_oid, "head_before_k": head_before, "head_after_k": head_after,
            "exit_status": exit_status, "api_calls": api_calls, "n_assistant_msgs": n_assistant_msgs,
            "resolved": resolved, "material_chars": len(material),
            "cumdiff_sha1": sha1_bytes(cum), "delta_sha1": sha1_bytes(delta),
            "proto": proto, "actor_model": actor_model,
            "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    assert tuple(meta) == BOUNDARY_KEYS, "boundary json keys drifted from BOUNDARY_FORMAT.md"
    atomic_write_bytes(files["json"], (json.dumps(meta, indent=1) + "\n").encode("utf-8"))
    return dict(meta, cumdiff_bytes=len(cum), delta_bytes=len(delta),
                n_written=len(written), n_exist=sum(1 for v in exists.values() if v))
