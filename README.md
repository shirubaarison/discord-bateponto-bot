# Bate-ponto Discord

Bot de bate-ponto para Discord com PostgreSQL.

## Recursos

- Painel com botões para iniciar, finalizar, pausar/retomar, ver status e histórico.
- Status individual pelo botão `Status`.
- Aviso privado quando a pessoa sai e volta ao canal de voz permitido.
- Configuração por servidor via comandos slash.
- Canal de auditoria para logs.
- Início e retomada permitidos apenas em canais de voz configurados.
- Tolerância de ausência ajustável. Ao exceder a tolerância, o ponto é encerrado automaticamente.
- Histórico persistido em PostgreSQL.

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

As variáveis de ambiente são validadas com Pydantic na inicialização.

Para parar o banco:

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
