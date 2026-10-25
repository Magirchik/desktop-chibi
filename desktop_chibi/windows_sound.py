"""Bounded PCM WAV playback through WinMM, with volume on our own audio handle."""
import ctypes as C
import wave
from PySide6.QtCore import QObject, QTimer, Signal


class WaveFormat(C.Structure):
    _pack_ = 1
    _fields_ = [('format', C.c_uint16), ('channels', C.c_uint16), ('rate', C.c_uint32),
                ('bytes_per_second', C.c_uint32), ('alignment', C.c_uint16),
                ('bits', C.c_uint16), ('extra', C.c_uint16)]


class WaveHeader(C.Structure):
    _fields_ = [('data', C.c_void_p), ('length', C.c_uint32), ('recorded', C.c_uint32),
                ('user', C.c_size_t), ('flags', C.c_uint32), ('loops', C.c_uint32),
                ('next', C.c_void_p), ('reserved', C.c_size_t)]


class WaveSound(QObject):
    finished = Signal(str)

    def __init__(self, parent=None, api=None):
        super().__init__(parent)
        self.api = api or C.WinDLL('winmm')
        self.handle = C.c_void_p()
        self.header = WaveHeader()
        self.buffer = None
        self.prepared = False
        if api is None:
            signatures = [
                ('waveOutOpen', [C.POINTER(C.c_void_p), C.c_uint32, C.POINTER(WaveFormat), C.c_size_t, C.c_size_t, C.c_uint32]),
                ('waveOutSetVolume', [C.c_void_p, C.c_uint32]),
                ('waveOutPrepareHeader', [C.c_void_p, C.POINTER(WaveHeader), C.c_uint32]),
                ('waveOutWrite', [C.c_void_p, C.POINTER(WaveHeader), C.c_uint32]),
                ('waveOutReset', [C.c_void_p]), ('waveOutClose', [C.c_void_p]),
                ('waveOutUnprepareHeader', [C.c_void_p, C.POINTER(WaveHeader), C.c_uint32]),
            ]
            for name, arguments in signatures:
                function = getattr(self.api, name)
                function.argtypes, function.restype = arguments, C.c_uint32
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.poll)

    @staticmethod
    def check(result):
        if result:
            raise ValueError('Windows не смогла открыть звуковой выход. Выберите WAV (PCM) и проверьте устройство воспроизведения.')

    def start(self, path, volume):
        if path.suffix.casefold() != '.wav':
            raise ValueError('В Windows выберите короткий WAV (PCM).')
        try:
            with wave.open(str(path), 'rb') as source:
                channels, width, rate = source.getnchannels(), source.getsampwidth(), source.getframerate()
                if channels not in (1, 2) or width not in (1, 2, 3, 4) or rate <= 0:
                    raise ValueError('Нужен моно или стерео WAV (PCM 8/16/24/32 бит).')
                # Decode at most fifteen seconds and sixteen MiB, even for a bad header.
                count = min(source.getnframes(), rate * 15, 16 * 1024 * 1024 // (channels * width))
                data = source.readframes(count)
            if not data:
                raise ValueError('Звуковой файл пуст.')
            alignment = channels * width
            format = WaveFormat(1, channels, rate, rate * alignment, alignment, width * 8, 0)
            self.check(self.api.waveOutOpen(C.byref(self.handle), 0xffffffff, C.byref(format), 0, 0, 0))
            level = round(max(0, min(100, volume)) * 65535 / 100)
            self.check(self.api.waveOutSetVolume(self.handle, level | (level << 16)))
            self.buffer = C.create_string_buffer(data)
            self.header = WaveHeader(C.cast(self.buffer, C.c_void_p), len(data), 0, 0, 0, 0, None, 0)
            self.check(self.api.waveOutPrepareHeader(self.handle, C.byref(self.header), C.sizeof(self.header)))
            self.prepared = True
            self.check(self.api.waveOutWrite(self.handle, C.byref(self.header), C.sizeof(self.header)))
            self.timer.start()
        except (OSError, wave.Error, EOFError, ValueError) as error:
            self.stop()
            raise ValueError(str(error)) from error

    def poll(self):
        if self.header.flags & 1:  # WHDR_DONE: WinMM has finished with the buffer.
            self.stop()
            self.finished.emit('Звук воспроизведён.')

    def stop(self):
        self.timer.stop()
        if self.handle.value:
            self.api.waveOutReset(self.handle)
            if self.prepared:
                self.api.waveOutUnprepareHeader(self.handle, C.byref(self.header), C.sizeof(self.header))
            self.api.waveOutClose(self.handle)
        self.handle = C.c_void_p()
        self.prepared = False
        self.buffer = None
