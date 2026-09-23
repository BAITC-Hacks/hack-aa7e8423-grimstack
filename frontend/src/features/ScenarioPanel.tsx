import { useState } from 'react';
import Chart from 'react-apexcharts';
import type { ApexOptions } from 'apexcharts';
import { getApi } from '../api/client';
import type { ChangedLine, CompareResult } from '../api/ProcurementApi';
import type { Meta, RunResult, Supplier } from '../api/types';
import { formatMoney, formatNumber, urgencyLabels } from '../shared/format';
import { Button, Field, InlineAlert, SectionPanel, Select } from '../shared/ui';
import styles from './ScenarioPanel.module.css';

interface Comparison {
  result: CompareResult;
  units: Map<string, string>;
  baseLead: number;
  scenarioLead: number;
  supplierName: string;
}

function signed(value: number): string {
  return `${value > 0 ? '+' : ''}${formatNumber(value)}`;
}

function quantity(value: number, unit: string): string {
  return `${formatNumber(value)} ${unit}`;
}

function lineUnit(line: ChangedLine, comparison: Comparison): string {
  return comparison.units.get(line.line_id) ?? 'ед.';
}

export default function ScenarioPanel({ run, meta }: { run: RunResult; meta: Meta | undefined }) {
  const [supplier, setSupplier] = useState<Supplier | ''>(run.params.supplier ?? run.suppliers[0]?.supplier ?? '');
  const [delay, setDelay] = useState('14');
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [unit, setUnit] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  const supplierInfo = meta?.suppliers.find((item) => item.supplier === supplier);
  const baseLead = run.params.lead_time_days ?? supplierInfo?.lead_time_days;
  const maxDelay = baseLead === undefined ? 0 : 365 - baseLead;
  const parsedDelay = Number(delay);
  const validDelay = /^\d+$/.test(delay) && Number.isInteger(parsedDelay) && parsedDelay >= 1 && parsedDelay <= maxDelay;

  async function compare() {
    if (!supplier || baseLead === undefined || !validDelay) return;
    setPending(true);
    setError('');
    setComparison(null);
    try {
      const api = await getApi();
      const base = { ...run.params, supplier };
      const scenario = { ...base, lead_time_days: baseLead + parsedDelay };
      const result = await api.compareRuns({ base, scenario });
      const [baseRun, scenarioRun] = await Promise.all([
        api.getRun(result.base_run_id),
        api.getRun(result.scenario_run_id),
      ]);
      const units = new Map(baseRun.lines.map((line) => [line.line_id, line.unit]));
      for (const line of scenarioRun.lines) units.set(line.line_id, line.unit);
      const next = { result, units, baseLead, scenarioLead: baseLead + parsedDelay, supplierName: supplierInfo?.supplier_name ?? supplier };
      setComparison(next);
      const firstChange = result.changed.at(0);
      setUnit(firstChange ? lineUnit(firstChange, next) : '');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось сравнить сценарии');
    } finally {
      setPending(false);
    }
  }

  const changed = comparison?.result.changed ?? [];
  const units = [...new Set(changed.map((line) => comparison ? lineUnit(line, comparison) : ''))];
  const selectedUnit = units.includes(unit) ? unit : (units[0] ?? '');
  const chartLines = comparison
    ? changed.filter((line) => lineUnit(line, comparison) === selectedUnit && line.scenario_qty !== line.base_qty).slice(0, 10)
    : [];
  const color = (token: string) => getComputedStyle(document.documentElement).getPropertyValue(token).trim();
  const chartOptions: ApexOptions = {
    chart: { background: 'transparent', toolbar: { show: false }, animations: { enabled: false }, fontFamily: color('--font-body') },
    theme: { mode: 'light' },
    colors: chartLines.map((line) => line.scenario_qty > line.base_qty ? color('--accent') : color('--critical')),
    plotOptions: { bar: { horizontal: true, distributed: true, borderRadius: 3, barHeight: '62%' } },
    grid: { borderColor: color('--border'), strokeDashArray: 3 },
    dataLabels: { enabled: false },
    legend: { show: false },
    xaxis: { categories: chartLines.map((line) => line.name), labels: { formatter: (value) => formatNumber(Number(value)), style: { colors: color('--text-muted') } } },
    yaxis: { labels: { maxWidth: 180, style: { colors: color('--text') } } },
    tooltip: { theme: 'light', y: { formatter: (value) => `${signed(value)} ${selectedUnit}` } },
  };

  return <SectionPanel className={styles.panel} aria-labelledby="scenario-heading">
    <header className={styles.heading}><p className={styles.kicker}>ЧТО ЕСЛИ</p><h2 id="scenario-heading">Задержка поставщика</h2><p>Пересчитываем два варианта по тем же данным и показываем, как изменится рекомендуемый заказ.</p></header>
    <div className={styles.controls}>
      <Select id="scenario-supplier" label="Поставщик" value={supplier} disabled={pending} onChange={(event) => { setSupplier(event.target.value as Supplier); setComparison(null); setError(''); }}>
        {run.suppliers.map((item) => <option key={item.supplier} value={item.supplier}>{item.supplier_name}</option>)}
      </Select>
      <Field id="scenario-delay" label="Задержка, дней" type="number" min={1} max={Math.max(1, maxDelay)} step={1} value={delay} disabled={pending} onChange={(event) => { setDelay(event.target.value); setComparison(null); setError(''); }} error={delay && !validDelay && baseLead !== undefined ? `Введите от 1 до ${maxDelay} дней` : undefined} />
      <Button type="button" variant="primary" loading={pending} disabled={!supplier || !validDelay || pending} onClick={() => void compare()}>Сравнить</Button>
    </div>
    <p className={styles.lead}>{baseLead === undefined ? 'Загружаем срок поставки…' : `Срок поставки: ${formatNumber(baseLead)} → ${validDelay ? formatNumber(baseLead + parsedDelay) : '—'} дней`}</p>
    {import.meta.env.VITE_MOCK === '1' && <InlineAlert tone="info">Демо-образец фиксирован: изменение срока поставки в нём не меняет расчёт.</InlineAlert>}
    {error && <InlineAlert tone="error">{error}</InlineAlert>}
    {comparison && <>
      <div className={styles.metrics} aria-label="Изменение относительно базового расчёта">
        <div><span>Позиций к заказу</span><strong>{signed(comparison.result.delta.lines_to_order)}</strong></div>
        <div><span>Срочных позиций</span><strong>{signed(comparison.result.delta.critical)}</strong></div>
        {comparison.result.delta.total_amount !== null && <div><span>Сумма заказа</span><strong>{comparison.result.delta.total_amount > 0 ? '+' : ''}{formatMoney(comparison.result.delta.total_amount)}</strong></div>}
      </div>
      <p className={styles.context}>{comparison.supplierName}: срок поставки {formatNumber(comparison.baseLead)} → {formatNumber(comparison.scenarioLead)} дней. Сравниваются рекомендации без ручных правок текущего заказа.</p>
      {changed.length === 0 ? <p className={styles.empty}>Изменений в рекомендациях и срочности нет.</p> : <>
        <div className={styles.chartHeading}><h3>Изменение количества к заказу</h3>{units.length > 1 && <Select id="scenario-unit" label="Единица измерения" value={selectedUnit} onChange={(event) => setUnit(event.target.value)}>{units.map((item) => <option key={item} value={item}>{item}</option>)}</Select>}</div>
        {chartLines.length > 0 ? <><p className={styles.note}>График показывает до 10 товаров в выбранной единице: вправо — больше, влево — меньше.</p><div className={styles.chart} aria-hidden="true"><Chart type="bar" height={Math.max(240, chartLines.length * 50)} options={chartOptions} series={[{ name: 'Изменение заказа', data: chartLines.map((line) => line.scenario_qty - line.base_qty) }]} /></div></> : <p className={styles.empty}>У этих товаров изменена только срочность.</p>}
        <div className={styles.tableScroll} role="region" aria-label="Изменения по товарам" tabIndex={0}><table><caption>До 20 наибольших изменений рекомендации и срочности</caption><thead><tr><th scope="col">Товар</th><th scope="col">База</th><th scope="col">Сценарий</th><th scope="col">Разница</th><th scope="col">Срочность</th></tr></thead><tbody>{changed.map((line) => {
          const rowUnit = lineUnit(line, comparison);
          return <tr key={line.line_id}><th scope="row">{line.name}</th><td>{quantity(line.base_qty, rowUnit)}</td><td>{quantity(line.scenario_qty, rowUnit)}</td><td>{signed(line.scenario_qty - line.base_qty)} {rowUnit}</td><td>{urgencyLabels[line.base_urgency]} → {urgencyLabels[line.scenario_urgency]}</td></tr>;
        })}</tbody></table></div>
      </>}
    </>}
  </SectionPanel>;
}
