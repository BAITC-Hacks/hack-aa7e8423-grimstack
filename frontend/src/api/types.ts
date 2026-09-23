// Зеркало моделей app/contracts.py. Даты и даты-время приходят строками ISO.
export type Supplier = string;
export type Urgency = 'critical' | 'high' | 'planned' | 'none';
export type FileRole = 'monthly_sales' | 'monthly_stock' | 'sales_tx' | 'in_transit' | 'moq' | 'seasonality';

export interface RunParams {
  dataset_id: string | null;
  supplier: Supplier | null;
  category: string | null;
  method: 'analyze' | 'baseline';
  lead_time_days: number | null;
  review_period_days: number | null;
  service_level: number | null;
  growth_pct: number;
}

export interface Component {
  key: string;
  label: string;
  value: number;
  kind: 'qty' | 'factor' | 'info';
  note: string | null;
}

export interface OrderLine {
  line_id: string;
  supplier: Supplier;
  sku: string;
  article: string | null;
  name: string;
  unit: string;
  category: string | null;
  product_group: string | null;
  stock_free: number;
  stock_source: 'warehouses' | 'estimate_lower_bound';
  in_transit: number;
  forecast_monthly: number;
  safety_stock: number;
  order_up_to: number;
  moq: number;
  recommended_qty: number;
  final_qty: number;
  baseline_qty: number | null;
  urgency: Urgency;
  days_of_cover: number | null;
  unit_cost: number | null;
  amount: number | null;
  explanation: string;
  components: Component[];
  flags: string[];
}

export interface SupplierSummary {
  supplier: Supplier;
  supplier_name: string;
  lines_count: number;
  critical_count: number;
  total_qty: number;
  total_amount: number | null;
  status: 'draft' | 'approved';
  approved_at: string | null;
}

export interface RunKpi {
  lines_to_order: number;
  critical: number;
  total_amount: number | null;
  oneoff_units_excluded: number;
  stockout_units_restored: number;
  overstock_lines: number;
}

export interface RunResult {
  run_id: string;
  created_at: string;
  params: RunParams;
  data_as_of: string;
  kpi: RunKpi;
  suppliers: SupplierSummary[];
  lines: OrderLine[];
  warnings: string[];
}

export interface OneoffEvent { date: string; doc: string; qty: number; capped_to: number }
export interface SkuHistory {
  supplier: Supplier;
  sku: string;
  name: string;
  months: string[];
  raw: number[];
  cleaned: number[];
  restored: number[];
  stockout: ('full' | 'start' | 'end' | 'effective' | null)[];
  forecast_months: string[];
  forecast: number[];
  oneoff_events: OneoffEvent[];
}

export interface SupplierInfo { supplier: Supplier; supplier_name: string; lead_time_days: number; review_period_days: number; categories: string[] }
export interface Meta { data_as_of: string; suppliers: SupplierInfo[]; product_groups: string[]; llm_available: boolean }
export interface LinePatch { final_qty: number }
export interface DatasetUploaded { dataset_id: string; supplier: Supplier; supplier_name: string; warnings: string[] }
export interface SummaryResponse { text: string; cached: boolean }
export interface ErrorBody { detail: string; code: string; meta: Record<string, unknown> }
