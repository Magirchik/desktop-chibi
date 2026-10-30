"""Named immutable snapshots; swapping changes a pet's look, never its event identity."""
import json
import shutil
import uuid
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from .animations import Animations, default_notification, read_manifest
from .animation_assets import copy_animation_assets


class PresetLibrary:
    LIMIT = 128

    def __init__(self, owner):
        self.owner = owner
        self.entries = []
        try:
            entries = json.loads(owner.store.value('presets_v1', '[]'))
            if not isinstance(entries, list):
                raise ValueError('Invalid presets')
            seen = set()
            for entry in entries[:self.LIMIT]:
                if (isinstance(entry, dict) and isinstance(entry.get('id'), str)
                        and entry['id'] not in seen and entry['id'] != 'builtin'
                        and isinstance(entry.get('manifest'), str) and isinstance(entry.get('name'), str)):
                    seen.add(entry['id'])
                    self.entries.append(entry)
        except (ValueError, TypeError):
            owner.companion_notice.setText('Библиотека пресетов недоступна. Текущие чибики сохранены.')

    def persist(self):
        self.owner.store.setValue('presets_v1', json.dumps(self.entries, ensure_ascii=False))

    def find(self, identity):
        return next((entry for entry in self.entries if entry['id'] == identity), None)

    def save(self, name, *, replace=None):
        name = name.strip()[:60]
        if not name:
            raise ValueError('Введите имя пресета.')
        previous = self.find(replace)
        if replace is not None and previous is None:
            raise ValueError('Выберите свой пресет для обновления.')
        if previous is None and len(self.entries) >= self.LIMIT:
            raise ValueError('В библиотеке уже 128 пресетов. Удалите ненужный или обновите существующий.')
        manager = self.owner.manager
        manager.capture_active()
        item = manager.selected
        specs = item.pet.animations.specs
        if not specs:
            specs = {'notify': default_notification()}
        checker = Animations()
        try:
            checker.configure(specs)
            specs = checker.specs
        finally:
            checker.configure({})
            checker.deleteLater()
        root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)) / 'presets'
        folder = root / uuid.uuid4().hex
        try:
            folder.mkdir(parents=True)
            output = {}
            for state, spec in specs.items():
                output[state] = copy_animation_assets(spec, folder, state, relative_to=folder)
            manifest = folder / 'chibi.json'
            manifest.write_text(json.dumps({'version': 1, 'name': name, 'animations': output},
                                            ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError as error:
            if folder.is_dir():
                shutil.rmtree(folder)
            raise ValueError(f'Не удалось сохранить пресет: {error}') from error
        entry = {'id': previous['id'] if previous else uuid.uuid4().hex,
                 'name': name, 'manifest': str(manifest), 'settings': dict(item.controller.values)}
        if previous:
            self.entries[self.entries.index(previous)] = entry
        else:
            self.entries.append(entry)
        self.persist()
        return entry['id']

    def rename(self, identity, name):
        entry = self.find(identity)
        name = name.strip()[:60]
        if not entry or not name:
            raise ValueError('Выберите свой пресет и введите имя.')
        entry['name'] = name
        self.persist()

    def remove(self, identity):
        entry = self.find(identity)
        if not entry:
            raise ValueError('Этот пресет удалить нельзя.')
        self.entries.remove(entry)
        # Active pets can still reference this snapshot. Retain its assets.
        self.persist()

    def apply(self, identity, *, target=None, with_settings=True):
        manager = self.owner.manager
        item = manager.find(target) if target else manager.selected
        if item is None:
            raise ValueError('Чибик больше не существует.')
        manager.capture_active()
        if identity == 'builtin':
            path, specs, settings = '', {'notify': default_notification()}, dict(manager.defaults)
        else:
            entry = self.find(identity)
            if not entry:
                raise ValueError('Пресет не найден.')
            path = entry['manifest']
            _, specs = read_manifest(path)
            settings = manager.clean_settings(entry.get('settings', {}))
        # Clip validation is transactional: bad assets do not replace the pet.
        item.pet.load_character(path, specs)
        item.pet.clear_notifications()
        item.path = path
        if with_settings:
            item.controller.values.update(settings)
            item.pet.drag_offset = None
            item.pet.apply_input_mode()
            item.pet.place(on_floor=True)
            manager.set_layer(item.pet, item.controller.values['layer'])
        if manager.selected is item:
            manager.load_active()
        manager.persist()
        manager.scheduler.wake()
