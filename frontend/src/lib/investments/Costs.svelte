<!--
@component
What investing cost each calendar year, newest first: the brokers' commissions, the tax withheld
from what investments paid, both together, and the tax as a part of what was paid; every year
together; and each account, what cost the most first. Each figure opens its transactions.
-->
<script lang="ts">
	import { resolve } from '$app/paths';
	import { approximate, percent } from '#lib/format/number.js';
	import type { CostsReport } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import { COMMISSIONS, costsAnything, costsOf, newestFirst, parts, TAX } from './costs.js';

	interface Props {
		costs: CostsReport;
		/** Whose figures, for the transactions a figure opens. */
		owner: string | null;
	}

	let { costs, owner }: Props = $props();

	const currency = $derived(costs.currency);
	const years = $derived(newestFirst(costs.years));
	const href = (what: Parameters<typeof costsOf>[0]) =>
		`${resolve('/transactions')}?${costsOf(what, owner)}`;
</script>

{#snippet figure(value: string, what: Parameters<typeof costsOf>[0])}
	{#if approximate(value) === 0}
		<span class="text-ink-muted">–</span>
	{:else}
		<a href={href(what)} class="hover:underline"><Amount {value} places={0} /></a>
	{/if}
{/snippet}

<Panel
	class="mt-10"
	title="Costs"
	note="What investing cost each year, in {currency}: commissions and the tax withheld"
>
	{#if !costsAnything(costs)}
		<p class="text-body text-ink-secondary">Nothing was charged or withheld.</p>
	{:else}
		<table class="w-full text-small tabular-nums">
			<thead class="text-caption text-ink-muted">
				<tr>
					<th scope="col" class="pb-2 text-left font-normal">Year</th>
					<th scope="col" class="pb-2 text-right font-normal">Commissions</th>
					<th scope="col" class="pb-2 text-right font-normal">Tax withheld</th>
					<th scope="col" class="hidden pb-2 text-right font-normal sm:table-cell">Total</th>
					<th scope="col" class="pb-2 text-right font-normal">
						<span class="sm:hidden">Of paid</span><span class="hidden sm:inline">Of what was paid</span>
					</th>
				</tr>
			</thead>
			<tbody>
				{#each years as year (year.year)}
					<tr class="border-t border-line">
						<th scope="row" class="py-2 text-left font-normal text-ink">
							{year.year}{#if !year.whole}<span class="ml-1 text-ink-muted">so far</span>{/if}
						</th>
						<td class="py-2 text-right">
							{@render figure(year.commissions, { category: COMMISSIONS, year: year.year })}
						</td>
						<td class="py-2 text-right">
							{@render figure(year.tax, { category: TAX, year: year.year })}
						</td>
						<td class="hidden py-2 text-right text-ink sm:table-cell">
							<Amount value={year.total} places={0} />
						</td>
						<td class="py-2 text-right text-ink-secondary">
							{year.tax_share === null ? '–' : percent(year.tax_share)}
						</td>
					</tr>
				{/each}
			</tbody>
			{#if years.length > 1}
				<tfoot>
					<tr class="border-t border-line-strong font-medium">
						<th scope="row" class="py-2 text-left text-ink">All years</th>
						<td class="py-2 text-right">{@render figure(costs.totals.commissions, { category: COMMISSIONS })}</td>
						<td class="py-2 text-right">{@render figure(costs.totals.tax, { category: TAX })}</td>
						<td class="hidden py-2 text-right text-ink sm:table-cell">
							<Amount value={costs.totals.total} {currency} places={0} />
						</td>
						<td class="py-2 text-right text-ink-secondary">
							{costs.totals.tax_share === null ? '–' : percent(costs.totals.tax_share)}
						</td>
					</tr>
				</tfoot>
			{/if}
		</table>

		{#if costs.accounts.length}
			<h3 class="mt-6 mb-1 text-small font-medium text-ink">By account, every year</h3>
			<ul>
				{#each costs.accounts as each (`${each.owner}/${each.account}`)}
					<li class="border-t border-line first:border-t-0">
						<a
							href={href({ account: each.account, owner: each.owner })}
							class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 py-2 text-small hover:bg-raised"
						>
							<span class="min-w-0">
								<span class="block truncate text-ink">{each.name}</span>
								<span class="block truncate text-ink-muted">{parts(each)}</span>
							</span>
							<Amount value={each.total} {currency} places={0} class="text-ink" />
						</a>
					</li>
				{/each}
			</ul>
		{/if}

		<p class="mt-4 max-w-prose text-caption text-ink-muted">
			Commissions are those charged on broker and pension accounts; a bank’s fees, which
			Transactions lists under {COMMISSIONS} too, are left out. Tax withheld is in percent of the
			dividends and interest paid, before tax.
		</p>
	{/if}
</Panel>
