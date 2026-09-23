import type { DatasetUploaded, FileRole, LinePatch, Meta, OrderLine, RunParams, RunResult, SkuHistory, SummaryResponse, Supplier, SupplierSummary } from './types';

export interface ExportFile { blob: Blob; filename: string | null }
export type DatasetFiles = Partial<Record<FileRole, File>>;

export interface ProcurementApi {
  getMeta(): Promise<Meta>;
  createRun(params: RunParams): Promise<RunResult>;
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
