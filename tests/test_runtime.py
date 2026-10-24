import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_TEST_CONFIG = tempfile.TemporaryDirectory(prefix='chibi-tests-')
os.environ['XDG_CONFIG_HOME'] = _TEST_CONFIG.name

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPixmap, QPainter
from PySide6.QtWidgets import QApplication, QDialog
from desktop_chibi.animations import Animations, read_manifest
from desktop_chibi.animation_editor import AnimationEditor
from desktop_chibi.app import Settings
from desktop_chibi.physics import Motion

APP = QApplication.instance() or QApplication([])


class PhysicsTests(unittest.TestCase):
    def test_acceleration_and_single_impact(self):
        motion = Motion(x=-40, y=0)
        self.assertFalse(motion.step(.1, 0, 200, 0, 300, 1000))
        self.assertAlmostEqual(motion.y, 5)
        self.assertAlmostEqual(motion.vy, 100)
        self.assertEqual(motion.x, 0)
        impacts = 0
        for _ in range(100):
            impacts += motion.step(.02, 0, 200, 0, 300, 1000)
        self.assertEqual(impacts, 1)
        self.assertEqual(motion.y, 300)
        self.assertEqual(motion.vy, 0)
        self.assertFalse(motion.airborne)

    def test_disabled_gravity_and_floor_changes(self):
        motion = Motion(x=300, y=0, airborne=True)
        motion.step(.02, 0, 100, 0, 200, 1000, enabled=False)
        self.assertEqual((motion.x, motion.y, motion.vy), (100, 200, 0))
        motion.step(.1, 0, 100, 0, 400, 1000)
        self.assertTrue(motion.airborne)
        self.assertGreater(motion.y, 200)
        motion.step(.02, 0, 100, 0, 100, 1000)
        self.assertEqual(motion.y, 100)


class AnimationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        image = QPixmap(64, 32)
        image.fill(QColor('red'))
        p = QPainter(image)
        p.fillRect(32, 0, 32, 32, QColor('blue'))
        p.end()
        self.sheet = self.root / 'walk.png'
        image.save(str(self.sheet))
        self.spec = {'file': str(self.sheet), 'width': 32, 'height': 32, 'frames': 2, 'fps': 10}
        self.player = Animations()

    def tearDown(self):
        self.player.configure({})
        self.temp.cleanup()

    def test_sheet_timing_fallback_and_transactional_validation(self):
        self.player.configure({'idle': self.spec})
        self.assertEqual(self.player.frame('walk', 0).toImage().pixelColor(0, 0), QColor('red'))
        self.assertEqual(self.player.frame('walk', .11).toImage().pixelColor(0, 0), QColor('blue'))
        self.assertEqual(self.player.frame('walk', .21).toImage().pixelColor(0, 0), QColor('red'))
        with self.assertRaises(ValueError):
            self.player.configure({'walk': {**self.spec, 'frames': 3}})
        self.assertIn('idle', self.player.clips)
        self.assertAlmostEqual(self.player.duration('idle', 1), .2)
        self.player.configure({'land': self.spec})
        self.assertEqual(self.player.frame('land', 1).toImage().pixelColor(0, 0), QColor('blue'))

    def test_gif_and_invalid_files(self):
        gif = self.root / 'idle.gif'
        gif.write_bytes(base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'))
        self.player.configure({'idle': {'file': str(gif)}})
        self.assertFalse(self.player.frame('idle', 0).isNull())
        for spec in [{'file': 'missing.png'}, {**self.spec, 'width': 7}, {**self.spec, 'fps': 0}]:
            with self.assertRaises(ValueError):
                self.player.configure({'idle': spec})
        self.assertIn('idle', self.player.clips)

    def test_portable_export_import(self):
        editor = AnimationEditor({'idle': self.spec, 'walk': self.spec})
        target = self.root / 'my-chibi.json'
        with patch('desktop_chibi.animation_editor.QFileDialog.getSaveFileName', return_value=(str(target), '')):
            editor.export_pack()
        self.assertTrue(target.is_file())
        data = json.loads(target.read_text())
        self.assertFalse(Path(data['animations']['walk']['file']).is_absolute())
        name, specs = read_manifest(target)
        self.player.configure(specs)
        self.assertEqual(name, 'my-chibi')
        self.assertEqual(len(self.player.clips['walk'].frames), 2)
        editor.reject()
        editor.deleteLater()

    def test_manifest_validation(self):
        target = self.root / 'bad.json'
        for contents in ['[]', '{}', '{"version": 1, "animations": {"idle": {}}}']:
            target.write_text(contents)
            with self.assertRaises(ValueError):
                read_manifest(target)

    def test_delete_blank_and_assigned_idle_without_losing_other_clips(self):
        original = {'idle': self.spec, 'walk': self.spec, 'idle_1': self.spec}
        editor = AnimationEditor(original)
        editor.add_idle()
        self.assertEqual(editor.current, 'idle_2')
        self.assertTrue(editor.delete_button.isEnabled())
        editor.delete_button.click()
        self.assertEqual(editor.state.findData('idle_2'), -1)
        editor.state.setCurrentIndex(editor.state.findData('idle_1'))
        editor.delete_button.click()
        self.assertEqual(editor.state.findData('idle_1'), -1)
        self.assertNotIn('idle_1', editor.specs)
        self.assertEqual(set(editor.specs), {'idle', 'walk'})
        self.assertIn('idle_1', original)  # dialog changes are still a draft
        editor.apply()
        self.assertEqual(editor.result(), QDialog.DialogCode.Accepted)
        self.player.configure(editor.specs)
        self.assertEqual(set(self.player.clips), {'idle', 'walk'})
        editor.deleteLater()

    def test_delete_base_animation_keeps_slot_and_reset_restores_source(self):
        defaults = {'idle': self.spec, 'walk': self.spec}
        editor = AnimationEditor({**defaults, 'idle_1': self.spec}, default_specs=defaults)
        editor.state.setCurrentIndex(editor.state.findData('walk'))
        editor.delete_button.click()
        self.assertNotIn('walk', editor.specs)
        self.assertGreaterEqual(editor.state.findData('walk'), 0)
        self.assertFalse(editor.delete_button.isEnabled())
        editor.reset_pack()
        self.assertEqual(editor.specs, defaults)
        self.assertEqual(editor.state.findData('idle_1'), -1)
        self.assertEqual(editor.current, 'idle')
        editor.reject()
        editor.deleteLater()


class PetTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()
        self.pet = self.settings.pet
        self.pet.timer.stop()
        self.settings.preview_timer.stop()
        self.settings.store.clear()
        self.settings.game_overlay.setChecked(False)
        self.settings.pinned.setChecked(False)
        self.settings.area_enabled.setChecked(False)
        self.settings.click_through.setChecked(False)
        self.settings.walk.setChecked(True)
        self.settings.gravity_enabled.setChecked(True)
        self.settings.random_idle.setChecked(False)
        self.pet.place(on_floor=True)

    def tearDown(self):
        self.pet.hide()
        self.settings.tray.hide()
        self.settings.hide()
        self.settings.deleteLater()
        APP.processEvents()

    def event(self, x, y):
        return SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton,
                               globalPosition=lambda: QPointF(x, y))

    def test_pinned_position_notifications_and_input(self):
        self.settings.pinned.setChecked(True)
        self.pet.motion.y = self.pet.bounds().top() + 25
        self.pet.place()
        position = (self.pet.motion.x, self.pet.motion.y)
        self.pet.advance(.1, time.monotonic())
        self.assertEqual((self.pet.motion.x, self.pet.motion.y), position)
        self.assertFalse(self.pet.motion.airborne)
        self.pet.trigger_notification('test', 'notify', 5, time.monotonic())
        self.pet.advance(.1, time.monotonic())
        self.assertEqual(self.pet.state, 'notify')
        self.assertEqual((self.pet.motion.x, self.pet.motion.y), position)
        self.settings.click_through.setChecked(True)
        self.assertTrue(self.pet.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        self.settings.click_through.setChecked(False)
        self.assertFalse(self.pet.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        self.settings.manager.persist()
        records = json.loads(self.settings.store.value('companions_v1'))
        self.assertEqual(records[0]['y'], position[1])

    def test_walking_area_clamps_and_normalizes_edges(self):
        original = self.pet.bounds()
        self.settings.area_left.setValue(75)
        self.settings.area_right.setValue(25)
        self.settings.area_top.setValue(20)
        self.settings.area_bottom.setValue(80)
        self.settings.area_enabled.setChecked(True)
        area = self.pet.bounds()
        self.assertLess(area.width(), original.width())
        self.assertTrue(original.contains(area))
        self.pet.motion.x = original.right() + 100
        self.pet.place(on_floor=True)
        self.assertLessEqual(self.pet.x() + self.pet.width(), area.right() + 1)
        self.assertEqual(self.pet.y(), self.pet.floor())

    def test_drag_fall_land_walk_and_interrupt(self):
        pet = self.pet
        initial_y = pet.y()
        pet.mousePressEvent(self.event(pet.x() + 10, initial_y + 10))
        pet.mouseMoveEvent(self.event(pet.x() + 10, initial_y - 190))
        self.assertEqual(pet.state, 'drag')
        self.assertLess(pet.motion.y, pet.floor())
        released_y = pet.motion.y
        pet.mouseReleaseEvent(self.event(pet.x() + 10, pet.y() + 10))
        self.assertEqual(pet.state, 'fall')
        self.assertEqual(pet.motion.y, released_y)  # no snap to floor
        now = time.monotonic()
        for _ in range(200):
            now += .016
            pet.advance(.016, now)
            if pet.state == 'land':
                break
        self.assertEqual(pet.state, 'land')
        self.assertEqual(pet.motion.y, pet.floor())
        self.assertEqual(pet.motion.vy, 0)
        pet.advance(.016, now + .1)
        self.assertEqual(pet.state, 'land')
        with patch('desktop_chibi.app.random.random', return_value=1):
            pet.advance(.016, now + 1)
        self.assertEqual(pet.state, 'walk')
        pet.motion.y -= 100
        pet.advance(.016, now + 1.1)
        self.assertEqual(pet.state, 'fall')
        pet.mousePressEvent(self.event(pet.x() + 10, pet.y() + 10))
        self.assertEqual(pet.state, 'drag')
        self.assertEqual(pet.motion.vy, 0)

    def test_idle_variants_and_saved_controls(self):
        settings = self.settings
        settings.walk.setChecked(False)
        settings.gravity.setValue(800)
        settings.idle_duration.setValue(5)
        settings.save()
        self.assertEqual(settings.store.value('gravity', type=int), 800)
        settings.pet.advance(.016, time.monotonic())
        self.assertTrue(settings.pet.state.startswith('idle'))
        settings.visibility_button.click()
        self.assertFalse(settings.pet.isVisible())
        self.assertEqual(settings.visibility_button.text(), 'Показать чибика')
        settings.visibility_button.click()
        self.assertTrue(settings.pet.isVisible())

    def test_animation_profiles_persist_separately(self):
        settings = self.settings
        target = Path(_TEST_CONFIG.name) / 'custom.png'
        image = QPixmap(32, 32)
        image.fill(QColor('pink'))
        image.save(str(target))
        settings.set_character(str(target))
        specs = {'idle': {'file': str(target)}, 'fall': {'file': str(target)}}
        settings.pet.load_character(str(target), specs)
        settings.store.setValue(settings.profile_key(str(target)), json.dumps(specs))
        settings.reset_character()
        self.assertEqual(settings.pet.animations.specs, {})
        settings.set_character(str(target))
        self.assertIn('fall', settings.pet.animations.clips)

    def test_short_idle_clip_does_not_extend_pause_forever(self):
        target = Path(_TEST_CONFIG.name) / 'short-idle.png'
        image = QPixmap(32, 32)
        image.fill(QColor('pink'))
        image.save(str(target))
        self.pet.load_character(str(target), {'idle': {'file': str(target), 'fps': 10}})
        now = time.monotonic()
        self.pet.begin_idle(now)
        deadline = self.pet.pause_until
        with patch('desktop_chibi.app.random.random', return_value=1):
            for _ in range(300):
                now += .02
                self.pet.advance(.02, now)
                if self.pet.state == 'walk':
                    break
        self.assertEqual(self.pet.state, 'walk')
        self.assertLessEqual(now, deadline + .03)

    def test_reset_settings_restores_and_saves_defaults_preserving_character(self):
        settings = self.settings
        target = Path(_TEST_CONFIG.name) / 'keep-me.png'
        image = QPixmap(32, 32)
        image.fill(QColor('pink'))
        image.save(str(target))
        settings.set_character(str(target))
        original = dict(settings.pet.animations.specs)
        for slider in (settings.pet_size, settings.speed, settings.offset, settings.gravity,
                       settings.idle_interval, settings.idle_duration):
            slider.setValue(slider.maximum())
        for checkbox in (settings.walk, settings.gravity_enabled, settings.random_idle):
            checkbox.setChecked(False)
        settings.layer.setCurrentIndex(2)
        settings.reset_settings_button.click()
        for key, expected in {'size': 128, 'speed': 45, 'offset': 0, 'gravity': 1200,
                              'idle_interval': 14, 'idle_duration': 3, 'screen': 0, 'layer': 0}.items():
            self.assertEqual(settings.store.value(key, type=int), expected)
        for key in ('walk', 'gravity_enabled', 'random_idle'):
            self.assertTrue(getattr(settings, key).isChecked())
            self.assertTrue(settings.store.value(key, type=bool))
        self.assertEqual(settings.character_path, str(target))
        self.assertEqual(settings.pet.animations.specs, original)
        self.assertEqual(settings.pet.motion.y, settings.pet.floor())


if __name__ == '__main__':
    unittest.main()
