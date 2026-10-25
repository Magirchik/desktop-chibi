import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from desktop_chibi.animations import Animations, default_notification, frame_index, read_manifest
from desktop_chibi.animation_editor import AnimationEditor
from desktop_chibi.pixel_editor import PixelDocument, PixelEditor
from desktop_chibi.event_adapters import NotificationAdapter, NOTIFICATION_METHODS
from test_notification_setup import FakeHub

APP = QApplication.instance() or QApplication([])


class LayersTests(unittest.TestCase):
    def test_editor_menus_reorder_frames_and_layers_and_update_navigation(self):
        editor = PixelEditor()
        try:
            editor.document.line((0, 0), (0, 0), QColor('red'))
            editor.mutate(lambda: editor.document.add_frame())
            editor.document.line((0, 0), (0, 0), QColor('blue'))
            editor.refresh()
            self.assertFalse(editor.navigation['next'].isEnabled())
            self.assertTrue(editor.frame_actions['first'].isEnabled())
            editor.frame_actions['first'].trigger()
            self.assertEqual(editor.document.frames[0].pixelColor(0, 0), QColor('blue'))
            self.assertFalse(editor.navigation['previous'].isEnabled())
            editor.frame_actions['copy_end'].trigger()
            self.assertEqual(editor.document.index, 2)
            self.assertEqual(editor.document.count, 3)
            self.assertFalse(editor.layer_actions['delete'].isEnabled())
            editor.layer_actions['shared'].trigger()
            self.assertTrue(editor.document.layer['shared'])
            self.assertTrue(editor.layer_actions['delete'].isEnabled())
            editor.layer_actions['down'].trigger()
            self.assertEqual(editor.document.layer_index, 0)
            editor.layer_actions['delete'].trigger()
            self.assertEqual(len(editor.document.layers), 1)
            editor.undo_button.click()
            self.assertEqual(len(editor.document.layers), 2)
        finally:
            editor.reject()
            editor.deleteLater()

    def test_shared_detail_changes_every_frame_without_touching_base(self):
        doc = PixelDocument(width=8, height=8)
        doc.line((0, 0), (0, 0), QColor('red'))
        doc.add_frame()
        doc.line((1, 0), (1, 0), QColor('green'))
        doc.add_layer(True)
        doc.begin_edit()
        doc.line((2, 0), (2, 0), QColor('blue'))
        self.assertTrue(all(frame.pixelColor(2, 0) == QColor('blue') for frame in doc.frames))
        self.assertEqual(doc.layers[0]['images'][0].pixelColor(2, 0).alpha(), 0)
        doc.undo()
        self.assertTrue(all(frame.pixelColor(2, 0).alpha() == 0 for frame in doc.frames))
        doc.redo()
        doc.set_layer('visible', False)
        self.assertTrue(all(frame.pixelColor(2, 0).alpha() == 0 for frame in doc.frames))
        doc.undo()
        doc.set_layer('locked', True)
        doc.line((3, 0), (3, 0), QColor('yellow'))
        self.assertEqual(doc.image.pixelColor(3, 0).alpha(), 0)

    def test_timeline_reorders_all_layers_and_shared_split_is_independent(self):
        doc = PixelDocument(width=8, height=8)
        doc.line((0, 0), (0, 0), QColor('red'))
        doc.add_frame()
        doc.line((0, 0), (0, 0), QColor('blue'))
        doc.add_layer(True)
        doc.line((1, 0), (1, 0), QColor('green'))
        doc.set_layer('shared', False)
        doc.line((1, 0), (1, 0), QColor('yellow'))
        doc.move_frame(-1)
        self.assertEqual(doc.frames[0].pixelColor(0, 0), QColor('blue'))
        self.assertEqual(doc.frames[0].pixelColor(1, 0), QColor('yellow'))
        self.assertEqual(doc.frames[1].pixelColor(1, 0), QColor('green'))
        doc.add_frame(True, at_end=True)
        self.assertEqual(doc.index, 2)
        self.assertEqual(doc.frames[2], doc.frames[0])
        doc.delete_frame()
        doc.undo()
        self.assertEqual(doc.count, 3)

    def test_project_roundtrip_export_and_reopen_keep_layers(self):
        with tempfile.TemporaryDirectory() as directory:
            workshop = AnimationEditor({})
            doc = PixelDocument(width=8, height=8)
            doc.add_frame(True)
            doc.add_layer(True)
            doc.line((2, 3), (2, 3), QColor('pink'))
            doc.pingpong = True
            workshop.specs['idle'] = workshop.write_drawing(doc)
            target = Path(directory) / 'portable.json'
            with patch('desktop_chibi.animation_editor.QFileDialog.getSaveFileName', return_value=(str(target), '')):
                workshop.export_pack()
            workshop.reject()
            _, specs = read_manifest(target)
            self.assertFalse(Path(json.loads(target.read_text())['animations']['idle']['project']).is_absolute())
            restored = PixelDocument.load_project(specs['idle']['project'])
            self.assertEqual(len(restored.layers), 2)
            self.assertTrue(restored.layer['shared'])
            self.assertTrue(restored.pingpong)
            self.assertEqual(restored.count, 2)
            reopened = AnimationEditor(specs)
            def inspect(editor):
                self.assertEqual(len(editor.document.layers), 2)
                editor.document.line((3, 3), (3, 3), QColor('red'))
                return QDialog.DialogCode.Accepted
            with patch.object(PixelEditor, 'exec', inspect):
                reopened.draw_animation()
            restored = PixelDocument.load_project(reopened.specs['idle']['project'])
            self.assertEqual(restored.frames[0].pixelColor(3, 3), QColor('red'))
            self.assertEqual(restored.frames[1].pixelColor(3, 3), QColor('red'))
            reopened.reject()
            reopened.deleteLater()
            workshop.deleteLater()

    def test_apply_commits_layer_project_outside_drafts(self):
        workshop = AnimationEditor({})
        doc = PixelDocument(width=8, height=8)
        doc.add_layer(True)
        doc.line((0, 0), (0, 0), QColor('blue'))
        workshop.specs = {'idle': workshop.write_drawing(doc)}
        draft_project = Path(workshop.specs['idle']['project'])
        workshop.apply()
        self.assertFalse(draft_project.exists())
        project = Path(workshop.specs['idle']['project'])
        self.assertTrue(project.exists())
        restored = PixelDocument.load_project(project)
        self.assertEqual(len(restored.layers), 2)
        self.assertEqual(restored.frames[0].pixelColor(0, 0), QColor('blue'))
        workshop.deleteLater()

    def test_real_keyboard_navigation_duplicate_end_and_ctrl_pick(self):
        editor = PixelEditor()
        editor.show()
        APP.processEvents()
        editor.canvas.setFocus()
        editor.document.line((0, 0), (0, 0), QColor('red'))
        editor.refresh()
        QTest.keyClick(editor.canvas, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(editor.canvas, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.document.count, 3)
        QTest.keyClick(editor.canvas, Qt.Key.Key_Home)
        self.assertEqual(editor.document.index, 0)
        QTest.keyClick(editor.canvas, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.document.index, 2)
        QTest.keyClick(editor.canvas, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(editor.document.index, 3)
        self.assertEqual(editor.document.count, 4)
        before = len(editor.document.undo_stack)
        editor.canvas.color = QColor('blue')
        QTest.mouseClick(editor.canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, QPoint(3, 3))
        self.assertEqual(editor.canvas.color, QColor('red'))
        self.assertEqual(len(editor.document.undo_stack), before)
        editor.layer_name.setFocus()
        editor.layer_name.selectAll()
        QTest.keyClicks(editor.layer_name, 'bdi')
        self.assertEqual(editor.layer_name.text(), 'bdi')
        self.assertEqual(editor.document.count, 4)
        self.assertEqual(editor.canvas.tool, 'brush')
        QTest.mouseClick(editor.canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, QPoint(3, 3))
        QTest.keyClick(editor.canvas, Qt.Key.Key_E)
        self.assertEqual(editor.canvas.tool, 'eraser')
        editor.reject()
        editor.deleteLater()


class PlaybackTests(unittest.TestCase):
    def test_pingpong_sequence_duration_and_one_shot_return(self):
        self.assertEqual([frame_index(i, 3, True) for i in range(9)], [0, 1, 2, 1, 0, 1, 2, 1, 0])
        self.assertEqual(frame_index(10, 3, True, loop=False), 0)
        self.assertEqual(frame_index(10, 3, False, loop=False), 2)
        with tempfile.TemporaryDirectory() as directory:
            doc = PixelDocument(width=8, height=8)
            for i, color in enumerate(('red', 'green', 'blue')):
                if i:
                    doc.add_frame()
                doc.line((0, 0), (0, 0), QColor(color))
            path = Path(directory) / 'clip.png'
            doc.sheet().save(str(path))
            player = Animations()
            player.configure({'notify': dict(file=str(path), width=8, height=8, frames=3, fps=2, pingpong=True)})
            self.assertEqual(player.duration('notify', 0), 2)
            self.assertEqual(player.frame('notify', 1.5).toImage().pixelColor(0, 0), QColor('green'))
            self.assertEqual(player.frame('notify', 2).toImage().pixelColor(0, 0), QColor('red'))
            player.configure({})

    def test_user_animation_is_bundled(self):
        player = Animations()
        player.configure({'notify': default_notification()})
        self.assertEqual(len(player.clips['notify'].frames), 38)
        self.assertEqual(player.duration('notify', 0), 4.75)
        self.assertEqual(player.frame('notify', 0).size().width(), 32)
        player.configure({})

    def test_telegram_aliases_route_to_existing_rule_name(self):
        hub = FakeHub()
        adapter = NotificationAdapter(hub, {'id': 'notifications', 'apps': ['Telegram Desktop']})
        for interface, member in NOTIFICATION_METHODS:
            raw = 'Telegram' if member == 'Notify' else 'org.telegram.desktop'
            adapter.feed(f'method call x interface={interface}; member={member}\n   string "{raw}"\n   string "PRIVATE"\n')
        self.assertEqual(hub.received, [('notifications', 'message.received', 'Telegram Desktop')] * 3)
        adapter.feed('method call x interface=org.freedesktop.portal.Notification; member=AddNotification\n   string "Telegram Desktop"\n')
        self.assertEqual(len(hub.received), 3)
        adapter.stop()
        hub.dispose()

    def test_monitor_filters_aliases_at_bus(self):
        hub = FakeHub()
        adapter = NotificationAdapter(hub, {'id': 'notifications', 'apps': ['Telegram Desktop']})
        with patch('desktop_chibi.event_adapters.sys.platform', 'linux'), patch('desktop_chibi.event_adapters.shutil.which', return_value='/usr/bin/dbus-monitor'), patch.object(adapter.process, 'start') as start:
            adapter.start()
        args = start.call_args.args[1]
        self.assertTrue(any("arg0='Telegram'" in match for match in args))
        self.assertTrue(all("arg0='" in match for match in args[1:]))
        self.assertTrue(any('org.gtk.Notifications' in match for match in args))
        adapter.stop()
        hub.dispose()


if __name__ == '__main__':
    unittest.main()
