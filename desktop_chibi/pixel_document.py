"""Editable layers and timeline; flattened sheets are only a playback/export format."""
import base64
import json
import math
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QImageReader, QPainter, QTransform

MAX_DIMENSION = 512
EDITOR_BYTES = 64 * 1024 * 1024
HISTORY_BYTES = 24 * 1024 * 1024
COMPOSITE_BYTES = 4 * 1024 * 1024


def blank_image(width, height):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    return image


class PixelDocument:
    def __init__(self, frames=None, width=32, height=32, fps=8, delays=None):
        if frames:
            size = frames[0].size()
            if not 1 <= len(frames) <= 512 or any(image.size() != size or image.isNull() for image in frames):
                raise ValueError('Нужно от 1 до 512 одинаковых непустых кадров.')
            width, height = size.width(), size.height()
        if not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION:
            raise ValueError('Редактор поддерживает кадры размером до 512×512.')
        if width * height * 4 * (len(frames) if frames else 1) > EDITOR_BYTES:
            raise ValueError('Кадры занимают больше 64 МиБ. Выберите меньший размер или часть анимации.')
        images = [image.convertToFormat(QImage.Format.Format_ARGB32) for image in frames] if frames else [blank_image(width, height)]
        if len(images) > 512 or any(image.isNull() for image in images):
            raise ValueError('Нужно от 1 до 512 непустых кадров.')
        size = images[0].size()
        self.layers = [dict(name='Основа', shared=False, visible=True, locked=False, images=images)]
        self.layer_index = self.index = 0
        self.count = len(images)
        self.fps = max(1, min(60, int(fps)))
        self.pingpong = False
        self.delays = self.validate_delays(delays, len(images))
        self.undo_stack, self.redo_stack = [], []
        self._cache = OrderedDict()

    @staticmethod
    def validate_delays(delays, count):
        if delays is None:
            return None
        if not isinstance(delays, (list, tuple)) or len(delays) != count or any(type(d) is not int or not 10 <= d <= 60000 for d in delays):
            raise ValueError('Задержки должны соответствовать кадрам и быть от 10 до 60000 мс.')
        return list(delays)

    @property
    def memory_bytes(self):
        return self.image.width() * self.image.height() * 4 * sum(len(layer['images']) for layer in self.layers)

    def check_capacity(self, extra_images):
        if self.memory_bytes + self.image.width() * self.image.height() * 4 * extra_images > EDITOR_BYTES:
            raise ValueError('Слои и кадры достигли 64 МиБ. Уменьшите холст или удалите лишние кадры / слои.')

    def export_dimensions(self, max_size=0):
        size = self.image.size()
        side = max(size.width(), size.height()) if max_size == 0 else max_size
        def scaled(value):
            return size.scaled(value, value, Qt.AspectRatioMode.KeepAspectRatio) if value > 0 and max(size.width(), size.height()) > value else size
        output = scaled(side)
        if max_size == 0:
            while output.width() * output.height() * self.count * 4 > 32 * 1024 * 1024:
                side = max(1, side // 2)
                output = scaled(side)
        if output.width() * output.height() * self.count * 4 > 32 * 1024 * 1024:
            raise ValueError('Анимация для чибика превышает 32 МиБ. Выберите «Авто» или меньший размер для чибика.')
        return output

    def prune_history(self):
        active = {image.cacheKey() for layer in self.layers for image in layer['images']}
        def history_cost():
            images = {image.cacheKey(): image.sizeInBytes() for stack in (self.undo_stack, self.redo_stack)
                      for snapshot in stack for layer in snapshot[0] for image in layer['images']
                      if image.cacheKey() not in active}
            return sum(images.values())
        while history_cost() > HISTORY_BYTES:
            stack = self.undo_stack if self.undo_stack else self.redo_stack
            if not stack:
                break
            stack.pop(0)

    @property
    def layer(self):
        return self.layers[self.layer_index]

    @property
    def image(self):
        return self.layer['images'][0 if self.layer['shared'] else self.index]

    @property
    def frames(self):
        return [self.composite(index) for index in range(self.count)]

    def composite(self, index):
        visible = [layer for layer in self.layers if layer['visible']]
        if len(visible) == 1:
            layer = visible[0]
            return QImage(layer['images'][0 if layer['shared'] else index])
        if index not in self._cache:
            result = blank_image(self.image.width(), self.image.height())
            painter = QPainter(result)
            for layer in self.layers:
                if layer['visible']:
                    painter.drawImage(0, 0, layer['images'][0 if layer['shared'] else index])
            painter.end()
            self._cache[index] = result
            cost = result.sizeInBytes()
            while len(self._cache) > max(1, COMPOSITE_BYTES // cost):
                self._cache.popitem(last=False)
        self._cache.move_to_end(index)
        return self._cache[index]

    def invalidate(self):
        self._cache.clear()

    def snapshot(self):
        return ([{**layer, 'images': [QImage(image) for image in layer['images']]} for layer in self.layers],
                self.index, self.layer_index, self.count, list(self.delays) if self.delays else None)

    def restore(self, snapshot):
        self.layers, self.index, self.layer_index, self.count, self.delays = snapshot
        self.invalidate()

    def begin_edit(self):
        self.undo_stack.append(self.snapshot())
        self.undo_stack = self.undo_stack[-50:]
        self.redo_stack.clear()
        self.prune_history()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.snapshot())
            self.restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.snapshot())
            self.restore(self.redo_stack.pop())

    def line(self, start, end, color, size=1):
        if self.layer['locked']:
            return
        self.invalidate()
        image = self.image
        x, y = start
        tx, ty = end
        dx, dy = abs(tx - x), -abs(ty - y)
        sx, sy = (1 if x < tx else -1), (1 if y < ty else -1)
        error = dx + dy
        while True:
            for py in range(y - (size - 1) // 2, y - (size - 1) // 2 + size):
                for px in range(x - (size - 1) // 2, x - (size - 1) // 2 + size):
                    if 0 <= px < image.width() and 0 <= py < image.height():
                        image.setPixelColor(px, py, color)
            if (x, y) == (tx, ty):
                break
            twice = 2 * error
            if twice >= dy:
                error += dy
                x += sx
            if twice <= dx:
                error += dx
                y += sy

    def fill(self, x, y, color):
        if self.layer['locked']:
            return
        image = self.image
        old = image.pixelColor(x, y).rgba()
        if old == color.rgba():
            return
        self.begin_edit()
        self.invalidate()
        pixels = image.bits().cast('I')
        width, height = image.width(), image.height()
        new = color.rgba()
        pending = [(x, y)]
        while pending:
            px, py = pending.pop()
            if pixels[py * width + px] != old:
                continue
            left = px
            while left > 0 and pixels[py * width + left - 1] == old:
                left -= 1
            right = px
            while right + 1 < width and pixels[py * width + right + 1] == old:
                right += 1
            above = below = False
            for sx in range(left, right + 1):
                pixels[py * width + sx] = new
                if py > 0:
                    match = pixels[(py - 1) * width + sx] == old
                    if match and not above:
                        pending.append((sx, py - 1))
                    above = match
                if py + 1 < height:
                    match = pixels[(py + 1) * width + sx] == old
                    if match and not below:
                        pending.append((sx, py + 1))
                    below = match
        self.prune_history()

    def add_layer(self, shared=False):
        if len(self.layers) >= 16:
            return
        self.check_capacity(1 if shared else self.count)
        self.begin_edit()
        images = [blank_image(self.image.width(), self.image.height()) for _ in range(1 if shared else self.count)]
        self.layer_index += 1
        self.layers.insert(self.layer_index, dict(name='Общая деталь' if shared else 'Новый слой',
                                                 shared=shared, visible=True, locked=False, images=images))
        self.invalidate()

    def delete_layer(self):
        if len(self.layers) > 1:
            self.begin_edit()
            self.layers.pop(self.layer_index)
            self.layer_index = min(self.layer_index, len(self.layers) - 1)
            self.invalidate()

    def move_layer(self, delta):
        target = self.layer_index + delta
        if 0 <= target < len(self.layers):
            self.begin_edit()
            layer = self.layers.pop(self.layer_index)
            self.layers.insert(target, layer)
            self.layer_index = target
            self.invalidate()

    def set_layer(self, key, value):
        if self.layer[key] == value:
            return
        if key == 'shared' and not value:
            self.check_capacity(self.count - 1)
        self.begin_edit()
        if key == 'shared':
            # Keep the selected pose when joining; fork copies when separating.
            image = self.image.copy()
            self.layer['images'] = [image] if value else [QImage(image) for _ in range(self.count)]
        self.layer[key] = value
        self.invalidate()

    def add_frame(self, duplicate=False, at_end=False):
        if self.count >= 512:
            return
        self.check_capacity(sum(not layer['shared'] for layer in self.layers))
        self.begin_edit()
        delay = self.delays[self.index] if self.delays is not None else None
        target = self.count if at_end else self.index + 1
        for layer in self.layers:
            if not layer['shared']:
                image = layer['images'][self.index]
                layer['images'].insert(target, QImage(image) if duplicate else blank_image(image.width(), image.height()))
        self.index = target
        if self.delays is not None:
            self.delays.insert(target, delay)
        self.count += 1
        self.invalidate()

    def delete_frame(self):
        self.begin_edit()
        if self.count == 1:
            for layer in self.layers:
                layer['images'] = [blank_image(self.image.width(), self.image.height())]
        else:
            if self.delays is not None:
                self.delays.pop(self.index)
            for layer in self.layers:
                if not layer['shared']:
                    layer['images'].pop(self.index)
            self.count -= 1
            self.index = min(self.index, self.count - 1)
        self.invalidate()

    def move_frame(self, delta):
        target = self.index + delta
        if 0 <= target < self.count and target != self.index:
            self.begin_edit()
            for layer in self.layers:
                if not layer['shared']:
                    layer['images'].insert(target, layer['images'].pop(self.index))
            if self.delays is not None:
                self.delays.insert(target, self.delays.pop(self.index))
            self.index = target
            self.invalidate()

    def transform(self, action, all_frames=False):
        if self.layer['locked']:
            return
        self.begin_edit()
        indices = range(len(self.layer['images'])) if all_frames else [0 if self.layer['shared'] else self.index]
        for index in indices:
            image = self.layer['images'][index]
            if action in ('flip_x', 'flip_y'):
                transform = QTransform().scale(-1 if action == 'flip_x' else 1, -1 if action == 'flip_y' else 1)
                result = image.transformed(transform, Qt.TransformationMode.FastTransformation)
            else:
                result = blank_image(image.width(), image.height())
                if action != 'clear':
                    dx, dy = {'left': (-1, 0), 'right': (1, 0), 'up': (0, -1), 'down': (0, 1)}[action]
                    painter = QPainter(result)
                    painter.drawImage(dx, dy, image)
                    painter.end()
            self.layer['images'][index] = result
        self.invalidate()
        self.prune_history()

    def new_canvas(self, size):
        if not 1 <= size <= MAX_DIMENSION:
            raise ValueError('Размер холста: до 512×512.')
        self.begin_edit()
        self.layers = [dict(name='Основа', shared=False, visible=True, locked=False, images=[blank_image(size, size)])]
        self.index = self.layer_index = 0
        self.count = 1
        self.delays = None
        self.invalidate()

    def sheet(self, max_size=0):
        width, height = self.image.width(), self.image.height()
        if max_size and max(width, height) > max_size:
            size = self.image.size().scaled(max_size, max_size, Qt.AspectRatioMode.KeepAspectRatio)
            width, height = size.width(), size.height()
        columns = min(16, self.count)
        output = blank_image(columns * width, math.ceil(self.count / columns) * height)
        painter = QPainter(output)
        for index in range(self.count):
            image = self.composite(index)
            if image.width() != width or image.height() != height:
                image = image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
            painter.drawImage((index % columns) * width, (index // columns) * height, image)
        painter.end()
        return output

    def save_project(self, path):
        layers = []
        for layer in self.layers:
            images = []
            for image in layer['images']:
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                if not image.save(buffer, 'PNG'):
                    raise ValueError('Не удалось сохранить слой.')
                images.append(base64.b64encode(bytes(buffer.data())).decode('ascii'))
            layers.append({key: value for key, value in layer.items() if key != 'images'} | {'images': images})
        Path(path).write_text(json.dumps(dict(version=1, layers=layers, count=self.count, fps=self.fps,
                                              pingpong=self.pingpong, delays=self.delays, index=self.index, layer_index=self.layer_index),
                                         ensure_ascii=False), encoding='utf-8')

    @classmethod
    def load_project(cls, path):
        try:
            path = Path(path)
            if path.stat().st_size > 128 * 1024 * 1024:
                raise ValueError('Проект слишком большой (максимум 128 МиБ).')
            data = json.loads(path.read_text(encoding='utf-8'))
            count, layers = data['count'], data['layers']
            if data['version'] != 1 or not isinstance(count, int) or not 1 <= count <= 512 or not 1 <= len(layers) <= 16:
                raise ValueError('Неверное число кадров или слоёв.')
            result = cls()
            size = None
            decoded = []
            cost = 0
            for layer in layers:
                if any(not isinstance(layer[key], bool) for key in ('shared', 'visible', 'locked')):
                    raise ValueError('Неверные свойства слоя.')
                expected = 1 if layer['shared'] else count
                if len(layer['images']) != expected:
                    raise ValueError('Количество кадров в слоях не совпадает.')
                images = []
                for value in layer['images']:
                    raw = base64.b64decode(value, validate=True)
                    buffer = QBuffer()
                    buffer.setData(raw)
                    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
                    reader = QImageReader(buffer, b'PNG')
                    dimensions = reader.size()
                    cost += max(0, dimensions.width()) * max(0, dimensions.height()) * 4
                    if max(dimensions.width(), dimensions.height()) > MAX_DIMENSION or cost > EDITOR_BYTES:
                        raise ValueError('Слои: размер до 512×512, общий объём до 64 МиБ.')
                    image = reader.read().convertToFormat(QImage.Format.Format_ARGB32)
                    if image.isNull() or (size is not None and image.size() != size):
                        raise ValueError('Неверный размер слоя.')
                    size = image.size()
                    images.append(image)
                decoded.append(dict(name=str(layer['name'])[:80], shared=layer['shared'], visible=layer['visible'], locked=layer['locked'], images=images))
            result.layers, result.count = decoded, count
            result.fps = max(1, min(60, int(data.get('fps', 8))))
            result.pingpong = data.get('pingpong') is True
            result.delays = cls.validate_delays(data.get('delays'), count)
            result.index = max(0, min(count - 1, int(data.get('index', 0))))
            result.layer_index = max(0, min(len(decoded) - 1, int(data.get('layer_index', 0))))
            return result
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            raise ValueError(f'Не удалось открыть проект слоёв: {error}') from error
