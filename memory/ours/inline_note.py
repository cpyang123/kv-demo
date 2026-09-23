"""`v5inline` —— 会话内自写便签臂(2026-09-22,DESIGN_inline_ledger_0921.md §A.2)。

`fusednote.py` **一字未动**:本文件只 import 它的 `_cut / V4_TAGS / ShuffleWrapMixin /
V4KNote / distill_fused_note`,所以 v5knote 及其便签池、GRPO parquet 全不受影响
(keep-original A/B)。

与 v5knote 的唯一差别在**写侧**:便签不是另起一次写手请求写的,而是 actor 在**同一条会话的
最后一轮**自己写的(执行侧见 `harness/inline_note.py`)。注入壳、模板字段集、校验规则、
FIFO 库、臂名规则全与 v5knote 同形。

回退:便签轮被判不可行(exit 坏、窗口不够、两次输出都不合法、HTTP 挂)时,走
`distill_fused_note(..., writer_prompt_v5_sharp.txt)` —— **与 v5knote 逐字同通路**,
`note_write_mode=fallback_separate` 并记 `note_write_reason`,比例由 ledger_rollup.py 统计。

**v5inline 忽略 RSM_WRITER_API / RSM_WRITER_MODEL**:便签写手 = actor 打的那个 served 名
(所以在挂了 LoRA 的 ckpt 上,是**训练过的 actor 自己**写便签 —— 行为变更,论文须点名)。
`RSM_INLINE_BASE_WRITER=1` 时**回退通路**改打冻结底座 `coder-next`(默认关)。
"""
import os

from ..base import register
from .fusednote import V4_TAGS, ShuffleWrapMixin, V4KNote, distill_fused_note


@register
class V5Inline(ShuffleWrapMixin, V4KNote):
    name = "v5inline"
    llm_writer = True          # runner 据此启用连续零写入哨兵(F9 补)
    inline_self_write = True   # runner 唯一的识别位:旧臂 getattr(...) -> False

    def write(self, spec, material, delta, env):
        p = getattr(self, "pending_inline", None) or {}
        self.pending_inline = None
        note = (p.get("note") or "").strip()
        mode, reason = p.get("mode") or "fallback_separate", p.get("reason") or "no_turn"
        if mode != "inline" or not note:
            note = self._fallback_note(material)
            mode = "fallback_separate" if note.strip() else "none"
        # 2026-09-17 的 rec_extra 回填口径:旧臂没有这个属性 -> runner getattr 取 None -> 空 dict。
        self.rec_extra = {
            "note_write_mode": mode,
            "note_write_reason": reason,
            "n_note_turns": p.get("n_note_turns", 0),
            "note_chars": len(note),
            "note_retries": p.get("retries", 0),
            "note_prompt_tokens": p.get("prompt_tokens"),
            "note_cached_tokens": p.get("cached_tokens"),
            "note_prompt_sha": p.get("prompt_sha"),
            # 2026-09-22:③ 的判据从"命中率 ≥ 0.95"改成"新增后缀有界 + cached 追得上上一条
            # actor prompt",两者都要这两个数,放进 results.jsonl 省得回头拼 calls.jsonl。
            "note_prev_actor_prompt_tokens": p.get("prev_actor_prompt_tokens"),
            "note_suffix_tokens": p.get("suffix_tokens"),
            "note_writer_env_seen": bool(p.get("writer_env_seen")),
        }
        if not note.strip():
            return 0
        self.store.add(spec["swebench_instance_id"], note)
        self.n_writes += 1
        return 1

    def _fallback_note(self, material):
        """与 v5knote 逐字同通路。RSM_INLINE_BASE_WRITER=1 时把回退写手钉回冻结底座。"""
        if os.environ.get("RSM_INLINE_BASE_WRITER") != "1":
            return distill_fused_note(material, prompt_file="writer_prompt_v5_sharp.txt",
                                      tags=V4_TAGS, first_tag_hint="FILES:")
        saved = {k: os.environ.pop(k, None) for k in ("RSM_WRITER_API", "RSM_WRITER_MODEL")}
        try:
            return distill_fused_note(material, prompt_file="writer_prompt_v5_sharp.txt",
                                      tags=V4_TAGS, first_tag_hint="FILES:")
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def stats(self):
        s = super().stats()
        s["inline_self_write"] = True
        return s
