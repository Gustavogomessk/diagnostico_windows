from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.labels import humanize
from ..core.models import STATUS_LABELS, CheckResult, Status
from . import icons, theme

STATUS_STYLE = {
    Status.PENDING: ("pending", "#9CA3AF"), Status.RUNNING: ("loader", theme.PRIMARY), Status.OK: ("check", theme.OK),
    Status.ALERT: ("alert", theme.WARN), Status.ERROR: ("error", theme.ERR),
}


# ---------- execucao em segundo plano ----------
class _Sig(QObject):
    done = Signal(object, object)


class _Task(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn, self.sig = fn, _Sig()
        self.setAutoDelete(False)

    def run(self):
        try:
            self.sig.done.emit(self.fn(), None)
        except Exception as e:  # noqa: BLE001
            self.sig.done.emit(None, e)


_live: set = set()


def run_async(fn, callback):
    """Executa fn fora da thread da interface; callback(resultado, erro) roda na thread da UI."""
    t = _Task(fn)
    _live.add(t)

    def fin(r, e):
        _live.discard(t)
        callback(r, e)
    t.sig.done.connect(fin)
    QThreadPool.globalInstance().start(t)


# ---------- componentes ----------
def label(text="", obj="", wrap=True) -> QLabel:
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


def button(text, primary=False, icon_name=None) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setObjectName("primary")
    if icon_name:
        b.setIcon(icons.icon(icon_name, "#FFFFFF" if primary else theme.PRIMARY, 18))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class Card(QFrame):
    def __init__(self, title: str = ""):
        super().__init__()
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 14, 16, 14)
        self.lay.setSpacing(8)
        if title:
            self.lay.addWidget(label(title, "h2"))


class StatusIcon(QLabel):
    def __init__(self, size=22):
        super().__init__()
        self._size, self._angle, self._status = size, 0, Status.PENDING
        self.setFixedSize(size + 4, size + 4)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._spin)
        self.set_status(Status.PENDING)

    def _spin(self):
        self._angle = (self._angle + 30) % 360
        self.setPixmap(icons.pixmap("loader", theme.PRIMARY, self._size, self._angle))

    def set_status(self, st: Status):
        self._status = st
        name, color = STATUS_STYLE[st]
        if st == Status.RUNNING:
            self._timer.start(80)
        else:
            self._timer.stop()
            self.setPixmap(icons.pixmap(name, color, self._size))


class CheckRow(QFrame):
    def __init__(self, key: str, title: str, on_run=None):
        super().__init__()
        self.key = key
        self.setObjectName("card")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.icon = StatusIcon()
        lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        self.title = label(title, "h2")
        self.desc = label(STATUS_LABELS[Status.PENDING], "muted")
        col.addWidget(self.title)
        col.addWidget(self.desc)
        lay.addLayout(col, 1)
        self.state = label("", "muted", wrap=False)
        lay.addWidget(self.state)
        self.btn = None
        if on_run:
            self.btn = button("Executar")
            self.btn.clicked.connect(lambda: on_run(key))
            lay.addWidget(self.btn)

    def set_result(self, r: CheckResult):
        self.icon.set_status(r.status)
        self.state.setText(STATUS_LABELS[r.status])
        lines = [r.summary or STATUS_LABELS[r.status]]
        if r.status == Status.ERROR and r.reason:
            lines.append("Motivo: " + r.reason)
        for a in r.admin_required:
            lines.append("Requer administrador: " + a)
        self.desc.setText("\n".join(lines))
        if self.btn:
            self.btn.setEnabled(r.status != Status.RUNNING)


class BarChart(QWidget):
    def __init__(self, items=None, unit="", color=theme.PRIMARY):
        super().__init__()
        self.items, self.unit, self.color = items or [], unit, QColor(color)
        self.setMinimumHeight(30 * max(len(self.items), 1) + 8)

    def set_items(self, items, unit=None):
        self.items = items
        if unit is not None:
            self.unit = unit
        self.setMinimumHeight(30 * max(len(items), 1) + 8)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        mx = max([v for _, v in self.items if v is not None] + [0.0001])
        label_w, val_w = 240, 90
        bar_w = max(self.width() - label_w - val_w - 10, 20)
        for i, (name, val) in enumerate(self.items):
            y = 4 + i * 30
            p.setPen(QColor(theme.TEXT))
            p.drawText(0, y, label_w - 8, 22, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, str(name))
            p.setBrush(QColor("#E5EAF3"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(label_w, y + 5, bar_w, 12, 5, 5)
            if val is not None:
                p.setBrush(self.color)
                p.drawRoundedRect(label_w, y + 5, max(int(bar_w * val / mx), 3), 12, 5, 5)
            p.setPen(QColor(theme.MUTED))
            txt = "N/D" if val is None else f"{val:g} {self.unit}".strip()
            p.drawText(label_w + bar_w + 8, y, val_w, 22, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, txt)


# ---------- renderizacao generica de dados ----------
def fmt_cell(v) -> str:
    if v is None or v == "":
        return "Não disponível"
    if isinstance(v, bool):
        return "Sim" if v else "Não"
    if isinstance(v, list):
        return ", ".join(fmt_cell(x) for x in v)
    return str(v)


def _fit(t: QTableWidget, n_rows: int, cap=380):
    hh = t.horizontalHeader()
    hh.setFixedHeight(34)
    t.verticalHeader().setDefaultSectionSize(30)
    t.resizeColumnsToContents()
    fm = t.fontMetrics()
    for i in range(t.columnCount()):
        head = t.horizontalHeaderItem(i)
        need = fm.horizontalAdvance(head.text() if head else "") + 44
        t.setColumnWidth(i, min(max(t.columnWidth(i) + 16, need), 340))
    t.resizeRowsToContents()
    for i in range(n_rows):
        t.setRowHeight(i, max(t.rowHeight(i) + 8, 30))
    head_h = 0 if hh.isHidden() else 34
    t.setFixedHeight(min(max(head_h + sum(t.rowHeight(i) for i in range(n_rows)) + 8, 40), cap))


def make_table(rows: list[dict], cols: list[str]) -> QTableWidget:
    t = QTableWidget(len(rows), len(cols))
    t.setHorizontalHeaderLabels([humanize(c) for c in cols])
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setAlternatingRowColors(True)
    t.setWordWrap(True)
    for r, row in enumerate(rows):
        for c, col in enumerate(cols):
            t.setItem(r, c, QTableWidgetItem(fmt_cell(row.get(col))))
    _fit(t, len(rows))
    return t


def kv_table(d: dict) -> QTableWidget:
    items = [(k, v) for k, v in d.items() if not isinstance(v, (dict, list)) or (isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v))]
    t = QTableWidget(len(items), 2)
    t.horizontalHeader().setVisible(False)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setWordWrap(True)
    for i, (k, v) in enumerate(items):
        a = QTableWidgetItem(humanize(k))
        a.setForeground(QColor(theme.MUTED))
        t.setItem(i, 0, a)
        t.setItem(i, 1, QTableWidgetItem(fmt_cell(v)))
    t.setColumnWidth(0, 230)
    t.horizontalHeader().setStretchLastSection(True)
    t.verticalHeader().setDefaultSectionSize(30)
    t.resizeRowsToContents()
    for i in range(len(items)):
        t.setRowHeight(i, max(t.rowHeight(i) + 6, 30))
    t.setFixedHeight(sum(t.rowHeight(i) for i in range(len(items))) + 8)
    return t


def render_value(title: str, value, skip=()) -> list[QWidget]:
    out: list[QWidget] = []
    head = label(humanize(title), "h2") if title else None
    if isinstance(value, dict):
        scal = {k: v for k, v in value.items() if k not in skip and not isinstance(v, (dict, list)) or
                (isinstance(v, list) and v and all(not isinstance(x, (dict, list)) for x in v))}
        if head:
            out.append(head)
        if scal:
            out.append(kv_table(scal))
        for k, v in value.items():
            if k in skip or k in scal:
                continue
            if isinstance(v, (dict, list)) and v:
                out += render_value(k, v, skip)
    elif isinstance(value, list):
        if not value:
            return out
        if all(isinstance(x, dict) for x in value):
            nested = any(isinstance(v, (dict, list)) and v and not all(not isinstance(i, (dict, list)) for i in (v if isinstance(v, list) else [v]))
                         for x in value for v in x.values())
            if head:
                out.append(head)
            if not nested:
                cols = list(dict.fromkeys(k for x in value for k in x))
                out.append(make_table(value, cols))
            else:
                for x in value:
                    name = x.get("modelo") or x.get("nome") or x.get("dispositivo") or ""
                    out += render_value(str(name), x, skip) if name else render_value("", x, skip)
        else:
            if head:
                out.append(head)
            out.append(label(fmt_cell(value)))
    else:
        out.append(label(f"{humanize(title)}: {fmt_cell(value)}"))
    return out


class ResultView(QScrollArea):
    """Mostra um CheckResult: status, motivo de falha, evidencias e dados detalhados."""

    def __init__(self, key: str, decorator=None, skip=()):
        super().__init__()
        self.key, self.decorator, self.skip = key, decorator, skip
        self.setWidgetResizable(True)
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 0, 8, 0)
        self.lay.setSpacing(10)
        self.setWidget(self.body)
        self.show_message("Execute o diagnóstico para ver os resultados.")

    def _clear(self):
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def show_message(self, text: str):
        self._clear()
        self.lay.addWidget(label(text, "muted"))
        self.lay.addStretch(1)

    def show_result(self, r: CheckResult):
        self._clear()
        head = Card()
        row = QHBoxLayout()
        ic = StatusIcon()
        ic.set_status(r.status)
        row.addWidget(ic)
        row.addWidget(label(f"{STATUS_LABELS[r.status]}: {r.summary}" if r.summary else STATUS_LABELS[r.status], "h2"), 1)
        head.lay.addLayout(row)
        if r.reason:
            head.lay.addWidget(label("Motivo: " + r.reason))
        for a in r.admin_required:
            head.lay.addWidget(label("Requer administrador: " + a, "muted"))
        for e in r.evidence:
            head.lay.addWidget(label(e, "muted"))
        for p in r.problems:
            head.lay.addWidget(label("Problema: " + p))
        self.lay.addWidget(head)
        if self.decorator:
            for w in self.decorator(r):
                self.lay.addWidget(w)
        if r.data:
            for k, v in r.data.items():
                if k in self.skip:
                    continue
                card = Card()
                for w in render_value(k, v, ()):
                    card.lay.addWidget(w)
                if card.lay.count():
                    self.lay.addWidget(card)
        self.lay.addStretch(1)
