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


class Tree(Widget):
    def __init__(self):
        super().__init__()
        self.rows = {}
        self.selected = ()

    def get_children(self):
        return tuple(self.rows)

    def delete(self, *items):
        for item in items:
            self.rows.pop(item, None)
        self.selected = tuple(item for item in self.selected if item in self.rows)

    def insert(self, parent, location, iid, values):
        self.rows[iid] = values

    def selection(self):
        return self.selected

    def selection_set(self, selected):
        self.selected = (selected,)

    def focus(self, selected):
        pass

    def see(self, selected):
        pass


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
        picker.record_tree = Tree()
        picker.records = {record['captureId']: record for record in self.workspace.list_captures()}
        picker._page_records = []
        picker.sequence_ordered = Value(False)
        picker.selection_status = Value('')
        picker.status = Value('')
        picker.saved_status = Value('')
        picker.editor_window = None
        picker.exporting = picker.sending = picker.closing = picker.connecting = False
        picker.capture_enabled = False
        picker.pending_count = 0
        picker._pending_save_count = lambda: picker.pending_count
        picker._workspace = lambda: self.workspace
        picker._queue_fit = lambda *args: None
        picker.logs = []
        picker._log = picker.logs.append
        for name in ('edit_record_button', 'delete_record_button', 'move_up_button',
                     'move_down_button', 'sequence_check', 'connect_button',
                     'disconnect_button', 'send_button', 'export_button'):
            setattr(picker, name, Widget())
        picker._update_saved_status()
        return picker

    def test_selected_page_lists_only_its_controls_and_resets_foreign_selection(self):
        picker = self.picker
        self.assertEqual(set(picker.record_tree.rows), {'cap-first', 'cap-second'})
        picker.record_tree.selection_set('cap-second')
        picker.page_combo.index = 1
        picker._update_saved_status()
        self.assertEqual(list(picker.record_tree.rows), ['cap-other'])
        self.assertEqual(picker._selected_record()['captureId'], 'cap-other')
        self.assertIn('1 项', picker.saved_status.get())
        picker.page_combo.index = -1
        picker._update_saved_status()
        self.assertEqual(picker.record_tree.rows, {})
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
        self.assertEqual(self.picker.record_tree.rows['cap-first'][1], '订单编号')
        self.assertEqual(self.picker._selected_record()['captureId'], 'cap-first')
        self.assertEqual(self.workspace.load_capture('cap-other')['annotation']['name'], '客户')

    def test_delete_requires_confirmation_refreshes_list_and_preserves_exported_snapshot(self):
        picker = self.picker
        bundle = self.workspace.export_page_bundle(self.page)
        path = Path(bundle['bundlePath'])
        before = path.read_bytes()
        picker.record_tree.selection_set('cap-first')
        with patch.object(app.messagebox, 'askyesno', return_value=False) as confirm:
            picker.delete_selected_record()
        confirm.assert_called_once()
        self.assertIn('cap-first', picker.record_tree.rows)
        with patch.object(app.messagebox, 'askyesno', return_value=True):
            picker.delete_selected_record()
        self.assertNotIn('cap-first', picker.record_tree.rows)
        self.assertNotIn('cap-first', picker.records)
        self.assertEqual([record['captureId'] for record in self.workspace.list_page_captures(self.page)], ['cap-second'])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(json.loads(before)['captureCount'], 2)
        self.assertEqual(self.workspace.load_capture('cap-other')['captureId'], 'cap-other')

    def test_move_and_order_declaration_survive_controller_reload_and_export(self):
        picker = self.picker
        picker.record_tree.selection_set('cap-second')
        picker.move_selected_record(-1)
        self.assertEqual(list(picker.record_tree.rows), ['cap-second', 'cap-first'])
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
                for name in ('edit_record_button', 'delete_record_button', 'move_up_button', 'move_down_button', 'sequence_check'):
                    self.assertEqual(getattr(picker, name).options['state'], 'disabled')
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
            self.picker.edit_selected_record()
        self.root.update_idletasks()
        return self.picker.editor_window

    def test_native_tree_editor_save_delete_and_unsaved_editor_barrier(self):
        picker = self.picker
        self.assertEqual(picker.record_tree.get_children(), ('cap-first', 'cap-second'))
        picker.record_tree.selection_set('cap-first')
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
        self.assertEqual(picker.record_tree.item('cap-first', 'values')[1], '订单编号')
        with patch.object(app.messagebox, 'askyesno', return_value=True):
            picker.delete_selected_record()
        self.assertEqual(picker.record_tree.get_children(), ('cap-second',))
        self.assertEqual(Path(bundle['bundlePath']).read_bytes(), snapshot)

    def test_native_order_buttons_freeze_and_expand_collapse_layout(self):
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
        picker.record_tree.selection_set('cap-second')
        picker._record_selection_changed()
        picker.move_up_button.invoke()
        self.assertEqual(picker.record_tree.get_children(), ('cap-second', 'cap-first'))
        self.assertTrue(picker.sequence_ordered.get())
        for state in ('sending', 'exporting', 'pending_count'):
            if state == 'pending_count':
                previous_pending = picker._pending_save_count
                picker._pending_save_count = lambda: 1
            else:
                setattr(picker, state, True)
            picker._controls()
            for widget in (picker.edit_record_button, picker.delete_record_button,
                           picker.move_up_button, picker.move_down_button, picker.sequence_check):
                self.assertTrue(widget.instate(('disabled',)))
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


if __name__ == '__main__':
    unittest.main()
