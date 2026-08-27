"""兼容垫片:入口已按 scaffold 命名(harness/mini_swe_agent.py)。
新增 harness = 平级新文件(如 harness/swe_edit.py),共享 grading/config/env/constants 与 memory 插件。"""
from harness.mini_swe_agent import main

if __name__ == "__main__":
    main()
