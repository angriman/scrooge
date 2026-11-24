import { describe, expect, it } from 'vitest';
import { split } from './paidBy.js';

describe('who paid', () => {
	it('splits a bar between the people, in their order', () => {
		expect(split(['Anna', 'Lukas'], { Lukas: '300.00', Anna: '100.00' })).toEqual([
			{ name: 'Anna', spent: '100.00', width: 25 },
			{ name: 'Lukas', spent: '300.00', width: 75 }
		]);
	});

	it('gives no part to someone who got more back than they spent', () => {
		expect(split(['Anna', 'Lukas'], { Anna: '-20.00', Lukas: '80.00' })).toEqual([
			{ name: 'Anna', spent: '-20.00', width: 0 },
			{ name: 'Lukas', spent: '80.00', width: 100 }
		]);
	});

	it('leaves the bar empty when nothing was spent, and someone missing at nothing', () => {
		expect(split(['Anna', 'Lukas'], { Anna: '0.00' })).toEqual([
			{ name: 'Anna', spent: '0.00', width: 0 },
			{ name: 'Lukas', spent: '0', width: 0 }
		]);
	});
});
