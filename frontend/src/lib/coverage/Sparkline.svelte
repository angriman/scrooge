<!--
@component
An account's balance at its last month-ends as a small line, with no axis: whether it grew,
shrank or kept still over the year, read beside the figure it ends on. Nothing is drawn for
fewer than two months.
-->
<script lang="ts">
	import type { Amount } from '#lib/types.js';
	import { cn } from '#lib/ui/cn.js';
	import { sparkline } from './holds.js';

	interface Props {
		/** Oldest first. */
		balances: Amount[];
		class?: string;
	}

	let { balances, class: className }: Props = $props();

	const WIDTH = 56;
	const HEIGHT = 20;
	const points = $derived(sparkline(balances, WIDTH, HEIGHT, 2.5));
	const last = $derived(points.split(' ').at(-1)?.split(',').map(Number) ?? [0, 0]);
</script>

{#if points}
	<svg
		viewBox="0 0 {WIDTH} {HEIGHT}"
		width={WIDTH}
		height={HEIGHT}
		class={cn('shrink-0 overflow-visible', className)}
		aria-hidden="true"
	>
		<polyline
			{points}
			fill="none"
			class="stroke-ink-muted"
			stroke-width="1.25"
			stroke-linejoin="round"
			stroke-linecap="round"
		/>
		<circle cx={last[0]} cy={last[1]} r="2" class="fill-ink-secondary" />
	</svg>
{/if}
