#!/usr/bin/env python3
"""Review by default; --install switches a closed Vesktop to the prepared build."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QSettings
from desktop_chibi.events import default_sources, validate_config

FILES = ('vencordDesktopMain.js', 'vencordDesktopPreload.js', 'vencordDesktopRenderer.js', 'vencordDesktopRenderer.css')


def read_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.chibi-tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def require_closed(client):
    lock = client / 'SingletonLock'
    if lock.is_symlink():
        try:
            pid = int(os.readlink(lock).rsplit('-', 1)[-1])
            os.kill(pid, 0)
        except (ValueError, ProcessLookupError):
            pass
        else:
            raise ValueError('Vesktop запущен. Сначала завершите его через меню «Выход».')


def restore_build(release, client, data_home, *, apply=False):
    state = read_json(client / 'state.json', {})
    destination = Path(state.get('vencordDir', '')).resolve()
    managed = (data_home / 'DesktopChibi/vesktop-builds').resolve()
    if destination == managed or not destination.is_relative_to(managed) or not destination.is_dir():
        raise ValueError('Не найдена установленная пользовательская сборка DesktopChibiBridge.')
    print('Восстановить сборку:', destination)
    print('Чибики, правила, ключи, канал и User ID не меняются.')
    print('Автообновление Vencord будет выключено: штатное обновление удаляет пользовательские плагины.')
    if not apply:
        print('Это только план. Закройте Vesktop через «Выход», затем добавьте --install.')
        return
    require_closed(client)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = destination.with_name(destination.name + '.before-restore-' + stamp)
    shutil.copytree(destination, backup)
    settings_file = client / 'settings/settings.json'
    settings = read_json(settings_file, {})
    settings_backup = settings_file.with_name(settings_file.name + '.before-chibi-restore-' + stamp + '.bak')
    had_settings = settings_file.exists()
    if had_settings:
        shutil.copy2(settings_file, settings_backup)
    try:
        shutil.copytree(release, destination, dirs_exist_ok=True)
        settings['autoUpdate'] = False
        settings.setdefault('plugins', {}).setdefault('DesktopChibiBridge', {})['enabled'] = True
        atomic_json(settings_file, settings)
    except Exception:
        shutil.copytree(backup, destination, dirs_exist_ok=True)
        if had_settings:
            shutil.copy2(settings_backup, settings_file)
        raise
    print('Восстановлено. Запустите Vesktop. Резервная копия:', backup)


def prepare_rules(store, target_id):
    data = json.loads(store.value('events_v1', '{}'))
    data.setdefault('sources', default_sources())
    data.setdefault('rules', [])
    companions = json.loads(store.value('companions_v1', '[]'))
    target = next((c for c in companions if c['id'] == target_id), None)
    if target is None:
        raise ValueError('Целевой чибик не найден.')
    source = next((s for s in data['sources'] if s['id'] == 'vesktop'), None)
    if source is None:
        source = next(s for s in default_sources() if s['id'] == 'vesktop')
        data['sources'].append(source)
    previous = next((s for s in data['sources'] if s['id'] == 'discord'), {})
    source.update(enabled=True, channel_id='',
                  users=source.get('users') or previous.get('users', []))
    for user in source['users']:
        existing = next((r for r in data['rules'] if r['target'] == target_id and r['source'] in ('discord', 'vesktop')
                         and r['event'] == 'voice.speaking' and r.get('subject') == user), None)
        if existing is None:
            import uuid
            existing = {'id': uuid.uuid4().hex}
            data['rules'].append(existing)
        existing.update(enabled=True, source='vesktop', event='voice.speaking', subject=user, target=target_id,
                        animation='talk', mode='hold', duration=6, cooldown=0, scope='personal')
    if previous and not any(r['enabled'] and r['source'] == 'discord' for r in data['rules']):
        previous['enabled'] = False
    target['mode'] = 'functional'
    target.setdefault('settings', {})['hide_when_absent'] = True
    validate_config(data['sources'], data['rules'])
    return data, companions, target['name']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--target-id')
    parser.add_argument('--restore-only', action='store_true', help='Восстановить только сборку моста, сохранив все правила чибиков')
    parser.add_argument('--release', type=Path, default=ROOT / '.tools/vesktop-release')
    args = parser.parse_args()
    config_home = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    data_home = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    client = config_home / 'vesktop'
    chibi = config_home / 'DesktopChibi/DesktopChibi.conf'
    state_file, settings_file = client / 'state.json', client / 'settings/settings.json'
    release = args.release.resolve()
    manifest = read_json(release / 'build.json', {})
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', manifest.get('version', '')):
        raise ValueError('Неверная версия сборки.')
    for name in FILES:
        file = release / name
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != manifest.get('files', {}).get(name):
            raise ValueError('Контрольная сумма сборки не совпала: ' + name)
    if args.restore_only:
        restore_build(release, client, data_home, apply=args.install)
        return
    if not args.target_id:
        parser.error('--target-id обязателен для установки; для восстановления используйте --restore-only')
    store = QSettings(str(chibi), QSettings.Format.IniFormat)
    data, companions, name = prepare_rules(store, args.target_id)
    source = next(s for s in data['sources'] if s['id'] == 'vesktop')
    destination = data_home / 'DesktopChibi/vesktop-builds' / manifest['version']
    print('Сборка:', manifest['version'])
    print('Папка установки:', destination)
    print('Плагин: DesktopChibiBridge, включён; без OAuth и токена Discord.')
    print('Личное правило:', name, '→ Говорит; канал:', source['channel_id'] or 'текущий автоматически', '; User ID:', ', '.join(source['users']))
    print('Остальные чибики, правила и настройки Vesktop сохраняются.')
    if not args.install:
        print('Это только план. Для записи: закройте Desktop Chibi и Vesktop, затем добавьте --install.')
        return
    require_closed(client)
    state, settings = read_json(state_file, {}), read_json(settings_file, {})
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backups = {}
    for file in (state_file, settings_file, chibi):
        if file.exists():
            backup = file.with_name(file.name + '.before-chibi-bridge-' + stamp + '.bak')
            shutil.copy2(file, backup)
            backups[file] = backup
    if destination.exists():
        for file in FILES:
            if hashlib.sha256((destination / file).read_bytes()).hexdigest() != manifest['files'][file]:
                raise ValueError('В папке установки другая сборка; существующие файлы не перезаписаны.')
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(release, destination)
    try:
        state['vencordDir'] = str(destination)
        settings.setdefault('plugins', {}).setdefault('DesktopChibiBridge', {})['enabled'] = True
        settings['autoUpdate'] = False
        atomic_json(state_file, state)
        atomic_json(settings_file, settings)
        store.setValue('events_v1', json.dumps(data, ensure_ascii=False))
        store.setValue('companions_v1', json.dumps(companions, ensure_ascii=False))
        store.sync()
        if store.status() != QSettings.Status.NoError:
            raise OSError('QSettings не смог записать настройки.')
    except Exception:
        for file in (state_file, settings_file, chibi):
            if file in backups:
                shutil.copy2(backups[file], file)
            elif file.exists():
                file.unlink()
        raise
    print('Установлено. Запустите Desktop Chibi и Vesktop, войдите в разрешённый канал.')
    for backup in backups.values():
        print('Резервная копия:', backup)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Не установлено:', error, file=sys.stderr)
        sys.exit(1)
