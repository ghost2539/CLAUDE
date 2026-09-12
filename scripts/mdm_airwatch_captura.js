/* ============================================================
   MDM de Coletores (Workspace ONE / AirWatch)
   Captura e análise dos dados que a tela carrega.

   PARA QUE SERVE
   --------------
   Descobrir QUAIS endpoints a tela chama e QUAL o formato dos dados
   que voltam. Com isso o portal passa a buscar essas informações em
   segundo plano e montar a tela de Obsolescência do parque.

   AGUENTA RECARREGAMENTO
   ----------------------
   A tela do MDM recarrega ao entrar em "Devices", ao pesquisar e ao
   abrir um coletor — e um script de console morre junto com a página.
   Por isso o que já foi capturado fica guardado no próprio navegador
   (localStorage) e NÃO se perde. Depois de cada recarregamento, basta
   colar este arquivo de novo: ele continua de onde parou, somando.

   Mais prático ainda: em vez de recolar, salve como Snippet
   (F12 > Sources > Snippets > New snippet > colar > Ctrl+Enter).
   Aí é só apertar Ctrl+Enter depois de cada recarregamento.

   COMO USAR
   ---------
   1. Abra a tela do MDM, já logado.
   2. F12 > aba "Console" > cole este arquivo > Enter.
   3. Faça o caminho todo, recolando após cada recarregamento:
      entrar > Devices > pesquisar > abrir um coletor > paginar.
   4. Ao final, rode:
      __mdmResumo()   -> endpoints chamados e quantos registros vieram
      __mdmSchema()   -> os campos de cada resposta
      __mdmSalvar()   -> baixa o .json com a análise, para me enviar
      __mdmLimpar()   -> zera tudo e recomeça
      __mdmParar()    -> desliga a captura desta página

   PRIVACIDADE — leia antes de rodar
   ---------------------------------
   Nada sai do seu navegador: o script só guarda localmente e o arquivo
   é salvo por você. Cookies, tokens e senhas são descartados antes de
   gravar, e só 2 registros de exemplo por endpoint entram no arquivo —
   o bastante para ver o FORMATO, sem exportar o parque inteiro.
   ============================================================ */

(function () {
    'use strict';

    var CHAVE = '__mdm_capturas';
    var MAX_AMOSTRAS = 2;
    var MAX_TEXTO = 300;
    var MAX_PROF = 6;

    var SENSIVEL = /(authoriz|cookie|token|senha|password|passwd|secret|api[-_]?key|sessionid|jsessionid|aw[-_]?tenant|bearer|credential)/i;
    var ESTATICO = /\.(js|css|png|jpe?g|gif|svg|woff2?|ttf|eot|ico|map)(\?|$)/i;

    // ── memória que atravessa o recarregamento ─────────────────
    function carregar() {
        try { return JSON.parse(localStorage.getItem(CHAVE)) || {}; }
        catch (_) { return {}; }
    }

    var mapa = carregar();
    var gravacaoPendente = null;

    function gravar() {
        if (gravacaoPendente) return;
        gravacaoPendente = setTimeout(function () {
            gravacaoPendente = null;
            try {
                localStorage.setItem(CHAVE, JSON.stringify(mapa));
            } catch (_) {
                // Estourou o espaço: solta as amostras e fica só com o formato,
                // que é o que realmente interessa para a análise.
                Object.keys(mapa).forEach(function (k) { delete mapa[k].amostra; });
                try { localStorage.setItem(CHAVE, JSON.stringify(mapa)); } catch (__) {}
            }
        }, 400);
    }

    if (window.__MDM_LIGADO) {
        console.log('%c[MDM] A captura desta página já estava ligada (' +
            Object.keys(mapa).length + ' endpoint[s] acumulado[s]).', 'color:#c47f00');
        return;
    }
    window.__MDM_LIGADO = true;
    window.__MDM_CAPT = mapa;

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

    function podar(v, prof) {
        prof = prof || 0;
        if (v === null || v === undefined) return null;
        if (prof > MAX_PROF) return '…';
        if (Array.isArray(v)) return v.slice(0, MAX_AMOSTRAS).map(function (x) { return podar(x, prof + 1); });
        if (typeof v === 'object') {
            var o = {};
            Object.keys(v).forEach(function (k) {
                o[k] = SENSIVEL.test(k) ? '«removido»' : podar(v[k], prof + 1);
            });
            return o;
        }
        if (typeof v === 'string') return cortar(v);
        return v;
    }

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

    function acharLista(corpo) {
        if (!corpo || typeof corpo !== 'object') return null;
        if (Array.isArray(corpo)) return { campo: '(raiz)', qtd: corpo.length };
        var achado = null;
        Object.keys(corpo).forEach(function (k) {
            if (!achado && Array.isArray(corpo[k]) && corpo[k].length) achado = { campo: k, qtd: corpo[k].length };
        });
        if (achado) return achado;
        Object.keys(corpo).forEach(function (k) {
            if (achado || !corpo[k] || typeof corpo[k] !== 'object' || Array.isArray(corpo[k])) return;
            var dentro = acharLista(corpo[k]);
            if (dentro) achado = { campo: k + '.' + dentro.campo, qtd: dentro.qtd };
        });
        return achado;
    }

    function partes(u) {
        try {
            var a = new URL(u, location.origin);
            return { caminho: a.pathname, consulta: a.search.slice(1, 300) };
        } catch (_) { return { caminho: String(u).slice(0, 200), consulta: '' }; }
    }

    // ── registro ───────────────────────────────────────────────
    // ── respostas em HTML ──────────────────────────────────────
    // A grade de coletores do AirWatch não volta em JSON: é HTML montado
    // no servidor. Aqui guardamos só a FORMA dela — os títulos das colunas
    // e quantas linhas vieram —, que é o que identifica o endpoint da lista.
    function textoDeTags(html, tag) {
        var re = new RegExp('<' + tag + '\\b[^>]*>([\\s\\S]*?)</' + tag + '>', 'gi');
        var fora = [], m;
        while ((m = re.exec(html)) !== null && fora.length < 40) {
            var t = m[1].replace(/<[^>]*>/g, ' ').replace(/&nbsp;/g, ' ')
                        .replace(/\s+/g, ' ').trim();
            if (t) fora.push(t.slice(0, 60));
        }
        return fora;
    }

    function registrarHtml(metodo, url, status, corpoResp) {
        if (typeof corpoResp !== 'string' || corpoResp.length < 200) return;
        if (corpoResp.indexOf('<') === -1) return;

        var p = partes(url);
        metodo = String(metodo || 'GET').toUpperCase();
        var chave = metodo + ' ' + p.caminho + ' [html]';
        var corpoTab = corpoResp.match(/<tbody\b[^>]*>([\s\S]*?)<\/tbody>/i);
        var alvoLinhas = corpoTab ? corpoTab[1] : corpoResp;
        var linhas = (alvoLinhas.match(/<tr[\s>]/gi) || []).length;
        var colunas = textoDeTags(corpoResp, 'th');

        var atual = mapa[chave];
        if (!atual) {
            mapa[chave] = {
                tipo: 'html', metodo: metodo, caminho: p.caminho,
                consulta_exemplo: p.consulta, status: status, chamadas: 1,
                registros: linhas, campo_lista: null,
                bytes: corpoResp.length, linhas_tabela: linhas, colunas: colunas,
                trecho: corpoResp.slice(0, 1200),
                visto_em: new Date().toISOString()
            };
            if (linhas > 1 || colunas.length) {
                console.log('%c[MDM/html] ' + metodo + ' ' + p.caminho + ' → ' + linhas +
                    ' linha(s), ' + colunas.length + ' coluna(s)', 'color:#a0f');
            }
        } else {
            atual.chamadas++;
            if (linhas > (atual.linhas_tabela || 0)) {
                atual.linhas_tabela = linhas;
                atual.registros = linhas;
                atual.colunas = colunas;
                atual.bytes = corpoResp.length;
                atual.consulta_exemplo = p.consulta;
                atual.trecho = corpoResp.slice(0, 1200);
            }
        }
        gravar();
    }

    function registrar(metodo, url, status, corpoReq, corpoResp) {
        if (ESTATICO.test(url)) return;
        var resp = jsonSeguro(corpoResp);
        if (resp === null) { registrarHtml(metodo, url, status, corpoResp); return; }

        var p = partes(url);
        metodo = String(metodo || 'GET').toUpperCase();
        var chave = metodo + ' ' + p.caminho;
        var lista = acharLista(resp);
        var registros = lista ? lista.qtd : 1;

        var atual = mapa[chave];
        if (!atual) {
            mapa[chave] = {
                metodo: metodo, caminho: p.caminho, consulta_exemplo: p.consulta,
                status: status, chamadas: 1, registros: registros,
                campo_lista: lista ? lista.campo : null,
                requisicao: podar(jsonSeguro(corpoReq)),
                amostra: podar(resp), schema: inferir(resp),
                visto_em: new Date().toISOString()
            };
        } else {
            atual.chamadas++;
            // Fica com a chamada mais rica: é a que melhor mostra o formato.
            if (registros > (atual.registros || 0)) {
                atual.registros = registros;
                atual.campo_lista = lista ? lista.campo : null;
                atual.consulta_exemplo = p.consulta;
                atual.amostra = podar(resp);
                atual.schema = inferir(resp);
            }
        }
        gravar();
        if (registros > 0 && !atual) {
            console.log('%c[MDM] ' + metodo + ' ' + p.caminho + ' → ' + registros +
                ' registro(s)' + (lista ? ' em "' + lista.campo + '"' : ''), 'color:#0a7');
        }
    }

    // ── ganchos ────────────────────────────────────────────────
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

    // ── comandos ───────────────────────────────────────────────
    function lista() {
        return Object.keys(mapa).map(function (k) { return mapa[k]; })
            .sort(function (a, b) { return (b.registros || 0) - (a.registros || 0); });
    }

    window.__mdmResumo = function () {
        var L = lista();
        if (!L.length) {
            console.log('%c[MDM] Nada capturado ainda. Navegue pela tela — e recole este script após cada recarregamento.', 'color:#c47f00');
            return;
        }
        console.table(L.map(function (x) {
            return {
                tipo: x.tipo || 'json', metodo: x.metodo, caminho: x.caminho,
                chamadas: x.chamadas, registros: x.registros,
                campo_lista: x.campo_lista || (x.colunas ? x.colunas.length + ' coluna(s)' : null),
                status: x.status
            };
        }));
        console.log('%c[MDM] ' + L.length + ' endpoint(s) acumulado(s), somando recarregamentos. ' +
            'Use __mdmSalvar() para baixar.', 'color:#0a7');
        return L.length;
    };

    window.__mdmSchema = function (filtro) {
        var L = lista().filter(function (x) {
            return x.registros > 0 && (!filtro || x.caminho.indexOf(filtro) !== -1);
        });
        if (!L.length) { console.log('%c[MDM] Nenhuma resposta com dados.', 'color:#c47f00'); return; }
        L.forEach(function (x) {
            console.groupCollapsed('%c' + x.metodo + ' ' + x.caminho + '  (' + x.registros + ' registro[s])', 'color:#06c');
            console.log(x.schema);
            console.groupEnd();
        });
        return L.length;
    };

    window.__mdmSalvar = function (nome) {
        var pacote = {
            gerado_em: new Date().toISOString(),
            origem: location.origin,
            observacao: 'Amostra limitada a ' + MAX_AMOSTRAS + ' registro(s) por endpoint; credenciais removidas.',
            total_endpoints: Object.keys(mapa).length,
            endpoints: lista()
        };
        var txt = JSON.stringify(pacote, null, 2);
        var a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([txt], { type: 'application/json' }));
        a.download = nome || ('mdm-analise-' + new Date().toISOString().slice(0, 19).replace(/[:T-]/g, '') + '.json');
        document.body.appendChild(a);
        a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
        console.log('%c[MDM] Arquivo gerado (' + Math.round(txt.length / 1024) + ' KB). Confira antes de enviar.', 'color:#0a7');
        return a.download;
    };

    window.__mdmLimpar = function () {
        Object.keys(mapa).forEach(function (k) { delete mapa[k]; });
        try { localStorage.removeItem(CHAVE); } catch (_) {}
        console.log('[MDM] Captura zerada.');
    };

    window.__mdmParar = function () {
        XMLHttpRequest.prototype.open = xhrOpen;
        XMLHttpRequest.prototype.send = xhrSend;
        if (fetchOrig) window.fetch = fetchOrig;
        window.__MDM_LIGADO = false;
        console.log('%c[MDM] Captura desligada nesta página. O acumulado continua salvo.', 'color:#c47f00');
    };

    var jaTem = Object.keys(mapa).length;
    console.log('%c[MDM] Captura ligada.' + (jaTem ? ' Continuando de ' + jaTem + ' endpoint(s) já capturado(s).' : ''),
        'color:#0a7;font-weight:bold');
    console.log('%cNavegue: Devices > pesquisar > abrir um coletor > paginar.\n' +
        'Recole este script depois de CADA recarregamento (o acumulado não se perde).\n' +
        'No final: __mdmResumo()  →  __mdmSchema()  →  __mdmSalvar()', 'color:#666');
})();
