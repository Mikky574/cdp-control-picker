"""CLI launch/protocol fixtures; never starts ZCode or calls a model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zcode_runner import RunnerError, ZCodeRunner, command_environment, resolve_command, _json_result


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="picker-launch-test-")
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace with spaces & text"
        self.workspace.mkdir()
        self.task = self.workspace / "task.md"
        self.task.write_text("Fixture task; no model call.", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def desktop(self, directory=None):
        directory = directory or self.root / "Installed ZCode with spaces"
        executable = directory / "ZCode.exe"
        bundle = directory / "resources" / "glm" / "zcode.cjs"
        builtin = directory / "resources" / "config" / "provider" / "zcode-builtin.json"
        bundle.parent.mkdir(parents=True)
        builtin.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture-not-an-executable")
        bundle.write_text("// Public CLI asset fixture", encoding="utf-8")
        builtin.write_text("{}", encoding="utf-8")
        return executable.resolve(), bundle.resolve(), builtin.resolve()

    def run_fake(self, stdout, stderr=""):
        script = self.root / "fake CLI.py"
        script.write_text("import sys\nsys.stdout.write(" + repr(stdout) + ")\nsys.stderr.write(" + repr(stderr) + ")\n", encoding="utf-8")
        runner = ZCodeRunner([sys.executable, str(script)], self.workspace)
        state = runner.wait(runner.start(self.task), 10)
        self.assertIsNotNone(state)
        return state

    def test_selected_desktop_uses_its_adjacent_cli_and_process_environment(self):
        executable, bundle, builtin = self.desktop()
        self.assertEqual(resolve_command(executable), [str(executable), str(bundle)])
        self.assertEqual(resolve_command([executable]), [str(executable), str(bundle)])
        self.assertEqual(resolve_command([executable, bundle]), [str(executable), str(bundle)])
        inherited = {"PATH": "fixture-path", "ELECTRON_RUN_AS_NODE": "0"}
        prepared = command_environment(resolve_command(executable), inherited)
        self.assertEqual(prepared["ELECTRON_RUN_AS_NODE"], "1")
        self.assertEqual(prepared["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"], str(builtin))
        self.assertEqual(prepared["ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE"], str(builtin))
        self.assertNotIn("ZCODE_PERSONAL_PROVIDER_CONFIG_FILE", prepared)
        self.assertEqual(inherited, {"PATH": "fixture-path", "ELECTRON_RUN_AS_NODE": "0"})

    def test_default_prefers_installed_desktop_over_path_cli(self):
        appdata = self.root / "Local App Data"
        executable, bundle, _ = self.desktop(appdata / "Programs" / "ZCode")
        other = self.root / "other-zcode.exe"
        other.write_bytes(b"fixture")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(appdata)}, clear=True), patch("zcode_runner.shutil.which", return_value=str(other)):
            self.assertEqual(resolve_command(), [str(executable), str(bundle)])

    def test_explicit_provider_environment_is_preserved(self):
        executable, _, _ = self.desktop()
        inherited = {"ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": "explicit-config", "ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE": "explicit-bundle"}
        prepared = command_environment(resolve_command(executable), inherited)
        self.assertEqual(prepared["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"], "explicit-config")
        self.assertEqual(prepared["ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE"], "explicit-bundle")

    def test_desktop_launch_passes_argument_array_and_env_to_popen(self):
        executable, bundle, _ = self.desktop()
        fixture = self.root / "fake process.py"
        fixture.write_text("import json\nprint(json.dumps({'sessionId':'sess_fixture','response':'draft'}))\n", encoding="utf-8")
        actual_popen = subprocess.Popen
        calls = []

        def launch(args, **options):
            calls.append((args, options))
            return actual_popen([sys.executable, str(fixture)], **options)

        with patch("zcode_runner.subprocess.Popen", side_effect=launch):
            runner = ZCodeRunner(executable, self.workspace)
            state = runner.wait(runner.start(self.task), 10)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(len(calls), 1)
        args, options = calls[0]
        self.assertEqual(args[:2], [str(executable), str(bundle)])
        self.assertEqual(args[args.index("-p") + 1], f"读取“{self.task}”，按其中说明整理能力草稿。")
        self.assertEqual(options["env"]["ELECTRON_RUN_AS_NODE"], "1")
        self.assertEqual(options["cwd"], str(self.workspace))
        self.assertFalse(options["shell"])
        self.assertEqual(args[args.index("--mode") + 1], "edit")
        self.assertEqual(state["mode"], "edit")
        self.assertEqual(json.loads(Path(state["runPath"]).read_text(encoding="utf-8"))["mode"], "edit")
        self.assertNotIn("yolo", args)
        self.assertNotIn("--resume", args)
        self.assertNotIn("resumeSessionId", state)
        self.assertEqual(json.loads(Path(state["runPath"]).read_text(encoding="utf-8"))["prompt"], args[args.index("-p") + 1])

    def test_explicit_yolo_prompt_and_resume_preserve_argv_and_persist_same_session(self):
        received = self.workspace / "received argv.jsonl"
        script = self.root / "fake resume CLI.py"
        script.write_text(
            "import json,pathlib,sys\n"
            "args=sys.argv[1:]\n"
            "with pathlib.Path(" + repr(str(received)) + ").open('a',encoding='utf-8') as output: output.write(json.dumps(args,ensure_ascii=False)+'\\n')\n"
            "session=args[args.index('--resume')+1] if '--resume' in args else 'sess_fixture-01'\n"
            "print(json.dumps({'sessionId':session,'response':'fixture draft'}))\n",
            encoding="utf-8",
        )
        runner = ZCodeRunner([sys.executable, str(script)], self.workspace, mode="yolo")
        first_prompt = '读取任务；保留 "引号"、&、$(fixture)、`文本`。'
        second_prompt = "只补充同一份任务的待验证项，不连接网页。"
        first = runner.wait(runner.start(self.task, prompt=first_prompt), 10)
        self.assertEqual(first["status"], "completed")
        second = runner.wait(runner.start(self.task, prompt=second_prompt, resume=first["sessionId"]), 10)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["sessionId"], first["sessionId"])
        self.assertNotEqual(first["runId"], second["runId"])
        arguments = [json.loads(line) for line in received.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(arguments), 2)
        self.assertNotIn("--resume", arguments[0])
        self.assertEqual(arguments[0][arguments[0].index("-p") + 1], first_prompt)
        self.assertEqual(arguments[1][arguments[1].index("--resume") + 1], first["sessionId"])
        self.assertEqual(arguments[1][arguments[1].index("-p") + 1], second_prompt)
        self.assertEqual(arguments[1].count("--resume"), 1)
        for args in arguments:
            self.assertEqual(args[args.index("--mode") + 1], "yolo")
            self.assertEqual(args.count("--mode"), 1)
        for state, expected_prompt in ((first, first_prompt), (second, second_prompt)):
            persisted = json.loads(Path(state["runPath"]).read_text(encoding="utf-8"))
            self.assertEqual(persisted["prompt"], expected_prompt)
            self.assertEqual(state["mode"], "yolo")
            self.assertEqual(persisted["mode"], "yolo")
        self.assertNotIn("resumeSessionId", first)
        self.assertEqual(second["resumeSessionId"], first["sessionId"])
        self.assertEqual(json.loads(Path(second["runPath"]).read_text(encoding="utf-8"))["resumeSessionId"], first["sessionId"])

    def test_mode_validation_rejects_unknown_values_before_launch(self):
        for mode in ("edit", "plan", "build", "guarded", "yolo"):
            with self.subTest(mode=mode):
                self.assertEqual(ZCodeRunner([sys.executable], self.workspace, mode=mode).mode, mode)
        with patch("zcode_runner.subprocess.Popen") as launch:
            for mode in (None, 0, [], {}, "", " ", "YOLO", " edit", "--mode", "yolo --model fixture"):
                with self.subTest(mode=mode), self.assertRaises(RunnerError):
                    ZCodeRunner([sys.executable], self.workspace, mode=mode)
        launch.assert_not_called()
        self.assertFalse((self.task.parent / "runs").exists())

    def test_invalid_prompt_or_resume_does_not_start_or_register_a_run(self):
        runner = ZCodeRunner([sys.executable], self.workspace)
        invalid_prompts = ("", "   ", 0, "line\nbreak", "line\rbreak", "tab\ttext", "nul\x00text", "escape\x1btext", "delete\x7ftext", "c1\x85text", "paragraph\u2029text")
        invalid_resumes = ("", " ", 0, "--model", "session with spaces", "session\nnext", "../session", "session/child", "session&other", "s" * 201)
        with patch("zcode_runner.subprocess.Popen") as launch:
            for prompt in invalid_prompts:
                with self.subTest(prompt=prompt), self.assertRaises(RunnerError):
                    runner.start(self.task, prompt=prompt)
            for resume in invalid_resumes:
                with self.subTest(resume=resume), self.assertRaises(RunnerError):
                    runner.start(self.task, resume=resume)
        launch.assert_not_called()
        self.assertFalse((self.task.parent / "runs").exists())

    def test_plain_executable_and_fake_argument_vector_stay_supported(self):
        executable = self.root / "standalone-cli.exe"
        executable.write_bytes(b"fixture")
        self.assertEqual(resolve_command(executable), [str(executable.resolve())])
        command = [sys.executable, "fixture script.py", "--fixture-argument"]
        self.assertEqual(resolve_command(command), command)
        self.assertNotIn("ELECTRON_RUN_AS_NODE", command_environment(command, {}))

    def test_complete_pretty_json_is_parsed_before_redacting_logs(self):
        response = "标注 {位置}，api_key=fixture-sensitive-value"
        state = self.run_fake(json.dumps({"sessionId": "sess_fixture", "response": response}, ensure_ascii=False, indent=2))
        self.assertEqual(state["status"], "completed")
        self.assertNotIn("fixture-sensitive-value", state["response"])
        self.assertNotIn("fixture-sensitive-value", Path(state["stdoutPath"]).read_text(encoding="utf-8"))

    def test_large_single_json_object_is_not_cut_at_old_line_limit(self):
        response = "x" * 300000
        state = self.run_fake(json.dumps({"sessionId": "sess_fixture", "response": response}))
        self.assertEqual(state["status"], "completed")
        self.assertFalse(state["stdoutTruncated"])
        self.assertGreater(Path(state["stdoutPath"]).stat().st_size, 300000)

    def test_electron_only_logs_exit_zero_are_unknown_and_retained(self):
        stdout = "[Electron] renderer ready\n[Electron] closing\n"
        stderr = "Electron diagnostic fixture\n"
        state = self.run_fake(stdout, stderr)
        self.assertEqual((state["status"], state["exitCode"]), ("unknown", 0))
        self.assertEqual(Path(state["stdoutPath"]).read_text(encoding="utf-8"), stdout)
        self.assertEqual(Path(state["stderrPath"]).read_text(encoding="utf-8"), stderr)

    def test_json_must_be_a_complete_summary_not_nested_or_log_fragment(self):
        summary = json.dumps({"sessionId": "sess_fixture", "response": "ready"})
        for output in ("log\n" + summary, summary + "\n" + summary, json.dumps({"tool": {"response": "not a summary"}})):
            self.assertIsNone(_json_result(output))
        self.assertEqual(_json_result("\ufeff\n" + summary + "\n")["sessionId"], "sess_fixture")


if __name__ == "__main__":
    unittest.main()
