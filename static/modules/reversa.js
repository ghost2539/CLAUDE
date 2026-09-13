/* ================================================================
   Módulo: Logística Reversa (A17)

   O caminho de volta: a loja devolve equipamento ao CD. O token é a
   coleta — o que se combinou que viria, com prazo acordado e código de
   rastreio — e o indicador é esperado × recebido.

   Três telas, nunca duas ao mesmo tempo:

     fila     as coletas em aberto, com quem está atrasado em destaque
     nova     abrir uma coleta, começando pelo chamado
     detalhe  acompanhar (rastreio) e conferir (bipe a bipe)
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};

(function () {
    var S = null;
    var ABAS = [['abertas', 'Em aberto'], ['encerradas', 'Encerradas']];
    var FINAIS = ['CONFERIDA_REV', 'DIVERGENTE_REV', 'CANCELADA_REV'];
    var vista = { tela: 'fila', aba: 'abertas', numero: null };

    window.SPARE_MODULES.reversa = {
        render: function (container, sub) {
            S = window.SPARE;
            vista.aba = sub === 'encerradas' ? 'encerradas' : 'abertas';
            vista.tela = 'fila';
            vista.numero = null;
            S.tabs(ABAS, vista.aba, 'reversa');
            desenhar(container);
        }
    };

    function desenhar(c) {
        if (vista.tela === 'nova') return telaNova(c);
        if (vista.tela === 'detalhe') return telaDetalhe(c);
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

    function carregando(c) {
        c.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando...</div>';
    }

    function selo(estado) {
        return ({
            AG_POSTAGEM_REV: 'badge-default', EM_TRANSITO_REV: 'badge-info',
            AG_CONFERENCIA_REV: 'badge-gold', EX_CONFERENCIA_REV: 'badge-teal',
            CONFERIDA_REV: 'badge-success', DIVERGENTE_REV: 'badge-danger',
            CANCELADA_REV: 'badge-default'
        })[estado] || 'badge-default';
    }

    /* ============================================================
       Tela 1 — as coletas
       ============================================================ */
    async function telaFila(c) {
        carregando(c);
        var d;
        try {
            d = await S.api('/reversa/coletas');
        } catch (e) { return erro(c, e); }

        var linhas = d.coletas.filter(function (x) {
            return vista.aba === 'encerradas' ? x.encerrada : !x.encerrada;
        });

        c.innerHTML = '';
        c.appendChild(cabecalho('Logística reversa', '',
            [botao('Nova coleta', 'btn-primary', function () {
                vista.tela = 'nova'; desenhar(c);
            })]));

        var n = d.contagem || {};
        c.appendChild(indicadores([
            ['Com a loja', n.AG_POSTAGEM_REV || 0, 'accent-gold'],
            ['Em trânsito', n.EM_TRANSITO_REV || 0, 'accent-teal'],
            ['Para conferir', (n.AG_CONFERENCIA_REV || 0) + (n.EX_CONFERENCIA_REV || 0), 'accent-green'],
            ['Loja atrasada', d.postagem_atrasada || 0, 'accent-orange'],
            ['Faltantes em aberto', d.faltantes_abertos || 0, 'accent-orange']
        ]));

        var corpo = S.el('div', { className: 'card-body' });
        if (!linhas.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio',
                textContent: vista.aba === 'encerradas'
                    ? 'Nenhuma coleta encerrada ainda.'
                    : 'Nenhuma coleta em aberto.' }));
        } else {
            corpo.appendChild(tabela(linhas, c));
        }
        c.appendChild(cartao(vista.aba === 'encerradas' ? 'Coletas encerradas' : 'Coletas em aberto', corpo));
    }

    function tabela(linhas, c) {
        var t = S.el('table', { className: 'data-table' });
        t.innerHTML = '<thead><tr>' +
            ['Coleta', 'Loja', 'Chamado', 'Prazo da loja', 'Esperado × recebido',
             'Situação', 'Em curso', ''].map(function (x) {
                return '<th>' + S.esc(x) + '</th>';
            }).join('') + '</tr></thead>';
        var tbody = S.el('tbody');
        linhas.forEach(function (x) {
            var tr = S.el('tr');
            var cmp = x.recebidos + '/' + x.esperados;
            if (x.inesperados) cmp += ' <span class="text-muted">+' + x.inesperados + ' inesperado(s)</span>';
            if (x.estado === 'DIVERGENTE_REV' && x.faltantes) {
                cmp += ' <span class="badge badge-danger">' + x.faltantes + ' faltante(s)</span>';
            }
            tr.innerHTML =
                '<td><b>' + S.esc(x.numero) + '</b></td>' +
                '<td>' + S.esc(x.loja || '—') + '</td>' +
                '<td>' + S.esc(x.chamado) + '</td>' +
                '<td>' + S.esc(data(x.prazo_acordado)) +
                    (x.postagem_atrasada ? ' <span class="badge badge-danger">atrasada</span>' : '') +
                '</td>' +
                '<td>' + cmp + '</td>' +
                '<td><span class="badge ' + selo(x.estado) + '">' + S.esc(x.estado_rotulo) + '</span>' +
                    (x.conferencia_atrasada ? ' <span class="badge badge-danger">CD atrasado</span>' : '') +
                '</td>' +
                '<td>' + S.esc(duracao(x.segundos_uteis)) + '</td>';
            var td = S.el('td');
            td.appendChild(botao('Abrir', 'btn-outline btn-sm', function () {
                vista.tela = 'detalhe'; vista.numero = x.numero; desenhar(c);
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
       Tela 2 — nova coleta
       ============================================================ */
    function telaNova(c) {
        c.innerHTML = '';
        c.appendChild(cabecalho('Nova coleta', '',
            [botao('Cancelar', 'btn-outline', function () {
                vista.tela = 'fila'; desenhar(c);
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
              '<div class="form-group"><label for="rev-chamado">Incidente ou requisição</label>' +
                '<input id="rev-chamado" class="form-control" placeholder="INC0000000 ou RITM0000000" autocomplete="off"></div>' +
              '<div class="form-group"><label for="rev-prazo">Loja posta até</label>' +
                '<input id="rev-prazo" type="date" class="form-control"></div>' +
              '<div class="form-group"><label for="rev-codigo">Código de rastreio (se já postou)</label>' +
                '<input id="rev-codigo" class="form-control" placeholder="AA123456789BR" autocomplete="off"></div>' +
              '<div class="form-group"><label for="rev-motivo">Motivo</label>' +
                '<input id="rev-motivo" class="form-control" placeholder="Troca, defeito, fechamento…"></div>' +
            '</div>' +
            '<div id="rev-chamado-info" class="mt-2"></div>';
        esquerda.appendChild(cartao('Coleta', corpoCh));

        var corpoIt = S.el('div', { className: 'card-body' });
        corpoIt.innerHTML =
            '<div class="form-grid cols-2">' +
              '<div class="form-group"><label for="rev-serial">Série</label>' +
                '<input id="rev-serial" class="form-control" autocomplete="off"></div>' +
              '<div class="form-group"><label for="rev-etiqueta">Etiqueta</label>' +
                '<input id="rev-etiqueta" class="form-control" autocomplete="off"></div>' +
              '<div class="form-group"><label for="rev-modelo">Modelo</label>' +
                '<input id="rev-modelo" class="form-control"></div>' +
            '</div>';
        corpoIt.appendChild(botao('Incluir esperado', 'btn-secondary', function () {
            var serial = corpoIt.querySelector('#rev-serial').value.trim();
            var etiqueta = corpoIt.querySelector('#rev-etiqueta').value.trim();
            var modelo = corpoIt.querySelector('#rev-modelo').value.trim();
            if (!serial && !etiqueta && !modelo) { S.toast('Informe série, etiqueta ou modelo.', 'warning'); return; }
            itens.push({ serial: serial, etiqueta: etiqueta, modelo: modelo });
            ['#rev-serial', '#rev-etiqueta', '#rev-modelo'].forEach(function (q) {
                corpoIt.querySelector(q).value = '';
            });
            corpoIt.querySelector('#rev-serial').focus();
            pintarItens();
        }));
        esquerda.appendChild(cartao('Equipamentos esperados', corpoIt));

        function pintarItens() {
            direita.innerHTML = '';
            var corpo = S.el('div', { className: 'card-body' });
            if (!itens.length) {
                corpo.appendChild(S.el('p', { className: 'sep-vazio',
                    textContent: 'Nenhum equipamento.' }));
            } else {
                itens.forEach(function (i, k) {
                    var linha = S.el('div', { className: 'sep-linha' });
                    linha.innerHTML = '<div><div class="sep-serie" style="font-size:13px">' +
                        S.esc(i.serial || i.etiqueta || '—') + '</div>' +
                        '<div class="text-muted" style="font-size:11.5px">' +
                        S.esc(i.modelo || 'modelo não informado') +
                        (i.serial && i.etiqueta ? ' · etiqueta ' + S.esc(i.etiqueta) : '') +
                        '</div></div>';
                    linha.appendChild(botao('Remover', 'btn-outline btn-sm', function () {
                        itens.splice(k, 1); pintarItens();
                    }));
                    corpo.appendChild(linha);
                });
                var total = S.el('div', { className: 'sep-total mt-3' });
                total.innerHTML = '<span>Esperados</span><b>' + itens.length + '</b>';
                corpo.appendChild(total);
            }
            var rodape = S.el('div', { className: 'card-footer' });
            var abrir = botao('Abrir coleta', 'btn-primary', gravar);
            if (!chamado || !itens.length) abrir.disabled = true;
            rodape.appendChild(abrir);
            var card = cartao('Esperado', corpo);
            card.appendChild(rodape);
            direita.appendChild(card);
        }
        pintarItens();

        var campo = corpoCh.querySelector('#rev-chamado');
        var info = corpoCh.querySelector('#rev-chamado-info');
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
                info.appendChild(S.el('div', { className: 'alert alert-danger', textContent: e.message }));
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
                var d = await S.api('/reversa/coletas', {
                    method: 'POST',
                    body: {
                        chamado: chamado.chamado,
                        motivo: corpoCh.querySelector('#rev-motivo').value.trim(),
                        prazo_acordado: corpoCh.querySelector('#rev-prazo').value || null,
                        codigo_rastreio: corpoCh.querySelector('#rev-codigo').value.trim(),
                        itens: itens
                    }
                });
                S.toast('Coleta ' + d.numero + ' aberta.', 'success');
                vista.tela = 'detalhe'; vista.numero = d.numero; desenhar(c);
            } catch (e) {
                S.toast(e.message, 'danger');
            } finally {
                S.loading(false);
            }
        }
    }

    /* ============================================================
       Tela 3 — acompanhar e conferir
       ============================================================ */
    async function telaDetalhe(c) {
        carregando(c);
        var d;
        try {
            d = await S.api('/reversa/coletas/' + encodeURIComponent(vista.numero));
        } catch (e) { return erro(c, e); }
        var cmp = d.comparacao;

        c.innerHTML = '';
        var acoes = [botao('Voltar', 'btn-outline', function () {
            vista.tela = 'fila'; desenhar(c);
        })];
        if (d.estado === 'AG_POSTAGEM_REV') {
            acoes.push(botao('Informar postagem', 'btn-primary', function () { modalPostagem(d, c); }));
            acoes.push(botao('Chegou sem rastreio', 'btn-outline', function () { acao(c, 'conferir'); }));
        } else if (d.estado === 'EM_TRANSITO_REV') {
            acoes.push(botao('Atualizar rastreio', 'btn-secondary', function () { acao(c, 'rastrear'); }));
            acoes.push(botao('Chegou — conferir', 'btn-primary', function () { acao(c, 'conferir'); }));
        } else if (d.estado === 'AG_CONFERENCIA_REV') {
            acoes.push(botao('Assumir conferência', 'btn-primary', function () { acao(c, 'conferir'); }));
        } else if (d.estado === 'EX_CONFERENCIA_REV') {
            acoes.push(botao(cmp.divergente ? 'Concluir como divergente' : 'Concluir conferência',
                             'btn-primary', function () { modalConcluir(d, c); }));
        }
        if (!d.encerrada && d.estado !== 'EX_CONFERENCIA_REV') {
            acoes.push(botao('Cancelar', 'btn-outline btn-sm', function () {
                modalMotivo('Cancelar coleta', 'Motivo', function (motivo) {
                    return acao(c, 'cancelar', { motivo: motivo });
                });
            }));
        }

        c.appendChild(cabecalho(d.loja || d.numero,
            d.numero + ' · chamado ' + d.chamado + ' · aberta por ' + d.aberta_por +
            ' · ' + duracao(d.segundos_uteis) + ' em curso', acoes));

        c.appendChild(indicadores([
            ['Esperados', cmp.esperados, 'accent-gold'],
            ['Recebidos', cmp.recebidos, 'accent-green'],
            ['Faltantes', cmp.faltantes.length, cmp.faltantes.length ? 'accent-orange' : 'accent-teal'],
            ['Inesperados', cmp.inesperados.length, cmp.inesperados.length ? 'accent-orange' : 'accent-teal']
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
        if (d.estado === 'EX_CONFERENCIA_REV') corpo.appendChild(barraBipe(d, c));
        corpo.appendChild(tabelaComparacao(cmp));
        esquerda.appendChild(cartao('Esperado × recebido · ' + cmp.ok.length + ' de ' + cmp.esperados, corpo));

        var info = S.el('div', { className: 'card-body' });
        info.innerHTML =
            '<div class="sep-total"><span>Situação</span><b>' + S.esc(d.estado_rotulo) + '</b></div>' +
            '<div class="sep-total mt-2"><span>Loja posta até</span><b>' + S.esc(data(d.prazo_acordado)) +
                (d.postagem_atrasada ? ' <span class="badge badge-danger">atrasada</span>' : '') + '</b></div>' +
            '<div class="sep-total mt-2"><span>Rastreio</span><b class="sep-serie">' +
                S.esc(d.codigo_rastreio || '—') + '</b></div>' +
            (d.ultimo_evento ? '<p class="text-muted mt-2" style="font-size:12.5px;margin:0">' +
                S.esc(d.ultimo_evento) + (d.ultimo_evento_em ? ' · ' + S.esc(d.ultimo_evento_em) : '') + '</p>' : '') +
            (d.chegou_em ? '<div class="sep-total mt-2"><span>Chegou ao CD</span><b>' + S.esc(data(d.chegou_em)) + '</b></div>' : '') +
            (d.prazo_conferencia ? '<div class="sep-total mt-2"><span>Conferir até</span><b>' + S.esc(data(d.prazo_conferencia)) +
                (d.conferencia_atrasada ? ' <span class="badge badge-danger">atrasada</span>' : '') + '</b></div>' : '') +
            (d.conferida_por ? '<div class="sep-total mt-2"><span>Conferiu</span><b>' + S.esc(d.conferida_por) + '</b></div>' : '') +
            (d.motivo ? '<div class="sep-total mt-2"><span>Motivo</span><b>' + S.esc(d.motivo) + '</b></div>' : '');
        if (d.observacao) {
            info.appendChild(S.el('p', { className: 'text-muted prj-obs', textContent: d.observacao }));
        }
        lateral.appendChild(cartao('Resumo', info));
    }

    function tabelaComparacao(cmp) {
        var wrap = S.el('div', { className: 'table-wrapper' });
        var t = S.el('table', { className: 'data-table' });
        var html = '<thead><tr><th>Equipamento</th><th>Série / etiqueta</th><th>Recebido</th><th>Situação</th></tr></thead><tbody>';
        cmp.ok.forEach(function (e) {
            html += '<tr><td>' + S.esc(e.modelo || '—') + '</td>' +
                '<td class="sep-serie">' + S.esc(e.serial || e.etiqueta) + '</td>' +
                '<td>' + S.esc(e.recebido_por) + (e.origem === 'RECEBIMENTO' ? ' <span class="text-muted">· pelo recebimento</span>' : '') + '</td>' +
                '<td><span class="badge badge-success">Recebido</span></td></tr>';
        });
        cmp.faltantes.forEach(function (e) {
            html += '<tr><td>' + S.esc(e.modelo || '—') + '</td>' +
                '<td class="sep-serie">' + S.esc(e.serial || e.etiqueta || '—') + '</td>' +
                '<td class="sep-pendente">—</td>' +
                '<td><span class="badge badge-default">Aguardando</span></td></tr>';
        });
        cmp.inesperados.forEach(function (r) {
            html += '<tr><td class="text-muted">não esperado</td>' +
                '<td class="sep-serie">' + S.esc(r.serial) + '</td>' +
                '<td>' + S.esc(r.recebido_por) + '</td>' +
                '<td><span class="badge badge-warning">Inesperado</span></td></tr>';
        });
        t.innerHTML = html + '</tbody>';
        wrap.appendChild(t);
        return wrap;
    }

    function barraBipe(d, c) {
        var barra = S.el('div', { className: 'form-row-inline mb-3' });
        var serie = S.el('input', {
            className: 'form-control form-control-inline', style: 'min-width:260px',
            placeholder: 'Bipe a série ou a etiqueta', autocomplete: 'off'
        });
        async function bipar() {
            var valor = serie.value.trim();
            if (!valor) return;
            try {
                var r = await S.api('/reversa/coletas/' + encodeURIComponent(d.numero) + '/bipar',
                                    { method: 'POST', body: { serial: valor } });
                S.toast(r.casou ? valor.toUpperCase() + ' conferido.'
                                : valor.toUpperCase() + ' não esperado.',
                        r.casou ? 'success' : 'warning');
                desenhar(c);
            } catch (e) {
                S.toast(e.message, 'danger');
                serie.select();
            }
        }
        serie.addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter') { ev.preventDefault(); bipar(); }
        });
        barra.appendChild(serie);
        barra.appendChild(botao('Confirmar', 'btn-secondary', bipar));
        setTimeout(function () { serie.focus(); }, 50);
        return barra;
    }

    function modalPostagem(d, c) {
        var corpo = S.el('div');
        var campo = S.el('input', { className: 'form-control', placeholder: 'AA123456789BR', autocomplete: 'off' });
        corpo.appendChild(S.el('label', { textContent: 'Código de rastreio dos Correios' }));
        corpo.appendChild(campo);
        S.openModal('Informar postagem', corpo, rodapeModal('Registrar', function () {
            if (campo.value.trim().length < 10) { S.toast('Código inválido.', 'warning'); return; }
            S.closeModal();
            acao(c, 'postagem', { codigo_rastreio: campo.value.trim() });
        }));
        setTimeout(function () { campo.focus(); }, 50);
    }

    function modalConcluir(d, c) {
        var cmp = d.comparacao;
        var corpo = S.el('div');
        if (cmp.divergente) {
            corpo.appendChild(S.el('div', { className: 'alert alert-warning',
                textContent: 'Faltam ' + cmp.faltantes.length + ' e sobraram ' + cmp.inesperados.length +
                             '.' }));
        }
        var obs = S.el('textarea', { className: 'form-control', rows: '3',
            placeholder: cmp.divergente ? 'O que aconteceu (obrigatório)' : 'Observação (opcional)' });
        corpo.appendChild(S.el('label', { textContent: 'Observação' }));
        corpo.appendChild(obs);
        S.openModal(cmp.divergente ? 'Concluir como divergente' : 'Concluir conferência', corpo,
            rodapeModal('Concluir', function () {
                if (cmp.divergente && !obs.value.trim()) { S.toast('Descreva o que aconteceu.', 'warning'); return; }
                S.closeModal();
                acao(c, 'concluir', { observacao: obs.value.trim() });
            }));
        setTimeout(function () { obs.focus(); }, 50);
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

    function rodapeModal(texto, aoConfirmar) {
        return [
            botao('Voltar', 'btn-outline', function () { S.closeModal(); }),
            botao(texto, 'btn-primary', aoConfirmar)
        ];
    }

    async function acao(c, qual, body) {
        try {
            S.loading(true);
            var r = await S.api('/reversa/coletas/' + encodeURIComponent(vista.numero) + '/' + qual,
                                { method: 'POST', body: body || undefined });
            var msg = ({
                postagem: 'Postagem registrada.',
                rastrear: r.ultimo_evento ? 'Correios: ' + r.ultimo_evento : 'Rastreio atualizado.',
                conferir: 'Conferência assumida.',
                concluir: r.estado === 'DIVERGENTE_REV'
                    ? 'Coleta fechada como divergente.' : 'Coleta conferida.',
                cancelar: 'Coleta cancelada.'
            })[qual] || 'Feito.';
            S.toast(msg, r.estado === 'DIVERGENTE_REV' && qual === 'concluir' ? 'warning' : 'success');
            vista.tela = qual === 'cancelar' ? 'fila' : 'detalhe';
            desenhar(c);
        } catch (e) {
            S.toast(e.message, 'danger');
        } finally {
            S.loading(false);
        }
    }

    /* ── Peças reaproveitadas ─────────────────────────────────── */
    function cabecalho(titulo, sub, acoes) {
        var topo = S.el('div', {
            style: 'display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:18px'
        });
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
        var cab = S.el('div', { className: 'card-header' });
        if (typeof titulo === 'string') cab.textContent = titulo; else cab.appendChild(titulo);
        card.appendChild(cab);
        card.appendChild(corpo);
        return card;
    }

    function botao(texto, classe, aoClicar) {
        return S.el('button', { className: 'btn ' + classe, type: 'button', textContent: texto, onClick: aoClicar });
    }
})();
