"""Pure file tests for page annotation bundles; no Tk, browser or model calls."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workspace import Workspace, WorkspaceError, page_identity


class PageBundleTests(unittest.TestCase):
    def setUp(self):
        task_work = Path.cwd() / 'work'
        task_work.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='page-bundle-tests-', dir=task_work)
        self.root = Path(self.temporary.name) / 'project space'
        skill = self.root / '.capture-kit' / 'cdp-page-contract' / 'SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: cdp-page-contract\ndescription: fixture\n---\n页面定位、前后条件、失败和 draft 验证规则。\n', encoding='utf-8')
        self.workspace = Workspace(self.root, skill_path=skill)

    def tearDown(self):
        self.temporary.cleanup()

    def capture(self, capture_id='cap-a', url='https://example.test/orders', **kwargs):
        card = {'page': {'url': url, 'title': '订单列表', 'targetId': kwargs.get('target_id', 'runtime-tab'), 'frameChain': []},
                'control': {'tagName': 'button', 'text': '详情', 'role': {'value': 'button', 'source': 'cdp-accessibility'},
                            'attributes': {'data-testid': capture_id},
                            'selectors': [{'strategy': 'css', 'value': '[data-testid="' + capture_id + '"]', 'matchCount': 1}]},
                'annotation': {'description': '打开指定订单的只读详情', 'allowedActions': ['click'], 'expectedResult': '详情订单号与选择的记录一致'},
                'validation': {'status': 'validated'}}
        return self.workspace.save_capture(card, capture_id=capture_id)

    def read_bundle(self, exported):
        return json.loads(Path(exported['bundlePath']).read_text(encoding='utf-8'))

    def test_all_current_page_annotations_follow_capture_order_without_mixing_host_or_path(self):
        self.capture('cap-z', 'https://EXAMPLE.test:443/orders?filter=one#row', target_id='tab-first')
        self.capture('cap-a', 'https://example.test/orders?filter=two#other', target_id='tab-second')
        self.capture('cap-other-path', 'https://example.test/users')
        self.capture('cap-other-host', 'https://other.test/orders')
        self.capture('cap-other-scheme', 'http://example.test/orders')
        exported = self.workspace.export_page_bundle({'origin': 'https://example.test', 'path': '/orders'})
        bundle = self.read_bundle(exported)
        self.assertEqual([item['captureId'] for item in bundle['captures']], ['cap-z', 'cap-a'])
        self.assertEqual(bundle['sequence']['captureIds'], ['cap-z', 'cap-a'])
        self.assertEqual(bundle['sequence']['source'], 'capture-order')
        self.assertFalse(bundle['sequence']['ordered'])
        self.assertFalse(bundle['sequence']['executionValidated'])
        self.assertEqual(bundle['page']['url'], 'https://example.test/orders')
        self.assertEqual(exported['captureCount'], 2)
        self.assertEqual({item['capture']['page']['targetId'] for item in bundle['captures']}, {'tab-first', 'tab-second'})
        self.assertFalse(bundle['recording']['allPageEventsRecorded'])
        self.assertFalse(bundle['recording']['orderIsWorkflow'])
        self.assertFalse(bundle['generation']['networkOrBrowserActionsAllowed'])
        self.assertEqual(bundle['status'], 'draft')
        self.assertTrue(all(item['capture']['validation']['status'] == 'draft' for item in bundle['captures']))

    def test_root_path_and_default_ports_are_canonical_but_nondefault_port_is_distinct(self):
        self.capture('cap-root', 'https://example.test')
        self.capture('cap-port', 'https://example.test:8443/')
        bundle = self.read_bundle(self.workspace.export_page_bundle('https://EXAMPLE.test:443/?q=x#item'))
        self.assertEqual(bundle['page']['path'], '/')
        self.assertEqual([item['captureId'] for item in bundle['captures']], ['cap-root'])
        self.assertEqual(page_identity('http://example.test:80')['url'], 'http://example.test/')
        self.assertEqual(page_identity('http://example.test:0/')['origin'], 'http://example.test:0')
        self.assertNotEqual(page_identity('https://example.test:8443/')['origin'], bundle['page']['origin'])

    def test_explicit_subset_preserves_input_order_and_rejects_cross_page_selection(self):
        for item in ('cap-c', 'cap-a', 'cap-b'):
            self.capture(item)
        self.capture('cap-foreign', 'https://example.test/another')
        bundle = self.read_bundle(self.workspace.export_page_bundle('https://example.test/orders', ['cap-c', 'cap-a']))
        self.assertEqual([item['captureId'] for item in bundle['captures']], ['cap-c', 'cap-a'])
        self.assertEqual(bundle['sequence']['captureIds'], ['cap-c', 'cap-a'])
        self.assertEqual(bundle['sequence']['source'], 'explicit-selection')
        self.assertFalse(bundle['sequence']['ordered'])
        self.assertEqual(bundle['selection']['mode'], 'explicit-capture-ids')
        with self.assertRaises(WorkspaceError):
            self.workspace.export_page_bundle('https://example.test/orders', ['cap-a', 'cap-foreign'])

    def test_empty_invalid_missing_and_duplicate_ids_are_rejected_without_creating_bundle(self):
        self.capture()
        for ids in ([], (), 'cap-a', [1], ['../outside'], ['cap-a/../../outside'], ['cap-missing'], ['cap-a', 'cap-a']):
            with self.subTest(ids=ids), self.assertRaises(WorkspaceError):
                self.workspace.export_page_bundle('https://example.test/orders', ids)
        with self.assertRaises(WorkspaceError):
            self.workspace.export_page_bundle('https://example.test/empty')
        self.assertFalse((self.root / 'page-bundles').exists())

    def test_file_pages_group_by_path_and_retain_only_the_page_file_identity(self):
        self.capture('cap-local-a', 'file:///C:/teaching/first.html?q=1#button')
        self.capture('cap-local-b', 'file:///C:/teaching/first.html?q=2')
        self.capture('cap-local-other', 'file:///C:/teaching/second.html')
        bundle = self.read_bundle(self.workspace.export_page_bundle('file:///C:/teaching/first.html#top'))
        self.assertEqual([item['captureId'] for item in bundle['captures']], ['cap-local-a', 'cap-local-b'])
        self.assertEqual(bundle['page']['url'], 'file:///C:/teaching/first.html')
        self.assertTrue(all(item['capture']['page']['url'] == bundle['page']['url'] for item in bundle['captures']))

    def test_main_frame_only_and_explicit_page_identity_required(self):
        record = self.capture()
        source = Path(record['capturePath'])
        raw = json.loads(source.read_text(encoding='utf-8'))
        raw['page']['frameChain'] = [{'url': 'https://frame.test/'}]
        source.write_text(json.dumps(raw), encoding='utf-8')
        with self.assertRaises(WorkspaceError):
            self.workspace.export_page_bundle('https://example.test/orders', ['cap-a'])
        with self.assertRaises(WorkspaceError):
            self.workspace.export_page_bundle({'url': 'https://example.test/orders', 'frameChain': ['iframe']})
        for page in ({}, '', 'about:blank', 'https:///orders', None):
            with self.subTest(page=page), self.assertRaises(WorkspaceError):
                self.workspace.export_page_bundle(page)

    def test_snapshot_drops_private_metadata_form_values_credentials_and_secret_locators(self):
        record = self.capture()
        source = Path(record['capturePath'])
        raw = json.loads(source.read_text(encoding='utf-8'))
        raw.update({'directory': str(self.root), 'privateKey': '-----BEGIN PRIVATE KEY-----\nFAKE_KEY\n-----END PRIVATE KEY-----'})
        raw['control'].update({'tagName': 'input', 'text': 'FAKE_INPUT_TEXT', 'value': 'FAKE_VALUE', 'outerHTML': 'FAKE_HTML'})
        raw['control']['attributes'].update({'value': 'FAKE_ATTR_VALUE', 'data-token': 'FAKE_ATTR_TOKEN'})
        raw['control']['selectors'].append({'strategy': 'css', 'value': '[data-token="FAKE_SELECTOR_TOKEN"]'})
        raw['annotation']['description'] = r'用途说明 password=FAKE_PASSWORD token=FAKE_TOKEN C:\Users\Alice\private.txt file:///C:/Users/Alice/profile.json https://user:pass@example.test/orders?secret=FAKE_URL_SECRET'
        raw['validation']['workspacePath'] = str(self.root)
        raw['validation'].update({'token': 'FAKE_VALIDATION_TOKEN', 'sessionId': 'FAKE_SESSION_ID'})
        raw['annotation']['notes'] = {'token': 'FAKE_NOTES_TOKEN', 'session_id': 'FAKE_NOTES_SESSION'}
        source.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
        before = source.read_bytes()
        exported = self.workspace.export_page_bundle('https://example.test/orders')
        data = Path(exported['bundlePath']).read_text(encoding='utf-8')
        for forbidden in ('FAKE_KEY', 'FAKE_INPUT_TEXT', 'FAKE_VALUE', 'FAKE_HTML', 'FAKE_ATTR_VALUE', 'FAKE_ATTR_TOKEN', 'FAKE_SELECTOR_TOKEN',
                          'FAKE_PASSWORD', 'FAKE_TOKEN', 'FAKE_URL_SECRET', 'Alice', 'user:pass', str(self.root), 'workspacePath', 'outerHTML'):
            self.assertNotIn(forbidden, data)
        for forbidden in ('FAKE_VALIDATION_TOKEN', 'FAKE_SESSION_ID', 'FAKE_NOTES_TOKEN', 'FAKE_NOTES_SESSION'):
            self.assertNotIn(forbidden, data)
        snapshot = self.read_bundle(exported)['captures'][0]['capture']
        self.assertEqual(snapshot['control']['text'], '')
        self.assertEqual(snapshot['control']['role']['value'], 'button')
        self.assertEqual(len(snapshot['control']['selectors']), 1)
        self.assertEqual(source.read_bytes(), before)

    def test_source_links_hashes_and_skill_reference_resolve_to_actual_files(self):
        original = self.capture()
        originals = {key: (Path(original[key]).read_bytes(), Path(original[key]).stat().st_mtime_ns)
                     for key in ('capturePath', 'descriptionPath', 'taskPath')}
        exported = self.workspace.export_page_bundle('https://example.test/orders')
        bundle = self.read_bundle(exported)
        directory = Path(exported['directory'])
        item = bundle['captures'][0]
        source = (directory / item['source']['capturePath']).resolve()
        self.assertEqual(source, Path(original['capturePath']))
        self.assertEqual(json.loads(source.read_text(encoding='utf-8'))['captureId'], item['captureId'])
        self.assertEqual(item['source']['sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
        description = (directory / item['source']['descriptionPath']).resolve()
        self.assertEqual(description.read_text(encoding='utf-8').strip(), item['capture']['annotation']['description'])
        self.assertEqual((directory / bundle['skillRef']).resolve(), self.workspace.skill_path)
        self.assertTrue(Path(exported['generatedPath']).is_dir())
        task = Path(exported['taskPath']).read_text(encoding='utf-8')
        self.assertIn(bundle['skillRef'], task)
        self.assertIn('page-bundle.json', task)
        self.assertIn('不连接网站', task)
        self.assertIn('缺少流程不编造', task)
        self.assertIn(str(Path(exported['contextPath'])), exported['shortTask'])
        self.assertNotIn(str(Path(exported['taskPath'])), exported['shortTask'])
        for key, (content, modified) in originals.items():
            self.assertEqual(Path(original[key]).read_bytes(), content)
            self.assertEqual(Path(original[key]).stat().st_mtime_ns, modified)

    def test_each_export_is_a_new_snapshot_and_never_overwrites_previous_bundle(self):
        self.capture('cap-a')
        first = self.workspace.export_page_bundle('https://example.test/orders')
        first_bytes = Path(first['bundlePath']).read_bytes()
        self.capture('cap-b')
        second = self.workspace.export_page_bundle('https://example.test/orders')
        self.assertNotEqual(first['bundleId'], second['bundleId'])
        self.assertNotEqual(first['taskPath'], second['taskPath'])
        self.assertEqual(first['captureCount'], 1)
        self.assertEqual(second['captureCount'], 2)
        self.assertEqual(Path(first['bundlePath']).read_bytes(), first_bytes)

    def test_context_handoff_reads_full_page_bundle_then_waits_for_user(self):
        self.capture('cap-first')
        self.capture('cap-second')
        exported = self.workspace.export_page_bundle('https://example.test/orders')
        context_path = Path(exported['contextPath'])
        self.assertEqual(context_path.parent, Path(exported['bundlePath']).parent)
        self.assertEqual(context_path.name, 'context.md')
        context = context_path.read_text(encoding='utf-8')
        self.assertIn('完整读取 [page-bundle.json](page-bundle.json)', context)
        self.assertIn('2 个已标注控件的位置与用途上下文', context)
        self.assertIn('source.capturePath', context)
        self.assertIn('source.descriptionPath', context)
        self.assertIn('不要读取本目录或采集卡目录中的 task.md', context)
        self.assertIn('不自动生成、改写或运行技能、页面契约、脚本或其他文件', context)
        self.assertIn('不连接网站', context)
        self.assertIn('等待用户下一条要求', context)
        self.assertIn('2 个控件上下文后等待我后续要求', exported['shortTask'])
        self.assertNotIn('统一能力草稿', exported['shortTask'])
        self.assertNotIn('(task.md)', context)
        self.assertNotIn('generated/', exported['shortTask'])

    def test_context_handoff_keeps_legacy_task_and_original_source_hashes_compatible(self):
        original = self.capture()
        source = Path(original['capturePath'])
        original_bytes = source.read_bytes()
        exported = self.workspace.export_page_bundle('https://example.test/orders')
        bundle = self.read_bundle(exported)
        self.assertEqual(bundle['captures'][0]['source']['sha256'], hashlib.sha256(original_bytes).hexdigest())
        self.assertEqual(source.read_bytes(), original_bytes)
        self.assertIn('统一页面 skill、contract 和候选 runner', Path(exported['taskPath']).read_text(encoding='utf-8'))
        self.assertEqual(list(Path(exported['generatedPath']).iterdir()), [])
        context = Path(exported['contextPath']).read_text(encoding='utf-8')
        self.assertIn('generation 字段、task.md 和 generated/ 目录仅为兼容旧格式保留，不是本次任务要求', context)
        self.assertEqual(bundle['generation']['outputs'][0], 'generated/SKILL.md')


if __name__ == '__main__':
    unittest.main()
