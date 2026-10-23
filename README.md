# DIFAL / GNRE Generator

Sistema web para geração automática de guias de recolhimento de DIFAL (EC 87/2015).

- **GNRE** — demais estados via WebService nacional (gnre.pe.gov.br)
- **DARE-ICMS SP** — São Paulo via portal da Fazenda SP (CAPTCHA: upload manual do XML)
- **DUA-e ES** — Espírito Santo via WebService da SEFAZ-ES

## Funcionalidades

- Geração a partir de **XML NF-e** (NFe / nfeProc), **formulário manual** ou **API JSON**
- Cadastro de empresas emitentes com dados e certificado A1
- Upload de certificado PFX/P12 — extraído e armazenado com segurança
- Cálculo automático de DIFAL e FECP por UF (alíquotas configuradas)
- Roteamento automático SP → DARE-ICMS | ES → DUA-e | demais UFs → GNRE
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
│   ├── gnre_config.py       # Regras por UF via GnreConfigUF (cache 24h)
│   ├── dare_es_service.py   # DUA-e ES (WebService SEFAZ-ES)
│   ├── dare_sp_service.py   # Automação DARE-ICMS SP (Playwright)
│   ├── guide_router.py      # Roteamento por UF
│   ├── empresa_router.py    # CRUD empresas + certificados
│   └── cert_manager.py      # Upload e extração de PFX/P12
├── frontend/
│   └── index.html           # SPA (vanilla JS)
├── docs/
│   └── API.md               # Guia de integração da API
├── Dockerfile
├── docker-compose.yml       # Dev local com Postgres
├── portainer-stack.yml      # Produção via Portainer
└── .env.example
```

---

## API

**Guia completo de integração (campos obrigatórios, exemplos, status): [docs/API.md](docs/API.md)**

Documentação interativa disponível em `/docs` (Swagger UI) após iniciar o servidor.

Endpoints principais:

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/api/gerar/json` | Gerar guias a partir de JSON (uma NF-e por chamada) |
| `POST` | `/api/gerar/xml` | Gerar guias a partir do XML da NF-e |
| `GET` | `/api/guias` | Histórico de guias emitidas (busca por NF, chave, destinatário) |
| `GET` | `/api/gnre/resultado/{recibo}` | Reconsultar lote que ficou `processando` |
| `GET` | `/api/gnre/config/{uf}/{receita}` | Regras da UF no portal GNRE |
| `GET` | `/api/empresas` | Listar empresas cadastradas |
| `POST` | `/api/empresas` | Cadastrar / atualizar empresa |
| `POST` | `/api/empresas/{cnpj}/certificado` | Upload do certificado A1 (PFX) |

---

## Observações sobre o GNRE WebService

Manual oficial: *Manual de Integração do Contribuinte GNRE v2.17* (gnre.pe.gov.br → Downloads).

O envio automático requer:
1. **Certificado A1 (e-CNPJ)** cadastrado na empresa (aba Empresas)
2. CNPJ habilitado em gnre.pe.gov.br → Automação → *Solicitar uso do WebService*
   (pedido separado para produção e para homologação em www.testegnre.pe.gov.br)
3. `GNRE_AMBIENTE=2` (homologação, guias sem valor) ou `GNRE_AMBIENTE=1` (produção)

Endereços dos WebServices (seção 4 do manual):
- Produção: `https://www.gnre.pe.gov.br/gnreWS/services/...`
- Homologação: `https://www.testegnre.pe.gov.br/gnreWS/services/...` (sem a porta 444; o
  certificado do servidor de testes não confere com o hostname, por isso a verificação SSL
  fica desligada só em homologação)

**Receitas usadas:** `100102` — ICMS Consumidor Final Não Contribuinte Outra UF por Operação (DIFAL)
e `100129` — FCP por Operação. As regras de preenchimento de cada UF (documento de origem,
referência, campos extras, detalhamento, FCP no mesmo guia) são consultadas no serviço
**GnreConfigUF** e guardadas em `data/gnre_config_cache.json` por 24h. A tabela de
`backend/uf_config.py` só é usada quando não há certificado para consultar o portal.

SP e ES não estão habilitados no GNRE (GnreConfigUF retorna 999): SP usa DARE-ICMS e ES usa DUA-e.

---

## Acesso e segurança

- A interface e a API exigem autenticação. `/api/status` continua público (healthcheck).
- **Primeiro acesso:** sem nenhum usuário cadastrado, a tela pede a criação do administrador.
  Faça isso logo após o primeiro deploy. Alternativa: definir `GNRE_ADMIN_LOGIN` e
  `GNRE_ADMIN_SENHA` no Portainer; o admin é criado na inicialização se não houver usuários.
- Perfis: **administrador** (empresas, certificado, usuários, tokens, backup) e
  **operador** (emitir, consultar e imprimir guias).
- Sessão por cookie HttpOnly, válida por 12h e renovada com o uso. Após 5 senhas erradas,
  o IP fica bloqueado por 5 minutos.
- ERP: token de API (`Authorization: Bearer <token>`) criado em **Usuários e acesso**.
  Detalhes em [docs/API.md](docs/API.md).

## Backup

Tela **Backup** (administradores):

- **Fazer backup agora:** gera e já baixa o arquivo no computador onde a tela está aberta.
- **Backup automático:** escolha dias da semana e horários (horário de Brasília) e quantos backups guardar.
- **Senha de proteção:** com senha, o backup é cifrado (`.gnrebak`). É recomendado, porque o
  backup inclui o certificado A1. Sem a senha não é possível restaurar.
- **Restaurar:** envie um arquivo de backup para substituir todos os dados (guias, empresas,
  usuários, tokens e certificados).
- Os arquivos ficam em `data/backups`, dentro do volume persistente `gnre_certs` (`/app/data`).
  O formato não depende do banco, então serve para migrar entre servidores (Postgres ou SQLite).
