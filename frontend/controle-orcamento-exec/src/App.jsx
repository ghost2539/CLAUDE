import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  LineChart, Line, Tooltip, LabelList, ResponsiveContainer,
} from "recharts";

/* ════════════════════════════════════════════════════════════════
   Domínio: opções, cores e dados iniciais
   ════════════════════════════════════════════════════════════════ */

const TIPOS = ["CAPEX", "OPEX"];

/* Categorias são cadastráveis: vêm da API ({ id, nome, cor }). */
const COR_CATEGORIA_PADRAO = "#9ca3af";
/* Paleta sugerida para novas categorias (primeira cor ainda não usada) */
const PALETA_CATEGORIAS = ["#2563eb", "#f97316", "#8b5cf6", "#22c55e", "#9ca3af", "#0ea5e9", "#ec4899", "#14b8a6", "#eab308", "#ef4444", "#6366f1", "#84cc16", "#a16207", "#64748b"];
const corSugerida = (categorias) => {
  const usadas = new Set(categorias.map((c) => (c.cor || "").toLowerCase()));
  return PALETA_CATEGORIAS.find((c) => !usadas.has(c)) || PALETA_CATEGORIAS[categorias.length % PALETA_CATEGORIAS.length];
};
const API_BASE = "/api/controle-orcamento-exec";
const API_CATEGORIAS = API_BASE + "/categorias";

const ESTAGIOS = ["Planejamento", "Aprovação", "Em Execução", "Concluído"];
const ESTAGIO_CORES = {
  "Planejamento": "#9ca3af",
  "Aprovação": "#eab308",
  "Em Execução": "#2563eb",
  "Concluído": "#22c55e",
};

const PRIORIDADES = ["Alta", "Média", "Baixa"];
const PRIORIDADE_CORES = {
  "Alta": "#ef4444",
  "Média": "#eab308",
  "Baixa": "#22c55e",
};

const STATUS_ESTILO = {
  "No Prazo":  { bg: "#dcfce7", fg: "#166534" },
  "Atenção":   { bg: "#fef9c3", fg: "#854d0e" },
  "Atrasado":  { bg: "#fee2e2", fg: "#991b1b" },
  "Concluído": { bg: "#d1fae5", fg: "#065f46" },
};

/* Os projetos são carregados da API (/api/controle-orcamento/projetos) e
   gravados no banco exclusivo do módulo. Os exemplos iniciais são criados
   pelo servidor quando a tabela está vazia (database_orcamento.SEED). */
const API = API_BASE + "/projetos";

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
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

const SALVAR_APOS_MS = 600; // agrupa digitação antes de gravar

/* Curva "S" mensal (fração acumulada do total). Os valores absolutos do
   gráfico 4 são obtidos multiplicando estas frações pelo orçamento e pelo
   realizado calculados a partir do estado da tabela. */
const EVOLUCAO_MENSAL = [
  { mes: "Jan", planejado: 0.03, realizado: 0.04 },
  { mes: "Fev", planejado: 0.06, realizado: 0.09 },
  { mes: "Mar", planejado: 0.11, realizado: 0.16 },
  { mes: "Abr", planejado: 0.18, realizado: 0.25 },
  { mes: "Mai", planejado: 0.27, realizado: 0.36 },
  { mes: "Jun", planejado: 0.38, realizado: 0.48 },
  { mes: "Jul", planejado: 0.50, realizado: 0.60 },
  { mes: "Ago", planejado: 0.62, realizado: 0.72 },
  { mes: "Set", planejado: 0.73, realizado: 0.82 },
  { mes: "Out", planejado: 0.83, realizado: 0.90 },
  { mes: "Nov", planejado: 0.92, realizado: 0.96 },
  { mes: "Dez", planejado: 1.00, realizado: 1.00 },
];
const FATOR_EAC = 1.006; // previsão ao término = 100,6% do orçamento (simulação)

/* ════════════════════════════════════════════════════════════════
   Utilitários
   ════════════════════════════════════════════════════════════════ */

const fmtBRL = (v) =>
  "R$ " + Math.round(Number(v) || 0).toLocaleString("pt-BR", { maximumFractionDigits: 0 });

const fmtCompact = (v) => {
  const n = Number(v) || 0;
  const abs = Math.abs(n);
  const f = (x, d) => x.toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d });
  if (abs >= 1e9) return "R$ " + f(n / 1e9, 1) + "B";
  if (abs >= 1e6) return "R$ " + f(n / 1e6, 1) + "M";
  if (abs >= 1e3) return "R$ " + f(n / 1e3, 0) + "K";
  return "R$ " + f(n, 0);
};

const fmtAxis = (v) => {
  const n = Number(v) || 0;
  if (n === 0) return "0";
  if (Math.abs(n) >= 1e6) return (n / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 0 }) + "M";
  if (Math.abs(n) >= 1e3) return (n / 1e3).toLocaleString("pt-BR", { maximumFractionDigits: 0 }) + "K";
  return String(n);
};

const fmtPct = (x, d = 1) =>
  ((Number(x) || 0) * 100).toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d }) + "%";

const fmtDate = (iso) => {
  if (!iso) return "";
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
};

const toNumber = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};

const todayISO = () => new Date().toISOString().slice(0, 10);

/* Colunas calculadas por projeto */
function derive(p) {
  const orcamento = toNumber(p.orcamento);
  const comprometido = toNumber(p.comprometido);
  const realizado = toNumber(p.realizado);
  // A Realizar: para projetos vindos do EBS usa o saldo do dia (saldo_dia);
  // para projetos manuais (sem sincronização) calcula na tela.
  const aRealizar = p.sincronizado_em != null
    ? toNumber(p.a_realizar)
    : orcamento - (comprometido + realizado);
  const pctRealizado = orcamento > 0 ? realizado / orcamento : 0;

  let status = "No Prazo";
  if (p.estagio === "Concluído") status = "Concluído";
  else if (p.vencimento && p.vencimento < todayISO()) status = "Atrasado";
  else if (aRealizar < 0) status = "Atenção";
  else if (p.vencimento) {
    const dias = (new Date(p.vencimento) - new Date(todayISO())) / 86400000;
    if (dias <= 30) status = "Atenção";
  }
  return { ...p, orcamento, comprometido, realizado, aRealizar, pctRealizado, status };
}

const sumBy = (arr, key) => arr.reduce((acc, p) => acc + toNumber(p[key]), 0);

const groupSum = (arr, groupKey, order) =>
  order.map((name) => ({
    name,
    value: arr.filter((p) => p[groupKey] === name).reduce((acc, p) => acc + toNumber(p.orcamento), 0),
  }));

/* ════════════════════════════════════════════════════════════════
   Ícones (SVG inline — sem dependência externa)
   ════════════════════════════════════════════════════════════════ */

const Icon = {
  doc: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /><path d="M9 13h6M9 17h6" />
    </svg>
  ),
  dollar: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
      <path d="M12 2v20" /><path d="M17 6.5c0-1.9-2.2-3-5-3s-5 1.1-5 3 2.2 3 5 3 5 1.1 5 3-2.2 3-5 3-5-1.1-5-3" />
    </svg>
  ),
  pie: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
      <path d="M21.2 15.9A10 10 0 1 1 8 2.8" /><path d="M22 12A10 10 0 0 0 12 2v10z" />
    </svg>
  ),
  trend: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
      <path d="M3 17l6-6 4 4 8-8" /><path d="M14 7h7v7" />
    </svg>
  ),
  clipboard: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
      <rect x="6" y="4" width="12" height="17" rx="2" /><path d="M9 4V3h6v1" /><path d="M9 10h6M9 14h6" />
    </svg>
  ),
  target: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
      <circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1.5" />
    </svg>
  ),
  filter: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
      <path d="M3 5h18l-7 8v6l-4 2v-8z" />
    </svg>
  ),
  plus: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-4 w-4">
      <path d="M12 5v14M5 12h14" />
    </svg>
  ),
  copy: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
      <rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" />
    </svg>
  ),
  trash: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
      <path d="M4 7h16" /><path d="M10 11v6M14 11v6" /><path d="M6 7l1 13h10l1-13" /><path d="M9 7V4h6v3" />
    </svg>
  ),
  refresh: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
      <path d="M21 12a9 9 0 1 1-3-6.7" /><path d="M21 3v6h-6" />
    </svg>
  ),
  cloud: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
      <path d="M7 18a4.5 4.5 0 0 1-.6-9A6 6 0 0 1 18 8.5a4 4 0 0 1-.5 9.5z" /><path d="M9 14l2 2 4-4" />
    </svg>
  ),
  tag: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
      <path d="M20 12l-8 8-9-9V4h7z" /><circle cx="7.5" cy="7.5" r="1.5" />
    </svg>
  ),
  close: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-4 w-4">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  ),
  lock: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
      <rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  ),
  lockOpen: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
      <rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V7a4 4 0 0 1 7.5-2" />
    </svg>
  ),
  back: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
      <path d="M19 12H5" /><path d="M12 19l-7-7 7-7" />
    </svg>
  ),
};

/* ════════════════════════════════════════════════════════════════
   Componentes de apoio
   ════════════════════════════════════════════════════════════════ */

function KpiCard({ icon, label, value, sub, color }) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4 flex items-start gap-3" style={{ borderTopWidth: 3, borderTopColor: color }}>
      <div className="shrink-0 h-9 w-9 rounded-md flex items-center justify-center" style={{ background: color + "1a", color }}>
        {icon}
      </div>
      <div className="min-w-0">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">{label}</div>
        <div className="text-base 2xl:text-lg font-bold text-gray-900 leading-tight whitespace-nowrap tabular-nums">{value}</div>
        <div className="text-[11px] text-gray-500 mt-0.5 truncate">{sub}</div>
      </div>
    </div>
  );
}

function ChartCard({ title, subtitle, footer, children }) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4 flex flex-col min-h-[260px]">
      <div className="text-sm font-semibold text-gray-800 mb-2">
        {title} {subtitle && <span className="font-normal text-gray-500">{subtitle}</span>}
      </div>
      <div className="flex-1 min-h-0">{children}</div>
      {footer && <div className="text-[11px] text-gray-500 mt-2">{footer}</div>}
    </div>
  );
}

function MoneyTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div className="bg-white border border-gray-200 rounded shadow-md px-3 py-2 text-xs">
      {label && <div className="font-semibold text-gray-700 mb-1">{label}</div>}
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2 text-gray-700">
          <span className="inline-block h-2 w-2 rounded-sm" style={{ background: p.color || p.payload?.fill }} />
          <span>{p.name}:</span>
          <span className="font-semibold tabular-nums">{fmtBRL(p.value)}</span>
        </div>
      ))}
    </div>
  );
}

/* Rótulo à direita da barra (texto SVG simples, sem quebra de linha) */
function BarValueLabel({ x, y, width, height, value }) {
  return (
    <text x={x + width + 6} y={y + height / 2} dy={4} fontSize={11} fill="#374151">{fmtCompact(value)}</text>
  );
}

function DonutChart({ data, colors }) {
  const total = data.reduce((a, d) => a + d.value, 0);
  const slices = data.filter((d) => d.value > 0);
  return (
    <div className="flex items-center gap-3 h-full">
      <div className="w-[150px] h-[150px] shrink-0">
        {slices.length ? (
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={slices} dataKey="value" nameKey="name" innerRadius={45} outerRadius={70}
                   paddingAngle={1.5} stroke="#fff" strokeWidth={1} isAnimationActive={false}>
                {slices.map((d) => <Cell key={d.name} fill={colors[d.name]} />)}
              </Pie>
              <Tooltip content={<MoneyTooltip />} />
            </PieChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-full w-full rounded-full border-8 border-gray-100 flex items-center justify-center text-[11px] text-gray-400">Sem dados</div>
        )}
      </div>
      <ul className="flex-1 space-y-1.5 text-xs">
        {slices.length === 0 && <li className="text-gray-400">Sem categorias com valor</li>}
        {slices.map((d) => (
          <li key={d.name} className="flex items-center gap-2">
            <span className="inline-block h-3 w-3 rounded-sm shrink-0" style={{ background: colors[d.name] }} />
            <span className="text-gray-700 truncate">{d.name}</span>
            <span className="ml-auto text-gray-500 tabular-nums">{total > 0 ? fmtPct(d.value / total, 0) : "0%"}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* Input monetário: exibe formatado, edita como número */
function MoneyInput({ value, onChange, title }) {
  const [focused, setFocused] = useState(false);
  const [draft, setDraft] = useState("");
  const ref = useRef(null);
  // Seleciona o valor bruto logo após a troca para o modo de edição
  // (antes de qualquer tecla ser processada).
  useLayoutEffect(() => { if (focused && ref.current) ref.current.select(); }, [focused]);
  return (
    <input
      ref={ref}
      type="text"
      inputMode="numeric"
      title={title}
      className="cell-input num w-[108px]"
      value={focused ? draft : fmtBRL(value)}
      onFocus={() => { setDraft(value ? String(Math.round(value)) : ""); setFocused(true); }}
      onBlur={() => setFocused(false)}
      onChange={(e) => {
        const digits = e.target.value.replace(/\D/g, "");
        setDraft(digits);
        onChange(digits === "" ? 0 : Number(digits));
      }}
    />
  );
}

function BadgeSelect({ value, options, colors, onChange, minWidth = 96 }) {
  return (
    <div className="select-wrap inline-block">
      <select className="cell-input badge !text-white !border-transparent pr-5"
              style={{ background: colors[value] || "#6b7280", minWidth }}
              value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o} value={o} style={{ color: "#111827", background: "#fff" }}>{o}</option>)}
      </select>
    </div>
  );
}

function FilterSelect({ label, value, options, onChange }) {
  return (
    <label className="block min-w-0">
      <span className="block text-[11px] font-medium text-gray-600 mb-1">{label}</span>
      <div className="select-wrap">
        <select className="w-full appearance-none bg-white border border-gray-300 rounded-md px-2.5 py-1.5 pr-7 text-xs text-gray-800 focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                value={value} onChange={(e) => onChange(e.target.value)}>
          {options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>
    </label>
  );
}

/* Gerenciador de categorias (nome + cor), com gravação imediata */
function CategoriasModal({ categorias, emUso, onCriar, onAtualizar, onExcluir, onFechar }) {
  const [novoNome, setNovoNome] = useState("");
  const [novaCor, setNovaCor] = useState(() => corSugerida(categorias));
  const [erro, setErro] = useState("");
  const [ocupado, setOcupado] = useState(false);

  const executar = async (fn) => {
    setOcupado(true); setErro("");
    try { await fn(); } catch (e) { setErro(e.message); } finally { setOcupado(false); }
  };

  const criar = () => {
    const nome = novoNome.trim();
    if (!nome) return;
    executar(async () => { await onCriar(nome, novaCor); setNovoNome(""); setNovaCor(corSugerida([...categorias, { cor: novaCor }])); });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-16" onMouseDown={(e) => { if (e.target === e.currentTarget) onFechar(); }}>
      <div className="w-full max-w-lg bg-white rounded-lg shadow-xl border border-gray-200">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-200">
          <span className="text-gray-600">{Icon.tag}</span>
          <h3 className="text-sm font-semibold text-gray-800">Categorias de projeto</h3>
          <button onClick={onFechar} className="ml-auto p-1 rounded text-gray-500 hover:bg-gray-100" title="Fechar">{Icon.close}</button>
        </div>

        <div className="px-4 py-3 space-y-1 max-h-[55vh] overflow-y-auto">
          {categorias.length === 0 && <div className="text-xs text-gray-500 py-4 text-center">Nenhuma categoria cadastrada.</div>}
          {categorias.map((c) => (
            <CategoriaLinha key={c.id} categoria={c} usos={emUso[c.nome] || 0}
                            onSalvar={(campos) => executar(() => onAtualizar(c.id, campos))}
                            onExcluir={() => { if (window.confirm(`Excluir a categoria "${c.nome}"?`)) executar(() => onExcluir(c.id)); }} />
          ))}
        </div>

        <div className="px-4 py-3 border-t border-gray-200 bg-gray-50 rounded-b-lg">
          <div className="text-[11px] font-medium text-gray-600 mb-1.5">Nova categoria</div>
          <div className="flex items-center gap-2">
            <input type="color" value={novaCor} onChange={(e) => setNovaCor(e.target.value)} title="Cor" className="h-8 w-10 p-0.5 border border-gray-300 rounded bg-white cursor-pointer" />
            <input value={novoNome} onChange={(e) => setNovoNome(e.target.value)} onKeyDown={(e) => e.key === "Enter" && criar()}
                   placeholder="Nome da categoria" maxLength={60}
                   className="flex-1 border border-gray-300 rounded-md px-2.5 py-1.5 text-xs focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100" />
            <button onClick={criar} disabled={ocupado || !novoNome.trim()}
                    className="inline-flex items-center gap-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-md px-3 py-1.5 disabled:opacity-50">
              {Icon.plus} Adicionar
            </button>
          </div>
          {erro && <div className="mt-2 text-[11px] text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1">{erro}</div>}
          <div className="mt-2 text-[11px] text-gray-500">Renomear atualiza os projetos que usam a categoria. Só é possível excluir categorias sem projetos vinculados.</div>
        </div>
      </div>
    </div>
  );
}

function CategoriaLinha({ categoria, usos, onSalvar, onExcluir }) {
  const [nome, setNome] = useState(categoria.nome);
  useEffect(() => setNome(categoria.nome), [categoria.nome]);
  const salvarNome = () => {
    const v = nome.trim();
    if (!v) { setNome(categoria.nome); return; }
    if (v !== categoria.nome) onSalvar({ nome: v });
  };
  return (
    <div className="flex items-center gap-2 py-1">
      <input type="color" value={categoria.cor} title="Cor" onChange={(e) => onSalvar({ cor: e.target.value })}
             className="h-8 w-10 p-0.5 border border-gray-300 rounded bg-white cursor-pointer" />
      <input value={nome} maxLength={60} onChange={(e) => setNome(e.target.value)} onBlur={salvarNome}
             onKeyDown={(e) => { if (e.key === "Enter") e.target.blur(); if (e.key === "Escape") setNome(categoria.nome); }}
             className="flex-1 border border-transparent hover:border-gray-300 focus:border-blue-500 rounded-md px-2.5 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-blue-100" />
      <span className="text-[11px] text-gray-500 w-20 text-right tabular-nums">{usos} projeto(s)</span>
      <button onClick={onExcluir} disabled={usos > 0} title={usos > 0 ? "Em uso por projetos" : "Excluir categoria"}
              className="p-1 rounded text-gray-500 hover:text-red-600 hover:bg-red-50 disabled:opacity-30 disabled:cursor-not-allowed">{Icon.trash}</button>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════
   App
   ════════════════════════════════════════════════════════════════ */

const FILTROS_INICIAIS = { ano: "Todos", tipo: "Todos", area: "Todas", categoria: "Todas", prioridade: "Todas", estagio: "Todos", status: "Todos" };

export default function App() {
  const [projects, setProjects] = useState([]);
  const [filtros, setFiltros] = useState(FILTROS_INICIAIS);
  const [user, setUser] = useState(null);
  const [categorias, setCategorias] = useState([]);
  const [modalCategorias, setModalCategorias] = useState(false);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState("");
  const [pendentes, setPendentes] = useState(0);   // gravações em andamento
  const [ultimoSalvo, setUltimoSalvo] = useState(null);

  /* Barra de inclusão de projetos (Número puxa do EBS) */
  const [inc, setInc] = useState({ numero: "", tipo: "CAPEX", projeto_demanda: "", categoria: "", area: "" });
  const [incBusy, setIncBusy] = useState(false);
  const [incMsg, setIncMsg] = useState("");
  const [sincBusy, setSincBusy] = useState(false);

  /* Alterações ainda não enviadas: { [id]: { campo: valor } } e seus timers */
  const filaRef = useRef({});
  const timersRef = useRef({});

  /* Usuário logado no portal, se houver (a tela é pública) */
  useEffect(() => {
    api(API_BASE + "/sessao").then((d) => d.usuario && setUser(d.usuario)).catch(() => {});
  }, []);

  /* ── Carga inicial ──────────────────────────────────────────── */
  const carregar = useCallback(async () => {
    setCarregando(true);
    setErro("");
    try {
      const data = await api(API);
      setProjects(data.projetos);
      setCategorias(data.opcoes?.categorias || []);
    } catch (e) {
      setErro("Não foi possível carregar os projetos: " + e.message);
    } finally {
      setCarregando(false);
    }
  }, []);
  useEffect(() => { carregar(); }, [carregar]);

  /* ── Gravação (debounce por projeto) ─────────────────────────── */
  const enviar = useCallback(async (id, opcoes = {}) => {
    const campos = filaRef.current[id];
    if (!campos) return;
    delete filaRef.current[id];
    clearTimeout(timersRef.current[id]);
    delete timersRef.current[id];
    setPendentes((n) => n + 1);
    try {
      await api(`${API}/${id}`, { method: "PATCH", body: JSON.stringify(campos), ...opcoes });
      setUltimoSalvo(new Date());
      setErro("");
    } catch (e) {
      // devolve à fila para permitir nova tentativa
      filaRef.current[id] = { ...campos, ...(filaRef.current[id] || {}) };
      setErro("Falha ao salvar: " + e.message);
    } finally {
      setPendentes((n) => n - 1);
    }
  }, []);

  const enviarTudo = useCallback((opcoes) => {
    Object.keys(filaRef.current).forEach((id) => enviar(Number(id), opcoes));
  }, [enviar]);

  /* Envia o que estiver pendente ao sair/ocultar a página */
  useEffect(() => {
    const flush = () => enviarTudo({ keepalive: true });
    const onVisibility = () => { if (document.visibilityState === "hidden") flush(); };
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [enviarTudo]);

  /* ── Atualização a partir da tabela: estado imediato + gravação ── */
  const handleUpdateProject = (id, field, value) => {
    setProjects((prev) => prev.map((p) => (p.id === id ? { ...p, [field]: value } : p)));
    filaRef.current[id] = { ...(filaRef.current[id] || {}), [field]: value };
    clearTimeout(timersRef.current[id]);
    timersRef.current[id] = setTimeout(() => enviar(id), SALVAR_APOS_MS);
  };

  const executar = async (fn) => {
    setPendentes((n) => n + 1);
    try {
      await fn();
      setUltimoSalvo(new Date());
      setErro("");
    } catch (e) {
      setErro("Falha ao salvar: " + e.message);
    } finally {
      setPendentes((n) => n - 1);
    }
  };

  const handleAddProject = () => executar(async () => {
    const seq = String(projects.length + 1).padStart(3, "0");
    const novo = await api(API, { method: "POST", body: JSON.stringify({ codigo: `PRJ-NOVO-${seq}`, nome: "Novo projeto" }) });
    setProjects((prev) => [...prev, novo]);
  });

  const handleDuplicateProject = (id) => executar(async () => {
    await enviar(id); // garante que a cópia parte do estado gravado
    const copia = await api(`${API}/${id}/duplicar`, { method: "POST" });
    setProjects((prev) => {
      const idx = prev.findIndex((p) => p.id === id);
      return idx < 0 ? [...prev, copia] : [...prev.slice(0, idx + 1), copia, ...prev.slice(idx + 1)];
    });
  });

  const handleDeleteProject = (id) => {
    const p = projects.find((x) => x.id === id);
    if (!window.confirm(`Excluir o projeto ${p?.codigo || ""} "${p?.nome || ""}"? Esta ação não pode ser desfeita.`)) return;
    executar(async () => {
      delete filaRef.current[id];
      clearTimeout(timersRef.current[id]);
      await api(`${API}/${id}`, { method: "DELETE" });
      setProjects((prev) => prev.filter((x) => x.id !== id));
    });
  };

  /* ── Categorias ──────────────────────────────────────────────── */
  const handleCriarCategoria = async (nome, cor) => {
    const c = await api(API_CATEGORIAS, { method: "POST", body: JSON.stringify({ nome, cor }) });
    setCategorias((prev) => [...prev, c]);
    setUltimoSalvo(new Date());
  };
  const handleAtualizarCategoria = async (id, campos) => {
    const antiga = categorias.find((c) => c.id === id);
    const c = await api(`${API_CATEGORIAS}/${id}`, { method: "PATCH", body: JSON.stringify(campos) });
    setCategorias((prev) => prev.map((x) => (x.id === id ? c : x)));
    if (antiga && c.nome !== antiga.nome) {
      // o servidor já propagou o novo nome aos projetos; refletir localmente
      setProjects((prev) => prev.map((p) => (p.categoria === antiga.nome ? { ...p, categoria: c.nome } : p)));
      if (filtros.categoria === antiga.nome) setFiltros({ ...filtros, categoria: c.nome });
    }
    setUltimoSalvo(new Date());
  };
  const handleExcluirCategoria = async (id) => {
    await api(`${API_CATEGORIAS}/${id}`, { method: "DELETE" });
    setCategorias((prev) => prev.filter((x) => x.id !== id));
    setUltimoSalvo(new Date());
  };

  const handleRecarregar = async () => {
    await Promise.all(Object.keys(filaRef.current).map((id) => enviar(Number(id))));
    await carregar();
  };

  /* ── Inclusão de projeto (puxa do EBS pelo número) ─────────────── */
  const handleIncluir = async () => {
    const numero = inc.numero.trim();
    if (!numero) { setIncMsg("Informe o número do projeto."); return; }
    setIncBusy(true); setIncMsg("");
    try {
      const r = await api(API_BASE + "/incluir", {
        method: "POST",
        body: JSON.stringify({
          numero,
          tipo: inc.tipo,
          projeto_demanda: inc.projeto_demanda.trim(),
          categoria: inc.categoria.trim(),
          area: inc.area.trim(),
        }),
      });
      await carregar();  // recarrega categorias (pode ter sido criada) e projetos
      setInc({ numero: "", tipo: inc.tipo, projeto_demanda: "", categoria: inc.categoria, area: inc.area });
      setIncMsg(r.aviso ? ("Incluído com aviso: " + r.aviso) : "Projeto(s) incluído(s) e valores puxados do EBS.");
      setUltimoSalvo(new Date());
    } catch (e) {
      setIncMsg("Falha ao incluir: " + e.message);
    } finally {
      setIncBusy(false);
    }
  };

  /* ── Atualizar: sincroniza todos os valores com o EBS e recarrega ── */
  const handleSincronizar = async () => {
    setSincBusy(true); setErro("");
    try {
      await Promise.all(Object.keys(filaRef.current).map((id) => enviar(Number(id))));
      const r = await api(API_BASE + "/sincronizar", { method: "POST" });
      await carregar();
      setIncMsg(
        `Sincronizado com o EBS: ${r.atualizados} projeto(s) atualizado(s).` +
        (r.aviso ? " " + r.aviso : "")
      );
      setUltimoSalvo(new Date());
    } catch (e) {
      setErro("Falha ao sincronizar com o EBS: " + e.message);
    } finally {
      setSincBusy(false);
    }
  };

  /* ── Variáveis derivadas (recalculadas a cada render) ─────────── */
  const derived = useMemo(() => projects.map(derive), [projects]);

  /* Nomes e cores das categorias (inclui nomes órfãos ainda usados em projetos) */
  const nomesCategoria = useMemo(() => {
    const nomes = categorias.map((c) => c.nome);
    derived.forEach((p) => { if (p.categoria && !nomes.includes(p.categoria)) nomes.push(p.categoria); });
    return nomes;
  }, [categorias, derived]);
  const coresCategoria = useMemo(() => {
    const m = {};
    // Categorias sem cor real (ou na cor padrão cinza) recebem uma cor da
    // paleta por índice, para o gráfico não ficar todo cinza.
    let i = 0;
    const proxima = () => PALETA_CATEGORIAS[i++ % PALETA_CATEGORIAS.length];
    categorias.forEach((c) => {
      const cor = (c.cor || "").toLowerCase();
      m[c.nome] = (!cor || cor === COR_CATEGORIA_PADRAO) ? proxima() : c.cor;
    });
    nomesCategoria.forEach((n) => { if (!m[n]) m[n] = proxima(); });
    return m;
  }, [categorias, nomesCategoria]);
  const usosCategoria = useMemo(() => {
    const m = {};
    derived.forEach((p) => { m[p.categoria] = (m[p.categoria] || 0) + 1; });
    return m;
  }, [derived]);

  const opcoesFiltro = useMemo(() => {
    const uniq = (arr) => Array.from(new Set(arr.filter(Boolean))).sort((a, b) => a.localeCompare(b, "pt-BR"));
    return {
      ano: ["Todos", ...uniq(derived.map((p) => (p.vencimento || "").slice(0, 4)))],
      tipo: ["Todos", ...TIPOS],
      area: ["Todas", ...uniq(derived.map((p) => p.area))],
      categoria: ["Todas", ...nomesCategoria],
      prioridade: ["Todas", ...PRIORIDADES],
      estagio: ["Todos", ...ESTAGIOS],
      status: ["Todos", ...Object.keys(STATUS_ESTILO)],
    };
  }, [derived, nomesCategoria]);

  const visiveis = useMemo(() => derived.filter((p) =>
    (filtros.ano === "Todos" || (p.vencimento || "").startsWith(filtros.ano)) &&
    (filtros.tipo === "Todos" || p.tipo === filtros.tipo) &&
    (filtros.area === "Todas" || p.area === filtros.area) &&
    (filtros.categoria === "Todas" || p.categoria === filtros.categoria) &&
    (filtros.prioridade === "Todas" || p.prioridade === filtros.prioridade) &&
    (filtros.estagio === "Todos" || p.estagio === filtros.estagio) &&
    (filtros.status === "Todos" || p.status === filtros.status)
  ), [derived, filtros]);

  const filtrosAtivos = Object.keys(filtros).some((k) => filtros[k] !== FILTROS_INICIAIS[k]);

  /* KPIs */
  const totalDemandas = visiveis.length;
  const totalOrcamento = sumBy(visiveis, "orcamento");
  const totalCapex = sumBy(visiveis.filter((p) => p.tipo === "CAPEX"), "orcamento");
  const totalRealizado = sumBy(visiveis, "realizado");
  const totalComprometido = sumBy(visiveis, "comprometido");
  const totalARealizar = sumBy(visiveis, "aRealizar");
  const emExecucao = visiveis.filter((p) => p.estagio === "Em Execução").length;
  const pct = (v) => (totalOrcamento > 0 ? fmtPct(v / totalOrcamento) : "0,0%") + " do orçamento total";

  /* Gráficos 1–3 */
  const porCategoria = useMemo(() => groupSum(visiveis, "categoria", nomesCategoria), [visiveis, nomesCategoria]);
  const porEstagio = useMemo(() => groupSum(visiveis, "estagio", ESTAGIOS), [visiveis]);
  const porPrioridade = useMemo(() => groupSum(visiveis, "prioridade", PRIORIDADES), [visiveis]);

  /* Gráfico 4: curva S escalada pelo orçamento/realizado atuais */
  const evolucao = useMemo(() => {
    const mesAtual = new Date().getMonth();
    const eac = totalOrcamento * FATOR_EAC;
    const fracRealAtual = EVOLUCAO_MENSAL[mesAtual].realizado;
    const fracPlanAtual = EVOLUCAO_MENSAL[mesAtual].planejado;
    const realAtual = totalRealizado;
    return EVOLUCAO_MENSAL.map((m, i) => {
      const planejado = Math.round(m.planejado * totalOrcamento);
      const realizado = i <= mesAtual ? Math.round(realAtual * (m.realizado / fracRealAtual)) : null;
      let forecast = null;
      if (i >= mesAtual) {
        const t = fracPlanAtual >= 1 ? 1 : (m.planejado - fracPlanAtual) / (1 - fracPlanAtual);
        forecast = Math.round(realAtual + (eac - realAtual) * t);
      }
      return { mes: m.mes, planejado, realizado, forecast };
    });
  }, [totalOrcamento, totalRealizado]);
  const eac = totalOrcamento * FATOR_EAC;
  const variacao = eac - totalOrcamento;

  const maxPrioridade = Math.max(0, ...porPrioridade.map((d) => d.value));

  /* ── Render ───────────────────────────────────────────────────── */
  return (
    <div className="min-h-screen bg-gray-100 text-gray-900 font-sans">
      {/* Cabeçalho */}
      <header className="bg-white border-b border-gray-200">
        <div className="max-w-[1600px] mx-auto px-4 py-3 flex flex-wrap items-center gap-3">
          <a href="/" className="inline-flex items-center gap-1.5 text-xs text-gray-600 hover:text-gray-900 border border-gray-300 rounded-md px-2.5 py-1.5 bg-white">
            {Icon.back} Portal
          </a>
          <div className="min-w-0">
            <h1 className="text-base font-bold leading-tight">Execução de CAPEX</h1>
            <p className="text-[11px] text-gray-500">Acompanhamento da execução · CAPEX / OPEX · dados do EBS</p>
          </div>
          <div className="ml-auto flex items-center gap-3">
            <span className={"inline-flex items-center gap-1.5 text-[11px] " + (erro ? "text-red-600" : pendentes > 0 ? "text-amber-600" : "text-gray-500")} title="Gravação automática no banco do módulo">
              {Icon.cloud}
              {erro ? "Erro ao salvar" : pendentes > 0 ? "Salvando…" : ultimoSalvo ? `Salvo ${ultimoSalvo.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}` : "Gravação automática"}
            </span>
            {user && <span className="text-xs text-gray-600 hidden sm:inline">{user.display_name || user.username}</span>}
            <button onClick={handleSincronizar} disabled={carregando || sincBusy} title="Puxa os valores de todos os projetos da API de CAPEX do EBS"
                    className="inline-flex items-center gap-1.5 text-xs text-white bg-blue-600 hover:bg-blue-700 rounded-md px-2.5 py-1.5 disabled:opacity-50">
              {Icon.refresh} {sincBusy ? "Atualizando…" : "Atualizar (EBS)"}
            </button>
          </div>
        </div>

        {/* Barra de inclusão de projetos */}
        <div className="border-t border-gray-100 bg-gray-50">
          <div className="max-w-[1600px] mx-auto px-4 py-2.5">
            <div className="flex flex-wrap items-end gap-2">
              <label className="block">
                <span className="block text-[10px] font-semibold uppercase tracking-wide text-gray-500 mb-0.5">Número</span>
                <input value={inc.numero} onChange={(e) => setInc({ ...inc, numero: e.target.value })}
                       onKeyDown={(e) => e.key === "Enter" && !incBusy && handleIncluir()}
                       placeholder="ex.: 260021" title="Número do projeto (puxa os dados do EBS). Vários separados por vírgula."
                       className="w-[130px] border border-gray-300 rounded-md px-2.5 py-1.5 text-xs focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100" />
              </label>
              <label className="block">
                <span className="block text-[10px] font-semibold uppercase tracking-wide text-gray-500 mb-0.5">Tipo</span>
                <div className="select-wrap">
                  <select value={inc.tipo} onChange={(e) => setInc({ ...inc, tipo: e.target.value })}
                          className="w-[92px] appearance-none bg-white border border-gray-300 rounded-md px-2.5 py-1.5 pr-7 text-xs focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100">
                    {TIPOS.map((o) => <option key={o}>{o}</option>)}
                  </select>
                </div>
              </label>
              <label className="block flex-1 min-w-[180px]">
                <span className="block text-[10px] font-semibold uppercase tracking-wide text-gray-500 mb-0.5">Projeto / Demanda</span>
                <input value={inc.projeto_demanda} onChange={(e) => setInc({ ...inc, projeto_demanda: e.target.value })}
                       onKeyDown={(e) => e.key === "Enter" && !incBusy && handleIncluir()}
                       placeholder="Descrição informada por você (não vem do EBS)" title="Nome/descrição do projeto — informado aqui, não é puxado do EBS"
                       className="w-full border border-gray-300 rounded-md px-2.5 py-1.5 text-xs focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100" />
              </label>
              <label className="block">
                <span className="block text-[10px] font-semibold uppercase tracking-wide text-gray-500 mb-0.5">Categoria</span>
                <input list="cat-list" value={inc.categoria} onChange={(e) => setInc({ ...inc, categoria: e.target.value })}
                       placeholder="Categoria" title="Escolha uma categoria existente ou digite uma nova"
                       className="w-[150px] border border-gray-300 rounded-md px-2.5 py-1.5 text-xs focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100" />
                <datalist id="cat-list">{nomesCategoria.map((n) => <option key={n} value={n} />)}</datalist>
              </label>
              <label className="block">
                <span className="block text-[10px] font-semibold uppercase tracking-wide text-gray-500 mb-0.5">Área Responsável</span>
                <input value={inc.area} onChange={(e) => setInc({ ...inc, area: e.target.value })}
                       onKeyDown={(e) => e.key === "Enter" && !incBusy && handleIncluir()}
                       placeholder="Área" className="w-[140px] border border-gray-300 rounded-md px-2.5 py-1.5 text-xs focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100" />
              </label>
              <button onClick={handleIncluir} disabled={incBusy || !inc.numero.trim()}
                      className="inline-flex items-center gap-1.5 text-xs font-medium text-white bg-green-600 hover:bg-green-700 rounded-md px-3 py-1.5 disabled:opacity-50">
                {Icon.plus} {incBusy ? "Incluindo…" : "Incluir"}
              </button>
            </div>
            {incMsg && (
              <div className="mt-1.5 text-[11px] text-gray-600">{incMsg}</div>
            )}
          </div>
        </div>
      </header>

      <main className="max-w-[1600px] mx-auto px-4 py-4 space-y-4">
        {erro && (
          <div className="flex flex-wrap items-center gap-3 bg-red-50 border border-red-200 text-red-800 rounded-lg px-4 py-2.5 text-xs">
            <span className="font-semibold">{erro}</span>
            <button onClick={() => (Object.keys(filaRef.current).length ? enviarTudo() : carregar())}
                    className="ml-auto inline-flex items-center gap-1.5 border border-red-300 rounded-md px-2.5 py-1 bg-white hover:bg-red-100">
              {Icon.refresh} Tentar novamente
            </button>
          </div>
        )}
        {carregando && (
          <div className="text-xs text-gray-500 bg-white border border-gray-200 rounded-lg px-4 py-2.5">Carregando projetos…</div>
        )}

        {/* KPIs */}
        <section className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
          <KpiCard icon={Icon.doc} color="#64748b" label="Demandas" value={totalDemandas} sub={`${emExecucao} em execução`} />
          <KpiCard icon={Icon.dollar} color="#22c55e" label="Valor Total" value={fmtBRL(totalOrcamento)} sub="Orçamento aprovado" />
          <KpiCard icon={Icon.pie} color="#2563eb" label="CAPEX Aprovado" value={fmtBRL(totalCapex)} sub={pct(totalCapex).replace("do orçamento total", "do valor total")} />
          <KpiCard icon={Icon.trend} color="#8b5cf6" label="Realizado (Acum.)" value={fmtBRL(totalRealizado)} sub={pct(totalRealizado)} />
          <KpiCard icon={Icon.clipboard} color="#f97316" label="Comprometido" value={fmtBRL(totalComprometido)} sub={pct(totalComprometido)} />
          <KpiCard icon={Icon.target} color="#06b6d4" label="A Realizar" value={fmtBRL(totalARealizar)} sub={pct(totalARealizar)} />
        </section>

        {/* Gráficos */}
        <section className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
          <ChartCard title="Distribuição por Categoria" footer={`Total: ${fmtBRL(totalOrcamento)}`}>
            <DonutChart data={porCategoria} colors={coresCategoria} />
          </ChartCard>

          <ChartCard title="Estágio dos Projetos" subtitle="(Valor Aprovado)" footer={`Total: ${fmtBRL(totalOrcamento)}`}>
            <DonutChart data={porEstagio} colors={ESTAGIO_CORES} />
          </ChartCard>

          <ChartCard title="Valor Aprovado por Prioridade" footer={`Total: ${fmtBRL(totalOrcamento)}`}>
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={porPrioridade} layout="vertical" margin={{ top: 4, right: 64, bottom: 0, left: 0 }} barCategoryGap={10}>
                <CartesianGrid horizontal={false} stroke="#e5e7eb" />
                <XAxis type="number" tickFormatter={fmtAxis} tick={{ fontSize: 10, fill: "#6b7280" }} axisLine={false} tickLine={false}
                       domain={[0, maxPrioridade > 0 ? "auto" : 1]} />
                <YAxis type="category" dataKey="name" width={44} tick={{ fontSize: 11, fill: "#374151" }} axisLine={false} tickLine={false} />
                <Tooltip content={<MoneyTooltip />} cursor={{ fill: "#f3f4f6" }} />
                <Bar dataKey="value" name="Valor aprovado" radius={[0, 3, 3, 0]} isAnimationActive={false}>
                  {porPrioridade.map((d) => <Cell key={d.name} fill={PRIORIDADE_CORES[d.name]} />)}
                  <LabelList dataKey="value" content={BarValueLabel} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>

          <ChartCard title="Evolução do Realizado (R$)">
            <div className="flex gap-2 h-full">
              <div className="flex-1 min-w-0">
                <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-gray-600 mb-1">
                  <span className="inline-flex items-center gap-1"><span className="inline-block w-3.5 border-t-2 border-blue-600" />Realizado Acumulado</span>
                  <span className="inline-flex items-center gap-1"><span className="inline-block w-3.5 border-t-2 border-dashed border-gray-400" />Planejado Acumulado</span>
                  <span className="inline-flex items-center gap-1"><span className="inline-block w-3.5 border-t-2 border-dashed border-green-500" />Forecast</span>
                </div>
                <ResponsiveContainer width="100%" height={180}>
                  <LineChart data={evolucao} margin={{ top: 4, right: 8, bottom: 0, left: -6 }}>
                    <CartesianGrid stroke="#e5e7eb" vertical={false} />
                    <XAxis dataKey="mes" interval={0} tick={{ fontSize: 8, fill: "#6b7280" }} axisLine={false} tickLine={false} />
                    <YAxis tickFormatter={fmtAxis} tick={{ fontSize: 10, fill: "#6b7280" }} axisLine={false} tickLine={false} width={36} />
                    <Tooltip content={<MoneyTooltip />} />
                    <Line type="monotone" dataKey="realizado" name="Realizado Acumulado" stroke="#2563eb" strokeWidth={2} dot={{ r: 2.5, fill: "#2563eb" }} connectNulls={false} isAnimationActive={false} />
                    <Line type="monotone" dataKey="planejado" name="Planejado Acumulado" stroke="#9ca3af" strokeWidth={1.5} strokeDasharray="4 3" dot={{ r: 2, fill: "#9ca3af" }} isAnimationActive={false} />
                    <Line type="monotone" dataKey="forecast" name="Forecast" stroke="#22c55e" strokeWidth={1.5} strokeDasharray="4 3" dot={{ r: 2, fill: "#22c55e" }} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
              <div className="w-[92px] shrink-0 border border-gray-200 rounded-md bg-gray-50 p-2 text-[10px] text-gray-500 space-y-1.5">
                <div><div>Previsto (EAC)</div><div className="text-[12px] font-bold text-gray-900 tabular-nums">{fmtCompact(eac)}</div></div>
                <div><div>Orçamento</div><div className="text-[12px] font-bold text-gray-900 tabular-nums">{fmtCompact(totalOrcamento)}</div></div>
                <div>
                  <div>Variação</div>
                  <div className="text-[12px] font-bold tabular-nums" style={{ color: variacao > 0 ? "#dc2626" : "#16a34a" }}>
                    {variacao >= 0 ? "+" : "-"}{fmtCompact(Math.abs(variacao)).replace("R$ ", "")}
                  </div>
                  <div className="tabular-nums">({totalOrcamento > 0 ? (variacao >= 0 ? "+" : "-") + fmtPct(Math.abs(variacao) / totalOrcamento) : "0,0%"})</div>
                </div>
              </div>
            </div>
          </ChartCard>
        </section>

        {/* Filtros */}
        <section className="bg-white rounded-lg border border-gray-200 shadow-sm p-3">
          <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3 items-end">
            <FilterSelect label="Ano" value={filtros.ano} options={opcoesFiltro.ano} onChange={(v) => setFiltros({ ...filtros, ano: v })} />
            <FilterSelect label="Tipo" value={filtros.tipo} options={opcoesFiltro.tipo} onChange={(v) => setFiltros({ ...filtros, tipo: v })} />
            <FilterSelect label="Unidade / Área" value={filtros.area} options={opcoesFiltro.area} onChange={(v) => setFiltros({ ...filtros, area: v })} />
            <FilterSelect label="Categoria" value={filtros.categoria} options={opcoesFiltro.categoria} onChange={(v) => setFiltros({ ...filtros, categoria: v })} />
            <FilterSelect label="Prioridade" value={filtros.prioridade} options={opcoesFiltro.prioridade} onChange={(v) => setFiltros({ ...filtros, prioridade: v })} />
            <FilterSelect label="Estágio" value={filtros.estagio} options={opcoesFiltro.estagio} onChange={(v) => setFiltros({ ...filtros, estagio: v })} />
            <FilterSelect label="Status" value={filtros.status} options={opcoesFiltro.status} onChange={(v) => setFiltros({ ...filtros, status: v })} />
            <button onClick={() => setFiltros(FILTROS_INICIAIS)} disabled={!filtrosAtivos}
                    className="inline-flex items-center justify-center gap-1.5 text-xs text-gray-700 border border-gray-300 rounded-md px-3 py-1.5 bg-white hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed">
              {Icon.filter} Limpar Filtros
            </button>
          </div>
        </section>

        {/* Tabela editável */}
        <section className="bg-white rounded-lg border border-gray-200 shadow-sm">
          <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-gray-200">
            <h2 className="text-sm font-semibold text-gray-800">Portfólio de Projetos</h2>
            <span className="text-[11px] text-gray-500">
              {visiveis.length} de {projects.length} projeto(s){filtrosAtivos ? " · filtros ativos" : ""} · clique em uma célula para editar
            </span>
            <button onClick={() => setModalCategorias(true)} disabled={carregando}
                    className="ml-auto disabled:opacity-50 inline-flex items-center gap-1.5 text-xs font-medium text-gray-700 bg-white border border-gray-300 hover:bg-gray-50 rounded-md px-3 py-1.5">
              {Icon.tag} Categorias
            </button>
            <button onClick={handleAddProject} disabled={carregando}
                    className="disabled:opacity-50 inline-flex items-center gap-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-md px-3 py-1.5">
              {Icon.plus} Novo projeto
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="min-w-[1200px] w-full text-[12px] border-collapse">
              <thead className="bg-gray-50 text-gray-600">
                <tr className="text-[11px]">
                  <th rowSpan={2} className="th">ID</th>
                  <th rowSpan={2} className="th text-left">Projeto / Demanda</th>
                  <th rowSpan={2} className="th">Tipo</th>
                  <th rowSpan={2} className="th">Categoria</th>
                  <th rowSpan={2} className="th text-left">Área Responsável</th>
                  <th rowSpan={2} className="th">Estágio</th>
                  <th rowSpan={2} className="th">Prioridade</th>
                  <th colSpan={4} className="th text-center text-blue-700 border-b border-gray-200">Valores (R$)</th>
                  <th rowSpan={2} className="th text-right">% Realizado</th>
                  <th rowSpan={2} className="th">Vencimento Previsto</th>
                  <th rowSpan={2} className="th">Status</th>
                  <th rowSpan={2} className="th">Ações</th>
                </tr>
                <tr className="text-[11px]">
                  <th className="th text-right">Orçamento Aprovado</th>
                  <th className="th text-right">Comprometido</th>
                  <th className="th text-right">Realizado (Acum.)</th>
                  <th className="th text-right">A Realizar</th>
                </tr>
              </thead>
              <tbody>
                {!carregando && visiveis.length === 0 && (
                  <tr><td colSpan={15} className="px-4 py-8 text-center text-gray-500 text-xs">
                    {projects.length === 0 ? "Nenhum projeto cadastrado. Clique em \"Novo projeto\" para começar." : "Nenhum projeto corresponde aos filtros selecionados."}
                  </td></tr>
                )}
                {visiveis.map((p) => {
                  const st = STATUS_ESTILO[p.status];
                  return (
                    <tr key={p.id} className={"border-t border-gray-100 " + (p.bloqueado ? "bg-amber-50/50 hover:bg-amber-50" : "hover:bg-blue-50/30")}>
                      <td className="td">
                        <input className="cell-input font-medium text-gray-600 w-[96px]" value={p.codigo} onChange={(e) => handleUpdateProject(p.id, "codigo", e.target.value)} />
                      </td>
                      <td className="td">
                        <input className="cell-input min-w-[190px]" value={p.nome} onChange={(e) => handleUpdateProject(p.id, "nome", e.target.value)} />
                      </td>
                      <td className="td">
                        <div className="select-wrap">
                          <select className="cell-input min-w-[84px]" value={p.tipo} onChange={(e) => handleUpdateProject(p.id, "tipo", e.target.value)}>
                            {TIPOS.map((o) => <option key={o}>{o}</option>)}
                          </select>
                        </div>
                      </td>
                      <td className="td">
                        <div className="select-wrap">
                          <select className="cell-input min-w-[136px]" value={p.categoria} onChange={(e) => handleUpdateProject(p.id, "categoria", e.target.value)}>
                            {nomesCategoria.map((o) => <option key={o}>{o}</option>)}
                          </select>
                        </div>
                      </td>
                      <td className="td">
                        <input className="cell-input min-w-[100px]" value={p.area} placeholder="Área" onChange={(e) => handleUpdateProject(p.id, "area", e.target.value)} />
                      </td>
                      <td className="td text-center">
                        <BadgeSelect minWidth={118} value={p.estagio} options={ESTAGIOS} colors={ESTAGIO_CORES} onChange={(v) => handleUpdateProject(p.id, "estagio", v)} />
                      </td>
                      <td className="td text-center">
                        <BadgeSelect minWidth={80} value={p.prioridade} options={PRIORIDADES} colors={PRIORIDADE_CORES} onChange={(v) => handleUpdateProject(p.id, "prioridade", v)} />
                      </td>
                      <td className="td"><MoneyInput title="Orçamento aprovado" value={p.orcamento} onChange={(v) => handleUpdateProject(p.id, "orcamento", v)} /></td>
                      <td className="td"><MoneyInput title="Comprometido" value={p.comprometido} onChange={(v) => handleUpdateProject(p.id, "comprometido", v)} /></td>
                      <td className="td"><MoneyInput title="Realizado acumulado" value={p.realizado} onChange={(v) => handleUpdateProject(p.id, "realizado", v)} /></td>
                      <td className={"td text-right tabular-nums " + (p.aRealizar < 0 ? "text-red-600 font-semibold" : "text-gray-700")} title={p.sincronizado_em ? "Saldo do dia (EBS)" : "Orçamento − (Comprometido + Realizado)"}>
                        {fmtBRL(p.aRealizar)}
                      </td>
                      <td className="td text-right tabular-nums" title="Realizado ÷ Orçamento">
                        <div className="flex items-center justify-end gap-2">
                          <div className="h-1.5 w-12 bg-gray-100 rounded overflow-hidden">
                            <div className="h-full" style={{ width: `${Math.min(100, p.pctRealizado * 100)}%`, background: p.pctRealizado > 1 ? "#ef4444" : "#2563eb" }} />
                          </div>
                          <span className={p.pctRealizado > 1 ? "text-red-600 font-semibold" : "text-gray-700"}>{fmtPct(p.pctRealizado)}</span>
                        </div>
                      </td>
                      <td className="td">
                        <input type="date" className="cell-input w-[124px]" value={p.vencimento} onChange={(e) => handleUpdateProject(p.id, "vencimento", e.target.value)} />
                      </td>
                      <td className="td text-center">
                        <span className="badge" style={{ background: st.bg, color: st.fg }}>{p.status}</span>
                      </td>
                      <td className="td">
                        <div className="flex items-center justify-center gap-1">
                          <button
                            title={p.bloqueado ? "Bloqueado para o Atualizar (EBS) — clique para liberar" : "Bloquear: não alterar no Atualizar (EBS)"}
                            onClick={() => handleUpdateProject(p.id, "bloqueado", !p.bloqueado)}
                            className={"p-1 rounded " + (p.bloqueado ? "text-amber-600 bg-amber-50 hover:bg-amber-100" : "text-gray-400 hover:text-amber-600 hover:bg-amber-50")}>
                            {p.bloqueado ? Icon.lock : Icon.lockOpen}
                          </button>
                          <button title="Duplicar" onClick={() => handleDuplicateProject(p.id)} className="p-1 rounded text-gray-500 hover:text-blue-600 hover:bg-blue-50">{Icon.copy}</button>
                          <button title="Excluir" onClick={() => handleDeleteProject(p.id)} className="p-1 rounded text-gray-500 hover:text-red-600 hover:bg-red-50">{Icon.trash}</button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              {visiveis.length > 0 && (
                <tfoot className="bg-gray-50 border-t border-gray-200 font-semibold text-gray-800">
                  <tr>
                    <td colSpan={7} className="td text-right text-gray-600">Totais ({visiveis.length})</td>
                    <td className="td text-right tabular-nums">{fmtBRL(totalOrcamento)}</td>
                    <td className="td text-right tabular-nums">{fmtBRL(totalComprometido)}</td>
                    <td className="td text-right tabular-nums">{fmtBRL(totalRealizado)}</td>
                    <td className={"td text-right tabular-nums " + (totalARealizar < 0 ? "text-red-600" : "")}>{fmtBRL(totalARealizar)}</td>
                    <td className="td text-right tabular-nums">{totalOrcamento > 0 ? fmtPct(totalRealizado / totalOrcamento) : "0,0%"}</td>
                    <td colSpan={3} className="td" />
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        </section>

        {modalCategorias && (
          <CategoriasModal categorias={categorias} emUso={usosCategoria}
                           onCriar={handleCriarCategoria} onAtualizar={handleAtualizarCategoria}
                           onExcluir={handleExcluirCategoria} onFechar={() => setModalCategorias(false)} />
        )}

        <footer className="text-[11px] text-gray-400 text-center pb-2">
          Valores dos projetos são puxados da API de CAPEX do EBS pelo número; projetos fora do EBS podem ser incluídos e editados manualmente. As edições são gravadas no banco exclusivo deste módulo (separado do /tv2).
        </footer>
      </main>
    </div>
  );
}
