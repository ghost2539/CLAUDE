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
    // Mais dois cadastros que o lançamento consome: o estoque de etiquetas
    // de patrimônio e a lista do que é imobilizado.
    async render(container, sub) {
        var S = window.SPARE;
        var ABAS = [
            ['lancamento',   'Lançamento'],
            ['patrimonio',   'Patrimônio'],
            ['entrada',      'Entrada de Equipamento'],
            ['etiquetas',    'Cadastro de Etiquetas'],
            ['imobilizados', 'Itens Imobilizados']
        ];
        sub = sub || 'lancamento';
        if (!ABAS.some(function (x) { return x[0] === sub; })) sub = 'lancamento';
        S.tabs(ABAS, sub, 'internalizacao');
        if (sub === 'patrimonio') return telaPatrimonio(container, S);
        if (sub === 'entrada') return telaEntrada(container, S);
        if (sub === 'etiquetas') return telaEtiquetas(container, S);
        if (sub === 'imobilizados') return telaImobilizados(container, S);
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
            '<div class="card mb-3"><div class="card-header" style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">' +
                '<span>Recebidos para internalizar</span>' +
                (podeEditar ? '<button id="int-conferir-sn" class="btn btn-secondary btn-sm" type="button">Conferir formulário do ServiceNow</button>' : '') +
            '</div><div class="card-body">' +
            '<div id="int-certs" class="text-muted mb-2"></div>' +
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
                if (it.entrega_parcial) badge += ' <span class="badge badge-warning">Entrega parcial</span>';
                if (it.sn_request_number) badge += ' <span class="badge badge-info">' + e(it.sn_request_number) + '</span>';
                else if (it.sn_pendente) badge += ' <span class="badge badge-danger">Chamado pendente</span>';
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
            var enviado = !!d.lancado_em;
            var editavel = podeEditar && !enviado;
            var rec = d.recebimento || null;
            var notas = d.notas || [];

            host.innerHTML =
                '<div class="form-grid cols-3" style="margin-bottom:12px">' +
                    info('NF', d.nf) + info('Fornecedor', d.fornecedor) +
                    info('BU', d.bu) + info('Destino', d.estoque_destino_rotulo) +
                    info('Recebido em', d.data_recebimento || '—') +
                    info('Situação', (d.status_rotulo || '') + (rec && rec.entrega_parcial ? ' · entrega parcial' : '')) +
                '</div>' +
                (d.sn_request_number ? '<div class="alert alert-success">Chamado <b>' + e(d.sn_request_number) + '</b>' + (d.sn_ritm_number ? ' · ' + e(d.sn_ritm_number) : '') + ' aberto em ' + e(fmtDataHora(d.sn_enviado_em)) + '.</div>' : '') +
                (d.sn_pendente ? '<div class="alert alert-danger">Chamado no ServiceNow pendente: ' + e(d.sn_erro || 'não enviado') + '</div>' : '') +
                (notas.length ? '<h3>Notas fiscais</h3><div id="int-notas"></div>' : '') +
                '<p class="text-muted" style="margin:.2em 0 .8em">Uma linha por equipamento recebido, com a etiqueta consumida do estoque. ' +
                'Confira o serial; ao dar OK a planilha do CSC Lançamentos é gerada e o chamado aberto no ServiceNow com o seu usuário.</p>' +
                '<div class="table-responsive"><table class="table table-sm" id="int-tab">' +
                    '<thead><tr><th style="width:110px">Item EBS</th><th>Descrição do item</th>' +
                    '<th style="width:120px">Plaqueta</th><th style="width:130px">Local da etiqueta</th>' +
                    '<th style="width:170px">Número de série</th><th style="width:110px">PO / linha</th><th style="width:90px">NF</th>' +
                    (editavel ? '<th style="width:40px"></th>' : '') + '</tr></thead>' +
                    '<tbody id="int-linhas"></tbody>' +
                '</table></div>' +
                (editavel ? '<div class="btn-row mt-2"><button type="button" id="int-add" class="btn btn-secondary btn-sm">+ Adicionar linha</button></div>' : '') +
                '<div class="btn-row mt-3">' +
                    (editavel ? '<button type="button" id="int-salvar" class="btn btn-secondary btn-sm">Salvar</button> ' +
                                '<button type="button" id="int-lancar" class="btn btn-primary btn-sm">OK — gerar planilha e abrir chamado</button> ' : '') +
                    (podeEditar && d.sn_pendente ? '<button type="button" id="int-reenviar" class="btn btn-primary btn-sm">Reenviar ao ServiceNow</button> ' : '') +
                    (d.planilha_arquivo ? '<button type="button" id="int-planilha" class="btn btn-secondary btn-sm">Baixar planilha</button> ' : '') +
                    (podeExportar ? '<button type="button" id="int-exportar" class="btn btn-secondary btn-sm">Exportar (antiga)</button> ' : '') +
                    '<button type="button" id="int-cancelar" class="btn btn-secondary btn-sm">Fechar</button>' +
                '</div>' +
                '<div id="int-erro" class="alert alert-danger mt-2" hidden></div>' +
                '<div id="int-msg" class="text-muted mt-2"></div>';

            if (notas.length) {
                var hn = document.getElementById('int-notas');
                notas.forEach(function (n) {
                    var linha = document.createElement('div');
                    linha.className = 'filter-grid mb-2';
                    linha.innerHTML =
                        '<div class="form-group"><label>NF</label><div style="padding:6px 0"><b>' + e(n.nf) + '</b>' +
                            (n.tem_pdf ? ' · <a href="' + S.apiUrl('/internalizacao/' + id + '/nota/' + encodeURIComponent(n.nf) + '/pdf') + '" target="_blank">PDF</a>' : ' · <span class="text-muted">sem PDF</span>') + '</div></div>' +
                        '<div class="form-group"><label>Vencimento</label><input type="date" class="form-control int-venc" value="' + e(n.vencimento || '') + '"' + (podeEditar ? '' : ' disabled') + '></div>' +
                        '<div class="form-group"><label>Origem</label><div style="padding:6px 0">' + e(n.origem_rotulo || '—') + (n.erro ? ' <span class="text-muted">· ' + e(n.erro) + '</span>' : '') + '</div></div>';
                    hn.appendChild(linha);
                    if (podeEditar) linha.querySelector('.int-venc').onchange = async function () {
                        try {
                            await S.api('/internalizacao/' + id + '/nota/' + encodeURIComponent(n.nf), { method: 'PATCH', body: { vencimento: this.value } });
                            S.toast('Vencimento da NF ' + n.nf + ' gravado.', 'success');
                        } catch (x) { S.toast(x.message, 'error'); }
                    };
                });
            }

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

            if (editavel) {
                document.getElementById('int-add').onclick = function () { addLinha(); };
                document.getElementById('int-salvar').onclick = function () { salvar(id, false); };
                document.getElementById('int-lancar').onclick = function () { lancar(id, false); };
            }
            if (document.getElementById('int-reenviar')) document.getElementById('int-reenviar').onclick = function () { lancar(id, true); };
            if (document.getElementById('int-planilha')) document.getElementById('int-planilha').onclick = function () {
                window.open(S.apiUrl('/internalizacao/' + id + '/planilha'), '_blank');
            };
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
            if (a.id) tr.dataset.id = a.id;
            var enviadoJa = !!(document.getElementById('int-reenviar') || document.getElementById('int-planilha')) && !document.getElementById('int-lancar');
            var ro = (podeEditar && !enviadoJa) ? '' : ' readonly';
            var plaqRo = (a.etiqueta_id || ro) ? ' readonly' : '';
            tr.innerHTML =
                '<td><input class="form-control int-ebs" value="' + e(a.ebs_item || '') + '"' + ro + '></td>' +
                '<td><input class="form-control int-desc" value="' + e(a.descricao || '') + '"' + ro + '></td>' +
                '<td><input class="form-control int-plaq" value="' + e(a.plaqueta || '') + '"' + plaqRo + '></td>' +
                '<td class="text-muted">' + e(a.etiqueta_local || '—') + '</td>' +
                '<td><input class="form-control int-serie" value="' + e(a.numero_serie || '') + '"' + ro + '></td>' +
                '<td class="text-muted">' + e(a.po || '—') + (a.linha != null ? ' / ' + e(String(a.linha)) : '') + '</td>' +
                '<td class="text-muted">' + e(a.nf || '—') + '</td>' +
                (!ro ? '<td><button type="button" class="btn btn-secondary btn-sm int-rem" title="Remover">&times;</button></td>' : '');
            if (!ro) tr.querySelector('.int-rem').onclick = function () { tr.remove(); };
            tb.appendChild(tr);
        }

        async function lancar(id, reenvio) {
            var erro = document.getElementById('int-erro');
            var msg = document.getElementById('int-msg');
            erro.hidden = true; msg.textContent = '';
            if (!reenvio) {
                var ativos = coletar();
                if (ativos.some(function (a) { return !a.plaqueta || !a.numero_serie; })) {
                    erro.hidden = false; erro.textContent = 'Toda linha precisa de plaqueta e número de série antes do OK.'; return;
                }
                if (!confirm('Gerar a planilha Cadastro de Ativos e abrir o chamado no ServiceNow com o seu usuário?\n\nDepois do OK os ativos não mudam mais aqui.')) return;
            }
            var b = document.getElementById(reenvio ? 'int-reenviar' : 'int-lancar');
            b.disabled = true;
            try {
                if (!reenvio) await S.api('/internalizacao/' + id, { method: 'PUT', body: JSON.stringify({ ativos: coletar(), concluir: false }) });
                var d = await S.api('/internalizacao/' + id + '/lancar' + (reenvio ? '/reenviar' : ''), { method: 'POST' });
                S.toast(d.sn_request_number ? 'Chamado ' + d.sn_request_number + ' aberto.' : 'Planilha gerada; chamado pendente.', d.sn_request_number ? 'success' : 'warning');
                carregar();
                abrir(id);
                if (d.aviso) S.toast(d.aviso, 'warning');
            } catch (x) {
                erro.hidden = false; erro.textContent = x.message;
                b.disabled = false;
            }
        }

        function coletar() {
            var ativos = [];
            Array.prototype.forEach.call(document.querySelectorAll('.int-linha'), function (tr) {
                var o = {
                    id: tr.dataset.id ? parseInt(tr.dataset.id, 10) : null,
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

        async function conferirSn() {
            var b = document.getElementById('int-conferir-sn');
            b.disabled = true;
            try {
                var d = await S.api('/internalizacao/catalogo-sn/conferir');
                var corpo = S.el('div');
                var h = '<p><b>' + e(d.item.name || d.item.sys_id) + '</b> · usuário: ' + e(d.usuario.user_name) + ' · impactado: ' + e(d.impacto.email || 'NÃO ENCONTRADO') + '</p>';
                if (d.faltantes.length) h += '<div class="alert alert-warning">Variáveis que o item não tem: ' + e(d.faltantes.join(', ')) + '</div>';
                if (d.erro) h += '<div class="alert alert-danger">' + e(d.erro) + '</div>';
                h += '<div class="table-wrapper"><table class="data-table"><thead><tr><th>Variável</th><th>Rótulo</th><th>Tipo</th><th>Obrig.</th><th>Opções</th><th>Enviaríamos</th></tr></thead><tbody>';
                function linha(v, pref) {
                    h += '<tr><td>' + e(pref + v.name) + '</td><td>' + e(v.label) + '</td><td>' + e(v.type) + (v.read_only ? ' (só leitura)' : '') + '</td><td>' + (v.mandatory ? 'sim' : '') + '</td><td>' +
                        e((v.choices || []).map(function (c) { return c.label; }).join(', ')) + '</td><td>' + e(d.exemplo[v.name] != null ? String(d.exemplo[v.name]) : '') + '</td></tr>';
                    (v.children || []).forEach(function (f) { linha(f, pref + v.name + '.'); });
                }
                d.item.variables.forEach(function (v) { linha(v, ''); });
                h += '</tbody></table></div>';
                corpo.innerHTML = h;
                S.openModal('Formulário do ServiceNow', corpo, [S.el('button', { className: 'btn btn-secondary', textContent: 'Fechar', onClick: S.closeModal })]);
            } catch (x) { S.toast(x.message, 'error'); }
            finally { b.disabled = false; }
        }
        if (podeEditar) document.getElementById('int-conferir-sn').onclick = conferirSn;
        S.api('/internalizacao/nfe/certificados').then(function (d) {
            document.getElementById('int-certs').innerHTML = 'Certificados NF-e: ' + d.certificados.map(function (c) {
                if (c.bu === 'Youcom') return 'Youcom (NF por arquivo)';
                if (!c.configurado) return e(c.bu) + ' <span class="badge badge-warning">não configurado</span>';
                return e(c.bu) + ' <span class="badge badge-' + (c.vencido ? 'danger' : 'success') + '">' + (c.vencido ? 'vencido' : 'vence ' + e(fmtDataHora(c.valido_ate).split(',')[0])) + '</span>';
            }).join(' · ');
        }).catch(function () {});

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



/* Data e hora vindas do servidor em ISO com fuso. O `S.formatDate` do
   portal é só para data (cola "T00:00:00" no fim), e com um instante
   completo devolve "Invalid Date". */
function fmtDataHora(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric',
                                       hour: '2-digit', minute: '2-digit' });
}


/* ── Cadastro de Etiquetas ──────────────────────────────────────────
   O estoque físico de plaquetas de patrimônio, cadastrado ANTES de o
   equipamento chegar, com o local em que cada lote está guardado. Quando
   um recebimento vira lançamento, o portal consome daqui na ordem de
   cadastro — a tela precisa dizer quantas há, e onde.
   ─────────────────────────────────────────────────────────────────── */
async function telaEtiquetas(c, S) {
    var e = S.esc;
    var u = S.user() || {};
    var pm = (u.permission_map || {}).internalizacao || {};
    var podeCriar = !!(u.is_admin || pm.can_create);
    var podeEditar = !!(u.is_admin || pm.can_edit);
    var podeExcluir = !!(u.is_admin || pm.can_admin);

    c.innerHTML =
        '<h1 class="page-title">Cadastro de Etiquetas</h1>' +
        '<p class="text-muted">As etiquetas de patrimônio que estão em estoque e onde ' +
            'estão guardadas. O lançamento consome daqui, uma por equipamento, na ' +
            'ordem em que foram cadastradas.</p>' +
        '<div id="etq-totais" class="mb-3"></div>' +
        (podeCriar ?
        '<div class="card mb-3"><div class="card-header">Cadastrar etiquetas</div>' +
        '<div class="card-body">' +
            '<div class="form-grid cols-2">' +
                '<div class="form-group"><label for="etq-codigos">Lista de etiquetas</label>' +
                    '<textarea id="etq-codigos" class="form-control" rows="5" ' +
                    'placeholder="Uma por linha, ou separadas por vírgula"></textarea></div>' +
                '<div>' +
                    '<div class="form-group"><label>Ou uma faixa numerada</label>' +
                        '<div style="display:flex;gap:8px;flex-wrap:wrap">' +
                            '<input id="etq-prefixo" class="form-control" placeholder="prefixo (opcional)" style="flex:1;min-width:110px">' +
                            '<input id="etq-de" class="form-control" placeholder="de: 000100" inputmode="numeric" style="flex:1;min-width:110px">' +
                            '<input id="etq-ate" class="form-control" placeholder="até: 000250" inputmode="numeric" style="flex:1;min-width:110px">' +
                        '</div>' +
                        '<p class="text-muted" style="margin:.4em 0 0">Os zeros à esquerda do número inicial são mantidos: ' +
                        'de 000100 até 000250 gera 000100, 000101…</p></div>' +
                    '<div class="form-group"><label for="etq-local">Local de armazenamento *</label>' +
                        '<input id="etq-local" class="form-control" list="etq-locais" placeholder="ex.: Armário 3, gaveta 2"></div>' +
                    '<datalist id="etq-locais"></datalist>' +
                '</div>' +
            '</div>' +
            '<div class="btn-row mt-2"><button id="etq-cadastrar" class="btn btn-primary" type="button">Cadastrar</button></div>' +
            '<div id="etq-resultado" class="mt-2"></div>' +
        '</div></div>' : '') +
        '<div class="card"><div class="card-header">Estoque de etiquetas</div><div class="card-body">' +
            '<div class="filter-grid">' +
                '<div class="form-group"><label for="etq-f-sit">Situação</label>' +
                    '<select id="etq-f-sit" class="form-control">' +
                    '<option value="DISPONIVEL">Disponíveis</option>' +
                    '<option value="CONSUMIDA">Consumidas</option>' +
                    '<option value="CANCELADA">Canceladas</option>' +
                    '<option value="">Todas</option></select></div>' +
                '<div class="form-group"><label for="etq-f-local">Local</label>' +
                    '<select id="etq-f-local" class="form-control"><option value="">Todos</option></select></div>' +
                '<div class="form-group"><label for="etq-f-busca">Buscar etiqueta</label>' +
                    '<input id="etq-f-busca" class="form-control" placeholder="digite e Enter"></div>' +
            '</div>' +
            (podeEditar ?
            '<div class="btn-row mb-2" style="align-items:center;flex-wrap:wrap">' +
                '<input id="etq-mover-local" class="form-control" list="etq-locais" placeholder="novo local" style="max-width:260px">' +
                '<button id="etq-mover" class="btn btn-secondary btn-sm" type="button">Mover marcadas</button>' +
                '<button id="etq-todas" class="btn btn-secondary btn-sm" type="button">Marcar todas</button>' +
            '</div>' : '') +
            '<div id="etq-lista"></div>' +
        '</div></div>';

    function badge(sit) {
        var cls = sit === 'DISPONIVEL' ? 'success' : sit === 'CONSUMIDA' ? 'info' : 'warning';
        var rot = sit === 'DISPONIVEL' ? 'Disponível' : sit === 'CONSUMIDA' ? 'Consumida' : 'Cancelada';
        return '<span class="badge badge-' + cls + '">' + rot + '</span>';
    }

    function locais(lista) {
        var dl = document.getElementById('etq-locais');
        var sel = document.getElementById('etq-f-local');
        var atual = sel.value;
        dl.innerHTML = lista.map(function (l) { return '<option value="' + e(l) + '">'; }).join('');
        sel.innerHTML = '<option value="">Todos</option>' + lista.map(function (l) {
            return '<option value="' + e(l) + '"' + (l === atual ? ' selected' : '') + '>' + e(l) + '</option>';
        }).join('');
    }

    async function carregar() {
        var alvo = document.getElementById('etq-lista');
        alvo.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div>';
        var qs = ['situacao=' + encodeURIComponent(document.getElementById('etq-f-sit').value),
                  'local=' + encodeURIComponent(document.getElementById('etq-f-local').value),
                  'busca=' + encodeURIComponent(document.getElementById('etq-f-busca').value.trim())];
        try {
            var d = await S.api('/internalizacao/cadastro/etiquetas?' + qs.join('&'));
            var t = d.totais || {};
            document.getElementById('etq-totais').innerHTML =
                '<span class="badge badge-success">' + (t.DISPONIVEL || 0) + ' disponíveis</span> ' +
                '<span class="badge badge-info">' + (t.CONSUMIDA || 0) + ' consumidas</span> ' +
                '<span class="badge badge-warning">' + (t.CANCELADA || 0) + ' canceladas</span>';
            locais(d.locais || []);
            alvo.innerHTML = '';
            if (!d.total) {
                alvo.appendChild(S.el('p', { className: 'text-muted',
                    textContent: 'Nenhuma etiqueta nesta condição.' }));
                return;
            }
            var cols = [];
            if (podeEditar) cols.push({ key: 'id', label: '', html: true, render: function (v, r) {
                return r.situacao === 'DISPONIVEL'
                    ? '<input class="etq-alvo" type="checkbox" value="' + e(String(v)) + '">' : '';
            } });
            cols = cols.concat([
                { key: 'codigo', label: 'Etiqueta' },
                { key: 'local', label: 'Local' },
                { key: 'situacao', label: 'Situação', html: true, render: badge },
                { key: 'criado_em', label: 'Cadastrada em', render: function (v, r) {
                    return (v ? fmtDataHora(v) : '—') + (r.criado_por ? ' · ' + r.criado_por : '');
                } },
                { key: 'consumida_em', label: 'Consumida em', render: function (v, r) {
                    if (r.situacao === 'CANCELADA') return r.cancelada_motivo ? 'cancelada: ' + r.cancelada_motivo : 'cancelada';
                    return v ? fmtDataHora(v) : '—';
                } }
            ]);
            if (podeEditar || podeExcluir) cols.push({ key: 'id', label: '', html: true, render: function (v, r) {
                if (r.situacao !== 'DISPONIVEL') return '';
                var h = '';
                if (podeEditar) h += '<button class="btn btn-secondary btn-sm etq-cancelar" data-id="' + e(String(v)) + '" data-cod="' + e(r.codigo) + '">Cancelar</button> ';
                if (podeExcluir) h += '<button class="btn btn-danger btn-sm etq-excluir" data-id="' + e(String(v)) + '" data-cod="' + e(r.codigo) + '">Excluir</button>';
                return h;
            } });
            alvo.appendChild(S.table(cols, d.itens));
            alvo.querySelectorAll('.etq-cancelar').forEach(function (b) {
                b.onclick = function () { cancelar(b.dataset.id, b.dataset.cod); };
            });
            alvo.querySelectorAll('.etq-excluir').forEach(function (b) {
                b.onclick = function () { excluir(b.dataset.id, b.dataset.cod); };
            });
        } catch (x) {
            alvo.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
        }
    }

    async function cancelar(id, cod) {
        var motivo = window.prompt('Cancelar a etiqueta ' + cod + '.\n\nPor quê? (perdida, danificada…)');
        if (motivo === null) return;
        try {
            await S.api('/internalizacao/cadastro/etiquetas/' + id + '/cancelar',
                        { method: 'POST', body: { motivo: motivo } });
            S.toast('Etiqueta ' + cod + ' cancelada.', 'success');
            carregar();
        } catch (x) { S.toast(x.message, 'error'); }
    }

    async function excluir(id, cod) {
        if (!window.confirm('Apagar a etiqueta ' + cod + ' do cadastro?\n\nUse só para engano de digitação. Etiqueta perdida ou danificada se cancela, não se apaga.')) return;
        try {
            await S.api('/internalizacao/cadastro/etiquetas/' + id, { method: 'DELETE' });
            S.toast('Etiqueta ' + cod + ' apagada.', 'success');
            carregar();
        } catch (x) { S.toast(x.message, 'error'); }
    }

    if (podeCriar) {
        document.getElementById('etq-cadastrar').onclick = async function () {
            var b = this, txt = b.textContent;
            var res = document.getElementById('etq-resultado');
            var corpo = {
                codigos: document.getElementById('etq-codigos').value,
                prefixo: document.getElementById('etq-prefixo').value.trim(),
                de: document.getElementById('etq-de').value.trim(),
                ate: document.getElementById('etq-ate').value.trim(),
                local: document.getElementById('etq-local').value.trim()
            };
            if (!corpo.local) {
                document.getElementById('etq-local').focus();
                return S.toast('Informe o local em que as etiquetas estão guardadas.', 'warning');
            }
            if (!corpo.codigos.trim() && !corpo.de && !corpo.ate) {
                return S.toast('Cole a lista de etiquetas ou preencha a faixa.', 'warning');
            }
            b.disabled = true; b.textContent = 'Cadastrando…';
            try {
                var d = await S.api('/internalizacao/cadastro/etiquetas', { method: 'POST', body: corpo });
                var partes = [d.criadas + ' etiqueta(s) cadastrada(s) em "' + e(d.local) + '"'];
                if (d.repetidas.length) partes.push(d.repetidas.length + ' já existiam: ' + e(d.repetidas.slice(0, 20).join(', ')) + (d.repetidas.length > 20 ? '…' : ''));
                if (d.invalidas.length) partes.push(d.invalidas.length + ' inválida(s): ' + e(d.invalidas.slice(0, 20).join(', ')) + (d.invalidas.length > 20 ? '…' : ''));
                res.innerHTML = '<div class="alert alert-' + (d.criadas ? 'success' : 'warning') + '">' + partes.join('<br>') + '</div>';
                if (d.criadas) {
                    document.getElementById('etq-codigos').value = '';
                    document.getElementById('etq-de').value = '';
                    document.getElementById('etq-ate').value = '';
                    S.toast(d.criadas + ' etiqueta(s) cadastrada(s).', 'success');
                }
                carregar();
            } catch (x) {
                res.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            } finally { b.disabled = false; b.textContent = txt; }
        };
    }
    if (podeEditar) {
        document.getElementById('etq-todas').onclick = function () {
            var todas = c.querySelectorAll('.etq-alvo');
            var marcar = Array.prototype.some.call(todas, function (x) { return !x.checked; });
            todas.forEach(function (x) { x.checked = marcar; });
        };
        document.getElementById('etq-mover').onclick = async function () {
            var local = document.getElementById('etq-mover-local').value.trim();
            var ids = [];
            c.querySelectorAll('.etq-alvo:checked').forEach(function (ch) { ids.push(parseInt(ch.value, 10)); });
            if (!local) return S.toast('Informe o novo local.', 'warning');
            if (!ids.length) return S.toast('Marque as etiquetas que mudaram de lugar.', 'warning');
            try {
                var d = await S.api('/internalizacao/cadastro/etiquetas/mover',
                                    { method: 'POST', body: { ids: ids, local: local } });
                S.toast(d.movidas + ' etiqueta(s) agora em "' + local + '".', 'success');
                carregar();
            } catch (x) { S.toast(x.message, 'error'); }
        };
    }
    document.getElementById('etq-f-sit').onchange = carregar;
    document.getElementById('etq-f-local').onchange = carregar;
    document.getElementById('etq-f-busca').onkeydown = function (ev) { if (ev.key === 'Enter') carregar(); };
    carregar();
}


/* ── Itens Imobilizados ─────────────────────────────────────────────
   A lista do que é patrimônio. Um pedido traz também o que não é — o cabo
   comprado junto com a impressora — e esses itens são pagos, mas não
   ganham etiqueta nem entram no lançamento. O recebimento só conta, e só
   consome etiqueta, para os itens que estão aqui.
   ─────────────────────────────────────────────────────────────────── */
async function telaImobilizados(c, S) {
    var e = S.esc;
    var u = S.user() || {};
    var pm = (u.permission_map || {}).internalizacao || {};
    var podeCriar = !!(u.is_admin || pm.can_create);
    var podeEditar = !!(u.is_admin || pm.can_edit);
    var podeExcluir = !!(u.is_admin || pm.can_admin);

    c.innerHTML =
        '<h1 class="page-title">Itens Imobilizados</h1>' +
        '<p class="text-muted">Os itens do EBS que viram patrimônio quando chegam. O que não ' +
            'está nesta lista (cabo, fonte, acessório) é pago junto com a nota, mas não ' +
            'ganha etiqueta nem vai para o lançamento.</p>' +
        (podeCriar ?
        '<div class="card mb-3"><div class="card-header">Incluir na lista</div><div class="card-body">' +
            '<div class="form-grid cols-2">' +
                '<div>' +
                    '<div class="form-group"><label for="imb-item">Item do EBS *</label>' +
                        '<input id="imb-item" class="form-control" placeholder="código do item, como na PO"></div>' +
                    '<div class="form-group"><label for="imb-desc">Descrição</label>' +
                        '<input id="imb-desc" class="form-control" placeholder="como aparece no pedido"></div>' +
                    '<div class="btn-row"><button id="imb-add" class="btn btn-primary btn-sm" type="button">Incluir</button></div>' +
                '</div>' +
                '<div class="form-group"><label for="imb-texto">Ou cole vários: código e descrição por linha</label>' +
                    '<textarea id="imb-texto" class="form-control" rows="5" placeholder="347191 ZEBRA IMPRESSORA INDUSTRIAL ZT231\n412000 DESKTOP POSITIVO MASTER"></textarea>' +
                    '<div class="btn-row mt-2"><button id="imb-colar" class="btn btn-secondary btn-sm" type="button">Incluir a lista</button></div>' +
                '</div>' +
            '</div>' +
            '<div id="imb-resultado" class="mt-2"></div>' +
        '</div></div>' : '') +
        '<div class="card"><div class="card-header">Lista</div><div class="card-body">' +
            '<div class="filter-grid"><div class="form-group"><label for="imb-busca">Buscar</label>' +
                '<input id="imb-busca" class="form-control" placeholder="item ou descrição, e Enter"></div></div>' +
            '<div id="imb-lista"></div>' +
        '</div></div>';

    async function carregar() {
        var alvo = document.getElementById('imb-lista');
        alvo.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div>';
        try {
            var d = await S.api('/internalizacao/cadastro/itens-imobilizados?busca=' +
                                encodeURIComponent(document.getElementById('imb-busca').value.trim()));
            alvo.innerHTML = '';
            if (!d.total) {
                alvo.appendChild(S.el('p', { className: 'text-muted',
                    textContent: 'Nenhum item na lista. Enquanto ela estiver vazia, nenhum recebimento de fornecedor consegue seguir para o lançamento.' }));
                return;
            }
            var cols = [
                { key: 'item_ebs', label: 'Item EBS' },
                { key: 'descricao', label: 'Descrição', render: function (v) { return v || '—'; } },
                { key: 'criado_em', label: 'Incluído em', render: function (v, r) {
                    return (v ? fmtDataHora(v) : '—') + (r.criado_por ? ' · ' + r.criado_por : '');
                } }
            ];
            if (podeEditar || podeExcluir) cols.push({ key: 'id', label: '', html: true, render: function (v, r) {
                var h = '';
                if (podeEditar) h += '<button class="btn btn-secondary btn-sm imb-editar" data-id="' + e(String(v)) + '">Editar</button> ';
                if (podeExcluir) h += '<button class="btn btn-danger btn-sm imb-excluir" data-id="' + e(String(v)) + '" data-item="' + e(r.item_ebs) + '">Remover</button>';
                return h;
            } });
            alvo.appendChild(S.table(cols, d.itens));
            var porId = {};
            d.itens.forEach(function (i) { porId[i.id] = i; });
            alvo.querySelectorAll('.imb-editar').forEach(function (b) {
                b.onclick = function () { editar(porId[b.dataset.id]); };
            });
            alvo.querySelectorAll('.imb-excluir').forEach(function (b) {
                b.onclick = function () { excluir(b.dataset.id, b.dataset.item); };
            });
        } catch (x) {
            alvo.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
        }
    }

    async function incluir(corpo) {
        var res = document.getElementById('imb-resultado');
        try {
            var d = await S.api('/internalizacao/cadastro/itens-imobilizados', { method: 'POST', body: corpo });
            res.innerHTML = '<div class="alert alert-success">' + d.criados + ' incluído(s)' +
                (d.atualizados ? ', ' + d.atualizados + ' com a descrição atualizada' : '') + '.</div>';
            S.toast(d.criados + ' item(ns) incluído(s).', 'success');
            carregar();
            return true;
        } catch (x) {
            res.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            return false;
        }
    }

    function editar(item) {
        if (!item) return;
        var corpo = S.el('div', { className: 'form-grid' });
        corpo.innerHTML =
            '<div class="form-group"><label for="imb-e-item">Item do EBS</label>' +
                '<input id="imb-e-item" class="form-control" value="' + e(item.item_ebs) + '"></div>' +
            '<div class="form-group"><label for="imb-e-desc">Descrição</label>' +
                '<input id="imb-e-desc" class="form-control" value="' + e(item.descricao) + '"></div>';
        var salvar = S.el('button', { className: 'btn btn-primary', textContent: 'Salvar' });
        var fechar = S.el('button', { className: 'btn btn-secondary', textContent: 'Cancelar', onClick: S.closeModal });
        salvar.onclick = async function () {
            try {
                await S.api('/internalizacao/cadastro/itens-imobilizados/' + item.id, {
                    method: 'PATCH',
                    body: { item_ebs: document.getElementById('imb-e-item').value,
                            descricao: document.getElementById('imb-e-desc').value }
                });
                S.closeModal();
                S.toast('Item atualizado.', 'success');
                carregar();
            } catch (x) { S.toast(x.message, 'error'); }
        };
        S.openModal('Editar item imobilizado', corpo, [fechar, salvar]);
    }

    async function excluir(id, item) {
        if (!window.confirm('Remover o item ' + item + ' da lista?\n\nA partir daqui ele chega como acessório: sem etiqueta e sem lançamento.')) return;
        try {
            await S.api('/internalizacao/cadastro/itens-imobilizados/' + id, { method: 'DELETE' });
            S.toast('Item ' + item + ' removido da lista.', 'success');
            carregar();
        } catch (x) { S.toast(x.message, 'error'); }
    }

    if (podeCriar) {
        document.getElementById('imb-add').onclick = async function () {
            var item = document.getElementById('imb-item').value.trim();
            if (!item) { document.getElementById('imb-item').focus(); return S.toast('Informe o item do EBS.', 'warning'); }
            var ok = await incluir({ itens: [{ item_ebs: item, descricao: document.getElementById('imb-desc').value.trim() }] });
            if (ok) {
                document.getElementById('imb-item').value = '';
                document.getElementById('imb-desc').value = '';
                document.getElementById('imb-item').focus();
            }
        };
        document.getElementById('imb-colar').onclick = async function () {
            var texto = document.getElementById('imb-texto').value;
            if (!texto.trim()) return S.toast('Cole ao menos uma linha.', 'warning');
            var ok = await incluir({ texto: texto });
            if (ok) document.getElementById('imb-texto').value = '';
        };
        ['imb-item', 'imb-desc'].forEach(function (id) {
            document.getElementById(id).onkeydown = function (ev) {
                if (ev.key === 'Enter') document.getElementById('imb-add').click();
            };
        });
    }
    document.getElementById('imb-busca').onkeydown = function (ev) { if (ev.key === 'Enter') carregar(); };
    carregar();
}
