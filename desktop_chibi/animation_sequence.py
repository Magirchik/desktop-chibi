"""Compose one state from ordered, independently editable animation parts."""
import copy
from pathlib import Path
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton,
    QScrollArea, QSlider, QSpinBox, QVBoxLayout,
)

from .animation_assets import MAX_SEQUENCE_STEPS
from .animations import Animations, STATES
from .pixel_ui import apply_theme, NoticeLabel, PixelPreview, PixelSurface, window_bar


class SequenceEditor(QDialog):
    def __init__(self, spec, available, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Сценарий анимации')
        apply_theme(self)
        self.resize(720, 740)
        self.steps = copy.deepcopy(spec.get('sequence', []))
        if spec and 'sequence' not in spec:
            self.steps = [{'animation': copy.deepcopy(spec), 'repeat': 1, 'mirror': False,
                           'name': Path(spec['file']).stem[:80]}]
        self.spec = {}
        self.loading = False
        self.playing = True
        self.elapsed = 0
        self.started = time.monotonic()
        self.player = Animations(self)
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.render)
        outer = QVBoxLayout(self)
        outer.addWidget(window_bar('Сценарий анимации'))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = PixelSurface()
        content.setObjectName('content')
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        layout = QVBoxLayout(content)
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        hint = QLabel('Части играют сверху вниз. Случайный выбор покоя выбирает сценарий целиком; '
                      'части внутри не перемешиваются.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.preview = PixelPreview()
        self.preview.setFixedHeight(144)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview)
        playback = QHBoxLayout()
        self.play_button = QPushButton('Пауза')
        self.play_button.clicked.connect(self.toggle_playback)
        playback.addWidget(self.play_button)
        restart = QPushButton('Сначала')
        restart.clicked.connect(self.restart)
        playback.addWidget(restart)
        self.info = QLabel()
        self.info.setWordWrap(True)
        playback.addWidget(self.info, 1)
        layout.addLayout(playback)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.setToolTip('Просмотреть весь сценарий по времени')
        self.seek.sliderMoved.connect(self.scrub)
        layout.addWidget(self.seek)
        add = QHBoxLayout()
        self.available = QComboBox()
        for state, animation in available.items():
            if 'sequence' not in animation:
                label = STATES.get(state, f'Покой: вариант {state[5:]}')
                self.available.addItem(label, copy.deepcopy(animation))
        add.addWidget(self.available, 1)
        self.add_existing = QPushButton('Добавить из набора')
        self.add_existing.clicked.connect(self.add_from_pack)
        add.addWidget(self.add_existing)
        self.add_files_button = QPushButton('Добавить файлы…')
        self.add_files_button.clicked.connect(self.add_files)
        add.addWidget(self.add_files_button)
        layout.addLayout(add)
        self.list = QListWidget()
        self.list.setMinimumHeight(164)
        self.list.currentRowChanged.connect(self.select_step)
        layout.addWidget(self.list, 1)
        order = QHBoxLayout()
        self.up = QPushButton('Выше')
        self.up.clicked.connect(lambda: self.move_step(-1))
        self.down = QPushButton('Ниже')
        self.down.clicked.connect(lambda: self.move_step(1))
        self.duplicate = QPushButton('Копия')
        self.duplicate.clicked.connect(self.duplicate_step)
        self.remove = QPushButton('Удалить часть')
        self.remove.clicked.connect(self.remove_step)
        for button in (self.up, self.down, self.duplicate, self.remove):
            order.addWidget(button)
        layout.addLayout(order)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setMaxLength(80)
        self.name.setPlaceholderText('Например, «Поворот вправо»')
        self.name.textEdited.connect(self.edit_properties)
        form.addRow('Имя части', self.name)
        self.repeat = QSpinBox()
        self.repeat.setRange(1, 100)
        self.repeat.setSuffix(' раз')
        self.repeat.valueChanged.connect(self.edit_properties)
        form.addRow('Проиграть часть', self.repeat)
        self.mirror = QCheckBox('Отразить по горизонтали')
        self.mirror.toggled.connect(self.edit_properties)
        form.addRow(self.mirror)
        layout.addLayout(form)
        self.edit = QPushButton('Редактировать анимацию части…')
        self.edit.setToolTip('Заменить GIF, настроить скорость и фрагмент или отредактировать кадры.')
        self.edit.clicked.connect(self.edit_step)
        layout.addWidget(self.edit)
        self.error = NoticeLabel()
        self.error.setObjectName('error')
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton('Сохранить сценарий', QDialogButtonBox.ButtonRole.AcceptRole)
        self.save_button.setObjectName('primary')
        buttons.addButton('Отмена', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.rebuild(0)

    def label(self, index):
        step = self.steps[index]
        name = step.get('name') or Path(step['animation']['file']).stem
        mirrored = ' · отражена' if step.get('mirror') else ''
        return f'{index + 1}. {name} · ×{step.get("repeat", 1)}{mirrored}'

    def rebuild(self, selected):
        self.list.blockSignals(True)
        self.list.clear()
        for index in range(len(self.steps)):
            self.list.addItem(self.label(index))
        self.list.setCurrentRow(min(selected, len(self.steps) - 1))
        self.list.blockSignals(False)
        self.select_step(self.list.currentRow())
        self.reload()

    def select_step(self, index):
        self.loading = True
        valid = 0 <= index < len(self.steps)
        step = self.steps[index] if valid else {}
        self.name.setText(step.get('name', ''))
        self.repeat.setValue(step.get('repeat', 1))
        self.mirror.setChecked(step.get('mirror', False))
        for widget in (self.name, self.repeat, self.mirror, self.edit, self.remove):
            widget.setEnabled(valid)
        self.up.setEnabled(valid and index > 0)
        self.down.setEnabled(valid and index < len(self.steps) - 1)
        self.duplicate.setEnabled(valid and len(self.steps) < MAX_SEQUENCE_STEPS)
        self.add_existing.setEnabled(self.available.count() > 0 and len(self.steps) < MAX_SEQUENCE_STEPS)
        self.add_files_button.setEnabled(len(self.steps) < MAX_SEQUENCE_STEPS)
        self.loading = False

    def edit_properties(self, *_):
        index = self.list.currentRow()
        if self.loading or not 0 <= index < len(self.steps):
            return
        self.steps[index].update(name=self.name.text(), repeat=self.repeat.value(), mirror=self.mirror.isChecked())
        self.list.item(index).setText(self.label(index))
        self.reload()

    def append_step(self, animation, name=''):
        if len(self.steps) >= MAX_SEQUENCE_STEPS:
            return
        self.steps.append({'animation': copy.deepcopy(animation), 'repeat': 1, 'mirror': False,
                           'name': name[:80] or Path(animation['file']).stem[:80]})
        self.rebuild(len(self.steps) - 1)

    def add_from_pack(self):
        animation = self.available.currentData()
        if animation:
            self.append_step(animation, self.available.currentText())

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Части сценария', '', 'Анимации (*.gif *.png *.webp)')
        for path in paths[:MAX_SEQUENCE_STEPS - len(self.steps)]:
            self.append_step({'file': path, 'frames': 1, 'fps': 8})

    def move_step(self, offset):
        index = self.list.currentRow()
        target = index + offset
        if 0 <= index < len(self.steps) and 0 <= target < len(self.steps):
            self.steps[index], self.steps[target] = self.steps[target], self.steps[index]
            self.rebuild(target)

    def duplicate_step(self):
        index = self.list.currentRow()
        if 0 <= index < len(self.steps) and len(self.steps) < MAX_SEQUENCE_STEPS:
            self.steps.insert(index + 1, copy.deepcopy(self.steps[index]))
            self.rebuild(index + 1)

    def remove_step(self):
        index = self.list.currentRow()
        if 0 <= index < len(self.steps):
            self.steps.pop(index)
            self.rebuild(index)

    def edit_step(self):
        from .animation_editor import AnimationEditor
        index = self.list.currentRow()
        if not 0 <= index < len(self.steps):
            return
        self.timer.stop()
        self.player.suspend()
        editor = AnimationEditor({'idle': self.steps[index]['animation']}, self, step_mode=True)
        editor.setWindowTitle('Анимация части сценария')
        try:
            if editor.exec() == QDialog.DialogCode.Accepted:
                animation = editor.specs.get('idle')
                if animation:
                    self.steps[index]['animation'] = copy.deepcopy(animation)
        finally:
            editor.deleteLater()
            self.rebuild(index)

    def reload(self):
        try:
            self.player.configure({'idle': {'sequence': self.steps}} if self.steps else {})
            self.error.setText('')
            self.save_button.setEnabled(bool(self.steps))
        except ValueError as error:
            self.player.configure({})
            self.error.setText(str(error))
            self.save_button.setEnabled(False)
        self.restart()

    def restart(self):
        self.started = time.monotonic()
        self.elapsed = 0
        self.player.restart()
        self.render()
        if self.isVisible() and self.playing:
            self.timer.start()

    def toggle_playback(self):
        if self.playing:
            self.elapsed = time.monotonic() - self.started
            clip = self.player.clips.get('idle')
            if clip:
                self.elapsed %= clip.duration
            self.timer.stop()
            self.player.suspend()
        else:
            self.started = time.monotonic() - self.elapsed
            if self.isVisible():
                self.timer.start()
        self.playing = not self.playing
        self.play_button.setText('Пауза' if self.playing else 'Играть')
        self.render()

    def scrub(self, value):
        clip = self.player.clips.get('idle')
        if clip is None:
            return
        if self.playing:
            self.toggle_playback()
        self.elapsed = value / 1000 * clip.duration
        self.render()

    def render(self):
        clip = self.player.clips.get('idle')
        seekable = clip is not None and not any(part.movie for part in clip.parts)
        self.seek.setEnabled(seekable)
        self.seek.setToolTip('Просмотреть весь сценарий по времени' if seekable else
                             'Для перемотки большого GIF задайте меньший размер в настройках его части.')
        if clip is None:
            self.preview.clear()
            self.info.setText('Добавьте первую часть')
            return
        elapsed = time.monotonic() - self.started if self.playing and self.isVisible() else self.elapsed
        frame = self.player.frame('idle', elapsed) if self.playing and self.isVisible() else clip.frame(elapsed, loop=False)
        self.preview.setPixmap(frame.scaled(128, 128, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.FastTransformation))
        index, repetition, _local, _cycle = clip.position(elapsed, loop=self.playing)
        self.info.setText(f'Часть {index + 1}/{len(clip.parts)} · повтор {repetition + 1} · всего {clip.duration:.2f} с')
        if not self.seek.isSliderDown():
            self.seek.setValue(round((elapsed % clip.duration if self.playing else elapsed) / clip.duration * 1000))

    def showEvent(self, event):
        super().showEvent(event)
        self.started = time.monotonic() - self.elapsed
        self.render()
        if self.playing:
            self.timer.start()

    def hideEvent(self, event):
        if self.playing:
            self.elapsed = time.monotonic() - self.started
            clip = self.player.clips.get('idle')
            if clip:
                self.elapsed %= clip.duration
        self.timer.stop()
        self.player.suspend()
        super().hideEvent(event)

    def save(self):
        try:
            self.player.configure({'idle': {'sequence': self.steps}})
            self.spec = copy.deepcopy(self.player.specs['idle'])
        except ValueError as error:
            QMessageBox.warning(self, 'Ошибка сценария', str(error))
            return
        self.accept()

    def done(self, result):
        self.timer.stop()
        self.player.configure({})
        super().done(result)
