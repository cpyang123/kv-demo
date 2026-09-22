"""memory layer 插件接口 —— 接入合同(v2, 2026-08-26)。

接一个新 memory 系统 = 写一个 MemoryLayer 子类 + @register,runner 零改动。

生命周期(runner 视角,每条链新建实例,store 从空开始):
    layer = REGISTRY[name]()
    for task in chain:
        w = layer.wrap(problem_statement, env)     # 注入;w.text 交给 agent
        ... agent 跑完 session ...
        n = layer.write(spec, material, delta, env)  # session 边界批量写
        ... runner 记 w.injected_chars / w.map_chars / n ...

度量口径(审计 F10):injected_chars 只计跨任务记忆载荷;每任务现算的结构图记 map_chars,
两者分开;injected 旗标只看跨任务库非空。

约定:material 的截断由 harness 统一控制(constants.raw_*_cap),插件不得自行截断输入;
插件内部 LLM 调用一律走本地端点 env 变量(RSM_WRITER_API/RSM_FACTS_API 等),
外部 API 仅 Mem0 官方 writer 文档化例外。"""
from dataclasses import dataclass, field


@dataclass
class WrapResult:
    text: str                 # 交给 agent 的完整题面(含注入)
    injected_chars: int = 0   # 跨任务记忆载荷字符数
    injected: bool = False    # 是否注入了跨任务记忆
    map_chars: int = 0        # 现算结构图字符数(repomap/mapdiff/structmem)


MEM_WRAP_HEAD = "<memory_context>\nNotes from earlier work sessions on this repository:\n"
import os as _os
STORE_FIFO_CHARS = int(_os.environ.get("RSM_STORE_FIFO_CHARS", "30000"))   # 注入库 FIFO 帽:注入 = 最近 30K 字符(rawcat 等原始臂的真实注入量;2026-09-02 核实主表 rawcat 中位 23K/最大 30K)


@dataclass
class LocalStore:
    """追加式注入库(rawcat/factsall 等臂的公共底座):FIFO 截断保最近 30K 字符。"""
    items: list = field(default_factory=list)     # [(name, payload)]

    def add(self, name, payload):
        self.items.append((name, payload))

    def body(self):
        return "\n\n".join(f"[{n}]\n{t}" for n, t in self.items)[-STORE_FIFO_CHARS:]

    def wrap(self, question):
        if not self.items:
            return WrapResult(question)
        b = self.body()
        return WrapResult(f"{MEM_WRAP_HEAD}{b}\n</memory_context>\n\n{question}",
                          injected_chars=len(b), injected=True)

    def chars(self):
        return sum(len(t) for _, t in self.items)


class MemoryLayer:
    name = "base"

    def __init__(self):
        self.store = LocalStore()
        self.n_writes = 0

    def wrap(self, question, env) -> WrapResult:
        return self.store.wrap(question)

    def write(self, spec, material, delta, env) -> int:
        """session 边界批量写。material=harness 构建的轨迹文本;delta=本任务真增量 diff。
        返回写入节点数。"""
        return 0

    def stats(self):
        return {"n_writes": self.n_writes, "store_chars": self.store.chars()}


REGISTRY: dict = {}


def register(cls):
    REGISTRY[cls.name] = cls
    return cls
