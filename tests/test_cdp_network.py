import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cdp_network import NetworkJobError, observe_network, prepare_query


class NetworkTests(unittest.TestCase):
    def test_query_rejects_external_paths_credentials_and_unbounded_projections(self):
        valid = {'purposeScope': 'read-only', 'request': {'path': '/api/orders/detail',
                 'query': {'tenantId': 'test-team', 'orderNo': 'DEMO-1042'},
                 'fields': {'order': 'data.orderNo', 'statuses': 'items.*.status'}}}
        self.assertEqual(prepare_query(valid)['fields']['statuses'], ['items', '*', 'status'])
        for field, value in [
            ('path', '//evil.test/api'), ('path', '/api/../admin'), ('path', '/api?token=x'),
            ('query', {'access_token': 'opaque'}), ('fields', {'status': 'details.accessToken'}),
            ('fields', {'status': 'items.*.orders.*.status'}),
        ]:
            with self.subTest(field=field, value=value), self.assertRaises(NetworkJobError):
                prepare_query({**valid, 'request': {**valid['request'], field: value}})

    def test_observer_reports_only_top_frame_same_origin_and_no_raw_material(self):
        class Client:
            on_event = None
            calls = []

            def call(self, method, params=None):
                self.calls.append(method)
                if method == 'Page.getFrameTree':
                    return {'frameTree': {'frame': {'id': 'root'}}}

        client = Client()
        def trigger():
            for key, frame, url in (
                ('expected', 'root', 'https://example.test/api/orders/1042?orderNo=PRIVATE_ID&token=PRIVATE_TOKEN'),
                ('child', 'child', 'https://example.test/api/child'),
                ('external', 'root', 'https://other.test/api/third-party'),
            ):
                client.on_event({'method': 'Network.requestWillBeSent', 'params': {
                    'requestId': key, 'frameId': frame, 'type': 'Fetch',
                    'request': {'url': url, 'method': 'GET', 'headers': {'Cookie': 'PRIVATE_COOKIE'}}}})
            client.on_event({'method': 'Network.responseReceived', 'params': {
                'requestId': 'expected', 'response': {'url': 'https://example.test/api/orders/1042',
                'status': 200, 'mimeType': 'application/json', 'headers': {'Set-Cookie': 'PRIVATE_RESPONSE'}}}})
            client.on_event({'method': 'Network.loadingFinished', 'params': {'requestId': 'expected'}})

        result = observe_network(client, 'https://example.test/orders', 1, trigger)
        self.assertEqual(result['evidence']['requests'], [
            {'method': 'GET', 'pathTemplate': '/api/orders/{id}', 'queryKeys': ['orderNo'],
             'type': 'Fetch', 'httpStatus': 200, 'mimeType': 'application/json', 'finished': True}])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(client.calls, ['Page.getFrameTree', 'Network.enable', 'Network.disable'])
        self.assertIsNone(client.on_event)


if __name__ == '__main__':
    unittest.main()
