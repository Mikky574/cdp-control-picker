"""Isolated SQLite fixtures only; never opens the user's ZCode databases."""
from pathlib import Path
from contextlib import closing, contextmanager
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_index import DesktopTaskIndex, DesktopIndexError, SUPPORTED_VERSION


# Independent fixture of the four inspected desktop tables. Other desktop
# tables are deliberately unrelated to this adapter and must be preserved.
DDL = """
CREATE TABLE hosts(host_id TEXT PRIMARY KEY,kind TEXT NOT NULL,target_json TEXT,membership_id TEXT,
 position INTEGER NOT NULL DEFAULT 0,last_connection_status TEXT,last_connection_error TEXT,last_opened_at INTEGER);
CREATE TABLE projects(project_id TEXT PRIMARY KEY,host_id TEXT NOT NULL REFERENCES hosts(host_id),name TEXT NOT NULL,
 name_overridden INTEGER NOT NULL DEFAULT 0,position INTEGER NOT NULL,pinned INTEGER NOT NULL DEFAULT 0,
 pinned_at INTEGER,deleted INTEGER NOT NULL DEFAULT 0,deleted_at INTEGER,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL);
CREATE UNIQUE INDEX idx_projects_host_name ON projects(host_id,name) WHERE deleted=0;
CREATE TABLE project_roots(project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
 root_path TEXT NOT NULL,position INTEGER NOT NULL,is_primary INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(project_id,root_path));
CREATE TABLE tasks(
 workspace_key TEXT NOT NULL,workspace_path TEXT NOT NULL,workspace_identity TEXT,task_id TEXT NOT NULL,
 title TEXT NOT NULL DEFAULT '',task_status TEXT,provider TEXT,mode TEXT NOT NULL DEFAULT 'build',model TEXT,
 migration_source TEXT,forked_from_task_id TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,unread_at INTEGER,
 last_unread_at INTEGER NOT NULL DEFAULT 0,pinned INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,
 deleted INTEGER NOT NULL DEFAULT 0,title_overridden INTEGER NOT NULL DEFAULT 0,meta_json TEXT NOT NULL DEFAULT '{}',
 searchable_text TEXT NOT NULL DEFAULT '',cron_automation_id TEXT,off_peak_task_id TEXT,host_id TEXT,root_path TEXT,
 project_id TEXT,execution_directories TEXT,sort_order INTEGER,cli_title TEXT,handoff_pending INTEGER NOT NULL DEFAULT 0,
 cli_title_source TEXT,cli_created_at INTEGER,cli_updated_at INTEGER,cli_last_outcome TEXT,cli_deleted INTEGER,
 PRIMARY KEY(workspace_key,task_id));
CREATE UNIQUE INDEX idx_tasks_host_task ON tasks(host_id,task_id);
CREATE TABLE unrelated_data(id TEXT PRIMARY KEY,payload TEXT);
INSERT INTO hosts(host_id,kind) VALUES('local','local');
INSERT INTO unrelated_data VALUES('fixture','keep this unrelated metadata');
"""

# Metadata-only subset of the inspected native CLI schema. No production
# connection can select messages, parts, model choices, or credentials.
CLI_DDL = """
CREATE TABLE session(id TEXT PRIMARY KEY,directory TEXT NOT NULL,workspace_id TEXT,parent_id TEXT,
 task_type TEXT NOT NULL DEFAULT 'interactive',time_deleted INTEGER);
CREATE TABLE session_entry(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,type TEXT NOT NULL,
 time_created INTEGER NOT NULL,time_updated INTEGER NOT NULL,data TEXT NOT NULL);
CREATE TABLE session_task_link(id TEXT PRIMARY KEY,child_session_id TEXT NOT NULL);
CREATE TABLE message(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,body TEXT NOT NULL);
INSERT INTO message VALUES('unrelated','sess_unrelated','Fixture body must not be selected');
"""


@contextmanager
def fixture_database(path):
    # sqlite3.Connection.__exit__ commits/rolls back but does not close handles.
    # Explicit closing keeps fixture cleanup deterministic on Windows.
    with closing(sqlite3.connect(path)) as connection, connection:
        yield connection


class DesktopIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="picker-index-test-")
        self.base = Path(self.temp.name)
        self.workspace = self.base / "CDP Workspace with spaces"
        self.workspace.mkdir()
        self.task = self.workspace / "task.md"
        self.task.write_text("Local fixture draft.", encoding="utf-8")
        self.exe = self.base / "ZCode.exe"
        self.exe.write_bytes(b"fixture-not-executable")
        self.db_path = self.base / "fixture tasks-index.sqlite"
        self.create_database(self.db_path)
        self.cli_db_path = self.base / "fixture CLI.sqlite"
        with fixture_database(self.cli_db_path) as db:
            db.executescript(CLI_DDL)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def create_database(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with fixture_database(path) as db:
            db.executescript(DDL)

    def adapter(self, **options):
        options.setdefault("database_path", self.db_path)
        options.setdefault("desktop_version", SUPPORTED_VERSION)
        options.setdefault("cli_db_path", self.cli_db_path)
        return DesktopTaskIndex(self.workspace, self.exe, **options)

    def seed_cli(self, session_id, *, directory=None, workspace_id=None, parent_id=None,
                 task_type="interactive", deleted=None, mode="yolo", plan=False, linked=False,
                 execution=None):
        directory = str(self.workspace) if directory is None else directory
        state = {"mode": mode, "planEnabled": plan} if execution is None else execution
        with fixture_database(self.cli_db_path) as db:
            db.execute("INSERT INTO session VALUES(?,?,?,?,?,?)", (session_id, directory, workspace_id, parent_id, task_type, deleted))
            db.execute("INSERT INTO session_entry VALUES(?,?,'runtime/execution_state',10,20,?)", (session_id + ":execution", session_id, json.dumps(state)))
            if linked:
                db.execute("INSERT INTO session_task_link VALUES(?,?)", (session_id + ":link", session_id))

    def query(self, sql, args=()):
        with fixture_database(self.db_path) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql, args)]

    def seed_project(self, project_id="project-existing", root=None, name="My renamed project"):
        root = str(root or self.workspace)
        with fixture_database(self.db_path) as db:
            db.execute("INSERT INTO projects(project_id,host_id,name,name_overridden,position,pinned,created_at,updated_at) VALUES(?,'local',?,1,7,1,100,200)", (project_id, name))
            db.execute("INSERT INTO project_roots VALUES(?,?,0,1)", (project_id, root))
        return project_id

    def test_preflight_is_read_only_and_reports_project_plan(self):
        before = self.db_path.read_bytes()
        cli_before = self.cli_db_path.read_bytes()
        result = self.adapter().preflight()
        self.assertTrue(result["canRegister"])
        self.assertTrue(result["willCreateProject"])
        self.assertIsNone(result["projectId"])
        self.assertEqual(result["workspaceKey"], str(self.workspace))
        self.assertEqual(self.db_path.read_bytes(), before)
        self.assertEqual(self.cli_db_path.read_bytes(), cli_before)
        self.assertTrue(result["cliSchemaChecked"])
        self.assertFalse((self.workspace / ".handoffs").exists())

    def test_register_creates_project_task_and_verified_backup_without_unrelated_changes(self):
        self.seed_cli("sess_actual_cli_id")
        cli_before = self.cli_db_path.read_bytes()
        result = self.adapter().register("sess_actual_cli_id", title="订单页面能力", task_path=self.task)
        self.assertEqual(result["taskId"], "sess_actual_cli_id")
        self.assertEqual(result["sessionId"], result["taskId"])
        self.assertTrue(result["created"])
        self.assertTrue(result["indexRegistered"])
        self.assertFalse(result["guiVisibilityVerified"])
        self.assertEqual(result["mode"], "yolo")
        row = self.query("SELECT * FROM tasks")[0]
        self.assertEqual((row["host_id"], row["workspace_key"], row["workspace_path"]), ("local", str(self.workspace), str(self.workspace)))
        self.assertEqual(row["project_id"], result["projectId"])
        self.assertEqual(row["mode"], "yolo")
        self.assertGreater(row["cli_created_at"], 0)
        self.assertEqual((row["archived"], row["deleted"], row["cli_deleted"]), (0, 0, 0))
        self.assertEqual(json.loads(row["meta_json"])["cdpControlPicker"]["taskPath"], "task.md")
        backup = Path(result["backupPath"])
        self.assertTrue(backup.is_relative_to(self.workspace / ".handoffs" / "index-backups"))
        with fixture_database(backup) as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)
        self.assertEqual(self.query("SELECT * FROM unrelated_data"), [{"id": "fixture", "payload": "keep this unrelated metadata"}])
        self.assertEqual(self.cli_db_path.read_bytes(), cli_before)

    def test_existing_project_spelling_name_and_user_metadata_are_preserved(self):
        stored_root = str(self.workspace).swapcase()
        project_id = self.seed_project(root=stored_root)
        self.seed_cli("sess_with_existing_project")
        before = self.query("SELECT * FROM projects")
        result = self.adapter().register("sess_with_existing_project", title="Draft", task_path=self.task)
        self.assertEqual(result["projectId"], project_id)
        self.assertEqual(result["rootPath"], stored_root)
        self.assertEqual(self.query("SELECT * FROM projects"), before)
        self.assertEqual(len(self.query("SELECT * FROM project_roots")), 1)

    def test_repeated_registration_never_updates_existing_task(self):
        self.seed_cli("sess_idempotent")
        adapter = self.adapter()
        first = adapter.register("sess_idempotent", title="Original title")
        before = self.query("SELECT * FROM tasks")
        second = adapter.register("sess_idempotent", title="Must not replace original")
        self.assertFalse(second["created"])
        self.assertIsNone(second["backupPath"])
        self.assertEqual(first["projectId"], second["projectId"])
        self.assertEqual(self.query("SELECT * FROM tasks"), before)
        self.assertEqual(len(list((self.workspace / ".handoffs" / "index-backups").glob("*.sqlite"))), 1)

    def test_foreign_task_collision_is_rejected_without_reassignment(self):
        self.seed_cli("sess_foreign")
        project = self.seed_project()
        foreign = str(self.base / "another workspace")
        with fixture_database(self.db_path) as db:
            db.execute("INSERT INTO tasks(workspace_key,workspace_path,task_id,title,created_at,updated_at,host_id,root_path,project_id,cli_created_at) VALUES(?,?,?,'User task',1,2,'local',?,?,1)", (foreign, foreign, "sess_foreign", foreign, project))
        before = self.query("SELECT * FROM tasks")
        with self.assertRaises(DesktopIndexError):
            self.adapter().register("sess_foreign", title="Do not overwrite")
        self.assertEqual(self.query("SELECT * FROM tasks"), before)
        self.assertFalse((self.workspace / ".handoffs").exists())

    def test_existing_archived_or_deleted_task_is_never_revived(self):
        adapter = self.adapter()
        for flag in ("archived", "deleted", "cli_deleted"):
            session = "sess_" + flag
            self.seed_cli(session)
            adapter.register(session, title="User action fixture")
            with fixture_database(self.db_path) as db:
                db.execute("UPDATE tasks SET " + flag + "=1 WHERE task_id=?", (session,))
            before = self.query("SELECT * FROM tasks WHERE task_id=?", (session,))
            with self.subTest(flag=flag), self.assertRaises(DesktopIndexError):
                adapter.register(session, title="Must not revive")
            self.assertEqual(self.query("SELECT * FROM tasks WHERE task_id=?", (session,)), before)

    def test_schema_version_and_unique_constraint_changes_fail_before_write(self):
        with self.assertRaises(DesktopIndexError):
            self.adapter(desktop_version="3.17.0.1")
        with fixture_database(self.db_path) as db:
            db.execute("DROP INDEX idx_tasks_host_task")
        with self.assertRaises(DesktopIndexError):
            self.adapter().register("sess_no_constraint", title="Draft")
        self.assertEqual(self.query("SELECT * FROM tasks"), [])
        self.assertFalse((self.workspace / ".handoffs").exists())
        self.db_path = self.base / "future schema.sqlite"
        self.create_database(self.db_path)
        with fixture_database(self.db_path) as db:
            db.execute("ALTER TABLE tasks ADD COLUMN unknown_future_field TEXT")
        with self.assertRaises(DesktopIndexError):
            self.adapter().preflight()

    def test_ambiguous_projects_and_outside_task_paths_are_rejected(self):
        self.seed_project("first", name="First")
        self.seed_project("second", name="Second")
        with self.assertRaises(DesktopIndexError):
            self.adapter().preflight()
        outside = self.base / "outside.md"
        outside.write_text("fixture", encoding="utf-8")
        with self.assertRaises(DesktopIndexError):
            self.adapter().register("sess_outside", title="Draft", task_path=outside)
        for invalid in ("invented-task-id", "sess_../escape", "", None):
            with self.subTest(session=invalid), self.assertRaises(DesktopIndexError):
                self.adapter().register(invalid, title="Draft")
        self.assertEqual(self.query("SELECT * FROM tasks"), [])

    def test_backup_failure_and_transaction_failure_leave_no_partial_project_or_task(self):
        self.seed_cli("sess_backup_failure")
        self.seed_cli("sess_transaction_failure")
        adapter = self.adapter()
        with patch.object(adapter, "_backup", side_effect=DesktopIndexError("fixture backup failure")):
            with self.assertRaises(DesktopIndexError):
                adapter.register("sess_backup_failure", title="Draft")
        self.assertEqual(self.query("SELECT * FROM projects"), [])
        original = adapter._existing
        calls = []

        def fail_readback(*args):
            calls.append(1)
            if len(calls) == 3:
                raise DesktopIndexError("fixture transaction readback failure")
            return original(*args)

        with patch.object(adapter, "_existing", side_effect=fail_readback):
            with self.assertRaises(DesktopIndexError):
                adapter.register("sess_transaction_failure", title="Draft")
        self.assertEqual(self.query("SELECT * FROM tasks"), [])
        self.assertEqual(self.query("SELECT * FROM projects"), [])
        self.assertEqual(self.query("SELECT * FROM project_roots"), [])

    def test_sqlite_backup_includes_uncheckpointed_wal_data(self):
        self.seed_cli("sess_wal_backup")
        writer = sqlite3.connect(self.db_path)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO unrelated_data VALUES('wal-only','committed in WAL')")
            writer.commit()
            self.assertTrue(Path(str(self.db_path) + "-wal").is_file())
            result = self.adapter().register("sess_wal_backup", title="Draft")
            with fixture_database(result["backupPath"]) as backup:
                self.assertEqual(backup.execute("SELECT payload FROM unrelated_data WHERE id='wal-only'").fetchone()[0], "committed in WAL")
                self.assertEqual(backup.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)
        finally:
            writer.close()

    def test_data_directory_environment_and_real_version_reader_are_used(self):
        data_base = self.base / "isolated ZCode data"
        expected = data_base / ".zcode" / "v2" / "tasks-index.sqlite"
        self.create_database(expected)
        with patch.dict(os.environ, {"ZCODE_DATA_BASE_DIR": str(data_base)}), patch("desktop_index._product_version", return_value=SUPPORTED_VERSION) as read_version:
            cli_path = data_base / ".zcode" / "cli" / "db" / "db.sqlite"
            cli_path.parent.mkdir(parents=True)
            with fixture_database(cli_path) as cli:
                cli.executescript(CLI_DDL)
            adapter = DesktopTaskIndex(self.workspace, self.exe)
            self.assertEqual(adapter.index, expected)
            self.assertEqual(adapter.cli_db, cli_path)
            read_version.assert_called_once_with(self.exe)
            self.assertTrue(adapter.preflight()["canRegister"])
        self.assertFalse((self.workspace / ".handoffs").exists())

    def test_missing_native_session_is_rejected_without_index_or_cli_writes(self):
        cli_before, index_before = self.cli_db_path.read_bytes(), self.db_path.read_bytes()
        with self.assertRaisesRegex(DesktopIndexError, "不存在"):
            self.adapter().register("sess_missing", title="Must not invent a session")
        self.assertEqual(self.cli_db_path.read_bytes(), cli_before)
        self.assertEqual(self.db_path.read_bytes(), index_before)
        self.assertFalse((self.workspace / ".handoffs").exists())

    def test_deleted_remote_child_and_wrong_project_sessions_are_rejected(self):
        other = self.base / "other project"
        other.mkdir()
        cases = (
            {"deleted": 0}, {"deleted": 100}, {"workspace_id": "remote-workspace"},
            {"task_type": "subagent_child"}, {"task_type": "workflow_child"},
            {"task_type": "selection_side_chat"}, {"task_type": "background"},
            {"task_type": "fork", "linked": True}, {"directory": str(other)},
            {"directory": "ssh://remote/project"}, {"directory": str(self.base / "missing project")},
        )
        for number, options in enumerate(cases):
            session_id = "sess_rejected_" + str(number)
            self.seed_cli(session_id, **options)
            cli_before = self.cli_db_path.read_bytes()
            with self.subTest(options=options), self.assertRaises(DesktopIndexError):
                self.adapter().register(session_id, title="Must not register")
            self.assertEqual(self.cli_db_path.read_bytes(), cli_before)
            self.assertEqual(self.query("SELECT * FROM tasks"), [])
            self.assertFalse((self.workspace / ".handoffs").exists())

    def test_visible_main_forks_and_workflow_parents_keep_native_parent_identity(self):
        for task_type in ("interactive", "fork", "workflow_parent"):
            session_id = "sess_visible_" + task_type
            self.seed_cli(session_id, task_type=task_type, parent_id="sess_original")
            result = self.adapter().register(session_id, title="Visible native conversation")
            self.assertEqual(result["sessionId"], session_id)
            row = self.query("SELECT * FROM tasks WHERE task_id=?", (session_id,))[0]
            self.assertEqual(row["forked_from_task_id"], "sess_original")
            self.assertEqual(row["mode"], "yolo")

    def test_native_workspace_key_and_existing_project_root_remain_separate_exact_strings(self):
        native_directory = str(self.workspace) + os.sep + "."
        stored_root = str(self.workspace).swapcase()
        project = self.seed_project(root=stored_root)
        self.seed_cli("sess_native_alias", directory=native_directory)
        result = self.adapter().register("sess_native_alias", title="Alias fixture")
        self.assertEqual(result["workspaceKey"], native_directory)
        self.assertEqual(result["rootPath"], stored_root)
        self.assertEqual(result["projectId"], project)
        row = self.query("SELECT * FROM tasks")[0]
        self.assertEqual((row["workspace_key"], row["workspace_path"], row["root_path"]), (native_directory, native_directory, stored_root))
        with fixture_database(self.cli_db_path) as cli:
            cli.execute("UPDATE session SET directory=? WHERE id='sess_native_alias'", (str(self.workspace),))
        before = self.query("SELECT * FROM tasks")
        with self.assertRaises(DesktopIndexError):
            self.adapter().register("sess_native_alias", title="Must not rekey")
        self.assertEqual(self.query("SELECT * FROM tasks"), before)

    def test_native_mode_must_be_complete_and_equal_to_requested_permission_mode(self):
        cases = (
            {"mode": "edit", "planEnabled": False}, {"mode": "yolo", "planEnabled": True},
            {"mode": "future", "planEnabled": False}, {"mode": [], "planEnabled": False},
            {"mode": "yolo", "planEnabled": "false"}, {"mode": "yolo"},
        )
        for number, execution in enumerate(cases):
            session_id = "sess_bad_mode_" + str(number)
            self.seed_cli(session_id, execution=execution)
            with self.subTest(execution=execution), self.assertRaises(DesktopIndexError):
                self.adapter().register(session_id, title="Mode mismatch")
        self.seed_cli("sess_no_execution")
        self.seed_cli("sess_bad_json")
        with fixture_database(self.cli_db_path) as cli:
            cli.execute("DELETE FROM session_entry WHERE session_id='sess_no_execution'")
            cli.execute("UPDATE session_entry SET data='invalid JSON' WHERE session_id='sess_bad_json'")
        for session_id in ("sess_no_execution", "sess_bad_json"):
            with self.subTest(session_id=session_id), self.assertRaises(DesktopIndexError):
                self.adapter().register(session_id, title="Missing mode")
        self.assertEqual(self.query("SELECT * FROM tasks"), [])
        self.assertFalse((self.workspace / ".handoffs").exists())

    def test_latest_execution_metadata_is_used_without_selecting_conversation_or_model_rows(self):
        session_id = "sess_latest_execution"
        self.seed_cli(session_id, mode="edit")
        with fixture_database(self.cli_db_path) as cli:
            cli.execute("INSERT INTO session_entry VALUES(?,?,'runtime/execution_state',30,30,?)", ("newer-execution", session_id, json.dumps({"mode": "yolo", "planEnabled": False})))
            cli.execute("INSERT INTO session_entry VALUES(?,?,'runtime/model_selection',100,100,?)", ("model-must-not-be-read", session_id, "sensitive fixture metadata"))
        adapter = self.adapter()
        original = adapter._connect_cli
        statements = []

        def traced_cli():
            connection = original()
            connection.set_trace_callback(statements.append)
            return connection

        before = self.cli_db_path.read_bytes()
        with patch.object(adapter, "_connect_cli", side_effect=traced_cli):
            result = adapter.register(session_id, title="Use latest native mode")
        self.assertEqual(result["mode"], "yolo")
        self.assertEqual(self.cli_db_path.read_bytes(), before)
        selects = [statement.lower() for statement in statements if statement.lstrip().lower().startswith("select")]
        self.assertTrue(any("runtime/execution_state" in statement for statement in selects))
        self.assertFalse(any("from message" in statement or "from part" in statement or "runtime/model_selection" in statement for statement in selects))
        self.assertFalse(any(statement.lstrip().lower().startswith(("insert", "update", "delete", "create", "alter", "replace")) for statement in statements))

    def test_missing_or_unknown_cli_metadata_schema_fails_before_any_index_write(self):
        missing = self.base / "missing CLI.sqlite"
        with self.assertRaises(DesktopIndexError):
            self.adapter(cli_db_path=missing).preflight()
        self.assertFalse(missing.exists())
        for number, change in enumerate(("DROP TABLE session_task_link", "ALTER TABLE session_entry RENAME COLUMN data TO unknown_data", "ALTER TABLE session RENAME COLUMN time_deleted TO unknown_deleted")):
            path = self.base / ("unknown-cli-" + str(number) + ".sqlite")
            with fixture_database(path) as cli:
                cli.executescript(CLI_DDL)
                cli.execute(change)
            with self.subTest(change=change), self.assertRaises(DesktopIndexError):
                self.adapter(cli_db_path=path).preflight()
        self.assertEqual(self.query("SELECT * FROM tasks"), [])
        self.assertFalse((self.workspace / ".handoffs").exists())

    def test_native_identity_change_during_registration_rolls_back_new_project_and_task(self):
        session_id = "sess_changed_identity"
        self.seed_cli(session_id)
        adapter = self.adapter()
        original = adapter._native_session
        calls = []

        def change_identity(*args):
            calls.append(1)
            if len(calls) == 2:
                with fixture_database(self.cli_db_path) as cli:
                    cli.execute("UPDATE session SET directory=? WHERE id=?", (str(self.workspace) + os.sep + ".", session_id))
            return original(*args)

        with patch.object(adapter, "_native_session", side_effect=change_identity), self.assertRaisesRegex(DesktopIndexError, "身份在登记期间变化"):
            adapter.register(session_id, title="Must not create a partial project")
        self.assertEqual(self.query("SELECT * FROM projects"), [])
        self.assertEqual(self.query("SELECT * FROM project_roots"), [])
        self.assertEqual(self.query("SELECT * FROM tasks"), [])

    def test_owned_mode_alignment_updates_only_mode_with_backup_and_exact_task_marker(self):
        session_id = "sess_owned_mode"
        self.seed_cli(session_id, mode="edit")
        adapter = self.adapter()
        adapter.register(session_id, title="Original owned task", task_path=self.task, mode="edit")
        with fixture_database(self.db_path) as index:
            index.execute("UPDATE tasks SET pinned=1,title='User renamed title',sort_order=17,unread_at=321 WHERE task_id=?", (session_id,))
        with fixture_database(self.cli_db_path) as cli:
            cli.execute("UPDATE session_entry SET data=? WHERE session_id=?", (json.dumps({"mode": "yolo", "planEnabled": False}), session_id))
        before = self.query("SELECT * FROM tasks")[0]
        with self.assertRaisesRegex(DesktopIndexError, "模式"):
            adapter.register(session_id, title="Do not replace", task_path=self.task)
        result = adapter.register(session_id, title="Do not replace", task_path=self.task, mode="yolo", update_owned_mode=True)
        self.assertTrue(result["modeUpdated"])
        self.assertFalse(result["created"])
        self.assertEqual(result["mode"], "yolo")
        self.assertEqual(self.query("SELECT * FROM tasks"), [{**before, "mode": "yolo"}])
        with fixture_database(result["backupPath"]) as backup:
            backup.row_factory = sqlite3.Row
            self.assertEqual(dict(backup.execute("SELECT * FROM tasks WHERE task_id=?", (session_id,)).fetchone()), before)
        repeated = adapter.register(session_id, title="Do not replace", task_path=self.task, update_owned_mode=True)
        self.assertFalse(repeated["modeUpdated"])
        self.assertIsNone(repeated["backupPath"])

    def test_foreign_missing_or_mismatched_task_marker_never_allows_mode_alignment(self):
        for number, marker in enumerate((None, {}, {"schemaVersion": 1, "sessionId": "sess_another", "taskPath": "task.md"}, {"schemaVersion": 1, "sessionId": "SELF", "taskPath": "another-task.md"})):
            session_id = "sess_foreign_mode_" + str(number)
            self.seed_cli(session_id, mode="edit")
            adapter = self.adapter()
            adapter.register(session_id, title="Existing task", task_path=self.task, mode="edit")
            metadata = {} if marker is None else {"cdpControlPicker": {**marker, **({"sessionId": session_id} if marker.get("sessionId") == "SELF" else {})}}
            with fixture_database(self.db_path) as index:
                index.execute("UPDATE tasks SET meta_json=? WHERE task_id=?", (json.dumps(metadata), session_id))
            with fixture_database(self.cli_db_path) as cli:
                cli.execute("UPDATE session_entry SET data=? WHERE session_id=?", (json.dumps({"mode": "yolo", "planEnabled": False}), session_id))
            before = self.query("SELECT * FROM tasks")
            with self.subTest(marker=marker), self.assertRaisesRegex(DesktopIndexError, "不能确认"):
                adapter.register(session_id, title="Must not modify", task_path=self.task, update_owned_mode=True)
            self.assertEqual(self.query("SELECT * FROM tasks"), before)


if __name__ == "__main__":
    unittest.main()
