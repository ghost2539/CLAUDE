/* ================================================================
   Módulo: Agendamentos de Fornecedores (menu Entrada)
   Cadastra a entrega antes de chegar; ao chegar, confirma o
   recebimento (que a manda para a etapa de internalização).
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.agendamentos_forn = {

    async render(container) {
        var S = window.SPARE, e = S.esc;
        var OPC = { bus: [], destinos: [], status: {} };
        var u = S.user() || {};
        var pm = (u.permission_map || {}).agendamentos_forn || {};
        var podeCriar = !!(u.is_admin || pm.can_create);
        var podeEditar = !!(u.is_admin || pm.can_edit);
        var podeExcluir = !!(u.is_admin || pm.can_admin);
        // Só estas BUs têm o pedido no EBS (a rota do servidor usa a mesma
        // lista). Youcom compra por fora, então não há PO para ler lá.
        var BUS_COM_EBS = ['Renner', 'Camicado'];

        container.innerHTML =
            '<h1 class="page-title">Agendamentos de Fornecedores</h1>' +
            '<div class="card mb-3"><div class="card-header" ' +
                'style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">' +
                '<span>Agendamentos</span>' +
                (podeCriar ? '<button id="agf-novo" class="btn btn-primary btn-sm">Novo agendamento</button>' : '') +
                '</div><div class="card-body">' +
                '<div class="filter-grid">' +
                    '<div class="form-group"><label for="agf-f-status">Status</label>' +
                        '<select id="agf-f-status" class="form-control">' +
                        '<option value="">Todos</option>' +
                        '<option value="AGENDADO">Agendado</option>' +
                        '<option value="RECEBIDO">Recebido</option></select></div>' +
                    '<div class="form-group"><label for="agf-f-busca">Buscar (NF, PO, fornecedor)</label>' +
                        '<input id="agf-f-busca" class="form-control" placeholder="digite e Enter"></div>' +
                '</div>' +
                '<div id="agf-lista" class="mt-3"></div>' +
            '</div></div>' +
            '<div class="card" id="agf-form-card" style="display:none">' +
                '<div class="card-header" id="agf-form-titulo">Novo agendamento</div>' +
                '<div class="card-body"><div id="agf-form"></div></div>' +
            '</div>';

        try { OPC = await S.api('/agendamentos-forn/opcoes'); } catch (x) { /* segue com listas vazias */ }

        function opcoesSelect(lista, sel, comVazio) {
            return (comVazio ? '<option value="">—</option>' : '') +
                lista.map(function (o) {
                    var val = o.valor !== undefined ? o.valor : o;
                    var rot = o.rotulo !== undefined ? o.rotulo : o;
                    return '<option value="' + e(val) + '"' + (val === sel ? ' selected' : '') + '>' + e(rot) + '</option>';
                }).join('');
        }

        // ── Formulário (novo/editar) ──────────────────────────────────
        function abrirForm(dado) {
            var edit = !!(dado && dado.id);
            document.getElementById('agf-form-titulo').textContent =
                edit ? ('Editar agendamento #' + dado.id) : 'Novo agendamento';
            document.getElementById('agf-form-card').style.display = '';
            var d = dado || {};
            var host = document.getElementById('agf-form');
            host.innerHTML =
                '<div class="form-grid cols-2">' +
                    campo('BU', '<select id="agf-bu" class="form-control">' + opcoesSelect(OPC.bus, d.bu, true) + '</select>') +
                    campo('Estoque destino *', '<select id="agf-destino" class="form-control">' + opcoesSelect(OPC.destinos, d.estoque_destino, true) + '</select>') +
                    campo('Fornecedor *', '<input id="agf-fornecedor" class="form-control" value="' + e(d.fornecedor || '') + '">') +
                    campo('Volumes', '<input id="agf-volumes" type="number" min="0" class="form-control" value="' + (d.volumes != null ? e(d.volumes) : '') + '">') +
                    campo('Data agendada *', '<input id="agf-data" type="date" class="form-control" value="' + e(d.data_agendada || '') + '">') +
                '</div>' +
                '<h3 class="mt-3">Pedidos</h3>' +
                '<p class="text-muted" style="margin-top:0">Uma linha por PO. A mesma NF pode cobrir mais de uma — ' +
                    'repita o número dela em cada PO que ela atende.</p>' +
                '<div id="agf-pos"></div>' +
                '<div class="btn-row mt-2"><button type="button" id="agf-add-po" class="btn btn-secondary btn-sm">+ Adicionar PO</button></div>' +
                '<h3 class="mt-3">Equipamentos</h3>' +
                '<div id="agf-equis"></div>' +
                '<div class="btn-row mt-2"><button type="button" id="agf-add-equi" class="btn btn-secondary btn-sm">+ Adicionar equipamento</button></div>' +
                '<div class="btn-row mt-3">' +
                    '<button type="button" id="agf-salvar" class="btn btn-primary btn-sm">' + (edit ? 'Salvar' : 'Cadastrar') + '</button> ' +
                    '<button type="button" id="agf-cancelar" class="btn btn-secondary btn-sm">Cancelar</button>' +
                '</div>' +
                '<div id="agf-form-erro" class="alert alert-danger mt-2" hidden></div>';

            // Agendamento antigo tem PO/NF nas colunas soltas; o novo já vem
            // com a lista. Um ou outro vira a primeira linha.
            var pos = (d.pedidos && d.pedidos.length) ? d.pedidos
                    : ((d.po || d.nf) ? [{ po: d.po, nf: d.nf }] : []);
            pos.forEach(addPo);
            if (!pos.length) addPo();

            var equis = (d.equipamentos && d.equipamentos.length) ? d.equipamentos : [];
            equis.forEach(addEqui);
            if (!equis.length) addEqui();

            document.getElementById('agf-add-po').onclick = function () { addPo(); };
            document.getElementById('agf-add-equi').onclick = function () { addEqui(); };
            document.getElementById('agf-cancelar').onclick = fecharForm;
            document.getElementById('agf-salvar').onclick = function () { salvar(edit ? d.id : 0); };
            host.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

        function campo(rotulo, controle) {
            return '<div class="form-group"><label>' + e(rotulo) + '</label>' + controle + '</div>';
        }

        // ── Pedidos (uma linha por PO) ────────────────────────────────
        function addPo(ped) {
            ped = ped || {};
            var linha = document.createElement('div');
            linha.className = 'agf-po-linha';
            // Caixa de uma linha: sem cor inventada — os tokens do tema já
            // dão o tom certo no claro e no escuro.
            linha.style.cssText = 'border:1px solid var(--sp-border);' +
                'border-radius:var(--sp-raio-campo);padding:10px;margin-bottom:8px';
            linha.innerHTML =
                '<div class="filter-grid">' +
                    '<div class="form-group"><label>PO *</label>' +
                        // Acordo de compras tem um número só e várias liberações;
                        // é o número depois do hífen que diz qual pedido é.
                        '<input class="form-control agf-po" placeholder="ex.: 2570313-25" ' +
                        'title="Acordo de compras: número da PO, hífen, e a liberação. ' +
                        'Compra avulsa: só o número." value="' + e(ped.po || '') + '"></div>' +
                    '<div class="form-group"><label>NF</label>' +
                        '<input class="form-control agf-nf" placeholder="nota que cobre esta PO" value="' + e(ped.nf || '') + '"></div>' +
                    '<div class="form-group"><label>&nbsp;</label><div class="btn-row">' +
                        '<button type="button" class="btn btn-outline btn-sm agf-po-buscar">Buscar no EBS</button>' +
                        '<button type="button" class="btn btn-outline btn-sm agf-po-rem" title="Remover esta PO">&times;</button>' +
                    '</div></div>' +
                '</div>' +
                '<div class="agf-po-msg text-muted" style="margin-top:6px"></div>';

            // O que o EBS respondeu fica guardado na linha e volta no salvar:
            // na chegada da carga a conferência não pode depender de o banco
            // do EBS estar de pé.
            linha.dataset.fornecedorEbs = ped.fornecedor_ebs || '';
            linha.dataset.statusEbs = ped.status_ebs || '';
            if (ped.fornecedor_ebs || ped.status_ebs) {
                avisoPo(linha, 'EBS: ' + (ped.fornecedor_ebs || 'fornecedor não informado') +
                    (ped.status_ebs ? ' — ' + ped.status_ebs : ''), 'ok');
            }

            linha.querySelector('.agf-po-rem').onclick = function () {
                linha.remove();
                // Sem nenhuma linha a tela fica sem onde digitar a PO.
                if (!document.querySelectorAll('.agf-po-linha').length) addPo();
            };
            linha.querySelector('.agf-po-buscar').onclick = function () { buscarPo(linha, true); };
            // Sair do campo também busca, mas só se a PO mudou desde a última
            // consulta: senão um Tab a mais repetiria a ida ao banco.
            linha.querySelector('.agf-po').onblur = function () { buscarPo(linha, false); };
            document.getElementById('agf-pos').appendChild(linha);
            return linha;
        }

        function avisoPo(linha, texto, tipo) {
            var box = linha.querySelector('.agf-po-msg');
            box.textContent = texto || '';
            // Cor por token: erro no acento, sucesso no teal, resto no muted
            // que já vem da classe.
            box.style.color = tipo === 'erro' ? 'var(--sp-alerta)'
                            : tipo === 'ok' ? 'var(--sp-teal-text)' : '';
        }

        async function buscarPo(linha, manual) {
            var po = linha.querySelector('.agf-po').value.trim();
            var bu = document.getElementById('agf-bu').value;
            if (!po) {
                if (manual) avisoPo(linha, 'Informe o número da PO antes de buscar.', '');
                return;
            }
            if (BUS_COM_EBS.indexOf(bu) === -1) {
                // Não é erro de ninguém: não há o que consultar. A tela diz
                // por quê para o operador não ficar esperando o preenchimento.
                avisoPo(linha, bu
                    ? ('A BU ' + bu + ' não tem pedido no EBS — informe os equipamentos à mão.')
                    : 'Escolha a BU primeiro: só Renner e Camicado têm o pedido no EBS.', '');
                return;
            }
            if (!manual && linha.dataset.ultimaPo === po) return;  // nada mudou
            if (linha.dataset.buscando === '1') return;            // clique + blur juntos

            var btn = linha.querySelector('.agf-po-buscar');
            linha.dataset.buscando = '1';
            btn.disabled = true;
            avisoPo(linha, 'Consultando o EBS…', '');
            try {
                // Consulta NOSSA, direto na base Oracle do EBS (não passa pelo
                // portal de compras). Ela foi escrita contra o esquema padrão
                // do EBS e ainda não foi rodada na base de produção deles — por
                // isso a tela pede conferência dos itens antes de salvar.
                var d = await S.api('/agendamentos-forn/po/' + encodeURIComponent(po) +
                                    '?bu=' + encodeURIComponent(bu));
                linha.dataset.ultimaPo = po;
                linha.dataset.fornecedorEbs = d.fornecedor || '';
                linha.dataset.statusEbs = d.status || '';
                // O fornecedor do pedido serve para o cabeçalho quando ninguém
                // digitou nada ainda — mas não sobrescreve o que já está lá.
                var forn = document.getElementById('agf-fornecedor');
                if (d.fornecedor && !forn.value.trim()) forn.value = d.fornecedor;
                var r = aplicarItensEbs(po, d.itens || []);
                avisoPo(linha, 'EBS: ' + (d.fornecedor || 'fornecedor não informado') +
                    (d.status ? ' — ' + d.status : '') +
                    ' · ' + (d.itens || []).length + ' item(ns) do pedido' +
                    (r.substituidos ? ' (linhas desta PO atualizadas)' : '') +
                    (r.mantidos ? ' · mantive ' + r.mantidos + ' equipamento(s) que você já tinha digitado' : '') +
                    '. Confira os equipamentos antes de salvar.', 'ok');
            } catch (x) {
                avisoPo(linha, erroPo(x, po), 'erro');
                if (manual) S.toast(erroPo(x, po), x.status === 503 ? 'warning' : 'error');
            } finally {
                btn.disabled = false;
                linha.dataset.buscando = '';
            }
        }

        function erroPo(x, po) {
            // 503 é do servidor (sem credencial ou sem driver), não de quem
            // digitou — a mensagem precisa deixar isso claro, senão o operador
            // fica corrigindo uma PO que está certa.
            if (x.status === 503)
                return 'A consulta ao EBS está indisponível neste servidor (falta a credencial do ' +
                       'banco Oracle). Não é erro do que você digitou: informe os equipamentos à ' +
                       'mão e avise o TI. Detalhe: ' + x.message;
            if (x.status === 502)
                return 'O banco do EBS recusou a consulta. Tente de novo em instantes; se persistir, ' +
                       'avise o TI e siga à mão. Detalhe: ' + x.message;
            if (x.status === 422) return x.message || ('PO ' + po + ' inválida.');
            if (x.status === 404)
                return 'PO ' + po + ' não encontrada no EBS (ou sem linha ativa). Confira o número.';
            return x.message || 'Não consegui consultar a PO ' + po + '.';
        }

        function aplicarItensEbs(po, itens) {
            /* Regra de convivência com o que já está na tela:
               - o que o operador digitou NUNCA é apagado — os itens do EBS
                 são ACRESCENTADOS ao fim;
               - só somem as linhas vazias (o campo em branco que a tela cria
                 sozinha) e as que vieram desta MESMA PO numa busca anterior,
                 para buscar duas vezes não duplicar tudo.
               Preferi acrescentar a pedir confirmação: com várias POs por
               agendamento, um "substituir?" a cada busca apagaria os itens da
               PO anterior e viraria clique no automático. */
            var host = document.getElementById('agf-equis');
            var mantidos = 0, substituidos = 0;
            Array.prototype.forEach.call(host.querySelectorAll('.agf-equi-linha'), function (l) {
                if (l.dataset.agfPo === po) { l.remove(); substituidos++; return; }
                var desc = l.querySelector('.agf-eq-desc').value.trim();
                var qtd = l.querySelector('.agf-eq-qtd').value.trim();
                if (!desc && !qtd) { l.remove(); return; }
                mantidos++;
            });
            itens.forEach(function (it) {
                addEqui({
                    descricao: it.descricao || it.item_ebs || '',
                    // Quantidade PEDIDA, como o pedido de quem usa a tela. O
                    // que já foi recebido aparece só como dica na linha.
                    quantidade: it.quantidade_pedida,
                    origem_po: po,
                    dica: 'PO ' + po + ' linha ' + (it.linha != null ? it.linha : '?') +
                          (it.item_ebs ? ' · item ' + it.item_ebs : '') +
                          (it.unidade ? ' · ' + it.unidade : '') +
                          ' · pedida ' + it.quantidade_pedida +
                          ', recebida ' + it.quantidade_recebida +
                          ', pendente ' + it.quantidade_pendente
                });
            });
            if (!host.querySelectorAll('.agf-equi-linha').length) addEqui();
            return { mantidos: mantidos, substituidos: substituidos };
        }

        function addEqui(eq) {
            eq = eq || {};
            var linha = document.createElement('div');
            linha.className = 'agf-equi-linha';
            linha.style.cssText = 'display:flex;gap:8px;margin-bottom:6px;align-items:center';
            var dica = eq.dica ? ' title="' + e(eq.dica) + '"' : '';
            linha.innerHTML =
                '<input class="form-control agf-eq-desc"' + dica + ' placeholder="Equipamento (ex.: Positivo Master CE800)" value="' + e(eq.descricao || '') + '" style="flex:1">' +
                '<input class="form-control agf-eq-qtd"' + dica + ' type="number" min="1" placeholder="Qtd" value="' + (eq.quantidade != null ? e(eq.quantidade) : '') + '" style="width:90px">' +
                '<button type="button" class="btn btn-secondary btn-sm agf-eq-rem" title="Remover">&times;</button>';
            // Marca de origem: é o que permite refazer a busca de uma PO sem
            // encostar nas linhas digitadas à mão nem nas das outras POs.
            if (eq.origem_po) linha.dataset.agfPo = eq.origem_po;
            linha.querySelector('.agf-eq-rem').onclick = function () { linha.remove(); };
            document.getElementById('agf-equis').appendChild(linha);
        }

        function fecharForm() {
            document.getElementById('agf-form-card').style.display = 'none';
            document.getElementById('agf-form').innerHTML = '';
        }

        function coletar() {
            var equis = [];
            Array.prototype.forEach.call(document.querySelectorAll('.agf-equi-linha'), function (l) {
                var desc = l.querySelector('.agf-eq-desc').value.trim();
                var qtd = parseInt(l.querySelector('.agf-eq-qtd').value, 10);
                if (desc || l.querySelector('.agf-eq-qtd').value) {
                    equis.push({ descricao: desc, quantidade: isNaN(qtd) ? 0 : qtd });
                }
            });
            var pedidos = [];
            Array.prototype.forEach.call(document.querySelectorAll('.agf-po-linha'), function (l) {
                var po = l.querySelector('.agf-po').value.trim();
                var nf = l.querySelector('.agf-nf').value.trim();
                // Linha totalmente em branco é a que a tela criou e ninguém
                // usou: não vai para o servidor.
                if (!po && !nf) return;
                pedidos.push({
                    po: po, nf: nf,
                    fornecedor_ebs: l.dataset.fornecedorEbs || '',
                    status_ebs: l.dataset.statusEbs || ''
                });
            });
            var vol = document.getElementById('agf-volumes').value.trim();
            return {
                bu: document.getElementById('agf-bu').value,
                // A PRIMEIRA PO/NF vai também nas colunas soltas porque a
                // listagem e a busca ainda leem de lá (o servidor refaz isso,
                // mas mandar já certo evita divergência se algo mudar aqui).
                nf: pedidos.length ? pedidos[0].nf : '',
                po: pedidos.length ? pedidos[0].po : '',
                pedidos: pedidos,
                volumes: vol === '' ? null : parseInt(vol, 10),
                fornecedor: document.getElementById('agf-fornecedor').value.trim(),
                estoque_destino: document.getElementById('agf-destino').value,
                data_agendada: document.getElementById('agf-data').value,
                equipamentos: equis
            };
        }

        async function salvar(id) {
            var erro = document.getElementById('agf-form-erro');
            erro.hidden = true;
            var corpo = coletar();
            try {
                if (id) {
                    await S.api('/agendamentos-forn/' + id, { method: 'PATCH', body: JSON.stringify(corpo) });
                    S.toast('Agendamento atualizado.', 'success');
                } else {
                    await S.api('/agendamentos-forn', { method: 'POST', body: JSON.stringify(corpo) });
                    S.toast('Agendamento cadastrado.', 'success');
                }
                fecharForm();
                carregar();
            } catch (x) {
                erro.hidden = false;
                erro.textContent = x.message;
            }
        }

        // ── Lista ─────────────────────────────────────────────────────
        async function carregar() {
            var host = document.getElementById('agf-lista');
            host.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div>';
            try {
                var qs = '?status=' + encodeURIComponent(document.getElementById('agf-f-status').value) +
                         '&busca=' + encodeURIComponent(document.getElementById('agf-f-busca').value.trim());
                var d = await S.api('/agendamentos-forn' + qs);
                if (!d.total) { host.innerHTML = '<p class="text-muted">Nenhum agendamento.</p>'; return; }
                host.innerHTML =
                    '<div class="table-wrapper"><table class="data-table"><thead><tr>' +
                    '<th>NF</th><th>PO</th><th>BU</th><th>Fornecedor</th><th>Destino</th>' +
                    '<th>Vol.</th><th>Agendada</th><th>Equipamentos</th><th>Status</th>' +
                    '<th>Recebimento</th><th></th>' +
                    '</tr></thead><tbody>' + d.itens.map(linhaHtml).join('') + '</tbody></table></div>';
                d.itens.forEach(function (a) {
                    var ed = document.getElementById('agf-editar-' + a.id);
                    if (ed) ed.onclick = function () { abrirForm(a); };
                    var ex = document.getElementById('agf-excluir-' + a.id);
                    if (ex) ex.onclick = function () { excluir(a.id); };
                });
            } catch (x) {
                host.innerHTML = '<div class="alert alert-danger">' + e(x.message) + '</div>';
            }
        }

        function linhaHtml(a) {
            var equis = (a.equipamentos || []).map(function (q) {
                return e(q.descricao) + ' <b>×' + e(q.quantidade) + '</b>';
            }).join('<br>') || '<span class="text-muted">—</span>';
            var badge = a.status === 'RECEBIDO'
                ? '<span class="badge badge-success">Recebido</span>'
                : '<span class="badge badge-warning">Agendado</span>';
            var acoes = '';
            if (a.status !== 'RECEBIDO' && podeEditar)
                acoes += '<button id="agf-editar-' + a.id + '" class="btn btn-secondary btn-sm">Editar</button> ';
            if (podeExcluir)
                acoes += '<button id="agf-excluir-' + a.id + '" class="btn btn-secondary btn-sm">Excluir</button>';
            return '<tr>' +
                '<td>' + e(a.nf) + '</td><td>' + e(a.po) + '</td><td>' + e(a.bu || '—') + '</td>' +
                '<td>' + e(a.fornecedor) + '</td><td>' + e(a.estoque_destino_rotulo) + '</td>' +
                '<td>' + (a.volumes != null ? e(a.volumes) : '—') + '</td>' +
                '<td>' + e(fmtData(a.data_agendada)) + '</td>' +
                '<td>' + equis + '</td><td>' + badge + '</td>' +
                '<td>' + (a.data_recebimento ? e(fmtData(a.data_recebimento)) : '<span class="text-muted">—</span>') + '</td>' +
                '<td style="white-space:nowrap">' + (acoes || '<span class="text-muted">—</span>') + '</td>' +
                '</tr>';
        }

        function fmtData(iso) {
            if (!iso) return '';
            var p = iso.split('-');
            return p.length === 3 ? (p[2] + '/' + p[1] + '/' + p[0]) : iso;
        }

        async function excluir(id) {
            if (!confirm('Excluir este agendamento? Esta ação não pode ser desfeita.')) return;
            try {
                await S.api('/agendamentos-forn/' + id, { method: 'DELETE' });
                S.toast('Agendamento excluído.', 'success');
                carregar();
            } catch (x) { S.toast(x.message, 'error'); }
        }

        if (podeCriar) document.getElementById('agf-novo').onclick = function () { abrirForm(null); };
        document.getElementById('agf-f-status').onchange = carregar;
        document.getElementById('agf-f-busca').onkeydown = function (ev) { if (ev.key === 'Enter') carregar(); };
        carregar();
    }
};
