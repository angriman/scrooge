<!--
@component
How much something moved over a span: `+12.5k  +0.8%  in 30 days`. Its colour says which way,
its arrow says it again for whoever does not see the colour.
-->
<script lang="ts">
	import ArrowDownRight from '@lucide/svelte/icons/arrow-down-right';
	import ArrowUpRight from '@lucide/svelte/icons/arrow-up-right';
	import { shortSigned, signedPercent } from '#lib/format/number.js';
	import type { Amount } from '#lib/types.js';
	import { cn } from './cn.js';

	interface Props {
		amount: Amount | null;
		percent?: Amount | null;
		/** What span it is: `in 30 days`. */
		label?: string;
		class?: string;
	}

	let { amount, percent = null, label, class: className }: Props = $props();

	const way = $derived.by(() => {
		if (amount === null) return 'none';
		const written = shortSigned(amount);
		return written.startsWith('+') ? 'up' : written === '0' ? 'flat' : 'down';
	});
</script>

<span class={cn('inline-flex items-baseline gap-1.5 whitespace-nowrap tabular-nums', className)}>
	{#if amount === null}
		<span class="text-ink-muted">not known</span>
	{:else if way === 'flat'}
		<span class="text-ink-muted">no change</span>
	{:else}
		<span
			class={cn(
				'inline-flex items-center gap-0.5 self-center font-medium',
				way === 'up' && 'text-gain',
				way === 'down' && 'text-loss'
			)}
		>
			{#if way === 'up'}<ArrowUpRight class="size-3.5" aria-hidden="true" />
			{:else}<ArrowDownRight class="size-3.5" aria-hidden="true" />{/if}
			{shortSigned(amount)}
		</span>
		{#if percent !== null}
			<span class="text-ink-secondary">{signedPercent(percent)}</span>
		{/if}
	{/if}
	{#if label}<span class="text-ink-muted">{label}</span>{/if}
</span>
