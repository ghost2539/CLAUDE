/* ================================================================
   Módulo: Agendamentos de Fornecedores (menu Entrada)
   Cadastra a entrega antes de chegar; ao chegar, confirma o
   recebimento (que a manda para a etapa de internalização).
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.agendamentos_forn = {

    async render(container) {
        var S = window.SPARE, e = S.esc;
        var OPC = { bus: [], destinos: [], status: {} };
        var u = S.user() || {};
        var pm = (u.permission_map || {}).agendamentos_forn || {};
        var podeCriar = !!(u.is_admin || pm.can_create);
        var podeEditar = !!(u.is_admin || pm.can_edit);
        var podeExcluir = !!(u.is_admin || pm.can_admin);

        container.innerHTML =
            '<h1 class="page-title">Agendamentos de Fornecedores</h1>' +
            '<div class="card mb-3"><div class="card-header" ' +
                'style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">' +
                '<span>Agendamentos</span>' +
                (podeCriar ? '<button id="agf-novo" class="btn btn-primary btn-sm">Novo agendamento</button>' : '') +
                '</div><div class="card-body">' +
                '<div class="filter-grid">' +
                    '<div class="form-group"><label for="agf-f-status">Status</label>' +
                        '<select id="agf-f-status" class="form-control">' +
                        '<option value="">Todos</option>' +
                        '<option value="AGENDADO">Agendado</option>' +
                        '<option value="RECEBIDO">Recebido</option></select></div>' +
                    '<div class="form-group"><label for="agf-f-busca">Buscar (NF, PO, fornecedor)</label>' +
                        '<input id="agf-f-busca" class="form-control" placeholder="digite e Enter"></div>' +
                '</div>' +
                '<div id="agf-lista" class="mt-3"></div>' +
            '</div></div>' +
            '<div class="card" id="agf-form-card" style="display:none">' +
                '<div class="card-header" id="agf-form-titulo">Novo agendamento</div>' +
                '<div class="card-body"><div id="agf-form"></div></div>' +
            '</div>';

        try { OPC = await S.api('/agendamentos-forn/opcoes'); } catch (x) { /* segue com listas vazias */ }

        function opcoesSelect(lista, sel, comVazio) {
            return (comVazio ? '<option value="">—</option>' : '') +
                lista.map(function (o) {
                    var val = o.valor !== undefined ? o.valor : o;
                    var rot = o.rotulo !== undefined ? o.rotulo : o;
                    return '<option value="' + e(val) + '"' + (val === sel ? ' selected' : '') + '>' + e(rot) + '</option>';
                }).join('');
        }

        // ── Formulário (novo/editar) ──────────────────────────────────
        function abrirForm(dado) {
            var edit = !!(dado && dado.id);
            document.getElementById('agf-form-titulo').textContent =
                edit ? ('Editar agendamento #' + dado.id) : 'Novo agendamento';
            document.getElementById('agf-form-card').style.display = '';
            var d = dado || {};
            var host = document.getElementById('agf-form');
            host.innerHTML =
                '<div class="card" style="background:var(--bg-input);margin-bottom:14px">' +
                  '<div class="card-body">' +
                  '<label style="display:block;margin-bottom:6px"><b>Importar da NF (PDF)</b> ' +
                  '<span class="text-muted">— preenche NF, PO e itens; confira antes de salvar. O PDF não é salvo no servidor.</span></label>' +
                  '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
                    '<input type="file" id="agf-pdf" accept="application/pdf,.pdf" class="form-control" style="max-width:360px">' +
                    '<button type="button" id="agf-pdf-btn" class="btn btn-secondary btn-sm">Ler PDF</button>' +
                    '<span id="agf-pdf-msg" class="text-muted"></span>' +
                  '</div></div>' +
                '</div>' +
                '<div class="form-grid cols-2">' +
                    campo('BU', '<select id="agf-bu" class="form-control">' + opcoesSelect(OPC.bus, d.bu, true) + '</select>') +
                    campo('Estoque destino *', '<select id="agf-destino" class="form-control">' + opcoesSelect(OPC.destinos, d.estoque_destino, true) + '</select>') +
                    campo('NF *', '<input id="agf-nf" class="form-control" value="' + e(d.nf || '') + '">') +
                    campo('PO *', '<input id="agf-po" class="form-control" value="' + e(d.po || '') + '">') +
                    campo('Fornecedor *', '<input id="agf-fornecedor" class="form-control" value="' + e(d.fornecedor || '') + '">') +
                    campo('Volumes', '<input id="agf-volumes" type="number" min="0" class="form-control" value="' + (d.volumes != null ? e(d.volumes) : '') + '">') +
                    campo('Data agendada *', '<input id="agf-data" type="date" class="form-control" value="' + e(d.data_agendada || '') + '">') +
                '</div>' +
                '<h3 class="mt-3">Equipamentos</h3>' +
                '<div id="agf-equis"></div>' +
                '<div class="btn-row mt-2"><button type="button" id="agf-add-equi" class="btn btn-secondary btn-sm">+ Adicionar equipamento</button></div>' +
                '<div class="btn-row mt-3">' +
                    '<button type="button" id="agf-salvar" class="btn btn-primary btn-sm">' + (edit ? 'Salvar' : 'Cadastrar') + '</button> ' +
                    '<button type="button" id="agf-cancelar" class="btn btn-secondary btn-sm">Cancelar</button>' +
                '</div>' +
                '<div id="agf-form-erro" class="alert alert-danger mt-2" hidden></div>';

            var equis = (d.equipamentos && d.equipamentos.length) ? d.equipamentos : [];
            equis.forEach(addEqui);
            if (!equis.length) addEqui();

            document.getElementById('agf-add-equi').onclick = function () { addEqui(); };
            document.getElementById('agf-cancelar').onclick = fecharForm;
            document.getElementById('agf-salvar').onclick = function () { salvar(edit ? d.id : 0); };
            document.getElementById('agf-pdf-btn').onclick = importarPdf;
            host.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

        async function importarPdf() {
            var inp = document.getElementById('agf-pdf');
            var msg = document.getElementById('agf-pdf-msg');
            if (!inp.files || !inp.files[0]) { msg.textContent = 'Escolha um PDF primeiro.'; return; }
            var fd = new FormData();
            fd.append('arquivo', inp.files[0]);
            msg.textContent = 'Lendo…';
            try {
                // multipart: sem Content-Type manual (o browser põe o boundary).
                var d = await S.api('/agendamentos-forn/extrair-nf', { method: 'POST', body: fd });
                if (d.nf) document.getElementById('agf-nf').value = d.nf;
                if (d.po) document.getElementById('agf-po').value = d.po;
                if (Array.isArray(d.itens) && d.itens.length) {
                    document.getElementById('agf-equis').innerHTML = '';
                    d.itens.forEach(addEqui);
                }
                var partes = [];
                if (d.nf) partes.push('NF ' + d.nf);
                if (d.po) partes.push('PO ' + d.po);
                partes.push((d.itens ? d.itens.length : 0) + ' item(ns)');
                msg.textContent = 'Lido: ' + partes.join(', ') + '. Confira e complete os campos.';
                if (!d.confiavel) msg.textContent += ' (chave da NF não encontrada — revise com atenção)';
            } catch (x) {
                msg.textContent = x.message;
            }
        }

        function campo(rotulo, controle) {
            return '<div class="form-group"><label>' + e(rotulo) + '</label>' + controle + '</div>';
        }

        function addEqui(eq) {
            eq = eq || {};
            var linha = document.createElement('div');
            linha.className = 'agf-equi-linha';
            linha.style.cssText = 'display:flex;gap:8px;margin-bottom:6px;align-items:center';
            linha.innerHTML =
                '<input class="form-control agf-eq-desc" placeholder="Equipamento (ex.: Positivo Master CE800)" value="' + e(eq.descricao || '') + '" style="flex:1">' +
                '<input class="form-control agf-eq-qtd" type="number" min="1" placeholder="Qtd" value="' + (eq.quantidade != null ? e(eq.quantidade) : '') + '" style="width:90px">' +
                '<button type="button" class="btn btn-secondary btn-sm agf-eq-rem" title="Remover">&times;</button>';
            linha.querySelector('.agf-eq-rem').onclick = function () { linha.remove(); };
            document.getElementById('agf-equis').appendChild(linha);
        }

        function fecharForm() {
            document.getElementById('agf-form-card').style.display = 'none';
            document.getElementById('agf-form').innerHTML = '';
        }

        function coletar() {
            var equis = [];
            Array.prototype.forEach.call(document.querySelectorAll('.agf-equi-linha'), function (l) {
                var desc = l.querySelector('.agf-eq-desc').value.trim();
                var qtd = parseInt(l.querySelector('.agf-eq-qtd').value, 10);
                if (desc || l.querySelector('.agf-eq-qtd').value) {
                    equis.push({ descricao: desc, quantidade: isNaN(qtd) ? 0 : qtd });
                }
            });
            var vol = document.getElementById('agf-volumes').value.trim();
            return {
                bu: document.getElementById('agf-bu').value,
                nf: document.getElementById('agf-nf').value.trim(),
                po: document.getElementById('agf-po').value.trim(),
                volumes: vol === '' ? null : parseInt(vol, 10),
                fornecedor: document.getElementById('agf-fornecedor').value.trim(),
                estoque_destino: document.getElementById('agf-destino').value,
                data_agendada: document.getElementById('agf-data').value,
                equipamentos: equis
            };
        }

        async function salvar(id) {
            var erro = document.getElementById('agf-form-erro');
            erro.hidden = true;
            var corpo = coletar();
            try {
                if (id) {
                    await S.api('/agendamentos-forn/' + id, { method: 'PATCH', body: JSON.stringify(corpo) });
                    S.toast('Agendamento atualizado.', 'success');
                } else {
                    await S.api('/agendamentos-forn', { method: 'POST', body: JSON.stringify(corpo) });
                    S.toast('Agendamento cadastrado.', 'success');
                }
                fecharForm();
                carregar();
            } catch (x) {
                erro.hidden = false;
                erro.textContent = x.message;
            }
        }

        // ── Lista ─────────────────────────────────────────────────────
        async function carregar() {
            var host = document.getElementById('agf-lista');
            host.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div>';
            try {
                var qs = '?status=' + encodeURIComponent(document.getElementById('agf-f-status').value) +
                         '&busca=' + encodeURIComponent(document.getElementById('agf-f-busca').value.trim());
                var d = await S.api('/agendamentos-forn' + qs);
                if (!d.total) { host.innerHTML = '<p class="text-muted">Nenhum agendamento.</p>'; return; }
                host.innerHTML =
                    '<div class="table-wrapper"><table class="data-table"><thead><tr>' +
                    '<th>NF</th><th>PO</th><th>BU</th><th>Fornecedor</th><th>Destino</th>' +
                    '<th>Vol.</th><th>Agendada</th><th>Equipamentos</th><th>Status</th>' +
                    '<th>Recebimento</th><th></th>' +
                    '</tr></thead><tbody>' + d.itens.map(linhaHtml).join('') + '</tbody></table></div>';
                d.itens.forEach(function (a) {
                    var rc = document.getElementById('agf-receber-' + a.id);
                    if (rc) rc.onclick = function () { receber(a.id); };
                    var ed = document.getElementById('agf-editar-' + a.id);
                    if (ed) ed.onclick = function () { abrirForm(a); };
                    var ex = document.getElementById('agf-excluir-' + a.id);
                    if (ex) ex.onclick = function () { excluir(a.id); };
                });
            } catch (x) {
                host.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            }
        }

        function linhaHtml(a) {
            var equis = (a.equipamentos || []).map(function (q) {
                return e(q.descricao) + ' <b>×' + e(q.quantidade) + '</b>';
            }).join('<br>') || '<span class="text-muted">—</span>';
            var badge = a.status === 'RECEBIDO'
                ? '<span class="badge badge-success">Recebido</span>'
                : '<span class="badge badge-warning">Agendado</span>';
            var acoes = '';
            if (a.status !== 'RECEBIDO' && podeEditar)
                acoes += '<button id="agf-receber-' + a.id + '" class="btn btn-primary btn-sm">Confirmar recebimento</button> ';
            if (a.status !== 'RECEBIDO' && podeEditar)
                acoes += '<button id="agf-editar-' + a.id + '" class="btn btn-secondary btn-sm">Editar</button> ';
            if (podeExcluir)
                acoes += '<button id="agf-excluir-' + a.id + '" class="btn btn-secondary btn-sm">Excluir</button>';
            return '<tr>' +
                '<td>' + e(a.nf) + '</td><td>' + e(a.po) + '</td><td>' + e(a.bu || '—') + '</td>' +
                '<td>' + e(a.fornecedor) + '</td><td>' + e(a.estoque_destino_rotulo) + '</td>' +
                '<td>' + (a.volumes != null ? e(a.volumes) : '—') + '</td>' +
                '<td>' + e(fmtData(a.data_agendada)) + '</td>' +
                '<td>' + equis + '</td><td>' + badge + '</td>' +
                '<td>' + (a.data_recebimento ? e(fmtData(a.data_recebimento)) : '<span class="text-muted">—</span>') + '</td>' +
                '<td style="white-space:nowrap">' + (acoes || '<span class="text-muted">—</span>') + '</td>' +
                '</tr>';
        }

        function fmtData(iso) {
            if (!iso) return '';
            var p = iso.split('-');
            return p.length === 3 ? (p[2] + '/' + p[1] + '/' + p[0]) : iso;
        }

        async function receber(id) {
            if (!confirm('Confirmar o recebimento deste agendamento? Ele seguirá para a internalização.')) return;
            try {
                await S.api('/agendamentos-forn/' + id + '/receber', { method: 'POST' });
                S.toast('Recebimento confirmado.', 'success');
                carregar();
            } catch (x) { S.toast(x.message, 'error'); }
        }

        async function excluir(id) {
            if (!confirm('Excluir este agendamento? Esta ação não pode ser desfeita.')) return;
            try {
                await S.api('/agendamentos-forn/' + id, { method: 'DELETE' });
                S.toast('Agendamento excluído.', 'success');
                carregar();
            } catch (x) { S.toast(x.message, 'error'); }
        }

        if (podeCriar) document.getElementById('agf-novo').onclick = function () { abrirForm(null); };
        document.getElementById('agf-f-status').onchange = carregar;
        document.getElementById('agf-f-busca').onkeydown = function (ev) { if (ev.key === 'Enter') carregar(); };
        carregar();
    }
};
