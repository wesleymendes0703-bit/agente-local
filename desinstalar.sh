#!/usr/bin/env bash
# Desliga e remove o servico. Pergunta antes de apagar suas conversas e memoria.
set -euo pipefail
PASTA="${AGENTE_PASTA:-$HOME/agente-local}"
sudo systemctl disable --now agente-local 2>/dev/null || true
sudo rm -f /etc/systemd/system/agente-local.service
sudo systemctl daemon-reload
sudo tailscale serve --https=443 off 2>/dev/null || true
echo "Servico removido."
read -r -p "Apagar TAMBEM conversas, memoria e chaves em $PASTA? Isso nao tem volta. (digite SIM): " r
if [ "$r" = "SIM" ]; then
  rm -rf "$PASTA"
  echo "Tudo apagado."
else
  echo "Seus dados ficaram em $PASTA/dados (e as chaves em $PASTA/.env)."
fi
echo "O Tailscale continua instalado. Para tirar: sudo apt remove tailscale"
