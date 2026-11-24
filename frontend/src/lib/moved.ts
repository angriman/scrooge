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

/** A step about to be taken, as `beforeNavigate` has it. */
export interface Step {
	readonly type: string;
	readonly willUnload: boolean;
	readonly to: { readonly url: Where & { readonly origin: string } } | null;
}

/** Where `step` goes instead when it is a step inside the app, on this site (`origin`), to an
 * old address in `moves`. `null` leaves it as it is: a step to another site or one that loads
 * the document again (`willUnload`, a link with `data-sveltekit-reload` too), Back and Forward,
 * which go where history has them, and a step to an address that did not move. */
export function stepTo(
	step: Step,
	origin: string,
	moves: ReadonlyMap<string, string>
): string | null {
	const to = step.to?.url;
	if (!to || to.origin !== origin || step.willUnload) return null;
	if (step.type === 'popstate' || step.type === 'leave') return null;
	return movedTo(to, moves);
}

/** An element as far as its link options go: an attribute, and the element it is in. */
export interface Tagged {
	getAttribute(name: string): string | null;
	readonly parentElement: Tagged | null;
}

/** What the link or form a step started from asks of it, as SvelteKit reads it: whether it
 * replaces the current history entry (`data-sveltekit-replacestate`) and whether scroll and focus
 * are reset (`data-sveltekit-reset`), each from the element or the nearest one around it that
 * says. Left out when nothing says, so the step keeps SvelteKit's default. */
export function asked(element: Tagged | null): { replace?: boolean; reset?: boolean } {
	let replace: boolean | undefined;
	let reset: boolean | undefined;
	for (let el = element; el; el = el.parentElement) {
		replace ??= option(el.getAttribute('data-sveltekit-replacestate'));
		reset ??= option(el.getAttribute('data-sveltekit-reset'));
	}
	return {
		...(replace === undefined ? {} : { replace }),
		...(reset === undefined ? {} : { reset })
	};
}

function option(value: string | null): boolean | undefined {
	if (value === null) return undefined;
	return value !== 'false';
}

/** The hash of `url` as the address bar shows it. A load cannot read `url.hash`, so the hash
 * comes from the bar, and only while the bar is at `url` (a visit straight to it, not a preload
 * from another page, whose hash is not this one's). */
export function barHash(url: { readonly pathname: string }, bar: Where | null): string {
	return bar && bar.pathname === url.pathname ? bar.hash : '';
}
