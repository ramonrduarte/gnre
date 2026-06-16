"""
GNRE guide generation:
  1. Builds the GNRE 2.0 XML (lxml)
  2. Optionally submits to the national GNRE WebService (SOAP)
  3. Falls back to "pendente_webservice" status with downloadable XML when
     no certificate is configured (common for homologation / first setup)

WebService endpoint (Pernambuco hosts national GNRE):
  Production:   https://www.gnre.pe.gov.br/gnreWS/services/GnreDebitoSincrono
  Homologação:  https://www.gnre.pe.gov.br/gnreHWS/services/GnreDebitoSincrono
"""
import os
import base64
from datetime import date, datetime
from decimal import Decimal
from lxml import etree
import requests

from models import NFeDados, GuiaGerada

_GNRE_NS = "http://www.gnre.pe.gov.br"
_WS_PROD = os.getenv(
    "GNRE_WS_URL_PROD",
    "https://www.gnre.pe.gov.br/gnreWS/services/GnreDebitoSincrono",
)
_WS_HOMOLOG = os.getenv(
    "GNRE_WS_URL_HOMOLOG",
    "https://www.gnre.pe.gov.br/gnreHWS/services/GnreDebitoSincrono",
)

_TP_AMB = os.getenv("GNRE_AMBIENTE", "2")  # 2=homolog, 1=prod
_CERT = os.getenv("CERT_PATH", "")
_KEY = os.getenv("KEY_PATH", "")
_SSL_VERIFY = os.getenv("GNRE_SSL_VERIFY", "true").lower() not in ("false", "0", "no")

# Only use cert if both paths are configured AND files actually exist
def _cert_configured() -> bool:
    return bool(_CERT and _KEY and os.path.isfile(_CERT) and os.path.isfile(_KEY))


# ── XML builder ────────────────────────────────────────────────────────────────

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
    """
    Build a TLote_GNRE 2.00 XML with a single TDadosGNRE guide item.
    Returns the XML as a UTF-8 string.
    """
    emit = dados.emitente
    dest_uf = dados.uf_dest
    doc_num = emit.cnpj  # numDoc = CNPJ/CPF do contribuinte
    tipo_doc = "1"  # 1=CNPJ, 2=CPF

    root = etree.Element("TLote_GNRE", versao="2.00", xmlns=_GNRE_NS)
    guias = _el(root, "guias")
    guide = etree.SubElement(guias, "TDadosGNRE", versao="2.00")

    _el(guide, "ufFavorecida", dest_uf)
    _el(guide, "tpAmb", _TP_AMB)
    _el(guide, "numDoc", doc_num)

    contribuinte = _el(guide, "contribuinte")
    _el(contribuinte, "tipo", tipo_doc)
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


def _parse_date(dh: str | None) -> date | None:
    if not dh:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(dh[:19], fmt[:len(fmt)]).date()
        except ValueError:
            continue
    return None


# ── WebService SOAP call ────────────────────────────────────────────────────────

_SOAP_ENVELOPE = """\
<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:gnr="http://www.gnre.pe.gov.br/wsdl/GnreDebitoSincrono">
  <soapenv:Header/>
  <soapenv:Body>
    <gnr:gnreDebitoSincrono>
      <msg_dados><![CDATA[{gnre_xml}]]></msg_dados>
    </gnr:gnreDebitoSincrono>
  </soapenv:Body>
</soapenv:Envelope>"""


def _call_webservice(gnre_xml: str, cert_override: tuple[str, str] | None = None) -> dict:
    """
    Send GNRE XML to the national webservice and return parsed result.
    cert_override: (cert.pem path, key.pem path) — empresa cert takes priority.
    """
    url = _WS_PROD if _TP_AMB == "1" else _WS_HOMOLOG
    soap = _SOAP_ENVELOPE.format(gnre_xml=gnre_xml)

    kwargs: dict = {
        "data": soap.encode("utf-8"),
        "headers": {
            "Content-Type": "text/xml;charset=utf-8",
            "SOAPAction": '""',
        },
        "timeout": 30,
        "verify": _SSL_VERIFY,
    }
    # Use empresa cert if provided, fall back to global .env cert
    cert = cert_override or ((_CERT, _KEY) if _cert_configured() else None)
    if cert:
        kwargs["cert"] = cert

    try:
        resp = requests.post(url, **kwargs)
        resp.raise_for_status()
        return _parse_ws_response(resp.text)
    except requests.exceptions.SSLError as exc:
        raise RuntimeError(f"Erro SSL no WebService GNRE. Verifique o certificado. Detalhe: {exc}") from exc
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(f"Não foi possível conectar ao WebService GNRE: {exc}") from exc
    except requests.exceptions.HTTPError as exc:
        body_preview = resp.text[:300] if resp.text else "(sem corpo)"
        raise RuntimeError(
            f"WebService GNRE retornou HTTP {resp.status_code}. "
            f"URL: {url} | Resposta: {body_preview}"
        ) from exc


def _parse_ws_response(xml_text: str) -> dict:
    """Parse GNRE WebService SOAP response and return normalized dict."""
    try:
        root = etree.fromstring(xml_text.encode())
    except Exception:
        return {"status": "erro", "mensagem": "Resposta inválida do WebService"}

    ns = {"s": "http://schemas.xmlsoap.org/soap/envelope/", "g": _GNRE_NS}
    ret_node = root.find(".//g:TRetLote_GNRE", ns)
    if ret_node is None:
        # Try without namespace
        ret_node = root.find(".//{%s}TRetLote_GNRE" % _GNRE_NS)
    if ret_node is None:
        return {"status": "erro", "mensagem": "Resposta sem TRetLote_GNRE"}

    def txt(el, path):
        node = el.find(".//" + path.replace("/", "//{%s}" % _GNRE_NS).lstrip("/"))
        if node is None:
            node = el.find(".//{%s}%s" % (_GNRE_NS, path.split("/")[-1]))
        return (node.text or "").strip() if node is not None else ""

    codigo = txt(ret_node, "codigo")
    descricao = txt(ret_node, "descricao")
    barcode = txt(ret_node, "codigoDeBarras")
    linha = txt(ret_node, "linhaDigitavel")

    if codigo == "1":
        return {
            "status": "gerada",
            "codigo_barras": barcode,
            "linha_digitavel": linha,
            "mensagem": descricao,
        }
    return {"status": "erro", "mensagem": descricao or f"Código: {codigo}"}


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
    Generate a single GNRE guide for the given receita/value.
    cert_paths: (cert.pem, key.pem) from the empresa DB record — takes priority
    over the global .env CERT_PATH/KEY_PATH settings.
    Falls back to 'pendente_webservice' (downloadable XML) when no cert is available.
    """
    from datetime import date as _date
    data_pag = data_pag or _date.today()

    xml_str = _build_gnre_xml(dados, receita, valor, data_pag)

    # Resolve which certificate to use: empresa cert > global .env > none
    effective_cert = cert_paths or ((_CERT, _KEY) if _cert_configured() else None)

    # Only attempt WebService call when we have a cert or in homologation without cert
    ws_enabled = effective_cert is not None or (_TP_AMB == "2" and not _CERT and not _KEY)

    if ws_enabled:
        try:
            ws_result = _call_webservice(xml_str, cert_override=effective_cert)
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
                status=ws_result.get("status", "erro"),
                mensagem=ws_result.get("mensagem"),
            )
        except RuntimeError as exc:
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
