<script lang="ts">
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { tick } from 'svelte';
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { assetColor, assetName } from '#lib/design/data.js';
	import { day } from '#lib/format/date.js';
	import { percent, percentOf, sum } from '#lib/format/number.js';
	import Bills from '#lib/holdings/Bills.svelte';
	import HoldingDetails from '#lib/holdings/HoldingDetails.svelte';
	import HoldingsList from '#lib/holdings/HoldingsList.svelte';
	import HoldingsMap from '#lib/holdings/HoldingsMap.svelte';
	import Spread from '#lib/holdings/Spread.svelte';
	import { holdingDue, holdingName } from '#lib/holdings/names.js';
	import { findHolding, holdingKey, holdingParam } from '#lib/holdings/returns.js';
	import { rangeName, rangePillLabel } from '#lib/cashflow/names.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import Costs from '#lib/investments/Costs.svelte';
	import Income from '#lib/investments/Income.svelte';
	import { DEFAULT_RANGE, firstStatementMonth } from '#lib/investments/income.js';
	import { choose as choosePreset, chosen as presetOf } from '#lib/period.js';
	import * as returns from '#lib/investments/performance.js';
	import Pensions from '#lib/investments/Pensions.svelte';
	import { DEFAULT_PERIOD, periodOf } from '#lib/investments/pensions.js';
	import Returns from '#lib/investments/Returns.svelte';
	import type { HeldInstrument } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import EmptyState from '#lib/ui/EmptyState.svelte';
	import Performance from '#lib/ui/Performance.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import SidePanel from '#lib/ui/SidePanel.svelte';
	import Swatch from '#lib/ui/Swatch.svelte';
	import { cn } from '#lib/ui/cn.js';
	import { detailOf, here, setDetail, show } from '#lib/view.js';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const held = $derived(data.held?.holdings);
	const errors = $derived([
		...new Set(
			data.held
				? [...data.held.errors, ...(data.pensions?.errors ?? [])]
				: [
						...(data.income?.errors ?? data.performance?.errors ?? []),
						...(data.costs?.sent?.errors ?? [])
					]
		)
	]);
	const pensions = $derived(data.pensions?.pensions);
	// The period the pensions' change is over: only the address changes, the figures of both
	// are there.
	const period = $derived(periodOf(here().searchParams.get('pension')));
	const owner = $derived(page.url.searchParams.get('owner'));
	const people = $derived(data.session.people.length > 1 && !owner);
	const title = $derived(
		data.session.people.length < 2
			? t('investments.securitiesTitle')
			: owner
				? t('investments.ownerSecuritiesTitle', { owner })
				: t('investments.householdSecuritiesTitle')
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
	// returns; legacy `view=income` links open Payouts.
	const views = $derived([
		{ value: 'performance' as const, label: t('investments.viewPerformance') },
		{ value: 'holdings' as const, label: t('investments.viewHoldings') },
		{ value: 'payouts' as const, label: t('investments.viewIncome') }
	]);
	const perfRanges = $derived(returns.rangeOptions());
	const rawView = $derived(page.url.searchParams.get('view'));
	const view = $derived(
		rawView === 'holdings'
			? ('holdings' as const)
			: rawView === 'payouts' || rawView === 'income'
				? ('payouts' as const)
				: ('performance' as const)
	);
	const pageTitle = $derived(
		view === 'holdings'
			? t('investments.pageTitleHoldings')
			: view === 'payouts'
				? t('investments.pageTitlePayouts')
				: t('investments.pageTitlePerformance')
	);
	const range = $derived(returns.rangeOf(page.url.searchParams.get('range')));
	const ranges = $derived(data.income?.ranges ?? []);
	const chosen = $derived(presetOf(ranges, page.url, DEFAULT_RANGE));
	const showBills = $derived(Boolean(held && held.ladder.length >= 2 && held.ladder_day));

	const holdingQuery = $derived(detailOf(here(), 'holding'));
	const chosenHolding = $derived(held ? findHolding(held.holdings, holdingQuery) : null);
	const chosenKey = $derived(chosenHolding ? holdingKey(chosenHolding) : null);

	// Remember the last shown holding so focus returns to its row on close and its body stays
	// populated during the closing animation.
	let lastKey = $state<string | null>(null);
	let displayedHolding = $state<HeldInstrument | null>(null);
	$effect(() => {
		if (chosenHolding && chosenKey) {
			lastKey = chosenKey;
			displayedHolding = chosenHolding;
		}
	});
	const panelHolding = $derived(chosenHolding ?? displayedHolding);

	function chooseFromList(holding: HeldInstrument) {
		if (!held) return;
		const key = holdingKey(holding);
		void setDetail('holding', chosenKey === key ? null : holdingParam(holding, held.holdings));
	}

	/** Open `holding` in the side panel, and keep its row in view. */
	async function chooseFromMap(holding: HeldInstrument) {
		if (!held) return;
		const key = holdingKey(holding);
		await setDetail('holding', holdingParam(holding, held.holdings));
		await tick();
		document.getElementById(`holding-${key}`)?.scrollIntoView({ block: 'nearest' });
	}

	function closeHolding() {
		void setDetail('holding', null);
	}

	function returnHoldingFocus(): HTMLElement | null {
		if (!lastKey) return null;
		return (
			document.getElementById(`holding-${lastKey}`)?.querySelector<HTMLElement>('button') ?? null
		);
	}
</script>

<svelte:head>
	<title>{pageTitle}</title>
</svelte:head>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
	<Segmented
		label={t('overview.showAria')}
		value={view}
		options={views}
		onchange={(value) =>
			show(
				here(),
				value === 'performance'
					? { view: null, from: null, to: null, holding: null }
					: {
							view: value,
							...(value === 'holdings'
								? { from: null, to: null }
								: { pension: null, holding: null })
						}
			)}
	/>
	{#if view === 'performance' && data.performance?.performance.totals}
		<Segmented
			label={t('overview.rangeAria')}
			value={range}
			options={perfRanges}
			onchange={(value) => show(page.url, { range: value })}
		/>
	{/if}
	{#if view === 'payouts' && ranges.length}
		<Segmented
			label={t('overview.rangeAria')}
			value={chosen}
			options={ranges.map((range) => ({ value: range.key, label: rangePillLabel(range) }))}
			onchange={(key) => show(page.url, choosePreset(ranges, key, DEFAULT_RANGE))}
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
			<EmptyState
				heading={t('investments.noAccountsHeading')}
				body={t('investments.noAccountsBody')}
			/>
		{/if}
	{:else}
		<Returns
			performance={data.performance.performance}
			{range}
			{people}
			token={data.session.prices ? data.session.token : null}
			onfetched={() => invalidateAll()}
		/>
	{/if}
{:else if data.income}
	{#if !data.income.income?.through}
		{#if !errors.length}
			<EmptyState heading={t('overview.emptyHeading')} body={t('investments.incomeEmptyBody')} />
		{/if}
	{:else}
		<Income
			income={data.income.income}
			logos={data.income.logos}
			range={rangeName(
				chosen || null,
				data.income.income.first,
				data.income.income.last,
				data.income.income.through
			)}
			{owner}
			firstMonth={firstStatementMonth(data.session.people, owner)}
		/>
		{#if data.costs}
			<Costs costs={data.costs.sent?.costs ?? null} unavailable={data.costs.unavailable} {owner} />
		{/if}
	{/if}
{:else if !held || !held.day}
	{#if !errors.length}
		<EmptyState heading={t('overview.emptyHeading')} body={t('investments.holdingsEmptyBody')} />
	{/if}
{:else if !held.holdings.length}
	<EmptyState
		heading={t('investments.noSecuritiesHeading')}
		body={t('investments.noSecuritiesBody')}
	/>
	{@render pensionGroup()}
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
				{t(
					held.accounts === 1
						? 'investments.holdingsSummaryOneAccount'
						: 'investments.holdingsSummaryManyAccounts',
					{
						holdings: held.holdings.length,
						accounts: held.accounts,
						date: day(held.day)
					}
				)}
			</p>
			<p class="mt-4 text-small text-ink-secondary [grid-area:span] sm:mt-0 sm:text-right">
				{t('investments.sinceBought')}
			</p>
			<Performance
				amount={held.cost === '0' && held.unknown_cost ? null : held.gain}
				percent={held.return_percent}
				class="mt-1 [grid-area:moved] sm:justify-end"
			/>
			{#if held.unknown_cost && costed}
				<p class="mt-1 text-small text-ink-muted [grid-area:note] sm:text-right">
					{t(costed.count === 1 ? 'investments.unknownCostOne' : 'investments.unknownCostMany', {
						count: costed.count,
						share: percent(costed.share, 0)
					})}
				</p>
			{/if}
		</div>
		<div class="mt-5">
			<HoldingsMap holdings={held.holdings} currency={held.currency} onchoose={chooseFromMap} />
		</div>
	</section>

	<HoldingsList
		holdings={held.holdings}
		logos={data.held?.logos ?? {}}
		currency={held.currency}
		selected={chosenKey}
		onchoose={chooseFromList}
	/>

	<SidePanel
		open={Boolean(chosenHolding)}
		title={panelHolding ? holdingName(panelHolding) : ''}
		subject={chosenKey}
		onclose={closeHolding}
		returnFocus={returnHoldingFocus}
	>
		{#snippet leading()}
			{#if panelHolding && data.held?.logos?.[panelHolding.symbol]}
				<img
					src={data.held.logos[panelHolding.symbol]}
					alt=""
					class="size-8 shrink-0 rounded-control border border-line bg-panel object-contain p-0.5 dark:border-0 dark:bg-ink"
				/>
			{/if}
		{/snippet}
		{#snippet meta()}
			{#if panelHolding}
				{@const display = holdingName(panelHolding)}
				{@const due = holdingDue(panelHolding)}
				<span class="flex flex-wrap items-center gap-1.5">
					<Swatch color={assetColor(panelHolding.asset)} class="size-2 rounded-full" />
					<span class="text-ink-secondary">{assetName(panelHolding.asset)}</span>
					{#if due}<span class="text-ink-secondary">{due}</span>{/if}
					{#if display !== panelHolding.symbol}<span>{panelHolding.symbol}</span>{/if}
					{#if panelHolding.positions.length > 1}
						<span>{t('holdings.inAccounts', { count: panelHolding.positions.length })}</span>
					{/if}
				</span>
			{/if}
		{/snippet}
		{#if panelHolding}
			<HoldingDetails
				holding={panelHolding}
				hasLogo={Boolean(data.held?.logos?.[panelHolding.symbol])}
				currency={held.currency}
				lastDay={held.day}
				{people}
				{owner}
				token={data.session.read_only ? null : data.session.token}
				onlogo={() => invalidateAll()}
			/>
		{/if}
	</SidePanel>
	{@render pensionGroup()}

	<div class={cn('mt-10 grid grid-cols-1 gap-4', showBills && 'lg:grid-cols-2')}>
		<Spread spread={held.spread} largest={held.holdings[0]} currency={held.currency} {people} />
		{#if showBills && held.ladder_day}
			<Bills ladder={held.ladder} from={held.ladder_day} {people} />
		{/if}
	</div>

	{#if held.notes.length}
		<div class="mt-8 grid gap-1 text-small text-ink-muted">
			{#each held.notes as note (note)}<p>{note}</p>{/each}
		</div>
	{/if}
{/if}

{#snippet pensionGroup()}
	{#if pensions?.day && pensions.pensions.length}
		<Pensions
			report={pensions}
			{period}
			{people}
			onperiod={(value) =>
				show(here(), { pension: value === DEFAULT_PERIOD ? null : value }, { shallow: true })}
		/>
	{/if}
{/snippet}
