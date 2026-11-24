// What the investments returned, as the performance view of the investments page shows it:
// the range asked for, what it is called, and the two lines of the chart, what the accounts
// were worth and what had been put in.

import { addDays, day, daysBetween } from '#lib/format/date.js';
import { approximate, money } from '#lib/format/number.js';
import { daysBack, RANGES, type Range } from '#lib/overview/range.js';
import type { Amount, Day, InvestedDay, Measured, MeasuredAccount } from '#lib/types.js';

export { RANGES, type Range };

/** `performance.DEFAULT_RANGE`: the year to date, unless another range is asked. */
export const DEFAULT_RANGE: Range = 'ytd';

/** The range in an address, `ytd` when it names none the overview offers. */
export function rangeOf(asked: string | null): Range {
	const value = asked?.toLowerCase();
	return RANGES.some((range) => range.value === value) ? (value as Range) : DEFAULT_RANGE;
}

/** What the range is called after a figure: `this year`, `over the past month`; `since 1 Sep
 * 2026` when the history starts after the range would. */
export function over(range: Range, first: Day, last: Day): string {
	const back = daysBack(range, last);
	if (back === null || addDays(last, 1 - back) !== first) return `since ${day(first)}`;
	switch (range) {
		case '1m':
			return 'over the past month';
		case '3m':
			return 'over the past 3 months';
		case 'ytd':
			return 'this year';
		case '1y':
			return 'over the past year';
		case '3y':
			return 'over the past 3 years';
	}
	return `since ${day(first)}`;
}

/** What is said under an account's name: whose it is, when both people's are listed, and the
 * accounts it is made of, when it is several. */
export function described(row: MeasuredAccount, people: boolean): string {
	const parts = [people ? row.owner : '', row.accounts.length > 1 ? row.accounts.join(', ') : ''];
	return parts.filter(Boolean).join(' · ');
}

/** What the money-weighted return is over: `a year` from a year up, `over the period` below. */
export function mwrPer(measured: Pick<Measured, 'mwr_a_year'>): string {
	return measured.mwr_a_year ? 'a year' : 'over the period';
}

/** Which way a figure went, as its rounding writes it: a change that rounds to nothing is flat. */
export function direction(value: Amount | null, places = 0): 'up' | 'down' | 'flat' {
	if (value === null || !/[1-9]/.test(money(value, places))) return 'flat';
	return value.trim().startsWith('-') ? 'down' : 'up';
}

/** Where the chart draws: its size, and the room left at each side. */
export interface Frame {
	width: number;
	height: number;
	top: number;
	right: number;
	bottom: number;
	left: number;
}

export interface Plot {
	/** The paths of the two lines, in the frame's coordinates. */
	value: string;
	invested: string;
	/** The value line closed down to the bottom of the plot, to fill under it. */
	area: string;
	/** The lowest and highest amounts the plot spans. */
	low: number;
	high: number;
	/** Where each day of the series is across. */
	xs: number[];
	y: (amount: number) => number;
}

/** The two lines of `series` in `frame`, across by day (the first, the day before the range,
 * at the left edge) and up by amount, both lines on one scale with a little room above and
 * below. Null without two days to draw between. */
export function plot(series: readonly InvestedDay[], frame: Frame): Plot | null {
	if (series.length < 2) return null;
	const values = series.map((point) => approximate(point.value));
	const invested = series.map((point) => approximate(point.invested));
	const every = [...values, ...invested];
	let [low, high] = [Math.min(...every), Math.max(...every)];
	const room = (high - low) * 0.08 || Math.abs(high) * 0.08 || 1;
	[low, high] = [low - room, high + room];
	if (Math.min(...every) >= 0) low = Math.max(0, low);
	const span = daysBetween(series[0].day, series.at(-1)!.day) || 1;
	const across = frame.width - frame.left - frame.right;
	const down = frame.height - frame.top - frame.bottom;
	const xs = series.map(
		(point) => frame.left + (daysBetween(series[0].day, point.day) / span) * across
	);
	const y = (amount: number) => frame.top + ((high - amount) / (high - low)) * down;
	const line = (amounts: number[]) =>
		amounts
			.map((amount, index) => `${index ? 'L' : 'M'}${round(xs[index])},${round(y(amount))}`)
			.join('');
	const bottom = round(frame.top + down);
	const value = line(values);
	return {
		value,
		invested: line(invested),
		area: `${value}L${round(xs.at(-1)!)},${bottom}L${round(xs[0])},${bottom}Z`,
		low,
		high,
		xs,
		y
	};
}

/** The day of `xs` nearest `x`: what the pointer is over. */
export function nearest(xs: readonly number[], x: number): number {
	let found = 0;
	for (let index = 1; index < xs.length; index++) {
		if (Math.abs(xs[index] - x) < Math.abs(xs[found] - x)) found = index;
	}
	return found;
}

function round(value: number): number {
	return Math.round(value * 10) / 10;
}
