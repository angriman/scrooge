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

/** What `category` can be merged into, under their headings: another of its kind (money
 * between accounts with its own, the rest with the rest), each name once. */
export function mergeInto(listed: readonly ListedCategory[], category: ListedCategory): Headed[] {
	const between = category.side === 'transfer';
	const others = listed.filter(
		(other) => other.name !== category.name && (other.side === 'transfer') === between
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

/** Whether `category` can be renamed or taken out: only what was added, the taxonomy's own is
 * merged instead. Taken out only when no row carries it. */
export const renamable = (category: ListedCategory): boolean => !category.built_in;
export const removable = (category: ListedCategory): boolean =>
	!category.built_in && category.rows === 0;
