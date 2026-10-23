"""Rotas de backup (somente administradores — controle feito no middleware de acesso)."""
import asyncio

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import backup_service as bk

router = APIRouter(prefix="/api/backup", tags=["backup"])


class ConfigIn(BaseModel):
    ativo: bool | None = None
    dias: list[int] | None = None
    horarios: list[str] | None = None
    manter: int | None = None
    incluir_certificados: bool | None = None
    senha: str | None = None          # "" remove a senha; None mantém a atual


class GerarIn(BaseModel):
    incluir_certificados: bool | None = None   # padrão: o da configuração
    senha: str | None = None                   # padrão: a senha da configuração


@router.get("")
async def resumo():
    return {
        "config": bk.config_publica(),
        "ultimo": bk.ultimo_resultado(),
        "backups": bk.listar_backups(),
        "agora": bk.agora().strftime("%d/%m/%Y %H:%M"),
    }


@router.put("/config")
async def salvar(dados: ConfigIn):
    try:
        bk.salvar_config(dados.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return bk.config_publica()


@router.post("/gerar")
async def gerar(dados: GerarIn | None = None):
    conf = bk.obter_config()
    dados = dados or GerarIn()
    incluir = conf["incluir_certificados"] if dados.incluir_certificados is None else dados.incluir_certificados
    senha = conf.get("senha", "") if dados.senha is None else dados.senha
    info = await asyncio.to_thread(bk.gerar_backup, incluir, senha, "manual")
    bk.registrar_manual(info)
    bk.aplicar_retencao(conf["manter"])
    return info


@router.get("/download/{nome}")
async def baixar(nome: str):
    try:
        p = bk.caminho_backup(nome)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Backup não encontrado.")
    return FileResponse(p, filename=p.name, media_type="application/octet-stream")


@router.delete("/{nome}")
async def apagar(nome: str):
    try:
        bk.caminho_backup(nome).unlink()
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Backup não encontrado.")
    return {"ok": True}


@router.post("/restaurar")
async def restaurar(arquivo: UploadFile = File(...), senha: str = Form("")):
    """Substitui TODOS os dados pelos do backup. Todos os usuários precisam entrar de novo."""
    conteudo = await arquivo.read()
    try:
        return await asyncio.to_thread(bk.restaurar, conteudo, senha)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
