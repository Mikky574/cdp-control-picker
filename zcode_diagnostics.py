"""Read-only CLI transport check; never creates a session or submits a prompt."""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import uuid

from zcode_runner import RunnerError, command_environment, resolve_command


def probe_backend(commandPath=None, cwd=None, *, timeout_seconds=12, server_factory=None):
    report = {'ok': False, 'phase': 'resolve', 'transport': 'app-server-stdio',
              'modelCalled': False, 'indexWritten': False, 'sessionCreated': False}
    try:
        command = resolve_command(commandPath)
        report['command'] = command
        executable = Path(command[0])
        bundled = (len(command) > 1 and executable.name.lower() == 'zcode.exe'
                   and command_environment(command, {}).get('ELECTRON_RUN_AS_NODE') == '1')
        report['runtimeType'] = ('desktop-bundled-cli' if bundled else
                                 'node-cli' if executable.name.lower() in {'node', 'node.exe'} else 'native-cli')
        workspace = Path(cwd).expanduser().resolve() if cwd else None
        if workspace is None or not workspace.is_dir() or workspace.parent == workspace:
            raise RunnerError('检测需要已存在的记录目录；不会自动建立或信任工作区。')
        report['phase'] = 'version'
        version = subprocess.run(command + ['--version'], cwd=str(workspace),
                                 env=command_environment(command), stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, encoding='utf-8', errors='replace',
                                 timeout=timeout_seconds, shell=False,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        output = version.stdout.strip().lstrip('\ufeff')
        recognized = not version.returncode and len(output) <= 250 and re.fullmatch(
            r'(?:[Zz][Cc]ode(?: CLI)?[ :]+)?v?\d+\.\d+\.\d+(?:[-+][\w.-]+)?', output)
        report['cliVersion'] = output if recognized else 'unknown'
        if not recognized:
            report['versionNote'] = '未识别版本输出；以实际 app-server 协议响应判断连通，不保存原始输出。'
        report['phase'] = 'app-server'
        if server_factory is None:
            from zcode_appserver import ZCodeAppServer
            server_factory = ZCodeAppServer
        with server_factory(cwd=workspace, commandPath=command,
                            timeout_seconds=timeout_seconds) as server:
            listing = server.request('session/list', {
                'sessionIds': ['picker-backend-probe-' + uuid.uuid4().hex]}, timeout=timeout_seconds)
            if not isinstance(listing, dict) or not isinstance(listing.get('sessions'), list):
                raise RunnerError('app-server 已响应，但 session/list 结构不兼容。')
        report.update(ok=True, phase='ready', appServerConnected=True,
                      message='后台 CLI 与 app-server stdio 检测通过。未调用模型，未创建会话或改写桌面索引。')
    except Exception as error:
        if isinstance(error, RunnerError):
            message = str(error)
        elif isinstance(error, subprocess.TimeoutExpired):
            message = '后端检测超时；未提交模型任务。'
        else:
            # Transport errors can contain provider details; keep diagnostics bounded.
            message = '后端检测失败（' + type(error).__name__ + '）；请核对所选 CLI 是否支持 app-server --stdio。'
        report['message'] = message
    return report
