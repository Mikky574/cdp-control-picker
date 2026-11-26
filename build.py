"""Build the Windows desktop executable in a dedicated Python environment."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys

tool = Path(__file__).resolve().parent
repository = tool
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=repository / 'dist')
parser.add_argument('--workdir', type=Path, default=tool / '_build')
parser.add_argument('--name', default='CDP-Control-Picker')
args = parser.parse_args()
output = args.output.resolve()
work = args.workdir.resolve()
icon = tool / 'assets' / 'app-icon.ico'
if not icon.is_file():
    parser.error('Missing application icon: ' + str(icon))
inputs = [path for path in tool.glob('*.py')] + [tool / 'page_picker.js', tool / 'cdp_task.js']
inputs += [path for directory in ('assets', 'skills') for path in (tool / directory).rglob('*') if path.is_file()]
def source_digest(path):
    data = path.read_bytes()
    if path.suffix.lower() in {'.py', '.js', '.cs', '.md', '.txt', '.json', '.svg'}:
        data = data.replace(b'\r\n', b'\n')
    return hashlib.sha256(data).hexdigest()
source_files = {path.relative_to(tool).as_posix(): source_digest(path) for path in sorted(inputs)}
subprocess.run([
    sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed',
    '--name', args.name, '--distpath', str(output),
    '--workpath', str(work / 'build'), '--specpath', str(work / 'spec'),
    '--icon', str(icon),
    '--add-data', str(tool / 'assets') + ';assets',
    '--add-data', str(tool / 'page_picker.js') + ';.',
    '--add-data', str(tool / 'cdp_task.js') + ';.',
    '--add-data', str(repository / 'skills') + ';skills',
    str(tool / 'app.py'),
], check=True)
if source_files != {path.relative_to(tool).as_posix(): source_digest(path) for path in sorted(inputs)}:
    raise RuntimeError('Source changed during build; rebuild before testing or publishing.')
executable = output / (args.name + '.exe')
manifest = {'schemaVersion': 1, 'executable': executable.name,
            'executableSha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
            'handoffBackend': 'app-server-with-desktop-index',
            'sourceFiles': source_files}
(work / 'build-info.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('Build source hashes written to ' + str(work / 'build-info.json'))
