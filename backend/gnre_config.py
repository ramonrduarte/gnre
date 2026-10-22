"""
Regras de preenchimento por UF/receita via WebService GnreConfigUF (Manual v2.17, seção 4.3).

Cada UF define quais campos a guia exige (documento de origem, referência, campos
extras, detalhamento, produto, FCP no mesmo guia...). Essas regras mudam com o tempo
e são diferentes entre homologação e produção, por isso são consultadas no próprio
portal GNRE e guardadas em cache (data/gnre_config_cache.json, validade 24h).
"""
import json
import logging
import time
from pathlib import Path

from lxml import etree

logger = logging.getLogger(__name__)

_G = "{http://www.gnre.pe.gov.br}"
_WS_NS = "http://www.gnre.pe.gov.br/webservice/GnreConfigUF"
_SOAP12_NS = "http://www.w3.org/2003/05/soap-envelope"
_CACHE_FILE = Path(__file__).parent.parent / "data" / "gnre_config_cache.json"
_CACHE_TTL = 24 * 3600

_memoria: dict[str, dict] = {}


def _url() -> str:
    from gnre_service import _TP_AMB
    host = "https://www.gnre.pe.gov.br" if _TP_AMB == "1" else "https://www.testegnre.pe.gov.br"
    return f"{host}/gnreWS/services/GnreConfigUF"


def _soap(uf: str, receita: str, ambiente: str) -> bytes:
    S, W = _SOAP12_NS, _WS_NS
    env = etree.Element(f"{{{S}}}Envelope", nsmap={"soap12": S})
    cab = etree.SubElement(etree.SubElement(env, f"{{{S}}}Header"), f"{{{W}}}gnreCabecMsg", nsmap={None: W})
    etree.SubElement(cab, f"{{{W}}}versaoDados").text = "1.00"
    msg = etree.SubElement(etree.SubElement(env, f"{{{S}}}Body"), f"{{{W}}}gnreDadosMsg", nsmap={None: W})
    cons = etree.SubElement(msg, f"{_G}TConsultaConfigUf", nsmap={None: _G[1:-1]})
    etree.SubElement(cons, f"{_G}ambiente").text = ambiente
    etree.SubElement(cons, f"{_G}uf").text = uf
    etree.SubElement(cons, f"{_G}receita", courier="N").text = receita
    return etree.tostring(env, xml_declaration=True, encoding="UTF-8")


def _txt(el: etree._Element | None, tag: str, default: str = "") -> str:
    if el is None:
        return default
    n = el.find(_G + tag)
    return (n.text or "").strip() if n is not None and n.text else default


def _lista(rec: etree._Element, tag: str) -> list[dict]:
    return [
        {"codigo": _txt(x, "codigo"), "descricao": _txt(x, "descricao")}
        for x in rec.iter(_G + tag)
        if x.find(_G + "codigo") is not None
    ]


def _parse(xml: str) -> dict:
    root = etree.fromstring(xml.encode())
    tc = root.find(f".//{_G}TConfigUf")
    if tc is None:
        raise RuntimeError(f"Resposta inesperada do GnreConfigUF: {xml[:300]}")
    sit = tc.find(_G + "situacaoConsulta")
    codigo = _txt(sit, "codigo")
    rec = tc.find(f".//{_G}receita")
    if codigo != "450" or rec is None:
        # 999 = UF não habilitada / receita não relacionada à UF
        return {"existe": False, "codigo": codigo, "descricao": _txt(sit, "descricao")}

    campos = []
    for c in rec.iter(_G + "campoAdicional"):
        tam = _txt(c, "tamanho")
        campos.append({
            "codigo": _txt(c, "codigo"),
            "tipo": _txt(c, "tipo"),            # T=texto, N=numérico, D=data
            "tamanho": int(tam) if tam.isdigit() else None,
            "titulo": _txt(c, "titulo"),
            "obrigatorio": _txt(c, "obrigatorio") == "S",
        })

    return {
        "existe": True,
        "descricao_receita": rec.get("descricao", ""),
        "exige_detalhamento": _txt(rec, "exigeDetalhamentoReceita") == "S",
        "detalhamentos": _lista(rec, "detalhamentoReceita"),
        "exige_produto": _txt(rec, "exigeProduto") == "S",
        "produtos": _lista(rec, "produto"),
        "exige_referencia": _txt(rec, "exigePeriodoReferencia") == "S",
        "exige_apuracao": _txt(rec, "exigePeriodoApuracao") == "S",
        "apuracoes": [a["codigo"] for a in _lista(rec, "periodoApuracao")],
        "exige_parcela": _txt(rec, "exigeParcela") == "S",
        "valor_exigido": _txt(rec, "valorExigido", "P"),  # P, T, A, N, PO, TO, AO
        "exige_doc_origem": _txt(rec, "exigeDocumentoOrigem") == "S",
        "tipos_doc_origem": [t["codigo"] for t in _lista(rec, "tipoDocumentoOrigem")],
        "exige_destinatario": _txt(rec, "exigeContribuinteDestinatario") == "S",
        "exige_vencimento": _txt(rec, "exigeDataVencimento", "S") == "S",
        "convenio": _txt(rec, "exigeConvenio", "N"),     # N=não exigido, S=opcional, O=obrigatório
        "exige_valor_fecp": _txt(rec, "exigeValorFecp") == "S",
        "campos_extras": campos,
        "aceita_pix": _txt(rec, "aceitaPgPix") == "S",
    }


def _carregar_cache() -> dict:
    if not _memoria and _CACHE_FILE.exists():
        try:
            _memoria.update(json.loads(_CACHE_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return _memoria


def _salvar_cache() -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(_memoria, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("Não foi possível salvar cache GnreConfigUF: %s", exc)


def obter_config(uf: str, receita: str, cert: tuple[str, str] | None, forcar: bool = False) -> dict | None:
    """
    Regras da receita na UF para o ambiente configurado (GNRE_AMBIENTE).
    Retorna None quando não é possível consultar (sem certificado ou portal fora do ar);
    nesse caso o chamador usa a configuração estática de uf_config.py.
    """
    from gnre_service import _TP_AMB, _post_soap

    chave = f"{_TP_AMB}-{uf}-{receita}"
    cache = _carregar_cache()
    item = cache.get(chave)
    if item and not forcar and time.time() - item["ts"] < _CACHE_TTL:
        return item["cfg"]
    if not cert:
        return item["cfg"] if item else None

    try:
        resp = _post_soap(_url(), _soap(uf, receita, _TP_AMB), cert, soap_action=f"{_WS_NS}/consultar")
        cfg = _parse(resp)
    except Exception as exc:
        logger.warning("GnreConfigUF %s/%s indisponível: %s", uf, receita, exc)
        return item["cfg"] if item else None

    cache[chave] = {"ts": time.time(), "cfg": cfg}
    _salvar_cache()
    return cfg
