/* ================================================================
   Módulo: Destinação (A09 a A13)

   Duas abas, porque são dois trabalhos e dois donos:

   - Descaracterização: quem tem o equipamento na mão, remove a mídia e
     anexa a evidência.
   - Lotes: quem decide o destino e conduz venda, descarte ou doação.

   O anexo não é enfeite: sem ele a tela não deixa concluir. É o
   documento que a empresa apresenta se for questionada.
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};

(function () {
    var S = null;
    var ABAS = [['baixa', 'Descaracterização'], ['lotes', 'Lotes']];
    var DESTINOS = [['VENDA', 'Venda'], ['DESCARTE', 'Descarte'],
                    ['DOACAO', 'Doação']];
    var DOC_DO_DESTINO = {
        VENDA: ['nf_venda', 'Nota fiscal de venda'],
        DESCARTE: ['certificado_destinacao', 'Certificado de destinação'],
        DOACAO: ['termo_doacao', 'Termo de doação']
    };

    var vista = { aba: 'baixa', ativo: null, lote: null, escolhidos: [] };

    window.SPARE_MODULES.destinacao = {
        render: function (container, sub) {
            S = window.SPARE;
            vista.aba = (sub === 'lotes') ? 'lotes' : 'baixa';
            vista.ativo = null;
            vista.lote = null;
            vista.escolhidos = [];
            S.tabs(ABAS, vista.aba, 'destinacao');
            desenhar(container);
        }
    };

    function desenhar(c) {
        return vista.aba === 'lotes' ? telaLotes(c) : telaBaixa(c);
    }

    /* ============================================================
       A09 — Descaracterização
       ============================================================ */
    async function telaBaixa(c) {
        c.innerHTML = carregando();
        var d;
        try { d = await S.api('/destinacao/fila'); }
        catch (e) { return falha(c, e); }

        c.innerHTML = '';
        c.appendChild(cabecalho('Descaracterização',
            'Remoção de mídia e análise de condição antes de o ativo sair da área.'));
        c.appendChild(indicadores([
            ['Aguardando baixa', d.aguardando.length, 'accent-gold'],
            ['Em bancada', d.em_curso.length, 'accent-teal'],
            ['Aguardando destino', d.aguardando_destino.length, 'accent-orange']
        ]));
        c.appendChild(barraBipe(c));
        if (vista.ativo) c.appendChild(formularioBaixa(c));
        c.appendChild(cartao('Aguardando descaracterização',
            lista(d.aguardando, 'Nada aguardando.')));
    }

    function barraBipe(c) {
        var corpo = S.el('div', { className: 'card-body' });
        var campo = S.el('input', {
            id: 'dst-serie', className: 'form-control form-control-inline',
            style: 'min-width:280px', autocomplete: 'off',
            placeholder: 'Bipe a série do equipamento'
        });
        var linha = S.el('div', { className: 'form-row-inline' });
        linha.appendChild(campo);
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
        var serie = campo.value.trim();
        if (!serie) return;
        try {
            S.loading(true);
            vista.ativo = await S.api('/destinacao/bipar', {
                method: 'POST', body: { serial: serie }
            });
            desenhar(c);
        } catch (e) {
            S.toast(e.message, 'danger');
            campo.select();
        } finally { S.loading(false); }
    }

    function formularioBaixa(c) {
        var corpo = S.el('div', { className: 'card-body' });
        corpo.innerHTML =
            '<div class="detail-grid mb-3">' +
              par('Série', vista.ativo.serial) +
              par('Modelo', vista.ativo.modelo || '—') +
              par('Origem', vista.ativo.origem || '—') +
            '</div>' +
            '<div class="form-group mb-3"><label>Possui mídia de armazenamento?' +
              '</label><div class="form-row-inline">' +
                '<label style="font-weight:400"><input type="radio" name="dst-midia" ' +
                  'id="dst-midia-sim" value="sim"> Sim</label>' +
                '<label style="font-weight:400"><input type="radio" name="dst-midia" ' +
                  'value="nao" checked> Não</label>' +
              '</div></div>' +
            '<div id="dst-bloco-midia"></div>' +
            '<div class="form-grid cols-2 mt-3">' +
              '<div class="form-group"><label for="dst-condicao">Condição física' +
                '</label><select id="dst-condicao" class="form-control">' +
                  '<option value="">Selecione</option>' +
                  '<option value="boa">Boa — candidato a doação</option>' +
                  '<option value="regular">Regular</option>' +
                  '<option value="ruim">Ruim</option>' +
                '</select></div>' +
              '<div class="form-group"><label for="dst-local">Local de destino' +
                '</label><input id="dst-local" class="form-control"></div>' +
            '</div>' +
            '<div class="form-group mt-3"><label for="dst-obs">Observação</label>' +
              '<input id="dst-obs" class="form-control"></div>';

        var acoes = S.el('div', { className: 'btn-row mt-3' });
        acoes.appendChild(botao('Concluir descaracterização', 'btn-primary',
            function () { concluirBaixa(c); }));
        corpo.appendChild(acoes);

        setTimeout(function () {
            var sim = document.getElementById('dst-midia-sim');
            var bloco = document.getElementById('dst-bloco-midia');
            function pintar() {
                bloco.innerHTML = sim.checked ?
                    '<div class="form-grid cols-2">' +
                      '<div class="form-group"><label for="dst-serie-midia">' +
                        'Série da mídia</label><input id="dst-serie-midia" ' +
                        'class="form-control"><span class="text-muted" ' +
                        'style="font-size:11.5px">A rastreabilidade é por ' +
                        'série da mídia, não do equipamento.</span></div>' +
                      '<div class="form-group"><label for="dst-metodo">Método' +
                        '</label><select id="dst-metodo" class="form-control">' +
                          '<option value="REMOCAO_FISICA">Remoção física</option>' +
                          '<option value="LAUDO_ERS">Laudo da ERS</option>' +
                        '</select></div>' +
                    '</div>' +
                    '<div class="form-group mt-3"><label for="dst-evidencia">' +
                      'Evidência da remoção</label>' +
                      '<input id="dst-evidencia" type="file" class="form-control" ' +
                        'accept=".pdf,.jpg,.jpeg,.png">' +
                      '<span class="text-muted" style="font-size:11.5px">' +
                      'Obrigatória. Sem ela a descaracterização não conclui.' +
                      '</span></div>' : '';
            }
            document.getElementsByName('dst-midia').forEach(function (r) {
                r.addEventListener('change', pintar);
            });
            pintar();
        }, 0);

        return cartao('Descaracterizando ' + vista.ativo.serial, corpo);
    }

    async function concluirBaixa(c) {
        function v(id) {
            var e = document.getElementById(id);
            return e ? e.value.trim() : '';
        }
        var comMidia = document.getElementById('dst-midia-sim').checked;
        var dados = new FormData();
        dados.append('serial', vista.ativo.serial);
        dados.append('possui_midia', comMidia ? 'true' : 'false');
        dados.append('serie_midia', v('dst-serie-midia'));
        dados.append('metodo', comMidia ? v('dst-metodo') : 'SEM_MIDIA');
        dados.append('condicao_fisica', v('dst-condicao'));
        dados.append('observacao', v('dst-obs'));
        dados.append('local_destino', v('dst-local'));
        var arq = document.getElementById('dst-evidencia');
        if (arq && arq.files && arq.files[0]) {
            dados.append('evidencia', arq.files[0]);
        }
        try {
            S.loading(true);
            await S.api('/destinacao/descaracterizar', {
                method: 'POST', body: dados
            });
            S.toast(vista.ativo.serial + ' descaracterizado.', 'success');
            vista.ativo = null;
            desenhar(c);
        } catch (e) {
            S.toast(e.message, 'danger');
        } finally { S.loading(false); }
    }

    /* ============================================================
       A10 a A13 — Lotes
       ============================================================ */
    async function telaLotes(c) {
        if (vista.lote) return telaLoteDetalhe(c);
        c.innerHTML = carregando();
        var d, fila;
        try {
            d = await S.api('/destinacao/lotes');
            fila = await S.api('/destinacao/fila');
        } catch (e) { return falha(c, e); }

        c.innerHTML = '';
        c.appendChild(cabecalho('Lotes de destinação',
            'Venda, descarte e doação. Cada destino tem o seu documento.',
            fila.aguardando_destino.length
                ? [botao('Formar lote', 'btn-primary', function () {
                      formarLote(c, fila.aguardando_destino);
                  })]
                : []));

        if (fila.aguardando_destino.length) {
            c.appendChild(cartao(
                'Aguardando destino · ' + fila.aguardando_destino.length,
                lista(fila.aguardando_destino, '')));
        }

        var corpo = S.el('div', { className: 'card-body' });
        if (!d.lotes.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio',
                textContent: 'Nenhum lote formado ainda.' }));
        } else {
            var t = S.el('table', { className: 'data-table' });
            t.innerHTML = '<thead><tr><th>Lote</th><th>Destino</th><th>Itens</th>' +
                '<th>Aberto por</th><th>Em curso há</th><th>Situação</th><th></th>' +
                '</tr></thead>';
            var tb = S.el('tbody');
            d.lotes.forEach(function (l) {
                var tr = S.el('tr');
                tr.innerHTML = '<td><b>' + S.esc(l.numero) + '</b></td>' +
                    '<td>' + S.esc(l.destino_rotulo) + '</td>' +
                    '<td>' + l.itens + '</td>' +
                    '<td>' + S.esc(l.aberto_por) + '</td>' +
                    '<td>' + S.esc(duracao(l.segundos)) + '</td>' +
                    '<td><span class="badge ' +
                        (l.estado === 'CONCLUIDO' ? 'badge-success' : 'badge-gold') +
                        '">' + S.esc(l.estado_rotulo) + '</span></td>';
                var td = S.el('td');
                td.appendChild(botao('Abrir', 'btn-outline btn-sm', function () {
                    vista.lote = l.numero;
                    desenhar(c);
                }));
                tr.appendChild(td);
                tb.appendChild(tr);
            });
            t.appendChild(tb);
            var w = S.el('div', { className: 'table-wrapper' });
            w.appendChild(t);
            corpo.appendChild(w);
        }
        c.appendChild(cartao('Lotes', corpo));
    }

    function formarLote(c, disponiveis) {
        var corpo = S.el('div');
        corpo.innerHTML =
            '<div class="form-group"><label for="dst-lote-destino">Destino</label>' +
              '<select id="dst-lote-destino" class="form-control">' +
                DESTINOS.map(function (d) {
                    return '<option value="' + d[0] + '">' + S.esc(d[1]) + '</option>';
                }).join('') + '</select></div>' +
            '<div class="form-group mt-2"><label for="dst-lote-just">' +
              'Por que este destino</label>' +
              '<textarea id="dst-lote-just" class="form-control" rows="2"></textarea>' +
              '<span class="text-muted" style="font-size:11.5px">É o que responde ' +
              '"por que vendemos em vez de doar" seis meses depois.</span></div>' +
            '<div class="form-group mt-3"><label>Equipamentos</label>' +
              '<div style="max-height:220px;overflow:auto;border:1px solid ' +
                'var(--border-subtle);padding:8px">' +
                disponiveis.map(function (x) {
                    return '<label style="display:block;font-weight:400;padding:3px 0">' +
                        '<input type="checkbox" class="dst-item" value="' +
                        S.esc(x.serial) + '"> ' + S.esc(x.serial) + ' — ' +
                        S.esc(x.modelo || 'sem modelo') + '</label>';
                }).join('') +
              '</div></div>';

        S.openModal('Formar lote', corpo, [
            botao('Cancelar', 'btn-outline', S.closeModal),
            botao('Criar lote', 'btn-primary', async function () {
                var seriais = Array.prototype.slice.call(
                    document.querySelectorAll('.dst-item:checked')
                ).map(function (x) { return x.value; });
                if (!seriais.length) {
                    S.toast('Escolha ao menos um equipamento.', 'warning');
                    return;
                }
                try {
                    S.loading(true);
                    var r = await S.api('/destinacao/lotes', {
                        method: 'POST',
                        body: {
                            destino: document.getElementById('dst-lote-destino').value,
                            justificativa: document.getElementById('dst-lote-just').value.trim(),
                            seriais: seriais
                        }
                    });
                    S.closeModal();
                    S.toast('Lote ' + r.numero + ' formado.', 'success');
                    vista.lote = r.numero;
                    desenhar(c);
                } catch (e) {
                    S.toast(e.message, 'danger');
                } finally { S.loading(false); }
            })
        ]);
    }

    async function telaLoteDetalhe(c) {
        c.innerHTML = carregando();
        var d;
        try { d = await S.api('/destinacao/lotes/' + encodeURIComponent(vista.lote)); }
        catch (e) { return falha(c, e); }

        var acoes = [botao('Voltar', 'btn-outline', function () {
            vista.lote = null; desenhar(c);
        })];
        if (d.estado !== 'CONCLUIDO') {
            acoes.push(botao('Concluir lote', 'btn-primary', function () {
                concluirLote(c, d);
            }));
        }

        c.innerHTML = '';
        c.appendChild(cabecalho(d.numero,
            d.destino_rotulo + ' · ' + d.itens.length + ' equipamento(s)', acoes));

        var layout = S.el('div', { className: 'sep-layout' });
        var esq = S.el('div');
        var dir = S.el('div');
        layout.appendChild(esq);
        layout.appendChild(dir);
        c.appendChild(layout);

        var itens = S.el('div', { className: 'card-body' });
        var t = S.el('table', { className: 'data-table' });
        t.innerHTML = '<thead><tr><th>Série</th><th>Modelo</th>' +
            '<th>Condição</th></tr></thead><tbody>' +
            d.itens.map(function (i) {
                return '<tr><td class="sep-serie">' + S.esc(i.serial) + '</td>' +
                    '<td>' + S.esc(i.modelo || '—') + '</td>' +
                    '<td>' + S.esc(i.condicao_fisica || '—') + '</td></tr>';
            }).join('') + '</tbody>';
        var w = S.el('div', { className: 'table-wrapper' });
        w.appendChild(t);
        itens.appendChild(w);
        esq.appendChild(cartao('Equipamentos do lote', itens));

        var doc = S.el('div', { className: 'card-body' });
        doc.innerHTML = '<p class="text-muted" style="font-size:13px;margin:0 0 12px">' +
            S.esc(d.documento_exigido
                ? 'O lote não fecha sem o ' + d.documento_exigido + '.'
                : 'Sem documento obrigatório para este destino.') + '</p>';
        d.anexos.forEach(function (a) {
            var l = S.el('div', { className: 'sep-total mt-1' });
            l.innerHTML = '<span>' + S.esc(a.nome) + '</span>';
            var link = S.el('a', {
                href: '/api/destinacao/anexos/' + a.id,
                target: '_blank', className: 'btn btn-outline btn-sm',
                textContent: 'Abrir'
            });
            l.appendChild(link);
            doc.appendChild(l);
        });
        if (d.estado !== 'CONCLUIDO' && d.documento_exigido) {
            var envio = S.el('div', { className: 'form-group mt-3' });
            envio.innerHTML = '<label for="dst-doc">Anexar ' +
                S.esc(d.documento_exigido.toLowerCase()) + '</label>' +
                '<input id="dst-doc" type="file" class="form-control" ' +
                'accept=".pdf,.jpg,.jpeg,.png">';
            doc.appendChild(envio);
            var b = S.el('div', { className: 'btn-row mt-2' });
            b.appendChild(botao('Enviar documento', 'btn-outline', function () {
                enviarDoc(c, d);
            }));
            doc.appendChild(b);
        }
        dir.appendChild(cartao('Documento', doc));

        var info = S.el('div', { className: 'card-body' });
        info.innerHTML = par('Justificativa', d.justificativa || '—') +
            par('Aberto por', d.aberto_por);
        dir.appendChild(cartao('Lote', info));
    }

    async function enviarDoc(c, d) {
        var arq = document.getElementById('dst-doc');
        if (!arq || !arq.files || !arq.files[0]) {
            S.toast('Escolha o arquivo.', 'warning');
            return;
        }
        var dados = new FormData();
        dados.append('tipo', DOC_DO_DESTINO[d.destino][0]);
        dados.append('arquivo', arq.files[0]);
        try {
            S.loading(true);
            await S.api('/destinacao/lotes/' + encodeURIComponent(d.numero) +
                        '/anexos', { method: 'POST', body: dados });
            S.toast('Documento anexado.', 'success');
            desenhar(c);
        } catch (e) {
            S.toast(e.message, 'danger');
        } finally { S.loading(false); }
    }

    function concluirLote(c, d) {
        var corpo = S.el('div');
        if (d.destino === 'VENDA') {
            corpo.innerHTML =
                campoForm('Comprador', 'dst-comprador') +
                campoForm('Valor estimado', 'dst-vest', 'number') +
                campoForm('Valor realizado', 'dst-vreal', 'number') +
                campoForm('NF de venda', 'dst-nf');
        } else if (d.destino === 'DESCARTE') {
            corpo.innerHTML =
                campoForm('Fornecedor', 'dst-fornec') +
                campoForm('Tipo de resíduo', 'dst-residuo') +
                campoForm('Peso (kg)', 'dst-peso', 'number');
        } else {
            corpo.innerHTML =
                campoForm('Entidade destinatária', 'dst-entidade') +
                campoForm('Quem recebeu', 'dst-recebedor');
        }
        S.openModal('Concluir ' + d.numero, corpo, [
            botao('Cancelar', 'btn-outline', S.closeModal),
            botao('Concluir', 'btn-primary', async function () {
                function v(id) {
                    var e = document.getElementById(id);
                    return e ? e.value.trim() : '';
                }
                function n(id) {
                    var x = v(id);
                    return x === '' ? null : parseFloat(x);
                }
                try {
                    S.loading(true);
                    await S.api('/destinacao/lotes/' +
                                encodeURIComponent(d.numero) + '/concluir', {
                        method: 'POST',
                        body: {
                            comprador: v('dst-comprador'),
                            valor_estimado: n('dst-vest'),
                            valor_realizado: n('dst-vreal'),
                            nf_venda: v('dst-nf'),
                            fornecedor: v('dst-fornec'),
                            tipo_residuo: v('dst-residuo'),
                            peso_kg: n('dst-peso'),
                            entidade: v('dst-entidade'),
                            recebedor: v('dst-recebedor')
                        }
                    });
                    S.closeModal();
                    S.toast('Lote concluído.', 'success');
                    desenhar(c);
                } catch (e) {
                    S.toast(e.message, 'danger');
                } finally { S.loading(false); }
            })
        ]);
    }

    /* ── Peças ─────────────────────────────────────────────────── */
    function campoForm(rot, id, tipo) {
        return '<div class="form-group mt-2"><label for="' + id + '">' +
            S.esc(rot) + '</label><input id="' + id + '" type="' +
            (tipo || 'text') + '" class="form-control"></div>';
    }

    function lista(linhas, vazio) {
        var corpo = S.el('div', { className: 'card-body' });
        if (!linhas.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio', textContent: vazio }));
            return corpo;
        }
        var t = S.el('table', { className: 'data-table' });
        t.innerHTML = '<thead><tr><th>Série</th><th>Modelo</th><th>Origem</th>' +
            '<th>Parado há</th></tr></thead><tbody>' +
            linhas.map(function (x) {
                return '<tr><td class="sep-serie">' + S.esc(x.serial) + '</td>' +
                    '<td>' + S.esc(x.modelo || '—') + '</td>' +
                    '<td>' + S.esc(x.origem || '—') + '</td>' +
                    '<td>' + S.esc(duracao(x.segundos)) + '</td></tr>';
            }).join('') + '</tbody>';
        var w = S.el('div', { className: 'table-wrapper' });
        w.appendChild(t);
        corpo.appendChild(w);
        return corpo;
    }

    function duracao(seg) {
        if (!seg && seg !== 0) return '—';
        var h = Math.floor(seg / 3600), m = Math.floor((seg % 3600) / 60);
        if (h >= 24) return Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
        return h ? h + 'h ' + m + 'm' : m + 'm';
    }

    function par(rot, valor) {
        return '<div><label style="font-size:12px;color:var(--text-secondary)">' +
            S.esc(rot) + '</label><div>' + S.esc(valor) + '</div></div>';
    }

    function carregando() {
        return '<div class="spinner-inline"><span class="spinner spinner-sm">' +
            '</span> Carregando...</div>';
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
        texto.appendChild(S.el('p', { className: 'text-muted',
                                      style: 'margin:0;font-size:13px',
                                      textContent: sub }));
        topo.appendChild(texto);
        var caixa = S.el('div', { className: 'btn-row' });
        (acoes || []).forEach(function (b) { caixa.appendChild(b); });
        topo.appendChild(caixa);
        return topo;
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
