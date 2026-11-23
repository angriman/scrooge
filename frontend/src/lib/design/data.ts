// Which colour of the theme's data colours (`--data-1` … `--data-8`) each series wears. A series
// keeps its colour on every page and in every chart: Stocks are the same colour on the
// overview's chart and on the holdings page's map.

export type Slot = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8;

/** The asset types of `networth.ASSETS`. A card is only ever owed: it wears the colour of a
 * loss (`assetColor`), not a slot. */
export const ASSET_SLOTS: Readonly<Record<string, Slot>> = {
	Stocks: 1,
	ETFs: 2,
	Cash: 3,
	'Pillar 3a': 4,
	'Pillar 2': 5,
	Bills: 6
};

/** The income groups of `income.GROUPS`. */
export const INCOME_SLOTS: Readonly<Record<string, Slot>> = {
	Lavoro: 4,
	Investimenti: 1,
	Altro: 6
};

/** The colour of `slot`, as CSS takes it (a `fill`, a `stroke`, a `background`). */
export function color(slot: Slot | null | undefined): string {
	return slot ? `var(--data-${slot})` : 'var(--data-other)';
}

/** The colour of an asset type, as every chart and list draws it. */
export function assetColor(name: string): string {
	return name === 'Card' ? 'var(--loss)' : color(ASSET_SLOTS[name]);
}

/** The colour of each of `names` in the order they come: the first eight get a slot of their
 * own, in order, and the rest share the colour of the rest. For series with no fixed slot,
 * like the categories of spending, largest first. */
export function inOrder(names: readonly string[]): Map<string, string> {
	return new Map(
		names.map((name, index) => [name, color(index < 8 ? ((index + 1) as Slot) : null)])
	);
}
