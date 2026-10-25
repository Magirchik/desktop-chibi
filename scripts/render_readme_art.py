"""Render small README decorations from the application's original pixel sprites."""
import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication
from desktop_chibi.pixel_ui import bunny_frame

INK, PAPER, PINK, LILAC, MINT = '#53406f', '#fff7fd', '#f2a9d0', '#c4a8ed', '#b9e0d3'


def rect(painter, x, y, width, height, color):
    painter.fillRect(x, y, width, height, QColor(color))


def heart(painter, x, y, color=PINK):
    for row, pixels in enumerate(('0110110', '1111111', '1111111', '0111110', '0011100', '0001000')):
        for column, pixel in enumerate(pixels):
            if pixel == '1':
                rect(painter, x + column, y + row, 1, 1, color)


def window(painter, x, y, width, height, accent=LILAC):
    rect(painter, x, y, width, height, INK)
    rect(painter, x + 1, y + 1, width - 2, height - 2, PAPER)
    rect(painter, x + 1, y + 1, width - 2, 5, accent)
    rect(painter, x + 1, y + 6, width - 2, 1, INK)
    for dx in (5, 10):
        rect(painter, x + width - dx, y + 2, 3, 3, INK)
        rect(painter, x + width - dx + 1, y + 3, 1, 1, PAPER)


def canvas():
    image = QImage(256, 56, QImage.Format.Format_ARGB32)
    image.fill(QColor(PAPER))
    return image, QPainter(image)


def save(image, painter, name):
    painter.end()
    target = ROOT / 'docs/images' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    scaled = image.scaled(image.width() * 4, image.height() * 4,
                          Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
    if not scaled.save(str(target)):
        raise RuntimeError(f'Could not save {target}')
    print(target.relative_to(ROOT))


def main():
    app = QApplication.instance() or QApplication([])
    image, p = canvas()
    # One bunny, two tiny windows and a few hearts; every edge is on the pixel grid.
    for x in range(12, 244, 4):
        rect(p, x, 46, 2, 1, LILAC)
    window(p, 36, 11, 47, 34, PINK)
    heart(p, 56, 26)
    window(p, 172, 11, 47, 34, MINT)
    for x, height in ((185, 5), (190, 9), (195, 13), (200, 7)):
        rect(p, x, 39 - height, 3, height, LILAC)
    p.drawPixmap(112, 12, bunny_frame())
    heart(p, 97, 15, LILAC)
    heart(p, 151, 23)
    save(image, p, 'pixel-desktop.png')

    image, p = canvas()
    for x, state, step in ((42, 'idle', 0), (112, 'walk', 2), (182, 'land', 0)):
        window(p, x - 6, 4, 44, 48, PINK if state == 'walk' else LILAC)
        p.drawPixmap(x, 15, bunny_frame(step=step, state=state))
    for x in (94, 164):
        rect(p, x - 4, 29, 7, 1, INK)
        for dx, dy in ((0, -2), (1, -1), (2, 0), (1, 1), (0, 2)):
            rect(p, x + dx, 29 + dy, 1, 1, INK)
    heart(p, 14, 25)
    heart(p, 235, 25)
    save(image, p, 'pixel-workshop.png')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
