# Ciclo do Ativo — contrato dos módulos

Os módulos que implementam o desenho de processos da área. A Trilha é o
núcleo — mede tempo. Os demais são processos montados em cima dela.

| Cód. | Processo | Módulo |
|---|---|---|
| A01 | Recebimento (entrada na trilha) | gancho em `routers/recebimento.py` |
| A02 A03 A04 | Bancadas de triagem e reparo | `bancada` |
| A05 A14 | Assistência externa e devolução | `externo` |
| A06 A07 A08.2 | Configuração, montagem, internalização | `preparacao` |
| A09 a A13 | Descaracterização, lote, venda, descarte, doação | `destinacao` |
| A15 | Separação e expedição | `separacao` |
| A16 | Projetos de loja (inauguração e reforma) | `projetos` |
| A17 | Logística reversa | `reversa` |
| A18 | Inventário e contagem | `inventario` |
| A19 | Regularização de ativo | `regularizacao` |
| A20 | Atendimento a chamados | `atendimento` |
| T1 T2 | Trilha do ativo e Torre de Controle | `torre` |

Cada um tem banco próprio, carrega isolado no `main.py`, e nenhum escreve no
banco de outro. O que compartilham é o núcleo e o calendário.

---

## 1. Trilha do Ativo (núcleo)

`db/trilha.py` · `routers/trilha.py` · banco `data/db/trilha.db`

### O problema que resolve

O portal registrava **estado** (`movements`: status anterior, status novo,
usuário, data). Os processos pedem **tempo**: quanto o ativo esperou, quanto
ficou na mão de quem assumiu, quantas vezes esse relógio pausou. Fila, SLA por
etapa, tempo total na área e painel individual saem todos daí.

### Três tabelas

| Tabela | Papel |
|---|---|
| `trl_ativo` | O token que atravessa os processos |
| `trl_movimentacao` | Uma linha por transição. Nunca alterada, nunca apagada |
| `trl_intervalo` | O relógio, com início e fim **crus** |

### Duas decisões que explicam o resto

**O intervalo é gravado sem desconto de expediente.** O tempo útil é calculado
na leitura, a partir do calendário em `trl_config`. Mudar o calendário
recalcula todo o histórico — gravar o número já descontado congelaria uma conta
feita com a regra errada. Isso é verificado em
`scripts/verificar_trilha.py`.

**Cada retomada abre uma sessão nova.** Um reparo que para para esperar peça e
volta é o mesmo trabalho, não dois; mas a espera fica num intervalo separado, do
tipo `EXTERNO`, que não entra na conta da pessoa. Guardar só o primeiro início e
o último fim contaria a espera como bancada.

### Vocabulário

```
Trilha    FISICA | ADMINISTRATIVA
Tipo      FILA (mede o fluxo) | TRATATIVA (mede a pessoa) | EXTERNO (não conta)
Origem    PORTAL | ADMIN (exige justificativa) | AUTOMACAO
```

Exclusividade de relógio é validada **por trilha**, não globalmente: física e
administrativa correm juntas de propósito na entrada de ativo novo, em que o
equipamento vai para o estoque sem esperar o EBS.

### Como um processo usa

Nenhum módulo escreve em `db.trilha` direto. Todos chamam `mover()`, que recebe
a sessão de quem chama — assim o que o processo grava e a trilha entram ou
falham juntos.

```python
from routers.trilha import mover
import db.trilha as dbt

with dbt.SessionLocal() as s:
    mover(s, ativo, estado="EX_REPARO", tipo=dbt.TRATATIVA,
          processo="A02", usuario=login)
    s.commit()
```

`mover()` recusa: tratativa sem usuário, correção manual sem justificativa,
estado vazio, trilha ou tipo desconhecido, movimentação anterior ao intervalo
aberto, e ativo já encerrado.

### Ativos anteriores ao módulo

Não entram. A contagem começa nos novos, sem retroativo — decisão da área.
Processo que topa com um ativo fora da trilha segue normalmente, só não mede.

### Reentrada

Serial que já encerrou um ciclo (saiu como `ENTREGUE`, `SUBSTITUIDO`…) e
volta pelo Recebimento é **reaberto** (`reabrir_ativo()`), não recusado: o
segundo ciclo entra em `AG_TRIAGEM` com `detalhe {"reentrada": true}` e o
histórico mostra os dois ciclos inteiros. Serial cujo ciclo ainda está em
curso é leitura duplicada e não mexe em nada.

### Termos vindos do usuário nas consultas ao ServiceNow

Toda série, etiqueta ou número de chamado que entra numa encoded query
passa por `routers.servicenow.termo_sn()`, que recusa `^ = , !` e afins.
Sem isso um bipe `X^ORinstall_status=6` casaria outro registro e a reserva
cairia no `sys_id` errado.

### Verificação

```
python3 scripts/verificar_trilha.py
```

45 casos em banco temporário. Rode depois de qualquer alteração no núcleo: é a
única peça cuja falha atrapalha todos os módulos.

---

## 2. Separação (A15)

`db/separacao.py` · `routers/separacao.py` · `static/modules/separacao.js` ·
banco `data/db/separacao.db`

### Regras que moldam o módulo

- **Toda solicitação nasce de um chamado** (INC ou RITM), validado no
  ServiceNow antes de gravar qualquer coisa. O destino vem do chamado.
- **O pedido é por modelo e quantidade.** A série sai na bancada, com quem
  separa.
- **Cada tipo de atendimento enxerga um estoque só.**

### Como o estoque é lido

Reposição e Inauguração dividem o mesmo depósito. O que separa é o começo do
espaço e do corredor, gravado no campo `aisle_space_location` do `alm_hardware`
— o mesmo que a tela de Saída de Ativos já escreve, e que aparece no ServiceNow
como "Aisle and Space".

Com os padrões, a consulta montada é:

```
FRENTE_RETAGUARDA     install_status=6^aisle_space_locationSTARTSWITHREP^substatus!=reserved
MOBILIDADE            install_status=6^aisle_space_locationSTARTSWITHREP^substatus!=reserved
INAUGURACAO_REFORMA   install_status=6^aisle_space_locationSTARTSWITHIN^substatus!=reserved
```

Mobilidade consome o estoque de reposição: o mesmo modelo existe nos dois, e é
a prateleira que decide, não o equipamento.

Tudo é parâmetro em **Parâmetros → Separação**: campo, comparação
(`STARTSWITH` / `=` / `LIKE`), prefixos, o mapa de tipo para estoque, e um
filtro manual com `$campo`, `$comparacao` e `$prefixo` como escape para o caso
que os parâmetros não cobrirem.

### Ciclo de vida

```
AG_SEPARACAO  →  EX_SEPARACAO  →  SEPARADA  →  ENVIADA
                        ↓
                    CANCELADA
```

| Passo | O que acontece no ServiceNow |
|---|---|
| Bipar a série | `substatus = reserved` — sai do saldo disponível na hora |
| Concluir | nada; é marco interno |
| Enviar | `install_status = 1` e `location` = loja de destino; na trilha, cada unidade é **encerrada** como `ENTREGUE` |
| Cancelar | `substatus = available` nas unidades já bipadas; na trilha, cada uma volta a `DISPONIVEL` (fila) |

A ordem no bipe importa e está explícita no código: **localizar, reservar, e só
então gravar**. Se o ServiceNow recusar a reserva, nada fica registrado no
portal — o contrário deixaria a unidade contada como separada e ainda visível
no catálogo de todo mundo.

Cancelar sem soltar a reserva deixaria equipamento invisível no saldo para
sempre; por isso o cancelamento libera unidade por unidade.

### O que ainda não existe

- **Embalagem, NF e etiqueta.** O documento prevê `EX_EMBALAGEM`, `EX_FISCAL` e
  `AG_POSTAGEM` entre separar e despachar. Hoje o envio é um passo só.

---

## 2.1 Projetos de Loja (A16)

`db/projetos.py` · `routers/projetos.py` · `static/modules/projetos.js` ·
banco `data/db/projetos.db`

### O que muda em relação à Separação

O estoque é o mesmo (a prateleira `IN`), lido pelas funções da Separação. O
que muda é o **token**: na Separação é o chamado; aqui é o **item do
projeto** — "tantos equipamentos deste modelo, para esta área da loja". Ele
nasce meses antes de existir série, e cada item anda no próprio ritmo: o
projeto inteiro não espera a linha que travou.

O item abre um token no núcleo com serial sintético (`PRJ-2026-0001/12`,
`tipo_equipamento = item_projeto`). É por isso que aparece na Torre, na
frente **Projetos**, com o mesmo relógio de todo mundo.

### Ciclo do item

```
AG_DEFINICAO → AG_SEPARACAO_PROJ → EX_SEPARACAO_PROJ → AG_CONFIGURACAO_PROJ
                     ↑                    ↓                       ↓
                 AG_ESTOQUE          (bipe reserva)        EX_CONFIGURACAO_PROJ
                                                              ↓          ↓
                                                    AG_REPARO_PROJ   PRONTO_PROJ → ENVIADO_PROJ
```

| Estado | Tipo no núcleo | Por quê |
|---|---|---|
| `AG_DEFINICAO`, `AG_ESTOQUE`, `AG_REPARO_PROJ` | `EXTERNO` | O tempo passa e entra no total do projeto, mas não é de ninguém da área |
| `AG_SEPARACAO_PROJ`, `AG_CONFIGURACAO_PROJ`, `PRONTO_PROJ` | `FILA` | Mede o fluxo |
| `EX_SEPARACAO_PROJ`, `EX_CONFIGURACAO_PROJ` | `TRATATIVA` | Mede a pessoa que assumiu |

A configuração (etapa entre separar e enviar) é onde o equipamento é
preparado para a loja. Reprovar uma unidade ali solta a reserva, manda o
equipamento para `AG_TRIAGEM` (parâmetro `estado_reparo`) e põe o item em
`AG_REPARO_PROJ`; a linha da unidade fica marcada como devolvida, para a
conta de retrabalho. A mesma série pode voltar ao mesmo item depois do
conserto — por isso o índice `(item_id, serial)` **não** é único.

No ServiceNow acontece o mesmo que na Separação: bipe = `substatus =
reserved`; envio = `install_status = 1` e `location` = loja; cancelar solta
as reservas. Os parâmetros de reserva e envio são os da Separação — um só
lugar decide o que "em uso na loja" significa.

O projeto em si não tem relógio: é uma pasta. Sai de `PLANEJAMENTO` no
primeiro item definido e vira `CONCLUIDO` quando não sobra item pendente.

### Verificação

```
python3 scripts/verificar_separacao.py   # A15: envio fecha o relógio, cancelamento devolve
python3 scripts/verificar_projetos.py
```

48 casos em banco temporário, com dublês no lugar do ServiceNow.

---

## 2.2 Logística Reversa (A17)

`db/reversa.py` · `routers/reversa.py` · `static/modules/reversa.js` ·
banco `data/db/reversa.db`

### O token é a Coleta; o indicador é esperado × recebido

O caminho de volta: a loja devolve equipamento ao CD. A coleta guarda o
que se combinou que viria (série ou etiqueta — a que a loja conseguir
ler), o **prazo acordado** para postar, e o código de rastreio quando
postar. A etiqueta reversa é emitida fora do portal; aqui fica o código, e
é por ele que o módulo acompanha nos Correios (`consultar_rastreio`, do
módulo Correios).

A coleta **não fecha como "recebida"**. Fecha como `CONFERIDA` (tudo que
era esperado chegou, e nada além) ou `DIVERGENTE` (faltou ou sobrou).
Divergente exige observação, e cada diferença vira um token de
regularização (A19) — faltante e inesperado, os dois.

### Ciclo

```
AG_POSTAGEM_REV → EM_TRANSITO_REV → AG_CONFERENCIA_REV → EX_CONFERENCIA_REV
   (loja)           (Correios)          (fila do CD)          (pessoa)
                                                                   ↓
                                                    CONFERIDA_REV | DIVERGENTE_REV
```

| Estado | Tipo no núcleo | Por quê |
|---|---|---|
| `AG_POSTAGEM_REV`, `EM_TRANSITO_REV` | `EXTERNO` | A bola está com a loja ou com os Correios |
| `AG_CONFERENCIA_REV` | `FILA` | O pacote está no CD e ninguém abriu |
| `EX_CONFERENCIA_REV` | `TRATATIVA` | Alguém está conferindo |

Dois prazos, dois culpados diferentes: **postagem atrasada** é a loja
(prazo acordado, em dias corridos, porque a loja não segue o expediente do
CD); **conferência atrasada** é o CD (dias úteis a partir da chegada).

### Custódia muda quando o recebedor confirma

"Entregue" nos Correios leva a coleta para a fila de conferência — não
fecha nada. Quem diz que chegou é o bipe de quem abriu a caixa, ou o
**Recebimento (A01)**: `registrar_recebimento()` é chamado pelo
`receipt_bulk_submit` e casa a série (ou etiqueta) recebida com o esperado
de qualquer coleta aberta, marcando a origem como `RECEBIMENTO` e levando a
coleta à fila de conferência. Recebimento avulso não abre coleta nem vira
inesperado: é recebimento, não divergência de coleta nenhuma.

Cancelar só vale enquanto nada chegou. Com equipamento recebido, cancelar
apagaria a evidência — o caminho é concluir como divergente.

### Verificação

```
python3 scripts/verificar_reversa.py
```

43 casos em banco temporário, com dublês no lugar do ServiceNow e dos
Correios.

---

## 2.3 Inventário e Contagem (A18)

`db/inventario.py` · `routers/inventario.py` · `static/modules/inventario.js` ·
banco `data/db/inventario.db`

O token é o **ciclo**: um recorte do estoque (começo do espaço/corredor —
`REP`, `IN`, `REP-A03` — ou vazio para o depósito inteiro), o **retrato**
do que o ServiceNow dizia estar lá e a contagem bipe a bipe.

O retrato é tirado **na abertura** e congelado em `inv_esperado`. Contar
contra o ServiceNow ao vivo faria o alvo se mover enquanto alguém conta.
Só o `install_status` vai na consulta; o corredor é filtrado em Python,
porque o JSONv2 aceita `STARTSWITH` sem reclamar e às vezes o ignora.

```
AG_CONTAGEM_INV → EX_CONTAGEM_INV → CONFERIDO_INV | DIVERGENTE_INV
```

| Diferença | O que é | Vira em A19 |
|---|---|---|
| No retrato, não contado | faltante | `FALTANTE` |
| Contado, fora do retrato, ServiceNow diz "em estoque em outro corredor" | local errado | `LOCAL_ERRADO` |
| Contado, fora do retrato, qualquer outra situação | inesperado | `INESPERADO` |

A sobra consulta o ServiceNow na hora do bipe e grava o que ele diz da
série — é essa informação que decide o tipo no fechamento e é o que quem
regularizar vai ler. Nada aqui escreve no ServiceNow.

---

## 2.4 Regularização de Ativo (A19)

`db/regularizacao.py` · `routers/regularizacao.py` ·
`static/modules/regularizacao.js` · banco `data/db/regularizacao.db`

O token é a **divergência**. O mecanismo inteiro são dois campos:
**responsável** e **prazo**. A divergência não sai da fila sem os dois
(`assumir`), e não fecha sem dizer **como** (`resolver`, com uma das
resoluções: encontrado, cadastro corrigido, baixado, devolvido pela loja,
perda reconhecida, duplicidade). Perda exige justificativa.

```
AG_TRATATIVA_REG → EX_TRATATIVA_REG → RESOLVIDA_REG
     (FILA)          (TRATATIVA)           ↓
                                      CANCELADA_REG (só admin)
```

Quem abre é outro processo ou uma pessoa:

```python
from routers.regularizacao import abrir_divergencia
abrir_divergencia(origem="A17", referencia="COL-2026-0001", tipo="FALTANTE",
                  serial="ABC123", usuario=login)
```

Idempotente por (origem, referência, série, tipo). A reversa e o
inventário chamam por import tardio dentro de `try/except`: a coleta ou o
ciclo já fecharam como divergentes, e isso não se desfaz porque o módulo
vizinho não respondeu.

Duas regras de permissão: assumir para si é `edit`; **atribuir a outra
pessoa**, reatribuir e cancelar são `admin` — distribuir trabalho é papel
do gestor. Resolver é do responsável ou de quem administra.

Dois alertas na fila, dois donos: **atrasada** (prazo vencido) é do
responsável; **sem dono há tempo demais** (`alerta_sem_dono_dias`) é do
gestor.

### Verificação

```
python3 scripts/verificar_regularizacao.py
python3 scripts/verificar_inventario.py
```

---

## 3. Parâmetros

**Parâmetros → Separação** (admin) reúne os dois módulos, porque hoje é a
Separação que usa o calendário:

- estoque, reserva, envio, chamado e prazos — do A15;
- **calendário de expediente** — do núcleo, vale para todos os módulos.

Cada linha do mapa de atendimento mostra a consulta que vai de fato para o
ServiceNow. Parâmetro que não deixa ver o que produz só serve depois do erro.


---

## 4. Como um estado novo entra no sistema

O ciclo é uma máquina de estados distribuída: cada módulo conhece as suas
entradas e saídas, e o núcleo não conhece nenhuma. Para acrescentar uma etapa:

1. Dê nome ao estado em `db/trilha.py`, no `ROTULO_ESTADO`. Sem isso o painel
   mostra `AG_COISA` em vez de "Aguardando coisa".
2. Diga a que frente ele pertence em `routers/torre.py`, no `FRENTE_DA_ETAPA`.
3. No módulo que produz o estado, chame `mover()` com o `processo` certo.
4. No módulo que o consome, leia a fila pelos intervalos abertos naquele
   estado — é assim que `bancada`, `preparacao`, `destinacao` e `externo`
   montam as filas deles.

O passo 4 é o que evita fila fantasma: um estado que ninguém lê vira ativo
parado para sempre, e foi exatamente o que aconteceu com `AG_CONFIGURACAO` e
`AG_ASSISTENCIA` entre um commit e outro.

---

## 5. O que ainda não existe

| Cód. | Processo | Situação |
|---|---|---|
| G01 a G14 | Bloco de gestão | só G02 (obsolescência de coletores) existe |
| T3 | Ponte ServiceNow | leitura e escrita existem; falta fila de reprocessamento na falha |
| T4 | Notificações | o canal de e-mail existe (`core/notificador.py`); falta a notificação por pedido |
| T5 | Perfis | os cinco níveis existem; falta o comportamento de ADMIN com justificativa nas telas |

Também em aberto, e que dependem de decisão fora do código:

- **Embalagem, NF e etiqueta** entre separar e despachar (`EX_EMBALAGEM`,
  `EX_FISCAL`, `AG_POSTAGEM`). Hoje o envio é um passo só, porque o portal não
  emite NF nem etiqueta.
- **Retenção dos anexos** de conformidade em `data/uploads/destinacao`. Estão
  no disco do servidor, com backup do servidor. Documento que a empresa
  apresenta em questionamento provavelmente merece política própria.
