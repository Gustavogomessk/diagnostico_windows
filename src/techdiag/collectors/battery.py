from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET

from ..core.models import Context, Status
from ..infra.powershell import PlatformUnsupported, PowerShellError, as_list, is_windows, run_process, run_ps_json
from .base import Collector, num


def _strip(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _entries(root: ET.Element, section: str, limit: int = 30) -> list[dict]:
    out = []
    for sec in root.iter():
        if _strip(sec.tag) == section:
            for e in list(sec)[:limit]:
                d = {k: v for k, v in e.attrib.items()}
                for c in e:
                    if c.text and c.text.strip():
                        d[_strip(c.tag)] = c.text.strip()
                out.append(d)
            break
    return out


def classify_battery(wear_pct: float) -> str:
    if wear_pct < 10:
        return "Excelente"
    if wear_pct < 25:
        return "Boa"
    if wear_pct < 40:
        return "Atenção"
    return "Necessita substituição"


def parse_battery_xml(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    bat = None
    for el in root.iter():
        if _strip(el.tag) == "Battery" and any(_strip(c.tag) == "DesignCapacity" for c in el):
            bat = el
            break
    if bat is None:
        return {"possui_bateria": False}
    vals = {_strip(c.tag): (c.text or "").strip() for c in bat}
    design, full = num(vals.get("DesignCapacity")), num(vals.get("FullChargeCapacity"))
    out = {
        "possui_bateria": True, "fabricante": vals.get("Manufacturer") or None, "nome": vals.get("Id") or None,
        "quimica": vals.get("Chemistry") or None, "design_mwh": design, "carga_total_mwh": full,
        "ciclos": num(vals.get("CycleCount")),
        "historico_uso": _entries(root, "RecentUsage"),
        "historico_capacidade": _entries(root, "CapacityHistory"),
        "estimativas_autonomia": _entries(root, "RuntimeEstimates") or _entries(root, "BatteryLifeEstimates"),
    }
    if design and full is not None:
        wear = round((1 - full / design) * 100, 1)
        out["desgaste_pct"] = max(wear, 0.0)
        out["classificacao"] = classify_battery(out["desgaste_pct"])
    return out


class BatteryCollector(Collector):
    key = "battery"
    title = "Bateria"

    def run(self, ctx: Context):
        if not is_windows():
            raise PlatformUnsupported("Disponível somente no Windows.")
        present = as_list(run_ps_json("Get-CimInstance Win32_Battery | Select-Object Name,DeviceID", 30))
        if not present:
            return self.new(Status.OK, "O equipamento não possui bateria detectável.",
                            data={"possui_bateria": False})
        out = os.path.join(tempfile.gettempdir(), f"techdiag_battery_{os.getpid()}.xml")
        try:
            rc, so, se = run_process(["powercfg", "/batteryreport", "/xml", "/output", out], timeout=60)
            if rc != 0 or not os.path.exists(out):
                raise PowerShellError(f"powercfg /batteryreport falhou: {(se or so).strip()}")
            with open(out, "rb") as f:
                text = f.read().decode("utf-8-sig", errors="replace")
        finally:
            try:
                os.remove(out)
            except OSError:
                pass
        data = parse_battery_xml(text)
        if not data.get("possui_bateria") or "desgaste_pct" not in data:
            return self.new(Status.ERROR, "Relatório de bateria sem capacidades", reason="O relatório do powercfg não trouxe DesignCapacity/FullChargeCapacity.", data=data)
        cl, wear = data["classificacao"], data["desgaste_pct"]
        evidence = [f"Capacidade de projeto {data['design_mwh']} mWh; atual {data['carga_total_mwh']} mWh; desgaste {wear}%."]
        problems = []
        if cl in ("Atenção", "Necessita substituição"):
            problems.append(f"Bateria com desgaste de {wear}% ({cl})")
        return self.new(Status.ALERT if problems else Status.OK, f"{cl} - desgaste de {wear}%", data=data,
                        evidence=evidence, problems=problems)
