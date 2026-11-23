// A page that was renamed keeps its old address: a bookmark or an old link to it still finds
// the page, with the same view.

/** Where the browser's address bar is: `location`, when there is one. */
export interface Bar {
	readonly pathname: string;
	readonly hash: string;
}

/** Where `url`, an address a page moved away from, goes now: `to` with the same query, and the
 * same hash when the address bar shows it. A load cannot read `url.hash`, so the hash comes from
 * the bar, and only while the bar is at `url` (a visit straight to it, not a step from another
 * page, whose hash is not this one's). */
export function movedTo(
	to: string,
	url: { readonly pathname: string; readonly search: string },
	bar: Bar | null
): string {
	const hash = bar && bar.pathname === url.pathname ? bar.hash : '';
	return to + url.search + hash;
}
