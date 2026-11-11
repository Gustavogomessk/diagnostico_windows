from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..core.config import APP_NAME
from ..core.models import CheckResult
from ..infra.powershell import PlatformUnsupported
from ..infra.tools import ToolError
from ..services.actions import ConfirmationRequired
from . import icons, theme
from .pages import (ActionsPage, DashboardPage, DiagnosticPage, IdentificationPage, ReportsPage, ResultPage,
                    SettingsPage, UpgradePage, drivers_decorator, memory_decorator, network_decorator, sensors_decorator, msg_error)
from .state import AppState
from .widgets import label, button, run_async

NAV = [("Dashboard", "dashboard"), ("Identificação", "user"), ("Diagnóstico", "activity"), ("Hardware", "cpu"),
       ("Armazenamento", "disk"), ("Memória RAM", "memory"), ("Bateria", "battery"), ("Drivers", "driver"),
       ("Rede e Internet", "network"), ("Logs do Windows", "logs"), ("Central de Ações", "tools"),
       ("Análise de Upgrade", "upgrade"), ("Relatórios", "report"), ("Configurações", "settings")]


class MainWindow(QMainWindow):
    def __init__(self, state: AppState | None = None):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} - Diagnóstico e Laudo Técnico")
        self.resize(1280, 800)
        self.setStyleSheet(theme.QSS)
        self.state = state or AppState()
        titles = {c.key: c.title for c in self.state.collectors}
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(230)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 14, 0, 10)
        title = label(APP_NAME, "h1")
        title.setContentsMargins(18, 0, 0, 8)
        sl.addWidget(title)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        for name, ic in NAV:
            it = QListWidgetItem(icons.icon(ic, theme.PRIMARY, 18), name)
            self.nav.addItem(it)
        sl.addWidget(self.nav, 1)

        self.stack = QStackedWidget()
        st = self.state
        self.dashboard = DashboardPage(st, titles, lambda: self.go(1))
        self.ident = IdentificationPage(st)
        self.diag = DiagnosticPage(st, titles)
        self.hardware = ResultPage(st, "Hardware", "Identificação completa dos componentes e temperaturas (HWiNFO).",
                                   [("hardware", "Hardware"), ("sensors", "Temperaturas")],
                                   decorators={"sensors": sensors_decorator(st)}, skips={"sensors": ("cpu",)})
        self.storage = ResultPage(st, "Armazenamento", "Saúde individual de cada unidade (SMART e contadores de confiabilidade).", [("storage", "Armazenamento")])
        self.memory = ResultPage(st, "Memória RAM", "Uso, categorias, histórico de testes e ferramentas.", [("memory", "Memória RAM")],
                                 decorators={"memory": memory_decorator(st, self.memory_action)})
        self.battery = ResultPage(st, "Bateria", "Baseado no relatório nativo powercfg /batteryreport.", [("battery", "Bateria")])
        self.drivers = ResultPage(st, "Drivers", "Dispositivos com erro, ausentes ou sem assinatura.", [("drivers", "Drivers")],
                                  decorators={"drivers": drivers_decorator(st)}, skips={"drivers": ("achados",)})
        self.network = ResultPage(st, "Rede e Internet", "Interfaces, Wi-Fi, latência e teste de velocidade.", [("network", "Rede e Internet")],
                                  decorators={"network": network_decorator(st, self.run_speedtest)}, skips={"network": ("speedtest",)})
        self.logs = ResultPage(st, "Logs do Windows", "Event Viewer, telas azuis, reinicializações e integridade.",
                               [("eventlogs", "Event Viewer"), ("reboots", "Reinicializações"), ("bsod", "Telas azuis"), ("windows", "Integridade"), ("temp_files", "Temporários")])
        self.actions = ActionsPage(st, self.memory_action)
        self.upgrade = UpgradePage(st)
        self.reports = ReportsPage(st)
        self.settings = SettingsPage(st)
        self.pages = [self.dashboard, self.ident, self.diag, self.hardware, self.storage, self.memory, self.battery, self.drivers,
                      self.network, self.logs, self.actions, self.upgrade, self.reports, self.settings]
        for p in self.pages:
            self.stack.addWidget(p)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        if not st.ctx.is_admin:
            b = label("Sem privilégios de administrador: verificações que exigem elevação ficam parciais e indicam o motivo. Veja Configurações para reiniciar como administrador.", "banner")
            b.setContentsMargins(0, 0, 0, 0)
            rl.addWidget(b)
        rl.addWidget(self.stack, 1)
        root = QWidget()
        hl = QHBoxLayout(root)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        hl.addWidget(side)
        hl.addWidget(right, 1)
        self.setCentralWidget(root)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(1)
        self.ident.started.connect(self.start_diagnostic)
        st.result_changed.connect(self.on_result)
        st.session_changed.connect(self.on_session)

    def go(self, idx: int):
        self.nav.setCurrentRow(idx)

    def start_diagnostic(self, session):
        self.state.start_session(session)
        self.go(0)
        self.state.run_all()

    def on_session(self):
        self.dashboard.on_session()
        self.reports.refresh()
        for p in (self.hardware, self.storage, self.memory, self.battery, self.drivers, self.network, self.logs):
            for v in p.views.values():
                v.show_message("Aguardando.")

    def on_result(self, res: CheckResult):
        self.dashboard.on_result(res)
        self.diag.on_result(res)
        for p in (self.hardware, self.storage, self.memory, self.battery, self.drivers, self.network, self.logs):
            p.on_result(res)
        self.reports.refresh()

    # ---- acoes com confirmacao -----------------------------------
    def memory_action(self, which: str):
        a = self.state.actions
        try:
            if which == "open_rammap":
                a.open_rammap()
            elif which == "standby":
                if QMessageBox.question(self, "Esvaziar Standby List",
                                        "O Windows usa a Standby List como cache. Esvaziá-la não aumenta a memória real e pode deixar o sistema mais lento por instantes.\n\nContinuar?") != QMessageBox.StandardButton.Yes:
                    return
                r = a.empty_standby(confirmed=True)
                QMessageBox.information(self, "Standby List", r.message)
            elif which == "mdsched":
                r = a.schedule_memory_test()
                QMessageBox.information(self, "Windows Memory Diagnostic", r.message)
        except (ToolError, PlatformUnsupported, ConfirmationRequired) as e:
            msg_error(self, str(e))
        except Exception as e:  # noqa: BLE001
            msg_error(self, f"Falha: {e}")

    def run_speedtest(self):
        from ..collectors.network import run_speedtest
        if QMessageBox.question(self, "Teste de velocidade",
                                "O Speedtest CLI da Ookla enviará seu IP público e dados de conexão aos servidores da Ookla.\n\nAutoriza o teste?") != QMessageBox.StandardButton.Yes:
            return
        self.state.ctx.speedtest_consent = True

        def done(res, err):
            if err:
                msg_error(self, f"Teste não executado: {err}")
                return
            r = self.state.results.get("network")
            if r:
                r.data["speedtest"] = res
                self.state.db.save_result(self.state.session.id, r) if self.state.session and self.state.session.id else None
                self.network.on_result(r)
        run_async(lambda: run_speedtest(self.state.settings.require_signed_tools), done)

    def closeEvent(self, e):
        self.state.orch.shutdown()
        super().closeEvent(e)
