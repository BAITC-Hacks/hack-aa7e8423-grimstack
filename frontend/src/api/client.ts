import type { ProcurementApi } from './ProcurementApi';

let selected: Promise<ProcurementApi> | undefined;

export function getApi(): Promise<ProcurementApi> {
  selected ??= import.meta.env.VITE_MOCK === '1'
    ? import('./mockClient').then(({ mockClient }) => mockClient)
    : import('./liveClient').then(({ liveClient }) => liveClient);
  return selected;
}
