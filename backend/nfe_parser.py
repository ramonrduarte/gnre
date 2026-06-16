"""
NF-e XML parser — handles nfeProc and NFe envelope formats (NF-e 4.0).
Extracts DIFAL-relevant fields from ICMSUFDest and totals.
"""
from lxml import etree
from decimal import Decimal
from models import NFeDados, EmitenteModel, DestinatarioModel, EnderecoModel, DadosDIFAL

_NS = "http://www.portalfiscal.inf.br/nfe"
_NSM = {"n": _NS}


def _find(el: etree._Element, xpath: str) -> etree._Element | None:
    return el.find(xpath, _NSM)


def _text(el: etree._Element, xpath: str, default: str = "") -> str:
    node = _find(el, xpath)
    return (node.text or "").strip() if node is not None else default


def _dec(el: etree._Element, xpath: str) -> Decimal:
    val = _text(el, xpath, "0")
    try:
        return Decimal(val)
    except Exception:
        return Decimal("0")


def parse_nfe_xml(content: bytes) -> NFeDados:
    """
    Parse NF-e XML (nfeProc or raw NFe) and return structured DIFAL data.
    Raises ValueError with a descriptive message if the XML is invalid or not a NF-e.
    """
    try:
        root = etree.fromstring(content)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"XML inválido: {exc}") from exc

    # Normalise: accept nfeProc wrapper or raw NFe
    tag = root.tag.replace(f"{{{_NS}}}", "")
    if tag == "nfeProc":
        nfe = _find(root, "n:NFe")
        if nfe is None:
            raise ValueError("nfeProc sem elemento NFe")
    elif tag == "NFe":
        nfe = root
    else:
        raise ValueError(f"Elemento raiz inesperado: {tag}. Envie um XML de NF-e.")

    inf = _find(nfe, "n:infNFe")
    if inf is None:
        raise ValueError("infNFe não encontrado")

    # ── Identificação ──────────────────────────────────────────────────────────
    ide = _find(inf, "n:ide")
    chave = inf.get("Id", "").replace("NFe", "")
    n_nf = _text(ide, "n:nNF")
    serie = _text(ide, "n:serie")
    dh_emi = _text(ide, "n:dhEmi")
    ind_final = _text(ide, "n:indFinal", "0")

    # ── Emitente ───────────────────────────────────────────────────────────────
    emit_el = _find(inf, "n:emit")
    ender_emit = _find(emit_el, "n:enderEmit")
    emitente = EmitenteModel(
        cnpj=_text(emit_el, "n:CNPJ"),
        razao_social=_text(emit_el, "n:xNome") or _text(emit_el, "n:xFant"),
        ie=_text(emit_el, "n:IE") or None,
        endereco=EnderecoModel(
            uf=_text(ender_emit, "n:UF"),
            municipio_codigo=_text(ender_emit, "n:cMun"),
            municipio_nome=_text(ender_emit, "n:xMun"),
            cep=_text(ender_emit, "n:CEP"),
            logradouro=_text(ender_emit, "n:xLgr"),
            numero=_text(ender_emit, "n:nro"),
            bairro=_text(ender_emit, "n:xBairro"),
            complemento=_text(ender_emit, "n:xCpl") or None,
        ),
    )

    # ── Destinatário ───────────────────────────────────────────────────────────
    dest_el = _find(inf, "n:dest")
    ender_dest = _find(dest_el, "n:enderDest")
    destinatario = DestinatarioModel(
        cnpj=_text(dest_el, "n:CNPJ") or None,
        cpf=_text(dest_el, "n:CPF") or None,
        nome=_text(dest_el, "n:xNome"),
        ind_ie=_text(dest_el, "n:indIEDest", "9"),
        uf=_text(ender_dest, "n:UF"),
        municipio_codigo=_text(ender_dest, "n:cMun") or None,
        municipio_nome=_text(ender_dest, "n:xMun") or None,
        cep=_text(ender_dest, "n:CEP") or None,
        logradouro=_text(ender_dest, "n:xLgr") or None,
        numero=_text(ender_dest, "n:nro") or None,
        bairro=_text(ender_dest, "n:xBairro") or None,
    )

    uf_emit = emitente.endereco.uf
    uf_dest = destinatario.uf

    # ── DIFAL values — prefer aggregate totals (ICMSTot) ──────────────────────
    # NF-e 4.0 puts DIFAL totals in <total><ICMSTot>
    total_el = _find(inf, "n:total")
    icms_tot = _find(total_el, "n:ICMSTot") if total_el is not None else None

    v_icms_uf_dest_tot = _dec(icms_tot, "n:vICMSUFDest") if icms_tot is not None else Decimal("0")
    v_icms_uf_remet_tot = _dec(icms_tot, "n:vICMSUFRemet") if icms_tot is not None else Decimal("0")
    v_fcp_uf_dest_tot = _dec(icms_tot, "n:vFCPUFDest") if icms_tot is not None else Decimal("0")

    # For rate/BC details, read first item with ICMSUFDest (all items share same rates)
    difal_item = inf.find(f".//{{{_NS}}}ICMSUFDest")
    if difal_item is not None:
        v_bc = _dec(difal_item, "n:vBCUFDest")
        p_fcp = _dec(difal_item, "n:pFCPUFDest")
        p_icms_dest = _dec(difal_item, "n:pICMSUFDest")
        p_icms_inter = _dec(difal_item, "n:pICMSInter")
        # Use totals from ICMSTot when available (more reliable)
        v_fcp = v_fcp_uf_dest_tot if v_fcp_uf_dest_tot else _dec(difal_item, "n:vFCPUFDest")
        v_difal = v_icms_uf_dest_tot if v_icms_uf_dest_tot else _dec(difal_item, "n:vICMSUFDest")
        v_remet = v_icms_uf_remet_tot if v_icms_uf_remet_tot else _dec(difal_item, "n:vICMSUFRemet")
    else:
        # DIFAL fields not present in XML — will need manual calculation
        v_bc = Decimal("0")
        p_fcp = Decimal("0")
        p_icms_dest = Decimal("0")
        p_icms_inter = Decimal("0")
        v_fcp = v_fcp_uf_dest_tot
        v_difal = v_icms_uf_dest_tot
        v_remet = v_icms_uf_remet_tot

    difal = DadosDIFAL(
        v_bc_uf_dest=v_bc,
        p_fcp_uf_dest=p_fcp,
        p_icms_uf_dest=p_icms_dest,
        p_icms_inter=p_icms_inter,
        v_fcp_uf_dest=v_fcp,
        v_icms_uf_dest=v_difal,
        v_icms_uf_remet=v_remet,
    )

    return NFeDados(
        chave_nfe=chave or None,
        n_nf=n_nf or None,
        serie=serie or None,
        dh_emi=dh_emi or None,
        uf_emit=uf_emit,
        uf_dest=uf_dest,
        ind_final=ind_final,
        emitente=emitente,
        destinatario=destinatario,
        difal=difal,
    )
