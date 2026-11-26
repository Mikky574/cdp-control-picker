"""Backend checks use fake processes/RPCs; no sessions, index or model calls."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zcode_diagnostics import probe_backend


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='picker-cli-probe-')
        self.root = Path(self.temp.name)
        self.command = [str(self.root / 'node.exe'), str(self.root / 'zcode.cjs')]
        self.calls = []
        self.closed = False

    def tearDown(self):
        self.temp.cleanup()

    def factory(self, **options):
        self.calls.append(('start', options))
        owner = self
        class Server:
            def __enter__(self):return self
            def __exit__(self, *args):owner.closed = True
            def request(self, method, params, timeout):
                owner.calls.append((method, params))
                return {'sessions': []}
        return Server()

    def execute(self, output='0.17.0', code=0, **kwargs):
        with patch('zcode_diagnostics.resolve_command', return_value=self.command), patch(
                'zcode_diagnostics.subprocess.run', return_value=subprocess.CompletedProcess([], code, output, '')) as run:
            result = probe_backend('zcode-cli', self.root, server_factory=self.factory, **kwargs)
        self.run = run
        return result

    def test_reports_real_entry_and_only_read_only_rpc_without_models(self):
        result = self.execute()
        self.assertTrue(result['ok'])
        self.assertEqual(result['command'], self.command)
        self.assertEqual(result['runtimeType'], 'node-cli')
        self.assertEqual(result['transport'], 'app-server-stdio')
        self.assertFalse(result['modelCalled'])
        self.assertFalse(result['indexWritten'])
        self.assertFalse(result['sessionCreated'])
        self.assertEqual([call[0] for call in self.calls], ['start', 'session/list'])
        self.assertTrue(self.calls[1][1]['sessionIds'][0].startswith('picker-backend-probe-'))
        self.assertTrue(self.closed)
        self.assertEqual(self.run.call_args.args[0], self.command + ['--version'])
        self.assertFalse(self.run.call_args.kwargs['shell'])

    def test_version_output_is_informational_and_cannot_reject_a_working_protocol(self):
        for output, code in (('[Electron] startup ready', 0), ('0.17.0', 1), ('PRIVATE_PROVIDER_ERROR', 1)):
            with self.subTest(output=output):
                self.calls.clear()
                result = self.execute(output, code)
                self.assertTrue(result['ok'])
                self.assertEqual(result['cliVersion'], 'unknown')
                self.assertNotIn('PRIVATE_PROVIDER', str(result))
                self.assertEqual([call[0] for call in self.calls], ['start', 'session/list'])

    def test_incompatible_rpc_result_does_not_claim_connection(self):
        original = self.factory
        def incompatible(**options):
            server = original(**options)
            server.request = lambda *args, **kwargs: {'wrong': []}
            return server
        self.factory = incompatible
        result = self.execute()
        self.assertFalse(result['ok'])
        self.assertEqual(result['phase'], 'app-server')
        self.assertTrue(self.closed)

    def test_missing_workspace_does_not_create_or_launch(self):
        missing = self.root / 'not-created'
        with patch('zcode_diagnostics.resolve_command', return_value=self.command), patch(
                'zcode_diagnostics.subprocess.run') as run:
            result = probe_backend('zcode-cli', missing, server_factory=self.factory)
        self.assertFalse(result['ok'])
        self.assertFalse(missing.exists())
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
