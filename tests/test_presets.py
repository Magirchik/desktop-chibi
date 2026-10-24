import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_ROOT = tempfile.TemporaryDirectory(prefix='chibi-preset-tests-')
os.environ['XDG_CONFIG_HOME'] = _ROOT.name + '/config'
os.environ['XDG_DATA_HOME'] = _ROOT.name + '/data'
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QMenu
from desktop_chibi.app import Settings
from desktop_chibi.pixel_editor import PixelDocument
from desktop_chibi.presets import PresetLibrary

APP = QApplication.instance() or QApplication([])


class PresetTests(unittest.TestCase):
    def setUp(self):
        QSettings('DesktopChibi', 'DesktopChibi').clear()
        self.temp = tempfile.TemporaryDirectory(prefix='chibi-preset-source-')
        self.settings = Settings()
        self.manager = self.settings.manager
        self.library = self.settings.presets
        self.settings.preview_timer.stop()

    def tearDown(self):
        self.settings.shutdown()
        self.settings.tray.hide()
        self.settings.hide()
        self.settings.deleteLater()
        APP.processEvents()
        QSettings('DesktopChibi', 'DesktopChibi').clear()
        self.temp.cleanup()

    def draw(self, color='red'):
        doc = PixelDocument(width=8, height=8)
        doc.line((1, 1), (1, 1), QColor(color))
        doc.add_layer(True)
        doc.line((2, 2), (2, 2), QColor('pink'))
        path = Path(self.temp.name) / (color + '.png')
        project = path.with_suffix('.pixel.json')
        doc.sheet().save(str(path))
        doc.save_project(project)
        self.settings.pet.load_character(str(path), {'idle': {'file': str(path), 'project': str(project)}})
        self.settings.character_path = str(path)
        self.manager.persist()
        return path, project

    def test_new_companion_uses_defaults_instead_of_selected_preset(self):
        from desktop_chibi.app import DEFAULT_SETTINGS
        path, _ = self.draw()
        self.settings.pet_size.setValue(240)
        self.settings.game_overlay.setChecked(True)
        self.manager.persist()
        old = self.manager.selected
        before = dict(old.controller.values)
        self.manager.add()
        new = self.manager.selected
        self.assertEqual(new.controller.values, DEFAULT_SETTINGS)
        self.assertEqual(new.path, '')
        self.assertEqual(new.pet.animations.specs, {})
        self.assertEqual(new.controller.mode, 'decorative')
        self.assertEqual(old.path, str(path))
        self.assertEqual(old.controller.values, before)
        new.controller.values['speed'] = 77
        self.assertEqual(old.controller.values, before)
        self.assertEqual(DEFAULT_SETTINGS['speed'], 45)

    def test_reset_button_preserves_art_and_other_companions_and_persists(self):
        from desktop_chibi.app import DEFAULT_SETTINGS
        self.manager.add()
        path, _ = self.draw()
        item = self.manager.selected
        specs = dict(item.pet.animations.specs)
        self.settings.pet_size.setValue(240)
        self.settings.game_overlay.setChecked(True)
        self.manager.persist()
        other = dict(self.manager.items[0].controller.values)
        self.settings.reset_companion_button.click()
        self.assertEqual(item.controller.values, DEFAULT_SETTINGS)
        self.assertEqual(item.path, str(path))
        self.assertEqual(item.pet.animations.specs, specs)
        self.assertEqual(self.manager.items[0].controller.values, other)
        saved = json.loads(self.settings.store.value('companions_v1'))
        record = next(c for c in saved if c['id'] == item.identity)
        self.assertEqual(record['settings'], DEFAULT_SETTINGS)
        self.assertEqual(record['character'], str(path))

    def test_snapshot_copies_assets_and_layers_survives_source_removal_and_reload(self):
        source, project = self.draw()
        identity = self.library.save('Красный')
        entry = self.library.find(identity)
        manifest = json.loads(Path(entry['manifest']).read_text())
        self.assertFalse(Path(manifest['animations']['idle']['file']).is_absolute())
        source.unlink()
        project.unlink()
        reloaded = PresetLibrary(self.settings)
        self.assertEqual(reloaded.find(identity)['name'], 'Красный')
        reloaded.apply(identity)
        pet = self.settings.pet
        self.assertEqual(pet.current_frame('idle', 0).toImage().pixelColor(1, 1), QColor('red'))
        layered = PixelDocument.load_project(pet.animations.specs['idle']['project'])
        self.assertEqual(len(layered.layers), 2)
        self.assertTrue(layered.layer['shared'])
        self.assertEqual(self.settings.store.value('character'), entry['manifest'])

    def test_swap_preserves_slot_rules_mode_visibility_other_pet_and_optional_settings(self):
        self.draw()
        self.settings.speed.setValue(77)
        identity = self.library.save('Красный')
        first = self.manager.selected
        self.manager.set_mode(first.identity, 'functional')
        self.manager.add()
        second = self.manager.selected
        self.settings.speed.setValue(23)
        first.pet.hide()
        hub = self.settings.event_hub
        rule = {'id': 'preset-rule', 'enabled': True, 'source': 'notifications', 'event': 'message.received',
                'target': first.identity, 'animation': 'notify', 'subject': 'Telegram Desktop',
                'mode': 'pulse', 'duration': 4, 'cooldown': 2}
        hub.rules = [rule]
        original = json.dumps(hub.rules)
        self.library.apply(identity, target=first.identity, with_settings=False)
        self.assertEqual(self.manager.selected.identity, second.identity)
        self.assertEqual(second.controller.values['speed'], 23)
        self.assertEqual(first.identity, rule['target'])
        self.assertEqual(first.controller.mode, 'functional')
        self.assertFalse(first.pet.isVisible())
        self.assertEqual(json.dumps(hub.rules), original)
        self.library.apply(identity, with_settings=False)
        self.assertEqual(second.controller.values['speed'], 23)
        self.library.apply(identity, with_settings=True)
        self.assertEqual(second.controller.values['speed'], 77)
        self.assertEqual(second.controller.mode, 'decorative')
        self.assertEqual(len(self.manager.items), 2)

    def test_update_rename_delete_keep_active_snapshot(self):
        self.draw()
        identity = self.library.save('Первый')
        self.library.apply(identity)
        old_spec = dict(self.settings.pet.animations.specs['idle'])
        old_manifest = self.library.find(identity)['manifest']
        self.library.rename(identity, 'Новое имя')
        self.assertEqual(self.library.find(identity)['name'], 'Новое имя')
        self.library.save('Обновлённый', replace=identity)
        self.assertEqual(len(self.library.entries), 1)
        self.assertNotEqual(self.library.find(identity)['manifest'], old_manifest)
        self.assertTrue(Path(old_spec['file']).is_file())
        self.library.remove(identity)
        self.assertFalse(PresetLibrary(self.settings).entries)
        self.assertTrue(Path(old_spec['file']).is_file())
        self.assertEqual(self.settings.pet.current_frame('idle', 0).toImage().pixelColor(1, 1), QColor('red'))
        self.assertTrue(Path(old_manifest).is_file())

    def test_missing_preset_asset_leaves_current_character_and_settings_intact(self):
        self.draw()
        identity = self.library.save('Красный')
        self.library.apply('builtin')
        previous = dict(self.settings.pet.animations.specs)
        previous_settings = self.settings.collect_settings()
        manifest = Path(self.library.find(identity)['manifest'])
        data = json.loads(manifest.read_text())
        (manifest.parent / data['animations']['idle']['file']).unlink()
        with self.assertRaises(ValueError):
            self.library.apply(identity)
        self.assertEqual(self.settings.pet.animations.specs, previous)
        self.assertEqual(self.settings.collect_settings(), previous_settings)

    def test_controls_and_context_menu_swap_named_preset(self):
        self.draw()
        with patch('desktop_chibi.app.QInputDialog.getText', return_value=('Клубника', True)):
            self.settings.save_preset_button.click()
        identity = self.settings.preset_select.currentData()
        self.assertEqual(self.library.find(identity)['name'], 'Клубника')
        self.library.apply('builtin')
        menu = QMenu()
        self.settings.fill_preset_menu(menu, self.manager.selected.identity)
        menu.actions()[1].trigger()
        self.assertEqual(self.settings.pet.current_frame('idle', 0).toImage().pixelColor(1, 1), QColor('red'))
        self.assertEqual(self.settings.preset_select.currentData(), identity)
        with patch('desktop_chibi.app.QInputDialog.getText', return_value=('Не сохранять', False)):
            self.settings.save_preset()
        self.assertEqual(len(self.library.entries), 1)

    def test_tray_submenu_is_populated_without_about_to_show_and_stays_current(self):
        menu = self.settings.tray_presets
        self.assertEqual(menu.actions()[0].text(), 'Встроенный зайчик')
        self.draw()
        with patch('desktop_chibi.app.QInputDialog.getText', return_value=('Клубника', True)):
            self.settings.save_preset_button.click()
        identity = self.settings.preset_select.currentData()
        self.assertEqual(menu.actions()[1].text(), 'Клубника')
        self.library.apply('builtin')
        menu.actions()[1].trigger()
        self.assertEqual(self.settings.pet.current_frame('idle', 0).toImage().pixelColor(1, 1), QColor('red'))
        with patch('desktop_chibi.app.QInputDialog.getText', return_value=('Земляника', True)):
            self.settings.rename_preset()
        self.assertEqual(menu.actions()[1].text(), 'Земляника')
        self.settings.remove_preset()
        self.assertIsNone(self.library.find(identity))
        self.assertEqual(menu.actions()[0].text(), 'Встроенный зайчик')
        self.assertFalse(menu.actions()[1].isEnabled())


if __name__ == '__main__':
    unittest.main()
