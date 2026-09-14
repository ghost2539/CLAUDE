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
            ['automacoes',      'Automações'],
            ['monitoramento',   'Monitoramento'],
            ['cofre',           'Cofre de segredos'],
            ['ebs-oracle',      'Base EBS (Oracle)'],
            ['acessos',         'Acessos & Alertas'],
            ['dashboards',      'Dashboards'],
            ['conta',           'Minha conta']
        ];

        // Automações fica fora da lista: a aba é de todos. Dentro dela, quem
        // não é admin vê a situação, os logs e o botão Exec Now — a
        // configuração (credencial, cofre, horários) segue só do admin.
        var adminOnly = ['visual', 'permissoes', 'sequencias', 'config-modulos',
                         'monitoramento', 'cofre', 'ebs-oracle', 'acessos',
                         'dashboards'];
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
            automacoes:     renderAutomacoes,
            monitoramento:  renderMonitoramento,
            cofre:          renderCofre,
            'ebs-oracle':   renderEbsOracle,
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
            '<div class="card-header">Central de Reparos — valor-hora</div>' +
            '<div class="card-body" id="cm-valor-hora">' +
                '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div>' +
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
    _renderValorHora(S);

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
    // A aba é de todos, e as REGRAS também: qualquer usuário cria, edita e
    // exclui regra, e roda a rotina (que age no ServiceNow com a sessão de
    // quem clicou). Só a CONFIGURAÇÃO da rotina — horários, cofre, credencial
    // — pede "Administrar" no módulo Automações.
    var usuario = S.user() || {};
    var permAutom = (usuario.permission_map || {}).automacoes || {};
    var ehAdmin = !!(usuario.is_admin || permAutom.can_admin);
    c.innerHTML =
        '<h1 class="page-title">Automações</h1>' +
        '<p class="text-muted">Rotina que encerra ou encaminha chamados entregues, ' +
            'com o seu usuário. Só age quando o último evento do rastreio é ENTREGUE.</p>' +
        '<div class="card mb-3"><div class="card-header">' +
            (ehAdmin ? 'Configuração da rotina' : 'Situação da rotina') + '</div>' +
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
    async function loadCfg() {
        var cfg = await S.api('/automacoes/config');
        var host = document.getElementById('au-cfg');
        if (cfg.somente_leitura) {
            host.innerHTML =
                '<div class="form-grid cols-2">' +
                    '<div class="form-group"><label>Rotina automática</label>' +
                        '<div style="padding-top:6px;font-weight:600">' +
                        (cfg.enabled ? 'LIGADA' : 'DESLIGADA') + '</div></div>' +
                    '<div class="form-group"><label>Horários</label>' +
                        '<div style="padding-top:6px">' + S.esc(cfg.horarios || '') + '</div></div>' +
                    '<div class="form-group"><label>Sessão para a rotina</label>' +
                        '<div style="padding-top:6px;font-size:.85rem;color:var(--text-secondary)">' +
                        (cfg.tem_sessao ? ('Ativa (usuário ' + S.esc(cfg.usuario || '') + ')') : 'Nenhuma sessão salva') +
                        '</div></div>' +
                    '<div class="form-group"><label>Última execução</label>' +
                        '<div style="padding-top:6px;font-size:.85rem;color:var(--text-secondary)">' +
                        S.esc(cfg.ultima_execucao || '—') + '</div></div>' +
                '</div>' +
                '<div class="mt-2"><button id="au-run" class="btn btn-primary">Exec Now</button>' +
                '<span class="text-muted" style="margin-left:10px">' +
                'A configuração da rotina é do administrador.</span></div>';
            ligarBotaoRodar();
            return;
        }
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
            '<button id="au-run" class="btn btn-secondary" style="margin-left:8px">Exec Now</button> ' +
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
        ligarBotaoRodar();
    }

    function ligarBotaoRodar() {
        var btn = document.getElementById('au-run');
        if (!btn) return;
        btn.onclick = async function () {
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
            { key: 'ativo', label: 'Ativa', render: function (v) { return v ? 'Sim' : 'Não'; } }
        ];
        cols.push(
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
/* Base EBS (Oracle) — provar o acesso antes de qualquer consulta.
   Nenhuma senha é digitada aqui: a credencial vem por referência do cofre,
   e a tela mostra só de onde cada valor foi resolvido. */
// O cofre visto de DENTRO do serviço. No servidor, quem roda o portal e quem
// abre um terminal são usuários diferentes: o CLI responde pelo shell, e só
// esta tela responde pelo processo que faz as consultas de verdade.
async function renderCofre(c, S) {
    var e = S.esc;
    c.innerHTML =
        '<h1 class="page-title">Cofre de segredos</h1>' +
        '<p class="text-muted">O que o <b>processo do portal</b> alcança. Nenhum valor de ' +
            'segredo aparece aqui — só de onde veio e quantos caracteres tem.</p>' +
        '<div class="card mb-3"><div class="card-header">Sondar nomes no cofre</div>' +
            '<div class="card-body">' +
            '<p class="text-muted" style="margin-top:0">O cofre corporativo responde por nome, ' +
            'uma chave de cada vez — não dá para listar. Cole os nomes candidatos (vírgula, ' +
            'espaço ou um por linha) e veja quais respondem.</p>' +
            '<div class="form-group"><textarea id="cf-nomes" class="form-control" rows="3" ' +
            'placeholder="ORACLE_EBS_USUARIO, ORACLE_EBS_SENHA, MYSQL_LOCAL_PASS"></textarea></div>' +
            '<div class="btn-row"><button id="cf-sondar" class="btn btn-primary btn-sm">Sondar</button></div>' +
            '<div id="cf-sondagem" class="mt-3"></div>' +
            '<hr>' +
            '<p class="text-muted">O cofre do time é mantido em PHP. Como só o serviço ' +
            'alcança o cofre, quem roda o teste é o próprio portal — no seu terminal o ' +
            'resultado seria sobre o seu usuário, não sobre ele.</p>' +
            '<div class="filter-grid">' +
                '<div class="form-group"><label for="cf-php-loader">Loader PHP</label>' +
                    '<input id="cf-php-loader" class="form-control" ' +
                    'value="/usr/local/lib/vcreports/secrets.php"></div>' +
                '<div class="form-group"><label for="cf-php-chave">Chave de prova</label>' +
                    '<input id="cf-php-chave" class="form-control" value="CORREIOS_USUARIO"></div>' +
            '</div>' +
            '<div class="btn-row"><button id="cf-php" class="btn btn-secondary btn-sm">Testar pelo PHP</button></div>' +
            '<div id="cf-php-saida" class="mt-3"></div>' +
            '</div></div>' +
        '<div class="card mb-3"><div class="card-header" style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">' +
            '<span>Situação</span>' +
            '<span><button id="cf-atualizar" class="btn btn-sm btn-secondary">Atualizar</button> ' +
            '<button id="cf-tudo" class="btn btn-sm btn-secondary" style="margin-left:6px">Ver ambiente e cofre local</button> ' +
            '<button id="cf-correios" class="btn btn-sm btn-primary" style="margin-left:6px">Testar Correios</button></span>' +
            '</div><div class="card-body" id="cf-situacao">' +
            '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div></div></div>' +
        '<div class="card mb-3" id="cf-tudo-card" style="display:none">' +
            '<div class="card-header">Ambiente e cofre local</div>' +
            '<div class="card-body" id="cf-tudo-corpo"></div></div>' +
        '<div class="card" id="cf-teste-card" style="display:none">' +
            '<div class="card-header">Teste dos Correios</div>' +
            '<div class="card-body" id="cf-teste"></div></div>';

    function badge(ok, sim, nao) {
        return ok ? '<span class="badge badge-success">' + sim + '</span>'
                  : '<span class="badge badge-danger">' + nao + '</span>';
    }

    function linha(k) {
        var onde = [];
        if (k.no_corporativo) onde.push('corporativo');
        if (k.no_local) onde.push('local');
        if (k.no_ambiente) onde.push('ambiente');
        // Sombreamento é a falha silenciosa clássica: o cofre local responde
        // primeiro e o corporativo, correto, nunca é consultado.
        // A ordem é corporativo → local → ambiente. Quando mais de uma fonte
        // tem a chave, a que vence pode não ser a que alguém acabou de
        // configurar — e é sempre aí que se perde tempo.
        var aviso = '';
        if (k.sombreado) aviso += ' <span class="badge badge-warning" title="' +
            e((k.fontes_com_valor || []).join(' e ')) +
            ' têm esta chave. Vale a primeira da ordem: corporativo, local, ambiente.">' +
            'em ' + (k.fontes_com_valor || []).length + ' fontes</span>';
        if (k.indistinguivel) aviso += ' <span class="badge badge-warning" ' +
            'title="O loader resolve cofre → ambiente. Ele devolveu o mesmo valor que ' +
            'está na variável de ambiente, então não dá para saber se o cofre respondeu.">' +
            'pode ser só o ambiente</span>';
        if (k.divergente) aviso += ' <span class="badge badge-danger" ' +
            'title="As fontes têm valores diferentes para esta chave.">' +
            'valores diferentes</span>';
        else if (k.no_local && !k.no_corporativo && !k.no_ambiente)
            aviso += ' <span class="badge badge-warning" ' +
                'title="Só o cofre local tem esta chave.">só no local</span>';
        return '<tr><td class="om-mono">' + e(k.chave) + '</td>' +
            '<td>' + badge(k.resolvida, 'resolvida', 'faltando') + aviso + '</td>' +
            '<td>' + e(k.fonte || '—') +
                (onde.length ? ' <span class="text-muted">(está em: ' + e(onde.join(', ')) + ')</span>' : '') + '</td>' +
            '<td>' + (k.valor ? e(k.valor)
                              : (k.resolvida ? '<span class="text-muted">' + k.tamanho + ' caracteres</span>'
                                             : '<span class="text-muted">—</span>')) + '</td></tr>';
    }

    // Cada parte do diagnóstico responde por si. Quando uma falha, ela
    // aparece nomeada — some da tela é o que não pode.
    function errosHtml(erros) {
        var nomes = Object.keys(erros || {});
        if (!nomes.length) return '';
        return '<div class="alert alert-danger"><b>Parte do diagnóstico falhou.</b>' +
            '<ul style="margin:6px 0 0">' + nomes.map(function (n) {
                return '<li>' + e(n) + ': <span class="om-mono">' + e(erros[n]) + '</span></li>';
            }).join('') + '</ul></div>';
    }

    // O módulo do cofre importa, mas nada resolve? Então o problema não é o
    // módulo: é o arquivo que ELE lê, ou o nome da chave. Estas duas seções
    // respondem as duas perguntas sem precisar de acesso ao servidor.
    // O prefixo também vem do ambiente. Sem ele o navegador busca CSS e JS
    // no lugar errado e a tela aparece crua — sintoma que não parece ter
    // nada a ver com a causa, e por isso mora aqui.
    function prefixoHtml(pf) {
        if (!pf) return '';
        return '<p><b>Prefixo do portal:</b> ' +
            (pf.em_uso
                ? '<span class="om-mono">' + e(pf.em_uso) + '</span> <span class="text-muted">(' +
                  e(pf.origem) + ')</span>'
                : '<span class="badge badge-danger">nenhum</span> <span class="text-muted">— ' +
                  'atrás de um proxy em subcaminho, CSS e JS vão ser buscados no lugar errado ' +
                  'e a tela abre sem estilo</span>') + '</p>';
    }

    // Se uma credencial funciona sem estar no cofre nem na unit, ela veio
    // daqui — e é aqui que se acrescenta a próxima, sem mexer na unit.
    function ambienteHtml(a) {
        if (!a) return '';
        var cab = '<h3 class="mt-3">Arquivo de ambiente do serviço</h3>';
        if (!a.existe) return cab + '<p class="text-muted om-mono">' +
            e(a.erro || 'não encontrado') + '</p>';
        if (!a.legivel) return cab + '<p class="om-mono">' + e(a.caminho) +
            ' — <span class="badge badge-danger">' + e(a.erro || 'sem leitura') + '</span></p>';
        var chaves = a.chaves || [];
        return cab + '<p class="om-mono">' + e(a.caminho) +
            ' — <span class="badge badge-success">legível</span> ' + chaves.length + ' variável(is)</p>' +
            (chaves.length
                ? '<div class="table-wrapper"><table class="data-table"><thead><tr>' +
                  '<th>Variável</th><th>Como está definida</th></tr></thead><tbody>' +
                  chaves.map(function (k) {
                      // O caso que derruba a tela: está no arquivo e não
                      // chegou ao processo — o systemd não leu a linha.
                      var perdida = k.chegou_ao_processo === false
                          ? ' <span class="badge badge-danger" title="O systemd não carregou ' +
                            'esta linha. Valor com espaço, aspas abertas ou cifrão sem escape ' +
                            'derruba a variável — e às vezes as seguintes junto.">' +
                            'não chegou ao processo</span>' : '';
                      var como = k.marcador
                          ? '<span class="badge badge-info">marcador do cofre</span> ' +
                            '<span class="om-mono">@cofre:' + e(k.aponta_para) + '@</span>' +
                            ' <span class="text-muted">— só resolve se o cofre responder</span>'
                          : (k.vazio ? '<span class="badge badge-warning">vazia</span>'
                                     : '<span class="badge badge-success">valor direto</span>');
                      return '<tr><td class="om-mono">' + e(k.chave) + '</td><td>' + como + perdida + '</td></tr>';
                  }).join('') + '</tbody></table></div>'
                : '') +
            '<p class="text-muted">Valores nunca aparecem — só o nome e se é valor direto ou ' +
            'marcador. Acrescentar uma variável aqui não exige mexer na unit: basta reiniciar ' +
            'o serviço.</p>';
    }

    function inventarioHtml(inv) {
        if (!inv) return '';
        return '<h3 class="mt-3">Loader do cofre corporativo</h3>' +
            '<p><b>Módulo:</b> ' +
            (inv.modulo_carregado
                ? '<span class="badge badge-success">carregado</span> <span class="text-muted">' +
                  e(inv.modulo_via || '') + '</span>' +
                  (inv.funcao ? ' <span class="text-muted">— função ' + e(inv.funcao) + '()</span>' : '')
                : '<span class="badge badge-danger">não carregado</span>') + '</p>' +
            (inv.comando_externo
                ? '<p><b>Comando externo:</b> <span class="om-mono">' + e(inv.comando_externo) +
                  '</span> <span class="text-muted">— quando configurado, é ele que resolve ' +
                  'antes do loader Python.</span></p>'
                : '') +
            '<p class="text-muted">O loader responde <b>por nome</b>, uma chave de cada vez, e não ' +
            'tem função de listar — o arquivo do cofre não é para ser aberto por quem consome. ' +
            'Então não há como "trazer tudo" daqui: para procurar, use a sondagem por nome. ' +
            'A ordem do loader é cofre → variável de ambiente → padrão, e é por isso que uma ' +
            'chave pode funcionar mesmo com o cofre inacessível.</p>';
    }

    function alternativasHtml(lista) {
        if (!lista || !lista.length) return '';
        return '<h3 class="mt-3">O nome da chave é outro?</h3>' +
            '<p class="text-muted">Apelidos plausíveis da credencial do EBS, sondados de uma vez. ' +
            'Se nenhum resolve, o problema não é o nome.</p>' +
            lista.map(function (g) {
                return '<div class="table-wrapper mb-2"><table class="data-table"><thead><tr>' +
                    '<th>' + e(g.nome) + '</th><th>Situação</th><th>Fonte</th><th>Valor</th>' +
                    '</tr></thead><tbody>' + (g.chaves || []).map(linha).join('') +
                    '</tbody></table></div>';
            }).join('');
    }

    async function carregar() {
        var host = document.getElementById('cf-situacao');
        host.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div>';
        try {
            var d = await S.api('/cofre/diagnostico');
            host.innerHTML =
                '<p><b>Usuário do serviço:</b> <span class="om-mono">' + e(d.usuario_do_servico || '?') + '</span></p>' +
                '<p><b>Cofre corporativo:</b> ' +
                    badge(d.corporativo_ok, 'disponível', 'indisponível') +
                    ' <span class="text-muted">' + e(d.corporativo_detalhe || '') + '</span></p>' +
                errosHtml(d.erros) +
                '<div class="table-wrapper mb-3"><table class="data-table"><tbody>' +
                    '<tr><td><b>Cofre local</b></td><td class="om-mono">' + e(d.cofre_local || '') + '</td>' +
                    '<td>' + (d.cofre_local_existe
                        ? '<span class="badge badge-info">existe</span> <span class="text-muted">' +
                          e(d.algoritmo || '') + '</span>'
                        : '<span class="text-muted">não existe</span>') + '</td></tr>' +
                '</tbody></table></div>' + prefixoHtml(d.prefixo) +
                ambienteHtml(d.ambiente_do_servico) +
                inventarioHtml(d.inventario) +
                (d.grupos || []).map(function (g) {
                    return '<h3 class="mt-3">' + e(g.nome) + '</h3>' +
                        '<div class="table-wrapper"><table class="data-table"><thead><tr>' +
                        '<th>Chave</th><th>Situação</th><th>Fonte</th><th>Valor</th>' +
                        '</tr></thead><tbody>' + (g.chaves || []).map(linha).join('') +
                        '</tbody></table></div>';
                }).join('') + alternativasHtml(d.alternativas);
        } catch (x) {
            host.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
        }
    }

    document.getElementById('cf-atualizar').onclick = carregar;

    document.getElementById('cf-sondar').onclick = async function () {
        var host = document.getElementById('cf-sondagem');
        var nomes = document.getElementById('cf-nomes').value;
        if (!nomes.trim()) { S.toast('Informe ao menos um nome.', 'warning'); return; }
        host.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Sondando…</div>';
        try {
            var d = await S.api('/cofre/sondar-varios', {
                method: 'POST', body: JSON.stringify({ nomes: nomes })
            });
            host.innerHTML =
                '<p><b>' + d.resolvidas + '</b> de ' + d.total + ' responderam.</p>' +
                '<div class="table-wrapper"><table class="data-table"><thead><tr>' +
                '<th>Chave</th><th>Situação</th><th>Fonte</th><th>Valor</th>' +
                '</tr></thead><tbody>' + (d.itens || []).map(linha).join('') +
                '</tbody></table></div>';
        } catch (x) {
            host.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
        }
    };

    document.getElementById('cf-php').onclick = async function () {
        var host = document.getElementById('cf-php-saida');
        host.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Rodando o PHP…</div>';
        try {
            var d = await S.api('/cofre/testar-php', {
                method: 'POST',
                body: JSON.stringify({
                    loader: document.getElementById('cf-php-loader').value.trim(),
                    chave: document.getElementById('cf-php-chave').value.trim()
                })
            });
            // Quando funciona, o que falta e uma linha na unit — entao ela ja
            // vem escrita, em vez de virar mais uma ida e volta.
            var receita = d.ok
                ? '<p>Para o portal passar a resolver por aqui, acrescente na unit:</p>' +
                  '<pre class="om-mono" style="white-space:pre-wrap">' +
                  e('Environment="VCREPORTS_SECRETS_CMD=' + d.comando_para_a_unit + '"') +
                  (d.variavel_do_loader
                      ? '\n' + e('Environment="' + d.variavel_do_loader + '"') : '') +
                  '\nsudo systemctl daemon-reload && sudo systemctl restart portal-spare' +
                  '</pre>'
                : '';
            host.innerHTML =
                '<div class="alert alert-' + (d.ok ? 'success' : 'danger') + '">' +
                (d.ok ? 'O PHP leu <b>' + e(d.chave) + '</b> no cofre — ' + e(d.detalhe) + '.' +
                        (d.retirada_do_ambiente
                            ? ' <br><small>Atenção: esta chave também está no ambiente do ' +
                              'serviço, e o loader resolve cofre → ambiente. O valor pode ter ' +
                              'vindo de lá, não do cofre.</small>'
                            : ' <br><small>A chave não está no ambiente do serviço, então o ' +
                              'valor só pode ter vindo do cofre.</small>')
                      : e(d.detalhe)) + '</div>' + receita;
            S.toast(d.ok ? 'O serviço alcança o cofre pelo PHP.' : 'O PHP também não leu.',
                    d.ok ? 'success' : 'error');
        } catch (x) {
            host.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            S.toast(x.message, 'error');
        }
    };

    document.getElementById('cf-tudo').onclick = async function () {
        var card = document.getElementById('cf-tudo-card');
        var host = document.getElementById('cf-tudo-corpo');
        card.style.display = '';
        host.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Lendo…</div>';
        try {
            var d = await S.api('/cofre/tudo');
            var f = d.por_fonte || {};
            host.innerHTML =
                '<p><b>' + d.total + '</b> chave(s) — ' +
                    (f['cofre corporativo'] || 0) + ' do cofre corporativo, ' +
                    (f['cofre local'] || 0) + ' do cofre local, ' +
                    (f['ambiente'] || 0) + ' do ambiente.</p>' +
                '<div class="form-group"><input id="cf-filtro" class="form-control" ' +
                    'placeholder="filtrar por nome (ex.: ORACLE, CORREIOS)"></div>' +
                '<div class="table-wrapper"><table class="data-table"><thead><tr>' +
                '<th>Chave</th><th>Situação</th><th>Fonte</th><th>Valor</th>' +
                '</tr></thead><tbody id="cf-tudo-linhas">' +
                (d.itens || []).map(linha).join('') + '</tbody></table></div>' +
                '<p class="text-muted" style="margin-bottom:0">Só o que é enumerável: as ' +
                'variáveis do processo e o cofre local. O cofre corporativo não entra aqui ' +
                'porque o loader não sabe listar — para ele, use a sondagem por nome. ' +
                'Valor só aparece quando o nome não denuncia um segredo.</p>';
            // Filtro no cliente: a lista já está toda aqui, e ir ao servidor
            // a cada tecla só serviria para deixar a tela lenta.
            var campo = document.getElementById('cf-filtro');
            campo.oninput = function () {
                var termo = campo.value.trim().toUpperCase();
                document.getElementById('cf-tudo-linhas').innerHTML =
                    (d.itens || []).filter(function (i) {
                        return !termo || i.chave.indexOf(termo) !== -1;
                    }).map(linha).join('');
            };
        } catch (x) {
            host.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
        }
    };

    document.getElementById('cf-correios').onclick = async function () {
        var card = document.getElementById('cf-teste-card');
        var host = document.getElementById('cf-teste');
        card.style.display = '';
        host.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Autenticando…</div>';
        try {
            var d = await S.api('/cofre/testar-correios', { method: 'POST' });
            host.innerHTML = '<div class="alert alert-' + (d.ok ? 'success' : 'danger') + '">' +
                e(d.detalhe || '') + '</div>' +
                '<div class="table-wrapper"><table class="data-table"><thead><tr>' +
                '<th>Chave</th><th>Situação</th><th>Fonte</th><th>Valor</th>' +
                '</tr></thead><tbody>' + (d.chaves || []).map(linha).join('') +
                '</tbody></table></div>';
            S.toast(d.ok ? 'Cofre e Correios respondendo.' : (d.detalhe || 'Falhou.'),
                    d.ok ? 'success' : 'error');
        } catch (x) {
            host.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            S.toast(x.message, 'error');
        }
    };

    carregar();
}

async function renderEbsOracle(c, S) {
    var e = S.esc;
    c.innerHTML =
        '<h1 class="page-title">Base EBS (Oracle)</h1>' +
        '<p class="text-muted">Leitura direta do EBSPRD, só para consulta. A credencial é ' +
            'resolvida por referência no cofre — nada é digitado nesta tela.</p>' +
        '<div class="card mb-3"><div class="card-header" style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">' +
            '<span>Situação</span>' +
            '<span><button id="eo-atualizar" class="btn btn-sm btn-secondary">Atualizar</button> ' +
            '<button id="eo-testar" class="btn btn-sm btn-primary" style="margin-left:6px">Testar conexão</button></span>' +
            '</div><div class="card-body" id="eo-situacao">' +
            '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div></div></div>' +
        '<div class="card mb-3" id="eo-teste-card" style="display:none">' +
            '<div class="card-header">Resultado do teste</div>' +
            '<div class="card-body" id="eo-teste"></div></div>' +
        '<div class="card mb-3"><div class="card-header">Consulta</div><div class="card-body">' +
            '<p class="text-muted" style="margin-top:0">Só leitura: a sessão é aberta como ' +
            'READ ONLY e a consulta precisa começar por SELECT ou WITH. A credencial vem do ' +
            'cofre pelo loader, como no resto do portal.</p>' +
            '<div class="form-group"><label for="eo-sql">SQL</label>' +
                '<textarea id="eo-sql" class="form-control om-mono" rows="5" ' +
                'placeholder="select * from apps.csi_item_instances where instance_number = :serie"></textarea></div>' +
            '<div class="filter-grid">' +
                '<div class="form-group"><label for="eo-binds">Parâmetros (opcional)</label>' +
                    '<input id="eo-binds" class="form-control om-mono" ' +
                    'placeholder=\'{"serie": "HF550123456"}\'></div>' +
                '<div class="form-group"><label for="eo-limite">Máximo de linhas</label>' +
                    '<input id="eo-limite" class="form-control" type="number" value="200" ' +
                    'min="1" max="5000"></div>' +
            '</div>' +
            '<div class="btn-row mt-2"><button id="eo-rodar" class="btn btn-primary btn-sm">Executar</button></div>' +
            '<div id="eo-resultado" class="mt-3"></div>' +
        '</div></div>' +
        '<div class="card"><div class="card-header">Procurar objeto</div><div class="card-body">' +
            '<p class="text-muted" style="margin-top:0">Lista tabelas e views que a conta enxerga. ' +
            'Só catálogo — nenhum dado de negócio é lido aqui.</p>' +
            '<div class="filter-grid">' +
                '<div class="form-group"><label for="eo-prefixo">Prefixo (mín. 3 letras)</label>' +
                    '<input id="eo-prefixo" class="form-control" placeholder="ex.: CSI_ITEM"></div>' +
                '<div class="form-group"><label for="eo-owner">Owner</label>' +
                    '<input id="eo-owner" class="form-control" value="APPS"></div>' +
            '</div>' +
            '<div class="btn-row mt-2"><button id="eo-buscar" class="btn btn-primary btn-sm">Procurar</button></div>' +
            '<div id="eo-objetos" class="mt-3"></div>' +
        '</div></div>';

    function linhaChave(k) {
        var marcas = {
            cofre:   '<span class="badge badge-success">do cofre</span>',
            padrao:  '<span class="badge badge-info">padrão do código</span>',
            ausente: '<span class="badge badge-danger">faltando</span>'
        };
        var marca = marcas[k.situacao] || (k.resolvida
            ? '<span class="badge badge-success">resolvida</span>'
            : '<span class="badge badge-danger">faltando</span>');
        var onde = [];
        if (k.no_corporativo) onde.push('corporativo');
        if (k.no_local) onde.push('local');
        var aviso = (k.no_local && !k.no_corporativo)
            ? ' <span class="badge badge-warning" title="Só o cofre local tem esta chave. ' +
              'Se o valor estiver errado, é aqui que ele está.">só no local</span>' : '';
        return '<tr><td class="om-mono">' + e(k.chave) + '</td><td>' + marca + aviso + '</td>' +
            '<td>' + e(k.fonte || '—') +
                (onde.length ? ' <span class="text-muted">(está em: ' + e(onde.join(', ')) + ')</span>' : '') +
            '</td>' +
            '<td>' + (k.valor ? e(k.valor) : '<span class="text-muted">—</span>') + '</td></tr>';
    }

    async function carregar() {
        var host = document.getElementById('eo-situacao');
        host.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div>';
        try {
            var d = await S.api('/ebs-oracle/situacao');
            var drv = d.driver || {};
            host.innerHTML =
                '<p><b>Cofre corporativo:</b> ' +
                    (d.cofre_corporativo ? '<span class="badge badge-success">disponível</span>'
                                         : '<span class="badge badge-danger">indisponível</span>') +
                    ' <span class="text-muted">' + e(d.cofre_detalhe || '') + '</span></p>' +
                '<p><b>Driver Oracle:</b> ' +
                    (drv.instalado ? '<span class="badge badge-success">instalado</span> ' + e(drv.versao || '')
                                   : '<span class="badge badge-danger">ausente</span> ' + e(drv.detalhe || '')) + '</p>' +
                '<div class="table-wrapper"><table class="data-table"><thead><tr>' +
                '<th>Chave</th><th>Situação</th><th>Fonte</th><th>Valor</th>' +
                '</tr></thead><tbody>' + (d.chaves || []).map(linhaChave).join('') +
                '</tbody></table></div>' +
                '<p class="text-muted" style="margin-bottom:0">A senha nunca aparece: dela só se ' +
                'mostra se foi resolvida e de onde.</p>';
        } catch (x) {
            host.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
        }
    }

    document.getElementById('eo-atualizar').onclick = carregar;

    document.getElementById('eo-testar').onclick = async function () {
        var card = document.getElementById('eo-teste-card');
        var host = document.getElementById('eo-teste');
        card.style.display = '';
        host.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Conectando…</div>';
        try {
            var d = await S.api('/ebs-oracle/testar', { method: 'POST' });
            var a = d.acesso || {};
            host.innerHTML = '<div class="alert alert-success">Conexão estabelecida.</div>' +
                '<div class="table-wrapper"><table class="data-table"><tbody>' +
                Object.keys(a).map(function (k) {
                    return '<tr><td><b>' + e(k) + '</b></td><td>' + e(a[k]) + '</td></tr>';
                }).join('') + '</tbody></table></div>';
            S.toast('EBSPRD respondeu.', 'success');
        } catch (x) {
            host.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            S.toast(x.message, 'error');
        }
    };

    document.getElementById('eo-rodar').onclick = async function () {
        var host = document.getElementById('eo-resultado');
        var sql = document.getElementById('eo-sql').value;
        if (!sql.trim()) { S.toast('Escreva a consulta.', 'warning'); return; }
        var binds = {};
        var bruto = document.getElementById('eo-binds').value.trim();
        if (bruto) {
            try { binds = JSON.parse(bruto); }
            catch (x) { S.toast('Parâmetros não são um JSON válido.', 'error'); return; }
        }
        host.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Consultando o EBSPRD…</div>';
        try {
            var d = await S.api('/ebs-oracle/consultar', {
                method: 'POST',
                body: JSON.stringify({
                    sql: sql, binds: binds,
                    limite: parseInt(document.getElementById('eo-limite').value, 10) || 200
                })
            });
            if (!d.total) {
                host.innerHTML = '<p class="text-muted">Nenhuma linha (' + d.ms + ' ms).</p>';
                return;
            }
            host.innerHTML =
                '<p>' + d.total + ' linha(s) em ' + d.ms + ' ms.' +
                (d.truncado ? ' <span class="badge badge-warning">cortado no limite de ' +
                    d.limite + '</span>' : '') + '</p>' +
                '<div class="table-wrapper"><table class="data-table"><thead><tr>' +
                d.colunas.map(function (c) { return '<th>' + e(c) + '</th>'; }).join('') +
                '</tr></thead><tbody>' + d.linhas.map(function (r) {
                    return '<tr>' + d.colunas.map(function (c) {
                        return '<td>' + e(r[c] == null ? '' : r[c]) + '</td>';
                    }).join('') + '</tr>';
                }).join('') + '</tbody></table></div>';
        } catch (x) {
            host.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            S.toast(x.message, 'error');
        }
    };

    document.getElementById('eo-buscar').onclick = async function () {
        var host = document.getElementById('eo-objetos');
        var prefixo = document.getElementById('eo-prefixo').value.trim();
        var owner = document.getElementById('eo-owner').value.trim() || 'APPS';
        if (prefixo.length < 3) { S.toast('Informe ao menos 3 letras.', 'warning'); return; }
        host.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Procurando…</div>';
        try {
            var d = await S.api('/ebs-oracle/objetos?prefixo=' + encodeURIComponent(prefixo) +
                                '&owner=' + encodeURIComponent(owner));
            var itens = d.itens || [];
            if (!itens.length) { host.innerHTML = '<p class="text-muted">Nada encontrado.</p>'; return; }
            var colunas = Object.keys(itens[0]);
            host.innerHTML = '<p class="text-muted">' + d.total + ' objeto(s).</p>' +
                '<div class="table-wrapper"><table class="data-table"><thead><tr>' +
                colunas.map(function (k) { return '<th>' + e(k) + '</th>'; }).join('') +
                '</tr></thead><tbody>' + itens.map(function (r) {
                    return '<tr>' + colunas.map(function (k) {
                        return '<td>' + e(r[k] == null ? '' : r[k]) + '</td>';
                    }).join('') + '</tr>';
                }).join('') + '</tbody></table></div>';
        } catch (x) {
            host.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
        }
    };

    carregar();
}

async function renderMonitoramento(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Monitoramento</h1>' +
        '<p class="text-muted">Saúde do servidor e dos serviços, e registro de falhas de API, ' +
            'integrações e automações.</p>' +
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

/* ── Acessos & Alertas (admin) ──────────────────────────────────────
   Tentativas de acesso negadas (credencial válida sem liberação e bloqueios
   dentro do portal) e configuração dos alertas por e-mail. */
async function renderAcessos(c, S) {
    c.innerHTML =
        '<h1 class="page-title">Acessos &amp; Alertas</h1>' +
        '<p class="text-muted">Tentativas de acesso negadas e envio de alertas por e-mail.</p>' +

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
                '<div class="stat-card"><div class="stat-value" style="font-size:1.5rem;color:#d97706">' +
                    (r.nao_autorizado || 0) + '</div><div class="stat-label">Sem liberação</div></div>' +
                '<div class="stat-card"><div class="stat-value" style="font-size:1.5rem;color:#dc2626">' +
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
                    return '<span style="color:' + (nao ? '#d97706' : '#dc2626') + ';font-weight:600">' +
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
                    '<b>Parâmetros → Usuários e Permissões</b>.</p>');
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
                '<p class="text-muted mt-2" style="font-size:.8rem">A senha é guardada cifrada no banco de ' +
                    'monitoramento. Quando existir chave <b>SMTP_SENHA</b> no cofre, ela tem prioridade.</p>';

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
                    var cor = v === 'negado' ? '#dc2626'
                            : (v === 'abrir' ? '#6b7280' : '#2563eb');
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
        '<p class="text-muted">Regras de classificação automática de ativos por descrição EBS. ' +
            'Salvar uma regra já reclassifica os ativos que casam com ela; use ' +
            '<b>Aplicar em toda a base</b> para passar todas as regras de novo sobre ' +
            'a base de recebimento inteira (inclusive o que está como NÃO CLASSIFICADA).</p>' +
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
        'servicenow', 'rastreio', 'reparos', 'status', 'parametros', 'orcamento',
        'orcamento_spare', 'orcamento_manutencao'];
    var MODULE_LABELS = {
        bemvindo: 'Bem-vindo', consulta: 'Consulta', recebimento: 'Recebimento',
        // A chave segue 'servicenow' (as telas escrevem no ServiceNow e a
        // permissão já existe nos usuários); só o nome no menu mudou.
        identificacao: 'Identificação', servicenow: 'Gestão de Ativos', rastreio: 'Correios',
        reparos: 'Central de Reparos', status: 'Status', parametros: 'Parâmetros',
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
        '<p class="text-muted">Telas de TV do portal. Cada uma tem o seu endereço e ' +
            'atualiza sozinha no intervalo abaixo. Desativada, a tela sai do ar sem ' +
            'precisar mexer no servidor.</p>';

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
                '<span class="text-muted" style="margin-left:10px">' +
                'A senha é a da rede — alterada só no AD.</span>' +
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
