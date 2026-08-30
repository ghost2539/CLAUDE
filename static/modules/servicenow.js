/* ================================================================
   Module: ServiceNow (Upload + Incidentes — sub-tabs)
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.servicenow = {

    render: function (container, sub) {
        var S = window.SPARE;
        var TAB_LIST = [
            ['upload',     'Upload Ativos'],
            ['incidentes', 'Incidentes']
        ];
        sub = sub || 'upload';
        S.tabs(TAB_LIST, sub, 'servicenow');

        var handlers = {
            upload:     _snRenderUpload,
            incidentes: _snRenderIncidentes
        };
        (handlers[sub] || _snRenderUpload)(container, S);
    }

};

/* ================================================================
   SUB-TAB: Upload Ativos (conteudo original)
   ================================================================ */

var _snData = [];

function _snRenderUpload(container, S) {
    container.innerHTML =
        '<h1 class="page-title">ServiceNow</h1>' +
        '<p style="color:var(--text-secondary);margin-bottom:1.5rem">' +
            'Upload de ativos do recebimento para alm_hardware via SSO.</p>' +

        /* ── Card 1: Filtros + Tabela ── */
        '<div class="card mb-3">' +
            '<div class="card-header">Selecionar Ativos do Recebimento</div>' +
            '<div class="card-body">' +
                '<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr auto;gap:.8rem;align-items:end;margin-bottom:1rem">' +
                    '<div><label>Status</label>' +
                        '<select id="sn-status" class="form-control"><option value="">Todos</option></select></div>' +
                    '<div><label>Lote</label>' +
                        '<input id="sn-lote" class="form-control" placeholder="Filtrar lote"></div>' +
                    '<div><label>Data início</label>' +
                        '<input id="sn-di" class="form-control" type="date"></div>' +
                    '<div><label>Data fim</label>' +
                        '<input id="sn-df" class="form-control" type="date"></div>' +
                    '<button class="btn" id="sn-search">Buscar</button>' +
                '</div>' +
                '<div style="display:flex;gap:.8rem;margin-bottom:1rem;align-items:center">' +
                    '<input id="sn-q" class="form-control" placeholder="Buscar série, etiqueta, modelo..." style="flex:1">' +
                    '<label style="white-space:nowrap;cursor:pointer">' +
                        '<input type="checkbox" id="sn-check-all" style="margin-right:.4rem">Selecionar todos</label>' +
                    '<span style="color:var(--text-secondary)">Selecionados: <strong id="sn-sel-count">0</strong></span>' +
                '</div>' +
                '<div id="sn-table" style="max-height:400px;overflow:auto">Clique em Buscar para carregar ativos.</div>' +
            '</div>' +
        '</div>' +

        /* ── Card 2: Configuração SN ── */
        '<div class="card mb-3">' +
            '<div class="card-header">Configuração ServiceNow</div>' +
            '<div class="card-body">' +
                '<p style="color:var(--text-secondary);font-size:.85rem;margin-bottom:1rem">' +
                    'Campos do ativo (serial_number, model, asset_tag, model_category, company, cost, purchased, depreciation_effective_date) ' +
                    'são preenchidos automaticamente pelo EBS. Os campos abaixo podem ser ajustados conforme necessário.</p>' +
                '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem">' +
                    '<div><label>Stockroom</label>' +
                        '<input id="sn-stockroom" class="form-control" value="SPARE - CD324"></div>' +
                    '<div><label>Aisle and Space</label>' +
                        '<input id="sn-aisle" class="form-control" placeholder="Opcional"></div>' +
                    '<div><label>Currency</label>' +
                        '<select id="sn-currency" class="form-control">' +
                            '<option value="BRL" selected>BRL - R$</option>' +
                            '<option value="USD">USD - $</option>' +
                            '<option value="ARS">ARS - $</option>' +
                            '<option value="UYU">UYU - $U</option>' +
                        '</select></div>' +
                '</div>' +
                '<div style="margin-top:1rem">' +
                    '<label style="cursor:pointer">' +
                        '<input type="checkbox" id="sn-calc-dep" checked style="margin-right:.4rem">' +
                        'Calcular depreciação após inserir</label>' +
                '</div>' +
            '</div>' +
        '</div>' +

        /* ── Card 3: Credenciais ServiceNow ── */
        '<div class="card mb-3">' +
            '<div class="card-header">Acesso ServiceNow</div>' +
            '<div class="card-body">' +
                '<div style="display:grid;grid-template-columns:1fr 1fr auto auto;gap:.8rem;align-items:end">' +
                    '<div><label>Usuário</label>' +
                        '<input id="sn-usuario" class="form-control" placeholder="Usuário corporativo"></div>' +
                    '<div><label>Senha</label>' +
                        '<input id="sn-senha" type="password" class="form-control" placeholder="Senha"></div>' +
                    '<button class="btn btn-sm" id="sn-test-login">Testar Login</button>' +
                    '<button class="btn btn-primary" id="sn-upload" style="background:#c06010;border-color:#c06010">' +
                        'Enviar para ServiceNow</button>' +
                '</div>' +
                '<div id="sn-login-status" style="margin-top:.5rem;font-size:.85rem"></div>' +
            '</div>' +
        '</div>' +

        /* ── Card 4: Progresso (hidden) ── */
        '<div class="card mb-3" id="sn-progress-card" style="display:none">' +
            '<div class="card-header">Progresso</div>' +
            '<div class="card-body">' +
                '<div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem">' +
                    '<div style="flex:1;background:var(--bg-input);border-radius:8px;height:24px;overflow:hidden">' +
                        '<div id="sn-bar" style="height:100%;background:#c06010;border-radius:8px;transition:width .3s;width:0%"></div>' +
                    '</div>' +
                    '<span id="sn-pct" style="min-width:60px;text-align:right;font-weight:600">0%</span>' +
                '</div>' +
                '<div id="sn-phase" style="color:var(--text-secondary);margin-bottom:.5rem"></div>' +
                '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin-bottom:1rem">' +
                    '<div style="text-align:center"><div style="font-size:1.8rem;font-weight:700" id="sn-s-total">0</div><div style="color:var(--text-secondary);font-size:.85rem">Total</div></div>' +
                    '<div style="text-align:center"><div style="font-size:1.8rem;font-weight:700;color:#16a34a" id="sn-s-ok">0</div><div style="color:var(--text-secondary);font-size:.85rem">Inseridos</div></div>' +
                    '<div style="text-align:center"><div style="font-size:1.8rem;font-weight:700;color:#dc2626" id="sn-s-err">0</div><div style="color:var(--text-secondary);font-size:.85rem">Erros</div></div>' +
                    '<div style="text-align:center"><div style="font-size:1.8rem;font-weight:700;color:#2563eb" id="sn-s-dep">0</div><div style="color:var(--text-secondary);font-size:.85rem">Depreciação</div></div>' +
                '</div>' +
                '<div id="sn-results" style="max-height:300px;overflow:auto"></div>' +
            '</div>' +
        '</div>';

    // Load status filter options
    S.api('/servicenow/statuses').then(function (d) {
        var sel = document.getElementById('sn-status');
        (d.statuses || []).forEach(function (st) {
            sel.innerHTML += '<option value="' + S.esc(st) + '">' + S.esc(st) + '</option>';
        });
    }).catch(function () {});

    // Events
    document.getElementById('sn-search').addEventListener('click', function () { _snSearch(S); });
    document.getElementById('sn-q').addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); _snSearch(S); }
    });
    document.getElementById('sn-check-all').addEventListener('change', function () {
        var cbs = document.querySelectorAll('.sn-cb');
        var checked = this.checked;
        cbs.forEach(function (cb) { cb.checked = checked; });
        _snUpdateCount();
    });
    document.getElementById('sn-test-login').addEventListener('click', function () { _snTestLogin(S); });
    document.getElementById('sn-upload').addEventListener('click', function () { _snStartUpload(S); });
}

function _snSearch(S) {
    var params = new URLSearchParams();
    var status = document.getElementById('sn-status').value;
    var lote = document.getElementById('sn-lote').value.trim();
    var di = document.getElementById('sn-di').value;
    var df = document.getElementById('sn-df').value;
    var q = document.getElementById('sn-q').value.trim();
    if (status) params.set('status', status);
    if (lote) params.set('lote', lote);
    if (di) params.set('data_inicio', di);
    if (df) params.set('data_fim', df);
    if (q) params.set('q', q);

    S.api('/servicenow/recebimentos?' + params.toString()).then(function (d) {
        _snData = d.recebimentos || [];
        _snRenderTable(S);
    }).catch(function (e) { S.toast(e.message, 'error'); });
}

function _snRenderTable(S) {
    var el = document.getElementById('sn-table');
    if (!_snData.length) {
        el.innerHTML = '<div style="padding:1rem;color:var(--text-secondary)">Nenhum ativo encontrado com os filtros informados.</div>';
        _snUpdateCount();
        return;
    }
    var html = '<table class="data-table"><thead><tr>' +
        '<th style="width:40px"><input type="checkbox" id="sn-check-all-tbl"></th>' +
        '<th>Empresa</th><th>Série</th><th>Etiqueta</th><th>Modelo</th>' +
        '<th>Categoria</th><th>Custo</th><th>Status</th><th>Lote</th><th>Data Rec.</th>' +
        '</tr></thead><tbody>';
    for (var i = 0; i < _snData.length; i++) {
        var r = _snData[i];
        html += '<tr>' +
            '<td><input type="checkbox" class="sn-cb" value="' + r.cycle_id + '"></td>' +
            '<td>' + S.esc(r.empresa) + '</td>' +
            '<td>' + S.esc(r.serie) + '</td>' +
            '<td>' + S.esc(r.etiqueta) + '</td>' +
            '<td>' + S.esc(r.modelo) + '</td>' +
            '<td>' + S.esc(r.categoria) + '</td>' +
            '<td>' + S.esc(r.custo) + '</td>' +
            '<td>' + S.badge(r.status) + '</td>' +
            '<td>' + S.esc(r.lote) + '</td>' +
            '<td>' + S.esc(r.data_recebimento) + '</td>' +
            '</tr>';
    }
    html += '</tbody></table>';
    el.innerHTML = html;

    // Bind checkbox events
    el.querySelectorAll('.sn-cb').forEach(function (cb) {
        cb.addEventListener('change', _snUpdateCount);
    });
    var allTbl = document.getElementById('sn-check-all-tbl');
    if (allTbl) {
        allTbl.addEventListener('change', function () {
            var checked = this.checked;
            document.getElementById('sn-check-all').checked = checked;
            el.querySelectorAll('.sn-cb').forEach(function (cb) { cb.checked = checked; });
            _snUpdateCount();
        });
    }
    _snUpdateCount();
}

function _snUpdateCount() {
    var count = document.querySelectorAll('.sn-cb:checked').length;
    var el = document.getElementById('sn-sel-count');
    if (el) el.textContent = count;
}

function _snGetSelectedIds() {
    var ids = [];
    document.querySelectorAll('.sn-cb:checked').forEach(function (cb) {
        ids.push(parseInt(cb.value));
    });
    return ids;
}

function _snTestLogin(S) {
    var usuario = document.getElementById('sn-usuario').value.trim();
    var senha = document.getElementById('sn-senha').value;
    var statusEl = document.getElementById('sn-login-status');
    if (!usuario || !senha) { S.toast('Informe usuário e senha', 'warning'); return; }
    statusEl.innerHTML = '<span style="color:var(--text-secondary)">Testando login...</span>';
    S.api('/servicenow/test-login', { method: 'POST', body: { usuario: usuario, senha: senha } })
        .then(function () {
            statusEl.innerHTML = '<span style="color:#16a34a;font-weight:600">Login OK</span>';
            S.toast('Login ServiceNow válido!', 'success');
        })
        .catch(function (e) {
            statusEl.innerHTML = '<span style="color:#dc2626;font-weight:600">Falhou: ' + S.esc(e.message) + '</span>';
        });
}

async function _snStartUpload(S) {
    var ids = _snGetSelectedIds();
    if (!ids.length) { S.toast('Selecione ao menos um ativo', 'warning'); return; }

    var usuario = document.getElementById('sn-usuario').value.trim();
    var senha = document.getElementById('sn-senha').value;
    if (!usuario || !senha) { S.toast('Informe usuário e senha do ServiceNow', 'warning'); return; }

    if (!confirm('Enviar ' + ids.length + ' ativo(s) para o ServiceNow?')) return;

    var body = {
        cycle_ids: ids,
        usuario: usuario,
        senha: senha,
        stockroom: document.getElementById('sn-stockroom').value,
        aisle_space: document.getElementById('sn-aisle').value,
        cost_currency: document.getElementById('sn-currency').value,
        calc_depreciation: document.getElementById('sn-calc-dep').checked,
    };

    document.getElementById('sn-upload').disabled = true;
    document.getElementById('sn-progress-card').style.display = '';
    document.getElementById('sn-s-total').textContent = ids.length;

    S.api('/servicenow/upload', { method: 'POST', body: body })
        .then(function (d) {
            S.toast('Upload iniciado — ' + d.total + ' ativo(s)', 'info');
            _snPollJob(S, d.job_id);
        })
        .catch(function (e) {
            S.toast(e.message, 'error');
            document.getElementById('sn-upload').disabled = false;
        });
}

var _snPollTimer = null;

function _snPollJob(S, jobId) {
    if (_snPollTimer) clearInterval(_snPollTimer);

    _snPollTimer = setInterval(function () {
        S.api('/servicenow/jobs/' + jobId).then(function (d) {
            // Update progress bar
            var pct = d.total > 0 ? Math.round((d.current / d.total) * 100) : 0;
            document.getElementById('sn-bar').style.width = pct + '%';
            document.getElementById('sn-pct').textContent = pct + '%';

            // Phase label
            var phases = {
                starting: 'Iniciando...',
                login: 'Autenticando SSO...',
                lookup: 'Resolvendo referências...',
                inserting: 'Inserindo ativos... (' + d.current + '/' + d.total + ')',
                done: 'Concluído',
            };
            document.getElementById('sn-phase').textContent = phases[d.phase] || d.phase;

            // Counters
            document.getElementById('sn-s-ok').textContent = d.ok_count;
            document.getElementById('sn-s-err').textContent = d.err_count;
            document.getElementById('sn-s-dep').textContent = d.dep_ok + (d.dep_fail > 0 ? ' / ' + d.dep_fail + ' falha' : '');

            // Results table
            if (d.results.length) {
                var html = '<table class="data-table"><thead><tr>' +
                    '<th>#</th><th>Série</th><th>Etiqueta</th><th>Empresa</th><th>Status</th><th>Depreciação</th><th>Detalhe</th>' +
                    '</tr></thead><tbody>';
                for (var i = 0; i < d.results.length; i++) {
                    var r = d.results[i];
                    var statusBadge = r.status === 'ok'
                        ? '<span style="color:#16a34a;font-weight:600">OK</span>'
                        : '<span style="color:#dc2626;font-weight:600">ERRO</span>';
                    var depBadge = '';
                    if (r.depreciation === 'ok') depBadge = '<span style="color:#16a34a">OK</span>';
                    else if (r.depreciation === 'falhou') depBadge = '<span style="color:#eab308">Falhou</span>';
                    else depBadge = '—';
                    html += '<tr>' +
                        '<td>' + r.idx + '</td>' +
                        '<td>' + S.esc(r.serie || '') + '</td>' +
                        '<td>' + S.esc(r.etiqueta || '') + '</td>' +
                        '<td>' + S.esc(r.empresa || '') + '</td>' +
                        '<td>' + statusBadge + '</td>' +
                        '<td>' + depBadge + '</td>' +
                        '<td style="font-size:.8rem;max-width:200px;overflow:hidden;text-overflow:ellipsis">' +
                            S.esc(r.display || r.detail || '') + '</td>' +
                        '</tr>';
                }
                html += '</tbody></table>';
                document.getElementById('sn-results').innerHTML = html;
            }

            // Done or error
            if (d.status === 'done') {
                clearInterval(_snPollTimer);
                _snPollTimer = null;
                document.getElementById('sn-upload').disabled = false;
                document.getElementById('sn-bar').style.width = '100%';
                document.getElementById('sn-pct').textContent = '100%';
                S.toast('Upload concluído: ' + d.ok_count + ' inseridos, ' + d.err_count + ' erros', 'success');
            } else if (d.status === 'error') {
                clearInterval(_snPollTimer);
                _snPollTimer = null;
                document.getElementById('sn-upload').disabled = false;
                document.getElementById('sn-phase').textContent = 'Erro: ' + d.error;
                document.getElementById('sn-phase').style.color = '#dc2626';
                S.toast(d.error, 'error');
            }
        }).catch(function (e) {
            clearInterval(_snPollTimer);
            _snPollTimer = null;
            document.getElementById('sn-upload').disabled = false;
            S.toast('Erro ao consultar progresso: ' + e.message, 'error');
        });
    }, 2000);
}


/* ================================================================
   SUB-TAB: Incidentes (via backend -> proxy -> ServiceNow)
   ================================================================ */

var _incPage = 0;
var _incLimit = 50;

function _snRenderIncidentes(container, S) {
    container.innerHTML =
        '<h1 class="page-title">Incidentes ServiceNow</h1>' +
        '<p style="color:var(--text-secondary);margin-bottom:1.5rem">' +
            'Consulta de incidentes na fila de atendimento.</p>' +

        /* ── Card: Resumo por estado ── */
        '<div class="card mb-3">' +
            '<div class="card-header">Resumo da Fila</div>' +
            '<div class="card-body">' +
                '<div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem">' +
                    '<div><label>Fila</label>' +
                        '<input id="inc-queue" class="form-control" value="TI_N2_FLD_RNR_LOJAS_SPARE" style="min-width:320px"></div>' +
                    '<button class="btn" id="inc-load" style="align-self:end">Carregar</button>' +
                '</div>' +
                '<div id="inc-summary" style="color:var(--text-secondary)">Clique em Carregar para consultar.</div>' +
            '</div>' +
        '</div>' +

        /* ── Card: Filtros + Lista ── */
        '<div class="card mb-3">' +
            '<div class="card-header">Lista de Incidentes</div>' +
            '<div class="card-body">' +
                '<div style="display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:.8rem;align-items:end;margin-bottom:1rem">' +
                    '<div><label>Estado</label>' +
                        '<select id="inc-state" class="form-control">' +
                            '<option value="">Todos</option>' +
                            '<option value="1">Novo</option>' +
                            '<option value="2">Em andamento</option>' +
                            '<option value="3">Em espera</option>' +
                            '<option value="6">Resolvido</option>' +
                            '<option value="7">Fechado</option>' +
                        '</select></div>' +
                    '<div><label>Prioridade</label>' +
                        '<select id="inc-priority" class="form-control">' +
                            '<option value="">Todas</option>' +
                            '<option value="1">1 - Crítica</option>' +
                            '<option value="2">2 - Alta</option>' +
                            '<option value="3">3 - Média</option>' +
                            '<option value="4">4 - Baixa</option>' +
                            '<option value="5">5 - Planejamento</option>' +
                        '</select></div>' +
                    '<div><label>Busca</label>' +
                        '<input id="inc-q" class="form-control" placeholder="Número, descrição, solicitante..."></div>' +
                    '<button class="btn" id="inc-search">Buscar</button>' +
                '</div>' +
                '<div id="inc-table" style="max-height:500px;overflow:auto">Carregue a fila primeiro.</div>' +
                '<div id="inc-pager" style="display:flex;justify-content:space-between;align-items:center;margin-top:.8rem" hidden>' +
                    '<button class="btn btn-sm" id="inc-prev" disabled>Anterior</button>' +
                    '<span id="inc-pager-info" style="color:var(--text-secondary);font-size:.85rem"></span>' +
                    '<button class="btn btn-sm" id="inc-next">Próximo</button>' +
                '</div>' +
            '</div>' +
        '</div>';

    document.getElementById('inc-load').addEventListener('click', function () {
        _incLoadSummary(S);
        _incPage = 0;
        _incSearch(S);
    });
    document.getElementById('inc-search').addEventListener('click', function () {
        _incPage = 0;
        _incSearch(S);
    });
    document.getElementById('inc-q').addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); _incPage = 0; _incSearch(S); }
    });
    document.getElementById('inc-prev').addEventListener('click', function () {
        if (_incPage > 0) { _incPage--; _incSearch(S); }
    });
    document.getElementById('inc-next').addEventListener('click', function () {
        _incPage++;
        _incSearch(S);
    });
}

function _incLoadSummary(S) {
    var queue = document.getElementById('inc-queue').value.trim();
    if (!queue) { S.toast('Informe o nome da fila', 'warning'); return; }
    var el = document.getElementById('inc-summary');
    el.innerHTML = '<span class="spinner spinner-sm"></span> Consultando...';

    S.api('/servicenow/incidents/count?queue=' + encodeURIComponent(queue))
        .then(function (d) {
            var html = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:1rem;text-align:center">';
            html += '<div><div style="font-size:2rem;font-weight:700;color:var(--color-primary)">' + d.total + '</div>' +
                    '<div style="color:var(--text-secondary);font-size:.85rem">Total</div></div>';
            var stateColors = {
                'New': '#3b82f6', 'Novo': '#3b82f6',
                'In Progress': '#f59e0b', 'Em andamento': '#f59e0b',
                'On Hold': '#8b5cf6', 'Em espera': '#8b5cf6',
                'Resolved': '#16a34a', 'Resolvido': '#16a34a',
                'Closed': '#6b7280', 'Fechado': '#6b7280'
            };
            Object.keys(d.by_state || {}).forEach(function (st) {
                var color = stateColors[st] || 'var(--text-primary)';
                html += '<div><div style="font-size:2rem;font-weight:700;color:' + color + '">' + d.by_state[st] + '</div>' +
                        '<div style="color:var(--text-secondary);font-size:.85rem">' + S.esc(st) + '</div></div>';
            });
            html += '</div>';
            el.innerHTML = html;
        })
        .catch(function (e) {
            el.innerHTML = '<span style="color:#dc2626">' + S.esc(e.message) + '</span>';
        });
}

function _incSearch(S) {
    var queue = document.getElementById('inc-queue').value.trim();
    if (!queue) { S.toast('Informe o nome da fila', 'warning'); return; }

    var params = new URLSearchParams();
    params.set('queue', queue);
    params.set('limit', _incLimit);
    params.set('offset', _incPage * _incLimit);
    var st = document.getElementById('inc-state').value;
    var pr = document.getElementById('inc-priority').value;
    var q = document.getElementById('inc-q').value.trim();
    if (st) params.set('state', st);
    if (pr) params.set('priority', pr);
    if (q) params.set('q', q);

    var tableEl = document.getElementById('inc-table');
    tableEl.innerHTML = '<div style="padding:1rem;color:var(--text-secondary)"><span class="spinner spinner-sm"></span> Buscando incidentes...</div>';

    S.api('/servicenow/incidents?' + params.toString())
        .then(function (d) {
            var incidents = d.incidents || [];
            var total = d.total || 0;

            if (!incidents.length) {
                tableEl.innerHTML = '<div style="padding:1rem;color:var(--text-secondary)">Nenhum incidente encontrado.</div>';
                document.getElementById('inc-pager').hidden = true;
                return;
            }

            var html = '<table class="data-table"><thead><tr>' +
                '<th>Número</th><th>Descrição</th><th>Estado</th><th>Prioridade</th>' +
                '<th>Atribuído a</th><th>Solicitante</th><th>Aberto em</th>' +
                '</tr></thead><tbody>';

            var stateStyles = {
                'New': 'background:#dbeafe;color:#1e40af',
                'Novo': 'background:#dbeafe;color:#1e40af',
                'In Progress': 'background:#fef3c7;color:#92400e',
                'Em andamento': 'background:#fef3c7;color:#92400e',
                'On Hold': 'background:#ede9fe;color:#5b21b6',
                'Em espera': 'background:#ede9fe;color:#5b21b6',
                'Resolved': 'background:#dcfce7;color:#166534',
                'Resolvido': 'background:#dcfce7;color:#166534',
                'Closed': 'background:#f3f4f6;color:#374151',
                'Fechado': 'background:#f3f4f6;color:#374151'
            };

            var prioStyles = {
                '1 - Critical': 'color:#dc2626;font-weight:700',
                '1 - Crítica': 'color:#dc2626;font-weight:700',
                '2 - High': 'color:#ea580c;font-weight:600',
                '2 - Alta': 'color:#ea580c;font-weight:600',
                '3 - Moderate': 'color:#ca8a04',
                '3 - Média': 'color:#ca8a04',
                '4 - Low': 'color:#6b7280',
                '4 - Baixa': 'color:#6b7280',
                '5 - Planning': 'color:#9ca3af',
                '5 - Planejamento': 'color:#9ca3af'
            };

            for (var i = 0; i < incidents.length; i++) {
                var inc = incidents[i];
                var stLabel = inc.state || '';
                var stStyle = stateStyles[stLabel] || '';
                var prLabel = inc.priority || '';
                var prStyle = prioStyles[prLabel] || '';
                var assignedTo = '';
                if (inc.assigned_to && typeof inc.assigned_to === 'object') {
                    assignedTo = inc.assigned_to.display_value || '';
                } else {
                    assignedTo = inc.assigned_to || '';
                }
                var caller = '';
                if (inc.caller_id && typeof inc.caller_id === 'object') {
                    caller = inc.caller_id.display_value || '';
                } else {
                    caller = inc.caller_id || '';
                }

                html += '<tr>' +
                    '<td style="white-space:nowrap;font-weight:600">' + S.esc(inc.number || '') + '</td>' +
                    '<td style="max-width:300px;overflow:hidden;text-overflow:ellipsis">' +
                        S.esc(inc.short_description || '') + '</td>' +
                    '<td><span style="padding:2px 8px;border-radius:4px;font-size:.8rem;white-space:nowrap;' +
                        stStyle + '">' + S.esc(stLabel) + '</span></td>' +
                    '<td style="' + prStyle + '">' + S.esc(prLabel) + '</td>' +
                    '<td>' + S.esc(assignedTo) + '</td>' +
                    '<td>' + S.esc(caller) + '</td>' +
                    '<td style="white-space:nowrap;font-size:.85rem">' + S.esc(inc.opened_at || '') + '</td>' +
                    '</tr>';
            }
            html += '</tbody></table>';
            tableEl.innerHTML = html;

            // Pager
            var pager = document.getElementById('inc-pager');
            pager.hidden = total <= _incLimit && _incPage === 0;
            var from = _incPage * _incLimit + 1;
            var to = Math.min(from + incidents.length - 1, total);
            document.getElementById('inc-pager-info').textContent =
                from + '–' + to + ' de ' + total;
            document.getElementById('inc-prev').disabled = _incPage === 0;
            document.getElementById('inc-next').disabled = to >= total;
        })
        .catch(function (e) {
            tableEl.innerHTML = '<div style="padding:1rem;color:#dc2626">' + S.esc(e.message) + '</div>';
            document.getElementById('inc-pager').hidden = true;
        });
}
