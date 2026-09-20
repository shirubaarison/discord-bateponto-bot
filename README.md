# Bate-ponto Discord

Bot de bate-ponto para Discord com PostgreSQL.

## Recursos

- Painel com botões para iniciar, finalizar, pausar/retomar, ver status e histórico.
- Resumo individual com primeiro e último ponto, total acumulado e horas por semana.
- Status individual pelo botão `Status`.
- Aviso privado quando a pessoa sai e volta ao canal de voz permitido.
- Configuração por servidor via comandos slash.
- Canal de auditoria para logs.
- Início e retomada permitidos apenas em canais de voz configurados.
- Tolerância de ausência ajustável. Ao exceder a tolerância, o ponto é encerrado automaticamente.
- Pontos mantidos em pausa por 24 horas são encerrados automaticamente por um job periódico.
- Histórico persistido em PostgreSQL.
- **Dashboard Web em Flask**:
  - Métricas gerais (total de horas trabalhadas, tempo em pausa, média por turno, ausências automáticas).
  - Visualização ao vivo de colaboradores trabalhando no momento.
  - Tabela completa de desempenho por colaborador com filtros de data (Hoje, 7 dias, Este Mês, Customizado).
  - Página individual de histórico e timeline de eventos por ponto.
  - Exportação de dados para CSV com compatibilidade para Excel (UTF-8 BOM).

## Requisitos

- Python 3.11+
- PostgreSQL ou Docker
- Um aplicativo/bot no Discord Developer Portal com token.

O bot usa eventos de servidor e canais de voz. Nenhum privileged intent é necessário para o MVP.

Permissões mínimas do bot nos canais configurados:

- Canal do painel: `View Channel`, `Send Messages` e `Manage Messages`.
- Canal de logs: `View Channel` e `Send Messages`.
- Canais de voz permitidos: `View Channel`.

## Configuração

Copie `.env.example` para `.env` e preencha:

```env
DISCORD_TOKEN=...
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/bate_ponto
```

Durante desenvolvimento, use `DEV_GUILD_ID` para sincronizar comandos slash somente em um servidor:

```env
DEV_GUILD_ID=123456789012345678
```

## Executar

Suba o PostgreSQL local:

```bash
docker compose up -d postgres
```

Rode as migrações:

```bash
uv run alembic upgrade head
```

Depois rode o bot:

```bash
uv run bate-ponto-bot
```

Para rodar o Dashboard Web em Flask:

```bash
uv run bate-ponto-dashboard
```

O dashboard estará disponível em `http://localhost:5000`.

### Login do dashboard

O dashboard não inicia sem usuário, hash de senha e uma chave de sessão segura. Gere a chave:

```bash
openssl rand -hex 32
```

Gere o hash da senha sem deixá-la no histórico do terminal:

```bash
docker compose run --rm dashboard python -c 'from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass("Senha: ")))'
```

Adicione os valores ao `.env`. Mantenha aspas simples ao redor do hash para preservar os caracteres
`$`:

```env
DASHBOARD_SECRET_KEY=resultado_do_openssl
DASHBOARD_USERNAME=Admin
DASHBOARD_PASSWORD_HASH='resultado_do_comando_de_hash'
DASHBOARD_SECURE_COOKIES=true
```

`DASHBOARD_SECURE_COOKIES=true` exige acesso por HTTPS. Use `false` somente no desenvolvimento
local por HTTP. O `.env` não deve ser commitado.

Para rodar tudo junto via Docker Compose:

```bash
docker compose up -d
```

As variáveis de ambiente são validadas com Pydantic na inicialização.

Para parar o banco e containers:

```bash
docker compose down
```

## Qualidade

O projeto usa Ruff para lint e formatação:

```bash
uv run ruff check .
uv run ruff format .
```

Rode os testes com:

```bash
uv run pytest
```

Para apenas verificar formatação sem alterar arquivos:

```bash
uv run ruff format --check .
```

## CI/CD

Todo pull request e push para `main` executa lint, testes e validação do Docker Compose no GitHub
Actions. Um push aprovado em `main` também pode fazer deploy automático na GCP por meio de um
runner auto-hospedado, instalado na própria VM. Assim, o GitHub não precisa guardar uma chave SSH,
nem o servidor precisa expor uma porta SSH à internet.

No GitHub, crie o ambiente `production` em **Settings → Environments** e configure um revisor
obrigatório antes do primeiro deploy. Proteja também a branch `main`, exigindo o check
`Tests and quality` antes de aceitar alterações.

Na VM, crie o runner fora do repositório e use o comando de cadastro temporário exibido em
**Settings → Actions → Runners → New self-hosted runner → Linux x64**:

```bash
mkdir -p /home/alyssonsilva40709/actions-runner
cd /home/alyssonsilva40709/actions-runner
# baixe e extraia o pacote indicado pelo GitHub nesta pasta
./config.sh --url https://github.com/shirubaarison/BatePontoLL --token SEU_TOKEN --labels production
sudo ./svc.sh install alyssonsilva40709
sudo ./svc.sh start
sudo ./svc.sh status
```

O token de cadastro expira rapidamente e não deve ser salvo. O arquivo `.env` de produção continua
somente em `/home/alyssonsilva40709/bot/.env`; a pipeline não o cria nem o substitui. Depois que o
runner aparecer como **Idle** no GitHub, cada push em `main` passa pela aprovação do ambiente e
executa migrações, atualização dos containers e a verificação de saúde do dashboard.

## Comandos

Todos os comandos de configuração exigem permissão `Manage Server`.

- `/config painel canal:#canal`: publica o painel de bate-ponto.
- `/config logs canal:#canal`: define o canal de auditoria.
- `/config voz-adicionar canal:CanalDeVoz`: permite ponto nesse canal de voz.
- `/config voz-remover canal:CanalDeVoz`: remove o canal de voz permitido.
- `/config tolerancia minutos:5`: ajusta a tolerância de ausência.
- `/config limpeza segundos:30`: apaga mensagens comuns do canal do painel após esse tempo.
- `/config mostrar`: mostra a configuração atual.

Use `/config limpeza segundos:0` para desativar a limpeza automática. A mensagem fixa do painel
não é apagada.

Os botões respondem de forma privada para quem clicou. O botão `Histórico` mostra os últimos pontos
finalizados com início, fim, total de cada registro e o total somado da lista exibida.

As mensagens automáticas de ausência são enviadas por DM para a pessoa afetada. O Discord só permite
mensagens privadas dentro do canal quando elas são respostas efêmeras a comandos ou botões.

## Regra de ausência

Quando uma pessoa com ponto ativo sai de um canal de voz permitido, o bot inicia a tolerância
configurada. Se ela voltar antes do prazo, o ponto continua ativo. Se exceder o prazo, o ponto é
encerrado automaticamente no horário limite da tolerância. Se ela voltar depois do encerramento,
o bot avisa a pessoa por DM que um novo ponto precisa ser iniciado.
