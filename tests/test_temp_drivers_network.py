import os
import pytest

from techdiag.collectors import temp_files as tf
from techdiag.collectors.drivers import analyze_drivers
from techdiag.collectors.network import (parse_speeds, parse_speedtest, parse_wifi, ping_stats, build_interfaces)
from techdiag.collectors.windows_integrity import parse_dism, parse_sfc
from techdiag.services.actions import ActionService, ConfirmationRequired
from techdiag.core.models import Context
from techdiag.services.driver_search import build_sources, best_url


def make_tree(root):
    (root / "a" / "b").mkdir(parents=True)
    (root / "x.tmp").write_bytes(b"1" * 1000)
    (root / "a" / "y.tmp").write_bytes(b"1" * 2000)
    (root / "a" / "b" / "z.tmp").write_bytes(b"1" * 3000)


def test_scan_counts(tmp_path):
    t = tmp_path / "Temp"
    make_tree(t)
    s = tf.scan_dir(str(t))
    assert (s["arquivos"], s["pastas"], s["bytes"]) == (3, 2, 6000)


def test_clean_only_allowed_and_keeps_root(tmp_path):
    t, other = tmp_path / "Temp", tmp_path / "Documentos"
    make_tree(t)
    other.mkdir()
    (other / "pessoal.docx").write_text("x")
    res = tf.clean_dir(str(t), [str(t)])
    assert res["liberado_bytes"] == 6000 and res["removidos"] == 3
    assert t.exists() and not any(t.iterdir())
    with pytest.raises(PermissionError):
        tf.clean_dir(str(other), [str(t)])
    assert (other / "pessoal.docx").exists()


def test_clean_does_not_follow_symlinks(tmp_path):
    t, target = tmp_path / "Temp", tmp_path / "alvo"
    t.mkdir(); target.mkdir()
    (target / "importante.txt").write_text("x")
    try:
        os.symlink(target, t / "link")
    except OSError:
        pytest.skip("Criar symlink exige privilegio (modo desenvolvedor/admin) neste Windows")
    tf.clean_dir(str(t), [str(t)])
    assert (target / "importante.txt").exists()


def test_clean_requires_confirmation():
    with pytest.raises(ConfirmationRequired):
        ActionService(Context()).clean_temp(["TEMP"], confirmed=False)


def test_failed_removal_is_reported(tmp_path, monkeypatch):
    t = tmp_path / "Temp"
    make_tree(t)
    real = os.remove
    monkeypatch.setattr(os, "remove", lambda p: (_ for _ in ()).throw(PermissionError()) if p.endswith("y.tmp") else real(p))
    monkeypatch.setattr(os, "chmod", lambda *a: None)
    res = tf.clean_dir(str(t), [str(t)])
    assert len(res["falhas"]) == 1 and res["falhas"][0]["caminho"].endswith("y.tmp") and (t / "a" / "y.tmp").exists()


def test_temp_roots_dedupe(monkeypatch, tmp_path):
    monkeypatch.setenv("TEMP", str(tmp_path / "AppData" / "Local" / "Temp"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    roots = {r["id"]: r for r in tf.temp_roots()}
    assert roots["LOCALAPPDATA_TEMP"]["duplicado_de"] == "TEMP"


def test_drivers_analysis():
    import datetime as dt
    raw = {"cs": [{"Manufacturer": "Dell Inc.", "Model": "Inspiron 15"}],
           "bad": [{"Name": "Dispositivo desconhecido", "ConfigManagerErrorCode": 28, "HardwareID": ["PCI\\VEN_8086&DEV_9D3D&SUBSYS_1"]},
                   {"Name": "Dispositivo desabilitado", "ConfigManagerErrorCode": 22}],
           "drv": [{"DeviceName": "Realtek Audio", "DriverVersion": "6.0", "DriverDate": "2015-01-01", "IsSigned": True, "DeviceClass": "MEDIA", "HardwareID": "HDAUDIO\\VEN_10EC"},
                   {"DeviceName": "Intel Net", "DriverVersion": "1", "DriverDate": "2024-01-01", "IsSigned": False, "DeviceClass": "NET"},
                   {"DeviceName": "Novo", "DriverDate": "2025-01-01", "IsSigned": True, "DeviceClass": "NET"}]}
    d = analyze_drivers(raw, today=dt.date(2026, 1, 1))
    assert d["resumo"]["com_erro"] == 1 and d["resumo"]["sem_assinatura"] == 1 and d["resumo"]["possivelmente_antigos"] == 1
    first = d["achados"][0]
    assert "Driver ausente" in first["problema"] and "intel.com" in first["fontes"][1]["url"] or "intel.com" in str(first["fontes"])
    assert all("site%3A" in s["url"] for a in d["achados"] for s in a["fontes"])
    assert first["versao_recomendada"].startswith("Verificar")      # nunca inventa versao


def test_driver_sources_prioritize_official():
    s = build_sources("HP", "HP 250 G7", "PCI\\VEN_10EC&DEV_8168", "Realtek")
    assert s[0]["oficial"] and "support.hp.com" in s[0]["url"]
    assert s[-1]["oficial"] is False
    assert best_url(s) == s[0]["url"]
    assert build_sources("", "", None, "") == []


WIFI_PT = """    Nome                   : Wi-Fi
    Estado                 : conectado
    SSID                   : MinhaRede
    BSSID                  : aa:bb:cc:dd:ee:ff
    Tipo de rádio          : 802.11ac
    Canal                  : 36
    Taxa de recepção (Mbps): 433,3
    Taxa de transmissão (Mbps): 300
    Sinal                  : 82%
"""
DRV_PT = "    Tipos de rádio com suporte : 802.11n 802.11a 802.11ac 802.11ax\n"


def test_parse_wifi():
    w = parse_wifi(WIFI_PT, DRV_PT)
    assert w["ssid"] == "MinhaRede" and w["canal"] == "36" and w["sinal"] == "82%"
    assert w["padrao"].startswith("802.11ac") and "Wi-Fi 5" in w["padrao"]
    assert w["taxa_recepcao_mbps"] == 433.3 and w["banda"].startswith("5 GHz")
    assert "802.11ax" in w["capacidade_maxima"]
    assert parse_wifi("Nenhuma interface sem fio", "") is None


def test_speeds_and_negotiated_vs_capacity():
    assert parse_speeds(["10 Mbps Half Duplex", "1.0 Gbps Full Duplex", "2.5 Gbps Full Duplex", "Auto Negotiation"]) == ["10 Mbps", "1 Gbps", "2.5 Gbps"]
    i = build_interfaces({"ad": [{"Name": "Ethernet", "Desc": "Realtek PCIe GbE", "Status": "Up", "Rx": 100_000_000,
                                  "Valid": ["10 Mbps Half Duplex", "1.0 Gbps Full Duplex"], "IPv4": "192.168.0.2"}]})[0]
    assert i["velocidade_negociada_mbps"] == 100 and "1 Gbps" in i["velocidades_suportadas"]   # capacidade != negociada


def test_speedtest_parse_and_ping():
    js = '{"ping":{"latency":12.3,"jitter":1.2},"download":{"bandwidth":12500000},"upload":{"bandwidth":2500000},"packetLoss":0,"isp":"Vivo","interface":{"externalIp":"1.2.3.4"},"server":{"name":"Srv","location":"Sao Paulo"}}'
    s = parse_speedtest(js)
    assert s["download_mbps"] == 100.0 and s["upload_mbps"] == 20.0 and s["provedor"] == "Vivo" and s["ip_publico"] == "1.2.3.4"
    assert ping_stats([10, 20, 10], 10) == {"latencia_ms": 13.3, "jitter_ms": 10.0, "perda_pct": 70.0}
    assert ping_stats([], 10)["perda_pct"] == 100.0


def test_integrity_parsers():
    assert parse_dism("No component store corruption detected.", 0)[0] == "ok"
    assert parse_dism("The component store is repairable.", 0)[0] == "alert"
    assert parse_dism("???", 5)[0] == "unknown"
    assert parse_sfc("Windows Resource Protection did not find any integrity violations.")[0] == "ok"
