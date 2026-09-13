/* ================================================================
   Module: Reparos (Central de Reparos — all sub-tabs)
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.reparos = {

    render(container, sub) {
        var S = window.SPARE;
        var TAB_LIST = [
            ['novo',      'Registro de Reparo'],
            ['saldos',    'Tratativa de saldos'],
            ['dashboard', 'Dashboard Reparos']
        ];
        sub = sub || 'novo';
        S.tabs(TAB_LIST, sub, 'reparos');

        var handlers = {
            novo:      renderRepairNew,
            saldos:    renderBalances,
            dashboard: renderRepairDash
        };
        (handlers[sub] || renderRepairNew)(container, S);
    }

};

/* ── Registro de Reparo ─────────────────────────────────────────── */
function renderRepairNew(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Registro de Reparo</h1>' +
        '<div class="card mb-3">' +
            '<div class="card-body form-row-inline">' +
                '<input id="rp-q" class="form-control" placeholder="Ativo, etiqueta ou série">' +
                '<button id="rp-find" class="btn btn-primary">Buscar</button>' +
            '</div>' +
        '</div>' +
        '<div id="rp-info"></div>' +
        '<div id="rp-form" hidden>' +
            '<div class="card mb-3">' +
                '<div class="card-body form-grid cols-2">' +
                    '<div class="form-group"><label>Triagem (min)</label>' +
                        '<input id="rt1" type="number" value="0" class="form-control"></div>' +
                    '<div class="form-group"><label>Reparo (min)</label>' +
                        '<input id="rt2" type="number" value="0" class="form-control"></div>' +
                    '<div class="form-group"><label>Pesquisa (min)</label>' +
                        '<input id="rt3" type="number" value="0" class="form-control"></div>' +
                    '<div class="form-group"><label>Higienização (min)</label>' +
                        '<input id="rt4" type="number" value="0" class="form-control"></div>' +
                    '<div class="form-group"><label>Resultado</label>' +
                        '<select id="rr" class="form-control">' +
                            '<option>DESCARTE</option>' +
                            '<option>DIRETO LOJA</option>' +
                            '<option>EM TRIAGEM</option>' +
                            '<option>INTERNALIZAR</option>' +
                            '<option>TRATATIVA DE SALDO</option>' +
                        '</select>' +
                    '</div>' +
                    '<div class="form-group"><label>Técnico</label>' +
                        '<input id="rtech" class="form-control"></div>' +
                '</div>' +
                '<div class="card-footer">' +
                    '<button id="rsave" class="btn btn-primary">Salvar</button>' +
                '</div>' +
            '</div>' +
        '</div>';

    var asset = null;

    document.getElementById('rp-find').onclick = async function () {
        try {
            asset = await S.api('/consulta/single?identificador=' +
                encodeURIComponent(document.getElementById('rp-q').value));
            document.getElementById('rp-info').innerHTML =
                '<div class="asset-info-grid mb-3">' +
                    '<div>Empresa: ' + S.esc(asset.empresa) + '</div>' +
                    '<div>Imobilizado: ' + S.esc(asset.imobilizado) + '</div>' +
                    '<div>Etiqueta: ' + S.esc(asset.etiqueta) + '</div>' +
                    '<div>Série: ' + S.esc(asset.numero_serie) + '</div>' +
                    '<div>Categoria: ' + S.esc(asset.categoria) + '</div>' +
                    '<div>Modelo: ' + S.esc(asset.modelo) + '</div>' +
                    '<div>Ciclo: ' + S.esc(asset.ciclo) + '</div>' +
                '</div>';
            document.getElementById('rp-form').hidden = false;
        } catch (e) {
            S.toast(e.message, 'error');
        }
    };

    document.getElementById('rsave').onclick = async function () {
        if (!asset) return;
        var body = {
            imobilizado:      asset.imobilizado,
            etiqueta:         asset.etiqueta,
            numero_serie:     asset.numero_serie,
            triagem_min:      +document.getElementById('rt1').value,
            reparo_min:       +document.getElementById('rt2').value,
            pesquisa_min:     +document.getElementById('rt3').value,
            higienizacao_min: +document.getElementById('rt4').value,
            resultado:        document.getElementById('rr').value,
            tecnico:          document.getElementById('rtech').value
        };
        try {
            var d = await S.api('/reparos', { method: 'POST', body: body });
            S.toast('Reparo salvo. Saving ' + S.money(d.saving) + '.', 'success');
            // Reset form
            asset = null;
            document.getElementById('rp-info').innerHTML = '';
            document.getElementById('rp-form').hidden = true;
            document.getElementById('rp-q').value = '';
        } catch (e) {
            S.toast(e.message, 'error');
        }
    };
}

/* ── Tratativa de saldos ────────────────────────────────────────── */
function renderBalances(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Tratativa de saldos</h1>' +
        '<div class="alert alert-info">' +
            'Registre reparos com resultado TRATATIVA DE SALDO e acompanhe os itens pela Base de Recebimentos.' +
        '</div>';
}

/* ── Dashboard Reparos ──────────────────────────────────────────── */
function renderRepairDash(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Dashboard Reparos</h1>' +
        '<div class="card mb-3">' +
            '<div class="card-body form-row-inline">' +
                '<input id="rdi" type="date" class="form-control">' +
                '<input id="rdf" type="date" class="form-control">' +
                '<button id="rdr" class="btn btn-primary">Consultar</button>' +
            '</div>' +
        '</div>' +
        '<div id="rdo"></div>';

    // Default date range: first of month to today
    var t = new Date();
    var f = new Date(t.getFullYear(), t.getMonth(), 1);
    document.getElementById('rdi').value = f.toISOString().slice(0, 10);
    document.getElementById('rdf').value = t.toISOString().slice(0, 10);

    document.getElementById('rdr').onclick = async function () {
        try {
            var d = await S.api(
                '/reparos/dashboard?data_inicio=' +
                document.getElementById('rdi').value +
                '&data_fim=' + document.getElementById('rdf').value
            );
            var out = document.getElementById('rdo');
            out.innerHTML =
                '<div class="stats-grid mb-3">' +
                    '<div class="stat-card accent-teal">' +
                        '<div class="stat-value">' + d.total + '</div>' +
                        '<div class="stat-label">Reparos</div>' +
                    '</div>' +
                    '<div class="stat-card accent-orange">' +
                        '<div class="stat-value">' + d.total_min + '</div>' +
                        '<div class="stat-label">Minutos</div>' +
                    '</div>' +
                    '<div class="stat-card accent-gold">' +
                        '<div class="stat-value">' + d.total_horas.toFixed(1) + '</div>' +
                        '<div class="stat-label">Horas</div>' +
                    '</div>' +
                    '<div class="stat-card accent-green">' +
                        '<div class="stat-value">' + S.money(d.total_saving) + '</div>' +
                        '<div class="stat-label">Saving</div>' +
                    '</div>' +
                '</div>';
            out.appendChild(S.table([
                { key: 'data',        label: 'Data' },
                { key: 'imobilizado', label: 'Imobilizado' },
                { key: 'categoria',   label: 'Categoria' },
                { key: 'total_min',   label: 'Minutos' },
                { key: 'saving',      label: 'Saving', render: function (v) { return S.money(v); } },
                { key: 'resultado',   label: 'Resultado' }
            ], d.registros));
        } catch (e) {
            S.toast(e.message, 'error');
        }
    };

    // Auto-load
    document.getElementById('rdr').click();
}
