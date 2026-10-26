"""Remove API keys from run artifacts. mini-swe-agent saves its model config (including `api_key`) into every
traj_*.json; a live run through a gateway would otherwise persist the gateway key on disk and in git.
Called by demo/server.py after each live run and by demo/custom_runner.py; also usable by hand:

    python demo/scrub.py runs/<run-id>            # scrubs every traj_*.json below the path
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

REDACTED = "REDACTED"
# Only the gateway key shape we actually use (gv_<16 hex>_<secret>) plus the explicit keys passed in. Generic
# patterns such as `sk-…` are deliberately NOT matched: recorded trajectories legitimately contain such strings
# (test fixtures in the repositories under study), and rewriting the paper's run data is not acceptable.
_KEY_SHAPES = [re.compile(r"gv_[0-9a-f]{16}_[A-Za-z0-9_-]{20,}")]


def scrub_obj(o, keys=()):
    """Replace `api_key`-like fields and any key-shaped strings; returns (obj, n_replacements)."""
    n = 0
    if isinstance(o, dict):
        for k, v in list(o.items()):
            if k in ("api_key", "apiKey", "authorization", "Authorization") and isinstance(v, str) and v not in ("", "EMPTY", REDACTED):
                o[k] = REDACTED; n += 1
            else:
                o[k], m = scrub_obj(v, keys); n += m
    elif isinstance(o, list):
        for i, v in enumerate(o):
            o[i], m = scrub_obj(v, keys); n += m
    elif isinstance(o, str):
        for k in keys:
            if k and k in o:
                o = o.replace(k, REDACTED); n += 1
        for rx in _KEY_SHAPES:
            o, m = rx.subn(REDACTED, o); n += m
    return o, n


def scrub_file(path, keys=()):
    p = pathlib.Path(path)
    try:
        if p.suffix == ".json":
            d = json.loads(p.read_text())
            d, n = scrub_obj(d, keys)
            if n:
                p.write_text(json.dumps(d))
            return n
        text = p.read_text(errors="replace"); n = 0
        for k in keys:
            if k and k in text:
                text = text.replace(k, REDACTED); n += 1
        for rx in _KEY_SHAPES:
            text, m = rx.subn(REDACTED, text); n += m
        if n:
            p.write_text(text)
        return n
    except Exception as e:
        print(f"SCRUB_ERROR {p}: {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
        return 0


def scrub_dir(root, keys=()):
    total = 0
    for p in pathlib.Path(root).rglob("*"):
        if p.is_file() and (p.name.startswith("traj_") or p.suffix in (".jsonl", ".log", ".json")):
            total += scrub_file(p, keys)
    return total


if __name__ == "__main__":
    import os
    ks = [os.environ.get(k, "") for k in ("GOODVISION_API_KEY", "RSM_API_KEY")]
    for root in sys.argv[1:]:
        print(f"{root}: {scrub_dir(root, ks)} replacements")
