// The period a page is looked at over, by the same names on every page (`range` in the
// address: 1m, 3m, ytd, 1y, 3y, all). The overview and the investments' performance take it as
// it is; the cash flow and what investments paid offer ranges of whole months, each matched to
// a period where one fits, and a year of its own by its ends (`from` and `to`).

import type { OfferedRange } from './types.js';

/** What of an address is read: SvelteKit's `page.url` as much as a `URL`. */
export interface Address {
	searchParams: { get(name: string): string | null };
}

/** A range a page offers, as the API sends it, by its key. */
export type Preset = Pick<OfferedRange, 'key' | 'from' | 'to' | 'range'>;

/** The key of the one of `offered` the address asks for: by its ends when it gives them, else by its
 * period, else `byDefault` (what the page shows when nothing is asked; the first one offered
 * unless said). Empty when the address asks for something none of them is, such as a period
 * of three months. */
export function chosen(
	offered: readonly Preset[],
	url: Address,
	byDefault: string = offered[0]?.key ?? ''
): string {
	const from = url.searchParams.get('from') ?? '';
	const to = url.searchParams.get('to') ?? '';
	if (from || to) return offered.find((one) => one.from === from && one.to === to)?.key ?? '';
	const period = url.searchParams.get('range');
	if (period) return offered.find((one) => one.range === period)?.key ?? '';
	return byDefault;
}

/** What choosing the one keyed `key` of `offered` changes in the address: the period for one that is a
 * period, even the page's own default, so that it goes along to pages whose default differs;
 * nothing for `byDefault` (the first one offered unless said) when it is no period; the ends
 * for a year of its own. */
export function choose(
	offered: readonly Preset[],
	key: string,
	byDefault: string = offered[0]?.key ?? ''
): Record<string, string | null> {
	const one = offered.find((each) => each.key === key);
	if (one?.range) return { from: null, to: null, range: one.range };
	if (!one || one.key === byDefault) return { from: null, to: null, range: null };
	return { from: one.from, to: one.to || null, range: null };
}
