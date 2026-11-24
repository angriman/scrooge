// The categories on the settings page: under their headings as the taxonomy has them, and what
// each can be merged into.

import type { Headed, ListedCategory } from '#lib/types.js';

export interface Section {
	heading: string;
	categories: ListedCategory[];
}

const SIDES: Record<ListedCategory['side'], string> = {
	uscite: 'Expenses',
	entrate: 'Income',
	transfer: 'Between accounts'
};

/** The categories under their headings: each top-level expense category, then income, then
 * money between accounts. */
export function sections(listed: readonly ListedCategory[]): Section[] {
	const found = new Map<string, ListedCategory[]>();
	for (const category of listed) {
		const heading = category.parent ?? SIDES[category.side];
		found.set(heading, [...(found.get(heading) ?? []), category]);
	}
	return [...found].map(([heading, categories]) => ({ heading, categories }));
}

/** A category's key among all: a name is on both sides at times (Trading). */
export const categoryKey = (category: ListedCategory): string =>
	`${category.side}|${category.name}`;

/** Its rows, in words. */
export function rowsName(rows: number): string {
	if (rows === 0) return 'no rows';
	return rows === 1 ? '1 row' : `${rows} rows`;
}

/** The sides each name is on: a few are on both (Trading). */
function sidesOf(listed: readonly ListedCategory[]): Map<string, Set<string>> {
	const sides = new Map<string, Set<string>>();
	for (const each of listed) sides.set(each.name, (sides.get(each.name) ?? new Set()).add(each.side));
	return sides;
}

/** What `category` can be merged into, under their headings: another on every side it is on
 * (income is not merged into an expense, nor money between accounts into either), each name
 * once. */
export function mergeInto(listed: readonly ListedCategory[], category: ListedCategory): Headed[] {
	const sides = sidesOf(listed);
	const own = sides.get(category.name) ?? new Set([category.side]);
	const others = listed.filter(
		(other) =>
			other.name !== category.name &&
			own.has(other.side) &&
			[...own].every((side) => sides.get(other.name)?.has(side))
	);
	const seen = new Set<string>();
	return sections(others)
		.map(({ heading, categories }) => ({
			heading,
			names: categories
				.map((other) => other.name)
				.filter((name) => !seen.has(name) && Boolean(seen.add(name)))
		}))
		.filter((group) => group.names.length);
}

/** Whether `category` can be renamed, moved or taken out: only what was added, the taxonomy's
 * own is merged instead; and not what the config gives rows no rule matches (`unmatched`),
 * which every import needs. Moved: an expense. Taken out: only when no row carries it. */
export const renamable = (category: ListedCategory, unmatched: string | null = null): boolean =>
	!category.built_in && category.name !== unmatched;
export const movable = (category: ListedCategory): boolean =>
	!category.built_in && category.side === 'uscite';
export const removable = (category: ListedCategory, unmatched: string | null = null): boolean =>
	renamable(category, unmatched) && category.rows === 0;
/** Whether it can be merged away into another. */
export const mergeable = (category: ListedCategory, unmatched: string | null = null): boolean =>
	category.name !== unmatched;
