import { get } from '#lib/api.js';
import { loaded, paidLabel, returnedLabel, type Loaded } from '#lib/overview/investments.js';
import {
	BREAKDOWNS,
	createMonthLoader,
	createWorthLoader,
	totalWorth,
	type LazyBreakdown
} from '#lib/overview/lazy.js';
import { DEFAULT_RANGE, RANGES, type Range } from '#lib/overview/range.js';
import type {
	Attention,
	InvestmentIncome,
	InvestmentPerformance,
	Month,
	NetWorth,
	NetWorthMonths,
	Overview
} from '#lib/types.js';
import { oneOf, pick } from '#lib/view.js';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, url, parent, untrack }) => {
	const { session } = await parent();
	// Only owner and currency reload the page; by, month, and range switch in place via shallow
	// URL updates and lazy per-scope caches.
	const whose = pick(url, ['owner', 'currency']);
	// The chart is cut by person only when there are several and they are all shown.
	const people = session.people.length > 1 && !whose.get('owner');
	const { by, monthParam, range } = untrack(() => {
		const asked = oneOf(url, 'by', BREAKDOWNS, 'asset');
		return {
			by: asked === 'owner' && !people ? ('asset' as const) : asked,
			monthParam: url.searchParams.get('month') ?? '',
			range: oneOf(
				url,
				'range',
				RANGES.map((option) => option.value),
				DEFAULT_RANGE
			)
		};
	});
	const fetchCut = (cut: LazyBreakdown) =>
		get<NetWorth>(fetch, '/api/networth', new URLSearchParams([...whose, ['by', cut]]));
	const fetchMonth = (asked: string) =>
		get<Month>(
			fetch,
			'/api/month',
			asked ? new URLSearchParams([...whose, ['month', asked]]) : whose
		);
	// What the investments returned over a range, asked once a range is first looked at: the
	// range changes only the address, so another is asked when it is chosen. Neither that nor
	// what they paid is waited for: the card shows each when it comes, and what the app cannot
	// answer for as a sentence, so neither holds up nor stops the page.
	const returns = new Map<Range, Promise<Loaded<InvestmentPerformance>>>();
	const returnsOver = (over: Range) => {
		let asked = returns.get(over);
		if (!asked) {
			const query = new URLSearchParams([...whose, ['range', over]]);
			asked = loaded(get<InvestmentPerformance>(fetch, '/api/performance', query), returnedLabel());
			returns.set(over, asked);
		}
		return asked;
	};
	returnsOver(range);
	const paid = loaded(get<InvestmentIncome>(fetch, '/api/income', whose), paidLabel());
	// The allocation sets each type against the range: the chart's own series, when cut by type.
	// `total` is derived in memory from `asset`; only `owner` and `liquidity` need a second call.
	const [shown, assets, cut, month, attention] = await Promise.all([
		get<Overview>(fetch, '/api/overview', whose),
		get<NetWorth>(fetch, '/api/networth', new URLSearchParams([...whose, ['by', 'asset']])),
		by === 'owner' || by === 'liquidity' ? fetchCut(by) : null,
		fetchMonth(monthParam),
		get<Attention>(fetch, '/api/attention', new URLSearchParams([...whose, ['recurring', '1']]))
	]);
	const worth = cut ?? (by === 'total' ? totalWorth(assets) : assets);
	const worthLoader = createWorthLoader(
		assets,
		fetchCut,
		cut ? { by: by as LazyBreakdown, worth: cut } : undefined
	);
	const monthLoader = createMonthLoader(monthParam, month, fetchMonth);
	// The month-end table values everything once more, in each of its currencies: it is asked
	// for only once it is shown (`view=table`, a view of the page that loads nothing else), and
	// kept until the page loads again.
	let ends: Promise<NetWorthMonths> | undefined;
	const monthEnds = () => (ends ??= get<NetWorthMonths>(fetch, '/api/networth/months', whose));
	return {
		shown,
		assets,
		worth,
		by,
		people,
		month,
		monthParam,
		worthLoader,
		monthLoader,
		attention,
		monthEnds,
		returnsOver,
		paid
	};
};
