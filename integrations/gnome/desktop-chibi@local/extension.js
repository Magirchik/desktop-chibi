import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE = `<node><interface name="org.gnome.Shell.Extensions.DesktopChibi">
  <method name="GetState"><arg type="s" direction="out"/></method>
  <method name="ListApplications"><arg type="s" direction="out"/></method>
  <signal name="ActiveAppChanged"><arg type="s"/></signal>
</interface></node>`;

export default class DesktopChibiControls extends Extension {
    enable() {
        this._active = '';
        this._impl = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._impl.export(Gio.DBus.session, '/org/gnome/Shell/Extensions/DesktopChibi');
        this._focusId = global.display.connect('notify::focus-window', () => this._updateActive());
        this._updateActive();
    }

    _identity(window) {
        if (!window)
            return '';
        const app = Shell.WindowTracker.get_default().get_window_app(window);
        return (window.get_wm_class() || app?.get_id() || '').toLowerCase().replace(/\.desktop$/, '');
    }

    _updateActive() {
        const identity = this._identity(global.display.focus_window);
        // Editing Chibi settings does not change the application being configured.
        if (identity === 'desktop-chibi' || identity === 'desktop chibi')
            return;
        if (identity !== this._active) {
            this._active = identity;
            this._impl.emit_signal('ActiveAppChanged', new GLib.Variant('(s)', [identity]));
        }
    }

    GetState() {
        this._updateActive();
        return JSON.stringify({active: this._active});
    }

    ListApplications() {
        const apps = new Map();
        for (const actor of global.get_window_actors()) {
            const window = actor.meta_window;
            const id = this._identity(window);
            if (!id || id === 'desktop-chibi' || id === 'desktop chibi')
                continue;
            const app = Shell.WindowTracker.get_default().get_window_app(window);
            apps.set(id, {id, name: app?.get_name() || id});
        }
        return JSON.stringify([...apps.values()].sort((a, b) => a.name.localeCompare(b.name)));
    }

    disable() {
        if (this._focusId)
            global.display.disconnect(this._focusId);
        this._impl?.unexport();
        this._impl = null;
        this._focusId = 0;
        this._active = '';
    }
}
