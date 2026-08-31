/* ================================================================
   Module: ServiceNow (4 sub-tabs)
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.servicenow = {

    render: function (container, sub) {
        var S = window.SPARE;
        var TAB_LIST = [
            ['entrada',   'Entrada de estoque'],
            ['saida',     'Saída de estoque'],
            ['correios',  'Chamados correios'],
            ['relatorios','Relatórios']
        ];
        sub = sub || 'entrada';
        S.tabs(TAB_LIST, sub, 'servicenow');

        var handlers = {
            entrada:    _snRenderUpload,
            saida:      _snRenderSaida,
            correios:   _snRenderCorreios,
            relatorios: _snRenderRelatorios
        };
        (handlers[sub] || _snRenderUpload)(container, S);
    }

};

/* ================================================================
   SUB-TAB 1: Entrada de estoque (upload de ativos — conteúdo original)
   ================================================================ */

var _snData = [];

function _snRenderUpload(container, S) {
    container.innerHTML =
        '<h1 class="page-title">Entrada de Estoque</h1>' +
        '<p style="color:var(--text-secondary);margin-bottom:1.5rem">' +
            'Upload de ativos do recebimento para alm_hardware via SSO.</p>' +

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

    S.api('/servicenow/statuses').then(function (d) {
        var sel = document.getElementById('sn-status');
        (d.statuses || []).forEach(function (st) {
            sel.innerHTML += '<option value="' + S.esc(st) + '">' + S.esc(st) + '</option>';
        });
    }).catch(function () {});

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
            var pct = d.total > 0 ? Math.round((d.current / d.total) * 100) : 0;
            document.getElementById('sn-bar').style.width = pct + '%';
            document.getElementById('sn-pct').textContent = pct + '%';

            var phases = {
                starting: 'Iniciando...',
                login: 'Autenticando SSO...',
                lookup: 'Resolvendo referências...',
                inserting: 'Inserindo ativos... (' + d.current + '/' + d.total + ')',
                done: 'Concluído',
            };
            document.getElementById('sn-phase').textContent = phases[d.phase] || d.phase;

            document.getElementById('sn-s-ok').textContent = d.ok_count;
            document.getElementById('sn-s-err').textContent = d.err_count;
            document.getElementById('sn-s-dep').textContent = d.dep_ok + (d.dep_fail > 0 ? ' / ' + d.dep_fail + ' falha' : '');

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
   SUB-TAB 2: Saída de estoque
   ================================================================ */

function _snRenderSaida(container, S) {
    container.innerHTML =
        '<h1 class="page-title">Saída de Estoque</h1>' +
        '<p style="color:var(--text-secondary);margin-bottom:1.5rem">' +
            'Busca e movimentação de ativos no ServiceNow (alm_hardware).</p>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Buscar Ativo</div>' +
            '<div class="card-body">' +
                '<div style="display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:.8rem;align-items:end">' +
                    '<div><label>Asset Tag</label>' +
                        '<input id="sa-tag" class="form-control" placeholder="Ex: A-123456"></div>' +
                    '<div><label>Número de Série</label>' +
                        '<input id="sa-serial" class="form-control" placeholder="Ex: SN12345678"></div>' +
                    '<div><label>Stockroom</label>' +
                        '<select id="sa-stockroom" class="form-control">' +
                            '<option value="">Todos (SPARE)</option>' +
                            '<option value="SPARE-ADM15">SPARE-ADM15</option>' +
                            '<option value="SPARE-CD324">SPARE-CD324</option>' +
                            '<option value="SPARE-CD504">SPARE-CD504</option>' +
                        '</select></div>' +
                    '<button class="btn btn-primary" id="sa-search">Buscar</button>' +
                '</div>' +
                '<p style="color:var(--text-secondary);font-size:.8rem;margin-top:.5rem">' +
                    'A busca usa Asset Tag OU Número de Série. Stockroom é obrigatório (filtra SPARE-ADM15, SPARE-CD324 ou SPARE-CD504).</p>' +
            '</div>' +
        '</div>' +

        '<div id="sa-results" style="display:none">' +
            '<div class="card mb-3">' +
                '<div class="card-header">Resultados</div>' +
                '<div class="card-body">' +
                    '<div id="sa-table" style="max-height:400px;overflow:auto"></div>' +
                '</div>' +
            '</div>' +
        '</div>' +

        '<div id="sa-action" style="display:none">' +
            '<div class="card mb-3">' +
                '<div class="card-header">Movimentar Ativo</div>' +
                '<div class="card-body">' +
                    '<div id="sa-selected-info" style="margin-bottom:1rem"></div>' +
                    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">' +
                        '<div><label>Novo Status</label>' +
                            '<select id="sa-new-status" class="form-control">' +
                                '<option value="In transit">Em Trânsito</option>' +
                                '<option value="In use">Em Uso</option>' +
                                '<option value="In stock">Em Estoque</option>' +
                                '<option value="On maintenance">Em Manutenção</option>' +
                                '<option value="Retired">Desativado</option>' +
                            '</select></div>' +
                        '<div><label>Stockroom Destino (opcional)</label>' +
                            '<input id="sa-dest-stockroom" class="form-control" placeholder="Ex: SPARE-CD504"></div>' +
                    '</div>' +
                    '<div style="margin-top:1rem"><label>Observações</label>' +
                        '<textarea id="sa-notes" class="form-control" rows="2" placeholder="Motivo da movimentação..."></textarea></div>' +
                    '<div style="margin-top:1rem">' +
                        '<button class="btn btn-primary" id="sa-move">Confirmar Saída</button>' +
                    '</div>' +
                    '<div id="sa-move-result" style="margin-top:.5rem"></div>' +
                '</div>' +
            '</div>' +
        '</div>';

    document.getElementById('sa-search').addEventListener('click', function () { _saSearch(S); });
    document.getElementById('sa-tag').addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); _saSearch(S); }
    });
    document.getElementById('sa-serial').addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); _saSearch(S); }
    });
}

var _saSelectedAsset = null;

function _saSearch(S) {
    var tag = document.getElementById('sa-tag').value.trim();
    var serial = document.getElementById('sa-serial').value.trim();
    var stockroom = document.getElementById('sa-stockroom').value;

    if (!tag && !serial) { S.toast('Informe Asset Tag ou Número de Série', 'warning'); return; }

    var body = { asset_tag: tag, serial_number: serial, stockroom: stockroom };

    document.getElementById('sa-results').style.display = '';
    document.getElementById('sa-table').innerHTML =
        '<div style="padding:1rem;color:var(--text-secondary)"><span class="spinner spinner-sm"></span> Buscando...</div>';
    document.getElementById('sa-action').style.display = 'none';

    S.api('/servicenow/saida/search', { method: 'POST', body: body })
        .then(function (d) {
            var assets = d.assets || [];
            if (!assets.length) {
                document.getElementById('sa-table').innerHTML =
                    '<div style="padding:1rem;color:var(--text-secondary)">Nenhum ativo encontrado.</div>';
                return;
            }

            var html = '<table class="data-table"><thead><tr>' +
                '<th></th><th>Asset Tag</th><th>Série</th><th>Nome</th><th>Modelo</th>' +
                '<th>Stockroom</th><th>Status</th><th>Empresa</th>' +
                '</tr></thead><tbody>';

            for (var i = 0; i < assets.length; i++) {
                var a = assets[i];
                var displayVal = function (v) {
                    if (!v) return '';
                    if (typeof v === 'object') return v.display_value || v.value || '';
                    return v;
                };
                html += '<tr style="cursor:pointer" data-idx="' + i + '">' +
                    '<td><input type="radio" name="sa-sel" value="' + i + '"></td>' +
                    '<td style="font-weight:600">' + S.esc(displayVal(a.asset_tag)) + '</td>' +
                    '<td>' + S.esc(displayVal(a.serial_number)) + '</td>' +
                    '<td>' + S.esc(displayVal(a.display_name)) + '</td>' +
                    '<td>' + S.esc(displayVal(a.model)) + '</td>' +
                    '<td>' + S.esc(displayVal(a.stockroom)) + '</td>' +
                    '<td>' + S.esc(displayVal(a.install_status)) + '</td>' +
                    '<td>' + S.esc(displayVal(a.company)) + '</td>' +
                    '</tr>';
            }
            html += '</tbody></table>';
            document.getElementById('sa-table').innerHTML = html;

            // Store assets data for selection
            window._saAssets = assets;

            document.querySelectorAll('#sa-table tr[data-idx]').forEach(function (row) {
                row.addEventListener('click', function () {
                    var idx = parseInt(this.getAttribute('data-idx'));
                    var radio = this.querySelector('input[type=radio]');
                    if (radio) radio.checked = true;
                    _saSelectAsset(S, window._saAssets[idx]);
                });
            });
        })
        .catch(function (e) {
            document.getElementById('sa-table').innerHTML =
                '<div style="padding:1rem;color:#dc2626">' + S.esc(e.message) + '</div>';
        });
}

function _saSelectAsset(S, asset) {
    _saSelectedAsset = asset;
    var displayVal = function (v) {
        if (!v) return '';
        if (typeof v === 'object') return v.display_value || v.value || '';
        return v;
    };

    document.getElementById('sa-action').style.display = '';
    document.getElementById('sa-selected-info').innerHTML =
        '<div class="asset-info-grid">' +
            '<div><strong>Asset Tag:</strong> ' + S.esc(displayVal(asset.asset_tag)) + '</div>' +
            '<div><strong>Série:</strong> ' + S.esc(displayVal(asset.serial_number)) + '</div>' +
            '<div><strong>Nome:</strong> ' + S.esc(displayVal(asset.display_name)) + '</div>' +
            '<div><strong>Stockroom:</strong> ' + S.esc(displayVal(asset.stockroom)) + '</div>' +
            '<div><strong>Status:</strong> ' + S.esc(displayVal(asset.install_status)) + '</div>' +
        '</div>';

    document.getElementById('sa-move').onclick = function () { _saMove(S); };
}

function _saMove(S) {
    if (!_saSelectedAsset) return;
    var sysId = _saSelectedAsset.sys_id;
    if (typeof sysId === 'object') sysId = sysId.value || sysId.display_value;

    if (!confirm('Confirmar saída deste ativo?')) return;

    var body = {
        sys_id: sysId,
        install_status: document.getElementById('sa-new-status').value,
        destination_stockroom: document.getElementById('sa-dest-stockroom').value.trim(),
        notes: document.getElementById('sa-notes').value.trim()
    };

    document.getElementById('sa-move').disabled = true;
    S.api('/servicenow/saida/move', { method: 'POST', body: body })
        .then(function () {
            document.getElementById('sa-move-result').innerHTML =
                '<span style="color:#16a34a;font-weight:600">Ativo movimentado com sucesso!</span>';
            S.toast('Saída registrada com sucesso!', 'success');
            document.getElementById('sa-move').disabled = false;
        })
        .catch(function (e) {
            document.getElementById('sa-move-result').innerHTML =
                '<span style="color:#dc2626">' + S.esc(e.message) + '</span>';
            document.getElementById('sa-move').disabled = false;
        });
}


/* ================================================================
   SUB-TAB 3: Chamados correios
   ================================================================ */

function _snRenderCorreios(container, S) {
    container.innerHTML =
        '<h1 class="page-title">Chamados Correios</h1>' +
        '<p style="color:var(--text-secondary);margin-bottom:1.5rem">' +
            'Incidentes com códigos de rastreio (campo correlation_display).</p>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Filtros</div>' +
            '<div class="card-body">' +
                '<div style="display:flex;gap:1rem;align-items:end">' +
                    '<div style="flex:1"><label>Fila</label>' +
                        '<input id="co-queue" class="form-control" value="TI_N2_FLD_RNR_LOJAS_SPARE"></div>' +
                    '<button class="btn btn-primary" id="co-load">Carregar</button>' +
                '</div>' +
            '</div>' +
        '</div>' +

        '<div id="co-table-wrap" style="display:none">' +
            '<div class="card mb-3">' +
                '<div class="card-header">Chamados com Rastreio</div>' +
                '<div class="card-body">' +
                    '<div id="co-table" style="max-height:500px;overflow:auto"></div>' +
                    '<div id="co-pager" style="display:flex;justify-content:space-between;align-items:center;margin-top:.8rem" hidden>' +
                        '<button class="btn btn-sm" id="co-prev" disabled>Anterior</button>' +
                        '<span id="co-pager-info" style="color:var(--text-secondary);font-size:.85rem"></span>' +
                        '<button class="btn btn-sm" id="co-next">Próximo</button>' +
                    '</div>' +
                '</div>' +
            '</div>' +
        '</div>' +

        '<div class="alert alert-info">' +
            'A integração com a API dos Correios será configurada posteriormente. ' +
            'Por enquanto, os códigos de rastreio são exibidos conforme registrados no ServiceNow.</div>';

    var coPage = 0;
    var coLimit = 50;

    document.getElementById('co-load').addEventListener('click', function () {
        coPage = 0;
        loadCorreios();
    });

    document.getElementById('co-prev').addEventListener('click', function () {
        if (coPage > 0) { coPage--; loadCorreios(); }
    });
    document.getElementById('co-next').addEventListener('click', function () {
        coPage++;
        loadCorreios();
    });

    function loadCorreios() {
        var queue = document.getElementById('co-queue').value.trim();
        if (!queue) { S.toast('Informe a fila', 'warning'); return; }

        document.getElementById('co-table-wrap').style.display = '';
        document.getElementById('co-table').innerHTML =
            '<div style="padding:1rem;color:var(--text-secondary)"><span class="spinner spinner-sm"></span> Buscando...</div>';

        S.api('/servicenow/chamados-correios?queue=' + encodeURIComponent(queue) +
              '&limit=' + coLimit + '&offset=' + (coPage * coLimit))
            .then(function (d) {
                var items = d.incidents || [];
                var total = d.total || 0;

                if (!items.length) {
                    document.getElementById('co-table').innerHTML =
                        '<div style="padding:1rem;color:var(--text-secondary)">Nenhum chamado com rastreio encontrado.</div>';
                    document.getElementById('co-pager').hidden = true;
                    return;
                }

                var displayVal = function (v) {
                    if (!v) return '';
                    if (typeof v === 'object') return v.display_value || v.value || '';
                    return v;
                };

                var html = '<table class="data-table"><thead><tr>' +
                    '<th>Número</th><th>Descrição</th><th>Código Rastreio</th>' +
                    '<th>Estado</th><th>Solicitante</th><th>Aberto em</th>' +
                    '</tr></thead><tbody>';

                for (var i = 0; i < items.length; i++) {
                    var inc = items[i];
                    html += '<tr>' +
                        '<td style="white-space:nowrap;font-weight:600">' + S.esc(displayVal(inc.number)) + '</td>' +
                        '<td style="max-width:250px;overflow:hidden;text-overflow:ellipsis">' +
                            S.esc(displayVal(inc.short_description)) + '</td>' +
                        '<td style="font-weight:600;color:#c06010">' +
                            S.esc(displayVal(inc.correlation_display)) + '</td>' +
                        '<td>' + S.esc(displayVal(inc.state)) + '</td>' +
                        '<td>' + S.esc(displayVal(inc.caller_id)) + '</td>' +
                        '<td style="white-space:nowrap;font-size:.85rem">' + S.esc(displayVal(inc.opened_at)) + '</td>' +
                        '</tr>';
                }
                html += '</tbody></table>';
                document.getElementById('co-table').innerHTML = html;

                var pager = document.getElementById('co-pager');
                pager.hidden = total <= coLimit && coPage === 0;
                var from = coPage * coLimit + 1;
                var to = Math.min(from + items.length - 1, total);
                document.getElementById('co-pager-info').textContent = from + '–' + to + ' de ' + total;
                document.getElementById('co-prev').disabled = coPage === 0;
                document.getElementById('co-next').disabled = to >= total;
            })
            .catch(function (e) {
                document.getElementById('co-table').innerHTML =
                    '<div style="padding:1rem;color:#dc2626">' + S.esc(e.message) + '</div>';
            });
    }
}


/* ================================================================
   SUB-TAB 4: Relatórios
   ================================================================ */

function _snRenderRelatorios(container, S) {
    container.innerHTML =
        '<h1 class="page-title">Relatórios ServiceNow</h1>' +
        '<p style="color:var(--text-secondary);margin-bottom:1.5rem">' +
            'Indicadores de desempenho da fila SPARE.</p>' +

        '<div style="display:flex;gap:1rem;align-items:end;margin-bottom:1.5rem">' +
            '<div style="flex:1"><label>Fila</label>' +
                '<input id="rel-queue" class="form-control" value="TI_N2_FLD_RNR_LOJAS_SPARE"></div>' +
            '<button class="btn btn-primary" id="rel-load">Carregar Relatórios</button>' +
            '<button class="btn" id="rel-refresh-tv" title="Atualizar cache do TV">Atualizar TV</button>' +
        '</div>' +

        '<div id="rel-loading" style="display:none;padding:2rem;text-align:center;color:var(--text-secondary)">' +
            '<span class="spinner spinner-sm"></span> Carregando dados do ServiceNow... Isso pode levar alguns segundos.</div>' +

        /* Tickets resolvidos */
        '<div id="rel-tickets" class="card mb-3" style="display:none">' +
            '<div class="card-header">Chamados Resolvidos (Ano Corrente)</div>' +
            '<div class="card-body">' +
                '<div id="rel-tickets-content"></div>' +
            '</div>' +
        '</div>' +

        /* SLA */
        '<div id="rel-sla" class="card mb-3" style="display:none">' +
            '<div class="card-header">Conformidade SLA</div>' +
            '<div class="card-body">' +
                '<div id="rel-sla-content"></div>' +
            '</div>' +
        '</div>' +

        /* TMA */
        '<div id="rel-tma" class="card mb-3" style="display:none">' +
            '<div class="card-header">TMA — Tempo Médio de Atendimento</div>' +
            '<div class="card-body">' +
                '<div id="rel-tma-content"></div>' +
            '</div>' +
        '</div>';

    document.getElementById('rel-load').addEventListener('click', function () { _relLoad(S); });
    document.getElementById('rel-refresh-tv').addEventListener('click', function () { _relRefreshTV(S); });
}

function _relLoad(S) {
    var queue = document.getElementById('rel-queue').value.trim();
    if (!queue) { S.toast('Informe a fila', 'warning'); return; }

    document.getElementById('rel-loading').style.display = '';
    document.getElementById('rel-tickets').style.display = 'none';
    document.getElementById('rel-sla').style.display = 'none';
    document.getElementById('rel-tma').style.display = 'none';

    var qp = 'queue=' + encodeURIComponent(queue);
    var done = 0;
    var total = 3;

    function checkDone() {
        done++;
        if (done >= total) document.getElementById('rel-loading').style.display = 'none';
    }

    // 1) Tickets
    S.api('/servicenow/relatorios/tickets?' + qp)
        .then(function (d) {
            document.getElementById('rel-tickets').style.display = '';
            var html = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1rem;text-align:center;margin-bottom:1.5rem">' +
                '<div class="stat-card accent-teal"><div class="stat-value">' + d.total + '</div>' +
                '<div class="stat-label">Chamados Resolvidos</div></div>' +
                '<div class="stat-card accent-orange"><div class="stat-value">' + d.year + '</div>' +
                '<div class="stat-label">Ano</div></div>' +
                '</div>';

            if (d.by_month && Object.keys(d.by_month).length) {
                html += '<h3 style="margin-bottom:.5rem">Por Período</h3>';
                html += '<table class="data-table"><thead><tr><th>Período</th><th>Quantidade</th></tr></thead><tbody>';
                Object.keys(d.by_month).forEach(function (m) {
                    html += '<tr><td>' + S.esc(m) + '</td><td style="font-weight:600">' + d.by_month[m] + '</td></tr>';
                });
                html += '</tbody></table>';
            }
            document.getElementById('rel-tickets-content').innerHTML = html;
            checkDone();
        })
        .catch(function (e) {
            document.getElementById('rel-tickets').style.display = '';
            document.getElementById('rel-tickets-content').innerHTML =
                '<div style="color:#dc2626">' + S.esc(e.message) + '</div>';
            checkDone();
        });

    // 2) SLA
    S.api('/servicenow/relatorios/sla?' + qp)
        .then(function (d) {
            document.getElementById('rel-sla').style.display = '';
            var pctColor = d.compliance_pct >= 90 ? '#16a34a' : d.compliance_pct >= 70 ? '#ca8a04' : '#dc2626';
            var html = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1rem;text-align:center;margin-bottom:1.5rem">' +
                '<div class="stat-card accent-green"><div class="stat-value" style="color:' + pctColor + '">' + d.compliance_pct + '%</div>' +
                '<div class="stat-label">Conformidade</div></div>' +
                '<div class="stat-card accent-teal"><div class="stat-value">' + d.total + '</div>' +
                '<div class="stat-label">Total SLAs</div></div>' +
                '<div class="stat-card accent-orange"><div class="stat-value">' + d.met + '</div>' +
                '<div class="stat-label">Dentro do SLA</div></div>' +
                '<div class="stat-card" style="border-top:4px solid #dc2626"><div class="stat-value" style="color:#dc2626">' + d.breached + '</div>' +
                '<div class="stat-label">Violados</div></div>' +
                '</div>';

            if (d.by_priority && Object.keys(d.by_priority).length) {
                html += '<h3 style="margin-bottom:.5rem">Por Prioridade</h3>';
                html += '<table class="data-table"><thead><tr><th>Prioridade</th><th>Total</th><th>Dentro</th><th>Violados</th><th>%</th></tr></thead><tbody>';
                Object.keys(d.by_priority).sort().forEach(function (p) {
                    var bp = d.by_priority[p];
                    var bpPct = bp.total > 0 ? Math.round((bp.met / bp.total) * 100) : 0;
                    html += '<tr><td style="font-weight:600">' + S.esc(p) + '</td>' +
                        '<td>' + bp.total + '</td><td style="color:#16a34a">' + bp.met + '</td>' +
                        '<td style="color:#dc2626">' + bp.breached + '</td>' +
                        '<td style="font-weight:600">' + bpPct + '%</td></tr>';
                });
                html += '</tbody></table>';
            }
            document.getElementById('rel-sla-content').innerHTML = html;
            checkDone();
        })
        .catch(function (e) {
            document.getElementById('rel-sla').style.display = '';
            document.getElementById('rel-sla-content').innerHTML =
                '<div style="color:#dc2626">' + S.esc(e.message) + '</div>';
            checkDone();
        });

    // 3) TMA
    S.api('/servicenow/relatorios/tma?' + qp)
        .then(function (d) {
            document.getElementById('rel-tma').style.display = '';
            var tma = d.tma || {};
            var html = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1.5rem;text-align:center">';

            if (tma.coletor) {
                html += '<div class="stat-card accent-teal">' +
                    '<div class="stat-value">' + tma.coletor.avg_hours + 'h</div>' +
                    '<div class="stat-label">TMA Coletores</div>' +
                    '<div style="color:var(--text-secondary);font-size:.8rem;margin-top:.3rem">' +
                        tma.coletor.count + ' chamados (' + tma.coletor.sample + ' amostras)</div></div>';
            }
            if (tma.sled) {
                html += '<div class="stat-card accent-orange">' +
                    '<div class="stat-value">' + tma.sled.avg_hours + 'h</div>' +
                    '<div class="stat-label">TMA SLEDs</div>' +
                    '<div style="color:var(--text-secondary);font-size:.8rem;margin-top:.3rem">' +
                        tma.sled.count + ' chamados (' + tma.sled.sample + ' amostras)</div></div>';
            }
            html += '</div>';
            document.getElementById('rel-tma-content').innerHTML = html;
            checkDone();
        })
        .catch(function (e) {
            document.getElementById('rel-tma').style.display = '';
            document.getElementById('rel-tma-content').innerHTML =
                '<div style="color:#dc2626">' + S.esc(e.message) + '</div>';
            checkDone();
        });
}

function _relRefreshTV(S) {
    S.toast('Atualizando cache do TV...', 'info');
    S.api('/servicenow/relatorios/refresh-tv', { method: 'POST' })
        .then(function () {
            S.toast('Cache do TV atualizado com sucesso!', 'success');
        })
        .catch(function (e) {
            S.toast('Erro: ' + e.message, 'error');
        });
}
