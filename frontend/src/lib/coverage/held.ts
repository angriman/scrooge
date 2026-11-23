// What the accounts hold now, together: the lead of the accounts page.

import { sum } from '#lib/format/number.js';
import type { Amount } from '#lib/types.js';

/** What `accounts` hold together, of those whose balance is known (`null` when none is), and
 * how many are left out because theirs is not. */
export function heldTogether(accounts: readonly { balance: Amount | null }[]): {
	total: Amount | null;
	unknown: number;
} {
	const known = accounts.flatMap((account) => (account.balance === null ? [] : [account.balance]));
	return { total: known.length ? sum(known) : null, unknown: accounts.length - known.length };
}
