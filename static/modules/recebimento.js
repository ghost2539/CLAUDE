/* ================================================================
   Module: Recebimento (Receiving — all sub-tabs)
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.recebimento = {

    render(container, sub) {
        var S = window.SPARE;
        var TAB_LIST = [
            ['novo',      'Novo Recebimento'],
            ['base',      'Base de Recebimentos'],
            ['dashboard', 'Dashboard'],
            ['lotes',     'Lotes'],
            ['modelos',   'Cadastro de modelos'],
            ['historico', 'Importar base histórica'],
            ['local',     'Base local EBS']
        ];
        sub = sub || 'novo';
        S.tabs(TAB_LIST, sub, 'recebimento');

        var handlers = {
            novo:      renderNovo,
            base:      renderBase,
            dashboard: renderDashboard,
            lotes:     renderLotes,
            modelos:   renderModelos,
            historico: renderHistorico,
            local:     renderLocal
        };
        (handlers[sub] || renderNovo)(container, S);
    }

};

/* ── Novo Recebimento ───────────────────────────────────────────── */
function renderNovo(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Novo Recebimento</h1>' +
        '<div class="card mb-3">' +
            '<div class="card-header">Leitura de Ativo</div>' +
            '<div class="card-body">' +
                '<input id="scan" class="form-control scan-input" ' +
                    'placeholder="Bipe ou digite e pressione Enter" autofocus>' +
                '<div id="scan-feedback" class="mt-2"></div>' +
            '</div>' +
        '</div>' +
        '<div class="card">' +
            '<div class="card-header">Últimos Recebidos</div>' +
            '<div id="scan-list" class="card-body"></div>' +
        '</div>';

    var rows = [];
    var cols = [
        { key: 'hora',        label: 'Hora' },
        { key: 'imobilizado', label: 'Imobilizado' },
        { key: 'descricao',   label: 'Descrição' },
        { key: 'categoria',   label: 'Categoria' },
        { key: 'status',      label: 'Status', html: true, render: function (v) { return S.badge(v); } }
    ];

    document.getElementById('scan').onkeydown = async function (e) {
        if (e.key !== 'Enter') return;
        var v = e.target.value.trim();
        if (!v) return;
        e.target.disabled = true;
        try {
            var d = await S.api('/recebimento/scan', {
                method: 'POST',
                body: { identificador: v }
            });
            var fb = document.getElementById('scan-feedback');
            fb.innerHTML = '<div class="alert ' +
                (d.warning ? 'alert-warning' : 'alert-success') + '">' +
                S.esc(d.warning || 'Recebimento registrado.') + '</div>';
            if (!d.existing) {
                rows.unshift(d);
                var list = document.getElementById('scan-list');
                list.innerHTML = '';
                list.appendChild(S.table(cols, rows));
            }
        } catch (x) {
            document.getElementById('scan-feedback').innerHTML =
                '<div class="alert alert-danger">' + S.esc(x.message) + '</div>';
        } finally {
            e.target.value = '';
            e.target.disabled = false;
            e.target.focus();
        }
    };
}

/* ── Base de Recebimentos ───────────────────────────────────────── */
async function renderBase(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Base de Recebimentos</h1>' +
        '<div class="btn-row mb-3">' +
            '<button id="bf-snow" class="btn btn-secondary">Exportar ServiceNow CSV</button>' +
        '</div>' +
        '<div class="card mb-3">' +
            '<div class="card-body filter-grid">' +
                '<div class="form-group"><label>Status</label><input id="bf-status" class="form-control"></div>' +
                '<div class="form-group"><label>Empresa</label><input id="bf-company" class="form-control"></div>' +
                '<div class="form-group"><label>Categoria</label><input id="bf-cat" class="form-control"></div>' +
                '<div class="form-group"><label>Busca</label><input id="bf-q" class="form-control"></div>' +
                '<div class="form-group"><button id="bf-run" class="btn btn-primary">Filtrar</button></div>' +
            '</div>' +
        '</div>' +
        '<div id="bf-out"></div>';

    async function load() {
        try {
            var p = new URLSearchParams({
                status:    document.getElementById('bf-status').value,
                empresa:   document.getElementById('bf-company').value,
                categoria: document.getElementById('bf-cat').value,
                q:         document.getElementById('bf-q').value
            });
            var d = await S.api('/recebimentos?' + p);
            var cols = [
                ['id',                'ID'],
                ['data_recebimento',  'Data'],
                ['empresa',           'Empresa'],
                ['imobilizado',       'Imobilizado'],
                ['etiqueta',          'Etiqueta'],
                ['numero_serie',      'Nº Série'],
                ['descricao',         'Descrição'],
                ['categoria',         'Categoria'],
                ['modelo',            'Modelo'],
                ['status',            'Status'],
                ['local',             'Local'],
                ['lote',              'Lote']
            ].map(function (x) {
                return {
                    key: x[0],
                    label: x[1],
                    html: x[0] === 'status',
                    render: x[0] === 'status' ? function (v) { return S.badge(v); } : undefined
                };
            });
            var out = document.getElementById('bf-out');
            out.innerHTML = '';
            out.appendChild(S.table(cols, d.registros));
        } catch (e) {
            S.toast(e.message, 'error');
        }
    }

    document.getElementById('bf-run').onclick = load;
    document.getElementById('bf-snow').onclick = function () {
        window.location = '/api/recebimentos/export-servicenow';
    };
    load();
}

/* ── Dashboard ──────────────────────────────────────────────────── */
async function renderDashboard(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Dashboard de Recebimentos</h1>' +
        '<div id="rd-stats" class="stats-grid mb-3"></div>' +
        '<div id="rd-tables" class="charts-grid"></div>';

    try {
        var d = await S.api('/recebimentos/dashboard');
        var stats = [
            ['Total',          d.total,       'teal'],
            ['Ativos Únicos',  d.unicos,      'orange'],
            ['Retornos',       d.devolucoes,   'gold']
        ];
        document.getElementById('rd-stats').innerHTML = stats.map(function (x) {
            return '<div class="stat-card accent-' + x[2] + '">' +
                '<div class="stat-value">' + x[1] + '</div>' +
                '<div class="stat-label">' + x[0] + '</div>' +
            '</div>';
        }).join('');

        var groups = [
            ['Por Empresa',   d.por_empresa,   'empresa'],
            ['Por Categoria', d.por_categoria,  'categoria'],
            ['Por Status',    d.por_status,     'status'],
            ['Por Local',     d.por_local,      'local']
        ];
        var tables = document.getElementById('rd-tables');
        tables.innerHTML = '';
        groups.forEach(function (g) {
            var card = S.el('div', { className: 'card' }, [
                S.el('div', { className: 'card-header', textContent: g[0] }),
                S.el('div', { className: 'card-body' },
                    S.table(
                        [{ key: g[2], label: g[2] }, { key: 'total', label: 'Total' }],
                        g[1]
                    )
                )
            ]);
            tables.appendChild(card);
        });
    } catch (e) {
        S.toast(e.message, 'error');
    }
}

/* ── Lotes ──────────────────────────────────────────────────────── */
function renderLotes(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Lotes</h1>' +
        '<div class="card mb-3">' +
            '<div class="card-body form-grid cols-2">' +
                '<div class="form-group"><label>Modo</label>' +
                    '<select id="lot-mode" class="form-control">' +
                        '<option value="base">Selecionar na base</option>' +
                        '<option value="scan">Bipar</option>' +
                    '</select>' +
                '</div>' +
                '<div class="form-group"><label>Tipo</label>' +
                    '<select id="lot-prefix" class="form-control">' +
                        '<option>VENDA</option><option>TRIAGEM</option>' +
                    '</select>' +
                '</div>' +
            '</div>' +
        '</div>' +
        '<div id="lot-scan-card" class="card mb-3" hidden>' +
            '<div class="card-body">' +
                '<input id="lot-scan" class="form-control scan-input" placeholder="Bipe o ativo">' +
            '</div>' +
        '</div>' +
        '<div class="card mb-3">' +
            '<div class="card-header">Itens</div>' +
            '<div id="lot-items" class="card-body"></div>' +
        '</div>' +
        '<button id="lot-create" class="btn btn-primary">Gerar Lote</button>';

    var items = [];
    var cols = [
        {
            key: 'choose', label: '',
            render: function (_, r) {
                var x = S.el('input', { type: 'checkbox' });
                x.checked = true;
                x.onchange = function () { r.selected = x.checked; };
                r.selected = true;
                return x;
            }
        },
        { key: 'id',          label: 'ID' },
        { key: 'imobilizado', label: 'Imobilizado' },
        { key: 'descricao',   label: 'Descrição' },
        { key: 'status',      label: 'Status', html: true, render: function (v) { return S.badge(v); } }
    ];

    function draw() {
        var el = document.getElementById('lot-items');
        el.innerHTML = '';
        el.appendChild(S.table(cols, items));
    }

    async function loadBase() {
        var d = await S.api('/recebimentos?status=RECEBIDO&limit=500');
        items = d.registros;
        draw();
    }

    document.getElementById('lot-mode').onchange = function (e) {
        if (e.target.value === 'scan') {
            document.getElementById('lot-scan-card').hidden = false;
            items = [];
            draw();
            document.getElementById('lot-scan').focus();
        } else {
            document.getElementById('lot-scan-card').hidden = true;
            loadBase();
        }
    };

    document.getElementById('lot-scan').onkeydown = async function (e) {
        if (e.key !== 'Enter') return;
        var v = e.target.value.trim();
        try {
            var d = await S.api('/recebimentos?q=' + encodeURIComponent(v));
            var x = d.registros[0];
            if (!x) return S.toast('Ativo não está na base de recebimentos.', 'warning');
            if (!items.some(function (i) { return i.id === x.id; })) items.push(x);
            draw();
        } catch (z) {
            S.toast(z.message, 'error');
        }
        e.target.value = '';
    };

    document.getElementById('lot-create').onclick = async function () {
        var ids = items
            .filter(function (x) { return x.selected !== false; })
            .map(function (x) { return x.id; });
        if (!ids.length) return S.toast('Selecione itens.', 'warning');
        try {
            var d = await S.api('/lotes', {
                method: 'POST',
                body: { prefixo: document.getElementById('lot-prefix').value, ids: ids }
            });
            S.toast('Lote ' + d.numero_lote + ' gerado para ' + d.quantidade + ' item(ns).', 'success');
            items = [];
            draw();
        } catch (e) {
            S.toast(e.message, 'error');
        }
    };

    loadBase();
}

/* ── Cadastro de modelos (Classifications) ──────────────────────── */
async function renderModelos(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Cadastro de modelos</h1>' +
        '<p class="text-muted">Ao salvar uma regra ativa, todos os ativos compatíveis são atualizados automaticamente.</p>' +
        '<button id="pm-class-add" class="btn btn-primary mb-3">Nova regra</button>' +
        '<button id="pm-class-import" class="btn btn-secondary mb-3" style="margin-left:8px">Importar planilha</button>' +
        '<div id="pm-class-list"></div>';

    function field(label, id, value) {
        var d = S.el('div', { className: 'form-group' });
        d.innerHTML = '<label>' + S.esc(label) + '</label>' +
            '<input id="' + id + '" class="form-control" value="' + S.esc(value || '') + '">';
        return d;
    }

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
                    var w = S.el('div', { className: 'btn-row' });
                    var e = S.el('button', { className: 'btn btn-sm btn-outline', textContent: 'Editar' });
                    var a = S.el('button', { className: 'btn btn-sm btn-secondary', textContent: 'Aplicar' });
                    e.onclick = function () { edit(r); };
                    a.onclick = async function () {
                        var x = await S.api('/parametros/classificacoes/' + r.id, { method: 'PUT', body: { padrao_descricao: r.padrao_descricao, empresa: r.empresa, categoria: r.categoria, modelo: r.modelo, ativo: true } });
                        S.toast('Regra atualizada.', 'success');
                    };
                    w.append(e, a);
                    return w;
                }
            }
        ];
        var el = document.getElementById('pm-class-list');
        el.innerHTML = '';
        el.appendChild(S.table(cols, d.regras));
    }

    function edit(r) {
        r = r || {};
        var f = S.el('div');
        [
            ['Padrão da descrição', 'pm-cp', r.padrao_descricao],
            ['Empresa (opcional)',   'pm-ce', r.empresa],
            ['Categoria',           'pm-cc', r.categoria],
            ['Modelo',              'pm-cm', r.modelo]
        ].forEach(function (x) { f.appendChild(field(x[0], x[1], x[2])); });

        var saveBtn = S.el('button', { className: 'btn btn-primary', textContent: 'Salvar e atualizar base' });
        saveBtn.onclick = async function () {
            try {
                var x = await S.api('/parametros/classificacoes' + (r.id ? '/' + r.id : ''), {
                    method: r.id ? 'PUT' : 'POST',
                    body: {
                        padrao_descricao: document.getElementById('pm-cp').value,
                        empresa:          document.getElementById('pm-ce').value,
                        categoria:        document.getElementById('pm-cc').value,
                        modelo:           document.getElementById('pm-cm').value,
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

    document.getElementById('pm-class-add').onclick = function () { edit(); };

    document.getElementById('pm-class-import').onclick = function () {
        var f = S.el('div');
        f.innerHTML =
            '<p class="text-muted">Colunas obrigatórias: Descrição EBS, Model category e Model.</p>' +
            '<input id="pm-class-file" type="file" accept=".xlsx,.xls,.csv" class="form-control">';
        var btn = S.el('button', { className: 'btn btn-primary', textContent: 'Importar e atualizar bases' });
        btn.onclick = async function () {
            var file = document.getElementById('pm-class-file').files[0];
            if (!file) return S.toast('Selecione uma planilha.', 'warning');
            var fd = new FormData();
            fd.append('file', file);
            try {
                S.toast('Importação de classificações requer upload individual por enquanto.', 'warning');
                S.closeModal();
                load();
            } catch (e) {
                S.toast(e.message, 'error');
            }
        };
        S.openModal('Importar classificações', f, [btn]);
    };

    load();
}

/* ── Importar base histórica ────────────────────────────────────── */
function renderHistorico(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Importar base histórica</h1>' +
        '<div class="card">' +
            '<div class="card-body">' +
                '<p class="text-muted">Aceita CSV ou XLSX com identificador, data, empresa, ' +
                    'categoria, modelo, status, local e lote.</p>' +
                '<form id="hi-form">' +
                    '<input name="file" type="file" class="form-control" required>' +
                    '<button class="btn btn-primary mt-2">Importar</button>' +
                '</form>' +
                '<div id="hi-result" class="mt-2"></div>' +
            '</div>' +
        '</div>';

    document.getElementById('hi-form').onsubmit = async function (e) {
        e.preventDefault();
        try {
            S.loading(true);
            var d = await S.api('/recebimentos/import-historico', {
                method: 'POST',
                body: new FormData(e.target)
            });
            document.getElementById('hi-result').innerHTML =
                '<div class="alert alert-success">' +
                d.importados + ' importados; ' + d.rejeitados + ' rejeitados.</div>';
        } catch (x) {
            S.toast(x.message, 'error');
        } finally {
            S.loading(false);
        }
    };
}

/* ── Base local EBS ─────────────────────────────────────────────── */
function renderLocal(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Base local EBS</h1>' +
        '<div class="card">' +
            '<div class="card-body">' +
                '<form id="local-form">' +
                    '<div class="form-grid cols-2">' +
                        '<div class="form-group"><label>Empresa</label>' +
                            '<select name="company" class="form-control">' +
                                '<option>RENNER</option>' +
                                '<option>YOUCOM</option>' +
                                '<option>CAMICADO</option>' +
                            '</select>' +
                        '</div>' +
                        '<div class="form-group"><label>Modo</label>' +
                            '<select name="mode" class="form-control">' +
                                '<option>SUBSTITUIR</option>' +
                                '<option>INCREMENTAR</option>' +
                            '</select>' +
                        '</div>' +
                    '</div>' +
                    '<div class="form-group mt-2">' +
                        '<label>Arquivo CSV/XLSX</label>' +
                        '<input name="file" type="file" class="form-control" required>' +
                    '</div>' +
                    '<button class="btn btn-primary mt-2">Importar</button>' +
                '</form>' +
                '<div id="local-result" class="mt-2"></div>' +
            '</div>' +
        '</div>';

    document.getElementById('local-form').onsubmit = async function (e) {
        e.preventDefault();
        try {
            S.loading(true);
            var d = await S.api('/parametros/base-local/upload', {
                method: 'POST',
                body: new FormData(e.target)
            });
            document.getElementById('local-result').innerHTML =
                '<div class="alert alert-success">' +
                d.validos + ' válidos; ' + d.rejeitados + ' rejeitados.</div>';
        } catch (x) {
            S.toast(x.message, 'error');
        } finally {
            S.loading(false);
        }
    };
}
