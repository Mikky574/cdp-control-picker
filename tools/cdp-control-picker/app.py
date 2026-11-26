"""Native desktop control picker. Connect only after an explicit user action."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import shutil
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit

from cdp_client import CDPClient
from workspace import Workspace, atomic_write, safe_url, sanitize_text
from zcode_runner import ZCodeRunner
from browser_launcher import launch_or_reuse


RESOURCE_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
SCRIPT = RESOURCE_DIR / 'page_picker.js'
VERSION = '0.1.0'
BINDING = '__cdpControlPickerSave'


def skill_source():
    bundled = RESOURCE_DIR / 'skills' / 'cdp-page-contract'
    return bundled if bundled.is_dir() else Path(__file__).resolve().parents[2] / 'skills' / 'cdp-page-contract'


def default_workspace():
    parent = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[3]
    return parent / 'CDP-Picker-Workspace'


def identity(url):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.netloc, parsed.path


class PickerApp:
    def __init__(self, root, workspace_path=None, endpoint='http://127.0.0.1:9222'):
        self.root = root
        self.events = queue.Queue()
        self.client = None
        self.pages = []
        self.current_target = None
        self.current_identity = None
        self.capture_enabled = False
        self.init_script_id = None
        self.records = {}
        self.runner = None
        self.running_id = None
        self.busy = False
        self.closed = False
        self.connecting = False
        self.connection_generation = 0
        self.endpoint = tk.StringVar(value=endpoint)
        self.workspace_path = tk.StringVar(value=str(workspace_path or default_workspace()))
        self.workspace_value = self.workspace_path.get()
        self.workspace_path.trace_add('write', lambda *_args: setattr(self, 'workspace_value', self.workspace_path.get()))
        self.page_choice = tk.StringVar()
        self.chrome_path = tk.StringVar(value='')
        self.cli_path = tk.StringVar(value=shutil.which('zcode') or (r'D:\ZCode\Bin\zcode.cmd' if Path(r'D:\ZCode\Bin\zcode.cmd').is_file() else ''))
        self.status = tk.StringVar(value='尚未连接 · 先刷新页面并选定你的专用 Chrome 标签页')
        self.capture_status = tk.StringVar(value='采集未开启')
        self._build()
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)
        self.root.after(100, self._drain)
        if (Path(self.workspace_path.get()) / 'captures').is_dir():
            self.reload_records()

    def _build(self):
        r = self.root
        r.title('CDP 网页控件采集器')
        r.geometry('1180x860')
        r.minsize(900, 680)
        r.configure(background='#f5f7fa')
        style = ttk.Style(r)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), background='#f5f7fa', foreground='#142139')
        style.configure('TButton', padding=(12, 7), background='#ffffff', bordercolor='#ccd8e8')
        style.map('TButton', background=[('active', '#eaf1fc')])
        style.configure('Primary.TButton', background='#175bd7', foreground='#ffffff', bordercolor='#175bd7')
        style.map('Primary.TButton', background=[('active', '#104cb8'), ('disabled', '#a8bddf')], foreground=[('disabled', '#ffffff')])
        style.configure('TEntry', padding=5, fieldbackground='#ffffff')
        style.configure('TCombobox', padding=5, fieldbackground='#ffffff')
        style.configure('TLabelframe', background='#f5f7fa', bordercolor='#d7e1ee')
        style.configure('TLabelframe.Label', font=('Microsoft YaHei UI', 10, 'bold'))
        style.configure('Treeview', background='#ffffff', fieldbackground='#ffffff', rowheight=31)
        style.configure('Treeview.Heading', background='#eaf0f8', font=('Microsoft YaHei UI', 10, 'bold'), padding=(8, 7))
        banner = tk.Frame(r, bg='#142139', padx=26, pady=18)
        banner.pack(fill='x')
        tk.Label(banner, text='网页控件采集器', bg='#142139', fg='#ffffff', font=('Microsoft YaHei UI', 21, 'bold')).pack(anchor='w')
        tk.Label(banner, text='连接已有登录身份的 Chrome，在网页中右键标注功能，再交给 ZCode 整理能力草稿。', bg='#142139', fg='#bfd0ea', font=('Microsoft YaHei UI', 10)).pack(anchor='w', pady=(7, 0))
        body = ttk.Frame(r, padding=20)
        body.pack(fill='both', expand=True)
        connection = ttk.LabelFrame(body, text='浏览器连接', padding=13)
        connection.pack(fill='x')
        connection.columnconfigure(1, weight=1)
        ttk.Label(connection, text='CDP 入口').grid(row=0, column=0, padx=(0, 10), sticky='w')
        self.endpoint_entry = ttk.Entry(connection, textvariable=self.endpoint)
        self.endpoint_entry.grid(row=0, column=1, sticky='ew')
        self.launch_button = ttk.Button(connection, text='启动专用 Chrome', style='Primary.TButton', command=self.launch_browser)
        self.launch_button.grid(row=0, column=2, padx=(10, 5))
        self.refresh_button = ttk.Button(connection, text='刷新页面', command=self.refresh_pages)
        self.refresh_button.grid(row=0, column=3, padx=5)
        self.connect_button = ttk.Button(connection, text='连接所选页', style='Primary.TButton', command=self.connect_page)
        self.connect_button.grid(row=0, column=4, padx=5)
        self.disconnect_button = ttk.Button(connection, text='断开', command=self.disconnect, state='disabled')
        self.disconnect_button.grid(row=0, column=5, padx=(5, 0))
        ttk.Label(connection, text='目标页面').grid(row=1, column=0, sticky='w', pady=(9, 0))
        self.page_combo = ttk.Combobox(connection, state='readonly', textvariable=self.page_choice)
        self.page_combo.grid(row=1, column=1, columnspan=4, sticky='ew', pady=(9, 0))
        ttk.Button(connection, text='Chrome 路径', command=self.choose_chrome).grid(row=1, column=5, padx=(5, 0), pady=(9, 0))
        ttk.Label(connection, textvariable=self.status, foreground='#566b86', wraplength=1020).grid(row=2, column=0, columnspan=6, sticky='w', pady=(8, 0))
        capture = ttk.Frame(body)
        capture.pack(fill='x', pady=13)
        self.capture_button = ttk.Button(capture, text='开启右键采集', style='Primary.TButton', command=self.toggle_capture, state='disabled')
        self.capture_button.pack(side='left')
        ttk.Label(capture, textvariable=self.capture_status, foreground='#087b6b').pack(side='left', padx=14)
        ttk.Label(capture, text='首版：普通网页主页面 · Esc 取消浮层', foreground='#6a7b91').pack(side='right')
        save = ttk.Frame(body)
        save.pack(fill='x', pady=(0, 13))
        ttk.Label(save, text='保存到').pack(side='left', padx=(0, 10))
        ttk.Entry(save, textvariable=self.workspace_path).pack(side='left', fill='x', expand=True)
        ttk.Button(save, text='选择目录', command=self.choose_workspace).pack(side='left', padx=(9, 0))
        self.notebook = ttk.Notebook(body)
        self.notebook.pack(fill='both', expand=True)
        capture_tab = ttk.Frame(self.notebook, padding=(0, 8))
        task_tab = ttk.Frame(self.notebook, padding=(0, 8))
        self.notebook.add(capture_tab, text='控件记录')
        self.notebook.add(task_tab, text='ZCode 任务与日志')
        middle = ttk.Panedwindow(capture_tab, orient='horizontal')
        middle.pack(fill='both', expand=True)
        left = ttk.LabelFrame(middle, text='已保存控件', padding=10)
        right = ttk.LabelFrame(middle, text='控件与功能说明', padding=10)
        middle.add(left, weight=1)
        middle.add(right, weight=2)
        self.tree = ttk.Treeview(left, columns=('name', 'page'), show='headings', selectmode='browse', height=4)
        self.tree.heading('name', text='控件名称')
        self.tree.heading('page', text='所在页面')
        self.tree.column('name', width=155, minwidth=100)
        self.tree.column('page', width=155, minwidth=80)
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self.show_selected())
        ttk.Button(left, text='重新读取记录', command=self.reload_records).pack(anchor='w', pady=(9, 0))
        actions = ttk.Frame(right)
        actions.pack(fill='x', side='bottom', pady=(9, 0))
        ttk.Button(actions, text='回页高亮验证', command=self.highlight_selected).pack(side='left')
        ttk.Button(actions, text='打开采集目录', command=self.open_capture).pack(side='left', padx=8)
        ttk.Button(actions, text='复制短任务', command=self.copy_task).pack(side='left')
        self.detail = tk.Text(right, wrap='word', font=('Microsoft YaHei UI', 10), bg='#ffffff', fg='#243b59', borderwidth=0, padx=12, pady=10, height=6)
        self.detail.pack(fill='both', expand=True)
        cli = ttk.LabelFrame(task_tab, text='ZCode CLI · 仅生成能力草稿，不自动执行网页操作', padding=12)
        cli.pack(fill='x')
        ttk.Entry(cli, textvariable=self.cli_path).pack(side='left', fill='x', expand=True)
        ttk.Button(cli, text='选择 CLI', command=self.choose_cli).pack(side='left', padx=8)
        self.send_button = ttk.Button(cli, text='发送所选控件', style='Primary.TButton', command=self.send_selected)
        self.send_button.pack(side='left')
        self.stop_button = ttk.Button(cli, text='停止任务', command=self.stop_run, state='disabled')
        self.stop_button.pack(side='left', padx=(8, 0))
        self.log = tk.Text(task_tab, height=5, wrap='word', bg='#edf2f9', fg='#4c6280', borderwidth=0, font=('Microsoft YaHei UI', 9), padx=12, pady=9)
        self.log.pack(fill='both', expand=True, pady=(12, 0))
        self._write_detail('先连接页面并开启采集。\n\n在网页中右键普通控件，填写用途并保存。这里会显示定位信息、功能说明和预期结果。\n\n控件采集与定位验证不等于业务能力已经验证。')
        self._log('准备就绪。可点“启动专用 Chrome”，首次在该窗口登录网站；不安装 ZCode 技能或修改默认模型。')

    def _log(self, text):
        self.log.insert('end', sanitize_text(text, 5000).strip() + '\n')
        self.log.see('end')

    def _write_detail(self, text):
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('1.0', text)
        self.detail.configure(state='disabled')

    def _background(self, function):
        def run():
            try:
                function()
            except Exception as error:
                self.events.put(('error', sanitize_text(error, 3000)))
            finally:
                self.events.put(('idle', None))
        threading.Thread(target=run, daemon=True).start()

    def _event(self, event):
        self.events.put(('cdp', (self.connection_generation, event)))

    def _drain(self):
        if self.closed:
            return
        for _ in range(100):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'error':
                self._log('未完成：' + value)
                self.status.set(value)
            elif kind == 'pages':
                self.pages = value
                labels = [f'{index + 1}. {page.get("title") or "未命名"}  |  {safe_url(page.get("url", ""))}' for index, page in enumerate(value)]
                self.page_combo.configure(values=labels)
                self.page_choice.set('')
                self.status.set(f'发现 {len(value)} 个普通网页，请明确选择目标页面。' if value else '浏览器可连接；请先在 Chrome 中打开服务网站并登录，然后刷新页面。')
            elif kind == 'connected':
                self.current_target = value
                self.current_identity = identity(value.get('url', ''))
                self.capture_enabled = False
                self.capture_status.set('已连接 · 请核对网站账号后开启采集')
                self.status.set('已连接：' + safe_url(value.get('url', '')))
                self.capture_button.configure(state='normal', text='开启右键采集')
                self.disconnect_button.configure(state='normal')
                self.connect_button.configure(state='disabled')
                self.endpoint_entry.configure(state='disabled')
            elif kind == 'browser-started':
                self.launch_button.configure(state='normal')
                if value.get('endpoint'):
                    self.endpoint.set(value['endpoint'])
                reused = value.get('status') == 'reused'
                self.status.set('已复用现有 CDP 浏览器，请核对网站账号后选择页面。' if reused else '专用 Chrome 已启动，请在该窗口打开网站并登录，再刷新页面。')
                self._log('浏览器入口可用；网站登录由你在 Chrome 内完成。' + ('专用资料目录：' + str(value.get('profilePath')) if not reused else '现有实例的资料目录未自动核对。'))
                self.refresh_pages()
            elif kind == 'browser-launch-finished':
                self.launch_button.configure(state='normal')
            elif kind == 'capture-mode':
                self.capture_enabled = value
                self.capture_button.configure(text='关闭右键采集' if value else '开启右键采集')
                self.capture_status.set('采集已开启 · 在目标网页右键控件并填写用途' if value else '采集已关闭 · 普通右键已恢复')
            elif kind == 'saved':
                self.reload_records()
                cap_id = value['captureId']
                if self.tree.exists(cap_id):
                    self.tree.selection_set(cap_id)
                    self.tree.see(cap_id)
                self.show_selected()
                self._log('已保存控件：' + value['annotation'].get('name', cap_id))
            elif kind == 'highlight':
                result, capture_id = value
                if result.get('ok'):
                    self._log('定位验证通过：唯一可见控件已高亮。尚未执行业务操作。')
                else:
                    self._log('定位未通过：' + str(result.get('message') or result.get('reason') or '页面不匹配'))
            elif kind == 'disconnected':
                self._reset_connection()
            elif kind == 'cdp':
                generation, event = value
                if generation == self.connection_generation:
                    self._handle_cdp(event)
            elif kind == 'connect-finished':
                self.connecting = False
                self.refresh_button.configure(state='normal')
                if not self.client or not self.client.connected:
                    self.connect_button.configure(state='normal')
            elif kind == 'cli-output':
                _, stream, line = value
                self._log(f'{stream}: ' + line)
            elif kind == 'cli-done':
                run_id, state = value
                self.running_id = None
                self.send_button.configure(state='normal')
                self.stop_button.configure(state='disabled')
                self._log(f'任务 {run_id}：{state.get("status")}。产物仍是草稿，请查看采集目录。')
            elif kind == 'idle':
                self.busy = False
        self.root.after(100, self._drain)

    def _handle_cdp(self, event):
        method = event.get('method')
        params = event.get('params') or {}
        if method == 'Runtime.bindingCalled' and params.get('name') == BINDING:
            if not self.capture_enabled or not self.client or not self.client.connected:
                return
            payload = params.get('payload', '')
            if not isinstance(payload, str) or len(payload) > 100000:
                self._log('忽略无效采集资料。')
                return
            self._background(lambda: self._save_binding(payload, params.get('executionContextId')))
        elif method == 'CDP.disconnected' and not params.get('intentional'):
            self._log('浏览器连接已断开：' + str(params.get('reason', '')))
            self._reset_connection()
        elif method == 'Page.frameNavigated':
            frame = params.get('frame') or {}
            if not frame.get('parentId') and frame.get('url') and self.current_identity:
                if identity(frame['url']) != self.current_identity and self.capture_enabled:
                    self._log('页面身份已变化，暂停采集；核对新页面后可重新开启。')
                    self._background(lambda: self._set_mode(False))

    def refresh_pages(self):
        endpoint = self.endpoint.get().strip()
        def refresh():
            client = CDPClient(endpoint)
            try:
                pages = [page for page in client.list_pages() if urlsplit(page.get('url', '')).scheme in {'http', 'https', 'file'}]
                self.events.put(('pages', pages))
            except Exception as error:
                raise RuntimeError('尚未找到可连接的浏览器。可先点击“启动专用 Chrome”；若已启动，请检查 CDP 地址。') from error
        self._background(refresh)

    def choose_chrome(self):
        filename = filedialog.askopenfilename(title='选择已有 Google Chrome 程序', filetypes=[('Google Chrome', 'chrome.exe'), ('可执行文件', '*.exe')], parent=self.root)
        if filename:
            self.chrome_path.set(filename)
            self._log('已选择 Chrome 程序路径；点击启动按钮生效。')

    def launch_browser(self):
        endpoint = self.endpoint.get().strip()
        chrome = self.chrome_path.get().strip() or None
        profile = default_workspace().parent / 'CDP-Chrome-Profile'
        self.launch_button.configure(state='disabled')
        self.status.set('正在准备专用 Chrome…')
        def launch():
            try:
                result = launch_or_reuse(endpoint, profile, chrome)
                self.events.put(('browser-started', result))
            finally:
                self.events.put(('browser-launch-finished', None))
        self._background(launch)

    def connect_page(self):
        if self.connecting or (self.client and self.client.connected):
            return
        index = self.page_combo.current()
        if index < 0 or index >= len(self.pages):
            messagebox.showinfo('选择页面', '请先刷新并明确选中目标页面。', parent=self.root)
            return
        target = dict(self.pages[index])
        endpoint = self.endpoint.get().strip()
        self.status.set('正在连接所选页面…')
        self.connecting = True
        self.connection_generation += 1
        generation = self.connection_generation
        self.connect_button.configure(state='disabled')
        self.refresh_button.configure(state='disabled')
        def connect():
            try:
                self._disconnect_now()
                client = CDPClient(endpoint, lambda event: self.events.put(('cdp', (generation, event))))
                client.connect(target)
                try:
                    client.call('Runtime.enable')
                    client.call('Page.enable')
                    client.call('Runtime.addBinding', {'name': BINDING})
                    client.call('Runtime.evaluate', {'expression': SCRIPT.read_text(encoding='utf-8'), 'returnByValue': True})
                except Exception:
                    client.close()
                    raise
                self.client = client
                self.events.put(('connected', target))
            finally:
                self.events.put(('connect-finished', None))
        self._background(connect)

    def _disconnect_now(self):
        client, self.client = self.client, None
        if client:
            try:
                if self.init_script_id:
                    client.call('Page.removeScriptToEvaluateOnNewDocument', {'identifier': self.init_script_id}, timeout=2)
                client.call('Runtime.evaluate', {'expression': 'window.__cdpControlPicker && window.__cdpControlPicker.disable()', 'returnByValue': True}, timeout=2)
            except Exception:
                pass
            client.close()
        self.init_script_id = None

    def disconnect(self):
        self.connection_generation += 1
        def disconnect():
            self._disconnect_now()
            self.events.put(('disconnected', None))
        self._background(disconnect)

    def _reset_connection(self):
        self.capture_enabled = False
        self.current_target = None
        self.current_identity = None
        self.capture_status.set('采集未开启')
        self.status.set('已断开；原 Chrome 与标签页保留。')
        self.capture_button.configure(state='disabled', text='开启右键采集')
        self.disconnect_button.configure(state='disabled')
        self.connect_button.configure(state='normal')
        self.endpoint_entry.configure(state='normal')

    def _set_mode(self, enabled):
        client = self.client
        if not client or not client.connected:
            raise RuntimeError('请先连接指定页面。')
        if self.init_script_id:
            client.call('Page.removeScriptToEvaluateOnNewDocument', {'identifier': self.init_script_id})
            self.init_script_id = None
        if enabled:
            current = client.call('Runtime.evaluate', {'expression': 'location.href', 'returnByValue': True})
            self.current_identity = identity(current.get('result', {}).get('value', ''))
            source = 'window.__CDP_PICKER_AUTO_ENABLE__ = true;\n' + SCRIPT.read_text(encoding='utf-8')
            self.init_script_id = client.call('Page.addScriptToEvaluateOnNewDocument', {'source': source})['identifier']
            result = client.call('Runtime.evaluate', {'expression': source, 'returnByValue': True})
            enabled_result = client.call('Runtime.evaluate', {'expression': 'window.__cdpControlPicker.enable()', 'returnByValue': True})
            if result.get('exceptionDetails') or enabled_result.get('exceptionDetails'):
                raise RuntimeError('页面采集层未能初始化。')
        else:
            client.call('Runtime.evaluate', {'expression': 'window.__CDP_PICKER_AUTO_ENABLE__ = false; window.__cdpControlPicker && window.__cdpControlPicker.disable()', 'returnByValue': True})
        self.events.put(('capture-mode', enabled))

    def toggle_capture(self):
        enabled = not self.capture_enabled
        self._background(lambda: self._set_mode(enabled))

    def _workspace(self):
        selected = Path(self.workspace_value.strip()).expanduser().resolve()
        if not self.workspace_value.strip():
            raise RuntimeError('请指定记录保存目录。')
        source = skill_source()
        if not (source / 'SKILL.md').is_file():
            raise RuntimeError('交付包缺少配套 CDP skill，请使用完整包。')
        durable = selected / '.capture-kit' / 'cdp-page-contract'
        durable.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, durable, dirs_exist_ok=True)
        return Workspace(selected, skill_path=durable / 'SKILL.md')

    def _save_binding(self, payload, context_id):
        card = json.loads(payload)
        client = self.client
        if not isinstance(card, dict) or not client:
            raise RuntimeError('采集资料格式错误。')
        token = (card.get('ephemeral') or {}).get('elementToken')
        if token:
            try:
                expression = 'window === window.top && window.__cdpControlPicker._resolveElement(' + json.dumps(token) + ')'
                args = {'expression': expression, 'returnByValue': False, 'objectGroup': 'control-picker'}
                if context_id:
                    args['contextId'] = context_id
                resolved = client.call('Runtime.evaluate', args)
                object_id = resolved.get('result', {}).get('objectId')
                if not object_id:
                    raise ValueError('控件已失效或不在主页面，请重新右键采集。')
                if object_id:
                    ax = client.call('Accessibility.getPartialAXTree', {'objectId': object_id, 'fetchRelatives': False})
                    nodes = ax.get('nodes') or []
                    if nodes:
                        role = (nodes[0].get('role') or {}).get('value')
                        name = (nodes[0].get('name') or {}).get('value')
                        if role:
                            card.setdefault('control', {})['role'] = {'value': role, 'source': 'cdp-accessibility'}
                        if name:
                            card.setdefault('control', {})['accessibleName'] = name
                client.call('Runtime.releaseObjectGroup', {'objectGroup': 'control-picker'})
            except ValueError:
                raise
            except Exception:
                pass  # DOM hints remain labelled as hints; never claim an AX result.
        else:
            raise ValueError('采集资料缺少当前控件身份，请重新右键采集。')
        card.setdefault('page', {})['targetId'] = self.current_target.get('id') if self.current_target else ''
        record = self._workspace().save_capture(card)
        self.events.put(('saved', record))

    def choose_workspace(self):
        folder = filedialog.askdirectory(title='选择采集与草稿保存目录', parent=self.root)
        if folder:
            self.workspace_path.set(folder)
            self.reload_records()

    def reload_records(self):
        try:
            path = Path(self.workspace_path.get())
            records = self._workspace().list_captures() if (path / 'captures').is_dir() else []
        except Exception as error:
            self._log('读取记录失败：' + str(error))
            return
        self.records = {item['captureId']: item for item in records}
        for item in self.tree.get_children():
            self.tree.delete(item)
        for item in records:
            name = item.get('annotation', {}).get('name') or item['captureId']
            self.tree.insert('', 'end', iid=item['captureId'], values=(name, item.get('page', {}).get('title') or item.get('page', {}).get('path')))

    def selected(self):
        selection = self.tree.selection()
        return self.records.get(selection[0]) if selection else None

    def show_selected(self):
        record = self.selected()
        if not record:
            return
        annotation = record.get('annotation', {})
        control = record.get('control', {})
        role = control.get('role', '')
        if isinstance(role, dict):
            role = str(role.get('value')) + '（' + str(role.get('source')) + '）'
        selectors = control.get('selectors') or control.get('locatorCandidates') or []
        lines = [annotation.get('name') or '未命名控件', '', '用途：' + annotation.get('description', ''), '允许动作：' + '、'.join(annotation.get('allowedActions', [])), '预期结果：' + annotation.get('expectedResult', ''), '', '页面：' + record.get('page', {}).get('url', ''), '控件：' + control.get('tagName', ''), '角色：' + str(role), '可访问名称：' + str(control.get('accessibleName') or control.get('domNameHint') or ''), '', '定位候选：']
        for candidate in selectors[:6]:
            lines.append('• ' + (str(candidate.get('value') or candidate.get('selector')) + '  匹配：' + str(candidate.get('matchCount', '未验证')) if isinstance(candidate, dict) else str(candidate)))
        lines.extend(['', '状态：已采集的能力草稿，业务流程尚未验证。', '文件：' + record['directory']])
        self._write_detail('\n'.join(lines))

    def highlight_selected(self):
        record = self.selected()
        if not record:
            messagebox.showinfo('选择控件', '请先选中一条已保存控件。', parent=self.root)
            return
        def highlight():
            if not self.client or not self.client.connected:
                raise RuntimeError('请先连接该控件所在的页面。')
            expression = 'window.__cdpControlPicker && window.__cdpControlPicker.highlight(' + json.dumps(record, ensure_ascii=False) + ')'
            response = self.client.call('Runtime.evaluate', {'expression': expression, 'returnByValue': True})
            result = response.get('result', {}).get('value') or {'ok': False, 'reason': 'page-layer-unavailable'}
            evidence = {'checkedAt': datetime.now(timezone.utc).isoformat(), 'result': result, 'businessFlowVerified': False}
            atomic_write(Path(record['directory']) / 'locator-check.json', json.dumps(evidence, ensure_ascii=False, indent=2))
            self.events.put(('highlight', (result, record['captureId'])))
        self._background(highlight)

    def open_capture(self):
        record = self.selected()
        if record and os.name == 'nt':
            os.startfile(record['directory'])

    def copy_task(self):
        record = self.selected()
        if not record:
            return
        text = f'读取 {record["taskPath"]}，按其中说明整理能力草稿。'
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._log('已复制短任务；详细规则在项目里的 task.md 和配套 skill。')

    def choose_cli(self):
        filename = filedialog.askopenfilename(title='选择已有 ZCode CLI', filetypes=[('ZCode CLI', '*.exe *.cmd'), ('所有文件', '*.*')], parent=self.root)
        if filename:
            self.cli_path.set(filename)

    def send_selected(self):
        record = self.selected()
        if not record:
            messagebox.showinfo('选择控件', '请先选中一条已保存控件。', parent=self.root)
            return
        if self.running_id:
            return
        try:
            self.runner = ZCodeRunner(self.cli_path.get(), cwd=self.workspace_path.get())
            self.running_id = self.runner.start(record['taskPath'], on_output=lambda *args: self.events.put(('cli-output', args)), on_done=lambda *args: self.events.put(('cli-done', args)))
            self.send_button.configure(state='disabled')
            self.stop_button.configure(state='normal')
            self._log('已提交一次 CLI 草稿任务：' + self.running_id)
            self.notebook.select(1)
        except Exception as error:
            self.running_id = None
            self._log('未能提交：' + str(error))

    def stop_run(self):
        if self.runner and self.running_id:
            self.runner.stop(self.running_id)
            self._log('已请求停止；未确认的结果不会自动重发。')

    def on_close(self):
        if self.connecting:
            messagebox.showinfo('正在连接', '请等待本次连接结束后再关闭。', parent=self.root)
            return
        if self.running_id:
            messagebox.showinfo('任务运行中', '请等待任务结束，或先点击“停止任务”后再关闭。', parent=self.root)
            return
        self.closed = True
        self._disconnect_now()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--endpoint', default='http://127.0.0.1:9222')
    parser.add_argument('--smoke-test', type=Path, help='Create the UI without connecting, write a startup report, then exit.')
    parser.add_argument('--package-check', type=Path, help='Verify bundled resources without opening a window.')
    args = parser.parse_args()
    if args.package_check:
        import websocket
        report = {'version': VERSION, 'pickerScriptExists': SCRIPT.is_file(), 'bundledSkillExists': (skill_source() / 'SKILL.md').is_file(), 'tkVersion': tk.TkVersion, 'websocketVersion': websocket.__version__, 'openedWindow': False, 'connectedBrowser': False}
        args.package_check.parent.mkdir(parents=True, exist_ok=True)
        args.package_check.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    app = PickerApp(root, args.workspace, args.endpoint)
    if args.smoke_test:
        root.withdraw()
        report = {'version': VERSION, 'tk': tk.TkVersion, 'pickerScriptExists': SCRIPT.is_file(), 'bundledSkillExists': (skill_source() / 'SKILL.md').is_file(), 'connected': bool(app.client), 'captureEnabled': app.capture_enabled}
        args.smoke_test.parent.mkdir(parents=True, exist_ok=True)
        args.smoke_test.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        root.after(50, app.on_close)
    root.mainloop()


if __name__ == '__main__':
    main()
