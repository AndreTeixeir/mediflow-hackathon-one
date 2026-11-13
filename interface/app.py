"""
MediFlow - Interface Streamlit de Triagem e Auditoria Clínica (Human-in-the-Loop)
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: interface/app.py
Execução a partir da raiz: python -m streamlit run interface/app.py

Tudo vem da API (MEDIFLOW_API_URL): o painel não lê arquivos de exemplo nem roda o grafo. A fila de auditoria,
o histórico e os contadores são lidos da API, e a decisão de quem revisa é gravada por ela, então
continua lá depois de recarregar a página. Se a API falhar, a tela mostra o erro e não simula resultado.
"""

import base64
import uuid
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

from contratos.modelos import CanalOrigem
from interface.cliente_api import TIMEOUT_DA_TRIAGEM_SEGUNDOS, ErroDaApi, chamar_api, url_da_api

load_dotenv()

# Limite do arquivo na tela, conferido antes de enviar. A API aceita 15 MB no corpo inteiro e o
# Base64 incha o arquivo em cerca de 33%: 10 MB viram ~13,4 MB e ainda cabem.
TAMANHO_MAXIMO_MB = 10
MODO_TEXTO = "Texto do documento"
MODO_ARQUIVO = "Enviar arquivo PDF ou imagem"
CANAIS_ORIGEM = [canal.value for canal in CanalOrigem]
# A fila e o histórico mudam por fora (outra pessoa decide, a API recebe um documento): o painel os
# relê a cada 30 s, e na hora quando esta sessão envia ou decide. No modo OCI, cada leitura de uma lista
# são até 50 consultas ao armazenamento: o cache poupa a cota do Always Free. A API tem o seu próprio cache
# (60 s), que protege a cota de todas as sessões juntas: uma mudança feita por outra pessoa pode levar até
# a soma dos dois para aparecer, e as gravações desta sessão aparecem na hora.
TTL_DO_CACHE_SEGUNDOS = 30
# Os destinos de um documento aprovado (o bucket de rotina), para o contador: o nome do bucket depende do
# prefixo configurado no servidor, o destino é do contrato.
DESTINOS_DE_ROTINA = {"Farmacia_Hospitalar", "Auditoria_Autorizacoes", "Prontuario_Eletronico_do_Paciente"}
# O rótulo na tela e o valor que a API espera.
DESTINOS_DA_DECISAO = {"Rotina": "rotina", "Urgente": "urgente", "Rejeitar": "rejeitado"}
# O mesmo teto das listagens da API (api/auditoria.py).
LIMITE_DA_API = 50
REVISOR_MAXIMO = 80
JUSTIFICATIVA_MINIMA = 5
JUSTIFICATIVA_MAXIMA = 500

# Configuração visual da página
st.set_page_config(
    page_title="MediFlow - Triagem & Auditoria Clínica",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Estilos visuais personalizados - Tema Clínico Moderno
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }

    /* Cabeçalho e Identidade Visual */
    .header-container {
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 12px;
        padding-bottom: 4px;
    }
    .titulo-principal {
        font-size: 2.1rem;
        font-weight: 700;
        color: #0b4f6c;
        margin-bottom: 0.15rem;
        letter-spacing: -0.02em;
    }
    .subtitulo {
        font-size: 1.0rem;
        color: #475569;
        margin-bottom: 0.5rem;
    }
    .status-badges {
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
    }
    .badge-oci {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        background: #F0FDF4;
        color: #166534;
        border: 1px solid #BBF7D0;
        padding: 5px 12px;
        border-radius: 9999px;
        font-size: 0.8rem;
        font-weight: 600;
    }
    .badge-ia {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        background: #EFF6FF;
        color: #1E40AF;
        border: 1px solid #BFDBFE;
        padding: 5px 12px;
        border-radius: 9999px;
        font-size: 0.8rem;
        font-weight: 600;
    }
    .pulse-dot {
        width: 8px;
        height: 8px;
        background-color: #22C55E;
        border-radius: 50%;
        display: inline-block;
        box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.7);
        animation: pulse-green 2s infinite;
    }
    @keyframes pulse-green {
        0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.7); }
        70% { transform: scale(1); box-shadow: 0 0 0 6px rgba(34, 197, 94, 0); }
        100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(34, 197, 94, 0); }
    }

    /* Cards de Métricas (KPIs do Topo) - Tema Escuro Integrado */
    div[data-testid="stMetric"] {
        background: #1E293B !important;
        border: 1px solid #334155 !important;
        border-radius: 10px;
        padding: 14px 16px;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
        transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    div[data-testid="stMetric"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 8px rgba(0, 0, 0, 0.3);
    }
    div[data-testid="stMetric"] label[data-testid="stMetricLabel"] {
        font-size: 0.82rem !important;
        font-weight: 600 !important;
        color: #94A3B8 !important;
        text-transform: uppercase;
        letter-spacing: 0.03em;
    }
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
        font-size: 1.85rem !important;
        font-weight: 700 !important;
        color: #F8FAFC !important;
    }

    /* Faixas de Destaque por Coluna de Métrica */
    div[data-testid="column"]:nth-of-type(1) div[data-testid="stMetric"] { border-top: 4px solid #0284C7 !important; }
    div[data-testid="column"]:nth-of-type(2) div[data-testid="stMetric"] { border-top: 4px solid #F59E0B !important; }
    div[data-testid="column"]:nth-of-type(3) div[data-testid="stMetric"] { border-top: 4px solid #EF4444 !important; }
    div[data-testid="column"]:nth-of-type(4) div[data-testid="stMetric"] { border-top: 4px solid #10B981 !important; }
    div[data-testid="column"]:nth-of-type(5) div[data-testid="stMetric"] { border-top: 4px solid #64748B !important; }

    /* Abas de Navegação */
    div[data-baseweb="tab-list"] {
        gap: 8px;
        border-bottom: 2px solid #334155;
        padding-bottom: 2px;
    }
    button[data-baseweb="tab"] {
        border-radius: 6px 6px 0 0 !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        padding: 8px 16px !important;
        color: #94A3B8 !important;
        transition: all 0.2s ease;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        color: #38BDF8 !important;
    }

    /* Botões de Ação */
    button[kind="primary"] {
        background: linear-gradient(135deg, #0284C7 0%, #0369A1 100%) !important;
        border: none !important;
        color: #FFFFFF !important;
        font-weight: 600 !important;
        border-radius: 8px !important;
        box-shadow: 0 2px 4px rgba(2, 132, 199, 0.25) !important;
        transition: all 0.2s ease !important;
    }
    button[kind="primary"]:hover {
        box-shadow: 0 4px 12px rgba(2, 132, 199, 0.35) !important;
        transform: translateY(-1px);
    }
    button[kind="secondary"] {
        border-radius: 8px !important;
        font-weight: 500 !important;
        background: #1E293B !important;
        border: 1px solid #334155 !important;
        color: #F8FAFC !important;
        transition: all 0.15s ease !important;
    }

    /* Cards e Containers em Dark Theme */
    .card-destaque {
        background-color: #1E293B;
        border-radius: 8px;
        padding: 16px;
        border-left: 5px solid #0284C7;
        color: #F8FAFC;
    }
    .card-clinico-resultado {
        background: #1E293B;
        border: 1px solid #334155;
        border-radius: 10px;
        padding: 16px 20px;
        margin-bottom: 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.2);
        color: #F8FAFC;
    }
    .tag-prioridade-urgente {
        display: inline-block;
        background: #FEE2E2;
        color: #991B1B;
        font-weight: 700;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.85rem;
    }
    .tag-prioridade-alta {
        display: inline-block;
        background: #FEF3C7;
        color: #92400E;
        font-weight: 700;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.85rem;
    }
    .tag-prioridade-rotina {
        display: inline-block;
        background: #D1FAE5;
        color: #065F46;
        font-weight: 700;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.85rem;
    }

    /* Rodapé Institucional */
    .mediflow-footer {
        margin-top: 40px;
        padding: 20px 0 10px 0;
        border-top: 1px solid #E2E8F0;
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 10px;
        color: #64748B;
        font-size: 0.82rem;
    }

    /* ========================================================================
       MODERN HEALTH TECH / SHADCN UI SYSTEM
       ======================================================================== */

    /* Container do Formulário de Auditoria Clínica */
    div[data-testid="stForm"] {
        background: #1E293B !important;
        border: 1px solid #334155 !important;
        border-radius: 14px !important;
        padding: 22px 24px !important;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3) !important;
    }

    /* Campos Desabilitados: Aparência Dark Integrada */
    div[data-testid="stTextInput"] input:disabled {
        background-color: #0F172A !important;
        color: #F8FAFC !important;
        -webkit-text-fill-color: #F8FAFC !important;
        font-weight: 600 !important;
        font-size: 0.92rem !important;
        border: 1px solid #334155 !important;
        border-radius: 8px !important;
        padding: 10px 14px !important;
    }

    /* Campos Ativos (Input e Textarea) */
    div[data-testid="stTextInput"] input:not(:disabled),
    div[data-testid="stTextArea"] textarea {
        background-color: #0F172A !important;
        border: 1px solid #334155 !important;
        border-radius: 8px !important;
        font-size: 0.95rem !important;
        color: #F8FAFC !important;
        transition: all 0.2s ease !important;
    }
    div[data-testid="stTextInput"] input:not(:disabled):focus,
    div[data-testid="stTextArea"] textarea:focus {
        border-color: #38BDF8 !important;
        box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.2) !important;
        outline: none !important;
    }

    /* Labels dos Campos */
    label[data-testid="stWidgetLabel"] p {
        font-size: 0.82rem !important;
        font-weight: 600 !important;
        color: #94A3B8 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.03em !important;
        margin-bottom: 4px !important;
    }

    /* Radio Buttons estilo Segmented Control (Pills Modernos) */
    div[data-testid="stRadio"] div[role="radiogroup"] {
        display: flex !important;
        gap: 8px !important;
        background: #0F172A !important;
        padding: 5px !important;
        border-radius: 10px !important;
        border: 1px solid #334155 !important;
    }
    div[data-testid="stRadio"] div[role="radiogroup"] label {
        flex: 1 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        background: transparent !important;
        border-radius: 8px !important;
        padding: 8px 14px !important;
        font-weight: 600 !important;
        font-size: 0.88rem !important;
        color: #94A3B8 !important;
        transition: all 0.2s ease !important;
        cursor: pointer !important;
        border: 1px solid transparent !important;
    }
    div[data-testid="stRadio"] div[role="radiogroup"] label:hover {
        background: #334155 !important;
        color: #F8FAFC !important;
    }
    /* Oculta o marcador circular de rádio nativo */
    div[data-testid="stRadio"] div[role="radiogroup"] label > div:first-child {
        display: none !important;
    }
    div[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) {
        background: #1E293B !important;
        color: #38BDF8 !important;
        border: 1px solid #0284C7 !important;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3) !important;
    }

    /* Selectbox Moderno */
    div[data-testid="stSelectbox"] > div {
        border-radius: 8px !important;
        border: 1px solid #334155 !important;
        background-color: #0F172A !important;
        color: #F8FAFC !important;
    }

    /* Alert / Warning estilo Banner Moderno */
    div[data-testid="stAlert"] {
        border-radius: 10px !important;
        border: 1px solid #92400E !important;
        background-color: rgba(69, 26, 3, 0.35) !important;
        color: #FEF3C7 !important;
        padding: 12px 16px !important;
    }

    /* Barra de Progresso com Gradiente */
    div[data-testid="stProgress"] > div {
        height: 10px !important;
        border-radius: 9999px !important;
        background-color: #334155 !important;
    }
    div[data-testid="stProgress"] > div > div {
        border-radius: 9999px !important;
        background: linear-gradient(90deg, #EF4444 0%, #F59E0B 50%, #10B981 100%) !important;
    }

    /* Card Dossiê da Auditoria (Coluna Esquerda) */
    .card-dossie-auditoria {
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 14px;
        padding: 22px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    }
</style>
""", unsafe_allow_html=True)


# ============================================================================
# DADOS DA API
# ============================================================================

@st.cache_data(ttl=TTL_DO_CACHE_SEGUNDOS, show_spinner=False)
def carregar_pendentes(url: str) -> list:
    """A fila de revisão humana. `url` entra na chave do cache: outro endereço, outra fila."""
    return chamar_api("GET", "/api/v1/auditoria/pendentes")


@st.cache_data(ttl=TTL_DO_CACHE_SEGUNDOS, show_spinner=False)
def carregar_historico(url: str) -> list:
    """O histórico dos buckets de rotina, urgentes, auditoria humana e rejeitados."""
    return chamar_api("GET", "/api/v1/documentos")


def recarregar_dados() -> None:
    """Esquece o que está em cache: a próxima leitura vai à API."""
    st.cache_data.clear()


def ler(carregar):
    """(dados, None) ou (None, erro): quem chama decide onde mostrar a mensagem."""
    try:
        return carregar(url_da_api()), None
    except ErroDaApi as erro:
        return None, erro


def mostrar_erro(erro: ErroDaApi) -> None:
    st.error(erro.mensagem)
    if erro.endereco:
        st.write(f"Endereço tentado: `{erro.endereco}`")
        st.info("Inicie a API FastAPI ou confira a variável MEDIFLOW_API_URL.")
    if erro.status == 422 and erro.corpo is not None:
        st.json(erro.corpo)


def formatar_data(data_iso: str) -> str:
    """A data que a API devolve (ISO 8601, UTC) como o painel a mostra."""
    try:
        return datetime.fromisoformat(data_iso.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M (UTC)")
    except ValueError:
        return data_iso


# ============================================================================
# ID E ARQUIVO DA ABA DE ENVIO
# ============================================================================

def gerar_novo_id() -> None:
    """Gera um ID e o mantém no estado da sessão. Roda como on_click, antes de o campo ser redesenhado."""
    st.session_state["documento_id"] = f"DOC-DEMO-{uuid.uuid4().hex[:8].upper()}"


# O ID fica na sessão: um value= com UUID novo a cada execução descartaria o que a pessoa digitou.
if "documento_id" not in st.session_state:
    gerar_novo_id()


def tipo_pela_extensao(nome_arquivo: str) -> str:
    """PDF, PNG, JPG, JPEG ou WEBP, pela extensão do arquivo enviado."""
    return nome_arquivo.rsplit(".", 1)[-1].upper()


# ============================================================================
# CABEÇALHO E CONTADORES (preenchidos no fim do script, depois de enviar ou decidir)
# ============================================================================

st.markdown("""
<div class="header-container">
    <div>
        <div class="titulo-principal">MediFlow — Triagem e Auditoria Clínica</div>
        <div class="subtitulo">Agente Autônomo para Triagem, Extração e Roteamento de Documentos Clínicos | OCI Always Free</div>
    </div>
    <div class="status-badges">
        <span class="badge-oci"><span class="pulse-dot"></span> VM OCI Always Free Ativa</span>
        <span class="badge-ia">⚡ Gemini 3.5 Flash-Lite</span>
    </div>
</div>
""", unsafe_allow_html=True)

# O Streamlit desenha na ordem do script. Os contadores ficam no topo da tela mas são preenchidos
# no fim, depois de uma triagem enviada ou de uma decisão registrada nesta mesma execução.
area_dos_contadores = st.container()

st.divider()


# ============================================================================
# ABAS DE NAVEGAÇÃO
# ============================================================================

tab_auditoria, tab_envio, tab_historico = st.tabs([
    "Fila de Auditoria Clínica (Human-in-the-Loop)",
    "Envio e Triagem de Documentos",
    "Histórico & Buckets OCI"
])


# ----------------------------------------------------------------------------
# ABA 2: ENVIO E TRIAGEM (lógica de envio da página do Guilherme, no visual da Lethicia)
# Vem primeiro no código para o envio limpar o cache antes de a fila e o histórico serem lidos.
# ----------------------------------------------------------------------------
with tab_envio:
    st.subheader("Enviar Documento para Triagem")
    st.caption("Envie o texto de um documento ou um PDF/imagem. Use apenas dados fictícios ou anonimizados.")

    modo_envio = st.radio(
        "Como deseja fornecer o documento?",
        [MODO_TEXTO, MODO_ARQUIVO],
        horizontal=True,
        key="modo_envio",
    )

    texto_input = ""
    arquivo_upload = None
    if modo_envio == MODO_TEXTO:
        texto_input = st.text_area("Texto do Documento Clínico:", height=180, key="texto_envio")
    else:
        arquivo_upload = st.file_uploader(
            "Selecione um arquivo clínico (PDF digital/digitalizado ou imagem de receita/laudo):",
            type=["pdf", "png", "jpg", "jpeg", "webp"],
            help=f"Até {TAMANHO_MAXIMO_MB} MB. Use somente documentos sintéticos ou anonimizados.",
            key="arquivo_envio",
        )

    col_id, col_novo_id, col_canal = st.columns([3, 1, 2], vertical_alignment="bottom")
    with col_id:
        doc_id_input = st.text_input(
            "ID do Documento:",
            key="documento_id",
            help="Só letras, números e hífen, até 64 caracteres. Cada envio precisa de um ID novo.",
        )
    with col_novo_id:
        st.button("Gerar novo ID", on_click=gerar_novo_id, width="stretch", key="btn_novo_id")
    with col_canal:
        canal_input = st.selectbox("Canal de Origem:", CANAIS_ORIGEM, key="canal_envio")

    if st.button("🚀 Processar Triagem Automática", type="primary", width="stretch", key="btn_processar"):
        # if/elif em vez de st.stop(): o stop interromperia o script e as outras abas sumiriam.
        if modo_envio == MODO_TEXTO and not texto_input.strip():
            st.error("Insira o texto do documento antes de enviar.")
        elif modo_envio == MODO_ARQUIVO and arquivo_upload is None:
            st.error("Selecione um arquivo antes de enviar.")
        elif arquivo_upload is not None and arquivo_upload.size > TAMANHO_MAXIMO_MB * 1024 * 1024:
            st.error(f"O arquivo passa de {TAMANHO_MAXIMO_MB} MB. Envie um arquivo menor.")
        else:
            payload = {"documento_id": doc_id_input, "canal_origem": canal_input}
            if arquivo_upload is not None:
                # A API recebe o arquivo em Base64 dentro do JSON.
                payload["tipo_arquivo"] = tipo_pela_extensao(arquivo_upload.name)
                payload["documento_base64"] = base64.b64encode(arquivo_upload.getvalue()).decode("ascii")
            else:
                payload["tipo_arquivo"] = "TXT"
                payload["documento_texto"] = texto_input

            try:
                with st.spinner("Enviando documento para a API MediFlow..."):
                    resultado = chamar_api(
                        "POST",
                        "/api/v1/triagem",
                        json=payload,
                        timeout=TIMEOUT_DA_TRIAGEM_SEGUNDOS,
                        mensagens={409: 'Já existe um documento com esse ID. Clique em "Gerar novo ID" e envie de novo.'},
                    )
            except ErroDaApi as erro:
                mostrar_erro(erro)
            else:
                st.success("✅ Triagem processada com sucesso via API FastAPI + Grafo LangGraph!")
                recarregar_dados()  # a fila, o histórico e os contadores passam a incluir este documento

                classif = resultado.get("classificacao") or {}
                roteamento = resultado.get("decisao_roteamento") or {}
                dados = resultado.get("dados_extraidos") or {}
                pac = dados.get("paciente") or {}
                med = dados.get("medico_solicitante") or {}

                prioridade = classif.get("nivel_prioridade", "N/A")
                score = classif.get("score_confianca", 0.0)
                destino = roteamento.get("destino_principal", "N/A")
                bucket = (resultado.get("armazenamento_oci") or {}).get("bucket", "N/A")

                tag_classe = (
                    "tag-prioridade-urgente" if "URG" in str(prioridade).upper() or "EMERG" in str(destino).upper()
                    else ("tag-prioridade-alta" if "ALTA" in str(prioridade).upper() else "tag-prioridade-rotina")
                )

                st.markdown(
                    f"""
                    <div class="card-clinico-resultado">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 8px;">
                            <div style="font-weight: 700; font-size: 1.05rem; color: #0F172A;">
                                📄 Resumo Clínico da Triagem — <code>{resultado.get('documento_id', '')}</code>
                            </div>
                            <div>
                                <span class="{tag_classe}">{prioridade}</span>
                            </div>
                        </div>
                        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; font-size: 0.88rem;">
                            <div>
                                <div style="color: #64748B; font-size: 0.75rem; text-transform: uppercase; font-weight: 600;">Tipo do Documento</div>
                                <div style="font-weight: 600; color: #1E293B;">{classif.get('tipo_documento', 'N/A')}</div>
                                <div style="color: #64748B; font-size: 0.75rem; margin-top: 2px;">Confiança: <b>{score * 100:.1f}%</b></div>
                            </div>
                            <div>
                                <div style="color: #64748B; font-size: 0.75rem; text-transform: uppercase; font-weight: 600;">Destino & OCI</div>
                                <div style="font-weight: 600; color: #0284C7;">{destino}</div>
                                <div style="color: #64748B; font-size: 0.75rem; margin-top: 2px;">Bucket: <code>{bucket}</code></div>
                            </div>
                            <div>
                                <div style="color: #64748B; font-size: 0.75rem; text-transform: uppercase; font-weight: 600;">Paciente</div>
                                <div style="font-weight: 600; color: #1E293B;">{pac.get('nome') or 'Não identificado'} ({pac.get('idade') or '-'} anos)</div>
                                <div style="color: #64748B; font-size: 0.75rem; margin-top: 2px;">CID: <b>{dados.get('cid_sugerido') or 'N/A'}</b></div>
                            </div>
                            <div>
                                <div style="color: #64748B; font-size: 0.75rem; text-transform: uppercase; font-weight: 600;">Médico Solicitante</div>
                                <div style="font-weight: 600; color: #1E293B;">{med.get('nome') or 'Não identificado'}</div>
                                <div style="color: #64748B; font-size: 0.75rem; margin-top: 2px;">CRM: <b>{med.get('crm') or 'N/A'}</b></div>
                            </div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                st.markdown("### Resultado da Triagem:")
                col_r1, col_r2 = st.columns(2)
                with col_r1:
                    st.json(resultado["classificacao"])
                    st.json(resultado["decisao_roteamento"])
                with col_r2:
                    st.json(resultado["dados_extraidos"])


# ----------------------------------------------------------------------------
# ABA 1: PAINEL DE AUDITORIA HUMANA (RESPONSABILIDADE DA LETHICIA)
# ----------------------------------------------------------------------------
def validar_decisao(revisor: str, justificativa: str):
    """A mensagem do que falta preencher, ou None. A API confere de novo."""
    if not revisor.strip():
        return "Informe o nome de quem revisa."
    if len(revisor.strip()) > REVISOR_MAXIMO:
        return f"O nome de quem revisa passa de {REVISOR_MAXIMO} caracteres."
    if len(justificativa.strip()) < JUSTIFICATIVA_MINIMA:
        return f"A justificativa é obrigatória e precisa ter pelo menos {JUSTIFICATIVA_MINIMA} caracteres."
    if len(justificativa.strip()) > JUSTIFICATIVA_MAXIMA:
        return f"A justificativa passa de {JUSTIFICATIVA_MAXIMA} caracteres."
    return None


def registrar_decisao(documento_id: str, destino: str, revisor: str, justificativa: str) -> None:
    """Envia a decisão. Se a API aceitar, relê a fila por ela; se não, mostra o erro e não muda nada."""
    try:
        resposta = chamar_api(
            "POST",
            f"/api/v1/auditoria/{documento_id}/decisao",
            json={"destino": DESTINOS_DA_DECISAO[destino], "revisor": revisor.strip(), "justificativa": justificativa.strip()},
        )
    except ErroDaApi as erro:
        if erro.status == 404:
            # Outra pessoa já decidiu (ou o documento saiu da fila): relê a fila e avisa.
            recarregar_dados()
            st.session_state["aviso_da_decisao"] = ("warning", f"{erro.mensagem} A fila foi atualizada.")
            st.rerun()
        mostrar_erro(erro)
        return

    destino_final = resposta["decisao_roteamento"]["destino_principal"]
    bucket = resposta["armazenamento_oci"]["bucket"]
    st.session_state["aviso_da_decisao"] = (
        "success",
        f"Documento {documento_id} encaminhado para {destino_final} (bucket {bucket}). A decisão ficou registrada.",
    )
    recarregar_dados()
    st.rerun()


with tab_auditoria:
    aviso = st.session_state.pop("aviso_da_decisao", None)
    if aviso:
        getattr(st, aviso[0])(aviso[1])

    st.button("🔄 Atualizar a fila", on_click=recarregar_dados, key="btn_atualizar")
    pendentes, erro_da_fila = ler(carregar_pendentes)

    if erro_da_fila:
        mostrar_erro(erro_da_fila)
    elif not pendentes:
        st.success("🎉 Não há documentos pendentes na fila de auditoria humana no momento!")
        st.info("Todos os laudos e receitas com alta confiabilidade foram triados e roteados automaticamente.")
    else:
        st.subheader(f"Documentos Retidos para Revisão ({len(pendentes)})")
        por_id = {p["documento_id"]: p for p in pendentes}
        documento_id = st.selectbox(
            "Selecione o caso para auditar:",
            list(por_id),
            format_func=lambda i: f"{i} — {por_id[i]['tipo_documento']} (prioridade {por_id[i]['nivel_prioridade']})",
            key="caso_em_auditoria",
        )
        doc = por_id[documento_id]
        dados_ext = doc.get("dados_extraidos") or {}
        paciente = dados_ext.get("paciente") or {}
        medico = dados_ext.get("medico_solicitante") or {}

        col_esq, col_dir = st.columns([1, 1.2])

        # Coluna da esquerda: o que o sistema decidiu e o motivo da retenção
        with col_esq:
            st.markdown("### Documento Retido")
            st.markdown(f"**ID do Documento:** `{doc['documento_id']}`")
            st.markdown(f"**Tipo:** {doc['tipo_documento']}")
            st.markdown(f"**Prioridade:** {doc['nivel_prioridade']}")
            st.markdown(f"**Entrou na fila em:** {formatar_data(doc['data'])}")

            st.warning(f"**Motivo da Retenção para Auditoria:**\n{doc['justificativa_roteamento']}")

            score = doc["score_confianca_classificacao"]
            st.markdown(f"**Score de Confiança da Extração:** `{score * 100:.1f}%`")
            st.progress(score)

        # Coluna da direita: dados extraídos (somente leitura) e a decisão de quem revisa
        with col_dir:
            st.markdown("### Formulário de Revisão Clínica")

            with st.form(key=f"form_auditoria_{documento_id}"):
                st.caption("Dados extraídos pela IA, como a API os devolveu.")
                st.warning("Correção de dados será habilitada numa próxima versão")
                c_p1, c_p2 = st.columns([2, 1])
                with c_p1:
                    st.text_input("Nome do Paciente:", value=paciente.get("nome") or "", disabled=True, key=f"nome_{documento_id}")
                with c_p2:
                    st.text_input("Idade:", value=str(paciente.get("idade") or ""), disabled=True, key=f"idade_{documento_id}")

                c_m1, c_m2 = st.columns([2, 1])
                with c_m1:
                    st.text_input("Médico Solicitante:", value=medico.get("nome") or "", disabled=True, key=f"medico_{documento_id}")
                with c_m2:
                    st.text_input("CRM com UF:", value=medico.get("crm") or "", disabled=True, key=f"crm_{documento_id}")

                st.text_input(
                    "Diagnóstico Principal:", value=dados_ext.get("diagnostico_principal") or "", disabled=True, key=f"diag_{documento_id}"
                )
                st.text_input("CID-10 Sugerido:", value=dados_ext.get("cid_sugerido") or "", disabled=True, key=f"cid_{documento_id}")

                st.markdown("**Decisão de quem revisa:**")
                destino = st.radio("Encaminhar para:", list(DESTINOS_DA_DECISAO), horizontal=True, key=f"destino_{documento_id}")
                revisor = st.text_input("Nome de quem revisa (obrigatório):", max_chars=REVISOR_MAXIMO, key="nome_do_revisor")
                justificativa = st.text_area(
                    f"Justificativa (obrigatória, de {JUSTIFICATIVA_MINIMA} a {JUSTIFICATIVA_MAXIMA} caracteres):",
                    max_chars=JUSTIFICATIVA_MAXIMA,
                    key=f"justificativa_{documento_id}",
                )

                registrar = st.form_submit_button("✅ Registrar decisão", type="primary", width="stretch")

            if registrar:
                pendencia = validar_decisao(revisor, justificativa)
                if pendencia:
                    st.error(pendencia)
                else:
                    registrar_decisao(documento_id, destino, revisor, justificativa)


# ----------------------------------------------------------------------------
# ABA 3: HISTÓRICO & BUCKETS OCI
# ----------------------------------------------------------------------------
with tab_historico:
    st.subheader("📦 Histórico dos Documentos no Armazenamento")
    st.caption("Organização segregada de arquivos conforme exigido pelo edital Always Free da Oracle.")

    st.markdown("""
    <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px;">
        <span style="background: #F1F5F9; border: 1px solid #CBD5E1; padding: 4px 8px; border-radius: 6px; font-size: 0.78rem; color: #334155;">📥 <code>mediflow-recebidos</code> (Ingestão)</span>
        <span style="background: #ECFDF5; border: 1px solid #A7F3D0; padding: 4px 8px; border-radius: 6px; font-size: 0.78rem; color: #065F46;">✅ <code>mediflow-rotina</code> (Farmácia / PEP / Autorizações)</span>
        <span style="background: #FEF2F2; border: 1px solid #FECACA; padding: 4px 8px; border-radius: 6px; font-size: 0.78rem; color: #991B1B;">🚨 <code>mediflow-urgentes</code> (Emergência)</span>
        <span style="background: #FFFBEB; border: 1px solid #FDE68A; padding: 4px 8px; border-radius: 6px; font-size: 0.78rem; color: #92400E;">🔍 <code>mediflow-auditoria</code> (Revisão Humana)</span>
        <span style="background: #F8FAFC; border: 1px solid #E2E8F0; padding: 4px 8px; border-radius: 6px; font-size: 0.78rem; color: #475569;">🚫 <code>mediflow-rejeitados</code> (Falhas / Rejeição)</span>
    </div>
    """, unsafe_allow_html=True)

    historico, erro_do_historico = ler(carregar_historico)
    if erro_do_historico:
        mostrar_erro(erro_do_historico)
    elif not historico:
        st.info("Nenhum documento no armazenamento ainda.")
    else:
        if len(historico) >= LIMITE_DA_API:
            st.caption(f"Os {LIMITE_DA_API} documentos mais recentes.")
        st.dataframe(
            [
                {
                    "Data": formatar_data(h["data"]),
                    "Documento": h["documento_id"],
                    "Status": h["status"],
                    "Prioridade": h["nivel_prioridade"],
                    "Destino": h["destino_principal"],
                    "Bucket": h["bucket"],
                }
                for h in historico
            ],
            width="stretch",
            hide_index=True,
        )


# ----------------------------------------------------------------------------
# CONTADORES DO TOPO: vêm da API, lidos depois de enviar ou decidir nesta execução
# ----------------------------------------------------------------------------
with area_dos_contadores:
    historico, _ = ler(carregar_historico)
    pendentes, _ = ler(carregar_pendentes)
    if historico is None or pendentes is None:
        st.warning("Contadores indisponíveis: a API não respondeu. O motivo aparece nas abas abaixo.")
    else:
        col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
        with col_m1:
            st.metric(label="📄 Total de Documentos", value=len(historico))
        with col_m2:
            st.metric(label="⚠️ Fila de Revisão Humana", value=len(pendentes))
        with col_m3:
            st.metric(label="🚨 Emergências Médicas", value=sum(1 for h in historico if h["destino_principal"] == "Fila_Emergencia_Medica"))
        with col_m4:
            st.metric(label="✅ Rotina", value=sum(1 for h in historico if h["destino_principal"] in DESTINOS_DE_ROTINA))
        with col_m5:
            st.metric(label="🚫 Rejeitados", value=sum(1 for h in historico if h["status"] == "rejeitado"))

st.markdown("""
<div class="mediflow-footer">
    <div>
        <strong>🏥 MediFlow</strong> · Sistema Autônomo de Roteamento Clínico · <em>Hackathon ONE G10</em>
    </div>
    <div>
        <strong>Equipe:</strong> André · Ingrid · Guilherme · Lethicia | <strong>Infra:</strong> OCI VM Always Free & Object Storage
    </div>
</div>
""", unsafe_allow_html=True)
