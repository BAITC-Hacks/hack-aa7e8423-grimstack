import type { BacktestReport } from '../api/ProcurementApi';
import type { Supplier } from '../api/types';
import { SectionPanel } from '../shared/ui';
import styles from './BacktestPanel.module.css';

const suppliers: Supplier[] = ['SE', 'IEK'];
const percentFormatter = new Intl.NumberFormat('ru-RU', { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 });
const millionsFormatter = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const countFormatter = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
const monthFormatter = new Intl.DateTimeFormat('ru-RU', { month: 'long', timeZone: 'UTC' });

function monthLabel(month: string): string {
  return monthFormatter.format(new Date(`${month}-01T00:00:00Z`));
}

function positionLabel(count: number): string {
  const lastTwo = count % 100;
  if (lastTwo >= 11 && lastTwo <= 14) return 'позиций';
  switch (count % 10) {
    case 1: return 'позиция';
    case 2:
    case 3:
    case 4: return 'позиции';
    default: return 'позиций';
  }
}

export function BacktestPanel({ report }: { report: BacktestReport }) {
  const frozen = report.suppliers.SE.frozen_capital_now;
  const first = report.checkpoints[0];
  const last = report.checkpoints.at(-1);
  const period = first && last ? `${monthLabel(first)}–${monthLabel(last)} ${last.slice(0, 4)}` : '';

  return <SectionPanel className={styles.panel} aria-labelledby="backtest-heading">
    <header className={styles.heading}><p className={styles.kicker}>ПРОВЕРКА НА ИСТОРИИ</p><h2 id="backtest-heading">Наш метод против Excel-метода</h2></header>
    <div className={styles.tableScroll} role="region" aria-label="Результаты бэктеста по поставщикам" tabIndex={0}>
      <table>
        <thead><tr><th scope="col">Поставщик</th><th scope="col">Ошибка прогноза: наш / Excel</th><th scope="col">Лучше на</th><th scope="col">Уровень сервиса: наш / Excel / цель</th></tr></thead>
        <tbody>{suppliers.map((supplier) => {
          const data = report.suppliers[supplier];
          const analyze = data.wape.cleaned.analyze.wape;
          const baseline = data.wape.cleaned.baseline.wape;
          const improvement = baseline === 0 ? null : 1 - analyze / baseline;
          return <tr key={supplier}><th scope="row">{supplier}</th><td>{percentFormatter.format(analyze)} / {percentFormatter.format(baseline)}</td><td>{improvement === null ? 'Нет данных' : percentFormatter.format(improvement)}</td><td>{percentFormatter.format(data.service_level.analyze.share)} / {percentFormatter.format(data.service_level.baseline.share)} / {percentFormatter.format(data.service_level.target)}</td></tr>;
        })}</tbody>
      </table>
    </div>
    {frozen && <p className={styles.frozen}>Заморожено сверх целевого запаса (SE, на {frozen.as_of.slice(8, 10)}.{frozen.as_of.slice(5, 7)}.{frozen.as_of.slice(0, 4)}): <strong>{millionsFormatter.format(frozen.amount / 1_000_000)} млн ₸ · {countFormatter.format(frozen.skus)} {positionLabel(frozen.skus)}</strong></p>}
    <p className={styles.note}>{report.checkpoints.length} контрольных точек{period ? `, ${period}` : ''}; прогноз строится только по данным до даты точки. Метод и оговорки — docs/backtest.md.</p>
  </SectionPanel>;
}
