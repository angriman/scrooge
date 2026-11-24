import { describe, expect, it } from 'vitest';
import { bookedAs } from './parts.js';

describe('what a row is booked as elsewhere', () => {
	it('names every currency account of a broker', () => {
		expect(bookedAs(['ACME CHF', 'ACME EUR', 'ACME USD'])).toBe(
			'Booked as ACME CHF, ACME EUR and ACME USD.'
		);
		expect(bookedAs(['ACME CHF', 'ACME USD'])).toBe('Booked as ACME CHF and ACME USD.');
	});

	it('says nothing of an account booked under its own name', () => {
		expect(bookedAs(['Migros'])).toBe('');
		expect(bookedAs([])).toBe('');
	});
});
