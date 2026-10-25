import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QDialog
from desktop_chibi.appearance import appearance, Appearance
from desktop_chibi.app_settings import AppSettings
from desktop_chibi.app import Settings
from desktop_chibi.animation_editor import AnimationEditor
from desktop_chibi.animations import Animations
from desktop_chibi.gif_import import GifImport
from desktop_chibi.pixel_document import PixelDocument, EDITOR_BYTES, COMPOSITE_BYTES
from desktop_chibi.pixel_editor import PixelEditor
from desktop_chibi.pixel_ui import bunny_frame, _tile
from test_imported_gif import write_gif

APP = QApplication.instance() or QApplication([])


class AppearanceTests(unittest.TestCase):
    def setUp(self):
        QSettings('DesktopChibi', 'DesktopChibi').clear()
        self.original = appearance().theme
        appearance().set_theme('light')
        self.settings = Settings()

    def tearDown(self):
        self.settings.shutdown()
        self.settings.tray.hide()
        self.settings.hide()
        self.settings.deleteLater()
        appearance().set_theme(self.original)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QSettings('DesktopChibi', 'DesktopChibi').clear()

    def test_theme_updates_open_and_future_dialogs_persists_and_preserves_artwork(self):
        editor = AnimationEditor({})
        dialog = AppSettings(self.settings)
        image = bunny_frame().toImage()
        before = editor.styleSheet()
        try:
            dialog.theme.setCurrentIndex(dialog.theme.findData('dark'))
            self.assertNotEqual(editor.styleSheet(), before)
            self.assertEqual(QSettings('DesktopChibi', 'DesktopChibi').value('app_theme'), 'dark')
            restored = Appearance()
            self.assertEqual(restored.theme, 'dark')
            restored.deleteLater()
            future = AppSettings(self.settings)
            self.assertEqual(future.theme.currentData(), 'dark')
            self.assertEqual(future.styleSheet(), editor.styleSheet())
            self.assertEqual(bunny_frame().toImage(), image)
            light = _tile(False, 'light').toImage().pixelColor(10, 10).lightness()
            dark = _tile(False, 'dark').toImage().pixelColor(10, 10).lightness()
            self.assertLess(dark, light / 2)
            self.assertIn('check-dark.svg', editor.styleSheet())
            future.reject()
            future.deleteLater()
        finally:
            dialog.reject()
            dialog.deleteLater()
            editor.reject()
            editor.deleteLater()


class LargeGifTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='chibi-512-')
        self.path = Path(self.temp.name) / 'outside.gif'
        write_gif(self.path)

    def tearDown(self):
        self.temp.cleanup()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_visible_frame_markers_trim_external_gif_and_reset(self):
        editor = AnimationEditor({'idle': {'file': str(self.path)}})
        try:
            editor.show()
            self.assertTrue(editor.gif_range.isVisible())
            editor.seek_frame(1)
            editor.range_buttons['first'].click()
            self.assertEqual(editor.specs['idle']['first_frame'], 1)
            editor.seek_frame(1)
            self.assertEqual(editor.source_frame(), 3)
            editor.range_buttons['last'].click()
            self.assertEqual(editor.specs['idle']['last_frame'], 3)
            self.assertAlmostEqual(editor.player.duration('idle', 0), .5)
            editor.range_buttons['reset'].click()
            self.assertEqual(editor.specs['idle']['first_frame'], 0)
            self.assertEqual(editor.specs['idle']['last_frame'], 0)
            self.assertAlmostEqual(editor.player.duration('idle', 0), .6)
        finally:
            editor.reject()
            editor.deleteLater()

    def test_external_512_edit_downscale_and_layer_project_preserve_timing_and_source(self):
        data = bytearray(self.path.read_bytes())
        data[6:10] = struct.pack('<HH', 512, 512)
        self.path.write_bytes(data)
        original = self.path.read_bytes()
        editor = AnimationEditor({'idle': {'file': str(self.path)}})
        player = Animations()
        try:
            def edit(pixel):
                self.assertEqual(pixel.document.image.size(), QSize(512, 512))
                self.assertEqual(pixel.document.delays, [100, 200, 300])
                pixel.document.line((20, 20), (25, 25), QColor('magenta'))
                pixel.output_size.setCurrentIndex(pixel.output_size.findData(256))
                return QDialog.DialogCode.Accepted
            with patch.object(PixelEditor, 'exec', edit):
                editor.draw_animation()
            spec = editor.specs['idle']
            self.assertEqual((spec['width'], spec['height']), (256, 256))
            self.assertEqual(spec['delays'], [100, 200, 300])
            restored = PixelDocument.load_project(spec['project'])
            self.assertEqual(restored.image.size(), QSize(512, 512))
            self.assertEqual(restored.delays, [100, 200, 300])
            player.configure({'idle': spec})
            self.assertAlmostEqual(player.duration('idle', 0), .6)
            self.assertEqual(player.clips['idle'].gif_delays, (100, 200, 300))
            self.assertEqual(self.path.read_bytes(), original)
        finally:
            player.configure({})
            editor.reject()
            editor.deleteLater()

    def test_oversized_external_gif_offers_preparation_before_pixel_editor(self):
        data = bytearray(self.path.read_bytes())
        data[6:10] = struct.pack('<HH', 1024, 512)
        self.path.write_bytes(data)
        editor = AnimationEditor({'idle': {'file': str(self.path)}})
        try:
            def prepare(dialog):
                self.assertEqual(dialog.source_size, QSize(1024, 512))
                self.assertEqual(dialog.size.currentData(), 512)
                return QDialog.DialogCode.Accepted
            def edit(dialog):
                self.assertEqual(dialog.document.image.size(), QSize(512, 256))
                return QDialog.DialogCode.Rejected
            with patch.object(GifImport, 'exec', prepare) as preparation, patch.object(PixelEditor, 'exec', edit):
                editor.draw_animation()
            self.assertEqual(editor.specs['idle']['file'], str(self.path))
        finally:
            editor.reject()
            editor.deleteLater()

    def test_import_preflight_limits_frames_and_memory_without_decoding(self):
        dialog = GifImport(QSize(512, 512), 100, {})
        try:
            self.assertEqual(dialog.size.currentData(), 256)
            self.assertLessEqual(dialog.cost(256), EDITOR_BYTES)
            dialog.size.setCurrentIndex(dialog.size.findData(512))
            self.assertFalse(dialog.import_button.isEnabled())
            dialog.last.setValue(50)
            self.assertTrue(dialog.import_button.isEnabled())
        finally:
            dialog.reject()
            dialog.deleteLater()


class EditorMemoryTests(unittest.TestCase):
    def image(self):
        image = QImage(512, 512, QImage.Format.Format_ARGB32)
        image.fill(QColor('red'))
        return image

    def test_512_capacity_export_auto_and_composite_cache_are_bounded(self):
        image = self.image()
        with self.assertRaises(ValueError):
            PixelDocument([image] * 65)
        document = PixelDocument([image] * 50)
        self.assertEqual(document.export_dimensions(), QSize(256, 256))
        with self.assertRaises(ValueError):
            document.export_dimensions(-1)
        document = PixelDocument([image] * 10)
        document.add_layer(True)
        for index in range(document.count):
            document.composite(index)
        self.assertLessEqual(sum(frame.sizeInBytes() for frame in document._cache.values()), COMPOSITE_BYTES)
        pixel = PixelEditor(document=document)
        try:
            self.assertTrue(all(max(icon.width(), icon.height()) <= 48 for index in range(pixel.timeline.count())
                                for icon in pixel.timeline.item(index).icon().availableSizes()))
        finally:
            pixel.reject()
            pixel.deleteLater()

    def test_timing_follows_copy_move_delete_undo_and_flood_fill_on_512_canvas(self):
        image = self.image()
        document = PixelDocument([image] * 3, delays=[100, 200, 300])
        document.index = 0
        document.add_frame(True, at_end=True)
        self.assertEqual(document.delays, [100, 200, 300, 100])
        document.move_frame(-2)
        self.assertEqual(document.delays, [100, 100, 200, 300])
        document.delete_frame()
        self.assertEqual(document.delays, [100, 200, 300])
        document.undo()
        self.assertEqual(document.delays, [100, 100, 200, 300])
        document.fill(0, 0, QColor('blue'))
        self.assertEqual(document.image.pixelColor(511, 511), QColor('blue'))
        document.undo()
        self.assertEqual(document.image.pixelColor(511, 511), QColor('red'))

    def test_history_and_new_layers_respect_image_budgets(self):
        image = self.image()
        full = PixelDocument([image] * 64)
        with self.assertRaises(ValueError):
            full.add_layer(True)
        with self.assertRaises(ValueError):
            full.add_frame()
        document = PixelDocument([image])
        with patch('desktop_chibi.pixel_document.HISTORY_BYTES', 1024 * 1024):
            for i in range(4):
                document.begin_edit()
                document.line((i, i), (i, i), QColor('blue'))
                document.prune_history()
            self.assertLessEqual(len(document.undo_stack), 1)


if __name__ == '__main__':
    unittest.main()
