"""Private per-user configuration for the Vesktop plugin's local IPC sender."""
import json
import os
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QTimer


def bridge_config_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'DesktopChibi' / 'vesktop-bridge.json'


class VesktopBridge(QObject):
    def __init__(self, hub, source):
        super().__init__(hub)
        self.hub, self.source = hub, source
        self.path = bridge_config_path()
        self.last_seen = 0.0
        self.waiting = False
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.check)
        config = {'version': 1, 'source': source['id'], 'token': source['token'],
                  'channel_id': source['channel_id'], 'users': source['users']}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name('.vesktop-bridge-' + uuid.uuid4().hex)
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(config, output)
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()
        self.hub.set_status(source['id'], 'Мост готов. Включите DesktopChibiBridge в Vesktop.')
        self.timer.start()

    def received(self):
        self.last_seen = time.monotonic()
        if not self.waiting:
            self.hub.set_status(self.source['id'], 'Плагин Vesktop подключён; фильтр канала и пользователей включён')
        self.waiting = True

    def check(self):
        if self.waiting and time.monotonic() - self.last_seen > 8:
            self.waiting = False
            self.hub.voice_presence.clear()
            self.hub.update_presence()
            for user in self.source['users']:
                self.hub.dispatch(self.source['id'], 'voice.speaking', user, False, record=False)
            self.hub.set_status(self.source['id'], 'Нет связи с плагином Vesktop. Реакции речи остановлены.')

    def stop(self):
        self.timer.stop()
        try:
            saved = json.loads(self.path.read_text())
            if saved.get('token') == self.source['token'] and saved.get('source') == self.source['id']:
                self.path.unlink()
        except (OSError, ValueError, AttributeError):
            pass
