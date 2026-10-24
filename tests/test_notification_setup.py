import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_CONFIG = tempfile.TemporaryDirectory(prefix='chibi-notify-tests-')
os.environ['XDG_CONFIG_HOME'] = _CONFIG.name + '/config'
os.environ['XDG_DATA_HOME'] = _CONFIG.name + '/data'

from PySide6.QtCore import QObject, QSettings, QCoreApplication, QEvent
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication
from desktop_chibi.app import Settings
from desktop_chibi.event_adapters import NotificationAdapter
from desktop_chibi.event_ui import EventDialog
from desktop_chibi.notification_setup import NotificationPicker, installed_applications, suggest_app_name

APP = QApplication.instance() or QApplication([])


class FakeHub(QObject):
    def __init__(self):
        super().__init__()
        self.received = []
        self.status = []

    def dispose(self):
        self.deleteLater()
        QCoreApplication.sendPostedEvents(self, QEvent.Type.DeferredDelete)

    def dispatch(self, *args):
        self.received.append(args)

    def set_status(self, *args):
        self.status.append(args)


class NotificationSetupTests(unittest.TestCase):
    def test_desktop_and_executable_paths_are_only_suggestions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            apps = root / 'applications'
            apps.mkdir()
            executable = root / 'test program'
            executable.touch()
            desktop = apps / 'chibi-test.desktop'
            desktop.write_text(f'[Desktop Entry]\nType=Application\nName=Chibi Test Messenger\nExec="{executable}" %U\n')
            with patch.dict(os.environ, XDG_DATA_HOME=str(root), XDG_DATA_DIRS=str(root)):
                entries = installed_applications()
            self.assertEqual(suggest_app_name(str(executable), entries), 'Chibi Test Messenger')
            self.assertEqual(suggest_app_name(str(desktop), entries), 'Chibi Test Messenger')
            self.assertEqual(suggest_app_name(str(root / 'unknown-app'), entries), 'unknown-app')

    def test_discovery_requires_consent_and_stops(self):
        picker = NotificationPicker()
        picker.start_probe()
        self.assertIsNone(picker.probe)
        picker.disclosure.setChecked(True)
        with patch.object(NotificationAdapter, 'start'):
            picker.start_probe()
        self.assertTrue(picker.timeout.isActive())
        picker.probe.found.emit('Actual Messenger')
        picker.probe.found.emit('Actual Messenger')
        self.assertEqual(picker.found.count(), 1)
        picker.found.setCurrentRow(0)
        self.assertEqual(picker.name.text(), 'Actual Messenger')
        picker.disclosure.setChecked(False)
        self.assertIsNone(picker.probe)
        self.assertFalse(picker.timeout.isActive())
        picker.reject()
        picker.deleteLater()

    def test_discovery_only_emits_names_and_does_not_dispatch(self):
        hub = FakeHub()
        adapter = NotificationAdapter(hub, {'id': 'discovery', 'apps': []}, discovery=True)
        names = []
        adapter.found.connect(names.append)
        adapter.feed('method call x member=Notify\n   string "AyuGram"\n   uint32 0\n   string "PRIVATE TITLE"\n   string "PRIVATE BODY"\n')
        self.assertEqual(names, ['AyuGram'])
        self.assertFalse(hub.received)
        adapter.stop()
        hub.dispose()

    def test_utf8_split_across_process_reads(self):
        hub = FakeHub()
        adapter = NotificationAdapter(hub, {'id': 'notifications', 'apps': ['Мессенджер']})
        payload = 'method call x member=Notify\n   string "Мессенджер"\n'.encode()
        position = payload.index('М'.encode()) + 1
        with patch.object(adapter.process, 'readAllStandardOutput', side_effect=[payload[:position], payload[position:]]):
            adapter.read()
            adapter.read()
        self.assertEqual(hub.received, [('notifications', 'message.received', 'Мессенджер')])
        adapter.stop()
        hub.dispose()

    def test_forwarded_notification_is_one_event_but_new_messages_are_independent(self):
        hub = FakeHub()
        adapter = NotificationAdapter(hub, {'id': 'notifications', 'apps': ['Telegram Desktop']})
        def packet(sender, destination, serial):
            return (f'method call time=1 sender={sender} -> destination={destination} serial={serial} '
                    'path=/org/freedesktop/Notifications; interface=org.freedesktop.Notifications; member=Notify\n'
                    '   string "Telegram Desktop"\n   string "PRIVATE MESSAGE"\n')
        first = packet(':1.100', ':1.34', 9)
        adapter.feed(first)
        adapter.feed(packet(':1.34', ':1.24', 211))
        adapter.feed(first)  # duplicate delivery of the exact same D-Bus packet
        self.assertEqual(len(hub.received), 1)
        adapter.feed(packet(':1.100', ':1.34', 10))  # another real message, no time delay
        adapter.feed(packet(':1.34', ':1.24', 212))
        self.assertEqual(len(hub.received), 2)
        self.assertNotIn('PRIVATE', repr(adapter.recent_hops))
        adapter.stop()
        self.assertFalse(adapter.recent_hops)
        hub.dispose()

    def test_custom_texture_gets_notification_badge_but_dedicated_clip_is_preserved(self):
        QSettings('DesktopChibi', 'DesktopChibi').clear()
        settings = Settings()
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'idle.png'
                image = QPixmap(32, 32)
                image.fill(QColor('pink'))
                image.save(str(path))
                settings.pet.load_character(str(path))
                normal = settings.pet.current_frame('idle', .1).toImage()
                notify = settings.pet.current_frame('notify', .1).toImage()
                later = settings.pet.current_frame('notify', .3).toImage()
                self.assertNotEqual(normal, notify)
                self.assertNotEqual(notify, later)
                self.assertEqual(settings.pet.current_frame('idle', .1).toImage(), normal)
                settings.pet.load_character(str(path), {'idle': {'file': str(path)}, 'notify': {'file': str(path)}})
                self.assertEqual(settings.pet.current_frame('notify', .1).toImage(), normal)
        finally:
            settings.shutdown()
            settings.tray.hide()
            settings.hide()
            settings.deleteLater()
            APP.processEvents()
            QSettings('DesktopChibi', 'DesktopChibi').clear()

    def test_system_test_uses_saved_permissions_not_unsaved_checkbox(self):
        QSettings('DesktopChibi', 'DesktopChibi').clear()
        settings = Settings()
        dialog = EventDialog(settings.event_hub, settings)
        try:
            dialog.source_list.setCurrentRow(2)
            dialog.source_enabled.setChecked(True)
            dialog.source_fields['apps'].setText('Telegram Desktop')
            with patch('desktop_chibi.event_ui.send_test_notification') as send:
                dialog.test_notification()
                send.assert_not_called()
            self.assertIn('Сначала', dialog.source_status.text())
        finally:
            dialog.reject()
            dialog.deleteLater()
            settings.shutdown()
            settings.tray.hide()
            settings.hide()
            settings.deleteLater()
            APP.processEvents()
            QSettings('DesktopChibi', 'DesktopChibi').clear()


if __name__ == '__main__':
    unittest.main()
