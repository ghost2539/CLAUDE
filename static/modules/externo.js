/* ================================================================
   Módulo: Assistência externa e devolução (A05, A14)

   Duas abas porque são duas finalidades opostas com a mesma forma:
   assistência sai e volta, devolução sai e fica.

   A coluna que importa é a previsão. Equipamento fora sem prazo vira
   equipamento perdido, e é por isso que o prazo é obrigatório no envio.
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};

(function () {
    var S = null;
    var ABAS = [['assistencia', 'Assistência externa'],
                ['devolucao', 'Devolução a terceiros']];
    var ESPECIE = { assistencia: 'ASSISTENCIA', devolucao: 'DEVOLUCAO' };
    var SUB = { ASSISTENCIA: 'assistencia', DEVOLUCAO: 'devolucao' };
    var RESULTADOS = [
        ['REPARADO', 'Reparado'],
        ['SUBSTITUIDO', 'Substituído por outro equipamento'],
        ['SEM_REPARO', 'Voltou sem reparo']
    ];

    var vista = { especie: 'ASSISTENCIA' };

    window.SPARE_MODULES.externo = {
        render: function (container, sub) {
            S = window.SPARE;
            vista.especie = ESPECIE[sub] || 'ASSISTENCIA';
            S.tabs(ABAS, SUB[vista.especie], 'externo');
            desenhar(container);
        }
    };

    async function desenhar(c) {
        c.innerHTML = '<div class="spinner-inline">' +
            '<span class="spinner spinner-sm"></span> Carregando...</div>';
        var d;
        try { d = await S.api('/externo/fila?especie=' + vista.especie); }
        catch (e) {
            c.innerHTML = '';
            c.appendChild(S.el('div', { className: 'alert alert-danger',
                                        textContent: e.message }));
            return;
        }

        c.innerHTML = '';
        c.appendChild(cabecalho(d.rotulo, vista.especie === 'ASSISTENCIA'
            ? 'O relógio de quem está fora não conta contra ninguém do SPARE.'
            : 'Operadora, Lexmark e comodato. O que volta é a confirmação.'));
        c.appendChild(indicadores([
            ['Para enviar', d.aguardando.length, 'accent-gold'],
            ['Fora da área', d.fora.length, 'accent-teal'],
            ['Atrasados', d.atrasados, 'accent-orange'],
            ['Para conferir', d.conferencia.length, 'accent-green']
        ]));

        c.appendChild(cartao('Aguardando envio',
            tabela(d.aguardando, c, 'enviar', 'Nada para enviar.')));
        c.appendChild(cartao('Fora da área',
            tabela(d.fora, c, 'retornar', 'Nada fora da área.')));
    }

    function tabela(linhas, c, acao, vazio) {
        var corpo = S.el('div', { className: 'card-body' });
        if (!linhas.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio', textContent: vazio }));
            return corpo;
        }
        var t = S.el('table', { className: 'data-table' });
        var cabecalhos = acao === 'enviar'
            ? ['Série', 'Modelo', 'Parado há', '']
            : ['Série', 'Fornecedor', 'RMA', 'Previsão', 'Fora há', ''];
        t.innerHTML = '<thead><tr>' + cabecalhos.map(function (x) {
            return '<th>' + S.esc(x) + '</th>';
        }).join('') + '</tr></thead>';
        var tb = S.el('tbody');
        linhas.forEach(function (x) {
            var tr = S.el('tr');
            tr.innerHTML = acao === 'enviar'
                ? '<td class="sep-serie">' + S.esc(x.serial) + '</td>' +
                  '<td>' + S.esc(x.modelo || '—') + '</td>' +
                  '<td>' + S.esc(duracao(x.segundos)) + '</td>'
                : '<td class="sep-serie">' + S.esc(x.serial) + '</td>' +
                  '<td>' + S.esc(x.fornecedor || '—') + '</td>' +
                  '<td>' + S.esc(x.rma || '—') + '</td>' +
                  '<td>' + S.esc(dataCurta(x.previsao)) +
                    (x.atrasado ? ' <span class="badge badge-danger">atrasado</span>' : '') +
                  '</td>' +
                  '<td>' + S.esc(duracao(x.segundos)) + '</td>';
            var td = S.el('td');
            td.appendChild(botao(acao === 'enviar' ? 'Enviar' : 'Registrar retorno',
                'btn-outline btn-sm', function () {
                    (acao === 'enviar' ? dialogoEnvio : dialogoRetorno)(c, x);
                }));
            tr.appendChild(td);
            tb.appendChild(tr);
        });
        t.appendChild(tb);
        var w = S.el('div', { className: 'table-wrapper' });
        w.appendChild(t);
        corpo.appendChild(w);
        return corpo;
    }

    function dialogoEnvio(c, item) {
        var corpo = S.el('div');
        corpo.innerHTML =
            '<p class="text-muted" style="font-size:13px;margin:0 0 12px">' +
              S.esc(item.serial) + (item.modelo ? ' · ' + S.esc(item.modelo) : '') +
            '</p>' +
            campo('Fornecedor', 'ext-fornecedor') +
            campo('Número do RMA ou OS', 'ext-rma') +
            campo('Previsão de retorno', 'ext-previsao', 'date') +
            '<div class="form-group mt-2"><label for="ext-just">Justificativa' +
              '</label><textarea id="ext-just" class="form-control" rows="2">' +
              '</textarea></div>';
        S.openModal('Enviar para fora', corpo, [
            botao('Cancelar', 'btn-outline', S.closeModal),
            botao('Confirmar envio', 'btn-primary', async function () {
                try {
                    S.loading(true);
                    await S.api('/externo/enviar', {
                        method: 'POST',
                        body: {
                            serial: item.serial, especie: vista.especie,
                            fornecedor: v('ext-fornecedor'),
                            numero_rma: v('ext-rma'),
                            previsao: v('ext-previsao'),
                            justificativa: v('ext-just')
                        }
                    });
                    S.closeModal();
                    S.toast(item.serial + ' enviado.', 'success');
                    desenhar(c);
                } catch (e) {
                    S.toast(e.message, 'danger');
                } finally { S.loading(false); }
            })
        ]);
    }

    function dialogoRetorno(c, item) {
        var corpo = S.el('div');
        corpo.innerHTML =
            '<p class="text-muted" style="font-size:13px;margin:0 0 12px">' +
              S.esc(item.serial) + ' · ' + S.esc(item.fornecedor || 'sem fornecedor') +
            '</p>' +
            (vista.especie === 'ASSISTENCIA' ?
              '<div class="form-group"><label for="ext-resultado">Resultado</label>' +
                '<select id="ext-resultado" class="form-control">' +
                  RESULTADOS.map(function (r) {
                      return '<option value="' + r[0] + '">' + S.esc(r[1]) + '</option>';
                  }).join('') + '</select></div>' +
              '<div id="ext-bloco-sub"></div>' +
              campo('Custo', 'ext-custo', 'number') : '') +
            '<div class="form-group mt-2"><label for="ext-obs">Observação</label>' +
              '<input id="ext-obs" class="form-control"></div>';

        S.openModal('Registrar retorno', corpo, [
            botao('Cancelar', 'btn-outline', S.closeModal),
            botao('Confirmar', 'btn-primary', async function () {
                try {
                    S.loading(true);
                    var r = await S.api('/externo/retornar', {
                        method: 'POST',
                        body: {
                            serial: item.serial, especie: vista.especie,
                            resultado: v('ext-resultado'),
                            serial_substituto: v('ext-substituto'),
                            custo: v('ext-custo') ? parseFloat(v('ext-custo')) : null,
                            observacao: v('ext-obs')
                        }
                    });
                    S.closeModal();
                    S.toast(r.substituto
                        ? 'Trocado por ' + r.substituto + '.'
                        : item.serial + ' voltou.', 'success');
                    desenhar(c);
                } catch (e) {
                    S.toast(e.message, 'danger');
                } finally { S.loading(false); }
            })
        ]);

        // O campo da série substituta só aparece quando houve troca —
        // é o dado sem o qual o histórico se perde no meio do caminho.
        setTimeout(function () {
            var sel = document.getElementById('ext-resultado');
            if (!sel) return;
            var bloco = document.getElementById('ext-bloco-sub');
            function pintar() {
                bloco.innerHTML = sel.value === 'SUBSTITUIDO'
                    ? campo('Série do equipamento que veio no lugar', 'ext-substituto')
                    : '';
            }
            sel.addEventListener('change', pintar);
            pintar();
        }, 0);
    }

    /* ── Peças ─────────────────────────────────────────────────── */
    function v(id) {
        var e = document.getElementById(id);
        return e ? e.value.trim() : '';
    }

    function campo(rot, id, tipo) {
        return '<div class="form-group mt-2"><label for="' + id + '">' +
            S.esc(rot) + '</label><input id="' + id + '" type="' +
            (tipo || 'text') + '" class="form-control"></div>';
    }

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
