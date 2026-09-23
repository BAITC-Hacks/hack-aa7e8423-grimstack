import rawMeta from './mock-data/meta.json';
import rawRun from './mock-data/run.json';
import rawHistory from './mock-data/sku-history.json';
import { ApiError, type ProcurementApi } from './ProcurementApi';
import type { Meta, RunParams, RunResult, SkuHistory, Supplier } from './types';

const meta = rawMeta as Meta;
const fixture = rawRun as RunResult;
const history = rawHistory as SkuHistory;
const runs = new Map<string, RunResult>();
const pause = () => new Promise((resolve) => setTimeout(resolve, 260));
const clone = <T>(value: T): T => structuredClone(value);

function getStored(runId: string): RunResult {
  const run = runs.get(runId);
  if (!run) throw new ApiError(404, 'not_found', 'Прогон не найден. Запустите новый расчёт.');
  return run;
}

function totals(run: RunResult): void {
  for (const summary of run.suppliers) {
    const lines = run.lines.filter((line) => line.supplier === summary.supplier);
    summary.lines_count = lines.length;
    summary.critical_count = lines.filter((line) => line.urgency === 'critical').length;
    summary.total_qty = lines.reduce((sum, line) => sum + line.final_qty, 0);
    summary.total_amount = lines.some((line) => line.unit_cost !== null)
      ? lines.reduce((sum, line) => sum + (line.amount ?? 0), 0)
      : null;
  }
  run.kpi.lines_to_order = run.lines.filter((line) => line.final_qty > 0).length;
  run.kpi.critical = run.lines.filter((line) => line.final_qty > 0 && line.urgency === 'critical').length;
  run.kpi.total_amount = run.suppliers.some((supplier) => supplier.total_amount !== null)
    ? run.suppliers.reduce((sum, supplier) => sum + (supplier.total_amount ?? 0), 0)
    : null;
}

export const mockClient: ProcurementApi = {
  async getMeta() { await pause(); return clone(meta); },
  async createRun(params: RunParams) {
    await pause();
    const run = clone(fixture);
    run.run_id = `demo-${Date.now()}`;
    run.created_at = new Date().toISOString();
    run.params = clone(params);
    if (params.supplier) {
      run.lines = run.lines.filter((line) => line.supplier === params.supplier);
      run.suppliers = run.suppliers.filter((summary) => summary.supplier === params.supplier);
    }
    if (params.supplier) run.warnings.push('Показатели разовых продаж, восстановленного спроса и излишков относятся к исходному образцу для всех поставщиков.');
    totals(run);
    runs.set(run.run_id, run);
    return clone(run);
  },
  async getRun(runId) { await pause(); return clone(getStored(runId)); },
  async patchLine(runId, lineId, patch) {
    await pause();
    const run = getStored(runId);
    const line = run.lines.find((item) => item.line_id === lineId);
    if (!line) throw new ApiError(404, 'not_found', 'Позиция не найдена');
    if (run.suppliers.find((supplier) => supplier.supplier === line.supplier)?.status === 'approved') throw new ApiError(409, 'approved', 'Заказ уже утверждён');
    if (!Number.isFinite(patch.final_qty) || patch.final_qty < 0 || Math.abs(patch.final_qty / line.moq - Math.round(patch.final_qty / line.moq)) > 1e-8) throw new ApiError(422, 'invalid_quantity', `Количество должно быть кратно ${line.moq}`);
    line.final_qty = patch.final_qty;
    line.amount = line.unit_cost === null ? null : line.final_qty * line.unit_cost;
    totals(run);
    return clone(line);
  },
  async approveSupplier(runId, supplier) {
    await pause();
    const summary = getStored(runId).suppliers.find((item) => item.supplier === supplier);
    if (!summary) throw new ApiError(404, 'not_found', 'Поставщик не найден');
    summary.status = 'approved';
    summary.approved_at = new Date().toISOString();
    return clone(summary);
  },
  async exportXlsx() { throw new ApiError(503, 'export_unavailable', 'Экспорт XLSX доступен при подключённом backend API'); },
  async getSkuHistory(supplier: Supplier, sku: string) {
    await pause();
    if (history.supplier !== supplier || history.sku !== sku) throw new ApiError(404, 'history_unavailable', 'Для этого товара в образце нет истории');
    return clone(history);
  },
  async uploadDataset(supplier) {
    await pause();
    return { dataset_id: `demo-${supplier}-${Date.now()}`, supplier, warnings: ['Демо-режим: файлы не разбирались, расчёт использует образец.'] };
  },
  async getSummary() { await pause(); throw new ApiError(503, 'summary_unavailable', 'В демонстрационном образце нет сводки'); },
};
