// What an account of the accounts page holds, by part, and the small line of its balance.

import { approximate } from '#lib/format/number.js';
import type { AccountHolds, Amount } from '#lib/types.js';

/** A line of what an account holds: cash in one currency, or its securities together. */
export interface Held {
	label: string;
	/** As kept: in `currency`. */
	amount: Amount;
	currency: string;
	/** In the page's currency, when that is not `currency`. */
	converted: Amount | null;
	/** Whether it is the line of securities, which links to where they are listed. */
	securities: boolean;
}

/** The lines of `holds` in the page's `currency`: one per currency of cash, then one for the
 * securities when it holds any. An account with cash in one currency alone has that one line. */
export function heldLines(holds: AccountHolds, currency: string): Held[] {
	const lines: Held[] = holds.cash.map((cash) => ({
		label: cash.currency === currency ? 'Cash' : `Cash in ${cash.currency}`,
		amount: cash.amount,
		currency: cash.currency,
		converted: cash.currency === currency ? null : cash.converted,
		securities: false
	}));
	if (holds.securities !== null) {
		lines.push({
			label: 'Securities',
			amount: holds.securities,
			currency,
			converted: null,
			securities: true
		});
	}
	return lines;
}

/** The points of a line through `balances`, oldest first, in a box of `width` by `height`
 * with `pad` kept clear at the top and bottom for the stroke: the lowest at the bottom, the
 * highest at the top, a flat one across the middle. Empty with fewer than two balances, which
 * draw no line. */
export function sparkline(
	balances: readonly Amount[],
	width: number,
	height: number,
	pad = 1
): string {
	if (balances.length < 2) return '';
	const values = balances.map(approximate);
	const [low, high] = [Math.min(...values), Math.max(...values)];
	const span = high - low;
	const step = width / (values.length - 1);
	const round = (n: number) => Math.round(n * 100) / 100;
	return values
		.map((value, index) => {
			const y = span === 0 ? height / 2 : pad + (1 - (value - low) / span) * (height - 2 * pad);
			return `${round(index * step)},${round(y)}`;
		})
		.join(' ');
}

/** The query of Investments' holdings of `owner`, in the `currency` the address names (none:
 * the one the app uses): where an account's securities are listed. */
export function securitiesQuery(owner: string, currency: string | null): string {
	const query = new URLSearchParams({ view: 'holdings', owner });
	if (currency) query.set('currency', currency);
	return query.toString();
}
