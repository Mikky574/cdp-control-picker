"""Local fake HTTP only: no ZCode, model, GUI, real token or user data."""
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_bridge import DesktopBridge, DesktopBridgeError


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='picker-native-bridge-test-')
        self.root = Path(self.temp.name)
        self.discovery = self.root / 'discovery'
        self.discovery.mkdir()
        self.calls = []
        self.behavior = None
        self.instances = {}
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                token = self.headers.get('Authorization', '')
                instance = fixture.instances.get(token)
                if instance is None:
                    self.send_response(403); self.end_headers(); return
                value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fixture.calls.append(value)
                method = value['method']
                if method == 'health':
                    result = {key: instance[key] for key in
                              ('schemaVersion', 'instanceId', 'pluginId', 'pluginVersion', 'workspacePath')}
                    result.update(transport='zcode-extension-host', nativeSessions=True)
                    if fixture.behavior == 'wrong-instance':
                        result['instanceId'] = 'not-this-instance'
                    elif fixture.behavior == 'unsupported-api':
                        result['nativeSessions'] = False
                elif fixture.behavior == 'redirect':
                    self.send_response(302); self.send_header('Location', 'http://example.test/never-follow'); self.end_headers(); return
                elif fixture.behavior == 'error':
                    data = json.dumps({'ok':False, 'error':{'code':'not-granted','message':'PRIVATE_PROVIDER_KEY_FIXTURE'}}).encode()
                    self.send_response(200); self.end_headers(); self.wfile.write(data); return
                elif fixture.behavior == 'invalid-json':
                    self.send_response(200); self.end_headers(); self.wfile.write(b'Electron log only'); return
                elif method == 'models':
                    result = [{'id':'vendor/model/with/slash','providerId':'vendor','modelId':'model/with/slash',
                               'label':'Demo model','providerName':'Demo'}]
                else:
                    result = {'method':method,'params':value['params']}
                data = json.dumps({'ok':True,'result':result}).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.file = self.connection('one')

    def connection(self, identifier):
        item = {'schemaVersion':1,'instanceId':identifier,'pid':12345,'pluginId':'picker-bridge',
                'pluginVersion':'1.0.0','endpoint':'http://127.0.0.1:'+str(self.http.server_port),
                'token':'FAKE_ONLY_'+identifier,'workspacePath':str(self.root/'native-workspace'), 'startedAt':'fixture'}
        self.instances['Bearer '+item['token']] = item
        path = self.discovery/(identifier+'.json')
        path.write_text(json.dumps(item), encoding='utf-8')
        return path

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join(timeout=2)
        self.temp.cleanup()

    def client(self, path=None):
        return DesktopBridge(self.root, discovery_path=path or self.file, timeout_seconds=.5)

    def test_health_models_use_authenticated_local_host_without_proxy_or_secret_output(self):
        with patch.dict(os.environ, {'HTTP_PROXY':'http://192.0.2.1:1','HTTPS_PROXY':'http://192.0.2.1:1'}):
            client = self.client()
            health = client.health()
            rows = client.list_models()
        self.assertTrue(health['nativeSessions'])
        self.assertNotIn('token', health)
        self.assertNotIn('FAKE_ONLY', json.dumps(health))
        self.assertEqual(rows[0]['modelId'], 'model/with/slash')
        self.assertEqual(client.model_reference({'providerId':'vendor','modelId':'model/with/slash'}), 'vendor/model/with/slash')
        self.assertFalse(any(row['method'] in {'authorize','create','send'} for row in self.calls))

    def test_multiple_active_instances_require_explicit_discovery_file(self):
        second = self.connection('two')
        with self.assertRaises(DesktopBridgeError) as caught:
            self.client(self.discovery).health()
        self.assertEqual(caught.exception.code, 'ambiguous_instances')
        self.assertEqual(len(caught.exception.candidates), 2)
        self.assertNotIn('FAKE_ONLY', json.dumps(caught.exception.candidates))
        self.assertEqual(self.client(second).health()['instanceId'], 'two')

    def test_mismatched_health_and_remote_discovery_never_select_a_host(self):
        self.behavior = 'wrong-instance'
        with self.assertRaises(DesktopBridgeError):
            self.client().health()
        value = json.loads(self.file.read_text())
        value['endpoint'] = 'http://192.0.2.1:9222'
        self.file.write_text(json.dumps(value))
        before = len(self.calls)
        with self.assertRaises(DesktopBridgeError):
            self.client().health()
        self.assertEqual(len(self.calls), before)

    def test_create_carries_stable_request_identity_once_and_authorization_is_explicit(self):
        client = self.client()
        client.health()
        client.authorize_connection()
        result = client.create_session('Read fixture context', request_id='handoff-fixture', model='vendor/model/with/slash')
        sent = next(row for row in self.calls if row['method']=='create')
        self.assertEqual(sent['requestId'], sent['params']['requestId'])
        self.assertEqual(sent['params']['mode'], 'yolo')
        self.assertEqual(result['params']['model'], 'vendor/model/with/slash')
        self.assertEqual(sum(row['method']=='create' for row in self.calls), 1)
        with self.assertRaises(DesktopBridgeError):
            client.request('send', {'requestId':'another'}, request_id='handoff-fixture')
        self.assertEqual(sum(row['method']=='send' for row in self.calls), 0)

    def test_native_rejection_is_bounded_and_does_not_echo_provider_error(self):
        client = self.client(); client.health(); self.behavior='error'
        with self.assertRaises(DesktopBridgeError) as caught:
            client.create_session('Read fixture', request_id='handoff-fixture')
        self.assertTrue(caught.exception.definite_rejection)
        self.assertEqual(caught.exception.code, 'not-granted')
        self.assertNotIn('PRIVATE_PROVIDER', str(caught.exception))

    def test_redirect_and_non_json_fail_without_automatic_mutation_retry(self):
        for behavior in ('redirect','invalid-json'):
            with self.subTest(behavior=behavior):
                self.behavior=None
                client=self.client(); client.health(); self.behavior=behavior
                before=sum(row['method']=='create' for row in self.calls)
                with self.assertRaises(DesktopBridgeError):
                    client.create_session('Read fixture', request_id='handoff-fixture')
                self.assertEqual(sum(row['method']=='create' for row in self.calls),before+1)

    def test_unknown_or_credential_model_fields_are_rejected_before_native_create(self):
        client=self.client()
        for selection in ({'providerId':'vendor','modelId':'other'},
                          {'providerId':'vendor','modelId':'model/with/slash','apiKey':'FAKE_ONLY'}):
            with self.subTest(selection=selection), self.assertRaises(DesktopBridgeError):
                client.model_reference(selection)
        self.assertFalse(any(row['method']=='create' for row in self.calls))

    def test_default_discovery_and_response_budget(self):
        with patch.dict(os.environ, {'LOCALAPPDATA':str(self.root)}):
            client=DesktopBridge(self.root)
        self.assertEqual(client.discovery_path,self.root/'CDP-Control-Picker'/'desktop-bridges')
        client=self.client(); client.health(); client.MAX_RESPONSE=16
        with self.assertRaises(DesktopBridgeError) as caught:
            client.list_models()
        self.assertEqual(caught.exception.code,'response_limit')

    def test_live_peer_without_native_api_is_reported_as_capability_not_missing_connection(self):
        self.behavior='unsupported-api'
        report=self.client().health()
        self.assertFalse(report['nativeSessions'])
        self.assertFalse(any(row['method']=='create' for row in self.calls))


if __name__ == '__main__':
    unittest.main()
