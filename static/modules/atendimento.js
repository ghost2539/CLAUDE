/* ================================================================
   Módulo: Atendimento a Chamados (A20)

   Duas frentes, como no desenho: Loja e Frota móvel. O chamado vive no
   ServiceNow; aqui fica o relógio da área e o que ele move.

   A ação que dá sentido à tela é "Preciso de equipamento": ela abre a
   separação e pausa o relógio de quem atende. Sem ela, quem atende é
   medido pelo tempo de separação de outra pessoa.
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};

(function () {
    var S = null;
    var FRENTES = [['LOJA', 'Loja'], ['FROTA', 'Frota móvel']];
    var ROTA = { loja: 'LOJA', frota: 'FROTA' };
    var SUB = { LOJA: 'loja', FROTA: 'frota' };
    var TIPOS_SEP = [
        ['FRENTE_RETAGUARDA', 'Frente e Retaguarda'],
        ['MOBILIDADE', 'Mobilidade'],
        ['INAUGURACAO_REFORMA', 'Inauguração e Reforma']
    ];

    var vista = { frente: 'LOJA', tela: 'fila', numero: null };

    window.SPARE_MODULES.atendimento = {
        render: function (container, sub) {
            S = window.SPARE;
            vista.frente = ROTA[sub] || 'LOJA';
            vista.tela = 'fila';
            vista.numero = null;
            S.tabs(FRENTES.map(function (f) { return [SUB[f[0]], f[1]]; }),
                   SUB[vista.frente], 'atendimento');
            desenhar(container);
        }
    };

    function desenhar(c) {
        return vista.tela === 'detalhe' ? telaDetalhe(c) : telaFila(c);
    }

    function duracao(seg) {
        if (!seg && seg !== 0) return '—';
        var h = Math.floor(seg / 3600), m = Math.floor((seg % 3600) / 60);
        if (h >= 24) return Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
        return h ? h + 'h ' + m + 'm' : m + 'm';
    }

    function selo(estado) {
        return ({
            AG_ATENDIMENTO: 'badge-gold', EX_ATENDIMENTO: 'badge-teal',
            AG_TERCEIRO: 'badge-warning', AG_EQUIPAMENTO: 'badge-orange',
            RESOLVIDO: 'badge-success'
        })[estado] || 'badge-default';
    }

    /* ============================================================
       Fila
       ============================================================ */
    async function telaFila(c) {
        c.innerHTML = '<div class="spinner-inline">' +
            '<span class="spinner spinner-sm"></span> Carregando...</div>';
        var d;
        try {
            d = await S.api('/atendimento/chamados?frente=' + vista.frente);
        } catch (e) { return falha(c, e); }

        var nome = FRENTES.filter(function (f) {
            return f[0] === vista.frente;
        })[0][1];

        c.innerHTML = '';
        c.appendChild(cabecalho(nome, '',
            [botao('Assumir chamado', 'btn-primary', assumirNovo)]));

        var n = d.contagem || {};
        c.appendChild(indicadores([
            ['Aguardando', n.AG_ATENDIMENTO || 0, 'accent-gold'],
            ['Em atendimento', n.EX_ATENDIMENTO || 0, 'accent-teal'],
            ['Aguardando equipamento', n.AG_EQUIPAMENTO || 0, 'accent-orange'],
            ['Fora do prazo', d.atrasados || 0, 'accent-orange']
        ]));

        var corpo = S.el('div', { className: 'card-body' });
        if (!d.chamados.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio',
                textContent: 'Nenhum chamado nesta frente.' }));
        } else {
            corpo.appendChild(tabela(d.chamados, c));
        }
        c.appendChild(cartao('Chamados', corpo));
    }

    function tabela(linhas, c) {
        var t = S.el('table', { className: 'data-table' });
        t.innerHTML = '<thead><tr>' +
            ['Chamado', 'Resumo', 'Local', 'Atendente', 'Com o atendente',
             'Em espera', 'Situação', ''].map(function (x) {
                return '<th>' + S.esc(x) + '</th>';
            }).join('') + '</tr></thead>';
        var tb = S.el('tbody');
        linhas.forEach(function (ch) {
            var tr = S.el('tr');
            tr.innerHTML =
                '<td><b>' + S.esc(ch.numero) + '</b></td>' +
                '<td>' + S.esc(corta(ch.resumo, 52)) + '</td>' +
                '<td>' + S.esc(ch.local || '—') + '</td>' +
                '<td>' + S.esc(ch.atendente || '—') + '</td>' +
                '<td>' + S.esc(duracao(ch.segundos_atendente)) + '</td>' +
                '<td>' + S.esc(duracao(ch.segundos_espera)) + '</td>' +
                '<td><span class="badge ' + selo(ch.estado) + '">' +
                    S.esc(ch.estado_rotulo) + '</span>' +
                    (ch.atrasado ? ' <span class="badge badge-danger">atrasado</span>' : '') +
                '</td>';
            var td = S.el('td');
            td.appendChild(botao('Abrir', 'btn-outline btn-sm', function () {
                vista.tela = 'detalhe'; vista.numero = ch.numero; desenhar(c);
            }));
            tr.appendChild(td);
            tb.appendChild(tr);
        });
        t.appendChild(tb);
        var w = S.el('div', { className: 'table-wrapper' });
        w.appendChild(t);
        return w;
    }

    function corta(texto, n) {
        texto = texto || '';
        return texto.length > n ? texto.slice(0, n - 1) + '…' : texto;
    }

    /* Traz um chamado do ServiceNow e já coloca o relógio a correr. */
    function assumirNovo() {
        var corpo = S.el('div');
        corpo.innerHTML =
            '<div class="form-group">' +
              '<label for="atd-novo">Número do chamado</label>' +
              '<input id="atd-novo" class="form-control" ' +
                     'placeholder="INC0000000 ou RITM0000000" autocomplete="off">' +
            '</div>';
        S.openModal('Assumir chamado', corpo, rodapeModal('Assumir',
            async function () {
                var numero = document.getElementById('atd-novo').value.trim();
                if (!numero) return;
                try {
                    S.loading(true);
                    await S.api('/atendimento/chamados/' +
                                encodeURIComponent(numero) + '/assumir',
                                { method: 'POST' });
                    S.closeModal();
                    S.toast('Chamado ' + numero.toUpperCase() + ' assumido.', 'success');
                    vista.tela = 'detalhe';
                    vista.numero = numero.toUpperCase();
                    desenhar(document.getElementById('page-content'));
                } catch (e) {
                    S.toast(e.message, 'danger');
                } finally {
                    S.loading(false);
                }
            }));
        setTimeout(function () {
            var i = document.getElementById('atd-novo');
            if (i) i.focus();
        }, 60);
    }

    /* ============================================================
       Detalhe
       ============================================================ */
    async function telaDetalhe(c) {
        c.innerHTML = '<div class="spinner-inline">' +
            '<span class="spinner spinner-sm"></span> Carregando...</div>';
        var d;
        try {
            d = await S.api('/atendimento/chamados/' + encodeURIComponent(vista.numero));
        } catch (e) { return falha(c, e); }

        c.innerHTML = '';
        c.appendChild(cabecalho(d.numero, d.resumo || 'Sem resumo no chamado.',
                                acoes(d, c)));

        var layout = S.el('div', { className: 'sep-layout' });
        var esq = S.el('div');
        var dir = S.el('div');
        layout.appendChild(esq);
        layout.appendChild(dir);
        c.appendChild(layout);

        /* Onde o tempo foi parar — o número que a área não tem hoje. */
        var tempos = S.el('div', { className: 'card-body' });
        tempos.innerHTML =
            '<div class="stats-grid">' +
              tile('Com o atendente', duracao(d.segundos_atendente), 'accent-teal') +
              tile('Em espera', duracao(d.segundos_espera), 'accent-gold') +
              tile('Total desde a abertura', duracao(d.segundos_total), 'accent-orange') +
            '</div>';
        esq.appendChild(cartao('Tempo', tempos));

        /* Linha do tempo */
        var linha = S.el('div', { className: 'card-body' });
        if (!d.linha_do_tempo.length) {
            linha.appendChild(S.el('p', { className: 'sep-vazio',
                                          textContent: 'Sem movimentação ainda.' }));
        } else {
            var ul = S.el('ul', { className: 'atd-tl' });
            d.linha_do_tempo.forEach(function (m, i) {
                var li = S.el('li', {
                    className: i === d.linha_do_tempo.length - 1 ? 'atual' : 'ok'
                });
                li.innerHTML =
                    '<div class="atd-tl-quando">' + S.esc(quando(m.quando)) + '</div>' +
                    '<div class="atd-tl-que">' + S.esc(m.para_rotulo) + '</div>' +
                    '<div class="atd-tl-quem">' +
                        S.esc(m.usuario || 'automático') +
                        (m.motivo ? ' · ' + S.esc(m.motivo) : '') + '</div>';
                ul.appendChild(li);
            });
            linha.appendChild(ul);
        }
        esq.appendChild(cartao('Andamento', linha));

        /* Lateral: dados e vínculos */
        var lado = S.el('div', { className: 'card-body' });
        lado.innerHTML =
            par('Situação', d.estado_rotulo) +
            par('Frente', d.frente_rotulo) +
            par('Local', d.local || '—') +
            par('Solicitante', d.solicitante || '—') +
            par('Atendente', d.atendente || '—') +
            (d.prazo ? par('Prazo', quando(d.prazo) +
                (d.atrasado ? ' (vencido)' : '')) : '');
        dir.appendChild(cartao('Chamado', lado));

        var vinc = S.el('div', { className: 'card-body' });
        if (!d.vinculos.length) {
            vinc.appendChild(S.el('p', {
                className: 'text-muted', style: 'font-size:12.5px;margin:0',
                textContent: 'Nenhuma série vinculada.'
            }));
        } else {
            d.vinculos.forEach(function (v) {
                var l = S.el('div', { className: 'sep-total mt-1' });
                l.innerHTML = '<span>' + S.esc(v.especie) + '</span><b>' +
                    S.esc(v.valor) + '</b>';
                vinc.appendChild(l);
            });
        }
        var rodape = S.el('div', { className: 'card-footer' });
        rodape.appendChild(botao('Vincular série', 'btn-outline btn-sm', function () {
            vincular(d, c);
        }));
        var cv = cartao('Vínculos', vinc);
        cv.appendChild(rodape);
        dir.appendChild(cv);
    }

    function acoes(d, c) {
        var lista = [botao('Voltar', 'btn-outline', function () {
            vista.tela = 'fila'; desenhar(c);
        })];
        if (d.estado === 'EX_ATENDIMENTO') {
            lista.push(botao('Preciso de equipamento', 'btn-secondary', function () {
                pedirEquipamento(d, c);
            }));
            lista.push(botao('Aguardar terceiro', 'btn-outline', function () {
                pausar(d, c);
            }));
            lista.push(botao('Resolver', 'btn-primary', function () {
                resolver(d, c);
            }));
        } else if (d.estado === 'RESOLVIDO') {
            // Nada a fazer: reabrir é ato do ServiceNow, não do espelho.
        } else {
            lista.push(botao('Retomar atendimento', 'btn-primary', function () {
                chamar(c, d.numero, 'retomar');
            }));
        }
        return lista;
    }

    function pedirEquipamento(d, c) {
        var corpo = S.el('div');
        corpo.innerHTML =
            '<div class="form-group">' +
              '<label for="atd-tipo">Tipo de atendimento</label>' +
              '<select id="atd-tipo" class="form-control">' +
                TIPOS_SEP.map(function (t) {
                    return '<option value="' + t[0] + '">' + S.esc(t[1]) + '</option>';
                }).join('') +
              '</select>' +
            '</div>' +
            '<div class="form-group mt-2">' +
              '<label for="atd-modelo">Equipamento</label>' +
              '<input id="atd-modelo" class="form-control" ' +
                     'placeholder="modelo, como aparece no estoque">' +
            '</div>' +
            '<div class="form-group mt-2" style="max-width:140px">' +
              '<label for="atd-qtd">Quantidade</label>' +
              '<input id="atd-qtd" type="number" min="1" value="1" class="form-control">' +
            '</div>';
        S.openModal('Preciso de equipamento', corpo,
            rodapeModal('Abrir separação', async function () {
                var modelo = document.getElementById('atd-modelo').value.trim();
                if (!modelo) { S.toast('Informe o equipamento.', 'warning'); return; }
                try {
                    S.loading(true);
                    await S.api('/atendimento/chamados/' +
                                encodeURIComponent(d.numero) + '/aguardar-equipamento', {
                        method: 'POST',
                        body: {
                            tipo_atendimento: document.getElementById('atd-tipo').value,
                            itens: [{ modelo: modelo,
                                      quantidade: parseInt(document.getElementById('atd-qtd').value, 10) || 1 }]
                        }
                    });
                    S.closeModal();
                    S.toast('Separação aberta. O seu relógio pausou.', 'success');
                    desenhar(c);
                } catch (e) {
                    S.toast(e.message, 'danger');
                } finally { S.loading(false); }
            }));
    }

    function pausar(d, c) {
        pedirMotivo('Aguardar terceiro', 'O que se está esperando?',
            'Este tempo não conta contra você.', function (motivo) {
                return chamar(c, d.numero, 'aguardar-terceiro', { motivo: motivo });
            });
    }

    function resolver(d, c) {
        pedirMotivo('Resolver chamado', 'Como foi resolvido? (opcional)', '',
            function (motivo) {
                return chamar(c, d.numero, 'resolver', { motivo: motivo });
            }, true);
    }

    function vincular(d, c) {
        var corpo = S.el('div');
        corpo.innerHTML =
            '<div class="form-group">' +
              '<label for="atd-v-esp">Tipo</label>' +
              '<select id="atd-v-esp" class="form-control">' +
                '<option value="serial">Série do equipamento</option>' +
                '<option value="remessa">Remessa</option>' +
                '<option value="projeto">Projeto</option>' +
              '</select></div>' +
            '<div class="form-group mt-2">' +
              '<label for="atd-v-val">Valor</label>' +
              '<input id="atd-v-val" class="form-control" autocomplete="off">' +
            '</div>';
        S.openModal('Vincular ao chamado', corpo,
            rodapeModal('Vincular', async function () {
                var valor = document.getElementById('atd-v-val').value.trim();
                if (!valor) return;
                try {
                    S.loading(true);
                    await S.api('/atendimento/chamados/' +
                                encodeURIComponent(d.numero) + '/vinculos', {
                        method: 'POST',
                        body: { especie: document.getElementById('atd-v-esp').value,
                                valor: valor }
                    });
                    S.closeModal();
                    desenhar(c);
                } catch (e) {
                    S.toast(e.message, 'danger');
                } finally { S.loading(false); }
            }));
    }

    function pedirMotivo(titulo, rotulo, ajuda, aoConfirmar, opcional) {
        var corpo = S.el('div');
        corpo.innerHTML =
            '<div class="form-group">' +
              '<label for="atd-motivo">' + S.esc(rotulo) + '</label>' +
              '<textarea id="atd-motivo" class="form-control" rows="3"></textarea>' +
              (ajuda ? '<span class="text-muted" style="font-size:11.5px">' +
                       S.esc(ajuda) + '</span>' : '') +
            '</div>';
        S.openModal(titulo, corpo, rodapeModal('Confirmar',
            async function () {
                var motivo = document.getElementById('atd-motivo').value.trim();
                if (!motivo && !opcional) {
                    S.toast('Diga o motivo.', 'warning');
                    return;
                }
                S.closeModal();
                await aoConfirmar(motivo);
            }));
        setTimeout(function () {
            var t = document.getElementById('atd-motivo');
            if (t) t.focus();
        }, 60);
    }

    async function chamar(c, numero, acao, corpo) {
        try {
            S.loading(true);
            await S.api('/atendimento/chamados/' + encodeURIComponent(numero) +
                        '/' + acao, { method: 'POST', body: corpo || {} });
            desenhar(c);
        } catch (e) {
            S.toast(e.message, 'danger');
        } finally { S.loading(false); }
    }

    /* ============================================================
       Peças
       ============================================================ */
    function quando(iso) {
        if (!iso) return '—';
        var d = new Date(iso);
        return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit',
                                           hour: '2-digit', minute: '2-digit' });
    }

    function par(rot, valor) {
        return '<div class="sep-total mt-1"><span>' + S.esc(rot) + '</span><b>' +
            S.esc(valor) + '</b></div>';
    }

    function tile(rotulo, valor, acento) {
        return '<div class="stat-card ' + acento + '">' +
            '<div class="stat-value" style="font-size:22px">' + S.esc(valor) + '</div>' +
            '<div class="stat-label">' + S.esc(rotulo) + '</div></div>';
    }

    function falha(c, e) {
        c.innerHTML = '';
        c.appendChild(S.el('div', { className: 'alert alert-danger',
                                    textContent: e.message || String(e) }));
    }

    function cabecalho(titulo, sub, acoes) {
        var topo = S.el('div', {
            style: 'display:flex;align-items:flex-end;justify-content:space-between;' +
                   'gap:16px;flex-wrap:wrap;margin-bottom:18px'
        });
        var texto = S.el('div');
        texto.appendChild(S.el('h2', { className: 'page-title',
                                       style: 'margin:0 0 4px', textContent: titulo }));
        if (sub) texto.appendChild(S.el('p', {
            className: 'text-muted', style: 'margin:0;font-size:13px',
            textContent: sub
        }));
        topo.appendChild(texto);
        var caixa = S.el('div', { className: 'btn-row' });
        (acoes || []).forEach(function (b) { caixa.appendChild(b); });
        topo.appendChild(caixa);
        return topo;
    }

    function indicadores(lista) {
        var g = S.el('div', { className: 'stats-grid mb-3' });
        lista.forEach(function (x) {
            g.innerHTML += tile(x[0], x[1], x[2]);
        });
        return g;
    }

    function cartao(titulo, corpo) {
        var card = S.el('div', { className: 'card mb-3' });
        card.appendChild(S.el('div', { className: 'card-header', textContent: titulo }));
        card.appendChild(corpo);
        return card;
    }

    /* O modal do portal recebe elementos no rodapé, não descrições. */
    function rodapeModal(rotuloOk, aoConfirmar) {
        return [
            botao('Cancelar', 'btn-outline', S.closeModal),
            botao(rotuloOk, 'btn-primary', aoConfirmar)
        ];
    }

    function botao(texto, classe, aoClicar) {
        return S.el('button', { className: 'btn ' + classe, type: 'button',
                                textContent: texto, onClick: aoClicar });
    }
})();
