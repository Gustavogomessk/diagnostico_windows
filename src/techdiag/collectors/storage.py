from __future__ import annotations

from ..core.models import Context, Status
from ..infra.powershell import PlatformUnsupported, as_list, is_windows, run_ps_json
from .base import Collector, gb, num

STORAGE_SCRIPT = r"""
$e='SilentlyContinue'
$pd=@(Get-PhysicalDisk -ErrorAction $e | ForEach-Object {
  $r=$_ | Get-StorageReliabilityCounter -ErrorAction $e
  $parts=@(Get-Partition -DiskNumber $_.DeviceId -ErrorAction $e | ForEach-Object {
     $v=$null; if($_.DriveLetter){ $v=Get-Volume -DriveLetter $_.DriveLetter -ErrorAction $e }
     [pscustomobject]@{Letter=if($_.DriveLetter){[string]$_.DriveLetter}else{$null};Size=$_.Size;Type=[string]$_.Type;
        Label=$v.FileSystemLabel;FS=$v.FileSystem;Free=$v.SizeRemaining;VolSize=$v.Size}})
  [pscustomobject]@{Id=$_.DeviceId;Name=$_.FriendlyName;Model=$_.Model;Manufacturer=$_.Manufacturer;Serial=$_.SerialNumber;
    Media=[string]$_.MediaType;Bus=[string]$_.BusType;Size=$_.Size;Health=[string]$_.HealthStatus;Oper=@($_.OperationalStatus|ForEach-Object{[string]$_});
    Temp=$r.Temperature;Wear=$r.Wear;Hours=$r.PowerOnHours;StartStop=$r.StartStopCycleCount;LoadCycles=$r.LoadUnloadCycleCount;
    ReadErr=$r.ReadErrorsTotal;ReadUnc=$r.ReadErrorsUncorrected;WriteErr=$r.WriteErrorsTotal;WriteUnc=$r.WriteErrorsUncorrected;Parts=$parts}
})
$map=@(Get-CimInstance Win32_DiskDrive -ErrorAction $e | Select-Object Index,PNPDeviceID)
$pred=@(Get-CimInstance -Namespace root/wmi -ClassName MSStorageDriver_FailurePredictStatus -ErrorAction $e | Select-Object InstanceName,PredictFailure)
$smart=@(Get-CimInstance -Namespace root/wmi -ClassName MSStorageDriver_FailurePredictData -ErrorAction $e | Select-Object InstanceName,@{n='Vendor';e={@($_.VendorSpecific)}})
[pscustomobject]@{pd=$pd;map=$map;pred=$pred;smart=$smart}
"""

SMART_IDS = {5: "realocados", 9: "horas", 12: "ciclos", 194: "temp", 196: "realoc_evt", 197: "pendentes", 198: "incorrigiveis"}


def parse_smart_attributes(vendor: list[int]) -> dict:
    """Interpreta a tabela de atributos SMART ATA (12 bytes por atributo a partir do offset 2)."""
    out: dict = {}
    for i in range(2, min(len(vendor), 2 + 30 * 12), 12):
        chunk = vendor[i:i + 12]
        if len(chunk) < 12 or chunk[0] == 0:
            continue
        name = SMART_IDS.get(chunk[0])
        if name:
            out[name] = int.from_bytes(bytes(chunk[5:11]), "little")
    return out


def media_type(bus: str, media: str) -> str:
    bus, media = (bus or "").upper(), (media or "").upper()
    if bus == "NVME":
        return "SSD NVMe"
    if media == "HDD":
        return "HDD"
    if media == "SSD":
        return "SSD SATA" if bus in ("SATA", "ATA", "SAS", "RAID", "SCSI") else f"SSD ({bus.title() or 'barramento desconhecido'})"
    return "Não identificado"


def classify_disk(d: dict) -> tuple[str, list[str]]:
    """Classifica usando apenas indicadores realmente disponiveis."""
    crit, warn, have = [], [], False
    h = (d.get("saude_windows") or "").lower()
    if h:
        have = True
    if h == "unhealthy":
        crit.append("Windows reporta o disco como Unhealthy")
    elif h == "warning":
        warn.append("Windows reporta o disco com status Warning")
    if d.get("predicao_falha"):
        crit.append("SMART prevê falha iminente (PredictFailure)")
    if d.get("predicao_falha") is False:
        have = True
    wear = d.get("desgaste_pct")
    if wear is not None:
        have = True
        if wear >= 90:
            crit.append(f"Desgaste de {wear}% (fim de vida útil)")
        elif wear >= 70:
            warn.append(f"Desgaste elevado ({wear}%)")
    for k, label in (("setores_realocados", "Setores realocados"), ("setores_pendentes", "Setores pendentes")):
        v = d.get(k)
        if v is not None:
            have = True
            if v >= 100:
                crit.append(f"{label}: {v}")
            elif v > 0:
                warn.append(f"{label}: {v}")
    for k, label in (("erros_leitura", "Erros de leitura não corrigidos"), ("erros_gravacao", "Erros de gravação não corrigidos")):
        v = d.get(k)
        if v:
            warn.append(f"{label}: {v}")
    t = d.get("temperatura_c")
    if t is not None:
        have = True
        if t >= 70:
            crit.append(f"Temperatura muito alta ({t} °C)")
        elif t >= 60:
            warn.append(f"Temperatura elevada ({t} °C)")
    if crit:
        return "Crítico", crit + warn
    if warn:
        return "Atenção", warn
    if not have:
        return "Não foi possível verificar", ["O dispositivo/controlador não expõe indicadores de saúde"]
    return "Saudável", []


def build_storage(raw: dict) -> dict:
    pnp = {num(m.get("Index")): (m.get("PNPDeviceID") or "").upper() for m in as_list(raw.get("map"))}
    pred, smart = {}, {}
    for p in as_list(raw.get("pred")):
        pred[(p.get("InstanceName") or "").upper()] = p.get("PredictFailure")
    for s in as_list(raw.get("smart")):
        smart[(s.get("InstanceName") or "").upper()] = parse_smart_attributes(as_list(s.get("Vendor")))

    def by_pnp(table, did):
        pn = pnp.get(did)
        if not pn:
            return None
        for inst, v in table.items():
            if inst.startswith(pn):
                return v
        return None

    discos = []
    for d in as_list(raw.get("pd")):
        did = num(d.get("Id"))
        attrs = by_pnp(smart, did) or {}
        wear = d.get("Wear")
        parts = []
        for p in as_list(d.get("Parts")):
            if not p.get("Letter"):
                continue
            size, free = p.get("VolSize") or p.get("Size"), p.get("Free")
            used = (size - free) if size is not None and free is not None else None
            parts.append({"letra": p["Letter"] + ":", "rotulo": p.get("Label"), "sistema_arquivos": p.get("FS"),
                          "tamanho_gb": gb(size), "usado_gb": gb(used), "livre_gb": gb(free),
                          "livre_pct": round(free / size * 100, 1) if size and free is not None else None})
        item = {
            "id": did, "modelo": (d.get("Model") or d.get("Name") or "").strip(), "fabricante": d.get("Manufacturer"),
            "numero_serie": (d.get("Serial") or "").strip() or None,
            "tipo": media_type(d.get("Bus"), d.get("Media")), "interface": d.get("Bus"),
            "capacidade_gb": gb(d.get("Size"), 0),
            "saude_windows": d.get("Health"), "estado_operacional": ", ".join(as_list(d.get("Oper"))) or None,
            "temperatura_c": d.get("Temp") if d.get("Temp") else attrs.get("temp"),
            "horas_uso": d.get("Hours") if d.get("Hours") is not None else attrs.get("horas"),
            "ciclos_inicializacao": d.get("StartStop") if d.get("StartStop") is not None else attrs.get("ciclos"),
            "erros_leitura": d.get("ReadUnc"), "erros_gravacao": d.get("WriteUnc"),
            "setores_realocados": attrs.get("realocados"), "setores_pendentes": attrs.get("pendentes"),
            "desgaste_pct": wear, "vida_restante_pct": (100 - wear) if wear is not None else None,
            "predicao_falha": by_pnp(pred, did),
            "particoes": parts,
        }
        item["classificacao"], item["motivos"] = classify_disk(item)
        discos.append(item)
    return {"discos": discos}


class StorageCollector(Collector):
    key = "storage"
    title = "Armazenamento"

    def run(self, ctx: Context):
        if not is_windows():
            raise PlatformUnsupported("Disponível somente no Windows.")
        raw = run_ps_json(STORAGE_SCRIPT, timeout=120) or {}
        data = build_storage(raw)
        discos = data["discos"]
        if not discos:
            res = self.new(Status.ERROR, "Nenhuma unidade pôde ser consultada",
                           reason="Get-PhysicalDisk não retornou unidades (execute como administrador).")
            res.admin_required = ["Consulta completa dos discos"]
            return res
        problems, evidence, admin = [], [], []
        worst = "Saudável"
        order = ["Saudável", "Não foi possível verificar", "Atenção", "Crítico"]
        for d in discos:
            evidence.append(f"{d['modelo']} ({d['tipo']}): {d['classificacao']}")
            if order.index(d["classificacao"]) > order.index(worst):
                worst = d["classificacao"]
            if d["classificacao"] in ("Atenção", "Crítico"):
                problems.append(f"Disco {d['modelo']}: " + "; ".join(d["motivos"]))
            for p in d["particoes"]:
                if p.get("livre_pct") is not None and p["livre_pct"] < 10:
                    problems.append(f"Pouco espaço livre em {p['letra']} ({p['livre_pct']}%)")
            if d["setores_realocados"] is None and not ctx.is_admin:
                admin.append(f"Atributos SMART de {d['modelo']}")
        data["recomendacao_backup"] = worst in ("Atenção", "Crítico")
        if data["recomendacao_backup"]:
            evidence.append("Recomendado backup imediato antes de qualquer manutenção.")
        status = Status.ALERT if problems else Status.OK
        summary = f"{len(discos)} unidade(s); pior classificação: {worst}"
        return self.new(status, summary, data=data, evidence=evidence, problems=problems,
                        admin_required=sorted(set(admin)))
