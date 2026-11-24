// What investing costs, as the costs panel under the income view shows it: the years newest
// first, what each is called, and the transactions behind a figure.

import { approximate, money } from '#lib/format/number.js';
import type { Cost, CostsReport, CostYear } from '#lib/types.js';

/** The categories the rows of a cost have: commissions, and the tax withheld. */
export const COMMISSIONS = 'Commissioni';
export const TAX = 'Ritenute';

/** The years of the table, newest first. */
export function newestFirst(years: readonly CostYear[]): CostYear[] {
	return [...years].reverse();
}

/** What an account's cost is made of, in whole units: `commissions 3, tax withheld 14`, of
 * those it has anything of. */
export function parts(cost: Pick<Cost, 'commissions' | 'tax'>): string {
	return [
		['commissions', cost.commissions],
		['tax withheld', cost.tax]
	]
		.filter(([, amount]) => approximate(amount) !== 0)
		.map(([name, amount]) => `${name} ${money(amount, 0)}`)
		.join(', ');
}

/** Whether anything cost something: when nothing did, the panel says so in a line. */
export function costsAnything(costs: Pick<CostsReport, 'years'>): boolean {
	return costs.years.some((year) => approximate(year.total) !== 0);
}

/** The filter of the rows behind a cost: those of `category` in `year`, or every year; or the
 * commissions and tax of one account, which is whose `owner` says, every year. */
export function costsOf(
	what: { category: string; year?: number } | { account: string; owner: string },
	owner: string | null
): URLSearchParams {
	const query = new URLSearchParams();
	const whose = 'account' in what ? what.owner : owner;
	if (whose) query.set('owner', whose);
	if ('account' in what) {
		query.set('account', what.account);
		query.set('kind', 'commission,tax');
	} else {
		query.set('category', what.category);
		if (what.year !== undefined) {
			query.set('from', String(what.year));
			query.set('to', String(what.year));
		}
	}
	return query;
}
