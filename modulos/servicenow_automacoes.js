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

    // Duas telas sob o mesmo item da barra lateral: a rotina que age nos
    // chamados (Automações) e a consulta em lote, que só lê. Ficam juntas
    // porque quem cria a regra é quem precisa conferir, na lista, se ela
    // pegou os chamados certos.
    render(container, sub) {
        var S = window.SPARE;
        var ABAS = [
            ['automacoes', 'Automações'],
            ['consulta',   'Consulta de chamados']
        ];
        sub = sub || 'automacoes';
        if (!ABAS.some(function (x) { return x[0] === sub; })) sub = 'automacoes';
        S.tabs(ABAS, sub, 'servicenow_automacoes');
        var tela = (sub === 'consulta') ? renderConsulta : renderAutomacoes;
        container.innerHTML =
            '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando...</div>';
        Promise.resolve(tela(container, S)).catch(function (e) {
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


/* ================================================================
   Aba: Consulta de chamados (incidents e RITMs)

   O fluxo é o de quem monta uma extração: escolhe a tabela, escolhe as
   colunas, filtra, confere uma amostra na tela e baixa o arquivo com o
   resultado inteiro.

   A amostra é de propósito: trazer 5 mil linhas para o navegador trava a
   aba e não ajuda a conferir nada. O que a tela precisa dizer é QUANTOS
   chamados a busca pegou — isso vem contado do servidor — para a pessoa
   decidir se estreita o filtro antes de exportar.
   ================================================================ */
async function renderConsulta(c, S) {
    var e = S.esc;
    var u = S.user() || {};
    var perm = (u.permission_map || {}).automacoes || {};
    var podeExportar = !!(u.is_admin || perm.can_export);

    var meta = null;        // tabelas e operadores
    var campos = [];        // campos da tabela escolhida
    var porNome = {};       // campo -> {rotulo, tipo}
    var escolhidos = [];    // colunas da visão, na ordem
    var ultimo = null;      // última resposta do /buscar

    c.innerHTML =
        '<h1 class="page-title">Consulta de chamados</h1>' +
        '<p class="text-muted">Lê o ServiceNow pela conta de serviço (só leitura). ' +
            'Os campos oferecidos são os que essa conta consegue ler de verdade — ' +
            'campo que a permissão dela barra não entra na lista, para não virar ' +
            'coluna vazia no arquivo.</p>' +
        '<div class="card mb-3"><div class="card-header">O que consultar</div>' +
            '<div class="card-body">' +
                '<div class="form-row">' +
                    '<div class="form-group"><label for="cn-tabela">Tabela</label>' +
                        '<select id="cn-tabela" class="form-control"></select></div>' +
                    '<div class="form-group"><label for="cn-ordem-campo">Ordenar por</label>' +
                        '<select id="cn-ordem-campo" class="form-control"></select></div>' +
                    '<div class="form-group"><label for="cn-ordem-dir">Ordem</label>' +
                        '<select id="cn-ordem-dir" class="form-control">' +
                            '<option value="desc">Mais recente primeiro</option>' +
                            '<option value="asc">Mais antigo primeiro</option>' +
                        '</select></div>' +
                '</div>' +
                '<div id="cn-conta" class="text-muted" style="font-size:12px"></div>' +
                '<div id="cn-problemas" class="mt-2"></div>' +
                '<div class="btn-row mt-2">' +
                    '<button id="cn-recarregar" class="btn btn-sm btn-secondary" type="button">' +
                        'Recarregar campos</button>' +
                '</div>' +
            '</div></div>' +
        '<div class="card mb-3"><div class="card-header" ' +
            'style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">' +
            '<span>Chamados a consultar</span>' +
            '<span id="cn-num-conta" class="text-muted" style="font-size:12px"></span>' +
            '</div>' +
            '<div class="card-body">' +
                '<p class="text-muted" style="margin-top:0">Cole os números — um por linha, ' +
                    'ou separados por vírgula/espaço. Colar uma coluna da planilha funciona. ' +
                    'Passa de 5 mil: a lista é partida em blocos, porque a consulta vai na ' +
                    'URL e 5 mil números de uma vez não cabem nela.<br>' +
                    '<b>Deixe vazio</b> para buscar pelos filtros abaixo, em vez de por lista.</p>' +
                '<div class="form-group"><label for="cn-numeros">Números dos chamados</label>' +
                    '<textarea id="cn-numeros" class="form-control" rows="4" ' +
                    'placeholder="INC1234567&#10;RITM1234567&#10;INC1234568"></textarea></div>' +
                '<div class="btn-row"><button id="cn-num-limpar" class="btn btn-sm btn-secondary" ' +
                    'type="button">Limpar lista</button></div>' +
            '</div></div>' +
        '<div class="card mb-3"><div class="card-header" ' +
            'style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">' +
            '<span>Colunas da visão</span>' +
            '<span><input id="cn-busca-campo" class="form-control form-control-inline" ' +
                'placeholder="Filtrar campos" style="min-width:200px"> ' +
            '<button id="cn-campos-padrao" class="btn btn-sm btn-secondary" type="button">Padrão</button> ' +
            '<button id="cn-campos-limpar" class="btn btn-sm btn-secondary" type="button">Limpar</button></span>' +
            '</div>' +
            '<div class="card-body">' +
                '<div id="cn-escolhidos" class="mb-3"></div>' +
                '<div id="cn-campos" style="max-height:260px;overflow:auto;border:1px solid var(--borda);' +
                    'border-radius:6px;padding:8px"></div>' +
            '</div></div>' +
        '<div class="card mb-3"><div class="card-header" ' +
            'style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">' +
            '<span>Tempo na fila (opcional)</span>' +
            '<button id="cn-tf-fontes" class="btn btn-sm btn-secondary" type="button">' +
                'Conferir se dá para medir</button>' +
            '</div>' +
            '<div class="card-body">' +
                '<p class="text-muted" style="margin-top:0">Mede quanto tempo cada chamado ' +
                    'ficou numa fila cujo nome CONTÉM o texto abaixo — somando todas as ' +
                    'passagens, se o chamado foi e voltou. A conta sai do histórico de troca ' +
                    'de fila do ServiceNow, então custa uma leitura a mais: deixe vazio para ' +
                    'não medir.</p>' +
                '<div class="form-row">' +
                    '<div class="form-group"><label for="cn-tempo-fila">Fila a medir</label>' +
                        '<input id="cn-tempo-fila" class="form-control" placeholder="SPARE" ' +
                        'style="max-width:260px"></div>' +
                '</div>' +
                '<div id="cn-tf-saida" class="mt-2"></div>' +
            '</div></div>' +
        '<div class="card mb-3"><div class="card-header" ' +
            'style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">' +
            '<span>Filtros</span>' +
            '<button id="cn-filtro-add" class="btn btn-sm btn-primary" type="button">Novo filtro</button>' +
            '</div>' +
            '<div class="card-body"><div id="cn-filtros"></div>' +
            '<p class="text-muted" style="font-size:12px;margin:10px 0 0">' +
                'Datas no formato <code>AAAA-MM-DD</code> (hora opcional: ' +
                '<code>AAAA-MM-DD HH:MM:SS</code>), no fuso do ServiceNow. ' +
                'Para um período, use dois filtros no mesmo campo: ' +
                '<b>a partir de</b> e <b>até</b>.</p>' +
            '</div></div>' +
        '<div class="card mb-3"><div class="card-header">Coleta pronta</div>' +
            '<div class="card-body">' +
                '<p class="text-muted" style="margin-top:0">Chamados das quatro filas de ' +
                    '<b>técnico de campo</b> (ENACEL_LOJAS, RNR_VSiT, SKY_LOJAS, ' +
                    'RNR_LOJAS_REMOTO), pela tabela <code>task_sla</code>, com o tempo que ' +
                    'cada um ficou na fila dele. Uma linha por chamado, coluna de mês, e ' +
                    'resumo por mês e fila no fim do arquivo.<br>' +
                    'Período longo demora: são duas passadas no ServiceNow, a segunda em ' +
                    'lotes. Comece por um trimestre se quiser conferir antes.</p>' +
                '<div class="form-row">' +
                    '<div class="form-group"><label for="cn-d2-desde">De</label>' +
                        '<input id="cn-d2-desde" class="form-control" type="date" ' +
                        'value="2025-01-01" style="max-width:190px"></div>' +
                    '<div class="form-group"><label for="cn-d2-ate">Até</label>' +
                        '<input id="cn-d2-ate" class="form-control" type="date" ' +
                        'style="max-width:190px"></div>' +
                '</div>' +
                '<div class="btn-row mt-2">' +
                    (podeExportar
                        ? '<button id="cn-dados2" class="btn btn-primary" type="button">' +
                              'Exportar - DADOS 2</button>'
                        : '<span class="text-muted">Exportar pede a permissão própria.</span>') +
                '</div>' +
            '</div></div>' +
        '<div class="card mb-3"><div class="card-body btn-row">' +
            '<button id="cn-buscar" class="btn btn-primary" type="button">Consultar</button>' +
            (podeExportar
                ? '<button id="cn-exportar" class="btn btn-secondary" type="button" disabled>Exportar CSV</button>'
                : '') +
            '<span id="cn-resumo" class="text-muted" style="align-self:center"></span>' +
            '</div></div>' +
        '<div id="cn-saida"></div>';

    function opcoesDeCampo(sel, vazio) {
        sel.innerHTML = '';
        if (vazio) sel.appendChild(S.el('option', { value: '', textContent: vazio }));
        campos.forEach(function (k) {
            sel.appendChild(S.el('option', { value: k.campo,
                textContent: k.rotulo + ' (' + k.campo + ')' }));
        });
    }

    /* ── Colunas ──────────────────────────────────────────────── */
    function desenharEscolhidos() {
        var host = document.getElementById('cn-escolhidos');
        host.innerHTML = '';
        if (!escolhidos.length) {
            host.appendChild(S.el('span', { className: 'text-muted',
                textContent: 'Nenhuma coluna marcada — a consulta usa o conjunto padrão da tabela.' }));
            return;
        }
        // A ORDEM das colunas é a de marcação, e é a ordem do arquivo. As
        // setas existem porque ninguém marca na ordem em que quer ler.
        escolhidos.forEach(function (nome, i) {
            var k = porNome[nome] || { rotulo: nome };
            var chip = S.el('span', { className: 'badge badge-neutral',
                style: 'margin:0 6px 6px 0;display:inline-flex;align-items:center;gap:6px' });
            chip.appendChild(S.el('span', { textContent: (i + 1) + '. ' + k.rotulo }));
            [['↑', -1], ['↓', 1]].forEach(function (mov) {
                chip.appendChild(S.el('button', {
                    className: 'btn btn-sm btn-secondary', type: 'button',
                    style: 'padding:0 5px;line-height:1.2', textContent: mov[0],
                    onClick: function () {
                        var j = i + mov[1];
                        if (j < 0 || j >= escolhidos.length) return;
                        var tmp = escolhidos[i]; escolhidos[i] = escolhidos[j]; escolhidos[j] = tmp;
                        desenharEscolhidos();
                    }
                }));
            });
            chip.appendChild(S.el('button', {
                className: 'btn btn-sm btn-danger', type: 'button',
                style: 'padding:0 6px;line-height:1.2', textContent: '×',
                onClick: function () {
                    escolhidos = escolhidos.filter(function (x) { return x !== nome; });
                    desenharEscolhidos(); desenharCampos();
                }
            }));
            host.appendChild(chip);
        });
    }

    function desenharCampos() {
        var filtro = (document.getElementById('cn-busca-campo').value || '').trim().toLowerCase();
        var host = document.getElementById('cn-campos');
        host.innerHTML = '';
        var visiveis = campos.filter(function (k) {
            return !filtro || k.campo.toLowerCase().indexOf(filtro) !== -1
                || (k.rotulo || '').toLowerCase().indexOf(filtro) !== -1;
        });
        if (!visiveis.length) {
            host.appendChild(S.el('div', { className: 'text-muted',
                textContent: 'Nenhum campo com esse texto.' }));
            return;
        }
        var grade = S.el('div', { style: 'display:grid;gap:4px;' +
            'grid-template-columns:repeat(auto-fill,minmax(260px,1fr))' });
        visiveis.forEach(function (k) {
            var id = 'cn-cp-' + k.campo.replace(/[^a-z0-9_]/gi, '_');
            var linha = S.el('label', { className: 'form-check',
                style: 'display:flex;gap:6px;align-items:baseline', 'for': id });
            var cx = S.el('input', { type: 'checkbox', id: id });
            cx.checked = escolhidos.indexOf(k.campo) !== -1;
            cx.onchange = function () {
                if (cx.checked) {
                    if (escolhidos.indexOf(k.campo) === -1) escolhidos.push(k.campo);
                } else {
                    escolhidos = escolhidos.filter(function (x) { return x !== k.campo; });
                }
                desenharEscolhidos();
            };
            linha.appendChild(cx);
            /* `lido` é o que a sonda achou: true = a conta leu o campo num
               registro real; false = não leu (provavelmente ACL, e a coluna
               virá vazia); null = não deu para saber. A marca existe porque
               antes o campo era simplesmente removido da lista — e campo
               sumido é indistinguível de defeito. */
            var marca = '';
            if (k.lido === false) {
                marca = ' <span class="badge badge-warning" title="A conta de ' +
                    'serviço não conseguiu ler este campo num registro real. ' +
                    'Provavelmente permissão: a coluna virá vazia.">sem leitura</span>';
            } else if (k.lido === null || k.lido === undefined) {
                marca = ' <span class="badge badge-neutral" title="Não deu para ' +
                    'conferir se a conta lê este campo. Ele é oferecido assim mesmo.">?</span>';
            }
            var rot = S.el('span');
            rot.innerHTML = e(k.rotulo) + ' <span class="text-muted" style="font-size:11px">' +
                e(k.campo) + (k.tipo ? ' · ' + e(k.tipo) : '') + '</span>' + marca;
            linha.appendChild(rot);
            grade.appendChild(linha);
        });
        host.appendChild(grade);
    }

    /* ── Filtros ──────────────────────────────────────────────── */
    function linhaFiltro(inicial) {
        inicial = inicial || {};
        var linha = S.el('div', { className: 'form-row', style: 'align-items:flex-end' });
        var selCampo = S.el('select', { className: 'form-control' });
        opcoesDeCampo(selCampo, '— campo —');
        if (inicial.campo) selCampo.value = inicial.campo;
        var selOp = S.el('select', { className: 'form-control' });
        (meta.operadores || []).forEach(function (o) {
            selOp.appendChild(S.el('option', { value: o.chave, textContent: o.rotulo }));
        });
        if (inicial.operador) selOp.value = inicial.operador;
        var txt = S.el('input', { className: 'form-control', placeholder: 'valor',
            value: inicial.valor || '' });
        // "Está vazio" e "está preenchido" não levam valor. Deixar a caixa
        // habilitada convida a digitar algo que o servidor vai recusar.
        function ajustar() {
            var o = (meta.operadores || []).filter(function (x) { return x.chave === selOp.value; })[0];
            var precisa = !o || o.precisa_valor;
            txt.disabled = !precisa;
            if (!precisa) txt.value = '';
            txt.placeholder = (selOp.value === 'em' || selOp.value === 'nao_em')
                ? 'valores separados por vírgula' : 'valor';
        }
        selOp.onchange = ajustar; ajustar();

        [['Campo', selCampo], ['Condição', selOp], ['Valor', txt]].forEach(function (x) {
            var g = S.el('div', { className: 'form-group' });
            g.appendChild(S.el('label', { textContent: x[0] }));
            g.appendChild(x[1]);
            linha.appendChild(g);
        });
        var g = S.el('div', { className: 'form-group' });
        var vazio = S.el('label');
        vazio.innerHTML = '&nbsp;';
        g.appendChild(vazio);
        g.appendChild(S.el('button', { className: 'btn btn-danger', type: 'button',
            textContent: 'Remover', onClick: function () { linha.remove(); } }));
        linha.appendChild(g);
        linha._ler = function () {
            return { campo: selCampo.value, operador: selOp.value, valor: txt.value };
        };
        return linha;
    }

    function lerFiltros() {
        return Array.prototype.slice
            .call(document.getElementById('cn-filtros').children)
            .map(function (l) { return l._ler(); })
            .filter(function (f) { return f.campo; });
    }

    function corpoDaConsulta() {
        return {
            tabela: document.getElementById('cn-tabela').value,
            campos: escolhidos.slice(),
            filtros: lerFiltros(),
            numeros: document.getElementById('cn-numeros').value,
            tempo_fila: document.getElementById('cn-tempo-fila').value.trim(),
            ordenar_por: document.getElementById('cn-ordem-campo').value,
            ordem: document.getElementById('cn-ordem-dir').value,
            exibir_rotulos: true
        };
    }

    /* Conta o que foi colado do mesmo jeito que o servidor conta: mesmos
       separadores, mesma remoção de repetido. Se a tela dissesse 5.000 e o
       servidor 4.812, seria a tela mentindo — e a diferença (repetidos) é
       justamente o que a pessoa quer saber antes de consultar. */
    function contarNumeros() {
        var bruto = document.getElementById('cn-numeros').value || '';
        var vistos = Object.create(null), n = 0, repetidos = 0;
        bruto.split(/[\s,;]+/).forEach(function (x) {
            var v = x.trim().replace(/^["']|["']$/g, '').toUpperCase();
            if (!v) return;
            if (vistos[v]) { repetidos++; return; }
            vistos[v] = 1; n++;
        });
        var alvo = document.getElementById('cn-num-conta');
        alvo.textContent = n
            ? (n.toLocaleString('pt-BR') + ' chamado(s)' +
               (repetidos ? ' · ' + repetidos + ' repetido(s) descartado(s)' : '') +
               ' · ' + Math.ceil(n / 250) + ' bloco(s)')
            : 'lista vazia — a consulta vai pelos filtros';
        return n;
    }

    /* ── Carga ────────────────────────────────────────────────── */
    async function carregarCampos(recarregar) {
        var tabela = document.getElementById('cn-tabela').value;
        var host = document.getElementById('cn-campos');
        host.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> ' +
            'Perguntando ao ServiceNow quais campos a conta de serviço lê…</div>';
        var d = await S.api('/sn-consulta/campos?tabela=' + encodeURIComponent(tabela) +
            (recarregar ? '&recarregar=true' : ''));
        campos = d.campos || [];
        porNome = {};
        campos.forEach(function (k) { porNome[k.campo] = k; });
        /* Antes isto descartava o padrão que não estivesse em `porNome` — e
           como a descoberta podia perder campos, a tela abria sem o número do
           chamado e sem a data de abertura, calada. Agora o servidor repõe o
           padrão que faltar (marcado como não conferido) e avisa; o filtro
           aqui só protege contra campo que a tabela realmente não tem. */
        escolhidos = (d.campos_padrao || []).filter(function (x) { return porNome[x]; });
        var conta = document.getElementById('cn-conta');
        conta.textContent = d.total + ' campos em ' + (d.hierarquia || []).join(' → ') +
            ' · conta ' + (d.conta || '(não configurada)') +
            (d.nao_lidos ? ' · ' + d.nao_lidos + ' sem leitura' : '') +
            (d.sem_resposta ? ' · ' + d.sem_resposta + ' não conferidos' : '') +
            (d.do_cache ? ' · lista em cache' : '');

        /* Quando a descoberta vem incompleta, a tela DIZ. Foi o que faltou
           quando `number` e `opened_at` sumiram: sem aviso, campo ausente
           parece defeito da tela, e não da consulta ao ServiceNow. */
        var problemas = document.getElementById('cn-problemas');
        problemas.innerHTML = '';
        var sonda = d.sonda || {};
        if ((d.padrao_ausentes || []).length) {
            var a1 = S.el('div', { className: 'alert alert-warning mb-2' });
            a1.innerHTML = '<b>A descoberta de campos veio incompleta.</b> Estes não ' +
                'vieram do dicionário do ServiceNow e foram repostos pelo portal: <code>' +
                e(d.padrao_ausentes.join(', ')) + '</code>. Dá para usá-los normalmente; ' +
                'o que pode faltar são OUTROS campos da tabela-mãe. Use <b>Recarregar ' +
                'campos</b>; se persistir, é a conta de serviço sem leitura em ' +
                '<code>sys_db_object</code> ou <code>sys_dictionary</code>.';
            problemas.appendChild(a1);
        }
        if ((sonda.erros || []).length) {
            var a2 = S.el('div', { className: 'alert alert-warning mb-2' });
            a2.innerHTML = '<b>Parte da conferência de permissão falhou</b>, então alguns ' +
                'campos aparecem marcados com “?”. Eles funcionam; só não foi possível ' +
                'confirmar se a conta os lê.<br><span style="font-size:11px">' +
                e(sonda.erros.join(' · ')) + '</span>';
            problemas.appendChild(a2);
        }
        if (sonda.sondou === false && sonda.motivo) {
            problemas.appendChild(S.el('div', { className: 'alert alert-info mb-2',
                textContent: 'Todos os campos aparecem sem confirmação de leitura: ' +
                    sonda.motivo + '.' }));
        }
        var ordem = document.getElementById('cn-ordem-campo');
        opcoesDeCampo(ordem, '— sem ordenação —');
        ordem.value = porNome.opened_at ? 'opened_at'
            : (porNome.sys_created_on ? 'sys_created_on' : '');
        // Os filtros são por campo, e os campos mudam com a tabela — por isso
        // eles se vão. A LISTA de chamados fica: trocar de Incidentes para
        // RITMs para procurar os mesmos números é exatamente o que se faz
        // quando parte da lista não aparece.
        document.getElementById('cn-filtros').innerHTML = '';
        desenharCampos(); desenharEscolhidos();
    }

    async function consultar(pagina) {
        var saida = document.getElementById('cn-saida');
        var resumo = document.getElementById('cn-resumo');
        saida.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Consultando…</div>';
        resumo.textContent = '';
        var corpo = corpoDaConsulta();
        corpo.pagina = pagina || 1;
        corpo.por_pagina = 100;
        var d;
        try {
            d = await S.api('/sn-consulta/buscar', { method: 'POST', body: corpo });
        } catch (x) {
            saida.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            if (podeExportar) document.getElementById('cn-exportar').disabled = true;
            return;
        }
        ultimo = d;
        if (podeExportar) document.getElementById('cn-exportar').disabled = d.total === 0;

        var de = (d.pagina - 1) * d.por_pagina + 1;
        var ate = Math.min(d.pagina * d.por_pagina, d.total);
        if (d.por_lista) {
            resumo.textContent = d.total.toLocaleString('pt-BR') + ' de ' +
                d.pedidos.toLocaleString('pt-BR') + ' chamados encontrados' +
                (d.nao_encontrados_total
                    ? ' · ' + d.nao_encontrados_total.toLocaleString('pt-BR') + ' não encontrado(s)'
                    : '') + '.';
        } else {
            resumo.textContent = d.total
                ? ('Mostrando ' + de + '–' + ate + ' de ' + d.total.toLocaleString('pt-BR') + ' chamados.')
                : 'Nenhum chamado com esses filtros.';
        }

        saida.innerHTML = '';

        /* "Não encontrado" tem três causas e a tela precisa dizer as três,
           senão a conclusão vira "o chamado não existe" — que é só uma
           delas, e normalmente a errada. */
        if (d.por_lista && d.nao_encontrados_total) {
            var av = S.el('div', { className: 'alert alert-warning mb-3' });
            var tit = S.el('div');
            tit.innerHTML = '<b>' + d.nao_encontrados_total.toLocaleString('pt-BR') +
                ' chamado(s) da sua lista não vieram.</b> Pode ser: o número não existe; ' +
                'está em outra tabela (um RITM procurado em Incidentes não aparece); ' +
                'ou os filtros abaixo o excluíram.';
            av.appendChild(tit);
            av.appendChild(S.el('div', {
                className: 'mt-2',
                style: 'font-family:monospace;font-size:11px;max-height:120px;overflow:auto',
                textContent: d.nao_encontrados.join(', ') +
                    (d.nao_encontrados_total > d.nao_encontrados.length
                        ? ' … (+' + (d.nao_encontrados_total - d.nao_encontrados.length) +
                          '; a lista completa vai no CSV)'
                        : '')
            }));
            saida.appendChild(av);
        }

        /* Medir 5 mil chamados para mostrar 100 na tela seriam 20 leituras do
           histórico jogadas fora. A tela mede a amostra; o CSV mede tudo — e
           isso precisa estar dito, senão a média da tela parece a do total. */
        if (d.tempo_fila_so_amostra) {
            saida.appendChild(S.el('div', { className: 'alert alert-info mb-3',
                textContent: 'O tempo de fila na tela foi medido só nas ' +
                    d.linhas.length + ' linhas mostradas. A exportação mede todas.' }));
        }

        if (d.total > d.teto_exportacao) {
            saida.appendChild(S.el('div', { className: 'alert alert-warning',
                textContent: 'A busca pegou ' + d.total.toLocaleString('pt-BR') +
                    ' chamados e a exportação para em ' + d.teto_exportacao.toLocaleString('pt-BR') +
                    '. Estreite por período e exporte em partes, senão o arquivo sai incompleto.' }));
        }
        if (!d.total) return;
        var cols = d.campos.map(function (nome) {
            return { key: nome, label: (d.rotulos || {})[nome] || nome };
        });
        saida.appendChild(S.table(cols, d.linhas));

        // Por lista não há paginação: a consulta já percorreu os blocos todos
        // para saber quem faltou, e a tela mostra a primeira fatia. O resto
        // sai no CSV — paginar de novo custaria outra varredura inteira.
        if (d.por_lista) {
            if (d.total > d.linhas.length) {
                saida.appendChild(S.el('p', { className: 'text-muted mt-3',
                    textContent: 'Mostrando os primeiros ' + d.linhas.length +
                        ' na tela. Exporte para ver os ' + d.total.toLocaleString('pt-BR') + '.' }));
            }
            return;
        }

        // Paginação da amostra. Quem vai até a página 40 devia estar
        // exportando — mas fechar a porta seria pior que deixar aberta.
        var paginas = Math.ceil(d.total / d.por_pagina);
        if (paginas > 1) {
            var nav = S.el('div', { className: 'btn-row mt-3' });
            // `disabled` sai do objeto de propriedade: S.el() usa
            // setAttribute, e `disabled="false"` desabilita o botão do mesmo
            // jeito que `disabled="true"` — o atributo vale pela presença.
            var btAnt = S.el('button', { className: 'btn btn-sm btn-secondary',
                type: 'button', textContent: '← Anterior',
                onClick: function () { consultar(d.pagina - 1); } });
            btAnt.disabled = d.pagina <= 1;
            nav.appendChild(btAnt);
            nav.appendChild(S.el('span', { className: 'text-muted',
                style: 'align-self:center',
                textContent: 'Página ' + d.pagina + ' de ' + paginas.toLocaleString('pt-BR') }));
            var btProx = S.el('button', { className: 'btn btn-sm btn-secondary',
                type: 'button', textContent: 'Próxima →',
                onClick: function () { consultar(d.pagina + 1); } });
            btProx.disabled = d.pagina >= paginas;
            nav.appendChild(btProx);
            saida.appendChild(nav);
        }
    }

    /* ── Ligações ─────────────────────────────────────────────── */
    meta = await S.api('/sn-consulta/tabelas');
    var selTabela = document.getElementById('cn-tabela');
    (meta.tabelas || []).forEach(function (t) {
        selTabela.appendChild(S.el('option', { value: t.tabela,
            textContent: t.rotulo + ' — ' + t.tela }));
    });
    selTabela.onchange = function () { carregarCampos(false).catch(function (x) {
        document.getElementById('cn-campos').innerHTML =
            '<div class="alert alert-danger">' + e(x.message) + '</div>';
    }); };

    document.getElementById('cn-recarregar').onclick = function () {
        // A lista fica meia hora em cache no servidor. Sem um jeito de forçar,
        // corrigir permissão no ServiceNow e não ver efeito por 30 minutos
        // parece que a correção não funcionou.
        carregarCampos(true).catch(function (x) {
            document.getElementById('cn-campos').innerHTML =
                '<div class="alert alert-danger">' + e(x.message) + '</div>';
        });
    };
    document.getElementById('cn-busca-campo').addEventListener('input', desenharCampos);
    document.getElementById('cn-campos-padrao').onclick = function () {
        var t = (meta.tabelas || []).filter(function (x) {
            return x.tabela === selTabela.value; })[0] || {};
        escolhidos = (t.campos_padrao || []).filter(function (x) { return porNome[x]; });
        desenharEscolhidos(); desenharCampos();
    };
    document.getElementById('cn-campos-limpar').onclick = function () {
        escolhidos = []; desenharEscolhidos(); desenharCampos();
    };
    document.getElementById('cn-filtro-add').onclick = function () {
        document.getElementById('cn-filtros').appendChild(linhaFiltro());
    };
    /* A conferência da fonte antes de confiar no número. Sem auditoria de
       assignment_group, "0 h de fila" pode ser "nunca passou" ou "o histórico
       não está guardado" — e as duas leituras levam a decisões opostas. */
    document.getElementById('cn-tf-fontes').onclick = async function () {
        var alvo = document.getElementById('cn-tf-saida');
        alvo.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> ' +
            'Perguntando ao ServiceNow…</div>';
        try {
            var d = await S.api('/sn-consulta/tempo-fila/fontes?tabela=' +
                encodeURIComponent(document.getElementById('cn-tabela').value));
            alvo.innerHTML = '';
            alvo.appendChild(S.el('div', {
                className: 'alert alert-' + (d.pode_medir ? 'success' : 'warning'),
                textContent: d.recado
            }));
            alvo.appendChild(S.table([
                { key: 'rotulo', label: 'Fonte' },
                { key: 'fonte', label: 'Tabela' },
                { key: 'acessivel', label: 'A conta lê', html: true,
                  render: function (v) {
                      return '<span class="badge badge-' + (v ? 'success' : 'danger') +
                          '">' + (v ? 'sim' : 'não') + '</span>';
                  } },
                { key: 'tem_dado', label: 'Tem registro', html: true,
                  render: function (v) {
                      return '<span class="badge badge-' + (v ? 'success' : 'warning') +
                          '">' + (v ? 'sim' : 'não') + '</span>';
                  } },
                { key: 'explica', label: 'Para quê' }
            ], d.fontes || []));
        } catch (x) {
            alvo.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
        }
    };

    document.getElementById('cn-numeros').addEventListener('input', contarNumeros);
    document.getElementById('cn-num-limpar').onclick = function () {
        document.getElementById('cn-numeros').value = '';
        contarNumeros();
    };
    contarNumeros();
    document.getElementById('cn-buscar').onclick = function () { consultar(1); };

    /* DADOS 2: a coleta de técnico de campo. Filas, colunas e o cálculo do
       tempo são do servidor (routers/sn_campo_lojas.py) — a mesma lógica que
       scripts/chamados_campo_lojas.py usa, para o botão e o script não darem
       números diferentes para a mesma pergunta. */
    var d2Ate = document.getElementById('cn-d2-ate');
    if (d2Ate && !d2Ate.value) d2Ate.value = new Date().toISOString().slice(0, 10);

    if (podeExportar) {
        document.getElementById('cn-dados2').onclick = async function () {
            var b = this, antes = b.textContent;
            var desde = document.getElementById('cn-d2-desde').value;
            var ate = document.getElementById('cn-d2-ate').value;
            if (!desde || !ate) { S.toast('Informe o período.', 'warning'); return; }
            b.disabled = true;
            // Sem barra de progresso: o servidor faz as duas passadas e só
            // então manda o arquivo. O que dá para prometer é dizer que está
            // trabalhando — e o arquivo avisa, no fim, se parou no meio.
            b.textContent = 'Coletando… pode demorar';
            try {
                var r = await S.api('/sn-consulta/campo-lojas/exportar', {
                    method: 'POST', body: { desde: desde, ate: ate }
                });
                var blob = await r.blob();
                var a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = 'campo_lojas_' + desde + '_a_' + ate + '.csv';
                a.click();
                URL.revokeObjectURL(a.href);
                S.toast('Arquivo gerado. Confira as últimas linhas: elas dizem se a ' +
                        'coleta terminou inteira.', 'success');
            } catch (x) {
                S.toast(x.message, 'error');
            } finally {
                b.textContent = antes; b.disabled = false;
            }
        };
    }

    if (podeExportar) {
        document.getElementById('cn-exportar').onclick = async function () {
            var b = this;
            b.disabled = true;
            var antes = b.textContent;
            // Não há barra de progresso: o servidor pagina o ServiceNow e só
            // manda o arquivo. O que dá para prometer é que o botão avisa
            // que está trabalhando — e que o arquivo diz, na última linha,
            // se parou no teto.
            b.textContent = 'Exportando…';
            try {
                var r = await S.api('/sn-consulta/exportar', {
                    method: 'POST', body: corpoDaConsulta()
                });
                var blob = await r.blob();
                var a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = (ultimo ? ultimo.tabela : 'chamados') + '.csv';
                a.click();
                URL.revokeObjectURL(a.href);
            } catch (x) {
                S.toast(x.message, 'error');
            } finally {
                b.textContent = antes; b.disabled = false;
            }
        };
    }

    await carregarCampos(false);
}
