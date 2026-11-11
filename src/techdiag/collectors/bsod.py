from __future__ import annotations

import csv
import io
import os
import re
import tempfile
import xml.etree.ElementTree as ET

from ..core.models import Context, Status
from ..infra.powershell import PlatformUnsupported, as_list, is_windows, run_ps_json
from ..infra.tools import ToolError, find_tool, run_tool
from .base import Collector

MINIDUMP_DIR = r"C:\Windows\Minidump"
MEMORY_DMP = r"C:\Windows\MEMORY.DMP"

BUGCHECKS = {
    0x0A: ("IRQL_NOT_LESS_OR_EQUAL", "Driver ou hardware acessando memória de forma inválida"),
    0x1A: ("MEMORY_MANAGEMENT", "Memória RAM, driver ou disco; recomenda-se testar a RAM"),
    0x1E: ("KMODE_EXCEPTION_NOT_HANDLED", "Driver em modo kernel"),
    0x3B: ("SYSTEM_SERVICE_EXCEPTION", "Driver ou software de sistema (frequentemente vídeo)"),
    0x50: ("PAGE_FAULT_IN_NONPAGED_AREA", "RAM defeituosa ou driver"),
    0x7A: ("KERNEL_DATA_INPAGE_ERROR", "Disco, cabo/controlador ou RAM"),
    0x7B: ("INACCESSIBLE_BOOT_DEVICE", "Controlador/driver de armazenamento ou disco"),
    0x7E: ("SYSTEM_THREAD_EXCEPTION_NOT_HANDLED", "Driver em modo kernel"),
    0x9C: ("MACHINE_CHECK_EXCEPTION", "Hardware (CPU, RAM, temperatura)"),
    0x9F: ("DRIVER_POWER_STATE_FAILURE", "Driver com falha de energia/suspensão"),
    0xD1: ("DRIVER_IRQL_NOT_LESS_OR_EQUAL", "Driver (rede, vídeo, armazenamento)"),
    0xEF: ("CRITICAL_PROCESS_DIED", "Processo crítico encerrado; sistema/disco/driver"),
    0x116: ("VIDEO_TDR_FAILURE", "Driver de vídeo ou GPU"),
    0x124: ("WHEA_UNCORRECTABLE_ERROR", "Hardware (CPU, RAM, placa-mãe, energia)"),
    0x133: ("DPC_WATCHDOG_VIOLATION", "Driver ou firmware (SSD/NVMe, rede)"),
    0x139: ("KERNEL_SECURITY_CHECK_FAILURE", "Driver ou memória corrompida"),
}

EVT_SCRIPT = r"""
Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-WER-SystemErrorReporting';Id=1001} -MaxEvents 50 -ErrorAction SilentlyContinue |
 ForEach-Object { [pscustomobject]@{T=$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss');X=$_.ToXml()} }
"""


def normalize_code(code: str | None) -> tuple[str, int | None]:
    m = re.search(r"0x([0-9a-fA-F]+)", code or "")
    if not m:
        return code or "", None
    v = int(m.group(1), 16)
    return f"0x{v:08X}", v


def interpret(code_int: int | None, driver: str | None) -> tuple[str, str]:
    name, hint = BUGCHECKS.get(code_int, ("", "Código não catalogado; consultar a documentação da Microsoft"))
    txt = hint
    if driver:
        txt += f". O módulo {driver} aparece associado ao dump; isto é um indício e não prova que seja a causa."
    return name, txt


def parse_bsv_csv(text: str) -> list[dict]:
    text = text.lstrip("\ufeff")
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        code, ci = normalize_code(r.get("bug check code"))
        driver = r.get("caused by driver") or None
        name, interp = interpret(ci, driver)
        rows.append({"arquivo": r.get("dump file"), "data": r.get("crash time"), "codigo": code,
                     "nome": r.get("bug check string") or name, "driver": driver,
                     "componente": r.get("file description") or r.get("product name") or None,
                     "interpretacao": interp})
    return rows


def parse_bugcheck_events(items: list[dict]) -> list[dict]:
    rows = []
    for it in items:
        try:
            root = ET.fromstring(it["X"])
            vals = [e.text or "" for e in root.iter() if e.tag.split("}")[-1] == "Data"]
        except ET.ParseError:
            continue
        code, ci = normalize_code(vals[0] if vals else "")
        name, interp = interpret(ci, None)
        rows.append({"arquivo": "(evento 1001 do Windows)", "data": it.get("T"), "codigo": code,
                     "nome": name, "driver": None, "componente": None, "interpretacao": interp})
    return rows


def list_dumps() -> tuple[list[str], dict | None, bool]:
    files, denied = [], False
    try:
        files = sorted(os.path.join(MINIDUMP_DIR, f) for f in os.listdir(MINIDUMP_DIR) if f.lower().endswith(".dmp"))
    except FileNotFoundError:
        pass
    except PermissionError:
        denied = True
    mem = None
    if os.path.exists(MEMORY_DMP):
        st = os.stat(MEMORY_DMP)
        mem = {"caminho": MEMORY_DMP, "tamanho_mb": round(st.st_size / 1024 ** 2)}
    return files, mem, denied


class BsodCollector(Collector):
    key = "bsod"
    title = "Telas azuis"

    def run(self, ctx: Context):
        if not is_windows():
            raise PlatformUnsupported("Disponível somente no Windows.")
        files, mem, denied = list_dumps()
        admin = ["Leitura de C:\\Windows\\Minidump"] if denied or (not ctx.is_admin and not files) else []
        evidence, registros, fonte = [], [], "Eventos do Windows"
        if find_tool("bluescreenview") and (files or mem):
            out = os.path.join(tempfile.gettempdir(), f"techdiag_bsv_{os.getpid()}.csv")
            try:
                run_tool("bluescreenview", ["/scomma", out, "/MiniDumpFolder", MINIDUMP_DIR] if files else
                         ["/scomma", out, "/LoadFrom", "3", "/SingleDumpFile", MEMORY_DMP],
                         timeout=120, require_signed=ctx.settings.require_signed_tools)
                if os.path.exists(out):
                    with open(out, "rb") as f:
                        raw = f.read()
                    for enc in ("utf-8-sig", "cp1252"):
                        try:
                            registros = parse_bsv_csv(raw.decode(enc))
                            break
                        except UnicodeDecodeError:
                            continue
                    fonte = "BlueScreenView"
            except ToolError as e:
                evidence.append(f"BlueScreenView não utilizado: {e}")
            finally:
                try:
                    os.remove(out)
                except OSError:
                    pass
        elif not find_tool("bluescreenview"):
            evidence.append("BlueScreenView.exe não encontrado em tools; usando eventos do Windows.")
        if not registros:
            try:
                registros = parse_bugcheck_events(as_list(run_ps_json(EVT_SCRIPT, timeout=60)))
            except Exception as e:
                evidence.append(f"Leitura de eventos de bugcheck indisponível: {e}")
        data = {"total": len(registros), "arquivos_minidump": len(files), "fonte": fonte,
                "memory_dmp": mem, "registros": registros}
        problems = [f"{len(registros)} tela(s) azul(is) registrada(s)"] if registros else []
        if registros:
            evidence.append("Drivers listados são indícios de associação, não causa definitiva.")
        return self.new(Status.ALERT if problems else Status.OK,
                        f"{len(registros)} tela(s) azul(is) ({fonte})" if registros else "Nenhuma tela azul registrada",
                        data=data, problems=problems, evidence=evidence, admin_required=admin)
