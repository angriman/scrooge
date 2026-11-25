// The cash flow as a table: one row per period, oldest first, and one for the whole range.

import { month } from '#lib/format/date.js';
import { percent } from '#lib/format/number.js';
import { t } from '#lib/i18n/index.svelte.js';
import type { Amount, CashFlowPeriod, Saved } from '#lib/types.js';

/** One line of the table: what came in, the salary within it, what went out, what was kept,
 * and what was kept as a part of income and of salary (`–` where there is nothing to divide
 * by). */
export interface Row {
	key: string;
	period: string;
	income: Amount;
	salary: Amount;
	spent: Amount;
	saved: Amount;
	rate: string;
	rateSalary: string;
}

/** A period as the table says it: `Sep 2026`, `Q3 2026`, `2026`. */
export function periodName(name: string): string {
	if (/^\d{4}-\d{2}$/.test(name)) return month(`${name}-01`);
	const quarter = /^(\d{4})-(Q\d)$/.exec(name);
	return quarter ? `${quarter[2]} ${quarter[1]}` : name;
}

function rate(value: Amount | null): string {
	return value === null ? '–' : percent(value, 1);
}

function row(key: string, period: string, totals: Saved): Row {
	return {
		key,
		period,
		income: totals.income,
		salary: totals.work,
		spent: totals.spent,
		saved: totals.saved,
		rate: rate(totals.rate),
		rateSalary: rate(totals.rate_salary)
	};
}

/** The periods of the chart, oldest first. */
export function periodRows(periods: readonly CashFlowPeriod[]): Row[] {
	return [...periods]
		.sort((a, b) => a.first.localeCompare(b.first))
		.map((period) => row(period.name, periodName(period.name), period));
}

/** The whole range, for the foot of the table. */
export function totalRow(totals: Saved): Row {
	return row('total', t('cashflow.inAllWord'), totals);
}
