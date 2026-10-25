import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_ROOT = tempfile.TemporaryDirectory(prefix='chibi-stacking-')
os.environ['XDG_CONFIG_HOME'] = _ROOT.name + '/config'
os.environ['XDG_DATA_HOME'] = _ROOT.name + '/data'

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtWidgets import QApplication
from desktop_chibi.app import Settings

APP = QApplication.instance() or QApplication([])


class StackingTests(unittest.TestCase):
    def setUp(self):
        QSettings('DesktopChibi', 'DesktopChibi').clear()
        self.settings = Settings()
        self.manager = self.settings.manager
        self.manager.add()
        self.manager.add()
        self.ids = [item.identity for item in self.manager.items]

    def tearDown(self):
        self.settings.shutdown()
        self.settings.tray.hide()
        self.settings.hide()
        self.settings.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QSettings('DesktopChibi', 'DesktopChibi').clear()

    def test_order_is_applied_back_to_front_and_hidden_windows_stay_hidden(self):
        first, second, third = self.ids
        self.manager.set_stacking_order([first, third, second])
        raised = []
        with ExitStack() as stack:
            for item in self.manager.items:
                stack.enter_context(patch.object(item.pet, 'raise_', side_effect=lambda identity=item.identity: raised.append(identity)))
            self.manager.apply_stacking_order()
            self.assertEqual(raised, [second, third, first])
            self.manager.find(third).pet.hide()
            raised.clear()
            self.manager.apply_stacking_order()
            self.assertEqual(raised, [second, first])
            self.assertFalse(self.manager.find(third).pet.requested_visible)

    def test_buttons_and_drag_keep_selection_independent_and_persist(self):
        editor = self.settings.stacking_editor
        selected = self.manager.selected.identity
        editor.list.setCurrentRow(2)
        moving = editor.list.currentItem().data(Qt.ItemDataRole.UserRole)
        editor.up.click()
        self.assertEqual(self.manager.stacking_order[1], moving)
        self.assertEqual(self.manager.selected.identity, selected)
        # Exercise the same model move used by InternalMove drag and drop.
        self.assertTrue(editor.list.model().moveRows(editor.list.rootIndex(), 1, 1,
                                                    editor.list.rootIndex(), 0))
        APP.processEvents()
        self.assertEqual(self.manager.stacking_order[0], moving)
        saved = json.loads(self.settings.store.value('companion_stacking_v1'))
        self.assertEqual(saved, self.manager.stacking_order)
        self.assertFalse(editor.up.isEnabled())
        self.assertTrue(editor.down.isEnabled())

    def test_restart_names_add_remove_and_invalid_saved_ids(self):
        order = [self.ids[0], self.ids[2], self.ids[1]]
        self.manager.set_stacking_order(order)
        self.manager.select_id(self.ids[0])
        self.manager.rename('Передний чибик')
        self.assertIn('Передний чибик', self.settings.stacking_editor.list.item(0).text())
        self.manager.persist()
        self.settings.shutdown()
        restored = Settings()
        try:
            manager = restored.manager
            self.assertEqual(manager.stacking_order, order)
            manager.select_id(self.ids[2])
            manager.remove()
            self.assertEqual(manager.stacking_order, [self.ids[0], self.ids[1]])
            manager.add()
            self.assertEqual(manager.stacking_order[0], manager.selected.identity)
            cleaned = manager.clean_stacking_order([self.ids[1], self.ids[1], 'gone', {}, None])
            self.assertEqual(cleaned[0], self.ids[1])
            self.assertEqual(set(cleaned), {item.identity for item in manager.items})
        finally:
            restored.shutdown()
            restored.tray.hide()
            restored.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_reshow_and_click_schedule_restack_without_continuous_timer(self):
        APP.processEvents()
        self.manager.stack_timer.stop()
        pet = self.manager.find(self.ids[0]).pet
        pet.hide()
        pet.show()
        self.assertTrue(self.manager.stack_timer.isActive())
        APP.processEvents()
        self.assertFalse(self.manager.stack_timer.isActive())
        self.manager.eventFilter(pet, QEvent(QEvent.Type.MouseButtonPress))
        self.assertTrue(self.manager.stack_timer.isActive())


if __name__ == '__main__':
    unittest.main()
