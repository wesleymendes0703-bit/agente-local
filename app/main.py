"""Servidor web do agente (FastAPI). Roda com: python -m app.main"""
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agente, cerebro, conhecimento, db, ferramentas, memoria
from .config import CHAVES, CHAVES_EXTRAS, HOST, PORTA, STATIC, chave, gravar_chave

COOKIE = "agente_sessao"
SESSAO_DIAS = 30


# ---------- senha e sessao ----------

def _hash(senha, sal=None):
    sal = sal or secrets.token_hex(16)
    h = hashlib.scrypt(senha.encode(), salt=bytes.fromhex(sal), n=2**14, r=8, p=1).hex()
    return f"{sal}${h}"


def _confere(senha, guardado):
    sal, _ = guardado.split("$", 1)
    return hmac.compare_digest(_hash(senha, sal), guardado)


def _nova_sessao(resp: Response):
    token = secrets.token_urlsafe(32)
    db.x("INSERT INTO sessoes(token,expira) VALUES(?,?)", (token, time.time() + SESSAO_DIAS * 86400))
    resp.set_cookie(COOKIE, token, max_age=SESSAO_DIAS * 86400, httponly=True, samesite="strict")


def logado(request: Request):
    token = request.cookies.get(COOKIE, "")
    if not token or not db.q("SELECT 1 FROM sessoes WHERE token=? AND expira>?", (token, time.time())):
        raise HTTPException(401, "entre com sua senha")
    return token


_falhas: dict[str, list[float]] = {}


def _limite_login(ip):
    agora = time.time()
    lista = [t for t in _falhas.get(ip, []) if agora - t < 300]
    _falhas[ip] = lista
    if len(lista) >= 5:
        raise HTTPException(429, "muitas tentativas, espere 5 minutos")


# ---------- agendador de lembretes ----------

_sync_lock = asyncio.Lock()
_tarefas: set = set()  # segura as tarefas vivas ate terminarem


async def _agendador():
    while True:
        try:
            for r in db.q("SELECT * FROM lembretes WHERE ativo=1 AND quando<=?", (time.time(),)):
                prox = ferramentas.proxima(r)
                if prox:
                    db.x("UPDATE lembretes SET quando=? WHERE id=?", (prox, r["id"]))
                else:
                    db.x("UPDATE lembretes SET ativo=0 WHERE id=?", (r["id"],))
                if r["tarefa_ia"]:
                    t = asyncio.create_task(_rodar_tarefa(r))
                    _tarefas.add(t)
                    t.add_done_callback(_tarefas.discard)
                else:
                    db.notificar("Lembrete", r["texto"])
        except Exception as ex:  # nunca deixa o agendador morrer
            print("agendador:", ex, flush=True)
        try:
            s = db.ajuste("cerebro_sync") or {}
            ligado = chave(conhecimento.ENV_TOKEN) or s.get("ok")
            if ligado and conhecimento.precisa_sincronizar() and not _sync_lock.locked():
                async with _sync_lock:
                    ok, msg = await asyncio.to_thread(conhecimento.sincronizar)
                    print("cerebro:", msg, flush=True)
        except Exception as ex:
            print("cerebro:", type(ex).__name__, flush=True)
        await asyncio.sleep(15)


async def _rodar_tarefa(r):
    try:
        texto, info = await agente.tarefa(r["texto"])
    except Exception as ex:
        print("tarefa:", type(ex).__name__, ex, flush=True)
        texto, info = f"Era hora de: {r['texto']}\n(nenhuma IA respondeu agora)", {}
    conv = db.ajuste("conversa_tarefas")
    if not conv or not db.q("SELECT 1 FROM conversas WHERE id=?", (conv,)):
        conv = db.x("INSERT INTO conversas(titulo,criada,atualizada) VALUES(?,?,?)",
                    ("Tarefas agendadas", time.time(), time.time()))
        db.set_ajuste("conversa_tarefas", conv)
    db.x("INSERT INTO mensagens(conversa_id,papel,conteudo,modelo,info,criada) VALUES(?,?,?,?,?,?)",
         (conv, "assistant", texto, info.get("rotulo"), json.dumps(info, ensure_ascii=False), time.time()))
    db.x("UPDATE conversas SET atualizada=? WHERE id=?", (time.time(), conv))
    db.notificar("Tarefa feita", texto[:300])


@asynccontextmanager
async def vida(app):
    memoria.iniciar(db.ajuste("nome_agente", "Levi"))
    t = asyncio.create_task(_agendador())
    yield
    t.cancel()


app = FastAPI(lifespan=vida, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def cabecalhos(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------- acesso ----------

class PrimeiroAcesso(BaseModel):
    senha: str
    nome_agente: str = "Levi"
    nome_dono: str = ""


class Senha(BaseModel):
    senha: str


class TrocaSenha(BaseModel):
    atual: str
    nova: str


@app.get("/api/estado")
def estado(request: Request):
    configurado = bool(db.ajuste("senha"))
    try:
        logado(request)
        ok = True
    except HTTPException:
        ok = False
    return {"configurado": configurado, "logado": ok, "nome": db.ajuste("nome_agente", "Levi")}


@app.post("/api/primeiro-acesso")
def primeiro_acesso(d: PrimeiroAcesso, resp: Response):
    if db.ajuste("senha"):
        raise HTTPException(409, "a senha ja foi criada")
    if len(d.senha) < 6:
        raise HTTPException(400, "a senha precisa de pelo menos 6 caracteres")
    nome = d.nome_agente.strip()[:40] or "Levi"
    db.set_ajuste("senha", _hash(d.senha))
    db.set_ajuste("nome_agente", nome)
    memoria.iniciar(nome, d.nome_dono.strip()[:60] or "meu dono", forcar=True)
    _nova_sessao(resp)
    return {"ok": True}


@app.post("/api/entrar")
def entrar(d: Senha, request: Request, resp: Response):
    ip = request.client.host if request.client else "?"
    _limite_login(ip)
    guardado = db.ajuste("senha")
    if not guardado or not _confere(d.senha, guardado):
        _falhas.setdefault(ip, []).append(time.time())
        raise HTTPException(401, "senha errada")
    _nova_sessao(resp)
    return {"ok": True}


@app.post("/api/sair")
def sair(resp: Response, token=Depends(logado)):
    db.x("DELETE FROM sessoes WHERE token=?", (token,))
    resp.delete_cookie(COOKIE)
    return {"ok": True}


@app.post("/api/senha")
def trocar_senha(d: TrocaSenha, request: Request, resp: Response, token=Depends(logado)):
    _limite_login(request.client.host if request.client else "?")
    if not _confere(d.atual, db.ajuste("senha")):
        raise HTTPException(401, "senha atual errada")
    if len(d.nova) < 6:
        raise HTTPException(400, "a senha nova precisa de pelo menos 6 caracteres")
    db.set_ajuste("senha", _hash(d.nova))
    db.x("DELETE FROM sessoes")  # derruba os outros aparelhos
    _nova_sessao(resp)
    return {"ok": True}


# ---------- conversas ----------

class Envio(BaseModel):
    texto: str = ""
    imagem: str | None = None


class Titulo(BaseModel):
    titulo: str


@app.get("/api/conversas", dependencies=[Depends(logado)])
def conversas():
    return db.q("SELECT id, titulo, atualizada FROM conversas ORDER BY atualizada DESC LIMIT 200")


@app.post("/api/conversas", dependencies=[Depends(logado)])
def nova_conversa():
    agora = time.time()
    cid = db.x("INSERT INTO conversas(titulo,criada,atualizada) VALUES(?,?,?)", ("Nova conversa", agora, agora))
    return {"id": cid, "titulo": "Nova conversa"}


@app.patch("/api/conversas/{cid}", dependencies=[Depends(logado)])
def renomear(cid: int, d: Titulo):
    db.x("UPDATE conversas SET titulo=? WHERE id=?", (d.titulo.strip()[:80] or "Conversa", cid))
    return {"ok": True}


@app.delete("/api/conversas/{cid}", dependencies=[Depends(logado)])
def apagar_conversa(cid: int):
    db.x("DELETE FROM mensagens WHERE conversa_id=?", (cid,))
    db.x("DELETE FROM conversas WHERE id=?", (cid,))
    return {"ok": True}


@app.get("/api/conversas/{cid}/mensagens", dependencies=[Depends(logado)])
def mensagens(cid: int):
    rs = db.q("SELECT id, papel, conteudo, imagem, modelo, info, criada FROM mensagens "
              "WHERE conversa_id=? ORDER BY id", (cid,))
    for r in rs:
        r["info"] = json.loads(r["info"]) if r["info"] else None
    return rs


@app.post("/api/conversas/{cid}/enviar", dependencies=[Depends(logado)])
async def enviar(cid: int, d: Envio):
    conv = db.q("SELECT titulo FROM conversas WHERE id=?", (cid,))
    if not conv:
        raise HTTPException(404, "conversa nao existe")
    texto = d.texto.strip()[:20000]
    if not texto and not d.imagem:
        raise HTTPException(400, "mensagem vazia")
    if d.imagem and (not d.imagem.startswith("data:image/") or len(d.imagem) > 6_000_000):
        raise HTTPException(400, "imagem invalida ou grande demais")
    if conv[0]["titulo"] == "Nova conversa":
        db.x("UPDATE conversas SET titulo=? WHERE id=?", ((texto or "Imagem")[:50], cid))
    return await agente.conversar(cid, texto, d.imagem)


@app.post("/api/audio", dependencies=[Depends(logado)])
async def audio(arquivo: UploadFile):
    k = chave("GROQ_API_KEY")
    if not k:
        raise HTTPException(400, "pra usar audio, cole a chave da Groq na Configuracao")
    dados = await arquivo.read()
    if len(dados) > 20_000_000:
        raise HTTPException(400, "audio grande demais")
    async with httpx.AsyncClient(timeout=60) as cli:
        r = await cli.post("https://api.groq.com/openai/v1/audio/transcriptions",
                           headers={"Authorization": f"Bearer {k}"},
                           data={"model": "whisper-large-v3-turbo", "language": "pt", "response_format": "json"},
                           files={"file": (arquivo.filename or "audio.webm", dados,
                                           arquivo.content_type or "audio/webm")})
    if r.status_code != 200:
        raise HTTPException(502, f"a transcricao falhou ({r.status_code})")
    return {"texto": r.json().get("text", "").strip()}


# ---------- cerebro ----------

class Combo(BaseModel):
    entradas: list[dict]


@app.get("/api/cerebro", dependencies=[Depends(logado)])
def ver_cerebro():
    provs = {k: {"nome": v["nome"], "tem_chave": bool(chave(v["env"])), "so_free": v.get("so_free", False)}
             for k, v in cerebro.PROVEDORES.items()}
    return {"combo": cerebro.estado(), "provedores": provs}


@app.put("/api/cerebro", dependencies=[Depends(logado)])
def salvar_cerebro(d: Combo):
    return {"combo": cerebro.salvar_combo(d.entradas)}


@app.post("/api/cerebro/padrao", dependencies=[Depends(logado)])
def cerebro_padrao():
    return {"combo": cerebro.salvar_combo(cerebro.COMBO_PADRAO)}


@app.post("/api/cerebro/despausar", dependencies=[Depends(logado)])
def despausar():
    cerebro.limpar_pausas()
    return {"ok": True}


@app.get("/api/cerebro/modelos/{prov}", dependencies=[Depends(logado)])
async def modelos(prov: str):
    if prov not in cerebro.PROVEDORES:
        raise HTTPException(404, "provedor desconhecido")
    try:
        return {"modelos": await cerebro.listar_modelos(prov)}
    except httpx.HTTPError as ex:
        raise HTTPException(502, f"nao consegui listar ({type(ex).__name__})")


# ---------- memoria ----------

class Memoria(BaseModel):
    persona: str | None = None
    memoria: str | None = None


@app.get("/api/memoria", dependencies=[Depends(logado)])
def ver_memoria():
    memoria.iniciar(db.ajuste("nome_agente", "Levi"))
    return {"persona": memoria.ler(memoria.PERSONA), "memoria": memoria.ler(memoria.MEMORIA),
            "dias": memoria.dias_do_diario()[:60]}


@app.put("/api/memoria", dependencies=[Depends(logado)])
def salvar_memoria(d: Memoria):
    if d.persona is not None:
        memoria.gravar(memoria.PERSONA, d.persona[:20000])
    if d.memoria is not None:
        memoria.gravar(memoria.MEMORIA, d.memoria[:100000])
    return {"ok": True}


@app.get("/api/diario/{dia}", dependencies=[Depends(logado)])
def ver_diario(dia: str):
    if dia not in memoria.dias_do_diario():
        raise HTTPException(404, "dia sem diario")
    return {"texto": memoria.ler(memoria.DIARIO / f"{dia}.md")}


# ---------- lembretes e avisos ----------

class Lembrete(BaseModel):
    texto: str
    quando: str
    repetir: str = "nao"
    tarefa_ia: bool = False


@app.get("/api/lembretes", dependencies=[Depends(logado)])
def lembretes():
    return db.q("SELECT * FROM lembretes WHERE ativo=1 ORDER BY quando")


@app.post("/api/lembretes", dependencies=[Depends(logado)])
def criar_lembrete(d: Lembrete):
    try:
        return {"msg": ferramentas.criar_lembrete(d.texto, d.quando, d.repetir, d.tarefa_ia)}
    except ValueError:
        raise HTTPException(400, "data invalida")


@app.delete("/api/lembretes/{lid}", dependencies=[Depends(logado)])
def apagar_lembrete(lid: int):
    return {"msg": ferramentas.apagar_lembrete(lid)}


@app.get("/api/notificacoes", dependencies=[Depends(logado)])
def notificacoes(depois: int = 0):
    return db.q("SELECT * FROM notificacoes WHERE id>? AND lida=0 ORDER BY id LIMIT 20", (depois,))


@app.post("/api/notificacoes/lidas", dependencies=[Depends(logado)])
def lidas():
    db.x("UPDATE notificacoes SET lida=1")
    return {"ok": True}


# ---------- configuracao ----------

class Chave(BaseModel):
    nome: str
    valor: str


@app.get("/api/config", dependencies=[Depends(logado)])
def ver_config():
    sites = {v["env"]: (k, v["site"]) for k, v in cerebro.PROVEDORES.items()}
    return {"chaves": [{"nome": n, "rotulo": r, "configurada": bool(chave(n)),
                        "provedor": sites[n][0], "site": sites[n][1]} for n, r in CHAVES.items()]}


@app.put("/api/config/chave", dependencies=[Depends(logado)])
def salvar_chave(d: Chave):
    if d.nome not in CHAVES and d.nome not in CHAVES_EXTRAS:
        raise HTTPException(400, "chave desconhecida")
    gravar_chave(d.nome, d.valor)
    prov = next((k for k, v in cerebro.PROVEDORES.items() if v["env"] == d.nome), None)
    if prov:
        cerebro._pausas.pop(prov, None)
    return {"ok": True}


# ---------- segundo cerebro ----------

class Conhecimento(BaseModel):
    repo: str | None = None
    excluidos: list[str] | None = None


@app.get("/api/conhecimento", dependencies=[Depends(logado)])
def ver_conhecimento():
    return conhecimento.estado()


@app.put("/api/conhecimento", dependencies=[Depends(logado)])
async def salvar_conhecimento(d: Conhecimento):
    async with _sync_lock:
        try:
            await asyncio.to_thread(conhecimento.salvar, d.repo, d.excluidos)
        except ValueError as ex:
            raise HTTPException(400, str(ex))
    return conhecimento.estado()


@app.post("/api/conhecimento/sincronizar", dependencies=[Depends(logado)])
async def sincronizar_conhecimento():
    if _sync_lock.locked():
        raise HTTPException(409, "ja estou sincronizando, espere um pouco")
    async with _sync_lock:
        ok, msg = await asyncio.to_thread(conhecimento.sincronizar)
    return {"ok": ok, "msg": msg, **conhecimento.estado()}


@app.post("/api/config/testar/{prov}", dependencies=[Depends(logado)])
async def testar(prov: str):
    if prov not in cerebro.PROVEDORES:
        raise HTTPException(404, "provedor desconhecido")
    ok, msg = await cerebro.testar_chave(prov)
    return {"ok": ok, "msg": msg}


# ---------- arquivos do app ----------

@app.get("/")
def inicio():
    return FileResponse(STATIC / "index.html")


@app.get("/sw.js")
def sw():
    return FileResponse(STATIC / "sw.js", media_type="text/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORTA, log_level=os.environ.get("AGENTE_LOG", "warning"),
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")
