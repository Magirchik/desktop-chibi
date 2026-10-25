"""Code-drawn pixel assets and a pastel desktop theme."""
from functools import lru_cache
from pathlib import Path
from PySide6.QtCore import Qt, QSize, QObject, QEvent
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QAbstractButton, QLabel, QHBoxLayout, QVBoxLayout, QWidget, QTabWidget, QSizePolicy, QSizeGrip, QMenu
from .appearance import appearance, theme_color, themed_stylesheet

THEME = '''
QWidget { color: #493258; font-family: "Pixeloid Sans", "DejaVu Sans", sans-serif; font-size: 18px; border-radius: 0; }
QDialog, QWidget#desktop { background: #e5cdef; border: 2px solid #765386; border-top-color: #fffaff; border-left-color: #fffaff; }
QWidget#content { background: transparent; }
QWidget#panel { background: #fff7fd; border: 1px solid #79548c; }
QWidget#panelHeader { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #efd7f7,stop:1 #ffe5f3); border: none; border-bottom: 1px solid #ac7ab4; }
QWidget#panelBody { background: #fff7fd; border: none; }
QWidget#titlebar { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #ffc0e0,stop:0.55 #edc8f6,stop:1 #d1bbef);
 border: 1px solid #79548c; border-top: 2px solid #fffaff; border-left: 2px solid #fffaff; border-bottom: 2px solid #9970a9; }
QLabel { background: transparent; border: none; }
QLabel#windowTitle { color: #4c2c61; font-family: "Pixeloid Sans", "DejaVu Sans", sans-serif; font-size: 18px; }
QLabel#brandTitle { color: #4c2c61; font-family: "Pixelify Sans", "DejaVu Sans", sans-serif; font-size: 18px; }
QLabel#bar { background: #f6c7e6; color: #4c2c61; font-family: "Pixeloid Sans"; font-size: 18px;
 padding: 8px 10px; border: 1px solid #79548c; border-top: 2px solid #ffffff; border-left: 2px solid #ffffff; }
QLabel#hero { color: #6e4386; font-family: "Pixeloid Sans", "DejaVu Sans", sans-serif; font-size: 27px; }
QLabel#muted { color: #82658d; font-size: 18px; }
QLabel#hint { font-size: 18px; color: #705481; background: #fff8fe; padding: 8px 10px; border: 1px solid #c7a5d5; border-left: 4px solid #dc9dc3; }
QLabel#section { color: #634076; font-family: "Pixeloid Sans", "DejaVu Sans", sans-serif; padding: 6px 6px; }
QLabel#status { color: #456e70; background: #e6f9f6; padding: 6px 10px; border: 1px solid #84b7ad; border-bottom: 2px solid #84b7ad; }
QLabel#status[tone="waiting"] { color: #965578; background: #fff0f6; border-color: #d89fbd; }
QLabel#status[tone="hidden"] { color: #80628e; background: #f0e5f7; border-color: #b99bc9; }
QLabel#caption { color: #ad4e87; font-family: "Pixeloid Sans"; padding-top: 9px; padding-bottom: 4px; border-bottom: 1px dotted #d8afd6; }
QLabel#value { background: #f0e0fa; color: #6b4085; padding: 4px 6px; border: 1px solid #b798ca; }
QLabel#error { color: #ac3868; padding: 4px; }
QLabel#animationPreview { background: transparent; border: 2px solid #ad8abf; border-top-color: #765386; border-left-color: #765386; border-bottom-color: #ffffff; border-right-color: #ffffff; }
QPushButton { background: #f2e5fa; border: 2px solid #ac88bf; border-top-color: #ffffff; border-left-color: #ffffff;
 padding: 7px 10px; min-height: 17px; }
QPushButton:hover { background: #ffe4f2; color: #914676; border-right-color: #995d91; border-bottom-color: #995d91; }
QPushButton:pressed, QPushButton:checked { background: #e4c9f1; border-top-color: #90679e; border-left-color: #90679e; border-bottom-color: #ffffff; border-right-color: #ffffff; }
QPushButton:checked { background: #f5c7e4; color: #6c3964; }
QPushButton#primary { background: #f9c9e5; color: #743d72; font-weight: bold; border-bottom-color: #af679b; border-right-color: #af679b; }
QPushButton#primary:hover { background: #ffd9ee; }
QPushButton#primary:pressed { background: #eeb5d9; border-top-color: #af679b; border-left-color: #af679b; border-bottom-color: #ffffff; border-right-color: #ffffff; }
QPushButton:disabled { color: #b49ac1; background: #f0e5f6; }
QPushButton:focus { outline: 1px dotted #865493; outline-offset: -5px; }
QComboBox, QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox { background: #fffbff;
 border: 2px solid #dbc4e8; border-top-color: #a07cb5; border-left-color: #a07cb5; padding: 6px; min-height: 17px; selection-background-color: #f1c4e4; selection-color: #4c2c61; }
QComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #bf83b0; }
QComboBox { padding-right: 28px; }
QComboBox::drop-down { width: 22px; margin: 1px; border: 1px solid #a07cb5; border-top-color: #ffffff; border-left-color: #ffffff; background: #eddef7; }
QComboBox::down-arrow, QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("@ASSETS@/arrow-down.svg"); width: 8px; height: 5px; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("@ASSETS@/arrow-up.svg"); width: 8px; height: 5px; }
QSpinBox, QDoubleSpinBox { padding-right: 22px; }
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button { width: 18px; background: #eddef7; border: 1px solid #b495c7; border-top-color: #ffffff; border-left-color: #ffffff; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; }
QPushButton::menu-indicator { image: url("@ASSETS@/arrow-down.svg"); width: 8px; height: 5px; subcontrol-position: right center; right: 5px; }
QComboBox QAbstractItemView { background: #fff7fd; border: 1px solid #9470a5; selection-background-color: #f3c7e5; selection-color: #4c2c61; padding: 3px; }
QListWidget, QTextBrowser { background: #fffaff; border: 2px solid #d8c1e7; border-top-color: #a07cb5; border-left-color: #a07cb5; padding: 4px; }
QListWidget::item { padding: 6px; }
QListWidget::item:selected { background: #efd0ed; color: #4c2c61; }
QListWidget::item:hover { background: #f8e6f4; }
QTabWidget::pane { border: 1px solid #9773aa; border-top: 2px solid #ffffff; background: #f8effb; top: -1px; }
QTabBar::tab { background: #e7d4f2; border: 1px solid #a580b8; border-top: 2px solid #ffffff;
 border-left: 2px solid #ffffff; padding: 8px 5px; margin-right: 1px; }
QTabBar::tab:selected { background: #fff5fc; color: #8f477b; border-bottom-color: #fff5fc; border-top-color: #e98bbf; }
QTabBar::tab:hover { background: #ffe3f2; }
QSlider { background: transparent; min-height: 25px; }
QSlider::groove:horizontal { height: 6px; background: #e6d4f1; border: 1px solid #a385b8; border-bottom-color: #ffffff; }
QSlider::sub-page:horizontal { background: #d8b9eb; }
QSlider::handle:horizontal { width: 13px; margin: -6px 0; background: #f5c0df; border: 2px solid #9b739f; border-top-color: #ffffff; border-left-color: #ffffff; }
QCheckBox { spacing: 8px; padding: 5px 0; }
QCheckBox::indicator { width: 15px; height: 15px; background: #fffbff; border: 2px solid #d8c1e7; border-top-color: #9e7cb1; border-left-color: #9e7cb1; }
QCheckBox::indicator:checked { background: #fff0fa; image: url("@ASSETS@/check.svg"); }
QCheckBox::indicator:disabled { background: #eadff1; border-color: #c2adc9; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: #f5eafa; width: 14px; margin: 0; border: 1px solid #d5bbdf; }
QScrollBar::handle:vertical { background: #dfc7ef; min-height: 24px; border: 2px solid #a889bb; border-top-color: #ffffff; border-left-color: #ffffff; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QMenu { background: #fff6fc; border: 1px solid #79548c; padding: 4px; }
QMenu::item { padding: 7px 20px; }
QMenu::item:selected { background: #efd0ed; color: #6c3964; }
QMenu::item:disabled { color: #b49ac1; }
QMenu::separator { height: 1px; background: #ddbfdf; margin: 4px 8px; }
QMenuBar { background: #f8eafb; color: #775087; border: none; border-bottom: 1px solid #c9a8d8; padding: 2px 3px; }
QMenuBar::item { background: transparent; padding: 4px 8px; }
QMenuBar::item:selected { background: #efd0ed; }
QMenuBar::item:pressed { background: #e5bfe2; }
QToolTip { background: #fff9fe; color: #573769; border: 1px solid #a17ab4; padding: 6px; font-size: 18px; }
QSizeGrip { background: transparent; width: 14px; height: 14px; }
'''


@lru_cache(maxsize=1)
def _load_pixel_font():
    folder = Path(__file__).parent / 'assets/fonts'
    return tuple(QFontDatabase.addApplicationFont(str(folder / name)) for name in
                 ('PixelifySans.ttf', 'PixeloidSans.ttf', 'PixeloidSans-Bold.ttf'))


def apply_theme(widget):
    _load_pixel_font()
    font = QFont('Pixeloid Sans')
    font.setPixelSize(18)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
    widget.setFont(font)
    appearance().register(widget)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    widget.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    if not hasattr(widget, '_pixel_chrome'):
        widget._pixel_chrome = _WindowChrome(widget)


def apply_theme_colors(widget):
    stylesheet = themed_stylesheet(THEME).replace('@ASSETS@', (Path(__file__).parent / 'assets').as_posix())
    if appearance().theme != 'light':
        for name in ('arrow-down', 'arrow-up', 'check'):
            stylesheet = stylesheet.replace(name + '.svg', name + '-dark.svg')
    widget.setStyleSheet(stylesheet)
    for menu in widget.findChildren(QMenu):
        menu.setStyleSheet('')
    for tabs in widget.findChildren(PixelTabs):
        for index in range(tabs.count()):
            tabs.tabInserted(index)
    for label in widget.findChildren(QLabel):
        kind = label.property('pixelGlyph')
        if kind:
            label.setPixmap(pixel_icon(kind).pixmap(14, 14))
    widget.update()
    for child in widget.findChildren(QWidget):
        child.update()


class _WindowChrome(QObject):
    """Keep a real resize grip for the decorated, resizable top-level window."""
    def __init__(self, window):
        super().__init__(window)
        self.grip = QSizeGrip(window)
        self.grip.setToolTip('Изменить размер окна')
        self.grip.setFixedSize(14, 14)
        window.installEventFilter(self)

    def eventFilter(self, window, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.WindowStateChange):
            self.grip.move(window.width() - 16, window.height() - 16)
            self.grip.setVisible(not window.isMaximized())
            self.grip.raise_()
        return False


class SystemHeading(QLabel):
    """Small retro desktop heading with a one-pixel highlight."""
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName('hero')
        self.setContentsMargins(0, 0, 2, 2)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(QColor(theme_color('#fff7ff')))
        painter.drawText(self.contentsRect().translated(1, 1), self.alignment(), self.text())
        painter.setPen(self.palette().windowText().color())
        painter.drawText(self.contentsRect(), self.alignment(), self.text())


@lru_cache(maxsize=6)
def _tile(checker=False, theme='light'):
    image = QPixmap(24, 24)
    image.fill(QColor(theme_color('#fff8fe' if checker else '#f8effb', theme)))
    painter = QPainter(image)
    if checker:
        painter.fillRect(0, 0, 12, 12, QColor(theme_color('#ecdaf5', theme)))
        painter.fillRect(12, 12, 12, 12, QColor(theme_color('#ecdaf5', theme)))
    else:
        painter.fillRect(1, 1, 1, 1, QColor(theme_color('#e9d7f2', theme)))
        painter.fillRect(13, 13, 1, 1, QColor(theme_color('#eddcf4', theme)))
    painter.end()
    return image


class PixelSurface(QWidget):
    """A cached desktop grid, painted only when the widget needs repainting."""
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawTiledPixmap(event.rect(), _tile(False, appearance().theme))


class PixelPreview(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('animationPreview')
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawTiledPixmap(event.rect(), _tile(True, appearance().theme))
        painter.end()
        super().paintEvent(event)


class NoticeLabel(QLabel):
    """Feedback reserves space only when there is an actual message."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.hide()

    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))


class PixelTabs(QTabWidget):
    """Size to the selected page, rather than the tallest hidden page."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        tab_font = QFont('Pixeloid Sans')
        tab_font.setPixelSize(18)
        tab_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -1)
        tab_font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
        self.tabBar().setFont(tab_font)
        self.currentChanged.connect(lambda *_: self.updateGeometry())

    def tabInserted(self, index):
        super().tabInserted(index)
        icons = {'Чибики': 'heart', 'Облик': 'portrait', 'Движение': 'motion',
                 'Экран': 'screen', 'Управление': 'settings', 'Анимация': 'film', 'Набор': 'folder'}
        kind = icons.get(self.tabText(index))
        if kind:
            self.setTabIcon(index, pixel_icon(kind))
            self.setIconSize(QSize(14, 14))

    def sizeHint(self):
        page = self.currentWidget()
        if page is None:
            return super().sizeHint()
        hint = page.sizeHint()
        tabs = self.tabBar().sizeHint()
        return QSize(max(hint.width(), tabs.width()) + 4, hint.height() + tabs.height() + 6)

    def minimumSizeHint(self):
        page = self.currentWidget()
        if page is None:
            return super().minimumSizeHint()
        hint = page.minimumSizeHint()
        tabs = self.tabBar().minimumSizeHint()
        return QSize(max(hint.width(), tabs.width()) + 4, hint.height() + tabs.height() + 6)

    def hasHeightForWidth(self):
        page = self.currentWidget()
        return bool(page and page.hasHeightForWidth())

    def heightForWidth(self, width):
        page = self.currentWidget()
        if page is None:
            return -1
        height = page.heightForWidth(max(1, width - 4)) if page.hasHeightForWidth() else page.sizeHint().height()
        return max(page.minimumSizeHint().height(), height) + self.tabBar().sizeHint().height() + 6


class _Hearts(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(64, 20)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        rows = ('0110110', '1111111', '1111111', '0111110', '0011100', '0001000')
        for offset, color in [(0, '#f6c4e1'), (24, '#d6c2ee'), (48, '#b9a1d6')]:
            for y, row in enumerate(rows):
                for x, value in enumerate(row):
                    if value == '1':
                        painter.fillRect(offset + x * 2, 4 + y * 2, 2, 2, QColor(color))


def pixel_icon(kind):
    return _pixel_icon(kind, appearance().theme)


@lru_cache(maxsize=48)
def _pixel_icon(kind, theme):
    """Original small UI symbols on a seven-pixel grid; no external icon theme."""
    patterns = {
        'heart': ('0110110', '1111111', '1111111', '0111110', '0011100', '0001000', '0000000'),
        'portrait': ('1111111', '1000001', '1011101', '1010101', '1001001', '1011101', '1111111'),
        'motion': ('0010000', '0111110', '1110000', '0111111', '0010010', '0000100', '0001000'),
        'screen': ('1111111', '1000001', '1010101', '1000001', '1111111', '0001000', '0011100'),
        'settings': ('0100010', '1110111', '0100010', '0100010', '0100010', '1110111', '0100010'),
        'film': ('1111111', '1010101', '1111111', '1000001', '1111111', '1010101', '1111111'),
        'folder': ('1110000', '1011111', '1000001', '1000001', '1000001', '1000001', '1111111'),
    }
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    for y, row in enumerate(patterns[kind]):
        for x, value in enumerate(row):
            if value == '1':
                painter.fillRect(x * 2, y * 2, 2, 2, QColor(theme_color('#ad4e87' if kind == 'heart' else '#6e4386', theme)))
    painter.end()
    return QIcon(pixmap)


class _WindowButton(QAbstractButton):
    def __init__(self, kind, parent):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(24, 24)
        title = {'minimize': 'Свернуть', 'maximize': 'Развернуть / восстановить', 'close': 'Закрыть окно'}[kind]
        self.setToolTip(title)
        self.setAccessibleName(title)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme_color('#ffd9ec' if self.underMouse() else '#f7eafa')))
        p.setPen(QColor(theme_color('#8a619d')))
        p.drawRect(0, 0, 23, 23)
        p.setPen(QColor(theme_color('#926a9f' if self.isDown() else '#ffffff')))
        p.drawLine(1, 1, 22, 1)
        p.drawLine(1, 1, 1, 22)
        p.setPen(QColor(theme_color('#ffffff' if self.isDown() else '#b894c8')))
        p.drawLine(2, 22, 22, 22)
        p.drawLine(22, 2, 22, 22)
        p.translate(1 if self.isDown() else 0, 1 if self.isDown() else 0)
        ink = QColor(theme_color('#774979'))
        if self.kind == 'minimize':
            p.fillRect(6, 15, 11, 2, ink)
        elif self.kind == 'maximize':
            p.fillRect(6, 6, 12, 3, ink)
            p.setPen(ink)
            p.drawRect(6, 6, 11, 11)
        else:
            for i in range(5):
                p.fillRect(6 + i * 2, 6 + i * 2, 2, 2, ink)
                p.fillRect(14 - i * 2, 6 + i * 2, 2, 2, ink)
        if self.hasFocus():
            p.setPen(QPen(ink, 1, Qt.PenStyle.DotLine))
            p.drawRect(3, 3, 17, 17)


class WindowBar(QWidget):
    def __init__(self, title, *, brand=False):
        super().__init__()
        self.setObjectName('titlebar')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 4, 4)
        row.setSpacing(4)
        icon = QLabel()
        icon.setPixmap(bunny_frame().copy(2, 0, 28, 26).scaled(26, 26, Qt.AspectRatioMode.KeepAspectRatio,
                                                             Qt.TransformationMode.FastTransformation))
        row.addWidget(icon)
        self.label = QLabel(title)
        self.label.setObjectName('brandTitle' if brand else 'windowTitle')
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.label.setToolTip(title)
        row.addWidget(self.label, 1)
        if brand:
            row.addWidget(_Hearts())
        self.buttons = {}
        for kind in ('minimize', 'maximize', 'close'):
            button = _WindowButton(kind, self)
            button.clicked.connect(lambda _checked=False, action=kind: self.perform(action))
            row.addWidget(button)
            self.buttons[kind] = button
        for child in (icon, self.label):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._drag = None

    def setText(self, title):
        self.label.setText(title)
        self.label.setToolTip(title)

    def text(self):
        return self.label.text()

    def perform(self, action):
        window = self.window()
        if action == 'close':
            window.close()
        elif action == 'minimize':
            window.showMinimized()
        elif window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if not handle or not handle.startSystemMove():
                self._drag = event.globalPosition().toPoint() - self.window().pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event):
        self._drag = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.perform('maximize')


def window_bar(title, *, brand=False):
    return WindowBar(title, brand=brand)


def bunny_frame(step=0, state='idle', elapsed=0.0):
    return _bunny_frame(step, state, state.startswith('idle') and elapsed % 3.5 > 3.25,
                        int(elapsed * 10) % 2, int(elapsed * 8) % 2)


def event_badge(frame, state, elapsed):
    """An observable fallback for a custom character without an event clip."""
    output = frame.copy()
    painter = QPainter(output)
    painter.scale(output.width() / 32, output.height() / 32)
    ink, white = QColor('#53406f'), QColor('#ffffff')
    accent = QColor({'notify': '#ed91bd', 'success': '#7bc6a7', 'error': '#e77887', 'talk': '#6fc4bd'}[state])
    y = 1 if int(elapsed * 4) % 2 else 3
    painter.fillRect(20, y, 12, 12, ink)
    painter.fillRect(21, y + 1, 10, 10, accent)
    if state == 'talk':
        phase = int(elapsed * 6) % 4
        for column in range(4):
            height = 2 + ((phase + column * 2) % 4) * 2
            painter.fillRect(22 + column * 2, y + 10 - height, 1, height, white)
    elif state == 'success':
        for x, dy in [(22, 5), (23, 6), (24, 7), (25, 6), (26, 5), (27, 4), (28, 3)]:
            painter.fillRect(x, y + dy, 1, 2, white)
    else:
        painter.fillRect(25, y + 2, 2, 5, white)
        painter.fillRect(25, y + 9, 2, 2, white)
    painter.end()
    return output


@lru_cache(maxsize=128)
def _bunny_frame(step, state, blink, arm_phase, talk_phase):
    """Cache the finite set of pixel poses rather than painting on every tick."""
    image = QImage(32, 32, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    def r(x, y, w, h, color):
        p.fillRect(x, y, w, h, QColor(color))
    ink, fur, light, pink = '#53406f', '#c4a8ed', '#f5e6ff', '#f2a9d0'
    # Stepped silhouette and upright ears.
    for x in (7, 20):
        lean = (-2 if x == 7 else 2) if state == 'fall' else 0
        r(x + 1 + lean, 1, 4, 1, ink); r(x + lean, 2, 6, 11, ink)
        r(x + 1 + lean, 2, 4, 10, fur); r(x + 2 + lean, 4, 2, 6, pink)
    r(7, 10, 18, 1, ink); r(4, 11, 24, 2, ink)
    r(3, 13, 26, 10, ink); r(5, 23, 22, 2, ink)
    r(7, 25, 18, 5, ink)
    r(5, 13, 22, 9, fur); r(7, 12, 18, 12, fur)
    r(7, 13, 3, 2, light); r(9, 25, 14, 4, fur)
    r(12, 25, 8, 4, light)
    if blink or state == 'land':
        r(8, 19, 4, 1, ink); r(20, 19, 4, 1, ink)
    else:
        r(9, 17, 2, 3, ink); r(21, 17, 2, 3, ink)
    r(7, 21, 4, 2, pink); r(22, 21, 4, 2, pink)
    if state in ('fall', 'drag') or (state == 'talk' and talk_phase):
        r(15, 21, 3, 3, ink); r(16, 22, 1, 1, pink)
        # Small arms lift and alternate as the character falls.
        arm_y = 14 + arm_phase * 2
        r(1, arm_y, 3, 5, ink); r(2, arm_y + 1, 2, 3, fur)
        r(28, arm_y, 3, 5, ink); r(28, arm_y + 1, 2, 3, fur)
    else:
        r(14, 21, 1, 1, ink); r(17, 21, 1, 1, ink); r(15, 22, 2, 1, ink)
    r(7 + step, 29, 7, 2, ink); r(19 - step, 29, 7, 2, ink)
    r(8 + step, 29, 5, 1, pink); r(20 - step, 29, 5, 1, pink)
    if state in ('notify', 'success', 'error'):
        accent = {'notify': '#ed91bd', 'success': '#7bc6a7', 'error': '#e77887'}[state]
        r(24, 1, 7, 7, ink); r(25, 2, 5, 5, accent)
        if state == 'success':
            r(25, 4, 1, 1, light); r(26, 5, 1, 1, light); r(27, 4, 1, 1, light); r(28, 3, 1, 1, light)
        else:
            r(27, 2, 1, 3, light); r(27, 6, 1, 1, light)
    p.end()
    return QPixmap.fromImage(image)


class Preview(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setMinimumHeight(154)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme_color('#e4d6f4')))
        # Desktop grid, with a tiny pixel meadow at the bottom.
        p.setPen(QColor(theme_color('#d4c3e9')))
        for x in range(0, self.width(), 16):
            p.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 16):
            p.drawLine(0, y, self.width(), y)
        p.fillRect(0, self.height() - 16, self.width(), 16, QColor(theme_color('#b8dcd9')))
        p.fillRect(0, self.height() - 18, self.width(), 2, QColor(theme_color('#739caa')))
        pet = getattr(self.controller, 'pet', None)
        frame = bunny_frame()
        if pet:
            frame = pet.current_frame()
        scaled = frame.scaled(112, 112, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
        p.drawPixmap((self.width() - scaled.width()) // 2, self.height() - 18 - scaled.height(), scaled)
        p.setPen(QColor(theme_color('#8d70ad')))
        p.drawText(12, 22, '+')
        p.drawText(self.width() - 28, 44, '+')


def panel(title):
    widget = QWidget()
    widget.setObjectName('panel')
    outer = QVBoxLayout(widget)
    outer.setContentsMargins(2, 2, 2, 2)
    outer.setSpacing(0)
    header = QWidget()
    header.setObjectName('panelHeader')
    header_layout = QHBoxLayout(header)
    header_layout.setContentsMargins(8, 0, 8, 0)
    icon = QLabel()
    icon.setProperty('pixelGlyph', 'folder')
    icon.setPixmap(pixel_icon('folder').pixmap(14, 14))
    header_layout.addWidget(icon)
    label = QLabel(title)
    label.setObjectName('section')
    header_layout.addWidget(label, 1)
    outer.addWidget(header)
    body = QWidget()
    body.setObjectName('panelBody')
    layout = QVBoxLayout(body)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(11)
    outer.addWidget(body)
    return widget, layout
