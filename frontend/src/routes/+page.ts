import { get } from '#lib/api.js';
import { loaded, PAID, RETURNED, type Loaded } from '#lib/overview/investments.js';
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

export const load: PageLoad = async ({ fetch, url, parent }) => {
	const { session } = await parent();
	// The chart is cut by person only when there are several and they are all shown.
	const people = session.people.length > 1 && !url.searchParams.get('owner');
	const asked = oneOf(url, 'by', ['asset', 'owner', 'liquidity', 'total'] as const, 'asset');
	const by = asked === 'owner' && !people ? 'asset' : asked;
	const whose = pick(url, ['owner', 'currency']);
	const series = (cut: string) =>
		get<NetWorth>(fetch, '/api/networth', new URLSearchParams([...whose, ['by', cut]]));
	// What the investments returned over a range, asked once a range is first looked at: the
	// range changes only the address, so another is asked when it is chosen. Neither that nor
	// what they paid is waited for: the card shows each when it comes, and what the app cannot
	// answer for as a sentence, so neither holds up nor stops the page.
	const range = oneOf(
		url,
		'range',
		RANGES.map((option) => option.value),
		DEFAULT_RANGE
	);
	const returns = new Map<Range, Promise<Loaded<InvestmentPerformance>>>();
	const returnsOver = (over: Range) => {
		let asked = returns.get(over);
		if (!asked) {
			const query = new URLSearchParams([...whose, ['range', over]]);
			asked = loaded(get<InvestmentPerformance>(fetch, '/api/performance', query), RETURNED);
			returns.set(over, asked);
		}
		return asked;
	};
	returnsOver(range);
	const paid = loaded(get<InvestmentIncome>(fetch, '/api/income', whose), PAID);
	// The allocation sets each type against the range: the chart's own series, when cut by type.
	const [shown, assets, cut, month, attention] = await Promise.all([
		get<Overview>(fetch, '/api/overview', whose),
		series('asset'),
		by === 'asset' ? null : series(by),
		get<Month>(fetch, '/api/month', pick(url, ['owner', 'currency', 'month'])),
		get<Attention>(
			fetch,
			'/api/attention',
			new URLSearchParams([...pick(url, ['owner', 'currency']), ['recurring', '1']])
		)
	]);
	// The month-end table values everything once more, in each of its currencies: it is asked
	// for only once it is shown (`view=table`, a view of the page that loads nothing else), and
	// kept until the page loads again.
	let ends: Promise<NetWorthMonths> | undefined;
	const monthEnds = () => (ends ??= get<NetWorthMonths>(fetch, '/api/networth/months', whose));
	return {
		shown,
		assets,
		worth: cut ?? assets,
		by,
		people,
		month,
		attention,
		monthEnds,
		returnsOver,
		paid
	};
};
