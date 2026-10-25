import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_ROOT = tempfile.TemporaryDirectory(prefix='chibi-vesktop-tests-')
os.environ['XDG_CONFIG_HOME'] = _ROOT.name + '/config'
os.environ['XDG_DATA_HOME'] = _ROOT.name + '/data'
from PySide6.QtCore import QCoreApplication, QEvent, QProcess, QProcessEnvironment, QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from desktop_chibi.app import Settings
from desktop_chibi.quick_setup import QuickSetup
from desktop_chibi.vesktop_bridge import bridge_config_path

APP = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix', 'The current Vesktop native plugin and installer target Linux')
class VesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        QSettings('DesktopChibi', 'DesktopChibi').clear()
        self.temp = tempfile.TemporaryDirectory(prefix='chibi-vesktop-ipc-')
        self.socket_path = str(Path(self.temp.name) / f'desktop-chibi-{os.getuid()}.sock')
        self.socket_patch = patch('desktop_chibi.events.socket_name', return_value=self.socket_path)
        self.socket_patch.start()
        self.settings = Settings()
        self.hub, self.manager = self.settings.event_hub, self.settings.manager
        self.target = self.manager.selected
        self.source = copy.deepcopy(self.hub.source('vesktop'))
        self.source.update(enabled=True, channel_id='123456789', users=['987654321'])
        sources = [copy.deepcopy(s) if s['id'] != 'vesktop' else self.source for s in self.hub.sources]
        self.rule = dict(id='vesktop-test', source='vesktop', event='voice.speaking', subject='987654321',
                         target=self.target.identity, animation='talk', mode='hold', duration=6, cooldown=0, enabled=True)
        self.hub.configure(sources, [self.rule])

    def tearDown(self):
        self.settings.shutdown()
        self.settings.tray.hide(); self.settings.hide(); self.settings.deleteLater()
        QCoreApplication.sendPostedEvents(self.settings, QEvent.Type.DeferredDelete)
        self.socket_patch.stop()
        self.temp.cleanup()
        QSettings('DesktopChibi', 'DesktopChibi').clear()

    def packet(self, **values):
        return dict(source='vesktop', event='voice.speaking', subject='987654321', channel='123456789',
                    active=True, token=self.source['token']) | values

    def test_allowlist_private_config_and_revocation(self):
        config = bridge_config_path()
        self.assertTrue(config.is_file())
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
        self.assertTrue(self.hub.receive_local(self.packet())['ok'])
        self.assertEqual(self.target.pet.state, 'talk')
        for values in (dict(subject='111'), dict(channel='222'), dict(token='wrong'), dict(message='PRIVATE'), dict(keepalive='yes')):
            self.assertFalse(self.hub.receive_local(self.packet(**values))['ok'])
        count = len(self.hub.log)
        self.hub.receive_local(self.packet(keepalive=True))
        self.assertEqual(len(self.hub.log), count)
        self.hub.receive_local(self.packet(active=False))
        self.assertFalse(self.target.pet.notifications)
        self.hub.stop()
        self.assertFalse(config.exists())

    def test_presence_visibility_silence_manual_hide_and_disconnect(self):
        pet = self.target.pet
        self.settings.hide_when_absent.setChecked(True)
        pet.setVisible(True)
        self.assertFalse(pet.isVisible())
        self.assertTrue(pet.requested_visible)
        self.assertTrue(self.hub.receive_local(self.packet(present=True, active=False))['ok'])
        self.assertTrue(pet.isVisible())  # Present but silent.
        self.hub.receive_local(self.packet(present=False, active=False))
        self.assertFalse(pet.isVisible())
        self.manager.persist()
        records = json.loads(self.settings.store.value('companions_v1'))
        self.assertTrue(records[0]['visible'])  # Automatic hiding does not overwrite intent.
        self.hub.receive_local(self.packet(present=True))
        self.assertTrue(pet.isVisible())
        pet.setVisible(False)
        self.hub.receive_local(self.packet(present=True))
        self.assertFalse(pet.isVisible())  # Never undo manual hiding.
        pet.setVisible(True)
        self.hub.vesktop_bridge.last_seen = time.monotonic() - 9
        self.hub.vesktop_bridge.check()
        self.assertFalse(pet.isVisible())
        self.settings.hide_when_absent.setChecked(False)
        self.assertTrue(pet.isVisible())
        self.assertFalse(self.hub.receive_local(self.packet(present='yes'))['ok'])

    def test_wizard_adds_friend_without_replacing_existing_allowlist_or_rules(self):
        wizard = QuickSetup(self.settings, 'vesktop')
        wizard.fields['users'].setText('619510616473731073')
        sources, rules = wizard.prepare(self.target.identity)
        source = next(s for s in sources if s['id'] == 'vesktop')
        self.assertEqual(source['users'], ['987654321', '619510616473731073'])
        self.assertTrue(any(r['subject'] == '987654321' and r['enabled'] for r in rules))
        self.assertTrue(any(r['subject'] == '619510616473731073' and r['enabled'] for r in rules))
        wizard.reject(); wizard.deleteLater()

    def test_fix_restores_missing_friend_and_preserves_settings_and_keys(self):
        from desktop_chibi.repair import repair_application
        source = copy.deepcopy(self.hub.source('vesktop'))
        source['users'] = ['111']  # Simulate the old wizard replacing the shared list.
        sources = [source if s['id'] == 'vesktop' else copy.deepcopy(s) for s in self.hub.sources]
        self.hub.configure(sources, self.hub.rules)
        original_rules = copy.deepcopy(self.hub.rules)
        token = source['token']
        self.target.pet.setVisible(False)
        messages, backup = repair_application(self.settings)
        self.assertTrue(backup.is_file())
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(self.hub.source('vesktop')['users'], ['111', '987654321'])
        self.assertEqual(self.hub.source('vesktop')['token'], token)
        self.assertEqual(self.hub.rules, original_rules)
        self.assertFalse(self.target.pet.requested_visible)
        self.assertTrue(any('Восстановлено' in message for message in messages))
        messages, another = repair_application(self.settings)
        self.assertFalse(any('Восстановлено' in message for message in messages))
        self.assertNotEqual(backup, another)

    def test_fix_never_enables_disabled_sources(self):
        from desktop_chibi.repair import repair_application
        source = copy.deepcopy(self.hub.source('vesktop'))
        source.update(enabled=False, users=[])
        self.hub.configure([source if s['id'] == 'vesktop' else copy.deepcopy(s) for s in self.hub.sources], self.hub.rules)
        messages, backup = repair_application(self.settings)
        self.assertFalse(self.hub.source('vesktop')['enabled'])
        self.assertEqual(self.hub.source('vesktop')['users'], [])
        self.assertTrue(any('выключен' in message for message in messages))

    def test_auto_channel_accepts_switch_and_still_filters_users(self):
        self.source['channel_id'] = ''
        sources = [copy.deepcopy(s) if s['id'] != 'vesktop' else self.source for s in self.hub.sources]
        self.hub.configure(sources, [self.rule])
        for channel in ('111', '222'):
            result = self.hub.receive_local(self.packet(channel=channel))
            self.assertTrue(result['ok'])
            self.assertEqual(result['matched'], 1)
        self.assertFalse(self.hub.receive_local(self.packet(subject='123'))['ok'])
        self.assertFalse(self.hub.receive_local(self.packet(channel='bad'))['ok'])
        self.assertTrue(self.hub.receive_local(self.packet(channel='0', active=False))['ok'])

    def test_watchdog_clears_activity_without_affecting_other_source(self):
        self.hub.receive_local(self.packet())
        self.hub.vesktop_bridge.last_seen = time.monotonic() - 9
        self.hub.vesktop_bridge.check()
        self.assertFalse(self.target.pet.notifications)
        self.assertIn('Нет связи', self.hub.status['vesktop'])

    def test_wizard_reuses_discord_ids_and_migrates_only_matching_rule(self):
        source = self.hub.source('discord')
        source.update(enabled=True, client_id='111111111', channel_id='123456789', users=['987654321'])
        self.hub.rules = [{**self.rule, 'source': 'discord'},
                          dict(id='telegram', source='notifications', event='message.received', subject='Telegram Desktop',
                               target=self.target.identity, animation='notify', mode='pulse', duration=4, cooldown=2, enabled=True)]
        wizard = QuickSetup(self.settings, 'vesktop')
        sources, rules = wizard.prepare(self.target.identity)
        self.assertEqual(wizard.fields['channel_id'].text(), '123456789')
        self.assertEqual(wizard.fields['users'].text(), '987654321')
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0]['id'], 'vesktop-test')
        self.assertEqual(rules[0]['source'], 'vesktop')
        self.assertEqual(rules[1]['id'], 'telegram')
        self.assertFalse(next(s for s in sources if s['id'] == 'discord')['enabled'])
        wizard.reject(); wizard.deleteLater()

    def test_installer_changes_only_bridge_preferences_and_preserves_rules(self):
        import hashlib
        import sys
        config = Path(os.environ['XDG_CONFIG_HOME'])
        data = Path(self.temp.name) / 'installed-data'
        client = config / 'vesktop'
        (client / 'settings').mkdir(parents=True, exist_ok=True)
        (client / 'state.json').write_text(json.dumps({'windowBounds': {'width': 900}}))
        (client / 'settings/settings.json').write_text(json.dumps({'plugins': {'OtherPlugin': {'enabled': True}}, 'theme': 'dark'}))
        release = Path(self.temp.name) / 'release'
        release.mkdir()
        files = {}
        for name in ('vencordDesktopMain.js', 'vencordDesktopPreload.js', 'vencordDesktopRenderer.js', 'vencordDesktopRenderer.css'):
            content = b'// test build'
            (release / name).write_bytes(content)
            files[name] = hashlib.sha256(content).hexdigest()
        (release / 'build.json').write_text(json.dumps({'version': 'test-version', 'files': files}))
        self.manager.persist()
        # QSettings caches standard paths across this multi-module Qt test suite.
        # Give the subprocess an explicit profile at its XDG config path.
        disk = QSettings(str(config / 'DesktopChibi/DesktopChibi.conf'), QSettings.Format.IniFormat)
        disk.setValue('companions_v1', json.dumps(self.manager.records()))
        disk.setValue('events_v1', json.dumps({'sources': self.hub.sources, 'rules': self.hub.rules}))
        disk.sync()
        env = dict(os.environ, XDG_DATA_HOME=str(data))
        command = [sys.executable, str(ROOT / 'integrations/vesktop/install.py'), '--target-id', self.target.identity, '--release', str(release)]
        planned = subprocess.run(command, capture_output=True, env=env)
        self.assertEqual(planned.returncode, 0, planned.stderr.decode())
        self.assertNotIn('vencordDir', json.loads((client / 'state.json').read_text()))
        result = subprocess.run([*command, '--install'], capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        installed = json.loads((client / 'state.json').read_text())
        self.assertEqual(installed['windowBounds']['width'], 900)
        self.assertTrue(Path(installed['vencordDir']).is_dir())
        settings = json.loads((client / 'settings/settings.json').read_text())
        self.assertTrue(settings['plugins']['OtherPlugin']['enabled'])
        self.assertTrue(settings['plugins']['DesktopChibiBridge']['enabled'])
        self.assertEqual(settings['theme'], 'dark')
        self.assertTrue(list(client.glob('state.json.before-chibi-bridge-*.bak')))

    def test_native_plugin_to_real_qt_ipc_and_animation(self):
        esbuild = ROOT / '.tools/vencord/node_modules/.bin/esbuild'
        if not esbuild.exists():
            self.skipTest('Install build dependencies for the native plugin integration check')
        module = Path(self.temp.name) / 'native.mjs'
        build = subprocess.run([str(esbuild), str(ROOT / 'integrations/vesktop/DesktopChibiBridge/native.ts'),
                                '--bundle', '--platform=node', '--format=esm', '--outfile=' + str(module)], capture_output=True)
        self.assertEqual(build.returncode, 0, build.stderr.decode())
        script = f'''const m = await import({json.dumps(module.as_uri())});
const config = await m.getConfiguration(null);
const start = await m.sendVoiceState(null, "123456789", "987654321", true, false);
const wrong = await m.sendVoiceState(null, "123456789", "111", true, false);
const stop = await m.sendVoiceState(null, "123456789", "987654321", false, false);
console.log(JSON.stringify({{config, start, wrong, stop}}));'''
        process = QProcess()
        env = QProcessEnvironment.systemEnvironment()
        env.insert('TMPDIR', self.temp.name)
        process.setProcessEnvironment(env)
        process.start('node', ['--input-type=module', '-e', script])
        for _ in range(500):
            APP.processEvents(); QTest.qWait(10)
            if process.state() == QProcess.ProcessState.NotRunning:
                break
        self.assertEqual(process.state(), QProcess.ProcessState.NotRunning)
        output = bytes(process.readAllStandardOutput()).decode()
        self.assertEqual(process.exitCode(), 0, bytes(process.readAllStandardError()).decode())
        result = json.loads(output)
        self.assertNotIn('token', result['config'])
        self.assertNotIn(self.source['token'], output)
        self.assertEqual(result['start'], {'ok': True, 'matched': 1})
        self.assertFalse(result['wrong']['ok'])
        self.assertTrue(result['stop']['ok'])
        self.assertFalse(self.target.pet.notifications)
        self.assertEqual([entry['active'] for entry in self.hub.log], [True, False])


if __name__ == '__main__':
    unittest.main()
