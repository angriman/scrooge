import { redirect } from '@sveltejs/kit';
import { resolve } from '$app/paths';
import { barHash } from './moved.js';

// The pages that were renamed: Statements is called Accounts now, Holdings Investments.
const RENAMED = {
	'/statements': '/accounts',
	'/holdings': '/investments'
} as const;

type Old = keyof typeof RENAMED;

/** Each old address, and the one its page has now, as the browser has them. */
export const renamed: ReadonlyMap<string, string> = new Map(
	(Object.keys(RENAMED) as Old[]).map((old) => [resolve(old), resolve(RENAMED[old])])
);

/** The load of the old address `old`: on to the new one, with its query and hash. Only a visit
 * straight to it gets here; the layout sends a step inside the app on before it loads anything,
 * since a load cannot read the hash of where it was stepped to. */
export function onward(old: Old): (event: { url: URL }) => never {
	return ({ url }) => {
		const hash = barHash(url, globalThis.location ?? null);
		redirect(307, resolve(RENAMED[old]) + url.search + hash);
	};
}
