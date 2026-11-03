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
- Login com Discord: cada pessoa vê apenas os servidores em que tem `Gerenciar Servidor`.
- Super-admins definidos por ID do Discord veem todos os servidores; proteção CSRF e cabeçalhos de segurança.

## Início rápido (Docker)

```bash
git clone <url-do-repositorio> && cd bate-ponto-discord
make env        # cria o .env
make secret     # copie o resultado para DASHBOARD_SECRET_KEY
# edite o .env: DISCORD_TOKEN, POSTGRES_PASSWORD, DISCORD_CLIENT_ID/SECRET/REDIRECT_URI
make up         # sobe banco, migrações, bot e dashboard
```

O dashboard fica em `http://127.0.0.1:5000`. Em produção, coloque-o atrás de um proxy reverso com
HTTPS; sem HTTPS, defina `DASHBOARD_SECURE_COOKIES=false` (apenas para testes locais).

### Criando o bot no Discord

1. Em [Discord Developer Portal](https://discord.com/developers/applications), crie uma aplicação e adicione um Bot.
2. Copie o token para `DISCORD_TOKEN`. Nenhum privileged intent é necessário.
3. Convide o bot com os escopos `bot` e `applications.commands`.
4. Permissões mínimas:
   - Canal do painel: `View Channel`, `Send Messages`, `Manage Messages`.
   - Canal de logs: `View Channel`, `Send Messages`.
   - Canais de voz permitidos: `View Channel`.
5. Para o login com Discord, em **OAuth2** copie `Client ID` e `Client Secret` para
   `DISCORD_CLIENT_ID` e `DISCORD_CLIENT_SECRET` e adicione o redirect
   `https://<seu-dashboard>/auth/discord/callback` (igual a `DISCORD_REDIRECT_URI`).

### Contas do dashboard

Qualquer pessoa pode entrar com a conta do Discord; a conta é criada no primeiro login. O painel
mostra os servidores em que ela é dona, administradora ou tem `Gerenciar Servidor` **e** onde o bot
já foi configurado (`/config ...`). As permissões do Discord são relidas a cada login.
Quem não tem nenhum servidor vê um link para adicionar o bot.

Contas listadas em `DASHBOARD_ADMIN_DISCORD_IDS` são super-admins e veem todos os servidores.

### Configurando no servidor

Ao entrar em um servidor, o bot envia uma mensagem curta com estes passos no canal de sistema
(ou no primeiro canal de texto em que puder escrever).

Todos os comandos exigem a permissão `Manage Server`.

| Comando | Efeito |
| --- | --- |
| `/config painel canal:#canal` | Publica o painel de bate-ponto |
| `/config logs canal:#canal` | Define o canal de auditoria |
| `/config voz-adicionar canal:Canal` | Permite ponto nesse canal de voz |
| `/config voz-remover canal:Canal` | Remove o canal de voz permitido |
| `/config tolerancia minutos:5` | Ajusta a tolerância de ausência |
| `/config fuso fuso:America/Sao_Paulo` | Define o fuso horário do servidor (padrão `America/Fortaleza`) |
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
| `make seed GUILD=<id>` | Cria pontos de exemplo para um servidor (`FUSO=` opcional) |
| `make down` | Para os containers |

### Testando o login com Discord localmente

Roteiro para ver contas, servidores vinculados e fusos horários funcionando na sua máquina.

1. **Aplicação no Discord** ([Developer Portal](https://discord.com/developers/applications)):
   - **Bot**: copie o token e deixe **Public Bot** ligado (para outras pessoas poderem convidá-lo).
   - **OAuth2**: copie `Client ID` e `Client Secret` e adicione o redirect
     `http://localhost:5000/auth/discord/callback`.
2. **`.env`** (`make env` e depois ajuste):

   ```env
   DISCORD_TOKEN=<token do bot>
   DISCORD_CLIENT_ID=<client id>
   DISCORD_CLIENT_SECRET=<client secret>
   DISCORD_REDIRECT_URI=http://localhost:5000/auth/discord/callback
   POSTGRES_HOST=localhost
   DASHBOARD_SECRET_KEY=<saída de make secret>
   DASHBOARD_SECURE_COOKIES=false
   DASHBOARD_PROXY_FIX=false
   # Opcional: comandos slash aparecem na hora neste servidor
   DEV_GUILD_ID=<id do seu servidor de testes>
   ```
3. **Suba tudo**: `make install`, `make db`, `make migrate` e, em dois terminais, `make bot` e
   `make dashboard`.
4. **Configure o servidor de testes** (em que você tem `Gerenciar Servidor`): convide o bot,
   rode `/config painel`, `/config voz-adicionar` e `/config fuso`. Sem nenhum servidor, o
   dashboard mostra o link de convite.
5. **Entre** em `http://localhost:5000` com **Entrar com Discord**.
6. **Dados de exemplo** (opcional): `make seed GUILD=<id do servidor> FUSO=Asia/Tokyo` cria
   três semanas de pontos com início entre 8h e 10h no fuso escolhido e uma pessoa trabalhando
   agora. `uv run python scripts/seed_demo.py --guild-id <id> --limpar` remove esses dados.

O que conferir:

- Com dois servidores com fusos diferentes, os horários de cada um aparecem no fuso dele (os
  pontos de exemplo começam às 8h–10h locais), e os filtros **Hoje** e **Este Mês** seguem esse
  fuso.
- Uma conta sem `Gerenciar Servidor` (outra conta sua ou de alguém) não vê o servidor, e
  `/?guild_id=<id de outro servidor>` responde 404.
- Depois de mudar permissões no Discord, saia e entre de novo para recarregar a lista.

`DEV_GUILD_ID` sincroniza os comandos apenas nesse servidor; para testar `/config` em um
segundo servidor, deixe-o vazio (sincronização global).

## Configuração

Toda a configuração vem de variáveis de ambiente (arquivo `.env`), validadas com Pydantic na
inicialização. O [`.env.example`](.env.example) documenta cada uma.

| Variável | Descrição |
| --- | --- |
| `DISCORD_TOKEN` | Token do bot (obrigatório) |
| `POSTGRES_*` / `DATABASE_URL` | Conexão com o banco. `POSTGRES_HOST` tem prioridade sobre `DATABASE_URL` |
| `DASHBOARD_SECRET_KEY` | Chave de sessão, com 32+ caracteres (obrigatória) |
| `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` | Login com Discord (OAuth2, obrigatório) |
| `DISCORD_REDIRECT_URI` | URL de retorno do OAuth2 cadastrada no Developer Portal |
| `DASHBOARD_URL` | Endereço público do dashboard, citado na mensagem de boas-vindas (opcional) |
| `DASHBOARD_ADMIN_DISCORD_IDS` | IDs de usuário do Discord, separados por vírgula, que veem todos os servidores |
| `DASHBOARD_SECURE_COOKIES` | `true` exige HTTPS |
| `DASHBOARD_PROXY_FIX` | `true` quando atrás de proxy reverso |
| `DEV_GUILD_ID` | Sincroniza comandos slash em um único servidor |

O dashboard recusa iniciar sem uma chave de sessão segura e sem `DISCORD_CLIENT_ID` e
`DISCORD_CLIENT_SECRET`.

## Estrutura

```
bate_ponto_bot/
├── bot.py, commands.py, views.py   # bot, comandos slash e botões
├── db.py, models.py                # acesso ao PostgreSQL (asyncpg)
└── web/                            # dashboard Flask (app, queries, CSV, templates)
migrations/                         # Alembic
tests/                              # pytest
scripts/seed_demo.py                # pontos de exemplo para testar o dashboard
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
