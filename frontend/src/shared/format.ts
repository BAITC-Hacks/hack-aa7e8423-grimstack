import type { Urgency } from '../api/types';

const numberFormatter = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
const moneyFormatter = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });

export function formatNumber(value: number | null | undefined): string {
  return value == null ? 'Нет данных' : numberFormatter.format(value);
}

export function formatMoney(value: number | null | undefined): string {
  return value == null ? 'Нет цены' : `${moneyFormatter.format(value)} ₸`;
}

export function formatQty(value: number | null | undefined, unit: string): string {
  return value == null ? 'Нет данных' : `${formatNumber(value)} ${unit}`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return 'Нет данных';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium' }).format(date);
}

export const urgencyLabels: Record<Urgency, string> = {
  critical: 'Срочно', high: 'Скоро', planned: 'Плановый', none: 'Без заказа',
};
