"""Memoria em arquivos: persona.md, memoria.md e diario/AAAA-MM-DD.md."""
from datetime import datetime

from .config import DADOS, FUSO

PERSONA = DADOS / "persona.md"
MEMORIA = DADOS / "memoria.md"
DIARIO = DADOS / "diario"
LIMITE_MEMORIA = 12000  # caracteres que entram no prompt

PERSONA_PADRAO = """# Quem eu sou

- Meu nome: {nome}
- Meu dono: {dono}
- Jeito: animado, direto e carinhoso. Falo portugues do Brasil simples.
- Mensagens curtas, um passo por vez, uma pergunta so.
- Nao uso travessao no meio da frase.
- Quando nao sei, digo que nao sei. Nunca invento fato, numero ou link.
"""

MEMORIA_PADRAO = """# O que eu sei (fatos que valem pra sempre)

"""


def agora():
    return datetime.now(FUSO)


def iniciar(nome="Levi", dono="meu dono", forcar=False):
    if forcar or not PERSONA.exists():
        PERSONA.write_text(PERSONA_PADRAO.format(nome=nome, dono=dono), encoding="utf-8")
    if not MEMORIA.exists():
        MEMORIA.write_text(MEMORIA_PADRAO, encoding="utf-8")


def ler(arq):
    return arq.read_text(encoding="utf-8") if arq.exists() else ""


def gravar(arq, texto):
    arq.write_text(texto.replace("\r\n", "\n"), encoding="utf-8")


def anotar_fato(fato: str) -> str:
    fato = " ".join(fato.split())[:500]
    if not fato:
        return "nada pra anotar"
    linha = f"- ({agora():%d/%m/%Y}) {fato}\n"
    texto = ler(MEMORIA) or MEMORIA_PADRAO
    if not texto.endswith("\n"):
        texto += "\n"
    gravar(MEMORIA, texto + linha)
    return "anotado na memoria"


def diario_hoje():
    return DIARIO / f"{agora():%Y-%m-%d}.md"


def anotar_diario(quem: str, texto: str):
    arq = diario_hoje()
    resumo = " ".join(texto.split())[:240]
    novo = not arq.exists()
    with arq.open("a", encoding="utf-8") as f:
        if novo:
            f.write(f"# Diario {agora():%d/%m/%Y}\n\n")
        f.write(f"- {agora():%H:%M} {quem}: {resumo}\n")


def dias_do_diario():
    return sorted((p.stem for p in DIARIO.glob("*.md")), reverse=True)


def contexto() -> str:
    mem = ler(MEMORIA)
    if len(mem) > LIMITE_MEMORIA:
        mem = "(...)\n" + mem[-LIMITE_MEMORIA:]
    return ler(PERSONA) + "\n\n" + mem
