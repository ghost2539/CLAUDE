/* ================================================================
   Module: Bem-vindo (Welcome / Dashboard)
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.bemvindo = {

    async render(container) {
        var S = window.SPARE;

        container.innerHTML =
            '<h1 class="page-title">Bem-vindo</h1>' +
            '<div id="welcome-stats" class="stats-grid mb-3"></div>' +
            '<div class="card">' +
                '<div class="card-header">Status das Integrações</div>' +
                '<div id="welcome-status" class="card-body btn-row"></div>' +
            '</div>';

        // Dashboard stats
        try {
            var d = await S.api('/dashboard/summary');
            var stats = [
                ['Recebidos no Mês', d.recebidos_mes,   'teal'],
                ['Recebidos no Ano', d.recebidos_ano,    'orange'],
                ['Reparos no Mês',   d.reparos_mes,      'gold'],
                ['Saving',           S.money(d.saving),   'green']
            ];
            document.getElementById('welcome-stats').innerHTML = stats.map(function (x) {
                return '<div class="stat-card accent-' + x[2] + '">' +
                    '<div class="stat-value">' + x[1] + '</div>' +
                    '<div class="stat-label">' + x[0] + '</div>' +
                '</div>';
            }).join('');
        } catch (e) {
            S.toast(e.message, 'error');
        }

        // Integration status
        try {
            var d = await S.api('/status');
            var items = [
                ['EBS',        d.ebs],
                ['PostgreSQL', d.postgres],
                ['Base Local', d.local]
            ];
            document.getElementById('welcome-status').innerHTML = items.map(function (x) {
                var dotClass = x[1].connected ? 'dot-green' : (x[1].not_applicable ? 'dot-muted' : 'dot-red');
                return '<div class="integ-item">' +
                    '<span class="dot ' + dotClass + '"></span>' + x[0] +
                '</div>';
            }).join('');
        } catch (_) {}
    }

};
