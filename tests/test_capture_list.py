"""Native CaptureList checks stay withdrawn; no external UI/browser/model."""
import itertools
import json
from pathlib import Path
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
        name = '很长的中文控件名称需要完整展示并自动换行' * 5 if long else '控件 ' + capture_id
        description = '先填写业务编号，再核对当前页面上这项控件的功能与预期结果。' * 50 if long else '读取本项业务信息'
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
        # Explicit native geometry resolves children even while the host is
        # withdrawn. Its height continues to follow the bounded request.
        widget.place(x=0, y=0, width=round(360 * ratio))
        self.settle(root)
        return root, widget, calls

    @staticmethod
    def settle(root):
        deadline = time.monotonic() + 3
        while root.tk.dooneevent(_tkinter.ALL_EVENTS | _tkinter.DONT_WAIT):
            if time.monotonic() > deadline:
                raise AssertionError('Native widget event queue did not settle; possible reflow loop')

    @staticmethod
    def box(widget, relative_to):
        x = widget.winfo_rootx() - relative_to.winfo_rootx()
        y = widget.winfo_rooty() - relative_to.winfo_rooty()
        return x, y, x + widget.winfo_width(), y + widget.winfo_height()

    def assert_card_layout(self, root, card):
        frame = card['frame']
        text_widgets = [card[key] for key in ('badge', 'title', 'tag', 'description')]
        widgets = text_widgets + list(card['buttons'].values())
        for widget in text_widgets:
            resolved_font = widget.cget('font') or ttk.Style(root).lookup(widget.cget('style') or widget.winfo_class(), 'font')
            metrics = font.Font(root=root, font=resolved_font).metrics('linespace')
            self.assertGreaterEqual(widget.winfo_height(), metrics)
            self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())
        for left, right in itertools.combinations(widgets, 2):
            a, b = self.box(left, frame), self.box(right, frame)
            overlap = min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])
            self.assertFalse(overlap, (left.winfo_class(), left.cget('text'), a,
                                       right.winfo_class(), right.cget('text'), b))
        for widget in widgets:
            x1, y1, x2, y2 = self.box(widget, frame)
            self.assertGreaterEqual(x1, 0)
            self.assertGreaterEqual(y1, 0)
            self.assertLessEqual(x2, frame.winfo_width())
            self.assertLessEqual(y2, frame.winfo_height())

    def test_long_chinese_layout_at_five_dpi_scales_and_larger_font(self):
        evidence = []
        for ratio, point_size in ((1, 9), (1.25, 9), (1.5, 9), (1.75, 9), (2, 9), (1.75, 14)):
            with self.subTest(dpi=round(ratio * 100), font=point_size):
                root, widget, _ = self.widget(ratio, font_size=point_size)
                widget.set_records([self.record('cap-long', long=True), self.record('cap-next')])
                self.settle(root)
                self.assertEqual(root.state(), 'withdrawn')
                card = widget.cards['cap-long']
                self.assert_card_layout(root, card)
                self.assertEqual(card['title'].cget('text'), self.record('cap-long', long=True)['annotation']['name'])
                line = font.Font(root=root, font=card['title'].cget('font')).metrics('linespace')
                self.assertGreater(card['title'].winfo_height(), line)
                self.assertLessEqual(widget.viewport_height(), max(line * 2, round(root.winfo_screenheight() * .48)))
                evidence.append({'dpi': round(ratio * 100), 'pointSize': point_size,
                                 'fontLine': line, 'titleHeight': card['title'].winfo_height(),
                                 'cardHeight': card['frame'].winfo_height(),
                                 'viewportHeight': widget.viewport_height()})
        print('HIDDEN_CAPTURE_LIST_DPI ' + json.dumps(evidence))

    def test_narrow_two_hundred_pixel_card_wraps_actions_without_horizontal_clipping(self):
        for ratio, point_size in ((1, 9), (1.75, 14)):
            with self.subTest(dpi=round(ratio * 100), font=point_size):
                root, widget, _ = self.widget(ratio, font_size=point_size)
                widget.place_configure(width=200)
                widget.set_records([self.record('cap-narrow', long=True)])
                self.settle(root)
                card = widget.cards['cap-narrow']
                self.assert_card_layout(root, card)
                self.assertEqual(card['buttons']['edit'].grid_info()['row'], 0)
                if point_size >= 14:
                    self.assertGreater(card['buttons']['up'].grid_info()['row'], 0)
                    self.assertEqual(card['buttons']['up'].grid_info()['column'], 0)

    def test_scroll_viewport_stays_bounded_when_records_grow(self):
        root, widget, _ = self.widget(1.75)
        widget.set_records([self.record('cap-' + str(index)) for index in range(12)])
        self.settle(root)
        preferred = widget.preferred_viewport_height
        limit = round(180 * 1.75)
        widget.set_viewport_height(limit)
        self.settle(root)
        first_height = widget.viewport_height()
        widget.set_records([self.record('cap-' + str(index)) for index in range(60)])
        widget.set_viewport_height(limit)
        self.settle(root)
        self.assertLessEqual(widget.viewport_height(), limit)
        self.assertEqual(widget.viewport_height(), first_height)
        self.assertEqual(widget.preferred_viewport_height, preferred)
        self.assertGreater(widget.canvas.bbox('all')[3], widget.viewport_height() * 2)
        widget.canvas.yview_moveto(1)
        self.assertGreater(widget.canvas.yview()[0], 0)

    def test_callbacks_keep_control_identity_after_selection_and_reorder(self):
        root, widget, calls = self.widget()
        records = [self.record('cap-a'), self.record('cap-b'), self.record('cap-c')]
        widget.set_records(records)
        self.settle(root)
        widget.select('cap-b')
        self.assertEqual(widget.selection(), ('cap-b',))
        widget.cards['cap-a']['buttons']['edit'].invoke()
        widget.cards['cap-c']['buttons']['delete'].invoke()
        widget.cards['cap-b']['buttons']['up'].invoke()
        widget.cards['cap-b']['buttons']['down'].invoke()
        self.assertEqual([call for call in calls if call[0] != 'select'],
                         [('edit', 'cap-a'), ('delete', 'cap-c'), ('move', 'cap-b', -1), ('move', 'cap-b', 1)])
        widget.set_records([records[2], records[0]], selected_id='cap-c')
        self.settle(root)
        self.assertEqual(widget.selection(), ('cap-c',))
        widget.cards['cap-a']['buttons']['edit'].invoke()
        self.assertEqual([call for call in calls if call[0] != 'select'][-1], ('edit', 'cap-a'))

    def test_busy_blocks_all_action_callbacks_and_rebuild_preserves_busy(self):
        root, widget, calls = self.widget()
        records = [self.record('cap-a'), self.record('cap-b')]
        widget.set_records(records)
        widget.set_busy(True)
        widget.set_records(records[::-1], selected_id='cap-a')
        self.settle(root)
        before = [call for call in calls if call[0] != 'select']
        for card in widget.cards.values():
            for button in card['buttons'].values():
                self.assertTrue(button.instate(('disabled',)))
                button.invoke()
        self.assertEqual([call for call in calls if call[0] != 'select'], before)
        widget.set_busy(False)
        widget.cards['cap-a']['buttons']['edit'].invoke()
        self.assertEqual([call for call in calls if call[0] != 'select'][-1], ('edit', 'cap-a'))

    def test_empty_page_and_removed_selection_do_not_keep_stale_controls(self):
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


if __name__ == '__main__':
    unittest.main()
