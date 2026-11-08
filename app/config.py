"""Caminhos, fuso e leitura/escrita do .env (chaves de API)."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

RAIZ = Path(__file__).resolve().parent.parent
DADOS = Path(os.environ.get("AGENTE_DADOS", RAIZ / "dados"))
ENV_FILE = Path(os.environ.get("AGENTE_ENV", RAIZ / ".env"))
STATIC = RAIZ / "static"
FUSO = ZoneInfo(os.environ.get("AGENTE_FUSO", "America/Sao_Paulo"))
PORTA = int(os.environ.get("AGENTE_PORTA", "8080"))
HOST = os.environ.get("AGENTE_HOST", "0.0.0.0")

# Chaves que a tela de Configuracao pode gravar. So IAs gratis.
CHAVES = {
    "GEMINI_API_KEY": "Gemini (Google AI Studio)",
    "GROQ_API_KEY": "Groq",
    "OPENROUTER_API_KEY": "OpenRouter",
    "CEREBRAS_API_KEY": "Cerebras",
    "MISTRAL_API_KEY": "Mistral",
}
# Outras chaves que a tela grava (nao sao de IA).
CHAVES_EXTRAS = {
    "CEREBRO_GITHUB_TOKEN": "Token do GitHub (segundo cerebro, so leitura)",
}

DADOS.mkdir(parents=True, exist_ok=True)
(DADOS / "diario").mkdir(exist_ok=True)


def _ler_env() -> dict:
    valores = {}
    if ENV_FILE.exists():
        for linha in ENV_FILE.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            k, v = linha.split("=", 1)
            valores[k.strip()] = v.strip().strip('"').strip("'")
    return valores


def carregar_env():
    """Joga o .env no ambiente do processo (o .env ganha do ambiente)."""
    for k, v in _ler_env().items():
        if v:
            os.environ[k] = v


def chave(nome: str) -> str:
    return os.environ.get(nome, "").strip()


def gravar_chave(nome: str, valor: str):
    if nome not in CHAVES and nome not in CHAVES_EXTRAS:
        raise ValueError("chave desconhecida")
    valor = valor.strip().replace("\n", "").replace("\r", "")
    atuais = _ler_env()
    if valor:
        atuais[nome] = valor
        os.environ[nome] = valor
    else:
        atuais.pop(nome, None)
        os.environ.pop(nome, None)
    texto = "# Chaves do agente. Arquivo so deste computador (permissao 600).\n"
    texto += "".join(f"{k}={v}\n" for k, v in atuais.items())
    tmp = ENV_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(texto)
    os.chmod(tmp, 0o600)
    os.replace(tmp, ENV_FILE)


carregar_env()
