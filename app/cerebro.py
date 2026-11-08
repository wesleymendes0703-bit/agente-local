"""Roteador de IA com fallback (o "combo").

Tenta cada modelo do combo na ordem. Se um responde 429 (cota), 5xx,
timeout ou erro de rede, pausa aquele modelo por um tempo e passa pro
proximo. Chave invalida (401/403) pausa o provedor inteiro.
So IAs com plano gratis. Tudo via API compativel com OpenAI.
"""
import json
import os
import re
import time

import httpx

from . import db
from .config import chave

PROVEDORES = {
    "gemini": {
        "nome": "Gemini",
        "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env": "GEMINI_API_KEY",
        "site": "https://aistudio.google.com/apikey",
    },
    "groq": {
        "nome": "Groq",
        "base": "https://api.groq.com/openai/v1",
        "env": "GROQ_API_KEY",
        "site": "https://console.groq.com/keys",
    },
    "openrouter": {
        "nome": "OpenRouter",
        "base": "https://openrouter.ai/api/v1",
        "env": "OPENROUTER_API_KEY",
        "site": "https://openrouter.ai/keys",
        "so_free": True,  # so aceita modelos terminados em :free
    },
    "cerebras": {
        "nome": "Cerebras",
        "base": "https://api.cerebras.ai/v1",
        "env": "CEREBRAS_API_KEY",
        "site": "https://cloud.cerebras.ai",
    },
    "mistral": {
        "nome": "Mistral",
        "base": "https://api.mistral.ai/v1",
        "env": "MISTRAL_API_KEY",
        "site": "https://console.mistral.ai/api-keys",
    },
}

COMBO_PADRAO = [
    {"provedor": "gemini", "modelo": "gemini-flash-latest", "visao": True, "ferramentas": True},
    {"provedor": "groq", "modelo": "openai/gpt-oss-120b", "visao": False, "ferramentas": True},
    {"provedor": "gemini", "modelo": "gemini-flash-lite-latest", "visao": True, "ferramentas": True},
    {"provedor": "cerebras", "modelo": "gpt-oss-120b", "visao": False, "ferramentas": True},
    {"provedor": "openrouter", "modelo": "google/gemma-4-31b-it:free", "visao": True, "ferramentas": True},
    {"provedor": "mistral", "modelo": "mistral-small-latest", "visao": True, "ferramentas": True},
    {"provedor": "groq", "modelo": "openai/gpt-oss-20b", "visao": False, "ferramentas": True},
]

PAUSA_COTA = 5 * 60
PAUSA_ERRO = 60
PAUSA_CHAVE = 60 * 60

_pausas: dict[str, tuple[float, str]] = {}  # "prov/modelo" ou "prov" -> (ate, motivo)


def combo():
    c = db.ajuste("combo")
    return c if c else [dict(e) for e in COMBO_PADRAO]


def salvar_combo(entradas):
    limpo = []
    for e in entradas:
        p = str(e.get("provedor", ""))
        m = str(e.get("modelo", "")).strip()
        if p not in PROVEDORES or not m:
            continue
        if PROVEDORES[p].get("so_free") and not m.endswith(":free"):
            continue
        limpo.append({"provedor": p, "modelo": m, "visao": bool(e.get("visao")),
                      "ferramentas": bool(e.get("ferramentas", True)),
                      "ligado": bool(e.get("ligado", True))})
    db.set_ajuste("combo", limpo)
    return limpo


def _pausar(k, segundos, motivo):
    _pausas[k] = (time.time() + segundos, motivo)


def _pausado(k):
    p = _pausas.get(k)
    if p and p[0] > time.time():
        return p
    _pausas.pop(k, None)
    return None


def estado():
    """Situacao de cada entrada do combo, pra tela Cerebro."""
    out = []
    for e in combo():
        prov = PROVEDORES[e["provedor"]]
        s = {**e, "nome_provedor": prov["nome"], "tem_chave": bool(chave(prov["env"]))}
        p = _pausado(e["provedor"]) or _pausado(f'{e["provedor"]}/{e["modelo"]}')
        if p:
            s["pausado_ate"] = p[0]
            s["motivo"] = p[1]
        out.append(s)
    return out


def limpar_pausas():
    _pausas.clear()


class SemIA(Exception):
    pass


# ---------- modo "ferramenta por prompt" (modelos sem tool calling) ----------

TAG = re.compile(r"<ferramenta>\s*(\{.*?\})\s*</ferramenta>", re.S)


def _instrucao_prompt(ferramentas):
    linhas = ["\n\n## Ferramentas",
              "Quando precisar de uma ferramenta, responda SO com uma linha assim:",
              '<ferramenta>{"nome": "NOME", "args": {...}}</ferramenta>',
              "e espere o resultado. Ferramentas disponiveis:"]
    for f in ferramentas:
        fn = f["function"]
        linhas.append(f'- {fn["name"]}: {fn["description"]} Args: '
                      f'{json.dumps(fn["parameters"].get("properties", {}), ensure_ascii=False)}')
    return "\n".join(linhas)


def _para_prompt(msgs, ferramentas):
    out = []
    for m in msgs:
        if m["role"] == "system" and ferramentas:
            out.append({"role": "system", "content": m["content"] + _instrucao_prompt(ferramentas)})
        elif m["role"] == "assistant" and m.get("tool_calls"):
            txt = "".join(
                f'<ferramenta>{json.dumps({"nome": t["function"]["name"], "args": _args(t)}, ensure_ascii=False)}</ferramenta>'
                for t in m["tool_calls"])
            out.append({"role": "assistant", "content": (m.get("content") or "") + txt})
        elif m["role"] == "tool":
            out.append({"role": "user", "content": f"[resultado da ferramenta]\n{m['content']}"})
        else:
            out.append(m)
    return out


def _args(tc):
    try:
        return json.loads(tc["function"].get("arguments") or "{}")
    except json.JSONDecodeError:
        return {}


def _ler_prompt(conteudo):
    achados = TAG.findall(conteudo or "")
    if not achados:
        return None
    calls = []
    for i, bruto in enumerate(achados):
        try:
            d = json.loads(bruto)
        except json.JSONDecodeError:
            continue
        calls.append({"id": f"p{int(time.time())}{i}", "type": "function",
                      "function": {"name": d.get("nome", ""),
                                   "arguments": json.dumps(d.get("args", {}), ensure_ascii=False)}})
    return calls or None


# ---------- imagens ----------

def _tem_imagem(msgs):
    for m in msgs:
        if isinstance(m.get("content"), list):
            if any(p.get("type") == "image_url" for p in m["content"]):
                return True
    return False


def _sem_imagem(msgs):
    out = []
    for m in msgs:
        if isinstance(m.get("content"), list):
            txt = " ".join(p.get("text", "") for p in m["content"] if p.get("type") == "text")
            out.append({**m, "content": txt + "\n(o usuario mandou uma imagem, mas este modelo nao enxerga imagens)"})
        else:
            out.append(m)
    return out


# ---------- chamada ----------

def _limpar(texto):
    return re.sub(r"<think>.*?</think>", "", texto or "", flags=re.S).strip()


async def _chamar(cli, entrada, msgs, ferramentas, modo_prompt):
    prov = PROVEDORES[entrada["provedor"]]
    simular = os.environ.get("AGENTE_SIMULAR_COTA", "").split(",")
    if entrada["provedor"] in simular:
        return 429, {"error": {"message": "cota simulada (teste)"}}
    corpo = {"model": entrada["modelo"], "messages": msgs, "temperature": 0.7}
    if ferramentas and not modo_prompt:
        corpo["tools"] = ferramentas
    cab = {"Authorization": f"Bearer {chave(prov['env'])}"}
    if entrada["provedor"] == "openrouter":
        cab["X-Title"] = "Agente Local"
    r = await cli.post(f"{prov['base']}/chat/completions", json=corpo, headers=cab)
    try:
        dados = r.json()
    except ValueError:
        dados = {"error": {"message": r.text[:300]}}
    return r.status_code, dados


def _erro(dados):
    e = dados.get("error") if isinstance(dados, dict) else None
    if isinstance(e, dict):
        return str(e.get("message", ""))[:200]
    if isinstance(dados, list) and dados and isinstance(dados[0], dict):
        return _erro(dados[0])
    return str(e or dados)[:200]


async def completar(msgs, ferramentas=None):
    """Devolve (mensagem_assistente, info). Levanta SemIA se ninguem respondeu."""
    entradas = [e for e in combo() if e.get("ligado", True)]
    precisa_visao = _tem_imagem(msgs)
    if precisa_visao and any(e.get("visao") for e in entradas):
        # modelos que enxergam primeiro, os outros depois (sem a imagem)
        entradas = [e for e in entradas if e.get("visao")] + [e for e in entradas if not e.get("visao")]
    tentativas = []
    timeout = httpx.Timeout(90, connect=10)
    async with httpx.AsyncClient(timeout=timeout) as cli:
        for e in entradas:
            prov = PROVEDORES.get(e["provedor"])
            rotulo = f'{prov["nome"]} {e["modelo"]}' if prov else e["modelo"]
            if not prov or not chave(prov["env"]):
                tentativas.append({"modelo": rotulo, "erro": "sem chave"})
                continue
            p = _pausado(e["provedor"]) or _pausado(f'{e["provedor"]}/{e["modelo"]}')
            if p:
                tentativas.append({"modelo": rotulo, "erro": f"pausado ({p[1]})"})
                continue
            m = msgs if e.get("visao") else _sem_imagem(msgs)
            modo_prompt = bool(ferramentas) and not e.get("ferramentas", True)
            for _ in range(2):  # 2a volta: tenta de novo em modo prompt se tools deu 400
                env = _para_prompt(m, ferramentas) if modo_prompt else m
                try:
                    status, dados = await _chamar(cli, e, env, ferramentas, modo_prompt)
                except (httpx.TimeoutException, httpx.NetworkError) as ex:
                    _pausar(f'{e["provedor"]}/{e["modelo"]}', PAUSA_ERRO, "sem resposta")
                    tentativas.append({"modelo": rotulo, "erro": f"rede: {type(ex).__name__}"})
                    break
                if status == 200 and dados.get("choices"):
                    msg = dados["choices"][0].get("message") or {}
                    saida = {"role": "assistant", "content": _limpar(msg.get("content"))}
                    if msg.get("tool_calls"):
                        saida["tool_calls"] = msg["tool_calls"]
                    elif modo_prompt:
                        calls = _ler_prompt(msg.get("content"))
                        if calls:
                            saida = {"role": "assistant", "content": TAG.sub("", saida["content"]).strip(),
                                     "tool_calls": calls}
                    if not saida["content"] and not saida.get("tool_calls"):
                        tentativas.append({"modelo": rotulo, "erro": "resposta vazia"})
                        break
                    info = {"provedor": e["provedor"], "modelo": e["modelo"], "rotulo": rotulo,
                            "tentativas": tentativas, "modo_prompt": modo_prompt}
                    return saida, info
                erro = _erro(dados)
                if status == 400 and ferramentas and not modo_prompt:
                    modo_prompt = True  # modelo nao gostou das tools: usa o modo prompt
                    continue
                if status in (401, 403):
                    _pausar(e["provedor"], PAUSA_CHAVE, "chave recusada")
                elif status in (402, 429):
                    _pausar(f'{e["provedor"]}/{e["modelo"]}', PAUSA_COTA, "cota acabou")
                elif status >= 500:
                    _pausar(f'{e["provedor"]}/{e["modelo"]}', PAUSA_ERRO, "servidor instavel")
                tentativas.append({"modelo": rotulo, "erro": f"{status}: {erro}"})
                break
    raise SemIA(tentativas)


async def testar_chave(provedor):
    prov = PROVEDORES[provedor]
    k = chave(prov["env"])
    if not k:
        return False, "sem chave"
    try:
        async with httpx.AsyncClient(timeout=20) as cli:
            r = await cli.get(f"{prov['base']}/models", headers={"Authorization": f"Bearer {k}"})
            if provedor == "openrouter":  # /models e publico; /key confere a chave
                r = await cli.get(f"{prov['base']}/key", headers={"Authorization": f"Bearer {k}"})
    except httpx.HTTPError as ex:
        return False, f"sem conexao ({type(ex).__name__})"
    if r.status_code == 200:
        return True, "funcionando"
    return False, f"recusada ({r.status_code})"


async def listar_modelos(provedor):
    prov = PROVEDORES[provedor]
    k = chave(prov["env"])
    cab = {"Authorization": f"Bearer {k}"} if k else {}
    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.get(f"{prov['base']}/models", headers=cab)
    r.raise_for_status()
    ids = [m["id"].removeprefix("models/") for m in r.json().get("data", [])]
    if prov.get("so_free"):
        ids = [i for i in ids if i.endswith(":free")]
    ruins = ("whisper", "tts", "guard", "embed", "image", "audio", "orpheus", "live", "moderation")
    return sorted(i for i in ids if not any(b in i for b in ruins))
