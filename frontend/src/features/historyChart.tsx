import Chart from 'react-apexcharts';
import type { ApexOptions } from 'apexcharts';
import type { SkuHistory } from '../api/types';
import { formatNumber } from '../shared/format';
import styles from './historyChart.module.css';

export default function HistoryChart({ history }: { history: SkuHistory }) {
  const months = [...history.months, ...history.forecast_months];
  const pad = (values: number[]) => [...values, ...Array<null>(history.forecast_months.length).fill(null)];
  const forecast = [...Array<null>(history.months.length - 1).fill(null), history.restored.at(-1) ?? null, ...history.forecast];
  const options: ApexOptions = {
    chart: { background: 'transparent', toolbar: { show: false }, animations: { enabled: false } },
    colors: ['#F2F4F5', '#AAB3BB', '#78B995', '#69A9E0'],
    theme: { mode: 'dark' },
    xaxis: { categories: months, labels: { rotate: -45 } },
    yaxis: { labels: { formatter: (value) => formatNumber(value) } },
    legend: { show: true, position: 'bottom' },
    stroke: { width: [2, 1, 2, 2], dashArray: [0, 0, 0, 6] },
    tooltip: { shared: true },
    grid: { borderColor: '#363C42' },
    annotations: { xaxis: history.months.flatMap((month, index) => history.stockout[index] ? [{ x: month, borderColor: '#E07878', label: { text: 'Нет товара', style: { background: '#43272A', color: '#F2F4F5' } } }] : []) },
  };
  const series = [
    { name: 'Продажи', data: pad(history.raw) },
    { name: 'Без разовых', data: pad(history.cleaned) },
    { name: 'Восстановлено', data: pad(history.restored) },
    { name: 'Прогноз', data: forecast },
  ];
  return <><div className={styles.chart} aria-hidden="true"><Chart options={options} series={series} type="line" height={260} /></div><details className={styles.tableDetails}><summary>Таблица значений по месяцам</summary><div className={styles.tableScroll}><table><thead><tr><th>Месяц</th><th>Продажи</th><th>Без разовых</th><th>Восстановлено</th><th>Дефицит</th></tr></thead><tbody>{history.months.map((month, index) => <tr key={month}><th>{month}</th><td>{formatNumber(history.raw[index])}</td><td>{formatNumber(history.cleaned[index])}</td><td>{formatNumber(history.restored[index])}</td><td>{history.stockout[index] ?? 'Нет'}</td></tr>)}</tbody></table></div></details></>;
}
