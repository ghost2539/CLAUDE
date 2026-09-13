# Trilha do Ativo e Separação

Contrato dos dois módulos que implementam o desenho de processos da área.
A Trilha é o núcleo — mede tempo. A Separação é o primeiro processo montado
em cima dela.

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
| Enviar | `install_status = 1` e `location` = loja de destino |
| Cancelar | `substatus = available` nas unidades já bipadas |

A ordem no bipe importa e está explícita no código: **localizar, reservar, e só
então gravar**. Se o ServiceNow recusar a reserva, nada fica registrado no
portal — o contrário deixaria a unidade contada como separada e ainda visível
no catálogo de todo mundo.

Cancelar sem soltar a reserva deixaria equipamento invisível no saldo para
sempre; por isso o cancelamento libera unidade por unidade.

### O que ainda não existe

- **A16 (Projetos de loja).** A aba de Inauguração e Reforma usa esta mesma
  tela por enquanto. O A16 tem token próprio (o item do projeto), a
  configuração do Leandro no meio e estados de exceção que pausam o relógio.
- **Embalagem, NF e etiqueta.** O documento prevê `EX_EMBALAGEM`, `EX_FISCAL` e
  `AG_POSTAGEM` entre separar e despachar. Hoje o envio é um passo só.

---

## 3. Parâmetros

**Parâmetros → Separação** (admin) reúne os dois módulos, porque hoje é a
Separação que usa o calendário:

- estoque, reserva, envio, chamado e prazos — do A15;
- **calendário de expediente** — do núcleo, vale para todos os módulos.

Cada linha do mapa de atendimento mostra a consulta que vai de fato para o
ServiceNow. Parâmetro que não deixa ver o que produz só serve depois do erro.
