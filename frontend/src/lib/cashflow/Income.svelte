<!--
@component
Where the money came from: work, investments and the rest, each with its sources, the largest
first: what each brought over the range, per month, and against its usual month and the same
months a year before. A source opens its transactions over the same range.
-->
<script lang="ts">
	import { incomeGroupName } from '#lib/design/data.js';
	import { approximate, percent, percentOf, signedPercent, sum } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { CashFlowReport } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import { cn } from '#lib/ui/cn.js';
	import { against, barWidth, changeTone } from './bars.js';
	import { GROUPS, incomeColor } from './names.js';

	interface Props {
		report: CashFlowReport;
		transactions: (changes: Record<string, string>) => string;
	}

	let { report, transactions }: Props = $props();

	const groups = $derived(
		GROUPS.map((group) => {
			const sources = report.sources.filter((source) => source.group === group);
			return { group, sources, total: sum(sources.map((source) => source.total)) };
		}).filter((group) => group.sources.length)
	);
	const all = $derived(sum(groups.map((group) => group.total)));
	const largest = $derived(Math.max(1, ...report.sources.map((s) => approximate(s.total))));
</script>

<Panel title={t('cashflow.incomeTitle')} note={t('cashflow.spendingNote')}>
	{#if !groups.length}
		<p class="text-body text-ink-secondary">{t('cashflow.nothingCameIn')}</p>
	{/if}
	<div class="space-y-4">
		{#each groups as { group, sources, total } (group)}
			{@const share = percentOf(total, all, 0)}
			<div>
				<h3 class="mb-1 flex items-baseline gap-2 px-1 text-small text-ink-secondary">
					<span
						class="size-2 shrink-0 self-center rounded-[2px]"
						style:background={incomeColor(group)}
					></span>
					<span class="flex-1 font-medium text-ink">{incomeGroupName(group)}</span>
					{#if share !== null}<span class="text-caption text-ink-muted">{percent(share, 0)}</span
						>{/if}
					<Amount
						value={total}
						places={0}
						class={total.startsWith('-') ? 'text-loss' : 'text-ink-secondary'}
					/>
				</h3>
				<ul class="divide-y divide-line">
					{#each sources as source (source.name)}
						{@const usual = against(source.monthly, source.usual)}
						{@const before = against(source.total, source.year_before)}
						{@const width = barWidth(source.total, largest)}
						<li>
							<a
								href={transactions({ category: source.name })}
								class="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 rounded-control px-1 py-2 hover:bg-sunken"
							>
								<span class="min-w-0 truncate text-body text-ink">{source.name}</span>
								<Amount
									value={source.total}
									places={0}
									class={cn(
										'text-right text-body',
										source.total.startsWith('-') ? 'text-loss' : 'text-ink'
									)}
								/>
								<span class="h-1.5 self-center rounded-full bg-sunken" aria-hidden="true">
									{#if width > 0}
										<span
											class="block h-full rounded-full"
											style:width="{width}%"
											style:background={incomeColor(group)}
										></span>
									{/if}
								</span>
								<span class="flex justify-end gap-3 text-caption whitespace-nowrap text-ink-muted">
									<span
										><Amount
											value={source.monthly}
											places={0}
											class={source.monthly.startsWith('-') ? 'text-loss' : 'text-ink-secondary'}
										/>
										{t('cashflow.aMonthSuffix')}</span
									>
									{#if usual !== null}<span class={changeTone(usual, 'up')}
											>{t('cashflow.usualSuffix', { pct: signedPercent(usual, 0) })}</span
										>{/if}
									{#if before !== null}<span
											class={cn('hidden sm:inline', changeTone(before, 'up'))}
											>{t('cashflow.yearBeforeSuffix', { pct: signedPercent(before, 0) })}</span
										>{/if}
								</span>
							</a>
						</li>
					{/each}
				</ul>
			</div>
		{/each}
	</div>
</Panel>
