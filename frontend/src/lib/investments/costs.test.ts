import { describe, expect, it } from 'vitest';
import type { CostYear } from '#lib/types.js';
import { costsAnything, costsOf, newestFirst, parts } from './costs.js';

const YEAR: CostYear = {
	year: 2025,
	commissions: '1.60',
	tax: '0.00',
	total: '1.60',
	paid: '80.00',
	tax_share: '0.000',
	last: '2025-12-31',
	whole: true
};
const SO_FAR: CostYear = {
	...YEAR,
	year: 2026,
	commissions: '11.80',
	tax: '15.25',
	total: '27.05',
	last: '2026-08-31',
	whole: false
};

describe('what investing costs', () => {
	it('lists the years newest first, leaving those it was given as they were', () => {
		const years = [YEAR, SO_FAR];
		expect(newestFirst(years).map((year) => year.year)).toEqual([2026, 2025]);
		expect(years[0]).toBe(YEAR);
	});

	it('says what an account cost is made of', () => {
		expect(parts({ commissions: '3.40', tax: '13.50' })).toBe('commissions 3, tax withheld 14');
		expect(parts({ commissions: '0.00', tax: '1.75' })).toBe('tax withheld 2');
	});

	it('says whether anything cost something', () => {
		expect(costsAnything({ years: [YEAR, SO_FAR] })).toBe(true);
		expect(costsAnything({ years: [{ ...YEAR, total: '0.00' }] })).toBe(false);
		expect(costsAnything({ years: [] })).toBe(false);
	});

	it('opens the rows of a category in a year, for the person shown', () => {
		expect(costsOf({ category: 'Ritenute', year: 2026 }, 'Lukas').toString()).toBe(
			'owner=Lukas&category=Ritenute&from=2026&to=2026'
		);
		expect(costsOf({ category: 'Commissioni' }, null).toString()).toBe('category=Commissioni');
	});

	it('opens the commissions and tax of an account, of its owner', () => {
		expect(costsOf({ account: 'IBKR USD', owner: 'Anna' }, null).toString()).toBe(
			'owner=Anna&account=IBKR+USD&kind=commission%2Ctax'
		);
	});
});
