from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge_setup import stage_connector, install_connector


class ConnectorSetupTests(unittest.TestCase):
    def test_staging_is_owned_and_refuses_local_edits(self):
        with tempfile.TemporaryDirectory(prefix='picker-connector-stage-') as root:
            target = Path(root) / 'managed'
            stage_connector(target=target)
            stage_connector(target=target)
            entry = target / 'cdp-picker-bridge/extension.js'
            entry.write_text('// local edits', encoding='utf-8')
            with self.assertRaises(ValueError):stage_connector(target=target)
            self.assertEqual(entry.read_text(encoding='utf-8'), '// local edits')

    def test_fake_plugin_manager_receives_arguments_without_gui_models_or_grants(self):
        with tempfile.TemporaryDirectory(prefix='picker-connector-cli-') as root:
            directory = Path(root)
            script = directory / 'fake.py'
            log = directory / 'argv.jsonl'
            script.write_text('import json,sys\nfrom pathlib import Path\np=Path(' + repr(str(log))
                + ')\nwith p.open("a",encoding="utf-8") as f:f.write(json.dumps(sys.argv[1:])+"\\n")\nprint(json.dumps({"ok":True}))\n', encoding='utf-8')
            result = install_connector([sys.executable, str(script)], directory, target=directory / 'staged')
            calls = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
            self.assertEqual([call[:2] for call in calls], [['plugins', 'validate'], ['plugins', 'marketplace'], ['plugins', 'install'], ['plugins', 'enable']])
            self.assertTrue(result['activationNeeded'])
            self.assertFalse(result['desktopConnected'])
            self.assertFalse(result['permissionsGranted'])
            self.assertFalse(result['appRestarted'])
            self.assertFalse(result['modelCalled'])

    def test_exit_zero_without_valid_plugin_validation_never_installs(self):
        with tempfile.TemporaryDirectory(prefix='picker-connector-invalid-') as root:
            directory = Path(root)
            script = directory / 'fake.py'
            log = directory / 'argv.jsonl'
            for stdout in ('Electron started', '{}', '{"ok":false}'):
                with self.subTest(stdout=stdout):
                    log.unlink(missing_ok=True)
                    script.write_text('import json,sys\nfrom pathlib import Path\n'
                        'with Path(' + repr(str(log)) + ').open("a",encoding="utf-8") as f:f.write(json.dumps(sys.argv[1:])+"\\n")\n'
                        'print(' + repr(stdout) + ')\n', encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, 'CLI'):
                        install_connector([sys.executable, str(script)], directory, target=directory / 'staged')
                    calls = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(calls[0][:2], ['plugins','validate'])


if __name__ == '__main__':unittest.main()
