/* ================================================================
   Module: Parâmetros (Admin / Settings — all sub-tabs)
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.parametros = {

    render(container, sub) {
        var S = window.SPARE;
        var u = S.user();

        container.classList.add('parameters-module');

        var allTabs = [
            ['visual',          'Visual'],
            ['locais',          'Locais'],
            ['classificacoes',  'Classificações'],
            ['valor-hora',      'Valor-hora'],
            ['permissoes',      'Usuários e Permissões'],
            ['sequencias',      'Sequências'],
            ['tv',              'TV'],
            ['conta',           'Minha conta']
        ];

        var adminOnly = ['visual', 'permissoes', 'sequencias'];
        var visibleTabs = allTabs.filter(function (x) {
            return u.is_admin || adminOnly.indexOf(x[0]) === -1;
        });

        sub = sub || 'conta';
        S.tabs(visibleTabs, sub, 'parametros');

        container.innerHTML =
            '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando...</div>';

        var handlers = {
            visual:         renderVisual,
            locais:         renderLocations,
            classificacoes: renderClassifications,
            'valor-hora':   renderHourly,
            permissoes:     renderPermissions,
            sequencias:     renderSequences,
            tv:             renderTV,
            conta:          renderAccount
        };

        var handler = handlers[sub] || renderAccount;
        Promise.resolve(handler(container, S)).catch(function (e) {
            container.innerHTML =
                '<div class="alert alert-danger"><strong>Falha ao carregar.</strong><br>' +
                S.esc(e.message || e) + '</div>';
        });
    }

};

/* ── Helper: field builder ──────────────────────────────────────── */
function _pField(label, id, value, type) {
    var S = window.SPARE;
    var d = S.el('div', { className: 'form-group' });
    d.innerHTML = '<label>' + S.esc(label) + '</label>' +
        '<input id="' + id + '" type="' + (type || 'text') + '" class="form-control" ' +
        'value="' + S.esc(value || '') + '">';
    return d;
}

/* ── Visual ─────────────────────────────────────────────────────── */
async function renderVisual(c, S) {
    c.innerHTML = '<h1 class="page-title">Administração visual</h1>';
    var d = await S.api('/parametros/config/visual');
    var fields = [
        ['nome_app',     'Nome da aplicação'],
        ['subtitulo',    'Subtítulo'],
        ['login_title',  'Título do login'],
        ['footer',       'Rodapé'],
        ['cor_primaria', 'Cor primária',    'color'],
        ['cor_fundo',    'Cor de fundo',    'color'],
        ['cor_painel',   'Cor dos painéis', 'color'],
        ['cor_texto',    'Cor do texto',    'color'],
        ['cor_destaque', 'Cor de destaque', 'color']
    ];
    var form = S.el('div', { className: 'form-grid cols-2' });
    fields.forEach(function (f) {
        form.appendChild(_pField(f[1], f[0], d[f[0]] || '', f[2] || 'text'));
    });
    var saveBtn = S.el('button', { className: 'btn btn-primary mt-3', textContent: 'Salvar' });
    saveBtn.onclick = async function () {
        var body = Object.assign({}, d);
        fields.forEach(function (f) {
            body[f[0]] = document.getElementById(f[0]).value;
        });
        await S.api('/parametros/config/visual', { method: 'PUT', body: body });
        S.toast('Configuração visual salva.', 'success');
    };
    var resetBtn = S.el('button', { className: 'btn btn-outline mt-3', textContent: 'Restaurar padrão', style: 'margin-left:8px' });
    resetBtn.onclick = async function () {
        await S.api('/parametros/visual/reset', { method: 'POST' });
        S.toast('Visual restaurado.', 'success');
        renderVisual(c, S);
    };
    var card = S.el('div', { className: 'card' });
    var cardBody = S.el('div', { className: 'card-body' });
    cardBody.appendChild(form);
    card.appendChild(cardBody);
    c.appendChild(card);
    var btnRow = S.el('div', { className: 'btn-row mt-2' });
    btnRow.appendChild(saveBtn);
    btnRow.appendChild(resetBtn);
    c.appendChild(btnRow);
}

/* ── Locais ─────────────────────────────────────────────────────── */
async function renderLocations(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Locais</h1>' +
        '<button id="pm-local-add" class="btn btn-primary mb-3">Novo local</button>' +
        '<div id="pm-locations"></div>';

    async function load() {
        var d = await S.api('/parametros/locais');
        var cols = [
            { key: 'nome',      label: 'Nome' },
            { key: 'descricao', label: 'Descrição' },
            { key: 'ativo',     label: 'Ativo' },
            {
                key: 'a', label: '',
                render: function (_, r) {
                    var b = S.el('button', { className: 'btn btn-sm btn-outline', textContent: 'Editar' });
                    b.onclick = function () { edit(r); };
                    return b;
                }
            }
        ];
        var el = document.getElementById('pm-locations');
        el.innerHTML = '';
        el.appendChild(S.table(cols, d.locais));
    }

    function edit(r) {
        r = r || {};
        var f = S.el('div');
        f.appendChild(_pField('Nome', 'pm-ln', r.nome || ''));
        f.appendChild(_pField('Descrição', 'pm-ld', r.descricao || ''));
        var saveBtn = S.el('button', { className: 'btn btn-primary', textContent: 'Salvar' });
        saveBtn.onclick = async function () {
            await S.api('/parametros/locais' + (r.id ? '/' + r.id : ''), {
                method: r.id ? 'PUT' : 'POST',
                body: {
                    nome:      document.getElementById('pm-ln').value,
                    descricao: document.getElementById('pm-ld').value,
                    ativo:     r.ativo !== false
                }
            });
            S.closeModal();
            S.toast('Local salvo.', 'success');
            load();
        };
        S.openModal(r.id ? 'Editar local' : 'Novo local', f, [saveBtn]);
    }

    document.getElementById('pm-local-add').onclick = function () { edit(); };
    load();
}

/* ── Classificações ─────────────────────────────────────────────── */
async function renderClassifications(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Classificações</h1>' +
        '<p class="text-muted">Regras de classificação automática de ativos por descrição EBS.</p>' +
        '<button id="pm-class-add2" class="btn btn-primary mb-3">Nova regra</button>' +
        '<div id="pm-class-list2"></div>';

    async function load() {
        var d = await S.api('/parametros/classificacoes');
        var cols = [
            { key: 'padrao_descricao', label: 'Padrão' },
            { key: 'empresa',          label: 'Empresa' },
            { key: 'categoria',        label: 'Categoria' },
            { key: 'modelo',           label: 'Modelo' },
            { key: 'ativo',            label: 'Ativa' },
            {
                key: 'a', label: '',
                render: function (_, r) {
                    var b = S.el('button', { className: 'btn btn-sm btn-outline', textContent: 'Editar' });
                    b.onclick = function () { edit(r); };
                    return b;
                }
            }
        ];
        var el = document.getElementById('pm-class-list2');
        el.innerHTML = '';
        el.appendChild(S.table(cols, d.regras));
    }

    function edit(r) {
        r = r || {};
        var f = S.el('div');
        [
            ['Padrão da descrição', 'pm-cp2', r.padrao_descricao],
            ['Empresa (opcional)',   'pm-ce2', r.empresa],
            ['Categoria',           'pm-cc2', r.categoria],
            ['Modelo',              'pm-cm2', r.modelo]
        ].forEach(function (x) { f.appendChild(_pField(x[0], x[1], x[2])); });

        var saveBtn = S.el('button', { className: 'btn btn-primary', textContent: 'Salvar' });
        saveBtn.onclick = async function () {
            try {
                await S.api('/parametros/classificacoes' + (r.id ? '/' + r.id : ''), {
                    method: r.id ? 'PUT' : 'POST',
                    body: {
                        padrao_descricao: document.getElementById('pm-cp2').value,
                        empresa:          document.getElementById('pm-ce2').value,
                        categoria:        document.getElementById('pm-cc2').value,
                        modelo:           document.getElementById('pm-cm2').value,
                        ativo:            true
                    }
                });
                S.closeModal();
                S.toast('Regra salva.', 'success');
                load();
            } catch (e) {
                S.toast(e.message, 'error');
            }
        };
        S.openModal(r.id ? 'Editar regra' : 'Nova regra', f, [saveBtn]);
    }

    document.getElementById('pm-class-add2').onclick = function () { edit(); };
    load();
}

/* ── Valor-hora ─────────────────────────────────────────────────── */
async function renderHourly(c, S) {
    var d = await S.api('/parametros/valor-hora');
    c.innerHTML =
        '<h1 class="page-title">Valor-hora</h1>' +
        '<div class="card">' +
            '<div class="card-body">' +
                '<div class="stat-value">' + S.money(d.valor) + '</div>' +
                '<div class="form-group mt-2">' +
                    '<label>Novo valor</label>' +
                    '<input id="pm-rate" type="number" step="0.01" class="form-control" value="' + d.valor + '">' +
                '</div>' +
                '<button id="pm-rate-save" class="btn btn-primary mt-2">Salvar</button>' +
            '</div>' +
        '</div>';

    document.getElementById('pm-rate-save').onclick = async function () {
        await S.api('/parametros/valor-hora', {
            method: 'PUT',
            body: { valor: +document.getElementById('pm-rate').value }
        });
        S.toast('Valor-hora atualizado.', 'success');
        renderHourly(c, S);
    };
}

/* ── Usuários e Permissões ──────────────────────────────────────── */
async function renderPermissions(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Usuários e Permissões</h1>' +
        '<p class="text-muted">Usuários AD são registrados automaticamente no primeiro login. ' +
            'Selecione Editar para definir acesso por módulo.</p>' +
        '<button id="pm-user-add" class="btn btn-primary mb-3">Novo usuário local</button>' +
        '<div id="pm-users"></div>';

    var MODULES = ['bemvindo', 'consulta', 'recebimento', 'reparos', 'status', 'parametros'];
    var ACTIONS = ['can_view', 'can_create', 'can_edit', 'can_export', 'can_admin'];
    var ACTION_LABELS = ['Visualizar', 'Criar', 'Editar', 'Exportar', 'Administrar'];

    async function load() {
        var d = await S.api('/parametros/permissoes');
        var cols = [
            { key: 'username',      label: 'Login' },
            { key: 'display_name',  label: 'Nome' },
            { key: 'auth_source',   label: 'Origem' },
            { key: 'active',        label: 'Ativo' },
            { key: 'is_admin',      label: 'Admin' },
            { key: 'last_access',   label: 'Último acesso' },
            {
                key: 'a', label: '',
                render: function (_, u) {
                    var b = S.el('button', { className: 'btn btn-sm btn-outline', textContent: 'Editar' });
                    b.onclick = function () { editUser(u); };
                    return b;
                }
            }
        ];
        var el = document.getElementById('pm-users');
        el.innerHTML = '';
        el.appendChild(S.table(cols, d.usuarios));
    }

    function editUser(u) {
        var box = S.el('div');

        box.innerHTML =
            '<div class="form-grid cols-2">' +
                '<div><strong>' + S.esc(u.display_name || u.username) + '</strong>' +
                    '<div class="text-muted">' + S.esc(u.username) + ' | ' + S.esc(u.auth_source || '') + '</div>' +
                '</div>' +
                '<div>' +
                    '<label class="checkbox-label">' +
                        '<input id="perm-active" type="checkbox" ' + (u.active ? 'checked' : '') + '> Usuário ativo' +
                    '</label>' +
                    '<label class="checkbox-label">' +
                        '<input id="perm-admin" type="checkbox" ' + (u.is_admin ? 'checked' : '') + '> Administrador total' +
                    '</label>' +
                '</div>' +
            '</div>' +
            '<div class="table-wrapper mt-3">' +
                '<table class="data-table">' +
                    '<thead><tr>' +
                        '<th>Módulo</th>' +
                        ACTION_LABELS.map(function (l) { return '<th>' + l + '</th>'; }).join('') +
                    '</tr></thead>' +
                    '<tbody id="perm-body"></tbody>' +
                '</table>' +
            '</div>';

        var tbody = box.querySelector('#perm-body');
        var permMap = u.permission_map || {};

        MODULES.forEach(function (m) {
            var perms = permMap[m] || {};
            var tr = S.el('tr');
            tr.innerHTML = '<td><strong>' + S.esc(m) + '</strong></td>' +
                ACTIONS.map(function (k) {
                    return '<td><input class="perm-check" data-module="' + S.esc(m) +
                        '" data-key="' + k + '" type="checkbox" ' +
                        (perms[k] ? 'checked' : '') + '></td>';
                }).join('');
            tbody.appendChild(tr);
        });

        var saveBtn = S.el('button', { className: 'btn btn-primary', textContent: 'Salvar permissões' });
        saveBtn.onclick = async function () {
            var map = {};
            box.querySelectorAll('.perm-check').forEach(function (ch) {
                var mod = ch.dataset.module;
                if (!map[mod]) map[mod] = {};
                map[mod][ch.dataset.key] = ch.checked;
            });
            await S.api('/parametros/permissoes/' + encodeURIComponent(u.username), {
                method: 'PUT',
                body: {
                    active:         box.querySelector('#perm-active').checked,
                    is_admin:       box.querySelector('#perm-admin').checked,
                    permission_map: map
                }
            });
            S.closeModal();
            S.toast('Permissões atualizadas.', 'success');
            load();
        };
        S.openModal('Permissões de ' + u.username, box, [saveBtn]);
    }

    document.getElementById('pm-user-add').onclick = function () {
        var f = S.el('div');
        [
            ['Login', 'pm-ul', ''],
            ['Nome',  'pm-un', '']
        ].forEach(function (x) { f.appendChild(_pField(x[0], x[1], x[2])); });
        f.appendChild(_pField('Senha temporária', 'pm-up', '', 'password'));

        var saveBtn = S.el('button', { className: 'btn btn-primary', textContent: 'Criar' });
        saveBtn.onclick = async function () {
            await S.api('/parametros/usuarios', {
                method: 'POST',
                body: {
                    login:        document.getElementById('pm-ul').value,
                    display_name: document.getElementById('pm-un').value,
                    password:     document.getElementById('pm-up').value
                }
            });
            S.closeModal();
            S.toast('Usuário criado.', 'success');
            load();
        };
        S.openModal('Novo usuário', f, [saveBtn]);
    };

    load();
}

/* ── Sequências ─────────────────────────────────────────────────── */
async function renderSequences(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Sequências de lotes</h1>' +
        '<div id="pm-seq"></div>';

    var d = await S.api('/parametros/sequencias');
    var cols = [
        { key: 'prefixo',         label: 'Prefixo' },
        { key: 'proximo_numero',  label: 'Próximo número' },
        {
            key: 'a', label: '',
            render: function (_, r) {
                var b = S.el('button', { className: 'btn btn-sm btn-outline', textContent: 'Alterar' });
                b.onclick = function () {
                    var f = _pField('Próximo número', 'pm-sn', r.proximo_numero, 'number');
                    var saveBtn = S.el('button', { className: 'btn btn-primary', textContent: 'Salvar' });
                    saveBtn.onclick = async function () {
                        await S.api('/lotes/sequencias/' + r.prefixo, {
                            method: 'PUT',
                            body: { next_number: +document.getElementById('pm-sn').value }
                        });
                        S.closeModal();
                        S.toast('Sequência atualizada.', 'success');
                        renderSequences(c, S);
                    };
                    S.openModal('Sequência ' + r.prefixo, f, [saveBtn]);
                };
                return b;
            }
        }
    ];
    document.getElementById('pm-seq').appendChild(S.table(cols, d.sequencias));
}

/* ── TV ─────────────────────────────────────────────────────────── */
async function renderTV(c, S) {
    c.innerHTML = '<h1 class="page-title">Painel TV</h1>';
    var d = await S.api('/parametros/config/tv');
    var fields = [
        ['title',     'Título'],
        ['interval',  'Intervalo em segundos', 'number'],
        ['last_rows', 'Quantidade de linhas',  'number']
    ];
    var form = S.el('div', { className: 'form-grid cols-2' });
    fields.forEach(function (f) {
        form.appendChild(_pField(f[1], f[0], d[f[0]] || '', f[2] || 'text'));
    });
    var saveBtn = S.el('button', { className: 'btn btn-primary mt-3', textContent: 'Salvar' });
    saveBtn.onclick = async function () {
        var body = Object.assign({}, d);
        fields.forEach(function (f) {
            body[f[0]] = document.getElementById(f[0]).value;
        });
        await S.api('/parametros/config/tv', { method: 'PUT', body: body });
        S.toast('Configuração TV salva.', 'success');
    };
    var card = S.el('div', { className: 'card' });
    var cardBody = S.el('div', { className: 'card-body' });
    cardBody.appendChild(form);
    card.appendChild(cardBody);
    c.appendChild(card);
    c.appendChild(saveBtn);
}

/* ── Minha conta ────────────────────────────────────────────────── */
function renderAccount(c, S) {
    var u = S.user();
    c.innerHTML =
        '<h1 class="page-title">Minha conta</h1>' +
        '<div class="card">' +
            '<div class="card-body">' +
                '<p><span class="text-muted">Usuário:</span> <strong>' + S.esc(u.username) + '</strong></p>' +
                '<p><span class="text-muted">Nome:</span> <strong>' + S.esc(u.display_name || '') + '</strong></p>' +
                '<p><span class="text-muted">Perfil:</span> <strong>' + S.esc(u.role || '') + '</strong></p>' +
                '<div class="form-grid cols-2">' +
                    '<div class="form-group"><label>Senha atual</label>' +
                        '<input id="pm-old" type="password" class="form-control"></div>' +
                    '<div class="form-group"><label>Nova senha</label>' +
                        '<input id="pm-new" type="password" class="form-control"></div>' +
                '</div>' +
                '<button id="pm-pass" class="btn btn-primary mt-2">Alterar senha</button>' +
            '</div>' +
        '</div>';

    document.getElementById('pm-pass').onclick = async function () {
        try {
            await S.api('/auth/change-password', {
                method: 'POST',
                body: {
                    current_password: document.getElementById('pm-old').value,
                    new_password:     document.getElementById('pm-new').value
                }
            });
            S.toast('Senha alterada.', 'success');
        } catch (e) {
            S.toast(e.message, 'error');
        }
    };
}
