"""Leitura de sensores do HWiNFO via memoria compartilhada (interface documentada pelo fabricante: HWiNFO_SENS_SM2).

Requer HWiNFO64.exe na pasta tools e 'Shared Memory Support' habilitado no HWiNFO. O programa tenta preparar um
HWiNFO64.INI ao lado do executavel (somente se nao existir) e, se o HWiNFO nao estiver aberto, inicia-o minimizado
e o encerra ao final. Sensores exigem privilegios de administrador."""
from __future__ import annotations

import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

SIGNATURE = 0x53695748          # 'HWiS'
CLOSED = 0xDEADDEAD
HEADER = struct.Struct("<IIIqIIIIII")             # pack(1), 44 bytes
SENSOR = struct.Struct("<II128s128s")
READING = struct.Struct("<III128s128s16sdddd")    # 316 bytes
SM_NAME = "Global\\HWiNFO_SENS_SM2"
TYPES = {1: "temp", 2: "volt", 3: "fan", 4: "corrente", 5: "potencia", 6: "clock", 7: "uso", 8: "outro"}
INI_TEXT = "[Settings]\r\nSensorsOnly=1\r\nSensorsSM=1\r\n"


class HwinfoUnavailable(RuntimeError):
    pass


@dataclass
class Reading:
    sensor: str
    label: str
    unit: str
    kind: str
    value: float
    vmin: float
    vmax: float
    vavg: float


@dataclass
class Snapshot:
    version: int
    readings: list[Reading] = field(default_factory=list)


def _s(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()


def parse_shared_memory(buf: bytes) -> Snapshot:
    if len(buf) < HEADER.size:
        raise HwinfoUnavailable("Memória compartilhada do HWiNFO incompleta.")
    sig, ver, _rev, _poll, off_s, size_s, n_s, off_r, size_r, n_r = HEADER.unpack_from(buf, 0)
    if sig == CLOSED:
        raise HwinfoUnavailable("O HWiNFO foi encerrado (memória compartilhada fechada).")
    if sig != SIGNATURE:
        raise HwinfoUnavailable("Assinatura inválida na memória compartilhada do HWiNFO.")
    if size_s < SENSOR.size or size_r < READING.size:
        raise HwinfoUnavailable(f"Layout de memória compartilhada não suportado (versão {ver}).")
    sensors = []
    for i in range(n_s):
        _id, _inst, name, name_user = SENSOR.unpack_from(buf, off_s + i * size_s)
        sensors.append(_s(name_user) or _s(name))
    out = Snapshot(ver)
    for i in range(n_r):
        base = off_r + i * size_r
        if base + READING.size > len(buf):
            break
        t, sidx, _rid, lab, lab_user, unit, val, vmin, vmax, vavg = READING.unpack_from(buf, base)
        out.readings.append(Reading(sensors[sidx] if sidx < len(sensors) else "", _s(lab_user) or _s(lab), _s(unit),
                                    TYPES.get(t, "outro"), val, vmin, vmax, vavg))
    return out


def read_snapshot() -> Snapshot:
    if sys.platform != "win32":
        raise HwinfoUnavailable("Disponível somente no Windows.")
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.OpenFileMappingW.restype = wintypes.HANDLE
    k.MapViewOfFile.restype = ctypes.c_void_p
    k.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    h = k.OpenFileMappingW(0x0004, False, SM_NAME)        # FILE_MAP_READ
    if not h:
        raise HwinfoUnavailable("Memória compartilhada do HWiNFO indisponível (HWiNFO fechado ou 'Shared Memory Support' desativado).")
    try:
        ptr = k.MapViewOfFile(h, 0x0004, 0, 0, 0)
        if not ptr:
            raise HwinfoUnavailable("Não foi possível mapear a memória compartilhada do HWiNFO (execute como administrador).")
        try:
            head = ctypes.string_at(ptr, HEADER.size)
            sig, _v, _r, _p, off_s, size_s, n_s, off_r, size_r, n_r = HEADER.unpack(head)
            if sig != SIGNATURE:
                raise HwinfoUnavailable("O HWiNFO não está publicando sensores.")
            total = max(off_s + size_s * n_s, off_r + size_r * n_r)
            return parse_shared_memory(ctypes.string_at(ptr, total))
        finally:
            k.UnmapViewOfFile(ctypes.c_void_p(ptr))
    finally:
        k.CloseHandle(h)


def prepare_ini(exe: Path) -> bool:
    ini = exe.with_suffix(".INI")
    try:
        if not ini.exists():
            ini.write_text(INI_TEXT, encoding="ascii")
            return True
    except OSError:
        pass
    return False


class _ElevatedProc:
    """Processo iniciado via ShellExecuteEx 'runas' (usado quando o CreateProcess falha com erro 740)."""

    def __init__(self, handle):
        self.h = handle

    def terminate(self):
        import ctypes
        ctypes.windll.kernel32.TerminateProcess(self.h, 0)    # pode ser negado se nao estivermos elevados: ignorado

    def wait(self, timeout=None):
        import ctypes
        ctypes.windll.kernel32.WaitForSingleObject(self.h, int((timeout or 5) * 1000))
        ctypes.windll.kernel32.CloseHandle(self.h)


def _launch_elevated(exe: Path):
    import ctypes
    from ctypes import wintypes

    class SEI(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                    ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]
    info = SEI()
    info.cbSize = ctypes.sizeof(SEI)
    info.fMask = 0x40                                      # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb, info.lpFile, info.lpDirectory, info.nShow = "runas", str(exe), str(exe.parent), 7
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        raise HwinfoUnavailable("Elevação do HWiNFO cancelada ou negada. Execute o TechDiag como administrador.")
    return _ElevatedProc(info.hProcess)


def launch(exe: Path, popen=subprocess.Popen, elevated=_launch_elevated):
    """Inicia o HWiNFO minimizado. O HWiNFO exige elevacao: se o CreateProcess falhar com 740, usa 'runas'."""
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 7                                     # SW_SHOWMINNOACTIVE
    try:
        return popen([str(exe)], cwd=str(exe.parent), startupinfo=si, close_fds=True)
    except OSError as e:
        if getattr(e, "winerror", None) == 740:
            return elevated(exe)
        raise HwinfoUnavailable(f"Não foi possível iniciar o HWiNFO: {e}") from e


def collect(exe: Path | None, timeout: int = 40, settle: float = 6.0):
    """Retorna (snapshot, processo). processo != None quando NOS iniciamos o HWiNFO (quem chama deve chamar stop())."""
    try:
        snap = read_snapshot()
        if any(r.kind == "temp" for r in snap.readings):
            return snap, None
    except HwinfoUnavailable:
        pass
    if exe is None:
        raise HwinfoUnavailable("HWiNFO64.exe não encontrado na pasta tools.")
    prepare_ini(exe)
    proc = launch(exe)
    try:
        t0, last = time.time(), None
        while time.time() - t0 < timeout:
            time.sleep(1.0)
            try:
                snap = read_snapshot()
                if any(r.kind == "temp" for r in snap.readings):
                    time.sleep(settle)                     # deixa min/max/media acumularem
                    return read_snapshot(), proc
            except HwinfoUnavailable as e:
                last = e
        raise HwinfoUnavailable(str(last or "Tempo esgotado aguardando os sensores do HWiNFO.") +
                                " Abra o HWiNFO, ative 'Shared Memory Support' nas configurações e tente novamente.")
    except Exception:
        stop(proc)
        raise


def stop(proc) -> None:
    try:
        proc.terminate()
        proc.wait(8)
    except Exception:
        pass
