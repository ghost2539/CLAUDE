/* ================================================================
   Módulo: CAPEX Spare — Controle de Orçamento do SPARE
   Board manual (não puxa do EBS). Núcleo fixo (identificação,
   classificação, os 4 valores, datas) + campos personalizados que o
   admin do módulo define. Banco próprio (/api/orcamento-spare).
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.orcamento_spare = {

    async render(container) {
        var S = window.SPARE, e = S.esc;
        var u = S.user() || {};
        var pm = (u.permission_map || {}).orcamento_spare || {};
        var podeCriar = !!(u.is_admin || pm.can_create);
        var podeEditar = !!(u.is_admin || pm.can_edit);
        var podeAdmin = !!(u.is_admin || pm.can_admin);

        var OPC = { tipos: [], situacoes: [] };
        var CAMPOS = [];   // campos personalizados ativos

        function money(v) {
            v = Number(v || 0);
            try { return S.money ? S.money(v) : v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }); }
            catch (x) { return 'R$ ' + v.toFixed(2); }
        }

        container.innerHTML =
            '<h1 class="page-title">CAPEX Spare</h1>' +
            '<div id="os-resumo" class="filter-grid" style="margin-bottom:14px"></div>' +
            '<div class="card mb-3"><div class="card-header" ' +
                'style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">' +
                '<span>Projetos</span><span style="display:flex;gap:8px">' +
                (podeAdmin ? '<button id="os-campos" class="btn btn-secondary btn-sm">Campos personalizados</button>' : '') +
                (podeCriar ? '<button id="os-novo" class="btn btn-primary btn-sm">Novo projeto</button>' : '') +
                '</span></div><div class="card-body">' +
                '<div class="form-group" style="max-width:360px">' +
                    '<label for="os-busca">Buscar (nº, nome, responsável)</label>' +
                    '<input id="os-busca" class="form-control" placeholder="digite e Enter"></div>' +
                '<div id="os-lista" class="mt-3"></div>' +
            '</div></div>' +
            '<div class="card" id="os-form-card" style="display:none">' +
                '<div class="card-header" id="os-form-titulo">Novo projeto</div>' +
                '<div class="card-body"><div id="os-form"></div></div>' +
            '</div>' +
            '<div class="card" id="os-campos-card" style="display:none">' +
                '<div class="card-header">Campos personalizados</div>' +
                '<div class="card-body"><div id="os-campos-box"></div></div>' +
            '</div>';

        var elLista = document.getElementById('os-lista');
        var dados = { projetos: [], totais: {}, campos: [] };

        function selOpts(lista, sel) {
            return lista.map(function (o) {
                return '<option value="' + e(o) + '"' + (o === sel ? ' selected' : '') + '>' + e(o) + '</option>';
            }).join('');
        }

        // ── Carregar ──────────────────────────────────────────────────
        async function carregar() {
            elLista.innerHTML = '<p class="text-muted">Carregando…</p>';
            try { dados = await S.api('/orcamento-spare/projetos'); }
            catch (x) { elLista.innerHTML = '<p class="alert alert-danger">' + e(x.message) + '</p>'; return; }
            OPC = dados.opcoes || OPC;
            CAMPOS = dados.campos || [];
            resumo(dados.totais || {});
            tabela();
        }

        function resumo(t) {
            var box = document.getElementById('os-resumo');
            function card(rot, val) {
                return '<div class="card" style="padding:10px 14px">' +
                    '<div class="text-muted" style="font-size:.8em">' + e(rot) + '</div>' +
                    '<div style="font-weight:600;font-size:1.05em">' + val + '</div></div>';
            }
            box.innerHTML =
                card('Projetos', e(t.projetos || 0)) +
                card('Aprovado', money(t.aprovado)) +
                card('Comprometido', money(t.comprometido)) +
                card('Realizado', money(t.realizado)) +
                card('A realizar', money(t.a_realizar));
        }

        function tabela() {
            var termo = (document.getElementById('os-busca').value || '').trim().toLowerCase();
            var linhas = dados.projetos.filter(function (p) {
                return !termo || [p.numero, p.nome, p.responsavel, p.categoria].some(function (c) {
                    return String(c || '').toLowerCase().indexOf(termo) !== -1;
                });
            });
            if (!linhas.length) { elLista.innerHTML = '<p class="text-muted">Nenhum projeto.</p>'; return; }
            var extraCols = CAMPOS.map(function (c) { return '<th>' + e(c.rotulo || c.chave) + '</th>'; }).join('');
            var corpo = linhas.map(function (p) {
                var extras = CAMPOS.map(function (c) {
                    var v = (p.dados || {})[c.chave];
                    if (c.tipo === 'booleano') v = v ? 'Sim' : '';
                    else if (c.tipo === 'moeda') v = (v === '' || v == null) ? '' : money(v);
                    return '<td>' + e(v == null ? '' : v) + '</td>';
                }).join('');
                var periodo = (p.inicio ? e(p.inicio) : '') + (p.fim ? ' – ' + e(p.fim) : '');
                var acoes = (podeEditar
                    ? '<button class="btn btn-secondary btn-sm os-ed" data-id="' + p.id + '">Editar</button> ' +
                      '<button class="btn btn-secondary btn-sm os-rm" data-id="' + p.id + '">Excluir</button>'
                    : '');
                return '<tr>' +
                    '<td><b>' + e(p.numero) + '</b></td>' +
                    '<td>' + e(p.nome) + '</td>' +
                    '<td>' + e(p.tipo) + '</td>' +
                    '<td>' + e(p.categoria) + '</td>' +
                    '<td>' + e(p.situacao) + '</td>' +
                    '<td>' + e(p.responsavel) + '</td>' +
                    '<td class="text-right">' + money(p.aprovado) + '</td>' +
                    '<td class="text-right">' + money(p.comprometido) + '</td>' +
                    '<td class="text-right">' + money(p.realizado) + '</td>' +
                    '<td class="text-right">' + money(p.a_realizar) + '</td>' +
                    '<td>' + periodo + '</td>' +
                    extras +
                    '<td style="white-space:nowrap">' + acoes + '</td>' +
                    '</tr>';
            }).join('');
            elLista.innerHTML =
                '<div class="table-responsive"><table class="table table-sm">' +
                '<thead><tr><th>Nº</th><th>Nome</th><th>Tipo</th><th>Categoria</th><th>Situação</th>' +
                '<th>Responsável</th><th class="text-right">Aprovado</th><th class="text-right">Comprometido</th>' +
                '<th class="text-right">Realizado</th><th class="text-right">A realizar</th><th>Período</th>' +
                extraCols + '<th></th></tr></thead><tbody>' + corpo + '</tbody></table></div>';
            Array.prototype.forEach.call(elLista.querySelectorAll('.os-ed'), function (b) {
                b.onclick = function () { abrirForm(dados.projetos.filter(function (p) { return p.id == b.dataset.id; })[0]); };
            });
            Array.prototype.forEach.call(elLista.querySelectorAll('.os-rm'), function (b) {
                b.onclick = function () { excluir(parseInt(b.dataset.id, 10)); };
            });
        }

        // ── Formulário projeto ────────────────────────────────────────
        function campo(rot, ctrl) {
            return '<div class="form-group"><label>' + e(rot) + '</label>' + ctrl + '</div>';
        }

        function inputCampoExtra(c, val) {
            var id = 'os-x-' + c.chave;
            if (c.tipo === 'lista')
                return '<select id="' + id + '" class="form-control"><option value="">—</option>' +
                    selOpts(c.opcoes || [], val) + '</select>';
            if (c.tipo === 'booleano')
                return '<select id="' + id + '" class="form-control"><option value="">—</option>' +
                    '<option value="1"' + (val ? ' selected' : '') + '>Sim</option>' +
                    '<option value="0"' + (val === false || val === 0 || val === '0' ? ' selected' : '') + '>Não</option></select>';
            var t = (c.tipo === 'numero' || c.tipo === 'moeda') ? 'number' : (c.tipo === 'data' ? 'date' : 'text');
            var step = c.tipo === 'moeda' ? ' step="0.01"' : '';
            return '<input id="' + id + '" type="' + t + '"' + step + ' class="form-control" value="' + e(val == null ? '' : val) + '">';
        }

        function abrirForm(d) {
            var edit = !!(d && d.id);
            d = d || {};
            document.getElementById('os-form-titulo').textContent = edit ? ('Editar projeto #' + d.id) : 'Novo projeto';
            document.getElementById('os-form-card').style.display = '';
            var extrasHtml = CAMPOS.map(function (c) {
                return campo((c.rotulo || c.chave) + (c.obrigatorio ? ' *' : ''), inputCampoExtra(c, (d.dados || {})[c.chave]));
            }).join('');
            document.getElementById('os-form').innerHTML =
                '<div class="form-grid cols-2">' +
                    campo('Número', '<input id="os-numero" class="form-control" value="' + e(d.numero || '') + '">') +
                    campo('Nome', '<input id="os-nome" class="form-control" value="' + e(d.nome || '') + '">') +
                    campo('Tipo', '<select id="os-tipo" class="form-control">' + selOpts(OPC.tipos, d.tipo || 'CAPEX') + '</select>') +
                    campo('Categoria', '<input id="os-categoria" class="form-control" value="' + e(d.categoria || '') + '">') +
                    campo('Situação', '<select id="os-situacao" class="form-control">' + selOpts(OPC.situacoes, d.situacao || 'Planejado') + '</select>') +
                    campo('Responsável', '<input id="os-responsavel" class="form-control" value="' + e(d.responsavel || '') + '">') +
                    campo('Aprovado', '<input id="os-aprovado" type="number" step="0.01" class="form-control" value="' + e(d.aprovado != null ? d.aprovado : 0) + '">') +
                    campo('Comprometido', '<input id="os-comprometido" type="number" step="0.01" class="form-control" value="' + e(d.comprometido != null ? d.comprometido : 0) + '">') +
                    campo('Realizado', '<input id="os-realizado" type="number" step="0.01" class="form-control" value="' + e(d.realizado != null ? d.realizado : 0) + '">') +
                    campo('A realizar', '<input id="os-arealizar" type="number" step="0.01" class="form-control" value="' + e(d.a_realizar != null ? d.a_realizar : 0) + '">') +
                    campo('Início', '<input id="os-inicio" type="date" class="form-control" value="' + e(d.inicio || '') + '">') +
                    campo('Fim', '<input id="os-fim" type="date" class="form-control" value="' + e(d.fim || '') + '">') +
                '</div>' +
                (extrasHtml ? '<h3 class="mt-3">Campos personalizados</h3><div class="form-grid cols-2">' + extrasHtml + '</div>' : '') +
                campo('Observação', '<textarea id="os-obs" class="form-control" rows="2">' + e(d.observacao || '') + '</textarea>') +
                '<div class="btn-row mt-3">' +
                    '<button type="button" id="os-salvar" class="btn btn-primary btn-sm">' + (edit ? 'Salvar' : 'Cadastrar') + '</button> ' +
                    '<button type="button" id="os-cancelar" class="btn btn-secondary btn-sm">Cancelar</button>' +
                '</div><div id="os-erro" class="alert alert-danger mt-2" hidden></div>';
            document.getElementById('os-salvar').onclick = function () { salvar(edit ? d.id : 0); };
            document.getElementById('os-cancelar').onclick = fecharForm;
            document.getElementById('os-form-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

        function fecharForm() {
            document.getElementById('os-form-card').style.display = 'none';
            document.getElementById('os-form').innerHTML = '';
        }

        function coletar() {
            function num(id) { var v = document.getElementById(id).value; return v === '' ? 0 : parseFloat(v); }
            var extras = {};
            CAMPOS.forEach(function (c) {
                var el = document.getElementById('os-x-' + c.chave);
                if (!el) return;
                var v = el.value;
                if (c.tipo === 'booleano') v = v === '1' ? true : (v === '0' ? false : '');
                else if (c.tipo === 'numero' || c.tipo === 'moeda') v = v === '' ? '' : parseFloat(v);
                if (v !== '' && v != null) extras[c.chave] = v;
            });
            return {
                numero: document.getElementById('os-numero').value.trim(),
                nome: document.getElementById('os-nome').value.trim(),
                tipo: document.getElementById('os-tipo').value,
                categoria: document.getElementById('os-categoria').value.trim(),
                situacao: document.getElementById('os-situacao').value,
                responsavel: document.getElementById('os-responsavel').value.trim(),
                aprovado: num('os-aprovado'), comprometido: num('os-comprometido'),
                realizado: num('os-realizado'), a_realizar: num('os-arealizar'),
                inicio: document.getElementById('os-inicio').value || null,
                fim: document.getElementById('os-fim').value || null,
                observacao: document.getElementById('os-obs').value.trim(),
                dados: extras
            };
        }

        async function salvar(id) {
            var erro = document.getElementById('os-erro');
            erro.hidden = true;
            var body = coletar();
            try {
                if (id) await S.api('/orcamento-spare/projetos/' + id, { method: 'PUT', body: body });
                else await S.api('/orcamento-spare/projetos', { method: 'POST', body: body });
                fecharForm();
                if (S.toast) S.toast('Projeto salvo.');
                carregar();
            } catch (x) { erro.hidden = false; erro.textContent = x.message; }
        }

        async function excluir(id) {
            if (!confirm('Excluir este projeto?')) return;
            try { await S.api('/orcamento-spare/projetos/' + id, { method: 'DELETE' }); carregar(); }
            catch (x) { alert(x.message); }
        }

        // ── Campos personalizados (admin) ─────────────────────────────
        async function abrirCampos() {
            var card = document.getElementById('os-campos-card');
            var box = document.getElementById('os-campos-box');
            card.style.display = '';
            box.innerHTML = '<p class="text-muted">Carregando…</p>';
            var d;
            try { d = await S.api('/orcamento-spare/campos'); }
            catch (x) { box.innerHTML = '<p class="alert alert-danger">' + e(x.message) + '</p>'; return; }
            var tipos = d.tipos || [];
            var linhas = (d.campos || []).map(function (c) {
                return '<tr><td>' + e(c.rotulo || c.chave) + '</td><td>' + e(c.chave) + '</td><td>' + e(c.tipo) +
                    '</td><td>' + (c.obrigatorio ? 'Sim' : '') + '</td><td>' + (c.ativo ? 'Sim' : 'Não') +
                    '</td><td><button class="btn btn-secondary btn-sm osc-rm" data-id="' + c.id + '">Excluir</button></td></tr>';
            }).join('');
            box.innerHTML =
                '<div class="table-responsive"><table class="table table-sm"><thead><tr>' +
                '<th>Rótulo</th><th>Chave</th><th>Tipo</th><th>Obrig.</th><th>Ativo</th><th></th></tr></thead>' +
                '<tbody>' + (linhas || '<tr><td colspan="6" class="text-muted">Nenhum campo.</td></tr>') + '</tbody></table></div>' +
                '<h3 class="mt-3">Novo campo</h3><div class="form-grid cols-2">' +
                    campo('Chave (técnica) *', '<input id="osc-chave" class="form-control" placeholder="ex.: centro_custo">') +
                    campo('Rótulo', '<input id="osc-rotulo" class="form-control" placeholder="ex.: Centro de Custo">') +
                    campo('Tipo', '<select id="osc-tipo" class="form-control">' + selOpts(tipos, 'texto') + '</select>') +
                    campo('Opções (lista, separadas por ;)', '<input id="osc-opcoes" class="form-control" placeholder="A;B;C">') +
                    campo('Obrigatório', '<select id="osc-obrig" class="form-control"><option value="0">Não</option><option value="1">Sim</option></select>') +
                '</div><div class="btn-row mt-2"><button id="osc-add" class="btn btn-primary btn-sm">Adicionar campo</button> ' +
                '<button id="osc-fechar" class="btn btn-secondary btn-sm">Fechar</button></div>' +
                '<div id="osc-erro" class="alert alert-danger mt-2" hidden></div>';
            Array.prototype.forEach.call(box.querySelectorAll('.osc-rm'), function (b) {
                b.onclick = async function () {
                    if (!confirm('Excluir este campo?')) return;
                    try { await S.api('/orcamento-spare/campos/' + b.dataset.id, { method: 'DELETE' }); abrirCampos(); carregar(); }
                    catch (x) { alert(x.message); }
                };
            });
            document.getElementById('osc-fechar').onclick = function () { card.style.display = 'none'; };
            document.getElementById('osc-add').onclick = async function () {
                var erro = document.getElementById('osc-erro'); erro.hidden = true;
                var body = {
                    chave: document.getElementById('osc-chave').value.trim(),
                    rotulo: document.getElementById('osc-rotulo').value.trim(),
                    tipo: document.getElementById('osc-tipo').value,
                    opcoes: document.getElementById('osc-opcoes').value.trim(),
                    obrigatorio: document.getElementById('osc-obrig').value === '1',
                    ativo: true
                };
                if (!body.chave) { erro.hidden = false; erro.textContent = 'Informe a chave técnica.'; return; }
                try { await S.api('/orcamento-spare/campos', { method: 'POST', body: body }); abrirCampos(); carregar(); }
                catch (x) { erro.hidden = false; erro.textContent = x.message; }
            };
            card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

        // ── Eventos ───────────────────────────────────────────────────
        if (podeCriar) document.getElementById('os-novo').onclick = function () { abrirForm(); };
        if (podeAdmin) document.getElementById('os-campos').onclick = abrirCampos;
        document.getElementById('os-busca').addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter') tabela();
        });
        carregar();
    }
};
