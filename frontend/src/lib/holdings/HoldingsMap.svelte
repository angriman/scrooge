<!--
@component
What is held as a map: each instrument a tile as large as its value, in the colour of its type,
with what it gained since it was bought. The largest holdings read at a glance, and so does how
much one of them weighs. A tile chosen opens that holding in the list.
-->
<script lang="ts">
	import { hierarchy, treemap } from 'd3-hierarchy';
	import { assetColor } from '#lib/design/data.js';
	import { approximate, percent, short, signedPercent } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { HeldInstrument } from '#lib/types.js';
	import { cn } from '#lib/ui/cn.js';
	import { holdingName, mapLabel } from './names.js';

	interface Props {
		holdings: HeldInstrument[];
		currency: string;
		onchoose: (holding: HeldInstrument) => void;
	}

	let { holdings, currency, onchoose }: Props = $props();

	// Laid out at the size it is shown: a tile's words keep the page's size.
	let width = $state(0);
	const height = $derived(width < 640 ? 260 : 320);

	const tiles = $derived.by(() => {
		if (!width) return [];
		const root = hierarchy<{ children?: HeldInstrument[] } | HeldInstrument>({
			children: holdings
		}).sum((node) => ('value' in node ? Math.max(0, approximate(node.value)) : 0));
		return treemap<{ children?: HeldInstrument[] } | HeldInstrument>()
			.size([width, height])
			.paddingInner(3)
			.round(true)(root)
			.leaves()
			.map((leaf) => ({
				holding: leaf.data as HeldInstrument,
				x: leaf.x0,
				y: leaf.y0,
				w: leaf.x1 - leaf.x0,
				h: leaf.y1 - leaf.y0
			}));
	});

	const label = (holding: HeldInstrument) => mapLabel(holding);
</script>

<div bind:clientWidth={width} class="relative w-full" style:height="{height}px">
	{#each tiles as tile (tile.holding.symbol + tile.holding.asset + tile.holding.currency)}
		{@const holding = tile.holding}
		{@const color = assetColor(holding.asset)}
		{@const roomy = tile.w >= 84 && tile.h >= 52}
		<button
			type="button"
			onclick={() => onchoose(holding)}
			title={t('holdings.mapTileTitle', {
				name: holdingName(holding),
				value: short(holding.value),
				currency,
				share: percent(holding.share)
			})}
			class="absolute flex flex-col justify-start overflow-hidden rounded-control border-t-2 text-left transition-[filter] hover:brightness-95 focus-visible:z-10 dark:hover:brightness-125"
			style:left="{tile.x}px"
			style:top="{tile.y}px"
			style:width="{tile.w}px"
			style:height="{tile.h}px"
			style:border-top-color={color}
			style:background-color="color-mix(in srgb, {color} 22%, var(--surface-panel))"
		>
			{#if tile.w >= 44 && tile.h >= 28}
				<span class="block px-2 pt-1.5">
					<span
						class={cn('block truncate font-medium text-ink', roomy ? 'text-body' : 'text-caption')}
					>
						{label(holding)}
					</span>
					{#if roomy}
						<span class="block text-small text-ink-secondary tabular-nums"
							>{short(holding.value)}</span
						>
						{#if holding.return_percent !== null}
							<span
								class={cn(
									'block text-caption tabular-nums',
									holding.return_percent.startsWith('-') ? 'text-loss' : 'text-gain'
								)}
							>
								{signedPercent(holding.return_percent)}
							</span>
						{/if}
					{/if}
				</span>
			{/if}
		</button>
	{/each}
</div>
