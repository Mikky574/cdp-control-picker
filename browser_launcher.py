"""Launch a dedicated visible Google Chrome, or reuse an existing loopback CDP.

No browser/profile is copied, closed or reconfigured. Existing CDP profile
identity cannot be established from /json/version and is never claimed here.
"""
from __future__ import annotations

import ipaddress
import json
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


def launch_or_reuse(endpoint, profile_dir, chrome_path=None):
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
        return {
            "status": "reused", "endpoint": normalized,
            "pid": None, "chromePath": None, "profilePath": None,
            "notes": ["这个端口已有可用的 Chrome CDP，已复用且没有重启。", "仅核对了 CDP 端点；现有实例的程序路径、实际 profile 和网站登录账号尚未核对。"],
        }
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
            return {"status": "reused", "endpoint": actual_endpoint, "pid": None, "chromePath": None, "profilePath": None, "notes": ["IPv4 回环地址已有可用的 Chrome CDP，已复用；没有重启浏览器。", "现有实例的实际 profile 和网站登录账号尚未核对。"]}
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
            return {"status": "started", **details, "notes": notes}
        time.sleep(min(POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))
    details["status"] = "started_unconfirmed"
    raise BrowserLaunchError("已尝试打开 Chrome，但 15 秒内未确认 CDP 就绪。可能是该资料目录已被其他 Chrome 使用或启动受到限制；请检查窗口和端口。工具没有终止或重启任何旧浏览器，也不会自动重复启动。", details)
