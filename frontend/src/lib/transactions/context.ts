// The small chart above the transactions cut to one category, merchant or account: a bar for
// each month, and where its usual month runs across them. Only what is drawn is worked out
// here, and how an account's net flow is said; the amounts written beside it are the API's.

import { approximate, negate } from '#lib/format/number.js';
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
	/** Below nothing: drawn down from the line of nothing. */
	negative: boolean;
}

export interface Drawn {
	bars: Bar[];
	/** Where the usual month runs across; null when it is not known. */
	usual: number | null;
	/** What the top of the chart stands for. */
	top: number;
	/** Where the line of nothing runs across: the bottom, unless a month is below nothing. */
	zero: number;
}

/** The bars of `months` in a chart `width` by `height`, the largest month (or the usual one,
 * when larger) reaching the top. Unless `signed`, a month below nothing (more came back than
 * went out) is drawn as nothing; `signed` (a net flow) draws it down from the line of nothing,
 * the smallest month (or the usual one) reaching the bottom. A month other than nothing is
 * never thinner than a line, so it shows. */
export function draw(
	months: readonly { month: string; amount: Amount }[],
	usual: Amount | null,
	partial: boolean,
	width: number,
	height: number,
	signed = false
): Drawn {
	const clamp = (value: number) => (signed ? value : Math.max(0, value));
	const values = months.map((month) => clamp(approximate(month.amount)));
	const typical = usual === null ? null : clamp(approximate(usual));
	const bottom = Math.min(0, ...values, typical ?? 0);
	// Nothing at all is drawn as at the bottom of a chart that reaches one.
	const top = Math.max(0, ...values, typical ?? 0) || (bottom < 0 ? 0 : 1);
	const range = top - bottom;
	const at = (value: number) => ((top - value) / range) * height;
	const zero = at(0);
	const step = width / Math.max(1, months.length);
	const bar = Math.max(2, step * 0.7);
	const bars = months.map((month, index) => {
		const value = values[index];
		const size = value === 0 ? 0 : Math.max(1, (Math.abs(value) / range) * height);
		return {
			month: month.month,
			amount: month.amount,
			x: step * index + (step - bar) / 2,
			y: value < 0 ? zero : zero - size,
			width: bar,
			height: size,
			partial: partial && index === months.length - 1,
			negative: value < 0
		};
	});
	return { bars, usual: typical === null ? null : at(typical), top, zero };
}

/** An account's net flow in a month, as it is said: money put aside when more came in than
 * went out, money taken out otherwise; `amount` is how much, without its sign. */
export function flow(amount: Amount): { said: 'Put aside' | 'Taken out'; amount: Amount } {
	return amount.startsWith('-')
		? { said: 'Taken out', amount: negate(amount) }
		: { said: 'Put aside', amount };
}
