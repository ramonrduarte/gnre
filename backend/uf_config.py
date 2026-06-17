"""
State-level configuration for DIFAL calculation and GNRE guide generation.
GNRE codes should be verified with each state's SEFAZ for production use.
"""

# Internal ICMS rates per state (modal/general rate)
# Note: may vary by product NCM — use as default, allow override via form
UF_ALIQ_INTERNA: dict[str, float] = {
    "AC": 17.0,
    "AL": 18.0,
    "AM": 18.0,
    "AP": 18.0,
    "BA": 20.5,
    "CE": 18.0,
    "DF": 18.0,
    "ES": 17.0,
    "GO": 17.0,
    "MA": 18.0,
    "MG": 18.0,
    "MS": 17.0,
    "MT": 17.0,
    "PA": 17.0,
    "PB": 18.0,
    "PE": 18.0,
    "PI": 18.0,
    "PR": 18.5,
    "RJ": 20.0,
    "RN": 18.0,
    "RO": 17.5,
    "RR": 17.0,
    "RS": 17.0,
    "SC": 17.0,
    "SE": 18.0,
    "SP": 18.0,
    "TO": 18.0,
}

# FECP (Fundo de Combate à Pobreza) rates per state
UF_FECP: dict[str, float] = {
    "AC": 0.0,
    "AL": 2.0,
    "AM": 2.0,
    "AP": 0.0,
    "BA": 2.0,
    "CE": 2.0,
    "DF": 2.0,
    "ES": 0.0,
    "GO": 2.0,
    "MA": 2.0,
    "MG": 2.0,
    "MS": 0.0,
    "MT": 2.0,
    "PA": 2.0,
    "PB": 2.0,
    "PE": 2.0,
    "PI": 2.0,
    "PR": 0.0,
    "RJ": 2.0,
    "RN": 2.0,
    "RO": 2.0,
    "RR": 0.0,
    "RS": 0.0,
    "SC": 0.0,
    "SE": 2.0,
    "SP": 0.0,
    "TO": 2.0,
}

# GNRE receita codes per state for DIFAL EC 87/2015
# Confirmado via GnreConfigUF em 17/06/2026:
# 100102 = ICMS Consumidor Final Não Contribuinte Outra UF por Operação (DIFAL)
# 100129 = ICMS Fundo Estadual de Combate à Pobreza por Operação (FCP — guia separada)
# RJ/RO/RS/SE: exigeValorFecp=S → FCP vai como valor tipo="12" no MESMO guia DIFAL (sem 100129)
UF_GNRE_CODES: dict[str, dict] = {
    "AC": {"difal": "100102", "fecp": "100129"},
    "AL": {"difal": "100102", "fecp": "100129"},
    "AM": {"difal": "100102", "fecp": "100129"},
    "AP": {"difal": "100102", "fecp": "100129"},
    "BA": {"difal": "100102", "fecp": "100129"},
    "CE": {"difal": "100102", "fecp": "100129"},
    "DF": {"difal": "100102", "fecp": "100129"},
    "ES": {"difal": "100102", "fecp": None},
    "GO": {"difal": "100102", "fecp": "100129"},
    "MA": {"difal": "100102", "fecp": "100129"},
    "MG": {"difal": "100102", "fecp": "100129"},
    "MS": {"difal": "100102", "fecp": None},
    "MT": {"difal": "100102", "fecp": "100129"},
    "PA": {"difal": "100102", "fecp": "100129"},
    "PB": {"difal": "100102", "fecp": "100129"},
    "PE": {"difal": "100102", "fecp": "100129"},
    "PI": {"difal": "100102", "fecp": "100129"},
    "PR": {"difal": "100102", "fecp": None},
    "RJ": {"difal": "100102", "fecp": None},   # FCP incluso via fecp_mesmo_guia
    "RN": {"difal": "100102", "fecp": "100129"},
    "RO": {"difal": "100102", "fecp": None},    # FCP incluso via fecp_mesmo_guia
    "RR": {"difal": "100102", "fecp": None},
    "RS": {"difal": "100102", "fecp": None},    # FCP incluso via fecp_mesmo_guia
    "SC": {"difal": "100102", "fecp": None},
    "SE": {"difal": "100102", "fecp": None},    # FCP incluso via fecp_mesmo_guia
    # SP does NOT use national GNRE — uses DARE-ICMS (SP portal)
    "SP": {"difal": None, "fecp": None},
    "TO": {"difal": "100102", "fecp": "100129"},
}

# Configuração detalhada por UF para montagem do XML GNRE (receita 100102)
# Obtida via GnreConfigUF em 17/06/2026.
#
# campos_extras: lista de {codigo, valor, obrig}
#   valor = "chave"    → dados.chave_nfe (44 dígitos)
#           "data_emi" → data de emissão da NF-e (AAAA-MM-DD)
#           "obs"      → texto de observação: "NF-e nº {n_nf}" para identificação
#
# fecp_mesmo_guia: True = FCP vai como <valor tipo="12"> no mesmo guia DIFAL
# tipo_doc: código do tipo documentoOrigem ("10"=NF, "22"=chave NFe, "24"=chave DFe, None=sem)
# doc_valor: "n_nf" (número NF) ou "chave" (44 dígitos)
#
UF_DIFAL_CONFIG: dict[str, dict | None] = {
    "AC": {
        "tipo_doc": "10",        # NOTA FISCAL → valor = número NF
        "doc_valor": "n_nf",
        "exige_dest":    True,
        "exige_periodo": True,
        "fecp_mesmo_guia": False,
        "convenio": False,
        "campos_extras": [
            {"codigo": "120", "valor": "chave", "obrig": True},   # Chave NF-e/CT-e
            {"codigo": "68",  "valor": "obs",   "obrig": False},  # OBS
        ],
    },
    "AL": {
        "tipo_doc": "22",        # CHAVE DA NFe → valor = chave 44 dígitos
        "doc_valor": "chave",
        "exige_dest":    True,
        "exige_periodo": True,
        "fecp_mesmo_guia": False,
        "convenio": True,        # obrigatório — preencher com NF-e info
        "campos_extras": [
            {"codigo": "65", "valor": "obs", "obrig": False},     # Info complementar
        ],
    },
    "AM": {
        "tipo_doc": "22",
        "doc_valor": "chave",
        "exige_dest":    True,
        "exige_periodo": True,
        "fecp_mesmo_guia": False,
        "convenio": True,
        "campos_extras": [
            {"codigo": "96", "valor": "chave", "obrig": True},    # Chave NF-e/CT-e
        ],
    },
    "AP": {
        "tipo_doc": "10",
        "doc_valor": "n_nf",
        "exige_dest":    True,
        "exige_periodo": True,
        "fecp_mesmo_guia": False,
        "convenio": True,
        "campos_extras": [
            {"codigo": "47", "valor": "chave", "obrig": True},    # Chave NF-e
        ],
    },
    "BA": {
        "tipo_doc": "22",
        "doc_valor": "chave",
        "exige_dest":    False,
        "exige_periodo": True,
        "fecp_mesmo_guia": False,
        "convenio": False,
        "campos_extras": [],
    },
    "CE": {
        "tipo_doc": "10",
        "doc_valor": "n_nf",
        "exige_dest":    True,
        "exige_periodo": False,
        "fecp_mesmo_guia": False,
        "convenio": True,
        "campos_extras": [
            {"codigo": "50", "valor": "data_emi", "obrig": False},  # Data de saída
        ],
    },
    "DF": {
        "tipo_doc": "10",
        "doc_valor": "n_nf",
        "exige_dest":    False,
        "exige_periodo": True,
        "fecp_mesmo_guia": False,
        "convenio": False,
        "campos_extras": [],
    },
    "ES": None,  # UF não habilitada no GNRE (cod 999)
    "GO": {
        "tipo_doc": "10",
        "doc_valor": "n_nf",
        "exige_dest":    True,
        "exige_periodo": True,
        "fecp_mesmo_guia": False,
        "convenio": True,
        "campos_extras": [
            {"codigo": "102", "valor": "chave", "obrig": True},   # Chave NF-e/CT-e
            {"codigo": "10",  "valor": "obs",   "obrig": False},  # Info complementar 1
            {"codigo": "60",  "valor": "obs",   "obrig": False},  # Info complementar 2
        ],
    },
    "MA": {
        "tipo_doc": "10",
        "doc_valor": "n_nf",
        "exige_dest":    True,
        "exige_periodo": True,
        "fecp_mesmo_guia": False,
        "convenio": True,
        "campos_extras": [
            {"codigo": "94", "valor": "chave", "obrig": True},    # Chave NF-e/CT-e
        ],
    },
    "MG": {
        "tipo_doc": "10",
        "doc_valor": "n_nf",
        "exige_dest":    False,
        "exige_periodo": False,
        "fecp_mesmo_guia": False,
        "convenio": True,
        "campos_extras": [],
    },
    "MS": {
        "tipo_doc": None,          # Sem documentoOrigem
        "doc_valor": None,
        "exige_dest":    True,
        "exige_periodo": False,
        "fecp_mesmo_guia": False,
        "convenio": True,
        "campos_extras": [
            {"codigo": "88", "valor": "chave", "obrig": True},    # Chave NF-e/CT-e
        ],
    },
    "MT": {
        "tipo_doc": "22",
        "doc_valor": "chave",
        "exige_dest":    False,
        "exige_periodo": True,
        "fecp_mesmo_guia": False,
        "convenio": True,
        "campos_extras": [],
    },
    "PA": {
        "tipo_doc": "10",
        "doc_valor": "n_nf",
        "exige_dest":    True,
        "exige_periodo": True,
        "fecp_mesmo_guia": False,
        "convenio": True,
        "campos_extras": [
            {"codigo": "101", "valor": "chave", "obrig": False},  # Chave NF-e
            {"codigo": "100", "valor": "obs",   "obrig": False},  # Chave CT-e (opcional)
            {"codigo": "66",  "valor": "obs",   "obrig": False},  # Info complementar
        ],
    },
    "PB": {
        "tipo_doc": None,          # Sem documentoOrigem
        "doc_valor": None,
        "exige_dest":    False,
        "exige_periodo": True,
        "fecp_mesmo_guia": False,
        "convenio": True,
        "campos_extras": [
            {"codigo": "99", "valor": "chave", "obrig": True},    # Chave NF-e/CT-e
        ],
    },
    "PE": {
        "tipo_doc": "24",          # CHAVE DO DFe (NF-e/CT-e)
        "doc_valor": "chave",
        "exige_dest":    True,
        "exige_periodo": False,
        "fecp_mesmo_guia": False,
        "convenio": False,
        "campos_extras": [],
    },
    "PI": {
        "tipo_doc": "10",
        "doc_valor": "n_nf",
        "exige_dest":    True,
        "exige_periodo": False,
        "fecp_mesmo_guia": False,
        "convenio": True,
        "campos_extras": [],
    },
    "PR": {
        "tipo_doc": "10",
        "doc_valor": "n_nf",
        "exige_dest":    True,
        "exige_periodo": False,
        "fecp_mesmo_guia": False,
        "convenio": True,
        "campos_extras": [
            {"codigo": "107", "valor": "chave", "obrig": True},   # Chave NF-e/CT-e
            {"codigo": "56",  "valor": "obs",   "obrig": False},  # Info complementar 1
            {"codigo": "57",  "valor": "obs",   "obrig": False},  # Info complementar 2
        ],
    },
    "RJ": {
        "tipo_doc": "24",
        "doc_valor": "chave",
        "exige_dest":    True,
        "exige_periodo": False,
        "fecp_mesmo_guia": True,   # FCP como valor tipo="12" no mesmo guia
        "convenio": False,
        "campos_extras": [
            {"codigo": "117", "valor": "data_emi", "obrig": True},   # Data de Emissão
            {"codigo": "118", "valor": "obs",      "obrig": False},  # Info complementar
        ],
    },
    "RN": {
        "tipo_doc": "22",
        "doc_valor": "chave",
        "exige_dest":    True,
        "exige_periodo": True,
        "fecp_mesmo_guia": False,
        "convenio": "O",           # Opcional
        "campos_extras": [],
    },
    "RO": {
        "tipo_doc": "10",
        "doc_valor": "n_nf",
        "exige_dest":    False,
        "exige_periodo": True,
        "fecp_mesmo_guia": True,   # FCP como valor tipo="12" no mesmo guia
        "convenio": True,
        "campos_extras": [
            {"codigo": "83", "valor": "chave", "obrig": True},    # Chave NF-e
        ],
    },
    "RR": {
        "tipo_doc": "10",
        "doc_valor": "n_nf",
        "exige_dest":    False,
        "exige_periodo": True,
        "fecp_mesmo_guia": False,
        "convenio": True,
        "campos_extras": [
            {"codigo": "36", "valor": "chave", "obrig": True},    # Chave NF-e
            {"codigo": "71", "valor": "obs",   "obrig": False},   # Info complementar
        ],
    },
    "RS": {
        "tipo_doc": "24",
        "doc_valor": "chave",
        "exige_dest":    True,
        "exige_periodo": False,
        "fecp_mesmo_guia": True,   # FCP como valor tipo="12" no mesmo guia
        "convenio": True,
        "campos_extras": [
            {"codigo": "74", "valor": "chave", "obrig": True},    # Chave NF-e/CT-e
            {"codigo": "62", "valor": "obs",   "obrig": False},   # Info complementar
        ],
    },
    "SC": {
        "tipo_doc": "24",
        "doc_valor": "chave",
        "exige_dest":    False,
        "exige_periodo": False,
        "fecp_mesmo_guia": False,
        "convenio": False,
        "campos_extras": [
            {"codigo": "84", "valor": "chave", "obrig": True},    # Chave NF-e
        ],
    },
    "SE": {
        "tipo_doc": "10",
        "doc_valor": "n_nf",
        "exige_dest":    True,
        "exige_periodo": True,
        "fecp_mesmo_guia": True,   # FCP como valor tipo="12" no mesmo guia
        "convenio": False,
        "campos_extras": [
            {"codigo": "77", "valor": "chave", "obrig": True},    # Chave NF-e/CT-e
            {"codigo": "73", "valor": "obs",   "obrig": False},   # Info complementar
        ],
    },
    # SP usa DARE-ICMS via portal fazenda.sp.gov.br/DareICMS/GnreLote
    # O portal aceita XML no mesmo layout GNRE e converte para DARE-SP automaticamente.
    # GNRE 100102 (10010-2) → DARE-SP 101-6
    "SP": {
        "tipo_doc": "24",      # CHAVE DO DFe
        "doc_valor": "chave",
        "exige_dest":    False,
        "exige_periodo": False,
        "fecp_mesmo_guia": False,
        "convenio": False,
        "campos_extras": [],
    },
    "TO": {
        "tipo_doc": "10",
        "doc_valor": "n_nf",
        "exige_dest":    True,
        "exige_periodo": True,
        "fecp_mesmo_guia": False,
        "convenio": True,
        "campos_extras": [
            {"codigo": "80", "valor": "chave", "obrig": True},    # Chave NF-e
        ],
    },
}

# States from Sul/Sudeste (for inter-state rate calculation)
_UF_SUDESTE_SUL = {"MG", "SP", "RJ", "ES", "PR", "SC", "RS"}


def get_aliq_interestadual(uf_emit: str, uf_dest: str, produto_importado: bool = False) -> float:
    """
    Returns the applicable inter-state ICMS rate.
    Rule: Sul/Sudeste → North/Northeast/CO: 7%, all others: 12%.
    Imported goods (RIPI Conv. 123/2012): 4%.
    """
    if produto_importado:
        return 4.0
    if uf_emit in _UF_SUDESTE_SUL and uf_dest not in _UF_SUDESTE_SUL:
        return 7.0
    return 12.0


def uf_usa_gnre(uf: str) -> bool:
    """Returns True if state uses national GNRE portal (not a state-specific system).
    SP → DARE-ICMS (fazenda.sp.gov.br)
    ES → DUA-e (app.sefaz.es.gov.br/WsDua)
    """
    return uf not in ("SP", "ES")


def get_uf_config(uf: str) -> dict:
    """Returns full config for a given state."""
    return {
        "uf": uf,
        "aliq_interna": UF_ALIQ_INTERNA.get(uf, 18.0),
        "fecp": UF_FECP.get(uf, 0.0),
        "gnre_codes": UF_GNRE_CODES.get(uf, {"difal": "100122", "fecp": None}),
        "usa_gnre": uf_usa_gnre(uf),
    }
