from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .powershell import is_windows, ps_quote, run_ps_json


@dataclass
class SignatureInfo:
    status: str          # Valid, NotSigned, HashMismatch, UnknownError, Indisponivel
    signer: str = ""

    @property
    def valid(self) -> bool:
        return self.status == "Valid"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def get_signature(path: Path) -> SignatureInfo:
    if not is_windows():
        return SignatureInfo("Indisponivel")
    try:
        d = run_ps_json(
            f"Get-AuthenticodeSignature -LiteralPath {ps_quote(str(path))} | "
            "Select-Object @{n='Status';e={$_.Status.ToString()}},"
            "@{n='Signer';e={$_.SignerCertificate.Subject}}", timeout=30) or {}
        return SignatureInfo(d.get("Status") or "UnknownError", d.get("Signer") or "")
    except Exception:
        return SignatureInfo("UnknownError")
