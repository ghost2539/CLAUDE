/* ================================================================
   Módulo: Status (integrações + saúde do servidor)
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.status = {

    async render(container) {
        var S = window.SPARE;

        container.innerHTML =
            '<h1 class="page-title">Status das Integrações</h1>' +
            '<div id="st" class="status-cards-grid"></div>' +
            '<div id="st-versao"></div>' +
            '<div id="st-servidor"></div>';

        this.renderVersao();

        try {
            var d = await S.api('/status');
            // "SQL" e não "PostgreSQL": o portal roda em Postgres ou em
            // SQLite conforme o servidor, e o nome do produto na tela dizia
            // errado no servidor novo. A Base Local saiu da tela a pedido.
            var items = [
                ['EBS', d.ebs],
                ['SQL', d.postgres]
            ];
            document.getElementById('st').innerHTML = items.map(function (x) {
                var connected = x[1].connected;
                var na = x[1].not_applicable;
                var dotClass = connected ? 'dot-green' : (na ? 'dot-muted' : 'dot-red');
                var label = connected ? 'ATIVO' : (na ? 'N/A' : 'INATIVO');
                var nota = x[1].modo || x[1].error || '';
                return '<div class="card">' +
                    '<div class="card-body">' +
                        '<h3>' + x[0] + '</h3>' +
                        '<div class="status-indicator">' +
                            '<span class="dot ' + dotClass + '"></span>' + label +
                        '</div>' +
                        (nota ? '<div class="text-muted" style="font-size:.78rem;margin-top:4px">' +
                            S.esc(nota) + '</div>' : '') +
                    '</div>' +
                '</div>';
            }).join('');

            this.renderServidor(d.servidor);
        } catch (e) {
            S.toast(e.message, 'error');
        }
    },

    /* Qual código está no ar. Responde "a correção subiu?" sem terminal. */
    async renderVersao() {
        var S = window.SPARE;
        var host = document.getElementById('st-versao');
        if (!host) return;
        try {
            var v = await S.api('/versao');
            host.innerHTML = '<div class="card mb-3"><div class="card-body">' +
                '<span class="text-muted" style="font-size:.8rem">Versão em execução</span> ' +
                '<b>' + S.esc(v.commit_curto || '—') + '</b>' +
                ' · ' + S.esc(v.ramo || '') + ' · ' + S.esc(v.ambiente || '') +
                (v.assunto ? '<div class="text-muted" style="font-size:.78rem;margin-top:4px">' +
                    S.esc(v.assunto) + '</div>' : '') +
                '</div></div>';
        } catch (e) { /* sem versão a tela segue */ }
    },

    /* Bloco de saúde do servidor. Vem do módulo de Monitoramento; quando ele
       não está carregado o back-end devolve null e o bloco some. */
    renderServidor(sv) {
        var S = window.SPARE;
        var host = document.getElementById('st-servidor');
        if (!host || !sv) { return; }

        var mem = sv.memoria || {}, dk = sv.disco || {},
            cg = sv.carga || {}, up = sv.uptime || {}, lim = sv.limiares || {};

        function barra(pct, alerta, critico) {
            pct = Number(pct) || 0;
            var cor = pct >= critico ? 'var(--sp-alerta)' : (pct >= alerta ? 'var(--sp-gold)' : 'var(--sp-ok)');
            return '<div style="background:var(--sp-border-soft);' +
                'height:8px;overflow:hidden;margin-top:6px">' +
                '<div style="height:100%;width:' + Math.min(100, pct) + '%;' +
                'background:' + cor + '"></div></div>';
        }
        function tile(titulo, valor, sub, extra) {
            return '<div class="stat-card">' +
                '<div class="stat-value" style="font-size:1.5rem">' + valor + '</div>' +
                '<div class="stat-label">' + S.esc(titulo) + '</div>' +
                (sub ? '<div class="text-muted" style="font-size:.78rem;margin-top:2px">' +
                    sub + '</div>' : '') +
                (extra || '') + '</div>';
        }

        var html = '<h2 class="page-title" style="font-size:1.1rem;margin-top:28px">' +
            'Saúde do servidor</h2>' +
            '<div class="stats-grid">' +
            tile('Uso de memória', (mem.pct_usado || 0) + '%',
                 (mem.usado_mb || 0) + ' / ' + (mem.total_mb || 0) + ' MB',
                 barra(mem.pct_usado, lim.mem_alerta, lim.mem_critico)) +
            tile('Uso de disco', (dk.pct_usado || 0) + '%',
                 (dk.usado_gb || 0) + ' / ' + (dk.total_gb || 0) + ' GB (livre ' +
                 (dk.livre_gb || 0) + ' GB)',
                 barra(dk.pct_usado, lim.disco_alerta, lim.disco_critico)) +
            tile('Carga (1 min)', (cg.load1 !== undefined ? cg.load1 : '—'),
                 (cg.cpus || 0) + ' CPU(s) · ' + (cg.pct_load1 || 0) + '%') +
            tile('Uptime', up.servidor || '—', 'portal: ' + (up.aplicacao || '—')) +
            '</div>';

        var falhas = sv.falhas_criticas || [];
        html += '<div style="margin-top:18px;font-weight:600;font-size:.9rem">' +
            'Falhas críticas' +
            (sv.total_criticas ? ' <span class="text-muted" style="font-weight:400">(' +
                sv.total_criticas + ' nas últimas ' + (sv.horas || 24) + 'h)</span>' : '') +
            '</div>';

        if (!falhas.length) {
            html += '<div class="alert alert-success" style="margin-top:6px">' +
                'Nenhuma falha crítica registrada.</div>';
        } else {
            html += '<div class="tw" style="overflow-x:auto;margin-top:6px">' +
                '<table class="data-table"><thead><tr>' +
                '<th>Quando</th><th>Origem</th><th>Alvo</th><th>Detalhe</th>' +
                '</tr></thead><tbody>';
            falhas.forEach(function (f) {
                var quando = f.quando ? new Date(f.quando).toLocaleString('pt-BR') : '—';
                html += '<tr><td>' + S.esc(quando) + '</td>' +
                    '<td>' + S.esc(f.origem || '—') + '</td>' +
                    '<td>' + S.esc(f.alvo || '—') +
                    (f.status_code ? ' <span class="text-muted">(' + f.status_code + ')</span>' : '') +
                    '</td>' +
                    '<td style="font-size:.8rem;color:var(--text-secondary)">' +
                    S.esc(f.detalhe || '') + '</td></tr>';
            });
            html += '</tbody></table></div>';
        }

        host.innerHTML = html;
    }

};
