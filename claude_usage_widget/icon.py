"""Tray/app icon, drawn at runtime so the repo stays free of binary assets.

Run `python -m claude_usage_widget.icon app.ico` to emit a Windows .ico for
PyInstaller. The .ico is written as a single PNG-in-ICO entry, which every
supported Windows version reads.
"""

from __future__ import annotations

import struct
import sys

from PySide6.QtCore import QBuffer, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

BACKDROP = QColor("#1F1F22")
BARS = ("#2F80F5", "#E0A32E", "#E5484D")


def make_pixmap(size: int = 256) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    radius = size * 0.22
    painter.setPen(Qt.NoPen)
    painter.setBrush(BACKDROP)
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    # Three stacked bars at increasing fill, echoing the widget itself.
    margin = size * 0.18
    width = size - margin * 2
    height = size * 0.1
    gap = size * 0.11
    top = margin + size * 0.06
    for index, colour in enumerate(BARS):
        y = top + index * (height + gap)
        painter.setBrush(QColor(255, 255, 255, 38))
        painter.drawRoundedRect(QRectF(margin, y, width, height), height / 2, height / 2)
        painter.setBrush(QColor(colour))
        fill = width * (0.35 + 0.28 * index)
        painter.drawRoundedRect(QRectF(margin, y, fill, height), height / 2, height / 2)

    painter.end()
    return pixmap


def make_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(make_pixmap(size))
    return icon


def write_ico(path: str, size: int = 256) -> None:
    # QBuffer() manages its own storage. Passing QBuffer(QByteArray()) hands it
    # a temporary that Python frees immediately, and the next write segfaults.
    buffer = QBuffer()
    buffer.open(QBuffer.WriteOnly)
    make_pixmap(size).save(buffer, "PNG")
    png = bytes(buffer.data())
    buffer.close()
    if not png:
        raise RuntimeError("Qt produced no PNG data for the icon.")

    # ICONDIR + one ICONDIRENTRY; width/height of 0 means 256.
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack(
        "<BBBBHHII",
        size % 256,
        size % 256,
        0,
        0,
        1,
        32,
        len(png),
        struct.calcsize("<HHH") + struct.calcsize("<BBBBHHII"),
    )
    with open(path, "wb") as handle:
        handle.write(header + entry + png)


if __name__ == "__main__":  # pragma: no cover - build-time helper
    from PySide6.QtGui import QGuiApplication

    target = sys.argv[1] if len(sys.argv) > 1 else "app.ico"
    app = QGuiApplication(sys.argv[:1])
    write_ico(target)
    print(f"wrote {target}")
