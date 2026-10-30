import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_ROOT = tempfile.TemporaryDirectory(prefix='chibi-sequences-')
os.environ['XDG_CONFIG_HOME'] = _ROOT.name + '/config'
os.environ['XDG_DATA_HOME'] = _ROOT.name + '/data'

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QDialog
from desktop_chibi.animations import Animations, SequenceClip, read_manifest
from desktop_chibi.animation_editor import AnimationEditor
from desktop_chibi.animation_sequence import SequenceEditor
from desktop_chibi.app import Settings
from desktop_chibi.pixel_document import PixelDocument
from test_imported_gif import write_gif

APP = QApplication.instance() or QApplication([])


class SequenceTests(unittest.TestCase):
    def setUp(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temporary = tempfile.TemporaryDirectory(prefix='chibi-sequence-assets-')
        self.folder = Path(self.temporary.name)
        self.player = Animations()
        # Each frame has a colored left pixel and transparent right pixel.
        sheet = QImage(4, 1, QImage.Format.Format_ARGB32)
        sheet.fill(QColor('transparent'))
        sheet.setPixelColor(0, 0, QColor('red'))
        sheet.setPixelColor(2, 0, QColor('green'))
        self.source = self.folder / 'dance.png'
        sheet.save(str(self.source))
        self.dance = dict(file=str(self.source), width=2, height=1, frames=2, fps=2)
        turn = QImage(2, 1, QImage.Format.Format_ARGB32)
        turn.fill(QColor('blue'))
        self.turn_path = self.folder / 'turn.png'
        turn.save(str(self.turn_path))
        self.turn = dict(file=str(self.turn_path), fps=4)
        self.spec = {'sequence': [
            {'animation': copy.deepcopy(self.dance), 'repeat': 2, 'name': 'Танец влево'},
            {'animation': copy.deepcopy(self.turn), 'name': 'Поворот'},
            {'animation': copy.deepcopy(self.dance), 'mirror': True, 'name': 'Танец вправо'},
        ]}

    def tearDown(self):
        self.player.configure({})
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temporary.cleanup()

    def color(self, elapsed, x=0, state='idle'):
        return self.player.frame(state, elapsed).toImage().pixelColor(x, 0)

    def test_order_repeats_mirroring_loop_and_landing_last_frame(self):
        self.player.configure({'idle': self.spec, 'land': self.spec})
        self.assertEqual(self.player.duration('idle', 0), 3.25)
        for elapsed, color in [(0, 'red'), (.5, 'green'), (1, 'red'), (1.5, 'green'), (2, 'blue')]:
            self.assertEqual(self.color(elapsed), QColor(color))
        self.assertEqual(self.color(2.25).alpha(), 0)
        self.assertEqual(self.color(2.25, 1), QColor('red'))
        self.assertEqual(self.color(3.249, 1), QColor('green'))
        self.assertEqual(self.color(3.25), QColor('red'))
        self.assertEqual(self.color(99, 1, 'land'), QColor('green'))
        clip = self.player.clips['idle']
        self.assertIsInstance(clip, SequenceClip)
        self.assertIs(clip.parts[0].frames, clip.parts[2].frames)

    def test_validation_is_transactional_and_rejects_nested_or_unbounded_sequences(self):
        self.player.configure({'idle': self.dance})
        original = self.player.clips['idle']
        invalid = [
            {'sequence': []}, {'sequence': 'bad'}, {'sequence': [{}]},
            {'sequence': [{'animation': self.dance}] * 33},
            {'sequence': [{'animation': self.spec}]},
            {'sequence': [{'animation': self.dance, 'repeat': 0}]},
            {'sequence': [{'animation': self.dance, 'repeat': True}]},
            {'sequence': [{'animation': self.dance, 'mirror': 'yes'}]},
            {'sequence': [{'animation': self.dance}, {'animation': {'file': 'missing.gif'}}]},
        ]
        for spec in invalid:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                self.player.configure({'idle': spec})
            self.assertIs(self.player.clips['idle'], original)
            self.assertEqual(self.color(0), QColor('red'))

    def test_gif_parts_preserve_variable_delays_trim_speed_and_reverse(self):
        path = self.folder / 'dance.gif'
        write_gif(path)
        self.player.configure({'idle': {'sequence': [
            {'animation': {'file': str(path)}, 'repeat': 2},
            {'animation': {'file': str(path), 'first_frame': 1, 'last_frame': 3,
                           'reverse': True, 'speed': 200}},
        ]}})
        self.assertAlmostEqual(self.player.duration('idle', 0), 1.45)
        for elapsed, color in [(0, 'red'), (.1, '#00ff00'), (.3, 'blue'), (.6, 'red'),
                               (.7, '#00ff00'), (.9, 'blue'), (1.2, 'blue'), (1.35, '#00ff00'),
                               (1.45, 'red')]:
            with self.subTest(elapsed=elapsed):
                self.assertEqual(self.color(elapsed), QColor(color))

    def test_streaming_parts_stop_on_transition_and_release_files_on_close(self):
        path = self.folder / 'large.gif'
        write_gif(path)
        with patch('desktop_chibi.animations._CACHE_LIMIT', 1):
            self.player.configure({'idle': {'sequence': [
                {'animation': {'file': str(path)}}, {'animation': self.turn},
            ]}})
        movie = self.player.clips['idle'].parts[0].movie
        self.player.frame('idle', 0)
        self.assertEqual(movie.state(), movie.MovieState.Running)
        self.player.frame('idle', .6)
        self.assertEqual(movie.state(), movie.MovieState.NotRunning)
        self.player.configure({})
        self.assertEqual(movie.fileName(), '')
        path.unlink()

    def test_normalized_specs_are_independent_and_priority_is_preserved(self):
        self.spec['priority'] = 27
        self.player.configure({'notify': self.spec})
        self.spec['sequence'][0]['repeat'] = 99
        self.assertEqual(self.player.specs['notify']['sequence'][0]['repeat'], 2)
        self.assertEqual(self.player.specs['notify']['priority'], 27)
        current = self.player.clips['notify']
        self.player.configure(copy.deepcopy(self.player.specs))
        self.assertIs(self.player.clips['notify'], current)
        self.player.restart()
        self.assertEqual(self.color(0, state='notify'), QColor('red'))

    def test_editor_reorders_copies_edits_and_cancels_without_changing_sources(self):
        original = copy.deepcopy(self.spec)
        editor = SequenceEditor(self.spec, {'idle': self.dance})
        try:
            editor.list.setCurrentRow(1)
            editor.move_step(-1)
            self.assertEqual(editor.steps[0]['name'], 'Поворот')
            editor.duplicate_step()
            editor.repeat.setValue(4)
            editor.mirror.setChecked(True)
            self.assertEqual(editor.steps[1]['repeat'], 4)
            self.assertEqual(editor.steps[0].get('repeat', 1), 1)
            editor.add_from_pack()
            self.assertEqual(len(editor.steps), 5)
            editor.remove_step()
            self.assertEqual(len(editor.steps), 4)
            self.assertEqual(self.spec, original)
            editor.reject()
        finally:
            editor.deleteLater()

    def test_workshop_converts_current_state_and_edits_parts_without_nested_sequences(self):
        workshop = AnimationEditor({'idle': {**self.dance, 'priority': 17}})
        try:
            def compose(dialog):
                dialog.append_step(self.turn, 'Поворот')
                dialog.save()
                return dialog.result()
            with patch.object(SequenceEditor, 'exec', compose):
                workshop.edit_sequence()
            self.assertEqual(len(workshop.specs['idle']['sequence']), 2)
            self.assertEqual(workshop.specs['idle']['priority'], 17)
            self.assertFalse(workshop.draw_button.isEnabled())
            self.assertIn('Сценарий', workshop.clip_info.text())
            editor = SequenceEditor(workshop.specs['idle'], {})
            try:
                def edit(part_editor):
                    self.assertTrue(part_editor.sequence_button.isHidden())
                    part_editor.specs['idle']['fps'] = 3
                    part_editor.apply()
                    return part_editor.result()
                with patch.object(AnimationEditor, 'exec', edit):
                    editor.edit_step()
                self.assertEqual(editor.steps[0]['animation']['fps'], 3)
            finally:
                editor.reject()
                editor.deleteLater()
        finally:
            workshop.reject()
            workshop.deleteLater()

    def test_export_import_and_apply_keep_nested_projects_and_generated_files(self):
        workshop = AnimationEditor({})
        doc = PixelDocument(width=8, height=8)
        doc.line((0, 0), (0, 0), QColor('red'))
        doc.add_layer(True)
        drawn = workshop.write_drawing(doc)
        draft = Path(drawn['file'])
        workshop.specs = {'idle': {'sequence': [
            {'animation': drawn, 'repeat': 3}, {'animation': self.turn, 'mirror': True},
        ]}}
        target = self.folder / 'pack.json'
        with patch('desktop_chibi.animation_editor.QFileDialog.getSaveFileName', return_value=(str(target), '')):
            workshop.export_pack()
        exported = json.loads(target.read_text())['animations']['idle']['sequence']
        self.assertFalse(Path(exported[0]['animation']['file']).is_absolute())
        self.assertFalse(Path(exported[0]['animation']['project']).is_absolute())
        _, loaded = read_manifest(target)
        self.player.configure(loaded)
        project = PixelDocument.load_project(loaded['idle']['sequence'][0]['animation']['project'])
        self.assertEqual(len(project.layers), 2)
        workshop.apply()
        self.assertFalse(draft.exists())
        committed = workshop.specs['idle']['sequence'][0]['animation']
        self.assertTrue(Path(committed['file']).is_file())
        self.assertTrue(Path(committed['project']).is_file())
        self.player.configure(workshop.specs)
        workshop.deleteLater()

    def test_idle_finishes_whole_scenario_before_selecting_next_variant(self):
        QSettings('DesktopChibi', 'DesktopChibi').clear()
        settings = Settings()
        try:
            settings.pinned.setChecked(True)
            settings.walk.setChecked(False)
            settings.idle_duration.setValue(4)
            settings.random_idle.setChecked(True)
            pet = settings.pet
            pet.animations.configure({'idle': self.spec})
            with patch('desktop_chibi.app.random.choice', return_value='idle') as choose:
                pet.begin_idle(100)
                self.assertEqual(pet.pause_until, 106.5)
                pet.advance(0, 103.25)
                pet.advance(0, 106.49)
                choose.assert_called_once()
                pet.advance(0, 106.5)
                self.assertEqual(choose.call_count, 2)
        finally:
            settings.shutdown()
            settings.tray.hide()
            settings.hide()
            settings.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            QSettings('DesktopChibi', 'DesktopChibi').clear()

    def test_presets_copy_all_sequence_sources_and_survive_original_removal(self):
        QSettings('DesktopChibi', 'DesktopChibi').clear()
        settings = Settings()
        try:
            settings.pet.load_character('', {'idle': self.spec})
            identity = settings.presets.save('Танец с поворотом')
            self.source.unlink()
            self.turn_path.unlink()
            settings.presets.apply(identity)
            self.assertEqual(settings.pet.current_frame('idle', 2).toImage().pixelColor(0, 0), QColor('blue'))
            self.assertTrue(settings.pet.animations.specs['idle']['sequence'][2]['mirror'])
        finally:
            settings.shutdown()
            settings.tray.hide()
            settings.hide()
            settings.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            QSettings('DesktopChibi', 'DesktopChibi').clear()


if __name__ == '__main__':
    unittest.main()
