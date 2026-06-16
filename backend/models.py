from __future__ import annotations
from pydantic import BaseModel, Field
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


# ── Input schemas ──────────────────────────────────────────────────────────────

class FormularioManual(BaseModel):
    """Manual form or JSON API input for DIFAL guide generation."""
    # Emitente
    cnpj_emit: str = Field(..., description="CNPJ do emitente (somente dígitos)")
    razao_social_emit: str
    ie_emit: Optional[str] = None
    uf_emit: str = Field(..., description="UF do emitente (ex: SP)")
    municipio_codigo_emit: Optional[str] = None
    municipio_nome_emit: Optional[str] = None
    cep_emit: Optional[str] = None
    logradouro_emit: Optional[str] = None
    numero_emit: Optional[str] = None
    bairro_emit: Optional[str] = None

    # Destinatário
    cnpj_dest: Optional[str] = Field(None, description="CNPJ do destinatário (somente dígitos)")
    cpf_dest: Optional[str] = Field(None, description="CPF do destinatário (somente dígitos)")
    nome_dest: str
    uf_dest: str = Field(..., description="UF do destinatário (ex: RJ)")
    municipio_codigo_dest: Optional[str] = None
    municipio_nome_dest: Optional[str] = None
    cep_dest: Optional[str] = None
    logradouro_dest: Optional[str] = None
    numero_dest: Optional[str] = None
    bairro_dest: Optional[str] = None

    # NF-e reference
    chave_nfe: Optional[str] = Field(None, description="Chave de acesso da NF-e (44 dígitos)")
    n_nf: Optional[str] = None
    serie: Optional[str] = None
    dh_emi: Optional[str] = Field(None, description="Data/hora emissão (ISO 8601 ou dd/mm/yyyy)")

    # Values for DIFAL calculation
    v_bc_difal: Decimal = Field(..., description="Base de cálculo do DIFAL")
    v_nf: Optional[Decimal] = Field(None, description="Valor total da NF")

    # Override calculated rates (optional — system uses state defaults if omitted)
    aliq_interna_dest: Optional[float] = Field(None, description="Alíquota interna UF destino (%)")
    aliq_interestadual: Optional[float] = Field(None, description="Alíquota interestadual (%)")
    aliq_fecp: Optional[float] = Field(None, description="Alíquota FECP (%)")
    produto_importado: bool = Field(False, description="Produto importado (alíq. inter. 4%)")

    # Payment
    data_pagamento: Optional[date] = None

    class Config:
        json_schema_extra = {
            "example": {
                "cnpj_emit": "12345678000195",
                "razao_social_emit": "Empresa Vendedora Ltda",
                "uf_emit": "SP",
                "municipio_codigo_emit": "3550308",
                "cnpj_dest": "98765432000100",
                "nome_dest": "Comprador Rio Ltda",
                "uf_dest": "RJ",
                "chave_nfe": "35240612345678000195550010000001231000012345",
                "n_nf": "123",
                "dh_emi": "2026-06-16T10:00:00-03:00",
                "v_bc_difal": "1000.00",
                "data_pagamento": "2026-06-30"
            }
        }


# ── Output schemas ─────────────────────────────────────────────────────────────

class GuiaGerada(BaseModel):
    tipo: str  # "GNRE" | "DARE-SP"
    uf: str
    receita_codigo: Optional[str] = None
    receita_descricao: str
    valor: Decimal
    data_vencimento: Optional[str] = None
    codigo_barras: Optional[str] = None
    linha_digitavel: Optional[str] = None
    pdf_base64: Optional[str] = None
    gnre_xml: Optional[str] = None
    status: str  # "gerada" | "pendente_webservice" | "erro"
    mensagem: Optional[str] = None


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
