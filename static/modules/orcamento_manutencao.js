/* ================================================================
   Módulo: Orçamento de Manutenção — Coletores e SLEDs
   Contrato: docs/ORCAMENTO_MANUTENCAO.md  (API /api/orcamento-manutencao)
   Sub-abas: painel · reparos · importar (admin) · config (admin)
   ================================================================ */
(function () {
    'use strict';

    var BASE  = '/orcamento-manutencao';
    var ROUTE = 'orcamento_manutencao';
    var PAGE  = 100;
    var MESES = ['JAN', 'FEV', 'MAR', 'ABR', 'MAI', 'JUN',
                 'JUL', 'AGO', 'SET', 'OUT', 'NOV', 'DEZ'];

    var STATUS_LABEL = {
        APROVADO:             'Aprovado',
        REPROVADO:            'Reprovado',
        AGUARDANDO_APROVACAO: 'Aguardando Aprovação',
        AGUARDANDO_ORCAMENTO: 'Aguardando Orçamento',
        VALIDANDO_ORCAMENTO:  'Validando Orçamento'
    };
    var STATUS_CLS = {
        APROVADO:             'success',
        REPROVADO:            'danger',
        AGUARDANDO_APROVACAO: 'warning',
        AGUARDANDO_ORCAMENTO: 'info',
        VALIDANDO_ORCAMENTO:  'info'
    };
    var RETORNO_LABEL = { DEVOLVIDO: 'Devolvido', EM_MANUTENCAO: 'Em manutenção' };
    var TIPO_LABEL    = { CONTRATO: 'Contrato', AVULSA: 'Avulsa' };
    var FAMILIA_LABEL = { COLETOR: 'Coletor', SLED: 'SLED', OUTRO: 'Outro' };
    var FONTE_CLS     = { EBS: 'teal', PLANILHA: 'gold', PADRAO: 'default', MANUAL: 'orange' };
    var AVISO_LABEL = {
        status_nao_reconhecido: 'Status não reconhecido',
        tipo_nao_reconhecido:   'Tipo de manutenção não reconhecido',
        empresa_vazia:          'Empresa vazia (o EBS pode completar)'
    };

    // Usados quando GET /opcoes falha ou não traz a lista.
    var DEFAULTS = {
        categorias:     ['Coletor', 'Coletor HF550X', 'Coletor S70', 'Sled RFID', 'Sled RFR901'],
        status:         Object.keys(STATUS_LABEL),
        tipos:          Object.keys(TIPO_LABEL),
        empresas:       ['RENNER', 'CAMICADO', 'YOUCOM'],
        status_retorno: Object.keys(RETORNO_LABEL),
        familias:       Object.keys(FAMILIA_LABEL),
        lotes:          [],
        anos:           []
    };

    var SPIN = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando...</div>';

    var STYLE =
        '<style>' +
        '.om-top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:16px}' +
        '.om-top .page-title{margin:0;flex:1 1 auto}' +
        '.om-top label{font-size:12px;color:var(--text-secondary);display:inline-flex;align-items:center;gap:6px}' +
        '.om-layout{display:grid;grid-template-columns:300px minmax(0,1fr);gap:16px;margin-bottom:16px;align-items:start}' +
        '.om-side,.om-main{display:flex;flex-direction:column;gap:16px;min-width:0}' +
        '.om-kv{display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:9px 16px;border-top:1px solid var(--border-subtle)}' +
        '.om-kv:first-child{border-top:0}' +
        '.om-k{font-size:11px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:.02em}' +
        '.om-v{font-weight:600;white-space:nowrap;font-variant-numeric:tabular-nums}' +
        '.om-v.om-big{font-size:17px}' +
        '.om-pos{color:#5DD39E}.om-neg{color:#F27980}.om-flat{color:var(--text-muted)}' +
        '.om-scroll{overflow-x:auto}' +
        '.om-gfam{padding:9px 16px 2px;font-size:11px;font-weight:700;letter-spacing:.04em;color:var(--color-gold);border-top:1px solid var(--border-subtle)}' +
        '.card-header+.om-gfam,.om-gfam+.om-kv{border-top:0}' +
        '.om-mtable{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}' +
        '.om-mtable th,.om-mtable td{padding:6px 10px;text-align:right;white-space:nowrap;border-top:1px solid var(--border-subtle)}' +
        '.om-mtable thead th{border-top:0;color:var(--text-secondary);font-size:11px;text-transform:uppercase;font-weight:600}' +
        '.om-mtable th:first-child,.om-mtable td:first-child{text-align:left;font-weight:600;position:sticky;left:0;background:var(--bg-panel);z-index:1}' +
        '.om-mtable tr.om-total td{font-weight:700;background:var(--bg-panel-alt)}' +
        '.om-mtable td.om-dash{color:var(--text-muted)}' +
        '.om-trend{display:inline-block;width:12px;margin-left:4px;font-size:10px;text-align:center}' +
        '.om-cards2{display:grid;grid-template-columns:1fr 1fr;gap:16px}' +
        '.om-card-head{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:10px 16px;border-bottom:1px solid var(--border-subtle);font-weight:600}' +
        '.data-table .om-num,.om-num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}' +
        '.om-row-click{cursor:pointer}' +
        '.om-src{font-size:9px;padding:1px 5px;margin-left:5px;vertical-align:middle}' +
        '.om-acoes{white-space:nowrap}' +
        '.om-pager{display:flex;align-items:center;gap:8px;justify-content:flex-end;margin-top:10px;flex-wrap:wrap;font-size:12px;color:var(--text-secondary)}' +
        '.om-kvrow{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto;gap:8px;align-items:center;margin-bottom:8px}' +
        '.om-info{font-size:12px;color:var(--text-secondary);background:var(--bg-panel-alt);border:1px solid var(--border-subtle);padding:8px 10px;border-radius:var(--radius);display:grid;grid-template-columns:1fr 1fr;gap:4px 12px}' +
        '.om-info b{color:var(--text-primary);font-weight:500}' +
        '.om-full{grid-column:1/-1}' +
        '.om-tw{max-height:60vh}' +
        '.om-tw td{white-space:nowrap}' +
        '.om-tw td.om-lote{white-space:normal;min-width:140px;max-width:260px}' +
        '.om-hint{font-size:12px;color:var(--text-muted);margin:4px 0 0}' +
        '@media(max-width:1100px){.om-layout{grid-template-columns:1fr}.om-cards2{grid-template-columns:1fr}}' +
        '</style>';

    var S = null;   // window.SPARE, atribuído a cada render()

    window.SPARE_MODULES = window.SPARE_MODULES || {};
    window.SPARE_MODULES.orcamento_manutencao = {

        async render(container, sub) {
            S = window.SPARE;
            var p = perms();
            var parsed = parseSub(sub);
            var tab = parsed.tab;

            var TABS = [['painel', 'Painel'], ['reparos', 'Reparos']];
            if (p.admin) TABS.push(['importar', 'Importar'], ['config', 'Configuração']);
            if (!TABS.some(function (t) { return t[0] === tab; })) tab = 'painel';
            S.tabs(TABS, tab, ROUTE);

            var handlers = {
                painel:   renderPainel,
                reparos:  renderReparos,
                importar: renderImportar,
                config:   renderConfig
            };
            try {
                await handlers[tab](container, p, parsed.params);
            } catch (e) {
                container.innerHTML = alertHtml(e.message);
                S.toast(e.message, 'error');
            }
        }
    };

    /* ── Permissões e utilitários ─────────────────────────────────── */
    function perms() {
        var u = (S.user && S.user()) || {};
        var pm = (u.permission_map || {})[ROUTE] || {};
        var admin = !!(u.is_admin || pm.can_admin);
        return {
            admin:    admin,
            create:   admin || !!pm.can_create,
            edit:     admin || !!pm.can_edit,
            exportar: admin || !!pm.can_export
        };
    }

    // "reparos?status=APROVADO" → { tab: 'reparos', params: { status: 'APROVADO' } }
    function parseSub(sub) {
        var s = String(sub || '');
        var q = s.indexOf('?');
        var tab = (q === -1 ? s : s.slice(0, q)) || 'painel';
        var params = {};
        try {
            new URLSearchParams(q === -1 ? '' : s.slice(q + 1)).forEach(function (v, k) { params[k] = v; });
        } catch (_) { /* sem parâmetros */ }
        return { tab: tab, params: params };
    }

    function qs(obj) {
        var u = new URLSearchParams();
        Object.keys(obj || {}).forEach(function (k) {
            var v = obj[k];
            if (v !== '' && v != null) u.append(k, v);
        });
        var s = u.toString();
        return s ? '?' + s : '';
    }

    function alertHtml(msg, tipo) {
        return '<div class="alert alert-' + (tipo || 'danger') + '">' + S.esc(msg || 'Erro.') + '</div>';
    }

    function pad2(n) { return (n < 10 ? '0' : '') + n; }
    function money(x) { return S.money(x); }
    function fmtInt(n) { return Number(n || 0).toLocaleString('pt-BR'); }
    function fmtDec(n) {
        return Number(n || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    function fmtPct(p) {
        if (p == null || p === '' || isNaN(Number(p))) return '—';
        return (Number(p) * 100).toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + ' %';
    }
    // 'AAAA-MM' → 'OUT/2026'
    function fmtMes(m) {
        if (!m) return '—';
        m = String(m);
        if (!/^\d{4}-\d{2}/.test(m)) return m;
        var i = parseInt(m.slice(5, 7), 10) - 1;
        return (MESES[i] || m.slice(5, 7)) + '/' + m.slice(0, 4);
    }
    function fmtDateTime(x) {
        if (!x) return '';
        var d = new Date(x);
        return isNaN(d.getTime()) ? String(x) : d.toLocaleString('pt-BR');
    }
    function mesAtual() {
        var d = new Date();
        return d.getFullYear() + '-' + pad2(d.getMonth() + 1);
    }

    function badgeHtml(text, cls, extraCls) {
        return '<span class="badge badge-' + cls + (extraCls ? ' ' + extraCls : '') + '">' + S.esc(text) + '</span>';
    }
    function statusBadge(st) {
        if (!st) return '<span class="text-muted">—</span>';
        return badgeHtml(STATUS_LABEL[st] || st, STATUS_CLS[st] || 'default');
    }
    function avalBadge(av) {
        if (av === 'DENTRO') return badgeHtml('DENTRO', 'success');
        if (av === 'FORA') return badgeHtml('FORA', 'danger');
        return '';
    }

    // Normaliza listas de GET /opcoes (aceita string ou {value,label}).
    function opts(opcoes, key, labels) {
        var raw = (opcoes && Array.isArray(opcoes[key]) && opcoes[key].length) ? opcoes[key] : (DEFAULTS[key] || []);
        var out = [], seen = {};
        raw.forEach(function (x) {
            var v, l;
            if (x && typeof x === 'object') {
                v = x.valor != null ? x.valor : (x.value != null ? x.value : (x.codigo != null ? x.codigo : (x.id != null ? x.id : x.nome)));
                l = x.label || x.rotulo || x.nome || v;
            } else {
                v = x;
                l = (labels && labels[x]) || x;
            }
            if (v == null || v === '') return;
            v = String(v);
            if (seen[v]) return;
            seen[v] = 1;
            out.push({ value: v, label: String(l) });
        });
        return out;
    }

    function selectHtml(id, label, list, value, emptyLabel, disabled) {
        var h = '<div class="form-group"><label>' + S.esc(label) + '</label>' +
            '<select id="' + id + '" class="form-control"' + (disabled ? ' disabled' : '') + '>';
        if (emptyLabel != null) h += '<option value="">' + S.esc(emptyLabel) + '</option>';
        list.forEach(function (o) {
            h += '<option value="' + S.esc(o.value) + '"' +
                (String(value == null ? '' : value) === o.value ? ' selected' : '') + '>' +
                S.esc(o.label) + '</option>';
        });
        return h + '</select></div>';
    }

    function inputHtml(id, label, type, value, extra, disabled) {
        return '<div class="form-group"><label>' + S.esc(label) + '</label>' +
            '<input id="' + id + '" type="' + (type || 'text') + '" class="form-control"' +
            ' value="' + S.esc(value == null ? '' : value) + '"' + (extra || '') +
            (disabled ? ' disabled' : '') + '></div>';
    }

    async function busy(fn) {
        try { S.loading(true); return await fn(); }
        finally { S.loading(false); }
    }

    function download(blob, nome) {
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = nome;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(a.href);
    }

    // Resume respostas curtas ({"alteradas": 12}) sem depender do nome da chave.
    function resumoNumerico(r) {
        if (typeof r === 'number') return fmtInt(r);
        if (r && typeof r === 'object') {
            var parts = Object.keys(r).filter(function (k) {
                return typeof r[k] === 'number' || typeof r[k] === 'string' || typeof r[k] === 'boolean';
            }).map(function (k) {
                var v = r[k];
                return k.replace(/_/g, ' ') + ': ' + (typeof v === 'number' ? fmtInt(v) : String(v));
            });
            return parts.length ? parts.join(' · ') : 'concluído';
        }
        return 'concluído';
    }

    /* ── Painel ───────────────────────────────────────────────────── */
    async function renderPainel(c) {
        c.innerHTML = STYLE +
            '<div class="om-top">' +
                '<h1 class="page-title">Orçamento de Manutenção — Coletores e SLEDs</h1>' +
                '<label>Ano <select id="om-ano" class="form-control form-control-inline"></select></label>' +
                '<button id="om-refresh" class="btn btn-outline btn-sm">Atualizar</button>' +
            '</div>' +
            '<div id="om-painel">' + SPIN + '</div>';

        var sel = document.getElementById('om-ano');
        var anoSel = null;

        async function load() {
            var host = document.getElementById('om-painel');
            if (!host) return;
            host.innerHTML = SPIN;
            try {
                var d = await S.api(BASE + '/resumo' + qs({ ano: anoSel }));
                anoSel = d.ano;
                fillAnos(sel, d.anos_disponiveis, d.ano);
                host.innerHTML = painelHtml(d);
                host.querySelectorAll('.om-go').forEach(function (b) {
                    b.onclick = function () { location.hash = ROUTE + '/' + b.getAttribute('data-go'); };
                });
            } catch (e) {
                host.innerHTML = alertHtml(e.message);
                S.toast(e.message, 'error');
            }
        }

        sel.onchange = function () { anoSel = sel.value; load(); };
        document.getElementById('om-refresh').onclick = load;
        await load();
    }

    function fillAnos(sel, anos, atual) {
        var list = (anos || []).map(Number).filter(function (a) { return a; });
        var cur = Number(atual) || new Date().getFullYear();
        if (list.indexOf(cur) === -1) list.push(cur);
        list.sort(function (a, b) { return b - a; });
        sel.innerHTML = list.map(function (a) {
            return '<option value="' + a + '"' + (a === cur ? ' selected' : '') + '>' + a + '</option>';
        }).join('');
    }

    function kv(k, v, big) {
        return '<div class="om-kv"><span class="om-k">' + S.esc(k) + '</span>' +
            '<span class="om-v' + (big ? ' om-big' : '') + '">' + v + '</span></div>';
    }

    function painelHtml(d) {
        var e = S.esc;
        var residual = Number(d.residual || 0);
        var semCota = !(Number(d.cota_mensal) > 0);
        var fin = '<div class="card"><div class="card-header">FINANCEIRO</div>' +
            kv('Cota mensal', semCota
                ? '<span class="text-muted" title="Defina a cota do ano na aba Configuração">não configurada</span>'
                : money(d.cota_mensal)) +
            kv('Cota em uso (mês)', e(fmtMes(d.cota_em_uso))) +
            kv('Consumo atual', money(d.consumo_atual)) +
            kv('Residual', semCota
                ? '<span class="text-muted">–</span>'
                : '<span class="' + (residual >= 0 ? 'om-pos' : 'om-neg') + '">' + money(d.residual) + '</span>', true) +
            kv('Total investido', money(d.total_investido)) +
            '</div>';

        var g = d.gerais || {};
        function geraisFam(rotulo, x) {
            x = x || {};
            return '<div class="om-gfam">' + S.esc(rotulo) + '</div>' +
                kv('Consumo', money(x.consumo)) +
                kv('Equipamentos reparados', fmtInt(x.reparados)) +
                kv('Média por reparo', money(x.media));
        }
        var gerais = '<div class="card"><div class="card-header">INFORMAÇÕES GERAIS</div>' +
            geraisFam('COLETOR', g.COLETOR) +
            geraisFam('SLED', g.SLED) +
            (d.limiar_percentual != null
                ? '<div class="om-kv"><span class="om-k">Limiar da regra</span><span class="om-v">' + e(fmtPct(d.limiar_percentual)) + '</span></div>'
                : '') +
            '</div>';

        var tabelas =
            monthTable('Consumo mês a mês (R$)', d, 'consumo', true, false) +
            monthTable('Equipamentos reparados', d, 'reparados', false, true) +
            monthTable('Reprovados — valor de aquisição (R$)', d, 'reprovados_valor', true, false) +
            monthTable('Equipamentos reprovados', d, 'reprovados_qtde', false, false);

        return '<div class="om-layout">' +
                '<div class="om-side">' + fin + gerais + '</div>' +
                '<div class="om-main">' + tabelas + '</div>' +
            '</div>' +
            '<div class="om-cards2">' + aprovacaoHtml(d.aguardando_aprovacao) + devolucaoHtml(d.aguardando_devolucao) + '</div>';
    }

    // Tabela JAN..DEZ × COLETOR/SLED/TOTAL. goodUp: subir é bom (reparados).
    function monthTable(titulo, d, key, isMoney, goodUp) {
        var ano = Number(d.ano) || new Date().getFullYear();
        var map = {};
        (d.meses || []).forEach(function (m) {
            if (m && m.mes) map[String(m.mes).slice(0, 7)] = m;
        });
        var fams = ['COLETOR', 'SLED', 'TOTAL'];
        var vals = {};
        fams.forEach(function (f) {
            vals[f] = MESES.map(function (_, i) {
                var m = map[ano + '-' + pad2(i + 1)];
                var blk = (m && m[key]) || {};
                if (f === 'TOTAL' && blk.TOTAL == null) {
                    return Number(blk.COLETOR || 0) + Number(blk.SLED || 0);
                }
                return Number(blk[f] || 0);
            });
        });

        function cell(v, extra) {
            if (!v) return '<td class="om-dash">–</td>';
            return '<td>' + (isMoney ? fmtDec(v) : fmtInt(v)) + (extra || '') + '</td>';
        }
        function trend(cur, prev, i) {
            if (!cur || i === 0) return '<span class="om-trend"></span>';
            var diff = cur - prev;
            if (Math.abs(diff) < 0.005) return '<span class="om-trend om-flat">▬</span>';
            var up = diff > 0;
            var good = up ? goodUp : !goodUp;
            return '<span class="om-trend ' + (good ? 'om-pos' : 'om-neg') + '">' + (up ? '▲' : '▼') + '</span>';
        }

        var h = '<div class="card"><div class="card-header">' + S.esc(titulo) + '</div>' +
            '<div class="om-scroll"><table class="om-mtable"><thead><tr><th></th>' +
            MESES.map(function (m) { return '<th>' + m + '</th>'; }).join('') +
            '</tr></thead><tbody>';
        fams.forEach(function (f) {
            var total = f === 'TOTAL';
            h += '<tr' + (total ? ' class="om-total"' : '') + '><td>' + (total ? 'TOTAL' : f) + '</td>';
            vals[f].forEach(function (v, i) {
                h += cell(v, total ? trend(v, vals[f][i - 1] || 0, i) : '');
            });
            h += '</tr>';
        });
        return h + '</tbody></table></div></div>';
    }

    function aprovacaoHtml(list) {
        list = list || [];
        var tq = 0, tv = 0;
        var rows = list.map(function (r) {
            tq += Number(r.qtde || 0); tv += Number(r.valor || 0);
            return '<tr><td>' + S.esc(r.categoria || '—') +
                (r.familia ? ' <span class="text-muted">(' + S.esc(FAMILIA_LABEL[r.familia] || r.familia) + ')</span>' : '') + '</td>' +
                '<td class="om-num">' + fmtInt(r.qtde) + '</td>' +
                '<td class="om-num">' + money(r.valor) + '</td></tr>';
        }).join('');
        if (!rows) rows = '<tr><td colspan="3" class="empty-row">Nenhum reparo aguardando aprovação.</td></tr>';
        else rows += '<tr><td><b>TOTAL</b></td><td class="om-num"><b>' + fmtInt(tq) + '</b></td>' +
            '<td class="om-num"><b>' + money(tv) + '</b></td></tr>';
        return '<div class="card"><div class="om-card-head"><span>Aguardando aprovação</span>' +
            '<button class="btn btn-sm btn-outline om-go" data-go="reparos?status=AGUARDANDO_APROVACAO">ver reparos</button></div>' +
            '<div class="table-wrapper" style="border:0"><table class="data-table"><thead><tr>' +
            '<th>Categoria</th><th class="om-num">Qtde</th><th class="om-num">Valor</th></tr></thead>' +
            '<tbody>' + rows + '</tbody></table></div></div>';
    }

    function devolucaoHtml(list) {
        list = list || [];
        var t = { total: 0, ag_manutencao: 0, ag_orcamento: 0, ag_aprovacao: 0, reprovado: 0 };
        var cols = ['total', 'ag_manutencao', 'ag_orcamento', 'ag_aprovacao', 'reprovado'];
        var rows = list.map(function (r) {
            cols.forEach(function (k) { t[k] += Number(r[k] || 0); });
            return '<tr><td>' + S.esc(r.categoria || '—') +
                (r.familia ? ' <span class="text-muted">(' + S.esc(FAMILIA_LABEL[r.familia] || r.familia) + ')</span>' : '') + '</td>' +
                cols.map(function (k) { return '<td class="om-num">' + fmtInt(r[k]) + '</td>'; }).join('') + '</tr>';
        }).join('');
        if (!rows) rows = '<tr><td colspan="6" class="empty-row">Nenhum equipamento em manutenção.</td></tr>';
        else rows += '<tr><td><b>TOTAL</b></td>' +
            cols.map(function (k) { return '<td class="om-num"><b>' + fmtInt(t[k]) + '</b></td>'; }).join('') + '</tr>';
        return '<div class="card"><div class="om-card-head"><span>Aguardando devolução</span>' +
            '<button class="btn btn-sm btn-outline om-go" data-go="reparos?status_retorno=EM_MANUTENCAO">ver reparos</button></div>' +
            '<div class="table-wrapper" style="border:0"><table class="data-table"><thead><tr>' +
            '<th>Categoria</th><th class="om-num">Total</th><th class="om-num">Ag. manutenção</th>' +
            '<th class="om-num">Ag. orçamento</th><th class="om-num">Ag. aprovação</th><th class="om-num">Reprovado</th>' +
            '</tr></thead><tbody>' + rows + '</tbody></table></div></div>';
    }

    /* ── Reparos ──────────────────────────────────────────────────── */
    async function renderReparos(c, p, preset) {
        var FILTROS = ['ano', 'mes', 'familia', 'categoria', 'status', 'status_retorno',
                       'empresa', 'tipo_manutencao', 'q'];
        var filtros = {};
        FILTROS.forEach(function (k) { filtros[k] = preset && preset[k] != null ? preset[k] : ''; });
        var offset = 0, total = 0, itens = [], opcoes = {};

        c.innerHTML = STYLE +
            '<div class="om-top">' +
                '<h1 class="page-title">Orçamento de Manutenção — Reparos</h1>' +
                (p.create ? '<button id="om-novo" class="btn btn-primary btn-sm">Novo reparo</button>' : '') +
                (p.exportar ? '<button id="om-export" class="btn btn-outline btn-sm">Exportar</button>' : '') +
            '</div>' +
            '<div class="card mb-3"><div class="card-body">' +
                '<div id="om-filtros" class="filter-grid">' + SPIN + '</div>' +
                '<div class="btn-row mt-2">' +
                    '<button id="om-f-apply" class="btn btn-primary btn-sm">Filtrar</button>' +
                    '<button id="om-f-clear" class="btn btn-outline btn-sm">Limpar</button>' +
                    '<span id="om-total" class="text-muted"></span>' +
                '</div>' +
            '</div></div>' +
            '<div id="om-lista">' + SPIN + '</div>' +
            '<div id="om-pager" class="om-pager"></div>';

        try {
            opcoes = await S.api(BASE + '/opcoes') || {};
        } catch (e) {
            opcoes = {};
            S.toast('Não foi possível carregar as opções de filtro: ' + e.message, 'warning');
        }
        renderFiltros();

        function anosOpts() {
            var list = opts(opcoes, 'anos');
            if (!list.length) {
                var y = new Date().getFullYear();
                list = [y, y - 1, y - 2].map(function (a) { return { value: String(a), label: String(a) }; });
            }
            if (filtros.ano && !list.some(function (o) { return o.value === String(filtros.ano); })) {
                list.unshift({ value: String(filtros.ano), label: String(filtros.ano) });
            }
            return list;
        }

        function renderFiltros() {
            var e = S.esc;
            document.getElementById('om-filtros').innerHTML =
                selectHtml('om-fl-ano', 'Ano', anosOpts(), filtros.ano, 'Todos') +
                '<div class="form-group"><label>Mês</label><input id="om-fl-mes" type="month" class="form-control" value="' + e(filtros.mes) + '"></div>' +
                selectHtml('om-fl-familia', 'Família', opts(opcoes, 'familias', FAMILIA_LABEL), filtros.familia, 'Todas') +
                selectHtml('om-fl-categoria', 'Categoria', opts(opcoes, 'categorias'), filtros.categoria, 'Todas') +
                selectHtml('om-fl-status', 'Status', opts(opcoes, 'status', STATUS_LABEL), filtros.status, 'Todos') +
                selectHtml('om-fl-status_retorno', 'Status de retorno', opts(opcoes, 'status_retorno', RETORNO_LABEL), filtros.status_retorno, 'Todos') +
                selectHtml('om-fl-empresa', 'Empresa', opts(opcoes, 'empresas'), filtros.empresa, 'Todas') +
                selectHtml('om-fl-tipo_manutencao', 'Tipo de manutenção', opts(opcoes, 'tipos', TIPO_LABEL), filtros.tipo_manutencao, 'Todos') +
                '<div class="form-group"><label>Busca (RMA, série, lote)</label><input id="om-fl-q" class="form-control" placeholder="RMA, série ou lote" value="' + e(filtros.q) + '"></div>';
            document.getElementById('om-fl-q').onkeydown = function (ev) {
                if (ev.key === 'Enter') aplicar();
            };
        }

        function lerFiltros() {
            FILTROS.forEach(function (k) {
                var i = document.getElementById('om-fl-' + k);
                filtros[k] = i ? i.value.trim() : '';
            });
        }

        function aplicar() { lerFiltros(); offset = 0; load(); }

        async function load() {
            var host = document.getElementById('om-lista');
            if (!host) return;
            host.innerHTML = SPIN;
            try {
                var d = await S.api(BASE + '/reparos' + qs(Object.assign({}, filtros, { limit: PAGE, offset: offset })));
                itens = d.itens || [];
                total = Number(d.total || 0);
                host.innerHTML = listaHtml(itens, p);
                renderPager();
                document.getElementById('om-total').textContent = fmtInt(total) + ' reparo(s)';
            } catch (e) {
                host.innerHTML = alertHtml(e.message);
                S.toast(e.message, 'error');
            }
        }

        function renderPager() {
            var pg = document.getElementById('om-pager');
            if (!pg) return;
            var ini = total ? offset + 1 : 0;
            var fim = Math.min(offset + PAGE, total);
            pg.innerHTML =
                '<span>Mostrando ' + fmtInt(ini) + '–' + fmtInt(fim) + ' de ' + fmtInt(total) + '</span>' +
                '<button id="om-prev" class="btn btn-outline btn-sm"' + (offset <= 0 ? ' disabled' : '') + '>Anterior</button>' +
                '<button id="om-next" class="btn btn-outline btn-sm"' + (fim >= total ? ' disabled' : '') + '>Próxima</button>';
            document.getElementById('om-prev').onclick = function () { offset = Math.max(0, offset - PAGE); load(); };
            document.getElementById('om-next').onclick = function () { offset += PAGE; load(); };
        }

        document.getElementById('om-f-apply').onclick = aplicar;
        document.getElementById('om-f-clear').onclick = function () {
            FILTROS.forEach(function (k) { filtros[k] = ''; });
            renderFiltros();
            offset = 0;
            load();
        };

        var novo = document.getElementById('om-novo');
        if (novo) novo.onclick = function () { openForm(null, p, opcoes, load); };

        var exp = document.getElementById('om-export');
        if (exp) exp.onclick = function () {
            lerFiltros();
            busy(async function () {
                var r = await S.api(BASE + '/exportar.xlsx' + qs(filtros));
                if (!r || typeof r.blob !== 'function') throw new Error('Resposta inesperada da exportação.');
                download(await r.blob(), 'orcamento_manutencao.xlsx');
            }).catch(function (e) { S.toast(e.message, 'error'); });
        };

        // Clique na linha / botões de ação (delegação: a tabela é recriada a cada load)
        document.getElementById('om-lista').addEventListener('click', function (ev) {
            var btn = ev.target.closest('.om-act');
            if (btn) {
                ev.stopPropagation();
                var it = itens[+btn.getAttribute('data-i')];
                if (!it) return;
                if (btn.getAttribute('data-act') === 'del') excluir(it, load);
                else openForm(it, p, opcoes, load);
                return;
            }
            var tr = ev.target.closest('tr[data-i]');
            if (tr) {
                var item = itens[+tr.getAttribute('data-i')];
                if (item) openForm(item, p, opcoes, load);
            }
        });

        await load();
    }

    function listaHtml(itens, p) {
        var acoes = p.edit || p.admin;
        var h = '<div class="table-wrapper om-tw"><table class="data-table"><thead><tr>' +
            '<th>RMA</th><th>Série</th><th class="om-num">Loja</th><th>Categoria</th><th>Empresa</th>' +
            '<th class="om-num">Orçamento</th><th class="om-num">Valor compra</th><th class="om-num">%</th>' +
            '<th>Status</th><th>Retorno</th><th>Mês</th><th>Tipo</th><th>Lote</th>' +
            (acoes ? '<th>Ações</th>' : '') +
            '</tr></thead><tbody>';
        if (!itens.length) {
            h += '<tr><td colspan="' + (acoes ? 14 : 13) + '" class="empty-row">Nenhum registro encontrado.</td></tr>';
        } else {
            itens.forEach(function (r, i) { h += rowHtml(r, i, p); });
        }
        return h + '</tbody></table></div>';
    }

    function rowHtml(r, i, p) {
        var e = S.esc;
        var orc = r.garantia
            ? badgeHtml('Garantia', 'teal')
            : money(r.orcamento);
        var vc = (r.valor_compra == null)
            ? '<span class="text-muted">—</span>'
            : money(r.valor_compra) + (r.valor_compra_fonte
                ? badgeHtml(r.valor_compra_fonte, FONTE_CLS[r.valor_compra_fonte] || 'default', 'om-src')
                : '');
        var pct = (r.percentual == null)
            ? '<span class="text-muted">—</span>'
            : e(fmtPct(r.percentual)) + ' ' + avalBadge(r.avaliacao);
        var acoes = '';
        if (p.edit)  acoes += '<button class="btn btn-sm btn-outline om-act" data-act="edit" data-i="' + i + '">Editar</button> ';
        if (p.admin) acoes += '<button class="btn btn-sm btn-outline-danger om-act" data-act="del" data-i="' + i + '">Excluir</button>';
        return '<tr data-i="' + i + '" class="om-row-click">' +
            '<td>' + e(r.rma) + '</td>' +
            '<td>' + e(r.serie) + '</td>' +
            '<td class="om-num">' + (r.loja == null ? '' : e(r.loja)) + '</td>' +
            '<td>' + e(r.categoria) +
                (r.familia ? ' <span class="text-muted">(' + e(FAMILIA_LABEL[r.familia] || r.familia) + ')</span>' : '') + '</td>' +
            '<td>' + e(r.empresa || '—') + '</td>' +
            '<td class="om-num">' + orc + '</td>' +
            '<td class="om-num">' + vc + '</td>' +
            '<td class="om-num">' + pct + '</td>' +
            '<td>' + statusBadge(r.status) + '</td>' +
            '<td>' + e(RETORNO_LABEL[r.status_retorno] || r.status_retorno || '—') + '</td>' +
            '<td>' + e(fmtMes(r.mes_referencia)) + '</td>' +
            '<td>' + e(TIPO_LABEL[r.tipo_manutencao] || r.tipo_manutencao || '—') + '</td>' +
            '<td class="om-lote">' + e(r.lote_prime || '') + '</td>' +
            (p.edit || p.admin ? '<td class="om-acoes">' + acoes + '</td>' : '') +
            '</tr>';
    }

    /* ── Modal novo/editar ────────────────────────────────────────── */
    function formHtml(r, opcoes, ro) {
        var e = S.esc;
        r = r || {};
        var isEdit = !!r.id;
        var cats = opts(opcoes, 'categorias');
        if (r.categoria && !cats.some(function (c) { return c.value === r.categoria; })) {
            cats.push({ value: r.categoria, label: r.categoria });
        }
        cats = cats.concat([{ value: '__outra__', label: 'Outra… (digitar)' }]);
        var empresas = opts(opcoes, 'empresas');
        if (r.empresa && !empresas.some(function (c) { return c.value === r.empresa; })) {
            empresas.push({ value: r.empresa, label: r.empresa });
        }
        var lotes = opts(opcoes, 'lotes');

        var info = '';
        if (isEdit) {
            function li(k, v) { return '<div>' + e(k) + ': <b>' + v + '</b></div>'; }
            info = '<div class="om-info om-full">' +
                li('Percentual', e(fmtPct(r.percentual)) + ' ' + avalBadge(r.avaliacao)) +
                li('Fonte do valor de compra', e(r.valor_compra_fonte || '—')) +
                li('Status original', e(r.status_original || '—')) +
                li('EBS consultado em', e(fmtDateTime(r.ebs_consultado_em) || 'nunca')) +
                (r.ebs_erro ? li('Erro EBS', e(r.ebs_erro)) : '') +
                li('Origem', e(r.origem || '—')) +
                li('Família', e(FAMILIA_LABEL[r.familia] || r.familia || '—')) +
                li('Atualizado', e((fmtDateTime(r.atualizado_em) || '—') + (r.atualizado_por ? ' por ' + r.atualizado_por : ''))) +
                '</div>';
        }

        return '<div class="form-grid cols-2">' + info +
            inputHtml('om-f-rma', 'RMA *', 'text', r.rma, ' maxlength="40"', ro) +
            inputHtml('om-f-serie', 'Série *', 'text', r.serie, ' maxlength="60"', ro) +
            inputHtml('om-f-loja', 'Loja', 'number', r.loja, ' min="0" step="1"', ro) +
            selectHtml('om-f-categoria', 'Categoria *', cats, r.categoria || '', isEdit ? null : 'Selecione…', ro) +
            '<div class="form-group" id="om-f-categoria-outra-wrap" hidden><label>Nova categoria</label>' +
                '<input id="om-f-categoria-outra" class="form-control" placeholder="ex.: Coletor S70"' + (ro ? ' disabled' : '') + '></div>' +
            inputHtml('om-f-orcamento', 'Orçamento (R$)', 'number', r.orcamento == null ? '' : r.orcamento, ' step="0.01" min="0"', ro) +
            '<div class="form-group"><label>&nbsp;</label><label class="checkbox-label">' +
                '<input id="om-f-garantia" type="checkbox"' + (r.garantia ? ' checked' : '') + (ro ? ' disabled' : '') + '> Reparo em garantia (sem custo)</label></div>' +
            selectHtml('om-f-status', 'Status', opts(opcoes, 'status', STATUS_LABEL), r.status || 'AGUARDANDO_APROVACAO', null, ro) +
            selectHtml('om-f-tipo_manutencao', 'Tipo de manutenção', opts(opcoes, 'tipos', TIPO_LABEL), r.tipo_manutencao || 'CONTRATO', null, ro) +
            selectHtml('om-f-status_retorno', 'Status de retorno', opts(opcoes, 'status_retorno', RETORNO_LABEL), r.status_retorno || 'EM_MANUTENCAO', null, ro) +
            inputHtml('om-f-mes_referencia', 'Mês (contrato)', 'month', r.mes_referencia ? String(r.mes_referencia).slice(0, 7) : (isEdit ? '' : mesAtual()), '', ro) +
            inputHtml('om-f-lote_prime', 'Lote Prime', 'text', r.lote_prime, ' list="om-lotes" maxlength="200"', ro) +
            '<datalist id="om-lotes">' + lotes.map(function (l) { return '<option value="' + e(l.value) + '">'; }).join('') + '</datalist>' +
            selectHtml('om-f-empresa', 'Empresa', empresas, r.empresa || '', '— (o EBS preenche)', ro) +
            inputHtml('om-f-valor_compra', 'Valor de compra (R$)', 'number', r.valor_compra == null ? '' : r.valor_compra,
                ' step="0.01" min="0" placeholder="deixe vazio para buscar no EBS"', ro) +
            '<div class="form-group om-full"><label>Observação</label>' +
                '<textarea id="om-f-observacao" class="form-control" rows="2"' + (ro ? ' disabled' : '') + '>' + e(r.observacao || '') + '</textarea></div>' +
            (isEdit ? '' : '<p class="om-hint om-full">Ao salvar, o portal consulta o EBS pela série para obter o valor de compra e a empresa, ' +
                'calcula o percentual e aplica a regra dos 60 % (reprovação automática quando acima do limiar).</p>') +
            '</div>';
    }

    function coletar(f) {
        function v(id) { var x = f.querySelector('#' + id); return x ? x.value : ''; }
        function num(s) { s = String(s).trim(); return s === '' ? null : Number(s.replace(',', '.')); }
        var cat = v('om-f-categoria');
        if (cat === '__outra__') cat = v('om-f-categoria-outra').trim();
        var garantia = !!f.querySelector('#om-f-garantia').checked;
        var orc = num(v('om-f-orcamento'));
        return {
            rma:              v('om-f-rma').trim(),
            serie:            v('om-f-serie').trim().toUpperCase(),
            loja:             num(v('om-f-loja')),
            categoria:        cat,
            orcamento:        garantia ? 0 : (orc == null ? 0 : orc),
            garantia:         garantia,
            status:           v('om-f-status'),
            tipo_manutencao:  v('om-f-tipo_manutencao'),
            status_retorno:   v('om-f-status_retorno'),
            mes_referencia:   v('om-f-mes_referencia') || null,
            lote_prime:       v('om-f-lote_prime').trim(),
            empresa:          v('om-f-empresa'),
            valor_compra:     num(v('om-f-valor_compra')),
            observacao:       v('om-f-observacao').trim()
        };
    }

    // Na edição só vai o que mudou: mandar valor_compra igual marcaria fonte MANUAL.
    function diff(body, orig) {
        var out = {};
        function norm(x) {
            if (x == null) return '';
            if (typeof x === 'number') return String(x);
            if (typeof x === 'boolean') return x ? '1' : '';
            return String(x);
        }
        Object.keys(body).forEach(function (k) {
            var a = body[k], b = orig[k];
            if (k === 'mes_referencia' && b) b = String(b).slice(0, 7);
            if (k === 'loja' || k === 'orcamento' || k === 'valor_compra') {
                if (norm(a === null ? '' : Number(a)) === norm(b == null || b === '' ? '' : Number(b))) return;
            } else if (norm(a) === norm(b)) return;
            out[k] = a;
        });
        return out;
    }

    function openForm(item, p, opcoes, onSaved) {
        var isEdit = !!(item && item.id);
        var ro = isEdit ? !p.edit : !p.create;
        var orig = item || {};
        var f = S.el('div');
        f.innerHTML = formHtml(orig, opcoes, ro);

        var catSel = f.querySelector('#om-f-categoria');
        var outraWrap = f.querySelector('#om-f-categoria-outra-wrap');
        catSel.onchange = function () { outraWrap.hidden = catSel.value !== '__outra__'; };

        var botoes = [];
        if (!ro) {
            botoes.push(S.el('button', { className: 'btn btn-primary', textContent: isEdit ? 'Salvar' : 'Incluir', onClick: salvar }));
        }
        if (isEdit && p.edit) {
            botoes.push(S.el('button', { className: 'btn btn-secondary', textContent: 'Reconsultar EBS', onClick: reconsultar }));
        }
        if (isEdit && p.admin) {
            botoes.push(S.el('button', { className: 'btn btn-outline-danger', textContent: 'Excluir', onClick: function () {
                excluir(orig, function () { S.closeModal(); if (onSaved) onSaved(); });
            } }));
        }
        botoes.push(S.el('button', { className: 'btn btn-outline', textContent: ro ? 'Fechar' : 'Cancelar', onClick: S.closeModal }));

        S.openModal(isEdit ? 'Reparo — RMA ' + (orig.rma || orig.id) : 'Novo reparo', f, botoes);

        async function salvar() {
            var body = coletar(f);
            if (!body.rma || !body.serie || !body.categoria) {
                S.toast('RMA, série e categoria são obrigatórios.', 'warning');
                return;
            }
            if (body.orcamento != null && (isNaN(body.orcamento) || body.orcamento < 0)) {
                S.toast('Orçamento inválido.', 'warning');
                return;
            }
            var enviado = body, statusEnviado = body.status;
            if (isEdit) {
                enviado = diff(body, orig);
                if (!Object.keys(enviado).length) {
                    S.toast('Nada para salvar.', 'info');
                    return;
                }
                statusEnviado = enviado.status != null ? enviado.status : orig.status;
            }
            try {
                var r = await busy(function () {
                    return S.api(BASE + '/reparos' + (isEdit ? '/' + orig.id : ''), {
                        method: isEdit ? 'PUT' : 'POST',
                        body: enviado
                    });
                });
                r = r || {};
                S.toast(isEdit ? 'Reparo atualizado.' : 'Reparo incluído.', 'success');
                if (!isEdit) {
                    var ebs = r.ebs || {};
                    if (ebs.consultado) {
                        S.toast('EBS consultado: valor de compra ' + money(r.valor_compra) +
                            (r.empresa ? ' · ' + r.empresa : '') + '.', 'success');
                    } else if (ebs.erro) {
                        S.toast('EBS não retornou valor (' + ebs.erro + '). Fonte usada: ' +
                            (r.valor_compra_fonte || 'nenhuma') + '.', 'warning');
                    }
                }
                if (r.avaliacao === 'FORA' && r.status === 'REPROVADO' && statusEnviado !== 'REPROVADO') {
                    S.toast('Reprovado automaticamente: orçamento em ' + fmtPct(r.percentual) +
                        ' do valor de compra, acima do limiar.', 'warning');
                } else if (r.avaliacao === 'FORA') {
                    S.toast('Atenção: orçamento em ' + fmtPct(r.percentual) + ' do valor de compra (FORA do limiar).', 'warning');
                }
                S.closeModal();
                if (onSaved) onSaved();
            } catch (e) {
                S.toast(e.message, 'error');
            }
        }

        async function reconsultar() {
            try {
                var r = await busy(function () {
                    return S.api(BASE + '/reparos/' + orig.id + '/ebs', { method: 'POST' });
                });
                r = r || {};
                var falhas = r.falhas || [];
                if (falhas.length) S.toast('EBS: ' + (falhas[0].erro || 'falha na consulta'), 'warning');
                else if (r.atualizados) S.toast('Valor de compra/empresa atualizados pelo EBS.', 'success');
                else S.toast('EBS consultado; nada a atualizar (valor manual ou série não encontrada).', 'info');

                // Recarrega a linha para refletir no formulário (não há GET /reparos/{id}).
                var novo = r.id ? r : null;
                if (!novo) {
                    try {
                        var d = await S.api(BASE + '/reparos' + qs({ q: orig.rma, limit: 10 }));
                        novo = (d.itens || []).filter(function (x) { return x.id === orig.id; })[0] || null;
                    } catch (_) { novo = null; }
                }
                if (novo) {
                    orig = novo;
                    var vc = f.querySelector('#om-f-valor_compra');
                    if (vc) vc.value = novo.valor_compra == null ? '' : novo.valor_compra;
                    var emp = f.querySelector('#om-f-empresa');
                    if (emp && novo.empresa) {
                        if (!Array.prototype.some.call(emp.options, function (o) { return o.value === novo.empresa; })) {
                            emp.appendChild(S.el('option', { value: novo.empresa, textContent: novo.empresa }));
                        }
                        emp.value = novo.empresa;
                    }
                    var info = f.querySelector('.om-info');
                    if (info) {
                        var tmp = S.el('div');
                        tmp.innerHTML = formHtml(novo, opcoes, ro);
                        var novoInfo = tmp.querySelector('.om-info');
                        if (novoInfo) info.replaceWith(novoInfo);
                    }
                }
                if (onSaved) onSaved();
            } catch (e) {
                S.toast(e.message, 'error');
            }
        }
    }

    function excluir(item, depois) {
        if (!item || !item.id) return;
        if (!window.confirm('Excluir o reparo RMA ' + (item.rma || item.id) + '? Esta ação não pode ser desfeita.')) return;
        busy(function () {
            return S.api(BASE + '/reparos/' + item.id, { method: 'DELETE' });
        }).then(function () {
            S.toast('Reparo excluído.', 'success');
            if (depois) depois();
        }).catch(function (e) { S.toast(e.message, 'error'); });
    }

    /* ── Importar (admin) ─────────────────────────────────────────── */
    async function renderImportar(c, p) {
        if (!p.admin) { c.innerHTML = STYLE + alertHtml('Acesso restrito ao administrador.', 'warning'); return; }
        c.innerHTML = STYLE +
            '<div class="om-top"><h1 class="page-title">Orçamento de Manutenção — Importar planilha</h1></div>' +
            '<div class="card mb-3">' +
                '<div class="card-header">Importar planilha de manutenção</div>' +
                '<div class="card-body">' +
                    '<p class="text-muted" style="margin-top:0">Aceita .xlsx ou .csv com as colunas da planilha ' +
                    '(RMA, SÉRIE, LOJA, CATEGORIA, EMPRESA, ORÇAMENTO, 60% Orçamento, STATUS ORÇAMENTO, TIPO DE MANUTENÇÃO, ' +
                    'STATUS DE RETORNO, ANO, MÊS CONTRATO, ANO DEVOLUÇÃO, LOTE PRIME, QTDE). ' +
                    'RMA já cadastrado é atualizado; o restante é incluído. O EBS não é consultado nesta etapa.</p>' +
                    '<div class="form-row-inline">' +
                        '<input id="om-imp-file" type="file" accept=".xlsx,.csv" class="form-control" style="max-width:420px">' +
                        '<button id="om-imp-btn" class="btn btn-primary">Importar</button>' +
                    '</div>' +
                    '<div id="om-imp-result" class="mt-3"></div>' +
                '</div>' +
            '</div>' +
            '<div class="card">' +
                '<div class="card-header">Completar valor de compra pelo EBS</div>' +
                '<div class="card-body">' +
                    '<p class="text-muted" style="margin-top:0">Consulta o EBS pela série para até 200 reparos sem valor de compra ou sem empresa ' +
                    '(os mais recentes primeiro). Valores digitados manualmente não são sobrescritos. Repita até zerar as pendências.</p>' +
                    '<button id="om-ebs-btn" class="btn btn-secondary">Completar valor de compra pelo EBS (até 200)</button>' +
                    '<div id="om-ebs-result" class="mt-3"></div>' +
                '</div>' +
            '</div>';

        document.getElementById('om-imp-btn').onclick = async function () {
            var inp = document.getElementById('om-imp-file');
            var file = inp.files && inp.files[0];
            if (!file) { S.toast('Selecione uma planilha (.xlsx ou .csv).', 'warning'); return; }
            var fd = new FormData();
            fd.append('file', file);
            var out = document.getElementById('om-imp-result');
            out.innerHTML = SPIN;
            try {
                var r = await busy(function () {
                    return S.api(BASE + '/importar', { method: 'POST', body: fd });
                });
                out.innerHTML = importResultHtml(r || {});
                S.toast('Importação concluída: ' + fmtInt(r.incluidas) + ' incluída(s), ' +
                    fmtInt(r.atualizadas) + ' atualizada(s), ' + fmtInt(r.rejeitadas) + ' rejeitada(s).', 'success');
            } catch (e) {
                out.innerHTML = alertHtml(e.message);
                S.toast(e.message, 'error');
            }
        };

        document.getElementById('om-ebs-btn').onclick = async function () {
            var out = document.getElementById('om-ebs-result');
            out.innerHTML = SPIN;
            try {
                var r = await busy(function () {
                    return S.api(BASE + '/reparos/ebs-pendentes?limite=200', { method: 'POST' });
                });
                out.innerHTML = ebsResultHtml(r || {});
                S.toast('EBS: ' + fmtInt(r.consultados) + ' consultado(s), ' + fmtInt(r.atualizados) + ' atualizado(s).', 'success');
            } catch (e) {
                out.innerHTML = alertHtml(e.message);
                S.toast(e.message, 'error');
            }
        };
    }

    function statCard(valor, rotulo, accent) {
        return '<div class="stat-card' + (accent ? ' accent-' + accent : '') + '">' +
            '<div class="stat-value">' + valor + '</div>' +
            '<div class="stat-label">' + S.esc(rotulo) + '</div></div>';
    }

    function importResultHtml(r) {
        var e = S.esc;
        var h = '<div class="stats-grid mb-3">' +
            statCard(fmtInt(r.lidas), 'Linhas lidas', 'teal') +
            statCard(fmtInt(r.incluidas), 'Incluídas', 'green') +
            statCard(fmtInt(r.atualizadas), 'Atualizadas', 'gold') +
            statCard(fmtInt(r.rejeitadas), 'Rejeitadas', 'orange') +
            '</div>';
        var avisos = r.avisos || {};
        var avKeys = Object.keys(avisos).filter(function (k) { return Number(avisos[k]) > 0; });
        if (avKeys.length) {
            h += '<div class="alert alert-warning mb-3"><b>Avisos:</b> ' + avKeys.map(function (k) {
                return e(AVISO_LABEL[k] || k.replace(/_/g, ' ')) + ': ' + fmtInt(avisos[k]);
            }).join(' · ') + '</div>';
        }
        var det = r.detalhes || [];
        if (det.length) {
            h += '<div style="font-weight:600;margin-bottom:6px">Linhas rejeitadas' +
                (Number(r.rejeitadas) > det.length ? ' <span class="text-muted" style="font-weight:400">(mostrando as primeiras ' + fmtInt(det.length) + ')</span>' : '') +
                '</div>' +
                '<div class="table-wrapper om-tw"><table class="data-table"><thead><tr><th class="om-num">Linha</th><th>Motivo</th></tr></thead><tbody>' +
                det.map(function (x) {
                    return '<tr><td class="om-num">' + e(x.linha) + '</td><td>' + e(x.motivo) + '</td></tr>';
                }).join('') +
                '</tbody></table></div>';
        } else {
            h += '<div class="alert alert-success">Nenhuma linha rejeitada.</div>';
        }
        return h;
    }

    function ebsResultHtml(r) {
        var e = S.esc;
        var falhas = r.falhas || [];
        var h = '<div class="stats-grid mb-3">' +
            statCard(fmtInt(r.consultados), 'Consultados', 'teal') +
            statCard(fmtInt(r.atualizados), 'Atualizados', 'green') +
            statCard(fmtInt(falhas.length), 'Falhas', 'orange') +
            '</div>';
        if (falhas.length) {
            h += '<div class="table-wrapper om-tw"><table class="data-table"><thead><tr><th class="om-num">ID</th><th>Erro</th></tr></thead><tbody>' +
                falhas.map(function (x) {
                    return '<tr><td class="om-num">' + e(x.id) + '</td><td>' + e(x.erro) + '</td></tr>';
                }).join('') + '</tbody></table></div>';
        } else if (!Number(r.consultados)) {
            h += '<div class="alert alert-info">Nenhum reparo pendente de valor de compra ou empresa.</div>';
        }
        return h;
    }

    /* ── Configuração (admin) ─────────────────────────────────────── */
    async function renderConfig(c, p) {
        if (!p.admin) { c.innerHTML = STYLE + alertHtml('Acesso restrito ao administrador.', 'warning'); return; }
        c.innerHTML = STYLE +
            '<div class="om-top"><h1 class="page-title">Orçamento de Manutenção — Configuração</h1></div>' +
            '<div id="om-cfg">' + SPIN + '</div>';

        var host = document.getElementById('om-cfg');
        var cfg;
        try {
            cfg = await S.api(BASE + '/config') || {};
        } catch (e) {
            host.innerHTML = alertHtml(e.message);
            S.toast(e.message, 'error');
            return;
        }

        var limiar = cfg.limiar_percentual == null ? 0.6 : Number(cfg.limiar_percentual);
        host.innerHTML =
            '<div class="form-grid cols-2 mb-3">' +
                '<div class="card"><div class="card-header">Cota mensal por ano (R$)</div><div class="card-body">' +
                    '<div id="om-cota-rows"></div>' +
                    '<button id="om-cota-add" class="btn btn-sm btn-outline">Adicionar ano</button>' +
                '</div></div>' +
                '<div class="card"><div class="card-header">Regra dos 60 %</div><div class="card-body">' +
                    '<div class="form-group"><label>Limiar percentual (%)</label>' +
                        '<input id="om-limiar" type="number" step="0.1" min="0" max="100" class="form-control" value="' +
                        S.esc(String(Math.round(limiar * 10000) / 100)) + '"></div>' +
                    '<p class="om-hint">Orçamento acima deste percentual do valor de compra é avaliado como FORA e, se o status ainda estiver pendente, reprovado automaticamente. ' +
                    'Mudar o limiar não recalcula o histórico: use o botão Recalcular.</p>' +
                '</div></div>' +
            '</div>' +
            '<div class="card mb-3"><div class="card-header">Valor de compra padrão por categoria (R$)</div><div class="card-body">' +
                '<p class="om-hint" style="margin:0 0 10px">Usado quando o EBS e a planilha não informam o valor de aquisição do equipamento.</p>' +
                '<div id="om-vcp-rows"></div>' +
                '<button id="om-vcp-add" class="btn btn-sm btn-outline">Adicionar categoria</button>' +
            '</div></div>' +
            '<div class="btn-row">' +
                '<button id="om-cfg-save" class="btn btn-primary">Salvar</button>' +
                '<button id="om-recalc" class="btn btn-secondary">Recalcular percentuais e regra dos 60 %</button>' +
                '<span id="om-cfg-msg" class="text-muted"></span>' +
            '</div>';

        var cotaRows = document.getElementById('om-cota-rows');
        var vcpRows = document.getElementById('om-vcp-rows');

        function rowHtmlKV(k, v, phK, tipoK) {
            return '<div class="om-kvrow">' +
                '<input class="form-control om-k-in" type="' + tipoK + '" placeholder="' + phK + '" value="' + S.esc(k) + '">' +
                '<input class="form-control om-v-in" type="number" step="0.01" min="0" placeholder="Valor (R$)" value="' + S.esc(v == null ? '' : v) + '">' +
                '<button class="btn btn-sm btn-outline-danger om-rm" title="Remover">×</button>' +
                '</div>';
        }
        function fill(container, obj, phK, tipoK, sortDesc) {
            var keys = Object.keys(obj || {});
            keys.sort();
            if (sortDesc) keys.reverse();
            container.innerHTML = keys.map(function (k) { return rowHtmlKV(k, obj[k], phK, tipoK); }).join('');
        }
        function add(container, phK, tipoK, k) {
            var tmp = S.el('div');
            tmp.innerHTML = rowHtmlKV(k || '', '', phK, tipoK);
            container.appendChild(tmp.firstChild);
            var inp = container.lastChild.querySelector(k ? '.om-v-in' : '.om-k-in');
            if (inp) inp.focus();
        }
        function collect(container) {
            var out = {};
            container.querySelectorAll('.om-kvrow').forEach(function (row) {
                var k = row.querySelector('.om-k-in').value.trim();
                var v = row.querySelector('.om-v-in').value.trim();
                if (!k || v === '') return;
                out[k] = Number(v);
            });
            return out;
        }
        [cotaRows, vcpRows].forEach(function (cont) {
            cont.addEventListener('click', function (ev) {
                var b = ev.target.closest('.om-rm');
                if (b) { var row = b.closest('.om-kvrow'); if (row) row.remove(); }
            });
        });

        fill(cotaRows, cfg.cota_mensal, 'Ano', 'number', true);
        fill(vcpRows, cfg.valor_compra_padrao, 'Categoria', 'text', false);

        document.getElementById('om-cota-add').onclick = function () {
            var anos = Object.keys(collect(cotaRows)).map(Number);
            var prox = anos.length ? Math.max.apply(null, anos) + 1 : new Date().getFullYear();
            add(cotaRows, 'Ano', 'number', String(prox));
        };
        document.getElementById('om-vcp-add').onclick = function () { add(vcpRows, 'Categoria', 'text'); };

        document.getElementById('om-cfg-save').onclick = async function () {
            var pct = Number(String(document.getElementById('om-limiar').value).replace(',', '.'));
            if (isNaN(pct) || pct <= 0 || pct > 100) { S.toast('Limiar inválido: informe um percentual entre 0 e 100.', 'warning'); return; }
            var body = {
                cota_mensal: collect(cotaRows),
                limiar_percentual: Math.round(pct * 100) / 10000,
                valor_compra_padrao: collect(vcpRows)
            };
            try {
                var r = await busy(function () { return S.api(BASE + '/config', { method: 'PUT', body: body }); });
                if (r && typeof r === 'object') {
                    cfg = r;
                    if (r.cota_mensal) fill(cotaRows, r.cota_mensal, 'Ano', 'number', true);
                    if (r.valor_compra_padrao) fill(vcpRows, r.valor_compra_padrao, 'Categoria', 'text', false);
                }
                S.toast('Configuração salva.', 'success');
                document.getElementById('om-cfg-msg').textContent = 'Salvo em ' + new Date().toLocaleString('pt-BR') + '.';
            } catch (e) {
                S.toast(e.message, 'error');
            }
        };

        document.getElementById('om-recalc').onclick = async function () {
            if (!window.confirm('Recalcular percentual e avaliação de todos os reparos e aplicar a regra dos 60 % nos pendentes?')) return;
            try {
                var r = await busy(function () { return S.api(BASE + '/recalcular', { method: 'POST' }); });
                var txt = 'Recálculo concluído — ' + resumoNumerico(r);
                S.toast(txt, 'success');
                document.getElementById('om-cfg-msg').textContent = txt;
            } catch (e) {
                S.toast(e.message, 'error');
            }
        };
    }

})();
