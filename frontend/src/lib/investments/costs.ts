// What investing costs, as the costs panel under the income view shows it: the years newest
// first, what an account's cost is made of, and the transactions behind a figure.

import { isHttpError } from '@sveltejs/kit';
import { approximate, money, negate } from '#lib/format/number.js';
import { t, type MessageKey } from '#lib/i18n/index.svelte.js';
import type { Cost, CostsReport, CostYear } from '#lib/types.js';

/** The categories the rows of a cost have, by their keys: commissions (`categories.FEES`),
 * and the tax withheld (`categories.WITHHOLDING_TAX`). */
export const COMMISSIONS = 'fees';
export const TAX = 'withholding_tax';

/** The years of the table, newest first. */
export function newestFirst(years: readonly CostYear[]): CostYear[] {
	return [...years].reverse();
}

/** What an account's cost is made of, in whole units: `commissions 3, tax withheld 14`, of
 * those it has anything of. More refunded than charged is said so: `commissions refunded 2`. */
export function parts(cost: Pick<Cost, 'commissions' | 'tax'>): string {
	return (
		[
			['costs.commissionsLower', cost.commissions],
			['costs.taxWithheldLower', cost.tax]
		] as [MessageKey, string][]
	)
		.filter(([, amount]) => approximate(amount) !== 0)
		.map(([key, amount]) => {
			const name = t(key);
			return approximate(amount) < 0
				? t('costs.partRefunded', { name, amount: money(negate(amount), 0) })
				: t('costs.partCharged', { name, amount: money(amount, 0) });
		})
		.join(', ');
}

/** Whether anything cost something: when nothing did, the panel says so in a line. */
export function costsAnything(costs: Pick<CostsReport, 'years'>): boolean {
	return costs.years.some((year) => approximate(year.total) !== 0);
}

/** Why the costs could not be loaded, as a sentence: what the API said, else what failed. */
export function whyNot(failed: unknown): string {
	if (isHttpError(failed)) return failed.body.message;
	return failed instanceof Error ? `${failed.message}.` : '';
}

/** What a figure of the panel stands for: the commissions or the tax of a year, or of every
 * year; or what one account cost, every year. */
export type CostFigure =
	| { category: typeof COMMISSIONS | typeof TAX; year?: number }
	| { account: string; owner: string; broker: boolean };

/** The filter of the rows behind a figure. Commissions are those of broker accounts, as the
 * panel counts them: a bank's fee is of the same category and kind. A broker's account opens
 * its commissions and its tax withheld; any other its tax withheld alone, which is all that
 * counts of it. An account opens the rows of its owner, a year those of the person shown. */
export function costsOf(what: CostFigure, owner: string | null): URLSearchParams {
	const query = new URLSearchParams();
	const whose = 'account' in what ? what.owner : owner;
	if (whose) query.set('owner', whose);
	if ('account' in what) {
		query.set('account', what.account);
		if (what.broker) query.set('kind', 'commission,tax');
		else query.set('category', TAX);
		return query;
	}
	query.set('category', what.category);
	if (what.category === COMMISSIONS) query.set('accounts', 'broker');
	if (what.year !== undefined) {
		query.set('from', String(what.year));
		query.set('to', String(what.year));
	}
	return query;
}
