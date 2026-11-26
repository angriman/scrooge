<!--
@component
Where the money went, by category, the largest first: what each took over the range, per
month, and against its usual month and the same months a year before. Below them, the places
most was spent at. Each opens its transactions over the same range.
-->
<script lang="ts">
	import { approximate, signedPercent } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { CashFlowReport } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import { cn } from '#lib/ui/cn.js';
	import { against, barWidth, changeTone } from './bars.js';
	import { spendingPartName } from './names.js';

	interface Props {
		report: CashFlowReport;
		colors: Map<string, string>;
		/** The address of the transactions with `changes` to the range shown. */
		transactions: (changes: Record<string, string>) => string;
	}

	let { report, colors, transactions }: Props = $props();

	const largest = $derived(Math.max(1, ...report.categories.map((c) => approximate(c.spent))));
	let merchants = $state(false);
</script>

<Panel title={t('cashflow.spendingTitle')} note={t('cashflow.spendingNote')}>
	<ul class="divide-y divide-line">
		{#each report.categories as category (category.name)}
			{@const usual = against(category.monthly, category.usual)}
			{@const before = against(category.spent, category.year_before)}
			{@const width = barWidth(category.spent, largest)}
			<li>
				<a
					href={transactions({ category: category.name })}
					class="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 rounded-control px-1 py-2 hover:bg-sunken"
				>
					<span class="flex min-w-0 items-center gap-2 text-body text-ink">
						<span
							class="size-2 shrink-0 rounded-[2px]"
							style:background={colors.get(category.name) ?? 'var(--data-other)'}
						></span>
						<span class="truncate">{spendingPartName(category.name)}</span>
					</span>
					<Amount value={category.spent} places={0} class="text-right text-body text-ink" />
					<span class="h-1.5 self-center rounded-full bg-sunken" aria-hidden="true">
						{#if width > 0}
							<span
								class="block h-full rounded-full"
								style:width="{width}%"
								style:background={colors.get(category.name) ?? 'var(--data-other)'}
							></span>
						{/if}
					</span>
					<span class="flex justify-end gap-3 text-caption whitespace-nowrap text-ink-muted">
						<span
							><Amount value={category.monthly} places={0} class="text-ink-secondary" />
							{t('cashflow.aMonthSuffix')}</span
						>
						{#if usual !== null}<span class={changeTone(usual, 'down')}
								>{t('cashflow.usualSuffix', { pct: signedPercent(usual, 0) })}</span
							>{/if}
						{#if before !== null}<span class={cn('hidden sm:inline', changeTone(before, 'down'))}
								>{t('cashflow.yearBeforeSuffix', { pct: signedPercent(before, 0) })}</span
							>{/if}
					</span>
				</a>
			</li>
		{/each}
	</ul>

	{#if report.merchants.length}
		<button
			type="button"
			onclick={() => (merchants = !merchants)}
			aria-expanded={merchants}
			class="mt-1 inline-flex min-h-11 items-center text-small text-brand hover:text-brand-hover sm:mt-3 sm:min-h-0"
		>
			{t(merchants ? 'cashflow.hidePlaces' : 'cashflow.showPlaces', {
				count: report.merchants.length
			})}
		</button>
		{#if merchants}
			<ul class="mt-2 divide-y divide-line">
				{#each report.merchants as merchant (merchant.name)}
					<li>
						<a
							href={transactions({ merchant: merchant.name })}
							class="flex min-h-11 items-center gap-3 rounded-control px-1 py-2 hover:bg-sunken sm:min-h-0 sm:items-baseline sm:py-1.5"
						>
							<span class="min-w-0 flex-1 truncate text-body text-ink">{merchant.name}</span>
							<span class="hidden text-caption text-ink-muted sm:inline"
								>{merchant.category}, {merchant.count}×</span
							>
							<Amount value={merchant.spent} places={0} class="text-body text-ink" />
						</a>
					</li>
				{/each}
			</ul>
			{#if report.other_merchants.count}
				<p class="mt-2 text-caption text-ink-muted">
					{t('cashflow.morePlacesBefore', { count: report.other_merchants.count })}
					<Amount value={report.other_merchants.spent} places={0} />
					{t('cashflow.morePlacesAfter')}
				</p>
			{/if}
		{/if}
	{/if}
</Panel>
