# MediFlow — Como rodar o projeto no seu computador

Este guia mostra como rodar a API e o painel **no próprio computador**, a partir da `main`. Cada passo traz os comandos do **Windows (PowerShell)** e do **Linux**. Use os do seu sistema.

> O MediFlow roda em duas partes: a **API** (FastAPI) e o **painel** (Streamlit). Você precisa de **dois terminais abertos ao mesmo tempo**, um para cada, e os dois precisam do `.venv` ativado.

## Por que rodar localmente

O ambiente na nuvem (OCI) está na conta do autor e usa credenciais que **não são compartilhadas**. Você **não precisa da OCI**: no modo `local`, o projeto grava tudo numa pasta do seu próprio computador e funciona por completo, com a API, o painel, a auditoria humana e o histórico.

Duas chaves entram na configuração, e **cada pessoa cria as suas**:

| Chave | Para que serve | Quem cria | Obrigatória? |
|---|---|---|---|
| `LLM_API_KEY` | A IA (Gemini) ler e classificar os documentos | **Cada pessoa cria a sua**, no Google AI Studio | Não. Sem ela, entra o motor determinístico local |
| `MEDIFLOW_API_KEY` | Proteger a API: só quem tem a chave a chama | **Cada ambiente gera a própria**, no próprio computador (passo 7) | Sim. Sem ela, a API não sobe |
| Credenciais da OCI | Gravar na nuvem | Só quem tem a conta da OCI | Não. Localmente, use `MEDIFLOW_ARMAZENAMENTO=local` |

Nunca reaproveite a `MEDIFLOW_API_KEY` de outro ambiente (o servidor, o computador de outra pessoa): uma chave por ambiente permite trocar ou revogar uma sem afetar as outras.

## Visão geral

1. Instalar os pré-requisitos (Python 3.13 e Git).
2. Baixar o código.
3. Criar e ativar o ambiente virtual (`.venv`).
4. Instalar as dependências.
5. Criar o arquivo `.env`.
6. Criar a sua chave do Gemini (opcional).
7. Gerar a sua chave de acesso da API.
8. Preencher o `.env`.
9. Subir a API (terminal 1).
10. Subir o painel (terminal 2).
11. Testar com os documentos de exemplo.
12. Rodar os testes automáticos (opcional).

---

## Passo 1 — Pré-requisitos

O projeto usa **Python 3.13** (o mesmo do `deploy/Dockerfile` e do CI) e **Git**.

Windows (PowerShell):

```powershell
py -3.13 --version
git --version
```

Linux:

```bash
python3.13 --version
git --version
```

Se o Python 3.13 não aparecer, instale antes de continuar:

- **Windows:** baixe em python.org e, na instalação, marque **"Add python.exe to PATH"**.
- **Linux:** instale pelo gerenciador de pacotes do seu sistema.

## Passo 2 — Baixar o código

**Quem ainda não tem o projeto:**

```bash
git clone https://github.com/No-Country-simulation/g10-Brasil27.git
cd g10-Brasil27
```

**Quem já tem o projeto clonado:** entre na pasta e traga a versão atual da `main`:

```bash
git checkout main
git pull
```

Os comandos de Git são iguais no Windows e no Linux. Todos os comandos daqui em diante são rodados **na pasta raiz do projeto** (`g10-Brasil27`).

## Passo 3 — Criar e ativar o ambiente virtual (`.venv`)

O `.venv` guarda as bibliotecas do projeto separadas do resto do computador.

Windows (PowerShell):

```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
```

Linux:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
```

Quando o `.venv` está ativo, o terminal mostra **`(.venv)`** no começo da linha.

**Windows: se aparecer um erro dizendo que a execução de scripts está desabilitada,** libere só para esta janela do PowerShell e ative de novo:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

Essa liberação vale só para a janela aberta e some quando ela é fechada.

**Toda vez que abrir um terminal novo para o projeto,** ative o `.venv` de novo (só o comando de ativação, sem criar outro).

## Passo 4 — Instalar as dependências

Com o `.venv` ativo, o comando é igual no Windows e no Linux:

```bash
pip install -r requirements.txt
```

## Passo 5 — Criar o arquivo `.env`

O `.env` guarda as configurações e as chaves **só no seu computador**. Ele **nunca vai para o Git**: o `.gitignore` já bloqueia. O ponto de partida é o modelo `.env.example`, que vem no repositório sem nenhum valor.

Windows (PowerShell):

```powershell
Copy-Item .env.example .env
notepad .env
```

Linux:

```bash
cp .env.example .env
nano .env
```

Deixe o arquivo aberto. Ele será preenchido no passo 8, depois de criar as chaves.

## Passo 6 — Criar a sua chave do Gemini (opcional)

A chave do Gemini permite que a IA leia e classifique os documentos. **Cada pessoa cria a sua.**

1. Acesse **aistudio.google.com/apikey** com a sua conta Google.
2. Clique em **"Create API key"** e escolha (ou crie) um projeto.
3. Copie a chave.

Por que cada pessoa precisa da própria chave:

- **A cota gratuita é contada por projeto do Google**, e não por chave. Se todos usarem o mesmo projeto, a cota acaba mais rápido para todo mundo.
- **Uma chave é uma senha.** Não mande por WhatsApp nem pelo chat, e nunca coloque no código ou no Git.

**A chave do Gemini é opcional.** Sem ela, o projeto usa o **motor determinístico**, um classificador local por regras (`extrair_dados_heuristico_fallback`, em `agente/visao_multimodal.py`) que não chama a IA. Os três cenários de exemplo funcionam do mesmo jeito, mas a leitura é mais simples: um PDF com camada de texto ainda tem o texto extraído, mas **PDF escaneado e imagem só são lidos pelo Gemini**.

## Passo 7 — Gerar a sua chave de acesso da API

A API **não sobe sem uma chave de acesso** (`MEDIFLOW_API_KEY`) de pelo menos 32 caracteres. Toda chamada à API precisa mandar essa chave no cabeçalho `X-API-Key`. O painel lê a mesma chave do `.env` e manda sozinho, então você não precisa digitá-la em lugar nenhum.

Gere a sua com o comando abaixo (igual no Windows e no Linux, com o `.venv` ativo):

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Ele imprime uma sequência aleatória de 43 caracteres. Copie essa sequência. Ela também é uma senha: é só sua, **não é a mesma do servidor nem a de outra pessoa**.

## Passo 8 — Preencher o `.env`

No `.env` aberto no passo 5, preencha **só estas quatro linhas** e deixe todas as outras vazias:

```
LLM_API_KEY=cole-aqui-a-sua-chave-do-gemini
LLM_MODEL=gemini-3.5-flash-lite
MEDIFLOW_ARMAZENAMENTO=local
MEDIFLOW_API_KEY=cole-aqui-a-chave-gerada-no-passo-7
```

Cuidados:

- **Sem aspas e sem espaços** antes ou depois do `=`.
- **Não mexa nas variáveis da OCI** (`OCI_...`): no modo `local`, elas não são usadas.
- **O modelo é o `gemini-3.5-flash-lite`**, que também é o padrão quando `LLM_MODEL` fica vazio. O `gemini-2.5-flash` não está disponível para chaves novas e responde 404.
- Sem chave do Gemini, deixe `LLM_API_KEY=` vazio, e o motor determinístico assume.

Salve e feche o arquivo.

## Passo 9 — Subir a API (terminal 1)

No primeiro terminal, com o `.venv` ativo e na pasta raiz do projeto (igual no Windows e no Linux):

```bash
python -m uvicorn api.main:app --reload
```

A API está no ar quando aparece **`Application startup complete.`** Deixe esse terminal aberto.

Para conferir, abra no navegador:

**http://localhost:8000/api/v1/saude**

A resposta precisa ser:

```json
{"status":"ok","armazenamento":"local"}
```

Essa é a única rota que dispensa a chave de acesso.

## Passo 10 — Subir o painel (terminal 2)

Abra um **segundo terminal**, entre na pasta do projeto e **ative o `.venv`** (passo 3). Depois rode (igual no Windows e no Linux):

```bash
python -m streamlit run interface/app.py
```

O painel abre no navegador em **http://localhost:8501**. Se não abrir sozinho, digite o endereço. O painel encontra a API em `http://localhost:8000` sem nenhuma configuração extra (é o valor padrão quando `MEDIFLOW_API_URL` está vazia).

## Passo 11 — Testar com os documentos de exemplo

A pasta `dados_teste` tem três cenários fictícios. Em cada `exemplo.json`, o texto do documento está no campo `documento_texto` de `requisicao_entrada`.

| Pasta | Destino esperado | Bucket |
|---|---|---|
| `dados_teste/1_rotina` | `Prontuario_Eletronico_do_Paciente` | `mediflow-rotina` |
| `dados_teste/2_urgencia` | `Fila_Emergencia_Medica` | `mediflow-urgentes` |
| `dados_teste/3_auditoria_humana` | `Fila_Revisao_Humana` (fica retido) | `mediflow-auditoria-humana` |

No painel:

1. Aba **"📤 Envio e Triagem de Documentos"**: escolha **"Texto do documento"**, cole o texto de um dos exemplos e clique em **"🚀 Processar Triagem Automática"**. Também é possível escolher **"Enviar arquivo PDF ou imagem"** (até 10 MB).
2. Cada envio precisa de um **ID diferente**. O mesmo ID de novo dá erro 409. Use o botão **"Gerar novo ID"** a cada envio.
3. Aba **"🔍 Fila de Auditoria Clínica (Human-in-the-Loop)"**: o documento retido aparece aqui. Escolha o destino (**Rotina**, **Urgente** ou **Rejeitar**), escreva o seu nome e a justificativa (de 5 a 500 caracteres) e clique em **"✅ Registrar decisão"**.
4. Aba **"📊 Histórico & Buckets OCI"**: mostra os documentos processados. Apesar do nome da aba, no modo `local` tudo fica gravado na pasta **`armazenamento_local/`** do projeto, que também não vai para o Git.

**Use somente dados fictícios.** Nunca envie documentos de pacientes reais.

## Passo 12 — Rodar os testes automáticos (opcional)

Com o `.venv` ativo, num terminal livre (igual no Windows e no Linux):

```bash
python -m pytest -q
```

Todos os testes devem passar. **Os testes nunca chamam o Gemini de verdade**, mesmo com a chave no `.env` (um `conftest.py` esvazia as variáveis da chave em cada teste), então não gastam a sua cota.

Se o seu `.env` estiver no modo `oci`, force o modo local só para os testes:

- Windows (PowerShell): `$env:MEDIFLOW_ARMAZENAMENTO="local"; python -m pytest -q`
- Linux: `MEDIFLOW_ARMAZENAMENTO=local python -m pytest -q`

---

## Problemas comuns

**A API não sobe e fala da `MEDIFLOW_API_KEY`.**
A chave está faltando no `.env` ou tem menos de 32 caracteres. Gere de novo (passo 7), cole no `.env` e suba a API outra vez.

**O painel mostra erro 401 (acesso negado).**
O painel e a API estão usando chaves diferentes. Isso acontece quando o `.env` foi alterado e só um dos dois foi reiniciado. Pare os dois (Ctrl+C) e suba de novo: primeiro a API, depois o painel.

**O painel diz "Não foi possível conectar à API".**
A API não está rodando no terminal 1, ou caiu. Confira a saúde em http://localhost:8000/api/v1/saude.

**Mudei o `.env` e nada mudou.**
O `.env` só é lido quando a API e o painel começam. Reinicie os dois. Além disso, **uma variável já definida no terminal vale mais que o `.env`**. Se você definiu, por exemplo, `LLM_API_KEY` direto no terminal, remova e reinicie:

- Windows (PowerShell): `Remove-Item Env:LLM_API_KEY`
- Linux: `unset LLM_API_KEY`

**O Gemini deu erro (404, 429 ou 503).**

- 404: o modelo não está disponível para a sua chave. Use `gemini-3.5-flash-lite`.
- 429: a cota gratuita do dia ou do minuto acabou. Esse erro não é repetido, para não gastar mais cota.
- 503 (e 500 e 504): o Gemini está sobrecarregado ou instável. A API tenta até 3 vezes, com espera de 2 e 4 segundos, antes de desistir.

Nos três casos, **o documento é processado mesmo assim**, pelo motor determinístico. O terminal da API mostra o aviso `Falha na chamada ao modelo Gemini`.

**A porta 8000 ou 8501 está ocupada.**
Ainda existe outra API ou outro painel rodando. Feche o terminal antigo ou pare com Ctrl+C e suba de novo.

**Erro 409 ao enviar.**
Esse ID já foi usado. Clique em "Gerar novo ID".

**Erro 413 ao enviar.**
O corpo da requisição passa de 15 MB. No painel, o arquivo é limitado a 10 MB, porque o Base64 aumenta o tamanho em cerca de 33%.

**Erro 429 ao enviar.**
A API tem um teto diário de 100 documentos e 300 MB. O teto zera à meia-noite (horário de Brasília) ou quando a API reinicia.

**Quero começar do zero, sem documentos.**
Pare a API e apague a pasta `armazenamento_local/`.

---

## Referência rápida da API e do armazenamento

### Modos de armazenamento

A API grava cada documento recebido e a resposta da triagem. Onde isso é gravado depende da variável `MEDIFLOW_ARMAZENAMENTO`:

| Modo | Onde grava | Credenciais | Quem usa |
|------|------------|-------------|----------|
| `local` (padrão) | Pasta `armazenamento_local/<bucket>/` no disco | Nenhuma | Quem desenvolve e o CI |
| `oci` | Buckets do OCI Object Storage | Perfil do `~/.oci/config` (`OCI_AUTH=api_key`) ou da VM (`OCI_AUTH=instance_principal`) | Quem tem acesso à conta da OCI |

Em cada modo, os objetos ficam em cinco buckets (ou cinco pastas, no modo `local`). O prefixo é `OCI_BUCKET_PREFIXO` (padrão `mediflow`):

| Bucket | O que recebe |
|--------|--------------|
| `mediflow-recebidos` | Todo documento recebido, antes de processar |
| `mediflow-urgentes` | Resposta dos casos que vão para a fila de emergência |
| `mediflow-rotina` | Resposta dos casos de farmácia, autorizações e prontuário |
| `mediflow-auditoria-humana` | Resposta dos casos retidos para revisão humana |
| `mediflow-rejeitados` | Documentos que a revisão humana rejeita (`Documento_Rejeitado`, status `rejeitado`) |

No modo `oci`, os buckets precisam existir antes. Cada objeto se chama `<documento_id>.json` e **nunca é sobrescrito**: enviar o mesmo `documento_id` duas vezes devolve `409`. Se a gravação falhar (`503`), o reenvio do mesmo `documento_id` funciona, porque a entrada parcial é desfeita. Todas as variáveis estão comentadas no `.env.example`.

### Endpoints

| Método | Rota | O que faz |
|--------|------|-----------|
| `GET` | `/api/v1/saude` | Responde `{"status":"ok","armazenamento":"local"}` (ou `"oci"`). Não exige chave |
| `POST` | `/api/v1/triagem` | Recebe um `DocumentoEntrada` e devolve a `TriagemResposta` |
| `GET` | `/api/v1/auditoria/pendentes` | A fila de revisão humana: do documento que espera há mais tempo para o mais novo, até 50 |
| `POST` | `/api/v1/auditoria/{documento_id}/decisao` | A decisão de quem revisa: `{"destino": "rotina" \| "urgente" \| "rejeitado", "revisor": "...", "justificativa": "..."}` |
| `GET` | `/api/v1/documentos` | O histórico dos buckets de rotina, urgentes, auditoria humana e rejeitados, do mais novo, até 50 |

Respostas de erro da triagem: `422` (`documento_id` fora do padrão `[A-Za-z0-9-]{1,64}`, sem `documento_texto` e sem `documento_base64`, `tipo_arquivo` fora de PDF/PNG/JPG/JPEG/WEBP/TXT, Base64 inválido, ou PDF/imagem que não abre), `409` (`documento_id` já recebido) e `503` (falha ao gravar no armazenamento).

Erros da decisão: `404` (o documento não está pendente, inclusive se já foi decidido), `409` (o documento já existe no bucket de destino), `422` (`destino` fora de `rotina`/`urgente`/`rejeitado`, `revisor` vazio ou com mais de 80 caracteres, `justificativa` com menos de 5 ou mais de 500, ou `documento_id` fora do padrão) e `503` (falha no armazenamento).

### Auditoria humana

A fila e o histórico devolvem no máximo 50 itens e ficam em **cache por 60 segundos** (cada item é uma leitura no armazenamento, e na OCI o Always Free tem 50 mil requisições por mês). Uma triagem ou uma decisão registrada limpa o cache, então o que você acabou de fazer aparece na hora; a mudança feita por outra pessoa pode levar até 60 s. O cache é em memória e vale para a API com um worker, como roda no deploy. O painel tem ainda o seu próprio cache, de 30 s.

A decisão move o documento de `mediflow-auditoria-humana` para `mediflow-rotina` (o destino segue o tipo do documento: farmácia, auditoria de autorizações ou prontuário), `mediflow-urgentes` (`Fila_Emergencia_Medica`, com o alerta ao plantão) ou `mediflow-rejeitados`. O JSON ganha o campo `auditoria_humana`, com quem decidiu, quando (UTC), o quê e por quê, **e também o destino e a justificativa que o grafo tinha decidido**, para a trilha mostrar o que a IA decidiu e o que a pessoa mudou.

### Chave de acesso e limites

- **Chave de acesso:** toda requisição precisa do cabeçalho `X-API-Key` igual a `MEDIFLOW_API_KEY`. A única exceção é `GET /api/v1/saude` (o healthcheck do Docker). Sem a chave certa, a resposta é `401` e nada é gravado. Isso vale também para `/docs`: o navegador não manda o cabeçalho, então teste pelo `curl` ou por uma extensão que o envie.
- **A API não sobe sem a chave**, nem com menos de 32 caracteres. Só para desenvolvimento, `MEDIFLOW_API_AUTH=desligada` sobe a API sem chave, com um aviso no log.
- **Tamanho:** o corpo da requisição tem no máximo 15 MB (o mesmo `client_max_body_size 15M` do nginx); acima disso, `413`.
- **Tetos por dia da triagem:** 100 documentos (`MEDIFLOW_TETO_DIARIO_DOCUMENTOS`) e 300 MB (`MEDIFLOW_TETO_DIARIO_MB`). Ao atingir um, `429`, sem gravar nada e sem chamar o Gemini. A contagem fica em memória e zera à meia-noite de `America/Sao_Paulo` e quando a API reinicia.

| Código | Quando |
|--------|--------|
| `401` | `X-API-Key` ausente ou diferente de `MEDIFLOW_API_KEY` |
| `413` | Corpo acima de 15 MB |
| `429` | Teto diário de documentos ou de MB atingido |
| `503` | Configuração de segurança inválida em tempo de requisição (por exemplo, um teto que não é número) |

### Exemplo com `curl` (Linux)

Com a API no ar, o exemplo envia o cenário de urgência. O `documento_id` muda para `TESTE-001`; para repetir o teste, mude o número. A variável `MEDIFLOW_API_KEY` precisa estar no seu terminal:

```bash
export MEDIFLOW_API_KEY="cole-aqui-a-sua-chave"
python -c "
import json
d = json.load(open('dados_teste/2_urgencia/exemplo.json', encoding='utf-8'))['requisicao_entrada']
d['documento_id'] = 'TESTE-001'
print(json.dumps(d))
" | curl -s -X POST http://localhost:8000/api/v1/triagem \
  -H "X-API-Key: $MEDIFLOW_API_KEY" \
  -H "Content-Type: application/json" --data-binary @-
```

A resposta traz `decisao_roteamento.destino_principal = "Fila_Emergencia_Medica"` e `armazenamento_oci.bucket = "mediflow-urgentes"`.

### Estrutura do repositório

```text
g10-Brasil27/
├── contratos/       # Modelos Pydantic de entrada e saída (contrato de dados)
├── regras/          # Regras de consistência, score de confiança e critérios de urgência
├── agente/          # Grafo de decisão (LangGraph) e leitura de documentos (texto / PDF / imagem)
├── prompts/         # Prompts de classificação e extração
├── api/             # FastAPI: triagem, auditoria, chave de acesso, limites e cache
├── armazenamento/   # Persistência no OCI Object Storage (Always Free) e no disco
├── interface/       # Painel Streamlit (envio, fila de auditoria e histórico)
├── dados_teste/     # Cenários da demonstração (somente dados fictícios)
├── deploy/          # Dockerfile, compose, nginx e script de setup da VM na OCI
├── docs/            # Esta documentação
└── tests/           # Testes automatizados (pytest)
```
