"""import 本包即完成全部插件注册(rev2-P2:合同承诺"写子类+@register 零改动"的闭环)。"""
from . import base                      # noqa: F401
from .attribution import probes         # noqa: F401
from .ours import fusednote             # noqa: F401
from .baselines import mem0_official    # noqa: F401
from .baselines import memop_prompt     # noqa: F401
from .baselines import reasoningbank    # noqa: F401
from .baselines import cline_membank    # noqa: F401
from .ours import inline_note          # noqa: F401  (2026-09-22 v5inline;纯注册,不改任何旧臂)
