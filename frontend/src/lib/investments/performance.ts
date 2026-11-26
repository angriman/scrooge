// What the investments returned, as the performance view of the investments page shows it:
// the range asked for, what it is called, the lines of the chart (what the accounts were
// worth, what had been put in, and what that would be worth in each benchmark), and how the
// return compares with the benchmarks'.

import { addDays, day, daysBetween } from '#lib/format/date.js';
import { approximate, money, signedPercent } from '#lib/format/number.js';
import { joinList, t } from '#lib/i18n/index.svelte.js';
import { daysBack, RANGES, rangeOptions, type Range } from '#lib/overview/range.js';
import type {
	Amount,
	BenchmarkReturn,
	Day,
	InvestedDay,
	Measured,
	MeasuredAccount
} from '#lib/types.js';

export { RANGES, rangeOptions, type Range };

/** `performance.DEFAULT_RANGE`: the year to date, unless another range is asked. */
export const DEFAULT_RANGE: Range = 'ytd';

/** The range in an address, `ytd` when it names none the overview offers. */
export function rangeOf(asked: string | null): Range {
	const value = asked?.toLowerCase();
	return RANGES.some((range) => range.value === value) ? (value as Range) : DEFAULT_RANGE;
}

/** What the time-weighted return is called after a figure: `this year`, `over the past month`;
 * `since 1 Sep 2026` when it counts from a later day (money was first put in later, or the
 * history starts after the range would). */
export function over(range: Range, first: Day, last: Day, since: Day | null = null): string {
	if (since && since > first) return t('investments.sinceDay', { date: day(since) });
	const back = daysBack(range, last);
	if (back === null || addDays(last, 1 - back) !== first) {
		return t('investments.sinceDay', { date: day(first) });
	}
	switch (range) {
		case '1m':
			return t('investments.overPastMonth');
		case '3m':
			return t('investments.overPast3Months');
		case 'ytd':
			return t('investments.thisYear');
		case '1y':
			return t('investments.overPastYear');
		case '3y':
			return t('investments.overPast3Years');
	}
	return t('investments.sinceDay', { date: day(first) });
}

/** Secondary note when the time-weighted return starts counting after the range's first day:
 * names the range's start and explains why the return starts later. Null when they match. */
export function twrSinceNote(first: Day | null, since: Day | null): string | null {
	if (!first || !since || since <= first) return null;
	return t('investments.twrSinceNote', { start: day(first), date: day(since) });
}

/** What is said under an account's name: whose it is, when both people's are listed; the
 * accounts it is made of, when it is several; and the day its time-weighted return counts from,
 * when that is not the one of the whole (`since`). */
export function described(row: MeasuredAccount, people: boolean, since: Day | null = null): string {
	const parts = [
		people ? row.owner : '',
		row.accounts.length > 1 ? row.accounts.join(', ') : '',
		row.twr_since && row.twr_since !== since
			? t('investments.returnFrom', { date: day(row.twr_since) })
			: ''
	];
	return parts.filter(Boolean).join(' · ');
}

/** What the money-weighted return is over: `a year` from a year up, `over the period` below. */
export function mwrPer(measured: Pick<Measured, 'mwr_a_year'>): string {
	return t(measured.mwr_a_year ? 'investments.mwrYear' : 'investments.mwrPeriod');
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
	/** The path of each benchmark's line, in the order of the series' `benchmarks`. */
	benchmarks: string[];
	/** The value line closed down to the bottom of the plot, to fill under it. */
	area: string;
	/** The lowest and highest amounts the plot spans. */
	low: number;
	high: number;
	/** Where each day of the series is across. */
	xs: number[];
	y: (amount: number) => number;
}

/** The lines of `series` in `frame`, across by day (the first, the day before the range, at
 * the left edge) and up by amount, every line on one scale with a little room above and below.
 * Null without two days to draw between. */
export function plot(series: readonly InvestedDay[], frame: Frame): Plot | null {
	if (series.length < 2) return null;
	const values = series.map((point) => approximate(point.value));
	const invested = series.map((point) => approximate(point.invested));
	const benchmarks = series[0].benchmarks.map((_, index) =>
		series.map((point) => approximate(point.benchmarks[index]))
	);
	const every = [...values, ...invested, ...benchmarks.flat()];
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
		benchmarks: benchmarks.map(line),
		area: `${value}L${round(xs.at(-1)!)},${bottom}L${round(xs[0])},${bottom}Z`,
		low,
		high,
		xs,
		y
	};
}

/** How the benchmarks did over the days the time-weighted return counts, to set beside it:
 * `VT +12.3%, VOO +15.0% over the same period`. Null without a return or a benchmark. */
export function compared(
	twr: Amount | null,
	benchmarks: readonly BenchmarkReturn[]
): string | null {
	if (twr === null || !benchmarks.length) return null;
	const each = benchmarks.map(
		(benchmark) => `${benchmark.symbol} ${signedPercent(benchmark.return)}`
	);
	return t('investments.benchmarksCompared', { list: each.join(', ') });
}

/** What the chart's caption says of the benchmarks' lines, after what it says of the others;
 * nothing without a benchmark. */
export function instead(benchmarks: readonly BenchmarkReturn[]): string {
	const symbols = benchmarks.map((benchmark) => benchmark.symbol);
	if (!symbols.length) return '';
	if (symbols.length === 1) return t('investments.benchmarkSingle', { symbol: symbols[0] });
	const rest = symbols.slice(1).map((symbol) => t('investments.inSymbolPhrase', { symbol }));
	const items = [symbols[0], ...rest];
	return t('investments.benchmarkMulti', {
		first: items.slice(0, -1).join(', '),
		last: items[items.length - 1]
	});
}

/** A single quiet note naming the benchmarks without stored closes or rates for this range.
 * Null when every benchmark asked for is shown (or none are configured). */
export function missingBenchmarksNote(missing: readonly string[]): string | null {
	if (!missing.length) return null;
	return t(
		missing.length === 1 ? 'investments.benchmarksMissingOne' : 'investments.benchmarksMissingMany',
		{ benchmarks: joinList(missing) }
	);
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
