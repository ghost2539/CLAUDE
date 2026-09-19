/* ================================================================
   Module: Recebimento (Receiving — all sub-tabs)
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.recebimento = {

    render(container, sub) {
        var S = window.SPARE;
        var TAB_LIST = [
            ['novo',      'Novo Recebimento'],
            ['base',      'Base de Recebimentos'],
            ['dashboard', 'Dashboard'],
            ['lotes',     'Lotes'],
            ['modelos',   'Cadastro de modelos']
        ];
        sub = sub || 'novo';
        S.tabs(TAB_LIST, sub, 'recebimento');

        var handlers = {
            novo:      renderNovo,
            base:      renderBase,
            dashboard: renderDashboard,
            lotes:     renderLotes,
            modelos:   renderModelos
        };
        (handlers[sub] || renderNovo)(container, S);
    }

};

/* ── Novo Recebimento (Sessão Temporária) ──────────────────────── */
function renderNovo(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Novo Recebimento</h1>' +
        // De onde o ativo está chegando. Muda o que se pede: reversa volta
        // da loja e já existe no EBS, então basta bipar; fornecedor é
        // compra nova, que não existe em lugar nenhum e é digitada.
        '<div class="card mb-3">' +
            '<div class="card-header">Origem da entrada</div>' +
            '<div class="card-body">' +
                '<div id="rec-origem" class="btn-row" role="group" aria-label="Origem da entrada">' +
                    '<button type="button" class="btn" data-origem="REVERSA">Reversa</button>' +
                    '<button type="button" class="btn" data-origem="FORNECEDOR">Fornecedores</button>' +
                '</div>' +
                '<p id="rec-origem-nota" class="text-muted mt-2"></p>' +
            '</div>' +
        '</div>' +
        '<div id="rec-entrada"></div>' +
        // Onde o lote foi guardado. Vai junto para o ServiceNow: sem o
        // local, o ativo ficaria "em estoque" sem dizer onde.
        '<div class="card mb-3">' +
            '<div class="card-header">Onde os ativos foram guardados</div>' +
            '<div class="card-body">' +
                '<label for="rec-espaco">Espaço e Corredor <span style="color:var(--sp-alerta)">*</span></label>' +
                '<input id="rec-espaco" class="form-control" style="max-width:320px" ' +
                    'list="rec-corredores" autocomplete="off" placeholder="Ex.: A-12">' +
                '<datalist id="rec-corredores"></datalist>' +
            '</div>' +
        '</div>' +
        '<div class="btn-row mb-3">' +
            '<button id="btn-select-ready" class="btn btn-secondary">Selecionar Prontos</button>' +
            '<button id="btn-submit-base" class="btn btn-primary">Enviar para Base</button>' +
            '<button id="btn-discard" class="btn btn-danger">Descartar Sessão</button>' +
            '<span id="session-count" class="text-muted" style="margin-left:12px"></span>' +
        '</div>' +
        '<div class="card">' +
            '<div class="card-header">Sessão Temporária</div>' +
            '<div id="session-list" class="card-body"></div>' +
        '</div>';

    var CACHE_KEY = 'spare_recebimento_session';
    var FORNECEDOR = 'FORNECEDOR';
    var REVERSA = 'REVERSA';
    var origem = REVERSA;

    // Destino de entrada por ativo: o Recebimento é a porta do Spare e é
    // aqui que se decide venda direta ou triagem. A subcategoria manda o
    // ativo para o backlog da bancada certa, por isso vem de lista.
    var subcategorias = [];

    function saveCache() {
        try {
            localStorage.setItem(CACHE_KEY, JSON.stringify({ origem: origem, itens: sessionItems }));
        } catch (_) {}
    }

    function loadCache() {
        try {
            var raw = localStorage.getItem(CACHE_KEY);
            if (!raw) return [];
            var guardado = JSON.parse(raw);
            // Sessão gravada antes de existir origem era só a lista.
            if (Array.isArray(guardado)) return guardado;
            origem = guardado.origem === FORNECEDOR ? FORNECEDOR : REVERSA;
            return guardado.itens || [];
        } catch (_) { return []; }
    }

    var sessionItems = loadCache();

    function situacaoBadge(text) {
        var map = {
            'PRONTO PARA ENVIO': 'success',
            'EDITADO':           'gold',
            'REQUER ATUAÇÃO':    'danger'
        };
        var cls = map[text] || 'default';
        return '<span class="badge badge-' + cls + '">' + S.esc(text || '—') + '</span>';
    }

    function drawSession() {
        var selecao = { key: 'sel', label: '', render: function (_, r, i) {
            var x = S.el('input', { type: 'checkbox' });
            x.checked = !!r._selected;
            x.onchange = function () { r._selected = x.checked; };
            return x;
        }};
        // O que o fornecedor traz é o que foi digitado; imobilizado,
        // etiqueta e empresa são do EBS e só existem na reversa.
        var identificacao = origem === FORNECEDOR ? [
            { key: 'descricao',    label: 'Descrição do item' },
            { key: 'numero_serie', label: 'Serial Number' },
            { key: 'po',           label: 'PO' },
            { key: 'nf',           label: 'NF' }
        ] : [
            { key: 'imobilizado', label: 'Imobilizado' },
            { key: 'etiqueta',    label: 'Etiqueta' },
            { key: 'numero_serie',label: 'Nº Série' },
            { key: 'empresa',     label: 'Empresa' },
            { key: 'descricao',   label: 'Descrição' },
            { key: 'categoria',   label: 'Categoria', html: true, render: function (v) {
                if (!v || v === 'NÃO CLASSIFICADA') {
                    return S.esc(v || 'NÃO CLASSIFICADA') +
                        ' <span class="badge badge-danger">Necessário ajuste</span>';
                }
                return S.esc(v);
            }},
            { key: 'modelo',      label: 'Modelo' }
        ];
        var cols = [selecao, { key: 'hora', label: 'Hora' }].concat(identificacao).concat([
            { key: 'destino_entrada', label: 'Destino', render: function (_, r) {
                var sel = S.el('select', { className: 'form-control form-control-sm' });
                sel.innerHTML = '<option value="TRIAGEM">Triagem</option>' +
                                '<option value="VENDA">Venda direta</option>';
                sel.value = r.destino_entrada || 'TRIAGEM';
                sel.onchange = function () {
                    r.destino_entrada = sel.value;
                    if (sel.value === 'VENDA') r.subcategoria = '';
                    drawSession();
                };
                return sel;
            }},
            { key: 'subcategoria', label: 'Subcategoria', render: function (_, r) {
                if ((r.destino_entrada || 'TRIAGEM') === 'VENDA') {
                    return S.el('span', { className: 'text-muted', textContent: '—' });
                }
                var sel = S.el('select', { className: 'form-control form-control-sm' });
                sel.innerHTML = '<option value="">Selecione…</option>' +
                    subcategorias.map(function (x) {
                        return '<option value="' + S.esc(x.nome) + '">' + S.esc(x.nome) + '</option>';
                    }).join('');
                sel.value = r.subcategoria || '';
                sel.onchange = function () { r.subcategoria = sel.value; saveCache(); };
                return sel;
            }},
            { key: 'situacao',    label: 'Situação', html: true, render: function (v, row) {
                var out = situacaoBadge(v);
                if (row._duplicadoLocal) {
                    out += ' <span class="badge badge-danger" title="Já existe na base local">DUPLICADO</span>';
                }
                return out;
            }},
            { key: '_actions',    label: 'Ações', render: function (_, r, i) {
                var w = S.el('div', { className: 'btn-row' });
                var editBtn = S.el('button', { className: 'btn btn-sm btn-outline', textContent: 'Editar' });
                editBtn.onclick = function () { openEditModal(r, i); };
                var removeBtn = S.el('button', { className: 'btn btn-sm btn-danger', textContent: 'Remover' });
                removeBtn.onclick = function () {
                    sessionItems.splice(i, 1);
                    drawSession();
                };
                w.appendChild(editBtn);
                if (r._hasDuplicates) {
                    var selBtn = S.el('button', { className: 'btn btn-sm btn-secondary', textContent: 'Selecionar' });
                    selBtn.onclick = function () { openDuplicateModal(r, i); };
                    w.appendChild(selBtn);
                }
                w.appendChild(removeBtn);
                return w;
            }}
        ]);
        var el = document.getElementById('session-list');
        el.innerHTML = '';
        el.appendChild(S.table(cols, sessionItems));
        var cnt = document.getElementById('session-count');
        var ready = sessionItems.filter(function (x) { return x.situacao === 'PRONTO PARA ENVIO'; }).length;
        cnt.textContent = sessionItems.length + ' ativo(s) na sessão | ' + ready + ' pronto(s) para envio';
        saveCache();
    }

    function openEditModal(item, idx) {
        var f = S.el('div');
        if (item._manual) {
            var info = S.el('div', { className: 'alert alert-warning' });
            info.innerHTML = 'Código <strong>' + S.esc(item.pesquisado || '') + '</strong> não localizado no EBS. ' +
                'Categoria e Modelo são obrigatórios. Informe pelo menos o Nº de Série ' +
                '<u>ou</u> o Imobilizado.';
            f.appendChild(info);
        }
        var fields = [
            ['Categoria',   'edit-cat',    item.categoria,    true],
            ['Modelo',      'edit-model',  item.modelo,       true],
            ['Empresa',     'edit-company',item.empresa,      false],
            ['Nº Série',    'edit-serial', item.numero_serie, false],
            ['Imobilizado', 'edit-asset',  item.imobilizado,  false]
        ];
        fields.forEach(function (x) {
            var g = S.el('div', { className: 'form-group' });
            g.innerHTML = '<label>' + S.esc(x[0]) + (x[3] ? ' <span style="color:var(--sp-alerta)">*</span>' : '') + '</label>' +
                '<input id="' + x[1] + '" class="form-control" value="' + S.esc(x[2] || '') + '">';
            f.appendChild(g);
        });
        var chkGroup = S.el('div', { className: 'form-group mt-2' });
        chkGroup.innerHTML = '<label style="display:flex;align-items:center;gap:8px;cursor:pointer">' +
            '<input type="checkbox" id="edit-ready" ' + (item.situacao === 'PRONTO PARA ENVIO' ? 'checked' : '') + '>' +
            ' Pronto para envio</label>';
        f.appendChild(chkGroup);
        var errEl = S.el('div', { className: 'mt-2', style: 'color:var(--sp-alerta);font-size:.85rem' });
        f.appendChild(errEl);

        var saveBtn = S.el('button', { className: 'btn btn-primary', textContent: 'Salvar' });
        saveBtn.onclick = function () {
            var cat    = document.getElementById('edit-cat').value.trim();
            var modelo = document.getElementById('edit-model').value.trim();
            var serial = document.getElementById('edit-serial').value.trim();
            var asset  = document.getElementById('edit-asset').value.trim();

            // Validação: categoria e modelo obrigatórios; ao menos um entre
            // Nº de série e Imobilizado.
            if (!cat || cat === 'NÃO CLASSIFICADA') {
                errEl.textContent = 'Informe a Categoria.'; return;
            }
            if (!modelo) { errEl.textContent = 'Informe o Modelo.'; return; }
            if (!serial && !asset) {
                errEl.textContent = 'Informe o Nº de Série ou o Imobilizado.'; return;
            }
            errEl.textContent = '';

            item.categoria    = cat;
            item.modelo       = modelo;
            item.empresa      = document.getElementById('edit-company').value.trim() || item.empresa;
            item.numero_serie = serial;
            item.imobilizado  = asset;
            item.ativo        = asset;
            item.asset_id     = asset;
            var ready = document.getElementById('edit-ready').checked;
            item.situacao = ready ? 'PRONTO PARA ENVIO' : 'EDITADO';
            S.closeModal();
            drawSession();
        };
        S.openModal('Editar Ativo', f, [saveBtn]);
    }

    function openDuplicateModal(item, idx) {
        var dups = item._duplicates || [];
        if (!dups.length) return;
        var f = S.el('div');
        f.innerHTML = '<p class="text-muted">Foram encontrados múltiplos registros para "' +
            S.esc(item.pesquisado) + '". Selecione o correto:</p>';
        var list = S.el('div');
        dups.forEach(function (dup, di) {
            var card = S.el('div', {
                className: 'card mb-2',
                style: 'cursor:pointer;border:2px solid transparent;padding:12px'
            });
            card.innerHTML =
                '<strong>' + S.esc(dup.empresa || 'N/D') + '</strong> — ' +
                S.esc(dup.descricao || '') + '<br>' +
                '<small>Imobilizado: ' + S.esc(dup.imobilizado || dup.ativo || '') +
                ' | Etiqueta: ' + S.esc(dup.etiqueta || '') +
                ' | Série: ' + S.esc(dup.numero_serie || '') + '</small>';
            card.onclick = function () {
                Object.keys(dup).forEach(function (k) {
                    if (k[0] !== '_') item[k] = dup[k];
                });
                item.situacao = (item.categoria && item.categoria !== 'NÃO CLASSIFICADA')
                    ? 'PRONTO PARA ENVIO' : 'EDITADO';
                item._hasDuplicates = false;
                S.closeModal();
                drawSession();
                S.toast('Ativo selecionado: ' + (dup.empresa || '') + ' — ' + (dup.imobilizado || ''), 'success');
            };
            list.appendChild(card);
        });
        f.appendChild(list);
        S.openModal('Selecionar Ativo', f, []);
    }

    async function aoBipar(e) {
        if (e.key !== 'Enter') return;
        var v = e.target.value.trim();
        if (!v) return;
        e.target.disabled = true;
        var fb = document.getElementById('scan-feedback');
        fb.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Consultando EBS...</div>';
        try {
            var d = await S.api('/recebimento/preview', {
                method: 'POST',
                body: { identificador: v }
            });
            if (!d.encontrado) {
                // Não encontrado no EBS: permitir seguir com cadastro manual.
                var manual = {
                    hora: new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }),
                    pesquisado: v,
                    empresa: '',
                    imobilizado: '',
                    ativo: '',
                    asset_id: '',
                    etiqueta: v,
                    numero_serie: '',
                    descricao: '',
                    categoria: '',
                    modelo: '',
                    fonte: 'MANUAL',
                    situacao: 'REQUER ATUAÇÃO',
                    _hasDuplicates: false,
                    _manual: true
                };
                sessionItems.unshift(manual);
                drawSession();
                fb.innerHTML = '<div class="alert alert-warning">Ativo não encontrado no EBS para "' + S.esc(v) +
                    '". Adicionado à sessão como "REQUER ATUAÇÃO" — edite para incluir.</div>';
            } else if (d.duplicatas) {
                var first = d.resultados[0];
                first.hora = new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
                first.pesquisado = d.pesquisado;
                first._hasDuplicates = true;
                first._duplicates = d.resultados;
                first.situacao = 'REQUER ATUAÇÃO';
                sessionItems.unshift(first);
                drawSession();
                fb.innerHTML = '<div class="alert alert-warning">Duplicatas encontradas para "' + S.esc(v) +
                    '". Selecione o ativo correto.</div>';
            } else {
                var item = d.resultados[0];
                item.hora = new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
                item.pesquisado = d.pesquisado;
                item._hasDuplicates = false;
                item._duplicadoLocal = d.duplicado_local || null;
                sessionItems.unshift(item);
                drawSession();
                var dupMsg = '';
                if (d.duplicado_local) {
                    var dl = d.duplicado_local;
                    dupMsg = '<div class="alert alert-danger mt-2">' +
                        '<strong>Ativo já existe na base local!</strong><br>' +
                        'Etiqueta: <strong>' + S.esc(dl.etiqueta || '—') + '</strong> | ' +
                        'Serial: <strong>' + S.esc(dl.numero_serie || '—') + '</strong> | ' +
                        'Empresa: <strong>' + S.esc(dl.empresa || '—') + '</strong><br>' +
                        'Último status: <strong>' + S.esc(dl.ultimo_status || '—') + '</strong>' +
                        (dl.data_recebimento ? ' (' + S.esc(dl.data_recebimento) + ')' : '') +
                        (dl.ciclo_aberto ? ' <span class="badge badge-warning">Ciclo aberto</span>' : '') +
                        '</div>';
                }
                fb.innerHTML = '<div class="alert ' +
                    (item.situacao === 'PRONTO PARA ENVIO' ? 'alert-success' : 'alert-warning') + '">' +
                    S.esc(item.situacao === 'PRONTO PARA ENVIO'
                        ? 'Ativo localizado e classificado.'
                        : 'Ativo localizado, mas requer edição manual.') + '</div>' + dupMsg;
            }
        } catch (x) {
            fb.innerHTML = '<div class="alert alert-danger">' + S.esc(x.message) + '</div>';
        } finally {
            e.target.value = '';
            e.target.disabled = false;
            e.target.focus();
        }
    }

    // Cada origem tem o seu cartão de entrada: um bipa, o outro digita.
    function montarEntrada() {
        var alvo = document.getElementById('rec-entrada');
        var nota = document.getElementById('rec-origem-nota');
        document.querySelectorAll('#rec-origem button').forEach(function (b) {
            var ativo = b.dataset.origem === origem;
            b.className = 'btn ' + (ativo ? 'btn-primary' : 'btn-secondary');
            b.setAttribute('aria-pressed', String(ativo));
        });

        var soReversa = ['session-list', 'session-count', 'btn-select-ready', 'btn-submit-base', 'btn-discard'];
        soReversa.forEach(function (id) {
            var n = document.getElementById(id);
            if (!n) return;
            var alvoEsc = (id === 'session-list') ? n.parentNode : n;
            alvoEsc.style.display = origem === FORNECEDOR ? 'none' : '';
        });
        var espaco = document.getElementById('rec-espaco');
        if (espaco && espaco.closest('.card')) espaco.closest('.card').style.display = origem === FORNECEDOR ? 'none' : '';

        if (origem === FORNECEDOR) {
            nota.textContent = 'Compra nova chega pelo agendamento. Confira aqui o que veio: quantidade, seriais e nota.';
            alvo.innerHTML = '';
            telaFornecedores(alvo, S);
        } else {
            nota.textContent = 'Devolução da loja. O ativo já existe no EBS: ' +
                'bipe a etiqueta, a série ou o imobilizado.';
            alvo.innerHTML =
                '<div class="card mb-3">' +
                    '<div class="card-header">Leitura de Ativo</div>' +
                    '<div class="card-body">' +
                        '<input id="scan" class="form-control scan-input" ' +
                            'placeholder="Bipe ou digite e pressione Enter" autofocus>' +
                        '<div id="scan-feedback" class="mt-2"></div>' +
                    '</div>' +
                '</div>';
            var campo = document.getElementById('scan');
            if (campo) { campo.onkeydown = aoBipar; campo.focus(); }
            drawSession();
        }
    }

    document.querySelectorAll('#rec-origem button').forEach(function (b) {
        b.onclick = function () {
            if (b.dataset.origem === origem) return;
            // Lote de compra e devolução de loja não se misturam: são notas,
            // conferências e destinos diferentes.
            if (sessionItems.length && origem === REVERSA) {
                S.toast('Envie ou descarte a sessão atual antes de trocar a origem.', 'warning');
                return;
            }
            origem = b.dataset.origem === FORNECEDOR ? FORNECEDOR : REVERSA;
            saveCache();
            montarEntrada();
        };
    });

    document.getElementById('btn-select-ready').onclick = function () {
        sessionItems.forEach(function (item) {
            item._selected = item.situacao === 'PRONTO PARA ENVIO';
        });
        drawSession();
        S.toast('Ativos prontos selecionados.', 'info');
    };

    document.getElementById('btn-discard').onclick = function () {
        if (!sessionItems.length) return;
        S.openModal('Descartar Sessão', '<p>Tem certeza que deseja descartar todos os ativos da sessão temporária?</p>', [
            S.el('button', { className: 'btn btn-danger', textContent: 'Sim, descartar', onClick: function () {
                sessionItems = [];
                try { localStorage.removeItem(CACHE_KEY); } catch (_) {}
                montarEntrada();
                S.closeModal();
                S.toast('Sessão descartada.', 'info');
            }}),
            S.el('button', { className: 'btn btn-secondary', textContent: 'Cancelar', onClick: function () {
                S.closeModal();
            }})
        ]);
    };

    document.getElementById('btn-submit-base').onclick = async function () {
        var selected = sessionItems.filter(function (x) { return x._selected; });
        if (!selected.length) {
            S.toast('Selecione ao menos um ativo para enviar.', 'warning');
            return;
        }
        var notReady = selected.filter(function (x) { return x.situacao !== 'PRONTO PARA ENVIO'; });
        if (notReady.length) {
            S.toast(notReady.length + ' ativo(s) selecionado(s) não estão prontos para envio.', 'warning');
            return;
        }
        var campoEspaco = document.getElementById('rec-espaco');
        var espaco = (campoEspaco && campoEspaco.value || '').trim();
        if (!espaco) {
            S.toast('Informe o Espaço e Corredor onde os ativos ficaram guardados.', 'warning');
            if (campoEspaco) campoEspaco.focus();
            return;
        }
        var semSub = selected.filter(function (x) {
            return (x.destino_entrada || 'TRIAGEM') !== 'VENDA' && !x.subcategoria;
        });
        if (semSub.length) {
            S.toast('Informe a subcategoria dos ' + semSub.length +
                    ' ativo(s) que vão para triagem.', 'warning');
            return;
        }
        try {
            S.loading(true);
            var payload = selected.map(function (x) {
                return {
                    empresa: x.empresa || '',
                    asset_id: x.asset_id || x.imobilizado || '',
                    ativo: x.ativo || x.imobilizado || '',
                    etiqueta: x.etiqueta || '',
                    numero_serie: x.numero_serie || '',
                    descricao: x.descricao || '',
                    categoria: x.categoria || 'NÃO CLASSIFICADA',
                    modelo: x.modelo || '',
                    custo_asset: x.custo_asset || null,
                    dpis: x.dpis || null,
                    fonte: x.fonte || 'EBS',
                    destino_entrada: x.destino_entrada || 'TRIAGEM',
                    subcategoria: x.subcategoria || '',
                    po: x.po || '',
                    nf: x.nf || ''
                };
            });
            var d = await S.api('/recebimento/bulk-submit', {
                method: 'POST',
                body: { items: payload, espaco_corredor: espaco, origem: origem }
            });
            var msg = d.criados + ' ativo(s) enviado(s) para a base.';
            if (d.ignorados) msg += ' ' + d.ignorados + ' já possuíam recebimento aberto.';
            if (d.erros && d.erros.length) msg += ' ' + d.erros.length + ' erro(s).';
            var sn = d.no_servicenow || {};
            if (sn.ativo) {
                var partes = [];
                if (sn.criados) partes.push(sn.criados + ' criado(s) no ServiceNow');
                if (sn.atualizados) partes.push(sn.atualizados + ' atualizado(s)');
                if (sn.incompletos) partes.push(sn.incompletos + ' sem custo/depreciação (não subiram)');
                if (sn.depreciados) partes.push(sn.depreciados + ' com depreciação calculada');
                if (sn.sem_depreciacao) partes.push(sn.sem_depreciacao + ' SEM depreciação');
                if (partes.length) msg += ' ' + partes.join(', ') + '.';
                if (sn.falhas && sn.falhas.length) msg += ' ServiceNow: ' + sn.falhas[0];
            }
            var mdm = d.mdm || {};
            if (mdm.removidos) msg += ' ' + mdm.removidos + ' coletor(es) removido(s) do MDM.';
            else if (mdm.pendentes) msg += ' ' + mdm.pendentes + ' remoção(ões) do MDM pendente(s): ' + (mdm.motivo || (mdm.falhas || [])[0] || '');
            else if (mdm.nao_encontrados) msg += ' ' + mdm.nao_encontrados + ' não localizado(s) no MDM.';
            S.toast(msg, (d.erros && d.erros.length) || (sn.falhas && sn.falhas.length) ? 'warning' : 'success');
            mostrarDesfecho(d);
            sessionItems = sessionItems.filter(function (x) { return !x._selected; });
            drawSession();
        } catch (x) {
            S.toast(x.message, 'error');
        } finally {
            S.loading(false);
        }
    };

    /* O que aconteceu com CADA série, sem ninguém precisar perguntar: a
       leitura já tem o serial, então a tela responde por ele. */
    function mostrarDesfecho(d) {
        var sn = d.no_servicenow || {};
        var mdm = d.mdm || {};
        var porItem = sn.por_item || [];
        var porSerie = mdm.por_serie || {};
        var seriais = porItem.map(function (x) { return x.serial; });
        Object.keys(porSerie).forEach(function (s) {
            if (seriais.indexOf(s) < 0) { seriais.push(s); porItem.push({ serial: s }); }
        });
        if (!porItem.length) return;

        var linhas = porItem.map(function (x) {
            var m = porSerie[(x.serial || '').toUpperCase()] || null;
            var sn_txt = x.acao || '—';
            if (x.motivo) sn_txt += ' · ' + x.motivo;
            var dep = x.depreciacao || (x.acao ? '—' : '');
            var mdm_txt = '—';
            if (m) mdm_txt = m.ok ? 'removido do MDM' : ('pendente · ' + (m.detalhe || ''));
            return '<tr><td class="sep-serie">' + S.esc(x.serial || x.etiqueta || '—') + '</td>' +
                '<td>' + S.esc(sn_txt) + '</td>' +
                '<td>' + S.esc(dep) + '</td>' +
                '<td>' + S.esc(mdm_txt) + '</td></tr>';
        }).join('');

        var alvo = document.getElementById('rec-desfecho');
        if (!alvo) {
            alvo = S.el('div', { id: 'rec-desfecho', className: 'card mb-3' });
            var lista = document.getElementById('session-list');
            lista.parentNode.parentNode.insertBefore(alvo, lista.parentNode);
        }
        alvo.innerHTML = '<div class="card-header">O que aconteceu com cada ativo</div>' +
            '<div class="card-body"><div class="table-wrapper"><table class="data-table">' +
            '<thead><tr><th>Série</th><th>ServiceNow</th><th>Depreciação</th>' +
            '<th>MDM</th></tr></thead><tbody>' + linhas + '</tbody></table></div></div>';
    }

    S.api('/recebimento/subcategorias').then(function (d) {
        subcategorias = d.subcategorias || [];
        drawSession();
    }).catch(function () {});

    S.api('/servicenow/gestao-ativos/config').then(function (cfg) {
        var dl = document.getElementById('rec-corredores');
        if (dl) dl.innerHTML = (cfg.corredores || []).map(function (x) {
            return '<option value="' + S.esc(x) + '">'; }).join('');
    }).catch(function () {});

    montarEntrada();

    if (sessionItems.length) {
        S.toast(sessionItems.length + ' ativo(s) restaurado(s) da sessão anterior.', 'info');
    }
}

/* ── Base de Recebimentos ───────────────────────────────────────── */
async function renderBase(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Base de Recebimentos</h1>' +
        '<div class="btn-row mb-3">' +
            '<button id="bf-export" class="btn btn-primary">Exportar base</button>' +
            '<button id="bf-snow" class="btn btn-secondary" ' +
                'title="Converte a base para o formato de importação do alm_hardware ' +
                'do ServiceNow: colunas em inglês e valores fixos. Não é a base.">' +
                'Exportar modelo do ServiceNow</button>' +
        '</div>' +
        '<div class="card mb-3">' +
            '<div class="card-body filter-grid">' +
                '<div class="form-group"><label>Status</label><input id="bf-status" class="form-control"></div>' +
                '<div class="form-group"><label>Empresa</label><input id="bf-company" class="form-control"></div>' +
                '<div class="form-group"><label>Categoria</label><input id="bf-cat" class="form-control"></div>' +
                '<div class="form-group"><label>Busca</label><input id="bf-q" class="form-control"></div>' +
                '<div class="form-group"><button id="bf-run" class="btn btn-primary">Filtrar</button></div>' +
            '</div>' +
        '</div>' +
        '<div id="bf-out"></div>';

    function openBaseEditModal(row) {
        var f = S.el('div');
        var fields = [
            ['Categoria',   'be-cat',    row.categoria],
            ['Modelo',      'be-model',  row.modelo],
            ['Empresa',     'be-company',row.empresa],
            ['Nº Série',    'be-serial', row.numero_serie],
            ['Imobilizado', 'be-asset',  row.imobilizado]
        ];
        fields.forEach(function (x) {
            var g = S.el('div', { className: 'form-group' });
            g.innerHTML = '<label>' + S.esc(x[0]) + '</label>' +
                '<input id="' + x[1] + '" class="form-control" value="' + S.esc(x[2] || '') + '">';
            f.appendChild(g);
        });
        var statusGroup = S.el('div', { className: 'form-group' });
        statusGroup.innerHTML = '<label>Status</label>' +
            '<input id="be-status" class="form-control" value="' + S.esc(row.status || '') + '">';
        f.appendChild(statusGroup);
        var noteGroup = S.el('div', { className: 'form-group' });
        noteGroup.innerHTML = '<label>Observação</label>' +
            '<input id="be-note" class="form-control" value="' + S.esc(row.note || '') + '">';
        f.appendChild(noteGroup);

        var saveBtn = S.el('button', { className: 'btn btn-primary', textContent: 'Salvar' });
        saveBtn.onclick = async function () {
            try {
                await S.api('/recebimentos/' + row.id + '/asset', {
                    method: 'PUT',
                    body: {
                        categoria: document.getElementById('be-cat').value.trim() || null,
                        modelo: document.getElementById('be-model').value.trim() || null,
                        empresa: document.getElementById('be-company').value.trim() || null,
                        numero_serie: document.getElementById('be-serial').value.trim() || null,
                        imobilizado: document.getElementById('be-asset').value.trim() || null
                    }
                });
                var newStatus = document.getElementById('be-status').value.trim();
                var newNote = document.getElementById('be-note').value.trim();
                if (newStatus !== row.status || newNote !== (row.note || '')) {
                    await S.api('/recebimentos/' + row.id, {
                        method: 'PUT',
                        body: {
                            status: newStatus || null,
                            note: newNote
                        }
                    });
                }
                S.closeModal();
                S.toast('Registro atualizado.', 'success');
                load();
            } catch (e) {
                S.toast(e.message, 'error');
            }
        };
        S.openModal('Editar Recebimento #' + row.id, f, [saveBtn]);
    }

    async function load() {
        try {
            var p = new URLSearchParams({
                status:    document.getElementById('bf-status').value,
                empresa:   document.getElementById('bf-company').value,
                categoria: document.getElementById('bf-cat').value,
                q:         document.getElementById('bf-q').value
            });
            var d = await S.api('/recebimentos?' + p);
            var cols = [
                ['id',                'ID'],
                ['data_recebimento',  'Data'],
                ['origem_entrada',    'Origem'],
                ['po',                'PO'],
                ['nf',                'NF'],
                ['empresa',           'Empresa'],
                ['imobilizado',       'Imobilizado'],
                ['etiqueta',          'Etiqueta'],
                ['numero_serie',      'Nº Série'],
                ['descricao',         'Descrição'],
                ['categoria',         'Categoria'],
                ['modelo',            'Modelo'],
                ['status',            'Status'],
                ['local',             'Local'],
                ['lote',              'Lote']
            ].map(function (x) {
                var col = {
                    key: x[0],
                    label: x[1],
                    html: x[0] === 'status' || x[0] === 'categoria',
                    render: x[0] === 'status' ? function (v) { return S.badge(v); } : undefined
                };
                if (x[0] === 'origem_entrada') {
                    col.render = function (v) {
                        return v === 'FORNECEDOR' ? 'Fornecedor' : 'Reversa';
                    };
                }
                if (x[0] === 'categoria') {
                    col.render = function (v) {
                        if (!v || v === 'NÃO CLASSIFICADA') {
                            return S.esc(v || 'NÃO CLASSIFICADA') +
                                ' <span class="badge badge-danger">Necessário ajuste</span>';
                        }
                        return S.esc(v);
                    };
                }
                return col;
            });
            cols.push({
                key: '_sn', label: 'Localizado no SN?', html: true,
                render: function (_, row) {
                    return '<span id="sn-exist-' + row.id + '" class="badge badge-default">…</span>';
                }
            });
            cols.push({
                key: '_actions', label: 'Ações',
                render: function (_, row) {
                    var w = S.el('div', { className: 'btn-row' });
                    var editBtn = S.el('button', { className: 'btn btn-sm btn-outline', textContent: 'Editar' });
                    editBtn.onclick = function () { openBaseEditModal(row); };
                    var removeBtn = S.el('button', { className: 'btn btn-sm btn-danger', textContent: 'Remover' });
                    removeBtn.onclick = function () {
                        S.openModal('Remover Recebimento', '<p>Confirma a remoção do recebimento #' + row.id + '?</p>', [
                            S.el('button', { className: 'btn btn-danger', textContent: 'Sim, remover', onClick: async function () {
                                try {
                                    await S.api('/recebimentos/' + row.id, { method: 'DELETE' });
                                    S.closeModal();
                                    S.toast('Recebimento removido.', 'success');
                                    load();
                                } catch (e) {
                                    S.toast(e.message, 'error');
                                }
                            }}),
                            S.el('button', { className: 'btn btn-secondary', textContent: 'Cancelar', onClick: function () {
                                S.closeModal();
                            }})
                        ]);
                    };
                    w.appendChild(editBtn);
                    w.appendChild(removeBtn);
                    return w;
                }
            });
            var out = document.getElementById('bf-out');
            out.innerHTML = '';
            out.appendChild(S.table(cols, d.registros));
            _verificarSN(d.registros);
        } catch (e) {
            S.toast(e.message, 'error');
        }
    }

    // Consulta o ServiceNow (alm_hardware) e preenche a coluna "Localizado no SN?".
    async function _verificarSN(registros) {
        if (!registros || !registros.length) return;
        var itens = registros.map(function (r) {
            return { id: r.id, asset_tag: r.etiqueta || '', serial_number: r.numero_serie || '' };
        });
        try {
            var d = await S.api('/servicenow/hardware-exists', {
                method: 'POST', body: { itens: itens }
            });
            (d.resultados || []).forEach(function (x) {
                var el = document.getElementById('sn-exist-' + x.id);
                if (!el) return;
                el.textContent = x.existe ? 'True' : 'False';
                el.className = 'badge ' + (x.existe ? 'badge-success' : 'badge-danger');
            });
        } catch (e) {
            registros.forEach(function (r) {
                var el = document.getElementById('sn-exist-' + r.id);
                if (el) { el.textContent = '—'; el.className = 'badge badge-default';
                    el.title = 'Sessão ServiceNow indisponível'; }
            });
        }
    }

    // Os filtros da tela valem para a exportação: quem filtra 200 linhas e
    // clica em exportar espera as 200, não a base inteira.
    function filtrosDaTela() {
        return new URLSearchParams({
            status:    document.getElementById('bf-status').value,
            empresa:   document.getElementById('bf-company').value,
            categoria: document.getElementById('bf-cat').value,
            q:         document.getElementById('bf-q').value
        });
    }

    document.getElementById('bf-run').onclick = load;
    document.getElementById('bf-export').onclick = function () {
        // A BASE: as mesmas colunas da tabela acima, com os mesmos valores.
        S.baixar('/recebimentos/export?' + filtrosDaTela());
    };
    document.getElementById('bf-snow').onclick = function () {
        // Outra coisa: a base traduzida para o formato de importação do
        // ServiceNow. Pelo S.baixar, que põe o prefixo do proxy — com o
        // caminho absoluto ('/api/...') o navegador resolvia contra a RAIZ do
        // domínio e o botão caía na API do outro sistema.
        S.baixar('/recebimentos/export-servicenow');
    };
    load();
}

/* ── Dashboard ──────────────────────────────────────────────────── */
async function renderDashboard(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Dashboard de Recebimentos</h1>' +
        '<div id="rd-stats" class="stats-grid mb-3"></div>' +
        '<div id="rd-tables" class="charts-grid"></div>';

    try {
        var d = await S.api('/recebimentos/dashboard');
        var stats = [
            ['Total',          d.total,       'teal'],
            ['Ativos Únicos',  d.unicos,      'orange'],
            ['Retornos',       d.devolucoes,   'gold']
        ];
        document.getElementById('rd-stats').innerHTML = stats.map(function (x) {
            return '<div class="stat-card accent-' + x[2] + '">' +
                '<div class="stat-value">' + x[1] + '</div>' +
                '<div class="stat-label">' + x[0] + '</div>' +
            '</div>';
        }).join('');

        var groups = [
            ['Por Empresa',   d.por_empresa,   'empresa'],
            ['Por Categoria', d.por_categoria,  'categoria'],
            ['Por Status',    d.por_status,     'status'],
            ['Por Local',     d.por_local,      'local']
        ];
        var tables = document.getElementById('rd-tables');
        tables.innerHTML = '';
        groups.forEach(function (g) {
            var card = S.el('div', { className: 'card' }, [
                S.el('div', { className: 'card-header', textContent: g[0] }),
                S.el('div', { className: 'card-body' },
                    S.table(
                        [{ key: g[2], label: g[2] }, { key: 'total', label: 'Total' }],
                        g[1]
                    )
                )
            ]);
            tables.appendChild(card);
        });
    } catch (e) {
        S.toast(e.message, 'error');
    }
}

/* ── Lotes ──────────────────────────────────────────────────────── */
function renderLotes(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Lotes</h1>' +
        '<div class="card mb-3">' +
            '<div class="card-body form-grid cols-2">' +
                '<div class="form-group"><label>Modo</label>' +
                    '<select id="lot-mode" class="form-control">' +
                        '<option value="base">Selecionar na base</option>' +
                        '<option value="scan">Bipar</option>' +
                    '</select>' +
                '</div>' +
                '<div class="form-group"><label>Tipo</label>' +
                    '<select id="lot-prefix" class="form-control">' +
                        '<option>VENDA</option><option>TRIAGEM</option>' +
                    '</select>' +
                '</div>' +
            '</div>' +
        '</div>' +
        '<div id="lot-scan-card" class="card mb-3" hidden>' +
            '<div class="card-body">' +
                '<input id="lot-scan" class="form-control scan-input" placeholder="Bipe o ativo">' +
            '</div>' +
        '</div>' +
        '<div class="card mb-3">' +
            '<div class="card-header">Itens</div>' +
            '<div id="lot-items" class="card-body"></div>' +
        '</div>' +
        '<button id="lot-create" class="btn btn-primary">Gerar Lote</button>';

    var items = [];
    var cols = [
        {
            key: 'choose', label: '',
            render: function (_, r) {
                var x = S.el('input', { type: 'checkbox' });
                x.checked = true;
                x.onchange = function () { r.selected = x.checked; };
                r.selected = true;
                return x;
            }
        },
        { key: 'id',          label: 'ID' },
        { key: 'imobilizado', label: 'Imobilizado' },
        { key: 'descricao',   label: 'Descrição' },
        { key: 'status',      label: 'Status', html: true, render: function (v) { return S.badge(v); } }
    ];

    function draw() {
        var el = document.getElementById('lot-items');
        el.innerHTML = '';
        el.appendChild(S.table(cols, items));
    }

    async function loadBase() {
        var d = await S.api('/recebimentos?status=RECEBIDO&limit=500');
        items = d.registros;
        draw();
    }

    document.getElementById('lot-mode').onchange = function (e) {
        if (e.target.value === 'scan') {
            document.getElementById('lot-scan-card').hidden = false;
            items = [];
            draw();
            document.getElementById('lot-scan').focus();
        } else {
            document.getElementById('lot-scan-card').hidden = true;
            loadBase();
        }
    };

    document.getElementById('lot-scan').onkeydown = async function (e) {
        if (e.key !== 'Enter') return;
        var v = e.target.value.trim();
        try {
            var d = await S.api('/recebimentos?q=' + encodeURIComponent(v));
            var x = d.registros[0];
            if (!x) return S.toast('Ativo não está na base de recebimentos.', 'warning');
            if (!items.some(function (i) { return i.id === x.id; })) items.push(x);
            draw();
        } catch (z) {
            S.toast(z.message, 'error');
        }
        e.target.value = '';
    };

    document.getElementById('lot-create').onclick = async function () {
        var ids = items
            .filter(function (x) { return x.selected !== false; })
            .map(function (x) { return x.id; });
        if (!ids.length) return S.toast('Selecione itens.', 'warning');
        try {
            var d = await S.api('/lotes', {
                method: 'POST',
                body: { prefixo: document.getElementById('lot-prefix').value, ids: ids }
            });
            S.toast('Lote ' + d.numero_lote + ' gerado para ' + d.quantidade + ' item(ns).', 'success');
            items = [];
            draw();
        } catch (e) {
            S.toast(e.message, 'error');
        }
    };

    loadBase();
}

/* ── Cadastro de modelos (Classifications) ──────────────────────── */
async function renderModelos(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Cadastro de modelos</h1>' +
        '<button id="pm-class-add" class="btn btn-primary mb-3">Nova regra</button>' +
        '<button id="pm-class-import" class="btn btn-secondary mb-3" style="margin-left:8px">Importar planilha</button>' +
        '<div id="pm-class-list"></div>';

    function field(label, id, value) {
        var d = S.el('div', { className: 'form-group' });
        d.innerHTML = '<label>' + S.esc(label) + '</label>' +
            '<input id="' + id + '" class="form-control" value="' + S.esc(value || '') + '">';
        return d;
    }

    async function load() {
        var d = await S.api('/parametros/classificacoes');
        var cols = [
            { key: 'padrao_descricao', label: 'Padrão' },
            { key: 'empresa',          label: 'Empresa' },
            { key: 'categoria',        label: 'Categoria' },
            { key: 'modelo',           label: 'Modelo' },
            { key: 'ativo',            label: 'Ativa' },
            {
                key: 'a', label: '',
                render: function (_, r) {
                    var w = S.el('div', { className: 'btn-row' });
                    var e = S.el('button', { className: 'btn btn-sm btn-outline', textContent: 'Editar' });
                    var a = S.el('button', { className: 'btn btn-sm btn-secondary', textContent: 'Aplicar' });
                    e.onclick = function () { edit(r); };
                    a.onclick = async function () {
                        var x = await S.api('/parametros/classificacoes/' + r.id, { method: 'PUT', body: { padrao_descricao: r.padrao_descricao, empresa: r.empresa, categoria: r.categoria, modelo: r.modelo, ativo: true } });
                        var msg = 'Regra aplicada.';
                        if (x && typeof x.atualizados === 'number') {
                            msg += ' ' + x.atualizados + ' ativo(s) da base atualizado(s).';
                        }
                        S.toast(msg, 'success');
                    };
                    w.append(e, a);
                    // Excluir só para o admin do portal inteiro. A rota já
                    // existia e ninguém alcançava: a tela não tinha o botão.
                    if ((S.user() || {}).is_admin) {
                        var x = S.el('button', { className: 'btn btn-sm btn-danger', textContent: 'Excluir' });
                        x.onclick = async function () {
                            if (!window.confirm('Excluir a regra "' + (r.padrao_descricao || '') +
                                                '"?\n\nOs ativos já classificados por ela NÃO mudam; ' +
                                                'a regra só deixa de valer para as próximas entradas.')) return;
                            try {
                                await S.api('/parametros/classificacoes/' + r.id, { method: 'DELETE' });
                                S.toast('Regra excluída.', 'success');
                                load();
                            } catch (err) {
                                S.toast(err.message, 'error');
                            }
                        };
                        w.append(x);
                    }
                    return w;
                }
            }
        ];
        var el = document.getElementById('pm-class-list');
        el.innerHTML = '';
        el.appendChild(S.table(cols, d.regras));
    }

    function edit(r) {
        r = r || {};
        var f = S.el('div');
        [
            ['Padrão da descrição', 'pm-cp', r.padrao_descricao],
            ['Empresa (opcional)',   'pm-ce', r.empresa],
            ['Categoria',           'pm-cc', r.categoria],
            ['Modelo',              'pm-cm', r.modelo]
        ].forEach(function (x) { f.appendChild(field(x[0], x[1], x[2])); });

        var saveBtn = S.el('button', { className: 'btn btn-primary', textContent: 'Salvar e atualizar base' });
        saveBtn.onclick = async function () {
            try {
                var x = await S.api('/parametros/classificacoes' + (r.id ? '/' + r.id : ''), {
                    method: r.id ? 'PUT' : 'POST',
                    body: {
                        padrao_descricao: document.getElementById('pm-cp').value,
                        empresa:          document.getElementById('pm-ce').value,
                        categoria:        document.getElementById('pm-cc').value,
                        modelo:           document.getElementById('pm-cm').value,
                        ativo:            true
                    }
                });
                S.closeModal();
                var msg = 'Regra salva.';
                if (x && typeof x.atualizados === 'number') {
                    msg += ' ' + x.atualizados + ' ativo(s) da base atualizado(s).';
                }
                S.toast(msg, 'success');
                load();
            } catch (e) {
                S.toast(e.message, 'error');
            }
        };
        S.openModal(r.id ? 'Editar regra' : 'Nova regra', f, [saveBtn]);
    }

    document.getElementById('pm-class-add').onclick = function () { edit(); };

    document.getElementById('pm-class-import').onclick = function () {
        var f = S.el('div');
        f.innerHTML =
            '<p class="text-muted">Colunas obrigatórias: Descrição EBS, Model category e Model.</p>' +
            '<input id="pm-class-file" type="file" accept=".xlsx,.xls,.csv" class="form-control">';
        var btn = S.el('button', { className: 'btn btn-primary', textContent: 'Importar e atualizar bases' });
        btn.onclick = async function () {
            var file = document.getElementById('pm-class-file').files[0];
            if (!file) return S.toast('Selecione uma planilha.', 'warning');
            var fd = new FormData();
            fd.append('file', file);
            try {
                S.toast('Importação de classificações requer upload individual por enquanto.', 'warning');
                S.closeModal();
                load();
            } catch (e) {
                S.toast(e.message, 'error');
            }
        };
        S.openModal('Importar classificações', f, [btn]);
    };

    load();
}
/* As abas "Importar base histórica" e "Base local EBS" foram movidas para
   Parâmetros → Configuração Módulos (visível apenas para ADMIN). */


/* ── Fornecedores: a chegada do agendamento ────────────────────── */
function telaFornecedores(host, S) {
    var e = S.esc;
    var u = S.user() || {};
    var pm = (u.permission_map || {}).recebimento || {};
    var podeReceber = !!(u.is_admin || pm.can_create);
    var B = '/recebimento/fornecedores';

    host.innerHTML =
        '<div class="card mb-3"><div class="card-header">Agendamentos de fornecedores</div><div class="card-body">' +
            '<div class="filter-grid">' +
                '<div class="form-group"><label for="rf-status">Situação</label>' +
                    '<select id="rf-status" class="form-control">' +
                    '<option value="AGENDADO">Agendados</option>' +
                    '<option value="RECEBIDO">Recebidos</option>' +
                    '<option value="">Todos</option></select></div>' +
                '<div class="form-group"><label for="rf-busca">Buscar (NF, PO, fornecedor)</label>' +
                    '<input id="rf-busca" class="form-control" placeholder="digite e Enter"></div>' +
            '</div>' +
            '<div id="rf-lista" class="mt-2"></div>' +
        '</div></div>' +
        '<div id="rf-conf" class="card mb-3" style="display:none">' +
            '<div class="card-header" id="rf-conf-titulo">Conferência</div>' +
            '<div class="card-body" id="rf-conf-corpo"></div>' +
        '</div>';

    var prep = null, agId = null, itens = [];

    function fmtData(iso) {
        if (!iso) return '—';
        var p = iso.split('-');
        return p.length === 3 ? (p[2] + '/' + p[1] + '/' + p[0]) : iso;
    }

    async function carregar() {
        var alvo = document.getElementById('rf-lista');
        alvo.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div>';
        var qs = '?status=' + encodeURIComponent(document.getElementById('rf-status').value) +
                 '&busca=' + encodeURIComponent(document.getElementById('rf-busca').value.trim());
        try {
            var d = await S.api(B + qs);
            alvo.innerHTML = '';
            if (!d.total) { alvo.innerHTML = '<p class="text-muted">Nenhum agendamento nesta condição.</p>'; return; }
            alvo.appendChild(S.table([
                { key: 'pedidos', label: 'NF / PO', html: true, render: function (v) {
                    return (v || []).map(function (p) { return '<b>' + e(p.nf || '—') + '</b> / ' + e(p.po); }).join('<br>') || '—';
                } },
                { key: 'bu', label: 'BU', render: function (v) { return v || '—'; } },
                { key: 'fornecedor', label: 'Fornecedor' },
                { key: 'estoque_destino_rotulo', label: 'Destino' },
                { key: 'volumes', label: 'Vol.', render: function (v) { return v == null ? '—' : v; } },
                { key: 'data_agendada', label: 'Agendada', render: fmtData },
                { key: 'equipamentos', label: 'Equipamentos', html: true, render: function (v) {
                    return (v || []).map(function (q) { return e(q.descricao) + ' <b>×' + e(q.quantidade) + '</b>'; }).join('<br>') || '—';
                } },
                { key: 'status', label: 'Situação', html: true, render: function (v, r) {
                    var h = v === 'RECEBIDO' ? '<span class="badge badge-success">Recebido</span>' : '<span class="badge badge-warning">Agendado</span>';
                    if (r.entrega_parcial) h += ' <span class="badge badge-warning">Entrega parcial</span>';
                    return h;
                } },
                { key: 'data_recebimento', label: 'Recebimento', render: fmtData },
                { key: 'id', label: '', html: true, render: function (v, r) {
                    if (r.status !== 'AGENDADO' || !podeReceber) return '';
                    return '<button class="btn btn-primary btn-sm rf-iniciar" data-id="' + e(String(v)) + '">Iniciar Recebimento</button>';
                } }
            ], d.itens));
            alvo.querySelectorAll('.rf-iniciar').forEach(function (b) {
                b.onclick = function () { iniciar(parseInt(b.dataset.id, 10)); };
            });
        } catch (x) {
            alvo.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
        }
    }

    function fechar() {
        document.getElementById('rf-conf').style.display = 'none';
        document.getElementById('rf-conf-corpo').innerHTML = '';
        prep = null; agId = null; itens = [];
    }

    async function iniciar(id) {
        var card = document.getElementById('rf-conf');
        var corpo = document.getElementById('rf-conf-corpo');
        card.style.display = '';
        corpo.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Consultando o pedido…</div>';
        card.scrollIntoView({ behavior: 'smooth', block: 'start' });
        try {
            prep = await S.api(B + '/' + id + '/preparar');
        } catch (x) {
            corpo.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>' +
                '<div class="btn-row mt-2"><button type="button" class="btn btn-secondary btn-sm" id="rf-fechar-erro">Fechar</button></div>';
            document.getElementById('rf-fechar-erro').onclick = fechar;
            return;
        }
        agId = id;
        itens = prep.itens.map(function (it) {
            var q = it.manual ? it.quantidade_pedida : it.quantidade_pendente;
            return Object.assign({}, it, { quantidade_recebida: q, seriais: [] });
        });
        montarConferencia();
    }

    function montarConferencia() {
        var a = prep.agendamento;
        var corpo = document.getElementById('rf-conf-corpo');
        document.getElementById('rf-conf-titulo').textContent =
            'Conferência — ' + (a.fornecedor || '') + ' · ' + (a.bu || 'sem BU');
        var pos = (a.pedidos || []).map(function (p) { return 'PO ' + e(p.po) + (p.nf ? ' / NF ' + e(p.nf) : ''); }).join(' · ');
        corpo.innerHTML =
            '<div class="form-grid cols-3">' +
                info('Fornecedor', a.fornecedor) + info('BU', a.bu || '—') + info('Destino', a.estoque_destino_rotulo) +
            '</div>' +
            '<p class="text-muted">' + pos + '</p>' +
            '<p id="rf-etq" class="text-muted">Etiquetas disponíveis: <b>' + e(String(prep.etiquetas_disponiveis)) + '</b></p>' +
            (prep.avisos.length ? '<div class="alert alert-warning">' + prep.avisos.map(e).join('<br>') + '</div>' : '') +
            '<h3 class="mt-3">Notas fiscais</h3><div id="rf-notas"></div>' +
            '<h3 class="mt-3">Itens imobilizados</h3><div id="rf-itens"></div>' +
            (prep.tem_ebs ? '' : '<div class="btn-row mt-2"><button type="button" id="rf-add-item" class="btn btn-secondary btn-sm">+ Adicionar item</button></div>') +
            (prep.nao_imobilizados.length ? '<h3 class="mt-3">Não imobilizados</h3><p class="text-muted">Seguem para o pagamento junto com a nota, sem etiqueta nem lançamento.</p><div id="rf-fora"></div>' : '') +
            '<h3 class="mt-3">Resumo</h3><div id="rf-resumo"></div>' +
            '<div class="form-group mt-2"><label for="rf-obs">Observação</label><input id="rf-obs" class="form-control"></div>' +
            '<div class="btn-row mt-2">' +
                '<button type="button" id="rf-confirmar" class="btn btn-primary">Confirmar recebimento e enviar ao Lançamento</button>' +
                '<button type="button" id="rf-fechar" class="btn btn-secondary">Fechar</button>' +
            '</div>' +
            '<div id="rf-erro" class="alert alert-danger mt-2" hidden></div>' +
            '<div id="rf-ok" class="mt-2"></div>';
        montarNotas();
        montarItens();
        if (prep.nao_imobilizados.length) {
            document.getElementById('rf-fora').appendChild(S.table([
                { key: 'po', label: 'PO' }, { key: 'linha', label: 'Linha' }, { key: 'item_ebs', label: 'Item' },
                { key: 'descricao', label: 'Descrição' }, { key: 'quantidade_pedida', label: 'Pedido' }
            ], prep.nao_imobilizados));
        }
        if (!prep.tem_ebs) document.getElementById('rf-add-item').onclick = function () {
            itens.push({ po: (a.pedidos[0] || {}).po || '', nf: (a.pedidos[0] || {}).nf || '', linha: null, item_ebs: '',
                         descricao: '', unidade: '', quantidade_pedida: 1, quantidade_pendente: 1, quantidade_nf: null,
                         imobilizado: true, manual: true, quantidade_recebida: 1, seriais: [] });
            montarItens();
        };
        document.getElementById('rf-fechar').onclick = fechar;
        document.getElementById('rf-confirmar').onclick = confirmar;
        resumo();
    }

    function info(rot, val) {
        return '<div class="form-group"><label>' + e(rot) + '</label><div style="padding:6px 0"><b>' + e(val || '—') + '</b></div></div>';
    }

    function montarNotas() {
        var alvo = document.getElementById('rf-notas');
        alvo.innerHTML = '';
        if (!prep.notas.length) { alvo.innerHTML = '<p class="text-muted">O agendamento não tem NF informada.</p>'; return; }
        prep.notas.forEach(function (n, i) {
            var box = document.createElement('div');
            box.className = 'mb-2';
            box.style.cssText = 'border:1px solid var(--sp-border);border-radius:var(--sp-raio-campo);padding:10px';
            box.innerHTML =
                '<div class="filter-grid">' +
                    '<div class="form-group"><label>NF</label><div style="padding:6px 0"><b>' + e(n.nf) + '</b></div></div>' +
                    '<div class="form-group"><label>Chave de acesso (44 dígitos) <span class="text-muted rf-cont"></span></label>' +
                        '<input class="form-control rf-chave" inputmode="numeric" maxlength="60" value="' + e(n.chave) + '"></div>' +
                    '<div class="form-group"><label>Vencimento</label><input type="date" class="form-control rf-venc" value="' + e(n.vencimento) + '"></div>' +
                '</div>' +
                '<div class="btn-row" style="align-items:center;flex-wrap:wrap">' +
                    (prep.certificado_bu ? '<button type="button" class="btn btn-primary btn-sm rf-sefaz">Buscar na SEFAZ</button>' : '') +
                    '<button type="button" class="btn btn-secondary btn-sm rf-gravar">Gravar chave e vencimento</button>' +
                    '<label class="btn btn-secondary btn-sm" style="margin:0">Enviar XML ou PDF<input type="file" class="rf-arquivo" accept=".xml,.pdf,application/xml,text/xml,application/pdf" style="display:none"></label>' +
                '</div>' +
                '<div class="rf-nota-estado mt-2"></div>';
            alvo.appendChild(box);
            var chave = box.querySelector('.rf-chave'), cont = box.querySelector('.rf-cont');
            function contar() {
                chave.value = chave.value.replace(/\D/g, '');
                cont.textContent = chave.value.length ? '(' + chave.value.length + '/44)' : '';
            }
            chave.oninput = contar; contar();
            async function gravar(buscar) {
                var estado = box.querySelector('.rf-nota-estado');
                estado.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> ' + (buscar ? 'Buscando na SEFAZ…' : 'Gravando…') + '</div>';
                try {
                    var d = await S.api(B + '/' + agId + '/nota', { method: 'POST',
                        body: { nf: n.nf, chave: chave.value.trim(), vencimento: box.querySelector('.rf-venc').value } });
                    prep.notas[i] = d;
                    estadoNota(box, d);
                    recasar();
                } catch (x) { estado.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>'; }
            }
            box.querySelector('.rf-gravar').onclick = function () { gravar(false); };
            if (prep.certificado_bu) box.querySelector('.rf-sefaz').onclick = function () {
                if (chave.value.length !== 44) { chave.focus(); return S.toast('A chave tem 44 dígitos.', 'warning'); }
                gravar(true);
            };
            box.querySelector('.rf-arquivo').onchange = async function () {
                var f = this.files[0];
                if (!f) return;
                var estado = box.querySelector('.rf-nota-estado');
                estado.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Lendo o arquivo…</div>';
                var fd = new FormData();
                fd.append('nf', n.nf);
                fd.append('arquivo', f);
                try {
                    var d = await S.api(B + '/' + agId + '/nota/arquivo', { method: 'POST', body: fd });
                    prep.notas[i] = d;
                    if (d.chave) { chave.value = d.chave; contar(); }
                    if (d.vencimento) box.querySelector('.rf-venc').value = d.vencimento;
                    estadoNota(box, d);
                    recasar();
                } catch (x) { estado.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>'; }
                this.value = '';
            };
            estadoNota(box, n);
        });
    }

    function estadoNota(box, n) {
        var estado = box.querySelector('.rf-nota-estado');
        var partes = [];
        if (n.origem_rotulo) partes.push('Origem: ' + e(n.origem_rotulo));
        if (n.emitente) partes.push('Emitente: ' + e(n.emitente));
        if (n.tem_xml) partes.push('<a href="' + S.apiUrl(B + '/' + agId + '/nota/' + encodeURIComponent(n.nf) + '/xml') + '" target="_blank">XML</a>');
        if (n.tem_pdf) partes.push('<a href="' + S.apiUrl(B + '/' + agId + '/nota/' + encodeURIComponent(n.nf) + '/pdf') + '" target="_blank">PDF</a>');
        var h = partes.length ? '<p class="text-muted" style="margin:0">' + partes.join(' · ') + '</p>' : '';
        if (n.itens && n.itens.length) {
            h += '<div class="table-wrapper mt-1"><table class="data-table"><thead><tr><th>Código</th><th>Descrição</th><th>Qtd na NF</th></tr></thead><tbody>' +
                n.itens.map(function (it) { return '<tr><td>' + e(it.codigo) + '</td><td>' + e(it.descricao) + '</td><td>' + e(String(it.quantidade)) + '</td></tr>'; }).join('') +
                '</tbody></table></div>';
        }
        if (n.erro) h += '<div class="alert alert-warning mt-1">' + e(n.erro) + '</div>';
        estado.innerHTML = h;
    }

    function normalizar(s) { return String(s || '').replace(/^0+/, '').trim().toUpperCase(); }

    function recasar() {
        itens.forEach(function (it) {
            var nota = prep.notas.filter(function (n) { return n.nf === it.nf; })[0];
            it.quantidade_nf = null;
            if (!nota || !nota.itens) return;
            nota.itens.forEach(function (ni) {
                if (it.quantidade_nf != null) return;
                if (it.item_ebs && normalizar(ni.codigo) === normalizar(it.item_ebs)) it.quantidade_nf = ni.quantidade;
            });
            if (it.quantidade_nf == null) nota.itens.forEach(function (ni) {
                if (it.quantidade_nf == null && it.descricao && String(ni.descricao).trim().toLowerCase() === it.descricao.trim().toLowerCase()) it.quantidade_nf = ni.quantidade;
            });
        });
        montarItens();
    }

    function separar(texto) {
        return String(texto || '').split(/[\s,;]+/).map(function (x) { return x.trim(); }).filter(Boolean);
    }

    function montarItens() {
        var alvo = document.getElementById('rf-itens');
        alvo.innerHTML = '';
        if (!itens.length) { alvo.innerHTML = '<p class="text-muted">Nenhum item.</p>'; resumo(); return; }
        itens.forEach(function (it, idx) {
            var box = document.createElement('div');
            box.className = 'mb-2';
            box.style.cssText = 'border:1px solid var(--sp-border);border-radius:var(--sp-raio-campo);padding:10px';
            var cab = it.manual
                ? '<div class="filter-grid">' +
                    '<div class="form-group"><label>Item EBS</label><input class="form-control rf-it-item" value="' + e(it.item_ebs) + '"></div>' +
                    '<div class="form-group"><label>Descrição *</label><input class="form-control rf-it-desc" value="' + e(it.descricao) + '"></div>' +
                    '<div class="form-group"><label>Pedido</label><input type="number" min="0" class="form-control rf-it-ped" value="' + e(String(it.quantidade_pedida)) + '"></div>' +
                    '<div class="form-group"><label>Imobilizado</label><div style="padding:6px 0"><input type="checkbox" class="rf-it-imob"' + (it.imobilizado ? ' checked' : '') + '></div></div>' +
                  '</div>'
                : '<p style="margin:0 0 6px"><b>' + e(it.descricao) + '</b> <span class="text-muted">· item ' + e(it.item_ebs) +
                    ' · PO ' + e(it.po) + (it.linha != null ? ' linha ' + e(String(it.linha)) : '') + (it.nf ? ' · NF ' + e(it.nf) : '') +
                    (it.unidade ? ' · ' + e(it.unidade) : '') + '</span><br><span class="text-muted">pedido ' + e(String(it.quantidade_pedida)) +
                    ' · já recebido no EBS ' + e(String(it.quantidade_recebida_ebs || 0)) + ' · pendente ' + e(String(it.quantidade_pendente)) +
                    (it.quantidade_nf != null ? ' · na NF ' + e(String(it.quantidade_nf)) : '') + '</span></p>';
            box.innerHTML = cab +
                '<div class="rf-it-corpo">' +
                '<div class="filter-grid">' +
                    '<div class="form-group"><label>Quantidade recebida</label><input type="number" min="0" class="form-control rf-it-qtd" value="' + e(String(it.quantidade_recebida)) + '"></div>' +
                    '<div class="form-group" style="grid-column:span 2"><label>Colar seriais (um por linha, ou separados por vírgula)</label>' +
                        '<textarea class="form-control rf-it-cola" rows="2"></textarea></div>' +
                '</div>' +
                '<div class="rf-it-seriais"></div>' +
                '<div class="rf-it-msg text-muted"></div>' +
                '</div>' +
                (it.manual ? '<div class="btn-row mt-1"><button type="button" class="btn btn-secondary btn-sm rf-it-rem">Remover item</button></div>' : '');
            alvo.appendChild(box);

            function seriais() {
                var wrap = box.querySelector('.rf-it-seriais');
                wrap.innerHTML = '';
                if (!it.imobilizado) { box.querySelector('.rf-it-corpo').style.display = 'none'; return; }
                box.querySelector('.rf-it-corpo').style.display = '';
                var n = Math.max(0, parseInt(it.quantidade_recebida, 10) || 0);
                it.seriais = it.seriais.slice(0, n);
                while (it.seriais.length < n) it.seriais.push('');
                var grade = document.createElement('div');
                grade.className = 'form-grid cols-3';
                it.seriais.forEach(function (s, k) {
                    var inp = document.createElement('input');
                    inp.className = 'form-control rf-serial';
                    inp.placeholder = 'Serial ' + (k + 1);
                    inp.value = s;
                    inp.oninput = function () { it.seriais[k] = inp.value.trim(); marcarRepetidos(); resumo(); };
                    grade.appendChild(inp);
                });
                wrap.appendChild(grade);
                marcarRepetidos();
            }
            function marcarRepetidos() {
                var todos = {};
                itens.forEach(function (o) { (o.seriais || []).forEach(function (s) { if (s) todos[s.toUpperCase()] = (todos[s.toUpperCase()] || 0) + 1; }); });
                box.querySelectorAll('.rf-serial').forEach(function (inp) {
                    var rep = inp.value && todos[inp.value.trim().toUpperCase()] > 1;
                    inp.style.borderColor = rep ? 'var(--sp-alerta)' : '';
                    inp.title = rep ? 'Serial repetido' : '';
                });
            }
            box.querySelector('.rf-it-qtd').onchange = function () {
                it.quantidade_recebida = Math.max(0, parseInt(this.value, 10) || 0);
                this.value = it.quantidade_recebida;
                seriais(); resumo();
            };
            box.querySelector('.rf-it-cola').onchange = function () {
                var lista = separar(this.value);
                var k = 0;
                for (var j = 0; j < it.seriais.length && k < lista.length; j++) {
                    if (!it.seriais[j]) it.seriais[j] = lista[k++];
                }
                var msg = box.querySelector('.rf-it-msg');
                var vazios = it.seriais.filter(function (s) { return !s; }).length;
                msg.textContent = (lista.length - k > 0 ? 'Sobraram ' + (lista.length - k) + ' serial(is) além da quantidade. ' : '') +
                                  (vazios ? 'Faltam ' + vazios + ' serial(is).' : 'Todos os seriais preenchidos.');
                this.value = '';
                seriais(); resumo();
            };
            if (it.manual) {
                box.querySelector('.rf-it-item').oninput = function () { it.item_ebs = this.value.trim(); };
                box.querySelector('.rf-it-desc').oninput = function () { it.descricao = this.value.trim(); };
                box.querySelector('.rf-it-ped').onchange = function () { it.quantidade_pedida = Math.max(0, parseInt(this.value, 10) || 0); resumo(); };
                box.querySelector('.rf-it-imob').onchange = function () { it.imobilizado = this.checked; seriais(); resumo(); };
                box.querySelector('.rf-it-rem').onclick = function () { itens.splice(idx, 1); montarItens(); };
            }
            seriais();
        });
        resumo();
    }

    function resumo() {
        var alvo = document.getElementById('rf-resumo');
        if (!alvo) return;
        var imob = itens.filter(function (i) { return i.imobilizado; });
        var unidades = imob.reduce(function (s, i) { return s + (parseInt(i.quantidade_recebida, 10) || 0); }, 0);
        var parcial = imob.filter(function (i) { return (parseInt(i.quantidade_recebida, 10) || 0) < (parseInt(i.quantidade_pedida, 10) || 0); });
        var difNf = imob.filter(function (i) { return i.quantidade_nf != null && i.quantidade_nf !== (parseInt(i.quantidade_recebida, 10) || 0); });
        var faltamSer = imob.reduce(function (s, i) { return s + (i.seriais || []).filter(function (x) { return !x; }).length; }, 0);
        var h = '<p style="margin:0">' + unidades + ' unidade(s) imobilizada(s) × ' + e(String(prep.etiquetas_disponiveis)) + ' etiqueta(s) disponível(is).</p>';
        if (unidades > prep.etiquetas_disponiveis) h += '<div class="alert alert-warning">Faltam ' + (unidades - prep.etiquetas_disponiveis) + ' etiqueta(s). Cadastre em Internalização → Cadastro de Etiquetas.</div>';
        if (parcial.length) h += '<div class="alert alert-warning"><b>Entrega parcial:</b> ' + parcial.map(function (i) { return e(i.descricao || i.item_ebs) + ' (faltam ' + ((parseInt(i.quantidade_pedida, 10) || 0) - (parseInt(i.quantidade_recebida, 10) || 0)) + ')'; }).join('; ') + '.</div>';
        if (difNf.length) h += '<div class="alert alert-warning">Recebido diferente da NF: ' + difNf.map(function (i) { return e(i.descricao || i.item_ebs) + ' (NF ' + i.quantidade_nf + ', conferido ' + i.quantidade_recebida + ')'; }).join('; ') + '.</div>';
        if (faltamSer) h += '<p class="text-muted" style="margin:0">Faltam ' + faltamSer + ' serial(is).</p>';
        alvo.innerHTML = h;
    }

    async function confirmar() {
        var erro = document.getElementById('rf-erro');
        erro.hidden = true;
        var imob = itens.filter(function (i) { return i.imobilizado; });
        var vistos = {};
        for (var i = 0; i < imob.length; i++) {
            var it = imob[i];
            if (!it.descricao && it.manual) { erro.hidden = false; erro.textContent = 'Item sem descrição.'; return; }
            var ser = (it.seriais || []).map(function (s) { return s.trim(); });
            if (ser.some(function (s) { return !s; }) || ser.length !== (parseInt(it.quantidade_recebida, 10) || 0)) {
                erro.hidden = false; erro.textContent = (it.descricao || it.item_ebs) + ': informe um serial para cada unidade recebida.';
                var vazio = document.querySelectorAll('.rf-serial');
                for (var k = 0; k < vazio.length; k++) if (!vazio[k].value.trim()) { vazio[k].focus(); break; }
                return;
            }
            for (var j = 0; j < ser.length; j++) {
                var chave = ser[j].toUpperCase();
                if (vistos[chave]) { erro.hidden = false; erro.textContent = 'Serial ' + ser[j] + ' repetido.'; return; }
                vistos[chave] = true;
            }
        }
        var b = document.getElementById('rf-confirmar');
        b.disabled = true;
        var txt = b.textContent; b.textContent = 'Confirmando…';
        try {
            var d = await S.api(B + '/' + agId + '/confirmar', { method: 'POST', body: {
                itens: itens.map(function (it) { return {
                    po: it.po, nf: it.nf, linha: it.linha, item_ebs: it.item_ebs, descricao: it.descricao, unidade: it.unidade,
                    quantidade_pedida: parseInt(it.quantidade_pedida, 10) || 0,
                    quantidade_recebida: parseInt(it.quantidade_recebida, 10) || 0,
                    imobilizado: !!it.imobilizado, seriais: it.imobilizado ? it.seriais : [] }; }),
                observacao: document.getElementById('rf-obs').value.trim() } });
            var h = '<div class="alert alert-' + (d.aviso ? 'warning' : 'success') + '">Recebimento confirmado. ' +
                d.etiquetas_consumidas + ' etiqueta(s) consumida(s).' +
                (d.entrega_parcial ? ' <b>Entrega parcial:</b> ' + d.faltantes.map(function (f) { return e(f.descricao) + ' (faltam ' + f.faltam + ')'; }).join('; ') + '.' : '') +
                (d.aviso ? '<br>' + e(d.aviso) : '') + '</div>';
            document.getElementById('rf-ok').innerHTML = h;
            S.toast('Recebimento confirmado.', d.aviso ? 'warning' : 'success');
            document.querySelectorAll('#rf-conf-corpo input, #rf-conf-corpo textarea, #rf-conf-corpo button').forEach(function (n) { if (n.id !== 'rf-fechar') n.disabled = true; });
            carregar();
        } catch (x) {
            erro.hidden = false; erro.textContent = x.message;
            b.disabled = false; b.textContent = txt;
        }
    }

    document.getElementById('rf-status').onchange = carregar;
    document.getElementById('rf-busca').onkeydown = function (ev) { if (ev.key === 'Enter') carregar(); };
    carregar();
}
