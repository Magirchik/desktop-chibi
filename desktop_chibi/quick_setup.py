"""Three-step presets with a review of permissions before any source is enabled."""
import copy
import uuid
import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QFileDialog, QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)
from .events import validate_config
from .animations import STATES
from .guides import GuideDialog, event_command
from .performance import MAX_COMPANIONS
from .pixel_ui import apply_theme, window_bar, PixelSurface
from .sound_ui import SoundOptions
from .notification_setup import NotificationPicker

SCENARIOS = {
    'decorative': ('Просто гулять', 'Декоративный персонаж без правил. Никакие источники подключаться не будут.'),
    'messages': ('Сообщения мессенджера', 'Чибик покажет уведомление, когда выбранный мессенджер отправит системное уведомление. Только Linux.'),
    'vesktop': ('Друг говорит в Vesktop (локальный плагин)', 'События речи выбранного друга через DesktopChibiBridge. Без OAuth и токена аккаунта; требуется подготовленный плагин Vesktop.'),
    'audio': ('Звук приложения (Firefox, плеер)', 'Анимация, пока приложение воспроизводит звук. Linux + PipeWire; без записи звука или микрофона.'),
    'code': ('Задача или генерация завершилась', 'Две реакции: «Готово» и «Ошибка». Вы добавите готовую команду в hook своего инструмента; приложение само не читает редактор.'),
    'reminder': ('Напоминать о перерыве', 'Повторяющееся напоминание: размяться, выпить воды или отдохнуть от экрана.'),
    'timer': ('Обратный отсчёт', 'Один сигнал через выбранное время, пока Desktop Chibi открыт.'),
    'focus': ('Работа / перерыв', 'Чередование работы и отдыха: две разные реакции, например 25 и 5 минут.'),
    'discord': ('Друг говорит в Discord', 'Анимация «Говорит» для разрешённых участников одного канала. Нужен одобренный Discord RPC/OAuth-доступ — одного клиента Discord недостаточно.'),
}


class QuickSetup(QDialog):
    def __init__(self, owner, scenario='decorative'):
        super().__init__(owner)
        self.owner, self.hub, self.manager = owner, owner.event_hub, owner.manager
        self.step = 0
        self.setWindowTitle('Быстрая настройка чибика')
        apply_theme(self)
        self.resize(650, 660)
        self.setMinimumSize(500, 440)
        layout = QVBoxLayout(self)
        self.step_label = window_bar('Помощник настройки')
        layout.addWidget(self.step_label)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        first = self.page()
        self.scenario = QComboBox()
        for key, (name, _) in SCENARIOS.items():
            self.scenario.addItem(name, key)
            if sys.platform == 'win32' and key in ('messages', 'audio', 'vesktop'):
                item = self.scenario.model().item(self.scenario.count() - 1)
                item.setEnabled(False)
                item.setToolTip('Этот источник использует системные службы Linux.')
        first.addWidget(QLabel('Что должен делать чибик?'))
        first.addWidget(self.scenario)
        self.description = QLabel()
        self.description.setWordWrap(True)
        first.addWidget(self.description)
        self.guide_button = QPushButton('Пошаговый гайд по этому сценарию')
        self.guide_button.clicked.connect(self.help)
        first.addWidget(self.guide_button)
        first.addStretch()

        second = self.page()
        form = QFormLayout()
        self.target = QComboBox()
        self.target.addItem('+ Новый чибик', '__new__')
        if len(self.manager.items) >= MAX_COMPANIONS:
            self.target.model().item(0).setEnabled(False)
        for item in self.manager.items:
            self.target.addItem(item.name, item.identity)
        self.target.setCurrentIndex(self.target.findData(self.manager.selected.identity))
        form.addRow('Кого настроить', self.target)
        self.name = QLineEdit('Новый чибик')
        self.name.setMaxLength(60)
        form.addRow('Имя нового', self.name)
        self.fields = {'name': self.name}
        for key, label, placeholder in [
            ('app', 'Приложение', 'Точное app_name, например Telegram Desktop'),
            ('client_id', 'Discord Client ID', 'ID одобренного RPC-приложения'),
            ('channel_id', 'Discord Channel ID', 'Для Vesktop: пусто = текущий голосовой канал'),
            ('users', 'Discord User ID', 'ID друзей через запятую'),
            ('token', 'OAuth-токен', 'Access token со scope rpc.voice.read; не токен аккаунта')]:
            field = QLineEdit()
            field.setPlaceholderText(placeholder)
            if key == 'token':
                field.setEchoMode(QLineEdit.EchoMode.Password)
                field.setText(self.hub.discord_token)
            form.addRow(label, field)
            self.fields[key] = field
        self.notification_animation = QComboBox()
        form.addRow('Анимация уведомления', self.notification_animation)
        self.form = form
        self.minutes = QSpinBox()
        self.break_minutes = QSpinBox()
        for spin in (self.minutes, self.break_minutes):
            spin.setRange(1, 1440)
            spin.setSuffix(' мин.')
        self.minutes.setValue(25)
        self.break_minutes.setValue(5)
        form.addRow('Через / каждые', self.minutes)
        form.addRow('Перерыв', self.break_minutes)
        second.addLayout(form)
        self.sound_options = SoundOptions(self.hub.sound)
        second.addWidget(self.sound_options)
        self.pick_app = QPushButton('Выбрать приложение / определить имя по уведомлению…')
        self.pick_app.clicked.connect(self.choose_app)
        second.addWidget(self.pick_app)
        self.command = QLineEdit(event_command())
        self.command.setReadOnly(True)
        self.command.setToolTip(self.command.text())
        self.copy_button = QPushButton('Скопировать команду завершения')
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.command.text()))
        second.addWidget(self.command)
        second.addWidget(self.copy_button)
        self.details = QLabel()
        self.details.setWordWrap(True)
        second.addWidget(self.details)
        second.addStretch()

        third = self.page()
        self.review = QLabel()
        self.review.setTextFormat(Qt.TextFormat.PlainText)
        self.review.setWordWrap(True)
        third.addWidget(self.review)
        self.consent = QCheckBox('Разрешаю указанный источник с перечисленными ограничениями')
        third.addWidget(self.consent)
        third.addStretch()
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        actions = QHBoxLayout()
        self.back = QPushButton('Назад')
        self.back.clicked.connect(self.previous)
        cancel = QPushButton('Отмена')
        cancel.clicked.connect(self.reject)
        self.next = QPushButton('Далее')
        self.next.setObjectName('primary')
        self.next.clicked.connect(self.advance)
        actions.addWidget(self.back)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(self.next)
        layout.addLayout(actions)
        self.scenario.currentIndexChanged.connect(self.scenario_changed)
        self.target.currentIndexChanged.connect(self.show_fields)
        self.target.currentIndexChanged.connect(self.load_sound_options)
        source = self.hub.source('notifications')
        if source and len(source.get('apps', [])) == 1:
            self.fields['app'].setText(source['apps'][0])
        self.scenario.setCurrentIndex(max(0, self.scenario.findData(scenario)))
        self.scenario_changed()
        self.update_step()

    def page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = PixelSurface()
        content.setObjectName('content')
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        scroll.setWidget(content)
        self.stack.addWidget(scroll)
        return layout

    def scenario_changed(self, *_):
        key = self.scenario.currentData()
        self.description.setText(SCENARIOS[key][1])
        self.consent.setText('Запустить таймер с указанными интервалами' if key in ('reminder', 'timer', 'focus') else
                             'Разрешаю указанный источник с перечисленными ограничениями')
        self.consent.setChecked(False)
        self.show_fields()
        self.load_sound_options()
        if key in ('reminder', 'timer', 'focus'):
            source = self.hub.source(key)
            self.minutes.setValue(source['seconds'] // 60)
            self.break_minutes.setValue(source['break_seconds'] // 60)
        if key == 'vesktop':
            source = self.hub.source('vesktop')
            previous = self.hub.source('discord')
            for field in ('channel_id', 'users'):
                value = (source or {}).get(field, '') if field == 'channel_id' else (source or {}).get(field) or (previous or {}).get(field, '')
                self.fields[field].setText(', '.join(value) if isinstance(value, list) else value)
            self.notification_animation.setCurrentIndex(max(0, self.notification_animation.findData('talk')))
        if key in ('audio', 'messages'):
            source = self.hub.source('audio' if key == 'audio' else 'notifications')
            apps = source.get('apps', []) if source else []
            self.fields['app'].setText(apps[0] if len(apps) == 1 else 'firefox' if key == 'audio' else '')
            self.notification_animation.setCurrentIndex(max(0, self.notification_animation.findData('talk' if key == 'audio' else 'notify')))

    def load_sound_options(self, *_):
        identity = {'messages': 'notifications'}.get(self.scenario.currentData(), self.scenario.currentData())
        existing = next((r for r in self.hub.rules if r['source'] == identity and r['target'] == self.target.currentData()), None)
        self.sound_options.setValue(existing.get('sound', {}) if existing else {})

    def show_fields(self, *_):
        scenario = self.scenario.currentData()
        timed = scenario in ('reminder', 'timer', 'focus')
        for widget, show in ((self.minutes, timed), (self.break_minutes, scenario == 'focus')):
            widget.setVisible(show)
            self.form.labelForField(widget).setVisible(show)
        self.form.labelForField(self.minutes).setText('Работа' if scenario == 'focus' else 'Интервал' if scenario == 'reminder' else 'Через')
        self.sound_options.setVisible(scenario not in ('decorative', 'audio', 'vesktop', 'discord'))
        for key, field in self.fields.items():
            show = key == 'name' and self.target.currentData() == '__new__' or key == 'app' and scenario in ('messages', 'audio') or (key in ('client_id', 'channel_id', 'users', 'token') and scenario == 'discord' or key in ('channel_id', 'users') and scenario == 'vesktop')
            field.setVisible(show)
            self.form.labelForField(field).setVisible(show)
        self.notification_animation.setVisible(scenario in ('messages', 'audio', 'vesktop'))
        self.form.labelForField(self.notification_animation).setVisible(scenario in ('messages', 'audio', 'vesktop'))
        self.form.labelForField(self.notification_animation).setText('Анимация речи' if scenario == 'vesktop' else 'Анимация при звуке' if scenario == 'audio' else 'Анимация уведомления')
        selected_animation = self.notification_animation.currentData() or 'notify'
        self.notification_animation.clear()
        item = self.manager.find(self.target.currentData())
        if item is None:
            item = self.manager.selected
        for key, label in {**STATES, **{key: key for key in item.pet.animations.clips if key not in STATES}}.items():
            suffix = ' — своя анимация' if key in item.pet.animations.clips else ' — индикатор звука' if key == 'talk' else ' — встроенная / покой'
            self.notification_animation.addItem(label + suffix, key)
        self.notification_animation.setCurrentIndex(max(0, self.notification_animation.findData(selected_animation)))
        self.command.setVisible(scenario == 'code')
        self.pick_app.setVisible(scenario in ('messages', 'audio'))
        self.pick_app.setText('Выбрать файл программы…' if scenario == 'audio' else 'Выбрать приложение / определить имя по уведомлению…')
        self.copy_button.setVisible(scenario == 'code')
        text = {
            'decorative': 'Режим «Декоративный», вес 1. Для существующего чибика правила сохранятся, но не будут запускаться.',
            'messages': 'Выберите приложение кнопкой выше. Путь даст предполагаемое имя; «Поймать уведомление» определит точное. Получите системное уведомление от мессенджера, выберите найденное имя и примените правило. В поле «Анимация уведомления» выберите свой клип; по умолчанию это «Уведомление». Это добавит правило, остальные правила сохранятся.',
            'vesktop': 'Включите DesktopChibiBridge в Vesktop. Укажите ID голосового канала и выбранного друга; OAuth и Client ID не нужны. Мост передаёт только состояние речи, не звук и не переписку. Плагин сам читает защищённый локальный ключ, копировать его не надо.',
            'audio': 'Укажите firefox, Firefox или путь к программе. Анимация работает, пока её выходной аудиопоток запущен. Пауза, остановка или отключение звука потока прекращают реакцию. Тишина внутри проигрываемого видео не анализируется. Если своего клипа «Говорит» нет, появится анимированный индикатор звука.',
            'code': 'Скопированную команду можно вызвать из любой папки после шага 3. Добавьте её в hook завершения своего инструмента. У разных инструментов этот hook настраивается по-разному.',
            'reminder': 'Периодическая реакция без внешних подключений. Если компьютер спал, пропущенные сигналы не накапливаются. Интервал этого источника общий для всех чибиков; после сохранения отсчёт начнётся заново.',
            'timer': 'Однократный сигнал. Отсчёт работает, пока приложение открыто, и начинается заново при сохранении настроек. Таймер не покажет вручную скрытого чибика; для него можно отдельно разрешить звук.',
            'focus': 'Сначала работа, затем перерыв. Начало отдыха — «Уведомление», возвращение к работе — «Готово». Анимации и отдельные звуки можно изменить в «События и правила». Интервалы этого источника общие для всех чибиков.',
            'discord': 'Нужен заранее полученный одобренный RPC/OAuth-доступ. Не вставляйте токен аккаунта. OAuth-токен останется только в памяти до закрытия программы.',
        }[scenario]
        self.details.setText(text)

    def prepare(self, target):
        sources = copy.deepcopy(self.hub.sources)
        rules = copy.deepcopy(self.hub.rules)
        scenario = self.scenario.currentData()
        specs = []
        if scenario == 'messages':
            app = self.fields['app'].text().strip()
            if not app:
                raise ValueError('Укажите точное имя приложения. Пример: Telegram Desktop.')
            source = next(s for s in sources if s['id'] == 'notifications')
            source['apps'] = list(dict.fromkeys([*source.get('apps', []), app]))
            source['enabled'] = True
            specs = [('notifications', 'message.received', self.notification_animation.currentData() or 'notify', app, 'pulse', 4)]
        elif scenario == 'vesktop':
            source = next(s for s in sources if s['id'] == 'vesktop')
            source['channel_id'] = self.fields['channel_id'].text().strip()
            selected_users = list(dict.fromkeys(value.strip() for value in self.fields['users'].text().split(',') if value.strip()))
            if not selected_users:
                raise ValueError('Укажите User ID друга. Channel ID можно оставить пустым — текущий канал автоматически.')
            source['users'] = list(dict.fromkeys([*source.get('users', []), *selected_users]))
            source['enabled'] = True
            specs = [('vesktop', 'voice.speaking', self.notification_animation.currentData() or 'talk', user, 'hold', 6) for user in selected_users]
        elif scenario == 'audio':
            app = self.fields['app'].text().strip()
            if not app:
                raise ValueError('Укажите приложение, например firefox или путь /snap/bin/firefox.')
            source = next(s for s in sources if s['id'] == 'audio')
            source['apps'] = list(dict.fromkeys([*source.get('apps', []), app]))
            source['enabled'] = True
            specs = [('audio', 'audio.playing', self.notification_animation.currentData() or 'talk', app, 'hold', 6)]
        elif scenario == 'code':
            source = next(s for s in sources if s['id'] == 'code')
            source['enabled'] = True
            source['events'] = list(dict.fromkeys([*source['events'], 'code.done', 'code.failed']))
            specs = [('code', 'code.done', 'success', '', 'pulse', 4), ('code', 'code.failed', 'error', '', 'pulse', 4)]
        elif scenario in ('reminder', 'timer', 'focus'):
            source = next(s for s in sources if s['id'] == scenario)
            source.update(enabled=True, seconds=self.minutes.value() * 60, break_seconds=self.break_minutes.value() * 60)
            specs = ([(scenario, 'focus.break', 'notify', '', 'pulse', 4),
                      (scenario, 'focus.work', 'success', '', 'pulse', 4)] if scenario == 'focus' else
                     [(scenario, 'timer.done', 'notify', '', 'pulse', 4)])
        elif scenario == 'discord':
            source = next(s for s in sources if s['id'] == 'discord')
            for key in ('client_id', 'channel_id'):
                source[key] = self.fields[key].text().strip()
            source['users'] = list(dict.fromkeys(value.strip() for value in self.fields['users'].text().split(',') if value.strip()))
            if not source['client_id'] or not source['channel_id'] or not source['users'] or not self.fields['token'].text().strip():
                raise ValueError('Заполните Client ID, Channel ID, User ID и OAuth-токен. Если доступа нет, прочитайте гайд и оставьте источник выключенным.')
            source['enabled'] = True
            specs = [('discord', 'voice.speaking', 'talk', user, 'hold', 10) for user in source['users']]
        for identity, event, animation, subject, mode, duration in specs:
            existing = next((rule for rule in rules if (rule['source'], rule['event'], rule['target'], rule.get('subject', '')) ==
                             (identity, event, target, subject) and (scenario in ('messages', 'audio', 'vesktop') or rule['animation'] == animation)), None)
            if existing is None and scenario == 'vesktop':
                existing = next((rule for rule in rules if rule['source'] == 'discord' and rule['event'] == event
                                 and rule['target'] == target and rule.get('subject') == subject), None)
                if existing:
                    existing.update(source='vesktop', scope='personal')
            if existing:
                existing['enabled'] = True
                if scenario in ('messages', 'audio', 'vesktop'):
                    existing.update(animation=animation, mode=mode)
                    if scenario in ('audio', 'vesktop'):
                        existing.update(duration=6, cooldown=0)
            else:
                rules.append({'id': uuid.uuid4().hex, 'enabled': True, 'source': identity,
                              'event': event, 'target': target, 'animation': animation, 'subject': subject,
                              'mode': mode, 'duration': duration, 'cooldown': 0 if scenario in ('audio', 'vesktop') else 2, 'scope': 'personal'})
                existing = rules[-1]
            if scenario in ('messages', 'code', 'reminder', 'timer', 'focus'):
                sound = self.sound_options.value()
                if sound['enabled'] or sound['file'] or 'sound' in existing:
                    existing['sound'] = sound
        if scenario == 'vesktop' and not any(r['enabled'] and r['source'] == 'discord' for r in rules):
            next(s for s in sources if s['id'] == 'discord')['enabled'] = False
        validate_config(sources, rules)
        return sources, rules

    def choose_app(self):
        if self.scenario.currentData() == 'audio':
            path, _ = QFileDialog.getOpenFileName(self, 'Выбрать приложение для реакции на звук')
            if path:
                self.fields['app'].setText(path)
            return
        dialog = NotificationPicker(self)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.fields['app'].setText(dialog.name.text())
        finally:
            dialog.deleteLater()

    def advance(self):
        self.error.setText('')
        if self.step == 0:
            self.step = 1
        elif self.step == 1:
            try:
                self.prepare(self.target.currentData())
            except (ValueError, StopIteration) as error:
                self.error.setText(str(error) or 'Не найден нужный источник. Проверьте точные настройки.')
                return
            self.step = 2
            scenario = self.scenario.currentData()
            name = (self.name.text().strip() or 'Новый чибик') if self.target.currentData() == '__new__' else self.target.currentText()
            summary = f'Чибик: {name}\nСценарий: {SCENARIOS[scenario][0]}\n'
            summary += 'Режим: Декоративный · вес 1\nИсточники: без изменений.' if scenario == 'decorative' else 'Режим: С событиями · вес 3\n'
            if scenario == 'messages':
                summary += f'Анимация: {self.notification_animation.currentText()}.\nРазрешить системные уведомления: {self.fields["app"].text().strip()}.\nДругие уже разрешённые приложения останутся в списке.'
            elif scenario == 'vesktop':
                summary += f'Локальный мост Vesktop: канал {self.fields["channel_id"].text().strip() or "текущий автоматически"}, пользователи {self.fields["users"].text().strip()}.\nАнимация: {self.notification_animation.currentText()}.\nТолько ID и начало/конец речи; без аудио и токена Discord. Нужен плагин DesktopChibiBridge.'
            elif scenario == 'audio':
                summary += f'Наблюдать состояние аудиопотоков: {self.fields["app"].text().strip()}.\nАнимация: {self.notification_animation.currentText()}.\nЗвук, микрофон и содержимое видео не читаются. Доступны имена и состояние аудиопотоков; сохраняются только разрешённые приложения.'
            elif scenario == 'code':
                summary += 'Разрешить локальный источник code: code.done и code.failed.\nНикакие команды автоматически запускаться не будут.'
            elif scenario in ('reminder', 'timer', 'focus'):
                summary += f'Встроенный таймер: {self.minutes.value()} мин.'
                if scenario == 'focus':
                    summary += f' работы / {self.break_minutes.value()} мин. отдыха.'
                summary += '\nСохранение перезапускает отсчёт. Внешние приложения не читаются.'
            elif scenario == 'discord':
                summary += f'Разрешить Discord RPC для канала {self.fields["channel_id"].text().strip()} и User ID {self.fields["users"].text().strip()}.\nЭто заменит прежний канал и список участников этого источника.\nАудио не читается; OAuth-токен не сохраняется на диск.'
            sound = self.sound_options.value()
            if sound['enabled'] and scenario in ('messages', 'code', 'reminder', 'timer', 'focus'):
                summary += f'\nСвой звук: {sound["file"]} · {sound["volume"]} %.'
                summary += '\nЗвук скрытого чибика: ' + ('включён.' if sound['when_hidden'] else 'выключен.')
            self.review.setText(summary)
            self.consent.setVisible(scenario != 'decorative')
        else:
            self.finish_setup()
            return
        self.update_step()

    def previous(self):
        self.step = max(0, self.step - 1)
        self.consent.setChecked(False)
        self.update_step()

    def update_step(self):
        self.stack.setCurrentIndex(self.step)
        self.step_label.setText(f'Помощник настройки · {self.step + 1} / 3')
        self.back.setEnabled(self.step > 0)
        self.next.setText('Применить' if self.step == 2 else 'Далее')

    def finish_setup(self):
        scenario = self.scenario.currentData()
        if scenario != 'decorative' and not self.consent.isChecked():
            self.error.setText('Подтвердите разрешение источника или нажмите «Отмена».')
            return
        target = self.target.currentData()
        if target == '__new__' and len(self.manager.items) >= MAX_COMPANIONS:
            self.error.setText('Уже создано 32 чибика. Выберите существующего.')
            return
        try:
            self.prepare(target)
            if target == '__new__':
                self.manager.add('decorative')
                target = self.manager.selected.identity
                self.manager.rename(self.name.text().strip() or 'Новый чибик')
            else:
                self.manager.select_id(target)
            if scenario == 'decorative':
                self.manager.set_mode(target, 'decorative')
            else:
                sources, rules = self.prepare(target)
                token = self.fields['token'].text().strip() if scenario == 'discord' else self.hub.discord_token
                self.hub.configure(sources, rules, token)
                self.manager.set_mode(target, 'functional')
            self.owner.companion_notice.setText('Настройка готова. Для проверки правил откройте «Правила и источники».')
            self.accept()
        except (ValueError, StopIteration) as error:
            self.error.setText(str(error) or 'Источник недоступен. Проверьте точные настройки.')

    def help(self):
        topic = {'decorative': 'start', 'messages': 'notifications', 'code': 'code', 'discord': 'discord', 'audio': 'audio', 'vesktop': 'vesktop',
                 'timer': 'routines', 'reminder': 'routines', 'focus': 'routines'}[self.scenario.currentData()]
        dialog = GuideDialog(self, topic)
        dialog.exec()
        dialog.deleteLater()
