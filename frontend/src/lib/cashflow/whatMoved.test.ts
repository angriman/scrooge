import { describe, expect, it } from 'vitest';
import type { CashFlowReport } from '#lib/types.js';
import { toneOf, whatMoved, type Moved } from './whatMoved.js';

const category = (name: string, monthly: string, usual: string | null): CashFlowReport['categories'][number] => ({
	name,
	count: 1,
	spent: monthly,
	share: null,
	monthly,
	usual,
	year_before: null,
	leaves: []
});

describe('what moved over the range', () => {
	it('picks the three categories furthest from their usual month, above or below', () => {
		const moved = whatMoved([
			category('Casa', '1500.00', '1500.00'),
			category('Viaggi', '400.00', '100.00'),
			category('Alimentari', '450.00', '600.00'),
			category('Ristoranti e bar', '180.00', '200.00'),
			category('Regali', '90.00', '80.00')
		]);
		expect(moved.map((each) => each.name)).toEqual(['Viaggi', 'Alimentari', 'Ristoranti e bar']);
		expect(moved[0]).toEqual({
			name: 'Viaggi',
			monthly: '400.00',
			usual: '100.00',
			change: '300.00',
			percent: '300'
		});
		expect(moved[1]).toMatchObject({ change: '-150.00', percent: '-25' });
	});

	it('leaves out categories without a usual month, and those on it', () => {
		const moved = whatMoved([
			category('Casa', '1500.00', null),
			category('Alimentari', '600.00', '600.00'),
			category('Regali', '50.00', '0.00')
		]);
		// Usually nothing: no percent says how far that is.
		expect(moved).toEqual([
			{ name: 'Regali', monthly: '50.00', usual: '0.00', change: '50.00', percent: null }
		]);
	});

	it('breaks a tie by name, and takes as many as asked', () => {
		const moved = whatMoved(
			[category('Viaggi', '200.00', '100.00'), category('Casa', '0.00', '100.00')],
			1
		);
		expect(moved.map((each) => each.name)).toEqual(['Casa']);
		expect(whatMoved([])).toEqual([]);
	});

	it('finds a category usually spent on and not at all in the range', () => {
		const moved = whatMoved([category('Alimentari', '420.00', '400.00'), category('Casa', '0.00', '1500.00')]);
		expect(moved[0]).toMatchObject({ name: 'Casa', change: '-1500.00', percent: '-100' });
	});

	it('colours only what is more than a tenth away from the usual month', () => {
		const moved = (percent: string | null): Moved => ({
			name: 'Casa',
			monthly: '0',
			usual: '0',
			change: '0',
			percent
		});
		expect(toneOf(moved('11'))).toBe('above');
		expect(toneOf(moved('10'))).toBeNull();
		expect(toneOf(moved('-10'))).toBeNull();
		expect(toneOf(moved('-11'))).toBe('below');
		expect(toneOf(moved(null))).toBeNull();
	});
});
