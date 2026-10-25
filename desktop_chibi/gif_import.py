"""Prepare a large external GIF for bounded frame editing."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QSpinBox, QVBoxLayout
from .pixel_document import EDITOR_BYTES
from .pixel_ui import apply_theme, window_bar


class GifImport(QDialog):
    def __init__(self, size, count, spec, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Подготовить GIF к редактированию')
        apply_theme(self)
        self.resize(620, 380)
        self.source_size, self.count = size, max(1, count)
        layout = QVBoxLayout(self)
        layout.addWidget(window_bar('Подготовить GIF'))
        note = QLabel(f'Исходник: {size.width()}×{size.height()} пкс · {self.count} кадров. Выберите размер и фрагмент для редактора. Исходный файл останется целым.')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.size = QComboBox()
        for label, value in [('Исходный', 0), ('512 пкс', 512), ('256 пкс', 256), ('128 пкс', 128)]:
            self.size.addItem(label, value)
        self.first, self.last = QSpinBox(), QSpinBox()
        for spin in (self.first, self.last):
            spin.setRange(1, self.count)
        self.first.setValue(min(self.count, spec.get('first_frame', 0) + 1))
        self.last.setValue(min(self.count, spec.get('last_frame') or self.count))
        selected_count = self.last.value() - self.first.value() + 1
        if selected_count > 512:
            self.last.setValue(min(self.count, self.first.value() + 511))
        for candidate in (spec.get('max_size') or 0, 512, 256, 128):
            if self.cost(candidate) <= EDITOR_BYTES and max(self.dimensions(candidate).width(), self.dimensions(candidate).height()) <= 512:
                self.size.setCurrentIndex(max(0, self.size.findData(candidate)))
                break
        form.addRow('Размер в редакторе', self.size)
        form.addRow('Первый кадр', self.first)
        form.addRow('Последний кадр', self.last)
        layout.addLayout(form)
        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        buttons = QDialogButtonBox()
        self.import_button = buttons.addButton('Открыть кадры', QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton('Отмена', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.size.currentIndexChanged.connect(self.refresh)
        self.first.valueChanged.connect(self.refresh)
        self.last.valueChanged.connect(self.refresh)
        self.refresh()

    def dimensions(self, value):
        return self.source_size.scaled(value, value, Qt.AspectRatioMode.KeepAspectRatio) if value and max(self.source_size.width(), self.source_size.height()) > value else self.source_size

    def cost(self, value):
        size = self.dimensions(value)
        return size.width() * size.height() * 4 * max(0, self.last.value() - self.first.value() + 1)

    def refresh(self, *_):
        count = self.last.value() - self.first.value() + 1
        size = self.dimensions(self.size.currentData())
        cost = self.cost(self.size.currentData())
        valid = 1 <= count <= 512 and max(size.width(), size.height()) <= 512 and cost <= EDITOR_BYTES
        self.import_button.setEnabled(valid)
        self.info.setText(f'{size.width()}×{size.height()} · {count} кадров · {cost / 1048576:.1f} МиБ изображений.\n'
                          + ('Задержки GIF сохранятся. Размер для чибика можно уменьшить после редактирования.' if valid else 'Нужно до 512 кадров, до 512×512 и до 64 МиБ. Уменьшите размер или сократите фрагмент.'))

    def values(self):
        return dict(first_frame=self.first.value() - 1, last_frame=self.last.value(), max_size=self.size.currentData())
