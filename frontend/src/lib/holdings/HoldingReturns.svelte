<!--
@component
What a holding returned, as its row opens to it: over the past month, this year, the past year
and since bought, each in percent (time-weighted, dividends included) and as an amount; then
what its sales realised, what is not realised yet and the dividends it paid. Every account
together, and each account on its own when it is held in several. A range it was not held
through shows a dash.
-->
<script lang="ts">
	import { signed, signedPercent, money } from '#lib/format/number.js';
	import { direction } from '#lib/investments/performance.js';
	import type { Amount, HeldInstrument } from '#lib/types.js';
	import { cn } from '#lib/ui/cn.js';
	import { columnsOf, periodLines, withheldNote } from './returns.js';

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
	const tone = (value: Amount | null, places = 0) => {
		const way = direction(value, places);
		return way === 'up' ? 'text-gain' : way === 'down' ? 'text-loss' : 'text-ink-secondary';
	};
	const totals = $derived([
		{ label: 'Realised', note: 'from sales', pick: 'realised' as const },
		{ label: 'Unrealised', note: 'still held', pick: 'unrealised' as const },
		{ label: holding.asset === 'Bills' ? 'Interest' : 'Dividends', note: '', pick: 'dividends' as const }
	]);
</script>

{#if columns.some((column) => column.returns)}
	<p id={titleId} class="mt-4 pb-2 text-caption text-ink-muted">
		What it returned, in {currency}, dividends included
	</p>
	<!-- On a phone the accounts scroll sideways under the ranges, which stay. -->
	<div class="overflow-x-auto">
		<table class="w-full text-small" aria-labelledby={titleId}>
			<thead class="text-caption text-ink-muted">
				<tr>
					<th class="sticky left-0 bg-panel pb-2 text-left font-normal"><span class="sr-only">Over</span></th>
					{#each columns as column (column.label)}
						<th class="pb-2 pl-3 text-right font-normal whitespace-nowrap">{column.label}</th>
					{/each}
				</tr>
			</thead>
			<tbody>
				{#each lines as line (line.label)}
					<tr class="border-t border-line">
						<th scope="row" class="sticky left-0 bg-panel py-2 pr-3 text-left font-normal whitespace-nowrap text-ink-secondary">
							{line.label}
							{#if line.note}<span class="block text-caption text-ink-muted">{line.note}</span>{/if}
						</th>
						{#each line.cells as cell, n (n)}
							<td class="py-2 pl-3 text-right whitespace-nowrap tabular-nums">
								{#if cell.percent === null || cell.amount === null}
									<span class="text-ink-muted" title="not held through all of it">–</span>
								{:else}
									<span class={cn('block font-medium', tone(cell.percent, 1))}>{signedPercent(cell.percent)}</span>
									<span class="block text-caption text-ink-muted">{signed(cell.amount)}</span>
								{/if}
							</td>
						{/each}
					</tr>
				{/each}
				{#each totals as total (total.pick)}
					<tr class="border-t border-line">
						<th scope="row" class="sticky left-0 bg-panel py-2 pr-3 text-left font-normal whitespace-nowrap text-ink-secondary">
							{total.label}
							{#if total.note}<span class="block text-caption text-ink-muted">{total.note}</span>{/if}
						</th>
						{#each columns as column (column.label)}
							{@const value = column.returns?.[total.pick] ?? null}
							{@const withheld = total.pick === 'dividends' ? withheldNote(column.returns, money) : ''}
							<td class="py-2 pl-3 text-right whitespace-nowrap tabular-nums">
								{#if value === null}
									<span class="text-ink-muted" title="not known">–</span>
								{:else}
									<span class={cn('block', total.pick === 'dividends' ? 'text-ink' : tone(value, 2))}>
										{total.pick === 'dividends' ? money(value) : signed(value)}
									</span>
									{#if withheld}<span class="block text-caption text-ink-muted">{withheld}</span>{/if}
								{/if}
							</td>
						{/each}
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
