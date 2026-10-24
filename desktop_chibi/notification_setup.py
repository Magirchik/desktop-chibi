"""Application suggestions, explicit temporary discovery, and a pipeline test."""
import configparser
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)
from .event_adapters import NotificationAdapter
from .pixel_ui import apply_theme


def installed_applications():
    roots = [Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'applications']
    roots += [Path(directory) / 'applications' for directory in os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':')]
    roots += [Path('/var/lib/flatpak/exports/share/applications'), Path.home() / '.local/share/flatpak/exports/share/applications', Path('/var/lib/snapd/desktop/applications')]
    seen, result = set(), []
    for root in roots:
        for path in sorted(root.glob('*.desktop')):
            if path.name in seen:
                continue
            seen.add(path.name)
            try:
                parser = configparser.ConfigParser(interpolation=None, strict=False)
                parser.read(path, encoding='utf-8')
                entry = parser['Desktop Entry']
                if entry.get('Type') != 'Application' or entry.get('Hidden', '').lower() == 'true':
                    continue
                name = entry.get('Name', '').strip()
                command = shlex.split(entry.get('Exec', ''))
                executable = command[0] if command else ''
                if name:
                    result.append({'name': name, 'executable': executable, 'desktop': str(path)})
            except (OSError, ValueError, configparser.Error, KeyError):
                continue
    return sorted(result, key=lambda record: record['name'].casefold())


def suggest_app_name(path, entries):
    selected = Path(path)
    match = next((entry for entry in entries if entry['desktop'] == str(selected)), None)
    if match:
        return match['name']
    if selected.suffix == '.desktop':
        try:
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.read(selected, encoding='utf-8')
            name = parser['Desktop Entry'].get('Name', '').strip()
            if name:
                return name
        except (OSError, KeyError, ValueError, configparser.Error):
            pass
    resolved = selected.resolve()
    for entry in entries:
        executable = entry['executable']
        candidate = shutil.which(executable) if executable and not executable.startswith('/') else executable
        if candidate and Path(candidate).resolve() == resolved:
            return entry['name']
    return selected.stem


def send_test_notification(app):
    if not shutil.which('notify-send'):
        raise ValueError('Не найден notify-send. Установите пакет libnotify-bin или проверьте предпросмотр анимации в правиле.')
    try:
        result = subprocess.run(['notify-send', '--app-name=' + app, '--expire-time=2000',
                                 'Desktop Chibi: проверка', 'Проверка системного источника и анимации уведомления'],
                                capture_output=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('Не удалось отправить тестовое системное уведомление.') from error
    if result.returncode:
        raise ValueError('Служба уведомлений не приняла тест. Проверьте системные уведомления и статус источника.')


class NotificationPicker(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Выбрать приложение для уведомлений')
        apply_theme(self)
        self.resize(670, 700)
        self.entries = installed_applications()
        self.probe = None
        layout = QVBoxLayout(self)
        bar = QLabel(' +  УВЕДОМЛЕНИЯ / ВЫБОР ПРИЛОЖЕНИЯ')
        bar.setObjectName('bar')
        layout.addWidget(bar)
        note = QLabel('Выберите установленное приложение или файл программы. Это подставит предполагаемое имя. '
                      'Для точного совпадения используйте «Поймать уведомление» ниже: путь к программе в самом уведомлении не передаётся.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Поиск: Telegram, AyuGram, Vesktop…')
        layout.addWidget(self.search)
        self.apps = QListWidget()
        for entry in self.entries:
            item = QListWidgetItem(entry['name'])
            item.setToolTip(entry['executable'] + '\n' + entry['desktop'])
            self.apps.addItem(item)
        layout.addWidget(self.apps, 1)
        self.apps.currentRowChanged.connect(self.select_app)
        self.search.textChanged.connect(self.filter_apps)
        file_button = QPushButton('Выбрать файл программы или .desktop…')
        file_button.clicked.connect(self.choose_file)
        layout.addWidget(file_button)
        self.disclosure = QCheckBox('Разрешить на 30 секунд определение имён отправителей уведомлений')
        layout.addWidget(self.disclosure)
        privacy = QLabel('Временно увидит имена всех приложений, отправивших системное уведомление. '
                         'Заголовки и тексты отбрасываются. Найденные имена не сохраняются без выбора и применения настройки.')
        privacy.setWordWrap(True)
        privacy.setObjectName('muted')
        layout.addWidget(privacy)
        self.listen = QPushButton('Поймать уведомление (30 секунд)')
        self.listen.clicked.connect(self.start_probe)
        layout.addWidget(self.listen)
        self.status = QLabel('Запустите поиск, затем получите системное уведомление от нужного приложения.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.found = QListWidget()
        self.found.setFixedHeight(90)
        self.found.currentTextChanged.connect(self.select_found)
        layout.addWidget(self.found)
        self.name = QLineEdit()
        self.name.setPlaceholderText('Точное имя отправителя — app_name')
        layout.addWidget(self.name)
        self.name_hint = QLabel('Список и путь дают подсказку; фактическое имя подтверждается пойманным уведомлением.')
        self.name_hint.setWordWrap(True)
        layout.addWidget(self.name_hint)
        buttons = QDialogButtonBox()
        buttons.addButton('Выбрать имя', QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton('Отмена', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.stop_probe)
        self.disclosure.toggled.connect(lambda enabled: self.stop_probe() if not enabled else None)

    def select_app(self, index):
        if 0 <= index < len(self.entries):
            self.name.setText(self.entries[index]['name'])
            self.name_hint.setText('Предполагаемое имя из ярлыка. Если не сработает, определите фактическое по уведомлению.')

    def filter_apps(self, text):
        for index, entry in enumerate(self.entries):
            self.apps.item(index).setHidden(text.casefold() not in (entry['name'] + ' ' + entry['executable']).casefold())

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Программа или ярлык приложения')
        if path:
            self.name.setText(suggest_app_name(path, self.entries))
            self.name_hint.setText('Подставлено предполагаемое имя по пути. Программа не запускается. Для подтверждения поймайте уведомление.')

    def start_probe(self):
        if not self.disclosure.isChecked():
            self.status.setText('Сначала разрешите временное определение имён. Без разрешения наблюдение не запускается.')
            return
        self.stop_probe(update_status=False)
        self.probe = NotificationAdapter(self, {'id': 'discovery', 'apps': []}, discovery=True)
        self.probe.found.connect(self.add_found)
        self.probe.start()
        self.listen.setEnabled(False)
        self.timeout.start(30000)
        self.status.setText('Сейчас получите уведомление от нужного приложения. Наблюдение автоматически закончится через 30 секунд.')

    def add_found(self, name):
        if not any(self.found.item(i).text() == name for i in range(self.found.count())):
            self.found.addItem(name)
        self.status.setText('Получено имя отправителя. Нажмите на него в списке ниже.')

    def select_found(self, name):
        if name:
            self.name.setText(name)
            self.name_hint.setText('Это фактическое app_name из системного уведомления.')

    def set_status(self, identity, text):
        self.status.setText(text)

    def stop_probe(self, update_status=True):
        self.timeout.stop()
        if self.probe:
            self.probe.stop()
            self.probe.deleteLater()
            self.probe = None
            if update_status:
                self.status.setText('Определение имён остановлено. Выберите найденное имя или повторите поиск.')
        self.listen.setEnabled(True)

    def apply(self):
        name = self.name.text().strip()
        if not name or len(name) > 100 or any(c in name for c in "'\\,\r\n"):
            self.status.setText('Укажите непустое имя до 100 символов без кавычек, запятых и обратного слеша.')
            return
        self.name.setText(name)
        self.accept()

    def done(self, result):
        self.stop_probe()
        super().done(result)
