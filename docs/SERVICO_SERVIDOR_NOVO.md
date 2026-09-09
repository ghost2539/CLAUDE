# Pedido à infraestrutura — criação do serviço do Portal SPARE

Documento para entregar a quem vai criar o serviço no servidor novo. É só o
que precisa ser feito **com root**; a instalação da aplicação, o cofre de
segredos e a operação do dia a dia ficam com a equipe do portal.

> **Princípio que não pode ser quebrado:** nenhuma senha em texto claro no
> `environment`, na unit do systemd ou em qualquer arquivo de configuração.
> A senha da conta de serviço do ServiceNow (a conta do Zabbix) é gravada
> **cifrada, no cofre local**, pela equipe do portal — a infraestrutura não
> precisa dela e não deve recebê-la por e-mail, chamado ou anexo.

---

## 1. Resumo do que se pede

| # | Item | Detalhe |
|---|---|---|
| 1 | Usuário de serviço | `portalspare`, de sistema, sem shell |
| 2 | Diretório da aplicação | `/var/www/vcreports/portal-spare` |
| 3 | Diretório de configuração | `/etc/portal_operacoes_spare` (+ subpasta `cofre`) |
| 4 | Python | 3.10 ou superior, com `venv` |
| 5 | Unit systemd | `portal-spare.service` (conteúdo no item 6) |
| 6 | Permissão de restart | a equipe do portal reinicia o serviço sem root pleno |
| 7 | Portas de entrada | 8901 e 8502 liberadas na rede interna |
| 8 | Saída de rede | os destinos da tabela do item 8 |

Nada disso depende de banco de dados externo: o portal usa SQLite em
`data/db/` por padrão. *(Se a decisão for PostgreSQL, veja o item 9.)*

---

## 2. Usuário e diretórios

```bash
# 2.1 usuário de serviço (sem login interativo)
useradd --system --shell /usr/sbin/nologin \
        --home-dir /var/www/vcreports/portal-spare portalspare

# 2.2 diretório da aplicação
mkdir -p /var/www/vcreports/portal-spare
chown -R portalspare:portalspare /var/www/vcreports/portal-spare

# 2.3 configuração e cofre
mkdir -p /etc/portal_operacoes_spare/cofre
chown root:portalspare /etc/portal_operacoes_spare
chmod 750             /etc/portal_operacoes_spare
# o cofre é escrito e lido SÓ pelo usuário do serviço
chown portalspare:portalspare /etc/portal_operacoes_spare/cofre
chmod 700                     /etc/portal_operacoes_spare/cofre
```

O arquivo `/etc/portal_operacoes_spare/environment` será entregue pela
equipe do portal — ele contém **apenas configuração** (URLs, portas, flags).
Permissão dele: `chown root:portalspare` e `chmod 640`.

---

## 3. Python

```bash
# Oracle Linux / RHEL
dnf install -y python3 python3-pip

python3 -m venv /var/www/vcreports/portal-spare/venv
chown -R portalspare:portalspare /var/www/vcreports/portal-spare/venv
```

A instalação das dependências (`pip install -r requirements.txt`) é feita
pela equipe do portal, como `portalspare`. Uma delas importa para a
segurança: **`cryptography`** — é a biblioteca que cifra o cofre. Sem ela o
cofre cai para uma cifra fraca de contingência.

---

## 4. Como a senha fica protegida (para conhecimento)

1. A equipe do portal roda, como `portalspare`:
   `python3 scripts/cofre.py definir SN_API_PASS` — a senha é digitada
   escondida, nunca aparece em linha de comando nem no histórico do shell.
2. O valor é gravado **cifrado** em
   `/etc/portal_operacoes_spare/cofre/cofre.json`, com a chave em
   `cofre.key` (arquivo `600`, do próprio usuário do serviço).
3. O portal lê o segredo na subida, direto do cofre. O `environment` **não
   tem** linha de usuário nem de senha da conta de serviço.

O que isso protege: quem abrir o `environment`, quem pegar um backup, quem
copiar a pasta da aplicação. O que **não** protege: quem executa comando
como `portalspare` ou como `root` — por isso o `cofre/` é `700` e o usuário
de serviço não tem shell.

---

## 5. Permissão para a equipe reiniciar o serviço

A equipe do portal precisa reiniciar o serviço depois de cada atualização —
sem root pleno. Escolha **uma** das duas formas:

**a) sudoers** (`/etc/sudoers.d/portal-spare`, `chmod 440`):

```
%grupo-portal ALL=(root) NOPASSWD: /usr/bin/systemctl restart portal-spare, \
    /usr/bin/systemctl stop portal-spare, /usr/bin/systemctl start portal-spare, \
    /usr/bin/systemctl status portal-spare, /usr/bin/journalctl -u portal-spare *
```

**b) polkit** (`/etc/polkit-1/rules.d/50-portal-spare.rules`):

```javascript
polkit.addRule(function (action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        action.lookup("unit") == "portal-spare.service" &&
        subject.isInGroup("grupo-portal")) {
        return polkit.Result.YES;
    }
});
```

> `[DECIDIR]` qual grupo do AD entra no lugar de `grupo-portal`, e quem faz
> parte dele.

---

## 6. Unit do systemd

Arquivo `/etc/systemd/system/portal-spare.service` — o conteúdo está
versionado no repositório em `deploy/portal_spare.service`. Reproduzido
aqui:

```ini
[Unit]
Description=Portal de Operacoes SPARE
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=portalspare
Group=portalspare
WorkingDirectory=/var/www/vcreports/portal-spare
EnvironmentFile=/etc/portal_operacoes_spare/environment
Environment=PORTAL_COFRE_DIR=/etc/portal_operacoes_spare/cofre
ExecStart=/var/www/vcreports/portal-spare/venv/bin/python -m uvicorn main:app \
    --host 0.0.0.0 --port 8901 --workers 1
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
UMask=0027
ReadWritePaths=/var/www/vcreports/portal-spare/data
ReadWritePaths=/etc/portal_operacoes_spare/cofre

[Install]
WantedBy=multi-user.target
```

Dois detalhes que costumam derrubar a subida e por isso estão explícitos:

- **`Environment=PORTAL_COFRE_DIR=...`** — com `ProtectHome=yes`, o serviço
  não enxerga `/home`; o cofre precisa morar em `/etc/portal_operacoes_spare/cofre`,
  como acima. Sem essa linha o portal sobe **sem enxergar segredo nenhum**.
- **Sem `Requires=postgresql.service`** — o portal usa SQLite; um `Requires`
  de banco que não existe impede o serviço de iniciar.

Ativação:

```bash
systemctl daemon-reload
systemctl enable --now portal-spare
systemctl status portal-spare
```

---

## 7. Portas de entrada

| Porta | Para quê | Origem |
|---|---|---|
| 8901 | Portal (todas as telas) | rede interna |
| 8502 | Tela "Consulta de Ativos — Times" | rede interna |

A 8502 é o endereço do aplicativo antigo, que os times têm salvo. É o
**mesmo processo** ouvindo em duas portas — não é um segundo serviço. Se a
decisão for não expor a 8502, a equipe do portal desliga por configuração e
a porta pode ficar fechada.

> `[DECIDIR]` Haverá HTTPS/certificado no servidor ou o acesso continua em
> HTTP na rede interna? Com certificado, informar o caminho do `.crt` e da
> chave, legíveis pelo usuário `portalspare`.

---

## 8. Saída de rede (liberação de firewall/proxy)

O servidor precisa **sair direto**, sem proxy. Destinos usados:

| Destino | Porta | Para quê |
|---|---|---|
| `renner.service-now.com` | 443 | ServiceNow — chamados e indicadores |
| `loginsso.lojasrenner.com.br` | 443 | Logon AD (SSO) do portal |
| `api.correios.com.br` | 443 | Rastreio de objetos |
| `ebscorporativo.lojasrenner.com.br` | 80/443 | Oracle EBS (consulta de ativos) |
| `suporte.lojasrenner.com.br` | 443 | API de consulta de imobilizado |
| relay SMTP interno | 25/587 | Alertas por e-mail *(se for usar)* |

> `[DECIDIR]` Endereço do relay SMTP, se os alertas por e-mail forem ligados.

---

## 9. Banco de dados

Padrão e recomendado: **SQLite**, em `/var/www/vcreports/portal-spare/data/db/`.
Nada a criar, nada a manter.

> `[DECIDIR]` Se a área exigir PostgreSQL, é preciso criar base e usuário e
> informar host/porta/base/usuário. A **senha vai para o cofre** e o
> `environment` recebe só o marcador — nunca o valor:
> `DATABASE_URL=postgresql+psycopg2://portal:@cofre:DB_SENHA@@HOST:5432/portal_spare`

---

## 10. Conferência final (o que confirma que ficou certo)

```bash
systemctl is-active portal-spare                 # active
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8901/    # 200
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8502/consulta-times   # 200
journalctl -u portal-spare -n 30 --no-pager      # sem erro de cofre/porta

# nenhuma senha em texto claro na configuração:
grep -iE 'pass|senha|secret' /etc/portal_operacoes_spare/environment
# só deve aparecer marcador @cofre:NOME@ ou linha vazia

# permissões do cofre
ls -ld /etc/portal_operacoes_spare/cofre         # drwx------ portalspare
ls -l  /etc/portal_operacoes_spare/cofre         # cofre.key e cofre.json em 600
```

---

## 11. O que fica com a equipe do portal (não é pedido à infraestrutura)

1. Clonar/atualizar o código em `/var/www/vcreports/portal-spare` e instalar
   as dependências no venv.
2. Escrever o `environment` (só configuração).
3. Gravar os segredos no cofre: `SN_API_USER`, `SN_API_PASS`,
   `PORTAL_SESSION_SECRET`, `INITIAL_ADMIN_LOGIN`, credenciais dos Correios
   e do EBS.
4. Conferir com `python3 scripts/cofre.py conferir` — inclusive que a cifra
   em uso é `fernet` (e não a de contingência).
5. Restaurar os dados do servidor antigo e validar as telas.
6. Reiniciar o serviço a cada atualização.
