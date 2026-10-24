#!/usr/bin/env python3
"""Install the Linux application launcher for this checkout and virtualenv."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def desktop_quote(value):
    value = str(value)
    if '\n' in value or '\r' in value:
        raise ValueError('Launcher paths must not contain newlines')
    # Desktop Entry escaping is applied before Exec argument escaping.
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, '\\' + char)
    return '"' + value.replace('\\', '\\\\').replace('%', '%%') + '"'


def install():
    if not sys.platform.startswith('linux'):
        raise SystemExit('This launcher installer supports Linux only.')
    root = Path(__file__).absolute().parent.parent
    python = root / '.venv' / 'bin' / 'python'
    if not python.is_file():
        raise SystemExit('Create .venv and install requirements.txt first.')
    data = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    if not data.is_absolute():
        raise SystemExit('XDG_DATA_HOME must be an absolute path.')
    applications = data / 'applications'
    icons = data / 'icons/hicolor/256x256/apps'
    applications.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / 'desktop_chibi/assets/desktop-chibi.png', icons / 'desktop-chibi.png')
    launcher = applications / 'desktop-chibi.desktop'
    # -c keeps launch independent of the working directory and supports spaces.
    code = f'import sys; sys.path.insert(0, {str(root)!r}); import desktop_chibi.__main__'
    launcher.write_text(
        '[Desktop Entry]\nType=Application\nVersion=1.0\n'
        'Name=Desktop Chibi\nComment=Animated desktop companions\n'
        'Comment[ru]=Анимированные персонажи на рабочем столе\n'
        f'Exec={desktop_quote(python)} -c {desktop_quote(code)}\n'
        'Icon=desktop-chibi\nTerminal=false\nStartupNotify=false\n'
        'StartupWMClass=desktop-chibi\nCategories=Utility;\n'
        'Keywords=chibi;bunny;pet;заяц;чиби;\n', encoding='utf-8')
    for command in (['update-desktop-database', str(applications)],
                    ['gtk-update-icon-cache', '-f', '-t', str(data / 'icons/hicolor')]):
        if shutil.which(command[0]):
            subprocess.run(command, check=False, stdout=subprocess.DEVNULL)
    print(f'Installed: {launcher}')


if __name__ == '__main__':
    install()
