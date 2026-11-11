import struct

import pytest

from techdiag.collectors.eventlogs import build_bsod_reboots
from techdiag.collectors.hardware import equipment_type
from techdiag.collectors.sensors import build_sensor_data
from techdiag.core.models import CheckResult, Status
from techdiag.infra import hwinfo
from techdiag.services.cpu_thermal import below_cutoff, evaluate, intel_generation, lookup_limit
from techdiag.services.upgrade_analysis import analyze_upgrades


def xml(**kv):
    return '<Event xmlns="x"><EventData>' + "".join(f'<Data Name="{k}">{v}</Data>' for k, v in kv.items()) + "</EventData></Event>"


def test_bsod_reboots_have_date_time_and_merge():
    items = [{"Id": 41, "T": "2026-03-05 14:22:10", "X": xml(BugcheckCode=209)},
             {"Id": 1001, "T": "2026-03-05 14:22:05", "X": xml(param1="0x000000d1 (0x1, 0x2)")},
             {"Id": 41, "T": "2026-03-01 08:00:00", "X": xml(BugcheckCode=0)},          # sem bugcheck: nao e tela azul
             {"Id": 1001, "T": "2026-02-01 09:00:00", "X": xml(param1="0x0000009f (0x3)")}]
    rows = build_bsod_reboots(items)
    assert [r["data_hora_reinicio"] for r in rows] == ["05/03/2026 14:22:10", "01/02/2026 09:00:00"]
    assert rows[0]["codigo"] == "0x000000D1" and "1001" in rows[0]["origem_evento"] and rows[1]["nome"] == "DRIVER_POWER_STATE_FAILURE"


@pytest.mark.parametrize("model,gen", [
    ("Intel(R) Core(TM) i5-6200U CPU @ 2.30GHz", 6), ("Intel(R) Core(TM) i7-7700 CPU", 7), ("Intel(R) Core(TM) i3-8100", 8),
    ("Intel(R) Core(TM) i5-10210U", 10), ("Intel(R) Core(TM) i5-1135G7", 11), ("Intel(R) Core(TM) i7-1255U", 12),
    ("Intel(R) Core(TM) i5-12400", 12), ("Intel(R) Core(TM) i5-4590", 4), ("Intel(R) Core(TM) i7 CPU 870", 1),
    ("Intel(R) Core(TM)2 Duo CPU E7500", 0), ("Intel(R) Core(TM) i3-N305", 12), ("Intel(R) Core(TM) m3-7Y30", 7),
    ("AMD Ryzen 5 5600X", None), ("Intel(R) Celeron(R) N4020", None)])
def test_intel_generation(model, gen):
    assert intel_generation(model) == gen


def test_cutoff_is_sixth_gen_and_below():
    assert below_cutoff("Intel(R) Core(TM) i5-6200U")[0] and below_cutoff("Intel(R) Core(TM) i7-4790")[0]
    assert not below_cutoff("Intel(R) Core(TM) i5-7200U")[0] and not below_cutoff("AMD Ryzen 3 3200G")[0]
    assert below_cutoff("AMD A10-7300 APU")[0] and below_cutoff("AMD FX(tm)-8350")[0]


def base_results(cpu, kind="Notebook", ram_mods=((8, 2400),), discos=(), battery=None, slots_livres=1, maxcap=32):
    hw = {"sistema": {"tipo_equipamento": kind, "modelo": "Modelo X"}, "processador": {"modelo": cpu, "nucleos": 4},
          "memoria": {"total_gb": sum(m[0] for m in ram_mods) - 0.1, "slots_livres": slots_livres, "capacidade_maxima_gb": maxcap,
                      "modulos": [{"capacidade_modulo_gb": c, "frequencia_mhz": f, "tipo": "DDR4"} for c, f in ram_mods]}}
    res = {"hardware": CheckResult("hardware", "H", Status.OK, data=hw),
           "storage": CheckResult("storage", "S", Status.OK, data={"discos": list(discos)})}
    if battery:
        res["battery"] = CheckResult("battery", "B", Status.OK, data=battery)
    return res


def ids(res):
    return {r["id"]: r for r in analyze_upgrades(res)}


def test_notebook_below_cutoff_replaces_machine_only():
    r = ids(base_results("Intel(R) Core(TM) i5-6200U", "Notebook", ram_mods=((4, 2133),),
                         discos=[{"id": 0, "tipo": "HDD", "modelo": "WD", "particoes": []}]))
    assert list(r) == ["cpu"] and "Substituir o notebook" in r["cpu"]["titulo"] and r["cpu"]["termo_busca"] is None


def test_desktop_below_cutoff_swaps_cpu_and_keeps_other_upgrades():
    r = ids(base_results("Intel(R) Core(TM) i5-6500", "Desktop", ram_mods=((8, 2133),)))
    assert "Trocar o processador" in r["cpu"]["titulo"] and r["cpu"]["requer_validacao"] and "ram" in r


def test_ram_rule_uses_16gb_cutoff_on_installed_modules():
    assert "ram" in ids(base_results("Intel(R) Core(TM) i5-8250U", ram_mods=((8, 2400),)))
    assert "ram" not in ids(base_results("Intel(R) Core(TM) i5-8250U", ram_mods=((8, 2666), (8, 2666))))   # 16 GB (SO pode mostrar 15,9)
    ram = ids(base_results("Intel(R) Core(TM) i5-8250U", ram_mods=((8, 2400),)))["ram"]
    assert "Adicionar 1 módulo de 8 GB" in ram["compatibilidade"] and ram["termo_busca"]
    no_slot = ids(base_results("Intel(R) Core(TM) i5-8250U", ram_mods=((8, 2400),), slots_livres=0))["ram"]
    assert "kit" in no_slot["compatibilidade"].lower()
    capped = ids(base_results("Intel(R) Core(TM) i5-8250U", ram_mods=((8, 2400),), maxcap=8))["ram"]
    assert "não é possível" in capped["compatibilidade"] and capped["termo_busca"] is None


def test_storage_rules_hdd_and_ssd_health_cutoff():
    d = lambda tipo, life, id=0: {"id": id, "tipo": tipo, "modelo": "M", "vida_restante_pct": life, "capacidade_gb": 256, "particoes": [], "classificacao": "Saudável"}  # noqa: E731
    cpu = "Intel(R) Core(TM) i5-8250U"
    ok = ids(base_results(cpu, ram_mods=((8, 1), (8, 1)), discos=[d("SSD SATA", 60)]))
    assert not any(k.startswith("ssd") for k in ok)
    assert "ssd_0" in ids(base_results(cpu, ram_mods=((8, 1), (8, 1)), discos=[d("SSD SATA", 49)]))
    nv = ids(base_results(cpu, ram_mods=((8, 1), (8, 1)), discos=[d("SSD NVMe", 30)]))["ssd_0"]
    assert "NVMe" in nv["termo_busca"] and "50%" in nv["motivo"]
    assert "ssd_0" in ids(base_results(cpu, ram_mods=((8, 1), (8, 1)), discos=[d("HDD", None)]))
    assert not any(k.startswith("nvme") for k in ids(base_results(cpu, ram_mods=((8, 1), (8, 1)), discos=[d("SSD SATA", 90)])))


def test_equipment_type():
    assert equipment_type([{"ChassisTypes": [10]}], False) == "Notebook"
    assert equipment_type([{"ChassisTypes": [3]}], False) == "Desktop"
    assert equipment_type([{"ChassisTypes": [13]}], False) == "All-in-One"
    assert equipment_type([], True) == "Notebook" and equipment_type([], False) == "Não identificado"


def test_thermal_limits_from_official_data():
    assert lookup_limit("AMD Ryzen 5 5600X 6-Core Processor")["limite_c"] == 95
    assert lookup_limit("AMD Ryzen 5 5600X 6-Core Processor")["confianca"] == "exato"
    assert lookup_limit("AMD Ryzen 5 5600 6-Core Processor")["limite_c"] == 90        # 5600 nao casa com 5600X
    i = lookup_limit("Intel(R) Core(TM) i5-8400 CPU @ 2.80GHz")
    assert i["limite_c"] == 100 and i["confianca"] == "familia" and "intel.com" in i["url"]
    unk = lookup_limit("AMD Ryzen 7 9999X")
    assert unk["limite_c"] is None and unk["confianca"] == "nao_identificado" and "google.com/search" in unk["url"]    # nunca inventa
    assert lookup_limit("Intel(R) Core(TM) i5-4590")["limite_c"] is None                                           # fora da familia conferida
    assert [evaluate(t, 100)["avaliacao"] for t in (60, 80, 92)] == ["Normal", "Atenção", "Crítico"]
    assert evaluate(70, None)["avaliacao"] == "Não avaliável"


def make_shm(readings, sensors=("CPU [#0]: Intel Core i5-8250U: Enhanced",)):
    pad = lambda s, n: s.encode()[:n].ljust(n, b"\x00")  # noqa: E731
    off_s = hwinfo.HEADER.size
    sens = b"".join(struct.pack("<II128s128s", i, 0, pad(n, 128), pad(n, 128)) for i, n in enumerate(sensors))
    off_r = off_s + len(sens)
    reads = b"".join(struct.pack("<III128s128s16sdddd", 1, si, i, pad(lab, 128), pad(lab, 128), pad("°C", 16), v, mn, mx, av)
                     for i, (si, lab, v, mn, mx, av) in enumerate(readings))
    head = hwinfo.HEADER.pack(hwinfo.SIGNATURE, 2, 0, 0, off_s, 264, len(sensors), off_r, 316, len(readings))
    return head + sens + reads


def test_hwinfo_shared_memory_parse_and_sensor_data():
    buf = make_shm([(0, "Core #0", 55, 40, 70, 52), (0, "Core #1", 57, 41, 72, 53), (0, "CPU Package", 58, 40, 75, 54)])
    snap = hwinfo.parse_shared_memory(buf)
    assert len(snap.readings) == 3 and snap.readings[2].label == "CPU Package" and snap.readings[2].unit == "°C"
    d = build_sensor_data(snap)
    cpu = d["cpu"]
    assert cpu["leitura"] == "CPU Package" and cpu["max_c"] == 75 and "i5-8250U" in cpu["modelo"]
    assert cpu["limite_oficial_c"] == 100 and cpu["margem_c"] == 25.0 and cpu["avaliacao"] == "Atenção"   # pico de 75 C: margem de 25 C
    assert len(d["nucleos"]) == 2
    with pytest.raises(hwinfo.HwinfoUnavailable):
        hwinfo.parse_shared_memory(struct.pack("<I", 0xDEADDEAD) + b"\x00" * 100)
    with pytest.raises(hwinfo.HwinfoUnavailable):
        hwinfo.parse_shared_memory(b"\x00" * 10)


def test_sensors_collector_reports_reason_without_windows():
    from techdiag.collectors.sensors import SensorsCollector
    from techdiag.core.models import Context
    r = SensorsCollector().execute(Context())
    assert r.status == Status.ERROR and r.reason
