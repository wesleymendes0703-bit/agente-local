#!/usr/bin/env bash
# Instalador do Agente Local. Pode rodar de novo quantas vezes quiser.
# Uso (no notebook, logado com o seu usuario):
#   bash install.sh
set -euo pipefail

REPO_URL="${AGENTE_REPO:-https://github.com/wesleymendes0703-bit/agente-local.git}"
PASTA="${AGENTE_PASTA:-$HOME/agente-local}"
PORTA="${AGENTE_PORTA:-8080}"
USUARIO="$(id -un)"

verde() { printf '\n\033[1;32m%s\033[0m\n' "$*"; }
amarelo() { printf '\n\033[1;33m%s\033[0m\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
  echo "Rode com o seu usuario normal (sem sudo). O script pede a senha quando precisar."
  exit 1
fi

verde "1/5  Instalando o basico (python, git, sqlite)..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates >/dev/null

verde "2/5  Baixando o agente em $PASTA ..."
if [ -d "$PASTA/.git" ]; then
  git -C "$PASTA" pull --ff-only -q || amarelo "Nao consegui baixar novidades agora; sigo com a versao que ja esta aqui."
elif [ -f "$(dirname "$0")/app/main.py" ] && [ "$(cd "$(dirname "$0")" && pwd)" != "$PASTA" ]; then
  mkdir -p "$PASTA" && cp -r "$(dirname "$0")"/. "$PASTA"/
elif [ ! -f "$PASTA/app/main.py" ]; then
  git clone -q "$REPO_URL" "$PASTA"
fi
cd "$PASTA"
python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt
mkdir -p dados
chmod 700 dados
if [ ! -f .env ]; then
  printf '# Chaves do agente. Arquivo so deste computador (permissao 600).\n' > .env
fi
chmod 600 .env

verde "3/5  Ligando o agente como servico (sobe sozinho quando o notebook liga)..."
sudo tee /etc/systemd/system/agente-local.service >/dev/null <<EOF
[Unit]
Description=Agente Local (assistente pessoal)
After=network-online.target
Wants=network-online.target

[Service]
User=$USUARIO
WorkingDirectory=$PASTA
Environment=AGENTE_PORTA=$PORTA
Environment=AGENTE_HOST=0.0.0.0
ExecStart=$PASTA/venv/bin/python -m app.main
Restart=always
RestartSec=5
MemoryMax=600M
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$PASTA

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now agente-local >/dev/null 2>&1
sudo systemctl restart agente-local

verde "4/5  Instalando o Tailscale (acesso de qualquer lugar, sem abrir porta)..."
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
if ! tailscale status >/dev/null 2>&1; then
  amarelo "Abra o link que vai aparecer abaixo (no celular ou PC) e entre com a sua conta."
  sudo tailscale up
fi
# HTTPS de verdade pelo Tailscale (precisa pro microfone e pros avisos no celular)
if timeout 30 sudo tailscale serve --bg --https=443 "http://127.0.0.1:$PORTA" >/dev/null 2>&1; then
  HTTPS_OK=1
else
  HTTPS_OK=0
fi

verde "5/5  Conferindo..."
for _ in $(seq 1 20); do
  curl -fs "http://127.0.0.1:$PORTA/api/estado" >/dev/null && break
  sleep 1
done
if ! curl -fs "http://127.0.0.1:$PORTA/api/estado" >/dev/null; then
  amarelo "O agente nao subiu. Veja o erro com:  journalctl -u agente-local -n 50"
  exit 1
fi

NOME_TS="$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
IP_TS="$(tailscale ip -4 2>/dev/null | head -1 || true)"
IP_LAN="$(hostname -I | awk '{print $1}')"

printf '\n\033[1;44;97m                                                          \033[0m\n'
printf '\033[1;44;97m   PRONTO! ABRA NO CELULAR:                               \033[0m\n'
printf '\033[1;44;97m                                                          \033[0m\n\n'
if [ "$HTTPS_OK" = 1 ] && [ -n "$NOME_TS" ]; then
  printf '   \033[1;32m  https://%s\033[0m\n\n' "$NOME_TS"
  echo "   (funciona em qualquer lugar, com o app Tailscale ligado no celular)"
else
  [ -n "$IP_TS" ] && printf '   \033[1;32m  http://%s:%s\033[0m   (de qualquer lugar, com Tailscale)\n' "$IP_TS" "$PORTA"
  echo
  amarelo "   Para ligar audio e avisos, ative HTTPS no Tailscale:"
  echo "   login.tailscale.com > DNS > ligue MagicDNS e HTTPS Certificates,"
  echo "   depois rode este instalador de novo."
fi
printf '\n   Em casa, no mesmo Wi-Fi:  http://%s:%s\n\n' "$IP_LAN" "$PORTA"
echo "   No celular: abra o endereco > menu do navegador > 'Adicionar a tela inicial'."
echo
