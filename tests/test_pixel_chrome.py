import os
import unittest

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import Qt
from PySide6.QtGui import QRawFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout
from desktop_chibi.pixel_ui import apply_theme, window_bar, WindowBar
from desktop_chibi.animation_editor import AnimationEditor

APP = QApplication.instance() or QApplication([])


class PixelChromeTests(unittest.TestCase):
    def test_window_controls_minimize_maximize_restore_and_cancel(self):
        dialog = QDialog()
        apply_theme(dialog)
        layout = QVBoxLayout(dialog)
        bar = window_bar('Проверка окна')
        layout.addWidget(bar)
        dialog.resize(420, 200)
        try:
            dialog.show()
            APP.processEvents()
            QTest.mouseClick(bar.buttons['maximize'], Qt.MouseButton.LeftButton)
            self.assertTrue(dialog.isMaximized())
            QTest.mouseClick(bar.buttons['maximize'], Qt.MouseButton.LeftButton)
            self.assertFalse(dialog.isMaximized())
            QTest.mouseClick(bar.buttons['minimize'], Qt.MouseButton.LeftButton)
            self.assertTrue(dialog.isMinimized())
            dialog.showNormal()
            QTest.mouseDClick(bar, Qt.MouseButton.LeftButton)
            self.assertTrue(dialog.isMaximized())
            bar.perform('maximize')
            QTest.mouseClick(bar.buttons['close'], Qt.MouseButton.LeftButton)
            self.assertFalse(dialog.isVisible())
            self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        finally:
            dialog.deleteLater()

    def test_workshop_title_stays_outside_scrolling_content_and_close_cleans_drafts(self):
        from pathlib import Path
        editor = AnimationEditor({})
        editor.show()
        APP.processEvents()
        drafts = Path(editor.drafts.name)
        bar = editor.findChild(WindowBar)
        self.assertIs(bar.parentWidget(), editor)
        bar.buttons['close'].click()
        self.assertFalse(drafts.exists())
        self.assertFalse(editor.timer.isActive())
        editor.deleteLater()

    def test_body_font_contains_russian_letters_without_system_fallback(self):
        dialog = QDialog()
        apply_theme(dialog)
        raw = QRawFont.fromFont(dialog.font())
        self.assertTrue(raw.isValid())
        self.assertTrue(all(raw.supportsCharacter(ord(char)) for char in 'НастройкиЧибикиЁжик'))
        dialog.deleteLater()


if __name__ == '__main__':
    unittest.main()
