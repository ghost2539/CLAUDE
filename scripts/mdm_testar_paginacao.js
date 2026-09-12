/* ============================================================
   MDM de Coletores — descobre COMO paginar e COMO buscar por série

   Em vez de capturar navegação e torcer para o parâmetro aparecer,
   este script pergunta direto ao servidor: repete a chamada da grade
   variando nomes de parâmetro conhecidos e compara o resultado.

   O que ele faz, e só isso:
   - GET em /AirWatch/Device/List/Search, algumas dezenas de vezes;
   - compara quais coletores voltaram em cada tentativa.

   É SOMENTE LEITURA. Não altera tag, não deleta, não salva nada.

   COMO USAR
   ---------
   1. Abra o MDM logado, em qualquer tela.
   2. F12 > Console > cole este arquivo > Enter.
   3. Espere terminar (~30s) e me mande o que ele imprimir —
      ou rode __mdmPagSalvar() para baixar o resultado.
   ============================================================ */

(function () {
    'use strict';

    var BASE = '/AirWatch/Device/List/Search';
    // Cada linha da grade tem um link para o detalhe com o id do aparelho.
    // Contar isso é contar LINHA; contar usuário não serve — o mesmo usuário
    // atende vários aparelhos e ainda aparece nos filtros do fragmento.
    var RE = /Device\/Details\/Summary\/(\d+)/gi;
    var PAUSA = 350;          // respira entre chamadas, para não martelar
    var resultado = { base: null, paginacao: [], tamanho: [], busca: [] };

    function esperar(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

    function conjunto(txt) {
        var s = {}, m;
        RE.lastIndex = 0;
        while ((m = RE.exec(txt)) !== null) { s[m[1]] = 1; }
        return Object.keys(s);
    }

    // O rodapé da grade diz "Items 1 - 50 of 15819". Este é o sinal EXATO:
    // não depende de quais aparelhos vieram, e a lista é ordenada por Last
    // Seen e se reordena sozinha entre chamadas — comparar conjuntos mente.
    var RE_ITENS = /Items?\s+([\d.,]+)\s*[-–]\s*([\d.,]+)\s+of\s+([\d.,]+)/i;

    function faixa(txt) {
        var m = txt.match(RE_ITENS);
        if (!m) return null;
        var n = function (x) { return parseInt(String(x).replace(/[.,]/g, ''), 10); };
        return { de: n(m[1]), ate: n(m[2]), total: n(m[3]) };
    }

    function faixaTexto(f) {
        return f ? (f.de + '-' + f.ate + ' de ' + f.total + ' (' + (f.ate - f.de + 1) + ' por página)')
                 : 'rodapé não encontrado';
    }

    // O console é ASP.NET MVC: sem o cabeçalho de AJAX ele devolve a PÁGINA
    // INTEIRA em vez do fragmento da grade — e aí ignora todo parâmetro.
    // Foi o que aconteceu na primeira tentativa (3,2 MB, o console todo).
    async function pegar(qs) {
        var r = await fetch(BASE + (qs ? '?' + qs : ''), {
            credentials: 'include',
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        var t = await r.text();
        return {
            qs: qs || '(sem parâmetro)', status: r.status,
            itens: conjunto(t), bytes: t.length, faixa: faixa(t),
            fragmento: t.indexOf('DeviceGrid') !== -1 && t.indexOf('<html') === -1
        };
    }

    function iguais(a, b) {
        if (a.length !== b.length) return false;
        var s = {}; a.forEach(function (x) { s[x] = 1; });
        return b.every(function (x) { return s[x]; });
    }

    function sobreposicao(a, b) {
        var s = {}; a.forEach(function (x) { s[x] = 1; });
        return b.filter(function (x) { return s[x]; }).length;
    }

    // Nomes usados por grades ASP.NET/AirWatch. Barato testar todos.
    var PAGINA = ['Page=2', 'page=2', 'PageNumber=2', 'pageNumber=2',
                  'CurrentPage=2', 'currentPage=2', 'PageIndex=1', 'pageIndex=1',
                  'Skip=50', 'skip=50', 'StartIndex=50', 'startIndex=50'];
    var TAMANHO = ['PageSize=100', 'pageSize=100', 'pagesize=100',
                   'Take=100', 'take=100', 'Limit=100', 'limit=100', 'MaxRows=100'];

    async function rodar() {
        console.log('%c[MDM] Testando paginação e busca. Aguarde…', 'color:#0a7;font-weight:bold');

        var base = await pegar('');
        await esperar(PAUSA);
        var base2 = await pegar('');        // mesma chamada, de novo
        var deriva = base.itens.length
            ? Math.round(100 * (1 - sobreposicao(base.itens, base2.itens) / base.itens.length))
            : 0;
        resultado.base = base;
        resultado.deriva_pct = deriva;
        console.log('%cRuído de fundo: duas chamadas idênticas diferem em ' + deriva +
            '% — a grade é ordenada por Last Seen e se move sozinha, por isso ' +
            'a conclusão usa o rodapé, não os aparelhos.',
            deriva > 20 ? 'color:#c60' : 'color:#888');
        console.log('%cRodapé da base: ' + faixaTexto(base.faixa),
            base.faixa ? 'color:#06c;font-weight:bold' : 'color:#c00;font-weight:bold');
        if (!base.faixa) {
            console.log('%c[MDM] Não achei o rodapé "Items X - Y of Z". A conclusão ' +
                'abaixo cai para contagem de linhas, que é menos confiável.', 'color:#c60');
        }
        console.log('%cBase: ' + base.itens.length + ' coletor(es), ' +
            Math.round(base.bytes / 1024) + ' KB, fragmento da grade: ' + base.fragmento,
            'color:#06c;font-weight:bold');
        if (!base.itens.length) {
            console.log('%c[MDM] A base não trouxe coletor nenhum — abra a lista de ' +
                'Devices uma vez e rode de novo.', 'color:#c00');
            return;
        }
        if (!base.fragmento) {
            console.log('%c[MDM] ATENÇÃO: veio a página inteira, não o fragmento da ' +
                'grade. Os parâmetros vão ser ignorados e o teste não vale. ' +
                'Abra Devices > List View e rode de novo a partir dela.',
                'color:#fff;background:#c00;font-weight:bold;padding:2px 6px');
        }

        console.log('%c── Qual parâmetro vira a página? ──', 'color:#666');
        for (var i = 0; i < PAGINA.length; i++) {
            await esperar(PAUSA);
            var r = await pegar(PAGINA[i]);
            // Paginou = o rodapé começa em outro item. Sinal exato.
            var paginou = !!(base.faixa && r.faixa && r.faixa.de > base.faixa.de);
            resultado.paginacao.push({ qs: r.qs, linhas: r.itens.length,
                                       faixa: r.faixa, paginou: paginou });
            console.log((paginou ? '%c★ ' : '%c  ') + r.qs + ' → ' + faixaTexto(r.faixa) +
                (paginou ? '   <<< PAGINOU' : ''),
                paginou ? 'color:#fff;background:#0a7;font-weight:bold' : 'color:#888');
        }

        console.log('%c── Qual parâmetro muda o tamanho da página? ──', 'color:#666');
        for (var j = 0; j < TAMANHO.length; j++) {
            await esperar(PAUSA);
            var t = await pegar(TAMANHO[j]);
            // Aumentou = o rodapé passa a cobrir mais itens por página.
            var porPag = t.faixa ? (t.faixa.ate - t.faixa.de + 1) : t.itens.length;
            var porPagBase = base.faixa ? (base.faixa.ate - base.faixa.de + 1) : base.itens.length;
            var cresceu = porPag >= porPagBase * 1.6;
            resultado.tamanho.push({ qs: t.qs, por_pagina: porPag, faixa: t.faixa, cresceu: cresceu });
            console.log((cresceu ? '%c★ ' : '%c  ') + t.qs + ' → ' + faixaTexto(t.faixa) +
                (cresceu ? '   <<< AUMENTOU' : ''),
                cresceu ? 'color:#fff;background:#0a7;font-weight:bold' : 'color:#888');
        }

        // Busca por série: usa uma série real, tirada da própria base.
        console.log('%c── Qual parâmetro busca por série? ──', 'color:#666');
        // Pega um usuário real do próprio fragmento para usar como alvo.
        var htmlBase = await (await fetch(BASE, { credentials: 'include',
            headers: { 'X-Requested-With': 'XMLHttpRequest' } })).text();
        var mu = htmlBase.match(/[a-z]{2,6}\d+_coletor/i);
        var alvo = mu ? mu[0] : '';
        if (!alvo) { console.log('   (não achei um alvo para buscar)'); return; }
        console.log('   (procurando por um coletor conhecido: ' + alvo + ')');
        var BUSCA = ['SearchText=', 'searchText=', 'searchtext=', 'search=',
                     'Search=', 'SerialNumber=', 'serialNumber=', 'Serial='];
        for (var k = 0; k < BUSCA.length; k++) {
            await esperar(PAUSA);
            var b = await pegar(BUSCA[k] + encodeURIComponent(alvo));
            // Filtrar de verdade é cair para pouquíssimas linhas, não oscilar.
            // Filtrou = o TOTAL do rodapé despencou (não o que veio na página).
            var totalB = b.faixa ? b.faixa.total : b.itens.length;
            var totalBase = base.faixa ? base.faixa.total : base.itens.length;
            var filtrou = totalB > 0 && totalB < totalBase * 0.5;
            resultado.busca.push({ qs: BUSCA[k], total: totalB, faixa: b.faixa, filtrou: filtrou });
            console.log((filtrou ? '%c★ ' : '%c  ') + BUSCA[k] + '… → ' + faixaTexto(b.faixa) +
                (filtrou ? '   <<< FILTROU' : ''),
                filtrou ? 'color:#fff;background:#0a7;font-weight:bold' : 'color:#888');
        }

        console.log('%c[MDM] Fim. Rode __mdmPagSalvar() para baixar, ou copie o que apareceu acima.',
            'color:#0a7;font-weight:bold');
        window.__MDM_PAG = resultado;
    }

    window.__mdmPagSalvar = function () {
        var txt = JSON.stringify(resultado, null, 2);
        var a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([txt], { type: 'application/json' }));
        a.download = 'mdm-paginacao.json';
        document.body.appendChild(a); a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    };

    rodar().catch(function (e) { console.error('[MDM] Falhou:', e); });
})();
