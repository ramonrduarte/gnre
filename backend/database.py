"""
Database setup via SQLAlchemy.
Supports PostgreSQL (production) and SQLite (local dev fallback).

Set DATABASE_URL in the environment:
  PostgreSQL: postgresql://user:password@host:5432/dbname
  SQLite:     sqlite:///./data/gnre.db  (default when DATABASE_URL is not set)
"""
import os
from pathlib import Path
from datetime import datetime

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Date, Text, Numeric
from sqlalchemy.orm import DeclarativeBase, sessionmaker

_DATA_DIR = Path(__file__).parent.parent / "data"
_DATA_DIR.mkdir(exist_ok=True)

# PostgreSQL in prod, SQLite as local fallback
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DATA_DIR / 'gnre.db'}")

# SQLite needs check_same_thread=False; PostgreSQL does not accept that arg
_is_sqlite = DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class Empresa(Base):
    __tablename__ = "empresas"

    id = Column(Integer, primary_key=True, index=True)
    cnpj = Column(String(14), unique=True, nullable=False, index=True)
    razao_social = Column(String(255), nullable=False)
    nome_fantasia = Column(String(255), nullable=True)
    ie = Column(String(30), nullable=True)
    uf = Column(String(2), nullable=False)
    municipio_codigo = Column(String(7), nullable=True)
    municipio_nome = Column(String(100), nullable=True)
    cep = Column(String(8), nullable=True)
    logradouro = Column(String(255), nullable=True)
    numero = Column(String(20), nullable=True)
    bairro = Column(String(100), nullable=True)
    complemento = Column(String(100), nullable=True)

    # Certificate metadata (files stored in CERTS_DIR/{cnpj}/)
    tem_certificado = Column(Boolean, default=False)
    cert_cn = Column(String(255), nullable=True)
    cert_validade = Column(String(10), nullable=True)   # DD/MM/YYYY
    cert_emitente = Column(String(255), nullable=True)

    criado_em = Column(DateTime, default=datetime.now)
    atualizado_em = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Guia(Base):
    """Histórico das guias emitidas (uma linha por guia: DIFAL e FCP separados)."""
    __tablename__ = "guias"

    id = Column(Integer, primary_key=True, index=True)
    ambiente = Column(String(1), nullable=False, default="2")   # 1=produção, 2=homologação

    # NF-e
    chave_nfe = Column(String(44), nullable=True, index=True)
    n_nf = Column(String(20), nullable=True, index=True)
    serie = Column(String(5), nullable=True)
    dh_emi = Column(String(30), nullable=True)
    cnpj_emit = Column(String(14), nullable=True, index=True)
    emitente = Column(String(255), nullable=True)
    uf_emit = Column(String(2), nullable=True)
    uf_dest = Column(String(2), nullable=False, index=True)
    destinatario = Column(String(255), nullable=True)
    doc_dest = Column(String(14), nullable=True)

    # Guia
    tipo = Column(String(10), nullable=False)                    # GNRE | DARE-SP | DUA-ES
    receita_codigo = Column(String(10), nullable=True)
    receita_descricao = Column(String(255), nullable=True)
    valor = Column(Numeric(15, 2), nullable=False)
    vencimento = Column(Date, nullable=True)
    status = Column(String(30), nullable=False, index=True)      # gerada | processando | pendente_webservice | erro
    mensagem = Column(Text, nullable=True)
    codigo_barras = Column(String(60), nullable=True)
    linha_digitavel = Column(String(60), nullable=True)
    qrcode_pix = Column(Text, nullable=True)
    numero_guia = Column(String(30), nullable=True)
    numero_recibo = Column(String(20), nullable=True, index=True)
    gnre_xml = Column(Text, nullable=True)
    pdf_base64 = Column(Text, nullable=True)

    criado_em = Column(DateTime, default=datetime.now, index=True)
    atualizado_em = Column(DateTime, default=datetime.now, onupdate=datetime.now)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
