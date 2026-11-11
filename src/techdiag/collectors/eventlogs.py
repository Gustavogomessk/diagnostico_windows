from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

from ..core.models import Context, Status
from ..infra.powershell import PlatformUnsupported, as_list, is_windows, run_ps_json
from .base import Collector
from .bsod import interpret, normalize_code
from .reboots import _event_data

LOG_SCRIPT = r"""
$st=(Get-Date).AddDays(-%DAYS%)
function M($e){ $m=$e.Message; if($m){ $m=$m -replace '\s+',' '; $m.Substring(0,[Math]::Min(260,$m.Length)) } else { '' } }
$out=@()
foreach($l in 'System','Application','Setup'){
  $out+=@(Get-WinEvent -FilterHashtable @{LogName=$l;Level=1,2;StartTime=$st} -MaxEvents 3000 -ErrorAction SilentlyContinue |
    ForEach-Object { [pscustomobject]@{L=$l;I=$_.Id;P=$_.ProviderName;V=$_.Level;T=$_.TimeCreated.ToString('yyyy-MM-dd HH:mm');M=(M $_)} })
}
$out+=@(Get-WinEvent -FilterHashtable @{LogName='Application';ProviderName='Windows Error Reporting';StartTime=$st} -MaxEvents 200 -ErrorAction SilentlyContinue |
  ForEach-Object { [pscustomobject]@{L='WER';I=$_.Id;P=$_.ProviderName;V=$_.Level;T=$_.TimeCreated.ToString('yyyy-MM-dd HH:mm');M=(M $_)} })
$out
"""

BSOD_SCRIPT = r"""
$st=(Get-Date).AddDays(-%DAYS%)
$o=@()
$o+=@(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Kernel-Power';Id=41;StartTime=$st} -ErrorAction SilentlyContinue |
  ForEach-Object { [pscustomobject]@{Id=41;T=$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss');X=$_.ToXml()} })
$o+=@(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-WER-SystemErrorReporting';Id=1001;StartTime=$st} -ErrorAction SilentlyContinue |
  ForEach-Object { [pscustomobject]@{Id=1001;T=$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss');X=$_.ToXml()} })
$o
"""


def _parse(t: str) -> dt.datetime:
    return dt.datetime.strptime(t, "%Y-%m-%d %H:%M:%S")


def build_bsod_reboots(items: list[dict], window_min: int = 15) -> list[dict]:
    """Une Kernel-Power 41 (reinicio apos bugcheck) e WER 1001 (codigo do bugcheck) em uma linha com data e horario."""
    k41, w = [], []
    for it in items:
        d = _event_data(it.get("X", ""))
        if it["Id"] == 41:
            try:
                bc = int(d.get("BugcheckCode") or 0)
            except ValueError:
                bc = 0
            if bc:
                k41.append({"t": it["T"], "code": f"0x{bc:08X}"})
        elif it["Id"] == 1001:
            first = next(iter(d.values()), "")
            code, _ = normalize_code(first)
            if re.match(r"0x", code):
                w.append({"t": it["T"], "code": code})
    rows, used = [], set()
    for e in k41:
        match = None
        for i, x in enumerate(w):
            if i not in used and abs((_parse(x["t"]) - _parse(e["t"])).total_seconds()) <= window_min * 60:
                match = i
                break
        if match is not None:
            used.add(match)
        rows.append({"t": e["t"], "code": e["code"], "src": "Kernel-Power 41" + (" + relatório de erros 1001" if match is not None else "")})
    for i, x in enumerate(w):
        if i not in used:
            rows.append({"t": x["t"], "code": x["code"], "src": "Relatório de erros 1001"})
    out = []
    for r in sorted(rows, key=lambda r: r["t"], reverse=True):
        _, ci = normalize_code(r["code"])
        name, interp = interpret(ci, None)
        out.append({"data_hora_reinicio": _parse(r["t"]).strftime("%d/%m/%Y %H:%M:%S"), "codigo": r["code"], "nome": name,
                    "origem_evento": r["src"], "interpretacao": interp})
    return out


# (provedor em minusculas, id) -> (categoria, impacto). Base de conhecimento conservadora.
IMPORTANT = {
    ("disk", 7): ("Disco", "Bloco defeituoso: risco de perda de dados; fazer backup"),
    ("disk", 11): ("Disco", "Erro de controlador/cabo; possível falha do disco"),
    ("disk", 51): ("Disco", "Erro de paginação em disco; possível falha ou cabo"),
    ("disk", 153): ("Disco", "Operação de I/O repetida; disco/controlador instável"),
    ("ntfs", 55): ("Disco", "Sistema de arquivos corrompido; executar chkdsk após backup"),
    ("ntfs", 98): ("Disco", "Verificação do volume necessária"),
    ("ntfs", 137): ("Disco", "Falha ao gravar dados do sistema de arquivos"),
    ("volmgr", 161): ("Disco", "Falha ao criar dump de memória"),
    ("microsoft-windows-whea-logger", 1): ("Hardware", "Erro de hardware corrigido/não corrigido (CPU, RAM, PCIe)"),
    ("microsoft-windows-whea-logger", 17): ("Hardware", "Erro de hardware PCIe corrigido"),
    ("microsoft-windows-whea-logger", 18): ("Hardware", "Erro fatal de hardware"),
    ("microsoft-windows-whea-logger", 19): ("Hardware", "Erro de hardware corrigido"),
    ("microsoft-windows-kernel-power", 41): ("Hardware", "Reinicialização sem desligamento limpo"),
    ("display", 4101): ("Driver de vídeo", "Driver de vídeo parou de responder e foi recuperado"),
    ("service control manager", 7000): ("Serviços", "Serviço não iniciou"),
    ("service control manager", 7001): ("Serviços", "Serviço dependente não iniciou"),
    ("service control manager", 7023): ("Serviços", "Serviço terminou com erro"),
    ("service control manager", 7031): ("Serviços", "Serviço terminou inesperadamente"),
    ("service control manager", 7034): ("Serviços", "Serviço terminou inesperadamente"),
    ("microsoft-windows-kernel-boot", 29): ("Inicialização", "Erro durante a inicialização"),
}
NOISE = {
    ("microsoft-windows-distributedcom", 10016): "Permissões DCOM: comum e normalmente inofensivo",
    ("distributedcom", 10016): "Permissões DCOM: comum e normalmente inofensivo",
    ("schannel", 36871): "Falha TLS pontual; comum",
    ("schannel", 36874): "Falha TLS pontual; comum",
    ("microsoft-windows-time-service", 134): "Sincronização de horário; comum",
    ("microsoft-windows-time-service", 36): "Sincronização de horário; comum",
    ("microsoft-windows-kernel-pnp", 219): "Driver não carregado em dispositivo removível; comum",
    ("microsoft-windows-dhcp-client", 1002): "Renovação DHCP; comum em Wi-Fi",
    ("application hang", 1002): "Travamento de aplicativo isolado",
}
LEVEL = {1: "Crítico", 2: "Erro"}
CATEGORY_HINTS = [("disk", "Disco"), ("ntfs", "Disco"), ("stor", "Disco"), ("nvme", "Disco"), ("whea", "Hardware"),
                  ("display", "Driver de vídeo"), ("nvlddmkm", "Driver de vídeo"), ("amdkmdag", "Driver de vídeo"),
                  ("service control manager", "Serviços"), ("application error", "Aplicativos"),
                  (".net runtime", "Aplicativos"), ("application hang", "Aplicativos"), ("windows error reporting", "Aplicativos"),
                  ("windowsupdate", "Atualização"), ("servicing", "Atualização"), ("msiinstaller", "Instalação")]


def categorize(provider: str) -> str:
    p = provider.lower()
    for hint, cat in CATEGORY_HINTS:
        if hint in p:
            return cat
    return "Sistema"


def analyze_events(events: list[dict], recurrence_threshold: int = 10) -> dict:
    groups: dict = defaultdict(list)
    for e in events:
        groups[(e["L"], (e.get("P") or "").strip(), e["I"], e["V"])].append(e)
    important, common, other, wer = [], [], [], []
    for (log, prov, eid, lvl), evs in groups.items():
        key = (prov.lower(), eid)
        last = max(x["T"] for x in evs)
        item = {"log": log, "fonte": prov, "id": eid, "nivel": LEVEL.get(lvl, "Informação"), "ocorrencias": len(evs),
                "ultima": last, "descricao": evs[-1].get("M") or "", "impacto": "", "categoria": categorize(prov)}
        if log == "WER":
            item["impacto"] = "Relatório de falha de aplicativo/sistema"
            wer.append(item)
        elif key in IMPORTANT:
            item["categoria"], item["impacto"] = IMPORTANT[key]
            important.append(item)
        elif key in NOISE:
            item["impacto"] = NOISE[key]
            common.append(item)
        elif lvl == 1:
            item["impacto"] = "Evento crítico não catalogado; investigar"
            important.append(item)
        elif len(evs) >= recurrence_threshold:
            item["impacto"] = "Erro recorrente; avaliar se há sintoma associado"
            common.append(item)
        else:
            item["impacto"] = "Erro isolado; sem indício de impacto por si só"
            other.append(item)
    srt = lambda L: sorted(L, key=lambda x: (-x["ocorrencias"], x["ultima"]), reverse=False)  # noqa: E731
    return {"importantes": srt(important)[:60], "comuns": srt(common)[:40], "outros": srt(other)[:40], "wer": srt(wer)[:30],
            "resumo": {"eventos_analisados": len(events), "grupos_importantes": len(important),
                       "grupos_comuns": len(common), "grupos_outros": len(other)}}


class EventLogsCollector(Collector):
    key = "eventlogs"
    title = "Logs do Windows"

    def run(self, ctx: Context):
        if not is_windows():
            raise PlatformUnsupported("Disponível somente no Windows.")
        days = ctx.settings.event_days
        events = as_list(run_ps_json(LOG_SCRIPT.replace("%DAYS%", str(int(days))), timeout=240))
        analysis = analyze_events(events)
        try:
            bsod_rows = build_bsod_reboots(as_list(run_ps_json(BSOD_SCRIPT.replace("%DAYS%", str(int(days))), timeout=120)))
        except Exception as e:  # coleta parcial: nao derruba o restante
            bsod_rows = []
            analysis["resumo"]["aviso"] = f"Reinícios por tela azul não consultados: {e}"
        data = {"reinicios_tela_azul": bsod_rows, **analysis}
        imp = data["importantes"]
        problems = [f"{i['fonte']} (ID {i['id']}) x{i['ocorrencias']}: {i['impacto']}" for i in imp[:10]]
        if bsod_rows:
            problems.insert(0, f"{len(bsod_rows)} reinício(s) após tela azul; último em {bsod_rows[0]['data_hora_reinicio']} ({bsod_rows[0]['codigo']})")
        evidence = [f"Eventos crítico/erro dos últimos {days} dias agrupados por fonte e ID."]
        admin = [] if ctx.is_admin else ["Alguns eventos de sistema podem exigir administrador"]
        return self.new(Status.ALERT if (imp or bsod_rows) else Status.OK,
                        f"{len(bsod_rows)} reinício(s) por tela azul; {len(imp)} grupo(s) importante(s); {data['resumo']['eventos_analisados']} evento(s) analisados",
                        data=data, problems=problems, evidence=evidence, admin_required=admin)
