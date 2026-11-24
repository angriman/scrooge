// The month-end table of net worth: a row per month, newest first, and for each currency a
// group of columns. What is written in each cell and which way it went is worked out here; the
// table only lays it out (MonthEnds.svelte).

import { month, monthName } from '#lib/format/date.js';
import { MINUS, money, percent, signed, signedPercent } from '#lib/format/number.js';
import type { MonthEndFigure, NetWorthMonths } from '#lib/types.js';

/** The columns of each currency, in order. Liquid and frozen net worth would join them here,
 * once the API sends them in each figure. */
export const COLUMNS = [
	{ key: 'total', label: 'Net worth' },
	{ key: 'change', label: 'Change' },
	{ key: 'percent', label: 'Change %' }
] as const satisfies readonly { key: keyof MonthEndFigure; label: string }[];

/** What a change leaves out, said under the table and on its change columns: a user taking
 * one month's total from the next's would otherwise think the table wrong. */
export const CHANGE_NOTE = 'Change leaves out what accounts held when they were first imported.';

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

/** The cells of one currency's group, in the order of `COLUMNS`. */
export function cells(figure: MonthEndFigure | undefined): Cell[] {
	if (!figure) return COLUMNS.map(() => ({ text: '–', way: 'none' }));
	return [
		{ text: money(figure.total, 0), way: 'none' },
		moved(figure.change, (text) => signed(text, 0), '0'),
		moved(figure.percent, (text) => signedPercent(text), percent('0'))
	];
}

/** The table of what the API sent, with `shown` (the header's currency) leading. */
export function table(sent: NetWorthMonths, shown: string): { currencies: string[]; rows: Row[] } {
	const currencies = inOrder(sent.currencies, shown);
	return {
		currencies,
		rows: sent.months.map((end) => ({
			month: end.month,
			label: monthName(end.month),
			short: month(`${end.month}-01`),
			partial: end.partial,
			day: end.day,
			groups: currencies.map((currency) => cells(end.values[currency]))
		}))
	};
}
