"""Opt-in official Discord local RPC voice events. No audio or account tokens."""
import json
import os
import struct
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalSocket


def default_discord_socket():
    if os.name == 'nt':
        return 'discord-ipc-0'
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
    candidates = [runtime / 'discord-ipc-0', runtime / 'app/com.discordapp.Discord/discord-ipc-0', Path('/tmp/discord-ipc-0')]
    return str(next((path for path in candidates if path.exists()), candidates[0]))


class DiscordRPC(QObject):
    def __init__(self, hub, source, token):
        super().__init__(hub)
        self.hub, self.source, self.token = hub, source, token
        self.socket = QLocalSocket(self)
        self.socket.connected.connect(self.connected)
        self.socket.readyRead.connect(self.read)
        self.socket.disconnected.connect(self.disconnected)
        self.socket.errorOccurred.connect(lambda _: self.failed('Discord IPC недоступен; проверьте клиент и путь к сокету.'))
        self.buffer = bytearray()
        self.pending = {}
        self.channel = None
        self.speakers = set()
        self.authenticated = False
        self.phase = 'connect'
        self.stopping = False
        self.poll = QTimer(self)
        self.poll.setInterval(5000)
        self.poll.timeout.connect(lambda: self.command('GET_SELECTED_VOICE_CHANNEL'))
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(3000)
        self.heartbeat.timeout.connect(self.refresh_speakers)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(10000)
        self.timeout.timeout.connect(self.timed_out)

    def timed_out(self):
        messages = {
            'connect': 'Не удалось подключиться к Discord IPC. Проверьте клиент и путь к сокету.',
            'handshake': 'Клиент не ответил на начало Discord RPC. Проверьте совместимость клиента.',
            'auth': 'Клиент ответил, но не подтвердил OAuth RPC. Нужен Application ID и доступ rpc.voice.read; arRPC/Vesktop не предоставляет события речи.',
        }
        self.failed(messages[self.phase])

    def start(self):
        if not self.source.get('client_id') or not self.token or not self.source.get('channel_id') or not self.source.get('users'):
            self.hub.set_status(self.source['id'], 'Нужны одобренный Client ID, OAuth-токен, Channel ID и разрешённые User ID.')
            return
        self.hub.set_status(self.source['id'], 'Подключение к локальному Discord RPC…')
        self.socket.connectToServer(self.source.get('socket') or default_discord_socket())
        self.timeout.start()

    def send(self, opcode, data):
        payload = json.dumps(data, separators=(',', ':')).encode()
        self.socket.write(struct.pack('<II', opcode, len(payload)) + payload)

    def connected(self):
        self.phase = 'handshake'
        self.timeout.start()
        self.send(0, {'v': 1, 'client_id': self.source['client_id']})

    def command(self, command, args=None, event=None):
        if not self.authenticated and command != 'AUTHENTICATE':
            return
        nonce = uuid.uuid4().hex
        self.pending[nonce] = command
        if len(self.pending) > 64:
            self.failed('Discord перестал отвечать на RPC-команды.')
            return
        data = {'cmd': command, 'nonce': nonce}
        if args is not None:
            data['args'] = args
        if event:
            data['evt'] = event
        self.send(1, data)

    def read(self):
        self.buffer.extend(bytes(self.socket.readAll()))
        if len(self.buffer) > 1024 * 1024:
            self.failed('Слишком большой пакет Discord RPC.')
            return
        while len(self.buffer) >= 8:
            opcode, length = struct.unpack('<II', self.buffer[:8])
            if length > 1024 * 1024:
                self.failed('Некорректный пакет Discord RPC.')
                return
            if len(self.buffer) < 8 + length:
                return
            raw = bytes(self.buffer[8:8 + length])
            del self.buffer[:8 + length]
            try:
                data = json.loads(raw)
                if opcode == 3:
                    self.send(4, data)
                elif opcode == 2:
                    self.failed('Discord закрыл RPC-подключение.')
                elif opcode == 1 and isinstance(data, dict):
                    self.handle(data)
            except (ValueError, TypeError):
                self.failed('Некорректный ответ Discord RPC.')
                return

    def handle(self, packet):
        event = packet.get('evt')
        command = self.pending.pop(packet.get('nonce'), None)
        data = packet.get('data') or {}
        if not isinstance(data, dict):
            return
        if event == 'ERROR':
            self.failed('Discord отказал в RPC-доступе. Нужен OAuth scope rpc.voice.read у одобренного приложения.')
        elif event == 'READY':
            user = data.get('user') or {}
            if isinstance(user, dict) and user.get('id') == '1045800378228281345' and user.get('username') == 'arrpc':
                self.failed('Обнаружен arRPC (Vesktop): этот интерфейс поддерживает Rich Presence, но не события речи. Для реакции на друга нужен отдельный мост Vesktop. OAuth-токен сюда не требуется.')
                return
            self.phase = 'auth'
            self.timeout.start()
            self.hub.set_status(self.source['id'], 'Клиент Discord RPC ответил; проверяется OAuth-доступ…')
            self.command('AUTHENTICATE', {'access_token': self.token})
        elif command == 'AUTHENTICATE':
            self.authenticated = True
            self.timeout.stop()
            self.command('GET_SELECTED_VOICE_CHANNEL')
            self.poll.start()
            self.heartbeat.start()
            self.hub.set_status(self.source['id'], 'RPC авторизован; ожидает выбранный голосовой канал')
        elif command == 'GET_SELECTED_VOICE_CHANNEL':
            self.select_channel(data.get('id'))
        elif event in ('SPEAKING_START', 'SPEAKING_STOP') and self.authenticated and self.channel:
            user = data.get('user_id')
            if user not in self.source.get('users', []):
                return
            active = event == 'SPEAKING_START'
            if active:
                self.speakers.add(user)
            else:
                self.speakers.discard(user)
            self.hub.dispatch(self.source['id'], 'voice.speaking', user, active)

    def select_channel(self, selected):
        wanted = self.source['channel_id']
        selected = selected if selected == wanted else None
        if selected == self.channel:
            return
        self.clear_speakers()
        if self.channel:
            for event in ('SPEAKING_START', 'SPEAKING_STOP'):
                self.command('UNSUBSCRIBE', {'channel_id': self.channel}, event)
        self.channel = selected
        if self.channel:
            for event in ('SPEAKING_START', 'SPEAKING_STOP'):
                self.command('SUBSCRIBE', {'channel_id': self.channel}, event)
            self.hub.set_status(self.source['id'], 'Слушает выбранный канал и разрешённых участников')
        else:
            self.hub.set_status(self.source['id'], 'Ожидает входа в разрешённый голосовой канал')

    def refresh_speakers(self):
        for user in self.speakers:
            self.hub.dispatch(self.source['id'], 'voice.speaking', user, True)

    def clear_speakers(self):
        for user in self.speakers:
            self.hub.dispatch(self.source['id'], 'voice.speaking', user, False)
        self.speakers.clear()

    def failed(self, message):
        if self.stopping:
            return
        self.hub.set_status(self.source['id'], message)
        self.stop()

    def disconnected(self):
        if not self.stopping:
            self.failed('Discord RPC отключён. Для повторного подключения нажмите «Сохранить» в источниках.')

    def stop(self):
        self.stopping = True
        self.poll.stop()
        self.heartbeat.stop()
        self.timeout.stop()
        self.clear_speakers()
        self.authenticated = False
        self.socket.abort()
        self.buffer.clear()
        self.pending.clear()
        self.token = ''
