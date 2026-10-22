# API de geração de guias DIFAL (GNRE / DARE-SP / DUA-ES)

Integração para o ERP enviar os dados de cada NF-e e receber as guias de recolhimento
do DIFAL (EC 87/2015) prontas: código de barras, linha digitável e, quando a UF aceita, PIX.

- Base URL: `http://<servidor>:<porta>` (ex.: `http://192.168.0.110:8002`)
- Formato: JSON (UTF-8)
- Documentação interativa: `/docs` (Swagger) e `/redoc`
- Uma requisição = uma NF-e. Para várias notas, faça uma chamada por nota (podem ser paralelas).

> A API não tem autenticação própria. Mantenha o servidor acessível apenas na rede interna.

---

## Pré-requisitos (uma vez)

1. Cadastrar a empresa emitente na aba **Empresas** da interface, com endereço e
   **certificado A1 (e-CNPJ)**. O certificado é usado no WebService GNRE e no DUA-ES.
2. CNPJ habilitado no WebService do portal GNRE
   (gnre.pe.gov.br → Automação → *Solicitar uso do WebService*), em produção e/ou homologação.
3. Variável `GNRE_AMBIENTE`: `1` = produção (guias reais), `2` = homologação (testes, não pagar).

Para ES (DUA-e) o CNPJ também precisa estar autorizado pela SEFAZ-ES (suare@sefaz.es.gov.br).

---

## Como o sistema escolhe o tipo de guia

| UF de destino | Guia | Como é gerada |
|---|---|---|
| SP | DARE-SP | Automação do portal DareICMS. O portal exige CAPTCHA, então normalmente a guia volta como `pendente_webservice` com o XML para upload manual em https://www4.fazenda.sp.gov.br/DareICMS/GnreLote |
| ES | DUA-e | WebService da SEFAZ-ES (serviço 3867), com PIX |
| Demais UFs | GNRE | WebService nacional (receita 100102 DIFAL e 100129 FCP) |

Nas UFs do GNRE as regras de preenchimento (documento de origem, referência, campos extras,
detalhamento, FCP no mesmo guia ou em guia separada) são lidas do próprio portal GNRE
(serviço GnreConfigUF, cache de 24h). O ERP não precisa saber essas regras.

---

## 1. Gerar guias — `POST /api/gerar/json`

### Exemplo de requisição (formato recomendado)

```json
{
  "cnpj_emit": "92613413000160",
  "razao_social_emit": "CASA DAS FECHADURAS ADROFECHA LTDA",
  "uf_emit": "RS",
  "cpf_dest": "12345678909",
  "nome_dest": "JOAO DA SILVA",
  "uf_dest": "MG",
  "municipio_codigo_dest": "3106200",
  "chave_nfe": "43261092613413000160550010000123451000123459",
  "n_nf": "12345",
  "serie": "1",
  "dh_emi": "2026-10-20T10:00:00-03:00",
  "v_difal": 60.00,
  "v_fcp": 20.00,
  "data_pagamento": "2026-10-23"
}
```

```bash
curl -X POST http://192.168.0.110:8002/api/gerar/json \
  -H "Content-Type: application/json" \
  -d @nota.json
```

### Campos

**Obrigatórios**

| Campo | Tipo | Origem na NF-e (XML) | Descrição |
|---|---|---|---|
| `cnpj_emit` | texto | `emit/CNPJ` | CNPJ do emitente, só dígitos |
| `razao_social_emit` | texto | `emit/xNome` | Razão social do emitente |
| `uf_emit` | texto (2) | `emit/enderEmit/UF` | UF do emitente |
| `cpf_dest` **ou** `cnpj_dest` | texto | `dest/CPF` ou `dest/CNPJ` | Documento do consumidor final, só dígitos |
| `nome_dest` | texto | `dest/xNome` | Nome do destinatário (até 60 caracteres são usados) |
| `uf_dest` | texto (2) | `dest/enderDest/UF` | UF que recebe o DIFAL |
| `v_difal` **ou** `v_bc_difal` | número | `total/ICMSTot/vICMSUFDest` | Valor do DIFAL (ver "Valores" abaixo) |

**Necessários na prática** (a maioria das UFs rejeita sem eles)

| Campo | Tipo | Origem na NF-e (XML) | Descrição |
|---|---|---|---|
| `chave_nfe` | texto (44) | `infNFe/@Id` sem o prefixo `NFe` | Chave de acesso. Vai como documento de origem ou campo extra, conforme a UF |
| `n_nf` | texto | `ide/nNF` | Número da nota |
| `dh_emi` | data/hora | `ide/dhEmi` | Emissão. Define mês/ano de referência e a "data de emissão" exigida por algumas UFs (ex.: RJ) |

**Opcionais**

| Campo | Tipo | Origem na NF-e (XML) | Descrição |
|---|---|---|---|
| `v_fcp` | número | `total/ICMSTot/vFCPUFDest` | Valor do FCP. O sistema decide se vai no mesmo guia (RJ, RO, SE) ou em guia separada (receita 100129) |
| `serie` | texto | `ide/serie` | Série da nota (usada em observações) |
| `municipio_codigo_dest` | texto (7) | `dest/enderDest/cMun` | Código IBGE do município de destino. Recomendado |
| `data_pagamento` | data `AAAA-MM-DD` | — | Vencimento/pagamento. Padrão: próximo dia útil. Não use data passada |
| `ie_emit`, `*_emit` de endereço | texto | `emit/enderEmit/*` | Só se a empresa **não** estiver cadastrada; com cadastro, o endereço vem da aba Empresas |
| `logradouro_dest`, `numero_dest`, `bairro_dest`, `cep_dest`, `municipio_nome_dest` | texto | `dest/enderDest/*` | Endereço do destinatário (informativo) |

**Valores: duas formas**

1. **Recomendado:** enviar `v_difal` (e `v_fcp`, se houver) exatamente como na NF-e.
   O sistema não recalcula nada, então a guia bate com a nota.
2. Enviar `v_bc_difal` (base) e deixar o sistema calcular com
   `aliq_interna_dest`, `aliq_interestadual` e `aliq_fecp`. Sem essas alíquotas
   ele usa a tabela padrão por UF e **FCP = 0**. Esse cálculo é por fora (base única) e
   pode divergir de UFs que usam base dupla, por isso prefira a forma 1.

**Ajustes de GNRE** (raramente necessários — só se a UF exigir algo que o sistema não consegue preencher)

| Campo | Exemplo | Quando usar |
|---|---|---|
| `detalhamento_receita` | `"000055"` | UF exige detalhamento e há mais de uma opção. O sistema já escolhe a opção de DIFAL/FCP automaticamente |
| `produto` | `"89"` | UF exige o produto da mercadoria |
| `convenio` | `"EC 87/2015"` | Texto do campo convênio (até 30 caracteres) |
| `campos_extras` | `{"117": "2026-10-20"}` | Valor de um campo extra pelo código; sobrescreve o preenchimento automático |

Os códigos aceitos por UF estão em `GET /api/gnre/config/{uf}/{receita}` (seção 3).

### Resposta

```json
{
  "chave_nfe": "43261092613413000160550010000123451000123459",
  "uf_emit": "RS",
  "uf_dest": "MG",
  "n_nf": "12345",
  "emitente": "CASA DAS FECHADURAS ADROFECHA LTDA",
  "destinatario": "JOAO DA SILVA",
  "calculo": { "v_difal": "60.00", "v_fecp": "20.00", "v_total": "80.00", "...": "..." },
  "guias": [
    {
      "tipo": "GNRE",
      "uf": "MG",
      "receita_codigo": "100102",
      "receita_descricao": "ICMS DIFAL EC 87/2015 – MG",
      "valor": "60.00",
      "data_vencimento": "23/10/2026",
      "codigo_barras": "85830000000600003022629601000000941554016800",
      "linha_digitavel": "858300000009600003022623960100000097415540168002",
      "qrcode_pix": null,
      "numero_guia": "0000009415540168",
      "numero_recibo": "26000000039419",
      "status": "gerada",
      "mensagem": "Lote Processado com sucesso",
      "gnre_xml": "<?xml ...>"
    },
    { "receita_codigo": "100129", "valor": "20.00", "status": "gerada", "...": "..." }
  ],
  "portal_url": "https://www.gnre.pe.gov.br:444/gnre/portal/GNRE_Principal.jsp"
}
```

Uma NF-e pode gerar **uma ou duas guias** (DIFAL e FCP separados). Trate a lista `guias`.

| Campo da guia | Descrição |
|---|---|
| `status` | Ver tabela abaixo |
| `linha_digitavel` / `codigo_barras` | Para pagamento no banco |
| `qrcode_pix` | PIX copia-e-cola, quando a UF aceita PIX |
| `numero_guia` | Nosso número da GNRE |
| `numero_recibo` | Recibo do lote no portal GNRE — usado para reconsultar |
| `mensagem` | Retorno do portal ou motivo do erro |
| `gnre_xml` | XML enviado (para auditoria ou upload manual) |
| `pdf_base64` | PDF da guia (DARE-SP) |

| `status` | Significado | O que fazer |
|---|---|---|
| `gerada` | Guia emitida | Pagar com a linha digitável / PIX |
| `processando` | A UF ainda está processando (pode levar até 1h) | Consultar depois com `GET /api/gnre/resultado/{numero_recibo}` |
| `pendente_webservice` | Não foi possível enviar (sem certificado, portal fora, CAPTCHA SP) | Usar `gnre_xml` no portal indicado em `mensagem` |
| `erro` | Rejeitada pelo portal/UF ou dado faltando | Ler `mensagem`, corrigir e reenviar |

Tempo de resposta: cerca de **30 segundos** por NF-e (o manual GNRE exige aguardar 30s
antes de consultar o resultado). Configure o timeout do cliente HTTP para **pelo menos 120s**.

### Erros HTTP

| HTTP | Quando |
|---|---|
| 422 | JSON inválido ou campo obrigatório ausente. `detail[].msg` explica o campo |
| 500 | Falha inesperada; `detail` traz a mensagem |

---

## 2. Reconsultar lote — `GET /api/gnre/resultado/{numero_recibo}`

Para guias que voltaram com `status: "processando"`.

```bash
curl "http://192.168.0.110:8002/api/gnre/resultado/26000000039396?cnpj=92613413000160"
```

```json
{
  "status": "gerada",
  "codigo_barras": "...",
  "linha_digitavel": "...",
  "qrcode_pix": null,
  "numero_guia": "...",
  "mensagem": "Lote Processado com sucesso",
  "numero_recibo": "26000000039396"
}
```

`cnpj` é opcional: indica qual certificado usar (padrão: primeira empresa com certificado).

---

## 3. Regras da UF — `GET /api/gnre/config/{uf}/{receita}`

Mostra o que o portal GNRE exige para a receita na UF (`100102` = DIFAL, `100129` = FCP),
no ambiente configurado: detalhamentos, produtos, campos extras, se aceita PIX, se o FCP vai
no mesmo guia (`exige_valor_fecp`). `existe: false` = a UF não emite essa receita pelo GNRE.

```bash
curl "http://192.168.0.110:8002/api/gnre/config/MT/100102"
```

---

## 4. Alternativa: enviar o XML da NF-e — `POST /api/gerar/xml`

Se for mais simples para o ERP, envie o próprio XML autorizado (`nfeProc` ou `NFe`)
como arquivo multipart. Todos os campos acima são lidos do XML.

```bash
curl -X POST http://192.168.0.110:8002/api/gerar/xml -F "file=@43261092613413000160550010000123451000123459-procNFe.xml"
```

A resposta é igual à de `/api/gerar/json`.

---

## Outros endpoints

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/status` | Saúde e ambiente (`producao` / `homologacao`) |
| GET | `/api/empresas` | Empresas cadastradas e validade do certificado |
| GET | `/api/empresas/{cnpj}/cert-status` | Situação do certificado (dias restantes) |
| GET | `/api/ufs`, `/api/uf/{uf}` | Alíquotas padrão por UF |
