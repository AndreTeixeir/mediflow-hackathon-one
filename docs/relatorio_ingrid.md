# 📋 Relatório Técnico de Entregas — Ingrid Soares (MediFlow)

> **Projeto:** Projeto 2 – 🏥 MediFlow – Agente Autônomo para Triagem, Extração e Roteamento de Documentos Clínicos  
> **Hackathon:** ONE G10 (Oracle Next Education & Alura)  
> **Repositório:** `No-Country-simulation/g10-Brasil27`  
> **Branch de Trabalho:** `feature/agente-deploy-ingrid` (PR #2 – Aprovado e pronto para merge na `main`)  
> **Responsável:** Ingrid Soares — *Grafo de IA (LangGraph), Visão Multimodal / OCR, Segurança, Redes & Deploy OCI*  
> **Última Atualização:** 28/10/2026

---

## 🎯 1. Escopo de Responsabilidades e Arquitetura Entregue

De acordo com a divisão oficial de tarefas da equipe (`divisao_tarefas_mediflow.pdf`) e o diagnóstico de integração da Fase 1 (`integracao/fase-1`), foram implementadas e alinhadas todas as entregas sob responsabilidade da **Ingrid**:

### 1.1. Grafo de Decisão Condicional (`agente/grafo.py` & `agente/__init__.py`)
Implementação do motor de orquestração autônomo com **LangGraph (`StateGraph`)** utilizando o estado tipado `EstadoAgenteMediFlow` e conectando os contratos Pydantic (`contratos/modelos.py`) às regras clínicas (`regras/regras_clinicas.py`):
- **`no_ingerir_documento`**: Recebe o `DocumentoEntrada` e aciona o pipeline de leitura e normalização documental (`PDF`, `PNG/JPG`, `Base64`, `TXT/JSON`).
- **`no_classificar_e_extrair`**: Executa a análise via **Google Gemini (`gemini-3.5-flash-lite`)** com *Structured Outputs* (`ResultadoExtracaoLLM`) ou fallback clínico determinístico, produzindo `ClassificacaoDocumento` e `DadosExtraidos`.
- **`no_avaliar_consistencia_e_urgencia`**: Calcula o Score de Confiança consolidado (`calcular_score_confianca`), identifica pendências clínicas e detecta quadros críticos de emergência (`detectar_urgencia_clinica`).
- **Aresta Condicional (`decidir_caminho_triagem`)**:
  - **`no_rotear_emergencia`**: Prioridade máxima para achados críticos (ex.: *TEP Agudo*), gerando alerta imediato no canal `Alerta_Plantao_Medico` e roteando para `Fila_Emergencia_Medica`.
  - **`no_rotear_revisao_humana` (*Human-in-the-Loop*)**: Retém documentos com score $< 0.75$, rasuras, carimbo borrado ou ausência de dados essenciais na `Fila_Revisao_Humana`, marcando `status = "pendente_auditoria"`.
  - **`no_rotear_fluxo_padrao`**: Encaminha documentos consistentes de rotina para `Farmacia_Hospitalar`, `Auditoria_Autorizacoes` ou `Prontuario_Eletronico_do_Paciente`.
- **`no_consolidar_resposta`**: Valida e retorna `TriagemResposta` com `armazenamento_oci=None` (*o grafo decide, a API executa e grava no bucket correspondente*). Exporta tanto `executar_triagem_mediflow(entrada)` quanto `analisar_documento(entrada) -> (ClassificacaoDocumento, DadosExtraidos)` para integração direta com a API FastAPI do André.

### 1.2. Visão Multimodal / OCR e Leitura de Documentos (`agente/leitor_documentos.py` & `agente/visao_multimodal.py`)
- **Leitura Híbrida de PDFs (`pypdf`)**: Extrai texto nativo de PDFs digitais e detecta automaticamente PDFs escaneados/digitalizados (quando não há camada textual suficiente), encaminhando os bytes `application/pdf` diretamente para a visão multimodal do Gemini.
- **Processamento de Imagens Clínicas (`Pillow`)**: Valida, converte e normaliza fotos de receitas médicas e exames (`PNG`, `JPG`, `WEBP`) recebidas em Base64 (com ou sem cabeçalho *Data URI*).
- **Integração Multimodal `google-genai` + Fallback Clínico Determinístico**: Garante funcionamento 100% resiliente com ou sem chave `LLM_API_KEY`.

### 1.3. Prompts Unificados e Compatíveis com a Interface (`prompts/`)
- **`prompts/classificacao.py` (`PROMPT_CLASSIFICACAO`)** e **`prompts/extracao.py` (`PROMPT_EXTRACAO`)**: Módulos compatíveis com os imports da tela Streamlit (`app.py`), unificados em **`prompts/extracao_clinica.py`** (`SYSTEM_PROMPT_MEDIFLOW`, `PROMPT_OCR_MULTIMODAL`, `ResultadoExtracaoLLM`).

### 1.4. Infraestrutura OCI, Redes, Segurança & Deploy (`deploy/` & `.dockerignore`)
- **`.dockerignore`**: Bloqueia vazamento de `.env`, chaves privadas `*.pem` / `*.key`, `.oci/` e `.venv/` para dentro da imagem.
- **`deploy/Dockerfile`**: Build *multi-stage* sobre `python:3.13-slim`, execução com usuário não-root dedicado (`mediflow`, UID `10001`) e `HEALTHCHECK` apontando para a rota oficial da API (`http://localhost:8000/api/v1/saude`).
- **`deploy/docker-compose.yml`**: Orquestração dos serviços `mediflow-api` (`127.0.0.1:8000:8000`) e `mediflow-ui` (`127.0.0.1:8501:8501`) restritos ao loopback para não expor portas diretamente à internet, com `cap_drop: [ALL]`, `no-new-privileges:true` e montagem somente leitura (`:ro`) de `~/.oci`.
- **`deploy/nginx_mediflow.conf`** e **`deploy/setup_oci_vm.sh`**: Reverse Proxy Nginx na porta `80`/`443` com *rate-limiting* (`10r/s`) e firewall UFW liberando apenas `22/tcp`, `80/tcp` e `443/tcp` para proteger a conta OCI.

### 1.5. Code Reviews e Integração Realizados pela Ingrid
- **PR #1 (`feature/contratos-lethicia` — Leticia):** Revisado, testado com o Grafo e aprovado/mergeado na `main` (`1f742e5`).
- **PR #3 (`feat/api-triagem` — André):** Revisado, validado com os 90 testes automatizados e aprovado/mergeado na `main` (`89c13a4`).

---

## 🧱 2. Histórico de Commits Atômicos Realizados (`feature/agente-deploy-ingrid`)

| Ordem | Hash | Mensagem do Commit | Escopo |
| :---: | :--- | :--- | :--- |
| **1** | `a7c0255` | `chore(deps): adiciona langgraph, pypdf e pillow ao requirements.txt` | Dependências do agente |
| **2** | `b125a02` | `feat(prompts): adiciona prompts estruturados de classificacao e extracao clinica` | Prompts e schema LLM |
| **3** | `8196fc5` | `feat(agente): adiciona pipeline de leitura de documentos e visao multimodal` | OCR / Visão Multimodal |
| **4** | `b70edb6` | `feat(agente): implementa grafo de decisao condicional com LangGraph` | Grafo LangGraph |
| **5** | `4f10af3` | `test(agente): adiciona testes automatizados do grafo e ingestao multimodal` | Testes unitários e ponta a ponta |
| **6** | `4047ef4` | `feat(deploy): adiciona Dockerfile e docker-compose com hardening de containers` | Containers Docker |
| **7** | `fed7b8a` | `feat(deploy): adiciona scripts de rede, seguranca e provisionamento OCI Always Free` | Rede, Firewall e OCI |
| **8** | `a5797f5` | `docs(relatorio): adiciona relatorio tecnico das entregas da Ingrid` | Documentação técnica |
| **9** | `997aa2d` | `feat(prompts): adiciona modulos classificacao e extracao compativeis com a interface` | Resolução do item **E1** da integração |
| **10** | `6fc5839` | `fix(agente): retorna armazenamento_oci=None no grafo e expoe analisar_documento` | Resolução dos itens **E6** e **E7** da integração |
| **11** | `8ba8cc3` | `fix(deploy): ajusta healthcheck para /api/v1/saude e restringe portas 8000 e 8501 ao localhost` | Resolução dos itens **E5** e **E8** da integração |
| **12** | `5043494` | `docs(relatorio): atualiza relatorio com correcoes da integracao fase 1` | Atualização do relatório |

---

## 🔄 3. Checklist de Progresso e Próximos Passos

- [x] Sincronização do repositório `g10-Brasil27`.
- [x] Code Review e Merge do **PR #1** da Leticia (`contratos/` e `regras/`).
- [x] Implementação do Grafo LangGraph (`agente/grafo.py`), Leitura/Visão Multimodal (`agente/leitor_documentos.py`, `agente/visao_multimodal.py`), Prompts (`prompts/`) e Testes (`tests/test_agente.py`).
- [x] Implementação da infraestrutura Docker, Nginx e script de firewall OCI (`deploy/`).
- [x] Resolução de todos os pontos de integração da Fase 1 atribuídos à Ingrid (`E1`, `E5`, `E6`, `E7`, `E8`).
- [x] Code Review e Merge do **PR #3** do André (`api/` e `armazenamento/`).
- [x] **Merge do PR #2 (`feature/agente-deploy-ingrid`) na `main` (`d67e823`):** Grafo LangGraph, Visão Multimodal, Prompts e Deploy integrados na branch principal.
- [x] **Integração API $\leftrightarrow$ Grafo (Item E7):** `api/triagem.py` conectado ao grafo real (`executar_triagem_mediflow` / `analisar_documento` do pacote `agente`), `.env.example` documentado com Google Gemini (`gemini-2.5-flash`) e 90/90 testes automatizados passando.
- [x] **Code Review, Merge e Integração da Interface Streamlit (PR #4 — `interface/app.py`):** Integrada na `main` com suporte a `MEDIFLOW_API_URL` (local/Docker), upload multimodal (`PDF`/`PNG`/`JPG` em Base64) e conexão direta com a API e o Grafo LangGraph.
- [ ] **Próximo Passo Final (Deploy em Nuvem OCI):** Subir a aplicação integrada na VM da OCI Always Free utilizando `deploy/setup_oci_vm.sh` e `deploy/docker-compose.yml`.
