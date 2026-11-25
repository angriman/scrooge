import { describe, expect, it } from 'vitest';
import { OTHERS, PAID_BACK, partColors, rangeName } from './names.js';

describe('the names of the cash flow', () => {
	it('says the range in words', () => {
		expect(rangeName('Year to date', '2026-01-01', null)).toBe('this year so far');
		expect(rangeName('2025', '2025-01-01', '2025-12-31')).toBe('in 2025');
		expect(rangeName(null, '2026-08-01', null)).toBe('since 1 Aug 2026');
		expect(rangeName(null, '2026-03-01', '2026-05-31')).toBe('from 1 Mar 2026 to 31 May 2026');
	});

	it('gives the largest categories a colour each and the rest one together', () => {
		const colors = partColors(['Home', 'Groceries', OTHERS, PAID_BACK]);
		expect([...colors]).toEqual([
			['Home', 'var(--data-1)'],
			['Groceries', 'var(--data-2)'],
			[OTHERS, 'var(--data-other)']
		]);
	});
});
