/* Renderizador das telas de TV.
   A página não sabe quais indicadores existem: ela desenha os blocos que o
   endpoint devolver. Definir indicador novo = escrever a consulta no
   servidor; este arquivo não muda.

   O endpoint vem de data-fonte no <body>. */
(function () {
    'use strict';

    var FONTE = document.body.getAttribute('data-fonte');
    var INTERVALO = (parseInt(document.body.getAttribute('data-intervalo'), 10) || 60) * 1000;

    var SERIES = ['--s1', '--s2', '--s3', '--s4', '--s5', '--s6'];
    var ESTADOS = { bom: 'Dentro da meta', atencao: 'Atenção',
                    grave: 'Fora da meta', critico: 'Crítico' };

    function esc(v) {
        return String(v == null ? '' : v)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function num(v) {
        if (typeof v !== 'number' || !isFinite(v)) return esc(v);
        return v.toLocaleString('pt-BR', { maximumFractionDigits: 2 });
    }

    /* Cor por posição na lista, em ordem fixa: a 7ª série repete a última em
       vez de gerar tom novo — cor identifica a entidade, não o ranking. */
    function corSerie(i) {
        return 'var(' + SERIES[Math.min(i, SERIES.length - 1)] + ')';
    }

    // ── Blocos ──────────────────────────────────────────────
    function kpis(b) {
        var h = '<div class="kpis">';
        (b.itens || []).forEach(function (k) {
            var est = ESTADOS[k.estado] ? k.estado : '';
            h += '<div class="kpi ' + est + '">' +
                 '<div class="rotulo">' + esc(k.rotulo) + '</div>' +
                 '<div class="valor">' + esc(k.valor) + '</div>' +
                 (k.sub ? '<div class="sub">' + esc(k.sub) + '</div>' : '') +
                 // O estado sempre vem escrito: a cor sozinha não informa.
                 (est ? '<div class="estado">' + esc(ESTADOS[est]) + '</div>' : '') +
                 '</div>';
        });
        return h + '</div>';
    }

    function barras(b) {
        var itens = b.itens || [];
        var max = itens.reduce(function (m, i) { return Math.max(m, i.valor || 0); }, 0) || 1;
        var h = '<div class="barras">';
        itens.forEach(function (it, i) {
            var pct = Math.max(0.6, ((it.valor || 0) / max) * 100);
            var cor = corSerie(typeof it.serie === 'number' ? it.serie : i);
            h += '<div class="barra">' +
                 '<div class="nome" title="' + esc(it.rotulo) + '">' + esc(it.rotulo) + '</div>' +
                 '<div class="trilho"><div class="marca" style="width:' + pct +
                     '%;background:' + cor + '"></div></div>' +
                 '<div class="num">' + num(it.valor) + esc(b.sufixo || '') + '</div>' +
                 '</div>';
        });
        return h + '</div>';
    }

    function tabela(b) {
        // Cabeçalho segue o alinhamento da coluna: título de coluna numérica
        // à direita, sobre os números.
        var primeira = (b.linhas || [])[0] || [];
        var ehNumCol = (b.colunas || []).map(function (_c, i) {
            return typeof primeira[i] === 'number';
        });
        var h = '<div class="tabela-wrap"><table><thead><tr>';
        (b.colunas || []).forEach(function (c, i) {
            h += '<th' + (ehNumCol[i] ? ' class="num"' : '') + '>' + esc(c) + '</th>';
        });
        h += '</tr></thead><tbody>';
        (b.linhas || []).forEach(function (linha) {
            h += '<tr>';
            (linha || []).forEach(function (c) {
                var ehNum = typeof c === 'number';
                h += '<td' + (ehNum ? ' class="num"' : '') + '>' + (ehNum ? num(c) : esc(c)) + '</td>';
            });
            h += '</tr>';
        });
        return h + '</tbody></table></div>';
    }

    function texto(b) {
        return '<div class="texto">' + esc(b.texto) + '</div>';
    }

    var DESENHO = { kpis: kpis, barras: barras, tabela: tabela, texto: texto };

    // ── Página ──────────────────────────────────────────────
    function render(d) {
        document.title = d.titulo + ' — Portal SPARE';
        document.getElementById('ck-titulo').textContent = d.titulo || '';
        document.getElementById('ck-sub').textContent = d.subtitulo || '';

        var main = document.getElementById('ck-main');
        if (d.pendente || !(d.blocos || []).length) {
            main.innerHTML = '<div class="aviso"><strong>Painel reservado</strong>' +
                '<p>' + esc(d.pendencia || 'Indicadores ainda não definidos.') + '</p>' +
                '<p>Assim que os indicadores forem definidos, aparecem aqui.</p></div>';
            return;
        }
        var h = '';
        (d.blocos || []).forEach(function (b) {
            var fn = DESENHO[b.tipo];
            if (!fn) return;
            h += '<section class="bloco">' +
                 (b.titulo ? '<h2>' + esc(b.titulo) + '</h2>' : '') +
                 fn(b) + '</section>';
        });
        main.innerHTML = h;
    }

    function carimbo(quando, ok) {
        var el = document.getElementById('ck-carimbo');
        var q = quando ? new Date(quando).toLocaleString('pt-BR') : '—';
        el.innerHTML = '<span class="pulso' + (ok ? '' : ' off') + '"></span>' +
            (ok ? 'Atualizado ' + esc(q) : 'Sem conexão — último dado ' + esc(q));
    }

    var ultimo = null;

    function buscar() {
        fetch(FONTE, { cache: 'no-store' })
            .then(function (r) {
                if (!r.ok) throw new Error(r.status);
                return r.json();
            })
            .then(function (d) {
                ultimo = d.atualizado_em;
                render(d);
                carimbo(ultimo, true);
            })
            .catch(function () {
                // Mantém o último dado na tela: painel em branco na parede
                // é pior do que número velho com aviso de que está velho.
                carimbo(ultimo, false);
            });
    }

    function relogio() {
        document.getElementById('ck-relogio').textContent =
            new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
    }

    relogio();
    setInterval(relogio, 20000);
    buscar();
    setInterval(buscar, INTERVALO);
}());
