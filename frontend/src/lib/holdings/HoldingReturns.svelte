<!--
@component
What a holding returned, as its row opens to it: over the past month, this year, the past year
and since bought, each in percent (time-weighted, dividends included) and as an amount; then
what its sales realised, what is not realised yet and the dividends it paid. Every account
together, and each account on its own when it is held in several. A range it was not held
through shows a dash, and one never worth 100 francs at the start of a day has none at all:
a note under the table tells the two apart.
-->
<script lang="ts">
	import { signed, signedPercent, money } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import { direction } from '#lib/investments/performance.js';
	import type { Amount, HeldInstrument } from '#lib/types.js';
	import { cn } from '#lib/ui/cn.js';
	import { columnsOf, dashNotes, periodLines, realisedText, withheldNote } from './returns.js';

	interface Props {
		holding: HeldInstrument;
		currency: string;
		/** Whether several people's positions are listed: each says whose it is. */
		people: boolean;
	}

	let { holding, currency, people }: Props = $props();
	const titleId = $props.id();

	const columns = $derived(columnsOf(holding, people));
	const lines = $derived(periodLines(columns));
	const notes = $derived(dashNotes(columns, lines));
	const tone = (value: Amount | null, places = 0) => {
		const way = direction(value, places);
		return way === 'up' ? 'text-gain' : way === 'down' ? 'text-loss' : 'text-ink-secondary';
	};
	const totals = $derived([
		{
			label: t('holdings.realisedLabel'),
			note: t('holdings.realisedNote'),
			pick: 'realised' as const
		},
		{
			label: t('holdings.unrealisedLabel'),
			note: t('holdings.unrealisedNote'),
			pick: 'unrealised' as const
		},
		{
			label: t(holding.asset === 'Bills' ? 'investments.interest' : 'investments.dividends'),
			note: '',
			pick: 'dividends' as const
		}
	]);
</script>

{#if columns.some((column) => column.returns)}
	<p id={titleId} class="mt-4 pb-2 text-caption text-ink-muted">
		{t('holdings.returnedTitle', { currency })}
	</p>
	<!-- On a phone the accounts scroll sideways under the ranges, which stay. -->
	<div class="overflow-x-auto">
		<table class="w-full text-small" aria-labelledby={titleId}>
			<thead class="text-caption text-ink-muted">
				<tr>
					<th class="sticky left-0 bg-panel pb-2 text-left font-normal"
						><span class="sr-only">{t('holdings.overSr')}</span></th
					>
					{#each columns as column (column.label)}
						<th class="pb-2 pl-3 text-right font-normal whitespace-nowrap">{column.label}</th>
					{/each}
				</tr>
			</thead>
			<tbody>
				{#each lines as line (line.label)}
					<tr class="border-t border-line">
						<th
							scope="row"
							class="sticky left-0 bg-panel py-2 pr-3 text-left font-normal whitespace-nowrap text-ink-secondary"
						>
							{line.label}
							{#if line.note}<span class="block text-caption text-ink-muted">{line.note}</span>{/if}
						</th>
						{#each line.cells as cell, n (n)}
							<td class="py-2 pl-3 text-right whitespace-nowrap tabular-nums">
								{#if cell.percent === null || cell.amount === null}
									<span class="text-ink-muted" title={t('holdings.notHeldThroughTitle')}>–</span>
								{:else}
									<span class={cn('block font-medium', tone(cell.percent, 1))}
										>{signedPercent(cell.percent)}</span
									>
									<span class="block text-caption text-ink-muted">{signed(cell.amount)}</span>
								{/if}
							</td>
						{/each}
					</tr>
				{/each}
				{#each totals as total (total.pick)}
					<tr class="border-t border-line">
						<th
							scope="row"
							class="sticky left-0 bg-panel py-2 pr-3 text-left font-normal whitespace-nowrap text-ink-secondary"
						>
							{total.label}
							{#if total.note}<span class="block text-caption text-ink-muted">{total.note}</span
								>{/if}
						</th>
						{#each columns as column (column.label)}
							{@const value = column.returns?.[total.pick] ?? null}
							{@const withheld =
								total.pick === 'dividends' ? withheldNote(column.returns, money) : ''}
							<td class="py-2 pl-3 text-right whitespace-nowrap tabular-nums">
								{#if total.pick === 'realised' && column.returns && !column.returns.sold}
									<span class="text-ink-muted">{realisedText(column.returns, signed)}</span>
								{:else if value === null}
									<span class="text-ink-muted" title={t('common.notKnown')}>–</span>
								{:else}
									<span
										class={cn('block', total.pick === 'dividends' ? 'text-ink' : tone(value, 2))}
									>
										{total.pick === 'dividends' ? money(value) : signed(value)}
									</span>
									{#if withheld}<span class="block text-caption text-ink-muted">{withheld}</span
										>{/if}
								{/if}
							</td>
						{/each}
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
	{#each notes as note (note)}
		<p class="mt-2 text-caption text-ink-muted">{note}</p>
	{/each}
{/if}
