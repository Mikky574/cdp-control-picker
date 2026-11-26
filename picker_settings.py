"""Per-user picker paths and session model IDs; never stores credentials."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import uuid

from workspace import atomic_write


class SettingsError(ValueError):
    pass


def default_settings_path():
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    return base / 'CDP-Control-Picker' / 'settings.json'


def documents_directory():
    if os.name == 'nt':
        class GUID(ctypes.Structure):
            _fields_ = [('data1', wintypes.DWORD), ('data2', wintypes.WORD),
                        ('data3', wintypes.WORD), ('data4', ctypes.c_ubyte * 8)]
        value = GUID.from_buffer_copy(uuid.UUID('FDD39AD0-238F-46AF-ADB4-6C85480369C7').bytes_le)
        try:
            shell = ctypes.WinDLL('shell32', use_last_error=True)
            shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD,
                                                  wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
            shell.SHGetKnownFolderPath.restype = ctypes.c_long
            ole = ctypes.WinDLL('ole32')
            ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
            ole.CoTaskMemFree.restype = None
            pointer = ctypes.c_void_p()
            if shell.SHGetKnownFolderPath(ctypes.byref(value), 0, None, ctypes.byref(pointer)) == 0:
                try:return Path(ctypes.wstring_at(pointer))
                finally:ole.CoTaskMemFree(pointer)
        except (OSError, ValueError):
            pass
    return Path.home() / 'Documents'


def recommended_workspace():
    return documents_directory() / 'CDP-Picker-Learning'


def validate_model(selection):
    if selection is None:return None
    if not isinstance(selection, dict) or set(selection) != {'providerId', 'modelId'}:
        raise SettingsError('模型设置仅接受 providerId 和 modelId，不保存密钥或配置正文。')
    result = {}
    for key in ('providerId', 'modelId'):
        value = selection[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 500 or any(ord(c) < 32 for c in value):
            raise SettingsError('模型编号无效。')
        result[key] = value.strip()
    return result


def clean_settings(value):
    allowed = {'schemaVersion', 'workspaceDir', 'zcodeCli', 'chromeExe', 'desktopBridgeFile', 'modelSelection'}
    if not isinstance(value, dict) or set(value) - allowed or type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1:
        raise SettingsError('本机配置格式无效；不会覆盖未知配置，请在设置中另行确认。')
    result = {'schemaVersion': 1}
    for key in ('workspaceDir', 'zcodeCli', 'chromeExe', 'desktopBridgeFile'):
        text = value.get(key, '')
        if not isinstance(text, str) or len(text) > 4096 or any(ord(c) < 32 for c in text):
            raise SettingsError('本机路径设置无效：' + key)
        result[key] = text.strip()
    if result['workspaceDir']:
        directory = Path(result['workspaceDir']).expanduser()
        if not directory.is_absolute() or directory.parent == directory:
            raise SettingsError('学习工作区必须是明确的绝对目录，不能使用磁盘根。')
    if result['desktopBridgeFile'] and not Path(result['desktopBridgeFile']).expanduser().is_absolute():
        raise SettingsError('桌面连接文件需要绝对路径，避免迁移后选错实例。')
    result['modelSelection'] = validate_model(value.get('modelSelection'))
    return result


class PickerSettings:
    def __init__(self, path=None):
        self.path = Path(path if path is not None else default_settings_path()).expanduser().resolve()

    def load(self):
        if not self.path.exists():return {'schemaVersion': 1, 'workspaceDir': '', 'zcodeCli': '', 'chromeExe': '', 'desktopBridgeFile': '', 'modelSelection': None}
        try:
            if self.path.stat().st_size > 32768:raise SettingsError('本机配置过大，拒绝读取。')
            return clean_settings(json.loads(self.path.read_text(encoding='utf-8-sig')))
        except (OSError, ValueError) as error:
            if isinstance(error, SettingsError):raise
            raise SettingsError('无法读取本机配置；原文件未改动。') from error

    def save(self, value):
        settings = clean_settings(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(settings, ensure_ascii=False, indent=2) + '\n')
        return settings

    @staticmethod
    def choose_workspace(explicit=None, saved=None, legacy=None):
        if explicit is not None and str(explicit).strip():return Path(explicit).expanduser().resolve()
        if saved:return Path(saved).expanduser().resolve()
        if legacy is not None and Path(legacy).is_dir():return Path(legacy).resolve()
        return recommended_workspace().resolve()
