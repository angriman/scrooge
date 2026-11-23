// The small chart above the transactions cut to one category, merchant or account: a bar for
// each month, and where its usual month runs across them. Only what is drawn is worked out
// here; the amounts written beside it are the API's.

import { approximate } from '#lib/format/number.js';
import type { Amount } from '#lib/types.js';

export interface Bar {
	month: string;
	amount: Amount;
	x: number;
	y: number;
	width: number;
	height: number;
	/** The last month, not over in the statements committed. */
	partial: boolean;
}

export interface Drawn {
	bars: Bar[];
	/** Where the usual month runs across; null when it is not known. */
	usual: number | null;
	/** What the top of the chart stands for. */
	top: number;
}

/** The bars of `months` in a chart `width` by `height`, the largest month (or the usual one,
 * when larger) reaching the top. A month below nothing (more came back than went out) is
 * drawn as nothing; one above nothing is never thinner than a line, so it shows. */
export function draw(
	months: readonly { month: string; amount: Amount }[],
	usual: Amount | null,
	partial: boolean,
	width: number,
	height: number
): Drawn {
	const values = months.map((month) => Math.max(0, approximate(month.amount)));
	const typical = usual === null ? null : Math.max(0, approximate(usual));
	const top = Math.max(...values, typical ?? 0) || 1;
	const step = width / Math.max(1, months.length);
	const bar = Math.max(2, step * 0.7);
	const bars = months.map((month, index) => {
		const size = values[index] > 0 ? Math.max(1, (values[index] / top) * height) : 0;
		return {
			month: month.month,
			amount: month.amount,
			x: step * index + (step - bar) / 2,
			y: height - size,
			width: bar,
			height: size,
			partial: partial && index === months.length - 1
		};
	});
	return { bars, usual: typical === null ? null : height - (typical / top) * height, top };
}
