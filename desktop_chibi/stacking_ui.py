"""A visual front-to-back companion order, independent of selection."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout, QWidget)


class StackingOrder(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = None
        self.updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel('Порядок чибиков · сверху — передний план'))
        self.list = QListWidget()
        self.list.setAccessibleName('Порядок наложения чибиков')
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setMinimumHeight(110)
        self.list.setMaximumHeight(190)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        self.up = QPushButton('Выше ↑')
        self.down = QPushButton('Ниже ↓')
        self.up.clicked.connect(lambda: self.move(-1))
        self.down.clicked.connect(lambda: self.move(1))
        row.addWidget(self.up)
        row.addWidget(self.down)
        layout.addLayout(row)
        hint = QLabel('Перетащите имя или используйте кнопки. Верхний чибик перекрывает нижних. '
                      'Порядок действует внутри одного слоя окон; игровой оверлей выше обычных слоёв.')
        hint.setWordWrap(True)
        hint.setObjectName('muted')
        layout.addWidget(hint)
        self.list.currentRowChanged.connect(self.update_buttons)
        self.list.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self.commit))
        self.update_buttons()

    def bind(self, manager):
        self.manager = manager
        manager.stacking_changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        if not self.manager:
            return
        selected = self.list.currentItem()
        identity = selected.data(Qt.ItemDataRole.UserRole) if selected else self.manager.selected.identity
        self.updating = True
        self.list.clear()
        for item_id in self.manager.stacking_order:
            companion = self.manager.find(item_id)
            if companion is None:
                continue
            values = companion.controller.values
            layer = ('Оверлей' if values['game_overlay'] else
                     ('Поверх окон', 'Обычный слой', 'Под окнами')[values['layer']])
            item = QListWidgetItem(f'{companion.name}  ·  {layer}')
            item.setData(Qt.ItemDataRole.UserRole, item_id)
            item.setToolTip(companion.name + '\n' + layer)
            self.list.addItem(item)
            if item_id == identity:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        row_height = max(28, self.list.sizeHintForRow(0))
        self.list.setFixedHeight(min(190, max(110, row_height * self.list.count() + 12)))
        self.updating = False
        self.update_buttons()

    def update_buttons(self, *_):
        row = self.list.currentRow()
        self.up.setEnabled(row > 0)
        self.down.setEnabled(0 <= row < self.list.count() - 1)

    def move(self, step):
        row = self.list.currentRow()
        target = row + step
        if not 0 <= row < self.list.count() or not 0 <= target < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentItem(item)
        self.commit()

    def commit(self, *_):
        if self.manager and not self.manager.stacking_stopped and not self.updating:
            self.manager.set_stacking_order([
                self.list.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.list.count())])
        self.update_buttons()
