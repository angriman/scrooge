import { get } from '#lib/api.js';
import { dimension, FIELDS } from '#lib/transactions/filter.js';
import type { Transactions, TransactionsContext } from '#lib/types.js';
import { pick } from '#lib/view.js';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, url, depends }) => {
	depends('api:transactions');
	// Cut to exactly one category, merchant or account, the list has that one's months above
	// it, whatever else narrows the rows.
	const one = dimension(url);
	const [listed, context] = await Promise.all([
		get<Transactions>(fetch, '/api/transactions', pick(url, ['owner', ...FIELDS])),
		one
			? get<TransactionsContext>(
					fetch,
					'/api/transactions/context',
					pick(url, ['owner', 'currency', one.name])
				)
			: null
	]);
	return { listed, context };
};
