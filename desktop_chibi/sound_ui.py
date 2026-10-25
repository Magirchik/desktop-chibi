"""Compact sound editor shared by the wizard and advanced rules."""
from pathlib import Path
import sys
from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget


class SoundOptions(QWidget):
    def __init__(self, player, parent=None):
        super().__init__(parent)
        self.player = player
        self.path = ''
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.enabled = QCheckBox('Проигрывать свой звук')
        layout.addWidget(self.enabled)
        self.options = QWidget()
        inner = QVBoxLayout(self.options)
        inner.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.file = QPushButton('Выбрать звук…')
        self.file.clicked.connect(self.choose)
        row.addWidget(self.file, 1)
        self.volume = QSpinBox()
        self.volume.setRange(0, 100)
        self.volume.setValue(35)
        self.volume.setSuffix(' %')
        self.volume.setToolTip('Громкость звука чибика')
        row.addWidget(self.volume)
        test = QPushButton('Послушать')
        test.clicked.connect(self.preview)
        row.addWidget(test)
        inner.addLayout(row)
        self.when_hidden = QCheckBox('Звук, даже когда чибик скрыт')
        inner.addWidget(self.when_hidden)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        self.feedback.setObjectName('muted')
        inner.addWidget(self.feedback)
        layout.addWidget(self.options)
        self.enabled.toggled.connect(self.options.setVisible)
        player.status.connect(self.feedback.setText)
        self.options.hide()

    def choose(self):
        formats = 'Звук WAV (*.wav)' if sys.platform == 'win32' else 'Звук (*.wav *.ogg *.flac);;Все файлы (*)'
        path, _ = QFileDialog.getOpenFileName(self, 'Короткий звук чибика', '', formats)
        if path:
            self.path = path
            self.file.setText(Path(path).name)
            self.file.setToolTip(path)

    def value(self):
        return dict(enabled=self.enabled.isChecked(), file=self.path,
                    volume=self.volume.value(), when_hidden=self.when_hidden.isChecked())

    def setValue(self, sound):
        self.path = sound.get('file', '')
        self.file.setText(Path(self.path).name if self.path else 'Выбрать звук…')
        self.file.setToolTip(self.path)
        self.volume.setValue(sound.get('volume', 35))
        self.when_hidden.setChecked(sound.get('when_hidden', False))
        self.enabled.setChecked(sound.get('enabled', False))
        self.options.setVisible(self.enabled.isChecked())
        self.feedback.clear()

    def preview(self):
        try:
            self.player.play(self.value())
        except ValueError as error:
            self.feedback.setText(str(error))
