"""Isolated CDP jobs and a fixed JavaScript DOM fixture; no browser or model."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cdp_task import run_task, SCRIPT
from workspace import page_identity


PAGE = 'https://example.test/orders?access_token=DO_NOT_PERSIST_THIS_QUERY#list'


class FakeCDP:
    def __init__(self, owner):
        self.owner = owner
        self.calls = []
        self.connected = None
        self.closed = False

    def list_pages(self):
        return copy.deepcopy(self.owner.pages)

    def connect(self, target):
        self.connected = target

    def call(self, method, params, timeout=10):
        self.calls.append((method, params))
        if len(self.calls) == 1:
            return {'result': {'value': {'url': self.owner.live_url, 'title': 'Fixture orders'}}}
        if self.owner.failure:
            raise ConnectionError('RAW_PRIVATE_TRANSPORT_DETAIL')
        return {'result': {'value': copy.deepcopy(self.owner.observed)}}

    def close(self):
        self.closed = True


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='picker-cdp-task-')
        self.root = Path(self.temporary.name)
        self.directory = self.root / 'page-fixture'
        self.learning = self.directory / 'learning'
        self.learning.mkdir(parents=True)
        self.bundle_path = self.directory / 'page-bundle.json'
        self.job_path = self.learning / 'step.json'
        self.output = self.learning / 'step-result.json'
        self.live_url = PAGE
        self.pages = [{'id': 'wrong-same-url', 'type': 'page', 'url': PAGE},
                      {'id': 'selected-target', 'type': 'page', 'url': PAGE}]
        self.clients = []
        self.failure = False
        self.bundle = {'schemaVersion': 1, 'kind': 'annotated-controls-page-bundle',
                       'taskIntent': {'mode': 'learn-and-verify', 'contractVersion': 1},
                       'page': page_identity(PAGE), 'captures': [
                           {'captureId': 'cap-input', 'capture': {
                               'control': {'tagName': 'input', 'selectors': [{'strategy': 'css', 'value': '#query'}]},
                               'annotation': {'allowedActions': ['read', 'fill']}}},
                           {'captureId': 'cap-query', 'capture': {
                               'control': {'tagName': 'button', 'selectors': [{'strategy': 'css', 'value': '#search'}]},
                               'annotation': {'allowedActions': ['read', 'click']}}}],
                       'connection': {'endpoint': 'http://127.0.0.1:9222', 'targetId': 'selected-target',
                           'page': page_identity(PAGE), 'initialUrlSha256': self.digest(PAGE)}}
        self.job = {'schemaVersion': 1, 'bundlePath': str(self.bundle_path), 'operation': 'inspect'}
        self.observed = {'status': 'completed', 'actionAttempted': False, 'controls': [
            {'captureId': 'cap-input', 'status': 'located', 'matchCount': 1,
             'visible': True, 'enabled': True, 'hasValue': True}]}

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode('utf-8')).hexdigest()

    def factory(self, endpoint):
        self.assertEqual(endpoint, 'http://127.0.0.1:9222')
        client = FakeCDP(self)
        self.clients.append(client)
        return client

    def execute(self):
        self.bundle_path.write_text(json.dumps(self.bundle), encoding='utf-8')
        self.job_path.write_text(json.dumps(self.job), encoding='utf-8')
        return run_task(self.job_path, self.output, client_factory=self.factory)

    def action(self, action='click', capture='cap-query', **extra):
        self.job.update(operation='act', captureId=capture, action=action, purposeScope='read-only', **extra)
        self.observed = {'status': 'completed', 'actionAttempted': True,
                         'evidence': {'action': action, 'actionDispatched': True}}

    def test_exact_target_and_file_result_do_not_expose_private_url(self):
        result = self.execute()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.clients[0].connected['id'], 'selected-target')
        self.assertTrue(self.clients[0].closed)
        self.assertEqual(result['page']['url'], 'https://example.test/orders')
        self.assertEqual(result['page']['urlSha256'], self.digest(PAGE))
        self.assertNotIn('DO_NOT_PERSIST', self.output.read_text(encoding='utf-8'))

    def test_missing_or_duplicate_target_never_uses_same_url_fallback(self):
        for pages in ([self.pages[0]], [self.pages[1], self.pages[1]]):
            with self.subTest(pages=pages):
                self.pages = pages
                result = self.execute()
                self.assertEqual(result['error']['code'], 'target-not-found')
                self.assertIsNone(self.clients[-1].connected)
                self.assertEqual(self.clients[-1].calls, [])
                self.assertTrue(self.clients[-1].closed)

    def test_url_route_change_blocks_action_and_explicit_fingerprint_allows_read(self):
        self.live_url = PAGE.replace('#list', '#another')
        result = self.execute()
        self.assertEqual(result['error']['code'], 'page-changed')
        self.assertEqual(len(self.clients[-1].calls), 1)
        self.job['expectedUrlHash'] = self.digest(self.live_url)
        result = self.execute()
        self.assertEqual(result['status'], 'completed')

    def test_external_endpoint_and_boolean_versions_are_rejected_before_client(self):
        for change in ('endpoint', 'job-version', 'intent-version'):
            with self.subTest(change=change):
                original_bundle, original_job = copy.deepcopy(self.bundle), copy.deepcopy(self.job)
                if change == 'endpoint':self.bundle['connection']['endpoint'] = 'https://external.example.test:9222'
                elif change == 'job-version':self.job['schemaVersion'] = True
                else:self.bundle['taskIntent']['contractVersion'] = True
                self.assertEqual(self.execute()['status'], 'blocked')
                self.assertEqual(self.clients, [])
                self.bundle, self.job = original_bundle, original_job

    def test_action_scope_allowed_action_and_typed_parameter_guard(self):
        cases = [({'purposeScope': 'business-write'}, 'purpose-not-authorized'),
                 ({'action': 'fill'}, 'action-not-allowed'),
                 ({'captureId': 'cap-input', 'action': 'fill'}, 'missing-action-value'),
                 ({'captureId': 'cap-missing'}, 'invalid-action')]
        for updates, code in cases:
            with self.subTest(code=code):
                self.action()
                self.job.update(updates)
                self.assertEqual(self.execute()['error']['code'], code)
                self.assertEqual(self.clients, [])

    def test_same_action_job_is_executed_once_and_values_are_not_journaled(self):
        secret = 'VALUE_ONLY_IN_EXPLICIT_JOB_NOT_IN_ANY_RESULT'
        self.action('fill', 'cap-input', value=secret)
        self.observed['evidence']['valueMatchesRequested'] = True
        first = self.execute()
        second = self.execute()
        self.assertEqual(first['status'], 'completed')
        self.assertFalse(first['businessSuccessVerified'])
        self.assertTrue(second['duplicate'])
        self.assertEqual(len(self.clients), 1)
        for path in (self.learning / '.executions').iterdir():
            self.assertNotIn(secret, path.read_text(encoding='utf-8'))
        self.assertNotIn(secret, self.output.read_text(encoding='utf-8'))

    def test_changed_content_same_action_file_is_blocked(self):
        self.action()
        self.execute()
        self.job['purposeScope'] = 'reversible'
        result = self.execute()
        self.assertEqual(result['error']['code'], 'job-was-modified')
        self.assertEqual(len(self.clients), 1)

    def test_disconnect_after_submission_is_unknown_and_never_submitted_again(self):
        self.action()
        self.failure = True
        first = self.execute()
        second = self.execute()
        self.assertEqual(first['status'], 'unknown')
        self.assertTrue(first['actionAttempted'])
        self.assertEqual(second['status'], 'unknown')
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(len(self.clients[0].calls), 2)
        self.assertNotIn('RAW_PRIVATE', self.output.read_text(encoding='utf-8'))

    def test_pending_exclusive_claim_blocks_a_subsequent_invocation(self):
        self.action()
        self.execute()
        journal = next((self.learning / '.executions').glob('*.json'))
        state = json.loads(journal.read_text(encoding='utf-8'))
        state.update(phase='executing', result=None, actionAttempted=True)
        journal.write_text(json.dumps(state), encoding='utf-8')
        result = self.execute()
        self.assertEqual(result['status'], 'unknown')
        self.assertTrue(result['duplicate'])
        self.assertEqual(len(self.clients), 1)

    def test_two_processes_submit_the_same_action_job_only_once(self):
        self.action()
        self.bundle_path.write_text(json.dumps(self.bundle), encoding='utf-8')
        self.job_path.write_text(json.dumps(self.job), encoding='utf-8')
        marker = self.learning / 'fake-dispatches.txt'
        code = '''import json,sys,time
from cdp_task import run_task
class Client:
 def __init__(self, endpoint):self.count=0
 def list_pages(self):return [{"id":"selected-target","type":"page","url":sys.argv[4]}]
 def connect(self, target):pass
 def close(self):pass
 def call(self, method, params, timeout=10):
  self.count+=1
  if self.count==1:return {"result":{"value":{"url":sys.argv[4],"title":"Fixture"}}}
  with open(sys.argv[3],"a",encoding="utf-8") as stream:stream.write("dispatch\\n")
  time.sleep(.2)
  return {"result":{"value":{"status":"completed","actionAttempted":True,"evidence":{"actionDispatched":True}}}}
print(json.dumps(run_task(sys.argv[1],sys.argv[2],client_factory=Client)))
'''
        processes = [subprocess.Popen([sys.executable, '-c', code, str(self.job_path),
                      str(self.learning / (str(index) + '-result.json')), str(marker), PAGE],
                      cwd=str(SCRIPT.parent), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for index in range(2)]
        outcomes = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            outcomes.append(json.loads(stdout))
        self.assertEqual(marker.read_text(encoding='utf-8').splitlines(), ['dispatch'])
        self.assertEqual(sum(result.get('duplicate') is True for result in outcomes), 1)
        self.assertTrue(all(result['status'] in {'completed', 'unknown'} for result in outcomes))

    def test_inspect_can_be_repeated_without_action_journal(self):
        self.execute();self.execute()
        self.assertEqual(len(self.clients), 2)
        self.assertFalse((self.learning / '.executions').exists())

    def test_invalid_locator_or_multi_match_receipts_remain_blocked_no_attempt(self):
        self.action()
        self.observed = {'status': 'blocked', 'actionAttempted': False,
                         'error': {'code': 'control-not-located', 'message': 'fixture'},
                         'controls': [{'captureId': 'cap-query', 'status': 'ambiguous', 'matchCount': 2}]}
        result = self.execute()
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(result['actionAttempted'])

    def test_output_and_input_are_confined_to_learning(self):
        self.bundle_path.write_text(json.dumps(self.bundle), encoding='utf-8')
        self.job_path.write_text(json.dumps(self.job), encoding='utf-8')
        outside = self.root / 'outside.json'
        result = run_task(self.job_path, outside, client_factory=self.factory)
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(outside.exists())
        original = self.job_path.read_bytes()
        result = run_task(self.job_path, self.job_path, client_factory=self.factory)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(self.job_path.read_bytes(), original)
        outside.write_text(json.dumps(self.job), encoding='utf-8')
        result = run_task(outside, self.output, client_factory=self.factory)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(self.clients, [])

    def test_post_action_location_is_returned_as_hash_without_literal_query(self):
        self.action()
        after = PAGE.replace('#list', '#results')
        self.observed['_pageLocation'] = {'url': after, 'title': 'Results'}
        result = self.execute()
        self.assertEqual(result['page']['urlSha256'], self.digest(after))
        self.assertNotIn('_pageLocation', result)
        self.assertNotIn('DO_NOT_PERSIST', json.dumps(result))


NODE_FIXTURE = r'''
const fs = require('fs'), vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const events = [];
class Base {
 constructor(spec) { Object.assign(this, {labels:[],readOnly:false,checked:false,textContent:'Query'}, spec); this.tagName=this.tagName||'BUTTON';this.attributes=this.attributes||{};this.type=this.attributes.type||'text';this._value=this.initialValue||''; }
 getAttribute(key){return this.attributes[key]||null;} hasAttribute(key){return key in this.attributes;}
 getBoundingClientRect(){return {width:this.hidden?0:100,height:20};}
 closest(selector){if(selector==='form')return null;return selector.split(',').some(part=>part.toLowerCase()===this.tagName.toLowerCase()||part==='[hidden]'&&this.hidden)?this:null;}
 matches(selector){return selector.split(',').some(part=>part===':disabled'?Boolean(this.disabled):part==='[contenteditable]'?false:part==='input[type="checkbox"]'?this.tagName==='INPUT'&&this.type==='checkbox':part==='input[type="radio"]'?this.tagName==='INPUT'&&this.type==='radio':part.toLowerCase()===this.tagName.toLowerCase());}
 dispatchEvent(event){events.push(event.type);return true;}
 click(){events.push('click');if(this.type==='checkbox')this.checked=!this.checked;}
}
class Input extends Base {} Object.defineProperty(Input.prototype,'value',{get(){return this._value},set(value){this._value=value}});
class Area extends Input {} Object.defineProperty(Area.prototype,'value',Object.getOwnPropertyDescriptor(Input.prototype,'value'));
class Select extends Input {} Object.defineProperty(Select.prototype,'value',Object.getOwnPropertyDescriptor(Input.prototype,'value'));
const specs=input.elements||[{tagName:'BUTTON',attributes:{id:'query'}}];
const elements=specs.map(spec=>new ({INPUT:Input,TEXTAREA:Area,SELECT:Select}[spec.tagName]||Base)(spec));
const document={title:'Fixture',body:{},getElementById(id){return elements.find(element=>element.attributes.id===id)||null;},querySelectorAll(selector){if(selector==='[[')throw Error('invalid css');if(selector==='button,a,input,textarea,select,[role]')return elements;if(selector[0]==='#')return elements.filter(element=>element.attributes.id===selector.slice(1));if(selector==='button')return elements.filter(element=>element.tagName==='BUTTON');return [];},createTreeWalker(){const nodes=(input.textNodes||[]).map(spec=>({textContent:spec.text,parentElement:new Base({tagName:spec.tagName||'P',hidden:spec.hidden,attributes:{}})}));let index=-1;return{currentNode:null,nextNode(){this.currentNode=nodes[++index];return Boolean(this.currentNode)}};}};
const location={href:input.url||'https://example.test/orders'};
const context={document,location,NodeFilter:{SHOW_TEXT:4},HTMLInputElement:Input,HTMLTextAreaElement:Area,HTMLSelectElement:Select,Event:class{constructor(type){this.type=type;}},getComputedStyle(){return{display:'block',visibility:'visible',opacity:'1'}}};
const result=vm.runInNewContext('('+fs.readFileSync(process.argv[1],'utf8')+')('+JSON.stringify(input.task)+')',context);
delete result._pageLocation;
process.stdout.write(JSON.stringify({result,events}));
'''


@unittest.skipUnless(shutil.which('node'), 'Local Node runtime unavailable for fixed DOM fixture')
class ScriptTests(unittest.TestCase):
    def execute(self, action=None, elements=None, selectors=None, text_nodes=None, **extra):
        task = {'operation': 'act' if action else 'inspect', 'expectedLiveUrl': 'https://example.test/orders',
                'captureId': 'cap-query', 'action': action,
                'captures': [{'captureId': 'cap-query', 'capture': {
                    'control': {'locatorCandidates': [], 'selectors': selectors or [{'strategy': 'css', 'value': '#query'}]},
                    'annotation': {'allowedActions': ['read', 'click', 'fill', 'select', 'check']}}}], **extra}
        fixture = {'task': task}
        if elements is not None:fixture['elements'] = elements
        if text_nodes is not None:fixture['textNodes'] = text_nodes
        run = subprocess.run([shutil.which('node'), '-e', NODE_FIXTURE, str(SCRIPT)],
                             input=json.dumps(fixture), text=True, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_fixed_template_uses_nonempty_selectors_and_returns_locator_counts(self):
        receipt = self.execute()
        self.assertEqual(receipt['result']['controls'][0]['status'], 'located')
        self.assertEqual(receipt['result']['controls'][0]['matchCount'], 1)
        self.assertEqual(receipt['events'], [])

    def test_bad_ambiguous_hidden_disabled_and_sensitive_controls_do_not_click(self):
        cases = [({}, [{'strategy': 'css', 'value': '[['}], 'invalid-locator'),
                 ({'elements': [{'tagName': 'BUTTON', 'attributes': {'id': 'query'}}]*2}, None, 'ambiguous'),
                 ({'elements': [{'tagName': 'BUTTON', 'hidden': True, 'attributes': {'id': 'query'}}]}, None, None),
                 ({'elements': [{'tagName': 'BUTTON', 'disabled': True, 'attributes': {'id': 'query'}}]}, None, None),
                 ({'elements': [{'tagName': 'INPUT', 'attributes': {'id': 'query', 'type': 'password'}}]}, None, 'sensitive')]
        for fixture, selectors, status in cases:
            with self.subTest(status=status, fixture=fixture):
                receipt = self.execute('click', selectors=selectors, **fixture)
                self.assertEqual(receipt['result']['status'], 'blocked')
                self.assertFalse(receipt['result']['actionAttempted'])
                self.assertEqual(receipt['events'], [])
                if status:self.assertEqual(receipt['result']['controls'][0]['status'], status)

    def test_fill_displays_only_matching_boolean_and_standard_frontend_events(self):
        receipt = self.execute('fill', elements=[{'tagName': 'INPUT', 'initialValue': 'OLD_PRIVATE_VALUE',
                                                 'attributes': {'id': 'query'}}], value='NEW_PRIVATE_VALUE')
        self.assertEqual(receipt['result']['status'], 'completed')
        self.assertTrue(receipt['result']['evidence']['valueMatchesRequested'])
        self.assertEqual(receipt['events'], ['input', 'change'])
        self.assertNotIn('PRIVATE_VALUE', json.dumps(receipt))

    def test_click_occurs_once_and_does_not_claim_business_success(self):
        receipt = self.execute('click')
        self.assertEqual(receipt['events'], ['click'])
        self.assertTrue(receipt['result']['actionAttempted'])
        self.assertFalse(receipt['result']['businessSuccessVerified'])

    def test_check_requires_explicit_state_and_does_not_toggle_matching_state(self):
        elements = [{'tagName': 'INPUT', 'checked': True, 'attributes': {'id': 'query', 'type': 'checkbox'}}]
        receipt = self.execute('check', elements=elements, value=True)
        self.assertEqual(receipt['events'], [])
        self.assertTrue(receipt['result']['evidence']['checkedMatchesRequested'])
        receipt = self.execute('check', elements=elements, value=False)
        self.assertEqual(receipt['events'], ['click'])
        self.assertTrue(receipt['result']['evidence']['checkedMatchesRequested'])

    def test_id_and_role_name_candidates_use_actual_matching(self):
        for locator in ({'strategy': 'id', 'value': 'query'},
                        {'strategy': 'role', 'role': 'button', 'name': 'Query'}):
            with self.subTest(locator=locator):
                receipt = self.execute(selectors=[locator])
                self.assertEqual(receipt['result']['controls'][0]['status'], 'located')
                self.assertEqual(receipt['events'], [])

    def test_read_page_is_bounded_and_excludes_input_and_hidden_text(self):
        receipt = self.execute(operation='read-page', text_nodes=[
            {'tagName': 'INPUT', 'text': 'ORIGINAL_INPUT_SECRET'},
            {'tagName': 'TEXTAREA', 'text': 'ORIGINAL_TEXTAREA_SECRET'},
            {'tagName': 'SCRIPT', 'text': 'INTERNAL_SCRIPT_SECRET'},
            {'tagName': 'P', 'hidden': True, 'text': 'HIDDEN_SECRET'},
            {'tagName': 'P', 'text': 'Readable output ' + 'X' * 4000}])
        evidence = receipt['result']['evidence']
        self.assertTrue(evidence['truncated'])
        self.assertLessEqual(len(evidence['text']), 3000)
        self.assertNotIn('SECRET', evidence['text'])
        self.assertEqual(receipt['events'], [])

    def test_select_requires_existing_enabled_option_and_does_not_echo_value(self):
        elements = [{'tagName': 'SELECT', 'attributes': {'id': 'query'},
                     'options': [{'value': 'PARAMETER_ONLY', 'disabled': False}]}]
        receipt = self.execute('select', elements=elements, value='PARAMETER_ONLY')
        self.assertTrue(receipt['result']['evidence']['selectionMatchesRequested'])
        self.assertNotIn('PARAMETER_ONLY', json.dumps(receipt))
        self.assertEqual(receipt['events'], ['input', 'change'])
        receipt = self.execute('select', elements=elements, value='MISSING')
        self.assertEqual(receipt['result']['status'], 'blocked')
        self.assertEqual(receipt['events'], [])

    def test_route_changes_inside_the_action_call_prevent_dispatch(self):
        receipt = self.execute('click', expectedLiveUrl='https://example.test/wrong')
        self.assertEqual(receipt['result']['status'], 'blocked')
        self.assertFalse(receipt['result']['actionAttempted'])
        self.assertEqual(receipt['events'], [])


if __name__ == '__main__':
    unittest.main()
