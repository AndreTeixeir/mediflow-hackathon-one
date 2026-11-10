"""
MediFlow - Testes da auditoria humana pela API
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: tests/test_auditoria.py
Rodam em modo local (pasta temporária) e sem chave do Gemini. Cobrem a fila, a decisão (rotina,
urgente e rejeitado), o histórico e cada código de erro (404, 409, 422 e 503).
Execução (a partir da raiz): python -m pytest tests/test_auditoria.py -v
"""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from api.main import app, obter_repositorio
from armazenamento.repositorio import FalhaArmazenamento, RepositorioLocal

PASTA_DADOS = Path(__file__).resolve().parent.parent / "dados_teste"
AUDITORIA = "mediflow-auditoria-humana"
ROTINA = "mediflow-rotina"
URGENTES = "mediflow-urgentes"
REJEITADOS = "mediflow-rejeitados"
RECEBIDOS = "mediflow-recebidos"
MOMENTO = datetime(2026, 11, 10, 15, 30, tzinfo=timezone.utc)
SEGREDO = "detalhe interno que não pode vazar"

LAUDO_SEM_CRM = (
    "LABORATORIO CENTRAL - LAUDO DE EXAME LABORATORIAL.\n"
    "Paciente: Mariana Alcantara de Souza, 34 anos.\n"
    "Solicitante: Dr. Roberto Viana.\n"
    "Exame: Hemograma completo.\n"
    "CONCLUSÃO: Parâmetros dentro dos limites da normalidade."
)


@pytest.fixture
def repositorio(tmp_path):
    return RepositorioLocal(tmp_path)


@pytest.fixture
def cliente(repositorio, novo_cliente):
    app.dependency_overrides[obter_repositorio] = lambda: repositorio
    yield novo_cliente()
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def momento_fixo(monkeypatch):
    monkeypatch.setattr("api.auditoria._agora", lambda: MOMENTO)


def cenario(pasta):
    with open(PASTA_DADOS / pasta / "exemplo.json", encoding="utf-8") as f:
        return json.load(f)["requisicao_entrada"]


def enviar(cliente, entrada, documento_id=None):
    if documento_id:
        entrada = dict(entrada, documento_id=documento_id)
    resposta = cliente.post("/api/v1/triagem", json=entrada)
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def enviar_pendente(cliente, documento_id="DOC-PENDENTE"):
    """O cenário 3 (receituário rasurado, canal Ambulatorio, sem CRM) vai para a revisão humana."""
    corpo = enviar(cliente, cenario("3_auditoria_humana"), documento_id)
    assert corpo["status"] == "pendente_auditoria"
    return corpo


def enviar_laudo_pendente(cliente, documento_id="DOC-LAUDO"):
    corpo = enviar(
        cliente, {"documento_id": documento_id, "documento_texto": LAUDO_SEM_CRM, "canal_origem": "Internacao"}
    )
    assert corpo["status"] == "pendente_auditoria"
    return corpo


def decisao(destino="urgente", revisor="Lethicia", justificativa="Dados conferidos com o original."):
    return {"destino": destino, "revisor": revisor, "justificativa": justificativa}


def decidir(cliente, documento_id, **campos):
    return cliente.post(f"/api/v1/auditoria/{documento_id}/decisao", json=decisao(**campos))


def lido(tmp_path, bucket, documento_id):
    return json.loads((tmp_path / bucket / f"{documento_id}.json").read_text(encoding="utf-8"))


def existe(tmp_path, bucket, documento_id):
    return (tmp_path / bucket / f"{documento_id}.json").exists()


def ids_da_fila(cliente):
    return [item["documento_id"] for item in cliente.get("/api/v1/auditoria/pendentes").json()]


# ============================================================================
# GET /api/v1/auditoria/pendentes
# ============================================================================

def test_fila_vazia_devolve_lista_vazia(cliente):
    resposta = cliente.get("/api/v1/auditoria/pendentes")

    assert resposta.status_code == 200
    assert resposta.json() == []


def test_fila_traz_o_que_a_pessoa_precisa_para_decidir(cliente):
    pendente = enviar_pendente(cliente, "DOC-FILA")

    [item] = cliente.get("/api/v1/auditoria/pendentes").json()

    assert item["documento_id"] == "DOC-FILA"
    assert item["tipo_documento"] == "Receituário Médico"
    assert item["nivel_prioridade"] == pendente["classificacao"]["nivel_prioridade"]
    assert item["score_confianca_classificacao"] == pendente["classificacao"]["score_confianca_classificacao"]
    assert item["justificativa_roteamento"] == pendente["decisao_roteamento"]["justificativa_roteamento"]
    assert item["dados_extraidos"] == pendente["dados_extraidos"]
    datetime.fromisoformat(item["data"].replace("Z", "+00:00"))  # ISO 8601
    assert item["data"].endswith("Z")  # UTC


def test_fila_so_lista_o_bucket_de_auditoria(cliente):
    enviar_pendente(cliente, "DOC-RETIDO")
    enviar(cliente, cenario("1_rotina"), "DOC-ROTINA")
    enviar(cliente, cenario("2_urgencia"), "DOC-URGENTE")

    assert ids_da_fila(cliente) == ["DOC-RETIDO"]


def test_fila_vem_do_documento_que_espera_ha_mais_tempo_para_o_mais_novo(cliente, tmp_path):
    for documento_id, instante in [("DOC-B", 3000), ("DOC-A", 1000), ("DOC-C", 2000)]:
        enviar_pendente(cliente, documento_id)
        os.utime(tmp_path / AUDITORIA / f"{documento_id}.json", (instante, instante))

    assert ids_da_fila(cliente) == ["DOC-A", "DOC-C", "DOC-B"]


def test_fila_devolve_no_maximo_200_itens_os_mais_antigos(cliente, repositorio, tmp_path):
    modelo = enviar_pendente(cliente, "DOC-MODELO")
    for numero in range(204):
        documento_id = f"DOC-LOTE-{numero:03d}"
        repositorio.salvar_json(AUDITORIA, f"{documento_id}.json", dict(modelo, documento_id=documento_id))
        os.utime(tmp_path / AUDITORIA / f"{documento_id}.json", (10_000 + numero, 10_000 + numero))
    os.utime(tmp_path / AUDITORIA / "DOC-MODELO.json", (99_999, 99_999))

    ids = ids_da_fila(cliente)

    assert len(ids) == 200
    assert ids[0] == "DOC-LOTE-000" and ids[-1] == "DOC-LOTE-199"


@pytest.mark.parametrize("conteudo", ['{"nao": "e uma triagem"}', "nao e json", "[1, 2, 3]", "\ufffd\ufffd"])
def test_um_objeto_ruim_nao_trava_a_fila_inteira(cliente, tmp_path, conteudo):
    enviar_pendente(cliente, "DOC-BOM")
    (tmp_path / AUDITORIA / "DOC-RUIM.json").write_text(conteudo, encoding="utf-8")

    resposta = cliente.get("/api/v1/auditoria/pendentes")

    assert resposta.status_code == 200
    assert [item["documento_id"] for item in resposta.json()] == ["DOC-BOM"]


def test_um_objeto_ruim_tambem_nao_trava_o_historico(cliente, tmp_path):
    enviar(cliente, cenario("1_rotina"), "DOC-BOM")
    (tmp_path / ROTINA / "DOC-RUIM.json").write_text("nao e json", encoding="utf-8")

    resposta = cliente.get("/api/v1/documentos")

    assert resposta.status_code == 200
    assert [h["documento_id"] for h in resposta.json()] == ["DOC-BOM"]


# ============================================================================
# POST /api/v1/auditoria/{documento_id}/decisao: urgente
# ============================================================================

def test_decisao_urgente_move_para_urgentes_e_grava_a_auditoria_com_o_que_o_grafo_decidiu(cliente, tmp_path):
    pendente = enviar_pendente(cliente, "DOC-URG")

    resposta = decidir(cliente, "DOC-URG", destino="urgente", revisor="Lethicia", justificativa="Quadro agudo confirmado.")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["status"] == "processado"
    assert corpo["decisao_roteamento"]["destino_principal"] == "Fila_Emergencia_Medica"
    assert corpo["decisao_roteamento"]["requer_auditoria_humana"] is False
    assert corpo["armazenamento_oci"] == {"bucket": URGENTES, "caminho_objeto": "DOC-URG.json", "status_backup": "sucesso"}
    assert corpo["auditoria_humana"] == {
        "revisor": "Lethicia",
        "decidido_em": "2026-11-10T15:30:00Z",
        "destino": "urgente",
        "justificativa": "Quadro agudo confirmado.",
        "destino_original": "Fila_Revisao_Humana",
        "justificativa_original": pendente["decisao_roteamento"]["justificativa_roteamento"],
    }
    # O documento saiu da auditoria, entrou em urgentes com o registro, e o que veio do grafo foi mantido.
    assert not existe(tmp_path, AUDITORIA, "DOC-URG")
    assert lido(tmp_path, URGENTES, "DOC-URG") == corpo
    assert corpo["dados_extraidos"] == pendente["dados_extraidos"]
    assert "Lethicia" in corpo["decisao_roteamento"]["justificativa_roteamento"]
    assert "DOC-URG" not in ids_da_fila(cliente)


def test_decisao_urgente_gera_a_notificacao_com_o_canal_da_entrada_original(cliente):
    enviar_laudo_pendente(cliente, "DOC-ALERTA")

    corpo = decidir(cliente, "DOC-ALERTA", destino="urgente").json()

    notificacao = corpo["decisao_roteamento"]["notificacao_gerada"]
    assert notificacao["canal"] == "Alerta_Plantao_Medico"
    assert notificacao["mensagem"].startswith("ALERTA URGENTE: Laudo critico de")
    assert "Mariana Alcantara de Souza" in notificacao["mensagem"]
    assert "Internacao" in notificacao["mensagem"]  # o canal vem da entrada gravada em recebidos
    assert "CRM do médico ausente ou inválido" in notificacao["mensagem"]  # o laudo não tem CRM


def test_decisao_urgente_sem_a_entrada_original_diz_que_o_canal_nao_foi_informado(cliente, tmp_path):
    enviar_laudo_pendente(cliente, "DOC-SEM-ENTRADA")
    (tmp_path / RECEBIDOS / "DOC-SEM-ENTRADA.json").unlink()

    corpo = decidir(cliente, "DOC-SEM-ENTRADA", destino="urgente").json()

    assert "canal de origem não informado" in corpo["decisao_roteamento"]["notificacao_gerada"]["mensagem"]


def test_decisao_urgente_eleva_a_prioridade_para_urgente(cliente):
    enviar_laudo_pendente(cliente, "DOC-NIVEL")

    corpo = decidir(cliente, "DOC-NIVEL", destino="urgente").json()

    assert corpo["classificacao"]["nivel_prioridade"] == "Urgente"


# ============================================================================
# POST /api/v1/auditoria/{documento_id}/decisao: rotina e rejeitado
# ============================================================================

def test_decisao_rotina_de_um_receituario_vai_para_a_farmacia_no_bucket_de_rotina(cliente, tmp_path):
    enviar_pendente(cliente, "DOC-ROT")

    corpo = decidir(cliente, "DOC-ROT", destino="rotina").json()

    assert corpo["status"] == "processado"
    assert corpo["decisao_roteamento"]["destino_principal"] == "Farmacia_Hospitalar"
    assert corpo["decisao_roteamento"]["requer_auditoria_humana"] is False
    assert corpo["decisao_roteamento"]["notificacao_gerada"] is None
    assert corpo["armazenamento_oci"]["bucket"] == ROTINA
    assert corpo["auditoria_humana"]["destino"] == "rotina"
    assert lido(tmp_path, ROTINA, "DOC-ROT") == corpo
    assert not existe(tmp_path, AUDITORIA, "DOC-ROT")


def test_decisao_rotina_de_um_laudo_vai_para_o_prontuario(cliente):
    enviar_laudo_pendente(cliente, "DOC-LAUDO-ROT")

    corpo = decidir(cliente, "DOC-LAUDO-ROT", destino="rotina").json()

    assert corpo["decisao_roteamento"]["destino_principal"] == "Prontuario_Eletronico_do_Paciente"
    assert corpo["armazenamento_oci"]["bucket"] == ROTINA


def test_decisao_rejeitado_usa_o_destino_novo_e_o_bucket_de_rejeitados(cliente, tmp_path):
    enviar_pendente(cliente, "DOC-REJ")

    corpo = decidir(cliente, "DOC-REJ", destino="rejeitado", justificativa="Rasura impede a leitura.").json()

    assert corpo["status"] == "rejeitado"
    assert corpo["decisao_roteamento"]["destino_principal"] == "Documento_Rejeitado"
    assert corpo["decisao_roteamento"]["requer_auditoria_humana"] is False
    assert corpo["decisao_roteamento"]["notificacao_gerada"] is None
    assert corpo["armazenamento_oci"] == {"bucket": REJEITADOS, "caminho_objeto": "DOC-REJ.json", "status_backup": "sucesso"}
    assert corpo["auditoria_humana"]["destino"] == "rejeitado"
    assert corpo["auditoria_humana"]["destino_original"] == "Fila_Revisao_Humana"
    assert lido(tmp_path, REJEITADOS, "DOC-REJ") == corpo
    assert not existe(tmp_path, AUDITORIA, "DOC-REJ")


def test_revisor_e_justificativa_sao_aparados(cliente):
    enviar_pendente(cliente, "DOC-APARA")

    corpo = decidir(cliente, "DOC-APARA", revisor="  Ana  ", justificativa="   Conferido com o original.  ").json()

    assert corpo["auditoria_humana"]["revisor"] == "Ana"
    assert corpo["auditoria_humana"]["justificativa"] == "Conferido com o original."


def test_a_decisao_nao_mexe_na_entrada_original_em_recebidos(cliente, tmp_path):
    enviar_pendente(cliente, "DOC-RECEB")
    antes = (tmp_path / RECEBIDOS / "DOC-RECEB.json").read_bytes()

    decidir(cliente, "DOC-RECEB", destino="rejeitado")

    assert (tmp_path / RECEBIDOS / "DOC-RECEB.json").read_bytes() == antes


# ============================================================================
# POST: 404, 409, 422 e 503
# ============================================================================

def test_documento_inexistente_da_404(cliente):
    resposta = decidir(cliente, "DOC-NAO-EXISTE")

    assert resposta.status_code == 404
    assert resposta.json() == {"detail": "Documento não encontrado entre os pendentes de auditoria."}


def test_decidir_o_mesmo_documento_de_novo_da_404(cliente, tmp_path):
    enviar_pendente(cliente, "DOC-DUPLA")
    assert decidir(cliente, "DOC-DUPLA", destino="urgente").status_code == 200
    antes = (tmp_path / URGENTES / "DOC-DUPLA.json").read_bytes()

    segunda = decidir(cliente, "DOC-DUPLA", destino="rejeitado")

    assert segunda.status_code == 404
    assert (tmp_path / URGENTES / "DOC-DUPLA.json").read_bytes() == antes
    assert not existe(tmp_path, REJEITADOS, "DOC-DUPLA")


@pytest.mark.parametrize("pasta", ["1_rotina", "2_urgencia"])
def test_documento_que_nao_esta_pendente_da_404(cliente, tmp_path, pasta):
    # Foi roteado direto (rotina ou urgência): nunca esteve na fila de auditoria.
    entrada = cenario(pasta)
    enviar(cliente, entrada)
    antes = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}

    resposta = decidir(cliente, entrada["documento_id"])

    assert resposta.status_code == 404
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json")} == antes


def test_destino_que_ja_tem_o_documento_da_409_e_nao_muda_nada(cliente, repositorio, tmp_path):
    enviar_pendente(cliente, "DOC-CONFLITO")
    repositorio.salvar_json(URGENTES, "DOC-CONFLITO.json", {"previo": True})
    antes_origem = (tmp_path / AUDITORIA / "DOC-CONFLITO.json").read_bytes()

    resposta = decidir(cliente, "DOC-CONFLITO", destino="urgente")

    assert resposta.status_code == 409
    assert resposta.json() == {"detail": "Já existe um documento com este ID no destino da decisão."}
    assert (tmp_path / AUDITORIA / "DOC-CONFLITO.json").read_bytes() == antes_origem
    assert lido(tmp_path, URGENTES, "DOC-CONFLITO") == {"previo": True}


@pytest.mark.parametrize(
    "corpo",
    [
        {"destino": "aprovado", "revisor": "Ana", "justificativa": "Conferido com o original."},
        {"destino": "Fila_Emergencia_Medica", "revisor": "Ana", "justificativa": "Conferido com o original."},
        {"destino": "rotina", "revisor": "", "justificativa": "Conferido com o original."},
        {"destino": "rotina", "revisor": "   ", "justificativa": "Conferido com o original."},
        {"destino": "rotina", "revisor": "A" * 81, "justificativa": "Conferido com o original."},
        {"destino": "rotina", "revisor": "Ana", "justificativa": "Ok"},
        {"destino": "rotina", "revisor": "Ana", "justificativa": "x" * 501},
        {"destino": "rotina", "revisor": "Ana"},
        {"revisor": "Ana", "justificativa": "Conferido com o original."},
        {},
    ],
)
def test_corpo_invalido_da_422_sem_mover_nada(cliente, tmp_path, corpo):
    enviar_pendente(cliente, "DOC-422")
    antes = (tmp_path / AUDITORIA / "DOC-422.json").read_bytes()

    resposta = cliente.post("/api/v1/auditoria/DOC-422/decisao", json=corpo)

    assert resposta.status_code == 422
    assert all(sorted(erro) == ["loc", "msg", "type"] for erro in resposta.json()["detail"])  # sem o campo input
    assert (tmp_path / AUDITORIA / "DOC-422.json").read_bytes() == antes


def test_corpo_que_nao_e_json_da_422(cliente):
    enviar_pendente(cliente, "DOC-MULTIPART")
    corpo = b'--XX\r\nContent-Disposition: form-data; name="a"; filename="a.bin"\r\n\r\n\xff\xfe\x00\x80\r\n--XX--\r\n'

    resposta = cliente.post(
        "/api/v1/auditoria/DOC-MULTIPART/decisao", content=corpo, headers={"content-type": "multipart/form-data; boundary=XX"}
    )

    assert resposta.status_code == 422


@pytest.mark.parametrize("documento_id", ["DOC.1", "DOC_1", "DOC%20ESPACO", "A" * 65, "acentuação", "%2E%2E"])
def test_documento_id_invalido_na_rota_da_422(cliente, documento_id):
    resposta = cliente.post(f"/api/v1/auditoria/{documento_id}/decisao", json=decisao())

    assert resposta.status_code == 422


class RepositorioQueFalha(RepositorioLocal):
    """Falha onde o teste mandar, com uma mensagem que não pode aparecer na resposta."""

    def __init__(self, base, **falhas):
        super().__init__(base)
        self.falhas = falhas

    def _falhar(self, operacao):
        if self.falhas.get(operacao):
            raise FalhaArmazenamento(SEGREDO)

    def listar(self, bucket):
        self._falhar("listar")
        return super().listar(bucket)

    def ler_json(self, bucket, nome_objeto):
        self._falhar("ler")
        return super().ler_json(bucket, nome_objeto)

    def mover_json(self, bucket_origem, bucket_destino, nome_objeto, conteudo):
        self._falhar("mover")
        return super().mover_json(bucket_origem, bucket_destino, nome_objeto, conteudo)


@pytest.fixture
def cliente_com_falha(tmp_path, novo_cliente):
    def criar(**falhas):
        repo = RepositorioQueFalha(tmp_path, **falhas)
        app.dependency_overrides[obter_repositorio] = lambda: repo
        return novo_cliente()

    yield criar
    app.dependency_overrides.clear()


@pytest.mark.parametrize("rota", ["/api/v1/auditoria/pendentes", "/api/v1/documentos"])
def test_falha_ao_listar_da_503_sem_vazar_o_detalhe(cliente_com_falha, rota):
    resposta = cliente_com_falha(listar=True).get(rota)

    assert resposta.status_code == 503
    assert resposta.json() == {"detail": "Falha ao acessar o armazenamento."}
    assert SEGREDO not in resposta.text


def test_falha_ao_ler_o_documento_na_decisao_da_503(cliente_com_falha, cliente):
    enviar_pendente(cliente, "DOC-LER")

    resposta = cliente_com_falha(ler=True).post("/api/v1/auditoria/DOC-LER/decisao", json=decisao())

    assert resposta.status_code == 503
    assert SEGREDO not in resposta.text


def test_falha_ao_mover_da_503_e_o_documento_continua_pendente(cliente_com_falha, cliente, tmp_path):
    enviar_pendente(cliente, "DOC-MOVER")
    antes = (tmp_path / AUDITORIA / "DOC-MOVER.json").read_bytes()

    resposta = cliente_com_falha(mover=True).post("/api/v1/auditoria/DOC-MOVER/decisao", json=decisao())

    assert resposta.status_code == 503
    assert resposta.json() == {"detail": "Falha ao acessar o armazenamento."}
    assert SEGREDO not in resposta.text
    assert (tmp_path / AUDITORIA / "DOC-MOVER.json").read_bytes() == antes
    assert not existe(tmp_path, URGENTES, "DOC-MOVER")


@pytest.mark.parametrize("conteudo", ['{"nao": "e uma triagem"}', "nao e json"])
def test_documento_pendente_ilegivel_da_503_e_nao_e_movido(cliente, tmp_path, conteudo):
    (tmp_path / AUDITORIA).mkdir()
    (tmp_path / AUDITORIA / "DOC-LIXO.json").write_text(conteudo, encoding="utf-8")

    resposta = decidir(cliente, "DOC-LIXO")

    assert resposta.status_code == 503
    assert resposta.json() == {"detail": "O documento pendente está ilegível no armazenamento."}
    assert existe(tmp_path, AUDITORIA, "DOC-LIXO")


def test_entrada_original_ilegivel_nao_impede_a_decisao_urgente(cliente, tmp_path):
    enviar_laudo_pendente(cliente, "DOC-ENTRADA-RUIM")
    (tmp_path / RECEBIDOS / "DOC-ENTRADA-RUIM.json").write_text("nao e json", encoding="utf-8")

    resposta = decidir(cliente, "DOC-ENTRADA-RUIM", destino="urgente")

    assert resposta.status_code == 200
    assert "canal de origem não informado" in resposta.json()["decisao_roteamento"]["notificacao_gerada"]["mensagem"]


def test_duas_decisoes_ao_mesmo_tempo_para_destinos_diferentes_resultam_em_uma_so(cliente, repositorio, tmp_path, novo_cliente):
    enviar_pendente(cliente, "DOC-CORRIDA")
    largada = threading.Barrier(2)

    def decidir_em_thread(destino):
        thread_cliente = novo_cliente()
        largada.wait()
        return thread_cliente.post("/api/v1/auditoria/DOC-CORRIDA/decisao", json=decisao(destino=destino)).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codigos = sorted(pool.map(decidir_em_thread, ["urgente", "rejeitado"]))

    assert codigos == [200, 404]  # uma decisão vale, a outra encontra o documento já movido
    onde_ficou = [b for b in (AUDITORIA, ROTINA, URGENTES, REJEITADOS) if existe(tmp_path, b, "DOC-CORRIDA")]
    assert len(onde_ficou) == 1 and onde_ficou[0] in (URGENTES, REJEITADOS)


# ============================================================================
# GET /api/v1/documentos
# ============================================================================

def test_historico_vazio(cliente):
    resposta = cliente.get("/api/v1/documentos")

    assert resposta.status_code == 200
    assert resposta.json() == []


def test_historico_junta_os_quatro_buckets_do_mais_novo_para_o_mais_antigo(cliente, tmp_path):
    enviar(cliente, cenario("1_rotina"), "DOC-H-ROTINA")
    enviar(cliente, cenario("2_urgencia"), "DOC-H-URGENTE")
    enviar_pendente(cliente, "DOC-H-RETIDO")
    enviar_pendente(cliente, "DOC-H-REJEITADO")
    decidir(cliente, "DOC-H-REJEITADO", destino="rejeitado")
    for documento_id, bucket, instante in [
        ("DOC-H-ROTINA", ROTINA, 1000), ("DOC-H-URGENTE", URGENTES, 2000),
        ("DOC-H-RETIDO", AUDITORIA, 3000), ("DOC-H-REJEITADO", REJEITADOS, 4000),
    ]:
        os.utime(tmp_path / bucket / f"{documento_id}.json", (instante, instante))

    historico = cliente.get("/api/v1/documentos").json()

    assert [(h["documento_id"], h["bucket"]) for h in historico] == [
        ("DOC-H-REJEITADO", REJEITADOS), ("DOC-H-RETIDO", AUDITORIA), ("DOC-H-URGENTE", URGENTES), ("DOC-H-ROTINA", ROTINA),
    ]
    rejeitado, retido, urgente, rotina = historico
    assert (rejeitado["status"], rejeitado["destino_principal"]) == ("rejeitado", "Documento_Rejeitado")
    assert (retido["status"], retido["destino_principal"]) == ("pendente_auditoria", "Fila_Revisao_Humana")
    assert (urgente["status"], urgente["destino_principal"], urgente["nivel_prioridade"]) == (
        "processado", "Fila_Emergencia_Medica", "Urgente",
    )
    assert (rotina["status"], rotina["destino_principal"], rotina["nivel_prioridade"]) == (
        "processado", "Prontuario_Eletronico_do_Paciente", "Rotina",
    )
    assert all(h["data"].endswith("Z") for h in historico)
    assert sorted(historico[0]) == ["bucket", "data", "destino_principal", "documento_id", "nivel_prioridade", "status"]


def test_historico_nao_inclui_o_bucket_de_recebidos(cliente):
    enviar(cliente, cenario("1_rotina"), "DOC-H-SO-ROTINA")

    historico = cliente.get("/api/v1/documentos").json()

    assert [h["bucket"] for h in historico] == [ROTINA]


def test_historico_mostra_o_documento_no_bucket_novo_depois_da_decisao(cliente):
    enviar_pendente(cliente, "DOC-H-MUDA")
    assert [h["bucket"] for h in cliente.get("/api/v1/documentos").json()] == [AUDITORIA]

    decidir(cliente, "DOC-H-MUDA", destino="urgente")

    historico = cliente.get("/api/v1/documentos").json()
    assert [(h["documento_id"], h["bucket"], h["status"]) for h in historico] == [("DOC-H-MUDA", URGENTES, "processado")]


def test_historico_devolve_no_maximo_200_os_mais_recentes(cliente, repositorio, tmp_path):
    modelo = enviar(cliente, cenario("1_rotina"), "DOC-MODELO")
    for numero in range(204):
        documento_id = f"DOC-LOTE-{numero:03d}"
        repositorio.salvar_json(ROTINA, f"{documento_id}.json", dict(modelo, documento_id=documento_id))
        os.utime(tmp_path / ROTINA / f"{documento_id}.json", (10_000 + numero, 10_000 + numero))
    os.utime(tmp_path / ROTINA / "DOC-MODELO.json", (1, 1))

    historico = cliente.get("/api/v1/documentos").json()

    assert len(historico) == 200
    assert historico[0]["documento_id"] == "DOC-LOTE-203"
    assert "DOC-MODELO" not in [h["documento_id"] for h in historico]
