"""
SP DARE-ICMS via upload de XML-GNRE (portal fazenda.sp.gov.br/DareICMS/GnreLote).

São Paulo não usa o GNRE nacional, mas aceita o mesmo formato de XML GNRE
no portal DARE-ICMS, convertendo automaticamente os códigos:
  GNRE 100102 (10010-2) → DARE-SP 101-6  (DIFAL EC 87/2015)
  GNRE 100110 (10011-0) → DARE-SP 102-8  (DIFAL por apuração)

Fluxo:
  1. Gera XML no formato TLote_GNRE (mesmo do GNRE nacional com ufFavorecida=SP)
  2. Playwright: navega até /DareICMS/GnreLote, faz upload do arquivo, clica Processar Lote
  3. Extrai barcode/linha digitável/PDF do resultado

Fallback: retorna XML para upload manual se Playwright falhar.
"""
import os
import base64
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from models import NFeDados, GuiaGerada

_PORTAL_URL_GNRE_LOTE = "https://www4.fazenda.sp.gov.br/DareICMS/GnreLote"
_TIMEOUT_MS = int(os.getenv("SP_PLAYWRIGHT_TIMEOUT", "60")) * 1000
_GNRE_RECEITA_DIFAL_SP = "100102"  # 10010-2 → DARE-SP 101-6


def _build_gnre_xml_sp(
    dados: NFeDados,
    valor_difal: Decimal,
    data_pag: date,
) -> str:
    """
    Reutiliza o builder do GNRE nacional com ufFavorecida=SP.
    Formato plano (flat) idêntico ao aceito pelo portal SP GnreLote.
    """
    from gnre_service import _build_gnre_xml

    dados_sp = dados.model_copy(update={"uf_dest": "SP"})
    return _build_gnre_xml(dados_sp, _GNRE_RECEITA_DIFAL_SP, valor_difal, data_pag)


async def gerar_dare_sp(
    dados: NFeDados,
    valor_difal: Decimal,
    _valor_fecp: Decimal,  # SP não tem FCP separado no DIFAL EC 87/2015
    data_pag: date | None = None,
) -> list[GuiaGerada]:
    """
    Gera DARE-SP via upload de XML no portal GnreLote.
    Playwright necessário: playwright install chromium
    """
    from datetime import date as _date
    data_pag = data_pag or _date.today()

    gnre_xml = _build_gnre_xml_sp(dados, valor_difal, data_pag)

    try:
        from playwright.async_api import async_playwright

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".xml", encoding="utf-8", delete=False
        ) as tmp:
            tmp.write(gnre_xml)
            xml_path = tmp.name

        zip_bytes = b""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                page.set_default_timeout(_TIMEOUT_MS)

                await page.goto(_PORTAL_URL_GNRE_LOTE)

                # 1) Upload do XML
                await page.locator("input[type='file']").set_input_files(xml_path)
                await page.wait_for_timeout(2000)

                # 2) Clica "Processar Lote"
                btn_proc = page.locator(
                    "#btnProcessarLote, button:has-text('Processar Lote')"
                ).first
                if await btn_proc.get_attribute("disabled") is not None:
                    await browser.close()
                    return [_dare_fallback(valor_difal, data_pag, gnre_xml,
                        "Portal SP: botão 'Processar Lote' desabilitado após upload.")]

                await btn_proc.click()

                # Aguarda até 20s o texto de sucesso ou erro aparecer
                try:
                    await page.wait_for_selector(
                        "text=processado com sucesso, text=processado com erros",
                        timeout=20000,
                    )
                except Exception:
                    pass

                page_text = await page.inner_text("body")

                if "processado com erros" in page_text.lower():
                    # Lê a linha de erro da tabela de resultados
                    erro_detalhe = ""
                    rows = page.locator("table tr")
                    for i in range(await rows.count()):
                        row_txt = (await rows.nth(i).inner_text() or "").strip()
                        if row_txt and row_txt != "Guia\tCampos com erro\tSituação":
                            erro_detalhe = row_txt
                            break
                    await browser.close()
                    return [_dare_fallback(valor_difal, data_pag, gnre_xml,
                        f"Portal SP processou com erro: {erro_detalhe}")]

                if "processado com sucesso" not in page_text.lower():
                    await browser.close()
                    return [_dare_fallback(valor_difal, data_pag, gnre_xml,
                        f"Portal SP: resposta inesperada — {page_text[:200]}")]

                # 3) Clica "Gerar DAREs" e baixa o ZIP
                gerar_btn = page.locator(
                    "button:has-text('Gerar DAREs'), #btnGerarDares"
                ).first
                if not await gerar_btn.count():
                    await browser.close()
                    return [_dare_fallback(valor_difal, data_pag, gnre_xml,
                        "Portal SP: botão 'Gerar DAREs' não encontrado.")]

                async with page.expect_download(timeout=30000) as dl_info:
                    await gerar_btn.click()

                dl = await dl_info.value
                dl_path = await dl.path()
                if dl_path:
                    zip_bytes = Path(dl_path).read_bytes()

                await browser.close()

        finally:
            Path(xml_path).unlink(missing_ok=True)

        # Extrai o PDF do ZIP e o código de barras do texto do PDF
        barcode, linha, pdf_b64, numero_dare = _extrair_dare_do_zip(zip_bytes)

        status = "gerada" if (pdf_b64 or barcode) else "pendente_webservice"
        mensagem = (
            f"DARE-SP nº {numero_dare} gerado. Valor: R$ {valor_difal:.2f}"
            if status == "gerada" and numero_dare
            else (
                None if status == "gerada"
                else f"ZIP não obtido. Acesse {_PORTAL_URL_GNRE_LOTE} e faça upload do XML."
            )
        )

        return [GuiaGerada(
            tipo="DARE-SP",
            uf="SP",
            receita_codigo=_GNRE_RECEITA_DIFAL_SP,
            receita_descricao="ICMS Diferencial de Alíquota – SP (DARE-ICMS)",
            valor=valor_difal,
            data_vencimento=data_pag.strftime("%d/%m/%Y"),
            codigo_barras=barcode or None,
            linha_digitavel=linha or None,
            pdf_base64=pdf_b64 or None,
            gnre_xml=gnre_xml,
            status=status,
            mensagem=mensagem,
        )]

    except ImportError:
        return [_dare_fallback(valor_difal, data_pag, gnre_xml,
            f"Playwright não instalado. Execute: playwright install chromium\n"
            f"Ou acesse {_PORTAL_URL_GNRE_LOTE} e faça upload do XML.")]
    except Exception as exc:
        return [_dare_fallback(valor_difal, data_pag, gnre_xml,
            f"Falha na automação: {exc}\nAcesse {_PORTAL_URL_GNRE_LOTE} manualmente.")]


def _extrair_dare_do_zip(zip_bytes: bytes) -> tuple[str, str, str, str]:
    """
    Extrai o PDF do DARE-SP do arquivo ZIP retornado pelo portal.
    Retorna (barcode, linha_digitavel, pdf_base64, numero_dare).

    O PDF do SP contém a linha digitável no formato:
        85860000000-4 50000185112-0 60590131312-1 40520260630-5
    e o número do DARE no campo "09 - Número do DARE".
    """
    import zipfile, re, io

    if not zip_bytes:
        return "", "", "", ""

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            # Pega o primeiro PDF do ZIP
            pdfs = [n for n in z.namelist() if n.lower().endswith(".pdf")]
            if not pdfs:
                return "", "", "", ""
            pdf_bytes = z.read(pdfs[0])
    except Exception:
        return "", "", "", ""

    pdf_b64 = base64.b64encode(pdf_bytes).decode()
    barcode = ""
    linha = ""
    numero_dare = ""

    try:
        import pdfplumber, io as _io
        with pdfplumber.open(_io.BytesIO(pdf_bytes)) as pdf:
            full_text = "\n".join(
                page.extract_text() or "" for page in pdf.pages
            )

            # Linha digitável: 4 grupos separados por espaço, no formato NNN...N-D
            # Exemplo: 85860000000-4 50000185112-0 60590131312-1 40520260630-5
            m = re.search(
                r"(\d{8,14}-\d)\s+(\d{8,14}-\d)\s+(\d{8,14}-\d)\s+(\d{8,14}-\d)",
                full_text,
            )
            if m:
                linha = " ".join(m.groups())
                barcode = re.sub(r"[-\s]", "", linha)

            # Número do DARE
            m2 = re.search(r"09\s*-\s*N[úu]mero do DARE\s*\n?\s*(\d{10,20})", full_text)
            if m2:
                numero_dare = m2.group(1)

    except Exception:
        pass  # pdfplumber não instalado ou PDF ilegível — retorna só o b64

    return barcode, linha, pdf_b64, numero_dare


def _dare_fallback(
    valor: Decimal,
    data_pag: date,
    gnre_xml: str,
    mensagem: str,
) -> GuiaGerada:
    return GuiaGerada(
        tipo="DARE-SP",
        uf="SP",
        receita_codigo=_GNRE_RECEITA_DIFAL_SP,
        receita_descricao="ICMS Diferencial de Alíquota – SP (DARE-ICMS)",
        valor=valor,
        data_vencimento=data_pag.strftime("%d/%m/%Y"),
        gnre_xml=gnre_xml,
        status="pendente_webservice",
        mensagem=mensagem,
    )
