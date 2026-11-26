"""Build the Windows desktop executable in a dedicated Python environment."""
from pathlib import Path
import argparse
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
subprocess.run([
    sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed',
    '--exclude-module', 'zcode_runner',
    '--name', args.name, '--distpath', str(output),
    '--workpath', str(work / 'build'), '--specpath', str(work / 'spec'),
    '--icon', str(icon),
    '--add-data', str(tool / 'assets') + ';assets',
    '--add-data', str(tool / 'page_picker.js') + ';.',
    '--add-data', str(repository / 'skills') + ';skills',
    str(tool / 'app.py'),
], check=True)
