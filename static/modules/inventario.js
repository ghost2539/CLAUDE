/* ================================================================
   Módulo: Inventário e Contagem (A18)

   O token é o ciclo: um recorte do estoque, o retrato do ServiceNow
   congelado na abertura, e a contagem bipe a bipe. Três telas:

     ciclos   o que está aberto e o que já fechou
     novo     escolher o recorte e ver quantos itens ele tem antes de abrir
     contagem bipar, ver o que falta por corredor, fechar
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};

(function () {
    var S = null;
    var vista = { tela: 'ciclos', numero: null, local: '' };

    window.SPARE_MODULES.inventario = {
        render: function (container) {
            S = window.SPARE;
            vista.tela = 'ciclos';
            vista.numero = null;
            desenhar(container);
        }
    };

    function desenhar(c) {
        if (vista.tela === 'novo') return telaNovo(c);
        if (vista.tela === 'detalhe') return telaDetalhe(c);
        return telaCiclos(c);
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
        return new Date(iso).toLocaleDateString('pt-BR');
    }
    function erro(c, e) {
        c.innerHTML = '';
        c.appendChild(S.el('div', { className: 'alert alert-danger', textContent: e.message || String(e) }));
    }
    function carregando(c, t) {
        c.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> ' + S.esc(t || 'Carregando...') + '</div>';
    }
    function selo(estado) {
        return ({ AG_CONTAGEM_INV: 'badge-gold', EX_CONTAGEM_INV: 'badge-teal', CONFERIDO_INV: 'badge-success',
                  DIVERGENTE_INV: 'badge-danger', CANCELADO_INV: 'badge-default' })[estado] || 'badge-default';
    }

    /* ============================================================
       Tela 1 — os ciclos
       ============================================================ */
    async function telaCiclos(c) {
        carregando(c);
        var d;
        try { d = await S.api('/inventario/ciclos'); } catch (e) { return erro(c, e); }

        c.innerHTML = '';
        c.appendChild(cabecalho('Inventário', '',
            [botao('Novo ciclo', 'btn-primary', function () { vista.tela = 'novo'; desenhar(c); })]));
        var n = d.contagem || {};
        c.appendChild(indicadores([
            ['Aguardando contagem', n.AG_CONTAGEM_INV || 0, 'accent-gold'],
            ['Em contagem', n.EX_CONTAGEM_INV || 0, 'accent-teal'],
            ['Conferidos', n.CONFERIDO_INV || 0, 'accent-green'],
            ['Divergentes', n.DIVERGENTE_INV || 0, 'accent-orange']
        ]));

        var corpo = S.el('div', { className: 'card-body' });
        if (!d.ciclos.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio', textContent: 'Nenhum ciclo ainda.' }));
        } else {
            var t = S.el('table', { className: 'data-table' });
            t.innerHTML = '<thead><tr>' + ['Ciclo', 'Recorte', 'Esperados', 'Contados', 'Sobras', 'Faltantes', 'Situação', 'Aberto', '']
                .map(function (x) { return '<th>' + S.esc(x) + '</th>'; }).join('') + '</tr></thead>';
            var tb = S.el('tbody');
            d.ciclos.forEach(function (x) {
                var tr = S.el('tr');
                tr.innerHTML = '<td><b>' + S.esc(x.numero) + '</b></td>' +
                    '<td>' + S.esc(x.descricao) + (x.prefixo ? ' <span class="text-muted">' + S.esc(x.prefixo) + '</span>' : '') + '</td>' +
                    '<td>' + x.esperados + '</td><td>' + x.casados + '</td>' +
                    '<td>' + (x.sobras ? '<span class="badge badge-warning">' + x.sobras + '</span>' : '0') + '</td>' +
                    '<td>' + (x.faltantes == null ? '—' : (x.faltantes ? '<span class="badge badge-danger">' + x.faltantes + '</span>' : '0')) + '</td>' +
                    '<td><span class="badge ' + selo(x.estado) + '">' + S.esc(x.estado_rotulo) + '</span></td>' +
                    '<td>' + S.esc(data(x.aberto_em)) + ' · ' + S.esc(x.aberto_por) + '</td>';
                var td = S.el('td');
                td.appendChild(botao('Abrir', 'btn-outline btn-sm', function () { vista.tela = 'detalhe'; vista.numero = x.numero; desenhar(c); }));
                tr.appendChild(td);
                tb.appendChild(tr);
            });
            t.appendChild(tb);
            var wrap = S.el('div', { className: 'table-wrapper' });
            wrap.appendChild(t);
            corpo.appendChild(wrap);
        }
        c.appendChild(cartao('Ciclos de contagem', corpo));
    }

    /* ============================================================
       Tela 2 — novo ciclo
       ============================================================ */
    function telaNovo(c) {
        c.innerHTML = '';
        c.appendChild(cabecalho('Novo ciclo de contagem', '',
            [botao('Cancelar', 'btn-outline', function () { vista.tela = 'ciclos'; desenhar(c); })]));
        var corpo = S.el('div', { className: 'card-body' });
        corpo.innerHTML =
            '<div class="form-grid cols-2">' +
              '<div class="form-group"><label for="inv-prefixo">Começo do espaço e corredor</label>' +
                '<input id="inv-prefixo" class="form-control" placeholder="REP, IN, REP-A03… (vazio = depósito inteiro)" autocomplete="off"></div>' +
              '<div class="form-group"><label for="inv-desc">Descrição</label>' +
                '<input id="inv-desc" class="form-control" placeholder="Contagem mensal do corredor de reposição"></div>' +
            '</div>' +
            '<div id="inv-previa" class="mt-2"></div>';
        var rodape = S.el('div', { className: 'card-footer' });
        var previa = botao('Ver quantos itens', 'btn-secondary', async function () {
            var alvo = corpo.querySelector('#inv-previa');
            alvo.innerHTML = '<span class="text-muted">Lendo…</span>';
            try {
                var p = await S.api('/inventario/previa?prefixo=' + encodeURIComponent(corpo.querySelector('#inv-prefixo').value.trim()));
                var html = '<div class="sep-total"><span>Itens no recorte</span><b>' + p.total + '</b></div>' +
                    '';
                if (p.por_local.length) {
                    html += '<div class="inv-locais">' + p.por_local.map(function (l) {
                        return '<span><b>' + S.esc(l.local) + '</b> ' + l.quantidade + '</span>';
                    }).join('') + '</div>';
                }
                alvo.innerHTML = html;
                abrir.disabled = !p.total;
            } catch (e) { alvo.innerHTML = ''; S.toast(e.message, 'danger'); }
        });
        var abrir = botao('Abrir ciclo', 'btn-primary', async function () {
            try {
                S.loading(true);
                var d = await S.api('/inventario/ciclos', { method: 'POST', body: {
                    prefixo: corpo.querySelector('#inv-prefixo').value.trim(),
                    descricao: corpo.querySelector('#inv-desc').value.trim() } });
                S.toast('Ciclo ' + d.numero + ' aberto com ' + d.esperados + ' itens.', 'success');
                vista.tela = 'detalhe'; vista.numero = d.numero; desenhar(c);
            } catch (e) { S.toast(e.message, 'danger'); } finally { S.loading(false); }
        });
        rodape.appendChild(previa);
        rodape.appendChild(abrir);
        var card = cartao('Recorte', corpo);
        card.appendChild(rodape);
        c.appendChild(card);
        corpo.querySelector('#inv-prefixo').focus();
    }

    /* ============================================================
       Tela 3 — a contagem
       ============================================================ */
    async function telaDetalhe(c) {
        carregando(c);
        var d;
        try { d = await S.api('/inventario/ciclos/' + encodeURIComponent(vista.numero)); } catch (e) { return erro(c, e); }
        var cmp = d.comparacao;

        c.innerHTML = '';
        var acoes = [botao('Voltar', 'btn-outline', function () { vista.tela = 'ciclos'; desenhar(c); })];
        if (d.estado === 'AG_CONTAGEM_INV') {
            acoes.push(botao('Iniciar contagem', 'btn-primary', function () { acao(c, 'iniciar'); }));
        } else if (d.estado === 'EX_CONTAGEM_INV') {
            acoes.push(botao(cmp.divergente ? 'Concluir como divergente' : 'Concluir contagem', 'btn-primary', function () { modalConcluir(d, c); }));
        }
        if (!d.encerrado) {
            acoes.push(botao('Cancelar', 'btn-outline btn-sm', function () {
                modalTexto('Cancelar ciclo', 'Motivo', function (t) { return acao(c, 'cancelar', { motivo: t }); });
            }));
        }
        c.appendChild(cabecalho(d.descricao || d.numero,
            d.numero + (d.prefixo ? ' · ' + d.prefixo : '') + ' · aberto por ' + d.aberto_por + ' · ' + duracao(d.segundos_uteis) + ' em curso', acoes));

        c.appendChild(indicadores([
            ['Esperados', cmp.esperados, 'accent-gold'],
            ['Contados', cmp.ok.length, 'accent-green'],
            ['Faltam contar', cmp.faltantes.length, cmp.faltantes.length ? 'accent-orange' : 'accent-teal'],
            ['Sobras', cmp.sobras.length, cmp.sobras.length ? 'accent-orange' : 'accent-teal']
        ]));

        var layout = S.el('div', { className: 'sep-layout' });
        var esquerda = S.el('div'), lateral = S.el('div');
        layout.appendChild(esquerda);
        layout.appendChild(lateral);
        c.appendChild(layout);

        var corpo = S.el('div', { className: 'card-body' });
        var trilho = S.el('div', { className: 'sep-trilho mb-3' });
        var pct = cmp.esperados ? Math.round((cmp.ok.length / cmp.esperados) * 100) : 0;
        trilho.innerHTML = '<div class="sep-fita" style="width:' + pct + '%"></div>';
        corpo.appendChild(trilho);
        if (d.estado === 'EX_CONTAGEM_INV') corpo.appendChild(barraBipe(d, c));

        /* Por corredor: é assim que se anda pelo depósito. */
        var locais = S.el('div', { className: 'inv-locais mb-3' });
        cmp.por_local.forEach(function (l) {
            var done = l.contados >= l.esperados;
            locais.appendChild(S.el('span', { className: done ? 'inv-local-ok' : '',
                innerHTML: '<b>' + S.esc(l.local) + '</b> ' + l.contados + '/' + l.esperados }));
        });
        corpo.appendChild(locais);
        corpo.appendChild(tabelaComparacao(cmp, d.encerrado));
        esquerda.appendChild(cartao('Contagem · ' + cmp.ok.length + ' de ' + cmp.esperados, corpo));

        var info = S.el('div', { className: 'card-body' });
        info.innerHTML =
            '<div class="sep-total"><span>Situação</span><b>' + S.esc(d.estado_rotulo) + '</b></div>' +
            (d.contado_por ? '<div class="sep-total mt-2"><span>Contando</span><b>' + S.esc(d.contado_por) + '</b></div>' : '') +
            (d.encerrado_em ? '<div class="sep-total mt-2"><span>Encerrado</span><b>' + S.esc(data(d.encerrado_em)) + '</b></div>' : '');
        if (d.observacao) info.appendChild(S.el('p', { className: 'text-muted prj-obs', textContent: d.observacao }));
        lateral.appendChild(cartao('Resumo', info));
    }

    function tabelaComparacao(cmp, encerrado) {
        var wrap = S.el('div', { className: 'table-wrapper' });
        var t = S.el('table', { className: 'data-table' });
        var html = '<thead><tr><th>Série</th><th>Etiqueta</th><th>Modelo</th><th>Corredor (retrato)</th><th>Situação</th></tr></thead><tbody>';
        cmp.sobras.forEach(function (r) {
            html += '<tr><td class="sep-serie">' + S.esc(r.serial) + '</td><td>—</td><td class="text-muted">' + S.esc(r.situacao || 'sem informação') + '</td>' +
                '<td>' + S.esc(r.local || '—') + ' <span class="text-muted">(contado)</span></td>' +
                '<td><span class="badge badge-warning">Sobra</span></td></tr>';
        });
        cmp.faltantes.forEach(function (e) {
            html += '<tr><td class="sep-serie">' + S.esc(e.serial || '—') + '</td><td>' + S.esc(e.etiqueta || '—') + '</td><td>' + S.esc(e.modelo || '—') + '</td>' +
                '<td>' + S.esc(e.local || '—') + '</td>' +
                '<td><span class="badge ' + (encerrado ? 'badge-danger' : 'badge-default') + '">' + (encerrado ? 'Faltante' : 'Pendente') + '</span></td></tr>';
        });
        cmp.ok.forEach(function (e) {
            html += '<tr><td class="sep-serie">' + S.esc(e.serial || '—') + '</td><td>' + S.esc(e.etiqueta || '—') + '</td><td>' + S.esc(e.modelo || '—') + '</td>' +
                '<td>' + S.esc(e.local || '—') + (e.local_contado && e.local_contado !== e.local ? ' <span class="badge badge-info">visto em ' + S.esc(e.local_contado) + '</span>' : '') + '</td>' +
                '<td><span class="badge badge-success">Contado</span></td></tr>';
        });
        t.innerHTML = html + '</tbody>';
        wrap.appendChild(t);
        return wrap;
    }

    function barraBipe(d, c) {
        var barra = S.el('div', { className: 'form-row-inline mb-3' });
        var local = S.el('input', { className: 'form-control form-control-inline', style: 'min-width:140px',
                                    placeholder: 'Corredor', value: vista.local, autocomplete: 'off' });
        var serie = S.el('input', { className: 'form-control form-control-inline', style: 'min-width:240px',
                                    placeholder: 'Bipe a série ou a etiqueta', autocomplete: 'off' });
        async function bipar() {
            var valor = serie.value.trim();
            if (!valor) return;
            vista.local = local.value.trim();
            try {
                var r = await S.api('/inventario/ciclos/' + encodeURIComponent(d.numero) + '/bipar',
                                    { method: 'POST', body: { serial: valor, local: vista.local } });
                S.toast(r.casou ? valor.toUpperCase() + ' contado.' : valor.toUpperCase() + ' não esperado (' + (r.situacao || 'sem informação') + ').',
                        r.casou ? 'success' : 'warning');
                desenhar(c);
            } catch (e) { S.toast(e.message, 'danger'); serie.select(); }
        }
        serie.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { ev.preventDefault(); bipar(); } });
        barra.appendChild(local);
        barra.appendChild(serie);
        barra.appendChild(botao('Confirmar', 'btn-secondary', bipar));
        setTimeout(function () { serie.focus(); }, 50);
        return barra;
    }

    function modalConcluir(d, c) {
        var cmp = d.comparacao;
        var corpo = S.el('div');
        if (cmp.divergente) {
            corpo.appendChild(S.el('div', { className: 'alert alert-warning',
                textContent: 'Faltam ' + cmp.faltantes.length + ' e sobram ' + cmp.sobras.length +
                             '.' }));
        }
        var obs = S.el('textarea', { className: 'form-control', rows: '3', placeholder: cmp.divergente ? 'Como foi a contagem (obrigatório)' : 'Observação (opcional)' });
        corpo.appendChild(S.el('label', { textContent: 'Observação' }));
        corpo.appendChild(obs);
        S.openModal(cmp.divergente ? 'Concluir como divergente' : 'Concluir contagem', corpo, rodapeModal('Concluir', function () {
            if (cmp.divergente && !obs.value.trim()) { S.toast('Descreva a contagem.', 'warning'); return; }
            S.closeModal();
            acao(c, 'concluir', { observacao: obs.value.trim() });
        }));
        setTimeout(function () { obs.focus(); }, 50);
    }

    function modalTexto(titulo, rotulo, aoConfirmar) {
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
    function rodapeModal(texto, aoConfirmar) {
        return [botao('Voltar', 'btn-outline', function () { S.closeModal(); }), botao(texto, 'btn-primary', aoConfirmar)];
    }

    async function acao(c, qual, body) {
        try {
            S.loading(true);
            var r = await S.api('/inventario/ciclos/' + encodeURIComponent(vista.numero) + '/' + qual, { method: 'POST', body: body || undefined });
            S.toast(({ iniciar: 'Contagem iniciada.', concluir: r.estado === 'DIVERGENTE_INV' ? 'Ciclo fechado como divergente.' : 'Ciclo conferido.',
                       cancelar: 'Ciclo cancelado.' })[qual] || 'Feito.', r.estado === 'DIVERGENTE_INV' ? 'warning' : 'success');
            vista.tela = qual === 'cancelar' ? 'ciclos' : 'detalhe';
            desenhar(c);
        } catch (e) { S.toast(e.message, 'danger'); } finally { S.loading(false); }
    }

    /* ── Peças reaproveitadas ─────────────────────────────────── */
    function cabecalho(titulo, sub, acoes) {
        var topo = S.el('div', { style: 'display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:18px' });
        var texto = S.el('div');
        texto.appendChild(S.el('h2', { className: 'page-title', style: 'margin:0 0 4px', textContent: titulo }));
        if (sub) texto.appendChild(S.el('p', { className: 'text-muted', style: 'margin:0;font-size:13px', textContent: sub }));
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
            card.innerHTML = '<div class="stat-value">' + x[1] + '</div><div class="stat-label">' + S.esc(x[0]) + '</div>';
            grade.appendChild(card);
        });
        return grade;
    }
    function cartao(titulo, corpo) {
        var card = S.el('div', { className: 'card mb-3' });
        card.appendChild(S.el('div', { className: 'card-header', textContent: titulo }));
        card.appendChild(corpo);
        return card;
    }
    function botao(texto, classe, aoClicar) {
        return S.el('button', { className: 'btn ' + classe, type: 'button', textContent: texto, onClick: aoClicar });
    }
})();
