from __future__ import annotations

import platform
import re

from ..core.models import Context, Status
from ..infra.powershell import PlatformUnsupported, as_list, is_windows, run_ps_json
from .base import Collector, gb, num

HW_SCRIPT = r"""
$e='SilentlyContinue'
function D($v){ if($v){ $v.ToString('yyyy-MM-dd') } else { $null } }
[pscustomobject]@{
cs=@(Get-CimInstance Win32_ComputerSystem -ErrorAction $e | Select-Object Manufacturer,Model,SystemSKUNumber,TotalPhysicalMemory,Name)
os=@(Get-CimInstance Win32_OperatingSystem -ErrorAction $e | Select-Object Caption,Version,BuildNumber,OSArchitecture,@{n='InstallDate';e={D $_.InstallDate}},@{n='LastBoot';e={D $_.LastBootUpTime}})
cpu=@(Get-CimInstance Win32_Processor -ErrorAction $e | Select-Object Name,Manufacturer,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed,CurrentClockSpeed,Architecture,SocketDesignation)
ram=@(Get-CimInstance Win32_PhysicalMemory -ErrorAction $e | Select-Object Capacity,Speed,ConfiguredClockSpeed,SMBIOSMemoryType,Manufacturer,PartNumber,DeviceLocator)
ramarr=@(Get-CimInstance Win32_PhysicalMemoryArray -ErrorAction $e | Select-Object MemoryDevices,MaxCapacity,MaxCapacityEx)
board=@(Get-CimInstance Win32_BaseBoard -ErrorAction $e | Select-Object Manufacturer,Product,SerialNumber)
bios=@(Get-CimInstance Win32_BIOS -ErrorAction $e | Select-Object Manufacturer,SMBIOSBIOSVersion,SerialNumber,@{n='ReleaseDate';e={D $_.ReleaseDate}})
gpu=@(Get-CimInstance Win32_VideoController -ErrorAction $e | Select-Object Name,AdapterCompatibility,AdapterRAM,DriverVersion,@{n='DriverDate';e={D $_.DriverDate}})
gpumem=@(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*' -ErrorAction $e | Select-Object DriverDesc,@{n='Mem';e={$_.'HardwareInformation.qwMemorySize'}})
disks=@(Get-CimInstance Win32_DiskDrive -ErrorAction $e | Select-Object Model,Manufacturer,SerialNumber,Size,InterfaceType)
nics=@(Get-CimInstance Win32_NetworkAdapter -Filter 'PhysicalAdapter=True' -ErrorAction $e | Select-Object Name,Manufacturer,NetConnectionID,MACAddress)
bt=@(Get-PnpDevice -Class Bluetooth -PresentOnly -ErrorAction $e | Select-Object FriendlyName,Manufacturer,Status)
mon=@(Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorID -ErrorAction $e | Select-Object ManufacturerName,UserFriendlyName,ProductCodeID,SerialNumberID,YearOfManufacture)
bat=@(Get-CimInstance Win32_Battery -ErrorAction $e | Select-Object Name,EstimatedChargeRemaining,BatteryStatus)
usb=@(Get-PnpDevice -PresentOnly -ErrorAction $e | Where-Object {$_.InstanceId -like 'USB\VID*'} | Select-Object FriendlyName,Manufacturer,Status)
chassis=@(Get-CimInstance Win32_SystemEnclosure -ErrorAction $e | Select-Object ChassisTypes)
temp=@(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction $e | Select-Object InstanceName,CurrentTemperature)
}
"""

ARCH = {0: "x86", 5: "ARM", 9: "x64", 12: "ARM64"}
DDR = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 30: "LPDDR4", 34: "DDR5", 35: "LPDDR5"}
_INTEGRATED = re.compile(r"(Intel.*(UHD|HD Graphics|Iris)|Radeon\(TM\) Graphics|Radeon Graphics|Vega \d+ Graphics|Microsoft Basic)", re.I)


def _first(v) -> dict:
    lst = as_list(v)
    return lst[0] if lst and isinstance(lst[0], dict) else {}


def _decode_wmi_str(arr) -> str:
    if not isinstance(arr, list):
        return str(arr or "")
    return "".join(chr(c) for c in arr if isinstance(c, int) and c > 0).strip()


LAPTOP_CHASSIS = {8, 9, 10, 11, 12, 14, 18, 21, 30, 31, 32}
AIO_CHASSIS = {13}


def equipment_type(chassis_raw, has_battery: bool) -> str:
    types = set()
    for c in as_list(chassis_raw):
        for t in as_list(c.get("ChassisTypes") if isinstance(c, dict) else c):
            n = num(t)
            if n is not None:
                types.add(n)
    if types & LAPTOP_CHASSIS:
        return "Notebook"
    if types & AIO_CHASSIS:
        return "All-in-One"
    if types:
        return "Desktop"
    return "Notebook" if has_battery else "Não identificado"


def build_hardware(raw: dict) -> dict:
    cs, osr, cpu = _first(raw.get("cs")), _first(raw.get("os")), _first(raw.get("cpu"))
    board, bios = _first(raw.get("board")), _first(raw.get("bios"))
    mods = []
    for m in as_list(raw.get("ram")):
        mods.append({
            "slot": m.get("DeviceLocator"),
            "capacidade_modulo_gb": gb(m.get("Capacity"), 0),
            "frequencia_mhz": m.get("ConfiguredClockSpeed") or m.get("Speed"),
            "tipo": DDR.get(num(m.get("SMBIOSMemoryType")), "Não identificado"),
            "fabricante": (m.get("Manufacturer") or "").strip() or None,
            "part_number": (m.get("PartNumber") or "").strip() or None,
        })
    slots_total = sum(num(a.get("MemoryDevices"), 0) for a in as_list(raw.get("ramarr"))) or None
    max_kb = None
    for a in as_list(raw.get("ramarr")):
        v = num(a.get("MaxCapacityEx")) or num(a.get("MaxCapacity"))
        if v:
            max_kb = (max_kb or 0) + v
    memoria = {
        "total_gb": gb(cs.get("TotalPhysicalMemory")),
        "modulos": mods,
        "slots_total": slots_total,
        "slots_usados": len(mods) if mods else None,
        "slots_livres": (slots_total - len(mods)) if slots_total and mods else None,
        "capacidade_maxima_gb": round(max_kb / 1024 / 1024) if max_kb else None,
    }
    temps = [round(num(t.get("CurrentTemperature"), 0) / 10 - 273.15, 1) for t in as_list(raw.get("temp"))
             if num(t.get("CurrentTemperature"))]
    processador = {
        "fabricante": cpu.get("Manufacturer"), "modelo": (cpu.get("Name") or "").strip() or None,
        "nucleos": cpu.get("NumberOfCores"), "threads": cpu.get("NumberOfLogicalProcessors"),
        "frequencia_base_mhz": cpu.get("MaxClockSpeed"), "frequencia_atual_mhz": cpu.get("CurrentClockSpeed"),
        "arquitetura": ARCH.get(num(cpu.get("Architecture")), osr.get("OSArchitecture")),
        "soquete": cpu.get("SocketDesignation"),
        "temperatura_c": max(temps) if temps else None,
    }
    mem_by_name = {g.get("DriverDesc"): g.get("Mem") for g in as_list(raw.get("gpumem")) if g.get("DriverDesc")}
    video = []
    for g in as_list(raw.get("gpu")):
        name = g.get("Name") or ""
        vram = mem_by_name.get(name) or g.get("AdapterRAM")
        if isinstance(vram, list):
            vram = None
        video.append({
            "nome": name, "fabricante": g.get("AdapterCompatibility"),
            "tipo": "Integrada (estimado pelo nome)" if _INTEGRATED.search(name) else "Dedicada (estimado pelo nome)",
            "memoria_gb": gb(vram, 1) if vram else None,
            "versao_driver": g.get("DriverVersion"), "data_driver": g.get("DriverDate"),
        })
    nics = []
    for n in as_list(raw.get("nics")):
        name = n.get("Name") or ""
        if re.search(r"bluetooth", name, re.I):
            continue
        tipo = "Wi-Fi" if re.search(r"wi-?fi|wireless|802\.11|wlan", name, re.I) else "Ethernet"
        nics.append({"nome": name, "fabricante": n.get("Manufacturer"), "tipo": tipo, "mac": n.get("MACAddress")})
    return {
        "sistema": {
            "fabricante": cs.get("Manufacturer"), "modelo": cs.get("Model"), "nome": cs.get("Name"),
            "numero_serie": bios.get("SerialNumber"),
            "tipo_equipamento": equipment_type(raw.get("chassis"), bool(as_list(raw.get("bat")))),
            "sistema_operacional": osr.get("Caption"), "versao": osr.get("Version"), "build": osr.get("BuildNumber"),
            "arquitetura": osr.get("OSArchitecture"), "instalado_em": osr.get("InstallDate"),
            "ultimo_boot": osr.get("LastBoot"),
        },
        "processador": processador,
        "memoria": memoria,
        "placa_mae": {
            "fabricante": board.get("Manufacturer"), "modelo": board.get("Product"),
            "numero_serie": board.get("SerialNumber"), "versao_bios": bios.get("SMBIOSBIOSVersion"),
            "data_bios": bios.get("ReleaseDate"),
        },
        "video": video,
        "armazenamento": [{
            "modelo": d.get("Model"), "fabricante": d.get("Manufacturer"), "numero_serie": (d.get("SerialNumber") or "").strip() or None,
            "capacidade_gb": gb(d.get("Size"), 0), "interface": d.get("InterfaceType")}
            for d in as_list(raw.get("disks"))],
        "rede": nics,
        "bluetooth": [{"nome": b.get("FriendlyName"), "fabricante": b.get("Manufacturer"), "status": b.get("Status")}
                      for b in as_list(raw.get("bt"))],
        "monitores": [{"fabricante": _decode_wmi_str(m.get("ManufacturerName")),
                       "modelo": _decode_wmi_str(m.get("UserFriendlyName")) or _decode_wmi_str(m.get("ProductCodeID")),
                       "numero_serie": _decode_wmi_str(m.get("SerialNumberID")) or None,
                       "ano": m.get("YearOfManufacture")} for m in as_list(raw.get("mon"))],
        "bateria": [{"nome": b.get("Name"), "carga_pct": b.get("EstimatedChargeRemaining"), "status": b.get("BatteryStatus")}
                    for b in as_list(raw.get("bat"))],
        "usb": [{"nome": u.get("FriendlyName"), "fabricante": u.get("Manufacturer"), "status": u.get("Status")}
                for u in as_list(raw.get("usb"))][:40],
    }


def detect_machine_model() -> str:
    """Preenchimento automatico do campo de identificacao da maquina."""
    if is_windows():
        try:
            d = _first(run_ps_json("Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model", 30))
            txt = f"{d.get('Manufacturer') or ''} {d.get('Model') or ''}".strip()
            if txt:
                return txt
        except Exception:
            pass
    return platform.node() or "Máquina"


class HardwareCollector(Collector):
    key = "hardware"
    title = "Hardware"

    def run(self, ctx: Context):
        if not is_windows():
            raise PlatformUnsupported("Disponível somente no Windows.")
        raw = run_ps_json(HW_SCRIPT, timeout=90) or {}
        data = build_hardware(raw)
        notes, admin = [], []
        if data["processador"]["temperatura_c"] is None:
            admin.append("Temperatura da CPU (zona térmica ACPI) exige administrador e sensor exposto pela BIOS")
        if not ctx.is_admin and admin:
            notes = admin
        cpu = data["processador"]
        summary = f"{cpu.get('modelo') or 'CPU não identificada'}; {data['memoria'].get('total_gb')} GB de RAM"
        res = self.new(Status.OK, summary, data=data, admin_required=notes)
        if not cpu.get("modelo") or not data["placa_mae"].get("modelo"):
            res.evidence.append("Parte das informações de hardware não foi exposta pelo fabricante/BIOS.")
        return res
