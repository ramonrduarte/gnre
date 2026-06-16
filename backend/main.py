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

Utilities:
  GET /api/uf/{uf}   — state config (rates, codes)
  GET /api/ufs       — all states
  GET /api/status    — health check
"""
import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from database import get_db, init_db
from empresa_router import router as empresa_router
from models import FormularioManual, ResultadoGuias
from nfe_parser import parse_nfe_xml
from difal_calculator import calcular_difal_manual
from guide_router import gerar_guias_completo
from uf_config import get_uf_config, UF_ALIQ_INTERNA, UF_FECP


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


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


# ── Guide generation ───────────────────────────────────────────────────────────

@app.post("/api/gerar/xml", response_model=ResultadoGuias)
async def gerar_por_xml(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Generate DIFAL guides from NF-e XML upload.
    If the emitente CNPJ is registered, loads address and certificate automatically.
    """
    content = await file.read()
    try:
        dados = parse_nfe_xml(content)
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
