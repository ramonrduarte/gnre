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

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
