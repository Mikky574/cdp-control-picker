"""Native desktop control picker. Connect only after an explicit user action."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
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
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit
import uuid

from cdp_client import CDPClient
from workspace import Workspace, atomic_write, page_identity, safe_url, sanitize_text, _bundle_capture
from browser_launcher import DEFAULT_CDP_ENDPOINT, launch_or_reuse, optional_chrome_directory
from capture_list import CaptureList, _two_line_summary
from send_feedback import SendFeedback
from picker_settings import PickerSettings, SettingsError, clean_settings, recommended_workspace, validate_model


RESOURCE_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
SCRIPT = RESOURCE_DIR / 'page_picker.js'
ICON = RESOURCE_DIR / 'assets' / 'app-icon.ico'
VERSION = '0.3.1'
BINDING = '__cdpControlPickerSave'
SAVE_WAIT_MESSAGE = '正在保存标注，完成后再发送。'


def skill_source():
    bundled = RESOURCE_DIR / 'skills' / 'cdp-page-contract'
    return bundled if bundled.is_dir() else Path(__file__).resolve().parent / 'skills' / 'cdp-page-contract'


def default_workspace():
    parent = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent / '.local'
    return parent / 'CDP-Picker-Workspace'


def default_zcode_path():
    return ''  # Resolve the installed/PATH CLI only when explicitly requested.


def cdp_runtime_command():
    return [str(Path(sys.executable).resolve())] if getattr(sys, 'frozen', False) else [str(Path(sys.executable).resolve()), str(Path(__file__).resolve())]


def identity(url):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.netloc, parsed.path



class PickerApp:
    def __init__(self, root, workspace_path=None, endpoint=None, *, settings_path=None):
        self.root=root
        self._settings_store=PickerSettings(settings_path)
        self._settings_warning=''
        self._settings_load_failed=False
        try:saved=self._settings_store.load()
        except SettingsError as error:
            saved={'schemaVersion':1,'workspaceDir':'','zcodeCli':'','chromeExe':'','modelSelection':None}
            self._settings_warning=str(error)
            self._settings_load_failed=True
        selected_workspace=PickerSettings.choose_workspace(workspace_path,saved.get('workspaceDir'),default_workspace())
        if workspace_path is None and not saved.get('workspaceDir') and not default_workspace().is_dir():
            try:selected_workspace.mkdir(parents=True,exist_ok=True)
            except OSError:self._settings_warning='推荐学习目录暂不可用，请在设置中选择可写目录。'
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
        self._backend_probe_active=False
        self._backend_probe_request=None
        self._model_catalog_active=False
        self._model_catalog_request=None
        self._model_catalog_scope=None
        self._models=[]
        self._model_selection=saved.get('modelSelection')
        self._desktop_connection_active=False
        self._desktop_connection_request=None
        self._desktop_health={}
        self._bridge_install_active=False
        self._bridge_install_request=None
        self._open_session_active=False
        self._open_session_request=None
        self._layout_specs={}
        self._layout_jobs={}
        self._layout_delays={}
        self._layout_busy=set()
        self._send_timer_job=None
        self._send_progress_running=False
        self.endpoint=tk.StringVar(value=endpoint or saved.get('cdpEndpoint') or DEFAULT_CDP_ENDPOINT)
        self.workspace_path=tk.StringVar(value=str(selected_workspace))
        self.workspace_value=self.workspace_path.get()
        self.workspace_path.trace_add('write',lambda *_:setattr(self,'workspace_value',self.workspace_path.get()))
        self.page_choice=tk.StringVar(value='先刷新，再选择目标网页')
        self.chrome_path=tk.StringVar(value=saved.get('chromeExe',''))
        self.zcode_path=tk.StringVar(value=saved.get('zcodeCli') or str(default_zcode_path()))
        self.desktop_bridge_path=tk.StringVar(value=saved.get('desktopBridgeFile',''))
        self.desktop_status=tk.StringVar(value='尚未连接 ZCode 桌面；CLI 检测不代表桌面已接入。')
        self.backend_probe_status=tk.StringVar(value='尚未检测；检测只检查版本与后台连接，不调用模型。')
        self.model_provider_choice=tk.StringVar(value=(self._model_selection or {}).get('providerId','跟随后端当前模型'))
        self.model_choice=tk.StringVar(value=(self._model_selection or {}).get('modelId',''))
        self.model_catalog_status=tk.StringVar(value='从 ZCode 桌面读取 Composer 模型目录，不创建会话或发送模型请求。')
        self.status=tk.StringVar(value='在专用 Chrome 登录网站，再刷新并选页。')
        self.capture_status=tk.StringVar(value='采集未开启')
        self.saved_status=tk.StringVar(value='选页后显示本页已保存标注')
        self.records_heading=tk.StringVar(value='本页控件')
        self.sequence_ordered=tk.BooleanVar(value=False)
        self.send_title=tk.StringVar(value='等待发送')
        self.send_detail=tk.StringVar(value='进度和完成结果会保留在这里。')
        self.send_time=tk.StringVar(value='')
        self._build()
        for variable in (self.status,self.saved_status):
            variable.trace_add('write',lambda *_:self._queue_fit())
        self.root.protocol('WM_DELETE_WINDOW',self.on_close)
        self.root.after(100,self._drain)
        self._log('小浮窗已就绪；只有点击发送时才运行 ZCode 任务并登记到项目。')
        if self._settings_warning:self.status.set(self._settings_warning)
        elif not self._settings_store.path.exists():
            self._persist_settings()
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
        self.ui_font=tkfont.Font(root=r,family='Microsoft YaHei UI',size=9)
        self.heading_font=tkfont.Font(root=r,family='Microsoft YaHei UI',size=10,weight='bold')
        self._ui_scale=max(0.8,self.ui_font.metrics('linespace')/17)
        self.palette_width=self._dip(280)
        r.geometry(f'{self.palette_width}x{self._dip(210)}');r.minsize(self.palette_width,self._dip(210));r.resizable(False,False);r.attributes('-topmost',True)
        r.configure(background='#f5f7fa')
        style=ttk.Style(r);style.theme_use('clam')
        style.configure('.',font=self.ui_font,background='#f5f7fa',foreground='#142139')
        style.configure('TButton',padding=(self._dip(8),self._dip(4)),background='#ffffff',bordercolor='#dce3ec')
        style.map('TButton',background=[('active','#eaf1fc')])
        style.configure('Small.TButton',padding=(self._dip(6),self._dip(3)))
        style.configure('Quiet.TButton',padding=(self._dip(5),self._dip(3)),relief='flat',borderwidth=0,background='#f5f7fa',foreground='#526780')
        style.map('Quiet.TButton',background=[('active','#e9eef5')])
        style.configure('Primary.TButton',background='#175bd7',foreground='#ffffff',bordercolor='#175bd7')
        style.map('Primary.TButton',background=[('active','#104cb8'),('disabled','#a8bddf')],foreground=[('disabled','#ffffff')])
        style.configure('TEntry',padding=5,fieldbackground='#ffffff')
        style.configure('TCombobox',padding=3,fieldbackground='#ffffff')
        body=ttk.Frame(r,padding=(self._dip(10),self._dip(8)));body.pack(fill='both',expand=True);body.columnconfigure(0,weight=1)
        header=ttk.Frame(body);header.grid(row=0,column=0,sticky='ew')
        heading=ttk.Label(header,text='⋮⋮  网页控件采集',font=self.heading_font,cursor='fleur');heading.pack(side='left')
        for widget in (header,heading):
            widget.bind('<ButtonPress-1>',self._drag_start);widget.bind('<B1-Motion>',self._drag_move)
        self.settings_button=ttk.Button(header,text='设置',width=4,style='Quiet.TButton',command=self.open_settings);self.settings_button.pack(side='right')
        self.page_combo=ttk.Combobox(body,state='readonly',textvariable=self.page_choice,width=1);self.page_combo.grid(row=2,column=0,sticky='ew',pady=(self._dip(6),0))
        self.page_combo.bind('<<ComboboxSelected>>',lambda _event:self._update_saved_status())
        tools=ttk.Frame(body);tools.grid(row=1,column=0,sticky='ew',pady=(self._dip(6),0))
        self.launch_button=ttk.Button(tools,text='启动 Chrome',width=0,style='Small.TButton',command=self.launch_browser);self.launch_button.pack(side='left')
        self.refresh_button=ttk.Button(tools,text='刷新',width=4,style='Quiet.TButton',command=self.refresh_pages);self.refresh_button.pack(side='left',padx=(self._dip(4),0))
        ttk.Button(tools,text='日志',width=4,style='Quiet.TButton',command=self.open_log).pack(side='right')
        ttk.Button(tools,text='记录',width=4,style='Quiet.TButton',command=self.open_record_folder).pack(side='right')
        actions=ttk.Frame(body);actions.grid(row=3,column=0,sticky='ew',pady=(self._dip(6),0));actions.columnconfigure(0,weight=1)
        self.connect_button=ttk.Button(actions,text='连接并开启采集',width=0,style='Primary.TButton',command=self.connect_and_capture);self.connect_button.grid(row=0,column=0,sticky='ew')
        self.disconnect_button=ttk.Button(actions,text='关闭',width=4,style='Quiet.TButton',command=self.disconnect,state='disabled');self.disconnect_button.grid(row=0,column=1,padx=(self._dip(5),0))
        self.capture_button=self.connect_button
        self.status_label=tk.Label(body,textvariable=self.status,bg='#f5f7fa',fg='#6b7a90',font=self.ui_font,wraplength=self.palette_width-self._dip(24),justify='left',anchor='w');self.status_label.grid(row=4,column=0,sticky='ew',pady=(self._dip(6),0))
        records_header=ttk.Frame(body);records_header.grid(row=5,column=0,sticky='ew',pady=(self._dip(9),self._dip(5)))
        ttk.Label(records_header,textvariable=self.records_heading,font=(self.ui_font.actual('family'),9,'bold')).pack(side='left')
        self.records_toggle=ttk.Button(records_header,text='收起',width=4,style='Quiet.TButton',command=self.toggle_records);self.records_toggle.pack(side='right')
        self.records_panel=ttk.Frame(body);self.records_panel.grid(row=6,column=0,sticky='ew');self.records_panel.columnconfigure(0,weight=1)
        self.record_list=CaptureList(self.records_panel,on_select=self._select_record,on_edit=self._edit_record,on_delete=self._delete_record,on_move=self._move_record,sanitizer=self._safe_text)
        self.record_list.grid(row=0,column=0,sticky='ew')
        self.sequence_check=ttk.Checkbutton(self.records_panel,text='按此顺序操作',variable=self.sequence_ordered,command=self.save_sequence_declaration);self.sequence_check.grid(row=1,column=0,sticky='w',pady=(self._dip(7),0))
        self.send_panel=ttk.Frame(body,padding=(0,self._dip(6)));self.send_panel.grid(row=7,column=0,sticky='ew',pady=(self._dip(5),0));self.send_panel.columnconfigure(0,weight=1)
        send_header=ttk.Frame(self.send_panel);send_header.grid(row=0,column=0,sticky='ew')
        self.send_title_label=ttk.Label(send_header,textvariable=self.send_title,font=(self.ui_font.actual('family'),9,'bold'),foreground='#175bd7');self.send_title_label.pack(side='left')
        ttk.Label(send_header,textvariable=self.send_time,foreground='#6b7a90').pack(side='right')
        self.send_progress=ttk.Progressbar(self.send_panel,mode='indeterminate',length=1);self.send_progress.grid(row=1,column=0,sticky='ew',pady=(self._dip(4),0));self.send_progress.grid_remove()
        self.send_detail_label=ttk.Label(self.send_panel,textvariable=self.send_detail,foreground='#526780',wraplength=self.palette_width-self._dip(24),justify='left');self.send_detail_label.grid(row=2,column=0,sticky='ew',pady=(self._dip(4),0))
        receipt_actions=ttk.Frame(self.send_panel);receipt_actions.grid(row=3,column=0,sticky='ew')
        self.receipt_button=ttk.Button(receipt_actions,text='查看发送回执',width=0,style='Quiet.TButton',command=self.open_send_receipt);self.receipt_button.grid(row=0,column=0,sticky='w');self.receipt_button.grid_remove()
        self.view_zcode_button=ttk.Button(receipt_actions,text='在 ZCode 查看',width=0,style='Quiet.TButton',command=self.view_zcode_session);self.view_zcode_button.grid(row=0,column=1,sticky='w',padx=(self._dip(5),0));self.view_zcode_button.grid_remove()
        footer=ttk.Frame(body);footer.grid(row=8,column=0,sticky='ew',pady=(self._dip(5),0));footer.columnconfigure(0,weight=1)
        self.send_button=ttk.Button(footer,text='交给 ZCode 学习',style='Primary.TButton',command=self.send_page_to_zcode);self.send_button.grid(row=0,column=0,sticky='ew')
        self.export_button=ttk.Button(footer,text='复制交接说明',width=0,style='Quiet.TButton',command=self.export_page_task);self.export_button.grid(row=0,column=1,padx=(self._dip(5),0),sticky='e')
        # Kept off-layout for backend compatibility; the actual field opens in Settings.
        self.endpoint_entry=ttk.Entry(r,textvariable=self.endpoint)
        self._fit_window(r,body,self.palette_width,self._dip(210),wraps=(self.status_label,self.send_detail_label))
        self._record_selection_changed()
        self._render_send_feedback()

    def _dip(self,value):
        return max(1,round(value*self._ui_scale))

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
            width=min_width if window is self.root else max(min_width,body.winfo_reqwidth())
            padding=32 if window is not self.root else self._dip(20)
            for label in wraps:label.configure(wraplength=max(180,width-padding))
            window.update_idletasks()
            width=min_width if window is self.root else max(min_width,body.winfo_reqwidth())
            if window is self.root and hasattr(self,'record_list') and self._records_expanded:
                limit=max(min_height,window.winfo_screenheight()-self._dip(90))
                current=self.record_list.viewport_height()
                chrome=max(0,body.winfo_reqheight()-current)
                available=max(self.ui_font.metrics('linespace')*3,limit-chrome)
                self.record_list.set_viewport_height(min(self.record_list.preferred_viewport_height,available))
                window.update_idletasks()
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

    def _send_state(self):
        value=getattr(self,'workspace_value','').strip()
        workspace=str(Path(value).expanduser().resolve()) if value else None
        current=getattr(self,'send_feedback',None)
        if current is None or (workspace!=getattr(self,'_feedback_workspace',None) and not current.active):
            self._feedback_workspace=workspace
            receipt=(Path(workspace)/'.handoffs'/'ui-receipts.json').resolve() if workspace else None
            if receipt is not None and not receipt.is_relative_to(Path(workspace)):
                receipt=None
            self.send_feedback=SendFeedback(receipt_path=receipt)
        return self.send_feedback

    def _page_revision(self):
        records=getattr(self,'_page_records',[])
        if not records:return None
        target=self._current_handoff_target()
        if not target or not isinstance(target.get('url'),str):return None
        try:page=self._selected_page()
        except ValueError:return None
        identity={'workspace':getattr(self,'workspace_value',''),'page':page,
                  'transport':'desktop-native-bridge','desktopDiscoveryPath':self._bridge_discovery(),
                  'taskIntent':{'mode':'learn-and-verify','contractVersion':1,'actionPolicy':'infer-from-purpose'},
                  'endpoint':self.endpoint.get() if hasattr(self,'endpoint') else None,
                  'targetId':self._current_handoff_target_id(),
                  'initialUrlSha256':hashlib.sha256(target['url'].encode('utf-8')).hexdigest(),
                  'modelSelection':getattr(self,'_model_selection',None),
                  'ordered':bool(self.sequence_ordered.get()),
                  'captures':[{'captureId':record['captureId'],'control':record.get('control'),
                               'annotation':record.get('annotation')} for record in records]}
        return hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()

    def _current_handoff_target(self):
        if getattr(self,'client',None) and self.client.connected and getattr(self,'current_target',None):
            return self.current_target
        index=self.page_combo.current()
        pages=getattr(self,'pages',[])
        return pages[index] if 0<=index<len(pages) else None

    def _current_handoff_target_id(self):
        target=self._current_handoff_target()
        return target.get('id') if target else None

    @staticmethod
    def _handoff_snapshot(records):
        return [_bundle_capture(record,record['captureId'],page_identity(record.get('page'))) for record in records]

    def _render_send_feedback(self):
        if not hasattr(self,'send_panel'):return
        view=self._send_state().view()
        self.send_title.set(view['title'])
        self.send_title_label.configure(foreground='#087b6b' if view['phase'] in {'registered','duplicate','native-completed'} else '#b42318' if view['phase'] in {'failed','native-failed'} else '#9b6a00' if view['phase'] in {'unknown','index_failed'} else '#175bd7')
        self.send_detail.set(_two_line_summary(view['message'],self.ui_font,self.palette_width-self._dip(24)))
        finished=view.get('completedAt') or ''
        self.send_time.set(f'{view["elapsed"]} 秒' if view['active'] else finished[11:19])
        if view['active']:
            self.send_progress.grid()
            if not self._send_progress_running:self.send_progress.start(70);self._send_progress_running=True
            if self._send_timer_job is None:self._send_timer_job=self.root.after(500,self._tick_send_feedback)
        else:
            if self._send_progress_running:self.send_progress.stop();self._send_progress_running=False
            self.send_progress.grid_remove()
            if self._send_timer_job is not None:
                self.root.after_cancel(self._send_timer_job);self._send_timer_job=None
        if view['result'] and (not view['active'] or view['result'].get('nativeAccepted') is True):self.receipt_button.grid()
        else:self.receipt_button.grid_remove()
        if SendFeedback._native_accepted(view['result']):
            self.view_zcode_button.grid();self.view_zcode_button.configure(state='disabled' if self._open_session_active else 'normal')
        else:self.view_zcode_button.grid_remove()
        self._queue_fit()

    def _tick_send_feedback(self):
        self._send_timer_job=None
        if not self.closed:self._render_send_feedback()

    def open_send_receipt(self):
        view=self._send_state().view();result=view['result']
        lines=[view['title'],view['message']]
        if result.get('captureCount'):lines.append(f'控件数量：{result["captureCount"]}')
        if result.get('conversationTitle'):lines.append('对话：'+self._safe_text(result['conversationTitle'],120))
        if result.get('sessionId'):lines.append('ZCode 会话 ID：'+self._safe_text(result['sessionId'],200))
        if view['completedAt']:lines.append('记录时间：'+view['completedAt'])
        if view['summary']:lines.extend(['','ZCode 公开回复：',view['summary']])
        messagebox.showinfo('ZCode 发送回执','\n\n'.join(lines),parent=self.root)

    def view_zcode_session(self):
        if getattr(self,'_open_session_active',False) or self.closing:return
        result=self._send_state().view()['result']
        if not SendFeedback._native_accepted(result):
            self.status.set('当前没有已确认的 ZCode 原生会话可打开。');return
        session_id=result['sessionId'];workspace=self.workspace_value;discovery=self._bridge_discovery() or None
        request_id=uuid.uuid4().hex;self._open_session_request=request_id;self._open_session_active=True
        self.status.set('正在 ZCode 打开原对话；不会重新发送任务。');self._controls()
        def navigate():
            try:
                from desktop_bridge import DesktopBridge
                opened=DesktopBridge(workspace,discovery_path=discovery).open_session(session_id)
                confirmed=isinstance(opened,dict) and opened.get('status')=='opened' and opened.get('sessionId',session_id)==session_id
                message='原对话已在 ZCode 打开，可切过去继续。' if confirmed else '原对话尚未确认打开；请检查桌面连接，不要重新发送任务。'
            except Exception as error:
                confirmed=False
                message='原对话未打开；'+(self._safe_text(error,170) if type(error).__name__=='DesktopBridgeError' else '桌面导航未确认（'+type(error).__name__+'）。')+' 不要重新发送任务。'
            self.events.put(('native-open-result',{'requestId':request_id,'opened':confirmed,'message':message}))
        self._background(navigate)

    def _controls(self):
        connected=bool(self.client and self.client.connected)
        saving=bool(self._pending_save_count())
        editing=bool(getattr(self,'editor_window',None))
        feedback=self._send_state().view()
        self.connect_button.configure(state='disabled' if self.connecting or self.closing or self.capture_enabled or saving else 'normal',text='采集中 · 请在网页右键' if self.capture_enabled else '连接并开启采集')
        self.disconnect_button.configure(state='normal' if connected and not self.connecting and not self.closing and not saving else 'disabled')
        self.page_combo.configure(state='disabled' if connected or self.connecting or editing or self.sending or self.exporting else 'readonly')
        button_text='交给 ZCode 学习' if feedback['buttonText']=='发给 ZCode' else feedback['buttonText']
        self.send_button.configure(state='disabled' if self.sending or self.exporting or self.closing or saving or editing or getattr(self,'_model_catalog_active',False) or getattr(self,'_desktop_connection_active',False) or getattr(self,'_bridge_install_active',False) or feedback['blocked'] else 'normal',text='正在保存标注…' if saving else button_text)
        self.export_button.configure(state='disabled' if self.exporting or self.sending or self.closing or saving or editing else 'normal')
        for widgets,disabled in ((getattr(self,'_settings_connection_controls',()),connected or self.connecting),
                                 (getattr(self,'_settings_workspace_controls',()),connected or self.connecting or self.sending or self.exporting or saving or bool(getattr(self,'editor_window',None)) or getattr(self,'_model_catalog_active',False) or getattr(self,'_desktop_connection_active',False) or getattr(self,'_bridge_install_active',False)),
                                 (getattr(self,'_settings_zcode_controls',()),self.sending or getattr(self,'_backend_probe_active',False) or getattr(self,'_bridge_install_active',False)),
                                 (getattr(self,'_settings_desktop_controls',()),self.sending or getattr(self,'_desktop_connection_active',False) or getattr(self,'_model_catalog_active',False) or getattr(self,'_bridge_install_active',False))):
            for widget in widgets:
                try:
                    if widget.winfo_exists():widget.configure(state='disabled' if disabled else 'readonly' if isinstance(widget,ttk.Combobox) else 'normal')
                except tk.TclError:pass
        if hasattr(self,'record_list'):self._record_selection_changed()
        self._render_send_feedback()
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
                labels=[f'{i+1}. {self._safe_text(p.get("title") or "未命名网页",32)} · {urlsplit(safe_url(p.get("url",""))).hostname or "本地页面"}' for i,p in enumerate(value)]
                self.page_combo.configure(values=labels)
                retained=next((i for i,p in enumerate(value) if previous and p.get('id')==previous),None)
                if retained is not None:self.page_combo.current(retained)
                elif len(value)==1:self.page_combo.current(0)
                else:self.page_choice.set('请选择目标网页' if value else '请先在 Chrome 中打开网站')
                self._update_saved_status()
                self.status.set('已保留所选网页。' if retained is not None else '点击“开启采集”，然后在网页右键标注。' if len(value)==1 else f'找到 {len(value)} 个网页，请选页后开启采集。' if value else '请在 Chrome 打开网站后刷新。')
                if getattr(self,'_browser_notice',None):
                    self.status.set(self._browser_notice);self._browser_notice=None
                self._log(f'刷新发现 {len(value)} 个普通网页；没有自动连接。')
            elif kind=='connected':
                self.current_target=value;self.current_identity=identity(value.get('url',''));self.capture_enabled=False
                self._update_saved_status()
                self.capture_status.set('已连接，正在检查采集层');self.status.set('已连接所选网页，正在开启采集…')
                self.endpoint_entry.configure(state='disabled')
            elif kind=='browser-started':
                if value.get('endpoint'):self.endpoint.set(value['endpoint'])
                self._browser_notice=value.get('message') or ('已复用 Chrome；核对账号后刷新选页。' if value.get('status')=='reused' else 'Chrome 已启动；登录网站后刷新选页。')
                self.status.set(self._browser_notice)
                self._log('浏览器入口可用；网站登录由用户完成。');self.refresh_pages()
            elif kind=='browser-launch-finished':self.launch_button.configure(state='normal')
            elif kind=='backend-probe-result':self._show_backend_probe_result(value)
            elif kind=='model-catalog-result':self._show_model_catalog(value)
            elif kind=='desktop-connection-result':self._show_desktop_connection(value)
            elif kind=='bridge-install-progress':
                if value.get('requestId')==getattr(self,'_bridge_install_request',None):self.desktop_status.set(self._safe_text(value.get('message') or '正在安装连接桥。',240))
            elif kind=='bridge-install-result':self._show_bridge_install(value)
            elif kind=='native-open-result':
                if value.get('requestId')==getattr(self,'_open_session_request',None):
                    self._open_session_active=False;self.status.set(self._safe_text(value.get('message') or '原对话打开结果未确认。',260))
            elif kind=='capture-mode':
                self.capture_enabled=bool(value)
                self.capture_status.set('采集已开启' if value else '采集已关闭')
                self.status.set('采集已开启 · 切到所选网页，右键填写用途。' if value else '采集已关闭，普通右键已恢复。')
                self._log(self.capture_status.get()+'；未切换浏览器标签页。')
            elif kind=='saved':
                self.records[value['captureId']]=value
                name=self._safe_text(value.get('annotation',{}).get('name') or value['captureId'],26)
                self._update_saved_status(value['captureId']);self.status.set('已保存 · '+name);self._log('已保存控件：'+name)
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
                if self.sending and self._send_state().active:
                    self._send_state().fail('后台没有返回确认结果，请检查原对话；这份内容不会自动重发。',uncertain=True)
                self.exporting=False;self.sending=False
            elif kind=='handoff-bundle':
                self._send_state().progress('running',f'已合并 {value["captureCount"]} 个控件，正在等待 ZCode 处理。')
                self._log(f'已准备页面任务：{value["bundleId"]}，{value["captureCount"]} 个控件；等待 ZCode 桌面接纳与本轮处理。')
            elif kind=='handoff-progress':
                if isinstance(value,dict):
                    message=value.get('message') or value.get('phase') or '正在交接到 ZCode…'
                    self._send_state().progress(value.get('phase') or value.get('type') or 'running',self._safe_text(message,200),details=value);self._log('ZCode：'+str(message))
                    if value.get('nativeAccepted') is True and value.get('sessionId'):
                        self.status.set('已在 ZCode 打开，可切过去继续；本轮仍在处理。' if value.get('uiOpened') is True else '已在 ZCode 创建会话；尚未确认打开，正在处理。')
            elif kind=='handoff-result':
                self._send_state().complete(value)
                native_completed=isinstance(value,dict) and SendFeedback._native_completed(value)
                registered=isinstance(value,dict) and SendFeedback._registered(value)
                if native_completed:
                    self.status.set('ZCode 本轮已完成，可在已打开的原对话继续。' if value.get('uiOpened') is True else 'ZCode 本轮已完成；尚未确认打开，请到原对话查看。')
                    self._log('原生 ZCode 本轮完成；未用私有索引冒充桌面投递。会话：'+value['sessionId'])
                elif registered:
                    self.status.set('这份标注已发送过，没有重复提交。' if value.get('duplicate') else '已交给 ZCode，可在该对话继续补充。')
                    self._log('已完成 ZCode 任务并登记会话索引；打开同一项目继续聊。会话：'+value['sessionId'])
                else:
                    message=value.get('message') if isinstance(value,dict) else ''
                    prefix='ZCode 交接未完成。' if isinstance(value,dict) and value.get('status')=='failed' else 'ZCode 处理结果尚未确认。'
                    self.status.set(prefix+(' '+self._safe_text(message,150) if message else ' 请查看日志，不会自动重发。'))
                    self._log(prefix+' '+str(message or '没有收到桌面对话确认，不自动重发。'))
                if isinstance(value,dict):
                    if value.get('requestId'):self._log('ZCode 交接请求：'+str(value['requestId']))
                    if value.get('journalPath'):self._log('交接记录：'+str(value['journalPath']))
            elif kind=='handoff-error':
                error=value if isinstance(value,dict) else {'message':str(value),'uncertain':True}
                self._send_state().fail(error.get('message') or '发送没有返回确认。',uncertain=error.get('uncertain') is True)
                self._log('发送未完成：'+str(error.get('message','')))
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

    def _update_saved_status(self,select_id=None):
        index=self.page_combo.current()
        target=self.current_target if self.client and self.client.connected else self.pages[index] if 0<=index<len(self.pages) else None
        try:
            selected=page_identity(target.get('url','') if target else '')
        except ValueError:
            self.saved_status.set(f'共已保存 {len(self.records)} 项 · 选页后显示本页数量')
            if hasattr(self,'record_list'):self._refresh_record_list(None)
            return
        count=0
        for record in self.records.values():
            try:
                page=page_identity(record.get('page'))
            except ValueError:continue
            if (page['origin'],page['path'])==(selected['origin'],selected['path']):count+=1
        self.saved_status.set(f'本页已保存 {count} 项 · 仅含已落盘标注')
        if hasattr(self,'record_list'):self._refresh_record_list(selected,select_id)

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
        selected=select_id or (self.record_list.selection() or [None])[0]
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
        self.records_heading.set(f'本页控件 · {len(records)}')
        self.record_list.set_records(records,selected_id=selected)
        self.record_list.set_ordered(ordered)
        self._send_state().select(self._page_revision())
        self._record_selection_changed()
        self._controls()

    def _selected_record(self):
        selected=(self.record_list.selection() or [None])[0]
        return next((record for record in self._page_records if record['captureId']==selected),None)

    def _record_selection_changed(self):
        blocked=self.exporting or self.sending or self.closing or bool(self._pending_save_count()) or bool(self.editor_window)
        self.record_list.set_busy(blocked)
        self.record_list.set_ordered(self.sequence_ordered.get())
        self.sequence_check.configure(state='disabled' if blocked or not self._page_records else 'normal')
        self._queue_fit()

    def _select_record(self,capture_id):
        self._record_selection_changed()

    def _edit_record(self,capture_id):
        self.record_list.select(capture_id);self.edit_selected_record()

    def _delete_record(self,capture_id):
        self.record_list.select(capture_id);self.delete_selected_record()

    def _move_record(self,capture_id,direction):
        self.record_list.select(capture_id);self.move_selected_record(direction)

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
        self._update_saved_status(capture_id)
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
            updated['name']=name.get().strip()
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
            self._send_state().select(self._page_revision())
            self._record_selection_changed()
            self.status.set('已注明操作顺序；这表示你的流程说明，尚未执行验证。' if self.sequence_ordered.get() else '已取消操作顺序声明，列表仅供整理。')
            self._controls()
        except Exception as error:
            self.status.set(self._safe_text(error,160));self._refresh_record_list(self._selected_page())

    def export_page_task(self):
        self._begin_page_task(send=False)

    def send_page_to_zcode(self):
        self._begin_page_task(send=True)

    def _begin_page_task(self,send=False):
        if self.exporting or self.sending:return
        if getattr(self,'_bridge_install_active',False):self.status.set('连接桥正在安装，请完成后再发送。');return
        model_selection=None
        if send:
            if getattr(self,'_model_provider_pending',None):
                self.status.set('请先选择服务商下的具体模型，或使用后端当前模型。');return
            try:model_selection=validate_model(getattr(self,'_model_selection',None))
            except SettingsError as error:self.status.set(str(error));return
            if model_selection is not None:
                if getattr(self,'_model_catalog_scope',None)!=self._model_scope():
                    self.refresh_model_catalog();self.status.set('正在核对当前后端模型目录；确认后请再次点击发送。');return
                if not any(row.get('available') and all(row.get(key)==value for key,value in model_selection.items()) for row in self._models):
                    self.status.set('上次选择的模型不在当前可用目录中，请在设置中重新选择。');return
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
        cdp_endpoint=self.endpoint.get().strip()
        selected_target_id=target.get('id')
        runtime_command=cdp_runtime_command()
        try:expected_snapshot=self._handoff_snapshot(self._page_records) if send else None
        except (ValueError,TypeError,KeyError) as error:
            self.status.set('标注资料无效，请重新核对：'+self._safe_text(error,120));return
        if send and not self._send_state().begin(self._page_revision()):
            self._render_send_feedback();self._controls();return
        expected_ordered=bool(self.sequence_ordered.get()) if send else False
        desktop_path=self.zcode_path.get().strip() or None
        bridge_discovery=self._bridge_discovery() or None
        generation=self.connection_generation
        self.exporting=True;self.sending=bool(send);self._controls()
        self.status.set('正在合并本页已标注的控件…')
        def export():
            attempted=False
            try:
                current_url=page_url
                workspace=self._workspace()
                if client:
                    current_url=self._saved_page_barrier(client,generation)
                if send and page_identity(current_url)!=page_identity(page_url):
                    raise RuntimeError('当前网页已变化，请重新选页并核对标注后发送。')
                # The barrier defines this explicit task's capture boundary.
                # Do not hold the callback lock during file IO on this worker.
                if self._pending_save_count():raise RuntimeError(SAVE_WAIT_MESSAGE)
                bundle=workspace.export_page_bundle(current_url,intent='learn-and-verify',
                    connection={'endpoint':cdp_endpoint,'targetId':selected_target_id},runtime_command=runtime_command)
                if send:
                    payload=json.loads(Path(bundle['bundlePath']).read_text(encoding='utf-8'))
                    if ([item.get('capture') for item in payload.get('captures',[])]!=expected_snapshot
                            or bool((payload.get('sequence') or {}).get('ordered'))!=expected_ordered):
                        raise RuntimeError('标注刚有变化，请先核对最新控件列表，再点击发送。')
                    self.events.put(('handoff-bundle',bundle))
                    # Import lazily: startup/copy never starts the CLI or calls
                    # a model. The adapter confirms durable session indexing.
                    from zcode_handoff import ZCodeHandoff, inspect_handoff
                    model_options={'modelSelection':model_selection} if model_selection is not None else {}
                    if bridge_discovery is not None:model_options['discovery_path']=bridge_discovery
                    inspection=inspect_handoff(workspace,bundle,**model_options)
                    previous=inspection.get('previous') or {}
                    mode_upgrade=previous.get('status')=='registered' and previous.get('mode')!='yolo'
                    if inspection.get('duplicate') and not inspection.get('canSubmit') and not inspection.get('canRegister') and not mode_upgrade:
                        result={**previous,'duplicate':True}
                    else:
                        adapter=ZCodeHandoff(workspace,commandPath=desktop_path,**model_options)
                        attempted=inspection.get('canSubmit') is True
                        result=adapter.send(bundle,on_event=lambda event:self.events.put(('handoff-progress',event)))
                    self.events.put(('handoff-result',result))
                else:self.events.put(('page-bundle',bundle))
            except Exception as error:
                if send:self.events.put(('handoff-error',{'message':sanitize_text(error,2000),'uncertain':attempted}))
                else:raise
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
        body=ttk.Frame(w,padding=12);body.pack(fill='both',expand=True);body.columnconfigure(0,weight=1)
        tabs=ttk.Notebook(body);tabs.grid(row=0,column=0,columnspan=3,sticky='nsew')
        paths=ttk.Frame(tabs,padding=10);paths.columnconfigure(1,weight=1)
        backend=ttk.Frame(tabs,padding=10);backend.columnconfigure(1,weight=1)
        native=ttk.Frame(tabs,padding=10);native.columnconfigure(1,weight=1)
        tabs.add(paths,text='学习工作区');tabs.add(native,text='ZCode 桌面与模型');tabs.add(backend,text='CLI 兼容诊断')
        ttk.Label(paths,text='学习/记录目录').grid(row=0,column=0,sticky='w',padx=(0,10))
        folder=ttk.Entry(paths,textvariable=self.workspace_path,width=42);folder.grid(row=0,column=1,sticky='ew')
        browse=ttk.Button(paths,text='选择',width=5,command=self.choose_workspace);browse.grid(row=0,column=2,padx=(7,0))
        recommend=ttk.Button(paths,text='使用推荐目录',command=self.choose_recommended_workspace);recommend.grid(row=1,column=1,sticky='w',pady=(7,0))
        workspace_hint=ttk.Label(paths,text='推荐固定使用“网页能力学习”资料目录，保存标注与能力文件。ZCode 桌面的学习项目可使用自己的工作区；连接后可显式统一目录，旧资料不会自动搬移。会话正文由 ZCode 管理。',foreground='#526780',wraplength=480,justify='left');workspace_hint.grid(row=2,column=0,columnspan=3,sticky='ew',pady=(8,10))
        ttk.Label(paths,text='CDP 地址').grid(row=3,column=0,sticky='w')
        ep=ttk.Entry(paths,textvariable=self.endpoint);ep.grid(row=3,column=1,columnspan=2,sticky='ew')
        ttk.Label(paths,text='Chrome 路径').grid(row=4,column=0,sticky='w',pady=(10,0))
        chrome=ttk.Entry(paths,textvariable=self.chrome_path);chrome.grid(row=4,column=1,sticky='ew',pady=(10,0))
        browse_chrome=ttk.Button(paths,text='选择',width=5,command=self.choose_chrome);browse_chrome.grid(row=4,column=2,padx=(7,0),pady=(10,0))
        profile_hint=ttk.Label(paths,text='Chrome 路径留空时自动查找已安装的 Chrome 和便携包的可选 Chrome。没有浏览器时运行 Prepare-Chrome.cmd。9222 被占用可填写 http://127.0.0.1:9223 并保存。专用 Chrome 会保留自己单独的登录资料。',foreground='#526780',wraplength=480,justify='left');profile_hint.grid(row=5,column=0,columnspan=3,sticky='ew',pady=(10,0))
        ttk.Label(backend,text='ZCode CLI').grid(row=0,column=0,sticky='w',padx=(0,10))
        zcode=ttk.Entry(backend,textvariable=self.zcode_path,width=42);zcode.grid(row=0,column=1,sticky='ew')
        browse_zcode=ttk.Button(backend,text='选择',width=5,command=self.choose_zcode);browse_zcode.grid(row=0,column=2,padx=(7,0))
        self.backend_probe_button=ttk.Button(backend,text='检测连接',command=self.probe_zcode_backend);self.backend_probe_button.grid(row=1,column=0,sticky='w',pady=(8,0))
        self._settings_backend_text=tk.Text(backend,height=4,width=60,wrap='word',font=(self.ui_font.actual('family'),8),state='disabled')
        self._settings_backend_text.grid(row=2,column=0,columnspan=3,sticky='ew',pady=(6,8));self._render_backend_diagnostics()
        cli_hint=ttk.Label(backend,text='独立 CLI 检测只检查版本与 app-server 通道，不证明桌面对话已接入。CLI 留空自动识别 PATH；也可选择 exe/cmd/bat/cjs/mjs/js。主发送通过 ZCode 桌面扩展，CLI 不是实时桌面接入的替代品。',foreground='#526780',wraplength=480,justify='left');cli_hint.grid(row=3,column=0,columnspan=3,sticky='ew',pady=(8,0))
        ttk.Label(native,text='连接文件（可选）').grid(row=0,column=0,sticky='w',padx=(0,8))
        discovery=ttk.Entry(native,textvariable=self.desktop_bridge_path,width=36);discovery.grid(row=0,column=1,sticky='ew')
        browse_bridge=ttk.Button(native,text='选择',width=5,command=self.choose_desktop_bridge);browse_bridge.grid(row=0,column=2,padx=(7,0))
        self.install_bridge_button=ttk.Button(native,text='安装连接桥',command=self.install_desktop_connector);self.install_bridge_button.grid(row=1,column=0,sticky='w',pady=(9,0))
        self.desktop_connect_button=ttk.Button(native,text='连接并授权',command=self.connect_zcode_desktop);self.desktop_connect_button.grid(row=1,column=1,sticky='w',pady=(9,0))
        self.models_refresh_button=ttk.Button(native,text='刷新模型',command=self.refresh_model_catalog);self.models_refresh_button.grid(row=1,column=2,sticky='e',pady=(9,0))
        desktop_hint=ttk.Label(native,textvariable=self.desktop_status,foreground='#526780',wraplength=480,justify='left');desktop_hint.grid(row=2,column=0,columnspan=3,sticky='ew',pady=(7,0))
        ttk.Label(native,text='服务商').grid(row=3,column=0,sticky='w',pady=(8,0))
        self.model_provider_combo=ttk.Combobox(native,textvariable=self.model_provider_choice,state='readonly',width=1)
        self.model_provider_combo.grid(row=3,column=1,columnspan=2,sticky='ew');self.model_provider_combo.bind('<<ComboboxSelected>>',lambda _:self._choose_model_provider())
        ttk.Label(native,text='本会话模型').grid(row=4,column=0,sticky='w',pady=(8,0))
        self.model_combo=ttk.Combobox(native,textvariable=self.model_choice,state='readonly',width=1)
        self.model_combo.grid(row=4,column=1,columnspan=2,sticky='ew',pady=(8,0));self.model_combo.bind('<<ComboboxSelected>>',lambda _:self._choose_session_model())
        catalog_hint=ttk.Label(native,textvariable=self.model_catalog_status,foreground='#526780',wraplength=480,justify='left');catalog_hint.grid(row=5,column=0,columnspan=3,sticky='ew',pady=(7,0))
        self.desktop_workspace_button=ttk.Button(native,text='使用 ZCode 学习空间',command=self.use_desktop_learning_workspace);self.desktop_workspace_button.grid(row=6,column=0,columnspan=3,sticky='w',pady=(7,0))
        hint=ttk.Label(native,text='首次：安装连接桥 → 在 ZCode 命令中心“重新加载扩展”一次 → 连接并授权，无需退出 App。多实例需明确选择连接文件。模型只对本会话生效；发送请求完全访问，权限以 ZCode 原生授权及会话状态为准。安装完成不代表桌面已连接或已授权。',foreground='#5a6b83',wraplength=480,justify='left');hint.grid(row=7,column=0,columnspan=3,sticky='ew',pady=(9,4))
        self._populate_model_choices()
        def done():
            if not self.workspace_value.strip():self.status.set('记录目录不能为空。');return
            if not self._persist_settings(create_workspace=True,allow_repair=True):return
            self.reload_records();self._log('已保存本机路径与会话模型选择。');w.destroy()
        self.settings_done_button=ttk.Button(body,text='保存并完成',command=done);self.settings_done_button.grid(row=1,column=2,sticky='e',pady=(10,0))
        secondary=ttk.Frame(body);secondary.grid(row=1,column=0,columnspan=2,sticky='w',pady=(10,0))
        ttk.Button(secondary,text='打开记录目录',style='Quiet.TButton',command=self.open_record_folder).pack(side='left')
        ttk.Button(secondary,text='查看日志',style='Quiet.TButton',command=self.open_log).pack(side='left',padx=(8,0))
        self._settings_connection_controls=(ep,chrome,browse_chrome)
        self._settings_workspace_controls=(folder,browse,recommend)
        self._settings_zcode_controls=(zcode,browse_zcode,self.backend_probe_button)
        self._settings_desktop_controls=(discovery,browse_bridge,self.install_bridge_button,self.desktop_connect_button,self.models_refresh_button,self.model_provider_combo,self.model_combo,self.desktop_workspace_button)
        w.protocol('WM_DELETE_WINDOW',done)
        self._controls()
        self._fit_window(w,body,570,340,wraps=(hint,catalog_hint,workspace_hint,profile_hint,desktop_hint,cli_hint))

    def _persist_settings(self,create_workspace=False,allow_repair=False):
        if not hasattr(self,'_settings_store'):return True
        try:
            if getattr(self,'_settings_load_failed',False) and not allow_repair:
                raise SettingsError('原配置读取失败，请在设置中“保存并完成”确认重建；原文件未改动。')
            if getattr(self,'_model_provider_pending',None):raise SettingsError('请为所选服务商选择模型，或选择跟随后端。')
            value=clean_settings({'schemaVersion':1,'workspaceDir':self.workspace_value,
                'zcodeCli':self.zcode_path.get(),'chromeExe':self.chrome_path.get(),
                'cdpEndpoint':self.endpoint.get(),
                'desktopBridgeFile':self._bridge_discovery(),
                'modelSelection':getattr(self,'_model_selection',None)})
            if not value['workspaceDir']:raise SettingsError('请选择固定的学习工作区。')
            if create_workspace:Path(value['workspaceDir']).mkdir(parents=True,exist_ok=True)
            self._settings_store.save(value)
            self._settings_warning=''
            self._settings_load_failed=False
            return True
        except (SettingsError,OSError) as error:
            self.status.set('本机设置未保存：'+self._safe_text(error,150))
            return False

    def choose_recommended_workspace(self):
        if self.sending or self.exporting or self.closing or self._pending_save_count() or (self.client and self.client.connected):return
        self.workspace_path.set(str(recommended_workspace().resolve()))
        if self._persist_settings(create_workspace=True):self.reload_records()

    def _model_scope(self):
        return ('desktop-native-bridge',self._bridge_discovery(),self.workspace_value.strip())

    def _bridge_discovery(self):
        variable=getattr(self,'desktop_bridge_path',None)
        return variable.get().strip() if variable else ''

    def choose_desktop_bridge(self):
        selected=filedialog.askopenfilename(title='选择明确的 ZCode 桌面连接文件',filetypes=[('桌面连接文件','*.json')],parent=self.settings_window or self.root)
        if selected:self.desktop_bridge_path.set(selected)

    def connect_zcode_desktop(self):
        if getattr(self,'_desktop_connection_active',False) or getattr(self,'_bridge_install_active',False) or self.sending or self.exporting or self.closing:return
        if not self._persist_settings(create_workspace=True):return
        scope=self._model_scope();request_id=uuid.uuid4().hex
        self._desktop_connection_active=True;self._desktop_connection_request=request_id
        self.desktop_status.set('正在连接 ZCode 桌面；首次连接请在 ZCode 确认扩展授权。')
        self._controls()
        def connect():
            try:
                from desktop_bridge import DesktopBridge
                bridge=DesktopBridge(scope[2],discovery_path=scope[1] or None)
                grant=bridge.authorize_connection()
                granted=grant.get('granted') if isinstance(grant,dict) else None
                if not isinstance(granted,list) or any(not isinstance(item,str) for item in granted) or 'sessions.fullAccess' not in granted:
                    raise SettingsError('ZCode 未确认 sessions.fullAccess 原生会话授权，请检查授权窗口。')
                health=bridge.health()
                if not isinstance(health,dict) or health.get('nativeSessions') is not True or health.get('transport')!='zcode-extension-host':
                    raise SettingsError('当前 ZCode 桌面未确认提供原生会话接口，未标记连接成功。')
                if health.get('authorized') is not True:
                    raise SettingsError('原生授权回读未确认，请在 ZCode 完成授权后重新连接。')
                result={'ok':True,'health':{key:health[key] for key in ('workspacePath','applicationVersion','version') if key in health}}
            except Exception as error:
                message=self._safe_text(error,220) if isinstance(error,SettingsError) or type(error).__name__=='DesktopBridgeError' else 'ZCode 桌面连接未确认（'+type(error).__name__+'）；请检查扩展是否启用并完成授权。'
                result={'ok':False,'message':message}
            self.events.put(('desktop-connection-result',{'requestId':request_id,'scope':scope,'result':result}))
        self._background(connect)

    def install_desktop_connector(self):
        if getattr(self,'_bridge_install_active',False) or getattr(self,'_desktop_connection_active',False) or getattr(self,'_model_catalog_active',False) or self.sending or self.exporting or self.closing:return
        if not self._persist_settings(create_workspace=True):return
        command=self.zcode_path.get().strip() or None;workspace=self.workspace_value
        request_id=uuid.uuid4().hex;self._bridge_install_request=request_id;self._bridge_install_active=True
        self.desktop_status.set('正在通过所选 CLI 安装随包连接桥；不调用模型、不代为授权、不重启 ZCode。')
        self._controls()
        def install():
            try:
                from bridge_setup import install_connector
                result=install_connector(commandPath=command,cwd=workspace,on_event=lambda event:
                    self.events.put(('bridge-install-progress',{'requestId':request_id,'message':event.get('message') or event.get('phase')})))
            except Exception as error:
                result={'installed':False,'message':self._safe_text(error,300) if isinstance(error,ValueError) else '安装未完成（'+type(error).__name__+'）；没有重启 ZCode 或发送模型任务。'}
            self.events.put(('bridge-install-result',{'requestId':request_id,'commandInput':command,'result':result}))
        self._background(install)

    def _show_bridge_install(self,value):
        if not isinstance(value,dict) or value.get('requestId')!=getattr(self,'_bridge_install_request',None):return
        self._bridge_install_active=False
        result=value.get('result') if isinstance(value.get('result'),dict) else {}
        self._desktop_health={};self._model_catalog_scope=None;self._models=[]
        self._populate_model_choices()
        self.desktop_status.set(self._safe_text(result.get('message') or '连接桥安装结果未确认。',380)+
            '\n安装时 CLI：'+self._safe_text(value.get('commandInput') or '自动识别（PATH）',180))
        self.status.set('连接桥已安装；请在 ZCode 重新加载扩展一次，再连接并授权。' if result.get('installed') is True else '连接桥安装未完成，请查看设置中的阶段与退出码。')

    def _show_desktop_connection(self,value):
        if not isinstance(value,dict) or value.get('requestId')!=getattr(self,'_desktop_connection_request',None):return
        self._desktop_connection_active=False;self._desktop_health={}
        result=value.get('result') if isinstance(value.get('result'),dict) else {}
        if tuple(value.get('scope',()))!=self._model_scope():
            self.desktop_status.set('连接设置已改变，请重新连接当前 ZCode 桌面。');return
        if result.get('ok') is True:
            self._desktop_health=result.get('health') or {}
            directory=self._desktop_health.get('workspacePath')
            self.desktop_status.set('已连接 ZCode 桌面，原生会话授权已确认；未创建业务会话或调用模型。'+('\nZCode 学习空间：'+self._safe_text(directory,300) if directory else ''))
        else:self.desktop_status.set(self._safe_text(result.get('message') or '尚未连接 ZCode 桌面。',220))
        self._queue_fit(self.settings_window) if getattr(self,'settings_window',None) else None

    def use_desktop_learning_workspace(self):
        if self.sending or self.exporting or self.closing or self._pending_save_count() or (self.client and self.client.connected):return
        directory=(getattr(self,'_desktop_health',None) or {}).get('workspacePath')
        if not isinstance(directory,str) or not Path(directory).is_absolute() or not Path(directory).is_dir():
            self.desktop_status.set('ZCode 尚未提供可用的本机学习空间，请先连接桌面。');return
        self.workspace_path.set(str(Path(directory).resolve()))
        if self._persist_settings():
            self.reload_records();self.status.set('已使用 ZCode 学习空间；原目录标注保留，未搬移。')

    def _render_backend_diagnostics(self):
        widget=getattr(self,'_settings_backend_text',None)
        try:
            if widget and widget.winfo_exists():
                widget.configure(state='normal');widget.delete('1.0','end')
                widget.insert('1.0',self.backend_probe_status.get());widget.configure(state='disabled')
        except tk.TclError:pass

    def refresh_model_catalog(self):
        if getattr(self,'_model_catalog_active',False) or getattr(self,'_bridge_install_active',False) or self.sending or self.exporting or self.closing:return
        if not self._persist_settings(create_workspace=True):return
        scope=self._model_scope()
        request_id=uuid.uuid4().hex
        self._model_catalog_request=request_id;self._model_catalog_active=True
        self.model_catalog_status.set('正在读取 ZCode 桌面 Composer 模型目录；不创建会话或调用模型。')
        self._controls()
        def refresh():
            try:
                from desktop_bridge import DesktopBridge
                models=DesktopBridge(scope[2],discovery_path=scope[1] or None).list_models()
                result={'ok':isinstance(models,list) and bool(models),'models':models,'transport':'desktop-native-bridge',
                    'message':'已读取 ZCode 桌面模型目录；未创建会话或调用模型。' if models else 'ZCode 桌面没有返回可用模型，请先在 ZCode 配置后刷新。'}
            except Exception as error:
                result={'ok':False,'models':[],'message':self._safe_text(error,200) if type(error).__name__=='DesktopBridgeError' else '模型目录读取失败（'+type(error).__name__+'）。'}
            self.events.put(('model-catalog-result',{'requestId':request_id,'scope':scope,'result':result}))
        self._background(refresh)

    def _show_model_catalog(self,value):
        if not isinstance(value,dict) or value.get('requestId')!=getattr(self,'_model_catalog_request',None):return
        self._model_catalog_active=False
        self._models=[];self._model_catalog_scope=None
        if tuple(value.get('scope',()))!=self._model_scope():
            self.model_catalog_status.set('后端或工作区已改变，未采用旧目录；请重新刷新模型。');self._populate_model_choices();return
        result=value.get('result') if isinstance(value.get('result'),dict) else {}
        if result.get('ok') is True:
            for row in result.get('models',[]) if isinstance(result.get('models'),list) else []:
                try:selection=validate_model({key:row.get(key) for key in ('providerId','modelId')})
                except (ValueError,AttributeError):continue
                self._models.append({**selection,'label':self._safe_text(row.get('label') or selection['modelId'],150),
                    'providerLabel':self._safe_text(row.get('providerLabel') or row.get('providerName') or selection['providerId'],150),
                    'nativeId':row.get('id'),
                    'available':row.get('available') is True or ('available' not in row and result.get('transport')=='desktop-native-bridge')})
            self._model_catalog_scope=self._model_scope()
        current=result.get('current') or {}
        current_text='；当前 '+str(current.get('providerId',''))+'/'+str(current.get('modelId','')) if current else ''
        self.model_catalog_status.set(self._safe_text(result.get('message') or '模型目录未就绪。',180)+current_text)
        self._populate_model_choices()
        window=getattr(self,'settings_window',None)
        try:
            if window and window.winfo_exists():self._queue_fit(window)
        except tk.TclError:pass

    def _populate_model_choices(self):
        providers={}
        for row in getattr(self,'_models',[]):
            if row.get('available'):providers.setdefault(row['providerId'],row.get('providerLabel') or row['providerId'])
        self._provider_choices={'跟随后端当前模型':None}
        self._provider_choices.update({label+' ('+key+')':key for key,label in providers.items()})
        selected=getattr(self,'_model_selection',None) or {}
        provider=getattr(self,'_model_provider_pending',None) or selected.get('providerId')
        display=next((label for label,key in self._provider_choices.items() if key==provider),provider or '跟随后端当前模型')
        self.model_provider_choice.set(display)
        self._model_choices={}
        for row in getattr(self,'_models',[]):
            if row.get('available') and row['providerId']==provider:
                label=row['label']+' ('+row['modelId']+')'
                self._model_choices[label]={key:row[key] for key in ('providerId','modelId')}
        chosen=next((label for label,selection in self._model_choices.items() if selection==selected),
                    '' if getattr(self,'_model_provider_pending',None) else selected.get('modelId','') if provider else '使用后端当前模型')
        self.model_choice.set(chosen)
        for name,values in (('model_provider_combo',tuple(self._provider_choices)),('model_combo',tuple(self._model_choices) if provider else ('使用后端当前模型',))):
            widget=getattr(self,name,None)
            try:
                if widget and widget.winfo_exists():widget.configure(values=values)
            except tk.TclError:pass

    def _choose_model_provider(self):
        if self.model_provider_choice.get() not in self._provider_choices:return
        provider=self._provider_choices.get(self.model_provider_choice.get())
        self._model_provider_pending=provider
        if provider is None:self._model_selection=None
        self._populate_model_choices()
        if provider is None:self._persist_settings()
        self._update_saved_status();self._controls()

    def _choose_session_model(self):
        selection=self._model_choices.get(self.model_choice.get())
        if selection is None:return
        self._model_selection=validate_model(selection);self._model_provider_pending=None
        self._persist_settings();self._update_saved_status();self._controls()

    def choose_zcode(self):
        filename=filedialog.askopenfilename(title='选择 ZCode CLI',filetypes=[('ZCode CLI','*.exe *.cmd *.bat *.cjs *.mjs *.js'),('Windows 程序','*.exe'),('命令脚本','*.cmd *.bat'),('Node CLI','*.cjs *.mjs *.js'),('所有文件','*.*')],parent=self.settings_window or self.root)
        if filename:
            self.zcode_path.set(filename)
            self._log('已选择 ZCode CLI；将在点击发送时使用。')

    def probe_zcode_backend(self):
        if getattr(self,'_backend_probe_active',False):return
        if self.sending or self.exporting or self.closing:
            self.backend_probe_status.set('当前正在保存或交接，完成后可检测后端。');return
        command=self.zcode_path.get().strip()
        workspace=self.workspace_value.strip()
        request_id=uuid.uuid4().hex
        self._backend_probe_request=request_id
        self._backend_probe_active=True
        self.backend_probe_status.set('正在检测输入快照：'+self._safe_text(command or '自动识别',180)+'\n仅检查版本与 app-server stdio，不调用模型。')
        self._render_backend_diagnostics()
        self._controls()
        def probe():
            try:
                from zcode_diagnostics import probe_backend
                result=probe_backend(commandPath=command or None,cwd=workspace,timeout_seconds=12)
            except Exception as error:
                result={'ok':False,'phase':'probe','message':'后端检测未完成（'+type(error).__name__+'）；未提交模型任务。'}
            self.events.put(('backend-probe-result',{'requestId':request_id,'input':command,'workspace':workspace,'result':result}))
        self._background(probe)

    def _show_backend_probe_result(self,value):
        if not isinstance(value,dict) or value.get('requestId')!=getattr(self,'_backend_probe_request',None):return
        self._backend_probe_active=False
        result=value.get('result') if isinstance(value.get('result'),dict) else {}
        ok=result.get('ok') is True
        phase=self._safe_text(result.get('phase') or 'unknown',60)
        title='后端检测通过' if ok else '后端检测失败（'+phase+'）'
        lines=[title,'已检测输入：'+self._safe_text(value.get('input') or '自动识别（PATH）',240),
               '已检测工作区：'+self._safe_text(value.get('workspace') or '未选择',280)]
        command=result.get('command')
        if isinstance(command,list) and all(isinstance(part,str) for part in command):
            lines.append('实际启动参数：'+self._safe_text(json.dumps(command,ensure_ascii=False),900))
        lines.append('运行类型：'+self._safe_text(result.get('runtimeType') or '未确认',80)+
                     '；版本：'+self._safe_text(result.get('cliVersion') or '未确认',80)+
                     '；传输：'+self._safe_text(result.get('transport') or '未确认',80))
        lines.append(self._safe_text(result.get('message') or '未收到有效检测结果。',320))
        self.backend_probe_status.set('\n'.join(lines))
        self._render_backend_diagnostics()
        self.status.set(title+'；未调用模型。')
        self._log(title+'；阶段：'+phase+'；未调用模型或改写索引。')
        window=getattr(self,'settings_window',None)
        try:
            if window and window.winfo_exists():self._queue_fit(window)
        except tk.TclError:pass

    def _reset_connection(self):
        self.capture_enabled=False;self.current_target=None;self.current_identity=None
        self.capture_status.set('采集未开启');self.status.set('已关闭采集；Chrome 与标签页保持原样。');self.endpoint_entry.configure(state='normal');self._controls()

    def on_close(self):
        if self.editor_window:self.status.set('请先保存或取消当前控件修改，再退出。');self.editor_window.lift();return
        if getattr(self,'_bridge_install_active',False):self.status.set('连接桥正在安装，请完成后再退出。');return
        native_detached=self.sending and self._send_state().can_detach_native()
        if self.sending and not native_detached:self.status.set('发送尚未确认接纳，请等待回执后再退出。');return
        if self.connecting:self.status.set('正在连接，请结束后再关闭浮窗。');return
        if self._pending_save_count():self.status.set('正在保存标注，请完成后再退出。');return
        if self.closed or self.closing:return
        self._persist_settings()
        self.closing=True
        if self._send_timer_job is not None:
            self.root.after_cancel(self._send_timer_job);self._send_timer_job=None
        self.status.set('正在退出采集器；ZCode 原对话任务继续运行，Chrome 保留。' if native_detached else '正在停止采集并退出；Chrome 将保留。')
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
        index=self.page_combo.current()
        selected=self.current_target if self.client and self.client.connected else self.pages[index] if 0<=index<len(self.pages) else None
        target_id=selected.get('id') if selected else None
        self.launch_button.configure(state='disabled')
        self.status.set('正在准备专用 Chrome…')
        def launch():
            try:
                result = launch_or_reuse(endpoint, profile, chrome,target_id=target_id)
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
        learning_source=source.parent/'cdp-learn-and-verify'
        learning_durable=durable.parent/'cdp-learn-and-verify'
        if not (learning_source/'SKILL.md').is_file():
            raise RuntimeError('交付包缺少 CDP 学习验证技能，请使用完整新版。')
        with self._workspace_lock:
            if selected not in self._prepared_workspaces or not (durable / 'SKILL.md').is_file() or not (learning_durable/'SKILL.md').is_file():
                durable.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, durable, dirs_exist_ok=True)
                shutil.copytree(learning_source,learning_durable,dirs_exist_ok=True)
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
        folder = filedialog.askdirectory(title='选择固定学习资料目录', parent=self.settings_window or self.root)
        if folder:
            self.workspace_path.set(folder)
            self.reload_records()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--endpoint', help='Override the saved loopback CDP address for this launch.')
    parser.add_argument('--smoke-test', type=Path, help='Create the UI without connecting, write a startup report, then exit.')
    parser.add_argument('--package-check', type=Path, help='Verify bundled resources without opening a window.')
    parser.add_argument('--prepare-chrome', type=Path, help='Prepare optional Chrome for Testing and write a JSON result.')
    parser.add_argument('--backend-check',type=Path,help='Write a no-model CLI transport diagnostic without opening a window.')
    parser.add_argument('--desktop-check',type=Path,help='Read native desktop bridge health/models without opening a window or requesting permission.')
    parser.add_argument('--desktop-bridge',type=Path,help='Explicit desktop bridge discovery file; never a token value.')
    parser.add_argument('--zcode-cli',help='ZCode CLI command name or exe/cmd/bat/cjs/mjs/js path; blank auto-detects.')
    parser.add_argument('--cdp-task',type=Path,help='Run one scoped CDP task without opening a window.')
    parser.add_argument('--output',type=Path,help='Structured JSON result for --cdp-task (stdout is not required).')
    args = parser.parse_args()
    if sum(bool(mode) for mode in (args.backend_check,args.desktop_check,args.cdp_task,args.package_check,args.smoke_test,args.prepare_chrome))>1:
        parser.error('Choose only one diagnostic or task mode')
    if args.output and not args.cdp_task:parser.error('--output is only valid with --cdp-task')
    if args.prepare_chrome:
        if not args.prepare_chrome.parent.is_dir():parser.error('--prepare-chrome report directory must already exist')
        from chrome_setup import prepare_optional_chrome
        result = {'status': 'preparing', 'message': '正在从 Google 准备完整的桌面 Chrome。'}
        def save_chrome_result():
            atomic_write(args.prepare_chrome, json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        save_chrome_result()
        try:
            result = prepare_optional_chrome(optional_chrome_directory().parents[1])
            result['message'] = '专用 Chrome 已准备好。回到采集器点「启动 Chrome」。'
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else '请检查 Google 下载地址的网络访问和便携目录写权限。'
            result = {'status': 'failed', 'message': sanitize_text(message, 250), 'errorType': type(error).__name__}
        save_chrome_result()
        raise SystemExit(2 if result['status'] == 'failed' else 0)
    if args.desktop_check:
        if not args.desktop_check.parent.is_dir():parser.error('--desktop-check report directory must already exist')
        report={'pickerVersion':VERSION,'transport':'zcode-native-extension-host','ok':False,
                'modelCalled':False,'sessionCreated':False,'permissionRequested':False,'openedWindow':False,
                'permissionScope':'read-only-health-observation-no-grant-request'}
        try:
            saved=PickerSettings().load() if args.workspace is None or args.desktop_bridge is None else {}
            workspace=PickerSettings.choose_workspace(args.workspace,saved.get('workspaceDir'),default_workspace())
            discovery=args.desktop_bridge or saved.get('desktopBridgeFile') or None
            from desktop_bridge import DesktopBridge
            bridge=DesktopBridge(workspace,discovery_path=discovery)
            health=bridge.health()
            if not isinstance(health,dict):raise SettingsError('桌面 health 返回结构未知。')
            report.update(nativeSessions=health.get('nativeSessions'),authorized=health.get('authorized'))
            if health.get('nativeSessions') is not True or health.get('transport')!='zcode-extension-host':
                report['phase']='incompatible'
                raise SettingsError('桌面尚未提供已确认的原生会话接口。')
            models=bridge.list_models()
            if not isinstance(models,list):raise SettingsError('桌面模型目录结构未知。')
            report.update(ok=True,phase='ready',modelCount=len(models),**{key:health[key] for key in ('workspacePath','applicationVersion','version') if key in health})
            report['message']='原生会话接口与模型目录只读检测通过；授权状态以 authorized 字段为准，发送前仍需连接并授权。'
        except Exception as error:
            report.update(phase=report.get('phase') or 'connect',message=sanitize_text(error,220) if isinstance(error,SettingsError) or type(error).__name__=='DesktopBridgeError' else '桌面桥检测失败（'+type(error).__name__+'）。')
        try:args.desktop_check.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        except OSError:parser.error('Cannot write --desktop-check report')
        raise SystemExit(0 if report['ok'] else 2)
    if args.backend_check:
        if not args.backend_check.parent.is_dir():parser.error('--backend-check report directory must already exist')
        try:
            from zcode_diagnostics import probe_backend
            saved=PickerSettings().load() if args.workspace is None or args.zcode_cli is None else {}
            selected_cli=args.zcode_cli if args.zcode_cli is not None else saved.get('zcodeCli')
            selected_workspace=PickerSettings.choose_workspace(args.workspace,saved.get('workspaceDir'),default_workspace())
            report=probe_backend(commandPath=selected_cli or None,cwd=selected_workspace)
        except Exception as error:
            report={'ok':False,'phase':'probe','message':'后端检测未完成（'+type(error).__name__+'）。',
                    'modelCalled':False,'indexWritten':False,'sessionCreated':False}
        report={'pickerVersion':VERSION,**report}
        try:args.backend_check.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        except OSError:parser.error('Cannot write --backend-check report')
        raise SystemExit(0 if report.get('ok') is True else 2)
    if args.cdp_task:
        if not args.output:parser.error('--cdp-task requires --output')
        from cdp_task import run_task
        result=run_task(args.cdp_task,args.output)
        raise SystemExit(0 if result.get('status')=='completed' else 2)
    if args.package_check:
        import websocket
        report = {'version': VERSION, 'uiMode': 'floating', 'controlPresentation': 'rows', 'fontAwareLayout': True, 'minimumCollapsedWindowSize': [280, 210], 'originalToolbarLayout': True, 'persistentSendFeedback': True, 'contentSendGuard': True, 'chromeWindowActivation': True, 'controlManagement': ['view', 'edit', 'soft-delete', 'reorder', 'declare-sequence'], 'automaticModelCalls': False, 'handoffBackend': 'zcode-native-extension-host', 'requestedPermissionMode': 'yolo', 'permissionScope': 'native-session-request', 'nativePermissionVerified': False, 'zcodeRunnerImported': 'zcode_runner' in sys.modules, 'pickerScriptExists': SCRIPT.is_file(), 'bundledSkillExists': (skill_source() / 'SKILL.md').is_file(), 'iconExists': ICON.is_file(), 'tkVersion': tk.TkVersion, 'websocketVersion': websocket.__version__, 'openedWindow': False, 'connectedBrowser': False}
        report.update(defaultHandoffIntent='learn-and-verify',actionPolicy='infer-from-purpose',manualActionSelection=False,headlessCdpTask=True,
                      backendSelection='path-cli-first',backendDiagnostic=True,
                      backendSelectionScope='cli-diagnostic-only',
                      nativeBridgeBundled=all((RESOURCE_DIR/'zcode-extension'/name).is_file() for name in ('.claude-plugin/marketplace.json','cdp-picker-bridge/.zcode-plugin/plugin.json','cdp-picker-bridge/package.json','cdp-picker-bridge/extension.js')),
                      desktopBridgeApi=importlib.util.find_spec('desktop_bridge') is not None,
                      persistentSettings=True,modelCatalogSource='zcode-desktop-composer',sessionModelSelection=True,
                      cdpTaskScriptExists=(RESOURCE_DIR/'cdp_task.js').is_file(),
                      learningSkillExists=(RESOURCE_DIR/'skills'/'cdp-learn-and-verify'/'SKILL.md').is_file())
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
    if args.zcode_cli is not None:app.zcode_path.set(args.zcode_cli)
    if args.desktop_bridge is not None:app.desktop_bridge_path.set(str(args.desktop_bridge))
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
