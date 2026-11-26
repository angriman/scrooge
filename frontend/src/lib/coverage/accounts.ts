// Pure helpers for the accounts page: stable account identity for the side-panel URL query
// parameter, collision-free timeline axis ticks, and balance-over-time SVG plot geometry.

import { daysBetween, monthOf, shortMonthOf, toDate } from '#lib/format/date.js';
import { approximate } from '#lib/format/number.js';
import { getLanguage, type Language } from '#lib/i18n/index.svelte.js';
import type { Amount, Day, StatementsAccount } from '#lib/types.js';

/** Unique key for one listed account across owner and source. */
export function accountKey(
	account: Pick<StatementsAccount, 'owner' | 'account' | 'source'>
): string {
	return `${account.owner}|${account.account}|${account.source ?? ''}`;
}

/** Query parameter value for `account`: the bare account name when unique across `accounts`,
 * falling back to its full key when two people share an account name. */
export function accountParam(
	account: StatementsAccount,
	accounts: readonly StatementsAccount[]
): string {
	const matches = accounts.filter((item) => item.account === account.account).length;
	return matches === 1 ? account.account : accountKey(account);
}

/** Find the account named by an `account` query parameter (matching full key first, then bare
 * account name). */
export function findAccount(
	accounts: readonly StatementsAccount[],
	param: string | null
): StatementsAccount | null {
	if (!param) return null;
	return (
		accounts.find((item) => accountKey(item) === param) ??
		accounts.find((item) => item.account === param) ??
		null
	);
}

/** One tick on the statement-coverage timeline. Every month (or year on long ranges) draws a
 * subtle vertical hairline; `label` is set only where it fits without colliding. */
export interface TimelineTick {
	/** Position along the track, 0..100 (%). */
	left: number;
	/** Text under the track, or null when omitted to prevent overlap. */
	label: string | null;
	/** Alignment of `label` relative to `left`. */
	align: 'left' | 'center' | 'right';
}

const CHAR_PX = 7.2;
const LABEL_PAD_PX = 4;
const LABEL_GAP_PX = 10;

interface Box {
	left: number;
	right: number;
	align: 'left' | 'center' | 'right';
}

function labelBox(x: number, width: number, trackWidth: number): Box | null {
	if (width > trackWidth) return null;
	const half = width / 2;
	if (x - half >= 0 && x + half <= trackWidth) {
		return { left: x - half, right: x + half, align: 'center' };
	}
	if (x - half < 0 && x + width <= trackWidth) {
		return { left: x, right: x + width, align: 'left' };
	}
	if (x + half > trackWidth && x - width >= 0) {
		return { left: x - width, right: x, align: 'right' };
	}
	return null;
}

function collides(candidate: Box, placed: readonly Box[]): boolean {
	return placed.some(
		(other) =>
			candidate.left < other.right + LABEL_GAP_PX && candidate.right + LABEL_GAP_PX > other.left
	);
}

/** Ticks for the statement coverage track from `start` to `today`, fitted into `widthPx`.
 * Short ranges (<= 800 days) tick every month using short month names (`Dec`, `Feb`, …) with
 * the year shown on January (`Jan 2026`); long ranges tick every January with the year alone.
 * January/year anchors are placed first so they win collisions over neighboring months. */
export function timelineTicks(
	start: Day,
	today: Day,
	widthPx: number,
	language: Language = getLanguage()
): TimelineTick[] {
	const length = Math.max(1, daysBetween(start, today));
	const at = (iso: string) => Math.min(100, Math.max(0, (daysBetween(start, iso) / length) * 100));
	const long = length > 800;

	const raw: { left: number; text: string; anchor: boolean }[] = [];
	const first = toDate(start);
	const end = toDate(today);
	const cursor = new Date(first.getFullYear(), first.getMonth() + 1, 1);
	while (cursor <= end) {
		const iso = `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, '0')}-01`;
		const january = cursor.getMonth() === 0;
		if (!long || january) {
			raw.push({
				left: at(iso),
				text: long
					? String(cursor.getFullYear())
					: january
						? monthOf(cursor, language)
						: shortMonthOf(cursor, language),
				anchor: long || january
			});
		}
		cursor.setMonth(cursor.getMonth() + 1);
	}

	const result: TimelineTick[] = raw.map((tick) => ({
		left: tick.left,
		label: null,
		align: 'center'
	}));
	if (widthPx <= 0) return result;

	const placed: Box[] = [];
	const place = (indices: number[]) => {
		for (const index of indices) {
			const tick = raw[index];
			const x = (tick.left / 100) * widthPx;
			const estimated = Math.ceil(tick.text.length * CHAR_PX) + LABEL_PAD_PX;
			const box = labelBox(x, estimated, widthPx);
			if (box && !collides(box, placed)) {
				placed.push(box);
				result[index] = { left: tick.left, label: tick.text, align: box.align };
			}
		}
	};

	const anchors: number[] = [];
	const others: number[] = [];
	for (let i = 0; i < raw.length; i++) {
		(raw[i].anchor ? anchors : others).push(i);
	}
	place(anchors);
	place(others);
	return result;
}

/** One month-end plotted on the account balance chart. */
export interface BalancePoint {
	day: Day;
	balance: Amount;
	value: number;
	x: number;
	y: number;
}

/** Geometry of an account's balance-over-time SVG chart; null when fewer than 2 points exist. */
export interface BalancePlot {
	width: number;
	height: number;
	pad: { top: number; right: number; bottom: number; left: number };
	points: BalancePoint[];
	/** SVG polyline `points` attribute. */
	line: string;
	/** SVG path `d` for the shaded area between the line and `baseY`. */
	area: string;
	/** Y coordinate of zero when the series crosses zero (`low < 0 && high > 0`), else null. */
	zeroY: number | null;
	/** Baseline Y coordinate for the area fill. */
	baseY: number;
	low: number;
	high: number;
}

const round2 = (value: number) => Math.round(value * 100) / 100;

/** Plot month-end balances (oldest first) inside `width` x `height`. Returns null when fewer
 * than two points are present. Supports negative, zero-crossing, and flat series. */
export function balancePlot(
	months: readonly { day: Day; balance: Amount }[],
	width: number,
	height: number,
	pad = { top: 10, right: 8, bottom: 10, left: 8 }
): BalancePlot | null {
	if (months.length < 2) return null;
	const values = months.map((m) => approximate(m.balance));
	const low = Math.min(...values);
	const high = Math.max(...values);
	const span = high - low;
	const plotW = Math.max(1, width - pad.left - pad.right);
	const plotH = Math.max(1, height - pad.top - pad.bottom);
	const step = plotW / (months.length - 1);

	const yFor = (value: number) =>
		span === 0 ? pad.top + plotH / 2 : pad.top + (1 - (value - low) / span) * plotH;

	const points: BalancePoint[] = months.map((entry, index) => ({
		day: entry.day,
		balance: entry.balance,
		value: values[index],
		x: round2(pad.left + index * step),
		y: round2(yFor(values[index]))
	}));

	const zeroY = low < 0 && high > 0 ? round2(yFor(0)) : null;
	const baseY = zeroY ?? round2(pad.top + plotH);
	const line = points.map((p) => `${p.x},${p.y}`).join(' ');
	const first = points[0];
	const last = points[points.length - 1];
	const area =
		`M ${first.x} ${baseY} ` +
		points.map((p) => `L ${p.x} ${p.y}`).join(' ') +
		` L ${last.x} ${baseY} Z`;

	return { width, height, pad, points, line, area, zeroY, baseY, low, high };
}

/** Index of the point closest horizontally to `x`, or -1 when `points` is empty. */
export function nearestBalancePoint(points: readonly BalancePoint[], x: number): number {
	if (!points.length) return -1;
	let best = 0;
	let distance = Math.abs(points[0].x - x);
	for (let i = 1; i < points.length; i++) {
		const next = Math.abs(points[i].x - x);
		if (next < distance) {
			distance = next;
			best = i;
		}
	}
	return best;
}
