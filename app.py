"""Native desktop control picker. Connect only after an explicit user action."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import shutil
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from urllib.parse import urlsplit

from cdp_client import CDPClient
from workspace import Workspace, atomic_write, safe_url, sanitize_text
from browser_launcher import launch_or_reuse


RESOURCE_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
SCRIPT = RESOURCE_DIR / 'page_picker.js'
ICON = RESOURCE_DIR / 'assets' / 'app-icon.ico'
VERSION = '0.1.1'
BINDING = '__cdpControlPickerSave'


def skill_source():
    bundled = RESOURCE_DIR / 'skills' / 'cdp-page-contract'
    return bundled if bundled.is_dir() else Path(__file__).resolve().parent / 'skills' / 'cdp-page-contract'


def default_workspace():
    parent = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent / '.local'
    return parent / 'CDP-Picker-Workspace'


def identity(url):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.netloc, parsed.path



class PickerApp:
    def __init__(self, root, workspace_path=None, endpoint='http://127.0.0.1:9222'):
        self.root=root
        self.events=queue.Queue()
        self.client=None
        self.pages=[]
        self.current_target=None
        self.current_identity=None
        self.capture_enabled=False
        self.init_script_id=None
        self.records={}
        self.closed=False
        self.closing=False
        self.connecting=False
        self.busy=False
        self.connection_generation=0
        self.settings_window=None
        self._refresh_selected_id=None
        self._drag_origin=None
        self._log_lock=threading.Lock()
        self.exporting=False
        self.endpoint=tk.StringVar(value=endpoint)
        self.workspace_path=tk.StringVar(value=str(workspace_path or default_workspace()))
        self.workspace_value=self.workspace_path.get()
        self.workspace_path.trace_add('write',lambda *_:setattr(self,'workspace_value',self.workspace_path.get()))
        self.page_choice=tk.StringVar(value='先刷新，再选择目标网页')
        self.chrome_path=tk.StringVar(value='')
        self.status=tk.StringVar(value='在专用 Chrome 登录网站，再刷新并选页。')
        self.capture_status=tk.StringVar(value='采集未开启')
        self.saved_status=tk.StringVar(value='已保存 0 项 · 每项附 task.md')
        self._build()
        self.root.protocol('WM_DELETE_WINDOW',self.on_close)
        self.root.after(100,self._drain)
        self._log('小浮窗已就绪；不自动调用 ZCode 或其他模型。')
        if (Path(self.workspace_value)/'captures').is_dir(): self.reload_records()

    def _build(self):
        r=self.root
        r.title(f'CDP 控件采集 · {VERSION}')
        self.icon_applied=False
        if ICON.is_file():
            try:
                r.iconbitmap(str(ICON));self.icon_applied=True
            except tk.TclError:
                image_path=ICON.with_suffix('.png')
                if image_path.is_file():
                    self.icon_image=tk.PhotoImage(file=str(image_path));r.iconphoto(True,self.icon_image);self.icon_applied=True
        r.geometry('380x210');r.minsize(380,210);r.resizable(False,False);r.attributes('-topmost',True)
        r.configure(background='#f5f7fa')
        style=ttk.Style(r);style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',9),background='#f5f7fa',foreground='#142139')
        style.configure('TButton',padding=(8,4),background='#ffffff',bordercolor='#ccd8e8')
        style.map('TButton',background=[('active','#eaf1fc')])
        style.configure('Small.TButton',padding=(6,2))
        style.configure('Primary.TButton',background='#175bd7',foreground='#ffffff',bordercolor='#175bd7')
        style.map('Primary.TButton',background=[('active','#104cb8'),('disabled','#a8bddf')],foreground=[('disabled','#ffffff')])
        style.configure('TEntry',padding=5,fieldbackground='#ffffff')
        style.configure('TCombobox',padding=3,fieldbackground='#ffffff')
        body=ttk.Frame(r,padding=(10,7));body.pack(fill='both',expand=True);body.columnconfigure(0,weight=1)
        header=ttk.Frame(body);header.grid(row=0,column=0,sticky='ew')
        heading=ttk.Label(header,text='⋮⋮  网页控件采集',font=('Microsoft YaHei UI',10,'bold'),cursor='fleur');heading.pack(side='left')
        for widget in (header,heading):
            widget.bind('<ButtonPress-1>',self._drag_start);widget.bind('<B1-Motion>',self._drag_move)
        ttk.Button(header,text='设置',width=5,style='Small.TButton',command=self.open_settings).pack(side='right')
        tools=ttk.Frame(body);tools.grid(row=1,column=0,sticky='ew',pady=(5,0))
        self.launch_button=ttk.Button(tools,text='启动 Chrome',style='Small.TButton',command=self.launch_browser);self.launch_button.pack(side='left')
        self.refresh_button=ttk.Button(tools,text='刷新',width=5,style='Small.TButton',command=self.refresh_pages);self.refresh_button.pack(side='left',padx=(5,0))
        ttk.Button(tools,text='日志',width=5,style='Small.TButton',command=self.open_log).pack(side='right')
        ttk.Button(tools,text='记录',width=5,style='Small.TButton',command=self.open_record_folder).pack(side='right',padx=(0,5))
        self.page_combo=ttk.Combobox(body,state='readonly',textvariable=self.page_choice,width=1);self.page_combo.grid(row=2,column=0,sticky='ew',pady=(6,0))
        actions=ttk.Frame(body);actions.grid(row=3,column=0,sticky='ew',pady=(6,0));actions.columnconfigure(0,weight=1)
        self.connect_button=ttk.Button(actions,text='连接并开启采集',style='Primary.TButton',command=self.connect_and_capture);self.connect_button.grid(row=0,column=0,sticky='ew')
        self.capture_button=self.connect_button
        self.disconnect_button=ttk.Button(actions,text='关闭',width=6,command=self.disconnect,state='disabled');self.disconnect_button.grid(row=0,column=1,padx=(6,0))
        self.status_label=tk.Label(body,textvariable=self.status,bg='#f5f7fa',fg='#526780',font=('Microsoft YaHei UI',9),wraplength=354,justify='left',anchor='w',height=2);self.status_label.grid(row=4,column=0,sticky='ew',pady=(5,0))
        footer=ttk.Frame(body);footer.grid(row=5,column=0,sticky='ew',pady=(1,0));footer.columnconfigure(0,weight=1)
        ttk.Label(footer,textvariable=self.saved_status,foreground='#087b6b',font=('Microsoft YaHei UI',8),width=23).grid(row=0,column=0,sticky='w')
        self.export_button=ttk.Button(footer,text='复制本页任务',style='Small.TButton',command=self.export_page_task);self.export_button.grid(row=0,column=1,sticky='e')
        # Kept off-layout for backend compatibility; the actual field opens in Settings.
        self.endpoint_entry=ttk.Entry(r,textvariable=self.endpoint)
        self._fit_window(r,body,380,210)

    @staticmethod
    def _fit_window(window,body,min_width,min_height):
        # Point-sized Windows fonts grow with DPI; fixed pixel heights clip them.
        window.update_idletasks()
        width=max(min_width,body.winfo_reqwidth())
        height=max(min_height,body.winfo_reqheight())
        window.geometry(f'{width}x{height}')
        window.minsize(width,height)

    def _drag_start(self,event):
        self._drag_origin=(event.x_root-self.root.winfo_x(),event.y_root-self.root.winfo_y())

    def _drag_move(self,event):
        if self._drag_origin:
            self.root.geometry(f'+{max(0,event.x_root-self._drag_origin[0])}+{max(0,event.y_root-self._drag_origin[1])}')

    def log_path(self):
        return Path(self.workspace_value.strip()).expanduser().resolve()/'.logs'/'picker.log'

    @staticmethod
    def _safe_text(value,limit=1000):
        text=sanitize_text(value,max(limit,5000))
        text=re.sub(r'https?://[^\s<>"\']+',lambda m:safe_url(m.group()),text)
        text=re.sub(r'(?i)\b(token|session[_-]?id|cookie)\s*[:=]\s*[^\s,;，；]+',r'\1=[REDACTED]',text)
        return text[:limit]

    def _log(self,text):
        try:
            with self._log_lock:
                destination=self.log_path();destination.parent.mkdir(parents=True,exist_ok=True)
                with destination.open('a',encoding='utf-8') as stream:
                    stream.write(datetime.now().astimezone().isoformat(timespec='seconds')+' '+self._safe_text(text,3000).strip()+'\n')
        except OSError: pass

    def _controls(self):
        connected=bool(self.client and self.client.connected)
        self.connect_button.configure(state='disabled' if self.connecting or self.closing or self.capture_enabled else 'normal',text='采集中 · 请在网页右键' if self.capture_enabled else '开启右键采集' if connected else '连接并开启采集')
        self.disconnect_button.configure(state='normal' if connected and not self.connecting and not self.closing else 'disabled')
        self.page_combo.configure(state='disabled' if connected or self.connecting else 'readonly')

    def _drain(self):
        if self.closed:return
        for _ in range(100):
            try:kind,value=self.events.get_nowait()
            except queue.Empty:break
            if kind=='error':
                self._log('未完成：'+value);self.status.set(self._safe_text(value,130).replace('\n',' '))
            elif kind=='pages':
                previous=self._refresh_selected_id
                self.pages=value
                labels=[f'{i+1}. {self._safe_text(p.get("title") or "未命名",70)} | {safe_url(p.get("url",""))}' for i,p in enumerate(value)]
                self.page_combo.configure(values=labels)
                retained=next((i for i,p in enumerate(value) if previous and p.get('id')==previous),None)
                if retained is not None:self.page_combo.current(retained)
                elif len(value)==1:self.page_combo.current(0)
                else:self.page_choice.set('请选择目标网页' if value else '请先在 Chrome 中打开网站')
                self.status.set(f'发现 {len(value)} 个网页；'+('已保留所选页。' if retained is not None else '已选中唯一网页，点击连接开始。' if len(value)==1 else '请选择目标页后连接。' if value else '登录网站后再刷新。'))
                self._log(f'刷新发现 {len(value)} 个普通网页；没有自动连接。')
            elif kind=='connected':
                self.current_target=value;self.current_identity=identity(value.get('url',''));self.capture_enabled=False
                self.capture_status.set('已连接，正在检查采集层');self.status.set('已连接所选网页，正在开启采集…')
                self.endpoint_entry.configure(state='disabled')
            elif kind=='browser-started':
                if value.get('endpoint'):self.endpoint.set(value['endpoint'])
                self.status.set('已复用 Chrome；核对账号后刷新选页。' if value.get('status')=='reused' else 'Chrome 已启动；登录网站后刷新选页。')
                self._log('浏览器入口可用；网站登录由用户完成。');self.refresh_pages()
            elif kind=='browser-launch-finished':self.launch_button.configure(state='normal')
            elif kind=='capture-mode':
                self.capture_enabled=bool(value)
                self.capture_status.set('采集已开启' if value else '采集已关闭')
                self.status.set('采集已开启 · 切到所选网页，右键填写用途。' if value else '采集已关闭，普通右键已恢复。')
                self._log(self.capture_status.get()+'；未切换浏览器标签页。')
            elif kind=='saved':
                self.records[value['captureId']]=value
                name=self._safe_text(value.get('annotation',{}).get('name') or value['captureId'],26)
                self.saved_status.set(f'已保存 {len(self.records)} 项 · 最近：{name}');self.status.set('已保存：'+name+'。可继续右键采集。');self._log('已保存控件：'+name)
            elif kind=='page-bundle':
                self.root.clipboard_clear();self.root.clipboard_append(value['shortTask'])
                self.status.set(f'已合并 {value["captureCount"]} 个控件，任务已复制。请贴入 ZCode 对话。')
                self._log(f'已导出本页任务：{value["bundleId"]}，{value["captureCount"]} 个已标注控件。')
            elif kind=='page-export-finished':
                self.exporting=False;self.export_button.configure(state='normal')
            elif kind=='disconnected':self._reset_connection()
            elif kind=='cdp':
                generation,event=value
                if generation==self.connection_generation:self._handle_cdp(event)
            elif kind=='connect-finished':
                self.connecting=False;self.refresh_button.configure(state='normal')
            elif kind=='idle':self.busy=False
            elif kind=='exit-ready':
                self.closed=True;self.root.destroy();return
            self._controls()
        self.root.after(100,self._drain)

    def refresh_pages(self):
        index=self.page_combo.current()
        self._refresh_selected_id=self.pages[index].get('id') if 0<=index<len(self.pages) else None
        if self.current_target and self.client and self.client.connected:self._refresh_selected_id=self.current_target.get('id')
        self.status.set('正在刷新网页列表…')
        self._refresh_pages_backend()

    def connect_and_capture(self):
        if self.connecting or self.capture_enabled:return
        if self.client and self.client.connected:
            self.connect_button.configure(state='disabled');self._background(lambda:self._set_mode(True))
        else:self.connect_page()

    def reload_records(self):
        try:
            records=self._workspace().list_captures() if (Path(self.workspace_value)/'captures').is_dir() else []
            self.records={r['captureId']:r for r in records};self.saved_status.set(f'已保存 {len(records)} 项 · 每项附 task.md')
        except Exception as error:self._log('读取记录失败：'+str(error));self.status.set(self._safe_text(error,130))

    def export_page_task(self):
        if self.exporting:return
        index=self.page_combo.current()
        client=self.client if self.client and self.client.connected else None
        target=self.current_target if client else self.pages[index] if 0<=index<len(self.pages) else None
        if not target:
            self.status.set('请先选中要合并控件的页面。');return
        page_url=target.get('url','')
        self.exporting=True;self.export_button.configure(state='disabled')
        self.status.set('正在合并本页已标注的控件…')
        def export():
            try:
                current_url=page_url
                if client:
                    response=client.call('Runtime.evaluate',{'expression':'location.href','returnByValue':True})
                    current_url=response.get('result',{}).get('value')
                    if response.get('exceptionDetails') or not isinstance(current_url,str):
                        raise RuntimeError('未能确认当前页面，请刷新选页后再导出。')
                self.events.put(('page-bundle',self._workspace().export_page_bundle(current_url)))
            finally:self.events.put(('page-export-finished',None))
        self._background(export)

    @staticmethod
    def _open_external(path,text=False):
        if os.name=='nt':
            if text:subprocess.Popen(['notepad.exe',str(path)])
            else:os.startfile(str(path))
        else:subprocess.Popen(['open' if sys.platform=='darwin' else 'xdg-open',str(path)])

    def open_record_folder(self):
        try:
            root=Path(self.workspace_value).expanduser().resolve();folder=root/'captures' if (root/'captures').is_dir() else root
            folder.mkdir(parents=True,exist_ok=True);self._open_external(folder)
        except Exception as error:self.status.set(self._safe_text(error,130))

    def open_log(self):
        try:self._log('用户打开本地日志。');self._open_external(self.log_path(),text=True)
        except Exception as error:self.status.set(self._safe_text(error,130))

    def open_settings(self):
        if self.settings_window and self.settings_window.winfo_exists():self.settings_window.lift();return
        w=tk.Toplevel(self.root);self.settings_window=w;w.title('连接与保存设置');w.geometry('570x285');w.resizable(False,False);w.transient(self.root);w.attributes('-topmost',True)
        body=ttk.Frame(w,padding=16);body.pack(fill='both',expand=True);body.columnconfigure(1,weight=1)
        ttk.Label(body,text='CDP 地址').grid(row=0,column=0,sticky='w',padx=(0,12))
        ep=ttk.Entry(body,textvariable=self.endpoint);ep.grid(row=0,column=1,columnspan=2,sticky='ew')
        ttk.Label(body,text='记录目录').grid(row=1,column=0,sticky='w',pady=(12,0))
        folder=ttk.Entry(body,textvariable=self.workspace_path);folder.grid(row=1,column=1,sticky='ew',pady=(12,0))
        browse=ttk.Button(body,text='选择',width=5,command=self.choose_workspace);browse.grid(row=1,column=2,padx=(8,0),pady=(12,0))
        ttk.Label(body,text='Chrome 路径').grid(row=2,column=0,sticky='w',pady=(12,0))
        chrome=ttk.Entry(body,textvariable=self.chrome_path);chrome.grid(row=2,column=1,sticky='ew',pady=(12,0))
        browse_chrome=ttk.Button(body,text='选择',width=5,command=self.choose_chrome);browse_chrome.grid(row=2,column=2,padx=(8,0),pady=(12,0))
        ttk.Label(body,text='Chrome 路径留空可自动寻找。连接期间请先关闭采集再改设置。\n配置和 AI 交接按配套 skill；每条记录附 task.md。\n本窗口不运行 ZCode 或其他模型。',foreground='#5a6b83',wraplength=520).grid(row=3,column=0,columnspan=3,sticky='w',pady=(15,12))
        def done():
            if not self.workspace_value.strip():self.status.set('记录目录不能为空。');return
            self.reload_records();self._log('已应用本次设置。');w.destroy()
        ttk.Button(body,text='完成',command=done).grid(row=4,column=2,sticky='e')
        if self.connecting or (self.client and self.client.connected):
            for control in (ep,folder,browse,chrome,browse_chrome):control.configure(state='disabled')
        self._fit_window(w,body,570,285)

    def _reset_connection(self):
        self.capture_enabled=False;self.current_target=None;self.current_identity=None
        self.capture_status.set('采集未开启');self.status.set('已关闭采集；Chrome 与标签页保持原样。');self.endpoint_entry.configure(state='normal');self._controls()

    def on_close(self):
        if self.connecting:self.status.set('正在连接，请结束后再关闭浮窗。');return
        if self.closed or self.closing:return
        self.closing=True
        self.status.set('正在停止采集并退出；Chrome 将保留。')
        self._controls()
        def close():
            self._disconnect_now()
            self.events.put(('exit-ready',None))
        self._background(close)


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


    def _refresh_pages_backend(self):
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
            self.status.set('请先刷新并明确选中目标网页。')
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
                self._set_mode(True)
            finally:
                self.events.put(('connect-finished', None))
        self._background(connect)


    def _disconnect_now(self):
        client, self.client = self.client, None
        if client:
            try:
                if self.init_script_id:
                    client.call('Page.removeScriptToEvaluateOnNewDocument', {'identifier': self.init_script_id}, timeout=2)
                client.call('Runtime.evaluate', {'expression': 'window.__CDP_PICKER_AUTO_ENABLE__ = false; window.__cdpControlPicker && window.__cdpControlPicker.disable()', 'returnByValue': True}, timeout=2)
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
            state = enabled_result.get('result', {}).get('value')
            if not isinstance(state, dict) or state.get('ok') is False or state.get('enabled') is not True:
                raise RuntimeError('页面没有确认采集开启，请核对目标网页。')
            probe = client.call('Runtime.evaluate', {'expression': "(() => { const h=document.getElementById('__cdp_control_picker_root__'); return !!(window===window.top && h && h.isConnected && getComputedStyle(h).position==='fixed' && h.getBoundingClientRect().width>0); })()", 'returnByValue': True})
            if probe.get('exceptionDetails') or probe.get('result', {}).get('value') is not True:
                client.call('Runtime.evaluate', {'expression': 'window.__CDP_PICKER_AUTO_ENABLE__ = false; window.__cdpControlPicker && window.__cdpControlPicker.disable()', 'returnByValue': True})
                raise RuntimeError('网页采集层未显示，请换普通主页面后再试。')
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--endpoint', default='http://127.0.0.1:9222')
    parser.add_argument('--smoke-test', type=Path, help='Create the UI without connecting, write a startup report, then exit.')
    parser.add_argument('--package-check', type=Path, help='Verify bundled resources without opening a window.')
    args = parser.parse_args()
    if args.package_check:
        import websocket
        report = {'version': VERSION, 'uiMode': 'floating', 'defaultWindowSize': [380, 210], 'automaticModelCalls': False, 'zcodeRunnerImported': 'zcode_runner' in sys.modules, 'pickerScriptExists': SCRIPT.is_file(), 'bundledSkillExists': (skill_source() / 'SKILL.md').is_file(), 'iconExists': ICON.is_file(), 'tkVersion': tk.TkVersion, 'websocketVersion': websocket.__version__, 'openedWindow': False, 'connectedBrowser': False}
        args.package_check.parent.mkdir(parents=True, exist_ok=True)
        args.package_check.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('AIConnectionKnowledge.CDPControlPicker')
        except Exception:
            pass
    root = tk.Tk()
    app = PickerApp(root, args.workspace, args.endpoint)
    if args.smoke_test:
        root.update_idletasks()
        report = {'version': VERSION, 'uiMode': 'floating', 'windowSize': [root.winfo_width(), root.winfo_height()], 'alwaysOnTop': bool(root.attributes('-topmost')), 'automaticModelCalls': False, 'zcodeRunnerImported': 'zcode_runner' in sys.modules, 'logPath': str(app.log_path()), 'tk': tk.TkVersion, 'pickerScriptExists': SCRIPT.is_file(), 'bundledSkillExists': (skill_source() / 'SKILL.md').is_file(), 'iconExists': ICON.is_file(), 'iconApplied': app.icon_applied, 'connected': bool(app.client), 'captureEnabled': app.capture_enabled}
        args.smoke_test.parent.mkdir(parents=True, exist_ok=True)
        args.smoke_test.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        root.withdraw()
        root.after(50, app.on_close)
    root.mainloop()


if __name__ == '__main__':
    main()
