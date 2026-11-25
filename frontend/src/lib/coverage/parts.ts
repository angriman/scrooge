// What a broker's row of the accounts page is booked as elsewhere: one account per currency.

import { joinList, t } from '#lib/i18n/index.svelte.js';

/** The names a row's account is booked under elsewhere (Transactions, Income, rules), said
 * once: `Booked as ACME CHF, ACME EUR and ACME USD.` Empty for an account booked under its own
 * name alone. */
export function bookedAs(parts: readonly string[]): string {
	if (parts.length < 2) return '';
	return t('accounts.bookedAs', { list: joinList(parts) });
}
