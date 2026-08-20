"""Vector icons drawn with QPainter.

Shipping no image assets keeps the app a pure-Python install, and drawing the
icons means they pick up the current theme colour and stay crisp at any DPI.
"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui

_CACHE: dict[tuple[str, str, int], QtGui.QIcon] = {}


def _pen(painter: QtGui.QPainter, color: QtGui.QColor, width: float) -> None:
    pen = QtGui.QPen(color, width)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)


def _draw(name: str, painter: QtGui.QPainter, color: QtGui.QColor, size: int) -> None:
    """Draw ``name`` inside a 24x24 logical box (already scaled by the caller)."""
    _pen(painter, color, 1.9)

    if name == "library":
        painter.drawRoundedRect(QtCore.QRectF(3, 5, 18, 14), 2.5, 2.5)
        painter.drawPolyline([QtCore.QPointF(3, 15), QtCore.QPointF(9, 10),
                              QtCore.QPointF(13, 14), QtCore.QPointF(16, 11.5),
                              QtCore.QPointF(21, 16)])
        painter.setBrush(color)
        painter.drawEllipse(QtCore.QPointF(15.5, 9), 1.5, 1.5)
    elif name == "duplicates":
        painter.drawRoundedRect(QtCore.QRectF(3, 3, 13, 13), 2.5, 2.5)
        painter.drawRoundedRect(QtCore.QRectF(8, 8, 13, 13), 2.5, 2.5)
    elif name == "ranking":
        for index, height in enumerate((7.0, 12.0, 17.0)):
            x = 4.0 + index * 6.0
            painter.drawLine(QtCore.QPointF(x, 20), QtCore.QPointF(x, 20 - height))
        painter.drawLine(QtCore.QPointF(2, 20.8), QtCore.QPointF(22, 20.8))
    elif name == "import":
        painter.drawPolyline([QtCore.QPointF(4, 14), QtCore.QPointF(4, 20),
                              QtCore.QPointF(20, 20), QtCore.QPointF(20, 14)])
        painter.drawLine(QtCore.QPointF(12, 3), QtCore.QPointF(12, 15))
        painter.drawPolyline([QtCore.QPointF(7.5, 10.5), QtCore.QPointF(12, 15),
                              QtCore.QPointF(16.5, 10.5)])
    elif name == "export":
        painter.drawPolyline([QtCore.QPointF(4, 14), QtCore.QPointF(4, 20),
                              QtCore.QPointF(20, 20), QtCore.QPointF(20, 14)])
        painter.drawLine(QtCore.QPointF(12, 3), QtCore.QPointF(12, 15))
        painter.drawPolyline([QtCore.QPointF(7.5, 7.5), QtCore.QPointF(12, 3),
                              QtCore.QPointF(16.5, 7.5)])
    elif name == "settings":
        painter.drawEllipse(QtCore.QPointF(12, 12), 3.2, 3.2)
        for step in range(6):
            angle = step * 60.0
            transform = QtGui.QTransform().translate(12, 12).rotate(angle)
            painter.save()
            painter.setTransform(transform, True)
            painter.drawLine(QtCore.QPointF(0, -6.2), QtCore.QPointF(0, -8.6))
            painter.restore()
    elif name == "folder":
        painter.drawPolyline([QtCore.QPointF(3, 19), QtCore.QPointF(3, 6),
                              QtCore.QPointF(9, 6), QtCore.QPointF(11, 8.5),
                              QtCore.QPointF(21, 8.5), QtCore.QPointF(21, 19),
                              QtCore.QPointF(3, 19)])
    elif name == "search":
        painter.drawEllipse(QtCore.QPointF(10.5, 10.5), 6.0, 6.0)
        painter.drawLine(QtCore.QPointF(15, 15), QtCore.QPointF(20.5, 20.5))
    elif name == "trash":
        painter.drawLine(QtCore.QPointF(3.5, 6.5), QtCore.QPointF(20.5, 6.5))
        painter.drawPolyline([QtCore.QPointF(5.5, 6.5), QtCore.QPointF(6.5, 20.5),
                              QtCore.QPointF(17.5, 20.5), QtCore.QPointF(18.5, 6.5)])
        painter.drawPolyline([QtCore.QPointF(9, 6.5), QtCore.QPointF(9, 3.5),
                              QtCore.QPointF(15, 3.5), QtCore.QPointF(15, 6.5)])
        painter.drawLine(QtCore.QPointF(10, 10), QtCore.QPointF(10, 17))
        painter.drawLine(QtCore.QPointF(14, 10), QtCore.QPointF(14, 17))
    elif name == "check":
        _pen(painter, color, 2.6)
        painter.drawPolyline([QtCore.QPointF(4.5, 12.5), QtCore.QPointF(9.5, 17.5),
                              QtCore.QPointF(19.5, 6.5)])
    elif name == "cross":
        _pen(painter, color, 2.4)
        painter.drawLine(QtCore.QPointF(6, 6), QtCore.QPointF(18, 18))
        painter.drawLine(QtCore.QPointF(18, 6), QtCore.QPointF(6, 18))
    elif name == "refresh":
        rect = QtCore.QRectF(4, 4, 16, 16)
        painter.drawArc(rect, 60 * 16, 260 * 16)
        painter.setBrush(color)
        painter.drawPolygon([QtCore.QPointF(16.5, 2.5), QtCore.QPointF(20.5, 7),
                             QtCore.QPointF(14.5, 7.5)])
    elif name == "star":
        painter.setBrush(color)
        path = QtGui.QPainterPath()
        import math

        for index in range(10):
            radius = 9.0 if index % 2 == 0 else 3.9
            angle = math.radians(-90 + index * 36)
            point = QtCore.QPointF(
                12 + radius * math.cos(angle), 12 + radius * math.sin(angle)
            )
            path.lineTo(point) if index else path.moveTo(point)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "info":
        painter.drawEllipse(QtCore.QPointF(12, 12), 8.5, 8.5)
        painter.drawLine(QtCore.QPointF(12, 11), QtCore.QPointF(12, 16.5))
        painter.setBrush(color)
        painter.drawEllipse(QtCore.QPointF(12, 7.8), 1.1, 1.1)
    elif name == "app":
        painter.setBrush(color)
        painter.drawRoundedRect(QtCore.QRectF(2.5, 4.5, 13, 13), 2.5, 2.5)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QtCore.QRectF(8.5, 8.5, 13, 13), 2.5, 2.5)


def icon(name: str, color: QtGui.QColor | str, size: int = 24) -> QtGui.QIcon:
    """A themed icon, cached per (name, colour, size)."""
    colour = QtGui.QColor(color)
    key = (name, colour.name(), size)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    ratio = 2  # render at 2x so the icon stays sharp on HiDPI screens
    pixmap = QtGui.QPixmap(size * ratio, size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    # The pixmap already carries a device pixel ratio, so the painter works in
    # logical pixels -- scaling by `ratio` as well would draw the icon at twice
    # its box and clip everything but the top-left corner.
    painter.scale(size / 24.0, size / 24.0)
    _draw(name, painter, colour, size)
    painter.end()

    result = QtGui.QIcon(pixmap)
    _CACHE[key] = result
    return result


def app_icon(accent: str = "#4c8dff") -> QtGui.QIcon:
    """Window and taskbar icon, rendered at several sizes."""
    result = QtGui.QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QtGui.QPixmap(size, size)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.scale(size / 24.0, size / 24.0)
        painter.setBrush(QtGui.QColor("#1e2129"))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QtCore.QRectF(0, 0, 24, 24), 5, 5)
        _draw("app", painter, QtGui.QColor(accent), size)
        painter.end()
        result.addPixmap(pixmap)
    return result


# ---------------------------------------------------------------------------
# stylesheet assets
# ---------------------------------------------------------------------------

_ARROW_CACHE: dict[tuple[str, str], str] = {}


def arrow_asset(direction: str, color: QtGui.QColor | str, size: int = 9) -> str:
    """Render a small triangle to a PNG and return its path.

    Qt stylesheets can only draw spin-box and combo-box arrows from an image
    file. Generating them on demand keeps the app free of binary assets while
    still letting the arrows follow the current theme colour.
    """
    colour = QtGui.QColor(color)
    key = (direction, colour.name())
    cached = _ARROW_CACHE.get(key)
    if cached is not None and Path(cached).exists():
        return cached

    from ..config import cache_dir

    folder = cache_dir() / "ui"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"arrow-{direction}-{colour.name().lstrip('#')}.png"

    scale = 2
    pixmap = QtGui.QPixmap(size * scale, size * scale)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    painter.scale(scale, scale)
    painter.setBrush(colour)
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    span, inset = size - 2.0, 1.0
    points = {
        "down": [(inset, inset + span * 0.28), (inset + span, inset + span * 0.28),
                 (inset + span / 2, inset + span * 0.78)],
        "up": [(inset, inset + span * 0.72), (inset + span, inset + span * 0.72),
               (inset + span / 2, inset + span * 0.22)],
    }[direction]
    painter.drawPolygon([QtCore.QPointF(x, y) for x, y in points])
    painter.end()
    pixmap.save(str(path), "PNG")

    resolved = str(path)
    _ARROW_CACHE[key] = resolved
    return resolved


def stylesheet_assets(palette) -> dict[str, str]:
    """Image paths the stylesheet needs, generated for the current palette."""
    return {
        "arrow_down": arrow_asset("down", palette.text_dim),
        "arrow_up": arrow_asset("up", palette.text_dim),
    }
