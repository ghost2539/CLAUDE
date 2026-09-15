/* ================================================================
   Módulo: CAPEX Spare — Controle de Orçamento do SPARE
   Projetos (nº EBS, descrição, serviço, categoria) com LINHAS DE ITEM
   (Item EBS, descrição, qtd, valor unit., valor total = qtd×unit).
   Total aprovado puxado do EBS pelo nº do projeto; a parcela destinada
   ao Spare é informada à mão. Dashboard: custo do projeto × Spare.
   Banco próprio (/api/orcamento-spare).
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.orcamento_spare = {

    async render(container) {
        var S = window.SPARE, e = S.esc;
        var u = S.user() || {};
        var pm = (u.permission_map || {}).orcamento_spare || {};
        var podeCriar = !!(u.is_admin || pm.can_create);
        var podeEditar = !!(u.is_admin || pm.can_edit);

        function money(v) {
            v = Number(v || 0);
            try { return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }); }
            catch (x) { return 'R$ ' + v.toFixed(2); }
        }
        function num(v) { return Number(v || 0).toLocaleString('pt-BR'); }

        container.innerHTML =
            '<h1 class="page-title">CAPEX Spare</h1>' +
            '<div id="os-resumo" class="filter-grid" style="margin-bottom:14px"></div>' +
            '<div class="card mb-3"><div class="card-header">Custo do projeto × orçamento destinado ao Spare</div>' +
                '<div class="card-body"><div id="os-chart"></div></div></div>' +
            '<div class="card" id="os-form-card" style="display:none">' +
                '<div class="card-header" id="os-form-titulo">Novo projeto</div>' +
                '<div class="card-body"><div id="os-form"></div></div>' +
            '</div>' +
            '<div class="card"><div class="card-header" ' +
                'style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">' +
                '<span>Projetos</span><span style="display:flex;gap:8px">' +
                (podeEditar ? '<button id="os-ebs" class="btn btn-secondary btn-sm">Atualizar (EBS)</button>' : '') +
                (podeCriar ? '<button id="os-novo" class="btn btn-primary btn-sm">Novo projeto</button>' : '') +
                '</span></div><div class="card-body">' +
                '<div class="form-group" style="max-width:360px">' +
                    '<label for="os-busca">Buscar (nº, descrição, serviço, categoria)</label>' +
                    '<input id="os-busca" class="form-control" placeholder="digite e Enter"></div>' +
                '<div id="os-msg" class="text-muted mt-2"></div>' +
                '<div id="os-lista" class="mt-3"></div>' +
            '</div></div>';

        var elLista = document.getElementById('os-lista');
        var DADOS = { projetos: [], totais: {} };

        async function carregar() {
            elLista.innerHTML = '<p class="text-muted">Carregando…</p>';
            try { DADOS = await S.api('/orcamento-spare/projetos'); }
            catch (x) { elLista.innerHTML = '<p class="alert alert-danger">' + e(x.message) + '</p>'; return; }
            resumo(DADOS.totais || {});
            grafico(DADOS.projetos || []);
            tabela();
        }

        function resumo(t) {
            function card(rot, val, cor) {
                return '<div class="card" style="padding:10px 14px">' +
                    '<div class="text-muted" style="font-size:.8em">' + e(rot) + '</div>' +
                    '<div style="font-weight:600;font-size:1.05em' + (cor ? ';color:' + cor : '') + '">' + val + '</div></div>';
            }
            var saldo = (t.aprovado_spare || 0) - (t.custo_total || 0);
            document.getElementById('os-resumo').innerHTML =
                card('Projetos', e(t.projetos || 0)) +
                card('Aprovado (EBS)', money(t.aprovado_ebs)) +
                card('Destinado ao Spare', money(t.aprovado_spare)) +
                card('Custo total', money(t.custo_total)) +
                card('Saldo Spare', money(saldo), saldo < 0 ? '#dc2626' : '#16a34a');
        }

        // ── Gráfico: custo × destinado ao Spare, por projeto (barras) ──
        function grafico(projetos) {
            var box = document.getElementById('os-chart');
            var comDado = projetos.filter(function (p) { return (p.custo_total || 0) > 0 || (p.aprovado_spare || 0) > 0; });
            if (!comDado.length) { box.innerHTML = '<p class="text-muted" style="margin:0">Sem dados para o gráfico ainda.</p>'; return; }
            var max = Math.max.apply(null, comDado.map(function (p) {
                return Math.max(p.custo_total || 0, p.aprovado_spare || 0);
            })) || 1;
            function barra(rot, val, cor) {
                var w = Math.max(0, Math.min(100, (val / max) * 100));
                return '<div style="display:flex;align-items:center;gap:8px;margin:2px 0">' +
                    '<span style="width:120px;font-size:.72em;color:#6b7280">' + rot + '</span>' +
                    '<div style="flex:1;background:#f1f5f9;border-radius:4px;height:16px;overflow:hidden">' +
                        '<div style="height:100%;width:' + w + '%;background:' + cor + '"></div></div>' +
                    '<span style="width:120px;text-align:right;font-size:.75em;font-variant-numeric:tabular-nums">' + money(val) + '</span>' +
                    '</div>';
            }
            var linhas = comDado.map(function (p) {
                var estouro = (p.custo_total || 0) > (p.aprovado_spare || 0);
                return '<div style="margin-bottom:12px">' +
                    '<div style="font-size:.82em;font-weight:600;margin-bottom:2px">' +
                        e(p.numero || '(sem nº)') + (p.descricao ? ' · ' + e(p.descricao) : '') + '</div>' +
                    barra('Destinado Spare', p.aprovado_spare || 0, '#2563eb') +
                    barra('Custo', p.custo_total || 0, estouro ? '#dc2626' : '#16a34a') +
                    '</div>';
            }).join('');
            box.innerHTML =
                '<div style="display:flex;gap:16px;font-size:.75em;color:#6b7280;margin-bottom:8px">' +
                    '<span><span style="display:inline-block;width:10px;height:10px;background:#2563eb;border-radius:2px;margin-right:4px"></span>Destinado ao Spare</span>' +
                    '<span><span style="display:inline-block;width:10px;height:10px;background:#16a34a;border-radius:2px;margin-right:4px"></span>Custo (dentro do orçado)</span>' +
                    '<span><span style="display:inline-block;width:10px;height:10px;background:#dc2626;border-radius:2px;margin-right:4px"></span>Custo (acima do orçado)</span>' +
                '</div>' + linhas;
        }

        function tabela() {
            var termo = (document.getElementById('os-busca').value || '').trim().toLowerCase();
            var linhas = DADOS.projetos.filter(function (p) {
                return !termo || [p.numero, p.descricao, p.servico, p.categoria].some(function (c) {
                    return String(c || '').toLowerCase().indexOf(termo) !== -1;
                });
            });
            if (!linhas.length) { elLista.innerHTML = '<p class="text-muted">Nenhum projeto.</p>'; return; }
            var corpo = linhas.map(function (p) {
                var acoes = podeEditar
                    ? '<button class="btn btn-secondary btn-sm os-ed" data-id="' + p.id + '">Editar</button> ' +
                      '<button class="btn btn-secondary btn-sm os-rm" data-id="' + p.id + '">Excluir</button>'
                    : '';
                return '<tr>' +
                    '<td><b>' + e(p.numero) + '</b></td>' +
                    '<td>' + e(p.descricao) + '</td>' +
                    '<td>' + e(p.servico) + '</td>' +
                    '<td>' + e(p.categoria) + '</td>' +
                    '<td class="text-center">' + e((p.itens || []).length) + '</td>' +
                    '<td class="text-right">' + money(p.aprovado_ebs) + '</td>' +
                    '<td class="text-right">' + money(p.aprovado_spare) + '</td>' +
                    '<td class="text-right">' + money(p.custo_total) + '</td>' +
                    '<td class="text-right" style="color:' + (p.saldo_spare < 0 ? '#dc2626' : '#16a34a') + '">' + money(p.saldo_spare) + '</td>' +
                    '<td style="white-space:nowrap">' + acoes + '</td>' +
                    '</tr>';
            }).join('');
            elLista.innerHTML =
                '<div class="table-responsive"><table class="table table-sm">' +
                '<thead><tr><th>Nº projeto</th><th>Descrição</th><th>Serviço</th><th>Categoria</th>' +
                '<th class="text-center">Itens</th><th class="text-right">Aprovado (EBS)</th>' +
                '<th class="text-right">Destinado Spare</th><th class="text-right">Custo total</th>' +
                '<th class="text-right">Saldo</th><th></th></tr></thead><tbody>' + corpo + '</tbody></table></div>';
            Array.prototype.forEach.call(elLista.querySelectorAll('.os-ed'), function (b) {
                b.onclick = function () { abrirForm(DADOS.projetos.filter(function (p) { return p.id == b.dataset.id; })[0]); };
            });
            Array.prototype.forEach.call(elLista.querySelectorAll('.os-rm'), function (b) {
                b.onclick = function () { excluir(parseInt(b.dataset.id, 10)); };
            });
        }

        function campo(rot, ctrl) {
            return '<div class="form-group" style="margin:0">' +
                (rot ? '<label>' + e(rot) + '</label>' : '') + ctrl + '</div>';
        }
        function secao(titulo) {
            return '<div style="font-size:.78em;font-weight:700;text-transform:uppercase;' +
                'letter-spacing:.04em;color:#6b7280;margin:20px 0 10px;border-bottom:1px solid #e5e7eb;' +
                'padding-bottom:5px">' + e(titulo) + '</div>';
        }
        function grade(cols, html) {
            return '<div style="display:grid;grid-template-columns:' + cols +
                ';gap:14px;align-items:end">' + html + '</div>';
        }

        // ── Formulário ────────────────────────────────────────────────
        function abrirForm(d) {
            var edit = !!(d && d.id);
            d = d || {};
            document.getElementById('os-form-titulo').textContent =
                edit ? ('Editar projeto ' + (d.numero || ('#' + d.id))) : 'Novo projeto';
            document.getElementById('os-form-card').style.display = '';
            document.getElementById('os-form').innerHTML =
                secao('Dados do projeto') +
                grade('minmax(140px,1fr) minmax(220px,2fr)',
                    campo('Nº do projeto (EBS)', '<input id="os-numero" class="form-control" value="' + e(d.numero || '') + '">') +
                    campo('Descrição', '<input id="os-descricao" class="form-control" value="' + e(d.descricao || '') + '">')) +
                '<div style="height:14px"></div>' +
                grade('1fr 1fr',
                    campo('Serviço', '<input id="os-servico" class="form-control" value="' + e(d.servico || '') + '">') +
                    campo('Categoria', '<input id="os-categoria" class="form-control" value="' + e(d.categoria || '') + '">')) +
                secao('Orçamento') +
                '<div style="max-width:520px">' + grade('1fr 1fr',
                    campo('Aprovado (EBS)', '<input class="form-control" value="' + money(d.aprovado_ebs || 0) + '" disabled title="Puxado do EBS pelo Atualizar (EBS)">') +
                    campo('Destinado ao Spare', '<input id="os-aprovspare" type="number" step="0.01" min="0" class="form-control" value="' + e(d.aprovado_spare != null ? d.aprovado_spare : 0) + '">')) +
                '</div>' +
                secao('Itens') +
                '<div class="table-responsive"><table class="table table-sm" id="os-itens-tab">' +
                    '<thead><tr><th style="width:140px">Item EBS</th><th>Descrição do item</th>' +
                    '<th style="width:100px">Qtd</th><th style="width:130px">Valor unit.</th>' +
                    '<th style="width:90px">% Imposto</th>' +
                    '<th style="width:140px" class="text-right">Valor total (c/ imposto)</th>' +
                    (podeEditar ? '<th style="width:40px"></th>' : '') + '</tr></thead>' +
                    '<tbody id="os-itens"></tbody>' +
                    '<tfoot><tr><td colspan="5" class="text-right"><b>Custo total (c/ imposto)</b></td>' +
                        '<td class="text-right"><b id="os-custo">R$ 0,00</b></td>' + (podeEditar ? '<td></td>' : '') + '</tr></tfoot>' +
                '</table></div>' +
                (podeEditar ? '<div class="btn-row mt-2"><button type="button" id="os-add-item" class="btn btn-secondary btn-sm">+ Adicionar item</button></div>' : '') +
                secao('Observação') +
                campo('', '<textarea id="os-obs" class="form-control" rows="2" placeholder="Anotações do projeto (opcional)">' + e(d.observacao || '') + '</textarea>') +
                '<div class="btn-row mt-3" style="margin-top:18px">' +
                    (podeEditar ? '<button type="button" id="os-salvar" class="btn btn-primary btn-sm">' + (edit ? 'Salvar' : 'Cadastrar') + '</button> ' : '') +
                    '<button type="button" id="os-cancelar" class="btn btn-secondary btn-sm">Fechar</button>' +
                '</div><div id="os-erro" class="alert alert-danger mt-2" hidden></div>';

            var itens = (d.itens && d.itens.length) ? d.itens : [];
            itens.forEach(addItem);
            if (!itens.length && podeEditar) addItem();
            recalc();

            if (podeEditar) {
                document.getElementById('os-add-item').onclick = function () { addItem(); recalc(); };
                document.getElementById('os-salvar').onclick = function () { salvar(edit ? d.id : 0); };
            }
            document.getElementById('os-cancelar').onclick = fecharForm;
            document.getElementById('os-form-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

        function addItem(it) {
            it = it || {};
            var ro = podeEditar ? '' : ' readonly';
            var tr = document.createElement('tr');
            tr.className = 'os-item';
            tr.innerHTML =
                '<td><input class="form-control os-i-ebs" value="' + e(it.item_ebs || '') + '"' + ro + '></td>' +
                '<td><input class="form-control os-i-desc" value="' + e(it.descricao_item || '') + '"' + ro + '></td>' +
                '<td><input class="form-control os-i-qtd" type="number" step="0.001" min="0" value="' + e(it.quantidade != null ? it.quantidade : '') + '"' + ro + '></td>' +
                '<td><input class="form-control os-i-vu" type="number" step="0.01" min="0" value="' + e(it.valor_unitario != null ? it.valor_unitario : '') + '"' + ro + '></td>' +
                '<td><input class="form-control os-i-imp" type="number" step="0.01" min="0" value="' + e(it.imposto_percent != null ? it.imposto_percent : '') + '"' + ro + '></td>' +
                '<td class="text-right os-i-vt" style="font-variant-numeric:tabular-nums">R$ 0,00</td>' +
                (podeEditar ? '<td><button type="button" class="btn btn-secondary btn-sm os-i-rm" title="Remover">&times;</button></td>' : '');
            document.getElementById('os-itens').appendChild(tr);
            if (podeEditar) {
                tr.querySelector('.os-i-rm').onclick = function () { tr.remove(); recalc(); };
                tr.querySelector('.os-i-qtd').oninput = recalc;
                tr.querySelector('.os-i-vu').oninput = recalc;
                tr.querySelector('.os-i-imp').oninput = recalc;
            }
        }

        function recalc() {
            var total = 0;
            Array.prototype.forEach.call(document.querySelectorAll('.os-item'), function (tr) {
                var q = parseFloat(tr.querySelector('.os-i-qtd').value) || 0;
                var vu = parseFloat(tr.querySelector('.os-i-vu').value) || 0;
                var imp = parseFloat(tr.querySelector('.os-i-imp').value) || 0;
                var vt = q * vu * (1 + imp / 100);
                tr.querySelector('.os-i-vt').textContent = money(vt);
                total += vt;
            });
            var el = document.getElementById('os-custo');
            if (el) el.textContent = money(total);
        }

        function fecharForm() {
            document.getElementById('os-form-card').style.display = 'none';
            document.getElementById('os-form').innerHTML = '';
        }

        function coletar() {
            var itens = [];
            Array.prototype.forEach.call(document.querySelectorAll('.os-item'), function (tr) {
                var o = {
                    item_ebs: tr.querySelector('.os-i-ebs').value.trim(),
                    descricao_item: tr.querySelector('.os-i-desc').value.trim(),
                    quantidade: parseFloat(tr.querySelector('.os-i-qtd').value) || 0,
                    valor_unitario: parseFloat(tr.querySelector('.os-i-vu').value) || 0,
                    imposto_percent: parseFloat(tr.querySelector('.os-i-imp').value) || 0
                };
                if (o.item_ebs || o.descricao_item || o.quantidade || o.valor_unitario || o.imposto_percent) itens.push(o);
            });
            var av = document.getElementById('os-aprovspare').value;
            return {
                numero: document.getElementById('os-numero').value.trim(),
                descricao: document.getElementById('os-descricao').value.trim(),
                servico: document.getElementById('os-servico').value.trim(),
                categoria: document.getElementById('os-categoria').value.trim(),
                aprovado_spare: av === '' ? 0 : parseFloat(av),
                observacao: document.getElementById('os-obs').value.trim(),
                itens: itens
            };
        }

        async function salvar(id) {
            var erro = document.getElementById('os-erro'); erro.hidden = true;
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
            if (!confirm('Excluir este projeto e seus itens?')) return;
            try { await S.api('/orcamento-spare/projetos/' + id, { method: 'DELETE' }); carregar(); }
            catch (x) { alert(x.message); }
        }

        async function sincronizarEbs() {
            var msg = document.getElementById('os-msg');
            msg.textContent = 'Consultando o EBS…';
            try {
                var d = await S.api('/orcamento-spare/sincronizar', { method: 'POST', body: {} });
                msg.textContent = 'Aprovado atualizado do EBS: ' + (d.atualizados || 0) + ' projeto(s).' + (d.aviso ? ' ' + d.aviso : '');
                carregar();
            } catch (x) { msg.textContent = 'Falha ao atualizar do EBS: ' + x.message; }
        }

        // ── Eventos ───────────────────────────────────────────────────
        if (podeCriar) document.getElementById('os-novo').onclick = function () { abrirForm(); };
        if (podeEditar) document.getElementById('os-ebs').onclick = sincronizarEbs;
        document.getElementById('os-busca').addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter') tabela();
        });
        carregar();
    }
};
