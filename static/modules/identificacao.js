/* ================================================================
   Module: Identificação
   Sub-tabs: Gerar Lote | Identificação A4 | Zebra Livre | Impressoras
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.identificacao = {
    render: function (container, sub) {
        var S = window.SPARE;
        var TABS = [
            ['lote',        'Gerar Lote'],
            ['a4',          'Identificação A4'],
            ['livre',       'Impressão Zebra Livre'],
            ['impressoras', 'Impressoras']
        ];
        sub = sub || 'lote';
        S.tabs(TABS, sub, 'identificacao');
        var h = { lote: _renderLote, a4: _renderA4, livre: _renderLivre, impressoras: _renderImpressoras };
        (h[sub] || _renderLote)(container, S);
    }
};

/* ================================================================
   1. GERAR LOTE
   ================================================================ */
var _loteAssets = [];

function _renderLote(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Gerar Lote</h1>' +
        '<p style="color:var(--text-secondary);margin-bottom:1.5rem">Operação integrada ao Recebimento.</p>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Gerar Lote</div>' +
            '<div class="card-body">' +
                '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem">' +
                    '<div><label>Tipo</label>' +
                        '<select id="lt-tipo" class="form-control">' +
                            '<option value="triagem">Triagem</option>' +
                            '<option value="venda">Venda</option>' +
                        '</select></div>' +
                    '<div><label>Próxima Caixa</label>' +
                        '<input id="lt-next" class="form-control" readonly></div>' +
                    '<div id="lt-lote-wrap" style="display:none"><label>Lote da Venda</label>' +
                        '<input id="lt-lote-venda" class="form-control" placeholder="Lote de venda"></div>' +
                '</div>' +
                '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:1rem">' +
                    '<div><label>Quantidade</label>' +
                        '<input id="lt-qty" class="form-control" type="number" value="1" min="1"></div>' +
                    '<div><label>Equipamento</label>' +
                        '<input id="lt-equip" class="form-control" placeholder="Ex: MONITOR"></div>' +
                '</div>' +
            '</div>' +
        '</div>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Bipagem de ativos</div>' +
            '<div class="card-body">' +
                '<div style="display:flex;gap:.8rem;margin-bottom:1rem">' +
                    '<input id="lt-scan" class="form-control" placeholder="Bipe o ativo e pressione Enter" style="flex:1">' +
                '</div>' +
                '<div id="lt-asset-list"></div>' +
                '<div style="margin-top:.5rem;color:var(--text-secondary)">' +
                    'Ativos incluídos: <strong id="lt-count">0</strong>' +
                '</div>' +
            '</div>' +
        '</div>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Impressão</div>' +
            '<div class="card-body">' +
                '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem">' +
                    '<div><label>Zebra salva</label>' +
                        '<select id="lt-printer" class="form-control"><option value="">Carregando...</option></select></div>' +
                    '<div><label>IP da Zebra</label>' +
                        '<input id="lt-ip" class="form-control" placeholder="10.x.x.x" readonly></div>' +
                    '<div><label>Porta</label>' +
                        '<input id="lt-port" class="form-control" value="9100" readonly></div>' +
                '</div>' +
                '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:1rem">' +
                    '<div><label>Nome para salvar</label>' +
                        '<input id="lt-save-name" class="form-control" placeholder="Zebra Triagem"></div>' +
                    '<div><label>Cópias</label>' +
                        '<input id="lt-copies" class="form-control" type="number" value="1" min="1" max="50"></div>' +
                '</div>' +
                '<div style="margin-top:1.5rem;display:flex;gap:.8rem;flex-wrap:wrap">' +
                    '<button class="btn" id="lt-save-ip">Salvar IP</button>' +
                    '<button class="btn" id="lt-test">Testar conexão</button>' +
                    '<button class="btn btn-dark" id="lt-preview">Baixar prévia ZPL</button>' +
                    '<button class="btn btn-primary" id="lt-generate" style="background:#c06010;border-color:#c06010">Gerar Caixa e imprimir</button>' +
                '</div>' +
                '<pre id="lt-zpl-out" style="margin-top:1rem;display:none;max-height:250px;overflow:auto;' +
                    'background:var(--bg-input);padding:1rem;border-radius:8px;font-size:.85rem"></pre>' +
            '</div>' +
        '</div>';

    _loteAssets = [];
    _loadZebraPrinters(S, 'lt-printer', 'lt-ip', 'lt-port');
    _loadNextSeq(S);

    var tipo = document.getElementById('lt-tipo');
    tipo.addEventListener('change', function () {
        document.getElementById('lt-lote-wrap').style.display =
            tipo.value === 'venda' ? '' : 'none';
        _loadNextSeq(S);
    });

    document.getElementById('lt-scan').addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            var val = this.value.trim();
            if (val) _lookupAsset(S, val);
            this.value = '';
        }
    });

    document.getElementById('lt-save-ip').addEventListener('click', function () {
        _saveNewPrinter(S, 'lt-save-name', 'lt-ip', 'lt-port', 'ZEBRA', function () {
            _loadZebraPrinters(S, 'lt-printer', 'lt-ip', 'lt-port');
        });
    });

    document.getElementById('lt-test').addEventListener('click', function () {
        var pid = document.getElementById('lt-printer').value;
        if (!pid) { S.toast('Selecione uma impressora', 'warning'); return; }
        S.api('/identificacao/test-printer', { method: 'POST', body: { printer_id: parseInt(pid) } })
            .then(function (d) { S.toast(d.message, 'success'); })
            .catch(function (e) { S.toast(e.message, 'error'); });
    });

    document.getElementById('lt-preview').addEventListener('click', function () { _lotePreview(S); });
    document.getElementById('lt-generate').addEventListener('click', function () { _loteGenerate(S); });
}

function _loadNextSeq(S) {
    var tipo = document.getElementById('lt-tipo').value;
    S.api('/identificacao/next-sequence/' + tipo).then(function (d) {
        document.getElementById('lt-next').value = d.display;
    }).catch(function () {});
}

function _lookupAsset(S, ident) {
    S.api('/identificacao/lookup-asset', { method: 'POST', body: { identificador: ident } })
        .then(function (d) {
            if (_loteAssets.find(function (a) { return a.asset_id === d.asset_id; })) {
                S.toast('Ativo já incluído', 'warning');
                return;
            }
            _loteAssets.push(d);
            _renderAssetList(S);
            S.toast(d.serie || d.etiqueta || d.ativo, 'success');
        })
        .catch(function (e) { S.toast(e.message, 'error'); });
}

function _renderAssetList(S) {
    var el = document.getElementById('lt-asset-list');
    document.getElementById('lt-count').textContent = _loteAssets.length;
    if (!_loteAssets.length) {
        el.innerHTML = '<div style="padding:.5rem;color:var(--text-secondary)">Nenhum ativo incluído</div>';
        return;
    }
    var html = '<table class="data-table"><thead><tr>' +
        '<th>Empresa</th><th>Série</th><th>Etiqueta</th><th>Modelo</th><th>Status</th><th></th>' +
        '</tr></thead><tbody>';
    for (var i = 0; i < _loteAssets.length; i++) {
        var a = _loteAssets[i];
        html += '<tr>' +
            '<td>' + S.esc(a.empresa) + '</td>' +
            '<td>' + S.esc(a.serie) + '</td>' +
            '<td>' + S.esc(a.etiqueta) + '</td>' +
            '<td>' + S.esc(a.modelo) + '</td>' +
            '<td>' + S.badge(a.cycle_status || '—') + '</td>' +
            '<td><button class="btn btn-sm btn-danger lt-rm" data-idx="' + i + '">✕</button></td>' +
        '</tr>';
    }
    html += '</tbody></table>';
    el.innerHTML = html;

    var btns = el.querySelectorAll('.lt-rm');
    for (var j = 0; j < btns.length; j++) {
        btns[j].addEventListener('click', function () {
            _loteAssets.splice(parseInt(this.dataset.idx), 1);
            _renderAssetList(S);
        });
    }
}

function _getLoteBody() {
    return {
        tipo: document.getElementById('lt-tipo').value,
        equipamento: document.getElementById('lt-equip').value,
        quantidade: parseInt(document.getElementById('lt-qty').value) || 1,
        lote_venda: document.getElementById('lt-lote-venda') ?
            document.getElementById('lt-lote-venda').value : '',
        asset_ids: _loteAssets.map(function (a) { return a.asset_id; }),
        printer_id: parseInt(document.getElementById('lt-printer').value) || null,
        copies: parseInt(document.getElementById('lt-copies').value) || 1,
    };
}

function _lotePreview(S) {
    var body = _getLoteBody();
    S.api('/identificacao/preview-lote', { method: 'POST', body: body })
        .then(function (d) {
            var pre = document.getElementById('lt-zpl-out');
            pre.style.display = 'block';
            pre.textContent = d.zpl;
            S.toast('Prévia: ' + d.caixa_preview + ' (não consumiu sequência)', 'info');
        })
        .catch(function (e) { S.toast(e.message, 'error'); });
}

function _loteGenerate(S) {
    var body = _getLoteBody();
    if (!body.asset_ids.length) {
        S.toast('Inclua ao menos um ativo', 'warning');
        return;
    }
    if (body.tipo === 'venda' && !body.lote_venda) {
        S.toast('Lote da Venda é obrigatório', 'warning');
        return;
    }
    if (!body.printer_id) {
        S.toast('Selecione uma impressora', 'warning');
        return;
    }
    S.api('/identificacao/gerar-lote', { method: 'POST', body: body })
        .then(function (d) {
            S.toast('Caixa ' + d.caixa + ' gerada — ' + d.ativos_atualizados +
                ' ativo(s) → ' + d.status_aplicado, 'success');
            var pre = document.getElementById('lt-zpl-out');
            pre.style.display = 'block';
            pre.textContent = d.zpl;
            _loteAssets = [];
            _renderAssetList(S);
            _loadNextSeq(S);
        })
        .catch(function (e) { S.toast(e.message, 'error'); });
}


/* ================================================================
   2. IDENTIFICAÇÃO A4
   ================================================================ */
function _renderA4(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Identificação A4</h1>' +
        '<p style="color:var(--text-secondary);margin-bottom:1.5rem">Operação integrada ao Recebimento.</p>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Identificação A4</div>' +
            '<div class="card-body">' +
                '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem">' +
                    '<div><label>Texto 1</label>' +
                        '<input id="a4-t1" class="form-control" placeholder="TEXTO1"></div>' +
                    '<div><label>Texto 2</label>' +
                        '<input id="a4-t2" class="form-control" placeholder="TEXTO2"></div>' +
                    '<div><label>Texto 3</label>' +
                        '<input id="a4-t3" class="form-control" placeholder="TEXTO3"></div>' +
                '</div>' +
                '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem;margin-top:1rem">' +
                    '<div><label>Cópias</label>' +
                        '<input id="a4-copies" class="form-control" type="number" value="1" min="1" max="50"></div>' +
                    '<div><label>Lexmark salva</label>' +
                        '<select id="a4-printer" class="form-control"><option value="">Carregando...</option></select></div>' +
                    '<div><label>IP da Lexmark</label>' +
                        '<input id="a4-ip" class="form-control" placeholder="10.x.x.x" readonly></div>' +
                '</div>' +
                '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:1rem">' +
                    '<div><label>Nome para salvar</label>' +
                        '<input id="a4-save-name" class="form-control" placeholder="Lexmark A4"></div>' +
                    '<div><label>Porta</label>' +
                        '<input id="a4-port" class="form-control" value="9100" readonly></div>' +
                '</div>' +
                '<div style="margin-top:1.5rem;display:flex;gap:.8rem;flex-wrap:wrap">' +
                    '<button class="btn" id="a4-save-ip">Salvar IP</button>' +
                    '<button class="btn btn-dark" id="a4-pdf">Visualizar PDF</button>' +
                    '<button class="btn btn-primary" id="a4-print" style="background:#c06010;border-color:#c06010">Imprimir na Lexmark</button>' +
                '</div>' +
            '</div>' +
        '</div>';

    _loadLexmarkPrinters(S, 'a4-printer', 'a4-ip', 'a4-port');

    document.getElementById('a4-save-ip').addEventListener('click', function () {
        _saveNewPrinter(S, 'a4-save-name', 'a4-ip', 'a4-port', 'LEXMARK', function () {
            _loadLexmarkPrinters(S, 'a4-printer', 'a4-ip', 'a4-port');
        });
    });

    document.getElementById('a4-pdf').addEventListener('click', function () {
        var body = _a4Body();
        fetch('/api/identificacao/a4.pdf', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(body)
        }).then(function (r) {
            if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || 'Erro'); });
            return r.blob();
        }).then(function (blob) {
            var url = URL.createObjectURL(blob);
            window.open(url, '_blank');
            S.toast('PDF gerado', 'success');
        }).catch(function (e) { S.toast(e.message, 'error'); });
    });

    document.getElementById('a4-print').addEventListener('click', function () {
        var body = _a4Body();
        var pid = document.getElementById('a4-printer').value;
        if (!pid) { S.toast('Selecione uma impressora Lexmark', 'warning'); return; }
        body.printer_id = parseInt(pid);
        S.api('/identificacao/a4.print', { method: 'POST', body: body })
            .then(function (d) { S.toast(d.message, 'success'); })
            .catch(function (e) { S.toast(e.message, 'error'); });
    });
}

function _a4Body() {
    return {
        texto1: document.getElementById('a4-t1').value,
        texto2: document.getElementById('a4-t2').value,
        texto3: document.getElementById('a4-t3').value,
        copies: parseInt(document.getElementById('a4-copies').value) || 1,
    };
}


/* ================================================================
   3. IMPRESSÃO ZEBRA LIVRE
   ================================================================ */
function _renderLivre(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Impressão Zebra Livre</h1>' +
        '<p style="color:var(--text-secondary);margin-bottom:1.5rem">' +
            'Operação integrada ao Recebimento. ' +
            'Não consome sequência e não altera recebimentos.</p>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Etiqueta Zebra livre</div>' +
            '<div class="card-body">' +
                '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem">' +
                    '<div><label>Título</label>' +
                        '<input id="zl-titulo" class="form-control" placeholder="TRIAGEM"></div>' +
                    '<div><label>Identificador</label>' +
                        '<input id="zl-ident" class="form-control" placeholder="CX1242"></div>' +
                    '<div><label>Quantidade</label>' +
                        '<input id="zl-qty" class="form-control" type="number" value="1" min="1"></div>' +
                '</div>' +
                '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem;margin-top:1rem">' +
                    '<div><label>Equipamento</label>' +
                        '<input id="zl-equip" class="form-control" placeholder="BERÇO"></div>' +
                    '<div><label>Zebra salva</label>' +
                        '<select id="zl-printer" class="form-control"><option value="">Carregando...</option></select></div>' +
                    '<div><label>IP da Zebra</label>' +
                        '<input id="zl-ip" class="form-control" placeholder="10.x.x.x" readonly></div>' +
                '</div>' +
                '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem;margin-top:1rem">' +
                    '<div><label>Nome para salvar</label>' +
                        '<input id="zl-save-name" class="form-control" placeholder="Zebra Livre"></div>' +
                    '<div><label>Porta</label>' +
                        '<input id="zl-port" class="form-control" value="9100" readonly></div>' +
                    '<div><label>Cópias</label>' +
                        '<input id="zl-copies" class="form-control" type="number" value="1" min="1" max="50"></div>' +
                '</div>' +
                '<div style="margin-top:1.5rem;display:flex;gap:.8rem;flex-wrap:wrap">' +
                    '<button class="btn" id="zl-save-ip">Salvar IP</button>' +
                    '<button class="btn" id="zl-test">Testar conexão</button>' +
                    '<button class="btn btn-dark" id="zl-download">Baixar ZPL</button>' +
                    '<button class="btn btn-primary" id="zl-print" style="background:#c06010;border-color:#c06010">Imprimir na Zebra</button>' +
                '</div>' +
                '<pre id="zl-zpl-out" style="margin-top:1rem;display:none;max-height:250px;overflow:auto;' +
                    'background:var(--bg-input);padding:1rem;border-radius:8px;font-size:.85rem"></pre>' +
            '</div>' +
        '</div>';

    _loadZebraPrinters(S, 'zl-printer', 'zl-ip', 'zl-port');

    document.getElementById('zl-save-ip').addEventListener('click', function () {
        _saveNewPrinter(S, 'zl-save-name', 'zl-ip', 'zl-port', 'ZEBRA', function () {
            _loadZebraPrinters(S, 'zl-printer', 'zl-ip', 'zl-port');
        });
    });

    document.getElementById('zl-test').addEventListener('click', function () {
        var pid = document.getElementById('zl-printer').value;
        if (!pid) { S.toast('Selecione uma impressora', 'warning'); return; }
        S.api('/identificacao/test-printer', { method: 'POST', body: { printer_id: parseInt(pid) } })
            .then(function (d) { S.toast(d.message, 'success'); })
            .catch(function (e) { S.toast(e.message, 'error'); });
    });

    document.getElementById('zl-download').addEventListener('click', function () {
        var body = _livreBody();
        S.api('/identificacao/preview-livre', { method: 'POST', body: body })
            .then(function (d) {
                var pre = document.getElementById('zl-zpl-out');
                pre.style.display = 'block';
                pre.textContent = d.zpl;
            })
            .catch(function (e) { S.toast(e.message, 'error'); });
    });

    document.getElementById('zl-print').addEventListener('click', function () {
        var body = _livreBody();
        body.printer_id = parseInt(document.getElementById('zl-printer').value) || null;
        if (!body.printer_id) { S.toast('Selecione uma impressora', 'warning'); return; }
        body.copies = parseInt(document.getElementById('zl-copies').value) || 1;
        S.api('/identificacao/zebra-livre', { method: 'POST', body: body })
            .then(function (d) {
                S.toast('Etiqueta enviada para impressora', 'success');
                var pre = document.getElementById('zl-zpl-out');
                pre.style.display = 'block';
                pre.textContent = d.zpl;
            })
            .catch(function (e) { S.toast(e.message, 'error'); });
    });
}

function _livreBody() {
    return {
        titulo: document.getElementById('zl-titulo').value,
        identificador: document.getElementById('zl-ident').value,
        quantidade: parseInt(document.getElementById('zl-qty').value) || 1,
        equipamento: document.getElementById('zl-equip').value,
    };
}


/* ================================================================
   4. IMPRESSORAS
   ================================================================ */
function _renderImpressoras(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Impressoras</h1>' +
        '<div class="card mb-3">' +
            '<div class="card-header">Impressoras Cadastradas</div>' +
            '<div class="card-body" id="pr-list">Carregando...</div>' +
        '</div>' +
        '<div class="card">' +
            '<div class="card-header">Adicionar Impressora</div>' +
            '<div class="card-body">' +
                '<div style="display:grid;grid-template-columns:2fr 1fr 2fr 1fr auto;gap:.8rem;align-items:end">' +
                    '<div><label>Nome</label><input id="pr-name" class="form-control" placeholder="Zebra Triagem"></div>' +
                    '<div><label>Tipo</label>' +
                        '<select id="pr-type" class="form-control">' +
                            '<option value="ZEBRA">Zebra</option>' +
                            '<option value="LEXMARK">Lexmark</option>' +
                        '</select></div>' +
                    '<div><label>IP</label><input id="pr-ip" class="form-control" placeholder="10.115.x.x"></div>' +
                    '<div><label>Porta</label><input id="pr-port" class="form-control" type="number" value="9100"></div>' +
                    '<button class="btn btn-primary" id="pr-save">Salvar</button>' +
                '</div>' +
            '</div>' +
        '</div>';

    _refreshPrinterList(S);

    document.getElementById('pr-save').addEventListener('click', function () {
        var name = document.getElementById('pr-name').value.trim();
        var ip = document.getElementById('pr-ip').value.trim();
        if (!name || !ip) { S.toast('Preencha nome e IP', 'warning'); return; }
        S.api('/identificacao/printers', {
            method: 'POST',
            body: {
                name: name,
                type: document.getElementById('pr-type').value,
                ip: ip,
                port: parseInt(document.getElementById('pr-port').value) || 9100
            }
        }).then(function () {
            S.toast('Impressora salva', 'success');
            document.getElementById('pr-name').value = '';
            document.getElementById('pr-ip').value = '';
            _refreshPrinterList(S);
        }).catch(function (e) { S.toast(e.message, 'error'); });
    });
}

function _refreshPrinterList(S) {
    S.api('/identificacao/printers').then(function (d) {
        var list = d.impressoras || [];
        var el = document.getElementById('pr-list');
        if (!el) return;
        if (!list.length) {
            el.innerHTML = '<div style="padding:1rem;color:var(--text-secondary)">Nenhuma impressora cadastrada</div>';
            return;
        }
        var html = '<table class="data-table"><thead><tr>' +
            '<th>Nome</th><th>Tipo</th><th>IP</th><th>Porta</th><th>Ações</th>' +
            '</tr></thead><tbody>';
        for (var i = 0; i < list.length; i++) {
            var p = list[i];
            html += '<tr>' +
                '<td>' + S.esc(p.name) + '</td>' +
                '<td>' + S.badge(p.type) + '</td>' +
                '<td>' + S.esc(p.ip) + '</td>' +
                '<td>' + p.port + '</td>' +
                '<td style="display:flex;gap:.4rem">' +
                    '<button class="btn btn-sm pr-test" data-id="' + p.id + '">Testar</button>' +
                    '<button class="btn btn-sm btn-danger pr-del" data-id="' + p.id + '">Excluir</button>' +
                '</td></tr>';
        }
        html += '</tbody></table>';
        el.innerHTML = html;

        el.querySelectorAll('.pr-test').forEach(function (btn) {
            btn.addEventListener('click', function () {
                S.api('/identificacao/test-printer', { method: 'POST', body: { printer_id: parseInt(this.dataset.id) } })
                    .then(function (d) { S.toast(d.message, 'success'); })
                    .catch(function (e) { S.toast(e.message, 'error'); });
            });
        });
        el.querySelectorAll('.pr-del').forEach(function (btn) {
            btn.addEventListener('click', function () {
                if (!confirm('Excluir essa impressora?')) return;
                S.api('/identificacao/printers/' + this.dataset.id, { method: 'DELETE' })
                    .then(function () { S.toast('Excluída', 'success'); _refreshPrinterList(S); })
                    .catch(function (e) { S.toast(e.message, 'error'); });
            });
        });
    }).catch(function (e) {
        var el = document.getElementById('pr-list');
        if (el) el.innerHTML = '<div style="color:var(--color-danger)">' + S.esc(e.message) + '</div>';
    });
}


/* ================================================================
   SHARED HELPERS — printer selects
   ================================================================ */
function _loadZebraPrinters(S, selId, ipId, portId) {
    _loadTypedPrinters(S, selId, ipId, portId, 'ZEBRA');
}
function _loadLexmarkPrinters(S, selId, ipId, portId) {
    _loadTypedPrinters(S, selId, ipId, portId, 'LEXMARK');
}

function _loadTypedPrinters(S, selId, ipId, portId, filterType) {
    S.api('/identificacao/printers').then(function (d) {
        var sel = document.getElementById(selId);
        if (!sel) return;
        var list = (d.impressoras || []).filter(function (p) {
            return p.type === filterType && p.active;
        });
        sel.innerHTML = '<option value="">Selecionar...</option>';
        for (var i = 0; i < list.length; i++) {
            sel.innerHTML += '<option value="' + list[i].id +
                '" data-ip="' + S.esc(list[i].ip) +
                '" data-port="' + list[i].port + '">' +
                S.esc(list[i].name) + ' (' + list[i].ip + ')</option>';
        }
        sel.addEventListener('change', function () {
            var opt = sel.options[sel.selectedIndex];
            var ipEl = document.getElementById(ipId);
            var portEl = document.getElementById(portId);
            if (ipEl) ipEl.value = opt.dataset.ip || '';
            if (portEl) portEl.value = opt.dataset.port || '9100';
        });
    }).catch(function () {
        var sel = document.getElementById(selId);
        if (sel) sel.innerHTML = '<option value="">Erro ao carregar</option>';
    });
}

function _saveNewPrinter(S, nameId, ipId, portId, type, cb) {
    var name = document.getElementById(nameId).value.trim();
    var ipEl = document.getElementById(ipId);
    var ip = ipEl.value.trim();
    var port = parseInt(document.getElementById(portId).value) || 9100;
    if (!name || !ip) { S.toast('Preencha nome e IP para salvar', 'warning'); return; }
    S.api('/identificacao/printers', {
        method: 'POST',
        body: { name: name, ip: ip, port: port, type: type }
    }).then(function () {
        S.toast('Impressora salva', 'success');
        document.getElementById(nameId).value = '';
        if (cb) cb();
    }).catch(function (e) { S.toast(e.message, 'error'); });
}
