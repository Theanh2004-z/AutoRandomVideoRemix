"""Render the code-native REMIX mark into a multi-resolution Windows icon."""
from pathlib import Path
import struct


def build_icon(project_root=None):
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    root = Path(project_root or Path(__file__).resolve().parents[1])
    renderer = QSvgRenderer(str(root / 'assets' / 'remix.svg'))
    if not renderer.isValid():
        raise ValueError('Invalid REMIX SVG')
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    frames = []
    for size in sizes:
        image = QImage(size, size, QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        if not image.save(buffer, 'PNG'):
            raise RuntimeError('Could not encode icon frame')
        frames.append(bytes(data))
    offset = 6 + 16 * len(frames)
    entries = []
    for size, frame in zip(sizes, frames):
        entries.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(frame), offset))
        offset += len(frame)
    target = root / 'assets' / 'remix.ico'
    target.write_bytes(struct.pack('<HHH', 0, 1, len(frames)) + b''.join(entries) + b''.join(frames))
    return target


if __name__ == '__main__':
    print(build_icon())
