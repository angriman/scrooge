// What investments paid, as the income view of the investments page shows it: the figures the
// API sends added up, the columns of the chart, and the transactions behind a holding.

import { month as monthOf } from '#lib/format/date.js';
import { approximate, difference, negate, percentOf, sum } from '#lib/format/number.js';
import type { PayingHolding, Payout, PayoutsReport, Person } from '#lib/types.js';

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
			(month) =>
				Math.max(0, approximate(month.dividends)) + Math.max(0, approximate(month.interest))
		)
	);
}

/** The kinds of rows a holding has: dividends, interest and tax withheld, those it has
 * anything of; all three when everything adds up to nothing. */
export function kindsOf(holding: PayingHolding): ('dividend' | 'interest' | 'tax')[] {
	const all = [
		['dividend', holding.dividends],
		['interest', holding.interest],
		['tax', holding.tax]
	] as const;
	const kinds = all.filter(([, amount]) => approximate(amount) !== 0).map(([kind]) => kind);
	return kinds.length ? kinds : all.map(([kind]) => kind);
}

/** The filter of the transactions behind a holding over the range: its kinds, so the tax
 * withheld shows beside what was paid, and the name its rows give, or the account of those
 * that name nothing. An account two people each have one of is named after its owner, and
 * opens that person's rows; any other keeps the person shown, since `owner` chooses whose
 * figures every page shows. */
export function transactionsOf(
	holding: PayingHolding,
	range: { first: string | null; last: string | null },
	owner: string | null
): URLSearchParams {
	const query = new URLSearchParams();
	const whose = holding.owner ?? owner;
	if (whose) query.set('owner', whose);
	if (range.first) query.set('from', range.first);
	if (range.last) query.set('to', range.last);
	query.set('kind', kindsOf(holding).join(','));
	if (holding.account) query.set('account', holding.account);
	else query.set('text', holding.key);
	return query;
}

/** The first day of the twelve months that end with the month of `through`. */
export function trailingFrom(through: string): string {
	const [year, month] = through.split('-').map(Number);
	const index = year * 12 + month - 12;
	return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}-01`;
}

/** The first day of the earliest month with statements for `owner`, or for everyone. */
export function firstStatementMonth(
	people: readonly Pick<Person, 'name' | 'first'>[],
	owner: string | null
): string | null {
	const firsts = people
		.filter((person) => person.first !== null && (owner === null || person.name === owner))
		.map((person) => `${person.first!.slice(0, 7)}-01`);
	if (!firsts.length) return null;
	return firsts.reduce((earliest, month) => (month < earliest ? month : earliest));
}

/** Whether the range shown is the twelve months up to the last day committed (clamped to the
 * first month with statements, when known), the figures of `trailing`. */
export function isTrailing(
	income: Pick<PayoutsReport, 'first' | 'last' | 'through'>,
	firstMonth: string | null = null
): boolean {
	if (income.through === null || income.last !== income.through) return false;
	const nominal = trailingFrom(income.through);
	const expected = firstMonth && firstMonth > nominal ? firstMonth : nominal;
	return income.first === expected;
}

/** The range shown when none is asked for (`lib/period.ts` picks the one asked). */
export const DEFAULT_RANGE = '12m';
