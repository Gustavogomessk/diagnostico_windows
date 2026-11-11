from __future__ import annotations

import dataclasses
import datetime as dt
import platform
from dataclasses import dataclass, field
from enum import Enum

from .config import Settings


class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    OK = "ok"
    ALERT = "alert"
    ERROR = "error"


STATUS_LABELS = {
    Status.PENDING: "Aguardando",
    Status.RUNNING: "Analisando...",
    Status.OK: "Concluído",
    Status.ALERT: "Atenção",
    Status.ERROR: "Erro",
}


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


@dataclass
class CheckResult:
    key: str
    title: str
    status: Status = Status.PENDING
    summary: str = ""
    data: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    reason: str = ""                       # motivo quando a verificacao falha
    admin_required: list = field(default_factory=list)
    duration: float = 0.0
    timestamp: str = ""

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CheckResult":
        d = dict(d)
        d["status"] = Status(d.get("status", "pending"))
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Session:
    client: str
    ticket: str
    technician: str = ""
    machine: str = ""
    created_at: str = field(default_factory=now_iso)
    id: int | None = None


def validate_identification(client: str, ticket: str, technician: str, machine: str) -> list[str]:
    errors = []
    if not client.strip():
        errors.append("Informe o nome do cliente ou empresa.")
    if not ticket.strip():
        errors.append("Informe o número do ticket de atendimento.")
    if not technician.strip():
        errors.append("Informe o nome do técnico responsável.")
    if not machine.strip():
        errors.append("Informe a identificação da máquina.")
    return errors


@dataclass
class Context:
    session: Session | None = None
    is_admin: bool = False
    settings: Settings = field(default_factory=Settings)
    results: dict = field(default_factory=dict)
    speedtest_consent: bool = False


def default_machine_name() -> str:
    return platform.node() or "Máquina"
