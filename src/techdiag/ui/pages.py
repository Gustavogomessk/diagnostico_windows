from __future__ import annotations

import os
import webbrowser

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QSpinBox, QTabWidget,
                               QTextEdit, QVBoxLayout, QWidget, QFileDialog)

from ..collectors.hardware import detect_machine_model
from ..collectors.temp_files import temp_roots
from ..core.config import TOOL_FILES
from ..core.models import STATUS_LABELS, CheckResult, Session, Status, validate_identification, now_iso
from ..core.privileges import relaunch_as_admin
from ..infra.powershell import PlatformUnsupported
from ..infra.tools import ToolError, inspect_tool
from ..reports.classify import DEFS
from ..reports.pdf_builder import safe_filename
from ..services.actions import ConfirmationRequired, analyze_advanced
from ..services.price_search import estimate_total, search_price
from ..services.upgrade_analysis import analyze_upgrades
from . import theme
from .widgets import (BarChart, Card, CheckRow, ResultView, StatusIcon, button, fmt_cell, label, make_table, run_async)


def page_shell(title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout, QHBoxLayout]:
    w = QWidget()
    w.setObjectName("page")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(24, 20, 24, 16)
    lay.setSpacing(12)
    top = QHBoxLayout()
    col = QVBoxLayout()
    col.addWidget(label(title, "h1"))
    if subtitle:
        col.addWidget(label(subtitle, "muted"))
    top.addLayout(col, 1)
    lay.addLayout(top)
    return w, lay, top


def msg_error(parent, text):
    QMessageBox.warning(parent, "Atenção", text)


# ------------------------------------------------------------------ Identificacao
class IdentificationPage(QWidget):
    started = Signal(object)

    def __init__(self, state):
        super().__init__()
        self.state = state
        shell, lay, _ = page_shell("Identificação do atendimento", "Obrigatória antes de qualquer diagnóstico.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(shell)
        card = Card()
        form = QFormLayout()
        form.setSpacing(10)
        self.client, self.ticket, self.tech, self.machine = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.client.setPlaceholderText("Nome do cliente ou empresa")
        self.ticket.setPlaceholderText("Número do ticket")
        self.tech.setPlaceholderText("Técnico responsável")
        self.machine.setPlaceholderText("Detectando modelo da máquina...")
        form.addRow("Cliente / empresa *", self.client)
        form.addRow("Ticket *", self.ticket)
        form.addRow("Técnico responsável *", self.tech)
        form.addRow("Máquina (editável)", self.machine)
        card.lay.addLayout(form)
        self.err = label("", "")
        self.err.setStyleSheet(f"color:{theme.ERR}")
        card.lay.addWidget(self.err)
        self.btn = button("Iniciar diagnóstico", primary=True, icon_name="activity")
        self.btn.clicked.connect(self.start)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.btn)
        card.lay.addLayout(row)
        lay.addWidget(card)
        lay.addStretch(1)
        run_async(detect_machine_model, self._machine_ready)

    def _machine_ready(self, r, e):
        if r and not self.machine.text():
            self.machine.setText(r)

    def start(self):
        errs = validate_identification(self.client.text(), self.ticket.text(), self.tech.text(), self.machine.text())
        self.err.setText("\n".join(errs))
        if errs:
            return
        s = Session(self.client.text().strip(), self.ticket.text().strip(), self.tech.text().strip(), self.machine.text().strip())
        self.started.emit(s)


# ------------------------------------------------------------------ Dashboard / Diagnostico
class DashboardPage(QWidget):
    def __init__(self, state, titles: dict, go_ident):
        super().__init__()
        self.state, self.rows = state, {}
        shell, lay, top = page_shell("Dashboard", "Visão geral das verificações do diagnóstico.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(shell)
        self.info = label("Nenhuma sessão iniciada. Preencha a identificação para começar.", "banner")
        lay.addWidget(self.info)
        cards = QHBoxLayout()
        self.c_class, self.c_done, self.c_alert = Card("Classificação geral"), Card("Concluídas"), Card("Alertas / erros")
        self.v_class, self.v_done, self.v_alert = label("-", "h1"), label("0", "h1"), label("0", "h1")
        for c, v in ((self.c_class, self.v_class), (self.c_done, self.v_done), (self.c_alert, self.v_alert)):
            c.lay.addWidget(v)
            cards.addWidget(c)
        lay.addLayout(cards)
        self.bar = QProgressBar()
        self.bar.setMaximum(len(titles))
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        from PySide6.QtWidgets import QScrollArea
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setSpacing(8)
        for k, t in titles.items():
            r = CheckRow(k, t)
            self.rows[k] = r
            bl.addWidget(r)
        bl.addStretch(1)
        sc.setWidget(body)
        lay.addWidget(sc, 1)
        self.n = len(titles)

    def on_session(self):
        s = self.state.session
        self.info.setText(f"Cliente: {s.client}   |   Ticket: {s.ticket}   |   Técnico: {s.technician}   |   Máquina: {s.machine}" if s else "")
        for r in self.rows.values():
            r.set_result(CheckResult(r.key, "", Status.PENDING))
        self.refresh()

    def on_result(self, res: CheckResult):
        if res.key in self.rows:
            self.rows[res.key].set_result(res)
        self.refresh()

    def refresh(self):
        rs = [r for r in self.state.results.values() if r.key in self.rows]
        done = [r for r in rs if r.status in (Status.OK, Status.ALERT, Status.ERROR)]
        self.bar.setValue(len(done))
        self.v_done.setText(f"{len(done)} / {self.n}")
        self.v_alert.setText(f"{sum(r.status == Status.ALERT for r in rs)} / {sum(r.status == Status.ERROR for r in rs)}")
        if done and len(done) == self.n:
            cl, _ = self.state.overall()
            self.v_class.setText(cl)
            self.v_class.setToolTip(DEFS[cl])
            col = {"Excelente": theme.OK, "Bom": theme.PRIMARY, "Atenção": theme.WARN, "Crítico": theme.ERR}[cl]
            self.v_class.setStyleSheet(f"color:{col}")
        else:
            self.v_class.setText("Em análise" if rs else "-")
            self.v_class.setStyleSheet("")


class DiagnosticPage(QWidget):
    def __init__(self, state, titles: dict):
        super().__init__()
        self.state, self.rows = state, {}
        shell, lay, top = page_shell("Diagnóstico", "Cada verificação é independente e pode ser executada isoladamente.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(shell)
        self.run_all = button("Executar todas", primary=True, icon_name="activity")
        self.run_all.clicked.connect(self._all)
        top.addWidget(self.run_all)
        from PySide6.QtWidgets import QScrollArea
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setSpacing(8)
        for k, t in titles.items():
            r = CheckRow(k, t, on_run=self._one)
            self.rows[k] = r
            bl.addWidget(r)
        bl.addStretch(1)
        sc.setWidget(body)
        lay.addWidget(sc, 1)

    def _guard(self) -> bool:
        if not self.state.session:
            msg_error(self, "Preencha a identificação antes de iniciar o diagnóstico.")
            return False
        return True

    def _all(self):
        if self._guard():
            self.state.run_all()

    def _one(self, key):
        if self._guard():
            self.state.run_one(key)

    def on_result(self, res):
        if res.key in self.rows:
            self.rows[res.key].set_result(res)


# ------------------------------------------------------------------ Paginas de resultado
class ResultPage(QWidget):
    """Pagina com uma ou mais abas, cada uma exibindo o resultado de uma verificacao."""

    def __init__(self, state, title, subtitle, tabs: list[tuple[str, str]], decorators: dict | None = None, skips: dict | None = None):
        super().__init__()
        self.state, self.views = state, {}
        shell, lay, top = page_shell(title, subtitle)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(shell)
        self.rerun = button("Executar novamente", icon_name="activity")
        self.rerun.clicked.connect(self._rerun)
        top.addWidget(self.rerun)
        decorators, skips = decorators or {}, skips or {}
        if len(tabs) == 1:
            k = tabs[0][0]
            v = ResultView(k, decorators.get(k), skips.get(k, ()))
            self.views[k] = v
            lay.addWidget(v, 1)
        else:
            self.tabw = QTabWidget()
            for k, name in tabs:
                v = ResultView(k, decorators.get(k), skips.get(k, ()))
                self.views[k] = v
                self.tabw.addTab(v, name)
            lay.addWidget(self.tabw, 1)

    def _rerun(self):
        if not self.state.session:
            msg_error(self, "Preencha a identificação antes de iniciar o diagnóstico.")
            return
        keys = list(self.views)
        if len(keys) > 1 and hasattr(self, "tabw"):
            keys = [keys[self.tabw.currentIndex()]]
        self.state.orch.start(keys)

    def on_result(self, res: CheckResult):
        v = self.views.get(res.key)
        if not v:
            return
        if res.status in (Status.PENDING, Status.RUNNING):
            v.show_message("Analisando..." if res.status == Status.RUNNING else "Aguardando.")
        else:
            v.show_result(res)


def memory_decorator(state, parent_fn):
    def deco(r: CheckResult):
        ws: list[QWidget] = []
        cats = r.data.get("categorias") or []
        card = Card("Consumo de memória por categoria")
        if cats:
            card.lay.addWidget(BarChart([(c["nome"], c["gb"]) for c in cats], "GB"))
        else:
            card.lay.addWidget(label("Categorias detalhadas indisponíveis (apenas Windows).", "muted"))
        top = r.data.get("top_processos") or []
        if top:
            card.lay.addWidget(label("Processos com maior consumo", "h2"))
            card.lay.addWidget(BarChart([(f"{p['nome']} ({p['pid']})", p["memoria_mb"]) for p in top[:8]], "MB"))
        card.lay.addWidget(label("Memória em cache e Standby é usada pelo próprio Windows para acelerar o sistema e não indica problema por si só. "
                                 "Mapped File e Driver Locked só são exibidos no RAMMap (botão abaixo).", "muted"))
        row = QHBoxLayout()
        for text, fn in (("Abrir RAMMap", "open_rammap"), ("Esvaziar Standby List", "standby"), ("Agendar teste de memória", "mdsched")):
            b = button(text)
            b.clicked.connect(lambda _=False, f=fn: parent_fn(f))
            row.addWidget(b)
        row.addStretch(1)
        card.lay.addLayout(row)
        ws.append(card)
        return ws
    return deco


def drivers_decorator(state):
    def deco(r: CheckResult):
        achados = r.data.get("achados") or []
        card = Card("Dispositivos que exigem atenção")
        if not achados:
            card.lay.addWidget(label("Nenhum dispositivo com erro, driver ausente ou sem assinatura.", "muted"))
            return [card]
        card.lay.addWidget(label("Os links abrem a busca em domínios oficiais (ou DriverPack, marcado como terceiro). Confirme modelo e versão antes de baixar; "
                                 "nada é baixado ou instalado automaticamente.", "muted"))
        cols = ["dispositivo", "fabricante", "hardware_id", "versao_instalada", "data_driver", "problema", "assinatura", "versao_recomendada"]
        t = make_table(achados, cols)
        t.setColumnCount(len(cols) + 2)
        t.setHorizontalHeaderLabels([*(c.replace("_", " ").capitalize() for c in cols), "Fonte", ""])
        from ..core.labels import humanize
        t.setHorizontalHeaderLabels([*(humanize(c) for c in cols), "Fonte", "Download"])
        for i, a in enumerate(achados):
            srcs = a.get("fontes") or []
            t.setItem(i, len(cols), __import__("PySide6.QtWidgets", fromlist=["QTableWidgetItem"]).QTableWidgetItem(
                (srcs[0]["nome"] if srcs else "Sem fonte identificada")))
            b = button("Baixar driver")
            b.setEnabled(bool(a.get("url")))
            b.clicked.connect(lambda _=False, u=a.get("url"): u and webbrowser.open(u))
            t.setCellWidget(i, len(cols) + 1, b)
            t.setRowHeight(i, max(t.rowHeight(i), 36))
        t.resizeColumnsToContents()
        t.setFixedHeight(min(t.horizontalHeader().sizeHint().height() + sum(t.rowHeight(i) for i in range(len(achados))) + 6, 420))
        card.lay.addWidget(t)
        return [card]
    return deco


def sensors_decorator(state):
    def deco(r: CheckResult):
        cpu = (r.data or {}).get("cpu")
        card = Card("Temperatura da CPU x limite oficial do fabricante")
        if not cpu:
            card.lay.addWidget(label("Sem leitura de temperatura. " + (r.reason or ""), "muted"))
            return [card]
        lim = cpu.get("limite_oficial_c")
        items = [("Temperatura atual", cpu["atual_c"]), ("Pico durante a leitura", cpu["max_c"])]
        if lim is not None:
            items.append(("Limite oficial do fabricante", lim))
        col = {"Crítico": theme.ERR, "Atenção": theme.WARN}.get(cpu.get("avaliacao"), theme.PRIMARY)
        card.lay.addWidget(BarChart(items, "°C", col))
        txt = f"{cpu.get('modelo') or 'CPU'}  |  Avaliação: {cpu.get('avaliacao')}"
        if cpu.get("margem_c") is not None:
            txt += f"  |  Margem até o limite: {cpu['margem_c']} °C"
        card.lay.addWidget(label(txt, "h2"))
        card.lay.addWidget(label("Fonte do limite: " + fmt_cell(cpu.get("fonte_limite")) + (f"  (confiança: {cpu.get('confianca_limite')})" if cpu.get("confianca_limite") else ""), "muted"))
        b = button("Abrir ficha oficial do fabricante")
        b.clicked.connect(lambda _=False, u=cpu.get("url_fonte"): u and webbrowser.open(u))
        card.lay.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        return [card]
    return deco


def network_decorator(state, speed_fn):
    def deco(r: CheckResult):
        card = Card("Teste de velocidade (Ookla Speedtest CLI)")
        sp = (r.data or {}).get("speedtest")
        if sp:
            card.lay.addWidget(BarChart([("Download", sp.get("download_mbps")), ("Upload", sp.get("upload_mbps"))], "Mbps"))
            card.lay.addWidget(BarChart([("Latência", sp.get("latencia_ms")), ("Jitter", sp.get("jitter_ms"))], "ms", theme.WARN))
            card.lay.addWidget(label(f"Servidor: {fmt_cell(sp.get('servidor'))}   |   Operadora: {fmt_cell(sp.get('provedor'))}   |   IP público: {fmt_cell(sp.get('ip_publico'))}"
                                     f"   |   Perda de pacotes: {fmt_cell(sp.get('perda_pct'))}%"))
        else:
            card.lay.addWidget(label("Teste ainda não executado. Ele envia o IP público ao serviço Ookla e exige sua autorização.", "muted"))
        b = button("Executar teste de velocidade", primary=True, icon_name="network")
        b.clicked.connect(speed_fn)
        card.lay.addWidget(b)
        return [card]
    return deco


# ------------------------------------------------------------------ Central de Acoes
class UpdateDialog(QDialog):
    def __init__(self, parent, candidates: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("Atualizar Máquina - atualizações recomendadas")
        self.resize(720, 460)
        lay = QVBoxLayout(self)
        lay.addWidget(label("Selecione os itens. O programa apenas abre as páginas de download; nenhum driver é instalado automaticamente.", "muted"))
        self.list = QListWidget()
        for c in candidates:
            it = QListWidgetItem(f"{c['dispositivo']}  -  {c['problema']}  [{c.get('hardware_id') or 'sem Hardware ID'}]")
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, c.get("url"))
            self.list.addItem(it)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        ok = button("Abrir páginas selecionadas", primary=True)
        ok.clicked.connect(self.open_selected)
        close = button("Fechar")
        close.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(close)
        row.addWidget(ok)
        lay.addLayout(row)

    def open_selected(self):
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.CheckState.Checked and it.data(Qt.ItemDataRole.UserRole):
                webbrowser.open(it.data(Qt.ItemDataRole.UserRole))


class ActionsPage(QWidget):
    def __init__(self, state, memory_handler):
        super().__init__()
        self.state, self.memory_handler = state, memory_handler
        shell, lay, _ = page_shell("Central de Ações e Manutenção", "Nenhuma ação é executada sem a sua confirmação.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(shell)
        from PySide6.QtWidgets import QScrollArea
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setSpacing(12)
        # atualizar
        c1 = Card("Atualizar Máquina")
        c1.lay.addWidget(label("Lista drivers ausentes, com erro, sem assinatura ou possivelmente desatualizados, com links de download.", "muted"))
        b = button("Atualizar Máquina", primary=True, icon_name="driver")
        b.clicked.connect(self.update_machine)
        c1.lay.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        bl.addWidget(c1)
        # memoria
        c2 = Card("Limpeza de memória")
        c2.lay.addWidget(label("O Windows usa memória em cache/Standby de propósito. Esvaziar a Standby List não aumenta a memória real e pode reduzir o desempenho temporariamente.", "muted"))
        row = QHBoxLayout()
        for text, fn in (("Abrir RAMMap", "open_rammap"), ("Esvaziar Standby List", "standby"), ("Agendar teste de memória", "mdsched")):
            bb = button(text)
            bb.clicked.connect(lambda _=False, f=fn: self.memory_handler(f))
            row.addWidget(bb)
        row.addStretch(1)
        c2.lay.addLayout(row)
        bl.addWidget(c2)
        # temporarios
        self.c3 = Card("Limpar arquivos temporários")
        self.temp_boxes: dict[str, QCheckBox] = {}
        for r in temp_roots():
            cb = QCheckBox(f"{r['rotulo']}  ({r['caminho']})" + ("  - mesmo caminho de outro item" if r["duplicado_de"] else ""))
            cb.setEnabled(not r["duplicado_de"])
            cb.setChecked(not r["duplicado_de"])
            self.temp_boxes[r["id"]] = cb
            self.c3.lay.addWidget(cb)
        self.c3.lay.addWidget(label("Somente %TEMP%, %LOCALAPPDATA%\\Temp e C:\\Windows\\Temp são tocados. Arquivos em uso são ignorados, nunca forçados.", "muted"))
        self.temp_btn = button("Limpar arquivos temporários", icon_name="tools")
        self.temp_btn.clicked.connect(self.clean_temp)
        self.c3.lay.addWidget(self.temp_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.temp_out = QTextEdit()
        self.temp_out.setReadOnly(True)
        self.temp_out.setFixedHeight(90)
        self.temp_out.hide()
        self.c3.lay.addWidget(self.temp_out)
        bl.addWidget(self.c3)
        # avancadas
        self.c4 = Card("Opções avançadas de otimização")
        self.c4.lay.addWidget(label("Apenas sugestões com motivo, benefício e impacto. Plano de energia, serviços e configurações gráficas não são alterados automaticamente.", "muted"))
        self.adv_btn = button("Analisar otimizações")
        self.adv_btn.clicked.connect(self.analyze_adv)
        self.c4.lay.addWidget(self.adv_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.adv_box = QVBoxLayout()
        self.c4.lay.addLayout(self.adv_box)
        bl.addWidget(self.c4)
        bl.addStretch(1)
        sc.setWidget(body)
        lay.addWidget(sc, 1)

    def update_machine(self):
        if "drivers" not in self.state.results:
            msg_error(self, "Execute a verificação de drivers primeiro.")
            return
        cands = self.state.actions.update_candidates(self.state.results)
        if not cands:
            QMessageBox.information(self, "Atualizar Máquina", "Nenhuma atualização recomendada com base no diagnóstico.")
            return
        UpdateDialog(self, cands).exec()

    def clean_temp(self):
        ids = [k for k, cb in self.temp_boxes.items() if cb.isChecked() and cb.isEnabled()]
        if not ids:
            msg_error(self, "Selecione ao menos um diretório.")
            return
        prev = self.state.actions.preview_temp(ids)
        mb = round(prev["total_bytes"] / 1024 ** 2, 1)
        ans = QMessageBox.question(self, "Confirmar limpeza", f"Serão liberados aproximadamente {mb} MB.\nArquivos em uso serão ignorados.\n\nDeseja continuar?")
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.temp_btn.setEnabled(False)

        def work():
            return self.state.actions.clean_temp(ids, confirmed=True)

        def done(res, err):
            self.temp_btn.setEnabled(True)
            self.temp_out.show()
            if err:
                self.temp_out.setPlainText(f"Falha: {err}")
                return
            lines = [res.message] + [f"Não removido: {f['caminho']} ({f['motivo']})" for f in res.details["falhas"][:30]]
            self.temp_out.setPlainText("\n".join(lines))
            if self.state.session:
                self.state.run_one("temp_files")
        run_async(work, done)

    def analyze_adv(self):
        self.adv_btn.setEnabled(False)
        mem = self.state.results.get("hardware")
        total = (mem.data.get("memoria") or {}).get("total_gb") if mem and mem.data else None

        def done(res, err):
            self.adv_btn.setEnabled(True)
            while self.adv_box.count():
                it = self.adv_box.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            if err:
                self.adv_box.addWidget(label(f"Análise indisponível: {err}"))
                return
            if not res:
                self.adv_box.addWidget(label("Nenhuma otimização necessária foi identificada.", "muted"))
            for s in res:
                c = Card(s["titulo"])
                c.lay.addWidget(label("Motivo: " + s["motivo"]))
                c.lay.addWidget(label("Benefício esperado: " + s["beneficio"]))
                c.lay.addWidget(label("Possíveis impactos: " + s["impacto"]))
                if s.get("itens"):
                    c.lay.addWidget(label("Itens: " + ", ".join(str(i) for i in s["itens"][:15]), "muted"))
                if s.get("abrir"):
                    b = button("Abrir configuração do Windows")
                    b.clicked.connect(lambda _=False, u=s["abrir"]: os.startfile(u) if hasattr(os, "startfile") else None)
                    c.lay.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
                self.adv_box.addWidget(c)
        run_async(lambda: analyze_advanced(total), done)


# ------------------------------------------------------------------ Upgrade
class UpgradePage(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state, self.recs, self.searches = state, [], []
        shell, lay, top = page_shell("Análise de Upgrade", "Baseada no hardware coletado; compatibilidade não confirmada é sinalizada.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(shell)
        self.b1 = button("Analisar upgrades", primary=True, icon_name="upgrade")
        self.b1.clicked.connect(self.analyze)
        self.b2 = button("Pesquisar preços")
        self.b2.setEnabled(False)
        self.b2.clicked.connect(self.prices)
        top.addWidget(self.b2)
        top.addWidget(self.b1)
        self.view = ResultView("upgrade")
        lay.addWidget(self.view, 1)

    def analyze(self):
        if not self.state.results:
            msg_error(self, "Execute o diagnóstico antes da análise de upgrade.")
            return
        self.recs = analyze_upgrades(self.state.results)
        self.searches = []
        self._save(None)
        self.b2.setEnabled(any(r.get("termo_busca") for r in self.recs))
        self._show()

    def prices(self):
        self.b2.setEnabled(False)
        terms = [r for r in self.recs if r.get("termo_busca")]

        def work():
            out = []
            for r in terms:
                s = search_price(r["termo_busca"], self.state.settings.ml_token)
                s.update({"id": r["id"], "titulo": r["titulo"], "quantidade": r.get("quantidade", 1)})
                out.append(s)
            return out

        def done(res, err):
            self.b2.setEnabled(True)
            if err:
                msg_error(self, f"Falha na pesquisa de preços: {err}")
                return
            self.searches = res
            self._save(estimate_total(res))
            self._show()
        run_async(work, done)

    def _save(self, budget):
        r = CheckResult("upgrade", "Análise de Upgrade", Status.OK, f"{len(self.recs)} recomendação(ões)",
                        data={"recomendacoes": self.recs, "precos": self.searches, "orcamento": budget}, timestamp=now_iso())
        self.state.save_upgrade(r)

    def _show(self):
        r = self.state.upgrade_result
        self.view.show_result(r)
        lay = self.view.lay
        if self.searches:
            c = Card("Pesquisa de preços")
            for s in self.searches:
                c.lay.addWidget(label(f"{s['titulo']} - consulta em {s['consultado_em']}", "h2"))
                if s["itens"]:
                    c.lay.addWidget(make_table(s["itens"][:3], ["nome", "preco", "loja", "link", "consultado_em"]))
                else:
                    c.lay.addWidget(label(s["motivo"] or "Sem resultados."))
                c.lay.addWidget(label("Busca manual: " + " | ".join(f"{l['loja']}: {l['link']}" for l in s["links_busca"][:3]), "muted"))
            lay.insertWidget(lay.count() - 1, c)


# ------------------------------------------------------------------ Relatorios
class ReportsPage(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        shell, lay, top = page_shell("Relatórios", "Laudo técnico profissional em PDF e histórico local de atendimentos.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(shell)
        c = Card("Laudo técnico")
        self.status = label("Execute o diagnóstico completo para gerar o laudo.", "muted")
        c.lay.addWidget(self.status)
        self.btn = button("Gerar Laudo Técnico", primary=True, icon_name="report")
        self.btn.clicked.connect(self.generate)
        c.lay.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(c)
        h = Card("Histórico de atendimentos")
        self.hist = QListWidget()
        h.lay.addWidget(self.hist)
        b = button("Carregar atendimento selecionado")
        b.clicked.connect(self.load)
        h.lay.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(h, 1)
        self.refresh()

    def refresh(self):
        self.hist.clear()
        for s in self.state.db.list_sessions():
            it = QListWidgetItem(f"#{s.id}  {s.created_at.replace('T', ' ')}  |  {s.client}  |  ticket {s.ticket}  |  {s.machine}")
            it.setData(Qt.ItemDataRole.UserRole, s.id)
            self.hist.addItem(it)
        done = self.state.finished()
        s = self.state.session
        self.status.setText(("Diagnóstico concluído para o ticket " + s.ticket + ". Pronto para gerar o laudo.") if (s and done) else
                            "Execute o diagnóstico completo para gerar o laudo." if not s else "Diagnóstico em andamento ou incompleto.")
        self.btn.setEnabled(bool(s and done))

    def load(self):
        it = self.hist.currentItem()
        if it:
            self.state.load_session(it.data(Qt.ItemDataRole.UserRole))

    def generate(self):
        s = self.state.session
        base = self.state.settings.output_dir or os.path.expanduser("~")
        path, _ = QFileDialog.getSaveFileName(self, "Salvar laudo técnico", os.path.join(base, safe_filename(s.client, s.ticket)), "PDF (*.pdf)")
        if not path:
            return
        self.btn.setEnabled(False)

        def done(res, err):
            self.btn.setEnabled(True)
            if err:
                msg_error(self, f"Não foi possível gerar o PDF: {err}")
                return
            self.status.setText(f"Laudo salvo em {res}")
            if self.state.session and self.state.session.id:
                self.state.db.log_action(self.state.session.id, "Laudo gerado", str(res), True)
            try:
                os.startfile(str(res))  # type: ignore[attr-defined]
            except Exception:
                pass
        run_async(lambda: self.state.generate_report(path), done)


# ------------------------------------------------------------------ Configuracoes
class SettingsPage(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        shell, lay, _ = page_shell("Configurações", "Permissões, consentimentos e ferramentas externas.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(shell)
        c = Card("Privilégios")
        self.adm = label("Executando como administrador." if state.ctx.is_admin else
                         "Executando sem privilégios de administrador: SMART detalhado, DISM/SFC, Windows\\Temp e alguns logs ficam parciais.")
        c.lay.addWidget(self.adm)
        if not state.ctx.is_admin:
            b = button("Reiniciar como administrador")
            b.clicked.connect(self.elevate)
            c.lay.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(c)
        c2 = Card("Preferências e consentimento")
        s = state.settings
        self.cb_speed = QCheckBox("Autorizo o teste de velocidade Ookla (envia o IP público ao serviço externo)")
        self.cb_speed.setChecked(s.speedtest_consent)
        self.cb_sfc = QCheckBox("Executar SFC /verifyonly no diagnóstico (lento)")
        self.cb_sfc.setChecked(s.run_sfc)
        self.cb_sig = QCheckBox("Bloquear ferramentas externas sem assinatura digital válida")
        self.cb_sig.setChecked(s.require_signed_tools)
        for w in (self.cb_speed, self.cb_sfc, self.cb_sig):
            c2.lay.addWidget(w)
        form = QFormLayout()
        self.days = QSpinBox()
        self.days.setRange(1, 365)
        self.days.setValue(s.event_days)
        self.rdays = QSpinBox()
        self.rdays.setRange(1, 730)
        self.rdays.setValue(s.reboot_days)
        self.token = QLineEdit(s.ml_token)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Opcional: token da API do Mercado Livre")
        form.addRow("Dias de logs analisados", self.days)
        form.addRow("Dias de reinicializações", self.rdays)
        form.addRow("Token Mercado Livre", self.token)
        c2.lay.addLayout(form)
        sv = button("Salvar configurações", primary=True)
        sv.clicked.connect(self.save)
        c2.lay.addWidget(sv, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(c2)
        c3 = Card("Ferramentas externas (pasta tools)")
        self.tools_out = QTextEdit()
        self.tools_out.setReadOnly(True)
        self.tools_out.setFixedHeight(130)
        c3.lay.addWidget(self.tools_out)
        b = button("Verificar ferramentas")
        b.clicked.connect(self.check_tools)
        c3.lay.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(c3)
        lay.addStretch(1)

    def elevate(self):
        if relaunch_as_admin():
            import sys
            sys.exit(0)
        msg_error(self, "Elevação cancelada ou indisponível.")

    def save(self):
        s = self.state.settings
        s.speedtest_consent, s.run_sfc, s.require_signed_tools = self.cb_speed.isChecked(), self.cb_sfc.isChecked(), self.cb_sig.isChecked()
        s.event_days, s.reboot_days, s.ml_token = self.days.value(), self.rdays.value(), self.token.text().strip()
        self.state.ctx.speedtest_consent = s.speedtest_consent
        s.save()
        QMessageBox.information(self, "Configurações", "Configurações salvas.")

    def check_tools(self):
        def work():
            lines = []
            for k, names in TOOL_FILES.items():
                i = inspect_tool(k)
                if i:
                    lines.append(f"{names[0]}: encontrado em {i.path}\n    assinatura: {i.signature.status} {i.signature.signer}\n    SHA-256: {i.sha256}")
                else:
                    lines.append(f"{' / '.join(names)}: NÃO encontrado")
            return "\n".join(lines)
        run_async(work, lambda r, e: self.tools_out.setPlainText(r if not e else f"Erro: {e}"))
