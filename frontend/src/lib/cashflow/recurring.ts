import { difference } from '#lib/format/number.js';
import type { Recurring, RecurringPayment } from '#lib/types.js';

/** Whether there is anything to list, active or stopped: the panel is left out otherwise. */
export function anyRecurring(recurring: Recurring): boolean {
	return recurring.through !== null && recurring.active.length + recurring.ended.length > 0;
}

/** Whether the price went up or down since the amount paid before; null when it did not
 * change. */
export function priceChange(payment: RecurringPayment): 'up' | 'down' | null {
	if (payment.previous === null) return null;
	const moved = difference(payment.amount, payment.previous);
	if (/^-?0*\.?0*$/.test(moved)) return null;
	return moved.startsWith('-') ? 'down' : 'up';
}

/** Whether the first payment came in the three months up to `through`, the last day of the
 * statements: a subscription taken out lately. */
export function isNew(payment: RecurringPayment, through: string | null): boolean {
	if (through === null) return false;
	return payment.first > monthsBefore(through, 3);
}

/** The same day `months` months before `day` (`2026-05-31`), or the last of that month when
 * it is shorter, as the server counts months back. */
export function monthsBefore(day: string, months: number): string {
	const [year, month, date] = day.split('-').map(Number);
	const index = year * 12 + month - 1 - months;
	const [y, m] = [Math.floor(index / 12), (index % 12) + 1];
	const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
	const pad = (n: number) => String(n).padStart(2, '0');
	return `${y}-${pad(m)}-${pad(Math.min(date, last))}`;
}

/** `a month` or `a year`, as the pace reads after an amount. */
export function everyName(every: RecurringPayment['every']): string {
	return every === 'monthly' ? 'a month' : 'a year';
}
