/* ================================================================
   Módulo: CAPEX Spare — Controle de Orçamento do SPARE
   Aba Projetos: projetos com LINHAS DE ITEM (Item EBS, descrição, NCM,
   qtd, valor unit., % imposto do NCM/TIPI; total = qtd×unit×(1+imposto/100)).
   Aba Itens: Cadastro de itens (Item EBS, descrição, acordo, NCM→alíquota
   da TIPI, preço de acordo) e Consumo por situação (orçado/andamento/exec).
   Regra: item sem acordo exige preço ao lançar; com acordo, puxa do cadastro.
   Banco próprio (/api/capex-spare).
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.capex_spare = {

    async render(container) {
        var S = window.SPARE, e = S.esc;
        var u = S.user() || {};
        var pm = (u.permission_map || {}).orcamento_spare || {};
        var podeCriar = !!(u.is_admin || pm.can_create);
        var podeEditar = !!(u.is_admin || pm.can_edit);

        var STATUS = [
            { valor: 'orcado', rotulo: 'Orçado/Previsto', cor: 'var(--sp-faint)' },
            { valor: 'andamento', rotulo: 'Em andamento', cor: 'var(--sp-teal-text)' },
            { valor: 'executado', rotulo: 'Executado', cor: 'var(--sp-ok)' }
        ];
        var ENTREGA = [
            { valor: 'pendente', rotulo: 'Pendente entrega' },
            { valor: 'agendado', rotulo: 'Agendado' },
            { valor: 'entregue', rotulo: 'Entregue' }
        ];
        var BUS = ['Renner', 'Camicado', 'Youcom', 'Renner Argentina', 'Renner Uruguai'];
        var FASES = ['1º semestre', '2º semestre'];
        function opcoesBU(sel) {
            return '<option value="">— selecione —</option>' + BUS.map(function (b) {
                return '<option value="' + e(b) + '"' + (b === sel ? ' selected' : '') + '>' + e(b) + '</option>';
            }).join('');
        }
        function statusInfo(st) {
            for (var i = 0; i < STATUS.length; i++) if (STATUS[i].valor === st) return STATUS[i];
            return STATUS[0];
        }
        function statusBadge(st) {
            var s = statusInfo(st);
            return '<span style="display:inline-block;padding:2px 9px;border-radius:0;font-size:.72em;' +
                'background:' + s.cor + '22;color:' + s.cor + ';border:1px solid ' + s.cor + '66">' + e(s.rotulo) + '</span>';
        }
        function money(v) {
            v = Number(v || 0);
            try { return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }); }
            catch (x) { return 'R$ ' + v.toFixed(2); }
        }
        function dataBR(iso) {
            var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
            return m ? (m[3] + '/' + m[2] + '/' + m[1]) : (iso || '');
        }
        function impostoTxt(it) {
            if (it.nt) return 'NT';
            var a = (it.imposto_percent != null ? it.imposto_percent : it.aliquota);
            return (Number(a || 0)).toLocaleString('pt-BR') + '%';
        }

        var padT = 'padding:8px 14px';
        var padN = 'padding:8px 14px;white-space:nowrap;font-variant-numeric:tabular-nums';

        container.innerHTML =
            '<h1 class="page-title">CAPEX Spare</h1>' +
            '<div id="os-abas" style="display:flex;gap:6px;border-bottom:1px solid #33415533;margin-bottom:16px">' +
                '<button class="btn btn-sm os-aba-btn" data-aba="projetos">Projetos</button>' +
                '<button class="btn btn-sm os-aba-btn" data-aba="itens">Itens</button>' +
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
                    (podeCriar ? '<button id="os-novo" class="btn btn-primary btn-sm">Novo projeto</button>' : '') +
                    '</span></div><div class="card-body">' +
                    '<div class="form-group" style="max-width:360px">' +
                        '<label for="os-busca">Buscar (nº, descrição, serviço, categoria)</label>' +
                        '<input id="os-busca" class="form-control" placeholder="digite e Enter"></div>' +
                    '<div id="os-msg" class="text-muted mt-2"></div>' +
                    '<div id="os-lista" class="mt-3"></div>' +
                '</div></div>' +
            '</div>' +
            // ── Aba Itens (sub-abas Cadastro / Consumo) ──
            '<div id="ab-itens" style="display:none">' +
                '<div style="display:flex;gap:6px;margin-bottom:14px">' +
                    '<button class="btn btn-sm os-sub-btn" data-sub="cadastro">Cadastro de itens</button>' +
                    '<button class="btn btn-sm os-sub-btn" data-sub="consumo">Consumo (por situação)</button>' +
                '</div>' +
                // Sub-aba Cadastro
                '<div id="sub-cadastro">' +
                    '<div class="card" id="cat-form-card" style="display:none">' +
                        '<div class="card-header" id="cat-form-titulo">Novo item</div>' +
                        '<div class="card-body"><div id="cat-form"></div></div></div>' +
                    '<div class="card"><div class="card-header" ' +
                        'style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">' +
                        '<span>Cadastro de itens</span>' +
                        '<span style="display:flex;gap:8px">' +
                        (podeCriar ? '<button id="cat-import" class="btn btn-secondary btn-sm">Importar acordos (Excel)</button>' : '') +
                        (podeCriar ? '<button id="cat-novo" class="btn btn-primary btn-sm">Novo item</button>' : '') +
                        '<input type="file" id="cat-file" accept=".xlsx,.xls" style="display:none">' +
                        '</span>' +
                        '</div><div class="card-body">' +
                        '<div id="cat-import-msg" class="text-muted mb-2"></div>' +
                        '<div class="form-group" style="max-width:360px"><label for="cat-busca">Buscar (Item EBS, descrição, NCM)</label>' +
                            '<input id="cat-busca" class="form-control" placeholder="digite para filtrar"></div>' +
                        '<div id="cat-lista" class="mt-3"></div>' +
                    '</div></div>' +
                '</div>' +
                // Sub-aba Consumo
                '<div id="sub-consumo" style="display:none">' +
                    '<div id="os-itens-resumo" class="filter-grid" style="margin-bottom:14px"></div>' +
                    '<div class="card"><div class="card-header">Itens que consomem os projetos</div>' +
                        '<div class="card-body">' +
                            '<div style="display:flex;gap:12px;flex-wrap:wrap;align-items:end">' +
                                '<div class="form-group" style="margin:0;min-width:200px"><label for="os-it-status">Situação</label>' +
                                    '<select id="os-it-status" class="form-control"><option value="">Todas</option>' +
                                    STATUS.map(function (s) { return '<option value="' + s.valor + '">' + e(s.rotulo) + '</option>'; }).join('') +
                                    '</select></div>' +
                                '<div class="form-group" style="margin:0;flex:1;min-width:240px"><label for="os-it-busca">Buscar (projeto, item EBS, descrição)</label>' +
                                    '<input id="os-it-busca" class="form-control" placeholder="digite para filtrar"></div>' +
                            '</div>' +
                            '<div id="os-itens-lista" class="mt-3"></div>' +
                        '</div></div>' +
                '</div>' +
            '</div>';

        var elLista = document.getElementById('os-lista');
        var DADOS = { projetos: [], totais: {} };
        var ITENS = { itens: [], totais_status: {} };
        var CAT = { itens: [] };
        var consumoCarregado = false, catCarregado = false;

        // ── Abas / sub-abas ───────────────────────────────────────────
        function mostrarAba(nome) {
            document.getElementById('ab-projetos').style.display = nome === 'projetos' ? '' : 'none';
            document.getElementById('ab-itens').style.display = nome === 'itens' ? '' : 'none';
            Array.prototype.forEach.call(document.querySelectorAll('.os-aba-btn'), function (b) {
                b.className = 'btn btn-sm os-aba-btn ' + (b.dataset.aba === nome ? 'btn-primary' : 'btn-secondary');
            });
            if (nome === 'itens') mostrarSub(subAtual);
        }
        var subAtual = 'cadastro';
        function mostrarSub(nome) {
            subAtual = nome;
            document.getElementById('sub-cadastro').style.display = nome === 'cadastro' ? '' : 'none';
            document.getElementById('sub-consumo').style.display = nome === 'consumo' ? '' : 'none';
            Array.prototype.forEach.call(document.querySelectorAll('.os-sub-btn'), function (b) {
                b.className = 'btn btn-sm os-sub-btn ' + (b.dataset.sub === nome ? 'btn-primary' : 'btn-secondary');
            });
            if (nome === 'cadastro') carregarCatalogo();
            else carregarConsumo();
        }
        Array.prototype.forEach.call(document.querySelectorAll('.os-aba-btn'), function (b) {
            b.onclick = function () { mostrarAba(b.dataset.aba); };
        });
        Array.prototype.forEach.call(document.querySelectorAll('.os-sub-btn'), function (b) {
            b.onclick = function () { mostrarSub(b.dataset.sub); };
        });

        // ── Projetos ──────────────────────────────────────────────────
        async function carregar() {
            elLista.innerHTML = '<p class="text-muted">Carregando…</p>';
            try { DADOS = await S.api('/capex-spare/projetos'); }
            catch (x) { elLista.innerHTML = '<p class="alert alert-danger">' + e(x.message) + '</p>'; return; }
            resumo(DADOS.totais || {});
            grafico(DADOS.projetos || []);
            tabela();
            consumoCarregado = false;
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
                card('Destinado ao Spare', money(t.aprovado_spare)) +
                card('Custo total (linhas)', money(t.custo_total)) +
                card('Saldo Spare', money(saldo), saldo < 0 ? 'var(--sp-alerta)' : 'var(--sp-ok)');
        }

        function grafico(projetos) {
            var box = document.getElementById('os-chart');
            var comDado = projetos.filter(function (p) { return (p.custo_total || 0) > 0 || (p.aprovado_spare || 0) > 0; });
            if (!comDado.length) { box.innerHTML = '<p class="text-muted" style="margin:0">Sem dados para o gráfico ainda.</p>'; return; }
            var max = Math.max.apply(null, comDado.map(function (p) { return Math.max(p.custo_total || 0, p.aprovado_spare || 0); })) || 1;
            function barra(rot, val, cor) {
                var w = Math.max(0, Math.min(100, (val / max) * 100));
                return '<div style="display:flex;align-items:center;gap:8px;margin:2px 0">' +
                    '<span style="width:120px;font-size:.72em;color:var(--sp-faint)">' + rot + '</span>' +
                    '<div style="flex:1;background:var(--sp-border-soft);border-radius:0;height:16px;overflow:hidden">' +
                        '<div style="height:100%;width:' + w + '%;background:' + cor + '"></div></div>' +
                    '<span style="width:120px;text-align:right;font-size:.75em;font-variant-numeric:tabular-nums">' + money(val) + '</span></div>';
            }
            var linhas = comDado.map(function (p) {
                var estouro = (p.custo_total || 0) > (p.aprovado_spare || 0);
                return '<div style="margin-bottom:12px">' +
                    '<div style="font-size:.82em;font-weight:600;margin-bottom:2px">' +
                        e(p.numero || '(sem nº)') + (p.descricao ? ' · ' + e(p.descricao) : '') + '</div>' +
                    barra('Destinado Spare', p.aprovado_spare || 0, 'var(--sp-teal-text)') +
                    barra('Custo', p.custo_total || 0, estouro ? 'var(--sp-alerta)' : 'var(--sp-ok)') + '</div>';
            }).join('');
            box.innerHTML =
                '<div style="display:flex;gap:16px;font-size:.75em;color:var(--sp-faint);margin-bottom:8px">' +
                    '<span><span style="display:inline-block;width:10px;height:10px;background:var(--sp-teal-text);border-radius:0;margin-right:4px"></span>Destinado ao Spare</span>' +
                    '<span><span style="display:inline-block;width:10px;height:10px;background:var(--sp-ok);border-radius:0;margin-right:4px"></span>Custo (dentro do orçado)</span>' +
                    '<span><span style="display:inline-block;width:10px;height:10px;background:var(--sp-alerta);border-radius:0;margin-right:4px"></span>Custo (acima do orçado)</span>' +
                '</div>' + linhas;
        }

        function tabela() {
            var termo = (document.getElementById('os-busca').value || '').trim().toLowerCase();
            var linhas = DADOS.projetos.filter(function (p) {
                return !termo || [p.numero, p.descricao, p.bu, p.categoria].some(function (c) {
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
                    '<td style="' + padT + ';white-space:nowrap">' + e(p.bu || '') + '</td>' +
                    '<td style="' + padT + '">' + e(p.categoria) + '</td>' +
                    '<td class="text-center" style="' + padT + '">' +
                        (nItens ? '<a href="#" class="os-verit" data-num="' + e(p.numero) + '" title="Ver itens deste projeto">' + nItens + '</a>' : '0') + '</td>' +
                    '<td class="text-right" style="' + padN + '">' + money(p.aprovado_spare) + '</td>' +
                    '<td class="text-right" style="' + padN + '">' + money(p.custo_total) + '</td>' +
                    '<td class="text-right" style="' + padN + ';color:' + (p.saldo_spare < 0 ? 'var(--sp-alerta)' : 'var(--sp-ok)') + '">' + money(p.saldo_spare) + '</td>' +
                    '<td style="' + padT + ';white-space:nowrap">' + acoes + '</td></tr>';
            }).join('');
            elLista.innerHTML =
                '<div class="table-responsive"><table class="table table-sm" style="min-width:960px">' +
                '<thead><tr>' +
                '<th style="' + padT + '">Nº projeto</th><th style="' + padT + '">Descrição</th>' +
                '<th style="' + padT + '">BU</th>' +
                '<th style="' + padT + '">Categoria</th>' +
                '<th class="text-center" style="' + padT + '">Itens</th>' +
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
                    ev.preventDefault(); mostrarAba('itens'); mostrarSub('consumo');
                    var bi = document.getElementById('os-it-busca'); if (bi) bi.value = a.dataset.num;
                    var st = document.getElementById('os-it-status'); if (st) st.value = '';
                    tabelaItens();
                };
            });
        }

        // ── Consumo (por situação) ────────────────────────────────────
        async function carregarConsumo() {
            if (consumoCarregado) { tabelaItens(); return; }
            var box = document.getElementById('os-itens-lista');
            box.innerHTML = '<p class="text-muted">Carregando…</p>';
            try { ITENS = await S.api('/capex-spare/itens'); }
            catch (x) { box.innerHTML = '<p class="alert alert-danger">' + e(x.message) + '</p>'; return; }
            consumoCarregado = true;
            resumoItens(); tabelaItens();
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
                if (fSt) {
                    if (fSt === 'executado' && !(it.valor_executado > 0)) return false;
                    if (fSt === 'andamento' && !(it.tem_po && it.valor_saldo > 0)) return false;
                    if (fSt === 'orcado' && it.tem_po) return false;
                }
                if (!termo) return true;
                return [it.projeto_numero, it.projeto_descricao, it.item_ebs, it.descricao_item, it.fase].some(function (c) {
                    return String(c || '').toLowerCase().indexOf(termo) !== -1;
                });
            });
            if (!linhas.length) { box.innerHTML = '<p class="text-muted">Nenhum item para este filtro.</p>'; return; }
            var totExec = linhas.reduce(function (a, it) { return a + (it.valor_executado || 0); }, 0);
            var totSaldo = linhas.reduce(function (a, it) { return a + (it.tem_po ? (it.valor_saldo || 0) : 0); }, 0);
            var totOrc = linhas.reduce(function (a, it) { return a + (!it.tem_po ? (it.valor_total || 0) : 0); }, 0);
            function badge(it) {
                var m = { orcado: ['Orçado', 'var(--sp-faint)'], andamento: ['Em andamento', 'var(--sp-teal-text)'],
                    parcial: ['Entrega parcial', 'var(--sp-gold)'], executado: ['Executado', 'var(--sp-ok)'] };
                var b = m[it.situacao] || m.orcado; var cor = b[1];
                return '<span style="display:inline-block;padding:2px 9px;border-radius:0;font-size:.72em;' +
                    'background:' + cor + '22;color:' + cor + ';border:1px solid ' + cor + '66;white-space:nowrap">' + b[0] + '</span>';
            }
            var corpo = linhas.map(function (it) {
                var ent = (Number(it.quantidade_entregue || 0)).toLocaleString('pt-BR') + '/' + (Number(it.quantidade || 0)).toLocaleString('pt-BR');
                return '<tr>' +
                    '<td style="' + padT + ';white-space:nowrap"><b>' + e(it.projeto_numero) + '</b>' +
                        (it.projeto_descricao ? '<div class="text-muted" style="font-size:.75em">' + e(it.projeto_descricao) + '</div>' : '') + '</td>' +
                    '<td style="' + padT + ';white-space:nowrap">' + e(it.item_ebs) + '</td>' +
                    '<td style="' + padT + '">' + e(it.descricao_item) + '</td>' +
                    '<td style="' + padT + ';white-space:nowrap">' + e(it.fase || '') + '</td>' +
                    '<td class="text-center" style="' + padN + '">' + ent + '</td>' +
                    '<td class="text-right" style="' + padN + '">' + money(it.valor_executado) + '</td>' +
                    '<td class="text-right" style="' + padN + '">' + money(it.tem_po ? it.valor_saldo : (it.valor_total)) + '</td>' +
                    '<td class="text-right" style="' + padN + '">' + money(it.valor_total) + '</td>' +
                    '<td style="' + padT + ';white-space:nowrap">' + badge(it) + '</td></tr>';
            }).join('');
            box.innerHTML =
                '<div class="table-responsive"><table class="table table-sm" style="min-width:960px">' +
                '<thead><tr><th style="' + padT + '">Projeto</th><th style="' + padT + '">Item EBS</th>' +
                '<th style="' + padT + '">Descrição do item</th><th style="' + padT + '">Fase</th>' +
                '<th class="text-center" style="' + padN + '">Entregue</th>' +
                '<th class="text-right" style="' + padN + '">Executado</th>' +
                '<th class="text-right" style="' + padN + '">Em andamento</th>' +
                '<th class="text-right" style="' + padN + '">Valor total</th>' +
                '<th style="' + padT + '">Situação</th></tr></thead><tbody>' + corpo + '</tbody>' +
                '<tfoot><tr><td colspan="5" class="text-right" style="' + padT + '"><b>Totais</b></td>' +
                    '<td class="text-right" style="' + padN + '"><b>' + money(totExec) + '</b></td>' +
                    '<td class="text-right" style="' + padN + '"><b>' + money(totSaldo + totOrc) + '</b></td>' +
                    '<td></td><td></td></tr></tfoot></table></div>';
        }

        // ── Cadastro de itens (catálogo) ──────────────────────────────
        async function carregarCatalogo() {
            if (catCarregado) { tabelaCatalogo(); return; }
            var box = document.getElementById('cat-lista');
            box.innerHTML = '<p class="text-muted">Carregando…</p>';
            try { CAT = await S.api('/capex-spare/catalogo'); }
            catch (x) { box.innerHTML = '<p class="alert alert-danger">' + e(x.message) + '</p>'; return; }
            catCarregado = true;
            tabelaCatalogo();
        }
        function tabelaCatalogo() {
            var box = document.getElementById('cat-lista');
            var termo = (document.getElementById('cat-busca').value || '').trim().toLowerCase();
            var linhas = (CAT.itens || []).filter(function (it) {
                return !termo || [it.item_ebs, it.descricao_item, it.ncm].some(function (c) {
                    return String(c || '').toLowerCase().indexOf(termo) !== -1;
                });
            });
            if (!linhas.length) { box.innerHTML = '<p class="text-muted">Nenhum item cadastrado.</p>'; return; }
            var corpo = linhas.map(function (it) {
                var acoes = podeEditar
                    ? '<button class="btn btn-secondary btn-sm cat-ed" data-id="' + it.id + '">Editar</button> ' +
                      '<button class="btn btn-secondary btn-sm cat-rm" data-id="' + it.id + '">Excluir</button>' : '';
                var acordo = it.acordo
                    ? ('Sim' + (it.acordo_bu ? ' · ' + e(it.acordo_bu) : '') + (it.acordo_numero ? ' (' + e(it.acordo_numero) + ')' : ''))
                    : 'Não';
                return '<tr>' +
                    '<td style="' + padT + ';white-space:nowrap"><b>' + e(it.item_ebs) + '</b></td>' +
                    '<td style="' + padT + '">' + e(it.descricao_item) + '</td>' +
                    '<td style="' + padT + ';white-space:nowrap">' + e(it.ncm) + '</td>' +
                    '<td class="text-right" style="' + padN + '">' + impostoTxt(it) + '</td>' +
                    '<td style="' + padT + ';white-space:nowrap">' + acordo + '</td>' +
                    '<td class="text-right" style="' + padN + '">' + (it.acordo ? money(it.preco_acordo) : '—') + '</td>' +
                    '<td style="' + padT + '">' + e(it.fornecedor || '') + '</td>' +
                    '<td style="' + padT + ';white-space:nowrap">' + (it.vencimento ? e(dataBR(it.vencimento)) : '') + '</td>' +
                    '<td style="' + padT + ';white-space:nowrap">' + acoes + '</td></tr>';
            }).join('');
            box.innerHTML =
                '<div class="table-responsive"><table class="table table-sm" style="min-width:1020px">' +
                '<thead><tr><th style="' + padT + '">Item EBS</th><th style="' + padT + '">Descrição do item</th>' +
                '<th style="' + padT + '">NCM</th><th class="text-right" style="' + padN + '">Imposto</th>' +
                '<th style="' + padT + '">Acordo</th><th class="text-right" style="' + padN + '">Preço acordo</th>' +
                '<th style="' + padT + '">Fornecedor</th><th style="' + padT + '">Vencimento</th>' +
                '<th style="' + padT + '"></th></tr></thead><tbody>' + corpo + '</tbody></table></div>';
            Array.prototype.forEach.call(box.querySelectorAll('.cat-ed'), function (b) {
                b.onclick = function () { abrirFormCat((CAT.itens || []).filter(function (i) { return i.id == b.dataset.id; })[0]); };
            });
            Array.prototype.forEach.call(box.querySelectorAll('.cat-rm'), function (b) {
                b.onclick = function () { excluirCat(parseInt(b.dataset.id, 10)); };
            });
        }

        function campo(rot, ctrl) {
            return '<div class="form-group" style="margin:0">' + (rot ? '<label>' + e(rot) + '</label>' : '') + ctrl + '</div>';
        }
        function secao(titulo) {
            return '<div style="font-size:.78em;font-weight:700;text-transform:uppercase;letter-spacing:.04em;' +
                'color:var(--sp-faint);margin:20px 0 10px;border-bottom:1px solid var(--sp-border);padding-bottom:5px">' + e(titulo) + '</div>';
        }
        function grade(cols, html) {
            return '<div style="display:grid;grid-template-columns:' + cols + ';gap:14px;align-items:end">' + html + '</div>';
        }

        function abrirFormCat(d) {
            var edit = !!(d && d.id);
            d = d || {};
            document.getElementById('cat-form-titulo').textContent = edit ? ('Editar item ' + (d.item_ebs || ('#' + d.id))) : 'Novo item';
            document.getElementById('cat-form-card').style.display = '';
            document.getElementById('cat-form').innerHTML =
                grade('minmax(140px,1fr) minmax(220px,2fr)',
                    campo('Item EBS', '<input id="cat-ebs" class="form-control" value="' + e(d.item_ebs || '') + '">') +
                    campo('Descrição do item', '<input id="cat-desc" class="form-control" value="' + e(d.descricao_item || '') + '">')) +
                '<div style="height:14px"></div>' +
                grade('minmax(160px,1fr) minmax(160px,1fr)',
                    campo('NCM (XXXX.XX.XX)', '<input id="cat-ncm" class="form-control" placeholder="ex.: 84713011" value="' + e(d.ncm || '') + '">') +
                    campo('Imposto (TIPI)', '<input id="cat-imp" class="form-control" value="' + (d.ncm ? impostoTxt(d) : '') + '" disabled>')) +
                '<div style="height:14px"></div>' +
                '<label style="display:flex;align-items:center;gap:6px;cursor:pointer"><input type="checkbox" id="cat-acordo"' + (d.acordo ? ' checked' : '') + '> É de acordo de compras</label>' +
                '<div id="cat-acordo-box" style="margin-top:12px;' + (d.acordo ? '' : 'display:none') + '">' +
                    grade('minmax(150px,1fr) minmax(150px,1fr) minmax(150px,1fr)',
                        campo('BU do acordo', '<select id="cat-bu" class="form-control">' + opcoesBU(d.acordo_bu || '') + '</select>') +
                        campo('Nº do acordo', '<input id="cat-acnum" class="form-control" value="' + e(d.acordo_numero || '') + '">') +
                        campo('Preço de acordo (valor unit.)', '<input id="cat-preco" type="number" step="0.01" min="0" class="form-control" value="' + e(d.preco_acordo != null ? d.preco_acordo : '') + '">')) +
                '</div>' +
                '<div class="btn-row mt-3" style="margin-top:18px">' +
                    (podeEditar ? '<button type="button" id="cat-salvar" class="btn btn-primary btn-sm">' + (edit ? 'Salvar' : 'Cadastrar') + '</button> ' : '') +
                    '<button type="button" id="cat-cancelar" class="btn btn-secondary btn-sm">Fechar</button>' +
                '</div><div id="cat-erro" class="alert alert-danger mt-2" hidden></div>';

            var ncmEl = document.getElementById('cat-ncm');
            var impEl = document.getElementById('cat-imp');
            ncmEl.onchange = async function () {
                var v = ncmEl.value.trim(); if (!v) { impEl.value = ''; return; }
                try {
                    var r = await S.api('/capex-spare/ncm?codigo=' + encodeURIComponent(v));
                    if (r && r.ncm) ncmEl.value = r.ncm;
                    impEl.value = r && r.encontrado ? (r.nt ? 'NT' : (Number(r.aliquota || 0)).toLocaleString('pt-BR') + '%') : 'NCM não encontrado';
                } catch (x) { impEl.value = ''; }
            };
            var chk = document.getElementById('cat-acordo');
            chk.onchange = function () { document.getElementById('cat-acordo-box').style.display = chk.checked ? '' : 'none'; };
            if (podeEditar) document.getElementById('cat-salvar').onclick = function () { salvarCat(edit ? d.id : 0); };
            document.getElementById('cat-cancelar').onclick = fecharFormCat;
            document.getElementById('cat-form-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
        function fecharFormCat() {
            document.getElementById('cat-form-card').style.display = 'none';
            document.getElementById('cat-form').innerHTML = '';
        }
        async function salvarCat(id) {
            var erro = document.getElementById('cat-erro'); erro.hidden = true;
            var acordo = document.getElementById('cat-acordo').checked;
            var body = {
                item_ebs: document.getElementById('cat-ebs').value.trim(),
                descricao_item: document.getElementById('cat-desc').value.trim(),
                ncm: document.getElementById('cat-ncm').value.trim(),
                acordo: acordo,
                acordo_numero: acordo ? document.getElementById('cat-acnum').value.trim() : '',
                acordo_bu: acordo ? document.getElementById('cat-bu').value : '',
                preco_acordo: acordo ? (parseFloat(document.getElementById('cat-preco').value) || 0) : 0
            };
            if (!body.item_ebs) { erro.hidden = false; erro.textContent = 'Informe o Item EBS.'; return; }
            if (acordo && !body.acordo_bu) { erro.hidden = false; erro.textContent = 'Selecione a BU do acordo.'; return; }
            try {
                if (id) await S.api('/capex-spare/catalogo/' + id, { method: 'PUT', body: body });
                else await S.api('/capex-spare/catalogo', { method: 'POST', body: body });
                fecharFormCat();
                if (S.toast) S.toast('Item salvo no cadastro.');
                catCarregado = false; carregarCatalogo();
            } catch (x) { erro.hidden = false; erro.textContent = x.message; }
        }
        async function excluirCat(id) {
            if (!confirm('Excluir este item do cadastro?')) return;
            try { await S.api('/capex-spare/catalogo/' + id, { method: 'DELETE' }); catCarregado = false; carregarCatalogo(); }
            catch (x) { alert(x.message); }
        }

        // ── Formulário de projeto ─────────────────────────────────────
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
                    campo('BU', '<select id="os-bu" class="form-control">' + opcoesBU(d.bu || '') + '</select>') +
                    campo('Categoria', '<input id="os-categoria" class="form-control" value="' + e(d.categoria || '') + '">')) +
                secao('Orçamento') +
                '<div style="max-width:280px">' +
                    campo('Destinado ao Spare', '<input id="os-aprovspare" type="number" step="0.01" min="0" class="form-control" value="' + e(d.aprovado_spare != null ? d.aprovado_spare : 0) + '">') +
                '</div>' +
                secao('Itens') +
                '<p class="text-muted" style="font-size:.8em;margin:-4px 0 10px">Digite o Item EBS: se estiver no cadastro, puxa descrição, NCM e imposto (e o preço, se for de acordo). Sem acordo, o preço é obrigatório.</p>' +
                '<div id="os-itens"></div>' +
                (podeEditar ? '<div class="btn-row mt-1"><button type="button" id="os-add-item" class="btn btn-secondary btn-sm">+ Adicionar item</button></div>' : '') +
                '<div style="display:flex;justify-content:flex-end;align-items:baseline;gap:10px;margin-top:12px;' +
                    'padding-top:10px;border-top:2px solid #64748b33">' +
                    '<span class="text-muted" style="font-size:.85em">Custo total (c/ imposto)</span>' +
                    '<span id="os-custo" style="font-weight:700;font-size:1.15em">R$ 0,00</span>' +
                '</div>' +
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
            var acordoOn = !!it.acordo;
            var entManual = (it.entrega_status === 'agendado') ? 'agendado' : 'pendente';
            var optsEnt = '<option value="pendente"' + (entManual === 'pendente' ? ' selected' : '') + '>Pendente entrega</option>' +
                '<option value="agendado"' + (entManual === 'agendado' ? ' selected' : '') + '>Agendado</option>';
            var optsFase = '<option value="">—</option>' + FASES.map(function (f) {
                return '<option value="' + e(f) + '"' + (f === (it.fase || '') ? ' selected' : '') + '>' + e(f) + '</option>';
            }).join('');
            function fld(rot, ctrl) {
                return '<div style="min-width:0"><label style="display:block;font-size:11px;color:var(--sp-faint);' +
                    'margin-bottom:3px;white-space:nowrap">' + e(rot) + '</label>' + ctrl + '</div>';
            }
            var tr = document.createElement('div');
            tr.className = 'os-item';
            tr.dataset.acordo = acordoOn ? '1' : '0';
            tr._entregas = (it.entregas && it.entregas.length) ? it.entregas.map(function (x) {
                return { quantidade: Number(x.quantidade || 0), nf: x.nf || '', data: x.data || '' };
            }) : [];
            tr.style.cssText = 'border:1px solid #64748b40;border-radius:0;padding:14px 16px;margin-bottom:12px';
            tr.innerHTML =
                // Linha 1 — identidade + situação derivada
                '<div style="display:grid;grid-template-columns:150px 1fr auto ' + (podeEditar ? '32px' : '') +
                    ';gap:12px;align-items:end">' +
                    fld('Item EBS', '<input class="form-control os-i-ebs" value="' + e(it.item_ebs || '') + '"' + ro + '>') +
                    fld('Descrição do item', '<input class="form-control os-i-desc" value="' + e(it.descricao_item || '') + '"' + ro + '>') +
                    '<div class="os-i-sit" style="align-self:center"></div>' +
                    (podeEditar ? '<button type="button" class="btn btn-secondary btn-sm os-i-rm" title="Remover item" style="height:34px">&times;</button>' : '') +
                '</div>' +
                // Linha 2 — valores
                '<div style="display:grid;grid-template-columns:130px 90px 1fr 80px auto;gap:12px;align-items:end;margin-top:12px">' +
                    fld('NCM', '<input class="form-control os-i-ncm" placeholder="XXXX.XX.XX" value="' + e(it.ncm || '') + '"' + ro + '>') +
                    fld('Qtd', '<input class="form-control os-i-qtd" type="number" step="0.001" min="0" value="' + e(it.quantidade != null ? it.quantidade : '') + '"' + ro + '>') +
                    fld('Valor unit.', '<input class="form-control os-i-vu" type="number" step="0.01" min="0" value="' + e(it.valor_unitario != null ? it.valor_unitario : '') + '"' + (acordoOn ? ' readonly title="Preço do acordo (cadastro)"' : ro) + '>') +
                    fld('% Imp.', '<input class="form-control os-i-imp" type="number" step="0.01" min="0" value="' + e(it.imposto_percent != null ? it.imposto_percent : '') + '" readonly title="Imposto do NCM (TIPI)">') +
                    '<div style="text-align:right;min-width:120px"><div style="font-size:11px;color:var(--sp-faint);margin-bottom:3px">Valor total</div>' +
                        '<div class="os-i-vt" style="font-weight:700;font-size:1.05em;font-variant-numeric:tabular-nums;white-space:nowrap">R$ 0,00</div></div>' +
                '</div>' +
                // divisória + Linha 3 — execução da compra
                '<div style="border-top:1px dashed #64748b40;margin:14px 0 12px"></div>' +
                '<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;align-items:end">' +
                    fld('RC (Solic. compra)', '<input class="form-control os-i-sc" placeholder="Nº RC" value="' + e(it.solicitacao_compra || '') + '"' + ro + '>') +
                    fld('PO (Pedido compra)', '<input class="form-control os-i-pc" placeholder="Nº PO" value="' + e(it.pedido_compra || '') + '"' + ro + '>') +
                    fld('Fase', '<select class="form-control os-i-fase"' + dis + '>' + optsFase + '</select>') +
                    fld('Status entrega', '<select class="form-control os-i-ent"' + dis + '>' + optsEnt + '</select>') +
                '</div>' +
                // Entregas (parciais, cada uma com NF)
                '<div style="margin-top:12px;background:#64748b14;border-radius:0;padding:10px 12px">' +
                    '<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">' +
                        '<span style="font-size:12px;color:var(--sp-faint)">Entregas <span class="os-i-entinfo"></span></span>' +
                        (podeEditar ? '<button type="button" class="btn btn-secondary btn-sm os-i-addent">+ Registrar entrega</button>' : '') +
                    '</div>' +
                    '<div class="os-i-entlist" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px"></div>' +
                    '<div class="os-i-entform" style="display:none;gap:8px;align-items:end;margin-top:10px;flex-wrap:wrap">' +
                        fld('Qtd entregue', '<input class="form-control os-ie-qtd" type="number" step="0.001" min="0" style="width:120px">') +
                        fld('NF', '<input class="form-control os-ie-nf" placeholder="Nº NF" style="width:140px">') +
                        fld('Data', '<input class="form-control os-ie-data" type="date" style="width:150px">') +
                        '<button type="button" class="btn btn-primary btn-sm os-ie-add">Adicionar</button>' +
                        '<button type="button" class="btn btn-secondary btn-sm os-ie-cancel">Cancelar</button>' +
                    '</div>' +
                '</div>';
            document.getElementById('os-itens').appendChild(tr);
            syncEntrega(tr);
            if (podeEditar) {
                tr.querySelector('.os-i-rm').onclick = function () { tr.remove(); recalc(); };
                tr.querySelector('.os-i-qtd').oninput = function () { recalc(); syncEntrega(tr); };
                tr.querySelector('.os-i-vu').oninput = recalc;
                tr.querySelector('.os-i-ebs').addEventListener('change', function () { puxarCatalogo(tr); });
                tr.querySelector('.os-i-ncm').addEventListener('change', function () { aplicarNcm(tr); });
                tr.querySelector('.os-i-pc').addEventListener('input', function () { syncEntrega(tr); });
                tr.querySelector('.os-i-ent').onchange = function () {
                    var ent = tr.querySelector('.os-i-ent');
                    if (ent.value === 'agendado' && !tr.querySelector('.os-i-pc').value.trim()) {
                        alert('Informe a PO (Pedido de compra) antes de agendar.');
                        ent.value = 'pendente';
                    }
                    syncEntrega(tr);
                };
                var form = tr.querySelector('.os-i-entform');
                tr.querySelector('.os-i-addent').onclick = function () {
                    if (!tr.querySelector('.os-i-pc').value.trim()) { alert('Informe a PO antes de registrar entrega.'); return; }
                    form.style.display = form.style.display === 'none' ? 'flex' : 'none';
                };
                tr.querySelector('.os-ie-cancel').onclick = function () { form.style.display = 'none'; };
                tr.querySelector('.os-ie-add').onclick = function () {
                    var qe = parseFloat(tr.querySelector('.os-ie-qtd').value) || 0;
                    var nfe = tr.querySelector('.os-ie-nf').value.trim();
                    var dt = tr.querySelector('.os-ie-data').value || '';
                    if (qe <= 0) { alert('Informe a quantidade entregue.'); return; }
                    if (!nfe) { alert('Informe a NF da entrega.'); return; }
                    var qtd = parseFloat(tr.querySelector('.os-i-qtd').value) || 0;
                    var jaEnt = tr._entregas.reduce(function (a, x) { return a + (x.quantidade || 0); }, 0);
                    if (qtd > 0 && jaEnt + qe > qtd) {
                        if (!confirm('A quantidade entregue ultrapassa a quantidade do item. Continuar?')) return;
                    }
                    tr._entregas.push({ quantidade: qe, nf: nfe, data: dt });
                    tr.querySelector('.os-ie-qtd').value = ''; tr.querySelector('.os-ie-nf').value = ''; tr.querySelector('.os-ie-data').value = '';
                    form.style.display = 'none';
                    syncEntrega(tr);
                };
            }
        }

        // Recalcula entregas: quantidade entregue, saldo, status derivado e chips.
        function syncEntrega(tr) {
            var qtd = parseFloat(tr.querySelector('.os-i-qtd').value) || 0;
            var lista = tr._entregas || [];
            var qent = lista.reduce(function (a, x) { return a + (Number(x.quantidade) || 0); }, 0);
            var saldo = Math.max(qtd - qent, 0);
            var pc = tr.querySelector('.os-i-pc').value.trim();
            var estado, cor;
            if (qtd > 0 && qent >= qtd) { estado = 'Entregue'; cor = 'var(--sp-ok)'; }
            else if (qent > 0) { estado = 'Entrega parcial'; cor = 'var(--sp-gold)'; }
            else if (pc) { estado = (tr.querySelector('.os-i-ent').value === 'agendado') ? 'Agendado' : 'Em andamento'; cor = 'var(--sp-teal-text)'; }
            else { estado = 'Orçado/Previsto'; cor = 'var(--sp-faint)'; }
            tr.querySelector('.os-i-sit').innerHTML =
                '<span style="display:inline-block;padding:3px 10px;border-radius:0;font-size:.74em;' +
                'background:' + cor + '22;color:' + cor + ';border:1px solid ' + cor + '66;white-space:nowrap">' + estado + '</span>';
            var info = tr.querySelector('.os-i-entinfo');
            if (qent > 0 || qtd > 0) {
                info.textContent = '— entregue ' + (qent).toLocaleString('pt-BR') + ' de ' + (qtd).toLocaleString('pt-BR') +
                    ' · saldo ' + (saldo).toLocaleString('pt-BR');
            } else { info.textContent = ''; }
            var box = tr.querySelector('.os-i-entlist');
            box.innerHTML = lista.map(function (x, idx) {
                return '<span style="display:inline-flex;align-items:center;gap:6px;background:#64748b22;' +
                    'border:1px solid #64748b55;border-radius:0;padding:3px 10px;font-size:.76em">' +
                    (Number(x.quantidade) || 0).toLocaleString('pt-BR') + ' un · NF ' + e(x.nf || '—') +
                    (x.data ? ' · ' + dataBR(x.data) : '') +
                    (podeEditar ? ' <a href="#" class="os-ie-rm" data-i="' + idx + '" style="color:var(--sp-alerta);text-decoration:none">&times;</a>' : '') +
                    '</span>';
            }).join('') || '<span class="text-muted" style="font-size:.76em">Nenhuma entrega registrada.</span>';
            // Status entrega manual só quando ainda não há entrega.
            var selEnt = tr.querySelector('.os-i-ent');
            if (selEnt) selEnt.disabled = (!podeEditar) || qent > 0;
            var addBtn = tr.querySelector('.os-i-addent');
            if (addBtn) addBtn.style.display = (pc && saldo > 0) ? '' : 'none';
            if (podeEditar) {
                Array.prototype.forEach.call(box.querySelectorAll('.os-ie-rm'), function (a) {
                    a.onclick = function (ev) {
                        ev.preventDefault();
                        tr._entregas.splice(parseInt(a.dataset.i, 10), 1);
                        syncEntrega(tr);
                    };
                });
            }
        }

        // Item EBS no cadastro → puxa descrição, NCM, imposto e (se acordo) preço.
        async function puxarCatalogo(tr) {
            var ebs = tr.querySelector('.os-i-ebs').value.trim();
            if (!ebs) return;
            var buEl = document.getElementById('os-bu');
            var bu = buEl ? buEl.value : '';
            try {
                var d = await S.api('/capex-spare/catalogo/item?item_ebs=' + encodeURIComponent(ebs) +
                    (bu ? '&bu=' + encodeURIComponent(bu) : ''));
                if (!d || !d.item_ebs) return;   // não cadastrado: preenche manual
                if (d.descricao_item) tr.querySelector('.os-i-desc').value = d.descricao_item;
                if (d.ncm) tr.querySelector('.os-i-ncm').value = d.ncm;
                if (d.imposto_percent != null) tr.querySelector('.os-i-imp').value = d.imposto_percent;
                var vu = tr.querySelector('.os-i-vu');
                tr.dataset.acordo = d.acordo ? '1' : '0';
                if (d.acordo) {
                    vu.value = (d.preco_acordo != null ? d.preco_acordo : '');
                    vu.readOnly = true; vu.title = 'Preço do acordo (cadastro)';
                } else {
                    vu.readOnly = false; vu.title = '';
                }
                recalc();
                if (S.toast) S.toast('Item do cadastro aplicado' + (d.acordo ? ' (preço do acordo).' : '.'));
            } catch (x) { /* silencioso */ }
        }

        // NCM digitado manual → formata e busca alíquota da TIPI.
        async function aplicarNcm(tr) {
            var el = tr.querySelector('.os-i-ncm'); var v = el.value.trim();
            if (!v) return;
            try {
                var d = await S.api('/capex-spare/ncm?codigo=' + encodeURIComponent(v));
                if (d && d.ncm) el.value = d.ncm;
                if (d && d.encontrado) { tr.querySelector('.os-i-imp').value = (d.aliquota != null ? d.aliquota : 0); recalc(); }
            } catch (x) { /* silencioso */ }
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
                    ncm: tr.querySelector('.os-i-ncm').value.trim(),
                    quantidade: parseFloat(tr.querySelector('.os-i-qtd').value) || 0,
                    valor_unitario: parseFloat(tr.querySelector('.os-i-vu').value) || 0,
                    imposto_percent: parseFloat(tr.querySelector('.os-i-imp').value) || 0,
                    acordo: tr.dataset.acordo === '1',
                    fase: tr.querySelector('.os-i-fase').value || '',
                    solicitacao_compra: tr.querySelector('.os-i-sc').value.trim(),
                    pedido_compra: tr.querySelector('.os-i-pc').value.trim(),
                    entrega_status: tr.querySelector('.os-i-ent').value || 'pendente',
                    entregas: (tr._entregas || []).slice()
                };
                if (o.item_ebs || o.descricao_item || o.quantidade || o.valor_unitario || o.ncm) itens.push(o);
            });
            var av = document.getElementById('os-aprovspare').value;
            return {
                numero: document.getElementById('os-numero').value.trim(),
                descricao: document.getElementById('os-descricao').value.trim(),
                bu: document.getElementById('os-bu').value,
                categoria: document.getElementById('os-categoria').value.trim(),
                aprovado_spare: av === '' ? 0 : parseFloat(av),
                observacao: document.getElementById('os-obs').value.trim(),
                itens: itens
            };
        }

        async function salvar(id) {
            var erro = document.getElementById('os-erro'); erro.hidden = true;
            var body = coletar();
            if (!body.bu) { erro.hidden = false; erro.textContent = 'Selecione a BU do projeto.'; return; }
            // Sem acordo, o preço é obrigatório (checa antes de enviar).
            for (var i = 0; i < body.itens.length; i++) {
                var it = body.itens[i];
                if (!it.acordo && (!it.valor_unitario || it.valor_unitario <= 0)) {
                    erro.hidden = false;
                    erro.textContent = 'Informe o preço do item "' + (it.item_ebs || it.descricao_item || ('linha ' + (i + 1))) + '" (não é de acordo de compras).';
                    return;
                }
            }
            try {
                if (id) await S.api('/capex-spare/projetos/' + id, { method: 'PUT', body: body });
                else await S.api('/capex-spare/projetos', { method: 'POST', body: body });
                fecharForm();
                if (S.toast) S.toast('Projeto salvo.');
                carregar();
            } catch (x) { erro.hidden = false; erro.textContent = x.message; }
        }

        async function excluir(id) {
            if (!confirm('Excluir este projeto e seus itens?')) return;
            try { await S.api('/capex-spare/projetos/' + id, { method: 'DELETE' }); carregar(); }
            catch (x) { alert(x.message); }
        }

        async function sincronizarEbs() {
            var msg = document.getElementById('os-msg');
            msg.textContent = 'Consultando o EBS…';
            try {
                var d = await S.api('/capex-spare/sincronizar', { method: 'POST', body: {} });
                msg.textContent = 'Aprovado atualizado do EBS: ' + (d.atualizados || 0) + ' projeto(s).' + (d.aviso ? ' ' + d.aviso : '');
                carregar();
            } catch (x) { msg.textContent = 'Falha ao atualizar do EBS: ' + x.message; }
        }

        // ── Eventos ───────────────────────────────────────────────────
        if (podeCriar) document.getElementById('os-novo').onclick = function () { abrirForm(); };
        if (podeCriar) document.getElementById('cat-novo').onclick = function () { abrirFormCat(); };
        if (podeCriar) {
            var catFile = document.getElementById('cat-file');
            document.getElementById('cat-import').onclick = function () { catFile.value = ''; catFile.click(); };
            catFile.onchange = async function () {
                if (!catFile.files || !catFile.files[0]) return;
                var msg = document.getElementById('cat-import-msg');
                msg.textContent = 'Importando acordos…';
                try {
                    var fd = new FormData(); fd.append('arquivo', catFile.files[0]);
                    var d = await S.api('/capex-spare/catalogo/importar', { method: 'POST', body: fd });
                    msg.textContent = 'Importado: ' + (d.criados || 0) + ' novo(s), ' + (d.atualizados || 0) +
                        ' atualizado(s) de ' + (d.total || 0) + ' acordo(s).' + (d.aviso ? ' ' + d.aviso : '');
                    catCarregado = false; carregarCatalogo();
                } catch (x) { msg.textContent = 'Falha ao importar: ' + x.message; }
            };
        }
        document.getElementById('os-busca').addEventListener('keydown', function (ev) { if (ev.key === 'Enter') tabela(); });
        document.getElementById('cat-busca').addEventListener('input', tabelaCatalogo);
        document.getElementById('os-it-status').onchange = tabelaItens;
        document.getElementById('os-it-busca').addEventListener('input', tabelaItens);

        mostrarAba('projetos');
        carregar();
    }
};
