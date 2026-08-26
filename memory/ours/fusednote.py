"""我们的方法:融合便签(fusednote = 27B actor 兼任 writer;trainednote = 独立 writer 端点,
如 SFT ckpt)。模板与输入上限经 env 可换(输入轴/模板族消融复用同一实现)。"""
import json, os, pathlib, urllib.request

from ..base import MemoryLayer, register

PROMPTS = pathlib.Path(__file__).parent / "prompts"


def distill_fused_note(raw_text):
    """按融合模板蒸馏便签。默认 actor 端点(RSM_API_BASE);设 RSM_WRITER_API/RSM_WRITER_MODEL
    时用独立 writer 端点。RSM_WRITER_PROMPT 换模板;RSM_DISTILL_CAP/RSM_WRITER_MAXTOK/
    RSM_NOTE_CAP 供全量输入与 free 模板消融放大。"""
    try:
        api = os.environ.get("RSM_WRITER_API", os.environ["RSM_API_BASE"]).rstrip("/") + "/chat/completions"
        sysp = (PROMPTS / os.environ.get("RSM_WRITER_PROMPT", "fused_writer_prompt.txt")).read_text()
        dc = int(os.environ.get("RSM_DISTILL_CAP", "18000"))
        h = dc // 2
        m = raw_text if len(raw_text) <= dc else raw_text[:h] + "\n...[middle truncated]...\n" + raw_text[-h:]
        body = {"model": os.environ.get("RSM_WRITER_MODEL", "coder-next"), "temperature": 0.0,
                "max_tokens": int(os.environ.get("RSM_WRITER_MAXTOK", "900")),
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "system", "content": sysp}, {"role": "user", "content": m}]}
        req = urllib.request.Request(api, json.dumps(body).encode(), {"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=300))
        txt = r["choices"][0]["message"]["content"].split("</think>")[-1].strip()
        return txt.strip("`\n ").removeprefix("```").strip()[: int(os.environ.get("RSM_NOTE_CAP", "1500"))]
    except Exception as e:
        print(f"FUSED_DISTILL_ERROR: {type(e).__name__}: {str(e)[:100]}", flush=True)
        return ""


@register
class FusedNote(MemoryLayer):
    name = "fusednote"

    def write(self, spec, material, delta, env):
        note = distill_fused_note(material)
        if not note.strip():
            return 0
        self.store.add(spec["swebench_instance_id"], note)
        self.n_writes += 1
        return 1


@register
class TrainedNote(FusedNote):
    """SFT/RL writer 评测臂:同通路,writer 换独立端点(lane 设 RSM_WRITER_API 指 ckpt vLLM)。"""
    name = "trainednote"
