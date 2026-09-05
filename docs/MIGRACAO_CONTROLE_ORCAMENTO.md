# Migração do Controle de Orçamento (/tv2) para outro servidor

O módulo é autocontido: código próprio, banco próprio (`controle_orcamento_db`)
e, se desejado, serviço próprio. Migrar significa levar **dados + arquivos** e
apontar a variável de ambiente para o banco novo.

Dois jeitos de rodar no servidor novo:

| Modo | Quando usar | URL |
|---|---|---|
| **Serviço isolado** (recomendado) | Sempre que possível. Não depende do portal: atualizações do portal nunca afetam a tela. | `http://<ip>:8902/tv2` |
| Embutido no portal | Só se o portal também for para o servidor novo e a URL `:8901/tv2` for obrigatória. | `http://<ip>:8901/tv2` |

Os dois modos podem coexistir apontando para o mesmo banco.

---

## Parte 1 — No servidor ATUAL: exportar os dados

Escolha **um** dos métodos. O método A é o mais fiel (Postgres → Postgres);
o método B funciona entre quaisquer bancos (inclusive SQLite) e gera um JSON
legível.

### Método A — `pg_dump` (Postgres → Postgres)

```bash
sudo -u postgres pg_dump -Fc controle_orcamento_db -f /tmp/controle_orcamento.dump
ls -la /tmp/controle_orcamento.dump
```

### Método B — JSON pelo script do módulo (qualquer banco)

Na pasta da aplicação, com a mesma variável de ambiente do serviço:

```bash
cd /opt/portal-spare-v2      # ou /opt/controle-orcamento, se já estiver isolado
export $(grep -E "^CONTROLE_ORCAMENTO_DATABASE_URL=" /etc/portal_operacoes_spare/environment)
python3 scripts/controle_orcamento_dados.py exportar /tmp/controle_orcamento.json
```

A saída informa quantas categorias e projetos foram exportados. Guarde o
arquivo: ele também serve de backup.

### Levar os arquivos

```bash
cd /opt/portal-spare-v2
tar -czf /tmp/controle-orcamento-codigo.tar.gz \
  apps/controle_orcamento.py deploy/controle_orcamento.service deploy/requirements-controle-orcamento.txt \
  config.py security.py database_orcamento.py routers/__init__.py routers/controle_orcamento.py \
  static/controle-orcamento static/favicon.svg scripts/controle_orcamento_dados.py \
  deploy/controle_orcamento_instalar.sh docs/CONTROLE_ORCAMENTO.md docs/MIGRACAO_CONTROLE_ORCAMENTO.md
```

Copie para o servidor novo os arquivos `/tmp/controle-orcamento-codigo.tar.gz`
e `/tmp/controle_orcamento.dump` (ou `.json`), por `scp`, WinSCP etc.

---

## Parte 2 — No servidor NOVO: banco

Requisitos: Python 3.10+ e PostgreSQL (qualquer versão recente). Se o
Postgres ainda não existir, instale-o pelo gerenciador de pacotes da
distribuição e inicie o serviço.

### 2.1 Usuário e banco

```bash
sudo -u postgres psql -c "CREATE ROLE portal_spare_app LOGIN PASSWORD 'ESCOLHA_UMA_SENHA';"   # pule se o usuário já existir
sudo -u postgres createdb -O portal_spare_app controle_orcamento_db
sudo -u postgres psql -d controle_orcamento_db -c "ALTER SCHEMA public OWNER TO portal_spare_app;"
```

### 2.2 Liberar acesso no `pg_hba.conf`

Descubra o arquivo e acrescente as duas linhas **antes** de qualquer regra
genérica de rejeição:

```bash
sudo -u postgres psql -Atc "show hba_file"
```

```
host    controle_orcamento_db    portal_spare_app    127.0.0.1/32    scram-sha-256
host    controle_orcamento_db    portal_spare_app    ::1/128         scram-sha-256
```

```bash
sudo -u postgres psql -c "SELECT pg_reload_conf();"
```

### 2.3 Restaurar os dados

**Método A (dump):**

```bash
sudo -u postgres pg_restore -d controle_orcamento_db --no-owner --role=portal_spare_app /tmp/controle_orcamento.dump
sudo -u postgres psql -d controle_orcamento_db -Atc "select count(*) from budget_projects"
```

**Método B (JSON):** faça depois de instalar o código (passo 3), pois usa o
script do módulo:

```bash
cd /opt/controle-orcamento
export $(grep -E "^CONTROLE_ORCAMENTO_DATABASE_URL=" /etc/controle_orcamento/environment)
venv/bin/python scripts/controle_orcamento_dados.py importar /tmp/controle_orcamento.json
```

Se o banco novo já tiver recebido os 8 projetos de exemplo (isso acontece
se a tela for aberta antes da importação), use `--substituir` no fim do
comando para apagá-los e importar por cima.

---

## Parte 3 — No servidor NOVO: código e serviço

### Modo recomendado: serviço isolado (porta 8902)

```bash
mkdir -p /tmp/co && tar -xzf /tmp/controle-orcamento-codigo.tar.gz -C /tmp/co
cd /tmp/co
sudo bash deploy/controle_orcamento_instalar.sh
sudo nano /etc/controle_orcamento/environment     # senha do banco na CONTROLE_ORCAMENTO_DATABASE_URL
sudo systemctl start controle_orcamento.service
sudo systemctl status controle_orcamento.service --no-pager
curl -s http://127.0.0.1:8902/api/controle-orcamento/projetos | head -c 200
```

O instalador cria `/opt/controle-orcamento`, um `venv` que reaproveita os
pacotes Python do sistema (instala o que faltar), o arquivo de ambiente e o
serviço `controle_orcamento.service`. O driver Postgres pode ser `psycopg2`
ou `psycopg`: o módulo usa o que existir.

Acesso: `http://<ip-novo>:8902/tv2`. Libere a porta 8902 no firewall, se houver.

### Modo embutido no portal (porta 8901)

Só se o Portal SPARE também estiver no servidor novo. Extraia o pacote do
módulo sobre a pasta do portal (mesmos arquivos do `tv2-controle-orcamento.tar.gz`),
acrescente `CONTROLE_ORCAMENTO_DATABASE_URL=...` em
`/etc/portal_operacoes_spare/environment` e reinicie `portal_spare.service`.
Detalhes em `docs/CONTROLE_ORCAMENTO.md`.

---

## Parte 4 — Validação e virada

1. Abra `http://<ip-novo>:8902/tv2` e confira: número de demandas, valor
   total e a lista de projetos iguais aos do servidor atual.
2. Confira as categorias (botão **Categorias**) e as cores.
3. Faça uma edição de teste e recarregue a página: deve persistir.
4. Congele edições no servidor antigo (aviso aos usuários) e, se houve
   edições entre a exportação e a virada, repita a exportação e importe com
   `--substituir`.
5. Atualize o link divulgado para a URL nova. No servidor antigo, o `/tv2`
   pode ser desligado removendo a variável de ambiente ou parando o serviço.

## Checklist rápido

- [ ] Dump ou JSON exportado e conferido (quantidade de projetos)
- [ ] Arquivos copiados para o servidor novo
- [ ] Banco, usuário, dono do schema e `pg_hba.conf` no servidor novo
- [ ] Dados restaurados (contagem confere)
- [ ] Serviço instalado, variável com a senha certa, `systemctl status` ativo
- [ ] `curl` na API retorna os projetos
- [ ] Tela validada no navegador e link atualizado

## Backup recorrente (recomendado)

```bash
sudo -u postgres pg_dump -Fc controle_orcamento_db -f /var/backups/controle_orcamento_$(date +%F).dump
```

Ou o JSON pelo script, que pode ser lido e restaurado em qualquer banco.
