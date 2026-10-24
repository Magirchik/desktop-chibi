#!/usr/bin/env python3
"""Install the user-local GNOME extension, preserving any previous version."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import zipfile


def install():
    root = Path(__file__).absolute().parent.parent
    source = root / 'integrations/gnome/desktop-chibi@local'
    data = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    if not data.is_absolute():
        raise SystemExit('XDG_DATA_HOME must be an absolute path')
    uuid = json.loads((source / 'metadata.json').read_text())['uuid']
    destination = data / 'gnome-shell/extensions' / uuid
    if destination.exists():
        backup = data / 'desktop-chibi/extension-backups' / str(time.time_ns()) / uuid
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(destination, backup)
        print('Backup:', backup)
    destination.mkdir(parents=True, exist_ok=True)
    for name in ('metadata.json', 'extension.js'):
        shutil.copyfile(source / name, destination / name)
    # Keep a reviewable package as well as the installed sources.
    archive = root / '.tools/desktop-chibi-controls.shell-extension.zip'
    archive.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
        for name in ('metadata.json', 'extension.js'):
            package.write(source / name, name)
    print('Installed:', destination)
    result = subprocess.run(['gnome-extensions', 'enable', uuid], capture_output=True, text=True)
    if result.returncode:
        print(result.stderr.strip())
        print('GNOME may require logout/login to discover a new extension; then run:')
        print(f'gnome-extensions enable {uuid}')
    else:
        print('Extension enabled. Restart Desktop Chibi to load the new controls.')


if __name__ == '__main__':
    install()
