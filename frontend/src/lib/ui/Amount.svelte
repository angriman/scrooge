<!--
@component
An amount of money as the app writes it: `1’592’896.43 CHF`. Its cents and its currency are
quieter than the francs, so a column of amounts is read by its whole numbers.
-->
<script lang="ts">
	import { money } from '#lib/format/number.js';
	import type { Amount } from '#lib/types.js';
	import { cn } from './cn.js';

	interface Props {
		value: Amount;
		currency?: string;
		/** Decimals shown: 2 for cents, 0 for whole francs. */
		places?: number;
		class?: string;
	}

	let { value, currency, places = 2, class: className }: Props = $props();

	const written = $derived(money(value, places));
	const point = $derived(written.indexOf('.'));
</script>

<span class={cn('whitespace-nowrap tabular-nums', className)}>
	{#if point < 0}{written}{:else}{written.slice(0, point)}<span
			class="text-[0.72em] text-ink-secondary">{written.slice(point)}</span
		>{/if}{#if currency}<span
			class="ml-[0.3em] text-[max(0.6em,0.6875rem)] font-normal text-ink-muted"
			>{currency}</span
		>{/if}
</span>
