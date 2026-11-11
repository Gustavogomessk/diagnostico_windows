"""Analise de upgrade conforme a LINHA DE CORTE da empresa. Ajuste as constantes abaixo para mudar as regras."""
from __future__ import annotations

from .cpu_thermal import below_cutoff

CUTOFF_INTEL_GEN = 6          # Intel Core ate a 6a geracao: substituir (notebook) / trocar processador (desktop)
MIN_RAM_GB = 16               # menos que isso: upgrade de RAM
MIN_SSD_LIFE_PCT = 50         # SSD com vida util restante abaixo disso: upgrade
SKIP_COMPONENT_UPGRADES_WHEN_REPLACING = True   # notebook a substituir nao recebe orcamento de RAM/SSD/bateria


def _d(results: dict, key: str) -> dict:
    r = results.get(key)
    return r.data if r and getattr(r, "data", None) else {}


def next_tier(gb_needed: float, tiers=(120, 240, 480, 500, 512, 960, 1000, 1024, 2000)) -> int:
    for t in tiers:
        if t >= gb_needed:
            return t
    return tiers[-1]


def total_ram_gb(hw: dict) -> tuple[float | None, bool]:
    """RAM instalada: soma dos modulos (exata). Sem modulos, usa o total do SO (pode vir menor por memoria reservada)."""
    mods = (hw.get("memoria") or {}).get("modulos") or []
    s = sum(m.get("capacidade_modulo_gb") or 0 for m in mods)
    if s:
        return float(s), True
    t = (hw.get("memoria") or {}).get("total_gb")
    return (float(t) if t is not None else None), False


def analyze_upgrades(results: dict) -> list[dict]:
    hw, st, mem, bat = _d(results, "hardware"), _d(results, "storage"), _d(results, "memory"), _d(results, "battery")
    recs: list[dict] = []
    sistema, cpu = hw.get("sistema") or {}, hw.get("processador") or {}
    kind = sistema.get("tipo_equipamento") or ("Notebook" if (bat.get("possui_bateria") or hw.get("bateria")) else "Não identificado")
    notebook = kind in ("Notebook", "All-in-One")
    model = cpu.get("modelo") or ""

    # 1) Processador abaixo da linha de corte
    old, why = below_cutoff(model, CUTOFF_INTEL_GEN)
    replacing = False
    if old:
        if notebook:
            replacing = True
            recs.append({
                "id": "cpu", "categoria": "Processador", "prioridade": "Alta",
                "titulo": f"Substituir o {'notebook' if kind == 'Notebook' else 'equipamento'} (processador abaixo da linha de corte)",
                "motivo": f"{model}: {why}.",
                "compatibilidade": "Em notebooks o processador normalmente é soldado: a troca não é viável. "
                                   + ("Upgrades de RAM/SSD/bateria não são recomendados para este equipamento." if SKIP_COMPONENT_UPGRADES_WHEN_REPLACING else ""),
                "requer_validacao": False, "termo_busca": None, "quantidade": 0})
        else:
            recs.append({
                "id": "cpu", "categoria": "Processador", "prioridade": "Alta",
                "titulo": "Trocar o processador (abaixo da linha de corte)",
                "motivo": f"{model}: {why}.",
                "compatibilidade": "Nesta geração a troca costuma exigir também placa-mãe e memória (plataforma nova). Requer validação técnica da compatibilidade antes do orçamento.",
                "requer_validacao": True, "termo_busca": None, "quantidade": 0})
    skip = replacing and SKIP_COMPONENT_UPGRADES_WHEN_REPLACING
    if skip:
        return recs

    # 2) RAM abaixo de 16 GB
    ram = hw.get("memoria", {})
    total, exact = total_ram_gb(hw)
    limit = MIN_RAM_GB if exact else MIN_RAM_GB - 1
    if total is not None and total < limit:
        mods = ram.get("modulos") or []
        free_slots, maxcap = ram.get("slots_livres"), ram.get("capacidade_maxima_gb")
        ddr = next((m["tipo"] for m in mods if m.get("tipo") and m["tipo"] != "Não identificado"), None)
        freq = next((m["frequencia_mhz"] for m in mods if m.get("frequencia_mhz")), None)
        form = "SODIMM (notebook)" if notebook else "DIMM (desktop)"
        add = MIN_RAM_GB - round(total)
        if maxcap and maxcap < MIN_RAM_GB:
            how, valid, term = f"A placa-mãe suporta no máximo {maxcap} GB: o upgrade para {MIN_RAM_GB} GB não é possível. Avaliar substituição do equipamento.", True, None
        elif free_slots and add in (4, 8):
            how, valid = f"Adicionar 1 módulo de {add} GB (há slot livre).", False
            term = f"memória {ddr or 'DDR4'} {add}GB {'notebook SODIMM' if notebook else 'desktop'} {freq or ''}".strip()
        else:
            how, valid = f"Substituir por kit totalizando {MIN_RAM_GB} GB (ex.: 2x8 GB), respeitando slots e capacidade máxima.", True
            term = f"memória {ddr or 'DDR4'} kit 2x8GB {'notebook SODIMM' if notebook else 'desktop'} {freq or ''}".strip()
        recs.append({
            "id": "ram", "categoria": "Memória RAM", "prioridade": "Alta" if total < 8 else "Média",
            "titulo": f"Ampliar RAM de {total:g} GB para {MIN_RAM_GB} GB (linha de corte: {MIN_RAM_GB} GB)",
            "motivo": f"RAM instalada de {total:g} GB, abaixo do mínimo de {MIN_RAM_GB} GB.",
            "compatibilidade": f"{how} Tipo inferido: {form}" + (f", {ddr}" if ddr else "") + (f", {freq} MHz" if freq else "")
                               + f". Capacidade máxima da placa: {maxcap or 'não informada'} GB.",
            "requer_validacao": bool(valid or not maxcap), "termo_busca": term, "quantidade": 1})

    # 3) Armazenamento: HDD mecanico ou SSD com vida util < 50%
    for d in st.get("discos", []):
        tipo = d.get("tipo", "")
        life = d.get("vida_restante_pct")
        cap_gb = d.get("capacidade_gb") or 0
        if tipo == "HDD":
            used = sum((p.get("usado_gb") or 0) for p in d.get("particoes", []))
            cap = next_tier(max(used * 1.5, 240))
            recs.append({
                "id": f"ssd_{d.get('id')}", "categoria": "Armazenamento", "prioridade": "Alta",
                "titulo": f"Substituir HDD mecânico {d.get('modelo')} por SSD de {cap} GB",
                "motivo": "HDD mecânico está abaixo da linha de corte (upgrade para SSD).",
                "compatibilidade": "SSD SATA 2,5\" é compatível com a maioria dos equipamentos com baia SATA; confirmar formato da baia. Fazer backup/clonagem antes.",
                "requer_validacao": True, "termo_busca": f"SSD SATA {cap}GB 2.5", "quantidade": 1})
        elif "SSD" in tipo and life is not None and life < MIN_SSD_LIFE_PCT:
            nvme = "NVMe" in tipo
            cap = next_tier(max(cap_gb, 240))
            recs.append({
                "id": f"ssd_{d.get('id')}", "categoria": "Armazenamento", "prioridade": "Alta",
                "titulo": f"Substituir SSD {d.get('modelo')} (vida útil restante {life}%)",
                "motivo": f"Vida útil restante de {life}%, abaixo da linha de corte de {MIN_SSD_LIFE_PCT}%.",
                "compatibilidade": ("Mesmo padrão M.2 NVMe do equipamento (validar tamanho 2280/2242)." if nvme else "Mesmo padrão SATA do equipamento.")
                                   + " Fazer backup/clonagem antes da troca.",
                "requer_validacao": True, "termo_busca": f"SSD {'NVMe M.2' if nvme else 'SATA'} {cap}GB", "quantidade": 1})
        if d.get("classificacao") == "Crítico" and tipo == "HDD":
            recs.append({
                "id": f"troca_{d.get('id')}", "categoria": "Armazenamento", "prioridade": "Alta",
                "titulo": f"Backup imediato: unidade {d.get('modelo')} em estado crítico", "motivo": "; ".join(d.get("motivos", [])),
                "compatibilidade": "Fazer backup antes de qualquer manutenção.", "requer_validacao": False, "termo_busca": None, "quantidade": 0})

    # 4) Bateria (substituicao por desgaste, nao e upgrade de desempenho)
    if bat.get("classificacao") in ("Atenção", "Necessita substituição"):
        recs.append({
            "id": "bateria", "categoria": "Bateria", "prioridade": "Alta" if bat["classificacao"].startswith("Necessita") else "Média",
            "titulo": "Substituir bateria", "motivo": f"Desgaste de {bat.get('desgaste_pct')}% ({bat['classificacao']}).",
            "compatibilidade": "Bateria depende do código exato do modelo: validar o part number antes da compra.",
            "requer_validacao": True, "termo_busca": f"bateria notebook {sistema.get('modelo') or ''}".strip(), "quantidade": 1})
    return recs
