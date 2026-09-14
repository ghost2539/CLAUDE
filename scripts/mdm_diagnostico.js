/* ============================================================
   MDM de Coletores — diagnóstico COMPLETO, em uma passada só

   Em vez de testar uma hipótese por vez, este script traz o material
   bruto necessário para montar o coletor inteiro:

     A. a grade: rodapé, colunas, formulário, paginador e UMA linha crua
     B. os nomes dos parâmetros, lidos do próprio HTML (sem adivinhar)
     C. paginação e tamanho de página, medidos pelo rodapé
     D. busca por SÉRIE de verdade
     E. detalhe de um coletor + atributos customizados
     F. lista de tags com os ids
     G. formulário de atribuição de tag (sem executar nada)

   SOMENTE LEITURA. Só faz GET. Não altera tag, não deleta, não grava.

   COMO USAR
   ---------
   1. Abra Devices > List View, com a grade na tela.
   2. F12 > Console > cole este arquivo > Enter.
   3. Espere terminar (~40s) e rode __mdmDiagSalvar().
   ============================================================ */

(function () {
    'use strict';

    var GRADE = '/AirWatch/Device/List/Search';
    var PAUSA = 300;
    var D = { gerado_em: new Date().toISOString(), origem: location.origin };
    window.__MDM_DIAG = D;

    function esperar(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

    async function get(url) {
        var r = await fetch(url, {
            credentials: 'include',
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        return { status: r.status, texto: await r.text() };
    }

    // ── extratores ─────────────────────────────────────────────
    var RE_ID = /Device\/Details\/Summary\/(\d+)/gi;
    var RE_ITENS = /Items?\s+([\d.,]+)\s*[-–]\s*([\d.,]+)\s+of\s+([\d.,]+)/i;

    function ids(t) {
        var s = {}, m; RE_ID.lastIndex = 0;
        while ((m = RE_ID.exec(t)) !== null) s[m[1]] = 1;
        return Object.keys(s);
    }

    function faixa(t) {
        var m = t.match(RE_ITENS);
        if (!m) return null;
        var n = function (x) { return parseInt(String(x).replace(/[.,]/g, ''), 10); };
        return { de: n(m[1]), ate: n(m[2]), total: n(m[3]) };
    }

    function fx(f) {
        return f ? f.de + '-' + f.ate + ' de ' + f.total + ' (' + (f.ate - f.de + 1) + '/pág)'
                 : 'sem rodapé';
    }

    // Nomes de campo do próprio HTML: é o contrato, não chute.
    function camposDoForm(t) {
        var fora = [], m;
        var re = /<(input|select)\b[^>]*\bname=["']([^"']+)["'][^>]*>/gi;
        while ((m = re.exec(t)) !== null && fora.length < 120) {
            var tag = m[0];
            var val = (tag.match(/\bvalue=["']([^"']*)["']/i) || [])[1];
            var tipo = (tag.match(/\btype=["']([^"']*)["']/i) || [])[1] || m[1];
            if (fora.some(function (x) { return x.nome === m[2]; })) continue;
            fora.push({ nome: m[2], tipo: tipo, valor: (val || '').slice(0, 40) });
        }
        return fora;
    }

    // Bloco do paginador: os links dele carregam o parâmetro de página.
    function blocoPaginador(t) {
        var i = t.search(/class=["'][^"']*(pagination|pager|grid_footer|paging)/i);
        if (i === -1) i = t.search(RE_ITENS);
        return i === -1 ? '' : t.slice(Math.max(0, i - 900), i + 2600);
    }

    function primeiraLinha(t) {
        var i = t.search(/<tr\b[^>]*data-|<tr\b[^>]*class=["'][^"']*row/i);
        if (i === -1) i = t.indexOf('<tbody');
        if (i === -1) return '';
        var f = t.indexOf('</tr>', i);
        return t.slice(i, f === -1 ? i + 4000 : Math.min(f + 5, i + 4000));
    }

    function th(t) {
        var fora = [], m, re = /<th\b[^>]*>([\s\S]*?)<\/th>/gi;
        while ((m = re.exec(t)) !== null && fora.length < 30) {
            var x = m[1].replace(/<[^>]*>/g, ' ').replace(/&nbsp;?/g, ' ').replace(/\s+/g, ' ').trim();
            if (x) fora.push(x.slice(0, 50));
        }
        return fora;
    }

    // Série: nos rótulos das linhas costuma vir junto do modelo.
    function series(t) {
        var fora = [], m;
        var re = /aria-label=["']([^"']{10,140})["']/gi;
        while ((m = re.exec(t)) !== null && fora.length < 6) fora.push(m[1]);
        return fora;
    }

    async function rodar() {
        console.log('%c[MDM] Diagnóstico completo. ~40s. Não mexa na tela.',
            'color:#0a7;font-weight:bold');

        // ── A. a grade
        var g = await get(GRADE);
        var base = faixa(g.texto);
        D.grade = {
            status: g.status, bytes: g.texto.length,
            fragmento: g.texto.indexOf('DeviceGrid') !== -1 && g.texto.indexOf('<html') === -1,
            rodape: base, linhas: ids(g.texto).length,
            colunas: th(g.texto),
            campos_form: camposDoForm(g.texto),
            rotulos_exemplo: series(g.texto),
            bloco_paginador: blocoPaginador(g.texto),
            linha_crua: primeiraLinha(g.texto)
        };
        console.log('%cGrade: ' + fx(base) + ' · ' + D.grade.linhas + ' linhas · fragmento=' +
            D.grade.fragmento, 'color:#06c;font-weight:bold');
        console.log('Campos do formulário achados no HTML:',
            D.grade.campos_form.map(function (x) { return x.nome; }).join(', ') || '(nenhum)');

        if (!D.grade.fragmento) {
            console.log('%c[MDM] Veio a página inteira, não a grade. Abra Devices > ' +
                'List View e rode de novo.', 'color:#fff;background:#c00;padding:2px 6px');
            return;
        }

        // ── B. candidatos: os do HTML primeiro, depois os conhecidos
        var doHtml = D.grade.campos_form
            .filter(function (c) { return /page|size|rows|skip|take|index|search/i.test(c.nome); })
            .map(function (c) { return c.nome; });
        var pagina = doHtml.filter(function (n) { return /page|index|skip|start/i.test(n) && !/size/i.test(n); })
            .map(function (n) { return n + '=2'; })
            .concat(['Page=2', 'PageNumber=2', 'pageNumber=2', 'CurrentPage=2', 'PageIndex=1', 'Skip=50']);
        var tamanho = doHtml.filter(function (n) { return /size|rows|take|limit/i.test(n); })
            .map(function (n) { return n + '=500'; })
            .concat(['PageSize=500', 'pageSize=500', 'Take=500', 'MaxRows=500']);

        console.log('%c── Paginação (medida pelo rodapé) ──', 'color:#666');
        D.paginacao = [];
        for (var i = 0; i < pagina.length; i++) {
            await esperar(PAUSA);
            var r = await get(GRADE + '?' + pagina[i]);
            var f = faixa(r.texto);
            var ok = !!(base && f && f.de > base.de);
            D.paginacao.push({ qs: pagina[i], rodape: f, paginou: ok });
            console.log((ok ? '%c★ ' : '%c  ') + pagina[i] + ' → ' + fx(f) + (ok ? '  <<< PAGINOU' : ''),
                ok ? 'color:#fff;background:#0a7;font-weight:bold' : 'color:#888');
        }

        console.log('%c── Tamanho de página ──', 'color:#666');
        D.tamanho = [];
        var porBase = base ? base.ate - base.de + 1 : 0;
        for (var j = 0; j < tamanho.length; j++) {
            await esperar(PAUSA);
            var t = await get(GRADE + '?' + tamanho[j]);
            var ft = faixa(t.texto);
            var por = ft ? ft.ate - ft.de + 1 : ids(t.texto).length;
            var ok2 = por >= porBase * 1.6;
            D.tamanho.push({ qs: tamanho[j], por_pagina: por, rodape: ft, cresceu: ok2 });
            console.log((ok2 ? '%c★ ' : '%c  ') + tamanho[j] + ' → ' + fx(ft) + (ok2 ? '  <<< AUMENTOU' : ''),
                ok2 ? 'color:#fff;background:#0a7;font-weight:bold' : 'color:#888');
        }

        // ── C. busca: com SÉRIE de verdade, tirada da linha crua
        console.log('%c── Busca ──', 'color:#666');
        var serie = '';
        var cand = (D.grade.linha_crua + ' ' + D.grade.rotulos_exemplo.join(' '))
            .match(/\b[A-Z0-9]{8,20}\b/g) || [];
        serie = cand.filter(function (x) { return /\d/.test(x) && /[A-Z]/.test(x); })[0] || '';
        var usuario = (D.grade.linha_crua.match(/[a-z]{2,6}\d+_coletor/i) || [])[0] || '';
        D.busca_alvo = { serie: serie, usuario: usuario };
        console.log('   alvos → série: ' + (serie || '(não achei)') + ' · usuário: ' + (usuario || '(não achei)'));

        D.busca = [];
        var nomes = ['SearchText', 'searchText', 'search'].concat(
            doHtml.filter(function (n) { return /search/i.test(n); }));
        for (var k = 0; k < nomes.length; k++) {
            for (var a = 0; a < 2; a++) {
                var alvo = a === 0 ? serie : usuario;
                if (!alvo) continue;
                await esperar(PAUSA);
                var b = await get(GRADE + '?' + nomes[k] + '=' + encodeURIComponent(alvo));
                var fb = faixa(b.texto);
                var tot = fb ? fb.total : ids(b.texto).length;
                var ok3 = tot > 0 && base && tot < base.total * 0.5;
                D.busca.push({ campo: nomes[k], alvo_tipo: a === 0 ? 'série' : 'usuário',
                               alvo: alvo, total: tot, filtrou: ok3 });
                console.log((ok3 ? '%c★ ' : '%c  ') + nomes[k] + '=' + (a === 0 ? 'SÉRIE' : 'USUÁRIO') +
                    ' → ' + fx(fb) + (ok3 ? '  <<< FILTROU' : ''),
                    ok3 ? 'color:#fff;background:#0a7;font-weight:bold' : 'color:#888');
            }
        }

        // ── D. detalhe do aparelho + atributos customizados
        var idAlvo = ids(D.grade.linha_crua)[0] || ids(g.texto)[0];
        D.detalhe = { id: idAlvo };
        if (idAlvo) {
            console.log('%c── Detalhe do coletor ' + idAlvo + ' ──', 'color:#666');
            var alvos = {
                summary: '/AirWatch/Device/Details/Summary/' + idAlvo,
                atributos: '/AirWatch/Devices/SearchCustomAttributesGrid?deviceId=' + idAlvo,
                tags_form: '/AirWatch/Devices/TagAssignment/' + idAlvo
            };
            for (var nome in alvos) {
                await esperar(PAUSA);
                try {
                    var d = await get(alvos[nome]);
                    D.detalhe[nome] = {
                        status: d.status, bytes: d.texto.length,
                        campos_form: camposDoForm(d.texto),
                        trecho: d.texto.slice(0, 2500)
                    };
                    console.log('   ' + nome + ': ' + d.status + ' · ' +
                        Math.round(d.texto.length / 1024) + ' KB · campos: ' +
                        (D.detalhe[nome].campos_form.map(function (x) { return x.nome; }).join(', ') || '—'));
                } catch (e) { D.detalhe[nome] = { erro: String(e) }; }
            }
        }

        // ── E. tags, com os ids
        await esperar(PAUSA);
        try {
            var tg = await get('/AirWatch/Device/List/TagsListSearch');
            var lista = JSON.parse(tg.texto);
            var querem = ['inatividade', 'manuten', 'movimenta'];
            D.tags = lista.filter(function (x) {
                var l = String(x.label || '').toLowerCase();
                return querem.some(function (q) { return l.indexOf(q) !== -1; });
            });
            D.tags_total = lista.length;
            console.log('%c── Tags de interesse ──', 'color:#666');
            D.tags.forEach(function (x) { console.log('   id=' + x.value + '  ' + x.label); });
        } catch (e) { D.tags = { erro: String(e) }; }

        console.log('%c[MDM] Pronto. Rode __mdmDiagSalvar() e mande o arquivo.',
            'color:#fff;background:#0a7;font-weight:bold;padding:2px 6px');
    }

    window.__mdmDiagSalvar = function () {
        var txt = JSON.stringify(D, null, 2);
        var a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([txt], { type: 'application/json' }));
        a.download = 'mdm-diagnostico.json';
        document.body.appendChild(a); a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
        console.log('[MDM] ' + Math.round(txt.length / 1024) + ' KB gerados.');
    };

    rodar().catch(function (e) { console.error('[MDM] Falhou:', e); });
})();
