"""Conservative recovery from existing, explicitly enabled event rules."""
import copy
import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings

from .events import validate_config


def repair_application(owner):
    hub, manager = owner.event_hub, owner.manager
    sources = copy.deepcopy(hub.sources)
    rules = copy.deepcopy(hub.rules)
    messages = []
    source = next((s for s in sources if s['kind'] == 'vesktop'), None)
    if source and source['enabled']:
        users = list(source.get('users', []))
        for rule in rules:
            if not (rule['enabled'] and rule['source'] == source['id'] and rule['event'] == 'voice.speaking'):
                continue
            if rule.get('scope', 'personal') != 'global' and manager.find(rule['target']) is None:
                continue
            user = rule.get('subject', '')
            if user and user.isdigit() and len(user) <= 24 and user not in users:
                users.append(user)
                target = manager.find(rule['target'])
                messages.append('Восстановлено отслеживание друга для «' + (target.name if target else 'Общее правило') + '».')
        source['users'] = users
    validate_config(sources, rules)
    # Persist in-memory positions/settings before making a complete backup.
    manager.persist()
    owner.store.sync()
    if owner.store.status() != QSettings.Status.NoError:
        raise OSError('Не удалось сохранить текущие настройки. Исправление остановлено.')
    path = Path(owner.store.fileName())
    backup = path.with_name(path.name + '.before-fix-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.bak')
    if not path.is_file():
        raise OSError('Файл настроек недоступен для резервного копирования.')
    shutil.copy2(path, backup)
    backup.chmod(0o600)
    # Same enabled sources and same rules: no new permissions or generated keys.
    hub.configure(sources, rules, hub.discord_token)
    owner.store.sync()
    if owner.store.status() != QSettings.Status.NoError:
        raise OSError('Не удалось записать исправление. Резервная копия: ' + str(backup))
    messages.append('Включённые источники переподключены. Ключи и правила сохранены.')
    for item in manager.items:
        personal = [r for r in rules if r['enabled'] and r.get('scope', 'personal') == 'personal' and r['target'] == item.identity]
        name = '«' + item.name + '»: '
        if not item.pet.requested_visible:
            messages.append(name + 'скрыт вручную. Нажмите «Показать чибика», если нужно.')
        elif item.controller.values.get('hide_when_absent'):
            if not any(r['source'] == 'vesktop' and r['event'] == 'voice.speaking' and r.get('subject') for r in personal):
                messages.append(name + 'скрыт до появления друга, но личное правило Vesktop не задано. Откройте «Речь друга в Vesktop…».')
            else:
                messages.append(name + 'ждёт присутствия друга; проверка связи занимает несколько секунд.')
        if personal and item.controller.mode != 'functional':
            messages.append(name + 'декоративный режим. Для реакций выберите «С событиями».')
        for rule in personal:
            configured = next((s for s in sources if s['id'] == rule['source']), None)
            if configured and not configured['enabled']:
                messages.append(name + 'источник «' + configured['name'] + '» выключен. Включите его в «Правила и источники».')
            animation = rule['animation']
            if animation not in item.pet.animations.clips and animation not in ('idle', 'walk', 'fall', 'land', 'drag', 'talk', 'notify', 'done', 'error'):
                messages.append(name + 'анимация «' + animation + '» недоступна. Выберите её заново в правиле.')
    for rule in rules:
        if rule.get('scope', 'personal') != 'global' and manager.find(rule['target']) is None:
            messages.append('Правило ссылается на удалённого чибика. Выберите нового получателя в «Правила и источники».')
    if source and source['enabled']:
        if not hub.server.isListening():
            messages.append('Локальный канал недоступен: проверьте, не запущен ли второй Desktop Chibi.')
        elif hub.vesktop_bridge is None:
            messages.append('Не удалось создать мост Vesktop. Проверьте состояние источника в диагностике.')
        else:
            messages.append('Мост Vesktop готов. Vesktop должен быть запущен с плагином DesktopChibiBridge.')
    manager.load_active()
    return messages, backup
