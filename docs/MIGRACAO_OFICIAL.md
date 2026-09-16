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

### Acesso direto ao banco Oracle do EBS
`integracoes/ebs_oracle.py`, `routers/ebs_oracle.py`, a aba "Base EBS
(Oracle)", `docs/EBS_ORACLE_BASE.md` e a verificação correspondente.

Eram SQL livre a partir da tela, comandos de exploração de catálogo
(`find`, `sql`) e credencial de esquema no ambiente. O portal fala com o
EBS por **API REST** e, para PO e projetos, pelo **módulo Gestão de
Compras por HTTP** — que é quem tem a credencial, do lado de lá.

A palavra "Oracle" continua no código onde é legítima: o SSO é o Oracle
Access Manager e o EBS é o Oracle E-Business Suite. O que não existe mais
é conexão a banco.

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
python3 scripts/verificar_seguranca.py       # as decisões de segurança
python3 scripts/verificar_ui.py              # o padrão de design
for f in scripts/verificar_*.py; do python3 "$f" || echo "FALHOU: $f"; done
```
