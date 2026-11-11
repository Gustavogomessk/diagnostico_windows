"""Icones vetoriais (estilo linha, 24x24). Sem emojis."""
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap, QTransform
from PySide6.QtSvg import QSvgRenderer

PATHS = {
    "check": ["M20 6 9 17l-5-5"],
    "alert": ["M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z", "M12 9v4", "M12 17h.01"],
    "error": ["M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z", "M15 9l-6 6", "M9 9l6 6"],
    "loader": ["M12 2v4", "M12 18v4", "M4.9 4.9l2.8 2.8", "M16.2 16.2l2.8 2.8", "M2 12h4", "M18 12h4", "M4.9 19.1l2.8-2.8", "M16.2 7.8l2.8-2.8"],
    "pending": ["M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z", "M12 6v6l4 2"],
    "dashboard": ["M3 3h7v7H3z", "M14 3h7v7h-7z", "M14 14h7v7h-7z", "M3 14h7v7H3z"],
    "user": ["M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2", "M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8z"],
    "activity": ["M22 12h-4l-3 9L9 3l-3 9H2"],
    "cpu": ["M6 6h12v12H6z", "M9 9h6v6H9z", "M9 1v3", "M15 1v3", "M9 20v3", "M15 20v3", "M20 9h3", "M20 14h3", "M1 9h3", "M1 14h3"],
    "disk": ["M22 12H2", "M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z", "M6 16h.01", "M10 16h.01"],
    "memory": ["M4 7h16v10H4z", "M8 7v10", "M12 7v10", "M16 7v10"],
    "battery": ["M3 7h14a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1z", "M22 11v2"],
    "driver": ["M14 3h7v7", "M10 14 21 3", "M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"],
    "network": ["M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z", "M2 12h20", "M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20z"],
    "logs": ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z", "M14 2v6h6", "M8 13h8", "M8 17h8"],
    "tools": ["M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"],
    "upgrade": ["M23 6l-9.5 9.5-5-5L1 18", "M17 6h6v6"],
    "report": ["M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z", "M13 2v7h7"],
    "settings": ["M4 21v-7", "M4 10V3", "M12 21v-9", "M12 8V3", "M20 21v-5", "M20 12V3", "M1 14h6", "M9 8h6", "M17 16h6"],
}


def _svg(name: str, color: str) -> bytes:
    body = "".join(f'<path d="{d}"/>' for d in PATHS[name])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


def pixmap(name: str, color: str = "#1F5FBF", size: int = 20, angle: float = 0) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    QSvgRenderer(QByteArray(_svg(name, color))).render(p)
    p.end()
    if angle:
        pm = pm.transformed(QTransform().rotate(angle), Qt.TransformationMode.SmoothTransformation)
        w = pm.width()
        pm = pm.copy((w - size * 2) // 2, (pm.height() - size * 2) // 2, size * 2, size * 2)
    return pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def icon(name: str, color: str = "#1F5FBF", size: int = 20) -> QIcon:
    return QIcon(pixmap(name, color, size))
