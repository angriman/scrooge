<script lang="ts">
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { page } from '$app/state';
	import { assetColor, inOrder } from '#lib/design/data.js';
	import { addDays } from '#lib/format/date.js';
	import Allocation from '#lib/overview/Allocation.svelte';
	import Checks from '#lib/overview/Checks.svelte';
	import Holdings from '#lib/overview/Holdings.svelte';
	import MonthSection from '#lib/overview/MonthSection.svelte';
	import NetWorthCard from '#lib/overview/NetWorthCard.svelte';
	import Notices from '#lib/overview/Notices.svelte';
	import { daysBack, DEFAULT_RANGE, RANGES, rangeLabel, type Range } from '#lib/overview/range.js';
	import { here, oneOf, show } from '#lib/view.js';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const figures = $derived(data.shown.overview);
	const series = $derived(data.worth.series);
	const currency = $derived(figures?.currency ?? data.session.currency);
	const owner = $derived(page.url.searchParams.get('owner'));
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
	const lastDay = $derived(
		series?.first ? addDays(series.first, series.total.length - 1) : null
	);
	const back = $derived(lastDay ? daysBack(range, lastDay) : null);

	const breakdowns = $derived([
		{ value: 'asset', label: 'By type' },
		...(data.people ? [{ value: 'owner', label: 'By person' }] : []),
		{ value: 'total', label: 'Total' }
	]);
	const colors = $derived(
		data.by === 'owner'
			? inOrder(data.session.people.map((person) => person.name))
			: new Map((series?.parts ?? []).map((part) => [part.name, assetColor(part.name)]))
	);
</script>

<svelte:head>
	<title>Overview · scrooge</title>
</svelte:head>

<Notices attention={data.attention} onePerson={single || owner !== null} />

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
			href="/import"
			data-sveltekit-reload
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
				through={figures.through && figures.through !== figures.day ? figures.through : null}
				{range}
				onrange={(chosen: Range) =>
					show(here(), { range: chosen === DEFAULT_RANGE ? null : chosen }, { shallow: true })}
				{breakdowns}
				by={data.by}
				onby={(by) => show(here(), { by: by === 'asset' ? null : by })}
			/>
		{/key}

		<div class="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
			<Allocation
				assets={figures.assets}
				held={figures.held}
				series={data.assets.series}
				{back}
				label={rangeLabel(range, series.first)}
				{currency}
			/>
			<Holdings top={figures.top} rest={figures.rest} logos={data.shown.logos} {currency} />
		</div>
	</div>

	{#if data.month.month}
		<MonthSection recap={data.month.month} />
	{/if}

	{#if data.shown.checks.length}
		<Checks checks={data.shown.checks} open={page.url.hash === '#checks'} {single} />
	{/if}
{/if}
