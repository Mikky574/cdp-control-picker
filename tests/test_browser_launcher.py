"""Fake HTTP/process tests: never launch a real browser or touch its settings."""
import io
import ctypes
import ipaddress
import json
from pathlib import Path
import sys
import tempfile
import socket
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
        # Launch tests never cross into native UI or inspect the real desktop.
        self.show_patch = patch.object(launcher, "show_existing", return_value={
            "visible": True, "foreground": True, "visibilityStatus": "foreground", "message": "Fixture Chrome shown"})
        self.show = self.show_patch.start()
        self.addCleanup(self.show_patch.stop)

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
        self.show.assert_called_once_with("http://127.0.0.1:9222", target_id=None)

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
        self.show.assert_called_once_with("http://127.0.0.1:9222", target_id=None)

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

    def test_portable_is_fallback_after_system_chrome_and_requires_complete_files(self):
        portable = self.base / 'optional-dependencies' / 'chrome-win64'
        portable.mkdir(parents=True)
        (portable / 'chrome.exe').touch()
        with patch.object(launcher, 'optional_chrome_directory', return_value=portable), \
                patch.object(launcher.shutil, 'which', return_value=None), \
                patch.object(launcher, '_registry_candidates', return_value=[]), \
                patch.dict(launcher.os.environ, {}, clear=True):
            with self.assertRaisesRegex(launcher.BrowserLaunchError, 'Prepare-Chrome'):
                launcher.find_chrome()
            for filename in ('chrome.dll', 'icudtl.dat', 'resources.pak'):
                (portable / filename).touch()
            self.assertEqual(launcher.find_chrome(), portable / 'chrome.exe')
            with patch.object(launcher, '_registry_candidates', return_value=[str(self.chrome)]):
                self.assertEqual(launcher.find_chrome(), self.chrome.resolve())

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


def window(hwnd=11, pid=100, title="Fixture - Google Chrome", **overrides):
    return {"hwnd": hwnd, "pid": pid, "className": "Chrome_WidgetWin_1", "title": title,
            "owner": 0, "visible": True, "minimized": False, "foreground": False, **overrides}


class FakeDesktop:
    def __init__(self):
        self.listeners = {100}
        self.processes = {100: {"pid": 100, "image": r"C:\Chrome\chrome.exe", "created": 100, "parentPid": 0}}
        self.process_reads = []
        self.window_reads = []
        self.entries = [window()]
        self.activated = []
        self.actual = {"visible": True, "foreground": True}

    def listener_pids(self, host, port):
        self.listener_query = (host, port)
        return self.listeners

    def process(self, pid):
        self.process_reads.append(pid)
        if pid not in self.processes:raise OSError("fixture process unavailable")
        return self.processes[pid]

    def windows(self, pids):
        self.window_reads.append(pids)
        return self.entries  # Wrapper must still reject unrelated process IDs.

    def activate(self, hwnd, pid):
        self.activated.append((hwnd, pid))
        return self.actual


class ShowExistingTests(unittest.TestCase):
    def setUp(self):
        self.desktop = FakeDesktop()
        for name, value in (("_windows_supported", True), ("_read_version", VERSION), ("_WindowsDesktop", self.desktop)):
            patcher = patch.object(launcher, name, return_value=value)
            setattr(self, name + "_mock", patcher.start())
            self.addCleanup(patcher.stop)
        self.target_patch = patch.object(launcher, "_prepare_target", return_value={"title": "Wanted", "cdpWindowId": 4})
        self.prepare = self.target_patch.start()
        self.addCleanup(self.target_patch.stop)

    def test_reused_launch_shows_same_listener_without_spawn_or_profile_claim(self):
        with patch.object(launcher.subprocess, "Popen") as spawn, patch.object(launcher, "find_chrome") as find:
            result = launcher.launch_or_reuse("http://localhost:45678", "unused-profile", target_id="selected-tab")
        self.assertEqual(result["status"], "reused")
        self.assertEqual(self.desktop.listener_query, ("127.0.0.1", 45678))
        self.assertEqual(self.desktop.activated, [(11, 100)])
        self.assertEqual(result["listenerPid"], 100)
        self.assertEqual(result["windowPid"], 100)
        self.assertTrue(result["visible"])
        self.assertTrue(result["foreground"])
        self.assertIsNone(result["profilePath"])
        self.prepare.assert_called_once_with("http://127.0.0.1:45678", "selected-tab")
        spawn.assert_not_called();find.assert_not_called()

    def test_multiple_instances_and_windows_select_only_exact_listener_and_target(self):
        self.desktop.entries = [window(99, 999, "Wanted - Google Chrome", foreground=True),
                                window(11, 100, "Wanted - Google Chrome"), window(12, 100, "Other - Google Chrome")]
        result = launcher.show_existing("http://127.0.0.1:9222", "target-wanted")
        self.assertTrue(result["foreground"])
        self.assertEqual(self.desktop.activated, [(11, 100)])
        self.assertEqual(self.desktop.process_reads, [100])
        self.assertNotIn("Wanted", result["message"])

    def test_listener_window_wins_over_another_chrome_parent_instance(self):
        self.desktop.processes[100]["parentPid"] = 50
        self.desktop.processes[50] = {"pid": 50, "image": r"C:\Chrome\chrome.exe", "created": 50, "parentPid": 0}
        self.desktop.entries = [window(50, 50, foreground=True), window(11, 100)]
        launcher.show_existing("http://127.0.0.1:9222")
        self.assertEqual(self.desktop.activated, [(11, 100)])

    def test_only_nearest_verified_chrome_parent_can_own_the_browser_window(self):
        self.desktop.processes[100]["parentPid"] = 50
        self.desktop.processes[50] = {"pid": 50, "image": r"C:\Chrome\chrome.exe", "created": 50, "parentPid": 5}
        self.desktop.processes[5] = {"pid": 5, "image": r"C:\Chrome\chrome.exe", "created": 5, "parentPid": 0}
        self.desktop.entries = [window(5, 5, foreground=True), window(50, 50)]
        result = launcher.show_existing("http://127.0.0.1:9222")
        self.assertEqual(self.desktop.activated, [(50, 50)])
        self.assertEqual(result["windowPid"], 50)

    def test_recycled_parent_pid_is_not_accepted_as_browser_owner(self):
        self.desktop.processes[100]["parentPid"] = 50
        self.desktop.processes[50] = {"pid": 50, "image": r"C:\Chrome\chrome.exe", "created": 200, "parentPid": 0}
        self.desktop.entries = [window(50, 50)]
        result = launcher.show_existing("http://127.0.0.1:9222")
        self.assertFalse(result["visible"])
        self.assertEqual(self.desktop.activated, [])

    def test_foreground_denial_reports_visible_only_and_never_restarts(self):
        self.desktop.actual = {"visible": True, "foreground": False}
        with patch.object(launcher.subprocess, "Popen") as spawn:
            result = launcher.show_existing("http://127.0.0.1:9222")
        self.assertTrue(result["visible"])
        self.assertFalse(result["foreground"])
        self.assertEqual(result["visibilityStatus"], "visible")
        self.assertIn("Windows 未允许", result["message"])
        spawn.assert_not_called()

    def test_no_window_ambiguous_caption_nonchrome_or_multiple_listeners_never_activate(self):
        scenarios = ("no-window", "duplicate-caption", "not-chrome", "multiple-listeners")
        for scenario in scenarios:
            self.desktop = FakeDesktop()
            self._WindowsDesktop_mock.return_value = self.desktop
            if scenario == "no-window":self.desktop.entries = []
            elif scenario == "duplicate-caption":self.desktop.entries = [window(1, title="Wanted - Google Chrome"), window(2, title="Wanted - Google Chrome")]
            elif scenario == "not-chrome":self.desktop.processes[100]["image"] = r"C:\Tools\ssh.exe"
            else:self.desktop.listeners = {100, 200}
            with self.subTest(scenario=scenario), patch.object(launcher.subprocess, "Popen") as spawn:
                result = launcher.show_existing("http://127.0.0.1:9222", "target-wanted")
                self.assertFalse(result["foreground"])
                self.assertFalse(result["visible"])
                self.assertEqual(self.desktop.activated, [])
                spawn.assert_not_called()

    def test_target_failure_does_not_activate_replacement_window(self):
        self.prepare.side_effect = launcher.BrowserLaunchError("Selected target is gone")
        result = launcher.show_existing("http://127.0.0.1:9222", "missing-target")
        self.assertFalse(result["visible"])
        self.assertEqual(self.desktop.activated, [])
        self.assertEqual(self.desktop.window_reads, [])

    def test_unsupported_platform_does_not_inspect_or_activate_native_desktop(self):
        self._windows_supported_mock.return_value = False
        result = launcher.show_existing("http://127.0.0.1:9222")
        self.assertFalse(result["foreground"])
        self._WindowsDesktop_mock.assert_not_called()
        self._read_version_mock.assert_not_called()


class NativeVisibilityFakeTests(unittest.TestCase):
    def test_minimized_window_is_restored_and_success_is_read_back(self):
        native = launcher._WindowsDesktop.__new__(launcher._WindowsDesktop)
        native.user = Mock()
        native.window_state = Mock(side_effect=[window(minimized=True, visible=False), window(foreground=True)])
        result = native.activate(11, 100)
        native.user.ShowWindowAsync.assert_called_once_with(11, 9)
        native.user.SetForegroundWindow.assert_called_once_with(11)
        self.assertEqual(result, {"visible": True, "foreground": True})

    def test_native_false_foreground_return_is_never_treated_as_success(self):
        native = launcher._WindowsDesktop.__new__(launcher._WindowsDesktop)
        native.user = Mock()
        native.user.SetForegroundWindow.return_value = False
        native.window_state = Mock(return_value=window())
        with patch.object(launcher, "SHOW_TIMEOUT_SECONDS", 0):
            result = native.activate(11, 100)
        self.assertEqual(result, {"visible": True, "foreground": False})
        native.user.ShowWindowAsync.assert_called_once_with(11, 5)

    def test_already_maximized_window_uses_show_not_restore(self):
        native = launcher._WindowsDesktop.__new__(launcher._WindowsDesktop)
        native.user = Mock()
        native.window_state = Mock(side_effect=[window(maximized=True), window(maximized=True, foreground=True)])
        result = native.activate(11, 100)
        native.user.ShowWindowAsync.assert_called_once_with(11, 5)
        self.assertTrue(result["foreground"])

    def test_changed_hwnd_owner_blocks_all_native_activation_calls(self):
        native = launcher._WindowsDesktop.__new__(launcher._WindowsDesktop)
        native.user = Mock()
        native.window_state = Mock(return_value=window(pid=999))
        with self.assertRaises(OSError):native.activate(11, 100)
        native.user.ShowWindowAsync.assert_not_called()
        native.user.SetForegroundWindow.assert_not_called()

    def test_tcp_owner_filter_uses_exact_address_port_and_network_byte_order(self):
        rows = []
        for address, port, pid in (("127.0.0.1", 9222, 100), ("127.0.0.2", 9222, 200), ("127.0.0.1", 9333, 300)):
            row = launcher._Tcp4Row()
            row.state, row.pid = 2, pid
            row.localAddress = int.from_bytes(ipaddress.ip_address(address).packed, "little")
            row.localPort = socket.htons(port)
            rows.append(row)
        payload = len(rows).to_bytes(4, "little") + bytes(launcher._Tcp4Table.table.offset - 4) + b"".join(bytes(row) for row in rows)

        def tcp_table(buffer, size, _order, family, table_class, _reserved):
            self.assertEqual((family, table_class), (2, 3))
            size._obj.value = len(payload)
            if buffer is None:return 122
            ctypes.memmove(buffer, payload, len(payload))
            return 0

        native = launcher._WindowsDesktop.__new__(launcher._WindowsDesktop)
        native.ip = Mock(GetExtendedTcpTable=Mock(side_effect=tcp_table))
        self.assertEqual(native.listener_pids("127.0.0.1", 9222), {100})

    def test_tcp_table_uses_declared_first_entry_padding_instead_of_fixed_four_bytes(self):
        class PaddedTable(ctypes.Structure):
            _fields_ = [("count", ctypes.c_uint32), ("alignmentPadding", ctypes.c_uint32), ("table", launcher._Tcp4Row * 1)]
        row = launcher._Tcp4Row()
        row.state, row.pid, row.localPort = 2, 100, socket.htons(9222)
        row.localAddress = int.from_bytes(ipaddress.ip_address("127.0.0.1").packed, "little")
        payload = (1).to_bytes(4, "little") + bytes(PaddedTable.table.offset - 4) + bytes(row)

        def tcp_table(buffer, size, *_args):
            size._obj.value = len(payload)
            if buffer is None:return 122
            ctypes.memmove(buffer, payload, len(payload))
            return 0

        native = launcher._WindowsDesktop.__new__(launcher._WindowsDesktop)
        native.ip = Mock(GetExtendedTcpTable=Mock(side_effect=tcp_table))
        with patch.object(launcher, "_Tcp4Table", PaddedTable):
            self.assertEqual(native.listener_pids("127.0.0.1", 9222), {100})

    def test_cdp_restores_only_selected_minimized_target_and_closes_transport_only(self):
        client = Mock()
        client.list_pages.return_value = [{"id": "target-two", "title": "Wanted", "type": "page"}]
        client.call.side_effect = lambda method, params=None: {"windowId": 7, "bounds": {"windowState": "minimized"}} if method == "Browser.getWindowForTarget" else {}
        with patch("cdp_client.CDPClient", return_value=client):
            result = launcher._prepare_target("http://127.0.0.1:9222", "target-two")
        self.assertEqual(result["cdpWindowId"], 7)
        client.call.assert_any_call("Browser.setWindowBounds", {"windowId": 7, "bounds": {"windowState": "normal"}})
        client.call.assert_any_call("Page.bringToFront")
        self.assertFalse(any(call.args[0] in {"Browser.close", "Page.close", "Target.createTarget"} for call in client.call.call_args_list))
        client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
