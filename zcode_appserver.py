"""Background ZCode app-server client; no desktop UI or database writes.

The NDJSON/session lifecycle follows the user's collaboration-plaza
plaza/zcode.py at commit 449ee2d. This module has no Plaza runtime dependency.
"""
from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time
import uuid

from workspace import sanitize_text
from zcode_runner import resolve_command, command_environment


class AppServerError(RuntimeError):
    pass


class AppServerBlocked(AppServerError):
    def __init__(self, method):
        self.method = method
        super().__init__("ZCode 需要额外交互，未自动批准或作答：" + method)


class AppServerTimeout(AppServerError):
    def __init__(self, method, seconds):
        self.method, self.seconds = method, seconds
        super().__init__("ZCode 请求超时，未重新提交：" + method)


def _message_id(message):
    return message.get("id") or (message.get("info") or {}).get("id") or (message.get("message") or {}).get("id")


def _text(message):
    info = message.get("info") or {}
    if any(item.get("hidden") or item.get("synthetic") or item.get("visibility") == "model-only"
           for item in (message, info)):
        return ""
    parts = message.get("parts")
    if isinstance(parts, list) and parts:
        return "\n".join(part.get("text", part.get("content", "")) for part in parts
                         if part.get("type") in {"text", "input_text", "output_text"}
                         and not part.get("hidden") and not part.get("synthetic")
                         and not part.get("ignored") and part.get("visibility") != "model-only"
                         and isinstance(part.get("text", part.get("content", "")), str))
    return message.get("content") if isinstance(message.get("content"), str) else ""


def _role(message):
    return message.get("role") or (message.get("info") or {}).get("role") or (message.get("message") or {}).get("role")


def _snapshot(value, session_id=None, *, final=False):
    if not isinstance(value, dict) or not isinstance(value.get("session"), dict):
        raise AppServerError("ZCode 原生快照缺少 session 对象。")
    native_id = value["session"].get("sessionId")
    if not isinstance(native_id, str) or not native_id.strip() or session_id is not None and native_id != session_id:
        raise AppServerError("ZCode 快照会话归属不一致或缺少有效 sessionId。")
    for key in ("settings", "projection", "runtime"):
        if key in value and not isinstance(value[key], dict):
            raise AppServerError("ZCode 快照结构无效：" + key)
    for key in ("permission", "mode"):
        if key in value.get("settings", {}) and not isinstance(value["settings"][key], dict):
            raise AppServerError("ZCode 快照设置结构无效：" + key)
    workspace = value["session"].get("workspace")
    if workspace is not None and not isinstance(workspace, dict):
        raise AppServerError("ZCode 快照工作区结构无效。")
    messages = value.get("messages", [])
    if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
        raise AppServerError("ZCode 快照消息结构无效。")
    for message in messages:
        for key in ("info", "message"):
            if key in message and not isinstance(message[key], dict):
                raise AppServerError("ZCode 消息元数据结构无效。")
        if "parts" in message and (not isinstance(message["parts"], list)
                                   or any(not isinstance(part, dict) for part in message["parts"])):
            raise AppServerError("ZCode 消息片段结构无效。")
    if final and "messages" not in value:
        raise AppServerError("ZCode 完成快照缺少消息，未确认完整结果。")
    return value


class ZCodeAppServer:
    READ_POLL_INTERVAL = 2.0
    READ_POLL_TIMEOUT = 5.0
    FINAL_READ_BUDGET = 90.0
    FINAL_READ_FIRST_TIMEOUT = 30.0
    MAX_PROTOCOL_LINE = 16 * 1024 * 1024

    def __init__(self, cwd, commandPath=None, timeout_seconds=900, mode="yolo"):
        self.cwd = Path(cwd).expanduser().resolve()
        if not self.cwd.is_dir() or self.cwd.parent == self.cwd:
            raise AppServerError("需要明确且存在的采集工作区。")
        if mode not in {"edit", "plan", "build", "guarded", "yolo", "auto"}:
            raise AppServerError("ZCode 执行权限模式无效。")
        self.timeout_seconds = float(timeout_seconds)
        if self.timeout_seconds <= 0:
            raise AppServerError("任务超时必须大于零。")
        self.mode = mode
        self.command = resolve_command(commandPath)
        self._pending = {}
        self._pending_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._events = queue.Queue(maxsize=2048)
        self._stderr = deque(maxlen=40)
        self._closed = False
        self._failure = None
        self._bound = None
        self._uncertain = False
        self._used_inputs = set()
        self.last_binding = None
        options = {"cwd": str(self.cwd), "stdin": subprocess.PIPE, "stdout": subprocess.PIPE,
                   "stderr": subprocess.PIPE, "text": True, "encoding": "utf-8", "errors": "replace",
                   "bufsize": 1, "shell": False, "env": command_environment(self.command),
                   "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
        if os.name != "nt":
            options["start_new_session"] = True
        try:
            self.proc = subprocess.Popen(self.command + ["app-server", "--stdio", "--cwd", str(self.cwd)], **options)
        except OSError as error:
            raise AppServerError("无法启动 ZCode app-server：" + type(error).__name__) from error
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._error_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader.start()
        self._error_reader.start()

    @property
    def stderr_tail(self):
        return list(self._stderr)

    def _write(self, value):
        with self._write_lock:
            if self._closed or self._failure:
                raise AppServerError(self._failure or "ZCode app-server 已关闭。")
            try:
                self.proc.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            except (OSError, ValueError) as error:
                raise AppServerError("ZCode 协议写入失败，未重发。") from error

    def _fail(self, message):
        with self._pending_lock:
            if not self._failure:
                self._failure = message
            for waiter in self._pending.values():
                waiter.put({"_transportFailure": self._failure})
        try:
            self._events.put_nowait({"_transportFailure": self._failure})
        except queue.Full:
            pass

    def _enqueue(self, value):
        try:
            self._events.put_nowait(value)
        except queue.Full:
            raise AppServerError("ZCode 事件积压超限，未确认任务结果。") from None

    def _read(self):
        failure = "ZCode app-server 连接已结束，未确认任务完成。"
        try:
            while True:
                line = self.proc.stdout.readline(self.MAX_PROTOCOL_LINE + 1)
                if not line:
                    break
                if len(line) > self.MAX_PROTOCOL_LINE:
                    raise AppServerError("ZCode 协议消息超限。")
                if not line.strip():
                    continue
                try:
                    value = json.loads(line.lstrip("\ufeff"))
                except ValueError:
                    raise AppServerError("ZCode stdout 出现非 NDJSON 协议数据。") from None
                if not isinstance(value, dict):
                    raise AppServerError("ZCode 协议消息必须是对象。")
                if "id" in value and "method" not in value:
                    with self._pending_lock:
                        waiter = self._pending.get(value["id"])
                    if waiter:
                        waiter.put(value)
                elif "id" in value and "method" in value:
                    method = value.get("method")
                    if method == "session/requestRuntimePreferences":
                        # Per-runtime preferences only; never issue a global
                        # workspace/update* or write the user's memory settings.
                        self._write({"id": value["id"], "result": {
                            "nativeSearchEnhancementsEnabled": False, "memoryEnabled": False,
                            "askUserQuestionAutoResolutionEnabled": False}})
                    else:
                        self._write({"id": value["id"], "error": {
                            "code": -32001, "message": "额外交互未配置；未自动批准或作答。"}})
                        params = value.get("params")
                        self._enqueue({"_blocked": str(method), "sessionId": params.get("sessionId") if isinstance(params, dict) else None})
                elif value.get("method") in {"session/event", "state.updated", "v4/telemetry/event"}:
                    self._enqueue(value)
                # Monitoring arrays and unknown notifications are not session
                # envelopes and cannot establish task completion.
        except Exception as error:
            failure = str(error) if isinstance(error, AppServerError) else "ZCode 协议读取失败。"
        finally:
            self._fail(failure)

    def _read_stderr(self):
        try:
            while True:
                line = self.proc.stderr.readline(4097)
                if not line:
                    break
                self._stderr.append(sanitize_text(line.rstrip(), 2000))
        except (OSError, ValueError):
            pass

    def request(self, method, params, timeout=90):
        if not isinstance(method, str) or not method or not isinstance(params, dict):
            raise AppServerError("RPC 方法或参数格式无效。")
        identifier = str(uuid.uuid4())
        waiter = queue.Queue()
        with self._pending_lock:
            if self._failure or self._closed:
                raise AppServerError(self._failure or "ZCode app-server 已关闭。")
            self._pending[identifier] = waiter
        try:
            self._write({"id": identifier, "method": method, "params": params})
            try:
                value = waiter.get(timeout=timeout)
            except queue.Empty:
                raise AppServerTimeout(method, timeout) from None
            if value.get("_transportFailure"):
                raise AppServerError(value["_transportFailure"])
            if "error" in value:
                # Do not expose provider text, raw parameters, or credentials.
                code = value["error"].get("code") if isinstance(value["error"], dict) else None
                raise AppServerError("ZCode RPC 返回错误：" + method + " (" + str(code) + ")")
            if "result" not in value:
                raise AppServerError("ZCode RPC 缺少 result：" + method)
            return value["result"]
        finally:
            with self._pending_lock:
                self._pending.pop(identifier, None)

    def _workspace_matches(self, snapshot):
        workspace = snapshot["session"].get("workspace")
        if not isinstance(workspace, dict) or not workspace:
            raise AppServerError("原生会话未提供可核对的工作区。")
        if (not isinstance(workspace.get("workspacePath"), str)
                          or os.path.normcase(os.path.normpath(workspace["workspacePath"])) != os.path.normcase(str(self.cwd))
                          or workspace.get("workspaceIdentity")):
            raise AppServerError("原生会话属于其他工作区，拒绝恢复或发送。")

    def bind(self, session_id=None):
        if not self._operation_lock.acquire(blocking=False):
            raise AppServerError("当前 app-server 正在执行任务。")
        try:
            if self._uncertain:
                raise AppServerError("原轮次结果不明，请先检查原会话；不能重新绑定后重发。")
            workspace = {"workspacePath": str(self.cwd), "workspaceKey": str(self.cwd)}
            if session_id is None:
                snapshot = _snapshot(self.request("session/create", {"workspace": workspace,
                    "mode": self.mode, "persistence": "immediate", "titleGenerationEnabled": False}))
            else:
                if not isinstance(session_id, str) or not session_id.strip():
                    raise AppServerError("恢复必须提供有效 sessionId。")
                listing = self.request("session/list", {"sessionIds": [session_id]})
                if not isinstance(listing, dict) or not isinstance(listing.get("sessions"), list):
                    raise AppServerError("ZCode 会话查询结构无效，未新建替代会话。")
                saved = next((item for item in listing["sessions"] if isinstance(item, dict) and item.get("sessionId") == session_id), None)
                if saved is None or not isinstance(saved.get("workspace"), dict):
                    raise AppServerError("原会话不存在或工作区未知，未新建替代会话。")
                self._workspace_matches({"session": saved})
                snapshot = _snapshot(self.request("session/resume", {"sessionId": session_id,
                    "workspace": saved["workspace"]}), session_id)
            self._workspace_matches(snapshot)
            native_id = snapshot["session"]["sessionId"]
            # A legacy mode can equal yolo while the independent plan flag is
            # still enabled. Apply the native selection once on every bind.
            # Its workspace preference effect is authorized only for this
            # selected tool workspace, and is explicitly recorded below.
            self.request("session/setMode", {"sessionId": native_id, "mode": self.mode})
            snapshot = _snapshot(self.request("session/read", {"sessionId": native_id}), native_id)
            self._workspace_matches(snapshot)
            # In installed 0.17 cold drafts, session/projection.mode may remain
            # "build" while the native permission settings already report yolo.
            # Those presentation fields are not additional permission votes.
            # Require the available authoritative settings to agree; only an
            # older snapshot with no settings object can use session.mode.
            if "settings" in snapshot:
                settings = snapshot["settings"]
                modes = [settings.get("permission", {}).get("mode"),
                         settings.get("mode", {}).get("current")]
                modes = [value for value in modes if value is not None]
            else:
                modes = [snapshot["session"].get("mode")]
            if self.mode != "plan" and (not modes or any(value != self.mode for value in modes)):
                raise AppServerError("原生会话未确认所选权限模式，未发送任务。")
            self._bound = native_id
            self.last_binding = {"sessionId": self._bound, "mode": self.mode,
                                 "permissionScope": "selected-workspace-mode-preference",
                                 "planResetByNativeModeSelection": self.mode != "plan"}
            return snapshot
        finally:
            self._operation_lock.release()

    @staticmethod
    def _notify(callback, value):
        if callback:
            try:
                callback(value)
            except Exception:
                pass

    @staticmethod
    def _raise_if_negative(event, session_id, input_id, active_turn):
        if event.get("_blocked") and event.get("sessionId") in (None, session_id):
            raise AppServerBlocked(event["_blocked"])
        params = event.get("params")
        if not isinstance(params, dict) or params.get("sessionId") != session_id:
            return
        payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
        identifiers = [params.get("inputId"), params.get("sourceCommandId"), payload.get("inputId")]
        if any(value is not None and value != input_id for value in identifiers):
            return
        turn = params.get("turnId") or payload.get("turnId")
        if active_turn is not None and turn not in (None, active_turn):
            return
        if input_id not in identifiers and not (active_turn and turn in (None, active_turn)):
            return
        method = event.get("method")
        if (method == "session/event" and (params.get("type") == "turn.failed"
                or params.get("type") == "turn.completed" and payload.get("resultType") != "success")
                or method == "state.updated" and params.get("reason") == "prompt_failed"
                or method == "v4/telemetry/event" and params.get("kind") == "turn.terminal" and params.get("resultType") != "success"):
            raise AppServerError("ZCode 已确认本轮取消、失败或未知终态；未重新提交。")

    def _drain_final_errors(self, session_id, input_id, active_turn):
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return
            self._raise_if_negative(event, session_id, input_id, active_turn)

    def _final_snapshot(self, session_id, content, active_turn, baseline):
        deadline = time.monotonic() + self.FINAL_READ_BUDGET
        read_timeouts, pause = 0, .1
        while time.monotonic() < deadline:
            budget = max(.01, deadline - time.monotonic())
            if read_timeouts == 0:
                budget = min(budget, self.FINAL_READ_FIRST_TIMEOUT)
            try:
                snapshot = _snapshot(self.request("session/read", {"sessionId": session_id}, timeout=budget), session_id, final=True)
            except AppServerTimeout:
                read_timeouts += 1
                if read_timeouts >= 2 or time.monotonic() >= deadline:
                    raise
                continue
            self._workspace_matches(snapshot)
            runtime, projection = snapshot.get("runtime", {}), snapshot.get("projection", {})
            if projection.get("status") == "error":
                raise AppServerError("原生完成快照报告执行失败。")
            if active_turn and projection.get("currentTurnId") not in (None, active_turn):
                raise AppServerError("完成快照属于其他轮次。")
            messages = snapshot["messages"]
            old_ids = {_message_id(message) for message in baseline if _message_id(message)}
            indexes = [index for index, message in enumerate(messages)
                       if _role(message) == "user" and _text(message).strip() == content.strip()
                       and ((_message_id(message) not in old_ids) if old_ids and _message_id(message)
                            else index >= len(baseline))]
            ready = (not runtime.get("activeTurnId") and projection.get("status") not in {"running", "waiting"}
                     and indexes and any(_role(message) == "assistant" and _text(message).strip()
                                         for message in messages[indexes[-1] + 1:]))
            if ready:
                return snapshot
            time.sleep(min(pause, max(0, deadline - time.monotonic())))
            pause = min(1.0, pause * 2)
        raise AppServerTimeout("session/read final snapshot", self.FINAL_READ_BUDGET)

    def prompt(self, session_id, content, input_id=None, on_event=None):
        if session_id != self._bound or not isinstance(content, str) or not content.strip():
            raise AppServerError("请先绑定同一有效会话，并提供非空任务。")
        input_id = input_id or str(uuid.uuid4())
        if not isinstance(input_id, str) or not input_id.strip() or len(input_id) > 240:
            raise AppServerError("本轮 inputId 格式无效。")
        if not self._operation_lock.acquire(blocking=False):
            raise AppServerError("同一 app-server 不能并发提交另一轮任务。")
        try:
            if self._uncertain:
                raise AppServerError("原轮次结果不明，请先检查原会话；不会提交另一轮。")
            if (session_id, input_id) in self._used_inputs:
                raise AppServerError("该 inputId 已提交过，不会自动重发。")
            if len(self._used_inputs) >= 4096:
                raise AppServerError("当前连接提交记录已满，请保留会话后重建连接。")
            while True:
                try:
                    self._events.get_nowait()
                except queue.Empty:
                    break
            self.request("session/subscribe", {"sessionId": session_id,
                         "deliveryKind": "desktop-continuous", "includeSnapshot": False}, timeout=10)
            baseline = _snapshot(self.request("session/read", {"sessionId": session_id}), session_id)
            self._workspace_matches(baseline)
            if baseline.get("runtime", {}).get("activeTurnId") or baseline.get("projection", {}).get("status") in {"running", "waiting"}:
                raise AppServerError("原会话已有正在执行的轮次，未追加另一轮。")
            self._used_inputs.add((session_id, input_id))
            self._uncertain = True
            self.request("session/send", {"sessionId": session_id, "content": content, "inputId": input_id})
            deadline = time.monotonic() + self.timeout_seconds
            next_read = time.monotonic() + self.READ_POLL_INTERVAL
            active_turn, emitted, text_budget, terminal_at = None, 0, 16000, None

            def publish(kind, **facts):
                nonlocal emitted
                if emitted < 256:
                    self._notify(on_event, {"type": kind, "sessionId": session_id, "inputId": input_id,
                                          "turnId": active_turn, **facts})
                    emitted += 1

            publish("request_admitted")
            while time.monotonic() < deadline:
                if self._failure:
                    raise AppServerError(self._failure)
                try:
                    event = self._events.get(timeout=min(.1, max(.01, deadline - time.monotonic())))
                except queue.Empty:
                    if terminal_at is not None and time.monotonic() - terminal_at >= .2:
                        publish("final_snapshot_read")
                        snapshot = self._final_snapshot(session_id, content, active_turn, baseline.get("messages", []))
                        self._drain_final_errors(session_id, input_id, active_turn)
                        self._uncertain = False
                        publish("completed")
                        return snapshot
                    if time.monotonic() >= next_read:
                        try:
                            current = _snapshot(self.request("session/read", {"sessionId": session_id},
                                timeout=min(self.READ_POLL_TIMEOUT, max(.01, deadline - time.monotonic()))), session_id)
                            publish("snapshot_observed", status=current.get("projection", {}).get("status"))
                        except AppServerTimeout:
                            publish("snapshot_read_timeout")
                        next_read = time.monotonic() + self.READ_POLL_INTERVAL
                    continue
                if event.get("_transportFailure"):
                    raise AppServerError(event["_transportFailure"])
                if event.get("_blocked"):
                    if event.get("sessionId") in (None, session_id):
                        raise AppServerBlocked(event["_blocked"])
                    continue
                self._raise_if_negative(event, session_id, input_id, active_turn)
                params = event.get("params")
                if not isinstance(params, dict) or params.get("sessionId") != session_id:
                    continue
                payload = params.get("payload")
                payload = payload if isinstance(payload, dict) else {}
                identities = [params.get("inputId"), params.get("sourceCommandId"), payload.get("inputId")]
                if any(value is not None and value != input_id for value in identities):
                    continue
                explicit_input = input_id in identities
                turn = params.get("turnId") or payload.get("turnId")
                if turn is not None and (not isinstance(turn, str) or active_turn is not None and turn != active_turn):
                    continue
                kind = params.get("type") if event.get("method") == "session/event" else None
                if kind == "turn.started" and explicit_input and turn:
                    active_turn = turn
                    publish("turn_started")
                attributed = explicit_input or active_turn is not None and turn in (None, active_turn)
                if not attributed:
                    continue
                if kind == "turn.failed" or event.get("method") == "state.updated" and params.get("reason") == "prompt_failed":
                    raise AppServerError("ZCode 已确认本轮执行失败；会话已保留。")
                completed = kind == "turn.completed"
                if completed and payload.get("resultType") != "success":
                    raise AppServerError("ZCode 本轮终态为取消、失败或未知结果；未重发。")
                if completed:
                    terminal_at = time.monotonic()
                if event.get("method") == "state.updated" and params.get("reason") == "prompt_completed":
                    publish("final_snapshot_read")
                    snapshot = self._final_snapshot(session_id, content, active_turn, baseline.get("messages", []))
                    self._drain_final_errors(session_id, input_id, active_turn)
                    self._uncertain = False
                    publish("completed")
                    return snapshot
                if kind == "model.streaming" and payload.get("kind") == "text_delta" and isinstance(payload.get("delta"), str):
                    visibility_sources = [params, payload]
                    for key in ("metadata", "_meta"):
                        if isinstance(payload.get(key), dict):
                            visibility_sources.append(payload[key])
                            if isinstance(payload[key].get("zcode"), dict):
                                visibility_sources.append(payload[key]["zcode"])
                    if not any(item.get("hidden") or item.get("synthetic") or item.get("ignored") or item.get("visibility") == "model-only"
                               for item in visibility_sources) and text_budget:
                        text = sanitize_text(payload["delta"], min(1000, text_budget))
                        text_budget -= len(text)
                        publish("public_text_delta", text=text)
                elif kind in {"tool.started", "tool.completed", "tool.failed", "model.request.started"}:
                    publish(kind, toolName=sanitize_text(payload.get("toolName", ""), 100))
                elif event.get("method") == "v4/telemetry/event" and params.get("kind") in {"tool.lifecycle", "model.request.status"}:
                    publish(params["kind"], status=sanitize_text(params.get("status", params.get("phase", "")), 100),
                            toolName=sanitize_text(params.get("toolName", ""), 100))
            raise AppServerTimeout("session/send completion", self.timeout_seconds)
        finally:
            self._operation_lock.release()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._fail("ZCode app-server 已关闭；未重新提交任务。")
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
            else:
                try:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)
        self._reader.join(timeout=1)
        self._error_reader.join(timeout=1)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
