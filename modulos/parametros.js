/* ================================================================
   Module: Parâmetros — as abas que todo usuário recebe

   Este arquivo desce para todo usuário, porque a aba "Minha conta" é
   de todos. Por isso as telas de administração não moram mais aqui:
   estão em `parametros_admin.js`, pedido só quando uma aba de admin é
   aberta — e só por quem é admin.
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

        // A mesma lista que esconde a aba decide o que vem do outro arquivo:
        // duas listas se desencontrariam, e o desencontro aqui seria justo
        // entregar tela de admin a quem não é. Quem não é admin para nesta
        // linha — nem a requisição do arquivo chega a sair, mesmo que o
        // endereço venha digitado na URL (#parametros/base-ebs).
        if (adminOnly.indexOf(sub) !== -1) {
            if (!u.is_admin) {
                container.innerHTML =
                    '<div class="alert alert-danger"><strong>Acesso negado.</strong><br>' +
                    'Esta tela é da administração do portal.</div>';
                return;
            }
            _pCarregarAdmin().then(function (telas) {
                var render = telas[sub];
                if (!render) throw new Error('Tela de administração desconhecida: ' + sub);
                return render(container, S);
            }).catch(function (e) {
                container.innerHTML =
                    '<div class="alert alert-danger"><strong>Falha ao carregar.</strong><br>' +
                    S.esc(e.message || e) + '</div>';
            });
            return;
        }

        var handlers = {
            locais:         renderLocations,
            classificacoes: renderClassifications,
            automacoes:     renderAutomacoes,
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

/* ── Telas de administração, sob demanda ──────────────────────────
   Mesmo desenho do `gestao_ativos.js`: uma tag <script> embrulhada em
   Promise. A Promise fica guardada para o arquivo ser pedido uma vez
   só na sessão — abrir e reabrir as abas de admin não repete o
   download. */
var _pAdminCarregando = null;

function _pCarregarAdmin() {
    if (window.SPARE_PARAMETROS_ADMIN) return Promise.resolve(window.SPARE_PARAMETROS_ADMIN);
    if (_pAdminCarregando) return _pAdminCarregando;
    _pAdminCarregando = new Promise(function (ok, falhou) {
        var s = document.createElement('script');
        s.src = _pAppBase() + '/modulos/parametros_admin.js?v=' + Date.now();
        s.onload = function () {
            // A rota é autenticada: ela pode responder 200 com outra coisa
            // (a tela de login, por exemplo) e o onload dispararia igual.
            // Quem diz que o módulo veio é o mapa das abas.
            if (!window.SPARE_PARAMETROS_ADMIN) {
                _pAdminCarregando = null;
                falhou(new Error('As telas de administração não chegaram. ' +
                                 'Entre de novo no portal e tente outra vez.'));
                return;
            }
            ok(window.SPARE_PARAMETROS_ADMIN);
        };
        s.onerror = function () {
            // Sem zerar aqui, a falha ficaria grudada na Promise e nenhuma
            // nova tentativa sairia até recarregar a página inteira.
            _pAdminCarregando = null;
            falhou(new Error('Não foi possível carregar as telas de administração.'));
        };
        document.head.appendChild(s);
    });
    return _pAdminCarregando;
}

/* Prefixo do portal quando ele é servido num subcaminho do proxy
   (/portal-spare): o main.py injeta <meta name="app-base">. Sem isto o
   <script> pediria o arquivo na raiz do domínio e tomaria 404. */
function _pAppBase() {
    var m = document.querySelector('meta[name="app-base"]');
    return (m && m.content ? m.content : '').replace(/\/+$/, '');
}
