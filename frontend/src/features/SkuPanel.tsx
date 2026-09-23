import { lazy, Suspense, useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { OrderLine } from '../api/types';
import { getApi } from '../api/client';
import { formatMoney, formatNumber, formatQty } from '../shared/format';
import { Button, InlineAlert, Skeleton } from '../shared/ui';
import styles from './SkuPanel.module.css';

const HistoryChart = lazy(() => import('./historyChart'));
const WaterfallChart = lazy(() => import('./WaterfallChart'));
const flagLabels: Record<string, string> = {
  oneoff_excluded: 'Разовые продажи исключены', project_order: 'Проектный заказ',
  stockout_restored: 'Спрос при дефиците восстановлен', seasonal: 'Сезонность',
  trend_up: 'Растущий спрос', trend_down: 'Снижающийся спрос',
  intermittent: 'Редкий спрос', overstock: 'Излишек', no_history: 'Нет истории',
  approx_stock: 'Остаток оценочный', discontinued: 'Снят с продажи',
  do_not_order: 'Не заказывать', keep_1m: 'Запас на месяц', new_item: 'Новая позиция',
};

export function SkuPanel({ line, runId, onClose }: { line: OrderLine; runId: string; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const qtySteps = line.components.filter((component) => component.kind === 'qty');
  const history = useQuery({ queryKey: ['history', runId, line.supplier, line.sku], queryFn: async () => (await getApi()).getSkuHistory(line.supplier, line.sku, runId) });
  useEffect(() => { closeRef.current?.focus(); }, [line.line_id]);
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [onClose]);

  return <aside className={styles.panel} aria-labelledby="sku-heading">
    <header className={styles.header}><div><p className={styles.kicker}>{line.supplier} · {line.sku}</p><h2 id="sku-heading">{line.name}</h2></div><Button ref={closeRef} variant="ghost" onClick={onClose}>Закрыть</Button></header>
    <p className={styles.description}>{line.explanation}</p>
    <div className={styles.facts}><div><span>Наш расчёт</span><strong>{formatQty(line.recommended_qty, line.unit)}</strong></div><div><span>Excel-метод</span><strong>{formatQty(line.baseline_qty, line.unit)}</strong></div><div><span>Цена</span><strong>{formatMoney(line.unit_cost)}</strong></div><div><span>Сумма</span><strong>{formatMoney(line.amount)}</strong></div></div>
    <section aria-labelledby="history-heading"><h3 id="history-heading">История и прогноз</h3>{history.isPending ? <Skeleton label="Загрузка истории" /> : history.isError ? <InlineAlert tone="info">{history.error.message}</InlineAlert> : <Suspense fallback={<Skeleton label="Загрузка графика" />}><HistoryChart history={history.data} /></Suspense>}</section>
    <section aria-labelledby="steps-heading"><h3 id="steps-heading">Почему столько</h3><Suspense fallback={<Skeleton label="Загрузка графика расчёта" />}><WaterfallChart steps={qtySteps} recommendedQty={line.recommended_qty} unit={line.unit} /></Suspense><div className={styles.steps}>{qtySteps.map((component) => <div className={styles.step} key={component.key}><span title={component.note ?? undefined}>{component.label}</span><strong>{component.value > 0 ? '+' : component.value < 0 ? '−' : ''}{formatNumber(Math.abs(component.value))} {line.unit}</strong></div>)}<div className={`${styles.step} ${styles.total}`}><span>Рекомендация</span><strong>{formatNumber(line.recommended_qty)} {line.unit}</strong></div></div></section>
    {line.components.some((component) => component.kind === 'factor') && <section><h3>Множители прогноза</h3><div className={styles.steps}>{line.components.filter((component) => component.kind === 'factor').map((component) => <div className={styles.step} key={component.key}><span>{component.label}</span><strong>×{formatNumber(component.value)}</strong></div>)}</div></section>}
    {line.components.some((component) => component.kind === 'info') && <section><h3>Корректировки истории</h3><div className={styles.steps}>{line.components.filter((component) => component.kind === 'info').map((component) => <div className={styles.step} key={component.key}><span>{component.label}</span><strong>{formatNumber(component.value)}</strong></div>)}</div></section>}
    {line.flags.length > 0 && <p className={styles.flags}>Флаги: {line.flags.map((flag) => flagLabels[flag] ?? flag).join(' · ')}</p>}
  </aside>;
}
