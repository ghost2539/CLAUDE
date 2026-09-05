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
            ['movimentacao', 'Movimentação Interna'],
            ['correios',  'Rastreio - Chamados'],
            ['relatorios','Relatórios']
        ];
        sub = sub || 'entrada';
        S.tabs(TAB_LIST, sub, 'servicenow');

        var handlers = {
            entrada:    _snRenderUpload,
            saida:      _snRenderSaida,
            movimentacao: _snRenderMovInterna,
            correios:   _snRenderCorreios,
            relatorios: _snRenderRelatorios
        };
        (handlers[sub] || _snRenderUpload)(container, S);
    }

};

/* ================================================================
   SN Login Bar — componente reutilizável de login ServiceNow
   ================================================================ */

var _snKeepAliveTimer = null;

/* A partir da unificação do login (Logon AD via ServiceNow), a sessão do SN
   já vem do próprio login do portal. Estas telas não pedem mais usuário/senha:
   mostram apenas o status da conexão e mantêm a sessão viva (keep-alive). */

function _snLoginBarHtml() {
    return '<div class="card mb-3" id="sn-login-bar">' +
        '<div class="card-body" style="display:flex;justify-content:space-between;align-items:center;gap:1rem">' +
            '<div style="font-size:.9rem">' +
                '<strong>ServiceNow</strong>' +
                '<span style="color:var(--text-secondary)"> — conectado automaticamente pelo login do portal (Logon AD).</span>' +
                '<div id="sn-bar-status" style="font-size:.82rem;color:var(--text-secondary);margin-top:.25rem"></div>' +
            '</div>' +
            '<span id="sn-session-badge" style="font-size:.8rem;padding:2px 10px;border-radius:10px;background:#dc262620;color:#dc2626;white-space:nowrap">Verificando…</span>' +
        '</div>' +
    '</div>';
}

function _snLoginBarBind(S, onSuccess) {
    var bar = document.getElementById('sn-login-bar');
    if (!bar) return;
    _snCheckSession(S, onSuccess);
}

function _snSetBadge(active) {
    var badge = document.getElementById('sn-session-badge');
    var st = document.getElementById('sn-bar-status');
    if (badge) {
        if (active) {
            badge.textContent = 'Conectado';
            badge.style.background = '#16a34a20';
            badge.style.color = '#16a34a';
        } else {
            badge.textContent = 'Sessão expirada';
            badge.style.background = '#dc262620';
            badge.style.color = '#dc2626';
        }
    }
    if (st) {
        st.innerHTML = active
            ? ''
            : '<span style="color:#dc2626">Sessão do ServiceNow expirou. Saia e entre novamente no portal (Logon AD) para renovar.</span>';
    }
}

function _snCheckSession(S, onSuccess) {
    S.api('/servicenow/session-status')
        .then(function (d) {
            _snSetBadge(d.active);
            if (d.active) { _snStartKeepAlive(S); if (onSuccess) onSuccess(); }
        })
        .catch(function () { _snSetBadge(false); });
}

function _snStartKeepAlive(S) {
    if (_snKeepAliveTimer) return;
    // Renova a sessão do ServiceNow a cada 5 minutos enquanto a tela estiver
    // aberta (o endpoint session-status atualiza os cookies no servidor).
    _snKeepAliveTimer = setInterval(function () {
        S.api('/servicenow/session-status')
            .then(function (d) { _snSetBadge(d.active); if (!d.active) _snStopKeepAlive(); })
            .catch(function () {});
    }, 5 * 60 * 1000);
}

function _snStopKeepAlive() {
    if (_snKeepAliveTimer) { clearInterval(_snKeepAliveTimer); _snKeepAliveTimer = null; }
}


/* ================================================================
   SUB-TAB 1: Entrada de estoque (upload de ativos — conteúdo original)
   ================================================================ */

var _snData = [];

function _snRenderUpload(container, S) {
    container.innerHTML =
        '<h1 class="page-title">Entrada de Estoque</h1>' +
        '<p style="color:var(--text-secondary);margin-bottom:1.5rem">' +
            'Upload de ativos para alm_hardware via a sua sessão do ServiceNow.</p>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Origem dos ativos</div>' +
            '<div class="card-body">' +
                '<label>Como deseja subir os ativos?</label>' +
                '<select id="sn-origem" class="form-control" style="max-width:420px">' +
                    '<option value="selecao">Selecionar ativos da base (marcar na lista)</option>' +
                    '<option value="status">Base de recebimento — por status</option>' +
                    '<option value="lista">Lista de ativos (consulta o EBS)</option>' +
                '</select>' +
            '</div>' +
        '</div>' +

        // ── Origem: seleção manual da base ──────────────────────────
        '<div class="card mb-3" id="sn-src-selecao">' +
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

        // ── Origem: base por status ─────────────────────────────────
        '<div class="card mb-3" id="sn-src-status" style="display:none">' +
            '<div class="card-header">Base de recebimento — por status</div>' +
            '<div class="card-body">' +
                '<label>Status da base a subir</label>' +
                '<select id="sn-status-base" class="form-control" style="max-width:420px">' +
                    '<option value="">Selecione…</option></select>' +
                '<p style="color:var(--text-secondary);font-size:.85rem;margin-top:.5rem">' +
                    'Todos os ativos da base com o status selecionado serão enviados.</p>' +
            '</div>' +
        '</div>' +

        // ── Origem: lista via EBS ───────────────────────────────────
        '<div class="card mb-3" id="sn-src-lista" style="display:none">' +
            '<div class="card-header">Lista de ativos (consulta o EBS)</div>' +
            '<div class="card-body">' +
                '<label>Ativos (um por linha ou separados por vírgula)</label>' +
                '<textarea id="sn-lista" class="form-control" rows="6" ' +
                    'placeholder="Ex.: 1234567&#10;7654321&#10;ou 1234567, 7654321"></textarea>' +
                '<p style="color:var(--text-secondary);font-size:.85rem;margin-top:.5rem">' +
                    'A conversão (modelo/categoria) usa o cadastro de modelos. Sem cadastro, ' +
                    'o ativo não sobe e o relatório orienta a regularizar.</p>' +
            '</div>' +
        '</div>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Configuração ServiceNow</div>' +
            '<div class="card-body">' +
                '<p style="color:var(--text-secondary);font-size:.85rem;margin-bottom:1rem">' +
                    'Moeda padrão BRL para todos os ativos. A depreciação é sempre calculada após a inclusão.</p>' +
                '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">' +
                    '<div><label>Stockroom</label>' +
                        '<input id="sn-stockroom" class="form-control" value="SPARE - CD324"></div>' +
                    '<div><label>Aisle and Space <span style="color:#dc2626">*</span></label>' +
                        '<input id="sn-aisle" class="form-control" placeholder="Obrigatório"></div>' +
                '</div>' +
            '</div>' +
        '</div>' +

        _snLoginBarHtml() +

        '<div class="card mb-3">' +
            '<div class="card-header">Enviar ao ServiceNow</div>' +
            '<div class="card-body">' +
                '<p style="color:var(--text-secondary);font-size:.85rem;margin-bottom:1rem">' +
                    'O envio usa a sua sessão do ServiceNow (login do portal). ' +
                    'Não é necessário informar usuário e senha.</p>' +
                '<button class="btn btn-primary" id="sn-upload" style="background:#c06010;border-color:#c06010">' +
                    'Enviar para ServiceNow</button>' +
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
        var selBase = document.getElementById('sn-status-base');
        (d.statuses || []).forEach(function (st) {
            sel.innerHTML += '<option value="' + S.esc(st) + '">' + S.esc(st) + '</option>';
            selBase.innerHTML += '<option value="' + S.esc(st) + '">' + S.esc(st) + '</option>';
        });
    }).catch(function () {});

    function aplicarOrigem() {
        var o = document.getElementById('sn-origem').value;
        document.getElementById('sn-src-selecao').style.display = (o === 'selecao') ? '' : 'none';
        document.getElementById('sn-src-status').style.display = (o === 'status') ? '' : 'none';
        document.getElementById('sn-src-lista').style.display = (o === 'lista') ? '' : 'none';
    }
    document.getElementById('sn-origem').addEventListener('change', aplicarOrigem);
    aplicarOrigem();

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
    document.getElementById('sn-upload').addEventListener('click', function () { _snStartUpload(S); });
    _snLoginBarBind(S);
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

async function _snStartUpload(S) {
    var origem = document.getElementById('sn-origem').value;
    var aisle = document.getElementById('sn-aisle').value.trim();
    if (!aisle) { S.toast('Informe o Aisle and Space (obrigatório).', 'warning'); return; }

    var body = {
        origem: origem,
        stockroom: document.getElementById('sn-stockroom').value,
        aisle_space: aisle,
    };
    var totalPrev = 0;

    if (origem === 'selecao') {
        var ids = _snGetSelectedIds();
        if (!ids.length) { S.toast('Selecione ao menos um ativo', 'warning'); return; }
        body.cycle_ids = ids;
        totalPrev = ids.length;
        if (!confirm('Enviar ' + ids.length + ' ativo(s) para o ServiceNow?')) return;
    } else if (origem === 'status') {
        var st = document.getElementById('sn-status-base').value;
        if (!st) { S.toast('Selecione o status da base.', 'warning'); return; }
        body.status = st;
        if (!confirm('Enviar todos os ativos da base com status "' + st + '"?')) return;
    } else { // lista
        var raw = document.getElementById('sn-lista').value || '';
        var lista = raw.split(/[\n,;]+/).map(function (x) { return x.trim(); })
            .filter(function (x) { return x; });
        if (!lista.length) { S.toast('Informe ao menos um ativo na lista.', 'warning'); return; }
        body.identificadores = lista;
        totalPrev = lista.length;
        if (!confirm('Consultar o EBS e enviar ' + lista.length + ' ativo(s)?')) return;
    }

    document.getElementById('sn-upload').disabled = true;
    document.getElementById('sn-progress-card').style.display = '';
    document.getElementById('sn-s-total').textContent = totalPrev || '…';

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
var _snLastResults = [];

// Relatório de erros ao final da subida (com motivos e download CSV).
function _snRenderErroReport(S, d) {
    var host = document.getElementById('sn-results');
    if (!host) return;
    var erros = (_snLastResults || []).filter(function (r) { return r.status === 'erro'; });
    var box = document.createElement('div');
    box.style.cssText = 'margin-top:1rem';
    if (!erros.length) {
        box.innerHTML = '<div class="alert alert-success">Concluído sem erros. ' +
            d.ok_count + ' ativo(s) inserido(s).</div>';
        host.appendChild(box);
        return;
    }
    var linhas = erros.map(function (r) {
        var motivos = (r.motivos && r.motivos.length) ? r.motivos.join(' | ') : (r.detail || '');
        return '<tr><td>' + r.idx + '</td><td>' + S.esc(r.serie || '') + '</td><td>' +
            S.esc(r.etiqueta || '') + '</td><td style="color:#dc2626">' + S.esc(motivos) + '</td></tr>';
    }).join('');
    box.innerHTML =
        '<div class="alert alert-danger" style="margin-bottom:.6rem">' +
            '<strong>' + erros.length + ' ativo(s) não foram enviados.</strong> ' +
            'Regularize os itens abaixo e reenvie.</div>' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.4rem">' +
            '<strong>Relatório de erros</strong>' +
            '<button class="btn btn-sm" id="sn-erro-csv">Baixar relatório (CSV)</button></div>' +
        '<div style="max-height:280px;overflow:auto"><table class="data-table"><thead><tr>' +
            '<th>#</th><th>Série</th><th>Etiqueta</th><th>Motivo(s)</th></tr></thead><tbody>' +
            linhas + '</tbody></table></div>';
    host.appendChild(box);

    document.getElementById('sn-erro-csv').addEventListener('click', function () {
        var rows = [['#', 'Serie', 'Etiqueta', 'Empresa', 'Motivos']];
        erros.forEach(function (r) {
            rows.push([r.idx, r.serie || '', r.etiqueta || '', r.empresa || '',
                (r.motivos && r.motivos.length) ? r.motivos.join(' | ') : (r.detail || '')]);
        });
        var csv = rows.map(function (row) {
            return row.map(function (c) {
                var s = String(c == null ? '' : c).replace(/"/g, '""');
                return '"' + s + '"';
            }).join(';');
        }).join('\r\n');
        var blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = 'relatorio_erros_entrada_' + new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-') + '.csv';
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    });
}

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
                _snLastResults = d.results;
                var html = '<table class="data-table"><thead><tr>' +
                    '<th>#</th><th>Série</th><th>Etiqueta</th><th>Empresa</th><th>Status</th><th>Depreciação</th><th>Detalhe / Motivo</th>' +
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
                    var detalhe = r.status === 'ok' ? (r.display || '') : (r.detail || '');
                    html += '<tr>' +
                        '<td>' + r.idx + '</td>' +
                        '<td>' + S.esc(r.serie || '') + '</td>' +
                        '<td>' + S.esc(r.etiqueta || '') + '</td>' +
                        '<td>' + S.esc(r.empresa || '') + '</td>' +
                        '<td>' + statusBadge + '</td>' +
                        '<td>' + depBadge + '</td>' +
                        '<td style="font-size:.8rem;color:' + (r.status === 'ok' ? 'inherit' : '#dc2626') + '">' +
                            S.esc(detalhe) + '</td>' +
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
                _snRenderErroReport(S, d);
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
            'Busca global e movimentação de ativos no ServiceNow.</p>' +

        _snLoginBarHtml() +

        '<div class="card mb-3">' +
            '<div class="card-header">Buscar Ativos (em lote)</div>' +
            '<div class="card-body">' +
                '<label>Identificadores (Asset Tag ou Número de Série)</label>' +
                '<textarea id="sa-ids" class="form-control" rows="5" ' +
                    'placeholder="Um por linha ou separados por vírgula. Sem limite de quantidade."></textarea>' +
                '<label style="display:flex;align-items:center;gap:.5rem;margin-top:.8rem;cursor:pointer">' +
                    '<input type="checkbox" id="sa-apply-all"> ' +
                    'Definir os dados de destino abaixo e aplicá-los a todos os ativos ao buscar' +
                '</label>' +
                '<div id="sa-defaults" style="display:none;margin-top:.8rem;padding:.8rem;' +
                    'background:var(--bg-root);border:1px solid var(--border-color);border-radius:6px">' +
                    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">' +
                        '<div><label>BU</label>' +
                            '<select id="sa-bu" class="form-control">' +
                                '<option value="">Selecione...</option>' +
                                '<option value="Renner Brasil">Renner Brasil</option>' +
                                '<option value="Youcom">Youcom</option>' +
                                '<option value="Camicado">Camicado</option>' +
                                '<option value="Ashua">Ashua</option>' +
                            '</select></div>' +
                        '<div><label>Código da Loja</label>' +
                            '<input id="sa-store-code" class="form-control" placeholder="Ex: 401"></div>' +
                    '</div>' +
                    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:.8rem">' +
                        '<div><label>Local destino (ServiceNow)</label>' +
                            '<select id="sa-location" class="form-control">' +
                                '<option value="">Informe BU e código da loja</option>' +
                            '</select></div>' +
                        '<div><label>Novo Status <span style="color:#dc2626">*</span></label>' +
                            _saStatusSelectHtml('sa-new-status', 'In transit', '') + '</div>' +
                    '</div>' +
                    '<div style="margin-top:.8rem">' +
                        '<label>Observações <span style="color:#dc2626">*</span> (nº do chamado ou motivo)</label>' +
                        '<input id="sa-notes" class="form-control" placeholder="Ex.: INC0012345 ou devolução de estoque"></div>' +
                '</div>' +
                '<div style="margin-top:1rem">' +
                    '<button class="btn btn-primary" id="sa-search">Buscar ativos</button></div>' +
                '<div id="sa-search-prog" style="margin-top:.5rem"></div>' +
            '</div>' +
        '</div>' +

        '<div id="sa-results" style="display:none">' +
            '<div class="card mb-3">' +
                '<div class="card-header">Ativos encontrados</div>' +
                '<div class="card-body">' +
                    '<div id="sa-found-info" style="margin-bottom:.6rem;color:var(--text-secondary)"></div>' +
                    '<div id="sa-table" style="max-height:460px;overflow:auto"></div>' +
                    '<div style="margin-top:1rem">' +
                        '<button class="btn btn-primary" id="sa-move-all">Confirmar saída dos marcados</button>' +
                    '</div>' +
                    '<div id="sa-move-prog" style="margin-top:.5rem"></div>' +
                    '<div id="sa-move-result" style="margin-top:.5rem"></div>' +
                '</div>' +
            '</div>' +
        '</div>' +
        '<datalist id="sa-loc-list"></datalist>';

    _snLoginBarBind(S);

    _saLocations = [];
    S.api('/servicenow/saida/locations').then(function (d) {
        _saLocations = d.locations || [];
        var dl = document.getElementById('sa-loc-list');
        if (dl) {
            dl.innerHTML = _saLocations.map(function (l) {
                return '<option value="' + S.esc(l.name) + '"></option>';
            }).join('');
        }
    }).catch(function () {});

    document.getElementById('sa-apply-all').addEventListener('change', function () {
        document.getElementById('sa-defaults').style.display = this.checked ? '' : 'none';
    });

    document.getElementById('sa-search').addEventListener('click', function () { _saSearch(S); });

    document.getElementById('sa-store-code').addEventListener('input', function () {
        var code = this.value.trim();
        var buSel = document.getElementById('sa-bu');
        if (code.length > 0) {
            if (code.charAt(0) === '4') buSel.value = 'Camicado';
            else if (code.charAt(0) === '3') buSel.value = 'Youcom';
        }
        _saFilterLocations(S);
    });
    document.getElementById('sa-bu').addEventListener('change', function () { _saFilterLocations(S); });
}

var _saSelectedAsset = null;
var _saLocations = [];

function _saFilterLocations(S) {
    var bu = document.getElementById('sa-bu').value;
    var code = document.getElementById('sa-store-code').value.trim();
    var sel = document.getElementById('sa-location');
    sel.innerHTML = '<option value="">Selecione...</option>';

    if (!code) return;

    var filtered = _saLocations.filter(function (loc) {
        return loc.codigo === code;
    });

    if (!filtered.length) {
        sel.innerHTML = '<option value="">Nenhum local encontrado para código ' + S.esc(code) + '</option>';
        return;
    }

    for (var i = 0; i < filtered.length; i++) {
        var opt = document.createElement('option');
        opt.value = filtered[i].name;
        opt.textContent = filtered[i].name;
        sel.appendChild(opt);
    }
    if (filtered.length === 1) sel.value = filtered[0].name;
}

function _saStatusSelectHtml(id, selected, cls) {
    var opts = [
        ['In transit', 'Em Trânsito'],
        ['In use', 'Em Uso'],
        ['In stock', 'Em Estoque'],
        ['On maintenance', 'Em Manutenção'],
        ['Retired', 'Desativado']
    ];
    var attrs = 'class="form-control' + (cls ? ' ' + cls : '') + '"' + (id ? ' id="' + id + '"' : '');
    var h = '<select ' + attrs + '>';
    for (var i = 0; i < opts.length; i++) {
        h += '<option value="' + opts[i][0] + '"' +
            (opts[i][0] === selected ? ' selected' : '') + '>' + opts[i][1] + '</option>';
    }
    return h + '</select>';
}

function _saSearch(S) {
    var raw = document.getElementById('sa-ids').value || '';
    var ids = raw.split(/[\n,;\t]+/).map(function (x) { return x.trim(); }).filter(Boolean);
    if (!ids.length) { S.toast('Informe ao menos um identificador.', 'warning'); return; }

    var prog = document.getElementById('sa-search-prog');
    prog.innerHTML = '<span class="spinner spinner-sm"></span> Buscando ' + ids.length + ' identificador(es)...';
    document.getElementById('sa-results').style.display = 'none';

    S.api('/servicenow/saida/search_lote', { method: 'POST', body: { identificadores: ids } })
        .then(function (d) {
            window._saAssets = d.assets || [];
            prog.innerHTML = '';
            document.getElementById('sa-results').style.display = '';
            _saRenderList(S, window._saAssets, d.nao_encontrados || [], d.solicitados || ids.length);
        })
        .catch(function (e) {
            prog.innerHTML = '<span style="color:#dc2626">' + S.esc(e.message) + '</span>';
        });
}

function _saRenderList(S, assets, naoEncontrados, solicitados) {
    var applyAll = document.getElementById('sa-apply-all').checked;
    var locEl = document.getElementById('sa-location');
    var stEl = document.getElementById('sa-new-status');
    var noEl = document.getElementById('sa-notes');
    var codeEl = document.getElementById('sa-store-code');
    var defLoc = applyAll && locEl ? locEl.value : '';
    var defStore = applyAll && codeEl ? codeEl.value.trim() : '';
    var defStatus = applyAll && stEl ? stEl.value : 'In transit';
    var defNotes = applyAll && noEl ? noEl.value.trim() : '';

    var info = document.getElementById('sa-found-info');
    var msg = assets.length + ' de ' + solicitados + ' ativo(s) encontrado(s).';
    if (naoEncontrados.length) {
        msg += ' <span style="color:#dc2626">Não encontrados (' + naoEncontrados.length + '): ' +
            S.esc(naoEncontrados.join(', ')) + '</span>';
    }
    info.innerHTML = msg;

    if (!assets.length) {
        document.getElementById('sa-table').innerHTML =
            '<div style="padding:1rem;color:var(--text-secondary)">Nenhum ativo encontrado.</div>';
        return;
    }

    var html = '<table class="data-table" style="min-width:1200px"><thead><tr>' +
        '<th><input type="checkbox" id="sa-check-all" checked></th>' +
        '<th>Asset Tag</th><th>Série</th><th>Nome</th><th>Local Atual</th><th>Status Atual</th>' +
        '<th>Nº Loja</th><th>Local Destino</th><th>Novo Status</th><th>Obs *</th><th>OK</th>' +
        '</tr></thead><tbody>';

    for (var i = 0; i < assets.length; i++) {
        var a = assets[i];
        html += '<tr data-idx="' + i + '">' +
            '<td><input type="checkbox" class="sa-row-inc" checked></td>' +
            '<td style="font-weight:600">' + S.esc(_saDisp(a.asset_tag)) + '</td>' +
            '<td>' + S.esc(_saDisp(a.serial_number)) + '</td>' +
            '<td>' + S.esc(_saDisp(a.display_name)) + '</td>' +
            '<td>' + S.esc(_saDisp(a.location)) + '</td>' +
            '<td>' + S.esc(_saDisp(a.install_status)) + '</td>' +
            '<td><input class="form-control sa-row-store" style="min-width:90px" ' +
                'placeholder="nº loja" value="' + S.esc(defStore) + '"></td>' +
            '<td class="sa-row-locname" data-loc="' + S.esc(defLoc) + '" ' +
                'style="min-width:180px;font-size:.85rem">' + S.esc(defLoc || '—') + '</td>' +
            '<td>' + _saStatusSelectHtml('', defStatus, 'sa-row-status') + '</td>' +
            '<td><input class="form-control sa-row-notes" style="min-width:150px" ' +
                'placeholder="chamado/motivo" value="' + S.esc(defNotes) + '"></td>' +
            '<td class="sa-row-result"></td>' +
            '</tr>';
    }
    html += '</tbody></table>';
    document.getElementById('sa-table').innerHTML = html;

    // Nº da loja → resolve o nome do Local destino pelo cadastro (locations_sn.json)
    document.querySelectorAll('#sa-table .sa-row-store').forEach(function (inp) {
        inp.addEventListener('input', function () {
            var cell = inp.closest('tr').querySelector('.sa-row-locname');
            var res = _saResolveStore(inp.value.trim());
            if (res.name) {
                cell.textContent = res.name + (res.multiple ? ' (+' + res.multiple + ')' : '');
                cell.setAttribute('data-loc', res.name);
                cell.style.color = '';
            } else {
                cell.textContent = inp.value.trim() ? 'Loja não encontrada' : '—';
                cell.setAttribute('data-loc', '');
                cell.style.color = inp.value.trim() ? '#dc2626' : '';
            }
        });
    });

    var chkAll = document.getElementById('sa-check-all');
    if (chkAll) chkAll.addEventListener('change', function () {
        var v = this.checked;
        document.querySelectorAll('#sa-table .sa-row-inc').forEach(function (c) { c.checked = v; });
    });

    document.getElementById('sa-move-all').onclick = function () { _saMoveAll(S); };
}

// Resolve um número de loja para o nome do local (cadastro locations_sn.json).
function _saResolveStore(code) {
    code = (code || '').trim();
    if (!code) return { name: '', multiple: 0 };
    var m = (_saLocations || []).filter(function (l) { return String(l.codigo) === code; });
    if (!m.length) return { name: '', multiple: 0 };
    return { name: m[0].name, multiple: m.length - 1 };
}

function _saDisp(v) {
    if (!v) return '';
    if (typeof v === 'object') return v.display_value || v.value || '';
    return v;
}

function _saMoveAll(S) {
    var rows = Array.prototype.slice.call(document.querySelectorAll('#sa-table tr[data-idx]'));
    var selected = rows.filter(function (r) {
        var c = r.querySelector('.sa-row-inc');
        return c && c.checked;
    });
    if (!selected.length) { S.toast('Marque ao menos um ativo.', 'warning'); return; }

    for (var i = 0; i < selected.length; i++) {
        var locName = selected[i].querySelector('.sa-row-locname').getAttribute('data-loc');
        if (!locName) {
            S.toast('Informe um nº de loja válido (Local destino) em todas as linhas marcadas.', 'warning');
            selected[i].querySelector('.sa-row-store').focus();
            return;
        }
        var obs = selected[i].querySelector('.sa-row-notes').value.trim();
        if (!obs) {
            S.toast('Observação é obrigatória (nº do chamado ou motivo) em todas as linhas.', 'warning');
            selected[i].querySelector('.sa-row-notes').focus();
            return;
        }
    }

    if (!confirm('Confirmar saída de ' + selected.length + ' ativo(s)?')) return;

    var btn = document.getElementById('sa-move-all');
    btn.disabled = true;
    var prog = document.getElementById('sa-move-prog');
    var result = document.getElementById('sa-move-result');
    result.innerHTML = '';
    var total = selected.length, done = 0, ok = 0, fail = 0;

    function setProg() {
        var pct = Math.round(done / total * 100);
        prog.innerHTML =
            '<div style="height:10px;background:#e5e7eb;border-radius:6px;overflow:hidden">' +
            '<div style="height:100%;width:' + pct + '%;background:#3b82f6;transition:width .2s"></div></div>' +
            '<div style="font-size:.8rem;color:var(--text-secondary);margin-top:2px">' +
            done + '/' + total + ' — ' + ok + ' ok, ' + fail + ' erro</div>';
    }
    setProg();

    var idx = 0;
    function next() {
        if (idx >= selected.length) {
            btn.disabled = false;
            result.innerHTML = '<span style="color:' + (fail ? '#d97706' : '#16a34a') + ';font-weight:600">' +
                'Concluído: ' + ok + ' movimentado(s), ' + fail + ' com erro.</span>';
            S.toast('Saída concluída: ' + ok + ' ok, ' + fail + ' erro.', fail ? 'warning' : 'success');
            return;
        }
        var row = selected[idx];
        var aidx = parseInt(row.getAttribute('data-idx'));
        var asset = window._saAssets[aidx];
        var sysId = asset.sys_id;
        if (typeof sysId === 'object') sysId = sysId.value || sysId.display_value;
        var cell = row.querySelector('.sa-row-result');
        cell.innerHTML = '<span class="spinner spinner-sm"></span>';

        var body = {
            sys_id: sysId,
            install_status: row.querySelector('.sa-row-status').value,
            location: row.querySelector('.sa-row-locname').getAttribute('data-loc') || '',
            notes: row.querySelector('.sa-row-notes').value.trim()
        };

        S.api('/servicenow/saida/move', { method: 'POST', body: body })
            .then(function () { cell.innerHTML = '<span style="color:#16a34a">✓</span>'; ok++; })
            .catch(function (e) {
                cell.innerHTML = '<span style="color:#dc2626" title="' + S.esc(e.message) + '">✗</span>';
                fail++;
            })
            .then(function () { done++; idx++; setProg(); next(); });
    }
    next();
}


/* ================================================================
   SUB-TAB 3: Movimentação Interna (entre estoques/espaços)
   ================================================================ */

var _miStockrooms = ['SPARE - CD324', 'SPARE-ADM15', 'SPARE-CD504'];

function _miStockroomSelectHtml(cls, val) {
    var h = '<select class="form-control' + (cls ? ' ' + cls : '') + '"><option value="">Selecione…</option>';
    _miStockrooms.forEach(function (s) {
        h += '<option value="' + s + '"' + (s === val ? ' selected' : '') + '>' + s + '</option>';
    });
    return h + '</select>';
}

function _snRenderMovInterna(container, S) {
    container.innerHTML =
        '<h1 class="page-title">Movimentação Interna</h1>' +
        '<p style="color:var(--text-secondary);margin-bottom:1.5rem">' +
            'Movimenta ativos entre estoques internos, espaços e corredores.</p>' +

        _snLoginBarHtml() +

        '<div class="card mb-3">' +
            '<div class="card-header">Buscar Ativos (em lote)</div>' +
            '<div class="card-body">' +
                '<label>Identificadores (Asset Tag ou Número de Série)</label>' +
                '<textarea id="mi-ids" class="form-control" rows="5" ' +
                    'placeholder="Um por linha ou separados por vírgula."></textarea>' +
                '<label style="display:flex;align-items:center;gap:.5rem;margin-top:.8rem;cursor:pointer">' +
                    '<input type="checkbox" id="mi-apply-all"> ' +
                    'Definir os dados abaixo e aplicá-los a todos os ativos ao buscar' +
                '</label>' +
                '<div id="mi-defaults" style="display:none;margin-top:.8rem;padding:.8rem;' +
                    'background:var(--bg-root);border:1px solid var(--border-color);border-radius:6px">' +
                    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">' +
                        '<div><label>Estoque destino <span style="color:#dc2626">*</span></label>' +
                            _miStockroomSelectHtml('mi-stockroom', 'SPARE - CD324') + '</div>' +
                        '<div><label>BU</label>' +
                            '<select id="mi-bu" class="form-control">' +
                                '<option value="">Selecione...</option>' +
                                '<option value="Renner Brasil">Renner Brasil</option>' +
                                '<option value="Youcom">Youcom</option>' +
                                '<option value="Camicado">Camicado</option>' +
                                '<option value="Ashua">Ashua</option>' +
                            '</select></div>' +
                    '</div>' +
                    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:.8rem">' +
                        '<div><label>Novo Status <span style="color:#dc2626">*</span></label>' +
                            _saStatusSelectHtml('mi-new-status', 'In stock', '') + '</div>' +
                        '<div><label>Aisle and Space <span style="color:#dc2626">*</span></label>' +
                            '<input id="mi-aisle" class="form-control" placeholder="Ex.: A-12"></div>' +
                    '</div>' +
                    '<div style="margin-top:.8rem">' +
                        '<label>Observações <span style="color:#dc2626">*</span></label>' +
                        '<input id="mi-notes" class="form-control" placeholder="Motivo da movimentação"></div>' +
                '</div>' +
                '<div style="margin-top:1rem">' +
                    '<button class="btn btn-primary" id="mi-search">Buscar ativos</button></div>' +
                '<div id="mi-search-prog" style="margin-top:.5rem"></div>' +
            '</div>' +
        '</div>' +

        '<div id="mi-results" style="display:none">' +
            '<div class="card mb-3">' +
                '<div class="card-header">Ativos encontrados</div>' +
                '<div class="card-body">' +
                    '<div id="mi-found-info" style="margin-bottom:.6rem;color:var(--text-secondary)"></div>' +
                    '<div id="mi-table" style="max-height:460px;overflow:auto"></div>' +
                    '<div style="margin-top:1rem">' +
                        '<button class="btn btn-primary" id="mi-move-all">Confirmar movimentação dos marcados</button>' +
                    '</div>' +
                    '<div id="mi-move-prog" style="margin-top:.5rem"></div>' +
                    '<div id="mi-move-result" style="margin-top:.5rem"></div>' +
                '</div>' +
            '</div>' +
        '</div>';

    _snLoginBarBind(S);

    document.getElementById('mi-apply-all').addEventListener('change', function () {
        document.getElementById('mi-defaults').style.display = this.checked ? '' : 'none';
    });
    document.getElementById('mi-search').addEventListener('click', function () { _miSearch(S); });
}

function _miSearch(S) {
    var raw = document.getElementById('mi-ids').value || '';
    var ids = raw.split(/[\n,;\t]+/).map(function (x) { return x.trim(); }).filter(Boolean);
    if (!ids.length) { S.toast('Informe ao menos um identificador.', 'warning'); return; }

    var prog = document.getElementById('mi-search-prog');
    prog.innerHTML = '<span class="spinner spinner-sm"></span> Buscando ' + ids.length + ' identificador(es)...';
    document.getElementById('mi-results').style.display = 'none';

    S.api('/servicenow/saida/search_lote', { method: 'POST', body: { identificadores: ids } })
        .then(function (d) {
            window._miAssets = d.assets || [];
            prog.innerHTML = '';
            document.getElementById('mi-results').style.display = '';
            _miRenderList(S, window._miAssets, d.nao_encontrados || [], d.solicitados || ids.length);
        })
        .catch(function (e) {
            prog.innerHTML = '<span style="color:#dc2626">' + S.esc(e.message) + '</span>';
        });
}

function _miRenderList(S, assets, naoEncontrados, solicitados) {
    var applyAll = document.getElementById('mi-apply-all').checked;
    var defStock = applyAll ? document.querySelector('.mi-stockroom').value : 'SPARE - CD324';
    var defStatus = applyAll ? document.getElementById('mi-new-status').value : 'In stock';
    var defAisle = applyAll ? document.getElementById('mi-aisle').value.trim() : '';
    var defNotes = applyAll ? document.getElementById('mi-notes').value.trim() : '';

    var info = document.getElementById('mi-found-info');
    var msg = assets.length + ' de ' + solicitados + ' ativo(s) encontrado(s).';
    if (naoEncontrados.length) {
        msg += ' <span style="color:#dc2626">Não encontrados (' + naoEncontrados.length + '): ' +
            S.esc(naoEncontrados.join(', ')) + '</span>';
    }
    info.innerHTML = msg;

    if (!assets.length) {
        document.getElementById('mi-table').innerHTML =
            '<div style="padding:1rem;color:var(--text-secondary)">Nenhum ativo encontrado.</div>';
        return;
    }

    var html = '<table class="data-table" style="min-width:1250px"><thead><tr>' +
        '<th><input type="checkbox" id="mi-check-all" checked></th>' +
        '<th>Asset Tag</th><th>Série</th><th>Nome</th><th>Local Atual</th><th>Status Atual</th>' +
        '<th>Estoque Destino *</th><th>Novo Status *</th><th>Aisle/Space *</th><th>Obs *</th><th>OK</th>' +
        '</tr></thead><tbody>';

    for (var i = 0; i < assets.length; i++) {
        var a = assets[i];
        html += '<tr data-idx="' + i + '">' +
            '<td><input type="checkbox" class="mi-row-inc" checked></td>' +
            '<td style="font-weight:600">' + S.esc(_saDisp(a.asset_tag)) + '</td>' +
            '<td>' + S.esc(_saDisp(a.serial_number)) + '</td>' +
            '<td>' + S.esc(_saDisp(a.display_name)) + '</td>' +
            '<td>' + S.esc(_saDisp(a.stockroom) || _saDisp(a.location)) + '</td>' +
            '<td>' + S.esc(_saDisp(a.install_status)) + '</td>' +
            '<td>' + _miStockroomSelectHtml('mi-row-stock', defStock) + '</td>' +
            '<td>' + _saStatusSelectHtml('', defStatus, 'mi-row-status') + '</td>' +
            '<td><input class="form-control mi-row-aisle" style="min-width:110px" value="' + S.esc(defAisle) + '"></td>' +
            '<td><input class="form-control mi-row-notes" style="min-width:150px" value="' + S.esc(defNotes) + '"></td>' +
            '<td class="mi-row-result"></td>' +
            '</tr>';
    }
    html += '</tbody></table>';
    document.getElementById('mi-table').innerHTML = html;

    var chkAll = document.getElementById('mi-check-all');
    if (chkAll) chkAll.addEventListener('change', function () {
        var v = this.checked;
        document.querySelectorAll('#mi-table .mi-row-inc').forEach(function (c) { c.checked = v; });
    });
    document.getElementById('mi-move-all').onclick = function () { _miMoveAll(S); };
}

function _miMoveAll(S) {
    var rows = Array.prototype.slice.call(document.querySelectorAll('#mi-table tr[data-idx]'));
    var selected = rows.filter(function (r) {
        var c = r.querySelector('.mi-row-inc'); return c && c.checked;
    });
    if (!selected.length) { S.toast('Marque ao menos um ativo.', 'warning'); return; }

    for (var i = 0; i < selected.length; i++) {
        var stk = selected[i].querySelector('.mi-row-stock').value;
        var ai = selected[i].querySelector('.mi-row-aisle').value.trim();
        var ob = selected[i].querySelector('.mi-row-notes').value.trim();
        var statAtual = selected[i].children[5].textContent.trim();
        if (!statAtual) { S.toast('Status atual ausente em uma linha marcada.', 'warning'); return; }
        if (!stk) { S.toast('Selecione o Estoque destino em todas as linhas.', 'warning'); return; }
        if (!ai) { S.toast('Aisle and Space é obrigatório em todas as linhas.', 'warning'); return; }
        if (!ob) { S.toast('Observações é obrigatória em todas as linhas.', 'warning'); return; }
    }

    if (!confirm('Confirmar movimentação de ' + selected.length + ' ativo(s)?')) return;

    var btn = document.getElementById('mi-move-all');
    btn.disabled = true;
    var prog = document.getElementById('mi-move-prog');
    var result = document.getElementById('mi-move-result');
    result.innerHTML = '';
    var total = selected.length, done = 0, ok = 0, fail = 0;

    function setProg() {
        var pct = Math.round(done / total * 100);
        prog.innerHTML =
            '<div style="height:10px;background:#e5e7eb;border-radius:6px;overflow:hidden">' +
            '<div style="height:100%;width:' + pct + '%;background:#3b82f6;transition:width .2s"></div></div>' +
            '<div style="font-size:.8rem;color:var(--text-secondary);margin-top:2px">' +
            done + '/' + total + ' — ' + ok + ' ok, ' + fail + ' erro</div>';
    }
    setProg();

    var idx = 0;
    function next() {
        if (idx >= selected.length) {
            btn.disabled = false;
            result.innerHTML = '<span style="color:' + (fail ? '#d97706' : '#16a34a') + ';font-weight:600">' +
                'Concluído: ' + ok + ' movimentado(s), ' + fail + ' com erro.</span>';
            S.toast('Movimentação concluída: ' + ok + ' ok, ' + fail + ' erro.', fail ? 'warning' : 'success');
            return;
        }
        var row = selected[idx];
        var aidx = parseInt(row.getAttribute('data-idx'));
        var asset = window._miAssets[aidx];
        var sysId = asset.sys_id;
        if (typeof sysId === 'object') sysId = sysId.value || sysId.display_value;
        var cell = row.querySelector('.mi-row-result');
        cell.innerHTML = '<span class="spinner spinner-sm"></span>';

        var body = {
            sys_id: sysId,
            stockroom: row.querySelector('.mi-row-stock').value,
            install_status: row.querySelector('.mi-row-status').value,
            aisle_space: row.querySelector('.mi-row-aisle').value.trim(),
            notes: row.querySelector('.mi-row-notes').value.trim()
        };

        S.api('/servicenow/mov-interna/move', { method: 'POST', body: body })
            .then(function () { cell.innerHTML = '<span style="color:#16a34a">✓</span>'; ok++; })
            .catch(function (e) {
                cell.innerHTML = '<span style="color:#dc2626" title="' + S.esc(e.message) + '">✗</span>';
                fail++;
            })
            .then(function () { done++; idx++; setProg(); next(); });
    }
    next();
}


/* ================================================================
   SUB-TAB 4: Chamados correios
   ================================================================ */

function _snRenderCorreios(container, S) {
    container.innerHTML =
        '<h1 class="page-title">Chamados Correios</h1>' +
        '<p style="color:var(--text-secondary);margin-bottom:1.5rem">' +
            'Incidentes com códigos de rastreio (campo correlation_display).</p>' +

        _snLoginBarHtml() +

        '<div class="card mb-3">' +
            '<div class="card-header">Filtros</div>' +
            '<div class="card-body">' +
                '<div style="display:flex;gap:1rem;align-items:end">' +
                    '<div style="flex:1"><label>Fila</label>' +
                        '<input id="co-queue" class="form-control" value="TI_N2_FLD_RNR_LOJAS_SPARE"></div>' +
                    '<button class="btn btn-primary" id="co-load">Carregar</button>' +
                    '<button class="btn btn-outline" id="co-debug" title="Mostra campos disponíveis nos incidentes para identificar onde está o código de rastreio">Diagnosticar campos</button>' +
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

        '<div id="co-tracking-panel" style="display:none">' +
            '<div class="card mb-3">' +
                '<div class="card-header" id="co-track-title">Rastreamento</div>' +
                '<div class="card-body" id="co-track-body"></div>' +
            '</div>' +
        '</div>';

    _snLoginBarBind(S);

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

    document.getElementById('co-debug').addEventListener('click', function () {
        var queue = document.getElementById('co-queue').value.trim();
        if (!queue) { S.toast('Informe a fila', 'warning'); return; }
        var panel = document.getElementById('co-tracking-panel');
        var body = document.getElementById('co-track-body');
        var title = document.getElementById('co-track-title');
        panel.style.display = '';
        title.textContent = 'Diagnóstico de campos';
        body.innerHTML = '<div style="padding:1rem;color:var(--text-secondary)"><span class="spinner spinner-sm"></span> Analisando...</div>';

        S.api('/servicenow/chamados-correios/debug?queue=' + encodeURIComponent(queue))
            .then(function (d) {
                var html = '<div style="margin-bottom:12px"><strong>Fila:</strong> ' + S.esc(d.queue) +
                    ' | <strong>Total incidentes:</strong> ' + d.total_incidents +
                    ' | <strong>Amostra:</strong> ' + d.sample_count + '</div>';

                // Campos de correlação
                html += '<h3 style="margin:16px 0 8px">Campos de correlação/rastreio</h3>';
                html += '<table class="data-table"><thead><tr><th>Campo</th><th>Incidente</th><th>Valor</th></tr></thead><tbody>';
                var cf = d.correlation_fields || {};
                for (var field in cf) {
                    var entries = cf[field];
                    for (var j = 0; j < entries.length; j++) {
                        html += '<tr><td style="font-weight:600">' + S.esc(field) + '</td>' +
                            '<td>' + S.esc(entries[j].incident) + '</td>' +
                            '<td style="color:' + (entries[j].value === '(vazio)' ? 'var(--text-secondary)' : '#c06010') + '">' +
                                S.esc(entries[j].value) + '</td></tr>';
                    }
                }
                html += '</tbody></table>';

                // Códigos detectados automaticamente
                var det = d.detected_tracking_codes || {};
                var detKeys = Object.keys(det);
                if (detKeys.length) {
                    html += '<h3 style="margin:16px 0 8px;color:#198754">Códigos de rastreio detectados automaticamente</h3>';
                    html += '<table class="data-table"><thead><tr><th>Campo</th><th>Incidente</th><th>Código</th></tr></thead><tbody>';
                    for (var k = 0; k < detKeys.length; k++) {
                        var items = det[detKeys[k]];
                        for (var m = 0; m < items.length; m++) {
                            html += '<tr><td style="font-weight:600;color:#198754">' + S.esc(detKeys[k]) + '</td>' +
                                '<td>' + S.esc(items[m].incident) + '</td>' +
                                '<td style="font-weight:600;color:#c06010">' + S.esc(items[m].value) + '</td></tr>';
                        }
                    }
                    html += '</tbody></table>';
                } else {
                    html += '<div style="margin-top:12px;color:var(--text-secondary)">' +
                        'Nenhum código de rastreio (formato XX000000000XX) detectado automaticamente nos últimos 5 incidentes.</div>';
                }

                // Todos os campos disponíveis
                html += '<h3 style="margin:16px 0 8px">Todos os campos disponíveis (' + (d.all_field_names || []).length + ')</h3>';
                html += '<div style="font-size:.8rem;color:var(--text-secondary);word-break:break-all">' +
                    (d.all_field_names || []).join(', ') + '</div>';

                body.innerHTML = html;
            })
            .catch(function (e) {
                _snShowError('co-track-body', e, S, function () {
                    document.getElementById('co-debug').click();
                });
            });
    });

    function displayVal(v) {
        if (!v) return '';
        if (typeof v === 'object') return v.display_value || v.value || '';
        return v;
    }

    function rastrear(codigo) {
        var panel = document.getElementById('co-tracking-panel');
        var title = document.getElementById('co-track-title');
        var body = document.getElementById('co-track-body');
        panel.style.display = '';
        title.textContent = 'Rastreamento: ' + codigo;
        body.innerHTML = '<div style="padding:1rem;color:var(--text-secondary)">' +
            '<span class="spinner spinner-sm"></span> Consultando Correios...</div>';
        panel.scrollIntoView({ behavior: 'smooth' });

        S.api('/servicenow/correios/rastrear/' + encodeURIComponent(codigo))
            .then(function (d) {
                if (!d.encontrado || !d.eventos || !d.eventos.length) {
                    body.innerHTML = '<div style="padding:1rem;color:var(--text-secondary)">' +
                        'Nenhum evento encontrado para ' + S.esc(codigo) + '.</div>';
                    return;
                }
                var html = '<div style="position:relative;padding-left:24px">';
                for (var i = 0; i < d.eventos.length; i++) {
                    var ev = d.eventos[i];
                    var isFirst = i === 0;
                    var dotColor = isFirst ? '#198754' : '#666';
                    var dtStr = '';
                    if (ev.data) {
                        try {
                            var dt = new Date(ev.data);
                            dtStr = dt.toLocaleDateString('pt-BR') + ' ' +
                                dt.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
                        } catch (_) { dtStr = ev.data; }
                    }
                    html += '<div style="position:relative;padding-bottom:20px;' +
                        (i < d.eventos.length - 1 ? 'border-left:2px solid #333;margin-left:6px;padding-left:24px' : 'margin-left:6px;padding-left:24px') + '">' +
                        '<div style="position:absolute;left:-7px;top:2px;width:14px;height:14px;border-radius:50%;background:' + dotColor + '"></div>' +
                        '<div style="font-weight:600;font-size:.95rem">' + S.esc(ev.descricao) + '</div>' +
                        (ev.detalhe ? '<div style="color:var(--text-secondary);font-size:.85rem">' + S.esc(ev.detalhe) + '</div>' : '') +
                        '<div style="color:var(--text-secondary);font-size:.8rem;margin-top:2px">' +
                            (ev.local ? S.esc(ev.local) + ' — ' : '') + dtStr +
                        '</div>' +
                    '</div>';
                }
                html += '</div>';

                // Info do objeto
                if (d.tipo || d.dt_prevista) {
                    html += '<div style="margin-top:16px;padding:12px;background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.2);border-radius:8px;font-size:.9rem">' +
                        '<div style="display:flex;gap:24px;flex-wrap:wrap">' +
                            (d.tipo ? '<div><span style="color:var(--text-secondary)">Tipo:</span> <strong>' + S.esc(d.tipo) + '</strong>' +
                                (d.tipo_nome ? ' — ' + S.esc(d.tipo_nome) : '') + '</div>' : '') +
                            (d.tipo_categoria ? '<div><span style="color:var(--text-secondary)">Categoria:</span> ' + S.esc(d.tipo_categoria) + '</div>' : '') +
                            (d.dt_prevista ? '<div><span style="color:var(--text-secondary)">Previsão de entrega:</span> ' + S.esc(d.dt_prevista) + '</div>' : '') +
                        '</div>' +
                    '</div>';
                }

                // Comprovante de entrega
                if (d.entrega && d.entrega.entregue) {
                    var ent = d.entrega;
                    var entDt = '';
                    if (ent.data) {
                        try {
                            var edt = new Date(ent.data);
                            entDt = edt.toLocaleDateString('pt-BR') + ' ' +
                                edt.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
                        } catch (_) { entDt = ent.data; }
                    }
                    html += '<div style="margin-top:20px;padding:16px;background:rgba(25,135,84,.1);border:1px solid rgba(25,135,84,.3);border-radius:8px">' +
                        '<div style="font-weight:700;font-size:1rem;color:#198754;margin-bottom:8px">' +
                            'COMPROVANTE DE ENTREGA</div>' +
                        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:.9rem">' +
                            '<div><span style="color:var(--text-secondary)">Status:</span> ' + S.esc(ent.descricao) + '</div>' +
                            '<div><span style="color:var(--text-secondary)">Data:</span> ' + entDt + '</div>' +
                            (ent.recebedor_nome ? '<div><span style="color:var(--text-secondary)">Recebedor:</span> <strong>' + S.esc(ent.recebedor_nome) + '</strong></div>' : '') +
                            (ent.recebedor_documento ? '<div><span style="color:var(--text-secondary)">Documento:</span> ' + S.esc(ent.recebedor_documento) + '</div>' : '') +
                            (ent.recebedor_celular ? '<div><span style="color:var(--text-secondary)">Celular:</span> ' + S.esc(ent.recebedor_celular) + '</div>' : '') +
                            (ent.recebedor_email ? '<div><span style="color:var(--text-secondary)">Email:</span> ' + S.esc(ent.recebedor_email) + '</div>' : '') +
                            (ent.local_entrega ? '<div><span style="color:var(--text-secondary)">Local:</span> ' + S.esc(ent.local_entrega) + '</div>' : '') +
                            (ent.destino ? '<div><span style="color:var(--text-secondary)">Destino:</span> ' + S.esc(ent.destino) + '</div>' : '') +
                            (ent.detalhe ? '<div style="grid-column:1/-1"><span style="color:var(--text-secondary)">Detalhe:</span> ' + S.esc(ent.detalhe) + '</div>' : '') +
                            (ent.recebedor_comentario ? '<div style="grid-column:1/-1"><span style="color:var(--text-secondary)">Obs:</span> ' + S.esc(ent.recebedor_comentario) + '</div>' : '') +
                        '</div>' +
                        '<div style="margin-top:12px">' +
                            '<button class="btn btn-sm btn-outline" id="co-ar-btn" style="color:#198754;border-color:#198754">' +
                                'Comprovante de entrega</button>' +
                            '<div id="co-ar-result" style="margin-top:8px"></div>' +
                        '</div>' +
                    '</div>';
                }

                body.innerHTML = html;

                // Bind AR button
                var arBtn = document.getElementById('co-ar-btn');
                if (arBtn) {
                    arBtn.onclick = function () {
                        arBtn.disabled = true;
                        arBtn.textContent = 'Buscando...';
                        var arResult = document.getElementById('co-ar-result');
                        S.api('/servicenow/correios/comprovante/' + encodeURIComponent(codigo))
                            .then(function (ar) {
                                if (!ar.encontrado) {
                                    arResult.innerHTML = '<div style="color:var(--text-secondary);font-size:.85rem">' +
                                        S.esc(ar.mensagem || 'Comprovante não disponível.') + '</div>';
                                    return;
                                }
                                var comp = ar.comprovante || {};
                                var arHtml = '<div style="font-size:.9rem">';
                                if (comp.imagem) {
                                    arHtml += '<div style="margin-bottom:8px"><img src="data:image/jpeg;base64,' + comp.imagem +
                                        '" style="max-width:100%;max-height:400px;border-radius:4px;border:1px solid #333" /></div>';
                                }
                                if (comp.assinatura) {
                                    arHtml += '<div style="margin-bottom:8px"><strong>Assinatura:</strong><br>' +
                                        '<img src="data:image/png;base64,' + comp.assinatura +
                                        '" style="max-width:300px;border:1px solid #333;border-radius:4px" /></div>';
                                }
                                if (comp.nome) {
                                    arHtml += '<div><span style="color:var(--text-secondary)">Nome:</span> ' + S.esc(comp.nome) + '</div>';
                                }
                                if (comp.documento) {
                                    arHtml += '<div><span style="color:var(--text-secondary)">Documento:</span> ' + S.esc(comp.documento) + '</div>';
                                }
                                if (comp.dataRecebimento) {
                                    arHtml += '<div><span style="color:var(--text-secondary)">Data recebimento:</span> ' + S.esc(comp.dataRecebimento) + '</div>';
                                }
                                if (comp.celular) {
                                    arHtml += '<div><span style="color:var(--text-secondary)">Celular:</span> ' + S.esc(comp.celular) + '</div>';
                                }
                                if (comp.email) {
                                    arHtml += '<div><span style="color:var(--text-secondary)">Email:</span> ' + S.esc(comp.email) + '</div>';
                                }
                                if (comp.local) {
                                    arHtml += '<div><span style="color:var(--text-secondary)">Local:</span> ' + S.esc(comp.local) + '</div>';
                                }
                                if (comp.descricao) {
                                    arHtml += '<div><span style="color:var(--text-secondary)">Evento:</span> ' + S.esc(comp.descricao) + '</div>';
                                }
                                if (comp.comentario) {
                                    arHtml += '<div><span style="color:var(--text-secondary)">Obs:</span> ' + S.esc(comp.comentario) + '</div>';
                                }
                                if (comp.imagens && comp.imagens.length) {
                                    for (var gi = 0; gi < comp.imagens.length; gi++) {
                                        var gimg = comp.imagens[gi];
                                        var src = (typeof gimg === 'string')
                                            ? 'data:image/jpeg;base64,' + gimg
                                            : (gimg.conteudo ? 'data:' + (gimg.tipo || 'image/jpeg') + ';base64,' + gimg.conteudo : '');
                                        if (src) {
                                            arHtml += '<div style="margin:8px 0"><img src="' + src +
                                                '" style="max-width:100%;max-height:400px;border-radius:4px;border:1px solid #333" /></div>';
                                        }
                                    }
                                }
                                // Handle nested objects/arrays from v3
                                if (comp.objetos) {
                                    var objs = Array.isArray(comp.objetos) ? comp.objetos : [comp.objetos];
                                    for (var oi = 0; oi < objs.length; oi++) {
                                        var o = objs[oi];
                                        if (o.imagens) {
                                            var imgs = Array.isArray(o.imagens) ? o.imagens : [o.imagens];
                                            for (var ii = 0; ii < imgs.length; ii++) {
                                                var img = imgs[ii];
                                                if (typeof img === 'string') {
                                                    arHtml += '<div style="margin:8px 0"><img src="data:image/jpeg;base64,' + img +
                                                        '" style="max-width:100%;max-height:400px;border-radius:4px;border:1px solid #333" /></div>';
                                                } else if (img.conteudo) {
                                                    arHtml += '<div style="margin:8px 0"><img src="data:' + (img.tipo || 'image/jpeg') + ';base64,' + img.conteudo +
                                                        '" style="max-width:100%;max-height:400px;border-radius:4px;border:1px solid #333" /></div>';
                                                }
                                            }
                                        }
                                        if (o.recebedor) {
                                            var rec = o.recebedor;
                                            if (rec.nome) arHtml += '<div><span style="color:var(--text-secondary)">Recebedor:</span> ' + S.esc(rec.nome) + '</div>';
                                            if (rec.documento) arHtml += '<div><span style="color:var(--text-secondary)">Documento:</span> ' + S.esc(rec.documento) + '</div>';
                                        }
                                    }
                                }
                                if (!arHtml.replace(/<div style="font-size:\.9rem">/, '')) {
                                    arHtml += '<div style="color:var(--text-secondary)">Dados retornados: ' +
                                        S.esc(JSON.stringify(comp).substring(0, 500)) + '</div>';
                                }
                                arHtml += '</div>';
                                arResult.innerHTML = arHtml;
                            })
                            .catch(function (e) {
                                arResult.innerHTML = '<div style="color:#dc2626;font-size:.85rem">' + S.esc(e.message) + '</div>';
                            })
                            .finally(function () {
                                arBtn.disabled = false;
                                arBtn.textContent = 'Comprovante de entrega';
                            });
                    };
                }
            })
            .catch(function (e) {
                body.innerHTML = '<div style="padding:1rem;color:#dc2626">' + S.esc(e.message) + '</div>';
            });
    }

    function loadCorreios() {
        var queue = document.getElementById('co-queue').value.trim();
        if (!queue) { S.toast('Informe a fila', 'warning'); return; }

        document.getElementById('co-table-wrap').style.display = '';
        document.getElementById('co-tracking-panel').style.display = 'none';
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

                var html = '<table class="data-table"><thead><tr>' +
                    '<th>Número</th><th>Descrição</th><th>Código Rastreio</th>' +
                    '<th>Estado</th><th>Entrega</th><th>Solicitante</th><th>Aberto em</th>' +
                    '</tr></thead><tbody>';

                var autoList = [];
                for (var i = 0; i < items.length; i++) {
                    var inc = items[i];
                    var rawCode = inc._tracking_code || displayVal(inc.correlation_display) || displayVal(inc.correlation_id) || '';
                    var codeMatch = String(rawCode).toUpperCase().match(/[A-Z]{2}\d{9}[A-Z]{2}/);
                    var hasValidCode = !!codeMatch;
                    var trackCode = codeMatch ? codeMatch[0] : String(rawCode).toUpperCase();
                    var sysId = displayVal(inc.sys_id);
                    var entId = 'co-ent-' + (sysId || i);
                    if (hasValidCode && sysId) {
                        autoList.push({ code: trackCode, sysId: sysId, number: displayVal(inc.number), entId: entId });
                    }
                    html += '<tr>' +
                        '<td style="white-space:nowrap;font-weight:600">' + S.esc(displayVal(inc.number)) + '</td>' +
                        '<td style="max-width:250px;overflow:hidden;text-overflow:ellipsis">' +
                            S.esc(displayVal(inc.short_description)) + '</td>' +
                        '<td>' +
                            (hasValidCode
                                ? '<button class="btn btn-sm btn-outline co-track-btn" ' +
                                    'data-code="' + S.esc(trackCode) + '" ' +
                                    'style="font-weight:600;color:#c06010">' +
                                    S.esc(trackCode) + '</button>'
                                : '<span style="color:var(--text-secondary);font-size:.85rem" title="' +
                                    S.esc(trackCode) + '">' +
                                    (trackCode ? S.esc(trackCode.substring(0, 20)) + '...' : '(sem código)') +
                                    '</span>') +
                        '</td>' +
                        '<td>' + S.esc(displayVal(inc.state)) + '</td>' +
                        '<td id="' + entId + '" style="white-space:nowrap">' +
                            (hasValidCode
                                ? '<span style="color:var(--text-secondary);font-size:.82rem">Verificando…</span>'
                                : '<span style="color:var(--text-secondary)">—</span>') +
                        '</td>' +
                        '<td>' + S.esc(displayVal(inc.caller_id)) + '</td>' +
                        '<td style="white-space:nowrap;font-size:.85rem">' + S.esc(displayVal(inc.opened_at)) + '</td>' +
                        '</tr>';
                }
                html += '</tbody></table>';
                document.getElementById('co-table').innerHTML = html;

                document.querySelectorAll('.co-track-btn').forEach(function (btn) {
                    btn.addEventListener('click', function () {
                        rastrear(btn.getAttribute('data-code'));
                    });
                });

                _coAutoRastrear(autoList, S, function () { loadCorreios(); });

                var pager = document.getElementById('co-pager');
                pager.hidden = total <= coLimit && coPage === 0;
                var from = coPage * coLimit + 1;
                var to = Math.min(from + items.length - 1, total);
                document.getElementById('co-pager-info').textContent = from + '–' + to + ' de ' + total;
                document.getElementById('co-prev').disabled = coPage === 0;
                document.getElementById('co-next').disabled = to >= total;
            })
            .catch(function (e) {
                _snShowError('co-table', e, S, function () { loadCorreios(); });
            });
    }
}

/* Rastreia automaticamente cada chamado da lista e mostra o status de
   entrega na coluna Entrega: "Encerrar" (entregue) ou "Aguardando Entrega". */
async function _coAutoRastrear(lista, S, reloadFn) {
    for (var i = 0; i < lista.length; i++) {
        var item = lista[i];
        var cell = document.getElementById(item.entId);
        if (!cell) continue;
        try {
            var d = await S.api('/servicenow/correios/rastrear/' + encodeURIComponent(item.code));
            var entregue = d && d.encontrado && d.entrega && d.entrega.entregue;
            if (entregue) {
                _coCellEncerrar(cell, item, S, reloadFn);
            } else if (d && !d.encontrado) {
                cell.innerHTML = '<span style="color:#6b7280;font-size:.82rem">Não encontrado</span>';
            } else {
                cell.innerHTML = '<span style="color:#2563eb;font-size:.82rem;font-weight:600">Aguardando Entrega</span>';
            }
        } catch (err) {
            cell.innerHTML = '<span style="color:#dc2626;font-size:.8rem" title="' +
                S.esc(err.message || '') + '">Erro no rastreio</span>';
        }
    }
}

function _coCellEncerrar(cell, item, S, reloadFn) {
    cell.innerHTML = '';
    var wrap = S.el('div', { style: 'display:flex;align-items:center;gap:6px' });
    wrap.appendChild(S.el('span', {
        style: 'color:#16a34a;font-weight:600;font-size:.82rem', textContent: 'Entregue'
    }));
    var btn = S.el('button', { className: 'btn btn-sm btn-primary', textContent: 'Encerrar' });
    btn.onclick = function () {
        if (!confirm('Encerrar o chamado ' + item.number + ' no ServiceNow?\n\n' +
            'Muda o estado para Resolvido e preenche a closure information. ' +
            'Ação real, não pode ser desfeita pelo portal.')) return;
        btn.disabled = true; btn.textContent = 'Encerrando…';
        S.api('/servicenow/encerramento/executar', {
            method: 'POST', body: { sys_id: item.sysId, confirmar: true }
        })
            .then(function (r) {
                S.toast('Chamado ' + (r.encerrado || item.number) + ' encerrado.', 'success');
                cell.innerHTML = '<span style="color:#16a34a;font-weight:600">Encerrado ✓</span>';
            })
            .catch(function (err) {
                S.toast(err.message || 'Falha ao encerrar.', 'error');
                btn.disabled = false; btn.textContent = 'Encerrar';
            });
    };
    wrap.appendChild(btn);
    cell.appendChild(wrap);
}

function _snShowError(targetId, err, S, retryFn) {
    var el = document.getElementById(targetId);
    var isSn = err.message && err.message.indexOf('ServiceNow') !== -1;
    var html = '<div style="padding:1rem;color:#dc2626">' + S.esc(err.message) + '</div>';
    if (isSn) {
        html += '<div style="padding:0 1rem .5rem"><button class="btn btn-sm btn-primary" id="' + targetId + '-relogin">Reconectar ServiceNow</button></div>';
    }
    el.innerHTML = html;
    if (isSn) {
        document.getElementById(targetId + '-relogin').addEventListener('click', function () {
            S.snReloginModal().then(function (ok) {
                if (ok && retryFn) retryFn();
            });
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

        _snLoginBarHtml() +

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

    _snLoginBarBind(S);

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
            _snShowError('rel-tickets-content', e, S, function () { _relLoad(S); });
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
            _snShowError('rel-sla-content', e, S, function () { _relLoad(S); });
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
            _snShowError('rel-tma-content', e, S, function () { _relLoad(S); });
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
