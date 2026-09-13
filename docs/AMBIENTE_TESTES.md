# Ambiente de testes ao lado da produção

Uma segunda instância do portal, no mesmo servidor, na porta **8999**, com
**cópia** dos bancos de produção. A produção só é lida durante a cópia;
nada do teste escreve nela.

## Subir ou atualizar

No servidor, a partir da pasta da produção (ou de qualquer clone):

```bash
sudo bash deploy/instalar_testes.sh              # produção como serviço do sistema
bash deploy/instalar_testes.sh                   # produção sem root
bash deploy/instalar_testes.sh --recopiar-bancos # refaz a cópia dos bancos
```

Rodar de novo só atualiza o código do branch `desenvolvimento`. Os bancos
são copiados uma vez e ficam do jeito que os testes deixarem, até alguém
pedir `--recopiar-bancos`.

Variáveis opcionais: `PROD_DIR`, `PROD_ENVFILE`, `TEST_DIR`, `TEST_PORT`,
`TEST_BRANCH`, `TEST_REPO`.

## O que o instalador faz

1. Clona ou atualiza o branch em `/opt/portal-spare-testes` (com root) ou
   `~/portal-spare-testes` (sem root), com venv próprio.
2. Copia os bancos: Postgres do portal via `pg_dump` para um banco novo
   `<nome>_testes` (o usuário do Postgres precisa de CREATEDB; senão, um
   DBA cria o banco vazio e o script segue); todos os SQLite de
   `data/db/` com `sqlite3 .backup`; `data/uploads`, `data/branding` e
   `data/referencias`.
3. Gera o arquivo de ambiente do teste a partir do de produção, trocando
   `DATABASE_URL`, porta, segredo de sessão e ligando `AMBIENTE=testes`.
   Segundo listener da Consulta Times desligado (`CONSULTA_TIMES_PORTA=0`).
4. Marca o nome da aplicação com **[TESTES]** no banco do teste.
5. Cria o serviço `portal-spare-testes` (systemd do sistema, do usuário,
   ou `deploy/portal.sh` com nohup) e sobe.

## O que muda com `AMBIENTE=testes`

- O agendador das automações (Correios) não roda. O botão manual continua.
- E-mails de alerta não são enviados (ficam no log).
- Nome da aplicação com o sufixo [TESTES].

ServiceNow, EBS, MDM e Correios continuam reais: uma saída de estoque
feita no teste é gravada no ServiceNow de verdade. Teste com ativos de
teste ou desfaça em seguida.

## Desligar

```bash
sudo systemctl disable --now portal-spare-testes && sudo rm /etc/systemd/system/portal-spare-testes.service
sudo rm -rf /opt/portal-spare-testes /etc/portal_operacoes_spare_testes
# Postgres: DROP DATABASE portal_spare_testes;   (o nome termina em _testes)
```
