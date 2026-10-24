"""Opt-in event routing and a user-local, authenticated IPC input."""
import collections
import getpass
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from .event_sound import EventSound, validate_sound

EVENT_PATTERN = re.compile(r'[a-z][a-z0-9_.-]{0,63}\Z')


def socket_name():
    if os.name == 'nt':
        return 'desktop-chibi-' + re.sub(r'[^a-zA-Z0-9_-]', '_', getpass.getuser())
    return str(Path(tempfile.gettempdir()) / f'desktop-chibi-{os.getuid()}.sock')


def default_sources():
    return [
        {'id': 'code', 'name': 'Завершение кода / команды', 'kind': 'local', 'enabled': False,
         'events': ['code.done', 'code.failed'], 'token': secrets.token_urlsafe(32)},
        {'id': 'custom', 'name': 'Свои локальные события', 'kind': 'local', 'enabled': False,
         'events': ['message.received', 'voice.speaking', 'custom'], 'token': secrets.token_urlsafe(32)},
        {'id': 'notifications', 'name': 'Системные уведомления Linux', 'kind': 'notifications',
         'enabled': False, 'events': ['message.received'], 'apps': []},
        {'id': 'discord', 'name': 'Discord: кто говорит (RPC)', 'kind': 'discord',
         'enabled': False, 'events': ['voice.speaking'], 'client_id': '', 'channel_id': '',
         'users': [], 'socket': ''},
        {'id': 'vesktop', 'name': 'Vesktop: речь выбранных пользователей (локальный плагин)', 'kind': 'vesktop',
         'enabled': False, 'events': ['voice.speaking'], 'token': secrets.token_urlsafe(32),
         'channel_id': '', 'users': []},
        {'id': 'audio', 'name': 'Воспроизведение звука приложения (PipeWire)', 'kind': 'audio',
         'enabled': False, 'events': ['audio.playing'], 'apps': []},
        {'id': 'reminder', 'name': 'Напоминание о перерыве', 'kind': 'timer', 'enabled': False,
         'events': ['timer.done'], 'timer_mode': 'repeat', 'seconds': 3600, 'break_seconds': 300},
        {'id': 'timer', 'name': 'Обратный отсчёт', 'kind': 'timer', 'enabled': False,
         'events': ['timer.done'], 'timer_mode': 'once', 'seconds': 600, 'break_seconds': 300},
        {'id': 'focus', 'name': 'Работа / перерыв', 'kind': 'timer', 'enabled': False,
         'events': ['focus.work', 'focus.break'], 'timer_mode': 'focus', 'seconds': 1500, 'break_seconds': 300},
    ]


def validate_config(sources, rules):
    if not isinstance(sources, list) or not isinstance(rules, list) or len(sources) > 32 or len(rules) > 128:
        raise ValueError('Допускается до 32 источников и 128 правил.')
    ids = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError('Неверное описание источника.')
        identity = source.get('id', '')
        if not isinstance(identity, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', identity) or identity in ids:
            raise ValueError('ID источника: латиница, цифры, _ или -, без повторений.')
        ids.add(identity)
        if source.get('kind') not in ('local', 'notifications', 'discord', 'audio', 'vesktop', 'timer') or type(source.get('enabled')) is not bool:
            raise ValueError('Неверный тип или разрешение источника.')
        allowed = {'id', 'name', 'kind', 'enabled', 'events'} | {
            'local': {'token'}, 'vesktop': {'token', 'channel_id', 'users'}, 'notifications': {'apps'}, 'audio': {'apps'},
            'discord': {'client_id', 'channel_id', 'users', 'socket'},
            'timer': {'timer_mode', 'seconds', 'break_seconds'},
        }[source['kind']]
        if set(source) - allowed:
            raise ValueError('У источника есть неподдерживаемые поля. OAuth-токен передаётся отдельно и не сохраняется.')
        events = source.get('events', [])
        if not isinstance(events, list) or not events or len(events) > 32 or any(not isinstance(e, str) or not EVENT_PATTERN.fullmatch(e) for e in events):
            raise ValueError('Задайте список допустимых имён событий: например code.done.')
        if source['kind'] == 'timer':
            if source.get('timer_mode') not in ('once', 'repeat', 'focus'):
                raise ValueError('Выберите режим таймера.')
            expected = {'focus.work', 'focus.break'} if source['timer_mode'] == 'focus' else {'timer.done'}
            if set(events) != expected:
                raise ValueError('События таймера должны соответствовать его режиму.')
            for key in ('seconds', 'break_seconds'):
                if type(source.get(key)) is not int or not 60 <= source[key] <= 86400:
                    raise ValueError('Интервал таймера: от 1 минуты до 24 часов.')
        if source['kind'] in ('local', 'vesktop') and (not isinstance(source.get('token'), str) or len(source['token']) < 32):
            raise ValueError('Локальному источнику нужен ключ длиной не менее 32 символов.')
        if source['kind'] in ('notifications', 'audio'):
            apps = source.get('apps', [])
            if not isinstance(apps, list) or len(apps) > 32 or any(not isinstance(a, str) or not a or len(a) > 100 or any(c in a for c in "'\\,\r\n") for a in apps):
                raise ValueError('Имена приложений: до 100 символов, без кавычек, запятых и обратного слеша.')
        if source['kind'] == 'vesktop' and (source['id'] != 'vesktop' or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', source['token'])):
            raise ValueError('Для моста Vesktop нужен ID vesktop и сгенерированный локальный ключ.')
        if source['kind'] in ('discord', 'vesktop'):
            users = source.get('users', [])
            if not isinstance(users, list) or len(users) > 64 or any(not isinstance(u, str) or not u.isdigit() or len(u) > 24 for u in users):
                raise ValueError('Discord: укажите числовые ID разрешённых пользователей.')
            if source['kind'] == 'vesktop' and source['enabled'] and not users:
                raise ValueError('Vesktop: укажите User ID разрешённого друга. Пустой Channel ID означает текущий канал.')
            for key in (('client_id', 'channel_id') if source['kind'] == 'discord' else ('channel_id',)):
                value = source.get(key, '')
                if not isinstance(value, str) or (value and (not value.isdigit() or len(value) > 24)):
                    raise ValueError('Discord: Client ID и Channel ID должны содержать цифры.')
    rule_ids = set()
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get('id'), str) or rule['id'] in rule_ids:
            raise ValueError('Правила должны иметь разные ID.')
        rule_ids.add(rule['id'])
        source = next((s for s in sources if s['id'] == rule.get('source')), None)
        if not source or rule.get('event') not in source['events']:
            raise ValueError('Событие правила должно быть разрешено выбранным источником.')
        if rule.get('scope', 'personal') not in ('personal', 'global'):
            raise ValueError('Выберите личное или общее правило.')
        if not isinstance(rule.get('target'), str) or not isinstance(rule.get('animation'), str):
            raise ValueError('Выберите чибика и анимацию.')
        if type(rule.get('enabled')) is not bool or rule.get('mode') not in ('pulse', 'hold'):
            raise ValueError('Неверный режим правила.')
        if not isinstance(rule.get('subject', ''), str) or len(rule.get('subject', '')) > 128:
            raise ValueError('Фильтр участника / приложения: до 128 символов.')
        if 'sound' in rule:
            validate_sound(rule['sound'])
        for key, low, high in [('duration', .2, 60), ('cooldown', 0, 300)]:
            value = rule.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError(f'Неверное значение {key}.')


class EventHub(QObject):
    changed = Signal()
    status_changed = Signal()

    @property
    def rules(self):
        return self._rules

    @rules.setter
    def rules(self, rules):
        self._rules = rules
        self._routes = collections.defaultdict(list)
        for rule in rules:
            if rule.get('enabled'):
                self._routes[(rule['source'], rule['event'])].append(rule)

    def __init__(self, manager, store, parent=None):
        super().__init__(parent)
        self.manager, self.store = manager, store
        self.sources, self.rules = default_sources(), []
        self.log = collections.deque(maxlen=40)
        self.last_fire = {}
        self.status = {}
        self.adapters = []
        self.vesktop_bridge = None
        self.voice_presence = {}
        self.discord_token = ''  # session only, never written to settings
        self.sound = EventSound(self)
        self.sound.status.connect(lambda text: self.set_status('sound', text))
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.setMaxPendingConnections(16)
        self.server.newConnection.connect(self.new_connection)
        self.sockets = set()
        saved = store.value('events_v1', '')
        if saved:
            try:
                data = json.loads(saved)
                validate_config(data['sources'], data['rules'])
                self.sources, self.rules = data['sources'], data['rules']
                for missing in ('audio', 'vesktop', 'reminder', 'timer', 'focus'):
                    if not self.source(missing) and len(self.sources) < 32:
                        self.sources.append(next(s for s in default_sources() if s['id'] == missing))
            except (ValueError, TypeError, KeyError, AttributeError):
                self.status['config'] = 'Конфигурация событий недоступна; источники выключены.'

    def start(self):
        self.restart_adapters()

    def configure(self, sources, rules, discord_token=''):
        validate_config(sources, rules)
        self.stop()
        previous_rules = {rule['id']: rule for rule in self.rules}
        self.sources, self.rules = json.loads(json.dumps(sources)), json.loads(json.dumps(rules))
        for rule in self.rules:
            if rule['enabled'] and rule.get('scope', 'personal') != 'global' and rule != previous_rules.get(rule['id']):
                self.manager.set_mode(rule['target'], 'functional')
        self.discord_token = discord_token
        self.last_fire.clear()
        self.store.setValue('events_v1', json.dumps({'sources': self.sources, 'rules': self.rules}, ensure_ascii=False))
        self.restart_adapters()
        self.changed.emit()

    def source(self, identity):
        return next((s for s in self.sources if s['id'] == identity), None)

    def set_status(self, identity, text):
        self.status[identity] = text
        self.status_changed.emit()

    def restart_adapters(self):
        self.update_presence()
        from .event_adapters import NotificationAdapter
        from .discord_rpc import DiscordRPC
        from .audio_adapter import AudioAdapter
        from .timed_events import TimedEvents
        if any(s['enabled'] and s['kind'] in ('local', 'vesktop') for s in self.sources):
            if self.listen_local():
                self.set_status('local', 'Локальный канал включён')
            else:
                self.set_status('local', 'Локальный канал занят или недоступен. Проверьте другой экземпляр приложения.')
        else:
            self.set_status('local', 'Локальный канал выключен')
        for source in self.sources:
            if not source['enabled']:
                self.set_status(source['id'], 'Выключен')
            elif source['kind'] == 'vesktop':
                if not self.server.isListening():
                    self.set_status(source['id'], 'Локальный канал занят. Закройте другой экземпляр Desktop Chibi.')
                    continue
                try:
                    from .vesktop_bridge import VesktopBridge
                    self.vesktop_bridge = VesktopBridge(self, source)
                except OSError:
                    self.set_status(source['id'], 'Не удалось создать приватную конфигурацию моста Vesktop.')
            elif source['kind'] == 'notifications':
                adapter = NotificationAdapter(self, source)
                self.adapters.append(adapter)
                adapter.start()
            elif source['kind'] == 'audio':
                adapter = AudioAdapter(self, source)
                self.adapters.append(adapter)
                adapter.start()
            elif source['kind'] == 'discord':
                adapter = DiscordRPC(self, source, self.discord_token)
                self.adapters.append(adapter)
                adapter.start()
            elif source['kind'] == 'timer':
                adapter = TimedEvents(self, source)
                self.adapters.append(adapter)
                adapter.start()
            else:
                self.set_status(source['id'], self.status.get('local', 'Выключен'))

    def listen_local(self):
        name = socket_name()
        if self.server.listen(name):
            return True
        # Recover only a stale socket owned by this user, never a live instance.
        if os.name != 'nt':
            try:
                info = Path(name).lstat()
                if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid():
                    probe = QLocalSocket()
                    probe.connectToServer(name)
                    connected = probe.waitForConnected(100)
                    error = probe.error()
                    probe.abort()
                    if not connected and error == QLocalSocket.LocalSocketError.ConnectionRefusedError:
                        QLocalServer.removeServer(name)
                        return self.server.listen(name)
            except OSError:
                pass
        return False

    def stop(self):
        self.sound.stop()
        self.voice_presence.clear()
        self.update_presence()
        if self.vesktop_bridge:
            self.vesktop_bridge.stop()
            self.vesktop_bridge.deleteLater()
            self.vesktop_bridge = None
        for adapter in self.adapters:
            adapter.stop()
            adapter.deleteLater()
        self.adapters.clear()
        self.server.close()
        for socket in list(self.sockets):
            socket.abort()
        for companion in self.manager.items:
            companion.pet.clear_notifications()

    def remove_target(self, identity):
        self.rules = [r for r in self.rules if r.get('scope') == 'global' or r['target'] != identity]
        self.store.setValue('events_v1', json.dumps({'sources': self.sources, 'rules': self.rules}, ensure_ascii=False))
        self.changed.emit()

    def dispatch(self, source_id, event, subject='', active=True, *, now=None, record=True, renew_only=False):
        source = self.source(source_id)
        if not source or not source['enabled'] or event not in source['events']:
            return 0
        if not isinstance(subject, str) or len(subject) > 128 or type(active) is not bool:
            return 0
        if source['kind'] in ('notifications', 'audio') and subject not in source.get('apps', []):
            return 0
        if source['kind'] in ('discord', 'vesktop') and subject not in source.get('users', []):
            return 0
        now = time.monotonic() if now is None else now
        matched = 0
        outcomes = []
        sounded = set()
        routes = self._routes.get((source_id, event), ())
        personal = {r['target'] for r in routes if r['enabled'] and r.get('scope', 'personal') == 'personal'
                    and (not r.get('subject') or r['subject'] == subject)}
        for rule in routes:
            if not rule['enabled'] or (renew_only and rule['mode'] != 'hold'):
                continue
            if rule.get('subject') and rule['subject'] != subject:
                outcomes.append({'result': 'filter'})
                continue
            common = rule.get('scope') == 'global'
            targets = list(self.manager.items) if common else [self.manager.find(rule['target'])]
            for target in targets:
                outcome = {'target': target.name if target else 'Удалённый чибик'}
                outcomes.append(outcome)
                if target is None:
                    outcome['result'] = 'missing'
                    continue
                if target.controller.mode != 'functional':
                    outcome['result'] = 'decorative'
                    continue
                if common and target.identity in personal:
                    outcome['result'] = 'personal'
                    continue
                key = rule['id'] + ':' + subject
                if not active:
                    target.pet.cancel_notification(key)
                    outcome['result'] = 'stopped'
                    continue
                cooldown_key = (rule['id'], target.identity) if common else rule['id']
                previous = self.last_fire.get(cooldown_key, -float('inf'))
                if rule['mode'] == 'pulse' and now - previous < rule['cooldown']:
                    outcome['result'] = 'cooldown'
                    continue
                ongoing = target.pet.notifications.get(key, {}).get('until', 0) > now
                if target.pet.trigger_notification(key, rule['animation'], rule['duration'], now,
                                                   restart=rule['mode'] == 'pulse'):
                    matched += 1
                    if rule['mode'] == 'pulse':
                        self.last_fire[cooldown_key] = now
                    outcome['result'] = ('hidden' if not target.pet.isVisible() else
                                         'waiting' if 'pending' in target.pet.notifications.get(key, {}) else 'started')
                    sound = rule.get('sound', {})
                    if (sound.get('enabled') and not renew_only and (rule['mode'] == 'pulse' or not ongoing)
                            and (target.pet.isVisible() or sound.get('when_hidden', False))
                            and sound.get('file') not in sounded):
                        self.sound.play(sound)
                        sounded.add(sound.get('file'))
                else:
                    outcome['result'] = 'animation'
        if record:
            self.log.append({'time': time.strftime('%H:%M:%S'), 'source': source_id, 'event': event,
                             'subject': subject, 'active': active, 'matched': matched, 'outcomes': outcomes})
            self.changed.emit()
        return matched

    def update_presence(self):
        source = self.source('vesktop')
        for item in self.manager.items:
            # Presence is attached to this companion's personal speaking rules.
            users = {r.get('subject') for r in self.rules if r['enabled'] and r['source'] == 'vesktop'
                     and r['event'] == 'voice.speaking' and r.get('scope', 'personal') == 'personal'
                     and r['target'] == item.identity}
            present = bool(source and source['enabled'] and item.controller.mode == 'functional'
                           and any(self.voice_presence.get(user, False) for user in users))
            item.pet.set_friend_present(present)

    def receive_local(self, data):
        if not isinstance(data, dict):
            return {'ok': False, 'error': 'invalid_packet'}
        source = self.source(data.get('source'))
        bridge = source is not None and source['kind'] == 'vesktop'
        allowed = {'source', 'event', 'subject', 'active', 'token'} | ({'channel', 'keepalive', 'present'} if bridge else set())
        if set(data) - allowed:
            return {'ok': False, 'error': 'invalid_packet'}
        token = data.get('token', '')
        if (not source or source['kind'] not in ('local', 'vesktop') or not source['enabled'] or not isinstance(token, str)
                or not hmac.compare_digest(token.encode(), source['token'].encode())):
            return {'ok': False, 'error': 'source_not_authorized'}
        if data.get('event') not in source['events'] or not isinstance(data.get('subject', ''), str) or len(data.get('subject', '')) > 128 or type(data.get('active', True)) is not bool:
            return {'ok': False, 'error': 'event_not_allowed'}
        if bridge:
            if ((source.get('channel_id') and data.get('channel') != source['channel_id']) or not isinstance(data.get('channel'), str) or not re.fullmatch(r'\d{1,24}', data.get('channel', '')) or data.get('subject') not in source['users']
                    or type(data.get('keepalive', False)) is not bool or ('present' in data and type(data['present']) is not bool)):
                return {'ok': False, 'error': 'voice_not_allowed'}
            if self.vesktop_bridge:
                self.vesktop_bridge.received()
            if 'present' in data:
                self.voice_presence[data['subject']] = data['present']
                self.update_presence()
        keepalive = bridge and data.get('keepalive', False)
        matched = self.dispatch(source['id'], data['event'], data.get('subject', ''), data.get('active', True),
                                record=not keepalive, renew_only=keepalive)
        return {'ok': True, 'matched': matched}

    def new_connection(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if len(self.sockets) >= 16:
                socket.abort()
                socket.deleteLater()
                continue
            self.sockets.add(socket)
            socket.setReadBufferSize(8193)
            socket._buffer = bytearray()
            socket._handled = False
            socket.readyRead.connect(lambda sock=socket: self.read_socket(sock))
            socket.disconnected.connect(lambda sock=socket: self.release_socket(sock))
            QTimer.singleShot(2000, socket, socket.disconnectFromServer)
            if socket.bytesAvailable():
                self.read_socket(socket)

    def read_socket(self, socket):
        if socket._handled:
            return
        socket._buffer.extend(bytes(socket.readAll()))
        if len(socket._buffer) > 8192:
            socket._handled = True
            socket.abort()
            return
        if b'\n' not in socket._buffer:
            return
        try:
            packet = json.loads(bytes(socket._buffer).split(b'\n', 1)[0])
            response = self.receive_local(packet)
        except (ValueError, TypeError, UnicodeError):
            response = {'ok': False, 'error': 'invalid_json'}
        socket._buffer.clear()
        socket._handled = True
        socket.write(json.dumps(response).encode() + b'\n')
        socket.disconnectFromServer()

    def release_socket(self, socket):
        self.sockets.discard(socket)
        socket.deleteLater()
