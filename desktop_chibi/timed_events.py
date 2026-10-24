"""Deadline-based reminders. No polling and no catch-up burst after suspend."""
import time
from PySide6.QtCore import QObject, QTimer, Qt


class TimedEvents(QObject):
    def __init__(self, hub, source):
        super().__init__(hub)
        self.hub, self.source = hub, source
        self.phase = 'work'
        self.deadline = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.fire)

    def start(self):
        self.phase = 'work'
        self.arm(self.source['seconds'])

    def arm(self, seconds):
        self.deadline = time.monotonic() + seconds
        self.timer.start(round(seconds * 1000))
        self.hub.set_status(self.source['id'], f'Запущен · следующий сигнал через {seconds / 60:g} мин.')

    def fire(self):
        # Qt timers can wake early on some platforms.
        remaining = self.deadline - time.monotonic()
        if remaining > .01:
            self.timer.start(max(1, round(remaining * 1000)))
            return
        self.timer.stop()
        mode = self.source['timer_mode']
        event = 'timer.done'
        if mode == 'focus':
            self.phase = 'break' if self.phase == 'work' else 'work'
            event = 'focus.' + self.phase
        self.hub.dispatch(self.source['id'], event)
        if mode == 'once':
            self.hub.set_status(self.source['id'], 'Таймер завершён. Сохраните настройки, чтобы запустить снова.')
        else:
            self.arm(self.source['break_seconds'] if self.phase == 'break' else self.source['seconds'])

    def stop(self):
        self.timer.stop()
