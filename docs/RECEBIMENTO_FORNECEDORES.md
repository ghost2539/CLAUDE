# Recebimento de Fornecedores → Lançamento → chamado no ServiceNow

Contrato do fluxo novo: o que cada tela mostra, o que cada rota devolve e
onde cada dado fica. Se código e este documento discordarem, um dos dois
está errado.

## O caminho

    Agendamento (já existe, não muda)
        │  PO(s) + NF(s) + equipamentos, BU, fornecedor
        ▼
    Recebimento → Fornecedores            [permissão: recebimento]
        │  lista os agendamentos; "Iniciar Recebimento"
        │  Renner/Camicado: itens vêm da PO no EBS (PO não achada = erro, para)
        │  Youcom: itens digitados à mão
        │  só itens da lista de IMOBILIZADOS contam; o resto é só pagamento
        │  quantidade recebida por item + seriais (um por unidade, colagem em massa)
        │  NF: chave de acesso (busca o XML na SEFAZ) ou arquivo (Youcom)
        │  parcial = recebido < pedido; segue se bater com a NF
        │  consome etiquetas (uma por unidade) e abre o processo
        ▼
    Internalização → Lançamento            [permissão: internalizacao]
        │  linhas já preenchidas: etiqueta, local, serial, item, descrição, PO/linha, NF
        │  "OK": gera a planilha Cadastro de Ativos (formato do CSC Lançamentos)
        │  e abre o chamado no ServiceNow COMO O USUÁRIO LOGADO, anexando NF + planilha
        ▼
    Patrimônio → Entrada de Equipamento    (já existem, não mudam)

## Regras

- **Só imobilizado** entra na conferência, consome etiqueta e vai ao
  lançamento. A lista é `Internalização → Itens Imobilizados` (código do
  item no EBS). Itens da PO fora da lista aparecem só como informação.
- **Serial obrigatório** em toda BU, um por unidade recebida. A tela aceita
  colagem em massa (um por linha ou separados por vírgula) e distribui nas
  linhas; só avisa o que ficou faltando.
- **Entrega parcial** não trava: fica marcada no recebimento. Se a NF diz
  a quantidade (XML lido) e o recebido bate com ela, segue; se não bate, a
  tela diz quanto a nota traz e não segue. O restante do pedido, quando
  vier, é outro agendamento (outra NF).
- **PO não encontrada no EBS** (Renner/Camicado): erro na tela, o
  recebimento não começa. Youcom não consulta o EBS.
- **Etiqueta faltando**: a confirmação diz quantas faltam e não segue.
- **Arquivos** (XML, PDF da NF, planilha) ficam em `data/tmp/<área>/<id>/`
  e são apagados **5 dias corridos** depois de gerados, sem agendador: a
  limpeza roda ao abrir as telas do fluxo.
- **Certificado A1** por BU (Renner e Camicado), caminho e senha no
  `data/environment` com chave própria (`NFE_CERT_<BU>_PFX/SENHA`), nunca
  junto de Correios ou EBS. Youcom: a NF entra por upload.
- **ServiceNow**: leitura do item de catálogo e abertura do chamado com os
  cookies SSO do usuário logado. Conta de serviço não participa.

## Modelo

Banco dos Agendamentos (`db/agendamentos_forn.py`), escrito pelo módulo
dele (`routers/agendamentos_forn.py::registrar_recebimento`):

| Tabela | O que guarda |
|---|---|
| `agf_recebimento` | um por agendamento: quando, quem, `entrega_parcial`, observação, `tem_nao_imobilizados` |
| `agf_recebimento_item` | por item conferido: `po`, `linha`, `item_ebs`, `descricao`, `unidade`, `quantidade_pedida`, `quantidade_nf`, `quantidade_recebida`, `imobilizado` |
| `agf_recebimento_unidade` | uma por unidade: `item_id`, `serial` |
| `agf_nota` | por NF do agendamento: `nf`, `chave` (44), `vencimento`, `origem` (`SEFAZ`/`ARQUIVO`/`DIGITADA`), `xml_arquivo`, `pdf_arquivo`, `itens_json`, `emitente`, `cstat`, `erro` |

Banco da Internalização (`db/internalizacao.py`), escrito pelo módulo dele
(`routers/internalizacao.py::abrir_processo_do_recebimento`):

- `int_processo` ganha `planilha_arquivo`, `planilha_em`, `sn_request_number`,
  `sn_request_sys_id`, `sn_ritm_number`, `sn_ritm_sys_id`, `sn_enviado_em`,
  `sn_erro`, `lancado_por`, `lancado_em`.
- `int_ativo` ganha `po`, `linha`, `nf`, `etiqueta_id`, `etiqueta_local`,
  `item_id_recebimento`.
- `int_etiqueta.situacao` vira `CONSUMIDA` com `ativo_id` e `consumida_em`
  na mesma transação que cria o ativo.

## API — `/api/recebimento/fornecedores` (permissão `recebimento`)

`GET ?status=&busca=` → `{total, itens: [agendamento resumido]}`. Cada item:
`id, bu, fornecedor, estoque_destino_rotulo, data_agendada, volumes, status,
status_rotulo, data_recebimento, entrega_parcial, tem_ebs, pedidos: [{po, nf}],
equipamentos: [{descricao, quantidade}]`. Sem filtro: AGENDADO primeiro,
depois os RECEBIDO mais recentes.

`GET /{id}/preparar` → monta a conferência. Resposta:

```json
{
  "agendamento": {"id": 7, "bu": "Renner", "fornecedor": "…", "pedidos": [{"po": "2570313-25", "nf": "200153"}], "…": "…"},
  "tem_ebs": true,
  "itens": [
    {"po": "2570313-25", "nf": "200153", "linha": 1, "item_ebs": "347191",
     "descricao": "ZEBRA IMPRESSORA INDUSTRIAL ZT231", "unidade": "UN",
     "quantidade_pedida": 10, "quantidade_recebida_ebs": 0, "quantidade_pendente": 10,
     "quantidade_nf": 10, "imobilizado": true}
  ],
  "nao_imobilizados": [{"po": "…", "nf": "…", "linha": 2, "item_ebs": "990001", "descricao": "CABO USB", "quantidade_pedida": 10}],
  "notas": [{"nf": "200153", "chave": "", "vencimento": "", "origem": "", "tem_xml": false, "tem_pdf": false, "itens": [], "erro": ""}],
  "etiquetas_disponiveis": 42,
  "certificado_bu": true,
  "avisos": ["…"]
}
```
Youcom: `tem_ebs=false`, `itens` vêm dos equipamentos do agendamento com
`item_ebs=""`, `linha=null`, `imobilizado=true`, `manual=true`; a tela deixa
editar item, descrição, quantidade e o marcador de imobilizado, e incluir
linhas. Erros: `409` já recebido; `404` PO não encontrada (diz qual);
`503` sem credencial do EBS; `502` EBS recusou; `422` nenhum item
imobilizado na PO (diz para cadastrar em Itens Imobilizados).

`POST /{id}/nota` body `{"nf": "200153", "chave": "3525…(44)", "vencimento": "2026-10-30"}`
→ grava a nota; com chave e certificado da BU, busca o XML na SEFAZ e
preenche `itens`, `vencimento` (se vazio) e gera o PDF. Devolve a nota
como em `notas[]`. Falha na SEFAZ não é erro HTTP: volta em `erro`.

`POST /{id}/nota/arquivo` multipart `nf`, `arquivo` (XML ou PDF, até 10 MB)
→ mesma resposta; XML dá itens exatos, PDF dá itens heurísticos.

`POST /{id}/confirmar` body:
```json
{"itens": [{"po": "…", "linha": 1, "item_ebs": "347191", "descricao": "…", "unidade": "UN",
            "quantidade_pedida": 10, "quantidade_recebida": 8, "imobilizado": true,
            "seriais": ["T3N1", "T3N2", "…"]}],
 "observacao": ""}
```
Valida (tudo antes de gravar): item imobilizado com `len(seriais) ==
quantidade_recebida`; serial sem repetição (na conferência e na base);
se a NF tem itens lidos, recebido == quantidade da NF por item (senão `422`
dizendo quanto a nota traz); etiquetas disponíveis ≥ unidades imobilizadas
(senão `409` "faltam N etiquetas"). Depois: grava o recebimento, marca o
agendamento `RECEBIDO` (+ `entrega_parcial`), consome as etiquetas e abre
o processo com um ativo por unidade. Resposta:
`{ok, entrega_parcial, faltantes: [{descricao, faltam}], processo_id,
etiquetas_consumidas, agendamento}`.

`GET /{id}/nota/{nf}/pdf` e `/xml` → baixa o arquivo guardado.

## API — Lançamento (`/api/internalizacao`, permissão `internalizacao`)

`GET /{agendamento_id}` já devolve os ativos; passam a vir com `po`, `linha`,
`nf`, `etiqueta_local`. `PUT /{id}` continua salvando (serial pode ser
corrigido). Novo:

`POST /{agendamento_id}/lancar` → gera a planilha em
`data/tmp/lancamentos/<id>/Cadastro_de_Ativos_NF_<nf>.xlsx`, abre o
chamado no ServiceNow (usuário logado), anexa planilha + PDF(s) da NF,
marca `CONCLUIDA` e leva os ativos à etapa Patrimônio. Falha no ServiceNow
NÃO desfaz: o processo fica `CONCLUIDA` com `sn_erro`, e o botão vira
"Reenviar ao ServiceNow" (`POST /{id}/lancar/reenviar`).

`GET /{agendamento_id}/planilha` → baixa a planilha gerada.

`GET /catalogo-sn/conferir` → descreve o item de catálogo como o usuário
logado (nome, tipo, obrigatoriedade, opções de cada variável; o bloco
multilinha das NFs). Só leitura; serve para conferir o mapa antes do
primeiro envio.

## Chamado no ServiceNow — item `66831e771b37b5901e870e9fe54bcb11`

| Variável | Valor |
|---|---|
| `requested_for` | usuário logado |
| `u_phone` | `+55 (11) 93098-4829` só se editável |
| `u_impact_employee` | `lucas.gonzaga@lojasrenner.com.br` |
| `question_brand` | Renner → `Renner Brasil`; Camicado → `Camicado`; Youcom → `Youcom` |
| `question_document_type` | `Material` |
| `question_demand_type` | `Others` |
| `question_has_a_purchase_order` | `Yes`/`Sim` |
| `question_supplier` | fornecedor do agendamento |
| `question_nature_of_the_transaction` | `Fixed assets (CAPEX)` |
| `question_the_document_is_being_sent_after_the_deadline` | `No`/`Não` |
| `question_number_of_documents_being_sent` | quantidade de NFs (padrão 1) |
| bloco "Add" (multilinha), uma linha por NF | `question_order_number_po`, `question_document_number`, `question_due_date` |
| `question_description` | `Prezados, gentileza enviar pra lançamento e cadastro de patrimonio a {NF} referente à PO {PO com liberação}.` |
| anexos | PDF da NF + planilha Cadastro de Ativos |

Opções (`Renner Brasil`, `Material`…) são resolvidas pelo RÓTULO contra as
opções que o próprio item devolve: o valor interno pode ser outro.

## Planilha "Cadastro de Ativos" (formato do CSC Lançamentos)

Aba `Placa Patrimonial`; linhas 1–4 vazias; **B5** `Placa Patrimonial (N° do
bem)`; linha 6: `Item | Descrição do item | Plaqueta | Número de série | Nf`;
dados da linha 7. Cabeçalho e título Calibri 12 negrito sobre `C00000`
(texto na cor padrão), dados Calibri 11; tudo centrado, bordas finas;
coluna D com formato `0`; larguras A 7.6 / B 123.3 / C 14.1 / D 28.9 /
E 8.1; alturas 5 e 6 = 15.6, 7 = 12.75; congelamento em `A7`. Gerador
próprio em `core/planilha_cadastro_ativos.py` — a exportação antiga da
Internalização não muda.
