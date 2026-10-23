"""
DIFAL/GNRE Generator — FastAPI backend.

Guide generation — three input methods:
  POST /api/gerar/xml    — upload NF-e XML file
  POST /api/gerar/json   — JSON body (FormularioManual schema)
  POST /api/gerar/manual — alias for /json

Company management:
  GET/POST /api/empresas
  POST     /api/empresas/{cnpj}/certificado
  DELETE   /api/empresas/{cnpj}/certificado
  GET      /api/empresas/{cnpj}/cert-status

Guide history:
  GET  /api/guias                         — guias emitidas (busca por NF, chave, destinatário)
  GET  /api/guias/{id}                    — guia completa (XML/PDF) para impressão
  POST /api/guias/{id}/atualizar          — reconsulta guia 'processando'
  POST /api/guias/atualizar-pendentes     — reconsulta todas as 'processando'

GNRE:
  GET /api/gnre/config/{uf}/{receita}   — regras da UF no portal GNRE (GnreConfigUF)
  GET /api/gnre/resultado/{recibo}      — reconsulta lote que ficou 'processando'

Utilities:
  GET /api/uf/{uf}   — state config (rates, codes)
  GET /api/ufs       — all states
  GET /api/status    — health check
"""
import asyncio
import os
from datetime import date, datetime, time
from pathlib import Path
from contextlib import asynccontextmanager

import logging

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from database import get_db, init_db
from auth import router as auth_router, autorizar, criar_admin_inicial, exige_acesso
from backup_router import router as backup_router
from empresa_router import router as empresa_router
from models import FormularioManual, ResultadoGuias
from nfe_parser import parse_nfe_xml
from difal_calculator import calcular_difal_manual
from guide_router import gerar_guias_completo
from uf_config import get_uf_config, UF_ALIQ_INTERNA, UF_FECP

logger = logging.getLogger(__name__)


async def _agendador_backup():
    """Verifica a cada 30s se é dia/horário de backup automático."""
    import backup_service

    while True:
        try:
            await asyncio.to_thread(backup_service.executar_agendado_se_for_hora)
        except Exception:
            logger.exception("Erro no agendador de backup")
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    criar_admin_inicial()
    tarefa = asyncio.create_task(_agendador_backup())
    yield
    tarefa.cancel()


app = FastAPI(
    title="DIFAL/GNRE Generator",
    description=(
        "Gera guias GNRE e DARE-SP para DIFAL a partir de NF-e XML, JSON ou formulário manual. "
        "Cadastre empresas com certificado A1 para envio automático ao WebService GNRE."
    ),
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def controle_de_acesso(request: Request, call_next):
    """Exige login (cookie) ou token de API em /api/* e na documentação; perfis admin/operador."""
    if exige_acesso(request.url.path, request.method):
        ident, erro = await asyncio.to_thread(autorizar, request)
        if erro:
            codigo, msg = erro.split(":", 1)
            if request.url.path in ("/docs", "/redoc", "/openapi.json"):
                return RedirectResponse("/")
            return JSONResponse({"detail": msg}, status_code=int(codigo))
        request.state.identidade = ident
    return await call_next(request)


app.include_router(auth_router)
app.include_router(backup_router)
app.include_router(empresa_router)

_frontend = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(_frontend)), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    return (_frontend / "index.html").read_text(encoding="utf-8")


# ── Utilities ──────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def status():
    return {
        "status": "ok",
        "versao": "1.1.0",
        "ambiente": "producao" if os.getenv("GNRE_AMBIENTE") == "1" else "homologacao",
        "certificado_global_configurado": bool(os.getenv("CERT_PATH") and os.getenv("KEY_PATH")),
    }


@app.get("/api/uf/{uf}")
async def get_uf_info(uf: str):
    uf = uf.upper()
    if uf not in UF_ALIQ_INTERNA:
        raise HTTPException(404, f"UF '{uf}' não encontrada.")
    return get_uf_config(uf)


@app.get("/api/ufs")
async def listar_ufs():
    return {
        uf: {"aliq_interna": UF_ALIQ_INTERNA[uf], "fecp": UF_FECP[uf]}
        for uf in sorted(UF_ALIQ_INTERNA)
    }


@app.get("/api/gnre/config/{uf}/{receita}")
async def gnre_config_uf(uf: str, receita: str = "100102", cnpj: str | None = None, db: Session = Depends(get_db)):
    """
    Regras de preenchimento da receita na UF, consultadas no portal GNRE (GnreConfigUF, cache 24h).
    Mostra detalhamentos, produtos e campos extras aceitos — úteis para os campos opcionais da API.
    cnpj: empresa cujo certificado será usado na consulta (padrão: primeira empresa com certificado).
    """
    from gnre_config import obter_config

    cfg = await asyncio.to_thread(obter_config, uf.upper(), receita, _cert_empresa(db, cnpj))
    if cfg is None:
        raise HTTPException(503, "Não foi possível consultar o GNRE (cadastre uma empresa com certificado A1).")
    return {"uf": uf.upper(), "receita": receita, "ambiente": os.getenv("GNRE_AMBIENTE", "2"), **cfg}


@app.get("/api/gnre/resultado/{numero_recibo}")
async def gnre_resultado(numero_recibo: str, cnpj: str | None = None, db: Session = Depends(get_db)):
    """
    Consulta novamente um lote GNRE já enviado — para guias que voltaram com status 'processando'
    (a UF pode levar até 1 hora). Use o numero_recibo devolvido na geração e o mesmo cnpj emitente.
    """
    from gnre_service import consultar_recibo

    from database import Guia
    from guia_store import aplicar_resultado

    cert = _cert_empresa(db, cnpj)
    if not cert:
        raise HTTPException(400, "Nenhuma empresa com certificado A1 encontrada para a consulta.")
    try:
        result = await asyncio.to_thread(consultar_recibo, numero_recibo, cert)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))
    # Mantém o histórico em dia (um recibo = um lote = uma guia)
    for g in db.query(Guia).filter(Guia.numero_recibo == numero_recibo).all():
        aplicar_resultado(db, g, result)
    return result


# ── Histórico de guias ─────────────────────────────────────────────────────────

@app.get("/api/guias")
async def listar_guias(
    busca: str | None = None,
    status: str | None = None,
    uf: str | None = None,
    de: date | None = None,
    ate: date | None = None,
    limite: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """
    Guias emitidas, mais recentes primeiro.
    busca: número da NF, chave de acesso, nome ou documento do destinatário.
    status: gerada | processando | pendente_webservice | erro | vencida.  de/ate: data de emissão da guia (AAAA-MM-DD).
    contagem: quantidade por status com os demais filtros aplicados.
    """
    from sqlalchemy import or_
    from database import Guia
    from guia_store import para_item

    q = db.query(Guia).filter(Guia.ambiente == os.getenv("GNRE_AMBIENTE", "2"))
    if busca:
        b = busca.strip()
        dig = "".join(c for c in b if c.isdigit())
        conds = [Guia.destinatario.ilike(f"%{b}%")]
        if dig:
            conds += [Guia.n_nf.in_({dig, dig.lstrip("0") or dig}), Guia.chave_nfe.contains(dig), Guia.doc_dest == dig]
        q = q.filter(or_(*conds))
    if uf:
        q = q.filter(Guia.uf_dest == uf.upper())
    if de:
        q = q.filter(Guia.criado_em >= datetime.combine(de, time.min))
    if ate:
        q = q.filter(Guia.criado_em <= datetime.combine(ate, time.max))
    # Contagem por status com os demais filtros aplicados (atalhos de filtro na tela)
    from sqlalchemy import func
    contagem = dict(q.with_entities(Guia.status, func.count()).group_by(Guia.status).all())
    contagem["vencida"] = q.filter(Guia.status == "gerada", Guia.vencimento < date.today()).count()
    if status == "vencida":
        q = q.filter(Guia.status == "gerada", Guia.vencimento < date.today())
    elif status:
        q = q.filter(Guia.status == status)
    total = q.count()
    itens = q.order_by(Guia.id.desc()).offset(offset).limit(min(limite, 500)).all()
    return {"total": total, "contagem": contagem, "itens": [para_item(g) for g in itens]}


@app.get("/api/guias/{guia_id}")
async def obter_guia(guia_id: int, db: Session = Depends(get_db)):
    """Guia completa (inclui XML enviado e PDF, quando houver) — usada para imprimir."""
    from database import Guia
    from guia_store import para_item

    g = db.get(Guia, guia_id)
    if not g:
        raise HTTPException(404, "Guia não encontrada.")
    return para_item(g, completa=True)


@app.post("/api/guias/{guia_id}/atualizar")
async def atualizar_guia(guia_id: int, db: Session = Depends(get_db)):
    """Reconsulta no portal GNRE uma guia que ficou 'processando'."""
    from database import Guia
    from gnre_service import consultar_recibo
    from guia_store import aplicar_resultado, para_item

    g = db.get(Guia, guia_id)
    if not g:
        raise HTTPException(404, "Guia não encontrada.")
    if g.tipo != "GNRE" or not g.numero_recibo:
        raise HTTPException(400, "Só guias GNRE com recibo podem ser reconsultadas.")
    cert = _cert_empresa(db, g.cnpj_emit)
    if not cert:
        raise HTTPException(400, "Empresa emitente sem certificado A1 para a consulta.")
    try:
        result = await asyncio.to_thread(consultar_recibo, g.numero_recibo, cert)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))
    return para_item(aplicar_resultado(db, g, result))


@app.post("/api/guias/atualizar-pendentes")
async def atualizar_pendentes(db: Session = Depends(get_db)):
    """Reconsulta todas as guias GNRE em 'processando' do ambiente atual."""
    from database import Guia
    from gnre_service import consultar_recibo
    from guia_store import aplicar_resultado

    pendentes = db.query(Guia).filter(
        Guia.ambiente == os.getenv("GNRE_AMBIENTE", "2"),
        Guia.status == "processando",
        Guia.numero_recibo.isnot(None),
    ).all()
    resumo = {"consultadas": 0, "geradas": 0, "erros": 0, "ainda_processando": 0}
    for g in pendentes:
        cert = _cert_empresa(db, g.cnpj_emit)
        if not cert:
            continue
        try:
            result = await asyncio.to_thread(consultar_recibo, g.numero_recibo, cert)
        except RuntimeError:
            continue
        aplicar_resultado(db, g, result)
        resumo["consultadas"] += 1
        chave = {"gerada": "geradas", "erro": "erros"}.get(g.status, "ainda_processando")
        resumo[chave] += 1
    return resumo


def _cert_empresa(db: Session, cnpj: str | None) -> tuple[str, str] | None:
    """Certificado da empresa informada, ou da primeira empresa cadastrada com certificado."""
    from database import Empresa
    from cert_manager import get_cert_paths

    q = db.query(Empresa).filter(Empresa.tem_certificado.is_(True))
    if cnpj:
        q = q.filter(Empresa.cnpj == "".join(c for c in cnpj if c.isdigit()))
    emp = q.first()
    return get_cert_paths(emp.cnpj) if emp else None


# ── Guide generation ───────────────────────────────────────────────────────────

@app.post("/api/gerar/xml", response_model=ResultadoGuias)
async def gerar_por_xml(
    file: UploadFile = File(...),
    reemitir: bool = False,
    db: Session = Depends(get_db),
):
    """
    Generate DIFAL guides from NF-e XML upload.
    If the emitente CNPJ is registered, loads address and certificate automatically.
    reemitir=true emite nova guia mesmo que a NF-e já tenha uma gerada e não vencida.
    """
    content = await file.read()
    try:
        dados = parse_nfe_xml(content).model_copy(update={"reemitir": reemitir})
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    try:
        return await gerar_guias_completo(dados, db=db)
    except Exception as exc:
        raise HTTPException(500, f"Erro ao gerar guias: {exc}")


@app.post("/api/gerar/json", response_model=ResultadoGuias)
async def gerar_por_json(
    formulario: FormularioManual,
    db: Session = Depends(get_db),
):
    """
    Generate DIFAL guides from JSON body.
    If cnpj_emit matches a registered empresa, its address and certificate are used.
    Emitente address fields are optional when the empresa is registered.
    """
    try:
        dados = calcular_difal_manual(formulario)
        return await gerar_guias_completo(dados, db=db)
    except Exception as exc:
        raise HTTPException(500, f"Erro ao gerar guias: {exc}")


# Alias for form submissions
app.post("/api/gerar/manual", response_model=ResultadoGuias)(gerar_por_json)


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=True)
