#!/usr/bin/env bash
# Baixa a versao nova e reinicia. Suas conversas, memoria e chaves ficam.
set -euo pipefail
PASTA="${AGENTE_PASTA:-$HOME/agente-local}"
cd "$PASTA"
echo "Baixando novidades..."
git pull --ff-only -q
venv/bin/pip install -q -r requirements.txt
sudo systemctl restart agente-local
sleep 2
systemctl is-active --quiet agente-local && echo "Pronto! Agente atualizado e rodando." || echo "Algo deu errado. Veja: journalctl -u agente-local -n 50"
