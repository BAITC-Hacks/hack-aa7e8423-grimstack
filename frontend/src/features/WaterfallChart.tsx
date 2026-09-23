import Chart from 'react-apexcharts';
import type { ApexOptions } from 'apexcharts';
import type { Component } from '../api/types';
import { formatNumber } from '../shared/format';
import styles from './WaterfallChart.module.css';

interface WaterfallChartProps {
  steps: Component[];
  recommendedQty: number;
  unit: string;
}

export default function WaterfallChart({ steps, recommendedQty, unit }: WaterfallChartProps) {
  if (steps.length === 0) return <p className={styles.message}>Шаги расчёта отсутствуют.</p>;
  if (!Number.isFinite(recommendedQty) || steps.some((step) => !Number.isFinite(step.value))) {
    return <p className={styles.message}>График недоступен: в расчёте есть некорректные значения.</p>;
  }

  const tokens = getComputedStyle(document.documentElement);
  const color = (name: string) => tokens.getPropertyValue(name).trim();
  const offsets = steps.reduce<number[]>((values, step) => [...values, (values.at(-1) ?? 0) + step.value], [0]);
  const data = steps.map((step, index) => {
    const start = offsets[index] ?? 0;
    const end = offsets[index + 1] ?? start;
    return {
      x: `${index + 1}. ${step.label}`,
      y: [Math.min(start, end), Math.max(start, end)],
    };
  });
  const total = offsets.at(-1) ?? 0;
  const values = [0, recommendedQty, ...data.flatMap((point) => point.y)];
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const extent = maximum - minimum;
  const mismatch = Math.abs(total - recommendedQty) > Math.max(0.01, Math.abs(recommendedQty) * 1e-8);

  if (extent === 0) return <p className={styles.message}>Все шаги расчёта равны нулю.</p>;

  const options: ApexOptions = {
    chart: { type: 'rangeBar', background: 'transparent', toolbar: { show: false }, animations: { enabled: false }, fontFamily: 'IBM Plex Sans, sans-serif' },
    colors: [...steps.map((step) => step.value < 0 ? color('--series-2') : color('--series-1')), color('--accent')],
    theme: { mode: 'light' },
    plotOptions: { bar: { horizontal: true, distributed: true, barHeight: '55%' } },
    dataLabels: { enabled: false },
    legend: { show: false },
    grid: { borderColor: color('--border'), strokeDashArray: 3 },
    xaxis: {
      type: 'numeric',
      min: minimum - extent * 0.04,
      max: maximum + extent * 0.04,
      labels: { formatter: (value) => formatNumber(Number(value)), style: { colors: color('--text-muted') } },
    },
    yaxis: { labels: { maxWidth: 150, style: { colors: color('--text') } } },
    annotations: { xaxis: [{ x: 0, borderColor: color('--border-strong'), strokeDashArray: 0 }] },
    tooltip: { enabled: false },
    responsive: [{ breakpoint: 540, options: { yaxis: { labels: { maxWidth: 82, style: { colors: color('--text') } } } } }],
  };

  return <>
    <p className={styles.caption}>Шаги изменения количества, {unit}. Плюс добавляет, минус вычитает.</p>
    <div className={styles.chart} aria-hidden="true">
      <Chart type="rangeBar" height={Math.max(240, (steps.length + 1) * 42)} options={options} series={[{ name: 'Расчёт', data: [...data, { x: 'Рекомендация', y: [Math.min(0, recommendedQty), Math.max(0, recommendedQty)] }] }]} />
    </div>
    {mismatch && <p className={styles.message}>Сумма шагов ({formatNumber(total)} {unit}) отличается от рекомендации ({formatNumber(recommendedQty)} {unit}).</p>}
  </>;
}
