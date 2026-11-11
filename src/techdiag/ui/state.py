from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..collectors import all_collectors
from ..core.config import Settings, data_dir
from ..core.models import CheckResult, Context, Session, Status
from ..core.orchestrator import Orchestrator
from ..core.privileges import is_admin
from ..infra.database import Database
from ..reports.classify import classify_overall
from ..reports.pdf_builder import build_report
from ..services.actions import ActionService


class AppState(QObject):
    result_changed = Signal(object)
    session_changed = Signal()

    def __init__(self, db_path: Path | None = None, settings: Settings | None = None):
        super().__init__()
        self.settings = settings or Settings.load()
        self.ctx = Context(is_admin=is_admin(), settings=self.settings, speedtest_consent=self.settings.speedtest_consent)
        self.db = Database(db_path or (data_dir() / "history.db"))
        self.collectors = all_collectors()
        self.orch = Orchestrator(self.collectors, self.ctx, on_update=self.result_changed.emit, db=self.db)
        self.actions = ActionService(self.ctx, self.db)
        self.upgrade_result: CheckResult | None = None

    @property
    def session(self) -> Session | None:
        return self.ctx.session

    @property
    def results(self) -> dict:
        return self.ctx.results

    def start_session(self, s: Session) -> None:
        self.db.create_session(s)
        self.ctx.session = s
        self.ctx.results.clear()
        self.upgrade_result = None
        self.ctx.speedtest_consent = self.settings.speedtest_consent
        self.session_changed.emit()

    def load_session(self, sid: int) -> bool:
        s = self.db.get_session(sid)
        if not s:
            return False
        self.ctx.session = s
        self.ctx.results.clear()
        self.ctx.results.update(self.db.load_results(sid))
        self.upgrade_result = self.ctx.results.pop("upgrade", None)
        self.session_changed.emit()
        for r in list(self.ctx.results.values()):
            self.result_changed.emit(r)
        return True

    def run_all(self):
        return self.orch.start()

    def run_one(self, key: str):
        return self.orch.start([key])

    def finished(self) -> bool:
        keys = set(self.orch.collectors)
        return bool(self.results) and keys <= set(self.results) and all(
            self.results[k].status in (Status.OK, Status.ALERT, Status.ERROR) for k in keys)

    def overall(self) -> tuple[str, list[str]]:
        res = dict(self.results)
        if self.upgrade_result:
            res["upgrade"] = self.upgrade_result
        return classify_overall(res)

    def save_upgrade(self, r: CheckResult) -> None:
        self.upgrade_result = r
        if self.session and self.session.id:
            self.db.save_result(self.session.id, r)

    def generate_report(self, path: str) -> Path:
        assert self.session, "Sem sessão ativa"
        actions = self.db.list_actions(self.session.id) if self.session.id else []
        return build_report(path, self.session, dict(self.results), actions, self.upgrade_result)
