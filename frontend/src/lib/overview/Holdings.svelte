<!--
@component
The largest holdings, every account together: each by its name when one is stored (a fund's
ISIN says little), with its type of asset beside it, so that a pension fund's holdings read as
Pillar 3a and not as one more fund.
-->
<script lang="ts">
	import { assetColor } from '#lib/design/data.js';
	import { money, percent } from '#lib/format/number.js';
	import type { Holding } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Swatch from '#lib/ui/Swatch.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import Share from '#lib/ui/Share.svelte';

	interface Props {
		top: Holding[];
		rest: { count: number; value: string; share: string | null } | null;
		logos: Record<string, string>;
		currency: string;
	}

	let { top, rest, logos, currency }: Props = $props();

	const colorOf = assetColor;
	// A logo is worth a column only once one is kept; letters would repeat the name beside them.
	const withLogos = $derived(top.some((holding) => logos[holding.symbol]));
</script>

<Panel title={rest ? 'Largest holdings' : 'Holdings'}>
	{#snippet actions()}
		{#if rest}
			<span class="text-small text-ink-muted">{top.length} of {top.length + rest.count}</span>
		{/if}
	{/snippet}
	<ul>
		{#each top as holding (holding.symbol + holding.asset)}
			<li class="flex items-center gap-3 border-t border-line py-2 first:border-t-0 first:pt-0">
				{#if withLogos}
					{#if logos[holding.symbol]}
						<img
							src={logos[holding.symbol]}
							alt=""
							class="size-7 shrink-0 rounded-control bg-ink object-contain p-0.5"
						/>
					{:else}
						<span class="size-7 shrink-0" aria-hidden="true"></span>
					{/if}
				{/if}
				<span class="min-w-0 flex-1">
					<span class="block truncate font-medium text-ink" title={holding.name || holding.symbol}>
						{holding.name || holding.symbol}
					</span>
					<span class="flex min-w-0 items-center gap-1.5 text-small text-ink-muted">
						<Swatch color={colorOf(holding.asset)} class="size-2 rounded-full" />
						<span class="shrink-0 text-ink-secondary">{holding.asset}</span>
						{#if holding.name}<span class="truncate">{holding.symbol}</span>{/if}
					</span>
				</span>
				<Share
					share={holding.share}
					color={colorOf(holding.asset)}
					class="hidden w-20 sm:block"
				/>
				<span class="w-28 shrink-0 text-right">
					<Amount value={holding.value} {currency} places={0} />
					{#if holding.share !== null}
						<span class="block text-caption text-ink-muted">{percent(holding.share)}</span>
					{/if}
				</span>
			</li>
		{/each}
	</ul>
	{#if rest}
		<p class="mt-2 border-t border-line pt-2 text-small text-ink-secondary">
			The other {rest.count === 1 ? 'one is' : `${rest.count} are`} worth
			{money(rest.value, 0)}
			{currency}{#if rest.share !== null}, {percent(rest.share)} of net worth{/if}
		</p>
	{/if}
</Panel>
