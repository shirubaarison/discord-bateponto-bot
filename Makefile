.DEFAULT_GOAL := help
.PHONY: help install env secret db migrate seed bot dashboard up down logs lint format test check

help: ## Lista os comandos disponíveis
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Instala as dependências (inclui as de desenvolvimento)
	uv sync --all-groups

env: ## Cria o .env a partir do .env.example (se ainda não existir)
	@test -f .env && echo ".env já existe" || (cp .env.example .env && echo ".env criado: preencha DISCORD_TOKEN e as credenciais do dashboard")

secret: ## Gera um valor para DASHBOARD_SECRET_KEY
	@openssl rand -hex 32

db: ## Sobe o PostgreSQL local
	docker compose up -d postgres

migrate: ## Aplica as migrações (use POSTGRES_HOST=localhost ao rodar fora do Docker)
	uv run alembic upgrade head

seed: ## Cria pontos de exemplo: make seed GUILD=<id do servidor> [FUSO=America/Sao_Paulo]
	@test -n "$(GUILD)" || (echo "Informe GUILD=<id do servidor>" && exit 1)
	uv run python scripts/seed_demo.py --guild-id $(GUILD) --fuso $(or $(FUSO),America/Sao_Paulo)

bot: ## Roda o bot localmente
	uv run bate-ponto-bot

dashboard: ## Roda o dashboard localmente (http://localhost:5000)
	uv run bate-ponto-dashboard

up: ## Sobe tudo via Docker Compose (migra o banco antes)
	docker compose up -d postgres
	docker compose run --rm migrate
	docker compose up -d --build bot dashboard

down: ## Para os containers
	docker compose down

logs: ## Acompanha os logs do bot e do dashboard
	docker compose logs -f bot dashboard

lint: ## Roda o Ruff
	uv run ruff check .

format: ## Formata o código
	uv run ruff check --fix .
	uv run ruff format .

test: ## Roda os testes
	uv run pytest -q

check: lint test ## Lint + testes (o mesmo que a CI)
