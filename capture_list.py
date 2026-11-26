"""Font-sized, single-line capture rows. No browser or workspace operations."""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont, ttk


_BACKGROUND = "#f5f7fa"
_ROW = "#ffffff"
_SELECTED = "#edf4fc"


def _two_line_summary(text, font, width):
    """Two-line pixel-measured summary for the separate send feedback area."""
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


def _single_line_name(text, font, width):
    """Tail-truncate by rendered pixels; keep the complete name in the record."""
    text = " ".join(str(text).split())
    width = max(0, int(width))
    if font.measure(text) <= width:
        return text
    if font.measure("…") > width:
        return ""
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if font.measure(text[:middle].rstrip() + "…") <= width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + "…"


class CaptureList(ttk.Frame):
    """Single-select, single-line rows with edit and an overflow menu.

    Callbacks receive capture IDs; move also receives -1 or +1. ``cards`` is
    retained as a compatibility name for row widget references. Descriptions
    and action tags belong to the editor and are never rendered in this list.
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
        self._wheel_fraction = 0.0
        self.cards = {}
        specification = ttk.Style(self).lookup(".", "font") or "TkDefaultFont"
        self.body_font = tkfont.Font(root=self, font=specification)
        self.title_font = self.body_font.copy()
        self.title_font.configure(weight="bold")
        self.small_font = self.body_font.copy()
        size = int(self.small_font.actual("size"))
        self.small_font.configure(size=max(8, size - 1) if size > 0 else min(-1, size + 1))
        self._set_metrics()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, width=1, height=self.preferred_viewport_height,
                                background=_BACKGROUND, borderwidth=0, highlightthickness=0,
                                yscrollincrement=self._row_height)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.content_frame = tk.Frame(self.canvas, background=_BACKGROUND, borderwidth=0)
        self.content_frame.columnconfigure(0, weight=1)
        self._window = self.canvas.create_window(0, 0, anchor="nw", window=self.content_frame, width=1)
        self._empty = tk.Label(self.content_frame, text="暂无本页控件", font=self.body_font,
                               foreground="#66758a", background=_BACKGROUND,
                               anchor="center", borderwidth=0, padx=0, pady=self._vertical_pad)
        self._empty.grid(row=0, column=0, sticky="ew")
        self.canvas.bind("<Configure>", self._schedule_reflow, add="+")
        self.content_frame.bind("<Configure>", self._content_changed, add="+")
        self.bind("<<ThemeChanged>>", self._schedule_reflow, add="+")
        for widget in (self, self.canvas, self.content_frame, self.scrollbar, self._empty):
            self._bind_wheel(widget)
        self._schedule_reflow()

    def _set_metrics(self):
        self._line = max(1, self.body_font.metrics("linespace"),
                         self.title_font.metrics("linespace"), self.small_font.metrics("linespace"))
        self._vertical_pad = max(2, round(self._line * .18))
        self._pad = max(3, round(self._line * .28))
        self._gap = max(3, round(self._line * .25))
        self._row_height = self._line + self._vertical_pad * 2
        screen_cap = max(self._row_height, round(self.winfo_screenheight() * .48))
        count = max(1, min(6, len(self._records)))
        self.preferred_viewport_height = max(self._row_height, min(self._row_height * count, screen_cap))

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
        filtered, seen = [], set()
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
            card["menu"].destroy()
            card["frame"].destroy()
        self.cards.clear()
        self._empty.grid_remove()
        if not self._records:
            self._empty.grid()
        self._set_metrics()
        for index, record in enumerate(self._records):
            self._create_card(record, index)
        selected = selected_id if selected_id in self.cards else previous if previous in self.cards else next(iter(self.cards), None)
        self._selected = None
        self._apply_viewport()
        self.select(selected)
        self._apply_button_states()
        self._schedule_reflow()

    def _create_card(self, record, index):
        capture_id = record["captureId"]
        annotation = record.get("annotation") if isinstance(record.get("annotation"), dict) else {}
        control = record.get("control") if isinstance(record.get("control"), dict) else {}
        name = annotation.get("name") or control.get("name") or control.get("accessibleName") or control.get("text") or capture_id
        title = " ".join(self._safe(name).split()) or capture_id
        frame = tk.Frame(self.content_frame, background=_ROW, borderwidth=0,
                         highlightthickness=0, height=self._row_height, width=1,
                         padx=self._pad, pady=self._vertical_pad)
        frame.grid(row=index, column=0, sticky="ew")
        frame.grid_propagate(False)
        frame.columnconfigure(1, weight=1, minsize=0)
        frame.rowconfigure(0, weight=1)
        badge = tk.Label(frame, text=str(index + 1), font=self.small_font, background=_ROW,
                         foreground="#708097", anchor="e", borderwidth=0, padx=0, pady=0)
        badge.grid(row=0, column=0, sticky="ns", padx=(0, self._gap))
        heading = tk.Label(frame, text="", font=self.title_font, background=_ROW,
                           foreground="#142139", anchor="w", borderwidth=0,
                           padx=0, pady=0, wraplength=0, width=0)
        heading.grid(row=0, column=1, sticky="nsew")
        edit = tk.Button(frame, text="编辑", font=self.small_font, foreground="#175bd7",
                         background=_ROW, activebackground=_SELECTED, activeforeground="#104cb8",
                         disabledforeground="#9ba8b9", relief="flat", borderwidth=0,
                         highlightthickness=0, padx=max(2, self._pad // 2), pady=0,
                         takefocus=True, command=lambda: self._action("edit", capture_id))
        edit.grid(row=0, column=2, sticky="ns", padx=(self._gap, 0))
        more = tk.Button(frame, text="⋯", font=self.small_font, foreground="#65748a",
                         background=_ROW, activebackground=_SELECTED, activeforeground="#3c5470",
                         disabledforeground="#9ba8b9", relief="flat", borderwidth=0,
                         highlightthickness=0, padx=max(2, self._pad // 2), pady=0,
                         takefocus=True, command=lambda: self._open_menu(capture_id))
        more.grid(row=0, column=3, sticky="ns", padx=(self._gap, 0))
        menu = tk.Menu(self, tearoff=False, font=self.small_font)
        menu.add_command(label="上移", command=lambda: self._action("move", capture_id, -1))
        menu.add_command(label="下移", command=lambda: self._action("move", capture_id, 1))
        menu.add_separator()
        menu.add_command(label="删除", command=lambda: self._action("delete", capture_id))
        self.cards[capture_id] = {"frame": frame, "badge": badge, "title": heading,
                                  "buttons": {"edit": edit, "more": more}, "menu": menu,
                                  "index": index, "titleText": title}
        for widget in (frame, badge, heading):
            widget.bind("<Button-1>", lambda _event, key=capture_id: self._choose(key), add="+")
        heading.bind("<Double-Button-1>", lambda _event, key=capture_id: self._action("edit", key), add="+")
        for widget in (frame, badge, heading, edit, more):
            self._bind_wheel(widget)

    def selection(self):
        return (self._selected,) if self._selected in self.cards else ()

    def select(self, capture_id):
        previous = self._selected
        if capture_id == previous:
            return
        self._selected = capture_id if capture_id in self.cards else None
        for key, card in self.cards.items():
            background = _SELECTED if key == self._selected else _ROW
            for widget in (card["frame"], card["title"], card["badge"], *card["buttons"].values()):
                widget.configure(background=background)
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

    def _open_menu(self, capture_id):
        if self._busy or capture_id not in self.cards:
            return
        self.select(capture_id)
        card = self.cards[capture_id]
        button, menu = card["buttons"]["more"], card["menu"]
        self._apply_button_states()
        try:
            menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        finally:
            menu.grab_release()

    def set_busy(self, busy):
        value = bool(busy)
        if value == self._busy:
            return
        self._busy = value
        self._apply_button_states()

    def _apply_button_states(self):
        for card in self.cards.values():
            state = "disabled" if self._busy else "normal"
            for button in card["buttons"].values():
                button.configure(state=state)
            card["menu"].entryconfigure(0, state="disabled" if self._busy or card["index"] == 0 else "normal")
            card["menu"].entryconfigure(1, state="disabled" if self._busy or card["index"] == len(self.cards) - 1 else "normal")
            card["menu"].entryconfigure(3, state=state)
            if self._busy:
                card["menu"].unpost()

    def set_ordered(self, ordered):
        value = bool(ordered)
        if value == self._ordered:
            return
        self._ordered = value
        for card in self.cards.values():
            self._paint_badge(card)

    def _paint_badge(self, card):
        card["badge"].configure(foreground="#087b6b" if self._ordered else "#708097")

    def set_viewport_height(self, height):
        self._viewport_override = None if height is None else max(self._row_height, int(height))
        self._apply_viewport()

    def viewport_height(self):
        return int(float(self.canvas.cget("height")))

    def _apply_viewport(self):
        preferred = self.preferred_viewport_height
        height = preferred if self._viewport_override is None else min(preferred, self._viewport_override)
        self.canvas.configure(height=max(self._row_height, int(height)))

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
            self.canvas.configure(yscrollincrement=self._row_height)
            width = max(1, self.canvas.winfo_width())
            self.canvas.itemconfigure(self._window, width=width)
            self._empty.configure(pady=self._vertical_pad)
            for card in self.cards.values():
                card["frame"].configure(height=self._row_height, padx=self._pad, pady=self._vertical_pad)
                card["badge"].grid_configure(padx=(0, self._gap))
                for button in card["buttons"].values():
                    button.configure(padx=max(2, self._pad // 2))
                    button.grid_configure(padx=(self._gap, 0))
                fixed = card["badge"].winfo_reqwidth() + sum(button.winfo_reqwidth() for button in card["buttons"].values())
                title_width = max(0, width - self._pad * 2 - fixed - self._gap * 3)
                card["title"].configure(text=_single_line_name(card["titleText"], self.title_font, title_width))
            self._content_changed()
        finally:
            self._reflowing = False

    def _content_changed(self, _event=None):
        region = self.canvas.bbox("all")
        if region:
            self.canvas.configure(scrollregion=region)

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
        for card in self.cards.values():
            try:
                card["menu"].destroy()
            except tk.TclError:
                pass
        super().destroy()
