import Chart from 'react-apexcharts';
import type { ApexOptions } from 'apexcharts';
import type { RunResult, Urgency } from '../api/types';
import { formatNumber } from '../shared/format';
import styles from './RunCharts.module.css';

const urgencies: { key: Urgency; label: string; token: string }[] = [
  { key: 'critical', label: 'Срочно', token: '--critical' },
  { key: 'high', label: 'Скоро', token: '--warning' },
  { key: 'planned', label: 'Плановый', token: '--success' },
  { key: 'none', label: 'Без заказа', token: '--border-strong' },
];

function palette() {
  const tokens = getComputedStyle(document.documentElement);
  return (name: string) => tokens.getPropertyValue(name).trim();
}

function baseOptions(): ApexOptions {
  const color = palette();
  return {
    chart: { background: 'transparent', toolbar: { show: false }, animations: { enabled: false }, fontFamily: 'IBM Plex Sans, sans-serif' },
    theme: { mode: 'light' },
    grid: { borderColor: color('--border'), strokeDashArray: 3 },
    dataLabels: { enabled: false },
    tooltip: { theme: 'light' },
    legend: { show: false },
    xaxis: { labels: { style: { colors: color('--text-muted') } } },
    yaxis: { labels: { style: { colors: color('--text-muted') }, formatter: (value) => formatNumber(value) } },
  };
}

export function RiskChart({ run }: { run: RunResult }) {
  const color = palette();
  // пустые категории не показываем: «Без заказа: 0» выглядит как сломанный показатель
  const shown = urgencies.map((item) => ({ ...item, count: run.lines.filter((line) => line.urgency === item.key).length })).filter((item) => item.count > 0);
  const counts = shown.map(({ count }) => count);
  if (run.lines.length === 0) return <p className={styles.noData}>В расчёте нет позиций.</p>;
  const options: ApexOptions = {
    ...baseOptions(),
    labels: shown.map(({ label }) => label),
    colors: shown.map(({ token }) => color(token)),
    stroke: { width: 2, colors: [color('--surface')] },
    plotOptions: { pie: { donut: { size: '76%', labels: { show: true, name: { show: true }, value: { show: true, formatter: (value: string) => formatNumber(Number(value)) }, total: { show: true, label: 'Позиций', formatter: () => formatNumber(run.lines.length) } } } } },
  };
  return <div className={styles.risk}><div className={styles.donut} aria-hidden="true"><Chart type="donut" height={260} options={options} series={counts} /></div><div className={styles.legend}>{shown.map(({ key, label, token, count }) => <div key={key}><span className={styles.legendLabel}><i style={{ background: color(token) }} />{label}</span><strong>{formatNumber(count)}</strong></div>)}</div></div>;
}

export function SupplierChart({ run }: { run: RunResult }) {
  const color = palette();
  const categories = run.suppliers.map((supplier) => supplier.supplier_name);
  if (categories.length === 0) return <p className={styles.noData}>Нет данных по поставщикам.</p>;
  const options: ApexOptions = {
    ...baseOptions(),
    colors: [color('--accent'), color('--border-strong')],
    chart: { ...baseOptions().chart, stacked: false },
    plotOptions: { bar: { horizontal: true, borderRadius: 4, barHeight: '38%' } },
    xaxis: { categories, labels: { formatter: (value) => formatNumber(Number(value)), style: { colors: color('--text-muted') } } },
    yaxis: { labels: { style: { colors: color('--text') }, maxWidth: 150 } },
    tooltip: { theme: 'light', y: { formatter: (value) => `${formatNumber(value)} поз.` } },
  };
  const series = [{ name: 'Позиции', data: run.suppliers.map((supplier) => supplier.lines_count) }];
  return <div className={styles.supplierChart}><div aria-hidden="true"><Chart type="bar" height={Math.max(190, categories.length * 72)} options={options} series={series} /></div><table className={styles.dataTable}><caption>Позиции заказа по поставщикам</caption><thead><tr><th>Поставщик</th><th>Позиций</th></tr></thead><tbody>{run.suppliers.map((supplier) => <tr key={supplier.supplier}><th>{supplier.supplier_name}</th><td>{formatNumber(supplier.lines_count)}</td></tr>)}</tbody></table></div>;
}

export function ComparisonChart({ run }: { run: RunResult }) {
  const color = palette();
  const comparable = run.lines.filter((line) => line.baseline_qty !== null);
  const difference = (line: typeof comparable[number]) => line.recommended_qty - (line.baseline_qty ?? 0);
  const less = comparable.filter((line) => difference(line) < 0).sort((a, b) => difference(a) - difference(b));
  const more = comparable.filter((line) => difference(line) > 0).sort((a, b) => difference(b) - difference(a));
  const lines = [...less.slice(0, 4), ...more.slice(0, 4)];
  if (comparable.length === 0) return <p className={styles.noData}>Нет сопоставимых значений Excel-метода.</p>;
  const summary = <p className={styles.comparisonSummary}>Меньше Excel-метода: {formatNumber(less.length)} поз. · больше: {formatNumber(more.length)} поз.</p>;
  if (lines.length === 0) return <>{summary}<p className={styles.noData}>Расхождений с Excel-методом нет.</p></>;
  const maxValue = Math.max(...lines.flatMap((line) => [line.recommended_qty, line.baseline_qty ?? 0]));
  const options: ApexOptions = {
    ...baseOptions(),
    colors: [color('--accent'), color('--border-strong')],
    plotOptions: { bar: { horizontal: true, borderRadius: 3, barHeight: '65%', dataLabels: { position: 'center' } } },
    dataLabels: { enabled: true, formatter: (value) => formatNumber(Math.round(Number(value))), style: { fontSize: '11px', colors: [color('--text')] }, background: { enabled: true, foreColor: color('--text'), backgroundColor: color('--surface'), borderRadius: 2, opacity: 0.9, padding: 2 } },
    xaxis: { categories: lines.map((line) => line.name), max: maxValue * 1.2, labels: { formatter: (value) => formatNumber(Number(value)), style: { colors: color('--text-muted') } } },
    yaxis: { labels: { style: { colors: color('--text') }, maxWidth: 150 } },
    tooltip: { theme: 'light', y: { formatter: (value) => formatNumber(Math.round(value)) } },
  };
  return <>{summary}<div className={styles.comparison} aria-hidden="true"><Chart type="bar" height={Math.max(320, lines.length * 48)} options={options} series={[{ name: 'Наш расчёт', data: lines.map((line) => line.recommended_qty) }, { name: 'Excel-метод', data: lines.map((line) => line.baseline_qty ?? 0) }]} /></div><div className={styles.seriesLegend}><span><i className={styles.green} />Наш расчёт</span><span><i className={styles.gray} />Excel-метод</span></div><table className={styles.dataTable}><caption>Четыре наибольших расхождения в каждую сторону относительно Excel-метода</caption><thead><tr><th>Товар</th><th>Наш расчёт</th><th>Excel</th><th>Разница</th></tr></thead><tbody>{lines.map((line) => <tr key={line.line_id}><th>{line.name}</th><td>{formatNumber(Math.round(line.recommended_qty))}</td><td>{formatNumber(Math.round(line.baseline_qty ?? 0))}</td><td>{difference(line) > 0 ? '+' : '−'}{formatNumber(Math.round(Math.abs(difference(line))))}</td></tr>)}</tbody></table></>;
}
