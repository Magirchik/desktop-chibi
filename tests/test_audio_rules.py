import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_ROOT = tempfile.TemporaryDirectory(prefix='chibi-audio-tests-')
os.environ['XDG_CONFIG_HOME'] = _ROOT.name + '/config'
os.environ['XDG_DATA_HOME'] = _ROOT.name + '/data'
from PySide6.QtCore import QObject, QSettings, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from desktop_chibi.app import Settings
from desktop_chibi.audio_adapter import AudioAdapter
from desktop_chibi.event_ui import EventDialog
from desktop_chibi.events import validate_config
from desktop_chibi.quick_setup import QuickSetup

APP = QApplication.instance() or QApplication([])


class Hub(QObject):
    def __init__(self):
        super().__init__()
        self.events, self.statuses = [], []
    def dispatch(self, *args, **kwargs):
        self.events.append((args, kwargs))
    def set_status(self, *args):
        self.statuses.append(args)


def node(identity=1, state='running', binary='firefox', **volume):
    return {'id': identity, 'type': 'PipeWire:Interface:Node', 'info': {'state': state,
            'props': {'application.process.binary': binary, 'media.class': 'Stream/Output/Audio'},
            'params': {'Props': [dict(mute=False, channelVolumes=[1, 1]) | volume]}}}


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.hub = Hub()
        self.adapter = AudioAdapter(self.hub, {'id': 'audio', 'apps': ['firefox']})
    def tearDown(self):
        self.adapter.stop()
        self.hub.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_fragmented_json_start_pause_resume_mute_and_stop(self):
        packet = json.dumps([node()])
        for offset in range(0, len(packet), 7):
            self.adapter.feed(packet[offset:offset + 7])
        self.assertEqual(self.hub.events[-1][0], ('audio', 'audio.playing', 'firefox', True))
        self.adapter.feed(json.dumps([{'id': 1, 'info': {'state': 'idle'}}]))
        self.assertFalse(self.hub.events[-1][0][-1])
        self.adapter.feed(json.dumps([{'id': 1, 'info': {'state': 'running'}}]))
        self.assertTrue(self.hub.events[-1][0][-1])
        self.adapter.feed(json.dumps([{'id': 1, 'info': {'params': {'Props': [{'mute': True}]}}}]))
        self.assertFalse(self.hub.events[-1][0][-1])
        self.adapter.feed(json.dumps([{'id': 1, 'info': {'params': {'Props': [{'mute': False, 'channelVolumes': [0, 0]}]}}}]))
        self.assertFalse(self.adapter.active)
        self.adapter.feed(json.dumps([{'id': 1, 'info': {'params': {'Props': [{'channelVolumes': [1, 1]}]}}}]))
        self.assertTrue(self.hub.events[-1][0][-1])
        self.adapter.feed(json.dumps([{'id': 1, 'info': None}]))
        self.assertFalse(self.hub.events[-1][0][-1])

    def test_multiple_streams_ignore_microphone_other_apps_and_content(self):
        other = node(2, binary='other')
        microphone = node(3)
        microphone['info']['props']['media.class'] = 'Stream/Input/Audio'
        one = node()
        one['info']['props']['media.name'] = 'PRIVATE VIDEO TITLE'
        self.adapter.feed(json.dumps([one, other, microphone, node(4)]))
        self.assertEqual(len(self.hub.events), 1)
        self.assertEqual(set(self.adapter.nodes), {1, 4})
        self.assertNotIn('PRIVATE', repr(self.adapter.nodes))
        self.adapter.feed(json.dumps([{'id': 1, 'info': None}]))
        self.assertEqual(len(self.hub.events), 1)
        self.adapter.feed(json.dumps([{'id': 4, 'info': None}]))
        self.assertFalse(self.hub.events[-1][0][-1])

    def test_heartbeat_refreshes_without_log_and_failure_clears_activity(self):
        self.adapter.apply_batch([node()])
        self.adapter.refresh()
        self.assertEqual(self.hub.events[-1][1], {'record': False, 'renew_only': True})
        self.adapter.failed()
        self.assertFalse(self.hub.events[-1][0][-1])
        self.assertFalse(self.adapter.active)


class ScopedRuleTests(unittest.TestCase):
    def setUp(self):
        QSettings('DesktopChibi', 'DesktopChibi').clear()
        self.settings = Settings()
        self.manager = self.settings.manager
        self.hub = self.settings.event_hub
        self.first = self.manager.selected
        self.manager.set_mode(self.first.identity, 'functional')
        self.manager.add('functional')
        self.second = self.manager.selected
        self.hub.source('audio').update(enabled=True, apps=['firefox'])
    def tearDown(self):
        self.settings.shutdown()
        self.settings.tray.hide()
        self.settings.hide()
        self.settings.deleteLater()
        APP.processEvents()
        QSettings('DesktopChibi', 'DesktopChibi').clear()
    def rule(self, identity, scope='personal', target=None, animation='talk', mode='hold'):
        return dict(id=identity, scope=scope, target=target or self.first.identity, source='audio',
                    event='audio.playing', subject='firefox', animation=animation, mode=mode,
                    enabled=True, duration=6, cooldown=2)

    def test_global_broadcast_and_personal_override_and_stop(self):
        common = self.rule('all', 'global', target='*')
        private = self.rule('one', target=self.first.identity, animation='notify')
        self.hub.rules = [common, private]
        self.assertEqual(self.hub.dispatch('audio', 'audio.playing', 'firefox', now=10), 2)
        self.assertEqual(self.first.pet.state, 'notify')
        self.assertEqual(self.second.pet.state, 'talk')
        self.assertNotIn('all:firefox', self.first.pet.notifications)
        self.assertIn('all:firefox', self.second.pet.notifications)
        self.hub.dispatch('audio', 'audio.playing', 'firefox', False, now=11)
        self.assertFalse(self.first.pet.notifications)
        self.assertFalse(self.second.pet.notifications)

    def test_common_cooldown_is_independent_for_each_target_and_decorative_ignored(self):
        self.hub.rules = [self.rule('all', 'global', target='*', mode='pulse')]
        self.assertEqual(self.hub.dispatch('audio', 'audio.playing', 'firefox', now=10), 2)
        self.assertEqual(self.hub.dispatch('audio', 'audio.playing', 'firefox', now=10.1), 0)
        self.manager.set_mode(self.second.identity, 'decorative')
        self.assertEqual(self.hub.dispatch('audio', 'audio.playing', 'firefox', now=20), 1)

    def test_dialog_filters_owners_and_global_and_saves_all_without_loss(self):
        rules = [self.rule('one'), self.rule('two', target=self.second.identity), self.rule('all', 'global', target='*')]
        self.hub.rules = copy.deepcopy(rules)
        dialog = EventDialog(self.hub)
        self.assertEqual(dialog.rule_list.count(), 1)
        self.assertEqual(dialog.rules[dialog.rule_index]['id'], 'two')
        dialog.rule_owner.setCurrentIndex(dialog.rule_owner.findData(self.first.identity))
        self.assertEqual(dialog.rules[dialog.rule_index]['id'], 'one')
        dialog.rule_animation.setCurrentIndex(dialog.rule_animation.findData('success'))
        dialog.rule_scope_tabs.setCurrentIndex(1)
        self.assertEqual(dialog.rule_list.count(), 1)
        self.assertEqual(dialog.rules[dialog.rule_index]['id'], 'all')
        dialog.rule_animation.setCurrentIndex(dialog.rule_animation.findData('error'))
        dialog.rule_scope_tabs.setCurrentIndex(0)
        with patch.object(AudioAdapter, 'start'):
            dialog.save()
        self.assertEqual(len(self.hub.rules), 3)
        self.assertEqual(next(r for r in self.hub.rules if r['id'] == 'one')['animation'], 'success')
        self.assertEqual(next(r for r in self.hub.rules if r['id'] == 'two'), rules[1])
        self.assertEqual(next(r for r in self.hub.rules if r['id'] == 'all')['animation'], 'error')
        dialog.deleteLater()

    def test_audio_wizard_defaults_to_talk_hold_and_keeps_other_rules(self):
        self.hub.rules = [self.rule('one')]
        wizard = QuickSetup(self.settings, 'audio')
        self.assertEqual(wizard.fields['app'].text(), 'firefox')
        self.assertEqual(wizard.notification_animation.currentData(), 'talk')
        sources, rules = wizard.prepare(self.second.identity)
        self.assertTrue(next(s for s in sources if s['id'] == 'audio')['enabled'])
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[-1]['mode'], 'hold')
        self.assertEqual(rules[-1]['event'], 'audio.playing')
        validate_config(sources, rules)
        wizard.reject(); wizard.deleteLater()

    def test_transfer_rule_between_scopes_keeps_other_rules_and_identity(self):
        self.hub.rules = [self.rule('one'), self.rule('two', target=self.second.identity)]
        dialog = EventDialog(self.hub)
        dialog.move_scope_button.click()
        self.assertEqual(dialog.rule_scope_tabs.currentIndex(), 1)
        moved = next(r for r in dialog.rules if r['id'] == 'two')
        self.assertEqual((moved['scope'], moved['target']), ('global', '*'))
        self.assertEqual(next(r for r in dialog.rules if r['id'] == 'one')['target'], self.first.identity)
        dialog.move_scope_button.click()
        self.assertEqual((moved['scope'], moved['target']), ('personal', self.second.identity))
        dialog.reject(); dialog.deleteLater()

    def test_keepalive_does_not_restart_pulse_rules(self):
        self.hub.rules = [self.rule('pulse', mode='pulse')]
        self.hub.dispatch('audio', 'audio.playing', 'firefox', now=10)
        until = self.first.pet.notifications['pulse:firefox']['until']
        self.hub.dispatch('audio', 'audio.playing', 'firefox', now=15, record=False, renew_only=True)
        self.assertEqual(self.first.pet.notifications['pulse:firefox']['until'], until)

    def test_audio_fallback_keeps_event_only_avatar_instead_of_becoming_bunny(self):
        from pathlib import Path
        from PySide6.QtGui import QImage, QColor
        image = QImage(32, 32, QImage.Format.Format_ARGB32)
        image.fill(QColor('red'))
        path = Path(_ROOT.name) / 'event-avatar.png'
        image.save(str(path))
        self.first.pet.load_character(str(path), {'notify': {'file': str(path)}})
        frame = self.first.pet.current_frame('talk', .1).toImage()
        self.assertEqual(frame.pixelColor(2, 20), QColor('red'))
        self.assertNotEqual(frame, self.first.pet.current_frame('talk', .3).toImage())

    def test_legacy_config_adds_disabled_audio_and_preserves_personal_rules(self):
        sources = [s for s in self.hub.sources if s['id'] != 'audio']
        legacy = self.rule('legacy'); legacy.pop('scope'); legacy.update(source='custom', event='voice.speaking')
        self.settings.store.setValue('events_v1', json.dumps({'sources': sources, 'rules': [legacy]}))
        from desktop_chibi.events import EventHub
        loaded = EventHub(self.manager, self.settings.store)
        self.assertFalse(loaded.source('audio')['enabled'])
        self.assertEqual(loaded.rules, [legacy])
        loaded.deleteLater()


if __name__ == '__main__':
    unittest.main()
