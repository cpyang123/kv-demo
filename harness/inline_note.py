"""会话内自写便签的执行侧(2026-09-22,DESIGN_inline_ledger_0921.md §A)。

本文件只有在臂 = `v5inline`(layer 带 `inline_self_write = True`)时才会被 import;
其它任何臂上 runner 的那一行 `getattr(self.mem, "inline_self_write", False)` 取到 False,
本模块从不加载,旧路径逐字不变。

做的事:在 `agent.run()` 返回之后、判分之前,**用同一个 model 对象、同一条会话**再发一轮,
让 actor 自己把便签写出来。因为前缀就是上一次 actor 请求的 prompt,vLLM 的前缀缓存几乎全中,
写侧的新算量 ≈ 一条 assistant + 一条 observation + 指令。

为什么不用 `model.query()`:它会 `_parse_actions`,便签不含 ```mswea_bash_command 围栏 ->
抛 FormatError;而且会把响应塞进 messages 污染 traj。这里走 `model._query()`,自带 2 次重试。

**绝不覆盖** temperature/top_p/seed/extra_body:chat_template_kwargs 一变整段 prompt 重新渲染,
缓存全丢。只覆盖 `max_tokens`(它不进 prompt,不影响 APC)。
"""
import hashlib
import math
import os
import pathlib
import re
import time

HERE = pathlib.Path(__file__).parent
PROMPTS = HERE.parent / "memory/ours/prompts"

V4_TAGS = ("FILES:", "STATUS:", "VERIFY:", "DID:", "CONTEXT:")
DEFAULT_FALLBACK_EXITS = "ContextWindowExceededError,RepeatedFormatError,TimeExceeded"

_PROMPT = {"text": None, "sha": None, "printed": False}


def _env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


def instruction():
    """-> (text, sha12)。首次使用时打印 INLINE_PROMPT_SHA。"""
    if _PROMPT["text"] is None:
        name = os.environ.get("RSM_INLINE_PROMPT", "inline_note_user_v1.txt")
        txt = (PROMPTS / name).read_text(encoding="utf-8")
        _PROMPT["text"] = txt
        _PROMPT["sha"] = hashlib.sha256(txt.encode("utf-8")).hexdigest()[:12]
    if not _PROMPT["printed"]:
        _PROMPT["printed"] = True
        print(f"INLINE_PROMPT_SHA={_PROMPT['sha']} file="
              f"{os.environ.get('RSM_INLINE_PROMPT', 'inline_note_user_v1.txt')}", flush=True)
    return _PROMPT["text"], _PROMPT["sha"]


# ------------------------------------------------------------------ 剥离与校验

_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
_THOUGHT = re.compile(r"^\s*THOUGHT\s*:.*$", re.M)


def _cut(txt, cap):
    """与 fusednote._cut 的 RSM_VALID_V2 分支同法:截到最后一个完整行。"""
    if len(txt) <= cap:
        return txt
    head = txt[:cap]
    if os.environ.get("RSM_VALID_V2") != "1":
        return head
    i = head.rfind("\n")
    return head[:i].rstrip() if i > 0 else head


def strip_action_residue(txt):
    """① 去 think 段;② 删所有三反引号围栏块(含 mswea_bash_command);③ 删 THOUGHT: 行;
    ④ 只保留以 V4_TAGS 开头的行;⑤ 按 RSM_NOTE_CAP 整行截断。

    比写手通路**更宽容**(写手是"出现围栏整条作废"),属有意差异:v5inline 是新臂,不与旧臂
    逐字对照。`RSM_INLINE_STRICT=1` 改成先校验后剥离(消融)。
    """
    t = (txt or "").split("</think>")[-1]
    t = _FENCE.sub("", t)
    t = _THOUGHT.sub("", t)
    keep = [l.rstrip() for l in t.splitlines() if l.strip().startswith(V4_TAGS)]
    return _cut("\n".join(keep).strip(), _env_int("RSM_NOTE_CAP", 1500))


def valid_note(t):
    """与 distill_fused_note._valid 同规则(同一 RSM_VALID_V2 门控)。"""
    t = (t or "").strip()
    if not t.startswith(V4_TAGS) or "```" in t:
        return False
    return os.environ.get("RSM_VALID_V2") == "1" or "mswea_bash_command" not in t


def parse_note(raw):
    """-> (note, ok)。RSM_INLINE_STRICT=1 时先校验后剥离。"""
    if os.environ.get("RSM_INLINE_STRICT") == "1":
        base = (raw or "").split("</think>")[-1].strip().strip("`\n ").removeprefix("```").strip()
        if not valid_note(base):
            return "", False
        return _cut(base, _env_int("RSM_NOTE_CAP", 1500)), True
    note = strip_action_residue(raw)
    return note, valid_note(note)


# ------------------------------------------------------------------ 回退状态机

def fallback_exits():
    raw = os.environ.get("RSM_INLINE_FALLBACK_EXITS", DEFAULT_FALLBACK_EXITS)
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def bad_exit(exit_status):
    """**`LimitsExceeded` 不在默认集合**:跑满步数的会话最需要便签,窗口闸已单独把关。"""
    e = str(exit_status or "")
    if e.startswith("error:"):
        return True
    return any(x and x in e for x in fallback_exits())


def headroom_ok(prev_prompt_tokens, tail_chars, maxtok=None, window=None):
    """est = prev_prompt_tokens + ceil(chars(last_assistant + last_obs + INSTR)/3.2)*1.15;
    要求 est + NOTE_MAXTOK + 256 <= RSM_INLINE_WINDOW。"""
    window = window or _env_int("RSM_INLINE_WINDOW", 98304)
    maxtok = maxtok if maxtok is not None else _env_int("RSM_INLINE_MAXTOK", 900)
    est = int(prev_prompt_tokens or 0) + int(math.ceil(tail_chars / 3.2) * 1.15)
    return (est + maxtok + 256) <= window, est


def decide(exit_status, prev_prompt_tokens, tail_chars, disabled=False):
    """PREFLIGHT。-> (go: bool, reason: str|None, est: int)。真值表由单测钉住。"""
    ok, est = headroom_ok(prev_prompt_tokens, tail_chars)
    if disabled or os.environ.get("RSM_INLINE_DISABLE") == "1":
        return False, "disabled", est
    if bad_exit(exit_status):
        return False, "bad_exit", est
    if not ok:
        return False, "no_headroom", est
    return True, None, est


# ------------------------------------------------------------------ 主入口

RETRY_USER = ("INVALID OUTPUT. Do not continue the transcript or emit commands/code fences. "
              "Output ONLY note lines, first line starting with 'FILES:'.")


def run_inline_turn(agent, model, exit_status, out, chain, order, instance, arm, run_id=None):
    """-> dict:{note, mode, reason, retries, chars, prompt_tokens, cached_tokens,
    prompt_sha, n_note_turns, writer_env_seen, est_prompt_tokens, prev_actor_prompt_tokens}。
    mode ∈ {inline, fallback_separate, none};fallback_separate 由 memory layer 执行
    (它才知道 material),这里只把决定与理由交回去。**本函数绝不抛**。"""
    instr, sha = instruction()
    res = {"note": "", "mode": "fallback_separate", "reason": "unset", "retries": 0,
           "chars": 0, "prompt_tokens": None, "cached_tokens": None, "prompt_sha": sha,
           "n_note_turns": 0, "writer_env_seen": False, "est_prompt_tokens": None,
           "prev_actor_prompt_tokens": None}
    try:
        if os.environ.get("RSM_WRITER_API") or os.environ.get("RSM_WRITER_MODEL"):
            res["writer_env_seen"] = True
            print("INLINE_IGNORES_WRITER_ENV: v5inline 的便签由 actor 自己写,"
                  "RSM_WRITER_API/RSM_WRITER_MODEL 只对回退通路生效(lane 侧不该设)", flush=True)
        from harness import ledger as L
        prev_pt = L.last_actor_prompt_tokens(agent)
        msgs = [m for m in agent.messages if m.get("role") != "exit"]
        tail = "".join(str(m.get("content") or "") for m in msgs[-2:]) + instr
        go, reason, est = decide(exit_status, prev_pt, len(tail))
        res["est_prompt_tokens"], res["prev_actor_prompt_tokens"] = est, prev_pt
        if not go:
            res["reason"] = reason
            print(f"INLINE_FALLBACK {chain}|{order} reason={reason} est={est} exit={exit_status}",
                  flush=True)
            return res

        maxtok = _env_int("RSM_INLINE_MAXTOK", 900)
        api_msgs = msgs + [{"role": "user", "content": instr}]
        raw, last_err = "", None
        for attempt in range(2):                       # CALL#1 / CALL#2(矫正重试)
            prep = model._prepare_messages_for_api(api_msgs)
            resp = None
            for net in range(3):                       # 网络类故障重试,不算 retries
                try:
                    t0 = time.time()
                    resp = model._query(prep, max_tokens=maxtok)
                    break
                except Exception as e:
                    last_err = f"{type(e).__name__}: {str(e)[:120]}"
                    if net == 2 or "ContextWindow" in type(e).__name__:
                        resp = None
                        break
                    time.sleep(3 * (net + 1))
            if resp is None:
                res["reason"] = "http_error"
                print(f"INLINE_FALLBACK {chain}|{order} reason=http_error {last_err}", flush=True)
                return res
            res["n_note_turns"] += 1
            d = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
            ch = (d.get("choices") or [{}])[0]
            raw = ((ch.get("message") or {}).get("content")) or ""
            u = d.get("usage") or {}
            det = u.get("prompt_tokens_details") or {}
            if not isinstance(det, dict):
                det = {"cached_tokens": getattr(det, "cached_tokens", None)}
            res["prompt_tokens"] = u.get("prompt_tokens")
            res["cached_tokens"] = det.get("cached_tokens")
            with L.role_ctx("note_writer", note_turn=True):
                L.record("note_writer", body={"model": d.get("model"), "max_tokens": maxtok,
                                              "messages": api_msgs},
                         usage=u, endpoint=str(os.environ.get("RSM_API_BASE", "")).split("//")[-1],
                         latency_s=time.time() - t0, note_turn=True,
                         finish_reason=ch.get("finish_reason"), model=d.get("model"),
                         prompt_text="")
            note, ok = parse_note(raw)
            if ok:
                res.update(note=note, mode="inline", reason="ok", retries=attempt,
                           chars=len(note))
                print(f"INLINE_OK {chain}|{order} chars={len(note)} retries={attempt} "
                      f"prompt={res['prompt_tokens']} cached={res['cached_tokens']}", flush=True)
                return res
            api_msgs = msgs + [{"role": "user", "content": instr},
                               {"role": "assistant", "content": raw[:500]},
                               {"role": "user", "content": RETRY_USER}]
        res["reason"] = "invalid_twice"
        res["retries"] = 1
        print(f"INLINE_FALLBACK {chain}|{order} reason=invalid_twice head={raw[:80]!r}", flush=True)
        return res
    except Exception as e:                              # 绝不让便签轮弄挂主流程
        res["reason"] = f"exc:{type(e).__name__}"
        print(f"INLINE_TURN_ERROR: {type(e).__name__}: {str(e)[:150]}", flush=True)
        return res
