// The ranges net worth is looked at over, and how much it moved over one. Each range ends on
// the last day of the history and starts some days before it: a month is 30 days back, as the
// overview's 30-day change has always been; the year to date starts on New Year's Eve.

import { day, daysBetween } from '#lib/format/date.js';
import { difference, percentOf, sum } from '#lib/format/number.js';
import type { Amount, Arrived, Day } from '#lib/types.js';

export const RANGES = [
	{ value: '1m', label: '1M' },
	{ value: '3m', label: '3M' },
	{ value: 'ytd', label: 'YTD' },
	{ value: '1y', label: '1Y' },
	{ value: '3y', label: '3Y' },
	{ value: 'all', label: 'All' }
] as const;

export type Range = (typeof RANGES)[number]['value'];
export const DEFAULT_RANGE: Range = '1y';

const BACK: Record<Exclude<Range, 'ytd' | 'all'>, number> = {
	'1m': 30,
	'3m': 91,
	'1y': 365,
	'3y': 1095
};

/** How many days before `last` the range starts; null for all of the history. */
export function daysBack(range: Range, last: Day): number | null {
	if (range === 'all') return null;
	if (range === 'ytd') return daysBetween(`${Number(last.slice(0, 4)) - 1}-12-31`, last);
	return BACK[range];
}

/** What the change over the range is called: `Past month`, `Since 1 Aug 2026`. */
export function rangeLabel(range: Range, first: Day | null): string {
	switch (range) {
		case '1m':
			return 'Past month';
		case '3m':
			return 'Past 3 months';
		case 'ytd':
			return 'Year to date';
		case '1y':
			return 'Past year';
		case '3y':
			return 'Past 3 years';
		case 'all':
			return first ? `Since ${day(first)}` : 'Since the start';
	}
}

export interface Moved {
	before: Amount;
	/** How much moved, less what came into view within the range. */
	amount: Amount;
	/** In percent of `before` and what came in; null unless that is above zero. */
	percent: Amount | null;
	/** What accounts brought when they first counted within the range. */
	arrived: Amount;
}

/** What came into view after the `start`th day, up to the `end`th, of each of `arrivals`. */
export function arrivedBetween(arrivals: readonly Arrived[], start: number, end: number): Amount {
	return sum(
		arrivals.flatMap((arrived) =>
			Object.entries(arrived)
				.filter(([index]) => Number(index) > start && Number(index) <= end)
				.map(([, amount]) => amount)
		)
	);
}

/** How much moved from `before` to `after`, leaving out what `arrived` meanwhile: an account
 * that comes into view brings what it holds, and that is neither earned nor made. */
export function change(before: Amount, after: Amount, arrived: Amount): Moved {
	const amount = difference(difference(after, before), arrived);
	return { before, amount, percent: percentOf(amount, sum([before, arrived])), arrived };
}

/** How much `values`, one a day, moved from `back` days before their last day to it, exactly,
 * leaving out what `arrived` meanwhile; from their first day when `back` is null. Null when
 * the history is shorter than the range. */
export function moved(
	values: readonly Amount[],
	back: number | null,
	arrived: Arrived = {}
): Moved | null {
	if (!values.length) return null;
	const last = values.length - 1;
	const start = back === null ? 0 : last - back;
	if (start < 0) return null;
	return change(values[start], values[last], arrivedBetween([arrived], start, last));
}
