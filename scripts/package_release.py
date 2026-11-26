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


def delivery_guide(data, version):
    """Keep standalone guides cross-linked; resolve source docs against their tag."""
    names = {'cdp-learning.html': 'CDP-Learning-Guide.html',
             'deployment.html': 'Deployment-Guide.html'}
    base = f'https://github.com/Mikky574/cdp-control-picker/blob/v{version}/docs/'
    def link(match):
        target = match.group(2)
        parts = urlsplit(target)
        if parts.scheme or parts.netloc or target.startswith('#'):
            return match.group(0)
        if parts.path in names:
            resolved = names[parts.path] + ('?' + parts.query if parts.query else '') + ('#' + parts.fragment if parts.fragment else '')
        else:
            resolved = urljoin(base, target)
        return match.group(1) + resolved + match.group(3)
    return re.sub(r'''(href\s*=\s*["'])([^"']+)(["'])''', link, data.decode('utf-8')).encode('utf-8')


def package(version, executable, build_info, output, release_version=None):
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
        if forbidden.intersection(path.parts) or path.suffix.lower() in {'.exe', '.pyc', '.log', '.zip', '.key', '.pem', '.har', '.sqlite', '.sqlite3', '.db'}:
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
    bridge_files = {'ZCode-Bridge/' + name.removeprefix('zcode-extension/'): data
                    for name, data in sources.items() if name.startswith('zcode-extension/')}
    inner.update(bridge_files)
    guides = {}
    for source_name, delivery_name in (('docs/cdp-learning.html', 'CDP-Learning-Guide.html'),
                                       ('docs/deployment.html', 'Deployment-Guide.html')):
        guide = sources.get(source_name)
        if guide:
            guide = delivery_guide(guide, release_version)
            guides[delivery_name] = guide
            inner[delivery_name] = guide
            (output / delivery_name).write_bytes(guide)
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
    args = parser.parse_args()
    report = package(args.version, args.exe, args.build_info, args.output, args.release_version)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
