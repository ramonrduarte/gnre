"""
Backup e restauração do sistema.

Um backup é um ZIP com:
  manifest.json            — versão, data, origem (manual/agendado), contagem por tabela
  tabelas/<tabela>.json    — linhas de empresas, guias, usuários, tokens e configurações
  certs/<cnpj>/...         — certificados A1 (opcional)

O formato independe do banco (PostgreSQL ou SQLite), então serve para migrar de servidor.
Com senha, o ZIP é cifrado (Fernet / AES-128 + HMAC, chave derivada por scrypt) e o
arquivo vira .gnrebak — recomendado sempre que os certificados forem incluídos.

Os arquivos ficam em data/backups (mesmo volume persistente do servidor) e podem ser
baixados pela interface em qualquer máquina.
"""
import base64
import hashlib
import io
import json
import logging
import os
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Date, DateTime, Numeric, text

from database import ApiToken, Configuracao, Empresa, Guia, Sessao, SessionLocal, Usuario, engine

logger = logging.getLogger(__name__)

_DATA = Path(__file__).parent.parent / "data"
BACKUP_DIR = _DATA / "backups"
CERTS_DIR = _DATA / "certs"
_MAGIC = b"GNREBAK1"
_NOME_OK = re.compile(r"^gnre-backup-\d{8}-\d{6}(-[a-z]+)?\.(zip|gnrebak)$")

# Ordem de inserção na restauração (pais antes dos filhos)
TABELAS = [Empresa, Usuario, ApiToken, Configuracao, Guia]

CONFIG_PADRAO = {
    "ativo": False,
    "dias": [0, 1, 2, 3, 4],        # 0 = segunda … 6 = domingo
    "horarios": ["23:00"],
    "manter": 30,                   # quantidade de backups guardados (os mais antigos são apagados)
    "incluir_certificados": True,
    "senha": "",                    # senha de cifragem dos backups agendados (vazio = sem cifra)
}


def fuso():
    """Horário de Brasília (sem horário de verão desde 2019)."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(os.getenv("TZ_BACKUP", "America/Sao_Paulo"))
    except Exception:
        return timezone(timedelta(hours=-3))


def agora() -> datetime:
    return datetime.now(fuso())


# ── Configuração ───────────────────────────────────────────────────────────────

def _ler_conf(db, chave: str, padrao):
    c = db.get(Configuracao, chave)
    if not c:
        return padrao
    try:
        return json.loads(c.valor)
    except Exception:
        return padrao


def _gravar_conf(db, chave: str, valor) -> None:
    c = db.get(Configuracao, chave)
    texto = json.dumps(valor, ensure_ascii=False)
    if c:
        c.valor = texto
    else:
        db.add(Configuracao(chave=chave, valor=texto))
    db.commit()


def obter_config() -> dict:
    db = SessionLocal()
    try:
        return {**CONFIG_PADRAO, **_ler_conf(db, "backup", {})}
    finally:
        db.close()


def salvar_config(novo: dict) -> dict:
    atual = obter_config()
    for k in CONFIG_PADRAO:
        if k in novo and novo[k] is not None:
            atual[k] = novo[k]
    atual["dias"] = sorted({int(d) for d in atual["dias"] if 0 <= int(d) <= 6})
    horarios = []
    for h in atual["horarios"]:
        if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(h)):
            horarios.append(str(h))
    atual["horarios"] = sorted(set(horarios))
    atual["manter"] = max(1, min(int(atual["manter"]), 365))
    if atual["ativo"] and (not atual["dias"] or not atual["horarios"]):
        raise ValueError("Escolha pelo menos um dia e um horário para o backup automático.")
    db = SessionLocal()
    try:
        _gravar_conf(db, "backup", atual)
    finally:
        db.close()
    return atual


def config_publica() -> dict:
    """Configuração sem a senha (apenas se há senha definida)."""
    c = obter_config()
    return {**{k: v for k, v in c.items() if k != "senha"}, "tem_senha": bool(c.get("senha"))}


def ultimo_resultado() -> dict | None:
    db = SessionLocal()
    try:
        return _ler_conf(db, "backup_ultimo", None)
    finally:
        db.close()


# ── Serialização das tabelas ───────────────────────────────────────────────────

def _serializar(valor):
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return str(valor)
    return valor


def _desserializar(coluna, valor):
    if valor is None:
        return None
    if isinstance(coluna.type, DateTime):
        return datetime.fromisoformat(valor)
    if isinstance(coluna.type, Date):
        return date.fromisoformat(valor[:10])
    if isinstance(coluna.type, Numeric):
        return Decimal(str(valor))
    return valor


def _cifrar(dados: bytes, senha: str) -> bytes:
    salt = os.urandom(16)
    return _MAGIC + salt + Fernet(_chave(senha, salt)).encrypt(dados)


def _decifrar(dados: bytes, senha: str) -> bytes:
    salt, corpo = dados[len(_MAGIC):len(_MAGIC) + 16], dados[len(_MAGIC) + 16:]
    try:
        return Fernet(_chave(senha, salt)).decrypt(corpo)
    except InvalidToken as exc:
        raise ValueError("Senha do backup incorreta.") from exc


def _chave(senha: str, salt: bytes) -> bytes:
    dk = hashlib.scrypt(senha.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return base64.urlsafe_b64encode(dk)


# ── Gerar ──────────────────────────────────────────────────────────────────────

def gerar_backup(incluir_certificados: bool = True, senha: str = "", origem: str = "manual") -> dict:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    quando = agora()
    buf = io.BytesIO()
    contagem = {}
    db = SessionLocal()
    try:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for modelo in TABELAS:
                cols = [c.name for c in modelo.__table__.columns]
                linhas = [{c: _serializar(getattr(r, c)) for c in cols} for r in db.query(modelo).all()]
                if modelo is Configuracao:
                    # o registro do último backup não precisa voltar numa restauração
                    linhas = [l for l in linhas if l["chave"] != "backup_ultimo"]
                contagem[modelo.__tablename__] = len(linhas)
                z.writestr(f"tabelas/{modelo.__tablename__}.json", json.dumps(linhas, ensure_ascii=False))
            n_certs = 0
            if incluir_certificados and CERTS_DIR.exists():
                for arq in CERTS_DIR.rglob("*"):
                    if arq.is_file():
                        z.write(arq, f"certs/{arq.relative_to(CERTS_DIR).as_posix()}")
                        n_certs += 1
            z.writestr("manifest.json", json.dumps({
                "sistema": "gnre-difal", "versao": 1, "criado_em": quando.isoformat(), "origem": origem,
                "tabelas": contagem, "certificados": n_certs, "cifrado": bool(senha),
            }, ensure_ascii=False, indent=1))
    finally:
        db.close()

    dados = buf.getvalue()
    ext = "gnrebak" if senha else "zip"
    nome = f"gnre-backup-{quando:%Y%m%d-%H%M%S}-{origem}.{ext}"
    (BACKUP_DIR / nome).write_bytes(_cifrar(dados, senha) if senha else dados)
    logger.info("Backup gerado: %s (%s)", nome, contagem)
    return info_arquivo(BACKUP_DIR / nome)


def aplicar_retencao(manter: int) -> list[str]:
    arquivos = sorted(listar_backups(), key=lambda b: b["nome"], reverse=True)
    apagados = []
    for b in arquivos[manter:]:
        (BACKUP_DIR / b["nome"]).unlink(missing_ok=True)
        apagados.append(b["nome"])
    return apagados


def info_arquivo(p: Path) -> dict:
    st = p.stat()
    m = re.search(r"(\d{8})-(\d{6})-?([a-z]*)", p.name)
    quando = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S") if m else datetime.fromtimestamp(st.st_mtime)
    return {
        "nome": p.name,
        "tamanho": st.st_size,
        "criado_em": quando.strftime("%d/%m/%Y %H:%M"),
        "origem": (m.group(3) if m and m.group(3) else "manual"),
        "cifrado": p.suffix == ".gnrebak",
    }


def listar_backups() -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    return sorted((info_arquivo(p) for p in BACKUP_DIR.iterdir() if _NOME_OK.match(p.name)),
                  key=lambda b: b["nome"], reverse=True)


def caminho_backup(nome: str) -> Path:
    if not _NOME_OK.match(nome):
        raise ValueError("Nome de backup inválido.")
    p = BACKUP_DIR / nome
    if not p.is_file():
        raise FileNotFoundError(nome)
    return p


# ── Restaurar ──────────────────────────────────────────────────────────────────

def restaurar(conteudo: bytes, senha: str = "") -> dict:
    """Substitui todos os dados (e certificados, se o backup tiver) pelo conteúdo do backup."""
    if conteudo.startswith(_MAGIC):
        if not senha:
            raise ValueError("Este backup é cifrado: informe a senha.")
        conteudo = _decifrar(conteudo, senha)
    try:
        z = zipfile.ZipFile(io.BytesIO(conteudo))
        manifest = json.loads(z.read("manifest.json"))
    except Exception as exc:
        raise ValueError("Arquivo não é um backup válido deste sistema.") from exc
    if manifest.get("sistema") != "gnre-difal":
        raise ValueError("Arquivo não é um backup deste sistema.")

    db = SessionLocal()
    try:
        db.query(Sessao).delete()
        for modelo in reversed(TABELAS):
            q = db.query(modelo)
            if modelo is Configuracao:
                q = q.filter(Configuracao.chave != "backup_ultimo")
            q.delete()
        for modelo in TABELAS:
            nome = f"tabelas/{modelo.__tablename__}.json"
            if nome not in z.namelist():
                continue
            cols = {c.name: c for c in modelo.__table__.columns}
            for linha in json.loads(z.read(nome)):
                db.add(modelo(**{k: _desserializar(cols[k], v) for k, v in linha.items() if k in cols}))
        db.commit()
        # PostgreSQL: realinha as sequências dos ids depois de inserir ids explícitos
        if engine.dialect.name == "postgresql":
            for modelo in TABELAS:
                tabela = modelo.__tablename__
                if "id" in modelo.__table__.columns:
                    db.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{tabela}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {tabela}), 1))"
                    ))
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    n_certs = 0
    for item in z.namelist():
        if item.startswith("certs/") and not item.endswith("/"):
            destino = (CERTS_DIR / item[len("certs/"):]).resolve()
            if CERTS_DIR.resolve() not in destino.parents:
                continue  # ignora caminhos fora da pasta de certificados
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(z.read(item))
            n_certs += 1
    return {"manifest": manifest, "certificados_restaurados": n_certs}


# ── Agendamento ────────────────────────────────────────────────────────────────

def executar_agendado_se_for_hora() -> dict | None:
    """Chamado a cada ~30s. Gera o backup quando dia/horário batem com a configuração."""
    conf = obter_config()
    if not conf["ativo"]:
        return None
    t = agora()
    hhmm = t.strftime("%H:%M")
    if t.weekday() not in conf["dias"] or hhmm not in conf["horarios"]:
        return None
    marca = t.strftime("%Y-%m-%d ") + hhmm
    db = SessionLocal()
    try:
        ultimo = _ler_conf(db, "backup_ultimo", {}) or {}
        if ultimo.get("marca") == marca:
            return None
        resultado = {"marca": marca, "quando": t.strftime("%d/%m/%Y %H:%M"), "origem": "agendado"}
        try:
            info = gerar_backup(conf["incluir_certificados"], conf.get("senha") or "", origem="agendado")
            aplicar_retencao(conf["manter"])
            resultado.update(ok=True, arquivo=info["nome"], tamanho=info["tamanho"])
        except Exception as exc:
            logger.exception("Falha no backup agendado")
            resultado.update(ok=False, erro=str(exc))
        _gravar_conf(db, "backup_ultimo", resultado)
        return resultado
    finally:
        db.close()


def registrar_manual(info: dict) -> None:
    db = SessionLocal()
    try:
        ultimo = _ler_conf(db, "backup_ultimo", {}) or {}
        _gravar_conf(db, "backup_ultimo", {**ultimo, "quando": agora().strftime("%d/%m/%Y %H:%M"),
                                           "origem": "manual", "ok": True, "arquivo": info["nome"],
                                           "tamanho": info["tamanho"]})
    finally:
        db.close()
