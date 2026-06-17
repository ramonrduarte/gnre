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

        modal_msg = ""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                ctx = await browser.new_context()
                page = await ctx.new_page()
                page.set_default_timeout(_TIMEOUT_MS)

                await page.goto(_PORTAL_URL_GNRE_LOTE)

                # Upload do arquivo XML
                file_input = page.locator("input[type='file']")
                await file_input.set_input_files(xml_path)

                # Aguarda o modal de feedback aparecer (SP valida antes de habilitar botão)
                try:
                    await page.wait_for_selector(
                        "#merro.show, #msucesso.show, #btnProcessarLote:not([disabled])",
                        timeout=15000,
                    )
                except Exception:
                    pass  # continua mesmo se timeout

                # Lê e fecha modal de erro (#merro), se aberto
                error_modal = page.locator("#merro.show, .modal.show[id*='erro']")
                if await error_modal.count():
                    modal_msg = (await error_modal.text_content() or "").strip()
                    # Fecha o modal
                    close_btn = error_modal.locator("button.close, .btn-close, button:has-text('Fechar'), button:has-text('×'), button:has-text('OK')")
                    if await close_btn.count():
                        await close_btn.first.click()
                    await page.wait_for_timeout(500)

                # Verifica se o botão foi habilitado após fechar o modal
                btn = page.locator("#btnProcessarLote, button:has-text('Processar'), input[value*='Processar']").first
                btn_disabled = await btn.get_attribute("disabled")
                if btn_disabled is not None:
                    # Botão ainda desabilitado — não prosseguir
                    await browser.close()
                    return [_dare_fallback(valor_difal, data_pag, gnre_xml,
                        f"Portal SP: upload aceito mas processamento bloqueado. {modal_msg}".strip())]

                await btn.click()
                await page.wait_for_load_state("networkidle", timeout=_TIMEOUT_MS)

                barcode = ""
                linha = ""
                pdf_b64 = ""

                # Captura código de barras / linha digitável
                for sel in ["[id*='codigoBarra']", "[id*='barcode']", "[class*='barcode']", "[id*='codigo']"]:
                    el = page.locator(sel).first
                    if await el.count():
                        txt = (await el.text_content() or "").strip()
                        if txt:
                            barcode = txt
                            break

                for sel in ["[id*='linhaDigitavel']", "[id*='linha']"]:
                    el = page.locator(sel).first
                    if await el.count():
                        txt = (await el.text_content() or "").strip()
                        if txt:
                            linha = txt
                            break

                # Tenta baixar PDF
                pdf_link = page.locator(
                    "a[href*='.pdf'], a:has-text('PDF'), a:has-text('Imprimir'), "
                    "button:has-text('Imprimir'), a:has-text('Download')"
                ).first
                if await pdf_link.count():
                    try:
                        async with page.expect_download() as dl_info:
                            await pdf_link.click()
                        dl = await dl_info.value
                        dl_path = await dl.path()
                        if dl_path:
                            pdf_bytes = Path(dl_path).read_bytes()
                            if pdf_bytes:
                                pdf_b64 = base64.b64encode(pdf_bytes).decode()
                    except Exception:
                        pass

                # Fallback: screenshot se não encontrou barcode
                if not pdf_b64 and not barcode:
                    screenshot = await page.screenshot(type="png", full_page=True)
                    pdf_b64 = base64.b64encode(screenshot).decode()

                await browser.close()

        finally:
            Path(xml_path).unlink(missing_ok=True)

        status = "gerada" if (barcode or linha or pdf_b64) else "pendente_webservice"
        mensagem = (
            None if status == "gerada"
            else (
                f"DARE gerado mas não foi possível extrair o código de barras. "
                f"Acesse {_PORTAL_URL_GNRE_LOTE} e faça upload do XML manualmente."
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
