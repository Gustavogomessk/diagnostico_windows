"""Fontes de download de drivers. Prioriza dominios oficiais dos fabricantes.

Os fabricantes nao oferecem API publica para localizar o driver exato, portanto os links
levam a uma busca restrita ao dominio oficial (site:) com modelo/Hardware ID. O tecnico
deve confirmar modelo e versao na pagina antes de baixar. Nenhum driver e baixado ou
instalado automaticamente e nenhuma versao 'recomendada' e inventada."""
from __future__ import annotations

import re
import urllib.parse

OEM_DOMAINS = {
    "dell": "dell.com/support", "lenovo": "support.lenovo.com", "hp": "support.hp.com",
    "hewlett": "support.hp.com", "asus": "asus.com/br/support", "acer": "acer.com/br-pt/support",
    "samsung": "samsung.com/br/support", "positivo": "meupositivo.com.br", "msi": "msi.com/support",
    "gigabyte": "gigabyte.com/Support", "toshiba": "support.dynabook.com",
}
VENDOR_DOMAINS = {
    "8086": ("Intel", "intel.com/content/www/us/en/download-center"),
    "8087": ("Intel", "intel.com/content/www/us/en/download-center"),
    "10DE": ("NVIDIA", "nvidia.com/Download"),
    "1002": ("AMD", "amd.com/en/support"),
    "10EC": ("Realtek", "realtek.com/Download"),
    "14E4": ("Broadcom", "broadcom.com/support"),
    "168C": ("Qualcomm Atheros", "qualcomm.com/support"),
    "14C3": ("MediaTek", "mediatek.com/products"),
}


def _search(domain: str, terms: str) -> str:
    q = urllib.parse.quote_plus(f"site:{domain} {terms} driver")
    return f"https://www.google.com/search?q={q}"


def short_hwid(hwid: str | None) -> str | None:
    if isinstance(hwid, (list, tuple)):
        hwid = hwid[0] if hwid else None
    if isinstance(hwid, dict):
        hwid = hwid.get("value")
    if not hwid:
        return None
    hwid = str(hwid)
    m = re.search(r"(VEN_[0-9A-F]{4}&DEV_[0-9A-F]{4}|VID_[0-9A-F]{4}&PID_[0-9A-F]{4})", hwid, re.I)
    return m.group(1).upper() if m else hwid


def build_sources(manufacturer: str, model: str, hardware_id: str | None, device_name: str = "") -> list[dict]:
    """Retorna fontes ordenadas: fabricante do equipamento, fabricante do componente, terceiros."""
    out: list[dict] = []
    man = (manufacturer or "").lower()
    for k, dom in OEM_DOMAINS.items():
        if k in man and model:
            out.append({"nome": f"Suporte oficial {manufacturer}", "url": _search(dom, model), "oficial": True})
            break
    sh = short_hwid(hardware_id)
    if sh:
        ven = re.search(r"VEN_([0-9A-F]{4})", sh)
        if ven and ven.group(1) in VENDOR_DOMAINS:
            name, dom = VENDOR_DOMAINS[ven.group(1)]
            out.append({"nome": f"Site oficial {name}", "url": _search(dom, device_name or sh), "oficial": True})
        out.append({"nome": "DriverPack (terceiro, validar)", "url": _search("driverpack.io", sh), "oficial": False})
    elif device_name:
        out.append({"nome": "DriverPack (terceiro, validar)", "url": _search("driverpack.io", device_name), "oficial": False})
    return out


def best_url(sources: list[dict]) -> str | None:
    for s in sources:
        if s["oficial"]:
            return s["url"]
    return sources[0]["url"] if sources else None
