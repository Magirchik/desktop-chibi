"""Code-drawn pixel assets and a pastel desktop theme."""
from functools import lru_cache
from pathlib import Path
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QHBoxLayout, QVBoxLayout, QWidget, QTabWidget, QSizePolicy

THEME = '''
QWidget { color: #402d55; font-family: "DejaVu Sans Mono", monospace; font-size: 14px; border-radius: 0; }
QDialog { background: #dfcef2; }
QWidget#content { background: transparent; }
QWidget#desktop { background: #cbb2e8; }
QWidget#panel { background: #fff4fc; border: 2px solid #82629d; }
QWidget#panelHeader { background: #e1d0f1; border: none; border-bottom: 2px solid #b69acb; }
QWidget#panelBody { background: #fff4fc; border: none; }
QWidget#titlebar { background: #8161a7; border: 2px solid #59406f; border-top-color: #f7e7ff; border-left-color: #f7e7ff; }
QLabel#windowTitle { color: #fff6ff; font-size: 14px; font-weight: bold; background: transparent; }
QLabel#brandTitle { color: #fff6ff; font-family: "Pixelify Sans", "DejaVu Sans Mono", monospace;
 font-size: 18px; background: transparent; }
QLabel { background: transparent; }
QLabel#bar { background: #7961aa; color: #ffffff;
 font-size: 14px; font-weight: bold; padding: 9px 10px;
 border: 2px solid #53406f; border-top-color: #cdbcf1; border-left-color: #cdbcf1; }
QLabel#hero { color: #65417e; font-size: 22px; font-weight: bold; }
QLabel#muted { color: #705b7e; font-size: 13px; }
QLabel#hint { color: #655073; font-size: 13px; background: #f9f2fc;
 padding: 9px 11px; border-left: 3px solid #b48acb; }
QLabel#section { color: #5f4178; font-size: 14px; font-weight: bold; padding: 7px 10px; }
QLabel#status { color: #27604f; background: #d8f3ec; padding: 7px; border: 2px solid #7fa9a2; }
QLabel#status[tone="waiting"] { color: #744154; background: #ffe3e9; border-color: #d69eb1; }
QLabel#status[tone="hidden"] { color: #655074; background: #e6dcf0; border-color: #ad97c2; }
QLabel#caption { color: #74456e; font-size: 14px; font-weight: bold; padding-top: 9px; padding-bottom: 5px; border-bottom: 1px solid #dec5df; }
QLabel#value { background: #e6daf5; color: #533770; padding: 4px; font-weight: bold; }
QLabel#error { color: #a34165; padding: 4px; }
QLabel#animationPreview { background: transparent; border: 2px solid #7961aa; border-bottom-color: #ffffff; border-right-color: #ffffff; }
QPushButton { background: #eee5fa; border: 2px solid #70588f; border-top-color: #ffffff;
 border-left-color: #ffffff; padding: 8px 11px; font-weight: normal; min-height: 18px; }
QPushButton:hover { background: #ffdaef; color: #873e7b; }
QPushButton:pressed, QPushButton:checked { border-top-color: #70588f; border-left-color: #70588f;
 border-bottom-color: #ffffff; border-right-color: #ffffff; background: #d9c6ef; }
QPushButton:checked { background: #f3bfdd; color: #59316d; }
QPushButton#primary { background: #f6b9dc; color: #59316d; border-right-color: #9e587f; border-bottom-color: #9e587f; font-weight: bold; }
QPushButton#primary:hover { background: #ffc9e7; }
QPushButton#primary:pressed { background: #e9a3cf; }
QPushButton:disabled { color: #a08cae; background: #e7def1; }
QPushButton:focus, QComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus { outline: none; border: 2px solid #b05ca5; }
QComboBox { background: #ffffff; border: 2px solid #927cb0; padding: 7px; min-height: 18px; }
QComboBox::drop-down { width: 24px; border-left: 2px solid #927cb0; background: #dfd0f1; }
QComboBox::down-arrow { image: url("@ASSETS@/arrow-down.svg"); width: 10px; height: 6px; }
QPushButton::menu-indicator { image: url("@ASSETS@/arrow-down.svg"); width: 10px; height: 6px; subcontrol-position: right center; right: 5px; }
QComboBox QAbstractItemView { background: #fff4fc; selection-background-color: #e6b5e6; selection-color: #382b61; }
QSpinBox, QDoubleSpinBox { background: #fff4fc; border: 2px solid #927cb0; padding: 5px; min-height: 20px; }
QSpinBox, QDoubleSpinBox, QLabel#value { font-family: "DejaVu Sans Mono", monospace; font-size: 14px; }
QSpinBox:disabled { background: #e1d7ec; color: #9685aa; }
QLineEdit, QPlainTextEdit { background: #fff4fc; border: 2px solid #927cb0; padding: 7px; }
QListWidget { background: #fff4fc; border: 2px solid #927cb0; padding: 4px; }
QListWidget::item { padding: 4px; }
QListWidget::item:selected { background: #d7b8e9; color: #382b61; }
QTabWidget::pane { border: 2px solid #927cb0; background: #fff4fc; top: -1px; }
QTabBar::tab { background: #dcd0ed; border: 2px solid #927cb0; border-top-color: #ffffff;
 border-left-color: #ffffff; padding: 8px 10px; }
QTabBar::tab:selected { background: #f6c4e1; color: #59316d; border-bottom-color: #f6c4e1; }
QTabBar::tab:hover { background: #ffdaef; }
QSlider { background: transparent; min-height: 26px; }
QSlider::groove:horizontal { height: 8px; background: #d9ccea; border: 1px solid #9c86b6; }
QSlider::sub-page:horizontal { background: #b39bdb; border: 1px solid #9c86b6; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; background: #f5b8dc;
 border: 2px solid #72548d; border-top-color: #ffffff; border-left-color: #ffffff; }
QCheckBox { spacing: 9px; padding: 5px 0; }
QCheckBox::indicator { width: 15px; height: 15px; background: #ffffff; border: 2px solid #947cad; }
QCheckBox::indicator:checked { background: #f9d6ec; border: 2px solid #947cad; image: url("@ASSETS@/check.svg"); }
QCheckBox::indicator:disabled { background: #e1d7ec; border-color: #b1a1c3; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: #ded1ef; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #ad95cc; min-height: 24px; border: 2px solid #eee5fa; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QMenu { background: #fff4fc; border: 2px solid #7961aa; padding: 4px; }
QMenu::item { padding: 8px 18px; }
QMenu::item:selected { background: #ead0f0; }
QMenu::item:disabled { color: #a08cae; }
QToolTip { background: #fff4fc; color: #382b61; border: 1px solid #7961aa; padding: 6px; font-size: 14px; }
'''


@lru_cache(maxsize=1)
def _load_pixel_font():
    return QFontDatabase.addApplicationFont(str(Path(__file__).parent / 'assets/fonts/PixelifySans.ttf'))


def apply_theme(widget):
    _load_pixel_font()
    font = QFont('DejaVu Sans Mono')
    font.setPixelSize(14)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    widget.setFont(font)
    widget.setStyleSheet(THEME.replace('@ASSETS@', (Path(__file__).parent / 'assets').as_posix()))


class SystemHeading(QLabel):
    """Small retro desktop heading with a one-pixel highlight."""
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName('hero')
        self.setContentsMargins(0, 0, 2, 2)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(QColor('#fff7ff'))
        painter.drawText(self.contentsRect().translated(1, 1), self.alignment(), self.text())
        painter.setPen(self.palette().windowText().color())
        painter.drawText(self.contentsRect(), self.alignment(), self.text())


@lru_cache(maxsize=2)
def _tile(checker=False):
    image = QPixmap(24, 24)
    image.fill(QColor('#fbf2fc' if checker else '#f4ecfa'))
    painter = QPainter(image)
    if checker:
        painter.fillRect(0, 0, 12, 12, QColor('#e9daf2'))
        painter.fillRect(12, 12, 12, 12, QColor('#e9daf2'))
    else:
        painter.setPen(QColor('#e8dff1'))
        painter.drawLine(0, 0, 23, 0)
        painter.drawLine(0, 0, 0, 23)
        painter.fillRect(0, 0, 2, 2, QColor('#dac9e8'))
    painter.end()
    return image


class PixelSurface(QWidget):
    """A cached desktop grid, painted only when the widget needs repainting."""
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawTiledPixmap(event.rect(), _tile())


class PixelPreview(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('animationPreview')
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawTiledPixmap(event.rect(), _tile(True))
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
        self.currentChanged.connect(lambda *_: self.updateGeometry())

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


def window_bar(title, *, brand=False):
    bar = QWidget()
    bar.setObjectName('titlebar')
    row = QHBoxLayout(bar)
    row.setContentsMargins(8, 5, 8, 5)
    icon = QLabel()
    icon.setPixmap(bunny_frame().copy(2, 0, 28, 26).scaled(26, 26, Qt.AspectRatioMode.KeepAspectRatio,
                                                         Qt.TransformationMode.FastTransformation))
    row.addWidget(icon)
    label = QLabel(title)
    label.setObjectName('brandTitle' if brand else 'windowTitle')
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    label.setToolTip(title)
    row.addWidget(label, 1)
    row.addWidget(_Hearts())
    return bar


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
        p.fillRect(self.rect(), QColor('#e4d6f4'))
        # Desktop grid, with a tiny pixel meadow at the bottom.
        p.setPen(QColor('#d4c3e9'))
        for x in range(0, self.width(), 16):
            p.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 16):
            p.drawLine(0, y, self.width(), y)
        p.fillRect(0, self.height() - 16, self.width(), 16, QColor('#b8dcd9'))
        p.fillRect(0, self.height() - 18, self.width(), 2, QColor('#739caa'))
        pet = getattr(self.controller, 'pet', None)
        frame = bunny_frame()
        if pet:
            frame = pet.current_frame()
        scaled = frame.scaled(112, 112, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
        p.drawPixmap((self.width() - scaled.width()) // 2, self.height() - 18 - scaled.height(), scaled)
        p.setPen(QColor('#8d70ad'))
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
    header_layout.setContentsMargins(0, 0, 8, 0)
    label = QLabel(title)
    label.setObjectName('section')
    header_layout.addWidget(label, 1)
    mark = QLabel('◆')
    mark.setStyleSheet('color: #a177b0; background: transparent; border: none;')
    header_layout.addWidget(mark)
    outer.addWidget(header)
    body = QWidget()
    body.setObjectName('panelBody')
    layout = QVBoxLayout(body)
    layout.setContentsMargins(14, 12, 14, 14)
    layout.setSpacing(10)
    outer.addWidget(body)
    return widget, layout
