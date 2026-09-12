/* ============================================================
   MDM de Coletores (Workspace ONE UEM / AirWatch)
   Captura e análise dos dados que a tela carrega.

   PARA QUE SERVE
   --------------
   Descobrir QUAIS endpoints a tela chama e QUAL o formato dos dados
   que voltam. Com isso o portal passa a buscar essas informações em
   segundo plano e montar a tela de Obsolescência do parque.

   COMO USAR
   ---------
   1. Abra a tela no navegador, já logado no MDM:
      https://cn258.awmdm.com/AirWatch/aa/#/devices/updates/platform/list/windows
   2. Tecle F12 e vá na aba "Console".
   3. Cole este arquivo INTEIRO e tecle Enter (aparece "captura ligada").
   4. Use a tela normalmente, para que ela carregue dados:
      - recarregue a página (F5) e espere a lista aparecer;
      - troque o filtro de plataforma / status;
      - passe para a página 2 da lista;
      - clique em um coletor para abrir o detalhe.
   5. Rode os comandos abaixo no mesmo Console:
      __mdmResumo()   -> lista os endpoints chamados e quantos registros vieram
      __mdmSchema()   -> mostra os campos (nome e tipo) de cada resposta
      __mdmSalvar()   -> baixa um .json com a análise, para me enviar
      __mdmParar()    -> desliga a captura

   PRIVACIDADE — leia antes de rodar
   ---------------------------------
   Nada sai do seu navegador. O script só guarda em memória; o arquivo
   é salvo por você, manualmente, no seu computador. Antes de gravar:
   - cabeçalhos e campos de autenticação (cookie, token, senha, apikey)
     são descartados;
   - por padrão só 2 registros de exemplo por endpoint vão para o
     arquivo — o resto vira contagem. Assim a análise mostra o FORMATO
     dos dados sem exportar o parque inteiro.
   ============================================================ */

(function () {
    'use strict';

    if (window.__MDM_CAPT) {
        console.log('%c[MDM] A captura já estava ligada. Use __mdmResumo().', 'color:#c47f00');
        return;
    }

    // Quantos registros de exemplo guardar por endpoint. O objetivo é
    // descobrir o formato, não exportar a base — por isso o número é baixo.
    var MAX_AMOSTRAS = 2;
    var MAX_TEXTO = 300;      // corte de strings longas na amostra
    var MAX_PROF = 6;         // profundidade máxima ao mapear o schema

    // Chaves que nunca entram na captura: são credenciais ou identificadores
    // de sessão, e não têm nenhum valor para a análise do formato.
    var SENSIVEL = /(authoriz|cookie|token|senha|password|passwd|secret|api[-_]?key|sessionid|jsessionid|aw[-_]?tenant|bearer|credential)/i;

    var reg = [];
    window.__MDM_CAPT = reg;

    // ── utilitários ────────────────────────────────────────────

    function cortar(s) {
        s = String(s);
        return s.length > MAX_TEXTO ? s.slice(0, MAX_TEXTO) + '…(+' + (s.length - MAX_TEXTO) + ')' : s;
    }

    function jsonSeguro(txt) {
        if (txt == null || txt === '') return null;
        if (typeof txt === 'object') return txt;
        try { return JSON.parse(txt); } catch (_) { return null; }
    }

    // Remove chaves sensíveis e encurta a estrutura, preservando o formato.
    function podar(v, prof) {
        prof = prof || 0;
        if (v === null || v === undefined) return null;
        if (prof > MAX_PROF) return '…';
        if (Array.isArray(v)) return v.slice(0, MAX_AMOSTRAS).map(function (x) { return podar(x, prof + 1); });
        if (typeof v === 'object') {
            var o = {};
            Object.keys(v).forEach(function (k) {
                if (SENSIVEL.test(k)) { o[k] = '«removido»'; return; }
                o[k] = podar(v[k], prof + 1);
            });
            return o;
        }
        if (typeof v === 'string') return cortar(v);
        return v;
    }

    // Descreve o formato: para cada campo, o tipo e um exemplo curto.
    function inferir(v, prof) {
        prof = prof || 0;
        if (prof > MAX_PROF) return '…';
        if (v === null || v === undefined) return 'nulo';
        if (Array.isArray(v)) {
            return {
                '«lista»': v.length + ' item(ns)',
                '«item»': v.length ? inferir(v[0], prof + 1) : 'lista vazia'
            };
        }
        if (typeof v === 'object') {
            var o = {};
            Object.keys(v).forEach(function (k) {
                o[k] = SENSIVEL.test(k) ? '«removido»' : inferir(v[k], prof + 1);
            });
            return o;
        }
        if (typeof v === 'string') {
            var ex = cortar(v);
            return 'texto · ex: ' + (ex.length > 60 ? ex.slice(0, 60) + '…' : ex);
        }
        if (typeof v === 'number') return 'número · ex: ' + v;
        if (typeof v === 'boolean') return 'booleano · ex: ' + v;
        return typeof v;
    }

    // Acha a lista principal da resposta (Devices, data, results, rows...).
    function acharLista(corpo) {
        if (!corpo || typeof corpo !== 'object') return null;
        if (Array.isArray(corpo)) return { campo: '(raiz)', qtd: corpo.length };
        var achado = null;
        Object.keys(corpo).forEach(function (k) {
            if (achado) return;
            if (Array.isArray(corpo[k]) && corpo[k].length) achado = { campo: k, qtd: corpo[k].length };
        });
        if (achado) return achado;
        // às vezes vem aninhado um nível (ex.: { Page: { Devices: [...] } })
        Object.keys(corpo).forEach(function (k) {
            if (achado || !corpo[k] || typeof corpo[k] !== 'object') return;
            var dentro = acharLista(corpo[k]);
            if (dentro) achado = { campo: k + '.' + dentro.campo, qtd: dentro.qtd };
        });
        return achado;
    }

    function urlCurta(u) {
        try {
            var a = new URL(u, location.origin);
            return a.pathname + (a.search ? a.search.slice(0, 160) : '');
        } catch (_) { return String(u).slice(0, 200); }
    }

    // ── registro das chamadas ──────────────────────────────────

    function registrar(metodo, url, status, corpoReq, corpoResp) {
        // Só interessa o que devolve dados; recursos estáticos poluem a análise.
        if (/\.(js|css|png|jpe?g|gif|svg|woff2?|ttf|ico|map)(\?|$)/i.test(url)) return;
        var resp = jsonSeguro(corpoResp);
        if (resp === null && !/json|api|odata|list|device|update/i.test(url)) return;

        var lista = acharLista(resp);
        var item = {
            quando: new Date().toISOString(),
            metodo: String(metodo || 'GET').toUpperCase(),
            url: urlCurta(url),
            url_completa: String(url),
            status: status,
            registros: lista ? lista.qtd : (resp ? 1 : 0),
            campo_lista: lista ? lista.campo : null,
            requisicao: podar(jsonSeguro(corpoReq)),
            amostra: podar(resp),
            schema: inferir(resp)
        };
        reg.push(item);
        if (item.registros > 0) {
            console.log('%c[MDM] ' + item.metodo + ' ' + item.url +
                ' → ' + item.registros + ' registro(s)' +
                (item.campo_lista ? ' em "' + item.campo_lista + '"' : ''),
                'color:#0a7');
        }
    }

    // ── ganchos (XHR e fetch) ──────────────────────────────────

    var xhrOpen = XMLHttpRequest.prototype.open;
    var xhrSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function (metodo, url) {
        this.__mdm = { metodo: metodo, url: url };
        return xhrOpen.apply(this, arguments);
    };

    XMLHttpRequest.prototype.send = function (corpo) {
        var self = this;
        if (self.__mdm) {
            self.addEventListener('loadend', function () {
                var txt = null;
                try {
                    if (!self.responseType || self.responseType === 'text') txt = self.responseText;
                    else if (self.responseType === 'json') txt = self.response;
                } catch (_) { txt = null; }
                try { registrar(self.__mdm.metodo, self.__mdm.url, self.status, corpo, txt); } catch (_) {}
            });
        }
        return xhrSend.apply(this, arguments);
    };

    var fetchOrig = window.fetch;
    if (fetchOrig) {
        window.fetch = function (entrada, init) {
            var url = (typeof entrada === 'string') ? entrada : (entrada && entrada.url) || '';
            var metodo = (init && init.method) || (entrada && entrada.method) || 'GET';
            var corpoReq = init && init.body;
            return fetchOrig.apply(this, arguments).then(function (resp) {
                try {
                    resp.clone().text().then(function (t) {
                        try { registrar(metodo, url, resp.status, corpoReq, t); } catch (_) {}
                    }, function () {});
                } catch (_) {}
                return resp;
            });
        };
    }
    window.__mdmFetchOrig = fetchOrig;
    window.__mdmXhrOpen = xhrOpen;
    window.__mdmXhrSend = xhrSend;

    // ── comandos para o usuário ────────────────────────────────

    // Lista o que foi capturado, do que traz mais dados para o que traz menos.
    window.__mdmResumo = function () {
        if (!reg.length) {
            console.log('%c[MDM] Nada capturado ainda. Recarregue a tela (F5) e espere a lista carregar.', 'color:#c47f00');
            return;
        }
        var porUrl = {};
        reg.forEach(function (x) {
            var c = porUrl[x.url] || (porUrl[x.url] = { chamadas: 0, registros: 0, metodo: x.metodo, campo: x.campo_lista, status: x.status });
            c.chamadas++;
            c.registros = Math.max(c.registros, x.registros);
        });
        var linhas = Object.keys(porUrl).map(function (u) {
            return {
                metodo: porUrl[u].metodo, endpoint: u, chamadas: porUrl[u].chamadas,
                registros: porUrl[u].registros, campo_lista: porUrl[u].campo, status: porUrl[u].status
            };
        }).sort(function (a, b) { return b.registros - a.registros; });
        console.table(linhas);
        console.log('%c[MDM] ' + reg.length + ' chamada(s) capturada(s). Use __mdmSchema() para ver os campos e __mdmSalvar() para baixar.', 'color:#0a7');
        return linhas;
    };

    // Mostra os campos das respostas que trouxeram dados.
    window.__mdmSchema = function (filtro) {
        var alvos = reg.filter(function (x) { return x.registros > 0; });
        if (filtro) alvos = alvos.filter(function (x) { return x.url.indexOf(filtro) !== -1; });
        if (!alvos.length) { console.log('%c[MDM] Nenhuma resposta com dados' + (filtro ? ' para "' + filtro + '"' : '') + '.', 'color:#c47f00'); return; }
        var vistos = {};
        alvos.forEach(function (x) {
            if (vistos[x.url]) return;
            vistos[x.url] = true;
            console.groupCollapsed('%c' + x.metodo + ' ' + x.url + '  (' + x.registros + ' registro[s])', 'color:#06c');
            console.log(x.schema);
            console.groupEnd();
        });
        return Object.keys(vistos);
    };

    // Baixa a análise em .json — é este arquivo que deve ser enviado.
    window.__mdmSalvar = function (nome) {
        var pacote = {
            gerado_em: new Date().toISOString(),
            origem: location.origin,
            tela: location.href,
            observacao: 'Amostra limitada a ' + MAX_AMOSTRAS + ' registro(s) por endpoint; campos de credencial removidos.',
            total_chamadas: reg.length,
            chamadas: reg
        };
        var txt = JSON.stringify(pacote, null, 2);
        var a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([txt], { type: 'application/json' }));
        a.download = nome || ('mdm-analise-' + new Date().toISOString().slice(0, 19).replace(/[:T]/g, '') + '.json');
        document.body.appendChild(a);
        a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
        console.log('%c[MDM] Arquivo gerado (' + Math.round(txt.length / 1024) + ' KB). Confira o conteúdo antes de enviar.', 'color:#0a7');
        return a.download;
    };

    window.__mdmLimpar = function () { reg.length = 0; console.log('[MDM] Captura zerada.'); };

    window.__mdmParar = function () {
        XMLHttpRequest.prototype.open = window.__mdmXhrOpen;
        XMLHttpRequest.prototype.send = window.__mdmXhrSend;
        if (window.__mdmFetchOrig) window.fetch = window.__mdmFetchOrig;
        console.log('%c[MDM] Captura desligada. Os dados já capturados continuam em __MDM_CAPT.', 'color:#c47f00');
    };

    console.log('%c[MDM] Captura ligada.', 'color:#0a7;font-weight:bold');
    console.log('%cAgora recarregue a tela (F5), espere a lista carregar, pagine e abra um coletor.\n' +
        'Depois rode: __mdmResumo()  →  __mdmSchema()  →  __mdmSalvar()', 'color:#666');
})();
