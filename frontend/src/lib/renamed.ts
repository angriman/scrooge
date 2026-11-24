import { redirect } from '@sveltejs/kit';
import { resolve } from '$app/paths';
import { movedTo, withBarHash } from './moved.js';

// The pages that were renamed: Statements is called Accounts now, Holdings Investments.

/** Each old address, and the one its page has now. */
export const renamed: ReadonlyMap<string, string> = new Map([
	[resolve('/statements'), resolve('/accounts')],
	[resolve('/holdings'), resolve('/investments')]
]);

/** The load of an old address: on to the new one, with its query and hash. Only a visit straight
 * to it gets here; the layout sends a step inside the app on before it loads anything, since a
 * load cannot read the hash of where it was stepped to. */
export function onward({ url }: { url: URL }): never {
	const bar = globalThis.location ?? null;
	redirect(307, movedTo(withBarHash(url, bar), renamed) ?? resolve('/'));
}
