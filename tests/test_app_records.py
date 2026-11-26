"""Record-management tests with fakes/withdrawn native widgets; no external apps."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from workspace import Workspace


class Value:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Widget:
    def __init__(self):
        self.options = {}

    def configure(self, **options):
        self.options.update(options)

    def winfo_exists(self):
        return True


class Combo(Widget):
    def __init__(self):
        super().__init__()
        self.index = 0

    def current(self):
        return self.index


class RecordList(Widget):
    def __init__(self):
        super().__init__()
        self.records = {}
        self.selected = ()
        self.busy = self.ordered = False

    def set_records(self, records, selected_id=None):
        self.records = {record['captureId']: record for record in records}
        previous = (self.selected or (None,))[0]
        selected = (selected_id if selected_id in self.records else previous
                    if previous in self.records else next(iter(self.records), None))
        self.selected = (selected,) if selected is not None else ()

    def selection(self):
        return self.selected

    def select(self, selected):
        self.selected = (selected,) if selected in self.records else ()

    def set_busy(self, value):
        self.busy = value

    def set_ordered(self, value):
        self.ordered = value


class AppRecordTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='picker-app-records-')
        self.workspace = Workspace(Path(self.temporary.name))
        self.page = 'https://example.test/orders'
        self.other = 'https://example.test/customers'
        self.first = self.workspace.save_capture(self.card('cap-first', self.page, '编号'))
        self.second = self.workspace.save_capture(self.card('cap-second', self.page, '查询'))
        self.foreign = self.workspace.save_capture(self.card('cap-other', self.other, '客户'))
        self.picker = self.controller()

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def card(capture_id, page, name):
        return {'captureId': capture_id, 'page': {'url': page},
                'control': {'tagName': 'button', 'text': name,
                            'selectors': [{'strategy': 'css', 'value': '#' + capture_id, 'matchCount': 1}],
                            'bounds': {'x': 10, 'y': 20, 'width': 100, 'height': 30}},
                'annotation': {'name': name, 'description': '功能：' + name,
                               'allowedActions': ['read'], 'preconditions': '原前置条件',
                               'expectedResult': '原预期结果'}}

    def controller(self):
        picker = app.PickerApp.__new__(app.PickerApp)
        picker.root = SimpleNamespace()
        picker.client = None
        picker.current_target = None
        picker.pages = [{'id': 'orders', 'url': self.page}, {'id': 'customers', 'url': self.other}]
        picker.page_combo = Combo()
        picker.record_list = RecordList()
        picker.records = {record['captureId']: record for record in self.workspace.list_captures()}
        picker._page_records = []
        picker.sequence_ordered = Value(False)
        picker.status = Value('')
        picker.saved_status = Value('')
        picker.records_heading = Value('')
        picker.editor_window = None
        picker.exporting = picker.sending = picker.closing = picker.connecting = False
        picker.capture_enabled = False
        picker.pending_count = 0
        picker._pending_save_count = lambda: picker.pending_count
        picker._workspace = lambda: self.workspace
        picker._queue_fit = lambda *args: None
        picker.logs = []
        picker._log = picker.logs.append
        for name in ('sequence_check', 'connect_button',
                     'disconnect_button', 'send_button', 'export_button'):
            setattr(picker, name, Widget())
        picker._update_saved_status()
        return picker

    def test_selected_page_lists_only_its_controls_and_resets_foreign_selection(self):
        picker = self.picker
        self.assertEqual(set(picker.record_list.records), {'cap-first', 'cap-second'})
        picker.record_list.select('cap-second')
        picker.page_combo.index = 1
        picker._update_saved_status()
        self.assertEqual(list(picker.record_list.records), ['cap-other'])
        self.assertEqual(picker._selected_record()['captureId'], 'cap-other')
        self.assertIn('1 项', picker.saved_status.get())
        picker.page_combo.index = -1
        picker._update_saved_status()
        self.assertEqual(picker.record_list.records, {})
        self.assertIsNone(picker._selected_record())

    def test_annotation_edit_preserves_page_and_all_locator_evidence(self):
        before = self.workspace.load_capture('cap-first')
        updated = self.picker._store_record_annotation('cap-first', {
            'name': '订单编号', 'description': '先填写订单编号',
            'preconditions': '必须已登录且知道编号', 'expectedResult': '编号出现在筛选条件',
            'allowedActions': ['read', 'fill']})
        for field in ('name', 'description', 'preconditions', 'expectedResult', 'allowedActions'):
            self.assertEqual(self.workspace.load_capture('cap-first')['annotation'][field], updated['annotation'][field])
        self.assertEqual(updated['control'], before['control'])
        self.assertEqual(updated['page'], before['page'])
        self.assertEqual(self.picker.record_list.records['cap-first']['annotation']['name'], '订单编号')
        self.assertEqual(self.picker._selected_record()['captureId'], 'cap-first')
        self.assertEqual(self.workspace.load_capture('cap-other')['annotation']['name'], '客户')

    def test_saved_capture_is_selected_only_when_it_belongs_to_the_current_page(self):
        picker = self.picker
        added = self.workspace.save_capture(self.card('cap-new', self.page, '新控件'))
        picker.records[added['captureId']] = added
        picker._update_saved_status(added['captureId'])
        self.assertEqual(picker.record_list.selection(), ('cap-new',))
        foreign = self.workspace.save_capture(self.card('cap-new-other', self.other, '另页控件'))
        picker.records[foreign['captureId']] = foreign
        picker._update_saved_status(foreign['captureId'])
        self.assertEqual(picker.record_list.selection(), ('cap-new',))
        self.assertNotIn('cap-new-other', picker.record_list.records)

    def test_delete_requires_confirmation_refreshes_list_and_preserves_exported_snapshot(self):
        picker = self.picker
        bundle = self.workspace.export_page_bundle(self.page)
        path = Path(bundle['bundlePath'])
        before = path.read_bytes()
        picker.record_list.select('cap-first')
        with patch.object(app.messagebox, 'askyesno', return_value=False) as confirm:
            picker.delete_selected_record()
        confirm.assert_called_once()
        self.assertIn('cap-first', picker.record_list.records)
        with patch.object(app.messagebox, 'askyesno', return_value=True):
            picker.delete_selected_record()
        self.assertNotIn('cap-first', picker.record_list.records)
        self.assertNotIn('cap-first', picker.records)
        self.assertEqual([record['captureId'] for record in self.workspace.list_page_captures(self.page)], ['cap-second'])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(json.loads(before)['captureCount'], 2)
        self.assertEqual(self.workspace.load_capture('cap-other')['captureId'], 'cap-other')

    def test_move_and_order_declaration_survive_controller_reload_and_export(self):
        picker = self.picker
        picker.record_list.select('cap-second')
        picker.move_selected_record(-1)
        self.assertEqual(list(picker.record_list.records), ['cap-second', 'cap-first'])
        self.assertTrue(picker.sequence_ordered.get())
        reopened = Workspace(self.workspace.root)
        self.assertEqual([record['captureId'] for record in reopened.list_page_captures(self.page)], ['cap-second', 'cap-first'])
        self.assertTrue(reopened.page_sequence(self.page)['ordered'])
        picker.sequence_ordered.set(False)
        picker.save_sequence_declaration()
        self.assertFalse(reopened.page_sequence(self.page)['ordered'])
        picker._refresh_record_list(picker._selected_page())
        self.assertFalse(picker.sequence_ordered.get())
        bundle = reopened.export_page_bundle(self.page)
        payload = json.loads(Path(bundle['bundlePath']).read_text(encoding='utf-8'))
        self.assertEqual([record['captureId'] for record in payload['captures']], ['cap-second', 'cap-first'])
        self.assertEqual([record['captureId'] for record in reopened.list_page_captures(self.other)], ['cap-other'])

    def test_open_editor_blocks_both_copy_and_send_without_starting_background_work(self):
        picker = self.picker
        editor = SimpleNamespace(lift=lambda: None)
        picker.editor_window = editor
        picker._background = lambda action: self.fail('An open editor must not start background work')
        for send in (False, True):
            with self.subTest(send=send):
                picker._begin_page_task(send=send)
                self.assertIn('保存或取消', picker.status.get())
                self.assertFalse(picker.exporting)
                self.assertFalse(picker.sending)

    def test_busy_state_disables_record_buttons_and_blocks_mutation_entry_points(self):
        picker = self.picker
        for state in ('exporting', 'sending', 'closing', 'pending_count'):
            with self.subTest(state=state):
                setattr(picker, state, True)
                before = copy.deepcopy(self.workspace.load_capture('cap-first'))
                order = [record['captureId'] for record in self.workspace.list_page_captures(self.page)]
                picker._record_selection_changed()
                self.assertTrue(picker.record_list.busy)
                self.assertEqual(picker.sequence_check.options['state'], 'disabled')
                with patch.object(app.tk, 'Toplevel', side_effect=AssertionError('No editor while busy')), \
                     patch.object(app.messagebox, 'askyesno', side_effect=AssertionError('No deletion while busy')):
                    picker.edit_selected_record()
                    picker.delete_selected_record()
                    picker.move_selected_record(1)
                    picker.save_sequence_declaration()
                self.assertEqual(self.workspace.load_capture('cap-first'), before)
                self.assertEqual([record['captureId'] for record in self.workspace.list_page_captures(self.page)], order)
                setattr(picker, state, False)

    def test_editor_commit_rechecks_busy_state_when_save_started_after_editor_opened(self):
        picker = self.picker
        picker.editor_window = SimpleNamespace(lift=lambda: None)
        for state in ('exporting', 'sending', 'closing', 'pending_count'):
            with self.subTest(state=state):
                before = self.workspace.load_capture('cap-first')
                setattr(picker, state, True)
                try:
                    picker._store_record_annotation('cap-first', {'name': '必须被拦住', 'description': '不应写入'})
                except (ValueError, RuntimeError):
                    pass
                finally:
                    setattr(picker, state, False)
                self.assertEqual(self.workspace.load_capture('cap-first'), before)

    def test_rejected_sequence_toggle_restores_persisted_checkbox_value(self):
        picker = self.picker
        self.assertFalse(self.workspace.page_sequence(self.page)['ordered'])
        picker.pending_count = 1
        picker.sequence_ordered.set(True)  # A Tk checkbutton changes its variable before command.
        picker.save_sequence_declaration()
        self.assertFalse(self.workspace.page_sequence(self.page)['ordered'])
        self.assertFalse(picker.sequence_ordered.get())


class HiddenTkRecordTests(unittest.TestCase):
    """Native widgets stay withdrawn; no desktop application is driven."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='picker-hidden-records-')
        self.root = app.tk.Tk()
        self.root.withdraw()
        self.picker = app.PickerApp(self.root, workspace_path=Path(self.temporary.name))
        # Allocate real child widths without mapping the withdrawn host.
        body = self.picker._layout_specs[str(self.root)][1]
        body.pack_forget()
        body.place(x=0, y=0, width=self.picker.palette_width,
                   height=self.root.winfo_screenheight())
        self.workspace = self.picker._workspace()
        self.page = 'https://example.test/orders'
        for capture_id, name in (('cap-first', '编号'), ('cap-second', '查询')):
            self.workspace.save_capture(AppRecordTests.card(capture_id, self.page, name))
        self.picker.pages = [{'id': 'fixture-orders', 'url': self.page}]
        self.picker.page_combo.configure(values=('虚构订单页',))
        self.picker.page_combo.current(0)
        self.picker.reload_records()
        self.root.update_idletasks()

    def tearDown(self):
        self.picker.closed = True
        for specification in list(self.picker._layout_specs.values()):
            self.picker._forget_window_layout(specification[0])
        for callback in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            try:
                # Keep callback command ownership with its actual root or
                # Toplevel; root.after_cancel would delete a child's Tcl
                # command without removing it from that child's registry.
                self.root.tk.call('after', 'cancel', callback)
            except app.tk.TclError:
                pass
        self.root.destroy()
        self.temporary.cleanup()

    @staticmethod
    def descendants(widget):
        result = []
        for child in widget.winfo_children():
            result.append(child)
            result.extend(HiddenTkRecordTests.descendants(child))
        return result

    def open_hidden_editor(self):
        original = app.tk.Toplevel

        def hidden(*args, **kwargs):
            window = original(*args, **kwargs)
            window.withdraw()
            # A withdrawn fixture cannot acquire a visible-window grab. The
            # record editor and its native entry/text/button widgets are real.
            window.grab_set = lambda: None
            return window

        with patch.object(app.tk, 'Toplevel', side_effect=hidden):
            capture_id = self.picker._selected_record()['captureId']
            self.picker.record_list.cards[capture_id]['buttons']['edit'].invoke()
        self.root.update_idletasks()
        return self.picker.editor_window

    def test_native_cards_editor_save_delete_and_unsaved_editor_barrier(self):
        picker = self.picker
        self.assertEqual(tuple(picker.record_list.cards), ('cap-first', 'cap-second'))
        picker.record_list.select('cap-first')
        before = self.workspace.load_capture('cap-first')
        bundle = self.workspace.export_page_bundle(self.page)
        snapshot = Path(bundle['bundlePath']).read_bytes()
        window = self.open_hidden_editor()
        self.assertEqual(window.state(), 'withdrawn')
        widgets = self.descendants(window)
        entry = next(widget for widget in widgets if isinstance(widget, app.ttk.Entry))
        entry.delete(0, 'end')
        entry.insert(0, '订单编号')
        text_fields = [widget for widget in widgets if isinstance(widget, app.tk.Text)]
        self.assertEqual(len(text_fields), 4)
        self.assertEqual(text_fields[-1].cget('state'), 'disabled')
        for field, value in zip(text_fields[:3], ('先填写编号', '必须登录', '编号作为筛选条件')):
            field.delete('1.0', 'end')
            field.insert('1.0', value)
        with patch.object(picker, '_background') as background:
            picker.export_page_task()
            picker.send_page_to_zcode()
            background.assert_not_called()
        save = next(widget for widget in widgets
                    if isinstance(widget, app.ttk.Button) and widget.cget('text') == '保存修改')
        save.invoke()
        self.assertIsNone(picker.editor_window)
        after = self.workspace.load_capture('cap-first')
        self.assertEqual(after['annotation']['name'], '订单编号')
        self.assertEqual(after['annotation']['description'], '先填写编号')
        self.assertEqual(after['annotation']['preconditions'], '必须登录')
        self.assertEqual(after['control'], before['control'])
        self.assertEqual(after['page'], before['page'])
        self.assertEqual(after['annotation']['allowedActions'], before['annotation']['allowedActions'])
        self.root.update_idletasks()
        self.assertEqual(picker.record_list.cards['cap-first']['titleText'], '订单编号')
        self.assertIn('订单编号', picker.record_list.cards['cap-first']['title'].cget('text'))
        with patch.object(app.messagebox, 'askyesno', return_value=True):
            picker.record_list.cards['cap-first']['menu'].invoke(3)
        self.assertEqual(tuple(picker.record_list.cards), ('cap-second',))
        self.assertEqual(Path(bundle['bundlePath']).read_bytes(), snapshot)

    def test_native_order_menu_freeze_and_expand_collapse_layout(self):
        picker = self.picker
        body = next(widget for widget in self.root.winfo_children() if isinstance(widget, app.ttk.Frame))
        picker._apply_fit(str(self.root))
        expanded = (body.winfo_reqwidth(), body.winfo_reqheight())
        picker.toggle_records()
        self.root.update_idletasks()
        picker._apply_fit(str(self.root))
        collapsed = (body.winfo_reqwidth(), body.winfo_reqheight())
        self.assertLess(collapsed[1], expanded[1])
        picker.toggle_records()
        picker.record_list.select('cap-second')
        picker._record_selection_changed()
        picker.record_list.cards['cap-second']['menu'].invoke(0)
        self.assertEqual(tuple(picker.record_list.cards), ('cap-second', 'cap-first'))
        self.assertTrue(picker.sequence_ordered.get())
        for state in ('sending', 'exporting', 'pending_count'):
            if state == 'pending_count':
                previous_pending = picker._pending_save_count
                picker._pending_save_count = lambda: 1
            else:
                setattr(picker, state, True)
            picker._controls()
            for widget in (list(picker.record_list.cards['cap-first']['buttons'].values())
                           + [picker.sequence_check]):
                self.assertEqual(str(widget.cget('state')), 'disabled')
            for index in (0, 1, 3):
                self.assertEqual(str(picker.record_list.cards['cap-first']['menu'].entrycget(index, 'state')),
                                 'disabled')
            if state == 'pending_count':
                picker._pending_save_count = previous_pending
            else:
                setattr(picker, state, False)
        picker._controls()
        window = self.open_hidden_editor()
        self.root.update_idletasks()
        editor_size = (window.winfo_reqwidth(), window.winfo_reqheight())
        editor_minimum = window.minsize()
        # A withdrawn Tk window may keep geometry 1x1 until its first map.
        # The configured minimum must contain the native widget request.
        self.assertGreaterEqual(editor_minimum[0], max(430, editor_size[0]))
        self.assertGreaterEqual(editor_minimum[1], max(480, editor_size[1]))
        self.assertEqual(self.root.state(), 'withdrawn')
        print('HIDDEN_TK_RECORD_LAYOUT ' + json.dumps({
            'expandedContent': expanded, 'collapsedContent': collapsed,
            'editorContent': editor_size, 'editorMinimum': editor_minimum}))

    def test_quick_editor_cancel_removes_its_idle_and_delayed_layout_callbacks(self):
        picker = self.picker
        window = self.open_hidden_editor()
        key = str(window)
        picker._queue_fit(window)
        owned = {picker._layout_jobs[key], picker._layout_delays[key]}
        pending = set(self.root.tk.splitlist(self.root.tk.call('after', 'info')))
        self.assertTrue(owned.issubset(pending))
        cancel = next(widget for widget in self.descendants(window)
                      if isinstance(widget, app.ttk.Button) and widget.cget('text') == '取消')
        cancel.invoke()
        self.assertIsNone(picker.editor_window)
        self.root.update_idletasks()
        for registry in (picker._layout_specs, picker._layout_jobs, picker._layout_delays):
            self.assertNotIn(key, registry)
        pending = set(self.root.tk.splitlist(self.root.tk.call('after', 'info')))
        self.assertFalse(owned & pending)

    def test_native_send_feedback_remains_visible_and_stops_its_progress_timer(self):
        picker = self.picker
        clock = [100.0]
        picker.send_feedback = app.SendFeedback(clock=lambda: clock[0])
        picker._feedback_workspace = str(self.workspace.root)
        revision = picker._page_revision()
        picker.send_feedback.select(revision)
        self.assertTrue(picker.send_feedback.begin(revision))
        picker.sending = True
        picker._controls()
        self.root.update_idletasks()
        timer = picker._send_timer_job
        self.assertIsNotNone(timer)
        self.assertTrue(picker._send_progress_running)
        self.assertTrue(picker.send_button.instate(('disabled',)))
        self.assertEqual(str(picker.page_combo.cget('state')), 'disabled')
        clock[0] += 5
        # Simulate Tcl consuming this callback before it invokes the handler.
        self.root.after_cancel(timer)
        picker._tick_send_feedback()
        self.assertEqual(picker.send_time.get(), '5 秒')
        picker.send_feedback.complete({'status': 'registered', 'indexRegistered': True,
                                       'sessionId': 'sess_native_fixture',
                                       'responseSummary': '已收到两个控件，等待说明。'})
        picker.sending = False
        picker._controls()
        self.root.update_idletasks()
        self.assertEqual(picker.send_title.get(), '发送完成')
        self.assertTrue(picker.send_button.instate(('disabled',)))
        self.assertEqual(picker.send_button.cget('text'), '本页已发送')
        self.assertFalse(picker._send_progress_running)
        self.assertIsNone(picker._send_timer_job)
        self.assertEqual(picker.send_progress.winfo_manager(), '')
        picker.status.set('Chrome 已刷新，此消息只属于浏览器状态。')
        self.root.update_idletasks()
        self.assertEqual(picker.send_title.get(), '发送完成')
        with patch.object(app.messagebox, 'showinfo') as dialog:
            picker.open_send_receipt()
        self.assertIn('已收到两个控件', dialog.call_args.args[1])
        picker._store_record_annotation('cap-first', {'name': '新名称', 'description': '新的用途'})
        self.assertTrue(picker.send_feedback.can_send(picker._page_revision()))
        self.assertFalse(picker.send_button.instate(('disabled',)))
        self.assertFalse(picker.send_feedback.can_send(revision))
        self.assertEqual(self.root.state(), 'withdrawn')


class HiddenTkPaletteDpiTests(unittest.TestCase):
    def test_main_palette_tools_and_height_at_five_dpi_scales(self):
        evidence = []
        for ratio in (1, 1.25, 1.5, 1.75, 2):
            with self.subTest(dpi=round(ratio * 100)), tempfile.TemporaryDirectory(prefix='picker-palette-dpi-') as temporary:
                root = app.tk.Tk()
                root.withdraw()
                original_scaling = float(root.tk.call('tk', 'scaling'))
                root.tk.call('tk', 'scaling', 96 / 72 * ratio)
                picker = None
                try:
                    picker = app.PickerApp(root, workspace_path=Path(temporary))
                    workspace = picker._workspace()
                    page = 'https://example.test/orders'
                    for capture_id in ('cap-long', 'cap-next'):
                        card = AppRecordTests.card(capture_id, page, '核对当前订单与客户状态的业务查询按钮名称' * 4)
                        card['annotation']['description'] = '先填入业务编号，再核对本项控件对应的页面位置、用途以及预期结果。' * 30
                        workspace.save_capture(card)
                    picker.pages = [{'id': 'fixture', 'url': page}]
                    picker.page_combo.configure(values=('虚构订单页',))
                    picker.page_combo.current(0)
                    body = picker._layout_specs[str(root)][1]
                    # Give the withdrawn host real allocation; never map it.
                    body.pack_forget()
                    body.place(x=0, y=0, width=picker.palette_width, height=root.winfo_screenheight())
                    picker.reload_records()
                    root.update_idletasks()
                    picker._apply_fit(str(root))
                    root.update_idletasks()
                    self.assertEqual(root.state(), 'withdrawn')
                    self.assertEqual(tuple(picker.record_list.cards), ('cap-long', 'cap-next'))
                    toolbar = picker.launch_button.master
                    actions = picker.connect_button.master
                    top_buttons = [widget for widget in toolbar.winfo_children()
                                   if isinstance(widget, app.ttk.Button)]
                    top_buttons.sort(key=lambda widget: widget.winfo_x())
                    self.assertEqual([button.cget('text') for button in top_buttons],
                                     ['启动 Chrome', '刷新', '记录', '日志'])
                    self.assertLess(toolbar.winfo_rooty(), picker.page_combo.winfo_rooty())
                    self.assertLess(picker.page_combo.winfo_rooty(), actions.winfo_rooty())
                    for row, buttons in ((toolbar, top_buttons),
                                         (actions, [picker.connect_button, picker.disconnect_button])):
                        boxes = []
                        for button in buttons:
                            x = button.winfo_rootx() - row.winfo_rootx()
                            y = button.winfo_rooty() - row.winfo_rooty()
                            box = (x, y, x + button.winfo_width(), y + button.winfo_height())
                            self.assertGreater(button.winfo_width(), 1)
                            self.assertGreaterEqual(box[0], 0)
                            self.assertGreaterEqual(box[1], 0)
                            self.assertLessEqual(box[2], row.winfo_width())
                            self.assertLessEqual(box[3], row.winfo_height())
                            boxes.append(box)
                        for index, left in enumerate(boxes):
                            for right in boxes[index + 1:]:
                                overlap = (min(left[2], right[2]) > max(left[0], right[0])
                                           and min(left[3], right[3]) > max(left[1], right[1]))
                                self.assertFalse(overlap, boxes)
                        self.assertLessEqual(row.winfo_rootx() - body.winfo_rootx() + row.winfo_width(), body.winfo_width())
                    expanded_height = body.winfo_reqheight()
                    safe_height = root.winfo_screenheight() - picker._dip(90)
                    self.assertLessEqual(expanded_height, safe_height)
                    self.assertLessEqual(root.minsize()[1], safe_height)
                    picker.toggle_records()
                    root.update_idletasks()
                    picker._apply_fit(str(root))
                    collapsed_height = body.winfo_reqheight()
                    self.assertLess(collapsed_height, expanded_height)
                    evidence.append({'dpi': round(ratio * 100), 'width': picker.palette_width,
                                     'expanded': expanded_height, 'collapsed': collapsed_height,
                                     'screenLimit': safe_height, 'viewport': picker.record_list.viewport_height()})
                finally:
                    if picker is not None:
                        picker.closed = True
                        for specification in list(picker._layout_specs.values()):
                            picker._forget_window_layout(specification[0])
                    for callback in root.tk.splitlist(root.tk.call('after', 'info')):
                        root.tk.call('after', 'cancel', callback)
                    root.tk.call('tk', 'scaling', original_scaling)
                    root.destroy()
        print('HIDDEN_PALETTE_DPI ' + json.dumps(evidence))


if __name__ == '__main__':
    unittest.main()
