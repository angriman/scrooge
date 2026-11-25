<!--
@component
The allocation of net worth, what it is made of by type: a ring of what is held (what is owed
is not in it) over a table of every type with its value, its share of net worth as a bar of the
whole, and how it moved over the range the chart shows. The investments page has the allocation
of securities alone.
-->
<script lang="ts">
	import { PieChart, type ChartState } from 'layerchart';
	import { assetColor, assetName } from '#lib/design/data.js';
	import { approximate, money, percent, sum } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { Part, Series } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Change from '#lib/ui/Change.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import Share from '#lib/ui/Share.svelte';
	import Swatch from '#lib/ui/Swatch.svelte';
	import { moved } from './range.js';

	interface Props {
		/** Every type, with its share of net worth: what is owed is negative. */
		assets: Part[];
		/** The types above zero, with their shares of what is held. */
		held: Part[];
		/** The types day by day, for how each moved over the range. */
		series: Series | null;
		/** How many days the range goes back (null: all of the history), and its name. */
		back: number | null;
		label: string;
		currency: string;
	}

	let { assets, held, series, back, label, currency }: Props = $props();

	const colorOf = assetColor;
	// Cash below zero is a debt (a margin loan): it is called what it is.
	const named = (part: Part) =>
		part.name === 'Cash' && part.value.startsWith('-') ? t('asset.CashOwed') : assetName(part.name);
	const heldTotal = $derived(sum(held.map((part) => part.value)));
	const movedOf = (name: string) => {
		const part = series?.parts.find((each) => each.name === name);
		return part ? moved(part.values, back, part.arrived) : null;
	};

	let context = $state<ChartState<Part>>();
	const pointed = $derived(context?.tooltip.data ?? null);
</script>

<Panel title={t('overview.allocationTitle')} note={t('overview.allocationNote')}>
	<div class="grid grid-cols-1 gap-5">
		<div class="relative mx-auto size-44">
			<PieChart
				bind:context
				data={held}
				key="name"
				value={(part: Part) => approximate(part.value)}
				c="name"
				cDomain={held.map((part) => part.name)}
				cRange={held.map((part) => colorOf(part.name))}
				innerRadius={0.72}
				padAngle={0.015}
				cornerRadius={2}
			>
				{#snippet tooltip()}{/snippet}
			</PieChart>
			<div class="pointer-events-none absolute inset-0 grid place-content-center text-center">
				{#if pointed}
					<span class="text-caption text-ink-secondary">{named(pointed)}</span>
					<span class="text-title font-medium text-ink"
						>{pointed.share === null ? '' : percent(pointed.share)}</span
					>
				{:else}
					<span class="text-caption text-ink-secondary">{t('overview.owned')}</span>
					<span class="text-title font-medium text-ink">{money(heldTotal, 0)}</span>
					<span class="text-caption text-ink-muted">{currency}</span>
				{/if}
			</div>
		</div>

		<table class="w-full text-body">
			<thead class="text-caption text-ink-muted">
				<tr>
					<th class="pb-2 text-left font-normal"
						><span class="sr-only">{t('overview.typeSr')}</span></th
					>
					<th class="pb-2 text-right font-normal"
						><span class="sr-only">{t('overview.valueSr')}</span></th
					>
					<th class="hidden pb-2 pl-4 text-left font-normal sm:table-cell"
						>{t('overview.ofNetWorth')}</th
					>
					<th class="pb-2 text-right font-normal">{label}</th>
				</tr>
			</thead>
			<tbody>
				{#each assets as part (part.name)}
					{@const change = movedOf(part.name)}
					<tr class="border-t border-line">
						<td class="py-2.5">
							<span class="flex items-center gap-2">
								<Swatch color={colorOf(part.name)} />
								{named(part)}
							</span>
						</td>
						<td class="py-2.5 text-right"><Amount value={part.value} {currency} places={0} /></td>
						<td class="hidden py-2.5 pl-4 sm:table-cell">
							{#if part.share !== null}
								<span class="flex items-center gap-2">
									<Share share={part.share} color={colorOf(part.name)} class="w-24" />
									<span class="w-12 text-right text-small text-ink-secondary"
										>{percent(part.share)}</span
									>
								</span>
							{/if}
						</td>
						<td class="py-2.5 text-right">
							<Change amount={change?.amount ?? null} class="text-small" />
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</Panel>
