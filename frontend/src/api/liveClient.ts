import { ApiError, type BacktestReport, type CompareResult, type ProcurementApi } from './ProcurementApi';
import type { ErrorBody, Supplier } from './types';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, init);
  } catch {
    throw new ApiError(0, 'network_error', 'Не удалось связаться с сервером');
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null) as Partial<ErrorBody> | null;
    throw new ApiError(response.status, body?.code ?? 'http_error', typeof body?.detail === 'string' ? body.detail : `Ошибка сервера (${response.status})`, body?.meta ?? {});
  }
  return response.json() as Promise<T>;
}

function parseFilename(header: string | null): string | null {
  if (!header) return null;
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)?.[1];
  if (encoded) {
    try { return decodeURIComponent(encoded); } catch { return null; }
  }
  return /filename="?([^";]+)"?/i.exec(header)?.[1] ?? null;
}

const id = encodeURIComponent;
const query = (supplier: Supplier) => `?supplier=${id(supplier)}`;

export const liveClient: ProcurementApi = {
  getMeta: (datasetId) => request(`/meta${datasetId ? `?dataset_id=${id(datasetId)}` : ''}`),
  async getBacktest() {
    try { return await request<BacktestReport>('/backtest'); }
    catch (cause) { if (cause instanceof ApiError && cause.status === 404) return null; throw cause; }
  },
  createRun: (params) => request('/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params) }),
  compareRuns: (comparison) => request<CompareResult>('/runs/compare', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(comparison) }),
  getRun: (runId) => request(`/runs/${id(runId)}`),
  patchLine: (runId, lineId, patch) => request(`/runs/${id(runId)}/lines/${id(lineId)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) }),
  approveSupplier: (runId, supplier) => request(`/runs/${id(runId)}/suppliers/${id(supplier)}/approve`, { method: 'POST' }),
  async exportXlsx(runId, supplier) {
    let response: Response;
    try { response = await fetch(`/api/runs/${id(runId)}/export.xlsx${query(supplier)}`); }
    catch { throw new ApiError(0, 'network_error', 'Не удалось скачать Excel-файл'); }
    if (!response.ok) {
      const body = await response.json().catch(() => null) as Partial<ErrorBody> | null;
      throw new ApiError(response.status, body?.code ?? 'export_error', body?.detail ?? 'Не удалось скачать Excel-файл', body?.meta ?? {});
    }
    return { blob: await response.blob(), filename: parseFilename(response.headers.get('Content-Disposition')) };
  },
  getSkuHistory: (supplier, sku, runId) => request(`/sku/${id(supplier)}/${id(sku)}/history${runId ? `?run_id=${id(runId)}` : ''}`),
  async uploadDataset(supplier, files) {
    const form = new FormData();
    for (const [role, file] of Object.entries(files)) if (file) form.append(role, file);
    return request(`/datasets/${id(supplier)}`, { method: 'POST', body: form });
  },
  async uploadNewSupplier(name, template, files) {
    const form = new FormData();
    form.append('name', name);
    form.append('template', template);
    for (const [role, file] of Object.entries(files)) if (file) form.append(role, file);
    return request('/datasets/new', { method: 'POST', body: form });
  },
  getSummary: (runId, supplier) => request(`/runs/${id(runId)}/summary${query(supplier)}`, { method: 'POST' }),
};
