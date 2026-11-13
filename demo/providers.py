"""Model endpoints the demo can run the harness against.

`local`           a vLLM server (OpenAI-compatible, no key) — the paper's setup, served by demo/slurm/serve_actor.sbatch
`goodvision-test` GoodVision SmartRouter, test environment (https://api-test.goodvision.tech/v1)
`goodvision-prod` GoodVision SmartRouter, production (https://api.goodvision.tech/v1)
`azure`           Azure OpenAI (v1 OpenAI-compatible API) for the GPT models: AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY;
                  when configured, GPT ids asked of GoodVision are served here instead (gpt_route()), to save router credit

What is known about the GoodVision API and where it comes from is in demo/goodvision.md. The key is
read from GOODVISION_API_KEY (environment, or demo/.env — git-ignored). `env_for_harness()` turns a
provider + model into the environment `harness/mini_swe_agent.py` needs; the harness changes it relies
on are env-gated (RSM_API_KEY, RSM_OPENAI_STRICT, RSM_FACTS_API_KEY), so nothing changes for local runs.
"""
from __future__ import annotations

import json
import os
import pathlib
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ENV_FILE = pathlib.Path(os.environ.get("DEMO_ENV_FILE", str(HERE / ".env")))


def load_env_file(path=ENV_FILE):
    """KEY=value lines into os.environ (existing variables win). Returns the keys it set."""
    done = []
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
                done.append(k)
    except OSError:
        pass
    return done


load_env_file()


def _azure_base():
    """AZURE_OPENAI_ENDPOINT or AZURE_OPENAI_BASE_URL (https://<resource>.openai.azure.com, with or without a path) -> its v1 OpenAI-compatible base."""
    ep = (os.environ.get("AZURE_OPENAI_ENDPOINT") or os.environ.get("AZURE_OPENAI_BASE_URL") or "").strip().rstrip("/")
    if not ep:
        return None
    if "/openai" in ep:
        ep = ep.split("/openai")[0]
    return ep + "/openai/v1"


def azure_deployments():
    """Model id -> Azure deployment name. AZURE_OPENAI_DEPLOYMENTS="gpt-5.5=my-gpt55,gpt-5.4-mini" (a bare name maps to itself);
    unlisted GPT ids are assumed to be deployed under their own name."""
    out = {}
    for part in os.environ.get("AZURE_OPENAI_DEPLOYMENTS", "").split(","):
        part = part.strip()
        if part:
            k, _, v = part.partition("=")
            out[k.strip()] = (v or k).strip()
    return out

PROVIDERS = {
    "local": {
        "label": "Local vLLM (cluster GPU)",
        "api_base": None,                    # resolved at request time (RSM_API_BASE or the sbatch endpoint file)
        "key_env": None,
        "strict": False,                     # vLLM accepts chat_template_kwargs / max_tokens / temperature 0
        "default_model": os.environ.get("DEMO_SERVED_NAME", "coder-next"),
        "models_hint": [],
        "docs": "demo/slurm/serve_actor.sbatch",
    },
    "goodvision-test": {
        "label": "GoodVision SmartRouter (test)",
        "api_base": os.environ.get("GOODVISION_TEST_API_BASE", "https://api-test.goodvision.tech/v1"),
        "key_env": "GOODVISION_API_KEY",
        "strict": True,
        "default_model": os.environ.get("GOODVISION_MODEL", "claude-sonnet-5"),
        "models_hint": ["auto", "claude-sonnet-5", "gpt-5.6-sol", "gpt-5.2"],   # ids seen in the console's snippets; the market is authoritative
        "docs": "demo/goodvision.md",
        "console": "https://console-test.goodvision.tech/console/router-settings",
        "probe_families": True,              # the router fronts several upstreams: ping one id per family
        "market": True,                      # /models lists the ids requests may name
    },
    "goodvision-prod": {
        "label": "GoodVision SmartRouter (production)",
        "api_base": os.environ.get("GOODVISION_API_BASE", "https://api.goodvision.tech/v1"),
        "key_env": "GOODVISION_API_KEY",
        "strict": True,
        "default_model": os.environ.get("GOODVISION_MODEL", "claude-sonnet-5"),
        "models_hint": ["auto", "claude-sonnet-5", "gpt-5.6-sol", "gpt-5.2"],
        "docs": "demo/goodvision.md",
        "console": "https://console.goodvision.tech",
        "probe_families": True,
        "market": True,
    },
    "azure": {
        "label": "Azure OpenAI (GPT models)",
        "api_base": _azure_base(),
        "key_env": "AZURE_OPENAI_API_KEY",
        "strict": True,                      # GPT-5 family: max_completion_tokens, default temperature
        "default_model": os.environ.get("AZURE_OPENAI_DEFAULT_MODEL", "gpt-5.4"),
        "models_hint": sorted(set(azure_deployments()) | {"gpt-5.5", "gpt-5.4-mini"}),
        "docs": "demo/README.md",
        "probe_families": False,
        "market": False,                     # /models lists base models, not this resource's deployments
    },
}


def azure_ready():
    return bool(PROVIDERS["azure"]["api_base"] and api_key("azure"))


_AZ_HAS = {}           # model id -> (deployed?, checked at)


def azure_has(model):
    """Is `model` deployed on the Azure resource? Ids named in AZURE_OPENAI_DEPLOYMENTS are trusted; others are asked once
    with a 16-token request (a hit is kept for the process, a miss re-checked after 5 min), so a GPT id Azure lacks
    (e.g. gpt-5.5 on 2026-11-05) stays on the router instead of failing with DeploymentNotFound."""
    if model in azure_deployments():
        return True
    hit = _AZ_HAS.get(model)
    if hit and (hit[0] or time.time() - hit[1] < 300):
        return hit[0]
    ok = bool(probe_model("azure", model).get("ok"))
    _AZ_HAS[model] = (ok, time.time())
    return ok


def gpt_route(provider, model):
    """GPT ids asked of a GoodVision endpoint go to Azure when Azure is configured (DEMO_GPT_VIA_AZURE=0 turns it off);
    `auto`, Claude ids and everything else stay where they were asked. -> (provider, model as the endpoint names it)."""
    if (provider or "").startswith("goodvision") and model and str(model).startswith("gpt-") \
            and os.environ.get("DEMO_GPT_VIA_AZURE", "1") != "0" and azure_ready() and azure_has(model):
        provider = "azure"
    if provider == "azure" and model:
        model = azure_deployments().get(model, model)
    return provider, model


def api_key(name):
    p = PROVIDERS[name]
    return os.environ.get(p["key_env"], "") if p.get("key_env") else ""


def _get(url, key=None, timeout=8):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(body)
        except Exception:
            body = {"raw": body[:300]}
        return e.code, body, dict(e.headers)
    except Exception as e:
        return 0, {"error": {"code": type(e).__name__, "message": str(e)[:200]}}, {}


def _post_json(url, key, payload, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(body)
        except Exception:
            body = {"raw": body[:300]}
        return e.code, body, dict(e.headers)
    except Exception as e:
        return 0, {"error": {"code": type(e).__name__, "message": str(e)[:200]}}, {}


def probe_model(name, model, api_base=None):
    """One tiny completion against `model` -> {model, ok, code, message}. The model market (/models) is not the routing
    policy: on 2026-11-01 claude-sonnet-5 was listed but every request for it failed with "No model offer satisfies the
    effective policy", while claude-haiku-4-5 and gpt-5.2 answered. Only a request tells which ids a key can use."""
    p = PROVIDERS[name]
    base = (api_base or p["api_base"] or "").rstrip("/")
    st2, body2, _ = _post_json(base + "/chat/completions", api_key(name), {"model": model, "messages": [{"role": "user", "content": "Say OK."}], "max_completion_tokens": 16}, timeout=60)
    err = (body2.get("error") if isinstance(body2, dict) else None) or {}
    if st2 != 200 and not err and isinstance(body2, dict) and body2.get("raw"):
        err = {"message": body2["raw"]}
    return {"model": model, "ok": st2 == 200, "code": None if st2 == 200 else (err.get("code") or str(st2)),
            "message": None if st2 == 200 else (err.get("message") or "")[:200]}


def check(name, api_base=None):
    """-> {name, api_base, has_key, ok, status, code, message, models, request_id}. Never raises."""
    p = PROVIDERS[name]
    base = (api_base or p["api_base"] or "").rstrip("/")
    key = api_key(name)
    out = {"name": name, "label": p["label"], "api_base": base, "has_key": bool(key), "strict": p["strict"],
           "default_model": p["default_model"], "models_hint": p["models_hint"], "docs": p["docs"],
           "console": p.get("console"), "ok": False, "status": None, "code": None, "message": None, "models": [], "request_id": None}
    if not base:
        out["message"] = "no endpoint configured"
        return out
    status, body, hdr = _get(base + "/models", key or None)
    out["status"] = status
    out["request_id"] = hdr.get("x-gv-request-id") or hdr.get("x-request-id")
    if status == 200:
        data = body.get("data") if isinstance(body, dict) else None
        out["ok"] = True
        out["models"] = sorted(m.get("id") for m in (data or []) if isinstance(m, dict) and m.get("id"))
        if not key and p.get("key_env"):
            out["message"] = "endpoint answered without a key"
        if out["models"] and p["strict"] and p.get("probe_families"):
            # Upstream availability is not masked by the router (2026-10-22: every Anthropic id failed at Bedrock with
            # "Access to Anthropic models is not allowed for this account" while GPT ids worked), so ping one id per family.
            fam = {}
            for name_, pref in (("anthropic", "claude-"), ("openai", "gpt-")):
                mid = next((m for m in out["models"] if m.startswith(pref)), None)
                if not mid:
                    continue
                st2, body2, _ = _post_json(base + "/chat/completions", key, {"model": mid, "messages": [{"role": "user", "content": "Say OK."}], "max_completion_tokens": 16}, timeout=60)
                err = (body2.get("error") if isinstance(body2, dict) else None) or {}
                fam[name_] = {"model": mid, "ok": st2 == 200, "code": None if st2 == 200 else (err.get("code") or str(st2)),
                              "message": None if st2 == 200 else (err.get("message") or "")[:160]}
            out["families"] = fam
            bad = [f"{k} ({v['model']}): {v['code']}" for k, v in fam.items() if not v["ok"]]
            if bad:
                out["message"] = "upstream blocked for " + "; ".join(bad) + " — coding prompts under auto may route there"
            # the default model is what the UI preselects: test it too unless a family probe already did
            dm = out["default_model"]
            if dm and dm != "auto" and dm in out["models"] and dm not in {v["model"] for v in fam.values()}:
                out["default_probe"] = probe_model(name, dm, api_base=api_base)
                if not out["default_probe"]["ok"]:
                    d = out["default_probe"]
                    out["message"] = ((out["message"] + "; ") if out["message"] else "") + f"default model {dm} refused: {d['code']} — {d['message']}"
        if p["strict"] and not p.get("probe_families") and out["default_model"]:
            # direct endpoints (Azure): /models lists base models, not deployments — ask the default deployment itself
            dep = azure_deployments().get(out["default_model"], out["default_model"]) if name == "azure" else out["default_model"]
            out["default_probe"] = probe_model(name, dep, api_base=api_base)
            if not out["default_probe"]["ok"]:
                d = out["default_probe"]
                out["message"] = f"default model {out['default_model']} (deployment {dep}) refused: {d['code']} — {d['message']}"
        if not out["models"] and p["strict"] and p.get("market"):
            # Authenticated but the model market is empty (seen 2026-10-20 with a fresh key): every request then
            # fails with 422 no_candidate. Probe one tiny completion so the UI shows the router's own code.
            st2, body2, _ = _post_json(base + "/chat/completions", key,
                                       {"model": "auto", "messages": [{"role": "user", "content": "ping"}], "max_completion_tokens": 1})
            err = (body2.get("error") if isinstance(body2, dict) else None) or {}
            out["ok"] = st2 == 200
            out["status"] = st2
            out["code"] = None if st2 == 200 else (err.get("code") or str(st2))
            out["message"] = None if st2 == 200 else (f"key accepted, but the model market is empty and requests fail with "
                                                     f"{err.get('code') or st2}: {err.get('message') or ''} — enable model offers for this key's "
                                                     f"routing policy in the console (SmartRouter settings)")
    else:
        err = (body.get("error") if isinstance(body, dict) else None) or {}
        out["code"] = err.get("code") or err.get("type") or (str(status) if status else "unreachable")
        out["message"] = err.get("message") or body.get("raw") if isinstance(body, dict) else str(body)
        if not key and p.get("key_env"):
            out["message"] = f"{out['message']} — set {p['key_env']} (demo/.env) once access is granted"
    return out


def env_for_harness(name, model, api_base=None):
    """Environment variables for a live run through this provider (merged over os.environ by the caller)."""
    p = PROVIDERS[name]
    base = (api_base or p["api_base"] or "").rstrip("/")
    key = api_key(name)
    env = {"RSM_API_BASE": base, "RSM_WRITER_MODEL": model, "RSM_FACTS_API": base, "RSM_FACTS_MODEL": model}
    if p["strict"]:
        env.update({"RSM_API_KEY": key, "RSM_WRITER_API_KEY": key, "RSM_FACTS_API_KEY": key, "RSM_OPENAI_STRICT": "1"})
    else:
        env["RSM_ACTOR_NOTHINK"] = "1"      # vLLM/Qwen: non-thinking actor, as in the recorded v3 runs
    return env


def describe_all():
    return [check(n) for n in PROVIDERS]
