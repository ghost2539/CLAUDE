# Fluxo do ativo no Spare

O caminho que um equipamento faz da porta do CD até sair, e o que cada
tela mede. Este documento é o contrato: se a tela e ele discordarem, um
dos dois está errado.

## Ninguém assume equipamento

Não existe "assumir". O ativo cai sozinho no **backlog** do processo e o
relógio dele corre ali. Quando alguém bipa ou informa a série e registra
o que fez, esse registro é a **saída**: despacha o ativo para a próxima
etapa no mesmo ato.

Do que isso é feito, na prática:

- o bipe apenas traz o equipamento para a tela (leitura, não movimento);
- o registro é o que move — e é obrigatório;
- o que se mede é **tempo parado** (do backlog ao despacho), **volume por
  pessoa e por dia**, e **volume diário e mensal**.

Não há mais relógio de custódia, e por isso saiu o *saving* por hora de
bancada: ele dependia de um tempo de posse que não existe mais.

## O caminho

    Recebimento ─┬─ venda direta ────────────────────────► Venda de Ativos
                 │
                 └─ triagem (subcategoria) ─► Central de Reparos
                                               Frente e Retaguarda
                                               Mobilidade
                                               Conectividade
                                                    │
                                     ┌──────────────┼──────────────┐
                                     ▼              ▼              ▼
                                   Venda      Assistência   Internalização
                                                Externa            │
                                                                   ▼
                                                             Disponível
                                                          (envio às lojas)

### Recebimento — a porta de entrada

Cada ativo recebido sai daqui com um destino:

- **Venda direta** — vai para a fila de venda e não passa por reparo.
- **Triagem** — exige a **subcategoria**, que é o que escolhe a bancada.
  A lista de subcategorias fica em Configuração → Configuração Módulos →
  Recebimento; cada uma aponta para Mobilidade, Frente e Retaguarda ou
  Conectividade. Subcategoria fora da lista recusa o envio, antes de
  gravar qualquer coisa.

O que foi decidido fica no detalhe da movimentação do núcleo.

### Central de Reparos — as três bancadas

Registrar **o que foi realizado no equipamento é obrigatório**, qualquer
que seja o destino. As três bancadas destinam para os mesmos lugares:

| Destino | Para onde vai | Exige |
|---|---|---|
| Venda | fila de venda | justificativa |
| Assistência externa | fila da assistência | fornecedor |
| Internalização | fila de internalização | — |

Duas saídas continuam existindo fora desse trio: **aguardando peça**, que
é pausa (o ativo continua sendo da bancada e só sai de lá pelo bipe de
retorno), e **devolver ao terceiro**, para o que não é nosso (comodato,
locação, garantia).

### Venda de Ativos

A área negocia por **ciclo trimestral**, não peça a peça. O ativo espera
na fila de venda; alguém o inclui no ciclo aberto (um por vez); o ciclo
fecha para negociação; e a conclusão registra comprador, documento e
valor, e **baixa** os ativos — encerramento na Trilha, que é o que para o
relógio de verdade.

### Internalização

Internalizado não é o que a tela declara: é o que o **ServiceNow mostra**.
O ativo só fica disponível para envio às lojas quando o cadastro trouxer

- estoque **SPARE - CD324** (`estoque_internalizacao`), e
- espaço e corredor contendo **INA** ou **REP** (`padroes_corredor`).

Enquanto não trouxer, ele fica na fila com o motivo visível — o cadastro
que está lá aparece na mensagem. A tela tem "Conferir a fila no
ServiceNow": o cadastro pode ser ajustado depois, e quem já estiver certo
passa a disponível sem ninguém bipar de novo. Conferência física
reprovada devolve o equipamento para a bancada, com motivo.

## Estados no núcleo

| Etapa | Estado |
|---|---|
| Backlog de Mobilidade e Frente e Retaguarda | `AG_TRIAGEM` |
| Backlog de Conectividade | `AG_TRIAGEM_CONECT` |
| Aguardando peça | `AG_PECAS` |
| Fila de venda | `AG_VENDA` |
| Dentro de um ciclo de venda | `AG_VENDA_CICLO` |
| Fila da assistência | `AG_ASSISTENCIA` |
| Fila de internalização | `AG_INTERNALIZACAO` |
| Pronto para envio | `DISPONIVEL` |
| Vendido (encerra) | `VENDIDO` |

Os estados de custódia do fluxo antigo (`EX_REPARO`, `EX_CONFIGURACAO`,
`EX_INTERNALIZACAO`…) não são mais produzidos. Ativo que ficou num deles
continua aparecendo na tela, como "fluxo antigo", para alguém tratar.

## Verificações

    python3 scripts/verificar_recebimento_triagem.py   # porta de entrada
    python3 scripts/verificar_bancada.py               # backlog e despacho
    python3 scripts/verificar_venda.py                 # ciclo e baixa
    python3 scripts/verificar_preparacao.py            # internalização
