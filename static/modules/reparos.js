/* ================================================================
   Módulo: Bancada de Triagem e Reparo (A02, A03, A04)

   Tela de quem trabalha em pé, com o equipamento na mão. Por isso o
   campo de série tem o foco desde que a tela abre e o Enter bipa: o
   leitor de código de barras termina com Enter.

   A fila vem da Trilha, não deste módulo. O que é gravado aqui é o
   conteúdo do reparo — ações, causa, peças e destino.
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};

(function () {
    var S = null;
    var BANCADAS = [
        ['LOJA', 'Frente e Retaguarda'],
        ['FROTA', 'Mobilidade'],
        ['CONECTIVIDADE', 'Conectividade']
    ];
    var ROTA = { frota: 'FROTA', loja: 'LOJA', conectividade: 'CONECTIVIDADE' };
    var SUB = { FROTA: 'frota', LOJA: 'loja', CONECTIVIDADE: 'conectividade' };

    var DESTINOS = [
        ['INTERNALIZACAO', 'Internalização'],
        ['AGUARDANDO_PECAS', 'Aguardando peças'],
        ['ASSISTENCIA', 'Assistência externa'],
        ['INVIAVEL', 'Reparo inviável'],
        ['DEVOLVER', 'Devolver ao terceiro']
    ];

    var vista = { bancada: 'FROTA', ativo: null, causas: [] };

    window.SPARE_MODULES.reparos = {
        render: function (container, sub) {
            S = window.SPARE;
            var abas = BANCADAS.map(function (b) { return [SUB[b[0]], b[1]]; }).concat([['dashboard', 'Dashboard']]);
            if (sub === 'dashboard') {
                S.tabs(abas, 'dashboard', 'reparos');
                return telaDashboard(container);
            }
            vista.bancada = ROTA[sub] || 'LOJA';
            vista.ativo = null;
            S.tabs(abas, SUB[vista.bancada], 'reparos');
            desenhar(container);
        }
    };

    async function desenhar(c) {
        c.innerHTML = '<div class="spinner-inline">' +
            '<span class="spinner spinner-sm"></span> Carregando...</div>';
        var d;
        try {
            d = await S.api('/bancada/fila?bancada=' + vista.bancada);
            if (!vista.causas.length) {
                var r = await S.api('/bancada/causas?bancada=' + vista.bancada);
                vista.causas = r.causas;
            }
        } catch (e) {
            c.innerHTML = '';
            c.appendChild(S.el('div', { className: 'alert alert-danger',
                                        textContent: e.message }));
            return;
        }

        c.innerHTML = '';
        c.appendChild(cabecalho(d.rotulo, ''));

        var cartoes = [
            ['No backlog', d.aguardando.length, 'accent-gold'],
            ['Aguardando peça', d.aguardando_pecas.length, 'accent-orange'],
            ['Peça atrasada', d.envelhecidos, 'accent-orange']
        ];
        if (d.em_curso.length) cartoes.splice(1, 0, ['Fluxo antigo', d.em_curso.length, 'accent-teal']);
        c.appendChild(indicadores(cartoes));

        c.appendChild(barraBipe(c));

        if (vista.ativo) c.appendChild(painelAtivo(c));

        var layout = S.el('div', { className: 'sep-layout' });
        var esq = S.el('div');
        var dir = S.el('div');
        layout.appendChild(esq);
        layout.appendChild(dir);

        esq.appendChild(cartao('Backlog da bancada',
            listaFila(d.aguardando, c, 'Nenhum equipamento no backlog.')));
        if (d.em_curso.length) {
            esq.appendChild(cartao('Assumidos no fluxo antigo',
                listaFila(d.em_curso, c, '')));
        }
        dir.appendChild(cartao('Aguardando peça',
            listaPecas(d.aguardando_pecas, c)));
        c.appendChild(layout);
    }

    function barraBipe(c) {
        var corpo = S.el('div', { className: 'card-body' });
        var campo = S.el('input', {
            id: 'bnc-serie', className: 'form-control form-control-inline',
            style: 'min-width:280px', autocomplete: 'off',
            placeholder: 'Bipe a série do equipamento'
        });
        var linha = S.el('div', { className: 'form-row-inline' });
        linha.appendChild(campo);
        // Secundário de propósito: o bipe só traz o equipamento à tela.
        // A ação que vale é registrar o que foi feito, e é ela que despacha
        // o ativo — dois botões em cobre disputariam a atenção.
        linha.appendChild(botao('Bipar', 'btn-secondary', function () {
            bipar(c, campo);
        }));
        corpo.appendChild(linha);
        campo.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); bipar(c, campo); }
        });
        setTimeout(function () { campo.focus(); }, 60);
        return cartao('Bipar equipamento', corpo);
    }

    async function bipar(c, campo) {
        var serie = (campo ? campo.value : '').trim();
        if (!serie) return;
        try {
            S.loading(true);
            vista.ativo = await S.api('/bancada/ativo/' + encodeURIComponent(serie) +
                                      '?bancada=' + vista.bancada);
            desenhar(c);
        } catch (e) {
            S.toast(e.message, 'danger');
            if (campo) campo.select();
        } finally { S.loading(false); }
    }

    /* ============================================================
       O equipamento na mão: contexto e registro
       ============================================================ */
    function painelAtivo(c) {
        var a = vista.ativo;
        var corpo = S.el('div', { className: 'card-body' });

        /* Reincidência primeiro: é o que muda a decisão do técnico. */
        if (a.reincidencia_janela > 1) {
            corpo.appendChild(S.el('div', {
                className: 'alert alert-warning mb-3',
                textContent: 'Este equipamento passou ' + a.reincidencia_janela +
                    ' vezes pela bancada nos últimos ' + a.janela_dias +
                    ' dias. Vale olhar a causa das vezes anteriores antes de repetir o reparo.'
            }));
        }

        var ficha = S.el('div', { className: 'detail-grid mb-3' });
        ficha.innerHTML =
            item('Série', a.serial) + item('Modelo', a.modelo || '—') +
            item('Origem', a.origem || '—') +
            item('Passagens (total)', String(a.reincidencia_total));
        corpo.appendChild(ficha);

        if (a.historico.length) {
            var h = S.el('details', { className: 'mb-3' });
            h.innerHTML = '<summary style="cursor:pointer;color:var(--text-secondary);' +
                'font-size:13px">Histórico de reparos (' + a.historico.length + ')</summary>';
            var t = S.el('table', { className: 'data-table mt-2' });
            t.innerHTML = '<thead><tr><th>Quando</th><th>Bancada</th>' +
                '<th>Causa</th><th>Destino</th><th>Técnico</th></tr></thead><tbody>' +
                a.historico.map(function (x) {
                    return '<tr><td>' + S.esc(dataCurta(x.quando)) + '</td>' +
                        '<td>' + S.esc(x.bancada) + '</td>' +
                        '<td>' + S.esc(x.causa || '—') + '</td>' +
                        '<td>' + S.esc(x.destino) + '</td>' +
                        '<td>' + S.esc(x.tecnico) + '</td></tr>';
                }).join('') + '</tbody>';
            var w = S.el('div', { className: 'table-wrapper' });
            w.appendChild(t);
            h.appendChild(w);
            corpo.appendChild(h);
        }

        corpo.appendChild(formulario(c));
        return cartao('Equipamento na mão · ' + a.serial, corpo);
    }

    function formulario(c) {
        var f = S.el('div');
        f.innerHTML =
            '<div class="form-group mb-3">' +
              '<label for="bnc-acoes">O que foi feito</label>' +
              '<textarea id="bnc-acoes" class="form-control" rows="2"></textarea>' +
            '</div>' +
            '<div class="form-grid cols-2">' +
              '<div class="form-group"><label for="bnc-causa">Causa identificada</label>' +
                '<select id="bnc-causa" class="form-control">' +
                  vista.causas.map(function (x) {
                      return '<option value="' + x.id + '">' + S.esc(x.nome) + '</option>';
                  }).join('') +
                '</select></div>' +
              '<div class="form-group"><label for="bnc-pecas">Peças trocadas</label>' +
                '<input id="bnc-pecas" class="form-control" ' +
                       'placeholder="N/A se não houve troca"></div>' +
            '</div>' +
            '<div class="form-group mt-3"><label for="bnc-destino">Destino</label>' +
              '<select id="bnc-destino" class="form-control" style="max-width:320px">' +
                DESTINOS.map(function (d) {
                    return '<option value="' + d[0] + '">' + S.esc(d[1]) + '</option>';
                }).join('') +
              '</select></div>' +
            '<div id="bnc-extra" class="mt-3"></div>';

        var rodape = S.el('div', { className: 'btn-row mt-3' });
        rodape.appendChild(botao('Registrar e encaminhar', 'btn-primary', function () {
            registrar(c);
        }));
        f.appendChild(rodape);

        /* Campo adicional só quando o destino pede — a tela não mostra
           o que não vai ser usado. */
        setTimeout(function () {
            var sel = document.getElementById('bnc-destino');
            var extra = document.getElementById('bnc-extra');
            function pintar() {
                var v = sel.value;
                if (v === 'AGUARDANDO_PECAS') {
                    extra.innerHTML =
                        '<div class="form-group"><label for="bnc-aguardada">' +
                        'Peça aguardada</label><input id="bnc-aguardada" ' +
                        'class="form-control"></div>';
                } else if (v === 'ASSISTENCIA') {
                    extra.innerHTML =
                        '<div class="form-group"><label for="bnc-fornec">' +
                        'Fornecedor</label><input id="bnc-fornec" class="form-control">' +
                        '</div>';
                } else if (v === 'VENDA') {
                    extra.innerHTML =
                        '<div class="form-group"><label for="bnc-just">' +
                        'Por que o equipamento vai para venda</label>' +
                        '<textarea id="bnc-just" class="form-control" rows="2"></textarea></div>';
                } else {
                    extra.innerHTML = '';
                }
            }
            sel.addEventListener('change', pintar);
            pintar();
        }, 0);

        return f;
    }

    async function registrar(c) {
        function v(id) {
            var e = document.getElementById(id);
            return e ? e.value.trim() : '';
        }
        try {
            S.loading(true);
            var r = await S.api('/bancada/registrar', {
                method: 'POST',
                body: {
                    serial: vista.ativo.serial,
                    bancada: vista.bancada,
                    acoes: v('bnc-acoes'),
                    causa_id: parseInt(v('bnc-causa'), 10),
                    pecas: v('bnc-pecas'),
                    destino: v('bnc-destino'),
                    pecas_aguardadas: v('bnc-aguardada'),
                    fornecedor: v('bnc-fornec'),
                    justificativa: v('bnc-just')
                }
            });
            S.toast(r.serial + ' → ' + r.destino_rotulo, 'success');
            vista.ativo = null;
            desenhar(c);
        } catch (e) {
            S.toast(e.message, 'danger');
        } finally { S.loading(false); }
    }

    /* ============================================================
       Listas
       ============================================================ */
    function listaFila(linhas, c, vazio) {
        var corpo = S.el('div', { className: 'card-body' });
        if (!linhas.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio', textContent: vazio }));
            return corpo;
        }
        var t = S.el('table', { className: 'data-table' });
        t.innerHTML = '<thead><tr><th>Série</th><th>Modelo</th><th>Origem</th>' +
            '<th>Parado há</th><th>Com</th></tr></thead>';
        var tb = S.el('tbody');
        linhas.forEach(function (x) {
            var tr = S.el('tr');
            tr.innerHTML = '<td class="sep-serie">' + S.esc(x.serial) + '</td>' +
                '<td>' + S.esc(x.modelo || '—') + '</td>' +
                '<td>' + S.esc(x.origem || '—') + '</td>' +
                '<td>' + S.esc(duracao(x.segundos)) + '</td>' +
                '<td>' + S.esc(x.usuario || '—') + '</td>';
            tb.appendChild(tr);
        });
        t.appendChild(tb);
        var w = S.el('div', { className: 'table-wrapper' });
        w.appendChild(t);
        corpo.appendChild(w);
        return corpo;
    }

    function listaPecas(linhas, c) {
        var corpo = S.el('div', { className: 'card-body' });
        if (!linhas.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio',
                textContent: 'Nada aguardando peça.' }));
            return corpo;
        }
        linhas.forEach(function (x) {
            var l = S.el('div', { className: 'sep-linha' });
            var esq = S.el('div');
            esq.innerHTML = '<div class="sep-serie">' + S.esc(x.serial) + '</div>' +
                '<div class="text-muted" style="font-size:11.5px">' +
                S.esc(duracao(x.segundos)) + ' aguardando' +
                (x.envelhecido ? ' · <span style="color:var(--sp-gold)">atrasado</span>' : '') +
                '</div>';
            l.appendChild(esq);
            l.appendChild(botao('Peça chegou', 'btn-outline btn-sm', async function () {
                try {
                    S.loading(true);
                    vista.ativo = await S.api('/bancada/retornar-peca', {
                        method: 'POST',
                        body: { serial: x.serial, bancada: vista.bancada }
                    });
                    S.toast('Reparo retomado.', 'success');
                    desenhar(c);
                } catch (e) {
                    S.toast(e.message, 'danger');
                } finally { S.loading(false); }
            }));
            corpo.appendChild(l);
        });
        return corpo;
    }

    /* ============================================================
       Peças
       ============================================================ */
    function duracao(seg) {
        if (!seg && seg !== 0) return '—';
        var h = Math.floor(seg / 3600), m = Math.floor((seg % 3600) / 60);
        if (h >= 24) return Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
        return h ? h + 'h ' + m + 'm' : m + 'm';
    }

    function dataCurta(iso) {
        if (!iso) return '—';
        return new Date(iso).toLocaleDateString('pt-BR',
            { day: '2-digit', month: '2-digit', year: '2-digit' });
    }

    function item(rot, valor) {
        return '<div><label style="font-size:12px;color:var(--text-secondary)">' +
            S.esc(rot) + '</label><div>' + S.esc(valor) + '</div></div>';
    }

    function cabecalho(titulo, sub) {
        var d = S.el('div', { style: 'margin-bottom:18px' });
        d.appendChild(S.el('h2', { className: 'page-title',
                                   style: 'margin:0 0 4px', textContent: titulo }));
        d.appendChild(S.el('p', { className: 'text-muted',
                                  style: 'margin:0;font-size:13px', textContent: sub }));
        return d;
    }

    function indicadores(lista) {
        var g = S.el('div', { className: 'stats-grid mb-3' });
        lista.forEach(function (x) {
            var card = S.el('div', { className: 'stat-card ' + x[2] });
            card.innerHTML = '<div class="stat-value">' + x[1] + '</div>' +
                '<div class="stat-label">' + S.esc(x[0]) + '</div>';
            g.appendChild(card);
        });
        return g;
    }

    function cartao(titulo, corpo) {
        var card = S.el('div', { className: 'card mb-3' });
        card.appendChild(S.el('div', { className: 'card-header', textContent: titulo }));
        card.appendChild(corpo);
        return card;
    }

    function botao(texto, classe, aoClicar) {
        return S.el('button', { className: 'btn ' + classe, type: 'button',
                                textContent: texto, onClick: aoClicar });
    }

    /* ============================================================
       Dashboard — tempo parado pelo núcleo e volume por pessoa
       ============================================================ */
    async function telaDashboard(c) {
        c.innerHTML = '';
        var hoje = new Date(); var ini = new Date(hoje.getFullYear(), hoje.getMonth(), 1);
        var filtro = S.el('div', { className: 'card mb-3' });
        filtro.innerHTML = '<div class="card-body form-row-inline">' +
            '<input id="rd-ini" type="date" class="form-control form-control-inline" value="' + ini.toISOString().slice(0, 10) + '">' +
            '<input id="rd-fim" type="date" class="form-control form-control-inline" value="' + hoje.toISOString().slice(0, 10) + '">' +
            '<button id="rd-ok" class="btn btn-secondary">Consultar</button></div>';
        c.appendChild(S.el('h2', { className: 'page-title', textContent: 'Central de Reparos' }));
        c.appendChild(filtro);
        var alvo = S.el('div'); c.appendChild(alvo);
        async function carregar() {
            alvo.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando...</div>';
            var d;
            try {
                d = await S.api('/bancada/dashboard?data_inicio=' + document.getElementById('rd-ini').value +
                                '&data_fim=' + document.getElementById('rd-fim').value);
            } catch (e) { alvo.innerHTML = '<div class="alert alert-danger">' + S.esc(e.message) + '</div>'; return; }
            alvo.innerHTML = '';
            alvo.appendChild(indicadores([
                ['Equipamentos despachados', d.total, 'accent-teal'],
                ['Tempo parado médio', duracao(d.medio_parado), 'accent-gold'],
                ['Dias com movimento', d.por_dia.length, 'accent-green'],
                ['Meses no período', d.por_mes.length, 'accent-orange']
            ]));
            var grade = S.el('div', { className: 'form-grid cols-2' });
            grade.appendChild(cartaoLista('Por bancada', d.por_bancada));
            grade.appendChild(cartaoVolume('Por pessoa', d.por_tecnico));
            grade.appendChild(cartaoLista('Por destino', d.por_destino));
            grade.appendChild(cartaoSerie('Volume por dia', d.por_dia, 'dia'));
            grade.appendChild(cartaoSerie('Volume por mês', d.por_mes, 'mes'));
            alvo.appendChild(grade);
            var corpo = S.el('div', { className: 'card-body' });
            corpo.appendChild(S.table([
                { key: 'fechado_em', label: 'Data', render: function (v) { return new Date(v).toLocaleDateString('pt-BR'); } },
                { key: 'serial', label: 'Série' }, { key: 'modelo', label: 'Modelo' },
                { key: 'bancada_rotulo', label: 'Bancada' }, { key: 'tecnico', label: 'Técnico' },
                { key: 'causa', label: 'Causa' }, { key: 'destino_rotulo', label: 'Destino' },
                { key: 'segundos', label: 'Parado', render: function (v) { return duracao(v); } }
            ], d.registros));
            alvo.appendChild(cartao('Equipamentos despachados no período', corpo));
        }
        document.getElementById('rd-ok').onclick = carregar;
        carregar();
    }

    function cartaoLista(titulo, linhas) {
        var corpo = S.el('div', { className: 'card-body' });
        if (!linhas.length) corpo.appendChild(S.el('p', { className: 'sep-vazio', textContent: 'Nada no período.' }));
        linhas.forEach(function (l) {
            var x = S.el('div', { className: 'sep-total mt-1' });
            x.innerHTML = '<span>' + S.esc(l.rotulo) + '</span><b>' + l.quantidade +
                ' · ' + S.esc(duracao(l.segundos)) + '</b>';
            corpo.appendChild(x);
        });
        return cartao(titulo, corpo);
    }

    /* Volume por pessoa: quantos equipamentos despachou e a média por dia
       em que trabalhou — é o que a área pediu para acompanhar. */
    function cartaoVolume(titulo, linhas) {
        var corpo = S.el('div', { className: 'card-body' });
        if (!linhas.length) corpo.appendChild(S.el('p', { className: 'sep-vazio', textContent: 'Nada no período.' }));
        linhas.forEach(function (l) {
            var x = S.el('div', { className: 'sep-total mt-1' });
            x.innerHTML = '<span>' + S.esc(l.rotulo) + '</span><b>' + l.quantidade +
                ' · ' + l.por_dia + '/dia em ' + l.dias + ' dia(s)</b>';
            corpo.appendChild(x);
        });
        return cartao(titulo, corpo);
    }

    function cartaoSerie(titulo, linhas, campo) {
        var corpo = S.el('div', { className: 'card-body' });
        if (!linhas.length) corpo.appendChild(S.el('p', { className: 'sep-vazio', textContent: 'Nada no período.' }));
        linhas.slice(-31).forEach(function (l) {
            var x = S.el('div', { className: 'sep-total mt-1' });
            var rot = campo === 'dia'
                ? l.dia.slice(8) + '/' + l.dia.slice(5, 7)
                : l.mes.slice(5) + '/' + l.mes.slice(0, 4);
            x.innerHTML = '<span>' + S.esc(rot) + '</span><b>' + l.quantidade + '</b>';
            corpo.appendChild(x);
        });
        return cartao(titulo, corpo);
    }
})();
