import { describe, expect, it } from 'vitest';
import { personColors, split } from './paidBy.js';

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

	it('gives each person a tone of their own, by where the app lists them', () => {
		const brand = (strength: number) =>
			`color-mix(in srgb, var(--brand) ${strength}%, var(--surface-panel))`;
		// Zoe has no statements: Lukas keeps the colour of the second.
		expect(personColors(['Anna', 'Lukas', 'Zoe'], ['Lukas', 'Anna'])).toEqual(
			new Map([
				['Lukas', brand(45)],
				['Anna', brand(100)]
			])
		);
		expect(personColors(['Anna'], ['Anna', 'Lukas', 'Mia', 'Zoe']).get('Zoe')).toBe(brand(25));
	});
});
