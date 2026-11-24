// Which day the net-worth card says its statements reach, if any. The header already says up to
// when every monthly account's statements are in (lib/coverage/covered.ts); a card naming a
// later day would read as two answers to one question, so then it names none.

import type { Day } from '#lib/types.js';

/** The day the card writes after "with statements up to": `through`, the last day the
 * statements of those shown cover, unless it is the card's own `day` (nothing to add) or is
 * after `covered`, the header's day for the same people (which already says how complete the
 * figures are). A day before or on the header's is kept: the card is behind it. */
export function statementsUpTo(through: Day | null, day: Day, covered: Day | null): Day | null {
	if (through === null || through === day) return null;
	// ISO days sort as text.
	return covered !== null && through > covered ? null : through;
}
