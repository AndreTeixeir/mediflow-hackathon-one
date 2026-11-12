# Histórico do projeto

Linha do tempo do MediFlow, do primeiro commit ao deploy na OCI. As datas dos commits vêm do `git log` (data do autor). As datas dos pull requests vêm do `git log` quando há um commit de merge e, quando não há, do GitHub (em UTC), e isso vem indicado.

- Repositório original da equipe: [`No-Country-simulation/g10-Brasil27`](https://github.com/No-Country-simulation/g10-Brasil27).
- Para ver um commit: `git show <hash>`.

> **Autoria:** quem escreveu um commit é o **autor do commit** no Git, mais a coautoria pelo `Co-authored-by`. A tag `[Nome]` no fim de algumas mensagens indica a **área** do projeto, não quem escreveu. Veja [Como ler a autoria](decisoes-tecnicas.md#como-ler-a-autoria-no-git).

## Pull requests

| PR | Título (resumo) | Autor | Situação | Link |
|---|---|---|---|---|
| #1 | Contratos Pydantic, regras clínicas e cenários de teste | Lethicia | Mesclado: `1f742e5`, em 26/10/2026 | [PR #1](https://github.com/No-Country-simulation/g10-Brasil27/pull/1) |
| #2 | Agente (grafo LangGraph, leitura multimodal, prompts) e deploy | Ingrid | Mesclado: `d67e823`, em 29/10/2026 | [PR #2](https://github.com/No-Country-simulation/g10-Brasil27/pull/2) |
| #3 | API de triagem com OCI Object Storage e modo local | André | Mesclado: `89c13a4`, em 28/10/2026 | [PR #3](https://github.com/No-Country-simulation/g10-Brasil27/pull/3) |
| #4 | Painel de auditoria human-in-the-loop e tela de triagem | Lethicia | Mesclado em 07/11/2026 (GitHub, UTC) | [PR #4](https://github.com/No-Country-simulation/g10-Brasil27/pull/4) |
| #5 | Integração da API com o grafo e da interface Streamlit, ajuste do deploy | Ingrid | Mesclado: `347a51c`, em 07/11/2026 | [PR #5](https://github.com/No-Country-simulation/g10-Brasil27/pull/5) |
| #6 | Compatibilidade do schema estruturado com o SDK `google-genai` e logging | Ingrid | Mesclado: `f3b2da3`, em 10/11/2026 | [PR #6](https://github.com/No-Country-simulation/g10-Brasil27/pull/6) |
| #7 | Fase 2: app completo e pronto para o deploy (envio, auditoria humana pela API, chave de acesso) | André | Mesclado: `1232ec5`, em 10/11/2026 | [PR #7](https://github.com/No-Country-simulation/g10-Brasil27/pull/7) |
| #8 | Ajustes de documentação e slides e roteiro do DemoDay | Ingrid | **Aberto**, com mudanças solicitadas na revisão (12/11/2026, UTC). **Não entra** na `main` | [PR #8](https://github.com/No-Country-simulation/g10-Brasil27/pull/8) |

## Linha do tempo

### 23/10/2026 — Estrutura inicial e a parte da Lethicia

- **André** abre o repositório com uma estrutura base (`1b1c8c7`) e a reorganiza nas pastas do projeto (`64ff7eb`): `agente/`, `api/`, `armazenamento/`, `contratos/`, `dados_teste/`, `deploy/` e `interface/`, com o `.env.example` e o `.gitignore`.
- **Lethicia** entrega os contratos de dados em Pydantic (`contratos/modelos.py`), as regras clínicas (`regras/regras_clinicas.py`), os três cenários de teste (`dados_teste/`) e os primeiros testes, tudo no `92f37ec`, e um ajuste em `98e4a31`. O trabalho entra pelo PR #1.

### 26 a 27/10/2026 — A parte da Ingrid

- Em 26/10, **Ingrid** entrega:
  - os prompts de classificação e extração (`b125a02`);
  - a leitura de documentos e a visão multimodal (`8196fc5`);
  - o grafo de decisão condicional com LangGraph (`b70edb6`);
  - os testes do grafo (`4f10af3`);
  - o `Dockerfile` e o `docker-compose` com o hardening dos containers (`4047ef4`);
  - os scripts de rede, segurança e provisionamento da OCI, com o nginx (`fed7b8a`);
  - um relatório técnico das entregas (`a5797f5`, em `docs/relatorio_ingrid.md`).
- Em 27/10, os prompts passam a ser compatíveis com a interface (`997aa2d`). O trabalho entra pelo PR #2, mesclado em 29/10.

### 27/10/2026 — A API e a tela do Guilherme

- **André** entrega a API de triagem com o armazenamento em OCI Object Storage e em disco (`edf65c8`), que traz também o workflow de testes do GitHub Actions. O PR #3 é mesclado em 28/10.
- **Guilherme** entrega a tela inicial de envio: `app.py`, `api_client.py`, `schemas.py` e um `EXEMPLOS_DEMO.md` (`6be4317`). Esse commit está na branch `feature/documentos-guilherme` e na `integracao/fase-1`, e **não** está no histórico da `main`.

### 27/10/2026 — Fase 1: a integração das partes

Na branch `integracao/fase-1`, o **André** junta as três partes em quatro commits: os merges da API (`86a4a35`), do agente e do deploy da Ingrid (`ed78223`) e da tela do Guilherme (`6782653`), mais um guia de execução com a lista de erros da integração (`282b81f`). Essa branch **não foi mesclada na `main`**: a `main` recebeu o trabalho pelos PRs #2 e #3.

### 29/10/2026 — O painel de auditoria

**Lethicia** entrega o painel human-in-the-loop e a tela de triagem em Streamlit (`7a3c54b`, `interface/app.py`). O PR #4 é mesclado depois, em 07/11.

### 05 a 07/11/2026 — Integração com o grafo

- Em 05/11, **Ingrid** liga a API de triagem ao grafo LangGraph (`db47d5e`), liga o painel ao grafo e à variável `MEDIFLOW_API_URL`, com envio de arquivos (`f9f082d`), e dá ao painel um healthcheck próprio no compose (`5d1694d`). O PR #5 é mesclado em 07/11.
- Em 06/11, **Guilherme** entrega a página de envio de arquivos (`ef31d47`, `1_Envio_de_Arquivos.py`).

### 09 a 10/11/2026 — O schema do Gemini

**Ingrid** corrige o schema de saída estruturada do Gemini e melhora o log (`9e385f4`) e trata respostas JSON delimitadas por markdown (`78176dc`). O PR #6 é mesclado em 10/11.

### 10/11/2026 — Fase 2: o app completo (PR #7)

Na branch `integracao/fase-2`, o **André** integra e fecha o app. O PR #7 é mesclado no mesmo dia (`1232ec5`). Em ordem de tema:

- **Integração da tela do Guilherme:** o merge da página de envio (`5c731a2`) e a aba de envio do painel com a lógica dele, com ele como coautor (`2a1ce95`).
- **API e agente:** o agente simulado sai e a API sempre chama o grafo (`01c4b8d`); a API valida tipo e conteúdo do arquivo antes de gravar (`92eb2ec`).
- **Agente, regras e testes** (áreas da Ingrid e da Lethicia): testes isolados do Gemini (`b203a07`), repetição da chamada em falha transitória (`6d6ea6d`), **modelo padrão `gemini-3.5-flash-lite`** (`544d1c6`), regras de urgência por palavra inteira e de CRM válido (`6e4946e`, `c861c9f`) e gabaritos atualizados (`f9246af`).
- **Armazenamento e auditoria:** listar, ler e mover objetos nos modos local e OCI (`6925388`), o destino `Documento_Rejeitado` (`2cc2b22`), os endpoints de auditoria humana e histórico (`f3ade5f`) e o painel de auditoria lendo e decidindo pela API (`97fcb09`).
- **Proteções da API:** chave `X-API-Key` (`8e6e845`), corpo de no máximo 15 MB (`d7b0e42`), tetos diários de documentos e MB (`fe5c765`), o painel que envia a chave e trata 401, 413 e 429 (`5d3c394`), a documentação delas (`2120ca3`) e o cache das listagens, com limite de 50 itens (`743db9c`).
- **Dependências:** o `tzdata` fixado no `requirements.txt` (`51b74fb`).

### 11/11/2026 — Deploy na OCI

O **André** sobe o MediFlow numa VM da OCI. As telas mostram:

- a instância `mediflow` (`VM.Standard.A1.Flex`, 1 OCPU, 5 GB) lançada em 11/11/2026, às 12:11 UTC ([`oci-01`](imagens/oci-01-instancia.png));
- os cinco buckets privados `mediflow-*`, criados em 27/10/2026 ([`oci-02`](imagens/oci-02-buckets.png));
- o primeiro objeto de teste do deploy no bucket `mediflow-rotina`, `DOC-DEPLOY-TESTE-001`, em 11/11/2026, às 19:24 UTC ([`oci-03`](imagens/oci-03-objetos-rotina.png)).

O passo a passo está em [`deploy-oci.md`](deploy-oci.md).

### 12/11/2026 — Custo e PR #8

O Cost Analysis registra custo de R$ 0,00 de 14/10 a 12/11/2026 ([`oci-04`](imagens/oci-04-custo-zero.png)).

Nesse dia, o PR #8 (slides e roteiro do DemoDay) é aberto e recebe um pedido de mudanças na revisão.
