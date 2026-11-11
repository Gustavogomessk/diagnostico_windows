"""Limite termico oficial do processador (documentacao do fabricante) e avaliacao da temperatura medida.

Os dados vem de data/cpu_thermal_limits.json (com fontes). Modelo sem entrada exata ou regra de familia
conferida NAO recebe limite estimado: o resultado informa 'nao identificado' e um link de busca na ficha oficial."""
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

from ..core.config import data_dir

# Criterios do TechDiag (NAO sao do fabricante): margem em graus ate o limite oficial.
MARGEM_CRITICA_C = 10
MARGEM_ATENCAO_C = 25

_LEGACY_AMD = re.compile(r"AMD\s+(A\d+-|A\d+\s|FX|Phenom|Sempron|E[12]-|Athlon\(tm\)\s*(II|X\d|64|Dual))", re.I)
_LEGACY_INTEL = re.compile(r"Core\(TM\)\s*2|Core2|Pentium\(R\)\s*(Dual|D\b)|Pentium\(R\) CPU\s+[EP]\d", re.I)


def intel_generation(model: str) -> int | None:
    """Geracao do Intel Core pelo numero do modelo. 0 = anterior ao Core i (Core 2 etc.). None = nao identificavel."""
    if not model:
        return None
    if _LEGACY_INTEL.search(model):
        return 0
    m = re.search(r"\bi[3579]-N\d+", model)
    if m:
        return 12                                       # Alder Lake-N
    m = re.search(r"\bi[3579]-(\d{3,5})", model) or re.search(r"\bi[3579]\s+CPU\s+(\d{3})\b", model, re.I)
    if m:
        d = m.group(1)
        if len(d) == 3:
            return 1
        if len(d) == 5:
            return int(d[:2])
        return int(d[:2]) if d[0] == "1" else int(d[0])  # 4 digitos: 1135G7 -> 11a ; 6200U -> 6a
    m = re.search(r"\bm[357]-(\d)", model)
    if m:
        return int(m.group(1))
    return None


def below_cutoff(model: str, cutoff_gen: int = 6) -> tuple[bool, str]:
    """Linha de corte: Intel Core ate a 6a geracao (ou anterior) e AMD pre-Ryzen. Retorna (abaixo, motivo)."""
    g = intel_generation(model)
    if g is not None and g <= cutoff_gen:
        txt = "anterior ao Intel Core i" if g == 0 else f"Intel Core de {g}ª geração"
        return True, f"{txt} (linha de corte: {cutoff_gen}ª geração)"
    if _LEGACY_AMD.search(model or ""):
        return True, "AMD anterior à família Ryzen (equiparado à 6ª geração Intel ou anterior)"
    return False, ""


def _load() -> dict:
    out = {"modelos": [], "regras": []}
    for p in (data_dir() / "cpu_thermal_limits.json", Path(__file__).resolve().parents[1] / "data" / "cpu_thermal_limits.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out["modelos"] += d.get("modelos", [])
            out["regras"] += d.get("regras", [])
        except (OSError, ValueError):
            continue
    return out


def spec_search_url(model: str) -> str:
    dom = "ark.intel.com" if re.search(r"intel", model or "", re.I) else "amd.com" if re.search(r"amd|ryzen", model or "", re.I) else ""
    q = f"site:{dom} {model} specifications max operating temperature" if dom else f"{model} specifications max operating temperature"
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(q)


def _model_pattern(modelo: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + r"\s*".join(re.escape(p) for p in modelo.split()) + r"(?!\w)", re.I)


def lookup_limit(cpu_model: str) -> dict:
    data = _load()
    for e in data["modelos"]:
        if _model_pattern(e["modelo"]).search(cpu_model or ""):
            return {"limite_c": e["limite_c"], "tipo": e["tipo"], "confianca": e["confianca"], "fonte": e["fonte"],
                    "url": e.get("url") or spec_search_url(cpu_model), "obs": ""}
    g = intel_generation(cpu_model)
    if g is not None and re.search(r"intel", cpu_model or "", re.I):
        for r in data["regras"]:
            if r.get("familia") == "intel_core_i" and r["geracao_min"] <= g <= r["geracao_max"]:
                return {"limite_c": r["limite_c"], "tipo": r["tipo"] + " (típico da família; confirmar no ARK)", "confianca": r["confianca"],
                        "fonte": r["fonte"], "url": r["url"], "obs": ""}
    return {"limite_c": None, "tipo": None, "confianca": "nao_identificado",
            "fonte": "Limite oficial não identificado para este modelo; consulte a ficha do fabricante.",
            "url": spec_search_url(cpu_model), "obs": ""}


def evaluate(temp_c: float | None, limit_c: float | None) -> dict:
    if temp_c is None or limit_c is None:
        return {"margem_c": None, "avaliacao": "Não avaliável"}
    margem = round(limit_c - temp_c, 1)
    if margem <= MARGEM_CRITICA_C:
        aval = "Crítico"
    elif margem <= MARGEM_ATENCAO_C:
        aval = "Atenção"
    else:
        aval = "Normal"
    return {"margem_c": margem, "avaliacao": aval}
