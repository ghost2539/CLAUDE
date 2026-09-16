/* ================================================================
   Portal de Operações SPARE — Core Application
   Modular SPA with lazy-loaded modules
   ================================================================ */
(function () {
    'use strict';

    var API = '/api';

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
        var url = path.indexOf('http') === 0 ? path : API + path;
        var r = await fetch(url, Object.assign({}, opts, {
            credentials: 'include',
            body: body,
            headers: Object.assign(headers, opts.headers || {})
        }));
        if (!r.ok) {
            var d;
            try { d = await r.json(); } catch (_) { d = { detail: r.statusText }; }
            var msg = d.detail || 'Erro na requisição.';
            if (r.status === 401 && !url.match(/\/auth\/login/)) {
                try {
                    var chk = await fetch(API + '/auth/me', { credentials: 'include' });
                    if (!chk.ok) showLogin();
                } catch (_) {
                    showLogin();
                }
            }
            throw new Error(msg);
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
        // O login é sempre escuro por conta própria: o tema escolhido pelo
        // usuário só vale dentro do portal.
        delete document.documentElement.dataset.tema;
        $('#login-screen').hidden = false;
        $('#app-wrapper').hidden = true;
        carregarVersao();
    }

    // Versão e ambiente vêm de /api/versao (público, só o essencial), uma
    // vez por carga, e preenchem o selo do login e o rodapé da sidebar. Se
    // falhar, fica o texto padrão do HTML.
    var _versao = null;
    function carregarVersao() {
        if (_versao) { aplicarVersao(_versao); return; }
        fetch(API + '/versao', { credentials: 'same-origin' })
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
        var selo = $('#login-ambiente-texto');
        if (selo) selo.textContent = rotulo + ' · rede interna';
        var ver = $('#login-versao');
        if (ver && v.commit_curto) ver.textContent = 'Portal de Operações · ' + v.commit_curto;
        var lateral = $('#sidebar-versao');
        if (lateral) lateral.textContent = (v.commit_curto || 'Portal de Operações') + ' · ' + rotulo.toLowerCase();
    }

    // Keep-alive global do ServiceNow: enquanto o portal estiver logado, mantém
    // a sessão do SN viva em segundo plano (o login do portal já autentica no
    // SN). Roda independente da tela aberta.
    var _snKeepAlive = null;
    function startSnKeepAlive() {
        if (_snKeepAlive) return;
        var ping = function () {
            fetch('/api/servicenow/session-status', { credentials: 'same-origin' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (d) { if (d) marcarIntegracao(!!d.active); })
                .catch(function () {});
        };
        ping();
        _snKeepAlive = setInterval(ping, 3 * 60 * 1000);   // 3 min
    }
    function stopSnKeepAlive() {
        if (_snKeepAlive) { clearInterval(_snKeepAlive); _snKeepAlive = null; }
    }

    function showApp() {
        aplicarTema(state.user.tema || temaGuardado(), false);
        $('#login-screen').hidden = true;
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
        $$('.tema-seg button').forEach(function (b) {
            b.setAttribute('aria-pressed', String(b.dataset.tema === tema));
        });
        if (salvar) {
            api('/auth/preferencias', { method: 'PUT', body: { tema: tema } }).catch(function () {});
            // A tela aberta redesenha para os gráficos lerem os tokens novos.
            if (mudou && state.current) nav(location.hash.slice(1) || state.current);
        }
    }
    function marcarIntegracao(ok) {
        var p = $('#topbar-integ');
        if (!p) return;
        p.classList.toggle('off', !ok);
        var d = $('.dot', p);
        if (d) d.className = 'dot ' + (ok ? 'dot-green' : 'dot-red');
        $('#topbar-integ-texto').textContent = ok ? 'ServiceNow ok' : 'ServiceNow off';
        p.title = ok ? 'Sessão do ServiceNow ativa' : 'Sem sessão no ServiceNow';
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
            var base = ESPACO === 'times' ? '/static/modules-times/' : '/static/modules/';
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
        location.hash = route;
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

        // Clear sub-tabs and content
        $('#sub-tabs').hidden = true;
        $('#sub-tabs').innerHTML = '';
        var content = $('#page-content');
        content.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando...</div>';

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

    window.addEventListener('hashchange', function () {
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

    window.SPARE = {
        api: api,
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
        // Login form
        var form = $('#login-form');
        form.onsubmit = async function (e) {
            e.preventDefault();
            var username = $('#login-username').value.trim();
            var password = $('#login-password').value;
            // Entrada única pelo Logon AD desde que o login local saiu. O
            // seletor de um item só foi removido: controle sem escolha ocupa
            // espaço, e estilizado como botão competia com o "Entrar".
            var authType = 'SSO';
            try {
                loading(true);
                state.user = await api('/auth/login', {
                    method: 'POST',
                    body: { username: username, password: password, auth_type: authType }
                });
                state.permissions = state.user.permissions || [];
                state.permission_map = state.user.permission_map || {};
                applyVisual(state.user.visual_config);
                showApp();
            } catch (x) {
                $('#login-error').hidden = false;
                $('#login-error-text').textContent = x.message;
            } finally {
                loading(false);
            }
        };

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

        // Tema claro/escuro
        $$('.tema-seg button').forEach(function (b) {
            b.onclick = function () { aplicarTema(b.dataset.tema, true); };
        });

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
