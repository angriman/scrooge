// The pensions as a group of their own, as the holdings view lists them: each account's change
// over the period chosen, what it says under its name, and what the group adds up to.

import { assetName } from '#lib/design/data.js';
import { day } from '#lib/format/date.js';
import { difference, money, sum } from '#lib/format/number.js';
import { t, type MessageKey } from '#lib/i18n/index.svelte.js';
import type { Amount, Pension } from '#lib/types.js';

/** The periods a change is over: the twelve months up to the last day, and the year to date. */
export const PERIODS = [
	{ value: '1y', label: '12 months' },
	{ value: 'ytd', label: 'This year' }
] as const;
export type Period = (typeof PERIODS)[number]['value'];
export const DEFAULT_PERIOD: Period = '1y';

const PERIOD_KEYS: Record<Period, MessageKey> = {
	'1y': 'pensions.12m',
	ytd: 'pensions.ytd'
};

/** The period choices in the active language. */
export function periodOptions(): { value: Period; label: string }[] {
	return PERIODS.map((period) => ({ value: period.value, label: t(PERIOD_KEYS[period.value]) }));
}

/** The period in an address, the twelve months when it names none. */
export function periodOf(asked: string | null): Period {
	return PERIODS.some((period) => period.value === asked) ? (asked as Period) : DEFAULT_PERIOD;
}

/** How much `pension` moved over `period`: its worth now less its worth then; null when
 * nothing was known of it then. */
export function changeOf(pension: Pension, period: Period): Amount | null {
	const start = pension.changes[period].start;
	return start === null ? null : difference(pension.value, start);
}

/** What is said under an account's name: whose it is, when both people's are listed; its
 * pillar; and for Pillar 2 the day of the figure it is carried at, for Pillar 3a the cash it
 * holds besides its fund units. */
export function described(pension: Pension, people: boolean): string {
	const parts = [people ? pension.owner : '', assetName(pension.pillar)];
	if (pension.stated) {
		parts.push(
			t(pension.typed ? 'pensions.typedFor' : 'pensions.certificateOf', {
				date: day(pension.stated)
			})
		);
	} else if (pension.cash !== null) {
		parts.push(t('pensions.ofItCash', { amount: money(pension.cash, 0) }));
	}
	return parts.filter(Boolean).join(' · ');
}

export interface Totals {
	value: Amount;
	/** Null unless the change of every pension is known. */
	change: Amount | null;
	/** What was paid into those whose money paid in is known; null when none is. */
	paidIn: Amount | null;
	/** Whether some pensions (Pillar 2) are not in `paidIn`. */
	paidInPartly: boolean;
}

/** What the pensions add up to over `period`. */
export function totals(pensions: readonly Pension[], period: Period): Totals {
	const changes = pensions.map((pension) => changeOf(pension, period));
	const paid = pensions
		.map((pension) => pension.changes[period].paid_in)
		.filter((amount): amount is Amount => amount !== null);
	return {
		value: sum(pensions.map((pension) => pension.value)),
		change: changes.includes(null) ? null : sum(changes as Amount[]),
		paidIn: paid.length ? sum(paid) : null,
		paidInPartly: paid.length > 0 && paid.length < pensions.length
	};
}
