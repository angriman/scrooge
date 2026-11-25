<!--
@component
The allocation of securities, how they are spread: by type, currency, account and (several
people's) person, each a bar of the whole with its parts. It leads with what one bar cannot say:
how much the largest single holding weighs. The overview has the allocation of net worth.
-->
<script lang="ts">
	import { assetColor, inOrder } from '#lib/design/data.js';
	import { approximate, percent } from '#lib/format/number.js';
	import type { HeldInstrument, Portfolio } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import Swatch from '#lib/ui/Swatch.svelte';

	interface Props {
		spread: Portfolio['spread'];
		largest: HeldInstrument | undefined;
		currency: string;
		/** Whether several people's securities are shown: then they are cut by person too. */
		people: boolean;
	}

	let { spread, largest, currency, people }: Props = $props();

	const lenses = $derived(
		[
			{ title: 'By type', slices: spread.asset, byType: true },
			{ title: 'By currency', slices: spread.currency, byType: false },
			{ title: 'By account', slices: spread.account, byType: false },
			...(people ? [{ title: 'By person', slices: spread.owner, byType: false }] : [])
		].filter((lens) => lens.slices.length)
	);
	const colorsOf = (lens: (typeof lenses)[number]) =>
		lens.byType
			? new Map(lens.slices.map((slice) => [slice.name, assetColor(slice.name)]))
			: inOrder(lens.slices.map((slice) => slice.name));
</script>

<Panel title="Allocation" note="Of securities">
	{#if largest}
		<p class="mb-4 text-body text-ink-secondary">
			The largest holding, <span class="text-ink">{largest.name || largest.symbol}</span>, is
			<span class={approximate(largest.share) >= 25 ? 'text-caution' : 'text-ink'}
				>{percent(largest.share)}</span
			>
			of securities.
		</p>
	{/if}
	<div class="grid gap-5">
		{#each lenses as lens (lens.title)}
			{@const colors = colorsOf(lens)}
			<div>
				<h3 class="mb-2 text-small text-ink-secondary">{lens.title}</h3>
				<div class="flex h-2.5 gap-0.5" aria-hidden="true">
					{#each lens.slices as slice (slice.name)}
						<span
							class="h-full rounded-mark"
							style:width="{Math.max(0, approximate(slice.share))}%"
							style:background-color={colors.get(slice.name)}
						></span>
					{/each}
				</div>
				<ul
					class="mt-2 grid grid-cols-[auto_minmax(0,1fr)_auto_3.5rem] items-center gap-x-2 gap-y-1 text-small"
				>
					{#each lens.slices as slice (slice.name)}
						<li class="contents">
							<Swatch color={colors.get(slice.name) ?? 'var(--data-other)'} />
							<span class="truncate text-ink-secondary">{slice.name}</span>
							<span class="text-right"><Amount value={slice.value} {currency} places={0} /></span>
							<span class="text-right text-ink-muted tabular-nums">{percent(slice.share)}</span>
						</li>
					{/each}
				</ul>
			</div>
		{/each}
	</div>
</Panel>
