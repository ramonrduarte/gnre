"""
SP DARE-ICMS automation via Playwright.

São Paulo does not use the national GNRE system for DIFAL.
Instead it uses the DARE-ICMS portal: https://www4.fazenda.sp.gov.br/DareICMS

This module fills the form, submits it and returns the generated guide data
(barcode / PDF). The selectors below are based on the SP DARE-ICMS form
structure — update them if the portal changes its layout.
"""
import os
import base64
from datetime import date
from decimal import Decimal

from models import NFeDados, GuiaGerada

_PORTAL_URL = "https://www4.fazenda.sp.gov.br/DareICMS"
_TIMEOUT_MS = int(os.getenv("SP_PLAYWRIGHT_TIMEOUT", "30")) * 1000
_DARE_CODIGO_DIFAL = os.getenv("SP_DARE_CODIGO_DIFAL", "064-2")


async def gerar_dare_sp(
    dados: NFeDados,
    valor_difal: Decimal,
    valor_fecp: Decimal,
    data_pag: date | None = None,
) -> list[GuiaGerada]:
    """
    Fill the SP DARE-ICMS portal form and return the generated guide(s).
    Requires `playwright install chromium` to have been run once.
    Returns one guide for DIFAL (SP has no separate FCP — fecp=0).
    """
    from datetime import date as _date
    data_pag = data_pag or _date.today()

    emit = dados.emitente
    doc_id = emit.cnpj  # always CNPJ for B2B; CPF for PF
    valor_total = valor_difal  # SP FECP = 0

    # Reference info for the form
    referencia = dados.chave_nfe or dados.n_nf or "S/N"
    periodo = ""
    if dados.dh_emi:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(dados.dh_emi[:19])
            periodo = dt.strftime("%m/%Y")
        except Exception:
            periodo = date.today().strftime("%m/%Y")

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context()
            page = await ctx.new_page()
            page.set_default_timeout(_TIMEOUT_MS)

            await page.goto(_PORTAL_URL)

            # ── Fill DARE form ────────────────────────────────────────────────
            # The SP DARE-ICMS form fields — selectors may need adjustment
            # if the portal updates its HTML structure.

            # CNPJ/CPF field
            cnpj_input = page.locator("input[name*='cnpj'], input[id*='cnpj'], input[name*='CNPJ']").first
            await cnpj_input.fill(_only_digits(doc_id))

            # IE (Inscrição Estadual)
            ie_val = emit.ie or "ISENTO"
            ie_input = page.locator("input[name*='ie'], input[id*='ie']").first
            if await ie_input.count():
                await ie_input.fill(ie_val)

            # Código de receita
            cod_input = page.locator("input[name*='codigo'], input[id*='codigo'], input[name*='receita']").first
            await cod_input.fill(_DARE_CODIGO_DIFAL.replace("-", ""))

            # Data de vencimento
            venc_input = page.locator("input[name*='vencimento'], input[id*='vencimento'], input[name*='dataVenc']").first
            await venc_input.fill(data_pag.strftime("%d/%m/%Y"))

            # Período de referência
            periodo_input = page.locator("input[name*='periodo'], input[id*='periodo'], input[name*='periodo']").first
            if await periodo_input.count() and periodo:
                await periodo_input.fill(periodo)

            # Número do documento (NF-e key or NF number)
            nro_input = page.locator("input[name*='documento'], input[id*='documento'], input[name*='nroDoc']").first
            if await nro_input.count():
                await nro_input.fill(referencia[:44])

            # Valor principal
            valor_input = page.locator("input[name*='valor'], input[id*='valor'], input[name*='valorPrincipal']").first
            await valor_input.fill(f"{valor_total:.2f}".replace(".", ","))

            # Submit
            submit_btn = page.locator("button[type='submit'], input[type='submit'], button:has-text('Gerar'), button:has-text('Emitir')").first
            await submit_btn.click()

            # ── Capture result ────────────────────────────────────────────────
            await page.wait_for_load_state("networkidle", timeout=_TIMEOUT_MS)

            # Try to get barcode or linha digitável from result page
            barcode = ""
            linha = ""
            pdf_b64 = ""

            barcode_el = page.locator("[id*='codigoBarra'], [id*='barcode'], [class*='barcode']").first
            if await barcode_el.count():
                barcode = (await barcode_el.text_content() or "").strip()

            linha_el = page.locator("[id*='linhaDigitavel'], [id*='linha']").first
            if await linha_el.count():
                linha = (await linha_el.text_content() or "").strip()

            # Try to get PDF link and download
            pdf_link = page.locator("a[href*='.pdf'], a:has-text('PDF'), a:has-text('Imprimir')").first
            if await pdf_link.count():
                async with page.expect_download() as dl_info:
                    await pdf_link.click()
                download = await dl_info.value
                pdf_bytes = await (await download.path()).read_bytes() if download else b""
                if pdf_bytes:
                    pdf_b64 = base64.b64encode(pdf_bytes).decode()

            # Fallback: take a screenshot of the result as PDF
            if not pdf_b64 and not barcode:
                screenshot = await page.screenshot(type="png", full_page=True)
                pdf_b64 = base64.b64encode(screenshot).decode()

            await browser.close()

        status = "gerada" if (barcode or linha or pdf_b64) else "pendente_webservice"
        mensagem = None if status == "gerada" else (
            "DARE gerado mas não foi possível extrair o código de barras automaticamente. "
            "Verifique a tela do portal."
        )

        return [GuiaGerada(
            tipo="DARE-SP",
            uf="SP",
            receita_codigo=_DARE_CODIGO_DIFAL,
            receita_descricao="ICMS Diferencial de Alíquota – SP",
            valor=valor_total,
            data_vencimento=data_pag.strftime("%d/%m/%Y"),
            codigo_barras=barcode or None,
            linha_digitavel=linha or None,
            pdf_base64=pdf_b64 or None,
            status=status,
            mensagem=mensagem,
        )]

    except ImportError:
        return [_dare_sem_playwright(dados, valor_total, data_pag)]
    except Exception as exc:
        return [GuiaGerada(
            tipo="DARE-SP",
            uf="SP",
            receita_codigo=_DARE_CODIGO_DIFAL,
            receita_descricao="ICMS Diferencial de Alíquota – SP",
            valor=valor_total,
            data_vencimento=data_pag.strftime("%d/%m/%Y"),
            status="erro",
            mensagem=f"Falha na automação do portal SP: {exc}. Acesse {_PORTAL_URL} manualmente.",
        )]


def _dare_sem_playwright(dados: NFeDados, valor: Decimal, data_pag: date) -> GuiaGerada:
    """Fallback when Playwright is not installed."""
    return GuiaGerada(
        tipo="DARE-SP",
        uf="SP",
        receita_codigo=_DARE_CODIGO_DIFAL,
        receita_descricao="ICMS Diferencial de Alíquota – SP",
        valor=valor,
        data_vencimento=data_pag.strftime("%d/%m/%Y"),
        status="pendente_webservice",
        mensagem=(
            f"Playwright não instalado. Execute: playwright install chromium\n"
            f"Ou acesse manualmente: {_PORTAL_URL}\n"
            f"Código receita: {_DARE_CODIGO_DIFAL} | Valor: R$ {valor:.2f}"
        ),
    )


def _only_digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())
