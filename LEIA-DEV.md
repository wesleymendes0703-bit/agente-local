# LEIA-DEV (tecnico)

## Stack
- Python 3.12, FastAPI, uvicorn (sem extras), httpx, python-multipart. Sem Docker, sem Node, sem build.
- SQLite (WAL) em `dados/agente.db`. Frontend em HTML/CSS/JS puro (`static/`), PWA com service worker.
- RSS medido: ~55 MB ocioso. Roda em CPU sem AVX (nada de wheel com AVX: so codigo Python puro + pydantic-core).

## Estrutura
```
app/
  config.py       caminhos, fuso, leitura/escrita do .env (chmod 600, escrita atomica)
  db.py           sqlite: ajustes, sessoes, conversas, mensagens, lembretes, notificacoes
  cerebro.py      roteador de fallback ("combo"), provedores, modo ferramenta-por-prompt
  ferramentas.py  tools: anotar_memoria, criar/listar/apagar_lembrete, buscar_web, ler_pagina, data_hora
  agente.py       monta o system prompt (persona + memoria + hora) e roda o laco IA -> tools
  memoria.py      persona.md, memoria.md, diario/AAAA-MM-DD.md
  main.py         rotas HTTP, auth, agendador (asyncio, 15 s), estaticos
static/           index.html, app.js, style.css, sw.js, manifest, icones
install.sh        apt + venv + systemd + tailscale + tailscale serve (HTTPS)
```

## Roteador (cerebro.py)
- Todos os provedores via API compativel com OpenAI (`/chat/completions`): Gemini (`/v1beta/openai`), Groq, OpenRouter, Cerebras, Mistral.
- Combo = lista ordenada `{provedor, modelo, visao, ferramentas, ligado}` salva em `ajustes.combo`.
- Por entrada: sem chave -> pula; pausada -> pula; 429/402 -> pausa o modelo 5 min; 5xx -> 1 min; timeout/rede -> 1 min; 401/403 -> pausa o provedor 1 h; 400 com tools -> repete no modo prompt.
- Pausas ficam em memoria (somem no restart, de proposito).
- Imagem na ultima mensagem: modelos com `visao` vao pra frente; os outros recebem o texto com aviso de que havia imagem.
- Modo prompt (modelos sem tool calling): as tools vao no system prompt e o modelo responde `<ferramenta>{"nome":..., "args":...}</ferramenta>`; o historico de tool_calls/tool e convertido pra texto.
- `tool_calls` do Gemini voltam intactos (preserva `extra_content`/thought signatures).
- OpenRouter: so aceita modelos `:free` (na tela e no backend).
- Teste de cota: `AGENTE_SIMULAR_COTA=gemini,groq` faz esses provedores responderem 429.

## Seguranca
- Sem ferramenta de terminal/arquivo. `ler_pagina` bloqueia IP privado/loopback/link-local (tambem apos redirect).
- Senha com scrypt (n=2^14). Sessao: token aleatorio em cookie httponly + SameSite=Strict, 30 dias. Trocar senha derruba as outras sessoes. 5 erros de login em 5 min por IP -> 429.
- Chaves so no `.env` (600). A API nunca devolve o valor, so "configurada".
- systemd: `NoNewPrivileges`, `ProtectSystem=strict`, `MemoryMax=600M`.
- Escuta 0.0.0.0:8080 (LAN/Tailscale). Nao abra porta no roteador. HTTPS via `tailscale serve`.

## Rodar em desenvolvimento
```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
AGENTE_HOST=127.0.0.1 AGENTE_PORTA=18765 AGENTE_DADOS=/tmp/dados-teste venv/bin/python -m app.main
```
Variaveis: `AGENTE_PORTA`, `AGENTE_HOST`, `AGENTE_DADOS`, `AGENTE_ENV`, `AGENTE_FUSO` (padrao America/Sao_Paulo), `AGENTE_LOG`, `AGENTE_SIMULAR_COTA`.

## API (todas exigem sessao, menos estado/primeiro-acesso/entrar)
`GET /api/estado` · `POST /api/primeiro-acesso` · `POST /api/entrar|sair|senha` ·
`GET|POST /api/conversas` · `PATCH|DELETE /api/conversas/{id}` · `GET /api/conversas/{id}/mensagens` · `POST /api/conversas/{id}/enviar {texto, imagem?}` ·
`POST /api/audio` (multipart `arquivo`, Groq whisper-large-v3-turbo) ·
`GET|PUT /api/cerebro` · `POST /api/cerebro/padrao|despausar` · `GET /api/cerebro/modelos/{prov}` ·
`GET|PUT /api/memoria` · `GET /api/diario/{dia}` ·
`GET|POST /api/lembretes` · `DELETE /api/lembretes/{id}` · `GET /api/notificacoes?depois=` · `POST /api/notificacoes/lidas` ·
`GET /api/config` · `PUT /api/config/chave` · `POST /api/config/testar/{prov}`

## Por que nao 9Router / LiteLLM / Open WebUI
- 9Router: Next.js com build pesado (1,5 a 2 GB de RAM), Google Analytics no painel, sync de chaves com servidor do autor a um clique, MITM com CA propria. Ver a auditoria no historico do projeto.
- LiteLLM: muitas dependencias, ~300 MB de RAM; para 5 provedores OpenAI-compatible um roteador de 300 linhas resolve.
- Open WebUI: pesado demais para a maquina alvo.

## Proximos passos (v1)
- Web Push (VAPID) pra avisar com o app fechado; hoje o aviso e por polling (app aberto ou em segundo plano).
- Streaming das respostas (SSE).
- Busca no historico; resumo automatico do diario a noite.
- Backup dos `dados/` (ex.: pro Google Drive).
