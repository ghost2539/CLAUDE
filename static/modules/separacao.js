/* ================================================================
   Módulo: Separação (A15)

   Três abas, uma por tipo de atendimento. Cada uma enxerga um estoque
   e nada além dele — quem decide é o começo do espaço e do corredor
   (REP / IN), configurado em Parâmetros.

   A tela tem três estados, e nunca dois ao mesmo tempo:

     fila     a lista do que está para separar
     nova     abrir uma solicitação, começando pelo chamado
     detalhe  a bancada: bipar série por série e fechar

   O chamado vem primeiro no formulário de propósito. É dele que sai o
   destino, e sem destino não há o que separar — pedir modelo antes
   seria montar um carrinho que pode não ter para onde ir.
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};

(function () {
    var S = null;
    var TIPOS = [
        ['FRENTE_RETAGUARDA', 'Frente e Retaguarda'],
        ['MOBILIDADE', 'Mobilidade'],
        ['INAUGURACAO_REFORMA', 'Inauguração e Reforma']
    ];
    var ROTA = { frente: 'FRENTE_RETAGUARDA', mobilidade: 'MOBILIDADE',
                 inauguracao: 'INAUGURACAO_REFORMA' };
    var SUB_DE_TIPO = { FRENTE_RETAGUARDA: 'frente', MOBILIDADE: 'mobilidade',
                        INAUGURACAO_REFORMA: 'inauguracao' };

    /* Estado da tela. Só o suficiente para não reconsultar à toa. */
    var vista = { tipo: 'FRENTE_RETAGUARDA', tela: 'fila', numero: null,
                  catalogo: null, escolhidos: [] };

    window.SPARE_MODULES.separacao = {
        render: function (container, sub) {
            S = window.SPARE;
            vista.tipo = ROTA[sub] || 'FRENTE_RETAGUARDA';
            vista.tela = 'fila';
            vista.numero = null;
            vista.catalogo = null;
            vista.escolhidos = [];
            S.tabs(TIPOS.map(function (t) { return [SUB_DE_TIPO[t[0]], t[1]]; }),
                   SUB_DE_TIPO[vista.tipo], 'separacao');
            desenhar(container);
        }
    };

    function desenhar(c) {
        if (vista.tela === 'nova') return telaNova(c);
        if (vista.tela === 'detalhe') return telaDetalhe(c);
        return telaFila(c);
    }

    function rotulo(tipo) {
        var achado = TIPOS.filter(function (t) { return t[0] === tipo; })[0];
        return achado ? achado[1] : tipo;
    }

    /* Tempo em texto curto: "3h 20m". Segundo não interessa a ninguém
       nesta tela, e minuto sozinho fica ilegível acima de um dia. */
    function duracao(seg) {
        if (!seg && seg !== 0) return '—';
        var h = Math.floor(seg / 3600), m = Math.floor((seg % 3600) / 60);
        if (h >= 24) return Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
        if (h) return h + 'h ' + m + 'm';
        return m + 'm';
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

    /* ============================================================
       Tela 1 — a fila
       ============================================================ */
    async function telaFila(c) {
        carregando(c);
        var d;
        try {
            d = await S.api('/separacao/solicitacoes?tipo=' + vista.tipo);
        } catch (e) { return erro(c, e); }

        c.innerHTML = '';
        c.appendChild(cabecalho(rotulo(vista.tipo), '',
            [botao('Nova solicitação', 'btn-primary', function () {
                vista.tela = 'nova'; desenhar(c);
            })]));

        var n = d.contagem || {};
        c.appendChild(indicadores([
            ['Aguardando separação', n.AG_SEPARACAO || 0, 'accent-gold'],
            ['Em separação', n.EX_SEPARACAO || 0, 'accent-teal'],
            ['Separadas', n.SEPARADA || 0, 'accent-green'],
            ['Fora do prazo', d.atrasadas || 0, 'accent-orange']
        ]));

        var corpo = S.el('div', { className: 'card-body' });
        if (!d.solicitacoes.length) {
            corpo.appendChild(S.el('p', {
                className: 'sep-vazio',
                textContent: 'Nenhuma solicitação.'
            }));
        } else {
            corpo.appendChild(tabelaFila(d.solicitacoes, c));
        }
        c.appendChild(cartao('Fila de separação', corpo));
    }

    function tabelaFila(linhas, c) {
        var thead = '<thead><tr>' +
            ['Solicitação', 'Chamado', 'Destino', 'Aberta por', 'Em fila',
             'Situação', ''].map(function (t) {
                return '<th>' + S.esc(t) + '</th>';
            }).join('') + '</tr></thead>';

        var tbody = S.el('tbody');
        linhas.forEach(function (p) {
            var tr = S.el('tr');
            tr.innerHTML =
                '<td><b>' + S.esc(p.numero) + '</b></td>' +
                '<td>' + S.esc(p.chamado) + '</td>' +
                '<td>' + S.esc(p.destino || '—') + '</td>' +
                '<td>' + S.esc(p.aberta_por) + '</td>' +
                '<td>' + S.esc(duracao(p.segundos_uteis)) +
                    (p.atrasada ? ' <span class="badge badge-danger">atrasada</span>' : '') +
                '</td>' +
                '<td><span class="badge ' + selo(p.estado) + '">' +
                    S.esc(p.estado_rotulo) + '</span></td>';
            var td = S.el('td');
            td.appendChild(botao('Abrir', 'btn-outline btn-sm', function () {
                vista.tela = 'detalhe'; vista.numero = p.numero; desenhar(c);
            }));
            tr.appendChild(td);
            tbody.appendChild(tr);
        });

        var tabela = S.el('table', { className: 'data-table' });
        tabela.innerHTML = thead;
        tabela.appendChild(tbody);
        var wrap = S.el('div', { className: 'table-wrapper' });
        wrap.appendChild(tabela);
        return wrap;
    }

    function selo(estado) {
        return ({
            AG_SEPARACAO: 'badge-gold', EX_SEPARACAO: 'badge-teal',
            SEPARADA: 'badge-success', ENVIADA: 'badge-info',
            CANCELADA: 'badge-default'
        })[estado] || 'badge-default';
    }

    /* ============================================================
       Tela 2 — nova solicitação
       ============================================================ */
    function telaNova(c) {
        c.innerHTML = '';
        c.appendChild(cabecalho('Nova solicitação de separação', rotulo(vista.tipo),
            [botao('Cancelar', 'btn-outline', function () {
                vista.tela = 'fila'; desenhar(c);
            })]));

        var layout = S.el('div', { className: 'sep-layout' });
        var esquerda = S.el('div');
        var direita = S.el('div');
        layout.appendChild(esquerda);
        layout.appendChild(direita);
        c.appendChild(layout);

        /* Bloco do chamado: primeiro e sozinho até ser válido. */
        var corpoCh = S.el('div', { className: 'card-body' });
        corpoCh.innerHTML =
            '<div class="form-row-inline">' +
              '<div class="form-group" style="flex:1;min-width:220px">' +
                '<label for="sep-chamado">Incidente ou requisição</label>' +
                '<input id="sep-chamado" class="form-control" ' +
                       'placeholder="INC0000000 ou RITM0000000" autocomplete="off">' +
              '</div>' +
            '</div>' +
            '<div id="sep-chamado-info" class="mt-2"></div>';
        esquerda.appendChild(cartao('Chamado de origem', corpoCh));

        var alvoCatalogo = S.el('div');
        esquerda.appendChild(alvoCatalogo);

        vista.escolhidos = [];
        vista.chamado = null;
        desenharPedido(direita, c);

        var campo = corpoCh.querySelector('#sep-chamado');
        var info = corpoCh.querySelector('#sep-chamado-info');
        var validando = false;

        async function validar() {
            var numero = campo.value.trim().toUpperCase();
            if (!numero || validando) return;
            validando = true;
            info.innerHTML = '<span class="text-muted">Consultando o ServiceNow...</span>';
            try {
                var ch = await S.api('/separacao/chamado/' + encodeURIComponent(numero));
                vista.chamado = ch;
                info.innerHTML =
                    '<div class="detail-grid">' +
                      campoLeitura('Destino', ch.destino || '—') +
                      campoLeitura('Resumo', ch.resumo || '—') +
                    '</div>';
                if (!ch.destino) {
                    info.appendChild(S.el('div', {
                        className: 'alert alert-warning mt-2',
                        textContent: 'O chamado não traz o local da loja. Corrija o ' +
                                     'chamado no ServiceNow antes de separar.'
                    }));
                }
                await mostrarCatalogo(alvoCatalogo, direita, c);
            } catch (e) {
                vista.chamado = null;
                alvoCatalogo.innerHTML = '';
                info.innerHTML = '';
                info.appendChild(S.el('div', { className: 'alert alert-danger',
                                               textContent: e.message }));
            } finally {
                validando = false;
                desenharPedido(direita, c);
            }
        }

        campo.addEventListener('blur', validar);
        campo.addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter') { ev.preventDefault(); validar(); }
        });
        campo.focus();
    }

    function campoLeitura(rot, valor) {
        return '<div><label style="font-size:12px;color:var(--text-secondary)">' +
            S.esc(rot) + '</label><div>' + S.esc(valor) + '</div></div>';
    }

    async function mostrarCatalogo(alvo, direita, c) {
        alvo.innerHTML = '';
        var corpo = S.el('div', { className: 'card-body' });
        corpo.innerHTML = '<div class="spinner-inline">' +
            '<span class="spinner spinner-sm"></span> Lendo o estoque...</div>';
        var cab = S.el('span');
        cab.appendChild(document.createTextNode('Catálogo de equipamentos'));
        alvo.appendChild(cartao(cab, corpo));

        try {
            vista.catalogo = await S.api('/separacao/catalogo?tipo=' + vista.tipo);
        } catch (e) {
            corpo.innerHTML = '';
            corpo.appendChild(S.el('div', { className: 'alert alert-danger',
                                            textContent: e.message }));
            return;
        }

        cab.appendChild(S.el('span', {
            className: 'badge badge-teal',
            style: 'margin-left:10px;font-weight:500',
            textContent: 'Estoque ' + vista.catalogo.prefixo
        }));

        corpo.innerHTML = '';
        var busca = S.el('input', {
            className: 'form-control form-control-inline',
            style: 'min-width:260px',
            placeholder: 'Buscar por modelo'
        });
        var barra = S.el('div', { className: 'form-row-inline mb-3' });
        barra.appendChild(busca);
        barra.appendChild(S.el('span', {
            className: 'text-muted',
            textContent: vista.catalogo.total + ' equipamento(s) disponíveis'
        }));
        corpo.appendChild(barra);

        var grade = S.el('div', { className: 'sep-grade' });
        corpo.appendChild(grade);

        function pintar() {
            var termo = busca.value.trim().toLowerCase();
            grade.innerHTML = '';
            var itens = vista.catalogo.itens.filter(function (i) {
                return !termo || i.modelo.toLowerCase().indexOf(termo) >= 0;
            });
            if (!itens.length) {
                grade.appendChild(S.el('p', { className: 'sep-vazio',
                    textContent: 'Nenhum equipamento com esse nome neste estoque.' }));
                return;
            }
            itens.forEach(function (i) {
                grade.appendChild(ficha(i, direita, c, pintar));
            });
        }
        busca.addEventListener('input', pintar);
        pintar();
    }

    function ficha(item, direita, c, repintar) {
        var escolhido = vista.escolhidos.filter(function (e) {
            return e.modelo === item.modelo;
        })[0];
        var b = S.el('button', {
            className: 'sep-ficha',
            type: 'button',
            'aria-pressed': escolhido ? 'true' : 'false',
            onClick: function () {
                if (escolhido) {
                    vista.escolhidos = vista.escolhidos.filter(function (e) {
                        return e.modelo !== item.modelo;
                    });
                } else {
                    vista.escolhidos.push({ modelo: item.modelo, quantidade: 1,
                                            saldo: item.saldo });
                }
                repintar();
                desenharPedido(direita, c);
            }
        });
        b.innerHTML =
            '<div class="sep-ficha-obj" aria-hidden="true">&#x25A3;</div>' +
            '<div class="sep-ficha-nome">' + S.esc(item.modelo) + '</div>' +
            '<div class="sep-ficha-saldo"><span>' +
                S.esc(item.categoria || 'Equipamento') + '</span>' +
                '<b class="' + (item.saldo <= 5 ? 'pouco' : '') + '">' +
                item.saldo + '</b></div>';
        return b;
    }

    /* Coluna da direita: o que já foi escolhido e o botão de enviar. */
    function desenharPedido(direita, c) {
        direita.innerHTML = '';
        var corpo = S.el('div', { className: 'card-body' });

        if (!vista.escolhidos.length) {
            corpo.appendChild(S.el('p', {
                className: 'sep-vazio',
                textContent: vista.chamado
                    ? 'Escolha os equipamentos no catálogo.'
                    : 'Informe o chamado para ver o estoque.'
            }));
        } else {
            vista.escolhidos.forEach(function (e) {
                corpo.appendChild(linhaPedido(e, direita, c));
            });
            var total = vista.escolhidos.reduce(function (a, e) {
                return a + e.quantidade;
            }, 0);
            var somatorio = S.el('div', { className: 'sep-total mt-3' });
            somatorio.innerHTML = '<span>Equipamentos</span><b>' + total + '</b>';
            corpo.appendChild(somatorio);
        }

        var rodape = S.el('div', { className: 'card-footer' });
        var enviar = botao('Enviar solicitação', 'btn-primary', function () {
            gravar(c);
        });
        if (!vista.chamado || !vista.escolhidos.length) enviar.disabled = true;
        rodape.appendChild(enviar);

        var cartaoPedido = cartao('Itens da solicitação', corpo);
        cartaoPedido.appendChild(rodape);
        direita.appendChild(cartaoPedido);
    }

    function linhaPedido(item, direita, c) {
        var linha = S.el('div', { className: 'sep-linha' });
        var nome = S.el('div');
        nome.innerHTML = '<div style="font-size:13px">' + S.esc(item.modelo) + '</div>' +
            '<div class="text-muted" style="font-size:11.5px">' +
            item.saldo + ' em estoque</div>';
        linha.appendChild(nome);

        var qtd = S.el('div', { className: 'sep-qtd' });
        var valor = S.el('span', { textContent: String(item.quantidade) });
        qtd.appendChild(S.el('button', {
            type: 'button', textContent: '−',
            'aria-label': 'Diminuir ' + item.modelo,
            onClick: function () {
                item.quantidade = Math.max(1, item.quantidade - 1);
                desenharPedido(direita, c);
            }
        }));
        qtd.appendChild(valor);
        qtd.appendChild(S.el('button', {
            type: 'button', textContent: '+',
            'aria-label': 'Aumentar ' + item.modelo,
            onClick: function () {
                // O saldo é o teto: pedir mais do que existe só adia a
                // frustração para a hora da bancada.
                if (item.quantidade >= item.saldo) {
                    S.toast('Só há ' + item.saldo + ' em estoque.', 'warning');
                    return;
                }
                item.quantidade += 1;
                desenharPedido(direita, c);
            }
        }));
        linha.appendChild(qtd);
        return linha;
    }

    async function gravar(c) {
        try {
            S.loading(true);
            var d = await S.api('/separacao/solicitacoes', {
                method: 'POST',
                body: {
                    chamado: vista.chamado.chamado,
                    tipo_atendimento: vista.tipo,
                    itens: vista.escolhidos.map(function (e) {
                        return { modelo: e.modelo, quantidade: e.quantidade };
                    })
                }
            });
            S.toast('Solicitação ' + d.numero + ' aberta.', 'success');
            vista.tela = 'detalhe';
            vista.numero = d.numero;
            desenhar(c);
        } catch (e) {
            S.toast(e.message, 'danger');
        } finally {
            S.loading(false);
        }
    }

    /* ============================================================
       Tela 3 — a bancada
       ============================================================ */
    async function telaDetalhe(c) {
        carregando(c);
        var d;
        try {
            d = await S.api('/separacao/solicitacoes/' + encodeURIComponent(vista.numero));
        } catch (e) { return erro(c, e); }

        c.innerHTML = '';
        var acoes = [botao('Voltar à fila', 'btn-outline', function () {
            vista.tela = 'fila'; desenhar(c);
        })];
        if (d.estado === 'AG_SEPARACAO') {
            acoes.push(botao('Assumir separação', 'btn-primary', function () {
                acao(c, 'iniciar');
            }));
        } else if (d.estado === 'EX_SEPARACAO') {
            var concluir = botao('Concluir separação', 'btn-primary', function () {
                acao(c, 'concluir');
            });
            if (d.separados < d.pedidos) concluir.disabled = true;
            acoes.push(concluir);
        } else if (d.estado === 'SEPARADA') {
            acoes.push(botao('Registrar envio', 'btn-primary', function () {
                acao(c, 'enviar');
            }));
        }

        c.appendChild(cabecalho(d.destino || d.numero,
            d.numero + ' · chamado ' + d.chamado + ' · aberta por ' + d.aberta_por +
            ' · ' + duracao(d.segundos_uteis) + ' em curso', acoes));

        var layout = S.el('div', { className: 'sep-layout' });
        var esquerda = S.el('div');
        layout.appendChild(esquerda);

        var corpo = S.el('div', { className: 'card-body' });

        var trilho = S.el('div', { className: 'sep-trilho mb-3' });
        var pct = d.pedidos ? Math.round((d.separados / d.pedidos) * 100) : 0;
        trilho.innerHTML = '<div class="sep-fita" style="width:' + pct + '%"></div>';
        corpo.appendChild(trilho);

        if (d.estado === 'EX_SEPARACAO') {
            corpo.appendChild(barraBipe(d, c));
        }
        corpo.appendChild(tabelaItens(d));
        esquerda.appendChild(cartao(
            'Separação · ' + d.separados + ' de ' + d.pedidos + ' equipamentos', corpo));

        var lateral = S.el('div');
        var infoCorpo = S.el('div', { className: 'card-body' });
        infoCorpo.innerHTML =
            '<div class="sep-total"><span>Situação</span><b>' +
                S.esc(d.estado_rotulo) + '</b></div>' +
            '<div class="sep-total mt-2"><span>Atendimento</span><b>' +
                S.esc(d.tipo_rotulo) + '</b></div>' +
            '<div class="sep-total mt-2"><span>Chamado</span><b>' +
                S.esc(d.chamado) + '</b></div>' +
            '<div class="sep-total mt-2"><span>Tempo em curso</span><b>' +
                S.esc(duracao(d.segundos_uteis)) + '</b></div>' +
            (d.separada_por ? '<div class="sep-total mt-2"><span>Separou</span><b>' +
                S.esc(d.separada_por) + '</b></div>' : '');
        if (d.chamado_resumo) {
            infoCorpo.appendChild(S.el('p', {
                className: 'text-muted mt-3',
                style: 'font-size:12.5px',
                textContent: d.chamado_resumo
            }));
        }
        lateral.appendChild(cartao('Resumo', infoCorpo));
        layout.appendChild(lateral);
        c.appendChild(layout);
    }

    function barraBipe(d, c) {
        var pendentes = d.itens.filter(function (i) {
            return i.separados < i.quantidade;
        });
        var barra = S.el('div', { className: 'form-row-inline mb-3' });
        if (!pendentes.length) {
            barra.appendChild(S.el('span', { className: 'text-muted',
                textContent: 'Todos os equipamentos foram bipados.' }));
            return barra;
        }

        var seletor = S.el('select', { className: 'form-control form-control-inline' });
        pendentes.forEach(function (i) {
            seletor.appendChild(S.el('option', {
                value: String(i.id),
                textContent: i.modelo + ' (' + (i.quantidade - i.separados) + ' restante)'
            }));
        });
        var serie = S.el('input', {
            className: 'form-control form-control-inline',
            style: 'min-width:240px',
            placeholder: 'Bipe a série do equipamento',
            autocomplete: 'off'
        });

        async function bipar() {
            var valor = serie.value.trim();
            if (!valor) return;
            try {
                await S.api('/separacao/solicitacoes/' +
                            encodeURIComponent(d.numero) + '/bipar', {
                    method: 'POST',
                    body: { item_id: parseInt(seletor.value, 10), serial: valor }
                });
                S.toast('Série ' + valor.toUpperCase() + ' reservada.', 'success');
                desenhar(c);
            } catch (e) {
                S.toast(e.message, 'danger');
                serie.select();
            }
        }

        serie.addEventListener('keydown', function (ev) {
            // O leitor de código de barras termina com Enter: bipar é a
            // ação de teclado, e o botão existe para quem digita.
            if (ev.key === 'Enter') { ev.preventDefault(); bipar(); }
        });

        barra.appendChild(seletor);
        barra.appendChild(serie);
        barra.appendChild(botao('Confirmar', 'btn-secondary', bipar));
        setTimeout(function () { serie.focus(); }, 50);
        return barra;
    }

    function tabelaItens(d) {
        var wrap = S.el('div', { className: 'table-wrapper' });
        var t = S.el('table', { className: 'data-table' });
        var html = '<thead><tr><th>Equipamento</th><th>Série</th>' +
                   '<th>Separou</th><th>Situação</th></tr></thead><tbody>';
        d.itens.forEach(function (i) {
            i.series.forEach(function (u) {
                html += '<tr><td>' + S.esc(i.modelo) + '</td>' +
                    '<td class="sep-serie">' + S.esc(u.serial) + '</td>' +
                    '<td>' + S.esc(u.por) + '</td>' +
                    '<td><span class="badge badge-success">Reservado</span></td></tr>';
            });
            for (var k = i.separados; k < i.quantidade; k++) {
                html += '<tr><td>' + S.esc(i.modelo) + '</td>' +
                    '<td class="sep-pendente">aguardando leitura</td>' +
                    '<td class="sep-pendente">—</td>' +
                    '<td><span class="badge badge-default">Pendente</span></td></tr>';
            }
        });
        t.innerHTML = html + '</tbody>';
        wrap.appendChild(t);
        return wrap;
    }

    async function acao(c, qual) {
        try {
            S.loading(true);
            await S.api('/separacao/solicitacoes/' +
                        encodeURIComponent(vista.numero) + '/' + qual,
                        { method: 'POST' });
            S.toast(({
                iniciar: 'Separação assumida.',
                concluir: 'Separação concluída.',
                enviar: 'Envio registrado.'
            })[qual], 'success');
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
            texto.appendChild(S.el('p', {
                className: 'text-muted',
                style: 'margin:0;font-size:13px', textContent: sub
            }));
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
