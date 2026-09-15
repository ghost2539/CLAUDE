/* ================================================================
   Módulo: CAPEX Spare — Controle de Orçamento do SPARE
   Aba Projetos: projetos (nº EBS, descrição, serviço, categoria) com
   LINHAS DE ITEM (Item EBS, descrição, qtd, valor unit., % imposto,
   valor total = qtd×unit×(1+imposto/100)). Aprovado puxado do EBS pelo
   nº; parcela destinada ao Spare informada à mão. Gráfico custo × Spare.
   Aba Itens: todos os itens que consomem os projetos, por situação
   (orçado/previsto, em andamento, executado).
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

        var STATUS = [
            { valor: 'orcado', rotulo: 'Orçado/Previsto', cor: '#6b7280' },
            { valor: 'andamento', rotulo: 'Em andamento', cor: '#2563eb' },
            { valor: 'executado', rotulo: 'Executado', cor: '#16a34a' }
        ];
        function statusInfo(st) {
            for (var i = 0; i < STATUS.length; i++) if (STATUS[i].valor === st) return STATUS[i];
            return STATUS[0];
        }
        function statusBadge(st) {
            var s = statusInfo(st);
            return '<span style="display:inline-block;padding:2px 9px;border-radius:10px;font-size:.72em;' +
                'background:' + s.cor + '22;color:' + s.cor + ';border:1px solid ' + s.cor + '66">' + e(s.rotulo) + '</span>';
        }

        function money(v) {
            v = Number(v || 0);
            try { return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }); }
            catch (x) { return 'R$ ' + v.toFixed(2); }
        }

        container.innerHTML =
            '<h1 class="page-title">CAPEX Spare</h1>' +
            '<div id="os-abas" style="display:flex;gap:6px;border-bottom:1px solid #33415533;margin-bottom:16px">' +
                '<button class="btn btn-sm os-aba-btn" data-aba="projetos">Projetos</button>' +
                '<button class="btn btn-sm os-aba-btn" data-aba="itens">Itens (consumo)</button>' +
            '</div>' +
            // ── Aba Projetos ──
            '<div id="ab-projetos">' +
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
                '</div></div>' +
            '</div>' +
            // ── Aba Itens ──
            '<div id="ab-itens" style="display:none">' +
                '<div id="os-itens-resumo" class="filter-grid" style="margin-bottom:14px"></div>' +
                '<div class="card"><div class="card-header">Itens que consomem os projetos</div>' +
                    '<div class="card-body">' +
                        '<div style="display:flex;gap:12px;flex-wrap:wrap;align-items:end">' +
                            '<div class="form-group" style="margin:0;min-width:200px">' +
                                '<label for="os-it-status">Situação</label>' +
                                '<select id="os-it-status" class="form-control"><option value="">Todas</option>' +
                                STATUS.map(function (s) { return '<option value="' + s.valor + '">' + e(s.rotulo) + '</option>'; }).join('') +
                                '</select></div>' +
                            '<div class="form-group" style="margin:0;flex:1;min-width:240px">' +
                                '<label for="os-it-busca">Buscar (projeto, item EBS, descrição)</label>' +
                                '<input id="os-it-busca" class="form-control" placeholder="digite para filtrar"></div>' +
                        '</div>' +
                        '<div id="os-itens-lista" class="mt-3"></div>' +
                    '</div></div>' +
            '</div>';

        var elLista = document.getElementById('os-lista');
        var DADOS = { projetos: [], totais: {} };
        var ITENS = { itens: [], totais_status: {} };
        var itensCarregados = false;

        // ── Abas ──────────────────────────────────────────────────────
        function mostrarAba(nome) {
            document.getElementById('ab-projetos').style.display = nome === 'projetos' ? '' : 'none';
            document.getElementById('ab-itens').style.display = nome === 'itens' ? '' : 'none';
            Array.prototype.forEach.call(document.querySelectorAll('.os-aba-btn'), function (b) {
                var ativo = b.dataset.aba === nome;
                b.className = 'btn btn-sm os-aba-btn ' + (ativo ? 'btn-primary' : 'btn-secondary');
            });
            if (nome === 'itens') carregarItens();
        }
        Array.prototype.forEach.call(document.querySelectorAll('.os-aba-btn'), function (b) {
            b.onclick = function () { mostrarAba(b.dataset.aba); };
        });

        async function carregar() {
            elLista.innerHTML = '<p class="text-muted">Carregando…</p>';
            try { DADOS = await S.api('/orcamento-spare/projetos'); }
            catch (x) { elLista.innerHTML = '<p class="alert alert-danger">' + e(x.message) + '</p>'; return; }
            resumo(DADOS.totais || {});
            grafico(DADOS.projetos || []);
            tabela();
            itensCarregados = false;   // itens podem ter mudado
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

        var padT = 'padding:8px 14px';
        var padN = 'padding:8px 14px;white-space:nowrap;font-variant-numeric:tabular-nums';

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
                var nItens = (p.itens || []).length;
                return '<tr>' +
                    '<td style="' + padT + ';white-space:nowrap"><b>' + e(p.numero) + '</b></td>' +
                    '<td style="' + padT + '">' + e(p.descricao) + '</td>' +
                    '<td style="' + padT + '">' + e(p.servico) + '</td>' +
                    '<td style="' + padT + '">' + e(p.categoria) + '</td>' +
                    '<td class="text-center" style="' + padT + '">' +
                        (nItens ? '<a href="#" class="os-verit" data-num="' + e(p.numero) + '" title="Ver itens deste projeto">' + nItens + '</a>' : '0') +
                    '</td>' +
                    '<td class="text-right" style="' + padN + '">' + money(p.aprovado_ebs) + '</td>' +
                    '<td class="text-right" style="' + padN + '">' + money(p.aprovado_spare) + '</td>' +
                    '<td class="text-right" style="' + padN + '">' + money(p.custo_total) + '</td>' +
                    '<td class="text-right" style="' + padN + ';color:' + (p.saldo_spare < 0 ? '#dc2626' : '#16a34a') + '">' + money(p.saldo_spare) + '</td>' +
                    '<td style="' + padT + ';white-space:nowrap">' + acoes + '</td>' +
                    '</tr>';
            }).join('');
            elLista.innerHTML =
                '<div class="table-responsive"><table class="table table-sm" style="min-width:960px">' +
                '<thead><tr>' +
                '<th style="' + padT + '">Nº projeto</th>' +
                '<th style="' + padT + '">Descrição</th>' +
                '<th style="' + padT + '">Serviço</th>' +
                '<th style="' + padT + '">Categoria</th>' +
                '<th class="text-center" style="' + padT + '">Itens</th>' +
                '<th class="text-right" style="' + padN + '">Aprovado (EBS)</th>' +
                '<th class="text-right" style="' + padN + '">Destinado Spare</th>' +
                '<th class="text-right" style="' + padN + '">Custo total</th>' +
                '<th class="text-right" style="' + padN + '">Saldo</th>' +
                '<th style="' + padT + '"></th></tr></thead><tbody>' + corpo + '</tbody></table></div>';
            Array.prototype.forEach.call(elLista.querySelectorAll('.os-ed'), function (b) {
                b.onclick = function () { abrirForm(DADOS.projetos.filter(function (p) { return p.id == b.dataset.id; })[0]); };
            });
            Array.prototype.forEach.call(elLista.querySelectorAll('.os-rm'), function (b) {
                b.onclick = function () { excluir(parseInt(b.dataset.id, 10)); };
            });
            Array.prototype.forEach.call(elLista.querySelectorAll('.os-verit'), function (a) {
                a.onclick = function (ev) {
                    ev.preventDefault();
                    mostrarAba('itens');
                    var bi = document.getElementById('os-it-busca');
                    if (bi) { bi.value = a.dataset.num; }
                    var st = document.getElementById('os-it-status'); if (st) st.value = '';
                    tabelaItens();
                };
            });
        }

        // ── Aba Itens: consumo por situação ───────────────────────────
        async function carregarItens() {
            if (itensCarregados) { tabelaItens(); return; }
            var box = document.getElementById('os-itens-lista');
            box.innerHTML = '<p class="text-muted">Carregando…</p>';
            try { ITENS = await S.api('/orcamento-spare/itens'); }
            catch (x) { box.innerHTML = '<p class="alert alert-danger">' + e(x.message) + '</p>'; return; }
            itensCarregados = true;
            resumoItens();
            tabelaItens();
        }

        function resumoItens() {
            var ts = ITENS.totais_status || {};
            function card(rot, val, sub, cor) {
                return '<div class="card" style="padding:10px 14px' + (cor ? ';border-left:3px solid ' + cor : '') + '">' +
                    '<div class="text-muted" style="font-size:.8em">' + e(rot) + '</div>' +
                    '<div style="font-weight:600;font-size:1.05em' + (cor ? ';color:' + cor : '') + '">' + val + '</div>' +
                    '<div class="text-muted" style="font-size:.72em">' + e(sub) + '</div></div>';
            }
            var html = STATUS.map(function (s) {
                var d = ts[s.valor] || { itens: 0, valor: 0 };
                return card(s.rotulo, money(d.valor), (d.itens || 0) + ' item(ns)', s.cor);
            }).join('');
            var totalV = STATUS.reduce(function (a, s) { return a + ((ts[s.valor] || {}).valor || 0); }, 0);
            html += card('Consumo total', money(totalV), (ITENS.itens || []).length + ' item(ns)');
            document.getElementById('os-itens-resumo').innerHTML = html;
        }

        function tabelaItens() {
            var box = document.getElementById('os-itens-lista');
            var fSt = (document.getElementById('os-it-status').value || '');
            var termo = (document.getElementById('os-it-busca').value || '').trim().toLowerCase();
            var linhas = (ITENS.itens || []).filter(function (it) {
                if (fSt && it.status !== fSt) return false;
                if (!termo) return true;
                return [it.projeto_numero, it.projeto_descricao, it.item_ebs, it.descricao_item].some(function (c) {
                    return String(c || '').toLowerCase().indexOf(termo) !== -1;
                });
            });
            if (!linhas.length) { box.innerHTML = '<p class="text-muted">Nenhum item para este filtro.</p>'; return; }
            var totalFiltro = linhas.reduce(function (a, it) { return a + (it.valor_total || 0); }, 0);
            var corpo = linhas.map(function (it) {
                return '<tr>' +
                    '<td style="' + padT + ';white-space:nowrap"><b>' + e(it.projeto_numero) + '</b>' +
                        (it.projeto_descricao ? '<div class="text-muted" style="font-size:.75em">' + e(it.projeto_descricao) + '</div>' : '') + '</td>' +
                    '<td style="' + padT + ';white-space:nowrap">' + e(it.item_ebs) + '</td>' +
                    '<td style="' + padT + '">' + e(it.descricao_item) + '</td>' +
                    '<td class="text-right" style="' + padN + '">' + (Number(it.quantidade || 0).toLocaleString('pt-BR')) + '</td>' +
                    '<td class="text-right" style="' + padN + '">' + money(it.valor_unitario) + '</td>' +
                    '<td class="text-right" style="' + padN + '">' + (Number(it.imposto_percent || 0)).toLocaleString('pt-BR') + '%</td>' +
                    '<td class="text-right" style="' + padN + '">' + money(it.valor_total) + '</td>' +
                    '<td style="' + padT + ';white-space:nowrap">' + statusBadge(it.status) + '</td>' +
                    '</tr>';
            }).join('');
            box.innerHTML =
                '<div class="table-responsive"><table class="table table-sm" style="min-width:900px">' +
                '<thead><tr>' +
                '<th style="' + padT + '">Projeto</th>' +
                '<th style="' + padT + '">Item EBS</th>' +
                '<th style="' + padT + '">Descrição do item</th>' +
                '<th class="text-right" style="' + padN + '">Qtd</th>' +
                '<th class="text-right" style="' + padN + '">Valor unit.</th>' +
                '<th class="text-right" style="' + padN + '">% Imp.</th>' +
                '<th class="text-right" style="' + padN + '">Valor total (c/ imp.)</th>' +
                '<th style="' + padT + '">Situação</th></tr></thead><tbody>' + corpo + '</tbody>' +
                '<tfoot><tr><td colspan="6" class="text-right" style="' + padT + '"><b>Total filtrado</b></td>' +
                    '<td class="text-right" style="' + padN + '"><b>' + money(totalFiltro) + '</b></td><td></td></tr></tfoot>' +
                '</table></div>';
        }

        // ── Formulário ────────────────────────────────────────────────
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
                    '<thead><tr><th style="width:130px">Item EBS</th><th>Descrição do item</th>' +
                    '<th style="width:90px">Qtd</th><th style="width:120px">Valor unit.</th>' +
                    '<th style="width:80px">% Imposto</th>' +
                    '<th style="width:170px">Acordo de compras</th>' +
                    '<th style="width:150px">Situação</th>' +
                    '<th style="width:140px" class="text-right">Valor total (c/ imposto)</th>' +
                    (podeEditar ? '<th style="width:40px"></th>' : '') + '</tr></thead>' +
                    '<tbody id="os-itens"></tbody>' +
                    '<tfoot><tr><td colspan="7" class="text-right"><b>Custo total (c/ imposto)</b></td>' +
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
            var dis = podeEditar ? '' : ' disabled';
            var stAtual = it.status || 'orcado';
            var opts = STATUS.map(function (s) {
                return '<option value="' + s.valor + '"' + (s.valor === stAtual ? ' selected' : '') + '>' + e(s.rotulo) + '</option>';
            }).join('');
            var tr = document.createElement('tr');
            tr.className = 'os-item';
            tr.innerHTML =
                '<td><input class="form-control os-i-ebs" value="' + e(it.item_ebs || '') + '"' + ro + '></td>' +
                '<td><input class="form-control os-i-desc" value="' + e(it.descricao_item || '') + '"' + ro + '></td>' +
                '<td><input class="form-control os-i-qtd" type="number" step="0.001" min="0" value="' + e(it.quantidade != null ? it.quantidade : '') + '"' + ro + '></td>' +
                '<td><input class="form-control os-i-vu" type="number" step="0.01" min="0" value="' + e(it.valor_unitario != null ? it.valor_unitario : '') + '"' + ro + '></td>' +
                '<td><input class="form-control os-i-imp" type="number" step="0.01" min="0" value="' + e(it.imposto_percent != null ? it.imposto_percent : '') + '"' + ro + '></td>' +
                '<td style="white-space:nowrap">' +
                    '<label style="display:flex;align-items:center;gap:5px;font-size:.8em;cursor:pointer">' +
                        '<input type="checkbox" class="os-i-acordo"' + (it.acordo ? ' checked' : '') + dis + '> É de acordo</label>' +
                    '<input class="form-control os-i-acnum" placeholder="Nº do acordo" value="' + e(it.acordo_numero || '') + '"' +
                        ' style="margin-top:4px;' + (it.acordo ? '' : 'display:none') + '"' + ro + '></td>' +
                '<td><select class="form-control os-i-st"' + dis + '>' + opts + '</select></td>' +
                '<td class="text-right os-i-vt" style="font-variant-numeric:tabular-nums">R$ 0,00</td>' +
                (podeEditar ? '<td><button type="button" class="btn btn-secondary btn-sm os-i-rm" title="Remover">&times;</button></td>' : '');
            document.getElementById('os-itens').appendChild(tr);
            if (podeEditar) {
                tr.querySelector('.os-i-rm').onclick = function () { tr.remove(); recalc(); };
                tr.querySelector('.os-i-qtd').oninput = recalc;
                tr.querySelector('.os-i-vu').oninput = recalc;
                tr.querySelector('.os-i-imp').oninput = recalc;
                var chk = tr.querySelector('.os-i-acordo');
                var acn = tr.querySelector('.os-i-acnum');
                chk.onchange = function () {
                    acn.style.display = chk.checked ? '' : 'none';
                    if (chk.checked) puxarAcordo(tr);
                };
                acn.onchange = function () { puxarAcordo(tr); };
                tr.querySelector('.os-i-ebs').addEventListener('change', function () { puxarAcordo(tr); });
            }
        }

        // Reaproveita descrição/valor/imposto de um item de acordo já cadastrado.
        async function puxarAcordo(tr) {
            var chk = tr.querySelector('.os-i-acordo');
            if (!chk || !chk.checked) return;
            var ebs = tr.querySelector('.os-i-ebs').value.trim();
            var acn = tr.querySelector('.os-i-acnum').value.trim();
            if (!ebs && !acn) return;
            try {
                var q = '/orcamento-spare/item-acordo?item_ebs=' + encodeURIComponent(ebs) +
                    (acn ? '&acordo=' + encodeURIComponent(acn) : '');
                var d = await S.api(q);
                if (!d || !(d.descricao_item || d.valor_unitario || d.imposto_percent || d.acordo_numero)) return;
                if (d.descricao_item) tr.querySelector('.os-i-desc').value = d.descricao_item;
                if (d.valor_unitario != null && d.valor_unitario !== '') tr.querySelector('.os-i-vu').value = d.valor_unitario;
                if (d.imposto_percent != null && d.imposto_percent !== '') tr.querySelector('.os-i-imp').value = d.imposto_percent;
                if (!acn && d.acordo_numero) tr.querySelector('.os-i-acnum').value = d.acordo_numero;
                recalc();
                if (S.toast) S.toast('Dados do acordo aplicados ao item.');
            } catch (x) { /* silencioso: sem correspondência, segue manual */ }
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
                    imposto_percent: parseFloat(tr.querySelector('.os-i-imp').value) || 0,
                    status: tr.querySelector('.os-i-st').value || 'orcado',
                    acordo: tr.querySelector('.os-i-acordo').checked,
                    acordo_numero: tr.querySelector('.os-i-acnum').value.trim()
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
        document.getElementById('os-it-status').onchange = tabelaItens;
        document.getElementById('os-it-busca').addEventListener('input', tabelaItens);

        mostrarAba('projetos');
        carregar();
    }
};
