<!--
@component
The periods of the range as a table, oldest first: what came in, the salary within it, what
went out, what was kept, and what was kept as a part of income and of salary; the whole range
in the last row. On a narrow screen what went out is left out, as it is what came in less what
was kept: each rate keeps the amount it is a part of. Should the rest still not fit, the table
scrolls sideways, the page does not.
-->
<script lang="ts">
	import { t } from '#lib/i18n/index.svelte.js';
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
	// Every column after the first, with less room between them on a narrow screen.
	const CELL = 'pl-3 text-right sm:pl-4';
	// What went out, which a narrow screen leaves out.
	const amount = (field: string, cell: string) =>
		cn(cell, CELL, field === 'spent' && 'hidden sm:table-cell');
</script>

<div class="-mx-4 overflow-x-auto px-4">
	<table class="w-full text-small">
		<caption class="sr-only">{t('cashflow.tableCaption', { currency })}</caption>
		<thead class="text-caption text-ink-muted">
			<tr>
				<th class="pb-2 text-left font-normal">{t('cashflow.colPeriod')}</th>
				<th class={amount('income', 'pb-2 font-normal')}>{t('cashflow.cameIn')}</th>
				<th class={amount('salary', 'pb-2 font-normal')} title={t('cashflow.salaryTitle')}
					>{t('cashflow.salary')}</th
				>
				<th class={amount('spent', 'pb-2 font-normal')}>{t('cashflow.wentOut')}</th>
				<th class={amount('saved', 'pb-2 font-normal')}>{t('cashflow.saved')}</th>
				<th class={cn('pb-2 font-normal', CELL)}>{t('cashflow.colPctIncome')}</th>
				<th class={cn('pb-2 font-normal', CELL)} title={t('cashflow.colPctSalaryTitle')}
					>{t('cashflow.colPctSalary')}</th
				>
			</tr>
		</thead>
		<tbody>
			{#each rows as row (row.key)}
				<tr class="border-t border-line">
					<th scope="row" class="py-2 text-left font-normal whitespace-nowrap text-ink-secondary"
						>{row.period}</th
					>
					{#each AMOUNTS as field (field)}
						<td class={amount(field, 'py-2')}>
							<Amount
								value={row[field]}
								places={0}
								class={cn(field === 'saved' && row.saved.startsWith('-') && 'text-loss')}
							/>
						</td>
					{/each}
					<td class={cn('py-2 tabular-nums text-ink', CELL)}>{row.rate}</td>
					<td class={cn('py-2 tabular-nums text-ink', CELL)}>{row.rateSalary}</td>
				</tr>
			{/each}
		</tbody>
		<tfoot class="font-medium text-ink">
			<tr class="border-t border-line-strong">
				<th scope="row" class="py-2 text-left font-medium whitespace-nowrap">{total.period}</th>
				{#each AMOUNTS as field (field)}
					<td class={amount(field, 'py-2')}>
						<Amount
							value={total[field]}
							places={0}
							class={cn(field === 'saved' && total.saved.startsWith('-') && 'text-loss')}
						/>
					</td>
				{/each}
				<td class={cn('py-2 tabular-nums text-brand', CELL)}>{total.rate}</td>
				<td class={cn('py-2 tabular-nums text-brand', CELL)}>{total.rateSalary}</td>
			</tr>
		</tfoot>
	</table>
</div>
<p class="mt-2 text-caption text-ink-muted">{t('cashflow.tableNote')}</p>
