import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
from desktop_chibi.animations import Animations, read_manifest, release_frame_cache, _SHEET_CACHE
from desktop_chibi.animation_editor import AnimationEditor
from desktop_chibi.pixel_editor import PixelEditor

APP = QApplication.instance() or QApplication([])


def write_gif(path):
    """Three independently drawn frames, with delays 100 / 200 / 300 ms."""
    data = b'GIF89a' + struct.pack('<HHBBB', 1, 1, 0x81, 0, 0)
    data += bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])
    for index, delay in enumerate((10, 20, 30)):
        data += b'!\xf9\x04\x04' + struct.pack('<HBB', delay, 0, 0)
        data += b',' + struct.pack('<HHHHB', 0, 0, 1, 1, 0)
        codes = 4 | (index << 3) | (5 << 6)
        data += b'\x02\x02' + struct.pack('<H', codes) + b'\x00'
    path.write_bytes(data + b';')


class ImportedGifTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='chibi-gif-tests-')
        self.path = Path(self.temp.name) / 'external.gif'
        write_gif(self.path)
        self.player = Animations()

    def tearDown(self):
        self.player.configure({})
        self.temp.cleanup()

    def color(self, state, elapsed):
        return self.player.frame(state, elapsed).toImage().pixelColor(0, 0).name()

    def test_pingpong_preserves_variable_delays_and_one_shot_returns_to_start(self):
        self.player.configure({'notify': {'file': str(self.path), 'pingpong': True}})
        self.assertAlmostEqual(self.player.duration('notify', 0), .8)
        self.assertEqual([self.color('notify', t) for t in (0, .1, .299, .3, .599, .6, .799, .8)],
                         ['#ff0000', '#00ff00', '#00ff00', '#0000ff', '#0000ff', '#00ff00', '#00ff00', '#ff0000'])
        self.player.configure({'land': {'file': str(self.path), 'pingpong': True}})
        self.assertEqual(self.color('land', 10), '#ff0000')
        self.player.configure({'land': {'file': str(self.path)}})
        self.assertAlmostEqual(self.player.duration('land', 0), .6)
        self.assertEqual(self.color('land', 10), '#0000ff')

    def test_frames_are_shared_and_unchanged_clips_are_not_reloaded(self):
        self.player.configure({'idle': {'file': str(self.path)}})
        clip = self.player.clips['idle']
        second = Animations()
        try:
            second.configure({'walk': {'file': str(self.path), 'pingpong': True}})
            self.assertIs(clip.frames, second.clips['walk'].frames)
            self.assertIsNone(clip.movie)
            with patch('desktop_chibi.animations.cached_gif', side_effect=AssertionError('decoded again')):
                self.player.configure(self.player.specs)
            self.assertIs(self.player.clips['idle'], clip)
            with self.assertRaises(ValueError):
                self.player.configure({**self.player.specs, 'walk': {'file': 'missing.gif'}})
            self.assertIs(self.player.clips['idle'], clip)
            self.assertEqual(self.color('idle', .1), '#00ff00')
        finally:
            second.configure({})

    def test_gif_workshop_toggle_export_and_reimport_leave_source_intact(self):
        original = self.path.read_bytes()
        editor = AnimationEditor({'idle': {'file': str(self.path)}})
        reopened = None
        try:
            self.assertTrue(editor.pingpong.isEnabled())
            editor.pingpong.setChecked(True)
            self.assertEqual(editor.error.text(), '')
            self.assertTrue(editor.specs['idle']['pingpong'])
            self.assertAlmostEqual(editor.player.duration('idle', 0), .8)
            self.assertFalse(editor.fields['fps'].isEnabled())
            target = Path(self.temp.name) / 'portable.json'
            with patch('desktop_chibi.animation_editor.QFileDialog.getSaveFileName', return_value=(str(target), '')):
                editor.export_pack()
            _, specs = read_manifest(target)
            reopened = AnimationEditor(specs)
            self.assertTrue(reopened.pingpong.isChecked())
            self.assertAlmostEqual(reopened.player.duration('idle', 0), .8)
            frames, fps = reopened.frames_for_drawing()
            self.assertEqual(len(frames), 3)  # reverse playback does not duplicate frames
            self.assertEqual(fps, 5)
            self.assertEqual(self.path.read_bytes(), original)
        finally:
            editor.reject()
            editor.deleteLater()
            if reopened:
                reopened.reject()
                reopened.deleteLater()

    def test_large_gif_uses_streaming_forward_and_reports_reverse_memory_limit(self):
        with patch('desktop_chibi.animations._CACHE_LIMIT', 1):
            # A new path avoids hitting a previously cached version.
            path = Path(self.temp.name) / 'large.gif'
            path.write_bytes(self.path.read_bytes())
            self.player.configure({'idle': {'file': str(path)}})
            self.assertIsNotNone(self.player.clips['idle'].movie)
            with self.assertRaisesRegex(ValueError, 'МиБ'):
                self.player.configure({'idle': {'file': str(path), 'pingpong': True}})
            self.assertIsNotNone(self.player.clips['idle'].movie)

    def test_trim_reverse_and_speed_preserve_original_timing_and_source(self):
        original = self.path.read_bytes()
        self.player.configure({'idle': {'file': str(self.path), 'first_frame': 1, 'last_frame': 3,
                                         'reverse': True, 'speed': 200, 'pingpong': True}})
        self.assertAlmostEqual(self.player.duration('idle', 0), .25)
        self.assertEqual([self.color('idle', t) for t in (0, .149, .15, .249, .25)],
                         ['#0000ff', '#0000ff', '#00ff00', '#00ff00', '#0000ff'])
        self.player.configure({'land': {'file': str(self.path), 'reverse': True}})
        self.assertEqual(self.color('land', 100), '#ff0000')
        self.assertEqual(self.path.read_bytes(), original)

    def test_cache_release_keeps_active_frames_shared_without_retaining_unused_data(self):
        import gc
        import weakref
        self.player.configure({'idle': {'file': str(self.path)}})
        reference = weakref.ref(self.player.clips['idle'].frames)
        release_frame_cache()
        self.assertFalse(_SHEET_CACHE)
        other = Animations()
        try:
            other.configure({'idle': {'file': str(self.path), 'speed': 150}})
            self.assertIs(self.player.clips['idle'].frames, other.clips['idle'].frames)
        finally:
            other.configure({})
        self.player.configure({})
        gc.collect()
        self.assertIsNone(reference())

    def test_workshop_external_file_transaction_seek_and_settings_export(self):
        editor = AnimationEditor({'idle': {'file': str(self.path)}})
        try:
            self.assertFalse(editor.load_file(Path(self.temp.name) / 'missing.gif'))
            self.assertEqual(editor.specs['idle']['file'], str(self.path))
            editor.gif_fields['first_frame'].setValue(2)
            editor.gif_fields['speed'].setValue(200)
            editor.reverse.setChecked(True)
            editor.reload_preview()
            self.assertEqual(editor.error.text(), '')
            self.assertEqual(len(editor.player.clips['idle'].frames), 2)
            editor.seek_frame(1)
            self.assertFalse(editor.preview_playing)
            self.assertAlmostEqual(editor.paused_elapsed, .15)
            frames, fps = editor.frames_for_drawing()
            self.assertEqual(frames[0].pixelColor(0, 0).name(), '#0000ff')
            self.assertEqual(fps, 8)
            target = Path(self.temp.name) / 'edited.json'
            with patch('desktop_chibi.animation_editor.QFileDialog.getSaveFileName', return_value=(str(target), '')):
                editor.export_pack()
            _, specs = read_manifest(target)
            self.assertEqual(specs['idle']['first_frame'], 1)
            self.assertEqual(specs['idle']['speed'], 200)
            self.assertTrue(specs['idle']['reverse'])
        finally:
            editor.reject()
            editor.deleteLater()

    def test_downscale_limits_retained_frame_dimensions(self):
        # Reuse the fixture with a larger logical screen; all frame pixels stay valid.
        data = bytearray(self.path.read_bytes())
        data[6:10] = struct.pack('<HH', 64, 32)
        path = Path(self.temp.name) / 'wide.gif'
        path.write_bytes(data)
        self.player.configure({'idle': {'file': str(path), 'max_size': 16}})
        frames = self.player.clips['idle'].frames
        self.assertEqual((frames[0].width(), frames[0].height()), (16, 8))
        self.assertEqual(len(frames), 3)

    def test_preview_timers_sleep_when_hidden_paused_or_on_pack_tab(self):
        editor = AnimationEditor({'idle': {'file': str(self.path)}})
        pixel = PixelEditor()
        try:
            self.assertFalse(editor.timer.isActive())
            editor.show()
            APP.processEvents()
            self.assertTrue(editor.timer.isActive())
            editor.play_button.click()
            self.assertFalse(editor.timer.isActive())
            editor.play_button.click()
            self.assertTrue(editor.timer.isActive())
            editor.tabs.setCurrentIndex(1)
            self.assertFalse(editor.timer.isActive())
            editor.tabs.setCurrentIndex(0)
            self.assertTrue(editor.timer.isActive())
            editor.hide()
            self.assertFalse(editor.timer.isActive())
            self.assertFalse(pixel.timer.isActive())
            pixel.show()
            APP.processEvents()
            self.assertTrue(pixel.timer.isActive())
            pixel.play.click()
            self.assertFalse(pixel.timer.isActive())
            pixel.hide()
            self.assertFalse(pixel.timer.isActive())
        finally:
            editor.reject()
            pixel.reject()
            editor.deleteLater()
            pixel.deleteLater()


if __name__ == '__main__':
    unittest.main()
