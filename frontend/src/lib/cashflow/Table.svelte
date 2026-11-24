<!--
@component
The periods of the range as a table, oldest first: what came in, the salary within it, what
went out, what was kept, and what was kept as a part of income and of salary; the whole range
in the last row. On a narrow screen what came in and what went out are left out, as the lead
above says them for the range; the salary, what was kept and both rates stay.
-->
<script lang="ts">
	import type { CashFlowPeriod, Saved } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import { cn } from '#lib/ui/cn.js';
	import { periodRows, totalRow } from './table.js';

	interface Props {
		periods: CashFlowPeriod[];
		totals: Saved;
		currency: string;
	}

	let { periods, totals, currency }: Props = $props();

	const rows = $derived(periodRows(periods));
	const total = $derived(totalRow(totals));
	const AMOUNTS = ['income', 'salary', 'spent', 'saved'] as const;
	// The amounts a narrow screen leaves out.
	const WIDE = new Set<string>(['income', 'spent']);
	const amount = (field: string, cell: string) => cn(cell, WIDE.has(field) && 'hidden sm:table-cell');
</script>

<table class="w-full text-small">
	<caption class="sr-only">Income, spending and what was saved in each period, in {currency}</caption>
	<thead class="text-caption text-ink-muted">
		<tr>
			<th class="pb-2 text-left font-normal">Period</th>
			<th class={amount('income', 'pb-2 pl-4 text-right font-normal')}>Came in</th>
			<th class="pb-2 pl-4 text-right font-normal" title="Income from work">Salary</th>
			<th class={amount('spent', 'pb-2 pl-4 text-right font-normal')}>Went out</th>
			<th class="pb-2 pl-4 text-right font-normal">Saved</th>
			<th class="pb-2 pl-4 text-right font-normal">% of income</th>
			<th class="pb-2 pl-4 text-right font-normal" title="Of the income from work">% of salary</th>
		</tr>
	</thead>
	<tbody>
		{#each rows as row (row.key)}
			<tr class="border-t border-line">
				<th scope="row" class="py-2 text-left font-normal whitespace-nowrap text-ink-secondary">{row.period}</th>
				{#each AMOUNTS as field (field)}
					<td class={amount(field, 'py-2 pl-4 text-right')}>
						<Amount value={row[field]} places={0} class={cn(field === 'saved' && row.saved.startsWith('-') && 'text-loss')} />
					</td>
				{/each}
				<td class="py-2 pl-4 text-right tabular-nums text-ink">{row.rate}</td>
				<td class="py-2 pl-4 text-right tabular-nums text-ink">{row.rateSalary}</td>
			</tr>
		{/each}
	</tbody>
	<tfoot class="font-medium text-ink">
		<tr class="border-t border-line-strong">
			<th scope="row" class="py-2 text-left font-medium whitespace-nowrap">{total.period}</th>
			{#each AMOUNTS as field (field)}
				<td class={amount(field, 'py-2 pl-4 text-right')}>
					<Amount value={total[field]} places={0} class={cn(field === 'saved' && total.saved.startsWith('-') && 'text-loss')} />
				</td>
			{/each}
			<td class="py-2 pl-4 text-right tabular-nums text-brand">{total.rate}</td>
			<td class="py-2 pl-4 text-right tabular-nums text-brand">{total.rateSalary}</td>
		</tr>
	</tfoot>
</table>
