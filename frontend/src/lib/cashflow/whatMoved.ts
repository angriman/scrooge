// What moved: the categories of spending furthest from their usual month over the range.

import { approximate, difference, percentOf } from '#lib/format/number.js';
import type { Amount, CashFlowReport } from '#lib/types.js';

/** A category set against its usual month: what it took a month over the range, the median
 * month of the twelve before, and how far apart they are. */
export interface Moved {
	name: string;
	monthly: Amount;
	usual: Amount;
	/** `monthly` less `usual`: above the usual month when positive. */
	change: Amount;
	/** `change` in percent of `usual`, whole; null when nothing is usually spent. */
	percent: Amount | null;
}

/** The `count` categories furthest from their usual month, by the amount a month above or
 * below it, the furthest first; only those with a usual month, and that are not on it. */
export function whatMoved(categories: CashFlowReport['categories'], count = 3): Moved[] {
	const moved: Moved[] = [];
	for (const { name, monthly, usual } of categories) {
		if (usual === null) continue;
		const change = difference(monthly, usual);
		if (approximate(change) === 0) continue;
		moved.push({ name, monthly, usual, change, percent: percentOf(change, usual, 0) });
	}
	const away = (each: Moved) => Math.abs(approximate(each.change));
	return moved
		.sort((a, b) => away(b) - away(a) || a.name.localeCompare(b.name))
		.slice(0, count);
}
