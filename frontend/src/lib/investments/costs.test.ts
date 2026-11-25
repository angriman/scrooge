import { error } from '@sveltejs/kit';
import { describe, expect, it } from 'vitest';
import type { CostYear } from '#lib/types.js';
import { costsAnything, costsOf, newestFirst, parts, whyNot } from './costs.js';

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

	it('says an account was refunded more commissions than it was charged', () => {
		expect(parts({ commissions: '-2.00', tax: '0.00' })).toBe('commissions refunded 2');
	});

	it('says whether anything cost something', () => {
		expect(costsAnything({ years: [YEAR, SO_FAR] })).toBe(true);
		expect(costsAnything({ years: [{ ...YEAR, total: '0.00' }] })).toBe(false);
		expect(costsAnything({ years: [] })).toBe(false);
	});

	it('says why the costs could not be loaded', () => {
		let failed: unknown;
		try {
			error(400, "No owner named 'Nobody'.");
		} catch (thrown) {
			failed = thrown;
		}
		expect(whyNot(failed)).toBe("No owner named 'Nobody'.");
		expect(whyNot(new TypeError('Failed to fetch'))).toBe('Failed to fetch.');
		expect(whyNot('?')).toBe('');
	});

	it('opens the commissions of a year on broker accounts alone, for the person shown', () => {
		expect(costsOf({ category: 'fees', year: 2026 }, 'Lukas').toString()).toBe(
			'owner=Lukas&category=fees&accounts=broker&from=2026&to=2026'
		);
		expect(costsOf({ category: 'fees' }, null).toString()).toBe('category=fees&accounts=broker');
	});

	it('opens the tax withheld of a year wherever it was withheld', () => {
		expect(costsOf({ category: 'withholding_tax', year: 2026 }, null).toString()).toBe(
			'category=withholding_tax&from=2026&to=2026'
		);
	});

	it('opens the commissions and tax of a broker account, of its owner', () => {
		expect(costsOf({ account: 'IBKR USD', owner: 'Anna', broker: true }, null).toString()).toBe(
			'owner=Anna&account=IBKR+USD&kind=commission%2Ctax'
		);
	});

	it('opens only the tax withheld of another account: its fees are no cost of investing', () => {
		// A bank account listed for 1.75 of tax withheld, whose fee of 5 the panel does not count.
		expect(costsOf({ account: 'Migros', owner: 'Lukas', broker: false }, 'Lukas').toString()).toBe(
			'owner=Lukas&account=Migros&category=withholding_tax'
		);
	});
});
