"""A single adaptive clock for all companions, with explicit workload weights."""
import time
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication
from .animations import release_frame_cache

MAX_COMPANIONS = 32
MODES = {'decorative': ('Декоративный', 1), 'functional': ('С событиями', 3)}
SOFT_BUDGET = 64


class PetClock:
    """Compatibility handle: stopping it disables only this companion."""
    def __init__(self, scheduler, pet):
        self.scheduler, self.pet = scheduler, pet
        self.enabled = True
        self.last_run = 0.0
        self.hidden = False

    def stop(self):
        self.enabled = False
        self.pet.animations.suspend()

    def start(self, interval=None):
        self.enabled = True
        self.last_run = 0
        self.scheduler.wake()

    def isActive(self):
        return self.enabled

    def interval(self):
        return round(1000 / self.scheduler.fps(self.pet))


class PetScheduler(QObject):
    def __init__(self, manager):
        super().__init__(manager)
        self.manager = manager
        self.handles = {}
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.stopped = False

    def attach(self, pet):
        pet.timer.stop()
        pet.timer.deleteLater()
        handle = PetClock(self, pet)
        pet.timer = handle
        self.handles[pet] = handle
        return handle

    def detach(self, pet):
        handle = self.handles.pop(pet, None)
        if handle:
            handle.stop()

    def wake(self):
        if not self.stopped:
            self.timer.setInterval(16)
            if not self.timer.isActive():
                self.timer.start()

    def fps(self, pet, crowded=None):
        if pet.drag_offset is not None or pet.motion.airborne or time.monotonic() < pet.landing_until:
            return 60
        mode = getattr(pet.controller, 'mode', 'functional')
        profile = self.manager.performance_mode
        if profile == 'eco':
            return 20 if mode == 'functional' or pet.notifications else 10
        if profile == 'smooth':
            return 60 if mode == 'functional' or pet.notifications else 30
        crowded = self.manager.active_weight > SOFT_BUDGET if crowded is None else crowded
        return (20 if crowded else 30) if mode == 'functional' or pet.notifications else (10 if crowded else 15)

    def tick(self):
        now = time.monotonic()
        visible = False
        next_due = .25
        owner = self.manager.owner
        crowded = self.manager.active_weight > SOFT_BUDGET
        for pet, handle in tuple(self.handles.items()):
            if not handle.enabled:
                continue
            if not pet.isVisible():
                handle.hidden = True
                if not (owner.isVisible() and owner.pet is pet and owner.preview.isVisible()):
                    pet.animations.suspend()
                continue
            visible = True
            if (pet.option('game_overlay') and QApplication.platformName() == 'xcb'
                    and now - getattr(handle, 'last_raise', 0) >= 2):
                self.manager.schedule_stacking()
                handle.last_raise = now
            if handle.hidden:
                pet.last_tick = now
                handle.hidden = False
                handle.last_run = 0
            period = 1 / self.fps(pet, crowded)
            if now - handle.last_run + .001 >= period:
                handle.last_run = now if now - handle.last_run > period * 2 else handle.last_run + period
                pet.tick()
            next_due = min(next_due, max(.004, period - (now - handle.last_run)))
        # Wake at the next actual companion deadline, not 60 times/sec for a 10 fps pet.
        if visible:
            self.timer.setInterval(max(4, round(next_due * 1000)))
        else:
            self.timer.stop()
            release_frame_cache()

    def shutdown(self):
        self.stopped = True
        self.timer.stop()
        for handle in self.handles.values():
            handle.stop()
