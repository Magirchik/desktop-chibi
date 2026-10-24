"""Optional Linux notification metadata adapter; no message text is retained."""
import shutil
import sys
import codecs
import json
import re
import time
from collections import deque
from PySide6.QtCore import QObject, QProcess, Signal

# A desktop label, libnotify app_name and GApplication ID can differ.
# Restrict aliases to this known client; arbitrary applications still match exactly.
TELEGRAM_NAMES = ('Telegram', 'Telegram Desktop', 'telegram-desktop',
                  'org.telegram.desktop', 'org.telegram.desktop.desktop',
                  'telegram-desktop_telegram-desktop')
NOTIFICATION_METHODS = (
    ('org.freedesktop.Notifications', 'Notify'),
    ('org.gtk.Notifications', 'AddNotification'),
    ('org.freedesktop.impl.portal.Notification', 'AddNotification'),
)


def notification_aliases(app):
    return TELEGRAM_NAMES if app in TELEGRAM_NAMES else (app,)


class NotificationAdapter(QObject):
    found = Signal(str)

    def __init__(self, hub, source, *, discovery=False):
        super().__init__(hub)
        self.hub, self.source = hub, source
        self.discovery = discovery
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.process = QProcess(self)
        self.buffer = ''
        self.expect_app = False
        self.dropping = False
        self.stopping = False
        self.hop = None
        self.recent_hops = deque(maxlen=64)
        self.process.readyReadStandardOutput.connect(self.read)
        self.process.readyReadStandardError.connect(lambda: self.process.readAllStandardError())
        self.process.errorOccurred.connect(self.error)
        self.process.finished.connect(self.finished)

    def start(self):
        if not sys.platform.startswith('linux') or not shutil.which('dbus-monitor'):
            self.hub.set_status(self.source['id'], 'Нужны Linux и dbus-monitor.')
            return
        apps = self.source.get('apps', [])
        if not apps and not self.discovery:
            self.hub.set_status(self.source['id'], 'Выберите разрешённые приложения.')
            return
        # Filter at the bus, before notification content reaches our parser.
        names = dict.fromkeys(name for app in apps for name in notification_aliases(app))
        matches = []
        for interface, member in NOTIFICATION_METHODS:
            base = f"type='method_call',interface='{interface}',member='{member}'"
            matches.extend([base] if self.discovery else [base + f",arg0='{name}'" for name in names])
        self.process.started.connect(lambda: self.hub.set_status(self.source['id'],
            'Временно определяет имена отправителей' if self.discovery else 'Слушает только выбранные приложения'))
        self.process.start('dbus-monitor', ['--session', *matches])

    def read(self):
        chunk = self.decoder.decode(bytes(self.process.readAllStandardOutput()))
        self.feed(chunk)

    def feed(self, chunk):
        # Lines containing message bodies are discarded, never sent to rules/logs.
        for character in chunk:
            if character == '\n':
                if not self.dropping:
                    self.line(self.buffer)
                self.buffer = ''
                self.dropping = False
            elif not self.dropping:
                self.buffer += character
                if len(self.buffer) > 2048:
                    self.buffer = ''
                    self.dropping = True

    def line(self, line):
        if line.startswith('method call '):
            sender = re.search(r'\bsender=([^ ]+)', line)
            destination = re.search(r'\bdestination=([^ ]+)', line)
            serial = re.search(r'\bserial=(\d+)', line)
            self.hop = (sender[1], destination[1], serial[1]) if sender and destination and serial else None
            self.expect_app = ('member=Notify' in line or any(
                f'interface={interface};' in line and f'member={member}' in line
                for interface, member in NOTIFICATION_METHODS))
        elif self.expect_app:
            self.expect_app = False
            value = line.strip()
            if value.startswith('string "') and value.endswith('"'):
                try:
                    app = json.loads(value[7:])
                except ValueError:
                    return
                if not isinstance(app, str) or not app or len(app) > 100 or any(c in app for c in "'\\,\r\n"):
                    return
                if self.discovery:
                    self.found.emit(app)
                else:
                    selected = next((name for name in self.source.get('apps', [])
                                     if app in notification_aliases(name)), None)
                    if selected:
                        if self.forwarded(selected):
                            return
                        self.hub.dispatch(self.source['id'], 'message.received', selected)
                        self.hub.set_status(self.source['id'], f'Получено уведомление: {app}')

    def forwarded(self, app):
        """A -> notification proxy -> shell is one delivery, not two messages.

        Keep only bounded routing metadata. Separate calls by the same client
        remain separate even when their app names and timestamps are identical.
        """
        if self.hop is None:
            return False
        now = time.monotonic()
        while self.recent_hops and now - self.recent_hops[0][0] > 2:
            self.recent_hops.popleft()
        sender, destination, serial = self.hop
        duplicate = any(name == app and (hop == self.hop or hop[1] == sender)
                        for _, name, hop in self.recent_hops)
        self.recent_hops.append((now, app, self.hop))
        return duplicate

    def finished(self, *_):
        if not self.stopping:
            self.hub.set_status(self.source['id'], 'Источник остановлен. Возможен запрет мониторинга D-Bus.')

    def error(self, *_):
        if not self.stopping:
            self.hub.set_status(self.source['id'], 'Не удалось запустить dbus-monitor. Проверьте Linux, D-Bus и доступ к сеансу.')

    def stop(self):
        self.stopping = True
        self.process.kill()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.waitForFinished(1000)
        self.buffer = ''
        self.expect_app = False
        self.hop = None
        self.recent_hops.clear()
