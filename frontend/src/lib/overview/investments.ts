// What the overview's investments card shows: what the investments returned over the range the
// net worth chart is looked at over, and what they paid in the last twelve months. Each is
// asked of the app on its own, so that one it cannot answer leaves the other on the card with
// a sentence saying why; and each links to where the investments page tells it in full.

import { isHttpError, isRedirect } from '@sveltejs/kit';
import { day } from '#lib/format/date.js';
import { approximate } from '#lib/format/number.js';
import { t } from '#lib/i18n/index.svelte.js';
import { withheld } from '#lib/investments/income.js';
import { DEFAULT_RANGE as RETURNS_RANGE, over } from '#lib/investments/performance.js';
import type {
	Amount,
	InvestmentIncome,
	InvestmentPerformance,
	Payout,
	PerformanceReport
} from '#lib/types.js';
import { pick, type Address } from '#lib/view.js';
import type { Range } from './range.js';

/** What the app answered, or null when it could not, and the sentences to show either way. */
export interface Loaded<T> {
	sent: T | null;
	errors: string[];
}

export const returnedLabel = () => t('overview.returnedSubject');
export const paidLabel = () => t('overview.paidSubject');

/** Why `what` is not known, after `failure`: the app's own sentences when it refused. */
export function refusal(failure: unknown, what: string): string {
	if (isRedirect(failure)) return t('overview.notKnownWhy', { what, why: t('api.signedOut') });
	const why = isHttpError(failure) ? failure.body.message : t('api.unreachable');
	return t('overview.notKnownWhy', { what, why });
}

/** `asked`, kept from failing: what the app could not answer is `what` and the reason it gave.
 * Not being signed in still leaves the page for the password page. */
export async function loaded<T extends { errors: string[] }>(
	asked: Promise<T>,
	what: string
): Promise<Loaded<T>> {
	try {
		const sent = await asked;
		return { sent, errors: sent.errors };
	} catch (failure) {
		if (isRedirect(failure)) throw failure;
		return { sent: null, errors: [refusal(failure, what)] };
	}
}

/** What the time-weighted return is over: the range (`over the past year`), or from the day
 * it counts from when money was first put in later (`since 3 Mar 2026`). Empty without days. */
export function returnSpan(range: Range, report: PerformanceReport): string {
	return report.first && report.last
		? over(range, report.first, report.last, report.totals?.twr_since ?? null)
		: '';
}

/** The range's start when the time-weighted return counts from a later day; else null. */
export function rangeStartNote(report: PerformanceReport): string | null {
	const since = report.totals?.twr_since;
	if (!report.first || !since || since <= report.first) return null;
	return t('overview.rangeStarts', { date: day(report.first) });
}

/** The return half of the card: `measured` with its figures, `uninvested` when nothing was
 * invested over the range (no rate to give), `none` without any investment account, and
 * `unknown` when the app could not answer; with the sentences to show, each once. */
export interface ReturnShown {
	state: 'measured' | 'uninvested' | 'none' | 'unknown';
	span: string;
	rangeNote: string | null;
	twr: Amount | null;
	gain: Amount | null;
	currency: string;
	errors: string[];
}

export function returnShown(shown: Loaded<InvestmentPerformance>, range: Range): ReturnShown {
	const report = shown.sent?.performance ?? null;
	const totals = report?.totals ?? null;
	const errors = [...new Set(shown.errors)];
	if (!report) {
		return {
			state: 'unknown',
			span: '',
			rangeNote: null,
			twr: null,
			gain: null,
			currency: '',
			errors
		};
	}
	const state = !totals ? 'none' : totals.twr === null ? 'uninvested' : 'measured';
	return {
		state,
		span: returnSpan(range, report),
		rangeNote: rangeStartNote(report),
		twr: totals?.twr ?? null,
		gain: totals?.gain ?? null,
		currency: report.currency,
		errors
	};
}

/** The income half of the card: what was paid over the last twelve months after the tax
 * withheld, and that tax; null when the app could not answer. */
export interface IncomeShown {
	paid: { net: Amount; tax: Amount | null; currency: string } | null;
	errors: string[];
}

export function incomeShown(shown: Loaded<InvestmentIncome>): IncomeShown {
	const income = shown.sent?.income ?? null;
	const errors = [...new Set(shown.errors)];
	if (!income) return { paid: null, errors };
	const { trailing, currency } = income;
	return { paid: { net: trailing.net, tax: taxWithheld(trailing), currency }, errors };
}

/** The query of the investments page's returns over `range`, for whom and in what currency
 * `url` shows; the range left out when it is the page's own. */
export function returnsQuery(url: Address, range: Range): URLSearchParams {
	const query = pick(url, ['owner', 'currency']);
	if (range !== RETURNS_RANGE) query.set('range', range);
	return query;
}

/** The query of the investments page's income: its range is the last twelve months, as the
 * card's figure. */
export function incomeQuery(url: Address): URLSearchParams {
	const query = pick(url, ['owner', 'currency']);
	query.set('view', 'income');
	return query;
}

/** The tax withheld from what was paid, as a positive amount; null when nothing was. */
export function taxWithheld(paid: Payout): Amount | null {
	return approximate(paid.tax) === 0 ? null : withheld(paid.tax);
}
