import copy
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_TEST_ROOT = tempfile.TemporaryDirectory(prefix='chibi-easy-tests-')
os.environ['XDG_CONFIG_HOME'] = _TEST_ROOT.name + '/config'
os.environ['XDG_DATA_HOME'] = _TEST_ROOT.name + '/data'

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QDialog
from desktop_chibi.app import Settings
from desktop_chibi.animations import Animations
from desktop_chibi.guides import GuideDialog, GUIDES
from desktop_chibi.performance import PetClock
from desktop_chibi.pixel_ui import _bunny_frame, bunny_frame
from desktop_chibi.quick_setup import QuickSetup

APP = QApplication.instance() or QApplication([])


class BeginnerAndPerformanceTests(unittest.TestCase):
    def setUp(self):
        QSettings('DesktopChibi', 'DesktopChibi').clear()
        self.settings = Settings()
        self.manager = self.settings.manager
        self.hub = self.settings.event_hub
        self.settings.preview_timer.stop()

    def tearDown(self):
        self.settings.shutdown()
        self.settings.tray.hide()
        self.settings.hide()
        self.settings.deleteLater()
        APP.processEvents()
        QSettings('DesktopChibi', 'DesktopChibi').clear()

    def test_game_overlay_preserves_manual_visibility_and_restores_flags(self):
        from PySide6.QtCore import Qt
        first = self.manager.selected
        first.controller.values['layer'] = 2
        first.pet.setVisible(False)
        self.manager.load_active()
        self.manager.add()
        second = self.manager.selected
        self.settings.set_game_overlay_all(True)
        for item in self.manager.items:
            self.assertTrue(item.pet.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            self.assertTrue(item.pet.windowFlags() & Qt.WindowType.WindowTransparentForInput)
            self.assertFalse(item.pet.windowFlags() & Qt.WindowType.WindowStaysOnBottomHint)
        self.assertFalse(first.pet.requested_visible)
        self.settings.set_game_overlay_all(False)
        self.assertTrue(first.pet.windowFlags() & Qt.WindowType.WindowStaysOnBottomHint)
        self.assertFalse(second.pet.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        self.assertFalse(first.pet.requested_visible)

    def test_scheduler_sleeps_until_next_frame(self):
        pet = self.settings.pet
        self.manager.set_performance('eco')
        self.manager.scheduler.tick()
        self.assertGreaterEqual(self.manager.scheduler.timer.interval(), 90)
        self.assertLessEqual(self.manager.scheduler.timer.interval(), 100)

    def test_limit_32_and_shared_clock_and_restore(self):
        for _ in range(31):
            self.manager.add()
        self.manager.add()  # 33rd is rejected without modifying the selected pet
        self.assertEqual(len(self.manager.items), 32)
        self.assertEqual(len({item.identity for item in self.manager.items}), 32)
        self.assertEqual(self.manager.total_weight, 32)
        self.assertEqual(len(self.manager.scheduler.handles), 32)
        self.assertTrue(all(isinstance(item.pet.timer, PetClock) for item in self.manager.items))
        self.assertFalse(any(timer.isActive() for item in self.manager.items for timer in item.pet.findChildren(QTimer)))
        self.assertTrue(self.manager.scheduler.timer.isActive())
        self.assertFalse(self.settings.add_companion_button.isEnabled())
        identity = self.manager.selected.identity
        self.manager.set_mode(identity, 'functional')
        self.assertEqual(self.manager.total_weight, 34)
        self.manager.persist()
        restored = Settings()
        try:
            self.assertEqual(len(restored.manager.items), 32)
            self.assertEqual(restored.manager.find(identity).controller.mode, 'functional')
            self.assertEqual(restored.manager.total_weight, 34)
        finally:
            restored.shutdown()
            restored.tray.hide()
            restored.hide()
            restored.deleteLater()
            APP.processEvents()

    def test_adaptive_fps_and_soft_budget(self):
        pet = self.settings.pet
        self.assertEqual(self.manager.selected.controller.mode, 'decorative')
        self.assertEqual(self.manager.scheduler.fps(pet), 15)
        self.manager.set_performance('eco')
        self.assertEqual(self.manager.scheduler.fps(pet), 10)
        self.manager.set_mode(self.manager.selected.identity, 'functional')
        self.assertEqual(self.manager.scheduler.fps(pet), 20)
        self.manager.set_performance('smooth')
        self.assertEqual(self.manager.scheduler.fps(pet), 60)
        self.manager.set_performance('auto')
        self.assertEqual(self.manager.scheduler.fps(pet, crowded=True), 20)
        pet.motion.airborne = True
        self.assertEqual(self.manager.scheduler.fps(pet, crowded=True), 60)
        pet.motion.airborne = False
        self.settings.toggle_pet()
        self.assertEqual(self.manager.active_weight, 0)
        self.assertEqual(self.manager.total_weight, 3)

    def test_hidden_pet_skips_ticks_and_wakes_when_shown(self):
        pet = self.settings.pet
        pet.hide()
        with patch.object(pet, 'tick') as tick:
            self.manager.scheduler.tick()
            tick.assert_not_called()
            self.assertFalse(self.manager.scheduler.timer.isActive())
            pet.show()
            self.assertEqual(self.manager.scheduler.timer.interval(), 16)
            self.manager.scheduler.tick()
            tick.assert_called_once()

    def test_hidden_streaming_gif_stops_immediately_and_expired_event_is_not_replayed(self):
        from desktop_chibi.animations import release_frame_cache
        from test_imported_gif import write_gif
        from PySide6.QtGui import QMovie
        pet = self.settings.pet
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stream.gif'
            write_gif(path)
            release_frame_cache()
            with patch('desktop_chibi.animations._CACHE_LIMIT', 1):
                pet.load_character(str(path))
            pet.animations.frame('idle', 0)
            movie = pet.animations.clips['idle'].movie
            self.assertEqual(movie.state(), QMovie.MovieState.Running)
            pet.hide()
            self.assertEqual(movie.state(), QMovie.MovieState.NotRunning)
            pet.motion.airborne = True
            pet.trigger_notification('hidden', 'notify', 1, 10)
            self.assertNotIn('pending', pet.notifications['hidden'])
            self.manager.scheduler.tick()
            self.assertFalse(self.manager.scheduler.timer.isActive())
            pet.show()
            self.assertNotIn('hidden', pet.notifications)
            self.assertTrue(self.manager.scheduler.timer.isActive())
            # The live pet intentionally keeps its streaming reader while hidden.
            # Unload it before deleting the fixture directory on Windows.
            pet.load_character('')

    def test_cached_frames_are_shared_and_builtins_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'idle.png'
            image = QPixmap(32, 32)
            image.fill(QColor('pink'))
            image.save(str(path))
            first, second = Animations(), Animations()
            first.configure({'idle': {'file': str(path)}})
            second.configure({'idle': {'file': str(path), 'fps': 20}})
            self.assertIs(first.clips['idle'].frames, second.clips['idle'].frames)
            editable = first.clips['idle'].frames[0].toImage()
            editable.setPixelColor(1, 1, QColor('red'))
            self.assertEqual(second.clips['idle'].frames[0].toImage().pixelColor(1, 1), QColor('pink'))
            image.fill(QColor('blue'))
            image.save(str(path))
            changed = Animations()
            changed.configure({'idle': {'file': str(path)}})
            self.assertEqual(changed.clips['idle'].frames[0].toImage().pixelColor(1, 1), QColor('blue'))
            changed.configure({})
            first.configure({})
            second.configure({})
        before = _bunny_frame.cache_info().hits
        for _ in range(100):
            bunny_frame(elapsed=.1)
        self.assertGreaterEqual(_bunny_frame.cache_info().hits - before, 99)

    def test_clock_does_not_update_decorative_pet_at_60fps(self):
        pet = self.settings.pet
        self.manager.scheduler.timer.stop()
        clock = self.manager.scheduler.handles[pet]
        clock.last_run = 100.0
        now = [100.0]
        with patch('desktop_chibi.performance.time.monotonic', side_effect=lambda: now[0]), patch.object(pet, 'tick') as tick:
            for index in range(1, 201):
                now[0] = 100.0 + index * .016
                self.manager.scheduler.tick()
            self.assertGreaterEqual(tick.call_count, 46)
            self.assertLessEqual(tick.call_count, 49)

    def test_quick_setup_requires_consent_and_creates_two_code_rules(self):
        wizard = QuickSetup(self.settings, 'code')
        wizard.advance()
        wizard.advance()
        self.assertEqual(wizard.step, 2)
        self.assertFalse(self.hub.source('code')['enabled'])
        wizard.finish_setup()
        self.assertFalse(self.hub.source('code')['enabled'])
        self.assertTrue(wizard.error.text())
        wizard.consent.setChecked(True)
        with patch.object(self.hub, 'restart_adapters'):
            wizard.finish_setup()
        self.assertEqual(wizard.result(), QDialog.DialogCode.Accepted)
        self.assertTrue(self.hub.source('code')['enabled'])
        self.assertEqual({rule['event'] for rule in self.hub.rules}, {'code.done', 'code.failed'})
        self.assertEqual(self.manager.selected.controller.mode, 'functional')
        self.assertEqual(self.hub.dispatch('code', 'code.done'), 1)
        wizard.deleteLater()

    def test_decorative_mode_pauses_rules_without_losing_them(self):
        wizard = QuickSetup(self.settings, 'code')
        wizard.consent.setChecked(True)
        with patch.object(self.hub, 'restart_adapters'):
            wizard.finish_setup()
        identity = self.manager.selected.identity
        self.manager.set_mode(identity, 'decorative')
        self.assertEqual(self.hub.dispatch('code', 'code.done'), 0)
        self.assertTrue(self.hub.rules[0]['enabled'])
        with patch.object(self.hub, 'restart_adapters'):
            self.hub.configure(copy.deepcopy(self.hub.sources), copy.deepcopy(self.hub.rules))
        self.assertEqual(self.manager.selected.controller.mode, 'decorative')
        self.manager.set_mode(identity, 'functional')
        self.assertEqual(self.hub.dispatch('code', 'code.done'), 1)
        wizard.deleteLater()

    def test_new_decorative_pet_has_no_external_permissions(self):
        wizard = QuickSetup(self.settings)
        wizard.target.setCurrentIndex(0)
        wizard.name.setText('Просто сосед')
        wizard.advance()
        wizard.advance()
        wizard.finish_setup()
        self.assertEqual(len(self.manager.items), 2)
        self.assertEqual(self.manager.selected.name, 'Просто сосед')
        self.assertEqual(self.manager.selected.controller.mode, 'decorative')
        self.assertTrue(all(not source['enabled'] for source in self.hub.sources))
        wizard.deleteLater()

    def test_message_preset_only_adds_selected_app_and_cancel_is_safe(self):
        wizard = QuickSetup(self.settings, 'messages')
        wizard.fields['app'].setText('Telegram Desktop')
        wizard.advance()
        wizard.advance()
        self.assertIn('Telegram Desktop', wizard.review.text())
        wizard.reject()
        self.assertFalse(self.hub.source('notifications')['enabled'])
        self.assertFalse(self.hub.rules)
        wizard.deleteLater()
        accepted = QuickSetup(self.settings, 'messages')
        accepted.fields['app'].setText('Telegram Desktop')
        accepted.consent.setChecked(True)
        with patch.object(self.hub, 'restart_adapters'):
            accepted.finish_setup()
        self.assertEqual(self.hub.source('notifications')['apps'], ['Telegram Desktop'])
        self.assertEqual(self.hub.rules[0]['subject'], 'Telegram Desktop')
        accepted.deleteLater()

    def test_discord_guide_does_not_bypass_missing_credentials(self):
        wizard = QuickSetup(self.settings, 'discord')
        wizard.advance()
        wizard.advance()
        self.assertEqual(wizard.step, 1)
        self.assertTrue(wizard.error.text())
        self.assertFalse(self.hub.source('discord')['enabled'])
        wizard.reject()
        wizard.deleteLater()

    def test_copied_launcher_imports_from_other_directory(self):
        from desktop_chibi import emit
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(Path(emit.__file__).resolve()),
                                     '--source', 'code', '--event', 'code.done'],
                                    cwd=directory, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Источник выключен', result.stderr)
        self.assertNotIn('ImportError', result.stderr)

    def test_guide_search_and_hidden_preview(self):
        guide = GuideDialog(self.settings)
        guide.search.setText('вес')
        self.assertEqual(guide.topics.currentData(), 'performance')
        self.assertIn('32', guide.browser.toPlainText())
        self.assertGreaterEqual(len(GUIDES), 7)
        self.settings.show()
        APP.processEvents()
        self.assertFalse(self.settings.preview_timer.isActive())
        self.settings.settings_tabs.setCurrentIndex(1)
        self.assertTrue(self.settings.preview_timer.isActive())
        self.settings.settings_tabs.setCurrentIndex(2)
        self.assertFalse(self.settings.preview_timer.isActive())
        self.settings.settings_tabs.setCurrentIndex(1)
        self.assertTrue(self.settings.preview_timer.isActive())
        self.settings.hide()
        self.assertFalse(self.settings.preview_timer.isActive())
        guide.reject()
        guide.deleteLater()


if __name__ == '__main__':
    unittest.main()
