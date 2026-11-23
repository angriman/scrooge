import { describe, expect, it } from 'vitest';
import { coveredUpTo } from './covered.js';

const people = [
	{ name: 'Anna', covered: '2026-10-31' },
	{ name: 'Lukas', covered: '2026-09-30' },
	{ name: 'Mia', covered: null }
];

describe('up to which day the statements shown are in', () => {
	it('is the day of the person shown', () => {
		expect(coveredUpTo(people, 'Anna')).toBe('2026-10-31');
		expect(coveredUpTo(people, 'Lukas')).toBe('2026-09-30');
	});

	it('is, for the household, the day of the person furthest behind', () => {
		expect(coveredUpTo(people, '')).toBe('2026-09-30');
		expect(coveredUpTo(people.slice(0, 1), '')).toBe('2026-10-31');
	});

	it('leaves out someone with nothing committed', () => {
		expect(coveredUpTo(people, 'Mia')).toBeNull();
		expect(coveredUpTo(people.slice(2), '')).toBeNull();
		expect(coveredUpTo([], '')).toBeNull();
	});

	it('is not known of someone who is not there', () => {
		expect(coveredUpTo(people, 'Nobody')).toBeNull();
	});
});
