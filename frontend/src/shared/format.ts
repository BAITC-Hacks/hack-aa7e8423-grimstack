import type { Urgency } from '../api/types';

const numberFormatter = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
const moneyFormatter = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
const quantityFormatter = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
const signedQuantityFormatter = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0, signDisplay: 'exceptZero' });

export function formatNumber(value: number | null | undefined): string {
  return value == null ? 'Нет данных' : numberFormatter.format(Object.is(value, -0) ? 0 : value);
}

export function formatMoney(value: number | null | undefined): string {
  return value == null ? 'Нет цены' : `${moneyFormatter.format(value)} ₸`;
}

export function formatQty(value: number | null | undefined, unit: string): string {
  return value == null ? 'Нет данных' : `${quantityFormatter.format(Object.is(value, -0) || (value < 0 && value > -0.5) ? 0 : value)} ${unit}`;
}

export function formatSignedQty(value: number, unit: string): string {
  return `${signedQuantityFormatter.format(value)} ${unit}`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return 'Нет данных';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium' }).format(date);
}

export const urgencyLabels: Record<Urgency, string> = {
  critical: 'Срочно', high: 'Скоро', planned: 'Плановый', none: 'Без заказа',
};
