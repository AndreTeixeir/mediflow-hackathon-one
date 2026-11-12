# ☁️ Infraestrutura OCI (Always Free), Redes, Segurança & Deploy — MediFlow

> **Responsável:** Ingrid Soares  
> **Escopo:** Configuração de VM na Oracle Cloud Infrastructure (Always Free), Redes/VCN, Hardening de Segurança, Containerização Docker e Orquestração da Aplicação.

---

## 1. Arquitetura em Nuvem (OCI Always Free)

Em conformidade com as diretrizes do **Hackathon ONE (Grupo 10)**, toda a arquitetura utiliza exclusivamente recursos da camada **100% Always Free** da Oracle Cloud Infrastructure:

| Recurso OCI | Especificação Always Free | Finalidade no MediFlow |
| :--- | :--- | :--- |
| **OCI Compute Instance** | `VM.Standard.A1.Flex` (Ampere ARM64, até 4 OCPUs e 24 GB RAM) ou `VM.Standard.E2.1.Micro` (AMD x86_64) | Hospedagem dos containers `mediflow-api` (FastAPI + LangGraph), `mediflow-ui` (Streamlit) e Reverse Proxy Nginx |
| **OCI Virtual Cloud Network (VCN)** | Sub-rede pública + Internet Gateway + Security Lists | Isolamento de rede e controle estrito de portas de entrada (Ingress Rules) |
| **OCI Object Storage** | Buckets por status (`mediflow-recebidos`, `mediflow-urgentes`, `mediflow-rotina`, `mediflow-auditoria-humana` – Always Free) | Persistência segregada de documentos e decisões clínicas |

---

## 2. Redes e Regras de Segurança (VCN Security List & Firewall)

### 2.1. Ingress Rules na VCN da OCI
No console da OCI (**Networking $\rightarrow$ Virtual Cloud Networks $\rightarrow$ Subnet $\rightarrow$ Security Lists**), libere **exclusivamente** as seguintes portas TCP:

- **Porta `22/TCP` (SSH):** Acesso administrativo restrito por chave pública SSH (`ssh-rsa` / `ed25519`).
- **Porta `80/TCP` (HTTP) / `443/TCP` (HTTPS):** Acesso controlado via Reverse Proxy Nginx (`deploy/nginx_mediflow.conf`) com rate-limiting (`10 req/s`), limite de upload (`15M`) e cabeçalhos OWASP.
- **Portas `8000` e `8501` fechadas para a internet:** No `deploy/docker-compose.yml`, os containers `mediflow-api` (`127.0.0.1:8000:8000`) e `mediflow-ui` (`127.0.0.1:8501:8501`) escutam apenas em loopback (`127.0.0.1`), evitando exposição direta da API e protegendo a cota do Free Tier na OCI.

### 2.2. Hardening Aplicado nos Containers e na VM
1. **Execução Não-Root:** O [`deploy/Dockerfile`](Dockerfile) cria e utiliza o usuário dedicado `mediflow` (`UID 10001`), impedindo execução com privilégios de `root`.
2. **Restrição de Capabilities Linux:** O [`deploy/docker-compose.yml`](docker-compose.yml) aplica `cap_drop: [ALL]` e `no-new-privileges:true` em todos os serviços.
3. **Proteção de Credenciais e Segredos:**
   - O arquivo [`.dockerignore`](../.dockerignore) bloqueia `.env`, `*.pem`, `*.key` e `.oci/` no contexto de build da imagem.
   - As credenciais do SDK da OCI (`~/.oci`) são montadas em modo **somente leitura (`:ro`)** no container da API.
   - O script [`deploy/setup_oci_vm.sh`](setup_oci_vm.sh) aplica permissões `0700` em `~/.oci` e `0600` nas chaves privadas `.pem` e no `.env`, além de ativar `ufw` e `fail2ban`.

---

## 3. Passo a Passo de Deploy

### Opção A: Deploy Automatizado em VM OCI (Ubuntu LTS)
Conectado via SSH na instância OCI Compute, clone o repositório, configure o `.env` na raiz do projeto e execute:

```bash
cp .env.example .env
# Preencha LLM_API_KEY, OCI_REGION, OCI_NAMESPACE e OCI_BUCKET_PREFIXO (padrão mediflow) no .env.
# Preencha também MEDIFLOW_API_KEY: a API não sobe sem ela. Gere com:
#   python3 -c "import secrets; print(secrets.token_urlsafe(32))"

chmod +x deploy/setup_oci_vm.sh
./deploy/setup_oci_vm.sh
```

### Opção B: Execução Direta com Docker Compose (Local ou Nuvem)
A partir da raiz do repositório:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

Para verificar os logs e o status dos containers:

```bash
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs -f
```
