#!/usr/bin/env python3
"""Build a standalone native bundle, then a portable archive and optional .deb."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop_chibi import __version__


def version_string(value):
    value = value.removeprefix('v')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?', value):
        raise ValueError('Version must look like 0.1.0 or 0.1.0-beta.1')
    return value


def desktop_entry():
    return ('[Desktop Entry]\nType=Application\nVersion=1.0\nName=Desktop Chibi\n'
            'Comment=Animated desktop companions\nComment[ru]=Персонажи на рабочем столе\n'
            'Exec=/opt/desktop-chibi/desktop-chibi\nIcon=desktop-chibi\nTerminal=false\n'
            'StartupNotify=false\nStartupWMClass=desktop-chibi\nCategories=Utility;\n'
            'Keywords=chibi;bunny;pet;чиби;заяц;\n')


def windows_icon(target):
    from PySide6.QtGui import QImage
    image = QImage(str(ROOT / 'desktop_chibi/assets/desktop-chibi.png'))
    if image.isNull():
        raise RuntimeError('Missing application icon')
    # ICO embeds PNG payloads and needs no extra image-writing dependency.
    payload = (ROOT / 'desktop_chibi/assets/desktop-chibi.png').read_bytes()
    import struct
    width, height = image.width(), image.height()
    target.write_bytes(struct.pack('<HHH', 0, 1, 1) + struct.pack('<BBBBHHII', width if width < 256 else 0,
                       height if height < 256 else 0, 0, 0, 1, 32, len(payload), 22) + payload)


def copy_notices(bundle):
    notices = bundle / 'licenses'
    notices.mkdir(exist_ok=True)
    shutil.copytree(ROOT / 'packaging/licenses', notices, dirs_exist_ok=True)
    for package in ('PySide6-Essentials', 'shiboken6'):
        distribution = importlib.metadata.distribution(package)
        for file in distribution.files or []:
            if any(part.casefold() in ('licenses', 'license') for part in file.parts) and not str(file).endswith('.py'):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    dest = notices / package / Path(*file.parts[-2:])
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)


def smoke(bundle, executable, output):
    report = output / 'smoke-test.json'
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    # Run from a different directory: bundled assets must not depend on cwd.
    with tempfile.TemporaryDirectory(prefix='chibi-bundle-cwd-') as working:
        subprocess.run([str(executable), '--self-test', '--report', str(report)], cwd=working,
                       env=env, check=True, timeout=90)
    data = json.loads(report.read_text())
    if not data.get('ok') or not data.get('frozen'):
        raise RuntimeError('Standalone application smoke test failed')
    return data


def build_deb(bundle, output, version):
    if not shutil.which('dpkg-deb'):
        raise RuntimeError('Install dpkg-deb to build the Ubuntu package')
    with tempfile.TemporaryDirectory(prefix='desktop-chibi-deb-') as temporary:
        package = Path(temporary)
        shutil.copytree(bundle, package / 'opt/desktop-chibi')
        command = package / 'usr/bin/desktop-chibi'
        command.parent.mkdir(parents=True)
        command.write_text('#!/bin/sh\nexec /opt/desktop-chibi/desktop-chibi "$@"\n')
        command.chmod(0o755)
        applications = package / 'usr/share/applications'
        applications.mkdir(parents=True)
        (applications / 'desktop-chibi.desktop').write_text(desktop_entry(), encoding='utf-8')
        icons = package / 'usr/share/icons/hicolor/256x256/apps'
        icons.mkdir(parents=True)
        shutil.copy2(ROOT / 'desktop_chibi/assets/desktop-chibi.png', icons / 'desktop-chibi.png')
        control = package / 'DEBIAN'
        control.mkdir()
        glibc = platform.libc_ver()[1] or '2.35'
        depends = [f'libc6 (>= {glibc})', 'libstdc++6', 'libglib2.0-0', 'libdbus-1-3',
                   'libx11-6', 'libxext6', 'libxcb1', 'libxcb-cursor0', 'libxcb-icccm4',
                   'libxcb-image0', 'libxcb-keysyms1', 'libxcb-render-util0', 'libxcb-xinerama0',
                   'libxcb-xkb1', 'libxkbcommon-x11-0', 'libegl1', 'libopengl0', 'libgl1',
                   'libfontconfig1', 'libfreetype6', 'zlib1g']
        deb_version = version.replace('-alpha.', '~alpha.').replace('-beta.', '~beta.').replace('-rc.', '~rc.')
        (control / 'control').write_text(
            f'Package: desktop-chibi\nVersion: {deb_version}\nArchitecture: amd64\n'
            'Maintainer: Magirchik <208955252+Magirchik@users.noreply.github.com>\n'
            'Section: utils\nPriority: optional\nHomepage: https://github.com/Magirchik/desktop-chibi\n'
            f'Depends: {", ".join(depends)}\nRecommends: dbus-bin, libnotify-bin, pipewire-bin, xdg-desktop-portal\n'
            'Description: Animated desktop companions and pixel animation workshop\n'
            ' Desktop pets, imported GIF editing, themes, event reactions and reminders.\n', encoding='utf-8')
        target = output / f'desktop-chibi-v{version}-ubuntu-amd64.deb'
        subprocess.run(['dpkg-deb', '--build', '--root-owner-group', str(package), str(target)], check=True)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', default=__version__)
    parser.add_argument('--deb', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'release')
    args = parser.parse_args(argv)
    version = version_string(args.version)
    tag = os.environ.get('CHIBI_RELEASE_TAG', '')
    if tag and version_string(tag) != version:
        parser.error('Git tag does not match the application version')
    if version != __version__:
        parser.error(f'Tag version {version} does not match desktop_chibi.__version__ {__version__}')
    if platform.machine().casefold() not in ('amd64', 'x86_64'):
        parser.error('This release target supports x86-64 only')
    if sys.platform not in ('win32', 'linux'):
        parser.error('Build on Windows or Linux')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = ROOT / 'build/release'
    work.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
               '--name', 'desktop-chibi', '--noupx', '--paths', str(ROOT),
               '--distpath', str(ROOT / 'dist'), '--workpath', str(work / 'pyinstaller'),
               '--specpath', str(work), '--add-data', str(ROOT / 'desktop_chibi/assets') + ':desktop_chibi/assets']
    if sys.platform == 'win32':
        icon = work / 'desktop-chibi.ico'
        windows_icon(icon)
        command += ['--icon', str(icon), '--exclude-module', 'PySide6.QtDBus']
    else:
        command += ['--icon', str(ROOT / 'desktop_chibi/assets/desktop-chibi.png')]
    command.append(str(ROOT / 'packaging/launcher.py'))
    subprocess.run(command, cwd=ROOT, check=True)
    bundle = ROOT / 'dist/desktop-chibi'
    shutil.copy2(ROOT / 'README.md', bundle / 'README.md')
    shutil.copytree(ROOT / 'docs', bundle / 'docs', dirs_exist_ok=True)
    copy_notices(bundle)
    executable = bundle / ('desktop-chibi.exe' if sys.platform == 'win32' else 'desktop-chibi')
    data = smoke(bundle, executable, output)
    suffix = 'windows-x64' if sys.platform == 'win32' else 'ubuntu-x64'
    name = f'desktop-chibi-v{version}-{suffix}'
    if sys.platform == 'win32':
        target = output / (name + '.zip')
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(bundle.rglob('*')):
                if file.is_file():
                    archive.write(file, Path('desktop-chibi') / file.relative_to(bundle))
    else:
        target = output / (name + '.tar.gz')
        with tarfile.open(target, 'w:gz') as archive:
            archive.add(bundle, arcname='desktop-chibi')
    artifacts = [target]
    if args.deb:
        artifacts.append(build_deb(bundle, output, version))
    manifest = {**data, 'python': platform.python_version(), 'glibc': platform.libc_ver(),
                'artifacts': {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in artifacts}}
    (output / f'build-{suffix}.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Built:', ', '.join(file.name for file in artifacts))


if __name__ == '__main__':
    main()
