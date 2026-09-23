import Chart from 'react-apexcharts';
import type { ApexOptions } from 'apexcharts';
import type { RunResult, Urgency } from '../api/types';
import { formatNumber } from '../shared/format';
import styles from './RunCharts.module.css';

const urgencies: { key: Urgency; label: string; token: string }[] = [
  { key: 'critical', label: 'Срочно', token: '--critical' },
  { key: 'high', label: 'Скоро', token: '--warning' },
  { key: 'planned', label: 'Плановый', token: '--accent' },
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
  const counts = urgencies.map(({ key }) => run.lines.filter((line) => line.urgency === key).length);
  if (run.lines.length === 0) return <p className={styles.noData}>В расчёте нет позиций.</p>;
  const options: ApexOptions = {
    ...baseOptions(),
    labels: urgencies.map(({ label }) => label),
    colors: urgencies.map(({ token }) => color(token)),
    stroke: { width: 0 },
    plotOptions: { pie: { donut: { size: '76%', labels: { show: true, name: { show: true }, value: { show: true, formatter: (value: string) => formatNumber(Number(value)) }, total: { show: true, label: 'Позиций', formatter: () => formatNumber(run.lines.length) } } } } },
  };
  return <div className={styles.risk}><div className={styles.donut} aria-hidden="true"><Chart type="donut" height={260} options={options} series={counts} /></div><div className={styles.legend}>{urgencies.map(({ key, label, token }, index) => <div key={key}><span className={styles.legendLabel}><i style={{ background: color(token) }} />{label}</span><strong>{formatNumber(counts[index])}</strong></div>)}</div></div>;
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
  const divergence = (recommended: number, baseline: number | null) => Math.abs(recommended - (baseline ?? 0)) / Math.max(recommended, baseline ?? 0, 1);
  const lines = [...run.lines].filter((line) => line.baseline_qty !== null).sort((a, b) => divergence(b.recommended_qty, b.baseline_qty) - divergence(a.recommended_qty, a.baseline_qty)).slice(0, 8);
  if (lines.length === 0) return <p className={styles.noData}>Нет сопоставимых значений Excel-метода.</p>;
  const options: ApexOptions = {
    ...baseOptions(),
    colors: [color('--accent'), color('--border-strong')],
    plotOptions: { bar: { horizontal: true, borderRadius: 3, barHeight: '65%' } },
    xaxis: { categories: lines.map((line) => line.sku), labels: { formatter: (value) => formatNumber(Number(value)), style: { colors: color('--text-muted') } } },
    yaxis: { labels: { style: { colors: color('--text') }, maxWidth: 120 } },
    tooltip: { theme: 'light', y: { formatter: (value) => formatNumber(value) } },
  };
  return <><div className={styles.comparison} aria-hidden="true"><Chart type="bar" height={Math.max(320, lines.length * 48)} options={options} series={[{ name: 'Наш расчёт', data: lines.map((line) => line.recommended_qty) }, { name: 'Excel-метод', data: lines.map((line) => line.baseline_qty ?? 0) }]} /></div><div className={styles.seriesLegend}><span><i className={styles.green} />Наш расчёт</span><span><i className={styles.gray} />Excel-метод</span></div><table className={styles.dataTable}><caption>Сравнение методов для позиций с наибольшим расхождением</caption><thead><tr><th>Код</th><th>Наш расчёт</th><th>Excel</th></tr></thead><tbody>{lines.map((line) => <tr key={line.line_id}><th>{line.sku}</th><td>{formatNumber(line.recommended_qty)}</td><td>{formatNumber(line.baseline_qty)}</td></tr>)}</tbody></table></>;
}
