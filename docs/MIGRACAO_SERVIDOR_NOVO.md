# Migração para o servidor novo — passo a passo

Cenário deste roteiro: **instalação sem root**, código vindo **direto do
GitHub**, tudo num **processo só na porta 8901**.

Nada aqui é destrutivo no servidor antigo — ele continua no ar o tempo todo.
A virada só acontece quando você trocar o endereço que as pessoas usam.

| | Servidor **antigo** | Servidor **novo** |
|---|---|---|
| O que fazer | gerar o pacote de dados e copiar | clonar, instalar, restaurar, subir |
| Fica fora do ar? | não | — |
| Precisa de root? | não | não |

---

## PARTE 1 — No servidor ANTIGO

### 1.1 Atualizar o código (para ter os scripts novos)

```bash
cd /opt/portal-spare-v2
env -u https_proxy -u http_proxy -u HTTPS_PROXY -u HTTP_PROXY \
    git pull --ff-only origin main
```

> No servidor **antigo** o proxy corporativo quebra o `git`, por isso o
> `env -u ...`. No servidor **novo** isso não é preciso: a saída é direta.

### 1.2 Gerar o pacote com TUDO

```bash
bash scripts/backup.sh
```

Sai um arquivo assim, com Postgres + todos os SQLite + uploads + dados de
referência + o arquivo de ambiente + o commit que está em produção:

```
~/backups-portal-spare/portal-spare-AAAAMMDD-HHMMSS.tar.gz
```

Confira o tamanho — se vier com poucos KB, algo não entrou:

```bash
bash scripts/backup.sh --listar
```

O script agora mostra o erro do `pg_dump` na hora e, no fim, avisa em
destaque se o pacote saiu **sem** o banco do portal. Um pacote sem o Postgres
não serve para migrar.

Se o `pg_dump` falhar, os motivos usuais são:

| Mensagem | Causa | O que fazer |
|---|---|---|
| `connection to server on socket ".s.PGSQL.5432" failed` | a URL não foi entendida e ele tentou o banco local | atualize o código: o script normaliza `postgresql+psycopg2://` sozinho |
| `server version X; pg_dump version Y` | cliente mais antigo que o servidor | instale o `postgresql-client` da mesma versão do servidor |
| `password authentication failed` | credencial errada no `DATABASE_URL` | confira o `environment` |
| `pg_dump: command not found` | cliente não instalado | `sudo apt install postgresql-client` |

Se não der para instalar o cliente nesse servidor, gere o dump de outra
máquina que alcance o banco:

```bash
set -a; . /etc/portal_operacoes_spare/environment; set +a
pg_dump -Fc --no-owner --dbname="$DATABASE_URL" -f ~/portal_postgres.dump
```

### 1.3 Copiar para o servidor novo

```bash
scp ~/backups-portal-spare/portal-spare-*.tar.gz* SEU_USUARIO@SERVIDOR_NOVO:~/
```

Sem rota direta entre os dois, baixe para a sua máquina e suba de lá. O
`.sha256` que acompanha serve para provar que o arquivo chegou inteiro.

**Pronto. O servidor antigo não precisa de mais nada** — e segue no ar.

---

## PARTE 2 — No servidor NOVO

### 2.1 Conferir os pré-requisitos

```bash
python3 --version          # precisa ser 3.10 ou maior
python3 -m venv --help     # se falhar, falta o pacote python3-venv
git --version
```

Faltando algum, é o único momento em que você precisa de alguém com root.

### 2.2 Trazer o código do GitHub

O caminho no servidor novo é **`/var/www/vcreports/portal-spare`**.

```bash
sudo mkdir -p /var/www/vcreports && sudo chown $USER /var/www/vcreports   # se ainda não existir
cd /var/www/vcreports
git clone https://github.com/ghost2539/CLAUDE.git portal-spare
cd /var/www/vcreports/portal-spare
```

> Este servidor sai direto para a rede — **não precisa de proxy**. Se o
> `git` reclamar de proxy herdado do ambiente, limpe só para o comando:
> `env -u https_proxy -u http_proxy -u HTTPS_PROXY -u HTTP_PROXY git clone ...`

Repositório privado pede autenticação. Use um **token** (Personal Access
Token do GitHub, escopo `repo`):

```bash
git clone https://SEU_USUARIO:SEU_TOKEN@github.com/ghost2539/CLAUDE.git portal-spare
```

> O token fica gravado em `.git/config`. Depois do clone, tire-o de lá:
> `git remote set-url origin https://github.com/ghost2539/CLAUDE.git`
> e configure `git config --global credential.helper store` quando for
> precisar de novo.

### 2.3 Instalar

```bash
bash deploy/instalar_usuario.sh
```

O script cria o venv, instala as dependências, cria `data/db/` e
`data/uploads/`, gera o arquivo de ambiente com um `PORTAL_SESSION_SECRET`
novo e **confere se a aplicação carrega**, mostrando o número de rotas.

Nada é escrito fora da sua pasta pessoal e do diretório do projeto.

### 2.4 Trazer a configuração do servidor antigo, sem as senhas

O pacote restaurado deixa o ambiente antigo em
`~/environment-do-servidor-antigo.txt` — **com as senhas em texto claro**.
Em vez de copiá-lo por cima, passe pelo importador: ele move o que é segredo
para o cofre e escreve um ambiente limpo, onde a senha vira `@cofre:NOME@`.

```bash
# 1. Veja o que aconteceria, sem gravar nada
venv/bin/python scripts/cofre.py importar-env ~/environment-do-servidor-antigo.txt --simular

# 2. Se estiver certo, execute
venv/bin/python scripts/cofre.py importar-env ~/environment-do-servidor-antigo.txt

# 3. Confira o resultado e coloque no lugar
cat ~/environment-do-servidor-antigo.txt.sem-segredo
cp ~/environment-do-servidor-antigo.txt.sem-segredo ~/.config/portal-spare/environment
chmod 600 ~/.config/portal-spare/environment

# 4. Apague o arquivo antigo — ele ainda tem as senhas
shred -u ~/environment-do-servidor-antigo.txt

# 5. Confirme que nada ficou faltando
venv/bin/python scripts/cofre.py conferir
```

#### Os dois cofres

Todo `@cofre:NOME@` é procurado nesta ordem: **cofre corporativo**
(`vcreports_secrets`, só leitura, mantido por outro time) → **cofre local**
cifrado → variável de ambiente.

Situação hoje neste servidor:

| Segredo | Onde está | O que fazer |
|---|---|---|
| Correios (usuário, chave, cartões) | cofre corporativo | nada — já funciona |
| ServiceNow (conta de serviço) | corporativo, nome desconhecido | guardar no cofre local por ora |
| Banco, admin, Oracle, SMTP | nenhum | cofre local |

> **O arquivo do cofre fica numa partição restrita.** Os serviços alcançam,
> o usuário comum não — e não há como contornar: um processo rodando como
> você lê exatamente o que você lê. Rode `scripts/cofre.py acesso` para ver
> dono, grupo e modo do arquivo, e escolha:
>
> 1. **entrar no grupo** que já tem acesso (`usermod -aG <grupo> <usuário>`);
> 2. **rodar o portal como o usuário de serviço** que tem acesso, via unit
>    systemd de sistema (`deploy/portal_spare.service`) — precisa de root uma vez;
> 3. **pedir os valores** ao time do cofre e guardar no cofre local — funciona
>    hoje, sem depender de permissão nenhuma;
> 4. se o time expuser um **comando de leitura**, configure
>    `VCREPORTS_SECRETS_CMD=sudo -n /usr/local/bin/vcreports-secret {chave}`
>    no `environment` e o portal passa a usá-lo.

> **"O cofre está indisponível, mas os Correios funcionam."** Então o cofre
> existe — quem não o enxerga é o interpretador. O `vcreports_secrets` fica
> instalado no Python **do sistema**, e um venv criado sem
> `--system-site-packages` não alcança pacote do sistema. Confirme:
>
> ```bash
> python3 -c 'import vcreports_secrets; print(vcreports_secrets.__file__)'   # sistema
> venv/bin/python -c 'import vcreports_secrets'                              # venv
> ```
>
> Se o primeiro acha e o segundo não, é isso. Corrija recriando o venv:
>
> ```bash
> rm -rf venv && bash deploy/instalar_usuario.sh
> ```
>
> Ou, sem recriar, aponte o diretório no `environment`:
> `VCREPORTS_SECRETS_PATH=/caminho/onde/esta/o/modulo`

Para descobrir o nome da chave do ServiceNow no corporativo — só leitura, e
o valor nunca aparece:

```bash
venv/bin/python scripts/cofre.py sondar
```

Ele testa os nomes prováveis e mostra quais respondem, com o tamanho do
valor (o suficiente para reconhecer a chave certa). Aceita nomes seus:

```bash
venv/bin/python scripts/cofre.py sondar NOME_QUE_ME_PASSARAM OUTRO_NOME
```

Achou? Aponte o marcador para o nome certo no `environment` e apague a
cópia local:

```bash
# no environment:  SN_API_PASS=@cofre:SERVICENOW_SENHA@
venv/bin/python scripts/cofre.py remover SN_API_PASS
```

Enquanto não achar, guarde no cofre local:

```bash
venv/bin/python scripts/cofre.py definir SN_API_USER
venv/bin/python scripts/cofre.py definir SN_API_PASS
```

O `conferir` lista cada `@cofre:NOME@` citado no ambiente e diz se o valor
existe. Também avisa se a cifra em uso não for a Fernet e se as permissões
do cofre estiverem frouxas.

> **Até onde o cofre local protege.** Ele tira as senhas do arquivo que
> qualquer um abre, do backup e de uma cópia da pasta. Ele **não** protege
> contra quem executa código como o mesmo usuário do portal: a aplicação
> precisa abrir o cofre sozinha na subida, então a chave está ao alcance
> dela — e de quem for esse usuário. Root lê tudo. Vale enquanto as
> permissões separarem as pessoas de verdade; se todos entram com o mesmo
> usuário, é cosmético. Segredo de valor alto deve ir para o cofre
> corporativo (`vcreports_secrets`), que o portal já prefere quando existe.

### 2.4b Ajustar o restante do ambiente

```bash
nano ~/.config/portal-spare/environment
```

O mínimo para o teste subir:

```ini
INITIAL_ADMIN_LOGIN=SEU_LOGIN_DE_REDE      # sem isso ninguém libera ninguém
```

E o banco principal — escolha um dos dois:

```ini
# (A) TESTE rápido, sem servidor de banco: já vem assim
DATABASE_URL=sqlite:////var/www/vcreports/portal-spare/data/db/portal.db

# (B) PRODUÇÃO, com Postgres
DATABASE_URL=postgresql+psycopg2://usuario:senha@host:5432/portal_spare
```

O arquivo nasce com permissão `600` (só você lê). Mantenha assim — tem senha
dentro.

### 2.5 Restaurar os dados do servidor antigo

```bash
bash scripts/restaurar.sh ~/portal-spare-AAAAMMDD-HHMMSS.tar.gz
```

O que ele faz:

- confere o checksum antes de mexer em qualquer coisa;
- restaura o **Postgres** com `pg_restore`, se o destino for Postgres e o
  cliente existir (se o destino for SQLite, ele **pula** e guarda o dump em
  `~/portal_postgres.dump` para você restaurar depois);
- copia os **SQLite** para `data/db/`, renomeando o que já existia para
  `*.anterior-<data>` — nada é sobrescrito sem cópia;
- copia **uploads** e **dados de referência**, inclusive do layout antigo
  (`static/data` → `data/referencias`);
- salva o ambiente antigo em `~/environment-do-servidor-antigo.txt` **sem**
  sobrescrever o novo, para você comparar linha a linha.

Ele se recusa a rodar com o portal no ar — restaurar SQLite com a aplicação
escrevendo corrompe o arquivo.

### 2.6 Subir

```bash
deploy/portal.sh start
deploy/portal.sh status
```

Para descobrir o endereço exato — IPs do servidor, se está preso em
localhost, quem escuta na porta e se há firewall no caminho:

```bash
deploy/portal.sh endereco
```

Acesse pela URL que ele indicar (algo como `http://10.x.x.x:8901`).

Deu errado? O log diz o porquê:

```bash
deploy/portal.sh logs
```

### 2.7 Conferir tela por tela

| Endereço | O que confirmar |
|---|---|
| `/` | login pelo Logon AD entra |
| `/consulta-times` | consulta de ativos responde |
| `/controle-orcamento` | pede login e respeita a permissão |
| `/indicadores` | painel abre |
| `/cockpit-spare` e `/dash-*` | abrem sem login |
| Parâmetros → Monitoramento | disco, memória e bancos OK |
| Parâmetros → Acessos & Alertas | tentativas aparecem |

---

## PARTE 3 — Manter no ar

### Atualizar depois de um commit novo

```bash
cd /var/www/vcreports/portal-spare
deploy/portal.sh atualizar
```

Faz `git pull`, atualiza as dependências e reinicia. Se o `git pull` falhar,
ele para e **não reinicia** — o que estava no ar continua no ar.

### Voltar atrás

```bash
deploy/portal.sh stop
env -u https_proxy -u http_proxy -u HTTPS_PROXY -u HTTP_PROXY git fetch origin
git checkout <commit-que-funcionava>
deploy/portal.sh start
```

O commit que estava em produção está no `VERSAO.txt` de dentro do pacote de
backup.

### Sobreviver ao logout

Sem root, o processo do `deploy/portal.sh` morre quando a sessão encerra em
servidores configurados para matar processos de usuário. Duas saídas:

1. **`systemctl --user`** (preferível) — instruções no cabeçalho de
   `deploy/portal_spare.user.service`. Para o serviço continuar depois do
   logout, alguém com root roda **uma vez**:
   `sudo loginctl enable-linger SEU_USUARIO`
2. **`nohup`** (o que o `portal.sh` já faz) — sobrevive na maioria dos casos,
   mas não é garantido sem o lingering.

### Backup automático

```bash
crontab -e
```

```cron
0 2 * * * cd /var/www/vcreports/portal-spare && PORTAL_APP_DIR=/var/www/vcreports/portal-spare \
  PORTAL_ENVFILE=$HOME/.config/portal-spare/environment \
  bash scripts/backup.sh >> $HOME/backup-portal.log 2>&1
```

---

## Anexo — quando houver HTTPS e subpath

O destino final é `https://suporte.lojasrenner.com.br/portal-spare`, com o
portal atrás de um proxy reverso. Duas coisas mudam quando chegar lá, e
**ainda não estão feitas**:

1. `root_path` no uvicorn e o cookie de sessão com `path=/portal-spare`;
2. `SESSION_COOKIE_SECURE` explícito, para o cookie só trafegar em HTTPS.

O proxy precisa repassar `X-Forwarded-For` (o portal usa para registrar o IP
nas tentativas de acesso) e `X-Forwarded-Proto`.

## Anexo — MySQL no lugar do Postgres

O portal fala com o banco por SQLAlchemy, então MySQL funciona trocando a
URL e instalando o driver:

```bash
venv/bin/pip install pymysql
```

```ini
DATABASE_URL=mysql+pymysql://portal:SENHA@HOST_MYSQL:3306/portal_spare
```

A carga dos dados não é `pg_restore` nesse caso: use
`scripts/migrar_pg_para_mysql.py`, que lê do Postgres e escreve no MySQL.

## Anexo — cofre de segredos

No servidor onde o módulo `vcreports_secrets` estiver disponível, as senhas
saem do arquivo de ambiente e passam a vir do cofre. O portal já procura o
cofre primeiro e só cai para o ambiente/store cifrado quando ele não existe —
não é preciso mudar código, só cadastrar as chaves:

| Chave no cofre | Para quê |
|---|---|
| `CORREIOS_USUARIO`, `CORREIOS_CHAVE` | rastreio dos Correios |
| `SN_AUTOMACAO_USUARIO`, `SN_AUTOMACAO_SENHA` | automação de encerramento |
| `SMTP_USUARIO`, `SMTP_SENHA` | alertas por e-mail |
