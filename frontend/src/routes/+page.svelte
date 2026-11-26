<script lang="ts">
	import { untrack } from 'svelte';
	import { isRedirect } from '@sveltejs/kit';
	import { resolve } from '$app/paths';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { page } from '$app/state';
	import { coveredUpTo } from '#lib/coverage/covered.js';
	import {
		assetColor,
		assetName,
		inOrder,
		liquidityColor,
		liquidityName
	} from '#lib/design/data.js';
	import { addDays } from '#lib/format/date.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import Allocation from '#lib/overview/Allocation.svelte';
	import Investments from '#lib/overview/Investments.svelte';
	import { incomeQuery, returnsQuery } from '#lib/overview/investments.js';
	import {
		BREAKDOWNS,
		createLatestRunner,
		type Breakdown,
		type LatestRunner,
		type MonthLoader,
		type WorthLoader
	} from '#lib/overview/lazy.js';
	import MonthSection from '#lib/overview/MonthSection.svelte';
	import NetWorthCard, { VIEWS, type View } from '#lib/overview/NetWorthCard.svelte';
	import Notices from '#lib/overview/Notices.svelte';
	import { statementsUpTo } from '#lib/overview/through.js';
	import { daysBack, DEFAULT_RANGE, RANGES, rangeLabel, type Range } from '#lib/overview/range.js';
	import type { Month, NetWorth } from '#lib/types.js';
	import EmptyState from '#lib/ui/EmptyState.svelte';
	import { here, oneOf, show } from '#lib/view.js';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const figures = $derived(data.shown.overview);
	const currency = $derived(figures?.currency ?? data.session.currency);
	const owner = $derived(page.url.searchParams.get('owner'));
	// The header's day for whose figures are shown: the card does not name a later one.
	const covered = $derived(coveredUpTo(data.session.people, owner ?? ''));
	// One person alone: nothing on the page is about whose it is.
	const single = $derived(data.session.people.length < 2);
	const title = $derived(
		single
			? t('overview.title')
			: owner
				? t('overview.ownerTitle', { owner })
				: t('overview.householdTitle')
	);

	// The breakdown and month live in the address via shallow updates: switching them fetches
	// only the slice needed and caches it for the current `(owner, currency)` load.
	const askedBy = $derived(oneOf(here(), 'by', BREAKDOWNS, 'asset'));
	const by = $derived<Breakdown>(askedBy === 'owner' && !data.people ? 'asset' : askedBy);
	const monthParam = $derived(here().searchParams.get('month') ?? '');

	// `$state.raw` keeps `loader` unproxied so `=== data.worthLoader` / `=== data.monthLoader`
	// identity checks match.
	let worthOverride = $state.raw<{
		loader: WorthLoader;
		by: Breakdown;
		worth: NetWorth;
		loading: boolean;
		error: string | null;
	} | null>(null);

	const activeWorth = $derived(
		worthOverride?.loader === data.worthLoader ? worthOverride.worth : data.worth
	);
	const seriesBy = $derived(
		worthOverride?.loader === data.worthLoader ? worthOverride.by : data.by
	);
	const worthLoading = $derived(
		worthOverride?.loader === data.worthLoader ? worthOverride.loading : false
	);
	const worthError = $derived(
		worthOverride?.loader === data.worthLoader ? worthOverride.error : null
	);
	const series = $derived(activeWorth.series);

	let monthOverride = $state.raw<{
		loader: MonthLoader;
		month: Month;
		loading: boolean;
		error: string | null;
	} | null>(null);

	const activeMonth = $derived(
		monthOverride?.loader === data.monthLoader ? monthOverride.month : data.month
	);
	const monthLoading = $derived(
		monthOverride?.loader === data.monthLoader ? monthOverride.loading : false
	);
	const monthError = $derived(
		monthOverride?.loader === data.monthLoader ? monthOverride.error : null
	);

	let worthRunner: LatestRunner<Breakdown> | null = null;
	$effect(() => {
		const loader = data.worthLoader;
		const fallbackBy = data.by;
		const fallbackWorth = data.worth;
		const currentWorth = () =>
			worthOverride?.loader === loader
				? { by: worthOverride.by, worth: worthOverride.worth }
				: { by: fallbackBy, worth: fallbackWorth };
		const runner = createLatestRunner<Breakdown, NetWorth>(loader, {
			subject: () => t('overview.worthSubject'),
			onImmediate(chosen, sent) {
				worthOverride = { loader, by: chosen, worth: sent, loading: false, error: null };
			},
			onLoading() {
				worthOverride = { loader, ...currentWorth(), loading: true, error: null };
			},
			onSuccess(chosen, sent) {
				if (sent.series) {
					worthOverride = { loader, by: chosen, worth: sent, loading: false, error: null };
				} else {
					worthOverride = {
						loader,
						...currentWorth(),
						loading: false,
						error:
							sent.errors.join(' ') ||
							t('overview.notKnownWhy', {
								what: t('overview.worthSubject'),
								why: t('api.unreachable')
							})
					};
				}
			},
			onError(_chosen, message) {
				worthOverride = { loader, ...currentWorth(), loading: false, error: message };
			},
			onRedirect(target) {
				location.assign(target);
			}
		});
		worthRunner = runner;
		return () => {
			runner.dispose();
			if (worthRunner === runner) worthRunner = null;
		};
	});

	$effect(() => {
		const loader = data.worthLoader;
		const chosen = by;
		const initialBy = data.by;
		const hasAssets = data.assets.series !== null;
		untrack(() => {
			if (!hasAssets) return;
			if (worthOverride?.loader !== loader && chosen === initialBy) return;
			worthRunner?.run(chosen);
		});
	});

	let monthRunner: LatestRunner<string> | null = null;
	$effect(() => {
		const loader = data.monthLoader;
		const fallbackMonth = data.month;
		const currentMonth = () =>
			monthOverride?.loader === loader ? monthOverride.month : fallbackMonth;
		const runner = createLatestRunner<string, Month>(loader, {
			subject: () => t('monthSection.monthSubject'),
			onImmediate(_chosen, sent) {
				monthOverride = { loader, month: sent, loading: false, error: null };
			},
			onLoading() {
				monthOverride = { loader, month: currentMonth(), loading: true, error: null };
			},
			onSuccess(_chosen, sent) {
				if (sent.month) {
					monthOverride = { loader, month: sent, loading: false, error: null };
				} else {
					monthOverride = {
						loader,
						month: currentMonth(),
						loading: false,
						error:
							sent.errors.join(' ') ||
							t('overview.notKnownWhy', {
								what: t('monthSection.monthSubject'),
								why: t('api.unreachable')
							})
					};
				}
			},
			onError(_chosen, message) {
				monthOverride = { loader, month: currentMonth(), loading: false, error: message };
			},
			onRedirect(target) {
				location.assign(target);
			}
		});
		monthRunner = runner;
		return () => {
			runner.dispose();
			if (monthRunner === runner) monthRunner = null;
		};
	});

	$effect(() => {
		const loader = data.monthLoader;
		const chosen = monthParam;
		const initialParam = data.monthParam;
		untrack(() => {
			if (monthOverride?.loader !== loader && chosen === initialParam) return;
			monthRunner?.run(chosen);
		});
	});

	function onmonth(chosen: string) {
		if (chosen === monthParam) {
			monthRunner?.run(chosen);
			return;
		}
		void show(here(), { month: chosen }, { shallow: true });
	}

	// The range only changes the address: the page has every day already.
	const range = $derived(
		oneOf(
			here(),
			'range',
			RANGES.map((option) => option.value),
			DEFAULT_RANGE
		)
	);
	// The chart or the table of the month-ends: a view of the page, which loads nothing again.
	const view = $derived(
		oneOf(
			here(),
			'view',
			VIEWS.map((option) => option.value),
			'chart'
		)
	);
	const months = $derived(view === 'table' ? data.monthEnds() : null);
	const lastDay = $derived(series?.first ? addDays(series.first, series.total.length - 1) : null);
	const back = $derived(lastDay ? daysBack(range, lastDay) : null);

	// The investments card follows the range: each is asked once, when first chosen. Signed out
	// meanwhile, the card says so and the page goes to sign in, as a load of the page would.
	const returned = $derived(data.returnsOver(range));
	$effect(() => {
		returned.catch((failure) => {
			if (isRedirect(failure)) location.assign(failure.location);
		});
	});
	const investments = (query: URLSearchParams) =>
		resolve('/investments') + (query.size ? `?${query}` : '');
	const returnsHref = $derived(investments(returnsQuery(here(), range)));
	const incomeHref = $derived(investments(incomeQuery(here())));

	const breakdowns = $derived([
		{ value: 'asset', label: t('overview.byAsset') },
		...(data.people ? [{ value: 'owner', label: t('overview.byOwner') }] : []),
		{ value: 'liquidity', label: t('overview.byLiquidity') },
		{ value: 'total', label: t('overview.total') }
	]);
	const nameOf = $derived(
		seriesBy === 'owner'
			? (name: string) => name
			: seriesBy === 'liquidity'
				? liquidityName
				: assetName
	);
	const colors = $derived.by(() => {
		if (seriesBy === 'owner') return inOrder(data.session.people.map((person) => person.name));
		const of = seriesBy === 'liquidity' ? liquidityColor : assetColor;
		return new Map((series?.parts ?? []).map((part) => [part.name, of(part.name)]));
	});
	const pageErrors = $derived([
		...new Set([...data.shown.errors, ...activeWorth.errors, ...activeMonth.errors])
	]);
</script>

<svelte:head>
	<title>{t('overview.pageTitle')}</title>
</svelte:head>

<Notices
	attention={data.attention}
	onePerson={single || owner !== null}
	readOnly={data.session.read_only}
/>

{#each pageErrors as error (error)}
	<p
		class="mb-4 flex items-start gap-2 rounded-panel border border-caution/40 bg-caution-soft px-4 py-3 text-body text-ink"
		role="status"
	>
		<CircleAlert class="mt-0.5 size-4 shrink-0 text-caution" aria-hidden="true" />
		{error}
	</p>
{/each}

{#if !figures || !figures.day || !series}
	<EmptyState
		heading={t('overview.emptyHeading')}
		body={t('overview.emptyBody')}
		importCta={!data.session.read_only}
	/>
{:else}
	<div class="grid grid-cols-1 gap-4">
		<NetWorthCard
			{series}
			{seriesBy}
			{colors}
			{nameOf}
			{title}
			{currency}
			through={statementsUpTo(figures.through, figures.day, covered)}
			{range}
			onrange={(chosen: Range) => show(here(), { range: chosen }, { shallow: true })}
			{breakdowns}
			{by}
			onby={(chosen) => show(here(), { by: chosen === 'asset' ? null : chosen }, { shallow: true })}
			loading={worthLoading}
			error={worthError}
			{view}
			onview={(chosen: View) =>
				show(here(), { view: chosen === 'chart' ? null : chosen }, { shallow: true })}
			{months}
		/>

		<div class="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
			<Allocation
				assets={figures.assets}
				held={figures.held}
				series={data.assets.series}
				{back}
				label={rangeLabel(range, series.first)}
				{currency}
			/>
			<Investments {returned} paid={data.paid} {range} {returnsHref} {incomeHref} />
		</div>

		{#if activeMonth.month}
			<MonthSection recap={activeMonth.month} {onmonth} loading={monthLoading} error={monthError} />
		{/if}
	</div>
{/if}
