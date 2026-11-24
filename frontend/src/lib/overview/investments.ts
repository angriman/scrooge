// What the overview's investments card shows: what the investments returned over the range the
// net worth chart is looked at over, and what they paid in the last twelve months. Each is
// asked of the app on its own, so that one it cannot answer leaves the other on the card with
// a sentence saying why; and each links to where the investments page tells it in full.

import { isHttpError, isRedirect } from '@sveltejs/kit';
import { day } from '#lib/format/date.js';
import { approximate } from '#lib/format/number.js';
import { withheld } from '#lib/investments/income.js';
import { DEFAULT_RANGE as RETURNS_RANGE, over } from '#lib/investments/performance.js';
import type { Amount, Payout, PerformanceReport } from '#lib/types.js';
import { pick, type Address } from '#lib/view.js';
import type { Range } from './range.js';

/** What the app answered, or null when it could not, and the sentences to show either way. */
export interface Loaded<T> {
	sent: T | null;
	errors: string[];
}

const UNREACHABLE = 'The app could not be reached: check that it is running, and try again.';

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
		const why = isHttpError(failure) ? failure.body.message : UNREACHABLE;
		return { sent: null, errors: [`${what} is not known: ${why}`] };
	}
}

/** What the time-weighted return is over: the range (`over the past year`), or from the day
 * it counts from when money was first put in later (`since 3 Mar 2026`). Empty without days. */
export function returnSpan(range: Range, report: PerformanceReport): string {
	const since = report.totals?.twr_since;
	if (since) return `since ${day(since)}`;
	return report.first && report.last ? over(range, report.first, report.last) : '';
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
