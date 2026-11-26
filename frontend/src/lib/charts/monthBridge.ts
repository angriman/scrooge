// The steps of a month's net-worth bridge and the screen-reader text for them: both come from
// the same list, so every bar the chart draws (including new accounts, when any came into view)
// is also read aloud, in the same order.

import { approximate, negate, short, shortSigned } from '#lib/format/number.js';
import { t } from '#lib/i18n/index.svelte.js';
import type { Amount } from '#lib/types.js';

export type Tone = 'end' | 'gain' | 'loss' | 'arrived' | 'other';

export interface BridgeInput {
	before: Amount;
	income: Amount;
	spent: Amount;
	/** What accounts held when they first counted: a step of its own unless it is zero. */
	arrived: Amount;
	other: Amount;
	after: Amount;
	/** What the two ends are called: `30 Sep`, `31 Oct`. */
	from: string;
	to: string;
}

export interface BridgeStep {
	/** Label written under the bar (shortened when the chart is narrow). */
	name: string;
	/** Full name read by a screen reader; null on the start and end bars. */
	spoken: string | null;
	/** Level where the bar starts, or null for the start and end bars on the baseline. */
	from: number | null;
	/** Level where the bar ends. */
	to: number;
	/** Formatted figure written over the bar and read in the screen-reader label. */
	label: string;
	tone: Tone;
}

/** Every bar of the bridge in drawing order: start, each move, and end. */
export function bridgeSteps(input: BridgeInput, width: number): BridgeStep[] {
	const { before, income, spent, arrived, other, after, from, to } = input;
	const start = approximate(before);
	const afterIncome = start + approximate(income);
	const afterSpending = afterIncome - approximate(spent);
	const afterArrivals = afterSpending + approximate(arrived);
	const end = approximate(after);
	const arrivals: BridgeStep[] =
		approximate(arrived) !== 0
			? [
					{
						name: width < 480 ? t('bridge.newShort') : t('bridge.newAccounts'),
						spoken: t('bridge.newAccounts'),
						from: afterSpending,
						to: afterArrivals,
						label: shortSigned(arrived),
						tone: 'arrived'
					}
				]
			: [];
	return [
		{ name: from, spoken: null, from: null, to: start, label: short(before), tone: 'end' },
		{
			name: t('bridge.income'),
			spoken: t('bridge.income'),
			from: start,
			to: afterIncome,
			label: shortSigned(income),
			tone: 'gain'
		},
		{
			name: t('bridge.spending'),
			spoken: t('bridge.spending'),
			from: afterIncome,
			to: afterSpending,
			label: shortSigned(negate(spent)),
			tone: 'loss'
		},
		...arrivals,
		{
			// Its full name where there is room; the panel's note says what else it holds.
			name: width < 420 ? t('bridge.marketsShort') : t('bridge.marketsAndOther'),
			spoken: t('bridge.marketsAndOther'),
			from: afterArrivals,
			to: end,
			label: shortSigned(other),
			tone: 'other'
		},
		{ name: to, spoken: null, from: null, to: end, label: short(after), tone: 'end' }
	];
}

/** Screen-reader description built from the exact `steps` the chart draws. */
export function bridgeAriaLabel(steps: readonly BridgeStep[]): string {
	const first = steps[0];
	const last = steps[steps.length - 1];
	if (!first || !last) return '';
	const moves = steps
		.flatMap((step) => (step.spoken === null ? [] : [`${step.spoken.toLowerCase()} ${step.label}`]))
		.join(', ');
	return t('bridge.ariaLabel', {
		before: first.label,
		from: first.name,
		steps: moves,
		after: last.label,
		to: last.name
	});
}
