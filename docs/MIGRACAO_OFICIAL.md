# Migração oficial — o que entrou, o que ficou de fora e por quê

Esta branch (`migracao-oficial`) junta quatro linhas de trabalho que
andaram separadas por meses:

| Linha | O que tinha de próprio |
|---|---|
| `producao` | Orçamento de Manutenção e Controle de Orçamento mais recentes |
| `migracao` | CAPEX Spare, Agendamentos de Fornecedor, Internalização, ponte com o Gestão de Compras, prefixo de proxy |
| `desenvolvimento` | a base sobre a qual a revisão de segurança foi feita |
| `security-architecture-review-cil5mo` | a revisão de segurança e o padrão de design SPARE |

O destino é a revisão de segurança: **o padrão de design e as decisões de
segurança dela mandam**. Quando uma branch trazia a mesma coisa de outro
jeito, quem ficou foi a versão daqui.

## Não foi um merge do git

`desenvolvimento` e `producao`/`migracao` **não têm ancestral comum**:
são duas raízes diferentes (`953a321`, 2026-09-13, e `bca03087`,
2026-08-28). `git merge` entre elas não tem base para comparar — ele
trataria todo arquivo como conflito total.

Então a junção foi feita **por conteúdo, arquivo a arquivo**: para cada
arquivo disputado, comparou-se o texto de cada revisão candidata com o
desta branch para descobrir qual era de fato o ancestral, e só então
mesclou-se. Nos arquivos de orçamento, o conteúdo desta branch batia
exatamente com `producao@d569c74` — prova de que `producao` estava à
frente, e por isso ela entrou inteira nesses arquivos.

Consequência prática: **não existe commit de merge**. Cada conjunto veio
como um commit próprio, com a origem escrita na mensagem.

## O que entrou

- **Orçamento de Manutenção e Controle de Orçamento** (de `producao`),
  com as verificações `verificar_orcamento_manutencao.py` (73) e
  `verificar_controle_orcamento.py` (20).
- **Prefixo de proxy** (`core/prefixo.py`, de `migracao`): o portal é
  publicado em `suporte.lojasrenner.com.br/portal-spare`, e sem isso o
  navegador buscaria `/static` e `/api` na raiz do domínio.
- **CAPEX Spare**, **Agendamentos de Fornecedor** e **Internalização**
  (de `migracao`): telas, bancos próprios e permissões.
- **Ponte com o módulo Gestão de Compras** e **diagnóstico do Cofre de
  segredos**, ambos como abas de Parâmetros, só para administrador.

## O que NÃO entrou, e por quê

O que está nesta lista foi removido de propósito em algum momento. Um
merge desatento traz tudo de volta sem ninguém perceber — por isso
`scripts/verificar_migracao.py` falha se qualquer item reaparecer.

### SQL livre na tela e credencial de banco no ambiente
**A conexão com a base do EBS existe** — `integracoes/ebs_oracle.py` e
`routers/ebs_oracle.py`, na aba *Base EBS* de Parâmetros. O que foi
removido dela são três coisas:

1. **SQL digitado na tela.** O endpoint aceitava um `SELECT` do
   navegador. O filtro "começa com SELECT" não impede subconsulta cara,
   leitura de tabela que não é do assunto, nem consulta que trava sessão
   no banco. Ficaram as consultas **nomeadas** de `QUERIES`, com bind
   variables: o SQL mora no código, versionado e revisável.
2. **Credencial lida de `os.environ`.** O módulo dizia "sem cofre" em
   letras maiúsculas. Agora resolve por `core.cofre.obter` — cofre
   corporativo, cofre local cifrado, ambiente, nessa ordem.
3. **Endereço e usuário escritos como padrão no código.** Host,
   instância e usuário são dado de acesso: no repositório fica só o
   NOME da chave (`ORACLE_EBS_DSN`, `_USER`, `_PASS`).

Também some o que o driver devolve: vários erros do Oracle (ORA-12154,
ORA-12541) trazem o endereço de conexão dentro da mensagem. O router
apaga o DSN — e os pedaços dele — antes de a mensagem chegar à tela.

`docs/EBS_ORACLE_BASE.md` continua fora: era catálogo de tabelas com o
endereço da base no cabeçalho.

Há dois caminhos para o mesmo dado, e a escolha é operacional: direto
(este módulo, quando o serviço alcança a base) ou por HTTP (o módulo
`/gestao_compras`, que consulta do lado de lá). **As consultas são as
mesmas, com os mesmos nomes e os mesmos binds**, de propósito.

### Ponte `/api/cofre/testar-php` e `scripts/cofre_php.php`
A tela mandava um caminho de arquivo `.php` e o portal o executava. Um
caminho vindo do navegador virava `require` no servidor. Removida
inteira.

### Cofre corporativo desligado e credencial lida do ambiente
Em `migracao`, `core/cofre.py` é um esqueleto que devolve vazio em tudo,
e Correios/EBS liam usuário e senha direto de `os.environ`. Aqui o cofre
continua **ligado por padrão** (`COFRE_CORPORATIVO=sim`) e a ordem de
busca é cofre corporativo → cofre local cifrado → ambiente.

### TLS sem verificação na saída
A ponte com o Gestão de Compras vinha com `verify` desligado por padrão e
sessão montada à mão. Passou a sair por `integracoes/http.py`, como toda
chamada externa do portal. Proxy que intercepta o TLS se resolve com
`PORTAL_CA_BUNDLE` — nunca desligando a conferência.

### Páginas e arquivos mortos
`routers/reparos.py` (duplicata: a Central de Reparos é
`routers/bancada.py`), `static/consulta-times/` (espelho antigo; aqui é
`static/modules-times/`), `docs/MIGRACAO.md`, `docs/DOCUMENTACAO_SISTEMA`
em `.html`/`.pdf` (gerados) e a duplicata em caixa baixa de
`Diretrizes do Projeto.md`.

### O visual concorrente de `migracao`
`migracao` tinha a própria repaginada do shell. O padrão que vale é o
**Padrão de UI SPARE** (`docs/PADRAO_UI_SPARE.md`): paleta LRSA 2025,
Arial em todo o sistema, estrutura reta com controles arredondados, uma
sombra só. Toda tela que veio foi reescrita nesses tokens — nenhuma cor
fixa sobrou.

**Exceção combinada:** o Controle de Orçamento InfraCSC (aplicação React
em `static/controle-orcamento-exec/`) fica fora do padrão de design, por
decisão de quem pediu a migração.

## Dois nomes parecidos, dois produtos

`migracao` chamava o CAPEX de `orcamento_spare`, que é o nome que esta
branch já usava para **outro** produto (`routers/orcamento_spare_exec.py`).
As duas telas disputariam a mesma chave de permissão.

Resolvido dando ao CAPEX chave, rota e banco próprios:

| | Orçamento Spare | CAPEX Spare |
|---|---|---|
| Permissão | `orcamento_spare` | `capex_spare` |
| Rota | `/api/orcamento-spare` | `/api/capex-spare` |
| Banco | `orcamento_spare_exec.db` | `capex_spare.db` |

As duas telas estão no ar. **Decidir se as duas ficam é de quem usa** —
elas resolvem problemas parecidos e ninguém aqui pode dizer qual sobra.

## Correção que a migração revelou

`config.py` resolvia proxy com encadeamento `or`. Com isso, a variável
`HTTPS_PROXY=` **declarada e vazia** no `environment` do servidor — que
significa "aqui não tem proxy" — caía para o `https_proxy` do perfil da
máquina, e toda chamada de API saía por um proxy que o destino não
conhece. Agora `config._proxy()` encerra a busca na primeira variável
declarada, vazia ou não. Foi assim que a verificação do Gestão de Compras
passou a fechar.

## Como conferir

```bash
python3 scripts/verificar_migracao.py        # o que não pode voltar
python3 scripts/verificar_ebs_oracle.py      # a conexão com a base do EBS
python3 scripts/verificar_seguranca.py       # as decisões de segurança
python3 scripts/verificar_ui.py              # o padrão de design
for f in scripts/verificar_*.py; do python3 "$f" || echo "FALHOU: $f"; done
```
