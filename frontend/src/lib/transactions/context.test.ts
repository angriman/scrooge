import { describe, expect, it } from 'vitest';
import { draw } from './context.js';

const months = (...amounts: string[]) =>
	amounts.map((amount, index) => ({ month: `2026-${String(index + 1).padStart(2, '0')}`, amount }));

describe('the chart of one category, merchant or account', () => {
	it('gives each month a bar of its share of the largest, side by side', () => {
		const drawn = draw(months('50.00', '100.00', '0.00', '25.00'), null, false, 400, 80);
		expect(drawn.top).toBe(100);
		expect(drawn.bars.map((bar) => bar.height)).toEqual([40, 80, 0, 20]);
		expect(drawn.bars.map((bar) => bar.y)).toEqual([40, 0, 80, 60]);
		expect(drawn.bars.map((bar) => bar.x)).toEqual([15, 115, 215, 315]);
		expect(drawn.bars.every((bar) => bar.width === 70 && !bar.partial)).toBe(true);
		expect(drawn.usual).toBeNull();
	});

	it('runs the usual month across, and reaches higher when it is the largest', () => {
		const drawn = draw(months('100.00', '50.00'), '200.00', false, 200, 100);
		expect(drawn.top).toBe(200);
		expect(drawn.usual).toBe(0);
		expect(drawn.bars.map((bar) => bar.height)).toEqual([50, 25]);
		expect(draw(months('100.00'), '25.00', false, 10, 100).usual).toBe(75);
	});

	it('marks the last month when it is not over', () => {
		const drawn = draw(months('10.00', '20.00', '5.00'), null, true, 300, 60);
		expect(drawn.bars.map((bar) => bar.partial)).toEqual([false, false, true]);
	});

	it('draws a month where more came back than went out as nothing, and a tiny one as a line', () => {
		const drawn = draw(months('-30.00', '0.01', '1000.00'), '-5.00', false, 300, 100);
		expect(drawn.bars.map((bar) => bar.height)).toEqual([0, 1, 100]);
		expect(drawn.usual).toBe(100);
	});

	it('draws nothing at all without a month spent, and never divides by nothing', () => {
		const drawn = draw(months('0.00', '0.00'), '0.00', false, 100, 50);
		expect(drawn.top).toBe(1);
		expect(drawn.bars.map((bar) => bar.height)).toEqual([0, 0]);
		expect(drawn.usual).toBe(50);
		expect(draw([], null, true, 100, 50).bars).toEqual([]);
	});
});
