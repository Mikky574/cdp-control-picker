"""Authenticated loopback client for the installed ZCode extension bridge.

No CLI startup, model retry, private-index writes, or automatic authorization.
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import re
import time
from urllib import error, request
from urllib.parse import urlsplit


class DesktopBridgeError(ValueError):
    def __init__(self, message, *, code="bridge_error", candidates=None, definite_rejection=False):
        super().__init__(message)
        self.code = code
        self.candidates = list(candidates or [])
        self.definite_rejection = definite_rejection


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise DesktopBridgeError("桌面连接拒绝重定向。", code="redirect_refused")


def normalize_model_selection(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"providerId", "modelId"}:
        raise DesktopBridgeError("请选择目录中的供应商与模型；原生桌面通道不接受凭据或额外模型参数。", code="invalid_model")
    result = {}
    for key in ("providerId", "modelId"):
        item = value.get(key)
        if not isinstance(item, str) or not item.strip() or len(item) > 512 or any(c in item for c in "\r\n\0"):
            raise DesktopBridgeError("模型标识无效。", code="invalid_model")
        result[key] = item.strip()
    return result


def _endpoint(value):
    if not isinstance(value, str):
        raise DesktopBridgeError("桌面连接文件缺少本机地址。", code="invalid_discovery")
    parsed = urlsplit(value)
    try:
        local = ipaddress.ip_address(parsed.hostname).is_loopback
        port = parsed.port
    except (ValueError, TypeError):
        local, port = False, None
    if (parsed.scheme != "http" or not local or not port or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise DesktopBridgeError("桌面连接只允许数值loopback HTTP端点。", code="invalid_discovery")
    return value.rstrip("/")


class DesktopBridge:
    MAX_RESPONSE = 8 * 1024 * 1024
    METHODS = {"health", "models", "projects", "authorize", "create", "send", "open", "read", "wait", "receipt"}

    def __init__(self, workspace, *, discovery_path=None, timeout_seconds=15):
        self.workspace = Path(workspace).expanduser().resolve()
        if not self.workspace.is_dir() or self.workspace.parent == self.workspace:
            raise DesktopBridgeError("需要已存在的资料工作区。", code="invalid_workspace")
        self.timeout = max(.05, float(timeout_seconds))
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        self.discovery_path = Path(discovery_path).expanduser().resolve() if discovery_path else base / "CDP-Control-Picker" / "desktop-bridges"
        self._opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
        self._selected = None

    @staticmethod
    def _public(item):
        return {key: item[key] for key in ("instanceId", "pid", "pluginId", "pluginVersion", "endpoint", "workspacePath", "discoveryPath") if key in item}

    def _candidates(self):
        paths = [self.discovery_path] if self.discovery_path.is_file() else sorted(self.discovery_path.glob("*.json"))
        candidates = []
        for path in paths[:64]:
            try:
                if path.stat().st_size > 65536:
                    continue
                item = json.loads(path.read_text(encoding="utf-8-sig"))
                if (not isinstance(item, dict) or type(item.get("schemaVersion")) is not int or item["schemaVersion"] != 1
                        or type(item.get("pid")) is not int or item['pid'] <= 0
                        or any(not isinstance(item.get(key), str) or not item[key].strip() for key in
                               ("instanceId", "pluginId", "pluginVersion", "token", "workspacePath"))
                        or len(item["token"]) > 4096 or any(c in item["token"] for c in "\r\n\0")):
                    continue
                item = {**item, "endpoint": _endpoint(item.get("endpoint")), "discoveryPath": str(path.resolve())}
                candidates.append(item)
            except (OSError, ValueError, TypeError):
                continue
        if not candidates:
            raise DesktopBridgeError("未发现ZCode桌面连接；请在当前ZCode启用采集器连接扩展，再连接。", code="not_connected")
        return candidates

    def _call(self, item, method, params, request_id=None, *, timeout=None):
        payload = {"method": method, "params": params}
        if request_id is not None:
            payload["requestId"] = request_id
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            raise DesktopBridgeError("桌面请求不是有效JSON参数。", code="invalid_input", definite_rejection=True) from None
        message = request.Request(item["endpoint"] + "/rpc", data=body,
                                  headers={"Authorization": "Bearer " + item["token"], "Content-Type": "application/json"}, method="POST")
        try:
            with self._opener.open(message, timeout=timeout or self.timeout) as response:
                data = response.read(self.MAX_RESPONSE + 1)
            if len(data) > self.MAX_RESPONSE:
                raise DesktopBridgeError("桌面响应超出本次读取上限。", code="response_limit")
            envelope = json.loads(data.decode("utf-8-sig"))
        except DesktopBridgeError:
            raise
        except error.HTTPError as failure:
            rejected = failure.code in {400, 401, 403, 404, 405, 413}
            failure.close()
            raise DesktopBridgeError("桌面连接HTTP请求未确认（" + str(failure.code) + "）。", code="http_error", definite_rejection=rejected) from None
        except (error.URLError, OSError, TimeoutError, ValueError, UnicodeError):
            raise DesktopBridgeError("桌面连接未返回可确认的结果；不会自动重发。", code="transport_unknown") from None
        if not isinstance(envelope, dict) or type(envelope.get("ok")) is not bool:
            raise DesktopBridgeError("桌面响应格式不兼容。", code="invalid_response")
        if envelope["ok"] is not True:
            failure = envelope.get("error")
            code = failure.get("code") if isinstance(failure, dict) else None
            code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,100}", code) else "native_error"
            rejected = code in {"not-granted", "not-declared", "forbidden", "invalid_input", "invalid-input", "invalid-params", "incompatible", "permission-denied"}
            messages = {"not-granted": "请点击连接ZCode桌面并在原生授权页允许会话访问。",
                        "not-declared": "桌面扩展尚未声明所需会话能力。",
                        "incompatible": "当前ZCode尚不支持连接扩展所需的原生接口。",
                        "forbidden": "当前桌面会话访问未获授权。"}
            raise DesktopBridgeError(messages.get(code, "ZCode桌面未完成操作（" + code + "）。"), code=code, definite_rejection=rejected)
        if "result" not in envelope:
            raise DesktopBridgeError("桌面响应缺少结果。", code="invalid_response")
        return envelope["result"]

    @staticmethod
    def _valid_health(item, result):
        return (isinstance(result, dict) and result.get("schemaVersion") == 1
                and result.get("instanceId") == item["instanceId"]
                and result.get("pluginId") == item["pluginId"]
                and result.get("workspacePath") == item["workspacePath"]
                and result.get("transport") == "zcode-extension-host"
                and type(result.get("nativeSessions")) is bool)

    def health(self):
        active = []
        deadline = time.monotonic() + self.timeout
        for item in self._candidates():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                result = self._call(item, "health", {}, timeout=min(2, remaining))
                if self._valid_health(item, result):
                    active.append((item, result))
            except DesktopBridgeError:
                continue
        if len(active) != 1:
            self._selected = None
            if len(active) > 1:
                raise DesktopBridgeError("发现多个活动ZCode窗口实例，请在设置中明确选择连接文件。", code="ambiguous_instances", candidates=[self._public(item) for item, _ in active])
            raise DesktopBridgeError("ZCode桌面连接当前不可用；请检查扩展是否正在当前应用中运行。", code="not_connected")
        self._selected, result = active[0]
        return {**result, "discoveryPath": self._selected["discoveryPath"]}

    def request(self, method, params=None, *, request_id=None):
        if method not in self.METHODS or (params is not None and not isinstance(params, dict)):
            raise DesktopBridgeError("桌面方法或参数无效。", code="invalid_input", definite_rejection=True)
        params = dict(params or {})
        if method in {"create", "send"}:
            if not isinstance(request_id, str) or not request_id.strip() or params.get("requestId", request_id) != request_id:
                raise DesktopBridgeError("发送必须使用同一个稳定请求编号。", code="invalid_input", definite_rejection=True)
            params["requestId"] = request_id
        if self._selected is None:
            self.health()
        return self._call(self._selected, method, params, request_id)

    def authorize_connection(self):
        return self.request("authorize")

    def list_models(self):
        rows = self.request("models")
        if not isinstance(rows, list):
            raise DesktopBridgeError("桌面模型目录格式不兼容。", code="invalid_response")
        result = []
        for row in rows:
            if not isinstance(row, dict) or any(not isinstance(row.get(key), str) or not row[key].strip() for key in ("id", "providerId", "modelId")):
                raise DesktopBridgeError("桌面模型目录缺少模型身份。", code="invalid_response")
            result.append({key: row[key] for key in ("id", "providerId", "modelId", "label", "providerName") if isinstance(row.get(key), str)})
        return result

    def model_reference(self, selection):
        selected = normalize_model_selection(selection)
        if selected is None:
            return None
        matches = [item for item in self.list_models() if all(item.get(key) == value for key, value in selected.items())]
        if len(matches) != 1:
            raise DesktopBridgeError("所选模型不在当前ZCode唯一可用目录中，请刷新目录。", code="invalid_model", definite_rejection=True)
        return matches[0]["id"]

    def list_projects(self, params=None):
        return self.request("projects", params)

    def create_session(self, prompt, *, request_id, model=None, mode="yolo", project=None):
        params = {"prompt": prompt, "mode": mode}
        if model is not None:
            params["model"] = model
        if project is not None:
            params["project"] = project
        return self.request("create", params, request_id=request_id)

    def open_session(self, session_id):
        return self.request("open", {"sessionId": session_id})

    def read_session(self, session_id, *, view="messages", limit=100):
        return self.request("read", {"sessionId": session_id, "view": view, "limit": limit})

    def wait_operation(self, operation, *, timeout_ms=10000):
        return self.request("wait", {"operations": [operation], "mode": "all", "timeoutMs": timeout_ms})

    def receipt(self, request_id):
        return self.request("receipt", {"requestId": request_id})
