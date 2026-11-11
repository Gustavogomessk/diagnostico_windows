from __future__ import annotations

import logging
import time

from ..core.models import CheckResult, Context, Status, now_iso
from ..infra.powershell import PlatformUnsupported, PowerShellError
from ..infra.tools import ToolError

log = logging.getLogger(__name__)


class Collector:
    key = ""
    title = ""

    def run(self, ctx: Context) -> CheckResult:  # pragma: no cover
        raise NotImplementedError

    def new(self, status: Status = Status.OK, summary: str = "", **kw) -> CheckResult:
        return CheckResult(key=self.key, title=self.title, status=status, summary=summary, **kw)

    def execute(self, ctx: Context) -> CheckResult:
        """Executa o coletor garantindo que qualquer falha vire um resultado de erro isolado."""
        t0 = time.time()
        try:
            res = self.run(ctx)
        except PlatformUnsupported as e:
            res = self.new(Status.ERROR, "Verificação indisponível nesta plataforma", reason=str(e))
        except (PowerShellError, ToolError) as e:
            res = self.new(Status.ERROR, "Não foi possível executar a verificação", reason=str(e))
        except Exception as e:  # noqa: BLE001 - isolamento intencional
            log.exception("Falha no coletor %s", self.key)
            res = self.new(Status.ERROR, "Falha inesperada na verificação", reason=f"{type(e).__name__}: {e}")
        res.duration = round(time.time() - t0, 2)
        res.timestamp = now_iso()
        return res


def gb(n, digits: int = 1):
    try:
        return round(float(n) / 1024 ** 3, digits)
    except (TypeError, ValueError):
        return None


def num(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default
