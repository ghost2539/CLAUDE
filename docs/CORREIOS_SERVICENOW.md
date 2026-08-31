# Integrações ServiceNow e Correios — notas técnicas

Estado validado em produção: commit `c77f203` (2026-08-31).

Este documento registra as descobertas que custaram caro para achar. Se a
integração parar de funcionar, comece por aqui antes de mexer no código.

---

## Onde ficam as variáveis de ambiente

**Não é o `.env` do projeto.** O app não usa `python-dotenv`; quem carrega as
variáveis é o systemd, a partir de:

```
/etc/portal_operacoes_spare/environment
```

Editar o `.env` do diretório do projeto não surte efeito nenhum. Depois de
alterar o arquivo acima:

```bash
sudo systemctl restart portal_spare.service
```

Variáveis relevantes:

| Variável | Uso |
|---|---|
| `SN_PROXY` | Proxy de saída para ServiceNow e Correios |
| `CORREIOS_USUARIO` | Usuário da API dos Correios |
| `CORREIOS_CHAVE` | Chave de acesso (não é a senha de login) |
| `CORREIOS_CARTOES` | Cartões de postagem, separados por vírgula |
| `CORREIOS_CONTRATO` | Opcional — elevação por contrato em vez de cartão |
| `CORREIOS_DR` | Diretoria Regional (64) |

---

## ServiceNow — limitações do JSONv2

A API JSONv2 (`/table.do?JSONv2&sysparm_action=getRecords`) é usada porque
funciona com os cookies do SSO. Ela tem armadilhas:

**Operadores de texto não funcionam.** `LIKE`, `!LIKE`, `NOT LIKE`,
`STARTSWITH` e `ENDSWITH` são aceitos sem erro, mas ignorados ou retornam
vazio. Já foram testados todos, um a um. O único confiável é `ISNOTEMPTY`.

Consequência: **qualquer filtro por conteúdo de texto tem que ser feito em
Python**, depois de trazer os registros. É o que `chamados_correios` faz com o
regex `[A-Z]{2}\d{9}[A-Z]{2}` sobre `correlation_display`.

**Paginação é obrigatória.** `sysparm_record_count` limita o retorno de uma
chamada; para varrer a fila inteira é preciso percorrer as páginas com
`sysparm_first_row`. Use `_sn_query_all()`, não `_sn_query()` direto, quando
o filtro for aplicado em Python — senão os registros que interessam podem
estar além da primeira página e simplesmente não aparecem.

Esse foi o motivo de "a busca não acha os chamados com rastreio": eles
estavam fundo na fila, atrás de centenas de chamados `AG.`.

---

## Correios — autenticação em duas etapas

O rastreio (`/srorastro/v1/objetos`) exige um **token elevado**. O fluxo:

1. `POST /token/v1/autentica` — `Authorization: Basic <usuario:chave>`, sem
   corpo. Retorna 201 com o token básico.
2. `POST /token/v1/autentica/cartaopostagem` — **também `Authorization: Basic`**,
   com `{"numero": "<cartão>"}` no corpo. Retorna o token com escopo.
3. `GET /srorastro/v1/objetos` — `Authorization: Bearer <token elevado>`.

**A etapa 2 usa Basic, não Bearer.** Enviar o Bearer do token básico devolve
`GTW-014: ... Utilize 'Authorization: Basic'`. Esse foi o erro que derrubou a
integração por vários dias.

### Interpretando os erros do gateway

| Código | Significado real |
|---|---|
| `GTW-014` | Cabeçalho de autorização errado na elevação — use Basic |
| `GTW-012` | Token sem escopo para a API pedida (normalmente porque a elevação falhou e o código caiu no token básico) |

`GTW-012` no rastreio quase sempre é **sintoma**: a causa está uma etapa
antes, na elevação. A mensagem de erro do `correios_rastrear` já traz as
tentativas de elevação junto — leia essa parte primeiro.

---

## Rede

O servidor **não alcança a internet diretamente** (porta 443 bloqueada). Tanto
o ServiceNow quanto os Correios saem pelo proxy em `SN_PROXY`.

O proxy roda numa estação de trabalho, então o IP muda por DHCP e o serviço
cai quando a máquina desliga. Sintoma típico: `ConnectTimeout` para o IP do
proxy. Conferir o IP atual e atualizar `SN_PROXY`.

O login SSO e as chamadas dos Correios tentam o proxy e, se ele não responder,
repetem em conexão direta — o erro resultante mostra as duas tentativas, o que
distingue "proxy fora" de "443 bloqueada".

Diagnóstico rápido, sem passar pela tela de login:

```
GET /api/servicenow/proxy-check
```

Testa proxy e conexão direta contra o ServiceNow e diz qual responde.

Solução definitiva (quando houver abertura com a equipe de rede): liberar a
saída do servidor para `renner.service-now.com:443` e `api.correios.com.br:443`,
e então deixar `SN_PROXY` vazio.

---

## Endpoints de diagnóstico

| Endpoint | Para que serve |
|---|---|
| `GET /api/servicenow/proxy-check` | Proxy e conexão direta estão de pé? |
| `GET /api/servicenow/chamados-correios/debug` | Varre a fila e separa com/sem rastreio, mostrando os valores brutos de `correlation_display` |
| `POST /api/servicenow/correios/test` | Percorre cada etapa da autenticação dos Correios e mostra request/response |

Todos exigem sessão do portal — abra pelo navegador logado, não por `curl`
sem cookie (retorna 401).
