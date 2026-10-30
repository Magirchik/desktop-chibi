import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desktop_chibi.vesktop_installation import BUILD_FILES, installation_problem

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('chibi_vesktop_installer', ROOT / 'integrations/vesktop/install.py')
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config, self.data = root / 'config', root / 'data'
        self.client = self.config / 'vesktop'
        self.build = self.data / 'DesktopChibi/vesktop-builds/test'
        self.release = root / 'release'
        self.release.mkdir()
        self.build.mkdir(parents=True)
        files = {}
        for name in BUILD_FILES:
            content = b'// DesktopChibiBridge fixture'
            (self.release / name).write_bytes(content)
            (self.build / name).write_bytes(content)
            files[name] = hashlib.sha256(content).hexdigest()
        manifest = json.dumps({'version': 'test', 'files': files})
        (self.release / 'build.json').write_text(manifest)
        (self.build / 'build.json').write_text(manifest)
        (self.client / 'settings').mkdir(parents=True)
        self.state = self.client / 'state.json'
        self.state.write_text(json.dumps({'vencordDir': str(self.build), 'windowBounds': {'width': 900}}))
        self.settings = self.client / 'settings/settings.json'
        self.settings.write_text(json.dumps({'autoUpdate': True, 'theme': 'dark',
                                            'plugins': {'DesktopChibiBridge': {'enabled': True}, 'Other': {'enabled': True}}}))

    def test_integrity_diagnosis_distinguishes_replaced_build_and_disabled_plugin(self):
        self.assertIsNone(installation_problem(self.config))
        (self.build / BUILD_FILES[0]).write_text('// upstream update without custom plugin')
        self.assertIn('изменена или повреждена', installation_problem(self.config))
        (self.build / BUILD_FILES[0]).write_bytes((self.release / BUILD_FILES[0]).read_bytes())
        settings = json.loads(self.settings.read_text())
        settings['plugins']['DesktopChibiBridge']['enabled'] = False
        self.settings.write_text(json.dumps(settings))
        self.assertIn('выключен', installation_problem(self.config))

    def test_restore_has_read_only_plan_and_preserves_settings_and_corrupt_backup(self):
        corrupt = b'// replaced by updater'
        (self.build / BUILD_FILES[0]).write_bytes(corrupt)
        state_before, settings_before = self.state.read_bytes(), self.settings.read_bytes()
        INSTALLER.restore_build(self.release, self.client, self.data)
        self.assertEqual((self.build / BUILD_FILES[0]).read_bytes(), corrupt)
        self.assertEqual(self.settings.read_bytes(), settings_before)
        INSTALLER.restore_build(self.release, self.client, self.data, apply=True)
        self.assertIsNone(installation_problem(self.config))
        self.assertEqual(self.state.read_bytes(), state_before)
        settings = json.loads(self.settings.read_text())
        self.assertFalse(settings['autoUpdate'])
        self.assertEqual(settings['theme'], 'dark')
        self.assertTrue(settings['plugins']['Other']['enabled'])
        backups = list(self.build.parent.glob('test.before-restore-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / BUILD_FILES[0]).read_bytes(), corrupt)

    def test_restore_rolls_back_if_settings_write_fails(self):
        corrupt = b'// previous build'
        (self.build / BUILD_FILES[0]).write_bytes(corrupt)
        settings_before = self.settings.read_bytes()
        with patch.object(INSTALLER, 'atomic_json', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                INSTALLER.restore_build(self.release, self.client, self.data, apply=True)
        self.assertEqual((self.build / BUILD_FILES[0]).read_bytes(), corrupt)
        self.assertEqual(self.settings.read_bytes(), settings_before)

    def test_restore_refuses_unmanaged_directory(self):
        self.state.write_text(json.dumps({'vencordDir': str(self.release)}))
        with self.assertRaises(ValueError):
            INSTALLER.restore_build(self.release, self.client, self.data, apply=True)
