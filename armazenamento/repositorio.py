"""
MediFlow - Camada de Armazenamento (OCI Object Storage e disco local)
Hackathon ONE G10 - Grupo 10: MediFlow

Mapeamento: armazenamento/repositorio.py
Os dois repositórios têm a mesma interface (RepositorioDocumentos). A API não sabe
qual está em uso: o modo é escolhido por variável de ambiente em criar_repositorio().
Nenhuma gravação sobrescreve um objeto existente. O método apagar() existe para a
compensação da API (api/triagem.py): desfazer a entrada quando o processamento falha.
A auditoria humana (api/auditoria.py) lista os buckets, lê um documento e o move de um
bucket para outro (mover_json), com a mesma compensação.
"""

import json
import logging
import os
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Protocol

logger = logging.getLogger("mediflow.armazenamento")

# Teto de páginas ao listar um bucket da OCI (1000 objetos por página): um laço de paginação
# nunca roda sem fim, mesmo que a resposta do serviço seja inesperada.
MAXIMO_DE_PAGINAS_NA_LISTAGEM = 100


class ObjetoJaExiste(Exception):
    """O objeto já existe no bucket (ou na pasta local); a gravação foi recusada."""


class FalhaArmazenamento(Exception):
    """Falha ao gravar, ler, listar ou apagar. A mensagem nunca contém credenciais, OCIDs nem o erro original."""


class ObjetoNaoEncontrado(Exception):
    """O objeto não existe no bucket (ou na pasta local)."""


@dataclass(frozen=True)
class InfoObjeto:
    """Um objeto de um bucket: o nome e quando foi gravado (UTC, com fuso)."""
    nome: str
    criado_em: datetime


class RepositorioDocumentos(Protocol):
    """Interface comum aos repositórios de documentos."""

    nome: str

    def salvar_json(self, bucket: str, nome_objeto: str, conteudo: dict) -> None:
        """Grava `conteudo` como JSON UTF-8. Levanta ObjetoJaExiste se já existir."""
        ...

    def apagar(self, bucket: str, nome_objeto: str) -> None:
        """Apaga o objeto. Se ele já não existir, não faz nada."""
        ...

    def listar(self, bucket: str) -> List[InfoObjeto]:
        """Os objetos JSON do bucket, do mais antigo para o mais novo. No modo local, a pasta que
        ainda não existe é uma lista vazia."""
        ...

    def ler_json(self, bucket: str, nome_objeto: str) -> dict:
        """Lê o objeto como JSON. ObjetoNaoEncontrado se não existe; FalhaArmazenamento se o conteúdo
        não for um objeto JSON (a mensagem nunca repete o conteúdo)."""
        ...

    def mover_json(self, bucket_origem: str, bucket_destino: str, nome_objeto: str, conteudo: dict) -> None:
        """Grava `conteudo` no destino e apaga a origem. ObjetoJaExiste se o destino já tem o objeto
        (nada muda). ObjetoNaoEncontrado se a origem sumiu e FalhaArmazenamento se apagar falhar:
        nos dois casos a gravação no destino é desfeita."""
        ...


def _serializar(conteudo: dict) -> bytes:
    try:
        return json.dumps(conteudo, ensure_ascii=False, indent=2).encode("utf-8")
    except (TypeError, ValueError) as erro:
        raise FalhaArmazenamento(
            f"Conteúdo não serializável em JSON ({type(erro).__name__})."
        ) from None


def _desserializar(corpo: bytes) -> dict:
    """O JSON gravado como objeto. Nunca repete o conteúdo na mensagem de erro."""
    try:
        conteudo = json.loads(corpo.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise FalhaArmazenamento("Objeto com conteúdo que não é um JSON válido.") from None
    if not isinstance(conteudo, dict):
        raise FalhaArmazenamento("Objeto com conteúdo que não é um objeto JSON.")
    return conteudo


def _em_utc(momento: datetime) -> datetime:
    """Datas sem fuso (como vêm de alguns serviços) são tratadas como UTC."""
    if momento.tzinfo is None:
        return momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(timezone.utc)


class _RepositorioBase:
    """O que é igual nos dois repositórios: mover = gravar no destino e apagar na origem."""

    def mover_json(self, bucket_origem: str, bucket_destino: str, nome_objeto: str, conteudo: dict) -> None:
        # Criação exclusiva: se o destino já tem o objeto, ObjetoJaExiste sobe e nada mudou.
        self.salvar_json(bucket_destino, nome_objeto, conteudo)
        try:
            self._apagar_origem_existente(bucket_origem, nome_objeto)
        except Exception:
            # Sem a compensação o documento ficaria nos dois buckets. Quem chama recebe a falha original.
            self._desfazer_gravacao(bucket_destino, nome_objeto)
            raise

    def _desfazer_gravacao(self, bucket: str, nome_objeto: str) -> None:
        """Nunca levanta: só o nome, o bucket e o tipo do erro vão para o log."""
        try:
            self.apagar(bucket, nome_objeto)
        except Exception as erro:
            logger.error(
                "Falha ao desfazer a gravação: objeto=%s bucket=%s erro=%s. O documento pode ficar "
                "nos dois buckets e exigir limpeza manual.",
                nome_objeto,
                bucket,
                type(erro).__name__,
            )


def _falha_servico(erro) -> FalhaArmazenamento:
    """Só o status e o código do erro do Object Storage; nunca o texto original."""
    return FalhaArmazenamento(
        f"Falha no Object Storage (status={erro.status}, codigo={erro.code})."
    )


def _falha_comunicacao(erro: Exception) -> FalhaArmazenamento:
    """Só o nome da classe do erro de rede; nunca o texto original."""
    return FalhaArmazenamento(
        f"Falha de comunicação com o Object Storage ({type(erro).__name__})."
    )


class RepositorioLocal(_RepositorioBase):
    """Grava em `diretorio_base/<bucket>/<nome_objeto>`. Usado pelos colegas e pelo CI."""

    nome = "local"

    def __init__(self, diretorio_base: Path):
        self._base = Path(diretorio_base)

    def _caminho_do_objeto(self, bucket: str, nome_objeto: str) -> Path:
        pasta = self._base / bucket
        caminho = pasta / nome_objeto

        # Defesa em profundidade: a API já valida o documento_id, mas o repositório não
        # pode gravar nem apagar fora da pasta do próprio bucket (nem na de outro) se for
        # usado sem a API.
        if not caminho.resolve().is_relative_to(pasta.resolve()):
            raise FalhaArmazenamento("Nome de objeto inválido.")
        return caminho

    def salvar_json(self, bucket: str, nome_objeto: str, conteudo: dict) -> None:
        corpo = _serializar(conteudo)
        caminho = self._caminho_do_objeto(bucket, nome_objeto)

        try:
            caminho.parent.mkdir(parents=True, exist_ok=True)
        except OSError as erro:
            raise FalhaArmazenamento(
                f"Falha ao gravar no disco local ({type(erro).__name__})."
            ) from None

        try:
            with open(caminho, "xb") as arquivo:
                arquivo.write(corpo)
        except FileExistsError:
            raise ObjetoJaExiste(f"{bucket}/{nome_objeto}") from None
        except OSError as erro:
            # Não deixa um arquivo pela metade: ele faria o reenvio virar 409.
            with suppress(OSError):
                caminho.unlink(missing_ok=True)
            raise FalhaArmazenamento(
                f"Falha ao gravar no disco local ({type(erro).__name__})."
            ) from None

    def apagar(self, bucket: str, nome_objeto: str) -> None:
        caminho = self._caminho_do_objeto(bucket, nome_objeto)
        try:
            caminho.unlink(missing_ok=True)
        except OSError as erro:
            raise FalhaArmazenamento(
                f"Falha ao apagar no disco local ({type(erro).__name__})."
            ) from None

    def _apagar_origem_existente(self, bucket: str, nome_objeto: str) -> None:
        caminho = self._caminho_do_objeto(bucket, nome_objeto)
        try:
            caminho.unlink()
        except FileNotFoundError:
            raise ObjetoNaoEncontrado(f"{bucket}/{nome_objeto}") from None
        except OSError as erro:
            raise FalhaArmazenamento(
                f"Falha ao apagar no disco local ({type(erro).__name__})."
            ) from None

    def listar(self, bucket: str) -> List[InfoObjeto]:
        pasta = self._base / bucket
        try:
            entradas = [e for e in os.scandir(pasta) if e.is_file() and e.name.endswith(".json")]
            objetos = [
                InfoObjeto(e.name, datetime.fromtimestamp(e.stat().st_mtime, tz=timezone.utc)) for e in entradas
            ]
        except FileNotFoundError:
            return []
        except OSError as erro:
            raise FalhaArmazenamento(
                f"Falha ao listar no disco local ({type(erro).__name__})."
            ) from None
        return sorted(objetos, key=lambda o: (o.criado_em, o.nome))

    def ler_json(self, bucket: str, nome_objeto: str) -> dict:
        caminho = self._caminho_do_objeto(bucket, nome_objeto)
        try:
            corpo = caminho.read_bytes()
        except FileNotFoundError:
            raise ObjetoNaoEncontrado(f"{bucket}/{nome_objeto}") from None
        except OSError as erro:
            raise FalhaArmazenamento(
                f"Falha ao ler no disco local ({type(erro).__name__})."
            ) from None
        return _desserializar(corpo)


class RepositorioOCI(_RepositorioBase):
    """Grava no OCI Object Storage com `if-none-match: *` (criação exclusiva)."""

    nome = "oci"

    def __init__(self, cliente, namespace: str):
        self._cliente = cliente
        self._namespace = namespace

    def salvar_json(self, bucket: str, nome_objeto: str, conteudo: dict) -> None:
        # Import local: o modo `local` não pode depender do pacote nem de credenciais OCI.
        from oci.exceptions import ServiceError

        corpo = _serializar(conteudo)
        try:
            self._cliente.put_object(
                self._namespace,
                bucket,
                nome_objeto,
                corpo,
                content_type="application/json",
                if_none_match="*",
            )
        except ServiceError as erro:
            if erro.status in (409, 412):
                raise ObjetoJaExiste(f"{bucket}/{nome_objeto}") from None
            raise _falha_servico(erro) from None
        except Exception as erro:
            raise _falha_comunicacao(erro) from None

    def apagar(self, bucket: str, nome_objeto: str) -> None:
        from oci.exceptions import ServiceError

        try:
            self._cliente.delete_object(self._namespace, bucket, nome_objeto)
        except ServiceError as erro:
            if erro.status == 404:  # já não existe: o resultado desejado
                return
            raise _falha_servico(erro) from None
        except Exception as erro:
            raise _falha_comunicacao(erro) from None

    def _apagar_origem_existente(self, bucket: str, nome_objeto: str) -> None:
        from oci.exceptions import ServiceError

        try:
            self._cliente.delete_object(self._namespace, bucket, nome_objeto)
        except ServiceError as erro:
            if erro.status == 404:  # alguém já o moveu ou apagou
                raise ObjetoNaoEncontrado(f"{bucket}/{nome_objeto}") from None
            raise _falha_servico(erro) from None
        except Exception as erro:
            raise _falha_comunicacao(erro) from None

    def listar(self, bucket: str) -> List[InfoObjeto]:
        from oci.exceptions import ServiceError

        objetos: List[InfoObjeto] = []
        inicio = None
        for _ in range(MAXIMO_DE_PAGINAS_NA_LISTAGEM):
            opcoes = {"fields": "name,timeCreated", "limit": 1000}
            if inicio:
                opcoes["start"] = inicio
            try:
                resposta = self._cliente.list_objects(self._namespace, bucket, **opcoes)
            except ServiceError as erro:
                # Na OCI os buckets são criados antes: 404 aqui é configuração errada, não bucket vazio.
                raise _falha_servico(erro) from None
            except Exception as erro:
                raise _falha_comunicacao(erro) from None
            for objeto in resposta.data.objects:
                if objeto.name.endswith(".json"):
                    objetos.append(InfoObjeto(objeto.name, _em_utc(objeto.time_created)))
            inicio = resposta.data.next_start_with
            if not inicio:
                break
        return sorted(objetos, key=lambda o: (o.criado_em, o.nome))

    def ler_json(self, bucket: str, nome_objeto: str) -> dict:
        from oci.exceptions import ServiceError

        try:
            resposta = self._cliente.get_object(self._namespace, bucket, nome_objeto)
        except ServiceError as erro:
            if erro.status == 404:
                raise ObjetoNaoEncontrado(f"{bucket}/{nome_objeto}") from None
            raise _falha_servico(erro) from None
        except Exception as erro:
            raise _falha_comunicacao(erro) from None
        try:
            corpo = resposta.data.content
        except Exception as erro:
            raise _falha_comunicacao(erro) from None
        return _desserializar(corpo)


# ============================================================================
# NOMES DOS BUCKETS
# ============================================================================

@dataclass(frozen=True)
class NomesBuckets:
    recebidos: str
    urgentes: str
    rotina: str
    auditoria_humana: str
    rejeitados: str


def obter_buckets() -> NomesBuckets:
    """Monta os nomes a partir de OCI_BUCKET_PREFIXO (padrão `mediflow`), lido a cada chamada."""
    prefixo = os.getenv("OCI_BUCKET_PREFIXO") or "mediflow"
    return NomesBuckets(
        recebidos=f"{prefixo}-recebidos",
        urgentes=f"{prefixo}-urgentes",
        rotina=f"{prefixo}-rotina",
        auditoria_humana=f"{prefixo}-auditoria-humana",
        rejeitados=f"{prefixo}-rejeitados",
    )


# ============================================================================
# FÁBRICA
# ============================================================================

def _criar_repositorio_oci() -> RepositorioOCI:
    # Import local: só o modo `oci` precisa do SDK.
    import oci

    autenticacao = (os.getenv("OCI_AUTH") or "api_key").strip().lower()
    if autenticacao not in ("api_key", "instance_principal"):
        raise ValueError(
            f"OCI_AUTH inválido: {autenticacao!r}. Use 'api_key' ou 'instance_principal'."
        )

    try:
        if autenticacao == "api_key":
            config = oci.config.from_file(
                file_location=os.getenv("OCI_CONFIG_FILE") or oci.config.DEFAULT_LOCATION,
                profile_name=os.getenv("OCI_CONFIG_PROFILE") or "DEFAULT",
            )
            cliente = oci.object_storage.ObjectStorageClient(config)
        else:
            signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
            cliente = oci.object_storage.ObjectStorageClient(config={}, signer=signer)

        namespace = os.getenv("OCI_NAMESPACE") or cliente.get_namespace().data
    except Exception as erro:
        raise FalhaArmazenamento(
            f"Não foi possível inicializar o cliente do Object Storage ({type(erro).__name__})."
        ) from None

    return RepositorioOCI(cliente, namespace)


def criar_repositorio() -> RepositorioDocumentos:
    """Escolhe o repositório por MEDIFLOW_ARMAZENAMENTO: `local` (padrão) ou `oci`."""
    modo = (os.getenv("MEDIFLOW_ARMAZENAMENTO") or "local").strip().lower()

    if modo == "local":
        return RepositorioLocal(
            Path(os.getenv("MEDIFLOW_ARMAZENAMENTO_LOCAL_DIR") or "armazenamento_local")
        )
    if modo == "oci":
        return _criar_repositorio_oci()

    raise ValueError(f"MEDIFLOW_ARMAZENAMENTO inválido: {modo!r}. Use 'local' ou 'oci'.")
