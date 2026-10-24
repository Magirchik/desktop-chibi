import json
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_ROOT = tempfile.TemporaryDirectory(prefix='chibi-controls-tests-')
os.environ['XDG_CONFIG_HOME'] = _ROOT.name + '/config'
os.environ['XDG_DATA_HOME'] = _ROOT.name + '/data'

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication
from desktop_chibi.app import Settings
from desktop_chibi.desktop_controls import clean_window_rule, window_allowed

APP = QApplication.instance() or QApplication([])


class WindowRuleTests(unittest.TestCase):
    def test_modes_normalization_and_unavailable_bridge(self):
        self.assertTrue(window_allowed({}, '', False))
        only = {'mode': 'only', 'apps': ['Firefox.desktop']}
        self.assertTrue(window_allowed(only, 'firefox'))
        self.assertFalse(window_allowed(only, 'steam'))
        self.assertFalse(window_allowed(only, 'firefox', False))
        exclude = {'mode': 'except', 'apps': ['steam']}
        self.assertTrue(window_allowed(exclude, 'firefox'))
        self.assertFalse(window_allowed(exclude, 'steam'))
        self.assertFalse(window_allowed(exclude, 'firefox', False))
        self.assertFalse(window_allowed({'mode': 'only', 'apps': []}, 'firefox'))
        self.assertTrue(window_allowed({'mode': 'except', 'apps': []}, 'firefox'))
        self.assertEqual(clean_window_rule({'mode': 'invalid', 'apps': [None, 'A', 'a.desktop']}),
                         {'mode': 'all', 'apps': ['a']})


class DesktopControlsTests(unittest.TestCase):
    def setUp(self):
        QSettings('DesktopChibi', 'DesktopChibi').clear()
        self.settings = Settings()
        self.manager = self.settings.manager
        self.controls = self.settings.desktop_controls
        self.controls.timer.stop()
        self.settings.preview_timer.stop()
        self.controls.receive_state({'active': 'firefox'})

    def tearDown(self):
        self.settings.shutdown()
        self.settings.tray.hide()
        self.settings.hide()
        self.settings.deleteLater()
        APP.processEvents()
        QSettings('DesktopChibi', 'DesktopChibi').clear()

    def test_global_hide_keeps_manual_visibility_and_window_modes(self):
        first = self.manager.selected
        first.pet.setVisible(False)
        self.manager.add()
        second = self.manager.selected
        self.controls.toggle_visibility()
        self.assertFalse(first.pet.isVisible())
        self.assertFalse(second.pet.isVisible())
        self.assertTrue(second.pet.requested_visible)
        second.pet.apply_window_mode()
        self.assertFalse(second.pet.isVisible())
        self.controls.toggle_visibility()
        self.assertFalse(first.pet.isVisible())
        self.assertTrue(second.pet.isVisible())

    def test_interactive_override_works_in_overlay_and_for_new_companions(self):
        self.settings.game_overlay.setChecked(True)
        pet = self.manager.selected.pet
        self.assertTrue(pet.input_passthrough())
        self.controls.toggle_input()
        self.assertFalse(pet.input_passthrough())
        self.assertFalse(pet.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        self.assertTrue(pet.option('game_overlay'))
        self.controls.toggle_input()
        self.assertTrue(pet.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        self.manager.add()
        self.assertTrue(self.manager.selected.pet.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        self.assertFalse(self.manager.selected.controller.values['click_through'])

    def test_window_rule_preserves_manual_visibility_and_presence(self):
        pet = self.manager.selected.pet
        self.settings.window_rule_apps.setPlainText('firefox')
        self.settings.window_rule_mode.setCurrentIndex(1)
        self.assertTrue(pet.isVisible())
        self.controls.active_changed('steam')
        self.assertFalse(pet.isVisible())
        self.assertTrue(pet.requested_visible)
        self.controls.toggle_visibility()
        self.controls.active_changed('firefox')
        self.assertFalse(pet.isVisible())
        self.controls.toggle_visibility()
        self.assertTrue(pet.isVisible())
        self.manager.selected.controller.values['hide_when_absent'] = True
        pet.set_friend_present(False)
        self.controls.apply_visibility()
        self.assertFalse(pet.isVisible())
        pet.set_friend_present(True)
        self.assertTrue(pet.isVisible())
        self.controls.active_changed('steam')
        pet.set_friend_present(True)
        self.assertFalse(pet.isVisible())

    def test_window_rules_persist_per_companion_new_and_reset_are_unrestricted(self):
        first = self.manager.selected
        self.settings.window_rule_apps.setPlainText('firefox\nsteam')
        self.settings.window_rule_mode.setCurrentIndex(2)
        self.manager.persist()
        saved = json.loads(self.settings.store.value('companions_v1'))
        self.assertEqual(saved[0]['window_rule'], {'mode': 'except', 'apps': ['firefox', 'steam']})
        self.manager.add()
        self.assertEqual(self.manager.selected.window_rule, {'mode': 'all', 'apps': []})
        self.manager.select_id(first.identity)
        self.assertEqual(self.settings.window_rule_apps.toPlainText(), 'firefox\nsteam')
        self.manager.reset_settings()
        self.assertEqual(first.window_rule, {'mode': 'all', 'apps': []})
        self.assertTrue(first.pet.isVisible())

    def test_portal_packets_activate_controls_and_save_enablement(self):
        self.controls.receive_shortcut_packet({'status': 'ready', 'shortcuts': {'visibility': 'F9', 'input': 'F10'}})
        self.assertTrue(self.settings.store.value('portal_shortcuts_enabled', False, type=bool))
        self.controls.receive_shortcut_packet({'action': 'visibility'})
        self.assertTrue(self.controls.hidden)
        self.controls.receive_shortcut_packet({'action': 'input'})
        self.assertTrue(self.controls.input_override)
        self.controls.stop_shortcuts()
        self.assertFalse(self.settings.store.value('portal_shortcuts_enabled', True, type=bool))

    def test_repeated_configure_opens_settings_without_replacing_shortcut_session(self):
        session = object()
        self.controls.shortcut_client = session
        try:
            with patch('desktop_chibi.desktop_controls.QProcess.startDetached', return_value=(True, 123)) as launch:
                self.controls.start_shortcuts()
                self.controls.start_shortcuts()
            self.assertEqual(launch.call_count, 2)
            launch.assert_called_with('gnome-control-center', ['applications', 'desktop-chibi.desktop'])
            self.assertIs(self.controls.shortcut_client, session)
            self.assertIn('Открыты настройки', self.controls.shortcut_status)
            self.controls.receive_shortcut_packet({'status': 'ready', 'shortcuts': {'input': 'Ctrl+F8'}})
            self.assertIn('Ctrl+F8', self.controls.shortcut_status)
        finally:
            self.controls.shortcut_client = None

    def test_repeated_configure_reports_settings_launch_failure_and_keeps_session(self):
        session = object()
        self.controls.shortcut_client = session
        try:
            with patch('desktop_chibi.desktop_controls.QProcess.startDetached', return_value=(False, 0)):
                self.controls.start_shortcuts()
            self.assertIs(self.controls.shortcut_client, session)
            self.assertIn('Не удалось открыть', self.controls.shortcut_status)
        finally:
            self.controls.shortcut_client = None

    def test_start_and_stop_use_in_process_client_without_spawning_worker(self):
        from desktop_chibi.portal_shortcuts import PortalShortcuts
        with patch.object(PortalShortcuts, 'start') as start, \
                patch('desktop_chibi.desktop_controls.QProcess.start') as spawn:
            self.controls.start_shortcuts()
        client = self.controls.shortcut_client
        self.assertIsInstance(client, PortalShortcuts)
        start.assert_called_once()
        spawn.assert_not_called()
        self.controls.stop_shortcuts()
        self.assertIsNone(self.controls.shortcut_client)
        self.assertTrue(client.stopped)

    def test_portal_error_releases_client_and_allows_retry(self):
        from desktop_chibi.portal_shortcuts import PortalShortcuts
        with patch.object(PortalShortcuts, 'start'):
            self.controls.start_shortcuts()
            failed_client = self.controls.shortcut_client
            failed_client.fail('Permission denied')
            self.assertIsNone(self.controls.shortcut_client)
            self.assertTrue(failed_client.stopped)
            self.assertEqual(self.controls.shortcut_status, 'Permission denied')
            self.controls.start_shortcuts()
            self.assertIsNot(self.controls.shortcut_client, failed_client)

    def test_selected_status_explains_global_rule_and_manual_hiding(self):
        self.assertEqual(self.settings.status.text(), 'Виден')
        self.controls.toggle_visibility()
        self.assertEqual(self.settings.status.text(), 'Пауза')
        self.assertIn('общим переключателем', self.settings.visibility_reason.text())
        self.controls.toggle_visibility()
        self.settings.window_rule_apps.setPlainText('steam')
        self.settings.window_rule_mode.setCurrentIndex(1)
        self.assertEqual(self.settings.status.text(), 'Ожидание')
        self.assertIn('правилом', self.settings.visibility_reason.text())
        self.controls.active_changed('steam')
        self.assertEqual(self.settings.status.text(), 'Виден')
        self.settings.toggle_pet()
        self.assertEqual(self.settings.status.text(), 'Скрыт')
        self.assertIn('Вы скрыли', self.settings.visibility_reason.text())

    def test_status_card_does_not_interrupt_event_animation(self):
        from desktop_chibi.animations import default_notification
        pet = self.settings.pet
        pet.animations.configure({'notify': default_notification()})
        pet.animations.frame('notify', .1)
        self.settings.sync_visibility()
        self.assertEqual(pet.animations.active, 'notify')
        self.assertFalse(self.settings.selected_thumbnail.pixmap().isNull())

    def test_rename_button_updates_selected_companion_only_and_cancel_keeps_name(self):
        first = self.manager.selected
        self.manager.add()
        second = self.manager.selected
        with patch('desktop_chibi.app.QInputDialog.getText', return_value=('Луна', True)):
            self.settings.rename_companion_button.click()
        self.assertEqual(second.name, 'Луна')
        self.assertEqual(first.name, 'Чибик 1')
        self.assertEqual(self.settings.companion_select.currentText(), 'Луна')
        self.assertIn('Луна', self.settings.character_label.text())
        with patch('desktop_chibi.app.QInputDialog.getText', return_value=('Другое имя', False)):
            self.settings.rename_companion_button.click()
        self.assertEqual(second.name, 'Луна')

    def test_mouse_hint_follows_global_input_override(self):
        self.assertIn('левой кнопкой', self.settings.interaction_hint.text())
        self.controls.toggle_input()
        self.assertIn('«Управление»', self.settings.interaction_hint.text())
        self.assertEqual(self.settings.toggle_input_button.text(), 'Разрешить перетаскивание')
        self.controls.toggle_input()
        self.assertIn('левой кнопкой', self.settings.interaction_hint.text())


if __name__ == '__main__':
    unittest.main()
