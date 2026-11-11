"""Caminhos, nomes de ferramentas externas e configuracoes persistentes."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

APP_NAME = "TechDiag"
APP_VERSION = "1.0.0"

# Nomes aceitos para cada ferramenta externa (colocadas pelo tecnico em tools/).
TOOL_FILES = {
    "rammap": ["RAMMap64.exe", "RAMMap.exe"],
    "bluescreenview": ["BlueScreenView.exe"],
    "speedtest": ["speedtest.exe"],
    "hwinfo": ["HWiNFO64.exe", "HWiNFO32.exe"],
}


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".local" / "share")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def tools_dirs() -> list[Path]:
    dirs = [app_dir() / "tools", app_dir() / "resources" / "tools"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass) / "resources" / "tools")
        dirs.append(Path(meipass) / "tools")
    return dirs


@dataclass
class Settings:
    speedtest_consent: bool = False      # permite enviar dados ao servico Ookla
    run_sfc: bool = False                # sfc /verifyonly (lento)
    require_signed_tools: bool = False   # bloqueia executaveis sem assinatura valida
    event_days: int = 30
    reboot_days: int = 90
    ml_token: str = ""                   # token opcional da API do Mercado Livre
    output_dir: str = ""

    @staticmethod
    def path() -> Path:
        return data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        try:
            raw = json.loads(cls.path().read_text(encoding="utf-8"))
            known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
            return cls(**known)
        except Exception:
            return cls()

    def save(self) -> None:
        self.path().write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
