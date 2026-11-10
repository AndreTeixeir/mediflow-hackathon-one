"""
MediFlow - Testes de Integração do Grafo de Decisão (LangGraph) e Visão Multimodal
Hackathon ONE G10 - Grupo 10: MediFlow

Responsável: Ingrid Soares
Mapeamento: tests/test_agente.py
Execução: python -m pytest
"""

import base64
import io
import json
import os
import unittest

from PIL import Image
from pypdf import PdfWriter

from agente import analisar_documento, executar_triagem_mediflow, ingerir_documento_clinico
from agente.visao_multimodal import obter_chave_llm
from contratos.modelos import (
    DestinoRoteamento,
    DocumentoEntrada,
    NivelPrioridade,
    StatusProcessamento,
    TipoDocumento,
)


class TestAgenteMediFlowLangGraph(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.raiz_projeto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.pasta_dados = os.path.join(cls.raiz_projeto, "dados_teste")

    def _carregar_cenario(self, subpasta: str) -> dict:
        caminho = os.path.join(self.pasta_dados, subpasta, "exemplo.json")
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_grafo_cenario_1_rotina_ponta_a_ponta(self):
        """Executa o Grafo LangGraph completo no Cenário 1 (Rotina Ambulatorial)."""
        cenario = self._carregar_cenario("1_rotina")
        entrada = DocumentoEntrada(**cenario["requisicao_entrada"])

        resposta = executar_triagem_mediflow(entrada)

        self.assertEqual(resposta.status, StatusProcessamento.PROCESSADO)
        self.assertEqual(resposta.documento_id, "DOC-CLIN-2026-1021")
        self.assertEqual(resposta.classificacao.tipo_documento, TipoDocumento.LAUDO_EXAME_LABORATORIAL.value)
        self.assertEqual(resposta.classificacao.nivel_prioridade, NivelPrioridade.ROTINA)
        self.assertEqual(resposta.dados_extraidos.paciente.nome, "Mariana Alcantara de Souza")
        self.assertEqual(resposta.dados_extraidos.paciente.idade, 34)
        self.assertEqual(resposta.dados_extraidos.medico_solicitante.crm, "98214-SP")
        self.assertEqual(resposta.dados_extraidos.cid_sugerido, "Z01.7")
        self.assertEqual(resposta.decisao_roteamento.destino_principal, DestinoRoteamento.PRONTUARIO_ELETRONICO)
        self.assertFalse(resposta.decisao_roteamento.requer_auditoria_humana)
        self.assertIsNone(resposta.armazenamento_oci)

    def test_grafo_cenario_2_urgencia_tep_agudo_ponta_a_ponta(self):
        """Executa o Grafo LangGraph completo no Cenário 2 (Urgência Crítica - TEP Agudo)."""
        cenario = self._carregar_cenario("2_urgencia")
        entrada = DocumentoEntrada(**cenario["requisicao_entrada"])

        resposta = executar_triagem_mediflow(entrada)

        self.assertEqual(resposta.status, StatusProcessamento.PROCESSADO)
        self.assertEqual(resposta.documento_id, "DOC-CLIN-2026-8942")
        self.assertEqual(resposta.classificacao.tipo_documento, TipoDocumento.LAUDO_EXAME_IMAGEM.value)
        self.assertEqual(resposta.classificacao.nivel_prioridade, NivelPrioridade.URGENTE)
        self.assertEqual(resposta.dados_extraidos.paciente.nome, "Carlos Eduardo Mendes")
        self.assertEqual(resposta.dados_extraidos.paciente.idade, 52)
        self.assertEqual(resposta.dados_extraidos.medico_solicitante.crm, "145892-SP")
        self.assertEqual(resposta.dados_extraidos.cid_sugerido, "I26.9")
        self.assertEqual(resposta.decisao_roteamento.destino_principal, DestinoRoteamento.FILA_EMERGENCIA)
        self.assertFalse(resposta.decisao_roteamento.requer_auditoria_humana)
        self.assertIsNotNone(resposta.decisao_roteamento.notificacao_gerada)
        self.assertEqual(resposta.decisao_roteamento.notificacao_gerada.canal, "Alerta_Plantao_Medico")
        self.assertTrue(any(termo in resposta.decisao_roteamento.notificacao_gerada.mensagem for termo in ("TEP", "Tromboembolismo")))
        self.assertIsNone(resposta.armazenamento_oci)

    def test_grafo_cenario_3_auditoria_humana_ponta_a_ponta(self):
        """Executa o Grafo LangGraph completo no Cenário 3 (Human-in-the-Loop / Ilegível)."""
        cenario = self._carregar_cenario("3_auditoria_humana")
        entrada = DocumentoEntrada(**cenario["requisicao_entrada"])

        resposta = executar_triagem_mediflow(entrada)

        self.assertEqual(resposta.status, StatusProcessamento.PENDENTE_AUDITORIA)
        self.assertEqual(resposta.documento_id, "DOC-CLIN-2026-3319")
        self.assertEqual(resposta.classificacao.tipo_documento, TipoDocumento.RECEITUARIO.value)
        self.assertLess(resposta.classificacao.score_confianca_classificacao, 0.75)
        self.assertEqual(resposta.decisao_roteamento.destino_principal, DestinoRoteamento.FILA_REVISAO_HUMANA)
        self.assertTrue(resposta.decisao_roteamento.requer_auditoria_humana)
        self.assertIsNone(resposta.armazenamento_oci)

    def test_grafo_roteamento_receituario_farmacia(self):
        """Valida roteamento de Receituário Médico legível e consistente para Farmácia Hospitalar."""
        entrada = DocumentoEntrada(
            documento_id="DOC-CLIN-2026-5001",
            tipo_arquivo="PDF",
            documento_texto=(
                "HOSPITAL CENTRAL - RECEITUÁRIO MÉDICO.\n"
                "Paciente: João Batista Oliveira, 61 anos.\n"
                "Solicitante: Dra. Camila Rocha CRM-SP 112233.\n"
                "Prescrição:\n"
                "1. Losartana Potássica 50mg - Tomar 1 comprimido de 12 em 12 horas.\n"
                "CONCLUSÃO: Controle de hipertensão arterial sistêmica."
            ),
            canal_origem="Ambulatorio",
        )
        resposta = executar_triagem_mediflow(entrada)

        self.assertEqual(resposta.status, StatusProcessamento.PROCESSADO)
        self.assertEqual(resposta.decisao_roteamento.destino_principal, DestinoRoteamento.FARMACIA_HOSPITALAR)
        self.assertFalse(resposta.decisao_roteamento.requer_auditoria_humana)
        self.assertIsNone(resposta.armazenamento_oci)

    def test_interface_analisar_documento_compativel_api(self):
        """Valida a assinatura analisar_documento(entrada) -> (ClassificacaoDocumento, DadosExtraidos)."""
        cenario = self._carregar_cenario("1_rotina")
        entrada = DocumentoEntrada(**cenario["requisicao_entrada"])
        classificacao, dados = analisar_documento(entrada)

        self.assertEqual(classificacao.tipo_documento, TipoDocumento.LAUDO_EXAME_LABORATORIAL.value)
        self.assertEqual(dados.paciente.nome, "Mariana Alcantara de Souza")

    def test_ingestao_multimodal_imagem_base64(self):
        """Valida pipeline de leitura e normalização de imagem clínica em Base64."""
        img = Image.new("RGBA", (320, 240), color=(255, 255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64_data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")

        entrada = DocumentoEntrada(
            documento_id="DOC-IMG-2026-0001",
            tipo_arquivo="PNG",
            documento_base64=b64_data_uri,
            canal_origem="Pronto_Socorro",
        )
        conteudo = ingerir_documento_clinico(entrada)

        self.assertTrue(conteudo.requer_visao_multimodal)
        self.assertIsNotNone(conteudo.bytes_midia)
        self.assertIn(conteudo.mime_type, ("image/jpeg", "image/png"))

    def test_ingestao_pdf_digitalizado_aciona_visao_multimodal(self):
        """Valida que um PDF sem camada de texto digital (escaneado) aciona a visão multimodal."""
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        buf_pdf = io.BytesIO()
        writer.write(buf_pdf)
        b64_pdf = base64.b64encode(buf_pdf.getvalue()).decode("utf-8")

        entrada = DocumentoEntrada(
            documento_id="DOC-PDF-SCAN-2026-0002",
            tipo_arquivo="PDF",
            documento_base64=b64_pdf,
        )
        conteudo = ingerir_documento_clinico(entrada)

        self.assertTrue(conteudo.requer_visao_multimodal)
        self.assertEqual(conteudo.mime_type, "application/pdf")
        self.assertIsNotNone(conteudo.bytes_midia)

    def test_schema_resultado_extracao_llm_compativel_com_gemini(self):
        """Valida que o schema de saída estruturada para o Gemini não contém 'examples' que quebram o SDK."""
        from google.genai import types, _transformers
        from prompts.extracao_clinica import ResultadoExtracaoLLM

        schema_json = ResultadoExtracaoLLM.model_json_schema()

        def _tem_examples(d):
            if isinstance(d, dict):
                if "examples" in d:
                    return True
                return any(_tem_examples(v) for v in d.values())
            elif isinstance(d, list):
                return any(_tem_examples(i) for i in d)
            return False

        self.assertFalse(_tem_examples(schema_json), "O JSON Schema gerado não deve conter a chave 'examples'.")

        # Testa validação direta no conversor de schema do SDK google-genai
        schema_sdk = _transformers.t_schema(None, ResultadoExtracaoLLM)
        self.assertIsInstance(schema_sdk, types.Schema)


def test_conftest_isola_os_testes_da_chave_do_llm():
    """As três variáveis ficam vazias (e não removidas) para o load_dotenv() não trazer a chave do .env."""
    for variavel in ("LLM_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        assert os.environ[variavel] == ""
    assert obter_chave_llm() is None


if __name__ == "__main__":
    unittest.main()
