"""One-shot, asynchronous ZCode CLI runs. No shell or model-config mutation."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import uuid

from workspace import atomic_write, sanitize_text


class RunnerError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _expand_wrapper_path(value, directory, variables):
    text = value.strip().strip('"')
    text = re.sub(r"%~dp0", lambda _: str(directory) + os.sep, text, flags=re.I)
    for _ in range(6):
        previous = text
        text = re.sub(r"%([A-Za-z_][A-Za-z0-9_]*)%", lambda match: variables.get(match.group(1).upper(), os.environ.get(match.group(1), match.group(0))), text)
        if text == previous:
            break
    if "%" in text:
        return None
    path = Path(text).expanduser()
    if path.is_absolute():
        return path.resolve()
    relative = directory / path
    if relative.is_file():
        return relative.resolve()
    found = shutil.which(text)
    return Path(found).resolve() if found else relative.resolve()


def _desktop_bundle(executable):
    """Recognize an installed desktop distribution by its adjacent CLI asset."""
    path = Path(executable).expanduser().resolve()
    if path.name.lower() != "zcode.exe":
        return None
    bundle = path.parent / "resources" / "glm" / "zcode.cjs"
    return bundle.resolve() if bundle.is_file() else None


def _installed_candidates():
    candidates = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "ZCode" / "ZCode.exe")
    if os.name == "nt":
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for access in (winreg.KEY_READ | winreg.KEY_WOW64_64KEY, winreg.KEY_READ | winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\ZCode.exe", 0, access) as key:
                        value, _ = winreg.QueryValueEx(key, None)
                        candidates.append(Path(os.path.expandvars(value.strip('"'))))
                except OSError:
                    pass
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / "ZCode" / "ZCode.exe")
    return candidates


def command_environment(command, inherited=None):
    """Prepare only this subprocess; never write ZCode configuration or defaults."""
    environment = dict(os.environ if inherited is None else inherited)
    executable = Path(command[0]).resolve()
    bundle = _desktop_bundle(executable)
    if bundle and len(command) > 1 and Path(command[1]).resolve() == bundle:
        # Running the desktop executable directly otherwise launches Electron and
        # may exit 0 after logging, without executing the CLI task at all.
        environment["ELECTRON_RUN_AS_NODE"] = "1"
        builtin = executable.parent / "resources" / "config" / "provider" / "zcode-builtin.json"
        if builtin.is_file():
            for variable in ("ZCODE_BUILTIN_PROVIDER_CONFIG_FILE", "ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE"):
                if not environment.get(variable, "").strip():
                    environment[variable] = str(builtin.resolve())
    elif executable.name.lower() in {"node", "node.exe"}:
        environment["PATH"] = str(executable.parent) + os.pathsep + environment.get("PATH", "")
    return environment


def resolve_command(commandPath=None):
    """Resolve native CLI or a recognized Node wrapper without executing it.

    Explicit argument vectors support embedders and fake CLI tests. Arbitrary cmd
    wrappers are rejected rather than passing the task through cmd.exe parsing.
    """
    if isinstance(commandPath, (list, tuple)):
        if not commandPath or not all(isinstance(value, (str, os.PathLike)) and str(value) for value in commandPath):
            raise RunnerError("CLI 参数列表无效。")
        command = [str(value) for value in commandPath]
        if not Path(command[0]).is_file():
            raise RunnerError("CLI 可执行文件不存在。")
        if Path(command[0]).suffix.lower() in {".cmd", ".bat"}:
            raise RunnerError("参数列表不能经 cmd/bat 解释任务。请选择可解析的 ZCode 包装器路径。")
        bundle = _desktop_bundle(command[0])
        if bundle and (len(command) == 1 or Path(command[1]).resolve() != bundle):
            command.insert(1, str(bundle))
        return command
    if commandPath is None or not str(commandPath).strip():
        # Prefer the installed desktop's CLI, not a hard-coded development tree.
        candidates = [candidate for candidate in _installed_candidates() if _desktop_bundle(candidate)]
        discovered = shutil.which("zcode")
        if discovered:
            candidates.append(Path(discovered))
        commandPath = next((candidate for candidate in candidates if candidate.is_file()), None)
        if commandPath is None:
            raise RunnerError("未找到 ZCode CLI，请选择 zcode.exe 或 zcode.cmd。")
    path = Path(commandPath).expanduser().resolve()
    if not path.is_file():
        raise RunnerError("所选 ZCode CLI 文件不存在。")
    bundle = _desktop_bundle(path)
    if bundle:
        return [str(path), str(bundle)]
    if path.suffix.lower() not in {".cmd", ".bat"}:
        if os.name == "nt" and path.suffix.lower() != ".exe":
            raise RunnerError("请选择 zcode.exe 或可解析的 zcode.cmd。")
        return [str(path)]
    if path.stat().st_size > 65536:
        raise RunnerError("CLI 包装器过大，无法安全解析。")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    variables = {"DP0": str(path.parent) + os.sep}
    for line in text.splitlines():
        assignment = re.match(r'^\s*@?set\s+(?:"([^"=]+)=([^\r\n]*)"|([^\s=]+)=(.*))\s*$', line, re.I)
        if assignment:
            name, value = (assignment.group(1), assignment.group(2)) if assignment.group(1) else (assignment.group(3), assignment.group(4))
            variables[name.upper()] = value
    for line in text.splitlines():
        invocation = re.match(r'^\s*@?("[^"]+"|[^\s"]+)\s+("[^\r\n"]+\.(?:cjs|mjs|js)")\s+%\*\s*$', line, re.I)
        if not invocation:
            continue
        executable = _expand_wrapper_path(invocation.group(1), path.parent, variables)
        script = _expand_wrapper_path(invocation.group(2), path.parent, variables)
        if executable and script and executable.is_file() and script.is_file() and (executable.name.lower() in {"node", "node.exe"} or _desktop_bundle(executable) == script) and "zcode" in str(script).lower():
            return [str(executable), str(script)]
    raise RunnerError("无法安全解析这个 CLI 包装器。请选择其真实 zcode.exe，或包含 Node 与 ZCode 脚本路径的标准包装器；不会经 shell 传递任务。")


def _json_result(stdout):
    # --json is one complete summary object, not JSON hidden inside Electron
    # logs, an event, or a nested tool result. Preserve polluted output for review.
    try:
        value = json.loads(stdout.lstrip("\ufeff").strip())
    except ValueError:
        return None
    if isinstance(value, dict) and ("response" in value or value.get("type") == "result"):
        return value
    return None


class ZCodeRunner:
    def __init__(self, commandPath=None, cwd=None, timeout_seconds=None, *, mode="edit"):
        if not isinstance(mode, str) or mode not in {"edit", "plan", "build", "guarded", "yolo"}:
            raise RunnerError("运行模式无效；请选择 edit、plan、build、guarded 或 yolo。")
        if cwd is None:
            raise RunnerError("需要明确的项目工作区。")
        self.cwd = Path(cwd).expanduser().resolve()
        if not self.cwd.is_dir():
            raise RunnerError("项目工作区不存在。")
        self.command = resolve_command(commandPath)
        self.timeout_seconds = timeout_seconds
        self.mode = mode
        self._runs = {}
        self._lock = threading.RLock()

    def start(self, taskPath, on_output=None, on_done=None, *, prompt=None, resume=None):
        task = Path(taskPath).expanduser().resolve()
        try:
            task.relative_to(self.cwd)
        except ValueError as exc:
            raise RunnerError("任务文件必须位于当前工作区中。") from exc
        if not task.is_file():
            raise RunnerError("任务文件不存在。")
        if prompt is None:
            prompt = f"读取“{task}”，按其中说明整理能力草稿。"
        elif not isinstance(prompt, str) or not prompt.strip() or re.search(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", prompt):
            raise RunnerError("提示必须是非空单段文字，不能含换行或控制字符。")
        if resume is not None and (not isinstance(resume, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", resume)):
            raise RunnerError("续聊会话编号无效；仅支持字母、数字、点、下划线和短横线，且不能以符号开头。")
        with self._lock:
            if any(entry["state"].get("taskPath") == str(task) and entry["state"].get("status") in {"admitted", "running"} for entry in self._runs.values()):
                raise RunnerError("该任务仍在执行，请查看原运行记录，不要重复提交。")
        run_id = "run-" + uuid.uuid4().hex[:16]
        directory = task.parent / "runs" / run_id
        if not directory.resolve().is_relative_to(self.cwd):
            raise RunnerError("运行记录路径超出当前工作区。")
        directory.mkdir(parents=True)
        args = self.command + ["--cwd", str(self.cwd), "--mode", self.mode, "--json"]
        if resume is not None:
            args += ["--resume", resume]
        args += ["-p", prompt]
        state = {"runId": run_id, "status": "admitted", "admittedAt": _now(), "cwd": str(self.cwd), "taskPath": str(task), "prompt": prompt, "mode": self.mode, "command": self.command, "runPath": str(directory / "run.json"), "stdoutPath": str(directory / "stdout.txt"), "stderrPath": str(directory / "stderr.txt"), "capabilityStatus": "draft", "note": "本地任务已登记；进程完成只表示草稿生成结束，不表示网页能力已验证。"}
        if resume is not None:
            state["resumeSessionId"] = resume
        entry = {"state": state, "process": None, "thread": None, "done": threading.Event(), "stopReason": None}
        with self._lock:
            self._runs[run_id] = entry
            self._save(entry)
        try:
            environment = command_environment(self.command)
            process = subprocess.Popen(args, cwd=str(self.cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1, shell=False, env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, ValueError) as exc:
            state.update({"status": "failed", "finishedAt": _now(), "error": sanitize_text(exc, 1500)})
            self._save(entry)
            entry["done"].set()
            self._callback(on_done, run_id, dict(state))
            return run_id
        entry["process"] = process
        state.update({"status": "running", "startedAt": _now(), "pid": process.pid})
        self._save(entry)
        thread = threading.Thread(target=self._monitor, args=(entry, on_output, on_done), daemon=True)
        entry["thread"] = thread
        thread.start()
        return run_id

    def _save(self, entry):
        with self._lock:
            state = entry["state"]
            atomic_write(Path(state["runPath"]), json.dumps(state, ensure_ascii=False, indent=2) + "\n")

    @staticmethod
    def _callback(callback, *args):
        if callback:
            try:
                callback(*args)
            except Exception:
                pass  # A UI callback must not corrupt task lifecycle or retry it.

    def _monitor(self, entry, on_output, on_done):
        state, process = entry["state"], entry["process"]
        buffers = {"stdout": [], "stderr": []}
        truncated = {"stdout": False, "stderr": False}
        output_limit = 16 * 1024 * 1024

        def read_stream(name, stream):
            total = 0
            with Path(state[name + "Path"]).open("w", encoding="utf-8", newline="\n") as log:
                for text in stream:
                    # Parse the original in-memory protocol; redacting a raw
                    # JSON string may otherwise alter JSON syntax. Files and
                    # UI callbacks retain the existing credential redaction.
                    kept = text[:max(0, output_limit - total)]
                    if kept:
                        log.write(sanitize_text(kept, None))
                        log.flush()
                        buffers[name].append(kept)
                        total += len(kept)
                    if len(kept) != len(text):
                        truncated[name] = True
                    self._callback(on_output, state["runId"], name, sanitize_text(text, 262144))
            stream.close()

        readers = [threading.Thread(target=read_stream, args=(name, getattr(process, name)), daemon=True) for name in buffers]
        for reader in readers:
            reader.start()
        try:
            return_code = process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            entry["stopReason"] = "timeout_result_uncertain"
            process.terminate()
            try:
                return_code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                return_code = process.wait()
        for reader in readers:
            reader.join(timeout=5)
        result = None if truncated["stdout"] else _json_result("".join(buffers["stdout"]))
        state.update({"exitCode": return_code, "finishedAt": _now(), "stdoutTruncated": truncated["stdout"], "stderrTruncated": truncated["stderr"]})
        if entry["stopReason"]:
            state.update({"status": "unknown", "error": entry["stopReason"], "note": "进程已停止，未确认完整结果；不会自动重新提交。"})
        elif return_code != 0:
            state.update({"status": "failed", "error": "CLI 进程返回非零退出码；请查看标准错误和已有产物。"})
        elif result is None:
            state.update({"status": "unknown", "error": "进程已退出，但未收到可确认的 CLI JSON 结果；不会自动重新提交。"})
        elif result.get("isError") is True or result.get("error") or result.get("status") in {"failed", "error", "cancelled"} or (isinstance(result.get("projection"), dict) and result["projection"].get("status") in {"failed", "error", "cancelled"}):
            state.update({"status": "failed", "error": "CLI JSON 结果报告失败或取消；请检查已有产物。"})
        else:
            state["status"] = "completed"
        if result:
            state["sessionId"] = result.get("sessionId")
            state["response"] = sanitize_text(result.get("response", ""), 64000)
        self._save(entry)
        entry["done"].set()
        self._callback(on_done, state["runId"], dict(state))

    def get_run(self, run_id):
        with self._lock:
            if run_id not in self._runs:
                raise RunnerError("运行编号不存在。")
            return dict(self._runs[run_id]["state"])

    def get_process(self, run_id):
        with self._lock:
            return self._runs[run_id]["process"]

    def wait(self, run_id, timeout=None):
        with self._lock:
            entry = self._runs[run_id]
        if not entry["done"].wait(timeout):
            return None
        return self.get_run(run_id)

    def stop(self, run_id):
        with self._lock:
            entry = self._runs[run_id]
            process = entry["process"]
            if process is None or process.poll() is not None:
                return False
            entry["stopReason"] = "stopped_result_uncertain"
            process.terminate()
            return True
