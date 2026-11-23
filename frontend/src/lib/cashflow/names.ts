// The names the cash flow is cut by, as the API sends them and as the page says them.

import { inOrder } from '#lib/design/data.js';
import { day } from '#lib/format/date.js';

/** The income groups of `income.GROUPS`, in the order they are stacked. */
export const GROUPS = ['Lavoro', 'Investimenti', 'Altro'] as const;

const GROUP_NAMES: Readonly<Record<string, string>> = {
	Lavoro: 'Work',
	Investimenti: 'Investments',
	Altro: 'Other'
};

/** The colour of an income group: shades of the colour of a gain, so that what came in never
 * wears the colour of a category of spending. */
export function incomeColor(group: string): string {
	const strength = { Lavoro: 100, Investimenti: 65, Altro: 35 }[group] ?? 35;
	return `color-mix(in srgb, var(--gain) ${strength}%, var(--surface-panel))`;
}

/** What an income group is called on the page. */
export function groupName(group: string): string {
	return GROUP_NAMES[group] ?? group;
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

/** The range as the lead says it: `this year so far`, `the last 12 months`, `2025`. */
export function rangeName(label: string | null, first: string | null, last: string | null): string {
	if (label === 'Year to date') return 'this year so far';
	if (label === 'Last 12 months') return 'over the last 12 months';
	if (label === 'All') return 'since the first statement';
	if (label) return `in ${label}`;
	if (first && last) return `from ${day(first)} to ${day(last)}`;
	return first ? `since ${day(first)}` : 'over these months';
}
