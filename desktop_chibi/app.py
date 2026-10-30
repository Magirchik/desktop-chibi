import hashlib
import json
import math
import random
import sys
import time
from pathlib import Path

from PySide6.QtCore import QRect, QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QMenuBar, QMessageBox, QInputDialog, QPushButton, QSlider, QSystemTrayIcon,
    QVBoxLayout, QWidget, QScrollArea, QPlainTextEdit,
)

from .pixel_ui import Preview, bunny_frame, panel, event_badge, apply_theme, PixelSurface, PixelTabs, PixelPreview, NoticeLabel, SystemHeading, window_bar
from .physics import Motion
from .animations import Animations, STATES, read_manifest, default_notification, cached_sheet
from .animation_editor import AnimationEditor
from .companions import CompanionManager
from .events import EventHub
from .event_ui import EventDialog
from .guides import GuideDialog
from .quick_setup import QuickSetup
from .presets import PresetLibrary

DEFAULT_SETTINGS = {
    'size': 128, 'speed': 45, 'offset': 0, 'gravity': 1200,
    'idle_interval': 14, 'idle_duration': 3,
    'walk': True, 'gravity_enabled': True, 'random_idle': True,
    'screen': 0, 'layer': 0,
    'game_overlay': False, 'hide_when_absent': False, 'pinned': False, 'click_through': False, 'area_enabled': False,
    'area_left': 0, 'area_right': 100, 'area_top': 0, 'area_bottom': 100,
}


class Pet(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setWindowTitle('Desktop Chibi — персонаж')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.motion = Motion()
        self.direction = 1
        self.phase = 0.0
        self.pause_until = 0.0
        self.drag_offset = None
        self.animations = Animations(self)
        self.notifications = {}
        self.state = 'idle'
        self.state_since = time.monotonic()
        self.landing_until = 0.0
        self.last_tick = time.monotonic()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

    @property
    def requested_visible(self):
        return getattr(self, '_wanted_visible', self.isVisible())

    def setVisible(self, visible):
        self._wanted_visible = bool(visible)
        self.refresh_visibility()

    def refresh_visibility(self):
        manager = getattr(self.controller, 'manager', None)
        controls = getattr(manager.owner, 'desktop_controls', None) if manager else None
        super().setVisible(self.requested_visible and not (
            self.option('hide_when_absent') and not getattr(self, '_friend_present', False))
            and (controls is None or controls.allows(self)))
        if manager and getattr(manager.owner, 'pet', None) is self and hasattr(manager.owner, 'visibility_action'):
            manager.owner.sync_visibility()

    def set_friend_present(self, present):
        self._friend_present = bool(present)
        self.refresh_visibility()

    @property
    def position(self):
        return self.motion.x

    @position.setter
    def position(self, value):
        self.motion.x = value

    def bounds(self):
        screens = QApplication.screens()
        index = min(self.controller.screen.currentIndex(), len(screens) - 1)
        screen = screens[max(0, index)]
        area = screen.geometry() if self.option('game_overlay') else screen.availableGeometry()
        if self.option('area_enabled'):
            left, right = sorted((self.option('area_left', 0), self.option('area_right', 100)))
            top, bottom = sorted((self.option('area_top', 0), self.option('area_bottom', 100)))
            size = self.controller.pet_size.value()
            width = min(area.width(), max(size, round(area.width() * (right - left) / 100)))
            height = min(area.height(), max(size, round(area.height() * (bottom - top) / 100)))
            x = min(area.right() - width + 1, area.left() + round(area.width() * left / 100))
            y = min(area.bottom() - height + 1, area.top() + round(area.height() * top / 100))
            return QRect(x, y, width, height)
        return area

    def option(self, key, default=False):
        widget = getattr(self.controller, key, None)
        return (widget.isChecked() if hasattr(widget, 'isChecked') else widget.value()) if widget is not None else default

    def apply_input_mode(self):
        enabled = self.input_passthrough()
        if self.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) == enabled:
            return
        visible = self.requested_visible
        self.drag_offset = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, enabled)
        self.setVisible(visible)

    def input_passthrough(self):
        manager = getattr(self.controller, 'manager', None)
        controls = getattr(manager.owner, 'desktop_controls', None) if manager else None
        if controls is not None and controls.input_override is not None:
            return controls.input_override
        return bool(self.option('click_through') or self.option('game_overlay'))

    def apply_window_mode(self):
        visible = self.requested_visible
        game = bool(self.option('game_overlay'))
        layer = self.controller.layer.currentIndex()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, self.input_passthrough())
        self.drag_offset = None
        # Use an integer mask: enum inversion can discard high-bit hints on
        # some Python/PySide combinations (notably the bottom-layer hint).
        flags = int(self.windowFlags())
        for flag, enabled in (
            (Qt.WindowType.WindowStaysOnTopHint, game or layer == 0),
            (Qt.WindowType.WindowStaysOnBottomHint, not game and layer == 2),
            (Qt.WindowType.X11BypassWindowManagerHint, game and QApplication.platformName() == 'xcb'),
            (Qt.WindowType.WindowTransparentForInput, self.input_passthrough())):
            flags = flags | int(flag) if enabled else flags & ~int(flag)
        if flags != int(self.windowFlags()):
            self.setWindowFlags(Qt.WindowType(flags))
        self.setVisible(visible)
        if game and self.isVisible():
            self.raise_()

    def floor(self, area=None):
        area = self.bounds() if area is None else area
        return max(area.top(), area.bottom() - self.height() + 1 - self.controller.offset.value())

    def place(self, on_floor=False, area=None):
        area = self.bounds() if area is None else area
        size = self.controller.pet_size.value()
        if self.width() != size or self.height() != size:
            self.resize(size, size)
        self.motion.x = max(float(area.left()), min(self.motion.x, float(max(area.left(), area.right() - size + 1))))
        if on_floor and not self.option('pinned'):
            self.motion.y = self.floor(area)
            self.motion.vy = 0
            self.motion.airborne = False
            self.landing_until = 0
            self.set_state('idle')
        bottom = max(area.top(), area.bottom() - size + 1) if self.option('pinned') else self.floor(area)
        self.motion.y = max(float(area.top()), min(self.motion.y, float(bottom)))
        x, y = round(self.motion.x), round(self.motion.y)
        if self.x() != x or self.y() != y:
            self.move(x, y)

    def set_state(self, state, now=None):
        if self.state != state:
            self.state = state
            self.state_since = now if now is not None else time.monotonic()

    def tick(self):
        now = time.monotonic()
        dt = min(now - self.last_tick, 0.1)
        self.last_tick = now
        if self.drag_offset is not None:
            self.set_state('drag', now)
            self.update()
            return
        self.advance(dt, now)

    def advance(self, dt, now):
        """One deterministic physics step; the clock is supplied for tests."""
        area = self.bounds()
        size = self.controller.pet_size.value()
        if self.width() != size or self.height() != size:
            self.resize(size, size)
        right = max(area.left(), area.right() - self.width() + 1)
        pinned = self.option('pinned')
        if pinned:
            self.motion.vy = 0
            self.motion.airborne = False
            self.landing_until = 0
        landed = False if pinned else self.motion.step(dt, area.left(), right, area.top(), self.floor(area),
                                  self.controller.gravity.value(), self.controller.gravity_enabled.isChecked())
        if landed:
            self.landing_until = now + self.animations.duration('land', 0.35)
            self.pause_until = self.landing_until + 0.2
            self.set_state('land', now)
        notification = self.notification_state(now)
        if self.motion.airborne:
            self.set_state('fall', now)
        elif now < self.landing_until:
            self.set_state('land', now)
        elif notification:
            self.set_state(notification, now)
        elif not pinned and self.controller.walk.isChecked() and now >= self.pause_until:
            self.set_state('walk', now)
            self.phase += dt * 9
            self.motion.x += self.direction * self.controller.speed.value() * dt
            if self.motion.x <= area.left() or self.motion.x >= right:
                self.direction = 1 if self.motion.x <= area.left() else -1
                self.begin_idle(now)
            elif random.random() < dt / self.controller.idle_interval.value():
                self.begin_idle(now)
        elif not self.state.startswith('idle'):
            self.begin_idle(now)
        elif ((pinned or not self.controller.walk.isChecked())
              and now - self.state_since >= self.animations.idle_duration(
                  self.state, self.controller.idle_duration.value())):
            # Also vary idle animations when walking is disabled.
            self.begin_idle(now)
        self.place(area=area)
        self.update()

    def trigger_notification(self, key, animation, duration, now, restart=True):
        if animation not in STATES and animation not in self.animations.clips:
            return False
        previous = self.notifications.get(key)
        if restart:
            minimum = 38 / 8 if animation == 'notify' and not ({'idle', 'notify'} & self.animations.clips.keys()) else 0
            duration = max(duration, self.animations.duration(animation, minimum))
        self.notifications[key] = {'animation': animation, 'until': now + duration,
                                   'started': now if restart or previous is None else previous['started']}
        if restart and self.isVisible() and (self.drag_offset is not None or self.motion.airborne or now < self.landing_until):
            self.notifications[key]['pending'] = duration
        if len(self.notifications) > 128:
            oldest = min(self.notifications, key=lambda item: self.notifications[item]['until'])
            self.notifications.pop(oldest, None)
        if ((restart or previous is None) and self.drag_offset is None and not self.motion.airborne
                and now >= self.landing_until and self.notification_state(now) == animation):
            self.set_state(animation, now)
            self.state_since = now
            self.animations.restart()
        return True

    def notification_state(self, now):
        if not self.notifications:
            return None
        ready = self.drag_offset is None and not self.motion.airborne and now >= self.landing_until
        for value in self.notifications.values():
            if 'pending' in value and ready:
                value['until'] = now + value.pop('pending')
                value['started'] = now
        self.notifications = {key: value for key, value in self.notifications.items()
                              if 'pending' in value or value['until'] > now}
        if not self.notifications:
            return None
        active = [value for value in self.notifications.values() if 'pending' not in value]
        return max(active, key=lambda value: (
            self.animations.specs.get(value['animation'], {}).get('priority', 0),
            value['started']))['animation'] if active else None

    def cancel_notification(self, key):
        if key not in self.notifications:
            return
        self.notifications.pop(key, None)
        if not self.notifications and self.drag_offset is None and not self.motion.airborne:
            self.set_state('idle')

    def clear_notifications(self):
        self.notifications.clear()
        if self.drag_offset is None and not self.motion.airborne:
            self.set_state('idle')

    def begin_idle(self, now):
        variants = ['idle'] + [key for key in self.animations.clips if key.startswith('idle_')]
        state = random.choice(variants) if self.controller.random_idle.isChecked() else 'idle'
        self.set_state(state, now)
        self.state_since = now
        self.pause_until = now + self.animations.idle_duration(state, self.controller.idle_duration.value())
        # Restart even if the same idle was randomly selected twice.
        self.animations.restart()

    def load_character(self, path, specs=None):
        if specs is None:
            if path and Path(path).suffix.lower() == '.json':
                _, specs = read_manifest(path)
            else:
                specs = {'idle': {'file': path, 'frames': 1, 'fps': 8}} if path else {}
        self.animations.configure(specs)
        self.state_since = time.monotonic()
        self.update()

    def current_frame(self, state=None, elapsed=None):
        state = state or self.state
        elapsed = max(0, time.monotonic() - self.state_since) if elapsed is None else elapsed
        frame = self.animations.frame(state, elapsed)
        if frame.isNull():
            if state == 'talk' and self.animations.clips:
                # An event-only avatar may have no idle clip. Keep its identity.
                base = next(iter(self.animations.clips.values())).frame(0)
                return event_badge(base, 'talk', elapsed)
            if state == 'notify':
                spec = default_notification()
                frames = cached_sheet(Path(spec['file']), 32, 32, 38)
                return frames[int(elapsed * 8) % len(frames)]
            # A custom character without a state keeps its base texture.
            return bunny_frame(round(math.sin(elapsed * 9)) if state == 'walk' else 0,
                               state=state, elapsed=elapsed)
        if state in ('notify', 'success', 'error', 'talk') and state not in self.animations.clips:
            return event_badge(frame, state, elapsed)
        return frame

    def paintEvent(self, event):
        p = QPainter(self)
        elapsed = max(0, time.monotonic() - self.state_since)
        frame = self.current_frame(elapsed=elapsed)
        if self.direction < 0:
            p.translate(self.width(), 0)
            p.scale(-1, 1)
        width = self.width()
        height = self.height()
        # Procedural secondary animation is used only without a dedicated clip.
        clip = self.animations.clips.get(self.state)
        secondary = clip is None or (self.state == 'idle' and len(clip.frames) == 1)
        if secondary:
            if self.state == 'land':
                progress = min(1, elapsed / 0.35)
                height *= 1 - 0.22 * math.sin(progress * math.pi)
            elif self.state.startswith('idle'):
                height *= 1 - 0.025 * (1 + math.sin(elapsed * 3))
            elif self.state == 'walk':
                height -= abs(math.sin(self.phase)) * 3
        scaled = frame.size().scaled(round(width), max(1, round(height)), Qt.AspectRatioMode.KeepAspectRatio)
        # Land squashes vertically while preserving the sprite's horizontal size.
        if self.state == 'land' and 'land' not in self.animations.clips:
            scaled.setWidth(frame.size().scaled(width, width, Qt.AspectRatioMode.KeepAspectRatio).width())
        p.drawPixmap(QRectF((width - scaled.width()) / 2, self.height() - scaled.height(),
                           scaled.width(), scaled.height()), frame, QRectF(frame.rect()))

    def showEvent(self, event):
        super().showEvent(event)
        manager = getattr(self.controller, 'manager', None)
        if manager:
            handle = manager.scheduler.handles.get(self)
            if handle:
                handle.hidden = True
            if not self.notification_state(time.monotonic()):
                self.clear_notifications()
            manager.scheduler.wake()

    def hideEvent(self, event):
        self.animations.suspend()
        super().hideEvent(event)
        manager = getattr(self.controller, 'manager', None)
        if manager:
            manager.scheduler.wake()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.pos()
            self.motion.vy = 0
            self.landing_until = 0
            self.set_state('drag')

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None:
            point = event.globalPosition().toPoint() - self.drag_offset
            area = self.bounds()
            self.motion.x = float(point.x())
            bottom = max(area.top(), area.bottom() - self.height() + 1) if self.option('pinned') else self.floor()
            self.motion.y = float(max(area.top(), min(point.y(), bottom)))
            self.place()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.drag_offset is not None:
            self.drag_offset = None
            self.motion.vy = 0
            self.motion.airborne = not self.option('pinned') and self.motion.y < self.floor()
            if self.motion.airborne and self.controller.gravity_enabled.isChecked():
                self.set_state('fall')
            else:
                self.place(on_floor=True)
            manager = getattr(self.controller, 'manager', None)
            if manager:
                manager.schedule_persist()
            self.last_tick = time.monotonic()

    def mouseDoubleClickEvent(self, event):
        self.controller.show_settings()

    def contextMenuEvent(self, event):
        self.controller.menu.exec(event.globalPos())


class Settings(QWidget):
    def __init__(self):
        super().__init__()
        self._loading_companion = False
        self._shutting_down = False
        self.manager = None
        self.store = QSettings('DesktopChibi', 'DesktopChibi')
        self.setWindowTitle('Desktop Chibi · Настройки')
        self.setMinimumSize(600, 520)
        self.setObjectName('desktop')
        apply_theme(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)
        bar = window_bar('DESKTOP CHIBI', brand=True)
        outer.addWidget(bar)
        menu_bar = QMenuBar()
        menu_bar.setNativeMenuBar(False)
        companion_menu = menu_bar.addMenu('Чибик')
        companion_menu.addAction('Добавить чибика', lambda: self.manager.add())
        companion_menu.addAction('Переименовать…', self.rename_companion)
        companion_menu.addSeparator()
        companion_menu.addAction('Показать / скрыть', self.toggle_pet)
        companion_menu.addSeparator()
        companion_menu.addAction('Выход', self.close_application)
        look_menu = menu_bar.addMenu('Облик')
        look_menu.addAction('Загрузить облик…', self.choose_character)
        look_menu.addAction('Мастерская анимаций…', self.edit_animations)
        tools_menu = menu_bar.addMenu('Настройка')
        tools_menu.addAction('Настройки приложения…', self.open_app_settings)
        tools_menu.addSeparator()
        tools_menu.addAction('Помощник настройки…', self.open_quick_setup)
        tools_menu.addAction('События и правила…', self.open_events)
        help_menu = menu_bar.addMenu('Помощь')
        help_menu.addAction('Открыть помощь', self.open_guide)
        outer.addWidget(menu_bar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = PixelSurface()
        content.setObjectName('content')
        scroll.setWidget(content)
        outer.addWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(12, 14, 12, 14)
        layout.setSpacing(14)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(6)
        title = SystemHeading('Чибики')
        heading.addWidget(title)
        subtitle = QLabel('Облик, движение и реакции на события')
        subtitle.setObjectName('muted')
        heading.addWidget(subtitle)
        header.addLayout(heading)
        header.addStretch()
        self.status = QLabel('Виден')
        self.status.setObjectName('status')
        header.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        companions, companions_layout = panel('Выбранный чибик')
        self.companion_select = QComboBox()
        self.companion_select.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.companion_select.setMinimumContentsLength(16)
        companion_actions = QHBoxLayout()
        companion_actions.addWidget(self.companion_select, 1)
        self.add_companion_button = QPushButton('+ Добавить')
        self.remove_companion_button = QPushButton('Удалить чибика')
        self.reset_companion_button = QPushButton('Сброс настроек')
        self.reset_companion_button.setToolTip('Сбросить размер, движение и экран выбранного чибика. Облик, анимации и правила сохраняются.')
        self.add_companion_button.setToolTip('Новый встроенный зайчик с настройками по умолчанию. Для готового облика выберите пресет.')
        companion_actions.addWidget(self.add_companion_button)
        companions_layout.addLayout(companion_actions)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel('Режим'))
        self.companion_mode = QComboBox()
        self.companion_mode.addItem('Без реакций', 'decorative')
        self.companion_mode.addItem('С реакциями', 'functional')
        self.companion_mode.setToolTip('Декоративный: прогулка и анимации. С событиями: дополнительно реакции на правила.')
        name_row.addWidget(self.companion_mode, 1)
        self.rename_companion_button = QPushButton('Переименовать…')
        name_row.addWidget(self.rename_companion_button)
        companions_layout.addLayout(name_row)
        selected_row = QHBoxLayout()
        self.selected_thumbnail = PixelPreview()
        self.selected_thumbnail.setFixedSize(72, 72)
        self.selected_thumbnail.setContentsMargins(4, 4, 4, 4)
        selected_row.addWidget(self.selected_thumbnail)
        selected_details = QVBoxLayout()
        selected_details.setSpacing(4)
        selected_details.addStretch()
        self.selected_appearance = QLabel()
        self.selected_appearance.setWordWrap(True)
        self.selected_details = QLabel()
        self.selected_details.setObjectName('muted')
        self.selected_details.setWordWrap(True)
        self.visibility_reason = QLabel()
        self.visibility_reason.setObjectName('muted')
        self.visibility_reason.setWordWrap(True)
        selected_details.addWidget(self.selected_appearance)
        selected_details.addWidget(self.selected_details)
        selected_details.addWidget(self.visibility_reason)
        selected_details.addStretch()
        selected_row.addLayout(selected_details, 1)
        selected_actions = QVBoxLayout()
        selected_actions.addWidget(self.reset_companion_button)
        selected_actions.addWidget(self.remove_companion_button)
        selected_row.addLayout(selected_actions)
        companions_layout.addLayout(selected_row)
        preset_caption = QLabel('Пресеты')
        preset_caption.setObjectName('caption')
        companions_layout.addWidget(preset_caption)
        preset_row = QHBoxLayout()
        self.preset_select = QComboBox()
        self.preset_select.setMinimumContentsLength(8)
        self.preset_select.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.preset_select.setToolTip('Выбор сразу меняет облик выбранного слота. Правила и режим событий сохраняются.')
        self.preset_select.activated.connect(self.choose_preset)
        preset_row.addWidget(self.preset_select, 1)
        self.save_preset_button = QPushButton('Сохранить…')
        self.save_preset_button.setToolTip('Сохранить облик и параметры выбранного чибика как новый пресет.')
        self.save_preset_button.clicked.connect(self.save_preset)
        preset_row.addWidget(self.save_preset_button)
        self.preset_manage = QPushButton('⋯')
        self.preset_manage.setToolTip('Обновить, переименовать или удалить выбранный пресет')
        preset_menu = QMenu(self.preset_manage)
        preset_menu.addAction('Обновить пресет', self.update_preset)
        preset_menu.addAction('Переименовать пресет…', self.rename_preset)
        preset_menu.addAction('Удалить пресет', self.remove_preset)
        self.preset_manage.setMenu(preset_menu)
        preset_row.addWidget(self.preset_manage)
        companions_layout.addLayout(preset_row)
        self.preset_settings = QCheckBox('Размер и движение из пресета')
        self.preset_settings.setChecked(self.store.value('preset_with_settings', True, type=bool))
        self.preset_settings.setToolTip('Включает также гравитацию, монитор и оконный слой. Имя слота, видимость и правила остаются прежними.')
        self.preset_settings.toggled.connect(lambda value: self.store.setValue('preset_with_settings', value))
        companions_layout.addWidget(self.preset_settings)
        self.load_label = QLabel()
        self.load_label.setObjectName('muted')
        self.load_label.setWordWrap(True)
        self.load_label.setToolTip('Вес — условная оценка, не мегабайты. 64 — мягкий ориентир: лимит 32 чибика всегда доступен.')
        self.load_label.hide()
        self.performance_mode = QComboBox()
        self.performance_mode.addItem('Производительность: Авто', 'auto')
        self.performance_mode.addItem('Производительность: Экономный', 'eco')
        self.performance_mode.addItem('Производительность: Плавный', 'smooth')
        self.performance_mode.hide()
        self.advanced_overview = QCheckBox('Показать настройки производительности')
        self.advanced_overview.toggled.connect(self.load_label.setVisible)
        self.advanced_overview.toggled.connect(self.performance_mode.setVisible)
        quick_row = QHBoxLayout()
        quick = QPushButton('Помощник настройки…')
        quick.setObjectName('primary')
        quick.clicked.connect(self.open_quick_setup)
        quick_row.addWidget(quick, 1)
        help_button = QPushButton('Помощь')
        help_button.clicked.connect(self.open_guide)
        quick_row.addWidget(help_button)
        companions_layout.addLayout(quick_row)
        self.events_button = QPushButton('События и правила…')
        self.events_button.clicked.connect(self.open_events)
        companions_layout.addWidget(self.events_button)
        controls_page, controls_layout = panel('Все чибики')
        game_row = QHBoxLayout()
        self.game_all_button = QPushButton('Все чибики поверх игры')
        self.game_all_button.clicked.connect(lambda: self.set_game_overlay_all(True))
        game_row.addWidget(self.game_all_button)
        self.game_off_button = QPushButton('Обычный режим')
        self.game_off_button.clicked.connect(lambda: self.set_game_overlay_all(False))
        game_row.addWidget(self.game_off_button)
        controls_layout.addLayout(game_row)
        controls_hint = QLabel('Эти настройки действуют на всех чибиков. '
                              + ('Сочетания клавиш настраиваются в приложении.' if sys.platform == 'win32' else 'Сочетания клавиш выбираются в системном окне.'))
        controls_hint.setWordWrap(True)
        controls_layout.addWidget(controls_hint)
        controls_row = QHBoxLayout()
        self.toggle_all_button = QPushButton('Скрыть всех')
        self.toggle_input_button = QPushButton('Сделать неосязаемыми')
        self.toggle_all_button.clicked.connect(lambda: self.desktop_controls.toggle_visibility())
        self.toggle_input_button.clicked.connect(lambda: self.desktop_controls.toggle_input())
        controls_row.addWidget(self.toggle_all_button)
        controls_row.addWidget(self.toggle_input_button)
        controls_layout.addLayout(controls_row)
        shortcuts_row = QHBoxLayout()
        configure_shortcuts = QPushButton('Настроить хоткеи…')
        configure_shortcuts.clicked.connect(lambda: self.desktop_controls.start_shortcuts())
        shortcuts_row.addWidget(configure_shortcuts)
        disable_shortcuts = QPushButton('Выключить хоткеи')
        disable_shortcuts.clicked.connect(lambda: self.desktop_controls.stop_shortcuts())
        shortcuts_row.addWidget(disable_shortcuts)
        controls_layout.addLayout(shortcuts_row)
        self.desktop_controls_status = QLabel()
        self.desktop_controls_status.setWordWrap(True)
        controls_layout.addWidget(self.desktop_controls_status)
        controls_layout.addWidget(self.advanced_overview)
        controls_layout.addWidget(self.load_label)
        controls_layout.addWidget(self.performance_mode)
        self.fix_button = QPushButton('Проверить и исправить')
        self.fix_button.setToolTip('Сохранить резервную копию, восстановить ID друзей по включённым правилам и переподключить включённые источники.')
        self.fix_button.clicked.connect(self.repair_application)
        controls_layout.addWidget(self.fix_button)
        controls_layout.addStretch()
        self.companion_notice = NoticeLabel()
        self.companion_notice.setWordWrap(True)
        companions_layout.addWidget(self.companion_notice)
        companions_layout.addStretch()

        character, character_layout = panel('Внешний вид')
        self.preview = Preview(self)
        character_layout.addWidget(self.preview)
        self.character_label = QLabel()
        self.character_label.setWordWrap(True)
        character_layout.addWidget(self.character_label)
        buttons = QHBoxLayout()
        load = QPushButton('Загрузить облик…')
        load.setObjectName('primary')
        load.clicked.connect(self.choose_character)
        reset = QPushButton('Вернуть зайчика')
        reset.setToolTip('Вернуть встроенного зайчика и его стандартные анимации.')
        reset.clicked.connect(self.reset_character)
        buttons.addWidget(load, 1)
        buttons.addWidget(reset)
        character_layout.addLayout(buttons)
        workshop = QPushButton('Мастерская анимаций…')
        workshop.clicked.connect(self.edit_animations)
        character_layout.addWidget(workshop)
        self.notification_binding = QLabel()
        self.notification_binding.setWordWrap(True)
        self.notification_binding.setObjectName('muted')
        character_layout.addWidget(self.notification_binding)
        self.bind_notifications_button = QPushButton('Реакция на уведомления…')
        self.bind_notifications_button.clicked.connect(self.bind_notifications)
        character_layout.addWidget(self.bind_notifications_button)
        self.bind_audio_button = QPushButton('Реакция на звук приложения…')
        self.bind_audio_button.clicked.connect(self.bind_audio)
        character_layout.addWidget(self.bind_audio_button)
        self.bind_vesktop_button = QPushButton('Речь друга в Vesktop…')
        self.bind_vesktop_button.clicked.connect(self.bind_vesktop)
        character_layout.addWidget(self.bind_vesktop_button)
        if sys.platform == 'win32':
            for button in (self.bind_notifications_button, self.bind_audio_button, self.bind_vesktop_button):
                button.setEnabled(False)
                button.setToolTip('Этот источник доступен только в Linux. В Windows доступны локальные события и таймеры.')
        self.settings_tabs = PixelTabs()
        layout.addWidget(self.settings_tabs)
        self.settings_tabs.addTab(companions, 'Чибики')
        character_layout.addStretch()
        self.settings_tabs.addTab(character, 'Облик')

        movement, movement_layout = panel('Движение')
        self.pet_size = self.slider(64, 256, 'size')
        self.speed = self.slider(10, 180, 'speed')
        self.offset = self.slider(0, 300, 'offset')
        self.gravity = self.slider(100, 3000, 'gravity')
        self.idle_interval = self.slider(2, 60, 'idle_interval')
        self.idle_duration = self.slider(1, 15, 'idle_duration')
        for label, slider, unit in [('Размер', self.pet_size, 'px'),
                                    ('Скорость', self.speed, 'px/s'),
                                    ('Отступ снизу', self.offset, 'px'),
                                    ('Гравитация', self.gravity, 'px/s²'),
                                    ('Пауза каждые ≈', self.idle_interval, 's'),
                                    ('Длина паузы', self.idle_duration, 's')]:
            row = QHBoxLayout()
            name = QLabel(label)
            name.setMinimumWidth(124)
            value = QLabel()
            value.setObjectName('value')
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value.setFixedWidth(88)
            def update_value(number, target=value, suffix=unit):
                target.setText(f'{number} {suffix}')
            slider.valueChanged.connect(update_value)
            update_value(slider.value())
            row.addWidget(name)
            row.addWidget(slider, 1)
            row.addWidget(value)
            movement_layout.addLayout(row)
        self.walk = QCheckBox('Гулять с короткими остановками')
        self.walk.setChecked(self.store.value('walk', DEFAULT_SETTINGS['walk'], type=bool))
        movement_layout.addWidget(self.walk)
        self.gravity_enabled = QCheckBox('Гравитация при отпускании мыши')
        self.gravity_enabled.setChecked(self.store.value('gravity_enabled', DEFAULT_SETTINGS['gravity_enabled'], type=bool))
        movement_layout.addWidget(self.gravity_enabled)
        self.random_idle = QCheckBox('Случайные idle-анимации')
        self.random_idle.setChecked(self.store.value('random_idle', DEFAULT_SETTINGS['random_idle'], type=bool))
        movement_layout.addWidget(self.random_idle)
        self.pinned = QCheckBox('Закрепить на месте — без ходьбы и падения')
        self.pinned.setChecked(self.store.value('pinned', False, type=bool))
        movement_layout.addWidget(self.pinned)
        self.area_enabled = QCheckBox('Ограничить область прогулки')
        self.area_enabled.setChecked(self.store.value('area_enabled', False, type=bool))
        movement_layout.addWidget(self.area_enabled)
        area_hint = QLabel('Границы в процентах от рабочего пространства монитора.\nЧибик гуляет по нижнему краю выбранной области.')
        area_hint.setWordWrap(True)
        movement_layout.addWidget(area_hint)
        for key, title in [('area_left', 'Левая граница'), ('area_right', 'Правая граница'),
                           ('area_top', 'Верхняя граница'), ('area_bottom', 'Нижняя граница')]:
            widget = self.slider(0, 100, key)
            setattr(self, key, widget)
            row = QHBoxLayout()
            row.addWidget(QLabel(title))
            row.addWidget(widget, 1)
            value = QLabel(f'{widget.value()}%')
            widget.valueChanged.connect(lambda number, label=value: label.setText(f'{number}%'))
            row.addWidget(value)
            movement_layout.addLayout(row)
            widget.valueChanged.connect(self.save)
        self.pinned.toggled.connect(self.save)
        self.area_enabled.toggled.connect(self.save)
        movement_layout.addStretch()
        self.settings_tabs.addTab(movement, 'Движение')

        display, display_layout = panel('Экран и видимость')
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.screen = QComboBox()
        for i, screen in enumerate(QApplication.screens()):
            self.screen.addItem(f'Монитор {i + 1}' + (f' — {screen.name()}' if screen.name() else ''))
        self.screen.setCurrentIndex(max(0, min(int(self.store.value('screen', DEFAULT_SETTINGS['screen'])), self.screen.count() - 1)))
        form.addRow('Монитор', self.screen)
        self.layer = QComboBox()
        self.layer.addItems(['Поверх всех окон', 'Обычный слой окон', 'Под обычными окнами'])
        self.layer.setCurrentIndex(max(0, min(2, int(self.store.value('layer', DEFAULT_SETTINGS['layer'])))))
        form.addRow('Слой', self.layer)
        display_layout.addLayout(form)
        from .stacking_ui import StackingOrder
        self.stacking_editor = StackingOrder()
        display_layout.addWidget(self.stacking_editor)
        self.game_overlay = QCheckBox('Игровой оверлей для этого чибика')
        self.game_overlay.setChecked(self.store.value('game_overlay', False, type=bool))
        display_layout.addWidget(self.game_overlay)
        self.game_overlay.toggled.connect(self.save)
        overlay_hint = QLabel('Для игр используйте режим «Окно без рамки». Полноэкранная игра или Wayland могут перекрывать чибика.')
        overlay_hint.setObjectName('muted')
        overlay_hint.setWordWrap(True)
        display_layout.addWidget(overlay_hint)
        self.hide_when_absent = QCheckBox('Показывать только с другом в звонке')
        self.hide_when_absent.setToolTip('Vesktop: присутствие друга определяется личными правилами выбранного чибика.')
        self.hide_when_absent.setChecked(self.store.value('hide_when_absent', False, type=bool))
        display_layout.addWidget(self.hide_when_absent)
        presence_hint = QLabel('Использует правило Vesktop. Чибик остаётся видимым, пока друг в звонке, даже если тот молчит.')
        presence_hint.setObjectName('muted')
        presence_hint.setWordWrap(True)
        display_layout.addWidget(presence_hint)
        self.hide_when_absent.toggled.connect(self.save)
        self.click_through = QCheckBox('Пропускать клики сквозь чибика')
        self.click_through.setChecked(self.store.value('click_through', False, type=bool))
        display_layout.addWidget(self.click_through)
        input_hint = QLabel('При пропуске кликов чибик не реагирует на мышь. Вернуть перетаскивание можно на вкладке «Управление».')
        input_hint.setObjectName('muted')
        input_hint.setWordWrap(True)
        display_layout.addWidget(input_hint)
        self.click_through.toggled.connect(self.save)
        self.window_rule_mode = QComboBox()
        for title, mode in [('Во всех приложениях', 'all'),
                            ('Только в выбранных приложениях', 'only'),
                            ('Кроме выбранных приложений', 'except')]:
            self.window_rule_mode.addItem(title, mode)
        display_layout.addWidget(QLabel('Когда показывать выбранного чибика:'))
        display_layout.addWidget(self.window_rule_mode)
        self.window_rule_apps = QPlainTextEdit()
        self.window_rule_apps.setPlaceholderText('ID приложений, по одному на строку: firefox, steam…')
        self.window_rule_apps.setMaximumHeight(90)
        display_layout.addWidget(self.window_rule_apps)
        app_row = QHBoxLayout()
        self.window_app_picker = QComboBox()
        self.window_app_picker.setMinimumWidth(120)
        app_row.addWidget(self.window_app_picker, 1)
        refresh_apps = QPushButton('Обновить список')
        refresh_apps.clicked.connect(lambda: self.desktop_controls.request_applications())
        app_row.addWidget(refresh_apps)
        add_app = QPushButton('Добавить')
        add_app.clicked.connect(self.add_window_application)
        app_row.addWidget(add_app)
        display_layout.addLayout(app_row)
        rule_hint = QLabel('Обновите список и добавьте нужные приложения. Правило учитывает активное приложение.'
                           + (' В Windows используется имя программы без .exe.' if sys.platform == 'win32' else ' Требуется расширение GNOME.'))
        rule_hint.setObjectName('muted')
        rule_hint.setWordWrap(True)
        display_layout.addWidget(rule_hint)
        self.window_rule_mode.currentIndexChanged.connect(self.save_window_rule)
        self.window_rule_apps.textChanged.connect(self.save_window_rule)
        self.reset_settings_button = QPushButton('Настройки по умолчанию')
        self.reset_settings_button.setToolTip('Сбросить движение, гравитацию, монитор и слой. Персонаж и его анимации сохранятся.')
        self.reset_settings_button.clicked.connect(self.reset_settings)
        display_layout.addWidget(self.reset_settings_button)
        display_layout.addStretch()
        self.settings_tabs.addTab(display, 'Экран')
        self.settings_tabs.addTab(controls_page, 'Управление')
        hint = QLabel()
        self.interaction_hint = hint
        hint.setObjectName('hint')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        footer = QHBoxLayout()
        footer.setContentsMargins(10, 10, 10, 2)
        self.visibility_button = QPushButton('Скрыть чибика')
        self.visibility_button.setObjectName('primary')
        self.visibility_button.clicked.connect(self.toggle_pet)
        footer.addWidget(self.visibility_button, 1)
        quit_button = QPushButton('Выход')
        quit_button.clicked.connect(QApplication.instance().quit)
        footer.addWidget(quit_button)
        outer.addLayout(footer)
        self.pet = Pet(self)
        self.menu = QMenu(self)
        self.menu.addAction('Настройки', self.show_settings)
        self.visibility_action = self.menu.addAction('Скрыть чибика', self.toggle_pet)
        self.menu.addSeparator()
        self.tray_presets = self.menu.addMenu('Быстрая смена пресета')
        self.menu.addAction('Выход', self.close_application)
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(QApplication.windowIcon())
        self.tray.setToolTip('Desktop Chibi')
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(lambda reason: self.show_settings()
                                   if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.character_path = ''
        self.set_character(str(self.store.value('character', '')))
        for widget in (self.pet_size, self.speed, self.offset, self.gravity, self.idle_interval, self.idle_duration):
            widget.valueChanged.connect(self.save)
        self.walk.toggled.connect(self.save)
        self.gravity_enabled.toggled.connect(self.save)
        self.random_idle.toggled.connect(self.save)
        self.screen.currentIndexChanged.connect(self.change_screen)
        self.layer.currentIndexChanged.connect(self.apply_layer)
        self.pet.show()
        self.apply_layer()
        self.change_screen()
        self.preview_timer = QTimer(self)
        self.preview_timer.timeout.connect(self.preview.update)
        self.settings_tabs.currentChanged.connect(self.sync_preview_clock)
        self.manager = CompanionManager(self, Pet, DEFAULT_SETTINGS)
        self.stacking_editor.bind(self.manager)
        self.companion_select.currentIndexChanged.connect(self.manager.select)
        self.add_companion_button.clicked.connect(self.manager.add)
        self.remove_companion_button.clicked.connect(self.manager.remove)
        self.reset_companion_button.clicked.connect(self.manager.reset_settings)
        self.companion_mode.currentIndexChanged.connect(self.change_companion_mode)
        self.performance_mode.currentIndexChanged.connect(lambda: self.manager.set_performance(self.performance_mode.currentData()) if not self._loading_companion else None)
        self.rename_companion_button.clicked.connect(self.rename_companion)
        self.presets = PresetLibrary(self)
        self.refresh_presets()
        self.event_hub = EventHub(self.manager, self.store, self)
        self.event_hub.start()
        self.event_hub.changed.connect(self.refresh_notification_binding)
        self.refresh_notification_binding()
        from .desktop_controls import DesktopControls
        self.desktop_controls = DesktopControls(self.manager)
        self.desktop_controls.changed.connect(self.refresh_desktop_controls)
        self.desktop_controls.applications.connect(self.populate_window_applications)
        self.desktop_controls.request_applications()
        self.menu.addSeparator()
        self.menu.addAction('Скрыть / вернуть всех', self.desktop_controls.toggle_visibility)
        self.menu.addAction('Переключить клики всех', self.desktop_controls.toggle_input)
        self.refresh_desktop_controls()
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self.manager.persist()
        self.resize(680, 860)

    def slider(self, low, high, key):
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setMinimumHeight(28)
        slider.setRange(low, high)
        slider.setValue(int(self.store.value(key, DEFAULT_SETTINGS[key])))
        slider.valueChanged.connect(lambda value: slider.setToolTip(str(value)))
        slider.setToolTip(str(slider.value()))
        return slider

    def refresh_presets(self, selected=None):
        self.preset_select.blockSignals(True)
        self.preset_select.clear()
        self.preset_select.addItem('Выбрать пресет…', None)
        self.preset_select.addItem('Встроенный зайчик', 'builtin')
        for entry in self.presets.entries:
            self.preset_select.addItem(entry['name'], entry['id'])
        self.preset_select.setCurrentIndex(max(0, self.preset_select.findData(selected)))
        self.preset_select.blockSignals(False)
        self.preset_manage.setEnabled(self.presets.find(self.preset_select.currentData()) is not None)
        # Native tray menus may never emit QMenu.aboutToShow for submenus.
        # Publish populated actions before the desktop exports the menu.
        self.fill_preset_menu(self.tray_presets)

    def apply_preset(self, identity, target=None):
        try:
            item = self.manager.find(target) if target else self.manager.selected
            self.presets.apply(identity, target=target, with_settings=self.preset_settings.isChecked())
            entry = self.presets.find(identity)
            self.refresh_presets(identity if item is self.manager.selected else None)
            self.companion_notice.setText(f'Для «{item.name}» применён пресет: ' + (entry['name'] if entry else 'Встроенный зайчик') +
                                          '. Правила и режим событий сохранены.')
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, 'Не удалось сменить пресет', str(error))

    def choose_preset(self, index):
        identity = self.preset_select.itemData(index)
        if identity:
            self.apply_preset(identity)

    def save_preset(self):
        name, ok = QInputDialog.getText(self, 'Сохранить пресет', 'Имя пресета:', text=self.manager.selected.name)
        if ok:
            try:
                identity = self.presets.save(name)
                self.refresh_presets(identity)
                self.companion_notice.setText('Пресет сохранён вместе с копиями текстур и слоями мастерской.')
            except ValueError as error:
                QMessageBox.warning(self, 'Не удалось сохранить пресет', str(error))

    def update_preset(self):
        identity = self.preset_select.currentData()
        entry = self.presets.find(identity)
        if entry:
            try:
                self.presets.save(entry['name'], replace=identity)
                self.refresh_presets(identity)
                self.companion_notice.setText('Пресет обновлён из текущего чибика.')
            except ValueError as error:
                QMessageBox.warning(self, 'Не удалось обновить пресет', str(error))

    def rename_preset(self):
        identity = self.preset_select.currentData()
        entry = self.presets.find(identity)
        if entry:
            name, ok = QInputDialog.getText(self, 'Переименовать пресет', 'Новое имя:', text=entry['name'])
            if ok:
                try:
                    self.presets.rename(identity, name)
                    self.refresh_presets(identity)
                except ValueError as error:
                    QMessageBox.warning(self, 'Не удалось переименовать', str(error))

    def remove_preset(self):
        identity = self.preset_select.currentData()
        if self.presets.find(identity):
            self.presets.remove(identity)
            self.refresh_presets()
            self.companion_notice.setText('Пресет удалён из списка. Активные чибики и их текстуры сохранены.')

    def fill_preset_menu(self, menu, target=None):
        menu.clear()
        if not hasattr(self, 'presets'):
            return
        menu.addAction('Встроенный зайчик', lambda: self.apply_preset('builtin', target))
        for entry in self.presets.entries:
            menu.addAction(entry['name'], lambda checked=False, identity=entry['id']: self.apply_preset(identity, target))
        if not self.presets.entries:
            hint = menu.addAction('Сохрани своего чибика в настройках')
            hint.setEnabled(False)

    def choose_character(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Выбрать персонажа', '', 'Персонаж (*.png *.gif *.webp *.json)')
        if path:
            self.set_character(path)

    def set_character(self, path):
        try:
            saved = self.store.value(self.profile_key(path), '')
            specs = json.loads(saved) if saved else None
            self.pet.load_character(path, specs)
        except (ValueError, TypeError) as error:
            QMessageBox.warning(self, 'Не удалось загрузить персонажа', str(error))
            if not self.character_path:
                self.character_label.setText('Персонаж: встроенный зайчик')
            return
        self.character_path = path
        name = Path(path).name if path else 'встроенный зайчик'
        if path and Path(path).suffix.lower() == '.json':
            try:
                name, _ = read_manifest(path)
            except ValueError:
                pass
        elif not path and self.pet.animations.specs:
            name = 'свой набор'
        self.character_label.setText(f'Персонаж: {name}')
        self.store.setValue('character', path)
        self.preview.update()
        if self.manager:
            self.manager.persist()

    def reset_character(self):
        self.store.remove(self.profile_key(''))
        self.set_character('')

    def profile_key(self, path):
        identity = str(Path(path).resolve()) if path else '__builtin__'
        return 'animations/' + hashlib.sha256(identity.encode()).hexdigest()

    def edit_animations(self):
        if self.character_path and Path(self.character_path).suffix.lower() == '.json':
            try:
                _, defaults = read_manifest(self.character_path)
            except ValueError:
                defaults = self.pet.animations.specs
        else:
            defaults = {'idle': {'file': self.character_path}} if self.character_path else {}
        specs = self.pet.animations.specs
        if not self.character_path and not ({'idle', 'notify'} & specs.keys()):
            specs = {**specs, 'notify': default_notification()}
            defaults = {**defaults, 'notify': default_notification()}
        editor = AnimationEditor(specs, self, default_specs=defaults)
        try:
            if editor.exec() == AnimationEditor.DialogCode.Accepted:
                if editor.character_manifest:
                    self.set_character(editor.character_manifest)
                else:
                    self.pet.load_character(self.character_path, editor.specs)
                    self.store.setValue(self.profile_key(self.character_path), json.dumps(editor.specs))
                    if not self.character_path:
                        self.character_label.setText('Персонаж: свой набор' if editor.specs else 'Персонаж: встроенный зайчик')
                self.preview.update()
                if self.manager:
                    self.manager.persist()
        except ValueError as error:
            QMessageBox.warning(self, 'Не удалось применить анимации', str(error))
        finally:
            editor.deleteLater()

    def reset_settings(self):
        if self.manager:
            self.manager.reset_settings()
            return
        for key in ('size', 'speed', 'offset', 'gravity', 'idle_interval', 'idle_duration', 'area_left', 'area_right', 'area_top', 'area_bottom'):
            getattr(self, 'pet_size' if key == 'size' else key).setValue(DEFAULT_SETTINGS[key])
        for key in ('walk', 'gravity_enabled', 'random_idle', 'pinned', 'click_through', 'area_enabled', 'hide_when_absent', 'game_overlay'):
            getattr(self, key).setChecked(DEFAULT_SETTINGS[key])
        self.screen.setCurrentIndex(DEFAULT_SETTINGS['screen'])
        self.layer.setCurrentIndex(DEFAULT_SETTINGS['layer'])
        self.pet.drag_offset = None
        self.pet.pause_until = 0
        self.change_screen()
        self.save()

    def collect_settings(self):
        values = {}
        for key in DEFAULT_SETTINGS:
            widget = getattr(self, 'pet_size' if key == 'size' else key)
            values[key] = widget.isChecked() if isinstance(DEFAULT_SETTINGS[key], bool) else widget.currentIndex() if key in ('screen', 'layer') else widget.value()
        return values

    def refresh_desktop_controls(self):
        controls = self.desktop_controls
        self.toggle_all_button.setText('Вернуть всех' if controls.hidden else 'Скрыть всех')
        passthrough = all(item.pet.input_passthrough() for item in self.manager.items)
        self.toggle_input_button.setText('Разрешить перетаскивание' if passthrough else 'Пропускать клики')
        input_status = ('Мышь работает по настройкам каждого чибика.' if controls.input_override is None else
                        'Клики проходят сквозь всех чибиков.' if controls.input_override else 'Всех чибиков можно перетаскивать.')
        messages = [controls.shortcut_status, input_status]
        if any(item.window_rule['mode'] != 'all' for item in self.manager.items):
            messages.append(controls.status)
        self.desktop_controls_status.setText('\n'.join(messages))
        self.manager.update_load_label()
        self.sync_visibility()

    def save_window_rule(self, *_):
        if self._loading_companion or not self.manager:
            return
        self.manager.schedule_persist()
        if hasattr(self, 'desktop_controls'):
            self.desktop_controls.apply_visibility()

    def populate_window_applications(self, apps):
        self.window_app_picker.clear()
        for app in apps:
            if isinstance(app, dict) and isinstance(app.get('id'), str):
                self.window_app_picker.addItem(f"{app.get('name', app['id'])} ({app['id']})", app['id'])

    def add_window_application(self):
        identity = self.window_app_picker.currentData()
        if identity and identity not in self.window_rule_apps.toPlainText().splitlines():
            self.window_rule_apps.appendPlainText(identity)

    def save(self, *_):
        if self._loading_companion:
            return
        for key in ('size', 'speed', 'offset', 'gravity', 'idle_interval', 'idle_duration', 'area_left', 'area_right', 'area_top', 'area_bottom'):
            self.store.setValue(key, getattr(self, 'pet_size' if key == 'size' else key).value())
        self.store.setValue('walk', self.walk.isChecked())
        self.store.setValue('gravity_enabled', self.gravity_enabled.isChecked())
        self.store.setValue('random_idle', self.random_idle.isChecked())
        self.store.setValue('screen', self.screen.currentIndex())
        self.store.setValue('layer', self.layer.currentIndex())
        for key in ('pinned', 'click_through', 'area_enabled', 'hide_when_absent', 'game_overlay'):
            self.store.setValue(key, getattr(self, key).isChecked())
        if self.manager:
            self.manager.schedule_persist()
            self.pet.apply_window_mode()
            hub = getattr(self, 'event_hub', None)
            if hub:
                hub.update_presence()

    def change_screen(self, *_):
        if self._loading_companion:
            return
        if self.manager:
            self.manager.capture_active()
        self.pet.position = float(self.pet.bounds().center().x())
        self.pet.place(on_floor=True)
        self.save()

    def apply_layer(self, *_):
        if self._loading_companion:
            return
        self.pet.apply_window_mode()
        self.save()

    def toggle_pet(self):
        self.pet.setVisible(not self.pet.requested_visible)
        self.sync_visibility()
        if self.manager:
            self.manager.persist()

    def sync_visibility(self):
        visible = self.pet.requested_visible
        text = 'Скрыть чибика' if visible else 'Показать чибика'
        self.visibility_action.setText(text)
        self.visibility_button.setText(text)
        controls = getattr(self, 'desktop_controls', None)
        if not visible:
            badge, tone, reason = 'Скрыт', 'hidden', 'Вы скрыли этого чибика.'
        elif controls and controls.hidden:
            badge, tone, reason = 'Пауза', 'waiting', 'Все чибики скрыты общим переключателем.'
        elif self.pet.option('hide_when_absent') and not getattr(self.pet, '_friend_present', False):
            badge, tone, reason = 'Ожидание', 'waiting', 'Появится, когда друг войдёт в звонок.'
        elif controls and not controls.allows(self.pet):
            badge, tone = 'Ожидание', 'waiting'
            reason = ('Скрыт правилом активного приложения.' if controls.available else
                      'Не удалось определить активное приложение Windows.' if sys.platform == 'win32' else
                      'Правила окон недоступны без расширения GNOME.')
        else:
            badge, tone, reason = 'Виден', 'visible', 'Виден на рабочем столе.'
        self.status.setText(badge)
        self.status.setToolTip(reason)
        if self.status.property('tone') != tone:
            self.status.setProperty('tone', tone)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)
        if hasattr(self, 'visibility_reason'):
            self.visibility_reason.setText(reason)
            self.visibility_reason.setVisible(tone != 'visible')
            clips = self.pet.animations.clips
            size = self.pet.controller.pet_size.value()
            self.selected_appearance.setText(f'Размер: {size} пкс')
            self.selected_thumbnail.setToolTip(self.character_path or 'Встроенный зайчик')
            interaction = 'Клики проходят сквозь' if self.pet.input_passthrough() else 'Можно перетаскивать'
            self.selected_details.setText(interaction)
            self.interaction_hint.setText(
                'Чтобы перетащить чибика, включите клики на вкладке «Управление».'
                if self.pet.input_passthrough() else
                'Перетаскивание — левой кнопкой мыши. Меню — правой.')
            clip = clips.get('idle') or next(iter(clips.values()), None)
            frame = clip.frames[0] if clip and clip.frames else clip.movie.currentPixmap() if clip and clip.movie else bunny_frame()
            # Inspect cached artwork directly: reading this card must not
            # restart or replace a live event animation on the desktop.
            key = frame.cacheKey()
            if getattr(self, '_thumbnail_key', None) != key:
                self._thumbnail_key = key
                self.selected_thumbnail.setPixmap(frame.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                                                Qt.TransformationMode.FastTransformation))

    def change_companion_mode(self, *_):
        if self.manager and not self._loading_companion:
            self.manager.set_mode(self.manager.selected.identity, self.companion_mode.currentData())

    def rename_companion(self):
        item = self.manager.selected
        name, accepted = QInputDialog.getText(self, 'Переименовать чибика', 'Новое имя:', text=item.name)
        if accepted and name.strip():
            self.manager.rename(name)
            self.manager.load_active()

    def set_game_overlay_all(self, enabled):
        self.manager.capture_active()
        for item in self.manager.items:
            item.controller.values['game_overlay'] = enabled
            item.pet.apply_window_mode()
        self.manager.load_active()
        self.manager.persist()
        self.manager.scheduler.wake()
        self.companion_notice.setText('Игровой оверлей включён для всех. Клики проходят сквозь чибиков; для игры используй окно без рамки.' if enabled else 'Обычный режим восстановлен. Индивидуальные настройки кликов и слоя сохранены.')

    def repair_application(self):
        from .repair import repair_application
        self.fix_button.setEnabled(False)
        try:
            messages, backup = repair_application(self)
            dialog = QMessageBox(self)
            dialog.setWindowTitle('Проверка и исправление')
            dialog.setIcon(QMessageBox.Icon.Information)
            dialog.setText('Проверка завершена')
            dialog.setInformativeText('\n'.join(messages[:6]) + ('\nПолный отчёт — в подробностях.' if len(messages) > 6 else ''))
            dialog.setDetailedText('\n'.join(messages) + '\n\nРезервная копия настроек:\n' + str(backup))
            dialog.exec()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Не удалось завершить исправление', str(error))
        finally:
            self.fix_button.setEnabled(True)

    def open_guide(self, checked=False, topic='start'):
        dialog = GuideDialog(self, topic)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def open_quick_setup(self, checked=False):
        dialog = QuickSetup(self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def open_app_settings(self, checked=False):
        from .app_settings import AppSettings
        dialog = AppSettings(self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'preview_timer'):
            self.sync_preview_clock()

    def sync_preview_clock(self, *_):
        if self.isVisible() and self.preview.isVisible():
            self.preview_timer.start(100)
        else:
            self.preview_timer.stop()

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, 'preview_timer'):
            self.preview_timer.stop()
        if self.manager:
            self.manager.scheduler.wake()

    def refresh_notification_binding(self):
        if not hasattr(self, 'event_hub'):
            return
        item = self.manager.selected
        rules = [rule for rule in self.event_hub.rules if rule['enabled'] and (rule.get('scope') == 'global' or rule['target'] == item.identity)
                 and rule['source'] == 'notifications' and rule['event'] == 'message.received']
        if not rules:
            self.notification_binding.setText('Уведомления не привязаны. Рисунок анимации сохраняется отдельно от правила её запуска.')
            return
        source = self.event_hub.source('notifications')
        bindings = '; '.join((rule.get('subject') or 'Все разрешённые приложения') + ' → ' +
                             STATES.get(rule['animation'], rule['animation']) + (' (общее)' if rule.get('scope') == 'global' else ' (личное)') for rule in rules)
        warning = ' Источник выключен.' if not source or not source['enabled'] else ''
        if item.controller.mode != 'functional':
            warning += ' Декоративный режим: реакции выключены.'
        self.notification_binding.setText('Уведомления: ' + bindings + '.' + warning)

    def bind_vesktop(self):
        dialog = QuickSetup(self, 'vesktop')
        dialog.advance()
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def bind_audio(self):
        dialog = QuickSetup(self, 'audio')
        dialog.advance()
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def bind_notifications(self):
        dialog = QuickSetup(self, 'messages')
        dialog.advance()
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
        self.refresh_notification_binding()

    def open_events(self):
        dialog = EventDialog(self.event_hub, self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        if hasattr(self, 'desktop_controls'):
            self.desktop_controls.shutdown()
        if hasattr(self, 'event_hub'):
            self.event_hub.stop()
        if self.manager:
            self.manager.shutdown()

    def close_application(self):
        self.shutdown()
        QApplication.instance().quit()

    def show_settings(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if self.tray.isVisible():
            event.ignore()
            self.hide()
        else:
            self.close_application()


def os_wayland():
    import os
    return os.environ.get('XDG_SESSION_TYPE') == 'wayland'


def main():
    import sys
    from .process_name import set_process_name
    set_process_name('desktop-chibi')
    # Keep X11 WM_CLASS and the Wayland desktop ID aligned with the launcher.
    sys.argv[0] = 'desktop-chibi'
    app = QApplication([])
    app.setApplicationName('Desktop Chibi')
    from . import __version__
    app.setApplicationVersion(__version__)
    app.setDesktopFileName('desktop-chibi')
    app.setWindowIcon(QIcon(str(Path(__file__).parent / 'assets' / 'desktop-chibi.png')))
    app.setQuitOnLastWindowClosed(False)
    from .single_instance import SingleInstance
    instance = None
    settings = None
    try:
        try:
            instance = SingleInstance(app)
            if not instance.start():
                return 0
        except OSError as error:
            QMessageBox.critical(None, 'Desktop Chibi', str(error))
            return 1
        settings = Settings()
        instance.activated.connect(settings.show_settings)
        settings.show()
        return app.exec()
    finally:
        if settings is not None:
            settings.shutdown()
        if instance is not None:
            instance.close()
