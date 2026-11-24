// A page that was renamed keeps its old address: a bookmark or an old link to it, to the checks
// of Statements (`#checks`) too, still finds the page, with the same view.

/** An address with its hash: `location`, or where a step inside the app goes (`to.url`). */
export interface Where {
	readonly pathname: string;
	readonly search: string;
	readonly hash: string;
}

/** Where `url` goes now when it is the old address of a page in `moves` (old path to new, both
 * as the browser has them, with the base): the new path with the same query and hash. `null`
 * for an address that did not move. */
export function movedTo(url: Where, moves: ReadonlyMap<string, string>): string | null {
	const to = moves.get(url.pathname.length > 1 ? url.pathname.replace(/\/$/, '') : url.pathname);
	return to === undefined ? null : to + url.search + url.hash;
}

/** `url` with the hash the address bar shows. A load cannot read `url.hash`, so the hash comes
 * from the bar, and only while the bar is at `url` (a visit straight to it, not a preload from
 * another page, whose hash is not this one's). */
export function withBarHash(
	url: { readonly pathname: string; readonly search: string },
	bar: Where | null
): Where {
	const hash = bar && bar.pathname === url.pathname ? bar.hash : '';
	return { pathname: url.pathname, search: url.search, hash };
}
