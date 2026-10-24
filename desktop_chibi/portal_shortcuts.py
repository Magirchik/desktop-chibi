"""In-process XDG GlobalShortcuts client, driven by Qt socket notifications.

PySide cannot marshal the portal's array of structs through QDBusArgument.
Use libdbus (already used by QtDBus) for this connection, with QSocketNotifier
for nonblocking I/O. No child process, extra Python dependency, or polling.
"""
import ctypes as C
import uuid

from PySide6.QtCore import QObject, QSocketNotifier, QTimer, Signal

SERVICE = 'org.freedesktop.portal.Desktop'
PATH = '/org/freedesktop/portal/desktop'
INTERFACE = 'org.freedesktop.portal.GlobalShortcuts'
ACTIONS = [
    ('visibility', {'description': ('s', 'Скрыть / вернуть всех чибиков'),
                    'preferred_trigger': ('s', 'CTRL+ALT+SHIFT+F9')}),
    ('input', {'description': ('s', 'Переключить пропуск кликов всех чибиков'),
               'preferred_trigger': ('s', 'CTRL+ALT+SHIFT+F10')}),
]


class _Iter(C.Structure):
    # Public ABI layout of DBusMessageIter; fields are never accessed directly.
    _fields_ = [('dummy1', C.c_void_p), ('dummy2', C.c_void_p), ('dummy3', C.c_uint32),
                *[(f'dummy{i}', C.c_int) for i in range(4, 12)],
                ('pad1', C.c_int), ('pad2', C.c_void_p), ('pad3', C.c_void_p)]


def _library():
    lib = C.CDLL('libdbus-1.so.3')
    ptr, string, integer = C.c_void_p, C.c_char_p, C.c_int
    functions = {
        'threads_init_default': (integer, []),
        'bus_get_private': (ptr, [integer, ptr]),
        'bus_get_unique_name': (string, [ptr]),
        'bus_add_match': (None, [ptr, string, ptr]),
        'connection_set_exit_on_disconnect': (None, [ptr, integer]),
        'connection_get_unix_fd': (integer, [ptr, C.POINTER(integer)]),
        'connection_read_write': (integer, [ptr, integer]),
        'connection_has_messages_to_send': (integer, [ptr]),
        'connection_pop_message': (ptr, [ptr]),
        'connection_send': (integer, [ptr, ptr, C.POINTER(C.c_uint32)]),
        'connection_close': (None, [ptr]),
        'connection_unref': (None, [ptr]),
        'message_new_method_call': (ptr, [string, string, string, string]),
        'message_unref': (None, [ptr]),
        'message_get_type': (integer, [ptr]),
        'message_get_reply_serial': (C.c_uint32, [ptr]),
        'message_get_path': (string, [ptr]),
        'message_get_interface': (string, [ptr]),
        'message_get_member': (string, [ptr]),
        'message_get_error_name': (string, [ptr]),
        'message_iter_init': (integer, [ptr, ptr]),
        'message_iter_init_append': (None, [ptr, ptr]),
        'message_iter_append_basic': (integer, [ptr, integer, ptr]),
        'message_iter_open_container': (integer, [ptr, integer, string, ptr]),
        'message_iter_close_container': (integer, [ptr, ptr]),
        'message_iter_get_arg_type': (integer, [ptr]),
        'message_iter_get_element_type': (integer, [ptr]),
        'message_iter_get_basic': (None, [ptr, ptr]),
        'message_iter_recurse': (None, [ptr, ptr]),
        'message_iter_next': (integer, [ptr]),
    }
    for name, (restype, argtypes) in functions.items():
        function = getattr(lib, 'dbus_' + name)
        function.restype, function.argtypes = restype, argtypes
    lib.dbus_threads_init_default()
    return lib


def _append(lib, iterator, signature, value):
    basic = {'s': C.c_char_p, 'o': C.c_char_p, 'u': C.c_uint32}
    if signature in basic:
        item = basic[signature](value.encode() if signature in ('s', 'o') else value)
        if not lib.dbus_message_iter_append_basic(C.byref(iterator), ord(signature), C.byref(item)):
            raise MemoryError('D-Bus append failed')
        return
    if signature == 'a{sv}':
        kind, contained = 'a', '{sv}'
        children = [('{sv}', pair) for pair in value.items()]
    elif signature == '{sv}':
        kind, contained = 'e', None
        children = [('s', value[0]), ('v', value[1])]
    elif signature == 'a(sa{sv})':
        kind, contained = 'a', '(sa{sv})'
        children = [('(sa{sv})', pair) for pair in value]
    elif signature == '(sa{sv})':
        kind, contained = 'r', None
        children = [('s', value[0]), ('a{sv}', value[1])]
    elif signature == 'v':
        kind, contained = 'v', value[0]
        children = [value]
    else:
        raise ValueError('Unsupported portal argument: ' + signature)
    child = _Iter()
    if not lib.dbus_message_iter_open_container(C.byref(iterator), ord(kind),
                                               contained.encode() if contained else None, C.byref(child)):
        raise MemoryError('D-Bus container allocation failed')
    for child_signature, child_value in children:
        _append(lib, child, child_signature, child_value)
    if not lib.dbus_message_iter_close_container(C.byref(iterator), C.byref(child)):
        raise MemoryError('D-Bus container close failed')


def _read(lib, iterator):
    kind = lib.dbus_message_iter_get_arg_type(C.byref(iterator))
    basic = {'s': C.c_char_p, 'o': C.c_char_p, 'g': C.c_char_p,
             'u': C.c_uint32, 't': C.c_uint64, 'b': C.c_uint32, 'i': C.c_int32}
    if chr(kind) in basic:
        value = basic[chr(kind)]()
        lib.dbus_message_iter_get_basic(C.byref(iterator), C.byref(value))
        return value.value.decode() if kind in (ord('s'), ord('o'), ord('g')) else value.value
    if chr(kind) not in ('a', 'r', 'e', 'v'):
        raise ValueError('Unsupported portal response type: ' + chr(kind))
    child = _Iter()
    lib.dbus_message_iter_recurse(C.byref(iterator), C.byref(child))
    values = []
    while lib.dbus_message_iter_get_arg_type(C.byref(child)):
        values.append(_read(lib, child))
        lib.dbus_message_iter_next(C.byref(child))
    if kind == ord('v'):
        return values[0]
    if kind == ord('a') and lib.dbus_message_iter_get_element_type(C.byref(iterator)) == ord('e'):
        return dict(values)
    return values


class _Bus(QObject):
    received = Signal(str, str, str, object)
    disconnected = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.lib = _library()
        self.connection = self.lib.dbus_bus_get_private(0, None)  # session bus
        if not self.connection:
            raise OSError('Не удалось подключиться к пользовательской D-Bus шине.')
        self.pending = {}
        self.lib.dbus_connection_set_exit_on_disconnect(self.connection, False)
        descriptor = C.c_int()
        if not self.lib.dbus_connection_get_unix_fd(self.connection, C.byref(descriptor)):
            self.close()
            raise OSError('D-Bus connection has no socket')
        self.unique_name = self.lib.dbus_bus_get_unique_name(self.connection).decode()
        self.reader = QSocketNotifier(descriptor.value, QSocketNotifier.Type.Read, self)
        self.writer = QSocketNotifier(descriptor.value, QSocketNotifier.Type.Write, self)
        self.writer.setEnabled(False)
        self.reader.activated.connect(self.pump)
        self.writer.activated.connect(self.pump)
        for match in [f"type='signal',sender='{SERVICE}'",
                      f"type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='{SERVICE}'"]:
            self.lib.dbus_bus_add_match(self.connection, match.encode(), None)
        self.pump()

    def call(self, path, interface, method, arguments, callback=None):
        if not self.connection:
            return
        message = self.lib.dbus_message_new_method_call(SERVICE.encode(), path.encode(), interface.encode(), method.encode())
        if not message:
            raise MemoryError('D-Bus message allocation failed')
        try:
            iterator = _Iter()
            self.lib.dbus_message_iter_init_append(message, C.byref(iterator))
            for signature, value in arguments:
                _append(self.lib, iterator, signature, value)
            serial = C.c_uint32()
            if not self.lib.dbus_connection_send(self.connection, message, C.byref(serial)):
                raise MemoryError('D-Bus send failed')
        finally:
            self.lib.dbus_message_unref(message)
        if callback:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self.complete(serial.value, [], 'D-Bus request timed out'))
            timer.start(5000)
            self.pending[serial.value] = (callback, timer)
        self.pump()

    def complete(self, serial, arguments, error=None):
        pending = self.pending.pop(serial, None)
        if pending:
            callback, timer = pending
            timer.stop()
            timer.deleteLater()
            callback(arguments, error)

    def pump(self, *_):
        if not self.connection:
            return
        if not self.lib.dbus_connection_read_write(self.connection, 0):
            self.close()
            self.disconnected.emit()
            return
        self.writer.setEnabled(bool(self.lib.dbus_connection_has_messages_to_send(self.connection)))
        while self.connection:
            message = self.lib.dbus_connection_pop_message(self.connection)
            if not message:
                break
            try:
                iterator, arguments = _Iter(), []
                self.lib.dbus_message_iter_init(message, C.byref(iterator))
                while self.lib.dbus_message_iter_get_arg_type(C.byref(iterator)):
                    arguments.append(_read(self.lib, iterator))
                    self.lib.dbus_message_iter_next(C.byref(iterator))
                kind = self.lib.dbus_message_get_type(message)
                if kind in (2, 3):
                    error = self.lib.dbus_message_get_error_name(message) if kind == 3 else None
                    self.complete(self.lib.dbus_message_get_reply_serial(message), arguments,
                                  (error.decode() + ': ' + str(arguments)) if error else None)
                elif kind == 4:
                    text = lambda function: (function(message) or b'').decode()
                    self.received.emit(text(self.lib.dbus_message_get_path),
                                       text(self.lib.dbus_message_get_interface),
                                       text(self.lib.dbus_message_get_member), arguments)
            except (ValueError, IndexError) as error:
                self.complete(self.lib.dbus_message_get_reply_serial(message), [], str(error))
            finally:
                self.lib.dbus_message_unref(message)

    def close(self):
        for name in ('reader', 'writer'):
            notifier = getattr(self, name, None)
            if notifier:
                notifier.setEnabled(False)
        for _callback, timer in self.pending.values():
            timer.stop()
            timer.deleteLater()
        self.pending.clear()
        if self.connection:
            self.lib.dbus_connection_close(self.connection)
            self.lib.dbus_connection_unref(self.connection)
            self.connection = None


class PortalShortcuts(QObject):
    packet = Signal(dict)

    def __init__(self, parent=None, window=''):
        super().__init__(parent)
        self.window = window
        self.bus = None
        self.session = None
        self.requests = {}
        self.down = set()
        self.stopped = False

    def start(self):
        try:
            self.bus = _Bus(self)
            self.bus.received.connect(self.receive)
            self.bus.disconnected.connect(lambda: self.fail('Соединение с порталом хоткеев закрыто.'))
            self.bus.call(PATH, 'org.freedesktop.host.portal.Registry', 'Register',
                          [('s', 'desktop-chibi'), ('a{sv}', {})], self.registered)
        except (OSError, AttributeError, MemoryError) as error:
            self.fail(str(error))

    def fail(self, message):
        if self.stopped:
            return
        self.stop()
        self.packet.emit({'status': 'error', 'message': message})

    def registered(self, _arguments, error):
        if error:
            self.fail(error)
            return
        self.request('CreateSession', [('a{sv}', {'session_handle_token': ('s', 'chibi_' + uuid.uuid4().hex)})], self.created)

    def request(self, method, arguments, callback):
        token = 'chibi_' + uuid.uuid4().hex
        arguments[-1][1]['handle_token'] = ('s', token)
        sender = self.bus.unique_name.lstrip(':').replace('.', '_')
        path = f'/org/freedesktop/portal/desktop/request/{sender}/{token}'
        self.requests[path] = callback

        def called(values, error):
            if error:
                self.fail(error)
            elif values and values[0] != path and path in self.requests:
                self.requests[values[0]] = self.requests.pop(path)
        self.bus.call(PATH, INTERFACE, method, arguments, called)

    def created(self, values):
        self.session = values.get('session_handle')
        if not isinstance(self.session, str) or not self.session.startswith('/'):
            self.fail('Портал не вернул сессию хоткеев.')
            return
        self.packet.emit({'status': 'configuring', 'message': 'Разрешите хоткеи в системном диалоге GNOME.'})
        self.request('BindShortcuts', [('o', self.session), ('a(sa{sv})', ACTIONS),
                                       ('s', self.window), ('a{sv}', {})], self.bound)

    def bound(self, values):
        shortcuts = {identity: properties.get('trigger_description', '')
                     for identity, properties in values.get('shortcuts', [])}
        # An empty set after revocation updates status without ending the session:
        # the user can reassign shortcuts in Settings and receive another update.
        self.packet.emit({'status': 'ready', 'shortcuts': shortcuts})

    def receive(self, path, interface, name, arguments):
        if self.stopped:
            return
        if interface == 'org.freedesktop.portal.Request' and name == 'Response':
            callback = self.requests.pop(path, None)
            if callback and len(arguments) == 2:
                if arguments[0] == 0:
                    callback(arguments[1])
                else:
                    self.fail('Настройка хоткеев отменена или не разрешена системой.')
        elif interface == 'org.freedesktop.portal.Session' and path == self.session and name == 'Closed':
            self.fail('Системная сессия хоткеев закрыта. Включите хоткеи заново.')
        elif interface == 'org.freedesktop.DBus' and name == 'NameOwnerChanged':
            if len(arguments) == 3 and arguments[0] == SERVICE and arguments[1]:
                self.fail('Системный портал перезапущен. Включите хоткеи заново.')
        elif interface == INTERFACE and path == PATH and arguments and arguments[0] == self.session:
            if name == 'ShortcutsChanged':
                self.bound({'shortcuts': arguments[1]})
            elif name in ('Activated', 'Deactivated') and arguments[1] in ('visibility', 'input'):
                identity = arguments[1]
                if name == 'Deactivated':
                    self.down.discard(identity)
                elif identity not in self.down:
                    self.down.add(identity)
                    self.packet.emit({'action': identity})

    def stop(self):
        self.stopped = True
        self.requests.clear()
        self.down.clear()
        if self.bus:
            # Closing this private connection also destroys all portal requests
            # and sessions it owns, including a pending permission dialog.
            self.bus.close()
