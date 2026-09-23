import Chart from 'react-apexcharts';
import type { ApexOptions } from 'apexcharts';
import type { SkuHistory } from '../api/types';
import { formatNumber } from '../shared/format';
import styles from './historyChart.module.css';

export default function HistoryChart({ history }: { history: SkuHistory }) {
  const tokens = getComputedStyle(document.documentElement);
  const color = (name: string) => tokens.getPropertyValue(name).trim();
  const months = [...history.months, ...history.forecast_months];
  const pad = (values: number[]) => [...values, ...Array<null>(history.forecast_months.length).fill(null)];
  const forecast = history.months.length > 0
    ? [...Array<null>(history.months.length - 1).fill(null), history.restored.at(-1) ?? null, ...history.forecast]
    : [...history.forecast];
  const stockoutBands = history.months.flatMap((month, index) => {
    if (!history.stockout[index] || history.stockout[index - 1]) return [];
    let endIndex = index + 1;
    while (endIndex < history.months.length && history.stockout[endIndex]) endIndex += 1;
    // Координата за пределами графика обрезается до его правой границы.
    const end = months[endIndex] ?? '100000px';
    return [{
      x: month,
      x2: end,
      fillColor: color('--critical-soft'),
      opacity: 0.55,
      borderColor: color('--critical'),
      label: {
        text: 'Нет товара',
        style: { background: color('--critical-soft'), color: color('--text') },
      },
    }];
  });
  const options: ApexOptions = {
    chart: { background: 'transparent', toolbar: { show: false }, animations: { enabled: false } },
    colors: [color('--border-strong'), color('--series-1'), color('--series-2'), color('--series-3')],
    theme: { mode: 'light' },
    xaxis: { categories: months, labels: { rotate: 0, formatter: (value) => /^\d{4}-(01|04|07|10)$/.test(String(value)) ? String(value) : '' } },
    yaxis: { labels: { formatter: (value) => formatNumber(value) } },
    legend: { show: true, position: 'bottom' },
    stroke: { width: [1, 2, 2, 2], dashArray: [0, 0, 0, 6] },
    tooltip: { shared: true },
    grid: { borderColor: color('--border') },
    annotations: { xaxis: stockoutBands },
  };
  const series = [
    { name: 'Продажи', data: pad(history.raw) },
    { name: 'Без разовых', data: pad(history.cleaned) },
    { name: 'Восстановлено', data: pad(history.restored) },
    { name: 'Прогноз', data: forecast },
  ];
  return <><div className={styles.chart} aria-hidden="true"><Chart options={options} series={series} type="line" height={260} /></div><details className={styles.tableDetails}><summary>Таблица значений по месяцам</summary><div className={styles.tableScroll}><table><thead><tr><th>Месяц</th><th>Продажи</th><th>Без разовых</th><th>Восстановлено</th><th>Дефицит</th></tr></thead><tbody>{history.months.map((month, index) => <tr key={month}><th>{month}</th><td>{formatNumber(history.raw[index])}</td><td>{formatNumber(history.cleaned[index])}</td><td>{formatNumber(history.restored[index])}</td><td>{history.stockout[index] ?? 'Нет'}</td></tr>)}</tbody></table></div></details></>;
}
