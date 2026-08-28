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

        document.getElementById('q-bg-run').onclick = async function () {
            lastIds = document.getElementById('q-bg-input').value
                .split(/[\n,;\t]+/)
                .map(function (x) { return x.trim(); })
                .filter(Boolean);
            if (!lastIds.length) return S.toast('Informe identificadores.', 'warning');

            try {
                S.loading(true);
                var d = await S.api('/consulta', {
                    method: 'POST',
                    body: { identificadores: lastIds }
                });
                var out = document.getElementById('q-bg-results');
                out.innerHTML =
                    '<p class="text-muted">' +
                    d.encontrados + ' encontrado(s), ' +
                    d.nao_encontrados + ' não encontrado(s).</p>';
                out.appendChild(S.table(columns, d.resultados));
                document.getElementById('q-bg-xlsx').disabled = false;
                S.toast('Consulta concluída.', 'success');
            } catch (e) {
                S.toast(e.message, 'error');
            } finally {
                S.loading(false);
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
