/* ================================================================
   Módulo: Preparação (A06, A07, A08.2)

   Três estações entre a bancada e o estoque. Mesma mecânica das
   bancadas — bipe com Enter, fila vinda da trilha —, formulários
   diferentes.

   O formulário muda com a estação porque as perguntas são outras:
   configurar pergunta a baseline, montar pergunta a carcaça,
   internalizar pergunta o endereço.
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};

(function () {
    var S = null;
    var ESTACOES = [
        ['CONFIGURACAO', 'Configuração'],
        ['MONTAGEM', 'Montagem de sled'],
        ['INTERNALIZACAO', 'Internalização']
    ];
    var ROTA = { configuracao: 'CONFIGURACAO', montagem: 'MONTAGEM',
                 internalizacao: 'INTERNALIZACAO' };
    var SUB = { CONFIGURACAO: 'configuracao', MONTAGEM: 'montagem',
                INTERNALIZACAO: 'internalizacao' };

    var vista = { estacao: 'CONFIGURACAO', ativo: null, baselines: [] };

    window.SPARE_MODULES.preparacao = {
        render: function (container, sub) {
            S = window.SPARE;
            vista.estacao = ROTA[sub] || 'CONFIGURACAO';
            vista.ativo = null;
            S.tabs(ESTACOES.map(function (e) { return [SUB[e[0]], e[1]]; }),
                   SUB[vista.estacao], 'preparacao');
            desenhar(container);
        }
    };

    async function desenhar(c) {
        c.innerHTML = '<div class="spinner-inline">' +
            '<span class="spinner spinner-sm"></span> Carregando...</div>';
        var d;
        try {
            d = await S.api('/preparacao/fila?estacao=' + vista.estacao);
            if (vista.estacao === 'CONFIGURACAO' && !vista.baselines.length) {
                vista.baselines = (await S.api('/preparacao/baselines')).baselines;
            }
        } catch (e) {
            c.innerHTML = '';
            c.appendChild(S.el('div', { className: 'alert alert-danger',
                                        textContent: e.message }));
            return;
        }

        c.innerHTML = '';
        c.appendChild(cabecalho(d.rotulo, textoDaEstacao()));
        c.appendChild(indicadores([
            ['Na fila', d.aguardando.length, 'accent-gold'],
            ['Em preparação', d.em_curso.length, 'accent-teal']
        ]));
        c.appendChild(barraBipe(c));
        if (vista.ativo) c.appendChild(painel(c));
        c.appendChild(cartao('Aguardando', lista(d.aguardando,
            'Nenhum equipamento nesta fila.')));
        if (d.em_curso.length) {
            c.appendChild(cartao('Em preparação agora', lista(d.em_curso, '')));
        }
    }

    function textoDaEstacao() {
        return ({
            CONFIGURACAO: 'Coletor apto recebe a baseline da frota antes de ' +
                          'voltar ao estoque.',
            MONTAGEM: 'Componentes entram, um sled sai. É o único processo ' +
                      'que transforma ativos.',
            INTERNALIZACAO: 'Conferência e endereçamento. Daqui o equipamento ' +
                            'entra no saldo.'
        })[vista.estacao];
    }

    function barraBipe(c) {
        var corpo = S.el('div', { className: 'card-body' });
        var campo = S.el('input', {
            id: 'prp-serie', className: 'form-control form-control-inline',
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
            vista.ativo = await S.api('/preparacao/bipar', {
                method: 'POST',
                body: { serial: serie, estacao: vista.estacao }
            });
            desenhar(c);
        } catch (e) {
            S.toast(e.message, 'danger');
            campo.select();
        } finally { S.loading(false); }
    }

    function painel(c) {
        var a = vista.ativo;
        var corpo = S.el('div', { className: 'card-body' });
        var ficha = S.el('div', { className: 'detail-grid mb-3' });
        ficha.innerHTML = campo('Série', a.serial) +
            campo('Modelo', a.modelo || '—') + campo('Origem', a.origem || '—');
        corpo.appendChild(ficha);
        corpo.appendChild(formulario());

        var acoes = S.el('div', { className: 'btn-row mt-3' });
        acoes.appendChild(botao('Concluir', 'btn-primary', function () {
            concluir(c);
        }));
        corpo.appendChild(acoes);
        return cartao(vista.ativo.serial + ' em preparação', corpo);
    }

    function formulario() {
        var f = S.el('div');
        if (vista.estacao === 'CONFIGURACAO') {
            f.innerHTML =
                '<div class="form-grid cols-2">' +
                  '<div class="form-group"><label for="prp-baseline">' +
                    'Baseline aplicada</label>' +
                    '<select id="prp-baseline" class="form-control">' +
                      vista.baselines.map(function (b) {
                          return '<option value="' + b.id + '"' +
                              (b.vigente ? ' selected' : '') + '>' +
                              S.esc(b.versao) + (b.vigente ? ' (vigente)' : '') +
                              '</option>';
                      }).join('') +
                    '</select>' +
                    (vista.baselines.length ? '' :
                      '<span class="text-muted" style="font-size:11.5px">' +
                      'Nenhuma baseline cadastrada. Um administrador precisa ' +
                      'criar a primeira em Parâmetros.</span>') +
                  '</div>' +
                  '<div class="form-group"><label for="prp-firmware">Firmware' +
                    '</label><input id="prp-firmware" class="form-control"></div>' +
                '</div>' + testeFuncional();
        } else if (vista.estacao === 'MONTAGEM') {
            f.innerHTML =
                '<div class="form-grid cols-2">' +
                  '<div class="form-group"><label for="prp-carcaca">' +
                    'Carcaça utilizada (série)</label>' +
                    '<input id="prp-carcaca" class="form-control"></div>' +
                  '<div class="form-group"><label for="prp-comp">' +
                    'Componentes consumidos</label>' +
                    '<input id="prp-comp" class="form-control" ' +
                           'placeholder="séries separadas por vírgula">' +
                    '<span class="text-muted" style="font-size:11.5px">' +
                    'Cada série informada é encerrada na trilha como ' +
                    'consumida nesta montagem.</span></div>' +
                '</div>' + testeFuncional();
        } else {
            f.innerHTML =
                '<div class="form-group mb-3"><label>Conferência física</label>' +
                  '<div class="form-row-inline">' +
                    '<label style="font-weight:400"><input type="radio" ' +
                      'name="prp-conf" id="prp-conf-ok" value="sim" checked> Passou</label>' +
                    '<label style="font-weight:400"><input type="radio" ' +
                      'name="prp-conf" value="nao"> Reprovou</label>' +
                  '</div></div>' +
                '<div class="form-grid cols-2">' +
                  '<div class="form-group"><label for="prp-endereco">' +
                    'Endereço de estoque</label>' +
                    '<input id="prp-endereco" class="form-control" ' +
                           'placeholder="espaço e corredor"></div>' +
                  '<div class="form-group"><label for="prp-obs">Observação' +
                    '</label><input id="prp-obs" class="form-control"></div>' +
                '</div>' +
                '<p class="text-muted mt-2" style="font-size:12px">Conferência ' +
                'reprovada devolve o equipamento para a bancada, com o motivo.</p>';
        }
        return f;
    }

    function testeFuncional() {
        return '<div class="form-group mt-3"><label>Teste funcional</label>' +
            '<div class="form-row-inline">' +
              '<label style="font-weight:400"><input type="radio" name="prp-teste" ' +
                'id="prp-teste-ok" value="sim" checked> Passou</label>' +
              '<label style="font-weight:400"><input type="radio" ' +
                'name="prp-teste" value="nao"> Não passou</label>' +
            '</div></div>';
    }

    async function concluir(c) {
        function v(id) {
            var e = document.getElementById(id);
            return e ? e.value.trim() : '';
        }
        function marcado(id) {
            var e = document.getElementById(id);
            return e ? e.checked : null;
        }
        var corpo = { serial: vista.ativo.serial, estacao: vista.estacao };
        if (vista.estacao === 'CONFIGURACAO') {
            corpo.baseline_id = parseInt(v('prp-baseline'), 10) || null;
            corpo.firmware = v('prp-firmware');
            corpo.teste_funcional = marcado('prp-teste-ok');
        } else if (vista.estacao === 'MONTAGEM') {
            corpo.carcaca = v('prp-carcaca');
            corpo.componentes = v('prp-comp').split(',').map(function (x) {
                return x.trim();
            }).filter(Boolean);
            corpo.teste_funcional = marcado('prp-teste-ok');
        } else {
            corpo.conferencia_ok = marcado('prp-conf-ok');
            corpo.endereco = v('prp-endereco');
            corpo.observacao = v('prp-obs');
        }
        try {
            S.loading(true);
            var r = await S.api('/preparacao/concluir', { method: 'POST', body: corpo });
            S.toast(r.devolvido
                ? r.serial + ' devolvido para a bancada.'
                : r.serial + ' seguiu para ' + r.proximo_estado + '.', 'success');
            vista.ativo = null;
            desenhar(c);
        } catch (e) {
            S.toast(e.message, 'danger');
        } finally { S.loading(false); }
    }

    /* ── Peças ─────────────────────────────────────────────────── */
    function lista(linhas, vazio) {
        var corpo = S.el('div', { className: 'card-body' });
        if (!linhas.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio', textContent: vazio }));
            return corpo;
        }
        var t = S.el('table', { className: 'data-table' });
        t.innerHTML = '<thead><tr><th>Série</th><th>Modelo</th>' +
            '<th>Parado há</th><th>Com</th></tr></thead><tbody>' +
            linhas.map(function (x) {
                return '<tr><td class="sep-serie">' + S.esc(x.serial) + '</td>' +
                    '<td>' + S.esc(x.modelo || '—') + '</td>' +
                    '<td>' + S.esc(duracao(x.segundos)) + '</td>' +
                    '<td>' + S.esc(x.usuario || '—') + '</td></tr>';
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

    function campo(rot, valor) {
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
