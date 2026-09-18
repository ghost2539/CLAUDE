/* ================================================================
   Módulo: ServiceNow — automação de chamados

   Era a aba "Automações" dentro de Parâmetros, visível a todos. Virou
   item próprio da barra lateral porque é tela de trabalho, e não
   configuração do portal: quem usa entra por ela, não por Parâmetros.
   A tela é a mesma, linha por linha — mudou o lugar e o nome.

   Não confundir com `servicenow.js`, que é outra coisa (Entrada, Saída
   e Movimentação de ativos). Os dois podem estar carregados ao mesmo
   tempo na mesma página: aquele usa os prefixos `_sn`/`_sa`/`_mi`,
   este só publica o `renderAutomacoes`.

   A permissão continua sendo a do módulo `automacoes` — a chave não
   mudou de nome, para ninguém perder acesso na troca. É ela que o
   `routers/modulos.py` exige para entregar este arquivo.
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.servicenow_automacoes = {

    render(container) {
        var S = window.SPARE;
        container.innerHTML =
            '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando...</div>';
        Promise.resolve(renderAutomacoes(container, S)).catch(function (e) {
            container.innerHTML =
                '<div class="alert alert-danger"><strong>Falha ao carregar.</strong><br>' +
                S.esc(e.message || e) + '</div>';
        });
    }

};

/* ── A tela (encerramento/encaminhamento de chamados) ───────────── */
async function renderAutomacoes(c, S) {
    // A tela é de quem tem "Visualizar" no módulo, e as REGRAS também:
    // esse usuário cria, edita e exclui regra, e roda a rotina (que age no
    // ServiceNow com a sessão de quem clicou). Só a CONFIGURAÇÃO (campo do
    // rastreio) pede "Administrar".
    var usuario = S.user() || {};
    var permAutom = (usuario.permission_map || {}).automacoes || {};
    var ehAdmin = !!(usuario.is_admin || permAutom.can_admin);
    c.innerHTML =
        '<h1 class="page-title">ServiceNow</h1>' +
        '<div class="card mb-3"><div class="card-header">' +
            (ehAdmin ? 'Configuração da rotina' : 'Rotina') + '</div>' +
            '<div class="card-body" id="au-cfg"><div class="spinner-inline">' +
            '<span class="spinner spinner-sm"></span> Carregando…</div></div></div>' +
        '<div class="card mb-3"><div class="card-header" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">' +
            '<span>Regras (subcategoria → ação)</span>' +
            '<button id="au-regra-add" class="btn btn-sm btn-primary">Nova regra</button>' +
            '</div>' +
            '<div class="card-body" id="au-regras"></div></div>' +
        '<div id="au-testar"></div>' +
        '<div class="card mt-3"><div class="card-header" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">' +
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
                '<div class="mt-2"><button id="au-run" class="btn btn-primary">Executar agora</button></div>' +
                '<div id="au-resumo"></div>';
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
            '<span id="au-cfg-msg" class="text-muted" style="margin-left:10px"></span></div>' +
            '<div id="au-resumo"></div>';

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

    /* Mostra o resumo da rodada abaixo do botão. "N ignorados" sozinho não
       explica nada — aqui aparece o porquê de cada um e qual regra pegou
       quantos chamados, que é onde se enxerga regra genérica engolindo a
       específica. */
    function mostrarResumo(r) {
        var host = document.getElementById('au-resumo');
        if (!host) return;
        var e = window.SPARE.esc;
        var MOTIVO = {
            sem_rastreio: 'sem código de rastreio no chamado',
            rastreio_indisponivel: 'Correios não respondeu o rastreio',
            nao_entregue: 'objeto ainda não entregue',
            sem_regra: 'nenhuma regra casa com a subcategoria'
        };
        var html = '<div class="card mt-3"><div class="card-header">Última execução</div>' +
            '<div class="card-body">' +
            '<p><b>' + (r.analisados || 0) + '</b> analisado(s) · ' +
            '<b>' + (r.encerrados || 0) + '</b> encerrado(s) · ' +
            '<b>' + (r.encaminhados || 0) + '</b> encaminhado(s) · ' +
            '<b>' + (r.ignorados || 0) + '</b> ignorado(s) · ' +
            '<b>' + (r.erros || 0) + '</b> erro(s)</p>';

        var porRegra = r.por_regra || {};
        var nomes = Object.keys(porRegra);
        if (nomes.length) {
            html += '<p class="text-muted" style="margin-bottom:4px">Regra aplicada:</p><ul>';
            nomes.forEach(function (k) {
                html += '<li>' + e(k) + ' — ' + porRegra[k] + '</li>';
            });
            html += '</ul>';
        }
        var motivos = r.motivos || {};
        var comMotivo = Object.keys(motivos).filter(function (k) { return motivos[k]; });
        if (comMotivo.length) {
            html += '<p class="text-muted" style="margin-bottom:4px">Por que foram ignorados:</p><ul>';
            comMotivo.forEach(function (k) {
                html += '<li>' + e(MOTIVO[k] || k) + ' — ' + motivos[k] + '</li>';
            });
            html += '</ul>';
        }
        host.innerHTML = html + '</div></div>';
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
                    ' encaminhado(s), ' + (r.erros || 0) + ' erro(s).',
                    (r.erros ? 'warning' : 'success'));
                mostrarResumo(r);
                loadLogs();
            } catch (e) { S.toast(e.message, 'error'); }
            finally { b.disabled = false; b.textContent = t; }
        };
    }

    // ── Regras ──
    async function loadRegras() {
        var d = await S.api('/automacoes/regras');
        // Subcategorias e ordem na lista: com várias regras, é o que responde
        // "qual delas pega este chamado?" sem abrir uma por uma.
        var cols = [
            { key: 'ordem', label: 'Ordem' },
            { key: 'nome', label: 'Nome' },
            { key: 'subcategorias', label: 'Subcategorias', render: function (v) {
                var lista = String(v || '').split(/[\n;,]+/)
                    .map(function (x) { return x.trim(); })
                    .filter(function (x) { return x; });
                if (!lista.length) return '—';
                var txt = lista.slice(0, 4).join(' · ');
                if (lista.length > 4) txt += ' · +' + (lista.length - 4);
                return S.el('span', { title: lista.join('\n'), textContent: txt });
            }},
            { key: 'acao', label: 'Ação', render: function (v) {
                return v === 'encaminhar' ? 'Encaminhar' : 'Encerrar';
            }},
            { key: 'fila_destino', label: 'Fila destino', render: function (v) { return v || '—'; } },
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
        montarTestador();
    }

    /* Testador: digita a subcategoria como ela vem do ServiceNow e mostra qual
       regra venceria — e quais outras casariam. Com várias regras, a pergunta
       deixa de ser "existe regra?" e passa a ser "QUAL pega?". */
    function montarTestador() {
        var host = document.getElementById('au-testar');
        if (!host) return;
        host.innerHTML =
            '<div class="card mt-3"><div class="card-header">Testar subcategoria</div>' +
            '<div class="card-body">' +
                '<div class="form-row-inline">' +
                    '<input id="au-t-sub" class="form-control form-control-inline" ' +
                        'style="min-width:280px" autocomplete="off" ' +
                        'placeholder="ex.: Coletor - Entrega ao usuário">' +
                    '<button id="au-t-ok" class="btn btn-secondary">Testar</button>' +
                '</div>' +
                '<p class="text-muted" style="font-size:.8rem;margin:6px 0 0">' +
                    'Só consulta as regras — não toca em chamado nenhum.</p>' +
                '<div id="au-t-saida" class="mt-2"></div>' +
            '</div></div>';

        var campo = document.getElementById('au-t-sub');
        var saida = document.getElementById('au-t-saida');

        async function rodar() {
            var sub = (campo.value || '').trim();
            if (!sub) { S.toast('Informe a subcategoria.', 'warning'); campo.focus(); return; }
            try {
                var d = await S.api('/automacoes/regras/testar?subcategoria=' +
                                    encodeURIComponent(sub));
                var e = S.esc, html = '';
                if (!d.vencedora) {
                    html = '<div class="alert alert-warning">Nenhuma regra casa com ' +
                           '<b>' + e(d.subcategoria) + '</b> — o chamado seria ignorado.</div>';
                } else {
                    var v = d.vencedora;
                    var oque = v.acao === 'encaminhar'
                        ? 'encaminhado para <b>' + e(v.fila_destino || '(fila não informada)') + '</b>'
                        : '<b>encerrado</b>';
                    html = '<div class="alert alert-success">' +
                           e(d.subcategoria) + ' → ' + oque +
                           ' pela regra <b>' + e(v.nome) + '</b> (ordem ' + e(v.ordem) + ').</div>';
                }
                var outras = (d.candidatas || []).filter(function (c) {
                    return !d.vencedora || c.nome !== d.vencedora.nome;
                });
                if (outras.length) {
                    html += '<p class="text-muted" style="margin-bottom:4px">' +
                            'Outras que também casam (perderam por serem menos específicas):</p><ul>';
                    outras.forEach(function (c) {
                        html += '<li>' + e(c.nome) + ' — ' + (c.acao === 'encaminhar'
                                ? 'encaminhar p/ ' + e(c.fila_destino || '—') : 'encerrar') +
                                ' <span class="text-muted">(por "' + e(c.apelido) + '", ' +
                                e(c.casou_por) + ')</span></li>';
                    });
                    html += '</ul>';
                }
                saida.innerHTML = html;
            } catch (er) {
                saida.innerHTML = '<div class="alert alert-danger">' + S.esc(er.message) + '</div>';
            }
        }

        document.getElementById('au-t-ok').onclick = rodar;
        campo.addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter') { ev.preventDefault(); rodar(); }
        });
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
