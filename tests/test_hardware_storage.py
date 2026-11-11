from techdiag.collectors.battery import classify_battery, parse_battery_xml
from techdiag.collectors.hardware import build_hardware
from techdiag.collectors.storage import build_storage, classify_disk, media_type, parse_smart_attributes

HW = {
    "cs": [{"Manufacturer": "Dell Inc.", "Model": "Inspiron 15", "TotalPhysicalMemory": 8 * 1024 ** 3, "Name": "PC1"}],
    "os": [{"Caption": "Windows 11 Pro", "Version": "10.0", "BuildNumber": "22631", "OSArchitecture": "64 bits"}],
    "cpu": [{"Name": "Intel(R) Core(TM) i5-7200U", "Manufacturer": "GenuineIntel", "NumberOfCores": 2, "NumberOfLogicalProcessors": 4,
             "MaxClockSpeed": 2500, "CurrentClockSpeed": 2700, "Architecture": 9}],
    "ram": [{"Capacity": 8 * 1024 ** 3, "ConfiguredClockSpeed": 2400, "SMBIOSMemoryType": 26, "Manufacturer": "Kingston", "DeviceLocator": "DIMM A"}],
    "ramarr": [{"MemoryDevices": 2, "MaxCapacity": 16 * 1024 * 1024}],
    "board": [{"Manufacturer": "Dell", "Product": "0XYZ"}], "bios": [{"SMBIOSBIOSVersion": "1.2", "ReleaseDate": "2020-01-01"}],
    "gpu": [{"Name": "Intel(R) UHD Graphics 620", "AdapterRAM": 1073741824}, {"Name": "NVIDIA GeForce MX150", "AdapterRAM": 4294967295}],
    "gpumem": [{"DriverDesc": "NVIDIA GeForce MX150", "Mem": 2 * 1024 ** 3}],
    "disks": [{"Model": "SSD X", "Size": 256 * 1024 ** 3}], "nics": [{"Name": "Intel Dual Band Wireless-AC 8265"}, {"Name": "Realtek PCIe GbE"}],
    "mon": [{"ManufacturerName": [76, 71, 0], "UserFriendlyName": [76, 71, 32, 50, 52]}],
}


def test_build_hardware():
    d = build_hardware(HW)
    assert d["memoria"]["slots_livres"] == 1 and d["memoria"]["modulos"][0]["tipo"] == "DDR4"
    assert d["memoria"]["capacidade_maxima_gb"] == 16
    assert d["processador"]["arquitetura"] == "x64" and d["processador"]["temperatura_c"] is None
    assert d["video"][0]["tipo"].startswith("Integrada") and d["video"][1]["tipo"].startswith("Dedicada")
    assert d["video"][1]["memoria_gb"] == 2.0       # usa qwMemorySize, nao o AdapterRAM limitado a 4 GB
    assert [n["tipo"] for n in d["rede"]] == ["Wi-Fi", "Ethernet"]
    assert d["monitores"][0]["modelo"] == "LG 24"


def test_media_type_and_classification():
    assert media_type("NVMe", "SSD") == "SSD NVMe"
    assert media_type("SATA", "SSD") == "SSD SATA"
    assert media_type("SATA", "HDD") == "HDD"
    assert media_type("USB", "Unspecified") == "Não identificado"
    assert classify_disk({"saude_windows": "Healthy", "desgaste_pct": 5})[0] == "Saudável"
    assert classify_disk({"saude_windows": "Healthy", "desgaste_pct": 75})[0] == "Atenção"
    assert classify_disk({"saude_windows": "Healthy", "desgaste_pct": 95})[0] == "Crítico"
    assert classify_disk({"saude_windows": "Unhealthy"})[0] == "Crítico"
    assert classify_disk({"saude_windows": "Healthy", "setores_realocados": 3})[0] == "Atenção"
    assert classify_disk({"predicao_falha": True})[0] == "Crítico"
    assert classify_disk({})[0] == "Não foi possível verificar"   # nada inventado


def test_smart_parse():
    v = [0, 0] + [5, 0, 0, 100, 100, 7, 0, 0, 0, 0, 0, 0] + [9, 0, 0, 90, 90, 0x10, 0x27, 0, 0, 0, 0, 0]
    assert parse_smart_attributes(v) == {"realocados": 7, "horas": 10000}


def test_build_storage_hdd_ssd_nvme():
    raw = {"pd": [
        {"Id": 0, "Model": "WD Blue", "Media": "HDD", "Bus": "SATA", "Size": 500 * 1024 ** 3, "Health": "Healthy", "Hours": 20000, "Temp": 35,
         "Parts": [{"Letter": "C", "VolSize": 500 * 1024 ** 3, "Free": 20 * 1024 ** 3, "FS": "NTFS"}]},
        {"Id": 1, "Model": "Samsung 860", "Media": "SSD", "Bus": "SATA", "Size": 250 * 1024 ** 3, "Health": "Healthy", "Wear": 10, "Parts": []},
        {"Id": 2, "Model": "WD SN550", "Media": "SSD", "Bus": "NVMe", "Size": 500 * 1024 ** 3, "Health": "Healthy", "Wear": 92, "Parts": []},
    ], "map": [], "pred": [], "smart": []}
    d = {x["id"]: x for x in build_storage(raw)["discos"]}
    assert d[0]["tipo"] == "HDD" and d[0]["classificacao"] == "Saudável" and d[0]["particoes"][0]["livre_pct"] == 4.0
    assert d[1]["tipo"] == "SSD SATA" and d[1]["vida_restante_pct"] == 90
    assert d[2]["tipo"] == "SSD NVMe" and d[2]["classificacao"] == "Crítico"
    assert d[0]["setores_realocados"] is None     # indicador indisponivel permanece None


XML = """<?xml version="1.0"?><BatteryReport xmlns="http://schemas.microsoft.com/battery/2012"><Batteries><Battery><Id>DELL ABC</Id>
<Manufacturer>SMP</Manufacturer><Chemistry>LiP</Chemistry><DesignCapacity>40000</DesignCapacity><FullChargeCapacity>30000</FullChargeCapacity>
<CycleCount>312</CycleCount></Battery></Batteries><RecentUsage><UsageEntry Timestamp="2024-01-01T10:00:00" Ac="false"><ChargeCapacity>28000</ChargeCapacity></UsageEntry></RecentUsage>
<CapacityHistory><HistoryEntry StartDate="2024-01-01" FullChargeCapacity="30000" DesignCapacity="40000"/></CapacityHistory></BatteryReport>"""


def test_battery_parse_and_wear_formula():
    d = parse_battery_xml(XML)
    assert d["desgaste_pct"] == 25.0           # (1 - 30000/40000) * 100
    assert d["classificacao"] == "Atenção" and d["ciclos"] == 312
    assert d["historico_uso"][0]["ChargeCapacity"] == "28000" and d["historico_capacidade"][0]["FullChargeCapacity"] == "30000"
    assert [classify_battery(x) for x in (5, 15, 30, 50)] == ["Excelente", "Boa", "Atenção", "Necessita substituição"]
    assert parse_battery_xml("<BatteryReport><Batteries/></BatteryReport>") == {"possui_bateria": False}
