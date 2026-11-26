// What an account of the accounts page holds, by part.

import { t, type MessageKey } from '#lib/i18n/index.svelte.js';
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

// What each kind of cash is called on its line: what a card owes or a pension fund states is
// not money to spend.
const KIND_KEYS: Record<string, MessageKey> = {
	Cash: 'accounts.kindCash',
	Card: 'accounts.kindCard',
	'Pillar 3a': 'accounts.kindPillar3a',
	'Pillar 2': 'accounts.kindPillar2'
};

/** The lines of `holds` in the page's `currency`: one per kind and currency of cash, named by
 * its kind (a card's balance, a pension fund's), then one for the securities when it holds
 * any. An account with cash in one currency alone has that one line. */
export function heldLines(holds: AccountHolds, currency: string): Held[] {
	const lines: Held[] = holds.cash.map((cash) => {
		const kind = t(KIND_KEYS[cash.asset] ?? 'accounts.kindCash');
		return {
			label:
				cash.currency === currency
					? kind
					: t('accounts.cashInCurrency', { kind, currency: cash.currency }),
			amount: cash.amount,
			currency: cash.currency,
			converted: cash.currency === currency ? null : cash.converted,
			securities: false
		};
	});
	if (holds.securities !== null) {
		lines.push({
			label: t('accounts.securities'),
			amount: holds.securities,
			currency,
			converted: null,
			securities: true
		});
	}
	return lines;
}

/** The query of Investments' holdings of `owner`, in the `currency` the address names (none:
 * the one the app uses): where an account's securities are listed. That page opens on its
 * returns, so the holdings are named. */
export function securitiesQuery(owner: string, currency: string | null): string {
	const query = new URLSearchParams({ view: 'holdings', owner });
	if (currency) query.set('currency', currency);
	return query.toString();
}
