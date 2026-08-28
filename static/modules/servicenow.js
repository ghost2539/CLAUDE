/* ================================================================
   Module: ServiceNow
   Upload de ativos do recebimento para alm_hardware
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.servicenow = {
    render: function (container) {
        var S = window.SPARE;
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
                        'são preenchidos automaticamente pelo EBS. Os campos abaixo são fixos para todos os ativos.</p>' +
                    '<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:1rem">' +
                        '<div><label>State</label>' +
                            '<input id="sn-state" class="form-control" value="In stock"></div>' +
                        '<div><label>Substate</label>' +
                            '<input id="sn-substate" class="form-control" value="available"></div>' +
                        '<div><label>Currency</label>' +
                            '<input id="sn-currency" class="form-control" value="R$"></div>' +
                        '<div><label>Acquisition Method</label>' +
                            '<input id="sn-acq" class="form-control" value="Purchase"></div>' +
                    '</div>' +
                    '<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:1rem;margin-top:1rem">' +
                        '<div><label>Expenditure Type</label>' +
                            '<input id="sn-exp" class="form-control" value="Capex"></div>' +
                        '<div><label>Depreciation</label>' +
                            '<input id="sn-dep" class="form-control" value="SL 5 Years"></div>' +
                        '<div><label>Stockroom</label>' +
                            '<input id="sn-stockroom" class="form-control" value="SPARE - CD324"></div>' +
                        '<div><label>Aisle and Space</label>' +
                            '<input id="sn-aisle" class="form-control" placeholder="Opcional"></div>' +
                    '</div>' +
                    '<div style="margin-top:1rem">' +
                        '<label style="cursor:pointer">' +
                            '<input type="checkbox" id="sn-calc-dep" checked style="margin-right:.4rem">' +
                            'Calcular depreciação após inserir</label>' +
                    '</div>' +
                '</div>' +
            '</div>' +

            /* ── Card 3: Credenciais SSO ── */
            '<div class="card mb-3">' +
                '<div class="card-header">Credenciais SSO</div>' +
                '<div class="card-body">' +
                    '<div style="display:grid;grid-template-columns:1fr 1fr auto auto;gap:1rem;align-items:end">' +
                        '<div><label>Usuário corporativo</label>' +
                            '<input id="sn-user" class="form-control" placeholder="usuario.corporativo" autocomplete="off"></div>' +
                        '<div><label>Senha</label>' +
                            '<input id="sn-pass" class="form-control" type="password" autocomplete="off"></div>' +
                        '<button class="btn" id="sn-test-login">Testar conexão</button>' +
                        '<button class="btn btn-primary" id="sn-upload" style="background:#c06010;border-color:#c06010">' +
                            'Enviar para ServiceNow</button>' +
                    '</div>' +
                    '<p style="color:var(--text-secondary);font-size:.85rem;margin-top:.5rem">' +
                        'As credenciais são usadas apenas para esta sessão e não são armazenadas.</p>' +
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
};

var _snData = [];

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
    var user = document.getElementById('sn-user').value.trim();
    var pass = document.getElementById('sn-pass').value;
    if (!user || !pass) { S.toast('Informe usuário e senha', 'warning'); return; }
    S.toast('Testando conexão SSO...', 'info');
    S.api('/servicenow/test-login', {
        method: 'POST',
        body: { usuario: user, senha: pass }
    }).then(function (d) {
        S.toast(d.message, 'success');
    }).catch(function (e) {
        S.toast(e.message, 'error');
    });
}

function _snStartUpload(S) {
    var ids = _snGetSelectedIds();
    if (!ids.length) { S.toast('Selecione ao menos um ativo', 'warning'); return; }
    var user = document.getElementById('sn-user').value.trim();
    var pass = document.getElementById('sn-pass').value;
    if (!user || !pass) { S.toast('Informe usuário e senha SSO', 'warning'); return; }

    if (!confirm('Enviar ' + ids.length + ' ativo(s) para o ServiceNow?')) return;

    var body = {
        cycle_ids: ids,
        usuario: user,
        senha: pass,
        // Campos fixos
        state: document.getElementById('sn-state').value,
        substate: document.getElementById('sn-substate').value,
        cost_currency: document.getElementById('sn-currency').value,
        acquisition_method: document.getElementById('sn-acq').value,
        expenditure_type: document.getElementById('sn-exp').value,
        depreciation: document.getElementById('sn-dep').value,
        stockroom: document.getElementById('sn-stockroom').value,
        aisle_space: document.getElementById('sn-aisle').value,
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
