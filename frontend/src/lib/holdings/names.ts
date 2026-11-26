// How an instrument is named on the investments page: its stored name, or for a Treasury bill
// without one, a readable label with its maturity (`T-bill 24 Dec 2026`), falling back to its
// symbol.

import { day } from '#lib/format/date.js';
import { t } from '#lib/i18n/index.svelte.js';
import type { HeldInstrument, PayingHolding } from '#lib/types.js';

const ISIN = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;

/** Readable name for a Treasury bill: `T-bill 24 Dec 2026`, or `T-bill` without a maturity. */
export function billName(maturity: string | null): string {
	return maturity ? t('bills.nameDue', { date: day(maturity) }) : t('bills.name');
}

/** Primary display name of a held instrument (list, allocation summary, treemap tooltip). */
export function holdingName(
	holding: Pick<HeldInstrument, 'symbol' | 'name' | 'type' | 'maturity'>
): string {
	if (holding.name) return holding.name;
	if (holding.type === 'bill') return billName(holding.maturity);
	return holding.symbol;
}

/** Label on a treemap tile: ticker for stocks and ETFs, readable name where the symbol is an
 * opaque identifier (a fund's ISIN or a Treasury bill's CUSIP). */
export function mapLabel(
	holding: Pick<HeldInstrument, 'symbol' | 'name' | 'type' | 'maturity'>
): string {
	if (holding.type === 'bill') return holdingName(holding);
	return holding.name && ISIN.test(holding.symbol) ? holding.name : holding.symbol;
}

/** Primary display name of a paying holding on Investments -> Income. */
export function payingName(
	holding: Pick<PayingHolding, 'key' | 'name' | 'symbol' | 'type' | 'maturity' | 'account'>
): string {
	if (holding.account) return holding.name;
	if (holding.type === 'bill' && holding.name === holding.symbol) {
		return billName(holding.maturity);
	}
	return holding.name;
}
