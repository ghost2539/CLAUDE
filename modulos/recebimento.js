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

        if (origem === FORNECEDOR) {
            nota.textContent = 'Compra nova, que ainda não existe no EBS. ' +
                'Informe descrição, serial, PO e NF de cada item.';
            alvo.innerHTML =
                '<div class="card mb-3">' +
                    '<div class="card-header">Item do fornecedor</div>' +
                    '<div class="card-body">' +
                        '<div class="filter-grid">' +
                            '<div class="form-group"><label for="fo-desc">Descrição do item <span style="color:var(--sp-alerta)">*</span></label>' +
                                '<input id="fo-desc" class="form-control" autocomplete="off"></div>' +
                            '<div class="form-group"><label for="fo-serie">Serial Number <span style="color:var(--sp-alerta)">*</span></label>' +
                                '<input id="fo-serie" class="form-control" autocomplete="off" spellcheck="false"></div>' +
                            '<div class="form-group"><label for="fo-po">PO <span style="color:var(--sp-alerta)">*</span></label>' +
                                '<input id="fo-po" class="form-control" autocomplete="off"></div>' +
                            '<div class="form-group"><label for="fo-nf">NF <span style="color:var(--sp-alerta)">*</span></label>' +
                                '<input id="fo-nf" class="form-control" autocomplete="off"></div>' +
                        '</div>' +
                        '<div class="btn-row mt-3">' +
                            '<button id="fo-add" class="btn btn-primary" type="button">Adicionar item</button>' +
                        '</div>' +
                        '<div id="fo-feedback" class="mt-2"></div>' +
                    '</div>' +
                '</div>';
            ligarFornecedor();
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
        }
        drawSession();
    }

    function ligarFornecedor() {
        var desc = document.getElementById('fo-desc');
        var serie = document.getElementById('fo-serie');
        var po = document.getElementById('fo-po');
        var nf = document.getElementById('fo-nf');
        var fb = document.getElementById('fo-feedback');
        var add = document.getElementById('fo-add');

        add.onclick = async function () {
            var valores = [['descrição do item', desc], ['serial number', serie],
                           ['PO', po], ['NF', nf]];
            var faltando = valores.filter(function (x) { return !x[1].value.trim(); });
            if (faltando.length) {
                fb.innerHTML = '<div class="alert alert-warning">Informe ' +
                    faltando.map(function (x) { return x[0]; }).join(', ') + '.</div>';
                faltando[0][1].focus();
                return;
            }
            add.disabled = true;
            var item = {
                hora: new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }),
                empresa: '', imobilizado: '', ativo: '', asset_id: '', etiqueta: '',
                numero_serie: serie.value.trim(),
                descricao: desc.value.trim(),
                po: po.value.trim(),
                nf: nf.value.trim(),
                categoria: 'NÃO CLASSIFICADA',
                modelo: '',
                fonte: FORNECEDOR,
                destino_entrada: 'TRIAGEM',
                subcategoria: '',
                situacao: 'PRONTO PARA ENVIO',
                _selected: true,
                _fornecedor: true
            };
            // Série que já está na base é equipamento que o Spare já
            // conhece: pode ser retrabalho de digitação ou nota repetida.
            try {
                var d = await S.api('/recebimento/check-duplicate', {
                    method: 'POST', body: { identificador: item.numero_serie }
                });
                item._duplicadoLocal = d.duplicado ? d.existente : null;
            } catch (_) { /* a conferência é um aviso, não um bloqueio */ }

            sessionItems.unshift(item);
            // PO e NF ficam: a mesma nota costuma trazer vários itens.
            desc.value = '';
            serie.value = '';
            drawSession();
            fb.innerHTML = item._duplicadoLocal
                ? '<div class="alert alert-warning">Item adicionado, mas a série <strong>' +
                  S.esc(item.numero_serie) + '</strong> já existe na base.</div>'
                : '<div class="alert alert-success">Item adicionado à sessão.</div>';
            add.disabled = false;
            desc.focus();
        };

        [desc, serie, po, nf].forEach(function (campo) {
            campo.onkeydown = function (e) { if (e.key === 'Enter') add.click(); };
        });
    }

    document.querySelectorAll('#rec-origem button').forEach(function (b) {
        b.onclick = function () {
            if (b.dataset.origem === origem) return;
            // Lote de compra e devolução de loja não se misturam: são notas,
            // conferências e destinos diferentes.
            if (sessionItems.length) {
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
            '<button id="bf-snow" class="btn btn-secondary">Exportar ServiceNow CSV</button>' +
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

    document.getElementById('bf-run').onclick = load;
    document.getElementById('bf-snow').onclick = function () {
        // Pelo S.baixar, que põe o prefixo do proxy. Com o caminho absoluto
        // ('/api/...') o navegador resolvia contra a RAIZ do domínio e o
        // botão caía na API do outro sistema, não no portal.
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
