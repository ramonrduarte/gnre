"""
DIFAL calculation logic — converts form data or NF-e data into NFeDados
with calculated DIFAL values, using state defaults when rates are not provided.
"""
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta

from models import (
    FormularioManual, NFeDados, EmitenteModel, DestinatarioModel,
    EnderecoModel, DadosDIFAL, DIFALCalculo,
)
from uf_config import UF_ALIQ_INTERNA, UF_FECP, get_aliq_interestadual


_CENT = Decimal("0.01")


def _round2(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def calcular_difal(
    v_bc: Decimal,
    aliq_interna: float,
    aliq_inter: float,
    aliq_fecp: float,
) -> DIFALCalculo:
    """
    DIFAL = BC × (aliq_interna − aliq_inter) / 100
    Since 2019, 100% of DIFAL goes to the destination state (EC 87/2015, transition ended).
    FCP = BC × aliq_fecp / 100
    """
    v_difal = _round2(v_bc * Decimal(str(aliq_interna - aliq_inter)) / Decimal("100"))
    v_fecp = _round2(v_bc * Decimal(str(aliq_fecp)) / Decimal("100"))
    return DIFALCalculo(
        v_bc=v_bc,
        p_aliq_interna=aliq_interna,
        p_aliq_inter=aliq_inter,
        p_fecp=aliq_fecp,
        v_difal=v_difal,
        v_fecp=v_fecp,
        v_total=v_difal + v_fecp,
    )


def _default_payment_date() -> date:
    """Default payment date = next business day from today."""
    d = date.today() + timedelta(days=1)
    while d.weekday() >= 5:  # skip Saturday/Sunday
        d += timedelta(days=1)
    return d


def calcular_difal_manual(form: FormularioManual) -> NFeDados:
    """
    Build NFeDados from manual form/JSON input, computing DIFAL values
    from the provided base (v_bc_difal) and state-default or override rates.
    """
    uf_dest = form.uf_dest.upper()
    uf_emit = form.uf_emit.upper()

    aliq_interna = form.aliq_interna_dest if form.aliq_interna_dest is not None else UF_ALIQ_INTERNA.get(uf_dest, 18.0)
    aliq_fecp = form.aliq_fecp if form.aliq_fecp is not None else UF_FECP.get(uf_dest, 0.0)
    aliq_inter = (
        form.aliq_interestadual
        if form.aliq_interestadual is not None
        else get_aliq_interestadual(uf_emit, uf_dest, form.produto_importado)
    )

    calc = calcular_difal(form.v_bc_difal, aliq_interna, aliq_inter, aliq_fecp)

    # Parse data_pagamento
    data_pag = form.data_pagamento or _default_payment_date()

    emitente = EmitenteModel(
        cnpj=form.cnpj_emit,
        razao_social=form.razao_social_emit,
        ie=form.ie_emit,
        endereco=EnderecoModel(
            uf=uf_emit,
            municipio_codigo=form.municipio_codigo_emit,
            municipio_nome=form.municipio_nome_emit,
            cep=form.cep_emit,
            logradouro=form.logradouro_emit,
            numero=form.numero_emit,
            bairro=form.bairro_emit,
        ),
    )

    destinatario = DestinatarioModel(
        cnpj=form.cnpj_dest,
        cpf=form.cpf_dest,
        nome=form.nome_dest,
        uf=uf_dest,
        municipio_codigo=form.municipio_codigo_dest,
        municipio_nome=form.municipio_nome_dest,
        cep=form.cep_dest,
        logradouro=form.logradouro_dest,
        numero=form.numero_dest,
        bairro=form.bairro_dest,
    )

    difal = DadosDIFAL(
        v_bc_uf_dest=form.v_bc_difal,
        p_fcp_uf_dest=Decimal(str(aliq_fecp)),
        p_icms_uf_dest=Decimal(str(aliq_interna)),
        p_icms_inter=Decimal(str(aliq_inter)),
        v_fcp_uf_dest=calc.v_fecp,
        v_icms_uf_dest=calc.v_difal,
        v_icms_uf_remet=Decimal("0"),
    )

    return NFeDados(
        chave_nfe=form.chave_nfe,
        n_nf=form.n_nf,
        serie=form.serie,
        dh_emi=form.dh_emi,
        uf_emit=uf_emit,
        uf_dest=uf_dest,
        ind_final="1",
        emitente=emitente,
        destinatario=destinatario,
        difal=difal,
        data_pagamento=data_pag,
    )


def build_calculo_from_nfe(dados: NFeDados) -> DIFALCalculo:
    """Build a DIFALCalculo summary from already-computed NFeDados."""
    return DIFALCalculo(
        v_bc=dados.difal.v_bc_uf_dest,
        p_aliq_interna=float(dados.difal.p_icms_uf_dest),
        p_aliq_inter=float(dados.difal.p_icms_inter),
        p_fecp=float(dados.difal.p_fcp_uf_dest),
        v_difal=dados.difal.v_icms_uf_dest,
        v_fecp=dados.difal.v_fcp_uf_dest,
        v_total=dados.difal.v_icms_uf_dest + dados.difal.v_fcp_uf_dest,
    )
