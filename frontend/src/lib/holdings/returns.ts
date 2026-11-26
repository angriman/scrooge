// What each holding returned, as its row in the holdings view opens to it: over the past month,
// this year, the past year and since bought, then what its sales realised, what is not
// realised yet and the dividends it paid; every account together, and each account on its own
// when it is held in several. And the order the list can be sorted in.

import { day } from '#lib/format/date.js';
import { approximate } from '#lib/format/number.js';
import { t, type MessageKey } from '#lib/i18n/index.svelte.js';
import type { Amount, HeldInstrument, HoldingReturns, PeriodReturn } from '#lib/types.js';

/** Unique key for one held instrument across currency and asset class. */
export function holdingKey(holding: Pick<HeldInstrument, 'symbol' | 'currency' | 'asset'>): string {
	return `${holding.symbol}|${holding.currency}|${holding.asset}`;
}

/** Query parameter value for `holding`: the bare symbol when unique across `holdings`,
 * falling back to its full key when two holdings share a symbol. */
export function holdingParam(holding: HeldInstrument, holdings: readonly HeldInstrument[]): string {
	const matches = holdings.filter((item) => item.symbol === holding.symbol).length;
	return matches === 1 ? holding.symbol : holdingKey(holding);
}

/** Find the holding named by a `holding` query parameter (matching full key first, then symbol). */
export function findHolding(
	holdings: readonly HeldInstrument[],
	param: string | null
): HeldInstrument | null {
	if (!param) return null;
	return (
		holdings.find((item) => holdingKey(item) === param) ??
		holdings.find((item) => item.symbol === param) ??
		null
	);
}

export const SORTS = [
	{ value: 'value', label: 'Value' },
	{ value: 'gain', label: 'Gain' },
	{ value: 'return', label: 'Return' },
	{ value: 'year', label: '1Y' }
] as const;
export type Sort = (typeof SORTS)[number]['value'];

const SORT_KEYS: Record<Sort, MessageKey> = {
	value: 'holdings.sortValue',
	gain: 'holdings.sortGain',
	return: 'holdings.sortReturn',
	year: 'holdings.sortYear'
};

/** The sort choices in the active language. */
export function sortOptions(): { value: Sort; label: string }[] {
	return SORTS.map((sort) => ({ value: sort.value, label: t(SORT_KEYS[sort.value]) }));
}

const PERIOD_KEYS: Record<PeriodReturn['range'], MessageKey> = {
	'1m': 'holdings.period1m',
	ytd: 'holdings.periodYtd',
	'1y': 'holdings.period1y',
	all: 'holdings.periodAll'
};

/** What a range is called in the table. */
export function periodLabel(range: PeriodReturn['range']): string {
	return t(PERIOD_KEYS[range]);
}

/** What it returned over the past year; null when it was not held all year. */
export function yearOf(returns: HoldingReturns | null): PeriodReturn | null {
	const year = returns?.periods.find((period) => period.range === '1y');
	return year?.percent ? year : null;
}

/** The return over the past year, in percent; null when it was not held all year. */
export function yearReturn(returns: HoldingReturns | null): Amount | null {
	return yearOf(returns)?.percent ?? null;
}

// What is not known goes last, whatever the order.
const rank = (value: Amount | null) => (value === null ? -Infinity : approximate(value));

/** The holdings in the order asked: by value as they come, else the most first. */
export function sortHoldings(holdings: HeldInstrument[], sort: Sort): HeldInstrument[] {
	if (sort === 'value') return holdings;
	const by = (holding: HeldInstrument) =>
		sort === 'gain'
			? holding.gain
			: sort === 'return'
				? holding.return_percent
				: yearReturn(holding.returns);
	return [...holdings].sort((a, b) => rank(by(b)) - rank(by(a)));
}

/** A column of the table: every account together, or one of them. */
export interface Column {
	label: string;
	returns: HoldingReturns | null;
}

/** The columns: the holding alone when it is in one account; otherwise every account
 * together first (`All`), then each account, named with whose it is when several people's are listed. */
export function columnsOf(holding: HeldInstrument, people: boolean): Column[] {
	if (holding.positions.length < 2) {
		return [{ label: t('holdings.sortReturn'), returns: holding.returns }];
	}
	return [
		{ label: t('holdings.allColumns'), returns: holding.returns },
		...holding.positions.map((position) => ({
			label: people ? `${position.account}, ${position.owner}` : position.account,
			returns: position.returns
		}))
	];
}

/** One line of the table: a range, with a percent and an amount in each column. */
export interface Line {
	label: string;
	/** Said under the label: since when, for since bought. */
	note: string;
	cells: { percent: Amount | null; amount: Amount | null }[];
}

/** The ranges, each as a line; since bought says the day it counts from when every column
 * counts from the same one. */
export function periodLines(columns: Column[]): Line[] {
	const ranges = columns.find((column) => column.returns)?.returns?.periods ?? [];
	return ranges.map(({ range }) => {
		const found = columns.map((column) =>
			column.returns?.periods.find((period) => period.range === range)
		);
		const firsts = new Set(found.map((period) => period?.first).filter(Boolean));
		const [first] = firsts;
		return {
			label: periodLabel(range),
			note:
				range === 'all' && firsts.size === 1 && first
					? t('holdings.fromDate', { date: day(first) })
					: '',
			cells: found.map((period) => ({
				percent: period?.percent ?? null,
				amount: period?.amount ?? null
			}))
		};
	});
}

/** What is said under the dividends: what was withheld from them, when anything was. */
export function withheldNote(
	returns: HoldingReturns | null,
	written: (amount: Amount) => string
): string {
	if (!returns || !/[1-9]/.test(returns.tax)) return '';
	return t('holdings.withheldAmount', { amount: written(returns.tax.replace(/^-/, '')) });
}

/** What realised says: `none sold` when nothing of it was ever sold, else the amount; null
 * when it is not known. */
export function realisedText(
	returns: HoldingReturns | null,
	written: (amount: Amount) => string
): string | null {
	if (!returns) return null;
	if (!returns.sold) return t('holdings.noneSold');
	return returns.realised === null ? null : written(returns.realised);
}

/** What is said under the table about its dashes: which columns are too small for any return,
 * and that a dash elsewhere is a range it was not held through. */
export function dashNotes(columns: Column[], lines: Line[]): string[] {
	const small = columns.filter((column) => column.returns?.too_small);
	const notes = [];
	if (small.length) {
		notes.push(
			small.length === columns.length
				? t('holdings.tooSmallAll')
				: t('holdings.tooSmallAccount', {
						account: small.map((column) => column.label).join(', ')
					})
		);
	}
	const unheld = lines.some((line) =>
		line.cells.some((cell, n) => cell.percent === null && !columns[n].returns?.too_small)
	);
	if (unheld) notes.push(t('holdings.unheldNote'));
	return notes;
}
