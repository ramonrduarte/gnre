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
# 100122 = ICMS Diferencial Alíquota Consumidor Final (10012-2)
# 100131 = FCP na operação consumidor final (10013-1)
# Verify with each state SEFAZ — some states use state-specific codes
UF_GNRE_CODES: dict[str, dict] = {
    "AC": {"difal": "100122", "fecp": None},
    "AL": {"difal": "100122", "fecp": "100131"},
    "AM": {"difal": "100122", "fecp": "100131"},
    "AP": {"difal": "100122", "fecp": None},
    "BA": {"difal": "100122", "fecp": "100131"},
    "CE": {"difal": "100122", "fecp": "100131"},
    "DF": {"difal": "100122", "fecp": "100131"},
    "ES": {"difal": "100122", "fecp": None},
    "GO": {"difal": "100122", "fecp": "100131"},
    "MA": {"difal": "100122", "fecp": "100131"},
    "MG": {"difal": "100122", "fecp": "100131"},
    "MS": {"difal": "100122", "fecp": None},
    "MT": {"difal": "100122", "fecp": "100131"},
    "PA": {"difal": "100122", "fecp": "100131"},
    "PB": {"difal": "100122", "fecp": "100131"},
    "PE": {"difal": "100122", "fecp": "100131"},
    "PI": {"difal": "100122", "fecp": "100131"},
    "PR": {"difal": "100122", "fecp": None},
    "RJ": {"difal": "100122", "fecp": "100131"},
    "RN": {"difal": "100122", "fecp": "100131"},
    "RO": {"difal": "100122", "fecp": "100131"},
    "RR": {"difal": "100122", "fecp": None},
    "RS": {"difal": "100122", "fecp": None},
    "SC": {"difal": "100122", "fecp": None},
    "SE": {"difal": "100122", "fecp": "100131"},
    # SP does NOT use national GNRE — uses DARE-ICMS (SP portal)
    "SP": {"difal": None, "fecp": None},
    "TO": {"difal": "100122", "fecp": "100131"},
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
    """Returns True if state uses national GNRE portal (not a state-specific system)."""
    return uf != "SP"


def get_uf_config(uf: str) -> dict:
    """Returns full config for a given state."""
    return {
        "uf": uf,
        "aliq_interna": UF_ALIQ_INTERNA.get(uf, 18.0),
        "fecp": UF_FECP.get(uf, 0.0),
        "gnre_codes": UF_GNRE_CODES.get(uf, {"difal": "100122", "fecp": None}),
        "usa_gnre": uf_usa_gnre(uf),
    }
