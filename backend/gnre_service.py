"""
GNRE guide generation — WebService assíncrono (lote):

Fluxo:
  1. Montar XML TLote_GNRE 2.00
  2. Enviar ao GnreLoteRecepcao → recebe nRec (protocolo)
  3. Consultar GnreResultadoLote com o nRec → recebe guia com código de barras

URLs (produção / homologação configuráveis por env var):
  Prod  lote:    https://www.gnre.pe.gov.br/gnreWS/services/GnreLoteRecepcao
  Prod  result:  https://www.gnre.pe.gov.br/gnreWS/services/GnreResultadoLote
  Homol lote:    https://www.testegnre.pe.gov.br:444/gnreHWS/services/GnreLoteRecepcao
  Homol result:  https://www.testegnre.pe.gov.br:444/gnreHWS/services/GnreResultadoLote

IMPORTANTE: o acesso ao WebService deve ser solicitado em:
  https://www.gnre.pe.gov.br/gnre/portal/automacao.jsp → "Solicitar uso do WebService"
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

_GNRE_NS = "http://www.gnre.pe.gov.br"
_TP_AMB = os.getenv("GNRE_AMBIENTE", "2")  # 2=homolog, 1=prod
_CERT = os.getenv("CERT_PATH", "")
_KEY = os.getenv("KEY_PATH", "")
_SSL_VERIFY = os.getenv("GNRE_SSL_VERIFY", "true").lower() not in ("false", "0", "no")

# WebService URLs — configuráveis por variável de ambiente
_DEFAULT_PROD_LOTE    = "https://www.gnre.pe.gov.br/gnreWS/services/GnreLoteRecepcao"
_DEFAULT_PROD_RESULT  = "https://www.gnre.pe.gov.br/gnreWS/services/GnreResultadoLote"
_DEFAULT_HOMOL_LOTE   = "https://www.testegnre.pe.gov.br:444/gnreHWS/services/GnreLoteRecepcao"
_DEFAULT_HOMOL_RESULT = "https://www.testegnre.pe.gov.br:444/gnreHWS/services/GnreResultadoLote"

_WS_LOTE   = os.getenv("GNRE_WS_URL_PROD",   _DEFAULT_PROD_LOTE)   if _TP_AMB == "1" else os.getenv("GNRE_WS_URL_HOMOLOG",   _DEFAULT_HOMOL_LOTE)
_WS_RESULT = os.getenv("GNRE_WS_URL_PROD_RESULT", _DEFAULT_PROD_RESULT) if _TP_AMB == "1" else os.getenv("GNRE_WS_URL_HOMOLOG_RESULT", _DEFAULT_HOMOL_RESULT)


def _cert_configured() -> bool:
    return bool(_CERT and _KEY and os.path.isfile(_CERT) and os.path.isfile(_KEY))


# ── XML builder ──────────────────────────────────────────────────────────────

def _el(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
    node = etree.SubElement(parent, tag)
    if text is not None:
        node.text = text
    return node


def _build_gnre_xml(
    dados: NFeDados,
    receita: str,
    valor: Decimal,
    data_pag: date,
) -> str:
    """Monta TLote_GNRE 2.00 com uma única guia TDadosGNRE."""
    emit = dados.emitente
    dest_uf = dados.uf_dest

    root = etree.Element("TLote_GNRE", versao="2.00", xmlns=_GNRE_NS)
    guias = _el(root, "guias")
    guide = etree.SubElement(guias, "TDadosGNRE", versao="2.00")

    _el(guide, "ufFavorecida", dest_uf)
    _el(guide, "tpAmb", _TP_AMB)
    _el(guide, "numDoc", emit.cnpj)

    contribuinte = _el(guide, "contribuinte")
    _el(contribuinte, "tipo", "1")  # 1=CNPJ
    _el(contribuinte, "CNPJ", emit.cnpj)
    _el(contribuinte, "ie", emit.ie or "ISENTO")
    _el(contribuinte, "razaoSocial", emit.razao_social[:60])

    endereco = _el(contribuinte, "endereco")
    if emit.endereco.cep:
        _el(endereco, "CEP", emit.endereco.cep.replace("-", ""))
    if emit.endereco.logradouro:
        _el(endereco, "logradouro", emit.endereco.logradouro[:60])
    if emit.endereco.numero:
        _el(endereco, "numero", emit.endereco.numero)
    if emit.endereco.complemento:
        _el(endereco, "complemento", emit.endereco.complemento[:60])
    if emit.endereco.bairro:
        _el(endereco, "bairro", emit.endereco.bairro[:60])
    municipio = _el(endereco, "municipio")
    if emit.endereco.municipio_codigo:
        _el(municipio, "codigo", emit.endereco.municipio_codigo)
    _el(endereco, "uf", emit.endereco.uf)

    itens = _el(guide, "itens")
    item = etree.SubElement(itens, "TItem")
    _el(item, "receita", receita)

    if dados.chave_nfe:
        doc_origem = _el(item, "docOrigem")
        _el(doc_origem, "tipo", "10")  # 10=NF-e
        _el(doc_origem, "numero", dados.chave_nfe)

    produto = _el(item, "produto")
    _el(produto, "tipo", "0")

    referencia = _el(item, "referencia")
    periodo = _el(referencia, "periodo")
    emi_date = _parse_date(dados.dh_emi) or date.today()
    _el(periodo, "mes", f"{emi_date.month:02d}")
    _el(periodo, "ano", str(emi_date.year))

    _el(item, "valor", f"{valor:.2f}")
    _el(item, "dataPagamento", data_pag.strftime("%d/%m/%Y"))

    return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8").decode()


def _build_consulta_xml(n_rec: str) -> str:
    """Monta TConsLoteGNRE para consultar o resultado de um lote pelo nRec."""
    root = etree.Element("TConsLoteGNRE", versao="2.00", xmlns=_GNRE_NS)
    _el(root, "tpAmb", _TP_AMB)
    _el(root, "nRec", n_rec)
    return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8").decode()


def _parse_date(dh: str | None) -> date | None:
    if not dh:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(dh[:19], fmt[:len(fmt)]).date()
        except ValueError:
            continue
    return None


# ── SOAP envelopes ────────────────────────────────────────────────────────────

_SOAP_LOTE = """\
<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:gnr="http://www.gnre.pe.gov.br/webservice/GnreLoteRecepcao">
  <soapenv:Header/>
  <soapenv:Body>
    <gnr:processar>
      <msg_dados><![CDATA[{xml}]]></msg_dados>
    </gnr:processar>
  </soapenv:Body>
</soapenv:Envelope>"""

_SOAP_CONSULTA = """\
<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:gnr="http://www.gnre.pe.gov.br/webservice/GnreResultadoLote">
  <soapenv:Header/>
  <soapenv:Body>
    <gnr:processar>
      <msg_dados><![CDATA[{xml}]]></msg_dados>
    </gnr:processar>
  </soapenv:Body>
</soapenv:Envelope>"""


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _post_soap(url: str, soap: str, cert: tuple[str, str] | None) -> str:
    """Send SOAP request; returns response body or raises RuntimeError."""
    kwargs: dict = {
        "data": soap.encode("utf-8"),
        "headers": {
            "Content-Type": "text/xml;charset=utf-8",
            "SOAPAction": '""',
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
        raise RuntimeError(f"Erro SSL: {exc}") from exc
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(f"Sem conexão com WebService GNRE ({url}): {exc}") from exc
    except requests.exceptions.HTTPError as exc:
        body = resp.text[:400] if resp.text else "(vazio)"
        raise RuntimeError(
            f"WebService GNRE HTTP {resp.status_code} em {url}\n"
            f"Resposta: {body}\n"
            "Verifique se o acesso ao WebService foi solicitado no portal GNRE "
            "(automacao.jsp → 'Solicitar uso do WebService')."
        ) from exc


# ── Response parsers ──────────────────────────────────────────────────────────

def _find(el: etree._Element, *tags: str) -> str:
    """Find first matching tag (with or without namespace) and return text."""
    for tag in tags:
        node = el.find(".//{%s}%s" % (_GNRE_NS, tag))
        if node is None:
            node = el.find(".//" + tag)
        if node is not None and node.text:
            return node.text.strip()
    return ""


def _parse_recepcao(xml_text: str) -> str:
    """Parse GnreLoteRecepcao response → return nRec or raise."""
    try:
        root = etree.fromstring(xml_text.encode())
    except Exception as exc:
        raise RuntimeError(f"Resposta inválida do WebService: {xml_text[:200]}") from exc

    c_stat = _find(root, "cStat")
    x_motivo = _find(root, "xMotivo")
    n_rec = _find(root, "nRec")

    logger.info("GNRE recepcao cStat=%s xMotivo=%s nRec=%s", c_stat, x_motivo, n_rec)

    # cStat 100 = lote aceito; 225 = lote duplicado (nRec ainda válido)
    if c_stat in ("100", "225") and n_rec:
        return n_rec
    raise RuntimeError(
        f"Lote rejeitado pelo WebService GNRE. "
        f"Código: {c_stat} — {x_motivo or 'sem descrição'}"
    )


def _parse_resultado(xml_text: str) -> dict:
    """Parse GnreResultadoLote response → return result dict."""
    try:
        root = etree.fromstring(xml_text.encode())
    except Exception as exc:
        raise RuntimeError(f"Resposta de resultado inválida: {xml_text[:200]}") from exc

    c_stat = _find(root, "cStat")
    x_motivo = _find(root, "xMotivo")

    logger.info("GNRE resultado cStat=%s xMotivo=%s", c_stat, x_motivo)

    # cStat 100 = processado com sucesso
    if c_stat == "100":
        barcode = _find(root, "codigoDeBarras", "codBarras")
        linha = _find(root, "linhaDigitavel")
        return {
            "status": "gerada",
            "codigo_barras": barcode or None,
            "linha_digitavel": linha or None,
            "mensagem": x_motivo,
        }
    # cStat 106 = lote em processamento — chamador deve tentar novamente
    if c_stat == "106":
        return {"status": "processando", "mensagem": x_motivo}

    return {"status": "erro", "mensagem": f"Código {c_stat}: {x_motivo}"}


# ── WebService orchestration ──────────────────────────────────────────────────

def _call_webservice(gnre_xml: str, cert: tuple[str, str] | None) -> dict:
    """
    Full async flow:
      1. Send lote → get nRec
      2. Poll GnreResultadoLote (up to ~15 s) → get result
    Returns result dict with status/barcode or raises RuntimeError.
    """
    # Step 1: send lote
    soap_lote = _SOAP_LOTE.format(xml=gnre_xml)
    resp_lote = _post_soap(_WS_LOTE, soap_lote, cert)
    n_rec = _parse_recepcao(resp_lote)

    # Step 2: poll for result (GNRE can be fast in homolog; up to 5 tries × 3 s)
    consul_xml = _build_consulta_xml(n_rec)
    soap_consul = _SOAP_CONSULTA.format(xml=consul_xml)

    for attempt in range(5):
        if attempt:
            time.sleep(3)
        resp_result = _post_soap(_WS_RESULT, soap_consul, cert)
        result = _parse_resultado(resp_result)
        if result["status"] != "processando":
            return result

    # Still processing after retries → return nRec so UI can show status
    return {
        "status": "processando",
        "mensagem": f"Lote enviado (nRec={n_rec}). Processamento pendente — tente novamente em instantes.",
        "n_rec": n_rec,
    }


# ── Public interface ──────────────────────────────────────────────────────────

def gerar_gnre(
    dados: NFeDados,
    receita: str,
    descricao: str,
    valor: Decimal,
    data_pag: date | None = None,
    cert_paths: tuple[str, str] | None = None,
) -> GuiaGerada:
    """
    Gera uma guia GNRE.
    cert_paths: (cert.pem, key.pem) da empresa — prioridade sobre .env global.
    Sem certificado: retorna XML para download (status pendente_webservice).
    """
    from datetime import date as _date
    data_pag = data_pag or _date.today()

    xml_str = _build_gnre_xml(dados, receita, valor, data_pag)

    effective_cert = cert_paths or ((_CERT, _KEY) if _cert_configured() else None)

    if effective_cert:
        try:
            ws_result = _call_webservice(xml_str, effective_cert)
            status = ws_result.get("status", "erro")
            return GuiaGerada(
                tipo="GNRE",
                uf=dados.uf_dest,
                receita_codigo=receita,
                receita_descricao=descricao,
                valor=valor,
                data_vencimento=data_pag.strftime("%d/%m/%Y"),
                codigo_barras=ws_result.get("codigo_barras"),
                linha_digitavel=ws_result.get("linha_digitavel"),
                gnre_xml=xml_str,
                status=status,
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
