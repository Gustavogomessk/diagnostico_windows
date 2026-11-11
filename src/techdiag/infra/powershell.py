"""Execucao segura de PowerShell. Somente Windows."""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from typing import Any

CREATE_NO_WINDOW = 0x08000000


class PlatformUnsupported(RuntimeError):
    pass


class PowerShellError(RuntimeError):
    pass


def is_windows() -> bool:
    return sys.platform == "win32"


def as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def decode_output(raw: bytes, encoding: str | None = None) -> str:
    if encoding:
        return raw.decode(encoding, errors="replace")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or (len(raw) > 2 and raw[1:2] == b"\x00"):
        return raw.decode("utf-16", errors="replace")
    for enc in ("utf-8", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def run_process(args: list[str], timeout: int = 60, encoding: str | None = None) -> tuple[int, str, str]:
    """Executa um processo sem janela. Retorna (codigo, stdout, stderr)."""
    if not is_windows():
        raise PlatformUnsupported("Disponível somente no Windows.")
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as e:
        raise PowerShellError(f"Tempo esgotado ({timeout}s): {args[0]}") from e
    except FileNotFoundError as e:
        raise PowerShellError(f"Executável não encontrado: {args[0]}") from e
    return p.returncode, decode_output(p.stdout, encoding), decode_output(p.stderr, encoding)


def run_ps(script: str, timeout: int = 60) -> str:
    if not is_windows():
        raise PlatformUnsupported("Disponível somente no Windows.")
    full = ("$ProgressPreference='SilentlyContinue';"
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;" + script)
    enc = base64.b64encode(full.encode("utf-16-le")).decode()
    rc, out, err = run_process(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", enc], timeout=timeout, encoding="utf-8")
    if rc != 0 and not out.strip():
        raise PowerShellError(err.strip() or f"PowerShell retornou código {rc}")
    return out


_PS_NOTE_PROPS = {"value", "Length", "PSPath", "PSParentPath", "PSChildName", "PSDrive", "PSProvider"}


def unwrap_ps(v: Any) -> Any:
    """PowerShell 5.1 serializa strings com propriedades extras como {"value": "...", "PSPath": ...}.
    Converte de volta para o valor simples, em qualquer nivel."""
    if isinstance(v, dict):
        if "value" in v and set(v) <= _PS_NOTE_PROPS:
            return unwrap_ps(v["value"])
        return {k: unwrap_ps(x) for k, x in v.items()}
    if isinstance(v, list):
        return [unwrap_ps(x) for x in v]
    return v


def run_ps_json(script: str, timeout: int = 60) -> Any:
    out = run_ps("& {" + script + "} | ConvertTo-Json -Depth 6 -Compress", timeout).strip()
    if not out:
        return None
    try:
        return unwrap_ps(json.loads(out))
    except json.JSONDecodeError as e:
        raise PowerShellError(f"Saída JSON inválida do PowerShell: {e}") from e


def ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"
