import base64
import os
import uuid

import requests
import streamlit as st

from prompts.classificacao import PROMPT_CLASSIFICACAO
from prompts.extracao import PROMPT_EXTRACAO


st.set_page_config(
    page_title="MediFlow | Envio de arquivos",
    page_icon="🏥",
    layout="wide",
)

API_URL = os.getenv("MEDIFLOW_API_URL", "http://localhost:8000")
TRIAGE_PATH = "/api/v1/triagem"
TAMANHO_MAXIMO_MB = 10

st.title("🏥 MediFlow — Triagem de documentos clínicos")
st.caption(
    "Protótipo de interface para demonstração. "
    "Não use dados reais de pacientes."
)

with st.sidebar:
    st.header("Configuração")
    st.text_input(
        "API em uso (variável MEDIFLOW_API_URL)",
        value=API_URL,
        disabled=True,
    )
    canal_origem = st.selectbox(
        "Canal de origem",
        ["Recepcao", "Pronto_Socorro", "Ambulatorio", "Laboratorio", "Outro"],
    )
    st.divider()
    st.subheader("Prompts da equipe")
    st.caption(
        "Os prompts estão em prompts/classificacao.py e prompts/extracao.py."
    )
    with st.expander("Ver prompt de classificação"):
        st.code(PROMPT_CLASSIFICACAO, language="text")
    with st.expander("Ver prompt de extração"):
        st.code(PROMPT_EXTRACAO, language="text")

modo = st.radio(
    "Como deseja fornecer o documento?",
    ["Texto do documento", "Enviar arquivo PDF ou imagem"],
    horizontal=True,
)

documento_texto = ""
arquivo = None
tipo_arquivo = "TXT"

if modo == "Texto do documento":
    documento_texto = st.text_area(
        "Conteúdo do documento (use apenas exemplos fictícios)",
        height=240,
        placeholder=(
            "Cole aqui o texto de um documento clínico fictício "
            "para a demonstração..."
        ),
    )
else:
    arquivo = st.file_uploader(
        "Selecione um PDF ou uma imagem",
        type=["pdf", "png", "jpg", "jpeg", "webp"],
        help="Use somente documentos sintéticos ou devidamente anonimizados.",
    )
    if arquivo is not None:
        tipo_arquivo = arquivo.name.rsplit(".", 1)[-1].upper()


def gerar_novo_id() -> None:
    """Gera um ID e o mantém no estado da sessão do Streamlit."""
    st.session_state["documento_id"] = (
        f"DOC-DEMO-{uuid.uuid4().hex[:8].upper()}"
    )


# O ID fica guardado na sessão para não mudar a cada atualização da tela.
if "documento_id" not in st.session_state:
    gerar_novo_id()

col1, col2 = st.columns([3, 1], vertical_alignment="bottom")
with col1:
    documento_id = st.text_input(
        "ID do documento",
        key="documento_id",
        help=(
            "Só letras, números e hífen, até 64 caracteres. "
            "Cada envio precisa de um ID novo."
        ),
    )
with col2:
    st.button(
        "Gerar novo ID",
        on_click=gerar_novo_id,
        width="stretch",
    )

st.warning(
    "Este protótipo não faz diagnóstico nem substitui profissionais de saúde. "
    "A sinalização de urgência deve seguir as regras do fluxo e os "
    "responsáveis clínicos."
)

if st.button("Enviar para triagem", type="primary", width="stretch"):
    if modo == "Texto do documento" and not documento_texto.strip():
        st.error("Insira o texto fictício do documento antes de enviar.")
        st.stop()

    if modo == "Enviar arquivo PDF ou imagem" and arquivo is None:
        st.error("Selecione um arquivo antes de enviar.")
        st.stop()

    if arquivo is not None and arquivo.size > TAMANHO_MAXIMO_MB * 1024 * 1024:
        st.error(
            f"O arquivo passa de {TAMANHO_MAXIMO_MB} MB. "
            "Envie um arquivo menor."
        )
        st.stop()

    payload = {
        "documento_id": documento_id,
        "tipo_arquivo": tipo_arquivo,
        "canal_origem": canal_origem,
    }

    if arquivo is not None:
        # A API recebe o arquivo em Base64 dentro do JSON.
        payload["documento_base64"] = base64.b64encode(
            arquivo.getvalue()
        ).decode("ascii")
    else:
        payload["documento_texto"] = documento_texto

    endpoint = f"{API_URL.rstrip('/')}{TRIAGE_PATH}"

    try:
        with st.spinner("Enviando documento para a API MediFlow..."):
            response = requests.post(endpoint, json=payload, timeout=90)

        if response.ok:
            resultado = response.json()
            st.success("A API retornou um resultado de triagem.")

            classificacao = resultado.get("classificacao") or {}
            roteamento = resultado.get("decisao_roteamento") or {}
            armazenamento = resultado.get("armazenamento_oci") or {}

            c1, c2 = st.columns(2)
            c1.metric(
                "Prioridade",
                classificacao.get("nivel_prioridade", "Não informada"),
            )
            c2.metric(
                "Revisão humana",
                "Sim"
                if roteamento.get("requer_auditoria_humana", True)
                else "Não",
            )
            st.write(
                "**Tipo de documento:** "
                f"{classificacao.get('tipo_documento', 'Não informado')}"
            )
            st.write(
                "**Destino:** "
                f"{roteamento.get('destino_principal', 'Não informado')}"
            )
            st.write(
                "**Bucket:** {0}".format(
                    armazenamento.get("bucket", "Não informado")
                )
            )
            st.write(
                "**Justificativa:** "
                f"{roteamento.get('justificativa_roteamento', '')}"
            )

            st.subheader("Resultado estruturado")
            st.json(resultado)

        elif response.status_code == 409:
            st.error(
                "Já existe um documento com esse ID. "
                'Clique em "Gerar novo ID" e envie de novo.'
            )
        elif response.status_code == 422:
            st.error("A API recusou os dados enviados (HTTP 422).")
            try:
                st.json(response.json())
            except ValueError:
                st.code(response.text[:4000])
        elif response.status_code == 503:
            st.error(
                "O armazenamento está indisponível no momento (HTTP 503). "
                "Tente de novo em instantes."
            )
        else:
            st.error(f"A API respondeu com HTTP {response.status_code}.")
            st.code(response.text[:4000])

    except requests.exceptions.ConnectionError:
        st.error("Não foi possível conectar à API.")
        st.write(f"Endereço tentado: `{endpoint}`")
        st.info(
            "Inicie a API FastAPI ou confira a variável MEDIFLOW_API_URL."
        )
    except requests.exceptions.Timeout:
        st.error(
            "A API demorou demais para responder. Tente novamente "
            "ou verifique os logs do back-end."
        )
    except ValueError:
        st.error("A resposta da API não veio em JSON válido.")
    except requests.exceptions.RequestException as exc:
        st.error(f"Erro na comunicação com a API: {exc}")

st.divider()
st.caption(
    "MediFlow • Hackathon ONE G10 • Envio de documentos em texto, PDF ou imagem"
)
