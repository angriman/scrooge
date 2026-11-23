// What a page shows is in its address: whose figures (`owner`), in what currency, over what
// range, cut how. A link to it, the back button and a reload all show the same thing.

import { goto } from '$app/navigation';
import { page } from '$app/state';

/** An address as a page has it (`page.url`): where it is, and its query. */
export interface Address {
	readonly pathname: string;
	readonly search: string;
	readonly searchParams: { get(name: string): string | null };
}

/** The address of `url` with `changes` made to its query; `null` takes a field out. */
export function withQuery(url: Address, changes: Record<string, string | null>): string {
	const query = new URLSearchParams(url.search);
	for (const [name, value] of Object.entries(changes)) {
		if (value === null) query.delete(name);
		else query.set(name, value);
	}
	const written = query.toString();
	return url.pathname + (written ? `?${written}` : '');
}

/** The address shown: the page's, or the one a change of view put in its place without
 * loading anything again (`show` with `shallow`). */
export function here(): Address {
	return page.shallow?.url ?? page.url;
}

/** Show `url` with `changes` made to its query, where the reader is on the page. `shallow`:
 * only the address changes, for what the page already has the figures of (a range). */
export function show(
	url: Address,
	changes: Record<string, string | null>,
	{ shallow = false }: { shallow?: boolean } = {}
): Promise<void> {
	return goto(withQuery(url, changes), { reset: false, shallow });
}

/** The fields of `url`'s query that `names` lists, for an API that takes them. */
export function pick(url: Address, names: readonly string[]): URLSearchParams {
	const picked = new URLSearchParams();
	for (const name of names) {
		const value = url.searchParams.get(name);
		if (value) picked.set(name, value);
	}
	return picked;
}

/** `url`'s field `name` if it is one of `allowed`, else `fallback`. */
export function oneOf<T extends string>(
	url: Address,
	name: string,
	allowed: readonly T[],
	fallback: T
): T {
	const value = url.searchParams.get(name);
	return allowed.includes(value as T) ? (value as T) : fallback;
}
