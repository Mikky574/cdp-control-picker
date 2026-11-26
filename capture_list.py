"""Font-sized, scrollable capture cards. No browser or workspace operations."""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont, ttk


_ACTIONS = {"read": "读取", "fill": "填写", "click": "点击", "select": "选择", "check": "勾选"}
_BACKGROUND = "#f5f7fa"
_CARD = "#ffffff"
_SELECTED = "#f0f6ff"


def _two_line_summary(text, font, width):
    """Wrap by rendered pixels; only the description may end in an ellipsis."""
    text = " ".join(str(text).split())
    width = max(font.measure("…"), int(width))
    lines, offset = [], 0
    for number in range(2):
        line = ""
        while offset < len(text):
            candidate = line + text[offset]
            if line and font.measure(candidate) > width:
                break
            line = candidate
            offset += 1
            if font.measure(line) > width:
                break
        if number == 1 and offset < len(text):
            while line and font.measure(line.rstrip() + "…") > width:
                line = line[:-1]
            line = line.rstrip() + "…"
        lines.append(line.rstrip())
        if offset >= len(text):
            break
    return "\n".join(lines)


class CaptureList(ttk.Frame):
    """Single-select cards; callbacks receive IDs, and moves also receive ±1.

    ``cards`` exposes widget references for layout checks, keyed by captureId.
    ``set_viewport_height`` lets the containing window cap the scrolling area.
    """

    def __init__(self, parent, on_select, on_edit, on_delete, on_move, sanitizer=None):
        super().__init__(parent)
        self._callbacks = {"select": on_select, "edit": on_edit, "delete": on_delete, "move": on_move}
        self._sanitizer = sanitizer
        self._records = []
        self._selected = None
        self._busy = False
        self._ordered = False
        self._reflow_job = None
        self._select_job = None
        self._reflowing = False
        self._viewport_override = None
        self._metrics_signature = None
        self._preferred_count = None
        self._wheel_fraction = 0.0
        self.cards = {}
        self._style = ttk.Style(self)
        specification = self._style.lookup(".", "font") or "TkDefaultFont"
        self.body_font = tkfont.Font(root=self, font=specification)
        self.title_font = self.body_font.copy()
        self.title_font.configure(weight="bold")
        self.small_font = self.body_font.copy()
        size = int(self.small_font.actual("size"))
        self.small_font.configure(size=max(8, size - 1) if size > 0 else min(-1, size + 1))
        self._line = max(1, self.body_font.metrics("linespace"))
        self._set_metrics()

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, width=1, height=self.preferred_viewport_height,
                                background=_BACKGROUND, borderwidth=0, highlightthickness=0,
                                yscrollincrement=self._line)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.content_frame = tk.Frame(self.canvas, background=_BACKGROUND, borderwidth=0)
        self.content_frame.columnconfigure(0, weight=1)
        self._window = self.canvas.create_window(0, 0, anchor="nw", window=self.content_frame, width=1)
        self._empty = tk.Label(self.content_frame, text="还没有本页标注\n在网页右键控件，填写用途后会显示在这里。",
                               font=self.body_font, foreground="#66758a", background=_BACKGROUND,
                               anchor="center", justify="center", borderwidth=0, padx=0, pady=0)
        self._empty.grid(row=0, column=0, sticky="ew", padx=self._pad, pady=self._line)
        self.canvas.bind("<Configure>", self._schedule_reflow, add="+")
        self.content_frame.bind("<Configure>", self._content_changed, add="+")
        self.bind("<<ThemeChanged>>", self._schedule_reflow, add="+")
        for widget in (self, self.canvas, self.content_frame, self.scrollbar, self._empty):
            self._bind_wheel(widget)
        self._schedule_reflow()

    def _set_metrics(self):
        self._line = max(1, self.body_font.metrics("linespace"))
        signature = (self._line, self.title_font.metrics("linespace"), self.small_font.metrics("linespace"))
        metrics_changed = signature != self._metrics_signature
        if not metrics_changed and self._preferred_count == len(self._records):
            return
        self._metrics_signature = signature
        self._preferred_count = len(self._records)
        self._pad = max(4, round(self._line * .5))
        self._gap = max(3, round(self._line * .35))
        self._border = max(1, round(self._line / 20))
        if metrics_changed:
            self._style.configure("CaptureCard.TButton", font=self.small_font, relief="flat", borderwidth=0,
                                  background=_CARD, foreground="#65748a", bordercolor=_CARD,
                                  lightcolor=_CARD, darkcolor=_CARD, focuscolor=_SELECTED,
                                  padding=(max(3, round(self._line * .3)), max(2, round(self._line * .12))))
            self._style.map("CaptureCard.TButton", background=[("disabled", _CARD), ("pressed", "#e3edfc"), ("active", _SELECTED)],
                            foreground=[("disabled", "#9ba8b9"), ("active", "#3c5470")],
                            bordercolor=[("active", _SELECTED), ("!active", _CARD)],
                            lightcolor=[("active", _SELECTED), ("!active", _CARD)],
                            darkcolor=[("active", _SELECTED), ("!active", _CARD)], relief=[("!disabled", "flat")])
            self._style.configure("Edit.CaptureCard.TButton", foreground="#175bd7")
            self._style.map("Edit.CaptureCard.TButton", foreground=[("disabled", "#9ba8b9"), ("active", "#104cb8")])
            self._style.configure("Delete.CaptureCard.TButton", foreground="#a04450")
            self._style.map("Delete.CaptureCard.TButton", foreground=[("disabled", "#9ba8b9"), ("active", "#842d39")])
        # Roughly 2.2 ordinary cards; long full names scroll within this area.
        estimated = round(self._line * (17.5 if len(self._records) > 2 else 8 * len(self._records) if self._records else 4.5))
        screen_cap = max(self._line * 2, round(self.winfo_screenheight() * .48))
        self.preferred_viewport_height = max(self._line * 2, min(estimated, screen_cap))

    def _safe(self, value):
        if self._sanitizer is None:
            return str(value)
        try:
            return str(self._sanitizer(value, 12000))
        except TypeError:
            try:
                return str(self._sanitizer(value))
            except Exception:
                return "内容暂不可用"
        except Exception:
            return "内容暂不可用"

    def set_records(self, records, selected_id=None):
        previous = self._selected
        filtered = []
        seen = set()
        for record in records:
            if not isinstance(record, dict):
                continue
            capture_id = record.get("captureId")
            if not isinstance(capture_id, str) or not capture_id or capture_id in seen:
                continue
            seen.add(capture_id)
            filtered.append(record)
        if filtered == self._records:
            if selected_id in self.cards and selected_id != self._selected:
                self.select(selected_id)
            return
        self._records = filtered
        for card in self.cards.values():
            card["frame"].destroy()
        self.cards.clear()
        self._empty.grid_remove()
        if not self._records:
            self._empty.grid()
        for index, record in enumerate(self._records):
            self._create_card(record, index)
        selected = selected_id if selected_id in self.cards else previous if previous in self.cards else next(iter(self.cards), None)
        self._selected = None
        self._set_metrics()
        self._apply_viewport()
        self.select(selected)
        self._apply_button_states()
        self._schedule_reflow()

    def _create_card(self, record, index):
        capture_id = record["captureId"]
        annotation = record.get("annotation") if isinstance(record.get("annotation"), dict) else {}
        control = record.get("control") if isinstance(record.get("control"), dict) else {}
        name = annotation.get("name") or control.get("name") or control.get("accessibleName") or control.get("text") or capture_id
        title = self._safe(name).strip() or capture_id
        description = self._safe(annotation.get("description") or "暂无用途说明")
        actions = annotation.get("allowedActions")
        actions = actions if isinstance(actions, list) else []
        tags = list(dict.fromkeys(_ACTIONS[action] for action in actions if isinstance(action, str) and action in _ACTIONS))
        frame = tk.Frame(self.content_frame, background=_CARD, borderwidth=0,
                         highlightthickness=self._border, highlightbackground="#d7e0ec",
                         padx=self._pad, pady=self._pad)
        frame.grid(row=index, column=0, sticky="ew", pady=(0, self._gap))
        frame.columnconfigure(0, weight=1)
        header = tk.Frame(frame, background=_CARD, borderwidth=0)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1, minsize=1)
        badge = tk.Label(header, text=str(index + 1), font=self.small_font, background="#edf1f6",
                         foreground="#526780", borderwidth=0, padx=max(3, self._pad // 2), pady=1)
        badge.grid(row=0, column=0, sticky="nw", padx=(0, self._gap))
        heading = tk.Label(header, text=title, font=self.title_font, background=_CARD, foreground="#142139",
                           justify="left", anchor="w", borderwidth=0, padx=0, pady=0, wraplength=1)
        heading.grid(row=0, column=1, sticky="ew")
        tag = tk.Label(frame, text=" · ".join(tags) or "动作未注明", font=self.small_font,
                       background="#edf3fa", foreground="#47658a", justify="left", anchor="w",
                       borderwidth=0, padx=max(2, self._pad // 3), pady=1, wraplength=1)
        tag.grid(row=1, column=0, sticky="w", pady=(max(2, self._gap // 2), 0))
        summary = tk.Label(frame, text="", font=self.body_font, background=_CARD, foreground="#526780",
                           justify="left", anchor="w", borderwidth=0, padx=0, pady=0, wraplength=1)
        summary.grid(row=2, column=0, sticky="ew", pady=(self._gap, 0))
        toolbar = tk.Frame(frame, background=_CARD, borderwidth=0)
        toolbar.grid(row=3, column=0, sticky="ew", pady=(self._gap, 0))
        toolbar.columnconfigure(2, weight=1)
        buttons = {}
        for key, text, column, action in (
            ("edit", "编辑", 0, lambda: self._action("edit", capture_id)),
            ("delete", "删除", 1, lambda: self._action("delete", capture_id)),
            ("up", "上移", 3, lambda: self._action("move", capture_id, -1)),
            ("down", "下移", 4, lambda: self._action("move", capture_id, 1)),
        ):
            style = "Edit.CaptureCard.TButton" if key == "edit" else "Delete.CaptureCard.TButton" if key == "delete" else "CaptureCard.TButton"
            button = ttk.Button(toolbar, text=text, style=style, command=action, takefocus=True, width=0)
            button.grid(row=0, column=column, padx=(0 if column == 0 else self._gap, 0))
            buttons[key] = button
        card = {"frame": frame, "header": header, "badge": badge, "title": heading, "tag": tag,
                "description": summary, "actions": toolbar, "buttons": buttons,
                "descriptionText": description, "index": index}
        self.cards[capture_id] = card
        for widget in (frame, header, badge, heading, tag, summary, toolbar):
            widget.bind("<Button-1>", lambda _event, key=capture_id: self._choose(key), add="+")
        for widget in (frame, header, badge, heading, tag, summary, toolbar, *buttons.values()):
            self._bind_wheel(widget)

    def selection(self):
        return (self._selected,) if self._selected in self.cards else ()

    def select(self, capture_id):
        previous = self._selected
        if capture_id == previous:
            return
        self._selected = capture_id if capture_id in self.cards else None
        for key, card in self.cards.items():
            background = _SELECTED if key == self._selected else _CARD
            card["frame"].configure(highlightbackground="#7ca8e9" if key == self._selected else "#d7e0ec")
            for name in ("frame", "header", "title", "description", "actions"):
                card[name].configure(background=background)
            self._paint_badge(card)
        if self._selected != previous and self._selected:
            callback = self._callbacks["select"]
            if callback:
                callback(self._selected)
        if self._select_job is not None:
            self.after_cancel(self._select_job)
        self._select_job = self.after_idle(self._see_selected)

    def _choose(self, capture_id):
        if not self._busy:
            self.select(capture_id)

    def _action(self, action, capture_id, direction=None):
        if self._busy or capture_id not in self.cards:
            return
        index = self.cards[capture_id]["index"]
        if action == "move" and (direction == -1 and index == 0 or direction == 1 and index == len(self.cards) - 1):
            return
        self.select(capture_id)
        callback = self._callbacks[action]
        if callback:
            callback(capture_id, direction) if action == "move" else callback(capture_id)

    def set_busy(self, busy):
        value = bool(busy)
        if value == self._busy:
            return
        self._busy = value
        self._apply_button_states()

    def _apply_button_states(self):
        for card in self.cards.values():
            for action, button in card["buttons"].items():
                disabled = self._busy or action == "up" and card["index"] == 0 or action == "down" and card["index"] == len(self.cards) - 1
                button.configure(state="disabled" if disabled else "normal")

    def set_ordered(self, ordered):
        value = bool(ordered)
        if value == self._ordered:
            return
        self._ordered = value
        for card in self.cards.values():
            self._paint_badge(card)

    def _paint_badge(self, card):
        card["badge"].configure(background="#e4f3ee" if self._ordered else "#edf1f6",
                                foreground="#087b6b" if self._ordered else "#526780")

    def set_viewport_height(self, height):
        self._viewport_override = None if height is None else max(self._line * 2, int(height))
        self._apply_viewport()

    def viewport_height(self):
        return int(float(self.canvas.cget("height")))

    def _apply_viewport(self):
        height = self.preferred_viewport_height if self._viewport_override is None else self._viewport_override
        self.canvas.configure(height=max(self._line * 2, int(height)))

    def _schedule_reflow(self, _event=None):
        if self._reflow_job is None:
            self._reflow_job = self.after_idle(self._reflow)

    def _reflow(self):
        self._reflow_job = None
        if self._reflowing or not self.winfo_exists():
            return
        self._reflowing = True
        try:
            self._set_metrics()
            self._apply_viewport()
            self.canvas.configure(yscrollincrement=self._line)
            width = max(1, self.canvas.winfo_width())
            self.canvas.itemconfigure(self._window, width=width)
            inner_width = max(self._line, width - self._pad * 2 - self._border * 2)
            self._empty.configure(wraplength=max(self._line, width - self._pad * 2))
            for card in self.cards.values():
                card["frame"].configure(padx=self._pad, pady=self._pad, highlightthickness=self._border)
                card["frame"].grid_configure(pady=(0, self._gap))
                badge_width = card["badge"].winfo_reqwidth()
                card["title"].configure(wraplength=max(self._line, inner_width - badge_width - self._gap))
                tag_padding = int(card["tag"].cget("padx")) * 2
                card["tag"].configure(wraplength=max(self._line, inner_width - tag_padding))
                card["description"].configure(text=_two_line_summary(card["descriptionText"], self.body_font, inner_width), wraplength=inner_width)
                buttons = card["buttons"]
                required = sum(button.winfo_reqwidth() for button in buttons.values()) + self._gap * 3
                pair_width = max(buttons["edit"].winfo_reqwidth() + buttons["delete"].winfo_reqwidth(),
                                 buttons["up"].winfo_reqwidth() + buttons["down"].winfo_reqwidth()) + self._gap
                if required <= inner_width:
                    positions = (("edit", 0, 0), ("delete", 0, 1), ("up", 0, 3), ("down", 0, 4))
                elif pair_width <= inner_width:
                    positions = (("edit", 0, 0), ("delete", 0, 1), ("up", 1, 0), ("down", 1, 1))
                else:
                    positions = tuple((key, row, 0) for row, key in enumerate(("edit", "delete", "up", "down")))
                for key, row, column in positions:
                    buttons[key].grid_configure(row=row, column=column, padx=(0 if column == 0 else self._gap, 0),
                                                 pady=(self._gap if row else 0, 0))
            self._content_changed()
        finally:
            self._reflowing = False

    def _content_changed(self, _event=None):
        region = self.canvas.bbox("all")
        if region:
            self.canvas.configure(scrollregion=region)
        if len(self._records) <= 2 and self.canvas.winfo_width() > 1:
            screen_cap = max(self._line * 2, round(self.winfo_screenheight() * .48))
            height = max(self._line * 2, min(self.content_frame.winfo_reqheight(), screen_cap))
            if height != self.preferred_viewport_height:
                self.preferred_viewport_height = height
                self._apply_viewport()

    def _see_selected(self):
        self._select_job = None
        if self._selected not in self.cards or not self.winfo_exists():
            return
        frame = self.cards[self._selected]["frame"]
        top, height = frame.winfo_y(), frame.winfo_height()
        visible_top = self.canvas.canvasy(0)
        visible_height = max(1, self.canvas.winfo_height())
        total = max(1, self.content_frame.winfo_height())
        if top < visible_top or height > visible_height:
            self.canvas.yview_moveto(top / total)
        elif top + height > visible_top + visible_height:
            self.canvas.yview_moveto(max(0, top + height - visible_height) / total)

    def _bind_wheel(self, widget):
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(sequence, self._mousewheel, add="+")

    def _mousewheel(self, event):
        x, y = self.winfo_pointerxy()
        if not (self.winfo_rootx() <= x < self.winfo_rootx() + self.winfo_width()
                and self.winfo_rooty() <= y < self.winfo_rooty() + self.winfo_height()):
            return None
        number = getattr(event, "num", None)
        if number in (4, 5):
            amount = -3 if number == 4 else 3
        else:
            self._wheel_fraction -= getattr(event, "delta", 0) / 120 * 3
            amount = math.trunc(self._wheel_fraction)
            self._wheel_fraction -= amount
        if amount:
            self.canvas.yview_scroll(amount, "units")
        return "break"

    def destroy(self):
        for identifier in (self._reflow_job, self._select_job):
            if identifier is not None:
                try:
                    self.after_cancel(identifier)
                except tk.TclError:
                    pass
        super().destroy()
