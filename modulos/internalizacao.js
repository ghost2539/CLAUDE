/* ================================================================
   Módulo: Internalização (menu Entrada)
   Recebe os agendamentos com "Recebimento confirmado". Para cada
   equipamento fisicamente recebido, informa-se: item do EBS,
   descrição, plaqueta (nº do bem), número de série e a NF. Grava em
   banco e exporta a planilha "Placa Patrimonial" (layout idêntico).
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.internalizacao = {

    // Três etapas, três telas. A etapa é de CADA equipamento: numa nota com
    // dez desktops, se sete aparecerem no EBS e três não, os sete seguem.
    async render(container, sub) {
        var S = window.SPARE;
        var ABAS = [
            ['lancamento',  'Lançamento'],
            ['patrimonio',  'Patrimônio'],
            ['entrada',     'Entrada de Equipamento']
        ];
        sub = sub || 'lancamento';
        if (!ABAS.some(function (x) { return x[0] === sub; })) sub = 'lancamento';
        S.tabs(ABAS, sub, 'internalizacao');
        if (sub === 'patrimonio') return telaPatrimonio(container, S);
        if (sub === 'entrada') return telaEntrada(container, S);
        return this._lancamento(container);
    },

    async _lancamento(container) {
        var S = window.SPARE, e = S.esc;
        var u = S.user() || {};
        var pm = (u.permission_map || {}).internalizacao || {};
        var podeEditar = !!(u.is_admin || pm.can_edit);
        var podeExportar = !!(u.is_admin || pm.can_export);
        // Excluir o processo é só do admin do portal inteiro — não da
        // permissão do módulo. Apaga o que já foi patrimoniado e lançado.
        var podeExcluir = !!u.is_admin;

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
                if (podeExcluir) {
                    btns += ' <button class="btn btn-danger btn-sm int-del" data-id="' + it.agendamento_id +
                            '" data-nf="' + e(it.nf) + '" data-ativos="' + e(it.total_ativos) +
                            '">Excluir</button>';
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
            Array.prototype.forEach.call(elLista.querySelectorAll('.int-del'), function (b) {
                b.onclick = function () {
                    excluir(parseInt(b.dataset.id, 10), b.dataset.nf,
                            parseInt(b.dataset.ativos, 10) || 0);
                };
            });
        }

        /* Apaga o processo de internalização daquele agendamento. O
           agendamento em si e os ativos já dados entrada no estoque NÃO são
           tocados — some o que foi registrado AQUI (patrimônio, plaquetas,
           confirmações). Por isso o aviso é explícito sobre o que se perde. */
        async function excluir(id, nf, ativos) {
            // Quem manda é o servidor (a rota exige admin); esta linha existe
            // para a trava andar junto da chamada, e não só do botão.
            if (!(S.user() || {}).is_admin) {
                S.toast('Só o administrador do portal pode excluir.', 'warning');
                return;
            }
            var texto = 'Excluir o processo de internalização da NF ' + nf + '?';
            if (ativos > 0) {
                texto += '\n\nEste processo tem ' + ativos + ' ativo(s) lançado(s). ' +
                         'Os números de patrimônio e as confirmações serão perdidos.';
            }
            texto += '\n\nNão dá para desfazer.';
            if (!window.confirm(texto)) return;
            try {
                await S.api('/internalizacao/' + id, { method: 'DELETE' });
                S.toast('Processo da NF ' + nf + ' excluído.', 'success');
                carregar();
            } catch (x) {
                S.toast(x.message, 'error');
            }
        }

        function exportar(id) {
            // GET autenticado por cookie; o browser baixa o arquivo.
            // `S.base` não existia quando isto foi escrito: caía sempre no
            // '' do ||, virando caminho absoluto, e a exportação ia para
            // fora do portal exatamente como a de Recebimentos.
            window.open(S.apiUrl('/internalizacao/' + id + '/exportar'), '_blank');
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


/* ── Patrimônio ─────────────────────────────────────────────────────
   Onde o equipamento espera o serial aparecer no EBS. Renner e Camicado
   têm o patrimônio criado lá e a consulta responde sozinha; Youcom compra
   por fora, não há o que consultar, e uma pessoa confirma no botão.
   ─────────────────────────────────────────────────────────────────── */
async function telaPatrimonio(c, S) {
    var e = S.esc;
    var u = S.user() || {};
    var pm = (u.permission_map || {}).internalizacao || {};
    var podeEditar = !!(u.is_admin || pm.can_edit);

    c.innerHTML =
        '<h1 class="page-title">Patrimônio</h1>' +
        '<p class="text-muted">Equipamentos lançados, esperando virar ativo. ' +
            'Renner e Camicado: a consulta procura o número de série no EBS. ' +
            'Youcom: a entrada é confirmada aqui, porque não há patrimônio no ' +
            'EBS para encontrar.</p>' +
        '<div class="btn-row mb-3">' +
            '<button id="pat-consultar" class="btn btn-primary"' +
                (podeEditar ? '' : ' disabled') + '>Consultar EBS agora</button>' +
            '<button id="pat-confirmar" class="btn btn-secondary"' +
                (podeEditar ? '' : ' disabled') + '>Confirmar entrada (marcados)</button>' +
        '</div>' +
        '<div id="pat-msg"></div>' +
        '<div id="pat-lista"></div>';

    async function carregar() {
        var alvo = document.getElementById('pat-lista');
        alvo.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div>';
        try {
            var d = await S.api('/internalizacao/fluxo/patrimonio');
            alvo.innerHTML = '';
            if (!d.total) {
                alvo.appendChild(S.el('p', { className: 'text-muted',
                    textContent: 'Nenhum equipamento esperando patrimônio.' }));
                return;
            }
            alvo.appendChild(S.el('p', { className: 'text-muted',
                textContent: d.total + ' equipamento(s): ' + d.aguardando_ebs +
                    ' esperando o EBS, ' + d.aguardando_confirmacao +
                    ' esperando confirmação manual.' }));
            alvo.appendChild(S.table([
                { key: 'id', label: '', html: true, render: function (v, r) {
                    // Só quem não tem EBS pode ser confirmado à mão: marcar
                    // os outros daria a impressão de que dá para pular a
                    // conferência que existe para pegar serial trocado.
                    if (r.tem_ebs) return '<span class="text-muted" title="Esta BU tem patrimônio no EBS: use a consulta">—</span>';
                    return '<input class="pat-alvo" type="checkbox" value="' + e(String(v)) + '">';
                } },
                { key: 'bu', label: 'BU' },
                { key: 'nf', label: 'NF' },
                { key: 'fornecedor', label: 'Fornecedor' },
                { key: 'descricao', label: 'Equipamento' },
                { key: 'numero_serie', label: 'Nº de série' },
                { key: 'plaqueta', label: 'Plaqueta' },
                { key: 'tem_ebs', label: 'Espera', html: true, render: function (v) {
                    return v ? '<span class="badge badge-info">EBS</span>'
                             : '<span class="badge badge-warning">Confirmação</span>';
                } },
                { key: 'data_recebimento', label: 'Recebido em' }
            ], d.itens));
        } catch (x) {
            alvo.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
        }
    }

    document.getElementById('pat-consultar').onclick = async function () {
        var b = this, txt = b.textContent;
        b.disabled = true; b.textContent = 'Consultando…';
        var msg = document.getElementById('pat-msg');
        try {
            var d = await S.api('/internalizacao/fluxo/patrimonio/consultar',
                                { method: 'POST' });
            var partes = [d.encontrados + ' de ' + d.consultados + ' encontrado(s) no EBS'];
            if (d.erros) partes.push(d.erros + ' consulta(s) falharam');
            msg.innerHTML = '<div class="alert alert-' +
                (d.encontrados ? 'success' : 'info') + '">' +
                e(d.aviso || partes.join('; ') + '.') + '</div>';
            if (d.encontrados) S.toast(d.encontrados + ' equipamento(s) liberados.', 'success');
            await carregar();
        } catch (x) {
            // 503 é configuração (credencial do EBS), não erro de quem clicou.
            var texto = x.status === 503
                ? 'Falta credencial da base do EBS. ' + x.message : x.message;
            msg.innerHTML = '<div class="alert alert-danger">' + e(texto) + '</div>';
            S.toast(texto, x.status === 503 ? 'warning' : 'error');
        } finally { b.disabled = false; b.textContent = txt; }
    };

    document.getElementById('pat-confirmar').onclick = async function () {
        var ids = [];
        c.querySelectorAll('.pat-alvo:checked').forEach(function (ch) {
            ids.push(parseInt(ch.value, 10));
        });
        if (!ids.length) return S.toast('Marque ao menos um equipamento.', 'warning');
        if (!confirm('Confirmar a entrada de ' + ids.length + ' equipamento(s)?\n\n' +
                     'Eles seguem para a Entrada de Equipamento.')) return;
        try {
            var d = await S.api('/internalizacao/fluxo/patrimonio/confirmar',
                                { method: 'POST', body: { ids: ids } });
            S.toast(d.confirmados.length + ' equipamento(s) confirmados.', 'success');
            await carregar();
        } catch (x) { S.toast(x.message, 'error'); }
    };

    carregar();
}


/* ── Entrada de Equipamento ─────────────────────────────────────────
   O técnico de gestão de ativos informa o espaço e corredor, o ativo sobe
   no ServiceNow e o equipamento entra no estoque do portal. É o passo que
   faz "entrou em estoque" significar alguma coisa: sem ele o equipamento
   ficaria concluído aqui e invisível na Consulta.
   ─────────────────────────────────────────────────────────────────── */
async function telaEntrada(c, S) {
    var e = S.esc;
    var u = S.user() || {};
    var pm = (u.permission_map || {}).internalizacao || {};
    var podeEditar = !!(u.is_admin || pm.can_edit);

    c.innerHTML =
        '<h1 class="page-title">Entrada de Equipamento</h1>' +
        '<p class="text-muted">Equipamentos com patrimônio resolvido. Informe o ' +
            'espaço e corredor, conclua, e eles sobem no ServiceNow e entram no ' +
            'estoque do portal.</p>' +
        '<div class="filter-grid mb-2">' +
            '<div class="form-group"><label for="ent-espaco">Espaço e corredor *</label>' +
                '<input id="ent-espaco" class="form-control" placeholder="ex.: A-12" ' +
                'title="Sem isto o ServiceNow recebe o ativo sem lugar, e ninguém acha o equipamento na prateleira"></div>' +
        '</div>' +
        '<div class="btn-row mb-3">' +
            '<button id="ent-concluir" class="btn btn-primary"' +
                (podeEditar ? '' : ' disabled') + '>Concluir entrada (marcados)</button>' +
            '<button id="ent-todos" class="btn btn-secondary" type="button">Marcar todos</button>' +
        '</div>' +
        '<div id="ent-msg"></div>' +
        '<div id="ent-lista"></div>';

    async function carregar() {
        var alvo = document.getElementById('ent-lista');
        alvo.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div>';
        try {
            var d = await S.api('/internalizacao/fluxo/entrada');
            alvo.innerHTML = '';
            if (!d.total) {
                alvo.appendChild(S.el('p', { className: 'text-muted',
                    textContent: 'Nenhum equipamento pronto para entrada.' }));
                return;
            }
            alvo.appendChild(S.table([
                { key: 'id', label: '', html: true, render: function (v) {
                    return '<input class="ent-alvo" type="checkbox" value="' + e(String(v)) + '">';
                } },
                { key: 'bu', label: 'BU' },
                { key: 'nf', label: 'NF' },
                { key: 'descricao', label: 'Equipamento' },
                { key: 'numero_serie', label: 'Nº de série' },
                { key: 'ebs_ativo', label: 'Ativo (EBS)', render: function (v) { return v || '—'; } },
                { key: 'plaqueta', label: 'Plaqueta' },
                { key: 'confirmado_por', label: 'Confirmado por',
                  render: function (v) { return v || '—'; } }
            ], d.itens));
        } catch (x) {
            alvo.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
        }
    }

    document.getElementById('ent-todos').onclick = function () {
        var todos = c.querySelectorAll('.ent-alvo');
        var marcar = Array.prototype.some.call(todos, function (x) { return !x.checked; });
        todos.forEach(function (x) { x.checked = marcar; });
    };

    document.getElementById('ent-concluir').onclick = async function () {
        var espaco = document.getElementById('ent-espaco').value.trim();
        if (!espaco) {
            document.getElementById('ent-espaco').focus();
            return S.toast('Informe o espaço e corredor.', 'warning');
        }
        var ids = [];
        c.querySelectorAll('.ent-alvo:checked').forEach(function (ch) {
            ids.push(parseInt(ch.value, 10));
        });
        if (!ids.length) return S.toast('Marque ao menos um equipamento.', 'warning');
        if (!confirm('Concluir ' + ids.length + ' equipamento(s) em "' + espaco + '"?\n\n' +
                     'Eles sobem no ServiceNow e entram no estoque do portal.')) return;
        var b = this, txt = b.textContent;
        b.disabled = true; b.textContent = 'Concluindo…';
        var msg = document.getElementById('ent-msg');
        try {
            var d = await S.api('/internalizacao/fluxo/entrada', {
                method: 'POST', body: { ids: ids, espaco_corredor: espaco } });
            // O aviso existe quando o estoque entrou mas o ServiceNow não
            // confirmou: dizer só "concluído" esconderia trabalho pendente.
            msg.innerHTML = d.aviso
                ? '<div class="alert alert-warning">' + e(d.aviso) + '</div>'
                : '<div class="alert alert-success">' + d.concluidos.length +
                  ' equipamento(s) no estoque e marcados no ServiceNow.</div>';
            S.toast(d.concluidos.length + ' equipamento(s) concluídos.',
                    d.aviso ? 'warning' : 'success');
            await carregar();
        } catch (x) {
            msg.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            S.toast(x.message, 'error');
        } finally { b.disabled = false; b.textContent = txt; }
    };

    carregar();
}
