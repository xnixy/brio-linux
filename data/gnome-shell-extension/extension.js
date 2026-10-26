// Reports the focused window's app id to briod (io.github.BrioControl1.FocusChanged).
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

export default class BrioFocusExtension extends Extension {
    enable() {
        this._tracker = Shell.WindowTracker.get_default();
        this._id = global.display.connect('notify::focus-window', () => this._report());
        this._report();
    }

    disable() {
        if (this._id)
            global.display.disconnect(this._id);
        this._id = 0;
        this._tracker = null;
    }

    _report() {
        const win = global.display.focus_window;
        let appId = '';
        if (win) {
            const app = this._tracker.get_window_app(win);
            appId = (app && app.get_id() ? app.get_id().replace(/\.desktop$/, '') : '') ||
                win.get_wm_class() || '';
        }
        Gio.DBus.session.call('io.github.BrioControl.Daemon', '/io/github/BrioControl/Daemon',
            'io.github.BrioControl1', 'FocusChanged', new GLib.Variant('(s)', [appId]),
            null, Gio.DBusCallFlags.NO_AUTO_START, 1000, null, null);
    }
}
