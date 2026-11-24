<script lang="ts">
	import { isRedirect } from '@sveltejs/kit';
	import { resolve } from '$app/paths';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { page } from '$app/state';
	import { coveredUpTo } from '#lib/coverage/covered.js';
	import { assetColor, inOrder, liquidityColor } from '#lib/design/data.js';
	import { addDays } from '#lib/format/date.js';
	import Allocation from '#lib/overview/Allocation.svelte';
	import Investments from '#lib/overview/Investments.svelte';
	import { incomeQuery, returnsQuery } from '#lib/overview/investments.js';
	import MonthSection from '#lib/overview/MonthSection.svelte';
	import NetWorthCard, { VIEWS, type View } from '#lib/overview/NetWorthCard.svelte';
	import Notices from '#lib/overview/Notices.svelte';
	import { statementsUpTo } from '#lib/overview/through.js';
	import { daysBack, DEFAULT_RANGE, RANGES, rangeLabel, type Range } from '#lib/overview/range.js';
	import { here, oneOf, show } from '#lib/view.js';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const figures = $derived(data.shown.overview);
	const series = $derived(data.worth.series);
	const currency = $derived(figures?.currency ?? data.session.currency);
	const owner = $derived(page.url.searchParams.get('owner'));
	// The header's day for whose figures are shown: the card does not name a later one.
	const covered = $derived(coveredUpTo(data.session.people, owner ?? ''));
	// One person alone: nothing on the page is about whose it is.
	const single = $derived(data.session.people.length < 2);
	const title = $derived(
		single ? 'Net worth' : owner ? `${owner}’s net worth` : 'Household net worth'
	);

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
	const lastDay = $derived(
		series?.first ? addDays(series.first, series.total.length - 1) : null
	);
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
		{ value: 'asset', label: 'By type' },
		...(data.people ? [{ value: 'owner', label: 'By person' }] : []),
		{ value: 'liquidity', label: 'By liquidity' },
		{ value: 'total', label: 'Total' }
	]);
	const colors = $derived.by(() => {
		if (data.by === 'owner') return inOrder(data.session.people.map((person) => person.name));
		const of = data.by === 'liquidity' ? liquidityColor : assetColor;
		return new Map((series?.parts ?? []).map((part) => [part.name, of(part.name)]));
	});
</script>

<svelte:head>
	<title>Overview · scrooge</title>
</svelte:head>

<Notices
	attention={data.attention}
	onePerson={single || owner !== null}
	readOnly={data.session.read_only}
/>

{#each [...data.shown.errors, ...data.worth.errors, ...data.month.errors] as error (error)}
	<p
		class="mb-4 flex items-start gap-2 rounded-panel border border-caution/40 bg-caution-soft px-4 py-3 text-body text-ink"
		role="status"
	>
		<CircleAlert class="mt-0.5 size-4 shrink-0 text-caution" aria-hidden="true" />
		{error}
	</p>
{/each}

{#if !figures || !figures.day || !series}
	<section class="rounded-lead border border-line bg-panel px-6 py-16 text-center">
		<h1 class="text-heading font-medium text-ink">Import your first statements</h1>
		<p class="mx-auto mt-2 max-w-prose text-body text-ink-secondary">
			Net worth, holdings and cash flow show here once a statement is committed.
		</p>
		<a
			href={resolve('/import')}
			class="mt-6 inline-flex rounded-control bg-brand px-4 py-2 text-body font-medium text-on-brand hover:bg-brand-hover"
		>
			Import statements
		</a>
	</section>
{:else}
	<div class="grid grid-cols-1 gap-4">
		{#key data.by}
			<NetWorthCard
				{series}
				{colors}
				{title}
				{currency}
				through={statementsUpTo(figures.through, figures.day, covered)}
				{range}
				onrange={(chosen: Range) =>
					show(here(), { range: chosen === DEFAULT_RANGE ? null : chosen }, { shallow: true })}
				{breakdowns}
				by={data.by}
				onby={(by) => show(here(), { by: by === 'asset' ? null : by })}
				{view}
				onview={(chosen: View) =>
					show(here(), { view: chosen === 'chart' ? null : chosen }, { shallow: true })}
				{months}
			/>
		{/key}

		<!-- The month next: how net worth moved in it and why, before what it is made of. -->
		{#if data.month.month}
			<MonthSection recap={data.month.month} />
		{/if}

		<div class="grid grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
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
	</div>

{/if}
