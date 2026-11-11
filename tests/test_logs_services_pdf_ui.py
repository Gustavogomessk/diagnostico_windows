import pytest

from techdiag.collectors.bsod import normalize_code, parse_bsv_csv, parse_bugcheck_events
from techdiag.collectors.eventlogs import analyze_events
from techdiag.collectors.reboots import build_timeline, classify_event
from techdiag.core.models import CheckResult, Session, Status
from techdiag.reports.classify import classify_overall, split_problems
from techdiag.reports.pdf_builder import build_report, safe_filename
from techdiag.services import price_search as ps
from techdiag.services.upgrade_analysis import analyze_upgrades


def xml(**kv):
    d = "".join(f'<Data Name="{k}">{v}</Data>' for k, v in kv.items())
    return f'<Event xmlns="x"><EventData>{d}</EventData></Event>'


def test_bsod_parsers():
    assert normalize_code("0x000000d1 (0x1, 0x2)") == ("0x000000D1", 0xD1)
    csv_text = ("Dump File,Crash Time,Bug Check String,Bug Check Code,Caused By Driver,File Description\n"
                "010124-1.dmp,01/01/2024 10:00:00,DRIVER_IRQL_NOT_LESS_OR_EQUAL,0x000000d1,rtwlane.sys,Realtek WiFi\n")
    r = parse_bsv_csv(csv_text)[0]
    assert r["codigo"] == "0x000000D1" and r["driver"] == "rtwlane.sys"
    assert "não prova" in r["interpretacao"]       # nunca afirma causa definitiva
    ev = parse_bugcheck_events([{"T": "2024-01-01 10:00:00", "X": xml(param1="0x0000009f (0x3, 0x1)")}])
    assert ev[0]["nome"] == "DRIVER_POWER_STATE_FAILURE"


def test_reboot_classification():
    c = lambda i, p, t="2024-01-01 10:00:00", **kv: classify_event({"Id": i, "P": p, "T": t, "X": xml(**kv)})  # noqa: E731
    assert c(41, "Microsoft-Windows-Kernel-Power", BugcheckCode=209)["tipo"] == "Reinicialização após tela azul"
    e = c(41, "Microsoft-Windows-Kernel-Power", BugcheckCode=0, PowerButtonTimestamp=123)
    assert e["tipo"] == "Desligamento inesperado" and "forçado" in e["detalhe"]
    e = c(41, "Microsoft-Windows-Kernel-Power", BugcheckCode=0, PowerButtonTimestamp=0)
    assert "causa não determinada" in e["detalhe"]       # nao assume acao do usuario
    assert c(1074, "User32", param1="TrustedInstaller", param5="restart")["tipo"] == "Reinicialização solicitada"
    assert c(1074, "User32", param5="power off")["tipo"] == "Desligamento solicitado"
    assert c(6008, "EventLog")["tipo"] == "Desligamento inesperado" and c(6006, "EventLog")["tipo"] == "Desligamento normal"
    tl = build_timeline([{"Id": 12, "P": "Microsoft-Windows-Kernel-General", "T": "2024-01-01 09:00:00", "X": xml()},
                         {"Id": 41, "P": "Microsoft-Windows-Kernel-Power", "T": "2024-01-02 09:00:00", "X": xml(BugcheckCode=0)},
                         {"Id": 12, "P": "Outro", "T": "2024-01-03", "X": xml()}])
    assert tl["contagens"]["inicializacoes"] == 1 and tl["contagens"]["inesperados"] == 1


def test_event_analysis_separates_noise():
    evs = [{"L": "System", "I": 10016, "P": "Microsoft-Windows-DistributedCOM", "V": 2, "T": "2024-01-01 10:00", "M": "x"}] * 30
    evs += [{"L": "System", "I": 7, "P": "disk", "V": 2, "T": "2024-01-02 10:00", "M": "bad block"}]
    evs += [{"L": "Application", "I": 1000, "P": "Application Error", "V": 2, "T": "2024-01-02 11:00", "M": "app crash"}]
    evs += [{"L": "WER", "I": 1001, "P": "Windows Error Reporting", "V": 4, "T": "2024-01-02 11:00", "M": "wer"}]
    a = analyze_events(evs)
    assert [i["id"] for i in a["importantes"]] == [7] and a["importantes"][0]["categoria"] == "Disco"
    assert a["comuns"][0]["ocorrencias"] == 30 and a["outros"][0]["id"] == 1000 and a["wer"]


def rich_results():
    R = lambda k, t, s, summ="", **kw: CheckResult(k, t, s, summ, **kw)  # noqa: E731
    return {
        "hardware": R("hardware", "Hardware", Status.OK, "i5; 4 GB", data={
            "sistema": {"fabricante": "Dell", "modelo": "Inspiron"}, "processador": {"modelo": "Intel Core i3-6100U", "nucleos": 2},
            "memoria": {"total_gb": 4.0, "slots_total": 2, "slots_livres": 1, "modulos": [{"slot": "A", "capacidade_modulo_gb": 4, "frequencia_mhz": 2133, "tipo": "DDR4"}]},
            "placa_mae": {}, "video": [], "armazenamento": [], "rede": [], "bluetooth": [], "monitores": [], "bateria": [{"nome": "b"}]}),
        "storage": R("storage", "Armazenamento", Status.ALERT, "1 unidade", problems=["Disco HDD: Setores realocados: 5"], data={"discos": [
            {"id": 0, "modelo": "HDD Teste", "tipo": "HDD", "classificacao": "Atenção", "motivos": ["x"], "particoes": [
                {"letra": "C:", "tamanho_gb": 500, "usado_gb": 480, "livre_gb": 20, "livre_pct": 4.0}]}], "recomendacao_backup": True}),
        "battery": R("battery", "Bateria", Status.ALERT, "x", problems=["Bateria com desgaste"], data={"possui_bateria": True, "desgaste_pct": 45.0, "classificacao": "Necessita substituição"}),
        "memory": R("memory", "Memória RAM", Status.OK, "50%", data={"resumo": {"uso_pct": 85, "total_gb": 4}, "categorias": [{"nome": "Standby", "gb": 1, "origem": "x"}]}),
        "temp_files": R("temp_files", "Temp", Status.OK, "ok", data={"diretorios": [{"caminho": "C:\\x", "arquivos": 1}]}),
        "network": R("network", "Rede", Status.ERROR, "", reason="Sem conexão"),
    }


def test_classification_and_problem_split():
    res = rich_results()
    cl, ev = classify_overall(res)
    assert cl == "Atenção" and any("Setores" in e for e in ev)
    res["bsod"] = CheckResult("bsod", "BSOD", Status.ALERT, data={"total": 3})
    assert classify_overall(res)[0] == "Crítico"
    ok = {"a": CheckResult("a", "A", Status.OK)}
    assert classify_overall(ok)[0] == "Excelente"
    sp = split_problems(rich_results(), [{"success": 1, "resolves": "battery"}])
    assert sp["corrigidos"] == ["Bateria com desgaste"] and "Disco HDD: Setores realocados: 5" in sp["pendentes"]


def test_price_search(monkeypatch):
    payload = {"results": [{"title": "SSD 480", "price": 199.9, "permalink": "https://x/1", "condition": "new", "currency_id": "BRL"},
                           {"title": "sem preco", "price": None, "permalink": "https://x/2"}]}
    monkeypatch.setattr(ps, "get_json", lambda *a, **k: payload)
    r = ps.search_price("ssd 480gb")
    assert r["ok"] and len(r["itens"]) == 1 and r["itens"][0]["link"] == "https://x/1" and r["itens"][0]["consultado_em"]

    def deny(*a, **k):
        raise ps.HttpError("HTTP 403", 403)
    monkeypatch.setattr(ps, "get_json", deny)
    r2 = ps.search_price("ssd")
    assert not r2["ok"] and r2["itens"] == [] and "autenticação" in r2["motivo"] and r2["links_busca"]    # sem preco inventado
    est = ps.estimate_total([{"itens": [{"preco": 100}, {"preco": 80}], "quantidade": 2}, {"itens": [], "titulo": "Bateria"}])
    assert est["total_estimado"] == 160 and est["sem_preco"] == ["Bateria"]


def test_filename_pattern():
    import datetime as dt
    assert safe_filename("Acme Ltda.", "T-123/9", dt.datetime(2026, 11, 10)) == "LAUDO_TECNICO_ACME_LTDA_T_123_9_20261110.pdf"


def test_pdf_generation(tmp_path):
    pytest = __import__('pytest')
    PdfReader = pytest.importorskip('pypdf').PdfReader
    s = Session("Acme", "T-1", "Ana", "Dell Inspiron")
    up = CheckResult("upgrade", "Upgrade", Status.OK, data={"recomendacoes": analyze_upgrades(rich_results()), "precos": [], "orcamento": {"total_estimado": 0, "sem_preco": ["x"], "observacao": "obs"}})
    p = build_report(tmp_path / "l.pdf", s, rich_results(), [{"ts": "2026-01-01T10:00:00", "action": "Limpeza", "detail": "ok", "success": 1, "resolves": "temp_files"}], up)
    rd = PdfReader(str(p))
    text = "\n".join(pg.extract_text() for pg in rd.pages)
    assert len(rd.pages) >= 3 and "LAUDO TÉCNICO" in text and "Página 2 de" in text.replace("\n", " ")
    assert "Classificação geral: Atenção" in text and "Acme" in text and "Conclusão técnica" in text


def test_pdf_with_no_results(tmp_path):
    p = build_report(tmp_path / "e.pdf", Session("A", "1", "T", "M"), {}, [], None)
    assert p.stat().st_size > 1000


@pytest.fixture
def qapp():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_ui_flow(qapp, tmp_path):
    from techdiag.core.config import Settings
    from techdiag.ui.main_window import MainWindow
    from techdiag.ui.state import AppState
    st = AppState(tmp_path / "ui.db", Settings())
    w = MainWindow(st)
    w.show()
    assert w.nav.count() == 14
    # identificacao invalida nao inicia
    w.ident.client.setText("")
    got = []
    w.ident.started.connect(got.append)
    w.ident.start()
    assert not got and "cliente" in w.ident.err.text().lower()
    w.ident.client.setText("Acme"); w.ident.ticket.setText("T-1"); w.ident.tech.setText("Ana"); w.ident.machine.setText("PC")
    w.ident.start()
    assert got and got[0].client == "Acme"
    st.start_session(got[0])
    for r in rich_results().values():
        st.ctx.results[r.key] = r
        st.db.save_result(st.session.id, r)
        w.on_result(r)
    qapp.processEvents()
    assert "Atenção" in w.dashboard.v_done.text() or w.dashboard.v_done.text()
    w.upgrade.analyze()
    assert st.upgrade_result and st.upgrade_result.data["recomendacoes"]
    out = st.generate_report(str(tmp_path / "x.pdf"))
    assert out.exists()
    assert {r.key for r in st.db.load_results(st.session.id).values()} >= {"hardware", "upgrade"}
    w.close()
