"""Source and frozen entry points, including the CLI and packaged-app smoke test."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from . import __version__


def self_test(report):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QSettings, qVersion
    from PySide6.QtGui import QFontDatabase, QImageReader
    from PySide6.QtWidgets import QApplication
    with tempfile.TemporaryDirectory(prefix='desktop-chibi-self-test-') as temporary:
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, temporary)
        app = QApplication.instance() or QApplication(['desktop-chibi-self-test'])
        app.setQuitOnLastWindowClosed(False)
        from .app import Settings
        from .animations import default_notification
        from .animation_editor import AnimationEditor
        from .appearance import appearance
        from .app_settings import AppSettings
        window = Settings()
        try:
            window.show()
            app.processEvents()
            assert not window.pet.current_frame().isNull(), 'Missing default character'
            assert 'Pixeloid Sans' in QFontDatabase.families(), 'Missing bundled font'
            formats = {bytes(value).decode() for value in QImageReader.supportedImageFormats()}
            assert {'png', 'gif'} <= formats, 'Missing image plugins'
            editor = AnimationEditor({'notify': default_notification()})
            editor.state.setCurrentIndex(editor.state.findData('notify'))
            editor.show()
            app.processEvents()
            assert len(editor.player.clips['notify'].frames) == 38
            editor.reject()
            editor.deleteLater()
            settings = AppSettings(window)
            for theme in ('light', 'dark', 'midnight'):
                appearance().set_theme(theme)
                app.processEvents()
                assert not window.grab().isNull(), 'Cannot render settings'
            settings.reject()
            settings.deleteLater()
            native = {}
            if sys.platform == 'win32':
                from .windows_desktop import WindowsShortcuts, WindowsApplications, DEFAULT_KEYS
                from .windows_sound import WaveSound
                shortcuts = WindowsShortcuts()
                bridge = WindowsApplications()
                sound = WaveSound()
                try:
                    shortcuts.rebind(DEFAULT_KEYS)
                    bridge.start()
                    bridge.applications()
                    native = {'hotkeys_registered': True, 'foreground_hook': True, 'winmm_loaded': True}
                finally:
                    shortcuts.stop()
                    bridge.stop()
                    sound.stop()
                    shortcuts.deleteLater()
                    bridge.deleteLater()
                    sound.deleteLater()
            payload = {'ok': True, 'version': __version__, 'platform': sys.platform, 'qt': qVersion(),
                       'frozen': bool(getattr(sys, 'frozen', False)), 'image_plugins': sorted(formats), 'native': native}
        finally:
            window.shutdown()
            window.tray.hide()
            window.hide()
            window.deleteLater()
            app.processEvents()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return 0


def main(argv=None):
    if getattr(sys, 'frozen', False) and sys.platform.startswith('linux'):
        # Child processes such as pw-play must use the host's shared libraries.
        original = os.environ.get('LD_LIBRARY_PATH_ORIG')
        if original:
            os.environ['LD_LIBRARY_PATH'] = original
        else:
            os.environ.pop('LD_LIBRARY_PATH', None)
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == '--emit':
        from .emit import main as emit_main
        return emit_main(args[1:])
    if '--version' in args:
        print('Desktop Chibi ' + __version__)
        return 0
    if '--self-test' in args:
        parser = argparse.ArgumentParser()
        parser.add_argument('--self-test', action='store_true')
        parser.add_argument('--report', type=Path, required=True)
        options = parser.parse_args(args)
        return self_test(options.report.resolve())
    if sys.platform.startswith('linux') and os.environ.get('DISPLAY'):
        os.environ.setdefault('QT_QPA_PLATFORM', 'xcb')
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Magirchik.DesktopChibi')
    from .app import main as application_main
    application_main()
    return 0
