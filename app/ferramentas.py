"""Ferramentas que a IA pode usar. Nada de terminal: so coisas seguras."""
import ipaddress
import json
import re
import socket
import time
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from . import conhecimento, db, memoria
from .config import FUSO

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


def _f(nome, desc, props, obrig=()):
    return {"type": "function", "function": {
        "name": nome, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": list(obrig)}}}


DEFINICOES = [
    _f("anotar_memoria", "Guarda um fato duravel sobre o dono (preferencia, pessoa, projeto, data importante). "
       "Use quando ele contar algo que vale lembrar pra sempre ou pedir 'anota isso'.",
       {"fato": {"type": "string", "description": "o fato, curto e claro"}}, ["fato"]),
    _f("criar_lembrete", "Cria um lembrete ou tarefa num horario. O app avisa o dono na hora. "
       "Se tarefa_ia for true, na hora marcada a IA executa o texto como pedido e manda o resultado.",
       {"texto": {"type": "string", "description": "o que lembrar ou o pedido a executar"},
        "quando": {"type": "string", "description": "data e hora local no formato AAAA-MM-DDTHH:MM"},
        "repetir": {"type": "string", "enum": ["nao", "diario", "semanal"], "description": "repeticao"},
        "tarefa_ia": {"type": "boolean", "description": "true = a IA faz o pedido na hora marcada"}},
       ["texto", "quando"]),
    _f("listar_lembretes", "Lista os lembretes ativos.", {}),
    _f("apagar_lembrete", "Apaga um lembrete pelo numero.", {"id": {"type": "integer"}}, ["id"]),
    _f("buscar_web", "Pesquisa na internet (DuckDuckGo) e devolve titulos, links e resumos.",
       {"consulta": {"type": "string"}}, ["consulta"]),
    _f("ler_pagina", "Abre um link e devolve o texto da pagina.",
       {"url": {"type": "string"}}, ["url"]),
    _f("data_hora", "Devolve a data e hora atuais.", {}),
    _f("buscar_cerebro", "Busca por palavras no segundo cerebro do dono (notas pessoais: metas, pendencias, "
       "projetos, aulas, clientes, ideias, pessoas, decisoes). Devolve trechos com o caminho da nota.",
       {"termo": {"type": "string", "description": "palavras-chave, ex.: 'metas youtube'"}}, ["termo"]),
    _f("ler_nota", "Le uma nota inteira do segundo cerebro pelo caminho (ex.: 'metas.md', 'aulas/turmas.md').",
       {"caminho": {"type": "string"}}, ["caminho"]),
]


# ---------- lembretes ----------

def _quando(texto):
    t = texto.strip().replace(" ", "T")
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=FUSO)
    return dt


def criar_lembrete(texto, quando, repetir="nao", tarefa_ia=False):
    dt = _quando(quando)
    if dt.timestamp() < time.time() - 60 and repetir == "nao":
        return "esse horario ja passou; confirme a data com o dono"
    repetir = repetir if repetir in ("diario", "semanal") else "nao"
    lid = db.x("INSERT INTO lembretes(texto,quando,repetir,tarefa_ia,criado) VALUES(?,?,?,?,?)",
               (texto.strip()[:1000], dt.timestamp(), repetir, int(bool(tarefa_ia)), time.time()))
    return f"lembrete {lid} criado para {dt:%d/%m/%Y %H:%M}" + (f" ({repetir})" if repetir != "nao" else "")


def listar_lembretes():
    rs = db.q("SELECT * FROM lembretes WHERE ativo=1 ORDER BY quando")
    if not rs:
        return "nenhum lembrete ativo"
    return "\n".join(
        f'{r["id"]}: {datetime.fromtimestamp(r["quando"], FUSO):%d/%m %H:%M} {r["texto"]}'
        + (f' ({r["repetir"]})' if r["repetir"] != "nao" else "") for r in rs)


def apagar_lembrete(id):
    db.x("UPDATE lembretes SET ativo=0 WHERE id=?", (int(id),))
    return f"lembrete {id} apagado"


def proxima(r):
    passo = {"diario": timedelta(days=1), "semanal": timedelta(days=7)}.get(r["repetir"])
    if not passo:
        return None
    dt = datetime.fromtimestamp(r["quando"], FUSO)
    while dt.timestamp() <= time.time():
        dt += passo
    return dt.timestamp()


# ---------- web ----------

class _Texto(HTMLParser):
    PULA = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}

    def __init__(self):
        super().__init__()
        self.partes, self._pula, self.titulo, self._no_titulo = [], 0, "", False

    def handle_starttag(self, tag, a):
        if tag in self.PULA:
            self._pula += 1
        if tag == "title":
            self._no_titulo = True
        if tag in ("p", "br", "li", "h1", "h2", "h3", "tr", "div"):
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in self.PULA and self._pula:
            self._pula -= 1
        if tag == "title":
            self._no_titulo = False

    def handle_data(self, d):
        if self._no_titulo:
            self.titulo += d
        elif not self._pula:
            self.partes.append(d)


def _url_segura(url):
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    try:
        for info in socket.getaddrinfo(u.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False  # nao deixa a IA fuxicar a rede da casa
    except (socket.gaierror, ValueError):
        return False
    return True


async def buscar_web(consulta):
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}, follow_redirects=True) as cli:
        r = await cli.post("https://html.duckduckgo.com/html/", data={"q": consulta, "kl": "br-pt"})
    html = r.text
    blocos = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
                        r'class="result__snippet"[^>]*>(.*?)</(?:a|div)>', html, re.S)
    out = []
    for href, tit, res in blocos[:6]:
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        limpa = lambda s: unescape(re.sub(r"<[^>]+>", "", s)).strip()
        out.append(f"- {limpa(tit)}\n  {href}\n  {limpa(res)}")
    return "\n".join(out) if out else "nenhum resultado (a busca pode ter falhado)"


async def ler_pagina(url):
    if not _url_segura(url):
        return "link nao permitido"
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}, follow_redirects=True) as cli:
        r = await cli.get(url)
    if not _url_segura(str(r.url)):
        return "link nao permitido"
    tipo = r.headers.get("content-type", "")
    if "html" not in tipo and "text" not in tipo:
        return f"a pagina nao e texto ({tipo})"
    p = _Texto()
    p.feed(r.text[:800_000])
    texto = re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", "".join(p.partes))).strip()
    return f"Titulo: {p.titulo.strip()}\n\n{texto[:8000]}"


def data_hora():
    agora = datetime.now(FUSO)
    dias = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]
    return f"{dias[agora.weekday()]}, {agora:%d/%m/%Y %H:%M} (horario de Brasilia)"


async def executar(nome, args):
    try:
        if nome == "anotar_memoria":
            return memoria.anotar_fato(str(args.get("fato", "")))
        if nome == "criar_lembrete":
            return criar_lembrete(str(args.get("texto", "")), str(args.get("quando", "")),
                                  str(args.get("repetir", "nao")), bool(args.get("tarefa_ia", False)))
        if nome == "listar_lembretes":
            return listar_lembretes()
        if nome == "apagar_lembrete":
            return apagar_lembrete(args.get("id"))
        if nome == "buscar_web":
            return await buscar_web(str(args.get("consulta", "")))
        if nome == "ler_pagina":
            return await ler_pagina(str(args.get("url", "")))
        if nome == "data_hora":
            return data_hora()
        if nome == "buscar_cerebro":
            return conhecimento.buscar(str(args.get("termo", "")))
        if nome == "ler_nota":
            return conhecimento.ler(str(args.get("caminho", "")))
        return f"ferramenta desconhecida: {nome}"
    except Exception as ex:  # a IA recebe o erro e explica pro dono
        return f"erro na ferramenta {nome}: {type(ex).__name__}: {str(ex)[:200]}"


def resumo(nome, args):
    """Texto curtinho pra mostrar no chat o que a IA usou."""
    rot = {"anotar_memoria": "Anotei na memoria", "criar_lembrete": "Criei lembrete",
           "listar_lembretes": "Olhei os lembretes", "apagar_lembrete": "Apaguei lembrete",
           "buscar_web": "Pesquisei na web", "ler_pagina": "Li a pagina", "data_hora": "Vi a hora",
           "buscar_cerebro": "Busquei no segundo cerebro", "ler_nota": "Li a nota"}
    det = args.get("fato") or args.get("consulta") or args.get("termo") or args.get("caminho") or args.get("url") or args.get("texto") or ""
    return f'{rot.get(nome, nome)}{": " + str(det)[:80] if det else ""}'


def args_de(tc):
    try:
        return json.loads(tc["function"].get("arguments") or "{}")
    except json.JSONDecodeError:
        return {}
