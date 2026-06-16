"""
Company (Empresa) CRUD + certificate management.

Routes:
  GET    /api/empresas                     — list all
  GET    /api/empresas/{cnpj}              — get one
  POST   /api/empresas                     — create / update (upsert by CNPJ)
  DELETE /api/empresas/{cnpj}              — delete
  POST   /api/empresas/{cnpj}/certificado  — upload PFX + password
  DELETE /api/empresas/{cnpj}/certificado  — remove certificate
  GET    /api/empresas/{cnpj}/cert-status  — certificate validity info
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db, Empresa
from cert_manager import process_pfx, remove_cert, cert_status, get_cert_paths

router = APIRouter(prefix="/api/empresas", tags=["empresas"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class EmpresaIn(BaseModel):
    cnpj: str
    razao_social: str
    nome_fantasia: Optional[str] = None
    ie: Optional[str] = None
    uf: str
    municipio_codigo: Optional[str] = None
    municipio_nome: Optional[str] = None
    cep: Optional[str] = None
    logradouro: Optional[str] = None
    numero: Optional[str] = None
    bairro: Optional[str] = None
    complemento: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "cnpj": "12345678000195",
                "razao_social": "Minha Empresa Ltda",
                "ie": "111.222.333.444",
                "uf": "SP",
                "municipio_codigo": "3550308",
                "municipio_nome": "São Paulo",
                "cep": "01310100",
                "logradouro": "Av. Paulista",
                "numero": "1000",
                "bairro": "Bela Vista"
            }
        }


class EmpresaOut(BaseModel):
    id: int
    cnpj: str
    razao_social: str
    nome_fantasia: Optional[str]
    ie: Optional[str]
    uf: str
    municipio_codigo: Optional[str]
    municipio_nome: Optional[str]
    cep: Optional[str]
    logradouro: Optional[str]
    numero: Optional[str]
    bairro: Optional[str]
    complemento: Optional[str]
    tem_certificado: bool
    cert_cn: Optional[str]
    cert_validade: Optional[str]
    cert_emitente: Optional[str]
    criado_em: datetime
    atualizado_em: datetime

    class Config:
        from_attributes = True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _only_digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def _get_or_404(cnpj: str, db: Session) -> Empresa:
    cnpj = _only_digits(cnpj)
    emp = db.query(Empresa).filter(Empresa.cnpj == cnpj).first()
    if not emp:
        raise HTTPException(404, f"Empresa com CNPJ {cnpj} não cadastrada.")
    return emp


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[EmpresaOut])
def listar_empresas(db: Session = Depends(get_db)):
    return db.query(Empresa).order_by(Empresa.razao_social).all()


@router.get("/{cnpj}", response_model=EmpresaOut)
def get_empresa(cnpj: str, db: Session = Depends(get_db)):
    return _get_or_404(cnpj, db)


@router.post("", response_model=EmpresaOut)
def criar_ou_atualizar_empresa(dados: EmpresaIn, db: Session = Depends(get_db)):
    """Create or update (upsert) by CNPJ."""
    cnpj = _only_digits(dados.cnpj)
    emp = db.query(Empresa).filter(Empresa.cnpj == cnpj).first()

    if emp:
        # Update existing
        for field, value in dados.model_dump().items():
            if field == "cnpj":
                continue
            setattr(emp, field, value)
        emp.atualizado_em = datetime.now()
    else:
        emp = Empresa(cnpj=cnpj, **{k: v for k, v in dados.model_dump().items() if k != "cnpj"})
        db.add(emp)

    db.commit()
    db.refresh(emp)
    return emp


@router.delete("/{cnpj}")
def deletar_empresa(cnpj: str, db: Session = Depends(get_db)):
    emp = _get_or_404(cnpj, db)
    remove_cert(emp.cnpj)
    db.delete(emp)
    db.commit()
    return {"ok": True, "mensagem": f"Empresa {emp.razao_social} removida."}


# ── Certificate management ────────────────────────────────────────────────────

@router.post("/{cnpj}/certificado")
async def upload_certificado(
    cnpj: str,
    arquivo: UploadFile = File(..., description="Arquivo PFX/P12 do certificado A1"),
    senha: str = Form(..., description="Senha do certificado"),
    db: Session = Depends(get_db),
):
    """Upload A1 certificate (PFX/P12). Extracts and stores PEM files."""
    emp = _get_or_404(cnpj, db)

    content = await arquivo.read()
    if not content:
        raise HTTPException(400, "Arquivo de certificado vazio.")

    try:
        info = process_pfx(emp.cnpj, content, senha)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    emp.tem_certificado = True
    emp.cert_cn = info["cn"]
    emp.cert_validade = info["validade"]
    emp.cert_emitente = info["emitente"]
    emp.atualizado_em = datetime.now()
    db.commit()

    return {
        "ok": True,
        "cn": info["cn"],
        "validade": info["validade"],
        "emitente": info["emitente"],
        "dias_restantes": info["dias_restantes"],
        "mensagem": f"Certificado instalado com sucesso. Válido por {info['dias_restantes']} dias.",
    }


@router.delete("/{cnpj}/certificado")
def remover_certificado(cnpj: str, db: Session = Depends(get_db)):
    emp = _get_or_404(cnpj, db)
    remove_cert(emp.cnpj)
    emp.tem_certificado = False
    emp.cert_cn = None
    emp.cert_validade = None
    emp.cert_emitente = None
    emp.atualizado_em = datetime.now()
    db.commit()
    return {"ok": True, "mensagem": "Certificado removido."}


@router.get("/{cnpj}/cert-status")
def status_certificado(cnpj: str, db: Session = Depends(get_db)):
    emp = _get_or_404(cnpj, db)
    status = cert_status(emp.cnpj, emp.cert_validade)
    return {
        **status,
        "cn": emp.cert_cn,
        "validade": emp.cert_validade,
        "emitente": emp.cert_emitente,
    }
