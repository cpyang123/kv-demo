"""精确 token 台账(2026-09-22,DESIGN_inline_ledger_0921.md §B)。

**总开关 `RSM_LEDGER=1`,默认关**:不设 env 时 `install()` 从不被调用,本模块除了被 import
之外零副作用 —— 不打补丁、不写盘、不改任何请求字节。设为 1 时也**只旁观**:补丁读 `Request`
对象但从不修改它,请求体的 sha256 逐字不变(见 tests/test_inline_ledger.py 的 C5)。

三口径(哪个当头条是出表的决定,不是代码的决定,所以三个都算、都落盘):
    ① raw        = Σ(prompt_tokens + completion_tokens)            部署无关,可跨系统比较
    ② cacheaware = Σ(uncached_prompt_tokens + completion_tokens)   本机这台服务器上的新算量
    ③ priced     = 按 prices.json 折算的美元                        折算价,非实际支出

采集(§B.1):一次 monkeypatch,插件文件零 diff。
  * `urllib.request.urlopen` —— fusednote 写手 / memop / rbank 判官+抽取 / reader gate / cline
    全走这条,零改动被覆盖;
  * `openai.resources.chat.completions.Completions.create` —— attribution/probes.py 的 mem0
    抽取与 statenote(走 openai SDK)。
  * **actor 不打补丁**:usage 早就在 `message["extra"]["response"]` 里,会话末
    `harvest_actor(agent)` 一次出全部行(历史 run 也能据此补账)。
  * 会话内自写便签轮是唯一的**显式打点**(harness/inline_note.py 调 `record(...)`)。

所有写盘包 try/except,异常只打印不抛(沿用 reasoningbank.py 的 `RB_WLOG_ERROR` 纪律:
台账绝不许弄挂主流程)。
"""
import hashlib
import json
import math
import os
import pathlib
import sys
import time

LEDGER_VER = 1
HERE = pathlib.Path(__file__).parent
_PROMPT_DIRS = [HERE.parent / "memory/ours/prompts", HERE.parent / "memory/baselines/prompts"]


def on():
    return os.environ.get("RSM_LEDGER") == "1"


# ---------------------------------------------------------------- 会话上下文

_CTX = {"run_id": None, "chain": None, "slot": None, "instance": None, "arm": None,
        "out": None, "served": None, "window": None}
_ROWS = []            # 本 session 的逐调用行(session_summary 读它)
_CALL_IDX = {"n": 0}
_FORCED = []          # role_ctx 栈
_INJ = {"tokens": None, "src": None}
_INSTALLED = {"done": False}
_SKIP = {"litellm": 0, "actor_endpoint": 0}   # SDK 补丁让路的次数(见 _caller_is_litellm)
_PREV_PROMPT = {}     # (role, endpoint) -> 上一条请求的 prompt 文本(cached 估计器用)


def begin_session(run_id=None, chain=None, slot=None, instance=None, arm=None, out=None,
                  served=None, window=None):
    """每个 session(= 链上一题)开始时调用。清空逐调用缓冲。"""
    _CTX.update(run_id=run_id, chain=chain, slot=slot, instance=instance, arm=arm,
                out=out, served=served,
                window=window or _env_int("RSM_INLINE_WINDOW", 98304))
    _ROWS.clear()
    _CALL_IDX["n"] = 0
    _INJ.update(tokens=None, src=None)


def _env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


class role_ctx:
    """显式 role 上下文(唯一显式打点:会话内自写便签轮)。"""

    def __init__(self, role, note_turn=False):
        self.role, self.note_turn = role, note_turn

    def __enter__(self):
        _FORCED.append((self.role, self.note_turn))
        return self

    def __exit__(self, *a):
        if _FORCED:
            _FORCED.pop()
        return False


# ---------------------------------------------------------------- role 规则表

# 逐字取自各 call site 的 system 提示常数。**故意重复**一份字面量:单元测试
# test_role_rules_no_drift 断言它们与 memory/ 里的活常数相等,规则表漂移会被测出来。
JUDGE_SYS_PREFIX = "You are a helpful assistant that judges"
READER_SYS_PREFIX = "You are the memory reader of a coding agent."
STATENOTE_SYS_PREFIX = "You are a repo-state note-taker for a software-engineering agent."
# openai SDK 上的抽取通路 = memory/attribution/probes.py 的两个 call site(mem0 两条抽取分支
# + statenote)。**必须逐条列名**:"走 SDK" 本身不再是判据,否则没有 usage 的调用会掉进
# memory_extract(2026-09-22 smoke 判据 ④ 的病因)。
EXTRACT_SYS_PREFIXES = (
    STATENOTE_SYS_PREFIX,
    "You are a smart memory manager",              # mem0 记忆更新
    "You are an Assistant Information Organizer",  # mem0 抽取:agent 分支
    "You are a Personal Information Organizer",    # mem0 抽取:个人分支
)

_WRITER_HASHES = None


def _h64(text):
    return hashlib.sha256((text or "")[:64].encode("utf-8", "replace")).hexdigest()[:16]


def writer_prompt_hashes():
    """memory/**/prompts/*.txt 的前 64 字符 hash 集合 = 所有"写便签"通路的 system 提示。
    rb 判官与 reader 不走文件,由上面的前缀常数单独判。"""
    global _WRITER_HASHES
    if _WRITER_HASHES is None:
        hs = {}
        for d in _PROMPT_DIRS:
            try:
                for f in sorted(d.glob("*.txt")):
                    hs[_h64(f.read_text(encoding="utf-8", errors="replace"))] = f.name
            except Exception:
                pass
        _WRITER_HASHES = hs
    return _WRITER_HASHES


def _host(url):
    """http://127.0.0.1:8013/v1 -> 127.0.0.1:8013"""
    return (url or "").split("//")[-1].split("/")[0]


endpoint_host = _host        # 别处(inline_note.py)统一用它写 endpoint,免得同一台服务器
                             # 在 actor 行上写成 "127.0.0.1:8013/v1"、在 urllib 行上写成 "127.0.0.1:8013"


def role_of(system_text, endpoint="", sdk=False):
    """按序匹配;未命中 -> other(绝不丢账)。"""
    if _FORCED:
        return _FORCED[-1][0]
    s = (system_text or "").lstrip()
    if s.startswith(JUDGE_SYS_PREFIX):
        return "judge"
    if s.startswith(READER_SYS_PREFIX):
        return "memory_retrieve"
    # reader 端点规则:只在 reader 确实**另起一台**(端点与 actor/写手都不同)时才生效,
    # 否则同端口会把写手调用也误判成检索。
    rd = _host(os.environ.get("RSM_READER_API"))
    if (rd and endpoint and rd in endpoint
            and rd not in (_host(os.environ.get("RSM_API_BASE")), _host(os.environ.get("RSM_WRITER_API")))):
        return "memory_retrieve"
    if s.startswith(EXTRACT_SYS_PREFIXES):
        return "memory_extract"
    if _h64(s) in writer_prompt_hashes():
        return "note_writer"
    # 2026-09-22 修:SDK 通路只有"端点确实是另起的抽取器"才算 memory_extract。
    # 旧规则是 `if sdk or …`,于是任何经 openai SDK 下去的调用都被扣在 memory_extract 头上;
    # litellm 的 actor 请求正是这样一条,smoke 里每条 actor 调用都多出一条影子行(判据 ④)。
    if sdk:
        fa = _host(os.environ.get("RSM_FACTS_API"))
        if fa and endpoint and fa in endpoint and fa != _host(os.environ.get("RSM_API_BASE")):
            return "memory_extract"
    return "other"


# ---------------------------------------------------------------- 逐调用记录

def _tok_est(text):
    return int(math.ceil(len(text or "") / 3.2))


def _lcp_cached(role, endpoint, prompt_text, prompt_tokens):
    """§B.6:cached_tokens 不可用时的估计器。同 (role, endpoint) 内留上一条请求的 prompt 文本,
    取最长公共前缀 -> 估 token -> 向下取整到 block 边界 -> min(lcp, prev_prompt_tokens)。"""
    key = (role, endpoint)
    prev = _PREV_PROMPT.get(key)
    # prompt_tokens 可能是 None(响应没带 usage):存估计值,否则下一条的 min(lcp, ptok or 0)
    # 恒为 0,估计器整条通路静默失效(2026-09-22 修)。
    _PREV_PROMPT[key] = (prompt_text, prompt_tokens if prompt_tokens else _tok_est(prompt_text))
    if not prev or not prompt_text:
        return 0
    ptext, ptok = prev
    n = 0
    for a, b in zip(ptext, prompt_text):
        if a != b:
            break
        n += 1
    blk = max(1, _env_int("RSM_LEDGER_BLOCK", 16))
    lcp_tok = int(_tok_est(ptext[:n]) // blk) * blk
    return max(0, min(lcp_tok, ptok or 0))


def record(role, body=None, usage=None, endpoint="", latency_s=None, note_turn=False,
           finish_reason=None, model=None, prompt_text=None):
    """记一行。body/usage 都是普通 dict(openai 响应的 usage 请先 model_dump/转 dict)。"""
    if not on():
        return None
    try:
        body = body or {}
        usage = usage or {}
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        det = usage.get("prompt_tokens_details") or {}
        if not isinstance(det, dict):
            det = {k: getattr(det, k, None) for k in ("cached_tokens", "created_cache_tokens")}
        cached = det.get("cached_tokens")
        if pt is None:
            # 响应根本没带 usage(litellm 的 raw-response 包装就是):prompt 只能按字符估,
            # cached **不估**(没有可信的基准),明确记 none —— 绝不假装有读数。
            if prompt_text is None:
                prompt_text = "".join(m.get("content") or "" for m in body.get("messages", []))
            cached, src = 0, "none"
        elif cached is None:
            if prompt_text is None:
                prompt_text = "".join(m.get("content") or "" for m in body.get("messages", []))
            cached = _lcp_cached(role, endpoint, prompt_text, pt)
            src = "lcp_estimate" if cached else "none"
        else:
            src = "usage"
            if prompt_text is None:
                prompt_text = "".join(m.get("content") or "" for m in body.get("messages", []))
            _PREV_PROMPT[(role, endpoint)] = (prompt_text, pt)
        pt = pt if pt is not None else _tok_est(prompt_text)
        ct = ct if ct is not None else 0
        cached = min(int(cached or 0), int(pt))
        _CALL_IDX["n"] += 1
        row = {"ts": round(time.time(), 1), "run_id": _CTX["run_id"], "chain": _CTX["chain"],
               "slot": _CTX["slot"], "instance": _CTX["instance"], "arm": _CTX["arm"],
               "role": role, "call_idx": _CALL_IDX["n"],
               "model": model or body.get("model"), "endpoint": endpoint,
               "prompt_tokens": int(pt), "cached_tokens": int(cached),
               "uncached_prompt_tokens": int(pt) - int(cached), "completion_tokens": int(ct),
               "total_tokens": int(pt) + int(ct), "cached_src": src,
               "created_cache_tokens": det.get("created_cache_tokens"),
               "latency_s": (round(latency_s, 2) if latency_s is not None else None),
               "window": _CTX["window"], "finish_reason": finish_reason,
               "truncated": finish_reason == "length",
               "max_tokens": body.get("max_tokens"), "temperature": body.get("temperature"),
               "served": _CTX["served"], "note_turn": bool(note_turn or (_FORCED and _FORCED[-1][1])),
               "ledger_ver": LEDGER_VER}
        _ROWS.append(row)
        out = _CTX.get("out")
        if out is not None:
            with open(pathlib.Path(out) / "calls.jsonl", "a") as f:
                f.write(json.dumps(row) + "\n")
        return row
    except Exception as e:                       # 台账绝不许弄挂主流程
        print(f"LEDGER_RECORD_ERROR: {type(e).__name__}: {str(e)[:120]}", flush=True)
        return None


# ---------------------------------------------------------------- 采集钩子

def install():
    """幂等。只在 RSM_LEDGER=1 时被 runner 调用。"""
    if not on() or _INSTALLED["done"]:
        return
    _INSTALLED["done"] = True
    _patch_urllib()
    _patch_openai()
    print(f"LEDGER: on ver={LEDGER_VER} price_sha={price_table_sha()} "
          f"prompts={len(writer_prompt_hashes())}", flush=True)


def _patch_urllib():
    import io
    import urllib.request as _u
    orig = _u.urlopen
    if getattr(orig, "_rsm_ledger", False):
        return

    def wrapped(url, *a, **kw):
        t0 = time.time()
        resp = orig(url, *a, **kw)
        try:
            full = getattr(url, "full_url", None) or (url if isinstance(url, str) else "")
            data = getattr(url, "data", None)
            if "/chat/completions" not in str(full) or not data:
                return resp
            raw = resp.read()
            body = json.loads(data.decode("utf-8", "replace"))
            try:
                rj = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                rj = {}
            sysm = ""
            for m in body.get("messages", []):
                if m.get("role") == "system":
                    sysm = m.get("content") or ""
                    break
            ep = str(full).split("//")[-1].split("/v1")[0]
            ch = (rj.get("choices") or [{}])[0]
            record(role_of(sysm, endpoint=ep), body=body, usage=rj.get("usage") or {},
                   endpoint=ep, latency_s=time.time() - t0,
                   finish_reason=ch.get("finish_reason"), model=body.get("model"))
            # 原样把字节还回去:调用方的 json.load(...) 行为逐字不变
            out = io.BytesIO(raw)
            out.headers = getattr(resp, "headers", None)
            out.status = getattr(resp, "status", 200)
            out.url = str(full)
            out.getcode = lambda: getattr(resp, "status", 200)
            out.info = lambda: getattr(resp, "headers", None)
            return out
        except Exception as e:
            print(f"LEDGER_URLLIB_ERROR: {type(e).__name__}: {str(e)[:120]}", flush=True)
            return resp

    wrapped._rsm_ledger = True
    _u.urlopen = wrapped


def _caller_is_litellm(max_depth=40):
    """调用栈里有 litellm 帧 -> 这条 SDK 调用是 actor(或便签轮)发的。

    actor 走 `litellm.completion` -> openai SDK,账由**会话末的 `harvest_actor`** 出;
    便签轮走 `model._query` -> 同一条路,账由 `harness/inline_note.py` 的显式 `record` 出。
    两者都已有人认领,SDK 补丁必须让路,否则同一条调用记两次
    (2026-09-22 smoke:每条 actor 调用多出一条无 usage 的影子行,还被扣到 memory_extract 头上)。
    `memory/attribution/probes.py` 的抽取器直接用 openai SDK、栈里没有 litellm,照记不误。
    """
    f = sys._getframe()
    for _ in range(max_depth):
        f = f.f_back
        if f is None:
            return False
        n = f.f_globals.get("__name__") or ""
        if n == "litellm" or n.startswith("litellm."):
            return True
    return False


def skipped():
    """SDK 补丁让路的计数(单测与 smoke 用)。"""
    return dict(_SKIP)


def _patch_openai():
    try:
        from openai.resources.chat import completions as _c
    except Exception:
        return
    cls = getattr(_c, "Completions", None)
    if cls is None or getattr(cls.create, "_rsm_ledger", False):
        return
    orig = cls.create

    def wrapped(self, *a, **kw):
        mine = not _caller_is_litellm()          # 先看栈,再发请求(发完栈就不一样了)
        if not mine:
            _SKIP["litellm"] += 1                # actor / 便签轮:别人已经认领,这里不记
        t0 = time.time()
        r = orig(self, *a, **kw)
        if not mine:
            return r
        try:
            msgs = kw.get("messages") or []
            sysm = next((m.get("content") or "" for m in msgs if m.get("role") == "system"), "")
            u = getattr(r, "usage", None)
            usage = {}
            if u is not None:
                usage = u.model_dump() if hasattr(u, "model_dump") else dict(u)
            # 端点取客户端自己的 base_url(抽取器可能不经 RSM_FACTS_API);取不到再回落到 env。
            ep = ""
            try:
                ep = _host(str(getattr(getattr(self, "_client", None), "base_url", "") or ""))
            except Exception:
                ep = ""
            ep = ep or _host(os.environ.get("RSM_FACTS_API"))
            if not usage and ep and ep == _host(os.environ.get("RSM_API_BASE")):
                # 兜底(litellm 帧没认出来时):打在 actor 那台服务器上、又没有 usage 的 SDK 调用
                # 只可能是 actor 自己的请求包装,它的账由 harvest_actor 出。
                _SKIP["actor_endpoint"] += 1
                return r
            fr = None
            try:
                fr = r.choices[0].finish_reason
            except Exception:
                pass
            record(role_of(sysm, endpoint=ep, sdk=True),
                   body={"model": kw.get("model"), "messages": msgs,
                         "max_tokens": kw.get("max_tokens"), "temperature": kw.get("temperature")},
                   usage=usage, endpoint=ep, latency_s=time.time() - t0,
                   finish_reason=fr, model=kw.get("model"))
        except Exception as e:
            print(f"LEDGER_OPENAI_ERROR: {type(e).__name__}: {str(e)[:120]}", flush=True)
        return r

    wrapped._rsm_ledger = True
    cls.create = wrapped


# ---------------------------------------------------------------- actor 收割

def harvest_actor(agent, served=None):
    """会话末一次性把 actor 的全部 usage 收进台账。usage 来自
    `message["extra"]["response"]["usage"]`(litellm 存的完整 response),历史 run 同样可补账。"""
    if not on():
        return 0
    n = 0
    try:
        for m in getattr(agent, "messages", []):
            resp = ((m.get("extra") or {}).get("response") or {})
            if not isinstance(resp, dict):
                continue
            u = resp.get("usage") or {}
            if not u:
                continue
            ch = (resp.get("choices") or [{}])[0]
            record("actor", body={"model": resp.get("model"),
                                  "max_tokens": None, "temperature": None},
                   usage=u, endpoint=_host(os.environ.get("RSM_API_BASE", "")),
                   finish_reason=(ch or {}).get("finish_reason"), model=resp.get("model"),
                   prompt_text="")
            n += 1
    except Exception as e:
        print(f"LEDGER_HARVEST_ERROR: {type(e).__name__}: {str(e)[:120]}", flush=True)
    return n


def last_actor_prompt_tokens(agent):
    """最后一条 actor 请求的 prompt_tokens(inline 轮的 headroom 预估 + smoke 判据用)。"""
    pt = None
    try:
        for m in getattr(agent, "messages", []):
            u = (((m.get("extra") or {}).get("response") or {}) or {}).get("usage") or {}
            if u.get("prompt_tokens"):
                pt = int(u["prompt_tokens"])
    except Exception:
        pass
    return pt


# ---------------------------------------------------------------- 注入 token

_TOKZ = {"obj": None, "tried": False}
MEM_HEAD = "<memory_context>\nNotes from earlier work sessions on this repository:\n"
MEM_TAIL = "</memory_context>"


def injected_payload(text):
    """从 wrap 后的题面里取 <memory_context> 壳内的载荷(base/rawcat/factsall/v5*/rbank/memop 全用这个壳)。"""
    if not text or MEM_HEAD not in text:
        return ""
    body = text.split(MEM_HEAD, 1)[1]
    return body.split(MEM_TAIL, 1)[0] if MEM_TAIL in body else body


def _tokenizer():
    if _TOKZ["tried"]:
        return _TOKZ["obj"]
    _TOKZ["tried"] = True
    p = os.environ.get("RSM_LEDGER_TOKENIZER")
    if p:
        try:
            from transformers import AutoTokenizer
            _TOKZ["obj"] = AutoTokenizer.from_pretrained(p)
        except Exception as e:
            print(f"LEDGER_TOKENIZER_ERROR: {type(e).__name__}: {str(e)[:120]}", flush=True)
    return _TOKZ["obj"]


def count_injected(wrap_text):
    """-> (tokens, src)。src ∈ {tokenizer, estimate, none}。口径 = **载荷** token,
    不含 chat template 开销,与 Inj(K)=chars/4000 同位可比。"""
    payload = injected_payload(wrap_text)
    if not payload:
        return 0, "none"
    tk = _tokenizer()
    if tk is not None:
        try:
            n = len(tk(payload, add_special_tokens=False)["input_ids"])
            _INJ.update(tokens=n, src="tokenizer")
            return n, "tokenizer"
        except Exception:
            pass
    n = round(len(payload) / 3.6)
    _INJ.update(tokens=n, src="estimate")
    return n, "estimate"


def note_injected(w):
    """runner 侧一行钩子:吃 WrapResult。"""
    if not on():
        return
    try:
        count_injected(getattr(w, "text", "") or "")
    except Exception as e:
        print(f"LEDGER_INJ_ERROR: {type(e).__name__}: {str(e)[:120]}", flush=True)


# ---------------------------------------------------------------- 价表

_PRICE = {"row": None, "sha": None}


def price_table_path():
    return pathlib.Path(os.environ.get("RSM_PRICE_TABLE", str(HERE / "prices.json")))


def price_row():
    if _PRICE["row"] is None:
        try:
            raw = price_table_path().read_bytes()
            _PRICE["sha"] = hashlib.sha256(raw).hexdigest()[:12]
            tbl = json.loads(raw.decode("utf-8"))
            name = os.environ.get("RSM_PRICE_ROW") or tbl.get("_default_row") or "opus5"
            _PRICE["row"] = dict(tbl["rows"][name], _row=name)
        except Exception as e:
            print(f"LEDGER_PRICE_ERROR: {type(e).__name__}: {str(e)[:120]}", flush=True)
            _PRICE["row"] = {"input": 0.0, "cached_input": 0.0, "cache_write": 0.0,
                             "output": 0.0, "_row": "missing"}
            _PRICE["sha"] = "missing"
    return _PRICE["row"]


def price_table_sha():
    price_row()
    return _PRICE["sha"]


def cost_usd(rows, pr=None):
    pr = pr or price_row()
    unc_price = pr["cache_write"] if os.environ.get("RSM_PRICE_CACHE_WRITE") == "1" else pr["input"]
    c = 0.0
    for r in rows:
        c += (r.get("uncached_prompt_tokens", 0) * unc_price
              + r.get("cached_tokens", 0) * pr["cached_input"]
              + r.get("completion_tokens", 0) * pr["output"])
    return c / 1e6


# ---------------------------------------------------------------- 汇总

WRITER_ROLES = ("note_writer",)
OTHER_ROLES = ("memory_extract", "memory_retrieve", "judge", "other")


def _sums(rows):
    return {"prompt": sum(r.get("prompt_tokens", 0) for r in rows),
            "cached": sum(r.get("cached_tokens", 0) for r in rows),
            "uncached": sum(r.get("uncached_prompt_tokens", 0) for r in rows),
            "completion": sum(r.get("completion_tokens", 0) for r in rows),
            "n": len(rows)}


def summarize(rows):
    """逐会话三口径 + 三分解。纯函数,`ledger_rollup.py` 与单测共用。"""
    act = [r for r in rows if r.get("role") == "actor"]
    wri = [r for r in rows if r.get("role") in WRITER_ROLES]
    oth = [r for r in rows if r.get("role") in OTHER_ROLES]
    out = {}
    for tag, grp in (("actor", act), ("writer", wri), ("memother", oth)):
        s = _sums(grp)
        out[f"tok_{tag}_prompt"] = s["prompt"]
        out[f"tok_{tag}_cached"] = s["cached"]
        out[f"tok_{tag}_uncached"] = s["uncached"]
        out[f"tok_{tag}_completion"] = s["completion"]
        out[f"n_calls_{tag}"] = s["n"]
    all_rows = act + wri + oth
    out["tok_raw_total"] = sum(r.get("prompt_tokens", 0) + r.get("completion_tokens", 0) for r in all_rows)
    out["tok_cacheaware_total"] = sum(r.get("uncached_prompt_tokens", 0) + r.get("completion_tokens", 0)
                                      for r in all_rows)
    out["cost_usd_priced"] = round(cost_usd(all_rows), 6)
    est = sum(1 for r in all_rows if r.get("cached_src") != "usage")
    out["cached_src_estimated_share"] = (round(est / len(all_rows), 4) if all_rows else None)
    return out


def session_summary():
    """runner 侧一行钩子:落进 results.jsonl 的新键(**只加不改**,旧键一个不动)。"""
    if not on():
        return {}
    try:
        d = summarize(_ROWS)
        d["injected_tokens"] = _INJ["tokens"]
        d["injected_tokens_src"] = _INJ["src"]
        d["ledger_ver"] = LEDGER_VER
        d["price_table_sha"] = price_table_sha()
        d["price_row"] = price_row().get("_row")
        return d
    except Exception as e:
        print(f"LEDGER_SUMMARY_ERROR: {type(e).__name__}: {str(e)[:120]}", flush=True)
        return {}


def rows():
    return list(_ROWS)
