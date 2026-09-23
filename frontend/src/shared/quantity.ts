export function isValidQuantity(quantity: number, moq: number): boolean {
  return Number.isFinite(quantity) && quantity >= 0 && moq > 0 && Math.abs(quantity / moq - Math.round(quantity / moq)) < 1e-8;
}
