"""Pesquisa de precos. Nunca inventa precos: sem resposta valida, apenas informa o motivo e oferece links de busca."""
from __future__ import annotations

import datetime as dt
import statistics
import urllib.parse

from ..infra.http_client import HttpError, get_json

ML_URL = "https://api.mercadolibre.com/sites/MLB/search?q={q}&limit={n}&condition=new"
STORES = {
    "Mercado Livre": "https://lista.mercadolivre.com.br/{q}",
    "Kabum": "https://www.kabum.com.br/busca/{q}",
    "Pichau": "https://www.pichau.com.br/search?q={q}",
    "Terabyte": "https://www.terabyteshop.com.br/busca?str={q}",
    "Amazon Brasil": "https://www.amazon.com.br/s?k={q}",
}


def search_links(term: str) -> list[dict]:
    return [{"loja": n, "link": u.format(q=urllib.parse.quote_plus(term))} for n, u in STORES.items()]


def parse_ml(payload: dict, now: str | None = None) -> list[dict]:
    now = now or dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    items = []
    for r in payload.get("results", []):
        if r.get("price") is None or not r.get("permalink"):
            continue
        items.append({"nome": r.get("title"), "preco": float(r["price"]), "moeda": r.get("currency_id", "BRL"),
                      "loja": "Mercado Livre" + (f" ({r['seller']['nickname']})" if r.get("seller", {}).get("nickname") else ""),
                      "link": r["permalink"], "condicao": r.get("condition"), "consultado_em": now})
    return items


def search_price(term: str, token: str = "", limit: int = 5) -> dict:
    result = {"termo": term, "ok": False, "itens": [], "motivo": "", "links_busca": search_links(term),
              "consultado_em": dt.datetime.now().strftime("%d/%m/%Y %H:%M")}
    headers = {"Authorization": f"Bearer {token}"} if token else None
    try:
        payload = get_json(ML_URL.format(q=urllib.parse.quote_plus(term), n=limit), headers)
        result["itens"] = parse_ml(payload, result["consultado_em"])
        result["ok"] = bool(result["itens"])
        if not result["itens"]:
            result["motivo"] = "Nenhum resultado com preço para o termo pesquisado."
    except HttpError as e:
        if e.status in (401, 403):
            result["motivo"] = "A API do Mercado Livre recusou a consulta sem autenticação; informe um token em Configurações. Use os links de busca."
        else:
            result["motivo"] = f"Consulta de preços indisponível ({e}). Use os links de busca."
    return result


def estimate_total(searches: list[dict]) -> dict:
    """Soma o menor preco encontrado de cada recomendacao com preco; itens sem preco ficam fora e sao listados."""
    total, missing = 0.0, []
    for s in searches:
        prices = [i["preco"] for i in s.get("itens", [])]
        if prices:
            total += min(prices) * s.get("quantidade", 1)
        else:
            missing.append(s.get("titulo") or s.get("termo"))
    return {"total_estimado": round(total, 2), "sem_preco": missing,
            "observacao": "Valor com base no menor preço encontrado por item; não inclui frete nem mão de obra."}
