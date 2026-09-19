/* ================================================================
   Portal de Operações SPARE — Core Application
   Modular SPA with lazy-loaded modules
   ================================================================ */
(function () {
    'use strict';

    // Prefixo quando o portal é servido num subcaminho do proxy (o main.py
    // injeta <meta name=app-base>). Vazio na raiz do domínio.
    var APP_BASE = (function () {
        var m = document.querySelector('meta[name="app-base"]');
        return (m && m.content ? m.content : '').replace(/\/+$/, '');
    })();
    var API = APP_BASE + '/api';

    // Alguns proxies acrescentam a barra no fim por REDIRECIONAMENTO (301).
    // O navegador reemite um POST como GET e o login quebra. Com a marca
    // ligada, a URL já sai com a barra e não há o que o proxy acrescentar.
    var API_BARRA = !!document.querySelector('meta[name="api-barra-final"]');

    // Junta a base ao caminho, pondo a barra ANTES da query quando preciso.
    function apiUrl(caminho) {
        var url = API + caminho;
        if (!API_BARRA) return url;
        var corte = url.indexOf('?');
        var base = corte === -1 ? url : url.slice(0, corte);
        var query = corte === -1 ? '' : url.slice(corte);
        if (base.charAt(base.length - 1) !== '/') base += '/';
        return base + query;
    }

    var ROUTES = {
        bemvindo:       'Bem-vindo',
        torre:          'Torre de Controle',
        consulta:       'Consulta',
        consulta_times: 'Acesso Consulta Times',
        recebimento:    'Recebimento',
        identificacao:  'Identificação',
        gestao_ativos:  'Gestão de Ativos',
        atendimento:    'Atendimento',
        preparacao:     'Preparação',
        separacao:      'Separação',
        projetos:       'Projetos de Loja',
        reversa:        'Logística Reversa',
        inventario:     'Inventário',
        regularizacao:  'Regularização',
        externo:        'Assistência',
        venda:          'Venda de Ativos',
        destinacao:     'Destinação',
        rastreio:       'Correios',
        reparos:        'Central de Reparos',
        orcamento_manutencao: 'Orçamento',
        orcamento_spare: 'Orçamento Spare',
        agendamentos_forn: 'Agendamentos Forn.',
        internalizacao: 'Internalização',
        servicenow_automacoes: 'ServiceNow',
        status:         'Status',
        parametros:     'Configuração'
    };

    // ── State ──────────────────────────────────────────────────────
    var state = {
        user: null,
        permissions: [],
        permission_map: {},
        current: '',
        nomeApp: 'Portal de Operações'
    };

    // ── Module registry (populated by lazy-loaded scripts) ────────
    window.SPARE_MODULES = window.SPARE_MODULES || {};

    // ── DOM helpers ────────────────────────────────────────────────
    function $(selector, context) {
        return (context || document).querySelector(selector);
    }

    function $$(selector, context) {
        return Array.from((context || document).querySelectorAll(selector));
    }

    function el(tag, attrs, children) {
        var e = document.createElement(tag);
        if (attrs) {
            Object.keys(attrs).forEach(function (k) {
                var v = attrs[k];
                if (k === 'className') e.className = v;
                else if (k === 'textContent') e.textContent = v;
                else if (k.indexOf('on') === 0 && k.length > 2)
                    e.addEventListener(k.slice(2).toLowerCase(), v);
                else e.setAttribute(k, v);
            });
        }
        if (Array.isArray(children)) {
            children.forEach(function (x) { if (x) e.appendChild(x); });
        } else if (children instanceof Node) {
            e.appendChild(children);
        } else if (typeof children === 'string') {
            e.innerHTML = children;
        }
        return e;
    }

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>'"]/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c];
        });
    }

    // ── API helper ─────────────────────────────────────────────────
    async function api(path, opts) {
        opts = opts || {};
        var headers = (opts.body instanceof FormData)
            ? {}
            : { 'Content-Type': 'application/json' };
        var body = opts.body;
        if (body && !(body instanceof FormData) && typeof body === 'object') {
            body = JSON.stringify(body);
        }
        var url = path.indexOf('http') === 0 ? path : apiUrl(path);
        var r = await fetch(url, Object.assign({}, opts, {
            credentials: 'include',
            body: body,
            headers: Object.assign(headers, opts.headers || {})
        }));
        if (!r.ok) {
            var d;
            try { d = await r.json(); } catch (_) { d = { detail: r.statusText }; }
            var msg = d.detail || 'Erro na requisição.';
            // O 422 do FastAPI vem como LISTA de erros de campo. Jogar isso
            // num textContent virava "[object Object]" na cara do operador:
            // aqui vira frase, sem o prefixo técnico do pydantic.
            if (Array.isArray(d.detail)) {
                msg = d.detail.map(function (it) {
                    return String((it && it.msg) || it).replace(/^Value error,\s*/, '');
                }).join(' ') || 'Erro na requisição.';
            }
            if (r.status === 401 && !url.match(/\/auth\/login/)) {
                // Perguntar ao /auth/me se o /auth/me falhou mesmo é circular:
                // dobrava toda abertura de página deslogada e enchia o console
                // de vermelho — dois 401 onde um já tinha respondido tudo.
                if (url.match(/\/auth\/me\/?($|\?)/)) {
                    showLogin();
                } else {
                    try {
                        // Pelo apiUrl, e não por API + caminho: é ele que põe a
                        // barra do fim quando a marca está ligada. Sem isso,
                        // justamente a chamada que existe para sobreviver ao
                        // proxy era a única que ia sem o contorno dele.
                        var chk = await fetch(apiUrl('/auth/me'), { credentials: 'include' });
                        if (!chk.ok) showLogin();
                    } catch (_) {
                        showLogin();
                    }
                }
            }
            // O status vai junto no erro: há tela que precisa separar "o
            // servidor está sem credencial" (503) de "você digitou errado"
            // (422). Quem só lê `.message` continua igual.
            var err = new Error(msg);
            err.status = r.status;
            throw err;
        }
        var ct = r.headers.get('content-type') || '';
        if (ct.indexOf('json') !== -1) return r.json();
        return r;
    }

    // ── Toast notifications ────────────────────────────────────────
    function toast(message, type) {
        type = type || 'info';
        var container = $('#toast-container');
        var t = el('div', { className: 'toast toast-' + type }, [
            el('span', { textContent: message }),
            el('button', { className: 'toast-close', textContent: '×', onClick: function () { t.remove(); } })
        ]);
        container.appendChild(t);
        setTimeout(function () { t.remove(); }, 4500);
    }

    // ── Loading overlay ────────────────────────────────────────────
    function loading(visible) {
        $('#loading-overlay').hidden = !visible;
    }

    // ── Formatting helpers ─────────────────────────────────────────
    function formatDate(x) {
        if (!x) return '';
        return new Date(x + 'T00:00:00').toLocaleDateString('pt-BR');
    }

    function money(x) {
        return Number(x || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
    }

    function badge(text) {
        var map = {
            'RECEBIDO':             'teal',
            'EM TRIAGEM':           'gold',
            'VENDA':                'orange',
            'S/ REPARO':            'danger',
            'ENVIADO LOJA':         'success',
            'INTERNALIZADO':        'info',
            'TRATATIVA DE SALDO':   'warning'
        };
        var cls = map[text] || 'default';
        return '<span class="badge badge-' + cls + '">' + esc(text || '—') + '</span>';
    }

    // ── Modal system ───────────────────────────────────────────────
    function openModal(title, body, buttons) {
        $('#modal-title').textContent = title;
        var b = $('#modal-body');
        b.innerHTML = '';
        if (body instanceof Node) b.appendChild(body);
        else b.innerHTML = body;
        var f = $('#modal-footer');
        f.innerHTML = '';
        (buttons || []).forEach(function (x) { f.appendChild(x); });
        $('#modal-overlay').hidden = false;
        $('#modal-overlay').classList.add('modal-open');
    }

    function closeModal() {
        $('#modal-overlay').classList.remove('modal-open');
        $('#modal-overlay').hidden = true;
    }

    // ── Table builder ──────────────────────────────────────────────
    function table(cols, rows) {
        var w = el('div', { className: 'table-wrapper' });
        var t = el('table', { className: 'data-table' });
        var thead = el('thead');
        var tr = el('tr');
        cols.forEach(function (c) {
            tr.appendChild(el('th', { textContent: c.label }));
        });
        thead.appendChild(tr);
        t.appendChild(thead);

        var tbody = el('tbody');
        if (!rows || !rows.length) {
            var r = el('tr');
            r.appendChild(el('td', {
                className: 'empty-row',
                colspan: String(cols.length),
                textContent: 'Nenhum registro encontrado.'
            }));
            tbody.appendChild(r);
        } else {
            rows.forEach(function (row, i) {
                var r = el('tr');
                cols.forEach(function (c) {
                    var td = el('td');
                    var v = c.render ? c.render(row[c.key], row, i) : row[c.key];
                    if (v instanceof Node) td.appendChild(v);
                    else if (c.html) td.innerHTML = v == null ? '' : v;
                    else td.textContent = v == null ? '' : v;
                    r.appendChild(td);
                });
                tbody.appendChild(r);
            });
        }
        t.appendChild(tbody);
        w.appendChild(t);
        return w;
    }

    // ── Tab system ─────────────────────────────────────────────────
    function tabs(list, active, base) {
        var c = $('#sub-tabs');
        c.hidden = false;
        c.innerHTML = '';
        list.forEach(function (x) {
            c.appendChild(el('button', {
                className: 'sub-tab' + (x[0] === active ? ' active' : ''),
                textContent: x[1],
                onClick: function () { nav(base + '/' + x[0]); }
            }));
        });
    }

    // ── Login / App visibility ─────────────────────────────────────
    function showLogin() {
        // O login é OUTRA página desde que o portal parou de vir junto para
        // quem não entrou. Sem sessão não há o que esconder aqui: o servidor
        // é quem entrega o login, e esta página nem deveria existir no
        // navegador. `replace` para o botão voltar não trazer o portal de
        // volta de dentro do cache.
        window.location.replace(API.replace(/\/api$/, '') + '/');
    }

    // Versão e ambiente vêm de /api/versao (público, só o essencial), uma
    // vez por carga, e preenchem o selo do login e o rodapé da sidebar. Se
    // falhar, fica o texto padrão do HTML.
    var _versao = null;
    function carregarVersao() {
        if (_versao) { aplicarVersao(_versao); return; }
        fetch(apiUrl('/versao'), { credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (v) { if (v) { _versao = v; aplicarVersao(v); } })
            .catch(function () {});
    }
    function rotuloAmbiente(v) {
        var amb = String(v.ambiente || 'producao').toLowerCase();
        return { producao: 'Produção', testes: 'Testes', teste: 'Testes',
                 homologacao: 'Homologação', homolog: 'Homologação' }[amb] || amb;
    }
    function aplicarVersao(v) {
        var rotulo = rotuloAmbiente(v);
        // O selo e a versão do LOGIN ficaram na página de login, com o
        // login.js. Aqui sobrou o rodapé da barra lateral.
        var lateral = $('#sidebar-versao');
        if (lateral) lateral.textContent = (v.commit_curto || 'Portal de Operações') + ' · ' + rotulo.toLowerCase();
    }

    // Keep-alive global do ServiceNow: enquanto o portal estiver logado, mantém
    // a sessão do SN viva em segundo plano (o login do portal já autentica no
    // SN). Roda independente da tela aberta.
    var _snKeepAlive = null;
    var _snUltimoPing = 0;

    var _snEstado = null;      // último estado conhecido, para o selo e o aviso
    var _snPedindoSenha = false;

    function snPing() {
        _snUltimoPing = Date.now();
        return fetch(apiUrl('/servicenow/session-status'), { credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) {
                    // O portal não respondeu. Não é a sessão do SN que caiu,
                    // mas também não dá para afirmar que está viva.
                    _snEstado = { estado: 'desconhecida' };
                    marcarIntegracao(_snEstado);
                    return;
                }
                _snEstado = d;
                marcarIntegracao(d);
                // A reconexão existia e ninguém a chamava: quando a sessão
                // expirava, o keep-alive só pintava o selo de vermelho e a
                // pessoa descobria ao ver uma ação falhar. Agora o portal
                // pede a senha na hora em que percebe.
                if (d.estado === 'expirada') pedirReconexaoSN();
            })
            .catch(function () {
                _snEstado = { estado: 'desconhecida' };
                marcarIntegracao(_snEstado);
            });
    }

    function pedirReconexaoSN() {
        if (_snPedindoSenha) return;
        _snPedindoSenha = true;
        Promise.resolve(snReloginModal())
            .then(function (ok) { if (ok) snPing(); })
            .finally(function () { _snPedindoSenha = false; });
    }

    // O relógio do navegador não é confiável para isto: aba em segundo
    // plano tem os temporizadores represados, e máquina suspensa não conta
    // tempo nenhum. Por isso o ping também sai quando a aba reaparece e
    // quando a rede volta — aí a resposta chega antes de o usuário clicar.
    function startSnKeepAlive() {
        if (_snKeepAlive) return;
        snPing();
        _snKeepAlive = setInterval(snPing, 3 * 60 * 1000);   // 3 min
        document.addEventListener('visibilitychange', _snAoVoltar);
        window.addEventListener('online', _snAoVoltar);
    }
    function _snAoVoltar() {
        if (!_snKeepAlive) return;
        if (document.visibilityState === 'hidden') return;
        // Sem repetir à toa quando a pessoa alterna de aba a cada instante.
        if (Date.now() - _snUltimoPing < 30 * 1000) return;
        snPing();
    }
    function stopSnKeepAlive() {
        if (_snKeepAlive) { clearInterval(_snKeepAlive); _snKeepAlive = null; }
        document.removeEventListener('visibilitychange', _snAoVoltar);
        window.removeEventListener('online', _snAoVoltar);
    }

    function showApp() {
        aplicarTema(state.user.tema || temaGuardado(), false);
        // Só o wrapper: a tela de login não vem mais nesta página, e mandar
        // esconder um elemento que não existe custava um erro de null.
        $('#app-wrapper').hidden = false;
        startSnKeepAlive();
        carregarVersao();
        buildMenu();
        var name = state.user.display_name || state.user.username;
        $('#topbar-user-name').textContent = state.user.username || '';
        $('#topbar-user-avatar').textContent = iniciais(name);
        $('#topbar-user-avatar').title = name;
        marcarIntegracao(!!state.user.sn_active);
        var destino = destinoPedido();
        if (destino) { location.replace(destino); return; }
        nav(location.hash.slice(1) || (ESPACO ? 'consulta' : 'bemvindo'));
    }

    // Telas fora do SPA (/controle-orcamento, /ebs-forms) mandam quem está sem
    // sessão para "/?next=<a tela>". Depois de entrar, o portal volta para lá
    // em vez de largar o usuário no Bem-vindo. Só caminho do próprio portal
    // é aceito: "//outro-host" e "http://..." levariam para fora do domínio.
    function destinoPedido() {
        var next = '';
        try {
            next = new URLSearchParams(location.search).get('next') || '';
        } catch (_) { return ''; }
        return /^\/[^/\\]/.test(next) ? next : '';
    }


    // ── Session check ──────────────────────────────────────────────
    async function checkSession() {
        try {
            state.user = await api('/auth/me');
            state.permissions = state.user.permissions || Object.keys(ROUTES);
            state.permission_map = state.user.permission_map || {};
            applyVisual(state.user.visual_config);
            showApp();
        } catch (_) {
            showLogin();
        }
    }

    // ── Visual config ──────────────────────────────────────────────
    // Só nome e rodapé são configuráveis: as cores são as do padrão de UI
    // SPARE (paleta LRSA 2025), iguais para todo mundo.
    function applyVisual(v) {
        v = v || {};
        if (v.nome_app) {
            state.nomeApp = v.nome_app;
            document.title = v.nome_app;
        }
        if (v.footer) {
            $('#portal-footer').textContent = v.footer;
        }
    }

    // ── Tema claro/escuro ─────────────────────────────────────────
    // Preferência do usuário: vale para todos os módulos, fica no perfil
    // (backend); o localStorage é só cache para não piscar ao abrir.
    var TEMA_CHAVE = 'spare-tema';
    function temaGuardado() {
        try { return localStorage.getItem(TEMA_CHAVE) === 'escuro' ? 'escuro' : 'claro'; }
        catch (_) { return 'claro'; }
    }
    function temaAtual() {
        return document.documentElement.dataset.tema === 'escuro' ? 'escuro' : 'claro';
    }
    function aplicarTema(tema, salvar) {
        tema = tema === 'escuro' ? 'escuro' : 'claro';
        var mudou = document.documentElement.dataset.tema !== tema;
        document.documentElement.dataset.tema = tema;
        try { localStorage.setItem(TEMA_CHAVE, tema); } catch (_) {}
        var botao = $('#tema-toggle');
        if (botao) {
            var escuro = tema === 'escuro';
            botao.setAttribute('aria-checked', String(escuro));
            botao.title = escuro ? 'Mudar para tema claro' : 'Mudar para tema escuro';
        }
        if (salvar) {
            // Se o perfil não aceitar a troca, o usuário precisa saber:
            // senão a escolha some no próximo login sem explicação.
            api('/auth/preferencias', { method: 'PUT', body: { tema: tema } })
                .catch(function () { toast('Não consegui guardar a preferência de tema.', 'warning'); });
            // A tela aberta redesenha para os gráficos lerem os tokens novos.
            if (mudou && state.current) nav(location.hash.slice(1) || state.current);
        }
    }
    /* Três estados, não dois. "Não consegui perguntar" virava VERDE, e era o
       pior caso: com o ping falhando há horas nada estava sendo renovado, a
       sessão morria, e o selo dizia que estava tudo bem até uma ação falhar.
       Aceita o objeto do ping ou um booleano (o estado inicial do login). */
    function marcarIntegracao(info) {
        var p = $('#topbar-integ');
        if (!p) return;
        var estado = (info && typeof info === 'object')
            ? (info.estado || (info.active ? 'ativa' : 'sem_sessao'))
            : (info ? 'ativa' : 'sem_sessao');
        var cor = { ativa: 'green', expirada: 'red', sem_sessao: 'red',
                    desconhecida: 'orange' }[estado] || 'orange';
        var texto = { ativa: 'ServiceNow ok', expirada: 'ServiceNow expirou',
                      sem_sessao: 'ServiceNow off',
                      desconhecida: 'ServiceNow ?' }[estado] || 'ServiceNow ?';
        p.classList.toggle('off', estado !== 'ativa');
        var d = $('.dot', p);
        if (d) d.className = 'dot dot-' + cor;
        $('#topbar-integ-texto').textContent = texto;

        // O título carrega o diagnóstico: é onde se responde "o keep-alive
        // está rodando?" sem abrir ferramenta nenhuma.
        var ka = (info && typeof info === 'object' && info.keepalive) || {};
        var linhas = {
            ativa: 'Sessão do ServiceNow ativa.',
            expirada: 'A sessão do ServiceNow expirou — reconecte para continuar.',
            sem_sessao: 'Sem sessão no ServiceNow.',
            desconhecida: 'Não foi possível falar com o ServiceNow. ' +
                'Enquanto isso, a sessão NÃO está sendo renovada.'
        }[estado] || '';
        if (ka.ultimo_ping) {
            linhas += '\nÚltimo ping: ' + ka.ultimo_ping;
            if (ka.ultima_renovacao) linhas += '\nÚltima renovação: ' + ka.ultima_renovacao;
            if (ka.falhas_seguidas) linhas += '\nFalhas seguidas: ' + ka.falhas_seguidas;
            if (ka.ultima_falha_motivo) linhas += '\nMotivo: ' + ka.ultima_falha_motivo;
        }
        p.title = linhas;
    }
    function iniciais(nome) {
        var partes = String(nome || '').trim().split(/\s+/).filter(Boolean);
        if (!partes.length) return 'U';
        var a = partes[0][0] || '';
        var b = partes.length > 1 ? partes[partes.length - 1][0] : '';
        return (a + b).toUpperCase();
    }

    // Trilha do header: grupo do menu / tela. Sem grupo (Bem-vindo, itens
    // do rodapé da sidebar), o grupo é o nome do portal.
    function atualizarTrilha(ativo, module) {
        var grupo = '', tela = '';
        if (ativo) {
            var g = ativo.closest('.sidebar-grupo');
            var t = g && $('.sidebar-grupo-titulo', g);
            grupo = t ? t.textContent.trim() : '';
            var rotulo = $('.sidebar-label', ativo);
            tela = rotulo ? rotulo.textContent.trim() : '';
        }
        $('#topbar-grupo').textContent = grupo || state.nomeApp;
        $('#topbar-tela').textContent = tela || ROUTES[module] || '';
    }

    // ── Espaços ───────────────────────────────────────────────────
    // O mesmo shell serve o portal inteiro e a Consulta Times. O espaço
    // vem do <body data-espaco>; fora do espaço padrão só aparecem os
    // itens marcados para ele, e a rota inicial é a Consulta.
    var ESPACO = document.body.dataset.espaco || '';
    var MODULOS_TIMES = ['consulta', 'gestao_ativos', 'consulta_times'];
    function rotaPermitida(module) {
        return !ESPACO || MODULOS_TIMES.indexOf(module) >= 0;
    }

    // ── Menu builder (permission-aware) ────────────────────────────
    function buildMenu() {
        $$('.sidebar-item').forEach(function (item) {
            var route = item.dataset.route || '';
            var href = item.dataset.href || '';
            // Um item pode ter chave de permissão diferente da rota: Gestão de
            // Ativos herda a permissão "servicenow", para que ninguém perca
            // acesso quando a tela muda de lugar no menu. Sem data-perm, a
            // chave é o módulo da rota (antes da barra).
            var perm = item.dataset.perm || route.split('/')[0];
            var visible = state.user.is_admin || !perm || state.permissions.indexOf(perm) !== -1;
            // "Minha conta" é de todo mundo.
            if (route === 'parametros/conta') visible = true;
            if (ESPACO) {
                var espacos = (item.dataset.espaco || '').split(' ');
                visible = espacos.indexOf(ESPACO) >= 0;
                // No espaço Times a permissão vem da liberação por login,
                // que o servidor já conferiu ao servir a página.
            }
            item.style.display = visible ? '' : 'none';
            item.onclick = function (e) {
                e.preventDefault();
                if (href) { window.location.href = href; return; }
                nav(route);
            };
        });
        // Grupo sem item visível some junto.
        $$('.sidebar-grupo').forEach(function (g) {
            var algum = $$('.sidebar-item', g).some(function (i) { return i.style.display !== 'none'; });
            g.style.display = algum ? '' : 'none';
        });
        // Grupos recolhíveis; o estado fica no navegador de cada um.
        var fechados = [];
        try { fechados = JSON.parse(localStorage.getItem('spare.menu.fechados') || '[]'); } catch (_) {}
        $$('.sidebar-grupo').forEach(function (g) {
            var titulo = $('.sidebar-grupo-titulo', g);
            var nome = titulo.textContent.trim();
            if (fechados.indexOf(nome) >= 0) g.classList.add('fechado');
            titulo.onclick = function () {
                g.classList.toggle('fechado');
                var lista = $$('.sidebar-grupo.fechado .sidebar-grupo-titulo').map(function (t) { return t.textContent.trim(); });
                try { localStorage.setItem('spare.menu.fechados', JSON.stringify(lista)); } catch (_) {}
            };
        });
    }

    // ── Module loader (lazy) ───────────────────────────────────────
    var _loadingModules = {};

    async function loadModule(name) {
        if (window.SPARE_MODULES[name]) return;
        if (_loadingModules[name]) return _loadingModules[name];

        _loadingModules[name] = new Promise(function (resolve, reject) {
            var script = document.createElement('script');
            // O espaço Times tem os módulos dele, em pasta própria: mexer
            // num lado não muda o outro.
            // Fora de /static: o módulo passa por uma rota que confere a
            // permissão antes de entregar. Em /static ele saía para
            // qualquer um, sem sessão.
            var base = APP_BASE + (ESPACO === 'times' ? '/modulos-times/' : '/modulos/');
            script.src = base + name + '.js?v=' + Date.now();
            script.onload = function () {
                delete _loadingModules[name];
                resolve();
            };
            script.onerror = function () {
                delete _loadingModules[name];
                reject(new Error('Falha ao carregar módulo: ' + name));
            };
            document.head.appendChild(script);
        });

        return _loadingModules[name];
    }

    // ── Router ─────────────────────────────────────────────────────
    async function nav(route) {
        var parts = route.split('/');
        var module = parts[0];
        var sub = parts.slice(1).join('/') || undefined;

        if (!ROUTES[module]) module = 'bemvindo';
        if (!rotaPermitida(module)) { module = 'consulta'; route = 'consulta'; }
        state.current = module;
        // Trocar o hash dispara `hashchange`, que chamaria nav() de novo:
        // duas renderizações da mesma tela correndo juntas, cada uma
        // anexando os blocos dela entre um `await` e outro. A marca faz o
        // ouvinte ignorar a troca que saiu daqui.
        if (location.hash.slice(1) !== route) {
            _hashInterno = true;
            location.hash = route;
            // Se o evento não vier, a marca não pode ficar pendurada e
            // engolir a próxima navegação pelo botão voltar.
            setTimeout(function () { _hashInterno = false; }, 0);
        }
        // Modal é global: um erro deixado aberto numa tela não pode
        // acompanhar o usuário para a próxima.
        closeModal();
        document.body.classList.remove('menu-aberto');

        // Update active sidebar item
        $$('.sidebar-item').forEach(function (x) {
            var r = x.dataset.route || '';
            x.classList.toggle('active', r === route || (r.indexOf('/') < 0 && r === module));
        });
        // O grupo do item ativo abre, mesmo que estivesse fechado.
        var ativo = $('.sidebar-item.active');
        if (ativo) { var g = ativo.closest('.sidebar-grupo'); if (g) g.classList.remove('fechado'); }
        atualizarTrilha(ativo, module);

        // Sub-abas e conteúdo. O conteúdo não é só esvaziado: o nó é
        // trocado por um novo. Assim um render anterior que ainda esteja
        // no meio de um `await` termina escrevendo num nó já fora da
        // tela, em vez de somar os blocos dele aos da tela nova.
        $('#sub-tabs').hidden = true;
        $('#sub-tabs').innerHTML = '';
        var content = document.createElement('section');
        content.id = 'page-content';
        content.className = 'page-content';
        content.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando...</div>';
        $('#page-content').replaceWith(content);

        try {
            await loadModule(module);
            if (window.SPARE_MODULES[module] && window.SPARE_MODULES[module].render) {
                window.SPARE_MODULES[module].render(content, sub);
            } else {
                content.innerHTML = '<div class="alert alert-danger">Módulo "' + esc(module) + '" não encontrado.</div>';
            }
        } catch (e) {
            content.innerHTML = '<div class="alert alert-danger">' + esc(e.message) + '</div>';
        }
    }

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { closeModal(); document.body.classList.remove('menu-aberto'); }
    });
    var _menuBtn = document.getElementById('menu-toggle');
    if (_menuBtn) {
        _menuBtn.onclick = function () { document.body.classList.toggle('menu-aberto'); };
        document.addEventListener('click', function (e) {
            if (document.body.classList.contains('menu-aberto') &&
                !e.target.closest('.sidebar') && !e.target.closest('#menu-toggle')) {
                document.body.classList.remove('menu-aberto');
            }
        });
    }

    var _hashInterno = false;
    window.addEventListener('hashchange', function () {
        if (_hashInterno) { _hashInterno = false; return; }
        nav(location.hash.slice(1) || (ESPACO ? 'consulta' : 'bemvindo'));
    });

    // ── Global search ──────────────────────────────────────────────
    function setupGlobalSearch() {
        $('#global-search').onkeydown = function (e) {
            if (e.key === 'Enter' && e.target.value.trim()) {
                nav('consulta');
                setTimeout(function () {
                    var input = document.getElementById('q-bg-input');
                    var btn = document.getElementById('q-bg-run');
                    if (input && btn) {
                        input.value = e.target.value;
                        btn.click();
                    }
                }, 200);
            }
        };
    }

    // ── Expose global SPARE object ─────────────────────────────────
    async function checkSnSession() {
        try {
            var r = await api('/auth/sn-session');
            return r.active;
        } catch (_) {
            return false;
        }
    }

    function snReloginModal() {
        return new Promise(function (resolve) {
            var body = el('div', {}, [
                el('p', { textContent: 'Sua sessão ServiceNow expirou. Informe sua senha para reconectar.' }),
                el('div', { className: 'form-group mt-2' }, [
                    el('label', { textContent: 'Senha ServiceNow' }),
                    el('input', { id: 'sn-relogin-pw', type: 'password', className: 'form-control' })
                ])
            ]);
            openModal('Reconectar ServiceNow', body, [
                el('button', { className: 'btn btn-primary', textContent: 'Reconectar', onClick: async function () {
                    var pw = document.getElementById('sn-relogin-pw').value;
                    if (!pw) { toast('Informe a senha.', 'warning'); return; }
                    try {
                        loading(true);
                        await api('/auth/sn-relogin', { method: 'POST', body: { password: pw } });
                        closeModal();
                        toast('ServiceNow reconectado.', 'success');
                        resolve(true);
                    } catch (x) {
                        toast(x.message, 'error');
                        resolve(false);
                    } finally {
                        loading(false);
                    }
                }}),
                el('button', { className: 'btn btn-secondary', textContent: 'Cancelar', onClick: function () {
                    closeModal();
                    resolve(false);
                }})
            ]);
        });
    }

    /* Navega para um caminho da API COM o prefixo do proxy. É o jeito de
       baixar arquivo: um GET autenticado por cookie em que o navegador é
       quem salva, sem passar pelo fetch.

       Existe porque a falta dele custou caro. `apiUrl` não era exportado,
       então quem escrevia módulo escrevia `window.location = '/api/...'` —
       caminho absoluto, que atrás do proxy resolve contra a RAIZ do domínio
       e cai no sistema do lado, não no portal. O botão "levava para a tela
       de API do suporte.lojas". */
    function baixar(caminho) {
        window.location.href = apiUrl(caminho);
    }

    /* Idem para uma página do próprio portal que não é da API
       (ex.: /obsolescencia). Mesmo problema, mesma correção. */
    function urlDoPortal(caminho) {
        return APP_BASE + (caminho.charAt(0) === '/' ? caminho : '/' + caminho);
    }

    window.SPARE = {
        api: api,
        // Sem estes dois, quem precisa montar uma URL à mão não tem como
        // acertar — e escreve caminho absoluto, que quebra atrás do proxy.
        apiUrl: apiUrl,
        base: APP_BASE,
        baixar: baixar,
        urlDoPortal: urlDoPortal,
        el: el,
        esc: esc,
        toast: toast,
        table: table,
        tabs: tabs,
        openModal: openModal,
        closeModal: closeModal,
        money: money,
        badge: badge,
        loading: loading,
        formatDate: formatDate,
        user: function () { return state.user; },
        tema: temaAtual,
        checkSnSession: checkSnSession,
        snReloginModal: snReloginModal
    };

    // ── Init ───────────────────────────────────────────────────────
    function init() {
        // O formulário de login vive em static/js/login.js, na página
        // dele. Este arquivo só é entregue a quem já tem sessão.

        // Login type selector

        // Logout
        $('#logout-btn').onclick = async function () {
            stopSnKeepAlive();
            await api('/auth/logout', { method: 'POST' }).catch(function () {});
            showLogin();
        };

        // Modal close
        $('#modal-close-btn').onclick = closeModal;
        $('#modal-overlay').onclick = function (e) {
            if (e.target.classList.contains('modal-backdrop')) closeModal();
        };

        // Tema claro/escuro. O que está no cache vale desde já, para a
        // tela não piscar; quando /auth/me responde, a preferência do
        // perfil assume.
        aplicarTema(temaGuardado(), false);
        $('#tema-toggle').onclick = function () {
            aplicarTema(temaAtual() === 'escuro' ? 'claro' : 'escuro', true);
        };

        // Global search
        setupGlobalSearch();

        // Session check
        checkSession();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
