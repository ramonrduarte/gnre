@echo off
echo === Instalando dependências do DIFAL/GNRE Generator ===

cd /d "%~dp0"

:: Verificar Python
python --version >nul 2>&1 || (echo ERRO: Python não encontrado. Instale em python.org && pause && exit /b 1)

:: Criar virtualenv
if not exist ".venv" (
    echo Criando ambiente virtual...
    python -m venv .venv
)

:: Ativar e instalar
call .venv\Scripts\activate.bat
echo Instalando pacotes Python...
pip install -r requirements.txt

echo Instalando Playwright (navegador para automação SP)...
playwright install chromium

:: Copiar .env se não existir
if not exist ".env" (
    copy .env.example .env
    echo Arquivo .env criado a partir do .env.example
    echo Edite .env para configurar seu certificado digital em producao.
)

echo.
echo === Instalação concluída! ===
echo Para iniciar: python backend\main.py
echo Acesse: http://localhost:8000
pause
