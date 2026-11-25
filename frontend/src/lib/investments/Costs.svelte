<!--
@component
What investing cost each calendar year, newest first: the brokers' commissions, the tax withheld
from what investments paid, both together, and the tax as a part of what was paid; every year
together; and each account, what cost the most first. Each figure opens its transactions. When
the costs could not be loaded, the panel says why in their place.
-->
<script lang="ts">
	import { resolve } from '$app/paths';
	import { approximate, percent } from '#lib/format/number.js';
	import type { CostsReport } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import {
		COMMISSIONS,
		type CostFigure,
		costsAnything,
		costsOf,
		newestFirst,
		parts,
		TAX
	} from './costs.js';

	interface Props {
		/** Null when they could not be loaded: `unavailable` says why. */
		costs: CostsReport | null;
		unavailable?: string;
		/** Whose figures, for the transactions a figure opens. */
		owner: string | null;
	}

	let { costs, unavailable = '', owner }: Props = $props();

	const years = $derived(costs ? newestFirst(costs.years) : []);
	const href = (what: CostFigure) => `${resolve('/transactions')}?${costsOf(what, owner)}`;
</script>

{#snippet figure(value: string, what: CostFigure)}
	{#if approximate(value) === 0}
		<span class="text-ink-muted">–</span>
	{:else}
		<a href={href(what)} class="hover:underline"><Amount {value} places={0} /></a>
	{/if}
{/snippet}

<Panel
	class="mt-10"
	title="Costs"
	note={costs
		? `What investing cost each year, in ${costs.currency}: commissions and the tax withheld`
		: undefined}
>
	{#if !costs}
		<p class="text-body text-ink-secondary" role="status">
			The costs are not available: {unavailable || 'they could not be loaded.'}
		</p>
	{:else if !costsAnything(costs)}
		<p class="text-body text-ink-secondary">Nothing was charged or withheld.</p>
	{:else}
		<table class="w-full text-small tabular-nums">
			<thead class="text-caption text-ink-muted">
				<tr>
					<th scope="col" class="pb-2 text-left font-normal">Year</th>
					<th scope="col" class="pb-2 text-right font-normal">Commissions</th>
					<th scope="col" class="pb-2 text-right font-normal">Tax withheld</th>
					<th scope="col" class="pb-2 text-right font-normal">Total</th>
					<th scope="col" class="hidden pb-2 text-right font-normal sm:table-cell"
						>Of what was paid</th
					>
				</tr>
			</thead>
			<tbody>
				{#each years as year (year.year)}
					<tr class="border-t border-line">
						<th scope="row" class="py-2 text-left font-normal text-ink">
							{year.year}{#if !year.whole}<span class="ml-1 text-ink-muted"
									>so far</span
								>{/if}
						</th>
						<td class="py-2 text-right">
							{@render figure(year.commissions, {
								category: COMMISSIONS,
								year: year.year
							})}
						</td>
						<td class="py-2 text-right">
							{@render figure(year.tax, { category: TAX, year: year.year })}
						</td>
						<td class="py-2 text-right text-ink">
							<Amount value={year.total} places={0} />
						</td>
						<td class="hidden py-2 text-right text-ink-secondary sm:table-cell">
							{year.tax_share === null ? '–' : percent(year.tax_share)}
						</td>
					</tr>
				{/each}
			</tbody>
			{#if years.length > 1}
				<tfoot>
					<tr class="border-t border-line-strong font-medium">
						<th scope="row" class="py-2 text-left text-ink">All years</th>
						<td class="py-2 text-right"
							>{@render figure(costs.totals.commissions, {
								category: COMMISSIONS
							})}</td
						>
						<td class="py-2 text-right"
							>{@render figure(costs.totals.tax, { category: TAX })}</td
						>
						<td class="py-2 text-right text-ink">
							<Amount value={costs.totals.total} places={0} />
						</td>
						<td class="hidden py-2 text-right text-ink-secondary sm:table-cell">
							{costs.totals.tax_share === null
								? '–'
								: percent(costs.totals.tax_share)}
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
							href={href({
								account: each.account,
								owner: each.owner,
								broker: each.broker
							})}
							class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 py-2 text-small hover:bg-raised"
						>
							<span class="min-w-0">
								<span class="block truncate text-ink">{each.name}</span>
								<span class="block truncate text-ink-muted">{parts(each)}</span>
							</span>
							<Amount
								value={each.total}
								currency={costs.currency}
								places={0}
								class="text-ink"
							/>
						</a>
					</li>
				{/each}
			</ul>
		{/if}

		<p class="mt-4 max-w-prose text-caption text-ink-muted">
			Commissions are those charged on broker and pension accounts; a bank’s fees, of the same
			category, are left out. From a tablet’s width on, the tax withheld is also in percent of
			the dividends and interest paid that year, before tax. Rows in a currency left out for
			want of a rate still show in the transactions a figure opens.
		</p>
	{/if}
</Panel>
