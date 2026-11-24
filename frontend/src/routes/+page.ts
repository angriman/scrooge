import { get } from '#lib/api.js';
import type { Attention, Month, NetWorth, NetWorthMonths, Overview } from '#lib/types.js';
import { oneOf, pick } from '#lib/view.js';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, url, parent }) => {
	const { session } = await parent();
	// The chart is cut by person only when there are several and they are all shown.
	const people = session.people.length > 1 && !url.searchParams.get('owner');
	const asked = oneOf(url, 'by', ['asset', 'owner', 'total'] as const, 'asset');
	const by = asked === 'owner' && !people ? 'asset' : asked;
	const whose = pick(url, ['owner', 'currency']);
	const series = (cut: string) =>
		get<NetWorth>(fetch, '/api/networth', new URLSearchParams([...whose, ['by', cut]]));
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
	return { shown, assets, worth: cut ?? assets, by, people, month, attention, monthEnds };
};
