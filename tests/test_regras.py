"""
MediFlow - Teste Unitário dos 3 Cenários Oficiais
Hackathon ONE G10 - Grupo 10: MediFlow

Responsável: Lethicia
Mapeamento: tests/test_regras.py
Execução: python -m unittest tests/test_regras.py
"""

import json
import os
import unittest

import pytest

from agente.grafo import no_avaliar_consistencia_e_urgencia
from contratos.modelos import (
    DocumentoEntrada,
    TriagemResposta,
    ClassificacaoDocumento,
    DadosExtraidos,
    DestinoRoteamento,
    MedicoSolicitante,
    NivelPrioridade,
    Paciente,
)
from regras.regras_clinicas import (
    NivelUrgencia,
    classificar_nivel_urgencia,
    detectar_urgencia_clinica,
    executar_roteamento_clinico,
    normalizar_texto,
)

class TestMediFlowCenarios(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Localiza a raiz do projeto (um nível acima da pasta tests)
        cls.raiz_projeto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.pasta_dados = os.path.join(cls.raiz_projeto, "dados_teste")

    def test_cenario_1_rotina(self):
        """Cenário 1: Laudo de rotina ambulatorial deve ir para Prontuário sem auditoria."""
        caminho = os.path.join(self.pasta_dados, "1_rotina", "exemplo.json")
        with open(caminho, "r", encoding="utf-8") as f:
            dado = json.load(f)

        req = DocumentoEntrada(**dado["requisicao_entrada"])
        classif = ClassificacaoDocumento(**dado["resposta_esperada"]["classificacao"])
        dados_ext = DadosExtraidos(**dado["resposta_esperada"]["dados_extraidos"])

        decisao = executar_roteamento_clinico(classif, dados_ext, req.documento_texto, req.canal_origem)

        self.assertEqual(decisao.destino_principal, DestinoRoteamento.PRONTUARIO_ELETRONICO)
        self.assertFalse(decisao.requer_auditoria_humana)

    def test_cenario_2_urgencia(self):
        """Cenário 2: TEP Agudo deve ir para Fila de Emergência com alerta gerado."""
        caminho = os.path.join(self.pasta_dados, "2_urgencia", "exemplo.json")
        with open(caminho, "r", encoding="utf-8") as f:
            dado = json.load(f)

        req = DocumentoEntrada(**dado["requisicao_entrada"])
        classif = ClassificacaoDocumento(**dado["resposta_esperada"]["classificacao"])
        dados_ext = DadosExtraidos(**dado["resposta_esperada"]["dados_extraidos"])

        decisao = executar_roteamento_clinico(classif, dados_ext, req.documento_texto, req.canal_origem)

        self.assertEqual(decisao.destino_principal, DestinoRoteamento.FILA_EMERGENCIA)
        self.assertIsNotNone(decisao.notificacao_gerada)
        self.assertIn("TEP", decisao.notificacao_gerada.mensagem)

    def test_cenario_3_auditoria_humana(self):
        """Cenário 3: Receituário com rasura/ilegível deve ir para Human-in-the-Loop."""
        caminho = os.path.join(self.pasta_dados, "3_auditoria_humana", "exemplo.json")
        with open(caminho, "r", encoding="utf-8") as f:
            dado = json.load(f)

        req = DocumentoEntrada(**dado["requisicao_entrada"])
        classif = ClassificacaoDocumento(**dado["resposta_esperada"]["classificacao"])
        dados_ext = DadosExtraidos(**dado["resposta_esperada"]["dados_extraidos"])

        decisao = executar_roteamento_clinico(classif, dados_ext, req.documento_texto, req.canal_origem)

        self.assertEqual(decisao.destino_principal, DestinoRoteamento.FILA_REVISAO_HUMANA)
        self.assertTrue(decisao.requer_auditoria_humana)


# ============================================================================
# Urgência clínica: palavra inteira, sem acento, com negação (pytest)
# ============================================================================

def nivel_de(texto, **campos):
    return classificar_nivel_urgencia(texto, DadosExtraidos(**campos)).nivel


# A tabela de casos da especificação (4.3).
TABELA_DE_URGENCIA = [
    ("Tomar 1 comprimido de Losartana 50mg diariamente.", NivelUrgencia.NENHUM),
    ("Os exames indicariam apenas controle anual.", NivelUrgencia.NENHUM),
    ("Hemograma normal. PCR: 3,2 mg/L.", NivelUrgencia.NENHUM),
    ("Ausencia de sinais de TEP.", NivelUrgencia.NENHUM),
    ("Sinusite leve, sem sinais de gravidade.", NivelUrgencia.NENHUM),
    ("Paciente evoluiu com choque séptico.", NivelUrgencia.CRITICO),
    ("Parada cardiorrespiratória revertida após 4 ciclos.", NivelUrgencia.CRITICO),
    ("Quadro de insuficiência respiratória aguda.", NivelUrgencia.CRITICO),
    ("Paciente em situação de emergência.", NivelUrgencia.PRIORITARIO),
]


@pytest.mark.parametrize("texto, esperado", TABELA_DE_URGENCIA)
def test_tabela_de_urgencia(texto, esperado):
    assert nivel_de(texto) == esperado


def test_normalizar_texto_tira_acento_caixa_e_espacos():
    assert normalizar_texto("  Choque   SÉPTICO\n  Insuficiência ") == "choque septico insuficiencia"
    assert normalizar_texto(None) == ""


@pytest.mark.parametrize(
    "texto",
    [
        "Tomar 1 comprimido diariamente.",  # "diariamente" contém "iam"
        "Os valores variam conforme a dieta.",  # "variam" contém "iam"
        "Os raios radiam para a regiao.",  # "radiam" contém "iam"
        "Sepsia nao e o caso, somente estepe.",  # "sepse"/"tep" dentro de outras palavras
        "Atepa do avcado.",
    ],
)
def test_termo_dentro_de_outra_palavra_nao_conta(texto):
    assert nivel_de(texto) == NivelUrgencia.NENHUM


@pytest.mark.parametrize(
    "texto, termo",
    [
        ("Suspeita de TEP.", "tep"),
        ("Suspeita de tep!", "tep"),
        ("Quadro de AVC isquemico", "avc"),
        ("IAM com supra de ST", "iam"),
        ("Evolui com CHOQUE SEPTICO.", "choque septico"),
        ("Evolui com choque séptico.", "choque septico"),
        ("Paciente com Anafilaxia.", "anafilaxia"),
    ],
)
def test_termo_critico_com_acento_caixa_e_pontuacao_e_achado(texto, termo):
    achado = classificar_nivel_urgencia(texto, DadosExtraidos())

    assert (achado.nivel, achado.termo) == (NivelUrgencia.CRITICO, termo)


@pytest.mark.parametrize(
    "texto",
    [
        "Sem sinais de TEP.",
        "Ausência de sinais de TEP.",
        "Nao ha sinais de infarto.",
        "Paciente nega dispneia súbita.",
        "Negativo para sepse.",
        "Afastado TEP.",
        "Afastada hemorragia.",
        "Descartado infarto.",
        "Descartada embolia pulmonar.",
        "Sem sinais clinicos evidentes de TEP.",  # o termo é a 5ª palavra depois de "sem"
    ],
)
def test_negacao_ate_cinco_palavras_antes_do_termo_anula_o_achado(texto):
    assert nivel_de(texto) == NivelUrgencia.NENHUM


def test_negacao_a_mais_de_cinco_palavras_nao_anula():
    # O termo é a 6ª palavra depois do "sem": fora da janela.
    assert nivel_de("Sem qualquer sinal clinico evidente de TEP.") == NivelUrgencia.CRITICO


def test_negacao_de_uma_frase_nao_alcanca_a_frase_seguinte():
    assert nivel_de("Sem queixas. TEP agudo confirmado.") == NivelUrgencia.CRITICO
    assert nivel_de("Sem queixas\nTEP agudo confirmado") == NivelUrgencia.CRITICO


def test_negacao_do_prioritario_tambem_vale():
    assert nivel_de("Sinusite leve, sem sinais de gravidade.") == NivelUrgencia.NENHUM


def test_limitacao_conhecida_negacao_depois_do_termo_nao_e_tratada():
    # Documentado: "TEP descartado" ainda é tratado como TEP (cai no lado seguro: mais atenção, não menos).
    assert nivel_de("TEP descartado.") == NivelUrgencia.CRITICO


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("Hemograma normal. PCR: 3,2 mg/L.", NivelUrgencia.NENHUM),
        ("PCR 3.2 mg/L.", NivelUrgencia.NENHUM),
        ("Proteína C reativa (PCR) elevada.", NivelUrgencia.NENHUM),
        ("PCR positivo para SARS-CoV-2.", NivelUrgencia.NENHUM),
        ("Paciente em PCR, iniciada RCP.", NivelUrgencia.CRITICO),
        ("PCR revertida apos reanimacao.", NivelUrgencia.CRITICO),
        ("PCR com parada documentada.", NivelUrgencia.CRITICO),
        ("PCR apos 3 ciclos de adrenalina.", NivelUrgencia.CRITICO),
        # Com contexto das duas famílias na mesma frase, a de proteína vence.
        ("PCR 3,2 mg/L apos parada.", NivelUrgencia.NENHUM),
        # O contexto em OUTRA frase não vale: "parada" solta não é termo crítico.
        ("Houve parada. PCR de 3,2 no exame.", NivelUrgencia.NENHUM),
    ],
)
def test_pcr_so_conta_como_parada_com_contexto_na_mesma_frase(texto, esperado):
    assert nivel_de(texto) == esperado


def test_diagnostico_e_observacoes_tambem_sao_analisados():
    assert nivel_de("Paciente estavel.", diagnostico_principal="TEP agudo") == NivelUrgencia.CRITICO
    assert nivel_de("Paciente estavel.", observacoes_clinicas="Quadro de sepse") == NivelUrgencia.CRITICO
    assert nivel_de("Paciente estavel.", diagnostico_principal="Sem sinais de TEP") == NivelUrgencia.NENHUM


def test_negacao_de_um_campo_nao_alcanca_o_campo_seguinte():
    # O texto termina em "sem" e o diagnóstico começa com o termo: campos separados, sem negação cruzada.
    achado = classificar_nivel_urgencia("O paciente esta sem", DadosExtraidos(diagnostico_principal="TEP agudo"))

    assert achado.nivel == NivelUrgencia.CRITICO


def test_critico_vence_o_prioritario_no_mesmo_documento():
    achado = classificar_nivel_urgencia("Situacao de emergencia por TEP agudo.", DadosExtraidos())

    assert (achado.nivel, achado.termo) == (NivelUrgencia.CRITICO, "tep")


# --- detectar_urgencia_clinica continua compatível ---------------------------------------

def test_detectar_urgencia_clinica_mantem_a_assinatura_e_o_retorno_para_o_critico():
    urgente, justificativa = detectar_urgencia_clinica("Paciente evoluiu com choque séptico.", DadosExtraidos())

    assert urgente is True
    assert "CHOQUE SEPTICO" in justificativa


def test_detectar_urgencia_clinica_so_e_verdadeiro_para_o_critico():
    urgente_prioritario, justificativa = detectar_urgencia_clinica("Paciente em situação de emergência.", DadosExtraidos())
    urgente_nenhum, _ = detectar_urgencia_clinica("Tomar 1 comprimido diariamente.", DadosExtraidos())

    assert urgente_prioritario is False
    assert "emergencia" in justificativa
    assert urgente_nenhum is False


# --- Destino por nível --------------------------------------------------------------------

def classificacao_de_rotina(score=0.95, **campos):
    return ClassificacaoDocumento(tipo_documento="Laudo de Exame Laboratorial", score_confianca_classificacao=score, **campos)


def dados_completos(**campos):
    base = dict(
        paciente=Paciente(nome="Paciente Exemplo", idade=40),
        medico_solicitante=MedicoSolicitante(nome="Dra. Clara Ficticia", crm="145892-SP"),
        exame_realizado="Hemograma",
        diagnostico_principal="Sem alteracoes",
    )
    base.update(campos)
    return DadosExtraidos(**base)


def test_critico_vai_para_a_emergencia_com_alerta():
    decisao = executar_roteamento_clinico(classificacao_de_rotina(), dados_completos(), "Paciente com choque séptico.", "Internacao")

    assert decisao.destino_principal == DestinoRoteamento.FILA_EMERGENCIA
    assert decisao.requer_auditoria_humana is False
    assert decisao.notificacao_gerada.canal == "Alerta_Plantao_Medico"
    assert "Internacao" in decisao.notificacao_gerada.mensagem


def test_prioritario_vai_para_revisao_humana_e_a_justificativa_diz_o_termo():
    decisao = executar_roteamento_clinico(classificacao_de_rotina(), dados_completos(), "Paciente em situação de emergência.")

    assert decisao.destino_principal == DestinoRoteamento.FILA_REVISAO_HUMANA
    assert decisao.requer_auditoria_humana is True
    assert "'emergencia'" in decisao.justificativa_roteamento
    assert decisao.notificacao_gerada is None


def test_sem_termo_o_fluxo_normal_segue():
    decisao = executar_roteamento_clinico(classificacao_de_rotina(), dados_completos(), "Hemograma normal. PCR: 3,2 mg/L.")

    assert decisao.destino_principal == DestinoRoteamento.PRONTUARIO_ELETRONICO
    assert decisao.requer_auditoria_humana is False


def test_texto_com_termo_dentro_de_outra_palavra_nao_vai_para_a_emergencia():
    decisao = executar_roteamento_clinico(classificacao_de_rotina(), dados_completos(), "Tomar 1 comprimido de Losartana 50mg diariamente.")

    assert decisao.destino_principal == DestinoRoteamento.PRONTUARIO_ELETRONICO


def test_prioritario_e_score_baixo_citam_os_dois_motivos():
    decisao = executar_roteamento_clinico(
        classificacao_de_rotina(score=0.40), dados_completos(), "Paciente em situação de emergência."
    )

    assert decisao.destino_principal == DestinoRoteamento.FILA_REVISAO_HUMANA
    assert "'emergencia'" in decisao.justificativa_roteamento
    assert "Score de confiança baixo (0.40)" in decisao.justificativa_roteamento


# --- O grafo aplica o nível de prioridade a partir da função, não da justificativa ---------

def estado_do_grafo(texto, **campos_da_classificacao):
    return {
        "entrada": DocumentoEntrada(documento_id="DOC-NIVEL", documento_texto=texto),
        "classificacao": classificacao_de_rotina(**campos_da_classificacao),
        "dados_extraidos": dados_completos(),
        "texto_processado": texto,
    }


def test_grafo_marca_prioridade_prioritario_quando_so_ha_termo_prioritario():
    saida = no_avaliar_consistencia_e_urgencia(estado_do_grafo("Paciente em situação de emergência."))

    assert saida["classificacao"].nivel_prioridade == NivelPrioridade.PRIORITARIO
    assert saida["decisao_roteamento"].destino_principal == DestinoRoteamento.FILA_REVISAO_HUMANA
    assert saida["caminho_decisao"] == "revisao_humana"


def test_grafo_nao_rebaixa_uma_prioridade_maior_que_o_llm_deu():
    saida = no_avaliar_consistencia_e_urgencia(
        estado_do_grafo("Paciente em situação de emergência.", nivel_prioridade=NivelPrioridade.URGENTE)
    )

    assert saida["classificacao"].nivel_prioridade == NivelPrioridade.URGENTE


def test_grafo_nao_mexe_na_prioridade_sem_termo():
    saida = no_avaliar_consistencia_e_urgencia(estado_do_grafo("Hemograma normal. PCR: 3,2 mg/L."))

    assert saida["classificacao"].nivel_prioridade == NivelPrioridade.ROTINA
    assert saida["caminho_decisao"] == "fluxo_padrao"


if __name__ == "__main__":
    unittest.main()
