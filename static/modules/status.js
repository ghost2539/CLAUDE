/* ================================================================
   Module: Status (Integration status page)
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.status = {

    async render(container) {
        var S = window.SPARE;

        container.innerHTML =
            '<h1 class="page-title">Status das Integrações</h1>' +
            '<div id="st" class="status-cards-grid"></div>';

        try {
            var d = await S.api('/status');
            var items = [
                ['EBS',        d.ebs],
                ['PostgreSQL', d.postgres],
                ['Base Local', d.local]
            ];
            document.getElementById('st').innerHTML = items.map(function (x) {
                var connected = x[1].connected;
                var na = x[1].not_applicable;
                var dotClass = connected ? 'dot-green' : (na ? 'dot-muted' : 'dot-red');
                var label = connected ? 'ATIVO' : (na ? 'N/A' : 'INATIVO');
                return '<div class="card">' +
                    '<div class="card-body">' +
                        '<h3>' + x[0] + '</h3>' +
                        '<div class="status-indicator">' +
                            '<span class="dot ' + dotClass + '"></span>' + label +
                        '</div>' +
                    '</div>' +
                '</div>';
            }).join('');
        } catch (e) {
            S.toast(e.message, 'error');
        }
    }

};
