"""
Integração com WebService DUA-e do Espírito Santo (SEFAZ/ES).

Documentação: Manual de Integração DUA-e v1.01b (Agosto/2023)
  https://s1-internet.sefaz.es.gov.br/site-internet-dua/assets/arquivos/Manual_DUA_v1.01b.pdf

WebService:
  Produção:    https://app.sefaz.es.gov.br/WsDua/DuaService.asmx
  Homologação: https://homologacao.sefaz.es.gov.br/WsDua/DuaService.asmx

Protocolo: SOAP 1.2, autenticação por certificado digital A1/ICP-Brasil (mesmo do GNRE).

Serviço DIFAL EC 87/2015:
  cArea = 5      (Receita de ICMS)
  cServ = 3867   (ICMS - Diferencial de Alíquota EC 87)
  cnpjOrg = 27080571000130  (CNPJ da SEFAZ/ES)

Fluxo: síncrono — envia emisDua → recebe retEmisDua com nBar (código de barras 48 dígitos).
"""
import os
import logging
from datetime import date
from decimal import Decimal
from lxml import etree
import requests

from models import NFeDados, GuiaGerada

logger = logging.getLogger(__name__)

_NS_DUAE   = "http://www.sefaz.es.gov.br/duae"
_NS_SOAP12 = "http://www.w3.org/2003/05/soap-envelope"

_TP_AMB = os.getenv("GNRE_AMBIENTE", "2")  # 2=homolog, 1=prod (compartilha com GNRE)
_CERT   = os.getenv("CERT_PATH", "")
_KEY    = os.getenv("KEY_PATH", "")

_WS_PROD  = "https://app.sefaz.es.gov.br/WsDua/DuaService.asmx"
_WS_HOMOL = "https://homologacao.sefaz.es.gov.br/WsDua/DuaService.asmx"
_WS_URL   = _WS_PROD if _TP_AMB == "1" else _WS_HOMOL

_CNPJ_ORG_SEFAZ_ES = "27080571000130"  # CNPJ da SEFAZ/ES (cnpjOrg fixo)
_C_AREA_ICMS       = "5"               # Área ICMS
_C_SERV_DIFAL      = "3867"            # ICMS Diferencial de Alíquota EC 87

# Mapa IBGE 5-dígitos → código DUA-ES (obtido via duaConsultaMunicipio em 17/06/2026)
# O ES usa códigos municipais próprios (5xxxx) diferentes do padrão IBGE
_MUN_IBGE_TO_DUA: dict[str, str] = {
    "05070": "56014",  # AFONSO CLAUDIO
    "05088": "57177",  # AGUA DOCE DO NORTE
    "05096": "57339",  # AGUIA BRANCA
    "05119": "56030",  # ALEGRE
    "05135": "56057",  # ALFREDO CHAVES
    "05151": "57193",  # ALTO RIO NOVO
    "05178": "56073",  # ANCHIETA
    "05194": "56090",  # APIACA
    "05216": "56111",  # ARACRUZ
    "05232": "56138",  # ATILIO VIVACQUA
    "05259": "56154",  # BAIXO GUANDU
    "05275": "56170",  # BARRA DE SAO FRANCISCO
    "05291": "56197",  # BOA ESPERANCA
    "05313": "56219",  # BOM JESUS DO NORTE
    "05329": "07587",  # BREJETUBA
    "05354": "56235",  # CACHOEIRO DE ITAPEMIRIM
    "05370": "56251",  # CARIACICA
    "05397": "56278",  # CASTELO
    "05416": "56294",  # COLATINA
    "05432": "56316",  # CONCEICAO DA BARRA
    "05459": "56332",  # CONCEICAO DO CASTELO
    "05475": "56359",  # DIVINO DE SAO LOURENCO
    "05491": "56375",  # DOMINGOS MARTINS
    "05513": "56391",  # DORES DO RIO PRETO
    "05530": "56413",  # ECOPORANGA
    "05554": "56430",  # FUNDAO
    "05562": "11142",  # GOVERNADOR LINDENBERG
    "05589": "56456",  # GUACUI
    "05605": "56472",  # GUARAPARI
    "05621": "57096",  # IBATIBA
    "05648": "56499",  # IBIRACU
    "05664": "60119",  # IBITIRAMA
    "05680": "56510",  # ICONHA
    "05699": "29319",  # IRUPI
    "05712": "56537",  # ITAGUACU
    "05739": "56553",  # ITAPEMIRIM
    "05755": "56570",  # ITARANA
    "05779": "56596",  # IUNA
    "05807": "57134",  # JAGUARE
    "05823": "56618",  # JERONIMO MONTEIRO
    "05859": "57215",  # JOAO NEIVA
    "05875": "57231",  # LARANJA DA TERRA
    "05908": "56634",  # LINHARES
    "05924": "56650",  # MANTENOPOLIS
    "05932": "07609",  # MARATAIZES
    "05940": "29297",  # MARECHAL FLORIANO
    "05958": "57070",  # MARILANDIA
    "05974": "56677",  # MIMOSO DO SUL
    "05990": "56693",  # MONTANHA
    "06013": "56715",  # MUCURICI
    "06030": "56731",  # MUNIZ FREIRE
    "06056": "56758",  # MUQUI
    "06072": "56774",  # NOVA VENECIA
    "06098": "56790",  # PANCAS
    "06110": "57150",  # PEDRO CANARIO
    "06137": "56812",  # PINHEIROS
    "06153": "56839",  # PIUMA
    "06170": "07625",  # PONTO BELO
    "06196": "56855",  # PRESIDENTE KENNEDY
    "06218": "57118",  # RIO BANANAL
    "06234": "56871",  # RIO NOVO DO SUL
    "06251": "56898",  # SANTA LEOPOLDINA
    "06269": "57258",  # SANTA MARIA DE JETIBA
    "06285": "56910",  # SANTA TERESA
    "06307": "29335",  # SAO DOMINGOS DO NORTE
    "06323": "56936",  # SAO GABRIEL DA PALHA
    "06340": "56952",  # SAO JOSE DO CALCADO
    "06366": "56979",  # SAO MATEUS
    "06382": "07641",  # SAO ROQUE DO CANAA
    "06405": "56995",  # SERRA
    "06421": "07668",  # SOORETAMA
    "06438": "57274",  # VARGEM ALTA
    "06455": "57290",  # VENDA NOVA DO IMIGRANTE
    "06471": "57010",  # VIANA
    "06499": "29351",  # VILA PAVAO
    "06505": "07684",  # VILA VALERIO
    "06529": "57037",  # VILA VELHA
    "06545": "57053",  # VITORIA
}
_DUA_MUN_DEFAULT = "57053"  # Vitória-ES (fallback)


def _digits(s: str | None) -> str:
    if not s:
        return ""
    return "".join(c for c in s if c.isdigit())


def _municipio_5dig(codigo: str | None) -> str:
    """5 dígitos sem prefixo de UF (ES DUA usa código IBGE sem os 2 primeiros)."""
    if not codigo:
        return ""
    d = _digits(codigo)
    return d[-5:] if len(d) >= 5 else d


def _build_emis_dua_xml(
    dados: NFeDados,
    valor: Decimal,
    data_pag: date,
) -> str:
    """
    Monta emisDua v1.01 conforme leiaute do Manual DUA-e v1.01b, seção 4.1.2.

    Campos obrigatórios:
      tpAmb     1=prod, 2=homolog
      cnpjEmi   CNPJ do emissor (emitente da NF-e)
      cnpjOrg   CNPJ da SEFAZ/ES (fixo: 27080571000130)
      cArea     5 (ICMS)
      cServ     3867 (DIFAL EC 87)
      cnpjPes   CPF/CNPJ do contribuinte (emitente da NF-e que paga o DIFAL)
      dRef      Mês de referência AAAA-MM
      dVen      Data de vencimento AAAA-MM-DD
      dPag      Data de pagamento AAAA-MM-DD
      cMun      Código do município (5 dígitos IBGE sem prefixo de UF)
      xInf      Informações complementares (max 256) — NF-e nº + chave
      vRec      Valor da receita
      qtde      Quantidade (fixo: 1)
      xIde      Identificação do solicitante (max 30) — NF-e nº
    """
    emit = dados.emitente
    dest = dados.destinatario
    cnpj_emit = _digits(emit.cnpj)
    n_nf = dados.n_nf or ""
    serie = dados.serie or ""
    chave = _digits(dados.chave_nfe or "")

    # cMun: usa código DUA-ES do município do DESTINO (ES) se disponível
    # O ES DUA usa códigos próprios (5xxxx) — mapeados de IBGE 5-dígitos
    dest_mun_ibge5 = _municipio_5dig(dest.municipio_codigo)
    mun_cod = _MUN_IBGE_TO_DUA.get(dest_mun_ibge5, _DUA_MUN_DEFAULT)

    from datetime import datetime
    try:
        emi_dt = datetime.fromisoformat(dados.dh_emi[:19]) if dados.dh_emi else datetime.now()
    except Exception:
        emi_dt = datetime.now()
    d_ref = emi_dt.strftime("%Y-%m")

    # xIde: identificação curta (30 chars)
    x_ide = f"NF-e {n_nf}/{serie}"[:30] if n_nf else f"DIFAL ES {data_pag.strftime('%m/%Y')}"[:30]

    # xInf: informação complementar completa (256 chars)
    if chave:
        x_inf = f"NF-e nº {n_nf} s.{serie} | Chave: {chave}"[:256]
    else:
        x_inf = f"ICMS DIFAL EC 87/2015 - NF-e nº {n_nf}"[:256]

    root = etree.Element(
        "emisDua",
        versao="1.01",
        xmlns=_NS_DUAE,
    )

    def el(tag: str, text: str) -> None:
        node = etree.SubElement(root, tag)
        node.text = text

    el("tpAmb",   _TP_AMB)
    el("cnpjEmi", cnpj_emit)
    el("cnpjOrg", _CNPJ_ORG_SEFAZ_ES)
    el("cArea",   _C_AREA_ICMS)
    el("cServ",   _C_SERV_DIFAL)
    el("cnpjPes", cnpj_emit)   # emitente é o contribuinte que paga o DIFAL
    el("dRef",    d_ref)
    el("dVen",    data_pag.strftime("%Y-%m-%d"))
    el("dPag",    data_pag.strftime("%Y-%m-%d"))
    if mun_cod:
        el("cMun", mun_cod)
    el("xInf",  x_inf)
    el("vRec",  f"{valor:.2f}")
    el("qtde",  "1")
    el("xIde",  x_ide)

    return etree.tostring(
        root, pretty_print=False, xml_declaration=True, encoding="UTF-8"
    ).decode()


def _build_soap(emisDua_xml: str) -> bytes:
    """
    SOAP 1.2 para duaEmissao conforme exemplo da seção 4.1.2 do manual.
    Header: duaServiceHeader > versao = "1.01"
    Body:   duaEmissao > duaDadosMsg = XML emisDua
    """
    S = _NS_SOAP12
    D = _NS_DUAE

    env = etree.Element(f"{{{S}}}Envelope", nsmap={"soap": S, "duae": D})

    hdr = etree.SubElement(env, f"{{{S}}}Header")
    svc_hdr = etree.SubElement(hdr, f"{{{D}}}DuaServiceHeader")
    etree.SubElement(svc_hdr, f"{{{D}}}versao").text = "1.01"

    body = etree.SubElement(env, f"{{{S}}}Body")
    emissao = etree.SubElement(body, f"{{{D}}}duaEmissao")
    dados_msg = etree.SubElement(emissao, f"{{{D}}}duaDadosMsg")
    dados_msg.append(etree.fromstring(emisDua_xml.encode()))

    return etree.tostring(env, xml_declaration=True, encoding="UTF-8")


def _parse_response(soap_text: str) -> dict:
    """
    Parse da resposta duaEmissaoResponse.
    cStat=105 = DUA emitido com sucesso (seção 4.1.8 e Anexo II).
    """
    try:
        root = etree.fromstring(soap_text.encode())
    except Exception as exc:
        raise RuntimeError(f"Resposta SOAP inválida do DUA-ES: {soap_text[:300]}") from exc

    def find(tag: str) -> str:
        for ns in (_NS_DUAE, ""):
            n = root.find(f".//{{{ns}}}{tag}") if ns else root.find(f".//{tag}")
            if n is not None and n.text:
                return n.text.strip()
        return ""

    c_stat = find("cStat")
    x_motivo = find("xMotivo")
    n_bar = find("nBar")
    n_dua = find("nDua")
    x_pix = find("xPix")
    v_tot = find("vTot")

    logger.info("DUA-ES cStat=%s xMotivo=%s nDua=%s nBar=%s", c_stat, x_motivo, n_dua, n_bar)

    if c_stat == "105":
        return {
            "status": "gerada",
            "codigo_barras": n_bar or None,
            "linha_digitavel": None,   # DUA ES usa código de barras CODE-128, não linha digitável
            "qrcode_pix": x_pix or None,
            "mensagem": f"DUA-ES nº {n_dua} emitido. Valor: R$ {v_tot}",
            "numero_dua": n_dua,
        }

    return {
        "status": "erro",
        "mensagem": f"DUA-ES rejeitado. cStat={c_stat}: {x_motivo}",
    }


def gerar_dua_es(
    dados: NFeDados,
    receita: str,
    descricao: str,
    valor: Decimal,
    data_pag: date | None = None,
    cert_paths: tuple[str, str] | None = None,
) -> GuiaGerada:
    """
    Gera guia DUA-e ES via WebService síncrono.
    Usa o mesmo certificado A1 ICP-Brasil do GNRE.
    """
    from datetime import date as _date
    data_pag = data_pag or _date.today()

    xml_str = _build_emis_dua_xml(dados, valor, data_pag)

    # Resolve certificado (mesmo do GNRE)
    effective_cert = cert_paths
    if not effective_cert and _CERT and _KEY:
        import os as _os
        if _os.path.isfile(_CERT) and _os.path.isfile(_KEY):
            effective_cert = (_CERT, _KEY)

    if not effective_cert:
        return GuiaGerada(
            tipo="DUA-ES",
            uf="ES",
            receita_codigo=_C_SERV_DIFAL,
            receita_descricao=descricao,
            valor=valor,
            data_vencimento=data_pag.strftime("%d/%m/%Y"),
            gnre_xml=xml_str,
            status="pendente_webservice",
            mensagem="Cadastre o certificado A1 da empresa para envio automático ao WebService DUA-ES.",
        )

    soap_bytes = _build_soap(xml_str)

    try:
        resp = requests.post(
            _WS_URL,
            data=soap_bytes,
            headers={
                "Content-Type": 'application/soap+xml;charset=utf-8;action="duaEmissao"',
                "SOAPAction": '"duaEmissao"',
            },
            cert=effective_cert,
            timeout=30,
            verify=True,
        )
        resp.raise_for_status()
        result = _parse_response(resp.text)
    except requests.exceptions.SSLError as exc:
        result = {"status": "erro", "mensagem": f"Erro SSL DUA-ES: {exc}"}
    except requests.exceptions.ConnectionError as exc:
        result = {"status": "erro", "mensagem": f"Sem conexão com WebService DUA-ES ({_WS_URL}): {exc}"}
    except requests.exceptions.HTTPError as exc:
        body = resp.text[:400] if resp.text else "(vazio)"
        result = {"status": "erro", "mensagem": f"HTTP {resp.status_code} DUA-ES: {body}"}
    except RuntimeError as exc:
        result = {"status": "erro", "mensagem": str(exc)}

    return GuiaGerada(
        tipo="DUA-ES",
        uf="ES",
        receita_codigo=_C_SERV_DIFAL,
        receita_descricao=descricao,
        valor=valor,
        data_vencimento=data_pag.strftime("%d/%m/%Y"),
        codigo_barras=result.get("codigo_barras"),
        linha_digitavel=result.get("linha_digitavel"),
        gnre_xml=xml_str,
        status=result.get("status", "erro"),
        mensagem=result.get("mensagem"),
    )
