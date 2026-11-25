<script lang="ts">
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { tick } from 'svelte';
	import { page } from '$app/state';
	import { day } from '#lib/format/date.js';
	import { money, percent, percentOf, sum } from '#lib/format/number.js';
	import Bills from '#lib/holdings/Bills.svelte';
	import HoldingsList from '#lib/holdings/HoldingsList.svelte';
	import HoldingsMap from '#lib/holdings/HoldingsMap.svelte';
	import Spread from '#lib/holdings/Spread.svelte';
	import { rangeName } from '#lib/cashflow/names.js';
	import Costs from '#lib/investments/Costs.svelte';
	import Income from '#lib/investments/Income.svelte';
	import { chosenRange, DEFAULT_RANGE } from '#lib/investments/income.js';
	import * as returns from '#lib/investments/performance.js';
	import Returns from '#lib/investments/Returns.svelte';
	import type { HeldInstrument } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Performance from '#lib/ui/Performance.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import { oneOf, show } from '#lib/view.js';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const held = $derived(data.held?.holdings);
	const errors = $derived([
		...new Set([
			...(data.held?.errors ?? data.income?.errors ?? data.performance?.errors ?? []),
			...(data.costs?.sent?.errors ?? [])
		])
	]);
	const owner = $derived(page.url.searchParams.get('owner'));
	const people = $derived(data.session.people.length > 1 && !owner);
	const title = $derived(
		data.session.people.length < 2
			? 'Securities'
			: owner
				? `${owner}’s securities`
				: 'Household securities'
	);

	// What the gain since bought is of, when some costs are not known: it would read as the
	// whole otherwise. It adds up the positions whose cost is known, as the gain does, so a
	// holding whose cost is known in one account only counts with that account's part.
	const costed = $derived.by(() => {
		if (!held) return null;
		const known = held.holdings.map((holding) =>
			holding.positions.filter((position) => position.cost_native !== null)
		);
		const share = percentOf(sum(known.flat().map((position) => position.value)), held.total, 0);
		const count = known.filter((positions) => positions.length).length;
		return share === null ? null : { count, share };
	});

	// What the investments returned, what is held, or what they paid: each view loads its own
	// figures. An address without a view, as every link to the page had before, shows the
	// returns.
	const VIEWS = [
		{ value: 'performance', label: 'Performance' },
		{ value: 'holdings', label: 'Holdings' },
		{ value: 'income', label: 'Income' }
	] as const;
	const view = $derived(
		oneOf(page.url, 'view', ['performance', 'holdings', 'income'], 'performance')
	);
	const range = $derived(returns.rangeOf(page.url.searchParams.get('range')));
	const ranges = $derived(data.income?.ranges ?? []);
	const chosen = $derived(
		chosenRange(
			ranges,
			page.url.searchParams.get('from') ?? '',
			page.url.searchParams.get('to') ?? ''
		)
	);

	let open = $state<string | null>(null);
	/** Open `holding` in the list, and bring it into view. */
	async function choose(holding: HeldInstrument) {
		open = `${holding.symbol}|${holding.currency}|${holding.asset}`;
		await tick();
		document.getElementById(`holding-${open}`)?.scrollIntoView({ block: 'start' });
	}
</script>

<svelte:head>
	<title>Investments · scrooge</title>
</svelte:head>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
	<Segmented
		label="Show"
		value={view}
		options={VIEWS}
		onchange={(value) =>
			show(
				page.url,
				value === 'performance'
					? { view: null, from: null, to: null }
					: {
							view: value,
							range: null,
							...(value === 'holdings' ? { from: null, to: null } : {})
						}
			)}
	/>
	{#if view === 'performance' && data.performance?.performance.totals}
		<Segmented
			label="Range"
			value={range}
			options={returns.RANGES}
			onchange={(value) =>
				show(page.url, { range: value === returns.DEFAULT_RANGE ? null : value })}
		/>
	{/if}
	{#if view === 'income' && ranges.length}
		<Segmented
			label="Range"
			value={chosen}
			options={ranges.map((range) => ({ value: range.label, label: range.label }))}
			onchange={(label) => {
				const range = ranges.find((r) => r.label === label)!;
				show(
					page.url,
					label === DEFAULT_RANGE
						? { from: null, to: null }
						: { from: range.from, to: range.to || null }
				);
			}}
		/>
	{/if}
</div>

{#each errors as error (error)}
	<p
		class="mb-4 flex items-start gap-2 rounded-panel border border-caution/40 bg-caution-soft px-4 py-3 text-body text-ink"
		role="status"
	>
		<CircleAlert class="mt-0.5 size-4 shrink-0 text-caution" aria-hidden="true" />
		{error}
	</p>
{/each}

{#if data.performance}
	{#if !data.performance.performance.totals}
		{#if !errors.length}
			<section class="rounded-lead border border-line bg-panel px-6 py-16 text-center">
				<h1 class="text-heading font-medium text-ink">No investment accounts yet</h1>
				<p class="mx-auto mt-2 max-w-prose text-body text-ink-secondary">
					What your investments returned shows here once a statement of a broker or a
					stock plan is committed.
				</p>
			</section>
		{/if}
	{:else}
		<Returns performance={data.performance.performance} {range} {people} />
	{/if}
{:else if data.income}
	{#if !data.income.income?.through}
		{#if !errors.length}
			<section class="rounded-lead border border-line bg-panel px-6 py-16 text-center">
				<h1 class="text-heading font-medium text-ink">Import your first statements</h1>
				<p class="mx-auto mt-2 max-w-prose text-body text-ink-secondary">
					What your investments paid shows here once a statement is committed.
				</p>
			</section>
		{/if}
	{:else}
		<Income
			income={data.income.income}
			logos={data.income.logos}
			range={rangeName(chosen || null, data.income.income.first, data.income.income.last)}
			{owner}
		/>
		{#if data.costs}
			<Costs
				costs={data.costs.sent?.costs ?? null}
				unavailable={data.costs.unavailable}
				{owner}
			/>
		{/if}
	{/if}
{:else if !held || !held.day}
	{#if !errors.length}
		<section class="rounded-lead border border-line bg-panel px-6 py-16 text-center">
			<h1 class="text-heading font-medium text-ink">Import your first statements</h1>
			<p class="mx-auto mt-2 max-w-prose text-body text-ink-secondary">
				What you hold shows here once a statement is committed.
			</p>
		</section>
	{/if}
{:else if !held.holdings.length}
	<section class="rounded-lead border border-line bg-panel px-6 py-16 text-center">
		<h1 class="text-heading font-medium text-ink">No securities yet</h1>
		<p class="mx-auto mt-2 max-w-prose text-body text-ink-secondary">
			Shares, ETFs, funds and bills show here once a statement with them is committed.
		</p>
	</section>
{:else}
	<!-- The lead: what securities are worth, what they gained since bought, and their map. -->
	<section
		class="rounded-lead border border-line bg-panel px-5 pt-5 pb-4"
		aria-labelledby="securities"
	>
		<div
			class="grid grid-cols-1 gap-x-10 [grid-template-areas:'title'_'figure'_'when'_'span'_'moved'] sm:grid-cols-[minmax(0,1fr)_auto] sm:items-baseline sm:[grid-template-areas:'title_span'_'figure_moved'_'when_note']"
		>
			<h1 id="securities" class="text-small text-ink-secondary [grid-area:title]">{title}</h1>
			<p class="mt-1 text-hero font-medium tracking-tight text-ink [grid-area:figure]">
				<Amount value={held.total} currency={held.currency} />
			</p>
			<p class="mt-1 text-small text-ink-muted [grid-area:when]">
				{held.holdings.length} holdings in {held.accounts}
				{held.accounts === 1 ? 'account' : 'accounts'}, as of {day(held.day)}
			</p>
			<p class="mt-4 text-small text-ink-secondary [grid-area:span] sm:mt-0 sm:text-right">
				Since bought
			</p>
			<Performance
				amount={held.cost === '0' && held.unknown_cost ? null : held.gain}
				percent={held.return_percent}
				class="mt-1 [grid-area:moved] sm:justify-end"
			/>
			{#if held.unknown_cost && costed}
				<p class="mt-1 text-small text-ink-muted [grid-area:note] sm:text-right">
					On {costed.count === 1 ? '1 holding' : `${costed.count} holdings`}, {percent(
						costed.share,
						0
					)} of securities: the cost of the others is not known
				</p>
			{/if}
		</div>
		<div class="mt-5">
			<HoldingsMap holdings={held.holdings} currency={held.currency} onchoose={choose} />
		</div>
	</section>

	<HoldingsList
		holdings={held.holdings}
		logos={data.held?.logos ?? {}}
		currency={held.currency}
		lastDay={held.day}
		{people}
		bind:open
	/>

	<div class="mt-10 grid grid-cols-1 gap-4 lg:grid-cols-2">
		<Spread spread={held.spread} largest={held.holdings[0]} currency={held.currency} {people} />
		{#if held.ladder.length && held.ladder_day}
			<Bills ladder={held.ladder} from={held.ladder_day} {people} />
		{/if}
	</div>

	{#if held.pension_cash !== null || held.notes.length}
		<div class="mt-8 grid gap-1 text-small text-ink-muted">
			{#if held.pension_cash !== null}
				<p>
					Pillar 3a here is the fund units only: the pension account’s cash,
					{money(held.pension_cash, 0)}
					{held.currency}, counts in Pillar 3a on the overview.
				</p>
			{/if}
			{#each held.notes as note (note)}<p>{note}</p>{/each}
		</div>
	{/if}
{/if}
