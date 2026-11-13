"""mini-swe-agent's text-based model, with the conversation cleaned for strict OpenAI-protocol gateways.

mini-swe-agent stores each reply as `response.choices[0].message.model_dump()` and sends it back on the next call
with only its own "extra" key removed, so every assistant turn carries `tool_calls: null`, `function_call: null`
and `provider_specific_fields`. The GoodVision SmartRouter refuses any request that looks tool-related with
422/400 "No model offer satisfies the effective policy" (demo/goodvision.md), so with it the first call of a task
worked and every later call failed (2026-11-01, `auto` and fixed ids alike), and the task ended in
error:BadRequestError after ~4 minutes of retries.

With RSM_OPENAI_STRICT=1 (set by demo/server.py for gateway runs) only role / content / name are sent. Otherwise
the class is LitellmTextbasedModel unchanged, so the local vLLM path of the paper runs is identical.
"""
import os

from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

_KEEP = ("role", "content", "name")


def strict_messages(messages):
    return [{k: m[k] for k in _KEEP if m.get(k) is not None} for m in messages]


class GatewayTextModel(LitellmTextbasedModel):
    def _prepare_messages_for_api(self, messages):
        prepared = super()._prepare_messages_for_api(messages)
        return strict_messages(prepared) if os.environ.get("RSM_OPENAI_STRICT") == "1" else prepared
