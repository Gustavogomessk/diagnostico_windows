from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET

from ..core.models import Context, Status
from ..infra.powershell import PlatformUnsupported, as_list, is_windows, run_ps_json
from .base import Collector

REB_SCRIPT = r"""
$st=(Get-Date).AddDays(-%DAYS%)
Get-WinEvent -FilterHashtable @{LogName='System';Id=41,6005,6006,6008,1074,12,13;StartTime=$st} -ErrorAction SilentlyContinue |
 ForEach-Object { [pscustomobject]@{Id=$_.Id;P=$_.ProviderName;T=$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss');X=$_.ToXml()} }
"""
VALID = {(41, "Microsoft-Windows-Kernel-Power"), (6005, "EventLog"), (6006, "EventLog"), (6008, "EventLog"),
         (1074, "User32"), (12, "Microsoft-Windows-Kernel-General"), (13, "Microsoft-Windows-Kernel-General")}


def _event_data(xml: str) -> dict:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {}
    out = {}
    for i, e in enumerate(e for e in root.iter() if e.tag.split("}")[-1] == "Data"):
        out[e.attrib.get("Name") or f"p{i}"] = (e.text or "").strip()
    return out


def classify_event(ev: dict) -> dict:
    eid, d = ev["Id"], _event_data(ev.get("X", ""))
    base = {"data": ev["T"], "evento": f"{ev['P']} {eid}"}
    if eid == 12:
        return {**base, "tipo": "Inicialização", "detalhe": "Sistema iniciado"}
    if eid == 13:
        return {**base, "tipo": "Desligamento normal", "detalhe": "Sistema encerrado pelo kernel"}
    if eid == 6005:
        return {**base, "tipo": "Inicialização", "detalhe": "Serviço de log de eventos iniciado"}
    if eid == 6006:
        return {**base, "tipo": "Desligamento normal", "detalhe": "Serviço de log encerrado de forma limpa"}
    if eid == 6008:
        return {**base, "tipo": "Desligamento inesperado", "detalhe": "O desligamento anterior não foi limpo (causa não determinada pelo evento)"}
    if eid == 1074:
        kind = (d.get("param5") or "").lower()
        restart = "restart" in kind or "reinici" in kind
        who = d.get("param1") or "processo não informado"
        return {**base, "tipo": "Reinicialização solicitada" if restart else "Desligamento solicitado",
                "detalhe": f"Solicitado por {who}; motivo: {d.get('param3') or 'não informado'}"}
    if eid == 41:
        try:
            bc = int(d.get("BugcheckCode") or 0)
        except ValueError:
            bc = 0
        if bc:
            return {**base, "tipo": "Reinicialização após tela azul", "detalhe": f"Bugcheck 0x{bc:X}"}
        try:
            pb = int(d.get("PowerButtonTimestamp") or 0)
        except ValueError:
            pb = 0
        if pb:
            return {**base, "tipo": "Desligamento inesperado", "detalhe": "Botão liga/desliga foi mantido pressionado (indício de desligamento forçado)"}
        return {**base, "tipo": "Desligamento inesperado",
                "detalhe": "Kernel-Power 41 sem bugcheck: possível queda de energia, travamento ou superaquecimento; causa não determinada"}
    return {**base, "tipo": "Outro", "detalhe": ""}


def build_timeline(events: list[dict]) -> dict:
    items = [classify_event(e) for e in events if (e.get("Id"), e.get("P")) in VALID]
    items.sort(key=lambda x: x["data"], reverse=True)
    count = lambda t: sum(1 for i in items if i["tipo"] == t)  # noqa: E731
    boots = sum(1 for i in items if i["evento"].endswith(" 12")) or count("Inicialização")
    unexpected = sum(1 for i in items if i["evento"].endswith(" 41") and i["tipo"] == "Desligamento inesperado")
    if not unexpected:
        unexpected = sum(1 for i in items if i["evento"].endswith(" 6008"))
    return {"contagens": {
        "inicializacoes": boots,
        "desligamentos": count("Desligamento normal") + count("Desligamento solicitado"),
        "reinicializacoes_solicitadas": count("Reinicialização solicitada"),
        "inesperados": unexpected, "telas_azuis": count("Reinicialização após tela azul")},
        "linha_do_tempo": items[:200]}


class RebootsCollector(Collector):
    key = "reboots"
    title = "Reinicializações"

    def run(self, ctx: Context):
        if not is_windows():
            raise PlatformUnsupported("Disponível somente no Windows.")
        days = ctx.settings.reboot_days
        events = as_list(run_ps_json(REB_SCRIPT.replace("%DAYS%", str(int(days))), timeout=120))
        data = build_timeline(events)
        c = data["contagens"]
        problems = []
        if c["inesperados"]:
            problems.append(f"{c['inesperados']} desligamento(s) inesperado(s) nos últimos {days} dias")
        if c["telas_azuis"]:
            problems.append(f"{c['telas_azuis']} reinicialização(ões) após tela azul")
        ev = ["Eventos Kernel-Power 41 não indicam, por si só, ação do usuário: a causa só é afirmada quando o evento traz evidência."]
        return self.new(Status.ALERT if problems else Status.OK,
                        f"{c['inicializacoes']} inicializações, {c['inesperados']} inesperadas (últimos {days} dias)",
                        data=data, problems=problems, evidence=ev)
