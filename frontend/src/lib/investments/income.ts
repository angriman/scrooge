// What investments paid, as the income view of the investments page shows it: the figures the
// API sends added up, the columns of the chart, and the transactions behind a holding.

import { month as monthOf } from '#lib/format/date.js';
import { approximate, difference, negate, percentOf, sum } from '#lib/format/number.js';
import type { PayingHolding, Payout } from '#lib/types.js';

/** The colour of dividends and of interest: shades of the colour of a gain, as income wears
 * on the cash flow. */
export const DIVIDENDS_COLOR = 'var(--gain)';
export const INTEREST_COLOR = 'color-mix(in srgb, var(--gain) 45%, var(--surface-panel))';

/** Dividends and interest, before tax. */
export function paid(payout: Payout): string {
	return sum([payout.dividends, payout.interest]);
}

/** What was withheld, as a positive amount: the API sends tax as it is stored, negative. */
export function withheld(tax: string): string {
	return sum([negate(tax)]);
}

/** How much more (or less, below zero) was paid than in the same months a year before, in
 * percent; null when there is nothing to compare with, or nothing was paid then. */
export function change(now: Payout, before: Payout | null): string | null {
	if (!before) return null;
	const then = paid(before);
	return percentOf(difference(paid(now), then), then, 1);
}

/** What a column of the chart is called: `Mar`, of `2026-03`. */
export function monthLabel(name: string): string {
	return monthOf(`${name}-01`).slice(0, 3);
}

/** The heights of a month's column, dividends below interest, of `scale` per unit of money.
 * What was charged (interest below zero) draws nothing. */
export function stack(payout: Payout, scale: number): { dividends: number; interest: number } {
	return {
		dividends: Math.max(0, approximate(payout.dividends)) * scale,
		interest: Math.max(0, approximate(payout.interest)) * scale
	};
}

/** The highest column of `months`; 1 when nothing was paid, so that nothing is divided by 0. */
export function highest(months: readonly Payout[]): number {
	return Math.max(
		1,
		...months.map(
			(month) => Math.max(0, approximate(month.dividends)) + Math.max(0, approximate(month.interest))
		)
	);
}

/** The kind of rows a holding is mostly: its dividends, else its interest, else the tax
 * withheld from it. */
export function kindOf(holding: PayingHolding): 'dividend' | 'interest' | 'tax' {
	if (approximate(holding.dividends) !== 0) return 'dividend';
	return approximate(holding.interest) !== 0 ? 'interest' : 'tax';
}

/** The filter of the transactions behind a holding over the range: its kind, and the name its
 * rows give, or the account of those that name nothing. */
export function transactionsOf(
	holding: PayingHolding,
	range: { first: string | null; last: string | null },
	owner: string | null
): URLSearchParams {
	const query = new URLSearchParams();
	if (owner) query.set('owner', owner);
	if (range.first) query.set('from', range.first);
	if (range.last) query.set('to', range.last);
	query.set('kind', kindOf(holding));
	if (holding.account) query.set('account', holding.account);
	else query.set('text', holding.key);
	return query;
}

/** The range chosen of those offered, by its ends; none asked is the last twelve months. */
export function chosenRange(
	ranges: readonly { label: string; from: string; to: string }[],
	from: string,
	to: string
): string {
	if (!from && !to) return DEFAULT_RANGE;
	return ranges.find((range) => range.from === from && range.to === to)?.label ?? '';
}

export const DEFAULT_RANGE = 'Last 12 months';
