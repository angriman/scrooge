// The categories chosen on the import page before they are saved: one for each counterparty
// waiting (a group), and one for any row on its own. A row chosen on its own keeps its choice
// over its group's.

import type { Staged, StagedRow } from '#lib/types.js';

export interface GroupChoice {
	category: string;
	/** Remembered as a rule for the counterparty's rows of every import to come. */
	rule: boolean;
}

/** What `/api/import/categories` takes: rows and the category they are given. */
export interface Sent {
	ids: string[];
	category: string;
	rule: boolean;
}

type Group = Staged['groups'][number];

/** A group's key: its rows, which no other group has. */
export const groupKey = (group: Group): string => group.ids.join(' ');

/** A choice for every group, nothing chosen yet: remembered by default where it can be.
 * A group that was there before keeps what was chosen for it. */
export function fresh(
	groups: readonly Group[],
	rules: boolean,
	before: Record<string, GroupChoice> = {}
): Record<string, GroupChoice> {
	return Object.fromEntries(
		groups.map((group) => [
			groupKey(group),
			before[groupKey(group)] ?? { category: '', rule: rules && Boolean(group.name) }
		])
	);
}

/** The choices to send: the rows chosen on their own, each category once, then each group
 * with a category, less those rows. A row given the category it has is left alone. */
export function toSend(
	groups: readonly Group[],
	chosen: Record<string, GroupChoice>,
	own: Record<string, string>,
	rows: ReadonlyMap<string, StagedRow>
): Sent[] {
	const alone = new Map<string, string[]>();
	for (const [id, category] of Object.entries(own)) {
		if (category && category !== rows.get(id)?.category) {
			alone.set(category, [...(alone.get(category) ?? []), id]);
		}
	}
	const sent: Sent[] = [...alone].map(([category, ids]) => ({ ids, category, rule: false }));
	for (const group of groups) {
		const choice = chosen[groupKey(group)];
		const ids = group.ids.filter((id) => !own[id]);
		if (choice?.category && ids.length) sent.push({ ids, category: choice.category, rule: choice.rule });
	}
	return sent;
}
