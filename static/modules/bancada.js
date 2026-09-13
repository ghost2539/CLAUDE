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
        ['FROTA', 'Frota móvel'],
        ['LOJA', 'Equipamento de loja'],
        ['CONECTIVIDADE', 'Conectividade']
    ];
    var ROTA = { frota: 'FROTA', loja: 'LOJA', conectividade: 'CONECTIVIDADE' };
    var SUB = { FROTA: 'frota', LOJA: 'loja', CONECTIVIDADE: 'conectividade' };

    var DESTINOS = [
        ['APTO', 'Apto / reparado'],
        ['AGUARDANDO_PECAS', 'Aguardando peças'],
        ['ASSISTENCIA', 'Assistência externa'],
        ['INVIAVEL', 'Reparo inviável'],
        ['DEVOLVER', 'Devolver ao terceiro']
    ];

    var vista = { bancada: 'FROTA', ativo: null, causas: [] };

    window.SPARE_MODULES.bancada = {
        render: function (container, sub) {
            S = window.SPARE;
            vista.bancada = ROTA[sub] || 'FROTA';
            vista.ativo = null;
            S.tabs(BANCADAS.map(function (b) { return [SUB[b[0]], b[1]]; }),
                   SUB[vista.bancada], 'bancada');
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
        c.appendChild(cabecalho(d.rotulo,
            'Triagem e reparo. A fila vem da trilha do ativo.'));

        c.appendChild(indicadores([
            ['Na fila', d.aguardando.length, 'accent-gold'],
            ['Em bancada', d.em_curso.length, 'accent-teal'],
            ['Aguardando peça', d.aguardando_pecas.length, 'accent-orange'],
            ['Peça atrasada', d.envelhecidos, 'accent-orange']
        ]));

        c.appendChild(barraBipe(c));

        if (vista.ativo) c.appendChild(painelAtivo(c));

        var layout = S.el('div', { className: 'sep-layout' });
        var esq = S.el('div');
        var dir = S.el('div');
        layout.appendChild(esq);
        layout.appendChild(dir);

        esq.appendChild(cartao('Aguardando triagem',
            listaFila(d.aguardando, c, 'Nenhum equipamento na fila.')));
        if (d.em_curso.length) {
            esq.appendChild(cartao('Em bancada agora',
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
        // Secundário de propósito: com equipamento na bancada, a ação
        // principal é registrar o reparo. Dois botões em cobre na mesma
        // tela disputam a atenção e nenhum dos dois ganha.
        linha.appendChild(botao('Assumir', 'btn-secondary', function () {
            bipar(c, campo);
        }));
        corpo.appendChild(linha);
        campo.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); bipar(c, campo); }
        });
        setTimeout(function () { campo.focus(); }, 60);
        return cartao('Assumir equipamento', corpo);
    }

    async function bipar(c, campo) {
        var serie = (campo ? campo.value : '').trim();
        if (!serie) return;
        try {
            S.loading(true);
            vista.ativo = await S.api('/bancada/bipar', {
                method: 'POST',
                body: { serial: serie, bancada: vista.bancada }
            });
            S.toast('Série ' + vista.ativo.serial + ' assumida.', 'success');
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
        return cartao('Equipamento em bancada · ' + a.serial, corpo);
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
                } else if (v === 'INVIAVEL') {
                    extra.innerHTML =
                        '<div class="form-group"><label for="bnc-just">' +
                        'Por que o reparo é inviável</label>' +
                        '<textarea id="bnc-just" class="form-control" rows="2"></textarea>' +
                        '<span class="text-muted" style="font-size:11.5px">É o que ' +
                        'sustenta a baixa do ativo depois.</span></div>';
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
                (x.envelhecido ? ' · <span style="color:#E8B94A">atrasado</span>' : '') +
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
})();
