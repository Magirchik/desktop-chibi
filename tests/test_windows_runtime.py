import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
from desktop_chibi.windows_desktop import WindowsShortcuts, hotkey_combination, DEFAULT_KEYS
from desktop_chibi.windows_sound import WaveSound
from desktop_chibi.guides import event_command

APP = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parents[1]


class FakeHotkeys:
    def __init__(self):
        self.keys = {}
        self.blocked = None

    def RegisterHotKey(self, window, identity, modifiers, key):
        if (modifiers, key) == self.blocked:
            return False
        self.keys[identity] = (modifiers, key)
        return True

    def UnregisterHotKey(self, window, identity):
        self.keys.pop(identity, None)
        return True


class WindowsHotkeyTests(unittest.TestCase):
    def test_native_bindings_dispatch_and_release_without_polling(self):
        api = FakeHotkeys()
        client = WindowsShortcuts(api=api)
        packets = []
        client.packet.connect(packets.append)
        try:
            client.rebind(DEFAULT_KEYS)
            self.assertEqual(len(api.keys), 2)
            self.assertTrue(client.activate(0x4340))
            self.assertEqual(packets[-1], {'action': 'visibility'})
            self.assertTrue(client.activate(0x4341))
            self.assertEqual(packets[-1], {'action': 'input'})
            self.assertFalse(client.activate(99))
            self.assertEqual(hotkey_combination('Ctrl+Alt+Shift+F9'), (0x4007, 0x78))
        finally:
            client.stop()
            self.assertFalse(api.keys)
            self.assertFalse(client.installed)
            client.deleteLater()

    def test_rebind_rejects_conflicts_and_restores_previous_working_keys(self):
        api = FakeHotkeys()
        client = WindowsShortcuts(api=api)
        try:
            client.rebind(DEFAULT_KEYS)
            api.blocked = hotkey_combination('Ctrl+F8')
            with self.assertRaises(ValueError):
                client.rebind({'visibility': 'Ctrl+F8', 'input': 'Ctrl+F7'})
            self.assertEqual(client.keys, DEFAULT_KEYS)
            self.assertEqual(len(api.keys), 2)
            with self.assertRaises(ValueError):
                client.rebind({'visibility': 'F9', 'input': 'F10'})
            with self.assertRaises(ValueError):
                client.rebind({'visibility': 'Ctrl+F9', 'input': 'Ctrl+F9'})
        finally:
            client.stop()
            client.deleteLater()


class FakeWave:
    def __init__(self):
        self.volume = None
        self.closed = False

    def waveOutOpen(self, handle, *args):
        handle._obj.value = 17
        return 0

    def waveOutSetVolume(self, handle, volume):
        self.volume = volume
        return 0

    def waveOutPrepareHeader(self, *args): return 0
    def waveOutWrite(self, *args): return 0
    def waveOutReset(self, *args): return 0
    def waveOutUnprepareHeader(self, *args): return 0

    def waveOutClose(self, *args):
        self.closed = True
        return 0


class WindowsSoundTests(unittest.TestCase):
    def test_bounded_pcm_buffer_volume_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sound.wav'
            with wave.open(str(path), 'wb') as file:
                file.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                file.writeframes(bytes(8000 * 2 * 20))
            api = FakeWave()
            sound = WaveSound(api=api)
            finished = []
            sound.finished.connect(finished.append)
            try:
                sound.start(path, 40)
                self.assertEqual(sound.header.length, 8000 * 2 * 15)
                self.assertEqual(api.volume, 0x66666666)
                self.assertTrue(sound.timer.isActive())
                sound.header.flags |= 1
                sound.poll()
                self.assertTrue(finished)
                self.assertTrue(api.closed)
                self.assertIsNone(sound.buffer)
                self.assertFalse(sound.timer.isActive())
            finally:
                sound.stop()
                sound.deleteLater()

    def test_invalid_format_does_not_open_device(self):
        api = FakeWave()
        sound = WaveSound(api=api)
        try:
            with self.assertRaises(ValueError):
                sound.start(Path('sound.ogg'), 35)
            self.assertFalse(sound.handle.value)
        finally:
            sound.deleteLater()


class ReleaseHelpersTests(unittest.TestCase):
    def test_frozen_hook_uses_embedded_cli_instead_of_a_python_source_path(self):
        with patch.object(sys, 'frozen', True, create=True):
            command = event_command()
        self.assertIn('--emit', command)
        self.assertNotIn('emit.py', command)

    def test_artifact_manifest_rejects_changed_files_before_release(self):
        spec = importlib.util.spec_from_file_location('publish_release', ROOT / 'scripts/publish_release.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            mapping = {}
            for suffix in ('windows-x64.zip', 'ubuntu-x64.tar.gz', 'ubuntu-amd64.deb'):
                name = 'desktop-chibi-v0.1.0-' + suffix
                (directory / name).write_bytes(b'package fixture')
                mapping[name] = hashlib.sha256(b'package fixture').hexdigest()
            for platform in ('windows-x64', 'ubuntu-x64'):
                artifacts = {name: value for name, value in mapping.items() if ('windows' in name) == ('windows' in platform)}
                (directory / ('build-' + platform + '.json')).write_text(json.dumps(dict(ok=True, frozen=True, version='0.1.0', artifacts=artifacts)))
            self.assertEqual(len(module.verified_artifacts(directory, '0.1.0')), 6)
            (directory / 'desktop-chibi-v0.1.0-windows-x64.zip').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                module.verified_artifacts(directory, '0.1.0')
