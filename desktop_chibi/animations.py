"""Validated, state-based GIF and sprite-sheet animation clips."""
import collections
from bisect import bisect_right
from itertools import accumulate
from functools import lru_cache
import json
from pathlib import Path
import weakref

from PySide6.QtCore import QObject, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QMovie, QPixmap

STATES = {'idle': 'Покой', 'walk': 'Ходьба', 'fall': 'Падение',
          'land': 'Приземление', 'drag': 'В руке', 'talk': 'Говорит',
          'notify': 'Уведомление', 'success': 'Готово', 'error': 'Ошибка'}


_SHEET_CACHE = collections.OrderedDict()
_CACHE_LIMIT = 32 * 1024 * 1024
_RETAINED_CACHE_LIMIT = 8 * 1024 * 1024
_LIVE_FRAMES = weakref.WeakValueDictionary()


class FrameSequence:
    """Share active clips even after their entry leaves the small warm cache."""
    def __init__(self, frames, delays=()):
        self.frames = tuple(frames)
        self.delays = tuple(delays)

    def __len__(self):
        return len(self.frames)

    def __getitem__(self, index):
        return self.frames[index]


def _cache_get(key):
    if key in _SHEET_CACHE:
        _SHEET_CACHE.move_to_end(key)
        return _SHEET_CACHE[key][0]
    live = _LIVE_FRAMES.get(key)
    if live is not None:
        return (live, live.delays) if key[0] == 'gif' else live


def release_frame_cache():
    """Drop unused decoded assets; live companions still share their frames."""
    _SHEET_CACHE.clear()


def _cache_put(key, value, cost):
    _LIVE_FRAMES[key] = value[0] if key[0] == 'gif' else value
    if cost <= _RETAINED_CACHE_LIMIT:
        _SHEET_CACHE[key] = (value, cost)
        while len(_SHEET_CACHE) > 32 or sum(item[1] for item in _SHEET_CACHE.values()) > _RETAINED_CACHE_LIMIT:
            _SHEET_CACHE.popitem(last=False)


class GifTooLarge(ValueError):
    pass


@lru_cache(maxsize=32)
def _stream_duration(filename, modified, length):
    """Keep only timing metadata for streaming GIFs, once per file revision."""
    reader = QImageReader(filename)
    if reader.imageCount() > 10000:
        raise ValueError('В GIF больше 10000 кадров. Выберите короткий фрагмент в настройках GIF.')
    reader.setScaledSize(QSize(1, 1))
    duration = 0
    count = 0
    while reader.canRead():
        if reader.read().isNull() or count >= 10000:
            raise ValueError('Не удалось прочитать все кадры GIF.')
        duration += max(10, reader.nextImageDelay()) / 1000
        count += 1
    return max(.15, duration)


def cached_gif(path, first=0, last=0, max_size=0, *, memory_limit=None):
    """Share decoded GIF frames, including their original per-frame delays."""
    info = path.stat()
    memory_limit = _CACHE_LIMIT if memory_limit is None else memory_limit
    key = ('gif', str(path.resolve()), info.st_mtime_ns, info.st_size, first, last, max_size)
    cached = _cache_get(key)
    if cached is not None:
        if sum(frame.width() * frame.height() * 4 for frame in cached[0]) > memory_limit:
            raise GifTooLarge('Декодированные кадры GIF превышают лимит памяти.')
        return cached
    reader = QImageReader(str(path))
    count = reader.imageCount()
    size = reader.size()
    if first < 0 or (last and last <= first) or (count > 0 and (first >= count or last > count)):
        raise ValueError('Проверьте первый и последний кадр GIF.')
    selected_count = (last or count) - first
    if max_size and max(size.width(), size.height()) > max_size:
        size = size.scaled(QSize(max_size, max_size), Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(size)
    if selected_count > 512 or size.width() * size.height() * 4 * max(1, selected_count) > memory_limit:
        raise GifTooLarge(f'Для обработки GIF нужно не больше 512 кадров и {memory_limit // 1048576} МиБ. Уменьшите размер или выберите часть кадров в настройках GIF.')
    frames, delays, cost = [], [], 0
    index = 0
    while reader.canRead():
        image = reader.read()
        if image.isNull():
            raise ValueError(f'Не удалось прочитать GIF: {path.name}')
        delay = max(10, reader.nextImageDelay())
        index += 1
        if index <= first:
            continue
        cost += image.width() * image.height() * 4
        if len(frames) >= 512 or cost > memory_limit:
            raise GifTooLarge('GIF слишком большой для обратного проигрывания: уменьшите размер или число кадров.')
        frames.append(QPixmap.fromImage(image))
        delays.append(delay)
        if last and index >= last:
            break
    if not frames or (selected_count > 0 and selected_count != len(frames)):
        raise ValueError(f'Не удалось прочитать все кадры GIF: {path.name}')
    sequence = FrameSequence(frames, delays)
    value = (sequence, sequence.delays)
    _cache_put(key, value, cost)
    return value


def default_notification():
    return {'file': str(Path(__file__).parent / 'assets/default_notify.png'),
            'width': 32, 'height': 32, 'frames': 38, 'fps': 8}


def frame_index(step, count, pingpong=False, loop=True):
    """Ping-pong has no duplicated endpoints: 0, 1, 2, 1, 0, ..."""
    if count <= 1:
        return 0
    period = 2 * count - 2 if pingpong else count
    if not loop and step >= period:
        return 0 if pingpong else count - 1
    index = max(0, step) % period
    return min(index, period - index) if pingpong else index


def cached_sheet(path, width, height, count):
    info = path.stat()
    key = (str(path.resolve()), info.st_mtime_ns, info.st_size, width, height, count)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    image = QPixmap.fromImage(QImage(str(path)))
    if image.isNull():
        raise ValueError(f'Не удалось прочитать изображение: {path.name}')
    width, height = width or image.width(), height or image.height()
    columns, rows = image.width() // width, image.height() // height
    if (columns < 1 or rows < 1 or count > columns * rows
            or image.width() % width or image.height() % height):
        raise ValueError(f'{path.name}: размеры кадра не делят лист или кадров больше, чем ячеек.')
    frames = FrameSequence(image.copy((i % columns) * width, (i // columns) * height,
                                     width, height) for i in range(count))
    cost = width * height * 4 * count
    _cache_put(key, frames, cost)
    return frames


class Clip:
    def __init__(self, spec, parent):
        self.spec = dict(spec)
        self.source_spec = dict(spec)
        self.movie = None
        self.frames = []
        self.gif_delays = ()
        self.speed = 100
        path = Path(spec.get('file', ''))
        if not path.is_file():
            raise ValueError(f'Файл не найден: {path}')
        self.fps = int(spec.get('fps', 8))
        self.pingpong = spec.get('pingpong', False)
        if not isinstance(self.pingpong, bool):
            raise ValueError('Обратное проигрывание должно быть включено или выключено.')
        count = int(spec.get('frames', 1))
        width = int(spec.get('width', 0))
        height = int(spec.get('height', 0))
        if not 1 <= self.fps <= 60 or not 1 <= count <= 512 or min(width, height) < 0:
            raise ValueError('FPS: 1–60; кадров: 1–512; размеры не могут быть отрицательными.')
        self.spec.update(file=str(path.resolve()), fps=self.fps, frames=count, width=width, height=height)
        if path.suffix.lower() == '.gif':
            first, last, max_size = (spec.get(key, 0) for key in ('first_frame', 'last_frame', 'max_size'))
            self.speed = spec.get('speed', 100)
            reverse = spec.get('reverse', False)
            if (any(type(value) is not int for value in (first, last, max_size, self.speed))
                    or not 0 <= first <= 9999 or not 0 <= last <= 10000 or not 0 <= max_size <= 1024
                    or not 25 <= self.speed <= 400 or type(reverse) is not bool):
                raise ValueError('GIF: скорость 25–400 %, размер до 1024 пкс, диапазон до 10000 кадров.')
            try:
                self.frames, self.gif_delays = cached_gif(path, first, last, max_size)
            except GifTooLarge:
                if self.pingpong or reverse or first or last:
                    raise
            except OSError as error:
                raise ValueError(f'Не удалось прочитать GIF: {path.name}') from error
            if self.frames:
                self.order = tuple(range(len(self.frames)))
                if reverse:
                    self.order = self.order[::-1]
                if self.pingpong:
                    self.order += self.order[-2:0:-1]
                self.frame_ends = tuple(accumulate(self.gif_delays[index] * 100 / self.speed for index in self.order))
                self.duration = self.frame_ends[-1] / 1000
                return
            # Large forward-only GIFs stay streaming instead of retaining every
            # decoded frame. Reverse playback always has a bounded working set.
            info = path.stat()
            duration = _stream_duration(str(path.resolve()), info.st_mtime_ns, info.st_size)
            self.movie = QMovie(str(path), parent=parent)
            if max_size:
                reader = QImageReader(str(path))
                size = reader.size()
                if max(size.width(), size.height()) > max_size:
                    self.movie.setScaledSize(size.scaled(QSize(max_size, max_size), Qt.AspectRatioMode.KeepAspectRatio))
            self.movie.setCacheMode(QMovie.CacheMode.CacheNone)
            self.movie.setSpeed(self.speed)
            if not self.movie.isValid():
                self.movie.deleteLater()
                self.movie = None
                raise ValueError(f'Не удалось прочитать GIF: {path.name}')
            self.movie.jumpToFrame(0)
            self.movie.finished.connect(self.start)
            self.duration = duration * 100 / self.speed
        else:
            try:
                self.frames = cached_sheet(path, width, height, count)
            except OSError as error:
                raise ValueError(f'Не удалось прочитать файл: {path.name}') from error
            self.duration = (max(1, 2 * count - 2) if self.pingpong else count) / self.fps
            delays = spec.get('delays')
            if delays is not None:
                if not isinstance(delays, list) or len(delays) != count or any(type(d) is not int or not 10 <= d <= 60000 for d in delays):
                    raise ValueError('Задержки должны соответствовать кадрам: 10–60000 мс.')
                self.gif_delays = tuple(delays)
                self.order = tuple(range(count))
                if self.pingpong:
                    self.order += self.order[-2:0:-1]
                self.frame_ends = tuple(accumulate(self.gif_delays[index] for index in self.order))
                self.duration = self.frame_ends[-1] / 1000

    def start(self):
        if self.movie:
            self.movie.stop()
            self.movie.jumpToFrame(0)
            self.movie.start()

    def stop(self):
        if self.movie:
            self.movie.stop()

    def close(self):
        if self.movie:
            self.movie.stop()
            self.movie.deleteLater()

    def frame(self, elapsed, loop=True):
        if self.movie:
            # QMovie controls GIF frame timing. Repeat looping GIFs naturally.
            return self.movie.currentPixmap()
        if self.gif_delays:
            milliseconds = max(0, elapsed) * 1000
            if not loop and milliseconds >= self.frame_ends[-1]:
                return self.frames[self.order[0 if self.pingpong else -1]]
            milliseconds %= self.frame_ends[-1]
            index = min(len(self.order) - 1, bisect_right(self.frame_ends, milliseconds + 1e-7))
            return self.frames[self.order[index]]
        index = int(max(0, elapsed) * self.fps)
        index = frame_index(index, len(self.frames), self.pingpong, loop)
        return self.frames[index]


class Animations(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.clips = {}
        self.specs = {}
        self.active = None

    def configure(self, specs):
        """Load transactionally: an invalid clip leaves the old character intact."""
        pending = {}
        created = []
        try:
            if not isinstance(specs, dict) or len(specs) > 32:
                raise ValueError('Нужен объект с максимум 32 анимациями.')
            for state, spec in specs.items():
                if not isinstance(state, str) or (state not in STATES and not (state.startswith('idle_') and state[5:].isdigit())):
                    raise ValueError(f'Неизвестное состояние: {state}')
                if not isinstance(spec, dict):
                    raise ValueError(f'{state}: неверное описание анимации.')
                priority = spec.get('priority', 0)
                if type(priority) is not int or not 0 <= priority <= 100:
                    raise ValueError(f'{state}: приоритет должен быть целым числом от 0 до 100.')
                existing = self.clips.get(state)
                if existing and (spec == existing.source_spec or spec == existing.spec):
                    pending[state] = existing
                else:
                    pending[state] = Clip(spec, self)
                    created.append(pending[state])
        except (ValueError, TypeError, OverflowError) as error:
            for clip in created:
                clip.close()
            raise ValueError(str(error)) from error
        for state, clip in self.clips.items():
            if pending.get(state) is not clip:
                clip.close()
            else:
                clip.stop()
        self.clips = pending
        self.specs = {state: dict(clip.spec) for state, clip in pending.items()}
        self.active = None

    def restart(self):
        if self.active in self.clips:
            self.clips[self.active].stop()
        self.active = None

    def suspend(self):
        if self.active is not None:
            self.restart()

    def frame(self, state, elapsed):
        key = state if state in self.clips else 'idle'
        if key not in self.clips:
            return QPixmap()
        if self.active != key:
            if self.active in self.clips:
                self.clips[self.active].stop()
            self.active = key
            self.clips[key].start()
        return self.clips[key].frame(elapsed, loop=state != 'land')

    def duration(self, state, default):
        clip = self.clips.get(state)
        return clip.duration if clip else default


def read_manifest(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('version') != 1 or not isinstance(data.get('animations'), dict):
            raise ValueError('Ожидается набор персонажа версии 1.')
        specs = data['animations']
        for spec in specs.values():
            spec['file'] = str((path.parent / Path(spec['file'])).resolve())
            if 'project' in spec:
                spec['project'] = str((path.parent / Path(spec['project'])).resolve())
        return str(data.get('name', path.stem)), specs
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise ValueError(f'Не удалось открыть набор: {error}') from error
