import * as RadixDialog from '@radix-ui/react-dialog';
import type { ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react';
import type { Urgency } from '../../api/types';
import { urgencyLabels } from '../format';
import styles from './ui.module.css';

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger'; loading?: boolean };
export function Button({ variant = 'secondary', loading = false, className = '', children, disabled, ...props }: ButtonProps) {
  return <button {...props} disabled={disabled || loading} aria-busy={loading} className={`${styles.button} ${styles[variant]} ${className}`}>{loading ? 'Загрузка…' : children}</button>;
}

export function PageShell({ children }: { children: ReactNode }) { return <main className={styles.shell}>{children}</main>; }
export function SectionPanel({ children, className = '', ...props }: HTMLAttributes<HTMLElement>) { return <section {...props} className={`${styles.panel} ${className}`}>{children}</section>; }

type FieldProps = InputHTMLAttributes<HTMLInputElement> & { label: string; error?: string; success?: string };
export function Field({ label, error, success, id, className = '', ...props }: FieldProps) {
  return <label className={`${styles.field} ${className}`} htmlFor={id}><span>{label}</span><input {...props} id={id} aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : undefined} className={`${styles.control} ${error ? styles.invalid : ''} ${success ? styles.valid : ''}`} />{error && <small id={`${id}-error`} className={styles.error}>{error}</small>}{success && <small className={styles.success}>{success}</small>}</label>;
}

type SelectProps = SelectHTMLAttributes<HTMLSelectElement> & { label: string; children: ReactNode };
export function Select({ label, children, id, className = '', ...props }: SelectProps) {
  return <label className={`${styles.field} ${className}`} htmlFor={id}><span>{label}</span><select {...props} id={id} className={styles.control}>{children}</select></label>;
}

export function StatusBadge({ urgency }: { urgency: Urgency }) { return <span className={`${styles.badge} ${styles[urgency]}`}>{urgencyLabels[urgency]}</span>; }
export function OrderStatus({ status }: { status: 'draft' | 'approved' }) { return <span className={`${styles.badge} ${styles[status]}`}>{status === 'approved' ? 'Утверждён' : 'Черновик'}</span>; }
export function KpiMetric({ label, value, critical = false }: { label: string; value: string; critical?: boolean }) { return <div className={styles.kpi}><span>{label}</span><strong className={critical ? styles.criticalValue : ''}>{value}</strong></div>; }
export function InlineAlert({ children, tone = 'warning' }: { children: ReactNode; tone?: 'warning' | 'error' | 'info' }) { return <div role={tone === 'error' ? 'alert' : 'status'} className={`${styles.alert} ${styles[tone]}`}>{children}</div>; }
export function Skeleton({ label = 'Загрузка данных' }: { label?: string }) { return <div role="status" aria-label={label} className={styles.skeleton}>{label}…</div>; }

export function Dialog({ open, onOpenChange, title, children }: { open: boolean; onOpenChange: (open: boolean) => void; title: string; children: ReactNode }) {
  return <RadixDialog.Root open={open} onOpenChange={onOpenChange}><RadixDialog.Portal><RadixDialog.Overlay className={styles.dialogOverlay} /><RadixDialog.Content className={styles.dialog}><RadixDialog.Title className={styles.dialogTitle}>{title}</RadixDialog.Title>{children}</RadixDialog.Content></RadixDialog.Portal></RadixDialog.Root>;
}

export function Tooltip({ text, children }: { text: string; children: ReactNode }) { return <span title={text} aria-label={text} className={styles.tooltip}>{children}</span>; }
