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
        '.om-top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px}' +
        '.om-top .page-title{margin:0;flex:1 1 auto}' +
        '.om-top label{font-size:12px;color:var(--text-secondary);display:inline-flex;align-items:center;gap:6px}' +
        /* painel: pilha, KPIs e grades */
        '.om-stack{display:flex;flex-direction:column;gap:14px;min-width:0}' +
        '.om-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}' +
        '.om-kpi{display:flex;flex-direction:column;min-width:0;padding:12px 16px 12px;background:var(--bg-panel);border:1px solid var(--border-subtle);border-top:3px solid var(--om-c,var(--color-primary));border-radius:var(--radius)}' +
        '.om-kpi.om-go{cursor:pointer}' +
        '.om-kpi.om-go:hover,.om-kpi.om-go:focus-visible{background:var(--bg-panel-alt);border-color:var(--border-light);border-top-color:var(--om-c);outline:0}' +
        '.om-kpi-top{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}' +
        '.om-kpi-ic{flex:0 0 32px;width:32px;height:32px;border-radius:6px;display:flex;align-items:center;justify-content:center;color:var(--om-c);background:var(--om-cbg)}' +
                '.om-kpi-l{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);line-height:1.3;padding-top:3px;min-width:0}' +
        '.om-kpi-v{font-size:22px;font-weight:700;line-height:1.2;margin-top:6px;color:var(--text-primary);font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
        '.om-kpi-v .text-muted{font-size:15px;font-weight:600}' +
        '.om-kpi-s{font-size:12px;color:var(--text-muted);margin-top:3px;line-height:1.35}' +
        '.om-g32{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:14px}' +
        '.om-g3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}' +
        /* cards de gráfico */
        '.om-chart{display:flex;flex-direction:column;min-width:0}' +
        '.om-chart .card-header{font-size:13px;padding:10px 16px}' +
        '.om-chart-body{flex:1 1 auto;display:flex;flex-direction:column;justify-content:center;padding:14px 16px 6px;min-height:200px}' +
        '.om-chart svg{display:block;width:100%;height:auto;max-height:280px;overflow:visible}' +
        '.om-chart svg text{font-family:inherit;font-size:11px;fill:var(--text-muted)}' +
        '.om-grid{stroke:rgba(255,255,255,.06);stroke-width:1;shape-rendering:crispEdges}' +
        '.om-cota{stroke:var(--text-secondary);stroke-width:1.2;stroke-dasharray:5 4}' +
        '.om-tk-cota{fill:var(--text-secondary)}' +
        '.om-dtrack{fill:none;stroke:var(--bg-panel-alt);stroke-width:16}' +
        '.om-dc{fill:var(--text-primary);font-size:14px;font-weight:700;font-variant-numeric:tabular-nums}' +
        '.om-dc2{fill:var(--text-muted);font-size:9px;text-transform:uppercase;letter-spacing:.04em}' +
        '.om-legend{display:flex;flex-wrap:wrap;gap:6px 14px;padding:6px 16px 10px;font-size:12px;color:var(--text-secondary)}' +
        '.om-sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}' +
        '.om-sw-line{height:0;border-top:2px dashed var(--text-secondary);vertical-align:2px;border-radius:0}' +
        '.om-foot{padding:8px 16px;border-top:1px solid var(--border-subtle);font-size:12px;color:var(--text-muted)}' +
        '.om-foot b{color:var(--text-secondary);font-weight:600}' +
        '.om-empty{color:var(--text-muted);font-size:12px;text-align:center;padding:24px 0}' +
        /* donut + legenda à direita */
        '.om-donut{display:flex;align-items:center;gap:18px;min-width:0}' +
        '.om-donut svg{flex:0 0 150px;width:150px;height:150px}' +
        '.om-dl{flex:1 1 auto;min-width:0;display:flex;flex-direction:column;gap:7px;font-size:12px}' +
        '.om-dl-row{display:grid;grid-template-columns:10px minmax(0,1fr) auto auto;gap:8px;align-items:center}' +
        '.om-dl-row .om-sw{margin:0}' +
        '.om-dl-n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-primary)}' +
        '.om-dl-v{color:var(--text-muted);white-space:nowrap;font-variant-numeric:tabular-nums}' +
        '.om-dl-p{font-weight:600;min-width:46px;text-align:right;font-variant-numeric:tabular-nums}' +
        /* barras horizontais empilhadas */
        '.om-hb{display:flex;flex-direction:column;gap:12px;font-size:12px}' +
        '.om-hb-row{display:grid;grid-template-columns:minmax(72px,112px) minmax(0,1fr) 36px;gap:10px;align-items:center}' +
        '.om-hb-n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
        '.om-hb-bar{display:flex;height:16px;border-radius:2px;overflow:hidden;background:var(--bg-panel-alt)}' +
        '.om-hb-seg{display:block;height:100%}' +
        '.om-hb-t{text-align:right;font-weight:600;font-variant-numeric:tabular-nums}' +
        /* detalhamento mensal */
        '.om-det-head{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid var(--border-subtle);font-weight:600}' +
        '.om-seg{display:flex;gap:6px;flex-wrap:wrap}' +
        '.om-pos{color:#5DD39E}.om-neg{color:#F27980}.om-flat{color:var(--text-muted)}' +
        '.om-scroll{overflow-x:auto}' +
        '.om-mtable{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}' +
        '.om-mtable th,.om-mtable td{padding:6px 10px;text-align:right;white-space:nowrap;border-top:1px solid var(--border-subtle)}' +
        '.om-mtable thead th{border-top:0;color:var(--text-secondary);font-size:11px;text-transform:uppercase;font-weight:600}' +
        '.om-mtable th:first-child,.om-mtable td:first-child{text-align:left;font-weight:600;position:sticky;left:0;background:var(--bg-panel);z-index:1}' +
        '.om-mtable thead th:first-child{font-weight:600;text-transform:none;letter-spacing:0}' +
        '.om-mtable tr.om-total td{font-weight:700;background:var(--bg-panel-alt)}' +
        '.om-mtable td.om-dash{color:var(--text-muted)}' +
        '.om-trend{display:inline-block;width:12px;margin-left:4px;font-size:10px;text-align:center}' +
        /* listas de pendências */
        '.om-cards2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}' +
        '.om-card-head{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:10px 16px;border-bottom:1px solid var(--border-subtle);font-weight:600}' +
        /* reparos / importar / config */
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
        '@media(max-width:1100px){.om-g32,.om-g3,.om-cards2{grid-template-columns:1fr}.om-kpis{grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}}' +
        '@media(max-width:560px){.om-donut{flex-direction:column;align-items:stretch}.om-donut svg{align-self:center}}' +
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
    // Cores fixas por entidade (nunca por posição).
    var FAM_COLOR = { COLETOR: '#F28C38', SLED: '#2FA39A' };
    var CAT_COLOR = { 'Coletor': '#F28C38', 'Coletor HF550X': '#C79105', 'Sled RFID': '#2FA39A', 'Sled RFR901': '#4C8DFF' };
    var CAT_OTHER = '#8A8F98';
    var EST_COLOR = { ok: '#2FB56B', bad: '#E5484D', pend: '#FFC107', orc: '#4C8DFF' };
    var DET_VIEWS = [
        ['consumo',          'Consumo (R$)',                         true,  false],
        ['reparados',        'Reparados',                            false, true],
        ['reprovados_valor', 'Reprovados — valor de aquisição (R$)', true,  false],
        ['reprovados_qtde',  'Reprovados (qtde)',                    false, false]
    ];
    function catColor(nome) { return CAT_COLOR[nome] || CAT_OTHER; }

    async function renderPainel(c) {
        c.innerHTML = STYLE +
            '<div class="om-top">' +
                '<h1 class="page-title">Orçamento de Manutenção — Coletores e SLEDs</h1>' +
                '<label>Ano <select id="om-ano" class="form-control form-control-inline"></select></label>' +
                '<button id="om-refresh" class="btn btn-outline btn-sm">Atualizar</button>' +
            '</div>' +
            '<div id="om-painel">' + SPIN + '</div>';

        var sel  = c.querySelector('#om-ano');
        var host = c.querySelector('#om-painel');
        var anoSel = null;

        async function load() {
            if (!host.isConnected) return;
            host.innerHTML = SPIN;
            var d;
            try {
                d = await S.api(BASE + '/resumo' + qs({ ano: anoSel }));
            } catch (e) {
                if (!host.isConnected) return;
                host.innerHTML = alertHtml(e.message);
                S.toast(e.message, 'error');
                return;
            }
            if (!host.isConnected) return;
            anoSel = d.ano;
            fillAnos(sel, d.anos_disponiveis, d.ano);
            host.innerHTML = painelHtml(d);
            bindPainel(host, d);
        }

        sel.addEventListener('change', function () { anoSel = sel.value; load(); });
        c.querySelector('#om-refresh').addEventListener('click', load);
        await load();
    }

    function bindPainel(host, d) {
        function go(el) { location.hash = '#' + ROUTE + '/' + el.getAttribute('data-go'); }
        host.querySelectorAll('.om-go').forEach(function (b) {
            b.addEventListener('click', function () { go(b); });
            if (b.tagName !== 'BUTTON') {
                b.addEventListener('keydown', function (ev) {
                    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); go(b); }
                });
            }
        });
        // Controle segmentado do detalhamento mensal.
        var seg = host.querySelector('.om-seg');
        var tbl = host.querySelector('#om-det-table');
        if (seg && tbl) {
            seg.addEventListener('click', function (ev) {
                var btn = ev.target.closest('button[data-key]');
                if (!btn || !seg.contains(btn)) return;
                seg.querySelectorAll('button').forEach(function (b) {
                    var on = b === btn;
                    b.classList.toggle('btn-primary', on);
                    b.classList.toggle('btn-outline', !on);
                });
                tbl.innerHTML = monthTable(d, btn.getAttribute('data-key'));
            });
        }
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

    /* ── formatação para gráficos ── */
    // 1234567 → 'R$ 1,23 mi' · 110691 → 'R$ 110,7 mil' · 950 → 'R$ 950'
    function abrev(v, dec) {
        v = Number(v || 0);
        var a = Math.abs(v);
        function f(n, dg) { return n.toLocaleString('pt-BR', { minimumFractionDigits: 0, maximumFractionDigits: dg }); }
        if (a >= 1e6) return 'R$ ' + f(v / 1e6, dec != null ? dec : 2) + ' mi';
        if (a >= 1e3) return 'R$ ' + f(v / 1e3, dec != null ? dec : 1) + ' mil';
        return 'R$ ' + f(v, 0);
    }
    function pct1(x) {
        return Number(x || 0).toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + ' %';
    }
    // Passo "redondo" para que 4 linhas de grade cubram `max`.
    function niceStep(raw, inteiro) {
        if (!(raw > 0)) return 1;
        if (inteiro && raw <= 1) return 1;
        var p = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
        var cands = inteiro && p < 10 ? [1, 2, 5, 10] : [1, 2, 2.5, 5, 10];
        for (var i = 0; i < cands.length; i++) if (cands[i] * p >= raw - 1e-9) return cands[i] * p;
        return 10 * p;
    }
    function sum(list, key) {
        return (list || []).reduce(function (a, r) { return a + Number((r && r[key]) || 0); }, 0);
    }
    // Mapa 'AAAA-MM' → item de d.meses, sempre na ordem jan..dez do ano.
    function mesesDoAno(d) {
        var ano = Number(d.ano) || new Date().getFullYear();
        var map = {};
        (d.meses || []).forEach(function (m) { if (m && m.mes) map[String(m.mes).slice(0, 7)] = m; });
        return MESES.map(function (_, i) {
            var key = ano + '-' + pad2(i + 1);
            return { key: key, item: map[key] || {} };
        });
    }
    function blkVal(m, key, fam) {
        var blk = (m && m[key]) || {};
        if (fam === 'TOTAL' && blk.TOTAL == null) return Number(blk.COLETOR || 0) + Number(blk.SLED || 0);
        return Number(blk[fam] || 0);
    }
    // Totais do ano com fallback (API antiga sem `totais`).
    function totaisDe(d) {
        var t = d.totais || {};
        var g = d.gerais || {};
        var meses = mesesDoAno(d);
        var aprov = t.aprovados != null ? Number(t.aprovados) : Number((g.COLETOR || {}).reparados || 0) + Number((g.SLED || {}).reparados || 0);
        var reprov = t.reprovados != null ? Number(t.reprovados)
            : meses.reduce(function (a, m) { return a + blkVal(m.item, 'reprovados_qtde', 'TOTAL'); }, 0);
        var reprovV = t.reprovados_valor != null ? Number(t.reprovados_valor)
            : meses.reduce(function (a, m) { return a + blkVal(m.item, 'reprovados_valor', 'TOTAL'); }, 0);
        var pend = t.pendentes != null ? Number(t.pendentes) : sum(d.aguardando_aprovacao, 'qtde');
        var mc = t.meses_com_consumo != null ? Number(t.meses_com_consumo)
            : meses.filter(function (m) { return blkVal(m.item, 'consumo', 'TOTAL') > 0; }).length;
        var media = t.media_mensal != null ? Number(t.media_mensal) : (mc ? Number(d.total_investido || 0) / mc : 0);
        var cota = Number(d.cota_mensal || 0);
        var pctMes = t.percentual_cota_mes != null ? Number(t.percentual_cota_mes)
            : (cota > 0 ? Number(d.consumo_atual || 0) / cota : null);
        return {
            aprovados: aprov, reprovados: reprov, reprovados_valor: reprovV, pendentes: pend,
            garantia: Number(t.garantia || 0), meses_com_consumo: mc, media_mensal: media,
            cota_anual: t.cota_anual != null ? Number(t.cota_anual) : cota * 12,
            percentual_cota_mes: pctMes
        };
    }

    /* ── ícones (SVG monocromático, currentColor) ── */
    var ICON = {
        cota:   '<path d="M4 6h16v12H4z"/><circle cx="12" cy="12" r="2.5"/><path d="M7 9v6M17 9v6"/>',
        consumo:'<path d="M4 18V6M4 18h16"/><path d="M7 14l3-4 3 2 4-6"/>',
        residual:'<path d="M12 3a9 9 0 1 0 9 9"/><path d="M12 3v9h9"/>',
        total:  '<path d="M5 20V10M10 20V4M15 20v-7M20 20v-4"/>',
        reparo: '<path d="M14.5 4.5a4 4 0 0 0-5 5L4 15l3 3 5.5-5.5a4 4 0 0 0 5-5l-2.5 2.5-2-2z"/>',
        reprov: '<circle cx="12" cy="12" r="8"/><path d="M9 9l6 6M15 9l-6 6"/>',
        aprov:  '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
        devol:  '<path d="M4 12a8 8 0 1 1 3 6.2"/><path d="M4 18v-6h6"/>'
    };
    function iconSvg(name) {
        return '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            (ICON[name] || ICON.total) + '</svg>';
    }

    /* ── componentes ── */
    function kpiCard(o) {
        var go = o.go ? ' om-go" role="button" tabindex="0" data-go="' + S.esc(o.go) : '';
        return '<div class="om-kpi' + go + '" style="--om-c:' + o.color + ';--om-cbg:' + o.color + '26"' +
                (o.title ? ' title="' + S.esc(o.title) + '"' : '') + '>' +
            '<div class="om-kpi-top"><div class="om-kpi-l">' + S.esc(o.label) + '</div>' +
                '<div class="om-kpi-ic">' + iconSvg(o.icon) + '</div></div>' +
            '<div class="om-kpi-v">' + o.value + '</div>' +
            '<div class="om-kpi-s">' + o.sub + '</div>' +
            '</div>';
    }
    function chartCard(titulo, body, legend, total) {
        return '<div class="card om-chart">' +
            '<div class="card-header">' + S.esc(titulo) + '</div>' +
            '<div class="om-chart-body">' + body + '</div>' +
            (legend ? '<div class="om-legend">' + legend + '</div>' : '') +
            (total != null ? '<div class="om-foot">Total: <b>' + total + '</b></div>' : '') +
            '</div>';
    }
    function legendItem(color, label) {
        return '<span><i class="om-sw" style="background:' + color + '"></i>' + S.esc(label) + '</span>';
    }
    function emptyHtml(msg) { return '<div class="om-empty">' + S.esc(msg) + '</div>'; }

    function kpisHtml(d, t) {
        var e = S.esc;
        var cota = Number(d.cota_mensal || 0);
        var semCota = !(cota > 0);
        var residual = Number(d.residual || 0);
        var g = d.gerais || {};
        var agAprQ = sum(d.aguardando_aprovacao, 'qtde'), agAprV = sum(d.aguardando_aprovacao, 'valor');
        var agDevT = sum(d.aguardando_devolucao, 'total'), agDevM = sum(d.aguardando_devolucao, 'ag_manutencao');
        var mesLbl = d.cota_em_uso ? 'Consumo · ' + fmtMes(d.cota_em_uso) : 'Consumo do mês';

        var cards = [
            kpiCard({ icon: 'cota', color: '#C79105', label: 'Cota mensal',
                value: semCota ? '<span class="text-muted">não configurada</span>' : money(cota),
                sub: semCota ? 'defina na aba Configuração' : 'cota anual ' + e(money(t.cota_anual)),
                title: semCota ? 'Defina a cota do ano na aba Configuração' : '' }),
            kpiCard({ icon: 'consumo', color: '#F28C38', label: mesLbl,
                value: money(d.consumo_atual),
                sub: semCota ? 'cota não definida'
                    : (t.percentual_cota_mes != null ? e(pct1(t.percentual_cota_mes * 100)) + ' da cota' : 'sem consumo no mês') }),
            kpiCard({ icon: 'residual', color: semCota ? '#8A8F98' : (residual >= 0 ? '#2FB56B' : '#E5484D'),
                label: 'Residual do mês',
                value: semCota ? '<span class="text-muted">–</span>' : money(residual),
                sub: semCota ? 'cota não definida' : (residual >= 0 ? 'sobra' : 'acima da cota') }),
            kpiCard({ icon: 'total', color: '#2FA39A', label: 'Total investido no ano',
                value: money(d.total_investido),
                sub: t.meses_com_consumo
                    ? 'média ' + e(money(t.media_mensal)) + ' em ' + fmtInt(t.meses_com_consumo) + (t.meses_com_consumo === 1 ? ' mês' : ' meses')
                    : 'sem consumo no ano' }),
            kpiCard({ icon: 'reparo', color: '#2FB56B', label: 'Equipamentos reparados',
                value: fmtInt(t.aprovados),
                sub: 'COLETOR ' + fmtInt((g.COLETOR || {}).reparados) + ' · SLED ' + fmtInt((g.SLED || {}).reparados) +
                     (t.garantia > 0 ? ' · ' + fmtInt(t.garantia) + ' em garantia' : '') }),
            kpiCard({ icon: 'reprov', color: '#E5484D', label: 'Reprovados',
                value: fmtInt(t.reprovados),
                sub: e(money(t.reprovados_valor)) + ' em valor de aquisição' }),
            kpiCard({ icon: 'aprov', color: '#FFC107', label: 'Aguardando aprovação',
                value: fmtInt(agAprQ), sub: e(money(agAprV)) + ' em orçamentos',
                go: 'reparos?status=AGUARDANDO_APROVACAO', title: 'Ver reparos aguardando aprovação' }),
            kpiCard({ icon: 'devol', color: '#4C8DFF', label: 'Aguardando devolução',
                value: fmtInt(agDevT), sub: fmtInt(agDevM) + ' aprovados em manutenção',
                go: 'reparos?status_retorno=EM_MANUTENCAO', title: 'Ver reparos em manutenção' })
        ];
        return '<div class="om-kpis">' + cards.join('') + '</div>';
    }

    // Barras empilhadas COLETOR+SLED por mês, com linha da cota.
    function consumoChart(d) {
        var meses = mesesDoAno(d);
        var cota = Number(d.cota_mensal || 0);
        var vals = meses.map(function (m) {
            return { key: m.key, c: blkVal(m.item, 'consumo', 'COLETOR'), s: blkVal(m.item, 'consumo', 'SLED') };
        });
        var totalAno = d.total_investido != null ? Number(d.total_investido) : vals.reduce(function (a, v) { return a + v.c + v.s; }, 0);
        var max = Math.max.apply(null, vals.map(function (v) { return v.c + v.s; }).concat([cota]));
        if (!(max > 0)) return chartCard('Consumo mensal × cota', emptyHtml('Sem consumo registrado no ano.'), null, money(0));

        var W = 720, H = 260, L = 66, R = 14, T = 18, B = 28, iw = W - L - R, ih = H - T - B;
        var step = niceStep(max * 1.05 / 4), top = step * 4;
        function y(v) { return T + ih - (v / top) * ih; }
        var s = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Consumo mensal por família">';
        for (var k = 0; k <= 4; k++) {
            var yy = y(k * step).toFixed(1);
            s += '<line class="om-grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + yy + '" y2="' + yy + '"/>' +
                 '<text class="om-tk" x="' + (L - 8) + '" y="' + yy + '" dy="4" text-anchor="end">' + (k ? abrev(k * step, 1) : '0') + '</text>';
        }
        var slot = iw / 12, bw = Math.round(slot * 0.58);
        vals.forEach(function (v, i) {
            var x = (L + i * slot + (slot - bw) / 2).toFixed(1);
            s += '<text class="om-tk" x="' + (L + i * slot + slot / 2).toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle">' + MESES[i] + '</text>';
            var base = 0;
            [['COLETOR', v.c], ['SLED', v.s]].forEach(function (p) {
                if (!(p[1] > 0)) return;
                var y1 = y(base + p[1]), y0 = y(base);
                var hgt = Math.max(1, y0 - y1);
                s += '<rect x="' + x + '" y="' + (y0 - hgt).toFixed(1) + '" width="' + bw + '" height="' + hgt.toFixed(1) + '" fill="' + FAM_COLOR[p[0]] + '">' +
                     '<title>' + S.esc(fmtMes(v.key) + ' · ' + p[0] + ' · ' + money(p[1])) + '</title></rect>';
                base += p[1];
            });
        });
        if (cota > 0) {
            var yc = y(cota).toFixed(1);
            s += '<line class="om-cota" x1="' + L + '" x2="' + (W - R) + '" y1="' + yc + '" y2="' + yc + '"/>' +
                 '<text class="om-tk om-tk-cota" x="' + (W - R) + '" y="' + yc + '" dy="-5" text-anchor="end">cota ' + S.esc(abrev(cota, 1)) + '</text>';
        }
        s += '</svg>';
        var legend = legendItem(FAM_COLOR.COLETOR, 'COLETOR') + legendItem(FAM_COLOR.SLED, 'SLED') +
            (cota > 0 ? '<span><i class="om-sw om-sw-line"></i>cota mensal</span>' : '');
        return chartCard('Consumo mensal × cota', s, legend, money(totalAno));
    }

    // Barras finas agrupadas: reparados (verde) × reprovados (vermelho) por mês.
    function qtdeChart(d) {
        var meses = mesesDoAno(d);
        var vals = meses.map(function (m) {
            return { key: m.key, a: blkVal(m.item, 'reparados', 'TOTAL'), r: blkVal(m.item, 'reprovados_qtde', 'TOTAL') };
        });
        var max = Math.max.apply(null, vals.map(function (v) { return Math.max(v.a, v.r); }));
        var totA = vals.reduce(function (a, v) { return a + v.a; }, 0), totR = vals.reduce(function (a, v) { return a + v.r; }, 0);
        if (!(max > 0)) return chartCard('Reparados × reprovados por mês', emptyHtml('Sem reparos registrados no ano.'), null, '0');

        var W = 380, H = 220, L = 40, R = 8, T = 14, B = 24, iw = W - L - R, ih = H - T - B;
        var step = niceStep(max * 1.05 / 4, true), top = step * 4;
        function y(v) { return T + ih - (v / top) * ih; }
        var s = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Reparados e reprovados por mês">';
        for (var k = 0; k <= 4; k++) {
            var yy = y(k * step).toFixed(1);
            s += '<line class="om-grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + yy + '" y2="' + yy + '"/>' +
                 '<text class="om-tk" x="' + (L - 6) + '" y="' + yy + '" dy="4" text-anchor="end">' + fmtInt(k * step) + '</text>';
        }
        var slot = iw / 12, bw = Math.max(4, Math.floor(slot * 0.28)), gap = 2;
        vals.forEach(function (v, i) {
            var cx = L + i * slot + slot / 2;
            s += '<text class="om-tk" x="' + cx.toFixed(1) + '" y="' + (H - 7) + '" text-anchor="middle">' + MESES[i] + '</text>';
            [[v.a, EST_COLOR.ok, 'reparados', cx - gap / 2 - bw], [v.r, EST_COLOR.bad, 'reprovados', cx + gap / 2]].forEach(function (p) {
                if (!(p[0] > 0)) return;
                var hgt = Math.max(1, y(0) - y(p[0]));
                s += '<rect x="' + p[3].toFixed(1) + '" y="' + (y(0) - hgt).toFixed(1) + '" width="' + bw + '" height="' + hgt.toFixed(1) + '" fill="' + p[1] + '" rx="1">' +
                     '<title>' + S.esc(fmtMes(v.key) + ' · ' + p[2] + ' · ' + fmtInt(p[0])) + '</title></rect>';
            });
        });
        s += '</svg>';
        var legend = legendItem(EST_COLOR.ok, 'Reparados') + legendItem(EST_COLOR.bad, 'Reprovados');
        return chartCard('Reparados × reprovados por mês', s, legend, fmtInt(totA) + ' reparados · ' + fmtInt(totR) + ' reprovados');
    }

    // Donut genérico: items = [{label, value, color, fmt}], centro = texto grande.
    function donutHtml(items, centro, centroSub, fmtVal) {
        var total = items.reduce(function (a, it) { return a + it.value; }, 0);
        var used = items.filter(function (it) { return it.value > 0; });
        var size = 150, cx = 75, cy = 75, r = 56, C = 2 * Math.PI * r, gap = used.length > 1 ? 2.5 : 0;
        var s = '<svg viewBox="0 0 ' + size + ' ' + size + '" role="img">' +
            '<circle class="om-dtrack" cx="' + cx + '" cy="' + cy + '" r="' + r + '"/>' +
            '<g transform="rotate(-90 ' + cx + ' ' + cy + ')">';
        var off = 0;
        used.forEach(function (it) {
            var len = C * it.value / total;
            var dash = Math.max(0.5, len - gap);
            s += '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="' + it.color + '" stroke-width="16"' +
                 ' stroke-dasharray="' + dash.toFixed(2) + ' ' + (C - dash).toFixed(2) + '" stroke-dashoffset="' + (-off).toFixed(2) + '">' +
                 '<title>' + S.esc(it.label + ' · ' + fmtVal(it.value) + ' · ' + pct1(it.value / total * 100)) + '</title></circle>';
            off += len;
        });
        s += '</g>' +
            '<text class="om-dc" x="' + cx + '" y="' + cy + '" dy="' + (centroSub ? 2 : 5) + '" text-anchor="middle">' + S.esc(centro) + '</text>' +
            (centroSub ? '<text class="om-dc2" x="' + cx + '" y="' + cy + '" dy="16" text-anchor="middle">' + S.esc(centroSub) + '</text>' : '') +
            '</svg>';
        var leg = items.map(function (it) {
            return '<div class="om-dl-row"><i class="om-sw" style="background:' + it.color + '"></i>' +
                '<span class="om-dl-n" title="' + S.esc(it.label) + '">' + S.esc(it.label) + '</span>' +
                '<span class="om-dl-v">' + S.esc(fmtVal(it.value)) + '</span>' +
                '<span class="om-dl-p">' + S.esc(total ? pct1(it.value / total * 100) : '–') + '</span></div>';
        }).join('');
        return '<div class="om-donut">' + s + '<div class="om-dl">' + leg + '</div></div>';
    }

    function categoriaChart(d) {
        var cats = (d.categorias || []).map(function (r) {
            return { label: r.categoria || '—', value: Number(r.consumo || 0), color: catColor(r.categoria) };
        }).filter(function (it) { return it.value > 0; })
          .sort(function (a, b) { return b.value - a.value; });
        var total = cats.reduce(function (a, it) { return a + it.value; }, 0);
        var body = cats.length ? donutHtml(cats, abrev(total), 'consumo', money) : emptyHtml('Sem consumo por categoria no ano.');
        return chartCard('Consumo por categoria', body, null, money(total));
    }

    function resultadoChart(t) {
        var items = [
            { label: 'Aprovados',  value: t.aprovados,  color: EST_COLOR.ok },
            { label: 'Reprovados', value: t.reprovados, color: EST_COLOR.bad },
            { label: 'Pendentes',  value: t.pendentes,  color: EST_COLOR.pend }
        ];
        var total = items.reduce(function (a, it) { return a + it.value; }, 0);
        var body = total ? donutHtml(items, fmtInt(total), 'orçamentos', fmtInt) : emptyHtml('Sem orçamentos no ano.');
        return chartCard('Resultado dos orçamentos no ano', body, null, fmtInt(total) + ' orçamentos');
    }

    // Barras horizontais empilhadas por categoria (aguardando devolução).
    function devolucaoChart(list) {
        list = (list || []).filter(function (r) { return Number(r.total || 0) > 0; })
            .sort(function (a, b) { return Number(b.total || 0) - Number(a.total || 0); });
        var SEGS = [['ag_manutencao', EST_COLOR.ok, 'Ag. manutenção'], ['ag_orcamento', EST_COLOR.orc, 'Ag. orçamento'],
                    ['ag_aprovacao', EST_COLOR.pend, 'Ag. aprovação'], ['reprovado', EST_COLOR.bad, 'Reprovado']];
        var total = sum(list, 'total');
        if (!list.length) return chartCard('Aguardando devolução por categoria', emptyHtml('Nenhum equipamento aguardando devolução'), null, '0');
        var max = Math.max.apply(null, list.map(function (r) { return Number(r.total || 0); }));
        var rows = list.map(function (r) {
            var tot = Number(r.total || 0);
            var segs = SEGS.map(function (sg) {
                var v = Number(r[sg[0]] || 0);
                if (!(v > 0)) return '';
                return '<span class="om-hb-seg" style="width:' + (v / max * 100).toFixed(2) + '%;background:' + sg[1] + '"' +
                       ' title="' + S.esc((r.categoria || '—') + ' · ' + sg[2] + ' · ' + fmtInt(v)) + '"></span>';
            }).join('');
            return '<div class="om-hb-row"><span class="om-hb-n" title="' + S.esc(r.categoria || '—') + '">' + S.esc(r.categoria || '—') + '</span>' +
                '<span class="om-hb-bar">' + segs + '</span><span class="om-hb-t">' + fmtInt(tot) + '</span></div>';
        }).join('');
        var legend = SEGS.map(function (sg) { return legendItem(sg[1], sg[2]); }).join('');
        return chartCard('Aguardando devolução por categoria', '<div class="om-hb">' + rows + '</div>', legend, fmtInt(total) + ' equipamentos');
    }

    function detalhamentoHtml(d) {
        var seg = DET_VIEWS.map(function (v, i) {
            return '<button class="btn btn-sm ' + (i === 0 ? 'btn-primary' : 'btn-outline') + '" data-key="' + v[0] + '">' + S.esc(v[1]) + '</button>';
        }).join('');
        return '<div class="card">' +
            '<div class="om-det-head"><span>Detalhamento mensal</span><div class="om-seg">' + seg + '</div></div>' +
            '<div id="om-det-table" class="om-scroll">' + monthTable(d, DET_VIEWS[0][0]) + '</div></div>';
    }

    function painelHtml(d) {
        var t = totaisDe(d);
        return '<div class="om-stack">' +
            kpisHtml(d, t) +
            '<div class="om-g32">' + consumoChart(d) + categoriaChart(d) + '</div>' +
            '<div class="om-g3">' + qtdeChart(d) + resultadoChart(t) + devolucaoChart(d.aguardando_devolucao) + '</div>' +
            detalhamentoHtml(d) +
            '<div class="om-cards2">' + aprovacaoHtml(d.aguardando_aprovacao) + devolucaoHtml(d.aguardando_devolucao) + '</div>' +
            '</div>';
    }

    // Tabela JAN..DEZ × COLETOR/SLED/TOTAL para a chave `key` de d.meses[].
    function monthTable(d, key) {
        var view = DET_VIEWS.filter(function (v) { return v[0] === key; })[0] || DET_VIEWS[0];
        var isMoney = view[2], goodUp = view[3];
        var meses = mesesDoAno(d);
        var fams = ['COLETOR', 'SLED', 'TOTAL'];
        var vals = {};
        fams.forEach(function (f) {
            vals[f] = meses.map(function (m) { return blkVal(m.item, key, f); });
        });

        function cell(v, extra) {
            if (!v) return '<td class="om-dash">–</td>';
            return '<td>' + (isMoney ? fmtDec(v) : fmtInt(v)) + (extra || '') + '</td>';
        }
        // ▲▼▬ na linha TOTAL: verde quando a variação é boa (goodUp: subir é bom).
        function trend(cur, prev, i) {
            if (!cur || i === 0) return '<span class="om-trend"></span>';
            var diff = cur - prev;
            if (Math.abs(diff) < 0.005) return '<span class="om-trend om-flat">▬</span>';
            var up = diff > 0;
            var good = up ? goodUp : !goodUp;
            return '<span class="om-trend ' + (good ? 'om-pos' : 'om-neg') + '">' + (up ? '▲' : '▼') + '</span>';
        }

        var h = '<table class="om-mtable"><thead><tr><th>' + S.esc(view[1]) + '</th>' +
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
        return h + '</tbody></table>';
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

        var host = c.querySelector('#om-cfg');
        // Busca sempre dentro deste host: a tela pode ser renderizada duas vezes
        // (clique + hashchange) e a cópia antiga não pode mexer na nova.
        function q(id) { return host.querySelector('#' + id); }
        var cfg;
        try {
            cfg = await S.api(BASE + '/config') || {};
        } catch (e) {
            host.innerHTML = alertHtml(e.message);
            S.toast(e.message, 'error');
            return;
        }
        if (!host.isConnected) return;  // renderização substituída por outra

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

        var cotaRows = q('om-cota-rows');
        var vcpRows = q('om-vcp-rows');

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

        q('om-cota-add').onclick = function () {
            var anos = Object.keys(collect(cotaRows)).map(Number);
            var prox = anos.length ? Math.max.apply(null, anos) + 1 : new Date().getFullYear();
            add(cotaRows, 'Ano', 'number', String(prox));
        };
        q('om-vcp-add').onclick = function () { add(vcpRows, 'Categoria', 'text'); };

        q('om-cfg-save').onclick = async function () {
            var pct = Number(String(q('om-limiar').value).replace(',', '.'));
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
                q('om-cfg-msg').textContent = 'Salvo em ' + new Date().toLocaleString('pt-BR') + '.';
            } catch (e) {
                S.toast(e.message, 'error');
            }
        };

        q('om-recalc').onclick = async function () {
            if (!window.confirm('Recalcular percentual e avaliação de todos os reparos e aplicar a regra dos 60 % nos pendentes?')) return;
            try {
                var r = await busy(function () { return S.api(BASE + '/recalcular', { method: 'POST' }); });
                var txt = 'Recálculo concluído — ' + resumoNumerico(r);
                S.toast(txt, 'success');
                q('om-cfg-msg').textContent = txt;
            } catch (e) {
                S.toast(e.message, 'error');
            }
        };
    }

})();
