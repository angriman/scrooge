// Pure helpers for the transactions page's List and By category views: shared column grid,
// day grouping with exact BigInt currency totals, and currency tab ordering.

import { sum } from '#lib/format/number.js';
import type { Amount, CategoryTotal, Transaction } from '#lib/types.js';

/** Fixed-width desktop grid shared by the column header and every transaction row:
 * date · description · category · account · amount. */
export const ROW_GRID =
	'md:grid-cols-[5.25rem_minmax(0,1fr)_10.5rem_9rem_9rem] lg:grid-cols-[5.75rem_minmax(0,1fr)_13rem_11rem_10rem]';

export interface DayTotal {
	currency: string;
	total: Amount;
}

export interface DayGroup {
	day: string;
	rows: Transaction[];
	totals: DayTotal[];
}

/** Order the currencies present in `totals`: `headerCurrency` first when it has matching rows,
 * followed by the rest sorted by matching row count descending (tie-broken alphabetically). */
export function orderCurrencies(
	totals: readonly CategoryTotal[],
	headerCurrency: string | null
): string[] {
	const counts = new Map<string, number>();
	for (const total of totals) {
		counts.set(total.currency, (counts.get(total.currency) ?? 0) + total.count);
	}
	const sorted = [...counts.entries()]
		.sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
		.map(([currency]) => currency);
	if (headerCurrency && counts.has(headerCurrency)) {
		return [headerCurrency, ...sorted.filter((code) => code !== headerCurrency)];
	}
	return sorted;
}

/** Group `rows` by `date` (or `booking_date` when `booking` is set), preserving row order,
 * with exact per-currency totals for each day's header. */
export function groupByDay(rows: readonly Transaction[], booking: boolean): DayGroup[] {
	const groups: { day: string; rows: Transaction[] }[] = [];
	for (const row of rows) {
		const day = booking ? row.booking_date : row.date;
		const last = groups.at(-1);
		if (last?.day === day) last.rows.push(row);
		else groups.push({ day, rows: [row] });
	}
	return groups.map((group) => {
		const currencies = [...new Set(group.rows.map((row) => row.currency))];
		const totals = currencies.map((currency) => ({
			currency,
			total: sum(group.rows.filter((row) => row.currency === currency).map((row) => row.amount))
		}));
		return { ...group, totals };
	});
}
