"""Portable, credential-free capture storage for the CDP control picker."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit, urlunsplit
import uuid


class WorkspaceError(ValueError):
    pass


_CAPTURE_ID = re.compile(r"cap-[A-Za-z0-9][A-Za-z0-9_-]{0,75}\Z")
_SECRET_KEY = re.compile(r"password|passwd|passphrase|private.?key|api.?key|access.?token|refresh.?token|authorization|cookie|localstorage|sessionstorage|secret", re.I)
_PRIVATE_KEY = re.compile(r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?(?:-----END [^-\r\n]*PRIVATE KEY-----|\Z)", re.S)
_PAGE_KEYS = {"url", "origin", "path", "title", "targetId", "frameId", "frameChain"}
_CONTROL_KEYS = {"tagName", "id", "type", "attributes", "text", "role", "domNameHint", "labelHint", "name", "accessibleName", "selectors", "locatorCandidates", "ancestors", "bounds", "rect", "frameChain"}
_ANNOTATION_KEYS = {"name", "description", "allowedActions", "expectedResult", "preconditions", "postconditions", "notes"}
_ATTRIBUTE_KEYS = {"id", "class", "type", "name", "role", "title", "placeholder", "for", "href", "disabled", "readonly", "checked", "required", "aria-label", "aria-labelledby", "aria-describedby", "aria-expanded", "aria-checked", "aria-selected", "aria-controls", "data-testid", "data-test", "data-action", "data-field", "data-order-id", "data-id"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sanitize_text(value, limit=12000):
    """Remove obvious credential literals; do not use this as a secret detector."""
    text = str(value)
    text = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", text)
    text = re.sub(r"(?i)\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED KEY]", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)((?:password|passwd|passphrase|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization|secret|密码|口令)\s*[:=：]\s*)([^\s,;，；\r\n]+)", r"\1[REDACTED]", text)
    return text[:limit]


def safe_url(value):
    """Capture page identity without userinfo, query parameters or fragments."""
    try:
        parsed = urlsplit(str(value))
        if parsed.scheme not in {"http", "https", "file", "about"}:
            return ""
        if parsed.scheme in {"http", "https"}:
            host = parsed.hostname or ""
            if ":" in host:
                host = "[" + host + "]"
            netloc = host + (":" + str(parsed.port) if parsed.port else "")
        else:
            netloc = parsed.netloc
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))[:2000]
    except (ValueError, TypeError):
        return ""


def _clean(value, depth=0):
    if depth > 7:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return sanitize_text(value, 2000)
    if isinstance(value, list):
        return [_clean(item, depth + 1) for item in value[:40]]
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:60]:
            if not isinstance(key, str) or _SECRET_KEY.search(key) or key.lower() in {"value", "inputvalue", "currentvalue", "defaultvalue", "innerhtml", "outerhtml", "html", "domsnippet", "elementtoken", "websocketdebuggerurl"} or key.lower().startswith("on"):
                continue
            result[key] = safe_url(item) if key.lower() in {"url", "href", "src"} else _clean(item, depth + 1)
        return result
    return None


def _locators(items):
    result = []
    if not isinstance(items, list):
        return result
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        locator = {}
        for key in ("strategy", "type", "value", "selector", "role", "name", "matchCount", "unique", "visible", "source"):
            value = item.get(key)
            if isinstance(value, (str, int, float, bool)):
                if key in {"value", "selector"}:
                    if not isinstance(value, str) or re.search(r"\[\s*value\s*[*^$|~]?=", value, re.I) or re.search(r"sk-[A-Za-z0-9_-]{8,}|Bearer\s+", value, re.I):
                        continue
                locator[key] = sanitize_text(value, 1200) if isinstance(value, str) else value
        if locator.get("value") or locator.get("selector") or locator.get("role"):
            result.append(locator)
    return result


def sanitize_capture(card, description=None):
    if not isinstance(card, dict) or not isinstance(card.get("page"), dict) or not isinstance(card.get("control"), dict):
        raise WorkspaceError("采集资料需要 page 和 control 对象。")
    page = {key: _clean(value) for key, value in card["page"].items() if key in _PAGE_KEYS}
    page["url"] = safe_url(card["page"].get("url", ""))
    if page["url"]:
        parsed = urlsplit(page["url"])
        page["origin"] = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        page["path"] = parsed.path
    control = {key: _clean(value) for key, value in card["control"].items() if key in _CONTROL_KEYS and key not in {"selectors", "locatorCandidates", "attributes"}}
    raw_role = card["control"].get("role")
    if isinstance(raw_role, dict) and isinstance(raw_role.get("value"), str):
        control["role"] = {"value": sanitize_text(raw_role["value"], 80), "source": sanitize_text(raw_role.get("source", ""), 80)}
    attributes = card["control"].get("attributes")
    control["attributes"] = {key: safe_url(value) if key == "href" else _clean(value) for key, value in (attributes.items() if isinstance(attributes, dict) else []) if key in _ATTRIBUTE_KEYS and not _SECRET_KEY.search(key)}
    for key in ("selectors", "locatorCandidates"):
        if key in card["control"]:
            control[key] = _locators(card["control"][key])
    raw_annotation = card.get("annotation") or {}
    if isinstance(raw_annotation, str):
        raw_annotation = {"description": raw_annotation}
    if not isinstance(raw_annotation, dict):
        raise WorkspaceError("功能说明需要 annotation 对象。")
    annotation = {key: _clean(value) for key, value in raw_annotation.items() if key in _ANNOTATION_KEYS}
    if description is not None:
        annotation["description"] = sanitize_text(description)
    if not isinstance(annotation.get("description"), str) or not annotation["description"].strip():
        raise WorkspaceError("请填写这个控件的功能说明。")
    annotation["allowedActions"] = [action for action in raw_annotation.get("allowedActions", []) if isinstance(action, str) and action in {"read", "click", "fill", "select", "check"}][:5] if isinstance(raw_annotation.get("allowedActions", []), list) else []
    validation = _clean(card.get("validation") or {})
    if not isinstance(validation, dict):
        validation = {}
    validation["status"] = "draft"
    validation["note"] = "已保存控件采集资料；尚未验证生成能力或执行业务操作。"
    return {"schemaVersion": 1, "page": page, "control": control, "annotation": annotation, "validation": validation}


def atomic_write(path, text):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Workspace:
    def __init__(self, rootPath, skill_path=None):
        self.root = Path(rootPath).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.captures = self.root / "captures"
        self.captures.mkdir(exist_ok=True)
        self._inside(self.captures)
        default_skill = Path(__file__).resolve().parents[2] / "skills" / "cdp-page-contract" / "SKILL.md"
        self.skill_path = Path(skill_path).expanduser().resolve() if skill_path else default_skill

    def _inside(self, path):
        resolved = Path(path).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError("文件路径超出当前工作区。") from exc
        return resolved

    def _capture_dir(self, capture_id):
        if not isinstance(capture_id, str) or not _CAPTURE_ID.fullmatch(capture_id):
            raise WorkspaceError("无效的采集编号。")
        return self._inside(self.captures / capture_id)

    def save_capture(self, card, description=None, capture_id=None):
        record = sanitize_capture(card, description)
        capture_id = capture_id or card.get("captureId") or card.get("capture_id") or "cap-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        directory = self._capture_dir(capture_id)
        directory.mkdir(exist_ok=True)
        directory = self._inside(directory)
        record.update({"captureId": capture_id, "createdAt": sanitize_text(card.get("createdAt") or utc_now(), 80), "savedAt": utc_now()})
        generated = self._inside(directory / "generated")
        generated.mkdir(exist_ok=True)
        try:
            skill_ref = os.path.relpath(self.skill_path, directory).replace("\\", "/")
        except ValueError:
            skill_ref = str(self.skill_path)
        description_text = record["annotation"]["description"].strip()
        task = f'''# 整理控件能力草稿：{capture_id}

先读取 `capture.json`、`description.md`，再读取以下仓库技能：

{skill_ref}

仅根据本次资料整理能力草稿，输出到本目录的 `generated/`：

- `SKILL.md`：适用任务、参数、页面与账号范围、定位和业务含义、前后条件、失败与待验证项。
- `page-contract.json`：可重新定位的页面约定，明确 frame、候选 locator、业务键与成功证据。
- `runner.py` 或 `runner.mjs`：前端操作的候选执行脚本；未知信息使用明确占位符，不填入凭据。

本任务只生成文件草稿，不连接或操作网页，不运行生成的 runner，不提交真实业务动作，不安装技能，不修改 ZCode 或浏览器配置。不要读取浏览器 profile、Cookie、token、私钥或密码。引用技能中的实际操作流程只用来编写草稿，本任务不授权执行那些流程。

保持固定已登录 Chrome 和原 profile 的前端路线。控件采集不等于能力可用；所有产物标注 draft，缺少页面步骤或业务含义时记录待验证项。对生成文件可做不接触网页的格式检查，并说明检查范围。
'''
        atomic_write(directory / "description.md", description_text + "\n")
        atomic_write(directory / "task.md", task)
        # The record is the commit point; list/load ignore directories without it.
        atomic_write(directory / "capture.json", json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        return self.load_capture(capture_id)

    def load_capture(self, capture_id):
        directory = self._capture_dir(capture_id)
        try:
            record = json.loads((directory / "capture.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorkspaceError("采集记录不存在或无法读取。") from exc
        if not isinstance(record, dict) or record.get("captureId") != capture_id:
            raise WorkspaceError("采集记录编号不一致。")
        return {**record, "id": capture_id, "directory": str(directory), "taskPath": str(directory / "task.md"), "capturePath": str(directory / "capture.json"), "descriptionPath": str(directory / "description.md"), "generatedPath": str(directory / "generated")}

    def list_captures(self):
        items = []
        for directory in self.captures.iterdir():
            if not directory.is_dir() or not _CAPTURE_ID.fullmatch(directory.name):
                continue
            try:
                record = self.load_capture(directory.name)
            except WorkspaceError:
                continue
            items.append(record)
        return sorted(items, key=lambda item: item.get("savedAt", ""), reverse=True)

    def task_path(self, capture_id):
        return Path(self.load_capture(capture_id)["taskPath"])
