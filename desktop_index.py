"""Narrow, versioned adapter for ZCode's private desktop task index.

Only verified CLI session IDs are inserted. An explicit request may align only
this picker's owned task mode; other task state is never changed. This is not
an official ZCode API.
"""
from __future__ import annotations

from contextlib import closing
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import ntpath
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from workspace import sanitize_text


SUPPORTED_VERSION = "3.16.3.8992"
_EXECUTION_MODES = {"build", "edit", "yolo", "guarded", "auto"}
_CLI_COLUMNS = {
    "session": {"id": "TEXT", "directory": "TEXT", "workspace_id": "TEXT", "parent_id": "TEXT",
                "task_type": "TEXT", "time_deleted": "INTEGER"},
    "session_entry": {"id": "TEXT", "session_id": "TEXT", "type": "TEXT", "time_created": "INTEGER",
                      "time_updated": "INTEGER", "data": "TEXT"},
    "session_task_link": {"child_session_id": "TEXT"},
}


class DesktopIndexError(ValueError):
    pass


# (column, SQLite type, NOT NULL, primary-key position), from the inspected
# 3.16.3.8992 distribution. No migration or schema creation is performed.
_SCHEMA = {
    "tasks": [
        ("workspace_key", "TEXT", 1, 1), ("workspace_path", "TEXT", 1, 0),
        ("workspace_identity", "TEXT", 0, 0), ("task_id", "TEXT", 1, 2),
        ("title", "TEXT", 1, 0), ("task_status", "TEXT", 0, 0),
        ("provider", "TEXT", 0, 0), ("mode", "TEXT", 1, 0), ("model", "TEXT", 0, 0),
        ("migration_source", "TEXT", 0, 0), ("forked_from_task_id", "TEXT", 0, 0),
        ("created_at", "INTEGER", 1, 0), ("updated_at", "INTEGER", 1, 0),
        ("unread_at", "INTEGER", 0, 0), ("last_unread_at", "INTEGER", 1, 0),
        ("pinned", "INTEGER", 1, 0), ("archived", "INTEGER", 1, 0),
        ("deleted", "INTEGER", 1, 0), ("title_overridden", "INTEGER", 1, 0),
        ("meta_json", "TEXT", 1, 0), ("searchable_text", "TEXT", 1, 0),
        ("cron_automation_id", "TEXT", 0, 0), ("off_peak_task_id", "TEXT", 0, 0),
        ("host_id", "TEXT", 0, 0), ("root_path", "TEXT", 0, 0),
        ("project_id", "TEXT", 0, 0), ("execution_directories", "TEXT", 0, 0),
        ("sort_order", "INTEGER", 0, 0), ("cli_title", "TEXT", 0, 0),
        ("handoff_pending", "INTEGER", 1, 0), ("cli_title_source", "TEXT", 0, 0),
        ("cli_created_at", "INTEGER", 0, 0), ("cli_updated_at", "INTEGER", 0, 0),
        ("cli_last_outcome", "TEXT", 0, 0), ("cli_deleted", "INTEGER", 0, 0),
    ],
    "hosts": [
        ("host_id", "TEXT", 0, 1), ("kind", "TEXT", 1, 0),
        ("target_json", "TEXT", 0, 0), ("membership_id", "TEXT", 0, 0),
        ("position", "INTEGER", 1, 0), ("last_connection_status", "TEXT", 0, 0),
        ("last_connection_error", "TEXT", 0, 0), ("last_opened_at", "INTEGER", 0, 0),
    ],
    "projects": [
        ("project_id", "TEXT", 0, 1), ("host_id", "TEXT", 1, 0),
        ("name", "TEXT", 1, 0), ("name_overridden", "INTEGER", 1, 0),
        ("position", "INTEGER", 1, 0), ("pinned", "INTEGER", 1, 0),
        ("pinned_at", "INTEGER", 0, 0), ("deleted", "INTEGER", 1, 0),
        ("deleted_at", "INTEGER", 0, 0), ("created_at", "INTEGER", 1, 0),
        ("updated_at", "INTEGER", 1, 0),
    ],
    "project_roots": [
        ("project_id", "TEXT", 1, 1), ("root_path", "TEXT", 1, 2),
        ("position", "INTEGER", 1, 0), ("is_primary", "INTEGER", 1, 0),
    ],
}


def _path_key(value):
    return ntpath.normcase(ntpath.normpath(str(value).replace("/", "\\")))


def _default_index():
    base = os.environ.get("ZCODE_DATA_BASE_DIR") or os.environ.get("HOME") or str(Path.home())
    return Path(base).expanduser() / ".zcode" / "v2" / "tasks-index.sqlite"


def _default_cli_db():
    base = os.environ.get("ZCODE_DATA_BASE_DIR") or os.environ.get("HOME") or str(Path.home())
    return Path(base).expanduser() / ".zcode" / "cli" / "db" / "db.sqlite"


def _same_directory(left, right):
    if _path_key(left) == _path_key(right):
        return True
    try:
        return os.path.samefile(left, right)
    except (OSError, ValueError):
        return False


def _product_version(path):
    if os.name != "nt":
        raise DesktopIndexError("桌面索引适配只支持已核对的 Windows ZCode 安装版。")
    library = ctypes.WinDLL("version", use_last_error=True)
    library.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    library.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    library.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    library.GetFileVersionInfoW.restype = wintypes.BOOL
    library.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    library.VerQueryValueW.restype = wintypes.BOOL
    unused = wintypes.DWORD()
    size = library.GetFileVersionInfoSizeW(str(path), ctypes.byref(unused))
    data = ctypes.create_string_buffer(size)
    if not size or not library.GetFileVersionInfoW(str(path), 0, size, data):
        raise DesktopIndexError("无法读取所选 ZCode.exe 的产品版本。")
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not library.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 24:
        raise DesktopIndexError("所选 ZCode.exe 缺少可核对的产品版本。")
    words = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
    if words[0] != 0xFEEF04BD:
        raise DesktopIndexError("ZCode.exe 产品版本格式未知。")
    high, low = words[4], words[5]
    return ".".join(str(value) for value in (high >> 16, high & 65535, low >> 16, low & 65535))


class DesktopTaskIndex:
    def __init__(self, workspace, desktop_path=None, *, database_path=None, desktop_version=None, cli_db_path=None):
        root = workspace if isinstance(workspace, (str, os.PathLike)) else workspace.root
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir() or self.root.parent == self.root:
            raise DesktopIndexError("需要明确且存在的采集工作区，不能使用磁盘根目录。")
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        self.desktop = Path(desktop_path or local / "Programs" / "ZCode" / "ZCode.exe").expanduser().resolve()
        if not self.desktop.is_file() or self.desktop.name.lower() != "zcode.exe":
            raise DesktopIndexError("请选择真实安装的 ZCode.exe。")
        # Explicit version injection supports isolated schema tests; production
        # callers omit it and read the executable's immutable version resource.
        self.version = desktop_version or _product_version(self.desktop)
        if self.version != SUPPORTED_VERSION:
            raise DesktopIndexError("桌面索引仅适配 ZCode " + SUPPORTED_VERSION + "，当前版本不受支持。")
        self.index = Path(database_path or _default_index()).expanduser().resolve()
        if not self.index.is_file():
            raise DesktopIndexError("未找到已有桌面任务索引，请先在 ZCode 中初始化工作区。")
        self.cli_db = Path(cli_db_path or _default_cli_db()).expanduser().resolve()
        self.backups = (self.root / ".handoffs" / "index-backups").resolve()
        if not self.backups.is_relative_to(self.root):
            raise DesktopIndexError("索引备份目录超出当前采集工作区。")

    def _connect(self, writable=False):
        connection = sqlite3.connect(self.index.as_uri() + ("?mode=rw" if writable else "?mode=ro"), uri=True, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _connect_cli(self):
        if not self.cli_db.is_file():
            raise DesktopIndexError("未找到已有 CLI 会话库，不能确认真实会话。")
        connection = sqlite3.connect(self.cli_db.as_uri() + "?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _validate_cli_schema(connection):
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, expected in _CLI_COLUMNS.items():
            if table not in tables:
                raise DesktopIndexError("CLI 会话库结构未知，缺少：" + table)
            columns = {row[1]: (row[2].upper(), row[5]) for row in connection.execute("PRAGMA table_info(" + table + ")")}
            if any(columns.get(name, (None,))[0] != kind for name, kind in expected.items()):
                raise DesktopIndexError("CLI 会话库元数据结构未知：" + table)
            if table in {"session", "session_entry"} and columns["id"][1] != 1:
                raise DesktopIndexError("CLI 会话库主键未知：" + table)

    def _native_session(self, session_id, mode):
        """Read only identity and execution metadata, never conversation rows."""
        try:
            with closing(self._connect_cli()) as connection:
                connection.execute("BEGIN")
                self._validate_cli_schema(connection)
                row = connection.execute("SELECT id,directory,workspace_id,parent_id,task_type,time_deleted FROM session WHERE id=?", (session_id,)).fetchone()
                if row is None:
                    raise DesktopIndexError("CLI 中不存在该真实会话，不能登记桌面索引。")
                if row["time_deleted"] is not None:
                    raise DesktopIndexError("CLI 会话已删除，不能重新登记。")
                if row["workspace_id"]:
                    raise DesktopIndexError("只能登记本地工作区的 CLI 会话。")
                if row["task_type"] not in {"interactive", "fork", "workflow_parent"}:
                    raise DesktopIndexError("只能登记可见的 CLI 主会话，不能登记内部子任务。")
                if connection.execute("SELECT 1 FROM session_task_link WHERE child_session_id=? LIMIT 1", (session_id,)).fetchone():
                    raise DesktopIndexError("该 CLI 会话属于子 Agent，不能登记为主对话。")
                directory = row["directory"]
                if (not isinstance(directory, str) or not directory.strip() or not Path(directory).is_absolute()
                        or not Path(directory).is_dir() or not _same_directory(directory, self.root)):
                    raise DesktopIndexError("CLI 会话目录与当前采集工作区不一致。")
                execution = connection.execute("SELECT data FROM session_entry WHERE session_id=? AND type='runtime/execution_state' ORDER BY time_updated DESC,time_created DESC,id DESC LIMIT 1", (session_id,)).fetchone()
                try:
                    state = json.loads(execution["data"]) if execution else None
                except (ValueError, TypeError):
                    state = None
                if (not isinstance(state, dict) or not isinstance(state.get("mode"), str) or state["mode"] not in _EXECUTION_MODES
                        or not isinstance(state.get("planEnabled"), bool)):
                    raise DesktopIndexError("CLI 会话缺少可确认的持久执行模式，未登记索引。")
                if state["mode"] != mode or state["planEnabled"]:
                    raise DesktopIndexError("CLI 会话执行模式与本次请求不一致或仍处于计划状态，未修改索引。")
                return {"sessionId": row["id"], "directory": directory, "mode": state["mode"], "parentId": row["parent_id"]}
        except (sqlite3.Error, OSError, ValueError) as error:
            if isinstance(error, DesktopIndexError):
                raise
            raise DesktopIndexError("无法只读确认 CLI 会话元数据：" + type(error).__name__) from error

    @staticmethod
    def _validate_schema(connection):
        for table, expected in _SCHEMA.items():
            actual = [(row[1], row[2].upper(), row[3], row[5]) for row in connection.execute("PRAGMA table_info(" + table + ")")]
            if actual != expected:
                raise DesktopIndexError("桌面索引结构未知，拒绝写入：" + table)
        indexes = {row[0]: re.sub(r"\s+", "", (row[1] or "").lower()) for row in connection.execute("SELECT name,sql FROM sqlite_master WHERE type='index'")}
        required = {
            "idx_tasks_host_task": "createuniqueindexidx_tasks_host_taskontasks(host_id,task_id)",
            "idx_projects_host_name": "createuniqueindexidx_projects_host_nameonprojects(host_id,name)wheredeleted=0",
        }
        if any(indexes.get(name) != sql for name, sql in required.items()):
            raise DesktopIndexError("桌面索引唯一约束未知，拒绝写入。")
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('tasks','hosts','projects','project_roots')").fetchone():
            raise DesktopIndexError("桌面索引存在未知写入触发器，拒绝适配。")
        local = connection.execute("SELECT kind FROM hosts WHERE host_id='local'").fetchone()
        if not local or local[0] != "local":
            raise DesktopIndexError("桌面本地主机映射未初始化或未知，拒绝推断。")

    def _project(self, connection):
        rows = connection.execute("SELECT p.project_id,p.name,r.root_path FROM projects p JOIN project_roots r ON r.project_id=p.project_id WHERE p.host_id='local' AND p.deleted=0").fetchall()
        matches = [row for row in rows if _same_directory(row["root_path"], self.root)]
        if len(matches) > 1:
            raise DesktopIndexError("同一工作区对应多个桌面项目，请先在 ZCode 中明确归属。")
        return dict(matches[0]) if matches else None

    def preflight(self):
        """Read-only checks to perform before starting a paid CLI task."""
        try:
            with closing(self._connect()) as connection:
                self._validate_schema(connection)
                project = self._project(connection)
            with closing(self._connect_cli()) as cli:
                self._validate_cli_schema(cli)
            return {"canRegister": True, "desktopVersion": self.version, "indexPath": str(self.index),
                    "workspacePath": str(self.root), "workspaceKey": str(self.root),
                    "cliDatabasePath": str(self.cli_db), "cliSchemaChecked": True,
                    "projectId": project["project_id"] if project else None,
                    "willCreateProject": project is None, "backupDirectory": str(self.backups)}
        except sqlite3.Error as error:
            raise DesktopIndexError("无法只读核对桌面索引：" + type(error).__name__) from error

    def _backup(self):
        self.backups.mkdir(parents=True, exist_ok=True)
        if not self.backups.resolve().is_relative_to(self.root):
            raise DesktopIndexError("备份目录超出采集工作区。")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = self.backups / ("tasks-index-" + stamp + "-" + uuid.uuid4().hex[:8] + ".sqlite")
        deadline = time.monotonic() + 15

        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise DesktopIndexError("索引备份超时，尚未登记任务。")

        with closing(self._connect()) as source, closing(sqlite3.connect(path)) as destination:
            source.backup(destination, pages=128, progress=progress, sleep=.05)
            if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise DesktopIndexError("索引备份校验失败，尚未登记任务。")
        return path

    def _existing(self, connection, session_id, project, native):
        rows = connection.execute("SELECT * FROM tasks WHERE task_id=?", (session_id,)).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise DesktopIndexError("该会话已有多个索引身份，拒绝覆盖。")
        row = rows[0]
        if (row["host_id"] != "local" or row["workspace_identity"] is not None
                or row["workspace_key"] != native["directory"]
                or row["workspace_path"] != native["directory"]
                or not project or row["project_id"] != project["project_id"]
                or row["root_path"] != project["root_path"]
                or row["archived"] or row["deleted"] or row["cli_deleted"]
                or not row["cli_created_at"]):
            raise DesktopIndexError("已有会话索引的范围或状态不匹配，拒绝覆盖、移动或取消归档。")
        return dict(row)

    @staticmethod
    def _needs_mode_update(row, native, task_relative, update_owned_mode):
        if row["mode"] == native["mode"]:
            return False
        if not update_owned_mode:
            raise DesktopIndexError("已有索引模式与真实 CLI 会话不一致；未修改既有任务。")
        try:
            metadata = json.loads(row["meta_json"])
            owner = metadata.get("cdpControlPicker") if isinstance(metadata, dict) else None
        except (ValueError, TypeError):
            owner = None
        if (not task_relative or not isinstance(owner, dict) or owner.get("schemaVersion") != 1
                or owner.get("sessionId") != native["sessionId"] or owner.get("taskPath") != task_relative):
            raise DesktopIndexError("不能确认该索引由本工具和当前任务创建，拒绝对齐模式。")
        return True

    def _result(self, row, backup=None, created=False, mode_updated=False):
        return {"taskId": row["task_id"], "sessionId": row["task_id"], "projectId": row["project_id"],
                "hostId": row["host_id"], "workspaceKey": row["workspace_key"], "rootPath": row["root_path"],
                "mode": row["mode"],
                "indexPath": str(self.index), "backupPath": str(backup) if backup else None,
                "created": created, "modeUpdated": mode_updated, "indexRegistered": True, "guiVisibilityVerified": False}

    def register(self, session_id, *, title, task_path=None, mode="yolo", update_owned_mode=False):
        if not isinstance(session_id, str) or not re.fullmatch(r"sess_[A-Za-z0-9][A-Za-z0-9_-]{0,159}", session_id):
            raise DesktopIndexError("需要 CLI 返回的真实 sessionId，不生成替代任务编号。")
        title = sanitize_text(title, 200).strip()
        if not title:
            raise DesktopIndexError("任务标题不能为空。")
        if not isinstance(mode, str) or mode not in _EXECUTION_MODES:
            raise DesktopIndexError("请求的 CLI 执行模式无效。")
        if not isinstance(update_owned_mode, bool):
            raise DesktopIndexError("模式对齐选项必须是布尔值。")
        task_relative = None
        if task_path is not None:
            task = Path(task_path).expanduser().resolve()
            if not task.is_file() or not task.is_relative_to(self.root):
                raise DesktopIndexError("任务文件须存在且位于当前采集工作区。")
            task_relative = task.relative_to(self.root).as_posix()
        self.preflight()
        native = self._native_session(session_id, mode)
        try:
            with closing(self._connect()) as connection:
                existing = self._existing(connection, session_id, self._project(connection), native)
                if existing and not self._needs_mode_update(existing, native, task_relative, update_owned_mode):
                    return self._result(existing)
            backup = self._backup()
            with closing(self._connect(writable=True)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    self._validate_schema(connection)
                    current_native = self._native_session(session_id, mode)
                    if current_native != native:
                        raise DesktopIndexError("CLI 会话身份在登记期间变化，原索引保持不变。")
                    project = self._project(connection)
                    existing = self._existing(connection, session_id, project, native)
                    if existing:
                        if not self._needs_mode_update(existing, native, task_relative, update_owned_mode):
                            connection.execute("ROLLBACK")
                            return self._result(existing, backup)
                        connection.execute("UPDATE tasks SET mode=? WHERE workspace_key=? AND task_id=?", (mode, existing["workspace_key"], session_id))
                        row = self._existing(connection, session_id, project, native)
                        if row is None or row["mode"] != mode:
                            raise DesktopIndexError("本工具模式对齐回读失败，事务已回滚。")
                        connection.execute("COMMIT")
                        row = self._existing(connection, session_id, self._project(connection), native)
                        if row is None or row["mode"] != mode:
                            raise DesktopIndexError("未确认已提交的索引模式。")
                        return self._result(row, backup, mode_updated=True)
                    now = int(time.time() * 1000)
                    if project is None:
                        names = {row[0] for row in connection.execute("SELECT name FROM projects WHERE host_id='local' AND deleted=0")}
                        base = self.root.name
                        name, number = base, 2
                        while name in names:
                            name, number = base + " (CDP " + str(number) + ")", number + 1
                        project = {"project_id": str(uuid.uuid4()), "root_path": str(self.root), "name": name}
                        position = connection.execute("SELECT coalesce(min(position),1)-1 FROM projects WHERE host_id='local'").fetchone()[0]
                        connection.execute("INSERT INTO projects(project_id,host_id,name,position,created_at,updated_at) VALUES(?,'local',?,?,?,?)", (project["project_id"], name, position, now, now))
                        connection.execute("INSERT INTO project_roots(project_id,root_path,position,is_primary) VALUES(?,?,0,1)", (project["project_id"], project["root_path"]))
                    meta = json.dumps({"cdpControlPicker": {"schemaVersion": 1, "sessionId": session_id, "taskPath": task_relative}}, ensure_ascii=False)
                    connection.execute("""INSERT INTO tasks(
                        workspace_key,workspace_path,task_id,title,task_status,provider,mode,forked_from_task_id,
                        created_at,updated_at,last_unread_at,pinned,archived,deleted,title_overridden,
                        meta_json,searchable_text,host_id,root_path,project_id,execution_directories,
                        cli_title,handoff_pending,cli_title_source,cli_created_at,cli_updated_at,cli_deleted)
                        VALUES(?,?,?,?,'completed','glm',?,?,?,?,0,0,0,0,0,?,'','local',?,?,
                               '[]',?,0,'first_input',?,?,0)""",
                        (native["directory"], native["directory"], session_id, title, mode, native["parentId"], now, now, meta,
                         project["root_path"], project["project_id"], title, now, now))
                    row = self._existing(connection, session_id, project, native)
                    if row is None:
                        raise DesktopIndexError("索引事务回读失败。")
                    connection.execute("COMMIT")
                    # Read the committed row, rather than reporting a planned insert.
                    row = self._existing(connection, session_id, self._project(connection), native)
                    return self._result(row, backup, True)
                except Exception:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
        except sqlite3.Error as error:
            raise DesktopIndexError("桌面索引登记失败；未覆盖既有任务：" + type(error).__name__) from error
