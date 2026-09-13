import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ComposedChart, Area, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer,
} from "recharts";

/* ════════════════════════════════════════════════════════════════
   Planejamento de compras — só na instância Spare.
   Histórico (real + imputado), previsão P50/P90 e necessidade de compra
   por item configurado. Consome /api/planejamento.
   ════════════════════════════════════════════════════════════════ */

const API = "/api/planejamento";
const MESES3 = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];

async function api(path, options = {}) {
  const isForm = options.body instanceof FormData;
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: isForm ? (options.headers || {}) : { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let detail = `Erro ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d) => d.msg).join("; ");
    } catch (_) { /* corpo não-JSON */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

const fmtMes = (ym) => (ym ? MESES3[Number(ym.slice(5, 7)) - 1] + "/" + ym.slice(2, 4) : "");
const fmtData = (iso) => (iso ? iso.slice(8, 10) + "/" + iso.slice(5, 7) + "/" + iso.slice(0, 4) : "");
const fmtBRL = (v) => "R$ " + Math.round(Number(v) || 0).toLocaleString("pt-BR");
const fmtNum = (v, d = 0) => (Number(v) || 0).toLocaleString("pt-BR", { maximumFractionDigits: d });

const btn = (ativo) => "text-xs font-medium rounded-md px-3 py-1.5 border " +
  (ativo ? "bg-blue-600 text-white border-blue-600" : "bg-white text-gray-700 border-gray-300 hover:bg-gray-50");
const btnPrim = "text-xs font-medium rounded-md px-3 py-1.5 bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50";
const btnSec = "text-xs font-medium rounded-md px-3 py-1.5 border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50";

function Card({ title, right, children, className = "" }) {
  return (
    <section className={"bg-white rounded-lg border border-gray-200 shadow-sm " + className}>
      {(title || right) && (
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-gray-100">
          <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
          <div className="ml-auto flex items-center gap-2">{right}</div>
        </div>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

function Kpi({ label, value, sub, color = "#2563eb" }) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4" style={{ borderTopWidth: 3, borderTopColor: color }}>
      <div className="text-[11px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className="text-xl font-bold text-gray-900 tabular-nums mt-0.5">{value}</div>
      {sub && <div className="text-[11px] text-gray-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function Msg({ texto, tipo }) {
  if (!texto) return null;
  const cls = tipo === "erro" ? "bg-red-50 border-red-200 text-red-800" : "bg-green-50 border-green-200 text-green-800";
  return <div className={"mb-3 border rounded-lg px-4 py-2.5 text-xs " + cls}>{texto}</div>;
}

/* ── Itens ─────────────────────────────────────────────────────── */
function Itens({ podeEditar, aoMudar }) {
  const [itens, setItens] = useState([]);
  const [modelos, setModelos] = useState([]);
  const [msg, setMsg] = useState({});
  const [novo, setNovo] = useState({ nome: "", modelos: [] });
  const [busy, setBusy] = useState(0);
  const timers = useRef({});

  const carregar = useCallback(async () => {
    try {
      const [a, b] = await Promise.all([api(API + "/itens"), api(API + "/modelos")]);
      setItens(a.itens); setModelos(b.modelos);
    } catch (e) { setMsg({ texto: e.message, tipo: "erro" }); }
  }, []);
  useEffect(() => { carregar(); }, [carregar]);

  const patch = (id, campos) => {
    setItens((prev) => prev.map((i) => (i.id === id ? { ...i, ...campos } : i)));
    clearTimeout(timers.current[id]);
    timers.current[id] = setTimeout(async () => {
      try { const r = await api(API + "/itens/" + id, { method: "PATCH", body: JSON.stringify(campos) });
        setItens((prev) => prev.map((i) => (i.id === id ? r : i))); aoMudar && aoMudar(); }
      catch (e) { setMsg({ texto: "Falha ao salvar: " + e.message, tipo: "erro" }); }
    }, 600);
  };
  const criar = async () => {
    if (!novo.nome.trim()) { setMsg({ texto: "Informe o nome do item.", tipo: "erro" }); return; }
    setBusy((b) => b + 1);
    try { await api(API + "/itens", { method: "POST", body: JSON.stringify(novo) }); setNovo({ nome: "", modelos: [] }); setMsg({}); await carregar(); aoMudar && aoMudar(); }
    catch (e) { setMsg({ texto: e.message, tipo: "erro" }); }
    finally { setBusy((b) => b - 1); }
  };
  const excluir = async (i) => {
    if (!window.confirm(`Excluir o item "${i.nome}" e o histórico imputado dele?`)) return;
    try { await api(API + "/itens/" + i.id, { method: "DELETE" }); await carregar(); aoMudar && aoMudar(); }
    catch (e) { setMsg({ texto: e.message, tipo: "erro" }); }
  };
  const contarSN = async (i) => {
    setBusy((b) => b + 1);
    try { const r = await api(API + "/itens/" + i.id + "/estoque-servicenow", { method: "POST" });
      setItens((prev) => prev.map((x) => (x.id === i.id ? r : x))); setMsg({ texto: `${i.nome}: ${r.estoque_atual} em estoque no ServiceNow.`, tipo: "ok" }); aoMudar && aoMudar(); }
    catch (e) { setMsg({ texto: e.message, tipo: "erro" }); }
    finally { setBusy((b) => b - 1); }
  };
  const toggleModelo = (lista, m) => (lista.includes(m) ? lista.filter((x) => x !== m) : [...lista, m]);

  const num = (i, campo, extra = {}) => (
    <input type="number" min="0" className="cell-input num w-[72px]" disabled={!podeEditar}
           value={i[campo] ?? 0} onChange={(e) => patch(i.id, { [campo]: Number(e.target.value || 0) })} {...extra} />
  );

  return (
    <>
      <Msg {...msg} />
      {podeEditar && (
        <Card title="Novo item" className="mb-4">
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs text-gray-600">
              <span className="block text-[10px] font-semibold uppercase tracking-wide text-gray-500 mb-0.5">Nome do item</span>
              <input value={novo.nome} onChange={(e) => setNovo({ ...novo, nome: e.target.value })} placeholder="ex.: PDV Dell 3050"
                     className="w-[260px] border border-gray-300 rounded-md px-2.5 py-1.5 text-xs focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100" />
            </label>
            <div className="text-xs text-gray-600 min-w-0">
              <span className="block text-[10px] font-semibold uppercase tracking-wide text-gray-500 mb-0.5">Modelos da Separação</span>
              <ModelosPicker modelos={modelos} selecionados={novo.modelos} onToggle={(m) => setNovo({ ...novo, modelos: toggleModelo(novo.modelos, m) })} />
            </div>
            <button onClick={criar} disabled={busy > 0} className={btnPrim}>Incluir</button>
          </div>
        </Card>
      )}

      <Card title={`Itens (${itens.length})`}>
        <div className="overflow-x-auto">
          <table className="w-full text-xs text-left">
            <thead className="text-[11px] text-gray-500 border-b border-gray-200">
              <tr>
                <th className="th">Item</th><th className="th">Modelos</th>
                <th className="th text-right">Estoque</th><th className="th">Atualizado</th>
                <th className="th text-right">Pedidos abertos</th><th className="th text-right">Lead time (dias)</th>
                <th className="th text-right">Segurança (dias)</th><th className="th text-right">Custo unit. (R$)</th>
                <th className="th">Ativo</th><th className="th"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {itens.length === 0 && (
                <tr><td colSpan={10} className="px-4 py-8 text-center text-gray-500">Nenhum item. {podeEditar ? "Inclua o primeiro acima." : ""}</td></tr>
              )}
              {itens.map((i) => (
                <tr key={i.id} className={i.ativo ? "" : "opacity-50"}>
                  <td className="td">
                    <input className="cell-input min-w-[160px] font-medium" disabled={!podeEditar} value={i.nome}
                           onChange={(e) => patch(i.id, { nome: e.target.value })} />
                  </td>
                  <td className="td max-w-[320px] whitespace-normal">
                    <ModelosPicker modelos={modelos} selecionados={i.modelos} compacto disabled={!podeEditar}
                                   onToggle={(m) => patch(i.id, { modelos: toggleModelo(i.modelos, m) })} />
                  </td>
                  <td className="td text-right">
                    <div className="flex items-center justify-end gap-1">
                      {num(i, "estoque_atual")}
                      {podeEditar && <button onClick={() => contarSN(i)} disabled={busy > 0} title="Contar no ServiceNow" className={btnSec + " px-2"}>SN</button>}
                    </div>
                  </td>
                  <td className="td text-gray-500">{i.estoque_atualizado_em ? fmtData(i.estoque_atualizado_em) + (i.estoque_origem === "servicenow" ? " · SN" : "") : "—"}</td>
                  <td className="td text-right">{num(i, "pedidos_abertos")}</td>
                  <td className="td text-right">{num(i, "lead_time_dias")}</td>
                  <td className="td text-right">{num(i, "seguranca_dias")}</td>
                  <td className="td text-right">
                    <input type="number" min="0" step="0.01" className="cell-input num w-[96px]" disabled={!podeEditar}
                           value={i.custo_unitario ?? 0} onChange={(e) => patch(i.id, { custo_unitario: Number(e.target.value || 0) })} />
                  </td>
                  <td className="td"><input type="checkbox" disabled={!podeEditar} checked={!!i.ativo} onChange={(e) => patch(i.id, { ativo: e.target.checked })} /></td>
                  <td className="td">{podeEditar && <button onClick={() => excluir(i)} className="text-[11px] text-red-600 hover:underline">excluir</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

function ModelosPicker({ modelos, selecionados, onToggle, compacto, disabled }) {
  const [aberto, setAberto] = useState(false);
  const [filtro, setFiltro] = useState("");
  const lista = useMemo(() => {
    const todos = Array.from(new Set([...(modelos || []), ...(selecionados || [])]));
    const f = filtro.trim().toLowerCase();
    return (f ? todos.filter((m) => m.toLowerCase().includes(f)) : todos).slice(0, 80);
  }, [modelos, selecionados, filtro]);
  return (
    <div className="relative">
      <button type="button" disabled={disabled} onClick={() => setAberto((a) => !a)}
              className={"text-left border border-gray-300 rounded-md px-2 py-1 text-xs bg-white hover:bg-gray-50 disabled:bg-transparent disabled:border-transparent " + (compacto ? "max-w-[300px] truncate" : "min-w-[260px]")}>
        {selecionados && selecionados.length ? selecionados.join(", ") : <span className="text-gray-400">escolher modelos…</span>}
      </button>
      {aberto && !disabled && (
        <div className="absolute z-20 mt-1 w-[320px] max-h-[260px] overflow-auto bg-white border border-gray-200 rounded-md shadow-lg p-2">
          <input autoFocus value={filtro} onChange={(e) => setFiltro(e.target.value)} placeholder="filtrar…"
                 className="w-full border border-gray-300 rounded px-2 py-1 text-xs mb-2" />
          {lista.length === 0 && <div className="text-[11px] text-gray-500 px-1 py-2">Nenhum modelo visto na Separação ainda.</div>}
          {lista.map((m) => (
            <label key={m} className="flex items-center gap-2 px-1 py-1 text-xs hover:bg-gray-50 rounded cursor-pointer">
              <input type="checkbox" checked={selecionados.includes(m)} onChange={() => onToggle(m)} /><span className="truncate">{m}</span>
            </label>
          ))}
          <div className="text-right mt-1"><button type="button" onClick={() => setAberto(false)} className="text-[11px] text-blue-600 hover:underline">fechar</button></div>
        </div>
      )}
    </div>
  );
}

/* ── Histórico ─────────────────────────────────────────────────── */
function Historico({ podeEditar, versao }) {
  const [dados, setDados] = useState(null);
  const [meses, setMeses] = useState(24);
  const [msg, setMsg] = useState({});
  const [busy, setBusy] = useState(false);
  const timers = useRef({});
  const arquivo = useRef(null);

  const carregar = useCallback(async (n) => {
    try { setDados(await api(API + "/historico?meses=" + (n || meses))); }
    catch (e) { setMsg({ texto: e.message, tipo: "erro" }); }
  }, [meses]);
  useEffect(() => { carregar(meses); }, [carregar, meses, versao]);

  const gravar = (itemId, mes, quantidade) => {
    setDados((prev) => ({ ...prev, linhas: prev.linhas.map((l) => (l.item.id !== itemId ? l :
      { ...l, serie: l.serie.map((p) => (p.mes === mes ? { ...p, quantidade } : p)) })) }));
    const k = itemId + ":" + mes;
    clearTimeout(timers.current[k]);
    timers.current[k] = setTimeout(async () => {
      try { const r = await api(API + "/historico", { method: "PUT", body: JSON.stringify([{ item_id: itemId, mes, quantidade }]) });
        if (r.recusados.length) setMsg({ texto: r.recusados[0].motivo, tipo: "erro" }); }
      catch (e) { setMsg({ texto: "Falha ao salvar: " + e.message, tipo: "erro" }); }
    }, 600);
  };
  const importar = async () => {
    const f = arquivo.current?.files?.[0];
    if (!f) { setMsg({ texto: "Escolha um arquivo CSV ou XLSX.", tipo: "erro" }); return; }
    setBusy(true);
    try {
      const fd = new FormData(); fd.append("arquivo", f);
      const r = await api(API + "/historico/importar", { method: "POST", body: fd });
      let t = `${r.gravadas} de ${r.lidas} linha(s) gravada(s).`;
      if (r.ignoradas_sistema) t += ` ${r.ignoradas_sistema} ignorada(s) por já estarem no período do sistema.`;
      if (r.itens_desconhecidos.length) t += ` Itens não cadastrados: ${r.itens_desconhecidos.join(", ")}.`;
      setMsg({ texto: t, tipo: r.gravadas ? "ok" : "erro" });
      arquivo.current.value = "";
      carregar(meses);
    } catch (e) { setMsg({ texto: e.message, tipo: "erro" }); }
    finally { setBusy(false); }
  };

  if (!dados) return <div className="text-xs text-gray-500">Carregando…</div>;
  const inicio = dados.inicio_sistema;
  const totalMes = (m) => dados.linhas.reduce((a, l) => a + (l.serie.find((p) => p.mes === m)?.quantidade || 0), 0);

  return (
    <>
      <Msg {...msg} />
      <div className="flex flex-wrap items-center gap-2 mb-3">
        {[12, 24, 36].map((n) => <button key={n} onClick={() => setMeses(n)} className={btn(meses === n)}>{n} meses</button>)}
        <span className="text-[11px] text-gray-500 ml-2">
          {inicio ? <>Real a partir de <b>{fmtMes(inicio)}</b>; antes disso, imputado.</> : "Sem data de início: todo o histórico é imputado."}
        </span>
        {podeEditar && (
          <div className="ml-auto flex items-center gap-2">
            <input ref={arquivo} type="file" accept=".csv,.xlsx,.xls" className="text-[11px]" />
            <button onClick={importar} disabled={busy} className={btnSec}>Importar planilha</button>
          </div>
        )}
      </div>
      <Card>
        <div className="overflow-x-auto">
          <table className="text-xs text-left">
            <thead className="text-[11px] text-gray-500 border-b border-gray-200">
              <tr>
                <th className="th sticky left-0 bg-white">Item</th>
                {dados.meses.map((m) => (
                  <th key={m} className={"th text-right " + (inicio && m >= inicio ? "text-blue-700" : "")}>{fmtMes(m)}</th>
                ))}
                <th className="th text-right">Total</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {dados.linhas.length === 0 && <tr><td colSpan={dados.meses.length + 2} className="px-4 py-8 text-center text-gray-500">Cadastre itens na aba Itens.</td></tr>}
              {dados.linhas.map((l) => (
                <tr key={l.item.id}>
                  <td className="td sticky left-0 bg-white font-medium">{l.item.nome}</td>
                  {l.serie.map((p) => (
                    <td key={p.mes} className={"td text-right tabular-nums " + (p.origem === "real" ? "bg-blue-50/40" : "")}>
                      {p.origem === "real" || !podeEditar
                        ? <span className={p.quantidade ? "" : "text-gray-300"}>{p.quantidade}</span>
                        : <input type="number" min="0" className="cell-input num w-[56px]" value={p.quantidade}
                                 onChange={(e) => gravar(l.item.id, p.mes, Number(e.target.value || 0))} />}
                    </td>
                  ))}
                  <td className="td text-right font-semibold tabular-nums">{fmtNum(l.serie.reduce((a, p) => a + p.quantidade, 0))}</td>
                </tr>
              ))}
            </tbody>
            {dados.linhas.length > 0 && (
              <tfoot className="border-t border-gray-200 text-[11px] font-semibold">
                <tr><td className="td sticky left-0 bg-white">Total</td>
                  {dados.meses.map((m) => <td key={m} className="td text-right tabular-nums">{fmtNum(totalMes(m))}</td>)}
                  <td className="td text-right tabular-nums">{fmtNum(dados.meses.reduce((a, m) => a + totalMes(m), 0))}</td></tr>
              </tfoot>
            )}
          </table>
        </div>
        <div className="text-[11px] text-gray-500 mt-2">Fundo azul: consumo real da Separação. Planilha: colunas item, mês (AAAA-MM ou MM/AAAA) e quantidade.</div>
      </Card>
    </>
  );
}

/* ── Previsão ──────────────────────────────────────────────────── */
function TooltipPrev({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-white border border-gray-200 rounded-md shadow px-3 py-2 text-[11px]">
      <div className="font-semibold text-gray-800 mb-1">{fmtMes(label)}</div>
      {d.real != null && <div>Real: <b>{fmtNum(d.real)}</b></div>}
      {d.imputado != null && <div>Imputado: <b>{fmtNum(d.imputado)}</b></div>}
      {d.p50 != null && <div>Previsto (P50): <b>{fmtNum(d.p50, 1)}</b></div>}
      {d.p90 != null && <div>P90: <b>{fmtNum(d.p90, 1)}</b></div>}
      {d.saldo != null && <div>Estoque projetado: <b>{fmtNum(d.saldo, 1)}</b></div>}
    </div>
  );
}

function GraficoItem({ x }) {
  const dados = useMemo(() => {
    const hist = x.historico.map((p) => ({ mes: p.mes, real: p.origem === "real" ? p.quantidade : null, imputado: p.origem === "imputado" ? p.quantidade : null }));
    // Abaixo de zero não é estoque, é ruptura: a linha para no chão.
    const saldoPorMes = Object.fromEntries((x.necessidade.projecao || []).map((p) => [p.mes, Math.max(0, p.saldo)]));
    const prev = x.previsao.meses.map((m, i) => ({ mes: m, p50: x.previsao.p50[i], p90: x.previsao.p90[i], faixa: [x.previsao.p50[i], x.previsao.p90[i]], saldo: saldoPorMes[m] }));
    return [...hist, ...prev];
  }, [x]);
  const inicioPrev = x.previsao.meses[0];
  return (
    <ResponsiveContainer width="100%" height={200}>
      <ComposedChart data={dados} margin={{ top: 6, right: 8, bottom: 0, left: -10 }}>
        <CartesianGrid stroke="#e5e7eb" vertical={false} />
        <XAxis dataKey="mes" tickFormatter={fmtMes} tick={{ fontSize: 9, fill: "#6b7280" }} axisLine={false} tickLine={false} interval={Math.max(0, Math.floor(dados.length / 12) - 1)} />
        <YAxis tick={{ fontSize: 10, fill: "#6b7280" }} axisLine={false} tickLine={false} width={40} allowDecimals={false} />
        <Tooltip content={<TooltipPrev />} />
        <Bar dataKey="imputado" name="Imputado" fill="#cbd5e1" radius={[3, 3, 0, 0]} isAnimationActive={false} />
        <Bar dataKey="real" name="Real" fill="#2563eb" radius={[3, 3, 0, 0]} isAnimationActive={false} />
        <Area dataKey="faixa" name="P50–P90" fill="#f59e0b" fillOpacity={0.18} stroke="none" isAnimationActive={false} />
        <Line dataKey="p50" name="Previsto" stroke="#f59e0b" strokeWidth={2} dot={{ r: 2 }} isAnimationActive={false} connectNulls={false} />
        <Line dataKey="saldo" name="Estoque projetado" stroke="#16a34a" strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
        {inicioPrev && <ReferenceLine x={inicioPrev} stroke="#9ca3af" strokeDasharray="3 3" />}
        {x.necessidade.seguranca_unidades > 0 && <ReferenceLine y={x.necessidade.seguranca_unidades} stroke="#16a34a" strokeDasharray="2 4" />}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function Previsao({ versao }) {
  const [dados, setDados] = useState(null);
  const [horizonte, setHorizonte] = useState(null);
  const [erro, setErro] = useState("");
  const [aberto, setAberto] = useState(null);

  const carregar = useCallback(async (h) => {
    setErro("");
    try { const d = await api(API + "/previsao" + (h ? "?horizonte=" + h : "")); setDados(d); setHorizonte(d.horizonte); }
    catch (e) { setErro(e.message); }
  }, []);
  useEffect(() => { carregar(horizonte); }, [carregar, horizonte, versao]);

  if (erro) return <Msg texto={erro} tipo="erro" />;
  if (!dados) return <div className="text-xs text-gray-500">Calculando…</div>;
  const r = dados.resumo;
  const itens = [...dados.itens].sort((a, b) => (a.necessidade.data_limite_pedido || "9999").localeCompare(b.necessidade.data_limite_pedido || "9999"));
  const hoje = new Date().toISOString().slice(0, 10);
  const corLimite = (d) => (!d ? "text-gray-500" : d < hoje ? "text-red-700 font-semibold" : "text-gray-800");

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        {[6, 12, 18, 24].map((n) => <button key={n} onClick={() => setHorizonte(n)} className={btn(horizonte === n)}>{n} meses</button>)}
        <span className="text-[11px] text-gray-500 ml-2">Último mês fechado: <b>{fmtMes(dados.ultimo_mes_fechado)}</b></span>
      </div>
      <section className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Kpi label="Itens com compra" value={`${r.itens_com_compra} / ${r.itens}`} color="#2563eb" />
        <Kpi label="Unidades a comprar" value={fmtNum(r.unidades)} sub={`no horizonte de ${dados.horizonte} meses`} color="#f59e0b" />
        <Kpi label="Valor estimado" value={fmtBRL(r.valor)} sub={`P90: ${fmtBRL(r.valor_p90)}`} color="#16a34a" />
        <Kpi label="Próximo pedido até" value={r.proximo_pedido ? fmtData(r.proximo_pedido) : "—"} color={r.proximo_pedido && r.proximo_pedido < hoje ? "#dc2626" : "#8b5cf6"} />
      </section>

      <Card title="Necessidade de compra por item">
        <div className="overflow-x-auto">
          <table className="w-full text-xs text-left">
            <thead className="text-[11px] text-gray-500 border-b border-gray-200">
              <tr>
                <th className="th">Item</th><th className="th text-right">Estoque</th><th className="th text-right">Pedidos</th>
                <th className="th text-right">Consumo previsto</th><th className="th text-right">Segurança</th>
                <th className="th text-right">Cobertura</th><th className="th">Ruptura</th><th className="th">Pedir até</th>
                <th className="th text-right">Comprar</th><th className="th text-right">P90</th><th className="th text-right">Valor</th><th className="th">Método</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {itens.length === 0 && <tr><td colSpan={12} className="px-4 py-8 text-center text-gray-500">Cadastre itens na aba Itens.</td></tr>}
              {itens.map((x) => {
                const n = x.necessidade, i = x.item;
                return (
                  <FragmentLinha key={i.id} aberto={aberto === i.id} onToggle={() => setAberto(aberto === i.id ? null : i.id)} x={x}>
                    <td className="td font-medium">{i.nome}</td>
                    <td className="td text-right tabular-nums">{fmtNum(i.estoque_atual)}</td>
                    <td className="td text-right tabular-nums">{fmtNum(i.pedidos_abertos)}</td>
                    <td className="td text-right tabular-nums">{fmtNum(n.consumo_previsto)}</td>
                    <td className="td text-right tabular-nums">{fmtNum(n.seguranca_unidades, 1)}</td>
                    <td className="td text-right tabular-nums">{n.cobertura_meses == null ? "—" : fmtNum(n.cobertura_meses, 1) + " m"}</td>
                    <td className="td">{n.mes_ruptura ? fmtMes(n.mes_ruptura) : <span className="text-green-700">não rompe</span>}</td>
                    <td className={"td " + corLimite(n.data_limite_pedido)}>{n.data_limite_pedido ? fmtData(n.data_limite_pedido) : "—"}</td>
                    <td className="td text-right tabular-nums font-semibold">{n.necessidade ? fmtNum(n.necessidade) : <span className="text-gray-400">0</span>}</td>
                    <td className="td text-right tabular-nums text-gray-500">{fmtNum(n.necessidade_p90)}</td>
                    <td className="td text-right tabular-nums">{n.valor ? fmtBRL(n.valor) : <span className="text-gray-400">—</span>}</td>
                    <td className="td text-gray-500 whitespace-normal max-w-[220px]">{x.previsao.metodo}</td>
                  </FragmentLinha>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="text-[11px] text-gray-500 mt-2">Clique no item para ver o gráfico. Comprar = consumo previsto + segurança − estoque − pedidos em aberto. Pedir até = mês de ruptura menos o lead time.</div>
      </Card>
    </>
  );
}

function FragmentLinha({ aberto, onToggle, x, children }) {
  return (
    <>
      <tr onClick={onToggle} className={"cursor-pointer hover:bg-gray-50 " + (aberto ? "bg-gray-50" : "")}>{children}</tr>
      {aberto && (
        <tr><td colSpan={12} className="px-2 pb-3">
          <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[10px] text-gray-600 mb-1 px-2">
            <span className="inline-flex items-center gap-1"><span className="inline-block w-3 h-2 bg-blue-600 rounded-sm" />Real</span>
            <span className="inline-flex items-center gap-1"><span className="inline-block w-3 h-2 bg-slate-300 rounded-sm" />Imputado</span>
            <span className="inline-flex items-center gap-1"><span className="inline-block w-3.5 border-t-2 border-amber-500" />Previsto (P50) e faixa até P90</span>
            <span className="inline-flex items-center gap-1"><span className="inline-block w-3.5 border-t-2 border-dashed border-green-600" />Estoque projetado e segurança</span>
            <span className="ml-auto">{x.previsao.metodo} · desvio {fmtNum(x.previsao.desvio, 1)}</span>
          </div>
          <GraficoItem x={x} />
        </td></tr>
      )}
    </>
  );
}

/* ── Configuração ──────────────────────────────────────────────── */
function Configuracao({ podeEditar, aoMudar }) {
  const [cfg, setCfg] = useState(null);
  const [msg, setMsg] = useState({});
  useEffect(() => { api(API + "/config").then(setCfg).catch((e) => setMsg({ texto: e.message, tipo: "erro" })); }, []);
  if (!cfg) return <div className="text-xs text-gray-500">Carregando…</div>;
  const salvar = async () => {
    try {
      const r = await api(API + "/config", { method: "PUT", body: JSON.stringify({
        data_inicio_sistema: cfg.data_inicio_configurada ? cfg.data_inicio_sistema : "",
        horizonte_meses: Number(cfg.horizonte_meses), meses_historico: Number(cfg.meses_historico) }) });
      setCfg(r); setMsg({ texto: "Configuração salva.", tipo: "ok" }); aoMudar && aoMudar();
    } catch (e) { setMsg({ texto: e.message, tipo: "erro" }); }
  };
  const campo = "border border-gray-300 rounded-md px-2.5 py-1.5 text-xs focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 disabled:bg-gray-50";
  return (
    <Card title="Configuração do planejamento">
      <Msg {...msg} />
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs text-gray-700">
        <label className="block">
          <span className="block text-[10px] font-semibold uppercase tracking-wide text-gray-500 mb-0.5">Início do sistema (mês)</span>
          <div className="flex items-center gap-2">
            <input type="month" disabled={!podeEditar || !cfg.data_inicio_configurada} value={cfg.data_inicio_sistema || ""} className={campo}
                   onChange={(e) => setCfg({ ...cfg, data_inicio_sistema: e.target.value })} />
            <label className="inline-flex items-center gap-1 text-[11px]">
              <input type="checkbox" disabled={!podeEditar} checked={!!cfg.data_inicio_configurada}
                     onChange={(e) => setCfg({ ...cfg, data_inicio_configurada: e.target.checked })} />
              definir manualmente
            </label>
          </div>
          <span className="block text-[11px] text-gray-500 mt-1">A partir deste mês o consumo vem da Separação. Sem definir, vale o primeiro mês com envio registrado.</span>
        </label>
        <label className="block">
          <span className="block text-[10px] font-semibold uppercase tracking-wide text-gray-500 mb-0.5">Horizonte padrão (meses)</span>
          <input type="number" min="1" max="36" disabled={!podeEditar} value={cfg.horizonte_meses} className={campo + " w-[100px]"}
                 onChange={(e) => setCfg({ ...cfg, horizonte_meses: e.target.value })} />
        </label>
        <label className="block">
          <span className="block text-[10px] font-semibold uppercase tracking-wide text-gray-500 mb-0.5">Meses de histórico na grade</span>
          <input type="number" min="6" max="60" disabled={!podeEditar} value={cfg.meses_historico} className={campo + " w-[100px]"}
                 onChange={(e) => setCfg({ ...cfg, meses_historico: e.target.value })} />
        </label>
      </div>
      {podeEditar && <div className="mt-4"><button onClick={salvar} className={btnPrim}>Salvar</button></div>}
    </Card>
  );
}

/* ── Raiz da aba ───────────────────────────────────────────────── */
export default function PlanejamentoView({ podeEditar }) {
  const [aba, setAba] = useState("previsao");
  const [versao, setVersao] = useState(0);
  const bump = () => setVersao((v) => v + 1);
  return (
    <>
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="min-w-0">
          <h1 className="text-xl font-bold leading-tight text-gray-900">Planejamento de compras</h1>
          <p className="text-xs text-gray-500">Estoque de reposição · histórico, previsão e necessidade por item</p>
        </div>
        <div className="ml-auto flex items-center gap-1">
          {[["previsao", "Previsão"], ["historico", "Histórico"], ["itens", "Itens"], ["config", "Configuração"]].map(([v, l]) => (
            <button key={v} onClick={() => setAba(v)} className={btn(aba === v)}>{l}</button>
          ))}
        </div>
      </div>
      {aba === "previsao" && <Previsao versao={versao} />}
      {aba === "historico" && <Historico podeEditar={podeEditar} versao={versao} />}
      {aba === "itens" && <Itens podeEditar={podeEditar} aoMudar={bump} />}
      {aba === "config" && <Configuracao podeEditar={podeEditar} aoMudar={bump} />}
    </>
  );
}
