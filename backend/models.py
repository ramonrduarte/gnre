from __future__ import annotations
from pydantic import BaseModel, Field, model_validator
from typing import Optional
from decimal import Decimal
from datetime import date


class EnderecoModel(BaseModel):
    uf: str
    municipio_codigo: Optional[str] = None
    municipio_nome: Optional[str] = None
    cep: Optional[str] = None
    logradouro: Optional[str] = None
    numero: Optional[str] = None
    bairro: Optional[str] = None
    complemento: Optional[str] = None


class EmitenteModel(BaseModel):
    cnpj: str
    razao_social: str
    ie: Optional[str] = None
    endereco: EnderecoModel


class DestinatarioModel(BaseModel):
    cnpj: Optional[str] = None
    cpf: Optional[str] = None
    nome: str
    ind_ie: str = "9"  # 1=contribuinte, 2=isento IE, 9=não contribuinte
    uf: str
    municipio_codigo: Optional[str] = None
    municipio_nome: Optional[str] = None
    cep: Optional[str] = None
    logradouro: Optional[str] = None
    numero: Optional[str] = None
    bairro: Optional[str] = None


class DadosDIFAL(BaseModel):
    """DIFAL values — either extracted from NF-e XML or calculated from form data."""
    v_bc_uf_dest: Decimal = Decimal("0")
    p_fcp_uf_dest: Decimal = Decimal("0")
    p_icms_uf_dest: Decimal = Decimal("0")
    p_icms_inter: Decimal = Decimal("0")
    v_fcp_uf_dest: Decimal = Decimal("0")
    v_icms_uf_dest: Decimal = Decimal("0")
    v_icms_uf_remet: Decimal = Decimal("0")


class NFeDados(BaseModel):
    """Complete NF-e data needed to generate DIFAL guides."""
    chave_nfe: Optional[str] = None
    n_nf: Optional[str] = None
    serie: Optional[str] = None
    dh_emi: Optional[str] = None
    uf_emit: str
    uf_dest: str
    ind_final: str = "1"  # 1=consumidor final
    emitente: EmitenteModel
    destinatario: DestinatarioModel
    difal: DadosDIFAL
    data_pagamento: Optional[date] = None
    # Sobrescritas opcionais para a GNRE: detalhamento_receita, produto, convenio, campos_extras
    opcoes_gnre: Optional[dict] = None
    # True = emite nova guia mesmo que a NF-e já tenha uma gerada e não vencida
    reemitir: bool = False


# ── Input schemas ──────────────────────────────────────────────────────────────

class FormularioManual(BaseModel):
    """
    Entrada JSON da API (POST /api/gerar/json) — uma NF-e por chamada.
    Documentação completa dos campos em docs/API.md.
    """
    # Emitente (quem vendeu e paga o DIFAL). Se o CNPJ estiver cadastrado em Empresas,
    # endereço e certificado vêm do cadastro e os campos de endereço abaixo podem ser omitidos.
    cnpj_emit: str = Field(..., description="CNPJ do emitente, só dígitos (obrigatório)")
    razao_social_emit: str = Field(..., description="Razão social do emitente (obrigatório)")
    ie_emit: Optional[str] = Field(None, description="IE do emitente (não é enviada à GNRE)")
    uf_emit: str = Field(..., description="UF do emitente, ex: RS (obrigatório)")
    municipio_codigo_emit: Optional[str] = Field(None, description="Código IBGE 7 dígitos do município do emitente")
    municipio_nome_emit: Optional[str] = None
    cep_emit: Optional[str] = None
    logradouro_emit: Optional[str] = None
    numero_emit: Optional[str] = None
    bairro_emit: Optional[str] = None

    # Destinatário (consumidor final na UF de destino)
    cnpj_dest: Optional[str] = Field(None, description="CNPJ do destinatário, só dígitos (CPF ou CNPJ obrigatório)")
    cpf_dest: Optional[str] = Field(None, description="CPF do destinatário, só dígitos (CPF ou CNPJ obrigatório)")
    nome_dest: str = Field(..., description="Nome / razão social do destinatário (obrigatório)")
    uf_dest: str = Field(..., description="UF de destino que recebe o DIFAL, ex: MG (obrigatório)")
    municipio_codigo_dest: Optional[str] = Field(None, description="Código IBGE 7 dígitos do município de destino (recomendado)")
    municipio_nome_dest: Optional[str] = None
    cep_dest: Optional[str] = None
    logradouro_dest: Optional[str] = None
    numero_dest: Optional[str] = None
    bairro_dest: Optional[str] = None

    # NF-e
    chave_nfe: Optional[str] = Field(None, description="Chave de acesso da NF-e, 44 dígitos (exigida pela maioria das UFs)")
    n_nf: Optional[str] = Field(None, description="Número da NF-e (obrigatório na prática)")
    serie: Optional[str] = Field(None, description="Série da NF-e")
    dh_emi: Optional[str] = Field(None, description="Data/hora de emissão (ISO 8601 ou dd/mm/aaaa) — define mês/ano de referência")

    # Valores — informe v_difal (recomendado: valor vICMSUFDest da NF-e) OU v_bc_difal para o sistema calcular
    v_difal: Optional[Decimal] = Field(None, description="Valor do DIFAL já calculado (vICMSUFDest da NF-e). Tem prioridade sobre o cálculo.")
    v_fcp: Optional[Decimal] = Field(None, description="Valor do FCP já calculado (vFCPUFDest da NF-e). Usado junto com v_difal.")
    v_bc_difal: Optional[Decimal] = Field(None, description="Base de cálculo do DIFAL — usada só quando v_difal não é informado")
    v_nf: Optional[Decimal] = Field(None, description="Valor total da NF (informativo)")

    # Alíquotas para o cálculo (só quando v_difal não é informado)
    aliq_interna_dest: Optional[float] = Field(None, description="Alíquota interna da UF destino (%). Padrão: tabela do sistema")
    aliq_interestadual: Optional[float] = Field(None, description="Alíquota interestadual (%). Padrão: 7 ou 12 conforme UFs, 4 se importado")
    aliq_fecp: Optional[float] = Field(
        None,
        description="Alíquota FCP (%). Padrão: 0 (sem FCP). Informe (ex: 2.0) quando o produto tiver FCP na UF destino.",
    )
    produto_importado: bool = Field(False, description="Produto importado (alíq. interestadual 4%)")

    # Pagamento
    data_pagamento: Optional[date] = Field(None, description="Data de vencimento/pagamento AAAA-MM-DD. Padrão: próximo dia útil")

    # Ajustes opcionais da GNRE — só quando a UF exigir algo que o sistema não preenche sozinho
    detalhamento_receita: Optional[str] = Field(None, description="Código do detalhamento da receita (ver GET /api/gnre/config/{uf}/{receita})")
    produto: Optional[str] = Field(None, description="Código do produto GNRE (ver GET /api/gnre/config/{uf}/{receita})")
    convenio: Optional[str] = Field(None, description="Texto do campo convênio (até 30 caracteres)")
    reemitir: bool = Field(
        False, description="Emitir nova guia mesmo se a NF-e já tiver guia gerada/aguardando e não vencida",
    )
    campos_extras: Optional[dict[str, str]] = Field(
        None, description="Valores de campos extras por código, ex: {\"117\": \"2026-10-20\"} — sobrescreve o preenchimento automático",
    )

    @model_validator(mode="after")
    def _validar(self):
        if self.v_difal is None and self.v_bc_difal is None:
            raise ValueError("Informe v_difal (valor do DIFAL) ou v_bc_difal (base para o sistema calcular).")
        if not (self.cpf_dest or self.cnpj_dest):
            raise ValueError("Informe cpf_dest ou cnpj_dest do destinatário.")
        return self

    model_config = {
        "json_schema_extra": {
            "example": {
                "cnpj_emit": "92613413000160",
                "razao_social_emit": "CASA DAS FECHADURAS ADROFECHA LTDA",
                "uf_emit": "RS",
                "cpf_dest": "12345678909",
                "nome_dest": "JOAO DA SILVA",
                "uf_dest": "MG",
                "municipio_codigo_dest": "3106200",
                "chave_nfe": "43261092613413000160550010000123451000123456",
                "n_nf": "12345",
                "serie": "1",
                "dh_emi": "2026-10-20T10:00:00-03:00",
                "v_difal": "60.00",
                "v_fcp": "20.00",
                "data_pagamento": "2026-10-23",
            }
        }
    }


# ── Output schemas ─────────────────────────────────────────────────────────────

class GuiaGerada(BaseModel):
    id: Optional[int] = None  # id no histórico (GET /api/guias/{id})
    tipo: str  # "GNRE" | "DARE-SP" | "DUA-ES"
    uf: str
    receita_codigo: Optional[str] = None
    receita_descricao: str
    valor: Decimal
    data_vencimento: Optional[str] = None
    codigo_barras: Optional[str] = None
    linha_digitavel: Optional[str] = None
    qrcode_pix: Optional[str] = None
    numero_guia: Optional[str] = None
    numero_recibo: Optional[str] = None  # recibo do lote GNRE — use em GET /api/gnre/resultado/{recibo}
    pdf_base64: Optional[str] = None
    gnre_xml: Optional[str] = None
    status: str  # "gerada" | "processando" | "pendente_webservice" | "erro"
    mensagem: Optional[str] = None
    criado_em: Optional[str] = None  # dd/mm/aaaa hh:mm


class DIFALCalculo(BaseModel):
    v_bc: Decimal
    p_aliq_interna: float
    p_aliq_inter: float
    p_fecp: float
    v_difal: Decimal
    v_fecp: Decimal
    v_total: Decimal


class ResultadoGuias(BaseModel):
    chave_nfe: Optional[str] = None
    uf_emit: str
    uf_dest: str
    n_nf: Optional[str] = None
    dh_emi: Optional[str] = None
    emitente: str
    destinatario: str
    calculo: DIFALCalculo
    guias: list[GuiaGerada]
    portal_url: Optional[str] = None
    # True = a NF-e já tinha guia gerada/aguardando dentro do vencimento; nada foi emitido de novo
    ja_emitida: bool = False
