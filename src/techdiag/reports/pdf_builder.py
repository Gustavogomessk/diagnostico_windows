"""Geracao do laudo tecnico em PDF (ReportLab)."""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate, Paragraph,
                                Spacer, Table, TableStyle)

from ..core.config import APP_NAME, APP_VERSION
from ..core.labels import humanize
from ..core.models import STATUS_LABELS, Session, Status
from .classify import DEFS, classify_overall, split_problems

BLUE = colors.HexColor("#1F5FBF")
DARK = colors.HexColor("#1F2937")
GREY = colors.HexColor("#6B7280")
LIGHT = colors.HexColor("#F3F6FB")
BORDER = colors.HexColor("#D5DDEA")
CLASS_COLOR = {"Excelente": "#15803D", "Bom": "#1F5FBF", "Atenção": "#B45309", "Crítico": "#B91C1C"}

_ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=_ss["Heading1"], fontName="Helvetica-Bold", fontSize=15, textColor=BLUE, spaceBefore=10, spaceAfter=6)
H2 = ParagraphStyle("H2", parent=_ss["Heading2"], fontName="Helvetica-Bold", fontSize=11, textColor=DARK, spaceBefore=8, spaceAfter=4)
BODY = ParagraphStyle("B", parent=_ss["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13, textColor=DARK, alignment=TA_LEFT)
CELL = ParagraphStyle("C", parent=BODY, fontSize=8, leading=10)
CELLB = ParagraphStyle("CB", parent=CELL, fontName="Helvetica-Bold", textColor=colors.white)
SMALL = ParagraphStyle("S", parent=BODY, fontSize=8, textColor=GREY)


def safe_filename(client: str, ticket: str, when: dt.datetime | None = None) -> str:
    when = when or dt.datetime.now()
    clean = lambda s: re.sub(r"[^A-Za-z0-9]+", "_", s.strip()).strip("_").upper() or "NA"  # noqa: E731
    return f"LAUDO_TECNICO_{clean(client)}_{clean(ticket)}_{when.strftime('%Y%m%d')}.pdf"


def fmt(v) -> str:
    if v is None or v == "":
        return "Não disponível"
    if isinstance(v, bool):
        return "Sim" if v else "Não"
    return str(v)


def P(text, style=BODY):
    return Paragraph(escape(fmt(text)) if not isinstance(text, Paragraph) else text, style)


def kv_table(d: dict, skip=()):
    rows = [[P(humanize(k), CELLB if False else CELL), P(v, CELL)] for k, v in d.items()
            if k not in skip and not isinstance(v, (list, dict))]
    if not rows:
        return None
    t = Table(rows, colWidths=[55 * mm, 115 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, BORDER), ("BACKGROUND", (0, 0), (0, -1), LIGHT),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


def list_table(items: list[dict], cols: list[str], widths: list[float] | None = None):
    if not items:
        return None
    head = [Paragraph(escape(humanize(c)), CELLB) for c in cols]
    rows = [head] + [[P(i.get(c), CELL) for c in cols] for i in items]
    total = 170 * mm
    widths = widths or [total / len(cols)] * len(cols)
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), BLUE), ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


def bullets(lines: list[str]):
    return [Paragraph("&bull; " + escape(l), BODY) for l in lines]


class NumberedCanvas(rl_canvas.Canvas):
    """Canvas de duas passagens para 'Pagina X de Y'."""
    meta: dict = {}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            if self._pageNumber > 1:
                self._chrome(total)
            super().showPage()
        super().save()

    def _chrome(self, total: int):
        w, h = A4
        m = self.meta
        self.setFillColor(BLUE)
        self.rect(0, h - 14 * mm, w, 14 * mm, stroke=0, fill=1)
        self.setFillColor(colors.white)
        self.setFont("Helvetica-Bold", 10)
        self.drawString(20 * mm, h - 9 * mm, "Laudo Técnico de Diagnóstico")
        self.setFont("Helvetica", 8.5)
        self.drawRightString(w - 20 * mm, h - 9 * mm, f"Ticket {m.get('ticket', '')}  |  {m.get('client', '')}")
        self.setStrokeColor(BORDER)
        self.line(20 * mm, 15 * mm, w - 20 * mm, 15 * mm)
        self.setFillColor(GREY)
        self.setFont("Helvetica", 8)
        self.drawString(20 * mm, 10 * mm, f"{APP_NAME} {APP_VERSION}  |  Gerado em {m.get('generated', '')}")
        self.drawRightString(w - 20 * mm, 10 * mm, f"Página {self._pageNumber} de {total}")


def _status_line(r):
    return f"{STATUS_LABELS[r.status]}: {r.summary}" if r.summary else STATUS_LABELS[r.status]


def _section(story, num, title, r=None):
    story.append(Paragraph(f"{num}. {escape(title)}", H1))
    if r is not None:
        story.append(P(_status_line(r)))
        if r.status == Status.ERROR and r.reason:
            story.append(P(f"Motivo: {r.reason}", SMALL))
        for a in r.admin_required:
            story.append(P(f"Requer administrador: {a}", SMALL))
        story.append(Spacer(1, 3))


def _missing(story, r, label):
    if r is None:
        story.append(P(f"{label}: verificação não executada nesta sessão."))
        return True
    if r.status in (Status.ERROR, Status.PENDING) and not r.data:
        return True
    return False


def build_report(path: str | Path, session: Session, results: dict, actions: list[dict], upgrade_result=None) -> Path:
    now = dt.datetime.now()
    meta = {"ticket": session.ticket, "client": session.client, "generated": now.strftime("%d/%m/%Y %H:%M")}
    NumberedCanvas.meta = meta
    doc = BaseDocTemplate(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=22 * mm, bottomMargin=22 * mm,
                          title=f"Laudo Técnico - {session.client} - {session.ticket}", author=session.technician or APP_NAME)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame])])

    classification, class_ev = classify_overall({**results, **({"upgrade": upgrade_result} if upgrade_result else {})})
    probs = split_problems(results, actions)
    g = lambda k: results.get(k)  # noqa: E731
    story: list = []

    # 1. Capa
    cover_cls = ParagraphStyle("cc", parent=BODY, fontSize=14, textColor=colors.HexColor(CLASS_COLOR[classification]), fontName="Helvetica-Bold")
    story += [Spacer(1, 45 * mm), Paragraph("LAUDO TÉCNICO", ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=30, textColor=BLUE, leading=34)),
              Paragraph("Diagnóstico de hardware e software Windows", ParagraphStyle("st", fontName="Helvetica", fontSize=13, textColor=GREY, spaceAfter=22)),
              kv_table({"cliente": session.client, "ticket": session.ticket, "tecnico": session.technician,
                        "maquina": session.machine, "data": now.strftime("%d/%m/%Y %H:%M")}) or Spacer(1, 1),
              Spacer(1, 14), Paragraph(f"Classificação geral: {classification}", cover_cls), P(DEFS[classification]), PageBreak()]
    # 2-6 identificacao
    story.append(Paragraph("2. Identificação do cliente  |  3. Ticket  |  4. Técnico  |  5. Data e horário", H1))
    ident = {"cliente": session.client, "ticket": session.ticket, "tecnico": session.technician,
             "data_do_diagnostico": session.created_at.replace("T", " "), "laudo_gerado_em": meta["generated"]}
    story.append(kv_table(ident))
    story.append(Paragraph("6. Identificação da máquina", H1))
    hw = g("hardware")
    story.append(P(f"Identificação informada: {session.machine}"))
    if hw and hw.data.get("sistema"):
        story.append(kv_table(hw.data["sistema"]) or Spacer(1, 1))
    # 7 resumo
    story.append(Paragraph("7. Resumo executivo", H1))
    story.append(P(f"Classificação geral: {classification}. {DEFS[classification]}"))
    story += bullets(class_ev[:12])
    story.append(Spacer(1, 4))
    story.append(list_table([{"nome": r.title, "status": STATUS_LABELS[r.status], "resumo": r.summary or r.reason}
                             for r in results.values() if r.key != "upgrade"], ["nome", "status", "resumo"], [40 * mm, 28 * mm, 102 * mm]) or Spacer(1, 1))
    # 8 hardware
    _section(story, 8, "Hardware completo", hw)
    if hw and hw.data:
        for k in ("processador", "memoria", "placa_mae"):
            story.append(Paragraph(humanize(k), H2))
            if hw.data.get(k):
                story.append(kv_table(hw.data[k]) or Spacer(1, 1))
        mods = hw.data.get("memoria", {}).get("modulos")
        if mods:
            story.append(list_table(mods, ["slot", "capacidade_modulo_gb", "frequencia_mhz", "tipo", "fabricante"]))
        for k, cols in (("video", ["nome", "tipo", "memoria_gb", "versao_driver"]), ("armazenamento", ["modelo", "capacidade_gb", "interface"]),
                        ("rede", ["nome", "tipo", "mac"]), ("bluetooth", ["nome", "status"]), ("monitores", ["fabricante", "modelo"]),
                        ("bateria", ["nome", "carga_pct"])):
            t = list_table(hw.data.get(k) or [], cols)
            if t:
                story += [Paragraph(humanize(k), H2), t]
    sn = g("sensors")
    if sn is not None:
        story.append(Paragraph("Temperaturas (HWiNFO) e limite oficial do processador", H2))
        story.append(P(_status_line(sn)))
        if sn.status == Status.ERROR and sn.reason:
            story.append(P(f"Motivo: {sn.reason}", SMALL))
        cpu = (sn.data or {}).get("cpu")
        if cpu:
            story.append(kv_table({k: cpu.get(k) for k in ("modelo", "leitura", "atual_c", "max_c", "media_c", "limite_oficial_c", "tipo_limite",
                                                           "margem_c", "avaliacao", "fonte_limite", "url_fonte")}) or Spacer(1, 1))
            story.append(P((sn.data or {}).get("criterio"), SMALL))
        for k in ("gpu", "discos"):
            t = list_table((sn.data or {}).get(k) or [], ["sensor", "leitura", "atual_c", "max_c"], [60 * mm, 50 * mm, 30 * mm, 30 * mm])
            if t:
                story += [Paragraph(humanize(k), H2), t]
    # 9 armazenamento
    st = g("storage")
    _section(story, 9, "Saúde do armazenamento", st)
    if st and st.data:
        for d in st.data.get("discos", []):
            story.append(Paragraph(f"{escape(str(d.get('modelo')))} - {escape(d.get('classificacao', ''))}", H2))
            story.append(kv_table({k: v for k, v in d.items() if k not in ("id",)}) or Spacer(1, 1))
            t = list_table(d.get("particoes") or [], ["letra", "sistema_arquivos", "tamanho_gb", "usado_gb", "livre_gb", "livre_pct"])
            if t:
                story.append(t)
        if st.data.get("recomendacao_backup"):
            story.append(P("Recomendado backup imediato dos dados antes de qualquer manutenção."))
    # 10 bateria
    bt = g("battery")
    _section(story, 10, "Saúde da bateria", bt)
    if bt and bt.data:
        story.append(kv_table(bt.data) or Spacer(1, 1))
        t = list_table(bt.data.get("historico_capacidade") or [], list((bt.data["historico_capacidade"][0] or {}).keys())[:5]) if bt.data.get("historico_capacidade") else None
        if t:
            story += [Paragraph("Histórico de capacidade", H2), t]
    # 11 memoria
    me = g("memory")
    _section(story, 11, "Diagnóstico de memória RAM", me)
    if me and me.data:
        story.append(kv_table(me.data.get("resumo", {})) or Spacer(1, 1))
        for k, cols in (("categorias", ["nome", "gb", "origem"]), ("top_processos", ["nome", "pid", "memoria_mb"]),
                        ("historico_diagnostico", ["data", "resultado"])):
            t = list_table(me.data.get(k) or [], cols)
            if t:
                story += [Paragraph(humanize(k), H2), t]
        story += bullets(me.evidence)
    # 12 temporarios
    tf = g("temp_files")
    _section(story, 12, "Análise de arquivos temporários", tf)
    if tf and tf.data:
        story.append(list_table(tf.data.get("diretorios") or [], ["caminho", "arquivos", "pastas", "tamanho_mb", "recuperavel_mb", "bloqueados"],
                                [52 * mm, 20 * mm, 18 * mm, 24 * mm, 30 * mm, 26 * mm]) or Spacer(1, 1))
    # 13 drivers
    dr = g("drivers")
    _section(story, 13, "Situação dos drivers", dr)
    if dr and dr.data:
        story.append(kv_table(dr.data.get("resumo", {})) or Spacer(1, 1))
        story.append(list_table(dr.data.get("achados") or [], ["dispositivo", "hardware_id", "problema", "assinatura"],
                                [45 * mm, 45 * mm, 55 * mm, 25 * mm]) or Spacer(1, 1))
    # 14 rede
    nw = g("network")
    _section(story, 14, "Diagnóstico de rede e internet", nw)
    if nw and nw.data:
        story.append(kv_table(nw.data.get("internet", {})) or Spacer(1, 1))
        story.append(list_table(nw.data.get("interfaces") or [], ["nome", "tipo", "status", "ipv4", "velocidade_negociada_mbps", "velocidades_suportadas"],
                                [28 * mm, 18 * mm, 15 * mm, 30 * mm, 35 * mm, 44 * mm]) or Spacer(1, 1))
        if nw.data.get("wifi"):
            story += [Paragraph("Wi-Fi", H2), kv_table(nw.data["wifi"]) or Spacer(1, 1)]
        if nw.data.get("speedtest"):
            story += [Paragraph("Teste de velocidade (Ookla Speedtest CLI)", H2), kv_table(nw.data["speedtest"]) or Spacer(1, 1)]
        story += bullets(nw.evidence)
    # 15 bsod
    bs = g("bsod")
    _section(story, 15, "Histórico de telas azuis", bs)
    if bs and bs.data:
        story.append(list_table(bs.data.get("registros") or [], ["data", "codigo", "nome", "driver", "interpretacao"],
                                [26 * mm, 22 * mm, 36 * mm, 24 * mm, 62 * mm]) or Spacer(1, 1))
        story += bullets(bs.evidence)
    # 16 reboots
    rb = g("reboots")
    _section(story, 16, "Histórico de reinicializações", rb)
    if rb and rb.data:
        story.append(kv_table(rb.data.get("contagens", {})) or Spacer(1, 1))
        story.append(list_table((rb.data.get("linha_do_tempo") or [])[:40], ["data", "tipo", "detalhe"], [30 * mm, 45 * mm, 95 * mm]) or Spacer(1, 1))
    # 17 logs
    ev = g("eventlogs")
    _section(story, 17, "Análise dos logs do Windows", ev)
    if ev and ev.data:
        t = list_table(ev.data.get("reinicios_tela_azul") or [], ["data_hora_reinicio", "codigo", "nome", "interpretacao"],
                       [34 * mm, 24 * mm, 42 * mm, 70 * mm])
        story += [Paragraph("Reinícios após tela azul (data e horário)", H2), t or P("Nenhum reinício após tela azul no período analisado.")]
        for k in ("importantes", "comuns"):
            t = list_table((ev.data.get(k) or [])[:25], ["log", "fonte", "id", "ocorrencias", "categoria", "impacto"],
                           [16 * mm, 38 * mm, 14 * mm, 20 * mm, 24 * mm, 58 * mm])
            if t:
                story += [Paragraph(humanize(k), H2), t]
    wi = g("windows")
    if wi:
        story += [Paragraph("Integridade do Windows", H2), P(_status_line(wi))]
        if wi.reason:
            story.append(P(f"Motivo: {wi.reason}", SMALL))
    # 18-20 problemas
    story.append(Paragraph("18. Problemas identificados", H1))
    story += bullets(probs["encontrados"]) or [P("Nenhum problema identificado.")]
    story.append(Paragraph("19. Ações corretivas recomendadas", H1))
    recs = [f"{p}" for p in probs["pendentes"]]
    story += bullets(recs) or [P("Nenhuma ação corretiva pendente.")]
    story.append(Paragraph("20. Ações efetivamente executadas", H1))
    done = [f"{a['ts'].replace('T', ' ')} - {a['action']}: {a['detail']}" for a in actions if a.get("success")]
    story += bullets(done) or [P("Nenhuma ação foi executada durante o atendimento.")]
    story.append(Paragraph("Separação por situação", H2))
    story.append(list_table([{"nome": "Encontrados", "status": len(probs["encontrados"])}, {"nome": "Corrigidos", "status": len(probs["corrigidos"])},
                             {"nome": "Pendentes", "status": len(probs["pendentes"])}], ["nome", "status"], [60 * mm, 40 * mm]))
    # 21-22 upgrade
    story.append(Paragraph("21. Recomendações de upgrade", H1))
    up = upgrade_result.data if upgrade_result else {}
    if up.get("recomendacoes"):
        for r in up["recomendacoes"]:
            story.append(KeepTogether([Paragraph(escape(f"{r['titulo']} (prioridade {r['prioridade']})"), H2), P(r["motivo"]),
                                       P("Compatibilidade: " + r["compatibilidade"] + (" Requer validação técnica." if r.get("requer_validacao") else ""), SMALL)]))
            for s in [x for x in up.get("precos", []) if x.get("id") == r["id"]]:
                t = list_table(s.get("itens", [])[:3], ["nome", "preco", "loja", "link", "consultado_em"], [48 * mm, 18 * mm, 32 * mm, 48 * mm, 24 * mm])
                story.append(t or P(s.get("motivo") or "Sem preços encontrados.", SMALL))
    else:
        story.append(P("Análise de upgrade não executada ou sem recomendações."))
    story.append(Paragraph("22. Orçamento estimado", H1))
    est = up.get("orcamento")
    if est:
        story.append(P(f"Total estimado: R$ {est['total_estimado']:.2f}. {est['observacao']}"))
        if est.get("sem_preco"):
            story.append(P("Itens sem preço (dependem de validação/consulta): " + "; ".join(est["sem_preco"]), SMALL))
    else:
        story.append(P("Sem orçamento: preços não consultados ou dependentes de validação técnica."))
    # 23 conclusao
    story.append(Paragraph("23. Conclusão técnica", H1))
    story.append(P(f"Com base nas evidências coletadas, o equipamento foi classificado como {classification}. {DEFS[classification]}"))
    story += bullets(class_ev[:10])
    unchecked = [r.title for r in results.values() if r.status == Status.ERROR]
    if unchecked:
        story.append(P("Não foi possível concluir: " + ", ".join(unchecked) + ". A conclusão não cobre estes itens.", SMALL))
    story += [Spacer(1, 18), P(f"Técnico responsável: {session.technician}")]
    doc.build(story, canvasmaker=NumberedCanvas)
    return Path(path)
