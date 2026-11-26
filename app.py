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
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit
import uuid

from cdp_client import CDPClient
from workspace import Workspace, atomic_write, page_identity, safe_url, sanitize_text
from browser_launcher import launch_or_reuse


RESOURCE_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
SCRIPT = RESOURCE_DIR / 'page_picker.js'
ICON = RESOURCE_DIR / 'assets' / 'app-icon.ico'
VERSION = '0.2.1'
BINDING = '__cdpControlPickerSave'
SAVE_WAIT_MESSAGE = '正在保存标注，完成后再发送。'


def skill_source():
    bundled = RESOURCE_DIR / 'skills' / 'cdp-page-contract'
    return bundled if bundled.is_dir() else Path(__file__).resolve().parent / 'skills' / 'cdp-page-contract'


def default_workspace():
    parent = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent / '.local'
    return parent / 'CDP-Picker-Workspace'


def default_zcode_path():
    local = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    return local / 'Programs' / 'ZCode' / 'ZCode.exe'


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
        self.editor_window=None
        self._page_records=[]
        self._records_expanded=True
        self._refresh_selected_id=None
        self._drag_origin=None
        self._log_lock=threading.Lock()
        self._save_lock=threading.RLock()
        self._workspace_lock=threading.RLock()
        self._prepared_workspaces=set()
        self._pending_saves={}
        self._save_barriers={}
        self.exporting=False
        self.sending=False
        self._layout_specs={}
        self._layout_jobs={}
        self._layout_delays={}
        self._layout_busy=set()
        self.endpoint=tk.StringVar(value=endpoint)
        self.workspace_path=tk.StringVar(value=str(workspace_path or default_workspace()))
        self.workspace_value=self.workspace_path.get()
        self.workspace_path.trace_add('write',lambda *_:setattr(self,'workspace_value',self.workspace_path.get()))
        self.page_choice=tk.StringVar(value='先刷新，再选择目标网页')
        self.chrome_path=tk.StringVar(value='')
        self.zcode_path=tk.StringVar(value=str(default_zcode_path()))
        self.status=tk.StringVar(value='在专用 Chrome 登录网站，再刷新并选页。')
        self.capture_status=tk.StringVar(value='采集未开启')
        self.saved_status=tk.StringVar(value='选页后显示本页已保存标注')
        self.sequence_ordered=tk.BooleanVar(value=False)
        self.selection_status=tk.StringVar(value='选中控件可查看详情，双击可编辑。')
        self._build()
        for variable in (self.status,self.saved_status):
            variable.trace_add('write',lambda *_:self._queue_fit())
        self.root.protocol('WM_DELETE_WINDOW',self.on_close)
        self.root.after(100,self._drain)
        self._log('小浮窗已就绪；只有点击发送时才运行 ZCode 任务并登记到项目。')
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
        self.settings_button=ttk.Button(header,text='设置',width=5,style='Small.TButton',command=self.open_settings);self.settings_button.pack(side='right')
        tools=ttk.Frame(body);tools.grid(row=1,column=0,sticky='ew',pady=(5,0))
        self.launch_button=ttk.Button(tools,text='启动 Chrome',style='Small.TButton',command=self.launch_browser);self.launch_button.pack(side='left')
        self.refresh_button=ttk.Button(tools,text='刷新',width=5,style='Small.TButton',command=self.refresh_pages);self.refresh_button.pack(side='left',padx=(5,0))
        ttk.Button(tools,text='日志',width=5,style='Small.TButton',command=self.open_log).pack(side='right')
        ttk.Button(tools,text='记录',width=5,style='Small.TButton',command=self.open_record_folder).pack(side='right',padx=(0,5))
        self.page_combo=ttk.Combobox(body,state='readonly',textvariable=self.page_choice,width=1);self.page_combo.grid(row=2,column=0,sticky='ew',pady=(6,0))
        self.page_combo.bind('<<ComboboxSelected>>',lambda _event:self._update_saved_status())
        actions=ttk.Frame(body);actions.grid(row=3,column=0,sticky='ew',pady=(6,0));actions.columnconfigure(0,weight=1)
        self.connect_button=ttk.Button(actions,text='连接并开启采集',style='Primary.TButton',command=self.connect_and_capture);self.connect_button.grid(row=0,column=0,sticky='ew')
        self.capture_button=self.connect_button
        self.disconnect_button=ttk.Button(actions,text='关闭',width=6,command=self.disconnect,state='disabled');self.disconnect_button.grid(row=0,column=1,padx=(6,0))
        self.status_label=tk.Label(body,textvariable=self.status,bg='#f5f7fa',fg='#526780',font=('Microsoft YaHei UI',9),wraplength=354,justify='left',anchor='w');self.status_label.grid(row=4,column=0,sticky='ew',pady=(6,0))
        self.saved_label=ttk.Label(body,textvariable=self.saved_status,foreground='#087b6b',font=('Microsoft YaHei UI',8),wraplength=354,justify='left',anchor='w');self.saved_label.grid(row=5,column=0,sticky='ew',pady=(4,0))
        records_header=ttk.Frame(body);records_header.grid(row=6,column=0,sticky='ew',pady=(6,0))
        ttk.Label(records_header,text='本页控件',font=('Microsoft YaHei UI',9,'bold')).pack(side='left')
        self.records_toggle=ttk.Button(records_header,text='收起',width=5,style='Small.TButton',command=self.toggle_records);self.records_toggle.pack(side='right')
        self.records_panel=ttk.Frame(body);self.records_panel.grid(row=7,column=0,sticky='ew',pady=(4,0));self.records_panel.columnconfigure(0,weight=1)
        table=ttk.Frame(self.records_panel);table.grid(row=0,column=0,sticky='ew');table.columnconfigure(0,weight=1)
        self.record_tree=ttk.Treeview(table,columns=('number','name','description'),show='headings',height=4,selectmode='browse')
        for key,title,width in (('number','序号',38),('name','名称',105),('description','用途',178)):
            self.record_tree.heading(key,text=title);self.record_tree.column(key,width=width,minwidth=width,stretch=key=='description')
        self.record_tree.grid(row=0,column=0,sticky='ew')
        scroll=ttk.Scrollbar(table,orient='vertical',command=self.record_tree.yview);scroll.grid(row=0,column=1,sticky='ns');self.record_tree.configure(yscrollcommand=scroll.set)
        self.record_tree.bind('<<TreeviewSelect>>',lambda _event:self._record_selection_changed())
        self.record_tree.bind('<Double-1>',lambda _event:self.edit_selected_record())
        self.record_detail=ttk.Label(self.records_panel,textvariable=self.selection_status,foreground='#526780',wraplength=354,justify='left',anchor='w');self.record_detail.grid(row=1,column=0,sticky='ew',pady=(4,0))
        record_tools=ttk.Frame(self.records_panel);record_tools.grid(row=2,column=0,sticky='ew',pady=(4,0))
        self.edit_record_button=ttk.Button(record_tools,text='编辑',style='Small.TButton',command=self.edit_selected_record);self.edit_record_button.pack(side='left')
        self.delete_record_button=ttk.Button(record_tools,text='删除',style='Small.TButton',command=self.delete_selected_record);self.delete_record_button.pack(side='left',padx=(4,0))
        self.move_up_button=ttk.Button(record_tools,text='上移',style='Small.TButton',command=lambda:self.move_selected_record(-1));self.move_up_button.pack(side='left',padx=(4,0))
        self.move_down_button=ttk.Button(record_tools,text='下移',style='Small.TButton',command=lambda:self.move_selected_record(1));self.move_down_button.pack(side='left',padx=(4,0))
        self.sequence_check=ttk.Checkbutton(self.records_panel,text='按此顺序操作',variable=self.sequence_ordered,command=self.save_sequence_declaration);self.sequence_check.grid(row=3,column=0,sticky='w',pady=(4,0))
        ttk.Label(self.records_panel,text='勾选后作为操作步骤发送；前置条件可在编辑中补充。',foreground='#526780',font=('Microsoft YaHei UI',8)).grid(row=4,column=0,sticky='w')
        footer=ttk.Frame(body);footer.grid(row=8,column=0,sticky='ew',pady=(7,0));footer.columnconfigure(0,weight=1)
        self.send_button=ttk.Button(footer,text='发给 ZCode',style='Primary.TButton',command=self.send_page_to_zcode);self.send_button.grid(row=0,column=0,sticky='ew')
        self.export_button=ttk.Button(footer,text='复制交接说明',style='Small.TButton',command=self.export_page_task);self.export_button.grid(row=0,column=1,padx=(6,0),sticky='e')
        # Kept off-layout for backend compatibility; the actual field opens in Settings.
        self.endpoint_entry=ttk.Entry(r,textvariable=self.endpoint)
        self._fit_window(r,body,380,210,wraps=(self.status_label,self.saved_label,self.record_detail))
        self._record_selection_changed()

    def _fit_window(self,window,body,min_width,min_height,wraps=()):
        # Fonts and native DPI metrics may settle after the first geometry pass.
        # Refit on map/theme/configure and text changes rather than fixing pixels.
        key=str(window)
        self._layout_specs[key]=(window,body,min_width,min_height,tuple(wraps))
        window.bind('<Map>',lambda event:self._queue_fit(window) if event.widget is window else None,add='+')
        window.bind('<Configure>',lambda event:self._queue_fit(window) if event.widget is window else None,add='+')
        window.bind('<Destroy>',lambda event:self._forget_window_layout(window) if event.widget is window else None,add='+')
        body.bind('<<ThemeChanged>>',lambda _event:self._queue_fit(window),add='+')
        self._apply_fit(key)
        self._queue_fit(window)
        def settle():
            self._layout_delays.pop(key,None);self._queue_fit(window)
        self._layout_delays[key]=window.after(150,settle)

    def _forget_window_layout(self,window):
        key=str(window)
        for jobs in (self._layout_jobs,self._layout_delays):
            pending=jobs.pop(key,None)
            if pending:
                try:window.after_cancel(pending)
                except tk.TclError:pass
        self._layout_specs.pop(key,None)

    def _queue_fit(self,window=None):
        window=window or self.root
        key=str(window)
        if self.closed or key not in self._layout_specs or key in self._layout_busy or key in self._layout_jobs:return
        try:
            if not window.winfo_exists():return
            self._layout_jobs[key]=window.after_idle(lambda:self._apply_fit(key))
        except tk.TclError:pass

    def _apply_fit(self,key):
        self._layout_jobs.pop(key,None)
        specification=self._layout_specs.get(key)
        if not specification or key in self._layout_busy:return
        window,body,min_width,min_height,wraps=specification
        self._layout_busy.add(key)
        try:
            if not window.winfo_exists():
                self._layout_specs.pop(key,None);return
            window.update_idletasks()
            width=max(min_width,body.winfo_reqwidth())
            padding=32 if window is not self.root else 20
            for label in wraps:label.configure(wraplength=max(180,width-padding))
            window.update_idletasks()
            width=max(min_width,body.winfo_reqwidth())
            height=max(min_height,body.winfo_reqheight())
            window.minsize(width,height)
            current=re.match(r'^(\d+)x(\d+)',window.geometry())
            if not current or (int(current[1]),int(current[2]))!=(width,height):window.geometry(f'{width}x{height}')
        except tk.TclError:
            self._layout_specs.pop(key,None)
        finally:self._layout_busy.discard(key)

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
        saving=bool(self._pending_save_count())
        editing=bool(getattr(self,'editor_window',None))
        self.connect_button.configure(state='disabled' if self.connecting or self.closing or self.capture_enabled or saving else 'normal',text='采集中 · 请在网页右键' if self.capture_enabled else '开启右键采集' if connected else '连接并开启采集')
        self.disconnect_button.configure(state='normal' if connected and not self.connecting and not self.closing and not saving else 'disabled')
        self.page_combo.configure(state='disabled' if connected or self.connecting or editing else 'readonly')
        self.send_button.configure(state='disabled' if self.sending or self.exporting or self.closing or saving or editing else 'normal',text='正在交接…' if self.sending else '正在保存标注…' if saving else '发给 ZCode')
        self.export_button.configure(state='disabled' if self.exporting or self.sending or self.closing or saving or editing else 'normal')
        for widgets,disabled in ((getattr(self,'_settings_connection_controls',()),connected or self.connecting),
                                 (getattr(self,'_settings_workspace_controls',()),connected or self.connecting or self.sending or self.exporting or saving or bool(getattr(self,'editor_window',None))),
                                 (getattr(self,'_settings_zcode_controls',()),self.sending)):
            for widget in widgets:
                try:
                    if widget.winfo_exists():widget.configure(state='disabled' if disabled else 'normal')
                except tk.TclError:pass
        if hasattr(self,'record_tree'):self._record_selection_changed()
        self._queue_fit()

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
                self._update_saved_status()
                self.status.set(f'发现 {len(value)} 个网页；'+('已保留所选页。' if retained is not None else '已选中唯一网页，点击连接开始。' if len(value)==1 else '请选择目标页后连接。' if value else '登录网站后再刷新。'))
                self._log(f'刷新发现 {len(value)} 个普通网页；没有自动连接。')
            elif kind=='connected':
                self.current_target=value;self.current_identity=identity(value.get('url',''));self.capture_enabled=False
                self._update_saved_status()
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
                self._update_saved_status();self.status.set('已保存：'+name+'。可继续右键采集。');self._log('已保存控件：'+name)
            elif kind=='save-finished':pass
            elif kind=='page-current-url':
                generation,url=value
                if generation==self.connection_generation and self.current_target:
                    self.current_target['url']=url;self._update_saved_status()
            elif kind=='page-bundle':
                try:
                    self.root.clipboard_clear();self.root.clipboard_append(value['shortTask'])
                except tk.TclError as error:
                    self.status.set(f'已合并 {value["captureCount"]} 个控件，但复制失败。请重试复制或在记录目录打开任务。')
                    self._log('任务已导出，剪贴板复制失败：'+str(error))
                else:
                    self.status.set(f'已合并 {value["captureCount"]} 个控件，交接说明已复制。请贴入 ZCode 对话。')
                self._log(f'已导出本页任务：{value["bundleId"]}，{value["captureCount"]} 个已标注控件。')
            elif kind=='page-export-finished':
                self.exporting=False;self.sending=False
            elif kind=='handoff-bundle':
                self.status.set(f'已合并 {value["captureCount"]} 个控件，正在交给 ZCode…')
                self._log(f'已准备页面任务：{value["bundleId"]}，{value["captureCount"]} 个控件；等待 CLI 完成与会话索引登记。')
            elif kind=='handoff-progress':
                if isinstance(value,dict):
                    message=value.get('message') or value.get('phase') or '正在交接到 ZCode…'
                    self.status.set(self._safe_text(message,160));self._log('ZCode：'+str(message))
            elif kind=='handoff-result':
                registered=isinstance(value,dict) and value.get('status')=='registered' and value.get('indexRegistered') is True and isinstance(value.get('sessionId'),str) and bool(value['sessionId'].strip())
                if registered:
                    self.status.set('已交给 ZCode，可在该对话继续补充。')
                    self._log('已完成 ZCode 任务并登记会话索引；打开同一项目继续聊。会话：'+value['sessionId'])
                else:
                    message=value.get('message') if isinstance(value,dict) else ''
                    prefix='ZCode 交接未完成。' if isinstance(value,dict) and value.get('status')=='failed' else 'ZCode 会话登记尚未确认。'
                    self.status.set(prefix+(' '+self._safe_text(message,150) if message else ' 请查看日志，不会自动重发。'))
                    self._log(prefix+' '+str(message or '没有收到桌面对话确认，不自动重发。'))
                if isinstance(value,dict):
                    if value.get('requestId'):self._log('ZCode 交接请求：'+str(value['requestId']))
                    if value.get('journalPath'):self._log('交接记录：'+str(value['journalPath']))
            elif kind=='disconnected':self._reset_connection()
            elif kind=='cdp':
                generation,event=value
                if generation==self.connection_generation:self._handle_cdp(event)
                else:self._finish_pending_save(event.get('_pickerSaveToken'))
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
            self.records={r['captureId']:r for r in records};self._update_saved_status()
        except Exception as error:self._log('读取记录失败：'+str(error));self.status.set(self._safe_text(error,130))

    def _update_saved_status(self):
        index=self.page_combo.current()
        target=self.current_target if self.client and self.client.connected else self.pages[index] if 0<=index<len(self.pages) else None
        try:
            selected=page_identity(target.get('url','') if target else '')
        except ValueError:
            self.saved_status.set(f'共已保存 {len(self.records)} 项 · 选页后显示本页数量')
            if hasattr(self,'record_tree'):self._refresh_record_list(None)
            return
        count=0
        for record in self.records.values():
            try:
                page=page_identity(record.get('page'))
            except ValueError:continue
            if (page['origin'],page['path'])==(selected['origin'],selected['path']):count+=1
        self.saved_status.set(f'本页已保存 {count} 项 · 仅含已落盘标注')
        if hasattr(self,'record_tree'):self._refresh_record_list(selected)

    def _selected_page(self):
        index=self.page_combo.current()
        target=self.current_target if self.client and self.client.connected else self.pages[index] if 0<=index<len(self.pages) else None
        return page_identity(target.get('url','') if target else '')

    def toggle_records(self):
        self._records_expanded=not self._records_expanded
        if self._records_expanded:self.records_panel.grid()
        else:self.records_panel.grid_remove()
        self.records_toggle.configure(text='收起' if self._records_expanded else '展开')
        self._queue_fit()

    def _refresh_record_list(self,page,select_id=None):
        selected=select_id or (self.record_tree.selection() or [None])[0]
        records=[];ordered=False
        if page:
            try:
                workspace=self._workspace()
                records=workspace.list_page_captures(page)
                ordered=workspace.page_sequence(page)['ordered']
            except Exception as error:
                self._log('读取本页控件失败：'+str(error));self.status.set(self._safe_text(error,130))
        self._page_records=records
        self.sequence_ordered.set(ordered)
        self.record_tree.delete(*self.record_tree.get_children())
        for number,record in enumerate(records,1):
            annotation=record.get('annotation') or {}
            name=annotation.get('name') or record.get('control',{}).get('name') or '未命名控件'
            self.record_tree.insert('', 'end',iid=record['captureId'],values=(number,self._safe_text(name,120),self._safe_text(annotation.get('description',''),500).replace('\n',' ')))
        ids=[record['captureId'] for record in records]
        if ids:
            selected=selected if selected in ids else ids[0]
            self.record_tree.selection_set(selected);self.record_tree.focus(selected);self.record_tree.see(selected)
        self._record_selection_changed()

    def _selected_record(self):
        selected=(self.record_tree.selection() or [None])[0]
        return next((record for record in self._page_records if record['captureId']==selected),None)

    def _record_selection_changed(self):
        record=self._selected_record()
        blocked=self.exporting or self.sending or self.closing or bool(self._pending_save_count()) or bool(self.editor_window)
        ids=[item['captureId'] for item in self._page_records]
        index=ids.index(record['captureId']) if record else -1
        for button,disabled in ((self.edit_record_button,not record),(self.delete_record_button,not record),
                                (self.move_up_button,index<=0),(self.move_down_button,index<0 or index>=len(ids)-1)):
            button.configure(state='disabled' if blocked or disabled else 'normal')
        self.sequence_check.configure(state='disabled' if blocked or not ids else 'normal')
        if record:
            annotation=record.get('annotation') or {}
            actions={'read':'读取','click':'点击','fill':'填写','select':'选择','check':'勾选'}
            text='、'.join(actions.get(item,item) for item in annotation.get('allowedActions',[])) or '未注明动作'
            expected=annotation.get('expectedResult') or '尚未填写'
            self.selection_status.set(self._safe_text(f'{text} · {annotation.get("description","")}\n预期：{expected}',210))
        else:self.selection_status.set('本页还没有标注，请在网页右键保存。')
        self._queue_fit()

    def _can_edit_records(self):
        if self.exporting or self.sending or self.closing or self._pending_save_count():
            self.status.set('正在保存或交接，请完成后再修改控件。');return False
        if self.editor_window:
            self.editor_window.lift();return False
        return True

    def _store_record_annotation(self,capture_id,annotation):
        if self.exporting or self.sending or self.closing or self._pending_save_count():
            raise RuntimeError('正在保存或交接，请完成后再保存修改。')
        record=self._workspace().update_capture(capture_id,annotation)
        self.records[capture_id]=record
        self._update_saved_status()
        self._refresh_record_list(self._selected_page(),capture_id)
        self.status.set('已保存修改；下次发送将使用更新后的控件信息。')
        return record

    def edit_selected_record(self):
        if not self._can_edit_records():return
        record=self._selected_record()
        if not record:return
        capture_id=record['captureId'];annotation=record.get('annotation') or {}
        w=tk.Toplevel(self.root);self.editor_window=w;w.title('编辑控件');w.resizable(False,False);w.transient(self.root);w.attributes('-topmost',True)
        body=ttk.Frame(w,padding=12);body.pack(fill='both',expand=True);body.columnconfigure(0,weight=1)
        name=tk.StringVar(value=annotation.get('name',''))
        ttk.Label(body,text='名称').grid(row=0,column=0,sticky='w')
        name_entry=ttk.Entry(body,textvariable=name,width=44);name_entry.grid(row=1,column=0,sticky='ew',pady=(2,6))
        fields={}
        for row,key,label,height in ((2,'description','功能说明',3),(4,'preconditions','前置条件（例如：先填写编号，再点查询）',2),(6,'expectedResult','预期结果',2)):
            ttk.Label(body,text=label).grid(row=row,column=0,sticky='w')
            text=tk.Text(body,height=height,width=44,wrap='word',font=('Microsoft YaHei UI',9),undo=True,relief='solid',borderwidth=1)
            value=annotation.get(key,'');value='\n'.join(str(item) for item in value) if isinstance(value,list) else str(value)
            text.insert('1.0',value);text.grid(row=row+1,column=0,sticky='ew',pady=(2,6));fields[key]=text
        action_frame=ttk.Frame(body);action_frame.grid(row=8,column=0,sticky='w')
        ttk.Label(action_frame,text='允许动作：').pack(side='left')
        actions={key:tk.BooleanVar(value=key in annotation.get('allowedActions',[])) for key in ('read','click','fill','select','check')}
        for key,label in (('read','读取'),('click','点击'),('fill','填写'),('select','选择'),('check','勾选')):
            ttk.Checkbutton(action_frame,text=label,variable=actions[key]).pack(side='left')
        control=record.get('control') or {};locators=control.get('selectors') or control.get('locatorCandidates') or []
        location=safe_url(record.get('page',{}).get('url',''))
        evidence='\n'.join(str(item.get('value') or item.get('selector') or item.get('role') or '') for item in locators[:3] if isinstance(item,dict))
        ttk.Label(body,text='页面与定位证据（只读）',foreground='#526780').grid(row=9,column=0,sticky='w',pady=(8,2))
        readonly=tk.Text(body,height=3,width=44,wrap='word',font=('Microsoft YaHei UI',8),background='#edf1f7',relief='flat')
        readonly.insert('1.0',self._safe_text(location+'\n'+evidence,1400));readonly.configure(state='disabled');readonly.grid(row=10,column=0,sticky='ew')
        error=tk.StringVar();error_label=ttk.Label(body,textvariable=error,foreground='#b42318',wraplength=400);error_label.grid(row=11,column=0,sticky='ew',pady=(5,0))
        footer=ttk.Frame(body);footer.grid(row=12,column=0,sticky='ew',pady=(7,0))
        def cancel():
            self.editor_window=None;w.destroy();self._controls()
        def save():
            updated={key:text.get('1.0','end-1c').strip() for key,text in fields.items()}
            updated.update(name=name.get().strip(),allowedActions=[key for key,value in actions.items() if value.get()])
            if not updated['name'] or not updated['description']:
                error.set('名称和功能说明不能为空。');self._queue_fit(w);return
            if len(updated['name'])>120 or any(len(updated[key])>1500 for key in fields):
                error.set('名称最多 120 字，其他字段各最多 1500 字。');self._queue_fit(w);return
            try:self._store_record_annotation(capture_id,updated)
            except Exception as failure:error.set(self._safe_text(failure,180));self._queue_fit(w);return
            cancel()
        ttk.Button(footer,text='取消',command=cancel).pack(side='right')
        ttk.Button(footer,text='保存修改',style='Primary.TButton',command=save).pack(side='right',padx=(0,6))
        w.protocol('WM_DELETE_WINDOW',cancel);w.grab_set();name_entry.focus_set();self._controls();self._fit_window(w,body,430,480,wraps=(error_label,))

    def delete_selected_record(self):
        if not self._can_edit_records():return
        record=self._selected_record()
        if not record:return
        name=record.get('annotation',{}).get('name') or '这个控件'
        if not messagebox.askyesno('删除本页控件',f'将“{self._safe_text(name,100)}”移入本地回收区？\n以后不再发送这项；已经发出的对话和旧交接包保留原快照。',parent=self.root):return
        try:
            self._workspace().delete_capture(record['captureId']);self.records.pop(record['captureId'],None)
            self._update_saved_status();self.status.set('已移入本地回收区，下次发送不再包含这项。')
        except Exception as error:self.status.set(self._safe_text(error,160))

    def move_selected_record(self,direction):
        if direction not in (-1,1) or not self._can_edit_records():return
        record=self._selected_record()
        if not record:return
        ids=[item['captureId'] for item in self._page_records];index=ids.index(record['captureId']);destination=index+direction
        if not 0<=destination<len(ids):return
        ids[index],ids[destination]=ids[destination],ids[index]
        try:
            page=self._selected_page();self._workspace().set_page_sequence(page,ids,ordered=True)
            self._refresh_record_list(page,record['captureId']);self.status.set('已调整操作顺序，下次发送会带上步骤。')
        except Exception as error:self.status.set(self._safe_text(error,160))

    def save_sequence_declaration(self):
        if not self._can_edit_records():
            try:self._refresh_record_list(self._selected_page())
            except ValueError:pass
            return
        try:
            page=self._selected_page();ids=[item['captureId'] for item in self._page_records]
            self._workspace().set_page_sequence(page,ids,ordered=self.sequence_ordered.get())
            self.status.set('已注明操作顺序；这表示你的流程说明，尚未执行验证。' if self.sequence_ordered.get() else '已取消操作顺序声明，列表仅供整理。')
        except Exception as error:
            self.status.set(self._safe_text(error,160));self._refresh_record_list(self._selected_page())

    def export_page_task(self):
        self._begin_page_task(send=False)

    def send_page_to_zcode(self):
        self._begin_page_task(send=True)

    def _begin_page_task(self,send=False):
        if self.exporting or self.sending:return
        if getattr(self,'editor_window',None):
            self.status.set('请先保存或取消当前控件修改，再发送。');self.editor_window.lift();return
        if self._pending_save_count():
            self.status.set(SAVE_WAIT_MESSAGE);self._controls();return
        index=self.page_combo.current()
        client=self.client if self.client and self.client.connected else None
        target=self.current_target if client else self.pages[index] if 0<=index<len(self.pages) else None
        if not target:
            self.status.set('请先选中要合并控件的页面。');return
        page_url=target.get('url','')
        desktop_path=self.zcode_path.get().strip() or None
        generation=self.connection_generation
        self.exporting=True;self.sending=bool(send);self._controls()
        self.status.set('正在合并本页已标注的控件…')
        def export():
            try:
                current_url=page_url
                workspace=self._workspace()
                if client:
                    current_url=self._saved_page_barrier(client,generation)
                # The barrier defines this explicit task's capture boundary.
                # Do not hold the callback lock during file IO on this worker.
                if self._pending_save_count():raise RuntimeError(SAVE_WAIT_MESSAGE)
                bundle=workspace.export_page_bundle(current_url)
                if send:
                    self.events.put(('handoff-bundle',bundle))
                    # Import lazily: startup/copy never starts the CLI or calls
                    # a model. The adapter confirms durable session indexing.
                    from zcode_handoff import ZCodeHandoff
                    result=ZCodeHandoff(workspace,commandPath=desktop_path).send(
                        bundle,on_event=lambda event:self.events.put(('handoff-progress',event)))
                    self.events.put(('handoff-result',result))
                else:self.events.put(('page-bundle',bundle))
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
        w=tk.Toplevel(self.root);self.settings_window=w;w.title('连接与交接设置');w.resizable(False,False);w.transient(self.root);w.attributes('-topmost',True)
        body=ttk.Frame(w,padding=16);body.pack(fill='both',expand=True);body.columnconfigure(1,weight=1)
        ttk.Label(body,text='CDP 地址').grid(row=0,column=0,sticky='w',padx=(0,12))
        ep=ttk.Entry(body,textvariable=self.endpoint);ep.grid(row=0,column=1,columnspan=2,sticky='ew')
        ttk.Label(body,text='记录目录').grid(row=1,column=0,sticky='w',pady=(12,0))
        folder=ttk.Entry(body,textvariable=self.workspace_path);folder.grid(row=1,column=1,sticky='ew',pady=(12,0))
        browse=ttk.Button(body,text='选择',width=5,command=self.choose_workspace);browse.grid(row=1,column=2,padx=(8,0),pady=(12,0))
        ttk.Label(body,text='Chrome 路径').grid(row=2,column=0,sticky='w',pady=(12,0))
        chrome=ttk.Entry(body,textvariable=self.chrome_path);chrome.grid(row=2,column=1,sticky='ew',pady=(12,0))
        browse_chrome=ttk.Button(body,text='选择',width=5,command=self.choose_chrome);browse_chrome.grid(row=2,column=2,padx=(8,0),pady=(12,0))
        ttk.Label(body,text='ZCode 程序').grid(row=3,column=0,sticky='w',pady=(12,0))
        zcode=ttk.Entry(body,textvariable=self.zcode_path);zcode.grid(row=3,column=1,sticky='ew',pady=(12,0))
        browse_zcode=ttk.Button(body,text='选择',width=5,command=self.choose_zcode);browse_zcode.grid(row=3,column=2,padx=(8,0),pady=(12,0))
        hint=ttk.Label(body,text='发送会把本页已标注控件合并，并复用上方记录目录作为 ZCode 工作区。\n任务权限：完全访问。AI 读取标注并确认收到，后续由你在 ZCode 对话里继续。\nChrome 路径留空可自动寻找；连接期间不能更改浏览器入口和记录目录。',foreground='#5a6b83',wraplength=520,justify='left');hint.grid(row=4,column=0,columnspan=3,sticky='ew',pady=(15,12))
        def done():
            if not self.workspace_value.strip():self.status.set('记录目录不能为空。');return
            self.reload_records();self._log('已应用本次设置。');w.destroy()
        self.settings_done_button=ttk.Button(body,text='完成',command=done);self.settings_done_button.grid(row=5,column=2,sticky='e')
        self._settings_connection_controls=(ep,chrome,browse_chrome)
        self._settings_workspace_controls=(folder,browse)
        self._settings_zcode_controls=(zcode,browse_zcode)
        self._controls()
        self._fit_window(w,body,570,285,wraps=(hint,))

    def choose_zcode(self):
        filename=filedialog.askopenfilename(title='选择 ZCode 桌面程序',initialfile='ZCode.exe',filetypes=[('ZCode 桌面程序','*.exe'),('所有文件','*.*')],parent=self.settings_window or self.root)
        if filename:
            self.zcode_path.set(filename)
            self._log('已选择 ZCode 桌面程序；将在点击发送时使用。')

    def _reset_connection(self):
        self.capture_enabled=False;self.current_target=None;self.current_identity=None
        self.capture_status.set('采集未开启');self.status.set('已关闭采集；Chrome 与标签页保持原样。');self.endpoint_entry.configure(state='normal');self._controls()

    def on_close(self):
        if self.editor_window:self.status.set('请先保存或取消当前控件修改，再退出。');self.editor_window.lift();return
        if self.sending:self.status.set('正在交接到 ZCode，请等待登记结果后再退出。');return
        if self.connecting:self.status.set('正在连接，请结束后再关闭浮窗。');return
        if self._pending_save_count():self.status.set('正在保存标注，请完成后再退出。');return
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


    def _pending_save_count(self):
        with self._save_lock:return len(self._pending_saves)

    def _finish_pending_save(self, token):
        if not token:return
        with self._save_lock:removed=self._pending_saves.pop(token,None) is not None
        if removed:self.events.put(('save-finished',None))

    def _event(self, event, generation=None):
        generation=self.connection_generation if generation is None else generation
        if generation!=self.connection_generation:return
        params=event.get('params') or {}
        if event.get('method')=='Runtime.bindingCalled' and params.get('name')==BINDING:
            payload=params.get('payload','')
            if isinstance(payload,str) and len(payload)<=100000:
                try:message=json.loads(payload)
                except ValueError:message=None
                if isinstance(message,dict) and '_pickerHostBarrier' in message:
                    nonce=message['_pickerHostBarrier']
                    with self._save_lock:barrier=self._save_barriers.get((generation,nonce)) if isinstance(nonce,str) else None
                    if barrier:barrier.set()
                    return
                token=uuid.uuid4().hex
                with self._save_lock:self._pending_saves[token]=generation
                event={**event,'_pickerSaveToken':token}
        self.events.put(('cdp',(generation,event)))

    def _saved_page_barrier(self,client,generation):
        """Wait off the Tk thread until preceding save callbacks are admitted."""
        nonce=uuid.uuid4().hex
        barrier=threading.Event()
        with self._save_lock:self._save_barriers[(generation,nonce)]=barrier
        try:
            expression="(() => { if (window !== window.top || typeof window.__cdpControlPickerSave !== 'function') return null; window.__cdpControlPickerSave(JSON.stringify({_pickerHostBarrier:"+json.dumps(nonce)+"})); return location.href; })()"
            response=client.call('Runtime.evaluate',{'expression':expression,'returnByValue':True})
            url=response.get('result',{}).get('value')
            if response.get('exceptionDetails') or not isinstance(url,str):
                raise RuntimeError('未能确认保存通道和当前页面，请重新连接后再整理。')
            if not barrier.wait(10):
                raise RuntimeError('尚未确认此前标注的保存通知，请稍后重试整理。')
            if client is not self.client or generation!=self.connection_generation:
                raise RuntimeError('页面连接已变化，请重新选页后整理。')
            if self._pending_save_count():raise RuntimeError(SAVE_WAIT_MESSAGE)
            self.events.put(('page-current-url',(generation,url)))
            return url
        finally:
            with self._save_lock:self._save_barriers.pop((generation,nonce),None)


    def _handle_cdp(self, event):
        method = event.get('method')
        params = event.get('params') or {}
        if method == 'Runtime.bindingCalled' and params.get('name') == BINDING:
            token=event.get('_pickerSaveToken')
            if not self.capture_enabled or not self.client or not self.client.connected:
                self._finish_pending_save(token)
                return
            payload = params.get('payload', '')
            if not isinstance(payload, str) or len(payload) > 100000:
                self._log('忽略无效采集资料。')
                self._finish_pending_save(token)
                return
            self.status.set(SAVE_WAIT_MESSAGE)
            def save():
                try:self._save_binding(payload,params.get('executionContextId'))
                finally:self._finish_pending_save(token)
            self._background(save)
        elif method == 'CDP.disconnected' and not params.get('intentional'):
            self._log('浏览器连接已断开：' + str(params.get('reason', '')))
            self._reset_connection()
        elif method == 'Page.frameNavigated':
            frame = params.get('frame') or {}
            if not frame.get('parentId') and frame.get('url') and self.current_target:
                self.current_target['url']=frame['url'];self._update_saved_status()
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
                client = CDPClient(endpoint, lambda event:self._event(event,generation))
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
        with self._workspace_lock:
            if selected not in self._prepared_workspaces or not (durable / 'SKILL.md').is_file():
                durable.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, durable, dirs_exist_ok=True)
                self._prepared_workspaces.add(selected)
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
        report = {'version': VERSION, 'uiMode': 'floating', 'minimumCollapsedWindowSize': [380, 210], 'controlManagement': ['view', 'edit', 'soft-delete', 'reorder', 'declare-sequence'], 'automaticModelCalls': False, 'handoffBackend': 'app-server-with-desktop-index', 'permissionMode': 'yolo', 'permissionLabel': '完全访问', 'zcodeRunnerImported': 'zcode_runner' in sys.modules, 'pickerScriptExists': SCRIPT.is_file(), 'bundledSkillExists': (skill_source() / 'SKILL.md').is_file(), 'iconExists': ICON.is_file(), 'tkVersion': tk.TkVersion, 'websocketVersion': websocket.__version__, 'openedWindow': False, 'connectedBrowser': False}
        args.package_check.parent.mkdir(parents=True, exist_ok=True)
        args.package_check.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('CDPControlPicker.Desktop')
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
