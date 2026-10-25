"""Application appearance, persisted once and applied to all open editors."""
import re
import weakref
from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

THEMES = {'light': 'Light · Светлая', 'dark': 'Dark · Тёмная', 'midnight': 'Midnight · Ночная'}


def appearance():
    app = QApplication.instance()
    if not hasattr(app, '_chibi_appearance'):
        app._chibi_appearance = Appearance(app)
    return app._chibi_appearance


def theme_color(value, theme=None):
    theme = theme or appearance().theme
    if theme == 'light':
        return value
    groups = {
        'text': ('#493258', '#4c2c61', '#634076', '#573769'),
        'muted': ('#82658d', '#705481', '#80628e', '#775087'),
        'accent': ('#6e4386', '#ad4e87', '#914676', '#6c3964', '#743d72', '#8f477b', '#6b4085', '#774979'),
        'paper': ('#fff7fd', '#fff8fe', '#fff6fc', '#fff9fe', '#f8effb', '#fff5fc'),
        'field': ('#fffbff', '#fffaff'),
        'button': ('#f2e5fa', '#f7eafa', '#eddef7', '#e7d4f2', '#f8eafb'),
        'pink': ('#f9c9e5', '#f5c7e4', '#f5c0df', '#f6c7e6', '#ffd9ee', '#eeb5d9', '#ffd9ec'),
        'highlight': ('#ffffff',),
        'disabled': ('#b49ac1', '#c2adc9'),
        'success': ('#456e70', '#739caa'),
        'success_bg': ('#e6f9f6', '#b8dcd9'),
        'error': ('#ac3868', '#965578'),
    }
    colors = {'text': '#eaddf6', 'muted': '#bba6cd', 'accent': '#efafd8', 'paper': '#292133',
              'field': '#211b2b', 'button': '#41324f', 'pink': '#62405e', 'highlight': '#8b739f',
              'disabled': '#8f7c9e', 'success': '#a1d7cf', 'success_bg': '#293e40', 'error': '#ffa8c4'}
    if theme == 'midnight':
        colors.update(text='#dbe9ff', muted='#9faeca', accent='#b9c5ff', paper='#1c2433', field='#151c29',
                      button='#303d56', pink='#434b74', highlight='#6d84ab', success_bg='#243d46')
    for name, values in groups.items():
        if value.lower() in values:
            return colors[name]
    original = QColor(value)
    hue = original.hslHueF() if theme == 'dark' else .63
    saturation = .22 if theme == 'dark' else .28
    lightness = original.lightnessF()
    if lightness > .72:
        lightness = .17 + (.98 - lightness) * .65
    elif lightness < .4:
        lightness = .77
    else:
        lightness = .42 + (lightness - .4) * .45
    return QColor.fromHslF(max(0, hue), saturation, min(.9, max(.12, lightness))).name()


def themed_stylesheet(source, theme=None):
    return re.sub(r'#[0-9a-fA-F]{6}', lambda match: theme_color(match.group(), theme), source)


class Appearance(QObject):
    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        saved = QSettings('DesktopChibi', 'DesktopChibi').value('app_theme', 'light')
        self.theme = saved if saved in THEMES else 'light'
        self.windows = weakref.WeakSet()

    def register(self, window):
        self.windows.add(window)
        self.apply(window)

    def apply(self, window):
        from .pixel_ui import apply_theme_colors
        apply_theme_colors(window)

    def set_theme(self, theme):
        if theme not in THEMES:
            raise ValueError('Неизвестная тема приложения.')
        self.theme = theme
        QSettings('DesktopChibi', 'DesktopChibi').setValue('app_theme', theme)
        for window in tuple(self.windows):
            if isValid(window):
                self.apply(window)
        self.changed.emit(theme)
