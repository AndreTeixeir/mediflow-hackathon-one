"""
MediFlow - Interface Streamlit de Triagem e Auditoria Clínica (Human-in-the-Loop)
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: interface/app.py
Execução a partir da raiz: python -m streamlit run interface/app.py
"""

import base64
import json
import os
import uuid

import requests
import streamlit as st
from dotenv import load_dotenv

from contratos.modelos import CanalOrigem, DestinoRoteamento

load_dotenv()

API_BASE_URL = (
    os.getenv("MEDIFLOW_API_URL", "http://localhost:8000").strip().rstrip("/")
    or "http://localhost:8000"
)

# Limite do arquivo na tela, conferido antes de enviar. A API aceita 15 MB no corpo inteiro e o
# Base64 incha o arquivo em cerca de 33%: 10 MB viram ~13,4 MB e ainda cabem.
TAMANHO_MAXIMO_MB = 10
# O pior caso da API é 3 chamadas ao Gemini de até 25 s mais as esperas (81 s): o timeout fica acima.
TIMEOUT_API_SEGUNDOS = 90
MODO_TEXTO = "Texto do documento"
MODO_ARQUIVO = "Enviar arquivo PDF ou imagem"
CANAIS_ORIGEM = [canal.value for canal in CanalOrigem]

# Configuração visual da página
st.set_page_config(
    page_title="MediFlow - Triagem & Auditoria Clínica",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Estilos visuais personalizados
st.markdown("""
<style>
    .titulo-principal {
        font-size: 2.2rem;
        font-weight: 700;
        color: #0b4f6c;
        margin-bottom: 0.2rem;
    }
    .subtitulo {
        font-size: 1.05rem;
        color: #555;
        margin-bottom: 1.5rem;
    }
    .card-destaque {
        background-color: #f8f9fa;
        border-radius: 8px;
        padding: 16px;
        border-left: 5px solid #0b4f6c;
    }
</style>
""", unsafe_allow_html=True)


# ============================================================================
# ESTADO DA SESSÃO (BANCO LOCAL DE DOCUMENTOS PARA DEMONSTRAÇÃO)
# ============================================================================

def carregar_cenarios_iniciais():
    """Carrega os 3 cenários oficiais de teste da pasta dados_teste."""
    cenarios = []
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pastas = ["1_rotina", "2_urgencia", "3_auditoria_humana"]
    
    for p in pastas:
        caminho = os.path.join(raiz, "dados_teste", p, "exemplo.json")
        if os.path.exists(caminho):
            with open(caminho, "r", encoding="utf-8") as f:
                dado = json.load(f)
                cenarios.append(dado)
    return cenarios


if "documentos_db" not in st.session_state:
    raw_data = carregar_cenarios_iniciais()
    st.session_state.documentos_db = []
    for item in raw_data:
        req = item.get("requisicao_entrada", {})
        resp = item.get("resposta_esperada", {})
        st.session_state.documentos_db.append({
            "requisicao": req,
            "triagem": resp,
            "cenario_nome": item.get("cenario", ""),
            "descricao": item.get("descricao", "")
        })

if "historico_auditorias" not in st.session_state:
    st.session_state.historico_auditorias = []


def gerar_novo_id() -> None:
    """Gera um ID e o mantém no estado da sessão. Roda como on_click, antes de o campo ser redesenhado."""
    st.session_state["documento_id"] = f"DOC-DEMO-{uuid.uuid4().hex[:8].upper()}"


# O ID fica na sessão: um value= com UUID novo a cada execução descartaria o que a pessoa digitou.
if "documento_id" not in st.session_state:
    gerar_novo_id()


def tipo_pela_extensao(nome_arquivo: str) -> str:
    """PDF, PNG, JPG, JPEG ou WEBP, pela extensão do arquivo enviado."""
    return nome_arquivo.rsplit(".", 1)[-1].upper()


def enviar_triagem(payload: dict) -> dict | None:
    """Envia o documento à API. Devolve a resposta, ou None depois de mostrar o erro na tela.

    Nenhum resultado é simulado: se a API não responder 200, não há triagem.
    """
    endereco = f"{API_BASE_URL}/api/v1/triagem"
    try:
        with st.spinner("Enviando documento para a API MediFlow..."):
            resposta = requests.post(endereco, json=payload, timeout=TIMEOUT_API_SEGUNDOS)
    except requests.exceptions.ConnectionError:
        st.error("Não foi possível conectar à API.")
        st.write(f"Endereço tentado: `{endereco}`")
        st.info("Inicie a API FastAPI ou confira a variável MEDIFLOW_API_URL.")
        return None
    except requests.exceptions.Timeout:
        st.error("A API demorou demais para responder. Tente novamente ou verifique os logs do back-end.")
        return None
    except requests.exceptions.RequestException as erro:
        st.error(f"Erro na comunicação com a API ({type(erro).__name__}).")
        return None

    if resposta.status_code == 200:
        try:
            return resposta.json()
        except ValueError:
            st.error("A resposta da API não veio em JSON válido.")
            return None

    if resposta.status_code == 409:
        st.error('Já existe um documento com esse ID. Clique em "Gerar novo ID" e envie de novo.')
    elif resposta.status_code == 422:
        st.error("A API recusou os dados enviados (HTTP 422).")
        try:
            st.json(resposta.json())
        except ValueError:
            st.code(resposta.text[:1000])
    elif resposta.status_code == 503:
        st.error("O armazenamento está indisponível no momento (HTTP 503). Tente de novo em instantes.")
    else:
        st.error(f"A API respondeu com HTTP {resposta.status_code}.")
        st.code(resposta.text[:1000])
    return None


# ============================================================================
# CABEÇALHO & MÉTRICAS
# ============================================================================

st.markdown('<div class="titulo-principal">🏥 MediFlow — Triagem e Auditoria Clínica</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitulo">Agente Autônomo para Triagem, Extração e Roteamento de Documentos Clínicos | OCI Always Free</div>', unsafe_allow_html=True)

# Métricas no topo
total_docs = len(st.session_state.documentos_db)
pendentes = sum(
    1 for d in st.session_state.documentos_db 
    if d["triagem"].get("status") == "pendente_auditoria" 
    or d["triagem"]["decisao_roteamento"].get("requer_auditoria_humana")
)
urgentes = sum(
    1 for d in st.session_state.documentos_db 
    if d["triagem"]["classificacao"].get("nivel_prioridade") in ["Urgente", "Emergência"]
)
aprovados = sum(
    1 for d in st.session_state.documentos_db 
    if d["triagem"].get("status") == "processado" 
    and not d["triagem"]["decisao_roteamento"].get("requer_auditoria_humana")
)

col_m1, col_m2, col_m3, col_m4 = st.columns(4)
with col_m1:
    st.metric(label="📄 Total de Documentos", value=total_docs)
with col_m2:
    st.metric(label="⚠️ Fila de Revisão Humana", value=pendentes, delta=f"{pendentes} retidos", delta_color="inverse")
with col_m3:
    st.metric(label="🚨 Emergências Médicas", value=urgentes)
with col_m4:
    st.metric(label="✅ Roteados Automaticamente", value=aprovados)

st.divider()


# ============================================================================
# ABAS DE NAVEGAÇÃO
# ============================================================================

tab_auditoria, tab_envio, tab_historico = st.tabs([
    "🔍 Fila de Auditoria Clínica (Human-in-the-Loop)",
    "📤 Envio e Triagem de Documentos",
    "📊 Histórico & Buckets OCI"
])


# ----------------------------------------------------------------------------
# ABA 1: PAINEL DE AUDITORIA HUMANA (RESPONSABILIDADE DA LETHICIA)
# ----------------------------------------------------------------------------
with tab_auditoria:
    docs_pendentes = [
        d for d in st.session_state.documentos_db 
        if d["triagem"].get("status") == "pendente_auditoria" 
        or d["triagem"]["decisao_roteamento"].get("requer_auditoria_humana")
    ]

    if not docs_pendentes:
        st.success("🎉 Não há documentos pendentes na fila de auditoria humana no momento!")
        st.info("Todos os laudos e receitas com alta confiabilidade foram triados e roteados automaticamente.")
    else:
        st.subheader(f"📋 Documentos Retidos para Revisão ({len(docs_pendentes)})")
        
        opcoes = [
            f"{d['triagem']['documento_id']} — {d['triagem']['classificacao']['tipo_documento']} ({d['cenario_nome']})" 
            for d in docs_pendentes
        ]
        escolha_idx = st.selectbox("Selecione o caso para auditar:", range(len(opcoes)), format_func=lambda i: opcoes[i])
        
        doc_selecionado = docs_pendentes[escolha_idx]
        req = doc_selecionado["requisicao"]
        tri = doc_selecionado["triagem"]
        dados_ext = tri.get("dados_extraidos", {})
        paciente = dados_ext.get("paciente", {}) or {}
        medico = dados_ext.get("medico_solicitante", {}) or {}
        decisao = tri.get("decisao_roteamento", {})
        classif = tri.get("classificacao", {})

        col_esq, col_dir = st.columns([1, 1.2])

        # Coluna da esquerda: O que o sistema recebeu e o motivo do alerta
        with col_esq:
            st.markdown("### 📄 Documento Recebido")
            st.markdown(f"**ID do Documento:** `{tri.get('documento_id')}`")
            st.markdown(f"**Canal de Origem:** `{req.get('canal_origem', 'Ambulatorio')}`")
            st.markdown(f"**Formato:** `{req.get('tipo_arquivo', 'PDF')}`")
            
            st.warning(f"**Motivo da Retenção para Auditoria:**\n{decisao.get('justificativa_roteamento')}")

            st.text_area(
                label="Texto Transcrito do Documento (OCR / LLM):",
                value=req.get("documento_texto", "Nenhum texto disponível"),
                height=260,
                disabled=True
            )

            score = classif.get("score_confianca_classificacao", 0.0)
            st.markdown(f"**Score de Confiança da Extração:** `{score * 100:.1f}%`")
            st.progress(score)

        # Coluna da direita: Formulário interativo para o especialista corrigir
        with col_dir:
            st.markdown("### ✍️ Formulário de Revisão Clínica")
            st.caption("O auditor confere e corrige as entidades clínicas antes de liberar o roteamento.")

            with st.form(key=f"form_auditoria_{tri.get('documento_id')}"):
                c_p1, c_p2 = st.columns([2, 1])
                with c_p1:
                    novo_nome = st.text_input("Nome do Paciente:", value=paciente.get("nome") or "")
                with c_p2:
                    nova_idade = st.number_input("Idade:", value=int(paciente.get("idade") or 0), min_value=0, max_value=120)

                c_m1, c_m2 = st.columns([2, 1])
                with c_m1:
                    novo_medico = st.text_input("Médico Solicitante:", value=medico.get("nome") or "")
                with c_m2:
                    novo_crm = st.text_input("CRM com UF:", value=medico.get("crm") or "")

                novo_diag = st.text_input("Diagnóstico Principal:", value=dados_ext.get("diagnostico_principal") or "")
                novo_cid = st.text_input("CID-10 Sugerido:", value=dados_ext.get("cid_sugerido") or "")

                st.markdown("**Destino Hospitalar:**")
                destinos_lista = [d.value for d in DestinoRoteamento]
                novo_destino = st.selectbox(
                    "Encaminhar para o departamento:",
                    destinos_lista,
                    index=destinos_lista.index("Farmacia_Hospitalar") if "Farmacia_Hospitalar" in destinos_lista else 0
                )

                nome_auditor = st.text_input("Auditor Responsável:", value="Lethicia - Auditora Clínica")
                parecer = st.text_area("Justificativa do Parecer:", value="Dados conferidos manualmente com a receita original. Erros de dosagem descartados.")

                col_btn1, col_btn2 = st.columns(2)
                with col_btn1:
                    btn_aprovar = st.form_submit_button("✅ Aprovar e Liberar Roteamento", use_container_width=True)
                with col_btn2:
                    btn_rejeitar = st.form_submit_button("❌ Rejeitar Documento", use_container_width=True)

            if btn_aprovar:
                # Atualiza os dados com a correção humana
                paciente["nome"] = novo_nome
                paciente["idade"] = nova_idade
                medico["nome"] = novo_medico
                medico["crm"] = novo_crm
                dados_ext["diagnostico_principal"] = novo_diag
                dados_ext["cid_sugerido"] = novo_cid
                
                # Atualiza a decisão
                tri["status"] = "processado"
                decisao["requer_auditoria_humana"] = False
                decisao["destino_principal"] = novo_destino
                decisao["justificativa_roteamento"] = f"Aprovado por auditoria humana ({nome_auditor}). Parecer: {parecer}"
                classif["score_confianca_classificacao"] = 1.0
                
                if tri.get("armazenamento_oci"):
                    tri["armazenamento_oci"]["bucket"] = "mediflow-rotina"
                    tri["armazenamento_oci"]["caminho_objeto"] = f"{tri['documento_id']}.json"

                st.session_state.historico_auditorias.append({
                    "documento_id": tri["documento_id"],
                    "auditor": nome_auditor,
                    "acao": "APROVADO",
                    "destino_final": novo_destino,
                    "parecer": parecer
                })

                st.success(f"Documento {tri['documento_id']} corrigido e aprovado! Encaminhado para {novo_destino}.")
                st.rerun()

            elif btn_rejeitar:
                tri["status"] = "rejeitado"
                decisao["requer_auditoria_humana"] = False
                decisao["justificativa_roteamento"] = f"Rejeitado por auditoria ({nome_auditor}). Motivo: {parecer}"
                
                if tri.get("armazenamento_oci"):
                    tri["armazenamento_oci"]["bucket"] = "mediflow-rejeitados"

                st.session_state.historico_auditorias.append({
                    "documento_id": tri["documento_id"],
                    "auditor": nome_auditor,
                    "acao": "REJEITADO",
                    "destino_final": "Descartado",
                    "parecer": parecer
                })

                st.warning(f"Documento {tri['documento_id']} rejeitado.")
                st.rerun()


# ----------------------------------------------------------------------------
# ABA 2: ENVIO E TRIAGEM (lógica de envio da página do Guilherme, no visual da Lethicia)
# ----------------------------------------------------------------------------
with tab_envio:
    st.subheader("📤 Enviar Documento para Triagem")
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
        # if/elif em vez de st.stop(): o stop interromperia o script e a aba de histórico sumiria.
        if modo_envio == MODO_TEXTO and not texto_input.strip():
            st.error("Insira o texto do documento antes de enviar.")
        elif modo_envio == MODO_ARQUIVO and arquivo_upload is None:
            st.error("Selecione um arquivo antes de enviar.")
        elif arquivo_upload is not None and arquivo_upload.size > TAMANHO_MAXIMO_MB * 1024 * 1024:
            st.error(f"O arquivo passa de {TAMANHO_MAXIMO_MB} MB. Envie um arquivo menor.")
        else:
            payload = {"documento_id": doc_id_input, "canal_origem": canal_input}
            if arquivo_upload is not None:
                tipo_arq = tipo_pela_extensao(arquivo_upload.name)
                # A API recebe o arquivo em Base64 dentro do JSON.
                payload["tipo_arquivo"] = tipo_arq
                payload["documento_base64"] = base64.b64encode(arquivo_upload.getvalue()).decode("ascii")
            else:
                tipo_arq = "TXT"
                payload["tipo_arquivo"] = tipo_arq
                payload["documento_texto"] = texto_input

            resultado = enviar_triagem(payload)

            if resultado is not None:
                st.success("✅ Triagem processada com sucesso via API FastAPI + Grafo LangGraph!")

                # Só uma triagem que a API confirmou entra no banco da sessão.
                novo_registro = {
                    "requisicao": {
                        "documento_id": doc_id_input,
                        "tipo_arquivo": tipo_arq,
                        "documento_texto": texto_input or f"[Arquivo {tipo_arq} enviado via upload]",
                        "canal_origem": canal_input,
                    },
                    "triagem": resultado,
                    "cenario_nome": "Envio pela tela",
                    "descricao": "Documento processado durante a sessão.",
                }
                st.session_state.documentos_db = [
                    d for d in st.session_state.documentos_db
                    if d["triagem"].get("documento_id") != doc_id_input
                ] + [novo_registro]

                # Exibe resultado detalhado
                st.markdown("### Resultado da Triagem:")
                col_r1, col_r2 = st.columns(2)
                with col_r1:
                    st.json(resultado["classificacao"])
                    st.json(resultado["decisao_roteamento"])
                with col_r2:
                    st.json(resultado["dados_extraidos"])


# ----------------------------------------------------------------------------
# ABA 3: HISTÓRICO & BUCKETS OCI
# ----------------------------------------------------------------------------
with tab_historico:
    st.subheader("📦 Visualização do Armazenamento OCI (Object Storage)")
    st.caption("Organização segregada de arquivos conforme exigido pelo edital Always Free da Oracle.")

    for item in st.session_state.documentos_db:
        t = item["triagem"]
        oci = t.get("armazenamento_oci", {})
        status_txt = "🟢 Processado" if t.get("status") == "processado" else "🟠 Pendente Auditoria"
        if t["classificacao"].get("nivel_prioridade") == "Urgente":
            status_txt += " | 🚨 Urgente"

        with st.expander(f"{t.get('documento_id')} — {status_txt} — Bucket: {oci.get('bucket')}"):
            st.json(t)

    if st.session_state.historico_auditorias:
        st.subheader("📝 Histórico de Intervenções Humanas:")
        st.table(st.session_state.historico_auditorias)
