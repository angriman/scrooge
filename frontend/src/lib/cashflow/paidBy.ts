// Who paid: how what the household spent is split between the people in it.

import { approximate } from '#lib/format/number.js';
import type { Amount } from '#lib/types.js';

/** How strong each person's tone is, in the order the app lists them. */
const TONES = [100, 45, 25];

/** The colour of each of `names`: a tone of the brand's colour, never a category's, taken from
 * where they stand in `everyone` (the people of the app), so that someone without statements
 * does not move the others' colours. */
export function personColors(
	everyone: readonly string[],
	names: readonly string[]
): Map<string, string> {
	const order = [...everyone, ...names.filter((name) => !everyone.includes(name))];
	return new Map(
		names.map((name) => {
			const strength = TONES[Math.min(order.indexOf(name), TONES.length - 1)];
			return [name, `color-mix(in srgb, var(--brand) ${strength}%, var(--surface-panel))`];
		})
	);
}

/** One person's part of a bar: what they spent, and how long their part is, in percent. */
export interface Part {
	name: string;
	spent: Amount;
	width: number;
}

/** The parts of a bar split between `names`, in their order, from what each spent. A refund
 * larger than what someone spent leaves them no part rather than a negative one; with
 * nothing spent the bar is empty. */
export function split(names: readonly string[], by: Readonly<Record<string, Amount>>): Part[] {
	const spent = names.map((name) => by[name] ?? '0');
	const drawn = spent.map((amount) => Math.max(0, approximate(amount)));
	const total = drawn.reduce((sum, amount) => sum + amount, 0);
	return names.map((name, index) => ({
		name,
		spent: spent[index],
		width: total > 0 ? (drawn[index] / total) * 100 : 0
	}));
}
