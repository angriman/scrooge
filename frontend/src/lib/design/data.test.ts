import { describe, expect, it } from 'vitest';
import { ASSET_SLOTS, assetColor, color } from './data.js';

describe('shared asset type colour map', () => {
	it('assigns one unique data slot per non-card asset type', () => {
		const types = ['Cash', 'Bills', 'ETFs', 'Stocks', 'Pillar 3a', 'Pillar 2'];
		const colors = types.map((type) => assetColor(type));
		expect(new Set(colors).size).toBe(types.length);
		for (const type of types) {
			expect(assetColor(type)).toBe(color(ASSET_SLOTS[type]));
		}
	});

	it('uses loss token for Card and fallback token for unknown types', () => {
		expect(assetColor('Card')).toBe('var(--loss)');
		expect(assetColor('Unknown')).toBe('var(--data-other)');
	});
});
