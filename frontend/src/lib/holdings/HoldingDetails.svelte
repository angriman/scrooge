<!--
@component
Details of one held instrument inside the side panel: its current value and gain, its positions
account by account in the instrument's currency, what it returned over each range, and its logo.
-->
<script lang="ts">
	import { assetColor } from '#lib/design/data.js';
	import { day } from '#lib/format/date.js';
	import { exact, money, percent } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { HeldInstrument, Position } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Change from '#lib/ui/Change.svelte';
	import Share from '#lib/ui/Share.svelte';
	import HoldingReturns from './HoldingReturns.svelte';
	import LogoControl from './LogoControl.svelte';

	interface Props {
		holding: HeldInstrument;
		/** Whether this instrument currently has a custom logo. */
		hasLogo: boolean;
		currency: string;
		/** The last day: a price from another day says so. */
		lastDay: string;
		/** Whether several people's positions are listed: each says whose it is. */
		people: boolean;
		/** The token a change of a logo needs; null where logos cannot be changed. */
		token?: string | null;
		/** Called once a logo is changed. */
		onlogo?: () => void | Promise<void>;
	}

	let {
		holding,
		hasLogo,
		currency,
		lastDay,
		people,
		token = null,
		onlogo = () => {}
	}: Props = $props();

	const color = $derived(assetColor(holding.asset));
	const priced = (position: Position) => {
		if (position.price === null) return '–';
		return exact(position.price);
	};
	const priceNote = (position: Position) =>
		position.price_is_trade
			? t('holdings.lastTradePrice')
			: position.price_day && position.price_day !== lastDay
				? t('holdings.closeOf', { date: day(position.price_day) })
				: '';
</script>

<div
	class="mb-5 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 border-b border-line pb-4"
>
	<div>
		<p class="text-figure font-medium tracking-tight text-ink">
			<Amount value={holding.value} {currency} places={0} />
		</p>
		<p class="mt-1 flex items-center gap-2 text-small text-ink-secondary tabular-nums">
			<Share share={holding.share} {color} class="w-16" />
			<span>{percent(holding.share)}</span>
		</p>
	</div>
	<div class="text-left sm:text-right">
		{#if holding.gain === null}
			<p class="text-small text-ink-muted">{t('holdings.costNotKnown')}</p>
		{:else}
			<p class="text-caption text-ink-muted">{t('investments.sinceBought')}</p>
			<Change amount={holding.gain} percent={holding.return_percent} class="mt-0.5 text-body" />
		{/if}
	</div>
</div>

<div class="overflow-x-auto">
	<table class="w-full text-small">
		<thead class="text-caption text-ink-muted">
			<tr>
				<th class="pb-2 text-left font-normal">{t('investments.colAccount')}</th>
				<th class="pb-2 pl-3 text-right font-normal">{t('holdings.colQuantity')}</th>
				<th class="pb-2 pl-3 text-right font-normal">{t('holdings.colPrice')}</th>
				<th class="pb-2 pl-3 text-right font-normal">{t('holdings.colValue')}</th>
				<th class="pb-2 pl-3 text-right font-normal">{t('holdings.colCost')}</th>
				<th class="pb-2 pl-3 text-right font-normal">{t('investments.sinceBought')}</th>
			</tr>
		</thead>
		<tbody>
			{#each holding.positions as position (position.owner + position.account)}
				<tr class="border-t border-line">
					<td class="py-2 whitespace-nowrap">
						{position.account}{#if people}<span class="text-ink-muted">, {position.owner}</span
							>{/if}
					</td>
					<td class="py-2 pl-3 text-right tabular-nums">{exact(position.quantity)}</td>
					<td class="py-2 pl-3 text-right whitespace-nowrap tabular-nums">
						{priced(position)}
						{#if priceNote(position)}<span class="block text-caption text-ink-muted"
								>{priceNote(position)}</span
							>{/if}
					</td>
					<td class="py-2 pl-3 text-right whitespace-nowrap tabular-nums">
						{money(position.value_native)}
						<span class="text-caption text-ink-muted">{holding.currency}</span>
					</td>
					<td class="py-2 pl-3 text-right whitespace-nowrap tabular-nums">
						{position.cost_native === null ? t('common.notKnown') : money(position.cost_native)}
					</td>
					<td class="py-2 pl-3 text-right whitespace-nowrap">
						<Change amount={position.gain_native} percent={position.gain_percent} />
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<HoldingReturns {holding} {currency} {people} />

{#if token && holding.asset !== 'Bills'}
	<LogoControl symbol={holding.symbol} has={hasLogo} {token} onchange={onlogo} />
{/if}
