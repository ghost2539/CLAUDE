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

Houve dois caminhos para o mesmo dado: direto (este módulo, quando o
serviço alcança a base) ou por HTTP (o módulo `/gestao_compras`, que
consultava do lado de lá). A ponte HTTP e a aba que a usava saíram do
portal; sobrou o caminho direto, **com as mesmas consultas, os mesmos
nomes e os mesmos binds** do lado de lá.

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
em `bundles/controle-orcamento-exec/`, fora de static/ porque lá o mount é
público) fica fora do padrão de design, por
decisão de quem pediu a migração.

## Dois nomes parecidos, dois produtos — resolvido

`migracao` chamava o CAPEX de `orcamento_spare`, que era também o nome de
**outro** produto. Na `migracao-oficial` isso foi separado dando ao CAPEX
chave, rota e banco próprios (`capex_spare`, `/api/capex-spare`,
`capex_spare.db`), e ficou registrado que **decidir se as duas ficam é de
quem usa**.

**Decidido (18/09): fica o Orçamento Spare; o CAPEX Spare sai.**

Na branch `migracao` a separação nunca chegou a existir como módulo:
`routers/capex_spare.py` e `db/capex_spare.py` não estão aqui. O que havia
eram **duas entradas de menu quebradas**, e nenhuma delas chegava à tela que
funciona:

| Entrada do menu | Apontava para | O que acontecia |
|---|---|---|
| Orçamento Spare | `data-href="/orcamento-spare"` | página inexistente → 404 |
| CAPEX Spare | `data-route="capex_spare"` | `modulos/capex_spare.js` não existe → "Falha ao carregar módulo" |

A tela real é `modulos/orcamento_spare.js`, servida por
`routers/orcamento_spare.py` em `/api/orcamento-spare`, com permissão
`orcamento_spare`. Ela estava no ar e sem caminho no menu.

O que ficou: **uma** entrada, `data-route="orcamento_spare"`, chamando o
módulo que existe. O nome interno "CAPEX Spare" (docstring do router, do
banco e título da tela) virou "Orçamento Spare", para o menu e a tela
dizerem a mesma coisa.

Sobra no servidor o arquivo `data/db/capex_spare.db`, que nenhum código
abre mais. Ele não está no repositório (`data/db/` é ignorado); apague à
mão quando quiser, depois de guardar uma cópia se houver dado que importe.

## Correção que a migração revelou

`config.py` resolvia proxy com encadeamento `or`. Com isso, a variável
`HTTPS_PROXY=` **declarada e vazia** no `environment` do servidor — que
significa "aqui não tem proxy" — caía para o `https_proxy` do perfil da
máquina, e toda chamada de API saía por um proxy que o destino não
conhece. Agora `config._proxy()` encerra a busca na primeira variável
declarada, vazia ou não. Foi assim que a verificação do Gestão de Compras
passou a fechar.

## Conferência `producao` → `migracao` (17/09)

A junção por conteúdo, arquivo a arquivo, tem um risco conhecido: um
trecho de back-end some sem que a tela some junto, e o defeito só aparece
quando alguém clica. A conferência foi refeita comparando as duas branches
inteiras — arquivos, rotas, colunas de modelo, itens de menu, colunas de
tabela e rótulos de tela.

O resultado: **`migracao` já continha tudo o que `producao` tem**, exceto
dois pontos, os dois de back-end.

### O painel do Orçamento de Manutenção não abria nos modelos

Os cards *Aguardando aprovação* e *Aguardando devolução* mostram uma linha
por categoria que abre nos modelos daquela categoria. A tela veio inteira
(`om-catrow`, `om-modrow`, o cursor que gira), mas o `/resumo` agrupava só
por categoria e família: sem `modelos` no retorno, `mods.length` era zero e
a linha nunca virava clicável. Nada quebrava, nada aparecia no log — a
linha simplesmente não abria.

O agrupamento por modelo voltou, com as duas conferências que faltavam:
a soma dos modelos fecha com a da categoria, coluna por coluna.

### A credencial dos Correios não passava pelo cofre

`routers/correios.py` era a última integração lendo `os.environ` direto —
e com `os.environ['CORREIOS_USUARIO']`, que estoura `KeyError` e vira 500
sem explicação quando a variável falta. Agora passa por `core.cofre.obter`,
como Automações, Notificador e EBS: cofre corporativo → cofre local
cifrado → ambiente. Quem roda com as variáveis no `environment` não muda
nada; quem já usa cofre para de guardar a senha dos Correios em arquivo.

### O que foi conferido e estava certo

- **Rotas**: as 194 de `producao` existem em `migracao` (que tem 354).
  As quatro de `/campos` são do *outro* Orçamento Spare — ver acima.
- **Colunas de modelo**: nenhuma perdida. As diferenças em `db/` são o
  `UtcDateTime`, que é correção de `migracao`.
- **Menu**: os 10 itens de `producao` estão nos 32 de `migracao`.
  `gestao_ativos` e `reparos` viraram submenu; `bemvindo` continua sendo
  a tela de entrada, só saiu da barra lateral.
- **Colunas e rótulos de tela**: nenhum rótulo ou coluna de `producao`
  ficou de fora.
- **Módulos com permissão**: os 14 de `producao` estão nos 30.

### O campo `CA_BUNDLE` que faltava no `Settings`

`integracoes/http.py` lê `_cfg.CA_BUNDLE` para achar o PEM da CA
corporativa, mas `config.Settings` não tinha esse campo. Com
`VERIFY_SSL=true`, a leitura estourava `AttributeError` **antes de sair a
primeira requisição** — ou seja, ligar a verificação do TLS pelo caminho
oficial do projeto não era possível. O campo entrou lendo
`PORTAL_CA_BUNDLE` (e, na falta dele, `REQUESTS_CA_BUNDLE`); vazio
continua significando "use a CA padrão do sistema".

### TLS de saída: o que foi visto e o que NÃO foi mexido

Duas coisas, as duas iguais nas duas branches — não são regressão da
migração, e por isso ficaram de fora desta conferência:

1. **`integracoes/http.py` não tem nenhum chamador.** O ajudante de
   sessão com TLS verificado veio da revisão de segurança, mas cada
   integração continua montando a própria sessão. `sess.verify = False`
   aparece em `routers/servicenow.py` (8×), `routers/auth.py` (2×),
   `correios.py`, `automacoes.py` e `controle_orcamento_exec.py`.
2. **`VERIFY_SSL` tem padrão `false`** em `config.py`, e
   `scripts/verificar_seguranca.py` espera o contrário já na primeira
   conferência — como ele sai no primeiro erro, **a suíte de segurança
   inteira não roda desde então**. O padrão também vale para
   `routers/indicadores.py`, que lê `VERIFY_SSL` direto.

Virar o padrão é uma linha em `config.py`, mas muda o comportamento de
saída do EBS e dos indicadores. Com proxy que intercepta o TLS isso só
funciona com `PORTAL_CA_BUNDLE` apontando para um PEM com a CA
corporativa — e `deploy/environment.modelo`, `deploy/install.sh` e
`.env.example` hoje escrevem `VERIFY_SSL=false` explicitamente. **Não foi
mexido**: é trabalho próprio, a combinar antes da virada de servidor.

## Como conferir

```bash
python3 scripts/verificar_migracao.py        # o que não pode voltar
python3 scripts/verificar_orcamento_manutencao.py   # o painel, inclusive por modelo
python3 scripts/verificar_correios_credencial.py    # a credencial dos Correios
python3 scripts/verificar_ebs_oracle.py      # a conexão com a base do EBS
python3 scripts/verificar_seguranca.py       # as decisões de segurança
python3 scripts/verificar_ui.py              # o padrão de design
for f in scripts/verificar_*.py; do python3 "$f" || echo "FALHOU: $f"; done
```
