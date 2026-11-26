// Helpers for the holding side panel: API query params, transactions drill-down link, and SVG
// coordinate math for the inline value-over-time chart.

import { approximate } from '#lib/format/number.js';
import type { Amount, HeldInstrument, HoldingValuePoint } from '#lib/types.js';

export interface ChartPoint {
	day: string;
	value: Amount;
	x: number;
	y: number;
}

export interface ChartGeometry {
	points: ChartPoint[];
	linePath: string;
	areaPath: string;
	min: Amount;
	max: Amount;
	baselineY: number;
}

export interface ChartPadding {
	top: number;
	right: number;
	bottom: number;
	left: number;
}

const DEFAULT_PADDING: ChartPadding = { top: 10, right: 6, bottom: 10, left: 6 };

/** Query parameters for `GET /api/holdings/detail`. */
export function detailParams(
	holding: Pick<HeldInstrument, 'symbol' | 'currency' | 'asset'>,
	currency: string,
	owner: string | null,
	limit?: number
): URLSearchParams {
	const params = new URLSearchParams();
	params.set('symbol', holding.symbol);
	params.set('currency', currency);
	params.set('instrument_currency', holding.currency);
	params.set('asset', holding.asset);
	if (owner) params.set('owner', owner);
	if (limit !== undefined) params.set('limit', String(limit));
	return params;
}

/** Query parameters for the `/transactions` drill-down from a holding's side panel. */
export function holdingTransactionsParams(
	holding: Pick<HeldInstrument, 'symbol'>,
	owner: string | null
): URLSearchParams {
	const params = new URLSearchParams();
	if (owner) params.set('owner', owner);
	params.set('text', holding.symbol);
	return params;
}

/** Map `series` into SVG coordinates within `width x height`. Returns `null` when empty, and a
 * safe centered dot when `series` has only one day (no NaN division). */
export function chartGeometry(
	series: readonly HoldingValuePoint[],
	width: number,
	height: number,
	padding: ChartPadding = DEFAULT_PADDING
): ChartGeometry | null {
	if (!series.length) return null;
	const plotWidth = Math.max(1, width - padding.left - padding.right);
	const plotHeight = Math.max(1, height - padding.top - padding.bottom);
	const baselineY = padding.top + plotHeight;

	if (series.length === 1) {
		const only = series[0];
		const x = padding.left + plotWidth / 2;
		const y = padding.top + plotHeight / 2;
		return {
			points: [{ day: only.day, value: only.value, x, y }],
			linePath: `M${x.toFixed(2)},${y.toFixed(2)}`,
			areaPath: '',
			min: only.value,
			max: only.value,
			baselineY
		};
	}

	const numbers = series.map((point) => approximate(point.value));
	let minIdx = 0;
	let maxIdx = 0;
	for (let i = 1; i < numbers.length; i++) {
		if (numbers[i] < numbers[minIdx]) minIdx = i;
		if (numbers[i] > numbers[maxIdx]) maxIdx = i;
	}
	const low = numbers[minIdx];
	const high = numbers[maxIdx];
	const span = high - low;
	const stepX = plotWidth / (series.length - 1);

	const points: ChartPoint[] = series.map((point, index) => {
		const x = padding.left + index * stepX;
		const y =
			span === 0
				? padding.top + plotHeight / 2
				: padding.top + ((high - numbers[index]) / span) * plotHeight;
		return { day: point.day, value: point.value, x, y };
	});

	const linePath = points
		.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x.toFixed(2)},${point.y.toFixed(2)}`)
		.join(' ');
	const first = points[0];
	const last = points[points.length - 1];
	const areaPath = `${linePath} L${last.x.toFixed(2)},${baselineY.toFixed(2)} L${first.x.toFixed(2)},${baselineY.toFixed(2)} Z`;

	return {
		points,
		linePath,
		areaPath,
		min: series[minIdx].value,
		max: series[maxIdx].value,
		baselineY
	};
}

/** Index of the chart point nearest to SVG horizontal position `x`, or `null` when empty. */
export function nearestPoint(points: readonly ChartPoint[], x: number): number | null {
	if (!points.length) return null;
	let best = 0;
	let distance = Math.abs(points[0].x - x);
	for (let i = 1; i < points.length; i++) {
		const candidate = Math.abs(points[i].x - x);
		if (candidate < distance) {
			best = i;
			distance = candidate;
		}
	}
	return best;
}
