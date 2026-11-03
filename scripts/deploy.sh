#!/usr/bin/env bash
# Atualiza e reinicia a aplicação na VM.
#
# O GitHub Actions envia este script por SSH:
#   ssh <vm> 'bash -s -- <diretório do projeto>' < scripts/deploy.sh
# Também pode ser rodado direto na VM:
#   bash scripts/deploy.sh /caminho/do/projeto
set -euo pipefail

deploy_path="${1:?Uso: deploy.sh <diretório do projeto na VM>}"
cd "$deploy_path"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "$deploy_path não é um clone git deste repositório." >&2
  exit 1
fi

git fetch origin main
# Match origin/main exactly, so a rewritten history cannot block deploys.
# The production .env is git-ignored and is left untouched.
git checkout -B main origin/main

docker compose build bot dashboard migrate
docker compose up -d postgres
docker compose run --rm migrate
docker compose up -d bot dashboard
curl --fail --silent --show-error --retry 10 --retry-all-errors --retry-delay 2 \
  http://127.0.0.1:5000/healthz
echo
echo "Deploy concluído: $(git log --oneline -1)"
