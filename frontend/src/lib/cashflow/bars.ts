// Shared bar width, baseline comparison and colour tone for cash flow category/source lists.

import { approximate, difference, percentOf } from '#lib/format/number.js';
import type { Amount } from '#lib/types.js';

/** Bar width in percent of `largest`: 0 for zero or negative amounts, otherwise at least 1.5%
 * so a small positive slice stays visible. */
export function barWidth(value: Amount, largest: number): number {
	const amount = approximate(value);
	if (amount <= 0 || largest <= 0) return 0;
	return Math.min(100, Math.max(1.5, (amount / largest) * 100));
}

/** How far `now` is from `then`, in whole percent of `then`; null when `then` is unknown or not
 * above zero. */
export function against(now: Amount, then: Amount | null): Amount | null {
	if (then === null || approximate(then) <= 0) return null;
	return percentOf(difference(now, then), then, 0);
}

/** Colour class for a percent change: more than 10% in the `favorable` direction is gain, more
 * than 10% the other way is caution, within 10% is secondary, and null has no class. */
export function changeTone(change: Amount | null, favorable: 'up' | 'down'): string {
	if (change === null) return '';
	const pct = approximate(change);
	if (pct > 10) return favorable === 'up' ? 'text-gain' : 'text-caution';
	if (pct < -10) return favorable === 'up' ? 'text-caution' : 'text-gain';
	return 'text-ink-secondary';
}
