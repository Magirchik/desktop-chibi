"""Private fake-portal test; never opens a real system permission dialog.

dbus-run-session -- /usr/bin/python3 tests/portal_shortcuts_fixture.py
The server uses system Gio; its client uses only the app's Qt runtime.
"""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SERVICE = 'org.freedesktop.portal.Desktop'
PATH = '/org/freedesktop/portal/desktop'
INTERFACE = 'org.freedesktop.portal.GlobalShortcuts'


def client():
    sys.path.insert(0, str(ROOT))
    from PySide6.QtCore import QCoreApplication, QTimer
    from desktop_chibi.portal_shortcuts import PortalShortcuts
    app = QCoreApplication([])
    portal = PortalShortcuts()
    packets, failures = [], []

    def packet(value):
        packets.append(value)
        if value.get('status') == 'error':
            failures.append(value['message'])
            app.quit()
        if value.get('status') == 'ready' and not value['shortcuts']:
            portal.stop()
            assert not portal.bus.connection
            assert not portal.bus.pending
            assert not portal.bus.reader.isEnabled()
            assert not portal.bus.writer.isEnabled()
            app.quit()

    portal.packet.connect(packet)
    QTimer.singleShot(5000, app.quit)
    portal.start()
    app.exec()
    assert not failures, failures
    assert [p['action'] for p in packets if 'action' in p] == ['visibility', 'visibility', 'input'], packets
    ready = [p['shortcuts'] for p in packets if p.get('status') == 'ready']
    assert len(ready) == 3, packets
    assert ready[0]['input'] == 'Ctrl+Alt+Shift+F10'
    assert ready[1]['input'] == 'Ctrl+F8'
    assert ready[2] == {}
    print(json.dumps({'ok': True, 'events': len(packets)}))


def server():
    from gi.repository import Gio, GLib
    xml = '''<node>
    <interface name="org.freedesktop.host.portal.Registry">
    <method name="Register"><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/></method>
    </interface>
    <interface name="org.freedesktop.portal.GlobalShortcuts">
    <method name="CreateSession"><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
    <method name="BindShortcuts"><arg type="o" direction="in"/><arg type="a(sa{sv})" direction="in"/>
    <arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
    </interface>
    </node>'''
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                  'RequestName', GLib.Variant('(su)', (SERVICE, 0)), None,
                  Gio.DBusCallFlags.NONE, 1000, None)
    loop = GLib.MainLoop()
    calls, failures = [], []
    session = '/org/freedesktop/portal/desktop/session/test/session'

    def events(sender):
        # Ignore other sessions/actions; autorepeat must not toggle repeatedly.
        for identity, names in [('visibility', ['Activated', 'Activated', 'Deactivated', 'Activated']),
                                ('input', ['Activated']), ('unknown', ['Activated'])]:
            for name in names:
                bus.emit_signal(sender, PATH, INTERFACE, name,
                                GLib.Variant('(osta{sv})', (session, identity, 1, {})))
        bus.emit_signal(sender, PATH, INTERFACE, 'Activated',
                        GLib.Variant('(osta{sv})', (session + '_other', 'input', 1, {})))
        bus.emit_signal(sender, PATH, INTERFACE, 'ShortcutsChanged',
                        GLib.Variant('(oa(sa{sv}))', (session, [
                            ('input', {'trigger_description': GLib.Variant('s', 'Ctrl+F8')})])))
        bus.emit_signal(sender, PATH, INTERFACE, 'ShortcutsChanged',
                        GLib.Variant('(oa(sa{sv}))', (session, [])))
        return False

    def method(_bus, sender, _path, _interface, name, parameters, invocation):
        try:
            args = parameters.unpack()
            calls.append(name)
            if name == 'Register':
                assert args[0] == 'desktop-chibi'
                invocation.return_value(None)
                return
            path = ('/org/freedesktop/portal/desktop/request/' + sender.lstrip(':').replace('.', '_')
                    + '/' + args[-1]['handle_token'])
            if name == 'CreateSession':
                result = {'session_handle': GLib.Variant('s', session)}
            else:
                assert args[0] == session
                assert [item[0] for item in args[1]] == ['visibility', 'input']
                assert args[1][0][1]['preferred_trigger'] == 'CTRL+ALT+SHIFT+F9'
                result = {'shortcuts': GLib.Variant('a(sa{sv})', [
                    ('visibility', {'trigger_description': GLib.Variant('s', 'Ctrl+Alt+Shift+F9')}),
                    ('input', {'trigger_description': GLib.Variant('s', 'Ctrl+Alt+Shift+F10')})])}
            invocation.return_value(GLib.Variant('(o)', (path,)))

            def respond():
                bus.emit_signal(sender, path, 'org.freedesktop.portal.Request', 'Response',
                                GLib.Variant('(ua{sv})', (0, result)))
                if name == 'BindShortcuts':
                    GLib.timeout_add(50, lambda: events(sender))
                return False
            GLib.idle_add(respond)
        except Exception as error:
            failures.append(str(error))
            loop.quit()

    info = Gio.DBusNodeInfo.new_for_xml(xml)
    for interface in info.interfaces:
        bus.register_object(PATH, interface, method, None, None)
    process = subprocess.Popen([str(ROOT / '.venv/bin/python'), __file__, '--client'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def poll():
        if process.poll() is not None:
            loop.quit()
            return False
        return True
    GLib.timeout_add(20, poll)

    def timeout():
        failures.append('Portal client timed out')
        process.kill()
        loop.quit()
        return False
    GLib.timeout_add_seconds(8, timeout)
    loop.run()
    if process.poll() is None:
        process.kill()
    output, errors = process.communicate(timeout=2)
    assert process.returncode == 0, errors
    assert not failures, failures
    assert calls == ['Register', 'CreateSession', 'BindShortcuts'], calls
    assert json.loads(output)['ok'], output
    print('In-process portal: identity, binding, actions, rebinding, revocation and socket cleanup OK')


if __name__ == '__main__':
    client() if '--client' in sys.argv else server()
