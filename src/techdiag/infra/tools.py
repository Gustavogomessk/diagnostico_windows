"""Localizacao, validacao e execucao das ferramentas externas (RAMMap, BlueScreenView, Speedtest)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..core.config import TOOL_FILES, tools_dirs
from .powershell import PowerShellError, run_process
from .signature import SignatureInfo, get_signature, sha256_file

log = logging.getLogger(__name__)


class ToolError(RuntimeError):
    pass


@dataclass
class ToolInfo:
    key: str
    path: Path
    sha256: str
    signature: SignatureInfo


def find_tool(key: str) -> Path | None:
    names = {n.lower() for n in TOOL_FILES[key]}
    for d in tools_dirs():
        if not d.is_dir():
            continue
        candidates = list(d.iterdir())
        candidates += [x for sub in d.iterdir() if sub.is_dir() for x in sub.iterdir()]
        for f in candidates:
            if f.is_file() and f.name.lower() in names:
                return f
    return None


def inspect_tool(key: str) -> ToolInfo | None:
    p = find_tool(key)
    if not p:
        return None
    return ToolInfo(key, p, sha256_file(p), get_signature(p))


def validate_tool(key: str, require_signed: bool = False) -> ToolInfo:
    info = inspect_tool(key)
    if not info:
        names = " ou ".join(TOOL_FILES[key])
        raise ToolError(f"Ferramenta não encontrada: coloque {names} na pasta tools.")
    if require_signed and not info.signature.valid:
        raise ToolError(f"{info.path.name} sem assinatura digital válida "
                        f"({info.signature.status}); execução bloqueada pelas configurações.")
    if not info.signature.valid:
        log.warning("Ferramenta %s sem assinatura valida (%s) sha256=%s",
                    info.path.name, info.signature.status, info.sha256)
    return info


def run_tool(key: str, args: list[str], timeout: int = 120, require_signed: bool = False):
    info = validate_tool(key, require_signed)
    try:
        rc, out, err = run_process([str(info.path), *args], timeout=timeout)
    except PowerShellError as e:
        raise ToolError(str(e)) from e
    return info, rc, out, err
