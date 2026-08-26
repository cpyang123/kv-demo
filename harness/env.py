"""RepoEnv:宿主 repo 拷贝 bind-mount 覆盖镜像 repo 路径(chain_runner 验证过的模式;
本集群强制 /gpfs bind 配置与 sandbox/--writable 冲突,故意不用基类 exec_args)。
跨任务工作区持久化 = 复用同一 host_repo 目录。"""
import pathlib, subprocess

from minisweagent.environments.singularity import SingularityEnvironment

from . import constants as K


class RepoEnv(SingularityEnvironment):

    def __init__(self, *, host_repo, repo_path, prefix="", **kwargs):
        self._host_repo = pathlib.Path(host_repo)
        self._repo_path = repo_path
        self._prefix = prefix
        kwargs.setdefault("cwd", repo_path)
        kwargs.setdefault("timeout", K.EXEC_TIMEOUT)
        super().__init__(**kwargs)
        if not self._host_repo.exists():
            self._host_repo.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(
                ["apptainer", "exec", "--bind", f"{self._host_repo.parent}:/rsmwork",
                 self.config.image, "cp", "-a", repo_path, f"/rsmwork/{self._host_repo.name}"],
                capture_output=True, text=True, timeout=K.EXTRACT_TIMEOUT)
            if r.returncode != 0:
                raise RuntimeError(f"repo extraction failed: {r.stderr[-400:]}")

    def _build_sandbox(self):
        return pathlib.Path("/tmp/rsm-noop-sandbox")   # sandbox 模式不用

    def cleanup(self):
        pass   # 基类会 rmtree 假 sandbox 路径;显式 no-op,不靠巧合无害

    def execute(self, action, cwd="", *, timeout=None):
        """agent 通道:观测截断 + Submitted 哨兵检测。"""
        command = self._prefix + action.get("command", "")
        cmd = ["apptainer", "--quiet", "exec", "--contain", "--cleanenv",
               "--bind", f"{self._host_repo}:{self._repo_path}",
               "--pwd", cwd or self.config.cwd, self.config.image, "bash", "-c", command]
        try:
            r = subprocess.run(cmd, text=True, timeout=timeout or self.config.timeout,
                               encoding="utf-8", errors="replace",
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            output = {"output": r.stdout, "returncode": r.returncode, "exception_info": ""}
        except Exception as e:
            raw = getattr(e, "output", None)
            raw = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else (raw or "")
            output = {"output": raw, "returncode": -1,
                      "exception_info": f"An error occurred while executing the command: {e}"}
        if len(output.get("output", "")) > K.OBS_CAP:
            output["output"] = (output["output"][:K.OBS_HEAD] + "\n...[output truncated]...\n"
                                + output["output"][-K.OBS_TAIL:])
        self._check_finished(output)
        return output

    def raw(self, command, timeout=K.RAW_TIMEOUT):
        """判分/setup 通道:带 conda prefix,无 Submitted 检测,无截断(解析要全量输出)。
        TimeoutExpired 会上抛 —— 调用方(grading)负责捕获,setup 阶段 fail-fast 是有意的。"""
        cmd = ["apptainer", "--quiet", "exec", "--contain", "--cleanenv",
               "--bind", f"{self._host_repo}:{self._repo_path}",
               "--pwd", self.config.cwd, self.config.image, "bash", "-c",
               self._prefix + command]
        r = subprocess.run(cmd, text=True, timeout=timeout, encoding="utf-8",
                           errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return {"output": r.stdout, "returncode": r.returncode}
