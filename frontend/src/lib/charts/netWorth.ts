// Screen-reader name for the net worth chart: what it shows, how it is cut, and the span drawn.

import { day, month } from '#lib/format/date.js';
import { t } from '#lib/i18n/index.svelte.js';

/** Screen-reader label for the net worth area chart over `first`..`last`. */
export function netWorthChartLabel(
	title: string,
	by: string,
	cut: boolean,
	first: string | null,
	last: string | null
): string {
	let subject = title;
	if (cut) {
		if (by === 'asset') subject = t('overview.chartByAsset', { title });
		else if (by === 'owner') subject = t('overview.chartByOwner', { title });
		else if (by === 'liquidity') subject = t('overview.chartByLiquidity', { title });
	}
	if (!first || !last) return subject;
	if (first === last) return t('overview.chartDay', { subject, date: day(first) });
	if (first.slice(0, 7) === last.slice(0, 7)) {
		return t('overview.chartDaysSpan', { subject, from: day(first), to: day(last) });
	}
	return t('overview.chartMonthsSpan', { subject, from: month(first), to: month(last) });
}
