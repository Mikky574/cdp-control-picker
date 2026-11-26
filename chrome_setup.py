"""Prepare the official Windows x64 Chrome for Testing in the portable folder."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.request import Request, build_opener
from zipfile import ZipFile

from browser_launcher import _NoRedirect, optional_chrome_directory, optional_chrome_ready


CATALOG = 'https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json'
STORAGE = 'https://storage.googleapis.com/chrome-for-testing-public'
MAX_ARCHIVE_BYTES = 500 * 1024 * 1024
MAX_EXPANDED_BYTES = 1500 * 1024 * 1024


def stable_download(catalog):
    stable = catalog['channels']['Stable']
    version = stable['version']
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', version):
        raise ValueError('Google 返回的 Chrome 版本格式无效。')
    expected = f'{STORAGE}/{version}/win64/chrome-win64.zip'
    if [item['url'] for item in stable['downloads']['chrome'] if item['platform'] == 'win64'] != [expected]:
        raise ValueError('Google 返回的 Chrome 下载地址不符合预期。')
    return version, expected


def _download(opener, url, target):
    with opener.open(Request(url, headers={'Accept-Encoding': 'identity'}), timeout=45) as response:
        length = int(response.headers.get('Content-Length', 0))
        digest = re.search(r'(?:^|,\s*)md5=([A-Za-z0-9+/=]+)', ','.join(response.headers.get_all('x-goog-hash', [])))
        if not 0 < length <= MAX_ARCHIVE_BYTES or not digest:
            raise ValueError('Google 返回的文件大小或完整性信息无效。')
        md5 = hashlib.md5(usedforsecurity=False)
        sha256 = hashlib.sha256()
        downloaded = 0
        deadline = time.monotonic() + 600
        with target.open('xb') as output:
            while chunk := response.read(1024 * 1024):
                if time.monotonic() > deadline:
                    raise ValueError('Chrome 下载已超过 10 分钟，请换用网络更快的环境或离线包。')
                downloaded += len(chunk)
                if downloaded > length:
                    raise ValueError('下载文件大于 Google 声明的大小。')
                md5.update(chunk)
                sha256.update(chunk)
                output.write(chunk)
    if downloaded != length or base64.b64encode(md5.digest()).decode('ascii') != digest[1]:
        raise ValueError('Chrome ZIP 下载不完整，请重新准备。')
    return sha256.hexdigest()


def _extract(archive, staging, directory_name):
    with ZipFile(archive) as package:
        members = package.infolist()
        if len(members) > 1600 or sum(member.file_size for member in members) > MAX_EXPANDED_BYTES:
            raise ValueError('Chrome ZIP 解压后的内容超出预期。')
        seen = set()
        for member in members:
            name = member.filename.rstrip('/')
            parts = name.split('/')
            if (parts[0] != directory_name or len(parts) < (1 if member.is_dir() else 2)
                    or any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in parts)
                    or ':' in name or '\\' in name or stat.S_ISLNK(member.external_attr >> 16)
                    or name.casefold() in seen):
                raise ValueError('Chrome ZIP 包含无效路径。')
            seen.add(name.casefold())
        package.extractall(staging)
    candidate = staging / directory_name
    if not optional_chrome_ready(candidate):
        raise ValueError('Chrome ZIP 缺少完整的桌面浏览器文件。')
    return candidate


def prepare_optional_chrome(install_dir):
    architecture = os.environ.get('PROCESSOR_ARCHITEW6432', os.environ.get('PROCESSOR_ARCHITECTURE', ''))
    if os.name != 'nt' or architecture.upper() != 'AMD64':
        raise ValueError('该准备程序提供 Windows x64 版 Chrome。其他架构请在采集器选择已安装的 Chrome。')
    root = Path(install_dir).resolve()
    destination = optional_chrome_directory(root)
    parent = destination.parent
    if parent.resolve() != parent or destination.resolve() != destination:
        raise ValueError('可选依赖目录存在路径跳转，请在便携包的实际目录中准备 Chrome。')
    if optional_chrome_ready(destination):
        return {'status': 'already-ready', 'chromePath': str(destination / 'chrome.exe')}
    if destination.exists():
        raise ValueError('发现不完整的 Chrome 目录，请先移走旧目录再准备。原文件保留。')
    parent.mkdir(parents=True, exist_ok=True)
    opener = build_opener(_NoRedirect())
    with opener.open(CATALOG, timeout=20) as response:
        data = response.read(512 * 1024 + 1)
    if len(data) > 512 * 1024:
        raise ValueError('Google Chrome 版本列表过大。')
    version, url = stable_download(json.loads(data))
    with tempfile.TemporaryDirectory(prefix='.picker-chrome-', dir=parent) as temporary:
        staging = Path(temporary).resolve()
        if staging.parent != parent or not staging.name.startswith('.picker-chrome-'):
            raise ValueError('Chrome 暂存目录不在可选依赖目录内。')
        archive = staging / 'download.zip'
        sha256 = _download(opener, url, archive)
        candidate = _extract(archive, staging, destination.name)
        if candidate.resolve().parent != staging or destination.exists() or destination.is_symlink():
            raise ValueError('Chrome 安装目录已变化，原有文件保留。')
        candidate.rename(destination)
    return {'status': 'ready', 'chromePath': str(destination / 'chrome.exe'),
            'version': version, 'zipSha256': sha256, 'source': url}
