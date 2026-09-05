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
            ['valor-hora',      'Valor-hora'],
            ['permissoes',      'Usuários e Permissões'],
            ['sequencias',      'Sequências'],
            ['config-modulos',  'Configuração Módulos'],
            ['automacoes',      'Automações'],
            ['monitoramento',   'Monitoramento'],
            ['tv',              'TV'],
            ['conta',           'Minha conta']
        ];

        var adminOnly = ['visual', 'permissoes', 'sequencias', 'config-modulos', 'automacoes', 'monitoramento'];
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
            'valor-hora':   renderHourly,
            permissoes:     renderPermissions,
            sequencias:     renderSequences,
            'config-modulos': renderConfigModulos,
            automacoes:     renderAutomacoes,
            monitoramento:  renderMonitoramento,
            tv:             renderTV,
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
        '<p class="text-muted">Configurações administrativas das bases dos módulos.</p>' +

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
            '<div class="card-header">Indicadores — filtros do ServiceNow</div>' +
            '<div class="card-body">' +
                '<p class="text-muted">Ajusta as consultas do painel de Indicadores. ' +
                    'Estados do incident são numéricos: 1 Novo · 2 Em andamento · 3 Em espera · ' +
                    '6 Resolvido · 7 Encerrado · 8 Cancelado.</p>' +
                '<div id="cm-ind-form"><div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div></div>' +
            '</div>' +
        '</div>';

    _renderIndicadoresConfig(S);

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
    c.innerHTML =
        '<h1 class="page-title">Automações</h1>' +
        '<p class="text-muted">Rotina que encerra ou encaminha chamados entregues, ' +
            'com o seu usuário. Só age quando o último evento do rastreio é ENTREGUE.</p>' +
        '<div class="card mb-3"><div class="card-header">Configuração da rotina</div>' +
            '<div class="card-body" id="au-cfg"><div class="spinner-inline">' +
            '<span class="spinner spinner-sm"></span> Carregando…</div></div></div>' +
        '<div class="card mb-3"><div class="card-header" style="display:flex;justify-content:space-between;align-items:center">' +
            '<span>Regras (subcategoria → ação)</span>' +
            '<button id="au-regra-add" class="btn btn-sm btn-primary">Nova regra</button></div>' +
            '<div class="card-body" id="au-regras"></div></div>' +
        '<div class="card"><div class="card-header" style="display:flex;justify-content:space-between;align-items:center">' +
            '<span>Logs</span>' +
            '<span><input id="au-log-q" class="form-control" placeholder="Buscar chamado/motivo" ' +
                'style="display:inline-block;width:220px;height:32px"> ' +
            '<button id="au-log-refresh" class="btn btn-sm btn-secondary">Atualizar</button></span></div>' +
            '<div class="card-body" id="au-logs"></div></div>';

    // ── Config ──
    async function loadCfg() {
        var cfg = await S.api('/automacoes/config');
        var host = document.getElementById('au-cfg');
        var modo100 = cfg.cofre_disponivel
            ? '<span style="color:#16a34a;font-weight:600">Cofre disponível</span> — a rotina roda 100% automática.'
            : (cfg.tem_credencial
                ? '<span style="color:#16a34a;font-weight:600">Credencial salva</span> (usuário ' + S.esc(cfg.credencial_usuario || '') + ') — roda 100% automática.'
                : '<span style="color:#d97706;font-weight:600">Sem credencial</span> — a rotina só roda quando há sessão sua ativa.');
        host.innerHTML =
            '<div class="form-grid cols-2">' +
                '<div class="form-group"><label style="display:flex;align-items:center;gap:8px;cursor:pointer">' +
                    '<input type="checkbox" id="au-enabled"' + (cfg.enabled ? ' checked' : '') + '> ' +
                    '<strong>Rotina automática ' + (cfg.enabled ? 'LIGADA' : 'DESLIGADA') + '</strong></label></div>' +
                '<div class="form-group"><label>Horários (horas, separadas por vírgula)</label>' +
                    '<input id="au-horarios" class="form-control" value="' + S.esc(cfg.horarios || '7,12,16') + '"></div>' +
                '<div class="form-group"><label>Campo do rastreio no incidente</label>' +
                    '<input id="au-tfield" class="form-control" value="' + S.esc(cfg.tracking_field || 'sys_tags') + '">' +
                    '<small class="text-muted">padrão: sys_tags</small></div>' +
                '<div class="form-group"><label>Sessão para a rotina</label>' +
                    '<div style="font-size:.85rem;color:var(--text-secondary);padding-top:8px">' +
                    (cfg.tem_sessao ? ('Ativa (usuário ' + S.esc(cfg.usuario || '') + ')') : 'Nenhuma sessão salva') +
                    (cfg.ultima_execucao ? ('<br>Última execução: ' + S.esc(cfg.ultima_execucao)) : '') +
                    '</div></div>' +
            '</div>' +
            '<hr style="border-color:var(--border-color);margin:14px 0">' +
            '<div style="font-weight:600;margin-bottom:4px">Automação 100% (sem depender de login)</div>' +
            '<div style="font-size:.85rem;margin-bottom:8px">' + modo100 + '</div>' +
            '<div class="form-grid cols-2">' +
                '<div class="form-group"><label>Chave do usuário no cofre</label>' +
                    '<input id="au-cofre-user-key" class="form-control" value="' + S.esc(cfg.cofre_user_key || 'SN_AUTOMACAO_USUARIO') + '"></div>' +
                '<div class="form-group"><label>Chave da senha no cofre</label>' +
                    '<input id="au-cofre-pass-key" class="form-control" value="' + S.esc(cfg.cofre_pass_key || 'SN_AUTOMACAO_SENHA') + '"></div>' +
            '</div>' +
            '<div style="font-size:.8rem;color:var(--text-secondary);margin-bottom:8px">' +
                'No servidor novo, grave a credencial no cofre com essas chaves — ela tem prioridade. ' +
                'Enquanto o cofre não existir, informe abaixo para guardar criptografado.</div>' +
            '<div class="form-grid cols-2">' +
                '<div class="form-group"><label>Usuário (AD) para a automação</label>' +
                    '<input id="au-cred-user" class="form-control" value="' + S.esc(cfg.credencial_usuario || '') + '" placeholder="seu usuário de rede"></div>' +
                '<div class="form-group"><label>Senha (AD)</label>' +
                    '<input id="au-cred-senha" type="password" class="form-control" placeholder="' +
                    (cfg.tem_credencial ? '•••••• (salva)' : 'informe para guardar') + '"></div>' +
            '</div>' +
            '<div class="mt-2"><button id="au-cfg-save" class="btn btn-primary">Salvar configuração</button> ' +
            '<button id="au-run" class="btn btn-secondary" style="margin-left:8px">Rodar agora</button> ' +
            (cfg.tem_credencial ? '<button id="au-cred-clear" class="btn btn-danger" style="margin-left:8px">Remover credencial salva</button>' : '') +
            '<span id="au-cfg-msg" class="text-muted" style="margin-left:10px"></span></div>';

        document.getElementById('au-cfg-save').onclick = async function () {
            try {
                await S.api('/automacoes/config', { method: 'PUT', body: {
                    enabled: document.getElementById('au-enabled').checked,
                    horarios: document.getElementById('au-horarios').value.trim(),
                    tracking_field: document.getElementById('au-tfield').value.trim(),
                    cofre_user_key: document.getElementById('au-cofre-user-key').value.trim(),
                    cofre_pass_key: document.getElementById('au-cofre-pass-key').value.trim(),
                    cred_user: document.getElementById('au-cred-user').value.trim(),
                    cred_senha: document.getElementById('au-cred-senha').value
                }});
                document.getElementById('au-cfg-msg').textContent = 'Configuração salva.';
                S.toast('Configuração salva.', 'success');
                loadCfg();
            } catch (e) { S.toast(e.message, 'error'); }
        };
        var clearBtn = document.getElementById('au-cred-clear');
        if (clearBtn) clearBtn.onclick = async function () {
            if (!confirm('Remover a credencial salva da automação?')) return;
            try {
                await S.api('/automacoes/config', { method: 'PUT', body: {
                    enabled: document.getElementById('au-enabled').checked,
                    limpar_credencial: true
                }});
                S.toast('Credencial removida.', 'success'); loadCfg();
            } catch (e) { S.toast(e.message, 'error'); }
        };
        document.getElementById('au-run').onclick = async function () {
            if (!confirm('Rodar a rotina agora com o seu usuário?')) return;
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
            { key: 'ativo', label: 'Ativa', render: function (v) { return v ? 'Sim' : 'Não'; } },
            { key: 'a', label: '', render: function (_, r) {
                var w = S.el('div', { className: 'btn-row' });
                var e = S.el('button', { className: 'btn btn-sm btn-outline', textContent: 'Editar' });
                var x = S.el('button', { className: 'btn btn-sm btn-danger', textContent: 'Excluir' });
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
        ];
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
async function renderMonitoramento(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Monitoramento</h1>' +
        '<p class="text-muted">Saúde do servidor e dos serviços, e registro de falhas de API, ' +
            'integrações e automações.</p>' +
        '<div class="card mb-3"><div class="card-header" style="display:flex;justify-content:space-between;align-items:center">' +
            '<span>Saúde</span>' +
            '<span><button id="mo-refresh" class="btn btn-sm btn-secondary">Atualizar</button> ' +
            '<button id="mo-checar" class="btn btn-sm btn-primary" style="margin-left:6px">Checar integrações</button></span>' +
            '</div><div class="card-body" id="mo-saude">' +
            '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div></div></div>' +
        '<div class="card mb-3" id="mo-check-card" style="display:none">' +
            '<div class="card-header">Resultado da checagem</div>' +
            '<div class="card-body" id="mo-check"></div></div>' +
        '<div class="card"><div class="card-header" style="display:flex;justify-content:space-between;align-items:center">' +
            '<span>Falhas registradas</span>' +
            '<span>' +
              '<select id="mo-sev" class="form-control" style="display:inline-block;width:130px;height:32px">' +
                '<option value="">Todas</option><option value="erro">Erros</option>' +
                '<option value="alerta">Alertas</option><option value="ok">OK</option></select> ' +
              '<select id="mo-org" class="form-control" style="display:inline-block;width:150px;height:32px">' +
                '<option value="">Toda origem</option><option value="api">API</option>' +
                '<option value="integracao">Integração</option><option value="automacao">Automação</option></select> ' +
              '<input id="mo-q" class="form-control" placeholder="Buscar" style="display:inline-block;width:170px;height:32px"> ' +
              '<button id="mo-falhas-refresh" class="btn btn-sm btn-secondary">Filtrar</button>' +
            '</span></div>' +
            '<div class="card-body" id="mo-falhas"></div></div>';

    function barra(pct, alerta, critico) {
        var cor = pct >= critico ? '#dc2626' : (pct >= alerta ? '#d97706' : '#16a34a');
        return '<div style="background:var(--bg-input,#eee);border-radius:6px;height:8px;overflow:hidden;margin-top:6px">' +
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
            var mapa = { ok: ['#16a34a', 'Tudo certo'], alerta: ['#d97706', 'Atenção'], critico: ['#dc2626', 'Crítico'] };
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
                    '<td>' + (b.ok ? '<span style="color:#16a34a;font-weight:600">OK</span>'
                                   : '<span style="color:#dc2626;font-weight:600">FALHA</span>') + '</td>' +
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
                    var cor = v === 'erro' ? '#dc2626' : (v === 'alerta' ? '#d97706' : '#16a34a');
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
                    '<td>' + (r.ok ? '<span style="color:#16a34a;font-weight:600">OK</span>'
                                   : '<span style="color:#dc2626;font-weight:600">FALHA</span>') + '</td>' +
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

/* ── Visual ─────────────────────────────────────────────────────── */
async function renderVisual(c, S) {
    c.innerHTML = '<h1 class="page-title">Administração visual</h1>';
    var d = await S.api('/parametros/config/visual');
    var fields = [
        ['nome_app',     'Nome da aplicação'],
        ['subtitulo',    'Subtítulo'],
        ['login_title',  'Título do login'],
        ['footer',       'Rodapé'],
        ['cor_primaria', 'Cor primária',    'color'],
        ['cor_fundo',    'Cor de fundo',    'color'],
        ['cor_painel',   'Cor dos painéis', 'color'],
        ['cor_texto',    'Cor do texto',    'color'],
        ['cor_destaque', 'Cor de destaque', 'color']
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
        '<p class="text-muted">Regras de classificação automática de ativos por descrição EBS.</p>' +
        '<button id="pm-class-add2" class="btn btn-primary mb-3">Nova regra</button>' +
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
    load();
}

/* ── Valor-hora ─────────────────────────────────────────────────── */
async function renderHourly(c, S) {
    var d = await S.api('/parametros/valor-hora');
    c.innerHTML =
        '<h1 class="page-title">Valor-hora</h1>' +
        '<div class="card">' +
            '<div class="card-body">' +
                '<div class="stat-value">' + S.money(d.valor) + '</div>' +
                '<div class="form-group mt-2">' +
                    '<label>Novo valor</label>' +
                    '<input id="pm-rate" type="number" step="0.01" class="form-control" value="' + d.valor + '">' +
                '</div>' +
                '<button id="pm-rate-save" class="btn btn-primary mt-2">Salvar</button>' +
            '</div>' +
        '</div>';

    document.getElementById('pm-rate-save').onclick = async function () {
        await S.api('/parametros/valor-hora', {
            method: 'PUT',
            body: { valor: +document.getElementById('pm-rate').value }
        });
        S.toast('Valor-hora atualizado.', 'success');
        renderHourly(c, S);
    };
}

/* ── Usuários e Permissões ──────────────────────────────────────── */
async function renderPermissions(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Usuários e Permissões</h1>' +
        '<p class="text-muted">Usuários ServiceNow são registrados automaticamente no primeiro login. ' +
            'Selecione Editar para definir acesso por módulo.</p>' +
        '<div class="card mb-3"><div class="card-header">Controle de Acesso Externo</div>' +
            '<div class="card-body">' +
                '<label class="checkbox-label">' +
                    '<input id="pm-block-external" type="checkbox"> ' +
                    'Bloquear acesso externo (somente usuários na lista de permitidos podem logar via ServiceNow)' +
                '</label>' +
                '<button id="pm-save-ac" class="btn btn-sm btn-primary mt-2">Salvar</button>' +
            '</div>' +
        '</div>' +
        '<button id="pm-user-add" class="btn btn-primary mb-3">Novo usuário (Local ou Rede/SSO)</button>' +
        '<div id="pm-users"></div>';

    var MODULES = ['bemvindo', 'consulta', 'recebimento', 'identificacao',
        'servicenow', 'rastreio', 'reparos', 'status', 'parametros'];
    var MODULE_LABELS = {
        bemvindo: 'Bem-vindo', consulta: 'Consulta', recebimento: 'Recebimento',
        identificacao: 'Identificação', servicenow: 'ServiceNow', rastreio: 'Correios',
        reparos: 'Central de Reparos', status: 'Status', parametros: 'Parâmetros'
    };
    var ACTIONS = ['can_view', 'can_create', 'can_edit', 'can_export', 'can_admin'];
    var ACTION_LABELS = ['Visualizar', 'Criar', 'Editar', 'Exportar', 'Administrar'];

    async function load() {
        var d = await S.api('/parametros/permissoes');
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
                    var del = S.el('button', { className: 'btn btn-sm btn-danger', textContent: 'Excluir' });
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

        MODULES.forEach(function (m) {
            var perms = permMap[m] || {};
            var tr = S.el('tr');
            tr.innerHTML = '<td><strong>' + S.esc(MODULE_LABELS[m] || m) + '</strong></td>' +
                ACTIONS.map(function (k) {
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

        var tipoWrap = S.el('div', { style: 'margin-bottom:10px' });
        tipoWrap.innerHTML =
            '<label style="display:block;font-size:.85rem;margin-bottom:4px">Tipo de acesso</label>' +
            '<select id="pm-utype" class="form-control">' +
                '<option value="LOCAL">Local (senha gerada no portal)</option>' +
                '<option value="SSO">Rede / AD (senha do AD)</option>' +
            '</select>';
        f.appendChild(tipoWrap);

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
            var tipo = document.getElementById('pm-utype').value;
            var r;
            try {
                r = await S.api('/parametros/usuarios', {
                    method: 'POST',
                    body: {
                        login:        login,
                        display_name: document.getElementById('pm-un').value,
                        auth_source:  tipo
                    }
                });
            } catch (e) {
                S.toast(e.message || 'Falha ao criar usuário.', 'error');
                return;
            }
            S.closeModal();
            load();

            if (tipo !== 'LOCAL') {
                S.toast('Usuário de rede "' + login + '" liberado para acesso via SSO.', 'success');
                return;
            }

            // LOCAL: mostra a senha temporária gerada para o admin repassar.
            var box = S.el('div');
            box.innerHTML =
                '<p>Usuário <strong>' + S.esc(r.login) + '</strong> criado.</p>' +
                '<p style="margin:8px 0 4px">Senha temporária (copie e repasse ao usuário — ' +
                'ele terá que trocá-la no primeiro acesso):</p>' +
                '<div style="display:flex;gap:8px;align-items:center">' +
                '<code id="pm-temp-pass" style="font-size:1.1rem;padding:8px 12px;' +
                'background:var(--bg-secondary);border-radius:6px;user-select:all">' +
                S.esc(r.senha_temporaria || '') + '</code></div>';
            var copyBtn = S.el('button', { className: 'btn btn-outline', textContent: 'Copiar senha' });
            copyBtn.onclick = function () {
                try {
                    navigator.clipboard.writeText(r.senha_temporaria || '');
                    S.toast('Senha copiada.', 'success');
                } catch (_) { S.toast('Copie manualmente.', 'info'); }
            };
            S.openModal('Usuário criado', box, [copyBtn]);
        };

        S.openModal('Novo usuário', f, [saveBtn]);

        function refreshAviso() {
            var t = document.getElementById('pm-utype').value;
            aviso.textContent = (t === 'LOCAL')
                ? 'Uma senha temporária será gerada automaticamente. O usuário troca no primeiro acesso.'
                : 'Login validado pelo SSO corporativo (loginsso). Sem senha no portal — a senha é a do AD. O usuário já entra liberado.';
        }
        refreshAviso();
        document.getElementById('pm-utype').addEventListener('change', refreshAviso);
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
async function renderTV(c, S) {
    c.innerHTML = '<h1 class="page-title">Painel TV</h1>';
    var d = await S.api('/parametros/config/tv');
    var fields = [
        ['title',     'Título'],
        ['interval',  'Intervalo em segundos', 'number'],
        ['last_rows', 'Quantidade de linhas',  'number']
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
        await S.api('/parametros/config/tv', { method: 'PUT', body: body });
        S.toast('Configuração TV salva.', 'success');
    };
    var card = S.el('div', { className: 'card' });
    var cardBody = S.el('div', { className: 'card-body' });
    cardBody.appendChild(form);
    card.appendChild(cardBody);
    c.appendChild(card);
    c.appendChild(saveBtn);
}

/* ── Minha conta ────────────────────────────────────────────────── */
function renderAccount(c, S) {
    var u = S.user();
    c.innerHTML =
        '<h1 class="page-title">Minha conta</h1>' +
        '<div class="card">' +
            '<div class="card-body">' +
                '<p><span class="text-muted">Usuário:</span> <strong>' + S.esc(u.username) + '</strong></p>' +
                '<p><span class="text-muted">Nome:</span> <strong>' + S.esc(u.display_name || '') + '</strong></p>' +
                '<p><span class="text-muted">Perfil:</span> <strong>' + S.esc(u.role || '') + '</strong></p>' +
                '<div class="form-grid cols-2">' +
                    '<div class="form-group"><label>Senha atual</label>' +
                        '<input id="pm-old" type="password" class="form-control"></div>' +
                    '<div class="form-group"><label>Nova senha</label>' +
                        '<input id="pm-new" type="password" class="form-control"></div>' +
                '</div>' +
                '<button id="pm-pass" class="btn btn-primary mt-2">Alterar senha</button>' +
            '</div>' +
        '</div>';

    document.getElementById('pm-pass').onclick = async function () {
        try {
            await S.api('/auth/change-password', {
                method: 'POST',
                body: {
                    current_password: document.getElementById('pm-old').value,
                    new_password:     document.getElementById('pm-new').value
                }
            });
            S.toast('Senha alterada.', 'success');
        } catch (e) {
            S.toast(e.message, 'error');
        }
    };
}
