"""Segundo cerebro: copia SO LEITURA de um repositorio GitHub de notas em markdown.

- Sincroniza com git (clone raso + sparse-checkout). O token vai por variavel de
  ambiente do git (header HTTP), nunca no .git/config, na linha de comando ou no log.
- Arquivos e pastas EXCLUIDOS nem descem pro disco (sparse-checkout) e, se algum
  estiver la, e apagado. Alem disso, toda leitura passa por permitido().
- O agente nunca faz commit nem push.
"""
import base64
import os
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path

from . import db
from .config import DADOS, chave

PASTA = Path(os.environ.get("AGENTE_CEREBRO_DIR", DADOS / "cerebro"))
ENV_TOKEN = "CEREBRO_GITHUB_TOKEN"
REPO_PADRAO = "wesleymendes0703-bit/segundo-cerebro"
EXCLUIDOS_PADRAO = ["financas/", "pessoal/saude.md"]
EXTENSOES = {".md", ".txt"}
INTERVALO = 60 * 60
MAX_NOTA = 12000
MAX_ARQUIVO = 400_000  # arquivos maiores que isso sao ignorados na busca


# ---------- ajustes ----------

def repo():
    return db.ajuste("cerebro_repo", REPO_PADRAO)


def excluidos():
    return db.ajuste("cerebro_excluidos", list(EXCLUIDOS_PADRAO))


def _normalizar_padrao(p):
    p = p.strip().replace("\\", "/").lstrip("/")
    while p.startswith("./"):
        p = p[2:]
    return p


def salvar(novo_repo=None, novos_excluidos=None):
    if novo_repo is not None:
        novo_repo = novo_repo.strip().removeprefix("https://github.com/").removesuffix(".git").strip("/")
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", novo_repo):
            raise ValueError("repositorio invalido (use dono/nome)")
        if novo_repo != repo() and (PASTA / ".git").exists():
            shutil.rmtree(PASTA)  # trocou de repo: comeca do zero
        db.set_ajuste("cerebro_repo", novo_repo)
    if novos_excluidos is not None:
        lista = []
        for p in novos_excluidos:
            p = _normalizar_padrao(p)
            if p and ".." not in p.split("/") and p not in lista:
                lista.append(p)
        db.set_ajuste("cerebro_excluidos", lista)
        aplicar_exclusoes()


# ---------- bloqueio ----------

def _bloqueado(rel: str) -> bool:
    r = rel.lower()
    for p in excluidos():
        p = p.lower()
        if p.endswith("/"):
            if r.startswith(p) or r == p.rstrip("/"):
                return True
        elif r == p or r.startswith(p + "/"):
            return True
    return False


def permitido(caminho: str):
    """Devolve o Path seguro do arquivo, ou None se nao pode ser lido."""
    rel = _normalizar_padrao(str(caminho))
    partes = rel.split("/")
    if not rel or any(x in ("", ".", "..") or x.startswith(".") for x in partes):
        return None
    if _bloqueado(rel):
        return None
    raiz = PASTA.resolve()
    alvo = (PASTA / rel)
    try:
        real = alvo.resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError):
        return None
    if raiz not in real.parents:  # symlink pra fora
        return None
    rel_real = real.relative_to(raiz).as_posix()
    if _bloqueado(rel_real) or real.suffix.lower() not in EXTENSOES or not real.is_file():
        return None
    return real


def arquivos():
    """Todos os arquivos legiveis (ja sem excluidos), caminho relativo."""
    if not PASTA.exists():
        return []
    out = []
    for raiz, pastas, nomes in os.walk(PASTA):
        pastas[:] = sorted(d for d in pastas if not d.startswith("."))
        for n in sorted(nomes):
            rel = (Path(raiz) / n).relative_to(PASTA).as_posix()
            if permitido(rel):
                out.append(rel)
    return out


def aplicar_exclusoes():
    """Apaga do disco o que estiver excluido e atualiza o sparse-checkout."""
    if not PASTA.exists():
        return
    for raiz, pastas, nomes in os.walk(PASTA, topdown=True):
        pastas[:] = [d for d in pastas if d != ".git"]
        for d in list(pastas):
            rel = (Path(raiz) / d).relative_to(PASTA).as_posix()
            if _bloqueado(rel + "/"):
                shutil.rmtree(Path(raiz) / d, ignore_errors=True)
                pastas.remove(d)
        for n in nomes:
            rel = (Path(raiz) / n).relative_to(PASTA).as_posix()
            if _bloqueado(rel):
                (Path(raiz) / n).unlink(missing_ok=True)
    if (PASTA / ".git").exists():
        _git(["sparse-checkout", "set", "--no-cone", *_padroes_sparse()], cwd=PASTA)


def _padroes_sparse():
    out = ["/*"]
    for p in excluidos():
        out.append("!/" + p)
    return out


# ---------- git ----------

def _ambiente_git():
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and k != ENV_TOKEN}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "true"
    tok = chave(ENV_TOKEN)
    if tok:
        b = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {b}"
    return env


def _limpar_saida(txt):
    tok = chave(ENV_TOKEN)
    if tok:
        txt = txt.replace(tok, "***")
    return re.sub(r"(Basic|Bearer) [A-Za-z0-9+/=_-]+", r"\1 ***", txt)[-400:]


def _git(args, cwd=None):
    r = subprocess.run(["git", *args], cwd=cwd, env=_ambiente_git(), capture_output=True,
                       text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(_limpar_saida(r.stderr or r.stdout).strip() or f"git {args[0]} falhou")
    return r.stdout


def sincronizar():
    """Baixa/atualiza a copia local. Devolve texto curto com o resultado."""
    url = f"https://github.com/{repo()}.git"
    try:
        if not (PASTA / ".git").exists():
            if PASTA.exists():
                shutil.rmtree(PASTA)
            PASTA.parent.mkdir(parents=True, exist_ok=True)
            _git(["clone", "--depth", "1", "--no-checkout", "--filter=blob:none", url, str(PASTA)])
            _git(["sparse-checkout", "set", "--no-cone", *_padroes_sparse()], cwd=PASTA)
            _git(["checkout"], cwd=PASTA)
        else:
            _git(["remote", "set-url", "origin", url], cwd=PASTA)
            _git(["sparse-checkout", "set", "--no-cone", *_padroes_sparse()], cwd=PASTA)
            _git(["fetch", "--depth", "1", "origin"], cwd=PASTA)
            _git(["reset", "--hard", "FETCH_HEAD"], cwd=PASTA)
        aplicar_exclusoes()
        n = len(arquivos())
        db.set_ajuste("cerebro_sync", {"quando": time.time(), "ok": True, "msg": f"{n} notas"})
        return True, f"{n} notas sincronizadas"
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as ex:
        msg = _limpar_saida(str(ex))
        if "could not read Username" in msg or "Authentication failed" in msg or "not found" in msg.lower():
            msg = "o GitHub recusou: confira o token e o nome do repositorio"
        db.set_ajuste("cerebro_sync", {"quando": time.time(), "ok": False, "msg": msg})
        return False, msg


def precisa_sincronizar():
    s = db.ajuste("cerebro_sync") or {}
    return bool(repo()) and time.time() - s.get("quando", 0) >= INTERVALO


def estado():
    arqs = arquivos()
    pastas = sorted({a.split("/")[0] + "/" for a in arqs if "/" in a})
    return {"repo": repo(), "excluidos": excluidos(), "tem_token": bool(chave(ENV_TOKEN)),
            "sync": db.ajuste("cerebro_sync"), "total": len(arqs), "pastas": pastas,
            "soltos": [a for a in arqs if "/" not in a]}


# ---------- busca e leitura ----------

def _sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


PARADAS = set("a o e de da do das dos que em no na nos nas um uma pra para por com sem meu minha meus "
              "minhas sao tem qual quais como onde quando eu voce ele ela isso esse essa ao aos se".split())


def buscar(termo: str, limite=8) -> str:
    if not PASTA.exists():
        return "o segundo cerebro ainda nao foi sincronizado"
    palavras = [p for p in re.findall(r"\w+", _sem_acento(termo)) if len(p) >= 3 and p not in PARADAS]
    if not palavras:
        return "diga uma palavra pra buscar"
    achados = []
    for rel in arquivos():
        arq = permitido(rel)
        if not arq or arq.stat().st_size > MAX_ARQUIVO:
            continue
        texto = arq.read_text(encoding="utf-8", errors="replace")
        linhas = texto.splitlines()
        norm = [_sem_acento(l) for l in linhas]
        nome = _sem_acento(rel)
        pontos = sum(3 for p in palavras if p in nome)
        trechos = []
        for i, l in enumerate(norm):
            n = sum(1 for p in palavras if p in l)
            if n:
                pontos += n
                if len(trechos) < 3:
                    trechos.append(" ".join(x.strip() for x in linhas[max(0, i - 1):i + 2] if x.strip())[:300])
        if pontos:
            if not trechos:
                trechos = [" ".join(x.strip() for x in linhas[:3] if x.strip())[:300]]
            achados.append((pontos, rel, trechos))
    if not achados:
        return f"nada encontrado no segundo cerebro para: {termo}"
    achados.sort(key=lambda a: -a[0])
    out = []
    for _, rel, trechos in achados[:limite]:
        out.append(f"[{rel}]\n" + "\n".join(f"  ... {t}" for t in trechos))
    return "\n".join(out)[:5000]


def ler(caminho: str) -> str:
    rel = _normalizar_padrao(str(caminho))
    if rel and _bloqueado(rel):
        return "essa nota e privada e esta bloqueada por decisao do dono; nao posso ler"
    arq = permitido(rel)
    if not arq:
        return "nota nao encontrada (use buscar_cerebro pra achar o caminho certo)"
    texto = arq.read_text(encoding="utf-8", errors="replace")
    if len(texto) > MAX_NOTA:
        texto = texto[:MAX_NOTA] + "\n(... nota cortada, muito longa)"
    return f"[{rel}]\n{texto}"


def resumo_mapa(limite=1500) -> str:
    """mapa.md sem nenhuma linha que cite algo excluido, pra ir no prompt."""
    arq = permitido("mapa.md")
    if not arq:
        return ""
    chaves = set()
    for p in excluidos():
        p = p.rstrip("/").lower()
        chaves.add(p)
        chaves.add(Path(p).name)
        chaves.add(Path(p).stem)
    chaves = {_sem_acento(c) for c in chaves if len(c) >= 3}
    linhas = [l for l in arq.read_text(encoding="utf-8", errors="replace").splitlines()
              if l.strip() and not any(c in _sem_acento(l) for c in chaves)]
    return "\n".join(linhas)[:limite]
