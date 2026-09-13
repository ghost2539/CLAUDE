/* ================================================================
   Módulo: Torre de Controle (T2)

   Três abas, três perguntas:

     Área          onde estão as filas e o que destravar agora
     Minha produção  quanto tempo ficou comigo, sem contar espera
     Equipe        consolidado por pessoa (só para quem administra)

   Nenhum número é gravado: tudo é derivado dos intervalos do núcleo.
   Por isso o painel muda sozinho quando o calendário muda.

   Os gráficos são barras em HTML. Uma barra horizontal ordenada
   responde "onde está a fila" melhor que qualquer biblioteca, e uma
   biblioteca a mais é um arquivo a mais para carregar numa TV.
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};

(function () {
    var S = null;
    var ABAS = [['area', 'Área'], ['trilha', 'Trilha do ativo'],
                ['minha', 'Minha produção']];
    var vista = { aba: 'area' };

    /* Uma cor para as barras e as de severidade à parte. Cor de estado
       não é cor de série: misturar as duas faz o olho ler gravidade
       onde só há identidade. */
    var COR_BARRA = '#3E9187';
    var COR_ALERTA = '#C79105';
    var COR_GRAVE = '#D4626C';

    function abasVisiveis() {
        var u = S.user() || {};
        var abas = ABAS.slice();
        if (u.is_admin) abas.push(['equipe', 'Equipe']);
        return abas;
    }

    window.SPARE_MODULES.torre = {
        render: function (container, sub) {
            S = window.SPARE;
            var abas = abasVisiveis();
            vista.aba = ['area', 'trilha', 'minha', 'equipe'].indexOf(sub) >= 0
                ? sub : 'area';
            S.tabs(abas, vista.aba, 'torre');
            if (vista.aba === 'trilha') return telaTrilha(container);
            if (vista.aba === 'minha') return telaPessoa(container);
            if (vista.aba === 'equipe') return telaEquipe(container);
            return telaArea(container);
        }
    };

    /* ============================================================
       Área
       ============================================================ */
    async function telaArea(c) {
        c.innerHTML = carregando();
        var d;
        try { d = await S.api('/torre/area'); }
        catch (e) { return falha(c, e); }

        c.innerHTML = '';
        c.appendChild(cabecalho('Torre de Controle',
            'Filas, backlogs e o que está parado há mais tempo.'));

        if (d.calendario_corrido) {
            c.appendChild(S.el('div', {
                className: 'alert alert-warning mb-3',
                textContent: 'O calendário de expediente ainda não foi definido, ' +
                    'então os tempos abaixo estão corridos — contam noite e fim ' +
                    'de semana. Configure em Parâmetros → Separação.'
            }));
        }

        c.appendChild(indicadores([
            ['Ativos em curso', String(d.em_curso), 'accent-teal'],
            ['Parados além do limite', String(d.em_alerta), 'accent-gold'],
            ['Mais antigo parado', duracao(d.mais_antigo), 'accent-orange'],
            ['Entradas / saídas hoje', d.entradas_hoje + ' / ' + d.saidas_hoje,
             'accent-green']
        ]));

        var filas = d.filas.filter(function (f) { return f.estado !== 'DISPONIVEL'; });

        var g1 = S.el('div', { className: 'card-body' });
        if (!filas.length) {
            g1.appendChild(S.el('p', { className: 'sep-vazio',
                textContent: 'Nenhum ativo em curso. A trilha começa a encher ' +
                             'quando o primeiro recebimento acontecer.' }));
        } else {
            g1.appendChild(barras(filas, 'quantidade', function (f) {
                return String(f.quantidade);
            }, function (f) {
                return f.em_alerta > 0 ? COR_ALERTA : COR_BARRA;
            }));
            g1.appendChild(S.el('p', {
                className: 'text-muted mt-2', style: 'font-size:11.5px;margin-bottom:0',
                textContent: 'Em âmbar, as etapas com ativo parado há mais de ' +
                             duracao(d.limite_alerta) + '.'
            }));
        }
        c.appendChild(cartao('Quantos ativos em cada etapa', g1));

        if (filas.length) {
            var g2 = S.el('div', { className: 'card-body' });
            var porTempo = filas.slice().sort(function (a, b) {
                return b.maior - a.maior;
            });
            g2.appendChild(barras(porTempo, 'maior', function (f) {
                return duracao(f.maior);
            }, function (f) {
                return f.maior > d.limite_alerta * 2 ? COR_GRAVE
                     : (f.maior > d.limite_alerta ? COR_ALERTA : COR_BARRA);
            }));
            c.appendChild(cartao('Há quanto tempo o mais antigo de cada etapa espera', g2));
        }

        if (d.frentes.length) {
            var gf = S.el('div', { className: 'card-body' });
            gf.appendChild(barras(d.frentes.map(function (f) {
                return { rotulo: f.frente, quantidade: f.quantidade,
                         em_alerta: f.em_alerta };
            }), 'quantidade', function (f) {
                return String(f.quantidade);
            }, function (f) {
                return f.em_alerta > 0 ? COR_ALERTA : COR_BARRA;
            }));
            c.appendChild(cartao('Por frente', gf));
        }

        var t = S.el('div', { className: 'card-body' });
        if (!d.parados.length) {
            t.appendChild(S.el('p', { className: 'sep-vazio',
                                      textContent: 'Nada parado.' }));
        } else {
            var tab = S.el('table', { className: 'data-table' });
            tab.innerHTML = '<thead><tr><th>Série</th><th>Modelo</th>' +
                '<th>Etapa</th><th>Com</th><th>Parado há</th></tr></thead><tbody>' +
                d.parados.map(function (p) {
                    return '<tr class="torre-clicavel" data-serial="' +
                        S.esc(p.serial) + '"><td class="sep-serie">' +
                        S.esc(p.serial) + '</td>' +
                        '<td>' + S.esc(p.modelo || '—') + '</td>' +
                        '<td>' + S.esc(p.rotulo) + '</td>' +
                        '<td>' + S.esc(p.usuario || '—') + '</td>' +
                        '<td' + (p.em_alerta ? ' style="color:#E8B94A"' : '') + '>' +
                        S.esc(duracao(p.segundos)) + '</td></tr>';
                }).join('') + '</tbody>';
            var w = S.el('div', { className: 'table-wrapper' });
            w.appendChild(tab);
            t.appendChild(w);
            // Clicar na linha abre a trilha daquele ativo: quem olha um
            // parado quer saber por onde ele andou, não copiar a série.
            tab.querySelectorAll('.torre-clicavel').forEach(function (tr) {
                tr.style.cursor = 'pointer';
                tr.addEventListener('click', function () {
                    vista.aba = 'trilha';
                    S.tabs(abasVisiveis(), 'trilha', 'torre');
                    telaTrilha(c, tr.dataset.serial);
                });
            });
        }
        c.appendChild(cartao('Parados há mais tempo', t));
    }

    /* ============================================================
       Trilha do ativo (T1)

       A linha do tempo completa de um equipamento. É a tela que se
       abre numa auditoria, e por isso mostra tudo: cada estado, quanto
       durou, quem estava com ele e o que foi justificado.
       ============================================================ */
    function telaTrilha(c, serial) {
        c.innerHTML = '';
        c.appendChild(cabecalho('Trilha do ativo',
            'Tudo que aconteceu com um equipamento desde que entrou na área.'));

        var busca = S.el('div', { className: 'card-body' });
        var campo = S.el('input', {
            id: 'torre-serie', className: 'form-control form-control-inline',
            style: 'min-width:280px', autocomplete: 'off',
            placeholder: 'Série do equipamento'
        });
        if (serial) campo.value = serial;
        var linha = S.el('div', { className: 'form-row-inline' });
        linha.appendChild(campo);
        linha.appendChild(S.el('button', {
            className: 'btn btn-secondary', type: 'button', textContent: 'Buscar',
            onClick: function () { carregarTrilha(c, campo.value.trim()); }
        }));
        busca.appendChild(linha);
        campo.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); carregarTrilha(c, campo.value.trim()); }
        });
        c.appendChild(cartao('Buscar', busca));

        var alvo = S.el('div', { id: 'torre-trilha' });
        c.appendChild(alvo);
        setTimeout(function () { campo.focus(); }, 60);
        if (serial) carregarTrilha(c, serial);
    }

    async function carregarTrilha(c, serial) {
        if (!serial) return;
        var alvo = document.getElementById('torre-trilha');
        alvo.innerHTML = carregando();
        var d;
        try {
            d = await S.api('/trilha/ativos/' + encodeURIComponent(serial));
        } catch (e) {
            alvo.innerHTML = '';
            alvo.appendChild(S.el('div', { className: 'alert alert-danger',
                                           textContent: e.message }));
            return;
        }

        alvo.innerHTML = '';
        var a = d.ativo;

        var ficha = S.el('div', { className: 'card-body' });
        ficha.innerHTML = '<div class="detail-grid">' +
            dado('Série', a.serial) + dado('Modelo', a.modelo || '—') +
            dado('Nº de ativo', a.numero_ativo || '—') +
            dado('Origem', a.origem || '—') +
            dado('Estado atual', a.encerrado
                ? 'Encerrado — ' + (a.estado_rotulo || a.estado_fisico)
                : (a.estado_rotulo || '—')) +
            dado('Na área desde', dataHora(a.criado_em)) +
            '</div>';
        alvo.appendChild(cartao(a.serial, ficha));

        // O número que a área não tinha: quanto tempo o equipamento
        // passou com o SPARE, somando fila e bancada.
        alvo.appendChild(indicadores([
            ['Tempo total na área', duracao(d.tempo_total_uteis), 'accent-orange'],
            ['Etapas percorridas', String(d.por_estado.length), 'accent-teal'],
            ['Movimentações', String(d.movimentacoes.length), 'accent-gold']
        ]));

        if (d.por_estado.length) {
            var g = S.el('div', { className: 'card-body' });
            g.appendChild(barras(d.por_estado.map(function (e) {
                return { rotulo: e.rotulo, valor: e.segundos_uteis };
            }), 'valor', function (e) { return duracao(e.valor); },
               function () { return COR_BARRA; }));
            alvo.appendChild(cartao('Onde o tempo foi gasto', g));
        }

        var tl = S.el('div', { className: 'card-body' });
        var ul = S.el('ul', { className: 'atd-tl' });
        d.intervalos.forEach(function (i) {
            var li = S.el('li', { className: i.aberto ? 'atual' : 'ok' });
            li.innerHTML =
                '<div class="atd-tl-quando">' + S.esc(dataHora(i.inicio)) +
                    (i.sessao > 1 ? ' · ' + i.sessao + 'ª vez' : '') + '</div>' +
                '<div class="atd-tl-que">' + S.esc(i.rotulo) +
                    ' — ' + S.esc(duracao(i.segundos_uteis)) +
                    (i.aberto ? ' (em curso)' : '') + '</div>' +
                '<div class="atd-tl-quem">' +
                    S.esc(i.processo || '—') +
                    (i.usuario ? ' · ' + S.esc(i.usuario) : '') +
                    ' · ' + S.esc(rotuloTipo(i.tipo)) + '</div>';
            ul.appendChild(li);
        });
        tl.appendChild(ul);
        alvo.appendChild(cartao('Linha do tempo', tl));

        var corrigidas = d.movimentacoes.filter(function (m) {
            return m.origem === 'ADMIN';
        });
        if (corrigidas.length) {
            var cor = S.el('div', { className: 'card-body' });
            cor.innerHTML = '<p class="text-muted" style="font-size:12.5px;' +
                'margin:0 0 10px">Movimentações feitas manualmente por ' +
                'administrador. A trilha não se apaga: correção é uma linha ' +
                'nova com justificativa.</p>' +
                corrigidas.map(function (m) {
                    return '<div class="sep-linha"><div>' +
                        '<div style="font-size:13px">' +
                        S.esc(m.de_rotulo || '—') + ' → ' +
                        S.esc(m.para_rotulo) + '</div>' +
                        '<div class="text-muted" style="font-size:11.5px">' +
                        S.esc(dataHora(m.quando)) + ' · ' + S.esc(m.usuario) +
                        ' · ' + S.esc(m.justificativa) + '</div></div></div>';
                }).join('');
            alvo.appendChild(cartao('Correções manuais', cor));
        }
    }

    function rotuloTipo(tipo) {
        return ({ FILA: 'fila', TRATATIVA: 'com alguém',
                  EXTERNO: 'espera externa' })[tipo] || tipo;
    }

    function dado(rot, valor) {
        return '<div><label style="font-size:12px;color:var(--text-secondary)">' +
            S.esc(rot) + '</label><div>' + S.esc(valor) + '</div></div>';
    }

    function dataHora(iso) {
        if (!iso) return '—';
        return new Date(iso).toLocaleString('pt-BR',
            { day: '2-digit', month: '2-digit', year: '2-digit',
              hour: '2-digit', minute: '2-digit' });
    }

    /* ============================================================
       Minha produção
       ============================================================ */
    async function telaPessoa(c, login) {
        c.innerHTML = carregando();
        var d;
        try {
            d = await S.api('/torre/pessoa' + (login ? '?login=' +
                encodeURIComponent(login) : ''));
        } catch (e) { return falha(c, e); }

        c.innerHTML = '';
        c.appendChild(cabecalho(login ? d.login : 'Minha produção',
            'Últimos ' + d.dias + ' dias. Só o tempo em que o equipamento ' +
            'esteve com você — fila e espera de terceiro não entram.'));

        c.appendChild(indicadores([
            ['Tempo em bancada', duracao(d.segundos_tratativa), 'accent-teal'],
            ['Passagens', String(d.passagens), 'accent-gold'],
            ['Em aberto agora', String(d.em_aberto), 'accent-orange'],
            ['Movimentações', String(d.movimentacoes), 'accent-green']
        ]));

        var corpo = S.el('div', { className: 'card-body' });
        if (!d.etapas.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio',
                textContent: 'Nenhuma passagem registrada no período.' }));
        } else {
            corpo.appendChild(barras(d.etapas, 'total', function (e) {
                return duracao(e.total) + ' · ' + e.passagens + 'x';
            }, function () { return COR_BARRA; }));
        }
        c.appendChild(cartao('Tempo por etapa', corpo));
    }

    /* ============================================================
       Equipe
       ============================================================ */
    async function telaEquipe(c) {
        c.innerHTML = carregando();
        var d;
        try { d = await S.api('/torre/pessoas'); }
        catch (e) { return falha(c, e); }

        c.innerHTML = '';
        c.appendChild(cabecalho('Equipe',
            'Últimos ' + d.dias + ' dias, só tempo de tratativa. Fila e espera ' +
            'externa não entram na conta de ninguém.'));

        var corpo = S.el('div', { className: 'card-body' });
        if (!d.pessoas.length) {
            corpo.appendChild(S.el('p', { className: 'sep-vazio',
                textContent: 'Ninguém assumiu equipamento no período.' }));
        } else {
            var t = S.el('table', { className: 'data-table' });
            t.innerHTML = '<thead><tr><th>Pessoa</th><th>Tempo em bancada</th>' +
                '<th>Passagens</th><th>Em aberto</th><th></th></tr></thead>';
            var tb = S.el('tbody');
            d.pessoas.forEach(function (p) {
                var tr = S.el('tr');
                tr.innerHTML = '<td>' + S.esc(p.login) + '</td>' +
                    '<td>' + S.esc(duracao(p.segundos)) + '</td>' +
                    '<td>' + p.passagens + '</td>' +
                    '<td>' + p.em_aberto + '</td>';
                var td = S.el('td');
                td.appendChild(S.el('button', {
                    className: 'btn btn-outline btn-sm', type: 'button',
                    textContent: 'Detalhar',
                    onClick: function () { telaPessoa(c, p.login); }
                }));
                tr.appendChild(td);
                tb.appendChild(tr);
            });
            t.appendChild(tb);
            var w = S.el('div', { className: 'table-wrapper' });
            w.appendChild(t);
            corpo.appendChild(w);
        }
        c.appendChild(cartao('Por pessoa', corpo));
    }

    /* ============================================================
       Barras horizontais

       Em HTML, não em SVG: um SVG com viewBox e altura fixa centraliza
       o desenho e abre um vão à esquerda quando o cartão é mais largo
       que o desenho. Com div e largura em porcentagem, a barra
       acompanha o cartão em qualquer tela sem conta nenhuma.

       Sem eixo: numa lista ordenada o eixo não acrescenta nada que o
       número no fim da barra já não diga.
       ============================================================ */
    function barras(linhas, chave, texto, cor) {
        var maximo = Math.max.apply(null, linhas.map(function (l) {
            return l[chave] || 0;
        }).concat([1]));

        var caixa = S.el('div', { className: 'torre-barras' });
        linhas.forEach(function (l) {
            var valor = l[chave] || 0;
            var pct = Math.max(1.5, (valor / maximo) * 100);
            var linha = S.el('div', { className: 'torre-barra' });
            linha.innerHTML =
                '<div class="torre-barra-rotulo" title="' + S.esc(l.rotulo) + '">' +
                    S.esc(l.rotulo) + '</div>' +
                '<div class="torre-barra-pista">' +
                    '<div class="torre-barra-fita" style="width:' + pct.toFixed(1) +
                    '%;background:' + cor(l) + '"></div></div>' +
                '<div class="torre-barra-valor">' + S.esc(texto(l)) + '</div>';
            caixa.appendChild(linha);
        });
        return caixa;
    }

    function corta(t, n) {
        t = t || '';
        return t.length > n ? t.slice(0, n - 1) + '…' : t;
    }

    /* ── Peças ─────────────────────────────────────────────────── */
    function duracao(seg) {
        if (!seg && seg !== 0) return '—';
        var h = Math.floor(seg / 3600), m = Math.floor((seg % 3600) / 60);
        if (h >= 24) return Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
        return h ? h + 'h ' + m + 'm' : m + 'm';
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
            card.innerHTML = '<div class="stat-value" style="font-size:24px">' +
                S.esc(x[1]) + '</div>' +
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
})();
