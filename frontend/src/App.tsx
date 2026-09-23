import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ChartLineUp, ClipboardText, Database, SquaresFour } from '@phosphor-icons/react';
import { ApiError, type DatasetFiles } from './api/ProcurementApi';
import { getApi } from './api/client';
import type { FileRole, Meta, OrderLine, RunParams, Supplier, SupplierSummary, Urgency } from './api/types';
import { SkuPanel } from './features/SkuPanel';
import { saveBlob } from './shared/download';
import { formatDate, formatMoney, formatNumber, formatQty } from './shared/format';
import { isValidQuantity } from './shared/quantity';
import { Button, Dialog, Field, InlineAlert, KpiMetric, OrderStatus, SectionPanel, Select, Skeleton, StatusBadge, Tooltip } from './shared/ui';
import styles from './App.module.css';

const initialParams: RunParams = { dataset_id: null, supplier: null, category: null, method: 'analyze', lead_time_days: null, review_period_days: null, service_level: null, growth_pct: 0 };
const urgencyOrder: Record<Urgency, number> = { critical: 0, high: 1, planned: 2, none: 3 };
const requiredRoles: FileRole[] = ['monthly_sales', 'monthly_stock', 'in_transit', 'moq'];
const allRoles: FileRole[] = [...requiredRoles, 'sales_tx', 'seasonality'];
const roleLabels: Record<FileRole, string> = { monthly_sales: 'Продажи по месяцам', monthly_stock: 'Остатки по месяцам', sales_tx: 'Строки продаж', in_transit: 'Товар в пути', moq: 'Кратность заказа', seasonality: 'Сезонность' };
const HistoryChart = lazy(() => import('./features/historyChart'));
const RiskChart = lazy(() => import('./features/RunCharts').then((module) => ({ default: module.RiskChart })));
const SupplierChart = lazy(() => import('./features/RunCharts').then((module) => ({ default: module.SupplierChart })));
const ComparisonChart = lazy(() => import('./features/RunCharts').then((module) => ({ default: module.ComparisonChart })));
const BacktestPanel = lazy(() => import('./features/BacktestPanel').then((module) => ({ default: module.BacktestPanel })));
const ScenarioPanel = lazy(() => import('./features/ScenarioPanel'));
type View = 'overview' | 'orders' | 'analytics' | 'data';
const viewItems: { id: View; label: string; shortLabel: string }[] = [
  { id: 'overview', label: 'Обзор', shortLabel: 'Обзор' },
  { id: 'orders', label: 'Заказы', shortLabel: 'Заказы' },
  { id: 'analytics', label: 'Аналитика', shortLabel: 'Аналитика' },
  { id: 'data', label: 'Данные и расчёт', shortLabel: 'Данные' },
];
const viewIcons = { overview: SquaresFour, orders: ClipboardText, analytics: ChartLineUp, data: Database };
function readView(): View {
  const value = window.location.pathname.slice(1).split('/')[0];
  return viewItems.find((item) => item.id === value)?.id ?? 'overview';
}

function Parameters({ meta, params, setParams, onCalculate, calculating, onOpenUpload }: { meta: Meta | undefined; params: RunParams; setParams: (params: RunParams) => void; onCalculate: () => void; calculating: boolean; onOpenUpload: () => void }) {
  const supplierInfo = meta?.suppliers.find((item) => item.supplier === params.supplier);
  return <div className={styles.parameterPanel} aria-label="Параметры расчёта">
    <div className={styles.parameterGrid}>
      <Select id="supplier" label="Поставщик" value={params.supplier ?? ''} onChange={(event) => setParams({ ...params, supplier: (event.target.value || null) as Supplier | null, dataset_id: null, category: params.category && meta?.product_groups.includes(params.category) ? params.category : null, lead_time_days: null, review_period_days: null })}><option value="">Все поставщики</option>{meta?.suppliers.map((item) => <option key={item.supplier} value={item.supplier}>{item.supplier_name}</option>)}</Select>
      <Select id="category" label="Категория" value={params.category ?? ''} onChange={(event) => setParams({ ...params, category: event.target.value || null })}><option value="">Все категории</option>{(meta?.product_groups.length ?? 0) > 0 && <optgroup label="Товарные группы">{meta?.product_groups.map((group) => <option key={group} value={group}>{group}</option>)}</optgroup>}{supplierInfo && <optgroup label={`Класс ${supplierInfo.supplier_name}`}>{supplierInfo.categories.map((category) => <option key={category} value={category}>{category}</option>)}</optgroup>}</Select>
      <Select id="method" label="Метод" value={params.method} onChange={(event) => setParams({ ...params, method: event.target.value as RunParams['method'] })}><option value="analyze">Наш расчёт</option><option value="baseline">Excel-метод</option></Select>
      <Field id="lead" label="Срок поставки L, дни" type="number" min={1} max={365} placeholder={supplierInfo ? `По умолчанию: ${supplierInfo.lead_time_days}` : 'По поставщику'} value={params.lead_time_days ?? ''} onChange={(event) => setParams({ ...params, lead_time_days: event.target.value ? Number(event.target.value) : null })} />
      <Field id="review" label="Период R, дни" type="number" min={1} max={365} placeholder={supplierInfo ? `По умолчанию: ${supplierInfo.review_period_days}` : 'По поставщику'} value={params.review_period_days ?? ''} onChange={(event) => setParams({ ...params, review_period_days: event.target.value ? Number(event.target.value) : null })} />
      <Field id="growth" label="Прирост, %" type="number" min={-90} max={500} value={params.growth_pct} onChange={(event) => setParams({ ...params, growth_pct: Number(event.target.value) })} />
      <Button variant="primary" type="button" loading={calculating} onClick={onCalculate}>Рассчитать</Button>
    </div>
    <details className={styles.more}><summary>Доп. параметры</summary><div className={styles.advancedGrid}>
      <Field id="service" label="Уровень сервиса, %" type="number" min={51} max={99} step="0.1" placeholder="По категории" value={params.service_level === null ? '' : params.service_level * 100} onChange={(event) => setParams({ ...params, service_level: event.target.value ? Number(event.target.value) / 100 : null })} />
    </div><div className={styles.uploadLink}><Button type="button" onClick={onOpenUpload}>Загрузить свои выгрузки</Button>{params.dataset_id && <span>Набор данных: {params.dataset_id}</span>}</div></details>
  </div>;
}

function QuantityEditor({ line, approved, onSave }: { line: OrderLine; approved: boolean; onSave: (line: OrderLine, quantity: number) => Promise<void> }) {
  const [draft, setDraft] = useState(String(line.final_qty));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const changed = Number(draft) !== line.final_qty;
  async function submit() {
    const value = Number(draft);
    if (draft.trim() === '' || !isValidQuantity(value, line.moq)) {
      const lower = Math.max(0, Math.floor(value / line.moq) * line.moq);
      setError(Number.isFinite(value) && value >= 0 ? `Нужно кратно ${formatNumber(line.moq)}: ближайшие ${formatNumber(lower)} или ${formatNumber(lower + line.moq)}` : 'Количество не может быть отрицательным');
      return;
    }
    setBusy(true); setError('');
    try { await onSave(line, value); } catch (cause) { if (cause instanceof ApiError && cause.status === 409) setDraft(String(line.final_qty)); setError(cause instanceof Error ? cause.message : 'Не удалось сохранить количество'); } finally { setBusy(false); }
  }
  return <div className={styles.editor}><div className={styles.editorControls}><input aria-label={`Итоговое количество для ${line.name}`} type="number" min="0" step={line.moq} value={draft} disabled={approved || busy} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void submit(); if (event.key === 'Escape') { setDraft(String(line.final_qty)); setError(''); } }} />{changed && !approved && <Button type="button" disabled={busy} loading={busy} onClick={() => void submit()}>Сохранить</Button>}</div>{error ? <small role="alert" className={styles.cellError}>{error}</small> : !changed && line.final_qty !== line.recommended_qty && <small className={styles.edited}>изменено вручную</small>}</div>;
}

function OrderCard({ line, approved, selected, onSelect, onSave }: { line: OrderLine; approved: boolean; selected: boolean; onSelect: (line: OrderLine) => void; onSave: (line: OrderLine, quantity: number) => Promise<void> }) {
  return <article className={`${styles.card} ${selected ? styles.selected : ''}`}><span className={styles.sku}>{line.sku}{line.article ? ` · ${line.article}` : ''}</span><button className={styles.productButton} type="button" onClick={() => onSelect(line)}>{line.name}</button><div className={styles.cardFacts}><div><span>Остаток</span><strong>{line.stock_source === 'estimate_lower_bound' ? '≈' : ''}{formatQty(line.stock_free, line.unit)}</strong></div><div><span>В пути</span><strong>{formatQty(line.in_transit, line.unit)}</strong></div><div><span>Прогноз/мес</span><strong>{formatQty(line.forecast_monthly, line.unit)}</strong></div><div><span>Наш расчёт</span><strong>{formatQty(line.recommended_qty, line.unit)}</strong></div><div><span>Excel-метод</span><strong className={line.baseline_qty === 0 && line.recommended_qty > 0 ? styles.compare : ''}>{formatQty(line.baseline_qty, line.unit)}</strong></div><div><span>Кратность</span><strong>{formatQty(line.moq, line.unit)}</strong></div><div><span>Срочность</span><StatusBadge urgency={line.urgency} /></div></div><QuantityEditor key={`${line.line_id}:${line.final_qty}`} line={line} approved={approved} onSave={onSave} /><p className={styles.cardExplanation}>{line.explanation}</p><Button type="button" variant="ghost" onClick={() => onSelect(line)}>Почему столько</Button></article>;
}

// lines — видимые по фильтрам, supplierLines — все строки поставщика: окно утверждения считает по всему заказу
function SupplierOrders({ summary, lines, supplierLines, selectedId, onSelect, onSave, onApprove, onExport, onSummary }: { summary: SupplierSummary; lines: OrderLine[]; supplierLines: OrderLine[]; selectedId: string | null; onSelect: (line: OrderLine) => void; onSave: (line: OrderLine, quantity: number) => Promise<void>; onApprove: (supplier: Supplier) => Promise<void>; onExport: (supplier: Supplier) => Promise<void>; onSummary: (supplier: Supplier) => Promise<void> }) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const [page, setPage] = useState(0);
  const pageSize = 50;
  const pageCount = Math.max(1, Math.ceil(lines.length / pageSize));
  const currentPage = Math.min(page, pageCount - 1);
  const pageLines = lines.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
  async function action(work: () => Promise<void>) { setBusy(true); setActionError(''); try { await work(); } catch (cause) { setActionError(cause instanceof Error ? cause.message : 'Действие не выполнено'); } finally { setBusy(false); } }
  return <section className={styles.supplier} aria-labelledby={`supplier-${summary.supplier}`}><header className={styles.supplierHeader}><div className={styles.supplierTitle}><h3 id={`supplier-${summary.supplier}`}>{summary.supplier_name}</h3><OrderStatus status={summary.status} /></div><div className={styles.supplierActions}><Button type="button" disabled={summary.status === 'approved' || busy} onClick={() => setConfirm(true)}>Утвердить</Button><Button type="button" disabled={busy || import.meta.env.VITE_MOCK === '1'} title={import.meta.env.VITE_MOCK === '1' ? 'Экспорт доступен в рабочем режиме' : undefined} onClick={() => void action(() => onExport(summary.supplier))}>Excel для 1С</Button><Button type="button" variant="ghost" disabled={busy} onClick={() => void action(() => onSummary(summary.supplier))}>Сводка</Button></div></header><p className={styles.supplierMeta}>{formatNumber(summary.lines_count)} поз. · {summary.total_amount === null ? 'Сумма: нет цены' : `Оценочная сумма: ${formatMoney(summary.total_amount)}`}{summary.approved_at ? ` · Утверждён ${formatDate(summary.approved_at)}` : ''}</p>{actionError && <InlineAlert tone="error">{actionError}</InlineAlert>}
    <div className={styles.tableScroll} role="region" aria-label={`Таблица заказов ${summary.supplier_name}`} tabIndex={0}>
      <table>
        <thead><tr><th>Товар</th><th>Срочность</th><th>Наш расчёт</th><th>Excel</th><th>Итог</th><th>Остаток</th><th>В пути</th><th>Прогноз/мес</th><th>Кратность</th></tr></thead>
        <tbody>{pageLines.map((line) => <tr key={line.line_id} className={selectedId === line.line_id ? styles.selected : ''}>
          <td className={styles.productCell}>
            <span className={styles.sku}>{line.sku}{line.article ? ` · ${line.article}` : ''}</span>
            <button className={styles.productButton} type="button" onClick={() => onSelect(line)}>{line.name}</button>
            <span className={styles.explanation} title={line.explanation}>{line.explanation}</span>
          </td>
          <td><StatusBadge urgency={line.urgency} /></td>
          <td className={styles.number}>{formatQty(line.recommended_qty, line.unit)}</td>
          <td className={`${styles.number} ${line.baseline_qty === 0 && line.recommended_qty > 0 ? styles.compare : ''}`}>{formatQty(line.baseline_qty, line.unit)}</td>
          <td><QuantityEditor key={`${line.line_id}:${line.final_qty}`} line={line} approved={summary.status === 'approved'} onSave={onSave} /></td>
          <td className={styles.number}>{line.stock_source === 'estimate_lower_bound' ? <Tooltip text="Нижняя оценка остатка">≈</Tooltip> : null}{formatQty(line.stock_free, line.unit)}</td>
          <td className={styles.number}>{formatQty(line.in_transit, line.unit)}</td>
          <td className={styles.number}>{formatQty(line.forecast_monthly, line.unit)}</td>
          <td className={styles.number}>{formatQty(line.moq, line.unit)}</td>
        </tr>)}</tbody>
      </table>
    </div>
    <div className={styles.cards}>{pageLines.map((line) => <OrderCard key={line.line_id} line={line} approved={summary.status === 'approved'} selected={selectedId === line.line_id} onSelect={onSelect} onSave={onSave} />)}</div>
    {pageCount > 1 && <nav className={styles.pagination} aria-label={`Страницы заказов ${summary.supplier_name}`}><span>Показаны {formatNumber(currentPage * pageSize + 1)}–{formatNumber(currentPage * pageSize + pageLines.length)} из {formatNumber(lines.length)}</span><div><Button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Назад</Button><span>Страница {formatNumber(currentPage + 1)} из {formatNumber(pageCount)}</span><Button type="button" disabled={currentPage >= pageCount - 1} onClick={() => setPage(currentPage + 1)}>Далее</Button></div></nav>}
    <Dialog open={confirm} onOpenChange={setConfirm} title={`Утвердить заказ ${summary.supplier_name}?`}><p>{formatNumber(summary.lines_count)} поз. · {formatNumber(Math.round(summary.total_qty))} ед.{summary.total_amount === null ? '' : ` · ${formatMoney(summary.total_amount)}`} · изменено вручную: {formatNumber(supplierLines.filter((line) => line.final_qty !== line.recommended_qty).length)}</p>{supplierLines.some((line) => line.urgency === 'critical' && line.flags.includes('approx_stock')) && <InlineAlert tone="warning">Часть срочных позиций рассчитана по оценочному остатку — сверьте фактический остаток в 1С.</InlineAlert>}<p>После утверждения количества этого поставщика нельзя будет изменить. Заказ поставщику автоматически не отправляется.</p><div className={styles.dialogActions}><Button type="button" onClick={() => setConfirm(false)}>Отмена</Button><Button type="button" variant="primary" loading={busy} onClick={() => void action(async () => { await onApprove(summary.supplier); setConfirm(false); })}>Утвердить</Button></div></Dialog>
  </section>;
}

function UploadDialog({ open, onOpenChange, onUploaded }: { open: boolean; onOpenChange: (open: boolean) => void; onUploaded: (datasetId: string, supplier: Supplier, warnings: string[]) => void }) {
  const [supplier, setSupplier] = useState<Supplier>('IEK');
  const [files, setFiles] = useState<DatasetFiles>({});
  const [error, setError] = useState('');
  const [fieldError, setFieldError] = useState<Partial<Record<FileRole, string>>>({});
  const [busy, setBusy] = useState(false);
  async function submit() {
    for (const role of requiredRoles) if (!files[role]) { setError(`Выберите файл: ${roleLabels[role]}`); return; }
    for (const file of Object.values(files)) if (file && (!file.name.toLowerCase().endsWith('.xlsx') || file.size === 0 || file.size > 30 * 1024 * 1024)) { setError('Нужны непустые .xlsx-файлы размером до 30 МБ каждый'); return; }
    setBusy(true); setError(''); setFieldError({});
    try { const uploaded = await (await getApi()).uploadDataset(supplier, files); onUploaded(uploaded.dataset_id, uploaded.supplier, uploaded.warnings); setFiles({}); onOpenChange(false); }
    catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Не удалось загрузить файлы';
      const role = cause instanceof ApiError ? cause.meta.file_role : undefined;
      if (typeof role === 'string' && allRoles.includes(role as FileRole)) setFieldError({ [role]: message });
      else setError(message);
    }
    finally { setBusy(false); }
  }
  return <Dialog open={open} onOpenChange={onOpenChange} title="Загрузка выгрузок"><p>Загрузите файлы одного поставщика. Первые четыре обязательны.</p><Select id="upload-supplier" label="Поставщик" value={supplier} onChange={(event) => { setSupplier(event.target.value as Supplier); setFiles({}); setError(''); setFieldError({}); }}><option value="IEK">IEK</option><option value="SE">Systeme Electric</option></Select><div className={styles.uploadFields}>{allRoles.map((role) => <Field key={`${supplier}-${role}`} id={`file-${role}`} label={`${roleLabels[role]}${requiredRoles.includes(role) ? ' *' : ''}`} error={fieldError[role]} type="file" accept=".xlsx" onChange={(event) => setFiles({ ...files, [role]: event.target.files?.[0] })} />)}</div>{error && <InlineAlert tone="error">{error}</InlineAlert>}<div className={styles.dialogActions}><Button type="button" onClick={() => onOpenChange(false)}>Отмена</Button><Button type="button" variant="primary" loading={busy} onClick={() => void submit()}>Загрузить</Button></div></Dialog>;
}

export default function App() {
  const queryClient = useQueryClient();
  const autoRunAttempted = useRef(false);
  const manualRunStarted = useRef(false);
  const historyProbeAttempted = useRef<string | null>(null);
  const runRecoveryAttempted = useRef(false);
  const [view, setView] = useState<View>(readView);
  const [params, setParams] = useState<RunParams>(initialParams);
  const [runId, setRunId] = useState<string | null>(() => import.meta.env.VITE_MOCK === '1' ? null : sessionStorage.getItem('grimstack-run-id'));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [summary, setSummary] = useState<{ text: string; cached: boolean; unavailable?: boolean } | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [calculating, setCalculating] = useState(false);
  const [bootstrapping, setBootstrapping] = useState(false);
  const [search, setSearch] = useState('');
  const [urgency, setUrgency] = useState<Urgency | ''>('');
  const metaQuery = useQuery({ queryKey: ['meta'], queryFn: async () => (await getApi()).getMeta() });
  const runQuery = useQuery({ queryKey: ['run', runId], enabled: Boolean(runId), queryFn: async () => (await getApi()).getRun(runId!) });
  useEffect(() => {
    const onPopState = () => setView(readView());
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);
  useEffect(() => {
    if (!metaQuery.isSuccess || runId || autoRunAttempted.current) return;
    autoRunAttempted.current = true;
    setBootstrapping(true);
    void (async () => {
      try {
        const result = await (await getApi()).createRun(initialParams);
        if (manualRunStarted.current) return;
        sessionStorage.setItem('grimstack-run-id', result.run_id);
        queryClient.setQueryData(['run', result.run_id], result);
        setRunId(result.run_id);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'Расчёт не выполнен');
      } finally { setBootstrapping(false); }
    })();
  }, [metaQuery.isSuccess, queryClient, runId]);
  useEffect(() => {
    if (!(runQuery.error instanceof ApiError) || runQuery.error.status !== 404) return;
    sessionStorage.removeItem('grimstack-run-id');
    if (!runRecoveryAttempted.current) { runRecoveryAttempted.current = true; autoRunAttempted.current = false; setRunId(null); }
  }, [runQuery.error]);
  const run = runQuery.data;
  const selected = run?.lines.find((line) => line.line_id === selectedId) ?? null;
  const [analysisId, setAnalysisId] = useState<string>('');
  const [analysisSearch, setAnalysisSearch] = useState('');
  const analysisLine = run?.lines.find((line) => line.line_id === analysisId) ?? run?.lines.find((line) => line.supplier === 'IEK' && line.flags.includes('stockout_restored')) ?? run?.lines[0] ?? null;
  const analysisOptions = useMemo(() => {
    const needle = analysisSearch.trim().toLocaleLowerCase('ru-RU');
    const matches = (run?.lines ?? []).filter((line) => !needle || `${line.supplier} ${line.sku} ${line.article ?? ''} ${line.name}`.toLocaleLowerCase('ru-RU').includes(needle)).slice(0, 49);
    return analysisLine && !matches.some((line) => line.line_id === analysisLine.line_id) ? [analysisLine, ...matches] : matches;
  }, [analysisLine, analysisSearch, run]);
  const analysisHistory = useQuery({ queryKey: ['history', run?.run_id, analysisLine?.supplier, analysisLine?.sku], enabled: view === 'analytics' && Boolean(analysisLine), queryFn: async () => (await getApi()).getSkuHistory(analysisLine!.supplier, analysisLine!.sku, run!.run_id) });
  const backtestQuery = useQuery({ queryKey: ['backtest'], enabled: view === 'analytics' && Boolean(run), queryFn: async () => (await getApi()).getBacktest() });
  useEffect(() => {
    if (import.meta.env.VITE_MOCK !== '1' || view !== 'analytics' || !run || !analysisHistory.isError || historyProbeAttempted.current === run.run_id) return;
    historyProbeAttempted.current = run.run_id;
    void Promise.any(run.lines.filter((line) => line.line_id !== analysisLine?.line_id).map(async (line) => ({ line, history: await (await getApi()).getSkuHistory(line.supplier, line.sku, run.run_id) }))).then(({ line, history }) => {
      queryClient.setQueryData(['history', run.run_id, line.supplier, line.sku], history);
      setAnalysisId(line.line_id);
    }).catch(() => undefined);
  }, [analysisHistory.isError, analysisLine?.line_id, queryClient, run, view]);
  const visibleLines = useMemo(() => (run?.lines ?? []).filter((line) => {
    const needle = search.toLocaleLowerCase('ru-RU');
    return (!urgency || line.urgency === urgency) && (!needle || `${line.sku} ${line.article ?? ''} ${line.name}`.toLocaleLowerCase('ru-RU').includes(needle));
  }).sort((a, b) => urgencyOrder[a.urgency] - urgencyOrder[b.urgency] || ((b.amount ?? -1) - (a.amount ?? -1)) || a.sku.localeCompare(b.sku)), [run, search, urgency]);

  async function calculate() {
    if (params.growth_pct < -90 || params.growth_pct > 500 || [params.lead_time_days, params.review_period_days].some((value) => value !== null && (value < 1 || value > 365)) || (params.service_level !== null && (params.service_level <= .5 || params.service_level >= 1))) { setError('Проверьте параметры: L/R — 1–365 дней, сервис — 50–100 %, прирост — от −90 до 500 %.'); return; }
    manualRunStarted.current = true;
    setCalculating(true); setError(''); setNotice(''); setSelectedId(null);
    try { const result = await (await getApi()).createRun(params); sessionStorage.setItem('grimstack-run-id', result.run_id); queryClient.setQueryData(['run', result.run_id], result); setRunId(result.run_id); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Расчёт не выполнен'); }
    finally { setCalculating(false); }
  }

  async function saveLine(line: OrderLine, quantity: number) {
    if (!runId) return;
    try { await (await getApi()).patchLine(runId, line.line_id, { final_qty: quantity }); await queryClient.invalidateQueries({ queryKey: ['run', runId] }); }
    catch (cause) { if (cause instanceof ApiError && cause.status === 409) await queryClient.invalidateQueries({ queryKey: ['run', runId] }); throw cause; }
  }
  async function approve(supplier: Supplier) { if (!runId) return; await (await getApi()).approveSupplier(runId, supplier); await queryClient.invalidateQueries({ queryKey: ['run', runId] }); }
  async function exportFile(supplier: Supplier) { if (!runId) return; const file = await (await getApi()).exportXlsx(runId, supplier); saveBlob(file.blob, file.filename ?? `order_${supplier}_${new Date().toISOString().slice(0, 10)}.xlsx`); }
  async function showSummary(supplier: Supplier) {
    if (!runId) return;
    try { setSummary(await (await getApi()).getSummary(runId, supplier)); }
    catch (cause) {
      if (!(cause instanceof ApiError && cause.status === 503)) throw cause;
      setSummary({ text: 'Готовая сводка сохранена для исходного расчёта с параметрами по умолчанию. После ручных правок или смены параметров текст пишет LLM — для этого нужен ключ OPENAI_API_KEY в .env. Числа заказа и обоснования строк от этого не зависят.', cached: false, unavailable: true });
    }
  }
  function resetRun() { autoRunAttempted.current = true; manualRunStarted.current = true; sessionStorage.removeItem('grimstack-run-id'); setRunId(null); setSelectedId(null); }
  function navigate(next: View) {
    if (next !== view) { window.history.pushState(null, '', next === 'overview' ? '/' : `/${next}`); setView(next); setSelectedId(null); window.scrollTo({ top: 0, behavior: 'instant' }); }
  }

  const title = { overview: 'Обзор закупок', orders: 'Заказы поставщикам', analytics: 'Аналитика спроса', data: 'Данные и расчёт' }[view];
  const subtitle = { overview: 'Что и сколько заказать у IEK и Systeme Electric, что срочно', orders: 'Проверьте количество, при необходимости поправьте и утвердите', analytics: 'Чем расчёт отличается от Excel-метода и насколько точен прогноз', data: 'Данные и параметры, на которых построен расчёт' }[view];

  return <div className={styles.appLayout}>
    <a className={styles.skipLink} href="#main-content">К содержанию</a>
    <aside className={styles.sidebar} aria-label="Навигация">
      <div className={styles.brand}><span className={styles.brandMark} aria-hidden="true">G</span><div><strong>GrimStack</strong><small>Заказы поставщикам</small></div></div>
      <div className={styles.navCaption}>РАБОЧАЯ ОБЛАСТЬ</div>
      <nav className={styles.nav} aria-label="Разделы">{viewItems.map((item) => {
        const Icon = viewIcons[item.id];
        return <a key={item.id} href={item.id === 'overview' ? '/' : `/${item.id}`} aria-current={view === item.id ? 'page' : undefined} className={view === item.id ? styles.activeNav : ''} onClick={(event) => { event.preventDefault(); navigate(item.id); }}><Icon size={19} weight={view === item.id ? 'fill' : 'regular'} aria-hidden="true" /><span className={styles.navLabel}>{item.label}</span><span className={styles.navShort}>{item.shortLabel}</span></a>;
      })}</nav>
      <div className={styles.sidebarBottom}><span className={styles.statusDot} />{run ? 'Расчёт активен' : bootstrapping ? 'Идёт расчёт' : 'Нет расчёта'}<small>Данные на {formatDate(run?.data_as_of ?? metaQuery.data?.data_as_of)}</small></div>
    </aside>

    <div className={styles.mainColumn}>
      <header className={styles.topbar}><span className={styles.topbarOrg}>Электрокомплект<span className={styles.topbarBrands}> · IEK и Systeme Electric</span></span><div>{import.meta.env.VITE_MOCK === '1' && <span className={styles.demoBadge}>Демо-данные</span>}<span className={styles.topbarDate}>Обновлено {formatDate(run?.created_at ?? metaQuery.data?.data_as_of)}</span></div></header>
      <main id="main-content" className={styles.content}>
        <header className={styles.pageHeader}><div><h1>{title}</h1><p className={styles.subtitle}>{subtitle}</p></div>{view !== 'data' && <Button type="button" variant="primary" onClick={() => navigate('data')}>Параметры расчёта</Button>}</header>
        {metaQuery.isError && <InlineAlert tone="error">Не удалось загрузить параметры: {metaQuery.error.message} <Button type="button" onClick={() => void metaQuery.refetch()}>Повторить</Button></InlineAlert>}
        {error && <InlineAlert tone="error">{error}</InlineAlert>}{notice && <InlineAlert tone="info">{notice}</InlineAlert>}
        {(calculating || bootstrapping || runQuery.isPending && Boolean(runId)) && <Skeleton label="Формирование расчёта" />}
        {runQuery.isError && !calculating && <InlineAlert tone="error">{runQuery.error instanceof ApiError && runQuery.error.status === 404 ? 'Расчёт больше не существует.' : runQuery.error.message} <Button type="button" onClick={() => { resetRun(); navigate('data'); }}>Новый расчёт</Button></InlineAlert>}
        {run && !calculating && <div className={styles.runMeta}><span>Расчёт от {formatDate(run.created_at)} · {import.meta.env.VITE_MOCK === '1' ? 'Демонстрационный образец' : run.params.method === 'analyze' ? 'Аналитический метод' : 'Excel-метод'}</span><span>ID {run.run_id}</span></div>}
        {run && run.warnings.length > 0 && view !== 'data' && <button className={styles.warningLink} type="button" onClick={() => navigate('data')}>{formatNumber(run.warnings.length)} примечания к данным</button>}
        {run && view === 'data' && run.warnings.map((warning, index) => <InlineAlert key={`${warning}-${index}`} tone="warning">{warning}</InlineAlert>)}
        {view === 'data' && import.meta.env.VITE_MOCK === '1' && <InlineAlert tone="info">Демо-режим показывает фиксированный образец расчёта. Изменение параметров и загрузка файлов не меняют его значения.</InlineAlert>}

        {view === 'overview' && run && !calculating && <>
          <section className={styles.kpis} aria-label="Главные показатели"><KpiMetric label="Позиций к заказу" value={formatNumber(run.kpi.lines_to_order)} /><KpiMetric label="Срочные позиции" value={formatNumber(run.kpi.critical)} hint={(() => { const count = run.lines.filter((line) => line.urgency === 'critical' && line.flags.includes('approx_stock')).length; return count ? `из них ${formatNumber(count)} — по оценочному остатку` : undefined; })()} critical /><KpiMetric label="Сумма заказа SE" value={run.kpi.total_amount === null ? 'Нет цены' : `${new Intl.NumberFormat('ru-RU', { notation: 'compact', maximumFractionDigits: 1 }).format(run.kpi.total_amount).replace(/\s+/, ' ')}\u00a0₸`} hint="у IEK нет цен" /><KpiMetric label="Позиции с излишком" value={formatNumber(run.kpi.overstock_lines)} hint="запас выше целевого уровня" /></section>
          <div className={styles.overviewSecondary}><span>Исключено разовых: <strong>{formatNumber(Math.round(run.kpi.oneoff_units_excluded))} шт</strong></span><span>Восстановлено спроса: <strong>{formatNumber(Math.round(run.kpi.stockout_units_restored))} шт</strong></span></div>
          <div className={styles.chartGrid}>
            <SectionPanel className={styles.chartPanel}><div className={styles.sectionHeading}><div><h2>Риск по позициям</h2></div><span>{formatNumber(run.lines.length)} SKU</span></div><Suspense fallback={<Skeleton label="Загрузка структуры заказов" />}><RiskChart run={run} /></Suspense></SectionPanel>
            <SectionPanel className={styles.chartPanel}><div className={styles.sectionHeading}><div><h2>Позиции по поставщикам</h2></div><span>Количество SKU</span></div><Suspense fallback={<Skeleton label="Загрузка заказов поставщиков" />}><SupplierChart run={run} /></Suspense></SectionPanel>
          </div>
          <div className={styles.overviewGrid}>
            <SectionPanel><div className={styles.sectionHeading}><div><h2>Срочные позиции</h2></div><button className={styles.textAction} type="button" onClick={() => { setUrgency('critical'); navigate('orders'); }}>Все заказы</button></div><div className={styles.priorityList}>{run.lines.filter((line) => line.urgency === 'critical').slice(0, 5).map((line) => <button key={line.line_id} type="button" onClick={() => { navigate('orders'); setSelectedId(line.line_id); }}><span><small>{line.supplier} · {line.sku}</small><strong>{line.name}</strong></span><span className={styles.priorityQty}>{formatQty(line.final_qty, line.unit)}<small>к заказу</small></span></button>)}{run.kpi.critical === 0 && <p className={styles.muted}>Срочных позиций нет.</p>}</div></SectionPanel>
            <SectionPanel><div className={styles.sectionHeading}><div><h2>Состояние заказов</h2></div></div><div className={styles.supplierOverview}>{run.suppliers.map((supplier) => <button key={supplier.supplier} type="button" onClick={() => navigate('orders')}><span><strong>{supplier.supplier_name}</strong><small>{formatNumber(supplier.lines_count)} поз. · {formatNumber(supplier.critical_count)} срочных</small></span><OrderStatus status={supplier.status} /></button>)}</div></SectionPanel>
          </div>
        </>}

        {view === 'orders' && run && !calculating && <div className={`${styles.workspace} ${selected ? styles.withDetail : ''}`}><SectionPanel className={styles.orders} aria-labelledby="orders-heading"><div className={styles.ordersHeading}><div><h2 id="orders-heading">Позиции к заказу</h2></div><div className={styles.filters}><Field id="search" label="Поиск по коду, артикулу, названию" value={search} onChange={(event) => setSearch(event.target.value)} /><Select id="urgency-filter" label="Срочность" value={urgency} onChange={(event) => setUrgency(event.target.value as Urgency | '')}><option value="">Все</option><option value="critical">Срочно</option><option value="high">Скоро</option><option value="planned">Плановый</option><option value="none">Без заказа</option></Select></div></div>{run.lines.length === 0 ? <p>В этом расчёте нет позиций к заказу.</p> : visibleLines.length === 0 ? <p>По выбранным фильтрам позиций нет.</p> : run.suppliers.filter((supplier) => visibleLines.some((line) => line.supplier === supplier.supplier)).map((supplier) => <SupplierOrders key={`${supplier.supplier}:${search}:${urgency}`} summary={supplier} lines={visibleLines.filter((line) => line.supplier === supplier.supplier)} supplierLines={run.lines.filter((line) => line.supplier === supplier.supplier)} selectedId={selectedId} onSelect={(line) => setSelectedId(line.line_id)} onSave={saveLine} onApprove={approve} onExport={exportFile} onSummary={showSummary} />)}</SectionPanel>{selected && <><button className={styles.backdrop} type="button" aria-label="Закрыть детали" onClick={() => setSelectedId(null)} /><SkuPanel line={selected} runId={run.run_id} onClose={() => setSelectedId(null)} /></>}</div>}

        {view === 'analytics' && run && !calculating && <>
          <div className={styles.chartGrid}><SectionPanel className={styles.chartPanel}><div className={styles.sectionHeading}><div><h2>Где расчёты расходятся</h2></div><span>4 больше и 4 меньше Excel-метода</span></div><Suspense fallback={<Skeleton label="Загрузка сравнения методов" />}><ComparisonChart run={run} /></Suspense></SectionPanel><SectionPanel className={styles.chartPanel}><div className={styles.sectionHeading}><div><h2>Распределение позиций</h2></div></div><Suspense fallback={<Skeleton label="Загрузка структуры заказов" />}><RiskChart run={run} /></Suspense><div className={styles.analysisFacts}><div><span>Исключено разовых</span><strong>{formatNumber(Math.round(run.kpi.oneoff_units_excluded))} шт</strong></div><div><span>Восстановлено при дефиците</span><strong>{formatNumber(Math.round(run.kpi.stockout_units_restored))} шт</strong></div></div></SectionPanel></div>
          {backtestQuery.isPending ? <SectionPanel><Skeleton label="Загрузка результатов бэктеста" /></SectionPanel> : backtestQuery.isError ? <InlineAlert tone="error">Не удалось загрузить бэктест: {backtestQuery.error.message} <Button type="button" onClick={() => void backtestQuery.refetch()}>Повторить</Button></InlineAlert> : backtestQuery.data ? <Suspense fallback={<SectionPanel><Skeleton label="Загрузка графиков бэктеста" /></SectionPanel>}><BacktestPanel report={backtestQuery.data} /></Suspense> : null}
          <Suspense fallback={<SectionPanel><Skeleton label="Загрузка сравнения сценариев" /></SectionPanel>}><ScenarioPanel key={run.run_id} run={run} meta={metaQuery.data} /></Suspense>
          <SectionPanel className={styles.historyPanel}><div className={styles.sectionHeading}><div><h2>Продажи и прогноз</h2></div><div className={styles.analysisPicker}><Field id="analysis-search" label="Найти SKU" value={analysisSearch} onChange={(event) => setAnalysisSearch(event.target.value)} placeholder="Код, артикул или название" /><Select id="analysis-sku" label="Позиция" value={analysisLine?.line_id ?? ''} onChange={(event) => setAnalysisId(event.target.value)}>{analysisOptions.map((line) => <option key={line.line_id} value={line.line_id}>{line.supplier} · {line.sku} · {line.name}</option>)}</Select></div></div>{analysisLine && <p className={styles.historyDescription}>{analysisLine.explanation}</p>}{analysisLine && (analysisHistory.isPending ? <Skeleton label="Загрузка истории продаж" /> : analysisHistory.isError ? <InlineAlert tone="error">{analysisHistory.error.message}</InlineAlert> : <Suspense fallback={<Skeleton label="Загрузка графика" />}><HistoryChart history={analysisHistory.data} /></Suspense>)}</SectionPanel>
        </>}

        {view === 'data' && <div className={styles.dataLayout}><div><SectionPanel><div className={styles.sectionHeading}><div><h2>Параметры расчёта</h2></div></div><Parameters meta={metaQuery.data} params={params} setParams={setParams} onCalculate={() => void calculate()} calculating={calculating} onOpenUpload={() => setUploadOpen(true)} /></SectionPanel></div><div className={styles.dataAside}><SectionPanel><h2>Набор данных</h2><p className={styles.muted}>{params.dataset_id ? `Загруженный набор ${params.dataset_id}` : 'Встроенные выгрузки поставщиков'}</p><dl><div><dt>Дата данных</dt><dd>{formatDate(run?.data_as_of ?? metaQuery.data?.data_as_of)}</dd></div><div><dt>Поставщиков</dt><dd>{formatNumber(metaQuery.data?.suppliers.length ?? null)}</dd></div><div><dt>Активный расчёт</dt><dd>{run ? formatDate(run.created_at) : 'Нет'}</dd></div></dl><Button type="button" onClick={() => setUploadOpen(true)}>Загрузить выгрузки</Button></SectionPanel>{run && <SectionPanel><h2>Текущий расчёт</h2><p className={styles.muted}>{formatNumber(run.kpi.lines_to_order)} позиций к заказу · {formatNumber(run.kpi.critical)} срочных</p><Button type="button" variant="primary" onClick={() => navigate('orders')}>Открыть заказы</Button></SectionPanel>}</div></div>}

        {!run && !calculating && !bootstrapping && !runQuery.isError && view !== 'data' && <SectionPanel className={styles.empty}><h2>Данные готовы к планированию</h2><p>Запустите расчёт по текущим выгрузкам, чтобы увидеть заказы и аналитику.</p><Button type="button" variant="primary" onClick={() => navigate('data')}>Перейти к расчёту</Button></SectionPanel>}
      </main>
    </div>
    <UploadDialog open={uploadOpen} onOpenChange={setUploadOpen} onUploaded={(datasetId, supplier, warnings) => { resetRun(); setParams((current) => ({ ...current, supplier, category: null, lead_time_days: null, review_period_days: null, dataset_id: datasetId })); setNotice(`Набор ${datasetId} загружен. Запустите новый расчёт. ${warnings.join(' ')}`); }} />
    <Dialog open={summary !== null} onOpenChange={(open) => { if (!open) setSummary(null); }} title={summary?.unavailable ? 'Сводка для этого расчёта не готова' : 'Сводка по заказу'}>{summary && <>{summary.unavailable ? <InlineAlert tone="info">{summary.text}</InlineAlert> : <p className={styles.summaryText}>{summary.text}</p>}{summary.cached && <p className={styles.muted}>Из кэша</p>}<div className={styles.dialogActions}><Button type="button" onClick={() => setSummary(null)}>Закрыть</Button></div></>}</Dialog>
  </div>;
}
