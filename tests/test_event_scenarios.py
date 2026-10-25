import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_ROOT = tempfile.TemporaryDirectory(prefix='chibi-scenarios-')
os.environ['XDG_CONFIG_HOME'] = _ROOT.name + '/config'
os.environ['XDG_DATA_HOME'] = _ROOT.name + '/data'

from PySide6.QtCore import QSettings, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from desktop_chibi.app import Settings
from desktop_chibi.event_ui import EventDialog
from desktop_chibi.quick_setup import QuickSetup
from desktop_chibi.events import validate_config
from desktop_chibi.event_sound import EventSound, validate_sound
from desktop_chibi.timed_events import TimedEvents

APP = QApplication.instance() or QApplication([])


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        QSettings('DesktopChibi', 'DesktopChibi').clear()
        self.settings = Settings()
        self.hub, self.manager = self.settings.event_hub, self.settings.manager
        self.first = self.manager.selected
        self.manager.set_mode(self.first.identity, 'functional')

    def tearDown(self):
        self.settings.shutdown()
        self.settings.tray.hide()
        self.settings.hide()
        self.settings.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QSettings('DesktopChibi', 'DesktopChibi').clear()

    def rule(self, mode='pulse', scope='personal'):
        return dict(id='sound-test', enabled=True, scope=scope, target='*' if scope == 'global' else self.first.identity,
                    source='custom', event='custom', subject='', animation='notify', mode=mode, duration=4,
                    cooldown=2, sound=dict(enabled=True, file='/tmp/test-sound.wav', volume=35, when_hidden=False))

    def test_sound_cooldown_stop_and_hidden_opt_in(self):
        self.hub.source('custom')['enabled'] = True
        rule = self.rule()
        self.hub.rules = [rule]
        with patch.object(self.hub.sound, 'play') as play:
            self.hub.dispatch('custom', 'custom', now=10)
            play.assert_called_once_with(rule['sound'])
            self.hub.dispatch('custom', 'custom', now=11)
            self.hub.dispatch('custom', 'custom', active=False, now=12)
            self.assertEqual(play.call_count, 1)
            self.first.pet.hide()
            self.hub.dispatch('custom', 'custom', now=20)
            self.assertEqual(play.call_count, 1)
            rule['sound']['when_hidden'] = True
            self.hub.dispatch('custom', 'custom', now=30)
            self.assertEqual(play.call_count, 2)

    def test_hold_keepalive_never_replays_sound_but_next_activation_does(self):
        self.hub.source('custom')['enabled'] = True
        self.hub.rules = [self.rule(mode='hold')]
        with patch.object(self.hub.sound, 'play') as play:
            self.hub.dispatch('custom', 'custom', now=10)
            self.hub.dispatch('custom', 'custom', now=11)
            self.hub.dispatch('custom', 'custom', now=12, renew_only=True)
            self.assertEqual(play.call_count, 1)
            self.hub.dispatch('custom', 'custom', active=False, now=13)
            self.hub.dispatch('custom', 'custom', now=14)
            self.assertEqual(play.call_count, 2)

    def test_global_sound_once_and_personal_rule_override(self):
        self.manager.add('functional')
        self.hub.source('custom')['enabled'] = True
        global_rule = self.rule(scope='global')
        self.hub.rules = [global_rule]
        with patch.object(self.hub.sound, 'play') as play:
            self.assertEqual(self.hub.dispatch('custom', 'custom', now=10), 2)
            self.assertEqual(play.call_count, 1)
        personal = self.rule()
        personal['id'] = 'personal'
        personal.pop('sound')
        self.hub.rules = [global_rule, personal]
        self.manager.selected.pet.hide()
        with patch.object(self.hub.sound, 'play') as play:
            self.hub.dispatch('custom', 'custom', now=20)
            play.assert_not_called()

    def test_three_timer_scenarios_and_no_change_until_save(self):
        for scenario in ('reminder', 'timer', 'focus'):
            wizard = QuickSetup(self.settings, scenario)
            try:
                wizard.minutes.setValue(7)
                wizard.break_minutes.setValue(2)
                sources, rules = wizard.prepare(self.first.identity)
                self.assertFalse(self.hub.source(scenario)['enabled'])
                source = next(s for s in sources if s['id'] == scenario)
                self.assertTrue(source['enabled'])
                self.assertEqual(source['seconds'], 420)
                self.assertEqual(source['break_seconds'], 120)
                self.assertEqual(len(rules), 2 if scenario == 'focus' else 1)
                validate_config(sources, rules)
            finally:
                wizard.reject()
                wizard.deleteLater()

    def test_timer_repeat_focus_once_and_no_catchup_burst(self):
        for identity, expected in [('reminder', ['timer.done', 'timer.done']),
                                   ('timer', ['timer.done']), ('focus', ['focus.break', 'focus.work'])]:
            adapter = TimedEvents(self.hub, copy.deepcopy(self.hub.source(identity)))
            now = [100.0]
            try:
                with patch('desktop_chibi.timed_events.time.monotonic', side_effect=lambda: now[0]), patch.object(self.hub, 'dispatch') as dispatch:
                    adapter.start()
                    adapter.fire()  # early wake cannot fire an event
                    dispatch.assert_not_called()
                    now[0] = adapter.deadline + 20000  # sleep/resume: one event, no missed-event loop
                    adapter.fire()
                    self.assertEqual(dispatch.call_count, 1)
                    if identity == 'timer':
                        self.assertFalse(adapter.timer.isActive())
                    else:
                        self.assertGreater(adapter.deadline, now[0])
                        now[0] = adapter.deadline
                        adapter.fire()
                    self.assertEqual([call.args[1] for call in dispatch.call_args_list], expected)
            finally:
                adapter.stop()
                adapter.deleteLater()

    def test_rule_sound_round_trip_and_cancel_without_changes(self):
        rule = self.rule()
        self.hub.rules = [rule]
        dialog = EventDialog(self.hub)
        try:
            self.assertEqual(dialog.rule_sound.value(), rule['sound'])
            dialog.rule_sound.volume.setValue(12)
            dialog.capture_rule()
            self.assertEqual(dialog.rules[0]['sound']['volume'], 12)
            self.assertEqual(self.hub.rules[0]['sound']['volume'], 35)
            with patch.object(self.hub, 'restart_adapters'):
                dialog.save()
            self.assertEqual(self.hub.rules[0]['sound']['volume'], 12)
        finally:
            dialog.deleteLater()

    def test_invalid_sound_and_timer_config_rejected(self):
        for sound in ({'enabled': 'true'}, {'volume': 101}, {'enabled': True, 'file': ''}, {'volume': float('nan')}):
            with self.assertRaises(ValueError):
                validate_sound(sound)
        sources = copy.deepcopy(self.hub.sources)
        next(s for s in sources if s['id'] == 'timer')['seconds'] = 0
        with self.assertRaises(ValueError):
            validate_config(sources, [])

    def test_wizard_can_disable_existing_sound_and_legacy_config_gains_disabled_timers(self):
        import json
        from desktop_chibi.events import EventHub
        rule = self.rule()
        rule.update(source='notifications', event='message.received', subject='Test App')
        self.hub.rules = [rule]
        self.hub.source('notifications')['apps'] = ['Test App']
        wizard = QuickSetup(self.settings, 'messages')
        try:
            self.assertTrue(wizard.sound_options.enabled.isChecked())
            wizard.sound_options.enabled.setChecked(False)
            _, rules = wizard.prepare(self.first.identity)
            self.assertFalse(rules[0]['sound']['enabled'])
        finally:
            wizard.reject()
            wizard.deleteLater()
        sources = [s for s in self.hub.sources if s['kind'] != 'timer']
        self.settings.store.setValue('events_v1', json.dumps(dict(sources=sources, rules=[rule])))
        restored = EventHub(self.manager, self.settings.store)
        try:
            self.assertEqual(restored.rules, [rule])
            for identity in ('reminder', 'timer', 'focus'):
                self.assertFalse(restored.source(identity)['enabled'])
        finally:
            restored.stop()
            restored.deleteLater()


class SoundProcessTests(unittest.TestCase):
    def test_no_process_until_play_and_native_player_exits_cleanly(self):
        sound = EventSound()
        self.assertIsNone(sound.process)
        self.assertFalse(sound.timeout.isActive())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sound with spaces.wav'
            path.write_bytes(b'test')
            settings = dict(enabled=True, file=str(path), volume=40)
            process = Mock()
            with patch('desktop_chibi.event_sound.sys.platform', 'linux'), patch('desktop_chibi.event_sound.shutil.which', return_value='/usr/bin/pw-play'), patch('desktop_chibi.event_sound.QProcess', return_value=process) as cls:
                cls.nullDevice.return_value = '/dev/null'
                self.assertTrue(sound.play(settings))
                process.start.assert_called_once_with('/usr/bin/pw-play', ['--volume=0.40', str(path.resolve())])
                self.assertFalse(sound.play(settings))
                sound.finished(process, 0)
                self.assertIsNone(sound.process)
                self.assertFalse(sound.timeout.isActive())
                process.deleteLater.assert_called_once()
        sound.stop()
        sound.deleteLater()


if __name__ == '__main__':
    unittest.main()
