"""Fake HTTP/process tests: never launch a real browser or touch its settings."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_launcher as launcher


VERSION = {
    "Browser": "Chrome/150.0.0.0", "Protocol-Version": "1.3",
    "User-Agent": "Mozilla/5.0 Chrome/150.0.0.0",
    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/example-id",
}


class LauncherTests(unittest.TestCase):
    def setUp(self):
        work = Path.cwd() / "work"
        work.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="browser-launcher-test-", dir=work)
        self.base = Path(self.temp.name)
        self.chrome = self.base / "Google Chrome" / "chrome.exe"
        self.chrome.parent.mkdir()
        self.chrome.write_bytes(b"fake executable; do not run")
        self.profile = self.base / "AI Chrome persistent profile"

    def tearDown(self):
        self.temp.cleanup()

    def test_existing_cdp_reuses_before_discovery_and_never_claims_profile(self):
        with patch.object(launcher, "_read_version", return_value=VERSION), patch.object(launcher, "find_chrome") as find, patch.object(launcher.subprocess, "Popen") as spawn:
            result = launcher.launch_or_reuse("http://localhost:9222/", self.profile, "missing-chrome.exe")
        self.assertEqual(result["status"], "reused")
        self.assertEqual(result["endpoint"], "http://127.0.0.1:9222")
        self.assertIsNone(result["profilePath"])
        self.assertIsNone(result["chromePath"])
        self.assertTrue(any("尚未核对" in note for note in result["notes"]))
        self.assertFalse(self.profile.exists())
        find.assert_not_called()
        spawn.assert_not_called()

    def test_external_or_nonbase_endpoints_rejected_without_http(self):
        invalid = ["http://example.com:9222", "ws://127.0.0.1:9222", "https://127.0.0.1:9222", "http://u:p@127.0.0.1:9222", "http://@127.0.0.1:9222", "http://127.0.0.1:9222/json/version", "http://127.0.0.1:9222?x=y", "http://127.0.0.1:0", "http://127.0.0.1:65536"]
        with patch.object(launcher, "_read_version") as read, patch.object(launcher.subprocess, "Popen") as spawn:
            for endpoint in invalid:
                with self.subTest(endpoint=endpoint), self.assertRaises(launcher.BrowserLaunchError):
                    launcher.launch_or_reuse(endpoint, self.profile)
        read.assert_not_called()
        spawn.assert_not_called()

    def test_http_non_cdp_port_is_not_restarted(self):
        with patch.object(launcher, "_read_version", return_value={"service": "another local app"}), patch.object(launcher.subprocess, "Popen") as spawn:
            with self.assertRaises(launcher.BrowserLaunchError) as caught:
                launcher.launch_or_reuse("http://127.0.0.1:9222", self.profile)
        self.assertEqual(caught.exception.details["status"], "occupied")
        spawn.assert_not_called()
        self.assertFalse(self.profile.exists())

    def test_unresponsive_occupied_tcp_port_is_not_taken_over(self):
        with patch.object(launcher, "_read_version", side_effect=URLError("timed out")), patch.object(launcher, "_port_accepts", return_value=True), patch.object(launcher.subprocess, "Popen") as spawn:
            with self.assertRaises(launcher.BrowserLaunchError):
                launcher.launch_or_reuse("http://127.0.0.1:9222", self.profile)
        spawn.assert_not_called()

    def test_start_arguments_keep_profile_and_do_not_use_shell(self):
        process = Mock(pid=12345)
        with patch.object(launcher, "_read_version", side_effect=[URLError("refused"), VERSION]), patch.object(launcher, "_port_accepts", return_value=False), patch.object(launcher.subprocess, "Popen", return_value=process) as spawn:
            result = launcher.launch_or_reuse("http://127.0.0.1:9222", self.profile, self.chrome)
        self.assertEqual(result["status"], "started")
        self.assertEqual(result["pid"], 12345)
        self.assertEqual(result["profilePath"], str(self.profile.resolve()))
        args = spawn.call_args.args[0]
        self.assertEqual(args[0], str(self.chrome.resolve()))
        self.assertIn("--user-data-dir=" + str(self.profile.resolve()), args)
        self.assertIn("--remote-debugging-address=127.0.0.1", args)
        self.assertIn("--remote-debugging-port=9222", args)
        self.assertIn("--no-first-run", args)
        self.assertIn("--no-default-browser-check", args)
        self.assertFalse(any("remote-allow-origin" in arg for arg in args))
        self.assertFalse(spawn.call_args.kwargs["shell"])
        self.assertNotIn("creationflags", spawn.call_args.kwargs)
        self.assertTrue(self.profile.is_dir())
        process.terminate.assert_not_called()

    def test_new_ipv6_request_returns_actual_ipv4_listener(self):
        with patch.object(launcher, "_read_version", side_effect=[URLError("refused"), VERSION]), patch.object(launcher, "_port_accepts", return_value=False), patch.object(launcher.subprocess, "Popen", return_value=Mock(pid=2)):
            result = launcher.launch_or_reuse("http://[::1]:9222", self.profile, self.chrome)
        self.assertEqual(result["endpoint"], "http://127.0.0.1:9222")
        self.assertTrue(any("IPv4" in note for note in result["notes"]))

    def test_default_chrome_profile_is_rejected(self):
        local = self.base / "local-app-data"
        default = local / "Google" / "Chrome" / "User Data" / "Default"
        with patch.dict(launcher.os.environ, {"LOCALAPPDATA": str(local)}), patch.object(launcher, "_read_version", side_effect=URLError("refused")), patch.object(launcher, "_port_accepts", return_value=False), patch.object(launcher.subprocess, "Popen") as spawn:
            with self.assertRaises(launcher.BrowserLaunchError):
                launcher.launch_or_reuse("http://127.0.0.1:9222", default, self.chrome)
        spawn.assert_not_called()
        self.assertFalse(default.exists())

    def test_timeout_keeps_browser_and_does_not_retry_launch(self):
        process = Mock(pid=4)
        with patch.object(launcher, "START_TIMEOUT_SECONDS", 0.02), patch.object(launcher, "POLL_INTERVAL_SECONDS", 0.005), patch.object(launcher, "_read_version", side_effect=URLError("refused")), patch.object(launcher, "_port_accepts", return_value=False), patch.object(launcher.subprocess, "Popen", return_value=process) as spawn:
            with self.assertRaises(launcher.BrowserLaunchError) as caught:
                launcher.launch_or_reuse("http://127.0.0.1:9222", self.profile, self.chrome)
        self.assertEqual(caught.exception.details["status"], "started_unconfirmed")
        self.assertEqual(caught.exception.details["pid"], 4)
        spawn.assert_called_once()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_failed_spawn_is_not_repeated(self):
        with patch.object(launcher, "_read_version", side_effect=URLError("refused")), patch.object(launcher, "_port_accepts", return_value=False), patch.object(launcher.subprocess, "Popen", side_effect=OSError("access denied")) as spawn:
            with self.assertRaises(launcher.BrowserLaunchError) as caught:
                launcher.launch_or_reuse("http://127.0.0.1:9222", self.profile, self.chrome)
        self.assertEqual(caught.exception.details["profilePath"], str(self.profile.resolve()))
        spawn.assert_called_once()

    def test_discovery_uses_actual_path_and_rejects_edge(self):
        with patch.object(launcher.shutil, "which", side_effect=lambda name: str(self.chrome) if name == "chrome.exe" else None), patch.object(launcher, "_registry_candidates", return_value=[]):
            self.assertEqual(launcher.find_chrome(), self.chrome.resolve())
        edge = self.base / "msedge.exe"
        edge.write_bytes(b"fixture")
        with self.assertRaises(launcher.BrowserLaunchError):
            launcher.find_chrome(edge)

    def test_registry_candidate_is_supported_without_executing_it(self):
        with patch.object(launcher.shutil, "which", return_value=None), patch.object(launcher, "_registry_candidates", return_value=[str(self.chrome)]):
            self.assertEqual(launcher.find_chrome(), self.chrome.resolve())

    def test_discovery_request_bypasses_proxies_and_redirects(self):
        response = Mock()
        response.__enter__ = Mock(return_value=io.BytesIO(json.dumps(VERSION).encode()))
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(launcher, "build_opener", return_value=opener) as build:
            self.assertEqual(launcher._read_version("http://127.0.0.1:9222", 0.5), VERSION)
        handlers = build.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], launcher._NoRedirect)
        with self.assertRaises(HTTPError):
            handlers[1].redirect_request(Mock(full_url="http://127.0.0.1:9222/json/version"), None, 302, "redirect", {}, "http://example.com/")

    def test_version_must_identify_chrome_browser_endpoint(self):
        self.assertTrue(launcher._is_chrome_cdp(VERSION))
        for update in [{"Browser": "Edg/150"}, {"User-Agent": "Chrome/150 Edg/150"}, {"webSocketDebuggerUrl": "ws://example.com/devtools/browser/id"}, {"webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/id"}]:
            self.assertFalse(launcher._is_chrome_cdp({**VERSION, **update}))


if __name__ == "__main__":
    unittest.main()
