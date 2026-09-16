# Portal de Operações SPARE — normas do projeto

Estas regras valem para **toda** alteração neste repositório. A referência
completa da estrutura está em `docs/ESTRUTURA.md`; este arquivo é o resumo
normativo.

## Organização — cada pasta tem um papel só

| Pasta | O que entra | O que NUNCA entra |
|---|---|---|
| raiz | `main.py`, `config.py`, `requirements.txt` | qualquer outro `.py` |
| `core/` | segurança, sessão, notificações | rota, SQL, cliente externo |
| `db/` | um módulo por banco | rota, regra de negócio |
| `routers/` | as APIs e páginas do portal | driver de banco, cliente externo cru |
| `integracoes/` | clientes de sistemas externos | rota, banco |
| `deploy/` | units systemd, instaladores, certificado | script de operação do dia a dia |
| `scripts/` | operação (backup, migração, carga) | código da aplicação |
| `static/` | front-end **público** | qualquer coisa que exija login |
| `data/` | `db/`, `uploads/`, `referencias/` | código |
| `docs/` | documentação | — |

### Regras que não se negociam

1. **Banco novo** → módulo em `db/`, URL declarada em `config.py` via
   `_sqlite()`. Nenhum módulo monta caminho de arquivo por conta própria, e
   nenhum escreve no banco de outro módulo.
2. **Rota nova** → em `routers/`, sob `/api/<área>`. Nunca na raiz.
3. **Sistema externo novo** → cliente em `integracoes/`.
   3.1. Índice composto declarado em `__table_args__` não pode ter o mesmo
   nome do índice automático de uma coluna com `index=True` — o SQLAlchemy
   gera `ix_<tabela>_<coluna>` e o `create_all` falha com "index already
   exists", derrubando o módulo inteiro no boot. Sufixe o composto
   (`ix_<tabela>_<coluna>_<algo>`).
4. **Nada gravado em disco fora de `data/`.**
5. **Nada que exija login pode morar em `static/`** — ali é público.
   As telas de TV (`/cockpit-spare`, `/dash-*`) são a exceção deliberada:
   são públicas, e por isso os endpoints `/api/cockpit/*` só devolvem
   agregado — nunca dado de colaborador, chamado, série ou loja isolada.
6. **Serviço systemd e instalador** → `deploy/`.
7. **Módulo novo carrega isolado** em `main.py` (bloco `try/except` próprio):
   falha de um módulo nunca derruba o portal.
8. **Um processo só, uma porta só (8901).** Sem root no servidor, cada
   serviço a mais é um problema a mais: tela nova é router do portal, nunca
   aplicativo com systemd próprio.
9. **Módulo novo entra em `config.py: MODULES`.** `require_permission` só
   libera não-admin para módulo dessa lista, porque é dela que a tela de
   permissões grava a linha. Módulo fora da lista funciona para admin e
   nega 403 para todo o resto — e ninguém percebe até o primeiro operador
   tentar.
10. **Valor do usuário em consulta ao ServiceNow passa por `termo_sn()`.**
   Série, etiqueta, chamado: tudo que entra numa encoded query por
   concatenação é sanitizado antes (`routers/servicenow.py`). `^ = , !`
   mudam o significado da consulta, e uma reserva no `sys_id` errado é o
   custo.
11. **Tela nova segue o padrão de UI SPARE** (`docs/PADRAO_UI_SPARE.md`):
   usa o shell e as classes de `static/app.css`, nenhum hex fora da paleta
   (cor entra por token `--sp-*`), Arial, estrutura reta com controles
   arredondados e funciona nos temas claro e escuro. Nada de fonte, script
   ou folha externa: a política de conteúdo só libera o próprio domínio.

## Segurança

- Toda tela e toda API exigem sessão autenticada e passam por
  `require_permission(req, "<módulo>", "<ação>")`. As ações que existem em
  cada módulo estão em `config.py: MODULE_ACTIONS` — módulo novo entra lá
  com as ações que de fato tem; a tela de permissões só oferece essas.
- `admin` de módulo administra **o módulo**. Usuários, liberações,
  desativação e o flag `is_admin` são só de administrador do portal
  (`require_admin`). Mudança de permissão é refletida na sessão viva.
- Autenticação só por **Logon AD** (SSO/loginsso). SSO exige liberação
  prévia por um administrador (`allowed`).
- Escrita no ServiceNow sempre **como o usuário logado** (cookies da
  sessão); leitura pode usar a conta de serviço. `sys_id` vindo do cliente
  passa por `sys_id_valido` (32 hexadecimais) — `_sn_update` recusa o resto.
- Saída HTTP sempre por `integracoes/http.py` (`sessao` / `verificacao_tls`).
  **`verify=False` no código é proibido**; desligar a verificação é
  `VERIFY_SSL=false` no ambiente, e fica em log. Proxy que intercepta o TLS
  pede `PORTAL_CA_BUNDLE`, não desligar.
- Segredo nunca no código nem no git: cofre primeiro, senão store cifrado
  (`core/notificador.py` e `routers/automacoes.py` são os modelos).
- CSP é `script-src 'self'`: **`<script>` inline é bloqueado**. JS de página
  sempre em arquivo externo.

## Estilo

- Código, comentários, mensagens de tela e commits em **português**.
- Comentário explica *por quê*, não *o quê*.
- Mudança aditiva e de baixo risco por padrão; o portal está em produção.

## Fluxo de trabalho

- Commitar no git a cada etapa. **Não gerar bundle de deploy** — só quando
  pedido explicitamente.
- Antes de commitar: `python3 -m compileall` no que mudou, `node --check`
  nos `.js`, `bash -n` nos `.sh`, e subir o app em memória conferindo que a
  lista de rotas não regrediu.
