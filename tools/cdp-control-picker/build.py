"""Build the Windows desktop executable in a dedicated Python environment."""
from pathlib import Path
import argparse
import subprocess
import sys

tool = Path(__file__).resolve().parent
repository = tool.parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=repository.parent)
parser.add_argument('--workdir', type=Path, default=tool / '_build')
parser.add_argument('--name', default='CDP-Control-Picker')
args = parser.parse_args()
output = args.output.resolve()
work = args.workdir.resolve()
subprocess.run([
    sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed',
    '--name', args.name, '--distpath', str(output),
    '--workpath', str(work / 'build'), '--specpath', str(work / 'spec'),
    '--add-data', str(tool / 'page_picker.js') + ';.',
    '--add-data', str(repository / 'skills' / 'cdp-page-contract') + ';skills/cdp-page-contract',
    str(tool / 'app.py'),
], check=True)
