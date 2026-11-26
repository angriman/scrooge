import { describe, expect, it } from 'vitest';
import type { StatementsAccount } from '#lib/types.js';
import {
	accountKey,
	accountParam,
	balancePlot,
	findAccount,
	nearestBalancePoint,
	timelineTicks
} from './accounts.js';

function makeAccount(
	owner: string,
	account: string,
	source: string | null = 'migros'
): StatementsAccount {
	return {
		owner,
		account,
		label: source ? `${source} statement` : '',
		committed: source ? '2026-10-31' : null,
		source,
		staged: null,
		behind: false,
		closed: null,
		monthly: Boolean(source),
		balance: '1000.00',
		spans: [],
		statements: [],
		holds: null,
		parts: [account],
		typed: [],
		typable: source === null ? 'both' : 'none'
	};
}

describe('account identity and query params', () => {
	const lukasMigros = makeAccount('Lukas', 'Migros', 'migros');
	const annaMigros = makeAccount('Anna', 'Migros', 'migros');
	const lukasIbkr = makeAccount('Lukas', 'IBKR', 'ibkr');
	const lukasCash = makeAccount('Lukas', 'Kasse', null);
	const all = [lukasMigros, annaMigros, lukasIbkr, lukasCash];

	it('builds a stable key including null sources', () => {
		expect(accountKey(lukasMigros)).toBe('Lukas|Migros|migros');
		expect(accountKey(lukasCash)).toBe('Lukas|Kasse|');
	});

	it('uses the bare account name when unique and falls back to full key on duplicates', () => {
		expect(accountParam(lukasIbkr, all)).toBe('IBKR');
		expect(accountParam(lukasCash, all)).toBe('Kasse');
		expect(accountParam(lukasMigros, all)).toBe('Lukas|Migros|migros');
		expect(accountParam(annaMigros, all)).toBe('Anna|Migros|migros');
	});

	it('finds an account by full key first, then bare name, else null', () => {
		expect(findAccount(all, 'Anna|Migros|migros')).toBe(annaMigros);
		expect(findAccount(all, 'Lukas|Kasse|')).toBe(lukasCash);
		expect(findAccount(all, 'IBKR')).toBe(lukasIbkr);
		expect(findAccount(all, 'Migros')).toBe(lukasMigros);
		expect(findAccount(all, 'Unknown')).toBeNull();
		expect(findAccount(all, null)).toBeNull();
	});
});

describe('timelineTicks', () => {
	it('uses short month names with year only on January at 1Y on wide desktop', () => {
		const ticks = timelineTicks('2025-11-26', '2026-11-26', 700, 'en');
		const labels = ticks.filter((tick) => tick.label !== null).map((tick) => tick.label);
		expect(labels).toContain('Jan 2026');
		expect(labels).toContain('Feb');
		expect(labels).toContain('Nov');
		// Non-January months never repeat the year
		expect(labels.filter((label) => /\d{4}/.test(label!))).toEqual(['Jan 2026']);
	});

	it('localizes month labels to Italian', () => {
		const ticks = timelineTicks('2025-11-26', '2026-11-26', 700, 'it');
		const labels = ticks.filter((tick) => tick.label !== null).map((tick) => tick.label);
		expect(labels).toContain('Gen 2026');
		expect(labels).toContain('Mag');
		expect(labels).toContain('Ott');
	});

	it('drops colliding neighbors while keeping Jan 2026 on narrower desktop widths', () => {
		const narrow = timelineTicks('2025-11-26', '2026-11-26', 320, 'en');
		const labels = narrow.filter((tick) => tick.label !== null).map((tick) => tick.label);
		expect(labels).toContain('Jan 2026');
		// Dec is at ~1.4% and collides with Jan 2026 at ~9.9% on 320px/480px widths
		expect(labels).not.toContain('Dec');
		expect(labels.length).toBeLessThan(12);

		const medium = timelineTicks('2025-11-26', '2026-11-26', 480, 'en');
		const mediumLabels = medium.filter((tick) => tick.label !== null).map((tick) => tick.label);
		expect(mediumLabels).toContain('Jan 2026');
		expect(mediumLabels).not.toContain('Dec');
	});

	it('left-aligns January when it falls right near the left edge so the year is not clipped', () => {
		const ticks = timelineTicks('2025-12-20', '2026-12-20', 600, 'en');
		const jan = ticks.find((tick) => tick.label === 'Jan 2026');
		expect(jan).toBeDefined();
		expect(jan?.align).toBe('left');
	});

	it('shows yearly ticks on 3Y and long All ranges', () => {
		const ticks = timelineTicks('2023-11-26', '2026-11-26', 500, 'en');
		expect(ticks.map((tick) => tick.label)).toEqual(['2024', '2025', '2026']);
	});

	it('returns unlabelled gridline ticks before track width is measured', () => {
		const ticks = timelineTicks('2025-11-26', '2026-11-26', 0, 'en');
		expect(ticks.length).toBe(12);
		expect(ticks.every((tick) => tick.label === null)).toBe(true);
	});
});

describe('balancePlot and nearestBalancePoint', () => {
	it('returns null for fewer than two month-ends', () => {
		expect(balancePlot([], 400, 120)).toBeNull();
		expect(balancePlot([{ day: '2026-09-30', balance: '500.00' }], 400, 120)).toBeNull();
	});

	it('plots positive balances without a zero line', () => {
		const plot = balancePlot(
			[
				{ day: '2026-07-31', balance: '100.00' },
				{ day: '2026-08-31', balance: '300.00' },
				{ day: '2026-09-30', balance: '200.00' }
			],
			420,
			120,
			{ top: 10, right: 10, bottom: 10, left: 10 }
		);
		expect(plot).not.toBeNull();
		expect(plot!.zeroY).toBeNull();
		expect(plot!.points.map((p) => [p.x, p.y])).toEqual([
			[10, 110],
			[210, 10],
			[410, 60]
		]);
		expect(nearestBalancePoint(plot!.points, 195)).toBe(1);
		expect(nearestBalancePoint(plot!.points, 0)).toBe(0);
		expect(nearestBalancePoint(plot!.points, 500)).toBe(2);
	});

	it('plots negative (credit card) balances cleanly without clamping low to zero', () => {
		const plot = balancePlot(
			[
				{ day: '2026-08-31', balance: '-300.00' },
				{ day: '2026-09-30', balance: '-150.00' }
			],
			220,
			120,
			{ top: 10, right: 10, bottom: 10, left: 10 }
		);
		expect(plot).not.toBeNull();
		expect(plot!.zeroY).toBeNull();
		expect(plot!.points[0].y).toBe(110);
		expect(plot!.points[1].y).toBe(10);
	});

	it('draws a zero reference line when balances cross zero', () => {
		const plot = balancePlot(
			[
				{ day: '2026-08-31', balance: '-100.00' },
				{ day: '2026-09-30', balance: '100.00' }
			],
			220,
			120,
			{ top: 10, right: 10, bottom: 10, left: 10 }
		);
		expect(plot).not.toBeNull();
		expect(plot!.zeroY).toBe(60);
		expect(plot!.baseY).toBe(60);
	});

	it('centers flat series vertically and handles empty nearest lookup', () => {
		const plot = balancePlot(
			[
				{ day: '2026-08-31', balance: '50.00' },
				{ day: '2026-09-30', balance: '50.00' }
			],
			220,
			120,
			{ top: 10, right: 10, bottom: 10, left: 10 }
		);
		expect(plot!.points.map((p) => p.y)).toEqual([60, 60]);
		expect(nearestBalancePoint([], 50)).toBe(-1);
	});
});
