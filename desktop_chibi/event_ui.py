"""Explicit permissions and event-to-companion rules."""
import json
import secrets
import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QTabBar, QTabWidget, QVBoxLayout, QWidget,
)
from .animations import STATES
from .discord_rpc import default_discord_socket
from .pixel_ui import apply_theme, window_bar, PixelSurface
from .sound_ui import SoundOptions
from .guides import GuideDialog
from .notification_setup import NotificationPicker, send_test_notification


class EventDialog(QDialog):
    def __init__(self, hub, parent=None):
        super().__init__(parent)
        self.hub = hub
        self.sources = json.loads(json.dumps(hub.sources))
        self.rules = json.loads(json.dumps(hub.rules))
        self.source_index = self.rule_index = -1
        self.loading = False
        self.discord_token = hub.discord_token
        self.setWindowTitle('Чибики · Точные настройки правил и источников')
        apply_theme(self)
        self.resize(800, 820)
        self.setMinimumSize(620, 520)
        outer = QVBoxLayout(self)
        bar = window_bar('События и правила')
        outer.addWidget(bar)
        help_button = QPushButton('Нужна помощь? Пошаговые гайды')
        help_button.clicked.connect(self.open_guide)
        outer.addWidget(help_button)
        tabs = QTabWidget()
        outer.addWidget(tabs, 1)
        sources, sl = self.page()
        tabs.addTab(sources, 'Источники')
        note = QLabel('Разрешения выдаются отдельно каждому источнику. По умолчанию всё выключено. '
                      'Источники запускают реакции и выбранные вами звуки.')
        note.setWordWrap(True)
        sl.addWidget(note)
        self.source_list = QListWidget()
        self.source_list.setFixedHeight(120)
        sl.addWidget(self.source_list)
        add = QPushButton('+ Локальный источник')
        add.clicked.connect(self.add_source)
        sl.addWidget(add)
        self.source_enabled = QCheckBox('Разрешить этот источник')
        sl.addWidget(self.source_enabled)
        form = QFormLayout()
        self.source_fields = {}
        for key, label in [('id', 'ID'), ('name', 'Название'), ('events', 'Разрешённые события'),
                           ('apps', 'Приложения (через запятую)'), ('client_id', 'Discord Client ID'),
                           ('channel_id', 'Discord Channel ID'), ('users', 'Discord User ID (список)'),
                           ('socket', 'Discord IPC-путь'), ('token', 'Ключ / OAuth-токен')]:
            field = QLineEdit()
            if key == 'id':
                field.setReadOnly(True)
            if key == 'token':
                field.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow(label, field)
            self.source_fields[key] = field
        sl.addLayout(form)
        self.source_form = form
        self.timer_options = QWidget()
        timer_form = QFormLayout(self.timer_options)
        self.timer_mode = QComboBox()
        for label, mode in [('Один раз', 'once'), ('Повторять', 'repeat'), ('Работа / перерыв', 'focus')]:
            self.timer_mode.addItem(label, mode)
        self.timer_minutes = QSpinBox()
        self.timer_break = QSpinBox()
        for spin in (self.timer_minutes, self.timer_break):
            spin.setRange(1, 1440)
            spin.setSuffix(' мин.')
        timer_form.addRow('Таймер', self.timer_mode)
        timer_form.addRow('Интервал / работа', self.timer_minutes)
        timer_form.addRow('Перерыв', self.timer_break)
        self.timer_mode.currentIndexChanged.connect(lambda *_: self.timer_break.setEnabled(self.timer_mode.currentData() == 'focus'))
        sl.addWidget(self.timer_options)
        self.pick_notification_app = QPushButton('Выбрать приложение / поймать имя отправителя…')
        self.pick_notification_app.clicked.connect(self.choose_notification_app)
        sl.addWidget(self.pick_notification_app)
        self.test_notification_button = QPushButton('Отправить тестовое системное уведомление')
        self.test_notification_button.clicked.connect(self.test_notification)
        sl.addWidget(self.test_notification_button)
        token_row = QHBoxLayout()
        self.show_token = QCheckBox('Показать ключ')
        self.show_token.toggled.connect(lambda checked: self.source_fields['token'].setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        token_row.addWidget(self.show_token)
        self.rotate = QPushButton('Новый ключ')
        self.rotate.clicked.connect(lambda: self.source_fields['token'].setText(secrets.token_urlsafe(32)))
        token_row.addWidget(self.rotate)
        sl.addLayout(token_row)
        self.source_hint = QLabel()
        self.source_hint.setWordWrap(True)
        self.source_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sl.addWidget(self.source_hint)
        self.source_status = QLabel()
        self.source_status.setWordWrap(True)
        sl.addWidget(self.source_status)
        sl.addStretch()

        rules, rl = self.page()
        tabs.addTab(rules, 'Правила')
        self.rule_scope_tabs = QTabBar()
        self.rule_scope_tabs.addTab('Личные правила')
        self.rule_scope_tabs.addTab('Общие правила')
        rl.addWidget(self.rule_scope_tabs)
        self.rule_owner = QComboBox()
        for item in hub.manager.items:
            self.rule_owner.addItem(item.name, item.identity)
        self.rule_owner.setCurrentIndex(max(0, self.rule_owner.findData(hub.manager.selected.identity)))
        rl.addWidget(self.rule_owner)
        self.rule_scope_hint = QLabel()
        self.rule_scope_hint.setWordWrap(True)
        rl.addWidget(self.rule_scope_hint)
        self.visible_rule_indices = []
        self.rule_list = QListWidget()
        self.rule_list.setFixedHeight(130)
        rl.addWidget(self.rule_list)
        actions = QHBoxLayout()
        add_rule = QPushButton('+ Правило')
        add_rule.clicked.connect(self.add_rule)
        remove = QPushButton('Удалить правило')
        remove.clicked.connect(self.remove_rule)
        actions.addWidget(add_rule)
        actions.addWidget(remove)
        rl.addLayout(actions)
        self.move_scope_button = QPushButton('Сделать общим')
        self.move_scope_button.clicked.connect(self.move_rule_scope)
        rl.addWidget(self.move_scope_button)
        self.rule_enabled = QCheckBox('Правило включено')
        rl.addWidget(self.rule_enabled)
        form = QFormLayout()
        self.rule_source = QComboBox()
        self.rule_event = QComboBox()
        self.rule_target = QComboBox()
        for item in hub.manager.items:
            self.rule_target.addItem(item.name, item.identity)
        self.rule_target.addItem('Все чибики с событиями', '*')
        self.rule_animation = QComboBox()
        self.rule_subject = QLineEdit()
        self.rule_subject.setPlaceholderText('Пусто — все разрешённые; иначе точное имя приложения / User ID')
        self.rule_mode = QComboBox()
        self.rule_mode.addItem('Показать на время', 'pulse')
        self.rule_mode.addItem('Пока активно, с автостопом', 'hold')
        self.rule_duration = QDoubleSpinBox()
        self.rule_duration.setRange(.2, 60)
        self.rule_duration.setSuffix(' s')
        self.rule_duration.setToolTip('Для разовой реакции — минимум времени показа. Анимация всегда успеет пройти один полный цикл. Для режима удержания — таймаут без новых событий.')
        self.rule_cooldown = QDoubleSpinBox()
        self.rule_cooldown.setRange(0, 300)
        self.rule_cooldown.setSuffix(' s')
        for label, widget in [('Источник', self.rule_source), ('Событие', self.rule_event),
                              ('Участник / приложение', self.rule_subject), ('Чибик', self.rule_target),
                              ('Анимация', self.rule_animation), ('Режим', self.rule_mode),
                              ('Длительность / автостоп', self.rule_duration), ('Пауза между повторами', self.rule_cooldown)]:
            form.addRow(label, widget)
        rl.addLayout(form)
        self.rule_sound = SoundOptions(hub.sound)
        rl.addWidget(self.rule_sound)
        self.rule_explanation = QLabel()
        self.rule_explanation.setWordWrap(True)
        self.rule_explanation.setObjectName('muted')
        rl.addWidget(self.rule_explanation)
        preview = QPushButton('Предпросмотр анимации')
        self.rule_preview_button = preview
        preview.clicked.connect(self.preview_rule)
        rl.addWidget(preview)
        info = QLabel('«Пока активно»: повторные сигналы продлевают анимацию, active=false останавливает её. '
                      'Для Discord выберите автостоп 10 секунд: RPC обновляет активность раз в 3 секунды. '
                      'Падение и перетаскивание имеют приоритет. Скрытый чибик остаётся скрытым.')
        info.setWordWrap(True)
        rl.addWidget(info)
        self.rule_edit_controls = [self.rule_enabled, self.rule_source, self.rule_event, self.rule_target,
                                   self.rule_animation, self.rule_subject, self.rule_mode,
                                   self.rule_duration, self.rule_cooldown, self.rule_sound, preview, remove, self.move_scope_button]
        rl.addStretch()

        log, ll = self.page()
        tabs.addTab(log, 'Диагностика')
        info = QLabel('Журнал хранится только в памяти (40 событий): источник, тип, ID участника / имя приложения '
                      'и количество сработавших правил. Тексты сообщений, звук и токены сюда не попадают.')
        info.setWordWrap(True)
        ll.addWidget(info)
        self.sound_status = QLabel()
        self.sound_status.setWordWrap(True)
        self.sound_status.setObjectName('muted')
        ll.addWidget(self.sound_status)
        self.log_list = QListWidget()
        self.log_list.setWordWrap(True)
        ll.addWidget(self.log_list, 1)
        clear = QPushButton('Очистить журнал')
        clear.clicked.connect(self.clear_log)
        ll.addWidget(clear)
        buttons = QDialogButtonBox()
        save = buttons.addButton('Сохранить и подключить', QDialogButtonBox.ButtonRole.AcceptRole)
        save.setObjectName('primary')
        buttons.addButton('Отмена', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.source_list.currentRowChanged.connect(self.select_source)
        self.rule_list.currentRowChanged.connect(self.select_rule)
        self.rule_scope_tabs.currentChanged.connect(self.switch_rule_scope)
        self.rule_owner.currentIndexChanged.connect(self.switch_rule_scope)
        self.rule_source.currentIndexChanged.connect(self.rule_source_changed)
        self.rule_target.currentIndexChanged.connect(self.update_animations)
        self.rule_source.currentIndexChanged.connect(self.explain_rule)
        self.rule_event.currentTextChanged.connect(self.explain_rule)
        self.rule_subject.textChanged.connect(self.explain_rule)
        hub.changed.connect(self.update_log)
        hub.status_changed.connect(self.update_status)
        self.refresh_sources()
        self.refresh_rules()
        self.update_log()
        self.update_status()
        tabs.currentChanged.connect(self.tab_changed)

    def page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = PixelSurface()
        content.setObjectName('content')
        layout = QVBoxLayout(content)
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        scroll.setWidget(content)
        return scroll, layout

    def open_guide(self):
        topic = 'start'
        if self.source_index >= 0:
            topic = {'local': 'code', 'notifications': 'notifications', 'discord': 'discord', 'audio': 'audio', 'vesktop': 'vesktop', 'timer': 'routines'}[self.sources[self.source_index]['kind']]
        dialog = GuideDialog(self, topic)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def capture_source(self):
        if self.source_index < 0:
            return
        source = self.sources[self.source_index]
        source['enabled'] = self.source_enabled.isChecked()
        source['name'] = self.source_fields['name'].text().strip()[:80] or source['id']
        if source['kind'] == 'local':
            source['events'] = self.csv(self.source_fields['events'].text())
            source['token'] = self.source_fields['token'].text()
        elif source['kind'] in ('notifications', 'audio'):
            source['apps'] = self.csv(self.source_fields['apps'].text())
        elif source['kind'] == 'timer':
            source.update(timer_mode=self.timer_mode.currentData(), seconds=self.timer_minutes.value() * 60,
                          break_seconds=self.timer_break.value() * 60)
            source['events'] = ['focus.work', 'focus.break'] if source['timer_mode'] == 'focus' else ['timer.done']
        else:
            for key in ('client_id', 'channel_id', 'socket'):
                source[key] = self.source_fields[key].text().strip()
            source['users'] = self.csv(self.source_fields['users'].text())
            self.discord_token = self.source_fields['token'].text().strip()

    @staticmethod
    def csv(text):
        return list(dict.fromkeys(part.strip() for part in text.split(',') if part.strip()))

    def tab_changed(self, index):
        self.capture_source()
        self.capture_rule()
        if index == 1:
            selected = self.rule_source.currentData()
            self.rule_source.blockSignals(True)
            self.rule_source.clear()
            for source in self.sources:
                self.rule_source.addItem(source['name'], source['id'])
            self.rule_source.setCurrentIndex(max(0, self.rule_source.findData(selected)))
            self.rule_source.blockSignals(False)
            self.update_events()

    def select_source(self, index):
        self.capture_source()
        self.source_index = index
        if index < 0:
            return
        source = self.sources[index]
        self.source_enabled.setChecked(source['enabled'])
        self.show_token.setChecked(False)
        for key, field in self.source_fields.items():
            value = source.get(key, '')
            if isinstance(value, list):
                value = ', '.join(value)
            if key == 'token' and source['kind'] == 'discord':
                value = self.discord_token
            field.setText(str(value))
            visible = key in ('id', 'name', 'events') or (key == 'apps' and source['kind'] in ('notifications', 'audio')) or (key in ('client_id', 'channel_id', 'users', 'socket') and source['kind'] == 'discord') or (key in ('token', 'channel_id', 'users') and source['kind'] == 'vesktop') or (key == 'token' and source['kind'] in ('local', 'discord'))
            field.setVisible(visible)
            self.source_form.labelForField(field).setVisible(visible)
            field.setReadOnly(key == 'id' or (key == 'events' and source['kind'] != 'local'))
        self.source_fields['channel_id'].setPlaceholderText('Пусто = текущий голосовой канал' if source['kind'] == 'vesktop' else 'ID голосового канала')
        self.rotate.setVisible(source['kind'] in ('local', 'vesktop'))
        self.pick_notification_app.setVisible(source['kind'] == 'notifications')
        self.test_notification_button.setVisible(source['kind'] == 'notifications')
        self.show_token.setVisible(source['kind'] in ('local', 'discord', 'vesktop'))
        self.timer_options.setVisible(source['kind'] == 'timer')
        if source['kind'] == 'timer':
            self.timer_mode.setCurrentIndex(max(0, self.timer_mode.findData(source['timer_mode'])))
            self.timer_minutes.setValue(source['seconds'] // 60)
            self.timer_break.setValue(source['break_seconds'] // 60)
            hint = 'Таймер работает только пока приложение открыто. Сохранение запускает отсчёт заново. После сна компьютера выдаётся один сигнал без очереди пропущенных напоминаний. Интервал общий для всех правил этого источника.'
        elif source['kind'] == 'notifications':
            hint = 'Выберите приложение или временно определите имя по его уведомлению. Путь к программе даёт подсказку, но в событии используется app_name. Затем сохраните источник и проверьте фильтр приложения в правиле. Тестовая кнопка проверяет сохранённую настройку; это не подтверждает имя реального мессенджера. Тексты уведомлений отбрасываются.'
        elif source['kind'] == 'vesktop':
            hint = 'Локальный плагин DesktopChibiBridge для Vesktop. Укажите разрешённые User ID. Пустой Channel ID — автоматически текущий голосовой канал; заполненный — только указанный канал. Ключ этого источника генерируется приложением; это НЕ токен Discord. Плагин читает его сам из приватного файла. Нужна отдельная сборка Vencord с плагином. Звук и переписка не передаются.'
        elif source['kind'] == 'audio':
            hint = 'Состояние выходных аудиопотоков PipeWire, без записи звука и микрофона. Приложение: firefox или путь /snap/bin/firefox. Событие audio.playing, режим «Пока активно», анимация «Говорит». Тишина внутри работающего видео не распознаётся как пауза. При выключенном звуке потока реакция остановится.'
        elif source['kind'] == 'discord':
            self.source_fields['socket'].setPlaceholderText(default_discord_socket())
            hint = 'Нужен одобренный Discord RPC Client ID и OAuth-токен со scope rpc.voice.read. Это НЕ токен аккаунта. Токен хранится только до закрытия программы; после перезапуска его нужно ввести снова. Канал и участники ограничены указанными ID. Звук не читается.'
        else:
            event = source['events'][0] if source['events'] else 'custom'
            hint = f'Локальный канал доступен текущему пользователю ОС. Отдельный ключ ограничивает источник и допустимые события. Команда после сохранения:\n.venv/bin/python -m desktop_chibi.emit --source {source["id"]} --event {event}\nДля внешнего инструмента задавайте CHIBI_EVENT_TOKEN и --token-env. Ключ не даёт права выполнять команды.'
        self.source_hint.setText(hint)
        self.update_status()

    def refresh_sources(self):
        self.source_list.blockSignals(True)
        self.source_list.clear()
        for source in self.sources:
            self.source_list.addItem(source['name'])
        self.source_list.blockSignals(False)
        self.source_list.setCurrentRow(0)
        self.rule_source.blockSignals(True)
        selected = self.rule_source.currentData()
        self.rule_source.clear()
        for source in self.sources:
            self.rule_source.addItem(source['name'], source['id'])
        self.rule_source.setCurrentIndex(max(0, self.rule_source.findData(selected)))
        self.rule_source.blockSignals(False)
        self.update_events()

    def choose_notification_app(self):
        dialog = NotificationPicker(self)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                apps = self.csv(self.source_fields['apps'].text())
                name = dialog.name.text()
                self.source_fields['apps'].setText(', '.join(dict.fromkeys([*apps, name])))
                self.source_status.setText('Имя добавлено в черновик. Сохраните настройки; в правиле выберите то же имя. Для автоматического создания правила используйте «Быструю настройку».')
        finally:
            dialog.deleteLater()

    def test_notification(self):
        if self.source_index < 0:
            return
        source = self.hub.source(self.sources[self.source_index]['id'])
        if not source or not source['enabled'] or not source.get('apps'):
            self.source_status.setText('Сначала включите источник, выберите приложение и сохраните настройки.')
            return
        try:
            send_test_notification(source['apps'][0])
            self.source_status.setText('Тест отправлен. Проверьте чибика и вкладку «Диагностика»: событие должно иметь хотя бы одну реакцию.')
        except ValueError as error:
            self.source_status.setText(str(error))

    def add_source(self):
        self.capture_source()
        identity, ok = QInputDialog.getText(self, 'Локальный источник', 'ID: латиница, цифры, _ или -')
        if not ok:
            return
        import re
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', identity) or any(s['id'] == identity for s in self.sources) or len(self.sources) >= 32:
            QMessageBox.warning(self, 'Неверный ID', 'Нужен уникальный ID; допускается максимум 32 источника.')
            return
        self.sources.append({'id': identity, 'name': identity, 'kind': 'local', 'enabled': False,
                             'events': ['custom'], 'token': secrets.token_urlsafe(32)})
        self.refresh_sources()
        self.source_list.setCurrentRow(len(self.sources) - 1)

    def capture_rule(self):
        if self.rule_index < 0 or self.rule_index >= len(self.rules):
            return
        self.rules[self.rule_index].update(enabled=self.rule_enabled.isChecked(), source=self.rule_source.currentData(),
             event=self.rule_event.currentText(), target='*' if self.rules[self.rule_index].get('scope') == 'global' else self.rule_target.currentData(), animation=self.rule_animation.currentData(),
             subject=self.rule_subject.text().strip(), mode=self.rule_mode.currentData(),
             duration=self.rule_duration.value(), cooldown=self.rule_cooldown.value())
        sound = self.rule_sound.value()
        if sound['enabled'] or sound['file'] or 'sound' in self.rules[self.rule_index]:
            self.rules[self.rule_index]['sound'] = sound

    def select_rule(self, index):
        self.capture_rule()
        self.rule_index = self.visible_rule_indices[index] if 0 <= index < len(self.visible_rule_indices) else -1
        if self.rule_index < 0:
            return
        rule = self.rules[self.rule_index]
        self.rule_enabled.setChecked(rule['enabled'])
        self.rule_source.setCurrentIndex(max(0, self.rule_source.findData(rule['source'])))
        self.update_events()
        self.rule_event.setCurrentText(rule['event'])
        self.rule_target.setCurrentIndex(max(0, self.rule_target.findData('*' if rule.get('scope') == 'global' else rule['target'])))
        self.update_animations()
        self.rule_animation.setCurrentIndex(max(0, self.rule_animation.findData(rule['animation'])))
        self.rule_subject.setText(rule.get('subject', ''))
        self.rule_mode.setCurrentIndex(max(0, self.rule_mode.findData(rule['mode'])))
        self.rule_duration.setValue(rule['duration'])
        self.rule_cooldown.setValue(rule['cooldown'])
        self.rule_sound.setValue(rule.get('sound', {}))
        self.explain_rule()

    def explain_rule(self, *_):
        source = next((s for s in self.sources if s['id'] == self.rule_source.currentData()), None)
        if source is None:
            self.rule_explanation.setText('Выберите источник события.')
        elif source['kind'] == 'local':
            self.rule_explanation.setText('Это локальный канал: событие должен отправить внешний hook или команда emit. '
                'Слова voice.speaking и firefox здесь лишь имя события и метка. '
                'Для уведомлений Telegram / Firefox выберите «Системные уведомления Linux → message.received».')
        elif source['kind'] == 'vesktop':
            self.rule_explanation.setText('Речь выбранного друга через плагин Vesktop. Событие voice.speaking, User ID участника, анимация «Говорит», режим «Пока активно» с автостопом 6 секунд. OAuth и Client ID не нужны.')
        elif source['kind'] == 'audio':
            self.rule_explanation.setText('Воспроизведение звука выбранного приложения через PipeWire. Используйте audio.playing, анимацию «Говорит» и режим «Пока активно» с автостопом 6 секунд. Это состояние потока, без записи и распознавания речи.')
        elif source['kind'] == 'notifications':
            self.rule_explanation.setText('Это системные уведомления. Приложение: ' +
                (self.rule_subject.text().strip() or 'все разрешённые в источнике') +
                '. Режим «Показать на время» запускает выбранную анимацию при каждом разрешённом уведомлении.')
        elif source['kind'] == 'timer':
            self.rule_explanation.setText('Встроенный таймер. timer.done — конец отсчёта; focus.break — начало перерыва; focus.work — возврат к работе. Выберите анимацию и, при желании, свой звук.')
        else:
            self.rule_explanation.setText('Это события речи Discord RPC. Для Telegram / Firefox нужен источник системных уведомлений.')

    def rule_source_changed(self, *_):
        self.update_events()
        source = next((s for s in self.sources if s['id'] == self.rule_source.currentData()), None)
        if source and source['kind'] in ('audio', 'vesktop'):
            self.rule_mode.setCurrentIndex(self.rule_mode.findData('hold'))
            self.rule_duration.setValue(6)
            self.rule_cooldown.setValue(0)
            self.rule_animation.setCurrentIndex(max(0, self.rule_animation.findData('talk')))

    def update_events(self, *_):
        current = self.rule_event.currentText()
        self.rule_event.clear()
        source = next((s for s in self.sources if s['id'] == self.rule_source.currentData()), None)
        if source:
            self.rule_event.addItems(source['events'])
        self.rule_event.setCurrentText(current)

    def update_animations(self, *_):
        selected = self.rule_animation.currentData()
        self.rule_animation.clear()
        item = self.hub.manager.find(self.rule_target.currentData())
        states = dict(STATES)
        if item:
            for state in item.pet.animations.clips:
                states.setdefault(state, state)
        for state, label in states.items():
            self.rule_animation.addItem(label, state)
        self.rule_animation.setCurrentIndex(max(0, self.rule_animation.findData(selected)))

    def move_rule_scope(self):
        self.capture_rule()
        if self.rule_index < 0:
            return
        index = self.rule_index
        rule = self.rules[index]
        common = rule.get('scope') != 'global'
        rule['scope'] = 'global' if common else 'personal'
        rule['target'] = '*' if common else self.rule_owner.currentData()
        self.rule_index = -1
        self.rule_scope_tabs.setCurrentIndex(1 if common else 0)
        self.refresh_rules()
        self.rule_list.setCurrentRow(self.visible_rule_indices.index(index))

    def switch_rule_scope(self, *_):
        self.capture_rule()
        self.rule_index = -1
        self.refresh_rules()

    def refresh_rules(self):
        common = self.rule_scope_tabs.currentIndex() == 1
        self.rule_owner.setVisible(not common)
        self.move_scope_button.setText('Сделать личным для «' + self.rule_owner.currentText() + '»' if common else 'Сделать общим')
        self.rule_scope_hint.setText('Общие: для всех чибиков в режиме «С событиями». Личное правило для того же события имеет приоритет.' if common
                                     else 'Личные правила только для выбранного здесь чибика. Общие настройки находятся в соседней вкладке.')
        self.rule_list.blockSignals(True)
        self.rule_list.clear()
        self.visible_rule_indices = []
        for index, rule in enumerate(self.rules):
            is_common = rule.get('scope', 'personal') == 'global'
            if is_common != common or (not common and rule['target'] != self.rule_owner.currentData()):
                continue
            self.visible_rule_indices.append(index)
            self.rule_list.addItem(f'{"✓" if rule["enabled"] else "○"} {rule["source"]} / {rule["event"]} / '
                                  f'{rule.get("subject") or "Все разрешённые"} → {STATES.get(rule["animation"], rule["animation"])}')
        self.rule_list.blockSignals(False)
        self.rule_index = -1
        for widget in self.rule_edit_controls:
            widget.setEnabled(bool(self.visible_rule_indices))
        self.rule_target.setEnabled(bool(self.visible_rule_indices) and not common)
        self.rule_target.model().item(self.rule_target.findData('*')).setEnabled(common)
        if self.visible_rule_indices:
            self.rule_list.setCurrentRow(0)
        else:
            self.rule_explanation.setText('Правил пока нет. Нажмите «+ Правило» или настройте реакцию из окна чибика.')

    def add_rule(self):
        self.capture_source()
        self.capture_rule()
        if len(self.rules) >= 128:
            return
        source = self.sources[self.source_index] if self.source_index >= 0 else self.sources[0]
        notification = source['kind'] in ('notifications', 'audio')
        audio = source['kind'] in ('audio', 'vesktop')
        common = self.rule_scope_tabs.currentIndex() == 1
        self.rules.append({'id': uuid.uuid4().hex, 'enabled': True, 'source': source['id'],
                           'event': source['events'][0] if source['events'] else 'custom',
                           'target': '*' if common else self.rule_owner.currentData(), 'scope': 'global' if common else 'personal',
                           'animation': 'talk' if audio else 'notify',
                           'subject': source['apps'][0] if notification and len(source.get('apps', [])) == 1 else '',
                           'mode': 'hold' if audio else 'pulse', 'duration': 6 if audio else 4, 'cooldown': 0 if audio else 2})
        self.refresh_rules()
        self.rule_list.setCurrentRow(len(self.visible_rule_indices) - 1)

    def remove_rule(self):
        if 0 <= self.rule_index < len(self.rules):
            self.rules.pop(self.rule_index)
            self.rule_index = -1
            self.refresh_rules()

    def preview_rule(self):
        self.capture_rule()
        if self.rule_index < 0:
            return
        rule = self.rules[self.rule_index]
        targets = self.hub.manager.items if rule.get('scope') == 'global' else [self.hub.manager.find(rule['target'])]
        for target in targets:
            if target and (rule.get('scope') != 'global' or target.controller.mode == 'functional'):
                import time
                target.pet.trigger_notification('manual-preview', rule['animation'], rule['duration'], time.monotonic())
        try:
            self.hub.sound.play(rule.get('sound', {}))
        except ValueError as error:
            self.rule_explanation.setText(str(error))

    def update_status(self):
        if self.source_index >= 0:
            self.source_status.setText('Состояние: ' + self.hub.status.get(self.sources[self.source_index]['id'], 'Выключен'))
        message = self.hub.status.get('sound', '')
        self.sound_status.setText('Звук: ' + message if message else '')
        self.sound_status.setVisible(bool(message))

    def update_log(self):
        self.log_list.clear()
        reasons = {'personal': 'действует личное правило вместо общего', 'started': 'анимация запущена', 'waiting': 'ждёт приземления',
                   'hidden': 'реакция принята, но чибик скрыт', 'cooldown': 'повтор: пауза между запусками',
                   'decorative': 'декоративный режим — события выключены', 'missing': 'цель удалена',
                   'animation': 'анимация недоступна', 'filter': 'не совпал фильтр приложения / участника',
                   'stopped': 'анимация остановлена'}
        for entry in reversed(self.hub.log):
            details = '; '.join((value.get('target', '') + ': ' if value.get('target') else '') +
                                reasons.get(value['result'], value['result']) for value in entry.get('outcomes', []))
            if not details:
                details = 'Нет включённых правил для этого события' if entry['matched'] == 0 else 'Реакция принята'
            self.log_list.addItem(f'{entry["time"]} / {entry["subject"]} / реакций: {entry["matched"]}\n{details}')
            self.log_list.item(self.log_list.count() - 1).setToolTip(
                f'{entry["source"]} / {entry["event"]} / {"ON" if entry["active"] else "OFF"}\n{details}')

    def clear_log(self):
        self.hub.log.clear()
        self.update_log()

    def save(self):
        self.capture_source()
        self.capture_rule()
        try:
            self.hub.configure(self.sources, self.rules, self.discord_token)
        except (ValueError, TypeError) as error:
            QMessageBox.warning(self, 'Проверьте настройки', str(error))
            return
        self.accept()
