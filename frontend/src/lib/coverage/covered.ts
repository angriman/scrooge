// Up to which day the statements of the person shown are all in: what the header says, so a
// figure is never read as more current than it is.

import type { Day } from '#lib/types.js';

/** The day up to which every monthly account of `owner` is committed; of everyone (`''`), the
 * earliest of the people's days, since a household figure is only complete up to the person
 * furthest behind. Someone with nothing committed has no figures to hold back. `null` when no
 * one shown has a day, or `owner` is not one of `people`. */
export function coveredUpTo(
	people: readonly { name: string; covered: Day | null }[],
	owner: string
): Day | null {
	const days = people
		.filter((person) => owner === '' || person.name === owner)
		.flatMap((person) => (person.covered === null ? [] : [person.covered]));
	// ISO days sort as text.
	return days.length ? days.reduce((earliest, day) => (day < earliest ? day : earliest)) : null;
}
