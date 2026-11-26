"""Small, threaded CDP transport for an existing loopback Chrome endpoint.

No browser is launched, no page is opened, and close() only drops this transport.
Requires websocket-client at connection time; HTTP discovery uses the stdlib.
Events are delivered as one dictionary argument on a separate callback thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import math
import queue
import threading
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

try:
    import websocket
except ImportError:  # The UI can still show setup/help and discover HTTP targets.
    websocket = None


class CDPError(RuntimeError):
    """Base transport/protocol error."""


class CDPDisconnectedError(CDPError):
    """No connection, closed target, or broken WebSocket."""


class CDPTimeoutError(CDPError, TimeoutError):
    """A CDP request did not finish before its individual deadline."""


class CDPTargetNotFoundError(CDPError):
    """The requested page ID is not present in discovery."""


class CDPProtocolError(CDPError):
    """Chrome returned an error response; protocol fields remain available."""

    def __init__(self, method: str, error: dict):
        self.method = method
        self.code = error.get("code")
        self.message = str(error.get("message", "Unknown protocol error"))
        self.data = error.get("data")
        super().__init__(f"{method}: {self.message} (CDP {self.code})")


class _NoDiscoveryRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise CDPError("CDP discovery must not redirect; use the exact configured local endpoint")


@dataclass
class _Pending:
    method: str
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Exception | None = None


def _loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host.rstrip(".").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_endpoint(endpoint: str) -> str:
    """Accept local Chrome or an explicitly configured local tunnel URL only."""
    try:
        parts = urllib.parse.urlsplit(str(endpoint).strip())
        port = parts.port  # Validate malformed/out-of-range ports now.
    except ValueError as error:
        raise ValueError("CDP endpoint must be a valid loopback HTTP(S) URL") from error
    if parts.scheme.lower() not in {"http", "https"} or not _loopback_host(parts.hostname):
        raise ValueError("CDP endpoint must use http(s) on localhost/127.0.0.1/::1; use a local tunnel for remote Chrome")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError("CDP endpoint must not contain credentials, a query, or a fragment")
    if port == 0:
        raise ValueError("CDP endpoint port must be greater than zero")
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc, parts.path.rstrip("/"), "", ""))


class CDPClient:
    """Connect to one explicitly selected page on an existing Chrome.

    API:
        list_pages() -> [{id, title, url, webSocketDebuggerUrl, ...}]
        connect(page_dict_or_target_id)
        call(method, params=None, timeout=10) -> result
        close()  # No CDP browser/page close command is sent.

    on_event receives raw CDP event dictionaries. A local CDP.disconnected event
    is additionally emitted with params.reason/targetId/intentional. Callbacks
    run outside the receive thread and may call call(); Tk callers must marshal
    UI changes onto their main thread. Callbacks should finish promptly.
    """

    def __init__(self, endpoint="http://127.0.0.1:9222", on_event: Callable[[dict], None] | None = None):
        self.endpoint = validate_endpoint(endpoint)
        self.on_event = on_event
        self.http_timeout = 5.0
        self.last_callback_error: Exception | None = None
        self._state_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._pending: dict[int, _Pending] = {}
        self._next_id = 0
        self._generation = 0
        self._ws = None
        self._target: dict | None = None
        self._reader: threading.Thread | None = None
        self._event_queue: queue.Queue | None = None
        self._dispatcher: threading.Thread | None = None
        self._disconnect_reason = "Not connected"

    @property
    def connected(self) -> bool:
        with self._state_lock:
            return self._ws is not None

    @property
    def target(self) -> dict | None:
        with self._state_lock:
            return dict(self._target) if self._target else None

    def list_pages(self) -> list[dict]:
        """Discover pages without choosing, creating, activating or navigating one."""
        request = urllib.request.Request(self.endpoint + "/json/list", headers={"Accept": "application/json"})
        # Local control traffic must not leak through HTTP_PROXY/system proxies.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoDiscoveryRedirect())
        try:
            with opener.open(request, timeout=self.http_timeout) as response:
                data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise CDPError("CDP discovery response is unexpectedly large")
            targets = json.loads(data.decode("utf-8"))
        except CDPError:
            raise
        except (OSError, urllib.error.URLError, UnicodeError, json.JSONDecodeError) as error:
            raise CDPDisconnectedError(f"Cannot discover Chrome pages: {type(error).__name__}: {error}") from error
        if not isinstance(targets, list):
            raise CDPError("CDP /json/list did not return a target list")
        pages = []
        for item in targets:
            if not isinstance(item, dict) or item.get("type") != "page" or not isinstance(item.get("id"), str):
                continue
            page = dict(item)
            for key in ("title", "url", "webSocketDebuggerUrl"):
                page[key] = str(page.get(key) or "")
            pages.append(page)
        return pages

    def _socket_url(self, target: dict) -> str:
        try:
            socket_url = urllib.parse.urlsplit(str(target.get("webSocketDebuggerUrl") or ""))
            if socket_url.scheme not in {"ws", "wss"} or not socket_url.netloc or not socket_url.path:
                raise ValueError("Missing page WebSocket URL")
            if socket_url.username is not None or socket_url.password is not None:
                raise ValueError("Unexpected credentials in page WebSocket URL")
            endpoint = urllib.parse.urlsplit(self.endpoint)
        except ValueError as error:
            raise CDPError("Selected page does not have a valid debugging WebSocket URL") from error
        # Rebase onto the explicitly chosen local authority. A forwarded Chrome
        # may advertise its original port, which is wrong for a local SSH tunnel.
        # This also prevents a discovery response from opening an external host.
        scheme = "wss" if endpoint.scheme == "https" else "ws"
        return urllib.parse.urlunsplit((scheme, endpoint.netloc, socket_url.path, socket_url.query, ""))

    def connect(self, target: dict | str):
        """Attach to a selected list_pages() entry or exact ID, never the first tab."""
        with self._lifecycle_lock:
            if isinstance(target, str):
                matches = [page for page in self.list_pages() if page["id"] == target]
                if len(matches) != 1:
                    raise CDPTargetNotFoundError("Selected Chrome page ID is no longer available")
                target = matches[0]
            if not isinstance(target, dict) or not target.get("id"):
                raise ValueError("connect() needs a selected page dictionary or exact target ID")
            if target.get("type", "page") != "page":
                raise ValueError("Only page targets can be attached")
            url = self._socket_url(target)
            if websocket is None:
                raise CDPError("Missing websocket-client. Install the application's requirements or use the packaged executable.")
            self.close()
            with self._state_lock:
                self._generation += 1
                generation = self._generation
                self._disconnect_reason = "Connecting"
            socket = None
            try:
                socket = websocket.create_connection(
                    url, timeout=self.http_timeout, suppress_origin=True,
                    enable_multithread=True,
                    http_no_proxy=[urllib.parse.urlsplit(self.endpoint).hostname, "localhost", "127.0.0.1", "::1"],
                    redirect_limit=0,
                )
                if socket.getstatus() != 101:
                    raise CDPDisconnectedError("Chrome did not accept the WebSocket upgrade")
                socket.settimeout(0.5)
            except Exception as error:
                if socket is not None:
                    self._close_socket(socket)
                with self._state_lock:
                    self._disconnect_reason = f"WebSocket handshake failed: {type(error).__name__}: {error}"
                raise CDPDisconnectedError(self._disconnect_reason) from error
            with self._state_lock:
                self._ws = socket
                self._target = dict(target)
                self._disconnect_reason = ""
                events = queue.Queue()
                self._event_queue = events
            self._dispatcher = threading.Thread(target=self._dispatch_events, args=(events,), name="cdp-callback", daemon=True)
            self._reader = threading.Thread(target=self._receive, args=(socket, generation), name="cdp-receive", daemon=True)
            self._dispatcher.start()
            self._reader.start()

    def call(self, method: str, params: dict | None = None, timeout: float = 10):
        if not isinstance(method, str) or not method:
            raise ValueError("CDP method must be a nonempty string")
        if params is not None and not isinstance(params, dict):
            raise TypeError("CDP params must be a dictionary or None")
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("CDP timeout must be greater than zero")
        with self._state_lock:
            socket, generation = self._ws, self._generation
            if socket is None:
                raise CDPDisconnectedError(self._disconnect_reason or "No CDP page is connected")
            self._next_id += 1
            request_id = self._next_id
            pending = _Pending(method)
            self._pending[request_id] = pending
        try:
            message = json.dumps({"id": request_id, "method": method, "params": params or {}}, ensure_ascii=False)
        except (TypeError, ValueError):
            with self._state_lock:
                self._pending.pop(request_id, None)
            raise
        try:
            with self._send_lock:
                with self._state_lock:
                    active = socket is self._ws and generation == self._generation
                if not active:
                    raise CDPDisconnectedError("CDP connection changed before the request could be sent")
                socket.send(message)
        except Exception as error:
            self._break_connection(socket, generation, f"CDP send failed: {type(error).__name__}: {error}")
            if not pending.error:
                pending.error = CDPDisconnectedError("CDP request could not be sent")
                pending.done.set()
        if not pending.done.wait(timeout):
            with self._state_lock:
                outstanding = self._pending.pop(request_id, None)
            if outstanding is not None:
                raise CDPTimeoutError(f"{method} timed out after {timeout:g}s; Chrome may still have executed it, check state before retrying")
            # The reader removed the response immediately before setting done.
            pending.done.wait(0.1)
        if pending.error:
            raise pending.error
        return pending.result

    def _receive(self, socket, generation):
        while True:
            with self._state_lock:
                if socket is not self._ws or generation != self._generation:
                    return
            try:
                message = socket.recv()
                if message is None or message == "" or message == b"":
                    raise CDPDisconnectedError("Chrome closed the page connection")
                if isinstance(message, bytes):
                    message = message.decode("utf-8")
                payload = json.loads(message)
                if not isinstance(payload, dict):
                    raise CDPError("Chrome sent a non-object CDP message")
            except Exception as error:
                timeout_type = getattr(websocket, "WebSocketTimeoutException", TimeoutError)
                if isinstance(error, (TimeoutError, timeout_type)):
                    continue
                self._break_connection(socket, generation, f"CDP connection lost: {type(error).__name__}: {error}")
                return
            if "id" in payload:
                if not isinstance(payload["id"], int):
                    self._break_connection(socket, generation, "Chrome sent an invalid CDP response ID")
                    return
                with self._state_lock:
                    pending = self._pending.pop(payload["id"], None)
                    if pending is not None:
                        if isinstance(payload.get("error"), dict):
                            pending.error = CDPProtocolError(pending.method, payload["error"])
                        elif "result" in payload:
                            pending.result = payload["result"]
                        else:
                            pending.error = CDPError("Chrome response has neither result nor error")
                        pending.done.set()
                continue
            if isinstance(payload.get("method"), str):
                self._queue_event(payload, generation)
                method, params = payload["method"], payload.get("params") or {}
                if not isinstance(params, dict):
                    self._break_connection(socket, generation, "Chrome sent invalid CDP event parameters")
                    return
                with self._state_lock:
                    selected_id = (self._target or {}).get("id")
                lost_target = method in {"Target.targetDestroyed", "Target.detachedFromTarget"} and params.get("targetId") == selected_id
                if method == "Inspector.detached" or lost_target:
                    reason = str(params.get("reason") or "Selected Chrome page was detached")
                    self._break_connection(socket, generation, reason)
                    return

    def _queue_event(self, event: dict, generation: int):
        with self._state_lock:
            events = self._event_queue
        if events is not None:
            events.put_nowait((generation, event))

    def _dispatch_events(self, events):
        while True:
            item = events.get()
            if item is None:
                return
            generation, event = item
            with self._state_lock:
                current = generation == self._generation
            if current and self.on_event:
                try:
                    self.on_event(event)
                except Exception as error:
                    self.last_callback_error = error

    @staticmethod
    def _close_socket(socket):
        try:
            socket.shutdown()  # Immediate socket-only teardown, no CDP command.
        except Exception:
            try:
                socket.close(timeout=0)
            except Exception:
                pass

    def _break_connection(self, socket, generation, reason, intentional=False):
        with self._state_lock:
            if socket is not self._ws or generation != self._generation:
                return
            self._ws = None
            self._disconnect_reason = str(reason)
            target_id = (self._target or {}).get("id")
            pending = list(self._pending.values())
            self._pending.clear()
            for request in pending:
                request.error = CDPDisconnectedError(str(reason))
                request.done.set()
        self._queue_event({"method": "CDP.disconnected", "params": {
            "reason": str(reason), "targetId": target_id, "intentional": bool(intentional),
        }}, generation)
        with self._state_lock:
            events = self._event_queue
        if events is not None:
            events.put_nowait(None)
        self._close_socket(socket)

    def close(self):
        """Disconnect this client only. Never send Browser.close or close a page."""
        with self._lifecycle_lock:
            with self._state_lock:
                socket, generation = self._ws, self._generation
                events = self._event_queue
                reader = self._reader
            if socket is not None:
                self._break_connection(socket, generation, "CDP client disconnected", intentional=True)
            if events is not None:
                events.put_nowait(None)
            if reader and reader is not threading.current_thread():
                reader.join(timeout=0.75)
            with self._state_lock:
                if self._event_queue is events:
                    self._event_queue = None
                self._reader = None
                self._target = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
