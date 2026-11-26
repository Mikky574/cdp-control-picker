"""Package the tested Windows build and its matching committed source."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urljoin, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
TEXT = {'.py', '.js', '.cs', '.md', '.txt', '.json', '.svg'}


def git(*args):
    return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, check=True).stdout


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical_digest(name, data):
    return digest(data.replace(b'\r\n', b'\n') if Path(name).suffix in TEXT else data)


def verify_zip(path, entries):
    with ZipFile(path) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(entries):
            raise RuntimeError('ZIP content check failed: ' + path.name)
        for name, expected in entries.items():
            if archive.read(name) != expected:
                raise RuntimeError('ZIP entry mismatch: ' + name)


def archive(path, entries):
    with ZipFile(path, 'w', ZIP_DEFLATED) as output:
        for name, data in sorted(entries.items()):
            output.writestr(name, data)
    verify_zip(path, entries)


def delivery_guide(data, version, local_skills=False):
    """Keep standalone guides cross-linked; resolve source docs against their tag."""
    names = {'cdp-learning.html': 'CDP-Learning-Guide.html',
             'deployment.html': 'Deployment-Guide.html'}
    if local_skills:
        names.update({'known-issues.md': 'Known-Issues.md', 'cdp-picker-validation.md': 'Validation.md'})
    base = f'https://github.com/Mikky574/cdp-control-picker/blob/v{version}/docs/'
    def link(match):
        target = match.group(2)
        parts = urlsplit(target)
        if parts.scheme or parts.netloc or target.startswith('#'):
            return match.group(0)
        if parts.path in names:
            resolved = names[parts.path] + ('?' + parts.query if parts.query else '') + ('#' + parts.fragment if parts.fragment else '')
        elif local_skills and target.startswith('../skills/'):
            resolved = target.removeprefix('../')
        else:
            resolved = urljoin(base, target)
        return match.group(1) + resolved + match.group(3)
    return re.sub(r'''(href\s*=\s*["'])([^"']+)(["'])''', link, data.decode('utf-8')).encode('utf-8')


def chrome_setup_command(executable):
    lines = [
        '@echo off',
        'setlocal',
        'chcp 65001 >nul',
        'echo Preparing optional Windows x64 Chrome for Testing from Google.',
        'echo A complete browser download may take a few minutes.',
        f'start "" /wait "%~dp0{executable}" --prepare-chrome "%~dp0Chrome-Setup-Result.json"',
        'set "PICKER_CHROME_EXIT=%ERRORLEVEL%"',
        'if exist "%~dp0Chrome-Setup-Result.json" type "%~dp0Chrome-Setup-Result.json"',
        'if not "%PICKER_CHROME_EXIT%"=="0" echo Chrome preparation did not finish. Check the result above.',
        'pause',
        'exit /b %PICKER_CHROME_EXIT%',
    ]
    return ('\r\n'.join(lines) + '\r\n').encode('ascii')


def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def deployment_archive(path, entries, chromium_zip, revision, chromium_license):
    if not re.fullmatch(r'[0-9]+', revision):
        raise ValueError('Chromium revision must be the upstream snapshot number.')
    prefix = 'optional-dependencies/chrome-win64/'
    required = {'chrome.exe', 'chrome.dll', 'icudtl.dat', 'resources.pak'}
    notice = ('Chromium Win_x64 snapshot ' + revision + '\n\n'
              'https://storage.googleapis.com/chromium-browser-snapshots/Win_x64/'
              + revision + '/chrome-win.zip\n'
              'Original archive SHA-256: ' + file_digest(chromium_zip) + '\n'
              'Upstream and source: https://www.chromium.org/getting-involved/download-chromium/\n'
              'Source revision: https://crrev.com/' + revision + '\n'
              'The upstream developer UI test executable is omitted.\n'
              'Third-party notices are available in the included browser at chrome://credits.\n')
    license_bytes = chromium_license.read_bytes()
    if b'The Chromium Authors' not in license_bytes or b'Redistribution and use' not in license_bytes:
        raise ValueError('Chromium license is missing or malformed.')
    entries = {**entries, 'optional-dependencies/CHROMIUM-NOTICE.txt': notice.encode('utf-8'),
               'optional-dependencies/CHROMIUM-LICENSE.txt': license_bytes}
    checksums = {name: digest(data) for name, data in entries.items()}
    with ZipFile(chromium_zip) as upstream, ZipFile(path, 'w', ZIP_DEFLATED, compresslevel=3) as output:
        files = [item for item in upstream.infolist()
                 if not item.is_dir() and item.filename.rsplit('/', 1)[-1] != 'interactive_ui_tests.exe']
        roots = {item.filename.split('/', 1)[0] for item in files}
        if roots not in ({'chrome-win'}, {'chrome-win64'}):
            raise ValueError('Unexpected Chromium archive directory layout.')
        names = [item.filename.split('/', 1)[-1] for item in files]
        if not required.issubset(names) or len({name.casefold() for name in names}) != len(names):
            raise ValueError('Chromium archive is incomplete or has duplicate paths.')
        for name, data in sorted(entries.items()):
            output.writestr(name, data)
        for item, name in zip(files, names):
            if any(part in {'', '.', '..'} for part in name.split('/')) or any(ch in name for ch in '\\:'):
                raise ValueError('Unsafe path in Chromium archive: ' + name)
            name = prefix + name
            checksum = hashlib.sha256()
            with upstream.open(item) as source, output.open(name, 'w') as destination:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    checksum.update(block)
                    destination.write(block)
            checksums[name] = checksum.hexdigest()
        output.writestr('SHA256SUMS.txt', ''.join(f'{value}  {name}\n' for name, value in sorted(checksums.items())))
    with ZipFile(path) as deployment:
        if set(deployment.namelist()) != checksums.keys() | {'SHA256SUMS.txt'}:
            raise RuntimeError('Deployment ZIP entries do not match the manifest.')
        for name, expected in checksums.items():
            with deployment.open(name) as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise RuntimeError('Deployment ZIP entry failed verification: ' + name)


def package(version, executable, build_info, output, release_version=None, *, chromium=None):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('Use a semantic version such as 0.2.0.')
    release_version = release_version or version
    if not re.fullmatch(re.escape(version) + r'(?:-[A-Za-z0-9.-]+)?', release_version):
        raise ValueError('Release version must match the app version, optionally with a prerelease suffix.')
    if git('status', '--porcelain').strip():
        raise RuntimeError('Commit the intended release source before packaging.')
    commit = git('rev-parse', 'HEAD').decode().strip()
    names = git('ls-tree', '-r', '--name-only', 'HEAD').decode('utf-8').splitlines()
    sources = {}
    forbidden = {'.git', '.local', '.venv', '__pycache__', 'captures', 'page-bundles',
                 '.handoffs', '.capture-kit', '.desktop-bridge', 'CDP-Chrome-Profile', 'CDP-Picker-Workspace', '_build', 'dist'}
    for name in names:
        path = Path(name)
        if forbidden.intersection(path.parts) or path.suffix.lower() in {'.exe', '.msi', '.pyc', '.log', '.zip', '.key', '.pem', '.har', '.sqlite', '.sqlite3', '.db'}:
            raise RuntimeError('Unexpected runtime/build file tracked in release source: ' + name)
        sources[name] = git('show', 'HEAD:' + name)
    app = ast.parse(sources['app.py'].decode('utf-8-sig'))
    app_version = next((node.value.value for node in app.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets)
                        and isinstance(node.value, ast.Constant)), None)
    if app_version != version:
        raise RuntimeError('Requested version differs from app.py VERSION.')
    build = json.loads(Path(build_info).read_text(encoding='utf-8'))
    executable = Path(executable).resolve()
    binary = executable.read_bytes()
    if binary[:2] != b'MZ' or digest(binary) != build['executableSha256']:
        raise RuntimeError('Executable does not match the recorded tested build.')
    if not build.get('sourceFiles'):
        raise RuntimeError('Missing build source hashes.')
    for name, expected in build['sourceFiles'].items():
        if name not in sources or canonical_digest(name, sources[name]) != expected:
            raise RuntimeError('Build source differs from this commit: ' + name)
    usage_name = f'docs/releases/v{release_version}-usage.txt'
    if usage_name not in sources:
        usage_name = f'docs/releases/v{version}-usage.txt'
    if usage_name not in sources:
        raise RuntimeError('Missing release usage file: ' + usage_name)
    usage = sources[usage_name]
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    stem = 'CDP-Control-Picker-v' + release_version
    target = output / (stem + '.exe')
    if target != executable:
        shutil.copyfile(executable, target)
    metadata = {**build, 'version': release_version, 'appVersion': version,
                'sourceTag': 'v' + release_version, 'prerelease': release_version != version, 'sourceCommit': commit,
                'executable': target.name, 'delivery': 'Windows floating picker with native ZCode Extension Host connector and deployment skills'}
    metadata_bytes = (json.dumps(metadata, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    (output / 'BUILD-INFO.json').write_bytes(metadata_bytes)
    (output / 'USAGE-zh-CN.txt').write_bytes(usage)
    inner = {target.name: binary, 'USAGE-zh-CN.txt': usage, 'BUILD-INFO.json': metadata_bytes}
    inner['Prepare-Chrome.cmd'] = chrome_setup_command(target.name)
    bridge_files = {'ZCode-Bridge/' + name.removeprefix('zcode-extension/'): data
                    for name, data in sources.items() if name.startswith('zcode-extension/')}
    inner.update(bridge_files)
    guides = {}
    for source_name, delivery_name in (('docs/cdp-learning.html', 'CDP-Learning-Guide.html'),
                                       ('docs/deployment.html', 'Deployment-Guide.html')):
        guide = sources.get(source_name)
        if guide:
            guide = delivery_guide(guide, release_version, local_skills=bool(chromium))
            guides[delivery_name] = guide
            inner[delivery_name] = guide
            (output / delivery_name).write_bytes(guide)
    if chromium:
        inner.update({name: data for name, data in sources.items() if name.startswith('skills/')})
        inner['Known-Issues.md'] = sources['docs/known-issues.md']
        inner['Validation.md'] = sources['docs/cdp-picker-validation.md']
        portable = output / (stem + '-windows-x64.zip')
        deployment_archive(portable, inner, *chromium)
        return {'version': release_version, 'appVersion': version, 'commit': commit,
                'assets': [{'path': str(portable), 'name': portable.name, 'size': portable.stat().st_size,
                            'sha256': file_digest(portable)}]}
    inner['SHA256SUMS.txt'] = ''.join(f'{digest(data)}  {name}\n' for name, data in sorted(inner.items())).encode('utf-8')
    portable = output / (stem + '.zip')
    archive(portable, inner)
    source_zip = output / (stem + '-source.zip')
    archive(source_zip, {'cdp-control-picker/' + name: data for name, data in sources.items()})
    skills_zip = output / (stem + '-skills.zip')
    archive(skills_zip, {name.removeprefix('skills/'): data for name, data in sources.items() if name.startswith('skills/')})
    assets = [target, portable, source_zip, skills_zip, output / 'USAGE-zh-CN.txt', output / 'BUILD-INFO.json']
    assets.extend(output / name for name in guides)
    if bridge_files:
        bridge_zip = output / (stem + '-zcode-bridge.zip')
        archive(bridge_zip, {**bridge_files, **guides})
        assets.append(bridge_zip)
    checksums = output / 'SHA256SUMS.txt'
    checksums.write_text(''.join(f'{digest(path.read_bytes())}  {path.name}\n' for path in assets), encoding='utf-8', newline='\n')
    assets.append(checksums)
    return {'version': release_version, 'appVersion': version, 'commit': commit,
            'assets': [{'path': str(path), 'name': path.name, 'size': path.stat().st_size,
                        'sha256': digest(path.read_bytes())} for path in assets]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--release-version', help='Optional prerelease version; app and build input versions remain checked.')
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--build-info', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--chromium-zip', type=Path, help='Create a single offline-ready ZIP from this official Win_x64 snapshot.')
    parser.add_argument('--chromium-revision')
    parser.add_argument('--chromium-license', type=Path)
    args = parser.parse_args()
    chromium = (args.chromium_zip, args.chromium_revision, args.chromium_license)
    if any(chromium) and not all(chromium):
        parser.error('Provide --chromium-zip, --chromium-revision and --chromium-license together.')
    report = package(args.version, args.exe, args.build_info, args.output, args.release_version,
                     chromium=chromium if all(chromium) else None)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
