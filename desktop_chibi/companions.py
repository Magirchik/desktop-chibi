"""Independent pet instances; the settings window edits one at a time."""
import json
import uuid
from dataclasses import dataclass, field
from .desktop_controls import clean_window_rule

from PySide6.QtCore import QObject, QTimer, Signal, QEvent
from .performance import MAX_COMPANIONS, MODES, SOFT_BUDGET, PetScheduler
from PySide6.QtWidgets import QMenu


class Value:
    def __init__(self, controller, key):
        self.controller, self.key = controller, key

    def value(self):
        return self.controller.values[self.key]

    isChecked = value
    currentIndex = value


class PetController:
    def __init__(self, manager, identity, values, mode='functional'):
        self.manager, self.identity, self.values = manager, identity, dict(values)
        self.mode = mode if mode in MODES else 'functional'
        for key in values:
            setattr(self, 'pet_size' if key == 'size' else key, Value(self, key))
        self.menu = QMenu(manager.owner)
        self.menu.addAction('Настройки этого чибика', self.show_settings)
        self.menu.addAction('Показать / спрятать', self.toggle)
        presets = self.menu.addMenu('Сменить пресет')
        presets.aboutToShow.connect(lambda: manager.owner.fill_preset_menu(presets, self.identity))
        self.menu.addAction('Выход', manager.owner.close_application)

    def show_settings(self):
        self.manager.select_id(self.identity)
        self.manager.owner.show_settings()

    def toggle(self):
        self.manager.select_id(self.identity)
        self.manager.owner.toggle_pet()


@dataclass
class Companion:
    identity: str
    name: str
    path: str
    pet: object
    controller: PetController
    window_rule: dict = field(default_factory=lambda: {'mode': 'all', 'apps': []})


class CompanionManager(QObject):
    stacking_changed = Signal()

    def __init__(self, owner, pet_class, defaults):
        super().__init__(owner)
        self.owner, self.pet_class, self.defaults = owner, pet_class, defaults
        self.items = []
        self.active = 0
        self.loading = True
        self.stacking_order = []
        self.restacking = False
        self.stacking_stopped = False
        self.stack_timer = QTimer(self)
        self.stack_timer.setSingleShot(True)
        self.stack_timer.timeout.connect(self.apply_stacking_order)
        self.performance_mode = owner.store.value('performance_mode', 'auto')
        if self.performance_mode not in ('auto', 'eco', 'smooth'):
            self.performance_mode = 'auto'
        self.scheduler = PetScheduler(self)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(180)
        self.save_timer.timeout.connect(self.persist)
        owner._loading_companion = True
        initial_pet = owner.pet
        saved = owner.store.value('companions_v1', '')
        try:
            records = json.loads(saved) if saved else []
            if not isinstance(records, list):
                records = []
        except (TypeError, ValueError):
            records = []
        if records:
            for record in records[:MAX_COMPANIONS]:
                if not isinstance(record, dict):
                    continue
                self.restore(record)
        if not self.items:
            identity = uuid.uuid4().hex
            controller = PetController(self, identity, owner.collect_settings(), 'decorative')
            initial_pet.controller = controller
            self.scheduler.attach(initial_pet)
            self.items.append(Companion(identity, 'Чибик 1', owner.character_path, initial_pet, controller))
        else:
            initial_pet.timer.stop()
            initial_pet.hide()
            initial_pet.deleteLater()
        self.active = min(max(0, int(owner.store.value('active_companion', 0))), len(self.items) - 1)
        try:
            order = json.loads(owner.store.value('companion_stacking_v1', '[]'))
        except (TypeError, ValueError):
            order = []
        self.stacking_order = self.clean_stacking_order(order)
        for item in self.items:
            item.pet.installEventFilter(self)
        self.loading = False
        self.rebuild_selector()
        self.load_active()
        owner._loading_companion = False
        self.schedule_stacking()

    def clean_stacking_order(self, order):
        valid = {item.identity for item in self.items}
        result = []
        for identity in order if isinstance(order, list) else []:
            if isinstance(identity, str) and identity in valid and identity not in result:
                result.append(identity)
        # Previously the last created window was in front.
        return result + [item.identity for item in reversed(self.items) if item.identity not in result]

    def set_stacking_order(self, order):
        self.stacking_order = self.clean_stacking_order(order)
        self.owner.store.setValue('companion_stacking_v1', json.dumps(self.stacking_order))
        self.stacking_changed.emit()
        self.schedule_stacking()

    def schedule_stacking(self):
        if not self.stacking_stopped and not self.restacking and not self.stack_timer.isActive():
            self.stack_timer.start(0)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.ZOrderChange, QEvent.Type.MouseButtonPress):
            self.schedule_stacking()
        return False

    def apply_stacking_order(self):
        if self.stacking_stopped or self.restacking:
            return
        self.restacking = True
        try:
            for identity in reversed(self.stacking_order):
                item = self.find(identity)
                if item and item.pet.isVisible():
                    item.pet.raise_()
        finally:
            self.restacking = False

    def clean_settings(self, values):
        result = dict(self.defaults)
        if not isinstance(values, dict):
            return result
        for key, default in self.defaults.items():
            value = values.get(key, default)
            if isinstance(default, bool):
                result[key] = value if isinstance(value, bool) else default
            elif isinstance(value, int) and not isinstance(value, bool):
                if key in ('screen', 'layer'):
                    maximum = self.owner.screen.count() - 1 if key == 'screen' else 2
                    result[key] = max(0, min(value, maximum))
                else:
                    widget = getattr(self.owner, 'pet_size' if key == 'size' else key)
                    result[key] = max(widget.minimum(), min(value, widget.maximum()))
        return result

    def restore(self, record):
        identity = str(record.get('id', ''))[:64]
        if not identity or any(c.identity == identity for c in self.items):
            identity = uuid.uuid4().hex
        controller = PetController(self, identity, self.clean_settings(record.get('settings')), record.get('mode', 'functional'))
        pet = self.pet_class(controller)
        self.scheduler.attach(pet)
        path = record.get('character', '')
        path = path if isinstance(path, str) else ''
        try:
            pet.load_character(path, record.get('animations', {}))
        except (ValueError, TypeError):
            pet.load_character('')
            path = ''
            self.owner.companion_notice.setText('Один из наборов недоступен. Для него временно показан встроенный зайчик.')
        pet.position = float(record.get('x', pet.bounds().center().x())) if isinstance(record.get('x'), (int, float)) else float(pet.bounds().center().x())
        if controller.values.get('pinned') and isinstance(record.get('y'), (int, float)):
            pet.motion.y = float(record['y'])
        pet.place(on_floor=True)
        pet.apply_input_mode()
        self.set_layer(pet, controller.values['layer'])
        pet.setVisible(record.get('visible', True) is True)
        name = str(record.get('name', 'Чибик'))[:60] or 'Чибик'
        self.items.append(Companion(identity, name, path, pet, controller, clean_window_rule(record.get('window_rule'))))

    @staticmethod
    def set_layer(pet, layer):
        pet.apply_window_mode()

    @property
    def selected(self):
        return self.items[self.active]

    def capture_active(self):
        if self.loading or self.owner._loading_companion:
            return
        item = self.selected
        item.path = self.owner.character_path
        item.controller.values.update(self.owner.collect_settings())
        item.window_rule = clean_window_rule({
            'mode': self.owner.window_rule_mode.currentData(),
            'apps': self.owner.window_rule_apps.toPlainText().splitlines()})

    def records(self):
        return [{'id': c.identity, 'name': c.name, 'character': c.path,
                 'animations': c.pet.animations.specs, 'settings': c.controller.values, 'mode': c.controller.mode,
                 'visible': c.pet.requested_visible, 'x': c.pet.position, 'y': c.pet.motion.y,
                 'window_rule': c.window_rule} for c in self.items]

    def persist(self):
        if self.stacking_stopped or self.loading or self.owner._loading_companion:
            return
        self.save_timer.stop()
        self.capture_active()
        self.owner.store.setValue('companions_v1', json.dumps(self.records(), ensure_ascii=False))
        self.owner.store.setValue('active_companion', self.active)
        self.owner.store.setValue('companion_stacking_v1', json.dumps(self.stacking_order))
        self.stacking_changed.emit()
        self.update_load_label()

    def schedule_persist(self):
        if self.stacking_stopped:
            return
        self.capture_active()
        self.update_load_label()
        self.save_timer.start()

    def rebuild_selector(self):
        selector = self.owner.companion_select
        selector.blockSignals(True)
        selector.clear()
        for companion in self.items:
            selector.addItem(companion.name, companion.identity)
        selector.setCurrentIndex(self.active)
        selector.blockSignals(False)
        self.owner.remove_companion_button.setEnabled(len(self.items) > 1)
        self.owner.add_companion_button.setEnabled(len(self.items) < MAX_COMPANIONS)

    def load_active(self):
        self.owner._loading_companion = True
        item = self.selected
        self.owner.pet, self.owner.character_path = item.pet, item.path
        self.owner.store.setValue('character', item.path)
        for key, value in item.controller.values.items():
            widget = getattr(self.owner, 'pet_size' if key == 'size' else key)
            if isinstance(self.defaults[key], bool):
                widget.setChecked(value)
            elif key in ('screen', 'layer'):
                widget.setCurrentIndex(value)
            else:
                widget.setValue(value)
        self.owner.window_rule_mode.setCurrentIndex(self.owner.window_rule_mode.findData(item.window_rule['mode']))
        self.owner.window_rule_apps.setPlainText('\n'.join(item.window_rule['apps']))
        self.owner.companion_mode.setCurrentIndex(max(0, self.owner.companion_mode.findData(item.controller.mode)))
        self.owner.performance_mode.setCurrentIndex(max(0, self.owner.performance_mode.findData(self.performance_mode)))
        self.owner.character_label.setText(f'Персонаж: {item.name}')
        self.owner.sync_visibility()
        if hasattr(self.owner, 'presets'):
            self.owner.refresh_presets()
        self.owner.preview.update()
        self.owner.refresh_notification_binding()
        self.owner._loading_companion = False
        controls = getattr(self.owner, 'desktop_controls', None)
        if controls:
            controls.apply_visibility()

    def select(self, index):
        if self.loading or not 0 <= index < len(self.items):
            return
        self.capture_active()
        self.active = index
        self.rebuild_selector()
        self.load_active()
        self.persist()

    def select_id(self, identity):
        for index, item in enumerate(self.items):
            if item.identity == identity:
                self.select(index)
                return

    def add(self, mode='decorative'):
        if len(self.items) >= MAX_COMPANIONS:
            return
        self.capture_active()
        source = self.selected
        identity = uuid.uuid4().hex
        controller = PetController(self, identity, self.defaults, mode if isinstance(mode, str) else 'decorative')
        pet = self.pet_class(controller)
        self.scheduler.attach(pet)
        pet.load_character('')
        pet.apply_input_mode()
        pet.position = source.pet.position + source.pet.width() + 12
        pet.place(on_floor=True)
        self.set_layer(pet, controller.values['layer'])
        pet.show()
        self.items.append(Companion(identity, f'Чибик {len(self.items) + 1}', '', pet, controller))
        pet.installEventFilter(self)
        self.set_stacking_order([identity] + self.stacking_order)
        self.select(len(self.items) - 1)

    def reset_settings(self):
        """Reset only the selected companion's movement and display options."""
        self.capture_active()
        item = self.selected
        item.controller.values.update(self.defaults)
        item.window_rule = {'mode': 'all', 'apps': []}
        item.pet.drag_offset = None
        item.pet.motion.vy = 0
        item.pet.apply_window_mode()
        item.pet.place(on_floor=True)
        self.load_active()
        self.owner.save()
        self.persist()
        self.scheduler.wake()

    def remove(self):
        if len(self.items) == 1:
            return
        removed = self.items.pop(self.active)
        removed.pet.removeEventFilter(self)
        self.stacking_order = self.clean_stacking_order(self.stacking_order)
        self.scheduler.detach(removed.pet)
        removed.pet.hide()
        removed.pet.deleteLater()
        removed.controller.menu.deleteLater()
        self.active = min(self.active, len(self.items) - 1)
        hub = getattr(self.owner, 'event_hub', None)
        if hub:
            hub.remove_target(removed.identity)
        self.rebuild_selector()
        self.load_active()
        self.persist()

    def rename(self, name):
        if self.loading or self.owner._loading_companion:
            return
        self.selected.name = name.strip()[:60] or 'Чибик'
        self.rebuild_selector()
        self.persist()

    def find(self, identity):
        return next((item for item in self.items if item.identity == identity), None)

    @property
    def total_weight(self):
        return sum(MODES[item.controller.mode][1] for item in self.items)

    @property
    def active_weight(self):
        return sum(MODES[item.controller.mode][1] for item in self.items if item.pet.isVisible())

    def update_load_label(self):
        if not hasattr(self.owner, 'load_label'):
            return
        visible = sum(item.pet.isVisible() for item in self.items)
        text = f'{len(self.items)} / {MAX_COMPANIONS} чибиков · видны {visible} · активный вес {self.active_weight} / {SOFT_BUDGET} ед. · весь набор {self.total_weight}'
        if self.active_weight > SOFT_BUDGET:
            text += '\nПовышенная нагрузка: в режиме «Авто» частота кадров снижена.'
        self.owner.load_label.setText(text)

    def set_mode(self, identity, mode):
        item = self.find(identity)
        if not item or mode not in MODES:
            return
        item.controller.mode = mode
        if mode == 'decorative':
            item.pet.clear_notifications()
        self.rebuild_selector()
        if self.selected is item:
            self.owner.companion_mode.blockSignals(True)
            self.owner.companion_mode.setCurrentIndex(self.owner.companion_mode.findData(mode))
            self.owner.companion_mode.blockSignals(False)
        self.persist()
        self.scheduler.wake()
        self.owner.refresh_notification_binding()

    def set_performance(self, mode):
        if mode in ('auto', 'eco', 'smooth'):
            self.performance_mode = mode
            self.owner.store.setValue('performance_mode', mode)
            self.update_load_label()
            self.scheduler.wake()

    def shutdown(self):
        self.persist()
        self.stacking_stopped = True
        self.stack_timer.stop()
        self.save_timer.stop()
        self.scheduler.shutdown()
        for item in self.items:
            item.pet.timer.stop()
            item.pet.hide()
