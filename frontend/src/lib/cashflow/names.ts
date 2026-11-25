// The names the cash flow is cut by, as the API sends them and as the page says them.

import { inOrder } from '#lib/design/data.js';
import { day } from '#lib/format/date.js';
import { t } from '#lib/i18n/index.svelte.js';
import { trailingFrom } from '#lib/investments/income.js';

/** The income groups of `income.GROUPS`, in the order they are stacked. */
export const GROUPS = ['Work', 'Investments', 'Other'] as const;

/** The colour of an income group: shades of the colour of a gain, so that what came in never
 * wears the colour of a category of spending. */
export function incomeColor(group: string): string {
	const strength = { Work: 100, Investments: 65, Other: 35 }[group] ?? 35;
	return `color-mix(in srgb, var(--gain) ${strength}%, var(--surface-panel))`;
}

/** `spending.OTHERS` and `spending.PAID_BACK`: the rest of the categories, and what the other
 * person paid back. */
export const OTHERS = 'Others';
export const PAID_BACK = 'Paid back';

/** The colour of each of the parts the bars stack: the largest categories in order, the
 * rest in the colour of the rest. */
export function partColors(parts: readonly string[]): Map<string, string> {
	const named = parts.filter((name) => name !== OTHERS && name !== PAID_BACK);
	const colors = inOrder(named);
	if (parts.includes(OTHERS)) colors.set(OTHERS, 'var(--data-other)');
	return colors;
}

/** Display label of a spending slice (`Others`, `Paid back`, or a category name). */
export function spendingPartName(name: string): string {
	if (name === OTHERS) return t('cashflow.others');
	if (name === PAID_BACK) return t('cashflow.paidBack');
	return name;
}

/** Label for an API-offered range pill (`{ key, label }`). */
export function rangePillLabel(range: { key?: string; label: string }): string {
	const id = range.key ?? range.label;
	if (id === 'ytd' || id === 'Year to date') return t('cashflow.rangeYtd');
	if (id === '12m' || id === 'Last 12 months') return t('cashflow.range12m');
	if (id === 'all' || id === 'All') return t('cashflow.rangeAll');
	return range.label;
}

/** The range as the lead says it: `this year so far`, `the last 12 months`, `2025`; falls back
 * to `since ...` / `from ... to ...` when a preset or year starts after its nominal start. */
export function rangeName(
	label: string | null,
	first: string | null,
	last: string | null,
	through: string | null = null
): string {
	const clamped = (nominal: string | null) => Boolean(first && nominal && first > nominal);
	if (label === 'ytd' || label === 'Year to date') {
		const year = (through ?? first)?.slice(0, 4);
		if (!clamped(year ? `${year}-01-01` : null)) return t('cashflow.inThisYear');
	} else if (label === '12m' || label === 'Last 12 months') {
		if (!clamped(through ? trailingFrom(through) : null)) return t('cashflow.inLast12m');
	} else if (label === 'all' || label === 'All') {
		return t('cashflow.inAll');
	} else if (label) {
		if (!clamped(/^\d{4}$/.test(label) ? `${label}-01-01` : null)) {
			return t('cashflow.inYear', { label });
		}
	}
	if (first && last && last !== through) {
		return t('cashflow.fromTo', { first: day(first), last: day(last) });
	}
	return first ? t('cashflow.sinceDay', { date: day(first) }) : t('cashflow.overMonths');
}
