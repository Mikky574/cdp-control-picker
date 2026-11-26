"""Learning bundle contracts use local fixtures; no browser/model execution."""
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workspace import Workspace, WorkspaceError, page_identity


class WorkspaceLearningTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='picker-learning-bundle-')
        self.root = Path(self.temporary.name) / 'workspace'
        self.page_skill = self.root / '.capture-kit' / 'cdp-page-contract' / 'SKILL.md'
        self.learning_skill = self.page_skill.parent.parent / 'cdp-learn-and-verify' / 'SKILL.md'
        for path in (self.page_skill, self.learning_skill):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# Local fixture instructions\n', encoding='utf-8')
        self.workspace = Workspace(self.root, skill_path=self.page_skill)
        self.url = 'https://example.test/orders?privateRecord=QUERY_SENTINEL#details'
        self.records = []
        for capture_id, label in (('cap-input', '编号输入'), ('cap-search', '查询按钮')):
            self.records.append(self.workspace.save_capture({
                'captureId': capture_id,
                'page': {'url': self.url, 'targetId': 'historical-target-before-browser-restart'},
                'control': {'tagName': 'button', 'selectors': [{'strategy': 'css', 'value': '#' + capture_id}]},
                'annotation': {'name': label, 'description': '功能：' + label, 'allowedActions': ['read']}}))
        self.connection = {'endpoint': 'http://localhost:19333/', 'targetId': 'current-selected-target'}
        self.runtime = [r'C:\Fixture Install\Picker.exe']

    def tearDown(self):
        self.temporary.cleanup()

    def export(self, **changes):
        options = {'intent': 'learn-and-verify', 'connection': self.connection,
                   'runtime_command': self.runtime}
        options.update(changes)
        return self.workspace.export_page_bundle(self.url, **options)

    def test_learning_uses_actual_endpoint_target_and_hashes_full_url_without_query_leak(self):
        bundle = self.export()
        payload = json.loads(Path(bundle['bundlePath']).read_text(encoding='utf-8'))
        self.assertEqual(payload['taskIntent'], {'mode': 'learn-and-verify', 'contractVersion': 1})
        self.assertEqual(payload['connection'], {
            'endpoint': 'http://localhost:19333', 'targetId': 'current-selected-target',
            'page': page_identity(self.url), 'initialUrlSha256': hashlib.sha256(self.url.encode('utf-8')).hexdigest()})
        self.assertNotEqual(payload['connection']['initialUrlSha256'],
                            hashlib.sha256(page_identity(self.url)['url'].encode('utf-8')).hexdigest())
        self.assertNotIn('9222', payload['connection']['endpoint'])
        self.assertEqual(payload['captures'][0]['capture']['page']['targetId'],
                         'historical-target-before-browser-restart')
        for path in Path(bundle['directory']).rglob('*'):
            if path.is_file():
                self.assertNotIn('QUERY_SENTINEL', path.read_text(encoding='utf-8'))
                self.assertNotIn('privateRecord=', path.read_text(encoding='utf-8'))
        self.assertEqual(payload['runtime'], {'command': self.runtime,
                                             'requestFlag': '--cdp-task', 'outputFlag': '--output'})

    def test_learning_context_links_both_skills_and_real_output_directory(self):
        bundle = self.export()
        directory = Path(bundle['directory'])
        payload = json.loads(Path(bundle['bundlePath']).read_text(encoding='utf-8'))
        context = Path(bundle['contextPath']).read_text(encoding='utf-8')
        self.assertEqual(Path(bundle['taskPath']).read_text(encoding='utf-8'), context)
        links = re.findall(r'\[[^\]]*\]\(([^)]+)\)', context)
        resolved = {(directory / link).resolve() for link in links}
        self.assertTrue(all(path.is_file() for path in resolved))
        self.assertIn(self.page_skill.resolve(), resolved)
        self.assertIn(self.learning_skill.resolve(), resolved)
        self.assertIn(Path(bundle['bundlePath']).resolve(), resolved)
        self.assertEqual(payload['learning']['directory'], 'learning')
        self.assertEqual((directory / payload['learning']['skillRef']).resolve(), self.learning_skill.resolve())
        self.assertTrue((directory / 'learning').is_dir())
        self.assertEqual(list((directory / 'learning').iterdir()), [])
        self.assertIn('先执行 inspect', context)
        self.assertIn('learning/learning-report.json', context)
        self.assertIn('learning/SKILL.md', context)
        self.assertIn('runner.json', context)
        self.assertIn('缺真实参数时列出参数', context)
        self.assertIn('真实回读结果', context)
        self.assertNotIn('简短确认收到后等待', context)
        self.assertNotIn('本次不连接网站', context)
        self.assertIn('学习并实际验证', bundle['shortTask'])

    def test_export_keeps_original_capture_evidence_and_unverified_sequence(self):
        before = {Path(record['capturePath']): Path(record['capturePath']).read_bytes()
                  for record in self.records}
        self.workspace.set_page_sequence(self.url, ['cap-search', 'cap-input'], ordered=True)
        bundle = self.export()
        payload = json.loads(Path(bundle['bundlePath']).read_text(encoding='utf-8'))
        self.assertEqual([entry['captureId'] for entry in payload['captures']], ['cap-search', 'cap-input'])
        self.assertTrue(payload['sequence']['ordered'])
        self.assertFalse(payload['sequence']['executionValidated'])
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(payload['captures'][0]['source']['sha256'],
                         hashlib.sha256(before[Path(self.records[1]['capturePath'])]).hexdigest())

    def test_missing_or_invalid_connection_runtime_or_intent_creates_no_bundle(self):
        invalid = [
            {'connection': None}, {'connection': {}},
            {'connection': {'endpoint': self.connection['endpoint'], 'targetId': ''}},
            {'connection': {'endpoint': 'https://remote.example.test:9222', 'targetId': 'selected'}},
            {'runtime_command': None}, {'runtime_command': []},
            {'runtime_command': ['']}, {'runtime_command': 'Picker.exe --shell'},
            {'intent': 'make-up-a-mode'},
        ]
        for case, changes in enumerate(invalid):
            with self.subTest(case=case):
                with self.assertRaises(WorkspaceError):
                    self.export(**changes)
                self.assertFalse((self.root / 'page-bundles').exists())

    def test_missing_learning_skill_or_page_skill_creates_no_bundle(self):
        self.learning_skill.unlink()
        with self.assertRaisesRegex(WorkspaceError, '学习验证技能'):
            self.export()
        self.assertFalse((self.root / 'page-bundles').exists())
        self.learning_skill.write_text('# Restored fixture\n', encoding='utf-8')
        self.page_skill.unlink()
        with self.assertRaises(WorkspaceError):
            self.export()
        self.assertFalse((self.root / 'page-bundles').exists())

    def test_legacy_default_and_explicit_receipt_keep_old_format(self):
        for intent in (None, 'context-only'):
            with self.subTest(intent=intent):
                bundle = self.workspace.export_page_bundle(self.url, intent=intent)
                payload = json.loads(Path(bundle['bundlePath']).read_text(encoding='utf-8'))
                for key in ('taskIntent', 'connection', 'runtime', 'learning'):
                    self.assertNotIn(key, payload)
                context = Path(bundle['contextPath']).read_text(encoding='utf-8')
                self.assertIn('只接收和理解控件上下文', context)
                self.assertIn('等待用户下一条要求', context)
                self.assertFalse((Path(bundle['directory']) / 'learning').exists())


if __name__ == '__main__':
    unittest.main()
