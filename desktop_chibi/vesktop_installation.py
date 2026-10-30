"""Read-only checks of the local custom Vencord installation."""
import hashlib
import json
import os
from pathlib import Path

BUILD_FILES = ('vencordDesktopMain.js', 'vencordDesktopPreload.js',
               'vencordDesktopRenderer.js', 'vencordDesktopRenderer.css')


def installation_problem(config_home=None):
    root = Path(config_home or os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    try:
        state_path = root / 'vesktop/state.json'
        if not state_path.is_file():
            return None
        state = json.loads(state_path.read_text())
        directory = state.get('vencordDir')
        if not directory:
            return 'Vesktop использует штатную сборку без моста. Установите DesktopChibiBridge и перезапустите Vesktop.'
        build = Path(directory)
        manifest_path = build / 'build.json'
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text())
            for name in BUILD_FILES:
                file = build / name
                if (not file.is_file() or file.stat().st_size > 256 * 1024 * 1024
                        or hashlib.sha256(file.read_bytes()).hexdigest() != manifest.get('files', {}).get(name)):
                    return ('Сборка DesktopChibiBridge изменена или повреждена — возможно, обновлением Vencord. '
                            'Восстановите сборку моста и перезапустите Vesktop.')
        settings_path = root / 'vesktop/settings/settings.json'
        if settings_path.is_file():
            settings = json.loads(settings_path.read_text())
            plugin = settings.get('plugins', {}).get('DesktopChibiBridge', {})
            if plugin.get('enabled') is False:
                return 'DesktopChibiBridge выключен в Vesktop. Включите его в настройках плагинов.'
    except (OSError, ValueError, TypeError, AttributeError):
        return 'Не удалось проверить установленный DesktopChibiBridge. Проверьте папку пользовательской сборки Vesktop.'
    return None
