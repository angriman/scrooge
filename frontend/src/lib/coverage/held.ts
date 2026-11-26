// What the accounts hold now, together: the lead of the accounts page.

import { sum } from '#lib/format/number.js';
import type { Amount } from '#lib/types.js';

/** What `accounts` hold together, of those whose balance is known (`null` when none is), and
 * how many are left out because theirs is not. When `total` is given (computed server-side from
 * unrounded balances and rounded once), it is used instead of adding up rounded balances. */
export function heldTogether(
	accounts: readonly { balance: Amount | null }[],
	total?: Amount | null
): {
	total: Amount | null;
	unknown: number;
} {
	const known = accounts.flatMap((account) => (account.balance === null ? [] : [account.balance]));
	return {
		total: total !== undefined ? total : known.length ? sum(known) : null,
		unknown: accounts.length - known.length
	};
}
