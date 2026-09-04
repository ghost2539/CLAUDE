/* Indicadores SPARE — RMR (painel executivo, tema dark, abas, auto-refresh 2 min).
   JS externo (a CSP do portal é script-src 'self' — script inline é bloqueado). */
(function () {
    "use strict";
    var AUTO_MS = 120000;                 // 2 minutos
    var PALETTE = ["#3B82F6", "#0891B2", "#22C55E", "#A855F7", "#EC4899", "#F59E0B"];
    var INK = "#C4D0E2", MUTED = "#8A98AE", GRID = "#22314A";

    var $ = function (s) { return document.querySelector(s); };
    var $$ = function (s) { return [].slice.call(document.querySelectorAll(s)); };
    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }
    var MESES = ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
    function mesLbl(m) { var p = String(m || "").split("-"); return p[1] ? MESES[parseInt(p[1], 10)] : m; }
    function nfmt(n) { return (Number(n) || 0).toLocaleString("pt-BR"); }

    /* ── Gráficos (SVG inline, tema escuro) ─────────────────────────── */
    function colChart(el, data, opts) {
        opts = opts || {}; var color = opts.color || PALETTE[0];
        if (!data.length) { el.innerHTML = '<div class="empty">Sem dados no período.</div>'; return; }
        var W = Math.max(620, data.length * 82), H = 300, pad = 40;
        var bw = Math.min(54, (W - pad * 2) / data.length - 18);
        var max = Math.max.apply(null, data.map(function (d) { return d.v; })) || 1;
        var g = "";
        [0, .25, .5, .75, 1].forEach(function (f) {
            var y = (H - pad) - (H - pad - 30) * f;
            g += '<line x1="' + pad + '" y1="' + y + '" x2="' + (W - pad) + '" y2="' + y + '" stroke="' + GRID + '"></line>';
        });
        data.forEach(function (d, i) {
            var x = pad + i * ((W - pad * 2) / data.length) + ((W - pad * 2) / data.length - bw) / 2;
            var h = Math.round((H - pad - 30) * (d.v / max)), y = H - pad - h;
            g += '<rect x="' + x + '" y="' + y + '" width="' + bw + '" height="' + Math.max(h, 1) + '" rx="6" fill="' + color + '"></rect>';
            g += '<text x="' + (x + bw / 2) + '" y="' + (y - 8) + '" text-anchor="middle" font-size="13" font-weight="800" fill="' + INK + '">' + (opts.fmt ? opts.fmt(d.v) : nfmt(d.v)) + '</text>';
            g += '<text x="' + (x + bw / 2) + '" y="' + (H - pad + 18) + '" text-anchor="middle" font-size="12" fill="' + MUTED + '">' + esc(d.l) + '</text>';
        });
        el.innerHTML = '<div style="overflow-x:auto"><svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '">' + g + "</svg></div>";
    }

    function lineChart(el, data, opts) {
        opts = opts || {}; var color = opts.color || PALETTE[2];
        if (!data.length) { el.innerHTML = '<div class="empty">Sem dados no período.</div>'; return; }
        var W = Math.max(620, data.length * 82), H = 290, pad = 40, max = 100;
        var plotW = W - pad * 2, plotH = H - pad - 26;
        function X(i) { return pad + (data.length <= 1 ? plotW / 2 : i * (plotW / (data.length - 1))); }
        function Y(v) { return H - pad - (plotH * (v / max)); }
        var g = "";
        [0, 25, 50, 75, 100].forEach(function (gl) {
            g += '<line x1="' + pad + '" y1="' + Y(gl) + '" x2="' + (W - pad) + '" y2="' + Y(gl) + '" stroke="' + GRID + '"></line>' +
                '<text x="' + (pad - 8) + '" y="' + (Y(gl) + 4) + '" text-anchor="end" font-size="10" fill="' + MUTED + '">' + gl + "</text>";
        });
        var pts = data.map(function (d, i) { return X(i) + "," + Y(d.v); }).join(" ");
        var area = "M" + X(0) + "," + Y(0) + " L" + data.map(function (d, i) { return X(i) + "," + Y(d.v); }).join(" L") + " L" + X(data.length - 1) + "," + Y(0) + " Z";
        g += '<path d="' + area + '" fill="' + color + '" opacity="0.12"></path>';
        g += '<polyline points="' + pts + '" fill="none" stroke="' + color + '" stroke-width="3"></polyline>';
        data.forEach(function (d, i) {
            g += '<circle cx="' + X(i) + '" cy="' + Y(d.v) + '" r="4.5" fill="' + color + '"></circle>';
            g += '<text x="' + X(i) + '" y="' + (Y(d.v) - 11) + '" text-anchor="middle" font-size="12" font-weight="800" fill="' + INK + '">' + d.v + "%</text>";
            g += '<text x="' + X(i) + '" y="' + (H - pad + 18) + '" text-anchor="middle" font-size="12" fill="' + MUTED + '">' + esc(d.l) + "</text>";
        });
        el.innerHTML = '<div style="overflow-x:auto"><svg viewBox="0 0 ' + W + " " + H + '" width="' + W + '">' + g + "</svg></div>";
    }

    /* Ranking visual: número, barra em gradiente, valor e % do total */
    function ranking(el, data, color) {
        if (!data.length) { el.innerHTML = '<div class="empty">Sem dados.</div>'; return; }
        var max = Math.max.apply(null, data.map(function (d) { return d.v; })) || 1;
        var tot = data.reduce(function (a, d) { return a + d.v; }, 0) || 1;
        el.innerHTML = data.map(function (d, i) {
            var w = Math.max(2, Math.round(d.v / max * 100));
            var pct = Math.round(d.v / tot * 100);
            var cls = i === 0 ? "top1" : i === 1 ? "top2" : i === 2 ? "top3" : "";
            var grad = "linear-gradient(90deg," + color + "," + color + "cc)";
            return '<div class="rrow ' + cls + '">' +
                '<span class="rk">' + (i + 1) + "</span>" +
                '<span class="nm" title="' + esc(d.l) + '">' + esc(d.l) + "</span>" +
                '<span class="track"><span class="fill" style="width:' + w + "%;background:" + grad + '"></span></span>' +
                '<span class="qt">' + nfmt(d.v) + "</span>" +
                '<span class="pctlabel">' + pct + "%</span>" +
                "</div>";
        }).join("");
    }

    function ring(pct, color) {
        var r = 20, c = 2 * Math.PI * r, off = c * (1 - Math.min(100, Math.max(0, pct)) / 100);
        return '<svg class="ring" width="52" height="52" viewBox="0 0 52 52" aria-label="medidor de SLA">' +
            '<circle cx="26" cy="26" r="' + r + '" fill="none" stroke="#22314A" stroke-width="6"></circle>' +
            '<circle cx="26" cy="26" r="' + r + '" fill="none" stroke="' + color + '" stroke-width="6" stroke-linecap="round" ' +
            'stroke-dasharray="' + c.toFixed(1) + '" stroke-dashoffset="' + off.toFixed(1) + '" transform="rotate(-90 26 26)"></circle></svg>';
    }

    function kpi(k, lbl, val, hint, ringHtml) {
        return '<div class="kpi" style="--k:' + k + '">' + (ringHtml || "") +
            '<div class="lbl">' + esc(lbl) + '</div><div class="val">' + esc(val) + "</div>" +
            (hint ? '<div class="hint">' + esc(hint) + "</div>" : "") + "</div>";
    }

    /* ── Render ─────────────────────────────────────────────────────── */
    function render(snap) {
        if (!snap || !snap.dados) {
            $("#ind-status").innerHTML = 'Nenhum dado calculado ainda. Clique em <strong>Atualizar do ServiceNow</strong>.';
            $("#ind-kpis").innerHTML = "";
            return;
        }
        var d = snap.dados, ts = d.tickets_sla, top = d.top, tma = d.tma;
        var C = PALETTE;

        var k = "";
        if (ts) {
            k += kpi(C[0], "Tickets resolvidos (ano)", nfmt(ts.tickets_total), "acumulado " + (ts.ano || ""));
            var meses = (ts.tickets_por_mes || []).filter(function (m) { return m.total > 0; });
            var ult = meses.length ? meses[meses.length - 1] : null;
            k += kpi(C[1], "Resolvidos no mês", ult ? nfmt(ult.total) : "0", ult ? mesLbl(ult.mes) : "—");
            k += kpi(C[2], "SLA", (ts.sla_compliance_pct || 0) + "%",
                "dentro do prazo · " + nfmt(ts.sla_dentro) + " de " + nfmt(ts.sla_total), ring(ts.sla_compliance_pct || 0, C[2]));
            k += kpi(C[4], "SLA violado", nfmt(ts.sla_violado), "no ano");
        }
        if (tma && tma.tma) {
            var byCat = {}; tma.tma.forEach(function (t) { byCat[t.categoria] = t; });
            var col = byCat["Coletor"] || {}, sled = byCat["SLED"] || {};
            k += kpi(C[5], "TMA Coletor", (col.dias || 0) + " d", "amostra " + nfmt(col.amostra || 0));
            k += kpi(C[3], "TMA SLED", (sled.dias || 0) + " d", "amostra " + nfmt(sled.amostra || 0));
        }
        $("#ind-kpis").innerHTML = k;

        if (ts) {
            colChart($("#ch-tickets"), (ts.tickets_por_mes || []).filter(function (m) { return m.total > 0; })
                .map(function (m) { return { l: mesLbl(m.mes), v: m.total }; }), { color: C[0] });
            lineChart($("#ch-sla"), (ts.sla_por_mes || []).filter(function (m) { return m.total > 0; })
                .map(function (m) { return { l: mesLbl(m.mes), v: m.pct }; }), { color: C[2] });
        } else {
            $("#ch-tickets").innerHTML = '<div class="empty">Sem dados de tickets/SLA.</div>';
            $("#ch-sla").innerHTML = "";
        }
        if (top) {
            ranking($("#ch-lojas"), (top.top_lojas || []).map(function (x) { return { l: x.loja, v: x.total }; }), C[1]);
            ranking($("#ch-subs"), (top.top_subcategorias || []).map(function (x) { return { l: x.subcategoria, v: x.total }; }), C[3]);
        } else {
            $("#ch-lojas").innerHTML = '<div class="empty">Sem dados.</div>';
            $("#ch-subs").innerHTML = '<div class="empty">Sem dados.</div>';
        }
        if (tma && tma.tma) {
            colChart($("#ch-tma"), tma.tma.map(function (t) { return { l: t.categoria, v: t.dias }; }),
                { color: C[5], fmt: function (v) { return v + " d"; } });
        } else {
            $("#ch-tma").innerHTML = '<div class="empty">Sem dados de TMA.</div>';
        }

        $("#foot-ref").textContent = snap.referencia || "—";
        $("#foot-gerado").textContent = (d.gerado_em || snap.criado_em || "").replace("T", " ").slice(0, 16) || "—";
        var qi = [];
        if (d.erros) { for (var kk in d.erros) { if (d.erros[kk]) qi.push(kk + ": " + d.erros[kk]); } }
        $("#ind-status").innerHTML =
            '<span class="chip">Referência&nbsp;<strong>' + esc(snap.referencia) + "</strong></span>" +
            '<span class="chip">Gerado ' + esc((d.gerado_em || "").replace("T", " ").slice(0, 16)) + "</span>" +
            (qi.length ? '<span class="err">Falhas: ' + esc(qi.join(" | ")) + "</span>" : "");
    }

    function fillRefs(refs, atual) {
        var sel = $("#ind-ref");
        if (!refs || !refs.length) { sel.innerHTML = "<option>—</option>"; return; }
        sel.innerHTML = refs.map(function (r) {
            return '<option value="' + r + '"' + (r === atual ? " selected" : "") + ">" + r + "</option>";
        }).join("");
    }

    /* ── Abas (menu lateral mostra só a seção escolhida) ────────────── */
    function selTab(tab) {
        $$("#nav button").forEach(function (b) { b.classList.toggle("active", b.getAttribute("data-tab") === tab); });
        $$(".tabpane").forEach(function (p) { p.classList.toggle("active", p.getAttribute("data-pane") === tab); });
        try { localStorage.setItem("ind-tab", tab); } catch (e) {}
    }
    $$("#nav button").forEach(function (b) {
        b.addEventListener("click", function () { selTab(b.getAttribute("data-tab")); });
    });

    /* ── Dados / atualização ────────────────────────────────────────── */
    var busy = false;
    function load(ref) {
        return fetch("/api/indicadores/dados" + (ref ? "?referencia=" + encodeURIComponent(ref) : ""))
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.config && d.config.queue) $("#foot-queue").textContent = d.config.queue;
                fillRefs(d.referencias, d.snapshot && d.snapshot.referencia);
                render(d.snapshot);
            })
            .catch(function (e) { $("#ind-status").innerHTML = '<span class="err">Erro ao carregar: ' + esc(e.message) + "</span>"; });
    }
    function atualizar(silent) {
        if (busy) return Promise.resolve();
        busy = true;
        var btn = $("#ind-refresh"), txt = btn.textContent;
        btn.disabled = true; btn.textContent = "Atualizando…";
        if (!silent) $("#ind-status").innerHTML = '<span class="chip">Consultando o ServiceNow…</span>';
        return fetch("/api/indicadores/atualizar", { method: "POST" })
            .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
            .then(function (res) {
                if (!res.ok) throw new Error((res.j && res.j.detail) || "Falha na atualização.");
                return load(res.j.referencia);
            })
            .catch(function (e) { $("#ind-status").innerHTML = '<span class="err">' + esc(e.message) + "</span>"; })
            .then(function () { busy = false; btn.disabled = false; btn.textContent = txt; });
    }

    /* ── Auto-refresh (2 min) ───────────────────────────────────────── */
    var autoOn = true, restante = AUTO_MS / 1000;
    function paintAuto() {
        var pill = $("#live-pill"), t = $("#live-txt"), b = $("#ind-auto");
        if (autoOn) { pill.classList.remove("paused"); t.textContent = "Ao vivo · " + restante + "s"; b.textContent = "⏸ Pausar"; }
        else { pill.classList.add("paused"); t.textContent = "Pausado"; b.textContent = "▶ Retomar"; }
    }
    function tick() {
        if (!autoOn) return;
        restante -= 1;
        if (restante <= 0) { restante = AUTO_MS / 1000; atualizar(true); }
        paintAuto();
    }
    $("#ind-auto").addEventListener("click", function () { autoOn = !autoOn; restante = AUTO_MS / 1000; paintAuto(); });
    $("#ind-refresh").addEventListener("click", function () { restante = AUTO_MS / 1000; atualizar(false); });
    $("#ind-ref").addEventListener("change", function () { load(this.value); });

    /* start */
    try { var t0 = localStorage.getItem("ind-tab"); if (t0) selTab(t0); } catch (e) {}
    load("").then(function () { paintAuto(); setInterval(tick, 1000); });
})();
