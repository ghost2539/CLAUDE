/* ================================================================
   Consulta de Ativos — Standalone App
   ================================================================ */
(function () {
    'use strict';

    // Prefixo quando o portal é servido num subcaminho do proxy: o router
    // injeta <meta name="app-base">. Vazio na raiz do domínio.
    var BASE = (function () {
        var m = document.querySelector('meta[name="app-base"]');
        return (m && m.content ? m.content : '').replace(/\/+$/, '');
    })();

    // Proxy que acrescenta a barra por REDIRECIONAMENTO quebra POST (o 301
    // vira GET). Com a marca ligada, a URL já sai com a barra.
    var API_BARRA = !!document.querySelector('meta[name="api-barra-final"]');
    function comBarra(url) {
        if (!API_BARRA) return url;
        var corte = url.indexOf('?');
        var base = corte === -1 ? url : url.slice(0, corte);
        var query = corte === -1 ? '' : url.slice(corte);
        if (base.charAt(base.length - 1) !== '/') base += '/';
        return base + query;
    }



    var API = BASE + '/api/consulta-times';

    // ── DOM helpers ────────────────────────────────────────────────
    function $(sel) { return document.querySelector(sel); }

    function el(tag, attrs, children) {
        var e = document.createElement(tag);
        if (attrs) {
            Object.keys(attrs).forEach(function (k) {
                var v = attrs[k];
                if (k === 'className') e.className = v;
                else if (k === 'textContent') e.textContent = v;
                else if (k.indexOf('on') === 0 && k.length > 2)
                    e.addEventListener(k.slice(2).toLowerCase(), v);
                else e.setAttribute(k, v);
            });
        }
        if (Array.isArray(children)) {
            children.forEach(function (x) { if (x) e.appendChild(x); });
        } else if (children instanceof Node) {
            e.appendChild(children);
        } else if (typeof children === 'string') {
            e.innerHTML = children;
        }
        return e;
    }

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>'"]/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c];
        });
    }

    // ── API helper ─────────────────────────────────────────────────
    async function api(path, opts) {
        opts = opts || {};
        var headers = { 'Content-Type': 'application/json' };
        var body = opts.body;
        if (body && typeof body === 'object') body = JSON.stringify(body);
        var url = path.indexOf('http') === 0 ? path : comBarra(API + path);
        var r = await fetch(url, Object.assign({}, opts, {
            body: body,
            headers: Object.assign(headers, opts.headers || {})
        }));
        if (!r.ok) {
            var d;
            try { d = await r.json(); } catch (_) { d = { detail: r.statusText }; }
            throw new Error(d.detail || 'Erro na requisição.');
        }
        var ct = r.headers.get('content-type') || '';
        if (ct.indexOf('json') !== -1) return r.json();
        return r;
    }

    // ── Toast ──────────────────────────────────────────────────────
    function toast(message, type) {
        type = type || 'info';
        var container = $('#toast-container');
        var t = el('div', { className: 'toast toast-' + type }, [
            el('span', { textContent: message }),
            el('button', { className: 'toast-close', textContent: '×', onClick: function () { t.remove(); } })
        ]);
        container.appendChild(t);
        setTimeout(function () { t.remove(); }, 4500);
    }

    // ── Loading ────────────────────────────────────────────────────
    function loading(visible) {
        $('#loading-overlay').hidden = !visible;
    }

    // ── Table builder ──────────────────────────────────────────────
    function table(cols, rows) {
        var w = el('div', { className: 'table-wrapper' });
        var t = el('table', { className: 'data-table' });
        var thead = el('thead');
        var tr = el('tr');
        cols.forEach(function (c) {
            tr.appendChild(el('th', { textContent: c.label }));
        });
        thead.appendChild(tr);
        t.appendChild(thead);

        var tbody = el('tbody');
        if (!rows || !rows.length) {
            var r = el('tr');
            r.appendChild(el('td', {
                className: 'empty-row',
                colspan: String(cols.length),
                textContent: 'Nenhum registro encontrado.'
            }));
            tbody.appendChild(r);
        } else {
            rows.forEach(function (row, i) {
                var r = el('tr');
                cols.forEach(function (c) {
                    var td = el('td');
                    var v = c.render ? c.render(row[c.key], row, i) : row[c.key];
                    if (v instanceof Node) td.appendChild(v);
                    else if (c.html) td.innerHTML = v == null ? '' : v;
                    else td.textContent = v == null ? '' : v;
                    r.appendChild(td);
                });
                tbody.appendChild(r);
            });
        }
        t.appendChild(tbody);
        w.appendChild(t);
        return w;
    }

    // ── Badge ──────────────────────────────────────────────────────
    function badge(text) {
        var map = {
            'RECEBIDO':             'teal',
            'EM TRIAGEM':           'gold',
            'VENDA':                'orange',
            'S/ REPARO':            'danger',
            'ENVIADO LOJA':         'success',
            'INTERNALIZADO':        'info',
            'TRATATIVA DE SALDO':   'warning'
        };
        var cls = map[text] || 'default';
        return '<span class="badge badge-' + cls + '">' + esc(text || '—') + '</span>';
    }

    // ── Consulta module ────────────────────────────────────────────
    function renderConsulta(container) {
        var lastIds = [];

        container.innerHTML =
            '<h1 class="page-title">Consulta de Ativos</h1>' +
            '<p class="text-muted mb-3">Consulta direta na base do EBS, sem login.</p>' +
            '<div class="card mb-3">' +
                '<div class="card-header">Identificadores</div>' +
                '<div class="card-body">' +
                    '<p class="text-muted">Informe ativo, etiqueta ou número de série. Aceita linha, vírgula, ponto e vírgula ou tabulação.</p>' +
                    '<textarea id="q-input" class="form-control" rows="6" ' +
                        'placeholder="Um identificador por linha"></textarea>' +
                    '<div class="btn-row mt-2">' +
                        '<button id="q-run" class="btn btn-primary">Consultar</button>' +
                        '<button id="q-clear" class="btn btn-outline">Limpar</button>' +
                        '<button id="q-export" class="btn btn-secondary" disabled>Exportar Excel</button>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div id="q-results"></div>';

        var columns = [
            { key: 'empresa',         label: 'Empresa (BU)' },
            { key: 'imobilizado',     label: 'Imobilizado', render: function (v, r) { return r.ativo || v || ''; } },
            { key: 'etiqueta',        label: 'Etiqueta do Ativo' },
            { key: 'numero_serie',    label: 'Nº de Série' },
            { key: 'descricao',       label: 'Descrição do ativo' },
            { key: 'categoria',       label: 'Categoria', html: true, render: function (v, r) {
                if (!r.encontrado) return '';
                if (!v || v === 'NÃO CLASSIFICADA') return '<span class="text-muted">' + esc(v || 'NÃO CLASSIFICADA') + '</span>';
                return esc(v);
            }},
            { key: 'local_atribuido', label: 'Local atribuído' },
            { key: 'baixado',         label: 'Baixado?' },
            { key: 'po',              label: 'PO' },
            { key: 'nf',              label: 'NF' },
            { key: 'erro',            label: 'Erro' }
        ];

        function setProgresso(out, feito, total) {
            var pct = total ? Math.round((feito / total) * 100) : 0;
            out.innerHTML =
                '<div style="margin:8px 0 16px">' +
                '<div style="display:flex;justify-content:space-between;font-size:.85rem;' +
                'color:var(--text-secondary,#666);margin-bottom:4px">' +
                '<span>Consultando...</span><span>' + pct + '% (' + feito + '/' + total + ')</span></div>' +
                '<div style="height:10px;background:#e5e7eb;border-radius:6px;overflow:hidden">' +
                '<div style="height:100%;width:' + pct + '%;background:#3b82f6;transition:width .2s"></div>' +
                '</div></div>';
        }

        document.getElementById('q-run').onclick = async function () {
            lastIds = document.getElementById('q-input').value
                .split(/[\n,;\t]+/)
                .map(function (x) { return x.trim(); })
                .filter(Boolean);
            if (!lastIds.length) return toast('Informe identificadores.', 'warning');

            var runBtn = document.getElementById('q-run');
            var out = document.getElementById('q-results');
            runBtn.disabled = true;
            setProgresso(out, 0, lastIds.length);

            var LOTE = 50;
            var acumulado = [];
            var encontrados = 0, naoEncontrados = 0;
            try {
                for (var i = 0; i < lastIds.length; i += LOTE) {
                    var chunk = lastIds.slice(i, i + LOTE);
                    var d = await api('/consulta', {
                        method: 'POST',
                        body: { identificadores: chunk }
                    });
                    encontrados += d.encontrados || 0;
                    naoEncontrados += d.nao_encontrados || 0;
                    acumulado = acumulado.concat(d.resultados || []);
                    setProgresso(out, Math.min(i + LOTE, lastIds.length), lastIds.length);
                }
                out.innerHTML =
                    '<p class="text-muted">' + encontrados + ' encontrado(s) e ' +
                    naoEncontrados + ' não encontrado(s).</p>';
                out.appendChild(table(columns, acumulado));
                document.getElementById('q-export').disabled = false;
                toast('Consulta concluída.', 'success');
            } catch (e) {
                out.innerHTML = '<p style="color:#dc2626">' + (e.message || 'Erro na consulta.') + '</p>';
                toast(e.message, 'error');
            } finally {
                runBtn.disabled = false;
            }
        };

        document.getElementById('q-clear').onclick = function () {
            document.getElementById('q-input').value = '';
            document.getElementById('q-results').innerHTML = '';
            document.getElementById('q-export').disabled = true;
            lastIds = [];
        };

        document.getElementById('q-export').onclick = async function () {
            if (!lastIds.length) return;
            try {
                var r = await api('/consulta/export', {
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
                toast(e.message, 'error');
            }
        };
    }

    // ── Init ───────────────────────────────────────────────────────
    function init() {
        var sn = document.querySelector('.sidebar-item[data-route="servicenow"]');
        if (sn) {
            sn.classList.remove('disabled');
            sn.style.opacity = '';
            sn.style.pointerEvents = '';
            sn.href = BASE + '/#gestao_ativos/entrada';
            sn.title = 'Abre o portal: exige login e permissão';
        }
        renderConsulta($('#page-content'));
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
