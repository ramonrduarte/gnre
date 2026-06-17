"""
Routes NF-e data to the correct guide generation service:
  - SP  → DARE-ICMS via portal fazenda.sp.gov.br/DareICMS/GnreLote (Playwright)
  - ES  → DUA-e via WebService app.sefaz.es.gov.br/WsDua/DuaService.asmx
  - others → national GNRE WebService gnre.pe.gov.br

When a CNPJ emit is registered in the DB:
  - Emitente address is auto-filled from the empresa record
  - The empresa's A1 certificate is used for all WebService calls
"""
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from models import NFeDados, ResultadoGuias, GuiaGerada
from uf_config import UF_GNRE_CODES, UF_DIFAL_CONFIG, uf_usa_gnre
from gnre_service import gerar_gnre
from dare_sp_service import gerar_dare_sp
from dare_es_service import gerar_dua_es
from difal_calculator import build_calculo_from_nfe
from cert_manager import get_cert_paths


_PORTAL_GNRE = "https://www.gnre.pe.gov.br:444/gnre/portal/GNRE_Principal.jsp"
_PORTAL_SP   = "https://www4.fazenda.sp.gov.br/DareICMS"
_PORTAL_ES   = "https://s1-internet.sefaz.es.gov.br/site-internet-dua/emissao/dua/icms"


def _validate_difal(dados: NFeDados) -> str | None:
    if dados.ind_final != "1":
        return "indFinal ≠ 1: esta NF-e não é para consumidor final. DIFAL pode não ser aplicável."
    if dados.uf_emit == dados.uf_dest:
        return "UF emitente = UF destinatário. DIFAL não se aplica a operações internas."
    return None


def _enrich_from_empresa(dados: NFeDados, db: Session) -> tuple[NFeDados, tuple | None]:
    """
    If the emitente CNPJ is registered in the DB, merge empresa address data
    into NFeDados and return the empresa's certificate paths.
    Returns (enriched_dados, cert_paths_or_None).
    """
    try:
        from database import Empresa
        emp = db.query(Empresa).filter(Empresa.cnpj == dados.emitente.cnpj).first()
    except Exception:
        return dados, None

    if not emp:
        return dados, None

    # Merge empresa address into emitente (empresa data is authoritative)
    emit = dados.emitente
    updated_endereco = emit.endereco.model_copy(update={
        k: v for k, v in {
            "uf": emp.uf,
            "municipio_codigo": emp.municipio_codigo,
            "municipio_nome": emp.municipio_nome,
            "cep": emp.cep,
            "logradouro": emp.logradouro,
            "numero": emp.numero,
            "bairro": emp.bairro,
            "complemento": emp.complemento,
        }.items() if v is not None
    })
    updated_emit = emit.model_copy(update={
        "razao_social": emp.razao_social,
        "ie": emp.ie or emit.ie,
        "endereco": updated_endereco,
    })
    dados = dados.model_copy(update={"emitente": updated_emit})

    cert_paths = get_cert_paths(emp.cnpj) if emp.tem_certificado else None
    return dados, cert_paths


async def gerar_guias_completo(dados: NFeDados, db: Session | None = None) -> ResultadoGuias:
    """
    Main routing function. Looks up empresa in DB to enrich emitente data
    and use the stored certificate. Falls back gracefully when DB is unavailable.
    """
    warning = _validate_difal(dados)
    uf_dest = dados.uf_dest.upper()
    data_pag = dados.data_pagamento or date.today()

    cert_paths: tuple | None = None
    if db is not None:
        dados, cert_paths = _enrich_from_empresa(dados, db)

    difal_val = dados.difal.v_icms_uf_dest
    fecp_val = dados.difal.v_fcp_uf_dest
    calculo = build_calculo_from_nfe(dados)

    guias: list[GuiaGerada] = []

    if uf_usa_gnre(uf_dest):
        codes = UF_GNRE_CODES.get(uf_dest, {"difal": "100102", "fecp": None})
        uf_cfg = UF_DIFAL_CONFIG.get(uf_dest) or {}
        fecp_mesmo_guia: bool = uf_cfg.get("fecp_mesmo_guia", False)

        if difal_val > Decimal("0") and codes["difal"]:
            # Para UFs com fecp_mesmo_guia (RJ, RO, RS, SE), o FCP vai dentro do
            # próprio guia DIFAL como valor tipo="12" — sem guia separada.
            fecp_para_difal = fecp_val if fecp_mesmo_guia else None
            guia_difal = gerar_gnre(
                dados,
                receita=codes["difal"],
                descricao=f"ICMS DIFAL EC 87/2015 – {uf_dest}",
                valor=difal_val,
                data_pag=data_pag,
                cert_paths=cert_paths,
                fecp_valor=fecp_para_difal,
            )
            guias.append(guia_difal)

        # Guia FCP separada apenas para estados sem fecp_mesmo_guia
        if fecp_val > Decimal("0") and codes.get("fecp") and not fecp_mesmo_guia:
            guia_fecp = gerar_gnre(
                dados,
                receita=codes["fecp"],
                descricao=f"ICMS FCP Consumidor Final – {uf_dest}",
                valor=fecp_val,
                data_pag=data_pag,
                cert_paths=cert_paths,
            )
            guias.append(guia_fecp)

        portal_url = _PORTAL_GNRE
    elif uf_dest == "ES":
        # ES usa DUA-e via WebService próprio (não GNRE nacional)
        if difal_val > Decimal("0"):
            guia_es = gerar_dua_es(
                dados,
                receita="3867",
                descricao="ICMS Diferencial de Alíquota EC 87/2015 – ES (DUA-e)",
                valor=difal_val,
                data_pag=data_pag,
                cert_paths=cert_paths,
            )
            guias.append(guia_es)
        portal_url = _PORTAL_ES
    else:
        # SP → DARE-ICMS via portal fazenda.sp.gov.br
        guias = await gerar_dare_sp(dados, difal_val, fecp_val, data_pag)
        portal_url = _PORTAL_SP

    if warning and guias:
        original = guias[0].mensagem or ""
        guias[0] = guias[0].model_copy(update={"mensagem": f"⚠️ {warning}\n{original}".strip()})

    return ResultadoGuias(
        chave_nfe=dados.chave_nfe,
        uf_emit=dados.uf_emit,
        uf_dest=uf_dest,
        n_nf=dados.n_nf,
        dh_emi=dados.dh_emi,
        emitente=dados.emitente.razao_social,
        destinatario=dados.destinatario.nome,
        calculo=calculo,
        guias=guias,
        portal_url=portal_url,
    )
