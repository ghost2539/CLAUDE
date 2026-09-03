# Migração para o servidor novo (MySQL + cofre de segredos)

Objetivo: subir o Portal SPARE no servidor novo **sem impacto na funcionalidade**,
com três mudanças:

1. **Correios** — credenciais vêm do **cofre** (`vcreports_secret`), não de
   variáveis de ambiente/arquivo.
2. **Banco** — **MySQL** no servidor novo, **preservando os mesmos dados**.
3. **Proteção** — como várias pessoas têm acesso ao servidor, o código e os
   segredos ficam restritos (permissões de arquivo + segredos no cofre).

> Toda mudança de código é **retrocompatível**: no servidor atual (env + PostgreSQL)
> continua funcionando igual; a troca é só de configuração no servidor novo.

---

## 0. Pré-requisitos no servidor novo

- Python 3.11+ e `pip`.
- **MySQL 8** acessível, com um banco e um usuário criados (ver passo 3).
- Módulo **`vcreports_secrets`** disponível no ambiente Python (o time de
  segurança fornece/instala) e os segredos dos Correios já cadastrados nele.
- Sem `sudo` por enquanto → use serviço de usuário (`systemctl --user`) ou
  execução direta; quando tiver `sudo`, promova para serviço do sistema.

---

## 1. Colocar o código

Traga o repositório (via bundle, como temos feito) para, por exemplo,
`/opt/portal-spare-v2` (ou uma pasta onde você tenha escrita, sem sudo).

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt      # já inclui PyMySQL + cryptography
```

---

## 2. Correios pelo cofre (sem env)

O código agora lê as credenciais assim (com fallback para env só na transição):

```python
from vcreports_secrets import vcreports_secret
user = vcreports_secret('CORREIOS_USUARIO')   # idem CORREIOS_CHAVE, CORREIOS_CARTOES...
```

**No servidor novo, NÃO** defina `CORREIOS_USUARIO/CHAVE/CARTOES` no arquivo de
ambiente. Cadastre-os **no cofre** (com o time de segurança):

- `CORREIOS_USUARIO`
- `CORREIOS_CHAVE`
- `CORREIOS_CARTOES` (separados por vírgula)
- `CORREIOS_DR` (ex.: 64) e `CORREIOS_CONTRATO` (se usar contrato)

Teste depois de subir: menu **Correios → testar**, ou o endpoint
`/api/servicenow/correios/test`.

---

## 3. Criar os bancos MySQL/MariaDB

> O servidor usa **MariaDB** (o comando `mysql` abre o MariaDB). O driver
> `mysql+pymysql://` funciona igual — nada muda no código.

**Tudo em MySQL**: crie os TRÊS schemas (portal + os dois módulos isolados),
todos separados entre si, e um usuário:

```sql
CREATE DATABASE portal              CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE indicadores         CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE controle_orcamento  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER 'portal'@'%' IDENTIFIED BY 'SENHA_FORTE';
GRANT ALL PRIVILEGES ON portal.*             TO 'portal'@'%';
GRANT ALL PRIVILEGES ON indicadores.*        TO 'portal'@'%';
GRANT ALL PRIVILEGES ON controle_orcamento.* TO 'portal'@'%';
FLUSH PRIVILEGES;
```

---

## 4. Configurar o ambiente do app

No arquivo de ambiente do serviço (ou `export` antes de rodar), aponte para o
MySQL. **Sem** as credenciais dos Correios (essas vão no cofre):

```
DATABASE_URL=mysql+pymysql://portal:SENHA_FORTE@HOST_MYSQL:3306/portal
PORTAL_SESSION_SECRET=<uma_chave_aleatoria_longa>
INITIAL_ADMIN_LOGIN=<seu_login>

# Integrações (iguais às de hoje)
SN_PROXY=http://10.115.30.135:8888
SN_API_USER=SIS.ZABBIXDCSN
SN_API_PASS=<senha da conta de serviço>     # (ou também via cofre, se preferir)
# SN_API_PROXY não precisa: cai para SN_PROXY automaticamente

# Módulos isolados — TUDO em MySQL (schemas separados):
INDICADORES_DATABASE_URL=mysql+pymysql://portal:SENHA_FORTE@HOST_MYSQL:3306/indicadores
CONTROLE_ORCAMENTO_DATABASE_URL=mysql+pymysql://portal:SENHA_FORTE@HOST_MYSQL:3306/controle_orcamento
```

---

## 5. Criar o schema no MySQL (o app faz sozinho)

Suba o app uma vez apontando para o MySQL — o `init_db()` cria todas as tabelas
(vazias):

```bash
. .venv/bin/activate
python3 main.py          # sobe; confirme que iniciou sem erro; depois pare (Ctrl+C)
```

(ou pelo serviço de usuário — ver passo 8). As tabelas agora existem no MySQL.

---

## 6. Copiar os DADOS (PostgreSQL → MySQL)

Com as tabelas já criadas, copie as linhas do banco atual para o novo. O script
é **idempotente** (limpa o destino e recopia) e ajusta o AUTO_INCREMENT:

```bash
. .venv/bin/activate
python3 scripts/migrar_pg_para_mysql.py \
  "postgresql+psycopg://USUARIO:SENHA@HOST_ANTIGO:5432/NOME_BD_ANTIGO" \
  "mysql+pymysql://portal:SENHA_FORTE@HOST_MYSQL:3306/portal"
```

Ele imprime linha a linha quantos registros copiou por tabela. Rode a partir de
uma máquina que **enxergue os dois bancos** (o novo servidor, se ele alcançar o
PostgreSQL antigo; senão, de um ponto intermediário).

**Tudo em MySQL — migre também os módulos isolados** (se hoje estão em SQLite,
a origem é o arquivo `.db`):

```bash
# Controle de Orçamento (origem SQLite atual -> MySQL)
python3 scripts/migrar_pg_para_mysql.py \
  "sqlite:////opt/portal-spare-v2/data/controle_orcamento.db" \
  "mysql+pymysql://portal:SENHA_FORTE@HOST_MYSQL:3306/controle_orcamento"

# Indicadores (só se já tiver snapshots que queira preservar)
python3 scripts/migrar_pg_para_mysql.py \
  "sqlite:////opt/portal-spare-v2/data/indicadores.db" \
  "mysql+pymysql://portal:SENHA_FORTE@HOST_MYSQL:3306/indicadores"
```

O script é genérico (origem PostgreSQL **ou** SQLite → destino MySQL); antes de
cada um, suba o app apontando as URLs para o MySQL para o schema ser criado.

---

## 7. Validar (antes de virar a chave)

```sql
-- comparar contagens no antigo (PG) e novo (MySQL)
SELECT COUNT(*) FROM users;
SELECT COUNT(*) FROM receipt_cycles;   -- e as demais principais
```

E funcional:
- Login (AD/SSO/local) funciona.
- Uma consulta e uma tela de ServiceNow abrem.
- **Correios → testar** retorna OK (confirma que o cofre está entregando as credenciais).

---

## 8. Rodar como serviço

**Com sudo (quando liberar) — serviço do sistema:**
```bash
sudo cp portal_spare.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now portal_spare.service
```

**Sem sudo agora — serviço de usuário:**
```bash
mkdir -p ~/.config/systemd/user
cp portal_spare.service ~/.config/systemd/user/portal_spare.service   # ajuste ExecStart/venv
systemctl --user daemon-reload
systemctl --user enable --now portal_spare.service
loginctl enable-linger "$USER"   # mantém rodando após logout
```

---

## 9. Proteger (servidor compartilhado)

Como várias pessoas têm acesso:

```bash
chmod -R o-rwx /opt/portal-spare-v2       # ninguém "outros" lê o código
# (idealmente dono = usuário do serviço; grupo restrito)
```

- **Segredos dos Correios**: só no cofre (`vcreports_secret`) — nunca em env,
  `.env` ou no código.
- Não deixe senha em histórico de shell (use formas interativas).
- O arquivo de ambiente do serviço deve ser `chmod 600`.

---

## 10. Rollback

Nada é destruído no servidor antigo. Se algo falhar:
- Mantenha o servidor atual (PG) no ar até validar 100% o novo.
- Vire o DNS/uso para o novo só depois do passo 7 ok.
- Para voltar: reaponte os usuários para o servidor antigo (que segue intacto).

---

## Resumo do que mudou no código

| Item | Antes | Depois |
|---|---|---|
| Credenciais Correios | `os.environ` | `vcreports_secret(...)` (fallback env) |
| Driver de banco | PostgreSQL (`psycopg`) | + MySQL (`PyMySQL`) — escolhido pela `DATABASE_URL` |
| Migração de dados | — | `scripts/migrar_pg_para_mysql.py` |

Schema é 100% compatível com MySQL (tipos genéricos do SQLAlchemy); nenhuma
tela precisa mudar.
