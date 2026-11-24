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
	const [year, month, day] = through.split('-').map(Number);
	const since = new Date(Date.UTC(year, month - 4, day)).toISOString().slice(0, 10);
	return payment.first > since;
}

/** `a month` or `a year`, as the pace reads after an amount. */
export function everyName(every: RecurringPayment['every']): string {
	return every === 'monthly' ? 'a month' : 'a year';
}
