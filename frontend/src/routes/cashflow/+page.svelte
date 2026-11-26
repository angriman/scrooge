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
	import WhoPaid from '#lib/cashflow/WhoPaid.svelte';
	import { partColors, rangeName, rangePillLabel } from '#lib/cashflow/names.js';
	import { anyRecurring } from '#lib/cashflow/recurring.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import EmptyState from '#lib/ui/EmptyState.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import { choose, chosen as presetOf } from '#lib/period.js';
	import { here, oneOf, show } from '#lib/view.js';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const flow = $derived(data.flow);
	const report = $derived(flow.report);
	const colors = $derived(partColors(report?.parts ?? []));

	// The range asked for: one offered, by its ends or by the period another page chose; none
	// asked is the year to date.
	const chosen = $derived(presetOf(flow.ranges, page.url));
	const byOptions = $derived([
		{ value: 'month' as const, label: t('cashflow.byMonth') },
		{ value: 'quarter' as const, label: t('cashflow.byQuarter') },
		{ value: 'year' as const, label: t('cashflow.byYear') }
	]);
	const by = $derived(oneOf(page.url, 'by', ['month', 'quarter', 'year'], 'month'));
	const views = $derived([
		{ value: 'chart' as const, label: t('overview.chart') },
		{ value: 'table' as const, label: t('overview.table') }
	]);
	const view = $derived(oneOf(here(), 'view', ['chart', 'table'], 'chart'));

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
	<title>{t('cashflow.pageTitle')}</title>
</svelte:head>

<div class="mb-5 flex flex-wrap items-center justify-between gap-3">
	<h1 class="text-heading font-medium text-ink">{t('cashflow.title')}</h1>
	{#if flow.ranges.length}
		<Segmented
			label={t('overview.rangeAria')}
			value={chosen}
			options={flow.ranges.map((range) => ({ value: range.key, label: rangePillLabel(range) }))}
			onchange={(key) => show(here(), choose(flow.ranges, key))}
		/>
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
	<EmptyState
		level="h2"
		heading={t('cashflow.emptyHeading')}
		body={t('cashflow.emptyBody')}
		importCta={!data.session.read_only}
	/>
{:else}
	<Lead
		totals={report.totals}
		yearBefore={report.year_before}
		currency={report.currency}
		range={rangeName(chosen || null, flow.first, flow.last, report.through)}
		paidBack={report.paid_back}
	/>

	<Panel class="mt-6" title={t('cashflow.viewFlow')}>
		<Flow {report} {colors} />
	</Panel>

	<Panel class="mt-6" title={t('cashflow.viewTime')}>
		{#snippet actions()}
			<Segmented
				label={t('overview.showAria')}
				value={view}
				options={views}
				onchange={(value) =>
					show(here(), { view: value === 'chart' ? null : value }, { shallow: true })}
			/>
			<Segmented
				label={t('cashflow.eachColumnAria')}
				value={by}
				options={byOptions}
				onchange={(value) => show(here(), { by: value === 'month' ? null : value })}
			/>
		{/snippet}
		{#if view === 'table'}
			<Table periods={report.periods} totals={report.totals} currency={report.currency} />
		{:else}
			<OverTime periods={report.periods} {colors} currency={report.currency} />
		{/if}
	</Panel>

	<div class="mt-6 grid items-start gap-6 lg:grid-cols-2">
		<Income {report} {transactions} />
		<Spending {report} {colors} {transactions} />
	</div>

	{#if anyRecurring(data.recurring)}
		<div class="mt-6">
			<Recurring recurring={data.recurring} transactions={allTransactions} />
		</div>
	{/if}

	{#if report.paid_by}
		<div class="mt-6">
			<WhoPaid paidBy={report.paid_by} currency={report.currency} people={data.session.people} />
		</div>
	{/if}

	<div class="mt-6">
		<AsPaid {report} />
	</div>
{/if}
