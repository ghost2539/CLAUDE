/* ================================================================
   Module: Parâmetros (Admin / Settings — all sub-tabs)
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.parametros = {

    render(container, sub) {
        var S = window.SPARE;
        var u = S.user();

        container.classList.add('parameters-module');

        var allTabs = [
            ['visual',          'Visual'],
            ['locais',          'Locais'],
            ['classificacoes',  'Classificações'],
            ['permissoes',      'Usuários e Permissões'],
            ['sequencias',      'Sequências'],
            ['config-modulos',  'Configuração Módulos'],
            ['separacao',       'Ciclo do ativo'],
            ['automacoes',      'Automações'],
            ['cofre',           'Cofre de segredos'],
            ['compras',         'Gestão de Compras'],
            ['base-ebs',        'Base EBS'],
            ['monitoramento',   'Monitoramento'],
            ['acessos',         'Acessos & Alertas'],
            ['dashboards',      'Dashboards'],
            ['conta',           'Minha conta']
        ];

        // Automações fica fora da lista: a aba é de todos. Dentro dela, quem
        // não é admin vê a situação, os logs e o botão Exec Now — a
        // configuração (credencial, cofre, horários) segue só do admin.
        var adminOnly = ['visual', 'permissoes', 'sequencias', 'config-modulos',
                         'separacao', 'monitoramento', 'cofre', 'compras', 'base-ebs',
                         'acessos', 'dashboards'];
        var visibleTabs = allTabs.filter(function (x) {
            return u.is_admin || adminOnly.indexOf(x[0]) === -1;
        });

        sub = sub || 'conta';
        S.tabs(visibleTabs, sub, 'parametros');

        container.innerHTML =
            '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando...</div>';

        var handlers = {
            visual:         renderVisual,
            locais:         renderLocations,
            classificacoes: renderClassifications,
            permissoes:     renderPermissions,
            sequencias:     renderSequences,
            'config-modulos': renderConfigModulos,
            separacao:      renderSeparacaoConfig,
            automacoes:     renderAutomacoes,
            cofre:          renderCofre,
            compras:        renderCompras,
            'base-ebs':     renderBaseEbs,
            monitoramento:  renderMonitoramento,
            acessos:        renderAcessos,
            dashboards:     renderDashboards,
            conta:          renderAccount
        };

        var handler = handlers[sub] || renderAccount;
        Promise.resolve(handler(container, S)).catch(function (e) {
            container.innerHTML =
                '<div class="alert alert-danger"><strong>Falha ao carregar.</strong><br>' +
                S.esc(e.message || e) + '</div>';
        });
    }

};

/* ── Helper: field builder ──────────────────────────────────────── */
function _pField(label, id, value, type) {
    var S = window.SPARE;
    var d = S.el('div', { className: 'form-group' });
    d.innerHTML = '<label>' + S.esc(label) + '</label>' +
        '<input id="' + id + '" type="' + (type || 'text') + '" class="form-control" ' +
        'value="' + S.esc(value || '') + '">';
    return d;
}

/* ── Configuração Módulos (admin) ───────────────────────────────────
   Reúne as configurações de bases dos módulos que antes ficavam em abas
   soltas (Recebimento). Visível apenas para ADMIN. */
function renderConfigModulos(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Configuração Módulos</h1>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Recebimento — Importar base histórica</div>' +
            '<div class="card-body">' +
                '<p class="text-muted">Aceita CSV ou XLSX com identificador, data, empresa, ' +
                    'categoria, modelo, status, local e lote.</p>' +
                '<form id="cm-hist-form">' +
                    '<input name="file" type="file" class="form-control" required>' +
                    '<button class="btn btn-primary mt-2">Importar</button>' +
                '</form>' +
                '<div id="cm-hist-result" class="mt-2"></div>' +
            '</div>' +
        '</div>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Recebimento — Base local EBS</div>' +
            '<div class="card-body">' +
                '<form id="cm-local-form">' +
                    '<div class="form-grid cols-2">' +
                        '<div class="form-group"><label>Empresa</label>' +
                            '<select name="company" class="form-control">' +
                                '<option>RENNER</option>' +
                                '<option>YOUCOM</option>' +
                                '<option>CAMICADO</option>' +
                            '</select>' +
                        '</div>' +
                        '<div class="form-group"><label>Modo</label>' +
                            '<select name="mode" class="form-control">' +
                                '<option>SUBSTITUIR</option>' +
                                '<option>INCREMENTAR</option>' +
                            '</select>' +
                        '</div>' +
                    '</div>' +
                    '<div class="form-group mt-2">' +
                        '<label>Arquivo CSV/XLSX</label>' +
                        '<input name="file" type="file" class="form-control" required>' +
                    '</div>' +
                    '<button class="btn btn-primary mt-2">Importar</button>' +
                '</form>' +
                '<div id="cm-local-result" class="mt-2"></div>' +
            '</div>' +
        '</div>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Central de Reparos — valor-hora</div>' +
            '<div class="card-body" id="cm-valor-hora">' +
                '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div>' +
            '</div>' +
        '</div>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Consulta — colunas padrão</div>' +
            '<div class="card-body" id="cm-consulta-cols"><div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div></div>' +
        '</div>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Gestão de Ativos — estoques, corredores e anotações</div>' +
            '<div class="card-body" id="cm-ga"><div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div></div>' +
        '</div>' +

        '<div class="card mb-3">' +
            '<div class="card-header">EBS — API de consulta</div>' +
            '<div class="card-body" id="cm-ebs"><div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div></div>' +
        '</div>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Recebimento — famílias e prefixos</div>' +
            '<div class="card-body" id="cm-familias"><div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div></div>' +
        '</div>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Recebimento — marcação no ServiceNow</div>' +
            '<div class="card-body">' +
                '<div id="cm-rec-sn"><div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div></div>' +
            '</div>' +
        '</div>' +

        '<div class="card mb-3">' +
            '<div class="card-header">Indicadores — filtros do ServiceNow</div>' +
            '<div class="card-body">' +
                '<div id="cm-ind-form"><div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div></div>' +
            '</div>' +
        '</div>';

    _renderIndicadoresConfig(S);
    _renderValorHora(S);
    _renderRecebimentoSN(S);
    _renderConsultaColunas(S);
    _renderGestaoAtivos(S);
    _renderFamilias(S);
    _renderEbs(S);

    document.getElementById('cm-hist-form').onsubmit = async function (e) {
        e.preventDefault();
        try {
            S.loading(true);
            var d = await S.api('/recebimentos/import-historico', {
                method: 'POST',
                body: new FormData(e.target)
            });
            document.getElementById('cm-hist-result').innerHTML =
                '<div class="alert alert-success">' +
                d.importados + ' importados; ' + d.rejeitados + ' rejeitados.</div>';
        } catch (x) {
            S.toast(x.message, 'error');
        } finally {
            S.loading(false);
        }
    };

    document.getElementById('cm-local-form').onsubmit = async function (e) {
        e.preventDefault();
        try {
            S.loading(true);
            var d = await S.api('/parametros/base-local/upload', {
                method: 'POST',
                body: new FormData(e.target)
            });
            document.getElementById('cm-local-result').innerHTML =
                '<div class="alert alert-success">' +
                d.validos + ' válidos; ' + d.rejeitados + ' rejeitados.</div>';
        } catch (x) {
            S.toast(x.message, 'error');
        } finally {
            S.loading(false);
        }
    };
}

/* EBS: URLs da API de consulta. Aplicado na próxima consulta, sem reiniciar. */
async function _renderEbs(S) {
    var host = document.getElementById('cm-ebs');
    if (!host) return;
    var d;
    try { d = await S.api('/parametros/ebs'); } catch (e) { host.innerHTML = '<div class="alert alert-danger">' + S.esc(e.message) + '</div>'; return; }
    var a = d.api || {};
    function campo(id, rotulo, val) {
        return '<div class="form-group"><label for="' + id + '">' + rotulo + '</label>' +
            '<input id="' + id + '" class="form-control" value="' + S.esc(val || '') + '" placeholder="https://…"></div>';
    }
    host.innerHTML =
        '<div class="form-grid cols-2">' +
            campo('ebs-login', 'API — URL de login', a.login_url) +
            campo('ebs-search', 'API — URL de busca', a.search_url) +
        '</div>' +
        '<div class="btn-row mt-2"><button id="ebs-salvar" class="btn btn-primary">Salvar</button></div>';
    var v = function (id) { return document.getElementById(id).value.trim(); };
    document.getElementById('ebs-salvar').onclick = async function () {
        try {
            await S.api('/parametros/ebs', { method: 'PUT', body: { login_url: v('ebs-login'), search_url: v('ebs-search') } });
            S.toast('EBS reconfigurado. Vale na próxima consulta.', 'success'); _renderEbs(S);
        } catch (e) { S.toast(e.message, 'error'); }
    };
}

/* Que palavra do modelo manda o ativo para qual bancada; prefixos de duplicidade. */
async function _renderFamilias(S) {
    var host = document.getElementById('cm-familias');
    if (!host) return;
    var c;
    try { c = await S.api('/recebimento/familias'); } catch (e) { host.innerHTML = '<div class="alert alert-danger">' + S.esc(e.message) + '</div>'; return; }
    function area(id, rotulo, lista) {
        return '<div class="form-group"><label for="' + id + '">' + rotulo + '</label>' +
            '<textarea id="' + id + '" class="form-control" rows="3" placeholder="um por linha">' + S.esc((lista || []).join('\n')) + '</textarea></div>';
    }
    host.innerHTML = '<div class="form-grid cols-2">' +
        area('fam-frota', 'Mobilidade (coletor, sled…)', c.frota) +
        area('fam-conect', 'Conectividade (AP, switch…)', c.conectividade) +
        area('fam-dup', 'Prefixos de duplicidade entre empresas', c.prefixos_duplicidade) + '</div>' +
        '<button id="fam-salvar" class="btn btn-primary mt-2">Salvar</button>';
    document.getElementById('fam-salvar').onclick = async function () {
        var linhas = function (id) { return document.getElementById(id).value.split('\n').map(function (x) { return x.trim(); }).filter(Boolean); };
        try {
            await S.api('/parametros/config/recebimento_familias', { method: 'PUT', body: {
                frota: linhas('fam-frota'), conectividade: linhas('fam-conect'), prefixos_duplicidade: linhas('fam-dup') } });
            S.toast('Configuração salva.', 'success');
        } catch (e) { S.toast(e.message, 'error'); }
    };
}

/* Listas das telas de Entrada / Saída / Movimentação interna. */
async function _renderGestaoAtivos(S) {
    var host = document.getElementById('cm-ga');
    if (!host) return;
    var c;
    try { c = await S.api('/servicenow/gestao-ativos/config'); } catch (e) { host.innerHTML = '<div class="alert alert-danger">' + S.esc(e.message) + '</div>'; return; }
    function area(id, rotulo, lista) {
        return '<div class="form-group"><label for="' + id + '">' + rotulo + '</label>' +
            '<textarea id="' + id + '" class="form-control" rows="4" placeholder="um por linha">' + S.esc((lista || []).join('\n')) + '</textarea></div>';
    }
    host.innerHTML = '<div class="form-grid cols-2">' +
        area('ga-estoques', 'Estoques (stockroom)', c.estoques) +
        area('ga-corredores-cfg', 'Corredores e espaços sugeridos', c.corredores) +
        area('ga-anotacoes-cfg', 'Anotações sugeridas na saída', c.anotacoes) + '</div>' +
        '<button id="ga-salvar" class="btn btn-primary mt-2">Salvar</button>';
    document.getElementById('ga-salvar').onclick = async function () {
        var linhas = function (id) { return document.getElementById(id).value.split('\n').map(function (x) { return x.trim(); }).filter(Boolean); };
        try {
            await S.api('/parametros/config/gestao_ativos', { method: 'PUT', body: {
                estoques: linhas('ga-estoques'), corredores: linhas('ga-corredores-cfg'), anotacoes: linhas('ga-anotacoes-cfg') } });
            S.toast('Configuração salva.', 'success');
        } catch (e) { S.toast(e.message, 'error'); }
    };
}

/* Colunas padrão da Consulta (cada usuário pode reduzir a sua). */
async function _renderConsultaColunas(S) {
    var host = document.getElementById('cm-consulta-cols');
    if (!host) return;
    var p;
    try { p = await S.api('/consulta/colunas'); } catch (e) { host.innerHTML = '<div class="alert alert-danger">' + S.esc(e.message) + '</div>'; return; }
    host.innerHTML = '';
    var grade = S.el('div', { style: 'display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:6px 16px' });
    p.disponiveis.forEach(function (c) {
        var l = S.el('label', { style: 'display:flex;gap:8px;align-items:center' });
        var cb = S.el('input', { type: 'checkbox', value: c.chave }); cb.checked = p.padrao.indexOf(c.chave) >= 0;
        l.appendChild(cb); l.appendChild(document.createTextNode(c.rotulo)); grade.appendChild(l);
    });
    host.appendChild(grade);
    host.appendChild(S.el('button', { className: 'btn btn-primary mt-2', textContent: 'Salvar', onClick: async function () {
        var sel = Array.from(grade.querySelectorAll('input:checked')).map(function (i) { return i.value; });
        if (!sel.length) { S.toast('Escolha ao menos uma coluna.', 'warning'); return; }
        try { await S.api('/parametros/config/consulta_colunas_padrao', { method: 'PUT', body: { colunas: sel } }); S.toast('Colunas padrão salvas.', 'success'); }
        catch (e) { S.toast(e.message, 'error'); }
    } }));
}

/* Marcação do recebimento no ServiceNow (dentro de Configuração Módulos). */
async function _renderRecebimentoSN(S) {
    var host = document.getElementById('cm-rec-sn');
    if (!host) return;
    var cfg = {};
    try {
        cfg = await S.api('/parametros/config/recebimento_servicenow') || {};
    } catch (e) {
        host.innerHTML = '<div class="alert alert-danger">Não foi possível carregar: ' +
            S.esc(e.message) + '</div>';
        return;
    }
    var ativo = cfg.ativo !== false;
    host.innerHTML =
        '<div class="form-group">' +
            '<label><input type="checkbox" id="rsn-ativo"' + (ativo ? ' checked' : '') +
            '> Marcar em estoque ao receber</label>' +
        '</div>' +
        '<div class="form-grid cols-2">' +
            '<div class="form-group"><label>Depósito (stockroom)</label>' +
                '<input id="rsn-stockroom" class="form-control" value="' +
                S.esc(cfg.stockroom || '') + '" placeholder="SPARE - CD324">' +
                '<small class="text-muted">padrão: SPARE - CD324</small>' +
            '</div>' +
            '<div class="form-group"><label>Estado (install_status)</label>' +
                '<input id="rsn-status" class="form-control" value="' +
                S.esc(cfg.install_status || '') + '" placeholder="6">' +
                '<small class="text-muted">padrão: 6 (Em estoque)</small>' +
            '</div>' +
        '</div>' +
        '<button class="btn btn-primary mt-2" id="rsn-salvar">Salvar</button>';

    document.getElementById('rsn-salvar').onclick = async function () {
        try {
            S.loading(true);
            await S.api('/parametros/config/recebimento_servicenow', {
                method: 'PUT',
                body: {
                    ativo: document.getElementById('rsn-ativo').checked,
                    stockroom: document.getElementById('rsn-stockroom').value.trim(),
                    install_status: document.getElementById('rsn-status').value.trim()
                }
            });
            S.toast('Configuração salva.', 'success');
        } catch (x) {
            S.toast(x.message, 'error');
        } finally {
            S.loading(false);
        }
    };
}

/* Editor da config dos Indicadores (dentro de Configuração Módulos). */
var _IND_CAMPOS = [
    ['state_aberto',        'Estados "Aberto" (Backlog / Localidade / Status / Priorizados)', 'ex.: 1,2,3'],
    ['state_atendimento',   'Estados "AG. Atendimento"', 'ex.: 1,2'],
    ['state_resolvido',     'Estados "Tratado/Resolvido"', 'ex.: 6,7'],
    ['state_cancelado',     'Estado "Cancelado"', 'ex.: 8'],
    ['resolved_date_field', 'Campo de data — Tratado por mês', 'ex.: closed_at'],
    ['backlog_date_field',  'Campo de data — Backlog por mês', 'ex.: u_data_bouncing'],
    ['status_field',        'Campo agrupador — "Abertos por status"', 'ex.: state ou u_stage_spare'],
    ['bu_field',            'Campo BU / empresa', 'ex.: company'],
    ['prioritized_query',   'Query "Priorizados" (encoded)', 'ex.: u_prioritized=true'],
    ['sub_sled_like',       'Subcategoria SLED (LIKE)', 'ex.: sled'],
    ['sub_coletor_like',    'Subcategoria Coletor (LIKE)', 'ex.: coletor']
];

async function _renderIndicadoresConfig(S) {
    var host = document.getElementById('cm-ind-form');
    if (!host) return;
    var cfg;
    try {
        cfg = await S.api('/indicadores/config');
    } catch (e) {
        host.innerHTML = '<div class="alert alert-danger">Não foi possível carregar a config dos indicadores: ' +
            S.esc(e.message) + '</div>';
        return;
    }
    var ef = cfg.efetiva || {}, def = cfg.defaults || {};
    var html = '<div class="form-grid cols-2">';
    _IND_CAMPOS.forEach(function (f) {
        var key = f[0], label = f[1], ph = f[2];
        var val = ef[key] != null ? ef[key] : '';
        html += '<div class="form-group">' +
            '<label>' + S.esc(label) + '</label>' +
            '<input id="ind-' + key + '" class="form-control" value="' + S.esc(val) + '" placeholder="' + S.esc(ph) + '">' +
            '<small class="text-muted">padrão: ' + S.esc(def[key] || '(vazio)') + '</small>' +
            '</div>';
    });
    html += '</div>' +
        '<div class="mt-2"><button id="ind-save" class="btn btn-primary">Salvar filtros dos indicadores</button>' +
        '<span id="ind-save-msg" class="text-muted" style="margin-left:10px"></span></div>';
    host.innerHTML = html;

    document.getElementById('ind-save').onclick = async function () {
        var payload = {};
        _IND_CAMPOS.forEach(function (f) {
            payload[f[0]] = document.getElementById('ind-' + f[0]).value.trim();
        });
        try {
            S.loading(true);
            await S.api('/indicadores/config', { method: 'PUT', body: payload });
            document.getElementById('ind-save-msg').textContent =
                'Salvo. Abra os Indicadores e clique em Atualizar para recalcular.';
            S.toast('Filtros dos indicadores salvos.', 'success');
        } catch (e) {
            S.toast(e.message, 'error');
        } finally {
            S.loading(false);
        }
    };
}

/* ── Automações (encerramento/encaminhamento) ───────────────────── */
async function renderAutomacoes(c, S) {
    // A aba é de todos, e as REGRAS também: qualquer usuário cria, edita e
    // exclui regra, e roda a rotina (que age no ServiceNow com a sessão de
    // quem clicou). Só a CONFIGURAÇÃO (campo do rastreio) pede "Administrar"
    // no módulo Automações.
    var usuario = S.user() || {};
    var permAutom = (usuario.permission_map || {}).automacoes || {};
    var ehAdmin = !!(usuario.is_admin || permAutom.can_admin);
    c.innerHTML =
        '<h1 class="page-title">Automações</h1>' +
        '<div class="card mb-3"><div class="card-header">' +
            (ehAdmin ? 'Configuração da rotina' : 'Rotina') + '</div>' +
            '<div class="card-body" id="au-cfg"><div class="spinner-inline">' +
            '<span class="spinner spinner-sm"></span> Carregando…</div></div></div>' +
        '<div class="card mb-3"><div class="card-header" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">' +
            '<span>Regras (subcategoria → ação)</span>' +
            '<button id="au-regra-add" class="btn btn-sm btn-primary">Nova regra</button>' +
            '</div>' +
            '<div class="card-body" id="au-regras"></div></div>' +
        '<div class="card"><div class="card-header" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">' +
            '<span>Logs</span>' +
            '<span><input id="au-log-q" class="form-control form-control-inline" ' +
                'placeholder="Buscar chamado/motivo" style="min-width:220px"> ' +
            '<button id="au-log-refresh" class="btn btn-sm btn-secondary">Atualizar</button></span></div>' +
            '<div class="card-body" id="au-logs"></div></div>';

    // ── Config ──
    // Não há agendador nem conta de serviço: a rotina só roda pelo botão,
    // com a sessão de quem clicou, e os apontamentos saem em nome dele.
    async function loadCfg() {
        var cfg = await S.api('/automacoes/config');
        var host = document.getElementById('au-cfg');
        var ultima = cfg.ultima_execucao
            ? (S.esc(cfg.ultima_execucao) + (cfg.ultimo_usuario ? ' por ' + S.esc(cfg.ultimo_usuario) : ''))
            : '—';
        var aviso =
            '<p class="text-muted" style="margin:0 0 12px;font-size:.85rem">' +
            'A rotina busca os rastreios dos chamados da fila e aplica as regras ' +
            '<strong>com o seu usuário do ServiceNow</strong>: encerramentos e ' +
            'encaminhamentos ficam registrados em seu nome. Não há execução automática.</p>';
        if (cfg.somente_leitura) {
            host.innerHTML = aviso +
                '<div class="form-grid cols-2">' +
                    '<div class="form-group"><label>Campo do rastreio no incidente</label>' +
                        '<div style="padding-top:6px">' + S.esc(cfg.tracking_field || 'sys_tags') + '</div></div>' +
                    '<div class="form-group"><label>Última execução</label>' +
                        '<div style="padding-top:6px;font-size:.85rem;color:var(--text-secondary)">' + ultima + '</div></div>' +
                '</div>' +
                '<div class="mt-2"><button id="au-run" class="btn btn-primary">Executar agora</button></div>';
            ligarBotaoRodar();
            return;
        }
        host.innerHTML = aviso +
            '<div class="form-grid cols-2">' +
                '<div class="form-group"><label>Campo do rastreio no incidente</label>' +
                    '<input id="au-tfield" class="form-control" value="' + S.esc(cfg.tracking_field || 'sys_tags') + '">' +
                    '<small class="text-muted">padrão: sys_tags</small></div>' +
                '<div class="form-group"><label>Última execução</label>' +
                    '<div style="font-size:.85rem;color:var(--text-secondary);padding-top:8px">' + ultima + '</div></div>' +
            '</div>' +
            '<div class="mt-2"><button id="au-cfg-save" class="btn btn-secondary">Salvar configuração</button> ' +
            '<button id="au-run" class="btn btn-primary" style="margin-left:8px">Executar agora</button> ' +
            '<span id="au-cfg-msg" class="text-muted" style="margin-left:10px"></span></div>';

        document.getElementById('au-cfg-save').onclick = async function () {
            try {
                await S.api('/automacoes/config', { method: 'PUT', body: {
                    tracking_field: document.getElementById('au-tfield').value.trim()
                }});
                document.getElementById('au-cfg-msg').textContent = 'Configuração salva.';
                S.toast('Configuração salva.', 'success');
                loadCfg();
            } catch (e) { S.toast(e.message, 'error'); }
        };
        ligarBotaoRodar();
    }

    function ligarBotaoRodar() {
        var btn = document.getElementById('au-run');
        if (!btn) return;
        btn.onclick = async function () {
            if (!confirm('Executar a rotina agora com o SEU usuário do ServiceNow? Os apontamentos sairão em seu nome.')) return;
            var b = this; b.disabled = true; var t = b.textContent; b.textContent = 'Rodando…';
            try {
                var d = await S.api('/automacoes/run', { method: 'POST' });
                var r = d.resumo || {};
                S.toast('Rotina: ' + (r.encerrados || 0) + ' encerrado(s), ' + (r.encaminhados || 0) +
                    ' encaminhado(s), ' + (r.erros || 0) + ' erro(s).', 'success');
                loadLogs();
            } catch (e) { S.toast(e.message, 'error'); }
            finally { b.disabled = false; b.textContent = t; }
        };
    }

    // ── Regras ──
    async function loadRegras() {
        var d = await S.api('/automacoes/regras');
        var cols = [
            { key: 'nome', label: 'Nome' },
            { key: 'acao', label: 'Ação' },
            { key: 'fila_destino', label: 'Fila destino' },
            { key: 'ativo', label: 'Ativa', render: function (v) { return v ? 'Sim' : 'Não'; } }
        ];
        cols.push(
            { key: 'a', label: '', render: function (_, r) {
                var w = S.el('div', { className: 'btn-row' });
                var e = S.el('button', { className: 'btn btn-sm btn-outline', textContent: 'Editar' });
                var x = S.el('button', { className: 'btn btn-sm btn-outline-danger', textContent: 'Excluir' });
                e.onclick = function () { editRegra(r); };
                x.onclick = function () {
                    if (!confirm('Excluir a regra "' + (r.nome || '') + '"?')) return;
                    S.api('/automacoes/regras/' + r.id, { method: 'DELETE' })
                        .then(function () { S.toast('Regra excluída.', 'success'); loadRegras(); })
                        .catch(function (er) { S.toast(er.message, 'error'); });
                };
                w.append(e, x);
                return w;
            }}
        );
        var host = document.getElementById('au-regras');
        host.innerHTML = '';
        host.appendChild(S.table(cols, d.regras));
    }

    function editRegra(r) {
        r = r || {};
        var f = S.el('div');
        f.innerHTML =
            '<div class="form-group"><label>Nome</label>' +
                '<input id="ar-nome" class="form-control" value="' + S.esc(r.nome || '') + '"></div>' +
            '<div class="form-group"><label>Subcategorias (uma por linha ou separadas por ;)</label>' +
                '<textarea id="ar-subs" class="form-control" rows="4">' + S.esc(r.subcategorias || '') + '</textarea></div>' +
            '<div class="form-grid cols-2">' +
                '<div class="form-group"><label>Ação</label>' +
                    '<select id="ar-acao" class="form-control">' +
                        '<option value="encerrar"' + (r.acao !== 'encaminhar' ? ' selected' : '') + '>Encerrar</option>' +
                        '<option value="encaminhar"' + (r.acao === 'encaminhar' ? ' selected' : '') + '>Encaminhar p/ fila</option>' +
                    '</select></div>' +
                '<div class="form-group"><label>Fila destino (se encaminhar)</label>' +
                    '<input id="ar-fila" class="form-control" value="' + S.esc(r.fila_destino || '') + '"></div>' +
            '</div>' +
            '<div class="form-group"><label>Mensagem (apontamento/close notes)</label>' +
                '<textarea id="ar-msg" class="form-control" rows="6">' + S.esc(r.mensagem || '') + '</textarea></div>' +
            '<div class="form-grid cols-2">' +
                '<div class="form-group"><label>Ordem</label>' +
                    '<input id="ar-ordem" type="number" class="form-control" value="' + S.esc(r.ordem != null ? r.ordem : 100) + '"></div>' +
                '<div class="form-group"><label style="display:flex;align-items:center;gap:8px;cursor:pointer;margin-top:26px">' +
                    '<input type="checkbox" id="ar-ativo"' + (r.ativo !== false ? ' checked' : '') + '> Ativa</label></div>' +
            '</div>';
        var save = S.el('button', { className: 'btn btn-primary', textContent: 'Salvar' });
        save.onclick = async function () {
            var body = {
                nome: document.getElementById('ar-nome').value.trim(),
                subcategorias: document.getElementById('ar-subs').value,
                acao: document.getElementById('ar-acao').value,
                fila_destino: document.getElementById('ar-fila').value.trim(),
                mensagem: document.getElementById('ar-msg').value,
                ordem: parseInt(document.getElementById('ar-ordem').value) || 100,
                ativo: document.getElementById('ar-ativo').checked
            };
            try {
                await S.api('/automacoes/regras' + (r.id ? '/' + r.id : ''), {
                    method: r.id ? 'PUT' : 'POST', body: body
                });
                S.closeModal(); S.toast('Regra salva.', 'success'); loadRegras();
            } catch (e) { S.toast(e.message, 'error'); }
        };
        S.openModal(r.id ? 'Editar regra' : 'Nova regra', f, [save]);
    }

    // ── Logs ──
    async function loadLogs() {
        var q = document.getElementById('au-log-q').value.trim();
        var d = await S.api('/automacoes/logs?limit=500' + (q ? '&q=' + encodeURIComponent(q) : ''));
        var cols = [
            { key: 'executado_em', label: 'Quando', render: function (v) {
                return v ? new Date(v).toLocaleString('pt-BR') : ''; } },
            { key: 'origem', label: 'Origem' },
            { key: 'usuario', label: 'Usuário' },
            { key: 'number', label: 'Chamado' },
            { key: 'subcategoria', label: 'Subcat.' },
            { key: 'acao', label: 'Ação' },
            { key: 'fila_destino', label: 'Fila destino' },
            { key: 'resultado', label: 'Resultado', html: true, render: function (v) { return S.badge(v); } },
            { key: 'detalhe', label: 'Detalhe' }
        ];
        var host = document.getElementById('au-logs');
        host.innerHTML = '';
        host.appendChild(S.table(cols, d.logs));
    }

    document.getElementById('au-regra-add').onclick = function () { editRegra(); };
    document.getElementById('au-log-refresh').onclick = loadLogs;
    document.getElementById('au-log-q').addEventListener('keydown', function (e) {
        if (e.key === 'Enter') loadLogs();
    });

    loadCfg(); loadRegras(); loadLogs();
}

/* ── Monitoramento (saúde e falhas) ─────────────────────────────── */
/* ── Cofre de segredos ──────────────────────────────────────────────
   O que o PROCESSO do portal alcança. Rodar o CLI no terminal responde
   sobre o seu usuário, não sobre o serviço — são ambientes diferentes, e
   no servidor só o serviço lê o cofre corporativo. Nenhum valor de
   segredo aparece: de cada chave se diz apenas se resolveu, de onde veio
   e quantos caracteres tem. */
async function renderCofre(c, S) {
    var e = S.esc;
    c.innerHTML =
        '<h1 class="page-title">Cofre de segredos</h1>' +
        '<p class="text-muted">O que o <b>processo do portal</b> alcança. ' +
            'Nenhum valor de segredo aparece aqui, só de onde veio e o tamanho.</p>' +
        '<div class="card mb-3">' +
            '<div class="card-header">Situação</div>' +
            '<div class="card-body" id="cf-situacao">' +
                '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div>' +
            '</div>' +
            '<div class="card-footer btn-row">' +
                '<button id="cf-atualizar" class="btn btn-secondary btn-sm" type="button">Atualizar</button>' +
                '<button id="cf-correios" class="btn btn-primary btn-sm" type="button">Testar Correios</button>' +
            '</div>' +
        '</div>' +
        '<div class="card mb-3">' +
            '<div class="card-header">Sondar nomes</div>' +
            '<div class="card-body">' +
                '<p class="text-muted" style="margin-top:0">O cofre corporativo responde por ' +
                    'nome, uma chave de cada vez — não dá para listar. Cole os nomes ' +
                    'candidatos (vírgula, espaço ou um por linha) e veja quais respondem.</p>' +
                '<div class="form-group"><label for="cf-nomes">Nomes</label>' +
                    '<textarea id="cf-nomes" class="form-control" rows="3" ' +
                    'placeholder="CORREIOS_USUARIO, SN_API_USER"></textarea></div>' +
                '<div class="btn-row mt-3">' +
                    '<button id="cf-sondar" class="btn btn-primary" type="button">Sondar</button>' +
                '</div>' +
                '<div id="cf-sondagem" class="mt-3"></div>' +
            '</div>' +
        '</div>' +
        '<div class="card" id="cf-teste-card" hidden>' +
            '<div class="card-header">Teste dos Correios</div>' +
            '<div class="card-body" id="cf-teste"></div>' +
        '</div>';

    function selo(ok, sim, nao) {
        return '<span class="badge badge-' + (ok ? 'success' : 'danger') + '">' +
            e(ok ? sim : nao) + '</span>';
    }

    function tabelaChaves(itens) {
        return S.table([
            { key: 'chave', label: 'Chave' },
            { key: 'resolvida', label: 'Resolveu', html: true,
              render: function (v) { return selo(v, 'sim', 'não'); } },
            { key: 'fonte', label: 'De onde veio' },
            { key: 'tamanho', label: 'Tamanho', render: function (v) { return v || '—'; } },
            { key: 'sombreado', label: '', html: true, render: function (v) {
                // Sombreamento é a falha silenciosa clássica: uma fonte
                // responde antes e a que alguém acabou de configurar nunca
                // é consultada.
                return v ? '<span class="badge badge-warning" title="A chave existe em mais de uma fonte; ' +
                    'vence a primeira da ordem">sombreada</span>' : '';
            } }
        ], itens);
    }

    async function carregar() {
        var alvo = document.getElementById('cf-situacao');
        var d;
        try { d = await S.api('/cofre/diagnostico'); }
        catch (x) {
            alvo.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            return;
        }
        var topo = S.el('div', { className: 'stats-grid mb-3' });
        [['Cofre corporativo', d.corporativo_ok ? 'alcança' : 'não alcança', d.corporativo_ok],
         ['Usuário do serviço', d.usuario_do_servico || '—', true],
         ['Cofre local', d.cofre_local_existe ? 'existe' : 'ausente', d.cofre_local_existe]
        ].forEach(function (x) {
            var cartao = S.el('div', { className: 'stat-card' + (x[2] ? '' : ' accent-orange') });
            cartao.appendChild(S.el('div', { className: 'stat-value', textContent: x[1] }));
            cartao.appendChild(S.el('div', { className: 'stat-label', textContent: x[0] }));
            topo.appendChild(cartao);
        });
        alvo.innerHTML = '';
        alvo.appendChild(topo);
        if (d.corporativo_detalhe) {
            alvo.appendChild(S.el('div', { className: 'alert alert-info mb-3',
                textContent: d.corporativo_detalhe }));
        }
        // Quando o cofre não alcança, o que resolve é permissão de arquivo:
        // dono, grupo e modo dizem exatamente o que pedir ao time. Quando
        // alcança, a lista de nomes responde "a chave existe com outro nome?".
        var inv = d.inventario || {};
        var ac = inv.acesso || {};
        if (ac.caminho) {
            var linhas = [
                ['Arquivo do cofre', ac.caminho],
                ['Situação', !ac.existe ? 'não existe neste caminho'
                    : (ac.legivel ? 'legível por este serviço' : 'existe, mas sem permissão de leitura')],
                ['Dono / grupo / modo', ac.existe
                    ? (ac.dono || '?') + ' / ' + (ac.grupo || '?') + ' / ' + (ac.modo || '?') : '—'],
                ['Serviço roda como', (ac.usuario_atual || '—') +
                    ((ac.grupos_atuais || []).length ? ' (' + ac.grupos_atuais.join(', ') + ')' : '')],
                ['Nomes no cofre', inv.sabe_listar ? (inv.nomes || []).join(', ')
                    : 'não dá para listar daqui — sonde por nome abaixo']
            ];
            var dl = S.el('div', { className: 'card mb-3' });
            dl.appendChild(S.el('div', { className: 'card-header', textContent: 'Acesso ao cofre corporativo' }));
            var corpo = S.el('div', { className: 'card-body' });
            corpo.appendChild(S.table([
                { key: 'o', label: 'O quê' },
                { key: 'q', label: 'Qual' }
            ], linhas.map(function (l) { return { o: l[0], q: l[1] }; })));
            if (ac.erro) {
                corpo.appendChild(S.el('div', { className: 'alert alert-warning mt-3', textContent: ac.erro }));
            }
            dl.appendChild(corpo);
            alvo.appendChild(dl);
        }
        (d.grupos || []).forEach(function (g) {
            alvo.appendChild(S.el('h2', { className: 'page-title', style: 'font-size:15px;margin:18px 0 8px',
                textContent: g.nome }));
            alvo.appendChild(tabelaChaves(g.chaves || []));
        });
        var erros = Object.keys(d.erros || {});
        if (erros.length) {
            alvo.appendChild(S.el('div', { className: 'alert alert-warning mt-3',
                textContent: 'Não consegui checar: ' + erros.join(', ') }));
        }
    }

    document.getElementById('cf-atualizar').onclick = carregar;

    document.getElementById('cf-sondar').onclick = async function () {
        var nomes = document.getElementById('cf-nomes').value;
        var saida = document.getElementById('cf-sondagem');
        if (!nomes.trim()) { S.toast('Informe ao menos um nome.', 'warning'); return; }
        saida.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Sondando…</div>';
        try {
            var d = await S.api('/cofre/sondar-varios', { method: 'POST', body: { nomes: nomes } });
            saida.innerHTML = '';
            saida.appendChild(S.el('p', { className: 'text-muted',
                textContent: d.resolvidas + ' de ' + d.total + ' responderam.' }));
            saida.appendChild(tabelaChaves(d.itens || []));
        } catch (x) {
            saida.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
        }
    };

    document.getElementById('cf-correios').onclick = async function () {
        var card = document.getElementById('cf-teste-card');
        var saida = document.getElementById('cf-teste');
        card.hidden = false;
        saida.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Testando…</div>';
        try {
            var d = await S.api('/cofre/testar-correios', { method: 'POST' });
            saida.innerHTML = '<div class="alert alert-' + (d.ok ? 'success' : 'danger') + '">' +
                e(d.detalhe || (d.ok ? 'Credencial aceita.' : 'Credencial recusada.')) + '</div>';
        } catch (x) {
            saida.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
        }
    };

    carregar();
}


/* ── Gestão de Compras ──────────────────────────────────────────────
   O portal não fala com a base do EBS: quem fala é o módulo
   /gestao_compras (Apache), que já expõe PO, projetos e acordos por
   HTTP. Aqui o portal é cliente dele — e esta tela é o lugar de
   conferir se a credencial do cofre abre a sessão de lá.
   ─────────────────────────────────────────────────────────────────── */
async function renderCompras(c, S) {
    var e = S.esc;
    c.innerHTML =
        '<h1 class="page-title">Gestão de Compras</h1>' +
        '<p class="text-muted">O portal consulta PO, projetos e acordos pelo módulo ' +
            '<span class="om-mono">/gestao_compras</span>, como cliente HTTP. ' +
            'A credencial vem do cofre — nada é digitado nesta tela.</p>' +
        '<div class="card mb-3">' +
            '<div class="card-header">Situação</div>' +
            '<div class="card-body" id="gc-situacao">' +
                '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div>' +
            '</div>' +
            '<div class="card-footer btn-row">' +
                '<button id="gc-atualizar" class="btn btn-secondary btn-sm" type="button">Atualizar</button>' +
                '<button id="gc-testar" class="btn btn-primary btn-sm" type="button">Testar login</button>' +
            '</div>' +
        '</div>' +
        '<div class="card mb-3" id="gc-teste-card" hidden>' +
            '<div class="card-header">Resultado do teste</div>' +
            '<div class="card-body" id="gc-teste"></div>' +
        '</div>' +
        '<div class="card">' +
            '<div class="card-header">Consulta</div>' +
            '<div class="card-body">' +
                '<div class="filter-grid">' +
                    '<div class="form-group"><label for="gc-acao">Ação</label>' +
                        '<select id="gc-acao" class="form-control"></select></div>' +
                    '<div class="form-group" data-gc="project"><label for="gc-project">Projeto (segment1)</label>' +
                        '<input id="gc-project" class="form-control" placeholder="ex.: 26.0123"></div>' +
                    '<div class="form-group" data-gc="po"><label for="gc-po">Número da PO</label>' +
                        '<input id="gc-po" class="form-control"></div>' +
                    '<div class="form-group" data-gc="line"><label for="gc-line">Linha (opcional)</label>' +
                        '<input id="gc-line" class="form-control" type="number" min="1"></div>' +
                    '<div class="form-group" data-gc="vendor"><label for="gc-vendor">Fornecedor</label>' +
                        '<input id="gc-vendor" class="form-control"></div>' +
                    '<div class="form-group" data-gc="days"><label for="gc-days">Dias até vencer</label>' +
                        '<input id="gc-days" class="form-control" type="number" value="90" min="1"></div>' +
                    '<div class="form-group" data-gc="org"><label for="gc-org">Unidade (opcional)</label>' +
                        '<input id="gc-org" class="form-control"></div>' +
                    '<div class="form-group" data-gc="projects"><label for="gc-projects">Projetos (vírgula)</label>' +
                        '<input id="gc-projects" class="form-control"></div>' +
                '</div>' +
                '<div class="btn-row mt-3">' +
                    '<button id="gc-consultar" class="btn btn-primary" type="button">Consultar</button>' +
                '</div>' +
                '<div id="gc-resultado" class="mt-3"></div>' +
            '</div>' +
        '</div>';

    var acoes = {};

    // Cada ação tem os próprios parâmetros; os campos que não servem somem,
    // em vez de ficarem lá convidando a preencher o que o módulo ignora.
    function mostrarCampos() {
        var usados = acoes[document.getElementById('gc-acao').value] || [];
        Array.prototype.forEach.call(c.querySelectorAll('[data-gc]'), function (el) {
            el.hidden = usados.indexOf(el.getAttribute('data-gc')) === -1;
        });
    }

    function selo(ok, sim, nao) {
        return '<span class="badge badge-' + (ok ? 'success' : 'danger') + '">' +
            e(ok ? sim : nao) + '</span>';
    }

    async function carregar() {
        var alvo = document.getElementById('gc-situacao');
        var d;
        try { d = await S.api('/gestao-compras/situacao'); }
        catch (x) {
            alvo.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            return;
        }
        var cr = d.credenciais || {};
        acoes = d.acoes || {};
        var sel = document.getElementById('gc-acao');
        sel.innerHTML = Object.keys(acoes).map(function (a) {
            return '<option value="' + e(a) + '">' + e(a) + '</option>';
        }).join('');
        sel.onchange = mostrarCampos;
        mostrarCampos();

        alvo.innerHTML =
            '<p><b>Módulo:</b> <span class="om-mono">' + e(d.url) + '</span> ' +
                '<span class="text-muted">— timeout ' + e(d.timeout) + ' s, TLS ' +
                (d.verify_ssl ? 'verificado' : 'sem verificação') +
                (d.proxy ? ', proxy ' + e(d.proxy) : ', saída direta') + '</span></p>' +
            '<p><b>Usuário:</b> ' + (cr.usuario
                ? '<span class="om-mono">' + e(cr.usuario) + '</span> <span class="text-muted">(' +
                  e(cr.usuario_chave) + ', ' + e(cr.usuario_fonte) + ')</span>'
                : selo(false, '', 'não definido')) +
            ' &nbsp; <b>Senha:</b> ' + (cr.senha_definida
                ? selo(true, 'definida', '') + ' <span class="text-muted">(' +
                  e(cr.senha_chave) + ', ' + e(cr.senha_fonte) + ')</span>'
                : selo(false, '', 'não definida')) + '</p>' +
            (cr.usuario && cr.senha_definida ? '' :
                '<div class="alert alert-warning mb-0">Grave no cofre: ' +
                '<span class="om-mono">python3 scripts/cofre.py definir GESTAO_COMPRAS_USER</span> ' +
                'e <span class="om-mono">GESTAO_COMPRAS_PASS</span>.</div>');
    }

    document.getElementById('gc-atualizar').onclick = carregar;

    document.getElementById('gc-testar').onclick = async function () {
        var card = document.getElementById('gc-teste-card');
        var saida = document.getElementById('gc-teste');
        card.hidden = false;
        saida.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Entrando no módulo…</div>';
        try {
            var d = await S.api('/gestao-compras/testar', { method: 'POST' });
            var u = d.usuario || {};
            saida.innerHTML = '<div class="alert alert-success">Sessão aberta como <b>' +
                e(u.username || '?') + '</b>' + (u.role ? ' (' + e(u.role) + ')' : '') + '.</div>';
            S.toast('O módulo aceitou a credencial.', 'success');
        } catch (x) {
            saida.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            S.toast(x.message, 'error');
        }
    };

    document.getElementById('gc-consultar').onclick = async function () {
        var saida = document.getElementById('gc-resultado');
        var acao = document.getElementById('gc-acao').value;
        if (!acao) { S.toast('Escolha uma ação.', 'warning'); return; }
        var q = { acao: acao };
        (acoes[acao] || []).forEach(function (nome) {
            var campo = document.getElementById('gc-' + nome);
            var v = campo ? String(campo.value).trim() : '';
            if (v) q[nome] = v;
        });
        saida.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Consultando pelo módulo…</div>';
        try {
            var d = await S.api('/gestao-compras/consultar?' + new URLSearchParams(q).toString());
            saida.innerHTML = '';
            if (!d.total) {
                saida.appendChild(S.el('p', { className: 'text-muted',
                    textContent: 'Nenhuma linha (' + d.ms + ' ms).' }));
                return;
            }
            saida.appendChild(S.el('p', { className: 'text-muted',
                textContent: d.total + ' linha(s) em ' + d.ms + ' ms, pela API do módulo.' }));
            saida.appendChild(S.table(d.colunas.map(function (col) {
                return { key: col, label: col, render: function (v) { return v == null ? '' : v; } };
            }), d.linhas));
        } catch (x) {
            saida.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            S.toast(x.message, 'error');
        }
    };

    carregar();
}


/* ── Base EBS ───────────────────────────────────────────────────────
   Leitura direta da base do EBS. Existe em paralelo com a aba Gestão
   de Compras: as consultas são as mesmas, pelos mesmos nomes — muda o
   caminho por onde o dado vem. Aqui, conexão direta; lá, HTTP pelo
   módulo do outro time.

   O que a tela executa são as consultas NOMEADAS. SQL digitado não
   existe: o SQL fica no código, versionado e revisável.
   ─────────────────────────────────────────────────────────────────── */
async function renderBaseEbs(c, S) {
    var e = S.esc;
    c.innerHTML =
        '<h1 class="page-title">Base EBS</h1>' +
        '<p class="text-muted">Leitura direta da base do EBS, só-leitura, com teto de linhas e ' +
            'de tempo. A credencial vem do cofre — endereço, usuário e senha não aparecem ' +
            'nesta tela nem ficam no repositório.</p>' +
        '<div class="card mb-3">' +
            '<div class="card-header">Situação</div>' +
            '<div class="card-body" id="eo-situacao">' +
                '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div>' +
            '</div>' +
            '<div class="card-footer btn-row">' +
                '<button id="eo-atualizar" class="btn btn-secondary btn-sm" type="button">Atualizar</button>' +
                '<button id="eo-testar" class="btn btn-primary btn-sm" type="button">Testar conexão</button>' +
            '</div>' +
        '</div>' +
        '<div class="card mb-3" id="eo-teste-card" hidden>' +
            '<div class="card-header">Resultado do teste</div>' +
            '<div class="card-body" id="eo-teste"></div>' +
        '</div>' +
        '<div class="card mb-3">' +
            '<div class="card-header">Consulta</div>' +
            '<div class="card-body">' +
                '<div class="filter-grid">' +
                    '<div class="form-group"><label for="eo-nome">Consulta</label>' +
                        '<select id="eo-nome" class="form-control"></select></div>' +
                    '<div class="form-group"><label for="eo-limite">Máximo de linhas</label>' +
                        '<input id="eo-limite" class="form-control" type="number" value="200" ' +
                        'min="1" max="5000"></div>' +
                '</div>' +
                '<div id="eo-binds" class="filter-grid mt-2"></div>' +
                '<div class="btn-row mt-3">' +
                    '<button id="eo-rodar" class="btn btn-primary" type="button">Executar</button>' +
                '</div>' +
                '<div id="eo-resultado" class="mt-3"></div>' +
            '</div>' +
        '</div>' +
        '<div class="card">' +
            '<div class="card-header">Procurar objeto</div>' +
            '<div class="card-body">' +
                '<p class="text-muted mt-0">Lista tabelas e views que a conta enxerga. ' +
                    'Só catálogo — nenhum dado de negócio é lido aqui.</p>' +
                '<div class="filter-grid">' +
                    '<div class="form-group"><label for="eo-prefixo">Prefixo (mín. 3 letras)</label>' +
                        '<input id="eo-prefixo" class="form-control" placeholder="ex.: PO_HEADERS"></div>' +
                    '<div class="form-group"><label for="eo-owner">Owner</label>' +
                        '<input id="eo-owner" class="form-control" value="APPS"></div>' +
                '</div>' +
                '<div class="btn-row mt-3">' +
                    '<button id="eo-buscar" class="btn btn-primary" type="button">Procurar</button>' +
                '</div>' +
                '<div id="eo-objetos" class="mt-3"></div>' +
            '</div>' +
        '</div>';

    var consultas = {};

    function selo(ok, sim, nao) {
        return '<span class="badge badge-' + (ok ? 'success' : 'danger') + '">' +
            e(ok ? sim : nao) + '</span>';
    }

    // Cada consulta tem os próprios parâmetros; o formulário se refaz a
    // cada escolha, em vez de oferecer campo que a consulta ignora.
    function montarBinds() {
        var nome = document.getElementById('eo-nome').value;
        var q = consultas[nome] || { binds: [] };
        var host = document.getElementById('eo-binds');
        host.innerHTML = q.binds.map(function (b) {
            return '<div class="form-group"><label for="eo-b-' + e(b) + '">' + e(b) + '</label>' +
                '<input id="eo-b-' + e(b) + '" class="form-control" data-bind="' + e(b) + '"></div>';
        }).join('') || '<p class="text-muted mb-0">Esta consulta não pede parâmetro.</p>';
    }

    async function carregar() {
        var alvo = document.getElementById('eo-situacao');
        var d;
        try { d = await S.api('/ebs-oracle/situacao'); }
        catch (x) {
            alvo.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            return;
        }
        alvo.innerHTML = '';
        var topo = S.el('div', { className: 'stats-grid mb-3' });
        [['Credencial', d.completo ? 'completa' : 'incompleta', d.completo],
         ['Driver Oracle', d.driver.instalado ? ('instalado ' + (d.driver.versao || '')) : 'ausente',
          d.driver.instalado],
         ['Cofre corporativo', d.cofre_corporativo ? 'alcança' : 'não alcança', d.cofre_corporativo]
        ].forEach(function (x) {
            var cart = S.el('div', { className: 'stat-card' + (x[2] ? '' : ' accent-orange') });
            cart.appendChild(S.el('div', { className: 'stat-value', textContent: x[1] }));
            cart.appendChild(S.el('div', { className: 'stat-label', textContent: x[0] }));
            topo.appendChild(cart);
        });
        alvo.appendChild(topo);

        alvo.appendChild(S.table([
            { key: 'rotulo', label: 'O quê' },
            { key: 'chave', label: 'Chave no cofre' },
            { key: 'resolvida', label: 'Resolveu', html: true,
              render: function (v) { return selo(v, 'sim', 'não'); } },
            { key: 'fonte', label: 'De onde veio' },
            { key: 'valor', label: 'Valor',
              render: function (v) { return v || '—'; } },
            { key: 'no_local', label: '', html: true, render: function (v, linha) {
                // Sombreamento: o cofre local vence o corporativo, então um
                // valor velho ali derruba o certo sem ninguém ver.
                return (v && !linha.no_corporativo)
                    ? '<span class="badge badge-warning" title="Só o cofre local tem esta chave">só no local</span>'
                    : '';
            } }
        ], d.chaves));

        if (!d.driver.instalado) {
            alvo.appendChild(S.el('div', { className: 'alert alert-warning mt-3',
                textContent: d.driver.detalhe || 'Driver Oracle ausente neste servidor.' }));
        }
        if (!d.completo) {
            alvo.appendChild(S.el('div', { className: 'alert alert-info mt-3',
                textContent: 'Grave o que falta com: python3 scripts/cofre.py definir <CHAVE>' }));
        }
        if (d.cofre_detalhe) {
            alvo.appendChild(S.el('p', { className: 'text-muted mb-0 mt-3',
                textContent: 'Cofre: ' + d.cofre_detalhe }));
        }
    }

    async function carregarConsultas() {
        var sel = document.getElementById('eo-nome');
        try {
            var d = await S.api('/ebs-oracle/consultas');
            consultas = {};
            (d.consultas || []).forEach(function (q) { consultas[q.nome] = q; });
            sel.innerHTML = (d.consultas || []).map(function (q) {
                return '<option value="' + e(q.nome) + '">' + e(q.nome) + '</option>';
            }).join('');
            sel.onchange = montarBinds;
            montarBinds();
        } catch (x) {
            sel.innerHTML = '';
            document.getElementById('eo-binds').innerHTML =
                '<div class="alert alert-danger">' + e(x.message) + '</div>';
        }
    }

    document.getElementById('eo-atualizar').onclick = carregar;

    document.getElementById('eo-testar').onclick = async function () {
        var card = document.getElementById('eo-teste-card');
        var saida = document.getElementById('eo-teste');
        card.hidden = false;
        saida.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Conectando…</div>';
        try {
            var d = await S.api('/ebs-oracle/testar', { method: 'POST' });
            saida.innerHTML = '';
            saida.appendChild(S.el('div', { className: 'alert alert-success',
                textContent: 'Conectado.' }));
            saida.appendChild(S.table([
                { key: 'o', label: 'O quê' }, { key: 'q', label: 'Qual' }
            ], Object.keys(d.acesso || {}).map(function (k) {
                return { o: k, q: String(d.acesso[k]) };
            })));
            S.toast('A base respondeu.', 'success');
        } catch (x) {
            saida.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            S.toast(x.message, 'error');
        }
    };

    document.getElementById('eo-rodar').onclick = async function () {
        var saida = document.getElementById('eo-resultado');
        var nome = document.getElementById('eo-nome').value;
        if (!nome) { S.toast('Escolha uma consulta.', 'warning'); return; }
        var binds = {};
        Array.prototype.forEach.call(c.querySelectorAll('#eo-binds [data-bind]'), function (el) {
            var v = el.value.trim();
            if (v) binds[el.getAttribute('data-bind')] = v;
        });
        var limite = parseInt(document.getElementById('eo-limite').value, 10) || 200;
        saida.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Consultando…</div>';
        try {
            var d = await S.api('/ebs-oracle/consultar', {
                method: 'POST', body: { nome: nome, binds: binds, limite: limite }
            });
            saida.innerHTML = '';
            if (!d.total) {
                saida.appendChild(S.el('p', { className: 'text-muted',
                    textContent: 'Nenhuma linha (' + d.ms + ' ms).' }));
                return;
            }
            saida.appendChild(S.el('p', { className: 'text-muted',
                textContent: d.total + ' linha(s) em ' + d.ms + ' ms.' +
                    (d.truncado ? ' Cortado no limite de ' + d.limite + ' — pode haver mais.' : '') }));
            saida.appendChild(S.table(d.colunas.map(function (col) {
                return { key: col, label: col, render: function (v) { return v == null ? '' : v; } };
            }), d.linhas));
        } catch (x) {
            saida.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            S.toast(x.message, 'error');
        }
    };

    document.getElementById('eo-buscar').onclick = async function () {
        var saida = document.getElementById('eo-objetos');
        var prefixo = document.getElementById('eo-prefixo').value.trim();
        var owner = document.getElementById('eo-owner').value.trim() || 'APPS';
        if (prefixo.length < 3) { S.toast('Informe ao menos 3 letras.', 'warning'); return; }
        saida.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Procurando…</div>';
        try {
            var d = await S.api('/ebs-oracle/objetos?prefixo=' + encodeURIComponent(prefixo) +
                                '&owner=' + encodeURIComponent(owner));
            saida.innerHTML = '';
            if (!d.total) {
                saida.appendChild(S.el('p', { className: 'text-muted', textContent: 'Nada encontrado.' }));
                return;
            }
            saida.appendChild(S.table([
                { key: 'owner', label: 'Owner' },
                { key: 'object_name', label: 'Objeto' },
                { key: 'object_type', label: 'Tipo' }
            ], d.itens));
        } catch (x) {
            saida.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
        }
    };

    carregar();
    carregarConsultas();
}


async function renderMonitoramento(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Monitoramento</h1>' +
        '<div class="card mb-3"><div class="card-header" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">' +
            '<span>Saúde</span>' +
            '<span><button id="mo-refresh" class="btn btn-sm btn-secondary">Atualizar</button> ' +
            '<button id="mo-checar" class="btn btn-sm btn-primary" style="margin-left:6px">Checar integrações</button></span>' +
            '</div><div class="card-body" id="mo-saude">' +
            '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div></div></div>' +
        '<div class="card mb-3" id="mo-check-card" style="display:none">' +
            '<div class="card-header">Resultado da checagem</div>' +
            '<div class="card-body" id="mo-check"></div></div>' +
        '<div class="card"><div class="card-header" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">' +
            '<span>Falhas registradas</span>' +
            '<span>' +
              '<select id="mo-sev" class="form-control form-control-inline">' +
                '<option value="">Todas</option><option value="erro">Erros</option>' +
                '<option value="alerta">Alertas</option><option value="ok">OK</option></select> ' +
              '<select id="mo-org" class="form-control form-control-inline">' +
                '<option value="">Toda origem</option><option value="api">API</option>' +
                '<option value="integracao">Integração</option><option value="automacao">Automação</option></select> ' +
              '<input id="mo-q" class="form-control form-control-inline" placeholder="Buscar" style="min-width:170px"> ' +
              '<button id="mo-falhas-refresh" class="btn btn-sm btn-secondary">Filtrar</button>' +
            '</span></div>' +
            '<div class="card-body" id="mo-falhas"></div></div>';

    function barra(pct, alerta, critico) {
        var cor = pct >= critico ? 'var(--sp-alerta)' : (pct >= alerta ? 'var(--sp-gold)' : 'var(--sp-ok)');
        return '<div style="background:var(--sp-border-soft);height:8px;overflow:hidden;margin-top:6px">' +
            '<div style="height:100%;width:' + Math.min(100, pct) + '%;background:' + cor + '"></div></div>';
    }
    function tile(titulo, valor, sub, extra) {
        return '<div class="stat-card"><div class="stat-value" style="font-size:1.5rem">' + valor + '</div>' +
            '<div class="stat-label">' + S.esc(titulo) + '</div>' +
            (sub ? '<div class="text-muted" style="font-size:.78rem;margin-top:2px">' + sub + '</div>' : '') +
            (extra || '') + '</div>';
    }

    async function loadSaude() {
        var host = document.getElementById('mo-saude');
        try {
            var d = await S.api('/monitor/saude');
            var sv = d.servidor || {}, mem = sv.memoria || {}, dk = sv.disco || {},
                cg = sv.carga || {}, up = sv.uptime || {}, ap = d.aplicacao || {},
                lim = d.limiares || {}, f = d.falhas || {};
            var mapa = { ok: ['var(--sp-ok)', 'Tudo certo'], alerta: ['var(--sp-gold)', 'Atenção'], critico: ['var(--sp-alerta)', 'Crítico'] };
            var st = mapa[d.status] || mapa.ok;
            var html = '<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">' +
                '<span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:' + st[0] + '"></span>' +
                '<strong style="color:' + st[0] + '">' + st[1] + '</strong>' +
                '<span class="text-muted" style="font-size:.85rem">· atualizado ' +
                new Date(d.gerado_em).toLocaleString('pt-BR') + '</span></div>';
            html += '<div class="stats-grid">' +
                tile('Memória', mem.pct_usado + '%', mem.usado_mb + ' / ' + mem.total_mb + ' MB',
                     barra(mem.pct_usado, lim.mem_alerta, lim.mem_critico)) +
                tile('Disco', dk.pct_usado + '%', dk.usado_gb + ' / ' + dk.total_gb + ' GB (livre ' + dk.livre_gb + ' GB)',
                     barra(dk.pct_usado, lim.disco_alerta, lim.disco_critico)) +
                tile('Carga (1 min)', cg.load1, cg.cpus + ' CPU(s) · ' + cg.pct_load1 + '%') +
                tile('Uptime', up.aplicacao || '—', 'servidor: ' + (up.servidor || '—')) +
                tile('Processo', (ap.memoria_mb || 0) + ' MB', 'PID ' + (ap.pid || '—')) +
                tile('Falhas (' + (f.horas || 24) + 'h)', f.erros || 0,
                     (f.alertas || 0) + ' alerta(s)') +
                '</div>';
            html += '<div style="margin-top:14px;font-weight:600;font-size:.9rem">Bancos de dados</div>' +
                '<div class="tw" style="overflow-x:auto;margin-top:6px"><table class="data-table"><thead><tr>' +
                '<th>Banco</th><th>Tipo</th><th>Status</th><th>Tamanho</th><th>Detalhe</th></tr></thead><tbody>';
            (d.bancos || []).forEach(function (b) {
                html += '<tr><td><b>' + S.esc(b.nome) + '</b></td><td>' + S.esc(b.tipo) + '</td>' +
                    '<td>' + (b.ok ? '<span style="color:var(--sp-ok);font-weight:600">OK</span>'
                                   : '<span style="color:var(--sp-alerta);font-weight:600">FALHA</span>') + '</td>' +
                    '<td>' + (b.tamanho_mb ? b.tamanho_mb + ' MB' : '—') + '</td>' +
                    '<td style="font-size:.8rem;color:var(--text-secondary)">' + S.esc(b.detalhe || '') + '</td></tr>';
            });
            html += '</tbody></table></div>';
            host.innerHTML = html;
        } catch (e) {
            host.innerHTML = '<div class="alert alert-danger">' + S.esc(e.message) + '</div>';
        }
    }

    async function loadFalhas() {
        var host = document.getElementById('mo-falhas');
        var p = new URLSearchParams({
            limit: '300',
            severidade: document.getElementById('mo-sev').value,
            origem: document.getElementById('mo-org').value,
            q: document.getElementById('mo-q').value.trim()
        });
        try {
            var d = await S.api('/monitor/falhas?' + p);
            var cols = [
                { key: 'quando', label: 'Quando', render: function (v) {
                    return v ? new Date(v).toLocaleString('pt-BR') : ''; } },
                { key: 'severidade', label: 'Sev.', html: true, render: function (v) {
                    var cor = v === 'erro' ? 'var(--sp-alerta)' : (v === 'alerta' ? 'var(--sp-gold)' : 'var(--sp-ok)');
                    return '<span style="color:' + cor + ';font-weight:600">' + S.esc(v) + '</span>'; } },
                { key: 'origem', label: 'Origem' },
                { key: 'alvo', label: 'Alvo' },
                { key: 'status_code', label: 'HTTP', render: function (v) { return v || ''; } },
                { key: 'duracao_ms', label: 'ms', render: function (v) { return v || ''; } },
                { key: 'usuario', label: 'Usuário' },
                { key: 'detalhe', label: 'Detalhe' }
            ];
            host.innerHTML = '';
            host.appendChild(S.table(cols, d.eventos));
        } catch (e) {
            host.innerHTML = '<div class="alert alert-danger">' + S.esc(e.message) + '</div>';
        }
    }

    document.getElementById('mo-refresh').onclick = loadSaude;
    document.getElementById('mo-falhas-refresh').onclick = loadFalhas;
    document.getElementById('mo-q').addEventListener('keydown', function (e) {
        if (e.key === 'Enter') loadFalhas();
    });
    document.getElementById('mo-checar').onclick = async function () {
        var b = this; b.disabled = true; var t = b.textContent; b.textContent = 'Checando…';
        var card = document.getElementById('mo-check-card');
        var host = document.getElementById('mo-check');
        card.style.display = '';
        host.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Testando integrações…</div>';
        try {
            var d = await S.api('/monitor/checar', { method: 'POST' });
            var html = '<table class="data-table"><thead><tr><th>Serviço</th><th>Status</th><th>Tempo</th><th>Detalhe</th></tr></thead><tbody>';
            (d.resultados || []).forEach(function (r) {
                html += '<tr><td><b>' + S.esc(r.servico) + '</b></td>' +
                    '<td>' + (r.ok ? '<span style="color:var(--sp-ok);font-weight:600">OK</span>'
                                   : '<span style="color:var(--sp-alerta);font-weight:600">FALHA</span>') + '</td>' +
                    '<td>' + r.ms + ' ms</td>' +
                    '<td style="font-size:.8rem">' + S.esc(r.detalhe || '') + '</td></tr>';
            });
            host.innerHTML = html + '</tbody></table>';
            loadSaude(); loadFalhas();
        } catch (e) {
            host.innerHTML = '<div class="alert alert-danger">' + S.esc(e.message) + '</div>';
        } finally { b.disabled = false; b.textContent = t; }
    };

    loadSaude(); loadFalhas();
}

/* ── Acessos & Alertas (admin) ──────────────────────────────────────
   Tentativas de acesso negadas (credencial válida sem liberação e bloqueios
   dentro do portal) e configuração dos alertas por e-mail. */
async function renderAcessos(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Acessos &amp; Alertas</h1>' +

        '<div class="card mb-3"><div class="card-header" ' +
            'style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">' +
            '<span>Tentativas de acesso</span>' +
            '<span>' +
              '<select id="ac-tipo" class="form-control form-control-inline">' +
                '<option value="">Todas as tentativas</option>' +
                '<option value="nao_autorizado" selected>Sem liberação de acesso</option>' +
                '<option value="credencial">Credencial inválida</option></select> ' +
              '<select id="ac-dias" class="form-control form-control-inline">' +
                '<option value="7">7 dias</option><option value="30" selected>30 dias</option>' +
                '<option value="90">90 dias</option></select> ' +
              '<button id="ac-refresh" class="btn btn-sm btn-secondary">Atualizar</button>' +
            '</span></div>' +
            '<div class="card-body" id="ac-lista">' +
            '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div></div></div>' +

        '<div class="card mb-3"><div class="card-header">Usuários aguardando liberação</div>' +
            '<div class="card-body" id="ac-pendentes"></div></div>' +

        '<div class="card mb-3"><div class="card-header" ' +
            'style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">' +
            '<span>Controle de Orçamento — trilha de acesso</span>' +
            '<button id="ac-orc-refresh" class="btn btn-sm btn-secondary">Atualizar</button>' +
            '</div><div class="card-body" id="ac-orcamento"></div></div>' +

        '<div class="card"><div class="card-header">Alertas por e-mail</div>' +
            '<div class="card-body" id="ac-alertas">' +
            '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div></div></div>';

    async function loadAcessos() {
        var host = document.getElementById('ac-lista');
        var p = new URLSearchParams({
            dias: document.getElementById('ac-dias').value,
            tipo: document.getElementById('ac-tipo').value,
            limit: '300'
        });
        try {
            var d = await S.api('/monitor/acessos?' + p);
            var r = d.resumo || {};
            var html = '<div class="stats-grid" style="margin-bottom:12px">' +
                '<div class="stat-card"><div class="stat-value" style="font-size:1.5rem;color:var(--sp-gold)">' +
                    (r.nao_autorizado || 0) + '</div><div class="stat-label">Sem liberação</div></div>' +
                '<div class="stat-card"><div class="stat-value" style="font-size:1.5rem;color:var(--sp-alerta)">' +
                    (r.credencial || 0) + '</div><div class="stat-label">Credencial inválida</div></div>' +
                '<div class="stat-card"><div class="stat-value" style="font-size:1.5rem">' +
                    (r.bloqueios_403 || 0) + '</div><div class="stat-label">Bloqueios dentro do portal</div></div>' +
                '</div>';
            host.innerHTML = html;
            var cols = [
                { key: 'quando', label: 'Quando', render: function (v) {
                    return v ? new Date(v).toLocaleString('pt-BR') : ''; } },
                { key: 'login', label: 'Usuário' },
                { key: 'tipo', label: 'Tipo', html: true, render: function (v) {
                    var nao = v === 'nao_autorizado';
                    return '<span style="color:' + (nao ? 'var(--sp-gold)' : 'var(--sp-alerta)') + ';font-weight:600">' +
                        (nao ? 'Sem liberação' : 'Credencial') + '</span>'; } },
                { key: 'origem', label: 'Autenticação' },
                { key: 'ip', label: 'IP' },
                { key: 'detalhe', label: 'Detalhe' }
            ];
            host.appendChild(S.table(cols, d.tentativas || []));

            var ph = document.getElementById('ac-pendentes');
            ph.innerHTML = '';
            if (!(d.pendentes || []).length) {
                ph.innerHTML = '<p class="text-muted">Nenhum usuário aguardando liberação.</p>';
            } else {
                ph.appendChild(S.table([
                    { key: 'login', label: 'Usuário' },
                    { key: 'nome', label: 'Nome' },
                    { key: 'origem', label: 'Autenticação' },
                    { key: 'criado_em', label: 'Primeira tentativa', render: function (v) {
                        return v ? new Date(v).toLocaleString('pt-BR') : ''; } }
                ], d.pendentes));
                ph.insertAdjacentHTML('beforeend',
                    '<p class="text-muted mt-2">Libere o acesso em ' +
                    '<b>Configuração → Usuários e Permissões</b>.</p>');
            }
        } catch (e) {
            host.innerHTML = '<div class="alert alert-danger">' + S.esc(e.message) + '</div>';
        }
    }

    async function loadAlertas() {
        var host = document.getElementById('ac-alertas');
        try {
            var d = await S.api('/monitor/alertas');
            var seg = d.seguranca || 'none';
            function opt(v, r) {
                return '<option value="' + v + '"' + (seg === v ? ' selected' : '') + '>' + r + '</option>';
            }
            host.innerHTML =
                '<div class="form-grid cols-2">' +
                    '<div class="form-group"><label>Servidor SMTP</label>' +
                        '<input id="al-host" class="form-control" value="' + S.esc(d.host || '') + '"></div>' +
                    '<div class="form-group"><label>Porta</label>' +
                        '<input id="al-porta" type="number" class="form-control" value="' + (d.porta || 25) + '"></div>' +
                    '<div class="form-group"><label>Segurança</label>' +
                        '<select id="al-seg" class="form-control">' +
                        opt('none', 'Nenhuma') + opt('starttls', 'STARTTLS') + opt('ssl', 'SSL/TLS') +
                        '</select></div>' +
                    '<div class="form-group"><label>Usuário (opcional)</label>' +
                        '<input id="al-user" class="form-control" value="' + S.esc(d.usuario || '') + '"></div>' +
                    '<div class="form-group"><label>Senha ' +
                        (d.senha_definida
                            ? '<span class="text-muted">(guardada — fonte: ' + S.esc(d.senha_fonte) + ')</span>'
                            : '<span class="text-muted">(não definida)</span>') + '</label>' +
                        '<input id="al-senha" type="password" class="form-control" ' +
                        'placeholder="' + (d.senha_definida ? 'deixe em branco para manter' : 'opcional') + '"></div>' +
                    '<div class="form-group"><label>Remetente</label>' +
                        '<input id="al-rem" class="form-control" value="' + S.esc(d.remetente || '') + '"></div>' +
                    '<div class="form-group"><label>Destinatários (separados por vírgula)</label>' +
                        '<input id="al-dest" class="form-control" value="' + S.esc(d.destinatarios || '') + '"></div>' +
                    '<div class="form-group"><label>Intervalo mínimo por assunto (min)</label>' +
                        '<input id="al-int" type="number" class="form-control" value="' + (d.intervalo_min || 0) + '"></div>' +
                    '<div class="form-group"><label>Máximo de e-mails por hora</label>' +
                        '<input id="al-max" type="number" class="form-control" value="' + (d.max_por_hora || 20) + '"></div>' +
                '</div>' +
                '<div style="margin-top:10px;display:flex;flex-direction:column;gap:6px">' +
                    '<label><input type="checkbox" id="al-ativo"' + (d.ativo ? ' checked' : '') + '> ' +
                        'Canal de alertas ativo</label>' +
                    '<label><input type="checkbox" id="al-acesso"' + (d.alerta_acesso_negado ? ' checked' : '') + '> ' +
                        'Avisar tentativa de acesso sem liberação</label>' +
                    '<label><input type="checkbox" id="al-falhas"' + (d.alerta_falhas ? ' checked' : '') + '> ' +
                        'Avisar falhas de API, integrações e automações</label>' +
                    (d.senha_definida
                        ? '<label><input type="checkbox" id="al-limpar"> Remover a senha guardada</label>'
                        : '') +
                '</div>' +
                '<div style="margin-top:12px">' +
                    '<button id="al-save" class="btn btn-primary">Salvar</button> ' +
                    '<button id="al-test" class="btn btn-outline" style="margin-left:8px">Enviar e-mail de teste</button>' +
                '</div>' +
                '<div id="al-result" class="mt-2"></div>' +
                '';

            document.getElementById('al-save').onclick = async function () {
                var limpar = document.getElementById('al-limpar');
                var body = {
                    ativo: document.getElementById('al-ativo').checked,
                    host: document.getElementById('al-host').value,
                    porta: document.getElementById('al-porta').value,
                    seguranca: document.getElementById('al-seg').value,
                    usuario: document.getElementById('al-user').value,
                    senha: document.getElementById('al-senha').value,
                    senha_limpar: !!(limpar && limpar.checked),
                    remetente: document.getElementById('al-rem').value,
                    destinatarios: document.getElementById('al-dest').value,
                    alerta_acesso_negado: document.getElementById('al-acesso').checked,
                    alerta_falhas: document.getElementById('al-falhas').checked,
                    intervalo_min: document.getElementById('al-int').value,
                    max_por_hora: document.getElementById('al-max').value
                };
                try {
                    await S.api('/monitor/alertas', { method: 'PUT', body: body });
                    S.toast('Configuração de alertas salva.', 'success');
                    loadAlertas();
                } catch (e) { S.toast(e.message, 'error'); }
            };

            document.getElementById('al-test').onclick = async function () {
                var b = this, t = b.textContent;
                b.disabled = true; b.textContent = 'Enviando…';
                var host2 = document.getElementById('al-result');
                try {
                    var r = await S.api('/monitor/alertas/teste', { method: 'POST' });
                    host2.innerHTML = '<div class="alert alert-' + (r.ok ? 'success' : 'danger') + '">' +
                        (r.ok ? 'E-mail enviado. ' : 'Não enviado. ') + S.esc(r.detalhe || '') + '</div>';
                } catch (e) {
                    host2.innerHTML = '<div class="alert alert-danger">' + S.esc(e.message) + '</div>';
                } finally { b.disabled = false; b.textContent = t; }
            };
        } catch (e) {
            host.innerHTML = '<div class="alert alert-danger">' + S.esc(e.message) + '</div>';
        }
    }

    async function loadOrcamento() {
        var host = document.getElementById('ac-orcamento');
        try {
            var d = await S.api('/controle-orcamento-exec/acessos?limit=200');
            if (!(d.acessos || []).length) {
                host.innerHTML = '<p class="text-muted">Nenhum acesso registrado ainda.</p>';
                return;
            }
            host.innerHTML = '';
            host.appendChild(S.table([
                { key: 'quando', label: 'Quando', render: function (v) {
                    return v ? new Date(v).toLocaleString('pt-BR') : ''; } },
                { key: 'usuario', label: 'Usuário' },
                { key: 'acao', label: 'Ação', html: true, render: function (v) {
                    var cor = v === 'negado' ? 'var(--sp-alerta)'
                            : (v === 'abrir' ? 'var(--sp-faint)' : 'var(--sp-teal-text)');
                    return '<span style="color:' + cor + ';font-weight:600">' + S.esc(v) + '</span>'; } },
                { key: 'ip', label: 'IP' },
                { key: 'detalhe', label: 'Detalhe' }
            ], d.acessos));
        } catch (e) {
            host.innerHTML = '<p class="text-muted">Trilha indisponível: ' + S.esc(e.message) + '</p>';
        }
    }

    document.getElementById('ac-orc-refresh').onclick = loadOrcamento;
    document.getElementById('ac-refresh').onclick = loadAcessos;
    document.getElementById('ac-tipo').onchange = loadAcessos;
    document.getElementById('ac-dias').onchange = loadAcessos;

    loadAcessos(); loadAlertas(); loadOrcamento();
}

/* ── Visual ─────────────────────────────────────────────────────── */
async function renderVisual(c, S) {
    // Cores não se configuram: o portal segue o padrão de UI SPARE (paleta
    // LRSA 2025) nos dois temas. Aqui ficam só os textos.
    c.innerHTML = '<h1 class="page-title">Administração visual</h1>';
    var d = await S.api('/parametros/config/visual');
    var fields = [
        ['nome_app',     'Nome da aplicação'],
        ['footer',       'Rodapé']
    ];
    var form = S.el('div', { className: 'form-grid cols-2' });
    fields.forEach(function (f) {
        form.appendChild(_pField(f[1], f[0], d[f[0]] || '', f[2] || 'text'));
    });
    var saveBtn = S.el('button', { className: 'btn btn-primary mt-3', textContent: 'Salvar' });
    saveBtn.onclick = async function () {
        var body = Object.assign({}, d);
        fields.forEach(function (f) {
            body[f[0]] = document.getElementById(f[0]).value;
        });
        await S.api('/parametros/config/visual', { method: 'PUT', body: body });
        S.toast('Configuração visual salva.', 'success');
    };
    var resetBtn = S.el('button', { className: 'btn btn-outline mt-3', textContent: 'Restaurar padrão', style: 'margin-left:8px' });
    resetBtn.onclick = async function () {
        await S.api('/parametros/visual/reset', { method: 'POST' });
        S.toast('Visual restaurado.', 'success');
        renderVisual(c, S);
    };
    var card = S.el('div', { className: 'card' });
    var cardBody = S.el('div', { className: 'card-body' });
    cardBody.appendChild(form);
    card.appendChild(cardBody);
    c.appendChild(card);
    var btnRow = S.el('div', { className: 'btn-row mt-2' });
    btnRow.appendChild(saveBtn);
    btnRow.appendChild(resetBtn);
    c.appendChild(btnRow);
    c.appendChild(await _cardIcone(S));
}

/* Ícone do portal (favicon). Só o admin geral altera; os demais só veem. */
async function _cardIcone(S) {
    var info = {};
    try { info = await S.api('/parametros/favicon'); } catch (e) { info = {}; }
    var card = S.el('div', { className: 'card mt-3' });
    card.appendChild(S.el('div', { className: 'card-header', textContent: 'Ícone do portal' }));
    var body = S.el('div', { className: 'card-body' });
    var linha = S.el('div', { style: 'display:flex;align-items:center;gap:16px;flex-wrap:wrap' });
    var img = S.el('img', { src: '/favicon.ico?v=' + (info.versao || 0), alt: '',
        style: 'width:48px;height:48px;background:var(--sp-th-bg);border:1px solid var(--sp-border);padding:4px' });
    linha.appendChild(img);
    var txt = S.el('div');
    txt.appendChild(S.el('div', { textContent: info.personalizado ? 'Ícone personalizado' : 'Ícone padrão' }));
    if (info.personalizado && info.atualizado_por) {
        txt.appendChild(S.el('div', { className: 'text-muted', style: 'font-size:12px',
            textContent: 'Alterado por ' + info.atualizado_por + (info.atualizado_em ? ' em ' + info.atualizado_em.split('-').reverse().join('/') : '') }));
    }
    linha.appendChild(txt);
    body.appendChild(linha);

    if (info.pode_alterar) {
        var acoes = S.el('div', { className: 'btn-row mt-2', style: 'align-items:center;gap:8px' });
        var arq = S.el('input', { type: 'file', accept: '.svg,.png,.ico,image/svg+xml,image/png,image/x-icon' });
        var enviar = S.el('button', { className: 'btn btn-primary btn-sm', textContent: 'Enviar' });
        enviar.onclick = async function () {
            if (!arq.files || !arq.files[0]) { S.toast('Escolha um arquivo SVG, PNG ou ICO.', 'error'); return; }
            var fd = new FormData(); fd.append('arquivo', arq.files[0]);
            try {
                var r = await S.api('/parametros/favicon', { method: 'POST', body: fd });
                _aplicarIcone(r.versao); S.toast('Ícone atualizado.', 'success');
                card.replaceWith(await _cardIcone(S));
            } catch (e) { S.toast(e.message, 'error'); }
        };
        acoes.appendChild(arq); acoes.appendChild(enviar);
        if (info.personalizado) {
            var restaurar = S.el('button', { className: 'btn btn-outline btn-sm', textContent: 'Restaurar padrão' });
            restaurar.onclick = async function () {
                try {
                    await S.api('/parametros/favicon', { method: 'DELETE' });
                    _aplicarIcone(Date.now()); S.toast('Ícone padrão restaurado.', 'success');
                    card.replaceWith(await _cardIcone(S));
                } catch (e) { S.toast(e.message, 'error'); }
            };
            acoes.appendChild(restaurar);
        }
        body.appendChild(acoes);
        body.appendChild(S.el('div', { className: 'text-muted mt-1', style: 'font-size:12px',
            textContent: 'SVG, PNG ou ICO, até 256 KB. Vale para todas as páginas do portal.' }));
    }
    card.appendChild(body);
    return card;
}

function _aplicarIcone(versao) {
    var link = document.querySelector('link[rel="icon"]');
    if (link) link.href = '/favicon.ico?v=' + versao;
}

/* ── Locais ─────────────────────────────────────────────────────── */
async function renderLocations(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Locais</h1>' +
        '<button id="pm-local-add" class="btn btn-primary mb-3">Novo local</button>' +
        '<div id="pm-locations"></div>';

    async function load() {
        var d = await S.api('/parametros/locais');
        var cols = [
            { key: 'nome',      label: 'Nome' },
            { key: 'descricao', label: 'Descrição' },
            { key: 'ativo',     label: 'Ativo' },
            {
                key: 'a', label: '',
                render: function (_, r) {
                    var b = S.el('button', { className: 'btn btn-sm btn-outline', textContent: 'Editar' });
                    b.onclick = function () { edit(r); };
                    return b;
                }
            }
        ];
        var el = document.getElementById('pm-locations');
        el.innerHTML = '';
        el.appendChild(S.table(cols, d.locais));
    }

    function edit(r) {
        r = r || {};
        var f = S.el('div');
        f.appendChild(_pField('Nome', 'pm-ln', r.nome || ''));
        f.appendChild(_pField('Descrição', 'pm-ld', r.descricao || ''));
        var saveBtn = S.el('button', { className: 'btn btn-primary', textContent: 'Salvar' });
        saveBtn.onclick = async function () {
            await S.api('/parametros/locais' + (r.id ? '/' + r.id : ''), {
                method: r.id ? 'PUT' : 'POST',
                body: {
                    nome:      document.getElementById('pm-ln').value,
                    descricao: document.getElementById('pm-ld').value,
                    ativo:     r.ativo !== false
                }
            });
            S.closeModal();
            S.toast('Local salvo.', 'success');
            load();
        };
        S.openModal(r.id ? 'Editar local' : 'Novo local', f, [saveBtn]);
    }

    document.getElementById('pm-local-add').onclick = function () { edit(); };
    load();
}

/* ── Classificações ─────────────────────────────────────────────── */
async function renderClassifications(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Classificações</h1>' +
        '<div class="btn-row mb-3">' +
            '<button id="pm-class-add2" class="btn btn-primary">Nova regra</button>' +
            '<button id="pm-class-apply" class="btn btn-secondary">Aplicar em toda a base</button>' +
        '</div>' +
        '<div id="pm-class-msg"></div>' +
        '<div id="pm-class-list2"></div>';

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
                    var b = S.el('button', { className: 'btn btn-sm btn-outline', textContent: 'Editar' });
                    b.onclick = function () { edit(r); };
                    return b;
                }
            }
        ];
        var el = document.getElementById('pm-class-list2');
        el.innerHTML = '';
        el.appendChild(S.table(cols, d.regras));
    }

    function edit(r) {
        r = r || {};
        var f = S.el('div');
        [
            ['Padrão da descrição', 'pm-cp2', r.padrao_descricao],
            ['Empresa (opcional)',   'pm-ce2', r.empresa],
            ['Categoria',           'pm-cc2', r.categoria],
            ['Modelo',              'pm-cm2', r.modelo]
        ].forEach(function (x) { f.appendChild(_pField(x[0], x[1], x[2])); });

        var saveBtn = S.el('button', { className: 'btn btn-primary', textContent: 'Salvar' });
        saveBtn.onclick = async function () {
            try {
                await S.api('/parametros/classificacoes' + (r.id ? '/' + r.id : ''), {
                    method: r.id ? 'PUT' : 'POST',
                    body: {
                        padrao_descricao: document.getElementById('pm-cp2').value,
                        empresa:          document.getElementById('pm-ce2').value,
                        categoria:        document.getElementById('pm-cc2').value,
                        modelo:           document.getElementById('pm-cm2').value,
                        ativo:            true
                    }
                });
                S.closeModal();
                S.toast('Regra salva.', 'success');
                load();
            } catch (e) {
                S.toast(e.message, 'error');
            }
        };
        S.openModal(r.id ? 'Editar regra' : 'Nova regra', f, [saveBtn]);
    }

    document.getElementById('pm-class-add2').onclick = function () { edit(); };

    document.getElementById('pm-class-apply').onclick = async function () {
        if (!confirm('Reaplicar todas as regras sobre a base de recebimento inteira?')) return;
        var b = this, t = b.textContent;
        b.disabled = true; b.textContent = 'Aplicando…';
        try {
            S.loading(true);
            var d = await S.api('/parametros/classificacoes/aplicar-base', { method: 'POST' });
            document.getElementById('pm-class-msg').innerHTML =
                '<div class="alert alert-success">' + d.analisados + ' ativo(s) analisado(s); ' +
                d.atualizados + ' reclassificado(s); ' + d.sem_regra + ' sem regra que case.</div>';
            S.toast('Base reclassificada.', 'success');
        } catch (e) {
            S.toast(e.message, 'error');
        } finally {
            S.loading(false); b.disabled = false; b.textContent = t;
        }
    };

    load();
}

/* ── Valor-hora ─────────────────────────────────────────────────── */
/* Valor-hora da Central de Reparos — mora dentro de Configuração Módulos. */
async function _renderValorHora(S) {
    var host = document.getElementById('cm-valor-hora');
    if (!host) return;
    var d;
    try {
        d = await S.api('/parametros/valor-hora');
    } catch (e) {
        host.innerHTML = '<div class="alert alert-danger">Não foi possível carregar o valor-hora: ' +
            S.esc(e.message) + '</div>';
        return;
    }
    host.innerHTML =
        '<div class="stat-value">' + S.money(d.valor) + '</div>' +
        '<div class="form-group mt-2" style="max-width:260px">' +
            '<label>Novo valor</label>' +
            '<input id="pm-rate" type="number" step="0.01" class="form-control" value="' + d.valor + '">' +
        '</div>' +
        '<button id="pm-rate-save" class="btn btn-primary mt-2">Salvar</button>';

    document.getElementById('pm-rate-save').onclick = async function () {
        try {
            await S.api('/parametros/valor-hora', {
                method: 'PUT',
                body: { valor: +document.getElementById('pm-rate').value }
            });
            S.toast('Valor-hora atualizado.', 'success');
            _renderValorHora(S);
        } catch (e) { S.toast(e.message, 'error'); }
    };
}

/* ── Usuários e Permissões ──────────────────────────────────────── */
async function renderPermissions(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Usuários e Permissões</h1>' +
        '<div class="card mb-3"><div class="card-header">Controle de Acesso Externo</div>' +
            '<div class="card-body">' +
                '<label class="checkbox-label">' +
                    '<input id="pm-block-external" type="checkbox"> ' +
                    'Exigir liberação prévia para login' +
                '</label>' +
                '<button id="pm-save-ac" class="btn btn-sm btn-primary mt-2">Salvar</button>' +
            '</div>' +
        '</div>' +
        '<button id="pm-user-add" class="btn btn-primary mb-3">Novo usuário</button>' +
        '<div id="pm-users"></div>';

    var MODULES = ['bemvindo', 'consulta', 'recebimento', 'identificacao',
        'servicenow', 'atendimento', 'separacao', 'projetos', 'reversa', 'inventario', 'regularizacao', 'preparacao',
        'destinacao', 'externo', 'torre', 'trilha',
        'rastreio', 'reparos', 'status', 'parametros', 'orcamento',
        'orcamento_spare', 'orcamento_manutencao', 'obsolescencia',
        'automacoes', 'ebs_forms', 'consulta_times'];
    var MODULE_LABELS = {
        bemvindo: 'Bem-vindo', consulta: 'Consulta', recebimento: 'Recebimento',
        // A chave segue 'servicenow' (as telas escrevem no ServiceNow e a
        // permissão já existe nos usuários); só o nome no menu mudou.
        identificacao: 'Identificação', servicenow: 'Gestão de Ativos',
        atendimento: 'Atendimento', separacao: 'Separação',
        projetos: 'Projetos de Loja', reversa: 'Logística Reversa',
        inventario: 'Inventário', regularizacao: 'Regularização',
        obsolescencia: 'Obsolescência do parque', automacoes: 'Automações',
        ebs_forms: 'EBS Forms',
        preparacao: 'Preparação (configuração e estoque)',
        destinacao: 'Destinação (baixa, venda, descarte, doação)',
        externo: 'Assistência externa e devolução',
        // "Administrar" aqui abre o painel individual dos outros.
        torre: 'Torre de Controle',
        // Sem tela própria ainda: dá acesso à trilha de um ativo e ao
        // painel de filas, que outros módulos consultam.
        trilha: 'Trilha do Ativo', rastreio: 'Correios',
        reparos: 'Central de Reparos (bancadas)', status: 'Status', parametros: 'Configuração',
        consulta_times: 'Consulta Times (acesso específico)',
        // Telas fora da sidebar, liberadas usuário a usuário
        orcamento: 'Controle de Orçamento',        // /controle-orcamento
        orcamento_spare: 'Orçamento SPARE',        // CAPEX da área
        orcamento_manutencao: 'Orçamento (manutenção)',  // coletores e SLEDs (na sidebar)
        ebs_forms: 'EBS Forms (RPA)',
        // A aba é de todos; "Administrar" é quem configura a rotina e as regras
        automacoes: 'Automações'
    };
    var ACTIONS = ['can_view', 'can_create', 'can_edit', 'can_export', 'can_admin'];
    var ACTION_LABELS = ['Visualizar', 'Criar', 'Editar', 'Exportar', 'Administrar'];
    // O servidor diz quais ações existem em cada módulo (config.MODULE_ACTIONS);
    // a grade só oferece essas. O que não existe aparece como "—".
    var modulosServidor = null;
    var acoesPorModulo = {};

    function acoesDe(m) {
        var lista = acoesPorModulo[m];
        return lista ? lista.map(function (a) { return 'can_' + a; }) : ACTIONS;
    }

    async function load() {
        var d = await S.api('/parametros/permissoes');
        if (d.modules && d.modules.length) modulosServidor = d.modules;
        acoesPorModulo = d.module_actions || {};
        document.getElementById('pm-block-external').checked = !!d.block_external;
        var cols = [
            { key: 'username',      label: 'Login' },
            { key: 'display_name',  label: 'Nome' },
            { key: 'auth_source',   label: 'Origem' },
            { key: 'active',        label: 'Ativo', html: true, render: function (v) {
                return v ? '<span class="badge badge-success">Sim</span>' : '<span class="badge badge-danger">Não</span>';
            }},
            { key: 'allowed',       label: 'Permitido', html: true, render: function (v) {
                return v ? '<span class="badge badge-success">Sim</span>' : '<span class="badge badge-danger">Não</span>';
            }},
            { key: 'is_admin',      label: 'Admin', html: true, render: function (v) {
                return v ? '<span class="badge badge-info">Sim</span>' : '—';
            }},
            { key: 'last_access',   label: 'Último acesso', render: function (v) {
                if (!v) return '—';
                try { return new Date(v).toLocaleString('pt-BR'); } catch (_) { return v; }
            }},
            {
                key: 'a', label: '',
                html: true,
                render: function (_, u) {
                    var wrap = S.el('div', { style: 'display:flex;gap:6px' });
                    var b = S.el('button', { className: 'btn btn-sm btn-outline', textContent: 'Editar' });
                    b.onclick = function () { editUser(u); };
                    wrap.appendChild(b);
                    var del = S.el('button', { className: 'btn btn-sm btn-outline-danger', textContent: 'Excluir' });
                    del.onclick = function () { deleteUser(u); };
                    wrap.appendChild(del);
                    return wrap;
                }
            }
        ];
        var el = document.getElementById('pm-users');
        el.innerHTML = '';
        el.appendChild(S.table(cols, d.usuarios));
    }

    document.getElementById('pm-save-ac').onclick = async function () {
        var blocked = document.getElementById('pm-block-external').checked;
        await S.api('/parametros/controle-acesso', {
            method: 'PUT',
            body: { block_external: blocked }
        });
        S.toast(blocked ? 'Bloqueio de acesso externo ativado.' : 'Bloqueio de acesso externo desativado.', 'success');
    };

    function deleteUser(u) {
        if (!confirm('Excluir o usuário "' + (u.username) + '"?\n\n' +
            'Esta ação remove o usuário e suas permissões e não pode ser desfeita.')) return;
        S.api('/parametros/usuarios/' + encodeURIComponent(u.username), { method: 'DELETE' })
            .then(function () { S.toast('Usuário excluído.', 'success'); load(); })
            .catch(function (e) { S.toast(e.message || 'Falha ao excluir.', 'error'); });
    }

    function editUser(u) {
        var box = S.el('div');

        box.innerHTML =
            '<div class="form-grid cols-2">' +
                '<div><strong>' + S.esc(u.display_name || u.username) + '</strong>' +
                    '<div class="text-muted">' + S.esc(u.username) + ' | ' + S.esc(u.auth_source || '') + '</div>' +
                '</div>' +
                '<div>' +
                    '<label class="checkbox-label">' +
                        '<input id="perm-active" type="checkbox" ' + (u.active ? 'checked' : '') + '> Usuário ativo' +
                    '</label>' +
                    '<label class="checkbox-label">' +
                        '<input id="perm-allowed" type="checkbox" ' + (u.allowed ? 'checked' : '') + '> Acesso permitido' +
                    '</label>' +
                    '<label class="checkbox-label">' +
                        '<input id="perm-admin" type="checkbox" ' + (u.is_admin ? 'checked' : '') + '> Administrador total' +
                    '</label>' +
                '</div>' +
            '</div>' +
            '<div class="table-wrapper mt-3">' +
                '<table class="data-table">' +
                    '<thead><tr>' +
                        '<th>Módulo</th>' +
                        ACTION_LABELS.map(function (l) { return '<th>' + l + '</th>'; }).join('') +
                    '</tr></thead>' +
                    '<tbody id="perm-body"></tbody>' +
                '</table>' +
            '</div>';

        var tbody = box.querySelector('#perm-body');
        var permMap = u.permission_map || {};

        (modulosServidor || MODULES).forEach(function (m) {
            var perms = permMap[m] || {};
            var existentes = acoesDe(m);
            var tr = S.el('tr');
            tr.innerHTML = '<td><strong>' + S.esc(MODULE_LABELS[m] || m) + '</strong></td>' +
                ACTIONS.map(function (k) {
                    if (existentes.indexOf(k) === -1) {
                        return '<td class="text-muted" title="Esta ação não existe neste módulo">—</td>';
                    }
                    return '<td><input class="perm-check" data-module="' + S.esc(m) +
                        '" data-key="' + k + '" type="checkbox" ' +
                        (perms[k] ? 'checked' : '') + '></td>';
                }).join('');
            tbody.appendChild(tr);
        });

        var saveBtn = S.el('button', { className: 'btn btn-primary', textContent: 'Salvar permissões' });
        saveBtn.onclick = async function () {
            var map = {};
            box.querySelectorAll('.perm-check').forEach(function (ch) {
                var mod = ch.dataset.module;
                if (!map[mod]) map[mod] = {};
                map[mod][ch.dataset.key] = ch.checked;
            });
            await S.api('/parametros/permissoes/' + encodeURIComponent(u.username), {
                method: 'PUT',
                body: {
                    active:         box.querySelector('#perm-active').checked,
                    allowed:        box.querySelector('#perm-allowed').checked,
                    is_admin:       box.querySelector('#perm-admin').checked,
                    permission_map: map
                }
            });
            S.closeModal();
            S.toast('Permissões atualizadas.', 'success');
            load();
        };
        S.openModal('Permissões de ' + u.username, box, [saveBtn]);
    }

    document.getElementById('pm-user-add').onclick = function () {
        var f = S.el('div');

        [
            ['Login (usuário de rede)', 'pm-ul', ''],
            ['Nome',  'pm-un', '']
        ].forEach(function (x) { f.appendChild(_pField(x[0], x[1], x[2])); });

        var aviso = S.el('p', { className: 'text-muted', style: 'margin:8px 0 0;font-size:.85rem' });
        f.appendChild(aviso);

        var saveBtn = S.el('button', { className: 'btn btn-primary', textContent: 'Criar' });
        saveBtn.onclick = async function () {
            var login = document.getElementById('pm-ul').value.trim();
            if (!login) { S.toast('Informe o login.', 'warning'); return; }
            try {
                await S.api('/parametros/usuarios', {
                    method: 'POST',
                    body: {
                        login:        login,
                        display_name: document.getElementById('pm-un').value,
                        auth_source:  'SSO'
                    }
                });
            } catch (e) {
                S.toast(e.message || 'Falha ao criar usuário.', 'error');
                return;
            }
            S.closeModal();
            load();
            S.toast('Usuário de rede "' + login + '" liberado para acesso via SSO.', 'success');
        };

        aviso.textContent = 'Login validado pelo SSO corporativo (loginsso). Sem senha no portal — a senha é a do AD. O usuário já entra liberado.';
        S.openModal('Novo usuário', f, [saveBtn]);
    };

    load();
}

/* ── Sequências ─────────────────────────────────────────────────── */
async function renderSequences(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Sequências de lotes</h1>' +
        '<div id="pm-seq"></div>';

    var d = await S.api('/parametros/sequencias');
    var cols = [
        { key: 'prefixo',         label: 'Prefixo' },
        { key: 'proximo_numero',  label: 'Próximo número' },
        {
            key: 'a', label: '',
            render: function (_, r) {
                var b = S.el('button', { className: 'btn btn-sm btn-outline', textContent: 'Alterar' });
                b.onclick = function () {
                    var f = _pField('Próximo número', 'pm-sn', r.proximo_numero, 'number');
                    var saveBtn = S.el('button', { className: 'btn btn-primary', textContent: 'Salvar' });
                    saveBtn.onclick = async function () {
                        await S.api('/lotes/sequencias/' + r.prefixo, {
                            method: 'PUT',
                            body: { next_number: +document.getElementById('pm-sn').value }
                        });
                        S.closeModal();
                        S.toast('Sequência atualizada.', 'success');
                        renderSequences(c, S);
                    };
                    S.openModal('Sequência ' + r.prefixo, f, [saveBtn]);
                };
                return b;
            }
        }
    ];
    document.getElementById('pm-seq').appendChild(S.table(cols, d.sequencias));
}

/* ── TV ─────────────────────────────────────────────────────────── */
/* ── Dashboards (telas de TV) ───────────────────────────────────── */
// Uma linha por tela: título, subtítulo, intervalo de atualização e se está
// no ar. As telas ficam em painel de parede, sem teclado — por isso o que se
// configura aqui é a apresentação, nunca o que elas mostram de dado.
var _DASHBOARDS = [
    ['cockpit-spare',      'Cockpit SPARE',      'Performance do time — visão gerencial'],
    ['dash-recebimento',   'Recebimento',        'Entrada de equipamentos'],
    ['dash-centralreparos','Central de Reparos', 'Frente, retaguarda e coletores/SLEDs'],
    ['dash-estoques',      'Estoques',           'Níveis e movimentação']
];

async function renderDashboards(c, S) {
    var cfg = {};
    try { cfg = (await S.api('/parametros/config/dashboards')) || {}; } catch (e) { cfg = {}; }

    var html =
        '<h1 class="page-title">Dashboards</h1>' +
        '';

    _DASHBOARDS.forEach(function (d) {
        var chave = d[0], nomePadrao = d[1], subPadrao = d[2];
        var atual = cfg[chave] || {};
        html +=
            '<div class="card mb-3"><div class="card-header" ' +
                'style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">' +
                '<span>' + S.esc(nomePadrao) + '</span>' +
                '<a class="btn btn-sm btn-secondary" href="/' + chave + '" target="_blank" ' +
                    'rel="noopener">Abrir a tela</a>' +
            '</div>' +
            '<div class="card-body">' +
                '<div class="form-grid cols-2">' +
                    '<div class="form-group"><label>Título</label>' +
                        '<input id="dh-' + chave + '-titulo" class="form-control" ' +
                        'placeholder="' + S.esc(nomePadrao) + '" value="' +
                        S.esc(atual.titulo || '') + '"></div>' +
                    '<div class="form-group"><label>Subtítulo</label>' +
                        '<input id="dh-' + chave + '-sub" class="form-control" ' +
                        'placeholder="' + S.esc(subPadrao) + '" value="' +
                        S.esc(atual.subtitulo || '') + '"></div>' +
                    '<div class="form-group"><label>Atualizar a cada (segundos)</label>' +
                        '<input id="dh-' + chave + '-int" type="number" min="10" class="form-control" ' +
                        'value="' + (atual.intervalo || 60) + '"></div>' +
                    '<div class="form-group"><label>Situação</label>' +
                        '<label class="checkbox-label" style="padding-top:8px">' +
                        '<input type="checkbox" id="dh-' + chave + '-ativo"' +
                        (atual.ativo === false ? '' : ' checked') + '> No ar</label></div>' +
                '</div>' +
                '<div class="text-muted" style="font-size:.8rem;margin-top:6px">' +
                    'Endereço: <code>/' + chave + '</code></div>' +
            '</div></div>';
    });

    html += '<button id="dh-save" class="btn btn-primary">Salvar dashboards</button>' +
            '<span id="dh-msg" class="text-muted" style="margin-left:10px"></span>';
    c.innerHTML = html;

    document.getElementById('dh-save').onclick = async function () {
        var corpo = {};
        var erro = '';
        _DASHBOARDS.forEach(function (d) {
            var chave = d[0];
            var intervalo = parseInt(document.getElementById('dh-' + chave + '-int').value, 10) || 60;
            if (intervalo < 10) { erro = 'O intervalo mínimo é 10 segundos.'; }
            corpo[chave] = {
                titulo:    document.getElementById('dh-' + chave + '-titulo').value.trim(),
                subtitulo: document.getElementById('dh-' + chave + '-sub').value.trim(),
                intervalo: intervalo,
                ativo:     document.getElementById('dh-' + chave + '-ativo').checked
            };
        });
        if (erro) { S.toast(erro, 'error'); return; }
        try {
            await S.api('/parametros/config/dashboards', { method: 'PUT', body: corpo });
            document.getElementById('dh-msg').textContent =
                'Salvo. As telas pegam a mudança ao recarregar.';
            S.toast('Dashboards salvos.', 'success');
        } catch (e) { S.toast(e.message, 'error'); }
    };
}

/* ── Minha conta ────────────────────────────────────────────────── */
function renderAccount(c, S) {
    var u = S.user();
    c.innerHTML =
        '<h1 class="page-title">Minha conta</h1>' +
        '<div class="card">' +
            '<div class="card-body">' +
                '<p><span class="text-muted">Usuário:</span> <strong>' + S.esc(u.username) + '</strong></p>' +
                '<p><span class="text-muted">Perfil:</span> <strong>' + S.esc(u.role || '') + '</strong></p>' +
                // Sem troca de senha: o acesso é só por Logon AD (SSO).
                '<div class="form-grid cols-2">' +
                    '<div class="form-group"><label>Nome de exibição</label>' +
                        '<input id="pm-nome" class="form-control" maxlength="120" value="' +
                        S.esc(u.display_name || '') + '"></div>' +
                '</div>' +
                '<button id="pm-nome-save" class="btn btn-primary mt-2">Salvar nome</button>' +
            '</div>' +
        '</div>';

    document.getElementById('pm-nome-save').onclick = async function () {
        var nome = document.getElementById('pm-nome').value.trim();
        try {
            var d = await S.api('/auth/meu-nome', { method: 'POST', body: { display_name: nome } });
            u.display_name = d.display_name;
            // o cabeçalho mostra o nome: atualiza sem precisar recarregar
            var alvo = document.getElementById('topbar-user-name');
            if (alvo) alvo.textContent = d.display_name;
            var av = document.getElementById('topbar-user-avatar');
            if (av && d.display_name) av.textContent = d.display_name[0].toUpperCase();
            S.toast('Nome atualizado.', 'success');
        } catch (e) {
            S.toast(e.message, 'error');
        }
    };
}

/* ── Separação (admin) ──────────────────────────────────────────────
   Duas coisas moram aqui: como ler o estoque no ServiceNow e o
   calendário de expediente. O calendário é do núcleo e vale para todos
   os módulos — está nesta aba porque hoje é a Separação que o usa. */
async function renderSeparacaoConfig(c, S) {
    var d = await S.api('/separacao/config');
    var cal = await S.api('/trilha/config');
    var cfg = d.config;
    // Projetos de loja carrega isolado: se o módulo não subiu, a aba da
    // Separação continua inteira e só o cartão dele não aparece.
    var prj = null;
    try { prj = (await S.api('/projetos/config')).config; } catch (_) { prj = null; }
    var rev = null;
    try { rev = (await S.api('/reversa/config')).config; } catch (_) { rev = null; }
    var inv = null, reg = null;
    try { inv = (await S.api('/inventario/config')).config; } catch (_) { inv = null; }
    try { reg = (await S.api('/regularizacao/config')).config; } catch (_) { reg = null; }
    var mods = {};
    for (var nomeMod of ['bancada', 'preparacao', 'destinacao', 'externo', 'atendimento']) {
        try { var rc = await S.api('/' + nomeMod + '/config'); mods[nomeMod] = rc.config || rc; } catch (_) { mods[nomeMod] = null; }
    }
    var obs = null;
    try { obs = await S.api('/obsolescencia/config'); } catch (_) { obs = null; }

    c.innerHTML = '';
    var topo = S.el('div', { style: 'display:flex;justify-content:space-between;align-items:center;margin-bottom:16px' });
    topo.appendChild(S.el('h1', { className: 'page-title', style: 'margin:0', textContent: 'Ciclo do ativo' }));
    topo.appendChild(S.el('button', { id: 'sep-cfg-salvar', className: 'btn btn-primary', textContent: 'Salvar' }));
    c.appendChild(topo);

    var largura = 'max-width:900px';

    /* Estoque ------------------------------------------------------ */
    var estoque = S.el('div', { className: 'card-body' });
    estoque.innerHTML =
        '<div class="form-grid cols-2">' +
          _sepCampo('Campo do espaço e corredor', 'sc-campo', cfg.campo_local) +
          _sepSelect('Comparação', 'sc-comp', cfg.comparacao,
                     [['STARTSWITH', 'Começa com'], ['=', 'É igual a'],
                      ['LIKE', 'Contém']]) +
          _sepCampo('Prefixo da reposição', 'sc-pref-rep', cfg.prefixo_reposicao) +
          _sepCampo('Prefixo da inauguração', 'sc-pref-in', cfg.prefixo_inauguracao) +
          _sepCampo('Situação em estoque (install_status)', 'sc-status', cfg.status_estoque) +
        '</div>' +
        '<div class="table-wrapper mt-3"><table class="data-table"><thead><tr>' +
        '<th>Atendimento</th><th>Estoque</th><th>Consulta enviada ao ServiceNow</th>' +
        '</tr></thead><tbody>' +
        _sepLinhaTipo('Frente e Retaguarda', 'sc-est-fr',
                      cfg.estoque_FRENTE_RETAGUARDA, d.filtros.FRENTE_RETAGUARDA) +
        _sepLinhaTipo('Mobilidade', 'sc-est-mob',
                      cfg.estoque_MOBILIDADE, d.filtros.MOBILIDADE) +
        _sepLinhaTipo('Inauguração e Reforma', 'sc-est-inau',
                      cfg.estoque_INAUGURACAO_REFORMA, d.filtros.INAUGURACAO_REFORMA) +
        '</tbody></table></div>' +
        '<div class="form-group mt-3">' +
          '<label for="sc-manual">Sobrescrever a consulta (opcional)</label>' +
          '<input id="sc-manual" class="form-control" value="' +
            S.esc(cfg.filtro_manual) + '" ' +
            'placeholder="em branco, vale a consulta montada acima">' +
          '<span class="text-muted" style="font-size:11.5px">Aceita encoded query ' +
            'inteira. Use $campo, $comparacao e $prefixo.</span>' +
        '</div>';
    c.appendChild(_sepCartao('Estoque', estoque, largura));

    /* Reserva e envio ---------------------------------------------- */
    var reserva = S.el('div', { className: 'card-body' });
    reserva.innerHTML =
        '<div class="form-grid cols-2">' +
          _sepCampo('Campo da reserva', 'sc-res-campo', cfg.reserva_campo) +
          _sepCampo('Valor quando reservado', 'sc-res-valor', cfg.reserva_valor) +
          _sepCampo('Valor quando livre', 'sc-res-livre', cfg.reserva_valor_livre) +
          _sepCampo('Situação no envio (install_status)', 'sc-envio-status', cfg.envio_status) +
          _sepCampo('Campo do local no envio', 'sc-envio-local', cfg.envio_campo_local) +
        '</div>';
    c.appendChild(_sepCartao('Reserva e envio', reserva, largura));

    /* Chamado e prazos --------------------------------------------- */
    var chamado = S.el('div', { className: 'card-body' });
    chamado.innerHTML =
        '<div class="form-grid cols-2">' +
          _sepCampo('Prefixos aceitos', 'sc-ch-pref', cfg.chamado_prefixos) +
          _sepCampo('Situações que bloqueiam', 'sc-ch-bloq',
                    cfg.chamado_estados_bloqueados) +
          _sepCampo('Prazo normal (dias úteis)', 'sc-prazo-n', cfg.prazo_normal) +
          _sepCampo('Prazo loja parada (dias úteis)', 'sc-prazo-lp', cfg.prazo_loja_parada) +
          _sepCampo('Prazo inauguração (dias úteis)', 'sc-prazo-in', cfg.prazo_inauguracao) +
        '</div>';
    c.appendChild(_sepCartao('Chamado de origem e prazos', chamado, largura));

    /* Projetos de loja (A16) --------------------------------------- */
    if (prj) {
        var projetos = S.el('div', { className: 'card-body' });
        projetos.innerHTML =
            '<div class="form-grid cols-2">' +
              _sepCampo('Definição (dias úteis)', 'sc-prj-def', prj.prazo_definicao) +
              _sepCampo('Separação (dias úteis)', 'sc-prj-sep', prj.prazo_separacao) +
              _sepCampo('Configuração (dias úteis)', 'sc-prj-cfg', prj.prazo_configuracao) +
              _sepCampo('Folga antes da abertura (dias úteis)', 'sc-prj-folga',
                        prj.folga_antes_abertura) +
              _sepCampo('Prefixos de chamado aceitos', 'sc-prj-ch', prj.chamado_prefixos) +
              _sepCampo('Estado da unidade reprovada', 'sc-prj-rep', prj.estado_reparo) +
            '</div>';
        c.appendChild(_sepCartao('Projetos de loja', projetos, largura));
    }

    /* Logística reversa (A17) -------------------------------------- */
    if (rev) {
        var reversa = S.el('div', { className: 'card-body' });
        reversa.innerHTML =
            '<div class="form-grid cols-2">' +
              _sepCampo('Loja posta em (dias corridos)', 'sc-rev-post', rev.prazo_postagem_dias) +
              _sepCampo('Conferir em (dias úteis)', 'sc-rev-conf', rev.prazo_conferencia_dias) +
              _sepCampo('Prefixos de chamado aceitos', 'sc-rev-ch', rev.chamado_prefixos) +
              _sepSelect('Divergência abre regularização', 'sc-rev-reg', rev.abrir_regularizacao,
                         [['1', 'Sim'], ['0', 'Não']]) +
            '</div>';
        c.appendChild(_sepCartao('Logística reversa', reversa, largura));
    }

    /* Inventário (A18) e Regularização (A19) ------------------------ */
    if (inv || reg) {
        var ctrl = S.el('div', { className: 'card-body' });
        ctrl.innerHTML =
            '<div class="form-grid cols-2">' +
            (inv ? _sepCampo('Inventário: situação em estoque', 'sc-inv-status', inv.status_estoque) +
                   _sepCampo('Inventário: campo do corredor', 'sc-inv-campo', inv.campo_local) +
                   _sepCampo('Inventário: prazo da contagem (dias úteis)', 'sc-inv-prazo', inv.prazo_contagem_dias) +
                   _sepSelect('Inventário: divergência abre regularização', 'sc-inv-reg', inv.abrir_regularizacao,
                              [['1', 'Sim'], ['0', 'Não']]) : '') +
            (reg ? _sepCampo('Regularização: prazo padrão ao assumir (dias úteis)', 'sc-reg-prazo', reg.prazo_padrao_dias) +
                   _sepCampo('Regularização: alerta sem dono após (dias úteis)', 'sc-reg-alerta', reg.alerta_sem_dono_dias) : '') +
            '</div>';
        c.appendChild(_sepCartao('Inventário e regularização', ctrl, largura));
    }

    /* Bancada, preparação, destinação, assistência, atendimento ------ */
    var b = mods.bancada, pr = mods.preparacao, ds = mods.destinacao, ex = mods.externo, at = mods.atendimento;
    if (b || pr || ds || ex || at) {
        var ofi = S.el('div', { className: 'card-body' });
        ofi.innerHTML = '<div class="form-grid cols-2">' +
            (b ? _sepCampo('Bancada: janela de reincidência (dias)', 'sc-bnc-reinc', b.janela_reincidencia) +
                 _sepCampo('Bancada: alerta de peça parada (dias)', 'sc-bnc-pecas', b.alerta_pecas_dias) : '') +
            (pr ? _sepSelect('Internalização grava corredor no ServiceNow', 'sc-prp-sn', pr.escrever_no_servicenow,
                             [['sim', 'Sim'], ['nao', 'Não']]) +
                  _sepCampo('Internalização: situação em estoque (install_status)', 'sc-prp-status', pr.status_disponivel) : '') +
            (ds ? _sepCampo('Destinação: extensões de anexo', 'sc-dst-ext', ds.anexo_extensoes) +
                  _sepCampo('Destinação: tamanho máximo do anexo (MB)', 'sc-dst-mb', ds.anexo_tamanho_mb) +
                  _sepCampo('Destinação: alerta sem destino (dias úteis)', 'sc-dst-alerta', ds.alerta_sem_destino_dias) : '') +
            (ex ? _sepCampo('Assistência: alerta de atraso (dias)', 'sc-ext-alerta', ex.alerta_atraso_dias) : '') +
            (at ? _sepCampo('Atendimento: categorias de Mobilidade', 'sc-atd-frota', at.categorias_frota) +
                  _sepCampo('Atendimento: prazo Frente e Retaguarda (dias úteis)', 'sc-atd-loja', at.prazo_loja) +
                  _sepCampo('Atendimento: prazo Mobilidade (dias úteis)', 'sc-atd-frt', at.prazo_frota) : '') +
            '</div>';
        c.appendChild(_sepCartao('Bancada, preparação, destinação, assistência e atendimento', ofi, largura));
    }

    /* Obsolescência do parque ---------------------------------------- */
    if (obs) {
        var ob = S.el('div', { className: 'card-body' });
        ob.innerHTML = '<div class="form-grid cols-2">' +
            _sepSelect('Regra', 'sc-obs-modo', obs.modo_regra, [['todos', 'Todos os critérios (E)'], ['qualquer', 'Qualquer critério (OU)']]) +
            _sepCampo('Idade limite (anos)', 'sc-obs-anos', obs.limite_anos) +
            _sepCampo('Sem comunicar há (dias)', 'sc-obs-semver', obs.limite_sem_ver) +
            _sepCampo('Modelos em fim de vida', 'sc-obs-eol', obs.modelos_eol) +
            _sepCampo('Android mínimo suportado', 'sc-obs-android', obs.versao_os_minima) +
            _sepCampo('Modelos sem atualização', 'sc-obs-semupd', obs.modelos_sem_update) +
            '</div>';
        c.appendChild(_sepCartao('Obsolescência do parque', ob, largura));
    }

    /* Metas por etapa (Torre) ---------------------------------------- */
    try {
        var mt = await S.api('/torre/metas');
        var metas = S.el('div', { className: 'card-body' });
        var grade = S.el('div', { style: 'display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:6px 18px' });
        (mt.metas || []).forEach(function (m) {
            var linha = S.el('label', { style: 'display:flex;align-items:center;gap:8px;font-size:13px' });
            linha.innerHTML = '<span style="flex:1">' + S.esc(m.rotulo) + ' <span class="text-muted">· ' + S.esc(m.frente) + '</span></span>' +
                '<input class="form-control form-control-inline sc-meta" data-estado="' + S.esc(m.estado) + '" type="number" min="0" step="0.5" style="width:84px" value="' + (m.horas_uteis == null ? '' : m.horas_uteis) + '" placeholder="h">';
            grade.appendChild(linha);
        });
        metas.appendChild(grade);
        metas.appendChild(S.el('p', { className: 'text-muted', style: 'font-size:12px;margin:10px 0 0', textContent: 'Horas úteis por etapa. Em branco, vale o alerta geral do calendário.' }));
        c.appendChild(_sepCartao('Metas por etapa (Torre)', metas, largura));
    } catch (_) { /* Torre fora do ar: cartão não aparece */ }

    /* Calendário (núcleo) ------------------------------------------ */
    var expediente = S.el('div', { className: 'card-body' });
    expediente.innerHTML =
        '<div class="form-grid cols-2">' +
          _sepCampo('Dias de expediente (0=seg … 6=dom)', 'sc-cal-dias', cal.expediente_dias) +
          _sepCampo('Fuso em relação ao UTC', 'sc-cal-fuso', cal.fuso_horas) +
          _sepCampo('Abre às', 'sc-cal-ini', cal.expediente_inicio) +
          _sepCampo('Fecha às', 'sc-cal-fim', cal.expediente_fim) +
          _sepCampo('Foto diária da Torre às', 'sc-cal-snap', cal.snapshot_hora) +
        '</div>' +
        '<div class="form-group mt-3">' +
          '<label for="sc-cal-fer">Feriados</label>' +
          '<textarea id="sc-cal-fer" class="form-control" rows="2" ' +
            'placeholder="2026-12-25, 2027-01-01">' + S.esc(cal.feriados) + '</textarea>' +
        '</div>';
    c.appendChild(_sepCartao('Calendário de expediente', expediente, largura));

    /* Salvar -------------------------------------------------------- */
    document.getElementById('sep-cfg-salvar').onclick = async function () {
        var v = function (id) { return document.getElementById(id).value.trim(); };
        try {
            S.loading(true);
            await S.api('/separacao/config', {
                method: 'PUT',
                body: {
                    campo_local: v('sc-campo'), comparacao: v('sc-comp'),
                    prefixo_reposicao: v('sc-pref-rep'),
                    prefixo_inauguracao: v('sc-pref-in'),
                    status_estoque: v('sc-status'),
                    estoque_FRENTE_RETAGUARDA: v('sc-est-fr'),
                    estoque_MOBILIDADE: v('sc-est-mob'),
                    estoque_INAUGURACAO_REFORMA: v('sc-est-inau'),
                    filtro_manual: v('sc-manual'),
                    reserva_campo: v('sc-res-campo'),
                    reserva_valor: v('sc-res-valor'),
                    reserva_valor_livre: v('sc-res-livre'),
                    envio_status: v('sc-envio-status'),
                    envio_campo_local: v('sc-envio-local'),
                    chamado_prefixos: v('sc-ch-pref'),
                    chamado_estados_bloqueados: v('sc-ch-bloq'),
                    prazo_normal: v('sc-prazo-n'),
                    prazo_loja_parada: v('sc-prazo-lp'),
                    prazo_inauguracao: v('sc-prazo-in')
                }
            });
            if (prj) {
                await S.api('/projetos/config', {
                    method: 'PUT',
                    body: {
                        prazo_definicao: v('sc-prj-def'),
                        prazo_separacao: v('sc-prj-sep'),
                        prazo_configuracao: v('sc-prj-cfg'),
                        folga_antes_abertura: v('sc-prj-folga'),
                        chamado_prefixos: v('sc-prj-ch'),
                        estado_reparo: v('sc-prj-rep')
                    }
                });
            }
            if (rev) {
                await S.api('/reversa/config', {
                    method: 'PUT',
                    body: {
                        prazo_postagem_dias: v('sc-rev-post'),
                        prazo_conferencia_dias: v('sc-rev-conf'),
                        chamado_prefixos: v('sc-rev-ch'),
                        abrir_regularizacao: v('sc-rev-reg')
                    }
                });
            }
            if (inv) {
                await S.api('/inventario/config', { method: 'PUT', body: {
                    status_estoque: v('sc-inv-status'), campo_local: v('sc-inv-campo'),
                    prazo_contagem_dias: v('sc-inv-prazo'), abrir_regularizacao: v('sc-inv-reg') } });
            }
            if (reg) {
                await S.api('/regularizacao/config', { method: 'PUT', body: {
                    prazo_padrao_dias: v('sc-reg-prazo'), alerta_sem_dono_dias: v('sc-reg-alerta') } });
            }
            if (mods.bancada) await S.api('/bancada/config', { method: 'PUT', body: {
                janela_reincidencia: v('sc-bnc-reinc'), alerta_pecas_dias: v('sc-bnc-pecas') } });
            if (mods.preparacao) await S.api('/preparacao/config', { method: 'PUT', body: {
                escrever_no_servicenow: v('sc-prp-sn'), status_disponivel: v('sc-prp-status') } });
            if (mods.destinacao) await S.api('/destinacao/config', { method: 'PUT', body: {
                anexo_extensoes: v('sc-dst-ext'), anexo_tamanho_mb: v('sc-dst-mb'), alerta_sem_destino_dias: v('sc-dst-alerta') } });
            if (mods.externo) await S.api('/externo/config', { method: 'PUT', body: { alerta_atraso_dias: v('sc-ext-alerta') } });
            if (mods.atendimento) await S.api('/atendimento/config', { method: 'PUT', body: {
                categorias_frota: v('sc-atd-frota'), prazo_loja: v('sc-atd-loja'), prazo_frota: v('sc-atd-frt') } });
            if (obs) await S.api('/obsolescencia/config', { method: 'PUT', body: {
                modo_regra: v('sc-obs-modo'), limite_anos: v('sc-obs-anos'), limite_sem_ver: v('sc-obs-semver'),
                modelos_eol: v('sc-obs-eol'), versao_os_minima: v('sc-obs-android'), modelos_sem_update: v('sc-obs-semupd') } });
            var metasBody = Array.from(document.querySelectorAll('.sc-meta')).map(function (i) {
                return { estado: i.dataset.estado, horas_uteis: i.value === '' ? null : parseFloat(i.value), ativa: true };
            });
            if (metasBody.length) await S.api('/torre/metas', { method: 'PUT', body: metasBody });
            await S.api('/trilha/config', {
                method: 'PUT',
                body: {
                    expediente_dias: v('sc-cal-dias'),
                    expediente_inicio: v('sc-cal-ini'),
                    expediente_fim: v('sc-cal-fim'),
                    feriados: v('sc-cal-fer'),
                    fuso_horas: v('sc-cal-fuso'),
                    snapshot_hora: v('sc-cal-snap')
                }
            });
            S.toast('Configuração salva.', 'success');
            renderSeparacaoConfig(c, S);
        } catch (e) {
            S.toast(e.message, 'danger');
        } finally {
            S.loading(false);
        }
    };
}

function _sepCartao(titulo, corpo, estilo) {
    var S = window.SPARE;
    var card = S.el('div', { className: 'card mb-3', style: estilo || '' });
    card.appendChild(S.el('div', { className: 'card-header', textContent: titulo }));
    card.appendChild(corpo);
    return card;
}

function _sepCampo(rotulo, id, valor, ajuda) {
    var S = window.SPARE;
    return '<div class="form-group"><label for="' + id + '">' + S.esc(rotulo) +
        '</label><input id="' + id + '" class="form-control" value="' +
        S.esc(valor == null ? '' : valor) + '">' +
        (ajuda ? '<span class="text-muted" style="font-size:11.5px">' +
                 S.esc(ajuda) + '</span>' : '') + '</div>';
}

function _sepSelect(rotulo, id, valor, opcoes) {
    var S = window.SPARE;
    return '<div class="form-group"><label for="' + id + '">' + S.esc(rotulo) +
        '</label><select id="' + id + '" class="form-control">' +
        opcoes.map(function (o) {
            return '<option value="' + S.esc(o[0]) + '"' +
                (o[0] === valor ? ' selected' : '') + '>' + S.esc(o[1]) + '</option>';
        }).join('') + '</select></div>';
}

function _sepLinhaTipo(rotulo, id, valor, consulta) {
    var S = window.SPARE;
    return '<tr><td>' + S.esc(rotulo) + '</td>' +
        '<td><select id="' + id + '" class="form-control form-control-inline">' +
          '<option value="reposicao"' + (valor === 'reposicao' ? ' selected' : '') +
            '>Reposição</option>' +
          '<option value="inauguracao"' + (valor === 'inauguracao' ? ' selected' : '') +
            '>Inauguração</option>' +
        '</select></td>' +
        '<td class="text-muted" style="font-size:11.5px;word-break:break-all">' +
        S.esc(consulta) + '</td></tr>';
}
