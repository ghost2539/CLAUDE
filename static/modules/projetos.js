/* ================================================================
   Módulo: Projetos de Loja (A16)

   Inauguração e reforma, item por item. O token é o item do projeto:
   uma linha "tantos equipamentos deste modelo, para esta área" que
   nasce antes de existir série e anda no próprio ritmo.

   A tela tem três estados, e nunca dois ao mesmo tempo:

     fila     todos os itens de todos os projetos, por etapa — é assim
              que quem separa e quem configura trabalha
     projetos a lista de projetos, para quem acompanha a loja
     detalhe  um projeto, com a ação certa em cada item

   O chamado vem primeiro no formulário pelo mesmo motivo da Separação:
   é dele que sai a loja, e projeto sem loja não tem para onde ir.
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};

(function () {
    var S = null;
    var ABAS = [['fila', 'Fila por etapa'], ['projetos', 'Projetos']];

    /* Ordem em que as etapas aparecem na fila. Pausas ficam no fim: são
       o que está esperando alguém de fora, não o que está para fazer. */
    var ETAPAS = ['AG_DEFINICAO', 'AG_SEPARACAO_PROJ', 'EX_SEPARACAO_PROJ',
                  'AG_CONFIGURACAO_PROJ', 'EX_CONFIGURACAO_PROJ', 'PRONTO_PROJ',
                  'AG_ESTOQUE', 'AG_REPARO_PROJ'];

    var vista = { tela: 'fila', numero: null, etapa: '' };

    window.SPARE_MODULES.projetos = {
        render: function (container, sub) {
            S = window.SPARE;
            vista.tela = sub === 'projetos' ? 'projetos' : 'fila';
            vista.numero = null;
            S.tabs(ABAS, vista.tela, 'projetos');
            desenhar(container);
        }
    };

    function desenhar(c) {
        if (vista.tela === 'novo') return telaNovo(c);
        if (vista.tela === 'detalhe') return telaDetalhe(c);
        if (vista.tela === 'projetos') return telaProjetos(c);
        return telaFila(c);
    }

    function duracao(seg) {
        if (!seg && seg !== 0) return '—';
        var h = Math.floor(seg / 3600), m = Math.floor((seg % 3600) / 60);
        if (h >= 24) return Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
        if (h) return h + 'h ' + m + 'm';
        return m + 'm';
    }

    function data(iso) {
        if (!iso) return '—';
        return new Date(iso.length === 10 ? iso + 'T00:00:00' : iso)
            .toLocaleDateString('pt-BR');
    }

    function erro(c, e) {
        c.innerHTML = '';
        c.appendChild(S.el('div', { className: 'alert alert-danger',
                                    textContent: e.message || String(e) }));
    }

    function carregando(c, texto) {
        c.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> ' +
            S.esc(texto || 'Carregando...') + '</div>';
    }

    function selo(estado) {
        return ({
            AG_DEFINICAO: 'badge-default', AG_SEPARACAO_PROJ: 'badge-gold',
            EX_SEPARACAO_PROJ: 'badge-teal', AG_CONFIGURACAO_PROJ: 'badge-gold',
            EX_CONFIGURACAO_PROJ: 'badge-teal', PRONTO_PROJ: 'badge-success',
            AG_ESTOQUE: 'badge-warning', AG_REPARO_PROJ: 'badge-warning',
            ENVIADO_PROJ: 'badge-info', CANCELADO_PROJ: 'badge-default',
            PLANEJAMENTO: 'badge-default', EM_ANDAMENTO: 'badge-teal',
            CONCLUIDO: 'badge-success', CANCELADO: 'badge-default'
        })[estado] || 'badge-default';
    }

    /* ============================================================
       Tela 1 — a fila por etapa
       ============================================================ */
    async function telaFila(c) {
        carregando(c);
        var d;
        try {
            d = await S.api('/projetos/fila' + (vista.etapa ? '?estado=' + vista.etapa : ''));
        } catch (e) { return erro(c, e); }

        c.innerHTML = '';
        c.appendChild(cabecalho('Fila por etapa',
            'Itens de todos os projetos, do mais urgente ao mais folgado.',
            [botao('Novo projeto', 'btn-primary', function () {
                vista.tela = 'novo'; desenhar(c);
            })]));

        var n = d.contagem || {};
        c.appendChild(indicadores([
            ['Para separar', (n.AG_SEPARACAO_PROJ || 0) + (n.EX_SEPARACAO_PROJ || 0), 'accent-gold'],
            ['Para configurar', (n.AG_CONFIGURACAO_PROJ || 0) + (n.EX_CONFIGURACAO_PROJ || 0), 'accent-teal'],
            ['Prontos para envio', n.PRONTO_PROJ || 0, 'accent-green'],
            ['Fora do prazo', d.atrasados || 0, 'accent-orange']
        ]));

        /* Filtro de etapa: chips, um por estado com item. */
        var chips = S.el('div', { className: 'btn-row mb-3' });
        chips.appendChild(chip('Todas', '', c));
        ETAPAS.forEach(function (e) {
            if (n[e]) chips.appendChild(chip(d.rotulos[e] + ' · ' + n[e], e, c));
        });
        c.appendChild(chips);

        var corpo = S.el('div', { className: 'card-body' });
        if (!d.itens.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio',
                textContent: 'Nenhum item nesta etapa.' }));
        } else {
            corpo.appendChild(tabelaFila(d.itens, c));
        }
        c.appendChild(cartao('Itens em andamento', corpo));
    }

    function chip(texto, etapa, c) {
        var ativo = vista.etapa === etapa;
        return botao(texto, ativo ? 'btn-secondary btn-sm' : 'btn-outline btn-sm',
            function () { vista.etapa = etapa; desenhar(c); });
    }

    function tabelaFila(linhas, c) {
        var t = S.el('table', { className: 'data-table' });
        t.innerHTML = '<thead><tr>' +
            ['Projeto', 'Loja', 'Equipamento', 'Qtd', 'Etapa', 'Prazo', 'Em curso', '']
                .map(function (x) { return '<th>' + S.esc(x) + '</th>'; }).join('') +
            '</tr></thead>';
        var tbody = S.el('tbody');
        linhas.forEach(function (i) {
            var tr = S.el('tr');
            tr.innerHTML =
                '<td><b>' + S.esc(i.projeto) + '</b></td>' +
                '<td>' + S.esc(i.loja || '—') + '</td>' +
                '<td>' + S.esc(i.modelo) +
                    (i.area ? ' <span class="text-muted">· ' + S.esc(i.area) + '</span>' : '') +
                '</td>' +
                '<td>' + i.separados + '/' + i.quantidade + '</td>' +
                '<td><span class="badge ' + selo(i.estado) + '">' +
                    S.esc(i.estado_rotulo) + '</span></td>' +
                '<td>' + S.esc(data(i.prazo)) +
                    (i.atrasado ? ' <span class="badge badge-danger">atrasado</span>' : '') +
                '</td>' +
                '<td>' + S.esc(duracao(i.segundos_uteis)) + '</td>';
            var td = S.el('td');
            td.appendChild(botao('Abrir', 'btn-outline btn-sm', function () {
                vista.tela = 'detalhe'; vista.numero = i.projeto; desenhar(c);
            }));
            tr.appendChild(td);
            tbody.appendChild(tr);
        });
        t.appendChild(tbody);
        var wrap = S.el('div', { className: 'table-wrapper' });
        wrap.appendChild(t);
        return wrap;
    }

    /* ============================================================
       Tela 2 — os projetos
       ============================================================ */
    async function telaProjetos(c) {
        carregando(c);
        var d;
        try {
            d = await S.api('/projetos/projetos');
        } catch (e) { return erro(c, e); }

        c.innerHTML = '';
        c.appendChild(cabecalho('Projetos de loja',
            'Inaugurações e reformas em andamento.',
            [botao('Novo projeto', 'btn-primary', function () {
                vista.tela = 'novo'; desenhar(c);
            })]));

        var n = d.contagem || {};
        c.appendChild(indicadores([
            ['Em planejamento', n.PLANEJAMENTO || 0, 'accent-gold'],
            ['Em andamento', n.EM_ANDAMENTO || 0, 'accent-teal'],
            ['Concluídos', n.CONCLUIDO || 0, 'accent-green'],
            ['Com item atrasado', d.em_risco || 0, 'accent-orange']
        ]));

        var corpo = S.el('div', { className: 'card-body' });
        if (!d.projetos.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio',
                textContent: 'Nenhum projeto aberto ainda.' }));
        } else {
            var t = S.el('table', { className: 'data-table' });
            t.innerHTML = '<thead><tr>' +
                ['Projeto', 'Loja', 'Tipo', 'Abertura', 'Itens', 'Equipamentos',
                 'Situação', ''].map(function (x) {
                    return '<th>' + S.esc(x) + '</th>';
                }).join('') + '</tr></thead>';
            var tbody = S.el('tbody');
            d.projetos.forEach(function (p) {
                var tr = S.el('tr');
                tr.innerHTML =
                    '<td><b>' + S.esc(p.numero) + '</b></td>' +
                    '<td>' + S.esc(p.loja || '—') + '</td>' +
                    '<td>' + S.esc(p.tipo_rotulo) + '</td>' +
                    '<td>' + S.esc(data(p.data_prevista)) + '</td>' +
                    '<td>' + (p.itens_total - p.itens_pendentes) + '/' + p.itens_total +
                        (p.itens_pausados ? ' <span class="text-muted">· ' +
                            p.itens_pausados + ' em pausa</span>' : '') +
                        (p.itens_atrasados ? ' <span class="badge badge-danger">' +
                            p.itens_atrasados + ' atrasado(s)</span>' : '') +
                    '</td>' +
                    '<td>' + p.separados + '/' + p.equipamentos + '</td>' +
                    '<td><span class="badge ' + selo(p.estado) + '">' +
                        S.esc(p.estado_rotulo) + '</span></td>';
                var td = S.el('td');
                td.appendChild(botao('Abrir', 'btn-outline btn-sm', function () {
                    vista.tela = 'detalhe'; vista.numero = p.numero; desenhar(c);
                }));
                tr.appendChild(td);
                tbody.appendChild(tr);
            });
            t.appendChild(tbody);
            var wrap = S.el('div', { className: 'table-wrapper' });
            wrap.appendChild(t);
            corpo.appendChild(wrap);
        }
        c.appendChild(cartao('Projetos', corpo));
    }

    /* ============================================================
       Tela 3 — novo projeto
       ============================================================ */
    function telaNovo(c) {
        c.innerHTML = '';
        c.appendChild(cabecalho('Novo projeto de loja',
            'O escopo pode entrar agora ou depois, item por item.',
            [botao('Cancelar', 'btn-outline', function () {
                vista.tela = 'projetos'; desenhar(c);
            })]));

        var layout = S.el('div', { className: 'sep-layout' });
        var esquerda = S.el('div'), direita = S.el('div');
        layout.appendChild(esquerda);
        layout.appendChild(direita);
        c.appendChild(layout);

        var chamado = null;
        var itens = [];

        var corpoCh = S.el('div', { className: 'card-body' });
        corpoCh.innerHTML =
            '<div class="form-grid cols-2">' +
              '<div class="form-group"><label for="prj-chamado">Requisição ou incidente</label>' +
                '<input id="prj-chamado" class="form-control" ' +
                       'placeholder="RITM0000000 ou INC0000000" autocomplete="off"></div>' +
              '<div class="form-group"><label for="prj-tipo">Tipo</label>' +
                '<select id="prj-tipo" class="form-control">' +
                  '<option value="INAUGURACAO">Inauguração</option>' +
                  '<option value="REFORMA">Reforma</option></select></div>' +
              '<div class="form-group"><label for="prj-data">Abertura da loja</label>' +
                '<input id="prj-data" type="date" class="form-control"></div>' +
              '<div class="form-group"><label for="prj-resp">Responsável</label>' +
                '<input id="prj-resp" class="form-control" placeholder="Quem responde pelo projeto"></div>' +
            '</div>' +
            '<div id="prj-chamado-info" class="mt-2"></div>';
        esquerda.appendChild(cartao('Projeto', corpoCh));

        var corpoIt = S.el('div', { className: 'card-body' });
        corpoIt.innerHTML =
            '<div class="form-grid cols-2">' +
              '<div class="form-group"><label for="prj-modelo">Modelo</label>' +
                '<input id="prj-modelo" class="form-control" list="prj-modelos" ' +
                       'placeholder="Como está no ServiceNow" autocomplete="off">' +
                '<datalist id="prj-modelos"></datalist></div>' +
              '<div class="form-group"><label for="prj-qtd">Quantidade</label>' +
                '<input id="prj-qtd" type="number" min="1" value="1" class="form-control"></div>' +
              '<div class="form-group"><label for="prj-area">Área da loja</label>' +
                '<input id="prj-area" class="form-control" placeholder="Frente, retaguarda, provador…"></div>' +
            '</div>';
        var addBtn = botao('Incluir item', 'btn-secondary', function () {
            var modelo = corpoIt.querySelector('#prj-modelo').value.trim();
            var qtd = parseInt(corpoIt.querySelector('#prj-qtd').value, 10) || 0;
            if (!modelo || qtd <= 0) { S.toast('Informe modelo e quantidade.', 'warning'); return; }
            itens.push({ modelo: modelo, quantidade: qtd,
                         area: corpoIt.querySelector('#prj-area').value.trim() });
            corpoIt.querySelector('#prj-modelo').value = '';
            corpoIt.querySelector('#prj-qtd').value = '1';
            pintarItens();
        });
        corpoIt.appendChild(addBtn);
        esquerda.appendChild(cartao('Escopo (opcional agora)', corpoIt));

        /* Sugestão de modelos a partir do estoque de inauguração. Só
           ajuda a digitar igual ao ServiceNow; não limita o escopo, que
           pode pedir modelo que ainda nem foi comprado. */
        S.api('/projetos/catalogo').then(function (cat) {
            var dl = corpoIt.querySelector('#prj-modelos');
            (cat.itens || []).forEach(function (i) {
                dl.appendChild(S.el('option', { value: i.modelo }));
            });
        }).catch(function () { /* sem sugestão; segue */ });

        function pintarItens() {
            direita.innerHTML = '';
            var corpo = S.el('div', { className: 'card-body' });
            if (!itens.length) {
                corpo.appendChild(S.el('p', { className: 'sep-vazio',
                    textContent: 'Nenhum item ainda. Pode abrir o projeto vazio e ' +
                                 'incluir o escopo depois.' }));
            } else {
                itens.forEach(function (i, k) {
                    var linha = S.el('div', { className: 'sep-linha' });
                    linha.innerHTML = '<div><div style="font-size:13px">' + S.esc(i.modelo) +
                        '</div><div class="text-muted" style="font-size:11.5px">' +
                        i.quantidade + ' un.' + (i.area ? ' · ' + S.esc(i.area) : '') +
                        '</div></div>';
                    linha.appendChild(botao('Remover', 'btn-outline btn-sm', function () {
                        itens.splice(k, 1); pintarItens();
                    }));
                    corpo.appendChild(linha);
                });
            }
            var rodape = S.el('div', { className: 'card-footer' });
            var abrir = botao('Abrir projeto', 'btn-primary', gravar);
            if (!chamado) abrir.disabled = true;
            rodape.appendChild(abrir);
            var card = cartao('Itens do projeto', corpo);
            card.appendChild(rodape);
            direita.appendChild(card);
        }
        pintarItens();

        var campo = corpoCh.querySelector('#prj-chamado');
        var info = corpoCh.querySelector('#prj-chamado-info');
        async function validar() {
            var numero = campo.value.trim().toUpperCase();
            if (!numero) return;
            info.innerHTML = '<span class="text-muted">Consultando o ServiceNow...</span>';
            try {
                chamado = await S.api('/separacao/chamado/' + encodeURIComponent(numero));
                info.innerHTML = '<div class="detail-grid">' +
                    '<div><label style="font-size:12px;color:var(--text-secondary)">Loja</label><div>' +
                        S.esc(chamado.destino || '—') + '</div></div>' +
                    '<div><label style="font-size:12px;color:var(--text-secondary)">Resumo</label><div>' +
                        S.esc(chamado.resumo || '—') + '</div></div></div>';
            } catch (e) {
                chamado = null;
                info.innerHTML = '';
                info.appendChild(S.el('div', { className: 'alert alert-danger',
                                               textContent: e.message }));
            }
            pintarItens();
        }
        campo.addEventListener('blur', validar);
        campo.addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter') { ev.preventDefault(); validar(); }
        });
        campo.focus();

        async function gravar() {
            try {
                S.loading(true);
                var d = await S.api('/projetos/projetos', {
                    method: 'POST',
                    body: {
                        chamado: chamado.chamado,
                        tipo: corpoCh.querySelector('#prj-tipo').value,
                        data_prevista: corpoCh.querySelector('#prj-data').value || null,
                        responsavel: corpoCh.querySelector('#prj-resp').value.trim(),
                        itens: itens
                    }
                });
                S.toast('Projeto ' + d.numero + ' aberto.', 'success');
                vista.tela = 'detalhe'; vista.numero = d.numero; desenhar(c);
            } catch (e) {
                S.toast(e.message, 'danger');
            } finally {
                S.loading(false);
            }
        }
    }

    /* ============================================================
       Tela 4 — o projeto, item por item
       ============================================================ */
    async function telaDetalhe(c) {
        carregando(c);
        var d;
        try {
            d = await S.api('/projetos/projetos/' + encodeURIComponent(vista.numero));
        } catch (e) { return erro(c, e); }

        c.innerHTML = '';
        var acoes = [botao('Voltar', 'btn-outline', function () {
            vista.tela = 'fila'; desenhar(c);
        })];
        var aberto = d.estado === 'PLANEJAMENTO' || d.estado === 'EM_ANDAMENTO';
        if (aberto) {
            acoes.push(botao('Incluir item', 'btn-secondary', function () {
                modalIncluirItem(d, c);
            }));
            acoes.push(botao('Cancelar projeto', 'btn-outline', function () {
                modalMotivo('Cancelar projeto', 'Motivo do cancelamento', function (motivo) {
                    return acaoProjeto(c, d.numero, 'cancelar', { motivo: motivo });
                });
            }));
        }

        c.appendChild(cabecalho(d.loja || d.numero,
            d.numero + ' · ' + d.tipo_rotulo + ' · abertura ' + data(d.data_prevista) +
            ' · chamado ' + d.chamado, acoes));

        c.appendChild(indicadores([
            ['Itens pendentes', d.itens_pendentes + '/' + d.itens_total, 'accent-gold'],
            ['Em pausa', d.itens_pausados, 'accent-teal'],
            ['Equipamentos separados', d.separados + '/' + d.equipamentos, 'accent-green'],
            ['Atrasados', d.itens_atrasados, 'accent-orange']
        ]));

        var corpo = S.el('div', { className: 'card-body' });
        if (!d.itens.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio',
                textContent: 'O projeto ainda não tem escopo. Inclua os itens.' }));
        }
        d.itens.forEach(function (i) { corpo.appendChild(blocoItem(i, d, c)); });
        c.appendChild(cartao('Itens do projeto', corpo));
    }

    /* Um item: cabeçalho com estado e prazo, as séries, e a ação certa
       para o estado — uma de cada vez, nunca um menu com tudo. */
    function blocoItem(i, d, c) {
        var bloco = S.el('div', { className: 'prj-item' + (i.pausado ? ' prj-item-pausa' : '') });

        var topo = S.el('div', { className: 'prj-item-topo' });
        topo.innerHTML =
            '<div><b>' + S.esc(i.modelo) + '</b>' +
                (i.area ? ' <span class="text-muted">· ' + S.esc(i.area) + '</span>' : '') +
                '<div class="text-muted" style="font-size:11.5px">' + S.esc(i.token) +
                ' · ' + i.separados + '/' + i.quantidade + ' separados · ' +
                S.esc(duracao(i.segundos_uteis)) + ' em curso' +
                (i.responsavel ? ' · com ' + S.esc(i.responsavel) : '') + '</div></div>' +
            '<div><span class="badge ' + selo(i.estado) + '">' + S.esc(i.estado_rotulo) +
                '</span>' + (i.atrasado ? ' <span class="badge badge-danger">atrasado</span>' : '') +
                '<div class="text-muted" style="font-size:11.5px;text-align:right">prazo ' +
                S.esc(data(i.prazo)) + '</div></div>';
        bloco.appendChild(topo);

        if (i.series.length || i.devolvidas.length) {
            var lista = S.el('div', { className: 'prj-series' });
            i.series.forEach(function (u) {
                var s = S.el('span', { className: 'sep-serie', textContent: u.serial });
                lista.appendChild(s);
            });
            i.devolvidas.forEach(function (u) {
                lista.appendChild(S.el('span', {
                    className: 'sep-serie prj-devolvida',
                    title: u.motivo, textContent: u.serial + ' (reprovada)'
                }));
            });
            bloco.appendChild(lista);
        }

        if (i.observacao) {
            bloco.appendChild(S.el('p', { className: 'text-muted prj-obs',
                                          textContent: i.observacao }));
        }

        if (!i.encerrado) bloco.appendChild(acoesItem(i, d, c));
        return bloco;
    }

    function acoesItem(i, d, c) {
        var linha = S.el('div', { className: 'btn-row prj-acoes' });
        var num = d.numero;
        function a(rotulo, classe, qual, body) {
            linha.appendChild(botao(rotulo, classe, function () {
                acaoItem(c, num, i.id, qual, body);
            }));
        }
        function m(rotulo, classe, titulo, campo, qual) {
            linha.appendChild(botao(rotulo, classe, function () {
                modalMotivo(titulo, campo, function (motivo) {
                    return acaoItem(c, num, i.id, qual, { motivo: motivo });
                });
            }));
        }

        switch (i.estado) {
        case 'AG_DEFINICAO':
            a('Definir escopo', 'btn-primary', 'definir');
            break;
        case 'AG_SEPARACAO_PROJ':
            a('Assumir separação', 'btn-primary', 'separar');
            m('Sem estoque', 'btn-outline', 'Sem estoque', 'O que falta', 'sem-estoque');
            break;
        case 'EX_SEPARACAO_PROJ':
            linha.appendChild(barraBipe(i, d, c));
            var concluir = botao('Concluir separação', 'btn-primary', function () {
                acaoItem(c, num, i.id, 'concluir-separacao');
            });
            if (i.separados < i.quantidade) concluir.disabled = true;
            linha.appendChild(concluir);
            m('Sem estoque', 'btn-outline', 'Sem estoque', 'O que falta', 'sem-estoque');
            break;
        case 'AG_ESTOQUE':
        case 'AG_REPARO_PROJ':
            a('Retomar', 'btn-primary', 'retomar');
            break;
        case 'AG_CONFIGURACAO_PROJ':
            a('Assumir configuração', 'btn-primary', 'configurar');
            break;
        case 'EX_CONFIGURACAO_PROJ':
            a('Concluir configuração', 'btn-primary', 'concluir-configuracao');
            linha.appendChild(botao('Reprovar unidade', 'btn-outline', function () {
                modalReprovar(i, d, c);
            }));
            break;
        case 'PRONTO_PROJ':
            a('Registrar envio', 'btn-primary', 'enviar');
            break;
        }
        m('Cancelar item', 'btn-outline btn-sm', 'Cancelar item', 'Motivo', 'cancelar');
        return linha;
    }

    function barraBipe(i, d, c) {
        var caixa = S.el('div', { className: 'form-row-inline' });
        var serie = S.el('input', {
            className: 'form-control form-control-inline',
            style: 'min-width:220px',
            placeholder: 'Bipe a série (' + (i.quantidade - i.separados) + ' restante)',
            autocomplete: 'off'
        });
        async function bipar() {
            var valor = serie.value.trim();
            if (!valor) return;
            try {
                await S.api('/projetos/projetos/' + encodeURIComponent(d.numero) +
                            '/itens/' + i.id + '/bipar',
                            { method: 'POST', body: { serial: valor } });
                S.toast('Série ' + valor.toUpperCase() + ' reservada.', 'success');
                desenhar(c);
            } catch (e) {
                S.toast(e.message, 'danger');
                serie.select();
            }
        }
        serie.addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter') { ev.preventDefault(); bipar(); }
        });
        caixa.appendChild(serie);
        caixa.appendChild(botao('Confirmar', 'btn-secondary', bipar));
        return caixa;
    }

    function modalReprovar(i, d, c) {
        var corpo = S.el('div');
        var sel = S.el('select', { className: 'form-control mb-2' });
        i.series.forEach(function (u) {
            sel.appendChild(S.el('option', { value: u.serial, textContent: u.serial }));
        });
        var motivo = S.el('textarea', { className: 'form-control', rows: '3',
                                        placeholder: 'O que reprovou' });
        corpo.appendChild(S.el('label', { textContent: 'Série' }));
        corpo.appendChild(sel);
        corpo.appendChild(S.el('label', { textContent: 'Motivo' }));
        corpo.appendChild(motivo);
        S.openModal('Reprovar unidade', corpo, rodapeModal('Reprovar', function () {
            if (!motivo.value.trim()) { S.toast('Informe o motivo.', 'warning'); return; }
            S.closeModal();
            acaoItem(c, d.numero, i.id, 'reprovar',
                     { serial: sel.value, motivo: motivo.value.trim() });
        }));
    }

    function modalIncluirItem(d, c) {
        var corpo = S.el('div');
        corpo.innerHTML =
            '<div class="form-group"><label>Modelo</label>' +
              '<input id="prj-m-modelo" class="form-control" autocomplete="off"></div>' +
            '<div class="form-group"><label>Quantidade</label>' +
              '<input id="prj-m-qtd" type="number" min="1" value="1" class="form-control"></div>' +
            '<div class="form-group"><label>Área da loja</label>' +
              '<input id="prj-m-area" class="form-control"></div>';
        S.openModal('Incluir item', corpo, rodapeModal('Incluir', function () {
            var modelo = corpo.querySelector('#prj-m-modelo').value.trim();
            var qtd = parseInt(corpo.querySelector('#prj-m-qtd').value, 10) || 0;
            if (!modelo || qtd <= 0) { S.toast('Informe modelo e quantidade.', 'warning'); return; }
            S.closeModal();
            acaoProjeto(c, d.numero, 'itens', {
                modelo: modelo, quantidade: qtd,
                area: corpo.querySelector('#prj-m-area').value.trim()
            });
        }));
        setTimeout(function () { corpo.querySelector('#prj-m-modelo').focus(); }, 50);
    }

    function modalMotivo(titulo, rotulo, aoConfirmar) {
        var corpo = S.el('div');
        var campo = S.el('textarea', { className: 'form-control', rows: '3' });
        corpo.appendChild(S.el('label', { textContent: rotulo }));
        corpo.appendChild(campo);
        S.openModal(titulo, corpo, rodapeModal('Confirmar', function () {
            if (!campo.value.trim()) { S.toast('Informe o motivo.', 'warning'); return; }
            S.closeModal();
            aoConfirmar(campo.value.trim());
        }));
        setTimeout(function () { campo.focus(); }, 50);
    }

    /* openModal recebe botões de verdade, não descrições. */
    function rodapeModal(texto, aoConfirmar) {
        return [
            botao('Voltar', 'btn-outline', function () { S.closeModal(); }),
            botao(texto, 'btn-primary', aoConfirmar)
        ];
    }

    async function acaoItem(c, numero, itemId, qual, body) {
        try {
            S.loading(true);
            await S.api('/projetos/projetos/' + encodeURIComponent(numero) +
                        '/itens/' + itemId + '/' + qual,
                        { method: 'POST', body: body || undefined });
            S.toast(({
                definir: 'Escopo definido: item na fila de separação.',
                separar: 'Separação assumida.',
                'concluir-separacao': 'Separação concluída: item na fila de configuração.',
                'sem-estoque': 'Item em espera de estoque. O relógio não conta contra a fila.',
                retomar: 'Item de volta à fila de separação.',
                configurar: 'Configuração assumida.',
                'concluir-configuracao': 'Item pronto para envio.',
                reprovar: 'Unidade devolvida à bancada. Item aguarda reparo.',
                enviar: 'Envio registrado: equipamentos alocados na loja.',
                cancelar: 'Item cancelado.'
            })[qual] || 'Feito.', 'success');
            vista.tela = 'detalhe'; vista.numero = numero; desenhar(c);
        } catch (e) {
            S.toast(e.message, 'danger');
        } finally {
            S.loading(false);
        }
    }

    async function acaoProjeto(c, numero, qual, body) {
        try {
            S.loading(true);
            await S.api('/projetos/projetos/' + encodeURIComponent(numero) + '/' + qual,
                        { method: 'POST', body: body });
            S.toast(qual === 'cancelar' ? 'Projeto cancelado.' : 'Item incluído.', 'success');
            vista.tela = qual === 'cancelar' ? 'projetos' : 'detalhe';
            desenhar(c);
        } catch (e) {
            S.toast(e.message, 'danger');
        } finally {
            S.loading(false);
        }
    }

    /* ============================================================
       Peças reaproveitadas
       ============================================================ */
    function cabecalho(titulo, sub, acoes) {
        var topo = S.el('div', {
            style: 'display:flex;align-items:flex-end;justify-content:space-between;' +
                   'gap:16px;flex-wrap:wrap;margin-bottom:18px'
        });
        var texto = S.el('div');
        texto.appendChild(S.el('h2', { className: 'page-title',
                                       style: 'margin:0 0 4px', textContent: titulo }));
        if (sub) {
            texto.appendChild(S.el('p', { className: 'text-muted',
                style: 'margin:0;font-size:13px', textContent: sub }));
        }
        topo.appendChild(texto);
        var caixa = S.el('div', { className: 'btn-row' });
        (acoes || []).forEach(function (b) { caixa.appendChild(b); });
        topo.appendChild(caixa);
        return topo;
    }

    function indicadores(lista) {
        var grade = S.el('div', { className: 'stats-grid mb-3' });
        lista.forEach(function (x) {
            var card = S.el('div', { className: 'stat-card ' + x[2] });
            card.innerHTML = '<div class="stat-value">' + x[1] + '</div>' +
                '<div class="stat-label">' + S.esc(x[0]) + '</div>';
            grade.appendChild(card);
        });
        return grade;
    }

    function cartao(titulo, corpo) {
        var card = S.el('div', { className: 'card mb-3' });
        var cab = S.el('div', { className: 'card-header' });
        if (typeof titulo === 'string') cab.textContent = titulo;
        else cab.appendChild(titulo);
        card.appendChild(cab);
        card.appendChild(corpo);
        return card;
    }

    function botao(texto, classe, aoClicar) {
        return S.el('button', { className: 'btn ' + classe, type: 'button',
                                textContent: texto, onClick: aoClicar });
    }
})();
