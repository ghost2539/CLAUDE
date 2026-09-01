/* ================================================================
   Portal de Operações SPARE — Core Application
   Modular SPA with lazy-loaded modules
   ================================================================ */
(function () {
    'use strict';

    var API = '/api';

    var ROUTES = {
        bemvindo:       'Bem-vindo',
        consulta:       'Consulta',
        recebimento:    'Recebimento',
        identificacao:  'Identificação',
        servicenow:     'ServiceNow',
        rastreio:       'Correios',
        reparos:        'Central de Reparos',
        status:         'Status',
        parametros:     'Parâmetros'
    };

    // ── State ──────────────────────────────────────────────────────
    var state = {
        user: null,
        permissions: [],
        permission_map: {},
        current: ''
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
        $('#login-screen').hidden = false;
        $('#app-wrapper').hidden = true;
    }

    function showApp() {
        $('#login-screen').hidden = true;
        $('#app-wrapper').hidden = false;
        buildMenu();
        var name = state.user.display_name || state.user.username;
        $('#topbar-user-name').textContent = name;
        $('#topbar-user-avatar').textContent = name[0].toUpperCase();
        // Troca obrigatória só para usuários LOCAIS no primeiro acesso.
        // AD/SN autenticam externamente e não trocam senha aqui.
        if (state.user.must_change_password && state.user.auth_source === 'LOCAL') {
            forcePasswordChange();
            return;
        }
        nav(location.hash.slice(1) || 'bemvindo');
    }

    // Troca de senha obrigatória no primeiro acesso (bloqueia o portal).
    function forcePasswordChange() {
        var existing = document.getElementById('force-pass-overlay');
        if (existing) existing.remove();
        var ov = el('div', { id: 'force-pass-overlay' });
        ov.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.6);' +
            'display:flex;align-items:center;justify-content:center;padding:16px';
        ov.innerHTML =
            '<div style="background:var(--bg-primary,#fff);max-width:420px;width:100%;' +
            'border-radius:12px;padding:24px;box-shadow:0 10px 40px rgba(0,0,0,.3)">' +
            '<h2 style="margin:0 0 6px;font-size:1.2rem">Defina uma nova senha</h2>' +
            '<p style="color:var(--text-secondary);margin:0 0 16px;font-size:.9rem">' +
            'Primeiro acesso: por segurança, troque a senha temporária antes de continuar.</p>' +
            '<div class="form-group"><label>Senha temporária</label>' +
            '<input id="fp-old" type="password" class="form-control" autocomplete="current-password"></div>' +
            '<div class="form-group"><label>Nova senha</label>' +
            '<input id="fp-new" type="password" class="form-control" autocomplete="new-password"></div>' +
            '<div class="form-group"><label>Confirmar nova senha</label>' +
            '<input id="fp-new2" type="password" class="form-control" autocomplete="new-password"></div>' +
            '<div id="fp-err" style="color:#dc2626;font-size:.85rem;margin:6px 0" hidden></div>' +
            '<button id="fp-save" class="btn btn-primary" style="width:100%;margin-top:8px">Salvar e entrar</button>' +
            '</div>';
        document.body.appendChild(ov);

        function erro(msg) {
            var e = document.getElementById('fp-err');
            e.textContent = msg; e.hidden = false;
        }
        document.getElementById('fp-save').onclick = async function () {
            var old = document.getElementById('fp-old').value;
            var nova = document.getElementById('fp-new').value;
            var nova2 = document.getElementById('fp-new2').value;
            if (!old || !nova) { erro('Preencha todos os campos.'); return; }
            if (nova.length < 6) { erro('A nova senha deve ter ao menos 6 caracteres.'); return; }
            if (nova !== nova2) { erro('A confirmação não confere.'); return; }
            if (nova === old) { erro('A nova senha deve ser diferente da temporária.'); return; }
            try {
                await api('/auth/change-password', {
                    method: 'POST',
                    body: { current_password: old, new_password: nova }
                });
                state.user.must_change_password = false;
                ov.remove();
                nav(location.hash.slice(1) || 'bemvindo');
            } catch (x) {
                erro(x.message || 'Falha ao alterar a senha.');
            }
        };
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
    function applyVisual(v) {
        v = v || {};
        var root = document.documentElement;
        var map = {
            cor_primaria: '--color-primary',
            cor_fundo:    '--bg-root',
            cor_painel:   '--bg-panel',
            cor_texto:    '--text-primary',
            cor_destaque: '--color-gold'
        };
        Object.keys(map).forEach(function (k) {
            if (v[k]) root.style.setProperty(map[k], v[k]);
        });
        if (v.nome_app) {
            $('#app-title').textContent = v.nome_app;
            document.title = v.nome_app;
        }
        if (v.footer) {
            $('#portal-footer').textContent = v.footer;
        }
    }

    // ── Menu builder (permission-aware) ────────────────────────────
    function buildMenu() {
        $$('.sidebar-item').forEach(function (item) {
            var route = item.dataset.route;
            var visible = state.user.is_admin || state.permissions.indexOf(route) !== -1;
            item.style.display = visible ? '' : 'none';
            item.onclick = function (e) {
                e.preventDefault();
                nav(route);
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
            script.src = '/static/modules/' + name + '.js?v=' + Date.now();
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
        state.current = module;
        location.hash = route;

        // Update active sidebar item
        $$('.sidebar-item').forEach(function (x) {
            x.classList.toggle('active', x.dataset.route === module);
        });

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

    window.addEventListener('hashchange', function () {
        nav(location.hash.slice(1) || 'bemvindo');
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
            var authType = $('.login-type-btn.active').dataset.authType;
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
        $$('.login-type-btn').forEach(function (b) {
            b.onclick = function () {
                $$('.login-type-btn').forEach(function (x) { x.classList.remove('active'); });
                b.classList.add('active');
            };
        });

        // Logout
        $('#logout-btn').onclick = async function () {
            await api('/auth/logout', { method: 'POST' }).catch(function () {});
            showLogin();
        };

        // Modal close
        $('#modal-close-btn').onclick = closeModal;
        $('#modal-overlay').onclick = function (e) {
            if (e.target.classList.contains('modal-backdrop')) closeModal();
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
