import { describe, expect, it } from 'vitest';
import { statementsUpTo } from './through.js';

const day = '2026-10-15';

describe('the day the net-worth card says its statements reach', () => {
	it('is left to the header when it is later than the header’s day', () => {
		expect(statementsUpTo('2026-10-10', day, '2026-09-30')).toBeNull();
	});

	it('is kept when it is the header’s day or before it', () => {
		expect(statementsUpTo('2026-09-30', day, '2026-09-30')).toBe('2026-09-30');
		expect(statementsUpTo('2026-09-20', day, '2026-09-30')).toBe('2026-09-20');
	});

	it('is kept when the header has no day', () => {
		expect(statementsUpTo('2026-10-10', day, null)).toBe('2026-10-10');
	});

	it('is not said when it is the card’s own day, or not known', () => {
		expect(statementsUpTo(day, day, '2026-10-31')).toBeNull();
		expect(statementsUpTo(day, day, null)).toBeNull();
		expect(statementsUpTo(null, day, '2026-09-30')).toBeNull();
	});
});
