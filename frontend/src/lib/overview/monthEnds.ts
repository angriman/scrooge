// The month-end table of net worth: a row per month, newest first, and for each currency a
// group of columns. What is written in each cell and which way it went is worked out here; the
// table only lays it out (MonthEnds.svelte).

import { month, monthName } from '#lib/format/date.js';
import { MINUS, money, percent, signed, signedPercent } from '#lib/format/number.js';
import type { MonthEndFigure, NetWorthMonths } from '#lib/types.js';

export interface Column {
	key: keyof MonthEndFigure;
	label: string;
}

/** The columns of each currency, in order. */
export const COLUMNS = [
	{ key: 'total', label: 'Net worth' },
	{ key: 'change', label: 'Change' },
	{ key: 'percent', label: 'Change %' }
] as const satisfies readonly Column[];

/** What of net worth can be reached and what is frozen: after the columns of the header's
 * currency alone, so that the table stays narrow. */
export const SPLIT = [
	{ key: 'liquid', label: 'Liquid' },
	{ key: 'frozen', label: 'Frozen' }
] as const satisfies readonly Column[];

/** What accounts held when they were first counted in a month, left out of its change: after
 * the change of the header's currency alone, and only when some month has any. */
export const ARRIVED = { key: 'arrived', label: 'Came into view' } as const satisfies Column;

/** What a change leaves out, said under the table and on its change columns: a user taking
 * one month's total from the next's would otherwise think the table wrong. */
export const CHANGE_NOTE = 'Change leaves out what accounts held when they were first imported.';

/** The same said when the table shows what came into view: then the two add up, to the
 * cent; rounded to whole units each, as the table shows them, they can be 1 apart. */
export const ARRIVED_NOTE =
	'Change leaves out what came into view, what accounts held when they were first imported: to the cent, a month’s total change is the two added up.';

/** What counts as frozen, said under the table and on the columns of the split. */
export const FROZEN_NOTE =
	'Frozen is Pillar 2, Pillar 3a and the accounts scrooge.yaml names frozen; the rest is liquid.';

/** What a column's heading says when pointed at, if anything. */
export function noteOf(key: Column['key']): string | undefined {
	if (key === 'change' || key === 'percent') return CHANGE_NOTE;
	if (key === 'arrived') return ARRIVED_NOTE;
	if (key === 'liquid' || key === 'frozen') return FROZEN_NOTE;
	return undefined;
}

/** Which way a figure went: its colour, `gain` or `loss`; `flat` and `none` stay plain. */
export type Way = 'gain' | 'loss' | 'flat' | 'none';

export interface Cell {
	text: string;
	way: Way;
}

export interface Row {
	/** `2026-10`, to key the row. */
	month: string;
	/** `October 2026`, and `Oct 2026` for a phone. */
	label: string;
	short: string;
	/** The month is not over: its figures are to `day`. */
	partial: boolean;
	day: string;
	/** One group for each of the table's currencies, in their order. */
	groups: Cell[][];
}

/** The currency shown in the header first, then the others in the order of the config. */
export function inOrder(currencies: readonly string[], shown: string): string[] {
	return currencies.includes(shown)
		? [shown, ...currencies.filter((currency) => currency !== shown)]
		: [...currencies];
}

/** What a signed figure's text says of its way: `+12` up, `−12` down, `+0` neither. */
function wayOf(written: string): Way {
	if (written.startsWith(MINUS)) return 'loss';
	return /^\+0(\.0*)?%?$/.test(written) ? 'flat' : 'gain';
}

/** A change written as it reads, with a plain zero when it rounds to nothing. */
function moved(value: string | null, write: (text: string) => string, zero: string): Cell {
	if (value === null) return { text: '–', way: 'none' };
	const written = write(value);
	const way = wayOf(written);
	return { text: way === 'flat' ? zero : written, way };
}

/** The cell of `figure` in the column `key`. What came into view is neither a gain nor a loss:
 * it is written plain, like a total. */
function cellOf(figure: MonthEndFigure, key: Column['key']): Cell {
	if (key === 'change') return moved(figure.change, (text) => signed(text, 0), '0');
	if (key === 'percent') return moved(figure.percent, (text) => signedPercent(text), percent('0'));
	const value = figure[key];
	return { text: value === null ? '–' : money(value, 0), way: 'none' };
}

/** The cells of one currency's group, a cell for each of `columns`. */
export function cells(
	figure: MonthEndFigure | undefined,
	columns: readonly Column[] = COLUMNS
): Cell[] {
	return columns.map((column) =>
		figure ? cellOf(figure, column.key) : { text: '–', way: 'none' }
	);
}

/** Whether something came into view in `currency` in any month sent, as the table writes it
 * (to whole units): otherwise the column of what did would be zeros and dashes only. */
export function anyArrived(sent: NetWorthMonths, currency: string): boolean {
	return sent.months.some((end) => {
		const arrived = end.values[currency]?.arrived;
		// A few cents are written `0` (or `−0`): no digit but zeros.
		return arrived != null && /[1-9]/.test(money(arrived, 0));
	});
}

/** The table of what the API sent, with `shown` (the header's currency) leading: it alone also
 * has what came into view, when anything did, and is split into what is liquid and what frozen.
 * `columns` are each currency's, in the order of `currencies`; `note` says what a change leaves
 * out. */
export function table(
	sent: NetWorthMonths,
	shown: string
): { currencies: string[]; columns: Column[][]; rows: Row[]; note: string } {
	const currencies = inOrder(sent.currencies, shown);
	const arrived = anyArrived(sent, shown);
	const columns = currencies.map((currency): Column[] =>
		currency === shown ? [...COLUMNS, ...(arrived ? [ARRIVED] : []), ...SPLIT] : [...COLUMNS]
	);
	return {
		currencies,
		columns,
		note: arrived ? ARRIVED_NOTE : CHANGE_NOTE,
		rows: sent.months.map((end) => ({
			month: end.month,
			label: monthName(end.month),
			short: month(`${end.month}-01`),
			partial: end.partial,
			day: end.day,
			groups: currencies.map((currency, n) => cells(end.values[currency], columns[n]))
		}))
	};
}
