"""Editable layers and timeline; flattened sheets are only a playback/export format."""
import base64
import json
import math
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter, QTransform


def blank_image(width, height):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    return image


class PixelDocument:
    def __init__(self, frames=None, width=32, height=32, fps=8):
        images = [image.convertToFormat(QImage.Format.Format_ARGB32) for image in frames] if frames else [blank_image(width, height)]
        if len(images) > 512 or any(image.isNull() for image in images):
            raise ValueError('Нужно от 1 до 512 непустых кадров.')
        size = images[0].size()
        if max(size.width(), size.height()) > 256 or any(image.size() != size for image in images):
            raise ValueError('Редактор поддерживает одинаковые кадры размером до 256×256.')
        self.layers = [dict(name='Основа', shared=False, visible=True, locked=False, images=images)]
        self.layer_index = self.index = 0
        self.count = len(images)
        self.fps = max(1, min(60, int(fps)))
        self.pingpong = False
        self.undo_stack, self.redo_stack = [], []
        self._cache = {}

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
        if index not in self._cache:
            result = blank_image(self.image.width(), self.image.height())
            painter = QPainter(result)
            for layer in self.layers:
                if layer['visible']:
                    painter.drawImage(0, 0, layer['images'][0 if layer['shared'] else index])
            painter.end()
            self._cache[index] = result
        return self._cache[index]

    def invalidate(self):
        self._cache.clear()

    def snapshot(self):
        return ([{**layer, 'images': [QImage(image) for image in layer['images']]} for layer in self.layers],
                self.index, self.layer_index, self.count)

    def restore(self, snapshot):
        self.layers, self.index, self.layer_index, self.count = snapshot
        self.invalidate()

    def begin_edit(self):
        self.undo_stack.append(self.snapshot())
        self.undo_stack = self.undo_stack[-50:]
        self.redo_stack.clear()

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
        pending = [(x, y)]
        while pending:
            px, py = pending.pop()
            if not (0 <= px < image.width() and 0 <= py < image.height()):
                continue
            if image.pixelColor(px, py).rgba() != old:
                continue
            image.setPixelColor(px, py, color)
            pending.extend(((px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)))

    def add_layer(self, shared=False):
        if len(self.layers) >= 16:
            return
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
        self.begin_edit()
        target = self.count if at_end else self.index + 1
        for layer in self.layers:
            if not layer['shared']:
                image = layer['images'][self.index]
                layer['images'].insert(target, QImage(image) if duplicate else blank_image(image.width(), image.height()))
        self.index = target
        self.count += 1
        self.invalidate()

    def delete_frame(self):
        self.begin_edit()
        if self.count == 1:
            for layer in self.layers:
                layer['images'] = [blank_image(self.image.width(), self.image.height())]
        else:
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

    def new_canvas(self, size):
        self.begin_edit()
        self.layers = [dict(name='Основа', shared=False, visible=True, locked=False, images=[blank_image(size, size)])]
        self.index = self.layer_index = 0
        self.count = 1
        self.invalidate()

    def sheet(self):
        width, height = self.image.width(), self.image.height()
        columns = min(16, self.count)
        output = blank_image(columns * width, math.ceil(self.count / columns) * height)
        painter = QPainter(output)
        for index in range(self.count):
            painter.drawImage((index % columns) * width, (index // columns) * height, self.composite(index))
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
                                              pingpong=self.pingpong, index=self.index, layer_index=self.layer_index),
                                         ensure_ascii=False), encoding='utf-8')

    @classmethod
    def load_project(cls, path):
        try:
            path = Path(path)
            if path.stat().st_size > 32 * 1024 * 1024:
                raise ValueError('Проект слишком большой (максимум 32 МБ).')
            data = json.loads(path.read_text(encoding='utf-8'))
            count, layers = data['count'], data['layers']
            if data['version'] != 1 or not isinstance(count, int) or not 1 <= count <= 512 or not 1 <= len(layers) <= 16:
                raise ValueError('Неверное число кадров или слоёв.')
            result = cls()
            size = None
            decoded = []
            for layer in layers:
                if any(not isinstance(layer[key], bool) for key in ('shared', 'visible', 'locked')):
                    raise ValueError('Неверные свойства слоя.')
                expected = 1 if layer['shared'] else count
                if len(layer['images']) != expected:
                    raise ValueError('Количество кадров в слоях не совпадает.')
                images = []
                for value in layer['images']:
                    raw = base64.b64decode(value, validate=True)
                    image = QImage.fromData(raw, 'PNG').convertToFormat(QImage.Format.Format_ARGB32)
                    if image.isNull() or max(image.width(), image.height()) > 256 or (size is not None and image.size() != size):
                        raise ValueError('Неверный размер слоя.')
                    size = image.size()
                    images.append(image)
                decoded.append(dict(name=str(layer['name'])[:80], shared=layer['shared'], visible=layer['visible'], locked=layer['locked'], images=images))
            result.layers, result.count = decoded, count
            result.fps = max(1, min(60, int(data.get('fps', 8))))
            result.pingpong = data.get('pingpong') is True
            result.index = max(0, min(count - 1, int(data.get('index', 0))))
            result.layer_index = max(0, min(len(decoded) - 1, int(data.get('layer_index', 0))))
            return result
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            raise ValueError(f'Не удалось открыть проект слоёв: {error}') from error
