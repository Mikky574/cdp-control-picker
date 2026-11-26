"""Transport tests use in-memory WebSockets; never contact Chrome or port 9222."""

from __future__ import annotations

import concurrent.futures
import io
import json
from pathlib import Path
import queue
import sys
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cdp_client


class FakeTimeout(TimeoutError):
    pass


class FakeSocket:
    def __init__(self, on_send=None):
        self.incoming = queue.Queue()
        self.sent = []
        self.timeout = 0.02
        self.closed = False
        self.on_send = on_send
        self.sent_event = threading.Event()

    def settimeout(self, value):
        self.timeout = min(value, 0.02)

    def getstatus(self):
        return 101

    def send(self, message):
        if self.closed:
            raise OSError("socket closed")
        payload = json.loads(message)
        self.sent.append(payload)
        self.sent_event.set()
        if self.on_send:
            self.on_send(payload, self)

    def recv(self):
        try:
            item = self.incoming.get(timeout=self.timeout)
        except queue.Empty as error:
            raise FakeTimeout() from error
        if isinstance(item, Exception):
            raise item
        return item

    def emit(self, payload):
        self.incoming.put(json.dumps(payload))

    def shutdown(self):
        self.closed = True
        self.incoming.put("")

    def close(self, **kwargs):
        self.shutdown()


PAGE = {"id": "demo-page", "type": "page", "title": "Test page", "url": "https://example.com/orders",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/demo-page"}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = None

    def tearDown(self):
        if self.client:
            self.client.close()

    def attach(self, socket=None, callback=None, endpoint="http://127.0.0.1:9222"):
        socket = socket or FakeSocket(lambda request, sock: sock.emit({"id": request["id"], "result": {"ok": True}}))
        self.factory = mock.Mock(return_value=socket)
        module = types.SimpleNamespace(create_connection=self.factory, WebSocketTimeoutException=FakeTimeout)
        patcher = mock.patch.object(cdp_client, "websocket", module)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = cdp_client.CDPClient(endpoint, on_event=callback)
        self.client.connect(PAGE)
        return socket

    def test_loopback_endpoint_and_tunnel(self):
        for endpoint in ("http://localhost:19222", "http://127.0.0.9:9222", "http://[::1]:9222"):
            self.assertEqual(cdp_client.validate_endpoint(endpoint), endpoint)
        for endpoint in ("http://192.0.2.10:9222", "http://example.com:9222", "file:///tmp/a", "http://user:pass@localhost:9222", "http://localhost:99999"):
            with self.assertRaises(ValueError):
                cdp_client.validate_endpoint(endpoint)
        self.attach(endpoint="http://127.0.0.1:19222")
        self.assertEqual(self.factory.call_args.args[0], "ws://127.0.0.1:19222/devtools/page/demo-page")
        self.assertTrue(self.factory.call_args.kwargs["suppress_origin"])

    def test_discovery_filters_workers_and_does_not_open_pages(self):
        response = io.BytesIO(json.dumps([PAGE, {"id": "worker", "type": "service_worker"}]).encode())
        opener = mock.Mock()
        opener.open.return_value = response
        client = cdp_client.CDPClient()
        with mock.patch.object(cdp_client.urllib.request, "build_opener", return_value=opener):
            pages = client.list_pages()
        self.assertEqual([page["id"] for page in pages], ["demo-page"])
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:9222/json/list")
        self.assertFalse(client.connected)

    def test_synchronous_result_and_protocol_error(self):
        socket = self.attach()
        self.assertEqual(self.client.call("Runtime.enable"), {"ok": True})
        socket.on_send = lambda request, sock: sock.emit({"id": request["id"], "error": {"code": -32601, "message": "Unknown method", "data": "diagnostic"}})
        with self.assertRaises(cdp_client.CDPProtocolError) as caught:
            self.client.call("Unknown.method")
        self.assertEqual(caught.exception.code, -32601)
        self.assertEqual(caught.exception.data, "diagnostic")
        self.assertTrue(self.client.connected)

    def test_concurrent_responses_are_routed_by_id_not_order(self):
        requests = []
        def on_send(request, socket):
            requests.append(request)
            if len(requests) == 2:
                for item in reversed(requests):
                    socket.emit({"id": item["id"], "result": {"method": item["method"]}})
        self.attach(FakeSocket(on_send))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(self.client.call, "Method.first")
            second = executor.submit(self.client.call, "Method.second")
            self.assertEqual(first.result(timeout=1), {"method": "Method.first"})
            self.assertEqual(second.result(timeout=1), {"method": "Method.second"})

    def test_slow_callback_does_not_block_responses(self):
        entered, release = threading.Event(), threading.Event()
        def callback(event):
            if event["method"] == "Runtime.bindingCalled":
                entered.set()
                release.wait(1)
        socket = self.attach(callback=callback)
        self.addCleanup(release.set)
        socket.emit({"method": "Runtime.bindingCalled", "params": {"name": "__cdpControlPickerSave", "payload": "{}"}})
        self.assertTrue(entered.wait(1))
        self.assertEqual(self.client.call("Runtime.enable", timeout=0.3), {"ok": True})
        release.set()

    def test_callback_can_itself_call_cdp(self):
        complete = threading.Event()
        results = []
        def callback(event):
            if event["method"] == "Runtime.bindingCalled":
                results.append(self.client.call("Runtime.evaluate", {"expression": "1"}, timeout=0.5))
                complete.set()
        socket = self.attach(callback=callback)
        socket.emit({"method": "Runtime.bindingCalled", "params": {"payload": "{}"}})
        self.assertTrue(complete.wait(1))
        self.assertEqual(results, [{"ok": True}])

    def test_close_releases_pending_and_never_closes_page(self):
        socket = self.attach(FakeSocket())
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.client.call, "Runtime.evaluate", {"expression": "1"}, 5)
            self.assertTrue(socket.sent_event.wait(1))
            self.client.close()
            with self.assertRaises(cdp_client.CDPDisconnectedError):
                pending.result(timeout=1)
        self.assertEqual([message["method"] for message in socket.sent], ["Runtime.evaluate"])
        self.assertTrue(socket.closed)

    def test_request_timeout_does_not_break_later_requests(self):
        socket = self.attach(FakeSocket())
        with self.assertRaises(cdp_client.CDPTimeoutError):
            self.client.call("Runtime.evaluate", timeout=0.03)
        old_id = socket.sent[0]["id"]
        socket.emit({"id": old_id, "result": {"stale": True}})
        socket.on_send = lambda request, sock: sock.emit({"id": request["id"], "result": {"current": True}})
        self.assertEqual(self.client.call("Runtime.enable", timeout=0.5), {"current": True})
        self.assertTrue(self.client.connected)

    def test_target_loss_is_reported_and_pending_fails(self):
        disconnected = threading.Event()
        events = []
        def callback(event):
            events.append(event)
            if event["method"] == "CDP.disconnected":
                disconnected.set()
        socket = self.attach(FakeSocket(), callback)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.client.call, "Runtime.evaluate", None, 5)
            self.assertTrue(socket.sent_event.wait(1))
            socket.emit({"method": "Inspector.detached", "params": {"reason": "target_closed"}})
            with self.assertRaises(cdp_client.CDPDisconnectedError):
                pending.result(timeout=1)
        self.assertTrue(disconnected.wait(1))
        self.assertFalse(self.client.connected)
        self.assertEqual(events[-1]["params"]["targetId"], "demo-page")
        self.assertFalse(events[-1]["params"]["intentional"])

    def test_callback_failure_does_not_kill_reader_or_dispatcher(self):
        received = threading.Event()
        def callback(event):
            if event["method"] == "Broken.callback":
                raise ValueError("callback failed")
            received.set()
        socket = self.attach(callback=callback)
        socket.emit({"method": "Broken.callback", "params": {}})
        socket.emit({"method": "Good.callback", "params": {}})
        self.assertTrue(received.wait(1))
        self.assertIsInstance(self.client.last_callback_error, ValueError)
        self.assertEqual(self.client.call("Runtime.enable"), {"ok": True})

    def test_reconnect_to_selected_id_and_reject_missing_id(self):
        self.attach()
        self.factory.return_value = FakeSocket(lambda request, socket: socket.emit({"id": request["id"], "result": {"new": True}}))
        with mock.patch.object(self.client, "list_pages", return_value=[PAGE]):
            self.client.connect("demo-page")
        self.assertEqual(self.client.target["id"], "demo-page")
        self.assertEqual(self.client.call("Runtime.enable"), {"new": True})
        with mock.patch.object(self.client, "list_pages", return_value=[]):
            with self.assertRaises(cdp_client.CDPTargetNotFoundError):
                self.client.connect("missing")

    def test_missing_dependency_has_actionable_error(self):
        client = cdp_client.CDPClient()
        with mock.patch.object(cdp_client, "websocket", None):
            with self.assertRaisesRegex(cdp_client.CDPError, "websocket-client"):
                client.connect(PAGE)

    def test_bad_upgrade_closes_only_failed_socket(self):
        socket = FakeSocket()
        socket.getstatus = lambda: 302
        module = types.SimpleNamespace(create_connection=mock.Mock(return_value=socket), WebSocketTimeoutException=FakeTimeout)
        client = cdp_client.CDPClient()
        with mock.patch.object(cdp_client, "websocket", module):
            with self.assertRaises(cdp_client.CDPDisconnectedError):
                client.connect(PAGE)
        self.assertTrue(socket.closed)
        self.assertEqual(socket.sent, [])
        self.assertFalse(client.connected)

    def test_malformed_response_fails_pending_instead_of_stranding_reader(self):
        socket = self.attach(FakeSocket())
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.client.call, "Runtime.evaluate", None, 5)
            self.assertTrue(socket.sent_event.wait(1))
            socket.emit({"id": [], "result": {}})
            with self.assertRaises(cdp_client.CDPDisconnectedError):
                pending.result(timeout=1)
        self.assertFalse(self.client.connected)

    def test_discovery_redirect_is_rejected(self):
        handler = cdp_client._NoDiscoveryRedirect()
        with self.assertRaises(cdp_client.CDPError):
            handler.redirect_request(None, None, 302, "Found", {}, "https://example.com/")


if __name__ == "__main__":
    unittest.main()
