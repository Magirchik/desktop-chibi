"""Windows desktop integration, using the GUI event loop and native Win32 APIs."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
from PySide6.QtCore import QObject, QAbstractNativeEventFilter, Signal, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

DEFAULT_KEYS = {'visibility': 'Ctrl+Alt+Shift+F9', 'input': 'Ctrl+Alt+Shift+F10'}


def hotkey_combination(text):
    sequence = QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)
    if sequence.count() != 1 or sequence.isEmpty():
        raise ValueError('Нужно одно сочетание для каждого действия.')
    combination = sequence[0]
    modifiers = combination.keyboardModifiers()
    mask = 0
    for flag, value in ((Qt.KeyboardModifier.AltModifier, 1), (Qt.KeyboardModifier.ControlModifier, 2),
                        (Qt.KeyboardModifier.ShiftModifier, 4), (Qt.KeyboardModifier.MetaModifier, 8)):
        if modifiers & flag:
            mask |= value
    if not mask:
        raise ValueError('Добавьте Ctrl, Alt, Shift или Win к сочетанию.')
    key = combination.key()
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
        vk = 0x70 + key - Qt.Key.Key_F1
    elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9 or Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
        vk = int(key)
    else:
        vk = {Qt.Key.Key_Space: 0x20, Qt.Key.Key_Home: 0x24, Qt.Key.Key_End: 0x23,
              Qt.Key.Key_Insert: 0x2d, Qt.Key.Key_Delete: 0x2e, Qt.Key.Key_PageUp: 0x21,
              Qt.Key.Key_PageDown: 0x22, Qt.Key.Key_Pause: 0x13}.get(key)
    if vk is None:
        raise ValueError('Используйте букву, цифру, F1–F24, Space, Home, End, Insert, Delete или PageUp / PageDown.')
    return mask | 0x4000, int(vk)  # MOD_NOREPEAT: a held key activates once.


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type, message):
        if event_type in (b'windows_generic_MSG', b'windows_dispatcher_MSG'):
            msg = W.MSG.from_address(int(message))
            if msg.message == 0x0312 and self.owner.activate(int(msg.wParam)):
                return True, 0
        return False, 0


class WindowsShortcuts(QObject):
    packet = Signal(dict)

    def __init__(self, parent=None, api=None):
        super().__init__(parent)
        self.api = api or C.WinDLL('user32', use_last_error=True)
        if api is None:
            self.api.RegisterHotKey.argtypes = [W.HWND, C.c_int, W.UINT, W.UINT]
            self.api.RegisterHotKey.restype = W.BOOL
            self.api.UnregisterHotKey.argtypes = [W.HWND, C.c_int]
            self.api.UnregisterHotKey.restype = W.BOOL
        self.filter = _HotkeyFilter(self)
        self.keys = {}
        self.registered = {}
        self.installed = False

    def activate(self, identity):
        action = self.registered.get(identity)
        if action:
            self.packet.emit({'action': action})
            return True
        return False

    def rebind(self, keys):
        if set(keys) != set(DEFAULT_KEYS):
            raise ValueError('Нужны сочетания для показа и кликов.')
        bindings = {action: hotkey_combination(value) for action, value in keys.items()}
        if len(set(bindings.values())) != 2:
            raise ValueError('Действиям нужны разные сочетания.')
        previous = dict(self.keys)
        self._unregister()
        try:
            for offset, (action, (modifiers, vk)) in enumerate(bindings.items()):
                identity = 0x4340 + offset
                if not self.api.RegisterHotKey(None, identity, modifiers, vk):
                    raise ValueError(f'Сочетание {keys[action]} уже занято другой программой.')
                self.registered[identity] = action
        except ValueError:
            self._unregister()
            for offset, (action, text) in enumerate(previous.items()):
                identity = 0x4340 + offset
                if self.api.RegisterHotKey(None, identity, *hotkey_combination(text)):
                    self.registered[identity] = action
            raise
        self.keys = dict(keys)
        if not self.installed:
            QApplication.instance().installNativeEventFilter(self.filter)
            self.installed = True
        self.packet.emit({'status': 'ready', 'shortcuts': self.keys})

    def _unregister(self):
        for identity in self.registered:
            self.api.UnregisterHotKey(None, identity)
        self.registered.clear()

    def stop(self):
        self._unregister()
        if self.installed:
            QApplication.instance().removeNativeEventFilter(self.filter)
        self.installed = False
        self.keys.clear()


class WindowsApplications(QObject):
    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.user32 = C.WinDLL('user32', use_last_error=True)
        self.kernel32 = C.WinDLL('kernel32', use_last_error=True)
        self.hook = None
        self.callback_type = C.WINFUNCTYPE(None, W.HANDLE, W.DWORD, W.HWND, W.LONG, W.LONG, W.DWORD, W.DWORD)
        self.callback = self.callback_type(lambda *_: self.changed.emit(self.active()))
        signatures = [
            (self.user32.GetForegroundWindow, [], W.HWND),
            (self.user32.GetWindowThreadProcessId, [W.HWND, C.POINTER(W.DWORD)], W.DWORD),
            (self.user32.SetWinEventHook, [W.DWORD, W.DWORD, W.HMODULE, self.callback_type, W.DWORD, W.DWORD, W.DWORD], W.HANDLE),
            (self.user32.UnhookWinEvent, [W.HANDLE], W.BOOL),
            (self.kernel32.OpenProcess, [W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            (self.kernel32.QueryFullProcessImageNameW, [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)], W.BOOL),
            (self.kernel32.CloseHandle, [W.HANDLE], W.BOOL),
            (self.user32.IsWindowVisible, [W.HWND], W.BOOL),
            (self.user32.GetWindowTextLengthW, [W.HWND], C.c_int),
            (self.user32.GetWindowTextW, [W.HWND, W.LPWSTR, C.c_int], C.c_int),
        ]
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result

    def executable(self, window):
        pid = W.DWORD()
        self.user32.GetWindowThreadProcessId(window, C.byref(pid))
        process = self.kernel32.OpenProcess(0x1000, False, pid.value)
        if not process:
            return ''
        try:
            text = C.create_unicode_buffer(32768)
            size = W.DWORD(len(text))
            if self.kernel32.QueryFullProcessImageNameW(process, 0, text, C.byref(size)):
                return Path(text.value).stem.casefold()
            return ''
        finally:
            self.kernel32.CloseHandle(process)

    def active(self):
        return self.executable(self.user32.GetForegroundWindow())

    def start(self):
        self.hook = self.user32.SetWinEventHook(3, 3, None, self.callback, 0, 0, 0)  # EVENT_SYSTEM_FOREGROUND
        if not self.hook:
            raise OSError('Не удалось подключить события активного окна Windows.')
        self.changed.emit(self.active())

    def applications(self):
        result = {}
        callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        def visit(window, _):
            if self.user32.IsWindowVisible(window) and self.user32.GetWindowTextLengthW(window):
                identity = self.executable(window)
                if identity:
                    result[identity] = {'id': identity, 'name': identity}
            return True
        self.user32.EnumWindows.argtypes = [callback_type, W.LPARAM]
        self.user32.EnumWindows.restype = W.BOOL
        self.user32.EnumWindows(callback_type(visit), 0)
        return sorted(result.values(), key=lambda item: item['id'])

    def stop(self):
        if self.hook:
            self.user32.UnhookWinEvent(self.hook)
            self.hook = None


def configure_shortcuts(keys, parent):
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QKeySequenceEdit, QLabel, QVBoxLayout
    from .pixel_ui import apply_theme, window_bar
    dialog = QDialog(parent)
    apply_theme(dialog)
    dialog.setWindowTitle('Горячие клавиши Windows')
    layout = QVBoxLayout(dialog)
    layout.addWidget(window_bar('Горячие клавиши'))
    form = QFormLayout()
    fields = {}
    for action, label in [('visibility', 'Скрыть / вернуть всех'), ('input', 'Переключить клики')]:
        field = QKeySequenceEdit(QKeySequence(keys[action]))
        field.setMaximumSequenceLength(1)
        fields[action] = field
        form.addRow(label, field)
    layout.addLayout(form)
    note = QLabel('Выберите по одному сочетанию с Ctrl, Alt, Shift или Win. Занятые сочетания будут отклонены.')
    note.setWordWrap(True)
    layout.addWidget(note)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    try:
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return {action: field.keySequence().toString(QKeySequence.SequenceFormat.PortableText) for action, field in fields.items()}
        return None
    finally:
        dialog.deleteLater()
