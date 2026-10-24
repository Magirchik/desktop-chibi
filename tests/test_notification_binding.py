import copy
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_ROOT = tempfile.TemporaryDirectory(prefix='chibi-binding-tests-')
os.environ['XDG_CONFIG_HOME'] = _ROOT.name + '/config'
os.environ['XDG_DATA_HOME'] = _ROOT.name + '/data'
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog
from desktop_chibi.app import Settings
from desktop_chibi.event_ui import EventDialog
from desktop_chibi.quick_setup import QuickSetup
from desktop_chibi.event_adapters import NotificationAdapter

APP = QApplication.instance() or QApplication([])


class NotificationBindingTests(unittest.TestCase):
    def setUp(self):
        QSettings('DesktopChibi', 'DesktopChibi').clear()
        self.settings = Settings()
        self.hub = self.settings.event_hub
        self.manager = self.settings.manager
        source = self.hub.source('notifications')
        source.update(enabled=True, apps=['Telegram Desktop'])
        self.original = {'id': 'firefox-listener', 'enabled': True, 'source': 'custom',
                         'event': 'voice.speaking', 'target': self.manager.selected.identity,
                         'animation': 'notify', 'subject': 'firefox', 'mode': 'hold', 'duration': 60, 'cooldown': 0}
        self.hub.rules = [copy.deepcopy(self.original)]

    def tearDown(self):
        self.settings.shutdown()
        self.settings.tray.hide()
        self.settings.hide()
        self.settings.deleteLater()
        APP.processEvents()
        QSettings('DesktopChibi', 'DesktopChibi').clear()

    def test_guided_binding_preserves_existing_rules_and_targets_selected_pet(self):
        target = self.manager.selected
        wizard = QuickSetup(self.settings, 'messages')
        self.assertEqual(wizard.fields['app'].text(), 'Telegram Desktop')
        wizard.notification_animation.setCurrentIndex(wizard.notification_animation.findData('success'))
        wizard.advance()
        wizard.advance()
        self.assertIn('Готово', wizard.review.text())
        wizard.consent.setChecked(True)
        with patch.object(NotificationAdapter, 'start'):
            wizard.finish_setup()
        self.assertEqual(wizard.result(), QDialog.DialogCode.Accepted)
        self.assertIn(self.original, self.hub.rules)
        rule = next(r for r in self.hub.rules if r['source'] == 'notifications')
        self.assertEqual(rule['event'], 'message.received')
        self.assertEqual(rule['target'], target.identity)
        self.assertEqual(rule['animation'], 'success')
        self.assertEqual(rule['mode'], 'pulse')
        self.assertEqual(self.hub.dispatch('notifications', 'message.received', 'Telegram Desktop'), 1)
        self.assertEqual(target.pet.state, 'success')
        self.assertIn('Telegram Desktop', self.settings.notification_binding.text())
        wizard.deleteLater()

    def test_changing_animation_updates_binding_without_duplicate_rules(self):
        first = QuickSetup(self.settings, 'messages')
        self.hub.sources, self.hub.rules = first.prepare(self.manager.selected.identity)
        second = QuickSetup(self.settings, 'messages')
        second.notification_animation.setCurrentIndex(second.notification_animation.findData('error'))
        _, rules = second.prepare(self.manager.selected.identity)
        self.assertEqual(len(rules), 2)
        self.assertIn(self.original, rules)
        self.assertEqual(next(r for r in rules if r['source'] == 'notifications')['animation'], 'error')
        first.reject(); second.reject()
        first.deleteLater(); second.deleteLater()

    def test_direct_button_opens_application_and_animation_step_without_changing_config_on_cancel(self):
        self.settings.refresh_notification_binding()
        self.assertIn('не привязаны', self.settings.notification_binding.text())
        original = copy.deepcopy(self.hub.rules)
        def inspect(wizard):
            self.assertEqual(wizard.scenario.currentData(), 'messages')
            self.assertEqual(wizard.step, 1)
            self.assertEqual(wizard.target.currentData(), self.manager.selected.identity)
            self.assertEqual(wizard.notification_animation.currentData(), 'notify')
            return QDialog.DialogCode.Rejected
        with patch.object(QuickSetup, 'exec', inspect):
            self.settings.bind_notifications_button.click()
        self.assertEqual(self.hub.rules, original)

    def test_advanced_rule_explains_local_events_and_new_rule_uses_selected_notification_source(self):
        dialog = EventDialog(self.hub)
        self.assertIn('локальный канал', dialog.rule_explanation.text())
        self.assertIn('Telegram', dialog.rule_explanation.text())
        index = next(i for i, source in enumerate(dialog.sources) if source['id'] == 'notifications')
        dialog.source_list.setCurrentRow(index)
        dialog.add_rule()
        rule = dialog.rules[-1]
        self.assertEqual(rule['source'], 'notifications')
        self.assertEqual(rule['event'], 'message.received')
        self.assertEqual(rule['subject'], 'Telegram Desktop')
        dialog.reject()
        dialog.deleteLater()


if __name__ == '__main__':
    unittest.main()
