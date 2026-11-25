// The filter of the transactions page, as its address has it: the fields `/api/transactions`
// reads, what each is called on a chip, and the periods a chip offers.

import { day, month } from '#lib/format/date.js';
import type { Address } from '#lib/view.js';

/** The fields of the address the API filters by (`owner` is the header's). */
export const FIELDS = [
	'text',
	'from',
	'to',
	'booking',
	'account',
	'accounts',
	'category',
	'merchant',
	'kind',
	'min',
	'max'
] as const;

/** What a kind of row is called where it is chosen. */
const KINDS: Readonly<Record<string, string>> = {
	expense: 'Expenses',
	income: 'Income',
	transfer: 'Transfers',
	internal: 'Between own accounts',
	fx: 'Currency exchange',
	trading: 'Trading',
	security_trade: 'Buying and selling securities',
	commission: 'Fees',
	interest: 'Interest',
	dividend: 'Dividends',
	tax: 'Taxes'
};

export function kindName(kind: string): string {
	if (kind.includes(',')) return kind.split(',').map(kindName).join(', ');
	return KINDS[kind] ?? kind.charAt(0).toUpperCase() + kind.slice(1).replaceAll('_', ' ');
}

/** The kinds of account the rows can be cut to (`accounts`), as the API names them
 * (`query.ACCOUNTS`), and what each is called on its chip. */
export const ACCOUNT_KINDS = [
	{ value: 'broker', label: 'Broker accounts' },
	{ value: 'bank', label: 'Bank accounts' },
	{ value: 'card', label: 'Cards' }
] as const;

export function accountsName(value: string): string {
	return ACCOUNT_KINDS.find((kind) => kind.value === value)?.label ?? value;
}

/** What the transactions can be cut to that a context is shown for, above the list. */
export const DIMENSIONS = ['category', 'merchant', 'account'] as const;

/** The one dimension the filter cuts the transactions to, with its value; null when it cuts
 * them to none of them, or to more than one. */
export function dimension(
	url: Address
): { name: (typeof DIMENSIONS)[number]; value: string } | null {
	const set = DIMENSIONS.filter((name) => url.searchParams.get(name));
	return set.length === 1 ? { name: set[0], value: url.searchParams.get(set[0]) ?? '' } : null;
}

/** Whether any of the filter's fields is set: the page shows everything otherwise. */
export function filtered(url: Address): boolean {
	return FIELDS.some((name) => url.searchParams.get(name));
}

export interface Period {
	label: string;
	from: string;
	to: string;
}

const pad = (n: number) => String(n).padStart(2, '0');

/** The periods offered, counted from `today` (ISO): its month, the one before, its year, the
 * one before. `from` and `to` are written as the API reads them: a month, a year. */
export function periods(today: string): Period[] {
	const year = Number(today.slice(0, 4));
	const at = Number(today.slice(5, 7));
	const thisMonth = today.slice(0, 7);
	const before = at === 1 ? `${year - 1}-12` : `${year}-${pad(at - 1)}`;
	const twelve = at === 12 ? `${year}-01` : `${year - 1}-${pad(at + 1)}`;
	return [
		{ label: 'This month', from: thisMonth, to: thisMonth },
		{ label: 'Last month', from: before, to: before },
		{ label: 'Last 12 months', from: twelve, to: thisMonth },
		{ label: 'This year', from: String(year), to: String(year) },
		{ label: 'Last year', from: String(year - 1), to: String(year - 1) }
	];
}

/** `2026-09` as `Sep 2026`, `2026` as itself, a day as `15 Sep 2026`. */
function written(text: string): string {
	if (/^\d{4}$/.test(text)) return text;
	if (/^\d{4}-\d{2}$/.test(text)) return month(`${text}-01`);
	return /^\d{4}-\d{2}-\d{2}$/.test(text) ? day(text) : text;
}

/** What the period chip says: a period it offers by its name, any other by its ends. */
export function periodName(from: string, to: string, today: string): string | null {
	if (!from && !to) return null;
	const offered = periods(today).find((period) => period.from === from && period.to === to);
	if (offered) return offered.label;
	if (from === to) return written(from);
	if (!to) return `Since ${written(from)}`;
	if (!from) return `Up to ${written(to)}`;
	return `${written(from)} to ${written(to)}`;
}

/** What the API sums the rows without a category as (`query.TRANSFERS`): money between one's
 * own accounts, which is neither spent nor earned. */
export const TRANSFERS = 'Transfers';
