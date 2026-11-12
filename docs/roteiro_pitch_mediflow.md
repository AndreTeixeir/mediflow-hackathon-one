# Roteiro Oficial do Pitch e Apresentacao — MediFlow

**Projeto:** Projeto 2 - MediFlow (Agente Autonomo para Triagem, Extracao e Roteamento de Documentos Clinicos)  
**Hackathon:** ONE G10 (Oracle Next Education & Alura)  
**Equipe:** Grupo 10 (g10-Brasil27)  
**Tempo estimado:** 5 a 7 minutos  
**Ordem de apresentacao:** Guilherme -> Lethicia -> Ingrid -> Andre  

---

## Bloco 1: Introducao, O Problema e a Interface (1 min)

**Apresentador:** Guilherme  
**Cenario de tela:** Tela inicial do Streamlit aberta na aba "Envio e Triagem de Documentos".  

**Fala do Guilherme:**  
"Ola avaliadores! Nos somos o Grupo 10 e apresentamos o MediFlow, um agente autonomo e multimodal desenvolvido para resolver um dos maiores gargalos da saude: a triagem lenta e manual de laudos, exames e receitas. Erros manuais e atrasos geram perdas financeiras e, principalmente, comprometem o atendimento em situacoes criticas.

Desenvolvemos uma interface no Streamlit intuitiva e preparada para o ambiente hospitalar. O profissional pode digitar o texto clinico ou fazer o upload direto de documentos em PDF digital, escaneado ou foto de receita.

Por tras dessa interface, opera um ecossistema rigoroso de validacao clinica, que a Lethicia vai apresentar agora."

---

## Bloco 2: Contratos de Dados, Validacao Clinica e Human-in-the-Loop (1.5 min)

**Apresentadora:** Lethicia  
**Cenario de tela:** Aba "Fila de Auditoria Clinica" no Streamlit demonstrando o Cenario 3 (Caso Ambiguo / Receita Rasurada).  

**Fala da Lethicia:**  
"Na area da saude, a seguranca do paciente exige rigor absoluto. Por isso, estruturei os Contratos de Dados utilizando Pydantic, assegurando que tanto a entrada quanto a saida sigam schemas estritos com validacao de paciente, medico, CRM e CID-10.

Desenvolvi tambem o motor de regras clinicas deterministico, responsavel por calcular o Score de Confianca e apontar inconsistencias, como ausencia de CRM valido ou medicamentos ilegiveis.

Quando um documento apresenta confiabilidade reduzida, como esta receita ilegivel do Cenario 3, o sistema retem o documento na Fila de Revisao Humana (Human-in-the-Loop). O auditor clinico analisa o documento original, revisa as informacoes extraidas, faz os ajustes necessarios no formulario e aprova ou rejeita o roteamento.

A orquestracao de todo esse fluxo inteligente e conduzida pelo agente de decisao, implementado pela Ingrid."

---

## Bloco 3: Grafo LangGraph, Visao Multimodal (Gemini) e Seguranca (1.5 min)

**Apresentadora:** Ingrid  
**Cenario de tela:** Demonstracao do Cenario 2 (Urgencia Critica - TEP Agudo) no Streamlit, exibindo o alerta gerado e o fluxo do LangGraph.  

**Fala da Ingrid:**  
"Para estruturar o raciocinio e as decisoes do agente, desenvolvi o Grafo de Decisao Condicional utilizando LangGraph. O documento percorre nos dedicados: Ingestao, Classificacao e Extracao, Avaliacao de Consistencia e Urgencia, e as Arestas de Roteamento Especializado.

Integrei a Visao Multimodal com Google Gemini (gemini-2.5-flash) utilizando Structured Outputs. O pipeline processa PDFs digitais nativamente e direciona PDFs escaneados ou fotos de receitas diretamente para a visao do modelo, contando tambem com fallback deterministico local para total resiliencia.

Ao processarmos este laudo com suspeita de Tromboembolismo Pulmonar Agudo (TEP), o agente identifica a criticidade imediatamente, eleva a prioridade para Urgente e gera uma notificacao instantanea para o canal do Plantao Medico.

A solucao foi empacotada em containers Docker multi-stage com usuario nao-root e proxy reverso Nginx com rate-limiting, garantindo seguranca e conformidade com a nuvem OCI, como o Andre demonstrara."

---

## Bloco 4: API FastAPI, OCI Object Storage Always Free e Conclusao (1.5 min)

**Apresentador:** Andre  
**Cenario de tela:** Aba "Historico & Buckets OCI" no Streamlit ou documentacao Swagger em /docs, exibindo os documentos segregados nos buckets.  

**Fala do Andre:**  
"Para interligar todas as camadas da aplicacao, desenvolvi a API REST em FastAPI, expondo as rotas de saude e triagem clinica. A API opera com controle transacional e compensacao automatica, garantindo que qualquer falha nao corrompa os registros.

Em total conformidade com as diretrizes do programa ONE, implementamos a persistencia dos documentos no Oracle Cloud Infrastructure (OCI) Object Storage na camada Always Free.

Os arquivos e as respostas estruturadas sao segregados por status nos buckets dedicados: mediflow-recebidos, mediflow-rotina para prontuario e farmacia, mediflow-urgentes para casos criticos e mediflow-auditoria-humana para revisao.

O MediFlow entrega uma solucao integrada: usabilidade simples, criterios clinicos consistentes, IA multimodal autonoma e infraestrutura em nuvem segura, reduzindo o tempo de triagem de horas para segundos com total rastreabilidade. Muito obrigado!"

---

## Guia Rapido de Gravacao da Equipe

1. Formato de gravacao: Reuniao gravada (Google Meet, Zoom ou Teams) com as cameras dos participantes abertas e tela compartilhada.
2. Demonstracao dos 3 cenarios obrigatorios durante as falas:
   - Cenario 1 (Rotina - Hemograma): Apresentado rapidamente durante a introducao ou demonstracao dos buckets.
   - Cenario 2 (Urgencia - TEP Agudo): Demonstrado na fala da Ingrid para destacar o alerta de emergencia imediato.
   - Cenario 3 (Auditoria Humana - Receita Rasurada): Demonstrado na fala da Lethicia para destacar o painel Human-in-the-Loop.
3. Exibicao dos Buckets OCI: Demonstrado na fala do Andre para comprovar o armazenamento segregado na nuvem Oracle Always Free.
