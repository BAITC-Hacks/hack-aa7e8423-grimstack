import type { DatasetUploaded, FileRole, LinePatch, Meta, OrderLine, RunParams, RunResult, SkuHistory, SummaryResponse, Supplier, SupplierSummary, Urgency } from './types';

export interface ExportFile { blob: Blob; filename: string | null }
export type DatasetFiles = Partial<Record<FileRole, File>>;

interface BacktestSupplier {
  wape: { cleaned: { analyze: { wape: number }; baseline: { wape: number } } };
  service_level: { analyze: { share: number }; baseline: { share: number }; target: number };
  frozen_capital_now?: { as_of: string; amount: number; skus: number };
}

export interface BacktestReport {
  checkpoints: string[];
  suppliers: Record<Supplier, BacktestSupplier>;
}

export interface CompareRequest { base: RunParams; scenario: RunParams }
export interface CompareDelta { lines_to_order: number; critical: number; total_qty: number; total_amount: number | null }
export interface ChangedLine {
  line_id: string;
  name: string;
  supplier: Supplier;
  base_qty: number;
  scenario_qty: number;
  base_urgency: Urgency;
  scenario_urgency: Urgency;
}
export interface CompareResult {
  base_run_id: string;
  scenario_run_id: string;
  delta: CompareDelta;
  changed: ChangedLine[];
}

export interface ProcurementApi {
  getMeta(): Promise<Meta>;
  getBacktest(): Promise<BacktestReport | null>;
  createRun(params: RunParams): Promise<RunResult>;
  compareRuns(request: CompareRequest): Promise<CompareResult>;
  getRun(runId: string): Promise<RunResult>;
  patchLine(runId: string, lineId: string, patch: LinePatch): Promise<OrderLine>;
  approveSupplier(runId: string, supplier: Supplier): Promise<SupplierSummary>;
  exportXlsx(runId: string, supplier: Supplier): Promise<ExportFile>;
  getSkuHistory(supplier: Supplier, sku: string, runId?: string): Promise<SkuHistory>;
  uploadDataset(supplier: Supplier, files: DatasetFiles): Promise<DatasetUploaded>;
  getSummary(runId: string, supplier: Supplier): Promise<SummaryResponse>;
}

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, detail: string, public readonly meta: Record<string, unknown> = {}) {
    super(detail);
    this.name = 'ApiError';
  }
}
