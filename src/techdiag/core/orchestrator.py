"""Executa coletores em paralelo, isolando falhas e persistindo resultados."""
from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

from .models import CheckResult, Context, Status

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, collectors: list, ctx: Context, on_update: Callable[[CheckResult], None] | None = None,
                 db=None, max_workers: int = 4):
        self.collectors = {c.key: c for c in collectors}
        self.ctx = ctx
        self.on_update = on_update
        self.db = db
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="diag")

    def _emit(self, res: CheckResult) -> None:
        self.ctx.results[res.key] = res
        if self.db and self.ctx.session and self.ctx.session.id:
            try:
                self.db.save_result(self.ctx.session.id, res)
            except Exception:
                log.exception("Falha ao salvar resultado %s", res.key)
        if self.on_update:
            try:
                self.on_update(res)
            except Exception:
                log.exception("Falha no callback de atualizacao")

    def _run_one(self, key: str) -> CheckResult:
        c = self.collectors[key]
        self._emit(CheckResult(key=key, title=c.title, status=Status.RUNNING, summary="Analisando..."))
        res = c.execute(self.ctx)
        self._emit(res)
        return res

    def start(self, keys: list[str] | None = None) -> list[Future]:
        keys = keys or list(self.collectors)
        for k in keys:
            c = self.collectors[k]
            self._emit(CheckResult(key=k, title=c.title, status=Status.PENDING, summary="Aguardando"))
        return [self._pool.submit(self._run_one, k) for k in keys]

    def run_blocking(self, keys: list[str] | None = None) -> dict[str, CheckResult]:
        futures = self.start(keys)
        return {f.result().key: f.result() for f in futures}

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)
