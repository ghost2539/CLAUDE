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
    var RE = /[a-z]{2,6}\d+_coletor/gi;
    var PAUSA = 350;          // respira entre chamadas, para não martelar
    var resultado = { base: null, paginacao: [], tamanho: [], busca: [] };

    function esperar(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

    function conjunto(txt) {
        var m = txt.match(RE) || [];
        var s = {};
        m.forEach(function (x) { s[x.toLowerCase()] = 1; });
        return Object.keys(s);
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
            itens: conjunto(t), bytes: t.length,
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
        resultado.base = base;
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
            var mudou = !iguais(base.itens, r.itens);
            var comuns = sobreposicao(base.itens, r.itens);
            resultado.paginacao.push({ qs: r.qs, itens: r.itens.length, comuns: comuns, mudou: mudou });
            console.log((mudou && comuns === 0 ? '%c★ ' : '%c  ') + r.qs +
                ' → ' + r.itens.length + ' coletor(es), ' + comuns + ' em comum com a base' +
                (mudou && comuns === 0 ? '   <<< PAGINOU' : ''),
                mudou && comuns === 0 ? 'color:#fff;background:#0a7;font-weight:bold' : 'color:#888');
        }

        console.log('%c── Qual parâmetro muda o tamanho da página? ──', 'color:#666');
        for (var j = 0; j < TAMANHO.length; j++) {
            await esperar(PAUSA);
            var t = await pegar(TAMANHO[j]);
            var cresceu = t.itens.length > base.itens.length;
            resultado.tamanho.push({ qs: t.qs, itens: t.itens.length, cresceu: cresceu });
            console.log((cresceu ? '%c★ ' : '%c  ') + t.qs + ' → ' + t.itens.length +
                ' coletor(es)' + (cresceu ? '   <<< AUMENTOU' : ''),
                cresceu ? 'color:#fff;background:#0a7;font-weight:bold' : 'color:#888');
        }

        // Busca por série: usa uma série real, tirada da própria base.
        console.log('%c── Qual parâmetro busca por série? ──', 'color:#666');
        var alvo = base.itens[0];
        console.log('   (procurando por um coletor conhecido: ' + alvo + ')');
        var BUSCA = ['SearchText=', 'searchText=', 'searchtext=', 'search=',
                     'Search=', 'SerialNumber=', 'serialNumber=', 'Serial='];
        for (var k = 0; k < BUSCA.length; k++) {
            await esperar(PAUSA);
            var b = await pegar(BUSCA[k] + encodeURIComponent(alvo));
            var filtrou = b.itens.length > 0 && b.itens.length < base.itens.length;
            resultado.busca.push({ qs: BUSCA[k], itens: b.itens.length, filtrou: filtrou });
            console.log((filtrou ? '%c★ ' : '%c  ') + BUSCA[k] + '… → ' + b.itens.length +
                ' coletor(es)' + (filtrou ? '   <<< FILTROU' : ''),
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
