import { describe, expect, it } from 'vitest';
import { against, barWidth, changeTone } from './bars.js';

describe('barWidth', () => {
	it('scales positive values proportionally with a 1.5% minimum and 100% cap', () => {
		expect(barWidth('100', 200)).toBe(50);
		expect(barWidth('1', 1000)).toBe(1.5);
		expect(barWidth('250', 200)).toBe(100);
	});

	it('clamps zero and negative amounts (or non-positive largest) to 0', () => {
		expect(barWidth('0', 200)).toBe(0);
		expect(barWidth('0.00', 200)).toBe(0);
		expect(barWidth('-120.00', 200)).toBe(0);
		expect(barWidth('50', 0)).toBe(0);
	});
});

describe('against', () => {
	it('returns whole percent difference against a positive baseline', () => {
		expect(against('120.00', '100.00')).toBe('20');
		expect(against('80.00', '100.00')).toBe('-20');
		expect(against('0.00', '5000.00')).toBe('-100');
		expect(against('-50.00', '100.00')).toBe('-150');
	});

	it('returns null when baseline is null, zero, or negative', () => {
		expect(against('100.00', null)).toBeNull();
		expect(against('100.00', '0')).toBeNull();
		expect(against('100.00', '0.00')).toBeNull();
		expect(against('100.00', '-50.00')).toBeNull();
	});
});

describe('changeTone', () => {
	it('highlights spending increases as caution and decreases as gain', () => {
		expect(changeTone('11', 'down')).toBe('text-caution');
		expect(changeTone('10', 'down')).toBe('text-ink-secondary');
		expect(changeTone('-10', 'down')).toBe('text-ink-secondary');
		expect(changeTone('-11', 'down')).toBe('text-gain');
		expect(changeTone(null, 'down')).toBe('');
	});

	it('highlights income increases as gain and decreases as caution', () => {
		expect(changeTone('11', 'up')).toBe('text-gain');
		expect(changeTone('10', 'up')).toBe('text-ink-secondary');
		expect(changeTone('-10', 'up')).toBe('text-ink-secondary');
		expect(changeTone('-11', 'up')).toBe('text-caution');
		expect(changeTone(null, 'up')).toBe('');
	});
});
