// Lazy loading and caching for Overview's chart breakdown (`/api/networth?by=...`) and month
// recap (`/api/month?month=...`), scoped to one `(owner, currency)` page load. Switching between
// already-loaded views is synchronous; uncached requests are deduplicated, cached on success,
// and sequence-guarded so out-of-order responses never overwrite a newer selection.

import { isRedirect } from '@sveltejs/kit';
import type { Month, NetWorth } from '#lib/types.js';
import { refusal } from './investments.js';

export const BREAKDOWNS = ['asset', 'owner', 'liquidity', 'total'] as const;
export type Breakdown = (typeof BREAKDOWNS)[number];
export type LazyBreakdown = Exclude<Breakdown, 'asset' | 'total'>;

export type LazyResult<V> = { cached: true; value: V } | { cached: false; promise: Promise<V> };

export interface LazyCacheOptions<K, V> {
	/** Extra keys to cache a resolved value under (e.g. the month string returned by `/api/month`). */
	aliasesOf?: (key: K, value: V) => readonly K[];
	/** Whether to store a resolved value in the cache (defaults to always caching). */
	shouldCache?: (value: V) => boolean;
}

export interface LazyCache<K, V> {
	peek(key: K): V | undefined;
	seed(key: K, value: V, aliases?: readonly K[]): void;
	load(key: K): LazyResult<V>;
}

/** Derive the `by=total` net worth response in memory from the already-loaded `by=asset` series. */
export function totalWorth(assets: NetWorth): NetWorth {
	return {
		...assets,
		series: assets.series ? { ...assets.series, parts: [] } : null
	};
}

/** Per-scope cache with synchronous reads, in-flight deduplication, alias seeding, and retry on
 * rejection. */
export function createLazyCache<K, V>(
	fetchValue: (key: K) => Promise<V>,
	options: LazyCacheOptions<K, V> = {}
): LazyCache<K, V> {
	const resolved = new Map<K, V>();
	const inflight = new Map<K, Promise<V>>();
	const { aliasesOf, shouldCache } = options;

	function seed(key: K, value: V, aliases: readonly K[] = []): void {
		resolved.set(key, value);
		for (const alias of aliases) resolved.set(alias, value);
	}

	function peek(key: K): V | undefined {
		return resolved.get(key);
	}

	function load(key: K): LazyResult<V> {
		if (resolved.has(key)) {
			return { cached: true, value: resolved.get(key)! };
		}
		const pending = inflight.get(key);
		if (pending) {
			return { cached: false, promise: pending };
		}
		const promise = fetchValue(key).then(
			(value) => {
				inflight.delete(key);
				if (!shouldCache || shouldCache(value)) {
					seed(key, value, aliasesOf ? aliasesOf(key, value) : []);
				}
				return value;
			},
			(failure) => {
				inflight.delete(key);
				throw failure;
			}
		);
		inflight.set(key, promise);
		return { cached: false, promise };
	}

	return { peek, seed, load };
}

export interface WorthLoader {
	peek(by: Breakdown): NetWorth | undefined;
	load(by: Breakdown): LazyResult<NetWorth>;
}

/** Lazy breakdown loader seeded with `asset`, derived `total`, and any breakdown loaded initially. */
export function createWorthLoader(
	assets: NetWorth,
	fetchCut: (by: LazyBreakdown) => Promise<NetWorth>,
	initial?: { by: Breakdown; worth: NetWorth }
): WorthLoader {
	const cache = createLazyCache<Breakdown, NetWorth>(
		(by) => {
			if (by === 'asset') return Promise.resolve(assets);
			if (by === 'total') return Promise.resolve(totalWorth(assets));
			return fetchCut(by);
		},
		{ shouldCache: (worth) => worth.series !== null }
	);
	cache.seed('asset', assets);
	cache.seed('total', totalWorth(assets));
	if (
		initial &&
		initial.by !== 'asset' &&
		initial.by !== 'total' &&
		initial.worth.series !== null
	) {
		cache.seed(initial.by, initial.worth);
	}
	return { peek: cache.peek, load: cache.load };
}

export interface MonthLoader {
	peek(month: string): Month | undefined;
	load(month: string): LazyResult<Month>;
}

/** Lazy month loader seeded with the initial month response. When loaded without `?month=`,
 * aliases both `''` and the returned `recap.month` so stepping away and back needs no refetch. */
export function createMonthLoader(
	initialParam: string,
	initialMonth: Month,
	fetchMonth: (month: string) => Promise<Month>
): MonthLoader {
	const cache = createLazyCache<string, Month>(fetchMonth, {
		aliasesOf: (asked, sent) =>
			sent.month && sent.month.month !== asked ? [sent.month.month] : [],
		shouldCache: (sent) => sent.month !== null
	});
	if (initialMonth.month !== null) {
		const aliases = initialMonth.month.month !== initialParam ? [initialMonth.month.month] : [];
		cache.seed(initialParam, initialMonth, aliases);
	}
	return { peek: cache.peek, load: cache.load };
}

export interface LatestRunnerCallbacks<K, V> {
	subject: () => string;
	onImmediate: (key: K, value: V) => void;
	onLoading: (key: K) => void;
	onSuccess: (key: K, value: V) => void;
	onError: (key: K, message: string) => void;
	onRedirect: (location: string) => void;
}

export interface LatestRunner<K> {
	run(key: K): void;
	reset(): void;
	dispose(): void;
}

/** Sequence-guarded coordinator over a lazy loader: serves cached hits synchronously, suppresses
 * stale resolutions/errors when superseded, and forwards 401 redirects. */
export function createLatestRunner<K, V>(
	loader: Pick<LazyCache<K, V>, 'load'>,
	callbacks: LatestRunnerCallbacks<K, V>
): LatestRunner<K> {
	let seq = 0;
	let disposed = false;

	function run(key: K): void {
		if (disposed) return;
		const ticket = ++seq;
		const result = loader.load(key);
		if (result.cached) {
			callbacks.onImmediate(key, result.value);
			return;
		}
		callbacks.onLoading(key);
		result.promise.then(
			(value) => {
				if (disposed || ticket !== seq) return;
				callbacks.onSuccess(key, value);
			},
			(failure) => {
				if (disposed || ticket !== seq) return;
				if (isRedirect(failure)) {
					callbacks.onRedirect(failure.location);
					return;
				}
				callbacks.onError(key, refusal(failure, callbacks.subject()));
			}
		);
	}

	function reset(): void {
		seq += 1;
	}

	function dispose(): void {
		disposed = true;
		seq += 1;
	}

	return { run, reset, dispose };
}
