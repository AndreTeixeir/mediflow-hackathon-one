#!/usr/bin/env bash
# ============================================================================
# MediFlow - Provisionamento, Firewall, Segurança e Deploy em VM OCI Always Free
# Hackathon ONE G10 - Grupo 10: MediFlow
# Responsável: Ingrid Soares (Infra OCI, Redes, Segurança & Deploy)
# Ajustes para o deploy real na OCI: André Teixeira
#
# Uso na instância OCI Compute (Ubuntu 24.04 LTS Always Free), a partir da raiz do projeto:
#   chmod +x deploy/setup_oci_vm.sh
#   ./deploy/setup_oci_vm.sh
#
# O que o script faz: instala os pacotes, instala o Docker e põe o usuário no grupo docker,
# ajusta as permissões do ~/.oci (dono uid 10001, o usuário do container), libera as portas
# 80 e 443 no iptables da VM, configura o nginx e sobe o Docker Compose SEM sudo.
# O que ele NÃO faz: criar o ~/.oci e o .env.
# Esses passos são manuais: veja deploy/README.md e docs/deploy-oci.md.
# ============================================================================

set -euo pipefail

USUARIO="${USER:-$(id -un)}"

echo "============================================================"
echo " 🏥 MediFlow - Setup Seguro de VM na Oracle Cloud (Always Free)"
echo "============================================================"

# 1. Atualização de pacotes e ferramentas de rede
# iptables-persistent traz o netfilter-persistent, que guarda as regras do firewall entre reinicializações.
echo "[1/6] Atualizando pacotes do sistema e instalando dependências básicas..."
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    fail2ban \
    nginx \
    iptables-persistent

# 2. Instalação do Docker e Docker Compose Plugin (caso ainda não instalados)
if ! command -v docker >/dev/null 2>&1; then
    echo "[2/6] Instalando Docker Engine oficial..."
    curl -fsSL https://get.docker.com | sudo sh
else
    echo "[2/6] Docker já instalado: $(docker --version)"
fi

# O usuário precisa estar no grupo docker para rodar o compose SEM sudo. Com sudo, o ${HOME} do
# docker-compose.yml viraria /root e o volume ~/.oci não seria o da conta.
if ! id -nG "$USUARIO" | tr ' ' '\n' | grep -qx docker; then
    sudo usermod -aG docker "$USUARIO"
    echo "      Usuário $USUARIO adicionado ao grupo docker."
fi

# 3. Permissões das credenciais OCI (~/.oci) e do .env
# O container roda como o usuário mediflow, uid 10001 (deploy/Dockerfile), e lê ~/.oci só para leitura.
# A pasta fica com permissão restrita (700) e dono 10001:10001: só o container a lê.
# sudo em todo o bloco: depois do chown o dono deixa de ser o usuário da VM, e o script continua repetível.
echo "[3/6] Aplicando permissões em ~/.oci (dono uid 10001) e no arquivo .env..."
sudo mkdir -p "$HOME/.oci"
sudo chmod 700 "$HOME/.oci"
sudo find "$HOME/.oci" -type f -exec chmod 600 {} +
sudo chown -R 10001:10001 "$HOME/.oci"
if [ -f ".env" ]; then
    chmod 600 ".env"
else
    echo "      ATENÇÃO: .env não encontrado na pasta atual. A API não sobe sem a MEDIFLOW_API_KEY."
fi

# 4. Firewall da VM (iptables) alinhado à Security List da VCN OCI
# A imagem Ubuntu da OCI já usa iptables, com um REJECT no fim da cadeia INPUT. As portas 80 e 443
# entram ANTES dele, e só se a regra ainda não existir. 22 já vem liberada pela imagem.
# 8000 e 8501 ficam restritas a 127.0.0.1 pelo compose.
echo "[4/6] Liberando as portas 80 e 443 no iptables e ativando o Fail2Ban..."
for porta in 80 443; do
    if ! sudo iptables -C INPUT -m state --state NEW -p tcp --dport "$porta" -j ACCEPT 2>/dev/null; then
        posicao="$(sudo iptables -L INPUT --line-numbers -n | awk '/REJECT/ {print $1; exit}')"
        sudo iptables -I INPUT "${posicao:-1}" -m state --state NEW -p tcp --dport "$porta" -j ACCEPT
        echo "      Porta $porta liberada (regra inserida na posição ${posicao:-1})."
    else
        echo "      Porta $porta já estava liberada."
    fi
done
sudo netfilter-persistent save

sudo systemctl enable --now fail2ban

# 5. Configuração do Nginx
# Se o site já tem um bloco "listen 443" (configurado à parte), ele é mantido: copiar o arquivo do
# repositório de novo apagaria esse bloco.
echo "[5/6] Configurando o Reverse Proxy Nginx..."
if [ -f "deploy/nginx_mediflow.conf" ]; then
    if sudo grep -qs "listen 443" /etc/nginx/sites-available/mediflow.conf; then
        echo "      O site do nginx já tem um bloco listen 443: mantido sem alteração."
    else
        sudo cp deploy/nginx_mediflow.conf /etc/nginx/sites-available/mediflow.conf
    fi
    sudo ln -sf /etc/nginx/sites-available/mediflow.conf /etc/nginx/sites-enabled/mediflow.conf
    sudo rm -f /etc/nginx/sites-enabled/default
    sudo nginx -t
    sudo systemctl restart nginx
fi

# 6. Subida dos containers MediFlow, SEM sudo.
# O grupo docker recém-concedido só vale em uma nova sessão; o sg roda o comando já com ele, e o
# ${HOME} continua sendo o da conta.
echo "[6/6] Iniciando os containers MediFlow com Docker Compose (sem sudo)..."
sg docker -c "docker compose -f deploy/docker-compose.yml up -d --build"
sg docker -c "docker compose -f deploy/docker-compose.yml ps"

echo "============================================================"
echo " ✅ Containers do MediFlow iniciados na OCI!"
echo " - Interface Streamlit (via Nginx): http://<IP_PUBLICO_VM>/"
echo " - API FastAPI (via Nginx):         http://<IP_PUBLICO_VM>/api/v1/saude"
echo "============================================================"
