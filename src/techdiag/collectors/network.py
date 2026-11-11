from __future__ import annotations

import json
import re
import socket
import statistics

from ..core.models import Context, Status
from ..infra.powershell import PlatformUnsupported, as_list, is_windows, run_process, run_ps_json
from ..infra.tools import ToolError, run_tool
from .base import Collector

NET_SCRIPT = r"""
$e='SilentlyContinue'
$ad=@(Get-NetAdapter -Physical -ErrorAction $e | ForEach-Object {
  $cfg=Get-NetIPConfiguration -InterfaceIndex $_.ifIndex -ErrorAction $e
  $ipif=Get-NetIPInterface -InterfaceIndex $_.ifIndex -AddressFamily IPv4 -ErrorAction $e
  $adv=Get-NetAdapterAdvancedProperty -Name $_.Name -RegistryKeyword '*SpeedDuplex' -ErrorAction $e
  $w=Get-CimInstance Win32_NetworkAdapter -Filter ("InterfaceIndex="+$_.ifIndex) -ErrorAction $e
  $c=Get-CimInstance Win32_NetworkAdapterConfiguration -Filter ("InterfaceIndex="+$_.ifIndex) -ErrorAction $e
  [pscustomobject]@{Name=$_.Name;Desc=$_.InterfaceDescription;Status=[string]$_.Status;Mac=$_.MacAddress;
    Media=[string]$_.PhysicalMediaType;LinkSpeed=$_.LinkSpeed;Rx=$_.ReceiveLinkSpeed;Tx=$_.TransmitLinkSpeed;
    Manufacturer=$w.Manufacturer;IPv4=@($cfg.IPv4Address|ForEach-Object{$_.IPAddress});Gateway=@($cfg.IPv4DefaultGateway|ForEach-Object{$_.NextHop});
    DNS=@($cfg.DnsServer.ServerAddresses);Dhcp=[string]$ipif.Dhcp;DhcpServer=$c.DHCPServer;Valid=@($adv.ValidDisplayValues)}
})
[pscustomobject]@{ad=$ad}
"""

PING_SCRIPT = r"""
$r=@(Test-Connection -ComputerName 1.1.1.1 -Count 10 -ErrorAction SilentlyContinue | ForEach-Object { if($_.ResponseTime -ne $null){$_.ResponseTime}else{$_.Latency} })
[pscustomobject]@{ok=$r.Count;times=$r}
"""

_KEYS = {
    "ssid": ("ssid",), "padrao": ("radio type", "tipo de rádio", "tipo de radio"),
    "banda": ("band", "banda"), "canal": ("channel", "canal"),
    "taxa_recepcao_mbps": ("receive rate", "taxa de recepção", "taxa de recebimento"),
    "taxa_transmissao_mbps": ("transmit rate", "taxa de transmissão"),
    "sinal": ("signal", "sinal"), "descricao": ("description", "descrição"),
}
WIFI_STD = {"802.11be": "Wi-Fi 7", "802.11ax": "Wi-Fi 6/6E", "802.11ac": "Wi-Fi 5", "802.11n": "Wi-Fi 4",
            "802.11g": "Wi-Fi 3", "802.11a": "Wi-Fi 2", "802.11b": "Wi-Fi 1"}
SPEED_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(Gbps|Mbps)", re.I)


def parse_kv(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip().lower()] = v.strip()
    return out


def parse_wifi(interfaces_txt: str, drivers_txt: str = "") -> dict | None:
    kv = parse_kv(interfaces_txt)
    if not kv or not any(k.startswith(("ssid", "state", "estado")) for k in kv):
        return None
    state = next((v for k, v in kv.items() if k in ("state", "estado")), "")
    out: dict = {"estado": state}
    for field, keys in _KEYS.items():
        for k, v in kv.items():
            if k == "bssid" or k.startswith("bssid"):
                continue
            if any(k == key or k.startswith(key) for key in keys):
                out.setdefault(field, v)
                break
    ch = re.search(r"\d+", out.get("canal", "") or "")
    channel = int(ch.group()) if ch else None
    band = out.get("banda")
    if not band and channel:
        band = "2,4 GHz" if channel <= 14 else "5 GHz (ou 6 GHz)"
    out["banda"] = band
    out["frequencia"] = {"2,4 GHz": "2,4 GHz"}.get(band, band)
    for f in ("taxa_recepcao_mbps", "taxa_transmissao_mbps"):
        m = re.search(r"\d+(?:[.,]\d+)?", out.get(f, "") or "")
        out[f] = float(m.group().replace(",", ".")) if m else None
    if out.get("padrao"):
        key = out["padrao"].split()[0]
        out["padrao"] = f"{out['padrao']} ({WIFI_STD.get(key, 'padrão não catalogado')})"
    out["sinal"] = out.get("sinal")
    dk = parse_kv(drivers_txt)
    radios = next((v for k, v in dk.items() if k.startswith(("radio types supported", "tipos de rádio", "tipos de radio"))), "")
    sup = re.findall(r"802\.11[a-z]{1,2}", radios + " " + " ".join(
        l.strip() for l in drivers_txt.splitlines() if re.fullmatch(r"\s*802\.11[a-z]{1,2}(\s+802\.11[a-z]{1,2})*\s*", l)))
    if sup:
        best = max(set(sup), key=lambda s: ["802.11b", "802.11a", "802.11g", "802.11n", "802.11ac", "802.11ax", "802.11be"].index(s)
                   if s in WIFI_STD else -1)
        out["capacidade_maxima"] = f"{best} ({WIFI_STD.get(best, '?')})"
    else:
        out["capacidade_maxima"] = None
    return out


def parse_speeds(values: list[str]) -> list[str]:
    found = []
    for v in values:
        m = SPEED_RE.search(v or "")
        if m:
            n = float(m.group(1).replace(",", "."))
            mbps = n * 1000 if m.group(2).lower() == "gbps" else n
            found.append(mbps)
    return [f"{int(s) if s < 1000 else s / 1000:g} {'Mbps' if s < 1000 else 'Gbps'}" for s in sorted(set(found))]


def build_interfaces(raw: dict) -> list[dict]:
    out = []
    for a in as_list(raw.get("ad")):
        name = (a.get("Desc") or "") + " " + (a.get("Media") or "")
        wifi = bool(re.search(r"wi-?fi|wireless|802\.11|wlan|native 802", name, re.I))
        rx = a.get("Rx")
        out.append({
            "nome": a.get("Name"), "descricao": a.get("Desc"), "fabricante": a.get("Manufacturer"),
            "tipo": "Wi-Fi" if wifi else "Ethernet", "status": a.get("Status"), "mac": a.get("Mac"),
            "ipv4": ", ".join(as_list(a.get("IPv4"))) or None, "gateway": ", ".join(as_list(a.get("Gateway"))) or None,
            "dns": ", ".join(as_list(a.get("DNS"))) or None,
            "dhcp": (a.get("Dhcp") or "") + (f" (servidor {a['DhcpServer']})" if a.get("DhcpServer") else ""),
            "velocidade_negociada_mbps": round(rx / 1e6) if isinstance(rx, (int, float)) and rx < 2 ** 62 else None,
            "velocidades_suportadas": ", ".join(parse_speeds(as_list(a.get("Valid")))) or "Não informado pelo driver",
        })
    return out


def parse_speedtest(text: str) -> dict:
    d = json.loads(text)
    mbps = lambda b: round(b * 8 / 1e6, 2)  # noqa: E731  bandwidth vem em bytes/s
    srv = d.get("server") or {}
    return {
        "latencia_ms": (d.get("ping") or {}).get("latency"), "jitter_ms": (d.get("ping") or {}).get("jitter"),
        "download_mbps": mbps((d.get("download") or {}).get("bandwidth", 0)),
        "upload_mbps": mbps((d.get("upload") or {}).get("bandwidth", 0)),
        "perda_pct": d.get("packetLoss"), "provedor": d.get("isp"),
        "ip_publico": (d.get("interface") or {}).get("externalIp"),
        "servidor": f"{srv.get('name', '')} - {srv.get('location', '')}".strip(" -"),
        "resultado_url": (d.get("result") or {}).get("url"),
    }


def run_speedtest(require_signed: bool = False) -> dict:
    _, rc, out, err = run_tool("speedtest", ["--accept-license", "--accept-gdpr", "--format=json"],
                                timeout=180, require_signed=require_signed)
    if rc != 0 and not out.strip():
        raise ToolError(f"Speedtest CLI falhou: {err.strip()[:300]}")
    return parse_speedtest(out)


def ping_stats(times: list[float], count: int = 10) -> dict:
    t = [float(x) for x in times]
    if not t:
        return {"latencia_ms": None, "jitter_ms": None, "perda_pct": 100.0}
    jit = statistics.mean(abs(a - b) for a, b in zip(t, t[1:])) if len(t) > 1 else 0.0
    return {"latencia_ms": round(statistics.mean(t), 1), "jitter_ms": round(jit, 1),
            "perda_pct": round((count - len(t)) / count * 100, 1)}


def has_internet() -> bool:
    for host in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            socket.create_connection(host, timeout=4).close()
            return True
        except OSError:
            continue
    return False


class NetworkCollector(Collector):
    key = "network"
    title = "Rede e Internet"

    def run(self, ctx: Context):
        if not is_windows():
            raise PlatformUnsupported("Disponível somente no Windows.")
        raw = run_ps_json(NET_SCRIPT, timeout=90) or {}
        interfaces = build_interfaces(raw)
        wifi = None
        try:
            _, wi, _ = run_process(["netsh", "wlan", "show", "interfaces"], timeout=20)
            _, wd, _ = run_process(["netsh", "wlan", "show", "drivers"], timeout=20)
            wifi = parse_wifi(wi, wd)
        except Exception:
            wifi = None
        online = has_internet()
        internet: dict = {"conectado": online}
        problems, evidence = [], []
        if online:
            try:
                p = run_ps_json(PING_SCRIPT, timeout=40) or {}
                internet.update(ping_stats(as_list(p.get("times"))))
            except Exception as e:
                evidence.append(f"Teste de latência indisponível: {e}")
        else:
            problems.append("Sem conexão com a internet: teste de velocidade não pôde ser executado")
        speed = None
        if online and ctx.speedtest_consent:
            try:
                speed = run_speedtest(ctx.settings.require_signed_tools)
            except ToolError as e:
                evidence.append(f"Speedtest não executado: {e}")
        elif online:
            evidence.append("Speedtest não executado: requer consentimento do técnico (envia IP ao serviço Ookla).")
        if (internet.get("perda_pct") or 0) > 2:
            problems.append(f"Perda de pacotes de {internet['perda_pct']}%")
        if (internet.get("latencia_ms") or 0) > 100:
            problems.append(f"Latência elevada ({internet['latencia_ms']} ms)")
        for i in interfaces:
            if i["tipo"] == "Ethernet" and i["status"] == "Up" and i["velocidade_negociada_mbps"] and i["velocidade_negociada_mbps"] < 1000:
                evidence.append(f"{i['nome']} negociou {i['velocidade_negociada_mbps']} Mbps; confirmar se a placa/cabo suportam Gigabit.")
        data = {"internet": internet, "interfaces": interfaces, "wifi": wifi, "speedtest": speed}
        status = Status.ALERT if problems else Status.OK
        summary = "Sem internet" if not online else f"Conectado; latência {internet.get('latencia_ms')} ms"
        return self.new(status, summary, data=data, problems=problems, evidence=evidence)
