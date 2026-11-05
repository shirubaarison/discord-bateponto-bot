# Deploy

A produção roda em uma única VM com Docker Compose. A VM não expõe nenhuma porta: o dashboard e o
SSH do deploy passam por um Cloudflare Tunnel, e o acesso SSH é liberado só por um service token do
Cloudflare Access.

A cada push na `main`, depois que o job `Tests and quality` passa, o GitHub Actions:

1. instala o `cloudflared` numa versão fixa, conferindo o checksum;
2. conecta por SSH através do túnel (`cloudflared access ssh`), com a chave do host fixada;
3. roda [`scripts/deploy.sh`](../scripts/deploy.sh) na VM, que faz fetch, build, migrações,
   reinicia os containers e verifica o `/healthz`.

O `.env` de produção fica só na VM, dentro de `DEPLOY_PATH`. A pipeline nunca o cria nem o altera.

## Configuração

1. **Ambiente no GitHub.** Crie o ambiente `production` em **Settings → Environments**. Exija um
   revisor, limite os branches de deploy à `main` e proteja a `main` exigindo o check
   `Tests and quality`.

2. **Chave de deploy na VM.** Crie uma chave só para o deploy e autorize-a no usuário que roda o
   Docker:

   ```bash
   ssh-keygen -t ed25519 -f ~/github-deploy -N "" -C github-deploy
   cat ~/github-deploy.pub >> ~/.ssh/authorized_keys
   cat ~/github-deploy            # conteúdo de DEPLOY_SSH_KEY
   rm ~/github-deploy ~/github-deploy.pub
   echo "ssh.<seu-domínio> $(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)"   # DEPLOY_KNOWN_HOSTS
   ```

   No GCP, se a VM usa OS Login (`enable-oslogin=TRUE`), o `authorized_keys` é ignorado: cadastre a
   chave pública nos metadados da instância ou desative o OS Login.

3. **Cloudflare Zero Trust.**
   - **Networks → Tunnels →** seu túnel **→ Public Hostname:** adicione `ssh.<seu-domínio>` com o
     serviço `SSH` em `localhost:22`. Se o túnel for configurado por arquivo, inclua
     `- hostname: ssh.<seu-domínio>` / `service: ssh://localhost:22` no `ingress` antes da regra
     final e reinicie o `cloudflared`.
   - **Access → Service Auth → Service Tokens:** crie um token (por exemplo `github-deploy`) e
     guarde o Client ID e o Client Secret.
   - **Access → Applications:** crie uma aplicação *Self-hosted* para `ssh.<seu-domínio>` com uma
     política de ação **Service Auth** que inclua esse token. Sem ela, o hostname do SSH fica
     acessível para qualquer um pela Cloudflare.

4. **Secrets e variáveis do ambiente.** Em **Settings → Environments → production**, cadastre:
   - **Secrets:** `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`, `DEPLOY_HOST` (`ssh.<seu-domínio>`),
     `DEPLOY_USER`, `TUNNEL_SERVICE_TOKEN_ID` (Client ID) e `TUNNEL_SERVICE_TOKEN_SECRET`
     (Client Secret)
   - **Variables:** `DEPLOY_PATH` (clone do projeto na VM, por exemplo `/opt/bate-ponto`) e
     `PRODUCTION_URL`

5. **`.env` de produção.** Crie-o na VM, dentro de `DEPLOY_PATH` (veja o
   [`.env.example`](../.env.example)), com `DASHBOARD_SECURE_COOKIES=true` e
   `DASHBOARD_PROXY_FIX=true`.

Para um deploy manual, rode `bash scripts/deploy.sh <DEPLOY_PATH>` na VM.
