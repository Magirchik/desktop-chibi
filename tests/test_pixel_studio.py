import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_TEST_DATA = tempfile.TemporaryDirectory(prefix='chibi-studio-tests-')
os.environ['XDG_DATA_HOME'] = _TEST_DATA.name
os.environ.setdefault('XDG_CONFIG_HOME', _TEST_DATA.name + '/config')

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from desktop_chibi.animation_editor import AnimationEditor
from desktop_chibi.animations import Animations, read_manifest
from desktop_chibi.app import Settings
from desktop_chibi.pixel_editor import PixelDocument, PixelEditor

APP = QApplication.instance() or QApplication([])


class PixelDocumentTests(unittest.TestCase):
    def test_continuous_stroke_and_undo_redo(self):
        doc = PixelDocument(width=8, height=8)
        doc.begin_edit()
        doc.line((0, 0), (7, 7), QColor('red'))
        for i in range(8):
            self.assertEqual(doc.image.pixelColor(i, i), QColor('red'))
        doc.undo()
        self.assertEqual(doc.image.pixelColor(3, 3).alpha(), 0)
        doc.redo()
        self.assertEqual(doc.image.pixelColor(3, 3), QColor('red'))
        doc.begin_edit()
        doc.line((3, 3), (3, 3), QColor(Qt.GlobalColor.transparent))
        self.assertEqual(doc.image.pixelColor(3, 3).alpha(), 0)
        self.assertFalse(doc.redo_stack)

    def test_fill_stops_at_outline_and_restores_transparency(self):
        doc = PixelDocument(width=8, height=8)
        for start, end in [((1, 1), (6, 1)), ((6, 1), (6, 6)), ((6, 6), (1, 6)), ((1, 6), (1, 1))]:
            doc.line(start, end, QColor('black'))
        doc.fill(3, 3, QColor('pink'))
        self.assertEqual(doc.image.pixelColor(3, 3), QColor('pink'))
        self.assertEqual(doc.image.pixelColor(1, 1), QColor('black'))
        self.assertEqual(doc.image.pixelColor(0, 0).alpha(), 0)
        doc.undo()
        self.assertEqual(doc.image.pixelColor(3, 3).alpha(), 0)
        self.assertEqual(doc.image.pixelColor(1, 1), QColor('black'))

    def test_frames_transform_reorder_and_new_canvas_undo(self):
        doc = PixelDocument(width=8, height=8)
        doc.line((1, 2), (1, 2), QColor('red'))
        doc.add_frame(True)
        doc.transform('right')
        self.assertEqual(doc.image.pixelColor(2, 2), QColor('red'))
        self.assertEqual(doc.frames[0].pixelColor(1, 2), QColor('red'))
        doc.transform('flip_x')
        self.assertEqual(doc.image.pixelColor(5, 2), QColor('red'))
        doc.move_frame(-1)
        self.assertEqual(doc.index, 0)
        self.assertEqual(doc.frames[1].pixelColor(1, 2), QColor('red'))
        doc.new_canvas(16)
        self.assertEqual((doc.image.width(), len(doc.frames)), (16, 1))
        doc.undo()
        self.assertEqual((doc.image.width(), len(doc.frames)), (8, 2))
        doc.delete_frame()
        self.assertEqual(len(doc.frames), 1)
        doc.delete_frame()
        self.assertEqual(len(doc.frames), 1)
        self.assertEqual(doc.image.pixelColor(1, 2).alpha(), 0)

    def test_multiline_sheet_roundtrip(self):
        doc = PixelDocument(width=8, height=8)
        for i in range(20):
            if i:
                doc.add_frame()
            doc.line((0, 0), (7, 7), QColor(i, 50, 150))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sheet.png'
            self.assertTrue(doc.sheet().save(str(path)))
            player = Animations()
            player.configure({'walk': {'file': str(path), 'width': 8, 'height': 8, 'frames': 20, 'fps': 10}})
            for i, frame in enumerate(player.clips['walk'].frames):
                self.assertEqual(frame.toImage().pixelColor(0, 0), QColor(i, 50, 150))
            player.configure({})


class PixelStudioTests(unittest.TestCase):
    def test_settings_can_create_apply_and_load_drawn_character(self):
        settings = Settings()
        settings.pet.timer.stop()
        settings.preview_timer.stop()
        try:
            settings.reset_character()
            def draw(editor):
                editor.document.line((2, 3), (6, 3), QColor('red'))
                return QDialog.DialogCode.Accepted
            def workshop_session(workshop):
                workshop.create_character()
                workshop.character_name.setText('Нарисованный друг')
                workshop.apply()
                return workshop.result()
            with patch.object(PixelEditor, 'exec', draw), patch.object(AnimationEditor, 'exec', workshop_session):
                settings.edit_animations()
            self.assertTrue(Path(settings.character_path).is_file())
            self.assertEqual(settings.character_label.text(), 'Персонаж: Нарисованный друг')
            self.assertEqual(settings.pet.current_frame().toImage().pixelColor(2, 3), QColor('red'))
            path = settings.character_path
            settings.reset_character()
            settings.set_character(path)
            self.assertEqual(settings.pet.current_frame().toImage().pixelColor(2, 3), QColor('red'))
        finally:
            settings.pet.hide()
            settings.tray.hide()
            settings.hide()
            settings.deleteLater()
            APP.processEvents()

    def test_mouse_stroke_and_undo_button(self):
        editor = PixelEditor()
        editor.show()
        APP.processEvents()
        canvas = editor.canvas
        zoom = canvas.zoom
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(zoom // 2, zoom // 2))
        QTest.mouseMove(canvas, QPoint(3 * zoom + zoom // 2, 3 * zoom + zoom // 2))
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(3 * zoom + zoom // 2, 3 * zoom + zoom // 2))
        self.assertEqual(editor.document.image.pixelColor(2, 2), canvas.color)
        self.assertEqual(len(editor.document.undo_stack), 1)
        editor.undo_button.click()
        self.assertEqual(editor.document.image.pixelColor(2, 2).alpha(), 0)
        editor.redo_button.click()
        self.assertEqual(editor.document.image.pixelColor(2, 2), canvas.color)
        editor.reject()
        editor.deleteLater()
        APP.processEvents()

    def test_edit_is_staged_and_cancel_keeps_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'original.png'
            doc = PixelDocument(width=8, height=8)
            doc.line((0, 0), (0, 0), QColor('red'))
            doc.sheet().save(str(source))
            original_bytes = source.read_bytes()
            original = {'idle': {'file': str(source)}}
            workshop = AnimationEditor(original)
            def draw(editor):
                editor.document.add_frame(True)
                editor.document.line((1, 1), (1, 1), QColor('blue'))
                return QDialog.DialogCode.Accepted
            with patch.object(PixelEditor, 'exec', draw):
                workshop.draw_animation()
            staged = Path(workshop.specs['idle']['file'])
            self.assertTrue(staged.exists())
            self.assertEqual(workshop.specs['idle']['frames'], 2)
            self.assertEqual(source.read_bytes(), original_bytes)
            self.assertEqual(original, {'idle': {'file': str(source)}})
            workshop.reject()
            self.assertFalse(staged.exists())
            self.assertEqual(source.read_bytes(), original_bytes)
            workshop.deleteLater()

    def test_new_character_commits_and_reopens(self):
        workshop = AnimationEditor({})
        def draw(editor):
            editor.document.line((4, 4), (8, 8), QColor('pink'))
            return QDialog.DialogCode.Accepted
        with patch.object(PixelEditor, 'exec', draw):
            workshop.create_character()
        self.assertTrue(workshop.created_character)
        self.assertEqual(set(workshop.specs), {'idle'})
        draft = Path(workshop.specs['idle']['file'])
        workshop.apply()
        self.assertEqual(workshop.result(), QDialog.DialogCode.Accepted)
        self.assertFalse(draft.exists())
        self.assertTrue(Path(workshop.specs['idle']['file']).is_file())
        name, specs = read_manifest(workshop.character_manifest)
        self.assertEqual(name, 'Мой чибик')
        reopened = AnimationEditor(specs)
        frames, fps = reopened.frames_for_drawing()
        self.assertEqual(frames[0].pixelColor(4, 4), QColor('pink'))
        reopened.state.setCurrentIndex(reopened.state.findData('walk'))
        frames, fps = reopened.frames_for_drawing()  # base texture seeds new states
        self.assertEqual(frames[0].pixelColor(4, 4), QColor('pink'))
        reopened.reject()
        reopened.deleteLater()
        workshop.deleteLater()


if __name__ == '__main__':
    unittest.main()
