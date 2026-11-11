from __future__ import annotations

import datetime as dt

from ..core.models import Context, Status
from ..infra.powershell import PlatformUnsupported, as_list, is_windows, run_ps_json
from ..services.driver_search import best_url, build_sources, short_hwid
from .base import Collector

DRV_SCRIPT = r"""
$e='SilentlyContinue'
function D($v){ if($v){ $v.ToString('yyyy-MM-dd') } else { $null } }
$cs=@(Get-CimInstance Win32_ComputerSystem -ErrorAction $e | Select-Object Manufacturer,Model)
$bad=@(Get-CimInstance Win32_PnPEntity -ErrorAction $e | Where-Object {$_.ConfigManagerErrorCode -ne 0} |
  Select-Object Name,DeviceID,Manufacturer,PNPClass,ConfigManagerErrorCode,@{n='HardwareID';e={@($_.HardwareID)}})
$drv=@(Get-CimInstance Win32_PnPSignedDriver -ErrorAction $e | Where-Object {$_.DeviceName} |
  Select-Object DeviceName,Manufacturer,DriverVersion,@{n='DriverDate';e={D $_.DriverDate}},IsSigned,DeviceClass,@{n='HardwareID';e={[string]$_.HardWareID}})
[pscustomobject]@{cs=$cs;bad=$bad;drv=$drv}
"""

ERR_TEXT = {
    1: "Dispositivo configurado incorretamente", 10: "O dispositivo não pode iniciar",
    12: "Recursos insuficientes (conflito)", 14: "Requer reinicialização", 18: "Reinstalar drivers",
    19: "Registro corrompido", 21: "Windows removendo o dispositivo", 22: "Dispositivo desabilitado",
    24: "Dispositivo ausente ou com defeito", 28: "Driver não instalado", 31: "Windows não consegue carregar o driver",
    32: "Driver desabilitado", 37: "Falha na inicialização do driver", 39: "Driver corrompido ou ausente",
    41: "Windows carregou o driver mas não encontrou o hardware", 43: "Dispositivo reportou problema ao Windows",
    45: "Dispositivo não conectado", 52: "Assinatura digital do driver não verificada",
}
OLD_CLASSES = {"DISPLAY", "NET", "BLUETOOTH", "MEDIA", "HDC", "SYSTEM", "USB"}


def _hwid(v):
    v = as_list(v)
    first = v[0] if v else None
    if isinstance(first, dict):
        first = first.get("value")
    return str(first) if first else None


def analyze_drivers(raw: dict, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    cs = as_list(raw.get("cs"))
    man = (cs[0].get("Manufacturer") if cs else "") or ""
    model = (cs[0].get("Model") if cs else "") or ""
    findings, unsigned, old = [], 0, 0
    drv = as_list(raw.get("drv"))
    for b in as_list(raw.get("bad")):
        code = b.get("ConfigManagerErrorCode")
        if code == 22:      # desabilitado pelo usuario: informativo, nao e falha de driver
            continue
        name = b.get("Name") or "Dispositivo desconhecido"
        hw = _hwid(b.get("HardwareID"))
        unknown = (b.get("Name") or "").lower().startswith(("dispositivo desconhecido", "unknown", "dispositivo de")) or not b.get("Name")
        issue = "Driver ausente" if code == 28 else ("Dispositivo desconhecido" if unknown else "Dispositivo com erro")
        src = build_sources(man, model, hw, name)
        findings.append({"dispositivo": name, "fabricante": b.get("Manufacturer"), "hardware_id": hw,
                         "versao_instalada": None, "data_driver": None,
                         "problema": f"{issue} (código {code}: {ERR_TEXT.get(code, 'erro não catalogado')})",
                         "codigo_erro": code, "assinatura": "N/D", "versao_recomendada": "Verificar na página do fabricante",
                         "fontes": src, "url": best_url(src)})
    for d in drv:
        signed = d.get("IsSigned")
        old_flag = False
        try:
            if d.get("DriverDate"):
                age = (today - dt.date.fromisoformat(d["DriverDate"])).days
                old_flag = age > 365 * 3 and (d.get("DeviceClass") or "").upper() in OLD_CLASSES
        except ValueError:
            pass
        if signed is False:
            unsigned += 1
        if old_flag:
            old += 1
        if signed is False or old_flag:
            hw = _hwid(d.get("HardwareID"))
            src = build_sources(man, model, hw, d.get("DeviceName") or "")
            issues = []
            if signed is False:
                issues.append("Driver sem assinatura digital")
            if old_flag:
                issues.append("Data do driver superior a 3 anos (possível atualização; não verificado)")
            findings.append({"dispositivo": d.get("DeviceName"), "fabricante": d.get("Manufacturer"), "hardware_id": hw,
                             "versao_instalada": d.get("DriverVersion"), "data_driver": d.get("DriverDate"),
                             "problema": "; ".join(issues), "codigo_erro": None,
                             "assinatura": "Assinado" if signed else "Não assinado",
                             "versao_recomendada": "Verificar na página do fabricante", "fontes": src, "url": best_url(src)})
    n_err = sum(1 for f in findings if f["codigo_erro"] is not None)
    n_unk = sum(1 for f in findings if f["problema"].startswith("Dispositivo desconhecido"))
    return {"fabricante_equipamento": man, "modelo_equipamento": model,
            "resumo": {"total_drivers": len(drv), "com_erro": n_err, "desconhecidos": n_unk,
                       "sem_assinatura": unsigned, "possivelmente_antigos": old},
            "achados": findings}


class DriversCollector(Collector):
    key = "drivers"
    title = "Drivers"

    def run(self, ctx: Context):
        if not is_windows():
            raise PlatformUnsupported("Disponível somente no Windows.")
        raw = run_ps_json(DRV_SCRIPT, timeout=120) or {}
        data = analyze_drivers(raw)
        r = data["resumo"]
        problems = []
        if r["com_erro"]:
            problems.append(f"{r['com_erro']} dispositivo(s) com erro ou driver ausente")
        if r["sem_assinatura"]:
            problems.append(f"{r['sem_assinatura']} driver(s) sem assinatura digital")
        evidence = [f"Hardware IDs resumidos: " + ", ".join(sorted({short_hwid(f['hardware_id']) or '' for f in data['achados'] if f['hardware_id']} - {''})[:6])] if data["achados"] else []
        status = Status.ALERT if problems else Status.OK
        return self.new(status, f"{r['total_drivers']} drivers analisados; {r['com_erro']} com erro, {r['sem_assinatura']} sem assinatura",
                        data=data, problems=problems, evidence=evidence)
