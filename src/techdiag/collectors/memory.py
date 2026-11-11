from __future__ import annotations

import psutil

from ..core.models import Context, Status
from ..infra.powershell import as_list, is_windows, run_ps_json
from ..infra.tools import find_tool
from .base import Collector, gb

PERF_SCRIPT = r"""
$e='SilentlyContinue'
$m=Get-CimInstance Win32_PerfRawData_PerfOS_Memory -ErrorAction $e | Select-Object AvailableBytes,CacheBytes,FreeAndZeroPageListBytes,ModifiedPageListBytes,StandbyCacheCoreBytes,StandbyCacheNormalPriorityBytes,StandbyCacheReserveBytes,PoolPagedBytes,PoolNonpagedBytes
$h=@(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-MemoryDiagnostics-Results'} -MaxEvents 10 -ErrorAction $e |
  Select-Object Id,@{n='T';e={$_.TimeCreated.ToString('yyyy-MM-dd HH:mm')}})
[pscustomobject]@{m=$m;h=$h}
"""


def build_categories(total: int, perf: dict, private_total: int | None) -> list[dict]:
    """Categorias derivadas dos contadores do Windows (equivalentes aproximados ao RAMMap)."""
    g = lambda k: int(perf.get(k) or 0)  # noqa: E731
    standby = g("StandbyCacheCoreBytes") + g("StandbyCacheNormalPriorityBytes") + g("StandbyCacheReserveBytes")
    modified, free = g("ModifiedPageListBytes"), g("FreeAndZeroPageListBytes")
    active = max(total - standby - modified - free, 0)
    src = "Contadores do Windows"
    cats = [
        {"nome": "Active (em uso)", "gb": gb(active, 2), "origem": src},
        {"nome": "Standby", "gb": gb(standby, 2), "origem": src},
        {"nome": "Modified", "gb": gb(modified, 2), "origem": src},
        {"nome": "Free + Zero", "gb": gb(free, 2), "origem": src},
        {"nome": "Cache do sistema", "gb": gb(g("CacheBytes"), 2), "origem": src},
        {"nome": "Pool paginado + não paginado", "gb": gb(g("PoolPagedBytes") + g("PoolNonpagedBytes"), 2), "origem": src},
    ]
    if private_total is not None:
        cats.append({"nome": "Process Private (soma dos processos acessíveis)", "gb": gb(private_total, 2), "origem": "psutil"})
    return cats


class MemoryCollector(Collector):
    key = "memory"
    title = "Memória RAM"

    def run(self, ctx: Context):
        vm = psutil.virtual_memory()
        procs, private_total = [], 0
        for p in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                mi = p.info["memory_info"]
                if mi is None:
                    continue
                priv = getattr(mi, "private", mi.rss)
                private_total += priv
                procs.append({"nome": p.info["name"], "pid": p.info["pid"], "memoria_mb": round(priv / 1024 ** 2)})
            except (psutil.Error, OSError):
                continue
        procs.sort(key=lambda x: x["memoria_mb"], reverse=True)
        data = {
            "resumo": {"total_gb": gb(vm.total), "em_uso_gb": gb(vm.used), "disponivel_gb": gb(vm.available),
                       "uso_pct": vm.percent},
            "categorias": [], "top_processos": procs[:10], "historico_diagnostico": [],
            "rammap_disponivel": find_tool("rammap") is not None,
        }
        evidence, problems = [], []
        status = Status.OK
        if is_windows():
            try:
                raw = run_ps_json(PERF_SCRIPT, timeout=60) or {}
                perf = raw.get("m") or {}
                if perf:
                    data["categorias"] = build_categories(vm.total, perf, private_total)
                for h in as_list(raw.get("h")):
                    res = {1101: "Sem erros detectados", 1102: "Erros de memória detectados"}.get(h.get("Id"), f"Evento {h.get('Id')}")
                    data["historico_diagnostico"].append({"data": h.get("T"), "resultado": res})
                    if h.get("Id") == 1102:
                        problems.append(f"Windows Memory Diagnostic detectou erros em {h.get('T')}")
            except Exception as e:  # coleta parcial: nao derruba o restante
                evidence.append(f"Contadores detalhados indisponíveis: {e}")
        if not data["historico_diagnostico"]:
            evidence.append("Nenhum teste do Windows Memory Diagnostic encontrado no histórico. "
                            "O uso de RAM não comprova a integridade física dos módulos.")
        if vm.percent >= 90:
            problems.append(f"Utilização de RAM elevada ({vm.percent}%)")
        if problems:
            status = Status.ALERT
        evidence.append("Memória em cache/Standby é gerenciada pelo Windows e não indica problema por si só.")
        return self.new(status, f"{data['resumo']['uso_pct']}% em uso de {data['resumo']['total_gb']} GB",
                        data=data, evidence=evidence, problems=problems)
