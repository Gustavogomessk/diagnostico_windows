from __future__ import annotations

import platform
import re

from ..core.config import TOOL_FILES
from ..core.models import Context, Status
from ..infra import hwinfo
from ..infra.powershell import PlatformUnsupported, is_windows
from ..infra.tools import ToolError, find_tool, validate_tool
from ..services.cpu_thermal import evaluate, lookup_limit
from .base import Collector

CPU_PRIORITY = ["cpu package", "cpu (tctl/tdie)", "cpu tctl/tdie", "cpu die (average)", "core max", "cpu ia cores", "cpu tdie", "cpu"]


def _r(r: hwinfo.Reading) -> dict:
    return {"sensor": r.sensor, "leitura": r.label, "atual_c": round(r.value, 1), "min_c": round(r.vmin, 1),
            "max_c": round(r.vmax, 1), "media_c": round(r.vavg, 1)}


def cpu_model_from_sensors(readings) -> str | None:
    for r in readings:
        m = re.match(r"CPU\s*\[#\d+\]:\s*(.+?)(:|$)", r.sensor)
        if m:
            return m.group(1).strip()
    return None


def build_sensor_data(snap: hwinfo.Snapshot, fallback_model: str = "") -> dict:
    temps = [r for r in snap.readings if r.kind == "temp" and r.unit.replace("\u00b0", "").strip().upper() in ("C", "") or
             (r.kind == "temp" and "C" in r.unit.upper())]
    cpu_all = [r for r in temps if r.sensor.upper().startswith("CPU") or "CPU" in r.sensor.upper().split(":")[0]]
    cpu = None
    for key in CPU_PRIORITY:
        cand = [r for r in cpu_all if r.label.lower() == key]
        if cand:
            cpu = cand[0]
            break
    if cpu is None:
        cores = [r for r in cpu_all if re.match(r"core\s*#?\d+", r.label, re.I)]
        if cores:
            cpu = max(cores, key=lambda r: r.vmax)
    model = cpu_model_from_sensors(snap.readings) or fallback_model
    limit = lookup_limit(model)
    cpu_d = None
    if cpu:
        cpu_d = {"modelo": model, "leitura": cpu.label, "atual_c": round(cpu.value, 1), "min_c": round(cpu.vmin, 1),
                 "max_c": round(cpu.vmax, 1), "media_c": round(cpu.vavg, 1)}
        cpu_d.update({"limite_oficial_c": limit["limite_c"], "tipo_limite": limit["tipo"], "confianca_limite": limit["confianca"],
                      "fonte_limite": limit["fonte"], "url_fonte": limit["url"]})
        cpu_d.update(evaluate(cpu.vmax, limit["limite_c"]))
    gpu = [_r(r) for r in temps if "GPU" in r.sensor.upper() or "GPU" in r.label.upper()]
    disks = [_r(r) for r in temps if r.sensor.lower().startswith("drive")]
    others = [_r(r) for r in temps if r not in cpu_all and not any(x["sensor"] == r.sensor and x["leitura"] == r.label for x in gpu + disks)]
    return {"fonte": f"HWiNFO (memória compartilhada, versão {snap.version})", "cpu": cpu_d,
            "nucleos": [_r(r) for r in cpu_all if re.match(r"core\s*#?\d+", r.label, re.I)][:32],
            "gpu": gpu[:6], "discos": disks[:8], "outros": others[:12],
            "criterio": "A avaliação usa o pico medido e a margem até o limite oficial (crítico: margem ≤ 10 °C; atenção: ≤ 25 °C). "
                        "Margens são critério do TechDiag, não do fabricante. A leitura ocorre durante o diagnóstico, não sob carga máxima."}


class SensorsCollector(Collector):
    key = "sensors"
    title = "Temperaturas (HWiNFO)"

    def run(self, ctx: Context):
        if not is_windows():
            raise PlatformUnsupported("Disponível somente no Windows.")
        exe = find_tool("hwinfo")
        if exe is None:
            return self.new(Status.ERROR, "Sensores não lidos", reason="HWiNFO64.exe não encontrado na pasta tools.")
        if not ctx.is_admin:
            return self.new(Status.ERROR, "Sensores não lidos", reason="A leitura de sensores exige administrador (driver do HWiNFO).",
                            admin_required=["Temperaturas via HWiNFO"])
        validate_tool("hwinfo", ctx.settings.require_signed_tools)
        proc = None
        try:
            snap, proc = hwinfo.collect(exe)
        except hwinfo.HwinfoUnavailable as e:
            return self.new(Status.ERROR, "Sensores não lidos", reason=str(e))
        finally:
            if proc is not None:
                hwinfo.stop(proc)
        data = build_sensor_data(snap, platform.processor())
        cpu = data["cpu"]
        if not cpu:
            return self.new(Status.ERROR, "Temperatura da CPU não encontrada nos sensores", reason="O HWiNFO não expôs sensor de temperatura da CPU.", data=data)
        problems = []
        if cpu["avaliacao"] in ("Atenção", "Crítico"):
            problems.append(f"Temperatura da CPU: pico de {cpu['max_c']} °C para limite oficial de {cpu['limite_oficial_c']} °C ({cpu['avaliacao']})")
        evidence = [f"Limite oficial: {cpu['limite_oficial_c']} °C ({cpu['tipo_limite']}). Fonte: {cpu['fonte_limite']}"] if cpu["limite_oficial_c"] else \
            [cpu["fonte_limite"]]
        summary = f"CPU {cpu['atual_c']} °C (pico {cpu['max_c']} °C)" + (f" / limite oficial {cpu['limite_oficial_c']} °C" if cpu["limite_oficial_c"] else " / limite oficial não identificado")
        return self.new(Status.ALERT if problems else Status.OK, summary, data=data, problems=problems, evidence=evidence)
