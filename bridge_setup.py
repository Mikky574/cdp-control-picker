"""Stage and install only the bundled native connector through ZCode's plugin CLI."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from workspace import atomic_write
from zcode_runner import resolve_command, command_environment

PLUGIN = 'cdp-picker-bridge@cdp-picker-local'
FILES = ('.claude-plugin/marketplace.json', 'cdp-picker-bridge/.zcode-plugin/plugin.json',
         'cdp-picker-bridge/package.json', 'cdp-picker-bridge/extension.js')


def stage_connector(*, source=None, target=None):
    resources = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    source = Path(source or resources / 'zcode-extension').resolve()
    local = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local')
    target = Path(target or local / 'CDP-Control-Picker/desktop-connector').expanduser().resolve()
    if target.parent == target or target.is_relative_to(source):
        raise ValueError('连接桥安装目录必须独立于程序资源，且不能是磁盘根目录。')
    marker = target / '.picker-managed.json'
    previous = {}
    if marker.exists():
        previous = json.loads(marker.read_text(encoding='utf-8'))
        if previous.get('owner') != 'cdp-control-picker-native-connector':
            raise ValueError('连接桥目标目录归属未知，未覆盖。')
    contents = {}
    for name in FILES:
        selected = (source / name).resolve()
        if not selected.is_relative_to(source) or not selected.is_file():
            raise ValueError('成品缺少完整的 ZCode 原生连接桥资源。')
        text = selected.read_text(encoding='utf-8')
        destination = target / name
        if destination.is_symlink() or not destination.resolve().is_relative_to(target):
            raise ValueError('连接桥目标文件指向安装目录之外，未覆盖。')
        if destination.exists():
            actual = hashlib.sha256(destination.read_bytes()).hexdigest()
            expected = previous.get('files', {}).get(name)
            same = hashlib.sha256(text.encode('utf-8')).hexdigest()
            if actual not in {expected, same}:
                raise ValueError('连接桥包含本地改动，请保留原目录并另选准备位置。')
        contents[name] = text
    target.mkdir(parents=True, exist_ok=True)
    for name, text in contents.items():
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(destination, text)
    atomic_write(marker, json.dumps({'owner': 'cdp-control-picker-native-connector',
        'files': {name: hashlib.sha256(text.encode('utf-8')).hexdigest() for name, text in contents.items()}}, indent=2))
    return target


def install_connector(commandPath=None, cwd=None, *, source=None, target=None, timeout_seconds=120, on_event=None):
    """Explicit first connection action. Never grants permissions or restarts the App."""
    workspace = Path(cwd).expanduser().resolve() if cwd else None
    if workspace is None or not workspace.is_dir() or workspace.parent == workspace:
        raise ValueError('请先选定已存在的学习资料目录。')
    command = resolve_command(commandPath)
    staged = stage_connector(source=source, target=target)
    steps = [
        ('validate', ['plugins', 'validate', str(staged / 'cdp-picker-bridge'), '--json']),
        ('marketplace', ['plugins', 'marketplace', 'add', str(staged)]),
        ('install', ['plugins', 'install', PLUGIN, '--scope', 'user']),
        ('enable', ['plugins', 'enable', PLUGIN]),
    ]
    completed = []
    for phase, arguments in steps:
        if on_event:on_event({'phase': phase, 'message': {'validate': '正在检查连接桥资源。',
            'marketplace': '正在登记随包本地连接市场。', 'install': '正在安装 ZCode 原生连接桥。',
            'enable': '正在启用连接桥。'}[phase]})
        result = subprocess.run(command + arguments, cwd=str(workspace),
            env=command_environment(command), stdin=subprocess.DEVNULL, capture_output=True,
            encoding='utf-8', errors='replace', timeout=timeout_seconds, shell=False,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            # Do not guess that nonzero means "already exists" and continue.
            raise ValueError('ZCode 插件管理未完成：' + phase + '（退出码 ' + str(result.returncode)
                             + '）。已完成步骤保留，不会重启 App 或发送模型任务。')
        if phase == 'validate':
            try:
                validation = json.loads(result.stdout.lstrip('\ufeff'))
            except (ValueError, TypeError):
                validation = None
            if not isinstance(validation, dict) or validation.get('ok') is not True:
                raise ValueError('ZCode 连接桥校验未返回有效的成功 JSON；退出码 0 不能证明 CLI 已执行。'
                                 '请核对所选 CLI 的实际入口，未继续安装或启动模型。')
        completed.append(phase)
    return {'installed': True, 'pluginId': PLUGIN, 'marketplacePath': str(staged),
            'completedSteps': completed, 'modelCalled': False, 'appRestarted': False,
            'permissionsGranted': False, 'desktopConnected': False,
            'activationNeeded': True,
            'message': '连接桥已安装。首次请在 ZCode 执行“重新加载扩展”（无需退出 App），'
                       '再回这里检测桌面连接并确认 ZCode 的会话授权。以后不需重复安装或重启。'}
