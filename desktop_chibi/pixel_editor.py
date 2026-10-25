"""In-app pixel drawing and frame animation, without modifying source assets."""
import time
from bisect import bisect_right
from itertools import accumulate

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSpinBox, QMenu,
    QVBoxLayout, QWidget,
)
from .pixel_ui import apply_theme, PixelSurface, PixelPreview, window_bar, _tile
from .appearance import appearance, theme_color
from .pixel_document import PixelDocument, blank_image
from .animations import frame_index

PALETTE = ['#53406f', '#7961aa', '#c4a8ed', '#eee5fa', '#ffffff', '#f2a9d0',
           '#e16c9b', '#a13f78', '#435575', '#9edbd4', '#edfff5', '#e8c17c']


class PixelCanvas(QWidget):
    changed = Signal()
    picked = Signal(QColor)

    def __init__(self, document):
        super().__init__()
        self.document = document
        self.zoom = 10
        self.tool = 'brush'
        self.color = QColor(PALETTE[0])
        self.brush_size = 1
        self.grid = True
        self.onion = True
        self.previous = None
        self.stroke_color = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.resize_canvas()

    def resize_canvas(self):
        self.setFixedSize(self.document.image.width() * self.zoom, self.document.image.height() * self.zoom)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.drawTiledPixmap(event.rect(), _tile(True, appearance().theme))
        if self.onion and self.document.index > 0:
            p.setOpacity(.25)
            p.drawImage(self.rect(), self.document.composite(self.document.index - 1))
            p.setOpacity(1)
        p.drawImage(self.rect(), self.document.composite(self.document.index))
        if self.grid and self.zoom >= 5:
            grid_color = QColor(theme_color('#9e7cb1'))
            grid_color.setAlpha(90)
            p.setPen(QPen(grid_color))
            for x in range(event.rect().left() // self.zoom * self.zoom, event.rect().right() + 1, self.zoom):
                p.drawLine(x, 0, x, self.height())
            for y in range(event.rect().top() // self.zoom * self.zoom, event.rect().bottom() + 1, self.zoom):
                p.drawLine(0, y, self.width(), y)

    def pixel(self, event):
        point = event.position().toPoint()
        return (max(0, min(self.document.image.width() - 1, point.x() // self.zoom)),
                max(0, min(self.document.image.height() - 1, point.y() // self.zoom)))

    def mousePressEvent(self, event):
        if event.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        x, y = self.pixel(event)
        if (self.tool == 'pick' or event.modifiers() & Qt.KeyboardModifier.ControlModifier) and event.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(self.document.composite(self.document.index).pixelColor(x, y))
            return
        if self.document.layer['locked']:
            return
        color = QColor(Qt.GlobalColor.transparent) if self.tool == 'eraser' or event.button() == Qt.MouseButton.RightButton else self.color
        if self.tool == 'fill':
            self.document.fill(x, y, color)
        else:
            self.document.begin_edit()
            self.previous = (x, y)
            self.stroke_color = color
            self.document.line((x, y), (x, y), color, self.brush_size)
        self.update()
        self.changed.emit()

    def mouseMoveEvent(self, event):
        if self.previous is not None:
            point = self.pixel(event)
            self.document.line(self.previous, point, self.stroke_color, self.brush_size)
            self.previous = point
            self.update()
            self.changed.emit()

    def mouseReleaseEvent(self, event):
        self.previous = None
        self.document.prune_history()


class PixelEditor(QDialog):
    def __init__(self, frames=None, fps=8, parent=None, *, title='Рисование анимации', document=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        apply_theme(self)
        self.document = document or PixelDocument(frames, fps=fps)
        self.canvas = PixelCanvas(self.document)
        self.canvas.zoom = max(1, min(12, 320 // max(self.document.image.width(), self.document.image.height())))
        self.canvas.resize_canvas()
        self.started = time.monotonic()
        self.playing = True
        self.preview_key = None
        self.thumbnail_keys = {}
        self._timing_key = None
        self.setMinimumSize(680, 520)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = PixelSurface()
        content.setObjectName('content')
        scroll.setWidget(content)
        outer.addWidget(scroll)
        root = QVBoxLayout(content)
        root.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        bar = window_bar('Редактор кадров' if title == 'Рисование анимации' else f'Кадры: {title}')
        outer.insertWidget(0, bar)
        body = QHBoxLayout()
        left = QVBoxLayout()
        tools = QHBoxLayout()
        group = QButtonGroup(self)
        self.tool_buttons = {}
        for key, label in [('brush', 'Кисть'), ('eraser', 'Ластик'), ('fill', 'Заливка'), ('pick', 'Пипетка')]:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setToolTip({'brush': 'Кисть · B', 'eraser': 'Ластик · E',
                               'fill': 'Заливка · F', 'pick': 'Пипетка · I'}[key])
            button.setChecked(key == 'brush')
            button.clicked.connect(lambda checked, tool=key: setattr(self.canvas, 'tool', tool))
            group.addButton(button)
            self.tool_buttons[key] = button
            tools.addWidget(button)
        left.addLayout(tools)
        colors = QHBoxLayout()
        for color in PALETTE:
            button = QPushButton()
            button.setFixedSize(25, 25)
            button.setStyleSheet(f'background: {color}; border: 2px solid #927cb0; padding: 0; min-height: 0;')
            button.setToolTip(color)
            button.clicked.connect(lambda checked, c=color: self.set_color(QColor(c)))
            colors.addWidget(button)
        colors.addStretch()
        left.addLayout(colors)
        color_row = QHBoxLayout()
        self.color_value = QLineEdit(PALETTE[0])
        self.color_value.setMaximumWidth(105)
        self.color_value.setToolTip('HEX-цвет: #RRGGBB или #AARRGGBB')
        self.color_value.editingFinished.connect(self.read_color)
        custom = QPushButton('Свой цвет')
        custom.clicked.connect(self.choose_color)
        color_row.addWidget(self.color_value)
        color_row.addWidget(custom)
        color_row.addWidget(QLabel('Кисть'))
        brush = QSpinBox()
        brush.setRange(1, 8)
        brush.setSuffix(' px')
        brush.valueChanged.connect(lambda value: setattr(self.canvas, 'brush_size', value))
        color_row.addWidget(brush)
        color_row.addStretch()
        left.addLayout(color_row)
        area = QScrollArea()
        area.setWidget(self.canvas)
        area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        area.setMinimumSize(380, 300)
        left.addWidget(area, 1)
        options = QHBoxLayout()
        for label, attr in [('Сетка', 'grid'), ('Предыдущий кадр', 'onion')]:
            check = QCheckBox(label)
            check.setChecked(True)
            check.toggled.connect(lambda checked, key=attr: self.canvas_option(key, checked))
            options.addWidget(check)
        self.zoom = QSpinBox()
        self.zoom.setRange(1, 24)
        self.zoom.setSuffix('×')
        self.zoom.setValue(self.canvas.zoom)
        self.zoom.valueChanged.connect(self.set_zoom)
        options.addWidget(self.zoom)
        left.addLayout(options)
        body.addLayout(left, 1)
        right = QVBoxLayout()
        caption = QLabel('Предпросмотр')
        caption.setObjectName('section')
        right.addWidget(caption)
        self.preview = PixelPreview()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(128, 128)
        right.addWidget(self.preview)
        self.play = QPushButton('Пауза')
        self.play.clicked.connect(self.toggle_play)
        right.addWidget(self.play)
        fps_row = QHBoxLayout()
        fps_row.addWidget(QLabel('FPS'))
        self.fps = QSpinBox()
        self.fps.setRange(1, 60)
        self.fps.setValue(self.document.fps)
        self.fps.valueChanged.connect(self.change_fps)
        self.fps.setToolTip('Изменение FPS заменяет исходные задержки GIF равномерной скоростью.')
        fps_row.addWidget(self.fps)
        right.addLayout(fps_row)
        self.pingpong = QCheckBox('Туда и обратно')
        self.pingpong.setToolTip('После последнего кадра вернуться к первому в обратном порядке, без копирования кадров.')
        self.pingpong.setChecked(self.document.pingpong)
        self.pingpong.toggled.connect(self.change_pingpong)
        right.addWidget(self.pingpong)
        self.transform_all = QCheckBox('Все кадры слоя')
        right.addWidget(self.transform_all)
        right.addSpacing(10)
        flips = QHBoxLayout()
        for action, label in [('flip_x', 'Отразить ↔'), ('flip_y', 'Отразить ↕')]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked, key=action: self.mutate(lambda: self.document.transform(key, self.transform_all.isChecked())))
            flips.addWidget(button)
        right.addLayout(flips)
        clear = QPushButton('Очистить слой / кадр')
        clear.clicked.connect(lambda: self.mutate(lambda: self.document.transform('clear', self.transform_all.isChecked())))
        right.addWidget(clear)
        shifts = QHBoxLayout()
        for action, label in [('left', '←'), ('up', '↑'), ('down', '↓'), ('right', '→')]:
            button = QPushButton(label)
            button.setStyleSheet('padding: 6px;')
            button.clicked.connect(lambda checked, key=action: self.mutate(lambda: self.document.transform(key, self.transform_all.isChecked())))
            shifts.addWidget(button)
        right.addLayout(shifts)
        right.addStretch()
        body.addLayout(right)
        root.addLayout(body, 1)
        self.build_layers(root)
        self.timeline = QListWidget()
        self.timeline.setViewMode(QListView.ViewMode.IconMode)
        self.timeline.setFlow(QListView.Flow.LeftToRight)
        self.timeline.setWrapping(False)
        self.timeline.setMovement(QListView.Movement.Static)
        self.timeline.setIconSize(QSize(48, 48))
        self.timeline.setFixedHeight(100)
        self.timeline.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.timeline.currentRowChanged.connect(self.select_frame)
        root.addWidget(self.timeline)
        frames_row = QHBoxLayout()
        for label, method in [('+ Кадр', lambda: self.document.add_frame()),
                              ('Копия кадра', lambda: self.document.add_frame(True)),
                              ('Удалить кадр', self.document.delete_frame)]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked, callback=method: self.mutate(callback))
            frames_row.addWidget(button)
        self.frame_menu = QMenu(self)
        self.frame_actions = {}
        for key, label, callback in [
                ('left', 'Переставить кадр влево · Ctrl+←', lambda: self.document.move_frame(-1)),
                ('right', 'Переставить кадр вправо · Ctrl+→', lambda: self.document.move_frame(1)),
                ('first', 'Переставить в начало · Ctrl+Home', lambda: self.document.move_frame(-self.document.index)),
                ('last', 'Переставить в конец · Ctrl+End', lambda: self.document.move_frame(self.document.count - 1 - self.document.index)),
                ('copy_end', 'Копировать в конец · Ctrl+Shift+D', lambda: self.document.add_frame(True, at_end=True))]:
            self.frame_actions[key] = self.frame_menu.addAction(label, lambda checked=False, fn=callback: self.mutate(fn))
        frame_more = QPushButton('Действия кадра')
        frame_more.setMenu(self.frame_menu)
        frames_row.addWidget(frame_more)
        self.timeline.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.timeline.customContextMenuRequested.connect(self.show_frame_menu)
        root.addLayout(frames_row)
        nav = QHBoxLayout()
        self.navigation = {}
        for key, label, tip, callback in [('first', 'Первый', 'Первый кадр · Home', lambda: self.go_frame(0)),
                                         ('previous', '←', 'Предыдущий кадр · ←', lambda: self.go_frame(self.document.index - 1))]:
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(callback)
            self.navigation[key] = button
            nav.addWidget(button)
        self.frame_number = QSpinBox()
        self.frame_number.setPrefix('Кадр ')
        self.frame_number.valueChanged.connect(lambda value: self.go_frame(value - 1))
        nav.addWidget(self.frame_number)
        for key, label, tip, callback in [('next', '→', 'Следующий кадр · →', lambda: self.go_frame(self.document.index + 1)),
                                         ('last', 'Последний', 'Последний кадр · End', lambda: self.go_frame(self.document.count - 1))]:
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(callback)
            self.navigation[key] = button
            nav.addWidget(button)
        root.addLayout(nav)
        history = QHBoxLayout()
        self.undo_button = QPushButton('Отменить')
        self.redo_button = QPushButton('Повторить')
        self.undo_button.clicked.connect(lambda: self.mutate(self.document.undo))
        self.redo_button.clicked.connect(lambda: self.mutate(self.document.redo))
        history.addWidget(self.undo_button)
        history.addWidget(self.redo_button)
        help_button = QPushButton('Клавиши и слои')
        help_button.clicked.connect(self.show_help)
        history.addWidget(help_button)
        history.addStretch()
        self.size_choice = QComboBox()
        for size in (16, 24, 32, 48, 64, 96, 128, 256, 512):
            self.size_choice.addItem(f'{size}×{size}', size)
        self.size_choice.setCurrentIndex(2)
        history.addWidget(self.size_choice)
        new = QPushButton('Новый холст')
        new.setToolTip('Создать пустую анимацию выбранного размера. Действие можно отменить.')
        new.clicked.connect(lambda: self.mutate(lambda: self.document.new_canvas(self.size_choice.currentData())))
        history.addWidget(new)
        root.addLayout(history)
        self.info = QLabel()
        self.info.setObjectName('muted')
        self.info.setWordWrap(True)
        root.addWidget(self.info)
        output = QHBoxLayout()
        output.addWidget(QLabel('Размер для чибика'))
        self.output_size = QComboBox()
        for label, value in [('Авто', 0), ('Исходный', -1), ('512 пкс', 512), ('256 пкс', 256), ('128 пкс', 128)]:
            self.output_size.addItem(label, value)
        self.output_size.setToolTip('Сжатие только при сохранении в мастерскую. Проект слоёв остаётся в исходном разрешении. Авто укладывает анимацию в 32 МиБ.')
        output.addWidget(self.output_size, 1)
        root.addLayout(output)
        self.output_info = QLabel()
        self.output_info.setObjectName('muted')
        self.output_info.setWordWrap(True)
        root.addWidget(self.output_info)
        self.output_size.currentIndexChanged.connect(self.update_output_info)
        buttons = QDialogButtonBox()
        done = buttons.addButton('Готово — в мастерскую', QDialogButtonBox.ButtonRole.AcceptRole)
        done.setObjectName('primary')
        buttons.addButton('Отмена', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.finish)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.install_shortcuts()
        self.canvas.changed.connect(lambda: self.refresh(all_frames=self.document.layer['shared']))
        self.canvas.picked.connect(self.set_color)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.render_preview)
        self.refresh()
        self.resize(1040, 940)

    def show_help(self):
        QMessageBox.information(self, 'Клавиши и слои',
            'СЛОИ\n+ Слой — разные рисунки на кадрах. + Общий — одна деталь на всей анимации.\n'
            'Выше — поверх других слоёв. Видимость и блокировка меняются отдельно.\n'
            'При включении общего режима сохраняется рисунок выбранного кадра. Ctrl+Z отменяет.\n\n'
            'РИСОВАНИЕ\nB — кисть; E — ластик; F — заливка; I — пипетка.\n'
            'Ctrl+клик — временная пипетка; ПКМ — стереть. + / − — масштаб.\n'
            'Ctrl+Z — отмена; Ctrl+Y или Ctrl+Shift+Z — повтор. Ctrl+L — общий слой.\n\n'
            'КАДРЫ\n← / → — предыдущий / следующий; Home / End — первый / последний.\n'
            'Ctrl+← / → — переместить кадр; Ctrl+Home / End — перенести в начало / конец.\n'
            'Ctrl+D — копия; Ctrl+Shift+D — копия в конец; Insert — пустой кадр.\n'
            'Shift+Delete — удалить кадр; Space — пауза / играть.\n'
            'При вводе текста и чисел сочетания редактора не срабатывают.\n\n'
            'ТУДА И ОБРАТНО\nВключите флажок рядом с FPS: 1 → 2 → 3 → 2 → 1. Дублировать кадры не нужно.')

    def build_layers(self, root):
        row = QHBoxLayout()
        row.addWidget(QLabel('Слой'))
        self.layer_list = QComboBox()
        self.layer_list.setMinimumWidth(170)
        self.layer_list.currentIndexChanged.connect(self.select_layer)
        row.addWidget(self.layer_list, 1)
        self.layer_name = QLineEdit()
        self.layer_name.setPlaceholderText('Имя слоя')
        self.layer_name.setMaximumWidth(160)
        self.layer_name.editingFinished.connect(lambda: self.mutate(lambda: self.document.set_layer('name', self.layer_name.text().strip() or 'Слой')))
        row.addWidget(self.layer_name)
        add = QPushButton('+ Слой')
        add.clicked.connect(lambda: self.mutate(lambda: self.document.add_layer()))
        row.addWidget(add)
        self.layer_menu = QMenu(self)
        self.layer_actions = {}
        for key, label, callback in [('shared', 'Добавить общий слой · Ctrl+L', lambda: self.document.add_layer(True)),
                                      ('down', 'Переместить ниже', lambda: self.document.move_layer(-1)),
                                      ('up', 'Переместить выше', lambda: self.document.move_layer(1)),
                                      ('delete', 'Удалить выбранный слой', self.document.delete_layer)]:
            self.layer_actions[key] = self.layer_menu.addAction(label, lambda checked=False, fn=callback: self.mutate(fn))
        more = QPushButton('Действия слоя')
        more.setMenu(self.layer_menu)
        row.addWidget(more)
        root.addLayout(row)
        row = QHBoxLayout()
        self.layer_checks = {}
        for key, label in [('shared', 'Общий рисунок на всех кадрах'), ('visible', 'Видимый'), ('locked', 'Заблокирован')]:
            check = QCheckBox(label)
            check.toggled.connect(lambda value, k=key: self.mutate(lambda: self.document.set_layer(k, value)))
            self.layer_checks[key] = check
            row.addWidget(check)
        hint = QLabel('Снизу вверх: основа → детали. «Общий» — нарисуй один раз.')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        row.addStretch()
        root.addLayout(row)
        root.addWidget(hint)

    def refresh_layers(self):
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        for index, layer in enumerate(self.document.layers):
            self.layer_list.addItem(f"{index + 1}. {layer['name']}" + (' / общий' if layer['shared'] else ''))
        self.layer_list.setCurrentIndex(self.document.layer_index)
        self.layer_list.blockSignals(False)
        if hasattr(self, 'layer_actions'):
            self.layer_actions['down'].setEnabled(self.document.layer_index > 0)
            self.layer_actions['up'].setEnabled(self.document.layer_index < len(self.document.layers) - 1)
            self.layer_actions['delete'].setEnabled(len(self.document.layers) > 1)
        self.layer_name.setText(self.document.layer['name'])
        for key, check in self.layer_checks.items():
            check.blockSignals(True)
            check.setChecked(self.document.layer[key])
            check.blockSignals(False)

    def select_layer(self, index):
        if 0 <= index < len(self.document.layers):
            self.canvas.previous = None
            self.document.layer_index = index
            self.refresh_layers()
            self.canvas.update()

    def go_frame(self, index):
        self.timeline.setCurrentRow(max(0, min(self.document.count - 1, index)))

    def install_shortcuts(self):
        actions = {
            'Ctrl+Z': lambda: self.mutate(self.document.undo),
            'Ctrl+Shift+Z': lambda: self.mutate(self.document.redo),
            'Ctrl+Y': lambda: self.mutate(self.document.redo),
            'Ctrl+D': lambda: self.mutate(lambda: self.document.add_frame(True)),
            'Ctrl+Shift+D': lambda: self.mutate(lambda: self.document.add_frame(True, at_end=True)),
            'Insert': lambda: self.mutate(self.document.add_frame),
            'Shift+Delete': lambda: self.mutate(self.document.delete_frame),
            'Left': lambda: self.go_frame(self.document.index - 1),
            'Right': lambda: self.go_frame(self.document.index + 1),
            'Home': lambda: self.go_frame(0), 'End': lambda: self.go_frame(self.document.count - 1),
            'Ctrl+Left': lambda: self.mutate(lambda: self.document.move_frame(-1)),
            'Ctrl+Right': lambda: self.mutate(lambda: self.document.move_frame(1)),
            'Ctrl+Home': lambda: self.mutate(lambda: self.document.move_frame(-self.document.index)),
            'Ctrl+End': lambda: self.mutate(lambda: self.document.move_frame(self.document.count - 1 - self.document.index)),
            'Ctrl+L': lambda: self.mutate(lambda: self.document.add_layer(True)),
            'Space': self.toggle_play,
            '+': lambda: self.zoom.setValue(self.zoom.value() + 1),
            '-': lambda: self.zoom.setValue(self.zoom.value() - 1),
        }
        for key, tool in [('B', 'brush'), ('E', 'eraser'), ('F', 'fill'), ('I', 'pick')]:
            actions[key] = lambda name=tool: self.tool_buttons[name].click()
        for key, callback in actions.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda fn=callback: self.shortcut_action(fn))

    def shortcut_action(self, callback):
        if isinstance(QApplication.focusWidget(), (QLineEdit, QSpinBox)):
            return
        callback()

    def change_pingpong(self, value):
        self.document.pingpong = value
        self.started = time.monotonic()
        self.render_preview()

    def mutate(self, callback):
        self.canvas.previous = None
        try:
            callback()
        except ValueError as error:
            QMessageBox.warning(self, 'Размер анимации', str(error))
            return
        self.document.prune_history()
        self.canvas.resize_canvas()
        self.refresh()

    def refresh(self, all_frames=True):
        self.refresh_layers()
        self.timeline.blockSignals(True)
        if self.timeline.count() != self.document.count:
            self.timeline.clear()
            self.thumbnail_keys.clear()
            for index in range(self.document.count):
                self.timeline.addItem(QListWidgetItem(str(index + 1)))
            indices = range(self.document.count)
        else:
            # Undo/reorder may affect more than the selected image.
            indices = range(self.document.count) if all_frames else [self.document.index]
        for index in indices:
            image = self.document.composite(index)
            if self.thumbnail_keys.get(index) != image.cacheKey():
                thumbnail = image.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
                self.timeline.item(index).setIcon(QIcon(QPixmap.fromImage(thumbnail)))
                self.thumbnail_keys[index] = image.cacheKey()
        self.timeline.setCurrentRow(self.document.index)
        self.timeline.blockSignals(False)
        self.timeline.scrollToItem(self.timeline.currentItem())
        self.frame_number.blockSignals(True)
        self.frame_number.setRange(1, self.document.count)
        self.frame_number.setValue(self.document.index + 1)
        self.frame_number.blockSignals(False)
        self.undo_button.setEnabled(bool(self.document.undo_stack))
        self.redo_button.setEnabled(bool(self.document.redo_stack))
        self.sync_frame_controls()
        self.update_frame_info()
        self.canvas.update()
        self.render_preview()

    def select_frame(self, index):
        if 0 <= index < self.document.count:
            self.canvas.previous = None
            self.document.index = index
            self.sync_frame_controls()
            self.frame_number.blockSignals(True)
            self.frame_number.setValue(index + 1)
            self.frame_number.blockSignals(False)
            self.timeline.scrollToItem(self.timeline.currentItem())
            self.canvas.update()
            self.render_preview()
            self.update_frame_info()

    def update_frame_info(self):
        image = self.document.image
        timing = ' · Ритм GIF' if self.document.delays else ''
        self.info.setText(f'{image.width()} × {image.height()} пкс · Кадр {self.document.index + 1} из {self.document.count} · {self.document.memory_bytes / 1048576:.1f} МиБ{timing}')
        self.update_output_info()

    def update_output_info(self, *_):
        try:
            size = self.document.export_dimensions(self.output_size.currentData())
            self.output_info.setText(f'Для чибика: {size.width()}×{size.height()} пкс. Проект слоёв сохранит полный размер.')
        except ValueError as error:
            self.output_info.setText(str(error))

    def finish(self):
        try:
            self.document.export_dimensions(self.output_size.currentData())
        except ValueError as error:
            QMessageBox.warning(self, 'Размер для чибика', str(error))
            return
        self.accept()

    def sync_frame_controls(self):
        previous = self.document.index > 0
        following = self.document.index < self.document.count - 1
        for key in ('first', 'previous'):
            self.navigation[key].setEnabled(previous)
        for key in ('next', 'last'):
            self.navigation[key].setEnabled(following)
        for key in ('left', 'first'):
            self.frame_actions[key].setEnabled(previous)
        for key in ('right', 'last'):
            self.frame_actions[key].setEnabled(following)
        self.frame_actions['copy_end'].setEnabled(self.document.count < 512)

    def show_frame_menu(self, position):
        item = self.timeline.itemAt(position)
        if item is not None:
            self.timeline.setCurrentItem(item)
        self.sync_frame_controls()
        self.frame_menu.popup(self.timeline.viewport().mapToGlobal(position))

    def render_preview(self):
        elapsed = max(0, time.monotonic() - self.started)
        if self.playing and self.document.delays:
            key = (tuple(self.document.delays), self.document.pingpong)
            if key != self._timing_key:
                self._timing_key = key
                self._timing_order = tuple(range(self.document.count))
                if self.document.pingpong:
                    self._timing_order += self._timing_order[-2:0:-1]
                self._timing_ends = tuple(accumulate(self.document.delays[i] for i in self._timing_order))
            position = bisect_right(self._timing_ends, elapsed * 1000 % self._timing_ends[-1])
            index = self._timing_order[min(position, len(self._timing_order) - 1)]
        else:
            index = frame_index(int(elapsed * self.document.fps), self.document.count, self.document.pingpong) if self.playing else self.document.index
        image = self.document.composite(index)
        key = image.cacheKey()
        if key == self.preview_key:
            return
        self.preview_key = key
        self.preview.setPixmap(QPixmap.fromImage(image).scaled(120, 120, Qt.AspectRatioMode.KeepAspectRatio,
                                                              Qt.TransformationMode.FastTransformation))

    def toggle_play(self):
        self.playing = not self.playing
        self.play.setText('Пауза' if self.playing else 'Играть')
        self.started = time.monotonic()
        self.sync_preview_timer()
        self.render_preview()

    def change_fps(self, fps):
        self.document.fps = fps
        self.document.delays = None
        self.started = time.monotonic()
        self.sync_preview_timer()
        self.update_frame_info()

    def sync_preview_timer(self):
        if self.isVisible() and self.playing:
            interval = min(self.document.delays) if self.document.delays else round(1000 / self.document.fps)
            self.timer.start(max(16, interval))
        else:
            self.timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self.sync_preview_timer()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.sync_preview_timer()

    def set_color(self, color):
        self.canvas.color = QColor(color)
        self.color_value.setText(color.name(QColor.NameFormat.HexArgb) if color.alpha() < 255 else color.name())

    def read_color(self):
        color = QColor(self.color_value.text())
        if color.isValid():
            self.set_color(color)
        else:
            self.set_color(self.canvas.color)

    def choose_color(self):
        color = QColorDialog.getColor(self.canvas.color, self, 'Цвет пикселя', QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if color.isValid():
            self.set_color(color)

    def canvas_option(self, key, value):
        setattr(self.canvas, key, value)
        self.canvas.update()

    def set_zoom(self, value):
        self.canvas.zoom = value
        self.canvas.resize_canvas()

    def done(self, result):
        self.canvas.previous = None
        self.timer.stop()
        super().done(result)
