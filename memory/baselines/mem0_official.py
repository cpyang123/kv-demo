"""开源 baseline 接入点(同学负责):Mem0 官方全管线(抽取 + ADD/UPDATE/DELETE ops + 读侧检索)。
接入合同见 memory/base.py。两种移植形态二选一或都做(见项目记录):
  A. 逐轮增量:一个 session = 一轮问答,session 末把全量轨迹作为该轮消息调官方 memory.add;
  B. procedural/agent memory 模式:整轨迹一次性输入。
写手默认官方 gpt-4o-mini(文档化例外);矩阵对照臂(_qw/_qwfull)用本地 Qwen,见 attribution.FactsAll。"""
from ..base import MemoryLayer, register


@register
class Mem0Official(MemoryLayer):
    name = "mem0_official"

    def write(self, spec, material, delta, env):
        raise NotImplementedError("待接入:官方 mem0.Memory add/ops 管线(见模块 docstring)")

    def wrap(self, question, env):
        raise NotImplementedError("待接入:官方检索读侧")
