/* ================================================================
   Module: Consulta (Asset query — direct POST, no job queue)
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.consulta = {

    render(container) {
        var S = window.SPARE;
        var lastIds = [];

        container.innerHTML =
            '<h1 class="page-title">Consulta de Ativos</h1>' +
            '<div class="card mb-3">' +
                '<div class="card-header">Identificadores</div>' +
                '<div class="card-body">' +
                    '<p class="text-muted">Cole os identificadores (um por linha, separados por vírgula ou tabulação).</p>' +
                    '<textarea id="q-bg-input" class="form-control" rows="6" ' +
                        'placeholder="Um identificador por linha"></textarea>' +
                    '<div class="btn-row mt-2">' +
                        '<button id="q-bg-run" class="btn btn-primary">Consultar</button>' +
                        '<button id="q-bg-clear" class="btn btn-outline">Limpar</button>' +
                        '<button id="q-bg-xlsx" class="btn btn-outline" disabled>Exportar Excel</button>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div id="q-bg-results"></div>';

        var columns = [
            { key: 'empresa',       label: 'Empresa' },
            { key: 'imobilizado',   label: 'Imobilizado',  render: function (v, r) { return r.ativo || v || ''; } },
            { key: 'etiqueta',      label: 'Etiqueta' },
            { key: 'numero_serie',  label: 'Nº Série' },
            { key: 'descricao',     label: 'Descrição' },
            { key: 'categoria',     label: 'Categoria' },
            { key: 'modelo',        label: 'Modelo' },
            { key: 'fonte',         label: 'Fonte' },
            { key: 'erro',          label: 'Erro' }
        ];

        function barraProgresso() {
            var wrap = S.el('div', { style: 'margin:8px 0 16px' });
            wrap.innerHTML =
                '<div style="display:flex;justify-content:space-between;font-size:.85rem;' +
                'color:var(--text-secondary);margin-bottom:4px">' +
                '<span class="cp-label">Consultando...</span><span class="cp-pct">0%</span></div>' +
                '<div style="height:10px;background:var(--bg-secondary,#eee);border-radius:6px;overflow:hidden">' +
                '<div class="cp-fill" style="height:100%;width:0%;background:#3b82f6;transition:width .2s"></div></div>';
            return {
                el: wrap,
                set: function (feito, total) {
                    var pct = total ? Math.round((feito / total) * 100) : 0;
                    wrap.querySelector('.cp-pct').textContent = pct + '% (' + feito + '/' + total + ')';
                    wrap.querySelector('.cp-fill').style.width = pct + '%';
                }
            };
        }

        document.getElementById('q-bg-run').onclick = async function () {
            lastIds = document.getElementById('q-bg-input').value
                .split(/[\n,;\t]+/)
                .map(function (x) { return x.trim(); })
                .filter(Boolean);
            if (!lastIds.length) return S.toast('Informe identificadores.', 'warning');

            var runBtn = document.getElementById('q-bg-run');
            var out = document.getElementById('q-bg-results');
            runBtn.disabled = true;
            out.innerHTML = '';
            var prog = barraProgresso();
            out.appendChild(prog.el);
            prog.set(0, lastIds.length);

            var LOTE = 25;
            var acumulado = [];
            var encontrados = 0, naoEncontrados = 0;
            try {
                for (var i = 0; i < lastIds.length; i += LOTE) {
                    var chunk = lastIds.slice(i, i + LOTE);
                    var d = await S.api('/consulta', {
                        method: 'POST',
                        body: { identificadores: chunk }
                    });
                    encontrados += d.encontrados || 0;
                    naoEncontrados += d.nao_encontrados || 0;
                    acumulado = acumulado.concat(d.resultados || []);
                    prog.set(Math.min(i + LOTE, lastIds.length), lastIds.length);
                }
                out.innerHTML =
                    '<p class="text-muted">' + encontrados + ' encontrado(s), ' +
                    naoEncontrados + ' não encontrado(s).</p>';
                out.appendChild(S.table(columns, acumulado));
                document.getElementById('q-bg-xlsx').disabled = false;
                S.toast('Consulta concluída.', 'success');
            } catch (e) {
                out.innerHTML = '<div class="alert alert-danger">' + S.esc(e.message) + '</div>';
                S.toast(e.message, 'error');
            } finally {
                runBtn.disabled = false;
            }
        };

        document.getElementById('q-bg-clear').onclick = function () {
            document.getElementById('q-bg-input').value = '';
            document.getElementById('q-bg-results').innerHTML = '';
            document.getElementById('q-bg-xlsx').disabled = true;
            lastIds = [];
        };

        document.getElementById('q-bg-xlsx').onclick = async function () {
            if (!lastIds.length) return;
            try {
                var r = await S.api('/consulta/export', {
                    method: 'POST',
                    body: { identificadores: lastIds }
                });
                var b = await r.blob();
                var a = document.createElement('a');
                a.href = URL.createObjectURL(b);
                a.download = 'consulta_ativos.xlsx';
                a.click();
                URL.revokeObjectURL(a.href);
            } catch (e) {
                S.toast(e.message, 'error');
            }
        };
    }

};
