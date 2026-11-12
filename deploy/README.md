# ☁️ Infraestrutura OCI (Always Free), Redes, Segurança & Deploy — MediFlow

> **Base da infraestrutura:** Ingrid Soares (Dockerfile, compose, nginx e script de setup).  
> **Deploy real na OCI e ajuste deste guia:** André Teixeira.  
> **Escopo:** VM na Oracle Cloud Infrastructure (Always Free), rede, hardening e containers Docker.

O passo a passo completo, com o porquê de cada cuidado e as telas da OCI, está em [`docs/deploy-oci.md`](../docs/deploy-oci.md). Este arquivo é o resumo de referência da pasta `deploy/`.

---

## 1. Arquitetura em nuvem (OCI Always Free)

O MediFlow usa somente recursos da camada **Always Free** da OCI:

| Recurso OCI | Especificação | Finalidade no MediFlow |
| :--- | :--- | :--- |
| **OCI Compute** | `VM.Standard.A1.Flex` (Ampere ARM64), **1 OCPU e 5 GB** no deploy real. A imagem Docker também roda em x86_64 (`VM.Standard.E2.1.Micro`) | Hospeda os containers `mediflow-api` (FastAPI + LangGraph) e `mediflow-ui` (Streamlit) e o nginx |
| **OCI VCN** | Sub-rede pública + Internet Gateway + Security List | Controle das portas de entrada |
| **OCI Object Storage** | **5 buckets privados**, um por status (veja a seção 3) | Persistência segregada dos documentos e das decisões |

---

## 2. Redes e segurança

### 2.1. Portas

- **VCN (Security List):** libere **só** as portas TCP `22` (SSH, por chave) e `80`.
- **Firewall da VM (`iptables`):** a imagem Ubuntu da OCI já usa `iptables`, com um `REJECT` no fim da cadeia `INPUT`. As portas `80` e `443` precisam de uma regra `ACCEPT` **antes** dele, persistida com `netfilter-persistent`. O `deploy/setup_oci_vm.sh` faz isso, e só cria a regra se ela ainda não existir.
- **Portas `8000` e `8501` fechadas para a internet:** no `docker-compose.yml`, `mediflow-api` (`127.0.0.1:8000`) e `mediflow-ui` (`127.0.0.1:8501`) escutam só em loopback. A entrada é o nginx.

### 2.2. Hardening

1. **Container não-root:** o [`Dockerfile`](Dockerfile) cria o usuário `mediflow` com **uid 10001** e roda o processo com ele.
2. **Capabilities:** o [`docker-compose.yml`](docker-compose.yml) aplica `cap_drop: [ALL]` e `no-new-privileges:true` nos dois serviços.
3. **Credenciais da OCI (`~/.oci`):**
   - fica só com o perfil `MEDIFLOW`, com `key_file=~/.oci/mediflow-api.pem`;
   - é montada **somente leitura** no container da API;
   - tem permissão `700` na pasta e `600` nos arquivos, com **dono `10001:10001`** (o uid do container). O script aplica `chown -R 10001:10001 ~/.oci`.
4. **Segredos:** o [`.dockerignore`](../.dockerignore) bloqueia `.env`, `*.pem`, `*.key` e `.oci/` na imagem. O `.env` do servidor fica com permissão `600`.
5. **nginx:** [`nginx_mediflow.conf`](nginx_mediflow.conf) limita a 10 requisições por segundo por IP em `/api/`, aceita corpo de até `15M` e envia cabeçalhos de segurança.
6. **Fail2Ban:** o script o instala e o ativa para proteger o SSH.

---

## 3. Variáveis do modo `oci` e os 5 buckets

### 3.1. Variáveis do `.env` do servidor

O `.env` fica na raiz do projeto, na VM, com permissão `600`. **Nunca vai para o Git.** Copie o `.env.example` e preencha **só** estas variáveis:

| Variável | Valor | Observação |
| :--- | :--- | :--- |
| `MEDIFLOW_ARMAZENAMENTO` | `oci` | Grava no Object Storage (o padrão é `local`, em disco) |
| `OCI_AUTH` | `api_key` | Usa o perfil do `~/.oci/config` |
| `OCI_CONFIG_PROFILE` | `MEDIFLOW` | O perfil do usuário IAM `mediflow-api` |
| `OCI_BUCKET_PREFIXO` | `mediflow` | Gera os nomes dos 5 buckets |
| `LLM_MODEL` | `gemini-3.5-flash-lite` | Vazio também vale esse padrão |
| `LLM_API_KEY` | `cole-aqui-a-chave-do-gemini` | Chave do Gemini, criada no Google AI Studio |
| `MEDIFLOW_API_KEY` | `cole-aqui-a-chave-gerada-no-servidor` | **Gere no próprio servidor** (veja abaixo). Mínimo de 32 caracteres |

Gere a `MEDIFLOW_API_KEY` no servidor, **uma chave por ambiente**:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Não é preciso preencher `OCI_NAMESPACE`, `OCI_REGION` nem `OCI_BUCKET_NAME`: a API descobre o namespace sozinha, a região vem do perfil do `~/.oci/config` e ela não lê `OCI_BUCKET_NAME`.

### 3.2. Os 5 buckets

Crie, antes de subir a API, estes buckets **privados** (a API não cria buckets):

| Bucket | Recebe |
| :--- | :--- |
| `mediflow-recebidos` | Todo documento, antes de processar |
| `mediflow-rotina` | Farmácia, autorizações e prontuário |
| `mediflow-urgentes` | Fila de emergência médica |
| `mediflow-auditoria-humana` | Documentos retidos para revisão humana |
| `mediflow-rejeitados` | Documentos que a revisão humana rejeita |

O usuário IAM `mediflow-api` só tem acesso a buckets cujo nome casa com `/mediflow-*/` (veja o [guia completo](../docs/deploy-oci.md#32-o-usuário-iam-mediflow-api-e-a-política-de-menor-privilégio)).

---

## 4. Passo a passo de deploy

### Opção A: script na VM (Ubuntu 24.04 LTS)

Conectado por SSH na instância, na raiz do projeto, com o `~/.oci` e o `.env` já preparados (seção 3):

```bash
chmod +x deploy/setup_oci_vm.sh
./deploy/setup_oci_vm.sh
```

O script instala os pacotes e o Docker, ajusta as permissões do `~/.oci`, libera as portas 80 e 443 no `iptables`, configura o nginx e sobe o compose **sem `sudo`** (com `sudo`, o `${HOME}` do volume viraria `/root`). Ele **não** cria o `~/.oci` nem o `.env`.

### Opção B: Docker Compose direto

Na raiz do repositório, com o usuário no grupo `docker` (sem `sudo`):

```bash
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs -f
```

### Verificação

```bash
curl -s http://<IP-da-VM>/api/v1/saude
```

Resposta esperada: `{"status":"ok","armazenamento":"oci"}`. Os demais testes estão no [guia completo](../docs/deploy-oci.md#7-testes-de-verificação).
