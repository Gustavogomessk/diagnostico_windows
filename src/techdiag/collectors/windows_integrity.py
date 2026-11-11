from __future__ import annotations

import re

from ..core.models import Context, Status
from ..infra.powershell import PlatformUnsupported, PowerShellError, decode_output, is_windows, run_process
from .base import Collector


def parse_dism(text: str, rc: int) -> tuple[str, str]:
    t = text.lower()
    if re.search(r"no component store corruption|nenhuma corrup", t):
        return "ok", "Nenhuma corrupção no repositório de componentes"
    if re.search(r"repairable|repar[aá]vel", t):
        return "alert", "Repositório de componentes reparável (DISM /RestoreHealth recomendado)"
    if re.search(r"not repairable|n[aã]o [eé] repar[aá]vel", t):
        return "alert", "Repositório de componentes irreparável"
    return "unknown", f"Saída do DISM não interpretada (código {rc})"


def parse_sfc(text: str) -> tuple[str, str]:
    t = text.replace("\x00", "").lower()
    if re.search(r"did not find any integrity violations|n[aã]o encontrou nenhuma viola", t):
        return "ok", "Nenhuma violação de integridade"
    if re.search(r"found integrity violations|encontrou viola", t):
        return "alert", "Violações de integridade encontradas (sfc /scannow recomendado)"
    return "unknown", "Saída do SFC não interpretada"


class WindowsIntegrityCollector(Collector):
    key = "windows"
    title = "Integridade do Windows"

    def run(self, ctx: Context):
        if not is_windows():
            raise PlatformUnsupported("Disponível somente no Windows.")
        if not ctx.is_admin:
            return self.new(Status.ERROR, "Verificação não executada", reason="Requer privilégios de administrador (DISM/SFC).",
                            admin_required=["DISM /CheckHealth e SFC"])
        data, problems, evidence = {}, [], []
        rc, out, err = run_process(["dism.exe", "/Online", "/Cleanup-Image", "/CheckHealth"], timeout=300)
        st, msg = parse_dism(out + err, rc)
        data["dism"] = {"estado": st, "mensagem": msg}
        if st == "alert":
            problems.append(msg)
        elif st == "unknown":
            evidence.append(msg)
        if ctx.settings.run_sfc:
            rc, out, err = run_process(["sfc.exe", "/verifyonly"], timeout=1800, encoding="utf-16-le")
            st2, msg2 = parse_sfc(out)
            data["sfc"] = {"estado": st2, "mensagem": msg2}
            if st2 == "alert":
                problems.append(msg2)
        else:
            evidence.append("SFC não executado (opção desativada em Configurações; leva vários minutos).")
        return self.new(Status.ALERT if problems else Status.OK, msg, data=data, problems=problems, evidence=evidence)
