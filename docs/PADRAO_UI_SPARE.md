# Padrão de UI — Portal de Operações Spare (CSC TI)

Referência única para replicar o design em todos os módulos.
Implementado em `static/app.css` (tokens `--sp-*` e componentes) e no shell de `static/index.html` / `static/app.js`; os módulos consomem a biblioteca, não copiam estilo.

## 1. Cores (extraídas do template LRSA 2025 — não inventar novas)

| Token | Hex | Uso |
|---|---|---|
| `--sp-black` | `#000000` | Sidebar, header escuro, ground do login |
| `--sp-ink-900` | `#0C0C0C` | Fundo de página no tema escuro |
| `--sp-ink-800` | `#141414` | Superfície de card/tabela (escuro) |
| `--sp-ink-700` | `#1F1F1F` / `#232323` | Bordas e divisores (escuro) |
| `--sp-white` | `#FFFFFF` | Superfície e texto principal invertido |
| `--sp-accent` | `#AB4807` | Ação primária, item ativo, foco, alerta de SLA |
| `--sp-accent-hi` | `#C05B12` | Hover do acento (escuro) |
| `--sp-accent-lo` | `#8E3B05` | Hover do acento (claro) |
| `--sp-gold` | `#C79105` | Atenção, contagens secundárias, badge de orçamento |
| `--sp-sand` | `#E9D39B` | Destaque tipográfico sobre preto |
| `--sp-teal` | `#246F68` | Sucesso, integração ok, concluído |

Neutros de texto — claro: `#000000` / `#3D3D3D` / `#7A7A7A`; escuro: `#FFFFFF` / `#B5B5B5` / `#8C8C8C`.
Regra: acento nunca preenche área grande; é ação, linha ou borda. Um só acento por tela.

## 2. Tipografia

- **Arial** (institucional Renner) para títulos, rótulos de UI e botões.
- **IBM Plex Mono** para números, patrimônio, matrícula, status, versão e labels técnicos em caixa-alta com `letter-spacing: .14em`.
- Escala: página 26px bold · seção 18px bold · corpo 13–14px · label 9–10px mono caixa-alta.
- `font-variant-numeric: tabular-nums` em toda coluna numérica.

## 3. Grid e espaçamento

- Sidebar fixa 236px, preta, sempre presente.
- Header 62px com breadcrumb à esquerda e status + usuário à direita.
- Conteúdo: padding 28px 32px, gap vertical 22px entre blocos.
- Cantos retos (`border-radius: 0`) em todo o sistema. Sem sombras; separação é borda de 1px.
- Grades de KPI e tabela usam `gap:1px` sobre fundo da cor da borda — o divisor é o próprio gap.

## 4. Componentes canônicos

1. **Sidebar de ciclo** — numeração 01…N, item ativo com borda-esquerda 3px acento + fundo `#141414`, badge mono dourado para pendências.
2. **Header de contexto** — breadcrumb mono, pílula de status de integrações (bolinha teal), avatar quadrado 28px acento com iniciais + matrícula.
3. **Cabeçalho de módulo** — kicker mono acento ("Módulo 04"), H1, ações à direita (secundária outline + primária sólida acento).
4. **Faixa de KPIs** — 2 a 5 cartões, label mono caixa-alta + número 26px mono. Cor do número: branco/preto por padrão, acento quando é desvio, teal quando é resultado.
5. **Tabela de trabalho** — busca + chips de filtro no topo, header mono caixa-alta, linhas 14px com hover, badge de status retangular, paginação mono no rodapé.
6. **Badge de status** — retângulo sem raio, mono 10px caixa-alta. Trânsito neutro · Assistência acento · Orçamento dourado · Aprovado/Concluído teal.
7. **Campos** — borda 1px, sem raio, foco `2px solid #AB4807` com `outline-offset: 2px`. Nunca o foco azul do navegador.

## 5. Temas

- **Login: sempre escuro.** É a porta de entrada e carrega a marca.
- **Módulos: claro e escuro, escolha do usuário.** Botão Claro/Escuro no header, à esquerda do status de integrações.
- A sidebar é preta nos dois temas — é a âncora de marca, não muda.
- A preferência é salva por usuário no perfil (`PUT /api/auth/preferencias`, chave `pref:<login>` em `settings`); `localStorage['spare-tema']` é só cache para não piscar ao abrir. Vale para todos os módulos.
- Cada módulo troca apenas a camada de superfície/borda/texto. Estrutura, grid e componentes são idênticos.

Tokens por tema:

| Papel | Claro | Escuro |
|---|---|---|
| Fundo de página | `#F4F4F4` | `#0C0C0C` |
| Superfície | `#FFFFFF` | `#141414` |
| Header | `#FFFFFF` | `#000000` |
| Header de tabela | `#FAFAFA` | `#0F0F0F` |
| Borda | `#E0E0E0` | `#232323` |
| Divisor de linha | `#EFEFEF` | `#1F1F1F` |
| Hover de linha | `#FAFAFA` | `#191919` |
| Texto | `#000000` | `#FFFFFF` |
| Texto secundário | `#4F4F4F` | `#B5B5B5` |
| Texto de apoio | `#7A7A7A` | `#8C8C8C` |
| Acento de ação | `#AB4807` (hover `#8E3B05`) | `#AB4807` (hover `#C05B12`) |
| Alerta de SLA | `#AB4807` | `#E0782F` |
| Sucesso | `#246F68` | `#5FB8AC` |
| Verde institucional em texto/botão secundário | `#1C5A54` (borda `#CBDCD9`) | `#5FB8AC` (borda `#24443F`) |

Onde o verde entra: ação secundária positiva (Exportar, entrar com conta corporativa), status de integração/ambiente e KPI de resultado concluído. Nunca como ação primária — essa é sempre `#AB4807`.

## 6. Conteúdo e voz

- Sistema interno: sem linguagem de marketing, sem explicar o portal.
- Rótulos no vocabulário da operação: patrimônio, OS, loja, SLA, remessa, assistência.
- Rodapé/sidebar sempre com versão, ambiente e "CSC TI - Spare".
- Aviso de acesso monitorado apenas na tela de login.

## 7. Plano de aplicação por módulo

| Fase | Escopo | Entregável |
|---|---|---|
| 0 | Fundação | Tokens de cor/tipo/espaço + componentes 1–7 como biblioteca compartilhada |
| 1 | Login | `Login SPARE v2` em produção |
| 2 | Recebimento e Internalização | Lista + detalhe + formulário de entrada |
| 3 | Manutenção e Reparo externo | Lista + remessa + acompanhamento de SLA |
| 4 | Devolução | Lista + confirmação de envio à loja |
| 5 | Consultas e painéis | Telas de consulta e KPIs consolidados |
| 6 | Integrações e RPAs | Telas de status de job, log e reprocessamento |

Critério de aceite por módulo: usa a sidebar e o header padrão; nenhum hex fora da tabela da seção 1; números em mono tabular; foco visível acento; tema claro com sidebar preta; nenhum raio de borda.

## 8. Onde está no código

- Seção 1 e tokens por tema: bloco `:root` / `:root[data-tema="escuro"]` em `static/app.css`.
- Seção 4: classes `.sidebar*`, `.topbar*`, `.page-title`/`.kicker`, `.stats-grid`/`.stat-card`, `.table-wrapper`/`.data-table`, `.badge-*`, `.form-control`, `.btn-*` em `static/app.css`.
- Alternador de tema e trilha do header: `aplicarTema`/`atualizarTrilha` em `static/app.js`.
- Módulo novo: usa as classes acima e nenhum hex; gráfico em SVG lê o token com `getComputedStyle` (atributo SVG não aceita `var()`), como em `static/modules/orcamento_manutencao.js`.
