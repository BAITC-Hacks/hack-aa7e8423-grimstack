import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, type DatasetFiles } from './api/ProcurementApi';
import { getApi } from './api/client';
import type { FileRole, Meta, OrderLine, RunParams, Supplier, SupplierSummary, Urgency } from './api/types';
import { SkuPanel } from './features/SkuPanel';
import { saveBlob } from './shared/download';
import { formatDate, formatMoney, formatNumber, formatQty } from './shared/format';
import { isValidQuantity } from './shared/quantity';
import { Button, Dialog, Field, InlineAlert, KpiMetric, OrderStatus, PageShell, SectionPanel, Select, Skeleton, StatusBadge, Tooltip } from './shared/ui';
import styles from './App.module.css';

const initialParams: RunParams = { dataset_id: null, supplier: null, category: null, method: 'analyze', lead_time_days: null, review_period_days: null, service_level: null, growth_pct: 0 };
const urgencyOrder: Record<Urgency, number> = { critical: 0, high: 1, planned: 2, none: 3 };
const requiredRoles: FileRole[] = ['monthly_sales', 'monthly_stock', 'sales_tx', 'in_transit'];
const allRoles: FileRole[] = [...requiredRoles, 'moq', 'seasonality'];
const roleLabels: Record<FileRole, string> = { monthly_sales: 'Продажи по месяцам', monthly_stock: 'Остатки по месяцам', sales_tx: 'Строки продаж', in_transit: 'Товар в пути', moq: 'Кратность заказа', seasonality: 'Сезонность' };

function Parameters({ meta, params, setParams, onCalculate, calculating, onOpenUpload }: { meta: Meta | undefined; params: RunParams; setParams: (params: RunParams) => void; onCalculate: () => void; calculating: boolean; onOpenUpload: () => void }) {
  const supplierInfo = meta?.suppliers.find((item) => item.supplier === params.supplier);
  return <SectionPanel aria-label="Параметры расчёта">
    <div className={styles.parameterGrid}>
      <Select id="supplier" label="Поставщик" value={params.supplier ?? ''} onChange={(event) => setParams({ ...params, supplier: (event.target.value || null) as Supplier | null, dataset_id: null, category: null, lead_time_days: null, review_period_days: null })}><option value="">Все поставщики</option>{meta?.suppliers.map((item) => <option key={item.supplier} value={item.supplier}>{item.supplier_name}</option>)}</Select>
      <Select id="method" label="Метод" value={params.method} onChange={(event) => setParams({ ...params, method: event.target.value as RunParams['method'] })}><option value="analyze">Наш расчёт</option><option value="baseline">Excel-метод</option></Select>
      <Field id="lead" label="Срок поставки L, дни" type="number" min={1} max={365} placeholder={supplierInfo ? `По умолчанию: ${supplierInfo.lead_time_days}` : 'По поставщику'} value={params.lead_time_days ?? ''} onChange={(event) => setParams({ ...params, lead_time_days: event.target.value ? Number(event.target.value) : null })} />
      <Field id="review" label="Период R, дни" type="number" min={1} max={365} placeholder={supplierInfo ? `По умолчанию: ${supplierInfo.review_period_days}` : 'По поставщику'} value={params.review_period_days ?? ''} onChange={(event) => setParams({ ...params, review_period_days: event.target.value ? Number(event.target.value) : null })} />
      <Field id="growth" label="Прирост, %" type="number" min={-90} max={500} value={params.growth_pct} onChange={(event) => setParams({ ...params, growth_pct: Number(event.target.value) })} />
      <Button variant="primary" type="button" loading={calculating} onClick={onCalculate}>Рассчитать</Button>
    </div>
    <details className={styles.more}><summary>Доп. параметры</summary><div className={styles.advancedGrid}>
      <Select id="category" label="Категория" value={params.category ?? ''} disabled={!supplierInfo} onChange={(event) => setParams({ ...params, category: event.target.value || null })}><option value="">Все категории</option>{supplierInfo?.categories.map((category) => <option key={category} value={category}>{category}</option>)}</Select>
      <Field id="service" label="Уровень сервиса, %" type="number" min={51} max={99} step="0.1" placeholder="По категории" value={params.service_level === null ? '' : params.service_level * 100} onChange={(event) => setParams({ ...params, service_level: event.target.value ? Number(event.target.value) / 100 : null })} />
    </div><div className={styles.uploadLink}><Button type="button" onClick={onOpenUpload}>Загрузить свои выгрузки</Button>{params.dataset_id && <span>Набор данных: {params.dataset_id}</span>}</div></details>
  </SectionPanel>;
}

function QuantityEditor({ line, approved, onSave }: { line: OrderLine; approved: boolean; onSave: (line: OrderLine, quantity: number) => Promise<void> }) {
  const [draft, setDraft] = useState(String(line.final_qty));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const changed = Number(draft) !== line.final_qty;
  async function submit() {
    const value = Number(draft);
    if (draft.trim() === '' || !isValidQuantity(value, line.moq)) { setError(`Количество должно быть кратно ${formatNumber(line.moq)} и не меньше 0`); return; }
    setBusy(true); setError('');
    try { await onSave(line, value); } catch (cause) { if (cause instanceof ApiError && cause.status === 409) setDraft(String(line.final_qty)); setError(cause instanceof Error ? cause.message : 'Не удалось сохранить количество'); } finally { setBusy(false); }
  }
  return <div className={styles.editor}><div className={styles.editorControls}><input aria-label={`Итоговое количество для ${line.name}`} type="number" min="0" step={line.moq} value={draft} disabled={approved || busy} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void submit(); if (event.key === 'Escape') { setDraft(String(line.final_qty)); setError(''); } }} />{changed && !approved && <Button type="button" disabled={busy} loading={busy} onClick={() => void submit()}>Сохранить</Button>}</div>{error && <small role="alert" className={styles.cellError}>{error}</small>}</div>;
}

function OrderCard({ line, approved, selected, onSelect, onSave }: { line: OrderLine; approved: boolean; selected: boolean; onSelect: (line: OrderLine) => void; onSave: (line: OrderLine, quantity: number) => Promise<void> }) {
  return <article className={`${styles.card} ${selected ? styles.selected : ''}`}><span className={styles.sku}>{line.sku}{line.article ? ` · ${line.article}` : ''}</span><button className={styles.productButton} type="button" onClick={() => onSelect(line)}>{line.name}</button><div className={styles.cardFacts}><div><span>Остаток</span><strong>{line.stock_source === 'estimate_lower_bound' ? '≈' : ''}{formatQty(line.stock_free, line.unit)}</strong></div><div><span>Наш расчёт</span><strong>{formatQty(line.recommended_qty, line.unit)}</strong></div><div><span>Excel-метод</span><strong className={line.baseline_qty === 0 && line.recommended_qty > 0 ? styles.compare : ''}>{formatQty(line.baseline_qty, line.unit)}</strong></div><div><span>Срочность</span><StatusBadge urgency={line.urgency} /></div></div><QuantityEditor key={`${line.line_id}:${line.final_qty}`} line={line} approved={approved} onSave={onSave} /><Button type="button" variant="ghost" onClick={() => onSelect(line)}>Почему столько</Button></article>;
}

function SupplierOrders({ summary, lines, selectedId, onSelect, onSave, onApprove, onExport, onSummary }: { summary: SupplierSummary; lines: OrderLine[]; selectedId: string | null; onSelect: (line: OrderLine) => void; onSave: (line: OrderLine, quantity: number) => Promise<void>; onApprove: (supplier: Supplier) => Promise<void>; onExport: (supplier: Supplier) => Promise<void>; onSummary: (supplier: Supplier) => Promise<void> }) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  async function action(work: () => Promise<void>) { setBusy(true); setActionError(''); try { await work(); } catch (cause) { setActionError(cause instanceof Error ? cause.message : 'Действие не выполнено'); } finally { setBusy(false); } }
  return <section className={styles.supplier} aria-labelledby={`supplier-${summary.supplier}`}><header className={styles.supplierHeader}><div className={styles.supplierTitle}><h3 id={`supplier-${summary.supplier}`}>{summary.supplier_name}</h3><OrderStatus status={summary.status} /></div><div className={styles.supplierActions}><Button type="button" disabled={summary.status === 'approved' || busy} onClick={() => setConfirm(true)}>Утвердить</Button><Button type="button" disabled={busy} onClick={() => void action(() => onExport(summary.supplier))}>Excel для 1С</Button><Button type="button" variant="ghost" disabled={busy} onClick={() => void action(() => onSummary(summary.supplier))}>Сводка</Button></div></header><p className={styles.supplierMeta}>{formatNumber(summary.lines_count)} поз. · {summary.total_amount === null ? 'Сумма: нет цены' : `Оценочная сумма: ${formatMoney(summary.total_amount)}`}{summary.approved_at ? ` · Утверждён ${formatDate(summary.approved_at)}` : ''}</p>{actionError && <InlineAlert tone="error">{actionError}</InlineAlert>}
    <div className={styles.tableScroll}><table><thead><tr><th>Товар</th><th>Остаток</th><th>Наш расчёт</th><th>Excel</th><th>Итог</th><th>Срочность</th></tr></thead><tbody>{lines.map((line) => <tr key={line.line_id} className={selectedId === line.line_id ? styles.selected : ''}><td className={styles.productCell}><span className={styles.sku}>{line.sku}{line.article ? ` · ${line.article}` : ''}</span><button className={styles.productButton} type="button" onClick={() => onSelect(line)}>{line.name}</button></td><td className={styles.number}>{line.stock_source === 'estimate_lower_bound' ? <Tooltip text="Нижняя оценка остатка IEK">≈</Tooltip> : null}{formatQty(line.stock_free, line.unit)}</td><td className={styles.number}>{formatQty(line.recommended_qty, line.unit)}</td><td className={`${styles.number} ${line.baseline_qty === 0 && line.recommended_qty > 0 ? styles.compare : ''}`}>{formatQty(line.baseline_qty, line.unit)}</td><td><QuantityEditor key={`${line.line_id}:${line.final_qty}`} line={line} approved={summary.status === 'approved'} onSave={onSave} /></td><td><StatusBadge urgency={line.urgency} /></td></tr>)}</tbody></table></div>
    <div className={styles.cards}>{lines.map((line) => <OrderCard key={line.line_id} line={line} approved={summary.status === 'approved'} selected={selectedId === line.line_id} onSelect={onSelect} onSave={onSave} />)}</div>
    <Dialog open={confirm} onOpenChange={setConfirm} title={`Утвердить заказ ${summary.supplier_name}?`}><p>После утверждения количества этого поставщика нельзя будет изменить.</p><div className={styles.dialogActions}><Button type="button" onClick={() => setConfirm(false)}>Отмена</Button><Button type="button" variant="primary" loading={busy} onClick={() => void action(async () => { await onApprove(summary.supplier); setConfirm(false); })}>Утвердить</Button></div></Dialog>
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
    try { const uploaded = await (await getApi()).uploadDataset(supplier, files); onUploaded(uploaded.dataset_id, uploaded.supplier, uploaded.warnings); onOpenChange(false); }
    catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Не удалось загрузить файлы';
      const role = cause instanceof ApiError ? cause.meta.file_role : undefined;
      if (typeof role === 'string' && allRoles.includes(role as FileRole)) setFieldError({ [role]: message });
      else setError(message);
    }
    finally { setBusy(false); }
  }
  return <Dialog open={open} onOpenChange={onOpenChange} title="Загрузка выгрузок"><p>Загрузите файлы одного поставщика. Первые четыре обязательны.</p><Select id="upload-supplier" label="Поставщик" value={supplier} onChange={(event) => setSupplier(event.target.value as Supplier)}><option value="IEK">IEK</option><option value="SE">Systeme Electric</option></Select><div className={styles.uploadFields}>{allRoles.map((role) => <Field key={role} id={`file-${role}`} label={`${roleLabels[role]}${requiredRoles.includes(role) ? ' *' : ''}`} error={fieldError[role]} type="file" accept=".xlsx" onChange={(event) => setFiles({ ...files, [role]: event.target.files?.[0] })} />)}</div>{error && <InlineAlert tone="error">{error}</InlineAlert>}<div className={styles.dialogActions}><Button type="button" onClick={() => onOpenChange(false)}>Отмена</Button><Button type="button" variant="primary" loading={busy} onClick={() => void submit()}>Загрузить</Button></div></Dialog>;
}

export default function App() {
  const queryClient = useQueryClient();
  const [params, setParams] = useState<RunParams>(initialParams);
  const [runId, setRunId] = useState<string | null>(() => sessionStorage.getItem('grimstack-run-id'));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [summary, setSummary] = useState<{ text: string; cached: boolean } | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [calculating, setCalculating] = useState(false);
  const [search, setSearch] = useState('');
  const [urgency, setUrgency] = useState<Urgency | ''>('');
  const metaQuery = useQuery({ queryKey: ['meta'], queryFn: async () => (await getApi()).getMeta() });
  const runQuery = useQuery({ queryKey: ['run', runId], enabled: Boolean(runId), queryFn: async () => (await getApi()).getRun(runId!) });
  useEffect(() => { if (runQuery.error instanceof ApiError && runQuery.error.status === 404) sessionStorage.removeItem('grimstack-run-id'); }, [runQuery.error]);
  const run = runQuery.data;
  const selected = run?.lines.find((line) => line.line_id === selectedId) ?? null;
  const visibleLines = useMemo(() => (run?.lines ?? []).filter((line) => {
    const needle = search.toLocaleLowerCase('ru-RU');
    return (!urgency || line.urgency === urgency) && (!needle || `${line.sku} ${line.article ?? ''} ${line.name}`.toLocaleLowerCase('ru-RU').includes(needle));
  }).sort((a, b) => urgencyOrder[a.urgency] - urgencyOrder[b.urgency] || ((b.amount ?? -1) - (a.amount ?? -1)) || a.sku.localeCompare(b.sku)), [run, search, urgency]);

  async function calculate() {
    if (params.growth_pct < -90 || params.growth_pct > 500 || [params.lead_time_days, params.review_period_days].some((value) => value !== null && (value < 1 || value > 365)) || (params.service_level !== null && (params.service_level <= .5 || params.service_level >= 1))) { setError('Проверьте параметры: L/R — 1–365 дней, сервис — 50–100 %, прирост — от −90 до 500 %.'); return; }
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
  async function showSummary(supplier: Supplier) { if (!runId) return; const result = await (await getApi()).getSummary(runId, supplier); setSummary(result); }
  function resetRun() { sessionStorage.removeItem('grimstack-run-id'); setRunId(null); setSelectedId(null); }

  return <PageShell>
    <header className={styles.pageHeader}><div><p className={styles.eyebrow}>GrimStack · закупки</p><h1>Планирование заказов</h1></div><div className={styles.headerMeta}>{import.meta.env.VITE_MOCK === '1' && <span className={styles.demoBadge}>Демо-данные</span>}<span>Данные на {formatDate(run?.data_as_of ?? metaQuery.data?.data_as_of)}</span></div></header>
    {import.meta.env.VITE_MOCK === '1' && <InlineAlert tone="warning">Демонстрационный режим: образец получен из реального расчёта. Изменение параметров и загрузка файлов не пересчитывают его.</InlineAlert>}
    {metaQuery.isError && <InlineAlert tone="error">Не удалось загрузить параметры: {metaQuery.error.message} <Button type="button" onClick={() => void metaQuery.refetch()}>Повторить</Button></InlineAlert>}
    <Parameters meta={metaQuery.data} params={params} setParams={setParams} onCalculate={() => void calculate()} calculating={calculating} onOpenUpload={() => setUploadOpen(true)} />
    {error && <InlineAlert tone="error">{error}</InlineAlert>}{notice && <InlineAlert tone="info">{notice}</InlineAlert>}
    {calculating && <Skeleton label="Расчёт ассортимента" />}
    {runQuery.isError && !calculating && <InlineAlert tone="error">{runQuery.error instanceof ApiError && runQuery.error.status === 404 ? 'Прогон больше не существует. Запустите новый расчёт.' : runQuery.error.message} <Button type="button" onClick={resetRun}>Новый расчёт</Button></InlineAlert>}
    {run && !calculating && <>
      <div className={styles.runMeta}><span>Расчёт от {formatDate(run.created_at)} · {run.params.method === 'analyze' ? 'Наш расчёт' : 'Excel-метод'}</span><span>Прогон: {run.run_id}</span></div>
      {run.warnings.map((warning, index) => <InlineAlert key={`${warning}-${index}`} tone="warning">{warning}</InlineAlert>)}
      <section className={styles.kpis} aria-label="Главные показатели"><KpiMetric label="К заказу" value={`${formatNumber(run.kpi.lines_to_order)} поз.`} /><KpiMetric label="Срочно" value={formatNumber(run.kpi.critical)} critical /><KpiMetric label="Разовых исключено" value={`${formatNumber(run.kpi.oneoff_units_excluded)} шт.`} /><KpiMetric label="Спрос восстановлен" value={`${formatNumber(run.kpi.stockout_units_restored)} шт.`} /></section>
      <div className={styles.secondaryMetrics}>Позиции с излишком: {formatNumber(run.kpi.overstock_lines)} · Оценочная сумма по данным с ценами: {formatMoney(run.kpi.total_amount)}</div>
      <div className={`${styles.workspace} ${selected ? styles.withDetail : ''}`}><SectionPanel className={styles.orders} aria-labelledby="orders-heading"><div className={styles.ordersHeading}><h2 id="orders-heading">Заказы поставщикам</h2><div className={styles.filters}><Field id="search" label="Поиск по коду, артикулу, названию" value={search} onChange={(event) => setSearch(event.target.value)} /><Select id="urgency-filter" label="Срочность" value={urgency} onChange={(event) => setUrgency(event.target.value as Urgency | '')}><option value="">Все</option><option value="critical">Срочно</option><option value="high">Скоро</option><option value="planned">Плановый</option><option value="none">Без заказа</option></Select></div></div>{run.lines.length === 0 ? <p>Заказывать нечего</p> : run.suppliers.map((supplier) => <SupplierOrders key={supplier.supplier} summary={supplier} lines={visibleLines.filter((line) => line.supplier === supplier.supplier)} selectedId={selectedId} onSelect={(line) => setSelectedId(line.line_id)} onSave={saveLine} onApprove={approve} onExport={exportFile} onSummary={showSummary} />)}</SectionPanel>{selected && <><button className={styles.backdrop} type="button" aria-label="Закрыть детали" onClick={() => setSelectedId(null)} /><SkuPanel line={selected} onClose={() => setSelectedId(null)} /></>}</div>
    </>}
    {!run && !calculating && !runQuery.isError && <SectionPanel className={styles.empty}><h2>Начните с расчёта</h2><p>Выберите параметры и нажмите «Рассчитать». Затем можно проверить каждую позицию, изменить количество и выгрузить заказ.</p></SectionPanel>}
    <UploadDialog open={uploadOpen} onOpenChange={setUploadOpen} onUploaded={(datasetId, supplier, warnings) => { setParams((current) => ({ ...current, supplier, category: null, dataset_id: datasetId })); setNotice(`Набор ${datasetId} загружен. ${warnings.join(' ')}`); }} />
    <Dialog open={summary !== null} onOpenChange={(open) => { if (!open) setSummary(null); }} title="Сводка по заказу">{summary && <><p>{summary.text}</p>{summary.cached && <p>Из кэша</p>}<div className={styles.dialogActions}><Button type="button" onClick={() => setSummary(null)}>Закрыть</Button></div></>}</Dialog>
  </PageShell>;
}
