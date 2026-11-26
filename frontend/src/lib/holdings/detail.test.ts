import { describe, expect, it } from 'vitest';
import { chartGeometry, detailParams, holdingTransactionsParams, nearestPoint } from './detail.js';

describe('detailParams and holdingTransactionsParams', () => {
	const holding = { symbol: 'ACME', currency: 'USD', asset: 'Stocks' };

	it('builds /api/holdings/detail params with optional owner and limit', () => {
		expect(detailParams(holding, 'CHF', null).toString()).toBe(
			'symbol=ACME&currency=CHF&instrument_currency=USD&asset=Stocks'
		);
		expect(detailParams(holding, 'EUR', 'Lukas', 10).toString()).toBe(
			'symbol=ACME&currency=EUR&instrument_currency=USD&asset=Stocks&owner=Lukas&limit=10'
		);
	});

	it('builds /transactions drill-down params for the holding', () => {
		expect(holdingTransactionsParams(holding, null).toString()).toBe('text=ACME');
		expect(holdingTransactionsParams(holding, 'Anna').toString()).toBe('owner=Anna&text=ACME');
	});
});

describe('chartGeometry and nearestPoint', () => {
	it('returns null for an empty series', () => {
		expect(chartGeometry([], 400, 120)).toBeNull();
		expect(nearestPoint([], 100)).toBeNull();
	});

	it('centers a single-day holding without NaN coordinates', () => {
		const geom = chartGeometry([{ day: '2026-09-30', value: '500.00' }], 400, 120);
		expect(geom).not.toBeNull();
		expect(geom!.points).toHaveLength(1);
		expect(Number.isFinite(geom!.points[0].x)).toBe(true);
		expect(Number.isFinite(geom!.points[0].y)).toBe(true);
		expect(geom!.areaPath).toBe('');
		expect(nearestPoint(geom!.points, 12)).toBe(0);
	});

	it('handles a flat series without dividing by zero', () => {
		const geom = chartGeometry(
			[
				{ day: '2026-09-01', value: '100.00' },
				{ day: '2026-09-15', value: '100.00' },
				{ day: '2026-09-30', value: '100.00' }
			],
			400,
			120
		);
		expect(geom).not.toBeNull();
		expect(geom!.points.every((p) => p.y === geom!.points[0].y && Number.isFinite(p.y))).toBe(true);
		expect(geom!.linePath).toMatch(/^M.*L.*L/);
		expect(geom!.areaPath).toMatch(/Z$/);
	});

	it('scales varying points monotonically and finds the nearest point by x', () => {
		const geom = chartGeometry(
			[
				{ day: '2026-09-01', value: '100.00' },
				{ day: '2026-09-15', value: '200.00' },
				{ day: '2026-09-30', value: '150.00' }
			],
			420,
			120
		);
		expect(geom).not.toBeNull();
		expect(geom!.min).toBe('100.00');
		expect(geom!.max).toBe('200.00');
		// Higher value has smaller SVG y coordinate.
		expect(geom!.points[1].y).toBeLessThan(geom!.points[0].y);
		expect(geom!.points[2].y).toBeGreaterThan(geom!.points[1].y);
		expect(geom!.points[2].y).toBeLessThan(geom!.points[0].y);

		expect(nearestPoint(geom!.points, geom!.points[0].x - 10)).toBe(0);
		expect(nearestPoint(geom!.points, geom!.points[1].x + 2)).toBe(1);
		expect(nearestPoint(geom!.points, geom!.points[2].x + 50)).toBe(2);
	});
});
