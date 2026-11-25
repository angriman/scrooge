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
	import { t } from '#lib/i18n/index.svelte.js';
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
	title={t('costs.title')}
	note={costs ? t('costs.note', { currency: costs.currency }) : undefined}
>
	{#if !costs}
		<p class="text-body text-ink-secondary" role="status">
			{t('costs.unavailablePrefix', { why: unavailable || t('costs.unavailableDefault') })}
		</p>
	{:else if !costsAnything(costs)}
		<p class="text-body text-ink-secondary">{t('costs.empty')}</p>
	{:else}
		<table class="w-full text-small tabular-nums">
			<thead class="text-caption text-ink-muted">
				<tr>
					<th scope="col" class="pb-2 text-left font-normal">{t('costs.colYear')}</th>
					<th scope="col" class="pb-2 text-right font-normal">{t('costs.colCommissions')}</th>
					<th scope="col" class="pb-2 text-right font-normal">{t('costs.colTaxWithheld')}</th>
					<th scope="col" class="pb-2 text-right font-normal">{t('overview.total')}</th>
					<th scope="col" class="hidden pb-2 text-right font-normal sm:table-cell"
						>{t('costs.colOfWhatPaid')}</th
					>
				</tr>
			</thead>
			<tbody>
				{#each years as year (year.year)}
					<tr class="border-t border-line">
						<th scope="row" class="py-2 text-left font-normal text-ink">
							{year.year}{#if !year.whole}<span class="ml-1 text-ink-muted">{t('costs.soFar')}</span
								>{/if}
						</th>
						<td class="py-2 text-right">
							{@render figure(year.commissions, { category: COMMISSIONS, year: year.year })}
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
						<th scope="row" class="py-2 text-left text-ink">{t('costs.allYears')}</th>
						<td class="py-2 text-right"
							>{@render figure(costs.totals.commissions, { category: COMMISSIONS })}</td
						>
						<td class="py-2 text-right">{@render figure(costs.totals.tax, { category: TAX })}</td>
						<td class="py-2 text-right text-ink">
							<Amount value={costs.totals.total} places={0} />
						</td>
						<td class="hidden py-2 text-right text-ink-secondary sm:table-cell">
							{costs.totals.tax_share === null ? '–' : percent(costs.totals.tax_share)}
						</td>
					</tr>
				</tfoot>
			{/if}
		</table>

		{#if costs.accounts.length}
			<h3 class="mt-6 mb-1 text-small font-medium text-ink">{t('costs.byAccountEveryYear')}</h3>
			<ul>
				{#each costs.accounts as each (`${each.owner}/${each.account}`)}
					<li class="border-t border-line first:border-t-0">
						<a
							href={href({ account: each.account, owner: each.owner, broker: each.broker })}
							class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 py-2 text-small hover:bg-raised"
						>
							<span class="min-w-0">
								<span class="block truncate text-ink">{each.name}</span>
								<span class="block truncate text-ink-muted">{parts(each)}</span>
							</span>
							<Amount value={each.total} currency={costs.currency} places={0} class="text-ink" />
						</a>
					</li>
				{/each}
			</ul>
		{/if}

		<p class="mt-4 max-w-prose text-caption text-ink-muted">
			{t('costs.footnote')}
		</p>
	{/if}
</Panel>
