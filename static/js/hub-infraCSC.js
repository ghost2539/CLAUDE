/* Hub Infra CSC - TI — comportamento da tela.
 *
 * Fica em arquivo separado, e não inline no HTML, porque a CSP do portal
 * traz `script-src 'self'`: <script> inline é BLOQUEADO pelo navegador, sem
 * erro visível na tela. O sintoma é cruel — o CSS aplica (style-src permite
 * inline), então a página abre preta, tipografada e com o título no lugar,
 * só que a lista nunca é montada. Parece problema de dados; é de política.
 *
 * Carregado no <head> SEM defer: a parte do tema tem de rodar antes da
 * primeira pintura, ou a página nasce escura e pisca para o claro. O resto
 * espera o DOMContentLoaded, porque nessa hora o <body> ainda não existe.
 */

/* Antes da primeira pintura: sem isto a página nasce escura e pisca para o
   claro quando o script do fim do corpo roda. Padrão é escuro. */
(function () {
  try {
    var t = localStorage.getItem("csc-tema");
    if (t === "claro" || t === "escuro") document.documentElement.dataset.tema = t;
  } catch (e) { /* navegação privada bloqueia localStorage; segue no padrão */ }
})();

document.addEventListener("DOMContentLoaded", function () {
  "use strict";

  /* ── A LISTA ──────────────────────────────────────────────────────────
     É só editar aqui para incluir, tirar ou reordenar um portal. Campos:

       marca  desenho: spare | field-adm | field-cd | orcamento | estoques
       nome   o que aparece em destaque
       sub    uma linha dizendo para que serve
       href   endereço completo — "" enquanto o sistema não existir
       grupo  operacoes | gestao
       cor    opcional; só o Spare usa, por ter identidade própria
     ------------------------------------------------------------------ */
  var PORTAIS = [
    { marca:"spare",     nome:"Spare",
       sub:"Ciclo de vida do equipamento de loja",
       href:"https://suporte.lojasrenner.com.br/portal-spare/",
       grupo:"operacoes", cor:"spare" },

    { marca:"field-adm", nome:"Field ADM",
       sub:"Controle de equipamentos do administrativo",
       href:"https://suporte.lojasrenner.com.br/controle_equipamentos/login.php",
       grupo:"operacoes" },

    { marca:"field-cd",  nome:"Field CD",
       sub:"Operação de equipamentos no centro de distribuição",
       href:"",
       grupo:"operacoes" },

    { marca:"orcamento", nome:"Controle de Orçamento",
       sub:"Previsto, comprometido e realizado da Infra CSC",
       href:"https://suporte.lojasrenner.com.br/portal-spare/controle-orcamento-InfraCSC/",
       grupo:"gestao" },

    { marca:"estoques",  nome:"Estoques de TI",
       sub:"Posição por item e depósito, entradas e saídas",
       href:"",
       grupo:"gestao" }
  ];

  var GRUPOS = [
    { id:"operacoes", titulo:"Operações", classe:"op", acento:"#C79105" },
    { id:"gestao",    titulo:"Gestão",    classe:"ge", acento:"#246F68" }
  ];

  var MARCAS = {
        "spare": "M6 6 L40 6 L47 10 L40 14 L14 14 L14 20 L42 20 L42 42 L8 42 L1 38 L8 34 L34 34 L34 28 L6 28 Z",
        "field-adm": "M8 8 L40 8 L40 45 L8 45 Z M13 13 L35 13 L35 40 L13 40 Z M19 3 L29 3 L29 11 L19 11 Z M17 19 L31 19 L31 23 L17 23 Z M17 28 L26 28 L26 32 L17 32 Z",
        "field-cd": "M24 3 L46 15 L46 21 L24 9 L2 21 L2 15 Z M6 26 L22 26 L22 45 L6 45 Z M26 26 L42 26 L42 45 L26 45 Z",
        "orcamento": "M4 40 L44 40 L44 45 L4 45 Z M6 28 L17 28 L17 37 L6 37 Z M19 18 L30 18 L30 37 L19 37 Z M32 6 L43 6 L43 37 L32 37 Z",
        "estoques": "M24 3 L44 13 L24 23 L4 13 Z M4 17 L22 26 L22 45 L4 36 Z M26 26 L44 17 L44 36 L26 45 Z"
  };

  /* Tudo entra por createElement/textContent — nunca innerHTML com texto da
     lista. Um nome de portal com "<" ou "&" quebraria a página montada por
     concatenação, e é o tipo de erro que só aparece no dia em que alguém
     cadastra "P&D". */
  function elemento(tag, classe, texto) {
    var e = document.createElement(tag);
    if (classe) e.className = classe;
    if (texto !== undefined && texto !== null) e.textContent = texto;
    return e;
  }

  function marcaSvg(nome) {
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 48 48");
    svg.setAttribute("aria-hidden", "true");   /* o nome ao lado já é o rótulo */
    var p = document.createElementNS(NS, "path");
    p.setAttribute("d", MARCAS[nome] || "");
    svg.appendChild(p);
    return svg;
  }

  function montarLinha(portal, classeGrupo) {
    var disponivel = !!portal.href;
    /* Sem endereço não vira <a>: um link para "" recarrega a própria página,
       o que parece defeito. Vira <div>, sem seta e com etiqueta. */
    var linha = document.createElement(disponivel ? "a" : "div");
    linha.className = "linha " + (portal.cor === "spare" ? "spare" : classeGrupo) +
                      (disponivel ? "" : " parado");
    if (disponivel) {
      linha.href = portal.href;
      linha.setAttribute("aria-label", portal.nome + " — " + portal.sub);
    }

    var cel = elemento("span");
    cel.appendChild(marcaSvg(portal.marca));
    linha.appendChild(cel);
    linha.appendChild(elemento("span", "linha-nome", portal.nome));
    linha.appendChild(elemento("span", "linha-sub", portal.sub));
    linha.appendChild(disponivel
        ? elemento("span", "linha-seta", "\u2192")
        : elemento("span", "etiqueta", "Em construção"));
    return linha;
  }

  function montar() {
    var raiz = document.getElementById("grupos");
    raiz.style.display = "flex";
    raiz.style.flexDirection = "column";
    raiz.style.gap = "56px";

    var total = 0;
    GRUPOS.forEach(function (g) {
      var doGrupo = PORTAIS.filter(function (p) { return p.grupo === g.id; });
      if (!doGrupo.length) return;          /* grupo vazio não vira cabeçalho órfão */
      total += doGrupo.length;

      var bloco = elemento("div", "grupo");
      var cab = elemento("div", "grupo-cab");
      var quadro = elemento("span", "grupo-quadro");
      quadro.style.background = g.acento;
      cab.appendChild(quadro);
      cab.appendChild(elemento("span", "grupo-nome", g.titulo));
      cab.appendChild(elemento("span", "grupo-regua"));
      cab.appendChild(elemento("span", "grupo-conta",
          doGrupo.length + (doGrupo.length === 1 ? " sistema" : " sistemas")));
      bloco.appendChild(cab);

      doGrupo.forEach(function (p) {
        bloco.appendChild(montarLinha(p, g.classe));
        bloco.appendChild(elemento("div", "separador"));
      });
      raiz.appendChild(bloco);
    });

    document.getElementById("contagem-total").textContent =
        total + (total === 1 ? " sistema" : " sistemas");
  }

  /* ── Tema ─────────────────────────────────────────────────────────── */
  var botao = document.getElementById("toggle-tema");

  function aplicarTema(tema) {
    document.documentElement.dataset.tema = tema;
    var escuro = tema === "escuro";
    botao.setAttribute("aria-checked", String(escuro));
    botao.title = escuro ? "Mudar para o tema claro" : "Mudar para o tema escuro";
    /* Em janela anônima o localStorage lança em vez de devolver vazio; a
       página tem de continuar funcionando, só sem lembrar a escolha. */
    try { localStorage.setItem("csc-tema", tema); } catch (e) {}
  }

  botao.addEventListener("click", function () {
    aplicarTema(document.documentElement.dataset.tema === "escuro" ? "claro" : "escuro");
  });

  aplicarTema(document.documentElement.dataset.tema || "escuro");
  montar();
});
