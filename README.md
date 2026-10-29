# Bate-Ponto Discord

Controle de ponto direto no Discord, com painel web para acompanhar as horas da equipe.

Python 3.11+ · discord.py · PostgreSQL · Flask · Docker Compose

## O que faz

**Bot**

- Painel com botões para iniciar, finalizar, pausar/retomar, ver status e histórico (respostas privadas).
- Início e retomada permitidos apenas em canais de voz configurados.
- Tolerância de ausência ajustável: se a pessoa sai do canal de voz e não volta a tempo, o ponto é encerrado no horário limite e ela recebe um aviso por DM.
- Pontos ativos ou em pausa por mais de 24 horas são encerrados automaticamente.
- Canal de auditoria para logs e configuração por servidor via comandos slash.

**Dashboard web**

- Métricas gerais, colaboradores trabalhando agora e desempenho por pessoa, com filtros de período.
- Histórico individual e linha do tempo de eventos de cada ponto.
- Exportação para CSV compatível com Excel (UTF-8 BOM).
- Login com senha em hash, proteção CSRF, limite de tentativas e cabeçalhos de segurança.

## Início rápido (Docker)

```bash
git clone <url-do-repositorio> && cd bate-ponto-discord
make env        # cria o .env
make secret     # copie o resultado para DASHBOARD_SECRET_KEY
make hash       # copie a linha gerada para o .env
# edite o .env: DISCORD_TOKEN, POSTGRES_PASSWORD, DASHBOARD_USERNAME
make up         # sobe banco, migrações, bot e dashboard
```

O dashboard fica em `http://127.0.0.1:5000`. Em produção, coloque-o atrás de um proxy reverso com
HTTPS; sem HTTPS, defina `DASHBOARD_SECURE_COOKIES=false` (apenas para testes locais).

> `make hash` usa `uv`. Sem ele, gere o hash com
> `python -c 'from getpass import getpass; from werkzeug.security import generate_password_hash as g; print(g(getpass()))'`
> e mantenha aspas simples ao redor do valor no `.env` por causa dos `$`.

### Criando o bot no Discord

1. Em [Discord Developer Portal](https://discord.com/developers/applications), crie uma aplicação e adicione um Bot.
2. Copie o token para `DISCORD_TOKEN`. Nenhum privileged intent é necessário.
3. Convide o bot com os escopos `bot` e `applications.commands`.
4. Permissões mínimas:
   - Canal do painel: `View Channel`, `Send Messages`, `Manage Messages`.
   - Canal de logs: `View Channel`, `Send Messages`.
   - Canais de voz permitidos: `View Channel`.

### Configurando no servidor

Todos os comandos exigem a permissão `Manage Server`.

| Comando | Efeito |
| --- | --- |
| `/config painel canal:#canal` | Publica o painel de bate-ponto |
| `/config logs canal:#canal` | Define o canal de auditoria |
| `/config voz-adicionar canal:Canal` | Permite ponto nesse canal de voz |
| `/config voz-remover canal:Canal` | Remove o canal de voz permitido |
| `/config tolerancia minutos:5` | Ajusta a tolerância de ausência |
| `/config limpeza segundos:30` | Apaga mensagens comuns do painel após esse tempo (`0` desativa) |
| `/config mostrar` | Mostra a configuração atual |

## Desenvolvimento local

Requer [uv](https://docs.astral.sh/uv/) e Docker (para o PostgreSQL).

```bash
make install
make env            # e ajuste o .env: POSTGRES_HOST=localhost
make db             # PostgreSQL via Docker
make migrate
make bot            # em um terminal
make dashboard      # em outro (http://localhost:5000)
```

Use `DEV_GUILD_ID` para sincronizar os comandos slash só no seu servidor de testes.
`make help` lista todos os alvos.

| Alvo | Descrição |
| --- | --- |
| `make check` | Lint + testes (o mesmo que a CI) |
| `make test` | Testes (`pytest`) |
| `make lint` / `make format` | Ruff |
| `make logs` | Logs dos containers |
| `make down` | Para os containers |

## Configuração

Toda a configuração vem de variáveis de ambiente (arquivo `.env`), validadas com Pydantic na
inicialização. O [`.env.example`](.env.example) documenta cada uma.

| Variável | Descrição |
| --- | --- |
| `DISCORD_TOKEN` | Token do bot (obrigatório) |
| `POSTGRES_*` / `DATABASE_URL` | Conexão com o banco. `POSTGRES_HOST` tem prioridade sobre `DATABASE_URL` |
| `DASHBOARD_SECRET_KEY` | Chave de sessão, com 32+ caracteres (obrigatória) |
| `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD_HASH` | Credenciais do dashboard (obrigatórias) |
| `DASHBOARD_SECURE_COOKIES` | `true` exige HTTPS |
| `DASHBOARD_PROXY_FIX` | `true` quando atrás de proxy reverso |
| `DEV_GUILD_ID` | Sincroniza comandos slash em um único servidor |

O dashboard recusa iniciar sem usuário, hash de senha e uma chave de sessão segura.

## Estrutura

```
bate_ponto_bot/
├── bot.py, commands.py, views.py   # bot, comandos slash e botões
├── db.py, models.py                # acesso ao PostgreSQL (asyncpg)
└── web/                            # dashboard Flask (app, queries, CSV, templates)
migrations/                         # Alembic
tests/                              # pytest
scripts/hash_password.py            # gerador do hash de senha
```

## CI/CD

Pull requests e pushes em `main` rodam lint, testes e validação do Docker Compose. Um push em
`main` pode também fazer deploy por um runner auto-hospedado instalado na VM, sem guardar chave
SSH no GitHub nem expor porta SSH.

1. Crie o ambiente `production` em **Settings → Environments**, com revisor obrigatório, e proteja a `main` exigindo o check `Tests and quality`.
2. Em **Settings → Secrets and variables → Actions → Variables**, defina:
   - `DEPLOY_PATH`: diretório do projeto na VM (por exemplo `/opt/bate-ponto`);
   - `PRODUCTION_URL`: URL pública do dashboard.
3. Na VM, registre o runner com o label `production` (**Settings → Actions → Runners → New self-hosted runner**) e instale-o como serviço (`sudo ./svc.sh install && sudo ./svc.sh start`).
4. Mantenha o `.env` de produção apenas na VM, dentro de `DEPLOY_PATH`; a pipeline nunca o cria nem o altera.

O deploy executa build, migrações, atualização dos containers e verifica `/healthz`.

## Regra de ausência

Quando alguém com ponto ativo sai de um canal de voz permitido, começa a tolerância configurada.
Se voltar a tempo, o ponto continua. Se não, ele é encerrado no horário limite da tolerância; ao
voltar depois disso, a pessoa recebe uma DM avisando que precisa iniciar um novo ponto. (O Discord
só permite mensagens privadas no canal como respostas efêmeras a comandos ou botões, por isso os
avisos automáticos vão por DM.)
