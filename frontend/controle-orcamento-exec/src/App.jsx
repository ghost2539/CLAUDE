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
  home: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
      <path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h14V9.5" /><path d="M9 21v-6h6v6" />
    </svg>
  ),
  grid: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
      <rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" />
    </svg>
  ),
  list: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
      <path d="M8 6h13M8 12h13M8 18h13" /><path d="M3 6h.01M3 12h.01M3 18h.01" />
    </svg>
  ),
  report: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
      <path d="M3 3v18h18" /><rect x="7" y="10" width="3" height="7" /><rect x="12" y="6" width="3" height="11" /><rect x="17" y="13" width="3" height="4" />
    </svg>
  ),
  gear: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
      <circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  ),
  download: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
      <path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" />
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
  const [cor, setCor] = useState(categoria.cor);
  useEffect(() => setNome(categoria.nome), [categoria.nome]);
  useEffect(() => setCor(categoria.cor), [categoria.cor]);
  const salvarNome = () => {
    const v = nome.trim();
    if (!v) { setNome(categoria.nome); return; }
    if (v !== categoria.nome) onSalvar({ nome: v });
  };
  // O seletor de cor dispara onChange a cada instante enquanto arrastado; só
  // gravamos quando ele fecha (onBlur), para não estourar o limite de requisições.
  const salvarCor = () => { if (cor && cor !== categoria.cor) onSalvar({ cor }); };
  return (
    <div className="flex items-center gap-2 py-1">
      <input type="color" value={cor} title="Cor" onChange={(e) => setCor(e.target.value)} onBlur={salvarCor}
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

/* Itens do menu lateral. `view` casa com o estado que troca a tela. */
const NAV = [
  { view: "geral", label: "Visão Geral", icon: Icon.home },
  { view: "portfolio", label: "CAPEX", icon: Icon.grid },
  { view: "opex", label: "OPEX", icon: Icon.list },
  { view: "relatorios", label: "Relatórios", icon: Icon.report },
  { view: "config", label: "Configurações", icon: Icon.gear },
];

/* ── OPEX: cada país na sua moeda, sem conversão ─────────────────── */
const OPEX_MOEDA = { BR: "BRL", AR: "ARS", UY: "UYU" };
const OPEX_PAIS_NOME = { BR: "Brasil", AR: "Argentina", UY: "Uruguai" };
const OPEX_PAIS_COR = { BR: "#22c55e", AR: "#2563eb", UY: "#8b5cf6" };
const MESES3 = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
const OPEX_ALERTA = {
  acima:     { txt: "Acima do orçado", bg: "#fee2e2", fg: "#b91c1c" },
  atencao:   { txt: "Acima do ritmo",  bg: "#fef3c7", fg: "#b45309" },
  abaixo:    { txt: "Abaixo do ritmo", bg: "#e0f2fe", fg: "#075985" },
  ok:        { txt: "No ritmo",        bg: "#dcfce7", fg: "#166534" },
  sem_orcado:{ txt: "Sem orçado",      bg: "#f3f4f6", fg: "#6b7280" },
};

function fmtMoeda(v, moeda, dec = 0) {
  const n = Number(v || 0);
  try {
    return new Intl.NumberFormat("pt-BR", { style: "currency", currency: moeda || "BRL", minimumFractionDigits: dec, maximumFractionDigits: dec }).format(n);
  } catch {
    return (moeda === "BRL" ? "R$" : "$") + " " + n.toLocaleString("pt-BR", { maximumFractionDigits: dec });
  }
}

/* Célula monetária editável de um mês do OPEX. Salva ao sair do campo. */
function CelulaMes({ value, moeda, onCommit, disabled }) {
  const [foco, setFoco] = useState(false);
  const [draft, setDraft] = useState("");
  const num = Number(value || 0);
  const parse = (s) => {
    const limpo = String(s).replace(/[^\d,.-]/g, "").replace(/\.(?=\d{3}(\D|$))/g, "").replace(",", ".");
    const n = parseFloat(limpo);
    return isNaN(n) ? 0 : n;
  };
  return (
    <input
      className={"cell-input text-right tabular-nums w-[76px] " + (num ? "" : "text-gray-300")}
      disabled={disabled}
      value={foco ? draft : (num ? num.toLocaleString("pt-BR", { maximumFractionDigits: 0 }) : "")}
      placeholder="0"
      onFocus={(e) => { setFoco(true); setDraft(num ? String(num) : ""); e.target.select(); }}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => { setFoco(false); const n = parse(draft); if (n !== num) onCommit(n); }}
    />
  );
}

function OpexResumoCard({ r }) {
  const al = OPEX_ALERTA[r.alerta] || OPEX_ALERTA.ok;
  const pct = r.pct == null ? null : r.pct;
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4 relative overflow-hidden">
      <div className="absolute inset-x-0 top-0 h-1" style={{ background: OPEX_PAIS_COR[r.pais] }} />
      <div className="flex items-center justify-between">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
          {OPEX_PAIS_NOME[r.pais]} <span className="text-gray-400">· {r.moeda}</span>
        </div>
        <span className="text-[10px] font-semibold rounded-full px-2 py-0.5" style={{ background: al.bg, color: al.fg }}>{al.txt}</span>
      </div>
      <div className="mt-2 flex items-end justify-between gap-2">
        <div>
          <div className="text-[10px] text-gray-500">Realizado</div>
          <div className="text-lg font-bold text-gray-900 tabular-nums leading-tight">{fmtMoeda(r.realizado, r.moeda)}</div>
        </div>
        <div className="text-right">
          <div className="text-[10px] text-gray-500">Orçado</div>
          <div className="text-sm font-semibold text-gray-700 tabular-nums">{fmtMoeda(r.orcado, r.moeda)}</div>
        </div>
      </div>
      <div className="mt-2">
        <div className="h-2 w-full bg-gray-100 rounded overflow-hidden">
          <div className="h-full rounded" style={{ width: `${Math.min(100, (pct || 0) * 100)}%`, background: r.alerta === "acima" ? "#ef4444" : OPEX_PAIS_COR[r.pais] }} />
        </div>
        <div className="mt-1 flex justify-between text-[10px] text-gray-500 tabular-nums">
          <span>{pct == null ? "—" : (pct * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + "% do orçado"}</span>
          <span>Residual: {fmtMoeda(r.residual, r.moeda)}</span>
        </div>
      </div>
    </div>
  );
}

function OpexView({ podeEditar, onResumo }) {
  const [ano, setAno] = useState(null);
  const [dados, setDados] = useState({ itens: [], resumo: {}, anos: [] });
  const [aba, setAba] = useState("BR");
  const [serie, setSerie] = useState("orcado");   // orcado | realizado (qual série mensal editar)
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState("");
  const timers = useRef({});
  const campoSerie = serie === "orcado" ? "orcado_meses" : "realizado_meses";

  const carregar = useCallback(async (a) => {
    setCarregando(true); setErro("");
    try {
      const d = await api(API_BASE + "/opex" + (a ? "?ano=" + a : ""));
      setDados(d); setAno(d.ano);
      if (onResumo) onResumo(d.resumo);
    } catch (e) { setErro("Falha ao carregar OPEX: " + e.message); }
    finally { setCarregando(false); }
  }, [onResumo]);
  useEffect(() => { carregar(); }, [carregar]);

  const incluir = async (pais) => {
    try {
      await api(API_BASE + "/opex", { method: "POST", body: JSON.stringify({ pais, ano, bu: "", fornecedor: "" }) });
      carregar(ano);
    } catch (e) { setErro("Falha ao incluir linha: " + e.message); }
  };
  const excluir = async (id) => {
    if (!window.confirm("Excluir esta linha?")) return;
    try { await api(API_BASE + "/opex/" + id, { method: "DELETE" }); carregar(ano); }
    catch (e) { setErro("Falha ao excluir: " + e.message); }
  };
  const recalcTotal = (it) => ({
    total_orcado: Object.values(it.orcado_meses || {}).reduce((a, b) => a + Number(b || 0), 0),
    total_realizado: Object.values(it.realizado_meses || {}).reduce((a, b) => a + Number(b || 0), 0),
  });
  const patch = (id, campos, recarregar) => {
    setDados((prev) => ({ ...prev, itens: prev.itens.map((it) => {
      if (it.id !== id) return it;
      const novo = { ...it, ...campos };
      return { ...novo, ...recalcTotal(novo) };
    }) }));
    clearTimeout(timers.current[id]);
    timers.current[id] = setTimeout(async () => {
      try { await api(API_BASE + "/opex/" + id, { method: "PATCH", body: JSON.stringify(campos) }); if (recarregar) carregar(ano); }
      catch (e) { setErro("Falha ao salvar: " + e.message); }
    }, 600);
  };
  const setMes = (it, mes, valor) => {
    const meses = { ...(it[campoSerie] || {}), [String(mes)]: valor };
    patch(it.id, { [campoSerie]: meses }, true);
  };

  const itensAba = dados.itens.filter((it) => (aba === "BR" ? it.pais === "BR" : it.pais !== "BR"));
  const totalMes = (m) => itensAba.reduce((a, it) => a + Number((it[campoSerie] || {})[String(m)] || 0), 0);
  const totalGeral = itensAba.reduce((a, it) => a + Number(serie === "orcado" ? it.total_orcado : it.total_realizado || 0), 0);
  const moedaAba = aba === "BR" ? "BRL" : null;   // LATAM tem AR e UY juntos

  const inputCls = "cell-input min-w-[90px]";

  return (
    <>
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="min-w-0">
          <h1 className="text-xl font-bold leading-tight text-gray-900">OPEX — Orçado e Realizado</h1>
          <p className="text-xs text-gray-500">Brasil e Latam · cada país na sua moeda, sem conversão · orçado e realizado mês a mês</p>
        </div>
        <label className="ml-auto text-xs text-gray-600 flex items-center gap-2">
          Ano
          <div className="select-wrap">
            <select value={ano || ""} onChange={(e) => carregar(Number(e.target.value))}
                    className="appearance-none bg-white border border-gray-300 rounded-md px-2.5 py-1.5 pr-7 text-xs">
              {(dados.anos || []).map((a) => <option key={a} value={a}>{a}</option>)}
              {ano && !(dados.anos || []).includes(ano) && <option value={ano}>{ano}</option>}
            </select>
          </div>
        </label>
      </div>

      {erro && <div className="mb-3 bg-red-50 border border-red-200 text-red-800 rounded-lg px-4 py-2.5 text-xs">{erro}</div>}

      {/* Orçado × Realizado por país (moeda local, somados das linhas) */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
        {["BR", "AR", "UY"].map((p) => (
          <OpexResumoCard key={p} r={dados.resumo?.[p] || { pais: p, moeda: OPEX_MOEDA[p], orcado: 0, realizado: 0, pct: null, residual: 0, alerta: "sem_orcado" }} />
        ))}
      </section>

      {/* Sub-abas BR / LATAM + alternador Orçado / Realizado */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <div className="flex items-center gap-1">
          {[["BR", "Brasil"], ["LATAM", "Latam (AR/UY)"]].map(([v, l]) => (
            <button key={v} onClick={() => setAba(v)}
                    className={"text-xs font-medium rounded-md px-3 py-1.5 border " + (aba === v ? "bg-blue-600 text-white border-blue-600" : "bg-white text-gray-700 border-gray-300 hover:bg-gray-50")}>{l}</button>
          ))}
        </div>
        <div className="inline-flex rounded-md border border-gray-300 overflow-hidden">
          {[["orcado", "Orçado (mês a mês)"], ["realizado", "Realizado (mês a mês)"]].map(([v, l]) => (
            <button key={v} onClick={() => setSerie(v)}
                    className={"text-xs font-medium px-3 py-1.5 " + (serie === v ? (v === "orcado" ? "bg-slate-700 text-white" : "bg-emerald-600 text-white") : "bg-white text-gray-700 hover:bg-gray-50")}>{l}</button>
          ))}
        </div>
        <span className="text-[11px] text-gray-500">Editando o <b>{serie === "orcado" ? "orçado" : "realizado"}</b> de cada mês.</span>
        {podeEditar && (
          <button onClick={() => incluir(aba === "BR" ? "BR" : "AR")}
                  className="ml-auto inline-flex items-center gap-1.5 text-xs font-medium text-white bg-green-600 hover:bg-green-700 rounded-md px-3 py-1.5">
            {Icon.plus} Nova linha
          </button>
        )}
      </div>

      <section className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-x-auto">
        <table className="min-w-[1100px] w-full text-[12px] border-collapse">
          <thead className="bg-gray-50 text-gray-600 text-[11px]">
            <tr>
              {aba !== "BR" && <th className="th">País</th>}
              <th className="th text-left">BU</th>
              <th className="th text-left">Fornecedor</th>
              {aba !== "BR" && <th className="th">Conta Contábil</th>}
              <th className="th text-left">Conta Descrição</th>
              {aba === "BR" && <th className="th">Tipo</th>}
              {MESES3.map((m) => <th key={m} className={"th text-right " + (serie === "realizado" ? "text-emerald-700" : "text-slate-600")}>{m}</th>)}
              <th className="th text-right">Orçado</th>
              <th className="th text-right">Realizado</th>
              <th className="th">Ações</th>
            </tr>
          </thead>
          <tbody>
            {!carregando && itensAba.length === 0 && (
              <tr><td colSpan={aba === "BR" ? 18 : 19} className="px-4 py-8 text-center text-gray-500 text-xs">
                Nenhuma linha para {aba === "BR" ? "o Brasil" : "a Latam"} em {ano}. {podeEditar ? "Use \"Nova linha\" para incluir." : ""}
              </td></tr>
            )}
            {itensAba.map((it) => {
              const moeda = OPEX_MOEDA[it.pais] || "BRL";
              return (
                <tr key={it.id} className="border-t border-gray-100 hover:bg-blue-50/30">
                  {aba !== "BR" && (
                    <td className="td text-center">
                      <div className="select-wrap">
                        <select className="cell-input min-w-[64px]" value={it.pais} disabled={!podeEditar}
                                onChange={(e) => patch(it.id, { pais: e.target.value }, true)}>
                          <option value="AR">AR</option><option value="UY">UY</option>
                        </select>
                      </div>
                    </td>
                  )}
                  <td className="td"><input className={inputCls} disabled={!podeEditar} value={it.bu} placeholder="BU" onChange={(e) => patch(it.id, { bu: e.target.value })} /></td>
                  <td className="td"><input className={inputCls + " min-w-[150px]"} disabled={!podeEditar} value={it.fornecedor} placeholder="Fornecedor" onChange={(e) => patch(it.id, { fornecedor: e.target.value })} /></td>
                  {aba !== "BR" && <td className="td"><input className="cell-input w-[90px]" disabled={!podeEditar} value={it.conta_contabil} placeholder="Conta" onChange={(e) => patch(it.id, { conta_contabil: e.target.value })} /></td>}
                  <td className="td"><input className={inputCls + " min-w-[150px]"} disabled={!podeEditar} value={it.conta_descricao} placeholder="Descrição" onChange={(e) => patch(it.id, { conta_descricao: e.target.value })} /></td>
                  {aba === "BR" && <td className="td"><input className="cell-input min-w-[110px]" disabled={!podeEditar} value={it.tipo_despesa} placeholder="Tipo" onChange={(e) => patch(it.id, { tipo_despesa: e.target.value })} /></td>}
                  {MESES3.map((_, i) => (
                    <td key={i} className="td"><CelulaMes value={(it[campoSerie] || {})[String(i + 1)]} moeda={moeda} disabled={!podeEditar} onCommit={(v) => setMes(it, i + 1, v)} /></td>
                  ))}
                  <td className="td text-right tabular-nums text-slate-700">{fmtMoeda(it.total_orcado, moeda)}</td>
                  <td className={"td text-right tabular-nums font-semibold " + (Number(it.total_realizado) > Number(it.total_orcado) ? "text-red-600" : "text-emerald-700")}>{fmtMoeda(it.total_realizado, moeda)}</td>
                  <td className="td text-center">
                    {podeEditar && <button title="Excluir" onClick={() => excluir(it.id)} className="p-1 rounded text-gray-500 hover:text-red-600 hover:bg-red-50">{Icon.trash}</button>}
                  </td>
                </tr>
              );
            })}
          </tbody>
          {itensAba.length > 0 && (
            <tfoot className="bg-gray-50 border-t border-gray-200 font-semibold text-gray-800">
              <tr>
                <td colSpan={aba === "BR" ? 4 : 5} className="td text-right text-gray-600">
                  Totais ({itensAba.length}) · {serie === "orcado" ? "orçado" : "realizado"}
                </td>
                {MESES3.map((_, i) => <td key={i} className="td text-right tabular-nums">{totalMes(i + 1).toLocaleString("pt-BR", { maximumFractionDigits: 0 })}</td>)}
                <td className="td text-right tabular-nums">{itensAba.reduce((a, it) => a + Number(it.total_orcado || 0), 0).toLocaleString("pt-BR", { maximumFractionDigits: 0 })}</td>
                <td className="td text-right tabular-nums">{itensAba.reduce((a, it) => a + Number(it.total_realizado || 0), 0).toLocaleString("pt-BR", { maximumFractionDigits: 0 })}</td>
                <td className="td" />
              </tr>
            </tfoot>
          )}
        </table>
      </section>
      {aba !== "BR" && <p className="mt-2 text-[11px] text-gray-500">Latam soma AR e UY em moedas diferentes: os totais por mês são apenas de contagem; use os cards por país acima para o valor em cada moeda.</p>}
    </>
  );
}

function Sidebar({ view, onView, colapsado, onToggle }) {
  return (
    <aside className={"shrink-0 bg-[#0b1f3a] text-gray-300 flex flex-col transition-all " + (colapsado ? "w-16" : "w-56")}>
      <div className="h-14 flex items-center gap-2 px-4 border-b border-white/10">
        <span className="text-white shrink-0">{Icon.report}</span>
        {!colapsado && <span className="text-sm font-semibold text-white truncate">Controle de Orçamento</span>}
      </div>
      <nav className="flex-1 py-3">
        {NAV.map((n) => {
          const ativo = view === n.view;
          return (
            <button key={n.view} onClick={() => onView(n.view)} title={n.label}
              className={"w-full flex items-center gap-3 px-4 py-2.5 text-sm transition-colors " +
                (ativo ? "bg-white/10 text-white border-l-2 border-blue-400" : "text-gray-400 hover:bg-white/5 hover:text-white border-l-2 border-transparent")}>
              <span className="shrink-0">{n.icon}</span>
              {!colapsado && <span className="truncate">{n.label}</span>}
            </button>
          );
        })}
      </nav>
      <div className="p-3 border-t border-white/10 space-y-1">
        <a href="/" title="Voltar ao Portal"
           className="w-full flex items-center gap-3 px-1 py-2 text-xs text-gray-400 hover:text-white">
          <span className="shrink-0">{Icon.back}</span>{!colapsado && <span>Portal</span>}
        </a>
        <button onClick={onToggle} title={colapsado ? "Expandir" : "Recolher"}
          className="w-full flex items-center gap-3 px-1 py-2 text-xs text-gray-400 hover:text-white">
          <span className="shrink-0 inline-block w-5 text-center">{colapsado ? "»" : "«"}</span>
          {!colapsado && <span>Recolher</span>}
        </button>
      </div>
    </aside>
  );
}

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
  const [view, setView] = useState("geral");        // seção do menu lateral
  const [colapsado, setColapsado] = useState(false);
  const [adminModulo, setAdminModulo] = useState(false);  // pode liberar acessos deste módulo
  const [nivelAcesso, setNivelAcesso] = useState("");     // view | edit | admin
  const [opexResumo, setOpexResumo] = useState(null);     // resumo OPEX para a Visão Geral
  const [permissoes, setPermissoes] = useState([]);       // liberações próprias do módulo
  const [novoAc, setNovoAc] = useState({ login: "", nome: "", nivel: "view" });
  const [acBusy, setAcBusy] = useState(false);
  const [acMsg, setAcMsg] = useState("");

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
    api(API_BASE + "/sessao").then((d) => {
      if (d.usuario) setUser(d.usuario);
      setAdminModulo(!!d.admin_modulo);
      setNivelAcesso(d.nivel || "");
    }).catch(() => {});
    // Resumo do OPEX para a Visão Geral (ano corrente por padrão).
    api(API_BASE + "/opex").then((d) => setOpexResumo(d.resumo)).catch(() => {});
  }, []);
  const podeEditar = nivelAcesso === "edit" || nivelAcesso === "admin" || adminModulo;

  /* ── Acessos próprios do módulo (só o admin do módulo gerencia) ── */
  const carregarPermissoes = useCallback(async () => {
    try {
      const d = await api(API_BASE + "/permissoes");
      setPermissoes(d.permissoes || []);
    } catch { /* sem permissão: ignora */ }
  }, []);
  useEffect(() => {
    if (adminModulo && view === "config") carregarPermissoes();
  }, [adminModulo, view, carregarPermissoes]);

  const salvarAcesso = async (login, nivel, nome) => {
    setAcBusy(true); setAcMsg("");
    try {
      await api(API_BASE + "/permissoes", { method: "POST", body: JSON.stringify({ login, nivel, nome }) });
      setNovoAc({ login: "", nome: "", nivel: "view" });
      await carregarPermissoes();
      setAcMsg("Acesso liberado.");
    } catch (e) {
      setAcMsg("Falha: " + e.message);
    } finally {
      setAcBusy(false);
    }
  };
  const removerAcesso = async (login) => {
    if (!window.confirm(`Revogar o acesso de "${login}" a este módulo?`)) return;
    setAcBusy(true); setAcMsg("");
    try {
      await api(API_BASE + "/permissoes/" + encodeURIComponent(login), { method: "DELETE" });
      await carregarPermissoes();
    } catch (e) {
      setAcMsg("Falha: " + e.message);
    } finally {
      setAcBusy(false);
    }
  };

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

  /* Situação do Orçamento: as três parcelas que compõem o orçado. */
  const situacao = [
    { name: "Realizado (Acum.)", value: totalRealizado },
    { name: "Comprometido", value: totalComprometido },
    { name: "Em Andamento", value: Math.max(0, totalARealizar) },
  ];
  const SITUACAO_CORES = {
    "Realizado (Acum.)": "#22c55e", "Comprometido": "#f97316", "Em Andamento": "#eab308",
  };

  /* Prazo por projeto: fora do prazo = vencido e ainda não concluído. */
  const hoje = todayISO();
  const foraDoPrazo = (p) => p.vencimento && p.vencimento < hoje && p.estagio !== "Concluído";
  const nFora = visiveis.filter(foraDoPrazo).length;
  const nDentro = visiveis.length - nFora;
  const prazo = [
    { name: "Dentro do prazo", value: nDentro },
    { name: "Fora do prazo", value: nFora },
  ];
  const PRAZO_CORES = { "Dentro do prazo": "#22c55e", "Fora do prazo": "#ef4444" };

  /* Exporta o que está visível (respeita os filtros) em CSV para análise. */
  const exportarCSV = () => {
    const cols = [
      ["ID", "codigo"], ["Projeto/Demanda", "nome"], ["Tipo", "tipo"], ["Categoria", "categoria"],
      ["Área", "area"], ["Estágio", "estagio"], ["Prioridade", "prioridade"],
      ["Orçamento Aprovado", "orcamento"], ["Comprometido", "comprometido"],
      ["Realizado (Acum.)", "realizado"], ["A Realizar", "aRealizar"],
      ["Vencimento", "vencimento"], ["Status", "status"], ["Bloqueado", "bloqueado"],
    ];
    const esc = (v) => {
      const s = v === true ? "Sim" : v === false ? "Não" : String(v == null ? "" : v);
      return /[";\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    const linhas = [cols.map((c) => c[0]).join(";")];
    visiveis.forEach((p) => linhas.push(cols.map((c) => esc(p[c[1]])).join(";")));
    const blob = new Blob(["﻿" + linhas.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "controle-orcamento.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  /* ── Blocos reutilizados entre as telas ───────────────────────── */
  const cabecalho = (titulo, subtitulo) => (
    <div className="flex flex-wrap items-center gap-3 mb-4">
      <div className="min-w-0">
        <h1 className="text-xl font-bold leading-tight text-gray-900">{titulo}</h1>
        {subtitulo && <p className="text-xs text-gray-500">{subtitulo}</p>}
      </div>
      <div className="ml-auto flex items-center gap-3">
        <span className={"inline-flex items-center gap-1.5 text-[11px] " + (erro ? "text-red-600" : pendentes > 0 ? "text-amber-600" : "text-gray-500")} title="Gravação automática no banco do módulo">
          {Icon.cloud}
          {erro ? "Erro ao salvar" : pendentes > 0 ? "Salvando…" : ultimoSalvo ? `Salvo ${ultimoSalvo.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}` : "Gravação automática"}
        </span>
        {user && <span className="text-xs text-gray-600 hidden sm:inline">{user.display_name || user.username}</span>}
        <button onClick={handleRecarregar} disabled={carregando} title="Recarregar os dados"
                className="inline-flex items-center gap-1.5 text-xs text-gray-700 border border-gray-300 rounded-md px-2.5 py-1.5 bg-white hover:bg-gray-50 disabled:opacity-50">
          {Icon.refresh} Atualizar tela
        </button>
        <button onClick={handleSincronizar} disabled={carregando || sincBusy} title="Puxa os valores de todos os projetos da API de CAPEX do EBS"
                className="inline-flex items-center gap-1.5 text-xs text-white bg-blue-600 hover:bg-blue-700 rounded-md px-2.5 py-1.5 disabled:opacity-50">
          {Icon.refresh} {sincBusy ? "Atualizando…" : "Atualizar (EBS)"}
        </button>
      </div>
    </div>
  );

  const barraInclusao = (
    <section className="bg-white rounded-lg border border-gray-200 shadow-sm p-3">
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
      {incMsg && <div className="mt-1.5 text-[11px] text-gray-600">{incMsg}</div>}
    </section>
  );

  const kpis = (
    <section className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
      <KpiCard icon={Icon.doc} color="#64748b" label="Demandas" value={totalDemandas} sub={`${emExecucao} em execução`} />
      <KpiCard icon={Icon.dollar} color="#22c55e" label="Valor Total" value={fmtBRL(totalOrcamento)} sub="Orçamento aprovado" />
      <KpiCard icon={Icon.pie} color="#2563eb" label="CAPEX Aprovado" value={fmtBRL(totalCapex)} sub={pct(totalCapex).replace("do orçamento total", "do valor total")} />
      <KpiCard icon={Icon.trend} color="#8b5cf6" label="Realizado (Acum.)" value={fmtBRL(totalRealizado)} sub={pct(totalRealizado)} />
      <KpiCard icon={Icon.clipboard} color="#f97316" label="Comprometido" value={fmtBRL(totalComprometido)} sub={pct(totalComprometido)} />
      <KpiCard icon={Icon.target} color="#06b6d4" label="Em Andamento" value={fmtBRL(totalARealizar)} sub={pct(totalARealizar)} />
    </section>
  );

  const filtrosBar = (
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
  );

  const tabela = (
    <section className="bg-white rounded-lg border border-gray-200 shadow-sm">
      <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-gray-200">
        <h2 className="text-sm font-semibold text-gray-800">Portfólio de Projetos</h2>
        <span className="text-[11px] text-gray-500">
          {visiveis.length} de {projects.length} projeto(s){filtrosAtivos ? " · filtros ativos" : ""} · clique em uma célula para editar
        </span>
        <button onClick={exportarCSV} disabled={carregando || !visiveis.length}
                className="ml-auto disabled:opacity-50 inline-flex items-center gap-1.5 text-xs font-medium text-gray-700 bg-white border border-gray-300 hover:bg-gray-50 rounded-md px-3 py-1.5">
          {Icon.download} Exportar
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
  );

  const analiseCharts = (
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
  );

  /* ── Render ───────────────────────────────────────────────────── */
  return (
    <div className="min-h-screen flex bg-gray-100 text-gray-900 font-sans">
      <Sidebar view={view} onView={setView} colapsado={colapsado} onToggle={() => setColapsado((c) => !c)} />

      <main className="flex-1 min-w-0 px-4 py-4 space-y-4 overflow-x-hidden">
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

        {/* ── Visão Geral ─────────────────────────────────────────── */}
        {view === "geral" && (
          <>
            {cabecalho("Visão Geral do Portfólio", "Cards, situação do orçamento e prazo dos projetos")}
            {kpis}
            <section className="grid grid-cols-1 lg:grid-cols-2 gap-3">
              <ChartCard title="Situação do Orçamento CAPEX (R$)" footer={`Total Orçado: ${fmtBRL(totalOrcamento)}`}>
                <DonutChart data={situacao} colors={SITUACAO_CORES} />
              </ChartCard>
              <ChartCard title="Prazo por Projeto (CAPEX)" footer={`Total: ${visiveis.length} projeto(s)`}>
                <DonutChart data={prazo} colors={PRAZO_CORES} />
              </ChartCard>
            </section>

            {/* OPEX — orçado × realizado por país (moeda local, sem conversão) */}
            <div className="flex items-center gap-2 mt-2">
              <h2 className="text-sm font-semibold text-gray-700">OPEX — Orçado × Realizado</h2>
              <span className="text-[11px] text-gray-500">cada país na sua moeda</span>
              <button onClick={() => setView("opex")} className="ml-auto text-[11px] text-blue-600 hover:underline">abrir OPEX →</button>
            </div>
            {opexResumo ? (
              <section className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {["BR", "AR", "UY"].map((p) => (
                  <OpexResumoCard key={p} r={opexResumo[p] || { pais: p, moeda: OPEX_MOEDA[p], orcado: 0, realizado: 0, pct: null, residual: 0, alerta: "sem_orcado" }} />
                ))}
              </section>
            ) : (
              <div className="text-xs text-gray-500 bg-white border border-gray-200 rounded-lg px-4 py-2.5">Carregando OPEX…</div>
            )}
            {opexResumo && ["BR", "AR", "UY"].some((p) => (opexResumo[p] || {}).alerta === "acima") && (
              <div className="bg-red-50 border border-red-200 text-red-800 rounded-lg px-4 py-2.5 text-xs">
                <b>Atenção:</b> há país com OPEX realizado acima do orçado do ano:{" "}
                {["BR", "AR", "UY"].filter((p) => (opexResumo[p] || {}).alerta === "acima").map((p) => OPEX_PAIS_NOME[p]).join(", ")}.
              </div>
            )}
          </>
        )}

        {/* ── OPEX ────────────────────────────────────────────────── */}
        {view === "opex" && (
          <OpexView podeEditar={podeEditar} onResumo={setOpexResumo} />
        )}

        {/* ── Portfólio (CAPEX) ───────────────────────────────────── */}
        {view === "portfolio" && (
          <>
            {cabecalho("Portfólio de Projetos", "Cards, gráficos e a tabela; a trava impede alteração pelo Atualizar (EBS)")}
            {kpis}
            <section className="grid grid-cols-1 lg:grid-cols-2 gap-3">
              <ChartCard title="Situação do Orçamento (R$)" footer={`Total Orçado: ${fmtBRL(totalOrcamento)}`}>
                <DonutChart data={situacao} colors={SITUACAO_CORES} />
              </ChartCard>
              <ChartCard title="Prazo por Projeto" footer={`Total: ${visiveis.length} projeto(s)`}>
                <DonutChart data={prazo} colors={PRAZO_CORES} />
              </ChartCard>
            </section>
            {barraInclusao}
            {filtrosBar}
            {tabela}
          </>
        )}

        {/* ── Relatórios ──────────────────────────────────────────── */}
        {view === "relatorios" && (
          <>
            {cabecalho("Relatórios", "Exporte os dados e veja a composição do portfólio")}
            <section className="bg-white rounded-lg border border-gray-200 shadow-sm p-4 flex flex-wrap items-center gap-3">
              <div className="min-w-0">
                <div className="text-sm font-semibold text-gray-800">Exportar dados</div>
                <div className="text-[11px] text-gray-500">{visiveis.length} projeto(s) conforme os filtros atuais.</div>
              </div>
              <button onClick={exportarCSV} disabled={!visiveis.length}
                      className="ml-auto inline-flex items-center gap-1.5 text-xs font-medium text-white bg-green-600 hover:bg-green-700 rounded-md px-3 py-1.5 disabled:opacity-50">
                {Icon.download} Exportar CSV
              </button>
              <button onClick={() => window.print()}
                      className="inline-flex items-center gap-1.5 text-xs font-medium text-gray-700 bg-white border border-gray-300 hover:bg-gray-50 rounded-md px-3 py-1.5">
                {Icon.report} Imprimir / PDF
              </button>
            </section>
            {filtrosBar}
            {analiseCharts}
          </>
        )}

        {/* ── Configurações ───────────────────────────────────────── */}
        {view === "config" && (
          <>
            {cabecalho("Configurações do módulo", "Acessos, categorias e informações do módulo")}

            {/* Controle de acesso PRÓPRIO do módulo — só o admin do módulo vê */}
            {adminModulo ? (
              <section className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
                <div className="flex items-center gap-2 mb-3">
                  <h2 className="text-sm font-semibold text-gray-800">Acesso ao módulo</h2>
                  <span className="text-[11px] text-gray-500">Informe o usuário de rede no formato 001+LOGIN.</span>
                </div>
                <div className="flex flex-wrap items-end gap-2 mb-3">
                  <label className="block">
                    <span className="block text-[10px] font-semibold uppercase tracking-wide text-gray-500 mb-0.5">Login (rede)</span>
                    <input value={novoAc.login} onChange={(e) => setNovoAc({ ...novoAc, login: e.target.value })}
                           placeholder="ex.: 001200660"
                           className="w-[150px] border border-gray-300 rounded-md px-2.5 py-1.5 text-xs focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100" />
                  </label>
                  <label className="block">
                    <span className="block text-[10px] font-semibold uppercase tracking-wide text-gray-500 mb-0.5">Nome (opcional)</span>
                    <input value={novoAc.nome} onChange={(e) => setNovoAc({ ...novoAc, nome: e.target.value })}
                           placeholder="Nome do colaborador"
                           className="w-[190px] border border-gray-300 rounded-md px-2.5 py-1.5 text-xs focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100" />
                  </label>
                  <label className="block">
                    <span className="block text-[10px] font-semibold uppercase tracking-wide text-gray-500 mb-0.5">Nível</span>
                    <div className="select-wrap">
                      <select value={novoAc.nivel} onChange={(e) => setNovoAc({ ...novoAc, nivel: e.target.value })}
                              className="w-[130px] appearance-none bg-white border border-gray-300 rounded-md px-2.5 py-1.5 pr-7 text-xs focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100">
                        <option value="view">Somente ver</option>
                        <option value="edit">Ver e editar</option>
                        <option value="admin">Administrador</option>
                      </select>
                    </div>
                  </label>
                  <button onClick={() => salvarAcesso(novoAc.login.trim(), novoAc.nivel, novoAc.nome.trim())}
                          disabled={acBusy || !novoAc.login.trim()}
                          className="inline-flex items-center gap-1.5 text-xs font-medium text-white bg-green-600 hover:bg-green-700 rounded-md px-3 py-1.5 disabled:opacity-50">
                    {Icon.plus} Liberar acesso
                  </button>
                  {acMsg && <span className="text-[11px] text-gray-600">{acMsg}</span>}
                </div>
                <div className="overflow-x-auto">
                  <table className="min-w-[520px] w-full text-[12px] border-collapse">
                    <thead className="bg-gray-50 text-gray-600 text-[11px]">
                      <tr><th className="th text-left">Login</th><th className="th text-left">Nome</th><th className="th">Nível</th><th className="th text-left">Liberado por</th><th className="th">Ações</th></tr>
                    </thead>
                    <tbody>
                      {permissoes.length === 0 && (
                        <tr><td colSpan={5} className="px-4 py-6 text-center text-gray-500 text-xs">
                          Nenhum acesso liberado. O admin do portal e quem já tinha permissão do portal continuam com acesso.
                        </td></tr>
                      )}
                      {permissoes.map((p) => (
                        <tr key={p.login} className="border-t border-gray-100">
                          <td className="td font-medium text-gray-700">{p.login}</td>
                          <td className="td">{p.nome || "—"}</td>
                          <td className="td text-center">
                            <div className="select-wrap inline-block">
                              <select value={p.nivel} onChange={(e) => salvarAcesso(p.login, e.target.value, p.nome)}
                                      className="cell-input min-w-[120px]">
                                <option value="view">Somente ver</option>
                                <option value="edit">Ver e editar</option>
                                <option value="admin">Administrador</option>
                              </select>
                            </div>
                          </td>
                          <td className="td text-gray-500">{p.criado_por || "—"}</td>
                          <td className="td text-center">
                            <button title="Revogar" onClick={() => removerAcesso(p.login)} className="p-1 rounded text-gray-500 hover:text-red-600 hover:bg-red-50">{Icon.trash}</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ) : (
              <section className="bg-white rounded-lg border border-gray-200 shadow-sm p-4 text-[11px] text-gray-500">
                A liberação de acessos deste módulo é feita pelo administrador do módulo (definido em Parâmetros).
              </section>
            )}

            <section className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
              <div className="flex items-center gap-2 mb-3">
                <h2 className="text-sm font-semibold text-gray-800">Categorias</h2>
                <span className="text-[11px] text-gray-500">Usadas na classificação e nos gráficos.</span>
                <button onClick={() => setModalCategorias(true)}
                        className="ml-auto inline-flex items-center gap-1.5 text-xs font-medium text-gray-700 bg-white border border-gray-300 hover:bg-gray-50 rounded-md px-3 py-1.5">
                  {Icon.tag} Gerenciar categorias
                </button>
              </div>
              <div className="flex flex-wrap gap-2">
                {nomesCategoria.length === 0 && <span className="text-xs text-gray-500">Nenhuma categoria cadastrada.</span>}
                {nomesCategoria.map((n) => (
                  <span key={n} className="inline-flex items-center gap-1.5 text-xs border border-gray-200 rounded-full px-2.5 py-1 bg-gray-50">
                    <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: coresCategoria[n] }} />
                    {n}<span className="text-gray-400">· {usosCategoria[n] || 0}</span>
                  </span>
                ))}
              </div>
            </section>
            <section className="bg-white rounded-lg border border-gray-200 shadow-sm p-4 text-[11px] text-gray-500 leading-relaxed">
              Os valores dos projetos são puxados da API de CAPEX do EBS pelo número. Projetos fora do EBS
              podem ser incluídos e editados manualmente. Projetos travados não são alterados pelo Atualizar (EBS).
              As edições são gravadas no banco exclusivo deste módulo.
            </section>
          </>
        )}

        {modalCategorias && (
          <CategoriasModal categorias={categorias} emUso={usosCategoria}
                           onCriar={handleCriarCategoria} onAtualizar={handleAtualizarCategoria}
                           onExcluir={handleExcluirCategoria} onFechar={() => setModalCategorias(false)} />
        )}
      </main>
    </div>
  );
}
