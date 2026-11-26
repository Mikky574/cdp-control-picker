"""Pure local fake NDJSON servers: no real ZCode, model, UI, or SQLite."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zcode_appserver import ZCodeAppServer, AppServerError, AppServerBlocked, AppServerTimeout


FAKE = r'''
import json,os,pathlib,sys,time,threading
scenario=sys.argv[1];log=pathlib.Path(sys.argv[2]);sid='sess_fixture';mode='yolo';turn=None
ws={'workspacePath':os.getcwd(),'workspaceKey':os.getcwd()}
messages=[];reads=0;sent=0;finished=True;read_after_terminal=0;plan=scenario=='resume_plan_flag'
output_lock=threading.Lock()
def emit(value):
 with output_lock: print(json.dumps(value),flush=True)
def remember(value):
 with log.open('a',encoding='utf-8') as f:f.write(json.dumps(value)+'\n')
def reply(value,result=None,error=None):
 emit({'id':value['id'],**({'error':error} if error else {'result':result})})
def snapshot():
 active=scenario=='inflight_final' and sent and read_after_terminal<2
 return {'session':{'sessionId':sid,'workspace':ws,'mode':mode},'settings':{'permission':{'mode':mode},'mode':{'current':'plan' if scenario=='mode_conflict' else mode}},
         'projection':{'status':'running' if active else 'idle','currentTurnId':turn},
         'runtime':{'activeTurnId':turn} if active else {},'messages':messages[:]}
def notification(method,**params):emit({'method':method,'params':params})
def event(event_type,input_id=None,turn_id=None,session=None,**payload):
 if input_id is not None:payload['inputId']=input_id
 notification('session/event',sessionId=session or sid,turnId=turn_id or turn,type=event_type,payload=payload)
def complete(input_id,content):
 global finished
 if scenario=='wrong_events':
  event('turn.completed',input_id,session='sess_unrelated',resultType='cancelled')
  event('turn.completed','wrong-input',resultType='cancelled')
  event('turn.completed',input_id,turn_id='turn-wrong',resultType='cancelled')
  emit({'method':'process/mcpResourceSamples','params':[{'opaque':'unrelated monitor'}]})
  event('model.streaming',input_id,kind='reasoning_delta',delta='PRIVATE_REASONING_FIXTURE')
  event('model.streaming',input_id,kind='text_delta',delta='PRIVATE_HIDDEN_FIXTURE',metadata={'zcode':{'visibility':'model-only'}})
  for i in range(25):event('model.streaming',input_id,kind='text_delta',delta='x'*1100)
  event('model.streaming',input_id,kind='text_delta',delta='api_key=fixture-sensitive-key')
 if scenario!='old_snapshot':messages.append({'id':'assistant-'+str(sent),'role':'assistant','content':'Done '+str(sent)})
 finished=True
 if scenario=='telemetry_cancel':notification('v4/telemetry/event',sessionId=sid,inputId=input_id,turnId=turn,kind='turn.terminal',resultType='cancelled')
 event('turn.completed',input_id,resultType='success')
 if scenario!='turn_only':notification('state.updated',sessionId=sid,turnId=turn,inputId=input_id,scope='session',reason='prompt_completed')
if scenario=='nonjson':
 print('not NDJSON api_key=fixture-sensitive-key',flush=True)
else:
 emit({'id':'runtime-prefs','method':'session/requestRuntimePreferences','params':{}})
remember({'argv':sys.argv[3:],'cwd':os.getcwd()})
for line in sys.stdin:
 value=json.loads(line);method=value.get('method');p=value.get('params',{})
 remember({'method':method,'params':p} if method else {'reverseReply':value})
 if method is None:continue
 if method=='session/create':
  mode=p['mode']
  if scenario=='old_snapshot':messages[:]=[{'id':'old-user','role':'user','content':'same task'},{'id':'old-assistant','role':'assistant','content':'Old answer'}]
  s=snapshot()
  if scenario=='missing_workspace':s['session'].pop('workspace')
  reply(value,s)
 elif method=='session/list':
  saved=dict(ws)
  if scenario=='wrong_workspace':saved['workspacePath']=os.path.join(os.getcwd(),'other')
  reply(value,{'sessions':[] if scenario=='missing_session' else [{'sessionId':sid,'workspace':saved}]})
 elif method=='session/resume':
  if scenario=='resume_error':reply(value,error={'code':-32004,'message':'private source detail'})
  else:
   mode='edit' if scenario=='resume_change_mode' else 'yolo';reply(value,snapshot())
 elif method=='session/setMode':mode=p['mode'];plan=False;reply(value,snapshot())
 elif method=='session/subscribe':reply(value,{})
 elif method=='session/read':
  reads+=1
  if sent:
   read_after_terminal+=1
   if scenario=='read_timeout' and read_after_terminal==1:time.sleep(.15)
   if scenario=='cancel_during_read':notification('v4/telemetry/event',sessionId=sid,inputId=current_input,turnId=turn,kind='turn.terminal',resultType='cancelled')
  reply(value,snapshot())
 elif method=='session/send':
  sent+=1;turn='turn-'+str(sent);finished=False;read_after_terminal=0;current_input=p['inputId']
  if scenario!='old_snapshot':messages.append({'id':'user-'+str(sent),'role':'user','content':p['content']})
  reply(value,{})
  if scenario=='eof':break
  event('turn.started',p['inputId'])
  if scenario in ('permission','question'):
   emit({'id':'extra-interaction','method':'interaction/requestPermission' if scenario=='permission' else 'interaction/requestUserInput',
         'params':{'sessionId':sid,'requestId':'extra','toolName':'Bash','input':{'command':'fixture only'}}})
  elif scenario=='idle_only':
   messages.append({'id':'assistant-'+str(sent),'role':'assistant','content':'Unconfirmed answer'})
  elif scenario=='bad_terminal':event('turn.completed',p['inputId'],resultType='cancelled')
  elif scenario!='pending':complete(p['inputId'],p['content'])
 elif method=='probe':reply(value,{'ready':True,'planEnabled':plan})
 else:reply(value,error={'code':-32601,'message':'unknown fixture method'})
'''


class AppServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="picker-appserver-test-")
        self.root = Path(self.temp.name) / "workspace space & name"
        self.root.mkdir()
        self.script = Path(self.temp.name) / "fake appserver.py"
        self.script.write_text(FAKE, encoding="utf-8")
        self.clients = []
        self.counter = 0

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.temp.cleanup()

    def client(self, scenario="normal", timeout=2):
        self.counter += 1
        log = Path(self.temp.name) / ("calls-" + str(self.counter) + ".jsonl")
        client = ZCodeAppServer(self.root, [sys.executable, "-u", str(self.script), scenario, str(log)], timeout_seconds=timeout)
        client.READ_POLL_INTERVAL = .05
        client.READ_POLL_TIMEOUT = .05
        client.FINAL_READ_BUDGET = .5
        client.FINAL_READ_FIRST_TIMEOUT = .05
        client.fixture_log = log
        self.clients.append(client)
        return client

    @staticmethod
    def calls(client):
        return [json.loads(line) for line in client.fixture_log.read_text(encoding="utf-8").splitlines()]

    def methods(self, client):
        return [value.get("method") for value in self.calls(client) if value.get("method")]

    def test_create_uses_yolo_local_runtime_and_one_send_then_native_snapshot(self):
        original_environment = dict(os.environ)
        with self.client() as client:
            snapshot = client.bind()
            sid = snapshot["session"]["sessionId"]
            result = client.prompt(sid, "Read local task", input_id="input-1")
            self.assertEqual(result["messages"][-1]["content"], "Done 1")
            self.assertEqual(client.mode, "yolo")
            client.request("probe", {})
            calls = self.calls(client)
            startup = calls[0]
            self.assertEqual(startup["argv"], ["app-server", "--stdio", "--cwd", str(self.root)])
            created = next(value["params"] for value in calls if value.get("method") == "session/create")
            self.assertEqual(created["mode"], "yolo")
            self.assertEqual(created["persistence"], "immediate")
            self.assertNotIn("model", created)
            preferences = next(value["reverseReply"]["result"] for value in calls if value.get("reverseReply", {}).get("id") == "runtime-prefs")
            self.assertFalse(preferences["memoryEnabled"])
            self.assertEqual(self.methods(client).count("session/send"), 1)
            self.assertFalse(any(method.startswith("workspace/update") for method in self.methods(client)))
        self.assertEqual(dict(os.environ), original_environment)

    def test_resume_preserves_session_and_only_records_selected_workspace_mode_change(self):
        client = self.client("resume_change_mode")
        snapshot = client.bind("sess_fixture")
        self.assertEqual(snapshot["session"]["sessionId"], "sess_fixture")
        self.assertEqual(snapshot["settings"]["permission"]["mode"], "yolo")
        self.assertEqual(client.last_binding["permissionScope"], "selected-workspace-mode-preference")
        self.assertNotIn("session/create", self.methods(client))
        self.assertEqual(self.methods(client).count("session/setMode"), 1)

    def test_bind_reapplies_mode_even_when_legacy_base_already_matches(self):
        client = self.client("resume_plan_flag")
        client.bind("sess_fixture")
        self.assertEqual(self.methods(client).count("session/setMode"), 1)
        self.assertFalse(client.request("probe", {})["planEnabled"])
        self.assertTrue(client.last_binding["planResetByNativeModeSelection"])
        conflict = self.client("mode_conflict")
        with self.assertRaises(AppServerError):
            conflict.bind()
        self.assertNotIn("session/send", self.methods(conflict))

    def test_missing_failed_or_foreign_resume_never_creates_replacement(self):
        for scenario in ("missing_session", "resume_error", "wrong_workspace"):
            with self.subTest(scenario=scenario):
                client = self.client(scenario)
                with self.assertRaises(AppServerError):
                    client.bind("sess_fixture")
                self.assertNotIn("session/create", self.methods(client))

    def test_missing_workspace_snapshot_is_rejected(self):
        client = self.client("missing_workspace")
        with self.assertRaises(AppServerError):
            client.bind()
        self.assertNotIn("session/send", self.methods(client))

    def test_other_session_input_turn_and_hidden_stream_cannot_finish_or_leak(self):
        client = self.client("wrong_events")
        sid = client.bind()["session"]["sessionId"]
        events = []
        result = client.prompt(sid, "Current task", input_id="current", on_event=events.append)
        self.assertEqual(result["session"]["sessionId"], sid)
        serialized = json.dumps(events)
        self.assertNotIn("PRIVATE_REASONING_FIXTURE", serialized)
        self.assertNotIn("PRIVATE_HIDDEN_FIXTURE", serialized)
        self.assertNotIn("fixture-sensitive-key", serialized)
        self.assertLessEqual(sum(len(event.get("text", "")) for event in events), 16000)
        self.assertEqual(self.methods(client).count("session/send"), 1)

    def test_extra_permissions_and_questions_are_blocked_without_allow_or_second_send(self):
        for scenario in ("permission", "question"):
            with self.subTest(scenario=scenario):
                client = self.client(scenario)
                sid = client.bind()["session"]["sessionId"]
                with self.assertRaises(AppServerBlocked):
                    client.prompt(sid, "Task", input_id="one-input")
                client.request("probe", {})
                response = next(value["reverseReply"] for value in self.calls(client)
                                if value.get("reverseReply", {}).get("id") == "extra-interaction")
                self.assertIn("error", response)
                self.assertNotIn("result", response)
                self.assertEqual(self.methods(client).count("session/send"), 1)

    def test_idle_snapshot_alone_times_out_and_does_not_allow_resubmission(self):
        client = self.client("idle_only", timeout=.2)
        sid = client.bind()["session"]["sessionId"]
        with self.assertRaises(AppServerTimeout):
            client.prompt(sid, "Task", input_id="one-input")
        with self.assertRaises(AppServerError):
            client.prompt(sid, "Task", input_id="different-input")
        with self.assertRaises(AppServerError):
            client.bind(sid)
        self.assertEqual(self.methods(client).count("session/send"), 1)

    def test_successful_turn_terminal_can_finish_after_inflight_snapshot_settles(self):
        for scenario in ("turn_only", "inflight_final"):
            with self.subTest(scenario=scenario):
                client = self.client(scenario)
                sid = client.bind()["session"]["sessionId"]
                self.assertEqual(client.prompt(sid, "Task")["messages"][-1]["content"], "Done 1")
                self.assertEqual(self.methods(client).count("session/send"), 1)

    def test_read_timeout_has_independent_budget_and_retries_only_read(self):
        client = self.client("read_timeout", timeout=.1)
        sid = client.bind()["session"]["sessionId"]
        result = client.prompt(sid, "Task")
        self.assertEqual(result["messages"][-1]["content"], "Done 1")
        self.assertGreaterEqual(self.methods(client).count("session/read"), 3)
        self.assertEqual(self.methods(client).count("session/send"), 1)

    def test_native_telemetry_cancellation_and_queued_negative_cannot_be_success(self):
        for scenario in ("telemetry_cancel", "cancel_during_read"):
            with self.subTest(scenario=scenario):
                client = self.client(scenario)
                sid = client.bind()["session"]["sessionId"]
                with self.assertRaises(AppServerError):
                    client.prompt(sid, "Task")
                self.assertEqual(self.methods(client).count("session/send"), 1)

    def test_identical_prompt_requires_new_user_message_not_old_snapshot(self):
        client = self.client("old_snapshot")
        client.FINAL_READ_BUDGET = .2
        sid = client.bind()["session"]["sessionId"]
        with self.assertRaises(AppServerTimeout):
            client.prompt(sid, "same task")
        normal = self.client()
        sid = normal.bind()["session"]["sessionId"]
        normal.prompt(sid, "same task", input_id="first")
        second = normal.prompt(sid, "same task", input_id="second")
        self.assertEqual(second["messages"][-1]["content"], "Done 2")
        self.assertEqual(len(second["messages"]), 4)
        with self.assertRaises(AppServerError):
            normal.prompt(sid, "same task", input_id="second")
        self.assertEqual(self.methods(normal).count("session/send"), 2)

    def test_nonjson_eof_and_cancelled_terminal_are_failures_without_private_text(self):
        for scenario in ("nonjson", "eof", "bad_terminal"):
            with self.subTest(scenario=scenario):
                client = self.client(scenario)
                with self.assertRaises(AppServerError) as rejected:
                    sid = client.bind()["session"]["sessionId"]
                    client.prompt(sid, "Task")
                self.assertNotIn("fixture-sensitive-key", str(rejected.exception))
                self.assertLessEqual(self.methods(client).count("session/send"), 1)

    def test_callback_exception_and_close_wake_waiters_without_resending(self):
        normal = self.client()
        sid = normal.bind()["session"]["sessionId"]

        def broken_callback(event):
            raise RuntimeError("fixture callback failure")

        self.assertEqual(normal.prompt(sid, "Task", on_event=broken_callback)["messages"][-1]["content"], "Done 1")
        client = self.client("pending", timeout=5)
        sid = client.bind()["session"]["sessionId"]
        outcome = []

        def execute():
            try:
                outcome.append(client.prompt(sid, "Task"))
            except Exception as error:
                outcome.append(error)

        worker = threading.Thread(target=execute)
        worker.start()
        deadline = time.monotonic() + 2
        while "session/send" not in self.methods(client) and time.monotonic() < deadline:
            time.sleep(.01)
        client.close()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertIsInstance(outcome[0], AppServerError)
        self.assertIsNotNone(client.proc.poll())
        self.assertEqual(self.methods(client).count("session/send"), 1)


if __name__ == "__main__":
    unittest.main()
