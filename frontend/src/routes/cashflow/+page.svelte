<script lang="ts">
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import AsPaid from '#lib/cashflow/AsPaid.svelte';
	import Flow from '#lib/cashflow/Flow.svelte';
	import Income from '#lib/cashflow/Income.svelte';
	import Lead from '#lib/cashflow/Lead.svelte';
	import OverTime from '#lib/cashflow/OverTime.svelte';
	import Recurring from '#lib/cashflow/Recurring.svelte';
	import Spending from '#lib/cashflow/Spending.svelte';
	import Table from '#lib/cashflow/Table.svelte';
	import WhatMoved from '#lib/cashflow/WhatMoved.svelte';
	import WhoPaid from '#lib/cashflow/WhoPaid.svelte';
	import { whatMoved } from '#lib/cashflow/whatMoved.js';
	import { partColors, rangeName } from '#lib/cashflow/names.js';
	import { anyRecurring } from '#lib/cashflow/recurring.js';
	import Panel from '#lib/ui/Panel.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import { here, oneOf, show } from '#lib/view.js';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const flow = $derived(data.flow);
	const report = $derived(flow.report);
	const colors = $derived(partColors(report?.parts ?? []));

	// The range asked for: one offered, matched by its ends; none asked is the year to date.
	const from = $derived(page.url.searchParams.get('from') ?? '');
	const to = $derived(page.url.searchParams.get('to') ?? '');
	const chosen = $derived(
		!from && !to
			? (flow.ranges[0]?.label ?? '')
			: (flow.ranges.find((range) => range.from === from && range.to === to)?.label ?? '')
	);
	const BY = [
		{ value: 'month', label: 'Month' },
		{ value: 'quarter', label: 'Quarter' },
		{ value: 'year', label: 'Year' }
	] as const;
	const by = $derived(oneOf(page.url, 'by', ['month', 'quarter', 'year'], 'month'));
	const VIEWS = [
		{ value: 'time', label: 'Over time' },
		{ value: 'flow', label: 'Flow' },
		{ value: 'table', label: 'Table' }
	] as const;
	const view = $derived(oneOf(here(), 'view', ['time', 'flow', 'table'], 'time'));

	// The transactions behind a line: the same person, the same range, and the same currency
	// for the months shown above them.
	const transactions = (changes: Record<string, string>) => {
		const query = new URLSearchParams();
		for (const name of ['owner', 'currency']) {
			const value = page.url.searchParams.get(name);
			if (value) query.set(name, value);
		}
		if (flow.first) query.set('from', flow.first);
		if (flow.last) query.set('to', flow.last);
		for (const [name, value] of Object.entries(changes)) query.set(name, value);
		return `${resolve('/transactions')}?${query}`;
	};
	// The transactions of a recurring payment: all of them, whenever they were, in the same
	// currency, and of the same person unless the payment names its own.
	const allTransactions = (changes: Record<string, string>) => {
		const query = new URLSearchParams(changes);
		for (const name of ['owner', 'currency']) {
			const value = page.url.searchParams.get(name);
			if (value && !query.has(name)) query.set(name, value);
		}
		return `${resolve('/transactions')}?${query}`;
	};
</script>

<svelte:head>
	<title>Cash flow · scrooge</title>
</svelte:head>

<div class="mb-5 flex flex-wrap items-center justify-between gap-3">
	<h1 class="text-heading font-medium text-ink">Cash flow</h1>
	{#if flow.ranges.length}
		<div class="flex flex-wrap items-center gap-2">
			<Segmented
				label="Range"
				value={chosen}
				options={flow.ranges.map((range) => ({ value: range.label, label: range.label }))}
				onchange={(label) => {
					const range = flow.ranges.find((r) => r.label === label)!;
					show(
						here(),
						label === flow.ranges[0].label
							? { from: null, to: null }
							: { from: range.from, to: range.to || null }
					);
				}}
			/>
			<Segmented
				label="Each column"
				value={by}
				options={BY}
				onchange={(value) => show(here(), { by: value === 'month' ? null : value })}
			/>
		</div>
	{/if}
</div>

{#each flow.errors as error (error)}
	<p
		class="mb-4 flex items-start gap-2 rounded-panel border border-caution/40 bg-caution-soft px-4 py-3 text-body text-ink"
		role="status"
	>
		<CircleAlert class="mt-0.5 size-4 shrink-0 text-caution" aria-hidden="true" />
		{error}
	</p>
{/each}

{#if !report?.totals}
	<section class="rounded-lead border border-line bg-panel px-6 py-16 text-center">
		<h2 class="text-heading font-medium text-ink">Nothing to show for these months</h2>
		<p class="mx-auto mt-2 max-w-prose text-body text-ink-secondary">
			Income and spending show here once statements are committed.
		</p>
		<a
			href={resolve('/import')}
			class="mt-6 inline-flex rounded-control bg-brand px-4 py-2 text-body font-medium text-on-brand hover:bg-brand-hover"
		>
			Import statements
		</a>
	</section>
{:else}
	<Lead
		totals={report.totals}
		yearBefore={report.year_before}
		currency={report.currency}
		range={rangeName(chosen || null, flow.first, flow.last)}
		paidBack={report.paid_back}
	/>

	{@const moved = whatMoved(report.categories)}
	{#if report.paid_by || moved.length}
		<div
			class={[
				'mt-6 grid items-start gap-6',
				report.paid_by && moved.length && 'lg:grid-cols-2'
			]}
		>
			{#if moved.length}<WhatMoved {moved} {colors} {transactions} />{/if}
			{#if report.paid_by}<WhoPaid
					paidBy={report.paid_by}
					currency={report.currency}
					people={data.session.people}
				/>{/if}
		</div>
	{/if}

	<Panel class="mt-6">
		{#snippet actions()}
			<Segmented
				label="Show"
				value={view}
				options={VIEWS}
				onchange={(value) =>
					show(here(), { view: value === 'time' ? null : value }, { shallow: true })}
			/>
		{/snippet}
		{#if view === 'flow'}
			<Flow {report} {colors} />
		{:else if view === 'table'}
			<Table periods={report.periods} totals={report.totals} currency={report.currency} />
		{:else}
			<OverTime periods={report.periods} {colors} currency={report.currency} />
		{/if}
	</Panel>

	<div class="mt-6 grid items-start gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
		<Spending {report} {colors} {transactions} />
		<Income {report} {transactions} />
	</div>

	{#if anyRecurring(data.recurring)}
		<div class="mt-6">
			<Recurring recurring={data.recurring} transactions={allTransactions} />
		</div>
	{/if}

	<div class="mt-6">
		<AsPaid {report} />
	</div>
{/if}
