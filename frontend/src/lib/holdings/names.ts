// Readable display names and maturity metadata for held instruments, especially Treasury bills
// whose symbols are opaque CUSIPs.

import { day, dayOfYear } from '#lib/format/date.js';
import { t } from '#lib/i18n/index.svelte.js';
import type { HeldInstrument } from '#lib/types.js';

type NamedHolding = Pick<HeldInstrument, 'symbol' | 'name' | 'asset'> & {
	type?: string;
	maturity?: string | null;
};

const ISIN = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;

/** Whether `holding` is a bill or bond whose symbol is a CUSIP/bond id rather than a ticker. */
export function isBill(holding: Pick<NamedHolding, 'asset' | 'type'>): boolean {
	return holding.asset === 'Bills' || holding.type === 'bill' || holding.type === 'bond';
}

/** Readable name for a Treasury bill or bond: its stored name when present, else localized label. */
export function billName(holding: Pick<NamedHolding, 'name' | 'type'>): string {
	if (holding.name) return holding.name;
	return holding.type === 'bond' ? t('holdings.bondName') : t('holdings.billName');
}

/** Primary display name of a holding in lists, allocation text, and the side-panel header. */
export function holdingName(holding: NamedHolding): string {
	if (isBill(holding)) return billName(holding);
	return holding.name || holding.symbol;
}

/** Localized maturity metadata for bills and bonds (`due 24 Dec 2026` / `no maturity`); empty for
 * equities and funds. */
export function holdingDue(holding: NamedHolding): string {
	if (!isBill(holding)) return '';
	return holding.maturity
		? t('holdings.dueDate', { date: day(holding.maturity) })
		: t('holdings.noMaturity');
}

/** Short label for treemap tiles: readable bill name (with short maturity when unnamed), fund name
 * when the symbol is an ISIN, or the ticker otherwise. */
export function mapLabel(holding: NamedHolding): string {
	if (isBill(holding)) {
		if (holding.name) return holding.name;
		return holding.maturity
			? `${billName(holding)} · ${dayOfYear(holding.maturity)}`
			: billName(holding);
	}
	return holding.name && ISIN.test(holding.symbol) ? holding.name : holding.symbol;
}
