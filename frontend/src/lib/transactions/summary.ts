// Currency selection for the transactions "By category" panel: which currencies have
// categories to show (ordered by row count, most first) and which tab is active.

import { approximate } from '#lib/format/number.js';
import type { CategoryTotal } from '#lib/types.js';
import { TRANSFERS } from './filter.js';

/** Currencies that have at least one non-transfer, non-zero category total, most rows first. */
export function summaryCurrencies(totals: readonly CategoryTotal[]): string[] {
	const rows = new Map<string, number>();
	for (const total of totals) {
		if (total.key === TRANSFERS || approximate(total.total) === 0) continue;
		rows.set(total.currency, (rows.get(total.currency) ?? 0) + total.count);
	}
	return [...rows].sort((a, b) => b[1] - a[1]).map(([currency]) => currency);
}

/** The currency tab shown: a tab the reader picked explicitly when still available, else the
 * header's currency when the transactions have any in it, else the first currency. */
export function activeCurrency(
	currencies: readonly string[],
	picked: string | null,
	preferred?: string | null
): string | undefined {
	if (picked && currencies.includes(picked)) return picked;
	if (preferred && currencies.includes(preferred)) return preferred;
	return currencies[0];
}
