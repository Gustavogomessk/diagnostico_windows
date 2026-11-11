"""Acoes corretivas. Toda acao exige confirmacao explicita e fica registrada no historico."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

import psutil

from ..collectors.temp_files import clean_dir, scan_dir, temp_roots
from ..infra.powershell import PlatformUnsupported, PowerShellError, as_list, is_windows, ps_quote, run_process, run_ps_json
from ..infra.tools import ToolError, run_tool, validate_tool


class ConfirmationRequired(RuntimeError):
    pass


@dataclass
class ActionResult:
    ok: bool
    message: str
    details: dict = field(default_factory=dict)


class ActionService:
    def __init__(self, ctx, db=None):
        self.ctx, self.db = ctx, db

    def _log(self, action: str, detail: str, ok: bool, resolves: str = ""):
        if self.db and self.ctx.session and self.ctx.session.id:
            self.db.log_action(self.ctx.session.id, action, detail, ok, resolves)

    # ---- temporarios -------------------------------------------------
    def preview_temp(self, root_ids: list[str]) -> dict:
        total, rows = 0, []
        for r in temp_roots():
            if r["id"] in root_ids and not r["duplicado_de"] and os.path.isdir(r["caminho"]):
                s = scan_dir(r["caminho"])
                rec = s["bytes"] - s["bytes_bloqueados"]
                total += rec
                rows.append({"id": r["id"], "caminho": r["caminho"], "recuperavel_bytes": rec, "bloqueados": s["bloqueados"]})
        return {"total_bytes": total, "diretorios": rows}

    def clean_temp(self, root_ids: list[str], confirmed: bool, allowed_roots: list[str] | None = None) -> ActionResult:
        if not confirmed:
            raise ConfirmationRequired("A limpeza exige confirmação do técnico.")
        allowed = allowed_roots or [r["caminho"] for r in temp_roots()]
        freed, removed, failed = 0, 0, []
        for r in temp_roots():
            if r["id"] not in root_ids or r["duplicado_de"] or not os.path.isdir(r["caminho"]):
                continue
            res = clean_dir(r["caminho"], allowed)
            freed += res["liberado_bytes"]
            removed += res["removidos"]
            failed += res["falhas"]
        msg = f"{round(freed / 1024 ** 2, 1)} MB liberados; {removed} arquivo(s) removido(s); {len(failed)} não removido(s)."
        self._log("Limpeza de arquivos temporários", msg, True, "temp_files")
        return ActionResult(True, msg, {"liberado_bytes": freed, "removidos": removed, "falhas": failed})

    # ---- memoria -----------------------------------------------------
    def open_rammap(self) -> ActionResult:
        info = validate_tool("rammap", self.ctx.settings.require_signed_tools)
        subprocess.Popen([str(info.path)], close_fds=True)   # GUI; EULA da Sysinternals aparece no primeiro uso
        self._log("RAMMap aberto", info.path.name, True)
        return ActionResult(True, "RAMMap aberto.")

    def empty_standby(self, confirmed: bool) -> ActionResult:
        if not confirmed:
            raise ConfirmationRequired("Esvaziar a Standby List exige confirmação.")
        if not self.ctx.is_admin:
            return ActionResult(False, "Requer administrador.")
        _, rc, out, err = run_tool("rammap", ["-Es"], timeout=60, require_signed=self.ctx.settings.require_signed_tools)
        ok = rc == 0
        msg = "Standby List esvaziada (o Windows voltará a usar o cache normalmente)." if ok else f"RAMMap retornou {rc}: {(err or out).strip()[:200]}"
        self._log("Esvaziar Standby List", msg, ok)
        return ActionResult(ok, msg)

    def schedule_memory_test(self) -> ActionResult:
        if not is_windows():
            raise PlatformUnsupported("Disponível somente no Windows.")
        subprocess.Popen(["mdsched.exe"], close_fds=True)
        self._log("Windows Memory Diagnostic", "Assistente aberto; o técnico escolhe 'reiniciar depois'", True)
        return ActionResult(True, "Assistente do Windows Memory Diagnostic aberto. Escolha 'Verificar problemas na próxima inicialização'. "
                                  "O teste exige reinicialização; o programa não reinicia a máquina.")

    # ---- atualizar maquina (somente abre links; nao instala nada) -----
    @staticmethod
    def update_candidates(results: dict) -> list[dict]:
        r = results.get("drivers")
        return list(r.data.get("achados", [])) if r else []

    def open_driver_page(self, url: str) -> ActionResult:
        import webbrowser
        webbrowser.open(url)
        self._log("Página de driver aberta", url, True)
        return ActionResult(True, "Página aberta no navegador.")


ADV_SCRIPT = r"""
$e='SilentlyContinue'
$plan=(powercfg /getactivescheme) -join ' '
$su=@(Get-CimInstance Win32_StartupCommand -ErrorAction $e | Select-Object Name,Command)
$sv=@(Get-CimInstance Win32_Service -Filter "StartMode='Auto' AND State='Running'" -ErrorAction $e |
  Where-Object {$_.PathName -notmatch '(?i)\\windows\\|svchost|system32'} | Select-Object -First 25 DisplayName,Name)
$hags=(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers' -ErrorAction $e).HwSchMode
$vfx=(Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects' -ErrorAction $e).VisualFXSetting
[pscustomobject]@{plan=$plan;startup=$su;services=$sv;hags=$hags;vfx=$vfx}
"""
POWER_SAVER = "a1841308-3541-4fab-bc81-f71556f20b4a"


def build_suggestions(raw: dict, total_ram_gb: float | None) -> list[dict]:
    """Somente sugestoes; nada e alterado automaticamente."""
    out = []
    plan = (raw.get("plan") or "")
    if POWER_SAVER in plan.lower():
        out.append({"titulo": "Plano de energia: Economia de energia", "motivo": "O plano atual limita o desempenho da CPU.",
                    "beneficio": "Mais desempenho em tarefas pesadas.", "impacto": "Maior consumo e menor autonomia em notebooks.",
                    "abrir": "ms-settings:powersleep"})
    su = as_list(raw.get("startup"))
    if len(su) > 12:
        out.append({"titulo": f"{len(su)} programas na inicialização", "motivo": "Muitos itens aumentam o tempo de boot.",
                    "beneficio": "Inicialização mais rápida e menor uso de RAM.",
                    "impacto": "Programas desativados deixam de abrir sozinhos; revisar item a item.",
                    "abrir": "ms-settings:startupapps", "itens": [s.get("Name") for s in su[:25]]})
    sv = as_list(raw.get("services"))
    if sv:
        out.append({"titulo": f"{len(sv)} serviço(s) de terceiros em execução automática", "motivo": "Serviços de terceiros consomem recursos.",
                    "beneficio": "Possível redução de CPU/RAM em segundo plano.",
                    "impacto": "Revisar manualmente; nunca desativar serviços do Windows. O programa não altera serviços.",
                    "itens": [s.get("DisplayName") for s in sv]})
    if raw.get("hags") == 1:
        out.append({"titulo": "Agendamento de GPU acelerado por hardware (HAGS) desativado",
                    "motivo": "Suportado por este equipamento (chave presente).", "beneficio": "Pode reduzir latência gráfica em alguns jogos/aplicativos.",
                    "impacto": "Requer reinicialização e driver compatível; em alguns sistemas causa instabilidade.",
                    "abrir": "ms-settings:display-advancedgraphics"})
    if raw.get("vfx") == 1 and total_ram_gb is not None and total_ram_gb < 8:
        out.append({"titulo": "Efeitos visuais em 'melhor aparência' com pouca RAM", "motivo": f"Equipamento com {total_ram_gb} GB de RAM.",
                    "beneficio": "Interface mais fluida.", "impacto": "Visual do Windows menos refinado.", "abrir": "ms-settings:easeofaccess-visualeffects"})
    return out


def analyze_advanced(total_ram_gb: float | None = None) -> list[dict]:
    if not is_windows():
        raise PlatformUnsupported("Disponível somente no Windows.")
    return build_suggestions(run_ps_json(ADV_SCRIPT, 60) or {}, total_ram_gb)
