"""
Autenticação da interface e da API.

- Interface: login com usuário/senha → cookie de sessão HttpOnly (validade 12h, renovada com o uso).
- Integração (ERP): token de API no cabeçalho `Authorization: Bearer <token>` ou `X-API-Key: <token>`.
- Senhas com hash scrypt; sessões e tokens guardados apenas como SHA-256.
- Perfis: admin (tudo) e operador (emitir, consultar e imprimir guias).

Primeiro acesso: sem nenhum usuário cadastrado, a tela pede a criação do administrador.
Também é possível criar o primeiro admin pelas variáveis GNRE_ADMIN_LOGIN / GNRE_ADMIN_SENHA.
"""
import base64
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import ApiToken, Sessao, SessionLocal, Usuario, get_db

COOKIE = "gnre_sessao"
_SESSAO_HORAS = 12
_PERFIS = ("admin", "operador")

# Rotas liberadas sem login
_PUBLICAS = {"/api/status", "/api/auth/estado", "/api/auth/login", "/api/auth/setup", "/api/auth/logout"}
# Rotas que o token de API (ERP) pode usar
_PREFIXOS_TOKEN = ("/api/gerar/", "/api/guias", "/api/gnre/", "/api/uf", "/api/ufs")
# Rotas só de administrador (além de qualquer escrita em /api/empresas)
_PREFIXOS_ADMIN = ("/api/usuarios", "/api/tokens", "/api/backup")


# ── Hash de senha e de tokens ──────────────────────────────────────────────────

def hash_senha(senha: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(senha.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verificar_senha(senha: str, armazenado: str) -> bool:
    try:
        _, salt_b64, dk_b64 = armazenado.split("$")
        dk = hashlib.scrypt(senha.encode(), salt=base64.b64decode(salt_b64), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


def _sha(valor: str) -> str:
    return hashlib.sha256(valor.encode()).hexdigest()


# ── Limite de tentativas de login (por IP, em memória) ─────────────────────────

_falhas: dict[str, list[float]] = {}
_MAX_FALHAS, _JANELA = 5, 300


def _bloqueado(ip: str) -> bool:
    agora = time.time()
    _falhas[ip] = [t for t in _falhas.get(ip, []) if agora - t < _JANELA]
    return len(_falhas[ip]) >= _MAX_FALHAS


def _registrar_falha(ip: str) -> None:
    _falhas.setdefault(ip, []).append(time.time())


# ── Resolução de quem está chamando ────────────────────────────────────────────

def _usuario_da_sessao(db: Session, token: str) -> Usuario | None:
    s = db.query(Sessao).filter(Sessao.token_hash == _sha(token)).first()
    if not s or s.expira_em < datetime.now():
        return None
    u = db.get(Usuario, s.usuario_id)
    if not u or not u.ativo:
        return None
    # renova a sessão quando passou da metade da validade
    if s.expira_em - datetime.now() < timedelta(hours=_SESSAO_HORAS / 2):
        s.expira_em = datetime.now() + timedelta(hours=_SESSAO_HORAS)
        db.commit()
    return u


def _token_api(db: Session, token: str) -> ApiToken | None:
    t = db.query(ApiToken).filter(ApiToken.token_hash == _sha(token), ApiToken.ativo.is_(True)).first()
    if t:
        t.ultimo_uso = datetime.now()
        db.commit()
    return t


def _token_do_cabecalho(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key") or None


def exige_acesso(path: str, method: str) -> bool:
    if path in _PUBLICAS:
        return False
    return path.startswith("/api/") or path in ("/docs", "/redoc", "/openapi.json")


def _so_admin(path: str, method: str) -> bool:
    if path.startswith(_PREFIXOS_ADMIN):
        return True
    return path.startswith("/api/empresas") and method not in ("GET", "HEAD")


def autorizar(request: Request) -> tuple[dict | None, str | None]:
    """
    Retorna (identidade, erro). identidade = {"tipo": "usuario"|"token", "login", "perfil", "id"}.
    erro = mensagem para 401/403 quando o acesso não é permitido.
    """
    path, method = request.url.path, request.method
    db = SessionLocal()
    try:
        token_api = _token_do_cabecalho(request)
        if token_api:
            t = _token_api(db, token_api)
            if not t:
                return None, "401:Token de API inválido ou revogado."
            if not path.startswith(_PREFIXOS_TOKEN):
                return None, "403:Token de API não tem acesso a esta rota."
            return {"tipo": "token", "login": f"token:{t.nome}", "perfil": "operador", "id": t.id}, None

        cookie = request.cookies.get(COOKIE)
        u = _usuario_da_sessao(db, cookie) if cookie else None
        if not u:
            return None, "401:Faça login para continuar."
        if _so_admin(path, method) and u.perfil != "admin":
            return None, "403:Apenas administradores podem fazer isso."
        return {"tipo": "usuario", "login": u.login, "perfil": u.perfil, "id": u.id, "nome": u.nome}, None
    finally:
        db.close()


def criar_admin_inicial() -> None:
    """Cria o administrador a partir de GNRE_ADMIN_LOGIN/GNRE_ADMIN_SENHA quando não há usuários."""
    login, senha = os.getenv("GNRE_ADMIN_LOGIN"), os.getenv("GNRE_ADMIN_SENHA")
    if not (login and senha):
        return
    db = SessionLocal()
    try:
        if db.query(Usuario).count() == 0:
            db.add(Usuario(nome="Administrador", login=login.strip().lower(), senha_hash=hash_senha(senha), perfil="admin"))
            db.commit()
    finally:
        db.close()


# ── Rotas ──────────────────────────────────────────────────────────────────────

router = APIRouter(tags=["acesso"])


class LoginIn(BaseModel):
    login: str
    senha: str


class SetupIn(BaseModel):
    nome: str = Field(..., min_length=2)
    login: str = Field(..., min_length=3)
    senha: str = Field(..., min_length=8)


class SenhaIn(BaseModel):
    senha_atual: str
    nova_senha: str = Field(..., min_length=8)


class UsuarioIn(BaseModel):
    nome: str = Field(..., min_length=2)
    login: str = Field(..., min_length=3)
    senha: str = Field(..., min_length=8)
    perfil: str = "operador"


class UsuarioUpd(BaseModel):
    nome: str | None = None
    perfil: str | None = None
    ativo: bool | None = None
    nova_senha: str | None = Field(None, min_length=8)


class TokenIn(BaseModel):
    nome: str = Field(..., min_length=2, description="Identificação, ex.: ERP Bling, Integração loja")


def _usuario_out(u: Usuario) -> dict:
    return {
        "id": u.id, "nome": u.nome, "login": u.login, "perfil": u.perfil, "ativo": u.ativo,
        "ultimo_acesso": u.ultimo_acesso.strftime("%d/%m/%Y %H:%M") if u.ultimo_acesso else None,
    }


def _abrir_sessao(db: Session, response: Response, u: Usuario, ip: str | None) -> None:
    token = secrets.token_urlsafe(32)
    db.add(Sessao(token_hash=_sha(token), usuario_id=u.id, ip=ip,
                  expira_em=datetime.now() + timedelta(hours=_SESSAO_HORAS)))
    u.ultimo_acesso = datetime.now()
    # limpa sessões vencidas
    db.query(Sessao).filter(Sessao.expira_em < datetime.now()).delete()
    db.commit()
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=_SESSAO_HORAS * 3600, path="/")


def identidade(request: Request) -> dict:
    ident = getattr(request.state, "identidade", None)
    if not ident:
        raise HTTPException(401, "Faça login para continuar.")
    return ident


@router.get("/api/auth/estado")
async def estado(request: Request, db: Session = Depends(get_db)):
    """Usado pela tela: se precisa criar o primeiro admin e quem está logado."""
    precisa_setup = db.query(Usuario).count() == 0
    cookie = request.cookies.get(COOKIE)
    u = _usuario_da_sessao(db, cookie) if cookie and not precisa_setup else None
    return {"precisa_setup": precisa_setup, "usuario": _usuario_out(u) if u else None}


@router.post("/api/auth/setup")
async def setup(dados: SetupIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """Cria o primeiro administrador. Só funciona enquanto não houver nenhum usuário."""
    if db.query(Usuario).count() > 0:
        raise HTTPException(409, "O sistema já tem usuários. Faça login.")
    u = Usuario(nome=dados.nome.strip(), login=dados.login.strip().lower(), senha_hash=hash_senha(dados.senha), perfil="admin")
    db.add(u)
    db.commit()
    _abrir_sessao(db, response, u, request.client.host if request.client else None)
    return _usuario_out(u)


@router.post("/api/auth/login")
async def login(dados: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    if _bloqueado(ip):
        raise HTTPException(429, "Muitas tentativas. Aguarde 5 minutos e tente de novo.")
    u = db.query(Usuario).filter(Usuario.login == dados.login.strip().lower()).first()
    if not u or not u.ativo or not verificar_senha(dados.senha, u.senha_hash):
        _registrar_falha(ip)
        raise HTTPException(401, "Usuário ou senha incorretos.")
    _falhas.pop(ip, None)
    _abrir_sessao(db, response, u, ip)
    return _usuario_out(u)


@router.post("/api/auth/logout")
async def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    cookie = request.cookies.get(COOKIE)
    if cookie:
        db.query(Sessao).filter(Sessao.token_hash == _sha(cookie)).delete()
        db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/api/auth/senha")
async def trocar_senha(dados: SenhaIn, request: Request, db: Session = Depends(get_db)):
    ident = identidade(request)
    if ident["tipo"] != "usuario":
        raise HTTPException(403, "Disponível apenas para usuários.")
    u = db.get(Usuario, ident["id"])
    if not verificar_senha(dados.senha_atual, u.senha_hash):
        raise HTTPException(400, "Senha atual incorreta.")
    u.senha_hash = hash_senha(dados.nova_senha)
    db.commit()
    return {"ok": True}


# Usuários (admin)

@router.get("/api/usuarios")
async def listar_usuarios(db: Session = Depends(get_db)):
    return [_usuario_out(u) for u in db.query(Usuario).order_by(Usuario.nome).all()]


@router.post("/api/usuarios")
async def criar_usuario(dados: UsuarioIn, db: Session = Depends(get_db)):
    if dados.perfil not in _PERFIS:
        raise HTTPException(400, "Perfil deve ser admin ou operador.")
    login_ = dados.login.strip().lower()
    if db.query(Usuario).filter(Usuario.login == login_).first():
        raise HTTPException(409, "Já existe um usuário com esse login.")
    u = Usuario(nome=dados.nome.strip(), login=login_, senha_hash=hash_senha(dados.senha), perfil=dados.perfil)
    db.add(u)
    db.commit()
    return _usuario_out(u)


@router.put("/api/usuarios/{usuario_id}")
async def atualizar_usuario(usuario_id: int, dados: UsuarioUpd, request: Request, db: Session = Depends(get_db)):
    u = db.get(Usuario, usuario_id)
    if not u:
        raise HTTPException(404, "Usuário não encontrado.")
    eu = identidade(request)["id"] == u.id
    if dados.perfil is not None:
        if dados.perfil not in _PERFIS:
            raise HTTPException(400, "Perfil deve ser admin ou operador.")
        if eu and dados.perfil != "admin":
            raise HTTPException(400, "Você não pode remover o próprio perfil de administrador.")
        u.perfil = dados.perfil
    if dados.ativo is not None:
        if eu and not dados.ativo:
            raise HTTPException(400, "Você não pode desativar o próprio usuário.")
        u.ativo = dados.ativo
        if not dados.ativo:
            db.query(Sessao).filter(Sessao.usuario_id == u.id).delete()
    if dados.nome:
        u.nome = dados.nome.strip()
    if dados.nova_senha:
        u.senha_hash = hash_senha(dados.nova_senha)
        if not eu:
            db.query(Sessao).filter(Sessao.usuario_id == u.id).delete()
    if db.query(Usuario).filter(Usuario.perfil == "admin", Usuario.ativo.is_(True)).count() == 0:
        db.rollback()
        raise HTTPException(400, "O sistema precisa de pelo menos um administrador ativo.")
    db.commit()
    return _usuario_out(u)


# Tokens de API (admin)

def _token_out(t: ApiToken) -> dict:
    return {
        "id": t.id, "nome": t.nome, "prefixo": t.prefixo, "ativo": t.ativo, "criado_por": t.criado_por,
        "criado_em": t.criado_em.strftime("%d/%m/%Y %H:%M") if t.criado_em else None,
        "ultimo_uso": t.ultimo_uso.strftime("%d/%m/%Y %H:%M") if t.ultimo_uso else None,
    }


@router.get("/api/tokens")
async def listar_tokens(db: Session = Depends(get_db)):
    return [_token_out(t) for t in db.query(ApiToken).order_by(ApiToken.id.desc()).all()]


@router.post("/api/tokens")
async def criar_token(dados: TokenIn, request: Request, db: Session = Depends(get_db)):
    """Cria um token de API. O valor completo só é mostrado nesta resposta."""
    token = "gnre_" + secrets.token_urlsafe(32)
    t = ApiToken(nome=dados.nome.strip(), prefixo=token[:12], token_hash=_sha(token),
                 criado_por=identidade(request)["login"])
    db.add(t)
    db.commit()
    return {**_token_out(t), "token": token}


@router.delete("/api/tokens/{token_id}")
async def revogar_token(token_id: int, db: Session = Depends(get_db)):
    t = db.get(ApiToken, token_id)
    if not t:
        raise HTTPException(404, "Token não encontrado.")
    t.ativo = False
    db.commit()
    return _token_out(t)
