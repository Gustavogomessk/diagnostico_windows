from __future__ import annotations

from ..core.models import Status


def classify_overall(results: dict) -> tuple[str, list[str]]:
    """Classificacao geral com evidencias que a justificam."""
    ev: list[str] = []
    critical = False
    st = results.get("storage")
    if st and st.data:
        for d in st.data.get("discos", []):
            if d.get("classificacao") == "Crítico":
                critical = True
                ev.append(f"Disco {d['modelo']} em estado crítico: " + "; ".join(d.get("motivos", [])))
    b = results.get("bsod")
    if b and b.data.get("total", 0) >= 3:
        critical = True
        ev.append(f"{b.data['total']} telas azuis registradas")
    m = results.get("memory")
    if m and any("erros" in p.lower() for p in m.problems):
        critical = True
        ev += [p for p in m.problems if "erros" in p.lower()]
    alerts = [r for r in results.values() if r.status == Status.ALERT and r.key != "upgrade"]
    for r in alerts:
        for p in r.problems:
            if p not in ev:
                ev.append(p)
    errors = [r for r in results.values() if r.status == Status.ERROR]
    if errors:
        ev.append("Verificações não concluídas: " + ", ".join(r.title for r in errors))
    if critical:
        return "Crítico", ev
    if alerts:
        return "Atenção", ev
    recs = results.get("upgrade")
    minor = bool(recs and recs.data.get("recomendacoes")) or bool(errors)
    if minor:
        return "Bom", ev or ["Equipamento funcional, com recomendações de melhoria."]
    return "Excelente", ev or ["Nenhum problema relevante encontrado."]


DEFS = {
    "Excelente": "Equipamento sem problemas relevantes.",
    "Bom": "Equipamento funcional, com pequenas recomendações.",
    "Atenção": "Foram identificados problemas que precisam de acompanhamento.",
    "Crítico": "Foram identificados problemas que podem comprometer o funcionamento do equipamento.",
}


def split_problems(results: dict, actions: list[dict]) -> dict:
    """Separa problemas encontrados, corrigidos, pendentes e recomendacoes futuras."""
    resolved = {a["resolves"] for a in actions if a.get("success") and a.get("resolves")}
    found, corrected, pending = [], [], []
    for r in results.values():
        for p in r.problems:
            found.append(p)
            (corrected if r.key in resolved else pending).append(p)
    return {"encontrados": found, "corrigidos": corrected, "pendentes": pending}
