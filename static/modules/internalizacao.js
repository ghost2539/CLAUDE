/* ================================================================
   Módulo: Internalização (menu Entrada)
   Recebe os agendamentos com "Recebimento confirmado". Para cada
   equipamento fisicamente recebido, informa-se: item do EBS,
   descrição, plaqueta (nº do bem), número de série e a NF. Grava em
   banco e exporta a planilha "Placa Patrimonial" (layout idêntico).
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.internalizacao = {

    async render(container) {
        var S = window.SPARE, e = S.esc;
        var u = S.user() || {};
        var pm = (u.permission_map || {}).internalizacao || {};
        var podeEditar = !!(u.is_admin || pm.can_edit);
        var podeExportar = !!(u.is_admin || pm.can_export);

        container.innerHTML =
            '<h1 class="page-title">Internalização</h1>' +
            '<div class="card mb-3"><div class="card-header">Recebidos para internalizar</div>' +
            '<div class="card-body">' +
                '<div class="filter-grid">' +
                    '<div class="form-group"><label for="int-f-status">Status</label>' +
                        '<select id="int-f-status" class="form-control">' +
                        '<option value="">Todos</option>' +
                        '<option value="PENDENTE">Pendente</option>' +
                        '<option value="CONCLUIDA">Concluída</option></select></div>' +
                    '<div class="form-group"><label for="int-f-busca">Buscar (NF, PO, fornecedor)</label>' +
                        '<input id="int-f-busca" class="form-control" placeholder="digite e Enter"></div>' +
                '</div>' +
                '<div id="int-lista" class="mt-3"></div>' +
            '</div></div>' +
            '<div class="card" id="int-form-card" style="display:none">' +
                '<div class="card-header" id="int-form-titulo">Internalizar</div>' +
                '<div class="card-body"><div id="int-form"></div></div>' +
            '</div>';

        var elLista = document.getElementById('int-lista');

        // ── Lista ─────────────────────────────────────────────────────
        async function carregar() {
            elLista.innerHTML = '<p class="text-muted">Carregando…</p>';
            var status = document.getElementById('int-f-status').value;
            var busca = document.getElementById('int-f-busca').value.trim();
            var qs = [];
            if (status) qs.push('status=' + encodeURIComponent(status));
            if (busca) qs.push('busca=' + encodeURIComponent(busca));
            var d;
            try {
                d = await S.api('/internalizacao' + (qs.length ? '?' + qs.join('&') : ''));
            } catch (x) { elLista.innerHTML = '<p class="alert alert-danger">' + e(x.message) + '</p>'; return; }
            if (!d.itens.length) { elLista.innerHTML = '<p class="text-muted">Nenhum agendamento recebido nesta condição.</p>'; return; }
            var linhas = d.itens.map(function (it) {
                var eqs = (it.equipamentos || []).map(function (q) {
                    return e(q.descricao) + ' <span class="text-muted">×' + e(q.quantidade) + '</span>';
                }).join('<br>');
                var badge = it.status === 'CONCLUIDA'
                    ? '<span class="badge badge-success">Concluída</span>'
                    : '<span class="badge badge-warning">Pendente</span>';
                var btns = '<button class="btn btn-primary btn-sm int-abrir" data-id="' + it.agendamento_id + '">' +
                           (podeEditar ? 'Internalizar' : 'Ver') + '</button>';
                if (podeExportar && it.total_ativos > 0) {
                    btns += ' <button class="btn btn-secondary btn-sm int-exp" data-id="' + it.agendamento_id + '">Exportar</button>';
                }
                return '<tr>' +
                    '<td><b>' + e(it.nf) + '</b></td>' +
                    '<td>' + e(it.fornecedor) + '</td>' +
                    '<td>' + e(it.bu) + '</td>' +
                    '<td>' + e(it.estoque_destino_rotulo) + '</td>' +
                    '<td>' + (it.data_recebimento ? e(S.formatDate ? S.formatDate(it.data_recebimento) : it.data_recebimento) : '—') + '</td>' +
                    '<td style="font-size:.85em">' + eqs + '</td>' +
                    '<td>' + badge + ' <span class="text-muted">' + e(it.total_ativos) + ' ativo(s)</span></td>' +
                    '<td>' + btns + '</td>' +
                    '</tr>';
            }).join('');
            elLista.innerHTML =
                '<div class="table-responsive"><table class="table table-sm">' +
                '<thead><tr><th>NF</th><th>Fornecedor</th><th>BU</th><th>Destino</th>' +
                '<th>Recebido</th><th>Equipamentos</th><th>Situação</th><th></th></tr></thead>' +
                '<tbody>' + linhas + '</tbody></table></div>';
            Array.prototype.forEach.call(elLista.querySelectorAll('.int-abrir'), function (b) {
                b.onclick = function () { abrir(parseInt(b.dataset.id, 10)); };
            });
            Array.prototype.forEach.call(elLista.querySelectorAll('.int-exp'), function (b) {
                b.onclick = function () { exportar(parseInt(b.dataset.id, 10)); };
            });
        }

        function exportar(id) {
            // GET autenticado por cookie; o browser baixa o arquivo.
            var base = (S.base || '');
            window.open(base + '/api/internalizacao/' + id + '/exportar', '_blank');
        }

        // ── Formulário de internalização ──────────────────────────────
        async function abrir(id) {
            var card = document.getElementById('int-form-card');
            var host = document.getElementById('int-form');
            card.style.display = '';
            host.innerHTML = '<p class="text-muted">Carregando…</p>';
            var d;
            try { d = await S.api('/internalizacao/' + id); }
            catch (x) { host.innerHTML = '<p class="alert alert-danger">' + e(x.message) + '</p>'; return; }

            document.getElementById('int-form-titulo').textContent =
                'Internalizar — NF ' + (d.nf || '') + ' · ' + (d.fornecedor || '');

            host.innerHTML =
                '<div class="form-grid cols-3" style="margin-bottom:12px">' +
                    info('NF', d.nf) + info('Fornecedor', d.fornecedor) +
                    info('BU', d.bu) + info('Destino', d.estoque_destino_rotulo) +
                    info('Recebido em', d.data_recebimento || '—') +
                    info('Situação', (d.status_rotulo || '')) +
                '</div>' +
                '<p class="text-muted" style="margin:.2em 0 .8em">Uma linha por equipamento físico recebido. ' +
                'A NF é a mesma da nota. Preencha item do EBS, plaqueta e número de série.</p>' +
                '<div class="table-responsive"><table class="table table-sm" id="int-tab">' +
                    '<thead><tr><th style="width:120px">Item EBS</th><th>Descrição do item</th>' +
                    '<th style="width:140px">Plaqueta</th><th style="width:180px">Número de série</th>' +
                    (podeEditar ? '<th style="width:40px"></th>' : '') + '</tr></thead>' +
                    '<tbody id="int-linhas"></tbody>' +
                '</table></div>' +
                (podeEditar ? '<div class="btn-row mt-2"><button type="button" id="int-add" class="btn btn-secondary btn-sm">+ Adicionar linha</button></div>' : '') +
                '<div class="btn-row mt-3">' +
                    (podeEditar ? '<button type="button" id="int-salvar" class="btn btn-primary btn-sm">Salvar</button> ' +
                                  '<button type="button" id="int-concluir" class="btn btn-success btn-sm">Salvar e concluir</button> ' : '') +
                    (podeExportar ? '<button type="button" id="int-exportar" class="btn btn-secondary btn-sm">Exportar planilha</button> ' : '') +
                    '<button type="button" id="int-cancelar" class="btn btn-secondary btn-sm">Fechar</button>' +
                '</div>' +
                '<div id="int-erro" class="alert alert-danger mt-2" hidden></div>' +
                '<div id="int-msg" class="text-muted mt-2"></div>';

            // popular linhas: ativos salvos, ou semear pelos equipamentos esperados
            var salvos = d.ativos || [];
            if (salvos.length) {
                salvos.forEach(addLinha);
            } else {
                (d.equipamentos_esperados || []).forEach(function (q) {
                    var n = Math.max(1, parseInt(q.quantidade, 10) || 1);
                    for (var i = 0; i < n; i++) addLinha({ descricao: q.descricao });
                });
                if (!(d.equipamentos_esperados || []).length) addLinha();
            }

            if (podeEditar) {
                document.getElementById('int-add').onclick = function () { addLinha(); };
                document.getElementById('int-salvar').onclick = function () { salvar(id, false); };
                document.getElementById('int-concluir').onclick = function () { salvar(id, true); };
            }
            if (podeExportar) document.getElementById('int-exportar').onclick = function () { exportar(id); };
            document.getElementById('int-cancelar').onclick = function () {
                card.style.display = 'none'; host.innerHTML = '';
            };
            host.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

        function info(rot, val) {
            return '<div class="form-group"><label>' + e(rot) + '</label>' +
                   '<div style="padding:6px 0"><b>' + e(val || '—') + '</b></div></div>';
        }

        function addLinha(a) {
            a = a || {};
            var tb = document.getElementById('int-linhas');
            var tr = document.createElement('tr');
            tr.className = 'int-linha';
            var ro = podeEditar ? '' : ' readonly';
            tr.innerHTML =
                '<td><input class="form-control int-ebs" value="' + e(a.ebs_item || '') + '"' + ro + '></td>' +
                '<td><input class="form-control int-desc" value="' + e(a.descricao || '') + '"' + ro + '></td>' +
                '<td><input class="form-control int-plaq" value="' + e(a.plaqueta || '') + '"' + ro + '></td>' +
                '<td><input class="form-control int-serie" value="' + e(a.numero_serie || '') + '"' + ro + '></td>' +
                (podeEditar ? '<td><button type="button" class="btn btn-secondary btn-sm int-rem" title="Remover">&times;</button></td>' : '');
            if (podeEditar) tr.querySelector('.int-rem').onclick = function () { tr.remove(); };
            tb.appendChild(tr);
        }

        function coletar() {
            var ativos = [];
            Array.prototype.forEach.call(document.querySelectorAll('.int-linha'), function (tr) {
                var o = {
                    ebs_item: tr.querySelector('.int-ebs').value.trim(),
                    descricao: tr.querySelector('.int-desc').value.trim(),
                    plaqueta: tr.querySelector('.int-plaq').value.trim(),
                    numero_serie: tr.querySelector('.int-serie').value.trim()
                };
                if (o.ebs_item || o.descricao || o.plaqueta || o.numero_serie) ativos.push(o);
            });
            return ativos;
        }

        async function salvar(id, concluir) {
            var erro = document.getElementById('int-erro');
            var msg = document.getElementById('int-msg');
            erro.hidden = true; msg.textContent = '';
            var ativos = coletar();
            if (concluir) {
                var faltando = ativos.some(function (a) { return !a.plaqueta || !a.numero_serie; });
                if (faltando && !confirm('Há ativos sem plaqueta ou número de série. Concluir mesmo assim?')) return;
            }
            try {
                var d = await S.api('/internalizacao/' + id, {
                    method: 'PUT',
                    body: JSON.stringify({ ativos: ativos, concluir: !!concluir })
                });
                msg.textContent = 'Salvo: ' + d.ativos.length + ' ativo(s). Situação: ' + (d.status_rotulo || '') + '.';
                if (S.toast) S.toast('Internalização salva.');
                carregar();
            } catch (x) {
                erro.hidden = false; erro.textContent = x.message;
            }
        }

        // ── Eventos da lista ──────────────────────────────────────────
        document.getElementById('int-f-status').onchange = carregar;
        document.getElementById('int-f-busca').addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter') carregar();
        });
        carregar();
    }
};
