"""
MediFlow - Pipeline de Visão Multimodal, OCR e Extração Estruturada com Google Gemini
Hackathon ONE G10 - Grupo 10: MediFlow

Responsável: Ingrid Soares
Mapeamento: agente/visao_multimodal.py
"""

import logging
import os
import re
import time
from typing import Optional, Tuple

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import errors as erros_genai
from google.genai import types

from agente.leitor_documentos import ConteudoDocumentoIngerido
from contratos.modelos import (
    ClassificacaoDocumento,
    DadosExtraidos,
    MedicamentoItem,
    MedicoSolicitante,
    NivelPrioridade,
    Paciente,
    TipoDocumento,
)
from prompts.extracao_clinica import (
    PROMPT_OCR_MULTIMODAL,
    ResultadoExtracaoLLM,
    SYSTEM_PROMPT_MEDIFLOW,
    montar_prompt_extracao_texto,
)

load_dotenv()

logger = logging.getLogger(__name__)

# O gemini-2.5-flash não está mais disponível para chaves novas (a API responde 404).
# O gemini-3.5-flash-lite foi testado com texto, PNG e PDF escaneado.
MODELO_GEMINI_DEFAULT = "gemini-3.5-flash-lite"


def resolver_modelo(valor_do_ambiente: Optional[str]) -> str:
    """O modelo de LLM_MODEL; vazio ou só espaços vale o padrão."""
    return (valor_do_ambiente or "").strip() or MODELO_GEMINI_DEFAULT


MODELO_GEMINI_PADRAO = resolver_modelo(os.getenv("LLM_MODEL"))

# Falha transitória (modelo sob alta demanda, rede lenta): a chamada é repetida. A original mais
# 2 novas tentativas, com espera de 2 e 4 segundos. 429 (cota) e os demais erros 4xx não são
# repetidos: repetir não resolve e gasta a cota. Depois da última falha, vale o fallback.
TENTATIVAS_MAXIMAS = 3
ESPERAS_ENTRE_TENTATIVAS_SEGUNDOS = (2, 4)
CODIGOS_HTTP_TRANSITORIOS = frozenset({500, 503, 504})
# Sem prazo, uma conexão presa nunca vira "timeout" e a nova tentativa nunca acontece. No pior caso,
# 3 chamadas de 25 s mais as esperas somam 81 s: cabe nos 90 s que o painel espera pela API.
TIMEOUT_CHAMADA_MS = 25_000


def _dormir(segundos: float) -> None:
    time.sleep(segundos)


def _falha_transitoria(erro: Exception) -> bool:
    if isinstance(erro, erros_genai.APIError):
        return erro.code in CODIGOS_HTTP_TRANSITORIOS
    return isinstance(erro, httpx.TimeoutException)


def _descrever_falha(erro: Exception) -> str:
    """O tipo da exceção e, quando houver, o código HTTP. Nunca a mensagem: num ValidationError
    da resposta, ela traz trechos do que o modelo devolveu, ou seja, dados do paciente."""
    codigo = getattr(erro, "code", None)
    if isinstance(codigo, int):
        return f"{type(erro).__name__}, HTTP {codigo}"
    return type(erro).__name__


def _gerar_conteudo_com_tentativas(cliente, modelo: str, conteudos: list, configuracao: types.GenerateContentConfig):
    """Chama o modelo e repete a chamada em falha transitória. Relança a última falha."""
    for tentativa in range(1, TENTATIVAS_MAXIMAS + 1):
        try:
            return cliente.models.generate_content(model=modelo, contents=conteudos, config=configuracao)
        except Exception as erro:
            if tentativa == TENTATIVAS_MAXIMAS or not _falha_transitoria(erro):
                raise
            espera = ESPERAS_ENTRE_TENTATIVAS_SEGUNDOS[tentativa - 1]
            logger.warning(
                "Falha transitória na chamada ao modelo Gemini %s (%s). Nova tentativa %d de %d em %d s.",
                modelo,
                _descrever_falha(erro),
                tentativa + 1,
                TENTATIVAS_MAXIMAS,
                espera,
            )
            _dormir(espera)


def obter_chave_llm() -> Optional[str]:
    """
    Obtém a chave da API da LLM a partir do ambiente (.env), respeitando LLM_API_KEY
    definido no .env.example da equipe (com fallback para GEMINI_API_KEY / GOOGLE_API_KEY).
    """
    for var_name in ("LLM_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        valor = os.getenv(var_name, "").strip()
        if valor and valor != "sua_chave_aqui":
            return valor
    return None


def _extrair_paciente_heuristico(texto: str) -> Paciente:
    """Extrai nome e idade do paciente por expressões regulares clínicas."""
    nome: Optional[str] = None
    idade: Optional[int] = None

    match_pac = re.search(
        r"Paciente:\s*([^\n\r\.]+?)(?:,\s*(\d{1,3})\s*anos|\.\s*(?:Solicitante|Exame|Medico|Médico|Resultado)|\n|$)",
        texto,
        flags=re.IGNORECASE,
    )
    if match_pac:
        nome_bruto = match_pac.group(1).strip()
        if match_pac.group(2):
            idade = int(match_pac.group(2))
        if "[ilegível]" in nome_bruto.lower():
            partes = re.sub(r"\[ilegível\]\.?", "", nome_bruto, flags=re.IGNORECASE).strip(" .,-")
            nome = f"{partes} (incompleto / ilegível)" if partes else "Paciente (incompleto / ilegível)"
        else:
            nome = nome_bruto.strip(" .,-")

    if idade is None:
        match_idade = re.search(r"(\d{1,3})\s*anos", texto, flags=re.IGNORECASE)
        if match_idade:
            idade = int(match_idade.group(1))

    return Paciente(nome=nome, idade=idade)


def _extrair_medico_heuristico(texto: str) -> MedicoSolicitante:
    """Extrai nome do médico e CRM padronizado no formato NUMERO-UF."""
    nome_med: Optional[str] = None
    crm_fmt: Optional[str] = None

    match_med = re.search(
        r"(?:Solicitante|M[eé]dico(?:\s+Solicitante)?):\s*(.+?)(?:\.\s*(?:Exame|Indica|Achados|CONCLUS|Prescri|Resultado|Data|CID)|\n|\r|$)",
        texto,
        flags=re.IGNORECASE,
    )
    if match_med:
        trecho_med = match_med.group(1).strip(" .")
        match_crm = re.search(r"CRM(?:-|\s*)([A-Z]{2})?\s*[:\-]?\s*(\d{3,8})(?:-([A-Z]{2}))?", trecho_med, flags=re.IGNORECASE)
        if match_crm:
            uf = (match_crm.group(1) or match_crm.group(3) or "SP").upper()
            num = match_crm.group(2)
            crm_fmt = f"{num}-{uf}"
            nome_med = trecho_med[: match_crm.start()].strip(" -.,")
        else:
            if any(termo in trecho_med.lower() for termo in ["ilegível", "rasurado", "borrado"]):
                prefixo = trecho_med.split("[")[0].strip(" .,-")
                nome_med = f"{prefixo} (não legível)" if prefixo else "Médico (não legível)"
            else:
                nome_med = trecho_med.strip(" .,-")

    if not crm_fmt:
        match_crm_global = re.search(r"CRM(?:-|\s*)([A-Z]{2})?\s*[:\-]?\s*(\d{3,8})(?:-([A-Z]{2}))?", texto, flags=re.IGNORECASE)
        if match_crm_global:
            uf = (match_crm_global.group(1) or match_crm_global.group(3) or "SP").upper()
            num = match_crm_global.group(2)
            crm_fmt = f"{num}-{uf}"

    return MedicoSolicitante(nome=nome_med, crm=crm_fmt)


def extrair_dados_heuristico_fallback(
    texto: str,
    formato_arquivo: str = "PDF",
) -> Tuple[ClassificacaoDocumento, DadosExtraidos]:
    """
    Motor determinístico de fallback clínico para quando a chave da API Gemini não está
    configurada (ex.: testes automatizados offline ou ambiente CI) ou em caso de indisponibilidade.
    """
    texto_limpo = " ".join((texto or "").split())
    texto_lower = texto_limpo.lower()

    tem_ilegibilidade = any(
        marcador in texto_lower
        for marcador in ["[ilegível]", "ilegivel", "rasurado", "borrado", "incompreensível"]
    )

    paciente = _extrair_paciente_heuristico(texto_limpo)
    medico = _extrair_medico_heuristico(texto_limpo)

    # Identificação do tipo de documento e especialidade
    if any(k in texto_lower for k in ["receituario", "receituário", "prescricao", "prescrição"]):
        tipo_doc = TipoDocumento.RECEITUARIO.value
        especialidade = "Clínica Geral"
    elif any(k in texto_lower for k in ["radiologico", "radiológico", "tomografia", "ressonancia", "raio-x", "ultrassonografia"]):
        tipo_doc = TipoDocumento.LAUDO_EXAME_IMAGEM.value
        especialidade = "Radiologia / Pneumologia" if "torax" in texto_lower or "pulmonar" in texto_lower else "Radiologia"
    elif any(k in texto_lower for k in ["laboratorial", "hemograma", "análises clínicas", "analises clinicas"]):
        tipo_doc = TipoDocumento.LAUDO_EXAME_LABORATORIAL.value
        especialidade = "Hematologia / Clínica Médica" if "hemograma" in texto_lower else "Patologia Clínica"
    elif any(k in texto_lower for k in ["guia", "tiss", "tuss", "solicitação de procedimento", "solicitacao de procedimento"]):
        tipo_doc = TipoDocumento.GUIA_SOLICITACAO.value
        especialidade = "Auditoria Médica"
    elif any(k in texto_lower for k in ["relatório de alta", "relatorio de alta", "internação", "internacao"]):
        tipo_doc = TipoDocumento.RELATORIO_ALTA.value
        especialidade = "Medicina Hospitalar"
    elif "atestado" in texto_lower:
        tipo_doc = TipoDocumento.ATESTADO.value
        especialidade = "Clínica Médica"
    elif not texto_limpo or tem_ilegibilidade or not (
        paciente.nome or medico.crm or "conclusão:" in texto_lower or "conclusao:" in texto_lower
    ):
        return (
            ClassificacaoDocumento(
                tipo_documento=TipoDocumento.OUTRO_DESCONHECIDO.value,
                especialidade=None,
                nivel_prioridade=NivelPrioridade.ROTINA,
                score_confianca_classificacao=0.0,
            ),
            DadosExtraidos(),
        )
    else:
        tipo_doc = TipoDocumento.LAUDO_EXAME_LABORATORIAL.value
        especialidade = "Geral"

    # Identificação de exame realizado
    exame_realizado: Optional[str] = None
    match_exame = re.search(r"Exame:\s*([^\n\r\.]+)", texto_limpo, flags=re.IGNORECASE)
    if match_exame:
        exame_realizado = match_exame.group(1).strip(" .")

    # Identificação de diagnóstico principal, CID e observações
    diagnostico_principal: Optional[str] = None
    cid_sugerido: Optional[str] = None
    observacoes_clinicas: Optional[str] = None
    medicamentos = None

    if "tromboembolismo pulmonar agudo" in texto_lower or "tep agudo" in texto_lower:
        diagnostico_principal = "Tromboembolismo Pulmonar Agudo (TEP)"
        cid_sugerido = "I26.9"
        nivel_prioridade = NivelPrioridade.URGENTE
        score_base = 0.99
        match_achados = re.search(r"Achados:\s*([^\n\r\.]+)", texto_limpo, flags=re.IGNORECASE)
        observacoes_clinicas = (
            match_achados.group(1).strip() + "."
            if match_achados
            else "Achado crítico compatível com TEP agudo."
        )
    elif tem_ilegibilidade and tipo_doc == TipoDocumento.RECEITUARIO.value:
        diagnostico_principal = "Prescrição medicamentosa ilegível"
        cid_sugerido = None
        nivel_prioridade = NivelPrioridade.PRIORITARIO
        score_base = 0.65
        observacoes_clinicas = "Receituário com dados rasurados. Risco de erro de dosagem medicamentosa."
        if "amox" in texto_lower:
            medicamentos = [
                MedicamentoItem(
                    nome="Amoxicilina (suspeita)",
                    dosagem="Ilegível",
                    frequencia="Ilegível",
                    via_administracao="Oral",
                    duracao=None,
                )
            ]
    elif tipo_doc == TipoDocumento.RECEITUARIO.value:
        diagnostico_principal = "Prescrição medicamentosa ambulatorial"
        nivel_prioridade = NivelPrioridade.ROTINA
        score_base = 0.94
        meds_encontrados = []
        for match_med_item in re.finditer(
            r"(?:1\.|2\.|-|\*)\s*([A-Za-zÀ-ÿ\s]+?)\s+(\d+\s*mg)\s*(?:-\s*([^\n\.]+))?",
            texto_limpo,
        ):
            meds_encontrados.append(
                MedicamentoItem(
                    nome=match_med_item.group(1).strip(),
                    dosagem=match_med_item.group(2).strip(),
                    frequencia=(match_med_item.group(3) or "Conforme prescrição").strip(),
                    via_administracao="Oral",
                    duracao=None,
                )
            )
        medicamentos = meds_encontrados or [
            MedicamentoItem(
                nome="Medicamento prescrito",
                dosagem="Conforme receita",
                frequencia="Uso indicado em receituário",
                via_administracao="Oral",
            )
        ]
        observacoes_clinicas = "Receituário médico legível e validado."
    elif "dentro dos limites da normalidade" in texto_lower or "controle ambulatorial de rotina" in texto_lower:
        diagnostico_principal = "Exame de controle ambulatorial de rotina sem alterações"
        cid_sugerido = "Z01.7"
        nivel_prioridade = NivelPrioridade.ROTINA
        score_base = 0.98
        observacoes_clinicas = "Parâmetros hematológicos normais."
    else:
        match_conclusao = re.search(r"CONCLUS[ÃA]O:\s*([^\n\r]+)", texto_limpo, flags=re.IGNORECASE)
        diagnostico_principal = (
            match_conclusao.group(1).strip(" .")
            if match_conclusao
            else ("Documento ilegível ou sem conclusão explícita" if tem_ilegibilidade else "Avaliação clínica de rotina")
        )
        nivel_prioridade = (
            NivelPrioridade.URGENTE
            if any(u in texto_lower for u in ["urgente", "emergencia", "infarto", "avc", "sepse"])
            else (NivelPrioridade.PRIORITARIO if tem_ilegibilidade else NivelPrioridade.ROTINA)
        )
        score_base = 0.50 if tem_ilegibilidade or not texto_limpo else 0.92
        observacoes_clinicas = "Extraído via analisador clínico estruturado."

    classificacao = ClassificacaoDocumento(
        tipo_documento=tipo_doc,
        especialidade=especialidade,
        nivel_prioridade=nivel_prioridade,
        score_confianca_classificacao=score_base,
    )
    dados = DadosExtraidos(
        paciente=paciente,
        medico_solicitante=medico,
        exame_realizado=exame_realizado,
        diagnostico_principal=diagnostico_principal,
        cid_sugerido=cid_sugerido,
        medicamentos=medicamentos,
        observacoes_clinicas=observacoes_clinicas,
    )
    return classificacao, dados


def analisar_documento_com_gemini(
    conteudo: ConteudoDocumentoIngerido,
    canal_origem: str = "Pronto_Socorro",
    modelo: str = MODELO_GEMINI_PADRAO,
) -> Tuple[ClassificacaoDocumento, DadosExtraidos, str]:
    """
    Executa a análise multimodal (Visão/OCR para imagens e PDFs digitalizados) ou textual
    utilizando o SDK oficial `google-genai` com Structured Outputs (`ResultadoExtracaoLLM`).
    Caso `LLM_API_KEY` não esteja configurada no ambiente, utiliza o fallback clínico determinístico.
    """
    api_key = obter_chave_llm()
    texto_atual = conteudo.texto_extraido or ""

    if api_key:
        try:
            cliente = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_CHAMADA_MS))
            partes_conteudo = []

            if conteudo.requer_visao_multimodal and conteudo.bytes_midia:
                partes_conteudo.append(
                    types.Part.from_bytes(
                        data=conteudo.bytes_midia,
                        mime_type=conteudo.mime_type,
                    )
                )
                prompt_usuario = PROMPT_OCR_MULTIMODAL
                if texto_atual:
                    prompt_usuario += f"\n\nContexto textual adicional:\n{texto_atual}"
                partes_conteudo.append(prompt_usuario)
            else:
                partes_conteudo.append(montar_prompt_extracao_texto(texto_atual, canal_origem))

            resposta = _gerar_conteudo_com_tentativas(
                cliente,
                modelo,
                partes_conteudo,
                types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT_MEDIFLOW,
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=ResultadoExtracaoLLM,
                ),
            )

            resultado_parsed: Optional[ResultadoExtracaoLLM] = getattr(resposta, "parsed", None)
            if resultado_parsed is None and resposta.text:
                raw_json = resposta.text.strip()
                if raw_json.startswith("```"):
                    raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json, flags=re.IGNORECASE)
                    raw_json = re.sub(r"\s*```$", "", raw_json)
                resultado_parsed = ResultadoExtracaoLLM.model_validate_json(raw_json)

            if resultado_parsed is not None:
                logger.info("Extração estruturada via Gemini (%s) concluída com sucesso.", modelo)
                texto_final = texto_atual or resultado_parsed.texto_transcrito_ocr or ""
                return (
                    resultado_parsed.classificacao,
                    resultado_parsed.dados_extraidos,
                    texto_final,
                )
        except Exception as exc:
            # Só o modelo, o tipo da exceção e o código HTTP: a mensagem completa pode trazer dados do paciente.
            logger.warning(
                "Falha na chamada ao modelo Gemini %s (%s). Ativando fallback clínico determinístico.",
                modelo,
                _descrever_falha(exc),
            )

    classificacao, dados = extrair_dados_heuristico_fallback(
        texto_atual,
        formato_arquivo=conteudo.formato_normalizado,
    )
    return classificacao, dados, texto_atual
