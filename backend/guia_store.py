"""
Histórico de guias emitidas (tabela `guias`).

- Toda guia gerada pela API ou pela interface é gravada aqui.
- Antes de emitir, o sistema procura guias da mesma NF-e já geradas (ou aguardando a UF)
  e ainda dentro do vencimento — e devolve essas em vez de emitir outra (evita pagamento em dobro).
"""
import os
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from database import Guia
from models import GuiaGerada, NFeDados, ResultadoGuias

# Situações em que a guia da NF-e já existe e não deve ser emitida de novo
_STATUS_ATIVOS = ("gerada", "processando")


def _ambiente() -> str:
    return os.getenv("GNRE_AMBIENTE", "2")


def _parse_venc(s: str | None) -> date | None:
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def para_guia_gerada(g: Guia, completa: bool = True) -> GuiaGerada:
    """Converte o registro do banco para o modelo da API. completa=False omite XML/PDF (listagens)."""
    return GuiaGerada(
        id=g.id,
        tipo=g.tipo,
        uf=g.uf_dest,
        receita_codigo=g.receita_codigo,
        receita_descricao=g.receita_descricao or "",
        valor=Decimal(g.valor),
        data_vencimento=g.vencimento.strftime("%d/%m/%Y") if g.vencimento else None,
        codigo_barras=g.codigo_barras,
        linha_digitavel=g.linha_digitavel,
        qrcode_pix=g.qrcode_pix,
        numero_guia=g.numero_guia,
        numero_recibo=g.numero_recibo,
        pdf_base64=g.pdf_base64 if completa else None,
        gnre_xml=g.gnre_xml if completa else None,
        status=g.status,
        mensagem=g.mensagem,
        criado_em=g.criado_em.strftime("%d/%m/%Y %H:%M") if g.criado_em else None,
    )


def para_item(g: Guia, completa: bool = False) -> dict:
    """Guia + dados da NF-e, para a consulta do histórico (GET /api/guias)."""
    item = para_guia_gerada(g, completa).model_dump(mode="json")
    item.update(
        chave_nfe=g.chave_nfe, n_nf=g.n_nf, serie=g.serie, dh_emi=g.dh_emi,
        cnpj_emit=g.cnpj_emit, emitente=g.emitente, uf_emit=g.uf_emit,
        destinatario=g.destinatario, doc_dest=g.doc_dest,
        ambiente="producao" if g.ambiente == "1" else "homologacao",
        vencida=bool(g.status == "gerada" and g.vencimento and g.vencimento < date.today()),
    )
    return item


def guias_ativas_da_nfe(db: Session, chave_nfe: str | None) -> list[Guia]:
    """Guias já geradas/aguardando para a chave, no ambiente atual e ainda não vencidas."""
    if not chave_nfe:
        return []
    hoje = date.today()
    return [
        g for g in db.query(Guia)
        .filter(Guia.chave_nfe == chave_nfe, Guia.ambiente == _ambiente(), Guia.status.in_(_STATUS_ATIVOS))
        .order_by(Guia.id)
        .all()
        if g.vencimento is None or g.vencimento >= hoje
    ]


def salvar_resultado(db: Session, dados: NFeDados, resultado: ResultadoGuias) -> ResultadoGuias:
    """Grava cada guia do resultado e devolve o resultado com o id de cada guia preenchido."""
    doc_dest = dados.destinatario.cnpj or dados.destinatario.cpf
    novas: list[GuiaGerada] = []
    for gg in resultado.guias:
        g = Guia(
            ambiente=_ambiente(),
            chave_nfe=dados.chave_nfe,
            n_nf=dados.n_nf,
            serie=dados.serie,
            dh_emi=dados.dh_emi,
            cnpj_emit=dados.emitente.cnpj,
            emitente=dados.emitente.razao_social,
            uf_emit=dados.uf_emit,
            uf_dest=gg.uf or dados.uf_dest,
            destinatario=dados.destinatario.nome,
            doc_dest=doc_dest,
            tipo=gg.tipo,
            receita_codigo=gg.receita_codigo,
            receita_descricao=gg.receita_descricao,
            valor=gg.valor,
            vencimento=_parse_venc(gg.data_vencimento),
            status=gg.status,
            mensagem=gg.mensagem,
            codigo_barras=gg.codigo_barras,
            linha_digitavel=gg.linha_digitavel,
            qrcode_pix=gg.qrcode_pix,
            numero_guia=gg.numero_guia,
            numero_recibo=gg.numero_recibo,
            gnre_xml=gg.gnre_xml,
            pdf_base64=gg.pdf_base64,
        )
        db.add(g)
        db.flush()
        novas.append(gg.model_copy(update={"id": g.id, "criado_em": datetime.now().strftime("%d/%m/%Y %H:%M")}))
    db.commit()
    return resultado.model_copy(update={"guias": novas})


def aplicar_resultado(db: Session, g: Guia, result: dict) -> Guia:
    """Atualiza a guia com o retorno de uma reconsulta do recibo (GnreResultadoLote)."""
    g.status = result.get("status", g.status)
    g.mensagem = result.get("mensagem") or g.mensagem
    for campo in ("codigo_barras", "linha_digitavel", "qrcode_pix", "numero_guia"):
        if result.get(campo):
            setattr(g, campo, result[campo])
    db.commit()
    return g
