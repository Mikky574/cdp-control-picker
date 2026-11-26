"""Single-line CaptureList checks stay withdrawn; no external UI or model."""
import itertools
import json
from pathlib import Path
import re
import sys
import time
import tkinter as tk
import _tkinter
from tkinter import font, ttk
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capture_list import CaptureList


class CaptureListTests(unittest.TestCase):
    def setUp(self):
        self.roots = []

    def tearDown(self):
        for root in reversed(self.roots):
            for callback in root.tk.splitlist(root.tk.call('after', 'info')):
                root.tk.call('after', 'cancel', callback)
            root.tk.call('tk', 'scaling', root._fixture_original_scaling)
            root.destroy()

    @staticmethod
    def record(capture_id, *, long=False):
        name = ('很长的中文控件名称只能单行显示' * 10)[:120] if long else '控件 ' + capture_id
        description = '先填写业务编号，再核对用途和预期结果。' * 200 if long else '读取信息'
        return {'captureId': capture_id, 'control': {'tagName': 'button'},
                'annotation': {'name': name, 'description': description,
                               'allowedActions': ['read', 'click', 'fill']}}

    def widget(self, ratio=1, *, font_size=9):
        root = tk.Tk()
        root.withdraw()
        self.roots.append(root)
        root._fixture_original_scaling = float(root.tk.call('tk', 'scaling'))
        root.tk.call('tk', 'scaling', 96 / 72 * ratio)
        style = ttk.Style(root)
        style.theme_use('clam')
        root._fixture_font = font.Font(root=root, family='Microsoft YaHei UI', size=font_size)
        style.configure('.', font=root._fixture_font)
        calls = []
        widget = CaptureList(root, on_select=lambda cid: calls.append(('select', cid)),
                             on_edit=lambda cid: calls.append(('edit', cid)),
                             on_delete=lambda cid: calls.append(('delete', cid)),
                             on_move=lambda cid, direction: calls.append(('move', cid, direction)))
        widget.place(x=0, y=0, width=round(280 * ratio))
        self.settle(root)
        return root, widget, calls

    @staticmethod
    def settle(root):
        deadline = time.monotonic() + 3
        while root.tk.dooneevent(_tkinter.ALL_EVENTS | _tkinter.DONT_WAIT):
            if time.monotonic() > deadline:
                raise AssertionError('Native event queue did not settle')

    @staticmethod
    def box(widget, relative_to):
        x = widget.winfo_rootx() - relative_to.winfo_rootx()
        y = widget.winfo_rooty() - relative_to.winfo_rooty()
        return x, y, x + widget.winfo_width(), y + widget.winfo_height()

    def assert_row_layout(self, root, row):
        self.assertNotIn('description', row)
        self.assertNotIn('tag', row)
        self.assertEqual(set(row['buttons']), {'edit', 'more'})
        widgets = [row['badge'], row['title']] + list(row['buttons'].values())
        for widget in (row['badge'], row['title']):
            resolved = widget.cget('font') or ttk.Style(root).lookup(widget.winfo_class(), 'font')
            native_font = font.Font(root=root, font=resolved)
            self.assertGreaterEqual(widget.winfo_height(), native_font.metrics('linespace'))
            self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())
        title = row['title']
        shown = str(title.cget('text'))
        self.assertNotIn('\n', shown)
        self.assertEqual(int(title.cget('wraplength')), 0)
        native_font = font.Font(root=root, font=title.cget('font'))
        self.assertLessEqual(native_font.measure(shown), title.winfo_width())
        for left, right in itertools.combinations(widgets, 2):
            a, b = self.box(left, row['frame']), self.box(right, row['frame'])
            overlap = min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])
            self.assertFalse(overlap, (left.cget('text'), a, right.cget('text'), b))
        for widget in widgets:
            x1, y1, x2, y2 = self.box(widget, row['frame'])
            self.assertGreaterEqual(x1, 0)
            self.assertGreaterEqual(y1, 0)
            self.assertLessEqual(x2, row['frame'].winfo_width())
            self.assertLessEqual(y2, row['frame'].winfo_height())

    def invoke_double_click_binding(self, widget):
        # Invoke the real Tcl binding without mapping a window or moving a mouse.
        script = widget.bind('<Double-Button-1>')
        command = re.search(r'\[([^\s\]]+)', script or '')
        self.assertIsNotNone(command, script)
        substitutions = ['0'] * len(widget._subst_format)
        substitutions[widget._subst_format.index('%W')] = str(widget)
        widget.tk.call(command.group(1), *substitutions)

    def test_single_line_height_and_ellipsis_at_five_dpi_scales_and_larger_font(self):
        evidence = []
        for ratio, point_size in ((1, 9), (1.25, 9), (1.5, 9), (1.75, 9), (2, 9), (1.75, 14)):
            with self.subTest(dpi=round(ratio * 100), font=point_size):
                root, widget, _ = self.widget(ratio, font_size=point_size)
                records = [self.record('cap-long', long=True), self.record('cap-short')]
                widget.set_records(records)
                self.settle(root)
                long_row, short_row = widget.cards['cap-long'], widget.cards['cap-short']
                self.assert_row_layout(root, long_row)
                self.assert_row_layout(root, short_row)
                self.assertEqual(long_row['titleText'], records[0]['annotation']['name'])
                self.assertTrue(str(long_row['title'].cget('text')).endswith('…'))
                self.assertEqual(long_row['frame'].winfo_height(), short_row['frame'].winfo_height())
                self.assertEqual(long_row['title'].winfo_height(), short_row['title'].winfo_height())
                self.assertEqual(root.state(), 'withdrawn')
                evidence.append({'dpi': round(ratio * 100), 'pointSize': point_size,
                                 'rowHeight': long_row['frame'].winfo_height(),
                                 'titleHeight': long_row['title'].winfo_height(),
                                 'viewportHeight': widget.viewport_height()})
        print('HIDDEN_SINGLE_ROW_DPI ' + json.dumps(evidence))

    def test_narrow_two_hundred_pixel_row_keeps_every_control_on_one_line(self):
        for ratio, point_size in ((1, 9), (1.75, 14)):
            with self.subTest(dpi=round(ratio * 100), font=point_size):
                root, widget, _ = self.widget(ratio, font_size=point_size)
                widget.place_configure(width=200)
                widget.set_records([self.record('cap-narrow', long=True)])
                self.settle(root)
                row = widget.cards['cap-narrow']
                self.assert_row_layout(root, row)
                centers = [(child.winfo_y() + child.winfo_height() / 2)
                           for child in [row['badge'], row['title']] + list(row['buttons'].values())]
                line = font.Font(root=root, font=row['title'].cget('font')).metrics('linespace')
                self.assertLess(max(centers) - min(centers), line / 2)

    def test_up_to_six_rows_fit_and_more_records_scroll_without_growing(self):
        root, widget, _ = self.widget(1)
        for count in (1, 2, 6):
            widget.set_records([self.record('cap-' + str(index), long=index % 2 == 0)
                                for index in range(count)])
            self.settle(root)
            bottom = widget.canvas.bbox('all')[3]
            self.assertLessEqual(bottom, widget.viewport_height())
            self.assertEqual(widget.canvas.yview()[0], 0)
        six_height = widget.viewport_height()
        widget.set_records([self.record('cap-' + str(index)) for index in range(60)])
        self.settle(root)
        self.assertEqual(widget.viewport_height(), six_height)
        self.assertGreater(widget.canvas.bbox('all')[3], widget.viewport_height() * 2)
        widget.canvas.yview_moveto(1)
        self.assertGreater(widget.canvas.yview()[0], 0)
        widget.set_viewport_height(max(20, six_height // 2))
        self.settle(root)
        self.assertLessEqual(widget.viewport_height(), max(20, six_height // 2))

    def test_menu_boundaries_and_callbacks_keep_the_correct_control_identity(self):
        root, widget, calls = self.widget()
        records = [self.record('cap-a'), self.record('cap-b'), self.record('cap-c')]
        widget.set_records(records)
        self.settle(root)
        first, middle, last = (widget.cards[cid] for cid in ('cap-a', 'cap-b', 'cap-c'))
        self.assertEqual(str(first['menu'].entrycget(0, 'state')), 'disabled')
        self.assertEqual(str(last['menu'].entrycget(1, 'state')), 'disabled')
        first['menu'].invoke(0)
        last['menu'].invoke(1)
        first['buttons']['edit'].invoke()
        last['menu'].invoke(3)
        middle['menu'].invoke(0)
        middle['menu'].invoke(1)
        self.invoke_double_click_binding(middle['title'])
        self.assertEqual([call for call in calls if call[0] != 'select'],
                         [('edit', 'cap-a'), ('delete', 'cap-c'), ('move', 'cap-b', -1),
                          ('move', 'cap-b', 1), ('edit', 'cap-b')])
        widget.set_records([records[2], records[0]], selected_id='cap-c')
        self.settle(root)
        self.assertEqual(widget.selection(), ('cap-c',))
        widget.cards['cap-a']['buttons']['edit'].invoke()
        self.assertEqual([call for call in calls if call[0] != 'select'][-1], ('edit', 'cap-a'))

    def test_busy_blocks_buttons_menu_and_double_click_after_rebuild(self):
        root, widget, calls = self.widget()
        records = [self.record('cap-a'), self.record('cap-b')]
        widget.set_records(records)
        widget.set_busy(True)
        widget.set_records(records[::-1], selected_id='cap-a')
        self.settle(root)
        before = [call for call in calls if call[0] != 'select']
        for row in widget.cards.values():
            for button in row['buttons'].values():
                self.assertEqual(str(button.cget('state')), 'disabled')
                button.invoke()
            for index in (0, 1, 3):
                self.assertEqual(str(row['menu'].entrycget(index, 'state')), 'disabled')
                row['menu'].invoke(index)
            self.invoke_double_click_binding(row['title'])
        self.assertEqual([call for call in calls if call[0] != 'select'], before)
        widget.set_busy(False)
        widget.cards['cap-a']['buttons']['edit'].invoke()
        self.assertEqual([call for call in calls if call[0] != 'select'][-1], ('edit', 'cap-a'))

    def test_empty_page_and_removed_selection_do_not_keep_stale_rows(self):
        root, widget, _ = self.widget()
        widget.set_records([self.record('cap-a'), self.record('cap-b')], selected_id='cap-b')
        widget.set_ordered(True)
        widget.set_records([self.record('cap-a')], selected_id='cap-b')
        self.assertEqual(widget.selection(), ('cap-a',))
        self.assertNotIn('cap-b', widget.cards)
        widget.set_records([])
        widget.set_busy(True)
        widget.set_ordered(False)
        self.settle(root)
        self.assertEqual(widget.selection(), ())
        self.assertEqual(widget.cards, {})
        self.assertEqual(root.state(), 'withdrawn')


if __name__ == '__main__':
    unittest.main()
