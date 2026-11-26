import { readFileSync } from 'node:fs';
import { error, redirect } from '@sveltejs/kit';
import { describe, expect, it, vi } from 'vitest';
import type { Month, MonthRecap, NetWorth, Series } from '#lib/types.js';
import {
	createLatestRunner,
	createMonthLoader,
	createWorthLoader,
	totalWorth,
	type Breakdown,
	type LazyBreakdown
} from './lazy.js';

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason?: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

function flushMicrotasks(): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, 0));
}

const sampleSeries = (parts: Series['parts']): Series => ({
	currency: 'CHF',
	first: '2026-01-01',
	total: ['1000', '1100'],
	arrived: { '0': '1000' },
	parts,
	notes: ['All accounts included']
});

const assetWorth: NetWorth = {
	series: sampleSeries([
		{ name: 'Cash', values: ['400', '450'], arrived: { '0': '400' } },
		{ name: 'ETFs', values: ['600', '650'], arrived: { '0': '600' } }
	]),
	errors: ['Missing rate for JPY.']
};

const ownerWorth: NetWorth = {
	series: sampleSeries([
		{ name: 'Anna', values: ['500', '550'], arrived: { '0': '500' } },
		{ name: 'Marco', values: ['500', '550'], arrived: { '0': '500' } }
	]),
	errors: []
};

const liquidityWorth: NetWorth = {
	series: sampleSeries([
		{ name: 'Frozen', values: ['300', '320'], arrived: { '0': '300' } },
		{ name: 'Liquid', values: ['700', '780'], arrived: { '0': '700' } }
	]),
	errors: ['Frozen account note']
};

const makeRecap = (month: string, earlier: string | null, later: string | null): MonthRecap => ({
	currency: 'CHF',
	month,
	first: `${month}-01`,
	last: `${month}-28`,
	over: true,
	before: '1000',
	after: '1100',
	moved: '100',
	income: '500',
	spent: '400',
	arrived: '0',
	saved: '100',
	other: '0',
	rate: '20',
	rate_year: '20',
	work: '500',
	rate_salary: '20',
	rate_salary_year: '20',
	categories: [],
	missing: [],
	earlier,
	later
});

const makeMonth = (month: string, earlier: string | null, later: string | null): Month => ({
	month: makeRecap(month, earlier, later),
	errors: []
});

describe('totalWorth', () => {
	it('strips series parts while preserving totals, metadata, and errors', () => {
		const total = totalWorth(assetWorth);
		expect(total).toEqual({
			series: {
				currency: 'CHF',
				first: '2026-01-01',
				total: ['1000', '1100'],
				arrived: { '0': '1000' },
				parts: [],
				notes: ['All accounts included']
			},
			errors: ['Missing rate for JPY.']
		});
		// Does not mutate the source asset series.
		expect(assetWorth.series?.parts).toHaveLength(2);
	});

	it('preserves null series when assets has no series', () => {
		const empty: NetWorth = { series: null, errors: ['Missing rate.'] };
		expect(totalWorth(empty)).toEqual({ series: null, errors: ['Missing rate.'] });
	});
});

describe('createWorthLoader', () => {
	it('serves asset, total, and any pre-seeded initial breakdown synchronously with zero fetches', () => {
		const fetchCut = vi.fn<(by: LazyBreakdown) => Promise<NetWorth>>();
		const loader = createWorthLoader(assetWorth, fetchCut, {
			by: 'owner',
			worth: ownerWorth
		});

		expect(loader.load('asset')).toEqual({ cached: true, value: assetWorth });
		expect(loader.load('total')).toEqual({ cached: true, value: totalWorth(assetWorth) });
		expect(loader.load('owner')).toEqual({ cached: true, value: ownerWorth });
		expect(fetchCut).not.toHaveBeenCalled();
	});

	it('fetches owner and liquidity lazily once, deduplicates in-flight calls, and caches results', async () => {
		const ownerPending = deferred<NetWorth>();
		const liquidityPending = deferred<NetWorth>();
		const fetchCut = vi.fn<(by: LazyBreakdown) => Promise<NetWorth>>((cut) =>
			cut === 'owner' ? ownerPending.promise : liquidityPending.promise
		);
		const loader = createWorthLoader(assetWorth, fetchCut);

		const firstOwner = loader.load('owner');
		const secondOwner = loader.load('owner');
		expect(firstOwner.cached).toBe(false);
		expect(secondOwner.cached).toBe(false);
		if (!firstOwner.cached && !secondOwner.cached) {
			expect(firstOwner.promise).toBe(secondOwner.promise);
		}
		expect(fetchCut).toHaveBeenCalledTimes(1);
		expect(fetchCut).toHaveBeenCalledWith('owner');

		ownerPending.resolve(ownerWorth);
		if (!firstOwner.cached) {
			expect(await firstOwner.promise).toBe(ownerWorth);
		}
		expect(loader.load('owner')).toEqual({ cached: true, value: ownerWorth });
		expect(fetchCut).toHaveBeenCalledTimes(1);

		const liq = loader.load('liquidity');
		expect(liq.cached).toBe(false);
		liquidityPending.resolve(liquidityWorth);
		if (!liq.cached) {
			expect(await liq.promise).toBe(liquidityWorth);
		}
		expect(loader.load('liquidity')).toEqual({ cached: true, value: liquidityWorth });
		expect(fetchCut).toHaveBeenCalledTimes(2);
	});

	it('evicts failed or soft-error fetches so they can be retried', async () => {
		const fetchCut = vi
			.fn<(by: LazyBreakdown) => Promise<NetWorth>>()
			.mockRejectedValueOnce(new TypeError('Failed to fetch'))
			.mockResolvedValueOnce({ series: null, errors: ['Missing rate.'] })
			.mockResolvedValueOnce(ownerWorth);
		const loader = createWorthLoader(assetWorth, fetchCut);

		const first = loader.load('owner');
		expect(first.cached).toBe(false);
		if (!first.cached) {
			await expect(first.promise).rejects.toThrow('Failed to fetch');
		}
		expect(loader.peek('owner')).toBeUndefined();

		const second = loader.load('owner');
		expect(second.cached).toBe(false);
		if (!second.cached) {
			expect(await second.promise).toEqual({ series: null, errors: ['Missing rate.'] });
		}
		expect(loader.peek('owner')).toBeUndefined();

		const third = loader.load('owner');
		expect(third.cached).toBe(false);
		if (!third.cached) {
			expect(await third.promise).toBe(ownerWorth);
		}
		expect(loader.load('owner')).toEqual({ cached: true, value: ownerWorth });
		expect(fetchCut).toHaveBeenCalledTimes(3);
	});
});

describe('createMonthLoader', () => {
	it('seeds both empty key and returned month when initialized without ?month=', async () => {
		const oct = makeMonth('2026-10', '2026-09', null);
		const sep = makeMonth('2026-09', '2026-08', '2026-10');
		const fetchMonth = vi.fn<(month: string) => Promise<Month>>().mockResolvedValue(sep);
		const loader = createMonthLoader('', oct, fetchMonth);

		expect(loader.load('')).toEqual({ cached: true, value: oct });
		expect(loader.load('2026-10')).toEqual({ cached: true, value: oct });
		expect(fetchMonth).not.toHaveBeenCalled();

		const stepBack = loader.load('2026-09');
		expect(stepBack.cached).toBe(false);
		if (!stepBack.cached) {
			expect(await stepBack.promise).toBe(sep);
		}
		expect(fetchMonth).toHaveBeenCalledTimes(1);
		expect(fetchMonth).toHaveBeenCalledWith('2026-09');

		// Stepping forward to 2026-10 or browser Back to '' uses the cache with zero requests.
		expect(loader.load('2026-10')).toEqual({ cached: true, value: oct });
		expect(loader.load('')).toEqual({ cached: true, value: oct });
		expect(fetchMonth).toHaveBeenCalledTimes(1);
	});

	it('does not alias the empty key when initialized with an explicit ?month=', async () => {
		const aug = makeMonth('2026-08', '2026-07', '2026-09');
		const oct = makeMonth('2026-10', '2026-09', null);
		const fetchMonth = vi.fn<(month: string) => Promise<Month>>().mockResolvedValue(oct);
		const loader = createMonthLoader('2026-08', aug, fetchMonth);

		expect(loader.load('2026-08')).toEqual({ cached: true, value: aug });
		expect(loader.peek('')).toBeUndefined();

		const latest = loader.load('');
		expect(latest.cached).toBe(false);
		if (!latest.cached) {
			expect(await latest.promise).toBe(oct);
		}
		// Once fetched, both '' and the returned '2026-10' are cached.
		expect(loader.load('')).toEqual({ cached: true, value: oct });
		expect(loader.load('2026-10')).toEqual({ cached: true, value: oct });
		expect(fetchMonth).toHaveBeenCalledTimes(1);
		expect(fetchMonth).toHaveBeenCalledWith('');
	});

	it('allows retry after network rejection or soft missing-rate response', async () => {
		const oct = makeMonth('2026-10', '2026-09', null);
		const sep = makeMonth('2026-09', '2026-08', '2026-10');
		const fetchMonth = vi
			.fn<(month: string) => Promise<Month>>()
			.mockRejectedValueOnce(new TypeError('offline'))
			.mockResolvedValueOnce({ month: null, errors: ['Missing rate for USD.'] })
			.mockResolvedValueOnce(sep);
		const loader = createMonthLoader('', oct, fetchMonth);

		const attempt1 = loader.load('2026-09');
		if (!attempt1.cached) await expect(attempt1.promise).rejects.toThrow('offline');

		const attempt2 = loader.load('2026-09');
		if (!attempt2.cached) {
			expect(await attempt2.promise).toEqual({ month: null, errors: ['Missing rate for USD.'] });
		}
		expect(loader.peek('2026-09')).toBeUndefined();

		const attempt3 = loader.load('2026-09');
		if (!attempt3.cached) expect(await attempt3.promise).toBe(sep);
		expect(loader.load('2026-09')).toEqual({ cached: true, value: sep });
		expect(fetchMonth).toHaveBeenCalledTimes(3);
	});
});

describe('createLatestRunner', () => {
	it('invokes onImmediate synchronously on cache hits without triggering onLoading', () => {
		const fetchCut = vi.fn<(by: LazyBreakdown) => Promise<NetWorth>>();
		const loader = createWorthLoader(assetWorth, fetchCut);
		const callbacks = {
			subject: () => 'This net worth breakdown',
			onImmediate: vi.fn(),
			onLoading: vi.fn(),
			onSuccess: vi.fn(),
			onError: vi.fn(),
			onRedirect: vi.fn()
		};
		const runner = createLatestRunner<Breakdown, NetWorth>(loader, callbacks);

		runner.run('total');
		expect(callbacks.onImmediate).toHaveBeenCalledWith('total', totalWorth(assetWorth));
		expect(callbacks.onLoading).not.toHaveBeenCalled();
		expect(callbacks.onSuccess).not.toHaveBeenCalled();
	});

	it('drops stale out-of-order resolutions while keeping them cached for later instant switches', async () => {
		const ownerPending = deferred<NetWorth>();
		const liqPending = deferred<NetWorth>();
		const fetchCut = vi.fn<(by: LazyBreakdown) => Promise<NetWorth>>((cut) =>
			cut === 'owner' ? ownerPending.promise : liqPending.promise
		);
		const loader = createWorthLoader(assetWorth, fetchCut);
		const callbacks = {
			subject: () => 'This net worth breakdown',
			onImmediate: vi.fn(),
			onLoading: vi.fn(),
			onSuccess: vi.fn(),
			onError: vi.fn(),
			onRedirect: vi.fn()
		};
		const runner = createLatestRunner<Breakdown, NetWorth>(loader, callbacks);

		runner.run('owner');
		runner.run('liquidity');
		expect(callbacks.onLoading).toHaveBeenNthCalledWith(1, 'owner');
		expect(callbacks.onLoading).toHaveBeenNthCalledWith(2, 'liquidity');

		liqPending.resolve(liquidityWorth);
		await flushMicrotasks();
		expect(callbacks.onSuccess).toHaveBeenCalledTimes(1);
		expect(callbacks.onSuccess).toHaveBeenCalledWith('liquidity', liquidityWorth);

		// Late owner response does not overwrite liquidity, but populates the cache.
		ownerPending.resolve(ownerWorth);
		await flushMicrotasks();
		expect(callbacks.onSuccess).toHaveBeenCalledTimes(1);

		runner.run('owner');
		expect(callbacks.onImmediate).toHaveBeenCalledWith('owner', ownerWorth);
	});

	it('drops stale resolutions and errors when superseded by a synchronous cache hit', async () => {
		const ownerPending = deferred<NetWorth>();
		const liqPending = deferred<NetWorth>();
		const fetchCut = vi.fn<(by: LazyBreakdown) => Promise<NetWorth>>((cut) =>
			cut === 'owner' ? ownerPending.promise : liqPending.promise
		);
		const loader = createWorthLoader(assetWorth, fetchCut);
		const callbacks = {
			subject: () => 'This net worth breakdown',
			onImmediate: vi.fn(),
			onLoading: vi.fn(),
			onSuccess: vi.fn(),
			onError: vi.fn(),
			onRedirect: vi.fn()
		};
		const runner = createLatestRunner<Breakdown, NetWorth>(loader, callbacks);

		runner.run('owner');
		runner.run('total');
		ownerPending.resolve(ownerWorth);
		await flushMicrotasks();
		expect(callbacks.onImmediate).toHaveBeenCalledWith('total', totalWorth(assetWorth));
		expect(callbacks.onSuccess).not.toHaveBeenCalled();

		runner.run('liquidity');
		runner.run('asset');
		liqPending.reject(new TypeError('Failed to fetch'));
		await flushMicrotasks();
		expect(callbacks.onError).not.toHaveBeenCalled();
	});

	it('forwards 401 Redirects via onRedirect and formats HTTP/network errors with refusal()', async () => {
		const redirectErr = (() => {
			try {
				redirect(303, '/login?next=%2Fapp');
			} catch (failure) {
				return failure;
			}
		})();
		const httpErr = (() => {
			try {
				error(502, 'Database busy.');
			} catch (failure) {
				return failure;
			}
		})();
		const fetchCut = vi
			.fn<(by: LazyBreakdown) => Promise<NetWorth>>()
			.mockRejectedValueOnce(redirectErr)
			.mockRejectedValueOnce(httpErr)
			.mockRejectedValueOnce(new TypeError('Failed to fetch'));
		const loader = createWorthLoader(assetWorth, fetchCut);
		const callbacks = {
			subject: () => 'This net worth breakdown',
			onImmediate: vi.fn(),
			onLoading: vi.fn(),
			onSuccess: vi.fn(),
			onError: vi.fn(),
			onRedirect: vi.fn()
		};
		const runner = createLatestRunner<Breakdown, NetWorth>(loader, callbacks);

		runner.run('owner');
		await flushMicrotasks();
		expect(callbacks.onRedirect).toHaveBeenCalledWith('/login?next=%2Fapp');
		expect(callbacks.onError).not.toHaveBeenCalled();

		runner.run('owner');
		await flushMicrotasks();
		expect(callbacks.onError).toHaveBeenNthCalledWith(
			1,
			'owner',
			'This net worth breakdown is not known: Database busy.'
		);

		runner.run('owner');
		await flushMicrotasks();
		expect(callbacks.onError.mock.calls[1]?.[1]).toMatch(
			/^This net worth breakdown is not known: The app could not be reached/
		);
	});

	it('suppresses callbacks after reset() and permanently after dispose()', async () => {
		const pending1 = deferred<NetWorth>();
		const pending2 = deferred<NetWorth>();
		const fetchCut = vi
			.fn<(by: LazyBreakdown) => Promise<NetWorth>>()
			.mockReturnValueOnce(pending1.promise)
			.mockReturnValueOnce(pending2.promise);
		const loader = createWorthLoader(assetWorth, fetchCut);
		const callbacks = {
			subject: () => 'This net worth breakdown',
			onImmediate: vi.fn(),
			onLoading: vi.fn(),
			onSuccess: vi.fn(),
			onError: vi.fn(),
			onRedirect: vi.fn()
		};
		const runner = createLatestRunner<Breakdown, NetWorth>(loader, callbacks);

		runner.run('owner');
		runner.reset();
		pending1.resolve(ownerWorth);
		await flushMicrotasks();
		expect(callbacks.onSuccess).not.toHaveBeenCalled();

		// Still usable after reset().
		runner.run('liquidity');
		runner.dispose();
		pending2.resolve(liquidityWorth);
		await flushMicrotasks();
		expect(callbacks.onSuccess).not.toHaveBeenCalled();

		// No-ops after dispose().
		runner.run('total');
		expect(callbacks.onImmediate).not.toHaveBeenCalled();
	});
});

describe('overview page override identity', () => {
	it('stores worthOverride and monthOverride with $state.raw so loader identity checks match', () => {
		const pageSource = readFileSync(new URL('../../routes/+page.svelte', import.meta.url), 'utf8');
		expect(pageSource).toMatch(/let worthOverride = \$state\.raw</);
		expect(pageSource).toMatch(/let monthOverride = \$state\.raw</);
	});
});
