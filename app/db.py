"""SQLite: ajustes, sessoes, conversas, mensagens, lembretes, notificacoes."""
import json
import sqlite3
import threading
import time

from .config import DADOS

_lock = threading.RLock()
_con = sqlite3.connect(DADOS / "agente.db", check_same_thread=False)
_con.row_factory = sqlite3.Row
_con.execute("PRAGMA journal_mode=WAL")
_con.executescript("""
CREATE TABLE IF NOT EXISTS ajustes (chave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS sessoes (token TEXT PRIMARY KEY, expira REAL);
CREATE TABLE IF NOT EXISTS conversas (
  id INTEGER PRIMARY KEY, titulo TEXT, criada REAL, atualizada REAL);
CREATE TABLE IF NOT EXISTS mensagens (
  id INTEGER PRIMARY KEY, conversa_id INTEGER, papel TEXT, conteudo TEXT,
  imagem TEXT, modelo TEXT, info TEXT, criada REAL);
CREATE INDEX IF NOT EXISTS ix_msg_conv ON mensagens(conversa_id);
CREATE TABLE IF NOT EXISTS lembretes (
  id INTEGER PRIMARY KEY, texto TEXT, quando REAL, repetir TEXT,
  tarefa_ia INTEGER, ativo INTEGER DEFAULT 1, criado REAL);
CREATE TABLE IF NOT EXISTS notificacoes (
  id INTEGER PRIMARY KEY, titulo TEXT, texto TEXT, criada REAL, lida INTEGER DEFAULT 0);
""")
_con.commit()


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in _con.execute(sql, args).fetchall()]


def x(sql, args=()):
    with _lock:
        cur = _con.execute(sql, args)
        _con.commit()
        return cur.lastrowid


def ajuste(chave, padrao=None):
    r = q("SELECT valor FROM ajustes WHERE chave=?", (chave,))
    return json.loads(r[0]["valor"]) if r else padrao


def set_ajuste(chave, valor):
    x("INSERT INTO ajustes(chave,valor) VALUES(?,?) "
      "ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
      (chave, json.dumps(valor, ensure_ascii=False)))


def notificar(titulo, texto):
    return x("INSERT INTO notificacoes(titulo,texto,criada) VALUES(?,?,?)",
             (titulo, texto, time.time()))
