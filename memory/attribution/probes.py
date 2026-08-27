"""预实验归因臂(冻结移植,行为与 run_kv_chain 原版逐字节对齐;仅修审计缺陷):
rawcat / rawprose / rawcmd / rawdiff / factsall / statenote / repomap / mapdiff / structmem。
split_components 的两条 08-24 修复注释是行为契约,原样保留。"""
import json, os, re

from ..base import MemoryLayer, WrapResult, register


# ---------- 确定性成分拆分 ----------

def split_components(raw_text):
    """把 raw 轨迹拆成三种纯成分(确定性解析,无 LLM):
    diff  = FINAL DIFF 段(逐字代码变更)
    cmd   = 命令台账(assistant ```bash 块内容 + 其后 observation 的 returncode)
    prose = assistant 的思考文本(剥除代码块与 diff)——"叙述/推理"成分
    """
    diff = ""
    m = raw_text.rfind("\nFINAL DIFF:\n")
    body = raw_text
    if m >= 0:
        diff = raw_text[m + len("\nFINAL DIFF:\n"):]
        body = raw_text[:m]
    cmds, prose_parts = [], []
    # 只在消息标记处切分:assistant 消息内部自带空行,朴素 split("\n\n") 会把围栏炸进
    # 不以 ASSISTANT: 开头的后续块导致命令恒空(08-24 修)
    blocks = re.split(r"\n\n(?=ASSISTANT:|OBSERVATION:)", body)
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b.startswith("ASSISTANT:"):
            txt = b[len("ASSISTANT:"):]
            # (?:```|\Z):消息被截断时闭合围栏可能丢失,截到块尾兜底
            fences = re.findall(r"```(?:mswea_bash_command|bash)\n(.*?)(?:```|\Z)", txt, re.S)
            rc = ""
            if i + 1 < len(blocks) and blocks[i + 1].startswith("OBSERVATION:"):
                mrc = re.search(r"returncode.{0,3}(\d+)", blocks[i + 1])
                rc = f"  [exit {mrc.group(1)}]" if mrc else ""
            for f in fences:
                f = f.strip()
                if f:
                    cmds.append(f"$ {f}{rc}")
            prose = re.sub(r"```\w*\n.*?(?:```|\Z)", "", txt, flags=re.S).strip()
            if prose:
                prose_parts.append(prose)
        i += 1
    return {"rawdiff": diff.strip(), "rawcmd": "\n".join(cmds), "rawprose": "\n\n".join(prose_parts)}


# ---------- LLM 抽取器(懒加载依赖;凭证口径统一 RSM_FACTS_API 回退) ----------

def _facts_client():
    from openai import OpenAI
    fa = os.environ.get("RSM_FACTS_API")
    return OpenAI(
        api_key="EMPTY" if fa else (os.environ.get("AZURE_OPENAI_API_KEY") or os.environ["OPENAI_API_KEY"]),
        base_url=fa or os.environ.get("AZURE_OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL")), \
        os.environ.get("RSM_FACTS_MODEL", "gpt-4o-mini")


def _log_usage(model, usage):
    ulog = os.environ.get("FACTS_USAGE_LOG")
    if ulog and usage:
        with open(ulog, "a") as f:
            f.write(json.dumps({"model": model, "prompt_tokens": usage.prompt_tokens,
                                "completion_tokens": usage.completion_tokens}) + "\n")


def extract_facts_mem0style(raw_text):
    """mem0 官方两条抽取分支的并集(个人+agent prompt,均为 mem0 原装,零自定义)。
    单用任一支对编码轨迹都欠抽取,故并集(默认口径,见预实验记录)。
    prompts 为逐字 vendor(评测环境不装 mem0;曾因缺包整臂静默退化,F17)。"""
    from .mem0_prompts import get_fact_retrieval_messages
    client, model = _facts_client()
    facts, seen = [], set()
    cap = int(os.environ.get("RSM_FACTS_INPUT_CAP", "23000"))
    for agent_mode in (True, False):
        sp, up = get_fact_retrieval_messages(raw_text[:cap], is_agent_memory=agent_mode)
        r = client.chat.completions.create(
            model=model, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": sp}, {"role": "user", "content": up}])
        _log_usage(model, r.usage)   # 修:记实际 model,不再硬编码
        try:
            batch = json.loads(r.choices[0].message.content).get("facts", [])
        except Exception:
            batch = []
        for f in batch:
            k = str(f).strip().lower()
            if k and k not in seen:
                seen.add(k)
                facts.append(str(f).strip())
    return facts


STATENOTE_SYS = (
    "You are a repo-state note-taker for a software-engineering agent. Given the agent's work "
    "session transcript and its final diff, produce STATE NOTES: a short structured record of what "
    "the repository's state now is because of this session. Output JSON {\"notes\": [...]} where each "
    "note is one line of the form 'FILE <path> :: CHANGED <function/area> :: WHY <one clause> :: "
    "VERIFY <exact test or command that exercises it>'. Rules: at most 8 notes; only files/commands "
    "that actually appear in the session; no advice, no narrative, no facts about the agent — only "
    "the repository's resulting state and how to verify it."
)


def distill_state_notes(raw_text):
    client, model = _facts_client()   # 统一口径:与 facts 同一端点回退(修旧版硬编码 Azure)
    cap = int(os.environ.get("RSM_FACTS_INPUT_CAP", "23000"))
    r = client.chat.completions.create(
        model=model, temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": STATENOTE_SYS},
                  {"role": "user", "content": raw_text[:cap]}])
    _log_usage(model, r.usage)
    try:
        return [str(n).strip() for n in json.loads(r.choices[0].message.content).get("notes", []) if str(n).strip()][:8]
    except Exception:
        return []


# ---------- structmem 写/读侧 ----------

def route_structmem(raw_text):
    """structmem 写侧:把 session 成分按 repo 结构编址(确定性,无 LLM)。
    diff 逐 hunk 挂到所属文件;命令挂到其提及的已改文件;无址命令与叙述进 __journal__。"""
    comp = split_components(raw_text)
    buckets, files = {}, []
    if comp["rawdiff"].strip():
        for ch in re.split(r"(?=diff --git )", comp["rawdiff"]):
            if not ch.strip():
                continue
            m = (re.search(r"diff --git a/(\S+) b/(\S+)", ch)
                 or re.search(r"\+\+\+ b?/?(\S+)", ch))
            p = m.group(m.lastindex) if m else "__journal__"
            buckets.setdefault(p, []).append("[diff]\n" + ch.strip()[:1500])
            if p != "__journal__" and p not in files:
                files.append(p)
    for line in comp["rawcmd"].splitlines():
        target = None
        for p in files:
            base = p.rsplit("/", 1)[-1]
            if p in line or (len(base) > 6 and base in line):
                target = p
                break
        buckets.setdefault(target or "__journal__", []).append(line)
    if comp["rawprose"].strip():
        buckets.setdefault("__journal__", []).append("[notes]\n" + comp["rawprose"][:1500])
    return {p: "\n".join(v)[:2500] for p, v in buckets.items()}


def pack_structmem(items, question, current_paths, budget):
    """structmem 读侧:按任务相关性打分选节点,预算内贪心装配;挂在已消失文件上的记忆丢弃。"""
    qtok = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", question)}
    scored = []
    for idx, (name, text) in enumerate(items):
        path = name.split("||", 1)[1] if "||" in name else "__journal__"
        if path != "__journal__" and current_paths is not None and path not in current_paths:
            continue                      # 结构校验:文件已不在 → 记忆随之失效
        ptok = {t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", path)}
        ttok = {t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text[:300])}
        score = 3 * len(ptok & qtok) + len(ttok & qtok) + (1 if path == "__journal__" else 0)
        scored.append((score, idx, path, text))
    scored.sort(key=lambda s: (-s[0], -s[1]))     # 高分优先,同分近期优先
    out, used, seen = [], 0, set()
    for score, idx, path, text in scored:
        block = f"== {path if path != '__journal__' else '(session journal)'}\n{text}"
        if used + len(block) > budget and out:
            break
        key = (path, text[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(block)
        used += len(block)
    return "\n\n".join(out)


def _repomap_block(env):
    """确定性结构摘要:文件树(截断)+ 核心 .py 的 def/class 骨架。零记忆,每任务现算。
    返回 (整块文本, 裸载荷字符数)——map_chars 统一按裸载荷计,与 structmem 同基数(rev2-P5)。"""
    tree = env.raw("git ls-files | head -200")["output"]
    skel = env.raw(
        "for f in $(git ls-files '*.py' | xargs -r wc -l 2>/dev/null | sort -rn | head -12 | awk '{print $2}' | grep -v total); do "
        "echo \"== $f\"; grep -nE '^(class |def |    def )' $f 2>/dev/null | head -20; done")["output"]
    blk = (f"<memory_context>\nRepository structure map (computed fresh, no cross-session memory):\n"
           f"FILES:\n{tree[:4000]}\nKEY SYMBOLS:\n{skel[:8000]}\n</memory_context>\n\n")
    return blk, len(tree[:4000]) + len(skel[:8000])


# ---------- 插件注册 ----------

@register
class RawCat(MemoryLayer):
    """原样拼接注入(零记忆层调用,下锚/天花板)。"""
    name = "rawcat"

    def write(self, spec, material, delta, env):
        self.store.add(spec["swebench_instance_id"], material)
        self.n_writes += 1
        return 1


class _Component(MemoryLayer):
    comp = ""

    def write(self, spec, material, delta, env):
        c = split_components(material)[self.comp]
        if not c.strip():
            return 0
        self.store.add(spec["swebench_instance_id"], c[:23000])
        self.n_writes += 1
        return 1


@register
class RawProse(_Component):
    name = "rawprose"
    comp = "rawprose"


@register
class RawCmd(_Component):
    name = "rawcmd"
    comp = "rawcmd"


@register
class RawDiff(_Component):
    name = "rawdiff"
    comp = "rawdiff"


@register
class FactsAll(MemoryLayer):
    """mem0 抽取后全量注入(无筛选;抽取=官方双分支并集)。"""
    name = "factsall"
    llm_writer = True

    def write(self, spec, material, delta, env):
        facts = extract_facts_mem0style(material)
        iid = spec["swebench_instance_id"]
        for i, f in enumerate(facts):
            self.store.add(f"{iid}__f{i+1}", f)
        self.n_writes += 1
        return len(facts)


@register
class StateNote(MemoryLayer):
    name = "statenote"
    llm_writer = True

    def write(self, spec, material, delta, env):
        notes = distill_state_notes(material)
        iid = spec["swebench_instance_id"]
        for i, n in enumerate(notes):
            self.store.add(f"{iid}__n{i+1}", n)
        self.n_writes += 1
        return len(notes)


@register
class RepoMap(MemoryLayer):
    """纯结构图,零跨任务记忆(诊断结构信息价值)。"""
    name = "repomap"

    def wrap(self, question, env):
        blk, payload = _repomap_block(env)
        return WrapResult(blk + question, map_chars=payload)

    def write(self, spec, material, delta, env):
        return 0


@register
class MapDiff(MemoryLayer):
    """结构图 + diff 记忆(F10 修复后:map_chars 与 injected_chars 分账)。"""
    name = "mapdiff"

    def wrap(self, question, env):
        blk, payload = _repomap_block(env)
        inner = self.store.wrap(question)
        return WrapResult(blk + inner.text, injected_chars=inner.injected_chars,
                          injected=inner.injected, map_chars=payload)

    def write(self, spec, material, delta, env):
        comp = split_components(material)["rawdiff"]
        if not comp.strip():
            return 0
        self.store.add(spec["swebench_instance_id"], comp[:23000])
        self.n_writes += 1
        return 1


@register
class StructMem(MemoryLayer):
    """结构编址库:写侧确定性路由,读侧结构图内联装配。"""
    name = "structmem"

    def wrap(self, question, env):
        tree = env.raw("git ls-files | head -200")["output"]
        skel = env.raw(
            "for f in $(git ls-files '*.py' | xargs -r wc -l 2>/dev/null | sort -rn | head -12 | awk '{print $2}' | grep -v total); do "
            "echo \"== $f\"; grep -nE '^(class |def |    def )' $f 2>/dev/null | head -20; done")["output"]
        current = set(env.raw("git ls-files")["output"].split())
        budget = int(os.environ.get("RSM_STRUCTMEM_BUDGET", "12000"))
        packed = pack_structmem(self.store.items, question, current, budget) if self.store.items else ""
        mem = (f"MEMORY BY LOCATION (from earlier work sessions, anchored to current files):\n{packed}"
               if packed else "(no prior-step memory yet)")
        head = (f"<memory_context>\nRepository structure map with attached session memory:\n"
                f"FILES:\n{tree[:3000]}\nKEY SYMBOLS:\n{skel[:6000]}\n")
        return WrapResult(f"{head}{mem}\n</memory_context>\n\n{question}",
                          injected_chars=len(packed), injected=bool(packed),
                          map_chars=len(tree[:3000]) + len(skel[:6000]))

    def write(self, spec, material, delta, env):
        routed = route_structmem(material)
        iid = spec["swebench_instance_id"]
        for p, t in routed.items():
            self.store.add(f"{iid}||{p}", t)
        self.n_writes += 1
        return len(routed)
