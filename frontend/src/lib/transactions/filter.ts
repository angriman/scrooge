// The filter of the transactions page, as its address has it: the fields `/api/transactions`
// reads, what each is called on a chip, and the periods a chip offers.

import { day, month } from '#lib/format/date.js';
import { t, type MessageKey } from '#lib/i18n/index.svelte.js';
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
const KIND_KEYS: Readonly<Record<string, MessageKey>> = {
	expense: 'transactions.kindExpense',
	income: 'transactions.kindIncome',
	transfer: 'transactions.kindTransfer',
	internal: 'transactions.kindInternal',
	fx: 'transactions.kindFx',
	trading: 'transactions.kindTrading',
	security_trade: 'transactions.kindSecurityTrade',
	commission: 'transactions.kindCommission',
	interest: 'transactions.kindInterest',
	dividend: 'transactions.kindDividends',
	tax: 'transactions.kindTax'
};

export function kindName(kind: string): string {
	if (kind.includes(',')) return kind.split(',').map(kindName).join(', ');
	return kind in KIND_KEYS
		? t(KIND_KEYS[kind])
		: kind.charAt(0).toUpperCase() + kind.slice(1).replaceAll('_', ' ');
}

/** The kinds of account the rows can be cut to (`accounts`), as the API names them
 * (`query.ACCOUNTS`), and what each is called on its chip. */
export const ACCOUNT_KINDS = [
	{ value: 'broker', label: 'Broker accounts' },
	{ value: 'bank', label: 'Bank accounts' },
	{ value: 'card', label: 'Cards' }
] as const;

const ACCOUNT_KIND_KEYS: Readonly<Record<(typeof ACCOUNT_KINDS)[number]['value'], MessageKey>> = {
	broker: 'transactions.accountsBroker',
	bank: 'transactions.accountsBank',
	card: 'transactions.accountsCard'
};

/** The account kind filter choices in the active language. */
export function accountKindOptions(): { value: string; label: string }[] {
	return ACCOUNT_KINDS.map((kind) => ({
		value: kind.value,
		label: t(ACCOUNT_KIND_KEYS[kind.value])
	}));
}

export function accountsName(value: string): string {
	return value in ACCOUNT_KIND_KEYS
		? t(ACCOUNT_KIND_KEYS[value as keyof typeof ACCOUNT_KIND_KEYS])
		: value;
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
		{ label: t('transactions.periodThisMonth'), from: thisMonth, to: thisMonth },
		{ label: t('transactions.periodLastMonth'), from: before, to: before },
		{ label: t('transactions.periodLast12m'), from: twelve, to: thisMonth },
		{ label: t('transactions.periodThisYear'), from: String(year), to: String(year) },
		{ label: t('transactions.periodLastYear'), from: String(year - 1), to: String(year - 1) }
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
	if (!to) return t('transactions.periodSince', { date: written(from) });
	if (!from) return t('transactions.periodUpTo', { date: written(to) });
	return t('transactions.periodFromTo', { from: written(from), to: written(to) });
}

/** What the API sums the rows without a category as (`query.TRANSFERS`): money between one's
 * own accounts, which is neither spent nor earned. */
export const TRANSFERS = 'Transfers';
