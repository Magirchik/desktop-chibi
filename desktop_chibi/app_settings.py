"""Application-wide appearance settings, independent of a companion preset."""
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout
from .appearance import THEMES, appearance
from .pixel_ui import apply_theme, window_bar, panel


class AppSettings(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Настройки приложения')
        apply_theme(self)
        self.resize(570, 340)
        layout = QVBoxLayout(self)
        layout.addWidget(window_bar('Настройки приложения'))
        section, body = panel('Оформление')
        body.addWidget(QLabel('Тема приложения'))
        self.theme = QComboBox()
        for key, label in THEMES.items():
            self.theme.addItem(label, key)
        self.theme.setCurrentIndex(self.theme.findData(appearance().theme))
        self.theme.currentIndexChanged.connect(lambda *_: appearance().set_theme(self.theme.currentData()))
        body.addWidget(self.theme)
        note = QLabel('Тема меняется сразу во всех окнах и сохраняется после перезапуска. Облик чибиков и пресеты остаются своими.')
        note.setWordWrap(True)
        note.setObjectName('muted')
        body.addWidget(note)
        layout.addWidget(section)
        layout.addStretch()
        buttons = QDialogButtonBox()
        buttons.addButton('Готово', QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
