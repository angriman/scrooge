<!--
@component
Income and expenses in each currency, as they were paid: nothing converted. Folded, for
checking a figure against the statements.
-->
<script lang="ts">
	import type { CashFlowReport } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';

	interface Props {
		report: CashFlowReport;
	}

	let { report }: Props = $props();

	const currencies = $derived([...new Set(report.as_paid.map((line) => line.currency))]);
</script>

{#if currencies.length}
	<details class="rounded-panel bg-panel px-4 py-3">
		<summary class="cursor-pointer text-body text-ink-secondary hover:text-ink">
			In each currency, as paid
		</summary>
		<p class="mt-1 text-small text-ink-muted">
			What came in and went out in each currency, before anything is converted. Investing is
			what went into securities, or came back out of them.
		</p>
		<div class="mt-3 grid gap-6 md:grid-cols-2 xl:grid-cols-3">
			{#each currencies as currency (currency)}
				<table class="w-full text-small">
					<caption class="pb-1 text-left text-title font-medium text-ink"
						>{currency}</caption
					>
					<thead class="text-caption text-ink-muted">
						<tr>
							<th class="py-1 text-left font-normal">Period</th>
							<th class="py-1 text-right font-normal">In</th>
							<th class="py-1 text-right font-normal">Out</th>
							<th class="py-1 text-right font-normal">Investing</th>
						</tr>
					</thead>
					<tbody>
						{#each report.as_paid.filter((line) => line.currency === currency) as line (line.period)}
							<tr class="border-t border-line">
								<td class="py-1 text-ink-secondary tabular-nums">{line.period}</td>
								<td class="py-1 text-right"
									><Amount value={line.income} places={0} /></td
								>
								<td class="py-1 text-right"
									><Amount value={line.expenses} places={0} /></td
								>
								<td class="py-1 text-right"
									><Amount value={line.investing} places={0} /></td
								>
							</tr>
						{/each}
					</tbody>
				</table>
			{/each}
		</div>
	</details>
{/if}
