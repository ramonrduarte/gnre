"""
GNRE guide generation — WebService assíncrono (lote) conforme Manual v2.14.

Fluxo (seção 3.3.2 do manual):
  1. Montar XML TLote_GNRE versao="2.00"
  2. Enviar ao GnreLoteRecepcao via SOAP 1.2 → recebe número do recibo (14 dígitos)
  3. Aguardar ≥30s (requisito do manual)
  4. Consultar GnreResultadoLote com o recibo → guia com código de barras

URLs (produção / homologação configuráveis por env var):
  Prod  lote:    https://www.gnre.pe.gov.br/gnreWS/services/GnreLoteRecepcao
  Prod  result:  https://www.gnre.pe.gov.br/gnreWS/services/GnreResultadoLote
  Homol lote:    https://www.testegnre.pe.gov.br:444/gnreHWS/services/GnreLoteRecepcao
  Homol result:  https://www.testegnre.pe.gov.br:444/gnreHWS/services/GnreResultadoLote
"""
import os
import time
import logging
from datetime import date, datetime
from decimal import Decimal
from lxml import etree
import requests

from models import NFeDados, GuiaGerada

logger = logging.getLogger(__name__)

_GNRE_NS    = "http://www.gnre.pe.gov.br"
_SOAP12_NS  = "http://www.w3.org/2003/05/soap-envelope"   # WSDL confirma soap12:binding
_WS_LOTE_NS   = "http://www.gnre.pe.gov.br/webservice/GnreLoteRecepcao"
_WS_RESULT_NS = "http://www.gnre.pe.gov.br/webservice/GnreResultadoLote"

# SOAPAction confirmado no WSDL (soap12:operation soapAction=...)
_SOAP_ACTION_LOTE   = "http://www.gnre.pe.gov.br/webservice/GnreLoteRecepcao/processar"
_SOAP_ACTION_RESULT = "http://www.gnre.pe.gov.br/webservice/GnreResultadoLote/consultar"

_TP_AMB    = os.getenv("GNRE_AMBIENTE", "2")   # 2=homolog, 1=prod
_CERT      = os.getenv("CERT_PATH", "")
_KEY       = os.getenv("KEY_PATH", "")
_SSL_VERIFY = os.getenv("GNRE_SSL_VERIFY", "true").lower() not in ("false", "0", "no")

_DEFAULT_PROD_LOTE    = "https://www.gnre.pe.gov.br/gnreWS/services/GnreLoteRecepcao"
_DEFAULT_PROD_RESULT  = "https://www.gnre.pe.gov.br/gnreWS/services/GnreResultadoLote"
_DEFAULT_HOMOL_LOTE   = "https://www.testegnre.pe.gov.br:444/gnreWS/services/GnreLoteRecepcao"
_DEFAULT_HOMOL_RESULT = "https://www.testegnre.pe.gov.br:444/gnreWS/services/GnreResultadoLote"

_WS_LOTE   = (os.getenv("GNRE_WS_URL_PROD", _DEFAULT_PROD_LOTE)
              if _TP_AMB == "1"
              else os.getenv("GNRE_WS_URL_HOMOLOG", _DEFAULT_HOMOL_LOTE))
_WS_RESULT = (os.getenv("GNRE_WS_URL_PROD_RESULT", _DEFAULT_PROD_RESULT)
              if _TP_AMB == "1"
              else os.getenv("GNRE_WS_URL_HOMOLOG_RESULT", _DEFAULT_HOMOL_RESULT))


def _cert_configured() -> bool:
    return bool(_CERT and _KEY and os.path.isfile(_CERT) and os.path.isfile(_KEY))


def _municipio_5dig(codigo: str | None) -> str:
    """Retorna código IBGE com 5 dígitos (sem prefixo de UF). Código 145/146 do manual."""
    if not codigo:
        return ""
    d = "".join(c for c in codigo if c.isdigit())
    # IBGE completo tem 7 dígitos (2 UF + 5 município); GNRE exige apenas os 5 finais
    return d[-5:] if len(d) >= 5 else d


def _digits(s: str | None) -> str:
    if not s:
        return ""
    return "".join(c for c in s if c.isdigit())


# ── GNRE XML builder (TLote_GNRE v2.00) ──────────────────────────────────────

def _el(parent: etree._Element, tag: str, text: str | None = None, **attrs) -> etree._Element:
    node = etree.SubElement(parent, tag, **attrs)
    if text is not None:
        node.text = text
    return node


def _obs_text(dados: NFeDados, max_len: int = 60) -> str:
    """Texto de observação para identificação: 'NF-e nº {n_nf} série {serie}'."""
    n = dados.n_nf or ""
    s = dados.serie or ""
    chave = _digits(dados.chave_nfe or "")
    if n and s:
        base = f"NF-e nº {n} s.{s}"
    elif n:
        base = f"NF-e nº {n}"
    elif chave:
        base = f"NF-e {chave[:20]}"
    else:
        base = "NF-e DIFAL"
    # Se caber, inclui a chave
    full = f"{base} | {chave}" if chave else base
    return full[:max_len]


def _build_gnre_xml(
    dados: NFeDados,
    receita: str,
    valor: Decimal,
    data_pag: date,
    fecp_valor: Decimal | None = None,
) -> str:
    """
    Monta XML TLote_GNRE versao="2.00" usando configuração por UF (UF_DIFAL_CONFIG).

    Para cada UF aplica automaticamente:
    - tipo e valor correto de documentoOrigem (varia por UF)
    - campos extras obrigatórios (chave NF-e, data emissão, etc.)
    - campo convenio (onde exigido) com número NF-e para identificação
    - referência/período (onde exigido)
    - contribuinteDestinatario (onde exigido)
    - valor tipo="12" FCP (para RJ, RO, RS, SE que têm fecp_mesmo_guia)
    """
    from uf_config import UF_DIFAL_CONFIG

    emit = dados.emitente
    dest = dados.destinatario
    dest_uf = dados.uf_dest.upper()
    emi_date = _parse_date(dados.dh_emi) or date.today()
    end = emit.endereco
    chave = _digits(dados.chave_nfe or "")
    n_nf = dados.n_nf or ""

    uf_cfg: dict = UF_DIFAL_CONFIG.get(dest_uf) or {}
    tipo_doc: str | None = uf_cfg.get("tipo_doc")
    doc_valor_tipo: str | None = uf_cfg.get("doc_valor")  # "chave" ou "n_nf"
    exige_dest: bool = uf_cfg.get("exige_dest", True)
    exige_periodo: bool = uf_cfg.get("exige_periodo", False)
    convenio_cfg = uf_cfg.get("convenio", False)  # False | True | "O"
    campos_cfg: list[dict] = uf_cfg.get("campos_extras", [])
    fecp_mesmo_guia: bool = uf_cfg.get("fecp_mesmo_guia", False)

    root = etree.Element("TLote_GNRE", versao="2.00", xmlns=_GNRE_NS)
    guias = _el(root, "guias")
    guide = etree.SubElement(guias, "TDadosGNRE", versao="2.00")

    _el(guide, "ufFavorecida", dest_uf)
    _el(guide, "tipoGnre", "0")

    # ── Emitente ─────────────────────────────────────────────────────────────
    emit_el = _el(guide, "contribuinteEmitente")
    ident = _el(emit_el, "identificacao")
    _el(ident, "CNPJ", _digits(emit.cnpj))
    # IE NÃO é enviada: a IE do emitente é do estado de origem (RS, SP, etc.),
    # não da UF favorecida. Enviar causa erro 700 "INSCRICAO ESTADUAL NAO CADASTRADA".
    # Para DIFAL, o emitente usa apenas CNPJ na identificação.
    _el(emit_el, "razaoSocial", emit.razao_social[:60])
    addr_parts = [p for p in [end.logradouro, end.numero, end.bairro] if p]
    _el(emit_el, "endereco", " ".join(addr_parts)[:60] if addr_parts else "")
    mun_cod = _municipio_5dig(end.municipio_codigo)
    if mun_cod:
        _el(emit_el, "municipio", mun_cod)
    _el(emit_el, "uf", end.uf)
    if end.cep:
        _el(emit_el, "cep", _digits(end.cep))

    # ── Item ──────────────────────────────────────────────────────────────────
    itens_el = _el(guide, "itensGNRE")
    item = _el(itens_el, "item")
    _el(item, "receita", receita)

    # documentoOrigem — tipo e valor dependem da UF
    if tipo_doc and (chave or n_nf):
        doc_val = chave if doc_valor_tipo == "chave" else n_nf
        if doc_val:
            _el(item, "documentoOrigem", doc_val, tipo=tipo_doc)

    # Referência/período — apenas onde exigido
    if exige_periodo:
        ref = _el(item, "referencia")
        _el(ref, "periodo", "0")  # 0 = mensal
        _el(ref, "mes", f"{emi_date.month:02d}")
        _el(ref, "ano", str(emi_date.year))

    _el(item, "dataVencimento", data_pag.strftime("%Y-%m-%d"))
    _el(item, "valor", f"{valor:.2f}", tipo="11")

    # FCP no mesmo guia (RJ, RO, RS, SE)
    if fecp_mesmo_guia and fecp_valor and fecp_valor > Decimal("0"):
        _el(item, "valor", f"{fecp_valor:.2f}", tipo="12")

    # Convênio — observação com NF-e número (max 30 chars, validação 114)
    # Ordem correta conforme manual do portal: valor → convenio → dest → camposExtras
    if convenio_cfg:  # True ou "O" (opcional)
        conv_txt = f"NF-e {n_nf}"[:29] if n_nf else ""
        if conv_txt:
            _el(item, "convenio", conv_txt)

    # Destinatário — onde exigido OU quando temos os dados
    if (exige_dest or dest.cnpj or dest.cpf) and (dest.cnpj or dest.cpf):
        dest_el = _el(item, "contribuinteDestinatario")
        dest_ident = _el(dest_el, "identificacao")
        if dest.cnpj:
            _el(dest_ident, "CNPJ", _digits(dest.cnpj))
        elif dest.cpf:
            _el(dest_ident, "CPF", _digits(dest.cpf))
        if dest.nome:
            _el(dest_el, "razaoSocial", dest.nome[:60])
        dest_mun = _municipio_5dig(dest.municipio_codigo)
        if dest_mun:
            _el(dest_el, "municipio", dest_mun)

    # Campos extras — preenchidos automaticamente a partir dos dados da NF-e
    campos_preenchidos: list[tuple[str, str]] = []
    for ce in campos_cfg:
        cod = ce["codigo"]
        val_tipo = ce["valor"]
        obrig = ce["obrig"]

        if val_tipo == "chave":
            val = chave[:44]
        elif val_tipo == "data_emi":
            val = emi_date.strftime("%Y-%m-%d")
        elif val_tipo == "obs":
            val = _obs_text(dados, max_len=100)
        else:
            val = ""

        if val or obrig:
            campos_preenchidos.append((cod, val))

    if campos_preenchidos:
        extras_el = _el(item, "camposExtras")
        for cod, val in campos_preenchidos:
            ce_el = _el(extras_el, "campoExtra")
            _el(ce_el, "codigo", cod)
            _el(ce_el, "valor", val)

    # ── Totais do TDadosGNRE ─────────────────────────────────────────────────
    valor_total = valor + (fecp_valor or Decimal("0")) if fecp_mesmo_guia else valor
    _el(guide, "valorGNRE", f"{valor_total:.2f}")
    _el(guide, "dataPagamento", data_pag.strftime("%Y-%m-%d"))

    return etree.tostring(
        root, pretty_print=False,
        xml_declaration=True, encoding="UTF-8", standalone=True,
    ).decode()


def _build_consulta_xml(numero_recibo: str) -> str:
    """
    Monta TconsLote_GNRE conforme schema lote_gnre_consulta_v1.00.xsd (seção 4.2.1).
    Campos: ambiente + numeroRecibo (não tpAmb/nRec como estava antes).
    """
    root = etree.Element("TConsLote_GNRE", xmlns=_GNRE_NS)
    _el(root, "ambiente", _TP_AMB)
    _el(root, "numeroRecibo", numero_recibo)
    return etree.tostring(root, pretty_print=False, encoding="unicode")


def _parse_date(dh: str | None) -> date | None:
    if not dh:
        return None
    for fmt, length in [
        ("%Y-%m-%dT%H:%M:%S%z", 19),
        ("%Y-%m-%dT%H:%M:%S",   19),
        ("%Y-%m-%d",            10),
        ("%d/%m/%Y",            10),
    ]:
        try:
            return datetime.strptime(dh[:length], fmt).date()
        except ValueError:
            continue
    return None


# ── SOAP 1.2 envelope builders ───────────────────────────────────────────────
# Confirmado pelo WSDL real (soap12:binding, document/literal):
# - gnreDadosMsg é <s:any/> → recebe XML filho real, não string/CDATA
# - gnreCabecMsg vai no Header, no namespace do serviço
# - SOAPAction = URL completa (confirmada no WSDL)
# - Sem wrapper "processar/consultar" no Body — document/literal usa o elemento diretamente

def _build_soap_lote(gnre_xml: str) -> bytes:
    """
    SOAP 1.2 para GnreLoteRecepcao.
    Body: gnreDadosMsg com TLote_GNRE como filho XML real.
    Header: gnreCabecMsg com versaoDados.
    """
    S = _SOAP12_NS
    G = _WS_LOTE_NS

    env = etree.Element(f"{{{S}}}Envelope", nsmap={"soapenv": S, "gnr": G})

    hdr = etree.SubElement(env, f"{{{S}}}Header")
    cab = etree.SubElement(hdr, f"{{{G}}}gnreCabecMsg")
    etree.SubElement(cab, f"{{{G}}}versaoDados").text = "2.00"

    body = etree.SubElement(env, f"{{{S}}}Body")
    msg = etree.SubElement(body, f"{{{G}}}gnreDadosMsg")
    # gnreDadosMsg é xsd:any → TLote_GNRE como elemento filho real
    msg.append(etree.fromstring(gnre_xml.encode()))

    return etree.tostring(env, xml_declaration=True, encoding="UTF-8")


def _build_soap_consulta(consulta_xml: str) -> bytes:
    """SOAP 1.2 para GnreResultadoLote."""
    S = _SOAP12_NS
    G = _WS_RESULT_NS

    env = etree.Element(f"{{{S}}}Envelope", nsmap={"soapenv": S, "gnr": G})

    hdr = etree.SubElement(env, f"{{{S}}}Header")
    cab = etree.SubElement(hdr, f"{{{G}}}gnreCabecMsg")
    etree.SubElement(cab, f"{{{G}}}versaoDados").text = "2.00"

    body = etree.SubElement(env, f"{{{S}}}Body")
    msg = etree.SubElement(body, f"{{{G}}}gnreDadosMsg")
    msg.append(etree.fromstring(consulta_xml.encode()))

    return etree.tostring(env, xml_declaration=True, encoding="UTF-8")


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _post_soap(url: str, soap_bytes: bytes, cert: tuple[str, str] | None, soap_action: str = "") -> str:
    # SOAP 1.2: action vai no Content-Type; também enviamos como header SOAPAction
    # para compatibilidade com Apache Axis que usa ambos
    ct = f'application/soap+xml;charset=utf-8;action="{soap_action}"'
    kwargs: dict = {
        "data": soap_bytes,
        "headers": {
            "Content-Type": ct,
            "SOAPAction": f'"{soap_action}"',
        },
        "timeout": 30,
        "verify": _SSL_VERIFY,
    }
    if cert:
        kwargs["cert"] = cert

    logger.info("GNRE WS POST %s cert=%s", url, bool(cert))
    try:
        resp = requests.post(url, **kwargs)
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.SSLError as exc:
        raise RuntimeError(f"Erro SSL ao conectar ao GNRE: {exc}") from exc
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(f"Sem conexão com WebService GNRE ({url}): {exc}") from exc
    except requests.exceptions.HTTPError as exc:
        body = resp.text[:500] if resp.text else "(vazio)"
        raise RuntimeError(
            f"WebService GNRE HTTP {resp.status_code} em {url}\n"
            f"Resposta: {body}\n"
            "Verifique se o acesso ao WebService foi solicitado no portal GNRE "
            "(automacao.jsp → 'Solicitar uso do WebService')."
        ) from exc


# ── Response parsers ──────────────────────────────────────────────────────────

def _extract_gnre_xml_from_soap(soap_text: str) -> etree._Element:
    """
    Extrai o XML GNRE de dentro da resposta SOAP.
    O portal GNRE retorna o XML dentro de <gnreRetornoMsg> como string (seção 3.4.1).
    """
    try:
        soap_root = etree.fromstring(soap_text.encode())
    except Exception as exc:
        raise RuntimeError(f"Resposta SOAP inválida: {soap_text[:300]}") from exc

    # Buscar gnreRetornoMsg em qualquer namespace
    for tag_search in (
        f"{{{_WS_LOTE_NS}}}gnreRetornoMsg",
        f"{{{_WS_RESULT_NS}}}gnreRetornoMsg",
        "gnreRetornoMsg",
    ):
        node = soap_root.find(f".//{tag_search}")
        if node is not None and node.text:
            inner = node.text.strip()
            try:
                return etree.fromstring(inner.encode())
            except Exception:
                pass  # fallback: some implementations return XML nodes directly

    # Fallback: o GNRE XML pode estar embutido como nós XML reais (não como string)
    return soap_root


def _find(root: etree._Element, tag: str) -> str:
    """Busca tag em qualquer nível, com ou sem namespace GNRE."""
    for ns in (_GNRE_NS, ""):
        search = f".//{{{ns}}}{tag}" if ns else f".//{tag}"
        node = root.find(search)
        if node is not None and node.text:
            return node.text.strip()
    return ""


def _find_motivos_rejeicao(root: etree._Element) -> str:
    """Extrai e formata os motivos de rejeição da guia (campo motivosRejeicao)."""
    motivos = []
    for ns in (_GNRE_NS, ""):
        prefix = f"{{{ns}}}" if ns else ""
        for motivo in root.findall(f".//{prefix}motivo"):
            def _first(*els):
                return next((e for e in els if e is not None), None)
            cod_el = _first(motivo.find(f"{prefix}codigo"), motivo.find(f"{{{_GNRE_NS}}}codigo"))
            desc_el = _first(motivo.find(f"{prefix}descricao"), motivo.find(f"{{{_GNRE_NS}}}descricao"))
            campo_el = _first(motivo.find(f"{prefix}campo"), motivo.find(f"{{{_GNRE_NS}}}campo"))
            cod = (cod_el.text or "").strip() if cod_el is not None else ""
            desc = (desc_el.text or "").strip() if desc_el is not None else ""
            campo = (campo_el.text or "").strip() if campo_el is not None else ""
            if desc:
                parte = f"[{cod}] {desc}"
                if campo:
                    parte += f" (campo: {campo})"
                motivos.append(parte)
        if motivos:
            break
    return " | ".join(motivos)


def _parse_recepcao(soap_text: str) -> str:
    """
    Parse da resposta GnreLoteRecepcao → retorna número do recibo ou lança exceção.
    Campos: situacaoRecepcao/codigo, situacaoRecepcao/descricao, recibo/numero (seção 4.1.2).
    """
    root = _extract_gnre_xml_from_soap(soap_text)

    codigo = _find(root, "codigo")
    descricao = _find(root, "descricao")
    numero_recibo = _find(root, "numero")

    logger.info("GNRE recepcao codigo=%s descricao=%s recibo=%s", codigo, descricao, numero_recibo)

    if codigo == "100" and numero_recibo:
        return numero_recibo

    raise RuntimeError(
        f"Lote rejeitado pelo WebService GNRE. "
        f"Código: {codigo} — {descricao or 'sem descrição'}\n"
        f"Resposta bruta: {soap_text[:400]}"
    )


def _parse_resultado(soap_text: str) -> dict:
    """
    Parse da resposta GnreResultadoLote (seção 4.2.2).
    Códigos situacaoProcess (Quadro IV):
      400 = Aguardando processamento
      401 = Em processamento
      402 = Processado com sucesso
      403 = Processado com pendência
      404 = Erro no processamento
    situacaoGuia: 0=sucesso, 1=invalidada portal, 2=invalidada UF, 3=erro comunicação, 4=pendência
    """
    root = _extract_gnre_xml_from_soap(soap_text)

    codigo = _find(root, "codigo")
    descricao = _find(root, "descricao")

    logger.info("GNRE resultado codigo=%s descricao=%s", codigo, descricao)

    # Extrai dados da guia (situacaoGuia, barcode, motivo)
    situacao_guia = _find(root, "situacaoGuia")
    barcode = _find(root, "codigoBarras")
    linha = _find(root, "linhaDigitavel")
    qrcode = _find(root, "qrcodePayload")
    numero_guia = _find(root, "numeroGuia") or None
    # Motivo de rejeição específico (dentro de motivosRejeicao)
    motivo_rej = _find_motivos_rejeicao(root)

    if codigo == "402":
        if situacao_guia == "0":
            return {
                "status": "gerada",
                "codigo_barras": barcode or None,
                "linha_digitavel": linha or None,
                "qrcode_pix": qrcode or None,
                "numero_guia": numero_guia,
                "mensagem": descricao,
            }
        return {
            "status": "erro",
            "mensagem": motivo_rej or f"Guia invalidada (situação {situacao_guia}): {descricao}",
        }

    if codigo in ("400", "401"):
        return {"status": "processando", "mensagem": descricao}

    if codigo == "403":
        if barcode and situacao_guia == "0":
            return {
                "status": "gerada",
                "codigo_barras": barcode,
                "linha_digitavel": linha or None,
                "qrcode_pix": qrcode or None,
                "numero_guia": numero_guia,
                "mensagem": descricao,
            }
        return {
            "status": "erro",
            "mensagem": motivo_rej or f"Processado com pendência: {descricao}",
        }

    return {"status": "erro", "mensagem": f"Código {codigo}: {descricao}"}


# ── WebService orchestration ──────────────────────────────────────────────────

def _call_webservice(gnre_xml: str, cert: tuple[str, str] | None) -> dict:
    """
    Fluxo assíncrono completo (seção 3.3.2 do manual):
      1. Envia lote ao GnreLoteRecepcao → obtém número do recibo
      2. Aguarda ≥30s (requisito mínimo do manual para não obter 401)
      3. Consulta GnreResultadoLote até obter resultado final
    """
    soap_lote = _build_soap_lote(gnre_xml)
    resp_lote = _post_soap(_WS_LOTE, soap_lote, cert, soap_action=_SOAP_ACTION_LOTE)
    numero_recibo = _parse_recepcao(resp_lote)
    logger.info("GNRE lote aceito, recibo=%s", numero_recibo)

    # Manual exige mínimo 30s de espera antes de consultar (seção 4.2.3)
    espera_inicial = 30 if _TP_AMB == "1" else 5
    logger.info("Aguardando %ds antes de consultar resultado...", espera_inicial)
    time.sleep(espera_inicial)

    consul_xml = _build_consulta_xml(numero_recibo)
    soap_consul = _build_soap_consulta(consul_xml)

    for tentativa in range(6):
        if tentativa:
            time.sleep(10)
        resp_result = _post_soap(_WS_RESULT, soap_consul, cert, soap_action=_SOAP_ACTION_RESULT)
        result = _parse_resultado(resp_result)
        if result.get("status") != "processando":
            return result
        logger.info("GNRE ainda processando (tentativa %d/6)...", tentativa + 1)

    return {
        "status": "processando",
        "mensagem": (
            f"Lote enviado (recibo={numero_recibo}). "
            "Processamento pendente — consulte novamente em alguns instantes."
        ),
        "numero_recibo": numero_recibo,
    }


# ── Public interface ──────────────────────────────────────────────────────────

def gerar_gnre(
    dados: NFeDados,
    receita: str,
    descricao: str,
    valor: Decimal,
    data_pag: date | None = None,
    cert_paths: tuple[str, str] | None = None,
    fecp_valor: Decimal | None = None,
) -> GuiaGerada:
    """
    Gera uma guia GNRE via WebService.
    cert_paths: (cert.pem, key.pem) da empresa — tem prioridade sobre .env global.
    fecp_valor: valor FCP para inclusão no mesmo guia (estados com fecp_mesmo_guia=True).
    Sem certificado: retorna XML para download manual (status pendente_webservice).
    """
    from datetime import date as _date
    data_pag = data_pag or _date.today()

    xml_str = _build_gnre_xml(dados, receita, valor, data_pag, fecp_valor=fecp_valor)
    effective_cert = cert_paths or ((_CERT, _KEY) if _cert_configured() else None)

    if effective_cert:
        try:
            ws_result = _call_webservice(xml_str, effective_cert)
            return GuiaGerada(
                tipo="GNRE",
                uf=dados.uf_dest,
                receita_codigo=receita,
                receita_descricao=descricao,
                valor=valor,
                data_vencimento=data_pag.strftime("%d/%m/%Y"),
                codigo_barras=ws_result.get("codigo_barras"),
                linha_digitavel=ws_result.get("linha_digitavel"),
                qrcode_pix=ws_result.get("qrcode_pix"),
                numero_guia=ws_result.get("numero_guia"),
                gnre_xml=xml_str,
                status=ws_result.get("status", "erro"),
                mensagem=ws_result.get("mensagem"),
            )
        except RuntimeError as exc:
            logger.error("GNRE WS error: %s", exc)
            return GuiaGerada(
                tipo="GNRE",
                uf=dados.uf_dest,
                receita_codigo=receita,
                receita_descricao=descricao,
                valor=valor,
                data_vencimento=data_pag.strftime("%d/%m/%Y"),
                gnre_xml=xml_str,
                status="pendente_webservice",
                mensagem=str(exc),
            )

    return GuiaGerada(
        tipo="GNRE",
        uf=dados.uf_dest,
        receita_codigo=receita,
        receita_descricao=descricao,
        valor=valor,
        data_vencimento=data_pag.strftime("%d/%m/%Y"),
        gnre_xml=xml_str,
        status="pendente_webservice",
        mensagem="Cadastre o certificado A1 da empresa para envio automático ao WebService GNRE.",
    )
