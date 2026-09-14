/* ================================================================
   Módulo: Venda de Ativos (A11)

   A fila é o que está parado esperando venda; o ciclo é o trimestre em
   que a venda acontece. Ninguém assume ativo: ele cai na fila sozinho e
   só sai quando alguém bipa a série para dentro do ciclo aberto.
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};

(function () {
    var S = null;
    var ABAS = [['fila', 'Fila de venda'], ['ciclos', 'Ciclos'], ['painel', 'Painel']];
    var vista = { aba: 'fila', ciclo: null };

    window.SPARE_MODULES.venda = {
        render: function (container, sub) {
            S = window.SPARE;
            vista.aba = (sub && ['fila', 'ciclos', 'painel'].indexOf(sub) >= 0) ? sub : 'fila';
            S.tabs(ABAS, vista.aba, 'venda');
            if (vista.aba === 'fila') telaFila(container);
            else if (vista.aba === 'ciclos') telaCiclos(container);
            else telaPainel(container);
        }
    };

    /* ── Utilidades ─────────────────────────────────────────────── */
    function duracao(seg) {
        seg = Math.max(0, parseInt(seg || 0, 10));
        var d = Math.floor(seg / 86400), h = Math.floor((seg % 86400) / 3600);
        if (d) return d + 'd ' + h + 'h';
        var m = Math.floor((seg % 3600) / 60);
        return h ? h + 'h ' + m + 'm' : m + 'm';
    }

    function cartao(titulo, corpo) {
        var c = S.el('div', { className: 'card mb-3' });
        c.appendChild(S.el('div', { className: 'card-header', textContent: titulo }));
        c.appendChild(corpo);
        return c;
    }

    function indicadores(lista) {
        var g = S.el('div', { className: 'stat-grid mb-3' });
        lista.forEach(function (x) {
            var b = S.el('div', { className: 'stat-card ' + (x[2] || '') });
            b.innerHTML = '<div class="stat-value">' + S.esc(String(x[1])) + '</div>' +
                          '<div class="stat-label">' + S.esc(x[0]) + '</div>';
            g.appendChild(b);
        });
        return g;
    }

    function botao(texto, classe, acao) {
        var b = S.el('button', { className: 'btn ' + classe, textContent: texto });
        b.onclick = acao;
        return b;
    }

    async function carregar(c, rota) {
        c.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando...</div>';
        try {
            return await S.api(rota);
        } catch (e) {
            c.innerHTML = '';
            c.appendChild(S.el('div', { className: 'alert alert-danger', textContent: e.message }));
            return null;
        }
    }

    /* ── Fila ───────────────────────────────────────────────────── */
    async function telaFila(c) {
        var d = await carregar(c, '/venda/fila');
        if (!d) return;
        c.innerHTML = '';
        c.appendChild(S.el('h1', { className: 'page-title', textContent: 'Fila de venda' }));
        c.appendChild(indicadores([
            ['Aguardando venda', d.fila.length, 'accent-gold'],
            ['Parados demais', d.envelhecidos, 'accent-orange']
        ]));
        var corpo = S.el('div', { className: 'card-body' });
        if (!d.fila.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio',
                textContent: 'Nenhum ativo aguardando venda.' }));
        } else {
            corpo.appendChild(S.table([
                { key: 'serial', label: 'Série' },
                { key: 'modelo', label: 'Modelo' },
                { key: 'origem', label: 'Origem' },
                { key: 'segundos', label: 'Parado há', render: function (v) { return duracao(v); } },
                { key: 'envelhecido', label: '', html: true, render: function (v) {
                    return v ? '<span class="badge badge-gold">passou do ciclo</span>' : '';
                }}
            ], d.fila));
        }
        c.appendChild(cartao('Ativos aguardando o próximo ciclo', corpo));
    }

    /* ── Ciclos ─────────────────────────────────────────────────── */
    async function telaCiclos(c) {
        var d = await carregar(c, '/venda/ciclos');
        if (!d) return;
        c.innerHTML = '';
        c.appendChild(S.el('h1', { className: 'page-title', textContent: 'Ciclos de venda' }));

        var abertos = d.ciclos.filter(function (x) { return x.estado === 'ABERTO'; });
        var barra = S.el('div', { className: 'btn-row mb-3' });
        if (!abertos.length) {
            barra.appendChild(botao('Abrir ciclo do trimestre', 'btn-primary', function () {
                abrirCiclo(c);
            }));
        }
        c.appendChild(barra);

        if (!d.ciclos.length) {
            var vazio = S.el('div', { className: 'card-body' });
            vazio.appendChild(S.el('p', { className: 'sep-vazio',
                textContent: 'Nenhum ciclo de venda ainda.' }));
            c.appendChild(cartao('Ciclos', vazio));
            return;
        }
        d.ciclos.forEach(function (ciclo) {
            c.appendChild(cartaoCiclo(c, ciclo));
        });
    }

    function cartaoCiclo(c, ciclo) {
        var corpo = S.el('div', { className: 'card-body' });
        var ficha = S.el('div', { className: 'detail-grid mb-3' });
        ficha.innerHTML =
            '<div class="detail-item"><span class="detail-label">Situação</span>' +
            '<span class="detail-value">' + S.esc(ciclo.estado_rotulo) + '</span></div>' +
            '<div class="detail-item"><span class="detail-label">Ativos</span>' +
            '<span class="detail-value">' + ciclo.quantidade + '</span></div>' +
            (ciclo.comprador ? '<div class="detail-item"><span class="detail-label">Comprador</span>' +
                '<span class="detail-value">' + S.esc(ciclo.comprador) + '</span></div>' : '') +
            (ciclo.documento ? '<div class="detail-item"><span class="detail-label">Documento</span>' +
                '<span class="detail-value">' + S.esc(ciclo.documento) + '</span></div>' : '');
        corpo.appendChild(ficha);

        if (ciclo.estado === 'ABERTO') corpo.appendChild(barraBipe(c, ciclo));

        if (ciclo.itens.length) {
            corpo.appendChild(S.table([
                { key: 'serial', label: 'Série' },
                { key: 'modelo', label: 'Modelo' },
                { key: 'origem', label: 'Veio de' },
                { key: 'valor', label: 'Valor', render: function (v) { return S.money(v); } },
                { key: 'baixado', label: 'Baixado', render: function (v) { return v ? 'sim' : 'não'; } },
                { key: '_acoes', label: '', render: function (_, r) {
                    if (ciclo.estado === 'CONCLUIDO' || r.baixado) return S.el('span');
                    return botao('Tirar', 'btn-sm btn-outline', function () {
                        remover(c, ciclo.id, r.id);
                    });
                }}
            ], ciclo.itens));
        } else {
            corpo.appendChild(S.el('p', { className: 'sep-vazio',
                textContent: 'Nenhum ativo neste ciclo.' }));
        }

        var acoes = S.el('div', { className: 'btn-row mt-3' });
        if (ciclo.estado === 'ABERTO') {
            acoes.appendChild(botao('Fechar para negociação', 'btn-secondary', function () {
                mudarEstado(c, ciclo.id, 'NEGOCIACAO');
            }));
        }
        if (ciclo.estado === 'NEGOCIACAO') {
            acoes.appendChild(botao('Registrar venda e baixar', 'btn-primary', function () {
                concluir(c, ciclo);
            }));
            acoes.appendChild(botao('Reabrir', 'btn-outline', function () {
                mudarEstado(c, ciclo.id, 'ABERTO');
            }));
        }
        if (ciclo.estado === 'ABERTO' || ciclo.estado === 'NEGOCIACAO') {
            acoes.appendChild(botao('Cancelar ciclo', 'btn-danger', function () {
                mudarEstado(c, ciclo.id, 'CANCELADO');
            }));
        }
        if (acoes.children.length) corpo.appendChild(acoes);
        return cartao('Ciclo ' + ciclo.trimestre, corpo);
    }

    function barraBipe(c, ciclo) {
        var linha = S.el('div', { className: 'form-row-inline mb-3' });
        var campo = S.el('input', {
            className: 'form-control form-control-inline', autocomplete: 'off',
            style: 'min-width:260px', placeholder: 'Bipe a série para incluir no ciclo'
        });
        var valor = S.el('input', {
            className: 'form-control form-control-inline', type: 'number',
            step: '0.01', min: '0', style: 'max-width:140px', placeholder: 'Valor'
        });
        async function incluir() {
            var serie = campo.value.trim();
            if (!serie) return;
            try {
                S.loading(true);
                await S.api('/venda/ciclos/' + ciclo.id + '/itens', {
                    method: 'POST',
                    body: { serial: serie, valor: parseFloat(valor.value || '0') || 0 }
                });
                S.toast(serie + ' incluída no ciclo ' + ciclo.trimestre + '.', 'success');
                telaCiclos(c);
            } catch (e) {
                S.toast(e.message, 'danger');
                campo.select();
            } finally { S.loading(false); }
        }
        campo.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); incluir(); }
        });
        linha.appendChild(campo);
        linha.appendChild(valor);
        linha.appendChild(botao('Incluir', 'btn-secondary', incluir));
        return linha;
    }

    async function abrirCiclo(c) {
        var f = S.el('div');
        f.innerHTML =
            '<div class="form-group"><label for="vd-tri">Trimestre</label>' +
            '<input id="vd-tri" class="form-control" placeholder="Ex.: 2026-T3 (vazio = o atual)"></div>' +
            '<div class="form-group"><label for="vd-obs">Observação</label>' +
            '<textarea id="vd-obs" class="form-control" rows="2"></textarea></div>';
        var salvar = botao('Abrir ciclo', 'btn-primary', async function () {
            try {
                S.loading(true);
                await S.api('/venda/ciclos', {
                    method: 'POST',
                    body: {
                        trimestre: document.getElementById('vd-tri').value.trim(),
                        observacao: document.getElementById('vd-obs').value.trim()
                    }
                });
                S.closeModal();
                S.toast('Ciclo aberto.', 'success');
                telaCiclos(c);
            } catch (e) {
                S.toast(e.message, 'danger');
            } finally { S.loading(false); }
        });
        S.openModal('Abrir ciclo de venda', f, [salvar]);
    }

    async function mudarEstado(c, id, estado) {
        try {
            S.loading(true);
            await S.api('/venda/ciclos/' + id + '/estado', {
                method: 'PUT', body: { estado: estado }
            });
            telaCiclos(c);
        } catch (e) {
            S.toast(e.message, 'danger');
        } finally { S.loading(false); }
    }

    async function remover(c, cicloId, itemId) {
        try {
            S.loading(true);
            await S.api('/venda/ciclos/' + cicloId + '/itens/' + itemId, { method: 'DELETE' });
            S.toast('Ativo devolvido à fila de venda.', 'success');
            telaCiclos(c);
        } catch (e) {
            S.toast(e.message, 'danger');
        } finally { S.loading(false); }
    }

    async function concluir(c, ciclo) {
        var f = S.el('div');
        f.innerHTML =
            '<div class="form-group"><label for="vd-comp">Comprador <span style="color:#dc2626">*</span></label>' +
            '<input id="vd-comp" class="form-control"></div>' +
            '<div class="form-group"><label for="vd-doc">Documento da venda <span style="color:#dc2626">*</span></label>' +
            '<input id="vd-doc" class="form-control" placeholder="Nota, contrato ou ata"></div>' +
            '<div class="form-group"><label for="vd-val">Valor total</label>' +
            '<input id="vd-val" class="form-control" type="number" step="0.01" min="0"></div>';
        var salvar = botao('Registrar venda e baixar ' + ciclo.quantidade + ' ativo(s)',
                           'btn-primary', async function () {
            try {
                S.loading(true);
                var r = await S.api('/venda/ciclos/' + ciclo.id + '/concluir', {
                    method: 'POST',
                    body: {
                        comprador: document.getElementById('vd-comp').value.trim(),
                        documento: document.getElementById('vd-doc').value.trim(),
                        valor_total: parseFloat(document.getElementById('vd-val').value || '0') || 0
                    }
                });
                S.closeModal();
                var msg = r.baixados + ' ativo(s) baixado(s).';
                if (r.falhas && r.falhas.length) msg += ' ' + r.falhas.length + ' com problema: ' + r.falhas[0];
                S.toast(msg, r.falhas && r.falhas.length ? 'warning' : 'success');
                telaCiclos(c);
            } catch (e) {
                S.toast(e.message, 'danger');
            } finally { S.loading(false); }
        });
        S.openModal('Registrar a venda do ciclo ' + ciclo.trimestre, f, [salvar]);
    }

    /* ── Painel ─────────────────────────────────────────────────── */
    async function telaPainel(c) {
        var d = await carregar(c, '/venda/dashboard');
        if (!d) return;
        c.innerHTML = '';
        c.appendChild(S.el('h1', { className: 'page-title', textContent: 'Painel de venda' }));
        c.appendChild(indicadores([
            ['Ciclos concluídos', d.ciclos, 'accent-teal'],
            ['Ativos vendidos', d.itens, 'accent-gold'],
            ['Valor total', S.money(d.valor_total), 'accent-green'],
            ['Espera média até a venda', duracao(d.espera_media), 'accent-orange']
        ]));
        var corpo = S.el('div', { className: 'card-body' });
        if (!d.por_ciclo.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio', textContent: 'Nenhuma venda no período.' }));
        } else {
            corpo.appendChild(S.table([
                { key: 'trimestre', label: 'Trimestre' },
                { key: 'quantidade', label: 'Ativos' },
                { key: 'valor_total', label: 'Valor', render: function (v) { return S.money(v); } },
                { key: 'comprador', label: 'Comprador' },
                { key: 'concluido_em', label: 'Concluído em', render: function (v) {
                    return new Date(v).toLocaleDateString('pt-BR');
                }}
            ], d.por_ciclo));
        }
        c.appendChild(cartao('Vendas por ciclo', corpo));
    }
})();
