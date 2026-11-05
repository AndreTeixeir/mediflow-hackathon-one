"""
MediFlow - Interface Streamlit de Triagem e Auditoria Clínica (Human-in-the-Loop)
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: interface/app.py
Execução a partir da raiz: python -m streamlit run interface/app.py
"""

import json
import os
import requests
import streamlit as st

from contratos.modelos import (
    CanalOrigem,
    ClassificacaoDocumento,
    DadosExtraidos,
    DecisaoRoteamento,
    DestinoRoteamento,
    DocumentoEntrada,
    MedicoSolicitante,
    NivelPrioridade,
    Paciente,
    StatusProcessamento,
    TipoDocumento,
    TriagemResposta,
)
from regras.regras_clinicas import executar_roteamento_clinico

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
# ABA 2: ENVIO E TESTE DOS CENÁRIOS
# ----------------------------------------------------------------------------
with tab_envio:
    st.subheader("📤 Enviar Documento para Triagem")
    st.caption("Você pode testar enviando um texto avulso ou carregar diretamente um dos 3 cenários oficiais.")

    opcao_cenario = st.selectbox(
        "Carregar Exemplo Pré-definido:",
        ["Nenhum (Digitar manualmente)", "Cenário 1: Rotina (Hemograma)", "Cenário 2: Urgência (TEP Agudo)", "Cenário 3: Ambíguo (Receita Rasurada)"]
    )

    texto_padrao = ""
    id_padrao = "DOC-CLIN-NOVO-01"
    canal_padrao = "Pronto_Socorro"

    if "Cenário 1" in opcao_cenario:
        c1 = [d for d in st.session_state.documentos_db if "Cenário 1" in d["cenario_nome"]]
        if c1:
            texto_padrao = c1[0]["requisicao"]["documento_texto"]
            id_padrao = "DOC-ROTINA-TESTE"
            canal_padrao = "Ambulatorio"
    elif "Cenário 2" in opcao_cenario:
        c2 = [d for d in st.session_state.documentos_db if "Cenário 2" in d["cenario_nome"]]
        if c2:
            texto_padrao = c2[0]["requisicao"]["documento_texto"]
            id_padrao = "DOC-URGENCIA-TESTE"
            canal_padrao = "Pronto_Socorro"
    elif "Cenário 3" in opcao_cenario:
        c3 = [d for d in st.session_state.documentos_db if "Cenário 3" in d["cenario_nome"]]
        if c3:
            texto_padrao = c3[0]["requisicao"]["documento_texto"]
            id_padrao = "DOC-AMBIGUO-TESTE"
            canal_padrao = "Ambulatorio"

    col_e1, col_e2 = st.columns([2, 1])
    with col_e1:
        doc_id_input = st.text_input("ID do Documento:", value=id_padrao)
    with col_e2:
        canal_input = st.selectbox("Canal de Origem:", ["Pronto_Socorro", "Ambulatorio", "Internacao"], index=0 if canal_padrao == "Pronto_Socorro" else 1)

    texto_input = st.text_area("Texto do Documento Clínico:", value=texto_padrao, height=180)

    if st.button("🚀 Processar Triagem Automática", use_container_width=True):
        if not texto_input.strip():
            st.error("Por favor, informe o texto do documento.")
        else:
            payload = {
                "documento_id": doc_id_input,
                "tipo_arquivo": "TXT",
                "documento_texto": texto_input,
                "canal_origem": canal_input
            }
            
            # Tenta chamar a API do André em localhost:8000
            try:
                resp = requests.post("http://localhost:8000/api/v1/triagem", json=payload, timeout=3)
                if resp.status_code == 200:
                    resultado = resp.json()
                    st.success("✅ Triagem processada com sucesso via API FastAPI do André!")
                else:
                    st.warning(f"API respondeu com status {resp.status_code}. Executando via motor local de regras...")
                    raise requests.exceptions.RequestException()
            except requests.exceptions.RequestException:
                # Fallback: executa as regras clínicas da Lethicia diretamente
                from api.agente_simulado import analisar_documento
                entrada_obj = DocumentoEntrada(**payload)
                classif_sim, dados_sim = analisar_documento(entrada_obj)
                decisao_sim = executar_roteamento_clinico(classif_sim, dados_sim, texto_input, canal_input)
                
                bucket = "mediflow-rotina"
                if decisao_sim.destino_principal == DestinoRoteamento.FILA_EMERGENCIA:
                    bucket = "mediflow-urgentes"
                elif decisao_sim.destino_principal == DestinoRoteamento.FILA_REVISAO_HUMANA:
                    bucket = "mediflow-auditoria-humana"

                resultado = {
                    "status": "pendente_auditoria" if decisao_sim.requer_auditoria_humana else "processado",
                    "documento_id": doc_id_input,
                    "classificacao": classif_sim.model_dump(),
                    "dados_extraidos": dados_sim.model_dump(),
                    "decisao_roteamento": decisao_sim.model_dump(),
                    "armazenamento_oci": {
                        "bucket": bucket,
                        "caminho_objeto": f"{doc_id_input}.json",
                        "status_backup": "sucesso"
                    }
                }
                st.info("ℹ️ Triagem processada localmente com as regras clínicas de Lethicia.")

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
