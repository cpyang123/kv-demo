"""我们的方法:融合便签(fusednote = 27B actor 兼任 writer;trainednote = 独立 writer 端点,
如 SFT ckpt)。模板与输入上限经 env 可换(输入轴/模板族消融复用同一实现)。"""
import json, os, pathlib, random, re, urllib.request

from ..base import MemoryLayer, register, WrapResult, MEM_WRAP_HEAD, STORE_FIFO_CHARS

PROMPTS = pathlib.Path(__file__).parent / "prompts"


def _cut(txt, cap):
    """RSM_NOTE_CAP 截断。

    RSM_VALID_V2=1(与校验器修正同一开关,默认关):截到最后一个完整行,不切在句中。
    起因(2026-09-04 实测,77 条配对):prompt 写的是 "4-8 notes, <=700 chars total",但
    **两个 writer 都不遵守字符预算**(Sonnet ≤700 合规 0%、27B 1%;条数规则倒是 100%/99%
    遵守——可数的规则守得住,跨行累计字符的守不住),真正在执行预算的一直是这个硬帽。
    于是 Sonnet 62% / 27B 21% 的便签末行被切成半句(实例:"...use test_"),半句垃圾照样
    注入给后继会话。改按整行截断:中位只多丢 125 字符,预算不变,对两个 writer 对称。
    """
    if len(txt) <= cap:
        return txt
    head = txt[:cap]
    if os.environ.get("RSM_VALID_V2") != "1":
        return head
    i = head.rfind("\n")
    return head[:i].rstrip() if i > 0 else head


V3_TAGS = ("DID:", "CONTEXT:", "VERIFY:", "LESSON:")
V4_TAGS = ("FILES:", "STATUS:", "VERIFY:", "DID:", "CONTEXT:")      # v4 two-block template (2026-09-05)



# =================================================================================================
# 2026-09-15 **打乱便签**对照臂(交互项判据,用户 13:3x 指定)
#
# 要判的假设是一个**交互项**:mem 训练出来的 ckpt 比 nomem 训练的更会利用便签。
# 只看 B(有便签) − A(无记忆) 分不出"便签有用"和"这个 actor 会读便签" —— 一个从不读便签、
# 只是被便签撑长了上下文的 actor,B−A 也可能是正的。所以加一条**内容对照**:
# 注入的便签**不是本边界自己的**,而是同帧内另一条链在**同一 order** 上、由**同一个未训练 4B
# 写手**用同一份 v5 模板写出来的一条真便签(长度过地板)。表面统计(条数、篇幅、格式、标签分布)
# 全都保住,**只有内容与本任务的对应关系被打断**。
#   真正"会读便签"的 actor 对内容敏感 -> B(真) − B(乱) 大;
#   只是把便签当噪声/当长上下文的 actor -> B(真) ≈ B(乱)。
# 这个差比 B − A 更有辨别力,而且每格只多一条 lane。
#
# 开关(**默认全关,不设 env 时逐字等于旧行为**):
#   RSM_NOTE_SHUFFLE=1        打开打乱(v5knote 臂上也能用;v5shuf 臂默认就是开的,=0 可关掉)
#   RSM_NOTE_POOL=<json>      便签池,build_shuf_pool.py 产出;{chain_id: {"2": body, "3": body}}
#   RSM_NOTE_SHUFFLE_SEED     错位映射的 seed(默认 20260915),同 seed 跨臂跨重放**完全一致**
#   RSM_NOTE_SHUFFLE_FLOOR    供体便签的字符地板(默认 200),不过地板就沿圈换下一条链
#
# 错位映射:把池里的 chain 列表按 seed 洗成一个**单圈**,每条链取圈上**下一条**链在同一
# order 上的便签体。单圈天然**没有不动点** —— 不会有任何一条链拿到自己的便签。
# =================================================================================================
_SHUF_CACHE = {}


def _shuf_pool():
    """-> (pool, cycle)。进程内只读一次;读不到就**硬失败**,绝不静默退化成"无注入"
    (monitor-silent-failure-modes:静默退化会让一条 lane 跑完才发现测的是空气)。"""
    key = os.environ.get("RSM_NOTE_POOL", "")
    if key in _SHUF_CACHE:
        return _SHUF_CACHE[key]
    if not key or not os.path.isfile(key):
        raise SystemExit(f"!! 打乱便签已开启,但 RSM_NOTE_POOL 不可用: {key!r}")
    pool = json.loads(pathlib.Path(key).read_text(encoding="utf-8"))
    cycle = sorted(pool)
    if len(cycle) < 2:
        raise SystemExit(f"!! 便签池只有 {len(cycle)} 条链,凑不出错位映射: {key}")
    random.Random(int(os.environ.get("RSM_NOTE_SHUFFLE_SEED", "20260915"))).shuffle(cycle)
    _SHUF_CACHE[key] = (pool, cycle)
    print(f"NOTE_SHUFFLE: 池 {key} 共 {len(cycle)} 条链,seed="
          f"{os.environ.get('RSM_NOTE_SHUFFLE_SEED', '20260915')}", flush=True)
    return _SHUF_CACHE[key]


def _repo_of(chain_id):
    """链号形如 <repo>_<yyyymmdd>[_<sha>];取第一个 8 位日期段之前的部分当仓库名。"""
    return re.split(r"_\d{8}", chain_id)[0]


def shuffled_body(chain_id, order):
    """-> (供体 chain_id, 便签体) 或 (None, None)。

    RSM_NOTE_SHUFFLE_XREPO(默认 1):**跨仓库**取供体。同一仓库的另一条链虽然边界不同,
    但它的 CONTEXT 行讲的是同一个代码库,对本任务可能仍然真有用 —— 那会把"打乱"这个
    处理做弱。48 条链的单圈里有 4 对是同仓库的,所以这条默认打开;实在找不到跨仓库的
    供体才退回同仓库(并打日志)。设 0 则纯按圈走。"""
    pool, cycle = _shuf_pool()
    floor = int(os.environ.get("RSM_NOTE_SHUFFLE_FLOOR", "200"))
    xrepo = os.environ.get("RSM_NOTE_SHUFFLE_XREPO", "1") != "0"
    if chain_id not in pool:
        return None, None
    i = cycle.index(chain_id)
    me = _repo_of(chain_id)
    fallback = (None, None)
    for d in range(1, len(cycle)):
        donor = cycle[(i + d) % len(cycle)]
        body = (pool.get(donor) or {}).get(str(order)) or ""
        if len(body) < floor:
            continue
        if xrepo and _repo_of(donor) == me:
            if fallback[1] is None:
                fallback = (donor, body)
            continue
        return donor, body
    if fallback[1] is not None:
        print(f"NOTE_SHUFFLE: {chain_id} 找不到跨仓库供体,退回同仓库 {fallback[0]}", flush=True)
    return fallback


class ShuffleWrapMixin:
    """挂在 v5knote 族前面。**force_shuffle=False 且 env 没开时,wrap 原样转给 super()**,
    所以对既有臂是逐字零改动(MRO 多一层、行为不变)。"""
    force_shuffle = False

    def _shuffle_on(self):
        v = os.environ.get("RSM_NOTE_SHUFFLE", "")
        if v == "0":
            return False
        return self.force_shuffle or v == "1"

    def wrap(self, question, env):
        if not self._shuffle_on():
            return super().wrap(question, env)
        chain_id = getattr(self, "chain_id", None)
        order = getattr(self, "task_idx", None)
        if not chain_id or not order:
            raise SystemExit("!! 打乱便签需要 harness 传入 chain_id/task_idx(mini_swe_agent.py "
                             "的 ChainRunner 里设置);当前拿不到,拒绝静默跑错的臂")
        if int(order) < 2:
            return WrapResult(question)          # 链首本来就没有跨任务注入
        donor, body = shuffled_body(chain_id, int(order))
        if not body:
            print(f"NOTE_SHUFFLE: {chain_id} order={order} 池里没有过地板的供体,本题无注入",
                  flush=True)
            return WrapResult(question)
        print(f"NOTE_SHUFFLE: {chain_id} order={order} <- 供体 {donor}({len(body)} 字符)",
              flush=True)
        return WrapResult(f"{MEM_WRAP_HEAD}{body}\n</memory_context>\n\n{question}",
                          injected_chars=len(body), injected=True)


def distill_fused_note(raw_text, prompt_file=None, tags=V3_TAGS, first_tag_hint="DID:"):
    """按融合模板蒸馏便签。默认 actor 端点(RSM_API_BASE);设 RSM_WRITER_API/RSM_WRITER_MODEL
    时用独立 writer 端点。RSM_WRITER_PROMPT 换模板;RSM_DISTILL_CAP/RSM_WRITER_MAXTOK/
    RSM_NOTE_CAP 供全量输入与 free 模板消融放大。
    prompt_file/tags:插件可固定模板与合法行首标签(v4note 用);默认值 = 既有臂的原行为。"""
    # env/模板/上限解析放 try 之外:配置错误(缺 RSM_API_BASE、模板名拼错、cap 非整数)
    # 必须上抛进 mem_write_error 落账,不得静默退化成零写入(rev2-P1)
    api = os.environ.get("RSM_WRITER_API", os.environ["RSM_API_BASE"]).rstrip("/") + "/chat/completions"
    sysp = (PROMPTS / (prompt_file or os.environ.get("RSM_WRITER_PROMPT", "fused_writer_prompt.txt"))).read_text()
    dc = int(os.environ.get("RSM_DISTILL_CAP", "18000"))
    h = dc // 2
    m = raw_text if len(raw_text) <= dc else raw_text[:h] + "\n...[middle truncated]...\n" + raw_text[-h:]
    body = {"model": os.environ.get("RSM_WRITER_MODEL", "coder-next"), "temperature": 0.0,
            "max_tokens": int(os.environ.get("RSM_WRITER_MAXTOK", "900")),
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": sysp}, {"role": "user", "content": m}]}
    if os.environ.get("RSM_OPENAI_STRICT") == "1":
        # 2026-10-20(env 门控,默认关):OpenAI 协议网关不认 vLLM 专有的 chat_template_kwargs,上游新模型只认
        # max_completion_tokens / 默认 temperature(见 demo/goodvision.md);本地路径的请求体逐字不变。
        body.pop("chat_template_kwargs"); body.pop("temperature")
        body["max_completion_tokens"] = body.pop("max_tokens")
    _hdr = {"Content-Type": "application/json"}
    _key = os.environ.get("RSM_WRITER_API_KEY") or os.environ.get("RSM_API_KEY")
    if _key and _key != "EMPTY":
        _hdr["Authorization"] = f"Bearer {_key}"   # 网关鉴权;本地 vLLM 不设 key 时头部不变
    def _valid(t):
        """结构校验:防 writer 被轨迹格式带跑续写(实测 14% 废品率的根因)。

        RSM_VALID_V2=1(env 门控,默认关——v3main100 等存量运行保持原行为):去掉裸
        "mswea_bash_command" 子串检查。依据:harness 的工具调用语法永远带围栏
        (swebench_backticks.yaml 里全部为 ```mswea_bash_command),已被同句的 "```" 检查
        严格覆盖;裸子串检查对本来目的冗余,只在 writer **提及**该工具名时假阳性。
        实测(2026-09-04,Sonnet writer,5 个 boundary):1 条因一句真实环境教训
        "/tmp is cleared between mswea_bash_command invocations" 被判空写 —— 便签越具体
        越容易中招,会把 writer 对比污染成"更具体 = 更多空写"。开启时须对被比较的
        **所有** writer 臂同时开启。
        """
        t = t.strip()
        if not t.startswith(tags) or "```" in t:
            return False
        return os.environ.get("RSM_VALID_V2") == "1" or "mswea_bash_command" not in t

    try:
        cap = int(os.environ.get("RSM_NOTE_CAP", "1500"))
        for attempt in range(2):
            req = urllib.request.Request(api, json.dumps(body).encode(), dict(_hdr))
            r = json.load(urllib.request.urlopen(req, timeout=300))
            txt = r["choices"][0]["message"]["content"].split("</think>")[-1].strip()
            txt = txt.strip("`\n ").removeprefix("```").strip()
            if _valid(txt):
                return _cut(txt, cap)
            # 一次矫正重试:显式点名违规(结构保证,不赌 prompt 自觉)
            body["messages"] = body["messages"][:2] + [
                {"role": "assistant", "content": txt[:500]},
                {"role": "user", "content": "INVALID OUTPUT. Do not continue the transcript or emit "
                 f"commands/code fences. Output ONLY note lines, first line starting with '{first_tag_hint}'."}]
        print(f"FUSED_DISTILL_INVALID after retry: {txt[:80]!r}", flush=True)
        return ""
    except OSError:
        raise   # 连接类故障上抛→runner 记 mem_write_error→零写入哨兵可见(P1-4 配套)
    except Exception as e:
        print(f"FUSED_DISTILL_ERROR: {type(e).__name__}: {str(e)[:100]}", flush=True)
        return ""


@register
class FusedNote(MemoryLayer):
    name = "fusednote"
    llm_writer = True   # runner 据此启用连续零写入哨兵(F9 补)

    def write(self, spec, material, delta, env):
        note = distill_fused_note(material)
        if not note.strip():
            return 0
        self.store.add(spec["swebench_instance_id"], note)
        self.n_writes += 1
        return 1


# ---- 字段遮罩臂(2026-09-02,用户:对现有便签删掉某一类行,看性能是否下降 = 模板的字段级消融)----
# writer/协议/注入壳与 fusednote 完全相同,只在 store.add 之前按行类型机械过滤;注入字符按过滤后计。
import re as _re
_VER = _re.compile(r"\s*\|\s*VERIFY:.*$")

def mask_note(note, drop):
    """drop ⊆ {DID, CONTEXT, LESSON, VERIFY}。VERIFY 指去掉 DID 行内联的 '| VERIFY: …'(保留 DID 文本);
    去 DID 会连同其内联 VERIFY 一起去掉(内联格式下二者不可分)。"""
    out = []
    for l in note.splitlines():
        if not l.strip(): continue
        head = l.split(":", 1)[0].strip()
        if head in drop: continue
        if "VERIFY" in drop and l.startswith("DID:"): l = _VER.sub("", l).rstrip()
        out.append(l)
    return "\n".join(out)


class _MaskedFused(FusedNote):
    drop = frozenset()

    def write(self, spec, material, delta, env):
        note = mask_note(distill_fused_note(material), self.drop)
        if not note.strip():
            return 0
        self.store.add(spec["swebench_instance_id"], note)
        self.n_writes += 1
        return 1


@register
class FusedNoDID(_MaskedFused):
    name = "fusednote_nodid"; drop = frozenset({"DID"})

@register
class FusedNoCTX(_MaskedFused):
    name = "fusednote_noctx"; drop = frozenset({"CONTEXT"})

@register
class FusedNoLESSON(_MaskedFused):
    name = "fusednote_nolesson"; drop = frozenset({"LESSON"})

@register
class FusedNoVERIFY(_MaskedFused):
    name = "fusednote_noverify"; drop = frozenset({"VERIFY"})


@register
class V4Note(FusedNote):
    """v4 两块模板(2026-09-05,由 L1 归因设计):TASK 块 FILES/STATUS/VERIFY*/DID + REPO 块 CONTEXT。
    writer 通路与 fusednote 完全相同(同端点、温度 0、同输入上限),只换模板文件与合法行首标签。"""
    name = "v4note"

    def write(self, spec, material, delta, env):
        note = distill_fused_note(material, prompt_file="writer_prompt_v4.txt", tags=V4_TAGS, first_tag_hint="FILES:")
        if not note.strip():
            return 0
        self.store.add(spec["swebench_instance_id"], note)
        self.n_writes += 1
        return 1


@register
class V4KNote(FusedNote):
    """v4knote = v4 模板 + 零内容锐化 + **占位符骨架**(writer_prompt_v4_skel.txt),2026-09-06。

    为什么还要再开一个臂:2026-09-06 四臂探针(24 份 attrib_genpass_v1 材料,同一个未训练
    Qwen3-4B,温度 0,只换 prompt)测得 ——
        A 原始 v4        REPO 块  4.2% | 越界行 25.4%
        B 仅加锐化指令   REPO 块  8.3% | 越界行 30.9%   <- 曾开为 v4snote 臂,几乎无效;
                                                            模板 v4_sharp 从未落盘,该臂
                                                            2026-09-10 删除
        C 加占位符骨架   REPO 块 91.7% | 越界行  0.9%   <- 本臂
        D 加真实样例     REPO 块 87.5% | 越界行  0.0%   (样例出自 split_v2_test 链,已弃用)
    骨架里全是尖括号占位符,零真实仓库内容,所以不构成测试集泄漏。
    GRPO 的 parquet 必须用同一份 prompt 构建,否则是离分布评测(见 [B1] 的教训)。
    """
    name = "v4knote"

    def write(self, spec, material, delta, env):
        note = distill_fused_note(material, prompt_file="writer_prompt_v4_skel.txt",
                                  tags=V4_TAGS, first_tag_hint="FILES:")
        if not note.strip():
            return 0
        self.store.add(spec["swebench_instance_id"], note)
        self.n_writes += 1
        return 1


@register
class V5KNote(ShuffleWrapMixin, V4KNote):
    """v5knote = v4knote 的孪生臂,唯一差别是写手提示换成 writer_prompt_v5_sharp.txt(2026-09-11)。

    v5 相对 v4 只删两条规则(见 prompts/writer_prompt_v5.txt):
      删 "VERIFY 是便签里最重要的部分,给它最多篇幅"
      删 "若须删减,按 CONTEXT->DID->STATUS 的顺序,且永不删 FILES/VERIFY"
      增 "行类型之间没有固定优先级,也不要求每类都写;按本次会话实际确立了什么分配篇幅"
    依据:VERIFY 占便签 28% 篇幅,却 94% 记 exit 0、在继承状态上真跑的检测率 0/28、误报 23%。

    **为什么必须单开一个臂**:V4KNote.write 把 prompt_file 写死成 writer_prompt_v4_skel.txt。
    v5 的 GRPO parquet 是用 v5_sharp 构建的,若沿用 v4knote 评测,写手在训练与考场看到的
    提示不同 —— 就是离分布评测,正是 trainednote 臂踩过的坑(它回退到 v3 融合模板)。
    训练与评测必须同契约。
    """
    name = "v5knote"

    def write(self, spec, material, delta, env):
        note = distill_fused_note(material, prompt_file="writer_prompt_v5_sharp.txt",
                                  tags=V4_TAGS, first_tag_hint="FILES:")
        if not note.strip():
            return 0
        self.store.add(spec["swebench_instance_id"], note)
        self.n_writes += 1
        return 1


@register
class TrainedNote(FusedNote):
    """SFT/RL writer 评测臂:同通路,writer 换独立端点(lane 设 RSM_WRITER_API 指 ckpt vLLM)。"""
    name = "trainednote"


# ---------------------------------------------------------------------------------------------
# 2026-09-08 读取侧门控臂:写手通路与 v4knote 逐字相同(同模板、同端点),只在**读取时**多一步 —— 把下一任务题面与便签交给
# reader 策略端点(RSM_READER_API/RSM_READER_MODEL,训练见 train-corpus/cs4b_train_reader_v1.sh),按其回答只注入
# {none, task, repo, full} 之一。同一 run-id 下与 v4knote(总是全注入)逐槽配对即为门控收益。
READER_SYS = ("You are the memory reader of a coding agent. A previous session in this repository left the memory note below. "
              "The next task is given. Decide which part of the note to place in the next session's context. "
              "Options: none (inject nothing), task (only the FILES/STATUS/VERIFY/DID lines), repo (only the CONTEXT lines), full (the whole note). "
              "Injected text costs the next session tokens; inject only what will save it work. "
              "Answer with exactly one line of the form INJECT: <none|task|repo|full>.")
_TASK_TAGS = ("FILES:", "STATUS:", "VERIFY:", "DID:"); _REPO_TAGS = ("CONTEXT:",)


READER_SYS_V5 = ("You are the memory reader of a coding agent. A previous session in this repository left the memory note below. "
                 "The next task is given. Decide whether to place the note in the next session's context. "
                 "Options: none (inject nothing) or full (inject the whole note). Injected text costs the next session tokens on every step; "
                 "inject only when the note will save it more work than it costs. "
                 "First write one line starting with REASON: that names the overlap (or lack of it) between the note and the next task, then a final line of the form INJECT: <none|full>.")


def _reader_decide(question, note):
    """-> one of none/task/repo/full.  Any failure falls back to RSM_READER_FALLBACK (default full) and is logged.
    RSM_READER_FORMAT=v1 (default): one-line INJECT prompt (reader v1).  =v5: the reader_v5/distill training prompt
    (2-way none|full, user turn ends with REASON:, the model writes REASON then INJECT; parse the LAST INJECT).
    RSM_GATE_SCORE_THR=<float>: with format v5, decide by the model's 'SCORE: <0-100>' line instead of its INJECT line
    (full iff score >= thr) -- the calibrated-threshold mode for the Sonnet-distilled value model."""
    import re as _re
    api = os.environ.get("RSM_READER_API")
    fb = os.environ.get("RSM_READER_FALLBACK", "full"); fmt = os.environ.get("RSM_READER_FORMAT", "v1"); thr = os.environ.get("RSM_GATE_SCORE_THR")
    if not api:
        print("READER_GATE: no RSM_READER_API, fallback", fb, flush=True); return fb
    if fmt in ("v5", "v5score"):
        body_note = "\n".join(l for l in note.splitlines() if l.strip() and not l.startswith("["))
        sysm = READER_SYS_V5 if fmt == "v5" else READER_SYS_V5 + " Before the INJECT line, write a line 'SCORE: <0-100>', your probability that injecting the note reduces the next session's total cost."
        body = {"model": os.environ.get("RSM_READER_MODEL", "reader"), "temperature": 0.0, "max_tokens": 220,
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "system", "content": sysm},
                             {"role": "user", "content": f"NEXT TASK:\n{question[:6000]}\n\nMEMORY NOTE:\n{body_note}\n\nREASON:"}]}
    else:
        body = {"model": os.environ.get("RSM_READER_MODEL", "reader"), "temperature": 0.0, "max_tokens": 16,
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "system", "content": READER_SYS},
                             {"role": "user", "content": f"NEXT TASK:\n{question[:6000]}\n\nMEMORY NOTE:\n{note}\n\nINJECT:"}]}
    try:
        req = urllib.request.Request(api.rstrip("/") + "/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
        txt = json.load(urllib.request.urlopen(req, timeout=180))["choices"][0]["message"]["content"].split("</think>")[-1]
        if fmt in ("v5", "v5score"):
            if thr:
                sm = _re.search(r"SCORE:\s*(\d+(?:\.\d+)?)", txt)
                if sm:
                    sc = float(sm.group(1)); d = "full" if sc >= float(thr) else "none"; print(f"READER_GATE: score {sc:.0f} thr {thr} -> {d}", flush=True); return d
                print(f"READER_GATE: no SCORE in {txt[:60]!r}, fallback {fb}", flush=True); return fb
            ms = list(_re.finditer(r"INJECT\s*:\s*(none|task|repo|full)", txt, _re.I))
            if ms: return ms[-1].group(1).lower()
        else:
            m = _re.search(r"(none|task|repo|full)", txt, _re.I)
            if m: return m.group(1).lower()
        print(f"READER_GATE: unparseable {txt[:60]!r}, fallback {fb}", flush=True)
    except Exception as e:
        print(f"READER_GATE: error {e}, fallback {fb}", flush=True)
    return fb


def _select_block(note, choice):
    ls = [l for l in note.splitlines() if l.strip()]
    if choice == "full": return "\n".join(ls)
    if choice == "task": return "\n".join(l for l in ls if l.startswith(_TASK_TAGS))
    if choice == "repo": return "\n".join(l for l in ls if l.startswith(_REPO_TAGS))
    return ""


@register
@register
class V4KVerify(V4KNote):
    """2026-09-09 challenge-1 pilot (zero-parameter): v4knote injection plus a standing instruction to VERIFY the
    inherited state first (run the note's VERIFY/TESTS commands) and REPAIR the previous session's breakage before
    starting the task.  Same notes, same writer, same actor as v4knote; only the wrap differs."""
    name = "v4kverify"
    VERIFY_INSTR = ("\n<inherited_state_protocol>\nThe working tree is inherited from the previous session described in the memory context above; "
                    "that session may have left it broken (its tests failing or its change incomplete). "
                    "Before working on the task below: (1) run the verification command(s) recorded in the memory note (VERIFY / TESTS lines); "
                    "(2) if they fail, first repair the previous session's change (the note's FILES / DID lines say what it touched) until those commands pass; "
                    "(3) only then start the task. Never modify test files. If the commands already pass, proceed directly.\n</inherited_state_protocol>\n\n")

    def wrap(self, question, env):
        w = super().wrap(question, env)
        if not w.injected:
            return w
        text = w.text.replace("</memory_context>\n\n", "</memory_context>\n" + self.VERIFY_INSTR, 1) if "</memory_context>\n\n" in w.text else w.text
        return WrapResult(text, injected_chars=w.injected_chars + len(self.VERIFY_INSTR), injected=True)


class V4KGate(V4KNote):
    """v4knote + 读取时的策略门控(见文件末注释)。stats 里记每种决策的次数。"""
    name = "v4kgate"

    def __init__(self):
        super().__init__(); self.decisions = {"none": 0, "task": 0, "repo": 0, "full": 0}

    def wrap(self, question, env):
        if not self.store.items:
            return WrapResult(question)
        kept = []
        for name, note in self.store.items:
            c = _reader_decide(question, note); self.decisions[c] = self.decisions.get(c, 0) + 1
            body = _select_block(note, c)
            if body: kept.append((name, body))
        print(f"READER_GATE decisions so far: {self.decisions}", flush=True)
        if not kept:
            return WrapResult(question)
        b = "\n\n".join(f"[{n}]\n{t}" for n, t in kept)[-STORE_FIFO_CHARS:]
        return WrapResult(f"{MEM_WRAP_HEAD}{b}\n</memory_context>\n\n{question}", injected_chars=len(b), injected=True)

    def stats(self):
        s = super().stats(); s["reader_decisions"] = dict(self.decisions); return s


@register
class V5Shuf(V5KNote):
    """**打乱便签**对照臂(2026-09-15)。写手通路、模板、端点、输入上限与 v5knote **逐字相同**
    —— 它照样把自己这条链的便签写出来(所以 lane 形态、写手开销、时序都与 v5knote 一致),
    只是**注入时换成同帧另一条链同 order 的真便签**(见上面 ShuffleWrapMixin 的抬头)。
    臂名 mem_v5shuf_<actor臂>;与同格的 mem_v5knote_<actor臂> 逐槽配对就是"内容敏感度"。
    mem_stats.json 里的 n_writes/store_chars 记的仍是**它自己写的**那条(没被注入),这点
    读表时要知道。"""
    name = "v5shuf"
    force_shuffle = True
