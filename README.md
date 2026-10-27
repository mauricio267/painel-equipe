# Rapha & Duda

Painel de tarefas do Rapha e da Duda, publicado no GitHub Pages e protegido por senha.
Mostra a produtividade de cada um: entregas do mês, por semana e por dia, % no prazo, atrasos e a fila do que fazer.

**Como conta:**
- **Entregue:** card com status final (concluído, postado, no instagram, aprovado, fechado) ou card que saiu da pasta da pessoa para as pastas de entrega: FERNANDA, ANDERSON, SUMMIT, ALIANÇA ou AH CLUB (lista em `pastas_entrega` no `config.json`). Para esses cards é preciso que a pessoa continue como responsável.
- **Atrasos, fila e "o que fazer":** só os cards que estão na pasta da própria pessoa.
- **Data da entrega:** a data de fechamento no ClickUp. Para cards enviados às pastas de entrega sem data de fechamento, vale a data da última alteração.
Os dados são atualizados a cada 15 minutos, das 8h às 20h, e a página aberta se recarrega sozinha a cada 10 minutos.

## Como funciona

1. O GitHub Actions roda `scripts/build.py`, que lê no ClickUp as pastas **Duda** e **Raphael** e gera a página.
2. O StatiCrypt criptografa a página com a sua senha. Sem a senha, o conteúdo fica ilegível, mesmo olhando o código.
3. A página criptografada é publicada no GitHub Pages.

Os dados do ClickUp nunca ficam salvos no repositório: são buscados e publicados já criptografados a cada execução.

## Passo a passo para colocar no ar

### 1. Criar o token do ClickUp

1. No ClickUp, clique na sua foto (canto superior direito) e abra **Settings**.
2. No menu lateral, abra **Apps**.
3. Em **API Token**, clique em **Generate** e depois em **Copy**. O token começa com `pk_`.
4. Guarde o token por um minuto: você vai colá-lo no GitHub no passo 3. Não mande o token para ninguém.

O token tem o mesmo acesso que a sua conta. Se ele vazar, gere um novo nessa mesma tela, que o antigo deixa de funcionar.

### 2. Criar o repositório e enviar os arquivos

O repositório é criado pelo **GitHub Desktop**, um aplicativo que envia os arquivos sem precisar de terminal.

1. Se ainda não tiver conta, crie uma em [github.com](https://github.com).
2. Instale o GitHub Desktop ([desktop.github.com](https://desktop.github.com)) e entre com a sua conta.
3. **File → Add Local Repository…** e escolha a pasta `ClickUp - Duda` na sua Mesa.
4. Ele avisa "This directory does not appear to be a Git repository". Clique em **create a repository**, troque o nome para `proatividade-equipe` e clique em **Create repository**.
5. Clique em **Publish repository**, **desmarque** "Keep this code private" e confirme.

O repositório precisa ser público porque, no plano gratuito, o GitHub Pages só funciona assim. Os dados continuam protegidos pela senha e nunca ficam salvos no repositório.

Para enviar mudanças no futuro: escreva um resumo em **Summary**, clique em **Commit to main** e depois em **Push origin**.

### 3. Guardar o token e a senha no GitHub

No repositório, abra **Settings → Secrets and variables → Actions → New repository secret** e crie dois segredos:

| Nome | Valor |
|---|---|
| `CLICKUP_TOKEN` | o token do ClickUp (começa com `pk_`) |
| `PAGE_PASSWORD` | a senha que as pessoas vão digitar para ver o painel |

Use uma senha com pelo menos 12 caracteres. É ela que protege os dados.

### 4. Ligar o GitHub Pages

1. No repositório, abra **Settings → Pages**.
2. Em **Source**, escolha **GitHub Actions**.

### 5. Rodar pela primeira vez

1. Abra a aba **Actions**, clique em **Atualizar painel** e depois em **Run workflow**.
2. Em uns 2 minutos aparece um visto verde. O endereço do painel fica em **Settings → Pages**, no formato `https://SEU-USUARIO.github.io/proatividade-equipe/`.

Depois disso ele se atualiza sozinho a cada 15 minutos (o GitHub às vezes atrasa alguns minutos). Para atualizar na hora, repita o passo 5.

## Manutenção

- **Trocar a senha:** edite o segredo `PAGE_PASSWORD` e rode o passo 5. A senha antiga deixa de funcionar.
- **Adicionar uma pessoa:** inclua um bloco em `pessoas` no `config.json` com o nome e o ID da pasta dela no ClickUp.
- **Status que contam como feitos:** edite `status_feitos` no `config.json`.
- **Mudar o horário das atualizações:** edite a linha `cron` em `.github/workflows/atualizar.yml` (o horário é UTC, 3 horas à frente de Brasília).
- **Ver o visual sem dados reais:** `python3 scripts/build.py --demo` gera `dist/index.html` com números de exemplo.
