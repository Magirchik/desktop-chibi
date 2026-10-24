import os
import sys

# Wayland does not allow clients to place their own top-level windows.
# Prefer XWayland when available, without overriding an explicit user choice.
if sys.platform.startswith('linux') and os.environ.get('DISPLAY'):
    os.environ.setdefault('QT_QPA_PLATFORM', 'xcb')

from .app import main

main()
