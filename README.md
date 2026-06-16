# DIFAL / GNRE Generator

Sistema web para geração automática de guias de recolhimento de DIFAL (EC 87/2015).

- **GNRE** — todos os estados via WebService nacional (gnre.pe.gov.br)
- **DARE-ICMS SP** — São Paulo via automação do portal da Fazenda SP

## Funcionalidades

- Geração a partir de **XML NF-e** (NFe / nfeProc), **formulário manual** ou **API JSON**
- Cadastro de empresas emitentes com dados e certificado A1
- Upload de certificado PFX/P12 — extraído e armazenado com segurança
- Cálculo automático de DIFAL e FECP por UF (alíquotas configuradas)
- Roteamento automático SP → DARE-ICMS | demais UFs → GNRE
- API REST com Swagger UI em `/docs`

---

## Deploy via Portainer

### Pré-requisitos no servidor

1. **Postgres** já rodando em algum container/stack Docker
2. Criar banco e usuário para a aplicação:

```sql
CREATE USER gnre_user WITH PASSWORD 'senha_forte';
CREATE DATABASE gnre_db OWNER gnre_user;
```

3. Anotar o **nome da rede Docker** onde o Postgres está:
   - Portainer → **Networks** → copie o nome da rede do container Postgres

### Variáveis de ambiente (obrigatórias)

| Variável | Descrição | Exemplo |
|---|---|---|
| `DATABASE_URL` | String de conexão PostgreSQL | `postgresql://gnre_user:senha@postgres:5432/gnre_db` |
| `POSTGRES_NETWORK` | Nome da rede Docker do Postgres | `postgres_network` |
| `GNRE_AMBIENTE` | `1` = Produção, `2` = Homologação | `2` |

### Variáveis opcionais

| Variável | Padrão | Descrição |
|---|---|---|
| `PORT` | `8000` | Porta da aplicação |
| `SP_DARE_CODIGO_DIFAL` | `064-2` | Código de receita DARE para DIFAL no portal SP |
| `SP_PLAYWRIGHT_TIMEOUT` | `30` | Timeout (segundos) para automação do portal SP |
| `CERT_PATH` | _(vazio)_ | Caminho do cert.pem global (use o cadastro por empresa via UI) |
| `KEY_PATH` | _(vazio)_ | Caminho do key.pem global |

> **Nota sobre certificados:** prefira cadastrar o certificado A1 por empresa na interface web (aba **Empresas**). O `CERT_PATH`/`KEY_PATH` é um fallback global.

### Passos no Portainer

1. **Stacks** → **Add Stack**
2. Selecione **Repository** e aponte para este repositório
   - Compose path: `portainer-stack.yml`
3. Em **Environment variables**, adicione as variáveis da tabela acima
4. **Deploy the stack**

O sistema cria as tabelas do banco automaticamente na primeira inicialização.

### Volume de dados

O volume `gnre_certs` é criado automaticamente e persiste:
- Certificados A1 das empresas cadastradas (arquivos PEM)
- Banco SQLite (quando `DATABASE_URL` não estiver configurado)

---

## Desenvolvimento local

```bash
# 1. Criar ambiente virtual e instalar dependências
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
playwright install chromium

# 2. Configurar variáveis
cp .env.example .env
# edite .env conforme necessário

# 3. Iniciar (SQLite por padrão, sem precisar de Postgres)
python backend/main.py
# Acesse: http://localhost:8000
```

### Ou com Docker + Postgres local

```bash
docker compose up --build
# Acesse: http://localhost:8000
```

---

## Estrutura do projeto

```
gnre/
├── backend/
│   ├── main.py              # FastAPI app
│   ├── database.py          # SQLAlchemy (PostgreSQL / SQLite)
│   ├── models.py            # Pydantic schemas
│   ├── nfe_parser.py        # Parser XML NF-e 4.0
│   ├── uf_config.py         # Alíquotas e códigos GNRE por UF
│   ├── difal_calculator.py  # Cálculo DIFAL / FCP
│   ├── gnre_service.py      # Geração XML GNRE + WebService SOAP
│   ├── dare_sp_service.py   # Automação DARE-ICMS SP (Playwright)
│   ├── guide_router.py      # Roteamento por UF
│   ├── empresa_router.py    # CRUD empresas + certificados
│   └── cert_manager.py      # Upload e extração de PFX/P12
├── frontend/
│   └── index.html           # SPA (vanilla JS)
├── Dockerfile
├── docker-compose.yml       # Dev local com Postgres
├── portainer-stack.yml      # Produção via Portainer
└── .env.example
```

---

## API

Documentação interativa disponível em `/docs` (Swagger UI) após iniciar o servidor.

Endpoints principais:

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/api/gerar/xml` | Gerar guias a partir de XML NF-e |
| `POST` | `/api/gerar/json` | Gerar guias a partir de JSON |
| `GET` | `/api/empresas` | Listar empresas cadastradas |
| `POST` | `/api/empresas` | Cadastrar / atualizar empresa |
| `POST` | `/api/empresas/{cnpj}/certificado` | Upload do certificado A1 (PFX) |
| `GET` | `/api/uf/{uf}` | Configuração de alíquotas de um estado |

---

## Observações sobre o GNRE WebService

O envio automático ao WebService GNRE requer:
1. **Certificado A1** cadastrado na empresa (via aba Empresas na interface)
2. **Ambiente de homologação** (`GNRE_AMBIENTE=2`) para testes — requer cadastro em gnre.pe.gov.br
3. **Ambiente de produção** (`GNRE_AMBIENTE=1`) para emissão real

Quando o certificado não está configurado, o sistema gera o **XML GNRE** para download e retorna o link do portal para preenchimento manual.

**Códigos GNRE para DIFAL EC 87/2015:**
- `100122` — ICMS Diferencial de Alíquota (consumidor final)
- `100131` — FCP/FECP (Fundo de Combate à Pobreza)

> Verifique os códigos com a SEFAZ de cada estado — podem variar. Edite `backend/uf_config.py` para ajustar.
