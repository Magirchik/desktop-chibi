"""A small desktop companion."""
__version__ = '0.1.0'

# File-backed settings also let the CLI and recovery tool share a portable
# profile on Windows; avoid depending on the registry for event permissions.
import sys
if sys.platform == 'win32':
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
