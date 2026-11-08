"""O agente: monta o prompt (persona + memoria + historico), roda as ferramentas."""
import json
import time

from . import cerebro, conhecimento, db, ferramentas, memoria

MAX_VOLTAS = 5
HISTORICO = 20


def _cerebro():
    mapa = conhecimento.resumo_mapa()
    if not mapa:
        return ""
    return ("\n\n## Segundo cerebro do dono (notas)\n"
            "Para perguntas sobre a vida, rotina, projetos, aulas, clientes, metas, pendencias, pessoas ou "
            "decisoes do dono, consulte com buscar_cerebro e ler_nota antes de responder, e cite a nota "
            "(ex.: 'segundo metas.md'). Algumas notas sao privadas e bloqueadas: nunca tente contornar.\n"
            "Mapa resumido:\n" + mapa)


def _sistema():
    return (memoria.contexto() + _cerebro()
            + f"\n\n## Agora\n{ferramentas.data_hora()}\n\n"
            "## Regras\n"
            "- Use a ferramenta anotar_memoria quando o dono contar algo que vale lembrar.\n"
            "- Para lembrete, calcule a data e hora certas a partir de agora e use criar_lembrete.\n"
            "- Para fato recente, preco, noticia ou algo que voce nao tem certeza, use buscar_web.\n"
            "- Nao invente link nem numero. Responda em portugues do Brasil.")


def _historico(conversa_id):
    rs = db.q("SELECT papel, conteudo FROM mensagens WHERE conversa_id=? "
              "ORDER BY id DESC LIMIT ?", (conversa_id, HISTORICO))
    return [{"role": r["papel"], "content": r["conteudo"]} for r in reversed(rs)
            if r["papel"] in ("user", "assistant") and r["conteudo"]]


async def rodar(msgs):
    """Roda o laco IA -> ferramentas -> IA. Devolve (texto, info, acoes)."""
    acoes = []
    info = {}
    for _ in range(MAX_VOLTAS):
        saida, info = await cerebro.completar(msgs, ferramentas.DEFINICOES)
        if not saida.get("tool_calls"):
            return saida["content"], info, acoes
        msgs.append(saida)
        for tc in saida["tool_calls"]:
            nome = tc["function"]["name"]
            args = ferramentas.args_de(tc)
            res = await ferramentas.executar(nome, args)
            acoes.append(ferramentas.resumo(nome, args))
            msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": str(res)[:9000]})
    # estourou as voltas: pede a resposta final sem ferramentas
    saida, info = await cerebro.completar(msgs + [{"role": "user", "content": "Responda agora com o que ja tem."}])
    return saida["content"], info, acoes


async def conversar(conversa_id, texto, imagem=None):
    agora = time.time()
    hist = _historico(conversa_id)
    db.x("INSERT INTO mensagens(conversa_id,papel,conteudo,imagem,criada) VALUES(?,?,?,?,?)",
         (conversa_id, "user", texto, imagem, agora))
    conteudo = texto
    if imagem:
        conteudo = [{"type": "text", "text": texto or "O que tem nesta imagem?"},
                    {"type": "image_url", "image_url": {"url": imagem}}]
    msgs = [{"role": "system", "content": _sistema()}, *hist, {"role": "user", "content": conteudo}]
    memoria.anotar_diario("dono", texto + (" [imagem]" if imagem else ""))
    try:
        resposta, info, acoes = await rodar(msgs)
    except cerebro.SemIA as ex:
        resposta = ("Nenhuma IA respondeu agora. Confira as chaves na tela Configuracao "
                    "ou espere uns minutos (a cota gratis pode ter acabado).")
        info, acoes = {"rotulo": "nenhuma", "tentativas": ex.args[0]}, []
    info["acoes"] = acoes
    mid = db.x("INSERT INTO mensagens(conversa_id,papel,conteudo,modelo,info,criada) VALUES(?,?,?,?,?,?)",
               (conversa_id, "assistant", resposta, info.get("rotulo"), json.dumps(info, ensure_ascii=False),
                time.time()))
    db.x("UPDATE conversas SET atualizada=? WHERE id=?", (time.time(), conversa_id))
    memoria.anotar_diario("eu", resposta)
    return {"id": mid, "conteudo": resposta, "modelo": info.get("rotulo"), "info": info}


async def tarefa(texto):
    """Pedido agendado (lembrete com tarefa_ia): roda sem historico."""
    msgs = [{"role": "system", "content": _sistema()},
            {"role": "user", "content": f"(tarefa agendada) {texto}"}]
    resposta, info, _ = await rodar(msgs)
    return resposta, info
