import datetime as dt

from techdiag.collectors.drivers import analyze_drivers
from techdiag.infra.powershell import unwrap_ps


def test_unwrap_powershell_wrapped_strings():
    raw = {"a": {"value": "PCI\\VEN_8086&DEV_1", "PSPath": "x", "Length": 5}, "b": [{"value": "z", "Length": 1}], "c": {"value": 1, "outro": 2}}
    out = unwrap_ps(raw)
    assert out["a"] == "PCI\\VEN_8086&DEV_1" and out["b"] == ["z"]
    assert out["c"] == {"value": 1, "outro": 2}      # dicts reais nao sao alterados


def test_drivers_accept_wrapped_hardware_id():
    raw = {"cs": [{"Manufacturer": "Dell", "Model": "X"}],
           "bad": [{"Name": "Dispositivo desconhecido", "ConfigManagerErrorCode": 28, "HardwareID": [{"value": "PCI\\VEN_8086&DEV_9D3D", "Length": 9}]}],
           "drv": [{"DeviceName": "Net", "DriverDate": "2020-01-01", "IsSigned": False, "DeviceClass": "NET", "HardwareID": {"value": "PCI\\VEN_10EC&DEV_8168"}}]}
    d = analyze_drivers(raw, today=dt.date(2026, 1, 1))
    assert len(d["achados"]) == 2 and all(a["hardware_id"].startswith("PCI") for a in d["achados"])
