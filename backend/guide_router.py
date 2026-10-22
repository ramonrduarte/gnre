"""
Routes NF-e data to the correct guide generation service:
  - SP  → DARE-ICMS via portal fazenda.sp.gov.br/DareICMS/GnreLote (Playwright)
  - ES  → DUA-e via WebService app.sefaz.es.gov.br/WsDua/DuaService.asmx
  - others → national GNRE WebService gnre.pe.gov.br

When a CNPJ emit is registered in the DB:
  - Emitente address is auto-filled from the empresa record
  - The empresa's A1 certificate is used for all WebService calls
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from models import NFeDados, ResultadoGuias, GuiaGerada
from uf_config import UF_GNRE_CODES, UF_DIFAL_CONFIG, uf_usa_gnre
from gnre_service import gerar_gnre
from gnre_config import obter_config
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


_RECEITA_DIFAL = "100102"   # ICMS Consumidor Final Não Contribuinte Outra UF por Operação
_RECEITA_FCP = "100129"     # ICMS Fundo Estadual de Combate à Pobreza por Operação


def _gerar_gnres(
    dados: NFeDados,
    uf_dest: str,
    difal_val: Decimal,
    fecp_val: Decimal,
    data_pag: date,
    cert_paths: tuple | None,
) -> list[GuiaGerada]:
    """
    Gera as guias GNRE da UF usando as regras do GnreConfigUF:
      - exigeValorFecp=S na receita DIFAL → FCP vai no mesmo guia (valor tipo 12)
      - senão, FCP em guia separada na receita 100129, se a UF tiver essa receita
    Sem acesso ao GnreConfigUF (sem certificado), usa a tabela estática de uf_config.
    """
    from gnre_service import _CERT, _KEY, _cert_configured

    cert = cert_paths or ((_CERT, _KEY) if _cert_configured() else None)
    cfg_difal = obter_config(uf_dest, _RECEITA_DIFAL, cert)
    guias: list[GuiaGerada] = []

    if cfg_difal is not None and not cfg_difal["existe"]:
        return [GuiaGerada(
            tipo="GNRE", uf=uf_dest, receita_codigo=_RECEITA_DIFAL,
            receita_descricao=f"ICMS DIFAL EC 87/2015 – {uf_dest}", valor=difal_val,
            status="erro", mensagem=f"UF {uf_dest} não emite a receita {_RECEITA_DIFAL} pelo GNRE: {cfg_difal['descricao']}",
        )]

    if cfg_difal is not None:
        fecp_mesmo_guia = cfg_difal["exige_valor_fecp"]
    else:
        fecp_mesmo_guia = (UF_DIFAL_CONFIG.get(uf_dest) or {}).get("fecp_mesmo_guia", False)

    # Cada guia espera ≥30s pelo resultado no portal: DIFAL e FCP rodam em paralelo
    jobs: list = []
    if difal_val > Decimal("0"):
        jobs.append(partial(
            gerar_gnre,
            dados,
            receita=_RECEITA_DIFAL,
            descricao=f"ICMS DIFAL EC 87/2015 – {uf_dest}",
            valor=difal_val,
            data_pag=data_pag,
            cert_paths=cert_paths,
            fecp_valor=fecp_val if fecp_mesmo_guia else None,
            cfg=cfg_difal,
        ))

    if fecp_val > Decimal("0") and not fecp_mesmo_guia:
        if cfg_difal is not None:
            cfg_fcp = obter_config(uf_dest, _RECEITA_FCP, cert)
            tem_fcp = cfg_fcp is None or cfg_fcp["existe"]
        else:
            cfg_fcp = None
            tem_fcp = bool(UF_GNRE_CODES.get(uf_dest, {}).get("fecp"))
        if tem_fcp:
            jobs.append(partial(
                gerar_gnre,
                dados,
                receita=_RECEITA_FCP,
                descricao=f"ICMS FCP Consumidor Final – {uf_dest}",
                valor=fecp_val,
                data_pag=data_pag,
                cert_paths=cert_paths,
                cfg=cfg_fcp,
            ))
        else:
            guias.append(GuiaGerada(
                tipo="GNRE", uf=uf_dest, receita_codigo=_RECEITA_FCP,
                receita_descricao=f"ICMS FCP Consumidor Final – {uf_dest}", valor=fecp_val,
                status="erro",
                mensagem=f"UF {uf_dest} não possui a receita FCP {_RECEITA_FCP} no GNRE nem aceita FCP na guia do DIFAL. "
                         "Confirme se há FCP devido para essa UF.",
            ))

    if jobs:
        with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
            guias = [f.result() for f in [ex.submit(j) for j in jobs]] + guias
    return guias


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
        guias = await asyncio.to_thread(_gerar_gnres, dados, uf_dest, difal_val, fecp_val, data_pag, cert_paths)
        portal_url = _PORTAL_GNRE
    elif uf_dest == "ES":
        # ES usa DUA-e via WebService próprio (não GNRE nacional)
        if difal_val > Decimal("0"):
            guia_es = await asyncio.to_thread(
                gerar_dua_es,
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
