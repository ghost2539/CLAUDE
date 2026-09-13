/* ================================================================
   Módulo: Regularização de Ativo (A19)

   A divergência com dono e prazo. Duas telas:

     fila     tudo que está aberto, do prazo mais próximo ao mais
              folgado; sem dono aparece primeiro e em destaque
     detalhe  a divergência, o histórico, e a ação certa para o estado

   O que a tela exige, o processo exige: não sai da fila sem
   responsável e prazo, não fecha sem dizer como.
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};

(function () {
    var S = null;
    var ABAS = [['fila', 'Em aberto'], ['minhas', 'Minhas'], ['encerradas', 'Encerradas']];
    var vista = { tela: 'fila', aba: 'fila', numero: null, rotulos: null };

    window.SPARE_MODULES.regularizacao = {
        render: function (container, sub) {
            S = window.SPARE;
            vista.aba = (sub === 'minhas' || sub === 'encerradas') ? sub : 'fila';
            vista.tela = 'fila';
            vista.numero = null;
            S.tabs(ABAS, vista.aba, 'regularizacao');
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
        return new Date(iso.length === 10 ? iso + 'T00:00:00' : iso).toLocaleDateString('pt-BR');
    }

    function erro(c, e) {
        c.innerHTML = '';
        c.appendChild(S.el('div', { className: 'alert alert-danger', textContent: e.message || String(e) }));
    }

    function carregando(c) {
        c.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando...</div>';
    }

    function selo(estado) {
        return ({
            AG_TRATATIVA_REG: 'badge-gold', EX_TRATATIVA_REG: 'badge-teal',
            RESOLVIDA_REG: 'badge-success', CANCELADA_REG: 'badge-default'
        })[estado] || 'badge-default';
    }

    function seloTipo(tipo) {
        return ({ FALTANTE: 'badge-danger', INESPERADO: 'badge-warning', LOCAL_ERRADO: 'badge-info' })[tipo] || 'badge-default';
    }

    /* ============================================================
       Tela 1 — a fila
       ============================================================ */
    async function telaFila(c) {
        carregando(c);
        var d;
        try {
            d = await S.api('/regularizacao/divergencias' + (vista.aba === 'minhas' ? '?minhas=1' : ''));
        } catch (e) { return erro(c, e); }
        vista.rotulos = d.rotulos;

        var linhas = d.divergencias.filter(function (x) {
            return vista.aba === 'encerradas' ? x.encerrada : !x.encerrada;
        });

        c.innerHTML = '';
        c.appendChild(cabecalho('Regularização de ativos',
            'Toda divergência tem dono e prazo. Sem os dois, é relatório.',
            [botao('Abrir divergência', 'btn-primary', function () { vista.tela = 'nova'; desenhar(c); })]));

        var n = d.contagem || {};
        var origens = (d.por_origem || []).map(function (o) { return o.rotulo + ' ' + o.quantidade; }).join(' · ');
        c.appendChild(indicadores([
            ['Sem dono', n.AG_TRATATIVA_REG || 0, (d.sem_dono ? 'accent-orange' : 'accent-gold')],
            ['Em tratativa', n.EX_TRATATIVA_REG || 0, 'accent-teal'],
            ['Fora do prazo', d.atrasadas || 0, 'accent-orange'],
            ['Resolvidas', n.RESOLVIDA_REG || 0, 'accent-green']
        ]));
        if (origens) {
            c.appendChild(S.el('p', { className: 'text-muted', style: 'margin:-6px 0 14px;font-size:12.5px',
                                      textContent: 'Em aberto por origem: ' + origens }));
        }

        var corpo = S.el('div', { className: 'card-body' });
        if (!linhas.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio',
                textContent: vista.aba === 'minhas' ? 'Nenhuma divergência sua em aberto.'
                    : vista.aba === 'encerradas' ? 'Nenhuma divergência encerrada ainda.'
                    : 'Nenhuma divergência em aberto.' }));
        } else {
            corpo.appendChild(tabela(linhas, c));
        }
        c.appendChild(cartao('Divergências', corpo));
    }

    function tabela(linhas, c) {
        var t = S.el('table', { className: 'data-table' });
        t.innerHTML = '<thead><tr>' +
            ['Divergência', 'Tipo', 'Série / etiqueta', 'Origem', 'Responsável', 'Prazo', 'Situação', 'Em curso', '']
                .map(function (x) { return '<th>' + S.esc(x) + '</th>'; }).join('') + '</tr></thead>';
        var tbody = S.el('tbody');
        linhas.forEach(function (x) {
            var tr = S.el('tr');
            tr.innerHTML =
                '<td><b>' + S.esc(x.numero) + '</b></td>' +
                '<td><span class="badge ' + seloTipo(x.tipo) + '">' + S.esc(x.tipo_rotulo) + '</span></td>' +
                '<td class="sep-serie">' + S.esc(x.serial || x.etiqueta || x.modelo || '—') +
                    (x.modelo && (x.serial || x.etiqueta) ? ' <span class="text-muted">· ' + S.esc(x.modelo) + '</span>' : '') + '</td>' +
                '<td>' + S.esc(x.origem_rotulo) + (x.referencia ? ' <span class="text-muted">' + S.esc(x.referencia) + '</span>' : '') + '</td>' +
                '<td>' + (x.responsavel ? S.esc(x.responsavel) : '<span class="sep-pendente">sem dono</span>') +
                    (x.sem_dono_alerta ? ' <span class="badge badge-danger">há tempo demais</span>' : '') + '</td>' +
                '<td>' + S.esc(data(x.prazo)) + (x.atrasada ? ' <span class="badge badge-danger">atrasada</span>' : '') + '</td>' +
                '<td><span class="badge ' + selo(x.estado) + '">' + S.esc(x.estado_rotulo) + '</span>' +
                    (x.resolucao_rotulo ? ' <span class="text-muted" style="font-size:11.5px">' + S.esc(x.resolucao_rotulo) + '</span>' : '') + '</td>' +
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
       Tela 2 — abrir à mão
       ============================================================ */
    function telaNova(c) {
        c.innerHTML = '';
        c.appendChild(cabecalho('Abrir divergência', 'Para o que você viu e nenhum processo registrou.',
            [botao('Cancelar', 'btn-outline', function () { vista.tela = 'fila'; desenhar(c); })]));
        var corpo = S.el('div', { className: 'card-body' });
        corpo.innerHTML =
            '<div class="form-grid cols-2">' +
              '<div class="form-group"><label for="reg-tipo">Tipo</label><select id="reg-tipo" class="form-control">' +
                '<option value="FALTANTE">Faltante — deveria estar, não está</option>' +
                '<option value="INESPERADO">Inesperado — está, e não se sabe de onde veio</option>' +
                '<option value="LOCAL_ERRADO">Local errado — está, mas o sistema diz outro lugar</option></select></div>' +
              '<div class="form-group"><label for="reg-serial">Série</label><input id="reg-serial" class="form-control" autocomplete="off"></div>' +
              '<div class="form-group"><label for="reg-etiqueta">Etiqueta</label><input id="reg-etiqueta" class="form-control" autocomplete="off"></div>' +
              '<div class="form-group"><label for="reg-modelo">Modelo</label><input id="reg-modelo" class="form-control"></div>' +
              '<div class="form-group"><label for="reg-sist">Onde o sistema diz que está</label><input id="reg-sist" class="form-control"></div>' +
              '<div class="form-group"><label for="reg-fis">Onde foi visto</label><input id="reg-fis" class="form-control"></div>' +
            '</div>' +
            '<div class="form-group"><label for="reg-desc">O que aconteceu</label>' +
              '<textarea id="reg-desc" class="form-control" rows="3"></textarea></div>';
        var rodape = S.el('div', { className: 'card-footer' });
        rodape.appendChild(botao('Abrir', 'btn-primary', async function () {
            var v = function (id) { return corpo.querySelector('#' + id).value.trim(); };
            try {
                S.loading(true);
                var d = await S.api('/regularizacao/divergencias', { method: 'POST', body: {
                    tipo: v('reg-tipo'), serial: v('reg-serial'), etiqueta: v('reg-etiqueta'),
                    modelo: v('reg-modelo'), local_sistema: v('reg-sist'), local_fisico: v('reg-fis'),
                    descricao: v('reg-desc') } });
                S.toast('Divergência ' + d.numero + ' aberta.', 'success');
                vista.tela = 'detalhe'; vista.numero = d.numero; desenhar(c);
            } catch (e) { S.toast(e.message, 'danger'); } finally { S.loading(false); }
        }));
        var card = cartao('Divergência', corpo);
        card.appendChild(rodape);
        c.appendChild(card);
        corpo.querySelector('#reg-serial').focus();
    }

    /* ============================================================
       Tela 3 — a divergência
       ============================================================ */
    async function telaDetalhe(c) {
        carregando(c);
        var d;
        try {
            d = await S.api('/regularizacao/divergencias/' + encodeURIComponent(vista.numero));
        } catch (e) { return erro(c, e); }
        var eu = (S.user() || {}).username || (S.user() || {}).login || '';
        var admin = !!(S.user() || {}).is_admin;

        c.innerHTML = '';
        var acoes = [botao('Voltar', 'btn-outline', function () { vista.tela = 'fila'; desenhar(c); })];
        if (d.estado === 'AG_TRATATIVA_REG') {
            acoes.push(botao('Assumir', 'btn-primary', function () { modalAssumir(d, c, false); }));
            if (admin) acoes.push(botao('Atribuir a alguém', 'btn-secondary', function () { modalAssumir(d, c, true); }));
        } else if (d.estado === 'EX_TRATATIVA_REG') {
            if (d.responsavel === eu || admin) {
                acoes.push(botao('Resolver', 'btn-primary', function () { modalResolver(d, c); }));
            }
            acoes.push(botao('Anotar', 'btn-secondary', function () {
                modalTexto('Anotação', 'O que foi feito ou descoberto', function (t) { return acao(c, 'anotar', { nota: t }); });
            }));
            if (admin) acoes.push(botao('Reatribuir', 'btn-outline', function () { modalAssumir(d, c, true); }));
        }
        if (!d.encerrada && admin) {
            acoes.push(botao('Cancelar', 'btn-outline btn-sm', function () {
                modalTexto('Cancelar divergência', 'Por que não era divergência', function (t) { return acao(c, 'cancelar', { motivo: t }); });
            }));
        }

        c.appendChild(cabecalho((d.serial || d.etiqueta || d.modelo || d.numero),
            d.numero + ' · ' + d.tipo_rotulo + ' · ' + d.origem_rotulo + (d.referencia ? ' ' + d.referencia : '') +
            ' · ' + duracao(d.segundos_uteis) + ' em curso', acoes));

        var layout = S.el('div', { className: 'sep-layout' });
        var esquerda = S.el('div'), lateral = S.el('div');
        layout.appendChild(esquerda);
        layout.appendChild(lateral);
        c.appendChild(layout);

        var corpo = S.el('div', { className: 'card-body' });
        corpo.innerHTML =
            '<div class="detail-grid">' +
              campo('Série', d.serial) + campo('Etiqueta', d.etiqueta) + campo('Modelo', d.modelo) +
              campo('Loja', d.loja) + campo('Sistema diz', d.local_sistema) + campo('Visto em', d.local_fisico) +
            '</div>' +
            (d.descricao ? '<p class="prj-obs" style="margin-top:14px">' + S.esc(d.descricao) + '</p>' : '');
        esquerda.appendChild(cartao('O que é', corpo));

        var hist = S.el('div', { className: 'card-body' });
        hist.appendChild(S.el('pre', { className: 'reg-historico', textContent: d.historico || '—' }));
        esquerda.appendChild(cartao('Histórico', hist));

        var info = S.el('div', { className: 'card-body' });
        info.innerHTML =
            '<div class="sep-total"><span>Situação</span><b><span class="badge ' + selo(d.estado) + '">' + S.esc(d.estado_rotulo) + '</span></b></div>' +
            '<div class="sep-total mt-2"><span>Responsável</span><b>' + (d.responsavel ? S.esc(d.responsavel) : '<span class="sep-pendente">sem dono</span>') + '</b></div>' +
            '<div class="sep-total mt-2"><span>Prazo</span><b>' + S.esc(data(d.prazo)) +
                (d.atrasada ? ' <span class="badge badge-danger">atrasada</span>' : '') + '</b></div>' +
            (d.resolucao_rotulo ? '<div class="sep-total mt-2"><span>Resolução</span><b>' + S.esc(d.resolucao_rotulo) + '</b></div>' +
                (d.resolucao_detalhe ? '<p class="text-muted mt-2" style="font-size:12.5px;margin:0">' + S.esc(d.resolucao_detalhe) + '</p>' : '') : '') +
            '<div class="sep-total mt-2"><span>Aberta por</span><b>' + S.esc(d.aberta_por) + ' · ' + S.esc(data(d.aberta_em)) + '</b></div>';
        lateral.appendChild(cartao('Resumo', info));
    }

    function campo(rot, val) {
        return '<div><label style="font-size:12px;color:var(--text-secondary)">' + S.esc(rot) + '</label><div>' + S.esc(val || '—') + '</div></div>';
    }

    async function modalAssumir(d, c, escolher) {
        var corpo = S.el('div');
        var sel = null;
        if (escolher) {
            sel = S.el('select', { className: 'form-control mb-2' });
            try {
                var r = await S.api('/regularizacao/responsaveis');
                (r.responsaveis || []).forEach(function (u) {
                    sel.appendChild(S.el('option', { value: u.login, textContent: u.nome + ' (' + u.login + ')' }));
                });
                if (d.responsavel) sel.value = d.responsavel;
            } catch (e) { S.toast(e.message, 'danger'); return; }
            corpo.appendChild(S.el('label', { textContent: 'Responsável' }));
            corpo.appendChild(sel);
        }
        var prazo = S.el('input', { type: 'date', className: 'form-control mb-2' });
        var nota = S.el('textarea', { className: 'form-control', rows: '2', placeholder: 'Opcional' });
        corpo.appendChild(S.el('label', { textContent: 'Prazo (em branco usa o padrão da área)' }));
        corpo.appendChild(prazo);
        corpo.appendChild(S.el('label', { textContent: 'Nota' }));
        corpo.appendChild(nota);
        S.openModal(escolher ? 'Atribuir divergência' : 'Assumir divergência', corpo, rodapeModal('Confirmar', function () {
            S.closeModal();
            acao(c, 'assumir', { responsavel: sel ? sel.value : '', prazo: prazo.value || null, nota: nota.value.trim() });
        }));
    }

    function modalResolver(d, c) {
        var corpo = S.el('div');
        var sel = S.el('select', { className: 'form-control mb-2' });
        var res = (vista.rotulos && vista.rotulos.resolucoes) || {
            ENCONTRADO: 'Equipamento encontrado', AJUSTE_SN: 'Cadastro corrigido no ServiceNow',
            BAIXA_SN: 'Baixado no ServiceNow', DEVOLVIDO_LOJA: 'Devolvido pela loja',
            PERDA: 'Perda reconhecida', DUPLICIDADE: 'Era duplicidade de registro' };
        Object.keys(res).forEach(function (k) { sel.appendChild(S.el('option', { value: k, textContent: res[k] })); });
        var det = S.el('textarea', { className: 'form-control', rows: '3', placeholder: 'Como foi resolvida (obrigatório para perda)' });
        corpo.appendChild(S.el('label', { textContent: 'Resolução' }));
        corpo.appendChild(sel);
        corpo.appendChild(S.el('label', { textContent: 'Detalhe' }));
        corpo.appendChild(det);
        S.openModal('Resolver divergência', corpo, rodapeModal('Resolver', function () {
            if (sel.value === 'PERDA' && !det.value.trim()) { S.toast('Perda exige justificativa.', 'warning'); return; }
            S.closeModal();
            acao(c, 'resolver', { resolucao: sel.value, detalhe: det.value.trim() });
        }));
    }

    function modalTexto(titulo, rotulo, aoConfirmar) {
        var corpo = S.el('div');
        var campo = S.el('textarea', { className: 'form-control', rows: '3' });
        corpo.appendChild(S.el('label', { textContent: rotulo }));
        corpo.appendChild(campo);
        S.openModal(titulo, corpo, rodapeModal('Confirmar', function () {
            if (!campo.value.trim()) { S.toast('Escreva o texto.', 'warning'); return; }
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
            await S.api('/regularizacao/divergencias/' + encodeURIComponent(vista.numero) + '/' + qual,
                        { method: 'POST', body: body });
            S.toast(({ assumir: 'Divergência com dono e prazo.', anotar: 'Anotado.',
                       resolver: 'Divergência resolvida.', cancelar: 'Divergência cancelada.' })[qual] || 'Feito.', 'success');
            vista.tela = qual === 'cancelar' ? 'fila' : 'detalhe';
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
