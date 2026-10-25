import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_TEST_ROOT = tempfile.TemporaryDirectory(prefix='chibi-events-tests-')
os.environ['XDG_CONFIG_HOME'] = _TEST_ROOT.name + '/config'
os.environ['XDG_DATA_HOME'] = _TEST_ROOT.name + '/data'

from PySide6.QtCore import QSettings
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from desktop_chibi.app import Settings
from desktop_chibi.discord_rpc import DiscordRPC
from desktop_chibi.event_adapters import NotificationAdapter
from desktop_chibi.event_ui import EventDialog
from desktop_chibi.events import validate_config

APP = QApplication.instance() or QApplication([])


class EventTests(unittest.TestCase):
    def setUp(self):
        QSettings('DesktopChibi', 'DesktopChibi').clear()
        self.temp = tempfile.TemporaryDirectory(prefix='chibi-ipc-')
        self.socket_path = 'chibi-test-' + uuid.uuid4().hex if os.name == 'nt' else str(Path(self.temp.name) / 'events.sock')
        self.patch = patch('desktop_chibi.events.socket_name', return_value=self.socket_path)
        self.patch.start()
        self.settings = Settings()
        self.manager = self.settings.manager
        self.manager.set_mode(self.manager.selected.identity, 'functional')
        self.hub = self.settings.event_hub
        self.pet = self.settings.pet
        self.pet.timer.stop()
        self.settings.preview_timer.stop()
        self.pet.place(on_floor=True)

    def tearDown(self):
        self.settings.shutdown()
        self.settings.hide()
        self.settings.tray.hide()
        self.settings.deleteLater()
        APP.processEvents()
        QSettings('DesktopChibi', 'DesktopChibi').clear()
        self.patch.stop()
        self.temp.cleanup()

    def rule(self, source='code', event='code.done', **kwargs):
        return {'id': 'test-rule', 'enabled': True, 'source': source, 'event': event,
                'target': self.manager.selected.identity, 'animation': 'success',
                'subject': '', 'mode': 'pulse', 'duration': 4, 'cooldown': 2, **kwargs}

    def enable_local(self, rules=None, source='code'):
        sources = copy.deepcopy(self.hub.sources)
        next(s for s in sources if s['id'] == source)['enabled'] = True
        self.hub.configure(sources, rules or [self.rule()])

    def packet(self, **overrides):
        return {'source': 'code', 'event': 'code.done', 'token': self.hub.source('code')['token'], **overrides}

    def send_socket(self, packet):
        socket = QLocalSocket()
        socket.connectToServer(self.socket_path)
        for _ in range(50):
            APP.processEvents()
            if socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
                break
            QTest.qWait(5)
        self.assertEqual(socket.state(), QLocalSocket.LocalSocketState.ConnectedState)
        socket.write(json.dumps(packet).encode() + b'\n')
        data = bytearray()
        for _ in range(100):
            APP.processEvents()
            data.extend(bytes(socket.readAll()))
            if b'\n' in data:
                break
            QTest.qWait(5)
        socket.abort()
        return json.loads(data)

    def test_permissions_default_off_and_packets_fail_closed(self):
        self.assertTrue(all(not source['enabled'] for source in self.hub.sources))
        self.assertFalse(self.hub.server.isListening())
        self.assertFalse(self.hub.receive_local(self.packet())['ok'])
        self.enable_local()
        for packet in [self.packet(token='wrong'), self.packet(source='discord'),
                       self.packet(event='voice.speaking'), self.packet(body='PRIVATE TEXT'),
                       self.packet(active='true')]:
            self.assertFalse(self.hub.receive_local(packet)['ok'])
        self.assertTrue(self.hub.receive_local(self.packet())['ok'])
        self.assertEqual(self.pet.state, 'success')
        self.assertNotIn(self.hub.source('code')['token'], json.dumps(list(self.hub.log)))
        self.assertNotIn('PRIVATE TEXT', json.dumps(list(self.hub.log)))

    def test_real_ipc_and_revoke(self):
        self.enable_local()
        self.assertTrue(self.send_socket(self.packet())['ok'])
        self.assertFalse(self.send_socket(self.packet(token='old-token'))['ok'])
        sources = copy.deepcopy(self.hub.sources)
        sources[0]['enabled'] = False
        self.hub.configure(sources, self.hub.rules)
        self.assertFalse(self.hub.server.isListening())
        self.assertFalse(self.pet.notifications)
        self.assertFalse(self.hub.receive_local(self.packet())['ok'])

    def test_cooldown_subject_filters_and_hidden_pet(self):
        self.enable_local([self.rule(subject='task-a')])
        self.pet.hide()
        self.assertEqual(self.hub.dispatch('code', 'code.done', 'other', now=10), 0)
        self.assertEqual(self.hub.dispatch('code', 'code.done', 'task-a', now=10), 1)
        self.assertFalse(self.pet.isVisible())
        self.assertEqual(self.hub.dispatch('code', 'code.done', 'task-a', now=11), 0)
        self.assertEqual(self.hub.dispatch('code', 'code.done', 'task-a', now=13), 1)

    def test_held_speakers_stop_independently_and_expire(self):
        rule = self.rule('custom', 'voice.speaking', animation='talk', mode='hold', duration=10)
        self.enable_local([rule], source='custom')
        self.hub.dispatch('custom', 'voice.speaking', 'a', now=10)
        self.hub.dispatch('custom', 'voice.speaking', 'b', now=12)
        self.hub.dispatch('custom', 'voice.speaking', 'a', False, now=13)
        self.assertEqual(self.pet.notification_state(14), 'talk')
        self.assertEqual(self.pet.notification_state(23), None)
        self.hub.dispatch('custom', 'voice.speaking', 'a', now=30)
        self.hub.dispatch('custom', 'voice.speaking', 'a', now=38)
        self.assertEqual(self.pet.notification_state(45), 'talk')
        self.assertEqual(self.pet.notification_state(49), None)

    def test_fall_and_drag_have_priority(self):
        self.enable_local()
        now = time.monotonic()
        self.pet.motion.y -= 100
        self.pet.motion.airborne = True
        self.hub.dispatch('code', 'code.done', now=now)
        self.pet.advance(.016, now + .016)
        self.assertEqual(self.pet.state, 'fall')
        self.pet.place(on_floor=True)
        self.pet.advance(.016, now + .1)
        self.assertEqual(self.pet.state, 'success')
        self.pet.drag_offset = object()
        self.pet.tick()
        self.assertEqual(self.pet.state, 'drag')
        self.pet.drag_offset = None

    def test_companions_are_independent_and_rules_are_targeted(self):
        original = self.manager.selected
        self.manager.add()
        clone = self.manager.selected
        clone.pet.timer.stop()
        self.settings.speed.setValue(100)
        self.settings.pet_size.setValue(192)
        self.manager.rename('Второй')
        self.assertEqual(original.controller.speed.value(), 45)
        self.assertEqual(clone.controller.speed.value(), 100)
        self.enable_local([self.rule(target=clone.identity)])
        self.hub.dispatch('code', 'code.done')
        self.assertEqual(clone.pet.state, 'success')
        self.assertFalse(original.pet.notifications)
        self.manager.select(0)
        self.assertEqual(self.settings.speed.value(), 45)
        self.manager.select(1)
        self.assertEqual(self.settings.pet_size.value(), 192)
        self.manager.persist()
        saved = json.loads(self.settings.store.value('companions_v1'))
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[1]['name'], 'Второй')
        self.manager.remove()
        self.assertEqual(len(self.manager.items), 1)
        self.assertEqual(self.hub.rules, [])

    def test_companions_restore_visibility_and_settings(self):
        self.manager.add()
        self.manager.selected.pet.timer.stop()
        self.settings.speed.setValue(99)
        self.settings.toggle_pet()
        identity = self.manager.selected.identity
        self.manager.persist()
        restored = Settings()
        try:
            restored.preview_timer.stop()
            self.assertEqual(len(restored.manager.items), 2)
            companion = restored.manager.find(identity)
            self.assertEqual(companion.controller.speed.value(), 99)
            self.assertFalse(companion.pet.isVisible())
            self.assertEqual(restored.manager.selected.identity, identity)
        finally:
            restored.shutdown()
            restored.tray.hide()
            restored.hide()
            restored.deleteLater()
            APP.processEvents()

    def test_pulse_plays_whole_clip_but_hold_keeps_its_timeout(self):
        from desktop_chibi.animations import default_notification
        spec = default_notification()
        self.pet.load_character('', {'notify': spec})
        self.pet.trigger_notification('pulse', 'notify', 4, 10)
        self.assertEqual(self.pet.notifications['pulse']['until'], 14.75)
        self.pet.clear_notifications()
        self.pet.trigger_notification('hold', 'notify', 1, 10, restart=False)
        self.assertEqual(self.pet.notifications['hold']['until'], 11)
        self.pet.clear_notifications()
        self.pet.load_character('', {'notify': {**spec, 'pingpong': True}})
        self.pet.trigger_notification('pulse', 'notify', 4, 10)
        self.assertEqual(self.pet.notifications['pulse']['until'], 19.25)

    def test_pulse_waits_for_landing_without_losing_animation_time(self):
        from desktop_chibi.animations import default_notification
        self.pet.load_character('', {'notify': default_notification()})
        self.pet.motion.airborne = True
        self.pet.trigger_notification('pulse', 'notify', 4, 10)
        self.assertIsNone(self.pet.notification_state(20))
        self.pet.motion.airborne = False
        self.pet.landing_until = 22
        self.assertIsNone(self.pet.notification_state(21))
        self.assertEqual(self.pet.notification_state(22), 'notify')
        self.assertEqual(self.pet.notifications['pulse']['until'], 26.75)

    def test_added_companion_does_not_steal_existing_notification_rule(self):
        source = self.hub.source('notifications')
        source.update(enabled=True, apps=['Telegram Desktop'])
        target = self.manager.selected
        self.hub.rules = [self.rule('notifications', 'message.received', animation='notify')]
        self.manager.add()
        self.assertEqual(self.manager.selected.controller.mode, 'decorative')
        self.assertEqual(target.controller.mode, 'functional')
        self.assertEqual(self.hub.dispatch('notifications', 'message.received', 'Telegram Desktop', now=10), 1)
        self.assertEqual(target.pet.state, 'notify')
        self.assertEqual(self.manager.selected.pet.state, 'idle')
        self.assertEqual(self.hub.log[-1]['outcomes'][0]['target'], target.name)
        self.assertEqual(self.hub.log[-1]['outcomes'][0]['result'], 'started')
        self.assertEqual(self.hub.dispatch('notifications', 'message.received', 'Telegram Desktop', now=10.1), 0)
        self.assertEqual(self.hub.log[-1]['outcomes'][0]['result'], 'cooldown')
        self.manager.set_mode(target.identity, 'decorative')
        self.assertEqual(self.hub.dispatch('notifications', 'message.received', 'Telegram Desktop', now=20), 0)
        self.assertEqual(self.hub.log[-1]['outcomes'][0]['result'], 'decorative')

    def test_notification_filter_does_not_expose_message_text(self):
        source = self.hub.source('notifications')
        source.update(enabled=True, apps=['Telegram'])
        self.hub.rules = [self.rule('notifications', 'message.received', animation='notify')]
        adapter = NotificationAdapter(self.hub, source)
        adapter.feed('method call x interface=org.freedesktop.Notifications; member=Notify\n   string "Telegram"\n   uint32 0\n   string "icon"\n   string "PRIVATE TITLE"\n   string "PRIVATE MESSAGE"\n')
        self.assertEqual(self.pet.state, 'notify')
        self.assertEqual(self.hub.log[-1]['subject'], 'Telegram')
        self.assertNotIn('PRIVATE', json.dumps(list(self.hub.log)))
        count = len(self.hub.log)
        adapter.feed('method call x member=Notify\n   string "Unknown"\n')
        self.assertEqual(len(self.hub.log), count)
        adapter.deleteLater()

    def test_arrpc_is_reported_without_sending_oauth_token(self):
        source = self.hub.source('discord')
        adapter = DiscordRPC(self.hub, source, 'private-oauth')
        with patch.object(adapter, 'command') as command:
            adapter.handle({'evt': 'READY', 'data': {'user': {'id': '1045800378228281345', 'username': 'arrpc'}}})
            command.assert_not_called()
        self.assertIn('arRPC', self.hub.status['discord'])
        self.assertIn('Vesktop', self.hub.status['discord'])
        self.assertEqual(adapter.token, '')
        self.assertFalse(adapter.timeout.isActive())
        adapter.deleteLater()

    def test_discord_timeout_explains_protocol_stage(self):
        adapter = DiscordRPC(self.hub, self.hub.source('discord'), 'private-oauth')
        adapter.phase = 'auth'
        adapter.timed_out()
        self.assertIn('OAuth', self.hub.status['discord'])
        self.assertNotIn('private-oauth', self.hub.status['discord'])
        adapter.deleteLater()

    def test_discord_rpc_authorizes_and_filters_speakers(self):
        source = self.hub.source('discord')
        source.update(enabled=True, client_id='123', channel_id='456', users=['789'])
        self.hub.rules = [self.rule('discord', 'voice.speaking', animation='talk', mode='hold', duration=10)]
        adapter = DiscordRPC(self.hub, source, 'oauth-secret')
        sent = []
        adapter.send = lambda opcode, data: sent.append((opcode, data))
        adapter.handle({'evt': 'READY'})
        self.assertEqual(sent[-1][1]['cmd'], 'AUTHENTICATE')
        adapter.handle({'nonce': sent[-1][1]['nonce'], 'data': {}})
        self.assertTrue(adapter.authenticated)
        adapter.handle({'nonce': sent[-1][1]['nonce'], 'data': {'id': '456'}})
        self.assertEqual(adapter.channel, '456')
        self.assertEqual(sent[-1][1]['evt'], 'SPEAKING_STOP')
        adapter.handle({'evt': 'SPEAKING_START', 'data': {'user_id': '000'}})
        self.assertFalse(self.pet.notifications)
        adapter.handle({'evt': 'SPEAKING_START', 'data': {'user_id': '789'}})
        self.assertEqual(self.pet.state, 'talk')
        adapter.handle({'evt': 'SPEAKING_STOP', 'data': {'user_id': '789'}})
        self.assertFalse(self.pet.notifications)
        self.assertNotIn('oauth-secret', self.settings.store.value('events_v1', ''))
        adapter.stop()
        adapter.deleteLater()

    def test_cli_completion_hook_preserves_exit_status(self):
        self.enable_local([self.rule(event='code.failed', animation='error')])
        env = dict(os.environ, CHIBI_EVENT_TOKEN=self.hub.source('code')['token'])
        # Use the test server path in the separate CLI process without touching the real socket.
        script = 'import sys; from desktop_chibi import emit; target=sys.argv.pop(1); emit.socket_name=lambda:target; raise SystemExit(emit.main())'
        process = subprocess.Popen([sys.executable, '-c', script, self.socket_path,
                                   '--source', 'code', '--token-env', '--run', sys.executable, '-c', 'raise SystemExit(7)'],
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(400):
            APP.processEvents()
            if process.poll() is not None:
                break
            QTest.qWait(5)
        stdout, stderr = process.communicate(timeout=2)
        self.assertEqual(process.returncode, 7, stderr.decode())
        self.assertEqual(self.pet.state, 'error')

    def test_event_dialog_cancel_keeps_permissions_off(self):
        dialog = EventDialog(self.hub, self.settings)
        dialog.source_enabled.setChecked(True)
        dialog.add_rule()
        dialog.reject()
        self.assertTrue(all(not source['enabled'] for source in self.hub.sources))
        self.assertFalse(self.hub.rules)
        dialog.deleteLater()

    def test_invalid_config_rejected(self):
        for mutation in [lambda s: s[0].update(token='short'),
                         lambda s: s[2].update(apps=["bad'arg"]),
                         lambda s: s[0].update(events=['bad event']),
                         lambda s: s[3].update(users=['not-a-user-id'])]:
            sources = copy.deepcopy(self.hub.sources)
            mutation(sources)
            with self.assertRaises(ValueError):
                validate_config(sources, [])

    def test_shutdown_is_idempotent_and_keeps_visible_pet_visible_on_restart(self):
        self.assertTrue(self.pet.isVisible())
        self.settings.shutdown()
        first = self.settings.store.value('companions_v1')
        self.assertTrue(json.loads(first)[0]['visible'])
        self.settings.shutdown()
        self.assertEqual(self.settings.store.value('companions_v1'), first)

    def test_dialog_saves_rules_and_keeps_oauth_token_out_of_settings(self):
        dialog = EventDialog(self.hub, self.settings)
        dialog.source_enabled.setChecked(True)
        dialog.add_rule()
        dialog.rule_animation.setCurrentIndex(dialog.rule_animation.findData('success'))
        dialog.source_list.setCurrentRow(3)
        dialog.source_fields['token'].setText('session-only-oauth')
        dialog.save()
        self.assertTrue(self.hub.source('code')['enabled'])
        self.assertTrue(self.hub.server.isListening())
        self.assertEqual(self.hub.rules[0]['animation'], 'success')
        self.assertEqual(self.hub.discord_token, 'session-only-oauth')
        self.assertNotIn('session-only-oauth', self.settings.store.value('events_v1'))
        dialog.deleteLater()


if __name__ == '__main__':
    unittest.main()
