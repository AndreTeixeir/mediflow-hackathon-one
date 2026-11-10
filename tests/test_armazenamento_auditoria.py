"""
MediFlow - Testes de listar, ler e mover objetos (armazenamento usado pela auditoria humana)
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: tests/test_armazenamento_auditoria.py
Rodam sem rede e sem credenciais OCI: o modo local usa uma pasta temporária e o modo OCI, um Object
Storage em memória que imita o que o repositório usa do SDK. Não há teste contra a OCI real.
Execução (a partir da raiz): python -m pytest tests/test_armazenamento_auditoria.py -v
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import oci
import pytest

from armazenamento.repositorio import (
    FalhaArmazenamento,
    InfoObjeto,
    ObjetoJaExiste,
    ObjetoNaoEncontrado,
    RepositorioLocal,
    RepositorioOCI,
)

ORIGEM = "mediflow-auditoria-humana"
DESTINO = "mediflow-rotina"
CONTEUDO_ORIGEM = {"documento_id": "DOC-1", "status": "pendente_auditoria", "texto": "Laudo de ação rápida"}
CONTEUDO_NOVO = {"documento_id": "DOC-1", "status": "processado", "texto": "Laudo de ação rápida"}
SEGREDO = "ocid1.tenancy.oc1..aaaaSEGREDO detalhe interno do servico"


def erro_oci(status, mensagem=SEGREDO):
    return oci.exceptions.ServiceError(status, "CodigoDoErro", {}, mensagem)


# ============================================================================
# Um Object Storage em memória
# ============================================================================

class ClienteOciEmMemoria:
    """put (com if_none_match), get, delete e list com páginas, como o repositório usa o SDK."""

    def __init__(self, tamanho_da_pagina=1000):
        self.objetos = {}  # (bucket, nome) -> (corpo, criado_em)
        self.chamadas = []  # (operação, bucket, nome)
        self.falhas = {}  # (operação, bucket) -> exceção a levantar
        self.tamanho_da_pagina = tamanho_da_pagina
        self.opcoes_da_listagem = []
        self._relogio = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

    def _chamar(self, operacao, bucket, nome=None):
        self.chamadas.append((operacao, bucket, nome))
        if (operacao, bucket) in self.falhas:
            raise self.falhas[(operacao, bucket)]

    def gravar_direto(self, bucket, nome, conteudo, criado_em=None):
        self._relogio += timedelta(seconds=1)
        self.objetos[(bucket, nome)] = (json.dumps(conteudo).encode("utf-8"), criado_em or self._relogio)

    def put_object(self, namespace, bucket, nome, corpo, **opcoes):
        self._chamar("put", bucket, nome)
        if opcoes.get("if_none_match") == "*" and (bucket, nome) in self.objetos:
            raise erro_oci(412)
        self._relogio += timedelta(seconds=1)
        self.objetos[(bucket, nome)] = (corpo, self._relogio)

    def get_object(self, namespace, bucket, nome, **opcoes):
        self._chamar("get", bucket, nome)
        if (bucket, nome) not in self.objetos:
            raise erro_oci(404)
        return SimpleNamespace(data=SimpleNamespace(content=self.objetos[(bucket, nome)][0]))

    def delete_object(self, namespace, bucket, nome, **opcoes):
        self._chamar("delete", bucket, nome)
        if (bucket, nome) not in self.objetos:
            raise erro_oci(404)
        del self.objetos[(bucket, nome)]

    def list_objects(self, namespace, bucket, **opcoes):
        self.opcoes_da_listagem.append(opcoes)
        self._chamar("list", bucket)
        nomes = sorted(n for (b, n) in self.objetos if b == bucket)  # a OCI lista por nome
        if opcoes.get("start"):
            nomes = [n for n in nomes if n >= opcoes["start"]]
        pagina, resto = nomes[: self.tamanho_da_pagina], nomes[self.tamanho_da_pagina :]
        objetos = [SimpleNamespace(name=n, time_created=self.objetos[(bucket, n)][1]) for n in pagina]
        return SimpleNamespace(data=SimpleNamespace(objects=objetos, next_start_with=resto[0] if resto else None))

    def nomes(self, bucket):
        return sorted(n for (b, n) in self.objetos if b == bucket)


@pytest.fixture
def cliente_oci():
    return ClienteOciEmMemoria()


@pytest.fixture
def oci_repo(cliente_oci):
    return RepositorioOCI(cliente_oci, "namespace-teste")


@pytest.fixture
def local(tmp_path):
    return RepositorioLocal(tmp_path)


# ============================================================================
# Modo local: listar
# ============================================================================

def test_local_listar_bucket_que_nao_existe_devolve_lista_vazia(local):
    assert local.listar(ORIGEM) == []


def test_local_listar_ordena_do_mais_antigo_para_o_mais_novo_e_so_lista_json(local, tmp_path):
    for nome, instante in [("DOC-B.json", 3000), ("DOC-A.json", 1000), ("DOC-C.json", 2000)]:
        local.salvar_json(ORIGEM, nome, {"n": nome})
        os.utime(tmp_path / ORIGEM / nome, (instante, instante))
    (tmp_path / ORIGEM / "lixo.txt").write_text("não é um documento")
    (tmp_path / ORIGEM / "subpasta").mkdir()

    objetos = local.listar(ORIGEM)

    assert [o.nome for o in objetos] == ["DOC-A.json", "DOC-C.json", "DOC-B.json"]
    assert all(isinstance(o, InfoObjeto) and o.criado_em.tzinfo is not None for o in objetos)
    assert objetos[0].criado_em == datetime.fromtimestamp(1000, tz=timezone.utc)


def test_local_listar_nao_mistura_buckets(local):
    local.salvar_json(ORIGEM, "DOC-1.json", {})
    local.salvar_json(DESTINO, "DOC-2.json", {})

    assert [o.nome for o in local.listar(ORIGEM)] == ["DOC-1.json"]
    assert [o.nome for o in local.listar(DESTINO)] == ["DOC-2.json"]


# ============================================================================
# Modo local: ler_json
# ============================================================================

def test_local_ler_json_devolve_o_que_foi_gravado(local):
    local.salvar_json(ORIGEM, "DOC-1.json", CONTEUDO_ORIGEM)

    assert local.ler_json(ORIGEM, "DOC-1.json") == CONTEUDO_ORIGEM


def test_local_ler_json_objeto_inexistente_levanta_objeto_nao_encontrado(local):
    with pytest.raises(ObjetoNaoEncontrado):
        local.ler_json(ORIGEM, "DOC-NAO-EXISTE.json")


@pytest.mark.parametrize("corpo", [b"nao e json SEGREDO-NO-ARQUIVO", b"[1, 2, 3]", b'"SEGREDO-NO-ARQUIVO"', b"\xff\xfe\x00"])
def test_local_ler_json_que_nao_e_um_objeto_json_vira_falha_sem_vazar_o_conteudo(local, tmp_path, corpo):
    (tmp_path / ORIGEM).mkdir()
    (tmp_path / ORIGEM / "DOC-1.json").write_bytes(corpo)

    with pytest.raises(FalhaArmazenamento) as capturada:
        local.ler_json(ORIGEM, "DOC-1.json")

    assert "SEGREDO" not in str(capturada.value)


def test_local_ler_json_recusa_nome_que_sai_da_pasta_do_bucket(local):
    with pytest.raises(FalhaArmazenamento, match="Nome de objeto inválido"):
        local.ler_json(ORIGEM, "../mediflow-rotina/DOC-1.json")


# ============================================================================
# Modo local: mover_json
# ============================================================================

def test_local_mover_grava_o_conteudo_novo_no_destino_e_apaga_a_origem(local, tmp_path):
    local.salvar_json(ORIGEM, "DOC-1.json", CONTEUDO_ORIGEM)

    local.mover_json(ORIGEM, DESTINO, "DOC-1.json", CONTEUDO_NOVO)

    assert local.ler_json(DESTINO, "DOC-1.json") == CONTEUDO_NOVO
    assert not (tmp_path / ORIGEM / "DOC-1.json").exists()


def test_local_mover_para_destino_que_ja_tem_o_objeto_levanta_objeto_ja_existe_e_nao_mexe_em_nada(local):
    local.salvar_json(ORIGEM, "DOC-1.json", CONTEUDO_ORIGEM)
    local.salvar_json(DESTINO, "DOC-1.json", {"previo": True})

    with pytest.raises(ObjetoJaExiste):
        local.mover_json(ORIGEM, DESTINO, "DOC-1.json", CONTEUDO_NOVO)

    assert local.ler_json(ORIGEM, "DOC-1.json") == CONTEUDO_ORIGEM
    assert local.ler_json(DESTINO, "DOC-1.json") == {"previo": True}


def test_local_mover_objeto_que_sumiu_da_origem_levanta_nao_encontrado_e_desfaz_o_destino(local, tmp_path):
    with pytest.raises(ObjetoNaoEncontrado):
        local.mover_json(ORIGEM, DESTINO, "DOC-1.json", CONTEUDO_NOVO)

    assert not (tmp_path / DESTINO / "DOC-1.json").exists()


class LocalQueNaoApagaAOrigem(RepositorioLocal):
    def _apagar_origem_existente(self, bucket, nome_objeto):
        raise FalhaArmazenamento("detalhe interno do disco")


def test_local_mover_com_falha_ao_apagar_a_origem_desfaz_a_gravacao_no_destino(tmp_path):
    repo = LocalQueNaoApagaAOrigem(tmp_path)
    repo.salvar_json(ORIGEM, "DOC-1.json", CONTEUDO_ORIGEM)

    with pytest.raises(FalhaArmazenamento, match="detalhe interno do disco"):
        repo.mover_json(ORIGEM, DESTINO, "DOC-1.json", CONTEUDO_NOVO)

    assert not (tmp_path / DESTINO / "DOC-1.json").exists()
    assert repo.ler_json(ORIGEM, "DOC-1.json") == CONTEUDO_ORIGEM  # a origem continua intacta


class LocalQueNaoApagaNada(LocalQueNaoApagaAOrigem):
    def apagar(self, bucket, nome_objeto):
        raise FalhaArmazenamento("detalhe interno da compensação")


def test_local_mover_com_falha_tambem_ao_desfazer_registra_log_seguro_e_relanca_a_original(tmp_path, caplog):
    repo = LocalQueNaoApagaNada(tmp_path)
    repo.salvar_json(ORIGEM, "DOC-1.json", CONTEUDO_ORIGEM)

    with caplog.at_level(logging.ERROR, logger="mediflow.armazenamento"):
        with pytest.raises(FalhaArmazenamento, match="detalhe interno do disco"):
            repo.mover_json(ORIGEM, DESTINO, "DOC-1.json", CONTEUDO_NOVO)

    assert len(caplog.records) == 1
    mensagem = caplog.records[0].getMessage()
    assert "DOC-1.json" in mensagem and DESTINO in mensagem and "FalhaArmazenamento" in mensagem
    assert "detalhe interno" not in mensagem
    assert caplog.records[0].exc_info is None


# ============================================================================
# Modo OCI: listar
# ============================================================================

def test_oci_listar_percorre_todas_as_paginas_e_ordena_pela_data_de_criacao(cliente_oci):
    cliente_oci.tamanho_da_pagina = 2
    # Nomes em ordem alfabética (como a OCI lista) e datas fora de ordem.
    base = datetime(2026, 11, 1, tzinfo=timezone.utc)
    for nome, dias in [("DOC-A.json", 4), ("DOC-B.json", 1), ("DOC-C.json", 5), ("DOC-D.json", 2), ("DOC-E.json", 3)]:
        cliente_oci.gravar_direto(ORIGEM, nome, {}, criado_em=base + timedelta(days=dias))
    repo = RepositorioOCI(cliente_oci, "namespace-teste")

    objetos = repo.listar(ORIGEM)

    assert [o.nome for o in objetos] == ["DOC-B.json", "DOC-D.json", "DOC-E.json", "DOC-A.json", "DOC-C.json"]
    assert len(cliente_oci.opcoes_da_listagem) == 3  # 5 objetos em páginas de 2
    primeira, segunda, terceira = cliente_oci.opcoes_da_listagem
    assert primeira == {"fields": "name,timeCreated", "limit": 1000}
    assert segunda["start"] == "DOC-C.json" and terceira["start"] == "DOC-E.json"


def test_oci_listar_bucket_sem_objetos_devolve_lista_vazia(oci_repo):
    assert oci_repo.listar(ORIGEM) == []


def test_oci_listar_ignora_o_que_nao_e_json_e_nao_mistura_buckets(oci_repo, cliente_oci):
    cliente_oci.gravar_direto(ORIGEM, "DOC-1.json", {})
    cliente_oci.gravar_direto(ORIGEM, "leia-me.txt", {})
    cliente_oci.gravar_direto(DESTINO, "DOC-2.json", {})

    assert [o.nome for o in oci_repo.listar(ORIGEM)] == ["DOC-1.json"]


def test_oci_listar_trata_data_sem_fuso_como_utc():
    sem_fuso = datetime(2026, 11, 10, 9, 30)
    cliente = SimpleNamespace(
        list_objects=lambda *a, **k: SimpleNamespace(
            data=SimpleNamespace(objects=[SimpleNamespace(name="DOC-1.json", time_created=sem_fuso)], next_start_with=None)
        )
    )

    [objeto] = RepositorioOCI(cliente, "namespace-teste").listar(ORIGEM)

    assert objeto.criado_em == datetime(2026, 11, 10, 9, 30, tzinfo=timezone.utc)


def test_oci_listar_converte_para_utc_uma_data_com_outro_fuso():
    brasilia = timezone(timedelta(hours=-3))
    cliente = SimpleNamespace(
        list_objects=lambda *a, **k: SimpleNamespace(
            data=SimpleNamespace(
                objects=[SimpleNamespace(name="DOC-1.json", time_created=datetime(2026, 11, 10, 9, 30, tzinfo=brasilia))],
                next_start_with=None,
            )
        )
    )

    [objeto] = RepositorioOCI(cliente, "namespace-teste").listar(ORIGEM)

    assert objeto.criado_em == datetime(2026, 11, 10, 12, 30, tzinfo=timezone.utc)
    assert objeto.criado_em.utcoffset() == timedelta(0)


@pytest.mark.parametrize("erro", [erro_oci(404), erro_oci(403), erro_oci(500), oci.exceptions.RequestException(SEGREDO)])
def test_oci_listar_com_erro_vira_falha_sem_vazar_o_texto_original(cliente_oci, oci_repo, erro):
    # 404 também: na OCI os buckets existem de antemão, então bucket inexistente é configuração errada.
    cliente_oci.falhas[("list", ORIGEM)] = erro

    with pytest.raises(FalhaArmazenamento) as capturada:
        oci_repo.listar(ORIGEM)

    assert "SEGREDO" not in str(capturada.value) and "ocid1" not in str(capturada.value)


def test_oci_listar_nao_entra_em_laco_infinito_se_o_servico_nunca_para_de_paginar():
    chamadas = []

    def sempre_mais_uma_pagina(*args, **kwargs):
        chamadas.append(kwargs)
        return SimpleNamespace(data=SimpleNamespace(objects=[], next_start_with=f"proxima-{len(chamadas)}"))

    RepositorioOCI(SimpleNamespace(list_objects=sempre_mais_uma_pagina), "namespace-teste").listar(ORIGEM)

    assert len(chamadas) == 100


# ============================================================================
# Modo OCI: ler_json
# ============================================================================

def test_oci_ler_json_devolve_o_que_foi_gravado(oci_repo):
    oci_repo.salvar_json(ORIGEM, "DOC-1.json", CONTEUDO_ORIGEM)

    assert oci_repo.ler_json(ORIGEM, "DOC-1.json") == CONTEUDO_ORIGEM


def test_oci_ler_json_404_levanta_objeto_nao_encontrado(oci_repo):
    with pytest.raises(ObjetoNaoEncontrado):
        oci_repo.ler_json(ORIGEM, "DOC-1.json")


@pytest.mark.parametrize("erro", [erro_oci(500), erro_oci(403), oci.exceptions.RequestException(SEGREDO)])
def test_oci_ler_json_com_erro_vira_falha_sem_vazar_o_texto_original(cliente_oci, oci_repo, erro):
    cliente_oci.falhas[("get", ORIGEM)] = erro

    with pytest.raises(FalhaArmazenamento) as capturada:
        oci_repo.ler_json(ORIGEM, "DOC-1.json")

    assert "SEGREDO" not in str(capturada.value) and "ocid1" not in str(capturada.value)


@pytest.mark.parametrize("corpo", [b"nao e json SEGREDO-NO-OBJETO", b"[1, 2]", b"\xff\xfe"])
def test_oci_ler_json_que_nao_e_um_objeto_json_vira_falha_sem_vazar_o_conteudo(cliente_oci, oci_repo, corpo):
    cliente_oci.objetos[(ORIGEM, "DOC-1.json")] = (corpo, datetime(2026, 11, 10, tzinfo=timezone.utc))

    with pytest.raises(FalhaArmazenamento) as capturada:
        oci_repo.ler_json(ORIGEM, "DOC-1.json")

    assert "SEGREDO" not in str(capturada.value)


# ============================================================================
# Modo OCI: mover_json
# ============================================================================

def operacoes(cliente):
    return [(op, bucket) for op, bucket, _ in cliente.chamadas]


def test_oci_mover_grava_no_destino_com_criacao_exclusiva_e_depois_apaga_a_origem(cliente_oci, oci_repo):
    cliente_oci.gravar_direto(ORIGEM, "DOC-1.json", CONTEUDO_ORIGEM)

    oci_repo.mover_json(ORIGEM, DESTINO, "DOC-1.json", CONTEUDO_NOVO)

    assert operacoes(cliente_oci) == [("put", DESTINO), ("delete", ORIGEM)]  # nesta ordem
    assert cliente_oci.nomes(ORIGEM) == [] and cliente_oci.nomes(DESTINO) == ["DOC-1.json"]
    assert oci_repo.ler_json(DESTINO, "DOC-1.json") == CONTEUDO_NOVO


def test_oci_mover_para_destino_que_ja_tem_o_objeto_levanta_objeto_ja_existe_e_nao_apaga_a_origem(cliente_oci, oci_repo):
    cliente_oci.gravar_direto(ORIGEM, "DOC-1.json", CONTEUDO_ORIGEM)
    cliente_oci.gravar_direto(DESTINO, "DOC-1.json", {"previo": True})

    with pytest.raises(ObjetoJaExiste):
        oci_repo.mover_json(ORIGEM, DESTINO, "DOC-1.json", CONTEUDO_NOVO)

    assert operacoes(cliente_oci) == [("put", DESTINO)]  # nem tentou apagar
    assert oci_repo.ler_json(ORIGEM, "DOC-1.json") == CONTEUDO_ORIGEM
    assert oci_repo.ler_json(DESTINO, "DOC-1.json") == {"previo": True}


def test_oci_mover_objeto_que_sumiu_da_origem_levanta_nao_encontrado_e_desfaz_o_destino(cliente_oci, oci_repo):
    with pytest.raises(ObjetoNaoEncontrado):
        oci_repo.mover_json(ORIGEM, DESTINO, "DOC-1.json", CONTEUDO_NOVO)

    assert operacoes(cliente_oci) == [("put", DESTINO), ("delete", ORIGEM), ("delete", DESTINO)]
    assert cliente_oci.nomes(DESTINO) == []


@pytest.mark.parametrize("erro", [erro_oci(500), erro_oci(403), oci.exceptions.RequestException(SEGREDO)])
def test_oci_mover_com_falha_ao_apagar_a_origem_desfaz_o_destino_e_nao_vaza_o_texto_original(cliente_oci, oci_repo, erro):
    cliente_oci.gravar_direto(ORIGEM, "DOC-1.json", CONTEUDO_ORIGEM)
    cliente_oci.falhas[("delete", ORIGEM)] = erro

    with pytest.raises(FalhaArmazenamento) as capturada:
        oci_repo.mover_json(ORIGEM, DESTINO, "DOC-1.json", CONTEUDO_NOVO)

    assert "SEGREDO" not in str(capturada.value) and "ocid1" not in str(capturada.value)
    assert cliente_oci.nomes(DESTINO) == []  # compensação feita
    assert cliente_oci.nomes(ORIGEM) == ["DOC-1.json"]  # origem intacta


def test_oci_mover_com_falha_tambem_ao_desfazer_registra_log_seguro(cliente_oci, oci_repo, caplog):
    cliente_oci.gravar_direto(ORIGEM, "DOC-1.json", CONTEUDO_ORIGEM)
    cliente_oci.falhas[("delete", ORIGEM)] = erro_oci(500)
    cliente_oci.falhas[("delete", DESTINO)] = erro_oci(500)

    with caplog.at_level(logging.ERROR, logger="mediflow.armazenamento"):
        with pytest.raises(FalhaArmazenamento):
            oci_repo.mover_json(ORIGEM, DESTINO, "DOC-1.json", CONTEUDO_NOVO)

    assert len(caplog.records) == 1
    mensagem = caplog.records[0].getMessage()
    assert "DOC-1.json" in mensagem and DESTINO in mensagem and "FalhaArmazenamento" in mensagem
    assert "SEGREDO" not in mensagem and "ocid1" not in mensagem


def test_oci_mover_falha_ao_gravar_no_destino_nao_apaga_a_origem(cliente_oci, oci_repo):
    cliente_oci.gravar_direto(ORIGEM, "DOC-1.json", CONTEUDO_ORIGEM)
    cliente_oci.falhas[("put", DESTINO)] = erro_oci(500)

    with pytest.raises(FalhaArmazenamento):
        oci_repo.mover_json(ORIGEM, DESTINO, "DOC-1.json", CONTEUDO_NOVO)

    assert operacoes(cliente_oci) == [("put", DESTINO)]
    assert cliente_oci.nomes(ORIGEM) == ["DOC-1.json"]
