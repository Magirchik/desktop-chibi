import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from desktop_chibi.single_instance import SingleInstance

APP = QApplication.instance() or QApplication([])

ROOT = Path(__file__).resolve().parents[1]
WORKER = '''
import sys
import time
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QTimer
from desktop_chibi.single_instance import SingleInstance

directory, identity, mode = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
app = QCoreApplication(['desktop-chibi'])
instance = SingleInstance(directory=directory / 'config')
result = directory / identity
def record(message):
    with result.open('a') as stream:
        stream.write(message + '\\n')
instance.activated.connect(lambda: record('activated'))
if mode == 'delayed':
    listen = instance.server.listen
    def delayed_listen(name):
        record('starting')
        while not (directory / 'ready').exists():
            time.sleep(.01)
        return listen(name)
    instance.server.listen = delayed_listen
if mode == 'race':
    record('waiting')
    while not (directory / 'ready').exists():
        time.sleep(.01)
try:
    if instance.start():
        record('primary')
        timer = QTimer()
        timer.timeout.connect(lambda: app.quit() if (directory / 'stop').exists() else None)
        timer.start(20)
        app.exec()
    else:
        record('secondary')
finally:
    instance.close()
'''


class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temporary = tempfile.TemporaryDirectory(prefix='chibi-instance-tests-')
        self.directory = Path(self.temporary.name)
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        self.temporary.cleanup()

    def launch(self, identity, mode='normal'):
        process = subprocess.Popen(
            [sys.executable, '-c', WORKER, str(self.directory), identity, mode],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen'), text=True)
        self.processes.append(process)
        return process

    def wait_for(self, identity, message):
        deadline = time.monotonic() + 8
        path = self.directory / identity
        while time.monotonic() < deadline:
            if path.exists() and message in path.read_text().splitlines():
                return
            for process in self.processes:
                if process.poll() not in (None, 0):
                    self.fail(process.communicate(timeout=2)[1])
            time.sleep(.02)
        self.fail(f'{identity}: did not receive {message}')

    def finish(self, process):
        output, errors = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 0, output + errors)

    def test_repeated_launch_activates_owner_and_secondary_cleanup_keeps_it_alive(self):
        primary = self.launch('first')
        self.wait_for('first', 'primary')
        for identity in ('second', 'third'):
            secondary = self.launch(identity)
            self.finish(secondary)
            self.wait_for(identity, 'secondary')
        self.wait_for('first', 'activated')
        deadline = time.monotonic() + 5
        while (self.directory / 'first').read_text().count('activated') < 2 and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual((self.directory / 'first').read_text().count('activated'), 2)
        (self.directory / 'stop').touch()
        self.finish(primary)
        self.assertFalse((self.directory / 'config/instance.lock').exists())
        (self.directory / 'stop').unlink()
        self.launch('restart')
        self.wait_for('restart', 'primary')

    def test_crashed_owner_leaves_no_permanent_lock_or_socket(self):
        primary = self.launch('first')
        self.wait_for('first', 'primary')
        primary.kill()
        primary.communicate(timeout=5)
        self.processes.remove(primary)
        self.launch('replacement')
        self.wait_for('replacement', 'primary')

    def test_simultaneous_launches_elect_one_owner(self):
        processes = [self.launch(str(index), 'race') for index in range(5)]
        for index in range(5):
            self.wait_for(str(index), 'waiting')
        (self.directory / 'ready').touch()
        for index in range(5):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                lines = (self.directory / str(index)).read_text().splitlines()
                if 'primary' in lines or 'secondary' in lines:
                    break
                time.sleep(.02)
            else:
                self.fail(f'Launch {index} timed out')
        results = [(self.directory / str(index)).read_text() for index in range(5)]
        self.assertEqual(sum('primary' in result for result in results), 1)
        self.assertEqual(sum('secondary' in result for result in results), 4)
        (self.directory / 'stop').touch()
        for process in processes:
            self.finish(process)

    def test_secondary_waits_for_owner_to_open_socket(self):
        primary = self.launch('first', 'delayed')
        self.wait_for('first', 'starting')
        secondary = self.launch('second')
        time.sleep(.2)
        self.assertIsNone(secondary.poll())
        (self.directory / 'ready').touch()
        self.finish(secondary)
        self.wait_for('first', 'primary')
        self.wait_for('first', 'activated')
        (self.directory / 'stop').touch()
        self.finish(primary)

    def test_secondary_gui_exits_before_constructing_settings(self):
        from desktop_chibi.app import main
        with patch('desktop_chibi.app.QApplication') as application, \
                patch('desktop_chibi.app.Settings') as settings, \
                patch.object(sys, 'argv', ['desktop-chibi']), \
                patch('desktop_chibi.single_instance.SingleInstance') as guard:
            guard.return_value.start.return_value = False
            self.assertEqual(main(), 0)
            settings.assert_not_called()
            application.return_value.exec.assert_not_called()
            guard.return_value.close.assert_called_once()

    def test_unresponsive_owner_is_not_replaced(self):
        owner = SingleInstance(directory=self.directory / 'config')
        secondary = SingleInstance(directory=self.directory / 'config')
        try:
            self.assertTrue(owner.lock.tryLock(0))
            with self.assertRaisesRegex(OSError, 'уже запущен'):
                secondary.start(timeout=50)
            secondary.close()
            self.assertTrue(owner.lock.isLocked())
            self.assertFalse(secondary.server.isListening())
        finally:
            secondary.close()
            owner.close()

    def test_lock_failure_reports_error_without_constructing_settings(self):
        from desktop_chibi.app import main
        with patch('desktop_chibi.app.QApplication') as application, \
                patch('desktop_chibi.app.Settings') as settings, \
                patch('desktop_chibi.app.QMessageBox.critical') as message, \
                patch.object(sys, 'argv', ['desktop-chibi']), \
                patch('desktop_chibi.single_instance.SingleInstance') as guard:
            guard.return_value.start.side_effect = OSError('Test lock error')
            self.assertEqual(main(), 1)
            settings.assert_not_called()
            application.return_value.exec.assert_not_called()
            message.assert_called_once_with(None, 'Desktop Chibi', 'Test lock error')
            guard.return_value.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
