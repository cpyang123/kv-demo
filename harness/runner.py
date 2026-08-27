"""兼容垫片:入口已按 scaffold 命名(harness/mini_swe_agent.py)。
新增 harness = 平级新文件(如 harness/swe_edit.py),共享 grading/config/env/constants 与 memory 插件。"""
import pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))   # chainswe/ 为包根

from harness.mini_swe_agent import main   # noqa: E402

if __name__ == "__main__":
    main()
