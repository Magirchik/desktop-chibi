"""A small animation workshop: each state can use a GIF or a grid sprite sheet."""
import copy
import json
import tempfile
import time
import uuid
from bisect import bisect_right
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QTimer
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget,
)
from .animations import Animations, STATES, read_manifest, cached_gif
from .animation_assets import copy_animation_assets
from .pixel_ui import bunny_frame, apply_theme, PixelSurface, PixelTabs, PixelPreview, window_bar
from .pixel_editor import PixelEditor, PixelDocument
from .pixel_document import EDITOR_BYTES
from .gif_import import GifImport


class AnimationEditor(QDialog):
    def __init__(self, specs, parent=None, *, default_specs=None, step_mode=False):
        super().__init__(parent)
        self.setWindowTitle('Мастерская анимаций')
        apply_theme(self)
        self.setMinimumWidth(520)
        self.specs = copy.deepcopy(specs)
        self.default_specs = copy.deepcopy(specs if default_specs is None else default_specs)
        self.player = Animations(self)
        self.loading = False
        self.setAcceptDrops(True)
        self.edit_timer = QTimer(self)
        self.edit_timer.setSingleShot(True)
        self.edit_timer.timeout.connect(self.reload_preview)
        self.current = 'idle'
        self.started = time.monotonic()
        self.preview_playing = True
        self.paused_elapsed = 0
        self.preview_key = None
        self.drafts = tempfile.TemporaryDirectory(prefix='chibi-drawing-')
        self.created_character = False
        self.character_manifest = None
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = PixelSurface()
        content.setObjectName('content')
        scroll.setWidget(content)
        outer.addWidget(scroll)
        layout = QVBoxLayout(content)
        content_layout = layout
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        bar = window_bar('Мастерская анимаций')
        outer.insertWidget(0, bar)
        subtitle = QLabel('Выберите состояние: ему можно назначить файл или сценарий из нескольких анимаций.')
        subtitle.setObjectName('muted')
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        self.tabs = PixelTabs()
        layout.addWidget(self.tabs)
        animation_page = QWidget()
        self.tabs.addTab(animation_page, 'Анимация')
        animation_layout = QVBoxLayout(animation_page)
        pack_page = QWidget()
        self.tabs.addTab(pack_page, 'Набор')
        self.tabs.setTabVisible(1, not step_mode)
        pack_layout = QVBoxLayout(pack_page)
        layout = animation_layout
        states = QHBoxLayout()
        self.state = QComboBox()
        for key, label in STATES.items():
            self.state.addItem(label, key)
        for key in self.specs:
            if key not in STATES:
                self.state.addItem(f'Покой: вариант {key[5:]}', key)
        states.addWidget(self.state, 1)
        add = QPushButton('+ Вариант')
        add.setToolTip('Добавить ещё одну анимацию покоя. Варианты чередуются во время остановок.')
        add.clicked.connect(self.add_idle)
        states.addWidget(add)
        self.delete_button = QPushButton('Удалить')
        self.delete_button.setToolTip('Дополнительный idle удаляется из списка. У базового состояния удаляется назначенная анимация.')
        self.delete_button.clicked.connect(self.remove_animation)
        states.addWidget(self.delete_button)
        layout.addLayout(states)
        if step_mode:
            for widget in (self.state, add, self.delete_button):
                widget.hide()
        self.preview = PixelPreview()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(150)
        self.preview.setObjectName('animationPreview')
        layout.addWidget(self.preview)
        playback = QHBoxLayout()
        self.play_button = QPushButton('Пауза')
        self.play_button.clicked.connect(self.toggle_preview)
        playback.addWidget(self.play_button)
        restart = QPushButton('Сначала')
        restart.clicked.connect(self.restart_preview)
        playback.addWidget(restart)
        self.clip_info = QLabel()
        self.clip_info.setObjectName('muted')
        self.clip_info.setWordWrap(True)
        playback.addWidget(self.clip_info, 1)
        layout.addLayout(playback)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setToolTip('Пауза и просмотр отдельного кадра GIF')
        self.seek.sliderMoved.connect(self.seek_frame)
        layout.addWidget(self.seek)
        self.gif_range = QWidget()
        range_layout = QVBoxLayout(self.gif_range)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_form = QHBoxLayout()
        range_layout.addLayout(range_form)
        markers = QHBoxLayout()
        self.range_buttons = {}
        for key, label in [('first', 'Начало здесь'), ('last', 'Конец здесь'), ('reset', 'Весь GIF')]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, boundary=key: self.set_boundary(boundary))
            markers.addWidget(button)
            self.range_buttons[key] = button
        range_layout.addLayout(markers)
        self.gif_frame_number = QLabel()
        self.gif_frame_number.setObjectName('muted')
        range_layout.addWidget(self.gif_frame_number)
        layout.addWidget(self.gif_range)
        self.file_label = QLabel()
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)
        files = QHBoxLayout()
        choose = QPushButton('Загрузить файл…')
        choose.clicked.connect(self.choose)
        self.clear_button = QPushButton('Убрать файл')
        self.clear_button.clicked.connect(self.clear)
        files.addWidget(choose, 1)
        files.addWidget(self.clear_button)
        layout.addLayout(files)
        self.sequence_button = QPushButton('Собрать сценарий…')
        self.sequence_button.setToolTip('Упорядоченные части: например, танец влево → поворот → танец вправо. Порядок внутри сценария сохраняется.')
        self.sequence_button.clicked.connect(self.edit_sequence)
        self.sequence_button.setVisible(not step_mode)
        layout.addWidget(self.sequence_button)
        priority_form = QFormLayout()
        self.priority = QSpinBox()
        self.priority.setRange(0, 100)
        self.priority.setSpecialValueText('Обычный · 0')
        self.priority.setToolTip('При одновременных событиях показывается анимация с большим приоритетом. При равных — последнее событие.')
        self.priority.valueChanged.connect(self.edit_priority)
        priority_form.addRow('Приоритет события', self.priority)
        layout.addLayout(priority_form)
        priority_hint = QLabel('Большее число перекрывает меньшее. После окончания важного события вернётся ещё активное событие. '
                              'Ходьба и покой уступают событиям; перетаскивание, падение и приземление всегда выше.')
        priority_hint.setWordWrap(True)
        priority_hint.setObjectName('muted')
        layout.addWidget(priority_hint)
        drawing = QHBoxLayout()
        self.draw_button = QPushButton('Редактировать кадры…')
        self.draw_button.setToolTip('Редактирование до 512×512 с сохранением ритма GIF. Для больших файлов можно выбрать размер и фрагмент; уменьшение для чибика доступно после рисования.')
        self.draw_button.setObjectName('primary')
        self.draw_button.clicked.connect(self.draw_animation)
        create = QPushButton('Нарисовать нового чибика…')
        create.setToolTip('Нарисовать нового персонажа с чистого холста. Затем добавьте ему анимации.')
        create.clicked.connect(self.create_character)
        drawing.addWidget(self.draw_button, 1)
        pack_layout.addWidget(create)
        layout.addLayout(drawing)
        self.character_name = QLineEdit('Мой чибик')
        self.character_name.setPlaceholderText('Имя нового чибика')
        self.character_name.setToolTip('Имя нового персонажа')
        self.character_name.setVisible(False)
        pack_layout.addWidget(self.character_name)
        self.pingpong = QCheckBox('Туда и обратно')
        self.pingpong.toggled.connect(self.edited)
        layout.addWidget(self.pingpong)
        self.gif_hint = QLabel('Исходные задержки кадров сохраняются. Скорость, размер и обрезка — в настройках GIF.')
        self.gif_hint.setObjectName('muted')
        self.gif_hint.setWordWrap(True)
        layout.addWidget(self.gif_hint)
        self.gif_toggle = QPushButton('Настройки GIF ▸')
        self.gif_toggle.setCheckable(True)
        layout.addWidget(self.gif_toggle)
        self.gif_options = QWidget()
        gif_form = QFormLayout(self.gif_options)
        self.gif_fields = {}
        for key, label, low, high, default in [
                ('speed', 'Скорость', 25, 400, 100),
                ('first_frame', 'Первый кадр', 1, 10000, 1),
                ('last_frame', 'Последний кадр', 0, 10000, 0),
                ('max_size', 'Размер, пкс', 0, 1024, 0)]:
            spin = QSpinBox()
            spin.setRange(low, high)
            spin.setValue(default)
            if key == 'speed':
                spin.setSuffix(' %')
            elif key == 'last_frame':
                spin.setSpecialValueText('До конца')
            elif key == 'max_size':
                spin.setSpecialValueText('Исходный')
                spin.setToolTip('Длинная сторона кадра. 128–256 пкс обычно достаточно для чибика и требует меньше памяти.')
            spin.valueChanged.connect(self.edit_gif)
            if key in ('first_frame', 'last_frame'):
                range_form.addWidget(QLabel('С кадра' if key == 'first_frame' else 'По кадр'))
                range_form.addWidget(spin, 1)
            else:
                gif_form.addRow(label, spin)
            self.gif_fields[key] = spin
        self.reverse = QCheckBox('Начинать с последнего кадра')
        self.reverse.toggled.connect(self.edit_gif)
        gif_form.addRow(self.reverse)
        hint = QLabel('Настройки применяются к копии при воспроизведении. Исходный GIF сохраняется без изменений.')
        hint.setWordWrap(True)
        hint.setObjectName('muted')
        gif_form.addRow(hint)
        self.gif_options.hide()
        self.gif_toggle.toggled.connect(self.gif_options.setVisible)
        self.gif_toggle.toggled.connect(lambda checked: self.gif_toggle.setText('Настройки GIF ▾' if checked else 'Настройки GIF ▸'))
        layout.addWidget(self.gif_options)
        self.sheet_toggle = QPushButton('Параметры нарезки и скорости ▸')
        self.sheet_toggle.setCheckable(True)
        layout.addWidget(self.sheet_toggle)
        self.sheet_options = QWidget()
        self.sheet_options.setVisible(False)
        self.sheet_toggle.toggled.connect(self.sheet_options.setVisible)
        self.sheet_toggle.toggled.connect(lambda checked: self.sheet_toggle.setText(
            'Параметры нарезки и скорости ▾' if checked else 'Параметры нарезки и скорости ▸'))
        layout.addWidget(self.sheet_options)
        form = QFormLayout()
        self.fields = {}
        for key, label, low, high, default in [
                ('width', 'Ширина кадра, пкс', 0, 8192, 0),
                ('height', 'Высота кадра, пкс', 0, 8192, 0),
                ('frames', 'Количество кадров', 1, 512, 1),
                ('fps', 'Кадров в секунду', 1, 60, 8)]:
            spin = QSpinBox()
            spin.setRange(low, high)
            spin.setValue(default)
            if key in ('width', 'height'):
                spin.setSpecialValueText('Авто')
                spin.setToolTip('«Авто» использует весь файл. Для листа кадров укажите размер одного кадра.')
            spin.valueChanged.connect(self.edited)
            self.fields[key] = spin
            form.addRow(label, spin)
        self.sheet_options.setLayout(form)
        hint = QLabel('PNG / WebP: кадры в сетке слева направо, затем сверху вниз.\n'
                      'GIF: используется его собственная скорость.\n'
                      'Пустое состояние использует текстуру покоя.\n'
                      'Дополнительные idle проигрываются случайно во время остановок.\n'
                      'Сценарий объединяет несколько частей в неизменном порядке.')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        pack_layout.addWidget(hint)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setObjectName('error')
        outer.addWidget(self.error)
        transfer = QHBoxLayout()
        load = QPushButton('Импорт набора')
        load.clicked.connect(self.import_pack)
        export = QPushButton('Экспорт набора')
        export.clicked.connect(self.export_pack)
        transfer.addWidget(load)
        transfer.addWidget(export)
        pack_layout.addLayout(transfer)
        reset = QPushButton('Вернуть исходный набор')
        reset.setToolTip('Вернуть анимации исходной текстуры или JSON-набора. Изменения вступят в силу после «Применить».')
        reset.clicked.connect(self.reset_pack)
        pack_layout.addWidget(reset)
        pack_layout.addStretch()
        animation_layout.addStretch()
        content_layout.addStretch()
        buttons = QDialogButtonBox()
        apply = buttons.addButton('Применить', QDialogButtonBox.ButtonRole.AcceptRole)
        apply.setObjectName('primary')
        buttons.addButton('Отмена', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.state.currentIndexChanged.connect(self.change_state)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.render_preview)
        self.tabs.currentChanged.connect(self.sync_preview_timer)
        self.refresh()
        self.resize(620, 720)

    def refresh(self):
        self.edit_timer.stop()
        self.loading = True
        spec = self.specs.get(self.current, {})
        self.delete_button.setEnabled(bool(spec) or self.current not in STATES)
        self.clear_button.setEnabled(bool(spec))
        sequence = 'sequence' in spec
        self.file_label.setText(f'Сценарий · частей: {len(spec["sequence"])}' if sequence else
                                Path(spec['file']).name if spec else 'Отдельный файл не выбран')
        self.file_label.setToolTip(spec.get('file', ''))
        self.sequence_button.setText('Редактировать сценарий…' if sequence else 'Собрать сценарий…')
        self.draw_button.setEnabled(not sequence)
        self.clear_button.setText('Убрать сценарий' if sequence else 'Убрать файл')
        self.priority.setValue(spec.get('priority', 0))
        self.priority.setEnabled(bool(spec))
        gif = Path(spec.get('file', '')).suffix.lower() == '.gif'
        for key, spin in self.fields.items():
            spin.setValue(spec.get(key, 8 if key == 'fps' else 1 if key == 'frames' else 0))
            spin.setEnabled(bool(spec) and not gif and not sequence)
        self.pingpong.setChecked(spec.get('pingpong', False))
        self.pingpong.setEnabled(bool(spec) and not sequence)
        self.pingpong.setToolTip('Кадры проходят вперёд и возвращаются назад без повторения крайних кадров. Для GIF сохраняются исходные задержки.')
        self.gif_hint.setVisible(gif)
        self.gif_toggle.setVisible(gif)
        self.gif_options.setVisible(gif and self.gif_toggle.isChecked())
        self.seek.setVisible(gif)
        self.gif_range.setVisible(gif)
        if gif:
            count = QImageReader(spec['file']).imageCount()
            for key in ('first_frame', 'last_frame'):
                self.gif_fields[key].setMaximum(min(10000, count) if count > 0 else 10000)
        for key, spin in self.gif_fields.items():
            spin.setValue(spec.get(key, 100 if key == 'speed' else 0) + (1 if key == 'first_frame' else 0))
        self.reverse.setChecked(spec.get('reverse', False))
        self.sheet_toggle.setVisible(bool(spec) and not gif and not sequence)
        self.sheet_options.setVisible(bool(spec) and not gif and not sequence and self.sheet_toggle.isChecked())
        self.loading = False
        self.reload_preview()

    def edit_gif(self, *_):
        if self.loading or self.current not in self.specs:
            return
        spec = self.specs[self.current]
        for key, spin in self.gif_fields.items():
            spec[key] = spin.value() - (1 if key == 'first_frame' else 0)
        spec['reverse'] = self.reverse.isChecked()
        self.edit_timer.start(200)

    def edited(self, *_):
        if self.loading or self.current not in self.specs:
            return
        spec = self.specs[self.current]
        for key, spin in self.fields.items():
            if key == 'fps' and spec.get(key, 8) != spin.value():
                spec.pop('delays', None)
            if key in ('width', 'height', 'frames') and spec.get(key) != spin.value():
                spec.pop('project', None)
            spec[key] = spin.value()
        spec['pingpong'] = self.pingpong.isChecked()
        self.reload_preview()

    def edit_priority(self, value):
        if not self.loading and self.current in self.specs:
            self.specs[self.current]['priority'] = value

    def reload_preview(self):
        self.edit_timer.stop()
        try:
            spec = self.specs.get(self.current)
            preview_specs = {'idle': self.specs['idle']} if 'idle' in self.specs else {}
            if spec:
                preview_specs[self.current] = spec
            self.player.configure(preview_specs)
            self.started = time.monotonic()
            self.paused_elapsed = 0
            self.preview_key = None
            self.error.setText('')
            clip = self.player.clips.get(self.current, self.player.clips.get('idle'))
            count = len(clip.frames) if clip and clip.frames else clip.movie.frameCount() if clip and clip.movie else 0
            self.seek.setRange(0, max(0, count - 1))
            self.seek.setEnabled(bool(clip and clip.gif_delays))
            for key in ('first', 'last'):
                self.range_buttons[key].setEnabled(bool(clip and clip.gif_delays))
            self.seek.setValue(0)
            duration = f'{clip.duration:.2f}'.replace('.', ',') if clip else ''
            size = clip.frames[0].size() if clip and clip.frames else None
            dimensions = f' · {size.width()}×{size.height()}' if size else ''
            self.clip_info.setText((f'Сценарий · {len(clip.parts)} частей · {duration} с' if hasattr(clip, 'parts') else
                                   f'Кадров: {count} · {duration} с{dimensions}') if clip else 'Встроенная анимация')
        except ValueError as error:
            self.error.setText(str(error))
        self.render_preview()
        self.sync_preview_timer()

    def render_preview(self):
        elapsed = time.monotonic() - self.started if self.preview_playing and self.isVisible() else self.paused_elapsed
        running = self.preview_playing and self.isVisible() and self.tabs.currentIndex() == 0
        if running:
            frame = self.player.frame(self.current, elapsed)
        else:
            clip = self.player.clips.get(self.current, self.player.clips.get('idle'))
            frame = clip.frame(elapsed, loop=self.current != 'land') if clip else bunny_frame(state=self.current, elapsed=elapsed)
        if frame.isNull():
            frame = bunny_frame(state=self.current, elapsed=elapsed)
        clip = self.player.clips.get(self.current)
        if running and clip and clip.gif_delays and not self.seek.isSliderDown():
            index = min(len(clip.order) - 1, bisect_right(clip.frame_ends, (max(0, elapsed) * 1000) % clip.frame_ends[-1]))
            source_index = clip.order[index]
            self.seek.setValue(len(clip.frames) - source_index - 1 if clip.spec.get('reverse') else source_index)
            next_frame = clip.frame_ends[index] - (max(0, elapsed) * 1000) % clip.frame_ends[-1]
            self.timer.setInterval(max(8, min(250, round(next_frame))))
        if clip and clip.gif_delays:
            self.gif_frame_number.setText(f'Кадр исходника: {self.source_frame()}')
        key = frame.cacheKey()
        if key == self.preview_key:
            return
        self.preview_key = key
        self.preview.setPixmap(frame.scaled(128, 128, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.FastTransformation))

    def toggle_preview(self):
        if self.preview_playing:
            self.paused_elapsed = time.monotonic() - self.started
        else:
            self.started = time.monotonic() - self.paused_elapsed
        self.preview_playing = not self.preview_playing
        self.play_button.setText('Пауза' if self.preview_playing else 'Играть')
        self.sync_preview_timer()

    def seek_frame(self, index):
        clip = self.player.clips.get(self.current)
        if not clip or not clip.gif_delays:
            return
        if self.preview_playing:
            self.toggle_preview()
        self.seek.setValue(index)
        order = tuple(range(len(clip.frames)))
        if clip.spec.get('reverse', False):
            order = order[::-1]
        self.paused_elapsed = sum(clip.gif_delays[i] for i in order[:index]) / 1000 * 100 / clip.speed
        self.started = time.monotonic() - self.paused_elapsed
        self.render_preview()

    def source_frame(self):
        clip = self.player.clips.get(self.current)
        if not clip or not clip.frames:
            return 1
        index = self.seek.value()
        if clip.spec.get('reverse'):
            index = len(clip.frames) - 1 - index
        return clip.spec.get('first_frame', 0) + index + 1

    def set_boundary(self, boundary):
        frame = self.source_frame()
        if boundary == 'reset':
            self.gif_fields['first_frame'].setValue(1)
            self.gif_fields['last_frame'].setValue(0)
        elif boundary == 'first':
            end = self.gif_fields['last_frame'].value()
            if end and end < frame:
                self.gif_fields['last_frame'].setValue(frame)
            self.gif_fields['first_frame'].setValue(frame)
        else:
            if frame < self.gif_fields['first_frame'].value():
                self.gif_fields['first_frame'].setValue(frame)
            self.gif_fields['last_frame'].setValue(frame)
        self.reload_preview()

    def restart_preview(self):
        self.started = time.monotonic()
        self.paused_elapsed = 0
        self.player.restart()
        self.render_preview()

    def sync_preview_timer(self, *_):
        if self.isVisible() and self.tabs.currentIndex() == 0 and self.preview_playing:
            self.timer.start(33)
        else:
            self.timer.stop()
            self.player.suspend()

    def showEvent(self, event):
        super().showEvent(event)
        self.started = time.monotonic() - self.paused_elapsed
        self.sync_preview_timer()

    def hideEvent(self, event):
        if self.preview_playing:
            self.paused_elapsed = time.monotonic() - self.started
        super().hideEvent(event)
        self.sync_preview_timer()

    def change_state(self, *_):
        self.current = self.state.currentData()
        self.refresh()

    def add_idle(self):
        if self.state.count() >= 32:
            return
        index = 1
        while self.state.findData(f'idle_{index}') >= 0:
            index += 1
        self.state.addItem(f'Покой: вариант {index}', f'idle_{index}')
        self.state.setCurrentIndex(self.state.count() - 1)

    def choose(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Анимация состояния', '', 'Изображения (*.png *.webp *.gif)')
        if path:
            self.load_file(path)

    def load_file(self, path):
        checker = Animations(self)
        try:
            spec = {'file': str(path), 'width': 0, 'height': 0, 'frames': 1, 'fps': 8,
                    'priority': self.specs.get(self.current, {}).get('priority', 0)}
            checker.configure({self.current: spec})
            self.specs[self.current] = spec
            self.refresh()
            return True
        except ValueError as error:
            self.error.setText(str(error))
            return False
        finally:
            checker.configure({})
            checker.deleteLater()

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).suffix.lower() in ('.gif', '.png', '.webp'):
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile() and self.load_file(urls[0].toLocalFile()):
            event.acceptProposedAction()

    def clear(self):
        self.specs.pop(self.current, None)
        self.refresh()

    def edit_sequence(self):
        from .animation_sequence import SequenceEditor
        self.edit_timer.stop()
        self.timer.stop()
        self.player.suspend()
        spec = self.specs.get(self.current, {})
        dialog = SequenceEditor(spec, self.specs, self)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.specs[self.current] = {**dialog.spec, 'priority': spec.get('priority', 0)}
        finally:
            dialog.deleteLater()
            self.refresh()

    def frames_for_drawing(self):
        document = self.drawing_document()
        return document.frames, document.fps

    def drawing_document(self, override=None):
        key = self.current if self.current in self.specs else 'idle'
        spec = dict(override or self.specs.get(key, {}))
        if 'sequence' in spec:
            raise ValueError('Откройте сценарий и выберите часть для редактирования кадров.')
        if not spec:
            return PixelDocument([bunny_frame(state=self.current).toImage()])
        if spec.get('project'):
            document = PixelDocument.load_project(spec['project'])
            document.fps = spec.get('fps', document.fps)
            document.delays = PixelDocument.validate_delays(spec.get('delays'), document.count)
            return document
        path = Path(spec['file'])
        if path.suffix.lower() == '.gif':
            frames, delays = cached_gif(path, spec.get('first_frame', 0), spec.get('last_frame', 0),
                                       spec.get('max_size', 0), memory_limit=EDITOR_BYTES)
            images = [frame.toImage() for frame in frames]
            delays = [max(10, round(delay * 100 / spec.get('speed', 100))) for delay in delays]
            if spec.get('reverse'):
                images.reverse()
                delays.reverse()
            fps = max(1, min(60, round(1000 * len(delays) / sum(delays))))
            return PixelDocument(images, fps=fps, delays=delays)
        loader = Animations(self)
        try:
            loader.configure({key: spec})
            clip = loader.clips[key]
            return PixelDocument([frame.toImage() for frame in clip.frames], fps=clip.fps,
                                 delays=list(clip.gif_delays) if clip.gif_delays else None)
        finally:
            loader.configure({})
            loader.deleteLater()

    def write_drawing(self, document, output_size=0):
        dimensions = document.export_dimensions(output_size)
        width, height = dimensions.width(), dimensions.height()
        path = Path(self.drafts.name) / (uuid.uuid4().hex + '.png')
        if not document.sheet(max(width, height)).save(str(path)):
            raise ValueError('Не удалось сохранить нарисованную анимацию.')
        project = path.with_suffix('.pixel.json')
        document.save_project(project)
        spec = {'file': str(path), 'project': str(project), 'width': width, 'height': height,
                'frames': document.count, 'fps': document.fps, 'pingpong': document.pingpong}
        if document.delays:
            spec['delays'] = list(document.delays)
        return spec

    def draw_animation(self):
        self.edit_timer.stop()
        key = self.current if self.current in self.specs else 'idle'
        spec = dict(self.specs.get(key, {}))
        if Path(spec.get('file', '')).suffix.lower() == '.gif':
            reader = QImageReader(spec['file'])
            size, count = reader.size(), reader.imageCount()
            chosen_size = spec.get('max_size', 0)
            if chosen_size and max(size.width(), size.height()) > chosen_size:
                edited_size = size.scaled(chosen_size, chosen_size, Qt.AspectRatioMode.KeepAspectRatio)
            else:
                edited_size = size
            selected_count = (spec.get('last_frame') or count) - spec.get('first_frame', 0)
            if (max(edited_size.width(), edited_size.height()) > 512 or selected_count > 512
                    or edited_size.width() * edited_size.height() * selected_count * 4 > EDITOR_BYTES):
                preparation = GifImport(size, count, spec, self)
                try:
                    if preparation.exec() != QDialog.DialogCode.Accepted:
                        return
                    spec.update(preparation.values())
                finally:
                    preparation.deleteLater()
        try:
            document = self.drawing_document(spec)
            document.pingpong = spec.get('pingpong', document.pingpong)
            editor = PixelEditor(parent=self, document=document,
                                 title=STATES.get(self.current, self.state.currentText()))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Не удалось открыть рисунок', str(error))
            return
        try:
            if editor.exec() == QDialog.DialogCode.Accepted:
                priority = self.specs.get(self.current, {}).get('priority', 0)
                self.specs[self.current] = {**self.write_drawing(editor.document, editor.output_size.currentData()),
                                            'priority': priority}
                self.refresh()
        except ValueError as error:
            QMessageBox.warning(self, 'Не удалось сохранить рисунок', str(error))
        finally:
            editor.deleteLater()

    def create_character(self):
        editor = PixelEditor(parent=self, title='Новый чибик')
        try:
            if editor.exec() == QDialog.DialogCode.Accepted:
                self.specs = {'idle': self.write_drawing(editor.document, editor.output_size.currentData())}
                self.created_character = True
                self.character_name.show()
                self.tabs.setCurrentIndex(1)
                self.rebuild_states()
        except ValueError as error:
            QMessageBox.warning(self, 'Не удалось создать чибика', str(error))
        finally:
            editor.deleteLater()

    def remove_animation(self):
        self.specs.pop(self.current, None)
        if self.current not in STATES:
            self.state.blockSignals(True)
            self.state.removeItem(self.state.currentIndex())
            self.state.setCurrentIndex(0)
            self.state.blockSignals(False)
            self.current = 'idle'
        self.refresh()

    def rebuild_states(self):
        self.state.blockSignals(True)
        self.state.clear()
        for key, label in STATES.items():
            self.state.addItem(label, key)
        for key in self.specs:
            if key not in STATES:
                self.state.addItem(f'Покой: вариант {key[5:]}', key)
        self.state.blockSignals(False)
        self.current = 'idle'
        self.refresh()

    def reset_pack(self):
        self.specs = copy.deepcopy(self.default_specs)
        self.created_character = False
        self.character_name.hide()
        self.rebuild_states()

    def import_pack(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Импорт персонажа', '', 'Набор чиби (*.json)')
        if not path:
            return
        try:
            _, specs = read_manifest(path)
            checker = Animations(self)
            try:
                checker.configure(specs)
                specs = checker.specs
            finally:
                checker.deleteLater()
            self.specs = specs
            self.created_character = False
            self.character_name.hide()
            self.rebuild_states()
        except ValueError as error:
            QMessageBox.warning(self, 'Ошибка импорта', str(error))

    def export_pack(self):
        try:
            checker = Animations(self)
            try:
                checker.configure(self.specs)
            finally:
                checker.deleteLater()
        except ValueError as error:
            QMessageBox.warning(self, 'Ошибка анимации', str(error))
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Экспорт персонажа', 'chibi.json', 'Набор чиби (*.json)')
        if not path:
            return
        target = Path(path).with_suffix('.json')
        assets = target.parent / (target.stem + '_assets')
        try:
            assets.mkdir(exist_ok=True)
            output = {}
            for state, spec in self.specs.items():
                output[state] = copy_animation_assets(spec, assets, state, relative_to=target.parent)
            target.write_text(json.dumps({'version': 1, 'name': target.stem, 'animations': output},
                                         ensure_ascii=False, indent=2), encoding='utf-8')
            self.error.setText('Набор сохранён. Переносите JSON вместе с папкой *_assets.')
        except OSError as error:
            QMessageBox.warning(self, 'Ошибка экспорта', str(error))

    def apply(self):
        try:
            self.player.configure(self.specs)
            # Commit only generated drafts; source images are never overwritten.
            drafts = Path(self.drafts.name).resolve()
            output = {}
            storage = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)) / 'characters'
            for state, spec in self.specs.items():
                output[state] = copy_animation_assets(spec, storage, uuid.uuid4().hex, only_from=drafts)
            self.player.configure(output)
            if self.created_character:
                storage.mkdir(parents=True, exist_ok=True)
                manifest = storage / (uuid.uuid4().hex + '.json')
                manifest.write_text(json.dumps({'version': 1, 'name': self.character_name.text().strip() or 'Мой чибик', 'animations': output},
                                               ensure_ascii=False, indent=2), encoding='utf-8')
                self.character_manifest = str(manifest)
            self.specs = output
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Ошибка анимации', str(error))
            return
        self.accept()

    def done(self, result):
        self.edit_timer.stop()
        self.timer.stop()
        self.player.configure({})
        self.drafts.cleanup()
        super().done(result)
