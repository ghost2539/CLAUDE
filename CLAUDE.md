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
| `apps/` | aplicativos com systemd próprio | código do portal |
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
4. **Nada gravado em disco fora de `data/`.**
5. **Nada que exija login pode morar em `static/`** — ali é público.
6. **Serviço systemd e instalador** → `deploy/`.
7. **Módulo novo carrega isolado** em `main.py` (bloco `try/except` próprio):
   falha de um módulo nunca derruba o portal.

## Segurança

- Toda tela e toda API exigem sessão autenticada e passam por
  `require_permission(req, "<módulo>", "<ação>")`. Ações: `view`, `create`,
  `edit`, `export`, `admin`.
- Autenticação só por **Logon AD** (SSO/loginsso). SSO exige liberação
  prévia por um administrador (`allowed`).
- Escrita no ServiceNow sempre **como o usuário logado** (cookies da
  sessão); leitura pode usar a conta de serviço.
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
