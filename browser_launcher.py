"""Launch a dedicated visible Google Chrome, or reuse an existing loopback CDP.

No browser/profile is copied, closed or reconfigured. Existing CDP profile
identity cannot be established from /json/version and is never claimed here.
"""
from __future__ import annotations

import ipaddress
import json
import ctypes
from ctypes import wintypes
import ntpath
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


START_TIMEOUT_SECONDS = 15.0
POLL_INTERVAL_SECONDS = 0.25
SHOW_TIMEOUT_SECONDS = 0.75


class BrowserLaunchError(RuntimeError):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


def _parse_endpoint(endpoint):
    try:
        parsed = urlsplit(str(endpoint).strip())
        host = parsed.hostname
        port = parsed.port if parsed.port is not None else 9222
        if parsed.scheme != "http" or not host or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError
        if host.lower() == "localhost":
            host = "127.0.0.1"
        address = ipaddress.ip_address(host)
        if not address.is_loopback or not 1 <= port <= 65535:
            raise ValueError
    except (ValueError, TypeError):
        raise BrowserLaunchError("请填写本机 HTTP CDP 入口，例如 http://127.0.0.1:9222；不接受远程地址、凭据或额外路径。") from None
    authority = "[" + host + "]" if ":" in host else host
    return host, port, "http://" + authority + ":" + str(port)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise HTTPError(request.full_url, code, "CDP discovery redirects are not followed", headers, response)


def _read_version(endpoint, timeout):
    """Intentionally bypass system HTTP proxies for a validated loopback URL."""
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    request = Request(endpoint + "/json/version", headers={"Accept": "application/json"})
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("version response too large")
    return json.loads(raw.decode("utf-8"))


def _is_chrome_cdp(version):
    if not isinstance(version, dict):
        return False
    browser = version.get("Browser", "")
    user_agent = version.get("User-Agent", "")
    if not isinstance(browser, str) or not browser.startswith(("Chrome/", "HeadlessChrome/")) or "Edg/" in str(user_agent) or not version.get("Protocol-Version"):
        return False
    try:
        websocket_url = urlsplit(version.get("webSocketDebuggerUrl", ""))
        host = websocket_url.hostname
        local = host == "localhost" or bool(host and ipaddress.ip_address(host).is_loopback)
        return websocket_url.scheme == "ws" and local and websocket_url.path.startswith("/devtools/browser/")
    except (ValueError, TypeError):
        return False


def _port_accepts(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


def _registry_candidates():
    try:
        import winreg
    except ImportError:
        return []
    candidates = []
    key_name = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
    views = {0, getattr(winreg, "KEY_WOW64_32KEY", 0), getattr(winreg, "KEY_WOW64_64KEY", 0)}
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in views:
            try:
                with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ | view) as key:
                    value, _ = winreg.QueryValueEx(key, None)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip().strip('"'))
            except OSError:
                continue
    return candidates


def find_chrome(chrome_path=None):
    """Discover actual chrome.exe paths, never Edge and never a download."""
    if chrome_path is not None and str(chrome_path).strip():
        candidate = Path(os.path.expandvars(str(chrome_path))).expanduser().resolve()
        if candidate.name.lower() not in {"chrome.exe", "chrome", "google-chrome", "google-chrome-stable"} or not candidate.is_file():
            raise BrowserLaunchError("所选文件不是可用的 Google Chrome 程序，请选择实际的 chrome.exe。")
        return candidate
    candidates = []
    for name in ("chrome.exe", "chrome", "google-chrome", "google-chrome-stable"):
        discovered = shutil.which(name)
        if discovered:
            candidates.append(discovered)
    candidates.extend(_registry_candidates())
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base:
            candidates.append(str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"))
    for value in candidates:
        candidate = Path(os.path.expandvars(value)).expanduser().resolve()
        if candidate.name.lower() in {"chrome.exe", "chrome", "google-chrome", "google-chrome-stable"} and candidate.is_file():
            return candidate
    raise BrowserLaunchError("未找到 Google Chrome。请先安装 Chrome，或在面板中选择已有的 chrome.exe；工具不会下载或改用 Edge。")


def _profile_path(profile_dir):
    if profile_dir is None or not str(profile_dir).strip():
        raise BrowserLaunchError("需要一个固定的专用浏览器资料目录。")
    profile = Path(os.path.expandvars(str(profile_dir))).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        default_data = (Path(local_app_data) / "Google" / "Chrome" / "User Data").resolve()
        if profile == default_data or profile.is_relative_to(default_data):
            raise BrowserLaunchError("请使用单独的专用资料目录，不要选择日常 Chrome 的默认 User Data 或其中的 profile。")
    if profile.exists() and not profile.is_dir():
        raise BrowserLaunchError("浏览器资料路径已经是文件，请选择专用文件夹。")
    return profile


class _Tcp4Row(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in ("state", "localAddress", "localPort", "remoteAddress", "remotePort", "pid")]


class _Tcp6Row(ctypes.Structure):
    _fields_ = [("localAddress", ctypes.c_ubyte * 16), ("localScope", ctypes.c_uint32),
                ("localPort", ctypes.c_uint32), ("remoteAddress", ctypes.c_ubyte * 16),
                ("remoteScope", ctypes.c_uint32), ("remotePort", ctypes.c_uint32),
                ("state", ctypes.c_uint32), ("pid", ctypes.c_uint32)]


class _Tcp4Table(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("table", _Tcp4Row * 1)]


class _Tcp6Table(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("table", _Tcp6Row * 1)]


class _ProcessBasicInformation(ctypes.Structure):
    _fields_ = [("exitStatus", ctypes.c_int32), ("peb", ctypes.c_void_p), ("affinity", ctypes.c_size_t),
                ("basePriority", ctypes.c_int32), ("pid", ctypes.c_size_t), ("parentPid", ctypes.c_size_t)]


def _windows_supported():
    return os.name == "nt"


class _WindowsDesktop:
    """Only known port owners and their bounded parent chain are inspected."""
    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.ip = ctypes.WinDLL("iphlpapi", use_last_error=True)
        self.nt = ctypes.WinDLL("ntdll", use_last_error=True)
        self.dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        self._enum_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = (
            (self.ip, "GetExtendedTcpTable", [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL, wintypes.ULONG, ctypes.c_int, wintypes.ULONG], wintypes.DWORD),
            (self.kernel, "OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (self.kernel, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
            (self.kernel, "QueryFullProcessImageNameW", [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (self.kernel, "GetProcessTimes", [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4, wintypes.BOOL),
            (self.nt, "NtQueryInformationProcess", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)], ctypes.c_int32),
            (self.user, "EnumWindows", [self._enum_callback, wintypes.LPARAM], wintypes.BOOL),
            (self.user, "GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            (self.user, "GetWindow", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
            (self.user, "GetClassNameW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.user, "GetWindowTextW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.user, "IsWindow", [wintypes.HWND], wintypes.BOOL),
            (self.user, "IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
            (self.user, "IsIconic", [wintypes.HWND], wintypes.BOOL),
            (self.user, "GetForegroundWindow", [], wintypes.HWND),
            (self.user, "ShowWindowAsync", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            (self.user, "BringWindowToTop", [wintypes.HWND], wintypes.BOOL),
            (self.user, "SetForegroundWindow", [wintypes.HWND], wintypes.BOOL),
            (self.dwm, "DwmGetWindowAttribute", [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD], ctypes.c_int32),
        )
        for library, name, arguments, result in signatures:
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result

    def listener_pids(self, host, port):
        family = 23 if ":" in host else 2
        size = wintypes.DWORD()
        row_type = _Tcp6Row if family == 23 else _Tcp4Row
        table_type = _Tcp6Table if family == 23 else _Tcp4Table
        offset, stride = table_type.table.offset, ctypes.sizeof(row_type)
        for _ in range(3):
            error = self.ip.GetExtendedTcpTable(None, ctypes.byref(size), False, family, 3, 0)
            if error not in (0, 122) or size.value < 4 or size.value > 16 * 1024 * 1024:
                raise OSError("Cannot inspect the CDP listener owner")
            buffer = ctypes.create_string_buffer(size.value)
            error = self.ip.GetExtendedTcpTable(buffer, ctypes.byref(size), False, family, 3, 0)
            if error == 122:
                continue
            if error:
                raise OSError("Cannot inspect the CDP listener owner")
            count = ctypes.c_uint32.from_buffer(buffer).value
            if offset + count * stride > len(buffer):
                raise OSError("Unexpected TCP owner table layout")
            result = set()
            expected = ipaddress.ip_address(host)
            for index in range(count):
                row = row_type.from_buffer(buffer, offset + index * stride)
                address = ipaddress.ip_address(bytes(row.localAddress) if family == 23 else int(row.localAddress).to_bytes(4, "little"))
                if row.state == 2 and socket.ntohs(row.localPort & 65535) == port and (address == expected or address.is_unspecified):
                    result.add(int(row.pid))
            return result
        raise OSError("CDP listener ownership changed during inspection")

    def process(self, pid):
        handle = self.kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            raise OSError("Cannot read the listener process metadata")
        try:
            image = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(image))
            if not self.kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length)):
                raise OSError("Cannot identify the CDP listener executable")
            times = [wintypes.FILETIME() for _ in range(4)]
            created = None
            if self.kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
                created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            information = _ProcessBasicInformation()
            returned = wintypes.ULONG()
            status = self.nt.NtQueryInformationProcess(handle, 0, ctypes.byref(information), ctypes.sizeof(information), ctypes.byref(returned))
            return {"pid": int(pid), "image": image.value, "created": created,
                    "parentPid": int(information.parentPid) if status == 0 and information.pid == pid else 0}
        finally:
            self.kernel.CloseHandle(handle)

    def window_state(self, hwnd):
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = ctypes.create_unicode_buffer(256)
        self.user.GetClassNameW(hwnd, name, len(name))
        title = ctypes.create_unicode_buffer(2048)
        self.user.GetWindowTextW(hwnd, title, len(title))
        cloaked = wintypes.DWORD()
        result = self.dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        minimized = bool(self.user.IsIconic(hwnd))
        visible = bool(self.user.IsWindowVisible(hwnd)) and not minimized and result == 0 and not cloaked.value
        return {"hwnd": int(hwnd), "pid": int(pid.value), "className": name.value, "title": title.value,
                "owner": int(self.user.GetWindow(hwnd, 4) or 0), "minimized": minimized,
                "visible": visible, "foreground": visible and int(self.user.GetForegroundWindow() or 0) == int(hwnd)}

    def windows(self, pids):
        results = []
        def visit(hwnd, _parameter):
            pid = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) in pids:
                state = self.window_state(hwnd)
                if state["className"] == "Chrome_WidgetWin_1" and not state["owner"] and (state["visible"] or state["minimized"] or state["title"]):
                    results.append(state)
            return True
        if not self.user.EnumWindows(self._enum_callback(visit), 0):
            raise OSError("Cannot enumerate the Chrome browser window")
        return results

    def activate(self, hwnd, pid):
        before = self.window_state(hwnd)
        if before["pid"] != pid or before["className"] != "Chrome_WidgetWin_1" or before["owner"]:
            raise OSError("The Chrome window ownership changed")
        # SW_SHOW keeps an already maximized window's current size/position.
        self.user.ShowWindowAsync(hwnd, 9 if before["minimized"] else 5)
        self.user.BringWindowToTop(hwnd)
        self.user.SetForegroundWindow(hwnd)
        deadline = time.monotonic() + SHOW_TIMEOUT_SECONDS
        while True:
            state = self.window_state(hwnd)
            if state["pid"] != pid:
                raise OSError("The Chrome window ownership changed")
            if state["foreground"] or time.monotonic() >= deadline:
                return {"visible": bool(state["visible"]), "foreground": bool(state["foreground"])}
            time.sleep(.05)


def _chrome_owner_chain(desktop, listener_pid):
    first = desktop.process(listener_pid)
    if ntpath.basename(first["image"]).lower() != "chrome.exe":
        raise BrowserLaunchError("CDP 入口的监听程序不是本机 Chrome，无法确认要显示的窗口。")
    owners = [listener_pid]
    current = first
    for _ in range(8):
        parent_pid = current.get("parentPid")
        if not parent_pid or parent_pid in owners:
            break
        try:
            parent = desktop.process(parent_pid)
        except OSError:
            break
        if (ntpath.normcase(parent["image"]) != ntpath.normcase(first["image"])
                or parent.get("created") is None or current.get("created") is None
                or parent["created"] > current["created"]):
            break
        owners.append(parent_pid)
        current = parent
    return owners


def _prepare_target(endpoint, target_id):
    from cdp_client import CDPClient
    client = CDPClient(endpoint)
    try:
        target = next((page for page in client.list_pages() if page.get("id") == target_id), None)
        if target is None:
            raise BrowserLaunchError("所选网页已不存在，请刷新网页列表；未打开替代标签页。")
        client.connect(target)
        window = client.call("Browser.getWindowForTarget", {"targetId": target_id})
        window_id = window.get("windowId")
        if not isinstance(window_id, int) or isinstance(window_id, bool):
            raise BrowserLaunchError("未能确认所选网页对应的 Chrome 窗口。")
        if (window.get("bounds") or {}).get("windowState") == "minimized":
            client.call("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "normal"}})
        client.call("Page.bringToFront")
        return {"title": target.get("title") or "", "cdpWindowId": window_id}
    finally:
        client.close()  # Socket only; never Browser.close or Page.close.


def _choose_window(windows, target):
    if len(windows) == 1:
        return windows[0]
    if target is not None:
        title = target.get("title")
        captions = {title, title + " - Google Chrome", title + " — Google Chrome", title + " – Google Chrome"} if title else set()
        matches = [window for window in windows if window.get("title") in captions]
    else:
        matches = [window for window in windows if window.get("foreground")]
        if not matches:
            matches = [window for window in windows if window.get("visible")]
    return matches[0] if len(matches) == 1 else None


def show_existing(endpoint, target_id=None):
    """Restore the exact local CDP Chrome window without opening another tab.

    Native foreground restrictions are reported, never bypassed with keystrokes
    or thread-input attachment. Window captions are used internally, not logged.
    """
    host, port, normalized = _parse_endpoint(endpoint)
    result = {"endpoint": normalized, "targetId": target_id, "visible": False, "foreground": False,
              "listenerPid": None, "windowPid": None, "hwnd": None, "visibilityStatus": "unconfirmed"}
    if not _windows_supported():
        return {**result, "message": "CDP 已就绪；当前平台不支持自动显示 Chrome 窗口，请手动切换浏览器。"}
    try:
        if not _is_chrome_cdp(_read_version(normalized, timeout=1.0)):
            raise BrowserLaunchError("没有确认可用的 Google Chrome CDP，未激活其他窗口。")
        desktop = _WindowsDesktop()
        listeners = desktop.listener_pids(host, port)
        if len(listeners) != 1:
            raise BrowserLaunchError("无法唯一确认 CDP 监听进程，未激活其他 Chrome。")
        listener_pid = next(iter(listeners))
        result["listenerPid"] = listener_pid
        owners = _chrome_owner_chain(desktop, listener_pid)
        target = _prepare_target(normalized, target_id) if target_id else None
        candidates = [window for window in desktop.windows(set(owners))
                      if window.get("pid") in owners and window.get("className") == "Chrome_WidgetWin_1" and not window.get("owner")]
        # Prefer the listener itself; a Chrome parent may be another instance.
        # Only the nearest owning ancestor with real browser windows is used.
        windows = next(([window for window in candidates if window["pid"] == owner]
                        for owner in owners if any(window["pid"] == owner for window in candidates)), [])
        if not windows:
            raise BrowserLaunchError("Chrome CDP 已就绪，但没有找到对应的桌面窗口；没有重启或新开浏览器。")
        selected = _choose_window(windows, target)
        if selected is None:
            raise BrowserLaunchError("该 Chrome 有多个窗口，尚未唯一确认目标；请先选中目标网页再点击显示。")
        result.update(hwnd=selected["hwnd"], windowPid=selected["pid"])
        state = desktop.activate(selected["hwnd"], selected["pid"])
        result.update(visible=state.get("visible") is True, foreground=state.get("visible") is True and state.get("foreground") is True)
        if result["foreground"]:
            result.update(visibilityStatus="foreground", message="Chrome 已显示并置于前台；请核对网页与账号。")
        elif result["visible"]:
            result.update(visibilityStatus="visible", message="Chrome 窗口已恢复可见，但 Windows 未允许置于前台；请点击任务栏中的 Chrome。")
        else:
            result["message"] = "已请求显示对应 Chrome，但未确认窗口可见；请检查任务栏，工具不会重启浏览器。"
        return result
    except Exception as error:
        message = str(error) if isinstance(error, BrowserLaunchError) else "未能确认 Chrome 窗口显示状态，请手动切换浏览器；没有重启或打开替代实例。"
        return {**result, "message": message}


def _shown(result, target_id):
    try:
        visibility = show_existing(result["endpoint"], target_id=target_id)
    except Exception:
        visibility = {"visible": False, "foreground": False, "visibilityStatus": "unconfirmed",
                      "message": "浏览器入口已就绪，但未确认窗口已显示，请手动切换 Chrome。"}
    result.update({key: value for key, value in visibility.items() if key not in {"status", "endpoint"}})
    result.setdefault("notes", []).append(result["message"])
    return result


def launch_or_reuse(endpoint, profile_dir, chrome_path=None, *, target_id=None):
    host, port, normalized = _parse_endpoint(endpoint)
    received_http = False
    try:
        version = _read_version(normalized, timeout=1.0)
        received_http = True
    except (HTTPError, ValueError, UnicodeError):
        version = None
        received_http = True
    except (URLError, OSError, TimeoutError):
        version = None
    if _is_chrome_cdp(version):
        return _shown({
            "status": "reused", "endpoint": normalized,
            "pid": None, "chromePath": None, "profilePath": None,
            "notes": ["这个端口已有可用的 Chrome CDP，已复用且没有重启。", "仅核对了 CDP 端点；现有实例的程序路径、实际 profile 和网站登录账号尚未核对。"],
        }, target_id)
    if received_http or _port_accepts(host, port):
        raise BrowserLaunchError("这个端口已被占用，但没有返回可用的 Google Chrome CDP。请检查占用程序或更换 CDP 端口；工具不会终止它。", {"endpoint": normalized, "status": "occupied"})

    chrome = find_chrome(chrome_path)
    profile = _profile_path(profile_dir)
    # Chrome's explicit bind is IPv4; return the actual new endpoint to the UI.
    actual_endpoint = "http://127.0.0.1:" + str(port)
    if host != "127.0.0.1" and _port_accepts("127.0.0.1", port):
        try:
            actual_version = _read_version(actual_endpoint, timeout=1.0)
        except (HTTPError, URLError, OSError, ValueError, UnicodeError):
            actual_version = None
        if _is_chrome_cdp(actual_version):
            return _shown({"status": "reused", "endpoint": actual_endpoint, "pid": None, "chromePath": None, "profilePath": None, "notes": ["IPv4 回环地址已有可用的 Chrome CDP，已复用；没有重启浏览器。", "现有实例的实际 profile 和网站登录账号尚未核对。"]}, target_id)
        raise BrowserLaunchError("IPv4 回环端口已被其他程序占用，无法为专用 Chrome 使用这个端口。", {"endpoint": actual_endpoint, "status": "occupied"})
    profile.mkdir(parents=True, exist_ok=True)
    arguments = [
        str(chrome), "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=" + str(port), "--user-data-dir=" + str(profile),
        "--no-first-run", "--no-default-browser-check",
    ]
    details = {"endpoint": actual_endpoint, "chromePath": str(chrome), "profilePath": str(profile)}
    try:
        process = subprocess.Popen(arguments, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise BrowserLaunchError("无法启动所选 Google Chrome，请检查程序路径和访问权限。", details) from exc
    details["pid"] = process.pid
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            version = _read_version(actual_endpoint, timeout=min(0.75, remaining))
        except (HTTPError, URLError, OSError, ValueError, UnicodeError):
            version = None
        if _is_chrome_cdp(version):
            notes = ["已启动专用 Chrome，CDP 入口已经就绪。", "启动不等于网站已登录；请在这个 Chrome 中自行完成登录，后续继续使用同一份资料。"]
            if actual_endpoint != normalized:
                notes.append("新实例使用 IPv4 回环入口，请以返回的连接地址为准。")
            return _shown({"status": "started", **details, "notes": notes}, target_id)
        time.sleep(min(POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))
    details["status"] = "started_unconfirmed"
    raise BrowserLaunchError("已尝试打开 Chrome，但 15 秒内未确认 CDP 就绪。可能是该资料目录已被其他 Chrome 使用或启动受到限制；请检查窗口和端口。工具没有终止或重启任何旧浏览器，也不会自动重复启动。", details)
