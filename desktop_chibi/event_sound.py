"""Optional short event sounds: one native player, only while audio is playing."""
from pathlib import Path
import shutil
import time
import sys

from PySide6.QtCore import QObject, QProcess, QTimer, Signal


def validate_sound(sound):
    if not isinstance(sound, dict) or set(sound) - {'enabled', 'file', 'volume', 'when_hidden'}:
        raise ValueError('Неверные настройки звука правила.')
    if type(sound.get('enabled', False)) is not bool or type(sound.get('when_hidden', False)) is not bool:
        raise ValueError('Звук и звук скрытого чибика должны быть включены или выключены.')
    path = sound.get('file', '')
    volume = sound.get('volume', 35)
    if not isinstance(path, str) or len(path) > 4096 or '\x00' in path:
        raise ValueError('Неверный путь к звуку.')
    if type(volume) is not int or not 0 <= volume <= 100:
        raise ValueError('Громкость: от 0 до 100 %.')
    if sound.get('enabled') and not path:
        raise ValueError('Выберите файл звука или выключите звук правила.')


class EventSound(QObject):
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.native = None
        self.last_play = -float('inf')
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.stop)

    def play(self, sound):
        validate_sound(sound)
        if not sound.get('enabled') or not sound.get('volume', 35):
            return False
        # One sound channel prevents a global rule from playing 32 copies.
        if self.process is not None or self.native is not None or time.monotonic() - self.last_play < .25:
            return False
        path = Path(sound['file']).expanduser()
        try:
            if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('Выберите звуковой файл размером до 16 МиБ.')
        except OSError:
            self.status.emit('Звуковой файл недоступен.')
            return False
        except ValueError as error:
            self.status.emit(str(error))
            return False
        volume = sound.get('volume', 35) / 100
        if sys.platform == 'win32':
            from .windows_sound import WaveSound
            native = WaveSound(self)
            try:
                native.start(path.resolve(), sound.get('volume', 35))
            except ValueError as error:
                native.deleteLater()
                self.status.emit(str(error))
                return False
            self.native = native
            native.finished.connect(self.native_finished)
            self.last_play = time.monotonic()
            self.timeout.start(15000)
            return True
        player = shutil.which('pw-play') or shutil.which('paplay')
        if not player:
            self.status.emit('Для звука нужен pw-play (PipeWire) или paplay (PulseAudio).')
            return False
        args = [f'--volume={volume:.2f}' if Path(player).name == 'pw-play'
                else f'--volume={round(volume * 65536)}', str(path.resolve())]
        process = QProcess(self)
        self.process = process
        process.setStandardOutputFile(QProcess.nullDevice())
        process.setStandardErrorFile(QProcess.nullDevice())
        process.finished.connect(lambda code, _status: self.finished(process, code))
        process.errorOccurred.connect(lambda _error: self.finished(process, -1))
        process.start(player, args)
        self.last_play = time.monotonic()
        self.timeout.start(15000)
        return True

    def native_finished(self, message):
        self.stop()
        self.status.emit(message)

    def finished(self, process, code):
        if self.process is not process:
            return
        self.process = None
        self.timeout.stop()
        process.deleteLater()
        self.status.emit('Звук воспроизведён.' if code == 0 else 'Не удалось проиграть звук. Попробуйте WAV, OGG или FLAC и проверьте звуковой выход.')

    def stop(self):
        self.timeout.stop()
        native, self.native = self.native, None
        if native is not None:
            native.stop()
            native.deleteLater()
        process, self.process = self.process, None
        if process is not None:
            process.kill()
            process.waitForFinished(100)
            process.deleteLater()
