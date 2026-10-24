"""GNOME bridge and transient, global controls; no keyboard polling."""
import json
import sys

from PySide6.QtCore import QObject, QTimer, Signal, Slot, SLOT, QProcess
from PySide6.QtWidgets import QApplication
from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusPendingCallWatcher

SERVICE = 'org.gnome.Shell'
PATH = '/org/gnome/Shell/Extensions/DesktopChibi'
INTERFACE = 'org.gnome.Shell.Extensions.DesktopChibi'


def normalize_app(value):
    return str(value).strip().casefold().removesuffix('.desktop')


def clean_window_rule(value):
    if not isinstance(value, dict):
        return {'mode': 'all', 'apps': []}
    mode = value.get('mode', 'all')
    apps = value.get('apps', [])
    return {'mode': mode if mode in ('all', 'only', 'except') else 'all',
            'apps': list(dict.fromkeys(normalize_app(app) for app in apps
                                      if isinstance(app, str) and normalize_app(app)))[:128]
            if isinstance(apps, list) else []}


def window_allowed(rule, active_app, available=True):
    rule = clean_window_rule(rule)
    if rule['mode'] == 'all':
        return True
    # A missing bridge never silently bypasses configured restrictions.
    if not available:
        return False
    match = normalize_app(active_app) in rule['apps']
    return match if rule['mode'] == 'only' else not match


class DesktopControls(QObject):
    changed = Signal()
    applications = Signal(list)

    def __init__(self, manager):
        super().__init__(manager)
        self.manager = manager
        self.hidden = False
        self.input_override = None
        self.active_app = ''
        self.available = False
        self.status = 'Правила окон: мост GNOME не подключён.'
        self.shortcut_status = 'Хоткеи выключены. Нажмите «Настроить хоткеи…».'
        self.shortcut_client = None
        self.bus = QDBusConnection.sessionBus()
        self.pending = set()
        self.state_pending = False
        self.bus.connect(SERVICE, PATH, INTERFACE, 'ActiveAppChanged', self, SLOT('active_changed(QString)'))
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()
        if manager.owner.store.value('portal_shortcuts_enabled', False, type=bool):
            QTimer.singleShot(0, self, self.start_shortcuts)

    def call(self, method, callback):
        message = QDBusMessage.createMethodCall(SERVICE, PATH, INTERFACE, method)
        watcher = QDBusPendingCallWatcher(self.bus.asyncCall(message, 1000), self)
        self.pending.add(watcher)

        def finished():
            reply = watcher.reply()
            self.pending.discard(watcher)
            watcher.deleteLater()
            try:
                data = json.loads(reply.arguments()[0]) if reply.type() != QDBusMessage.MessageType.ErrorMessage else None
            except (ValueError, TypeError, IndexError):
                data = None
            callback(data)

        watcher.finished.connect(finished)

    def refresh(self):
        if self.state_pending:
            return
        self.state_pending = True
        self.call('GetState', self.receive_state)

    def receive_state(self, state):
        self.state_pending = False
        previous = (self.available, self.active_app)
        self.available = isinstance(state, dict) and isinstance(state.get('active'), str)
        if self.available:
            self.active_app = state['active']
            self.status = 'Правила окон: активно ' + (self.active_app or 'рабочий стол')
        else:
            self.status = 'Правила окон: мост GNOME не подключён; чибики с правилами окон скрыты.'
        if previous != (self.available, self.active_app):
            self.apply_visibility()
        else:
            self.changed.emit()

    @Slot(str)
    def active_changed(self, identity):
        if self.available and self.active_app == identity:
            return
        self.active_app = identity
        self.available = True
        self.status = 'Правила окон: активно ' + (self.active_app or 'рабочий стол')
        self.apply_visibility()

    @Slot(str)
    def shortcut(self, action):
        if action == 'visibility':
            self.toggle_visibility()
        elif action == 'input':
            self.toggle_input()

    def toggle_visibility(self):
        self.hidden = not self.hidden
        self.apply_visibility()

    def start_shortcuts(self):
        if self.shortcut_client is not None:
            # Portal v1 cannot reopen its binding dialog. GNOME's application
            # settings edit bindings in place and notify our existing session.
            started, _pid = QProcess.startDetached(
                'gnome-control-center', ['applications', 'desktop-chibi.desktop'])
            self.shortcut_status = (
                'Открыты настройки Desktop Chibi в GNOME. Выберите «Сочетания клавиш» для изменения хоткеев.'
                if started else
                'Не удалось открыть настройки GNOME. Откройте «Настройки → Приложения → Desktop Chibi → Сочетания клавиш».')
            self.changed.emit()
            return
        if not sys.platform.startswith('linux'):
            self.shortcut_status = 'Системный портал хоткеев доступен в Linux.'
            self.changed.emit()
            return
        from .portal_shortcuts import PortalShortcuts
        parent = f'x11:{int(self.manager.owner.winId()):x}' if QApplication.platformName() == 'xcb' else ''
        client = PortalShortcuts(self, parent)
        self.shortcut_client = client
        client.packet.connect(self.receive_shortcut_packet)
        self.shortcut_status = 'Подключение системного портала хоткеев…'
        self.changed.emit()
        client.start()

    def receive_shortcut_packet(self, packet):
        if packet.get('action') in ('visibility', 'input'):
            self.shortcut(packet['action'])
        elif packet.get('status') == 'ready':
            bindings = packet.get('shortcuts', {})
            labels = {'visibility': 'Показ', 'input': 'Клики'}
            self.shortcut_status = 'Хоткеи: ' + '; '.join(
                labels[key] + ' — ' + (str(bindings[key]) or 'назначено системой')
                for key in labels if key in bindings)
            if not bindings:
                self.shortcut_status = 'Хоткеи не назначены. Нажмите «Настроить хоткеи…».'
            self.manager.owner.store.setValue('portal_shortcuts_enabled', True)
        elif packet.get('status') in ('error', 'configuring'):
            self.shortcut_status = packet.get('message', 'Портал хоткеев недоступен.')
        if packet.get('status') == 'error':
            self.close_shortcuts()
        self.changed.emit()

    def stop_shortcuts(self):
        self.manager.owner.store.setValue('portal_shortcuts_enabled', False)
        self.close_shortcuts()
        self.shortcut_status = 'Хоткеи выключены.'
        self.changed.emit()

    def close_shortcuts(self):
        client = self.shortcut_client
        self.shortcut_client = None
        if client is not None:
            client.stop()
            client.deleteLater()

    def toggle_input(self):
        self.input_override = not all(item.pet.input_passthrough() for item in self.manager.items)
        for item in self.manager.items:
            item.pet.apply_window_mode()
        self.changed.emit()

    def apply_visibility(self):
        for item in self.manager.items:
            item.pet.refresh_visibility()
        self.manager.scheduler.wake()
        self.changed.emit()

    def allows(self, pet):
        item = next((item for item in self.manager.items if item.pet is pet), None)
        return not self.hidden and (item is None or window_allowed(item.window_rule, self.active_app, self.available))

    def request_applications(self):
        self.call('ListApplications', lambda apps: self.applications.emit(apps if isinstance(apps, list) else []))

    def shutdown(self):
        self.close_shortcuts()
        self.timer.stop()
        self.bus.disconnect(SERVICE, PATH, INTERFACE, 'ActiveAppChanged', self, SLOT('active_changed(QString)'))
        for watcher in self.pending:
            watcher.finished.disconnect()
