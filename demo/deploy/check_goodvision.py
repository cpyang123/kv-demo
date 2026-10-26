"""GoodVision SmartRouter connectivity check — used by CI (.github/workflows) and by hand.

Exit codes: 0 = key works and at least one model answers; 1 = key missing/invalid or no model answers;
2 = key works but something a demo needs is degraded (a model family blocked upstream, tool calls refused)
— the workflow treats 2 as a warning. Facts about the API: demo/goodvision.md.

  GOODVISION_API_KEY=… python demo/deploy/check_goodvision.py [--base https://api-test.goodvision.tech/v1] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def call(base, key, method, path, body=None, timeout=60):
    root = base.rstrip("/")
    if root.endswith("/v1"):                       # accept either the API origin or the OpenAI-protocol base URL
        root = root[:-3]
    req = urllib.request.Request(root + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except Exception:
            return e.code, {"error": {"code": str(e.code), "message": raw[:200]}}, dict(e.headers)
    except Exception as e:
        return 0, {"error": {"code": type(e).__name__, "message": str(e)[:200]}}, {}


def err(body):
    e = (body.get("error") if isinstance(body, dict) else None) or {}
    return e.get("code") or e.get("type") or "?", (e.get("message") or "")[:160]


def run_checks(base, key):
    out = {"base": base, "has_key": bool(key), "ok": False, "degraded": [], "models": [], "families": {}, "routing": None, "tools": None}
    if not key:
        out["error"] = "GOODVISION_API_KEY is not set"
        return out
    st, body, _ = call(base, key, "GET", "/v1/models", timeout=30)
    if st != 200:
        out["error"] = "models: %s — %s" % err(body)
        return out
    out["models"] = sorted(m.get("id") for m in body.get("data", []) if isinstance(m, dict) and m.get("id"))
    if not out["models"]:
        out["error"] = "key accepted but the model market is empty (routing policy without model offers)"
        return out
    ping = {"messages": [{"role": "user", "content": "Say OK."}], "max_completion_tokens": 16}
    # one model per family + auto: what the harness will route to
    for fam, pref in (("openai", "gpt-"), ("anthropic", "claude-")):
        mid = next((m for m in out["models"] if m.startswith(pref)), None)
        if not mid:
            continue
        st, body, hdr = call(base, key, "POST", "/v1/chat/completions", {"model": mid, **ping})
        out["families"][fam] = {"model": mid, "ok": st == 200, "served": body.get("model") if st == 200 else None,
                                "error": None if st == 200 else "%s — %s" % err(body)}
        if st != 200:
            out["degraded"].append(f"{fam} ({mid}) blocked: {out['families'][fam]['error']}")
    st, body, hdr = call(base, key, "POST", "/v1/chat/completions", {"model": "auto", **ping})
    out["routing"] = {"ok": st == 200, "served": body.get("model") if st == 200 else None,
                      "selected": hdr.get("x-gv-selected-model"), "mode": hdr.get("x-gv-route-mode"), "decision": hdr.get("x-gv-decision-id"),
                      "error": None if st == 200 else "%s — %s" % err(body)}
    if st != 200:
        out["degraded"].append(f"auto routing failed: {out['routing']['error']}")
    # tool calling (Codex / function-calling harnesses need it; the text-based ChainSWE harness does not)
    st, body, _ = call(base, key, "POST", "/v1/chat/completions", {**ping, "model": out["models"][0] if "gpt-5.4-mini" not in out["models"] else "gpt-5.4-mini",
                       "tools": [{"type": "function", "function": {"name": "shell", "parameters": {"type": "object", "properties": {}}}}]})
    out["tools"] = {"ok": st == 200, "error": None if st == 200 else "%s — %s" % err(body)}
    if st != 200:
        out["degraded"].append(f"tool calls refused: {out['tools']['error']}")
    out["ok"] = any(f["ok"] for f in out["families"].values()) or out["routing"]["ok"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("GOODVISION_TEST_API_BASE", "https://api-test.goodvision.tech/v1"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = run_checks(a.base, os.environ.get("GOODVISION_API_KEY", ""))
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        print(f"GoodVision {a.base}: {'OK' if res['ok'] else 'FAILED'}" + (f" — {res['error']}" if res.get("error") else ""))
        if res["models"]:
            print(f"  market: {len(res['models'])} models")
        for fam, f in res["families"].items():
            print(f"  {fam:9s} {f['model']:18s} {'ok, served ' + str(f['served']) if f['ok'] else 'BLOCKED: ' + str(f['error'])}")
        if res["routing"]:
            r = res["routing"]; print(f"  auto      {'ok -> ' + str(r['served']) + ' (selected ' + str(r['selected']) + ')' if r['ok'] else 'FAILED: ' + str(r['error'])}")
        if res["tools"]:
            print(f"  tools     {'ok' if res['tools']['ok'] else 'refused: ' + str(res['tools']['error'])}")
        for d in res["degraded"]:
            print(f"  ⚠ {d}")
    if not res["ok"]:
        return 1
    return 2 if res["degraded"] else 0


if __name__ == "__main__":
    sys.exit(main())
