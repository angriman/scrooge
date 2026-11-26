import { describe, expect, it } from 'vitest';
import { assetName } from '#lib/design/data.js';
import { setLanguage } from '#lib/i18n/index.svelte.js';
import type { Part } from '#lib/types.js';
import { allocationChartLabel } from './allocation.js';

describe('allocationChartLabel', () => {
	const held: Part[] = [
		{ name: 'ETFs', value: '50000', share: '50.0', then: [] },
		{ name: 'Cash', value: '30000', share: '30.0', then: [] },
		{ name: 'Pillar 2', value: '20000', share: '20.0', then: [] }
	];

	it('names the allocation ring with each held part and share in English', () => {
		expect(allocationChartLabel(held, (part) => assetName(part.name))).toBe(
			'Allocation of what is owned, by type: ETFs 50.0%, Cash 30.0%, Pillar 2 20.0%'
		);
	});

	it('handles empty held list and null shares', () => {
		expect(allocationChartLabel([], (part) => assetName(part.name))).toBe(
			'Allocation of what is owned, by type'
		);
		expect(
			allocationChartLabel([{ name: 'Cash', value: '100', share: null, then: [] }], (part) =>
				assetName(part.name)
			)
		).toBe('Allocation of what is owned, by type: Cash');
	});

	it('localizes the allocation ring label in Italian', () => {
		setLanguage('it');
		try {
			expect(allocationChartLabel(held, (part) => assetName(part.name))).toBe(
				'Allocazione degli attivi per tipo: ETF 50.0%, Liquidità 30.0%, 2º pilastro 20.0%'
			);
			expect(allocationChartLabel([], (part) => assetName(part.name))).toBe(
				'Allocazione degli attivi per tipo'
			);
		} finally {
			setLanguage('en');
		}
	});
});
